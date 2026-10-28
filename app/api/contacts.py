from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import SessionLocal
from app.models import Contact, DirectMessage

router = APIRouter(tags=["contacts"])


def _media_url(path: str) -> str:
    value = str(path or "")
    if not value:
        return ""
    if value.startswith("replica/"):
        return f"/media/replica/{quote(value.split('/', 1)[1])}"
    if value.startswith("chat/"):
        return f"/media/chat/{quote(value.split('/', 1)[1])}"
    return value


async def _contact_or_404(db, runtime: RuntimeManager, contact_id: int) -> Contact:
    row = await db.get(Contact, contact_id)
    if row is None:
        raise HTTPException(status_code=404, detail="联系人不存在")
    if row.account_key and runtime.account_key and row.account_key != runtime.account_key:
        raise HTTPException(status_code=400, detail="该联系人属于其他微信账号")
    return row


@router.get("/contacts")
async def contacts_list(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    contacts = await runtime.sync_contacts()
    ids = [item["id"] for item in contacts]
    last_by_contact: dict[int, DirectMessage] = {}
    if ids:
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(DirectMessage)
                    .where(DirectMessage.contact_id.in_(ids))
                    .order_by(DirectMessage.id.desc())
                    .limit(5000)
                )
            ).scalars().all()
            for row in rows:
                last_by_contact.setdefault(row.contact_id, row)
    for item in contacts:
        last = last_by_contact.get(item["id"])
        if last is not None:
            item["last_message"] = {
                "content": last.content,
                "kind": last.kind,
                "sender_name": last.sender_name,
                "is_self": last.is_self,
                "created_at": last.created_at.isoformat() if last.created_at else None,
            }
    return {"contacts": contacts}


@router.post("/contacts/sync")
async def contacts_sync(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    return {"contacts": await runtime.sync_contacts()}


@router.patch("/contacts/{contact_id}")
async def contact_update(
    contact_id: int,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    allowed = {
        "is_admin",
        "trigger_mode",
        "keywords",
        "temperature",
        "persona",
    }
    fields = {key: payload[key] for key in allowed if key in payload}
    if not fields:
        raise HTTPException(status_code=400, detail="没有可更新的联系人设置")
    try:
        result = await runtime.update_contact(contact_id, **fields)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "contact": result}


@router.get("/contacts/{contact_id}/messages")
async def contact_messages(
    contact_id: int,
    limit: int = Query(default=100, ge=1, le=300),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    async with SessionLocal() as db:
        contact = await _contact_or_404(db, runtime, contact_id)
        wxid = contact.wxid
    messages: list[dict] = []
    adapter = runtime.adapter
    if adapter is not None and hasattr(adapter, "get_contact_messages"):
        try:
            rows = await adapter.get_contact_messages(wxid, limit)
            for row in rows:
                messages.append(
                    {
                        **row,
                        "contact_id": contact_id,
                        "media_url": _media_url(row.get("media_path") or ""),
                        "created_at": datetime.fromtimestamp(
                            float(row.get("timestamp") or 0),
                            timezone.utc,
                        ).isoformat().replace("+00:00", "Z"),
                    }
                )
        except Exception:
            messages = []
    if not messages:
        async with SessionLocal() as db:
            rows = list(
                (
                    await db.execute(
                        select(DirectMessage)
                        .where(DirectMessage.contact_id == contact_id)
                        .order_by(DirectMessage.id.asc())
                        .limit(limit)
                    )
                ).scalars().all()
            )
        messages = [
            {
                "id": row.id,
                "contact_id": row.contact_id,
                "external_id": row.external_id,
                "sender_wxid": row.sender_wxid,
                "sender_name": row.sender_name,
                "is_self": row.is_self,
                "kind": row.kind,
                "content": row.content,
                "media_url": _media_url(row.media_path),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "meta": row.meta or {},
            }
            for row in rows
        ]
    return {"contact": {"id": contact_id, "wxid": wxid}, "messages": messages}


@router.post("/contacts/{contact_id}/send")
async def contact_send(
    contact_id: int,
    payload: dict = Body(...),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="文字不能超过 2000 字")
    try:
        message = await runtime.send_contact_message(contact_id, text)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": message}
