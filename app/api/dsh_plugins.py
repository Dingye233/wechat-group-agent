from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, HTTPException

from app.services import dsh_plugins

router = APIRouter(tags=["dsh"])


@router.get("/dsh/plugins")
async def dsh_plugins_catalog() -> dict:
    try:
        return await asyncio.to_thread(dsh_plugins.catalog)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/dsh/plugins/import")
async def dsh_plugins_import(body: dict | None = Body(default=None)) -> dict:
    payload = body or {}
    try:
        job = dsh_plugins.start_import(
            str(payload.get("spec") or ""),
            enabled=bool(payload.get("enabled", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job": job}


@router.post("/dsh/plugins/create")
async def dsh_plugins_create(body: dict | None = Body(default=None)) -> dict:
    payload = body or {}
    try:
        job = dsh_plugins.start_create(
            str(payload.get("name") or ""),
            str(payload.get("description") or ""),
            str(payload.get("requirements") or ""),
            use_agent=bool(payload.get("use_agent", True)),
            enabled=bool(payload.get("enabled", True)),
            private=bool(payload.get("private", True)),
            group=bool(payload.get("group", False)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job": job}


@router.get("/dsh/plugins/jobs/{job_id}")
async def dsh_plugins_job(job_id: str) -> dict:
    job = dsh_plugins.job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return {"job": job}


@router.post("/dsh/plugins/install")
async def dsh_plugins_install(body: dict | None = Body(default=None)) -> dict:
    payload = body or {}
    try:
        return await asyncio.to_thread(
            dsh_plugins.install_existing_transactional,
            str(payload.get("path") or ""),
            enabled=bool(payload.get("enabled", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/dsh/plugins/{plugin_id}/rollback")
async def dsh_plugins_rollback(plugin_id: str) -> dict:
    try:
        return await asyncio.to_thread(dsh_plugins.rollback_plugin, plugin_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/dsh/plugins/{plugin_id}/promote")
async def dsh_plugins_promote(
    plugin_id: str, body: dict | None = Body(default=None)
) -> dict:
    payload = body or {}
    try:
        return await asyncio.to_thread(
            dsh_plugins.promote_plugin,
            plugin_id,
            private=bool(payload.get("private", True)),
            group=bool(payload.get("group", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dsh/plugins/tool-channels")
async def dsh_plugins_tool_channels(body: dict | None = Body(default=None)) -> dict:
    payload = body or {}
    try:
        return dsh_plugins.set_tool_channels(
            str(payload.get("id") or ""),
            str(payload.get("tool") or ""),
            private=bool(payload.get("private", True)),
            group=bool(payload.get("group", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dsh/plugins/channels")
async def dsh_plugins_channels(body: dict | None = Body(default=None)) -> dict:
    payload = body or {}
    try:
        return dsh_plugins.set_channels(
            str(payload.get("id") or ""),
            private=bool(payload.get("private", True)),
            group=bool(payload.get("group", False)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dsh/plugins/toggle")
async def dsh_plugins_toggle(body: dict | None = Body(default=None)) -> dict:
    payload = body or {}
    try:
        return dsh_plugins.set_enabled(
            str(payload.get("id") or ""),
            bool(payload.get("enabled", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
