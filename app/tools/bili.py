"""Bilibili tool component."""

from __future__ import annotations

import json
from typing import Any

from app.tools.common import call_json


def register(server: Any) -> None:
    @server.tool(
        name="bili_search",
        description="搜索 B站视频，返回标题、作者、bvid 和链接。",
    )
    async def bili_search(keyword: str, limit: int = 10) -> str:
        data = await call_json(
            "bili_search", {"keyword": keyword, "limit": int(limit)}
        )
        rows = data.get("videos", []) if isinstance(data, dict) else data
        compact = [
            {
                "bvid": row.get("bvid"),
                "title": row.get("title"),
                "author": row.get("author") or row.get("owner"),
                "duration": row.get("duration"),
                "url": row.get("url"),
            }
            for row in (rows or [])
            if isinstance(row, dict)
        ]
        return json.dumps(compact[:50], ensure_ascii=False)

    @server.tool(
        name="bili_knowledge",
        description="检索已写入群伴知识库的 B站视频内容。",
    )
    async def bili_knowledge(query: str, limit: int = 5) -> str:
        data = await call_json(
            "bili_knowledge_search", {"query": query, "limit": int(limit)}
        )
        rows = data if isinstance(data, list) else (data or {}).get("results", [])
        compact = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            compact.append(
                {
                    "bvid": row.get("bvid"),
                    "title": row.get("title"),
                    "text": str(row.get("text") or row.get("content") or "")[:1200],
                    "score": row.get("score"),
                }
            )
        return json.dumps(compact, ensure_ascii=False)
