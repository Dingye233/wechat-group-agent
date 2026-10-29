"""Mirror RuntimeManager events to the Qunban sidecar WebSocket hub."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx


class EventRelay:
    def __init__(self) -> None:
        base = str(os.environ.get("QUNBAN_SIDECAR_URL") or "").rstrip("/")
        self.url = f"{base}/v1/events/publish" if base else ""
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._worker(), name="qunban-event-relay")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(
                {"type": event_type, "data": data, "ts": 0}
            )
        except asyncio.QueueFull:
            pass

    async def _worker(self) -> None:
        async with httpx.AsyncClient(timeout=2.0) as client:
            while True:
                try:
                    event = await self._queue.get()
                except asyncio.CancelledError:
                    raise
                try:
                    await client.post(self.url, json=event)
                except Exception:
                    await asyncio.sleep(1.0)
