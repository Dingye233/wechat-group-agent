"""Memory tool component."""

from __future__ import annotations

import json
from typing import Any

from app.tools.common import call, call_json


def register(server: Any) -> None:
    @server.tool(name="memory_save", description="保存一条长期记忆。")
    async def memory_save(
        content: str,
        title: str = "",
        category: str = "operator",
        importance: int = 3,
    ) -> str:
        return await call(
            "save_memory",
            {
                "title": title,
                "content": content,
                "category": category,
                "importance": int(importance),
            },
        )

    @server.tool(name="memory_search", description="搜索群伴长期记忆。")
    async def memory_search(query: str, limit: int = 10) -> str:
        data = await call_json(
            "search_memories", {"query": query, "limit": int(limit)}
        )
        rows = data if isinstance(data, list) else (data or {}).get("memories", [])
        compact = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            compact.append(
                {
                    "title": row.get("title"),
                    "content": str(row.get("content") or "")[:1200],
                    "category": row.get("category"),
                    "importance": row.get("importance"),
                }
            )
        return json.dumps(compact, ensure_ascii=False)
