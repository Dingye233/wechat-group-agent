"""Durable Agent task records.

A task is not a chat message and not a tool call: it is a unit of promised work
with explicit steps and a terminal state.  This is the record the Agent must
create before it says "I will do that".
"""

from __future__ import annotations
from app.services.timefmt import iso_utc

from datetime import datetime
import copy
import os
from typing import Any, Callable

import httpx
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


def _sidecar_url() -> str:
    return str(os.environ.get("QUNBAN_SIDECAR_URL") or "").rstrip("/")


async def _remote(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    base = _sidecar_url()
    if not base:
        return None
    async with httpx.AsyncClient(base_url=base, timeout=10.0) as client:
        response = await client.request(method, path, json=payload)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()


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
        "created_at": iso_utc(row.created_at),
        "started_at": iso_utc(row.started_at),
        "finished_at": iso_utc(row.finished_at),
        "updated_at": iso_utc(row.updated_at),
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
    if _sidecar_url():
        try:
            data = await _remote(
                "POST",
                "/v1/tasks",
                {
                    "kind": kind,
                    "source": source,
                    "group_id": group_id,
                    "message_id": message_id,
                    "session_id": session_id,
                    "request": request,
                    "steps": steps or initial_steps(),
                },
            )
            task = dict((data or {}).get("task") or {})
            if task:
                _emit(task)
                return task
        except Exception:
            pass
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
    if _sidecar_url():
        payload = {
            key: value
            for key, value in {
                "status": status,
                "result": result,
                "error": error,
                "steps": steps,
                "verified": verified,
                "attempts": attempts,
            }.items()
            if value is not None
        }
        try:
            data = await _remote("PATCH", f"/v1/tasks/{int(task_id)}", payload)
            task = (data or {}).get("task")
            if task:
                _emit(task)
                return task
        except Exception:
            pass
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
    if _sidecar_url():
        try:
            data = await _remote(
                "POST",
                f"/v1/tasks/{int(task_id)}/step",
                {"name": name, "status": status, "detail": detail},
            )
            task = (data or {}).get("task")
            if task:
                _emit(task)
                return task
        except Exception:
            pass
    async with SessionLocal() as db:
        row = await db.get(AgentTaskRun, int(task_id))
        if row is None:
            return None
        steps = copy.deepcopy(list(row.steps or []))
        found = False
        for item in steps:
            if isinstance(item, dict) and item.get("name") == name:
                item["status"] = status
                item["detail"] = detail[:500]
                item["updated_at"] = iso_utc(datetime.utcnow())
                found = True
                break
        if not found:
            steps.append(
                {
                    "name": name,
                    "status": status,
                    "detail": detail[:500],
                    "updated_at": iso_utc(datetime.utcnow()),
                }
            )
        row.steps = steps
        await db.commit()
        await db.refresh(row)
        task = _dict(row)
        _emit(task)
        return task


async def get(task_id: int) -> dict[str, Any] | None:
    if _sidecar_url():
        try:
            data = await _remote("GET", f"/v1/tasks/{int(task_id)}")
            return (data or {}).get("task")
        except Exception:
            pass
    async with SessionLocal() as db:
        row = await db.get(AgentTaskRun, int(task_id))
        return _dict(row) if row else None


async def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    if _sidecar_url():
        try:
            data = await _remote("GET", f"/v1/tasks?limit={int(limit)}")
            return list((data or {}).get("tasks") or [])
        except Exception:
            pass
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
    if _sidecar_url():
        try:
            await _remote("DELETE", "/v1/tasks")
            return
        except Exception:
            pass
    async with SessionLocal() as db:
        await db.execute(delete(AgentTaskRun))
        await db.commit()
