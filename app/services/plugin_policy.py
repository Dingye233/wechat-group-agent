"""Per-plugin channel policy for DSH plugins.

A DSH plugin can be mounted for the private/Operator profile, the group primary
profile, both, or neither.  Imported plugins also carry the tool split produced
by the DSH import review; the native Qunban plugin applies that split with
``ctx.tools.restrict`` so a multi-tool package can be limited per channel
without rewriting the third-party source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.dsh_runtime import HOME_DIR

PROFILE_DIR = HOME_DIR / "profiles" / "qunban"
POLICY_PATH = PROFILE_DIR / "plugin-policies.json"
CHANNELS = ("private", "group")

# Native DSH capabilities that bypass Qunban's WeChat gateway or are unsafe in
# a chat lane.  They stay visible in the plugin page but their channel switch is
# locked off.  Everything else is opt-in per channel.
PRIVATE_LOCKED = {
    "tool-pwsh",
    "tool-bash",
    "tool-terminal",
    "tool-workflow",
    "tool-subagent",
    "tool-subagent-control",
    "tool-subagent-list-agents",
    "tool-subagent-fork",
}
GROUP_LOCKED = {
    "tool-fs",
    "tool-fs-search",
    "tool-web",
    "tool-jobs",
    "tool-todo",
    "tool-goal",
    "tool-working-directory",
    "tool-skill",
    "skill",
    "skill-filesystem",
    "ptc-runtime",
    "workflow-ptc",
    "subagent",
    "subagent-spawn-in-process",
    "subagent-fork-in-process",
    "mcp-resources",
    *PRIVATE_LOCKED,
}


def _read() -> dict[str, Any]:
    try:
        data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data: dict[str, Any]) -> None:
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = POLICY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(POLICY_PATH)


def all_policies() -> dict[str, Any]:
    return _read()


def get(plugin_id: str, *, name: str = "") -> dict[str, Any] | None:
    data = _read()
    for key in (str(plugin_id or ""), str(name or "")):
        if key and isinstance(data.get(key), dict):
            return data[key]
    return None


def ensure(
    plugin_id: str,
    *,
    name: str = "",
    private: bool = True,
    group: bool = True,
    tools: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plugin_id = str(plugin_id or "").strip()
    if not plugin_id:
        return {}
    data = _read()
    row = data.get(plugin_id)
    if not isinstance(row, dict):
        row = {
            "private": bool(private),
            "group": bool(group),
            "tools": tools if isinstance(tools, dict) else {},
        }
        data[plugin_id] = row
        _write(data)
    return row


def ensure_builtin(plugin_id: str, *, enabled: bool, name: str = "") -> dict[str, Any]:
    private = bool(enabled) and plugin_id not in PRIVATE_LOCKED
    group = bool(enabled) and plugin_id not in GROUP_LOCKED
    row = ensure(plugin_id, name=name, private=private, group=group)
    changed = False
    if plugin_id in PRIVATE_LOCKED and row.get("private"):
        row["private"] = False
        changed = True
    if plugin_id in GROUP_LOCKED and row.get("group"):
        row["group"] = False
        changed = True
    if changed:
        data = _read()
        data[plugin_id] = row
        _write(data)
    return row


def locked(plugin_id: str) -> dict[str, bool]:
    return {
        "private": plugin_id in PRIVATE_LOCKED,
        "group": plugin_id in GROUP_LOCKED,
    }


def tool_channels(plugin_id: str, tool_name: str) -> dict[str, bool] | None:
    row = get(plugin_id)
    if not isinstance(row, dict):
        return None
    tools = row.get("tools")
    if not isinstance(tools, dict):
        return None
    value = tools.get(tool_name)
    if not isinstance(value, dict):
        return None
    return {
        "private": bool(value.get("private", True)),
        "group": bool(value.get("group", True)),
    }


def tools_for(plugin_id: str) -> list[dict[str, Any]]:
    row = get(plugin_id)
    if not isinstance(row, dict):
        return []
    tools = row.get("tools")
    if not isinstance(tools, dict):
        return []
    result: list[dict[str, Any]] = []
    for name, value in tools.items():
        if not isinstance(value, dict):
            continue
        result.append(
            {
                "name": str(name),
                "description": str(value.get("description") or ""),
                "channels": {
                    "private": bool(value.get("private", True)),
                    "group": bool(value.get("group", True)),
                },
            }
        )
    result.sort(key=lambda item: item["name"])
    return result


def set_tool_channels(
    plugin_id: str,
    tool_name: str,
    *,
    private: bool,
    group: bool,
) -> dict[str, Any]:
    plugin_id = str(plugin_id or "").strip()
    tool_name = str(tool_name or "").strip()
    if not plugin_id or not tool_name:
        raise ValueError("plugin id / tool name 不能为空")
    data = _read()
    row = data.get(plugin_id)
    if not isinstance(row, dict):
        row = {"private": True, "group": True, "tools": {}}
    tools = row.get("tools")
    if not isinstance(tools, dict):
        tools = {}
    current = tools.get(tool_name)
    value = dict(current) if isinstance(current, dict) else {}
    value["private"] = bool(private)
    value["group"] = bool(group)
    tools[tool_name] = value
    row["tools"] = tools
    data[plugin_id] = row
    _write(data)
    return {"name": tool_name, **value}


def channels(plugin_id: str, *, name: str = "", default: bool = True) -> dict[str, bool]:
    row = get(plugin_id, name=name)
    if not isinstance(row, dict):
        return {"private": bool(default), "group": bool(default)}
    return {
        "private": bool(row.get("private", default)),
        "group": bool(row.get("group", default)),
    }


def set_channels(
    plugin_id: str,
    *,
    private: bool | None = None,
    group: bool | None = None,
    name: str = "",
    tools: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plugin_id = str(plugin_id or "").strip()
    if not plugin_id:
        raise ValueError("plugin id 不能为空")
    data = _read()
    row = data.get(plugin_id)
    if not isinstance(row, dict):
        row = {"private": True, "group": False, "tools": {}}
    if private is not None:
        row["private"] = bool(private)
    if group is not None:
        row["group"] = bool(group)
    if isinstance(tools, dict):
        row["tools"] = tools
    data[plugin_id] = row
    _write(data)
    return row


def is_channel_enabled(plugin_id: str, channel: str, *, name: str = "") -> bool:
    row = get(plugin_id, name=name)
    if not isinstance(row, dict):
        return True
    return bool(row.get(channel, True))


def deny_tools(channel: str) -> list[str]:
    """Tool names that must be hidden in this channel.

    A tool entry may be either ``{"private": bool, "group": bool}`` or a list
    of allowed channel names.  Unknown/legacy entries are left enabled.
    """
    denied: list[str] = []
    for row in _read().values():
        if not isinstance(row, dict):
            continue
        tools = row.get("tools")
        if not isinstance(tools, dict):
            continue
        for tool_name, policy in tools.items():
            allowed = True
            if isinstance(policy, dict):
                allowed = bool(policy.get(channel, True))
            elif isinstance(policy, list):
                allowed = channel in {str(item) for item in policy}
            elif isinstance(policy, bool):
                allowed = policy
            if not allowed and tool_name not in denied:
                denied.append(str(tool_name))
    return denied


def filter_rows(rows: list[dict[str, Any]], channel: str) -> list[dict[str, Any]]:
    """Filter imported/generated patch rows by channel policy."""
    result: list[dict[str, Any]] = []
    for row in rows:
        items = row.get("insert") if isinstance(row, dict) else None
        if isinstance(items, list):
            kept = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                plugin_id = str(item.get("id") or "")
                name = str(item.get("name") or "")
                if is_channel_enabled(plugin_id, channel, name=name):
                    kept.append(item)
            if kept:
                result.append({**row, "insert": kept})
            continue
        plugin_id = str(row.get("id") or "")
        name = str(row.get("name") or "")
        if is_channel_enabled(plugin_id, channel, name=name):
            result.append(row)
    return result
