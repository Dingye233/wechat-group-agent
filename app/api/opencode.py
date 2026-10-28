from __future__ import annotations

import asyncio
import hmac
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import SessionLocal
from app.services import assistant_tools, tool_registry
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


def _manifest_entry(name: str) -> dict[str, Any] | None:
    try:
        return tool_registry.get_entry(name)
    except Exception:
        return None


async def _run_script_tool(
    entry: dict[str, Any], args: dict[str, Any]
) -> dict[str, Any]:
    root = (Path(tool_registry.ROOT) / "data" / "opencode" / "tools").resolve()
    try:
        script = (root / str(entry.get("script") or "")).resolve()
        script.relative_to(root)
    except Exception as exc:
        raise ValueError("脚本路径非法") from exc
    if not script.is_file():
        raise ValueError(f"脚本不存在：{script}")
    suffix = script.suffix.lower()
    if suffix == ".py":
        command = [sys.executable, str(script)]
    elif suffix == ".ps1":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
    else:
        raise ValueError("脚本只支持 .py 或 .ps1")
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(assistant_tools.workspace_root()),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    payload = json.dumps(args or {}, ensure_ascii=False).encode("utf-8")
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(payload), timeout=120
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("脚本工具执行超过 120 秒")
    out = stdout.decode("utf-8", "replace").strip()
    err = stderr.decode("utf-8", "replace").strip()
    return {
        "exit_code": proc.returncode,
        "stdout": out[:20000],
        "stderr": err[:8000],
        "script": entry.get("script"),
    }


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


@router.post("/opencode/refresh-mcp")
async def opencode_refresh_mcp(request: Request) -> dict:
    """Schedule an MCP reconnect after the current tool result is returned."""
    supplied = request.headers.get("x-qunban-token", "")
    expected = tool_token()
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="无效的 OpenCode 工具令牌")

    runtime = _runtime()

    async def later() -> None:
        # Never cut an active agent turn.  A direct MCP tool entry only needs
        # to become available for the next turn; qunban_tool already resolves
        # newly created aliases/scripts immediately.
        deadline = asyncio.get_running_loop().time() + 300
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(f"{runtime.url}/session/status")
                statuses = response.json() if response.status_code == 200 else {}
                busy = any(
                    str((value or {}).get("type") or "") == "busy"
                    for value in statuses.values()
                    if isinstance(value, dict)
                )
            except Exception:
                busy = False
            if not busy:
                break
            await asyncio.sleep(1.0)
        result = await runtime.refresh_mcp()
        logger.info("OpenCode MCP refresh: %s", result)

    asyncio.create_task(later(), name="opencode-mcp-refresh")
    return {"ok": True, "scheduled": True, "wait_for_idle": True}


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
    if name.startswith(("keyboard_", "mouse_")) and not payload.get("allow_desktop"):
        return {
            "ok": False,
            "tool": name,
            "error": (
                "桌面键鼠工具默认不允许由 OpenCode 调用。"
                "发送微信消息请用 qunban_send_message；"
                "确需桌面操作时由用户在设置中显式开启。"
            ),
        }
    if name == "run_powershell":
        command = str((args or {}).get("command") or "").lower()
        blocked = (
            "sendkeys",
            "wechatauto",
            "wechat",
            "weixin",
            "微信",
            "sendmsg",
            "uiautomation",
        )
        if any(token in command for token in blocked):
            return {
                "ok": False,
                "tool": name,
                "error": (
                    "禁止用 PowerShell 操作微信窗口或模拟发送。"
                    "微信消息请用 qunban_send_message(group_id/contact_id)。"
                ),
            }

    async with SessionLocal() as db:
        settings = await get_settings(db)
    account_key = str((runtime.account or {}).get("wxid") or "")
    service = AssistantService(runtime.llm, runtime)
    original_name = name
    entry = _manifest_entry(name)
    if entry is not None:
        if not entry.get("enabled", True):
            return {"ok": False, "tool": original_name, "error": "工具已停用"}
        handler_type = str(entry.get("handler_type") or "native").lower()
        if handler_type == "script":
            if scope != "admin":
                return {
                    "ok": False,
                    "tool": original_name,
                    "error": "脚本工具只允许 admin 范围调用",
                }
            try:
                result = await _run_script_tool(entry, args)
            except Exception as exc:
                logger.warning("OpenCode 脚本工具 %s 失败：%s", original_name, exc)
                return {"ok": False, "tool": original_name, "error": str(exc)}
            if int(result.get("exit_code") or 0) != 0:
                return {
                    "ok": False,
                    "tool": original_name,
                    "error": result.get("stderr") or result.get("stdout") or "脚本执行失败",
                    "result": result,
                }
            return {
                "ok": True,
                "tool": original_name,
                "handler_type": "script",
                "result": result,
            }
        # Native alias: route to the underlying Qunban capability.
        name = str(entry.get("handler") or name)
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
        logger.warning("OpenCode 工具 %s 失败：%s", original_name, exc)
        return {"ok": False, "tool": original_name, "error": str(exc)}
    return {"ok": True, "tool": original_name, "result": result}
