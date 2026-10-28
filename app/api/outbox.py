from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager

router = APIRouter(tags=["outbox"])


@router.get("/outbox")
async def list_outbox(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    return {"messages": await runtime.list_failed_sends()}


@router.post("/outbox/retry")
async def retry_outbox(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    result = await runtime.retry_failed_sends()
    if not result.get("ok", False):
        raise HTTPException(status_code=400, detail=result.get("error") or "重发失败")
    return result


@router.post("/outbox/{message_id}/retry")
async def retry_outbox_message(
    message_id: int,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    result = await runtime.retry_failed_sends(message_id=message_id, limit=1)
    if not result.get("ok", False):
        raise HTTPException(status_code=400, detail=result.get("error") or "重发失败")
    return result
