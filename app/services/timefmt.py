# -*- coding: utf-8 -*-
"""时间格式化工具：库里的 datatime 都是 naive UTC，输出时统一带上时区。"""
from __future__ import annotations

from datetime import datetime, timezone

LOCAL_TZ_LABEL = "北京时间"


def iso_utc(value: datetime | None) -> str | None:
    """naive → 视为 UTC 并输出带 Z 的 ISO；带时区的按 UTC 归一。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def local_text(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """给提示词/日志用：把 naive UTC 转成本地时间文本。"""
    if value is None:
        return "未知"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime(fmt)


def now_local_text(fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.now().astimezone().strftime(fmt)
