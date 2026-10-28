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


@server.tool(
    name="qunban_tool",
    description=(
        "调用群伴的微信/B站/记忆/文件/终端工具。"
        "name 是工具名，例如 list_groups、read_group_messages、send_group_message、"
        "bili_search、bili_knowledge_search、save_memory、search_memories、run_powershell。"
        "args 是工具参数对象。不确定工具名时先调用 qunban_capabilities。"
    ),
)
async def qunban_tool(name: str, args: dict[str, Any] | None = None) -> str:
    if not str(name or "").strip():
        return json.dumps({"ok": False, "error": "name 不能为空"}, ensure_ascii=False)
    return await _call(name, args)


@server.tool(
    name="qunban_capabilities",
    description="列出群伴当前可用工具的名称、类型、风险和说明。",
)
async def qunban_capabilities() -> str:
    data = await _call_json("list_capabilities", {})
    rows = data if isinstance(data, list) else (data or {}).get("capabilities", [])
    compact = [
        {
            "name": row.get("name") or row.get("key"),
            "type": row.get("type"),
            "risk": row.get("risk"),
            "description": str(row.get("description") or "")[:160],
        }
        for row in rows
        if isinstance(row, dict)
    ]
    return json.dumps(compact[:120], ensure_ascii=False)


@server.tool(
    name="qunban_send_message",
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
    name="qunban_read_group",
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
    name="qunban_list_groups",
    description="列出已读取并纳入群伴的微信群，返回群 ID、名称和 Agent 开关。",
)
async def qunban_list_groups() -> str:
    data = await _call_json("list_groups", {})
    return json.dumps(_groups(data), ensure_ascii=False)


@server.tool(
    name="qunban_bili_search",
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
    name="qunban_bili_knowledge",
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
    name="qunban_memory_save",
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
    name="qunban_memory_search",
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


def main() -> None:
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
