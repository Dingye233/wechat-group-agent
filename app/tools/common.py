"""Shared transport and formatting helpers for Qunban tool modules."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

API_BASE = os.environ.get("QUNBAN_API_BASE", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("QUNBAN_OPENCODE_TOKEN", "")
SCOPE = os.environ.get("QUNBAN_SCOPE", "admin")

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
    "tool_admin",
}


async def call_json(name: str, args: dict[str, Any] | None = None) -> Any:
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


async def call(name: str, args: dict[str, Any] | None = None) -> str:
    result = await call_json(name, args)
    return json.dumps(result, ensure_ascii=False, default=str)


async def admin_call(payload: dict[str, Any]) -> dict[str, Any]:
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


async def refresh_mcp() -> dict[str, Any]:
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


def groups(data: Any) -> list[dict[str, Any]]:
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


def messages(data: Any) -> list[dict[str, Any]]:
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
