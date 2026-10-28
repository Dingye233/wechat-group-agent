from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.runtime import RuntimeManager

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    runtime: RuntimeManager = websocket.app.state.runtime
    queue = runtime.bus.subscribe()
    try:
        await websocket.send_json(
            {"type": "hello", "data": runtime.status_dict(), "ts": 0}
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "data": {}, "ts": 0})
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        pass
    except Exception:
        logger.debug("WebSocket 连接结束", exc_info=True)
    finally:
        runtime.bus.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await websocket.close()
