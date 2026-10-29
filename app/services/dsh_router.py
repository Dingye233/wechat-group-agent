"""Lightweight DSH router lane.

Rules handle the obvious cases.  The DSH lane is used only when the rule
confidence is low, so the host can arbitrate ambiguous messages without adding
a model hop to every chat turn.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.agent_router import Route
from app.services.dsh_primary import DSHPrimaryService

ROUTER_PROMPT = """你是群伴的 Router，只做分流，不回复群友。
输出一个 JSON：
{"lane":"chat|task|ignore","confidence":0.0-1.0,"reason":"短句"}
规则：
- 简单寒暄、接梗、情绪表达 → chat
- 需要查资料、调用工具、读写文件、执行任务、安装插件 → task
- 明确不需要回复 → ignore
不要输出 Markdown。"""


class DSHRouter:
    def __init__(self) -> None:
        self.service = DSHPrimaryService(
            profile="qunban-router",
            profile_scope="chat",
            toolset="none",
            env_scope="group",
            channel="group",
            use_native=False,
            system_prompt=ROUTER_PROMPT,
        )

    @property
    def running(self) -> bool:
        return self.service.running

    def status(self) -> dict[str, Any]:
        return self.service.status()

    async def classify(
        self,
        text: str,
        *,
        settings: dict[str, Any],
        context: str = "",
        timeout: float = 20.0,
    ) -> Route | None:
        prompt = f"""# 最近上下文
{context[-1200:] or '（无）'}

# 当前消息
{str(text or '')[:2000]}

只输出路由 JSON。"""
        try:
            result = await self.service.prompt(
                "router", prompt, settings=settings, timeout=timeout
            )
            parsed = json.loads(str(result.get("text") or "{}"))
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        lane = str(parsed.get("lane") or "").lower()
        if lane not in {"chat", "task", "ignore"}:
            return None
        try:
            confidence = float(parsed.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return Route(
            lane,
            max(0.0, min(1.0, confidence)),
            str(parsed.get("reason") or "DSH Router"),
        )


_router: DSHRouter | None = None


def get_dsh_router() -> DSHRouter:
    global _router
    if _router is None:
        _router = DSHRouter()
    return _router
