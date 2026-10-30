# -*- coding: utf-8 -*-
"""局域网游戏服务：只负责把 frontend/yunyin 这棵静态站点发到 0.0.0.0:8021。

历史背景：原来的 rpg_server.py 在主仓库清理「修仙界」功能时被一起删掉了，
云隐页面的静态托管就靠这个精简版恢复；游戏本体是纯前端（存档在浏览器），
不需要后端接口也能玩。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
GAME_DIR = ROOT / "frontend" / "yunyin"

app = FastAPI(title="群伴 · 雪覆江湖 LAN", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "game": GAME_DIR.is_dir(),
        "pid": os.getpid(),
    })


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/rpg/yunyin/", status_code=307)


if GAME_DIR.is_dir():
    app.mount("/rpg/yunyin", StaticFiles(directory=str(GAME_DIR), html=True), name="yunyin-game")
