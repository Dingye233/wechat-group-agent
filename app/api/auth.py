from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from app.adapters import adapter_catalog
from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.schemas import LoginRequest

router = APIRouter(tags=["auth"])


@router.get("/adapters")
async def adapters() -> dict:
    return {"adapters": adapter_catalog()}


@router.post("/login")
async def login(
    payload: LoginRequest, runtime: RuntimeManager = Depends(get_runtime)
) -> dict:
    try:
        data = await runtime.login(payload.adapter)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "status": data}


@router.post("/logout")
async def logout(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    await runtime.logout()
    return {"ok": True, "status": runtime.status_dict()}


@router.get("/login/qr")
async def login_qr(runtime: RuntimeManager = Depends(get_runtime)) -> Response:
    qr = runtime.get_qr()
    if qr is None:
        raise HTTPException(status_code=404, detail="暂无二维码，请先开始登录")
    image, mime, version = qr
    return Response(
        content=image,
        media_type=mime,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-QR-Version": str(version),
        },
    )


@router.get("/account")
async def account(runtime: RuntimeManager = Depends(get_runtime)) -> JSONResponse:
    return JSONResponse({"account": runtime.account, "state": runtime.state})
