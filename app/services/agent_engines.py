"""Choose the agent runtime.

DeepSeek Harness is the only agent engine.  OpenCode is no longer a fallback:
the native DSH plugin and the Qunban HTTP gateway share one tool surface, one
session model and one WeChat send path.  ``native`` remains only for a profile
where DSH is explicitly disabled (for example a fresh checkout without the
runtime installed); it is not an OpenCode fallback.
"""

from __future__ import annotations

from typing import Any


def resolve_agent_engine(settings: dict[str, Any]) -> str:
    if settings.get("dsh_enabled", True):
        return "dsh"
    return "native"


def engine_label(engine: str) -> str:
    return {
        "dsh": "DeepSeek Harness",
        "native": "内置循环（DSH 未启用）",
        "opencode": "OpenCode（已停用）",
    }.get(engine, engine)
