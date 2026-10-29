from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    assistant,
    auth,
    bilibili,
    chat_tasks,
    contacts,
    dsh,
    dsh_plugins,
    groups,
    llm,
    opencode,
    outbox,
    replica_tools,
    settings as settings_api,
    status,
    stickers,
    tasks,
    transport,
    ws,
)
from app.config import settings
from app.core.runtime import RuntimeManager
from app.db import SessionLocal, dispose_db, init_db
from app.services.settings import get_settings
from app.services.capabilities import sync_capabilities
from app.services.bili_autopilot import BiliAutopilot
from app.services.stickers import seed_sample_stickers

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("app")


def _install_file_logging() -> None:
    """Keep application logs even when the server runs hidden in background."""
    target = settings.data_dir / "server.log"
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler):
            try:
                if Path(handler.baseFilename) == target:
                    return
            except Exception:
                pass
    handler = RotatingFileHandler(
        target,
        maxBytes=5 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)


_install_file_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    await init_db()
    try:
        await sync_capabilities()
    except Exception:
        logger.exception("能力自动识别失败")
    saved: dict = {}
    try:
        async with SessionLocal() as db:
            saved = await get_settings(db)
            if saved.get("seed_sample_stickers", True):
                created = await seed_sample_stickers(db)
                if created:
                    logger.info("已生成 %s 个内置示例表情包", created)
    except Exception:
        logger.exception("初始化表情包失败，服务继续启动")
    try:
        # Load FastEmbed/Chroma in the background so the first group reply does
        # not pay the 1-3s mem0 cold-start cost.
        from app.services import mem0_memory

        mem0_memory.warmup(saved)
    except Exception:
        logger.debug("Mem0 预热失败", exc_info=True)

    runtime = RuntimeManager()
    app.state.runtime = runtime
    await runtime.start()
    bili_autopilot = BiliAutopilot(runtime)
    app.state.bili_autopilot = bili_autopilot
    await bili_autopilot.start()
    auto_task = asyncio.create_task(runtime.maybe_auto_login(), name="auto-login")
    primary_task = None
    try:
        async with SessionLocal() as db:
            saved_settings = await get_settings(db)
        if str(saved_settings.get("group_channel_mode") or "dsh_primary") == "dsh_primary":
            from app.services.dsh_primary import get_dsh_primary_service

            async def _start_primary_later() -> None:
                # Let Uvicorn finish its startup/lifespan before the ACP plugin
                # asks /api/capabilities for the dynamic tool catalogue.
                await asyncio.sleep(2.0)
                await get_dsh_primary_service().start(saved_settings)

            primary_task = asyncio.create_task(
                _start_primary_later(),
                name="dsh-primary-start",
            )
    except Exception:
        logger.warning("DSH primary 服务启动失败", exc_info=True)
    logger.info("%s v%s 已启动", settings.app_name, settings.version)
    logger.info("控制台: http://%s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        if primary_task and not primary_task.done():
            primary_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await primary_task
        with contextlib.suppress(Exception):
            from app.services.dsh_primary import get_dsh_primary_service

            await get_dsh_primary_service().stop()
        if not auto_task.done():
            auto_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await auto_task
        await bili_autopilot.stop()
        with contextlib.suppress(Exception):
            from app.services.opencode_agent import get_opencode_runtime

            await get_opencode_runtime().stop()
        with contextlib.suppress(Exception):
            from app.services.dsh_agent import get_dsh_agent_runtime

            await get_dsh_agent_runtime().stop()
        await runtime.close()
        await dispose_db()
        logger.info("服务已停止")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(status.router, prefix="/api", tags=["system"])
app.include_router(llm.router, prefix="/api", tags=["llm"])
app.include_router(dsh.router, prefix="/api", tags=["dsh"])
app.include_router(dsh_plugins.router, prefix="/api", tags=["dsh"])
app.include_router(transport.router, prefix="/api", tags=["transport"])
app.include_router(tasks.router, prefix="/api", tags=["tasks"])
app.include_router(groups.router, prefix="/api", tags=["groups"])
app.include_router(chat_tasks.router, prefix="/api", tags=["groups"])
app.include_router(outbox.router, prefix="/api", tags=["outbox"])
app.include_router(contacts.router, prefix="/api", tags=["contacts"])
app.include_router(replica_tools.router, prefix="/api", tags=["replica"])
app.include_router(assistant.router, prefix="/api", tags=["assistant"])
app.include_router(opencode.router, prefix="/api", tags=["opencode"])
app.include_router(bilibili.router, prefix="/api", tags=["bilibili"])
app.include_router(stickers.router, prefix="/api", tags=["stickers"])
app.include_router(settings_api.router, prefix="/api", tags=["settings"])
app.include_router(ws.router)


@app.middleware("http")
async def no_store_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path in (
        "/",
        "/index.html",
        "/app.js",
        "/styles.css",
    ):
        # Always revalidate the dashboard shell so UI fixes are visible after
        # a normal refresh instead of a stale cached bundle.
        response.headers["Cache-Control"] = "no-store"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.mount(
    "/media/stickers",
    StaticFiles(directory=str(settings.sticker_dir)),
    name="stickers",
)

app.mount(
    "/media/chat",
    StaticFiles(directory=str(settings.chat_media_dir)),
    name="chat-media",
)

replica_media_dir = settings.data_dir / "media" / "replica"
replica_media_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/media/replica",
    StaticFiles(directory=str(replica_media_dir)),
    name="replica-media",
)

assistant_media_dir = settings.data_dir / "media" / "assistant"
assistant_media_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/media/assistant",
    StaticFiles(directory=str(assistant_media_dir)),
    name="assistant-media",
)

if settings.frontend_dir.exists():
    app.mount(
        "/",
        StaticFiles(directory=str(settings.frontend_dir), html=True),
        name="frontend",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
