"""Persistent task rows for private-chat tool calls."""

from __future__ import annotations
from app.services.timefmt import iso_utc

from typing import Any, Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.models import AssistantToolTask

_Broadcast = Callable[[str, dict[str, Any]], Any]


def _task_dict(row: AssistantToolTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "account_key": row.account_key,
        "channel": row.channel,
        "call_id": row.call_id,
        "tool_name": row.tool_name,
        "tool_input": row.tool_input or {},
        "tool_output": row.tool_output or "",
        "status": row.status,
        "error": row.error or "",
        "created_at": iso_utc(row.created_at),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def start(
    *,
    session_id: str,
    account_key: str,
    channel: str,
    call_id: str,
    tool_name: str,
    tool_input: dict[str, Any] | None,
    broadcast: _Broadcast | None = None,
) -> dict[str, Any]:
    async with SessionLocal() as db:
        row = AssistantToolTask(
            session_id=str(session_id or "main")[:64],
            account_key=str(account_key or "")[:160],
            channel=str(channel or "dashboard")[:24],
            call_id=str(call_id or "")[:96],
            tool_name=str(tool_name or "tool")[:96],
            tool_input=tool_input if isinstance(tool_input, dict) else {},
            status="running",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        payload = _task_dict(row)
    if broadcast:
        try:
            broadcast("assistant.tool_task", {"action": "started", "task": payload})
        except Exception:
            pass
    return payload


async def finish(
    *,
    session_id: str,
    call_id: str,
    tool_name: str,
    output: str,
    ok: bool,
    broadcast: _Broadcast | None = None,
) -> dict[str, Any] | None:
    async with SessionLocal() as db:
        row = None
        if call_id:
            row = (
                await db.execute(
                    select(AssistantToolTask)
                    .where(
                        AssistantToolTask.session_id == str(session_id or "main")[:64],
                        AssistantToolTask.call_id == str(call_id)[:96],
                    )
                    .order_by(AssistantToolTask.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            row = (
                await db.execute(
                    select(AssistantToolTask)
                    .where(
                        AssistantToolTask.session_id == str(session_id or "main")[:64],
                        AssistantToolTask.tool_name == str(tool_name or "tool")[:96],
                        AssistantToolTask.status == "running",
                    )
                    .order_by(AssistantToolTask.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "done" if ok else "error"
        row.tool_output = str(output or "")[:12000]
        row.error = "" if ok else str(output or "")[:4000]
        await db.commit()
        await db.refresh(row)
        payload = _task_dict(row)
    if broadcast:
        try:
            broadcast("assistant.tool_task", {"action": "finished", "task": payload})
        except Exception:
            pass
    await prune_completed()
    return payload


async def prune_completed(max_age_seconds: int = 120) -> int:
    """Drop finished tool-call rows after a short visible history.

    Running tasks are never touched.  The dashboard keeps recent done/error
    rows for a couple of minutes so the user can see what just happened, then
    the row disappears instead of accumulating for a whole day.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        seconds=max(30, int(max_age_seconds))
    )
    async with SessionLocal() as db:
        result = await db.execute(
            delete(AssistantToolTask).where(
                AssistantToolTask.status.in_(("done", "error")),
                AssistantToolTask.updated_at < cutoff,
            )
        )
        await db.commit()
        return int(result.rowcount or 0)


async def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(200, int(limit)))
    await prune_completed()
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(AssistantToolTask)
                .order_by(AssistantToolTask.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [_task_dict(row) for row in rows]


async def count() -> int:
    await prune_completed()
    async with SessionLocal() as db:
        return int(
            (await db.execute(select(func.count(AssistantToolTask.id)))).scalar() or 0
        )


async def clear() -> None:
    async with SessionLocal() as db:
        await db.execute(delete(AssistantToolTask))
        await db.commit()
