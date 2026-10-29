from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.core.runtime import RuntimeManager
from app.services import bili_knowledge, task_runs, wechatauto_events
from app.services.dsh_primary import (
    get_dsh_chat_lane_service,
    get_dsh_primary_service,
    get_dsh_task_lane_service,
)

router = APIRouter(tags=["diagnostics"])


@router.get("/diagnostics")
async def diagnostics(runtime: RuntimeManager = Depends(get_runtime)) -> dict:
    transport = getattr(runtime, "transport", None)
    transport_stats = await transport.stats() if transport else {}
    try:
        knowledge = await bili_knowledge.stats_async()
    except Exception as exc:
        knowledge = {"count": 0, "error": str(exc)}
    try:
        tasks = await task_runs.list_tasks(50)
    except Exception:
        tasks = []
    counts: dict[str, int] = {}
    for task in tasks:
        status = str(task.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "runtime": {
            "online": runtime.is_online,
            "state": runtime.state,
            "adapter": runtime.adapter_id,
        },
        "transport": {
            "running": bool(getattr(transport, "running", False)),
            "stats": transport_stats,
            "in_process": True,
        },
        "lanes": {
            "primary": get_dsh_primary_service().status(),
            "chat": get_dsh_chat_lane_service().status(),
            "task": get_dsh_task_lane_service().status(),
        },
        "knowledge": knowledge,
        "tasks": {"counts": counts, "recent": tasks[:10]},
        "events": {
            "buffered": len(wechatauto_events.recent(500)),
            "durable": False,
        },
    }
