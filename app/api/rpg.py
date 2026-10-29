# -*- coding: utf-8 -*-
"""修仙 RPG 的 HTTP + WebSocket 接口。

挂在群伴主服务上（/api/rpg/*、/api/rpg/ws、/rpg 页面），
也可以被 app/rpg_server.py 单独拿去跑局域网服务。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.services import rpg_game

router = APIRouter(tags=["rpg"])
api_router = router
PAGE = Path(__file__).resolve().parents[2] / "frontend" / "rpg.html"


class Hub:
    """极简在线大厅：广播聊天 / 行动 / 世界播报。"""

    def __init__(self) -> None:
        self.clients: dict[WebSocket, str] = {}
        self.lock = asyncio.Lock()

    async def join(self, ws: WebSocket, name: str) -> None:
        async with self.lock:
            self.clients[ws] = name
        await self.broadcast({"type": "presence", "players": self.online()})
        await self.broadcast({"type": "world", "text": f"🌊 {name} 踏入修行界"})

    async def leave(self, ws: WebSocket) -> None:
        async with self.lock:
            name = self.clients.pop(ws, None)
        if name:
            await self.broadcast({"type": "presence", "players": self.online()})
            await self.broadcast({"type": "world", "text": f"🍃 {name} 隐入云雾"})

    def online(self) -> list[str]:
        return sorted(set(self.clients.values()))

    async def broadcast(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            async with self.lock:
                self.clients.pop(ws, None)


hub = Hub()


def _payload(name: str) -> dict:
    return rpg_game.join(name)


@router.post("/rpg/join")
async def rpg_join(payload: dict = Body(...)) -> dict:
    try:
        state = _payload(str(payload.get("name") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "state": state, "stats": rpg_game.stats()}


@router.get("/rpg/state")
async def rpg_state(name: str = Query(...)) -> dict:
    try:
        return {"ok": True, "state": rpg_game.get_state(name)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/rpg/cultivate")
async def rpg_cultivate(payload: dict = Body(...)) -> dict:
    try:
        result = rpg_game.cultivate(str(payload.get("name") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("ok"):
        await hub.broadcast({"type": "action", "name": payload.get("name"), "kind": "cultivate"})
    return result


@router.post("/rpg/breakthrough")
async def rpg_breakthrough(payload: dict = Body(...)) -> dict:
    name = str(payload.get("name") or "")
    try:
        result = rpg_game.breakthrough(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("ok") and result.get("message"):
        rpg_game.log_event(name, "breakthrough", result["message"])
        await hub.broadcast({"type": "world", "text": result["message"]})
    return result


@router.post("/rpg/explore")
async def rpg_explore(payload: dict = Body(...)) -> dict:
    name = str(payload.get("name") or "")
    try:
        result = rpg_game.explore(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("ok") and result.get("kind") == "秘境":
        rpg_game.log_event(name, "secret", result["message"])
        await hub.broadcast({"type": "world", "text": f"✨ {result['message']}"})
    return result


@router.post("/rpg/heal")
async def rpg_heal(payload: dict = Body(...)) -> dict:
    try:
        return rpg_game.heal(str(payload.get("name") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/rpg/leaderboard")
async def rpg_leaderboard(limit: int = Query(default=20, ge=1, le=50)) -> dict:
    return {"ok": True, "players": rpg_game.leaderboard(limit)}


@router.get("/rpg/world")
async def rpg_world(limit: int = Query(default=30, ge=1, le=100)) -> dict:
    return {"ok": True, "events": rpg_game.world_log(limit), "online": hub.online()}


# ----------------------------------------------------------------- party
@router.post("/rpg/party/create")
async def rpg_party_create(payload: dict = Body(...)) -> dict:
    name = str(payload.get("name") or "")
    try:
        party = rpg_game.create_party(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await hub.broadcast({"type": "party", "text": f"🤝 {name} 立下队伍 {party['code']}"})
    return {"ok": True, "party": party}


@router.post("/rpg/party/join")
async def rpg_party_join(payload: dict = Body(...)) -> dict:
    name = str(payload.get("name") or "")
    try:
        party = rpg_game.join_party(name, str(payload.get("code") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await hub.broadcast({"type": "party", "text": f"➕ {name} 加入队伍 {party['code']}"})
    return {"ok": True, "party": party}


@router.post("/rpg/party/leave")
async def rpg_party_leave(payload: dict = Body(...)) -> dict:
    name = str(payload.get("name") or "")
    result = rpg_game.leave_party(name)
    await hub.broadcast({"type": "party", "text": f"👋 {name} 离队"})
    return {"ok": True, **result}


@router.get("/rpg/party/list")
async def rpg_party_list() -> dict:
    return {"ok": True, "parties": rpg_game.list_parties()}


@router.post("/rpg/party/start")
async def rpg_party_start(payload: dict = Body(...)) -> dict:
    name = str(payload.get("name") or "")
    try:
        result = rpg_game.start_dungeon(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("victory"):
        rpg_game.log_event(name, "dungeon", f"⚔️ {name} 的队伍讨伐了【{result['boss']}】")
        text = f"⚔️ {name} 的队伍讨伐了【{result['boss']}】"
    else:
        text = f"💀 {name} 的队伍折在【{result['boss']}】手上"
    await hub.broadcast({"type": "dungeon", "text": text, "log": result["log"]})
    return result


# ------------------------------------------------------------------- pvp
@router.post("/rpg/pvp/duel")
async def rpg_pvp_duel(payload: dict = Body(...)) -> dict:
    a = str(payload.get("from") or payload.get("name") or "")
    b = str(payload.get("to") or "")
    try:
        result = rpg_game.duel(a, b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await hub.broadcast({"type": "duel", "text": f"🥋 {a} 与 {b} 论道，{result['winner']} 胜", "log": result["log"]})
    return result


@router.get("/rpg/stats")
async def rpg_stats() -> dict:
    return {"ok": True, **rpg_game.stats(), "online": len(hub.online())}


@router.websocket("/rpg/ws")
async def rpg_ws(ws: WebSocket, name: str = Query(default="无名散修")) -> None:
    await ws.accept()
    name = (name or "无名散修")[:16]
    await hub.join(ws, name)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                msg = {"type": "chat", "text": raw}
            kind = str(msg.get("type") or "chat")
            if kind == "chat":
                text = str(msg.get("text") or "").strip()[:120]
                if text:
                    await hub.broadcast({"type": "chat", "name": name, "text": text, "at": time.time()})
            elif kind == "ping":
                await ws.send_text(json.dumps({"type": "pong"}, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.leave(ws)


# ---------------------------------------------------------------- admin
@router.get("/rpg/admin/overview")
async def rpg_admin_overview() -> dict:
    return {"ok": True, **rpg_game.admin_overview(), "online": hub.online()}


@router.get("/rpg/admin/players")
async def rpg_admin_players(
    query: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return {"ok": True, **rpg_game.admin_players(query, limit, offset)}


@router.post("/rpg/admin/player/update")
async def rpg_admin_player_update(payload: dict = Body(...)) -> dict:
    name = str(payload.get("name") or "")
    fields = {k: payload.get(k) for k in ("realm", "exp", "stones", "hp", "sect") if k in payload}
    try:
        state = rpg_game.admin_update_player(name, fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await hub.broadcast({"type": "world", "text": f"🛠 天道调整了 {name} 的气数"})
    return {"ok": True, "state": state}


@router.post("/rpg/admin/player/delete")
async def rpg_admin_player_delete(payload: dict = Body(...)) -> dict:
    try:
        result = rpg_game.admin_delete_player(str(payload.get("name") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/rpg/admin/reset")
async def rpg_admin_reset(payload: dict = Body(default={})) -> dict:
    result = rpg_game.admin_reset_all(bool((payload or {}).get("keep_events")))
    await hub.broadcast({"type": "world", "text": "🌑 天地重开，一切从头"})
    return result


@router.post("/rpg/admin/broadcast")
async def rpg_admin_broadcast(payload: dict = Body(...)) -> dict:
    text = str(payload.get("text") or "").strip()[:120]
    if not text:
        raise HTTPException(status_code=400, detail="播报内容不能为空")
    rpg_game.log_event("天道", "broadcast", text)
    await hub.broadcast({"type": "world", "text": f"📢 {text}"})
    return {"ok": True, "text": text}


page_router = APIRouter(tags=["rpg"])

ACTION_PAGE = Path(__file__).resolve().parents[2] / "frontend" / "rpg-action.html"


def page_html() -> HTMLResponse:
    if not PAGE.is_file():
        return HTMLResponse("<h1>修仙界尚未开辟</h1>", status_code=404)
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


def action_html() -> HTMLResponse:
    if not ACTION_PAGE.is_file():
        return HTMLResponse("<h1>动作模式尚未开辟</h1>", status_code=404)
    return HTMLResponse(ACTION_PAGE.read_text(encoding="utf-8"))


@page_router.get("/rpg/action", response_class=HTMLResponse)
async def rpg_action_page() -> HTMLResponse:
    return action_html()


PLAY_PAGE = Path(__file__).resolve().parents[2] / "frontend" / "rpg-play.html"
MANIFEST = Path(__file__).resolve().parents[2] / "frontend" / "manifest.json"
SW_JS = Path(__file__).resolve().parents[2] / "frontend" / "sw.js"
ICON_DIR = Path(__file__).resolve().parents[2] / "frontend"


@page_router.get("/rpg/play", response_class=HTMLResponse)
async def rpg_play_page() -> HTMLResponse:
    if not PLAY_PAGE.is_file():
        return RedirectResponse("/rpg", status_code=307)
    return HTMLResponse(PLAY_PAGE.read_text(encoding="utf-8"))


@page_router.get("/rpg/manifest.json")
async def rpg_manifest() -> FileResponse:
    if not MANIFEST.is_file():
        raise HTTPException(status_code=404, detail="manifest 缺失")
    return FileResponse(str(MANIFEST), media_type="application/manifest+json")


@page_router.get("/rpg/sw.js")
async def rpg_sw() -> FileResponse:
    if not SW_JS.is_file():
        raise HTTPException(status_code=404, detail="service worker 缺失")
    return FileResponse(str(SW_JS), media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})


@page_router.get("/rpg/icon-{size}.png")
async def rpg_icon(size: str) -> FileResponse:
    path = ICON_DIR / f"icon-{size}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图标缺失")
    return FileResponse(str(path), media_type="image/png")


@router.get("/rpg/action/save")
async def rpg_action_load(name: str = Query(...)) -> dict:
    return {"ok": True, "save": rpg_game.load_action(name)}


@router.post("/rpg/action/save")
async def rpg_action_save(payload: dict = Body(...)) -> dict:
    try:
        save = rpg_game.save_action(str(payload.get("name") or ""), payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "save": save}


@router.get("/rpg/action/leaderboard")
async def rpg_action_leaderboard(limit: int = Query(default=10, ge=1, le=50)) -> dict:
    return {"ok": True, "players": rpg_game.action_leaderboard(limit)}


@page_router.get("/rpg", response_class=HTMLResponse)
async def rpg_page() -> HTMLResponse:
    return page_html()
