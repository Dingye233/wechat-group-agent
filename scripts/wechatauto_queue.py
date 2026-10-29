"""Outbound queue owned by the standalone wechatauto service.

The queue is intentionally small and SQLite-backed so a service restart can
recover queued work.  Sending is serialized by one worker, matching WeChat UI
automation's single-writer constraint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ServiceQueue:
    def __init__(
        self,
        adapter: Any,
        db_path: Path,
        *,
        group_db_path: Path | None = None,
        sticker_dir: Path | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.adapter = adapter
        self.db_path = db_path
        self.group_db_path = group_db_path
        self.sticker_dir = sticker_dir or (db_path.parent / "stickers")
        self.on_event = on_event
        self.queue: asyncio.Queue[int] = asyncio.Queue(maxsize=2000)
        self.worker: asyncio.Task | None = None
        self._started = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def init(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT,
                    group_id INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT DEFAULT '',
                    result TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS ix_outbound_jobs_status "
                "ON outbound_jobs (status, id)"
            )
            con.commit()

    async def start(self) -> None:
        if self._started:
            return
        self.init()
        self._started = True
        with self._connect() as con:
            rows = con.execute(
                "SELECT id FROM outbound_jobs WHERE status IN ('queued','sending') "
                "ORDER BY id ASC LIMIT 200"
            ).fetchall()
        for row in rows:
            try:
                self.queue.put_nowait(int(row["id"]))
            except asyncio.QueueFull:
                break
        self.worker = asyncio.create_task(self._worker_loop(), name="wechatauto-outbound")

    async def stop(self) -> None:
        self._started = False
        if self.worker is not None:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
            self.worker = None

    def _job(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "idempotency_key": row["idempotency_key"] or "",
            "group_id": int(row["group_id"]),
            "payload": json.loads(row["payload"] or "{}"),
            "status": row["status"],
            "attempts": int(row["attempts"] or 0),
            "max_attempts": 1,
            "error": row["error"] or "",
            "result": json.loads(row["result"] or "{}"),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def _event(self, action: str, job: dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(action, job)
            except Exception:
                pass

    def _sticker_path(self, sticker_id: int) -> Path | None:
        if not self.group_db_path or not self.group_db_path.is_file():
            return None
        try:
            con = sqlite3.connect(self.group_db_path)
            row = con.execute(
                "SELECT filename FROM stickers WHERE id=?", (int(sticker_id),)
            ).fetchone()
            con.close()
            if row and row[0]:
                path = self.sticker_dir / str(row[0])
                return path if path.is_file() else None
        except Exception:
            return None
        return None

    def _external_id(self, group_id: int, payload: dict[str, Any]) -> str:
        explicit = str((payload.get("meta") or {}).get("external_id") or "")
        if explicit:
            return explicit
        if self.group_db_path and self.group_db_path.is_file():
            try:
                con = sqlite3.connect(self.group_db_path)
                row = con.execute(
                    "SELECT external_id FROM groups WHERE id=?", (int(group_id),)
                ).fetchone()
                con.close()
                if row and row[0]:
                    return str(row[0])
            except Exception:
                pass
        raise RuntimeError(f"找不到群 {group_id} 的微信 external_id")

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
    ) -> dict[str, Any]:
        key = str(idempotency_key or (meta or {}).get("client_msg_id") or uuid.uuid4().hex)[:160]
        payload = {
            "text": str(text or ""),
            "sticker_id": sticker_id,
            "files": [str(Path(item)) for item in files or []],
            "meta": meta or {},
            "reuse_chat": bool(reuse_chat),
        }
        with self._connect() as con:
            if key:
                row = con.execute(
                    "SELECT * FROM outbound_jobs WHERE idempotency_key=? "
                    "ORDER BY id DESC LIMIT 1",
                    (key,),
                ).fetchone()
                if row is not None:
                    return self._job(row)
            cur = con.execute(
                "INSERT INTO outbound_jobs "
                "(idempotency_key, group_id, payload, status, created_at) "
                "VALUES (?, ?, ?, 'queued', ?)",
                (key, int(group_id), json.dumps(payload, ensure_ascii=False), time.time()),
            )
            con.commit()
            job_id = int(cur.lastrowid)
            row = con.execute(
                "SELECT * FROM outbound_jobs WHERE id=?", (job_id,)
            ).fetchone()
        job = self._job(row)
        self._event("queued", job)
        await self.queue.put(job_id)
        return job

    async def get(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM outbound_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
        return self._job(row) if row else None

    async def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM outbound_jobs ORDER BY id DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [self._job(row) for row in rows]

    async def stats(self) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT status, count(*) AS n FROM outbound_jobs GROUP BY status"
            ).fetchall()
        out = {"queued": 0, "sending": 0, "sent": 0, "failed": 0}
        for row in rows:
            out[str(row["status"])] = int(row["n"])
        return out

    async def wait(self, job_id: int, timeout: float = 180.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            job = await self.get(job_id)
            if job is None:
                raise RuntimeError("发送任务不存在")
            if job["status"] in {"sent", "failed"}:
                return job
            await asyncio.sleep(0.2)
        raise TimeoutError("发送任务等待超时")

    async def retry(self, job_id: int) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM outbound_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
            if row is None:
                raise RuntimeError("发送任务不存在")
            if row["status"] != "failed":
                raise RuntimeError("只有失败任务可以重试")
            con.execute(
                "UPDATE outbound_jobs SET status='queued', error='', result='{}', "
                "finished_at=NULL, started_at=NULL WHERE id=?",
                (int(job_id),),
            )
            con.commit()
            row = con.execute(
                "SELECT * FROM outbound_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
        job = self._job(row)
        self._event("queued", job)
        await self.queue.put(int(job_id))
        return job

    async def _worker_loop(self) -> None:
        while self._started:
            try:
                job_id = await self.queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._run(int(job_id))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("wechatauto outbound worker error")

    async def _run(self, job_id: int) -> None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM outbound_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
            if row is None or row["status"] not in {"queued", "sending"}:
                return
            con.execute(
                "UPDATE outbound_jobs SET status='sending', attempts=attempts+1, "
                "started_at=COALESCE(started_at, ?) WHERE id=?",
                (time.time(), int(job_id)),
            )
            con.commit()
            row = con.execute(
                "SELECT * FROM outbound_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
        job = self._job(row)
        self._event("sending", job)
        payload = job["payload"]
        started = time.perf_counter()
        try:
            if int(job["group_id"]) <= 0:
                raise RuntimeError("缺少目标群")
            external_id = self._external_id(int(job["group_id"]), payload)
            text = str(payload.get("text") or "")
            files = [Path(item) for item in payload.get("files") or []]
            sticker_id = int(payload.get("sticker_id") or 0)
            if text.strip():
                try:
                    await self.adapter.send_text(
                        external_id,
                        text,
                        reuse_chat=bool(payload.get("reuse_chat")),
                    )
                except TypeError:
                    await self.adapter.send_text(external_id, text)
            if sticker_id:
                sticker_path = self._sticker_path(sticker_id)
                if sticker_path is None:
                    raise RuntimeError(f"表情包文件不存在：{sticker_id}")
                await self.adapter.send_sticker(external_id, sticker_path)
            for path in files:
                await self.adapter.send_file(external_id, path)
            result = {
                "sent": True,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
            with self._connect() as con:
                con.execute(
                    "UPDATE outbound_jobs SET status='sent', result=?, error='', "
                    "finished_at=? WHERE id=?",
                    (json.dumps(result), time.time(), int(job_id)),
                )
                con.commit()
            self._event("sent", await self.get(job_id) or job)
        except Exception as exc:
            error = str(exc).strip() or type(exc).__name__
            with self._connect() as con:
                con.execute(
                    "UPDATE outbound_jobs SET status='failed', error=?, "
                    "result=?, finished_at=? WHERE id=?",
                    (
                        error[:2000],
                        json.dumps({"uncertain": True, "retryable": True}),
                        time.time(),
                        int(job_id),
                    ),
                )
                con.commit()
            self._event("failed", await self.get(job_id) or job)
            logger.warning("wechatauto outbound %s failed: %s", job_id, error)
