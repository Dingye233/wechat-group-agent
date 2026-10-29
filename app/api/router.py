from __future__ import annotations

from fastapi import APIRouter, Body

from app.services import agent_router

router = APIRouter(tags=["router"])


@router.post("/router/classify")
async def classify(payload: dict = Body(...)) -> dict:
    text = str(payload.get("text") or "")
    scope = str(payload.get("scope") or "group")
    has_tools = bool(payload.get("has_tools", True))
    result = (
        agent_router.classify_private(text, has_tools=has_tools)
        if scope == "private"
        else agent_router.classify_group(text, has_tools=has_tools)
    )
    return {"route": result.as_dict(), "scope": scope}
