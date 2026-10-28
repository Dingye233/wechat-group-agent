"""Thin MCP entrypoint for the modular Qunban tool components.

The actual tools live in ``app.tools``:
- ``wechat``: groups, contacts and wechatauto send
- ``bili``: B站 search/knowledge
- ``memory``: long-term memory
- ``system``: generic gateway, MCP projects, skills, interfaces, tool admin

OpenCode starts this process as one local MCP server and sees the composed
component catalog.  Each component can be moved/split later without changing
the OpenCode provider or the Qunban API.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.mcpserver import MCPServer

from app.tools import register_all

server = MCPServer(
    "qunban",
    instructions=(
        "群伴的工具组件：微信、B站、记忆、文件、终端和工具管理。"
        "微信发送必须走 qunban_send_message / wechatauto-replica；"
        "不要用键盘或 PowerShell 操作微信窗口。"
    ),
)

register_all(server)


def main() -> None:
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
