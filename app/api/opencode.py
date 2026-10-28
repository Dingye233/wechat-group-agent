from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import SessionLocal
from app.services.assistant import AssistantService
from app.services.opencode_agent import (
    OpenCodeError,
    get_opencode_runtime,
    tool_token,
)
from app.services.settings import get_settings

logger = logging.getLogger("opencode.api")
router = APIRouter(tags=["opencode"])


def _runtime():
    return get_opencode_runtime()


@router.get("/opencode/status")
async def opencode_status() -> dict:
    return {"opencode": await _runtime().status()}


@router.get("/opencode/tools")
async def opencode_tools() -> dict:
    runtime = _runtime()
    async with SessionLocal() as db:
        settings = await get_settings(db)
    start_error = ""
    if not runtime.running and settings.get("opencode_enabled", True):
        try:
            await runtime.ensure_started(settings)
        except Exception as exc:
            start_error = str(exc)
            logger.warning("OpenCode 启动失败（工具面板）：%s", exc)
    tools = await runtime.tools(settings)
    plugins = await runtime.plugins()
    mcp = await runtime.mcp_status()
    mcp_tools = [
        {"id": "qunban_tool", "description": "通用群伴工具网关（name + args）"},
        {"id": "qunban_capabilities", "description": "列出群伴可用工具"},
        {"id": "qunban_list_groups", "description": "列出微信群"},
        {"id": "qunban_read_group", "description": "读取群消息"},
        {"id": "qunban_send_message", "description": "发送群聊/私聊消息"},
        {"id": "qunban_bili_search", "description": "搜索 B站视频"},
        {"id": "qunban_bili_knowledge", "description": "检索 B站知识库"},
        {"id": "qunban_memory_save", "description": "保存长期记忆"},
        {"id": "qunban_memory_search", "description": "搜索长期记忆"},
    ]
    return {
        "tools": tools,
        "mcp_tools": mcp_tools,
        "plugins": plugins,
        "mcp": mcp,
        "running": runtime.running,
        "version": (await runtime.status()).get("version", ""),
        "source": "https://github.com/anomalyco/opencode",
        "error": start_error,
    }


@router.post("/opencode/restart")
async def opencode_restart() -> dict:
    runtime = _runtime()
    async with SessionLocal() as db:
        settings = await get_settings(db)
    try:
        await runtime.restart(settings)
    except OpenCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "status": await runtime.status()}


@router.post("/opencode/reset")
async def opencode_reset(payload: dict = Body(default={})) -> dict:
    session_id = str(payload.get("session_id") or "main")[:100]
    await _runtime().reset_session(session_id)
    return {"ok": True, "session_id": session_id}


@router.post("/opencode/tool")
async def opencode_tool(
    request: Request,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    """Internal tool gateway called by the Qunban MCP process."""
    supplied = request.headers.get("x-qunban-token", "")
    expected = tool_token()
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="无效的 OpenCode 工具令牌")

    name = str(payload.get("name") or "").strip()
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    scope = str(payload.get("scope") or "admin")
    if scope not in {"admin", "assistant", "group"}:
        scope = "admin"
    if not name:
        raise HTTPException(status_code=400, detail="工具名不能为空")

    async with SessionLocal() as db:
        settings = await get_settings(db)
    account_key = str((runtime.account or {}).get("wxid") or "")
    service = AssistantService(runtime.llm, runtime)
    try:
        result = await service._tool_result(
            name,
            args,
            session_id="opencode",
            settings=settings,
            account_key=account_key,
            scope=scope,
        )
    except Exception as exc:
        logger.warning("OpenCode 工具 %s 失败：%s", name, exc)
        return {"ok": False, "tool": name, "error": str(exc)}
    return {"ok": True, "tool": name, "result": result}
