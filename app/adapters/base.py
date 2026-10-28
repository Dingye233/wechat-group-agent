from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class AccountInfo:
    wxid: str
    name: str
    avatar: str = ""


@dataclass(slots=True)
class GroupInfo:
    external_id: str
    name: str
    member_count: int = 0
    avatar: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemberInfo:
    wxid: str
    nickname: str
    alias: str = ""
    is_owner: bool = False
    is_admin: bool = False


@dataclass(slots=True)
class IncomingMessage:
    room_id: str
    sender_id: str
    sender_name: str
    content: str
    kind: str = "text"
    timestamp: float = field(default_factory=time.time)
    external_id: str | None = None
    is_self: bool = False
    is_group: bool = True
    media_path: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AdapterEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


EmitFn = Callable[[AdapterEvent], None]


class BaseAdapter(ABC):
    """Protocol adapter contract.

    Implementations may be asynchronous (mock) or wrap a blocking SDK with a
    worker thread (wcf). Events are emitted through ``emit`` and must be safe
    to call from any thread. The runtime serialises them onto the event loop.
    """

    adapter_id: str = "base"
    display_name: str = "Base"
    description: str = ""

    def __init__(self, emit: EmitFn, options: dict[str, Any] | None = None) -> None:
        self._emit = emit
        self.options = options or {}
        self._stop = threading.Event()
        self._last_error = ""

    # ------------------------------------------------------------------ utils
    def emit(self, event_type: str, **data: Any) -> None:
        try:
            self._emit(AdapterEvent(event_type, data))
        except Exception:
            # Event delivery must never kill the adapter loop.
            pass

    def status(self, state: str, message: str = "", **extra: Any) -> None:
        self.emit("status", state=state, message=message, **extra)

    def log(self, message: str, level: str = "info") -> None:
        self.emit("log", message=message, level=level)

    @classmethod
    def availability(cls) -> tuple[bool, str]:
        return True, ""

    # --------------------------------------------------------------- lifecycle
    @abstractmethod
    async def start(self) -> None:
        """Start login flow. Must return quickly; progress is reported by events."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop background work and release protocol resources."""

    # --------------------------------------------------------------- messaging
    @abstractmethod
    async def send_text(self, room_id: str, text: str) -> None:
        ...

    @abstractmethod
    async def send_sticker(self, room_id: str, path: Path) -> None:
        ...

    # ----------------------------------------------------------------- queries
    @abstractmethod
    async def list_groups(self) -> list[GroupInfo]:
        ...

    async def get_members(
        self, room_id: str, include_history: bool = True
    ) -> list[MemberInfo]:
        return []

    async def get_account(self) -> AccountInfo | None:
        return None

    async def refresh(self) -> None:
        """Optional hook to refresh contact/group data."""
        return None

    def set_readable_groups(self, room_ids: set[str]) -> None:
        """Update which groups the runtime allows the adapter to ingest.

        Adapters that can filter at the source (UI automation / SDK readers)
        should override this. The runtime also enforces the switch before
        persisting messages.
        """
        return None

    def set_read_schedule(self, schedule: dict[str, float]) -> None:
        """Set a per-group read check schedule ``{room_id: interval_seconds}``."""
        self.set_readable_groups(set(schedule))

    def set_poll_interval(self, seconds: float) -> None:
        """Set how often the adapter checks read-enabled groups."""
        return None

    def set_media_download_enabled(self, enabled: bool) -> None:
        """Whether the adapter should try to download image/sticker media."""
        return None
