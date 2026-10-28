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
    updates = payload.model_dump(exclude_unset=True)
    try:
        data = await update_settings(db, updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    public = public_settings(data)
    await runtime.apply_read_filter()
    hotkey_result = runtime.apply_hotkey(str(data.get("takeover_hotkey") or ""))
    if (
        "takeover_hotkey" in updates
        and not hotkey_result.get("ok")
        and hotkey_result.get("restored")
    ):
        # Do not persist a hotkey that Windows refused to register (usually
        # because another app already owns it).  Keep the previous one.
        data = await update_settings(
            db, {"takeover_hotkey": hotkey_result["restored"]}
        )
        public = public_settings(data)
    runtime.broadcast(
        "settings.updated",
        {
            "llm_configured": public["llm_configured"],
            "takeover_hotkey": hotkey_result,
        },
    )
    return {"settings": public, "hotkey": hotkey_result}


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
