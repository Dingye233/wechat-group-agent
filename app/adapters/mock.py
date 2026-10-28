from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import random
import time
import uuid
from pathlib import Path
from typing import Any

import qrcode

from app.adapters.base import (
    AccountInfo,
    BaseAdapter,
    GroupInfo,
    IncomingMessage,
    MemberInfo,
)

BOT_WXID = "wxid_agent_demo"
BOT_NAME = "小助手"

PRODUCT_GROUP = "room_demo_product@chatroom"
BADMINTON_GROUP = "room_demo_badminton@chatroom"
FAMILY_GROUP = "room_demo_family@chatroom"

MEMBERS: dict[str, list[tuple[str, str]]] = {
    PRODUCT_GROUP: [
        ("wxid_azhe", "阿哲"),
        ("wxid_xiaolu", "小鹿"),
        ("wxid_laowang", "老王"),
        ("wxid_taozi", "桃子"),
        ("wxid_agent_demo", BOT_NAME),
    ],
    BADMINTON_GROUP: [
        ("wxid_azhe", "阿哲"),
        ("wxid_xiaolu", "小鹿"),
        ("wxid_laowang", "老王"),
        ("wxid_agent_demo", BOT_NAME),
    ],
    FAMILY_GROUP: [
        ("wxid_laowang", "老王"),
        ("wxid_taozi", "桃子"),
        ("wxid_agent_demo", BOT_NAME),
    ],
}

GROUPS = [
    GroupInfo(
        external_id=PRODUCT_GROUP,
        name="产品摸鱼群",
        member_count=5,
        meta={"demo_agent_enabled": True, "trigger_mode": "mention"},
    ),
    GroupInfo(external_id=BADMINTON_GROUP, name="周末羽毛球", member_count=4),
    GroupInfo(external_id=FAMILY_GROUP, name="家人群", member_count=3),
]

CHATTER: list[tuple[str, str, str, bool]] = [
    (PRODUCT_GROUP, "wxid_azhe", "今天这个版本还会发吗？", False),
    (PRODUCT_GROUP, "wxid_xiaolu", "别急，让测试先过一轮吧 😂", False),
    (PRODUCT_GROUP, "wxid_laowang", "我刚看了下，登录那块好像还有个偶现问题", False),
    (PRODUCT_GROUP, "wxid_taozi", "中午吃什么？有人拼奶茶吗 🧋", False),
    (PRODUCT_GROUP, "wxid_xiaolu", "我我我，少冰三分糖", False),
    (PRODUCT_GROUP, "wxid_azhe", "@小助手 帮我把今天的待办整理一下", True),
    (PRODUCT_GROUP, "wxid_laowang", "这个新功能上线前记得同步一下运营", False),
    (BADMINTON_GROUP, "wxid_azhe", "周六下午两点，老地方？", False),
    (BADMINTON_GROUP, "wxid_xiaolu", "可以，我订个场地", False),
    (BADMINTON_GROUP, "wxid_laowang", "带我一个，拍子刚好换了线", False),
    (FAMILY_GROUP, "wxid_taozi", "晚上回家吃饭吗？", False),
    (FAMILY_GROUP, "wxid_laowang", "回，七点前到", False),
]


class MockAdapter(BaseAdapter):
    """Fully local adapter used for development, demos and UI tests."""

    adapter_id = "mock"
    display_name = "演示模式"
    description = "内置模拟群聊与成员，无需微信，适合体验功能和二次开发。"

    def __init__(self, emit, options: dict[str, Any] | None = None) -> None:
        super().__init__(emit, options)
        self._account = AccountInfo(wxid=BOT_WXID, name=BOT_NAME)
        self._login_task: asyncio.Task | None = None
        self._chatter_task: asyncio.Task | None = None
        self._online = False
        self._counter = 0
        self.sent: list[tuple[str, str, str]] = []

    @classmethod
    def availability(cls) -> tuple[bool, str]:
        return True, ""

    async def start(self) -> None:
        self._stop.clear()
        self._online = False
        self.emit("status", state="starting", message="正在准备演示环境…")
        payload = f"wechat-agent://mock-login/{uuid.uuid4().hex}"
        qr_bytes = await asyncio.to_thread(self._make_qr, payload)
        self.emit(
            "qr",
            image_b64=base64.b64encode(qr_bytes).decode("ascii"),
            mime="image/png",
        )
        self.emit("status", state="qr", message="演示模式二维码已生成，即将自动确认登录")
        self._login_task = asyncio.create_task(self._finish_login())

    async def _finish_login(self) -> None:
        try:
            await asyncio.sleep(4.5)
            if self._stop.is_set():
                return
            self._online = True
            self.emit("status", state="online", message="演示账号登录成功")
            self.emit(
                "account",
                wxid=self._account.wxid,
                name=self._account.name,
                avatar=self._account.avatar,
            )
            self.emit("groups", groups=GROUPS)
            await asyncio.sleep(0.4)
            await self._seed_messages()
            self._chatter_task = asyncio.create_task(self._chatter_loop())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self.emit("error", message=f"演示登录失败：{exc}")

    async def _seed_messages(self) -> None:
        seeds = [
            ("mock-seed-product-1", PRODUCT_GROUP, "wxid_azhe", "早上好，今天也要努力摸鱼", 620),
            ("mock-seed-product-2", PRODUCT_GROUP, "wxid_xiaolu", "新来的同学可以冒个泡～", 480),
            ("mock-seed-product-3", PRODUCT_GROUP, "wxid_laowang", "测试环境我重新发了一版", 300),
            ("mock-seed-badminton-1", BADMINTON_GROUP, "wxid_xiaolu", "场地订好了，周六见", 900),
            ("mock-seed-family-1", FAMILY_GROUP, "wxid_taozi", "记得买点水果回来", 1200),
        ]
        now = time.time()
        for external_id, room, wxid, text, age in seeds:
            name = self._name_of(room, wxid)
            self._emit_message(
                IncomingMessage(
                    room_id=room,
                    sender_id=wxid,
                    sender_name=name,
                    content=text,
                    timestamp=now - age,
                    external_id=external_id,
                )
            )
            await asyncio.sleep(0.12)

    async def _chatter_loop(self) -> None:
        # First organic message arrives quickly and mentions the bot, so the
        # agent flow can be observed right after login.
        await asyncio.sleep(8)
        if self._stop.is_set():
            return
        self._emit_message(
            IncomingMessage(
                room_id=PRODUCT_GROUP,
                sender_id="wxid_laowang",
                sender_name=self._name_of(PRODUCT_GROUP, "wxid_laowang"),
                content="@小助手 在吗？帮我把今天的待办记一下",
                timestamp=time.time(),
                external_id=f"mock-{uuid.uuid4().hex[:12]}",
            )
        )
        while not self._stop.is_set():
            try:
                self._counter += 1
                if self._counter % 5 == 0:
                    room, wxid, text, _ = random.choice(
                        [c for c in CHATTER if c[3] or c[0] == PRODUCT_GROUP]
                    )
                else:
                    pool = [c for c in CHATTER if c[0] == PRODUCT_GROUP and not c[3]]
                    room, wxid, text, _ = random.choice(pool)
                self._emit_message(
                    IncomingMessage(
                        room_id=room,
                        sender_id=wxid,
                        sender_name=self._name_of(room, wxid),
                        content=text,
                        timestamp=time.time(),
                        external_id=f"mock-{uuid.uuid4().hex[:12]}",
                    )
                )
                await asyncio.sleep(random.uniform(35, 65))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(5)

    def _emit_message(self, message: IncomingMessage) -> None:
        if self._stop.is_set():
            return
        data = {
            "room_id": message.room_id,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "content": message.content,
            "kind": message.kind,
            "timestamp": message.timestamp,
            "external_id": message.external_id,
            "is_self": message.is_self,
            "is_group": message.is_group,
            "meta": message.meta,
        }
        self.emit("message", message=data)

    async def stop(self) -> None:
        self._stop.set()
        self._online = False
        for task in (self._login_task, self._chatter_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._login_task = None
        self._chatter_task = None
        self.log("演示适配器已停止")

    async def send_text(self, room_id: str, text: str) -> None:
        if not self._online:
            raise RuntimeError("演示账号尚未登录")
        self.sent.append(("text", room_id, text))
        self.log(f"[演示发送文本] {self._short(text)}")

    async def send_sticker(self, room_id: str, path: Path) -> None:
        if not self._online:
            raise RuntimeError("演示账号尚未登录")
        self.sent.append(("sticker", room_id, str(path)))
        self.log(f"[演示发送表情] {path.name}")

    async def list_groups(self) -> list[GroupInfo]:
        return GROUPS

    async def get_members(
        self, room_id: str, include_history: bool = True
    ) -> list[MemberInfo]:
        return [
            MemberInfo(wxid=wxid, nickname=name)
            for wxid, name in MEMBERS.get(room_id, [])
        ]

    async def get_account(self) -> AccountInfo | None:
        return self._account if self._online else None

    def _name_of(self, room_id: str, wxid: str) -> str:
        for member_wxid, name in MEMBERS.get(room_id, []):
            if member_wxid == wxid:
                return name
        return wxid

    @staticmethod
    def _make_qr(payload: str) -> bytes:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=9,
            border=2,
        )
        qr.add_data(payload)
        qr.make(fit=True)
        image = qr.make_image(fill_color="#0b1f16", back_color="#ffffff")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _short(text: str) -> str:
        return text if len(text) <= 40 else text[:40] + "…"
