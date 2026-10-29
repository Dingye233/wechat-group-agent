"""End-to-end chat test against the mock adapter.

Uses a temporary DATA_DIR, copies only the LLM/settings needed from the real
database, starts a fresh RuntimeManager, emits a mention into the mock group,
and waits for a mock send.  No real WeChat window is touched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
PROD_DB = PROJECT / "data" / "agent.db"
COPY_KEYS = {
    "llm_base_url",
    "llm_api_key",
    "llm_model",
    "llm_temperature",
    "llm_max_tokens",
    "llm_json_mode",
    "group_channel_mode",
    "dsh_chat_lane_enabled",
    "default_persona",
    "reply_global_interval_seconds",
    "read_poll_interval_seconds",
}


def read_prod_settings() -> dict[str, object]:
    con = sqlite3.connect(PROD_DB)
    con.row_factory = sqlite3.Row
    out: dict[str, object] = {}
    for row in con.execute("select key, value from settings"):
        key = str(row["key"] or "")
        if key not in COPY_KEYS:
            continue
        try:
            out[key] = json.loads(row["value"])
        except Exception:
            out[key] = row["value"]
    con.close()
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--task", action="store_true")
    parser.add_argument(
        "--dsh",
        action="store_true",
        help="reuse the project's installed DSH runtime/profile (chat lane only)",
    )
    args = parser.parse_args()

    temp_dir = Path(tempfile.mkdtemp(prefix=".e2e-", dir=PROJECT))
    if args.dsh:
        dsh_source = PROJECT / "data" / "dsh"
        if not dsh_source.exists():
            print("SKIP: project DSH runtime not found")
            return 2
        result = subprocess.run(
            [
                "cmd",
                "/c",
                "mklink",
                "/J",
                str(temp_dir / "dsh"),
                str(dsh_source),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("SKIP: cannot create DSH junction:", result.stderr.strip())
            return 2
    os.environ["DATA_DIR"] = str(temp_dir)
    prod = read_prod_settings()
    if not str(prod.get("llm_api_key") or "").strip():
        print("SKIP: no llm_api_key")
        return 2

    from app.core.runtime import RuntimeManager
    from app.db import SessionLocal, init_db
    from app.services.settings import update_settings

    await init_db()
    async with SessionLocal() as db:
        await update_settings(db, {**prod, "auto_login": ""})
    runtime = RuntimeManager()
    await runtime.start()
    try:
        await runtime.login("mock")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not runtime.is_online:
            await asyncio.sleep(0.3)
        if not runtime.is_online:
            print("FAIL: mock adapter did not become online", runtime.last_error)
            return 3
        from app.adapters.base import IncomingMessage
        from app.models import AgentTaskRun, Group
        from sqlalchemy import select

        async with SessionLocal() as db:
            group = (
                await db.execute(
                    Group.__table__.select().order_by(Group.id.asc()).limit(1)
                )
            ).first()
        if group is None:
            print("FAIL: no group seeded")
            return 4
        group_id = int(group.id)
        adapter = runtime.adapter
        adapter._emit_message(
            IncomingMessage(
                room_id=str(group.external_id),
                sender_id="wxid_laowang",
                sender_name="老王",
                content=(
                    "@小助手 帮我记一下：明天十点开项目会"
                    if args.task
                    else "@小助手 在吗？今天好累"
                ),
                timestamp=time.time(),
                external_id=f"mock-e2e-{uuid.uuid4().hex[:10]}",
            )
        )
        sent_deadline = time.monotonic() + 45
        sent: list[tuple[str, str, str]] = []
        while time.monotonic() < sent_deadline:
            sent = list(getattr(adapter, "sent", []) or [])
            if sent:
                break
            await asyncio.sleep(0.5)
        print(f"group_id={group_id} online={runtime.is_online} sent={len(sent)}")
        for kind, _room, text in sent[:3]:
            print(f"SENT[{kind}]:", str(text)[:300])
        if not args.task:
            return 0 if sent else 5
        task_deadline = time.monotonic() + 75
        task = None
        while time.monotonic() < task_deadline:
            async with SessionLocal() as db:
                task = (
                    await db.execute(
                        select(AgentTaskRun)
                        .order_by(AgentTaskRun.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if task is not None and task.status in {"done", "failed", "cancelled"}:
                break
            await asyncio.sleep(1.0)
        print(
            "TASK:",
            getattr(task, "status", None),
            "verified=",
            getattr(task, "verified", None),
            "error=",
            getattr(task, "error", "")[:200],
        )
        return 0 if task is not None and task.status == "done" else 6
    finally:
        await runtime.close()
        if not args.keep:
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            print("kept:", temp_dir)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
