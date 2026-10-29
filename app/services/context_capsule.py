"""Context capsule assembly for DSH chat lanes.

The capsule keeps the stable prefix (persona, group identity, long-lived
memory) separate from volatile retrieval and the current message.  That makes
prompt-cache reuse predictable and prevents a large knowledge block from
pushing the current message out of the context window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if limit <= 0:
        return ""
    return text[:limit]


@dataclass
class ChatCapsule:
    scope: str
    stable: str
    memory: str
    knowledge: str
    recent: str
    current: str

    def render(self) -> str:
        parts = [self.stable, "# 长期记忆\n" + (self.memory or "（无）")]
        if self.knowledge:
            parts.append("# 相关知识（可选用，不要强行引用）\n" + self.knowledge)
        parts.append("# 最近对话\n" + (self.recent or "（无）"))
        parts.append("# 当前消息\n" + self.current)
        parts.append(
            "# 你的任务\n直接回复，不要调用工具，不要输出 JSON、Markdown 或分析过程。\n"
            "通常 1–2 句；需要工具/复杂操作时，用一句自然话说明后台会处理。"
        )
        return "\n\n".join(part for part in parts if part.strip())

    def estimate_tokens(self) -> int:
        # Rough estimate only; useful for logs and budget guards.
        return max(1, len(self.render()) // 2)


def build_chat_capsule(
    *,
    scope: str,
    group_name: str = "",
    sender: str = "",
    persona: str = "",
    memory: str = "",
    knowledge: str = "",
    recent: str = "",
    current: str = "",
    max_chars: int = 6000,
) -> ChatCapsule:
    """Build with a hard character budget, stable sections first."""
    stable = (
        f"# 通道\n{scope}\n"
        f"# 群聊\n群：{group_name or '未命名'}\n触发者：{sender or '群友'}\n"
        f"# 人设\n{persona or '自然、简短、像群友一样接话'}"
    )
    capsule = ChatCapsule(
        scope=scope,
        stable=stable[:1200],
        memory=_clip(memory, 1200),
        knowledge=_clip(knowledge, 1100),
        recent=_clip(recent, 1800),
        current=_clip(current, 2000),
    )
    if len(capsule.render()) <= max_chars:
        return capsule
    # Drop lower-value sections first, never the current message.
    capsule.knowledge = _clip(capsule.knowledge, 500)
    capsule.memory = _clip(capsule.memory, 600)
    capsule.recent = _clip(capsule.recent, 1000)
    return capsule
