from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import get_db
from app.schemas import SettingsUpdate
from app.services.llm import LLMError
from app.services.settings import get_settings, public_settings, update_settings

router = APIRouter(tags=["settings"])


@router.get("/settings")
async def read_settings(db: AsyncSession = Depends(get_db)) -> dict:
    data = await get_settings(db)
    return {"settings": public_settings(data)}


@router.put("/settings")
async def save_settings(
    payload: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        data = await update_settings(db, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    public = public_settings(data)
    await runtime.apply_read_filter()
    runtime.broadcast("settings.updated", {"llm_configured": public["llm_configured"]})
    return {"settings": public}


@router.post("/settings/test-llm")
async def test_llm(
    db: AsyncSession = Depends(get_db),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    data = await get_settings(db)
    if not data.get("llm_api_key"):
        raise HTTPException(status_code=400, detail="请先填写并保存 API Key")
    try:
        result = await runtime.llm.test(
            base_url=str(data.get("llm_base_url") or ""),
            api_key=str(data.get("llm_api_key") or ""),
            model=str(data.get("llm_model") or ""),
        )
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": result}
