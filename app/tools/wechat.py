"""WeChat/group tools.  Sending is always routed through wechatauto-replica."""

from __future__ import annotations

import json
from typing import Any

from app.tools.common import call, call_json, groups, messages


def register(server: Any) -> None:
    @server.tool(
        name="list_groups",
        description="列出已读取并纳入群伴的微信群，返回群 ID、名称和 Agent 开关。",
    )
    async def list_groups() -> str:
        data = await call_json("list_groups", {})
        return json.dumps(groups(data), ensure_ascii=False)

    @server.tool(
        name="read_group",
        description="读取指定群最近消息。group_id 是群伴中的群数据库 ID。",
    )
    async def read_group(
        group_id: int,
        limit: int = 30,
        since_id: int | None = None,
    ) -> str:
        args: dict[str, Any] = {"group_id": int(group_id), "limit": int(limit)}
        if since_id is not None:
            args["since_id"] = int(since_id)
        data = await call_json("read_group_messages", args)
        return json.dumps(messages(data), ensure_ascii=False)

    @server.tool(
        name="send_message",
        description=(
            "发送微信消息。group_id 用于群聊，contact_id 用于私聊；"
            "文本发送只走 wechatauto-replica，禁止键盘/PowerShell 模拟。"
        ),
    )
    async def send_message(
        text: str,
        group_id: int | None = None,
        contact_id: int | None = None,
    ) -> str:
        if group_id:
            return await call(
                "send_group_message", {"group_id": int(group_id), "text": text}
            )
        if contact_id:
            return await call(
                "send_contact_message", {"contact_id": int(contact_id), "text": text}
            )
        return json.dumps(
            {"ok": False, "error": "必须提供 group_id 或 contact_id"},
            ensure_ascii=False,
        )
