"""Offline checks for the fast reply path.

    .\\.venv312\\Scripts\\python.exe scripts\\check_reply_latency.py
"""

from __future__ import annotations

import asyncio
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.agent import AgentEngine  # noqa: E402
from app.models import Message  # noqa: E402
from app.services import mem0_memory  # noqa: E402
from app.services.settings import DEFAULT_SETTINGS  # noqa: E402


class FakeRuntime:
    bot_name = "小助手"


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise SystemExit(f"FAIL {name} {detail}")
    print(f"ok   {name}")


def main() -> None:
    engine = AgentEngine(FakeRuntime(), None)

    cases = {
        "你好": True,
        "哈哈": True,
        "谢谢": True,
        "在吗": True,
        "今天有点累，随便聊聊": True,
        "帮我查一下明天天气": False,
        "明天天气怎么样？": False,
        "让 opencode 去做": False,
        "继续": False,
        "发群里": False,
        "做一份点检表": False,
    }
    for text, expected in cases.items():
        got = engine._is_fast_chat(Message(group_id=1, content=text))
        check(f"fast-chat {text}", got == expected, f"got {got}")

    plan = engine._fallback_plan(
        Message(group_id=1, content="你好"),
        None,  # type: ignore[arg-type]
        [{"name": "call_interface"}],
    )
    check(
        "fast plan is a chat reply",
        bool(plan.get("should_reply")) and not plan.get("need_tools"),
    )

    check(
        "default read interval",
        float(DEFAULT_SETTINGS["read_poll_interval_seconds"]) == 0.5,
    )
    check(
        "default memory timeout",
        float(DEFAULT_SETTINGS["memory_search_timeout_seconds"]) == 1.5,
    )
    check("fast planner default", bool(DEFAULT_SETTINGS["fast_chat_planner"]))

    # The burst window already elapsed, so it must be deducted from the pause.
    random.seed(0)
    fixed = AgentEngine._reply_delay(
        {"reply_delay_seconds": 1.0, "reply_delay_random_seconds": 0.0},
        0.4,
    )
    check("delay deducts coalesce", abs(fixed - 0.6) < 1e-6, str(fixed))
    for coalesce in (0.0, 0.15, 0.4):
        value = AgentEngine._reply_delay(
            {"reply_delay_seconds": 0.8, "reply_delay_random_seconds": 1.5},
            coalesce,
        )
        check(
            f"delay range {coalesce}",
            0.0 <= value <= 2.3,
            str(value),
        )

    # A reply must never wait for mem0 initialization.
    original_warmup = mem0_memory.warmup
    mem0_memory.warmup = lambda _settings: None  # type: ignore[assignment]
    try:
        mem0_memory._READY = False
        mem0_memory._INIT_TASK = None

        async def probe() -> tuple[int, float]:
            started = time.perf_counter()
            items = await mem0_memory.search(
                {"memory_engine": "auto", "llm_api_key": "test"},
                account_key="",
                query="项目点检",
                timeout=1.5,
            )
            return len(items), (time.perf_counter() - started) * 1000

        count, elapsed = asyncio.run(probe())
    finally:
        mem0_memory.warmup = original_warmup  # type: ignore[assignment]

    check("mem0 not ready is non-blocking", elapsed < 100, f"{elapsed:.1f}ms")
    check("mem0 not ready returns empty", count == 0)

    print("\nREPLY LATENCY CHECKS PASSED")


if __name__ == "__main__":
    main()
