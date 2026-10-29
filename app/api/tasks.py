from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

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


@router.delete("/tasks")
async def clear_tasks() -> dict:
    await task_runs.clear()
    return {"ok": True}
