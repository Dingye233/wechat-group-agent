"""Verify Qunban's remote adapter against a mock wechatauto service.

Requires:
    python scripts/wechatauto_service.py --backend mock --port 8011
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
SERVICE = os.environ.get("WECHATAUTO_SERVICE_URL", "http://127.0.0.1:8011")


async def main() -> int:
    temp_dir = Path(tempfile.mkdtemp(prefix="qunban-remote-e2e-"))
    os.environ["DATA_DIR"] = str(temp_dir)
    from app.core.runtime import RuntimeManager
    from app.db import SessionLocal, init_db
    from app.models import Group, TransportJob
    from sqlalchemy import select

    await init_db()
    runtime = RuntimeManager()
    await runtime.start()
    try:
        await runtime.login("replica-remote")
        deadline = asyncio.get_event_loop().time() + 15
        while asyncio.get_event_loop().time() < deadline and not runtime.is_online:
            await asyncio.sleep(0.3)
        group = None
        group_deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < group_deadline:
            async with SessionLocal() as db:
                group = (
                    await db.execute(select(Group).order_by(Group.id.asc()).limit(1))
                ).scalar_one_or_none()
            if group is not None:
                break
            await asyncio.sleep(0.3)
        if not runtime.is_online or group is None:
            print("FAIL: runtime/group not ready", runtime.last_error)
            return 2
        sent = await runtime.send_agent_message(
            group.id, text="remote transport e2e", meta={"source": "e2e"}
        )
        async with SessionLocal() as db:
            job = (
                await db.execute(
                    select(TransportJob).order_by(TransportJob.id.desc()).limit(1)
                )
            ).scalar_one_or_none()
        print(
            f"adapter={runtime.adapter_id} groups=1 sent={sent} "
            f"job={getattr(job, 'status', None)}"
        )
        return 0 if sent and getattr(job, "status", None) == "sent" else 3
    finally:
        await runtime.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
