"""Durable Agent task records.

A task is not a chat message and not a tool call: it is a unit of promised work
with explicit steps and a terminal state.  This is the record the Agent must
create before it says "I will do that".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import AgentTaskRun

STEP_NAMES = ("ack", "execute", "send", "verify")
_EVENT_SINK: Callable[[dict[str, Any]], None] | None = None


def set_event_sink(sink: Callable[[dict[str, Any]], None] | None) -> None:
    global _EVENT_SINK
    _EVENT_SINK = sink


def _emit(task: dict[str, Any]) -> None:
    if _EVENT_SINK is None:
        return
    try:
        _EVENT_SINK(task)
    except Exception:
        pass


def _dict(row: AgentTaskRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "source": row.source,
        "group_id": row.group_id,
        "message_id": row.message_id,
        "session_id": row.session_id,
        "request": row.request,
        "status": row.status,
        "steps": row.steps or [],
        "result": row.result or "",
        "error": row.error or "",
        "attempts": row.attempts,
        "verified": bool(row.verified),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def initial_steps(*, ack: bool = True) -> list[dict[str, Any]]:
    steps = [
        {"name": "ack", "status": "pending" if ack else "skipped"},
        {"name": "execute", "status": "pending"},
        {"name": "send", "status": "pending"},
        {"name": "verify", "status": "pending"},
    ]
    return steps


async def create(
    *,
    kind: str,
    source: str,
    group_id: int | None,
    message_id: int | None = None,
    session_id: str,
    request: str,
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    async with SessionLocal() as db:
        row = AgentTaskRun(
            kind=str(kind or "group_task")[:32],
            source=str(source or "dsh_primary")[:32],
            group_id=int(group_id) if group_id else None,
            message_id=int(message_id) if message_id else None,
            session_id=str(session_id or "")[:96],
            request=str(request or ""),
            status="queued",
            steps=steps or initial_steps(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        task = _dict(row)
        _emit(task)
        return task


async def update(
    task_id: int,
    *,
    status: str | None = None,
    result: str | None = None,
    error: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    verified: bool | None = None,
    attempts: int | None = None,
) -> dict[str, Any] | None:
    async with SessionLocal() as db:
        row = await db.get(AgentTaskRun, int(task_id))
        if row is None:
            return None
        if status:
            row.status = status
        if result is not None:
            row.result = str(result)
        if error is not None:
            row.error = str(error)
        if steps is not None:
            row.steps = steps
        if verified is not None:
            row.verified = bool(verified)
        if attempts is not None:
            row.attempts = int(attempts)
        if status == "running" and row.started_at is None:
            row.started_at = datetime.utcnow()
        if status in {"done", "failed", "cancelled"}:
            row.finished_at = datetime.utcnow()
        await db.commit()
        await db.refresh(row)
        task = _dict(row)
        _emit(task)
        return task


async def step(
    task_id: int,
    name: str,
    status: str,
    *,
    detail: str = "",
) -> dict[str, Any] | None:
    async with SessionLocal() as db:
        row = await db.get(AgentTaskRun, int(task_id))
        if row is None:
            return None
        steps = list(row.steps or [])
        found = False
        for item in steps:
            if isinstance(item, dict) and item.get("name") == name:
                item["status"] = status
                item["detail"] = detail[:500]
                item["updated_at"] = datetime.utcnow().isoformat()
                found = True
                break
        if not found:
            steps.append(
                {
                    "name": name,
                    "status": status,
                    "detail": detail[:500],
                    "updated_at": datetime.utcnow().isoformat(),
                }
            )
        row.steps = steps
        await db.commit()
        await db.refresh(row)
        task = _dict(row)
        _emit(task)
        return task


async def get(task_id: int) -> dict[str, Any] | None:
    async with SessionLocal() as db:
        row = await db.get(AgentTaskRun, int(task_id))
        return _dict(row) if row else None


async def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(AgentTaskRun)
                .order_by(AgentTaskRun.id.desc())
                .limit(max(1, min(200, int(limit))))
            )
        ).scalars().all()
        return [_dict(row) for row in rows]


async def clear() -> None:
    async with SessionLocal() as db:
        await db.execute(delete(AgentTaskRun))
        await db.commit()
