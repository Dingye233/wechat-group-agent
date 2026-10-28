"""System/management tool component.

This module owns the generic tool gateway, capability discovery, MCP project
execution and the OpenCode-managed native-tool manifest.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from app.tools.common import (
    CURATED_TOOL_NAMES,
    admin_call,
    call,
    call_json,
    refresh_mcp,
)

_server: Any = None
_MANIFEST_TOOL_NAMES: set[str] = set()


def _make_tool(handler: str):
    async def run(args: dict[str, Any] | None = None) -> str:
        return await call(handler, args or {})

    run.__name__ = f"qunban_{handler}"
    return run


def _entry_description(entry: dict[str, Any]) -> str:
    description = str(entry.get("description") or "群伴原生工具")
    example = entry.get("args_example") or {}
    try:
        example_text = json.dumps(example, ensure_ascii=False)
    except Exception:
        example_text = "{}"
    return (
        f"{description} 参数示例：{example_text}。"
        "参数放在 args 对象里；工具清单由 OpenCode 的 tool_admin 管理。"
    )


def _add_manifest_tool(entry: dict[str, Any]) -> None:
    name = str(entry.get("name") or "").strip()
    handler = str(entry.get("handler") or name).strip()
    if not name or not handler or name in CURATED_TOOL_NAMES:
        return
    if name.startswith(("keyboard_", "mouse_")) and not entry.get("allow_desktop"):
        return
    try:
        _server.remove_tool(name)
    except Exception:
        pass
    _server.add_tool(
        _make_tool(name),
        name=name,
        description=_entry_description(entry),
    )
    _MANIFEST_TOOL_NAMES.add(name)


def reload_manifest() -> list[str]:
    """Reload the OpenCode-managed native tool manifest."""
    from app.services import tool_registry

    entries = tool_registry.list_entries()
    wanted = {
        str(entry.get("name") or "").strip()
        for entry in entries
        if entry.get("enabled", True)
        and str(entry.get("name") or "").strip() not in CURATED_TOOL_NAMES
    }
    for old in list(_MANIFEST_TOOL_NAMES - wanted):
        try:
            _server.remove_tool(old)
        except Exception:
            pass
        _MANIFEST_TOOL_NAMES.discard(old)
    for entry in entries:
        if entry.get("enabled", True):
            _add_manifest_tool(entry)
    return sorted(wanted)


def register(server: Any) -> None:
    global _server
    _server = server

    @server.tool(
        name="tool",
        description=(
            "通用群伴工具入口。name 是工具名，args 是工具参数对象。"
            "不确定工具名时先调用 capabilities；也可以直接调用具体工具。"
        ),
    )
    async def tool(name: str, args: dict[str, Any] | None = None) -> str:
        if not str(name or "").strip():
            return json.dumps(
                {"ok": False, "error": "name 不能为空"}, ensure_ascii=False
            )
        return await call(name, args)

    @server.tool(
        name="capabilities",
        description="列出群伴当前可用工具的名称、类型、风险、说明和示例参数。",
    )
    async def capabilities() -> str:
        data = await call_json("list_capabilities", {})
        rows = data if isinstance(data, list) else (data or {}).get("capabilities", [])
        examples: dict[str, Any] = {}
        try:
            from app.services.assistant_tools import tool_catalog

            for item in tool_catalog():
                examples.setdefault(
                    str(item.get("name") or ""),
                    item.get("args") or {},
                )
        except Exception:
            pass
        compact = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("key") or "")
            key = str(row.get("key") or "")
            if key.startswith("tool:"):
                name = key.split(":", 1)[1]
            entry = {
                "name": name,
                "key": key,
                "type": row.get("type"),
                "risk": row.get("risk"),
                "description": str(row.get("description") or "")[:200],
                "args_example": examples.get(name),
            }
            if key.startswith("mcp:"):
                entry["call_tool"] = "qunban_run_mcp"
                entry["call_args"] = {"name": key.split(":", 1)[1]}
            elif key.startswith("skill:"):
                entry["call_tool"] = "qunban_run_skill"
                entry["call_args"] = {"name": key.split(":", 1)[1]}
            elif key.startswith("api:"):
                entry["call_tool"] = "qunban_call_interface"
                entry["call_args"] = {"name": key.split(":", 1)[1]}
            elif key.startswith("tool:"):
                entry["call_tool"] = f"qunban_{name}"
                example = examples.get(name) or {}
                if name == "run_mcp":
                    example = {"name": "screen_control_mcp", "command": "..."}
                elif name == "call_interface":
                    example = {"name": "api-name", "args": {}}
                elif name == "run_skill":
                    example = {"name": "skill-name", "args": ""}
                entry["call_args"] = (
                    example
                    if name in {"bili_search", "run_mcp", "run_skill", "call_interface"}
                    else {"args": example}
                )
            compact.append(entry)
        return json.dumps(compact[:160], ensure_ascii=False)

    @server.tool(
        name="run_mcp",
        description=(
            "运行已启用的群伴 MCP 项目。name 是项目名；command 可选，"
            "留空则使用 manifest 里的 run；timeout 秒。"
        ),
    )
    async def run_mcp(
        name: str,
        command: str = "",
        timeout: int = 120,
        admin: bool = False,
    ) -> str:
        return await call(
            "run_mcp",
            {
                "name": name,
                "command": command,
                "timeout": int(timeout),
                "admin": bool(admin),
            },
        )

    @server.tool(
        name="run_skill",
        description="运行群伴技能。name 是技能名，args 是传给技能脚本的参数。",
    )
    async def run_skill(name: str, args: str = "") -> str:
        return await call("run_skill", {"name": name, "args": args})

    @server.tool(
        name="call_interface",
        description="调用群伴已登记的 HTTP 接口。name 是接口名，args 是请求参数。",
    )
    async def call_interface(
        name: str, args: dict[str, Any] | None = None
    ) -> str:
        return await call(
            "call_interface", {"name": name, "args": args or {}}
        )

    @server.tool(
        name="tool_admin",
        description=(
            "管理群伴工具清单。action 支持 list/get/upsert/remove/enable/disable/sync/reload/refresh。"
            "upsert 时在 tool 对象里传 name、description、handler_type、handler/script、args_example、enabled。"
            "handler_type=native 时 handler 引用群伴原生工具；handler_type=script 时 script 指向 "
            "data/opencode/tools 下的 .py/.ps1 脚本，脚本从 stdin 读 JSON 参数并输出结果。"
            "upsert 后当前任务内用 qunban_tool(name=..., args=...) 立即调用；直接入口 "
            "qunban_<name> 会在本轮空闲后自动刷新 MCP，下一轮可用。"
        ),
    )
    async def tool_admin(
        action: str,
        name: str = "",
        tool: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {"action": action}
        if name:
            payload["name"] = name
        if tool:
            payload["tool"] = tool
        if action.strip().lower() == "refresh":
            return json.dumps(await refresh_mcp(), ensure_ascii=False, default=str)
        result = await admin_call(payload)
        if action.strip().lower() in {
            "upsert",
            "remove",
            "enable",
            "disable",
            "sync",
            "reload",
        }:
            try:
                names = reload_manifest()
                result["active_manifest_tools"] = len(names)
            except Exception as exc:
                result["reload_error"] = str(exc)
            result["direct_tool_note"] = (
                '当前任务内请用 qunban_tool(name="工具名", args={...}) 立即调用；'
                "直接入口会在本轮空闲后自动刷新 MCP，下一轮可用。"
            )
            await refresh_mcp()
        return json.dumps(result, ensure_ascii=False, default=str)

    _register_manifest_tools()


def _register_manifest_tools() -> None:
    try:
        from app.services import tool_registry

        tool_registry.load_manifest()
    except Exception as exc:
        print(f"qunban tool manifest unavailable: {exc}", file=sys.stderr)
        return
    reload_manifest()
