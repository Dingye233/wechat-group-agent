from __future__ import annotations

from fastapi import APIRouter

from app.services import update_check

router = APIRouter(tags=["update"])


@router.get("/update/status")
async def update_status() -> dict:
    return {"update": update_check.status()}


@router.post("/update/check")
async def update_check_now() -> dict:
    return {"update": await update_check.check_update()}
