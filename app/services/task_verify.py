"""External checks for durable task completion.

``verified`` must not mean "we sent something".  It means we found evidence
for the task's promised artifact: a sent transport job, a file, a B站 record,
or a clearly stated no-op.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import BiliVideo, TransportJob

BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
WIN_PATH_RE = re.compile(
    r"[A-Za-z]:\\[^\s\"'<>|]+?\.(?:xlsx|xls|csv|docx|pdf|png|jpg|jpeg|zip|txt)",
    re.IGNORECASE,
)


async def verify_task(
    task_id: int,
    *,
    request: str,
    result_text: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    text = f"{request}\n{result_text}"
    bvids = BVID_RE.findall(text)
    paths = WIN_PATH_RE.findall(text)

    sent_job = False
    try:
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(TransportJob)
                    .order_by(TransportJob.id.desc())
                    .limit(200)
                )
            ).scalars().all()
        for row in rows:
            meta = (row.payload or {}).get("meta") or {}
            if str(meta.get("task_id") or "") != str(task_id):
                continue
            checks.append(
                {
                    "name": "send",
                    "ok": row.status == "sent",
                    "detail": f"job #{row.id} {row.status}",
                }
            )
            sent_job = row.status == "sent"
            break
    except Exception as exc:
        checks.append({"name": "send", "ok": False, "detail": str(exc)[:200]})

    if not any(item.get("name") == "send" for item in checks):
        checks.append(
            {
                "name": "send",
                "ok": False,
                "detail": "没有找到该任务的 transport job",
            }
        )

    for raw in paths[:5]:
        path = Path(raw)
        checks.append(
            {
                "name": "file",
                "ok": path.is_file(),
                "detail": raw,
            }
        )

    if bvids:
        for bvid in bvids[:3]:
            found = False
            try:
                async with SessionLocal() as db:
                    found = (
                        await db.execute(
                            select(BiliVideo.id).where(BiliVideo.bvid == bvid)
                        )
                    ).first() is not None
            except Exception:
                found = False
            checks.append(
                {
                    "name": "bili",
                    "ok": found,
                    "detail": bvid,
                }
            )

    required = [item for item in checks if item.get("name") != "none"]
    ok = bool(required) and all(bool(item.get("ok")) for item in required)
    return {
        "ok": ok,
        "checks": checks,
        "summary": "；".join(
            f"{item.get('name')}={'ok' if item.get('ok') else 'fail'}"
            for item in checks
        )[:1000],
    }
