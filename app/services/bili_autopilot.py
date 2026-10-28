from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    AssistantMessage,
    BiliAutopilotConfig,
    BiliVideo,
    Contact,
    Group,
    utcnow,
)
from app.services import bilibili, bili_knowledge
from app.services.memory import (
    DEFAULT_MEMORY_PROMPT_TEMPLATE,
    format_memory_line,
    search_memories,
)
from app.services.settings import get_settings

if TYPE_CHECKING:
    from app.core.runtime import RuntimeManager

logger = logging.getLogger("bilibili.autopilot")

DEFAULT_TOPICS: list[str] = []


def _config_payload(row: BiliAutopilotConfig | None) -> dict[str, Any]:
    if row is None:
        return {
            "enabled": False,
            "interval_minutes": 60,
            "topics": [],
            "like_enabled": True,
            "favorite_enabled": True,
            "comment_enabled": True,
            "reply_comments_enabled": False,
            "use_agent_context": True,
            "forward_group_id": None,
            "forward_contact_id": None,
            "asr_model": "small",
            "last_run_at": None,
        }
    return {
        "enabled": bool(row.enabled),
        "interval_minutes": int(row.interval_minutes or 60),
        "topics": list(row.topics or []),
        "like_enabled": bool(row.like_enabled),
        "favorite_enabled": bool(row.favorite_enabled),
        "comment_enabled": bool(row.comment_enabled),
        "reply_comments_enabled": bool(row.reply_comments_enabled),
        "use_agent_context": bool(
            row.use_agent_context if row.use_agent_context is not None else True
        ),
        "forward_group_id": row.forward_group_id,
        "forward_contact_id": row.forward_contact_id,
        "asr_model": row.asr_model or "small",
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
    }


def _video_payload(row: BiliVideo) -> dict[str, Any]:
    return {
        "id": row.id,
        "bvid": row.bvid,
        "title": row.title,
        "author": row.author,
        "url": row.url,
        "duration": row.duration,
        "summary": row.summary,
        "topics": list(row.topics or []),
        "analysis": row.analysis or {},
        "actions": row.actions or {},
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _duration_seconds(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    parts = [int(item) for item in re.findall(r"\d+", text)]
    if not parts:
        return 0
    total = 0
    for part in parts:
        total = total * 60 + part
    return total


class BiliAutopilot:
    def __init__(self, runtime: "RuntimeManager") -> None:
        self.runtime = runtime
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.current: dict[str, Any] = {}
        self.started_at = time.time()
        self._login_warned_at = 0.0

    async def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop(), name="bili-autopilot")

    async def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.task = None

    def notify(self) -> None:
        self.wake.set()

    async def get_config(self, account_key: str = "") -> BiliAutopilotConfig | None:
        async with SessionLocal() as db:
            stmt = select(BiliAutopilotConfig).order_by(
                BiliAutopilotConfig.id.desc()
            ).limit(1)
            if account_key:
                stmt = (
                    select(BiliAutopilotConfig)
                    .where(BiliAutopilotConfig.account_key == account_key)
                    .order_by(BiliAutopilotConfig.id.desc())
                    .limit(1)
                )
            return (await db.execute(stmt)).scalar_one_or_none()

    async def save_config(
        self, payload: dict[str, Any], account_key: str = ""
    ) -> dict[str, Any]:
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(BiliAutopilotConfig)
                    .where(BiliAutopilotConfig.account_key == account_key)
                    .order_by(BiliAutopilotConfig.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                row = BiliAutopilotConfig(account_key=account_key)
                db.add(row)
            if "enabled" in payload:
                row.enabled = bool(payload["enabled"])
            if "interval_minutes" in payload:
                row.interval_minutes = max(10, min(1440, int(payload["interval_minutes"])))
            if "topics" in payload:
                topics = payload["topics"]
                if isinstance(topics, str):
                    topics = [item.strip() for item in topics.split(",")]
                row.topics = [
                    str(item).strip()
                    for item in (topics or [])
                    if str(item).strip()
                ][:20]
            for key in (
                "like_enabled",
                "favorite_enabled",
                "comment_enabled",
                "reply_comments_enabled",
                "use_agent_context",
            ):
                if key in payload:
                    setattr(row, key, bool(payload[key]))
            if "forward_group_id" in payload:
                value = payload["forward_group_id"]
                row.forward_group_id = int(value) if value else None
            if "forward_contact_id" in payload:
                value = payload["forward_contact_id"]
                row.forward_contact_id = int(value) if value else None
            if "asr_model" in payload:
                row.asr_model = str(payload["asr_model"] or "small")[:32]
            await db.commit()
            await db.refresh(row)
            result = _config_payload(row)
        if not result.get("enabled"):
            self.current = {}
        self.notify()
        return result

    async def status(self, account_key: str = "") -> dict[str, Any]:
        config = await self.get_config(account_key)
        async with SessionLocal() as db:
            rows = list(
                (
                    await db.execute(
                        select(BiliVideo)
                        .order_by(BiliVideo.id.desc())
                        .limit(20)
                    )
                ).scalars().all()
            )
        knowledge = {"count": 0}
        try:
            knowledge = await bili_knowledge.stats_async()
        except Exception:
            pass
        return {
            "config": _config_payload(config),
            "running": self.lock.locked(),
            "current": self.current,
            "videos": [_video_payload(row) for row in rows],
            "knowledge": knowledge,
        }

    async def _loop(self) -> None:
        while True:
            try:
                await self._maybe_run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("B站自动刷循环失败: %s", exc)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=30)
                self.wake.clear()
            except asyncio.TimeoutError:
                pass

    async def _maybe_run(self) -> None:
        config = await self.get_config()
        if config is None or not config.enabled:
            return
        interval = max(10, int(config.interval_minutes or 60)) * 60
        last = config.last_run_at
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - last).total_seconds() < interval:
                return
        await self.run_cycle(force=False)

    async def run_cycle(self, force: bool = False) -> dict[str, Any]:
        if self.lock.locked():
            return {"started": False, "message": "已有自动刷任务在运行"}
        asyncio.create_task(self._run_cycle(force=force), name="bili-cycle")
        return {"started": True, "message": "已开始自动刷任务"}

    async def _run_cycle(self, force: bool = False) -> None:
        async with self.lock:
            config = await self.get_config()
            payload = _config_payload(config)
            if not force and not payload["enabled"]:
                return
            self.current = {"phase": "start", "message": "开始挑选视频"}
            await self._emit("start", "开始自动刷 B站")
            try:
                await self._cycle(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("B站自动刷任务失败: %s", exc)
                await self._emit("error", f"任务失败：{exc}")

    async def _cycle(self, config: dict[str, Any]) -> None:
        account_key = self.runtime.account_key
        account = await bilibili.status(account_key)
        if not account.get("logged_in"):
            if time.time() - self._login_warned_at > 300:
                self._login_warned_at = time.time()
                message = (
                    "B站登录已失效，请重新扫码登录"
                    if account.get("expired")
                    else "请先在「助手 → B站」登录 B站账号"
                )
                await self._emit("error", message)
            return
        async with SessionLocal() as db:
            settings = await get_settings(db)
            seen = {
                row.bvid
                for row in (await db.execute(select(BiliVideo))).scalars().all()
            }
        agent_context = ""
        if config.get("use_agent_context", True):
            agent_context = await self._agent_context(settings, account_key)
            if agent_context:
                await self._emit(
                    "context",
                    "已加载 Agent 人设、长期记忆和最近对话，本轮由 Agent 自主判断",
                )
        topics = [
            str(item).strip()
            for item in (config.get("topics") or [])
            if str(item).strip()
        ]
        topic = ""
        use_agent_choice = bool(
            config.get("use_agent_context", True) and settings.get("llm_api_key")
        )
        if topics and not use_agent_choice:
            topic = random.choice(topics)
            await self._emit("topic", f"使用兴趣关键词：{topic}")
            videos = await bilibili.search_videos(topic, 12)
        else:
            topic = await self._pick_topic(settings, agent_context, topics)
            if topic:
                await self._emit(
                    "topic",
                    f"Agent 自选主题：{topic}"
                    + ("（用户关键词仅作参考）" if topics else ""),
                )
                videos = await bilibili.search_videos(topic, 12)
            elif topics:
                topic = random.choice(topics)
                await self._emit(
                    "topic", f"Agent 自选失败，回退兴趣关键词：{topic}"
                )
                videos = await bilibili.search_videos(topic, 12)
            else:
                await self._emit("topic", "Agent 直接浏览热门推荐")
                videos = await bilibili.recommend_videos(15)
        def collect(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            result = []
            for video in items:
                bvid = str(video.get("bvid") or "")
                if not bvid or bvid in seen:
                    continue
                seconds = _duration_seconds(str(video.get("duration") or ""))
                if seconds and seconds > 1800:
                    continue
                result.append(video)
            return result

        candidates = collect(videos)
        if not candidates:
            await self._emit("search", "搜索结果没有未处理的短视频，改用热门推荐")
            candidates = collect(await bilibili.recommend_videos(15))
        if not candidates:
            await self._mark_run()
            await self._emit("idle", "没有找到未处理过的短视频")
            return
        video, reason = await self._pick_video(
            candidates, topic or "Agent 自选", settings, agent_context
        )
        bvid = video["bvid"]
        title = video.get("title") or bvid
        await self._emit(
            "selected",
            f"选中视频：{title}" + (f"（{reason}）" if reason else ""),
            bvid=bvid,
            title=title,
        )
        async with SessionLocal() as db:
            row = (
                await db.execute(select(BiliVideo).where(BiliVideo.bvid == bvid))
            ).scalar_one_or_none()
            if row is None:
                row = BiliVideo(
                    account_key=account_key,
                    bvid=bvid,
                    title=title,
                    author=video.get("author") or "",
                    url=video.get("url") or "",
                    duration=str(video.get("duration") or ""),
                )
                db.add(row)
            row.status = "processing"
            row.error = ""
            await db.commit()
            row_id = row.id
        self.current = {"phase": "transcribing", "bvid": bvid, "title": title}
        await self._emit("transcribing", f"正在把音频转成文字：{title}", bvid=bvid, title=title)
        try:
            transcript = await bilibili.transcribe_video(
                bvid, config.get("asr_model") or "small", account_key
            )
        except Exception as exc:
            await self._fail(row_id, f"转写失败：{exc}")
            return
        text = str(transcript.get("text") or "").strip()
        await self._emit(
            "transcribed",
            f"转写完成（{transcript.get('source')}，{len(text)} 字）",
            bvid=bvid,
            title=title,
        )
        await self._emit("analyzing", "正在理解视频内容", bvid=bvid, title=title)
        analysis = await self._analyze(
            title, video.get("author") or "", text, settings, agent_context
        )
        opinion = str(analysis.get("opinion") or "").strip()
        if opinion:
            await self._emit(
                "opinion", f"Agent 看法：{opinion}", bvid=bvid, title=title
            )
        summary = str(analysis.get("summary") or text[:300]).strip()
        topics_out = [
            str(item).strip()
            for item in (analysis.get("topics") or [])
            if str(item).strip()
        ][:12]
        actions: dict[str, Any] = {}
        # Write a compact long-term memory so the group chat model can use
        # what the autopilot watched, even when the video is no longer in the
        # recent BiliVideo window.
        try:
            from app.services.memory import save_memory

            memory_parts = [
                f"B站视频《{title}》",
                f"作者：{video.get('author') or '未知'}",
                f"主题：{'、'.join(topics_out) or '未分类'}",
                f"总结：{summary}",
            ]
            if opinion:
                memory_parts.append(f"我的看法：{opinion}")
            await save_memory(
                group_id=None,
                account_key=account_key,
                scope="global",
                category="bili",
                title=f"B站观看：{title[:60]}",
                content="\n".join(memory_parts)[:1800],
                importance=3,
                source="bili-autopilot",
            )
            actions["memory"] = True
        except Exception as exc:
            actions["memory_error"] = str(exc)
        raw_actions = analysis.get("actions")
        if isinstance(raw_actions, list):
            chosen_actions = {
                str(item).strip().lower() for item in raw_actions if str(item).strip()
            }
        else:
            # Compatibility with older model output.
            chosen_actions = set()
            if analysis.get("like"):
                chosen_actions.add("like")
            if analysis.get("favorite"):
                chosen_actions.add("favorite")
            if analysis.get("comment"):
                chosen_actions.add("comment")
            if analysis.get("reply_text"):
                chosen_actions.add("reply")
        if not chosen_actions:
            await self._emit("action", "Agent 决定本轮只看不互动", bvid=bvid, title=title)
        if "like" in chosen_actions and config.get("like_enabled"):
            try:
                actions["like"] = await bilibili.like_video(
                    bvid, True, account_key
                )
                await self._emit("action", "已点赞", bvid=bvid, title=title)
            except Exception as exc:
                actions["like_error"] = str(exc)
        if "favorite" in chosen_actions and config.get("favorite_enabled"):
            try:
                actions["favorite"] = await bilibili.favorite_video(bvid, account_key)
                await self._emit("action", "已收藏", bvid=bvid, title=title)
            except Exception as exc:
                actions["favorite_error"] = str(exc)
        comment_text = str(
            analysis.get("comment_text") or analysis.get("comment") or ""
        ).strip()
        if "comment" in chosen_actions and config.get("comment_enabled"):
            try:
                actions.update(
                    await self._comment_with_tool(
                        bvid=bvid,
                        title=title,
                        comment_text=comment_text,
                        opinion=opinion,
                        account_key=account_key,
                    )
                )
            except Exception as exc:
                actions["comment_error"] = str(exc)
                await self._emit(
                    "error",
                    f"bili_comment 工具发表评论失败：{exc}",
                    bvid=bvid,
                    title=title,
                )
        reply_text = str(analysis.get("reply_text") or "").strip()
        if "reply" in chosen_actions and config.get("reply_comments_enabled") and reply_text:
            try:
                comments = await bilibili.get_video_comments(bvid, 5, account_key)
                if comments:
                    target = comments[0]
                    actions["reply"] = await bilibili.reply_video_comment(
                        bvid,
                        reply_text[:1000],
                        int(target["rpid"]),
                        account_key=account_key,
                    )
                    await self._emit(
                        "action", "已回复评论", bvid=bvid, title=title
                    )
            except Exception as exc:
                actions["reply_error"] = str(exc)
        forward_text = str(
            analysis.get("forward_text")
            or f"B站视频：{title}\n{summary}\n{video.get('url') or ''}"
        ).strip()
        if forward_text:
            group_id = config.get("forward_group_id")
            contact_id = config.get("forward_contact_id")
            if group_id:
                try:
                    sent = await self.runtime.send_agent_message(
                        int(group_id),
                        text=forward_text,
                        meta={"source": "bili-autopilot"},
                    )
                    actions["forward_group"] = bool(sent)
                    await self._emit(
                        "forwarded", "已转发到群聊", bvid=bvid, title=title
                    )
                except Exception as exc:
                    actions["forward_group_error"] = str(exc)
            if contact_id:
                try:
                    await self.runtime.send_contact_message(
                        int(contact_id), forward_text, is_agent=True
                    )
                    actions["forward_contact"] = True
                    await self._emit(
                        "forwarded", "已转发到联系人", bvid=bvid, title=title
                    )
                except Exception as exc:
                    actions["forward_contact_error"] = str(exc)
        try:
            await bili_knowledge.add_video_async(
                bvid=bvid,
                title=title,
                author=video.get("author") or "",
                url=video.get("url") or "",
                transcript=text,
                summary=summary,
                topics=topics_out,
            )
            actions["knowledge"] = True
            await self._emit("stored", "已写入 B站知识库", bvid=bvid, title=title)
        except Exception as exc:
            actions["knowledge_error"] = str(exc)
        async with SessionLocal() as db:
            row = await db.get(BiliVideo, row_id)
            if row is not None:
                row.title = title
                row.author = video.get("author") or row.author
                row.url = video.get("url") or row.url
                row.duration = str(video.get("duration") or row.duration)
                row.transcript = text
                row.summary = summary
                row.topics = topics_out
                row.analysis = analysis
                row.actions = actions
                row.status = "done"
                row.error = ""
                row.updated_at = utcnow()
                await db.commit()
            config_row = (
                await db.execute(
                    select(BiliAutopilotConfig)
                    .order_by(BiliAutopilotConfig.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if config_row is not None:
                config_row.last_run_at = utcnow()
                await db.commit()
        self.current = {"phase": "done", "bvid": bvid, "title": title}
        await self._emit("done", f"完成：{title}", bvid=bvid, title=title, actions=actions)
        self.notify()

    async def _comment_with_tool(
        self,
        *,
        bvid: str,
        title: str,
        comment_text: str,
        opinion: str,
        account_key: str,
    ) -> dict[str, Any]:
        """Send the Agent's opinion through the same bili_comment tool.

        If the analysis chose `comment` but left `comment_text` empty, the
        independent `opinion` becomes the comment.  This keeps the decision in
        the Agent layer while the actual write goes through the capability
        gateway and its permissions/audit path.
        """
        text = str(comment_text or "").strip()
        if not text:
            text = str(opinion or "").strip()
        if not text:
            return {"comment_skipped": "没有看法或评论正文"}
        text = text[:1000]
        from app.services.capabilities import execute_capability

        await self._emit(
            "action",
            "Agent 正在调用 bili_comment 工具，把自己的看法发到评论区",
            bvid=bvid,
            title=title,
            text=text[:300],
        )
        result = await execute_capability(
            "bili_comment",
            {"bvid": bvid, "text": text},
            "admin",
            account_key=account_key,
        )
        await self._emit(
            "action",
            "已通过 bili_comment 工具发表评论",
            bvid=bvid,
            title=title,
        )
        return {
            "comment": result,
            "comment_text": text,
            "comment_tool": "bili_comment",
            "comment_from_opinion": bool(
                opinion and text == str(opinion).strip()
            ),
        }

    async def _agent_context(
        self, settings: dict[str, Any], account_key: str
    ) -> str:
        """Build the Agent's own taste/context for autonomous B站 decisions."""
        parts: list[str] = []
        persona = str(settings.get("operator_persona") or "").strip()
        extra = str(settings.get("operator_prompt_extra") or "").strip()
        if persona:
            parts.append(f"# Agent 人设\n{persona}")
        if extra:
            parts.append(f"# 用户对 Agent 的额外要求\n{extra}")

        template = str(
            settings.get("memory_prompt_template")
            or DEFAULT_MEMORY_PROMPT_TEMPLATE
        )
        memory_lines: list[str] = []
        try:
            rows = await search_memories(
                query="B站 视频 兴趣 偏好 审美 喜欢 讨厌 内容",
                group_id=None,
                account_key=account_key,
                limit=8,
            )
            for item in rows:
                line = format_memory_line(item, template)
                if line and line not in memory_lines:
                    memory_lines.append(line)
        except Exception:
            pass
        try:
            from app.services import mem0_memory

            items = await mem0_memory.search(
                settings,
                account_key=account_key,
                query="B站 视频 兴趣 偏好 审美 喜欢 讨厌",
                limit=6,
                timeout=3.0,
            )
            for item in items:
                line = format_memory_line(item, template)
                if line and line not in memory_lines:
                    memory_lines.append(line)
        except Exception:
            pass
        if memory_lines:
            parts.append(
                "# 长期记忆与偏好（来自用户对话和 Agent 自己的历史）\n"
                + "\n".join(memory_lines[-12:])
            )

        try:
            async with SessionLocal() as db:
                rows = list(
                    (
                        await db.execute(
                            select(AssistantMessage)
                            .where(
                                AssistantMessage.session_id == "main",
                                AssistantMessage.role.in_(("user", "assistant")),
                            )
                            .order_by(AssistantMessage.id.desc())
                            .limit(8)
                        )
                    ).scalars().all()
                )
            recent = [
                f"{row.role}：{str(row.content or '').strip()[:240]}"
                for row in reversed(rows)
                if str(row.content or "").strip()
            ]
            if recent:
                parts.append(
                    "# 最近与用户的 Operator 对话\n" + "\n".join(recent)
                )
        except Exception:
            pass
        return "\n\n".join(parts)[:6000]

    async def _pick_topic(
        self,
        settings: dict[str, Any],
        agent_context: str = "",
        candidate_topics: list[str] | None = None,
    ) -> str:
        if not settings.get("llm_api_key"):
            return ""
        async with SessionLocal() as db:
            rows = list(
                (
                    await db.execute(
                        select(BiliVideo).order_by(BiliVideo.id.desc()).limit(12)
                    )
                ).scalars().all()
            )
        history = "\n".join(
            f"- {row.title}｜{'、'.join(row.topics or [])}" for row in rows
        ) or "还没有历史记录"
        context_block = f"{agent_context}\n\n" if agent_context else ""
        candidate_text = (
            "、".join(candidate_topics or []) or "无（用户没有限定关键词）"
        )
        try:
            data, _result = await self.runtime.llm.complete_json(
                base_url=str(settings.get("llm_base_url") or ""),
                api_key=str(settings.get("llm_api_key") or ""),
                model=str(settings.get("llm_model") or ""),
                system_prompt=(
                    f"{context_block}"
                    "你是一个有自己内容品味和独立判断的中文 B站用户，不是用户偏好的执行器。"
                    "上面的 Agent 人设、长期记忆和最近对话只是参考；你可以喜欢、不喜欢、"
                    "改变兴趣，也可以主动探索一个最近没接触过的新方向。"
                    "用户可能给出兴趣关键词，但那只是建议，不是命令；"
                    "你可以从中选择，也可以根据自己的判断换成更适合当前心情的主题。"
                    "请结合你已有的视频历史和自己的判断，选择一个你真正想继续了解、"
                    "且最近没有重复刷过的中文搜索主题。不要讨好式地永远选同一类。"
                    "只输出 JSON：{\"topic\":\"一个简短搜索词\"}"
                ),
                user_prompt=(
                    f"用户提供的候选关键词（仅参考）：{candidate_text}\n\n"
                    f"最近刷过的视频：\n{history}\n\n"
                    "请以你自己的品味给出本轮主题；可以沿用候选，也可以提出新方向。"
                ),
                temperature=0.9,
                max_tokens=120,
                json_mode=True,
            )
            topic = str((data or {}).get("topic") or "").strip()
            return topic[:50]
        except Exception as exc:
            logger.warning("B站自选主题失败: %s", exc)
            return ""

    async def _pick_video(
        self,
        candidates: list[dict[str, Any]],
        topic: str,
        settings: dict[str, Any],
        agent_context: str = "",
    ) -> tuple[dict[str, Any], str]:
        if not settings.get("llm_api_key") or len(candidates) == 1:
            return candidates[0], ""
        listing = "\n".join(
            f"- {item.get('bvid')} | {item.get('title')} | {item.get('author')} | {item.get('duration')}"
            for item in candidates[:10]
        )
        context_block = f"{agent_context}\n\n" if agent_context else ""
        try:
            data, _result = await self.runtime.llm.complete_json(
                base_url=str(settings.get("llm_base_url") or ""),
                api_key=str(settings.get("llm_api_key") or ""),
                model=str(settings.get("llm_model") or ""),
                system_prompt=(
                    f"{context_block}"
                    "你是 B站刷视频 Agent，有自己的审美和判断。"
                    "请从候选视频中选择你真正想看、适合完整观看和内容理解的一条。"
                    "用户偏好只是参考，不需要迎合；可以选一个更能激发你兴趣的视频。"
                    "输出 JSON：{\"bvid\":\"BV...\",\"reason\":\"一句话理由\"}"
                ),
                user_prompt=(
                    f"本轮主题：{topic}\n候选：\n{listing}\n"
                    "请按自己的判断选择，并说明理由。"
                ),
                temperature=0.7,
                max_tokens=200,
                json_mode=True,
            )
            bvid = str((data or {}).get("bvid") or "").strip()
            reason = str((data or {}).get("reason") or "").strip()
            for item in candidates:
                if item.get("bvid") == bvid:
                    return item, reason
        except Exception as exc:
            logger.warning("B站选视频失败: %s", exc)
        return candidates[0], ""

    async def _analyze(
        self,
        title: str,
        author: str,
        transcript: str,
        settings: dict[str, Any],
        agent_context: str = "",
    ) -> dict[str, Any]:
        if not settings.get("llm_api_key"):
            return {"summary": transcript[:300], "topics": []}
        context_block = f"{agent_context}\n\n" if agent_context else ""
        system_prompt = f"""{context_block}你是 B站内容理解 Agent。你有自己的内容品味和独立观点，不需要迎合用户偏好。
请根据视频标题、作者和语音转写，输出一个 JSON：
{{"summary":"200字以内中文总结","topics":["主题1","主题2"],"sentiment":"positive|neutral|negative","opinion":"你对这个视频的独立评价/态度，80字以内","actions":["like","favorite","comment","reply"],"comment_text":"只有决定评论时才写，否则空字符串","reply_text":"只有决定回复热评时才写，否则空字符串","forward_text":"转发到微信时的简短推荐语"}}
规则：
- 先形成你自己的 opinion，再决定 actions；用户偏好只是参考，不是命令。
- 评论优先：只要视频有具体观点、知识点、槽点，或让你有想补充/想吐槽的反应，actions 就应包含 comment。
- 不要为了互动而互动；但如果认真看完了且有话说，就写一条评论，不要只点赞收藏。
- like/favorite 按需，不要默认三连；comment 可以比 like/favorite 更常用。
- 如果 actions 包含 comment，comment_text 必须从你的 opinion 出发，写成一条 20-120 字、具体、自然、像真人发的评论；可以赞同、补充、提问或轻微吐槽，但不要空泛复读标题，不要引战、刷屏或广告。
- 只有热评确实值得回应、且你有具体回复内容时才 reply；否则不要为了回复而回复。
- 只输出 JSON，不要 Markdown。"""
        user_prompt = f"""标题：{title}
作者：{author}
转写文本（可能不完整）：
{transcript[:8000]}

请分析并输出 JSON。"""
        try:
            data, _result = await self.runtime.llm.complete_json(
                base_url=str(settings.get("llm_base_url") or ""),
                api_key=str(settings.get("llm_api_key") or ""),
                model=str(settings.get("llm_model") or ""),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=1200,
                json_mode=True,
            )
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("B站视频分析失败: %s", exc)
            return {"summary": transcript[:300], "topics": []}

    async def _mark_run(self) -> None:
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(BiliAutopilotConfig)
                    .order_by(BiliAutopilotConfig.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is not None:
                row.last_run_at = utcnow()
                await db.commit()

    async def _fail(self, row_id: int, message: str) -> None:
        async with SessionLocal() as db:
            row = await db.get(BiliVideo, row_id)
            if row is not None:
                row.status = "failed"
                row.error = message[:1000]
                await db.commit()
        await self._mark_run()
        self.current = {"phase": "error", "message": message}
        await self._emit("error", message)

    async def _emit(self, phase: str, message: str, **extra: Any) -> None:
        event = {"phase": phase, "message": message, **extra}
        self.current = event
        try:
            self.runtime.broadcast("bili.autopilot", event)
        except Exception:
            pass
