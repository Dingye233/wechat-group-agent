"""Standalone wechatauto transport service.

Run separately from the Qunban API:

    python scripts/wechatauto_service.py --backend replica --port 8010

The service owns exactly one adapter.  Qunban connects to it through the
``replica-remote`` adapter and never starts a second WeChat adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from app.adapters import create_adapter
from app.adapters.base import AdapterEvent
from scripts.wechatauto_queue import ServiceQueue


class ServiceState:
    def __init__(self, backend: str, token: str = "") -> None:
        self.backend = backend
        self.token = token
        self.adapter: Any = None
        self.queue: ServiceQueue | None = None
        self.events: deque[dict[str, Any]] = deque(maxlen=2000)
        self.seq = 0
        self.lock = threading.Lock()
        self.status: dict[str, Any] = {"state": "offline", "message": ""}
        self.account: dict[str, Any] = {}
        self.started_at = time.time()

    def emit(self, event: AdapterEvent) -> None:
        data = dict(event.data or {})
        if event.type == "groups":
            data["groups"] = [
                {
                    "external_id": getattr(item, "external_id", ""),
                    "name": getattr(item, "name", ""),
                    "member_count": getattr(item, "member_count", 0),
                    "avatar": getattr(item, "avatar", ""),
                    "meta": getattr(item, "meta", {}),
                }
                for item in data.get("groups") or []
            ]
        if event.type == "members":
            data["members"] = [
                {
                    "wxid": getattr(item, "wxid", ""),
                    "nickname": getattr(item, "nickname", ""),
                    "alias": getattr(item, "alias", ""),
                    "is_owner": getattr(item, "is_owner", False),
                    "is_admin": getattr(item, "is_admin", False),
                }
                for item in data.get("members") or []
            ]
        if event.type == "status":
            self.status = {
                "state": str(data.get("state") or "offline"),
                "message": str(data.get("message") or ""),
            }
        if event.type == "account":
            self.account = {
                "wxid": str(data.get("wxid") or ""),
                "name": str(data.get("name") or ""),
                "avatar": str(data.get("avatar") or ""),
            }
        with self.lock:
            self.seq += 1
            self.events.append(
                {"seq": self.seq, "type": event.type, "data": data}
            )

    async def start(self) -> None:
        self.adapter = create_adapter(self.backend, self.emit)
        await self.adapter.start()
        self.queue = ServiceQueue(
            self.adapter,
            PROJECT / "data" / "wechatauto-queue.db",
            group_db_path=PROJECT / "data" / "agent.db",
        )
        await self.queue.start()

    async def stop(self) -> None:
        if self.queue is not None:
            await self.queue.stop()
            self.queue = None
        if self.adapter is not None:
            await self.adapter.stop()
            self.adapter = None


state: ServiceState | None = None


def _auth(authorization: str = Header(default="")) -> None:
    if state is None:
        raise HTTPException(status_code=503, detail="service not ready")
    if state.token and authorization != f"Bearer {state.token}":
        raise HTTPException(status_code=401, detail="unauthorized")


def create_app(backend: str, token: str = "") -> FastAPI:
    global state
    state = ServiceState(backend=backend, token=token)
    app = FastAPI(title="qunban wechatauto transport")

    @app.on_event("startup")
    async def _startup() -> None:
        assert state is not None
        await state.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if state is not None:
            await state.stop()

    @app.get("/v1/status")
    async def status() -> dict[str, Any]:
        assert state is not None
        adapter = state.adapter
        return {
            "backend": state.backend,
            "status": {
                "state": state.status.get("state") or "offline",
                "message": state.status.get("message") or "",
            },
            "account": state.account,
            "pid": os.getpid(),
            "adapter": getattr(adapter, "adapter_id", ""),
            "uptime": int(time.time() - state.started_at),
            "seq": state.seq,
        }

    @app.get("/v1/events")
    async def events(
        since: int = 0, timeout: float = 0.8
    ) -> dict[str, Any]:
        assert state is not None
        end = time.monotonic() + max(0.05, min(3.0, float(timeout)))
        while True:
            with state.lock:
                rows = [event for event in state.events if int(event["seq"]) > since]
            if rows or time.monotonic() >= end:
                return {"events": rows, "seq": state.seq}
            await asyncio.sleep(0.08)

    @app.post("/v1/outbound", dependencies=[Depends(_auth)])
    async def outbound_submit(payload: dict[str, Any]) -> dict[str, Any]:
        assert state is not None and state.queue is not None
        job = await state.queue.submit(
            group_id=int(payload.get("group_id") or 0),
            text=str(payload.get("text") or ""),
            sticker_id=payload.get("sticker_id"),
            files=[str(item) for item in payload.get("files") or []],
            meta=dict(payload.get("meta") or {}),
            reuse_chat=bool(payload.get("reuse_chat")),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            target_wxid=str(payload.get("target_wxid") or ""),
        )
        return {"job": job}

    @app.get("/v1/outbound", dependencies=[Depends(_auth)])
    async def outbound_list(limit: int = 50) -> dict[str, Any]:
        assert state is not None and state.queue is not None
        return {
            "jobs": await state.queue.list(limit),
            "stats": await state.queue.stats(),
        }

    @app.get("/v1/outbound/stats", dependencies=[Depends(_auth)])
    async def outbound_stats() -> dict[str, Any]:
        assert state is not None and state.queue is not None
        return await state.queue.stats()

    @app.get("/v1/outbound/{job_id}", dependencies=[Depends(_auth)])
    async def outbound_get(job_id: int) -> dict[str, Any]:
        assert state is not None and state.queue is not None
        job = await state.queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="发送任务不存在")
        return {"job": job}

    @app.post("/v1/outbound/{job_id}/retry", dependencies=[Depends(_auth)])
    async def outbound_retry(job_id: int) -> dict[str, Any]:
        assert state is not None and state.queue is not None
        try:
            job = await state.queue.retry(job_id)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job": job}

    @app.post("/v1/outbound/retry_failed_private", dependencies=[Depends(_auth)])
    async def outbound_retry_failed_private() -> dict[str, Any]:
        assert state is not None and state.queue is not None
        jobs = await state.queue.retry_failed_private()
        return {"jobs": jobs, "count": len(jobs)}

    @app.get("/v1/groups", dependencies=[Depends(_auth)])
    async def groups() -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        rows = await state.adapter.list_groups()
        return {
            "groups": [
                {
                    "external_id": getattr(item, "external_id", ""),
                    "name": getattr(item, "name", ""),
                    "member_count": getattr(item, "member_count", 0),
                    "avatar": getattr(item, "avatar", ""),
                    "meta": getattr(item, "meta", {}),
                }
                for item in rows
            ]
        }

    @app.get("/v1/members", dependencies=[Depends(_auth)])
    async def members(room_id: str, include_history: bool = True) -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        rows = await state.adapter.get_members(
            room_id, include_history=include_history
        )
        return {
            "members": [
                {
                    "wxid": getattr(item, "wxid", ""),
                    "nickname": getattr(item, "nickname", ""),
                    "alias": getattr(item, "alias", ""),
                    "is_owner": getattr(item, "is_owner", False),
                    "is_admin": getattr(item, "is_admin", False),
                }
                for item in rows
            ]
        }

    @app.get("/v1/account", dependencies=[Depends(_auth)])
    async def account() -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        item = await state.adapter.get_account()
        return {
            "account": {
                "wxid": getattr(item, "wxid", ""),
                "name": getattr(item, "name", ""),
                "avatar": getattr(item, "avatar", ""),
            }
            if item
            else {}
        }

    @app.get("/v1/contacts", dependencies=[Depends(_auth)])
    async def contacts() -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        loader = getattr(state.adapter, "list_contacts", None)
        if not callable(loader):
            return {"contacts": []}
        try:
            rows = await loader()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"contacts": list(rows or [])}

    @app.get("/v1/contacts/{wxid}/messages", dependencies=[Depends(_auth)])
    async def contact_messages(wxid: str, limit: int = 100) -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        loader = getattr(state.adapter, "get_contact_messages", None)
        if not callable(loader):
            return {"messages": []}
        try:
            rows = await loader(wxid, max(1, min(300, int(limit))))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"messages": list(rows or [])}

    @app.post("/v1/admin_contacts", dependencies=[Depends(_auth)])
    async def admin_contacts(payload: dict[str, Any]) -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        setter = getattr(state.adapter, "set_admin_contacts", None)
        if callable(setter):
            setter({str(item) for item in payload.get("wxids") or [] if str(item)})
        return {"ok": True, "count": len(payload.get("wxids") or [])}

    @app.post("/v1/send_text", dependencies=[Depends(_auth)])
    async def send_text(payload: dict[str, Any]) -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        try:
            await state.adapter.send_text(
                str(payload.get("room_id") or ""),
                str(payload.get("text") or ""),
                reuse_chat=bool(payload.get("reuse_chat")),
            )
        except TypeError:
            await state.adapter.send_text(
                str(payload.get("room_id") or ""), str(payload.get("text") or "")
            )
        return {"ok": True}

    @app.post("/v1/send_sticker", dependencies=[Depends(_auth)])
    async def send_sticker(payload: dict[str, Any]) -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        path = Path(str(payload.get("path") or ""))
        try:
            await state.adapter.send_sticker(
                str(payload.get("room_id") or ""),
                path,
                reuse_chat=bool(payload.get("reuse_chat")),
            )
        except TypeError:
            await state.adapter.send_sticker(
                str(payload.get("room_id") or ""), path
            )
        return {"ok": True}

    @app.post("/v1/send_file", dependencies=[Depends(_auth)])
    async def send_file(payload: dict[str, Any]) -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        await state.adapter.send_file(
            str(payload.get("room_id") or ""),
            Path(str(payload.get("path") or "")),
        )
        return {"ok": True}

    @app.post("/v1/download_media", dependencies=[Depends(_auth)])
    async def download_media(payload: dict[str, Any]) -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        result = await state.adapter.download_media(
            str(payload.get("room_id") or ""),
            dict(payload.get("meta") or {}),
            tier=str(payload.get("tier") or "best"),
            original=bool(payload.get("original")),
        )
        return {"result": result}

    @app.post("/v1/refresh", dependencies=[Depends(_auth)])
    async def refresh() -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        await state.adapter.refresh()
        return {"ok": True}

    @app.post("/v1/preflight", dependencies=[Depends(_auth)])
    async def preflight(payload: dict[str, Any]) -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        try:
            result = await state.adapter.preflight(
                str(payload.get("room_id") or "")
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"{type(exc).__name__}: {exc}".strip(),
            ) from exc
        return {"result": dict(result or {})}

    @app.post("/v1/config", dependencies=[Depends(_auth)])
    async def config(payload: dict[str, Any]) -> dict[str, Any]:
        assert state is not None and state.adapter is not None
        if "readable_groups" in payload:
            state.adapter.set_readable_groups(
                {str(item) for item in payload.get("readable_groups") or []}
            )
        if "read_schedule" in payload:
            state.adapter.set_read_schedule(
                {
                    str(key): float(value)
                    for key, value in (payload.get("read_schedule") or {}).items()
                }
            )
        if "poll_interval" in payload:
            state.adapter.set_poll_interval(float(payload["poll_interval"]))
        if "media_download" in payload:
            state.adapter.set_media_download_enabled(bool(payload["media_download"]))
        return {"ok": True}

    @app.post("/v1/stop", dependencies=[Depends(_auth)])
    async def stop() -> dict[str, Any]:
        assert state is not None
        await state.stop()
        return {"ok": True}

    @app.exception_handler(Exception)
    async def _error(_request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["replica", "mock"], default="replica")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--token", default=os.environ.get("WECHATAUTO_SERVICE_TOKEN", ""))
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(
        create_app(args.backend, args.token),
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
