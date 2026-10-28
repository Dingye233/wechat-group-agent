from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import get_db
from app.models import AgentLog, Group, GroupConfig, Message, Sticker
from app.schemas import GroupConfigUpdate, SendMessageRequest
from app.services.serializers import agent_log_dict, group_dict, message_dict

router = APIRouter(tags=["groups"])


async def _require_group(
    db: AsyncSession, group_id: int, runtime: RuntimeManager | None = None
) -> Group:
    group = await db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="群聊不存在")
    if (
        runtime is not None
        and group.account_key
        and runtime.account_key
        and group.account_key != runtime.account_key
    ):
        raise HTTPException(status_code=404, detail="该群聊属于其他微信账号")
    return group


async def _group_payload(
    db: AsyncSession, group_id: int, runtime: RuntimeManager | None = None
) -> dict:
    group = await _require_group(db, group_id, runtime)
    config = await db.get(GroupConfig, group_id)
    last = (
        await db.execute(
            select(Message)
            .where(Message.group_id == group_id)
            .order_by(Message.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    count = (
        await db.execute(
            select(func.count(Message.id)).where(Message.group_id == group_id)
        )
    ).scalar() or 0
    return group_dict(group, config, last, int(count))


@router.get("/groups")
async def list_groups(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    return {"groups": await runtime.list_group_payloads()}


@router.post("/groups/refresh")
async def refresh_groups(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    try:
        groups = await runtime.refresh_groups()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"groups": groups}


@router.get("/groups/{group_id}")
async def get_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    return {"group": await _group_payload(db, group_id, runtime)}


@router.get("/groups/{group_id}/messages")
async def get_messages(
    group_id: int,
    limit: int = Query(default=60, ge=1, le=200),
    before_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    await _require_group(db, group_id, runtime)
    statement = select(Message).where(Message.group_id == group_id)
    if before_id:
        statement = statement.where(Message.id < before_id)
    rows = list(
        (
            await db.execute(
                statement.order_by(Message.id.desc()).limit(limit + 1)
            )
        ).scalars().all()
    )
    has_more = len(rows) > limit
    rows = list(reversed(rows[:limit]))
    return {
        "messages": [message_dict(row) for row in rows],
        "has_more": has_more,
    }


@router.get("/groups/{group_id}/members")
async def get_members(
    group_id: int, runtime: RuntimeManager = Depends(get_runtime)
) -> dict:
    try:
        members = await runtime.sync_members(group_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "members": [
            {
                "wxid": row.wxid,
                "nickname": row.nickname,
                "alias": row.alias,
                "is_owner": row.is_owner,
                "is_admin": row.is_admin,
            }
            for row in members
        ],
        "count": len(members),
    }


@router.post("/groups/{group_id}/pin")
async def pin_group(
    group_id: int,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        result = await runtime.pin_group(group_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result, "status": runtime.status_dict()}


@router.delete("/groups/{group_id}/pin")
async def unpin_group(
    group_id: int,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    result = await runtime.unpin_group(group_id)
    return {"ok": True, **result, "status": runtime.status_dict()}


@router.patch("/groups/{group_id}/config")
async def update_config(
    group_id: int,
    payload: GroupConfigUpdate,
    db: AsyncSession = Depends(get_db),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    await _require_group(db, group_id, runtime)
    config = await db.get(GroupConfig, group_id)
    if config is None:
        config = GroupConfig(group_id=group_id)
        db.add(config)
        await db.flush()

    values = payload.model_dump(exclude_unset=True)
    # Reply requires reading. Read-only is the safe default when the reply
    # switch is off; disabling reading always disables auto-reply.
    if values.get("read_enabled") is False:
        values["agent_enabled"] = False
    elif values.get("agent_enabled") is True:
        values["read_enabled"] = True
    for key, value in values.items():
        if value is None:
            continue
        setattr(config, key, value)
    if "quiet_start" in values and "quiet_end" in values:
        if not values["quiet_start"] and not values["quiet_end"]:
            config.quiet_start = ""
            config.quiet_end = ""
    await db.commit()
    await db.refresh(config)
    await runtime.apply_read_filter()
    runtime.broadcast(
        "groups.updated",
        {"count": 1, "group_id": group_id, "reason": "config"},
    )
    return {"group": await _group_payload(db, group_id, runtime)}


@router.post("/groups/{group_id}/send")
async def send_message(
    group_id: int,
    payload: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    await _require_group(db, group_id, runtime)
    sticker = None
    if payload.sticker_id is not None:
        sticker = await db.get(Sticker, payload.sticker_id)
        if sticker is None:
            raise HTTPException(status_code=404, detail="表情包不存在")
    try:
        messages = await runtime.send_manual_message(
            group_id, text=payload.text, sticker=sticker
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"发送失败：{exc}") from exc
    return {"ok": True, "messages": messages}


@router.post("/groups/{group_id}/media/backfill")
async def backfill_group_images(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    await _require_group(db, group_id, runtime)
    return await runtime.backfill_images(group_id=group_id, limit_per_group=80)


@router.get("/groups/{group_id}/logs")
async def group_logs(
    group_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    await _require_group(db, group_id, runtime)
    rows = (
        await db.execute(
            select(AgentLog)
            .where(AgentLog.group_id == group_id)
            .order_by(AgentLog.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {"logs": [agent_log_dict(row) for row in rows]}
