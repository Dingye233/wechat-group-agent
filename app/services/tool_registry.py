"""OpenCode-managed manifest for Qunban native tools.

Qunban still owns the native Python handlers, but the *tool catalog* used by
the official OpenCode agent is stored in a manifest that OpenCode can edit via
``qunban_tool_admin``.  This keeps tool descriptions, enablement and aliases
out of the hard-coded prompt/catalog path.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.assistant_tools import tool_catalog

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "data" / "opencode" / "tools.json"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HANDLER_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")
SCRIPT_ROOT = ROOT / "data" / "opencode" / "tools"

_SPECIAL_HANDLERS = {
    "bili_last_opinion",
    "list_groups",
    "read_group_messages",
    "send_group_message",
    "send_contact_message",
    "run_mcp",
    "run_skill",
    "call_interface",
    "save_memory",
    "search_memories",
    "list_capabilities",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _handler_names() -> set[str]:
    names = {
        str(item.get("name") or "")
        for item in tool_catalog()
        if str(item.get("name") or "")
    }
    names.update(_SPECIAL_HANDLERS)
    return names


def _seed_entry(item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("name") or "").strip()
    args = item.get("args") if isinstance(item.get("args"), dict) else {}
    desc = str(item.get("desc") or "群伴原生工具")
    if name == "run_powershell":
        desc += "；禁止用 PowerShell 操作微信窗口或模拟发送消息，微信发送只能走 send_contact_message/send_group_message"
    return {
        "name": name,
        "description": f"{desc}。参数示例：{json.dumps(args, ensure_ascii=False)}",
        "handler": name,
        "handler_type": "native",
        "args_example": args,
        "enabled": True,
        "source": "qunban-native",
        "managed_by": "opencode",
        "created_at": _now(),
        "updated_at": _now(),
    }


def default_entries() -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in tool_catalog():
        name = str(item.get("name") or "").strip()
        if name and name not in rows:
            rows[name] = _seed_entry(item)
    # These are Qunban-native semantics that are not in the compact catalog.
    for name, handler, description, example in (
        ("bili_last_opinion", "bili_last_opinion", "读取最近一次 B站视频分析中 Agent 的看法", {}),
        ("bili_comment", "bili_comment", "把 Agent 的看法发到 B站视频评论区", {"bvid": "BV...", "text": "..."}),
        ("read_group", "read_group_messages", "读取指定群的最近消息", {"group_id": 211, "limit": 30}),
        ("send_group", "send_group_message", "向指定群发送一条消息", {"group_id": 211, "text": "..."}),
    ):
        rows.setdefault(
            name,
            {
                "name": name,
                "description": f"{description}。参数示例：{json.dumps(example, ensure_ascii=False)}",
                "handler": handler,
                "handler_type": "native",
                "args_example": example,
                "enabled": True,
                "source": "qunban-native",
                "managed_by": "opencode",
                "created_at": _now(),
                "updated_at": _now(),
            },
        )
    return list(rows.values())


def _write(data: dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        tools = default_entries()
        data = {"version": 1, "managed_by": "opencode", "tools": tools}
        _write(data)
        return data
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
        data = {"version": 1, "managed_by": "opencode", "tools": default_entries()}
        _write(data)
    # Desktop input simulation must never be the message-send path.  It stays
    # available in the legacy capability panel, but OpenCode tools are off
    # unless a user explicitly sets allow_desktop=true.
    changed = False
    for row in data.get("tools") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if name.startswith(("keyboard_", "mouse_")) and not row.get("allow_desktop"):
            if row.get("enabled", True):
                row["enabled"] = False
                changed = True
    if changed:
        _write(data)
    return data


def list_entries() -> list[dict[str, Any]]:
    rows = load_manifest().get("tools") or []
    return sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: str(row.get("name") or ""),
    )


def get_entry(name: str) -> dict[str, Any] | None:
    for row in list_entries():
        if row.get("name") == name:
            return row
    return None


def _normalise_tool(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    row = dict(existing or {})
    name = str(payload.get("name") or row.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("工具名只能包含字母、数字、下划线和短横线，长度 1-64")
    handler_type = str(
        payload.get("handler_type") or row.get("handler_type") or "native"
    ).strip().lower()
    if handler_type not in {"native", "script"}:
        raise ValueError("handler_type 只能是 native 或 script")
    script = ""
    if handler_type == "native":
        handler = str(payload.get("handler") or row.get("handler") or name).strip()
        if not HANDLER_RE.match(handler):
            raise ValueError("handler 名称非法")
        if handler not in _handler_names():
            raise ValueError(
                f"handler 不存在：{handler}。只能引用群伴已存在的原生工具。"
            )
    else:
        raw_script = str(payload.get("script") or row.get("script") or "").strip()
        if not raw_script:
            raise ValueError("script 工具必须提供 script 路径")
        candidate = Path(raw_script)
        if not candidate.is_absolute():
            candidate = SCRIPT_ROOT / candidate
        SCRIPT_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(SCRIPT_ROOT.resolve())
        except Exception as exc:
            raise ValueError(
                "script 必须位于 data/opencode/tools 目录内"
            ) from exc
        if not resolved.is_file():
            raise ValueError(f"script 文件不存在：{resolved}")
        if resolved.suffix.lower() not in {".py", ".ps1"}:
            raise ValueError("script 只支持 .py 或 .ps1")
        script = str(relative).replace("\\", "/")
        handler = "script"
    description = str(payload.get("description") or row.get("description") or "").strip()
    if not description:
        description = f"群伴工具 {name}"
    args_example = payload.get("args_example", row.get("args_example", {}))
    if not isinstance(args_example, dict):
        raise ValueError("args_example 必须是对象")
    enabled = payload.get("enabled", row.get("enabled", True))
    allow_desktop = payload.get("allow_desktop", row.get("allow_desktop", False))
    row.update(
        {
            "name": name,
            "description": description[:1200],
            "handler": handler,
            "handler_type": handler_type,
            "script": script,
            "args_example": args_example,
            "enabled": bool(enabled),
            "allow_desktop": bool(allow_desktop),
            "source": row.get("source") or "opencode",
            "managed_by": "opencode",
            "updated_at": _now(),
        }
    )
    row.setdefault("created_at", _now())
    return row


def upsert(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("工具名不能为空")
    existing = get_entry(name)
    row = _normalise_tool(payload, existing)
    data = load_manifest()
    rows = [item for item in data.get("tools") or [] if item.get("name") != name]
    rows.append(row)
    data["tools"] = rows
    data["updated_at"] = _now()
    _write(data)
    return row


def remove(name: str) -> bool:
    data = load_manifest()
    rows = [item for item in data.get("tools") or [] if item.get("name") != name]
    if len(rows) == len(data.get("tools") or []):
        return False
    data["tools"] = rows
    data["updated_at"] = _now()
    _write(data)
    return True


def set_enabled(name: str, enabled: bool) -> dict[str, Any]:
    row = get_entry(name)
    if row is None:
        raise ValueError(f"工具不存在：{name}")
    row = dict(row)
    row["enabled"] = bool(enabled)
    row["updated_at"] = _now()
    data = load_manifest()
    data["tools"] = [
        row if item.get("name") == name else item
        for item in data.get("tools") or []
    ]
    data["updated_at"] = _now()
    _write(data)
    return row


def sync_defaults() -> list[dict[str, Any]]:
    """Add newly shipped native tools without overwriting OpenCode edits."""
    data = load_manifest()
    existing = {
        str(item.get("name") or ""): item
        for item in data.get("tools") or []
        if isinstance(item, dict)
    }
    added: list[dict[str, Any]] = []
    for item in default_entries():
        name = str(item.get("name") or "")
        if name and name not in existing:
            existing[name] = item
            added.append(item)
    if added:
        data["tools"] = list(existing.values())
        data["updated_at"] = _now()
        _write(data)
    return added


def admin_action(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    action = str(action or "").strip().lower()
    if action == "list":
        return {"tools": list_entries(), "path": str(MANIFEST_PATH)}
    if action == "get":
        row = get_entry(str(payload.get("name") or ""))
        if row is None:
            raise ValueError("工具不存在")
        return {"tool": row}
    if action == "upsert":
        tool = payload.get("tool")
        if not isinstance(tool, dict):
            tool = payload
        return {"tool": upsert(tool)}
    if action == "remove":
        name = str(payload.get("name") or "")
        return {"removed": remove(name), "name": name}
    if action in {"enable", "disable"}:
        name = str(payload.get("name") or "")
        return {"tool": set_enabled(name, action == "enable")}
    if action == "sync":
        return {"added": sync_defaults(), "tools": list_entries()}
    if action == "reload":
        return {"tools": list_entries(), "path": str(MANIFEST_PATH)}
    raise ValueError("action 只能是 list/get/upsert/remove/enable/disable/sync/reload")
