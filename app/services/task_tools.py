"""Pick a small tool allowlist for one durable task.

The task lane starts a fresh ACP process for the task, so the native plugin can
load only the tools this task needs.  Rules are intentionally conservative:
when in doubt, add the read/search tool, not shell or plugin installation.
"""

from __future__ import annotations

from typing import Any

BASE_TOOLS = (
    "wechat_status",
    "wechat_list_groups",
    "wechat_read_group",
    "qunban_memory_search",
    "qunban_memory_save",
)


def _has(text: str, *words: str) -> bool:
    return any(word and word in text for word in words)


def tools_for_task(request: str) -> list[str]:
    text = str(request or "").lower()
    tools = list(BASE_TOOLS)
    if _has(text, "b站", "bilibili", "视频", "知识库"):
        tools.extend(["qunban_bili_search", "qunban_bili_knowledge"])
    if _has(text, "查", "搜索", "搜一下", "接口", "api", "天气", "资料"):
        tools.append("qunban_call_interface")
    if _has(text, "表格", "excel", "xlsx", "csv", "导出"):
        tools.append("qunban_create_excel")
    if _has(text, "技能", "skill"):
        tools.extend(["qunban_run_skill", "qunban_skill_*"])
    if _has(text, "mcp", "屏幕", "浏览器"):
        tools.extend(["qunban_run_mcp", "qunban_mcp_*"])
    if _has(text, "插件", "plugin"):
        # A plugin task may write studio files and then install them.  The
        # task lane is switched to admin scope only for this allowlist.
        tools.extend(
            [
                "qunban_plugin_install",
                "qunban_powershell",
                "qunban_run_skill",
            ]
        )
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(tools))


def is_plugin_task(request: str) -> bool:
    text = str(request or "").lower()
    return _has(text, "插件", "plugin")


def allow_env(request: str) -> str:
    return ",".join(tools_for_task(request))


def describe(request: str) -> dict[str, Any]:
    return {"request": str(request or ""), "tools": tools_for_task(request)}
