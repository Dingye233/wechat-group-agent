from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.services import task_runs

router = APIRouter(tags=["tasks"])


@router.get("/tasks")
async def tasks(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return {"tasks": await task_runs.list_tasks(limit)}


@router.get("/tasks/{task_id}")
async def task(task_id: int) -> dict:
    row = await task_runs.get(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task": row}


@router.post("/tasks")
async def create_task(payload: dict = Body(...)) -> dict:
    task = await task_runs.create(
        kind=str(payload.get("kind") or "group_task"),
        source=str(payload.get("source") or "manual"),
        group_id=int(payload["group_id"]) if payload.get("group_id") else None,
        session_id=str(payload.get("session_id") or ""),
        request=str(payload.get("request") or ""),
    )
    return {"task": task}


@router.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: int,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    row = await task_runs.get(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if row.get("status") not in {"failed", "cancelled", "queued", "done"}:
        raise HTTPException(status_code=409, detail="任务仍在执行，不能重试")
    await task_runs.update(
        task_id,
        status="queued",
        result="",
        error="",
        verified=False,
        steps=task_runs.initial_steps(ack=True),
        attempts=int(row.get("attempts") or 0),
    )
    runtime.agent.restart_primary_task(task_id)
    return {"task": await task_runs.get(task_id)}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    row = await task_runs.get(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    runtime.agent.cancel_primary_task(task_id)
    await task_runs.update(
        task_id,
        status="cancelled",
        error="用户取消",
    )
    return {"task": await task_runs.get(task_id)}


@router.delete("/tasks")
async def clear_tasks() -> dict:
    await task_runs.clear()
    return {"ok": True}
