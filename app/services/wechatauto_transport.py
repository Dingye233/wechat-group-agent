"""Durable outbound transport boundary for WeChat Agent replies.

The transport owns an explicit job queue instead of letting every Agent/Send
caller drive the WeChat UI directly.  The worker can later be moved into a
separate wechatauto service process without changing the Agent-facing API.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Sticker, TransportJob

if TYPE_CHECKING:
    from app.core.runtime import RuntimeManager

logger = logging.getLogger(__name__)


class TransportUnavailable(RuntimeError):
    pass


class WechatautoTransport:
    def __init__(self, runtime: "RuntimeManager") -> None:
        self.runtime = runtime
        self._queue: asyncio.Queue[int] = asyncio.Queue(maxsize=2000)
        self._worker: asyncio.Task | None = None
        self._futures: dict[int, asyncio.Future] = {}
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _remote(self) -> Any | None:
        adapter = getattr(self.runtime, "adapter", None)
        if (
            getattr(self.runtime, "adapter_id", "") == "replica-remote"
            and callable(getattr(adapter, "submit_outbound", None))
        ):
            return adapter
        return None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._remote() is not None:
            logger.info("wechatauto transport 使用独立服务队列")
            return
        await self._recover()
        self._worker = asyncio.create_task(
            self._worker_loop(), name="wechatauto-transport"
        )
        logger.info("wechatauto transport worker 已启动")

    async def stop(self) -> None:
        self._running = False
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        for future in self._futures.values():
            if not future.done():
                future.set_exception(TransportUnavailable("transport 已停止"))
        self._futures.clear()

    async def _recover(self) -> None:
        """Requeue jobs left queued by a previous service run."""
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(TransportJob)
                    .where(TransportJob.status == "queued")
                    .order_by(TransportJob.id.asc())
                    .limit(100)
                )
            ).scalars().all()
        for row in rows:
            try:
                self._queue.put_nowait(row.id)
            except asyncio.QueueFull:
                break

    async def submit(
        self,
        *,
        group_id: int,
        text: str = "",
        sticker_id: int | None = None,
        files: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        reuse_chat: bool = False,
        idempotency_key: str = "",
        target_wxid: str = "",
    ) -> dict[str, Any]:
        remote = self._remote()
        if remote is not None:
            if not self._running:
                raise TransportUnavailable("wechatauto transport 未启动")
            return await remote.submit_outbound(
                {
                    "group_id": int(group_id),
                    "text": str(text or ""),
                    "sticker_id": sticker_id,
                    "files": [str(Path(item)) for item in files or []],
                    "meta": meta or {},
                    "reuse_chat": bool(reuse_chat),
                    "idempotency_key": str(idempotency_key or ""),
                    "target_wxid": str(target_wxid or ""),
                }
            )
        key = str(
            idempotency_key
            or (meta or {}).get("client_msg_id")
            or uuid.uuid4().hex
        )[:160]
        async with SessionLocal() as db:
            if key:
                existing = (
                    await db.execute(
                        select(TransportJob)
                        .where(TransportJob.idempotency_key == key)
                        .order_by(TransportJob.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return self._job_dict(existing)
            job = TransportJob(
                idempotency_key=key,
                group_id=int(group_id),
                kind="sticker" if sticker_id else "text",
                payload={
                    "text": str(text or ""),
                    "sticker_id": sticker_id,
                    "files": [str(Path(item)) for item in files or []],
                    "meta": meta or {},
                    "reuse_chat": bool(reuse_chat),
                },
                status="queued",
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            payload = self._job_dict(job)
        if not self._running:
            raise TransportUnavailable("wechatauto transport 未启动")
        loop = asyncio.get_running_loop()
        self._futures[int(job.id)] = loop.create_future()
        self._queue.put_nowait(int(job.id))
        self._broadcast("queued", payload)
        return payload

    async def wait(self, job_id: int, timeout: float = 180.0) -> dict[str, Any]:
        remote = self._remote()
        if remote is not None:
            deadline = time.monotonic() + max(1.0, float(timeout))
            while time.monotonic() < deadline:
                job = await remote.get_outbound(int(job_id))
                if job.get("status") in {"sent", "failed"}:
                    return job
                await asyncio.sleep(0.2)
            raise TransportUnavailable("发送任务等待超时")
        future = self._futures.get(int(job_id))
        if future is None:
            # Job may have been submitted by a previous process or recovered;
            # wait by polling its durable row instead of returning immediately.
            deadline = time.monotonic() + max(1.0, timeout)
            while time.monotonic() < deadline:
                job = await self._load_job(int(job_id))
                if job.get("status") in {"sent", "failed"}:
                    return job
                await asyncio.sleep(0.1)
            raise TransportUnavailable("发送任务等待超时")
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TransportUnavailable("发送任务等待超时") from exc
        return await self._load_job(int(job_id))

    async def job(self, job_id: int) -> dict[str, Any] | None:
        remote = self._remote()
        if remote is not None:
            try:
                return await remote.get_outbound(int(job_id))
            except Exception:
                return None
        async with SessionLocal() as db:
            row = await db.get(TransportJob, int(job_id))
            return self._job_dict(row) if row else None

    async def retry_failed_private(self) -> dict[str, Any]:
        remote = self._remote()
        if remote is None or not callable(
            getattr(remote, "retry_failed_private", None)
        ):
            return {"jobs": [], "count": 0}
        return await remote.retry_failed_private()

    async def requeue(self, job_id: int) -> dict[str, Any]:
        remote = self._remote()
        if remote is not None:
            return await remote.retry_outbound(int(job_id))
        """Manually retry a failed job after the user accepts the risk.

        Automatic retry is intentionally disabled for uncertain sends: the
        first attempt may have reached WeChat even though the caller saw an
        exception.  A human-triggered retry first checks the outbox.
        """
        async with SessionLocal() as db:
            row = await db.get(TransportJob, int(job_id))
            if row is None:
                raise TransportUnavailable("发送任务不存在")
            if row.status not in {"failed"}:
                raise TransportUnavailable("只有失败任务可以重试")
            row.status = "queued"
            row.error = ""
            row.result = {}
            row.finished_at = None
            row.started_at = None
            await db.commit()
            await db.refresh(row)
            payload = self._job_dict(row)
        if not self._running:
            raise TransportUnavailable("wechatauto transport 未启动")
        loop = asyncio.get_running_loop()
        self._futures[int(job_id)] = loop.create_future()
        self._queue.put_nowait(int(job_id))
        self._broadcast("queued", payload)
        return payload

    async def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        remote = self._remote()
        if remote is not None:
            data = await remote.list_outbound(limit)
            return list(data.get("jobs") or [])
        limit = max(1, min(200, int(limit)))
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(TransportJob)
                    .order_by(TransportJob.id.desc())
                    .limit(limit)
                )
            ).scalars().all()
            return [self._job_dict(row) for row in rows]

    async def stats(self) -> dict[str, int]:
        remote = self._remote()
        if remote is not None:
            return await remote.stats_outbound()
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(TransportJob.status, func.count(TransportJob.id))
                    .group_by(TransportJob.status)
                )
            ).all()
        result = {"queued": 0, "sending": 0, "sent": 0, "failed": 0}
        for status, count in rows:
            result[str(status)] = int(count)
        return result

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._run_job(int(job_id))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("transport worker 处理任务异常")

    async def _run_job(self, job_id: int) -> None:
        async with SessionLocal() as db:
            row = await db.get(TransportJob, job_id)
            if row is None or row.status not in {"queued", "sending"}:
                self._resolve(job_id, None)
                return
            row.status = "sending"
            row.attempts = int(row.attempts or 0) + 1
            row.started_at = row.started_at or datetime.utcnow()
            await db.commit()
            payload = dict(row.payload or {})
            attempts = int(row.attempts or 1)
            group_id = int(row.group_id or 0)
        self._broadcast("sending", await self._load_job(job_id))
        sticker = None
        sticker_id = payload.get("sticker_id")
        if sticker_id:
            async with SessionLocal() as db:
                sticker = await db.get(Sticker, int(sticker_id))
        try:
            if group_id <= 0:
                raise ValueError("transport job 缺少目标群")
            started = time.perf_counter()
            sent = await self.runtime._send_agent_message_now(
                group_id,
                text=str(payload.get("text") or ""),
                sticker=sticker,
                meta=dict(payload.get("meta") or {}),
                files=[Path(item) for item in payload.get("files") or []],
                reuse_chat=bool(payload.get("reuse_chat")),
            )
            elapsed = int((time.perf_counter() - started) * 1000)
            async with SessionLocal() as db:
                row = await db.get(TransportJob, job_id)
                if row is not None:
                    row.status = "sent" if sent else "failed"
                    row.result = {"sent": bool(sent), "elapsed_ms": elapsed}
                    row.error = "" if sent else "发送未确认"
                    row.finished_at = datetime.utcnow()
                    await db.commit()
            self._broadcast("sent", await self._load_job(job_id))
            self._resolve(job_id, True)
            return
        except Exception as exc:
            async with SessionLocal() as db:
                row = await db.get(TransportJob, job_id)
                if row is not None:
                    row.error = (str(exc).strip() or type(exc).__name__)[:2000]
                    row.status = "failed"
                    row.result = {
                        "uncertain": True,
                        "retryable": True,
                        "attempts": attempts,
                    }
                    row.finished_at = datetime.utcnow()
                    await db.commit()
            self._broadcast("failed", await self._load_job(job_id))
            self._resolve(job_id, False)
            logger.warning(
                "transport job %s 发送异常，已停止自动重试（避免重复发送）: %s",
                job_id,
                exc,
            )

    def _resolve(self, job_id: int, value: Any) -> None:
        future = self._futures.pop(job_id, None)
        if future is not None and not future.done():
            future.set_result(value)

    async def _load_job(self, job_id: int) -> dict[str, Any]:
        async with SessionLocal() as db:
            row = await db.get(TransportJob, job_id)
            if row is None:
                raise TransportUnavailable("发送任务不存在")
            return self._job_dict(row)

    @staticmethod
    def _job_dict(row: TransportJob) -> dict[str, Any]:
        return {
            "id": row.id,
            "idempotency_key": row.idempotency_key,
            "group_id": row.group_id,
            "kind": row.kind,
            "payload": row.payload or {},
            "status": row.status,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "error": row.error,
            "result": row.result or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }

    def _broadcast(self, action: str, job: dict[str, Any]) -> None:
        try:
            self.runtime.broadcast("transport.job", {"action": action, "job": job})
        except Exception:
            pass
        try:
            from app.services import wechatauto_events

            wechatauto_events.publish(
                f"outbound.{action}",
                {
                    "job_id": job.get("id"),
                    "group_id": job.get("group_id"),
                    "status": job.get("status"),
                    "attempts": job.get("attempts"),
                    "error": job.get("error") or "",
                },
            )
        except Exception:
            pass
