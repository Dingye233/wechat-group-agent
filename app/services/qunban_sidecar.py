"""HTTP client for the Qunban sidecar (memory/knowledge/context reads)."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx


class QunbanSidecar:
    def __init__(self, base_url: str = "") -> None:
        self.base_url = str(
            base_url or os.environ.get("QUNBAN_SIDECAR_URL") or ""
        ).rstrip("/")
        self._cooldown_until = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    async def _post(self, path: str, payload: dict[str, Any], timeout: float = 8.0) -> Any:
        if not self.enabled or time.monotonic() < self._cooldown_until:
            return None
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
                response = await client.post(path, json=payload)
                response.raise_for_status()
                return response.json()
        except Exception:
            self._cooldown_until = time.monotonic() + 20.0
            raise

    async def health(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=3.0) as client:
                response = await client.get("/v1/health")
                response.raise_for_status()
                self._cooldown_until = 0.0
                return dict(response.json())
        except Exception as exc:
            self._cooldown_until = time.monotonic() + 20.0
            return {"ok": False, "error": str(exc) or type(exc).__name__}

    async def group_memory(
        self,
        *,
        group_id: int,
        account_key: str,
        query: str,
        settings: dict[str, Any],
    ) -> str | None:
        try:
            data = await self._post(
                "/v1/memory/group_context",
                {
                    "group_id": int(group_id),
                    "account_key": account_key,
                    "query": query,
                    "settings": settings,
                },
            )
            if data is None:
                return None
            return str((data or {}).get("memory") or "")
        except Exception:
            return None

    async def private_memory(
        self,
        *,
        contact_wxid: str,
        account_key: str,
        query: str,
        settings: dict[str, Any],
    ) -> str | None:
        try:
            data = await self._post(
                "/v1/memory/private_context",
                {
                    "contact_wxid": contact_wxid,
                    "account_key": account_key,
                    "query": query,
                    "settings": settings,
                },
            )
            if data is None:
                return None
            return str((data or {}).get("memory") or "")
        except Exception:
            return None

    async def knowledge_search(self, query: str, limit: int = 3) -> list[dict[str, Any]] | None:
        try:
            data = await self._post(
                "/v1/knowledge/search",
                {"query": query, "limit": limit},
                timeout=5.0,
            )
            if data is None:
                return None
            return list((data or {}).get("results") or [])
        except Exception:
            return None

    async def save_memory(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            data = await self._post("/v1/memory/save", payload)
            if data is None:
                return None
            return dict((data or {}).get("memory") or {})
        except Exception:
            return None

    async def trigger(
        self,
        *,
        content: str,
        mode: str,
        keywords: list[str],
        aliases: list[str],
        mentioned_me: bool,
    ) -> bool | None:
        try:
            data = await self._post(
                "/v1/rules/trigger",
                {
                    "content": content,
                    "mode": mode,
                    "keywords": keywords,
                    "aliases": aliases,
                    "mentioned_me": mentioned_me,
                },
                timeout=3.0,
            )
            if data is None:
                return None
            return bool(data.get("triggered"))
        except Exception:
            return None

    async def limits(
        self,
        *,
        group_id: int,
        config: dict[str, Any],
        timezone_name: str,
    ) -> bool | None:
        try:
            data = await self._post(
                "/v1/rules/limits",
                {
                    "group_id": group_id,
                    "max_replies_per_hour": config.get("max_replies_per_hour"),
                    "quiet_start": config.get("quiet_start"),
                    "quiet_end": config.get("quiet_end"),
                    "timezone": timezone_name,
                },
                timeout=3.0,
            )
            if data is None:
                return None
            return bool(data.get("allowed"))
        except Exception:
            return None

    async def record_reply(self, *, group_id: int, cooldown_seconds: float) -> None:
        try:
            await self._post(
                "/v1/rules/record_reply",
                {"group_id": group_id, "cooldown_seconds": cooldown_seconds},
                timeout=3.0,
            )
        except Exception:
            pass


_sidecar: QunbanSidecar | None = None


def get_qunban_sidecar() -> QunbanSidecar:
    global _sidecar
    if _sidecar is None:
        _sidecar = QunbanSidecar()
    return _sidecar
