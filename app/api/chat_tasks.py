from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ChatTask
from app.services.serializers import chat_task_dict

router = APIRouter(tags=["chat-tasks"])


@router.get("/groups/{group_id}/tasks")
async def list_chat_tasks(
    group_id: int,
    limit: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (
        await db.execute(
            select(ChatTask)
            .where(ChatTask.group_id == group_id)
            .order_by(ChatTask.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {"tasks": [chat_task_dict(row) for row in rows]}
