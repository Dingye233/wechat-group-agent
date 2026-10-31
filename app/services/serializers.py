from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from app.models import (
    AgentLog,
    ChatTask,
    DirectMessage,
    Group,
    GroupConfig,
    Message,
    Sticker,
)

EMPTY_CONFIG = {
    "read_enabled": False,
    "read_interval_seconds": 0,
    "agent_enabled": False,
    "tools_enabled": True,
    "trigger_mode": "mention",
    "keywords": [],
    "probability": 1.0,
    "cooldown_seconds": 30,
    "max_replies_per_hour": 30,
    "persona": "",
    "model": "",
    "sticker_enabled": True,
    "multi_message_enabled": False,
    "max_messages": 3,
    "proactive_enabled": False,
    "proactive_interval_minutes": 90,
    "quiet_start": "",
    "quiet_end": "",
    "mention_on_reply": False,
    "roast_mode": False,
}


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sticker_url(filename: str) -> str:
    return f"/media/stickers/{quote(filename)}"


def config_dict(config: GroupConfig | None) -> dict[str, Any]:
    if config is None:
        return dict(EMPTY_CONFIG)
    return {
        "read_enabled": config.read_enabled,
        "read_interval_seconds": config.read_interval_seconds,
        "agent_enabled": config.agent_enabled,
        "tools_enabled": config.tools_enabled,
        "trigger_mode": config.trigger_mode,
        "keywords": list(config.keywords or []),
        "probability": config.probability,
        "cooldown_seconds": config.cooldown_seconds,
        "max_replies_per_hour": config.max_replies_per_hour,
        "persona": config.persona or "",
        "model": config.model or "",
        "sticker_enabled": config.sticker_enabled,
        "multi_message_enabled": config.multi_message_enabled,
        "max_messages": config.max_messages,
        "proactive_enabled": config.proactive_enabled,
        "proactive_interval_minutes": config.proactive_interval_minutes,
        "quiet_start": config.quiet_start or "",
        "quiet_end": config.quiet_end or "",
        "mention_on_reply": config.mention_on_reply,
        "mention_mode": getattr(config, "mention_mode", None)
        or ("always" if config.mention_on_reply else "off"),
        "roast_mode": bool(config.roast_mode),
    }


def message_dict(message: Message) -> dict[str, Any]:
    media = ""
    if message.media_path:
        if message.media_path.startswith("stickers/"):
            media = sticker_url(message.media_path.split("/", 1)[1])
        elif message.media_path.startswith("chat/"):
            media = f"/media/chat/{quote(message.media_path.split('/', 1)[1])}"
        elif message.media_path.startswith("replica/"):
            media = f"/media/replica/{quote(message.media_path.split('/', 1)[1])}"
        elif message.media_path.startswith("/"):
            media = message.media_path
        else:
            media = sticker_url(message.media_path)
    return {
        "id": message.id,
        "group_id": message.group_id,
        "external_id": message.external_id,
        "sender_id": message.sender_wxid,
        "sender_name": message.sender_name,
        "is_from_agent": message.is_from_agent,
        "is_self": message.is_self,
        "kind": message.kind,
        "content": message.content,
        "media_url": media,
        "sticker_url": media if message.kind == "sticker" else "",
        "created_at": iso(message.created_at),
        "meta": message.meta or {},
    }


def chat_task_dict(task: ChatTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "group_id": task.group_id,
        "message_id": task.message_id,
        "user_wxid": task.user_wxid,
        "user_name": task.user_name,
        "intent": task.intent,
        "query": task.query,
        "engine": str((task.plan or {}).get("engine") or "qunban"),
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "created_at": iso(task.created_at),
        "updated_at": iso(task.updated_at),
        "finished_at": iso(task.finished_at) if task.finished_at else "",
    }


def direct_message_dict(message: DirectMessage) -> dict[str, Any]:
    media = ""
    if message.media_path:
        if message.media_path.startswith("replica/"):
            media = f"/media/replica/{quote(message.media_path.split('/', 1)[1])}"
        elif message.media_path.startswith("chat/"):
            media = f"/media/chat/{quote(message.media_path.split('/', 1)[1])}"
        else:
            media = message.media_path
    return {
        "id": message.id,
        "contact_id": message.contact_id,
        "sender_wxid": message.sender_wxid,
        "sender_name": message.sender_name,
        "is_self": message.is_self,
        "kind": message.kind,
        "content": message.content,
        "media_url": media,
        "created_at": iso(message.created_at),
        "meta": message.meta or {},
    }


def last_message_dict(message: Message | None) -> dict[str, Any] | None:
    if message is None:
        return None
    return {
        "id": message.id,
        "sender_name": message.sender_name,
        "content": message.content,
        "kind": message.kind,
        "is_from_agent": message.is_from_agent,
        "created_at": iso(message.created_at),
    }


def group_dict(
    group: Group,
    config: GroupConfig | None,
    last_message: Message | None,
    message_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": group.id,
        "external_id": group.external_id,
        "name": group.name,
        "remark": group.remark,
        "member_count": group.member_count,
        "avatar": group.avatar,
        "last_message_at": iso(group.last_message_at),
        "created_at": iso(group.created_at),
        "agent": config_dict(config),
        "last_message": last_message_dict(last_message),
        "message_count": message_count,
    }


def sticker_dict(sticker: Sticker) -> dict[str, Any]:
    return {
        "id": sticker.id,
        "name": sticker.name,
        "tags": list(sticker.tags or []),
        "description": sticker.description,
        "url": sticker_url(sticker.filename),
        "filename": sticker.filename,
        "mime": sticker.mime,
        "width": sticker.width,
        "height": sticker.height,
        "size_bytes": sticker.size_bytes,
        "usage_count": sticker.usage_count,
        "created_at": iso(sticker.created_at),
    }


def agent_log_dict(log: AgentLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "group_id": log.group_id,
        "level": log.level,
        "event": log.event,
        "detail": log.detail,
        "created_at": iso(log.created_at),
    }
