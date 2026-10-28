from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import io
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from queue import Empty
from typing import Any

from app.adapters.base import (
    AccountInfo,
    BaseAdapter,
    GroupInfo,
    IncomingMessage,
    MemberInfo,
)

SUPPORTED_WECHAT = "3.9.12.51（WeChatFerry v39.5.2 兼容版本）"
GROUP_SUFFIX = "@chatroom"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class WeChatFerryAdapter(BaseAdapter):
    """Windows WeChat PC adapter powered by WeChatFerry.

    The adapter runs the blocking SDK in a dedicated thread. QR login is
    implemented by screenshotting the official WeChat login window, so the
    user can scan the QR code directly inside the web dashboard.

    Requirements:
      * Windows
      * WeChat PC 3.9.12.51 (matching WeChatFerry v39.5.2)
      * ``pip install -r requirements-wcf.txt``
    """

    adapter_id = "wcf"
    display_name = "微信 PC（WeChatFerry）"
    description = "通过 Windows 微信 PC 3.9.x 客户端接入，支持扫码、收发消息与表情包。"

    def __init__(self, emit, options: dict[str, Any] | None = None) -> None:
        super().__init__(emit, options)
        self._thread: threading.Thread | None = None
        self._wcf: Any = None
        self._api_lock = threading.RLock()
        self._cache_lock = threading.Lock()
        self._member_cache: dict[str, tuple[float, dict[str, str]]] = {}
        self._group_names: dict[str, str] = {}
        self._account = AccountInfo(wxid="", name="微信助手")
        self._wechat_exe: Path | None = None
        self._last_launch = 0.0
        self._last_error = ""
        self._last_error_at = 0.0
        self._last_qr_hash = ""
        self._recent_outgoing: list[tuple[str, str, float]] = []
        self._recent_sticker_at: dict[str, float] = {}
        self._self_wxid = ""
        self._connected_once = False
        self._readable_rooms: set[str] = set()

    # ---------------------------------------------------------------- metadata
    @classmethod
    def availability(cls) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "仅支持 Windows"
        reasons: list[str] = []
        try:
            import wcferry  # noqa: F401
        except Exception:
            reasons.append("缺少 wcferry，请安装 requirements-wcf.txt")
        compatible = cls._find_compatible_wechat()
        weixin4 = cls._detect_weixin4()
        if weixin4 is not None and compatible is None:
            reasons.append(
                f"检测到微信 4.x（{weixin4}），WeChatFerry 仅支持微信 PC 3.9.x，请安装 3.9.12.51"
            )
        if compatible is not None:
            version = cls._wechat_version(compatible)
            if version and version != "3.9.12.51":
                reasons.append(
                    f"当前微信版本为 {version}，WeChatFerry v39.5.2 需要 3.9.12.51；"
                    "请改用「微信 PC（界面自动化）」适配器"
                )
        if reasons:
            return False, "；".join(reasons)
        return True, ""

    @staticmethod
    def _wechat_version(path: Path) -> str:
        try:
            import win32api

            info = win32api.GetFileVersionInfo(str(path), "\\")
            ms = int(info["FileVersionMS"])
            ls = int(info["FileVersionLS"])
            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        except Exception:
            return ""

    @classmethod
    def _find_compatible_wechat(cls) -> Path | None:
        for candidate in cls._default_wechat_paths():
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _default_wechat_paths() -> list[Path]:
        candidates = [
            # WeChatFerry's native DLLs are 32-bit; prefer the x86 client.
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Tencent"
            / "WeChat"
            / "WeChat.exe",
            Path(r"D:\WeChat3.9x86\WeChat.exe"),
            Path(r"D:\WeChat3.9\WeChat.exe"),
            Path(r"D:\WeChat\WeChat.exe"),
            Path(r"D:\Tencent\WeChat\WeChat.exe"),
            Path(r"D:\Program Files\Tencent\WeChat\WeChat.exe"),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Tencent"
            / "WeChat"
            / "WeChat.exe",
        ]
        # Some installers keep the executable in a versioned subdirectory.
        for root in (
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Tencent"
            / "WeChat",
            Path(r"D:\WeChat3.9x86"),
            Path(r"D:\WeChat3.9"),
            Path(r"D:\WeChat"),
            Path(r"D:\Tencent\WeChat"),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Tencent"
            / "WeChat",
        ):
            try:
                for child in root.glob("*/WeChat.exe"):
                    if child not in candidates:
                        candidates.insert(0, child)
            except Exception:
                pass
        try:
            import winreg

            locations = [
                (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\WeChat"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\WeChat"),
            ]
            for root, key in locations:
                try:
                    with winreg.OpenKey(root, key) as handle:
                        install, _ = winreg.QueryValueEx(handle, "InstallPath")
                        candidate = Path(str(install)) / "WeChat.exe"
                        if candidate not in candidates:
                            candidates.insert(0, candidate)
                except Exception:
                    continue
        except Exception:
            pass
        return candidates

    @staticmethod
    def _detect_weixin4() -> Path | None:
        """Find an installed Weixin 4.x client to give a useful diagnosis."""
        candidates: list[Path] = []
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_name)
            if base:
                candidates.extend(
                    [
                        Path(base) / "Tencent" / "Weixin" / "Weixin.exe",
                        Path(base) / "Tencent" / "WeChat" / "Weixin.exe",
                    ]
                )
        candidates.extend(
            [
                Path(r"D:\Weixin\Weixin.exe"),
                Path(r"D:\WeChat\Weixin.exe"),
                Path(r"C:\Weixin\Weixin.exe"),
            ]
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        ok, reason = self.availability()
        if not ok:
            self.emit("error", message=reason)
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_error = ""
        self.emit(
            "status",
            state="starting",
            message="正在启动微信 PC 客户端并准备扫码登录…",
        )
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="wechatferry-worker"
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        await asyncio.to_thread(self._stop_sync)
        self.emit("status", state="offline", message="已停止微信适配器")

    def _stop_sync(self) -> None:
        with self._api_lock:
            wcf = self._wcf
            self._wcf = None
        if wcf is not None:
            try:
                wcf.disable_receiving_msg()
            except Exception:
                pass
            cleanup = getattr(wcf, "cleanup", None)
            if callable(cleanup):
                try:
                    cleanup()
                except Exception:
                    pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=4)

    # ------------------------------------------------------------------ worker
    def _worker(self) -> None:
        self._setup_dpi()
        self._wechat_exe = self._find_wechat_exe()
        if not self._wechat_exe and not self._wechat_running():
            self._report_error(
                "未找到微信客户端。请安装微信 PC 3.9.12.51，或在设置中填写 WeChat.exe 完整路径。"
            )
            self.emit("status", state="error", message="未找到微信客户端")
            return

        if self._wechat_exe and not self._wechat_running():
            self._launch_wechat()

        while not self._stop.is_set():
            try:
                if self._connected_once and self._wcf is None:
                    # A successful WCF session disappeared. Do not retry the
                    # SDK in-process: its init can call os._exit on a stale
                    # spy/wcf.exe state. Keep the API service alive and let
                    # the user logout/relogin or restart WeChat.
                    self.emit(
                        "status",
                        state="error",
                        message="微信消息连接已断开，请退出登录后重新连接",
                    )
                    return

                if self._wcf is not None:
                    self._receive_loop()
                    continue

                if not self._wechat_running():
                    if self._wechat_exe and time.time() - self._last_launch > 12:
                        self._launch_wechat()
                    self.emit(
                        "status",
                        state="qr",
                        message="等待微信启动，请扫码登录…",
                    )
                    self._capture_qr()
                    self._stop.wait(2.5)
                    continue

                window_state = self._window_state()
                if window_state == "login":
                    # Never ask WCF to connect while the official login window
                    # is open: the SDK constructor can block until login.
                    self._capture_qr()
                    self.emit(
                        "status",
                        state="qr",
                        message="请用手机微信扫描二维码登录…",
                    )
                    self._stop.wait(2.0)
                    continue
                if window_state != "main":
                    # WeChat is starting or minimized. Keep trying to capture
                    # the login window without blocking on WCF.
                    self._capture_qr()
                    self.emit(
                        "status",
                        state="qr",
                        message="请保持微信登录窗口可见，或手动打开微信扫码…",
                    )
                    self._stop.wait(2.0)
                    continue

                if self._try_connect():
                    continue

                self._capture_qr()
                self._stop.wait(3.0)
            except Exception as exc:  # keep the worker alive
                self._report_error(f"适配器异常：{exc}")
                self._stop.wait(4.0)

    @staticmethod
    def _window_state() -> str:
        """Return login/main/none based on the official WeChat window class."""
        if sys.platform != "win32":
            return "none"
        try:
            import win32gui
        except Exception:
            return "none"

        state = {"login": False, "main": False}

        def _enum(hwnd: int, _extra: Any) -> None:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                cls = win32gui.GetClassName(hwnd)
                if cls == "WeChatLoginWndForPC":
                    state["login"] = True
                elif cls == "WeChatMainWndForPC":
                    state["main"] = True
            except Exception:
                return

        try:
            win32gui.EnumWindows(_enum, None)
        except Exception:
            return "none"
        if state["login"]:
            return "login"
        if state["main"]:
            return "main"
        return "none"

    def _try_connect(self) -> bool:
        try:
            from wcferry import Wcf
        except Exception:
            self._report_error("未安装 wcferry，请运行 pip install -r requirements-wcf.txt")
            return False

        wcf = None
        try:
            wcf = Wcf(debug=False, block=False)
            if not wcf.is_login():
                raise RuntimeError("微信尚未登录")
            info = wcf.get_user_info() or {}
            wxid = str(_get(info, "wxid", "") or "")
            if not wxid:
                raise RuntimeError("微信尚未登录")
            name = str(
                _get(info, "name", "")
                or _get(info, "nickname", "")
                or _get(info, "remark", "")
                or "微信助手"
            )
            self._self_wxid = wxid
            self._account = AccountInfo(
                wxid=wxid,
                name=name,
                avatar=str(_get(info, "avatar", "") or ""),
            )
            if not wcf.enable_receiving_msg():
                raise RuntimeError("无法开启消息接收，请确认微信版本与注入状态")

            with self._api_lock:
                self._wcf = wcf
            self._last_error = ""
            self._connected_once = True
            self.emit(
                "account",
                wxid=self._account.wxid,
                name=self._account.name,
                avatar=self._account.avatar,
            )
            self.emit("status", state="online", message="微信登录成功，正在同步群聊…")
            groups = self._fetch_groups(wcf)
            self.emit("groups", groups=groups)
            if not groups:
                threading.Thread(
                    target=self._delayed_group_refresh,
                    args=(wcf,),
                    daemon=True,
                    name="wcf-group-refresh",
                ).start()
            self.emit(
                "status",
                state="online",
                message="微信已连接，Agent 正在后台运行",
            )
            self.log(f"微信登录成功：{name}（{wxid}），发现 {len(groups)} 个群聊")
            return True
        except Exception as exc:
            if wcf is not None:
                try:
                    wcf.disable_receiving_msg()
                except Exception:
                    pass
                cleanup = getattr(wcf, "cleanup", None)
                if callable(cleanup):
                    try:
                        cleanup()
                    except Exception:
                        pass
            self._wcf = None
            msg = str(exc)
            self._report_error(msg)
            self.emit(
                "status",
                state="qr",
                message=self._login_hint(msg),
            )
            return False

    def _receive_loop(self) -> None:
        wcf = self._wcf
        if wcf is None:
            return
        try:
            while not self._stop.is_set() and self._wcf is wcf:
                try:
                    msg = self._next_message(wcf)
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    self._report_error(f"消息接收中断：{exc}")
                    self.emit(
                        "status",
                        state="error",
                        message="微信连接中断，正在自动重连…",
                    )
                    break
                if msg is None:
                    continue
                try:
                    self._handle_message(msg)
                except Exception as exc:
                    self.log(f"处理消息失败：{exc}", level="warn")
        finally:
            self._cleanup_connection()
            if not self._stop.is_set():
                self.emit(
                    "status",
                    state="qr",
                    message="连接已断开，请在微信中重新登录或稍候自动重连",
                )

    def _next_message(self, wcf: Any) -> Any:
        # wcferry's get_msg(block=True) uses Queue.get with a 1s timeout and
        # raises queue.Empty when there is no message. That is normal idle
        # behaviour, not a connection failure.
        try:
            return wcf.get_msg(block=True)
        except Empty:
            return None
        except TypeError:
            try:
                return wcf.get_msg()
            except Empty:
                return None

    def _cleanup_connection(self) -> None:
        with self._api_lock:
            wcf = self._wcf
            self._wcf = None
        if wcf is not None:
            try:
                wcf.disable_receiving_msg()
            except Exception:
                pass

    # ------------------------------------------------------------- message in
    def _handle_message(self, msg: Any) -> None:
        room_id = str(_get(msg, "roomid", "") or _get(msg, "room_id", "") or "")
        if not room_id or not room_id.endswith(GROUP_SUFFIX):
            return
        if room_id not in self._readable_rooms:
            return

        msg_type = int(_get(msg, "type", 0) or 0)
        content = str(_get(msg, "content", "") or "")
        sender = str(_get(msg, "sender", "") or "")
        is_self = bool(_get(msg, "is_self", False))

        # Older SDK versions embed "wxid:\n" in group content.
        match = re.match(r"^([A-Za-z0-9_\-]{4,}):\n", content[:160])
        if match:
            if not sender:
                sender = match.group(1)
            content = content[match.end() :]

        if is_self and self._is_outgoing_echo(room_id, content):
            return

        kind, text = self._describe(msg_type, content)
        if not text:
            return
        if is_self and kind in ("sticker", "image"):
            last_sticker = self._recent_sticker_at.get(room_id, 0)
            if time.time() - last_sticker < 15:
                return

        if is_self:
            sender = self._self_wxid or sender
            sender_name = self._account.name
        else:
            sender_name = self._member_name(room_id, sender)

        external_id = str(_get(msg, "id", "") or "") or f"wcf-{uuid.uuid4().hex}"
        timestamp = float(_get(msg, "ts", 0) or time.time())
        group_name = self._group_names.get(room_id, "")
        self.emit(
            "message",
            message={
                "room_id": room_id,
                "sender_id": sender,
                "sender_name": sender_name,
                "content": text,
                "kind": kind,
                "timestamp": timestamp,
                "external_id": external_id,
                "is_self": is_self,
                "is_group": True,
                "meta": {"msg_type": msg_type, "group_name": group_name},
            },
        )

    @staticmethod
    def _describe(msg_type: int, content: str) -> tuple[str, str]:
        if msg_type == 1:
            return "text", content
        if msg_type == 3:
            return "image", "[图片]"
        if msg_type == 34:
            return "voice", "[语音]"
        if msg_type == 43:
            return "video", "[视频]"
        if msg_type == 47:
            return "sticker", "[表情]"
        if msg_type == 49:
            title = ""
            m = re.search(r"<title>(.*?)</title>", content, re.S)
            if m:
                title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            return "app", f"[卡片] {title}" if title else "[卡片消息]"
        if msg_type in (10000, 10002):
            return "system", content or "[系统消息]"
        return "other", content or "[消息]"

    # ------------------------------------------------------------ message out
    async def send_text(self, room_id: str, text: str) -> None:
        await asyncio.to_thread(self._send_text_sync, room_id, text)

    def _send_text_sync(self, room_id: str, text: str) -> None:
        with self._api_lock:
            wcf = self._wcf
            if wcf is None:
                raise RuntimeError("微信尚未连接")
            ret = wcf.send_text(text, room_id)
            if isinstance(ret, int) and ret != 0:
                raise RuntimeError(f"微信发送失败，错误码 {ret}")
        self._remember_outgoing(room_id, text)

    async def send_sticker(self, room_id: str, path: Path) -> None:
        await asyncio.to_thread(self._send_sticker_sync, room_id, path.resolve())

    def _send_sticker_sync(self, room_id: str, path: Path) -> None:
        if not path.exists():
            raise RuntimeError(f"表情文件不存在：{path}")
        with self._api_lock:
            wcf = self._wcf
            if wcf is None:
                raise RuntimeError("微信尚未连接")
            ret: Any = None
            if path.suffix.lower() == ".gif" and callable(getattr(wcf, "send_emotion", None)):
                try:
                    ret = wcf.send_emotion(str(path), room_id)
                except Exception:
                    ret = None
                if isinstance(ret, int) and ret == 0:
                    self._recent_sticker_at[room_id] = time.time()
                    return
            ret = wcf.send_image(str(path), room_id)
            if isinstance(ret, int) and ret != 0:
                raise RuntimeError(f"表情发送失败，错误码 {ret}")
        self._recent_sticker_at[room_id] = time.time()
        self._remember_outgoing(room_id, f"[sticker]{path.name}")

    def _remember_outgoing(self, room_id: str, content: str) -> None:
        now = time.time()
        self._recent_outgoing.append((room_id, content, now))
        self._recent_outgoing = [x for x in self._recent_outgoing if now - x[2] < 20][-50:]

    def _is_outgoing_echo(self, room_id: str, content: str) -> bool:
        now = time.time()
        for r, c, ts in self._recent_outgoing:
            if r == room_id and c == content and now - ts < 12:
                return True
        return False

    # ---------------------------------------------------------------- queries
    async def list_groups(self) -> list[GroupInfo]:
        if self._wcf is None:
            return []
        return await asyncio.to_thread(self._fetch_groups, self._wcf)

    async def get_members(
        self, room_id: str, include_history: bool = True
    ) -> list[MemberInfo]:
        if self._wcf is None:
            return []
        data = await asyncio.to_thread(self._fetch_members, room_id)
        return [
            MemberInfo(wxid=wxid, nickname=name)
            for wxid, name in data.items()
        ]

    def set_readable_groups(self, room_ids: set[str]) -> None:
        self._readable_rooms = set(room_ids)

    async def get_account(self) -> AccountInfo | None:
        return self._account if self._wcf else None

    async def refresh(self) -> None:
        if self._wcf is not None:
            groups = await asyncio.to_thread(self._fetch_groups, self._wcf)
            self.emit("groups", groups=groups)

    def _fetch_groups(self, wcf: Any) -> list[GroupInfo]:
        contacts: Any = []
        try:
            with self._api_lock:
                getter = getattr(wcf, "get_chatrooms", None)
                if callable(getter):
                    contacts = getter() or []
                if not contacts:
                    contacts = wcf.get_contacts() or []
        except Exception as exc:
            self.log(f"获取通讯录失败：{exc}", level="warn")

        count = len(contacts) if hasattr(contacts, "__len__") else "?"
        self.log(f"通讯录原始数据 type={type(contacts).__name__} count={count}")
        result = self._groups_from_contacts(contacts)
        if not result:
            self.log("通讯录中没有 @chatroom 条目，尝试 SQL 兜底")
            result = self._groups_from_sql(wcf)
        if result:
            self.log(f"同步到 {len(result)} 个群聊")
        else:
            self.log("未读取到群聊，稍后自动重试；可点击控制台刷新群列表", level="warn")
        result.sort(key=lambda group: group.name)
        return result

    def _groups_from_contacts(self, contacts: Any) -> list[GroupInfo]:
        result: dict[str, GroupInfo] = {}
        if not contacts:
            return []
        if isinstance(contacts, dict):
            contacts = (
                contacts.get("contacts")
                or contacts.get("data")
                or contacts.get("list")
                or []
            )
        if not isinstance(contacts, (list, tuple)):
            self.log(f"通讯录格式异常：{type(contacts).__name__}", level="warn")
            return []
        sample = []
        for contact in contacts[:5]:
            sample.append(
                {
                    "wxid": str(_get(contact, "wxid", "") or ""),
                    "name": str(_get(contact, "name", "") or ""),
                    "remark": str(_get(contact, "remark", "") or ""),
                }
            )
        self.log(f"通讯录样例：{sample}")
        for contact in contacts:
            wxid = str(_get(contact, "wxid", "") or "")
            if not wxid.endswith(GROUP_SUFFIX):
                continue
            name = str(
                _get(contact, "remark", "")
                or _get(contact, "name", "")
                or _get(contact, "nickname", "")
                or wxid
            )
            self._group_names[wxid] = name
            result[wxid] = GroupInfo(
                external_id=wxid,
                name=name,
                avatar=str(_get(contact, "avatar", "") or ""),
                meta={"source": "wcf"},
            )
        return list(result.values())

    def _groups_from_sql(self, wcf: Any) -> list[GroupInfo]:
        """Fallback for WeChat builds where get_contacts omits chatrooms."""
        rows: list[dict[str, Any]] = []
        contact_names: dict[str, str] = {}
        contact_rows: list[dict[str, Any]] = []
        try:
            with self._api_lock:
                rows = wcf.query_sql("MicroMsg.db", "SELECT * FROM ChatRoom;") or []
                contact_rows = (
                    wcf.query_sql(
                        "MicroMsg.db",
                        "SELECT UserName, NickName, Remark FROM Contact;",
                    )
                    or []
                )
            self.log(
                f"SQL 结果：ChatRoom={len(rows)} 行，Contact={len(contact_rows)} 行"
            )
            if rows:
                self.log(
                    f"ChatRoom 列：{list(rows[0].keys())}；样例：{rows[0]}"
                )
            for row in contact_rows:
                wxid = str(_get(row, "UserName", "") or "")
                if not wxid:
                    continue
                contact_names[wxid] = str(
                    _get(row, "Remark", "")
                    or _get(row, "NickName", "")
                    or wxid
                )
        except Exception as exc:
            self.log(f"SQL 读取群聊失败：{exc}", level="warn")
            return []

        result: dict[str, GroupInfo] = {}
        for row in rows:
            wxid = str(
                _get(row, "ChatRoomName", "")
                or _get(row, "UserName", "")
                or ""
            )
            if not wxid.endswith(GROUP_SUFFIX):
                continue
            name = str(
                _get(row, "NickName", "")
                or _get(row, "Remark", "")
                or contact_names.get(wxid, "")
                or wxid
            )
            if name == wxid and wxid in self._group_names:
                name = self._group_names[wxid]
            self._group_names[wxid] = name
            result[wxid] = GroupInfo(
                external_id=wxid,
                name=name,
                avatar="",
                meta={"source": "wcf-sql"},
            )
        return list(result.values())

    def _delayed_group_refresh(self, wcf: Any) -> None:
        for delay in (3, 5, 10):
            if self._stop.is_set() or self._wcf is not wcf:
                return
            time.sleep(delay)
            if self._stop.is_set() or self._wcf is not wcf:
                return
            groups = self._fetch_groups(wcf)
            if groups:
                self.emit("groups", groups=groups)
                return

    def _fetch_members(self, room_id: str) -> dict[str, str]:
        cached = self._member_cache.get(room_id)
        if cached and time.time() - cached[0] < 600:
            return cached[1]
        wcf = self._wcf
        if wcf is None:
            return cached[1] if cached else {}
        try:
            with self._api_lock:
                raw = wcf.get_chatroom_members(room_id)
            members = self._normalise_members(raw)
        except Exception as exc:
            self.log(f"获取群成员失败：{exc}", level="warn")
            return cached[1] if cached else {}
        with self._cache_lock:
            self._member_cache[room_id] = (time.time(), members)
        return members

    @staticmethod
    def _normalise_members(raw: Any) -> dict[str, str]:
        members: dict[str, str] = {}
        if isinstance(raw, dict):
            for wxid, value in raw.items():
                if isinstance(value, dict):
                    name = (
                        value.get("name")
                        or value.get("nickname")
                        or value.get("remark")
                        or wxid
                    )
                else:
                    name = str(value or wxid)
                members[str(wxid)] = str(name)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    wxid = str(item.get("wxid") or item.get("userName") or "")
                    if not wxid:
                        continue
                    name = str(
                        item.get("name")
                        or item.get("nickname")
                        or item.get("remark")
                        or wxid
                    )
                    members[wxid] = name
                else:
                    members[str(item)] = str(item)
        return members

    def _member_name(self, room_id: str, wxid: str) -> str:
        if not wxid:
            return "系统"
        members = self._fetch_members(room_id)
        return members.get(wxid) or wxid

    # ------------------------------------------------------------------ qr/UI
    def _capture_qr(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import win32gui
            from PIL import Image, ImageGrab
        except Exception:
            self.emit(
                "status",
                state="qr",
                message="请安装 pywin32/Pillow，或直接打开微信窗口扫码",
            )
            return

        candidates: list[int] = []

        def _enum(hwnd: int, _extra: Any) -> None:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                if win32gui.GetClassName(hwnd) == "WeChatLoginWndForPC":
                    candidates.append(hwnd)
            except Exception:
                return

        try:
            win32gui.EnumWindows(_enum, None)
        except Exception:
            return
        if not candidates:
            return
        hwnd = candidates[0]

        image = self._print_window(hwnd)
        if image is None:
            # PrintWindow can fail on GPU-rendered skins. Fall back to a screen
            # capture after bringing the official login window forward.
            try:
                win32gui.ShowWindow(hwnd, 9)
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.25)
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                image = ImageGrab.grab(
                    bbox=(left, top, right, bottom), all_screens=True
                )
            except Exception as exc:
                self._report_error(f"截取二维码失败：{exc}")
                return
        if image is None:
            return

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        digest = hashlib.md5(data).hexdigest()
        if digest == self._last_qr_hash:
            return
        self._last_qr_hash = digest
        self.emit(
            "qr",
            image_b64=base64.b64encode(data).decode("ascii"),
            mime="image/png",
        )

    @staticmethod
    def _print_window(hwnd: int):
        """Capture a window even when it is covered by another window."""
        try:
            import win32gui
            import win32ui
            from PIL import Image
        except Exception:
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(bitmap)
            result = 0
            for flags in (2, 1):
                result = ctypes.windll.user32.PrintWindow(  # type: ignore[attr-defined]
                    hwnd, save_dc.GetSafeHdc(), flags
                )
                if result:
                    break
            if not result:
                return None
            info = bitmap.GetInfo()
            bits = bitmap.GetBitmapBits(True)
            image = Image.frombuffer(
                "RGB",
                (info["bmWidth"], info["bmHeight"]),
                bits,
                "raw",
                "BGRX",
                0,
                1,
            )
            if image.getbbox() is None:
                return None
            return image
        except Exception:
            return None
        finally:
            try:
                win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass
            try:
                save_dc.DeleteDC()
                mfc_dc.DeleteDC()
                win32gui.ReleaseDC(hwnd, hwnd_dc)
            except Exception:
                pass

    @staticmethod
    def _setup_dpi() -> None:
        if sys.platform != "win32":
            return
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
            except Exception:
                pass

    # -------------------------------------------------------------- wechat exe
    def _find_wechat_exe(self) -> Path | None:
        configured = str(self.options.get("wechat_exe", "") or "").strip()
        if configured:
            path = Path(configured)
            if path.exists():
                return path
        for candidate in self._default_wechat_paths():
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _wechat_running() -> bool:
        if sys.platform != "win32":
            return False
        # Check the compatible client process explicitly. Weixin 4.x runs as
        # Weixin.exe and must not be mistaken for the 3.9 client, otherwise the
        # adapter would skip launching the installed 3.9 executable.
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq WeChat.exe", "/NH"],
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "WeChat.exe" in result.stdout
        except Exception:
            return False

    def _launch_wechat(self) -> None:
        if not self._wechat_exe:
            return
        self._last_launch = time.time()
        try:
            env = os.environ.copy()
            # WeChat 3.9 32-bit on 64-bit Windows otherwise reports
            # "微信版本过低". This compatibility layer is the same approach
            # used by the WeChat3.9-32bit-Compatibility-Launcher project.
            env["__COMPAT_LAYER"] = "~ ARM64WOWONAMD64"
            subprocess.Popen(
                [str(self._wechat_exe)],
                cwd=str(self._wechat_exe.parent),
                env=env,
                close_fds=True,
            )
            self.log(f"已启动微信：{self._wechat_exe}（启用 32 位兼容层）")
        except Exception as exc:
            self._report_error(f"启动微信失败：{exc}")

    # ------------------------------------------------------------------- misc
    def _login_hint(self, error: str) -> str:
        if "尚未登录" in error:
            return "微信已启动，请用手机扫码登录（二维码显示在左侧）"
        if "版本" in error or "注入" in error:
            return f"请确认微信版本为 {SUPPORTED_WECHAT}，并保持微信窗口打开"
        if "wcferry" in error.lower() or "DLL" in error:
            return "WeChatFerry 初始化失败，请检查安装与微信版本"
        return f"等待微信登录：{error}"

    def _report_error(self, message: str) -> None:
        now = time.time()
        if message == self._last_error and now - self._last_error_at < 30:
            return
        self._last_error = message
        self._last_error_at = now
        self.emit("log", message=message, level="warn")
