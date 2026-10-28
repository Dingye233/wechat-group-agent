from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import SessionLocal
from app.models import AgentMemory, AgentTask, AssistantMessage, Group, GroupConfig
from app.services import assistant_tools, mem0_memory
from app.services.assistant import (
    AssistantService,
    cancel_session,
    clear_history,
    create_task,
    history,
    is_session_active,
    list_tasks,
    update_task,
)
from app.services.capabilities import list_capabilities, set_capability, sync_capabilities
from app.services.memory import memory_dict, save_memory, search_memories, summarize_group_memories
from app.services.settings import get_settings

router = APIRouter(tags=["assistant"])


def _service(runtime: RuntimeManager) -> AssistantService:
    return AssistantService(runtime.llm, runtime)


def _tool_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/assistant/status")
async def assistant_status(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    async with SessionLocal() as db:
        settings = await get_settings(db)
        memories = (
            await db.execute(
                select(func.count(AgentMemory.id)).where(AgentMemory.active.is_(True))
            )
        ).scalar() or 0
        tasks = (await db.execute(select(func.count(AgentTask.id)))).scalar() or 0
        messages = (await db.execute(select(func.count(AssistantMessage.id)))).scalar() or 0
    mem0_memory.warmup(settings)
    root = assistant_tools.workspace_root()
    return {
        "admin": assistant_tools.is_admin(),
        "workspace": str(root),
        "skills": len(assistant_tools.list_skills()),
        "memories": int(memories),
        "tasks": int(tasks),
        "messages": int(messages),
        "model": settings.get("llm_model", ""),
        "max_steps": int(settings.get("assistant_max_steps") or 0),
        "assistant_active": is_session_active("main"),
        "memory": mem0_memory.status(settings),
        "llm_configured": bool(settings.get("llm_api_key")),
        "memory_enabled": bool(settings.get("memory_enabled", True)),
        "service_online": runtime.is_online,
        "account": runtime.account,
    }


@router.get("/capabilities")
async def capabilities_list() -> dict:
    return {"capabilities": await list_capabilities()}


@router.post("/capabilities/sync")
async def capabilities_sync() -> dict:
    await sync_capabilities(force=True)
    return {"capabilities": await list_capabilities()}


@router.patch("/capabilities")
async def capabilities_toggle(payload: dict = Body(...)) -> dict:
    key = str(payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key 不能为空")
    try:
        result = await set_capability(
            key,
            assistant=payload.get("enabled_assistant")
            if "enabled_assistant" in payload
            else None,
            group=payload.get("enabled_group")
            if "enabled_group" in payload
            else None,
        )
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/assistant/admin/restart")
async def assistant_admin_restart() -> dict:
    """Ask Windows for an elevated service restart via start-admin.ps1."""
    if assistant_tools.is_admin():
        return {"ok": True, "message": "当前已经是管理员权限"}
    script = Path(__file__).resolve().parents[2] / "start-admin.ps1"
    if not script.is_file():
        raise HTTPException(status_code=404, detail="start-admin.ps1 不存在")
    command = (
        "Start-Process -FilePath 'powershell.exe' -Verb RunAs "
        "-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"
        f"'{str(script)}'"
    )
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动提权失败：{exc}") from exc
    return {
        "ok": True,
        "message": "已请求 UAC 提权重启，请在弹窗中确认；服务重启后本页会自动恢复",
    }


@router.post("/assistant/upload")
async def assistant_upload(file: UploadFile = File(...)) -> dict:
    safe = "".join(ch for ch in (file.filename or "image") if ch.isalnum() or ch in "._- ").strip()
    safe = safe[:100] or "image.png"
    target_dir = Path("data") / "media" / "assistant" / "uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{int(time.time())}_{safe}"
    target.write_bytes(await file.read())
    try:
        ocr = await asyncio.to_thread(assistant_tools.read_image, str(target))
    except Exception as exc:
        ocr = {"error": str(exc), "text": ""}
    return {
        "path": str(target),
        "url": f"/media/assistant/uploads/{target.name}",
        "ocr": ocr,
    }


@router.post("/assistant/chat")
async def assistant_chat(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")
    attachment = str(payload.get("attachment_path") or "").strip()
    kind = "text"
    media_path = ""
    if attachment:
        try:
            attachment_path = assistant_tools.resolve_path(attachment)
            image = await asyncio.to_thread(
                assistant_tools.read_image, str(attachment_path)
            )
            kind = "image"
            media_path = str(attachment_path)
            message = (
                f"用户上传了一张图片：{attachment_path}\n"
                f"OCR 文本：\n{image.get('text') or '（未识别到文字）'}\n\n"
                f"用户消息：{message}"
            )
        except Exception as exc:
            message = f"用户上传了图片但读取失败（{exc}）。用户消息：{message}"
    progress_id = str(payload.get("progress_id") or "")[:64]
    session_id = str(payload.get("session_id") or "main")[:64]

    async def emit_progress(event: dict) -> None:
        if not progress_id:
            return
        runtime.broadcast(
            "assistant.progress",
            {"request_id": progress_id, **event},
        )

    try:
        result = await _service(runtime).chat(
            message,
            session_id=session_id,
            kind=kind,
            media_path=media_path,
            channel="dashboard",
            progress=emit_progress if progress_id else None,
        )
        if result.get("queued"):
            return {
                "ok": True,
                "queued": True,
                "reply": "已插入当前任务，Agent 会在下一步看到这条新要求。",
            }
        return {"ok": True, **result}
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/assistant/stop")
async def assistant_stop(payload: dict = Body(default={})) -> dict:
    session_id = str(payload.get("session_id") or "main")[:64]
    if cancel_session(session_id):
        return {
            "ok": True,
            "stopped": True,
            "message": "已发送停止请求，当前工具返回后就会停下。",
        }
    return {
        "ok": True,
        "stopped": False,
        "message": "当前没有正在执行的任务。",
    }


@router.get("/assistant/history")
async def assistant_history(
    session_id: str = Query(default="main"),
    limit: int = Query(default=80, ge=1, le=300),
) -> dict:
    return {"messages": await history(session_id, limit)}


@router.delete("/assistant/history")
async def assistant_clear(
    session_id: str = Query(default="main"),
) -> dict:
    deleted = await clear_history(session_id)
    try:
        from app.services.opencode_agent import get_opencode_runtime

        await get_opencode_runtime().reset_session(session_id)
    except Exception:
        pass
    return {"deleted": deleted}


# ------------------------------------------------------------------- files
@router.get("/assistant/files")
async def assistant_files(
    path: str = Query(default="."),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await asyncio.to_thread(assistant_tools.list_dir, path, 300)
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.get("/assistant/file")
async def assistant_read_file(
    path: str = Query(...),
    max_chars: int = Query(default=40000, ge=100, le=200000),
) -> dict:
    try:
        return await asyncio.to_thread(assistant_tools.read_file, path, max_chars)
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.put("/assistant/file")
async def assistant_write_file(payload: dict = Body(...)) -> dict:
    try:
        return await asyncio.to_thread(
            assistant_tools.write_file,
            str(payload.get("path") or ""),
            str(payload.get("content") or ""),
            bool(payload.get("append")),
            bool(payload.get("base64_data")),
        )
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.post("/assistant/mkdir")
async def assistant_mkdir(payload: dict = Body(...)) -> dict:
    try:
        return await asyncio.to_thread(
            assistant_tools.make_dir, str(payload.get("path") or "")
        )
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.delete("/assistant/file")
async def assistant_delete(
    path: str = Query(...),
    recursive: bool = Query(default=False),
) -> dict:
    try:
        return await asyncio.to_thread(assistant_tools.delete_path, path, recursive)
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.post("/assistant/move")
async def assistant_move(payload: dict = Body(...)) -> dict:
    try:
        return await asyncio.to_thread(
            assistant_tools.move_path,
            str(payload.get("src") or ""),
            str(payload.get("dst") or ""),
        )
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.post("/assistant/search")
async def assistant_search(payload: dict = Body(...)) -> dict:
    try:
        return await asyncio.to_thread(
            assistant_tools.search_files,
            str(payload.get("pattern") or "*"),
            str(payload.get("root") or "."),
            int(payload.get("limit") or 200),
        )
    except Exception as exc:
        raise _tool_error(exc) from exc


# ------------------------------------------------------------------- shell
@router.post("/assistant/shell")
async def assistant_shell(payload: dict = Body(...)) -> dict:
    try:
        return await asyncio.to_thread(
            assistant_tools.run_powershell,
            str(payload.get("command") or ""),
            payload.get("cwd"),
            float(payload.get("timeout") or 60),
            bool(payload.get("admin")),
        )
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.post("/assistant/web")
async def assistant_web(payload: dict = Body(...)) -> dict:
    try:
        return await assistant_tools.web_request(
            str(payload.get("method") or "GET"),
            str(payload.get("url") or ""),
            payload.get("headers") if isinstance(payload.get("headers"), dict) else None,
            payload.get("json") if "json" in payload else None,
            str(payload.get("body") or ""),
            float(payload.get("timeout") or 30),
        )
    except Exception as exc:
        raise _tool_error(exc) from exc


# ------------------------------------------------------------------ skills
@router.get("/assistant/skills")
async def assistant_skills() -> dict:
    return {"skills": assistant_tools.list_skills()}


@router.get("/assistant/skills/{name}")
async def assistant_skill(name: str) -> dict:
    try:
        return await asyncio.to_thread(assistant_tools.read_skill, name)
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.put("/assistant/skills/{name}")
async def assistant_write_skill(name: str, payload: dict = Body(...)) -> dict:
    try:
        return await asyncio.to_thread(
            assistant_tools.write_skill,
            name,
            str(payload.get("content") or ""),
            payload.get("files") if isinstance(payload.get("files"), dict) else {},
        )
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.delete("/assistant/skills/{name}")
async def assistant_delete_skill(name: str) -> dict:
    try:
        return await asyncio.to_thread(assistant_tools.delete_skill, name)
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.post("/assistant/skills/{name}/run")
async def assistant_run_skill(name: str, payload: dict = Body(default={})) -> dict:
    try:
        return await asyncio.to_thread(
            assistant_tools.run_skill,
            name,
            str(payload.get("args") or ""),
            float(payload.get("timeout") or 120),
        )
    except Exception as exc:
        raise _tool_error(exc) from exc


# ------------------------------------------------------------------- tasks
@router.get("/assistant/tasks")
async def assistant_tasks(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return {"tasks": await list_tasks(limit)}


@router.post("/assistant/tasks")
async def assistant_create_task(payload: dict = Body(...)) -> dict:
    try:
        return {"task": await create_task(
            str(payload.get("title") or ""),
            str(payload.get("detail") or ""),
            str(payload.get("status") or "pending"),
        )}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/assistant/tasks/{task_id}")
async def assistant_update_task(task_id: int, payload: dict = Body(...)) -> dict:
    try:
        return {"task": await update_task(
            task_id,
            status=str(payload.get("status") or ""),
            result=str(payload.get("result") or ""),
            detail=str(payload.get("detail") or ""),
        )}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ----------------------------------------------------------------- memory
@router.post("/assistant/memories/forget")
async def assistant_forget_memories(
    payload: dict = Body(default={}),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    async with SessionLocal() as db:
        settings = await get_settings(db)
    days = int(payload.get("days") or settings.get("memory_forgetting_days") or 180)
    dry_run = bool(payload.get("dry_run", False))
    from app.services.memory import forget_sqlite_memories

    mem0_result = await mem0_memory.forget_old(
        settings,
        account_key=runtime.account_key,
        days=days,
        dry_run=dry_run,
    )
    sqlite_result = await forget_sqlite_memories(
        days=days,
        account_key=runtime.account_key,
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "deleted": int(mem0_result.get("deleted") or 0)
        + int(sqlite_result.get("deleted") or 0),
        "scanned": int(mem0_result.get("scanned") or 0)
        + int(sqlite_result.get("candidates") or 0),
        "days": days,
        "dry_run": dry_run,
        "mem0": mem0_result,
        "sqlite": sqlite_result,
    }


@router.get("/assistant/memories")
async def assistant_memories(
    q: str = Query(default=""),
    group_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    account_key = str((runtime.account or {}).get("wxid") or "")
    return {
        "memories": await search_memories(
            query=q,
            group_id=group_id,
            account_key=account_key,
            limit=limit,
        )
    }


@router.post("/assistant/memories")
async def assistant_save_memory(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        memory = await save_memory(
            group_id=int(payload["group_id"]) if payload.get("group_id") else None,
            account_key=str((runtime.account or {}).get("wxid") or ""),
            scope=str(payload.get("scope") or ("group" if payload.get("group_id") else "global")),
            category=str(payload.get("category") or "general"),
            title=str(payload.get("title") or ""),
            content=str(payload.get("content") or ""),
            importance=int(payload.get("importance") or 3),
            source="manual",
        )
        return {"memory": memory_dict(memory)}
    except Exception as exc:
        raise _tool_error(exc) from exc


@router.delete("/assistant/memories/{memory_id}")
async def assistant_delete_memory(memory_id: int) -> dict:
    async with SessionLocal() as db:
        row = await db.get(AgentMemory, memory_id)
        if row is None:
            raise HTTPException(status_code=404, detail="记忆不存在")
        row.active = False
        await db.commit()
    return {"deleted": True, "id": memory_id}


@router.post("/assistant/memories/summarize")
async def assistant_summarize_memory(
    payload: dict = Body(default={}),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    group_id = payload.get("group_id")
    async with SessionLocal() as db:
        settings = await get_settings(db)
    if group_id:
        results = [
            await summarize_group_memories(
                runtime.llm, int(group_id), settings, force=bool(payload.get("force"))
            )
        ]
    else:
        async with SessionLocal() as db:
            statement = (
                select(Group.id)
                .join(GroupConfig, GroupConfig.group_id == Group.id)
                .where(GroupConfig.read_enabled.is_(True))
            )
            if runtime.account_key:
                statement = statement.where(Group.account_key == runtime.account_key)
            ids = [row[0] for row in (await db.execute(statement)).all()]
        results = [
            await summarize_group_memories(runtime.llm, int(gid), settings, force=True)
            for gid in ids
        ]
    return {"results": results}
