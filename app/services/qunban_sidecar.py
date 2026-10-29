"""HTTP client for the Qunban sidecar (memory/knowledge/context reads)."""

from __future__ import annotations

import os
from typing import Any

import httpx


class QunbanSidecar:
    def __init__(self, base_url: str = "") -> None:
        self.base_url = str(
            base_url or os.environ.get("QUNBAN_SIDECAR_URL") or ""
        ).rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    async def _post(self, path: str, payload: dict[str, Any], timeout: float = 8.0) -> Any:
        if not self.enabled:
            return None
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
            response = await client.post(path, json=payload)
            response.raise_for_status()
            return response.json()

    async def health(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=3.0) as client:
                response = await client.get("/v1/health")
                response.raise_for_status()
                return dict(response.json())
        except Exception as exc:
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
            return list((data or {}).get("results") or [])
        except Exception:
            return None

    async def save_memory(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            data = await self._post("/v1/memory/save", payload)
            return dict((data or {}).get("memory") or {})
        except Exception:
            return None


_sidecar: QunbanSidecar | None = None


def get_qunban_sidecar() -> QunbanSidecar:
    global _sidecar
    if _sidecar is None:
        _sidecar = QunbanSidecar()
    return _sidecar
