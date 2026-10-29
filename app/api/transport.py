from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager

router = APIRouter(tags=["transport"])


@router.get("/transport/jobs")
async def transport_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    transport = getattr(runtime, "transport", None)
    if transport is None:
        return {"jobs": [], "stats": {}}
    return {"jobs": await transport.list_jobs(limit), "stats": await transport.stats()}


@router.get("/transport/jobs/{job_id}")
async def transport_job(
    job_id: int,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    transport = getattr(runtime, "transport", None)
    if transport is None:
        raise HTTPException(status_code=503, detail="transport 未启动")
    job = await transport.job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="发送任务不存在")
    return {"job": job}
