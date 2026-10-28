from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Mem0 enables PostHog telemetry by default. This project is local-first, so
# keep memory contents and usage telemetry on the machine.
os.environ.setdefault("MEM0_TELEMETRY", "False")
os.environ.setdefault("MEM0_TELEMETRY_SAMPLE_RATE", "0")

_MEMORY_CLASS: Any = None
_IMPORT_CHECKED = False
_IMPORT_ERROR = ""
_CACHE: dict[str, Any] = {}
_CACHE_LOCK = asyncio.Lock()
_RUN_LOCK = asyncio.Lock()
_LAST_FORGET = 0.0
_BACKGROUND_TASKS: set[asyncio.Task] = set()

MEM0_DIR = Path("data") / "memory" / "mem0"
DEFAULT_EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_COLLECTION = "agent_memory"


def available() -> bool:
    """Return whether the optional mem0ai package can be imported."""
    global _MEMORY_CLASS, _IMPORT_CHECKED, _IMPORT_ERROR
    if _IMPORT_CHECKED:
        return _MEMORY_CLASS is not None
    _IMPORT_CHECKED = True
    try:
        from mem0 import Memory  # type: ignore

        _MEMORY_CLASS = Memory
    except Exception as exc:  # pragma: no cover - optional dependency
        _IMPORT_ERROR = str(exc)
        logger.info("Mem0 未安装或不可用，使用 SQLite 记忆回退：%s", exc)
        _MEMORY_CLASS = None
    return _MEMORY_CLASS is not None


def enabled(settings: dict[str, Any]) -> bool:
    engine = str(settings.get("memory_engine") or "auto").strip().lower()
    if engine == "sqlite":
        return False
    return available()


def status(settings: dict[str, Any]) -> dict[str, Any]:
    engine = str(settings.get("memory_engine") or "auto").strip().lower()
    return {
        "available": available(),
        "enabled": enabled(settings),
        "engine": engine,
        "collection": str(settings.get("mem0_collection") or DEFAULT_COLLECTION),
        "embedder": DEFAULT_EMBED_MODEL,
        "error": _IMPORT_ERROR,
        "forgetting_enabled": bool(settings.get("memory_forgetting_enabled", True)),
        "forgetting_days": int(settings.get("memory_forgetting_days") or 180),
    }


def _config(settings: dict[str, Any]) -> dict[str, Any] | None:
    api_key = str(settings.get("llm_api_key") or "").strip()
    if not api_key:
        return None
    base_url = str(settings.get("llm_base_url") or "https://api.deepseek.com").strip()
    model = str(settings.get("llm_model") or "deepseek-chat").strip()
    collection = str(
        settings.get("mem0_collection") or DEFAULT_COLLECTION
    ).strip() or DEFAULT_COLLECTION
    MEM0_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "llm": {
            "provider": "deepseek",
            "config": {
                "model": model,
                "temperature": 0.2,
                "max_tokens": 2000,
                "api_key": api_key,
                "deepseek_base_url": base_url.rstrip("/"),
            },
        },
        "embedder": {
            "provider": "fastembed",
            "config": {"model": DEFAULT_EMBED_MODEL},
        },
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": collection,
                "path": str(MEM0_DIR.resolve()),
            },
        },
        "custom_instructions": (
            "请用简体中文提取和书写记忆，保留原始人名、群名、项目名和专有名词。"
            "每条记忆必须能脱离上下文独立理解；不要翻译成英文。"
        ),
    }


def _user_id(account_key: str = "") -> str:
    return str(account_key or "default")


async def _call(method: Any, *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    return await asyncio.to_thread(method, *args, **kwargs)


async def _get_memory(settings: dict[str, Any]) -> Any:
    if not enabled(settings):
        return None
    config = _config(settings)
    if config is None:
        return None
    key = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    async with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        try:
            instance = await asyncio.to_thread(
                _MEMORY_CLASS.from_config, config
            )
        except Exception as exc:
            logger.warning("Mem0 初始化失败，回退 SQLite 记忆：%s", exc)
            return None
        _CACHE.clear()
        _CACHE[key] = instance
        return instance


def _as_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("results") or raw.get("memories") or raw.get("data") or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _as_memory(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    text = str(item.get("memory") or item.get("text") or "").strip()
    return {
        "id": item.get("id") or item.get("memory_id") or "",
        "importance": int(metadata.get("importance") or 3),
        "category": str(metadata.get("category") or "memory"),
        "title": str(metadata.get("title") or "长期记忆"),
        "content": text,
        "scope": str(metadata.get("scope") or "global"),
        "group_id": str(metadata.get("group_id") or ""),
        "contact_id": str(metadata.get("contact_id") or ""),
        "score": item.get("score"),
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
        "metadata": metadata,
    }


def _matches(
    item: dict[str, Any],
    *,
    group_id: int | str | None = None,
    contact_id: int | str | None = None,
) -> bool:
    if group_id not in (None, ""):
        if str(item.get("group_id") or "") != str(group_id):
            return False
    if contact_id not in (None, ""):
        if str(item.get("contact_id") or "") != str(contact_id):
            return False
    return True


async def search(
    settings: dict[str, Any],
    *,
    account_key: str,
    query: str,
    limit: int = 8,
    group_id: int | str | None = None,
    contact_id: int | str | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over Mem0 memories, filtered by group/contact metadata."""
    memory = await _get_memory(settings)
    if memory is None:
        return []
    query = str(query or "").strip()
    user_id = _user_id(account_key)
    filters: dict[str, Any] = {"user_id": user_id}
    if group_id not in (None, ""):
        filters["group_id"] = str(group_id)
    if contact_id not in (None, ""):
        filters["contact_id"] = str(contact_id)
    try:
        async with _RUN_LOCK:
            if query:
                raw = await asyncio.wait_for(
                    _call(
                        memory.search,
                        query,
                        top_k=max(1, min(100, int(limit) * 4)),
                        filters=filters,
                    ),
                    timeout=8.0,
                )
            else:
                raw = await asyncio.wait_for(
                    _call(
                        memory.get_all,
                        filters=filters,
                        top_k=1000,
                    ),
                    timeout=8.0,
                )
    except Exception as exc:
        logger.warning("Mem0 检索失败，回退 SQLite 记忆：%s", exc)
        return []
    items = []
    for item in _as_items(raw):
        memory_item = _as_memory(item)
        if not memory_item["content"]:
            continue
        if not _matches(memory_item, group_id=group_id, contact_id=contact_id):
            continue
        items.append(memory_item)
        if len(items) >= max(1, int(limit)):
            break
    return items


async def add_turn(
    settings: dict[str, Any],
    *,
    account_key: str,
    messages: list[dict[str, str]],
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Extract and persist facts from one conversation turn."""
    memory = await _get_memory(settings)
    if memory is None:
        return False
    clean_messages = [
        {
            "role": str(message.get("role") or "user"),
            "content": str(message.get("content") or "").strip(),
        }
        for message in messages
        if str(message.get("content") or "").strip()
    ]
    if not clean_messages:
        return False
    clean_meta = {
        str(key): value
        for key, value in (metadata or {}).items()
        if value is not None and str(value) != ""
    }
    try:
        async with _RUN_LOCK:
            await asyncio.wait_for(
                _call(
                    memory.add,
                    clean_messages,
                    user_id=_user_id(account_key),
                    metadata=clean_meta,
                ),
                timeout=120.0,
            )
        return True
    except Exception as exc:
        logger.warning("Mem0 写入记忆失败：%s", exc)
        return False


def schedule_add_turn(
    settings: dict[str, Any],
    *,
    account_key: str,
    messages: list[dict[str, str]],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget ingestion so a memory extraction call never delays chat."""
    if not enabled(settings):
        return
    if not any(str(item.get("content") or "").strip() for item in messages):
        return
    task = asyncio.create_task(
        add_turn(
            settings,
            account_key=account_key,
            messages=messages,
            metadata=metadata,
        ),
        name="mem0-add-turn",
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def _parse_time(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


async def forget_old(
    settings: dict[str, Any],
    *,
    account_key: str,
    days: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete memories older than N days, complementing Mem0's search decay."""
    memory = await _get_memory(settings)
    if memory is None:
        return {"deleted": 0, "scanned": 0, "available": False}
    keep_days = max(
        1,
        int(days or settings.get("memory_forgetting_days") or 180),
    )
    cutoff = time.time() - keep_days * 86400
    try:
        async with _RUN_LOCK:
            raw = await asyncio.wait_for(
                _call(
                    memory.get_all,
                    filters={"user_id": _user_id(account_key)},
                    top_k=1000,
                ),
                timeout=30.0,
            )
    except Exception as exc:
        logger.warning("Mem0 读取遗忘候选失败：%s", exc)
        return {"deleted": 0, "scanned": 0, "available": True, "error": str(exc)}
    items = [_as_memory(item) for item in _as_items(raw)]
    candidates = []
    for item in items:
        timestamp = _parse_time(item.get("created_at") or item.get("updated_at"))
        if timestamp and timestamp < cutoff:
            candidates.append(item)
    deleted = 0
    if not dry_run:
        for item in candidates:
            memory_id = str(item.get("id") or "")
            if not memory_id:
                continue
            try:
                async with _RUN_LOCK:
                    await _call(memory.delete, memory_id)
                deleted += 1
            except Exception as exc:
                logger.warning("Mem0 删除记忆失败 %s：%s", memory_id, exc)
    return {
        "deleted": deleted,
        "candidates": len(candidates),
        "scanned": len(items),
        "days": keep_days,
        "dry_run": dry_run,
        "available": True,
    }


async def maybe_forget(
    settings: dict[str, Any], *, account_key: str
) -> dict[str, Any]:
    global _LAST_FORGET
    if not enabled(settings) or not settings.get("memory_forgetting_enabled", True):
        return {"deleted": 0, "skipped": "disabled"}
    now = time.time()
    if now - _LAST_FORGET < 86400:
        return {"deleted": 0, "skipped": "recent"}
    _LAST_FORGET = now
    return await forget_old(settings, account_key=account_key)


def warmup(settings: dict[str, Any]) -> None:
    """Best-effort background initialization for the first memory search."""
    if not enabled(settings):
        return
    if not settings.get("llm_api_key"):
        return
    task = asyncio.create_task(_get_memory(settings), name="mem0-warmup")
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
