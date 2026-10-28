from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import math
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import adapter_catalog, create_adapter
from app.adapters.replica import AutomationDetached
from app.adapters.base import (
    AdapterEvent,
    BaseAdapter,
    GroupInfo,
    IncomingMessage,
)
from app.config import settings
from app.core.agent import AgentEngine
from app.core.bus import EventBus
from app.db import SessionLocal
from app.models import (
    Account,
    Contact,
    DirectMessage,
    Group,
    GroupConfig,
    GroupMember,
    Message,
    Setting,
    Sticker,
    utcnow,
)
from app.services.assistant import (
    AssistantService,
    cancel_session,
    enqueue_session_message,
    is_session_active,
    take_session_inbox,
)
from app.services.hotkey import GlobalHotkey
from app.services.llm import LLMService
from app.services.memory import (
    maybe_forget_sqlite,
    prune_memories,
    summarize_group_memories,
)
from app.services.serializers import direct_message_dict, group_dict, message_dict
from app.services.settings import get_settings

logger = logging.getLogger(__name__)

_EXPIRED_ROOM_RE = re.compile(r"^\d+@chatroom$", re.I)

_CANCEL_WORDS = {
    "停止",
    "取消",
    "停下",
    "中断",
    "别做了",
    "不用了",
    "stop",
    "cancel",
}

_TOOL_LABELS = {
    "tool": "群伴工具",
    "send_message": "发送消息",
    "read_group": "读取群消息",
    "bili_knowledge": "检索 B站知识库",
    "memory_save": "写入记忆",
    "memory_search": "检索记忆",
    "read": "读取文件",
    "grep": "搜索内容",
    "glob": "查找文件",
    "webfetch": "读取网页",
    "websearch": "搜索网页",
    "read_file": "读取文件",
    "write_file": "写入文件",
    "list_dir": "查看目录",
    "search_files": "搜索文件",
    "run_powershell": "执行 PowerShell",
    "web_request": "请求网页",
    "screen_capture": "截屏",
    "display_image": "显示图片",
    "read_image": "识别图片",
    "save_memory": "写入记忆",
    "search_memories": "检索记忆",
    "create_task": "创建任务",
    "update_task": "更新任务",
    "list_tasks": "查看任务",
    "list_groups": "读取群聊",
    "send_group_message": "发送群消息",
    "read_group_messages": "读取群消息",
    "send_contact_message": "发送私聊消息",
    "send_contact_image": "发送私聊图片",
    "send_image": "发送图片",
    "list_capabilities": "查看能力",
    "discover_capabilities": "发现能力",
    "run_mcp": "运行 MCP",
    "call_interface": "调用接口",
    "bili_search": "搜索 B站",
    "bili_feed": "刷 B站推荐",
    "bili_recommend": "读取 B站推荐",
    "bili_status": "查看 B站账号",
    "bili_video_info": "读取 B站视频信息",
    "bili_transcribe": "转写 B站视频",
    "bili_watch": "观看 B站视频",
    "bili_like": "点赞 B站视频",
    "bili_favorite": "收藏 B站视频",
    "bili_comment": "评论 B站视频",
    "bili_comments": "读取 B站评论",
    "bili_reply_comment": "回复 B站评论",
    "bili_knowledge_search": "检索 B站知识库",
    "bili_knowledge_add": "记录 B站视频到知识库",
    "bili_last_opinion": "读取 B站最近看法",
}


def _is_temporary_room(external_id: str, name: str) -> bool:
    raw = str(external_id or "").split(":", 1)[-1]
    name = str(name or "").strip()
    if not name or name == raw:
        return True
    # A raw "@chatroom" in the display name means WeChat never exposed a
    # real group title: temporary chat. Never list, read or reply.
    return "@chatroom" in name.lower() or bool(_EXPIRED_ROOM_RE.match(name))


def _is_hidden_expired_group(group: Group) -> bool:
    if not str(group.external_id or "").startswith("replica:"):
        return False
    return _is_temporary_room(group.external_id, group.name)


class RuntimeManager:
    """Owns the active adapter, state machine, message store and agent tasks."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.llm = LLMService()
        self.agent = AgentEngine(self, self.llm)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[tuple[int, AdapterEvent]] | None = None
        self._consumer: asyncio.Task | None = None
        self._bg_tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()

        self.state = "offline"
        self.adapter_id = ""
        self.adapter: BaseAdapter | None = None
        self.account: dict[str, str] | None = None
        self.qr_bytes: bytes | None = None
        self.qr_mime = "image/png"
        self.qr_version = 0
        self.last_error = ""
        self.status_message = "未连接"
        self.online_since: float | None = None
        self.service_started_at = time.time()
        self.pinned_group_id = 0
        self.detached = False
        self.detach_reason = ""
        self.detach_at = 0.0
        self._generation = 0
        self._auto_login_done = False
        self._last_media_backfill = 0.0
        self._memory_tasks: dict[int, asyncio.Task] = {}
        self._admin_tasks: dict[str, asyncio.Task] = {}
        self._proactive_tasks: dict[int, asyncio.Task] = {}
        self._memory_loop: asyncio.Task | None = None
        self._hotkey = GlobalHotkey()
        self._hotkey_text = ""
        self._outbox_task: asyncio.Task | None = None
        self._outbox_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=3000)
        self._consumer = asyncio.create_task(
            self._consume_events(), name="adapter-event-consumer"
        )
        self._memory_loop = asyncio.create_task(
            self._memory_scheduler(), name="memory-scheduler"
        )
        try:
            async with SessionLocal() as db:
                data = await get_settings(db)
            self.apply_hotkey(str(data.get("takeover_hotkey") or "Alt+P"))
        except Exception as exc:
            logger.warning("暂停快捷键初始化失败: %s", exc)

    def apply_hotkey(self, hotkey: str) -> dict[str, Any]:
        """(Re)register the global pause hotkey.

        The callback runs on the hotkey thread, so it is marshalled back to
        the runtime event loop.  Pausing never resumes automatically.
        """

        text = str(hotkey or "").strip()
        loop = self._loop

        def on_hotkey() -> None:
            if loop is None or loop.is_closed():
                return
            asyncio.run_coroutine_threadsafe(self._pause_from_hotkey(), loop)

        previous = self._hotkey_text
        result = self._hotkey.set(text, on_hotkey)
        if not result.get("ok") and previous and previous != text:
            restored = self._hotkey.set(previous, on_hotkey)
            if restored.get("ok"):
                result["restored"] = restored.get("hotkey") or previous
                result["hotkey"] = result["restored"]
                result["active"] = True
        self._hotkey_text = str(
            result.get("hotkey") or (self._hotkey.canonical or previous or text)
        )
        if result.get("error"):
            logger.warning("暂停快捷键不可用: %s", result["error"])
        return result

    async def _pause_from_hotkey(self) -> None:
        if self.detached:
            return
        label = self._hotkey_text or "Alt+P"
        await self.detach(f"按下快捷键 {label}，自动操作已暂停")

    async def maybe_auto_login(self) -> None:
        if self._auto_login_done:
            return
        self._auto_login_done = True
        try:
            async with SessionLocal() as db:
                data = await get_settings(db)
            auto = str(data.get("auto_login") or "").strip()
            if auto in {"mock", "uia", "wcf", "replica"}:
                await self.login(auto)
        except Exception as exc:
            logger.warning("自动登录失败: %s", exc)

    async def login(self, adapter_id: str) -> dict[str, Any]:
        async with self._lock:
            if self.adapter_id == adapter_id and self.state in {"online", "qr", "starting"}:
                return self.status_dict()

            catalog = {item["id"]: item for item in adapter_catalog()}
            item = catalog.get(adapter_id)
            if item is None:
                raise RuntimeError(f"未知适配器：{adapter_id}")
            if not item["available"]:
                # Do not disturb the currently connected adapter when the
                # user asks for an unavailable one.
                raise RuntimeError(item["reason"])

            await self._stop_current(announce=False)
            self._generation += 1
            generation = self._generation
            self.adapter_id = adapter_id
            self.state = "starting"
            self.last_error = ""
            self.status_message = "正在启动适配器…"
            self.account = None
            self.qr_bytes = None
            self.qr_version = 0
            self.online_since = None
            self.detached = False
            self.detach_reason = ""
            self.detach_at = 0.0
            self.broadcast("status", self.status_dict())

            try:
                async with SessionLocal() as db:
                    data = await get_settings(db)
                options = {
                    "wechat_exe": data.get("wcf_wechat_exe", ""),
                    "replica_wechat_exe": data.get("replica_wechat_exe", ""),
                    "replica_db_dir": data.get("replica_db_dir", ""),
                }
            except Exception:
                options = {}
            self.adapter = create_adapter(adapter_id, self._make_emit(generation), options)
            try:
                await self.adapter.start()
            except Exception as exc:
                self.state = "error"
                self.last_error = str(exc)
                self.status_message = str(exc)
                self.adapter = None
                self.broadcast("status", self.status_dict())
                raise
            return self.status_dict()

    def _make_emit(self, generation: int):
        def _emit(event: AdapterEvent) -> None:
            self.post_event(event, generation)

        return _emit

    async def logout(self, *, announce: bool = True) -> None:
        async with self._lock:
            await self._stop_current(announce=announce)

    async def _stop_current(self, *, announce: bool) -> None:
        self._generation += 1
        adapter = self.adapter
        self.adapter = None
        if adapter is not None:
            try:
                await adapter.stop()
            except Exception as exc:
                logger.warning("停止适配器失败: %s", exc)
        self._cancel_agent_tasks()
        self.state = "offline"
        self.status_message = "已退出登录"
        self.account = None
        self.qr_bytes = None
        self.online_since = None
        self.pinned_group_id = 0
        self.detached = False
        self.detach_reason = ""
        self.detach_at = 0.0
        try:
            async with SessionLocal() as db:
                await db.execute(update(Account).values(active=False))
                await db.commit()
        except Exception:
            pass
        if announce:
            self.broadcast("status", self.status_dict())

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            self._hotkey.stop()
        if self._outbox_task and not self._outbox_task.done():
            self._outbox_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._outbox_task
            self._outbox_task = None
        with contextlib.suppress(Exception):
            await self.logout(announce=False)
        if self._memory_loop:
            self._memory_loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._memory_loop
            self._memory_loop = None
        for task in list(self._memory_tasks.values()):
            task.cancel()
        self._memory_tasks.clear()
        for task in list(self._admin_tasks.values()):
            task.cancel()
        self._admin_tasks.clear()
        if self._consumer:
            self._consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer
        await self.llm.close()

    # --------------------------------------------------------- event plumbing
    def post_event(self, event: AdapterEvent, generation: int | None = None) -> None:
        if generation is None:
            generation = self._generation
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._enqueue_event, generation, event)
        except RuntimeError:
            pass

    def _enqueue_event(self, generation: int, event: AdapterEvent) -> None:
        if self._queue is None:
            return
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        try:
            self._queue.put_nowait((generation, event))
        except asyncio.QueueFull:
            pass

    async def _consume_events(self) -> None:
        assert self._queue is not None
        while True:
            try:
                generation, event = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                if generation != self._generation:
                    continue
                await self._handle_event(event)
            except Exception:
                logger.exception("处理适配器事件失败: %s", event.type)
            finally:
                self._queue.task_done()

    async def _handle_event(self, event: AdapterEvent) -> None:
        if event.type == "status":
            was_online = self.state == "online"
            state = str(event.data.get("state") or "offline")
            message = str(event.data.get("message") or "")
            self.status_message = message or self.status_message
            if state == "online":
                self.state = "online"
                self.online_since = self.online_since or time.time()
                self.last_error = ""
                self.qr_bytes = None
            elif state == "qr":
                self.state = "qr"
                self.last_error = ""
                self.online_since = None
            elif state == "starting":
                self.state = "starting"
            elif state == "error":
                self.state = "error"
                self.last_error = message
            elif state == "offline":
                self.state = "offline"
                self.account = None
                self.online_since = None
            self.broadcast("status", self.status_dict())
            if state == "online" and not was_online and not self.detached:
                # Give the adapter a few seconds to finish its initial sync,
                # then verify and retry messages that never reached WeChat.
                self._schedule_outbox_retry(6.0)
        elif event.type == "qr":
            encoded = event.data.get("image_b64") or ""
            try:
                self.qr_bytes = base64.b64decode(encoded)
                self.qr_mime = str(event.data.get("mime") or "image/png")
                self.qr_version += 1
                self.state = "qr"
                self.broadcast("status", self.status_dict())
            except Exception:
                logger.warning("无法解析二维码图片")
        elif event.type == "account":
            await self._handle_account(event.data)
        elif event.type == "groups":
            await self._handle_groups(event.data.get("groups") or [])
        elif event.type == "message":
            await self._handle_message(event.data.get("message"))
        elif event.type == "direct_message":
            await self._handle_direct_message(event.data.get("message"))
        elif event.type == "log":
            level = str(event.data.get("level") or "info")
            message = str(event.data.get("message") or "")
            if level == "error":
                self.last_error = message
                logger.error("adapter: %s", message)
            elif level in ("warn", "warning"):
                logger.warning("adapter: %s", message)
            else:
                logger.info("adapter: %s", message)
            self.broadcast("adapter.log", {"level": level, "message": message})
        elif event.type == "detached":
            self.detached = True
            self.detach_reason = str(
                event.data.get("reason") or "检测到用户接管鼠标"
            )
            self.detach_at = float(event.data.get("at") or time.time())
            self.status_message = (
                f"{self.detach_reason}，自动操作已暂停，请点击重新接管"
            )
            self.broadcast(
                "adapter.detached",
                {
                    "reason": self.detach_reason,
                    "at": self.detach_at,
                },
            )
            self.broadcast("status", self.status_dict())
        elif event.type == "reattached":
            self.detached = False
            self.detach_reason = ""
            self.detach_at = 0.0
            self.status_message = "已重新接管"
            self.broadcast("adapter.reattached", {})
            self.broadcast("status", self.status_dict())
        elif event.type == "error":
            self.state = "error"
            self.last_error = str(event.data.get("message") or "适配器错误")
            self.status_message = self.last_error
            self.broadcast("status", self.status_dict())
            self.broadcast(
                "adapter.error",
                {"message": self.last_error},
            )

    # ----------------------------------------------------------------- account
    async def _handle_account(self, data: dict[str, Any]) -> None:
        wxid = str(data.get("wxid") or "").strip()
        if not wxid:
            return
        self.account = {
            "wxid": wxid,
            "name": str(data.get("name") or "微信助手"),
            "avatar": str(data.get("avatar") or ""),
        }
        try:
            async with SessionLocal() as db:
                await db.execute(update(Account).values(active=False))
                account = (
                    await db.execute(
                        select(Account).where(
                            Account.wxid == wxid,
                            Account.adapter == self.adapter_id,
                        )
                    )
                ).scalar_one_or_none()
                if account is None:
                    account = Account(
                        adapter=self.adapter_id,
                        wxid=wxid,
                        name=self.account["name"],
                        avatar=self.account["avatar"],
                        active=True,
                    )
                    db.add(account)
                else:
                    account.name = self.account["name"]
                    account.avatar = self.account["avatar"]
                    account.active = True
                await db.commit()
        except Exception:
            logger.exception("保存账号失败")
        self.broadcast("account.updated", self.account)
        self.broadcast("status", self.status_dict())
        await self.apply_read_filter()
        task = asyncio.create_task(self.sync_contacts(), name="sync-contacts")
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ------------------------------------------------------------------ groups
    async def _handle_groups(self, groups: list[GroupInfo]) -> None:
        if not groups:
            return
        account_key = self.account_key
        async with SessionLocal() as db:
            data = await get_settings(db)
            new_count = 0
            for info in groups:
                group = (
                    await db.execute(
                        select(Group).where(Group.external_id == info.external_id)
                    )
                ).scalar_one_or_none()
                if group is None:
                    group = Group(
                        external_id=info.external_id,
                        account_key=account_key,
                        name=info.name or info.external_id,
                        avatar=info.avatar or "",
                        member_count=max(int(info.member_count or 0), 0),
                    )
                    db.add(group)
                    await db.flush()
                    config = GroupConfig(
                        group_id=group.id,
                        read_enabled=self.adapter_id == "mock",
                        trigger_mode=str(data.get("default_trigger_mode") or "mention"),
                        keywords=list(data.get("default_keywords") or []),
                        cooldown_seconds=30,
                        max_replies_per_hour=30,
                        sticker_enabled=True,
                    )
                    meta = info.meta or {}
                    if meta.get("demo_agent_enabled"):
                        config.agent_enabled = True
                        config.trigger_mode = str(meta.get("trigger_mode") or "mention")
                        config.cooldown_seconds = 10
                    db.add(config)
                    new_count += 1
                else:
                    if (
                        account_key
                        and group.account_key
                        and group.account_key != account_key
                    ):
                        # The same group name can be visible from two WeChat
                        # accounts (for example a shared test group). Keep it
                        # scoped to the account that first discovered it
                        # instead of moving another account's data.
                        logger.info(
                            "群聊 %s 属于账号 %s，当前账号 %s，跳过",
                            group.external_id,
                            group.account_key,
                            account_key,
                        )
                        continue
                    if account_key and not group.account_key:
                        group.account_key = account_key
                    if info.name and group.name != info.name:
                        group.name = info.name
                    if info.avatar and group.avatar != info.avatar:
                        group.avatar = info.avatar
                    if info.member_count and group.member_count != info.member_count:
                        group.member_count = info.member_count
                    if await db.get(GroupConfig, group.id) is None:
                        db.add(
                            GroupConfig(
                                group_id=group.id,
                                read_enabled=self.adapter_id == "mock",
                                trigger_mode=str(
                                    data.get("default_trigger_mode") or "mention"
                                ),
                                keywords=list(data.get("default_keywords") or []),
                                cooldown_seconds=30,
                                max_replies_per_hour=30,
                                sticker_enabled=True,
                            )
                        )
            await db.commit()
        self.broadcast(
            "groups.updated",
            {"count": len(groups), "new": new_count},
        )
        self.broadcast("status", self.status_dict())
        await self.apply_read_filter()

    async def apply_read_filter(self) -> None:
        """Tell the adapter which groups are allowed to be read."""
        adapter = self.adapter
        if adapter is None:
            return
        try:
            async with SessionLocal() as db:
                settings = await get_settings(db)
                statement = (
                    select(
                        Group.external_id,
                        GroupConfig.read_interval_seconds,
                        Group.name,
                    )
                    .join(GroupConfig, GroupConfig.group_id == Group.id)
                    .where(GroupConfig.read_enabled.is_(True))
                )
                if self.account_key:
                    statement = statement.where(Group.account_key == self.account_key)
                rows = (await db.execute(statement)).all()
                pinned_external = ""
                pinned_id = int(settings.get("pinned_group_id") or 0)
                if pinned_id:
                    pinned_group = await db.get(Group, pinned_id)
                    if pinned_group and (
                        not self.account_key
                        or pinned_group.account_key == self.account_key
                    ):
                        pinned_config = await db.get(GroupConfig, pinned_id)
                        if pinned_config and pinned_config.read_enabled:
                            pinned_external = pinned_group.external_id
                        else:
                            # Reading was disabled; clear the pin so it does
                            # not unexpectedly resume later.
                            row = await db.get(Setting, "pinned_group_id")
                            if row is not None:
                                row.value = 0
                                await db.commit()
                            pinned_id = 0
                    else:
                        # Keep the setting for the other account, but never
                        # open that chat while a different account is active.
                        pinned_id = 0
                self.pinned_group_id = pinned_id
            default_interval = float(
                settings.get("read_poll_interval_seconds") or 1.0
            )
            schedule = {
                str(external_id): max(
                    1.0, float(interval or default_interval)
                )
                for external_id, interval, name in rows
                if external_id and not _is_temporary_room(external_id, name)
            }
            schedule_setter = getattr(adapter, "set_read_schedule", None)
            if callable(schedule_setter):
                schedule_setter(schedule)
            else:
                readable_setter = getattr(adapter, "set_readable_groups", None)
                if callable(readable_setter):
                    readable_setter(set(schedule))
            pin_setter = getattr(adapter, "set_pinned_group", None)
            if callable(pin_setter):
                pin_setter(pinned_external)
            media_setter = getattr(adapter, "set_media_download_enabled", None)
            if callable(media_setter):
                media_setter(
                    bool(settings.get("read_media_download_enabled", False))
                )
            logger.info(
                "群消息读取开关：%s 个群开启，独立计时器，默认 %.1f 秒，置顶=%s",
                len(schedule),
                default_interval,
                pinned_external or "无",
            )
            self._schedule_media_backfill()
        except Exception as exc:
            logger.warning("应用群读取开关失败: %s", exc)

    def _schedule_media_backfill(self) -> None:
        if self.adapter_id != "replica" or not self.is_online:
            return
        now = time.time()
        if now - self._last_media_backfill < 20:
            return
        self._last_media_backfill = now
        task = asyncio.create_task(
            self.backfill_images(), name="replica-image-backfill"
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def backfill_images(
        self, group_id: int | None = None, limit_per_group: int = 30
    ) -> dict[str, int]:
        """Decrypt images already stored before URL support was enabled."""
        if self.adapter_id != "replica" or self.adapter is None:
            return {"updated": 0}
        async with SessionLocal() as db:
            statement = (
                select(Group.id, Group.external_id)
                .join(GroupConfig, GroupConfig.group_id == Group.id)
                .where(GroupConfig.read_enabled.is_(True))
            )
            if self.account_key:
                statement = statement.where(Group.account_key == self.account_key)
            if group_id is not None:
                statement = statement.where(Group.id == group_id)
            groups = (await db.execute(statement)).all()
            jobs: list[tuple[int, str, dict[str, Any], str]] = []
            for current_group_id, external_id in groups:
                rows = (
                    await db.execute(
                        select(Message)
                        .where(
                            Message.group_id == current_group_id,
                            Message.kind == "image",
                            Message.media_path == "",
                        )
                        .order_by(Message.id.desc())
                        .limit(limit_per_group)
                    )
                ).scalars().all()
                for message in rows:
                    meta = dict(message.meta or {})
                    if meta.get("local_id") is None:
                        continue
                    jobs.append((message.id, external_id, meta, message.content))
        updated = 0
        for message_id, external_id, meta, content in jobs:
            try:
                result = await self.adapter.download_media(external_id, meta)
            except Exception as exc:
                logger.debug("回填图片失败 %s: %s", message_id, exc)
                continue
            media_path = str(result.get("media_path") or "")
            if not media_path:
                continue
            async with SessionLocal() as db:
                message = await db.get(Message, message_id)
                if message is None or message.media_path:
                    continue
                message.media_path = media_path
                await db.commit()
                await db.refresh(message)
                payload = message_dict(message)
            self.broadcast("message.updated", payload)
            updated += 1
        if updated:
            logger.info("已回填 %s 张历史图片", updated)
        return {"updated": updated}

    async def pin_group(self, group_id: int) -> dict[str, Any]:
        if not self.is_online or self.adapter is None:
            raise RuntimeError("微信账号未登录")
        self._ensure_attached()
        allow = getattr(self.adapter, "allow_ui_action", None)
        if callable(allow):
            allow(12.0)
        async with SessionLocal() as db:
            group = await db.get(Group, group_id)
            if group is None:
                raise RuntimeError("群聊不存在")
            if (
                group.account_key
                and self.account_key
                and group.account_key != self.account_key
            ):
                raise RuntimeError("该群聊属于其他微信账号")
            config = await db.get(GroupConfig, group_id)
            if config is None:
                config = GroupConfig(
                    group_id=group_id,
                    read_enabled=True,
                    agent_enabled=False,
                )
                db.add(config)
            else:
                config.read_enabled = True
            row = await db.get(Setting, "pinned_group_id")
            if row is None:
                db.add(Setting(key="pinned_group_id", value=group_id))
            else:
                row.value = group_id
            await db.commit()
            name = group.name
        self.pinned_group_id = group_id
        await self.apply_read_filter()
        self.broadcast(
            "pinned.updated",
            {"group_id": group_id, "name": name},
        )
        self.broadcast(
            "groups.updated",
            {"count": 1, "group_id": group_id, "reason": "pin"},
        )
        self.broadcast("status", self.status_dict())
        return {"pinned_group_id": group_id, "name": name}

    async def unpin_group(self, group_id: int | None = None) -> dict[str, Any]:
        async with SessionLocal() as db:
            row = await db.get(Setting, "pinned_group_id")
            if row is not None:
                row.value = 0
                await db.commit()
        self.pinned_group_id = 0
        await self.apply_read_filter()
        self.broadcast("pinned.updated", {"group_id": 0, "name": ""})
        self.broadcast(
            "groups.updated",
            {"count": 1, "group_id": group_id, "reason": "unpin"},
        )
        self.broadcast("status", self.status_dict())
        return {"pinned_group_id": 0, "name": ""}

    # ---------------------------------------------------------------- messages
    async def _handle_message(self, data: Any) -> None:
        if data is None:
            return
        try:
            incoming = data if isinstance(data, IncomingMessage) else IncomingMessage(**data)
        except Exception as exc:
            logger.warning("无效消息事件: %s", exc)
            return
        result = await self._store_incoming(incoming)
        if result is None:
            return
        group_id, message_id, trigger = result
        if trigger:
            self._schedule_agent(group_id, message_id)

    async def _store_incoming(
        self, incoming: IncomingMessage
    ) -> tuple[int, int, bool] | None:
        now = time.time()
        timestamp = incoming.timestamp if math.isfinite(incoming.timestamp) else now
        if timestamp <= 0 or abs(now - timestamp) > 60 * 60 * 24 * 30:
            timestamp = now
        created = datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
        incoming_meta = incoming.meta or {}
        if (
            str(incoming.room_id or "").startswith("replica:")
            and str(incoming_meta.get("source") or "") == "replica"
            and _is_temporary_room(
                incoming.room_id,
                str(incoming_meta.get("group_name") or ""),
            )
        ):
            logger.info("忽略临时群消息：%s", incoming.room_id)
            return None

        async with SessionLocal() as db:
            group = (
                await db.execute(
                    select(Group).where(Group.external_id == incoming.room_id)
                )
            ).scalar_one_or_none()
            if group is not None and str(group.external_id or "").startswith(
                "replica:"
            ) and _is_temporary_room(group.external_id, group.name):
                return None
            if (
                group is not None
                and group.account_key
                and self.account_key
                and group.account_key != self.account_key
            ):
                logger.info(
                    "忽略其他微信账号的消息：%s（群账号 %s，当前 %s）",
                    incoming.room_id,
                    group.account_key,
                    self.account_key,
                )
                return None
            created_group = False
            if group is None:
                name = str((incoming.meta or {}).get("group_name") or "未命名群聊")
                group = Group(
                    external_id=incoming.room_id,
                    account_key=self.account_key,
                    name=name or incoming.room_id,
                )
                db.add(group)
                await db.flush()
                data = await get_settings(db)
                db.add(
                    GroupConfig(
                        group_id=group.id,
                        read_enabled=self.adapter_id == "mock",
                        trigger_mode=str(data.get("default_trigger_mode") or "mention"),
                        keywords=list(data.get("default_keywords") or []),
                        cooldown_seconds=30,
                        max_replies_per_hour=30,
                        sticker_enabled=True,
                    )
                )
                created_group = True
            elif not group.name or group.name == group.external_id:
                meta_name = str((incoming.meta or {}).get("group_name") or "")
                if meta_name:
                    group.name = meta_name

            config = await db.get(GroupConfig, group.id)
            if config is None:
                data = await get_settings(db)
                config = GroupConfig(
                    group_id=group.id,
                    read_enabled=self.adapter_id == "mock",
                    trigger_mode=str(data.get("default_trigger_mode") or "mention"),
                    keywords=list(data.get("default_keywords") or []),
                    cooldown_seconds=30,
                    max_replies_per_hour=30,
                    sticker_enabled=True,
                )
                db.add(config)
            if not config.read_enabled:
                # The group is visible in the dashboard, but its messages are
                # intentionally not ingested until the user enables reading.
                if created_group or config in db.new:
                    await db.commit()
                    self.broadcast(
                        "groups.updated",
                        {"count": 1, "new": int(created_group)},
                    )
                return None

            meta = incoming.meta or {}
            source = str(meta.get("source") or "")
            if source == "replica" and incoming.is_self:
                # Sending records an optimistic dashboard row first. The 4.x
                # database listener then sees the same message and must consume
                # that row instead of inserting a duplicate. Historical sync
                # uses a wider window so toggling read off/on cannot re-import
                # messages that were already sent from the dashboard.
                historical = bool(meta.get("historical"))
                window = 600 if historical else 120
                pending = (
                    await db.execute(
                        select(Message)
                        .where(
                            Message.group_id == group.id,
                            Message.is_self.is_(True),
                            Message.external_id.is_(None),
                            Message.created_at >= created - timedelta(seconds=window),
                            Message.created_at <= created + timedelta(seconds=window),
                        )
                        .order_by(Message.id.asc())
                        .limit(50)
                    )
                ).scalars().all()
                for row in pending:
                    row_meta = row.meta or {}
                    if not historical and not row_meta.get("_replica_pending"):
                        continue
                    if not self._outgoing_matches(row, incoming.kind, incoming.content):
                        continue
                    row.meta = {
                        **row_meta,
                        "_replica_pending": False,
                        "_replica_outgoing": True,
                        "_replica_confirmed": incoming.external_id or "",
                    }
                    if incoming.media_path and not row.media_path:
                        row.media_path = incoming.media_path
                    await db.commit()
                    return None
            if source.startswith("uia"):
                # Reopening a chat or syncing history gives UIA controls new
                # runtime ids. Deduplicate by content/time so the "last other
                # person's message" is not inserted again on every refresh.
                historical = bool(meta.get("historical"))
                kind = incoming.kind or "text"
                window = 600 if historical else 45
                duplicate_stmt = select(Message.id).where(
                    Message.group_id == group.id,
                    Message.kind == kind,
                    Message.content == (incoming.content or ""),
                    Message.created_at >= created - timedelta(seconds=window),
                    Message.created_at <= created + timedelta(seconds=window),
                )
                if not historical and incoming.sender_name:
                    duplicate_stmt = duplicate_stmt.where(
                        Message.sender_name == incoming.sender_name
                    )
                duplicate = (
                    await db.execute(duplicate_stmt.limit(1))
                ).scalar_one_or_none()
                if duplicate is not None:
                    return None

            if incoming.external_id:
                exists = (
                    await db.execute(
                        select(Message.id).where(
                            Message.external_id == incoming.external_id
                        )
                    )
                ).scalar_one_or_none()
                if exists:
                    return None

            sender_name = incoming.sender_name or incoming.sender_id
            if incoming.is_self:
                sender_name = self.bot_name or sender_name or "我"

            if incoming.sender_id:
                if not incoming.is_self and (
                    not sender_name or sender_name == incoming.sender_id
                ):
                    existing_name = (
                        await db.execute(
                            select(GroupMember.nickname).where(
                                GroupMember.group_id == group.id,
                                GroupMember.wxid == incoming.sender_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing_name and existing_name != incoming.sender_id:
                        sender_name = existing_name

                if incoming.is_self:
                    nickname = self.bot_name or sender_name or incoming.sender_id
                else:
                    nickname = sender_name or incoming.sender_id

                if incoming.is_self or nickname != incoming.sender_id:
                    conflict_nickname = nickname
                else:
                    # Do not overwrite a real nickname with a wxid placeholder
                    # if the member was inserted concurrently by another caller.
                    conflict_nickname = case(
                        (
                            or_(
                                GroupMember.nickname == "",
                                GroupMember.nickname.is_(None),
                                GroupMember.nickname == GroupMember.wxid,
                            ),
                            nickname,
                        ),
                        else_=GroupMember.nickname,
                    )
                await db.execute(
                    sqlite_insert(GroupMember)
                    .values(
                        group_id=group.id,
                        wxid=incoming.sender_id,
                        nickname=nickname,
                        updated_at=utcnow(),
                    )
                    .on_conflict_do_update(
                        index_elements=["group_id", "wxid"],
                        set_={
                            "nickname": conflict_nickname,
                            "updated_at": utcnow(),
                        },
                    )
                )

            message = Message(
                external_id=incoming.external_id,
                group_id=group.id,
                sender_wxid=incoming.sender_id or "",
                sender_name=sender_name or "",
                is_from_agent=False,
                is_self=incoming.is_self,
                kind=incoming.kind or "text",
                content=incoming.content or "",
                media_path=incoming.media_path or "",
                meta=incoming.meta or {},
                created_at=created,
            )
            db.add(message)
            if group.last_message_at is None or created > group.last_message_at:
                group.last_message_at = created
            await db.flush()
            await db.refresh(message)
            message_id = message.id
            group_id = group.id
            payload = message_dict(message)
            await db.commit()

        self.broadcast("message.new", payload)
        if config.read_enabled:
            self._schedule_memory_summary(group_id)
            if (
                not incoming.is_self
                and config.agent_enabled
                and config.proactive_enabled
            ):
                self._schedule_proactive(
                    group_id, int(config.proactive_interval_minutes or 90)
                )
        trigger = (
            not incoming.is_self
            and (incoming.kind or "text") in ("text", "app")
            and not bool((incoming.meta or {}).get("historical"))
        )
        return group_id, message_id, trigger

    # ------------------------------------------------------------- contacts
    @staticmethod
    def _contact_payload(row: Contact) -> dict[str, Any]:
        return {
            "id": row.id,
            "wxid": row.wxid,
            "name": row.name or row.remark or row.wxid,
            "remark": row.remark,
            "is_admin": bool(row.is_admin),
            "trigger_mode": row.trigger_mode or "all",
            "keywords": list(row.keywords or []),
            "temperature": float(row.temperature if row.temperature is not None else 0.3),
            "persona": row.persona or "",
            "last_message_at": row.last_message_at.isoformat()
            if row.last_message_at
            else None,
        }

    async def sync_contacts(self) -> list[dict[str, Any]]:
        adapter = self.adapter
        if adapter is None or not hasattr(adapter, "list_contacts"):
            return []
        try:
            rows = await adapter.list_contacts()
        except Exception as exc:
            logger.warning("读取联系人失败: %s", exc)
            return []
        account_key = self.account_key
        async with SessionLocal() as db:
            existing = {
                row.wxid: row
                for row in (
                    await db.execute(
                        select(Contact).where(Contact.account_key == account_key)
                    )
                ).scalars().all()
            }
            changed = False
            for item in rows:
                wxid = str(item.get("wxid") or "").strip()
                if not wxid:
                    continue
                row = existing.get(wxid)
                if row is None:
                    row = Contact(
                        account_key=account_key,
                        wxid=wxid,
                        name=str(item.get("name") or wxid),
                        remark=str(item.get("remark") or ""),
                    )
                    db.add(row)
                    existing[wxid] = row
                    changed = True
                else:
                    name = str(item.get("name") or row.name or wxid)
                    remark = str(item.get("remark") or row.remark or "")
                    if row.name != name or row.remark != remark:
                        changed = True
                    row.name = name
                    row.remark = remark
            await db.commit()
            contacts = list(
                (
                    await db.execute(
                        select(Contact)
                        .where(Contact.account_key == account_key)
                        .order_by(Contact.is_admin.desc(), Contact.name.asc())
                    )
                ).scalars().all()
            )
            payloads = [self._contact_payload(row) for row in contacts]
        await self._sync_admin_contacts()
        if changed:
            self.broadcast("contacts.updated", {"count": len(payloads)})
        return payloads

    async def _sync_admin_contacts(self) -> None:
        adapter = self.adapter
        if adapter is None or not hasattr(adapter, "set_admin_contacts"):
            return
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(Contact.wxid).where(
                        Contact.account_key == self.account_key,
                        Contact.is_admin.is_(True),
                    )
                )
            ).scalars().all()
        adapter.set_admin_contacts(set(rows))

    async def set_contact_admin(self, contact_id: int, is_admin: bool) -> dict[str, Any]:
        async with SessionLocal() as db:
            row = await db.get(Contact, contact_id)
            if row is None:
                raise RuntimeError("联系人不存在")
            if row.account_key and self.account_key and row.account_key != self.account_key:
                raise RuntimeError("该联系人属于其他微信账号")
            row.is_admin = bool(is_admin)
            await db.commit()
            await db.refresh(row)
            payload = self._contact_payload(row)
        await self._sync_admin_contacts()
        self.broadcast("contact.updated", payload)
        return payload

    async def update_contact(self, contact_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {
            "is_admin",
            "trigger_mode",
            "keywords",
            "temperature",
            "persona",
        }
        admin_changed = False
        async with SessionLocal() as db:
            row = await db.get(Contact, contact_id)
            if row is None:
                raise RuntimeError("联系人不存在")
            if row.account_key and self.account_key and row.account_key != self.account_key:
                raise RuntimeError("该联系人属于其他微信账号")
            for key, value in fields.items():
                if key not in allowed:
                    continue
                if key == "is_admin":
                    if bool(row.is_admin) != bool(value):
                        admin_changed = True
                    row.is_admin = bool(value)
                elif key == "trigger_mode":
                    row.trigger_mode = str(value or "all")
                elif key == "keywords":
                    if isinstance(value, str):
                        value = [item.strip() for item in value.split(",")]
                    row.keywords = [
                        str(item).strip()
                        for item in (value or [])
                        if str(item).strip()
                    ][:20]
                elif key == "temperature":
                    try:
                        row.temperature = max(0.0, min(2.0, float(value)))
                    except (TypeError, ValueError):
                        row.temperature = 0.0
                elif key == "persona":
                    row.persona = str(value or "")[:4000]
            await db.commit()
            await db.refresh(row)
            payload = self._contact_payload(row)
        if admin_changed:
            await self._sync_admin_contacts()
        self.broadcast("contact.updated", payload)
        return payload

    async def _handle_direct_message(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        wxid = str(data.get("contact_wxid") or "").strip()
        if not wxid:
            return
        content = str(data.get("content") or "")
        kind = str(data.get("kind") or "text")
        external_id = str(data.get("external_id") or "")
        created = datetime.fromtimestamp(
            float(data.get("timestamp") or time.time()), timezone.utc
        ).replace(tzinfo=None)
        async with SessionLocal() as db:
            contact = (
                await db.execute(
                    select(Contact).where(
                        Contact.account_key == self.account_key,
                        Contact.wxid == wxid,
                    )
                )
            ).scalar_one_or_none()
            if contact is None:
                contact = Contact(
                    account_key=self.account_key,
                    wxid=wxid,
                    name=str(data.get("sender_name") or wxid),
                    remark="",
                    is_admin=False,
                )
                db.add(contact)
                await db.flush()
            if external_id:
                exists = (
                    await db.execute(
                        select(DirectMessage.id).where(
                            DirectMessage.external_id == external_id
                        )
                    )
                ).scalar_one_or_none()
                if exists:
                    return
            if bool(data.get("is_self")):
                recent = (
                    await db.execute(
                        select(DirectMessage)
                        .where(
                            DirectMessage.contact_id == contact.id,
                            DirectMessage.is_self.is_(True),
                            DirectMessage.external_id.is_(None),
                            DirectMessage.created_at >= created - timedelta(seconds=120),
                        )
                        .order_by(DirectMessage.id.desc())
                        .limit(20)
                    )
                ).scalars().all()
                for row in recent:
                    if (row.content or "").strip() == content.strip():
                        row.meta = {
                            **(row.meta or {}),
                            "_direct_pending": False,
                            "_direct_confirmed": external_id,
                        }
                        await db.commit()
                        return
            row = DirectMessage(
                external_id=external_id or None,
                contact_id=contact.id,
                account_key=self.account_key,
                sender_wxid=str(data.get("sender_wxid") or ""),
                sender_name=str(data.get("sender_name") or contact.name or wxid),
                is_self=bool(data.get("is_self")),
                kind=kind,
                content=content,
                media_path=str(data.get("media_path") or ""),
                meta=data.get("meta") or {},
                created_at=created,
            )
            db.add(row)
            contact.last_message_at = created
            await db.commit()
            await db.refresh(row)
            payload = direct_message_dict(row)
        self.broadcast("direct.message", payload)
        if not bool(data.get("is_self")) and kind in ("text", "app"):
            enabled = bool(contact.is_admin)
            if enabled and str(contact.trigger_mode or "all") == "keyword":
                keywords = [
                    str(item).lower()
                    for item in (contact.keywords or [])
                    if str(item).strip()
                ]
                enabled = any(item in content.lower() for item in keywords)
            if enabled:
                self._schedule_admin_reply(contact.id, wxid, content)

    @staticmethod
    def _image_paths_from_trace(trace: list[dict[str, Any]]) -> list[Path]:
        paths: list[Path] = []
        seen: set[str] = set()
        image_tools = {"display_image", "screen_capture", "run_mcp", "call_interface"}
        for item in trace:
            if item.get("tool") not in image_tools:
                continue
            raw = str(item.get("output") or "")
            url = ""
            path_text = ""
            try:
                data = json.loads(raw)
            except Exception:
                data = None
            if isinstance(data, dict):
                url = str(data.get("url") or "")
                path_text = str(data.get("path") or "")
            else:
                match = re.search(r"/media/assistant/[^\s\"',}]+", raw)
                if match:
                    url = match.group(0)
            candidates: list[Path] = []
            if path_text:
                candidates.append(Path(path_text))
            if url.startswith("/media/assistant/"):
                candidates.append(
                    Path("data")
                    / "media"
                    / "assistant"
                    / url.split("/media/assistant/", 1)[1]
                )
            for candidate in candidates:
                path = candidate if candidate.is_absolute() else Path.cwd() / candidate
                if not path.is_file():
                    continue
                key = str(path.resolve())
                if key not in seen:
                    seen.add(key)
                    paths.append(path)
                break
        return paths

    @staticmethod
    def _strip_inline_images(text: str) -> str:
        value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", str(text or ""))
        value = re.sub(r"/media/assistant/[^\s\"',}]+", "", value)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    def _admin_progress_text(
        self, event: dict[str, Any], state: dict[str, Any]
    ) -> str:
        phase = str(event.get("phase") or "")
        if phase == "start":
            # Do not spend a full WeChat UI send on an acknowledgement. For a
            # normal chat this was adding 5-15 seconds before the model even
            # started. Tool/failure progress is still sent when it is useful.
            state["started"] = time.monotonic()
            return ""
        if phase == "queued":
            state["sent"] = int(state.get("sent") or 0) + 1
            state["last_sent"] = time.monotonic()
            return "收到你的新要求，已插入当前任务。"
        if phase == "tool":
            sent = int(state.get("sent") or 0)
            if sent >= int(state.get("max") or 6):
                return ""
            now = time.monotonic()
            if sent > 1 and now - float(state.get("last_sent") or 0.0) < 20.0:
                return ""
            state["sent"] = sent + 1
            state["last_sent"] = now
            tool = str(event.get("tool") or "工具")
            normalized = (
                tool.removeprefix("qunban_qunban_")
                .removeprefix("qunban_")
                .replace("qunban_", "")
            )
            label = _TOOL_LABELS.get(normalized, _TOOL_LABELS.get(tool, normalized))
            thought = str(event.get("thought") or "").strip().replace("\n", " ")
            if thought:
                return f"🔧 {label}：{thought[:140]}"
            return f"🔧 正在{label}"
        if phase == "tool_result" and event.get("ok") is False:
            sent = int(state.get("sent") or 0)
            if sent >= int(state.get("max") or 6) + 3:
                return ""
            state["sent"] = sent + 1
            state["last_sent"] = time.monotonic()
            tool = str(event.get("tool") or "工具")
            normalized = (
                tool.removeprefix("qunban_qunban_")
                .removeprefix("qunban_")
                .replace("qunban_", "")
            )
            label = _TOOL_LABELS.get(normalized, _TOOL_LABELS.get(tool, normalized))
            message = str(event.get("message") or "").strip().replace("\n", " ")
            if message:
                return f"⚠ {label}失败：{message[:180]}"
            return f"⚠ {label}失败"
        return ""

    async def _send_admin_notice(
        self, contact_id: int, text: str, *, allow_seconds: float = 60.0
    ) -> None:
        text = str(text or "").strip()
        if not text:
            return
        for attempt in range(2):
            try:
                await self.send_contact_message(
                    contact_id,
                    text,
                    is_agent=True,
                    allow_seconds=allow_seconds,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if getattr(self, "detached", False):
                    logger.warning("发送管理员通知暂停：用户已重新接管微信")
                    return
                if attempt > 0:
                    logger.warning("发送管理员通知仍失败: %s", exc)
                    return
                logger.warning("发送管理员通知失败，尝试恢复微信窗口: %s", exc)
                recover = getattr(self.adapter, "recover_window", None)
                if callable(recover):
                    try:
                        await asyncio.to_thread(recover)
                    except Exception:
                        pass
                await asyncio.sleep(0.8)

    def _schedule_admin_reply(self, contact_id: int, wxid: str, text: str) -> None:
        session_id = f"admin:{wxid}"
        clean = str(text or "").strip()
        if not clean:
            return

        active = is_session_active(session_id)
        previous = self._admin_tasks.get(wxid)
        starting = bool(previous and not previous.done())
        normalized = re.sub(r"[\s，。！？,.!?~～]+", "", clean).lower()
        is_cancel = normalized in _CANCEL_WORDS or (
            (active or starting)
            and normalized.startswith(("停止", "取消", "打断", "中断"))
        )
        if is_cancel:
            if active and cancel_session(session_id):
                asyncio.create_task(
                    self._send_admin_notice(
                        contact_id, "收到，当前工具返回后就会停止。"
                    ),
                    name=f"admin-stop-{wxid}",
                )
                return
            if starting and previous is not None:
                previous.cancel()
                take_session_inbox(session_id)
                asyncio.create_task(
                    self._send_admin_notice(contact_id, "已停止当前任务。"),
                    name=f"admin-stop-{wxid}",
                )
                return
            asyncio.create_task(
                self._send_admin_notice(
                    contact_id, "当前没有正在执行的任务。"
                ),
                name=f"admin-idle-{wxid}",
            )
            return

        if starting:
            # Do not cancel the running task. Queue this message and let the
            # running loop process it as an inserted requirement.
            enqueue_session_message(session_id, clean)
            return

        # The previous task may have ended between our check and its cleanup.
        # Merge any message that arrived in that tiny window.
        self._admin_tasks.pop(wxid, None)
        pending = take_session_inbox(session_id)
        if pending:
            clean = "\n".join([*pending, clean])

        async def run() -> None:
            progress_queue: asyncio.Queue[str] = asyncio.Queue()
            progress_worker: asyncio.Task | None = None
            try:
                await asyncio.sleep(0.2)
                if self.detached:
                    logger.warning(
                        "管理员消息已收到，但检测到用户重新接管微信，"
                        "已暂停自动回复，等待重新接管"
                    )
                    return
                if not self.is_online:
                    return
                temperature: float | None = None
                persona = ""
                contact_name = wxid
                async with SessionLocal() as db:
                    contact = await db.get(Contact, contact_id)
                    if contact is not None:
                        temperature = float(
                            contact.temperature
                            if contact.temperature is not None
                            else 0.3
                        )
                        persona = contact.persona or ""
                        contact_name = contact.name or contact.remark or wxid
                service = AssistantService(self.llm, self)
                progress_state: dict[str, Any] = {
                    "sent": 0,
                    "last_sent": 0.0,
                    "max": 6,
                }

                async def progress_worker_loop() -> None:
                    while True:
                        message = await progress_queue.get()
                        try:
                            await self._send_admin_notice(
                                contact_id, message, allow_seconds=8.0
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.warning("发送管理员进度失败: %s", exc)
                        finally:
                            progress_queue.task_done()

                progress_worker = asyncio.create_task(
                    progress_worker_loop(), name=f"admin-progress-{wxid}"
                )

                async def emit_progress(event: dict[str, Any]) -> None:
                    message = self._admin_progress_text(event, progress_state)
                    if message:
                        progress_queue.put_nowait(message)

                result = await service.chat(
                    clean,
                    session_id=session_id,
                    scope="admin",
                    temperature=temperature,
                    persona=persona,
                    channel="wechat",
                    progress=emit_progress,
                    memory_scope="contact",
                    memory_metadata={
                        "contact_id": str(contact_id),
                        "wxid": wxid,
                        "name": contact_name,
                    },
                )
                if result.get("queued"):
                    return
                reply = str(result.get("reply") or "").strip()
                if not reply:
                    return
                # Always wait for in-flight progress work. Checking empty() is
                # racy: the worker may have dequeued the last item but not
                # finished sending it yet, and the final reply would overtake
                # or cancel that message.
                await progress_queue.join()
                image_paths = RuntimeManager._image_paths_from_trace(
                    result.get("trace") or []
                )
                sent_images = 0
                for path in image_paths:
                    try:
                        await self.send_contact_image(wxid, path)
                        sent_images += 1
                    except Exception as exc:
                        logger.warning("发送联系人图片失败: %s", exc)
                text_reply = RuntimeManager._strip_inline_images(reply)
                if text_reply:
                    await self._send_admin_notice(contact_id, text_reply)
                elif sent_images == 0:
                    # If the image could not be sent, keep the original reply
                    # so the user still gets the link instead of silence.
                    await self._send_admin_notice(contact_id, reply)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("管理员 Agent 回复失败: %s", exc)
                await self._send_admin_notice(
                    contact_id, f"Agent 执行失败：{exc}"
                )
            finally:
                if progress_worker is not None:
                    progress_worker.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await progress_worker

        task = asyncio.create_task(run(), name=f"admin-chat-{wxid}")
        self._admin_tasks[wxid] = task

        def done(finished: asyncio.Task) -> None:
            if self._admin_tasks.get(wxid) is finished:
                self._admin_tasks.pop(wxid, None)
            if self.detached or not self.is_online:
                return
            leftover = take_session_inbox(session_id)
            if leftover:
                self._schedule_admin_reply(
                    contact_id, wxid, "\n".join(leftover)
                )

        task.add_done_callback(done)

    async def send_contact_image(self, wxid: str, path: Path) -> None:
        if not self.is_online or self.adapter is None:
            raise RuntimeError("服务未在线")
        self._ensure_attached()
        allow = getattr(self.adapter, "allow_ui_action", None)
        if callable(allow):
            allow(120.0)
        await self.adapter.send_image(wxid, path)

    async def send_contact_message(
        self,
        contact_id: int,
        text: str,
        *,
        is_agent: bool = False,
        allow_seconds: float = 60.0,
    ) -> dict[str, Any]:
        if not self.is_online or self.adapter is None:
            raise RuntimeError("服务未在线")
        self._ensure_attached()
        async with SessionLocal() as db:
            contact = await db.get(Contact, contact_id)
            if contact is None:
                raise RuntimeError("联系人不存在")
            if contact.account_key and self.account_key and contact.account_key != self.account_key:
                raise RuntimeError("该联系人属于其他微信账号")
            wxid = contact.wxid
            name = contact.name or contact.remark or wxid
        allow = getattr(self.adapter, "allow_ui_action", None)
        if callable(allow):
            allow(max(1.0, float(allow_seconds)))
        await self.adapter.send_text(wxid, text)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with SessionLocal() as db:
            contact = await db.get(Contact, contact_id)
            row = DirectMessage(
                external_id=None,
                contact_id=contact_id,
                account_key=self.account_key,
                sender_wxid=self.account_key,
                sender_name=self.bot_name,
                is_self=True,
                kind="text",
                content=text,
                meta={
                    "source": "admin-agent" if is_agent else "dashboard",
                    "_direct_pending": True,
                },
                created_at=now,
            )
            db.add(row)
            if contact is not None:
                contact.last_message_at = now
            await db.commit()
            await db.refresh(row)
            payload = direct_message_dict(row)
        self.broadcast("direct.message", payload)
        return payload

    def _schedule_proactive(self, group_id: int, minutes: int = 90) -> None:
        previous = self._proactive_tasks.pop(group_id, None)
        if previous and not previous.done():
            previous.cancel()
        delay = max(60.0, min(86400.0, float(minutes or 90) * 60.0))
        # A small jitter avoids every group waking at exactly the same time.
        delay += random.uniform(0.0, min(120.0, delay * 0.05))
        task = asyncio.create_task(
            self._run_proactive_later(group_id, delay),
            name=f"proactive-{group_id}",
        )
        self._proactive_tasks[group_id] = task
        task.add_done_callback(
            lambda _task: self._proactive_tasks.pop(group_id, None)
        )

    async def _run_proactive_later(self, group_id: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        try:
            if self.detached or not self.is_online:
                return
            async with SessionLocal() as db:
                config = await db.get(GroupConfig, group_id)
                settings = await get_settings(db)
            if (
                config is None
                or not config.read_enabled
                or not config.agent_enabled
                or not config.proactive_enabled
            ):
                return
            if AgentEngine._in_quiet_hours(
                config, str(settings.get("timezone") or "Asia/Shanghai")
            ):
                return
            await self.agent.proactive_message(group_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("主动消息失败: %s", exc)

    def _schedule_memory_summary(self, group_id: int, delay: float = 45.0) -> None:
        if group_id in self._memory_tasks and not self._memory_tasks[group_id].done():
            return
        task = asyncio.create_task(
            self._run_memory_summary_later(group_id, delay),
            name=f"memory-summary-{group_id}",
        )
        self._memory_tasks[group_id] = task
        task.add_done_callback(lambda _task: self._memory_tasks.pop(group_id, None))

    async def _run_memory_summary_later(self, group_id: int, delay: float) -> None:
        try:
            await asyncio.sleep(max(1.0, delay))
            async with SessionLocal() as db:
                settings = await get_settings(db)
                config = await db.get(GroupConfig, group_id)
                if (
                    config is None
                    or not config.read_enabled
                    or not settings.get("memory_enabled", True)
                    or not settings.get("llm_api_key")
                ):
                    return
            result = await summarize_group_memories(self.llm, group_id, settings)
            if result.get("count"):
                await prune_memories(group_id, keep=200)
                self.broadcast(
                    "memory.updated",
                    {"group_id": group_id, "count": result.get("count", 0)},
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("群聊记忆总结失败")

    async def _memory_scheduler(self) -> None:
        """Periodically summarize groups that have accumulated new messages."""
        while True:
            try:
                await asyncio.sleep(600)
                async with SessionLocal() as db:
                    settings = await get_settings(db)
                    if (
                        not settings.get("memory_enabled", True)
                        or not settings.get("llm_api_key")
                    ):
                        continue
                    statement = (
                        select(Group.id)
                        .join(GroupConfig, GroupConfig.group_id == Group.id)
                        .where(GroupConfig.read_enabled.is_(True))
                    )
                    if self.account_key:
                        statement = statement.where(
                            Group.account_key == self.account_key
                        )
                    group_ids = [int(row[0]) for row in (await db.execute(statement)).all()]
                for group_id in group_ids:
                    result = await summarize_group_memories(
                        self.llm, group_id, settings
                    )
                    if result.get("count"):
                        await prune_memories(group_id, keep=200)
                        self.broadcast(
                            "memory.updated",
                            {
                                "group_id": group_id,
                                "count": result.get("count", 0),
                            },
                        )
                    await asyncio.sleep(2)
                try:
                    from app.services import mem0_memory

                    await mem0_memory.maybe_forget(
                        settings, account_key=self.account_key
                    )
                    await maybe_forget_sqlite(
                        settings, account_key=self.account_key
                    )
                except Exception:
                    logger.warning("记忆遗忘清理失败", exc_info=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("记忆调度器异常")

    def _schedule_agent(self, group_id: int, message_id: int) -> None:
        if not self.is_online:
            return
        task = asyncio.create_task(
            self.agent.handle_message(group_id, message_id),
            name=f"agent-{group_id}-{message_id}",
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _cancel_agent_tasks(self) -> None:
        for task in list(self._bg_tasks):
            if not task.done():
                task.cancel()
        self._bg_tasks.clear()
        for task in list(self._admin_tasks.values()):
            if not task.done():
                task.cancel()
        self._admin_tasks.clear()
        for task in list(self._proactive_tasks.values()):
            if not task.done():
                task.cancel()
        self._proactive_tasks.clear()

    # --------------------------------------------------------------- messaging
    @property
    def is_online(self) -> bool:
        return self.state == "online" and self.adapter is not None

    @property
    def bot_name(self) -> str:
        return (self.account or {}).get("name") or "小助手"

    @property
    def account_key(self) -> str:
        """Stable key of the currently active WeChat account."""
        return str((self.account or {}).get("wxid") or "")

    async def send_agent_message(
        self,
        group_id: int,
        *,
        text: str = "",
        sticker: Sticker | None = None,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        if not self.is_online or self.adapter is None:
            raise RuntimeError("微信账号未登录")
        group = await self._get_group(group_id)
        account = self.account or {}
        sent_any = False
        meta = meta or {}
        image_paths = RuntimeManager._image_paths_from_trace(
            meta.get("tool_trace") or []
        )
        if image_paths:
            text = RuntimeManager._strip_inline_images(text)

        if text.strip():
            try:
                self._ensure_attached()
                await self.adapter.send_text(group.external_id, text.strip())
            except Exception as exc:
                await self._record_failed_send(
                    group,
                    kind="text",
                    content=text.strip(),
                    is_from_agent=True,
                    meta=meta,
                    account=account,
                    error=str(exc),
                )
                raise
            await self._persist_outgoing(
                group,
                kind="text",
                content=text.strip(),
                is_from_agent=True,
                meta=meta,
                account=account,
            )
            sent_any = True

        for path in image_paths:
            if sent_any:
                await asyncio.sleep(0.45)
            allow = getattr(self.adapter, "allow_ui_action", None)
            if callable(allow):
                allow(120.0)
            image_meta = {**meta, "source": "agent-image"}
            try:
                self._ensure_attached()
                await self.adapter.send_image(group.external_id, path)
            except Exception as exc:
                await self._record_failed_send(
                    group,
                    kind="image",
                    content="[图片]",
                    is_from_agent=True,
                    meta=image_meta,
                    account=account,
                    media_path=str(path),
                    error=str(exc),
                )
                raise
            await self._persist_outgoing(
                group,
                kind="image",
                content="[图片]",
                is_from_agent=True,
                meta=image_meta,
                account=account,
            )
            sent_any = True

        if sticker is not None:
            if sent_any:
                await asyncio.sleep(0.65)
            from app.services.stickers import sticker_path

            path = await sticker_path(sticker)
            sticker_meta = {**meta, "sticker_id": sticker.id}
            try:
                self._ensure_attached()
                await self.adapter.send_sticker(group.external_id, path)
            except Exception as exc:
                await self._record_failed_send(
                    group,
                    kind="sticker",
                    content=sticker.name or "[表情包]",
                    is_from_agent=True,
                    meta=sticker_meta,
                    account=account,
                    sticker=sticker,
                    media_path=str(path),
                    error=str(exc),
                )
                raise
            await self._persist_outgoing(
                group,
                kind="sticker",
                content=sticker.name or "[表情包]",
                is_from_agent=True,
                meta=sticker_meta,
                sticker=sticker,
                account=account,
            )
            sent_any = True

        return sent_any

    async def send_manual_message(
        self,
        group_id: int,
        *,
        text: str = "",
        sticker: Sticker | None = None,
    ) -> list[dict[str, Any]]:
        if not self.is_online or self.adapter is None:
            raise RuntimeError("微信账号未登录")
        group = await self._get_group(group_id)
        account = self.account or {}
        results: list[dict[str, Any]] = []
        allow = getattr(self.adapter, "allow_ui_action", None)
        if callable(allow):
            # A manual send is an explicit user request. Long OCR/UIA opens can
            # take tens of seconds; the guard still aborts if the cursor is
            # actually moved while automation is moving it.
            allow(120.0)
        if text.strip():
            try:
                self._ensure_attached()
                await self.adapter.send_text(group.external_id, text.strip())
            except Exception as exc:
                await self._record_failed_send(
                    group,
                    kind="text",
                    content=text.strip(),
                    is_from_agent=False,
                    meta={"source": "dashboard"},
                    account=account,
                    error=str(exc),
                )
                raise
            payload = await self._persist_outgoing(
                group,
                kind="text",
                content=text.strip(),
                is_from_agent=False,
                meta={"source": "dashboard"},
                account=account,
            )
            results.append(payload)
        if sticker is not None:
            if results:
                await asyncio.sleep(0.4)
            from app.services.stickers import sticker_path

            path = await sticker_path(sticker)
            sticker_meta = {"source": "dashboard", "sticker_id": sticker.id}
            try:
                self._ensure_attached()
                await self.adapter.send_sticker(group.external_id, path)
            except Exception as exc:
                await self._record_failed_send(
                    group,
                    kind="sticker",
                    content=sticker.name or "[表情包]",
                    is_from_agent=False,
                    meta=sticker_meta,
                    sticker=sticker,
                    media_path=str(path),
                    account=account,
                    error=str(exc),
                )
                raise
            payload = await self._persist_outgoing(
                group,
                kind="sticker",
                content=sticker.name or "[表情包]",
                is_from_agent=False,
                meta=sticker_meta,
                sticker=sticker,
                account=account,
            )
            results.append(payload)
        return results

    @staticmethod
    def _outgoing_matches(row: Message, kind: str, content: str) -> bool:
        """Whether a database message is the echo of an optimistic send."""
        row_kind = str(row.kind or "text")
        kind = str(kind or "text")
        row_content = str(row.content or "").strip()
        content = str(content or "").strip()
        if row_kind == kind and row_content == content:
            return True
        # WeChat stores an image sent by the sticker picker as an image row;
        # the dashboard keeps the friendlier "sticker" kind.
        if {row_kind, kind} <= {"image", "sticker"}:
            created = row.created_at
            if created is not None:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                age = abs((now - created).total_seconds())
                if age <= 120:
                    return True
        return False

    async def _persist_outgoing(
        self,
        group: Group,
        *,
        kind: str,
        content: str,
        is_from_agent: bool,
        meta: dict[str, Any],
        account: dict[str, str],
        sticker: Sticker | None = None,
    ) -> dict[str, Any]:
        async with SessionLocal() as db:
            db_group = await db.get(Group, group.id)
            if db_group is None:
                raise RuntimeError("群聊不存在")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            record_meta = dict(meta or {})
            if self.adapter_id == "replica":
                # The 4.x listener reads the message back from the local
                # database a moment after sending. If that row already landed,
                # reuse it instead of creating a second dashboard copy.
                recent = (
                    await db.execute(
                        select(Message)
                        .where(
                            Message.group_id == group.id,
                            Message.is_self.is_(True),
                            Message.external_id.is_not(None),
                            Message.created_at >= now - timedelta(seconds=120),
                        )
                        .order_by(Message.id.desc())
                        .limit(20)
                    )
                ).scalars().all()
                for row in recent:
                    if self._outgoing_matches(row, kind, content):
                        return message_dict(row)
                # Otherwise mark this optimistic record so the listener can
                # consume it when the same message appears in the database.
                record_meta["_replica_pending"] = True
                record_meta["_replica_outgoing"] = True
            message = Message(
                external_id=None,
                group_id=group.id,
                sender_wxid=account.get("wxid", ""),
                sender_name=account.get("name") or self.bot_name,
                is_from_agent=is_from_agent,
                is_self=True,
                kind=kind,
                content=content,
                media_path=sticker.filename if sticker else "",
                meta=record_meta,
                created_at=now,
            )
            db.add(message)
            db_group.last_message_at = now
            if sticker is not None:
                db_sticker = await db.get(Sticker, sticker.id)
                if db_sticker is not None:
                    db_sticker.usage_count = (db_sticker.usage_count or 0) + 1
            await db.flush()
            await db.refresh(message)
            payload = message_dict(message)
            await db.commit()
        self.broadcast("message.new", payload)
        return payload

    async def _get_group(self, group_id: int) -> Group:
        async with SessionLocal() as db:
            group = await db.get(Group, group_id)
            if group is None:
                raise RuntimeError("群聊不存在")
            if (
                group.account_key
                and self.account_key
                and group.account_key != self.account_key
            ):
                raise RuntimeError("该群聊属于其他微信账号，请切换账号后再试")
            return group

    # ---------------------------------------------------------------- members
    async def refresh_groups(self) -> list[dict[str, Any]]:
        if not self.is_online or self.adapter is None:
            raise RuntimeError("微信账号未登录")
        self._ensure_attached()
        allow = getattr(self.adapter, "allow_ui_action", None)
        if callable(allow):
            allow(25.0)
        groups = await self.adapter.list_groups()
        if groups:
            await self._handle_groups(groups)
        return await self.list_group_payloads()

    async def sync_members(self, group_id: int) -> list[GroupMember]:
        self._ensure_attached()
        allow = getattr(self.adapter, "allow_ui_action", None)
        if callable(allow):
            allow(20.0)
        async with SessionLocal() as db:
            group = await db.get(Group, group_id)
            if group is None:
                raise RuntimeError("群聊不存在")
            if (
                group.account_key
                and self.account_key
                and group.account_key != self.account_key
            ):
                raise RuntimeError("该群聊属于其他微信账号，请切换到对应账号后再同步")
            if not group.account_key and self.account_key:
                group.account_key = self.account_key
            external_id = group.external_id
            config = await db.get(GroupConfig, group_id)
            include_history = bool(config.read_enabled) if config else False
        members = []
        if self.is_online and self.adapter is not None:
            try:
                members = await self.adapter.get_members(
                    external_id, include_history=include_history
                )
            except Exception as exc:
                logger.warning("同步群成员失败: %s", exc)
        if members:
            async with SessionLocal() as db:
                group = await db.get(Group, group_id)
                if group is None:
                    return []
                for info in members:
                    nickname = info.nickname or info.wxid
                    await db.execute(
                        sqlite_insert(GroupMember)
                        .values(
                            group_id=group_id,
                            wxid=info.wxid,
                            nickname=nickname,
                            alias=info.alias or "",
                            is_owner=info.is_owner,
                            is_admin=info.is_admin,
                            updated_at=utcnow(),
                        )
                        .on_conflict_do_update(
                            index_elements=["group_id", "wxid"],
                            set_={
                                "nickname": nickname,
                                "alias": info.alias or "",
                                "is_owner": info.is_owner,
                                "is_admin": info.is_admin,
                                "updated_at": utcnow(),
                            },
                        )
                    )
                group.member_count = len(members)
                await db.commit()
            self.broadcast(
                "members.updated",
                {"group_id": group_id, "count": len(members)},
            )
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(GroupMember)
                    .where(GroupMember.group_id == group_id)
                    .order_by(GroupMember.is_owner.desc(), GroupMember.nickname)
                )
            ).scalars().all()
            return list(rows)

    # -------------------------------------------------------------------- qr
    def get_qr(self) -> tuple[bytes, str, int] | None:
        if not self.qr_bytes:
            return None
        return self.qr_bytes, self.qr_mime, self.qr_version

    def emit_typing(self, group_id: int) -> None:
        self.broadcast("agent.typing", {"group_id": group_id})

    # ---------------------------------------------------------------- status
    def status_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "adapter": self.adapter_id or None,
            "account": self.account,
            "has_qr": bool(self.qr_bytes),
            "qr_version": self.qr_version,
            "last_error": self.last_error,
            "message": self.status_message,
            "online_seconds": int(time.time() - self.online_since)
            if self.online_since
            else 0,
            "service_seconds": int(time.time() - self.service_started_at),
            "bot_name": self.bot_name,
            "available_adapters": adapter_catalog(),
            "ws_clients": self.bus.client_count,
            "pinned_group_id": self.pinned_group_id,
            "detached": self.detached,
            "detach_reason": self.detach_reason,
            "takeover_hotkey": self._hotkey_text,
            "takeover_hotkey_active": self._hotkey.active,
            "takeover_hotkey_error": self._hotkey.error,
            "version": settings.version,
        }

    def _ensure_attached(self) -> None:
        if self.detached:
            raise RuntimeError(
                self.detach_reason
                + "，自动操作已暂停，请点击「重新接管」"
            )

    async def reattach(self) -> dict[str, Any]:
        adapter = self.adapter
        if adapter is not None:
            reattach = getattr(adapter, "reattach", None)
            if callable(reattach):
                reattach()
        self.detached = False
        self.detach_reason = ""
        self.detach_at = 0.0
        self.status_message = "已重新接管，正在校验并重发未发送消息"
        self.broadcast("adapter.reattached", {})
        self.broadcast("status", self.status_dict())
        retry_scheduled = False
        try:
            async with SessionLocal() as db:
                data = await get_settings(db)
            if bool(data.get("auto_retry_failed_sends", True)):
                retry_scheduled = self._schedule_outbox_retry(0.4)
        except Exception:
            retry_scheduled = self._schedule_outbox_retry(0.4)
        return {"detached": False, "outbox_retry_scheduled": retry_scheduled}

    async def detach(self, reason: str = "用户接管鼠标") -> dict[str, Any]:
        adapter = self.adapter
        if adapter is not None:
            detach = getattr(adapter, "detach", None)
            if callable(detach):
                detach(reason)
        self.detached = True
        self.detach_reason = reason
        self.detach_at = time.time()
        self.status_message = (
            f"{reason}，自动操作已暂停，请点击重新接管"
        )
        self.broadcast(
            "adapter.detached",
            {"reason": reason, "at": self.detach_at},
        )
        self.broadcast("status", self.status_dict())
        return {"detached": True, "reason": reason}

    # ------------------------------------------------------------ send outbox
    def _schedule_outbox_retry(self, delay: float = 0.0) -> bool:
        if self._outbox_task and not self._outbox_task.done():
            return False
        if not self.is_online or self.detached:
            return False
        self._outbox_task = asyncio.create_task(
            self._run_outbox_retry(delay), name="outbox-retry"
        )
        return True

    async def _run_outbox_retry(self, delay: float = 0.0) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            if not self.is_online or self.detached:
                return
            result = await self.retry_failed_sends()
            if result.get("retried") or result.get("failed"):
                logger.info(
                    "未发送消息校验完成：成功 %s，仍失败 %s，已确认重复 %s",
                    result.get("retried", 0),
                    result.get("failed", 0),
                    result.get("duplicates", 0),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("重发未发送消息失败")
        finally:
            self._outbox_task = None

    async def list_failed_sends(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._pending_outbox_rows(limit=limit)
        return [message_dict(row) for row in rows]

    async def _pending_outbox_rows(
        self, limit: int = 50, message_id: int | None = None
    ) -> list[Message]:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(Message)
                    .where(
                        Message.is_self.is_(True),
                        Message.created_at >= cutoff,
                    )
                    .order_by(Message.id.asc())
                    .limit(3000)
                )
            ).scalars().all()
        result: list[Message] = []
        for row in rows:
            meta = row.meta or {}
            if not meta.get("_outbox"):
                continue
            if meta.get("_send_state") not in {"failed", "pending"}:
                continue
            if message_id is not None and int(row.id) != int(message_id):
                continue
            result.append(row)
            if len(result) >= max(1, int(limit)):
                break
        return result

    async def _record_failed_send(
        self,
        group: Group,
        *,
        kind: str,
        content: str,
        is_from_agent: bool,
        meta: dict[str, Any],
        account: dict[str, str],
        sticker: Sticker | None = None,
        media_path: str = "",
        error: str = "",
    ) -> dict[str, Any] | None:
        async with SessionLocal() as db:
            db_group = await db.get(Group, group.id)
            if db_group is None:
                return None
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            record_meta = dict(meta or {})
            record_meta.update(
                {
                    "_outbox": True,
                    "_send_state": "failed",
                    "_send_error": str(error or "")[:600],
                    "_outbox_created_at": now.isoformat(),
                }
            )
            if media_path:
                record_meta["_outbox_media_path"] = str(media_path)
            if sticker is not None:
                record_meta["sticker_id"] = sticker.id
            row = Message(
                external_id=None,
                group_id=group.id,
                sender_wxid=account.get("wxid", ""),
                sender_name=account.get("name") or self.bot_name,
                is_from_agent=is_from_agent,
                is_self=True,
                kind=kind,
                content=content,
                media_path=sticker.filename if sticker else "",
                meta=record_meta,
                created_at=now,
            )
            db.add(row)
            db_group.last_message_at = now
            await db.flush()
            await db.refresh(row)
            payload = message_dict(row)
            await db.commit()
        self.broadcast("message.new", payload)
        self.log_agent_event(
            "warn", f"消息未发送成功，已加入待重发：{str(error or '')[:160]}"
        )
        return payload

    async def _mark_outbox_pending(self, message_id: int) -> None:
        async with SessionLocal() as db:
            row = await db.get(Message, message_id)
            if row is None:
                return
            meta = dict(row.meta or {})
            meta["_send_state"] = "pending"
            meta["_send_error"] = ""
            if self.adapter_id == "replica":
                # Must be set before the UI send starts.  The database
                # listener can otherwise see the echo first and insert a
                # second row before we mark this one as awaiting confirmation.
                meta["_replica_pending"] = True
            row.meta = meta
            await db.commit()
            payload = message_dict(row)
        self.broadcast("message.updated", payload)

    async def _mark_outbox_sent(
        self, message_id: int, duplicate_of: int | None = None
    ) -> None:
        async with SessionLocal() as db:
            row = await db.get(Message, message_id)
            if row is None:
                return
            group_id = row.group_id
            if duplicate_of is not None:
                await db.delete(row)
                await db.commit()
                self.broadcast("message.deleted", {"id": message_id, "group_id": group_id})
                return
            meta = dict(row.meta or {})
            meta["_send_state"] = "sent"
            meta["_send_error"] = ""
            meta["_sent_verified_at"] = datetime.now(timezone.utc).replace(
                tzinfo=None
            ).isoformat()
            if meta.get("_replica_confirmed"):
                meta["_replica_pending"] = False
            elif self.adapter_id == "replica":
                meta["_replica_pending"] = True
            row.meta = meta
            await db.commit()
            payload = message_dict(row)
        self.broadcast("message.updated", payload)

    async def _mark_outbox_error(self, message_id: int, error: str) -> None:
        async with SessionLocal() as db:
            row = await db.get(Message, message_id)
            if row is None:
                return
            meta = dict(row.meta or {})
            meta["_send_state"] = "failed"
            meta["_send_error"] = str(error or "")[:600]
            meta["_replica_pending"] = False
            row.meta = meta
            await db.commit()
            payload = message_dict(row)
        self.broadcast("message.updated", payload)

    async def _find_confirmed_echo(self, row: Message) -> Message | None:
        created = row.created_at
        if created is None:
            return None
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(Message)
                    .where(
                        Message.group_id == row.group_id,
                        Message.id != row.id,
                        Message.is_self.is_(True),
                        Message.external_id.is_not(None),
                        Message.created_at >= created - timedelta(seconds=8),
                        Message.created_at <= created + timedelta(minutes=5),
                    )
                    .order_by(Message.id.desc())
                    .limit(40)
                )
            ).scalars().all()
        for other in rows:
            if other.created_at is not None:
                age = abs((other.created_at - created).total_seconds())
                if age > 300:
                    continue
            if self._outgoing_matches(other, row.kind, row.content):
                return other
        return None

    async def retry_failed_sends(
        self, message_id: int | None = None, limit: int = 20
    ) -> dict[str, Any]:
        if not self.is_online or self.adapter is None:
            return {"ok": False, "error": "微信账号未登录"}
        if self.detached:
            return {"ok": False, "error": "自动操作已暂停，请先重新接管"}
        if self._outbox_lock is None:
            self._outbox_lock = asyncio.Lock()
        async with self._outbox_lock:
            rows = await self._pending_outbox_rows(
                limit=limit, message_id=message_id
            )
            if not rows:
                return {
                    "ok": True,
                    "retried": 0,
                    "failed": 0,
                    "duplicates": 0,
                    "remaining": 0,
                }
            retried = 0
            failed = 0
            duplicates = 0
            remaining = 0
            for index, row in enumerate(rows):
                if self.detached or not self.is_online or self.adapter is None:
                    remaining += len(rows) - index
                    break
                group = await self._get_group(row.group_id)
                if row.external_id or (row.meta or {}).get("_replica_confirmed"):
                    await self._mark_outbox_sent(row.id)
                    duplicates += 1
                    continue
                echo = await self._find_confirmed_echo(row)
                if echo is not None:
                    # The previous attempt did land in WeChat even though the
                    # caller saw an exception.  Keep the real echo, drop the
                    # optimistic failed row.
                    await self._mark_outbox_sent(row.id, duplicate_of=echo.id)
                    duplicates += 1
                    continue
                try:
                    meta = row.meta or {}
                    await self._mark_outbox_pending(row.id)
                    if row.kind == "text":
                        await self.adapter.send_text(group.external_id, row.content)
                    elif row.kind == "image":
                        path = str(meta.get("_outbox_media_path") or "").strip()
                        if not path:
                            raise RuntimeError("待重发图片缺少本地文件路径")
                        await self.adapter.send_image(group.external_id, Path(path))
                    elif row.kind == "sticker":
                        sticker_id = meta.get("sticker_id")
                        db_sticker = None
                        if sticker_id is not None:
                            async with SessionLocal() as db:
                                db_sticker = await db.get(Sticker, int(sticker_id))
                        if db_sticker is None:
                            raise RuntimeError("待重发表情已不存在")
                        from app.services.stickers import sticker_path

                        path = await sticker_path(db_sticker)
                        await self.adapter.send_sticker(group.external_id, path)
                    else:
                        raise RuntimeError(f"不支持重发消息类型：{row.kind}")
                    echo = await self._find_confirmed_echo(row)
                    await self._mark_outbox_sent(
                        row.id, duplicate_of=echo.id if echo is not None else None
                    )
                    retried += 1
                    await asyncio.sleep(0.6)
                except AutomationDetached as exc:
                    await self._mark_outbox_error(row.id, str(exc))
                    remaining += len(rows) - index
                    break
                except Exception as exc:
                    await self._mark_outbox_error(row.id, str(exc))
                    failed += 1
            self.status_message = (
                f"未发送消息校验完成：重发 {retried} 条，仍失败 {failed} 条"
            )
            self.broadcast("status", self.status_dict())
            self.broadcast(
                "outbox.retry",
                {
                    "retried": retried,
                    "failed": failed,
                    "duplicates": duplicates,
                    "remaining": remaining,
                },
            )
            return {
                "ok": True,
                "retried": retried,
                "failed": failed,
                "duplicates": duplicates,
                "remaining": remaining,
            }

    def log_agent_event(self, level: str, message: str) -> None:
        self.broadcast("adapter.log", {"level": level, "message": message})
        if level in {"error", "warn", "warning"}:
            logger.warning(message)
        else:
            logger.info(message)

    def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        self.bus.publish(event_type, data)

    # ---------------------------------------------------------------- groups API
    async def list_group_payloads(self) -> list[dict[str, Any]]:
        async with SessionLocal() as db:
            statement = select(Group)
            if self.account_key:
                statement = statement.where(Group.account_key == self.account_key)
            groups = (
                await db.execute(
                    statement.order_by(
                        Group.last_message_at.desc().nullslast(), Group.id.desc()
                    )
                )
            ).scalars().all()
            groups = [group for group in groups if not _is_hidden_expired_group(group)]
            if not groups:
                return []
            last_subq = (
                select(
                    Message.group_id,
                    Message.id,
                    func.row_number()
                    .over(
                        partition_by=Message.group_id,
                        order_by=(Message.created_at.desc(), Message.id.desc()),
                    )
                    .label("rn"),
                )
                .subquery()
            )
            lasts = {
                row.group_id: row
                for row in (
                    await db.execute(
                        select(Message)
                        .join(last_subq, Message.id == last_subq.c.id)
                        .where(last_subq.c.rn == 1)
                    )
                ).scalars().all()
            }
            counts = dict(
                (
                    await db.execute(
                        select(Message.group_id, func.count(Message.id)).group_by(
                            Message.group_id
                        )
                    )
                ).all()
            )
            configs = {
                row.group_id: row
                for row in (await db.execute(select(GroupConfig))).scalars().all()
            }
            return [
                group_dict(
                    group,
                    configs.get(group.id),
                    lasts.get(group.id),
                    int(counts.get(group.id, 0)),
                )
                for group in groups
            ]
