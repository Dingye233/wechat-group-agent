from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AgentMemory, Group, GroupConfig, Message, utcnow
from app.services.llm import LLMError, LLMService, parse_json_object

DEFAULT_MEMORY_PROMPT_TEMPLATE = "- [{importance}★ {category}] {title}：{content}"


def format_memory_line(item: Any, template: str = "") -> str:
    """Render one memory with the user-editable injection template.

    Supported placeholders: {importance}, {category}, {title}, {content},
    {scope}, {group_id}, {account_key}.
    """
    if isinstance(item, dict):
        def value(name: str, default: Any = "") -> Any:
            return item.get(name, default)
    else:
        def value(name: str, default: Any = "") -> Any:
            return getattr(item, name, default)

    values = {
        "importance": value("importance", 1),
        "category": value("category", "fact"),
        "title": value("title", ""),
        "content": value("content", ""),
        "scope": value("scope", "global"),
        "group_id": value("group_id", ""),
        "account_key": value("account_key", ""),
    }
    text = str(template or DEFAULT_MEMORY_PROMPT_TEMPLATE)
    try:
        line = text.format_map(values)
    except Exception:
        line = (
            f"[{values['importance']}★ {values['category']}] "
            f"{values['title']}：{values['content']}"
        )
    return str(line).strip()


MEMORY_SYSTEM = """你是长期记忆压缩器。把微信群聊压缩成少量、可复用、不会过期的记忆。
要求：
1. 只输出 JSON：{"memories":[{"importance":1-5,"category":"fact|preference|relationship|task|event|style","title":"短标题","content":"不超过80字"}]}
2. 最多保留 5 条；重要且长期有效的放 4-5 星，次要/短期信息放 1-3 星。
3. 不要记录闲聊寒暄、重复内容、表情包、广告、无法确认的信息。
4. 优先记录：人物关系、偏好、长期项目、明确承诺、固定安排、称呼/禁忌、重要结论。
5. 每条 content 必须能脱离上下文独立理解，写清楚是谁/哪个群/什么事。
6. 不要编造；没有值得长期记忆的内容就输出 {"memories":[]}。"""


def estimate_tokens(text: str) -> int:
    """Rough token estimate for Chinese + English without a tokenizer."""
    value = str(text or "")
    if not value:
        return 0
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    other = max(0, len(value) - chinese)
    return max(1, chinese + other // 4)


def memory_dict(memory: AgentMemory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "group_id": memory.group_id,
        "scope": memory.scope,
        "category": memory.category,
        "title": memory.title,
        "content": memory.content,
        "importance": int(memory.importance or 1),
        "source": memory.source,
        "token_count": int(memory.token_count or 0),
        "active": bool(memory.active),
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
        "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
        "last_used_at": memory.last_used_at.isoformat() if memory.last_used_at else None,
        "use_count": int(memory.use_count or 0),
    }


async def save_memory(
    *,
    group_id: int | None,
    account_key: str = "",
    scope: str = "group",
    category: str = "general",
    title: str = "",
    content: str = "",
    importance: int = 3,
    source: str = "assistant",
) -> AgentMemory:
    content = str(content or "").strip()
    if not content:
        raise ValueError("记忆内容不能为空")
    title = str(title or content[:40]).strip()[:160]
    importance = max(1, min(5, int(importance or 3)))
    async with SessionLocal() as db:
        existing = (
            await db.execute(
                select(AgentMemory).where(
                    AgentMemory.group_id == group_id,
                    AgentMemory.content == content,
                    AgentMemory.active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.importance = max(int(existing.importance or 1), importance)
            existing.title = title or existing.title
            existing.updated_at = utcnow()
            await db.commit()
            await db.refresh(existing)
            return existing
        memory = AgentMemory(
            group_id=group_id,
            account_key=account_key,
            scope=scope if group_id is None else "group",
            category=str(category or "general")[:32],
            title=title,
            content=content[:2000],
            importance=importance,
            source=source,
            token_count=estimate_tokens(content),
        )
        db.add(memory)
        await db.commit()
        await db.refresh(memory)
        return memory


async def search_memories(
    *,
    query: str = "",
    group_id: int | None = None,
    account_key: str = "",
    limit: int = 20,
    include_global: bool = True,
) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        statement = select(AgentMemory).where(AgentMemory.active.is_(True))
        if account_key:
            statement = statement.where(
                (AgentMemory.account_key == account_key)
                | (AgentMemory.account_key == "")
            )
        if group_id is not None:
            if include_global:
                statement = statement.where(
                    (AgentMemory.group_id == group_id)
                    | (AgentMemory.scope == "global")
                )
            else:
                statement = statement.where(AgentMemory.group_id == group_id)
        memories = list(
            (
                await db.execute(
                    statement.order_by(
                        AgentMemory.importance.desc(), AgentMemory.updated_at.desc()
                    ).limit(200)
                )
            )
            .scalars()
            .all()
        )
    q = str(query or "").strip().lower()
    scored = []
    for memory in memories:
        haystack = f"{memory.title} {memory.content}".lower()
        score = float(memory.importance or 1) * 10
        if q:
            terms = [term for term in re.split(r"\s+", q) if term]
            score += sum(5 for term in terms if term in haystack)
        score += min(5.0, max(0.0, 1.0 - (time.time() - (memory.updated_at.timestamp() if memory.updated_at else time.time())) / 86400 / 30))
        scored.append((score, memory))
    scored.sort(key=lambda item: (item[0], item[1].updated_at or datetime.min), reverse=True)
    return [memory_dict(item[1]) for item in scored[:limit]]


async def build_memory_context(
    db,
    *,
    group_id: int,
    account_key: str,
    settings: dict[str, Any],
    query: str = "",
) -> tuple[str, list[int]]:
    limit = max(1, int(settings.get("memory_context_items") or 8))
    max_chars = max(200, int(settings.get("memory_context_max_chars") or 1800))
    statement = select(AgentMemory).where(
        AgentMemory.active.is_(True),
        (AgentMemory.group_id == group_id) | (AgentMemory.scope == "global"),
    )
    if account_key:
        statement = statement.where(
            (AgentMemory.account_key == account_key) | (AgentMemory.account_key == "")
        )
    rows = list(
        (
            await db.execute(
                statement.order_by(
                    AgentMemory.importance.desc(), AgentMemory.updated_at.desc()
                ).limit(60)
            )
        )
        .scalars()
        .all()
    )
    q = str(query or "").lower()
    scored = []
    for item in rows:
        haystack = f"{item.title} {item.content}".lower()
        score = float(item.importance or 1) * 10
        if q:
            score += sum(3 for term in re.split(r"\s+", q) if term and term in haystack)
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    lines = []
    used_ids = []
    used_chars = 0
    template = str(
        settings.get("memory_prompt_template") or DEFAULT_MEMORY_PROMPT_TEMPLATE
    )
    for _score, item in scored[:limit]:
        line = format_memory_line(item, template)
        if used_chars + len(line) > max_chars:
            break
        lines.append(line)
        used_ids.append(item.id)
        used_chars += len(line) + 1
    if used_ids:
        for item in rows:
            if item.id in used_ids:
                item.last_used_at = utcnow()
                item.use_count = int(item.use_count or 0) + 1
        await db.commit()
    return "\n".join(lines), used_ids


async def summarize_group_memories(
    llm: LLMService,
    group_id: int,
    settings: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    if not settings.get("memory_enabled", True):
        return {"skipped": "memory_disabled"}
    if not settings.get("llm_api_key"):
        return {"skipped": "no_api_key"}
    max_input = max(1000, int(settings.get("memory_summary_max_input_chars") or 6000))
    max_tokens = max(100, int(settings.get("memory_summary_max_tokens") or 500))
    max_items = max(1, min(12, int(settings.get("memory_summary_max_items") or 5)))
    async with SessionLocal() as db:
        group = await db.get(Group, group_id)
        config = await db.get(GroupConfig, group_id)
        if group is None or config is None or not config.read_enabled:
            return {"skipped": "not_readable"}
        last_id = int(config.last_memory_message_id or 0)
        rows = list(
            (
                await db.execute(
                    select(Message)
                    .where(
                        Message.group_id == group_id,
                        Message.id > last_id,
                        Message.kind.in_(("text", "app")),
                        Message.is_from_agent.is_(False),
                    )
                    .order_by(Message.id.asc())
                    .limit(80)
                )
            )
            .scalars()
            .all()
        )
        if len(rows) < (1 if force else 4):
            return {"skipped": "not_enough_messages", "messages": len(rows)}
        # Build oldest -> newest, slice to the input budget, and keep the last
        # id only after a successful summary.
        lines = []
        used = 0
        selected = []
        for row in reversed(rows):
            text = str(row.content or "").strip()
            if not text or row.kind not in ("text", "app"):
                continue
            line = f"{row.sender_name or '成员'}：{text[:500]}"
            if used + len(line) > max_input:
                break
            lines.append(line)
            selected.append(row)
            used += len(line) + 1
        lines.reverse()
        selected.reverse()
        if len(lines) < 2:
            return {"skipped": "not_enough_text"}
        existing = list(
            (
                await db.execute(
                    select(AgentMemory).where(
                        AgentMemory.group_id == group_id,
                        AgentMemory.active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_text = "\n".join(
            f"- {item.content}" for item in existing[:30]
        )
        group_name = group.name or "群聊"
        account_key = group.account_key or ""
    user_prompt = f"""群名：{group_name}
已有长期记忆（避免重复）：
{existing_text or '无'}

需要压缩的聊天记录（从早到晚）：
{chr(10).join(lines)}

请输出 JSON。最多 {max_items} 条。"""
    try:
        data, result = await llm.complete_json(
            base_url=str(settings.get("llm_base_url") or ""),
            api_key=str(settings.get("llm_api_key") or ""),
            model=str(settings.get("llm_model") or ""),
            system_prompt=MEMORY_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=max_tokens,
            json_mode=True,
        )
    except LLMError as exc:
        return {"error": str(exc)}
    raw_items = data.get("memories") if isinstance(data, dict) else []
    saved = []
    if isinstance(raw_items, list):
        for raw in raw_items[:max_items]:
            if not isinstance(raw, dict):
                continue
            content = str(raw.get("content") or "").strip()
            if not content:
                continue
            title = str(raw.get("title") or content[:40]).strip()
            category = str(raw.get("category") or "general")[:32]
            try:
                importance = max(1, min(5, int(raw.get("importance") or 3)))
            except (TypeError, ValueError):
                importance = 3
            content = content[:300]
            duplicate = next(
                (
                    item
                    for item in existing
                    if item.content == content
                    or (len(content) > 8 and content in item.content)
                ),
                None,
            )
            if duplicate is not None:
                continue
            try:
                memory = await save_memory(
                    group_id=group_id,
                    account_key=account_key,
                    scope="group",
                    category=category,
                    title=title,
                    content=content,
                    importance=importance,
                    source="summary",
                )
                saved.append(memory_dict(memory))
            except Exception:
                continue
    mem0_added = False
    if selected:
        try:
            from app.services import mem0_memory

            if mem0_memory.enabled(settings):
                mem_messages = [
                    {
                        "role": "user",
                        "content": (
                            f"[{row.sender_name or '群成员'}] "
                            f"{str(row.content or '').strip()[:800]}"
                        ),
                    }
                    for row in selected
                    if str(row.content or "").strip()
                ]
                mem0_memory.schedule_add_turn(
                    settings,
                    account_key=account_key,
                    messages=mem_messages,
                    metadata={
                        "scope": "group",
                        "group_id": str(group_id),
                        "group_name": group_name,
                        "source": "summary_batch",
                        "importance": 3,
                        "category": "conversation",
                        "title": f"{group_name} 群聊记忆",
                    },
                )
                mem0_added = True
        except Exception:
            mem0_added = False
    if selected:
        last_selected_id = selected[-1].id
        async with SessionLocal() as db:
            config = await db.get(GroupConfig, group_id)
            if config is not None:
                config.last_memory_message_id = last_selected_id
                await db.commit()
    return {
        "saved": saved,
        "count": len(saved),
        "input_messages": len(selected),
        "mem0_added": mem0_added,
        "model": result.get("model"),
        "prompt_tokens": result.get("prompt_tokens", 0),
        "completion_tokens": result.get("completion_tokens", 0),
    }


_LAST_SQLITE_FORGET = 0.0


async def forget_sqlite_memories(
    *,
    days: int = 180,
    account_key: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Deactivate old SQLite memories, keeping 5-star memories."""
    cutoff = utcnow() - timedelta(days=max(1, int(days)))
    async with SessionLocal() as db:
        statement = select(AgentMemory).where(
            AgentMemory.active.is_(True),
            AgentMemory.updated_at < cutoff,
            AgentMemory.importance < 5,
        )
        if account_key:
            statement = statement.where(
                (AgentMemory.account_key == account_key)
                | (AgentMemory.account_key == "")
            )
        rows = list((await db.execute(statement)).scalars().all())
        if rows and not dry_run:
            for item in rows:
                item.active = False
            await db.commit()
    return {
        "candidates": len(rows),
        "deleted": 0 if dry_run else len(rows),
        "days": max(1, int(days)),
        "dry_run": dry_run,
    }


async def maybe_forget_sqlite(
    settings: dict[str, Any], *, account_key: str = ""
) -> dict[str, Any]:
    global _LAST_SQLITE_FORGET
    if not settings.get("memory_forgetting_enabled", True):
        return {"deleted": 0, "skipped": "disabled"}
    now = time.time()
    if now - _LAST_SQLITE_FORGET < 86400:
        return {"deleted": 0, "skipped": "recent"}
    _LAST_SQLITE_FORGET = now
    return await forget_sqlite_memories(
        days=int(settings.get("memory_forgetting_days") or 180),
        account_key=account_key,
    )


async def prune_memories(group_id: int | None, keep: int = 200) -> int:
    async with SessionLocal() as db:
        statement = select(AgentMemory).where(AgentMemory.active.is_(True))
        if group_id is None:
            statement = statement.where(AgentMemory.group_id.is_(None))
        else:
            statement = statement.where(AgentMemory.group_id == group_id)
        rows = list(
            (
                await db.execute(
                    statement.order_by(
                        AgentMemory.importance.desc(), AgentMemory.updated_at.desc()
                    )
                )
            )
            .scalars()
            .all()
        )
        remove = rows[keep:]
        for item in remove:
            item.active = False
        if remove:
            await db.commit()
        return len(remove)
