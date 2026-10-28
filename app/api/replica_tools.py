from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import SessionLocal
from app.models import Group, Message

router = APIRouter(tags=["replica"])

MEDIA_ROOT = Path("data") / "media" / "replica"
EXPORT_ROOT = Path("data") / "exports"
UPLOAD_ROOT = Path("data") / "uploads"


def _adapter(runtime: RuntimeManager) -> Any:
    adapter = runtime.adapter
    if adapter is None or getattr(adapter, "adapter_id", "") != "replica":
        raise HTTPException(status_code=400, detail="当前未连接微信 4.x 适配器")
    if not runtime.is_online:
        raise HTTPException(status_code=400, detail="微信 4.x 尚未登录")
    return adapter


async def _group(runtime: RuntimeManager, group_id: int) -> Group:
    try:
        return await runtime._get_group(group_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _message_context(
    runtime: RuntimeManager, message_id: int
) -> tuple[Message, Group]:
    async with SessionLocal() as db:
        message = await db.get(Message, message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        group = await db.get(Group, message.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if (
            group.account_key
            and runtime.account_key
            and group.account_key != runtime.account_key
        ):
            raise HTTPException(status_code=400, detail="该群聊属于其他微信账号")
        return message, group


def _safe_name(name: str, fallback: str = "file") -> str:
    value = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", name or "").strip("._")
    return value[:120] or fallback


def _response(value: Any) -> dict[str, Any]:
    return {"ok": True, "result": value}


@router.get("/replica/doctor")
async def replica_doctor(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return await adapter.doctor()


@router.get("/replica/accounts")
async def replica_accounts(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"accounts": await adapter.list_accounts()}


@router.get("/replica/sessions")
async def replica_sessions(
    limit: int = Query(default=100, ge=1, le=500),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"sessions": await adapter.sessions(limit)}


@router.get("/replica/contacts")
async def replica_contacts(
    q: str = Query(default=""),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"contacts": await adapter.search_contacts(q)}


@router.get("/replica/moments")
async def replica_moments(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    username: str = Query(default=""),
    keyword: str = Query(default=""),
    since: int | None = Query(default=None),
    until: int | None = Query(default=None),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {
        "moments": await adapter.moments(
            limit=limit,
            offset=offset,
            username=username,
            keyword=keyword,
            since=since,
            until=until,
        )
    }


@router.get("/replica/moments/mine")
async def replica_moments_mine(
    limit: int = Query(default=20, ge=1, le=100),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"moments": await adapter.my_moments(limit)}


@router.get("/replica/moments/count")
async def replica_moments_count(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"count": await adapter.moment_count()}


@router.get("/replica/moments/interactions")
async def replica_moment_interactions(
    limit: int = Query(default=50, ge=1, le=200),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"interactions": await adapter.moment_interactions(limit)}


@router.post("/replica/moments/{index}/like")
async def replica_moment_like(
    index: int,
    payload: dict = Body(default={}),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return _response(await adapter.like_moment(index, bool(payload.get("cancel"))))


@router.post("/replica/moments/{index}/comment")
async def replica_moment_comment(
    index: int,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="评论内容不能为空")
    return _response(
        await adapter.comment_moment(index, text, str(payload.get("reply_to") or ""))
    )


@router.post("/replica/moments/{tid}/download")
async def replica_moment_download(
    tid: str,
    payload: dict = Body(default={}),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    paths = await adapter.download_moment_media(
        tid,
        images=bool(payload.get("images", True)),
        videos=bool(payload.get("videos", True)),
    )
    return {
        "paths": paths,
        "urls": [f"/api/replica/file?path={_relative_media(Path(p))}" for p in paths],
    }


@router.get("/replica/labels")
async def replica_labels(
    prefer: str = Query(default="db"),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"labels": await adapter.list_labels(prefer)}


@router.post("/replica/labels")
async def replica_create_label(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="标签名不能为空")
    return _response(await adapter.create_label(name))


@router.patch("/replica/labels/{label}")
async def replica_rename_label(
    label: str,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    new = str(payload.get("new") or "").strip()
    if not new:
        raise HTTPException(status_code=400, detail="新标签名不能为空")
    return _response(await adapter.rename_label(label, new))


@router.delete("/replica/labels/{label}")
async def replica_delete_label(
    label: str,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return _response(await adapter.delete_label(label))


@router.post("/replica/labels/{label}/members")
async def replica_add_label_members(
    label: str,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    members = [str(item) for item in payload.get("members") or [] if str(item).strip()]
    if not members:
        raise HTTPException(status_code=400, detail="members 不能为空")
    return _response(await adapter.add_label_members(label, members))


@router.delete("/replica/labels/{label}/members")
async def replica_remove_label_members(
    label: str,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    members = [str(item) for item in payload.get("members") or [] if str(item).strip()]
    if not members:
        raise HTTPException(status_code=400, detail="members 不能为空")
    return _response(await adapter.remove_label_members(label, members))


@router.get("/replica/labels/{label}/members")
async def replica_label_members(
    label: str,
    limit: int = Query(default=200, ge=1, le=1000),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return _response(await adapter.label_members(label, limit))


@router.post("/replica/labels/{label}/send")
async def replica_send_to_label(
    label: str,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    members = payload.get("members")
    return _response(
        await adapter.send_to_label(
            label,
            text,
            members=[str(x) for x in members] if members else None,
            dry_run=bool(payload.get("dry_run")),
            limit=int(payload["limit"]) if payload.get("limit") is not None else None,
        )
    )


@router.post("/replica/forward")
async def replica_forward(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    to = payload.get("to")
    if isinstance(to, str):
        targets: list[str] | str = to
    else:
        targets = [str(item) for item in to or [] if str(item).strip()]
    if not targets:
        raise HTTPException(status_code=400, detail="转发目标不能为空")
    return _response(
        await adapter.forward_message(
            targets,
            chat=str(payload.get("chat") or ""),
            match=str(payload.get("match") or ""),
            dry_run=bool(payload.get("dry_run")),
            text=str(payload.get("text") or ""),
        )
    )


@router.post("/replica/forward/label")
async def replica_forward_label(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    label = str(payload.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="标签不能为空")
    return _response(
        await adapter.forward_to_label(
            label,
            chat=str(payload.get("chat") or ""),
            match=str(payload.get("match") or ""),
            dry_run=bool(payload.get("dry_run", True)),
            limit=int(payload["limit"]) if payload.get("limit") is not None else None,
            text=str(payload.get("text") or ""),
        )
    )


@router.post("/replica/remark")
async def replica_remark(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    who = str(payload.get("who") or "").strip()
    if not who:
        raise HTTPException(status_code=400, detail="联系人不能为空")
    return _response(
        await adapter.rename_member(
            who,
            str(payload.get("remark") or ""),
            allow_empty=bool(payload.get("allow_empty")),
        )
    )


@router.post("/replica/remark/probe")
async def replica_remark_probe(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return _response(await adapter.probe_remark(str(payload.get("who") or "")))


@router.post("/replica/add-friend")
async def replica_add_friend(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    group = str(payload.get("group") or "").strip()
    member = str(payload.get("member") or "").strip()
    if not group or not member:
        raise HTTPException(status_code=400, detail="group 和 member 不能为空")
    return _response(
        await adapter.add_friend_from_group(
            group,
            member,
            message=str(payload.get("message") or ""),
            dry_run=bool(payload.get("dry_run")),
        )
    )


@router.post("/replica/add-friend/probe")
async def replica_add_friend_probe(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return _response(
        await adapter.probe_add_friend(
            str(payload.get("group") or ""), str(payload.get("member") or "")
        )
    )


@router.post("/replica/labels/probe")
async def replica_labels_probe(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return _response(await adapter.probe_labels())


@router.post("/replica/forward-voice")
async def replica_forward_voice(
    payload: dict = Body(default={}),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return _response(
        await adapter.forward_voice(
            str(payload.get("who") or ""), str(payload.get("target") or "")
        )
    )


@router.get("/replica/groups/{group_id}/members/watch")
async def replica_group_members_watch(
    group_id: int,
    action: str = Query(default="capture"),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    group = await _group(runtime, group_id)
    return await adapter.group_members_watch(group.external_id, action)


@router.post("/replica/poke")
async def replica_poke(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    who = str(payload.get("who") or "").strip()
    if not who:
        raise HTTPException(status_code=400, detail="联系人不能为空")
    return _response(await adapter.poke(who))


@router.post("/replica/voice-call")
async def replica_voice_call(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    who = str(payload.get("who") or "").strip()
    if not who:
        raise HTTPException(status_code=400, detail="联系人不能为空")
    return _response(
        await adapter.voice_call(who, bool(payload.get("video")))
    )


@router.post("/replica/recall")
async def replica_recall(
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    who = str(payload.get("who") or "").strip()
    if not who:
        raise HTTPException(status_code=400, detail="联系人不能为空")
    return _response(await adapter.recall_last(who))


@router.get("/replica/chat-info")
async def replica_chat_info(
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"info": await adapter.chat_info()}


@router.post("/replica/groups/{group_id}/send-at")
async def replica_send_at(
    group_id: int,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    group = await _group(runtime, group_id)
    text = str(payload.get("text") or "").strip()
    members = [str(item) for item in payload.get("members") or [] if str(item).strip()]
    if not text or not members:
        raise HTTPException(status_code=400, detail="text 和 members 不能为空")
    return _response(await adapter.send_at(group.external_id, text, members))


@router.post("/replica/groups/{group_id}/quote")
async def replica_quote(
    group_id: int,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    group = await _group(runtime, group_id)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")
    return _response(
        await adapter.quote(
            group.external_id, text, str(payload.get("target_text") or "")
        )
    )


@router.post("/replica/groups/{group_id}/send-file")
async def replica_send_file(
    group_id: int,
    file: UploadFile = File(...),
    as_image: bool = Query(default=False),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    group = await _group(runtime, group_id)
    await asyncio.to_thread(UPLOAD_ROOT.mkdir, parents=True, exist_ok=True)
    name = _safe_name(file.filename or "upload.bin")
    target = UPLOAD_ROOT / f"{int(time.time())}_{name}"
    target.write_bytes(await file.read())
    if as_image:
        await adapter.send_image(group.external_id, target)
    else:
        await adapter.send_file(group.external_id, target)
    return {"ok": True, "path": str(target)}


@router.get("/replica/groups/{group_id}/export")
async def replica_export(
    group_id: int,
    fmt: str = Query(default="json"),
    limit: int | None = Query(default=None, ge=1, le=200000),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    group = await _group(runtime, group_id)
    return await adapter.export_history(group.external_id, fmt=fmt, limit=limit)


@router.get("/replica/messages/{message_id}/media-status")
async def replica_media_status(
    message_id: int,
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    message, group = await _message_context(runtime, message_id)
    meta = dict(message.meta or {})
    return {
        "status": await adapter.media_status(group.external_id, meta),
        "meta": meta,
    }


@router.get("/replica/messages/{message_id}/media")
async def replica_media(
    message_id: int,
    tier: str = Query(default="best"),
    original: bool = Query(default=False),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    message, group = await _message_context(runtime, message_id)
    meta = dict(message.meta or {})
    return await adapter.download_media(
        group.external_id, meta, tier=tier, original=original
    )


@router.post("/replica/media/detect-key")
async def replica_detect_image_key(
    payload: dict = Body(default={}),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    result = await adapter.detect_image_key(bool(payload.get("refresh")))
    return {"result": result}


@router.get("/replica/media/images")
async def replica_image_status(
    limit: int = Query(default=100, ge=1, le=1000),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"images": await adapter.list_image_status(limit)}


@router.get("/replica/media/voices")
async def replica_voice_status(
    limit: int = Query(default=100, ge=1, le=1000),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    adapter = _adapter(runtime)
    return {"voices": await adapter.list_voice_status(limit)}


@router.get("/replica/file")
async def replica_file(
    path: str = Query(...),
) -> FileResponse:
    root = MEDIA_ROOT.resolve()
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(status_code=400, detail="非法路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(target)


@router.get("/replica/exports/{filename}")
async def replica_export_file(filename: str) -> FileResponse:
    name = _safe_name(filename)
    target = (EXPORT_ROOT.resolve() / name).resolve()
    if not target.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    return FileResponse(target, filename=name)


def _relative_media(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(MEDIA_ROOT.resolve())).replace("\\", "/")
    except Exception:
        return path.name
