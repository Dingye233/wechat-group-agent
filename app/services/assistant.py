from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    AgentTask,
    AssistantMessage,
    Group,
    GroupConfig,
    Message,
    utcnow,
)
from app.services import assistant_tools, mem0_memory
from app.services.capabilities import (
    catalog_for_scope,
    check_tool_allowed,
    execute_capability,
)
from app.services.agent_engines import resolve_agent_engine
from app.services.llm import LLMError, LLMService, parse_json_object
from app.services.prompting import (
    Prompt,
    build_opencode_operator_prompt,
    build_operator_prompt,
    build_side_chat_prompt,
)
from app.services.memory import (
    DEFAULT_MEMORY_PROMPT_TEMPLATE,
    format_memory_line,
    memory_dict,
    save_memory,
    search_memories,
)
from app.services.settings import get_settings

logger = logging.getLogger(__name__)

MAX_HISTORY_CHARS = 9000
MAX_OBSERVATION_CHARS = 7000
MAX_TASK_SECONDS = 1800
MAX_SAFETY_STEPS = 500
# OpenCode keeps the session transcript on its side.  A system prompt that
# changes every turn would invalidate that whole prefix in the provider cache,
# so the memory snapshot is frozen per session and refreshed on this interval.
OPENCODE_PROMPT_TTL = 900.0

_ACTIVE_SESSIONS: set[str] = set()
_CANCEL_EVENTS: dict[str, asyncio.Event] = {}
# AssistantService is created per HTTP request, so the OpenCode prompt cache
# must live at module level to survive across turns.
_OPENCODE_PROMPT_CACHE: dict[str, tuple[float, str, str]] = {}
_INBOX: dict[str, list[str]] = {}


def is_session_active(session_id: str) -> bool:
    return session_id in _ACTIVE_SESSIONS


def enqueue_session_message(session_id: str, message: str) -> bool:
    """Queue a mid-task message. Returns True when a task is currently running."""
    text = str(message or "").strip()
    if not text:
        return False
    bucket = _INBOX.setdefault(session_id, [])
    bucket.append(text)
    if len(bucket) > 20:
        del bucket[:-20]
    return session_id in _ACTIVE_SESSIONS


def take_session_inbox(session_id: str) -> list[str]:
    return [
        item for item in _INBOX.pop(session_id, []) if str(item or "").strip()
    ]


def cancel_session(session_id: str) -> bool:
    event = _CANCEL_EVENTS.get(session_id)
    if event is None:
        return False
    event.set()
    # Requirements queued before the stop belong to the cancelled task.
    # Anything sent after this point can become a fresh follow-up task.
    _INBOX.pop(session_id, None)
    return True


def cleanup_session(session_id: str) -> None:
    _ACTIVE_SESSIONS.discard(session_id)
    _CANCEL_EVENTS.pop(session_id, None)
    # Do not throw away messages that arrived just before the loop ended.
    # The next chat() for this session drains them as inserted requirements.
    if not _INBOX.get(session_id):
        _INBOX.pop(session_id, None)


def _drain_inbox(session_id: str) -> list[str]:
    return take_session_inbox(session_id)


def _extract_json_string_field(content: str, field: str) -> str:
    """Best-effort extraction of a string field from malformed JSON."""
    marker = f'"{field}"'
    index = content.find(marker)
    if index < 0:
        return ""
    colon = content.find(":", index + len(marker))
    if colon < 0:
        return ""
    index = colon + 1
    while index < len(content) and content[index].isspace():
        index += 1
    if index >= len(content) or content[index] != '"':
        return ""
    start = index
    index += 1
    escaped = False
    while index < len(content):
        char = content[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            raw = content[start : index + 1]
            try:
                return str(json.loads(raw))
            except Exception:
                return ""
        index += 1
    return ""


def _merge_memories(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*primary, *secondary]:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        key = content[:240]
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= max(1, int(limit)):
            break
    return merged


def _dedupe_image_markdown(text: str) -> str:
    seen: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        url = match.group(2).strip()
        if url in seen:
            return ""
        seen.add(url)
        return match.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace, text)


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "\n…（截断）"


def _assistant_message_dict(row: AssistantMessage) -> dict[str, Any]:
    media_path = str(row.media_path or "")
    media_url = ""
    if media_path:
        normalized = media_path.replace("\\", "/")
        marker = "data/media/assistant/"
        if marker in normalized:
            media_url = "/media/assistant/" + normalized.split(marker, 1)[1]
        elif media_path.startswith("/media/"):
            media_url = media_path
    return {
        "id": row.id,
        "session_id": row.session_id,
        "role": row.role,
        "kind": row.kind,
        "content": row.content,
        "media_path": media_path,
        "media_url": media_url,
        "tool_name": row.tool_name,
        "tool_input": row.tool_input or {},
        "tool_output": row.tool_output,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _store(
    session_id: str,
    role: str,
    content: str,
    *,
    tool_name: str = "",
    tool_input: dict[str, Any] | None = None,
    tool_output: str = "",
    kind: str = "text",
    media_path: str = "",
) -> AssistantMessage:
    async with SessionLocal() as db:
        row = AssistantMessage(
            session_id=session_id,
            role=role,
            kind=kind,
            content=content,
            media_path=media_path,
            tool_name=tool_name,
            tool_input=tool_input or {},
            tool_output=tool_output,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def history(session_id: str = "main", limit: int = 80) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(AssistantMessage)
                    .where(AssistantMessage.session_id == session_id)
                    .order_by(AssistantMessage.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [_assistant_message_dict(row) for row in reversed(rows)]


async def clear_history(session_id: str = "main") -> int:
    async with SessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(AssistantMessage).where(
                        AssistantMessage.session_id == session_id
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await db.delete(row)
        await db.commit()
    return len(rows)


async def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(AgentTask)
                    .order_by(AgentTask.updated_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": row.id,
            "title": row.title,
            "detail": row.detail,
            "status": row.status,
            "result": row.result,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


async def create_task(title: str, detail: str = "", status: str = "pending") -> dict[str, Any]:
    title = str(title or "").strip()
    if not title:
        raise ValueError("任务标题不能为空")
    async with SessionLocal() as db:
        row = AgentTask(
            title=title[:240], detail=str(detail or "")[:10000], status=status
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return {
            "id": row.id,
            "title": row.title,
            "detail": row.detail,
            "status": row.status,
            "result": row.result,
        }


async def update_task(
    task_id: int, *, status: str = "", result: str = "", detail: str = ""
) -> dict[str, Any]:
    async with SessionLocal() as db:
        row = await db.get(AgentTask, int(task_id))
        if row is None:
            raise ValueError("任务不存在")
        if status:
            row.status = status[:24]
        if result:
            row.result = str(result)[:20000]
        if detail:
            row.detail = str(detail)[:20000]
        await db.commit()
        await db.refresh(row)
        return {
            "id": row.id,
            "title": row.title,
            "detail": row.detail,
            "status": row.status,
            "result": row.result,
        }


class AssistantService:
    """Operator service.

    The default path delegates decisions to the official OpenCode server
    (``opencode serve``).  The in-process JSON tool loop below is retained as
    an auditable fallback when OpenCode is disabled or unavailable.
    """

    def __init__(self, llm: LLMService, runtime: Any = None) -> None:
        self.llm = llm
        self.runtime = runtime

    async def _tool_result(
        self,
        name: str,
        args: dict[str, Any],
        *,
        session_id: str,
        settings: dict[str, Any],
        account_key: str,
        scope: str = "assistant",
    ) -> Any:
        await check_tool_allowed(name, args, scope)
        if name == "save_memory":
            memory = await save_memory(
                group_id=None,
                account_key=account_key,
                scope="global",
                category=str(args.get("category") or "assistant"),
                title=str(args.get("title") or ""),
                content=str(args.get("content") or ""),
                importance=int(args.get("importance") or 3),
                source="assistant",
            )
            return memory_dict(memory)
        if name == "search_memories":
            return await search_memories(
                query=str(args.get("query") or ""),
                group_id=None,
                account_key=account_key,
                limit=int(args.get("limit") or 12),
            )
        if name == "create_task":
            return await create_task(
                str(args.get("title") or ""),
                str(args.get("detail") or ""),
                str(args.get("status") or "pending"),
            )
        if name == "update_task":
            return await update_task(
                int(args.get("task_id") or 0),
                status=str(args.get("status") or ""),
                result=str(args.get("result") or ""),
                detail=str(args.get("detail") or ""),
            )
        if name == "list_tasks":
            return await list_tasks(int(args.get("limit") or 50))
        if name == "list_groups":
            return await self.runtime.list_group_payloads()
        if name in ("read_group_messages", "send_group_message", "set_group_access", "pin_group", "summarize_group"):
            return await self._admin_group_tool(name, args, settings)
        if name == "send_contact_message":
            contact_id = int(args.get("contact_id") or 0)
            text = str(args.get("text") or "").strip()
            if contact_id <= 0 or not text:
                raise ValueError("contact_id 和 text 不能为空")
            if self.runtime is None:
                raise ValueError("运行时不可用")
            await self.runtime.send_contact_message(
                contact_id, text, is_agent=True
            )
            return {"ok": True, "contact_id": contact_id, "text": text}
        if name == "send_contact_image":
            contact_id = int(args.get("contact_id") or 0)
            path = str(args.get("path") or "").strip()
            if contact_id <= 0 or not path:
                raise ValueError("contact_id 和 path 不能为空")
            if self.runtime is None:
                raise ValueError("运行时不可用")
            await self.runtime.send_contact_image(contact_id, Path(path))
            return {"ok": True, "contact_id": contact_id, "path": path}
        if name == "list_capabilities":
            from app.services.capabilities import list_capabilities as _list_caps

            rows = await _list_caps()
            return [
                {
                    "key": item["key"],
                    "name": item["name"],
                    "type": item["type"],
                    "risk": item["risk"],
                    "assistant": item["enabled_assistant"],
                    "group": item["enabled_group"],
                    "description": (item.get("description") or "")[:120],
                }
                for item in rows
            ]
        if name == "discover_capabilities":
            from app.services.capabilities import (
                list_capabilities as _list_caps,
                sync_capabilities as _sync_caps,
            )

            await _sync_caps(force=True)
            return await _list_caps()
        if name == "web_request":
            return await assistant_tools.web_request(
                str(args.get("method") or "GET"),
                str(args.get("url") or ""),
                args.get("headers") if isinstance(args.get("headers"), dict) else None,
                args.get("json") if "json" in args else None,
                str(args.get("body") or ""),
                float(args.get("timeout") or 30),
            )
        # File / PowerShell / skill / desktop tools are blocking; keep them off
        # the event loop. The capability gate is applied by execute_capability.
        return await execute_capability(
            name, args, scope, account_key=account_key
        )

    async def _admin_group_tool(
        self,
        name: str,
        args: dict[str, Any],
        settings: dict[str, Any],
    ) -> Any:
        """Group tools used by the Operator/OpenCode tool gateway."""
        group_id = int(args.get("group_id") or 0)
        if group_id <= 0:
            raise ValueError("group_id 必须是正整数")
        async with SessionLocal() as db:
            group = await db.get(Group, group_id)
            if group is None:
                raise ValueError("群不存在")
            if self.runtime is not None and self.runtime.account_key:
                if group.account_key and group.account_key != self.runtime.account_key:
                    raise ValueError("该群属于其他微信账号")
        if name == "read_group_messages":
            limit = max(1, min(200, int(args.get("limit") or 30)))
            since_id = int(args.get("since_id") or 0)
            async with SessionLocal() as db:
                stmt = select(Message).where(Message.group_id == group_id)
                if since_id:
                    stmt = stmt.where(Message.id > since_id)
                rows = list(
                    (
                        await db.execute(
                            stmt.order_by(Message.id.desc()).limit(limit)
                        )
                    )
                    .scalars()
                    .all()
                )
            rows.reverse()
            return [
                {
                    "id": row.id,
                    "sender": row.sender_name or row.sender_wxid or "",
                    "content": row.content or "",
                    "kind": row.kind,
                    "is_agent": bool(row.is_from_agent),
                    "created_at": row.created_at.isoformat()
                    if row.created_at
                    else None,
                }
                for row in rows
            ]
        if name == "send_group_message":
            text = str(args.get("text") or "").strip()
            if not text:
                raise ValueError("消息内容不能为空")
            if self.runtime is None:
                raise ValueError("运行时不可用")
            sent = await self.runtime.send_agent_message(
                group_id,
                text=text,
                meta={"source": "opencode"},
            )
            return {"ok": bool(sent), "group_id": group_id}
        if name == "set_group_access":
            enabled = bool(args.get("enabled", args.get("agent_enabled", True)))
            async with SessionLocal() as db:
                config = (
                    await db.execute(
                        select(GroupConfig).where(GroupConfig.group_id == group_id)
                    )
                ).scalar_one_or_none()
                if config is None:
                    config = GroupConfig(group_id=group_id)
                    db.add(config)
                config.agent_enabled = enabled
                await db.commit()
            return {"ok": True, "group_id": group_id, "agent_enabled": enabled}
        if name == "pin_group":
            if self.runtime is None:
                raise ValueError("运行时不可用")
            return await self.runtime.pin_group(group_id)
        if name == "summarize_group":
            from app.services.memory import summarize_group_memories

            return await summarize_group_memories(
                self.llm, group_id, settings
            )
        raise ValueError(f"未知群工具：{name}")

    def _opencode_system_prompt(
        self,
        *,
        settings: dict[str, Any],
        remembers: list[dict[str, Any]],
        capability_payload: dict[str, Any],
        contact_persona: str = "",
        channel: str = "dashboard",
    ) -> str:
        """Prompt for the native OpenCode loop (no JSON action protocol)."""
        memory_template = str(
            settings.get("memory_prompt_template") or DEFAULT_MEMORY_PROMPT_TEMPLATE
        )
        memory_lines = []
        used = 0
        limit = max(200, int(settings.get("memory_context_max_chars") or 1800))
        for item in remembers:
            line = format_memory_line(item, memory_template)
            if used + len(line) > limit:
                break
            memory_lines.append(line)
            used += len(line) + 1

        persona_parts = []
        global_persona = str(settings.get("operator_persona") or "").strip()
        if global_persona:
            persona_parts.append(global_persona)
        if str(contact_persona or "").strip():
            persona_parts.append(
                "当前联系人专属人设（叠加在全局人设之上）：\n"
                + str(contact_persona).strip()
            )
        persona_block = "\n\n".join(persona_parts)

        tools = capability_payload.get("tools") or []
        tool_names = "、".join(str(item.get("name")) for item in tools[:80]) or "无"
        resources: list[str] = []
        for item in capability_payload.get("skills") or []:
            resources.append(
                f"- skill:{item.get('name')} — {item.get('description', '')}"
            )
        for item in capability_payload.get("mcps") or []:
            resources.append(
                f"- mcp:{str(item.get('key', '')).split(':', 1)[-1]} — "
                f"{item.get('description', '')}"
            )
        for item in capability_payload.get("apis") or []:
            resources.append(
                f"- api:{item.get('name')} — {item.get('description', '')}"
            )
        resource_block = "\n".join(resources) or "无"

        channel_text = (
            "你正在微信私聊里回复管理员。最终答复要像真人发微信：自然中文短段落，"
            "不要 Markdown 表格或代码块。工具调用进度由系统自动同步；"
            "不需要再调用发送工具把最终答复重复发一遍。"
            if channel == "wechat"
            else "你在群伴后台的 Agent 对话中，可以使用 Markdown，但保持简洁、可执行。"
        )
        if channel == "wechat":
            channel_text += (
                "\n微信私聊模式下可以用屏幕/鼠标/键盘/PowerShell 做非微信任务，"
                "但不要用它们操作微信窗口；需要发消息只能用 "
                "qunban_send_message(contact_id=..., text=...)，"
                "最终答复仍由系统通过 wechatauto-replica 自动发送。"
            )

        prompt = build_opencode_operator_prompt(
            core=str(settings.get("operator_system_prompt") or "").strip(),
            channel_text=channel_text,
            workspace=str(assistant_tools.workspace_root()),
            admin_text="是" if assistant_tools.is_admin() else "否",
            tool_names=tool_names,
            resource_block=resource_block,
            persona_block=persona_block,
            extra_prompt=str(settings.get("operator_prompt_extra") or "").strip(),
            memory_lines="\n".join(memory_lines),
        )
        return prompt.system

    def _cached_opencode_system_prompt(
        self,
        *,
        session_id: str,
        settings: dict[str, Any],
        remembers: list[dict[str, Any]],
        capability_payload: dict[str, Any],
        contact_persona: str,
        channel: str,
    ) -> str:
        """Freeze the memory snapshot per session so the prefix stays cached.

        OpenCode keeps the transcript on its side, so the system prompt leads
        every model request.  Rebuilding it with fresh memory each turn would
        invalidate the whole transcript after it; refresh the snapshot only
        every ``OPENCODE_PROMPT_TTL`` seconds instead.
        """
        signature = json.dumps(
            {
                "contact_persona": contact_persona,
                "channel": channel,
                "operator_persona": settings.get("operator_persona"),
                "core": settings.get("operator_system_prompt"),
                "extra": settings.get("operator_prompt_extra"),
                "memory_template": settings.get("memory_prompt_template"),
                "memory_max_chars": settings.get("memory_context_max_chars"),
                "tools": [
                    item.get("name")
                    for item in capability_payload.get("tools") or []
                ],
                "skills": [
                    item.get("name")
                    for item in capability_payload.get("skills") or []
                ],
                "mcps": [
                    item.get("key")
                    for item in capability_payload.get("mcps") or []
                ],
                "apis": [
                    item.get("name")
                    for item in capability_payload.get("apis") or []
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        now = time.monotonic()
        cached = _OPENCODE_PROMPT_CACHE.get(session_id)
        if cached and cached[0] > now and cached[1] == signature:
            return cached[2]
        prompt = self._opencode_system_prompt(
            settings=settings,
            remembers=remembers,
            capability_payload=capability_payload,
            contact_persona=contact_persona,
            channel=channel,
        )
        _OPENCODE_PROMPT_CACHE[session_id] = (
            now + OPENCODE_PROMPT_TTL,
            signature,
            prompt,
        )
        if len(_OPENCODE_PROMPT_CACHE) > 64:
            oldest = min(
                _OPENCODE_PROMPT_CACHE,
                key=lambda key: _OPENCODE_PROMPT_CACHE[key][0],
            )
            _OPENCODE_PROMPT_CACHE.pop(oldest, None)
        return prompt


    def _dsh_system_prompt(
        self,
        *,
        session_id: str,
        settings: dict[str, Any],
        remembers: list[dict[str, Any]],
        capability_payload: dict[str, Any],
        contact_persona: str,
        channel: str,
    ) -> str:
        """Qunban-specific context for the native DSH Operator.

        DSH already contributes its own base prompt and native tool schemas, so
        this text only carries what DSH cannot know: WeChat safety rules, the
        Qunban tool names, channel rules, persona and frozen memory.  Keeping it
        short is deliberate: the first turn pays for it, later turns hit the
        prompt cache once the DSH session prefix is warm.
        """
        signature = json.dumps(
            {
                "persona": contact_persona,
                "channel": channel,
                "operator_persona": settings.get("operator_persona"),
                "core": settings.get("operator_system_prompt"),
                "extra": settings.get("operator_prompt_extra"),
                "tools": [
                    item.get("name")
                    for item in capability_payload.get("tools") or []
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        cache_key = f"dsh:{session_id}"
        now = time.monotonic()
        cached = _OPENCODE_PROMPT_CACHE.get(cache_key)
        if cached and cached[0] > now and cached[1] == signature:
            return cached[2]

        memory_template = str(
            settings.get("memory_prompt_template") or DEFAULT_MEMORY_PROMPT_TEMPLATE
        )
        memory_limit = max(200, int(settings.get("memory_context_max_chars") or 1800))
        memory_lines: list[str] = []
        used = 0
        for item in remembers:
            line = format_memory_line(item, memory_template)
            if not line or used + len(line) > memory_limit:
                break
            memory_lines.append(line)
            used += len(line) + 1
        memory_text = "\n".join(memory_lines) or "无"

        persona_parts = []
        global_persona = str(settings.get("operator_persona") or "").strip()
        if global_persona:
            persona_parts.append(global_persona)
        if str(contact_persona or "").strip():
            persona_parts.append(
                "当前联系人专属人设（叠加在全局人设之上）：\n"
                + str(contact_persona).strip()
            )
        persona_text = "\n\n".join(persona_parts) or "无"

        if channel == "wechat":
            channel_text = (
                "你正在微信私聊里回复管理员。最终答复要像真人发微信：自然中文短段落，"
                "不要 Markdown 表格、代码块或超长清单。工具调用进度由系统自动同步；"
                "不需要再用 wechat_send_message 把最终答复重复发一遍，系统会自动发送。"
            )
        else:
            channel_text = (
                "你在群伴后台的 Agent 对话中。可以使用 Markdown，但保持简洁、可执行。"
            )

        studio_root = (
            Path(__file__).resolve().parents[2] / "data" / "dsh" / "studio"
        )

        prompt = f"""你是「群伴 Operator」，由 DeepSeek Harness 原生 tool calling 执行。
你的职责是完成管理员交办的任务，并把真实结果说清楚；不要输出 JSON 动作协议，直接调用工具。

# 微信规则（最高优先级）
- 发送微信消息的唯一入口是 `wechat_send_message`。群聊传 group_id，私聊传 contact_id，二者只能选一个。
- 发送前先用 `wechat_list_groups` / `wechat_list_contacts` 确认目标 ID；不要凭群名猜 ID。
- 严禁用 shell、PowerShell、键盘、鼠标、UIA 或任何脚本直接操作微信窗口或模拟发送。
- 文本由 wechatauto-replica 发送并校验数据库落库；工具返回失败时如实报告，不要假装成功，也不要重复盲发。
- 消息应简洁自然；群聊里不要贴绝对路径、内部目录、代码块或工具名。

# 群伴工具
- 微信：`wechat_status`、`wechat_list_groups`、`wechat_read_group`、`wechat_send_message`、`wechat_list_contacts`
- 记忆：`qunban_memory_search`、`qunban_memory_save`
- B站：`qunban_bili_search`、`qunban_bili_knowledge`
- 接口：`qunban_call_interface`
- 技能/MCP：`qunban_run_skill`、`qunban_run_mcp`
- 命令：`qunban_powershell`（已经过微信关键词拦截）
- 文件/搜索/网页：使用 DSH 原生 `read`、`write`、`edit`、`glob`、`grep`、`webfetch`

# 工作方式
1. 先判断目标与约束，信息不足时先读文件或查群/联系人，不要猜。
2. 每一步只做一个可验证的动作；工具失败先读错误再换方案。
3. 只有拿到真实工具结果才说完成；未完成的部分明确说明。
4. 有副作用的操作先确认目标；能自己判断的默认值直接选，不要反复追问。
5. 完成后用简短中文汇报：做了什么、结果、文件/链接、未完成项。
6. 只有用户明确要求新增/修改 DSH 插件时，才做插件自扩展：
   - 在 `{studio_root}/<slug>` 写 `package.json`（name=`@qunban/<slug>`，type=module）
     和 `lib/index.js`（导出 name、inject=['tools']、apply；用 QUNBAN_DSH_RUNTIME_ROOT
     动态 import defineTool；schema 显式 additionalProperties；output 提供 render）。
   - 用 `qunban_powershell` 跑 `node --check lib/index.js`。
   - 通过 `qunban_plugin_install(path=..., enabled=true)` 安装/更新，再告诉用户插件名和启用状态。
   - 不要安装未校验的第三方代码，不要修改 `@qunban/dsh-native` 本身。

# 通道
{channel_text}

# 工作区
{assistant_tools.workspace_root()}

# 人设
{persona_text}

# 长期记忆（参考，不要机械复述）
{memory_text}
"""

        _OPENCODE_PROMPT_CACHE[cache_key] = (
            now + OPENCODE_PROMPT_TTL,
            signature,
            prompt,
        )
        if len(_OPENCODE_PROMPT_CACHE) > 96:
            oldest = min(
                _OPENCODE_PROMPT_CACHE,
                key=lambda key: _OPENCODE_PROMPT_CACHE[key][0],
            )
            _OPENCODE_PROMPT_CACHE.pop(oldest, None)
        return prompt

    async def _run_opencode(
        self,
        *,
        engine: str = "opencode",
        message: str,
        session_id: str,
        scope: str,
        settings: dict[str, Any],
        remembers: list[dict[str, Any]],
        capability_payload: dict[str, Any],
        persona: str,
        channel: str,
        temperature: float | None,
        cancel_event: asyncio.Event,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        store_assistant: bool,
        emit_final: bool,
        followup_depth: int,
        memory_scope: str,
        memory_metadata: dict[str, Any] | None,
        account_key: str,
    ) -> dict[str, Any]:
        from app.services.dsh_agent import DSHError, get_dsh_agent_runtime
        from app.services.opencode_agent import (
            OpenCodeError,
            get_opencode_runtime,
        )

        if engine == "dsh":
            system_prompt = self._dsh_system_prompt(
                session_id=session_id,
                settings=settings,
                remembers=remembers,
                capability_payload=capability_payload,
                contact_persona=persona,
                channel=channel,
            )
            runtime = get_dsh_agent_runtime()
            start_message = "DeepSeek Harness 已接管，开始执行"
        else:
            system_prompt = self._cached_opencode_system_prompt(
                session_id=session_id,
                settings=settings,
                remembers=remembers,
                capability_payload=capability_payload,
                contact_persona=persona,
                channel=channel,
            )
            runtime = get_opencode_runtime()
            start_message = "官方 OpenCode 已接管，开始执行"
        result: dict[str, Any] = {}
        final = ""
        pending_followup: list[str] = []
        await emit(
            {
                "phase": "start",
                "engine": engine,
                "unlimited": True,
                "message": start_message,
            }
        )
        try:
            result = await runtime.chat(
                message,
                session_id=session_id,
                system_prompt=system_prompt,
                settings=settings,
                temperature=temperature,
                progress=emit,
                cancel_event=cancel_event,
                call_site="dsh.assistant" if engine == "dsh" else "opencode.assistant",
            )
            final = str(result.get("reply") or "").strip()
            trace = list(result.get("trace") or [])
        except (OpenCodeError, DSHError) as exc:
            if engine == "dsh":
                raise
            logger.warning("OpenCode 执行失败：%s", exc)
            final = f"OpenCode 执行失败：{exc}"
            trace = []
        finally:
            cleanup_session(session_id)
            pending_followup = take_session_inbox(session_id)

        if pending_followup and followup_depth < 2:
            followup = await self.chat(
                "\n".join(pending_followup),
                session_id=session_id,
                scope=scope,
                kind="text",
                temperature=temperature,
                persona=persona,
                channel=channel,
                progress=emit,
                store_user=False,
                store_assistant=False,
                emit_final=False,
                followup_depth=followup_depth + 1,
            )
            followup_reply = str(followup.get("reply") or "").strip()
            if followup_reply:
                final = f"{final}\n\n{followup_reply}".strip()
            trace.extend(followup.get("trace") or [])

        # OpenCode events use `input`; Qunban's history/image helpers use `args`.
        for item in trace:
            if "args" not in item:
                item["args"] = item.get("input") or {}
        for item in trace:
            tool = str(item.get("tool") or "tool")
            output = str(item.get("output") or "")
            try:
                await _store(
                    session_id,
                    "tool",
                    output,
                    tool_name=tool,
                    tool_input=item.get("args") or {},
                    tool_output=output,
                )
            except Exception:
                pass

        display_urls: list[str] = []
        image_tools = {"display_image", "screen_capture", "run_mcp", "call_interface"}
        for item in trace:
            if item.get("tool") not in image_tools:
                continue
            match = re.search(
                r"/media/assistant/[^\s\"',}]+", str(item.get("output") or "")
            )
            if match and match.group(0) not in display_urls:
                display_urls.append(match.group(0))
        for match in re.finditer(r"/media/assistant/[^\s\"',}]+", final):
            if match.group(0) not in display_urls:
                display_urls.append(match.group(0))
        for url in display_urls:
            if f"]({url})" in final:
                continue
            markdown = f"![图片]({url})"
            if url in final:
                final = final.replace(url, markdown)
            else:
                final += f"\n\n{markdown}"
        final = _dedupe_image_markdown(final)
        if emit_final:
            await emit({"phase": "final", "message": "任务完成"})
        if store_assistant:
            await _store(session_id, "assistant", final)
            if final:
                memory_meta = {
                    "scope": memory_scope,
                    "source": "opencode",
                    "importance": 3,
                    "category": "conversation",
                    "title": "对话记忆",
                }
                memory_meta.update(memory_metadata or {})
                mem0_memory.schedule_add_turn(
                    settings,
                    account_key=account_key,
                    messages=[
                        {"role": "user", "content": message},
                        {"role": "assistant", "content": final},
                    ],
                    metadata=memory_meta,
                )
        return {
            "reply": final,
            "trace": trace,
            "model": str(settings.get("llm_model") or ""),
            "prompt_tokens": int(result.get("prompt_tokens") or 0),
            "completion_tokens": int(result.get("completion_tokens") or 0),
            "admin": assistant_tools.is_admin(),
            "engine": engine,
        }

    async def quick_chat(
        self,
        message: str,
        *,
        session_id: str,
        persona: str = "",
        task_status: str = "",
        busy: bool = True,
        temperature: float | None = None,
    ) -> str:
        """Reply to casual chat while a long task owns the main session.

        This deliberately does not touch OpenCode or the tool loop: it is a
        separate chat-model call so a running task can never block a simple
        question or status check.
        """
        message = str(message or "").strip()
        if not message:
            return ""
        async with SessionLocal() as db:
            settings = await get_settings(db)
        if not settings.get("llm_api_key"):
            return "我这边还没配好模型，等配置好再跟你聊。"
        rows = await history(session_id, limit=20)
        transcript: list[str] = []
        for row in rows:
            if row.get("role") not in {"user", "assistant"}:
                continue
            content = _clip(str(row.get("content") or ""), 600).strip()
            if not content:
                continue
            speaker = "用户" if row.get("role") == "user" else "我"
            transcript.append(f"{speaker}：{content}")
        recent = "\n".join(transcript[-12:]) or "（无）"
        persona_text = str(
            persona or settings.get("operator_persona") or ""
        ).strip()
        status_text = str(task_status or "后台任务仍在执行").strip()
        prompt = build_side_chat_prompt(
            persona_text=persona_text,
            busy=busy,
            status_text=status_text,
        )
        system_prompt = prompt.system
        user_prompt = (
            f"最近对话：\n{recent}\n\n"
            f"用户刚刚说：{message}\n\n"
            "请只输出要发给用户的回复正文。"
        )
        result = await self.llm.complete(
            base_url=str(settings.get("llm_base_url") or ""),
            api_key=str(settings.get("llm_api_key") or ""),
            model=str(settings.get("llm_model") or ""),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.8 if temperature is None else float(temperature),
            max_tokens=600,
            json_mode=False,
            call_site="assistant.quick_chat",
            cache_prefix_chars=prompt.cache_prefix_chars,
        )
        reply = str(result.get("content") or "").strip()
        if not reply:
            reply = "我看到了。后台任务还在跑，等有结果我再详细跟你说。"
        await _store(session_id, "user", message)
        await _store(session_id, "assistant", reply)
        return reply

    def _system_prompt(
        self,
        *,
        settings: dict[str, Any],
        remembers: list[dict[str, Any]],
        is_admin: bool,
        capability_payload: dict[str, Any],
        contact_persona: str = "",
        max_steps: int = 0,
        unlimited: bool = True,
        channel: str = "dashboard",
    ) -> Prompt:
        workspace = str(assistant_tools.workspace_root())
        catalog = json.dumps(capability_payload.get("tools") or [], ensure_ascii=False)
        memory_template = str(
            settings.get("memory_prompt_template")
            or DEFAULT_MEMORY_PROMPT_TEMPLATE
        )
        memory_max_chars = max(
            200, int(settings.get("memory_context_max_chars") or 1800)
        )
        memory_rows: list[str] = []
        memory_used = 0
        for item in remembers:
            line = format_memory_line(item, memory_template)
            if memory_used + len(line) > memory_max_chars:
                break
            memory_rows.append(line)
            memory_used += len(line) + 1
        memory_lines = "\n".join(memory_rows) or "无"

        resources = []
        for item in capability_payload.get("skills") or []:
            resources.append(f"- skill:{item['name']} — {item.get('description', '')}")
        for item in capability_payload.get("mcps") or []:
            resources.append(
                f"- mcp:{item['key'].split(':', 1)[-1]} — {item.get('description', '')}"
            )
        for item in capability_payload.get("apis") or []:
            resources.append(f"- api:{item['name']} — {item.get('description', '')}")
        resource_lines = "\n".join(resources) or "无（用户尚未在能力列表启用）"

        admin_text = (
            "是"
            if is_admin
            else "否（需要管理员权限时会触发 UAC；正式使用建议用 start-admin.ps1 启动服务）"
        )

        persona_parts: list[str] = []
        global_persona = str(settings.get("operator_persona") or "").strip()
        if global_persona:
            persona_parts.append(global_persona)
        contact_persona = str(contact_persona or "").strip()
        if contact_persona:
            persona_parts.append(
                "当前联系人专属人设（叠加在全局人设之上）：\n" + contact_persona
            )
        persona_block = "\n\n".join(persona_parts)
        if persona_block:
            persona_block += (
                "\n（人设只影响语气和称呼；当它与 Operator 的任务执行、"
                "工具调用、结果汇报冲突时，以 Operator 职责为准。"
                "执行任务时可以多说几句，把工具和结果讲清楚。）"
            )

        if unlimited:
            limit_text = (
                f"不限制；系统仍保留 {MAX_SAFETY_STEPS} 步 / "
                f"{MAX_TASK_SECONDS // 60} 分钟的安全上限，防止死循环"
            )
        else:
            limit_text = f"最多 {max_steps} 步"

        if channel == "wechat":
            channel_text = (
                "你正在微信私聊里回复管理员。最终答复要像真人发微信："
                "自然的中文短段落，不要 Markdown 表格、代码块或超长清单。"
                "工具调用进度会由系统自动同步给管理员；"
                "任务执行中继续发消息会插入当前任务，发「停止」或「取消」可以打断。"
                "不需要调用 send_contact_message 把 final 再发一遍，系统会自动发送。"
            )
        else:
            channel_text = (
                "你在群伴后台的 Agent 对话中。可以使用 Markdown，"
                "但保持简洁、可执行，优先说结果和下一步。"
            )

        return build_operator_prompt(
            core=str(settings.get("operator_system_prompt") or "").strip(),
            channel_text=channel_text,
            workspace=workspace,
            admin_text=admin_text,
            persona_block=persona_block,
            extra_prompt=str(settings.get("operator_prompt_extra") or "").strip(),
            tool_catalog=catalog,
            resource_lines=resource_lines,
            limit_text=limit_text,
            memory_lines=memory_lines,
        )

    async def _repair_json_output(
        self,
        content: str,
        *,
        settings: dict[str, Any],
        system_prompt: str,
        temperature: float | None,
        max_tokens: int,
        cache_prefix_chars: int = 0,
    ) -> dict[str, Any] | None:
        """Ask the model once to turn a malformed reply into valid JSON."""
        repair_prompt = f"""你上一条回复不是合法 JSON。原始内容如下：

{_clip(content, 4000)}

请只输出一个合法 JSON 对象，不要 Markdown，不要解释：
{{"thought":"一句中文进度/判断","action":"工具名","args":{{...}}}}
或
{{"thought":"一句中文总结","final":"给用户的最终答复"}}"""
        try:
            result = await self.llm.complete(
                base_url=str(settings.get("llm_base_url") or ""),
                api_key=str(settings.get("llm_api_key") or ""),
                model=str(settings.get("llm_model") or ""),
                system_prompt=system_prompt,
                user_prompt=repair_prompt,
                temperature=0.0,
                max_tokens=max_tokens,
                json_mode=True,
                call_site="assistant.repair",
                cache_prefix_chars=cache_prefix_chars,
            )
            return parse_json_object(result.get("content") or "")
        except Exception:
            return None

    async def chat(
        self,
        message: str,
        *,
        session_id: str = "main",
        scope: str = "assistant",
        kind: str = "text",
        media_path: str = "",
        temperature: float | None = None,
        persona: str = "",
        channel: str = "dashboard",
        progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        memory_scope: str = "operator",
        memory_metadata: dict[str, Any] | None = None,
        store_user: bool = True,
        store_assistant: bool = True,
        emit_final: bool = True,
        followup_depth: int = 0,
    ) -> dict[str, Any]:
        message = str(message or "").strip()
        if not message:
            raise ValueError("消息不能为空")

        # A task is already running for this session: queue the message instead
        # of cancelling the task. The running loop drains it as an inserted
        # requirement, similar to opencode's session prompt queue.
        if session_id in _ACTIVE_SESSIONS:
            enqueue_session_message(session_id, message)
            await _store(
                session_id,
                "user",
                message,
                kind=kind,
                media_path=media_path,
            )
            return {
                "queued": True,
                "reply": "",
                "trace": [],
                "model": "",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "admin": assistant_tools.is_admin(),
            }

        async with SessionLocal() as db:
            settings = await get_settings(db)
        if not settings.get("llm_api_key"):
            raise RuntimeError("请先在设置里配置 API Key，Operator 才能规划工具调用")
        account_key = str(
            (self.runtime.account or {}).get("wxid") if self.runtime else ""
        )
        memory_limit = max(
            1, min(30, int(settings.get("memory_context_items") or 8))
        )
        remembers = await search_memories(
            query=message,
            group_id=None,
            account_key=account_key,
            limit=memory_limit,
        )
        if settings.get("memory_always_include", True):
            # Always carry the most important long-term memories, even when
            # the current message has no keyword overlap with them.
            always = await search_memories(
                query="",
                group_id=None,
                account_key=account_key,
                limit=memory_limit,
            )
            remembers = _merge_memories(remembers, always, memory_limit)
        try:
            mem0_items = await mem0_memory.search(
                settings,
                account_key=account_key,
                query=message,
                limit=memory_limit,
                timeout=float(
                    settings.get("memory_search_timeout_seconds") or 1.5
                ),
            )
            if mem0_items:
                remembers = _merge_memories(
                    remembers, mem0_items, memory_limit
                )
        except Exception as exc:
            logger.debug("Mem0 上下文检索跳过：%s", exc)
        rows = await history(session_id, limit=30)
        transcript = []
        used = 0
        for row in reversed(rows):
            line = f"{row['role'].upper()}：{_clip(row['content'], 1200)}"
            if used + len(line) > MAX_HISTORY_CHARS:
                break
            transcript.append(line)
            used += len(line) + 1
        transcript.reverse()

        configured_steps = int(settings.get("assistant_max_steps") or 0)
        unlimited = configured_steps <= 0
        max_steps = (
            MAX_SAFETY_STEPS
            if unlimited
            else max(1, min(MAX_SAFETY_STEPS, configured_steps))
        )
        max_tokens = max(128, min(3000, int(settings.get("assistant_max_tokens") or 900)))
        limit_text = "不限制" if unlimited else f"最多 {max_steps} 步"

        async def emit(event: dict[str, Any]) -> None:
            if progress is None:
                return
            try:
                await progress(event)
            except Exception:
                pass

        if not settings.get("opencode_enabled", True):
            await emit(
                {
                    "phase": "start",
                    "max_steps": max_steps,
                    "unlimited": unlimited,
                    "message": (
                        "开始处理，步数不限（安全上限 "
                        f"{MAX_SAFETY_STEPS} 步 / {MAX_TASK_SECONDS // 60} 分钟）"
                        if unlimited
                        else f"开始处理，最多 {max_steps} 步"
                    ),
                }
            )
        capability_payload = await catalog_for_scope(scope)
        prompt = self._system_prompt(
            settings=settings,
            remembers=remembers,
            is_admin=assistant_tools.is_admin(),
            capability_payload=capability_payload,
            contact_persona=persona,
            max_steps=max_steps,
            unlimited=unlimited,
            channel=channel,
        )
        system_prompt = prompt.system
        observations: list[str] = []
        trace: list[dict[str, Any]] = []
        insertions: list[str] = _drain_inbox(session_id)
        if kind == "image":
            message = (
                "【这是一条图片消息，OCR 文本已经附在下面。"
                "请直接根据图片/OCR 回答；除非用户明确要求操作文件、屏幕或外部系统，"
                "否则不要调用 read_file、search_files、read_skill，也不要展开无关代码。】\n"
                + message
            )
        if store_user:
            await _store(
                session_id,
                "user",
                message,
                kind=kind,
                media_path=media_path,
            )

        _ACTIVE_SESSIONS.add(session_id)
        cancel_event = asyncio.Event()
        _CANCEL_EVENTS[session_id] = cancel_event
        engine = resolve_agent_engine(settings)
        if engine in {"opencode", "dsh"}:
            try:
                return await self._run_opencode(
                    engine=engine,
                    message=message,
                    session_id=session_id,
                    scope=scope,
                    settings=settings,
                    remembers=remembers,
                    capability_payload=capability_payload,
                    persona=persona,
                    channel=channel,
                    temperature=temperature,
                    cancel_event=cancel_event,
                    emit=emit,
                    store_assistant=store_assistant,
                    emit_final=emit_final,
                    followup_depth=followup_depth,
                    memory_scope=memory_scope,
                    memory_metadata=memory_metadata,
                    account_key=account_key,
                )
            except Exception as exc:
                if engine != "dsh" or not settings.get("opencode_enabled", True):
                    raise
                logger.warning("DeepSeek Harness 执行失败，回退 OpenCode：%s", exc)
                await emit(
                    {
                        "phase": "engine",
                        "message": "DeepSeek Harness 不可用，已回退 OpenCode",
                    }
                )
                return await self._run_opencode(
                    engine="opencode",
                    message=message,
                    session_id=session_id,
                    scope=scope,
                    settings=settings,
                    remembers=remembers,
                    capability_payload=capability_payload,
                    persona=persona,
                    channel=channel,
                    temperature=temperature,
                    cancel_event=cancel_event,
                    emit=emit,
                    store_assistant=store_assistant,
                    emit_final=emit_final,
                    followup_depth=followup_depth,
                    memory_scope=memory_scope,
                    memory_metadata=memory_metadata,
                    account_key=account_key,
                )
        started_at = time.monotonic()
        final = ""
        cancelled = False
        timed_out = False
        failure_counts: dict[str, int] = {}
        repeat_counts: dict[str, int] = {}
        last_outputs: dict[str, str] = {}

        def signature(action_name: str, action_args: dict[str, Any]) -> str:
            try:
                raw = json.dumps(
                    action_args,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            except Exception:
                raw = str(action_args)
            return f"{action_name}|{raw[:1500]}"

        async def take_insertions(step: int) -> None:
            new_items = _drain_inbox(session_id)
            if not new_items:
                return
            insertions.extend(new_items)
            await emit(
                {
                    "phase": "queued",
                    "step": step,
                    "max_steps": max_steps,
                    "unlimited": unlimited,
                    "message": "收到新要求，已插入当前任务",
                }
            )

        pending_followup: list[str] = []
        try:
            for step in range(1, max_steps + 1):
                if cancel_event.is_set():
                    cancelled = True
                    final = "已按你的要求停止当前任务。"
                    break
                if time.monotonic() - started_at > MAX_TASK_SECONDS:
                    timed_out = True
                    final = (
                        f"任务已运行超过 {MAX_TASK_SECONDS // 60} 分钟，我先停下来，"
                        "避免无限循环。你可以发「继续」让我接着做。"
                    )
                    break
                await take_insertions(step)

                observation_text = "\n".join(observations[-8:]) or "无"
                if len(observation_text) > 12000:
                    observation_text = (
                        "…（较早的工具结果已省略）\n" + observation_text[-12000:]
                    )
                inserted_text = "\n".join(f"- {item}" for item in insertions) or "无"
                user_prompt = f"""最近对话：
{chr(10).join(transcript) or '无'}

用户最新消息：
{message}

用户中途插入的新要求（必须一起考虑，不要忽略）：
{inserted_text}

当前进度：第 {step} 步 / {limit_text}

已执行工具结果（按执行顺序，较早的已省略）：
{observation_text}

请决定下一步，输出 JSON。如果目标已经完成，输出 final。"""
                await emit(
                    {
                        "phase": "thinking",
                        "step": step,
                        "max_steps": max_steps,
                        "unlimited": unlimited,
                    }
                )
                try:
                    result = await self.llm.complete(
                        base_url=str(settings.get("llm_base_url") or ""),
                        api_key=str(settings.get("llm_api_key") or ""),
                        model=str(settings.get("llm_model") or ""),
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        temperature=temperature
                        if temperature is not None
                        else 0.25,
                        max_tokens=max_tokens,
                        json_mode=True,
                        call_site="assistant.operator",
                        cache_prefix_chars=prompt.cache_prefix_chars,
                    )
                except LLMError as exc:
                    raise RuntimeError(str(exc)) from exc
                content = result.get("content") or ""
                parsed = parse_json_object(content)
                if not isinstance(parsed, dict):
                    late = _drain_inbox(session_id)
                    if late:
                        insertions.extend(late)
                        await emit(
                            {
                                "phase": "queued",
                                "step": step,
                                "max_steps": max_steps,
                                "unlimited": unlimited,
                                "message": "收到新要求，已插入当前任务",
                            }
                        )
                        continue
                    recovered = _extract_json_string_field(content, "final")
                    if recovered:
                        final = recovered
                        break
                    repaired = await self._repair_json_output(
                        content,
                        settings=settings,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        cache_prefix_chars=prompt.cache_prefix_chars,
                    )
                    if not isinstance(repaired, dict):
                        # Interception removed: after one repair attempt, keep
                        # the model's actual text instead of showing a generic
                        # "format error" message.
                        final = content.strip()
                        break
                    parsed = repaired
                thought = str(parsed.get("thought") or "").strip()
                final_text = parsed.get("final")
                action = str(parsed.get("action") or "").strip()
                args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
                if final_text is not None and not action:
                    late = _drain_inbox(session_id)
                    if late:
                        insertions.extend(late)
                        await emit(
                            {
                                "phase": "queued",
                                "step": step,
                                "max_steps": max_steps,
                                "unlimited": unlimited,
                                "message": "收到新要求，已插入当前任务",
                            }
                        )
                        continue
                    final = str(final_text).strip()
                    break
                if not action:
                    late = _drain_inbox(session_id)
                    if late:
                        insertions.extend(late)
                        await emit(
                            {
                                "phase": "queued",
                                "step": step,
                                "max_steps": max_steps,
                                "unlimited": unlimited,
                                "message": "收到新要求，已插入当前任务",
                            }
                        )
                        continue
                    final = str(final_text or thought or content).strip()
                    break
                started = time.time()
                await emit(
                    {
                        "phase": "tool",
                        "step": step,
                        "max_steps": max_steps,
                        "unlimited": unlimited,
                        "tool": action,
                        "args": args,
                        "thought": thought,
                    }
                )
                try:
                    output = await self._tool_result(
                        action,
                        args,
                        session_id=session_id,
                        settings=settings,
                        account_key=account_key,
                        scope=scope,
                    )
                    if isinstance(output, (dict, list)):
                        output_text = json.dumps(output, ensure_ascii=False, default=str)
                    else:
                        output_text = str(output)
                    await _store(
                        session_id,
                        "tool",
                        output_text[:MAX_OBSERVATION_CHARS],
                        tool_name=action,
                        tool_input=args,
                        tool_output=output_text[:MAX_OBSERVATION_CHARS],
                    )
                except Exception as exc:
                    output_text = f"ERROR: {exc}"
                    await _store(
                        session_id,
                        "tool",
                        output_text,
                        tool_name=action,
                        tool_input=args,
                        tool_output=output_text,
                    )
                failed = output_text.startswith("ERROR")
                trace.append(
                    {
                        "tool": action,
                        "args": args,
                        "output": _clip(output_text, 1200),
                        "seconds": round(time.time() - started, 2),
                    }
                )
                if len(trace) > 200:
                    trace = trace[-200:]
                observations.append(
                    f"<{action}> {_clip(output_text, MAX_OBSERVATION_CHARS)}"
                )
                if len(observations) > 12:
                    observations = observations[-12:]
                sig = signature(action, args)
                if failed:
                    failure_counts[sig] = failure_counts.get(sig, 0) + 1
                    repeat_counts.pop(sig, None)
                else:
                    failure_counts.pop(sig, None)
                    snapshot = _clip(output_text, 1000)
                    if last_outputs.get(sig) == snapshot:
                        repeat_counts[sig] = repeat_counts.get(sig, 0) + 1
                    else:
                        repeat_counts[sig] = 1
                    last_outputs[sig] = snapshot
                await emit(
                    {
                        "phase": "tool_result",
                        "step": step,
                        "max_steps": max_steps,
                        "unlimited": unlimited,
                        "tool": action,
                        "ok": not failed,
                        "message": _clip(output_text, 220),
                    }
                )
                if failed and failure_counts[sig] >= 3:
                    final = (
                        f"同一个工具用相同参数连续失败 {failure_counts[sig]} 次，"
                        "我先自行停止，避免继续空转。最后错误：\n"
                        + _clip(output_text, 800)
                    )
                    break
                if not failed and repeat_counts[sig] >= 5:
                    final = (
                        "同一个工具用相同参数连续 5 次返回相同结果，"
                        "我已经自行停止，避免无限重复。最后结果：\n"
                        + _clip(output_text, 800)
                    )
                    break
        finally:
            cleanup_session(session_id)
            pending_followup = take_session_inbox(session_id)
        if not final:
            if cancelled:
                final = "已按你的要求停止当前任务。"
            elif timed_out:
                final = (
                    f"任务已运行超过 {MAX_TASK_SECONDS // 60} 分钟，我先停下来。"
                    "你可以发「继续」让我接着做。"
                )
            elif observations:
                final = (
                    f"已达到 {max_steps} 步安全上限，下面是最后一步的真实结果；"
                    "如果要继续，我可以接着做：\n\n"
                    + _clip(observations[-1], 2000)
                )
            else:
                final = "模型没有返回最终答复，请重试或把任务拆小一点。"
        if pending_followup and followup_depth < 2:
            # A message may arrive in the tiny window between the loop's final
            # drain and cleanup. Do not lose it: process it as a follow-up in
            # the same request, with a small depth cap.
            followup = await self.chat(
                "\n".join(pending_followup),
                session_id=session_id,
                scope=scope,
                kind="text",
                temperature=temperature,
                persona=persona,
                channel=channel,
                progress=progress,
                store_user=False,
                store_assistant=False,
                emit_final=False,
                followup_depth=followup_depth + 1,
            )
            followup_reply = str(followup.get("reply") or "").strip()
            if followup_reply:
                final = f"{final}\n\n{followup_reply}".strip()
            trace.extend(followup.get("trace") or [])
        display_urls: list[str] = []
        image_tools = {"display_image", "screen_capture", "run_mcp", "call_interface"}
        for item in trace:
            if item.get("tool") not in image_tools:
                continue
            match = re.search(
                r"/media/assistant/[^\s\"',}]+", str(item.get("output") or "")
            )
            if match and match.group(0) not in display_urls:
                display_urls.append(match.group(0))
        for match in re.finditer(r"/media/assistant/[^\s\"',}]+", final):
            if match.group(0) not in display_urls:
                display_urls.append(match.group(0))
        for url in display_urls:
            if f"]({url})" in final:
                continue
            markdown = f"![图片]({url})"
            if url in final:
                final = final.replace(url, markdown)
            else:
                final += f"\n\n{markdown}"
        final = _dedupe_image_markdown(final)
        if emit_final:
            await emit({"phase": "final", "message": "任务完成"})
        if store_assistant:
            await _store(session_id, "assistant", final)
        if store_assistant and final:
            memory_meta = {
                "scope": memory_scope,
                "source": "assistant",
                "importance": 3,
                "category": "conversation",
                "title": "对话记忆",
            }
            memory_meta.update(memory_metadata or {})
            memory_user_text = "\n".join(
                [message, *insertions, *pending_followup]
            ).strip()
            mem0_memory.schedule_add_turn(
                settings,
                account_key=account_key,
                messages=[
                    {"role": "user", "content": memory_user_text},
                    {"role": "assistant", "content": final},
                ],
                metadata=memory_meta,
            )
        return {
            "reply": final,
            "trace": trace,
            "model": result.get("model") if "result" in locals() else "",
            "prompt_tokens": result.get("prompt_tokens", 0) if "result" in locals() else 0,
            "completion_tokens": result.get("completion_tokens", 0) if "result" in locals() else 0,
            "admin": assistant_tools.is_admin(),
        }
