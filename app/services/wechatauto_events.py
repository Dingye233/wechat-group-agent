"""Durable event boundary for the embedded or future standalone wechatauto.

Events are appended to a small JSONL log before being kept in the in-memory
ring.  The file is intentionally simple: a separate wechatauto daemon can tail
or replay it later, and Qunban can recover recent events after a restart.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings as process_settings

_EVENTS: deque[dict[str, Any]] = deque(maxlen=500)
_LOCK = threading.Lock()
_MAX_BYTES = 5 * 1024 * 1024
_EVENT_PATH = process_settings.data_dir / "wechatauto-events.jsonl"


def _rotate_if_needed() -> None:
    try:
        if _EVENT_PATH.is_file() and _EVENT_PATH.stat().st_size > _MAX_BYTES:
            backup = _EVENT_PATH.with_suffix(".jsonl.1")
            if backup.exists():
                backup.unlink()
            _EVENT_PATH.replace(backup)
    except OSError:
        pass


def _append(event: dict[str, Any]) -> None:
    try:
        process_settings.data_dir.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            _rotate_if_needed()
            with _EVENT_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def _load_recent() -> None:
    if not _EVENT_PATH.is_file():
        return
    try:
        lines = _EVENT_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines[-500:]:
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict):
            _EVENTS.append(event)


def publish(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    event = {
        "id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
        "type": str(event_type or "event"),
        "payload": payload,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _EVENTS.append(event)
    _append(event)
    return event


def recent(limit: int = 100, *, event_type: str = "") -> list[dict[str, Any]]:
    limit = max(1, min(500, int(limit)))
    rows = list(_EVENTS)
    if event_type:
        rows = [row for row in rows if row.get("type") == event_type]
    return rows[-limit:][::-1]


def event_path() -> str:
    return str(_EVENT_PATH)


def clear() -> None:
    _EVENTS.clear()
    try:
        if _EVENT_PATH.exists():
            _EVENT_PATH.unlink()
    except OSError:
        pass


_load_recent()
