from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import get_runtime
from app.config import settings
from app.core.runtime import RuntimeManager
from app.db import SessionLocal
from app.models import AgentLog, Group, GroupMember, Message, Sticker
from app.services.settings import get_settings

router = APIRouter(tags=["system"])


@router.get("/status")
async def status(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    data = runtime.status_dict()
    async with SessionLocal() as db:
        saved = await get_settings(db)
    data["llm_configured"] = bool(saved.get("llm_api_key"))
    data["demo_mode"] = not bool(saved.get("llm_api_key"))
    data["llm_model"] = saved.get("llm_model")
    data["timezone"] = saved.get("timezone")
    return data


@router.get("/health")
async def health() -> dict:
    return {"ok": True, "version": settings.version}


@router.post("/control/reattach")
async def reattach(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    result = await runtime.reattach()
    return {"ok": True, **result, "status": runtime.status_dict()}


@router.post("/control/detach")
async def detach(
    reason: str = Query(default="用户接管鼠标"),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    result = await runtime.detach(reason)
    return {"ok": True, **result, "status": runtime.status_dict()}


@router.get("/debug/uia")
async def debug_uia(
    action: str = Query(default="all"),
    depth: int = Query(default=8, ge=1, le=12),
    group: str = Query(default=""),
    kind: str = Query(default=""),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    """Local-only UIA tree dump used while developing the UI adapter."""
    adapter = runtime.adapter
    if adapter is None or getattr(adapter, "adapter_id", "") != "uia":
        raise HTTPException(status_code=400, detail="当前未连接界面自动化适配器")
    dump = getattr(adapter, "debug_dump", None)
    if not callable(dump):
        raise HTTPException(status_code=400, detail="适配器不支持 UI 诊断")
    lines = await asyncio.to_thread(dump, action, depth, group, kind)
    return {"action": action, "count": len(lines), "lines": lines}


@router.get("/debug/replica")
async def debug_replica(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = runtime.adapter
    if adapter is None or getattr(adapter, "adapter_id", "") != "replica":
        raise HTTPException(status_code=400, detail="当前未连接微信 4.x 适配器")
    diagnostics = getattr(adapter, "diagnostics", None)
    if not callable(diagnostics):
        raise HTTPException(status_code=400, detail="适配器不支持诊断")
    lines = []
    data = diagnostics()
    for key in ("account", "self_wxid", "groups", "readable", "pinned"):
        lines.append(f"{key}|{data.get(key)}")
    lines.append(f"detached|{data.get('detached')}")
    lines.append(f"listener|{data.get('listener')}")
    def label(room: str) -> str:
        raw = room[len("replica:") :] if room.startswith("replica:") else room
        try:
            return adapter._group_name(raw) or "未命名群聊"
        except Exception:
            return "未命名群聊"

    for room, seconds in sorted((data.get("timers") or {}).items()):
        lines.append(
            f"timer|{label(room)}|next_in={seconds}"
            f"|interval={adapter._read_schedule.get(room, adapter._poll_interval)}"
        )
    for room, count in sorted((data.get("pending") or {}).items()):
        lines.append(f"pending|{label(room)}|count={count}")
    # Do not leak raw chatroom ids in diagnostics; the dashboard only deals
    # in display names now.
    data = dict(data)
    data["timers"] = {
        label(room): seconds for room, seconds in (data.get("timers") or {}).items()
    }
    data["pending"] = {
        label(room): count for room, count in (data.get("pending") or {}).items()
    }
    return {"action": "replica", "count": len(lines), "data": data, "lines": lines}


@router.get("/stats")
async def stats(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    async with SessionLocal() as db:
        group_stmt = select(Group.id)
        if runtime.account_key:
            group_stmt = group_stmt.where(Group.account_key == runtime.account_key)
        group_ids = group_stmt.subquery()
        groups = (
            await db.execute(select(func.count()).select_from(group_ids))
        ).scalar() or 0
        messages = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.group_id.in_(select(group_ids.c.id))
                )
            )
        ).scalar() or 0
        stickers = (await db.execute(select(func.count(Sticker.id)))).scalar() or 0
        members = (
            await db.execute(
                select(func.count(GroupMember.id)).where(
                    GroupMember.group_id.in_(select(group_ids.c.id))
                )
            )
        ).scalar() or 0
        replies = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.is_from_agent.is_(True),
                    Message.group_id.in_(select(group_ids.c.id)),
                )
            )
        ).scalar() or 0
        agent_logs = (
            await db.execute(
                select(func.count(AgentLog.id)).where(
                    AgentLog.group_id.in_(select(group_ids.c.id))
                )
            )
        ).scalar() or 0
    try:
        db_files = [
            settings.db_path,
            settings.db_path.with_suffix(settings.db_path.suffix + "-wal"),
            settings.db_path.with_suffix(settings.db_path.suffix + "-shm"),
        ]
        db_size = sum(path.stat().st_size for path in db_files if path.exists())
    except OSError:
        db_size = 0
    return {
        "groups": int(groups),
        "messages": int(messages),
        "stickers": int(stickers),
        "members": int(members),
        "agent_replies": int(replies),
        "agent_logs": int(agent_logs),
        "db_size_bytes": int(db_size),
    }
