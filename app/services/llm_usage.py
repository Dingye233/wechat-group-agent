"""Prompt-cache telemetry.

Every LLM request records one lightweight row so the dashboard can show the
real cache hit rate per call site. Writing is best-effort and never blocks or
fails the actual model call.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.models import LLMUsage, utcnow

logger = logging.getLogger(__name__)

_pending: set[asyncio.Task[Any]] = set()
_pruned = False


async def _insert(**fields: Any) -> None:
    try:
        async with SessionLocal() as db:
            db.add(LLMUsage(**fields))
            await db.commit()
    except Exception as exc:  # telemetry must never break a reply
        logger.debug("记录 LLM 用量失败：%s", exc)


def record_usage(
    *,
    call_site: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    latency_ms: int = 0,
    cache_prefix_chars: int = 0,
    ok: bool = True,
) -> None:
    """Queue a usage row without awaiting the database."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(
        _insert(
            call_site=str(call_site or "generic")[:64],
            model=str(model or "")[:128],
            prompt_tokens=max(int(prompt_tokens or 0), 0),
            completion_tokens=max(int(completion_tokens or 0), 0),
            cache_hit_tokens=max(int(cache_hit_tokens or 0), 0),
            cache_miss_tokens=max(int(cache_miss_tokens or 0), 0),
            cache_prefix_chars=max(int(cache_prefix_chars or 0), 0),
            latency_ms=max(int(latency_ms or 0), 0),
            ok=bool(ok),
        )
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def _rate(hit: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(hit / total, 4)


async def summary(hours: int = 24) -> dict[str, Any]:
    """Aggregate the last N hours of usage, grouped by call site."""
    global _pruned
    window = max(1, min(int(hours or 24), 24 * 30))
    cutoff = utcnow() - timedelta(hours=window)
    async with SessionLocal() as db:
        if not _pruned:
            _pruned = True
            try:
                await db.execute(
                    delete(LLMUsage).where(
                        LLMUsage.created_at < utcnow() - timedelta(days=30)
                    )
                )
                await db.commit()
            except Exception:
                await db.rollback()
        try:
            rows = (
                await db.execute(
                    select(
                        LLMUsage.call_site,
                        func.count(LLMUsage.id),
                        func.coalesce(func.sum(LLMUsage.prompt_tokens), 0),
                        func.coalesce(func.sum(LLMUsage.completion_tokens), 0),
                        func.coalesce(func.sum(LLMUsage.cache_hit_tokens), 0),
                        func.coalesce(func.sum(LLMUsage.cache_miss_tokens), 0),
                    )
                    .where(LLMUsage.created_at >= cutoff)
                    .group_by(LLMUsage.call_site)
                )
            ).all()
        except Exception as exc:
            logger.debug("读取 LLM 用量失败：%s", exc)
            return {"hours": window, "calls": 0, "sites": []}

    sites: list[dict[str, Any]] = []
    total = {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
    }
    for call_site, calls, prompt, completion, hit, miss in rows:
        calls = int(calls or 0)
        prompt = int(prompt or 0)
        completion = int(completion or 0)
        hit = int(hit or 0)
        miss = int(miss or 0)
        sites.append(
            {
                "call_site": call_site or "generic",
                "calls": calls,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cache_hit_tokens": hit,
                "cache_miss_tokens": miss,
                "cache_hit_rate": _rate(hit, prompt),
            }
        )
        total["calls"] += calls
        total["prompt_tokens"] += prompt
        total["completion_tokens"] += completion
        total["cache_hit_tokens"] += hit
        total["cache_miss_tokens"] += miss
    sites.sort(key=lambda item: item["prompt_tokens"], reverse=True)
    return {
        "hours": window,
        **total,
        "cache_hit_rate": _rate(total["cache_hit_tokens"], total["prompt_tokens"]),
        "sites": sites,
    }
