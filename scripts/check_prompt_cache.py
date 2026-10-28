"""Offline checks for the cache-friendly prompt layout.

Run without starting the server:

    .\\.venv312\\Scripts\\python.exe scripts\\check_prompt_cache.py

The checks encode the two rules that keep provider-side prefix caching
working:

1. Changing only volatile data (time, memory, impressions, current messages)
   must not change a single byte of the cacheable prefix.
2. Volatile data must sit after the stable prefix, never before it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.llm import cache_hit_tokens, cache_miss_tokens  # noqa: E402
from app.services.prompting import (  # noqa: E402
    PromptBuilder,
    build_group_proactive_prompt,
    build_group_reply_prompt,
    build_opencode_operator_prompt,
    build_operator_prompt,
    build_side_chat_prompt,
)

PLAN = {
    "intent": "chat",
    "user_emotion": "neutral",
    "emotion_intensity": 0.2,
    "tone": "warm",
    "missing_capability": "",
}


def group_prompt(
    *,
    now_local: str = "2026-10-28 23:10",
    memory: str = "",
    bili: str = "",
    members: list[str] | None = None,
    group_name: str = "测试群",
):
    return build_group_reply_prompt(
        persona="你是一个测试人设。",
        group_name=group_name,
        bot_name="小助手",
        trigger_label="所有消息",
        now_local=now_local,
        timezone="Asia/Shanghai",
        members=members or ["张三", "李四"],
        sticker_catalog="- id=1 名称=OK 标签=同意 描述=可以",
        sticker_enabled=True,
        tool_lines=[
            "- bili_search: 搜索 B站视频 args={\"keyword\": \"\"}",
            "- create_excel: 生成表格 args={\"sheets\": []}",
        ],
        resources=["api:weather args={\"city\": \"\"}"],
        tool_example='{"action": "tool", "tool": "bili_search", "args": {}, "thought": "查一下"}',
        api_example="\n接口参数必须使用 args 里的键，例如：\n{\"action\": \"tool\"}",
        bili_hint="",
        extra_prompt="",
        custom_core="",
        multi_message_enabled=False,
        max_messages=3,
        memory_text=memory,
        bili_context=bili,
        plan=PLAN,
        impression_text="喜欢问技术问题",
        use_tools=False,
    )


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise SystemExit(f"FAIL {name} {detail}")
    print(f"ok   {name}")


def main() -> None:
    # --- builder ordering -------------------------------------------------
    builder = (
        PromptBuilder("test")
        .static("STATIC")
        .context("CONTEXT")
        .dynamic("DYNAMIC")
    )
    built = builder.build()
    check(
        "builder order",
        built.system.index("STATIC") < built.system.index("CONTEXT") < built.system.index("DYNAMIC"),
    )
    check(
        "builder prefix",
        built.system[: built.cache_prefix_chars] == "STATIC\n\nCONTEXT",
        built.system,
    )

    # --- group reply: volatile tail only ---------------------------------
    a = group_prompt(now_local="2026-10-28 23:10", memory="记忆 A")
    b = group_prompt(now_local="2026-10-29 09:00", memory="记忆 B", members=["王五"])
    prefix_a = a.system[: a.cache_prefix_chars]
    prefix_b = b.system[: b.cache_prefix_chars]
    check("group prefix stable across time/memory", prefix_a == prefix_b)
    check("group prefix has no clock", "23:10" not in prefix_a and "09:00" not in prefix_a)
    check("group dynamic differs", a.system != b.system)
    check("group prefix ratio > 0.5", a.cache_prefix_chars / len(a.system) > 0.5,
          f"{a.cache_prefix_chars}/{len(a.system)}")
    check("group starts with rules", a.system.startswith("# 回复规则"))
    check("group memory after prefix", "记忆 A" in a.system[a.cache_prefix_chars :])

    bili_prompt = group_prompt(now_local="2026-10-28 23:10", memory="记忆 A", bili="看过：缓存机制")
    check(
        "bili context is cached",
        "看过：缓存机制" in bili_prompt.system[: bili_prompt.cache_prefix_chars],
    )

    # --- group reply: capability toggles only move the tail --------------
    without_tools = build_group_reply_prompt(
        persona="你是一个测试人设。",
        group_name="测试群",
        bot_name="小助手",
        trigger_label="所有消息",
        now_local="2026-10-28 23:10",
        timezone="Asia/Shanghai",
        members=["张三", "李四"],
        sticker_catalog="- id=1 名称=OK 标签=同意 描述=可以",
        sticker_enabled=True,
        tool_lines=[],
        resources=[],
        tool_example="",
        api_example="",
        bili_hint="",
        extra_prompt="",
        custom_core="",
        multi_message_enabled=False,
        max_messages=3,
        memory_text="记忆 A",
        bili_context="",
        plan=PLAN,
        impression_text="喜欢问技术问题",
        use_tools=False,
    )
    check(
        "tool catalog only changes tail",
        a.system.startswith(
            without_tools.system[: without_tools.cache_prefix_chars]
        ),
    )

    # --- proactive --------------------------------------------------------
    p1 = build_group_proactive_prompt(
        persona="人设",
        group_name="测试群",
        bot_name="小助手",
        now_local="2026-10-28 23:10",
        timezone="Asia/Shanghai",
        members=["张三"],
        sticker_catalog="- id=1",
        sticker_enabled=True,
        extra_prompt="",
        multi_message_enabled=False,
        max_messages=3,
        memory_text="记忆 A",
    )
    p2 = build_group_proactive_prompt(
        persona="人设",
        group_name="测试群",
        bot_name="小助手",
        now_local="2026-10-30 10:00",
        timezone="Asia/Shanghai",
        members=["李四"],
        sticker_catalog="- id=1",
        sticker_enabled=True,
        extra_prompt="",
        multi_message_enabled=False,
        max_messages=3,
        memory_text="记忆 B",
    )
    check(
        "proactive prefix stable",
        p1.system[: p1.cache_prefix_chars] == p2.system[: p2.cache_prefix_chars],
    )

    # --- operator ---------------------------------------------------------
    o1 = build_operator_prompt(
        core="",
        channel_text="后台",
        workspace=r"C:\work",
        admin_text="否",
        persona_block="人设",
        extra_prompt="",
        tool_catalog='[{"name": "read_file"}]',
        resource_lines="无",
        limit_text="不限制",
        memory_lines="记忆 A",
    )
    o2 = build_operator_prompt(
        core="",
        channel_text="后台",
        workspace=r"C:\work",
        admin_text="否",
        persona_block="人设",
        extra_prompt="",
        tool_catalog='[{"name": "read_file"}]',
        resource_lines="无",
        limit_text="不限制",
        memory_lines="记忆 B",
    )
    check(
        "operator prefix stable with memory change",
        o1.system[: o1.cache_prefix_chars] == o2.system[: o2.cache_prefix_chars],
    )
    check(
        "operator memory is last",
        o1.system[o1.cache_prefix_chars :].strip().startswith("# 长期记忆"),
    )
    check("operator protocol kept", "action" in o1.system and "final" in o1.system)

    oc = build_opencode_operator_prompt(
        core="",
        channel_text="后台",
        workspace=r"C:\work",
        admin_text="否",
        tool_names="qunban_read_file",
        resource_block="无",
        persona_block="人设",
        extra_prompt="",
        memory_lines="记忆 A",
    )
    check("opencode prompt includes rules", "官方 OpenCode" in oc.system)
    check("opencode memory is last", oc.system[oc.cache_prefix_chars :].strip().startswith("# 长期记忆"))

    # OpenCode session cache: memory changes must not rebuild inside the TTL.
    from app.services.assistant import AssistantService  # noqa: PLC0415

    service = AssistantService(None)
    settings = {"operator_persona": "人设", "operator_prompt_extra": ""}
    capability = {
        "tools": [{"name": "read_file"}],
        "skills": [],
        "mcps": [],
        "apis": [],
    }

    def memory(content: str) -> list[dict[str, object]]:
        return [
            {
                "content": content,
                "title": content,
                "category": "general",
                "importance": 3,
                "scope": "operator",
            }
        ]

    first = service._cached_opencode_system_prompt(
        session_id="cache-check",
        settings=settings,
        remembers=memory("记忆 A"),
        capability_payload=capability,
        contact_persona="",
        channel="dashboard",
    )
    second = service._cached_opencode_system_prompt(
        session_id="cache-check",
        settings=settings,
        remembers=memory("记忆 B"),
        capability_payload=capability,
        contact_persona="",
        channel="dashboard",
    )
    check("opencode system frozen per session", first == second)
    third = service._cached_opencode_system_prompt(
        session_id="cache-check",
        settings={**settings, "operator_persona": "新人设"},
        remembers=memory("记忆 B"),
        capability_payload=capability,
        contact_persona="",
        channel="dashboard",
    )
    check("opencode system rebuilds on persona change", third != first)

    side1 = build_side_chat_prompt(persona_text="人设", busy=True, status_text="步骤 3")
    side2 = build_side_chat_prompt(persona_text="人设", busy=False, status_text="空闲")
    check(
        "side chat prefix stable",
        side1.system[: side1.cache_prefix_chars] == side2.system[: side2.cache_prefix_chars],
    )

    # --- provider usage parsing ------------------------------------------
    check(
        "deepseek cache fields",
        cache_hit_tokens({"prompt_cache_hit_tokens": 800}, 1000) == 800,
    )
    check(
        "openai cache fields",
        cache_hit_tokens({"prompt_tokens_details": {"cached_tokens": 700}}, 1000) == 700,
    )
    check(
        "cache miss explicit",
        cache_miss_tokens({"prompt_cache_miss_tokens": 200}, 1000, 800) == 200,
    )
    check("cache miss derived", cache_miss_tokens({}, 1000, 800) == 200)
    check("no cache", cache_hit_tokens({}, 1000) == 0)

    print("\nPROMPT CACHE CHECKS PASSED")


if __name__ == "__main__":
    main()
