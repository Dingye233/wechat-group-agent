"""Rebuild the B站 knowledge Chroma index from BiliVideo rows.

Run with the project virtualenv; --limit 20 for a small backfill.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from app.config import settings  # noqa: E402
from app.services import bili_knowledge  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 means all")
    args = parser.parse_args()

    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    query = (
        "SELECT * FROM bili_videos "
        "WHERE status='done' AND (transcript!='' OR summary!='') "
        "ORDER BY id DESC"
    )
    if args.limit and args.limit > 0:
        query += f" LIMIT {int(args.limit)}"
    rows = con.execute(query).fetchall()
    print(f"db={settings.db_path} rows={len(rows)}")
    added = 0
    failed = 0
    for index, row in enumerate(rows, 1):
        transcript = str(row["transcript"] or "").strip()
        summary = str(row["summary"] or "").strip()
        if not transcript and not summary:
            continue
        try:
            topics = json.loads(row["topics"] or "[]")
            if not isinstance(topics, list):
                topics = []
        except Exception:
            topics = []
        try:
            result = bili_knowledge.add_video(
                bvid=str(row["bvid"] or ""),
                title=str(row["title"] or ""),
                author=str(row["author"] or ""),
                url=str(row["url"] or ""),
                transcript=transcript,
                summary=summary,
                topics=[str(item) for item in topics],
            )
            added += int(result.get("added") or 0)
            print(f"[{index}/{len(rows)}] {row['bvid']} +{result.get('added', 0)}")
        except Exception as exc:
            failed += 1
            print(f"[{index}/{len(rows)}] {row['bvid']} FAILED: {exc}")
    print(f"done added={added} failed={failed} index={bili_knowledge.stats()}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
