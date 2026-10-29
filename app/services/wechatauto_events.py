"""Small event boundary for the future standalone wechatauto service.

Today the adapter still runs in the Qunban process, but incoming messages and
transport job updates are published through this module.  A separate
wechatauto daemon can later feed the same event shape over HTTP/WebSocket.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

_EVENTS: deque[dict[str, Any]] = deque(maxlen=500)


def publish(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    event = {
        "id": f"{int(time.time() * 1000)}-{len(_EVENTS)}",
        "type": str(event_type or "event"),
        "payload": payload,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _EVENTS.append(event)
    return event


def recent(limit: int = 100, *, event_type: str = "") -> list[dict[str, Any]]:
    limit = max(1, min(500, int(limit)))
    rows = list(_EVENTS)
    if event_type:
        rows = [row for row in rows if row.get("type") == event_type]
    return rows[-limit:][::-1]


def clear() -> None:
    _EVENTS.clear()
