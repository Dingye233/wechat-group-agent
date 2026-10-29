# -*- coding: utf-8 -*-
"""修仙 RPG 的独立局域网服务。

    uvicorn app.rpg_server:app --host 0.0.0.0 --port 8020

只暴露游戏（页面 + /api/rpg/* + WebSocket），不带群伴控制台，
方便在局域网里直接开玩：手机访问 http://<你的内网IP>:8020/
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.rpg import api_router, page_html, page_router

app = FastAPI(title="问道 · 修仙界", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")
app.include_router(page_router)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return page_html()


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "game": "immortal-realm"}
