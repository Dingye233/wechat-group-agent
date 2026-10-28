from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import AgentLog, ChatTask, Group, GroupConfig, GroupMember, Message, Sticker
from app.services.capabilities import catalog_for_scope, execute_capability
from app.services.impressions import (
    get_impression,
    impression_prompt,
    update_impression,
)
from app.services.llm import LLMError, LLMService
from app.services.serializers import agent_log_dict
from app.services.settings import get_settings
from app.services.stickers import resolve_sticker, sticker_prompt_catalog

if TYPE_CHECKING:
    from app.core.runtime import RuntimeManager

TRIGGER_LABELS = {
    "mention": "@ 提及",
    "keyword": "关键词",
    "all": "所有消息",
}
MAX_REPLY_CHARS = 600


class AgentEngine:
    """Trigger evaluation, context building, LLM call and sticker selection."""

    def __init__(self, runtime: RuntimeManager, llm: LLMService) -> None:
        self.runtime = runtime
        self.llm = llm
        self._sem = asyncio.Semaphore(4)
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_reply: dict[int, float] = {}
        self._reply_times: dict[int, deque[float]] = defaultdict(deque)
        self._next_reply_at: dict[int, float] = {}
        # Coalesce a burst per sender, not per group.  A group-wide key let a
        # message from another member cancel an earlier member's pending
        # question while a slow multi-segment reply was being sent.
        self._latest_trigger: dict[tuple[int, str], int] = {}
        self._global_lock = asyncio.Lock()
        self._global_next_reply = 0.0
        self._processed: deque[int] = deque(maxlen=3000)
        self._processed_set: set[int] = set()
        self._task_jobs: set[asyncio.Task] = set()

    async def handle_message(self, group_id: int, message_id: int) -> None:
        if not self._mark_processed(message_id):
            return
        async with self._sem:
            try:
                await self._handle(group_id, message_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._log(
                    group_id,
                    "error",
                    f"Agent 处理异常：{exc}",
                    level="error",
                )

    def _mark_processed(self, message_id: int) -> bool:
        if message_id in self._processed_set:
            return False
        if len(self._processed) == self._processed.maxlen:
            old = self._processed[0]
            self._processed_set.discard(old)
        self._processed.append(message_id)
        self._processed_set.add(message_id)
        return True

    # ------------------------------------------------------------ intent router
    async def _plan_intent(
        self,
        message: Message,
        group: Group,
        config: GroupConfig,
        settings: dict[str, Any],
        context: list[str],
        memory_text: str,
        impression: dict[str, Any],
        tool_payload: dict[str, Any],
        bili_context: str = "",
    ) -> dict[str, Any]:
        """Fast intent/emotion analysis before choosing a reply agent."""

        tools = tool_payload.get("tools") or []
        tool_lines = [
            f"- {item['name']}: {item.get('desc', '')} args={json.dumps(item.get('args') or {}, ensure_ascii=False)}"
            for item in tools
        ]
        system_prompt = """你是群伴的「群聊意图分类器」。你不负责回复用户，只输出一个 JSON。
你的任务不是判断“这句话像什么”，而是判断“群伴下一步应该做什么”。

# 一、决策顺序（必须按顺序判断）
1. 这句话是不是在对群伴说？
   - 叫名字、@、提问、求助、派活、明显对着机器人说话 → should_reply=true。
   - 群友之间互相聊天、灌水、已经有人回答且不需要群伴 → should_reply=false。
   - 拿不准 → true。不要因为消息短就跳过。
2. 用户要的是聊天，还是一件可执行的事？
   - 要查、做、找、改、下载、发、运行、生成、保存、整理、操作、盯着 → intent=task。
   - 只是表达情绪、吐槽、评价 → intent=emotional/complaint。
   - 问事实/建议，且不需要外部动作 → intent=question/chat。
   - 查人、手机号、住址、身份证、泄露隐私 → intent=privacy。
   - 广告、刷屏、纯灌水 → intent=spam。
3. 这件事群聊现有工具能不能做？
   - 查天气 → call_interface。
   - B站视频/评论/知识库 → bili_*。
   - 简单表格/点检表/Excel，只要把给定内容生成文件 → create_excel。
   - 工具描述明确匹配 → need_tools=true，tools 只选真正要用的 1-3 个。
4. 现有工具做不了，或者任务复杂/多步 → delegate_to_opencode=true。
   - 写代码、写脚本、运行命令、批量处理文件。
   - 找数据集、Kaggle、下载、爬取、抓取、标注、训练集。
   - 生成 PPT、复杂文档、图表、宏、多个文件。
   - 操作桌面、多个应用、需要多步工具编排。
   - 用户明确说“让 opencode 做 / 交给后台执行器 / 让后台跑 / 你负责盯着”。
   - 需要修改/读取现有文件，而群聊工具只有生成能力。
5. 判断情绪、语气、对这个人的印象。
6. 输出 JSON。

# 二、硬规则
- 一条消息如果 intent=task，就不能既 need_tools=false 又不 delegate_to_opencode；必须二选一。
- 能匹配工具就选工具；匹配不了就 delegate，不要回答“做不到/没能力/不能驱动 opencode”。
- 不要为了凑 need_tools 选 run_skill、run_mcp 或无关工具。
- 用户说“让opencode做/交给后台/你负责盯”时，必须 delegate_to_opencode=true，即使你觉得本地工具也能做。
- 找数据集、Kaggle、下载到桌面、爬取/标注数据，即使你觉得自己能搜，也必须 delegate。
- 简单表格/Excel/点检表 → create_excel；复杂格式、宏、图表、读取并修改现有 Excel → delegate。
- 普通闲聊不调用工具；纯聊天不要 delegate。
- 隐私请求：should_reply=true，intent=privacy，need_tools=false，delegate=false，不调用工具。
- 纯灌水/广告：intent=spam，should_reply=false。
- 短句必须结合最近上下文理解。“你让opencode做啊”“继续”“就这个”不是普通聊天，它们承接上文任务。
- 不确定要不要参与 → should_reply=true；不确定是否需要外部动作 → 按 task 处理，匹配不到工具就 delegate。
- 只输出 JSON，不要解释，不要 Markdown。

# 三、输出字段
{
  "should_reply": true,
  "intent": "chat|question|task|emotional|complaint|privacy|spam",
  "need_tools": false,
  "tools": [],
  "user_emotion": "neutral|happy|excited|playful|confused|anxious|frustrated|angry|sad|disappointed",
  "emotion_intensity": 0.0,
  "tone": "warm|calm|professional|playful|apologetic",
  "ack": "",
  "traits": [],
  "impression_note": "",
  "missing_capability": "",
  "delegate_to_opencode": false,
  "opencode_brief": ""
}

# 四、示例
例1 用户：@小助手 帮我查一下武夷山今天天气
输出：{"should_reply":true,"intent":"question","need_tools":true,"tools":["call_interface"],"user_emotion":"neutral","emotion_intensity":0.3,"tone":"warm","ack":"我去查一下","traits":[],"impression_note":"","missing_capability":"","delegate_to_opencode":false,"opencode_brief":""}

例2 用户：给我做一份点检表，5行，发群里
输出：{"should_reply":true,"intent":"task","need_tools":true,"tools":["create_excel"],"user_emotion":"neutral","emotion_intensity":0.3,"tone":"warm","ack":"我这就整理成表格发群里","traits":[],"impression_note":"","missing_capability":"","delegate_to_opencode":false,"opencode_brief":""}

例3 用户：帮我在Kaggle找个表情包数据集，下载到桌面
输出：{"should_reply":true,"intent":"task","need_tools":false,"tools":[],"user_emotion":"neutral","emotion_intensity":0.4,"tone":"warm","ack":"我去Kaggle翻一下，找到合适的就下载到你桌面","traits":[],"impression_note":"","missing_capability":"","delegate_to_opencode":true,"opencode_brief":"在Kaggle搜索表情包meme数据集，筛选2026年10月前、带label标注的数据集，下载到用户桌面，并汇报名称、链接、大小、label说明和保存路径。"}

例4 用户：你让opencode做啊
输出：{"should_reply":true,"intent":"task","need_tools":false,"tools":[],"user_emotion":"neutral","emotion_intensity":0.4,"tone":"calm","ack":"好，我交给后台执行器处理","traits":[],"impression_note":"","missing_capability":"","delegate_to_opencode":true,"opencode_brief":"根据上文任务，交给OpenCode执行；先确认上文的具体目标、文件和数据来源，完成后由群伴发送结果。"}

例5 用户：哈哈哈
输出：{"should_reply":false,"intent":"chat","need_tools":false,"tools":[],"user_emotion":"happy","emotion_intensity":0.5,"tone":"warm","ack":"","traits":[],"impression_note":"","missing_capability":"","delegate_to_opencode":false,"opencode_brief":""}"""
        user_prompt = f"""# 群聊
- 群名：{group.name or '未知'}
- 机器人：{self.runtime.bot_name or '小助手'}
- 触发方式：{config.trigger_mode}
- 最后消息发送者：{message.sender_name or '未知'}（{message.sender_wxid or ''}）
- 最后消息：{message.content or ''}

# 最近上下文
{chr(10).join(context[-10:])}

# 长期记忆
{(memory_text or '无')[:1000]}

# 对这个人的印象
{impression_prompt(impression)}

# 最近看过的B站内容
{(bili_context or '无')[:800]}

# 可用工具
{chr(10).join(tool_lines) or '无'}"""
        model = (config.model or settings.get("llm_model") or "").strip()
        try:
            data, _ = await self.llm.complete_json(
                base_url=str(settings.get("llm_base_url") or ""),
                api_key=str(settings.get("llm_api_key") or ""),
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.1,
                max_tokens=420,
                json_mode=True,
            )
        except LLMError:
            return self._fallback_plan(message, config, tools)
        except Exception:
            return self._fallback_plan(message, config, tools)

        allowed_tools = {str(item.get("name") or "") for item in tools}
        intent = str(data.get("intent") or "chat").strip().lower()
        if intent not in {
            "chat",
            "question",
            "task",
            "emotional",
            "complaint",
            "privacy",
            "spam",
        }:
            intent = "chat"
        requested = data.get("tools")
        if not isinstance(requested, list):
            requested = []
        selected = [
            str(item).strip()
            for item in requested
            if str(item).strip() in allowed_tools
        ][:3]
        need_tools = bool(data.get("need_tools")) and bool(selected)
        if intent == "privacy":
            need_tools = False
            selected = []
        if intent == "spam":
            need_tools = False
            selected = []
        should_reply = data.get("should_reply")
        if not isinstance(should_reply, bool):
            should_reply = True
        emotion = str(data.get("user_emotion") or "neutral").strip().lower()
        try:
            intensity = max(0.0, min(1.0, float(data.get("emotion_intensity") or 0)))
        except (TypeError, ValueError):
            intensity = 0.0
        traits = data.get("traits")
        if not isinstance(traits, list):
            traits = []
        ack = str(data.get("ack") or "").strip()
        if need_tools and not ack:
            ack = random.choice(
                ["我去看看，稍等～", "我查一下，等我一下", "我去确认一下"]
            )
        delegate = bool(data.get("delegate_to_opencode"))
        brief = str(data.get("opencode_brief") or "")[:800]
        content_text = str(message.content or "").lower()
        complex_words = (
            "写脚本",
            "写个脚本",
            "写代码",
            "写个程序",
            "python",
            "ppt",
            "幻灯片",
            "爬虫",
            "自动化",
            "工作区",
            "批量",
            "多步",
            "桌面",
            "生成报告",
            "运行脚本",
            "命令行",
            "宏",
        )
        explicit_opencode = self._wants_opencode(str(message.content or ""))
        hard_delegate = self._wants_file_research(str(message.content or ""))
        if (explicit_opencode or hard_delegate) and intent not in {
            "privacy",
            "spam",
        }:
            delegate = True
            need_tools = False
            selected = []
            if intent == "chat":
                intent = "task"
            brief = brief or str(message.content or "")[:600]
        if (
            not delegate
            and intent not in {"privacy", "spam"}
            and not selected
            and any(word in content_text for word in complex_words)
        ):
            delegate = True
            brief = brief or str(message.content or "")[:600]
            if intent == "chat":
                intent = "task"
        # A task with no usable group tool must not be answered with a fake
        # "I can't" reply. Hand it to the background executor instead.
        if (
            intent == "task"
            and not delegate
            and not need_tools
            and not selected
        ):
            delegate = True
            should_reply = True
            brief = brief or str(message.content or "")[:600]
        if intent in {"privacy", "spam"}:
            delegate = False
            brief = ""
        if delegate:
            should_reply = True
            if not ack:
                ack = random.choice(
                    [
                        "这个我得到后台跑一下，稍等～",
                        "我去后台处理，弄好发你",
                        "我安排一下，马上回来",
                    ]
                )
            if not brief:
                brief = str(message.content or "")[:600]
        return {
            "should_reply": should_reply,
            "intent": intent,
            "need_tools": need_tools,
            "tools": selected,
            "user_emotion": emotion,
            "emotion_intensity": intensity,
            "tone": str(data.get("tone") or "warm"),
            "ack": ack,
            "traits": [str(item)[:30] for item in traits[:6]],
            "impression_note": str(data.get("impression_note") or "")[:240],
            "missing_capability": str(data.get("missing_capability") or "")[:120],
            "delegate_to_opencode": delegate,
            "opencode_brief": brief,
            "source": "planner",
        }

    @staticmethod
    def _wants_file_research(text: str) -> bool:
        value = str(text or "").lower()
        if not value:
            return False
        return any(
            word in value
            for word in (
                "数据集",
                "kaggle",
                "下载",
                "爬取",
                "抓取",
                "训练集",
                "标注",
                "label",
                "放到桌面",
                "放到我的桌面",
                "保存到桌面",
                "存到桌面",
            )
        )

    @staticmethod
    def _wants_opencode(text: str) -> bool:
        value = str(text or "").lower()
        if not value:
            return False
        if not any(
            word in value
            for word in ("opencode", "open code", "后台执行器")
        ):
            return False
        if any(
            word in value
            for word in (
                "不要用opencode",
                "不要用 opencode",
                "别用opencode",
                "不用opencode",
                "不要opencode",
                "opencode不行",
                "opencode没用",
            )
        ):
            return False
        return any(
            phrase in value
            for phrase in (
                "让opencode",
                "让 opencode",
                "用opencode",
                "用 opencode",
                "交给opencode",
                "交给 opencode",
                "opencode做",
                "opencode 做",
                "后台执行器",
                "后台跑",
                "让后台",
                "交给后台",
            )
        )

    def _fallback_plan(
        self, message: Message, config: GroupConfig, tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        text = str(message.content or "").lower()
        allowed = {str(item.get("name") or "") for item in tools}
        task_words = (
            "查",
            "搜索",
            "天气",
            "新闻",
            "视频",
            "推荐",
            "知识库",
            "帮我",
            "看一下",
            "找一下",
            "翻译",
            "总结",
            "表格",
            "excel",
            "点检表",
        )
        privacy_words = ("个人信息", "手机号", "住址", "身份证", "查人")
        emotion_words = {
            "生气": "angry",
            "烦": "frustrated",
            "焦虑": "anxious",
            "难过": "sad",
            "开心": "happy",
            "笑死": "playful",
            "急": "anxious",
        }
        intent = "chat"
        if any(word in text for word in privacy_words):
            intent = "privacy"
        elif any(word in text for word in task_words):
            intent = "task"
        elif any(word in text for word in emotion_words):
            intent = "emotional"
        need_tools = intent not in {"privacy", "spam"} and any(
            name in allowed
            for name in ("call_interface", "bili_search", "bili_knowledge_search")
        ) and any(word in text for word in task_words)
        selected = []
        if need_tools:
            if any(word in text for word in ("表格", "excel", "点检表")) and "create_excel" in allowed:
                selected = ["create_excel"]
            elif "天气" in text and "call_interface" in allowed:
                selected = ["call_interface"]
            elif "视频" in text and "bili_knowledge_search" in allowed:
                selected = ["bili_knowledge_search"]
        complex_words = (
            "写脚本",
            "写个脚本",
            "写代码",
            "写个程序",
            "python",
            "ppt",
            "幻灯片",
            "爬虫",
            "自动化",
            "工作区",
            "批量",
            "多步",
            "桌面",
            "生成报告",
            "运行脚本",
            "命令行",
            "宏",
        )
        delegate = (
            intent not in {"privacy", "spam"}
            and (
                any(word in text for word in complex_words)
                or self._wants_opencode(str(message.content or ""))
                or self._wants_file_research(str(message.content or ""))
            )
        )
        if delegate:
            intent = "task"
        emotion = next(
            (value for word, value in emotion_words.items() if word in text),
            "neutral",
        )
        return {
            "should_reply": bool(str(message.content or "").strip()),
            "intent": intent,
            "need_tools": bool(selected),
            "tools": selected,
            "user_emotion": emotion,
            "emotion_intensity": 0.5,
            "tone": "warm",
            "ack": (
                "我去后台处理，稍等～"
                if delegate
                else ("我去看看，稍等～" if selected else "")
            ),
            "traits": [],
            "impression_note": "",
            "missing_capability": "",
            "delegate_to_opencode": delegate,
            "opencode_brief": str(message.content or "")[:600] if delegate else "",
            "source": "fallback",
        }

    # -------------------------------------------------------------- chat tasks
    async def _create_chat_task(
        self,
        *,
        group: Group,
        message: Message,
        plan: dict[str, Any],
        account_key: str,
    ) -> dict[str, Any]:
        async with SessionLocal() as db:
            row = ChatTask(
                group_id=group.id,
                account_key=account_key,
                message_id=message.id,
                user_wxid=str(message.sender_wxid or ""),
                user_name=str(message.sender_name or ""),
                intent=str(plan.get("intent") or "task"),
                query=str(message.content or ""),
                plan=plan,
                status="pending",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            payload = self._chat_task_dict(row)
        self.runtime.broadcast("chat_task.updated", payload)
        return payload

    def _chat_task_dict(self, row: ChatTask) -> dict[str, Any]:
        return {
            "id": row.id,
            "group_id": row.group_id,
            "message_id": row.message_id,
            "user_wxid": row.user_wxid,
            "user_name": row.user_name,
            "intent": row.intent,
            "query": row.query,
            "engine": str((row.plan or {}).get("engine") or "qunban"),
            "status": row.status,
            "result": row.result,
            "error": row.error,
            "created_at": row.created_at.isoformat() + "Z"
            if row.created_at
            else "",
            "updated_at": row.updated_at.isoformat() + "Z"
            if row.updated_at
            else "",
            "finished_at": row.finished_at.isoformat() + "Z"
            if row.finished_at
            else "",
        }

    async def _update_chat_task(
        self, task_id: int, status: str, *, result: str = "", error: str = ""
    ) -> dict[str, Any] | None:
        async with SessionLocal() as db:
            row = await db.get(ChatTask, task_id)
            if row is None:
                return None
            row.status = status
            if result:
                row.result = result[:8000]
            if error:
                row.error = error[:2000]
            if status in {"done", "error", "cancelled"}:
                row.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
            await db.refresh(row)
            payload = self._chat_task_dict(row)
        self.runtime.broadcast("chat_task.updated", payload)
        return payload

    def _start_chat_task(self, task_id: int) -> None:
        job = asyncio.create_task(
            self._run_chat_task(task_id), name=f"chat-task-{task_id}"
        )
        self._task_jobs.add(job)
        job.add_done_callback(self._task_jobs.discard)

    async def recover_chat_tasks(self) -> None:
        """Resume queued tasks after a restart; mark interrupted ones."""

        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(ChatTask)
                    .where(ChatTask.status.in_(("pending", "running")))
                    .order_by(ChatTask.id.asc())
                    .limit(20)
                )
            ).scalars().all()
        for row in rows:
            if row.status == "running":
                await self._update_chat_task(
                    row.id, "error", error="服务重启中断，请重新发起"
                )
            else:
                self._start_chat_task(row.id)

    async def close(self) -> None:
        jobs = list(self._task_jobs)
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        self._task_jobs.clear()

    async def _run_chat_task(self, task_id: int) -> None:
        async with SessionLocal() as db:
            task = await db.get(ChatTask, task_id)
            if task is None or task.status not in {"pending", "running"}:
                return
            group = await db.get(Group, task.group_id)
            config = await db.get(GroupConfig, task.group_id)
            settings = await get_settings(db)
            message = (
                await db.get(Message, task.message_id)
                if task.message_id
                else None
            )
            if group is None or config is None:
                await self._update_chat_task(task_id, "error", error="群聊不存在")
                return
            plan = dict(task.plan or {})
            context, members = await self._build_context(db, group.id, settings)
            memory_text = await self._load_memory_context(
                db, group.id, settings, message
            )
            stickers = await self._load_sticker_catalog(db, config)
            impression = await get_impression(
                group.id,
                str(task.user_wxid or ""),
                self.runtime.account_key or str(task.account_key or ""),
            )
        if message is None:
            message = Message(
                id=int(task.message_id or 0),
                group_id=group.id,
                sender_wxid=task.user_wxid,
                sender_name=task.user_name,
                content=task.query,
                is_self=False,
                kind="text",
            )
        await self._update_chat_task(task_id, "running")

        # Natural acknowledgement first, under the send lock only.
        ack = str(plan.get("ack") or "我去看看，稍等～")
        if ack and not self.runtime.detached and self.runtime.is_online:
            lock = self._locks.setdefault(group.id, asyncio.Lock())
            async with lock:
                try:
                    await self.runtime.send_agent_message(
                        group.id,
                        text=ack,
                        meta={
                            "source": "llm",
                            "task_id": task_id,
                            "task_ack": True,
                        },
                    )
                except Exception:
                    pass

        if plan.get("delegate_to_opencode"):
            await self._run_opencode_task(
                task_id=task_id,
                group=group,
                config=config,
                settings=settings,
                message=message,
                plan=plan,
                context=context,
                memory_text=memory_text,
                impression=impression,
                reason="planner 判定为复杂任务",
            )
            return

        try:
            reply_data, meta = await self._generate_llm(
                message,
                group,
                config,
                settings,
                context,
                members,
                stickers,
                memory_text,
                plan=plan,
                impression=impression,
                force_tools=True,
            )
        except Exception as exc:
            await self._log(
                group.id,
                "opencode",
                f"群聊工具执行失败，转交 OpenCode：{exc}",
                level="warn",
            )
            await self._run_opencode_task(
                task_id=task_id,
                group=group,
                config=config,
                settings=settings,
                message=message,
                plan=plan,
                context=context,
                memory_text=memory_text,
                impression=impression,
                reason=f"群聊工具执行失败：{exc}",
            )
            return
        if meta.get("tool_required_unmet"):
            await self._log(
                group.id,
                "opencode",
                "群聊工具不足或模型没有调用工具，转交 OpenCode 继续",
                level="warn",
            )
            await self._run_opencode_task(
                task_id=task_id,
                group=group,
                config=config,
                settings=settings,
                message=message,
                plan=plan,
                context=context,
                memory_text=memory_text,
                impression=impression,
                reason="群聊工具不足，需要后台执行器",
            )
            return
        if reply_data is None:
            await self._run_opencode_task(
                task_id=task_id,
                group=group,
                config=config,
                settings=settings,
                message=message,
                plan=plan,
                context=context,
                memory_text=memory_text,
                impression=impression,
                reason="群聊模型没有生成回复",
            )
            return
        async with SessionLocal() as db:
            segments = await self._build_segments(db, config, reply_data)
        if not segments:
            await self._run_opencode_task(
                task_id=task_id,
                group=group,
                config=config,
                settings=settings,
                message=message,
                plan=plan,
                context=context,
                memory_text=memory_text,
                impression=impression,
                reason="群聊模型返回空回复",
            )
            return
        lock = self._locks.setdefault(group.id, asyncio.Lock())
        sent_text = ""
        async with lock:
            for index, segment in enumerate(segments):
                if index:
                    await asyncio.sleep(random.uniform(0.5, 1.1))
                text = str(segment.get("text") or "")
                try:
                    await self.runtime.send_agent_message(
                        group.id,
                        text=text,
                        sticker=segment.get("sticker"),
                        meta={
                            **meta,
                            "source": "llm",
                            "task_id": task_id,
                            "segment": index + 1,
                            "segment_count": len(segments),
                        },
                        reuse_chat=index > 0,
                    )
                    sent_text += text
                except Exception as exc:
                    await self._update_chat_task(task_id, "error", error=str(exc))
                    return
        self._mark_reply_sent(group.id, config)
        await self._update_chat_task(
            task_id, "done", result=(sent_text or "仅表情")[:4000]
        )

    def _mark_reply_sent(self, group_id: int, config: GroupConfig) -> None:
        now = time.time()
        self._last_reply[group_id] = now
        self._reply_times[group_id].append(now)
        self._next_reply_at[group_id] = now + max(
            int(config.cooldown_seconds or 0), 0
        )

    @staticmethod
    def _split_plain_text(text: str, limit: int = 500) -> list[str]:
        value = str(text or "").strip()
        if not value:
            return []
        parts: list[str] = []
        while value and len(parts) < 3:
            if len(value) <= limit:
                parts.append(value)
                value = ""
                break
            cut = value.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = limit
            parts.append(value[:cut].strip())
            value = value[cut:].strip()
        if value and parts:
            parts[-1] = parts[-1].rstrip("…") + "…"
        return [part for part in parts if part]

    @staticmethod
    def _clean_opencode_reply(text: str) -> str:
        value = str(text or "").strip()
        value = re.sub(r"```.*?```", "", value, flags=re.S)
        value = re.sub(
            r"(?m)^.*(?:文件路径|输出目录|保存路径|路径)[:：].*$\n?",
            "",
            value,
        )
        value = re.sub(r"[A-Za-z]:\\[^\s\n，。；、]+", "", value)
        value = re.sub(
            r"(?:data[\\/]agent_workspace[\\/]exports|exports[\\/]task-\d+)"
            r"(?:[\\/][^\s，。；、]*)?",
            "",
            value,
        )
        value = re.sub(r"(?m)^\s*[-*]\s*", "", value)
        value = value.replace("`", "")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        value = re.sub(r"文件路径[:：]?\s*$", "", value)
        value = value.strip()
        return value or "任务跑完了，文件已经发你了。"

    def _opencode_brief(
        self,
        *,
        group: Group,
        message: Message,
        plan: dict[str, Any],
        context: list[str],
        memory_text: str,
        impression: dict[str, Any],
        output_dir: Path,
        reason: str,
    ) -> str:
        plan_view = {
            key: plan.get(key)
            for key in (
                "intent",
                "tools",
                "missing_capability",
                "opencode_brief",
                "ack",
            )
        }
        return f"""# 群伴任务
- 群名：{group.name or '未知'}
- 发送者：{message.sender_name or '未知'}（{message.sender_wxid or ''}）
- 转交原因：{reason or '复杂任务'}
- 原始需求：
{message.content or ''}

# 任务计划
{json.dumps(plan_view, ensure_ascii=False)}

# 最近上下文
{chr(10).join(context[-10:])}

# 长期记忆
{(memory_text or '无')[:1500]}

# 对这个人的印象
{impression_prompt(impression or {})}

# 输出要求
- 需要生成文件时，写到：{output_dir}
- 文件名要清晰，最多保留 6 个最终文件。
- 完成后用简洁中文说明：实际做了什么、结果、生成了哪些文件、还有什么没完成。
- 不要直接给群友发消息，不要操作微信窗口；最终由群伴 Agent 统一发送。
- 群聊最终答复只写群友需要看的结果，用 1-3 句自然中文；不要写绝对路径、内部目录、代码块、工具名或 Markdown 清单，文件会由群伴自动附上。
"""

    def _opencode_task_system_prompt(self, output_dir: Path) -> str:
        from app.services.assistant_tools import workspace_root
        from app.services.opencode_agent import WORKSPACE

        return f"""你是群伴的「后台执行器」，由 OpenCode 运行。群伴 Agent 已经把复杂任务转交给你。
- 你的工作区：{WORKSPACE}
- 群伴工作区：{workspace_root()}
- 你可以写脚本、运行命令、读写文件、使用 OpenCode 原生工具和 qunban_* MCP 工具，也可以使用现有 skills/projects。
- 运行 Python 项目脚本时优先用：{sys.executable}（已安装项目依赖，包括 openpyxl）。
- 如果任务是优化已有 Excel，先在 `data/agent_workspace/exports/` 找最新的相关文件，改好后写到：{output_dir}
- 需要生成新文件时写到：{output_dir}
- 如果用户要求“放到桌面/保存到桌面”，最终文件要同时复制到桌面和输出目录；群伴只会自动发送输出目录里的文件。
- 不要直接给群友发消息，不要操作微信窗口；群伴 Agent 会统一发送结果。
- 遇到权限确认或选择题，群伴 Agent 会自动确认；你按最合理的方案继续，不要停下来反复追问。
- 最终说明实际做了什么、结果、文件路径、未完成项；不要声称未执行的事情。
- 给群友的最终答复要短、自然，不要贴绝对路径或内部目录，文件会自动附带。
"""

    async def _run_opencode_task(
        self,
        *,
        task_id: int,
        group: Group,
        config: GroupConfig,
        settings: dict[str, Any],
        message: Message,
        plan: dict[str, Any],
        context: list[str],
        memory_text: str,
        impression: dict[str, Any],
        reason: str = "",
    ) -> None:
        from app.services.opencode_agent import OpenCodeError, get_opencode_runtime

        output_dir = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "agent_workspace"
            / "exports"
            / f"task-{task_id}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        brief = self._opencode_brief(
            group=group,
            message=message,
            plan=plan,
            context=context,
            memory_text=memory_text,
            impression=impression,
            output_dir=output_dir,
            reason=reason,
        )
        system_prompt = self._opencode_task_system_prompt(output_dir)
        session_id = f"group-task-{task_id}-{getattr(message, 'id', 0) or 0}"

        async with SessionLocal() as db:
            row = await db.get(ChatTask, task_id)
            if row is not None:
                row.plan = {
                    **(row.plan or {}),
                    "engine": "opencode",
                    "output_dir": str(output_dir),
                    "opencode_reason": reason,
                }
                await db.commit()
                await db.refresh(row)
                self.runtime.broadcast(
                    "chat_task.updated", self._chat_task_dict(row)
                )

        async def on_progress(event: dict[str, Any]) -> None:
            phase = str(event.get("phase") or "")
            if phase == "tool":
                tool = str(event.get("tool") or "tool")
                thought = str(event.get("thought") or "").strip()
                await self._log(
                    group.id,
                    "opencode",
                    f"OpenCode 调用 {tool}：{thought[:160]}",
                    level="info",
                )
            elif phase == "permission":
                await self._log(
                    group.id,
                    "opencode",
                    "已自动确认 OpenCode 权限："
                    f"{event.get('tool') or ''} {event.get('patterns') or ''}",
                    level="info",
                )
            elif phase == "question":
                await self._log(
                    group.id,
                    "opencode",
                    f"已自动回答 OpenCode 确认选项：{event.get('answers') or ''}",
                    level="info",
                )
            elif phase == "tool_result" and event.get("ok") is False:
                await self._log(
                    group.id,
                    "opencode",
                    f"OpenCode 工具失败：{event.get('tool') or ''} "
                    f"{str(event.get('message') or '')[:160]}",
                    level="warn",
                )

        await self._log(
            group.id,
            "opencode",
            f"任务 #{task_id} 转交 OpenCode：{reason or '复杂任务'}",
            level="info",
        )

        runtime = get_opencode_runtime()
        result: dict[str, Any] = {}
        try:
            result = await runtime.chat(
                brief,
                session_id=session_id,
                system_prompt=system_prompt,
                settings=settings,
                progress=on_progress,
                timeout=1800.0,
            )
        except OpenCodeError as exc:
            await self._update_chat_task(
                task_id, "error", error=f"OpenCode 执行失败：{exc}"
            )
            await self._send_task_notice(
                group.id,
                "后台执行器没跑起来，我已经把错误记下来了；稍后可以再让我试一次。",
            )
            return
        except Exception as exc:
            await self._update_chat_task(
                task_id, "error", error=f"OpenCode 执行异常：{exc}"
            )
            await self._send_task_notice(
                group.id,
                "后台执行器出了点问题，我已经把错误记下来了。",
            )
            return

        reply = str(result.get("reply") or "").strip()
        if result.get("timed_out") or (result.get("error") and not reply):
            await self._log(
                group.id,
                "opencode",
                "OpenCode 超时或中断，继续一次",
                level="warn",
            )
            try:
                result = await runtime.chat(
                    "继续完成刚才的任务，不要重复已经完成的步骤。"
                    "完成后给出最终结果、文件路径和未完成项。",
                    session_id=session_id,
                    system_prompt=system_prompt,
                    settings=settings,
                    progress=on_progress,
                    timeout=1800.0,
                )
                reply = str(result.get("reply") or "").strip()
            except Exception as exc:
                await self._update_chat_task(
                    task_id, "error", error=f"OpenCode 继续执行失败：{exc}"
                )
                await self._send_task_notice(
                    group.id,
                    "后台执行器中断了，我没拿到最终结果，先把任务停在这里。",
                )
                return

        if not reply:
            await self._update_chat_task(
                task_id, "error", error="OpenCode 没有返回结果"
            )
            await self._send_task_notice(
                group.id,
                "后台执行器跑完了，但没有返回可读结果，我先不糊弄你。",
            )
            return

        reply = self._clean_reply(self._clean_opencode_reply(reply))
        if not reply:
            reply = "任务跑完了，但结果不太完整，我再整理一下。"
        files: list[Path] = []
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                if path.stat().st_size > 25 * 1024 * 1024:
                    continue
            except OSError:
                continue
            files.append(path)
            if len(files) >= 6:
                break

        segments = self._split_plain_text(reply, 500) or [reply]
        lock = self._locks.setdefault(group.id, asyncio.Lock())
        sent_text = ""
        async with lock:
            for index, segment in enumerate(segments):
                if index:
                    await asyncio.sleep(random.uniform(0.5, 1.1))
                try:
                    await self.runtime.send_agent_message(
                        group.id,
                        text=segment,
                        files=files if index == len(segments) - 1 else None,
                        meta={
                            "source": "opencode",
                            "engine": "opencode",
                            "task_id": task_id,
                            "segment": index + 1,
                            "segment_count": len(segments),
                        },
                        reuse_chat=index > 0,
                    )
                    sent_text += segment
                except Exception as exc:
                    await self._update_chat_task(
                        task_id, "error", error=str(exc)
                    )
                    return
        self._mark_reply_sent(group.id, config)
        await self._update_chat_task(
            task_id, "done", result=(sent_text or "OpenCode 完成")[:4000]
        )

    async def _send_task_notice(self, group_id: int, text: str) -> None:
        try:
            await self.runtime.send_agent_message(
                group_id,
                text=text,
                meta={"source": "opencode", "task_notice": True},
            )
        except Exception:
            pass

    async def _handle(self, group_id: int, message_id: int) -> None:
        if self.runtime.detached:
            return
        async with SessionLocal() as db:
            message = await db.get(Message, message_id)
            group = await db.get(Group, group_id)
            config = await db.get(GroupConfig, group_id)
            if message is None or group is None or config is None:
                return
            if message.is_from_agent or message.is_self:
                return
            if message.kind not in ("text", "app"):
                return
            if not config.read_enabled or not config.agent_enabled:
                return

            settings = await get_settings(db)
            if not self._triggered(message, config, settings):
                return
            if config.probability <= 0 or random.random() > config.probability:
                return
            if not self._passes_limits(group_id, config, settings):
                return
            if not self.runtime.is_online:
                return

            lock = self._locks.setdefault(group_id, asyncio.Lock())

        # Merge a burst from the same sender only.  Different members must not
        # cancel each other just because one reply's UI send is still running.
        trigger_key = (
            group_id,
            str(message.sender_wxid or message.sender_name or message.id),
        )
        self._latest_trigger[trigger_key] = message_id
        # Short burst-coalescing window. The previous 1.0-2.5s window made a
        # normal group reply feel noticeably delayed.
        await asyncio.sleep(random.uniform(0.3, 0.9))
        if self._latest_trigger.get(trigger_key) != message_id:
            return

        async with lock:
            # Configuration may have changed while waiting for the lock.
            async with SessionLocal() as db:
                config = await db.get(GroupConfig, group_id)
                settings = await get_settings(db)
                if config is None or not config.read_enabled or not config.agent_enabled:
                    return
                if not self._passes_limits(group_id, config, settings):
                    return
                context, members = await self._build_context(db, group_id, settings)
                memory_text = await self._load_memory_context(
                    db, group_id, settings, message
                )
                stickers = await self._load_sticker_catalog(db, config)
                message = await db.get(Message, message_id)
                if message is not None:
                    tool_payload = await catalog_for_scope("group")
                    bili_context = await self._load_bili_context()
                    impression = await get_impression(
                        group_id,
                        str(message.sender_wxid or ""),
                        self.runtime.account_key or "",
                    )
                    plan = await self._plan_intent(
                        message,
                        group,
                        config,
                        settings,
                        context,
                        memory_text,
                        impression,
                        tool_payload,
                        bili_context,
                    )
                    if not settings.get("llm_api_key"):
                        plan["need_tools"] = False
                        plan["tools"] = []
                        plan["ack"] = ""
                    if (
                        (
                            self._wants_opencode(str(message.content or ""))
                            or self._wants_file_research(
                                str(message.content or "")
                            )
                        )
                        and plan.get("intent") not in {"privacy", "spam"}
                    ):
                        plan["should_reply"] = True
                        plan["intent"] = "task"
                        plan["need_tools"] = False
                        plan["tools"] = []
                        plan["delegate_to_opencode"] = True
                        plan["ack"] = (
                            plan.get("ack")
                            or "我这就交给后台 OpenCode 处理，稍等～"
                        )
                        plan["opencode_brief"] = (
                            plan.get("opencode_brief")
                            or str(message.content or "")[:600]
                        )
                    await update_impression(
                        group_id=group_id,
                        user_wxid=str(message.sender_wxid or ""),
                        display_name=str(message.sender_name or ""),
                        account_key=self.runtime.account_key or "",
                        emotion=str(plan.get("user_emotion") or "neutral"),
                        intensity=float(plan.get("emotion_intensity") or 0),
                        traits=list(plan.get("traits") or []),
                        note=str(plan.get("impression_note") or ""),
                    )
                else:
                    plan = {}

            if message is None or self.runtime.detached:
                return
            if not plan.get("should_reply", True):
                await self._log(
                    group_id,
                    "skip",
                    f"意图={plan.get('intent', 'chat')}，情绪={plan.get('user_emotion', 'neutral')}，"
                    "本轮不参与",
                )
                return
            if plan.get("need_tools") or plan.get("delegate_to_opencode"):
                task = await self._create_chat_task(
                    group=group,
                    message=message,
                    plan=plan,
                    account_key=self.runtime.account_key or "",
                )
                await self._log(
                    group_id,
                    "task",
                    f"挂载任务 #{task['id']}：{plan.get('intent', 'task')} "
                    f"tools={plan.get('tools') or []} "
                    f"delegate={bool(plan.get('delegate_to_opencode'))} "
                    f"{str(message.content or '')[:80]}",
                )
                self._start_chat_task(int(task["id"]))
                return
            self.runtime.emit_typing(group_id)
            base_delay = float(settings.get("reply_delay_seconds") or 0)
            random_delay = float(settings.get("reply_delay_random_seconds") or 0)
            delay = max(0.0, base_delay + random.uniform(0.0, random_delay))
            if delay > 0:
                await asyncio.sleep(delay)

            async with SessionLocal() as db:
                config = await db.get(GroupConfig, group_id)
                settings = await get_settings(db)
                group = await db.get(Group, group_id)
                if self.runtime.detached:
                    return
                if (
                    config is None
                    or group is None
                    or not config.read_enabled
                    or not config.agent_enabled
                ):
                    return
                if not self._passes_limits(group_id, config, settings):
                    return

                if settings.get("llm_api_key"):
                    reply_data, meta = await self._generate_llm(
                        message,
                        group,
                        config,
                        settings,
                        context,
                        members,
                        stickers,
                        memory_text,
                        plan=plan,
                        impression=impression,
                    )
                else:
                    reply_data, meta = self._generate_demo(
                        message, config, stickers
                    )
                if reply_data is None:
                    return

                action = str(reply_data.get("action") or "reply").lower()
                segments = (
                    await self._build_segments(db, config, reply_data)
                    if action != "none"
                    else []
                )
                reply_text = " ".join(
                    str(segment.get("text") or "")
                    for segment in segments
                    if segment.get("text")
                ).strip()
                if action == "none" or not segments:
                    await self._log(
                        group_id,
                        "skip",
                        f"模型选择不回复：{meta.get('model', '—')}",
                    )
                    return

                if config.mention_on_reply:
                    mention = f"@{message.sender_name}"
                    for segment in segments:
                        text = str(segment.get("text") or "")
                        if text:
                            if not text.startswith(mention):
                                segment["text"] = f"{mention} {text}"
                            break

                global_interval = float(
                    settings.get("reply_global_interval_seconds") or 0
                )
                async with self._global_lock:
                    wait = self._global_next_reply - time.time()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    if not self.runtime.is_online or self.runtime.detached:
                        return
                    if not config.read_enabled or not config.agent_enabled:
                        return
                    if not self._passes_limits(group_id, config, settings):
                        return
                    sent_any = False
                    sticker_count = 0
                    for index, segment in enumerate(segments):
                        if index:
                            await asyncio.sleep(random.uniform(0.7, 1.5))
                        segment_meta = {
                            **meta,
                            "segment": index + 1,
                            "segment_count": len(segments),
                        }
                        # Screenshot/tool images belong to the first segment only.
                        if index > 0:
                            segment_meta["tool_trace"] = []
                        sent = await self.runtime.send_agent_message(
                            group_id,
                            text=str(segment.get("text") or ""),
                            sticker=segment.get("sticker"),
                            meta=segment_meta,
                            reuse_chat=index > 0,
                        )
                        if sent:
                            sent_any = True
                        if segment.get("sticker") is not None:
                            sticker_count += 1
                    if not sent_any:
                        return

                    now = time.time()
                    self._last_reply[group_id] = now
                    self._reply_times[group_id].append(now)
                    self._global_next_reply = now + global_interval + random.uniform(
                        0.0, global_interval * 0.5
                    )
                    cooldown_jitter = float(
                        settings.get("reply_cooldown_jitter_seconds") or 0
                    )
                    self._next_reply_at[group_id] = (
                        now
                        + max(int(config.cooldown_seconds or 0), 0)
                        + random.uniform(0.0, max(cooldown_jitter, 0.0))
                    )
                sticker_part = f"｜表情：{sticker_count} 个" if sticker_count else ""
                await self._log(
                    group_id,
                    "reply",
                    f"回复：{(reply_text or '仅表情')[:80]}{sticker_part}｜"
                    f"消息：{len(segments)} 条｜模型：{meta.get('model', '—')}"
                    f"｜耗时：{meta.get('latency_ms', 0)}ms",
                )

    # ------------------------------------------------------------------ trigger
    def _triggered(
        self,
        message: Message,
        config: GroupConfig,
        settings: dict[str, Any],
    ) -> bool:
        text = (message.content or "").strip()
        if not text:
            return False
        mode = config.trigger_mode
        if mode == "all":
            return True
        if mode == "keyword":
            keywords = [str(k).strip() for k in (config.keywords or []) if str(k).strip()]
            lowered = text.lower()
            return any(keyword.lower() in lowered for keyword in keywords)
        if mode == "mention":
            if message.meta and message.meta.get("mentioned_me"):
                return True
            aliases = self._bot_aliases(settings)
            compact = text.replace("\u2005", " ").replace("\xa0", " ")
            return any(f"@{alias}" in compact for alias in aliases)
        return False

    def _bot_aliases(self, settings: dict[str, Any]) -> list[str]:
        aliases: set[str] = set()
        name = (self.runtime.bot_name or "").strip()
        if name:
            aliases.add(name)
        raw = str(settings.get("bot_aliases") or "")
        for item in re.split(r"[,，、;；\s]+", raw):
            item = item.strip()
            if item:
                aliases.add(item)
        return list(aliases)

    def _passes_limits(
        self,
        group_id: int,
        config: GroupConfig,
        settings: dict[str, Any],
    ) -> bool:
        now = time.time()
        if now < self._next_reply_at.get(group_id, 0.0):
            return False
        times = self._reply_times[group_id]
        while times and now - times[0] > 3600:
            times.popleft()
        # Throttle randomly below the configured hourly ceiling: near the
        # limit the bot becomes progressively less likely to answer.
        configured_cap = max(int(config.max_replies_per_hour or 1), 1)
        effective_cap = max(1, int(configured_cap * random.uniform(0.65, 1.0)))
        if len(times) >= effective_cap:
            return False
        return not self._in_quiet_hours(config, str(settings.get("timezone") or "Asia/Shanghai"))

    @staticmethod
    def _in_quiet_hours(config: GroupConfig, timezone_name: str) -> bool:
        start = (config.quiet_start or "").strip()
        end = (config.quiet_end or "").strip()
        if not start or not end or start == end:
            return False
        try:
            now = datetime.now(ZoneInfo(timezone_name)).time()
            start_t = datetime.strptime(start, "%H:%M").time()
            end_t = datetime.strptime(end, "%H:%M").time()
        except Exception:
            return False
        if start_t < end_t:
            return start_t <= now < end_t
        return now >= start_t or now < end_t

    # ------------------------------------------------------------------ context
    async def _build_context(
        self,
        db: AsyncSession,
        group_id: int,
        settings: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        limit = int(settings.get("context_messages") or 18)
        rows = (
            await db.execute(
                select(Message)
                .where(Message.group_id == group_id)
                .order_by(Message.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        rows = list(reversed(rows))
        tz = ZoneInfo(str(settings.get("timezone") or "Asia/Shanghai"))
        lines: list[str] = []
        context_rows: list[Message] = []

        def _timestamp(value: datetime | None) -> float:
            if value is None:
                return 0.0
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()

        for row in rows:
            content = self._flatten(row.content)
            # Replica stores an optimistic Agent row and later the real
            # WeChat echo with the same text. Feeding both to the model made
            # it look like it had answered twice and increased prompt size.
            duplicate = False
            if row.is_self:
                for previous in reversed(context_rows[-8:]):
                    if not previous.is_self:
                        continue
                    if previous.kind != row.kind:
                        continue
                    if previous.is_from_agent == row.is_from_agent:
                        continue
                    if (previous.content or "").strip() != (row.content or "").strip():
                        continue
                    if abs(_timestamp(previous.created_at) - _timestamp(row.created_at)) <= 120:
                        duplicate = True
                        break
            if duplicate:
                continue
            context_rows.append(row)
            created = row.created_at
            if created is None:
                created = datetime.now(timezone.utc)
            elif created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            local = created.astimezone(tz).strftime("%m-%d %H:%M")
            if row.kind == "sticker":
                content = f"[表情包] {content}".strip()
            elif row.kind in ("image", "voice", "video", "app", "system", "other") and not content:
                content = f"[{row.kind}]"
            lines.append(f"[{local}] {row.sender_name or '未知'}：{content}")
        members = (
            await db.execute(
                select(GroupMember.nickname)
                .where(GroupMember.group_id == group_id)
                .limit(40)
            )
        ).scalars().all()
        return lines, [str(name) for name in members if name]

    async def _load_bili_context(self, limit: int = 5) -> str:
        """Recent watched-video summaries and opinions for chat context."""

        try:
            from app.models import BiliVideo

            async with SessionLocal() as db:
                rows = (
                    await db.execute(
                        select(BiliVideo)
                        .where(BiliVideo.status == "done")
                        .order_by(BiliVideo.updated_at.desc())
                        .limit(16)
                    )
                ).scalars().all()
        except Exception:
            return ""
        lines: list[str] = []
        videos = 0
        for row in rows:
            analysis = row.analysis or {}
            opinion = str(analysis.get("opinion") or "").strip()
            summary = str(row.summary or "").strip()
            if not summary and not opinion:
                continue
            title = str(row.title or row.bvid or "").strip()
            topics = "、".join(str(item) for item in (row.topics or []) if str(item))
            seen = row.updated_at or row.created_at
            stamp = seen.strftime("%m-%d %H:%M") if seen is not None else ""
            lines.append(f"- [{stamp}] 《{title}》")
            if topics:
                lines.append(f"  主题：{topics[:120]}")
            if summary:
                lines.append(f"  总结：{summary[:260]}")
            if opinion:
                lines.append(f"  我的看法：{opinion[:140]}")
            videos += 1
            if videos >= max(1, int(limit)):
                break
        return "\n".join(lines)

    async def _load_memory_context(
        self,
        db: AsyncSession,
        group_id: int,
        settings: dict[str, Any],
        message: Message | None,
    ) -> str:
        if not settings.get("memory_always_include", True) and not settings.get(
            "memory_enabled", True
        ):
            return ""
        from app.services import mem0_memory
        from app.services.memory import (
            DEFAULT_MEMORY_PROMPT_TEMPLATE,
            build_memory_context,
            format_memory_line,
        )

        query = str(message.content or "")[:200] if message is not None else ""
        try:
            text, _ids = await build_memory_context(
                db,
                group_id=group_id,
                account_key=self.runtime.account_key,
                settings=settings,
                query=query,
            )
        except Exception:
            text = ""
        lines = [text] if text else []
        try:
            mem0_items = await mem0_memory.search(
                settings,
                account_key=self.runtime.account_key,
                query=query,
                limit=max(1, int(settings.get("memory_context_items") or 8)),
                group_id=group_id,
                timeout=4.0,
            )
            template = str(
                settings.get("memory_prompt_template")
                or DEFAULT_MEMORY_PROMPT_TEMPLATE
            )
            max_chars = max(
                200, int(settings.get("memory_context_max_chars") or 1800)
            )
            used_chars = sum(len(line) + 1 for line in lines)
            for item in mem0_items:
                line = format_memory_line(item, template)
                if not line or line in lines:
                    continue
                if used_chars + len(line) > max_chars:
                    break
                lines.append(line)
                used_chars += len(line) + 1
        except Exception:
            pass
        return "\n".join(lines)

    async def _load_sticker_catalog(
        self, db: AsyncSession, config: GroupConfig
    ) -> list[Sticker]:
        if not config.sticker_enabled:
            return []
        return list(
            (
                await db.execute(
                    select(Sticker).order_by(Sticker.usage_count.desc()).limit(40)
                )
            ).scalars().all()
        )

    # -------------------------------------------------------------------- reply
    async def _build_segments(
        self,
        db: AsyncSession,
        config: GroupConfig,
        reply_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        max_messages = max(2, min(6, int(config.max_messages or 3)))
        raw = reply_data.get("messages")
        segments: list[dict[str, Any]] = []
        if config.multi_message_enabled and isinstance(raw, list):
            for item in raw[:max_messages]:
                if isinstance(item, str):
                    text = self._clean_reply(item)
                    if text:
                        segments.append({"text": text, "sticker": None})
                    continue
                if not isinstance(item, dict):
                    continue
                sticker_value = item.get("sticker")
                if config.sticker_enabled and sticker_value not in (None, "", False):
                    sticker = await resolve_sticker(db, sticker_value)
                    if sticker is not None:
                        segments.append({"text": "", "sticker": sticker})
                        continue
                text = self._clean_reply(item.get("text") or item.get("reply"))
                if text:
                    segments.append({"text": text, "sticker": None})
        if not segments:
            text = self._clean_reply(reply_data.get("reply"))
            sticker: Sticker | None = None
            value = reply_data.get("sticker")
            if config.sticker_enabled and value not in (None, "", False):
                sticker = await resolve_sticker(db, value)
            if text or sticker is not None:
                segments.append({"text": text, "sticker": sticker})
        return segments[:max_messages]

    async def proactive_message(self, group_id: int) -> None:
        if self.runtime.detached or not self.runtime.is_online:
            return
        async with SessionLocal() as db:
            group = await db.get(Group, group_id)
            config = await db.get(GroupConfig, group_id)
            settings = await get_settings(db)
            if (
                group is None
                or config is None
                or not config.read_enabled
                or not config.agent_enabled
                or not config.proactive_enabled
            ):
                return
            if not self._passes_limits(group_id, config, settings):
                return
            if not settings.get("llm_api_key"):
                return
            context, members = await self._build_context(db, group_id, settings)
            last = (
                await db.execute(
                    select(Message)
                    .where(Message.group_id == group_id)
                    .order_by(Message.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            memory_text = await self._load_memory_context(
                db, group_id, settings, last
            )
            stickers = await self._load_sticker_catalog(db, config)

        lock = self._locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            async with SessionLocal() as db:
                config = await db.get(GroupConfig, group_id)
                settings = await get_settings(db)
                if (
                    config is None
                    or not config.read_enabled
                    or not config.agent_enabled
                    or not config.proactive_enabled
                ):
                    return
                if not self._passes_limits(group_id, config, settings):
                    return
            persona = (config.persona or settings.get("default_persona") or "").strip()
            sticker_catalog = (
                sticker_prompt_catalog(stickers)
                if config.sticker_enabled
                else "（本群已关闭表情包发送）"
            )
            message_limit = max(2, min(6, int(config.max_messages or 3)))
            format_line = (
                '{"action":"reply","messages":[{"text":"..."},{"sticker":3}]}'
                if config.multi_message_enabled
                else '{"action":"reply","reply":"...","sticker":3}'
            )
            multi_line = (
                f"可以拆成 1–{message_limit} 条短消息，表情包穿插在文字之间。"
                if config.multi_message_enabled
                else "通常 1 条，最多 1 个表情包。"
            )
            proactive_extra = (
                str(settings.get("group_prompt_extra") or "").strip() or "无"
            )
            system_prompt = f"""{persona}

# 群聊信息
- 群名：{group.name or '未知群聊'}
- 你的名字：{self.runtime.bot_name or '小助手'}
- 群成员昵称参考：{'、'.join(members[:30]) or '暂未同步'}
- 长期记忆：
{memory_text or '无'}

# 任务
群聊已经安静了一段时间。请你主动发起一个自然、轻松、和最近话题相关的新话题，像群友一样接话，不要像客服问候，也不要总结聊天记录。
{multi_line}
不要编造事实；只输出一个 JSON：
{format_line}
{{"action":"none"}}

# 用户自定义提示词（高优先级，必须遵守）
{proactive_extra}

# 可用表情包
{sticker_catalog}"""
            user_prompt = f"""最近群聊记录（从早到晚）：
{chr(10).join(context)}

请主动发一条新消息，输出 JSON。"""
            try:
                data, result = await self.llm.complete_json(
                    base_url=str(settings.get("llm_base_url") or ""),
                    api_key=str(settings.get("llm_api_key") or ""),
                    model=(config.model or settings.get("llm_model") or "").strip(),
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=float(settings.get("llm_temperature") or 0.8),
                    max_tokens=int(settings.get("llm_max_tokens") or 300),
                    json_mode=bool(settings.get("llm_json_mode", True)),
                )
            except LLMError as exc:
                await self._log(
                    group_id,
                    "error",
                    f"主动消息模型失败：{exc}",
                    level="error",
                )
                return
            if not isinstance(data, dict):
                return
            async with SessionLocal() as db:
                config = await db.get(GroupConfig, group_id)
                if config is None:
                    return
                segments = await self._build_segments(db, config, data)
            if not segments:
                return
            meta = {
                "model": result.get("model") or "",
                "latency_ms": result.get("latency_ms") or 0,
                "source": "llm",
                "proactive": True,
                "prompt_tokens": result.get("prompt_tokens") or 0,
                "completion_tokens": result.get("completion_tokens") or 0,
            }
            sent_any = False
            for index, segment in enumerate(segments):
                if index:
                    await asyncio.sleep(random.uniform(0.7, 1.5))
                sent = await self.runtime.send_agent_message(
                    group_id,
                    text=str(segment.get("text") or ""),
                    sticker=segment.get("sticker"),
                    meta={
                        **meta,
                        "segment": index + 1,
                        "segment_count": len(segments),
                    },
                    reuse_chat=index > 0,
                )
                sent_any = sent_any or bool(sent)
            if sent_any:
                self._last_reply[group_id] = time.time()
                self._reply_times[group_id].append(time.time())
                self._next_reply_at[group_id] = (
                    time.time() + max(int(config.cooldown_seconds or 0), 0)
                )
                await self._log(group_id, "proactive", "Agent 主动发起了一条消息")

    async def _generate_llm(
        self,
        message: Message,
        group: Group,
        config: GroupConfig,
        settings: dict[str, Any],
        context: list[str],
        members: list[str],
        stickers: list[Sticker],
        memory_text: str = "",
        *,
        plan: dict[str, Any] | None = None,
        impression: dict[str, Any] | None = None,
        force_tools: bool = False,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        persona = (config.persona or settings.get("default_persona") or "").strip()
        trigger_label = TRIGGER_LABELS.get(config.trigger_mode, config.trigger_mode)
        sticker_catalog = (
            sticker_prompt_catalog(stickers)
            if config.sticker_enabled
            else "（本群已关闭表情包发送）"
        )
        now_local = datetime.now(
            ZoneInfo(str(settings.get("timezone") or "Asia/Shanghai"))
        ).strftime("%Y-%m-%d %H:%M")
        plan = plan or {
            "should_reply": True,
            "intent": "chat",
            "need_tools": False,
            "tools": [],
            "user_emotion": "neutral",
            "emotion_intensity": 0.0,
            "tone": "warm",
            "ack": "",
            "traits": [],
            "impression_note": "",
            "missing_capability": "",
            "delegate_to_opencode": False,
            "opencode_brief": "",
        }
        tool_payload = await catalog_for_scope("group")
        group_tools = tool_payload.get("tools") or []
        use_tools = bool(plan.get("need_tools") or force_tools) and bool(group_tools)
        tool_lines = []
        for item in group_tools:
            tool_lines.append(
                f"- {item['name']}: {item.get('desc', '')} args={json.dumps(item.get('args') or {}, ensure_ascii=False)}"
            )
        resources = []
        for item in tool_payload.get("skills") or []:
            resources.append(f"skill:{item['name']}")
        for item in tool_payload.get("mcps") or []:
            resources.append(f"mcp:{item['key'].split(':', 1)[-1]}")
        apis = tool_payload.get("apis") or []
        for item in apis:
            resources.append(
                f"api:{item['name']} args={json.dumps(item.get('args') or {}, ensure_ascii=False)}"
            )
        api_example = ""
        if apis:
            api_example = (
                "\n接口参数必须使用 args 里的键，例如：\n"
                + json.dumps(
                    {
                        "action": "tool",
                        "tool": "call_interface",
                        "args": {
                            "name": apis[0]["name"],
                            "args": apis[0].get("args") or {},
                        },
                    },
                    ensure_ascii=False,
                )
            )
        tool_section = ""
        if use_tools:
            tool_names = {str(item.get("name") or "") for item in group_tools}
            tool_example = json.dumps(
                {
                    "action": "tool",
                    "tool": group_tools[0]["name"],
                    "args": group_tools[0].get("args") or {},
                    "thought": "为什么调用",
                },
                ensure_ascii=False,
            )
            bili_hint = ""
            if tool_names & {"bili_knowledge_search", "bili_last_opinion"}:
                bili_hint = (
                    "\n如果用户问你看过/学到了什么、最近对视频的看法，"
                    "必须先调用 bili_knowledge_search 或 bili_last_opinion；"
                    "没有 OBSERVATION 时不要编造具体视频、作者或评论内容。"
                )
            tool_section = f"""

# 已启用的群聊工具（只有这些能调用）
{chr(10).join(tool_lines)}
已启用资源：{'、'.join(resources) or '无'}

# 工具协议
当你确实需要工具时，输出：
{tool_example}{api_example}
工具结果会以 OBSERVATION 返回，然后你再决定回复或继续调用。
参数键必须与工具列表完全一致；如果 OBSERVATION 以 ERROR 开头，先修正参数重试，不要编造结果。
群聊里不要调用屏幕、鼠标、键盘、终端、沙箱或任意路径读写工具；只允许调用上面列出的群聊安全工具。create_excel 只生成表格，不会读取本地文件；生成后会自动把文件发到群里。
不要假装调用过工具；没有拿到 OBSERVATION 就不能声称完成。{bili_hint}"""
        multi_rules = ""
        multi_format = '{"action": "reply", "reply": "要发送的文字，可为空", "sticker": 3}'
        if config.multi_message_enabled:
            message_limit = max(2, min(6, int(config.max_messages or 3)))
            multi_rules = (
                f"- 可以像真人一样连续发 1–{message_limit} 条短消息，每条 1–2 句；"
                "表情包可以穿插在文字之间。\n"
            )
            multi_format = (
                '{"action": "reply", "messages": '
                '[{"text": "第一句"}, {"sticker": 3}, {"text": "第二句"}]}'
            )
        group_info = f"""# 群聊信息
- 群名：{group.name or '未知群聊'}
- 你的名字：{self.runtime.bot_name or '小助手'}
- 当前时间：{now_local}（{settings.get('timezone')}）
- 触发方式：{trigger_label}
- 群成员昵称参考：{'、'.join(members[:30]) or '暂未同步'}"""
        memory_block = f"""# 长期记忆（每次对话自动注入）
以下是过去总结的群聊记忆，可作为背景参考；不要机械复述，也不要编造记忆里没有的内容：
{memory_text or '无'}"""
        bili_context = await self._load_bili_context()
        bili_block = (
            f"""# 最近看过的 B站内容（背景资料，不是新消息）
{ bili_context }
以上只用于回答“你看了什么/学到了什么”；不要把它当成当前群聊的新消息。"""
            if bili_context
            else ""
        )
        intent_block = f"""# 本轮意图（内部参考，绝对不要直接复述）
- 意图：{plan.get('intent', 'chat')}
- 用户情绪：{plan.get('user_emotion', 'neutral')}（强度 {float(plan.get('emotion_intensity') or 0):.2f}）
- 回复语气：{plan.get('tone', 'warm')}
- 是否需要工具：{'是' if use_tools else '否'}
- 缺少的能力：{plan.get('missing_capability') or '无'}
- 对这个人的印象：
{impression_prompt(impression or {})}

# 输出风格（内部规则，不要复述）
- 不要输出工具名、工具调用过程、OBSERVATION、🔧、JSON 字段名或分析过程。
- 需要工具时，用“我去看看”“我查一下”这类自然话过渡；只给最终结果。
- 缺少的能力不是“无”时，直接如实说明当前做不到，不要编造结果或假装完成。
- 需要生成表格时调用 create_excel，把内容整理成 sheets[].rows，第一行是表头；不要调用屏幕/鼠标/键盘类工具。
- 像真人聊天，带一点符合当前情绪的语气，但不要夸张表演。"""
        extra_prompt = str(settings.get("group_prompt_extra") or "").strip()
        extra_block = (
            f"\n# 用户自定义提示词（高优先级，必须遵守）\n{extra_prompt}\n"
            if extra_prompt
            else ""
        )
        json_block = f"""# JSON 格式（必须遵守）
{multi_format}
{{"action": "none"}}
其中 sticker 填表情包 id 或标签，也可为 null。只有当表情包确实贴合语境时才使用。"""
        custom_prompt = str(settings.get("group_system_prompt") or "").strip()
        if custom_prompt:
            system_prompt = f"""# 用户自定义群聊系统提示词
{custom_prompt}

# 人设（每次对话自动注入）
{persona}

{group_info}

{memory_block}
{bili_block}
{intent_block}
{extra_block}
# 可用表情包
{sticker_catalog}

{json_block}
{tool_section}"""
        else:
            system_prompt = f"""{persona}

{group_info}

{memory_block}
{bili_block}
{intent_block}

# 回复规则
1. 你是在微信群里自然地接话，回复最新一条消息，不要像客服或机器人。
2. 通常 1-2 句，最多 80 个汉字；不要使用 Markdown，不要分点列清单。
{multi_rules}3. 不要复述或总结整段聊天，除非有人明确要求总结。
4. 不确定的事实不要编造；涉及隐私、金钱、承诺时保持谨慎。
5. 可以结合上下文和成员昵称，但不要叫错人。
6. 普通模式最多选择一个表情包；连续消息模式可穿插表情包，但不要每条都发。
7. 只输出一个 JSON 对象，不要输出 Markdown 代码块或其他文字。
8. 先直接回应最后一条消息的具体问题；不要用玩笑、人设或旧话题回避。用户在反馈卡顿、答非所问或工具问题时，先说明你实际看到了什么、准备怎么处理。
9. 同一个人连续发多条时，把它们当成一个完整意图回答，不要只抓最后两个字。涉及查询/泄露他人个人信息、手机号、住址等隐私时，明确说明群聊里不能提供，不要编造。
{extra_block}
# 可用表情包
{sticker_catalog}

{json_block}
{tool_section}"""

        user_prompt = f"""以下是「{group.name}」最近的群聊记录（从早到晚）：

{chr(10).join(context)}

请针对最后一条消息决定是否回复，并输出 JSON。"""
        model = (config.model or settings.get("llm_model") or "").strip()
        observations: list[str] = []
        tool_trace: list[dict[str, Any]] = []
        total_prompt = 0
        total_completion = 0
        last_result: dict[str, Any] = {}
        # Allow a tool error to be corrected, a second lookup, and then the
        # final reply.  The old limit of 2 stopped right after the first tool
        # result, so a bad argument could never be retried.
        max_steps = 4 if use_tools else 1
        tool_required = bool(plan.get("need_tools")) and bool(plan.get("tools"))
        tool_retry_used = False
        nudges: list[str] = []
        try:
            for _step in range(max_steps):
                prompt = user_prompt
                if observations:
                    prompt += (
                        "\n\nOBSERVATION\uff08\u771f\u5b9e\u5de5\u5177\u7ed3\u679c\uff0c\u6309\u987a\u5e8f\uff09\uff1a\n"
                        + "\n".join(observations[-3:])
                    )
                if nudges:
                    prompt += "\n\n# 系统提醒（必须遵守）\n" + "\n".join(
                        nudges[-2:]
                    )
                data, result = await self.llm.complete_json(
                    base_url=str(settings.get("llm_base_url") or ""),
                    api_key=str(settings.get("llm_api_key") or ""),
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    temperature=float(settings.get("llm_temperature") or 0.8),
                    max_tokens=int(settings.get("llm_max_tokens") or 300),
                    json_mode=bool(settings.get("llm_json_mode", True)),
                )
                last_result = result
                total_prompt += int(result.get("prompt_tokens") or 0)
                total_completion += int(result.get("completion_tokens") or 0)
                action = str(data.get("action") or "reply").lower()
                if action != "tool":
                    if tool_required and not tool_trace and not tool_retry_used:
                        tool_retry_used = True
                        nudges.append(
                            "本轮是必须使用工具的任务，但你还没有调用任何工具。"
                            "请立刻从工具列表中选择最合适的工具并输出 action=tool。"
                            "如果工具列表里确实没有能完成任务的工具，就明确说当前没有这个能力，"
                            "不要声称已经完成，也不要编造结果。"
                        )
                        continue
                    meta = {
                        "model": result.get("model") or model,
                        "latency_ms": result.get("latency_ms") or 0,
                        "source": "llm",
                        "prompt_tokens": total_prompt,
                        "completion_tokens": total_completion,
                        "tool_trace": tool_trace,
                        "intent": plan.get("intent"),
                        "emotion": plan.get("user_emotion"),
                        "need_tools": use_tools,
                        "tool_required_unmet": bool(
                            tool_required and not tool_trace
                        ),
                    }
                    return data, meta
                if not use_tools:
                    meta = {
                        "model": result.get("model") or model,
                        "latency_ms": result.get("latency_ms") or 0,
                        "source": "llm",
                        "prompt_tokens": total_prompt,
                        "completion_tokens": total_completion,
                        "tool_trace": tool_trace,
                        "intent": plan.get("intent"),
                        "emotion": plan.get("user_emotion"),
                        "need_tools": False,
                    }
                    return {"action": "none"}, meta
                tool_name = str(data.get("tool") or "").strip()
                args = data.get("args") if isinstance(data.get("args"), dict) else {}
                started = time.time()
                try:
                    output = await execute_capability(tool_name, args, "group")
                    output_text = (
                        json.dumps(output, ensure_ascii=False, default=str)
                        if isinstance(output, (dict, list))
                        else str(output)
                    )
                except Exception as exc:
                    output_text = f"ERROR: {exc}"
                tool_trace.append(
                    {
                        "tool": tool_name,
                        "args": args,
                        "output": output_text[:1200],
                        "seconds": round(time.time() - started, 2),
                    }
                )
                failed = output_text.startswith("ERROR")
                await self._log(
                    message.group_id,
                    "tool",
                    f"工具 {tool_name} {'失败' if failed else '成功'} "
                    f"args={json.dumps(args, ensure_ascii=False)} "
                    f"结果={output_text[:180]}",
                    level="warn" if failed else "info",
                )
                observations.append(f"<{tool_name}> {output_text[:5000]}")
            await self._log(
                message.group_id,
                "error",
                f"工具调用达到 {max_steps} 步仍未生成回复",
                level="warn",
            )
            meta = {
                "model": last_result.get("model") or model,
                "latency_ms": last_result.get("latency_ms") or 0,
                "source": "llm",
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "tool_trace": tool_trace,
                "intent": plan.get("intent"),
                "emotion": plan.get("user_emotion"),
                "need_tools": use_tools,
                "tool_required_unmet": bool(tool_required and not tool_trace),
            }
            return {"action": "none"}, meta
        except LLMError as exc:
            await self._log(
                message.group_id,
                "error",
                f"\u6a21\u578b\u8c03\u7528\u5931\u8d25\uff1a{exc}",
                level="error",
            )
            return None, {}

    def _generate_demo(
        self,
        message: Message,
        config: GroupConfig,
        stickers: list[Sticker],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        text = (message.content or "").strip()
        name = message.sender_name or "朋友"
        sticker: Sticker | None = None
        wants_sticker = any(word in text for word in ("表情", "斗图", "发个图", "来个图"))

        if wants_sticker and stickers and config.sticker_enabled:
            reply = random.choice(["接住了 😎", "来，图来了", "这个够不够应景"])
        elif "总结" in text or "待办" in text or "梳理" in text:
            reply = (
                "我捋一下哈：先把当前版本的问题收尾，测试回归完再发；"
                "上线前记得同步运营。就这三件事。"
            )
        elif "在吗" in text or "在不在" in text:
            reply = random.choice(["在的，怎么啦～", "在呢在呢", "我在，你说"])
        elif "你好" in text or "hi" in text.lower():
            reply = random.choice([f"你好呀，{name} 👋", "嗨，今天怎么样？"])
        elif "吃" in text or "奶茶" in text:
            reply = random.choice(["建议火锅，谁反对？", "我投奶茶一票 🧋", "吃啥都行，别问我"])
        elif "周末" in text or "羽毛球" in text:
            reply = "可以啊，我有空，到时候叫我"
        elif "晚安" in text:
            reply = "晚安，早点休息 🌙"
        elif "哈哈" in text or "笑死" in text:
            reply = random.choice(["哈哈哈哈", "真的绷不住了", "笑死我了"])
        else:
            reply = random.choice(
                [
                    "收到，我看看",
                    "有道理",
                    "这个我赞同",
                    "行，那就这么定",
                    "我这边没问题",
                ]
            )
        if (
            not wants_sticker
            and stickers
            and config.sticker_enabled
            and random.random() < 0.35
        ):
            sticker = random.choice(stickers)
        return (
            {
                "action": "reply",
                "reply": reply,
                "sticker": sticker.id if sticker else None,
            },
            {"model": "demo", "latency_ms": 0, "source": "demo"},
        )

    @staticmethod
    def _clean_reply(value: Any) -> str:
        if value is None:
            return ""
        original = str(value).strip()
        text = original
        if text.startswith("```"):
            text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
        text = text.strip().strip('"').strip()
        # Models sometimes leak their tool narration into the final bubble.
        # Keep the human-facing result, never the internal action log.
        text = re.sub(
            r"(?m)^\s*(?:🔧|🛠|工具调用|正在调用|调用工具|使用工具|"
            r"读取|查询|搜索)\s*[^\n]{0,100}[：:][^\n]*\n?",
            "",
            text,
        )
        text = re.sub(r"(?m)^\s*(?:OBSERVATION|工具结果|Tool Result)\b.*$", "", text)
        text = text.replace("OBSERVATION", "").replace("🔧", "").strip()
        if original and not text:
            text = "我去看看，稍等～"
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) > MAX_REPLY_CHARS:
            text = text[:MAX_REPLY_CHARS].rstrip() + "…"
        return text

    @staticmethod
    def _flatten(text: str) -> str:
        value = (text or "").replace("\r", " ").replace("\n", " ").strip()
        return value[:400]

    async def _log(
        self,
        group_id: int | None,
        event: str,
        detail: str,
        level: str = "info",
    ) -> None:
        try:
            async with SessionLocal() as db:
                log = AgentLog(
                    group_id=group_id,
                    level=level,
                    event=event,
                    detail=detail[:1000],
                )
                db.add(log)
                await db.commit()
                await db.refresh(log)
                self.runtime.broadcast("agent.log", agent_log_dict(log))
        except Exception:
            pass
