from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.services import wechatauto_events

router = APIRouter(tags=["wechatauto"])


@router.get("/wechatauto/status")
async def wechatauto_status(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    transport = getattr(runtime, "transport", None)
    return {
        "online": runtime.is_online,
        "state": runtime.state,
        "adapter": runtime.adapter_id,
        "account": runtime.account or {},
        "transport": {
            "running": bool(getattr(transport, "running", False)),
            "stats": await transport.stats() if transport else {},
        },
        "events": len(wechatauto_events.recent(500)),
    }


@router.get("/wechatauto/events")
async def wechatauto_event_list(
    limit: int = Query(default=100, ge=1, le=500),
    type: str = Query(default="", alias="type"),
) -> dict:
    return {"events": wechatauto_events.recent(limit, event_type=type)}


@router.post("/wechatauto/outbound")
async def wechatauto_outbound(
    body: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    transport = getattr(runtime, "transport", None)
    if transport is None or not getattr(transport, "running", False):
        raise HTTPException(status_code=503, detail="wechatauto transport 未启动")
    try:
        group_id = int(body.get("group_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="group_id 无效")
    if group_id <= 0:
        raise HTTPException(status_code=400, detail="group_id 必填")
    job = await transport.submit(
        group_id=group_id,
        text=str(body.get("text") or ""),
        sticker_id=int(body["sticker_id"]) if body.get("sticker_id") else None,
        files=[str(item) for item in body.get("files") or []],
        meta=dict(body.get("meta") or {}),
        reuse_chat=bool(body.get("reuse_chat")),
        idempotency_key=str(body.get("idempotency_key") or ""),
    )
    if bool(body.get("wait")):
        job = await transport.wait(int(job["id"]), timeout=180.0)
    return {"job": job}
