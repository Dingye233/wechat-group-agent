"""Remote adapter for an independently running wechatauto service.

The service owns the real wechatauto/WeChat connection.  Qunban only polls its
event stream and calls its send/query API, so the transport process can be
restarted without restarting the Agent.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.adapters.base import (
    AccountInfo,
    AdapterEvent,
    BaseAdapter,
    GroupInfo,
    MemberInfo,
)


class RemoteAdapter(BaseAdapter):
    adapter_id = "replica-remote"
    display_name = "微信 4.x（wechatauto 独立服务）"
    description = "通过本地 HTTP 服务读取和发送消息；服务进程独占微信适配器。"

    def __init__(self, emit, options: dict[str, Any] | None = None) -> None:
        super().__init__(emit, options)
        self.base_url = str(
            (options or {}).get("base_url")
            or os.environ.get("WECHATAUTO_SERVICE_URL")
            or "http://127.0.0.1:8010"
        ).rstrip("/")
        self.token = str(
            (options or {}).get("token")
            or os.environ.get("WECHATAUTO_SERVICE_TOKEN")
            or ""
        )
        self._client: httpx.AsyncClient | None = None
        self._poll: asyncio.Task | None = None
        self._seq = 0
        self._groups: list[GroupInfo] = []
        self._account: AccountInfo | None = None
        self._last_error = ""
        self._connected = False

    @classmethod
    def availability(cls) -> tuple[bool, str]:
        # Discovery is dynamic; the service itself validates wechatauto.
        return True, ""

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(120.0, connect=5.0),
                headers=self._headers(),
            )
        return self._client

    async def start(self) -> None:
        self._stop.clear()
        client = self._http()
        try:
            status = (await client.get("/v1/status")).json()
        except Exception as exc:
            self._last_error = str(exc)
            self.emit("error", message=f"wechatauto 服务不可用：{exc}")
            raise RuntimeError(f"wechatauto 服务不可用：{exc}") from exc
        account = status.get("account")
        if isinstance(account, dict) and account.get("wxid"):
            self._account = AccountInfo(
                wxid=str(account.get("wxid") or ""),
                name=str(account.get("name") or ""),
                avatar=str(account.get("avatar") or ""),
            )
        self.emit("status", **dict(status.get("status") or {}))
        if self._account is not None:
            self.emit(
                "account",
                wxid=self._account.wxid,
                name=self._account.name,
                avatar=self._account.avatar,
            )
        try:
            groups = await self.list_groups()
            if groups:
                self.emit("groups", groups=groups)
        except Exception:
            pass
        self._poll = asyncio.create_task(
            self._poll_loop(), name="wechatauto-remote-events"
        )
        self._connected = True

    async def stop(self) -> None:
        self._stop.set()
        if self._poll is not None:
            self._poll.cancel()
            try:
                await self._poll
            except asyncio.CancelledError:
                pass
            self._poll = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _refresh_snapshot(self) -> None:
        status = (await self._http().get("/v1/status", timeout=10.0)).json()
        account = status.get("account")
        if isinstance(account, dict) and account.get("wxid"):
            self._account = AccountInfo(
                wxid=str(account.get("wxid") or ""),
                name=str(account.get("name") or ""),
                avatar=str(account.get("avatar") or ""),
            )
            self.emit(
                "account",
                wxid=self._account.wxid,
                name=self._account.name,
                avatar=self._account.avatar,
            )
        self.emit("status", **dict(status.get("status") or {}))
        groups = await self.list_groups()
        if groups:
            self.emit("groups", groups=groups)

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                response = await self._http().get(
                    "/v1/events",
                    params={"since": self._seq, "timeout": 0.8},
                    timeout=5.0,
                )
                response.raise_for_status()
                data = response.json()
                server_seq = int(data.get("seq") or 0)
                if not self._connected or server_seq < self._seq:
                    # The service restarted and its sequence reset.  Re-sync
                    # status/account/groups before consuming new events.
                    self._seq = 0
                    self._connected = True
                    await self._refresh_snapshot()
                    continue
                for item in data.get("events") or []:
                    try:
                        seq = int(item.get("seq") or 0)
                        if seq > self._seq:
                            self._seq = seq
                        event_type = str(item.get("type") or "log")
                        payload = dict(item.get("data") or {})
                        if event_type == "groups":
                            payload["groups"] = [
                                GroupInfo(
                                    external_id=str(row.get("external_id") or ""),
                                    name=str(row.get("name") or ""),
                                    member_count=int(row.get("member_count") or 0),
                                    avatar=str(row.get("avatar") or ""),
                                    meta=dict(row.get("meta") or {}),
                                )
                                for row in payload.get("groups") or []
                                if isinstance(row, dict)
                            ]
                        elif event_type == "members":
                            payload["members"] = [
                                MemberInfo(
                                    wxid=str(row.get("wxid") or ""),
                                    nickname=str(row.get("nickname") or ""),
                                    alias=str(row.get("alias") or ""),
                                    is_owner=bool(row.get("is_owner")),
                                    is_admin=bool(row.get("is_admin")),
                                )
                                for row in payload.get("members") or []
                                if isinstance(row, dict)
                            ]
                        self.emit(event_type, **payload)
                    except Exception:
                        continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                if self._connected:
                    self._connected = False
                    self.emit(
                        "status",
                        state="offline",
                        message=f"wechatauto 服务断开：{exc}",
                    )
                self.emit("log", message=f"wechatauto 事件流断开：{exc}", level="warn")
                await asyncio.sleep(1.5)

    async def list_contacts(self) -> list[dict[str, Any]]:
        response = await self._http().get("/v1/contacts")
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )
        return list(response.json().get("contacts") or [])

    async def get_contact_messages(
        self, wxid: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        response = await self._http().get(
            f"/v1/contacts/{quote(str(wxid), safe='')}/messages",
            params={"limit": max(1, min(300, int(limit)))},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )
        return list(response.json().get("messages") or [])

    def set_admin_contacts(self, wxids: set[str]) -> None:
        """Push admin DM listen targets to the service without blocking sync."""
        values = [str(item) for item in (wxids or set()) if str(item)]

        async def push() -> None:
            try:
                await self._http().post("/v1/admin_contacts", json={"wxids": values})
            except Exception:
                pass

        try:
            asyncio.get_running_loop().create_task(push())
        except RuntimeError:
            pass

    async def send_text(self, room_id: str, text: str, *, reuse_chat: bool = False) -> None:
        response = await self._http().post(
            "/v1/send_text",
            json={"room_id": room_id, "text": text, "reuse_chat": reuse_chat},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )

    async def send_sticker(self, room_id: str, path: Path, *, reuse_chat: bool = False) -> None:
        response = await self._http().post(
            "/v1/send_sticker",
            json={"room_id": room_id, "path": str(path), "reuse_chat": reuse_chat},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )

    async def send_file(self, room_id: str, path: Path) -> None:
        response = await self._http().post(
            "/v1/send_file",
            json={"room_id": room_id, "path": str(path)},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )

    async def download_media(
        self,
        group_id_external: str,
        message_meta: dict[str, Any],
        *,
        tier: str = "best",
        original: bool = False,
    ) -> dict[str, Any]:
        response = await self._http().post(
            "/v1/download_media",
            json={
                "room_id": group_id_external,
                "meta": message_meta,
                "tier": tier,
                "original": original,
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )
        return dict(response.json().get("result") or {})

    async def list_groups(self) -> list[GroupInfo]:
        response = await self._http().get("/v1/groups")
        response.raise_for_status()
        rows = response.json().get("groups") or []
        self._groups = [
            GroupInfo(
                external_id=str(row.get("external_id") or ""),
                name=str(row.get("name") or ""),
                member_count=int(row.get("member_count") or 0),
                avatar=str(row.get("avatar") or ""),
                meta=dict(row.get("meta") or {}),
            )
            for row in rows
            if isinstance(row, dict)
        ]
        return list(self._groups)

    async def get_members(
        self, room_id: str, include_history: bool = True
    ) -> list[MemberInfo]:
        response = await self._http().get(
            "/v1/members",
            params={"room_id": room_id, "include_history": include_history},
        )
        response.raise_for_status()
        return [
            MemberInfo(
                wxid=str(row.get("wxid") or ""),
                nickname=str(row.get("nickname") or ""),
                alias=str(row.get("alias") or ""),
                is_owner=bool(row.get("is_owner")),
                is_admin=bool(row.get("is_admin")),
            )
            for row in response.json().get("members") or []
            if isinstance(row, dict)
        ]

    async def get_account(self) -> AccountInfo | None:
        response = await self._http().get("/v1/account")
        response.raise_for_status()
        row = response.json().get("account") or {}
        if not row.get("wxid"):
            return None
        self._account = AccountInfo(
            wxid=str(row.get("wxid") or ""),
            name=str(row.get("name") or ""),
            avatar=str(row.get("avatar") or ""),
        )
        return self._account

    async def refresh(self) -> None:
        await self._http().post("/v1/refresh")

    async def preflight(self, room_id: str) -> dict[str, Any]:
        response = await self._http().post(
            "/v1/preflight", json={"room_id": room_id}, timeout=100.0
        )
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )
        return dict(response.json().get("result") or {})

    async def submit_outbound(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._http().post(
            "/v1/outbound", json=payload, timeout=30.0
        )
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )
        return dict(response.json().get("job") or {})

    async def get_outbound(self, job_id: int) -> dict[str, Any]:
        response = await self._http().get(f"/v1/outbound/{int(job_id)}")
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )
        return dict(response.json().get("job") or {})

    async def list_outbound(self, limit: int = 50) -> dict[str, Any]:
        response = await self._http().get(
            "/v1/outbound", params={"limit": int(limit)}
        )
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )
        return dict(response.json())

    async def stats_outbound(self) -> dict[str, int]:
        response = await self._http().get("/v1/outbound/stats")
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )
        return dict(response.json())

    async def retry_outbound(self, job_id: int) -> dict[str, Any]:
        response = await self._http().post(f"/v1/outbound/{int(job_id)}/retry")
        if response.status_code >= 400:
            raise RuntimeError(
                str(response.json().get("detail") or response.text)[:1000]
            )
        return dict(response.json().get("job") or {})

    async def _config(self, payload: dict[str, Any]) -> None:
        await self._http().post("/v1/config", json=payload)

    def set_readable_groups(self, room_ids: set[str]) -> None:
        asyncio.create_task(self._config({"readable_groups": sorted(room_ids)}))

    def set_read_schedule(self, schedule: dict[str, float]) -> None:
        asyncio.create_task(
            self._config({"read_schedule": {str(k): float(v) for k, v in schedule.items()}})
        )

    def set_poll_interval(self, seconds: float) -> None:
        asyncio.create_task(self._config({"poll_interval": float(seconds)}))

    def set_media_download_enabled(self, enabled: bool) -> None:
        asyncio.create_task(self._config({"media_download": bool(enabled)}))
