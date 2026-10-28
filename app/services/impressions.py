"""Per-user emotional/relationship memory for group chats.

The group Agent uses this as a lightweight, inspectable complement to the
long-term memory engine: every interacted member gets an emotion histogram,
a smoothed sentiment score, a few traits, and a short impression summary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import UserImpression

_EMOTION_SENTIMENT = {
    "happy": 0.8,
    "excited": 0.7,
    "playful": 0.5,
    "neutral": 0.0,
    "confused": -0.2,
    "anxious": -0.4,
    "frustrated": -0.6,
    "angry": -0.9,
    "sad": -0.7,
    "disappointed": -0.5,
}

_ALLOWED_EMOTIONS = set(_EMOTION_SENTIMENT)


def normalize_emotion(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "开心": "happy",
        "高兴": "happy",
        "兴奋": "excited",
        "生气": "angry",
        "愤怒": "angry",
        "烦": "frustrated",
        "烦躁": "frustrated",
        "焦虑": "anxious",
        "难过": "sad",
        "失望": "disappointed",
        "困惑": "confused",
        "中性": "neutral",
    }
    text = aliases.get(text, text)
    return text if text in _ALLOWED_EMOTIONS else "neutral"


def impression_dict(row: UserImpression | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "user_wxid": row.user_wxid,
        "display_name": row.display_name,
        "sentiment": round(float(row.sentiment or 0), 2),
        "last_emotion": row.last_emotion or "neutral",
        "emotion_intensity": round(float(row.emotion_intensity or 0), 2),
        "emotion_counts": dict(row.emotion_counts or {}),
        "traits": list(row.traits or []),
        "summary": row.summary or "",
        "interaction_count": int(row.interaction_count or 0),
    }


async def get_impression(
    group_id: int, user_wxid: str, account_key: str = ""
) -> dict[str, Any]:
    if not user_wxid:
        return {}
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(UserImpression).where(
                    UserImpression.group_id == group_id,
                    UserImpression.user_wxid == user_wxid,
                    UserImpression.account_key == account_key,
                )
            )
        ).scalar_one_or_none()
        return impression_dict(row)


async def update_impression(
    *,
    group_id: int,
    user_wxid: str,
    display_name: str,
    account_key: str = "",
    emotion: str = "neutral",
    intensity: float = 0.0,
    traits: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    if not user_wxid:
        return {}
    emotion = normalize_emotion(emotion)
    try:
        intensity = max(0.0, min(1.0, float(intensity)))
    except (TypeError, ValueError):
        intensity = 0.0
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(UserImpression).where(
                    UserImpression.group_id == group_id,
                    UserImpression.user_wxid == user_wxid,
                    UserImpression.account_key == account_key,
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if row is None:
            row = UserImpression(
                group_id=group_id,
                account_key=account_key,
                user_wxid=user_wxid,
                display_name=display_name or user_wxid,
            )
            db.add(row)
        count = int(row.interaction_count or 0)
        target = _EMOTION_SENTIMENT.get(emotion, 0.0)
        old_sentiment = float(row.sentiment or 0)
        row.sentiment = (old_sentiment * count + target) / max(1, count + 1)
        row.last_emotion = emotion
        row.emotion_intensity = intensity
        counts = dict(row.emotion_counts or {})
        counts[emotion] = int(counts.get(emotion, 0)) + 1
        row.emotion_counts = counts
        if display_name:
            row.display_name = display_name
        if traits:
            merged = list(row.traits or [])
            for item in traits:
                item = str(item).strip()
                if item and item not in merged:
                    merged.append(item)
            row.traits = merged[-12:]
        note = str(note or "").strip()
        if note:
            old = str(row.summary or "").strip()
            # Keep the most recent impressions, newest first, capped.
            merged_note = (note + " " + old).strip()
            row.summary = merged_note[:900]
        row.interaction_count = count + 1
        row.last_seen_at = now
        await db.commit()
        await db.refresh(row)
        return impression_dict(row)


def impression_prompt(impression: dict[str, Any]) -> str:
    if not impression:
        return "暂无这个人的历史印象，先观察本轮表现。"
    counts = impression.get("emotion_counts") or {}
    emotions = "、".join(
        f"{key}×{value}" for key, value in sorted(counts.items())
    ) or "无"
    traits = "、".join(impression.get("traits") or []) or "暂无"
    return (
        f"- 昵称：{impression.get('display_name') or '未知'}\n"
        f"- 互动次数：{impression.get('interaction_count') or 0}\n"
        f"- 最近情绪：{impression.get('last_emotion')}（强度 {impression.get('emotion_intensity'):.2f}）\n"
        f"- 历史情绪分布：{emotions}\n"
        f"- 情感倾向：{impression.get('sentiment'):+.2f}（-1 负面，+1 正面）\n"
        f"- 性格/偏好标签：{traits}\n"
        f"- 印象摘要：{impression.get('summary') or '暂无'}"
    )
