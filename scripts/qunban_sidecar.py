"""Qunban sidecar v1: memory / knowledge / context reads.

The main Qunban API keeps its orchestration and write path.  This process owns
the memory and knowledge retrieval boundary so those reads can be moved out of
the producer process without changing the WeChat transport service.
"""

from __future__ import annotations

import asyncio
import copy
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import AgentTaskRun
from app.services import bili_knowledge
from app.services.memory import build_memory_context, search_memories

app = FastAPI(title="qunban-sidecar")


class GroupContextRequest(BaseModel):
    group_id: int
    account_key: str = ""
    query: str = ""
    settings: dict[str, Any] = {}


class PrivateContextRequest(BaseModel):
    contact_wxid: str
    account_key: str = ""
    query: str = ""
    settings: dict[str, Any] = {}


class SaveMemoryRequest(BaseModel):
    group_id: int | None = None
    contact_wxid: str = ""
    account_key: str = ""
    scope: str = "global"
    category: str = "general"
    title: str = ""
    content: str = ""
    importance: int = 3
    source: str = "sidecar"


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "qunban-sidecar", "version": 1}


@app.post("/v1/memory/group_context")
async def group_context(payload: GroupContextRequest) -> dict[str, Any]:
    async with SessionLocal() as db:
        text, ids = await build_memory_context(
            db,
            group_id=payload.group_id,
            account_key=payload.account_key,
            settings=payload.settings,
            query=payload.query,
        )
    return {"memory": text, "ids": ids}


@app.post("/v1/memory/private_context")
async def private_context(payload: PrivateContextRequest) -> dict[str, Any]:
    async with SessionLocal() as db:
        text, ids = await build_memory_context(
            db,
            group_id=None,
            account_key=payload.account_key,
            contact_wxid=payload.contact_wxid,
            settings=payload.settings,
            query=payload.query,
        )
    return {"memory": text, "ids": ids}


@app.post("/v1/memory/search")
async def memory_search(payload: dict[str, Any]) -> dict[str, Any]:
    rows = await search_memories(
        query=str(payload.get("query") or ""),
        group_id=payload.get("group_id"),
        account_key=str(payload.get("account_key") or ""),
        contact_wxid=str(payload.get("contact_wxid") or ""),
        limit=int(payload.get("limit") or 20),
    )
    return {"memories": rows}


@app.post("/v1/memory/save")
async def memory_save(payload: SaveMemoryRequest) -> dict[str, Any]:
    from app.services.memory import memory_dict, save_memory

    row = await save_memory(
        group_id=payload.group_id,
        contact_wxid=payload.contact_wxid,
        account_key=payload.account_key,
        scope=payload.scope,
        category=payload.category,
        title=payload.title,
        content=payload.content,
        importance=payload.importance,
        source=payload.source,
    )
    return {"memory": memory_dict(row)}


@app.post("/v1/rules/trigger")
async def rules_trigger(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("content") or "").strip()
    mode = str(payload.get("mode") or "mention")
    if not text:
        return {"triggered": False}
    if mode == "all":
        return {"triggered": True}
    if mode == "keyword":
        lowered = text.lower()
        keywords = [
            str(item).strip()
            for item in payload.get("keywords") or []
            if str(item).strip()
        ]
        return {"triggered": any(item.lower() in lowered for item in keywords)}
    if mode == "mention":
        if bool(payload.get("mentioned_me")):
            return {"triggered": True}
        compact = text.replace("\u2005", " ").replace("\xa0", " ")
        aliases = [
            str(item).strip()
            for item in payload.get("aliases") or []
            if str(item).strip()
        ]
        return {"triggered": any(f"@{alias}" in compact for alias in aliases)}
    return {"triggered": False}


@app.post("/v1/knowledge/search")
async def knowledge_search(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    limit = max(1, min(10, int(payload.get("limit") or 3)))
    try:
        rows = await asyncio.wait_for(
            asyncio.to_thread(bili_knowledge.search, query, limit),
            timeout=3.0,
        )
    except Exception as exc:
        return {"results": [], "error": str(exc) or type(exc).__name__}
    return {"results": rows}


def _task_dict(row: AgentTaskRun) -> dict[str, Any]:
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


def _initial_steps() -> list[dict[str, Any]]:
    return [
        {"name": "ack", "status": "pending"},
        {"name": "execute", "status": "pending"},
        {"name": "send", "status": "pending"},
        {"name": "verify", "status": "pending"},
    ]


@app.post("/v1/tasks")
async def task_create(payload: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as db:
        row = AgentTaskRun(
            kind=str(payload.get("kind") or "group_task")[:32],
            source=str(payload.get("source") or "dsh_primary")[:32],
            group_id=int(payload["group_id"]) if payload.get("group_id") else None,
            message_id=int(payload["message_id"]) if payload.get("message_id") else None,
            session_id=str(payload.get("session_id") or "")[:96],
            request=str(payload.get("request") or ""),
            status="queued",
            steps=payload.get("steps") or _initial_steps(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return {"task": _task_dict(row)}


@app.get("/v1/tasks")
async def task_list(limit: int = 50) -> dict[str, Any]:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(AgentTaskRun)
                .order_by(AgentTaskRun.id.desc())
                .limit(max(1, min(200, int(limit))))
            )
        ).scalars().all()
    return {"tasks": [_task_dict(row) for row in rows]}


@app.get("/v1/tasks/{task_id}")
async def task_get(task_id: int) -> dict[str, Any]:
    async with SessionLocal() as db:
        row = await db.get(AgentTaskRun, int(task_id))
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task": _task_dict(row)}


@app.patch("/v1/tasks/{task_id}")
async def task_update(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as db:
        row = await db.get(AgentTaskRun, int(task_id))
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        for key in ("status", "result", "error", "steps", "verified", "attempts"):
            if key in payload:
                value = payload[key]
                if key == "verified":
                    row.verified = bool(value)
                elif key == "attempts":
                    row.attempts = int(value)
                else:
                    setattr(row, key, value)
        status = str(row.status)
        if status == "running" and row.started_at is None:
            row.started_at = datetime.utcnow()
        if status in {"done", "failed", "cancelled"}:
            row.finished_at = datetime.utcnow()
        await db.commit()
        await db.refresh(row)
        return {"task": _task_dict(row)}


@app.post("/v1/tasks/{task_id}/step")
async def task_step(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as db:
        row = await db.get(AgentTaskRun, int(task_id))
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        name = str(payload.get("name") or "")
        status = str(payload.get("status") or "pending")
        detail = str(payload.get("detail") or "")[:500]
        steps = copy.deepcopy(list(row.steps or []))
        found = False
        for item in steps:
            if isinstance(item, dict) and item.get("name") == name:
                item["status"] = status
                item["detail"] = detail
                item["updated_at"] = datetime.utcnow().isoformat()
                found = True
                break
        if not found:
            steps.append(
                {
                    "name": name,
                    "status": status,
                    "detail": detail,
                    "updated_at": datetime.utcnow().isoformat(),
                }
            )
        row.steps = steps
        await db.commit()
        await db.refresh(row)
        return {"task": _task_dict(row)}


@app.delete("/v1/tasks")
async def task_clear() -> dict[str, Any]:
    async with SessionLocal() as db:
        await db.execute(delete(AgentTaskRun))
        await db.commit()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8020, log_level="info")
