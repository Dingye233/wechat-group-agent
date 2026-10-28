from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.db import get_db
from app.models import Sticker
from app.schemas import StickerSendRequest, StickerUpdate
from app.services.serializers import sticker_dict
from app.services.stickers import (
    MAX_UPLOAD_BYTES,
    all_tags,
    create_from_upload,
    delete_sticker,
    list_stickers,
    seed_sample_stickers,
    sticker_path,
)

router = APIRouter(tags=["stickers"])


def _parse_tags(raw: str) -> list[str]:
    parts = re.split(r"[,，、#;；\s]+", raw or "")
    result: list[str] = []
    for item in parts:
        item = item.strip()
        if item and item not in result:
            result.append(item[:40])
    return result[:20]


@router.get("/stickers")
async def get_stickers(
    tag: str = "", db: AsyncSession = Depends(get_db)
) -> dict:
    rows = await list_stickers(db, tag)
    return {"stickers": [sticker_dict(row) for row in rows], "tags": await all_tags(db)}


@router.post("/stickers", status_code=201)
async def upload_sticker(
    file: UploadFile = File(...),
    name: str = Form(""),
    tags: str = Form(""),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择图片文件")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        sticker = await create_from_upload(
            db,
            data=data,
            original_name=file.filename,
            name=name.strip() or file.filename.rsplit(".", 1)[0],
            tags=_parse_tags(tags),
            description=description.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"sticker": sticker_dict(sticker)}


@router.patch("/stickers/{sticker_id}")
async def update_sticker(
    sticker_id: int,
    payload: StickerUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    sticker = await db.get(Sticker, sticker_id)
    if sticker is None:
        raise HTTPException(status_code=404, detail="表情包不存在")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values and values["name"] is not None:
        sticker.name = str(values["name"]).strip()[:120] or sticker.name
    if "tags" in values and values["tags"] is not None:
        sticker.tags = [str(item).strip()[:40] for item in values["tags"] if str(item).strip()][
            :20
        ]
    if "description" in values and values["description"] is not None:
        sticker.description = str(values["description"]).strip()[:400]
    await db.commit()
    await db.refresh(sticker)
    return {"sticker": sticker_dict(sticker)}


@router.delete("/stickers/{sticker_id}")
async def remove_sticker(
    sticker_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    sticker = await db.get(Sticker, sticker_id)
    if sticker is None:
        raise HTTPException(status_code=404, detail="表情包不存在")
    await delete_sticker(db, sticker)
    return {"ok": True}


@router.post("/stickers/{sticker_id}/send")
async def send_sticker(
    sticker_id: int,
    payload: StickerSendRequest,
    db: AsyncSession = Depends(get_db),
    runtime: RuntimeManager = Depends(get_runtime),
) -> dict:
    sticker = await db.get(Sticker, sticker_id)
    if sticker is None:
        raise HTTPException(status_code=404, detail="表情包不存在")
    path = await sticker_path(sticker)
    if not path.exists():
        raise HTTPException(status_code=404, detail="表情文件缺失，请重新上传")
    try:
        messages = await runtime.send_manual_message(
            payload.group_id, sticker=sticker
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"发送失败：{exc}") from exc
    return {"ok": True, "messages": messages}


@router.post("/stickers/seed")
async def seed_stickers(db: AsyncSession = Depends(get_db)) -> dict:
    created = await seed_sample_stickers(db)
    rows = (await db.execute(select(Sticker))).scalars().all()
    return {"created": created, "stickers": [sticker_dict(row) for row in rows]}
