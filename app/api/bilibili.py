from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import SessionLocal
from app.models import BiliVideo
from app.services import bilibili, bili_knowledge

router = APIRouter(tags=["bilibili"])


def _account_key(runtime: RuntimeManager) -> str:
    return str(runtime.account_key or "")


@router.get("/bili/status")
async def bili_status(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    return {"account": await bilibili.status(_account_key(runtime))}


@router.post("/bili/login/qr")
async def bili_login_qr() -> dict:
    try:
        return await bilibili.start_qr_login()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/bili/login/qr/{key}")
async def bili_login_qr_poll(
    key: str,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.poll_qr_login(key, _account_key(runtime))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/login/password/start")
async def bili_login_password_start(payload: dict = Body(...)) -> dict:
    try:
        return await bilibili.start_password_login(
            str(payload.get("username") or ""),
            str(payload.get("password") or ""),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/login/password/poll")
async def bili_login_password_poll(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.poll_password_login(
            str(payload.get("session_id") or ""),
            _account_key(runtime),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/account/cookie")
async def bili_account_cookie(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        account = await bilibili.save_cookie(
            sessdata=str(payload.get("sessdata") or ""),
            bili_jct=str(payload.get("bili_jct") or ""),
            buvid3=str(payload.get("buvid3") or ""),
            buvid4=str(payload.get("buvid4") or ""),
            dedeuserid=str(payload.get("dedeuserid") or ""),
            ac_time_value=str(payload.get("ac_time_value") or ""),
            account_key=_account_key(runtime),
        )
        return {"ok": True, "account": account}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/bili/account")
async def bili_account_delete(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    await bilibili.clear_account(_account_key(runtime))
    return {"ok": True}


@router.get("/bili/search")
async def bili_search(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=10, ge=1, le=30),
) -> dict:
    try:
        return {"videos": await bilibili.search_videos(q, limit)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/bili/recommend")
async def bili_recommend(limit: int = Query(default=8, ge=1, le=30)) -> dict:
    try:
        return {"videos": await bilibili.recommend_videos(limit)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/bili/video/{bvid}")
async def bili_video_info(bvid: str) -> dict:
    try:
        return {"video": await bilibili.video_info(bvid)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/watch")
async def bili_watch(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.watch_video(
            str(payload.get("bvid") or ""),
            open_browser=bool(payload.get("open_browser", True)),
            account_key=_account_key(runtime),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/like")
async def bili_like(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.like_video(
            str(payload.get("bvid") or ""),
            bool(payload.get("liked", True)),
            _account_key(runtime),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/comment")
async def bili_comment(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.comment_video(
            str(payload.get("bvid") or ""),
            str(payload.get("text") or ""),
            _account_key(runtime),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/feed")
async def bili_feed(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.feed_videos(
            int(payload.get("limit") or 5),
            open_browser=bool(payload.get("open_browser", True)),
            account_key=_account_key(runtime),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/transcribe")
async def bili_transcribe(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.transcribe_video(
            str(payload.get("bvid") or ""),
            str(payload.get("model") or "small"),
            _account_key(runtime),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/favorite")
async def bili_favorite(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.favorite_video(
            str(payload.get("bvid") or ""), _account_key(runtime)
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/bili/comments")
async def bili_comments(
    bvid: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=30),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return {
            "comments": await bilibili.get_video_comments(
                bvid, limit, _account_key(runtime)
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/reply")
async def bili_reply(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.reply_video_comment(
            str(payload.get("bvid") or ""),
            str(payload.get("text") or ""),
            int(payload.get("root") or 0),
            int(payload["parent"]) if payload.get("parent") else None,
            _account_key(runtime),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/bili/autopilot")
async def bili_autopilot_status(
    request: Request,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    autopilot = getattr(request.app.state, "bili_autopilot", None)
    if autopilot is None:
        raise HTTPException(status_code=503, detail="自动刷服务未启动")
    return await autopilot.status(_account_key(runtime))


@router.put("/bili/autopilot")
async def bili_autopilot_save(
    request: Request,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    autopilot = getattr(request.app.state, "bili_autopilot", None)
    if autopilot is None:
        raise HTTPException(status_code=503, detail="自动刷服务未启动")
    try:
        config = await autopilot.save_config(payload, _account_key(runtime))
        return {"config": config}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bili/autopilot/run")
async def bili_autopilot_run(
    request: Request,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    autopilot = getattr(request.app.state, "bili_autopilot", None)
    if autopilot is None:
        raise HTTPException(status_code=503, detail="自动刷服务未启动")
    return await autopilot.run_cycle(force=True)


@router.get("/bili/videos")
async def bili_videos(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    async with SessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(BiliVideo).order_by(BiliVideo.id.desc()).limit(limit)
                )
            ).scalars().all()
        )
    return {
        "videos": [
            {
                "id": row.id,
                "bvid": row.bvid,
                "title": row.title,
                "author": row.author,
                "url": row.url,
                "summary": row.summary,
                "topics": row.topics,
                "actions": row.actions,
                "status": row.status,
                "error": row.error,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.post("/bili/knowledge/add")
async def bili_knowledge_add(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    try:
        return await bilibili.record_video_to_knowledge(
            str(payload.get("bvid") or ""),
            str(payload.get("model") or "small"),
            _account_key(runtime),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/bili/knowledge/search")
async def bili_knowledge_search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    try:
        return {"results": await bili_knowledge.search_async(q, limit)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/bili/knowledge/stats")
async def bili_knowledge_stats() -> dict:
    try:
        return await bili_knowledge.stats_async()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

# ---------------------------------------------------- 监听：定时轮询 + 自动回复
@router.get("/bili/listen")
async def bili_listen_status() -> dict:
    from app.services import bili_watch

    return bili_watch.status()


@router.post("/bili/listen")
async def bili_listen_save(payload: dict) -> dict:
    from app.services import bili_watch

    conf = bili_watch.load_config()
    patch = {}
    if "enabled" in payload:
        patch["enabled"] = bool(payload.get("enabled"))
    if "interval_minutes" in payload:
        try:
            patch["interval_minutes"] = max(1, min(1440, int(payload.get("interval_minutes"))))
        except Exception:
            raise HTTPException(status_code=400, detail="间隔需要是分钟数")
    if "admin_name" in payload or "admin_uid" in payload:
        name = str(payload.get("admin_name") or "").strip()
        try:
            uid = int(payload.get("admin_uid") or 0)
        except Exception:
            uid = 0
        if not name or uid <= 0:
            raise HTTPException(status_code=400, detail="管理员必须同时提供名称和 UID")
        patch["admin_name"] = name
        patch["admin_uid"] = uid
    if "reply_others" in payload:
        patch["reply_others"] = bool(payload.get("reply_others"))
    if "reply_admin" in payload:
        patch["reply_admin"] = bool(payload.get("reply_admin"))
    conf = bili_watch.save_config(patch)
    return {"ok": True, "config": conf}


@router.post("/bili/listen/run")
async def bili_listen_run() -> dict:
    import asyncio as _asyncio

    from app.services import bili_watch

    return await _asyncio.to_thread(bili_watch.poll_once, True)