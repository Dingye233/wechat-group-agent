"""Qunban tools exposed to the official OpenCode server over MCP.

OpenCode starts this process as a local MCP server.  Every tool call is
forwarded to the running Qunban FastAPI process so the existing capability
gate, permissions and audit trail stay authoritative.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API_BASE = os.environ.get("QUNBAN_API_BASE", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("QUNBAN_OPENCODE_TOKEN", "")
SCOPE = os.environ.get("QUNBAN_SCOPE", "admin")

server = MCPServer(
    "qunban",
    instructions=(
        "群伴的微信、B站、记忆、文件、终端和技能工具。"
        "先用 qunban_capabilities 查看可用工具名，再调用 qunban_tool。"
        "qunban_tool 的参数是 name（工具名）和 args（该工具的参数对象）。"
    ),
)


async def _call_json(name: str, args: dict[str, Any] | None = None) -> Any:
    payload = {
        "name": str(name or "").strip(),
        "args": args if isinstance(args, dict) else {},
        "scope": SCOPE,
    }
    headers = {"x-qunban-token": TOKEN}
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{API_BASE}/api/opencode/tool", json=payload, headers=headers
            )
        if response.status_code >= 400:
            return {"ok": False, "error": response.text[:4000]}
        data = response.json()
        if data.get("ok") is False:
            return data
        return data.get("result", data)
    except Exception as exc:
        return {"ok": False, "error": f"群伴工具网关不可用：{exc}"}


async def _call(name: str, args: dict[str, Any] | None = None) -> str:
    result = await _call_json(name, args)
    return json.dumps(result, ensure_ascii=False, default=str)


def _groups(data: Any) -> list[dict[str, Any]]:
    rows = data if isinstance(data, list) else (data or {}).get("groups", [])
    if not isinstance(rows, list):
        return []
    compact = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        compact.append(
            {
                "id": row.get("id"),
                "name": row.get("name") or row.get("remark"),
                "agent_enabled": bool(
                    (row.get("config") or {}).get("agent_enabled")
                    if isinstance(row.get("config"), dict)
                    else row.get("agent_enabled")
                ),
                "message_count": row.get("message_count") or row.get("messages"),
                "last_message_at": row.get("last_message_at"),
            }
        )
    return compact


def _messages(data: Any) -> list[dict[str, Any]]:
    rows = data if isinstance(data, list) else (data or {}).get("messages", [])
    compact = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        compact.append(
            {
                "id": row.get("id"),
                "sender": row.get("sender") or row.get("sender_name"),
                "content": str(row.get("content") or "")[:1500],
                "kind": row.get("kind"),
                "is_agent": row.get("is_agent"),
                "created_at": row.get("created_at"),
            }
        )
    return compact


CURATED_TOOL_NAMES = {
    "tool",
    "capabilities",
    "list_groups",
    "read_group",
    "send_message",
    "bili_search",
    "bili_knowledge",
    "memory_save",
    "memory_search",
    "run_mcp",
    "run_skill",
    "call_interface",
}
DIRECT_ARG_TOOLS = {"bili_search", "run_mcp", "run_skill", "call_interface"}


@server.tool(
    name="tool",
    description=(
        "通用群伴工具入口。name 是工具名，args 是工具参数对象。"
        "不确定工具名时先调用 capabilities；也可以直接调用下面注册的具体工具。"
    ),
)
async def qunban_tool(name: str, args: dict[str, Any] | None = None) -> str:
    if not str(name or "").strip():
        return json.dumps({"ok": False, "error": "name 不能为空"}, ensure_ascii=False)
    return await _call(name, args)


@server.tool(
    name="capabilities",
    description="列出群伴当前可用工具的名称、类型、风险、说明和示例参数。",
)
async def qunban_capabilities() -> str:
    data = await _call_json("list_capabilities", {})
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
                example if name in DIRECT_ARG_TOOLS else {"args": example}
            )
        compact.append(entry)
    return json.dumps(compact[:160], ensure_ascii=False)


@server.tool(
    name="send_message",
    description=(
        "发送消息。group_id 用于群聊，contact_id 用于私聊；二者选一个。"
        "只允许在管理员授权的范围内发送。"
    ),
)
async def qunban_send_message(
    text: str,
    group_id: int | None = None,
    contact_id: int | None = None,
) -> str:
    if group_id:
        return await _call(
            "send_group_message", {"group_id": int(group_id), "text": text}
        )
    if contact_id:
        return await _call(
            "send_contact_message", {"contact_id": int(contact_id), "text": text}
        )
    return json.dumps(
        {"ok": False, "error": "必须提供 group_id 或 contact_id"}, ensure_ascii=False
    )


@server.tool(
    name="read_group",
    description="读取指定群最近消息。group_id 是群伴中的群数据库 ID。",
)
async def qunban_read_group(
    group_id: int,
    limit: int = 30,
    since_id: int | None = None,
) -> str:
    args: dict[str, Any] = {"group_id": int(group_id), "limit": int(limit)}
    if since_id is not None:
        args["since_id"] = int(since_id)
    data = await _call_json("read_group_messages", args)
    return json.dumps(_messages(data), ensure_ascii=False)


@server.tool(
    name="list_groups",
    description="列出已读取并纳入群伴的微信群，返回群 ID、名称和 Agent 开关。",
)
async def qunban_list_groups() -> str:
    data = await _call_json("list_groups", {})
    return json.dumps(_groups(data), ensure_ascii=False)


@server.tool(
    name="bili_search",
    description="搜索 B站视频，返回标题、作者、bvid 和链接。",
)
async def qunban_bili_search(keyword: str, limit: int = 10) -> str:
    data = await _call_json("bili_search", {"keyword": keyword, "limit": int(limit)})
    rows = data.get("videos", []) if isinstance(data, dict) else data
    compact = [
        {
            "bvid": row.get("bvid"),
            "title": row.get("title"),
            "author": row.get("author") or row.get("owner"),
            "duration": row.get("duration"),
            "url": row.get("url"),
        }
        for row in (rows or [])
        if isinstance(row, dict)
    ]
    return json.dumps(compact[:50], ensure_ascii=False)


@server.tool(
    name="bili_knowledge",
    description="检索已写入群伴知识库的 B站视频内容。",
)
async def qunban_bili_knowledge(query: str, limit: int = 5) -> str:
    data = await _call_json(
        "bili_knowledge_search", {"query": query, "limit": int(limit)}
    )
    rows = data if isinstance(data, list) else (data or {}).get("results", [])
    compact = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        compact.append(
            {
                "bvid": row.get("bvid"),
                "title": row.get("title"),
                "text": str(row.get("text") or row.get("content") or "")[:1200],
                "score": row.get("score"),
            }
        )
    return json.dumps(compact, ensure_ascii=False)


@server.tool(
    name="memory_save",
    description="保存一条长期记忆。",
)
async def qunban_memory_save(
    content: str,
    title: str = "",
    category: str = "operator",
    importance: int = 3,
) -> str:
    return await _call(
        "save_memory",
        {
            "title": title,
            "content": content,
            "category": category,
            "importance": int(importance),
        },
    )


@server.tool(
    name="memory_search",
    description="搜索群伴长期记忆。",
)
async def qunban_memory_search(query: str, limit: int = 10) -> str:
    data = await _call_json("search_memories", {"query": query, "limit": int(limit)})
    rows = data if isinstance(data, list) else (data or {}).get("memories", [])
    compact = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        compact.append(
            {
                "title": row.get("title"),
                "content": str(row.get("content") or "")[:1200],
                "category": row.get("category"),
                "importance": row.get("importance"),
            }
        )
    return json.dumps(compact, ensure_ascii=False)


@server.tool(
    name="run_mcp",
    description=(
        "运行已启用的群伴 MCP 项目。name 是项目名；command 可选，留空则使用 manifest 里的 run；"
        "timeout 秒。返回 stdout/stderr/exit_code。"
    ),
)
async def qunban_run_mcp(
    name: str,
    command: str = "",
    timeout: int = 120,
    admin: bool = False,
) -> str:
    return await _call(
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
async def qunban_run_skill(name: str, args: str = "") -> str:
    return await _call("run_skill", {"name": name, "args": args})


@server.tool(
    name="call_interface",
    description="调用群伴已登记的 HTTP 接口。name 是接口名，args 是 URL/请求参数对象。",
)
async def qunban_call_interface(
    name: str, args: dict[str, Any] | None = None
) -> str:
    return await _call("call_interface", {"name": name, "args": args or {}})


_MANIFEST_TOOL_NAMES: set[str] = set()


def _make_tool(handler: str):
    async def run(args: dict[str, Any] | None = None) -> str:
        return await _call(handler, args or {})

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
        server.remove_tool(name)
    except Exception:
        pass
    # Call the manifest name, not the underlying handler.  The Qunban gateway
    # resolves aliases and script/http handlers; this also makes newly created
    # aliases callable through qunban_tool without a direct MCP snapshot.
    server.add_tool(
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
            server.remove_tool(old)
        except Exception:
            pass
        _MANIFEST_TOOL_NAMES.discard(old)
    for entry in entries:
        if entry.get("enabled", True):
            _add_manifest_tool(entry)
    return sorted(wanted)


def _register_manifest_tools() -> None:
    try:
        from app.services import tool_registry

        tool_registry.load_manifest()
    except Exception as exc:
        print(f"qunban tool manifest unavailable: {exc}", file=sys.stderr)
        return
    reload_manifest()


async def _admin_call(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{API_BASE}/api/opencode/tools/admin",
                json=payload,
                headers={"x-qunban-token": TOKEN},
            )
        if response.status_code >= 400:
            return {"ok": False, "error": response.text[:4000]}
        return response.json()
    except Exception as exc:
        return {"ok": False, "error": f"工具管理 API 不可用：{exc}"}


async def _refresh_mcp() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{API_BASE}/api/opencode/refresh-mcp",
                headers={"x-qunban-token": TOKEN},
            )
        if response.status_code >= 400:
            return {"ok": False, "error": response.text[:4000]}
        return response.json()
    except Exception as exc:
        return {"ok": False, "error": f"MCP 刷新 API 不可用：{exc}"}


@server.tool(
    name="tool_admin",
    description=(
        "管理群伴工具清单。action 支持 list/get/upsert/remove/enable/disable/sync/reload/refresh。"
        "refresh 会让 OpenCode 重连 MCP，使新建工具的直接入口进入工具列表。"
        "upsert 时在 tool 对象里传 name、description、handler_type、handler/script、args_example、enabled。"
        "handler_type=native 时 handler 引用群伴原生工具；handler_type=script 时 script 指向 "
        "data/opencode/tools 下的 .py/.ps1 脚本，脚本从 stdin 读 JSON 参数并输出结果。"
        "upsert 后当前任务内用 qunban_tool(name=..., args=...) 立即调用；直接入口 qunban_<name> "
        "会在本轮空闲后自动刷新 MCP，下一轮可用。示例："
        '{"action":"upsert","tool":{"name":"now_time","handler_type":"script","script":"now_time.py","description":"返回当前时间","args_example":{}}}'
    ),
)
async def qunban_tool_admin(
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
        return json.dumps(await _refresh_mcp(), ensure_ascii=False, default=str)
    result = await _admin_call(payload)
    if action.strip().lower() in {"upsert", "remove", "enable", "disable", "sync", "reload"}:
        try:
            names = reload_manifest()
            result["active_manifest_tools"] = len(names)
        except Exception as exc:
            result["reload_error"] = str(exc)
        result["direct_tool_note"] = (
            "当前任务内请用 qunban_tool(name=\"工具名\", args={...}) 立即调用；"
            "直接入口会在本轮空闲后自动刷新 MCP，下一轮可用。"
        )
        await _refresh_mcp()
    return json.dumps(result, ensure_ascii=False, default=str)


_register_manifest_tools()


def main() -> None:
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
