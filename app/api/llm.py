from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.llm_usage import summary

router = APIRouter(tags=["llm"])


@router.get("/llm/usage")
async def llm_usage(hours: int = Query(24, ge=1, le=720)) -> dict:
    """Prompt-cache hit rate and token usage, grouped by call site."""
    return await summary(hours)
