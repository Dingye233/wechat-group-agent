"""Modular Qunban tool components exposed to the official OpenCode server."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer


def register_all(server: "MCPServer") -> None:
    # Imported lazily so a single module can be loaded/tested without starting
    # every component or creating circular imports around the shared server.
    from app.tools import bili, memory, system, wechat

    for module in (wechat, bili, memory, system):
        module.register(server)


__all__ = ["register_all"]
