"""Qunban sidecar v1: memory / knowledge / context reads.

The main Qunban API keeps its orchestration and write path.  This process owns
the memory and knowledge retrieval boundary so those reads can be moved out of
the producer process without changing the WeChat transport service.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from fastapi import FastAPI
from pydantic import BaseModel

from app.db import SessionLocal
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8020, log_level="info")
