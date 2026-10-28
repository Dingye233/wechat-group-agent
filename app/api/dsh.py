from __future__ import annotations

from fastapi import APIRouter, Body

from app.db import SessionLocal
from app.services.agent_engines import resolve_agent_engine
from app.services.dsh_agent import get_dsh_agent_runtime
from app.services.dsh_runtime import get_dsh_runtime
from app.services.settings import get_settings

router = APIRouter(tags=["dsh"])


@router.get("/dsh/status")
async def dsh_status() -> dict:
    async with SessionLocal() as db:
        settings = await get_settings(db)
    return {
        "dsh": {
            **get_dsh_runtime().status(),
            **get_dsh_agent_runtime().status(),
        },
        "engine": resolve_agent_engine(settings),
        "channel": settings.get("dsh_channel", "stable"),
    }


@router.post("/dsh/check")
async def dsh_check(body: dict | None = Body(default=None)) -> dict:
    async with SessionLocal() as db:
        settings = await get_settings(db)
    channel = str((body or {}).get("channel") or settings.get("dsh_channel") or "stable")
    return {"update": await get_dsh_runtime().check_update(channel)}


@router.post("/dsh/update")
async def dsh_update(body: dict | None = Body(default=None)) -> dict:
    async with SessionLocal() as db:
        settings = await get_settings(db)
    channel = str((body or {}).get("channel") or settings.get("dsh_channel") or "stable")
    return get_dsh_runtime().start_update(channel)


@router.get("/dsh/sessions")
async def dsh_sessions(limit: int = 30) -> dict:
    return {"sessions": await get_dsh_agent_runtime().sessions(limit)}


@router.post("/dsh/reset")
async def dsh_reset(body: dict | None = Body(default=None)) -> dict:
    session_id = str((body or {}).get("session_id") or "main")
    await get_dsh_agent_runtime().reset_session(session_id)
    return {"ok": True, "session_id": session_id}


@router.post("/dsh/stop")
async def dsh_stop() -> dict:
    await get_dsh_agent_runtime().stop()
    return {"ok": True}
