from __future__ import annotations

from typing import Any

from app.adapters.base import BaseAdapter, EmitFn
from app.adapters.mock import MockAdapter
from app.adapters.replica import ReplicaAdapter
from app.adapters.remote import RemoteAdapter
from app.adapters.wcf import WeChatFerryAdapter

# The old 3.9 UI-automation adapter is intentionally no longer registered.
# It discovered groups by scanning 通讯录管理/最近群聊, which surfaced expired
# temporary <id>@chatroom rows. WeChat 4.x now reads groups/messages from the
# local database only.
ADAPTERS: dict[str, type[BaseAdapter]] = {
    MockAdapter.adapter_id: MockAdapter,
    ReplicaAdapter.adapter_id: ReplicaAdapter,
    RemoteAdapter.adapter_id: RemoteAdapter,
    WeChatFerryAdapter.adapter_id: WeChatFerryAdapter,
}

DEFAULT_ADAPTER = MockAdapter.adapter_id


def create_adapter(
    adapter_id: str, emit: EmitFn, options: dict[str, Any] | None = None
) -> BaseAdapter:
    cls = ADAPTERS.get(adapter_id)
    if cls is None:
        raise ValueError(f"未知适配器：{adapter_id}")
    return cls(emit, options)


def adapter_catalog() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for adapter_id, cls in ADAPTERS.items():
        available, reason = cls.availability()
        result.append(
            {
                "id": adapter_id,
                "name": cls.display_name,
                "description": cls.description,
                "available": available,
                "reason": reason,
            }
        )
    return result


__all__ = [
    "ADAPTERS",
    "DEFAULT_ADAPTER",
    "adapter_catalog",
    "create_adapter",
]
