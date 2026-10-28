from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import SessionLocal
from app.services import tool_registry
from app.services.assistant import AssistantService
from app.services.capabilities import list_capabilities
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
    capabilities = await list_capabilities()
    qunban_projects = {
        "mcps": [
            {
                "name": item["name"],
                "key": item["key"],
                "description": item.get("description", ""),
                "risk": item.get("risk"),
            }
            for item in capabilities
            if item.get("type") == "mcp"
        ],
        "skills": [
            {
                "name": item["name"],
                "key": item["key"],
                "description": item.get("description", ""),
                "risk": item.get("risk"),
            }
            for item in capabilities
            if item.get("type") == "skill"
        ],
        "apis": [
            {
                "name": item["name"],
                "key": item["key"],
                "description": item.get("description", ""),
                "risk": item.get("risk"),
            }
            for item in capabilities
            if item.get("type") == "api"
        ],
    }
    mcp_tools: list[dict[str, Any]] = []
    seen_mcp: set[str] = set()
    for item in tool_registry.list_entries():
        name = str(item.get("name") or "").strip()
        if not name or name in seen_mcp:
            continue
        seen_mcp.add(name)
        mcp_tools.append(
            {
                "id": f"qunban_{name}",
                "name": name,
                "description": str(item.get("description") or "群伴工具"),
                "args_example": item.get("args_example") or {},
                "handler": item.get("handler") or name,
                "enabled": bool(item.get("enabled", True)),
                "managed_by": "opencode",
                "source": "qunban-mcp",
            }
        )
    for name, description in (
        ("tool", "通用群伴工具网关（name + args）"),
        ("capabilities", "列出群伴可用工具和示例参数"),
        ("tool_admin", "OpenCode 管理群伴工具：增删/停用/改描述/重载"),
        ("list_groups", "列出微信群"),
        ("read_group", "读取群消息"),
        ("send_message", "发送群聊/私聊消息"),
        ("bili_knowledge", "检索 B站知识库"),
        ("memory_save", "保存长期记忆"),
        ("memory_search", "搜索长期记忆"),
        ("run_mcp", "运行群伴 MCP 项目"),
        ("run_skill", "运行群伴技能"),
        ("call_interface", "调用群伴已登记接口"),
    ):
        if name in seen_mcp:
            continue
        seen_mcp.add(name)
        mcp_tools.append(
            {
                "id": f"qunban_{name}",
                "name": name,
                "description": description,
                "managed_by": "opencode",
                "source": "qunban-mcp",
            }
        )
    return {
        "tools": tools,
        "mcp_tools": mcp_tools,
        "qunban_projects": qunban_projects,
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


@router.get("/opencode/tools/manifest")
async def opencode_tools_manifest() -> dict:
    return {
        "manifest": tool_registry.load_manifest(),
        "path": str(tool_registry.MANIFEST_PATH),
    }


@router.post("/opencode/tools/admin")
async def opencode_tools_admin(
    request: Request,
    payload: dict = Body(...),
) -> dict:
    """OpenCode-managed tool definitions (called by the MCP tool_admin tool)."""
    supplied = request.headers.get("x-qunban-token", "")
    expected = tool_token()
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="无效的 OpenCode 工具令牌")
    action = str(payload.get("action") or "list")
    try:
        result = tool_registry.admin_action(action, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "action": action, **result}


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
