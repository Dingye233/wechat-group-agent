from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import io
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

from app.adapters.base import (
    AccountInfo,
    BaseAdapter,
    GroupInfo,
    IncomingMessage,
    MemberInfo,
)

GROUP_TITLE_RE = re.compile(r"\s*\((\d+)\)\s*$")
GROUP_ID_PREFIX = "uia:"
MEMBER_ID_PREFIX = "uia:"
_HUMAN_INPUT_INSTALLED = False
_ACTIVE_GUARD: Any = None


class AutomationDetached(RuntimeError):
    """Raised when a real user takes over the mouse."""


def _install_human_input(guard: Any = None) -> None:
    """Make wxauto clicks move the cursor smoothly like a person.

    wxauto calls ``Click(simulateMove=False)`` in many places, which teleports
    the cursor. We replace the UIAutomation Click/MoveTo helpers once per
    process so every click first travels along a short, slightly curved path.
    The active adapter also guards every movement against real user input.
    """
    global _HUMAN_INPUT_INSTALLED, _ACTIVE_GUARD
    _ACTIVE_GUARD = guard
    if _HUMAN_INPUT_INSTALLED:
        return
    try:
        import win32api
        from wxauto import uiautomation as uia
    except Exception:
        return

    def human_move_to(
        x: int, y: int, moveSpeed: float = 1, waitTime: float = 0
    ) -> None:
        guard = _ACTIVE_GUARD
        if guard is not None:
            guard.before_mouse_action()
        started_at = time.time()
        if guard is not None:
            guard.begin_synthetic_motion()
        try:
            start_x, start_y = win32api.GetCursorPos()
            dx = float(x) - start_x
            dy = float(y) - start_y
            distance = math.hypot(dx, dy)
            duration = (
                min(0.95, max(0.18, distance / 1300.0))
                * random.uniform(0.85, 1.15)
            )
            steps = max(10, min(80, int(duration / 0.012)))
            mid_x = start_x + dx / 2.0
            mid_y = start_y + dy / 2.0
            if distance > 40:
                mid_x += random.uniform(-0.07, 0.07) * abs(dy)
                mid_y += random.uniform(-0.07, 0.07) * abs(dx)
            expected_x, expected_y = start_x, start_y
            for index in range(1, steps + 1):
                if guard is not None:
                    guard.check_move_takeover(started_at, expected_x, expected_y)
                t = index / steps
                ease = t * t * (3.0 - 2.0 * t)
                # Quadratic Bézier with a small lateral offset.
                cx = (
                    (1 - ease) * (1 - ease) * start_x
                    + 2 * (1 - ease) * ease * mid_x
                    + ease * ease * x
                )
                cy = (
                    (1 - ease) * (1 - ease) * start_y
                    + 2 * (1 - ease) * ease * mid_y
                    + ease * ease * y
                )
                win32api.SetCursorPos((int(cx), int(cy)))
                expected_x, expected_y = int(cx), int(cy)
                time.sleep(duration / steps)
            if guard is not None:
                guard.check_move_takeover(started_at, expected_x, expected_y)
            win32api.SetCursorPos((int(x), int(y)))
            if waitTime:
                time.sleep(waitTime)
        except AutomationDetached:
            raise
        except Exception:
            if guard is not None:
                guard.before_mouse_action()
            try:
                win32api.SetCursorPos((int(x), int(y)))
            except Exception:
                pass
        finally:
            if guard is not None:
                guard.end_synthetic_motion()
                guard.note_auto_action()

    uia.MoveTo = human_move_to
    original_click = uia.Control.Click
    default_wait = float(getattr(uia, "OPERATION_WAIT_TIME", 0.5))

    def human_click(
        self,
        x=None,
        y=None,
        ratioX=0.5,
        ratioY=0.5,
        simulateMove=True,
        waitTime=default_wait,
    ):
        guard = _ACTIVE_GUARD
        if guard is not None:
            guard.before_mouse_action()
        # A short hesitation before the click and a small random point inside
        # the control make repeated actions look less mechanical.
        time.sleep(random.uniform(0.04, 0.14))
        if x is None and y is None:
            ratioX = random.uniform(0.42, 0.58)
            ratioY = random.uniform(0.42, 0.58)
        try:
            return original_click(
                self, x, y, ratioX, ratioY, True, waitTime
            )
        finally:
            if guard is not None:
                guard.note_auto_action()

    uia.Control.Click = human_click
    _HUMAN_INPUT_INSTALLED = True

SKIP_SESSION_NAMES = {
    "",
    "群聊",
    "新的朋友",
    "标签",
    "公众号",
    "通讯录",
    "聊天",
    "搜索",
    "全部",
    "微信",
    "置顶",
    "最小化",
    "最大化",
    "关闭",
    "折叠置顶聊天",
    "通讯录管理",
    "添加朋友",
    "发起群聊",
    "文件传输助手",
}

CONTENT_KINDS: tuple[tuple[str, str], ...] = (
    ("[图片]", "image"),
    ("[照片]", "image"),
    ("[文件]", "file"),
    ("[语音]", "voice"),
    ("[视频]", "video"),
    ("[动画表情]", "sticker"),
    ("[表情]", "sticker"),
    ("[链接]", "app"),
    ("[小程序]", "app"),
    ("[音乐]", "app"),
    ("[位置]", "app"),
    ("[名片]", "app"),
    ("[转账]", "app"),
    ("[红包]", "app"),
    ("[聊天记录]", "app"),
    ("[合并转发]", "app"),
)


def _clean_group_title(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*已置顶$", "", text).strip()
    return GROUP_TITLE_RE.sub("", text).strip()


def _split_group_title(value: Any) -> tuple[str, int]:
    text = str(value or "").strip()
    match = GROUP_TITLE_RE.search(text)
    count = int(match.group(1)) if match else 0
    return _clean_group_title(text), count


def _describe_content(content: str) -> tuple[str, str]:
    for prefix, kind in CONTENT_KINDS:
        if content.startswith(prefix):
            return kind, content
    return "text", content


def _is_valid_group_name(name: str) -> bool:
    value = str(name or "").strip()
    if not value or value in SKIP_SESSION_NAMES:
        return False
    if re.fullmatch(r"\d+", value):
        return False
    if value.startswith("共") and value.endswith("个群聊"):
        return False
    if "添加" in value and len(value) <= 6:
        return False
    if len(value) > 120:
        return False
    return True


def _parse_session_preview(preview: str) -> tuple[str, str]:
    """Split a session preview into ``(sender, content)``.

    Group conversations are rendered as ``发送者: 内容``; private chats and
    system notices have no sender prefix. The first colon is the separator,
    so a message that itself contains a colon still works.
    """
    text = str(preview or "").strip().replace("\r", " ").replace("\n", " ")
    if not text:
        return "", ""
    if text.startswith(("http://", "https://")):
        return "", text
    match = re.match(r"^(.{1,32}?)[:：]\s*(.+)$", text)
    if match:
        sender = match.group(1).strip()
        content = match.group(2).strip()
        sender = re.sub(r"^\[\d+条\]\s*", "", sender).strip()
        if sender and not sender.lower().startswith(("http", "www")):
            return sender, content
    return "", text


class UiAutomationAdapter(BaseAdapter):
    """WeChat PC adapter powered by UI Automation (wxauto).

    Compared with protocol injection this adapter does not need a matching
    WeChat build or native DLLs. It reads the official 3.9 client window,
    therefore it can work with 32-bit clients that cannot be used by
    WeChatFerry any more. The trade-off is that the WeChat window must stay
    open and UI operations can briefly bring it to the foreground.

    All UI calls happen on one dedicated thread. Other asyncio tasks submit
    commands through a queue, which keeps COM/UIAutomation usage single
    threaded and avoids cross-apartment errors.
    """

    adapter_id = "uia"
    display_name = "微信 PC（界面自动化）"
    description = (
        "读取 Windows 微信 3.9 客户端界面，支持扫码、实时消息、群成员与聊天记录；"
        "无需注入，兼容 32 位微信。"
    )

    def __init__(self, emit, options: dict[str, Any] | None = None) -> None:
        super().__init__(emit, options)
        self._thread: threading.Thread | None = None
        self._commands: Queue[tuple[Callable[[Any], Any], dict[str, Any]]] = Queue()
        self._api_lock = threading.RLock()
        self._wx: Any = None
        self._wx_name = ""
        self._account = AccountInfo(wxid="", name="微信助手")
        self._wechat_exe: Path | None = None
        self._last_launch = 0.0
        self._last_qr_hash = ""
        self._last_qr_at = 0.0
        self._groups: dict[str, GroupInfo] = {}
        self._raw_names: dict[str, str] = {}
        self._seen: dict[str, set[str]] = {}
        self._seen_order: dict[str, list[str]] = {}
        self._last_discover = 0.0
        self._group_signature = ""
        self._on_contacts_page = False
        self._last_error = ""
        self._last_error_at = 0.0
        self._wxlog_ready = False
        self._session_fingerprints: dict[str, str] = {}
        self._recent_outgoing: list[tuple[str, str, float]] = []
        self._recent_previews: dict[tuple[str, str, str], float] = {}
        self._session_error_count = 0
        self._readable_rooms: set[str] = set()
        self._read_schedule: dict[str, float] = {}
        self._next_check: dict[str, float] = {}
        self._poll_interval = 5.0
        self._media_download_enabled = True
        self._media_pending: dict[str, tuple[str, str, float]] = {}
        self._media_seen: dict[str, set[str]] = {}
        self._pinned_room = ""
        self._pinned_next_check = 0.0
        self._pinned_baseline_ready = False
        self._detached = False
        self._detach_reason = ""
        self._user_mouse_at = 0.0
        self._user_consent_until = 0.0
        self._synthetic_motion = False
        self._last_auto_at = 0.0
        self._last_auto_pos: tuple[int, int] | None = None
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None

    # ---------------------------------------------------------------- metadata
    @classmethod
    def availability(cls) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "仅支持 Windows"
        try:
            import importlib.util

            if importlib.util.find_spec("wxauto") is None:
                return False, "缺少 wxauto，请安装 requirements-uia.txt"
        except Exception:
            return False, "缺少 wxauto，请安装 requirements-uia.txt"
        if cls._find_compatible_wechat() is None:
            weixin4 = cls._detect_weixin4()
            if weixin4 is not None:
                return (
                    False,
                    f"检测到微信 4.x（{weixin4}），界面自动化适配器仅支持微信 PC 3.9.x",
                )
            return False, "未找到微信 PC 3.9.x，请先安装并登录微信"
        return True, ""

    @classmethod
    def _detect_weixin4(cls) -> Path | None:
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

    @classmethod
    def _find_compatible_wechat(cls) -> Path | None:
        for candidate in cls._default_wechat_paths():
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _default_wechat_paths() -> list[Path]:
        roots = [
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Tencent"
            / "WeChat",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Tencent"
            / "WeChat",
            Path(r"D:\WeChat3.9x86"),
            Path(r"D:\WeChat3.9"),
            Path(r"D:\WeChat"),
            Path(r"D:\Tencent\WeChat"),
        ]
        candidates: list[Path] = []
        # Prefer the versioned 3.9 subdirectory. WeChat's updater keeps both
        # the launcher and the real executable around.
        for root in roots:
            try:
                for child in sorted(root.glob("*/WeChat.exe"), reverse=True):
                    candidates.append(child)
            except Exception:
                pass
        for root in roots:
            candidates.append(root / "WeChat.exe")
        candidates.extend(
            [
                Path(r"D:\WeChat3.9x86\WeChat.exe"),
                Path(r"D:\WeChat3.9\WeChat.exe"),
                Path(r"D:\WeChat\WeChat.exe"),
                Path(r"D:\Tencent\WeChat\WeChat.exe"),
            ]
        )
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
                            candidates.append(candidate)
                except Exception:
                    continue
        except Exception:
            pass
        return candidates

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
            target=self._worker, daemon=True, name="wechat-uia-worker"
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        self._fail_pending("适配器已停止")
        await asyncio.to_thread(self._join_worker)
        self.emit("status", state="offline", message="已停止微信界面适配器")

    def _join_worker(self) -> None:
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=8)

    def _fail_pending(self, message: str) -> None:
        while True:
            try:
                _, box = self._commands.get_nowait()
            except Empty:
                break
            box["error"] = message
            box["event"].set()

    # ------------------------------------------------------------------ worker
    def _worker(self) -> None:
        self._setup_dpi()
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # type: ignore[attr-defined]
        except Exception:
            pass
        _install_human_input(self)
        self._start_mouse_watch()
        try:
            self._wechat_exe = self._find_wechat_exe()
            if self._wechat_exe is None and not self._wechat_running():
                self._report_error(
                    "未找到微信 PC 3.9 客户端，请安装微信 3.9.x 或在设置中填写 WeChat.exe 路径。"
                )
                self.emit("status", state="error", message="未找到微信 PC 3.9 客户端")
                return
            if self._wechat_exe and not self._wechat_running():
                self._launch_wechat()

            while not self._stop.is_set():
                self._drain_commands()
                try:
                    state = self._window_state()
                    if state == "login":
                        self._drop_wx()
                        self._emit_login_qr()
                        self._try_enter_wechat()
                        self.emit(
                            "status",
                            state="qr",
                            message="请用手机微信扫描二维码登录，登录后请保持微信窗口打开…",
                        )
                        self._stop.wait(1.5)
                    elif state == "main":
                        if self._wx is None:
                            if self._connect():
                                continue
                            self._stop.wait(2.5)
                            continue
                        self._poll_cycle()
                        # Per-group timers decide what to read; this is only a
                        # short scheduler tick so due groups are not delayed.
                        self._stop.wait(0.5)
                    else:
                        if (
                            self._wechat_exe
                            and not self._wechat_running()
                            and time.time() - self._last_launch > 10
                        ):
                            self._launch_wechat()
                        self.emit(
                            "status",
                            state="qr",
                            message="等待微信启动，请稍候或手动打开微信扫码…",
                        )
                        self._emit_login_qr()
                        self._stop.wait(1.5)
                except AutomationDetached:
                    if self._stop.is_set():
                        break
                    self._stop.wait(0.5)
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    self._report_error(f"界面适配器异常：{exc}")
                    self._stop.wait(2.0)
                self._drain_commands()
        finally:
            self._stop_mouse_watch()
            self._fail_pending("微信界面适配器已停止")
            with self._api_lock:
                self._wx = None
            try:
                ctypes.windll.ole32.CoUninitialize()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _drain_commands(self) -> None:
        while True:
            try:
                func, box = self._commands.get_nowait()
            except Empty:
                break
            try:
                with self._api_lock:
                    wx = self._wx
                    if wx is None:
                        raise RuntimeError("微信尚未登录")
                    box["result"] = func(wx)
            except Exception as exc:
                box["error"] = str(exc)
            finally:
                box["event"].set()

    def _run_on_worker(
        self, func: Callable[[Any], Any], timeout: float = 60.0
    ) -> Any:
        if self._stop.is_set():
            raise RuntimeError("适配器已停止")
        if not self._thread or not self._thread.is_alive():
            raise RuntimeError("微信界面适配器未运行")
        busy = self._commands.qsize()
        if busy > 6:
            raise RuntimeError("微信界面操作繁忙，请稍后再试")
        box: dict[str, Any] = {"event": threading.Event()}
        self._commands.put((func, box))
        if not box["event"].wait(timeout):
            raise TimeoutError("微信界面操作超时，请确认微信窗口没有卡住")
        if box.get("error"):
            raise RuntimeError(str(box["error"]))
        return box.get("result")

    # ----------------------------------------------------------------- connect
    def _connect(self) -> bool:
        try:
            self._configure_wxauto_logging()
            from wxauto import WeChat
        except Exception as exc:
            self._report_error(f"无法加载 wxauto：{exc}")
            self.emit("status", state="qr", message="界面自动化组件加载失败")
            return False

        wx = None
        try:
            wx = WeChat(debug=False)
            name = str(getattr(wx, "nickname", "") or "").strip()
            if not name:
                name = str(getattr(getattr(wx, "A_MyIcon", None), "Name", "") or "").strip()
            if not name:
                name = "微信用户"
            self._wx_name = name
            self._session_fingerprints.clear()
            self._recent_outgoing.clear()
            self._recent_previews.clear()
            self._readable_rooms.clear()
            self._read_schedule.clear()
            self._next_check.clear()
            self._media_pending.clear()
            self._media_seen.clear()
            self._pinned_room = ""
            self._pinned_next_check = 0.0
            self._pinned_baseline_ready = False
            with self._api_lock:
                self._wx = wx
            self._account = AccountInfo(
                wxid=f"{GROUP_ID_PREFIX}{name}",
                name=name,
            )
            self.emit(
                "account",
                wxid=self._account.wxid,
                name=self._account.name,
                avatar=self._account.avatar,
            )
            self.emit(
                "status",
                state="online",
                message="微信登录成功，正在读取群聊…",
            )
            # The runtime applies the saved read schedule as soon as it sees
            # the account event. If there are enabled groups, use the saved
            # group index and skip the slow/flaky full contact-manager scan.
            time.sleep(1.5)
            if self._read_schedule:
                groups = list(self._groups.values())
                self.log(
                    "按已保存的群读取计划启动，跳过自动群列表刷新"
                    "（可手动点击刷新群列表）"
                )
            else:
                groups = self._discover_groups(wx, quiet=False)
                if groups:
                    self._emit_groups(groups, force=True)
                else:
                    self.log(
                        "未读取到群聊，可点击控制台刷新群列表；稍后会自动重试",
                        level="warn",
                    )
            self.emit(
                "status",
                state="online",
                message="微信已连接，Agent 正在后台运行",
            )
            self.log(
                f"微信界面登录成功：{name}，"
                f"{'启用读取' if self._read_schedule else '发现'} "
                f"{len(groups)} 个群聊"
            )
            return True
        except AutomationDetached:
            self.emit(
                "status",
                state="online",
                message="检测到用户使用鼠标，自动操作已暂停，请重新接管",
            )
            return True
        except Exception as exc:
            with self._api_lock:
                self._wx = None
            self._report_error(f"连接微信界面失败：{exc}")
            self.emit(
                "status",
                state="qr",
                message="微信界面尚未就绪，正在重试…",
            )
            return False

    def _drop_wx(self) -> None:
        with self._api_lock:
            self._wx = None
        self._wx_name = ""
        self._readable_rooms.clear()
        self._read_schedule.clear()
        self._next_check.clear()
        self._media_pending.clear()
        self._media_seen.clear()
        self._pinned_room = ""
        self._pinned_next_check = 0.0
        self._pinned_baseline_ready = False

    def _poll_cycle(self) -> None:
        """Check only the groups whose independent timer is due.

        Each read-enabled group has its own ``next_check`` timestamp. On a
        scheduler tick we read the session list once, index it by group name,
        and update only the due groups. Groups with reading disabled never
        enter the schedule, so their previews are never processed.
        """
        wx = self._wx
        if wx is None or not self._read_schedule:
            return
        now = time.time()
        due = [
            room_id
            for room_id, next_at in self._next_check.items()
            if next_at <= now and room_id in self._read_schedule
        ]
        if due:
            sessions = self._read_sessions(wx)
            if sessions:
                session_index: dict[str, Any] = {}
                for session in sessions:
                    try:
                        name = _clean_group_title(getattr(session, "name", ""))
                        if name:
                            session_index[name] = session
                    except Exception:
                        continue
                due_names: set[str] = set()
                for room_id in due:
                    interval = max(
                        1.0,
                        float(
                            self._read_schedule.get(room_id)
                            or self._poll_interval
                        ),
                    )
                    name = (
                        room_id[len(GROUP_ID_PREFIX) :]
                        if room_id.startswith(GROUP_ID_PREFIX)
                        else room_id
                    )
                    # The pinned group is read from the open chat window,
                    # not from the session preview, so skip it here.
                    if room_id != self._pinned_room and name in session_index:
                        due_names.add(name)
                    self._next_check[room_id] = now + interval + random.uniform(
                        0.0, min(1.0, max(0.1, interval * 0.1))
                    )
                if due_names:
                    self._consume_session_previews(
                        sessions, only_groups=due_names
                    )
                    self._process_media_pending(wx)
        self._pinned_tick(wx)

    def _pinned_tick(self, wx: Any) -> None:
        if self._detached:
            return
        if not self._pinned_room:
            return
        if self._pinned_room not in self._read_schedule:
            return
        now = time.time()
        if now < self._pinned_next_check:
            return
        self._pinned_next_check = now + 1.0
        name = (
            self._pinned_room[len(GROUP_ID_PREFIX) :]
            if self._pinned_room.startswith(GROUP_ID_PREFIX)
            else self._pinned_room
        )
        if not name:
            return
        try:
            current = _clean_group_title(wx.CurrentChat())
        except Exception:
            current = ""
        if current != name:
            if not self._open_group(wx, name):
                return
            self._scroll_chat_bottom(wx)
            time.sleep(0.2)
        if not self._pinned_baseline_ready:
            try:
                messages = wx.GetAllMessage() or []
            except Exception:
                messages = []
            seen = self._seen.setdefault(name, set())
            for message in messages:
                msg_id = str(getattr(message, "id", "") or "")
                if msg_id:
                    seen.add(msg_id)
            self._pinned_baseline_ready = True
            self.log(f"已置顶「{name}」，开始实时读取当前聊天")
            return
        self._read_group(wx, name, limit=50, historical=False)

    def _scroll_chat_bottom(self, wx: Any) -> None:
        self._ensure_not_detached()
        # Use the UIA scroll pattern only. WheelDown/WheelUp move the real
        # cursor to the chat and back, which would interrupt the user.
        try:
            list_control = wx.C_MsgList
            pattern = list_control.GetScrollPattern()
            if pattern is not None:
                pattern.SetScrollPercent(-1, 100)
        except Exception:
            pass

    def _read_sessions(self, wx: Any) -> list[Any]:
        # A minimized Qt window may not expose its UIA tree. Restore it
        # without activating it; the window stays behind the user's current
        # application.
        self._ensure_window_readable()
        try:
            sessions = wx.GetSession() or []
            self._session_error_count = 0
            return list(sessions)
        except Exception as exc:
            self._session_error_count += 1
            if self._session_error_count <= 3:
                self.log(f"读取会话列表失败：{exc}", level="warn")
            return []

    @staticmethod
    def _ensure_window_readable() -> None:
        if sys.platform != "win32":
            return
        try:
            import win32con
            import win32gui

            hwnd = win32gui.FindWindow("WeChatMainWndForPC", None)
            if not hwnd:
                return
            if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
                # SW_SHOWNOACTIVATE: restore/show without stealing focus.
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                time.sleep(0.15)
        except Exception:
            return

    def _consume_session_previews(
        self, sessions: list[Any], only_groups: set[str] | None = None
    ) -> None:
        group_names = {group.name for group in self._groups.values()}
        if not group_names:
            return
        for session in sessions:
            try:
                group_name = _clean_group_title(getattr(session, "name", ""))
                if not group_name or group_name not in group_names:
                    continue
                if only_groups is not None and group_name not in only_groups:
                    continue
                if f"{GROUP_ID_PREFIX}{group_name}" not in self._read_schedule:
                    continue
                preview = str(getattr(session, "content", "") or "").strip()
                if not preview:
                    continue
                stamp = str(getattr(session, "time", "") or "").strip()
                parsed_sender, parsed_content = _parse_session_preview(preview)
                if not parsed_content:
                    continue
                # Do not include the unread-count prefix or the relative time
                # label in the fingerprint. WeChat rewrites those while the
                # latest message stays unchanged, which used to re-emit the
                # previous message on every timer tick.
                fingerprint = f"{parsed_sender}|{parsed_content}"
                if self._session_fingerprints.get(group_name) == fingerprint:
                    continue
                first_seen = group_name not in self._session_fingerprints
                self._session_fingerprints[group_name] = fingerprint
                if first_seen:
                    self.log(f"首次检查群「{group_name}」，建立会话预览基线")
                    continue
                if self._preview_matches_recent_outgoing(group_name, preview):
                    continue
                payload = self._preview_payload(group_name, preview, stamp)
                if payload is None:
                    continue
                if (
                    payload["kind"] in ("image", "sticker")
                    and self._media_download_enabled
                ):
                    self._media_pending[group_name] = (
                        preview,
                        stamp,
                        time.time(),
                    )
                    continue
                if self._is_recent_preview(
                    group_name, payload["sender_name"], payload["content"]
                ) or self._is_recent_preview_content(
                    group_name, payload["content"]
                ):
                    continue
                self._remember_preview(
                    group_name, payload["sender_name"], payload["content"]
                )
                self.log(
                    f"会话预览：{group_name} / {payload['sender_name']}："
                    f"{payload['content'][:80]}"
                )
                self.emit("message", message=payload)
            except Exception:
                continue

    def _process_media_pending(self, wx: Any) -> None:
        if self._detached or not self._media_pending:
            return
        if not self._media_download_enabled:
            for group_name, (preview, stamp, _) in list(
                self._media_pending.items()
            ):
                self._emit_preview_fallback(group_name, preview, stamp)
            self._media_pending.clear()
            return
        items = list(self._media_pending.items())[:2]
        for group_name, (preview, stamp, queued_at) in items:
            self._media_pending.pop(group_name, None)
            if time.time() - queued_at > 180:
                continue
            if f"{GROUP_ID_PREFIX}{group_name}" not in self._read_schedule:
                continue
            try:
                self._sync_media_group(wx, group_name, preview, stamp)
            except Exception as exc:
                self.log(f"读取图片/表情包失败：{exc}", level="warn")
                self._emit_preview_fallback(group_name, preview, stamp)

    def _sync_media_group(
        self, wx: Any, group_name: str, preview: str, stamp: str
    ) -> None:
        if not self._media_download_enabled:
            return
        if f"{GROUP_ID_PREFIX}{group_name}" not in self._read_schedule:
            return
        if not self._open_group(wx, group_name):
            self._emit_preview_fallback(group_name, preview, stamp)
            return
        try:
            messages = wx.GetAllMessage() or []
        except Exception:
            messages = []
        preview_sender, preview_content = _parse_session_preview(preview)
        want_kind, _ = _describe_content(preview_content)
        if want_kind not in ("image", "sticker"):
            return
        seen = self._media_seen.setdefault(group_name, set())
        candidate = None
        for message in reversed(messages):
            try:
                if getattr(message, "type", "") != "friend":
                    continue
                content = str(getattr(message, "content", "") or "").strip()
                kind, _ = _describe_content(content)
                if kind != want_kind:
                    continue
                sender = str(
                    getattr(message, "sender", "")
                    or getattr(message, "sender_remark", "")
                    or ""
                ).strip()
                if preview_sender and sender and sender != preview_sender:
                    continue
                msg_id = str(getattr(message, "id", "") or "")
                if not msg_id or msg_id in seen:
                    continue
                candidate = message
                break
            except Exception:
                continue
        if candidate is None:
            self._emit_preview_fallback(group_name, preview, stamp)
            return
        msg_id = str(getattr(candidate, "id", "") or "")
        media_path = self._download_media_message(wx, candidate)
        if not media_path:
            self._emit_preview_fallback(group_name, preview, stamp)
            self.log(
                f"未能保存{('图片' if want_kind == 'image' else '表情包')}，已记录占位",
                level="warn",
            )
            return
        seen.add(msg_id)
        self._seen.setdefault(group_name, set()).add(msg_id)
        payload = self._media_payload(
            group_name, preview, stamp, media_path, msg_id
        )
        if payload is None:
            return
        self._remember_preview(
            group_name, payload["sender_name"], payload["content"]
        )
        self.log(
            f"已读取{'图片' if want_kind == 'image' else '表情包'}："
            f"{group_name} / {payload['sender_name']}"
        )
        self.emit("message", message=payload)

    def _emit_preview_fallback(
        self, group_name: str, preview: str, stamp: str
    ) -> None:
        payload = self._preview_payload(group_name, preview, stamp)
        if payload is None:
            return
        if self._is_recent_preview(
            group_name, payload["sender_name"], payload["content"]
        ) or self._is_recent_preview_content(group_name, payload["content"]):
            return
        self._remember_preview(
            group_name, payload["sender_name"], payload["content"]
        )
        self.emit("message", message=payload)

    def _media_payload(
        self,
        group_name: str,
        preview: str,
        stamp: str,
        media_path: str,
        msg_id: str,
    ) -> dict[str, Any] | None:
        payload = self._preview_payload(group_name, preview, stamp)
        if payload is None:
            return None
        kind, _ = _describe_content(str(payload.get("content") or ""))
        payload["kind"] = kind
        payload["external_id"] = "uia:media:" + hashlib.sha1(
            f"{group_name}|{msg_id}".encode("utf-8", errors="ignore")
        ).hexdigest()
        payload["media_path"] = media_path
        payload["meta"] = {
            **(payload.get("meta") or {}),
            "source": "uia-media",
            "media_path": media_path,
            "historical": False,
        }
        return payload

    def _media_target(self, suffix: str = ".png"):
        from app.config import settings as app_settings

        folder = app_settings.chat_media_dir
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{uuid.uuid4().hex}{suffix}"

    @staticmethod
    def _media_relative(path: Path) -> str:
        return f"chat/{path.name}"

    def _save_preview_media(
        self, control: Any, suffix: str = ".png", timeout: float = 12.0
    ) -> str:
        from wxauto import uiautomation as uia
        from wxauto.elements import WeChatImage

        target = self._media_target(suffix)
        started = time.time()
        try:
            image = WeChatImage()
            self.log(f"图片预览已打开，尝试保存到 {target.name}")
            saved = image.Save(savepath=str(target), timeout=timeout)
            self.log(f"图片保存返回：{saved}")
        except Exception as exc:
            self.log(f"图片保存失败：{exc!r}", level="warn")
            try:
                uia.WindowControl(
                    ClassName="ImagePreviewWnd", searchDepth=1
                ).SendKeys("{Esc}")
            except Exception:
                pass
            return ""
        try:
            image.Close()
        except Exception:
            pass

        # WeChat's save dialog often ignores WM_SETTEXT and falls back to
        # Documents\微信图片_*.jpg. Look for the file it actually wrote.
        candidates = [Path(saved) if saved else target]
        candidates.extend(
            target.with_suffix(ext)
            for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")
        )
        roots = [
            Path.home() / "Documents",
            Path.home() / "Pictures",
            Path.cwd() / "wxauto文件",
        ]
        for root in roots:
            try:
                for path in root.glob("*"):
                    if not path.is_file():
                        continue
                    if path.suffix.lower() not in (
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".gif",
                        ".webp",
                    ):
                        continue
                    try:
                        if path.stat().st_mtime >= started - 2:
                            candidates.append(path)
                    except Exception:
                        continue
            except Exception:
                continue
        candidates.sort(
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        )
        for candidate in candidates:
            try:
                if not candidate.exists() or candidate.stat().st_size <= 0:
                    continue
                suffix = candidate.suffix.lower() or ".jpg"
                destination = self._media_target(suffix)
                if candidate.parent == destination.parent:
                    return self._media_relative(candidate)
                try:
                    candidate.replace(destination)
                except Exception:
                    destination.write_bytes(candidate.read_bytes())
                return self._media_relative(destination)
            except Exception:
                continue
        return ""

    @staticmethod
    def _find_media_click_target(control: Any) -> Any:
        best_button = None
        best_button_area = 0
        best_pane = None
        best_pane_area = 0
        for child in UiAutomationAdapter._walk_controls(control, max_depth=6):
            if child is control:
                continue
            try:
                rect = child.BoundingRectangle
                area = max(0, rect.right - rect.left) * max(
                    0, rect.bottom - rect.top
                )
                if area < 6000:
                    continue
                if child.ControlTypeName == "ButtonControl":
                    if area > best_button_area:
                        best_button_area = area
                        best_button = child
                elif child.ControlTypeName in (
                    "PaneControl",
                    "ImageControl",
                ):
                    if area > best_pane_area:
                        best_pane_area = area
                        best_pane = child
            except Exception:
                continue
        # Image messages render the picture in a large PaneControl; native
        # stickers use a ButtonControl. Prefer the large button when present.
        if best_button is not None and best_button_area >= 8000:
            return best_button
        if best_pane is not None and best_pane_area >= 12000:
            return best_pane
        return None

    def _download_media_message(self, wx: Any, message: Any) -> str:
        content = str(getattr(message, "content", "") or "").strip()
        kind, _ = _describe_content(content)
        control = getattr(message, "control", None)
        if control is None:
            return ""
        target = self._find_media_click_target(control)
        if target is None:
            return ""
        if kind == "image":
            try:
                target.Click(simulateMove=False)
                time.sleep(random.uniform(0.45, 0.8))
                saved = self._save_preview_media(target)
                if saved:
                    return saved
            except Exception:
                pass
            return self._save_control_screenshot(target)
        if kind == "sticker":
            # Try opening the sticker preview. Many native sticker types do
            # not expose a save dialog; fall back to the placeholder.
            try:
                target.Click(simulateMove=False)
                time.sleep(random.uniform(0.45, 0.8))
                from wxauto import uiautomation as uia

                preview = uia.WindowControl(
                    ClassName="ImagePreviewWnd", searchDepth=1
                )
                if preview.Exists(0.5):
                    return self._save_preview_media(target, timeout=4.0)
                try:
                    preview.SendKeys("{Esc}")
                except Exception:
                    pass
            except Exception:
                pass
            try:
                rect = target.BoundingRectangle
                if rect and rect.width() > 0:
                    from wxauto import uiautomation as uia

                    uia.MoveTo(
                        (rect.left + rect.right) // 2,
                        (rect.top + rect.bottom) // 2,
                        waitTime=0,
                    )
                target.RightClick(simulateMove=False)
                time.sleep(0.3)
                from wxauto import uiautomation as uia

                menu = uia.MenuControl(ClassName="CMenuWnd")
                for label in ("另存为...", "另存为", "保存图片", "保存"):
                    option = menu.MenuItemControl(Name=label)
                    if option.Exists(0.2):
                        # Native sticker save dialogs vary by WeChat build.
                        # Do not risk leaving a modal dialog open.
                        menu.SendKeys("{Esc}")
                        return self._save_control_screenshot(target)
                menu.SendKeys("{Esc}")
            except Exception:
                pass
            return self._save_control_screenshot(target)
        return ""

    def _save_control_screenshot(self, control: Any) -> str:
        target = self._media_target(".png")
        try:
            control.ScreenShot(savePath=str(target))
            if target.exists() and target.stat().st_size > 0:
                return self._media_relative(target)
        except Exception:
            pass
        return ""

    def _preview_payload(
        self, group_name: str, preview: str, stamp: str
    ) -> dict[str, Any] | None:
        sender, content = _parse_session_preview(preview)
        if not content:
            return None
        if sender in ("我", "你", self._wx_name):
            return None
        if any(
            marker in content
            for marker in ("撤回了一条消息", "加入了群聊", "邀请", "修改群名", "群公告")
        ):
            self._remember_outgoing(group_name, content)
            return None
        if self._is_recent_outgoing(group_name, content):
            return None
        kind, text = _describe_content(content)
        timestamp = self._parse_session_time(stamp) or time.time()
        external_id = "uia:preview:" + hashlib.sha1(
            f"{group_name}|{sender}|{text}|{stamp}|{preview}".encode(
                "utf-8", errors="ignore"
            )
        ).hexdigest()
        return {
            "room_id": f"{GROUP_ID_PREFIX}{group_name}",
            "sender_id": f"{MEMBER_ID_PREFIX}{sender or '群成员'}",
            "sender_name": sender or "群成员",
            "content": text,
            "kind": kind,
            "timestamp": timestamp,
            "external_id": external_id,
            "is_self": False,
            "is_group": True,
            "meta": {
                "source": "uia-session",
                "group_name": group_name,
                "preview": True,
            },
        }

    def _remember_outgoing(self, group_name: str, content: str) -> None:
        now = time.time()
        self._recent_outgoing.append((group_name, content, now))
        self._recent_outgoing = [
            item for item in self._recent_outgoing if now - item[2] < 45
        ][-80:]

    def _remember_preview(
        self, group_name: str, sender: str, content: str
    ) -> None:
        now = time.time()
        self._recent_previews[(group_name, sender, content)] = now
        if len(self._recent_previews) > 800:
            self._recent_previews = {
                key: ts
                for key, ts in self._recent_previews.items()
                if now - ts < 180
            }

    def _is_recent_preview(
        self, group_name: str, sender: str, content: str
    ) -> bool:
        ts = self._recent_previews.get((group_name, sender, content))
        return bool(ts and time.time() - ts < 120)

    def _is_recent_preview_content(self, group_name: str, content: str) -> bool:
        now = time.time()
        for (name, _sender, stored), ts in self._recent_previews.items():
            if name == group_name and stored == content and now - ts < 120:
                return True
        return False

    def _is_recent_outgoing(self, group_name: str, content: str) -> bool:
        now = time.time()
        for name, text, ts in self._recent_outgoing:
            if name != group_name or now - ts >= 45:
                continue
            if text == content:
                return True
            if content.startswith("[") and text == "[sticker]":
                return True
        return False

    def _preview_matches_recent_outgoing(
        self, group_name: str, preview: str
    ) -> bool:
        """Suppress the session preview created by our own dashboard send.

        A text like "群伴测试：..." contains a colon, so parsing the preview as
        "sender: content" would otherwise create a duplicate message whose
        sender is the first part of our text.
        """
        now = time.time()
        for name, text, ts in self._recent_outgoing:
            if name != group_name or now - ts >= 45:
                continue
            if text and (text == preview or text in preview):
                return True
            if text == "[sticker]" and any(
                marker in preview
                for marker in ("[图片]", "[动画表情]", "[表情]")
            ):
                return True
        return False

    @staticmethod
    def _parse_session_time(value: str) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        now = datetime.now()
        patterns = (
            (r"^(\d{1,2}):(\d{2})$", "today"),
            (r"^昨天\s+(\d{1,2}):(\d{2})$", "yesterday"),
            (r"^星期([一二三四五六日])\s+(\d{1,2}):(\d{2})$", "weekday"),
            (r"^(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})$", "date"),
            (r"^(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})$", "short"),
            (r"^(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})$", "short_min"),
        )
        for pattern, kind in patterns:
            match = re.match(pattern, text)
            if not match:
                continue
            try:
                if kind == "today":
                    hour, minute = map(int, match.groups())
                    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                elif kind == "yesterday":
                    hour, minute = map(int, match.groups())
                    dt = (now - timedelta(days=1)).replace(
                        hour=hour, minute=minute, second=0, microsecond=0
                    )
                elif kind == "weekday":
                    weekday_text, hour, minute = match.groups()
                    target = "一二三四五六日".index(weekday_text)
                    delta = (now.weekday() - target) % 7
                    dt = (now - timedelta(days=delta)).replace(
                        hour=int(hour), minute=int(minute), second=0, microsecond=0
                    )
                elif kind == "date":
                    year, month, day, hour, minute = map(int, match.groups())
                    dt = datetime(year, month, day, hour, minute)
                elif kind == "short":
                    month, day, hour, minute, second = map(int, match.groups())
                    dt = datetime(now.year, month, day, hour, minute, second)
                else:
                    month, day, hour, minute = map(int, match.groups())
                    dt = datetime(now.year, month, day, hour, minute)
                return time.mktime(dt.timetuple())
            except Exception:
                continue
        return None

    # -------------------------------------------------------------------- qr
    def _emit_login_qr(self) -> None:
        now = time.time()
        if now - self._last_qr_at < 1.2:
            return
        self._last_qr_at = now
        try:
            from wxauto.elements import LoginWnd

            wnd = LoginWnd()
            path = wnd.get_qrcode()
            if not path:
                return
            file = Path(path)
            try:
                data = file.read_bytes()
            finally:
                try:
                    file.unlink()
                except Exception:
                    pass
            if not data:
                return
            digest = hashlib.md5(data).hexdigest()
            if digest == self._last_qr_hash:
                return
            self._last_qr_hash = digest
            self.emit(
                "qr",
                image_b64=base64.b64encode(data).decode("ascii"),
                mime="image/png",
            )
        except Exception as exc:
            self.log(f"读取登录二维码失败：{exc}", level="warn")

    def _try_enter_wechat(self) -> None:
        try:
            from wxauto.elements import LoginWnd

            wnd = LoginWnd()
            # 3.9 shows "登录" when a previous session can be resumed, and
            # "进入微信" after the phone has confirmed a scan.
            for label in ("登录", "进入微信"):
                try:
                    button = wnd.UiaAPI.ButtonControl(Name=label)
                    if button.Exists(0.2):
                        button.Click(simulateMove=False)
                        time.sleep(0.8)
                        return
                except Exception:
                    continue
            wnd.login()
        except Exception:
            pass

    # ---------------------------------------------------------------- groups
    def _discover_groups(self, wx: Any, quiet: bool = False) -> list[GroupInfo]:
        self._ensure_not_detached()
        self._last_discover = time.time()
        names: list[str] = []
        try:
            self._on_contacts_page = True
            names = self._groups_from_contact_manager(wx)
            for attempt in range(3 if not names else 0):
                wx.SwitchToContact()
                # The contacts list loads asynchronously and may be scrolled
                # down to a contact. "群聊" is a virtualised row at the top,
                # so scroll back to the beginning before searching for it.
                time.sleep(0.7 + attempt * 0.5)
                self._scroll_contacts_to_top(wx)
                entry = self._find_control_by_name(wx.SessionBox, "群聊")
                if entry is None:
                    entry = self._find_control_by_name(wx.UiaAPI, "群聊")
                if entry is None:
                    if attempt == 2:
                        self._log_control_sample(wx.SessionBox, "通讯录 SessionBox")
                    continue
                self._click_control(entry)
                time.sleep(1.0 + attempt * 0.4)
                names = self._collect_contact_groups(wx)
                if names:
                    break
                self._log_control_sample(wx.ChatBox, "群聊页 ChatBox")
                self._log_control_sample(wx.SessionBox, "群聊页 SessionBox")
            if not names:
                names = self._groups_from_session_previews(wx)
        except Exception as exc:
            self.log(f"读取通讯录群聊失败：{exc}", level="warn")
        finally:
            try:
                wx.SwitchToChat()
            except Exception:
                pass
            self._on_contacts_page = False
            time.sleep(0.25)

        if not names:
            # Do not fall back to opening every recent session: that both
            # steals focus and can take minutes with a large chat list. The
            # user can retry via the dashboard's "刷新群列表" button.
            self.log(
                "通讯录群聊页未读取到群，未执行会话逐个探测（避免抢占前台）；"
                "请点击控制台刷新群列表重试",
                level="warn",
            )

        result: dict[str, GroupInfo] = {}
        for raw_name in names:
            name, count = _split_group_title(raw_name)
            if not name:
                continue
            external_id = f"{GROUP_ID_PREFIX}{name}"
            result[external_id] = GroupInfo(
                external_id=external_id,
                name=name,
                member_count=count,
                avatar="",
                meta={"source": "uia", "raw_name": str(raw_name)},
            )
            self._raw_names[name] = str(raw_name)
        if result:
            self._groups = result
        if not quiet:
            if result:
                self.log(f"同步到 {len(result)} 个群聊")
            else:
                self.log("未读取到群聊，稍后自动重试", level="warn")
        return list(result.values()) or list(self._groups.values())

    def _groups_from_contact_manager(self, wx: Any) -> list[str]:
        """Read real group chats from 通讯录管理 → 最近群聊.

        The inline contacts page only exposes the currently rendered rows and
        its「群聊」row is not always clickable on 3.9.12.56. The contact
        manager window is a stable, separate UIA window with an explicit
        "最近群聊" filter, so use that as the primary source.
        """
        try:
            from wxauto import uiautomation as uia
        except Exception:
            return []
        names: list[str] = []
        manager = None
        try:
            wx.SwitchToContact()
            time.sleep(0.8)
            self._scroll_contacts_to_top(wx)
            entry = self._find_control_by_name(wx.SessionBox, "通讯录管理")
            if entry is None:
                self.log("通讯录页未找到「通讯录管理」入口", level="warn")
                return []
            self._click_control(entry)
            time.sleep(1.8)
            manager = uia.WindowControl(
                ClassName="ContactManagerWindow", searchDepth=1
            )
            if not manager.Exists(1.0):
                self.log("通讯录管理窗口未打开", level="warn")
                return []
            recent = manager.ButtonControl(Name="最近群聊", searchDepth=10)
            if not recent.Exists(0.8):
                self.log("通讯录管理窗口未找到「最近群聊」筛选", level="warn")
                return []
            recent.Click(simulateMove=False)
            time.sleep(1.2)

            right_list = None
            best_width = 0
            for control in self._walk_controls(manager, max_depth=8):
                try:
                    if control.ControlTypeName != "ListControl":
                        continue
                    rect = control.BoundingRectangle
                    width = rect.right - rect.left
                    if width > best_width:
                        best_width = width
                        right_list = control
                except Exception:
                    continue
            if right_list is None:
                self.log("通讯录管理窗口未找到群聊列表", level="warn")
                return []

            headers = {
                "昵称",
                "备注",
                "标签",
                "朋友权限",
                "微信号",
                "地区",
                "全部",
                "最近群聊",
                "筛选",
            }

            def row_name(item: Any) -> str:
                for control in self._walk_controls(item, max_depth=4):
                    try:
                        if control.ControlTypeName not in (
                            "ButtonControl",
                            "TextControl",
                        ):
                            continue
                        value = str(control.Name or "").strip()
                        if not value or value in headers:
                            continue
                        if _is_valid_group_name(value):
                            return value
                    except Exception:
                        continue
                return ""

            seen: set[str] = set()
            for _ in range(20):
                before = len(names)
                try:
                    items = right_list.GetChildren()
                except Exception:
                    break
                for item in items:
                    try:
                        if item.ControlTypeName != "ListItemControl":
                            continue
                        name = row_name(item)
                        if name and name not in seen:
                            seen.add(name)
                            names.append(name)
                    except Exception:
                        continue
                try:
                    right_list.WheelDown(wheelTimes=5, waitTime=0.15)
                except Exception:
                    break
                time.sleep(0.2)
                if len(names) == before:
                    break
            if names:
                self.log(
                    f"通讯录管理→最近群聊：读取到 {len(names)} 个群聊，"
                    f"样例：{names[:8]}"
                )
            return names
        except Exception as exc:
            self.log(f"读取通讯录管理群聊失败：{exc}", level="warn")
            return []
        finally:
            if manager is not None:
                try:
                    if manager.Exists(0.2):
                        manager.SendKeys("{Esc}")
                        time.sleep(0.4)
                except Exception:
                    pass
            try:
                wx.SwitchToChat()
            except Exception:
                pass
            time.sleep(0.2)

    def _scroll_contacts_to_top(self, wx: Any) -> None:
        """Scroll the contacts list back to the top so「群聊」is rendered."""
        for control in self._walk_controls(wx.SessionBox, max_depth=7):
            try:
                if control.ControlTypeName != "ListControl":
                    continue
                name = str(control.Name or "").strip()
                if name not in ("联系人", "通讯录", ""):
                    continue
                self._ensure_not_detached()
                try:
                    pattern = control.GetScrollPattern()
                    if pattern is not None:
                        pattern.SetScrollPercent(-1, 0)
                except Exception:
                    pass
                time.sleep(0.35)
                return
            except AutomationDetached:
                raise
            except Exception:
                continue

    def _groups_from_session_previews(self, wx: Any) -> list[str]:
        """Read groups from the session list without opening any chat.

        Group previews are rendered as ``发送者：内容`` (full-width colon),
        while private chats show only the message content. This fallback is
        background-safe and also works when the contacts list is unavailable.
        """
        try:
            sessions = wx.GetSession() or []
        except Exception:
            return []
        names: list[str] = []
        for session in sessions:
            try:
                name = _clean_group_title(getattr(session, "name", ""))
                if not _is_valid_group_name(name):
                    continue
                preview = str(getattr(session, "content", "") or "").strip()
                match = re.match(r"^(.{1,24}?)：(.*)$", preview)
                if not match:
                    continue
                sender = match.group(1).strip()
                if not sender or sender in ("我", "你", self._wx_name):
                    continue
                if name == sender:
                    continue
                if name not in names:
                    names.append(name)
            except Exception:
                continue
        return names

    def _collect_contact_groups(self, wx: Any) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def display_name(control: Any) -> str:
            try:
                name = str(control.Name or "").strip()
                if name:
                    return name
                for child in control.GetChildren():
                    try:
                        if child.ControlTypeName != "TextControl":
                            continue
                        text = str(child.Name or "").strip()
                        if text:
                            return text
                    except Exception:
                        continue
            except Exception:
                pass
            return ""

        def collect(list_control: Any) -> None:
            try:
                items = list_control.GetChildren()
            except Exception:
                return
            for control in items:
                try:
                    if control.ControlTypeName not in (
                        "ListItemControl",
                        "ButtonControl",
                    ):
                        continue
                    name = display_name(control)
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    names.append(name)
                except Exception:
                    continue

        # Only inspect ListControls: walking the whole chat pane also picks up
        # window chrome buttons such as "置顶/最小化/最大化/关闭".
        candidates: list[tuple[int, Any]] = []
        for control in self._walk_controls(wx.ChatBox, max_depth=8):
            try:
                if control.ControlTypeName != "ListControl":
                    continue
                count = len(control.GetChildren())
                if count:
                    candidates.append((count, control))
            except Exception:
                continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        best_list = candidates[0][1] if candidates else None
        if best_list is None:
            return []
        collect(best_list)

        # The list is virtualised; scroll it with the UIA pattern so the
        # cursor is never moved into the WeChat window.
        self._ensure_not_detached()
        for step in range(15):
            before = len(names)
            try:
                pattern = best_list.GetScrollPattern()
                if pattern is None:
                    break
                pattern.SetScrollPercent(-1, min(100.0, (step + 1) * 10.0))
            except AutomationDetached:
                raise
            except Exception:
                break
            time.sleep(0.2)
            collect(best_list)
            if len(names) == before:
                break

        filtered: list[str] = []
        for name in names:
            if _is_valid_group_name(name):
                filtered.append(name)
        return filtered

    def _discover_groups_from_sessions(self, wx: Any) -> list[str]:
        """Last-resort fallback: open recent sessions and detect groups.

        Group chat titles in WeChat PC include the member count, for example
        ``产品交流群 (12)``. This is slower and changes the active chat, so it
        is only used when the contacts page cannot be read.
        """
        names: list[str] = []
        try:
            sessions = wx.GetSession() or []
        except Exception:
            return []
        for session in sessions[:80]:
            if self._stop.is_set():
                break
            raw = str(getattr(session, "name", "") or "").strip()
            cleaned = _clean_group_title(raw)
            if not cleaned or cleaned in self._groups:
                continue
            try:
                wx.ChatWith(raw)
                time.sleep(0.15)
                current = str(wx.CurrentChat() or "")
            except Exception:
                continue
            if GROUP_TITLE_RE.search(current):
                names.append(raw)
        if names:
            self.log(f"通过会话列表识别到 {len(names)} 个群聊")
        return names

    def _classify_session(self, wx: Any, name: str) -> bool:
        if not name or name in self._groups:
            return False
        try:
            if not self._open_group(wx, name, bring_to_front=False):
                return False
            current = str(wx.CurrentChat() or "")
        except Exception:
            return False
        if not GROUP_TITLE_RE.search(current):
            return False
        group_name, count = _split_group_title(current)
        external_id = f"{GROUP_ID_PREFIX}{group_name}"
        info = GroupInfo(
            external_id=external_id,
            name=group_name,
            member_count=count,
            meta={"source": "uia-session", "raw_name": name},
        )
        self._groups[external_id] = info
        self._raw_names[group_name] = name
        self._emit_groups(list(self._groups.values()), force=True)
        self.log(f"发现新群聊：{group_name}")
        return True

    def _emit_groups(self, groups: list[GroupInfo], force: bool = False) -> None:
        signature = "|".join(
            sorted(f"{group.external_id}:{group.name}:{group.member_count}" for group in groups)
        )
        if not force and signature == self._group_signature:
            return
        self._group_signature = signature
        self.emit("groups", groups=groups)

    # --------------------------------------------------------------- members
    async def get_members(
        self, room_id: str, include_history: bool = True
    ) -> list[MemberInfo]:
        if self._wx is None:
            return []
        names = await asyncio.to_thread(
            self._run_on_worker,
            lambda wx: self._members_sync(wx, room_id, include_history),
            90.0,
        )
        return [
            MemberInfo(wxid=f"{MEMBER_ID_PREFIX}{name}", nickname=name)
            for name in names
        ]

    def _members_sync(
        self, wx: Any, room_id: str, include_history: bool = True
    ) -> list[str]:
        self._ensure_not_detached()
        name = self._room_name(room_id)
        if not name:
            return []
        if not self._open_group(wx, name):
            raise RuntimeError(f"无法打开群聊：{name}")
        if include_history:
            # Loading one extra page gives the dashboard a useful recent history.
            try:
                wx.LoadMoreMessage()
                time.sleep(0.2)
            except Exception:
                pass
            self._read_group(wx, name, limit=60)

        members: list[str] = []
        try:
            raw_members = wx.GetGroupMembers() or []
        except Exception as exc:
            self.log(f"读取群成员失败：{exc}", level="warn")
            raw_members = []
        for item in raw_members:
            member = str(item or "").strip()
            if member and member not in members and member not in {"添加", "移出"}:
                members.append(member)

        if not members:
            # Fallback: sender names from the currently loaded messages.
            try:
                for message in wx.GetAllMessage() or []:
                    if getattr(message, "type", "") != "friend":
                        continue
                    sender = str(
                        getattr(message, "sender", "")
                        or getattr(message, "sender_remark", "")
                        or ""
                    ).strip()
                    if sender and sender not in members:
                        members.append(sender)
            except Exception:
                pass
        if self._wx_name and self._wx_name not in members:
            members.append(self._wx_name)
        if members:
            current, count = _split_group_title(wx.CurrentChat())
            if count and count >= len(members):
                self._groups.setdefault(
                    f"{GROUP_ID_PREFIX}{current}",
                    GroupInfo(
                        external_id=f"{GROUP_ID_PREFIX}{current}",
                        name=current,
                        member_count=count,
                        meta={"source": "uia"},
                    ),
                )
        return members

    # --------------------------------------------------------------- messages
    def _read_group(
        self, wx: Any, name: str, limit: int = 40, historical: bool = True
    ) -> None:
        try:
            messages = wx.GetAllMessage() or []
        except Exception as exc:
            self.log(f"读取「{name}」聊天记录失败：{exc}", level="warn")
            return
        payloads = self._messages_to_payloads(name, messages, historical)
        if not payloads:
            return
        for payload in payloads[-limit:]:
            self.emit("message", message=payload)

    def _messages_to_payloads(
        self, group_name: str, messages: list[Any], historical: bool = True
    ) -> list[dict[str, Any]]:
        seen = self._seen.setdefault(group_name, set())
        order = self._seen_order.setdefault(group_name, [])
        payloads: list[dict[str, Any]] = []
        current_ts = time.time()
        for message in messages:
            try:
                msg_type = str(getattr(message, "type", "") or "")
                msg_id = str(getattr(message, "id", "") or "")
                if msg_type == "time":
                    parsed = self._parse_message_time(getattr(message, "time", ""))
                    if parsed:
                        current_ts = parsed
                    continue
                if not msg_id:
                    continue
                if msg_id in seen:
                    continue
                seen.add(msg_id)
                order.append(msg_id)
                if msg_type != "friend":
                    continue
                content = str(getattr(message, "content", "") or "").strip()
                if not content:
                    continue
                sender = str(
                    getattr(message, "sender", "")
                    or getattr(message, "sender_remark", "")
                    or "未知成员"
                ).strip()
                if not sender:
                    sender = "未知成员"
                kind, text = _describe_content(content)
                if self._is_recent_preview(
                    group_name, sender, text
                ) or self._is_recent_preview_content(group_name, text):
                    continue
                self._remember_preview(group_name, sender, text)
                external_id = (
                    "uia:"
                    + hashlib.sha1(
                        f"{group_name}|{msg_id}".encode("utf-8", errors="ignore")
                    ).hexdigest()
                )
                payloads.append(
                    {
                        "room_id": f"{GROUP_ID_PREFIX}{group_name}",
                        "sender_id": f"{MEMBER_ID_PREFIX}{sender}",
                        "sender_name": sender,
                        "content": text,
                        "kind": kind,
                        "timestamp": current_ts,
                        "external_id": external_id,
                        "is_self": False,
                        "is_group": True,
                        "meta": {
                            "source": "uia",
                            "group_name": group_name,
                            "msg_type": kind,
                            "historical": historical,
                        },
                    }
                )
            except Exception:
                continue
        if len(order) > 4000:
            keep = order[-2000:]
            self._seen_order[group_name] = keep
            self._seen[group_name] = set(keep)
        return payloads

    @staticmethod
    def _parse_message_time(value: Any) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return time.mktime(datetime.strptime(text, fmt).timetuple())
            except Exception:
                continue
        return None

    # ---------------------------------------------------------------- sending
    async def send_text(self, room_id: str, text: str) -> None:
        await asyncio.to_thread(
            self._run_on_worker, lambda wx: self._send_text_sync(wx, room_id, text), 60.0
        )

    def _send_text_sync(self, wx: Any, room_id: str, text: str) -> None:
        self._ensure_not_detached()
        name = self._room_name(room_id)
        if not self._open_group(wx, name):
            raise RuntimeError(f"无法打开群聊：{name}")
        time.sleep(random.uniform(0.2, 0.5))
        wx.SendMsg(text)
        self._remember_outgoing(name, text)
        time.sleep(random.uniform(0.25, 0.6))

    async def send_sticker(self, room_id: str, path: Path) -> None:
        await asyncio.to_thread(
            self._run_on_worker,
            lambda wx: self._send_sticker_sync(wx, room_id, path.resolve()),
            90.0,
        )

    def _send_sticker_sync(self, wx: Any, room_id: str, path: Path) -> None:
        self._ensure_not_detached()
        if not path.exists():
            raise RuntimeError(f"表情文件不存在：{path}")
        name = self._room_name(room_id)
        if not self._open_group(wx, name):
            raise RuntimeError(f"无法打开群聊：{name}")
        # WeChat's UI does not expose a stable sticker-library API. Sending the
        # PNG as an image is the reliable fallback and still appears in the
        # conversation; the dashboard records it as a sticker.
        time.sleep(random.uniform(0.2, 0.5))
        result = wx.SendFiles(str(path))
        if result is False:
            raise RuntimeError("表情发送失败，请确认微信窗口可以操作")
        self._remember_outgoing(name, "[sticker]")
        time.sleep(random.uniform(0.3, 0.7))

    def _open_group(self, wx: Any, name: str, bring_to_front: bool = True) -> bool:
        self._ensure_not_detached()
        if not name:
            return False
        if self._on_contacts_page:
            try:
                wx.SwitchToChat()
                self._on_contacts_page = False
                time.sleep(0.2)
            except Exception:
                return False
        try:
            if _clean_group_title(wx.CurrentChat()) == name:
                return True
        except Exception:
            pass
        raw = self._raw_names.get(name) or name
        candidates = [raw]
        if raw != name:
            candidates.append(name)
        for candidate in candidates:
            try:
                wx.ChatWith(candidate)
                time.sleep(0.2)
                current = _clean_group_title(wx.CurrentChat())
                if current == name:
                    return True
                # The contact manager list truncates very long group names.
                # A unique long prefix is enough to consider the group opened.
                if len(name) >= 6 and (
                    current.startswith(name) or name.startswith(current)
                ):
                    return True
            except Exception:
                continue
        # Some builds do not expose the current title until the chat is focused.
        try:
            if bring_to_front:
                wx.UiaAPI.SwitchToThisWindow()
            current = _clean_group_title(wx.CurrentChat())
            if current == name:
                return True
            return bool(
                len(name) >= 6
                and (current.startswith(name) or name.startswith(current))
            )
        except Exception:
            return False

    # ---------------------------------------------------------------- queries
    async def list_groups(self) -> list[GroupInfo]:
        if self._wx is None:
            return []
        groups = await asyncio.to_thread(
            self._run_on_worker,
            lambda wx: self._discover_groups(wx, quiet=False),
            150.0,
        )
        return list(groups or [])

    async def get_account(self) -> AccountInfo | None:
        return self._account if self._wx else None

    def set_readable_groups(self, room_ids: set[str]) -> None:
        interval = max(1.0, float(self._poll_interval or 5.0))
        self.set_read_schedule({room_id: interval for room_id in room_ids})

    def set_read_schedule(self, schedule: dict[str, float]) -> None:
        now = time.time()
        self._read_schedule = {
            str(room_id): max(1.0, float(interval or self._poll_interval))
            for room_id, interval in schedule.items()
            if room_id
        }
        self._readable_rooms = set(self._read_schedule)
        # Seed the name index from the runtime schedule. This makes reading
        # work even when the WeChat contacts page is unavailable on login.
        for room_id in self._read_schedule:
            if room_id.startswith(GROUP_ID_PREFIX):
                group_name = room_id[len(GROUP_ID_PREFIX) :]
                if not group_name:
                    continue
                self._groups.setdefault(
                    room_id,
                    GroupInfo(
                        external_id=room_id,
                        name=group_name,
                        meta={"source": "runtime-schedule"},
                    ),
                )
                self._raw_names[group_name] = group_name
        for room_id, interval in self._read_schedule.items():
            if room_id not in self._next_check:
                # Stagger initial checks so many groups do not run at once.
                self._next_check[room_id] = now + random.uniform(
                    0.0, min(2.0, max(0.2, interval * 0.25))
                )
            else:
                self._next_check[room_id] = min(
                    self._next_check[room_id], now + interval
                )
        for room_id in list(self._next_check):
            if room_id not in self._read_schedule:
                self._next_check.pop(room_id, None)
        for group_name in list(self._media_pending):
            if f"{GROUP_ID_PREFIX}{group_name}" not in self._read_schedule:
                self._media_pending.pop(group_name, None)

    def set_poll_interval(self, seconds: float) -> None:
        self._poll_interval = max(1.0, float(seconds or 5.0))

    def set_media_download_enabled(self, enabled: bool) -> None:
        self._media_download_enabled = bool(enabled)

    def set_pinned_group(self, room_id: str) -> None:
        room_id = str(room_id or "")
        if room_id == self._pinned_room:
            return
        self._pinned_room = room_id
        self._pinned_next_check = 0.0
        self._pinned_baseline_ready = False

    async def refresh(self) -> None:
        if self._wx is not None:
            groups = await self.list_groups()
            if groups:
                self.emit("groups", groups=groups)

    # ---------------------------------------------------------------- debug
    def debug_dump(
        self,
        action: str = "all",
        depth: int = 8,
        group_name: str = "",
        media_kind: str = "",
    ) -> list[str]:
        """Return a compact UIA tree for local troubleshooting."""
        return self._run_on_worker(
            lambda wx: self._debug_dump_sync(
                wx, action, depth, group_name, media_kind
            ),
            90.0,
        )

    def _debug_dump_sync(
        self,
        wx: Any,
        action: str,
        depth: int,
        group_name: str = "",
        media_kind: str = "",
    ) -> list[str]:
        switched = False
        try:
            if action in ("media", "media_tree"):
                if action == "media" and not self._media_download_enabled:
                    return ["media|disabled"]
                if not group_name:
                    return ["media|error|group name required"]
                if not self._open_group(wx, group_name):
                    return ["media|error|open failed"]
                messages = wx.GetAllMessage() or []
                if action == "media_tree":
                    lines: list[str] = []
                    target = None
                    for message in reversed(messages):
                        content = str(getattr(message, "content", "") or "")
                        kind, _ = _describe_content(content)
                        if kind in ("image", "sticker") and getattr(
                            message, "type", ""
                        ) in ("friend", "self"):
                            target = message
                            break
                    if target is None:
                        return ["media_tree|error|no media message"]
                    lines.append(
                        "media_tree|parent|"
                        f"{target.control.ControlTypeName}|"
                        f"{str(target.control.Name or '')[:80]}|"
                        f"{target.control.BoundingRectangle}"
                    )
                    for control, level in self._walk_controls_with_depth(
                        target.control, 6
                    ):
                        try:
                            rect = control.BoundingRectangle
                            lines.append(
                                f"media_tree|{level}|{control.ControlTypeName}|"
                                f"{str(control.Name or '')[:80]}|{control.ClassName}|"
                                f"{rect.left},{rect.top},{rect.right},{rect.bottom}|"
                                f"area={(rect.right-rect.left)*(rect.bottom-rect.top)}"
                            )
                        except Exception:
                            continue
                    return lines
                images = []
                stickers = []
                for message in reversed(messages):
                    try:
                        if getattr(message, "type", "") not in ("friend", "self"):
                            continue
                        content = str(getattr(message, "content", "") or "")
                        kind, _ = _describe_content(content)
                        if media_kind and kind != media_kind:
                            continue
                        if kind == "image":
                            images.append(message)
                        elif kind == "sticker":
                            stickers.append(message)
                    except Exception:
                        continue
                for message in images + stickers:
                    try:
                        content = str(getattr(message, "content", "") or "")
                        kind, _ = _describe_content(content)
                        path = self._download_media_message(wx, message)
                        sender = str(
                            getattr(message, "sender", "")
                            or getattr(message, "sender_remark", "")
                            or "self"
                        )
                        return [
                            f"media|{kind}|{sender}|{content}|{path or 'SAVE_FAILED'}"
                        ]
                    except Exception as exc:
                        return [f"media|error|{exc}"]
                return ["media|error|no media message"]
            if action == "timers":
                now = time.time()
                lines: list[str] = []
                for room_id, interval in sorted(self._read_schedule.items()):
                    next_at = self._next_check.get(room_id, 0.0)
                    lines.append(
                        f"timer|{room_id}|interval={interval:.1f}|"
                        f"next_in={max(0.0, next_at - now):.1f}"
                    )
                return lines or ["timer|none"]
            if action == "sessions":
                lines: list[str] = []
                for session in wx.GetSession() or []:
                    try:
                        name = _clean_group_title(getattr(session, "name", ""))
                        preview = str(getattr(session, "content", "") or "")
                        stamp = str(getattr(session, "time", "") or "")
                        isnew = bool(getattr(session, "isnew", False))
                        sender, content = _parse_session_preview(preview)
                        lines.append(
                            f"session|{name}|time={stamp}|new={isnew}|"
                            f"sender={sender}|content={content}|"
                            f"valid={_is_valid_group_name(name)}"
                        )
                    except Exception:
                        continue
                return lines
            if action in ("contacts", "groups"):
                wx.SwitchToContact()
                switched = True
                time.sleep(1.2)
                self._scroll_contacts_to_top(wx)
                entry = self._find_control_by_name(wx.SessionBox, "群聊")
                if entry is not None and action == "groups":
                    self._click_control(entry)
                    time.sleep(1.5)
            elif action in ("contact_manager", "recent_groups"):
                wx.SwitchToContact()
                switched = True
                time.sleep(1.2)
                self._scroll_contacts_to_top(wx)
                entry = self._find_control_by_name(wx.SessionBox, "通讯录管理")
                if entry is not None:
                    self._click_control(entry)
                    time.sleep(2.0)
                if action == "recent_groups":
                    try:
                        from wxauto import uiautomation as uia

                        manager = uia.WindowControl(
                            ClassName="ContactManagerWindow", searchDepth=1
                        )
                        button = manager.ButtonControl(
                            Name="最近群聊", searchDepth=10
                        )
                        if button.Exists(1.0):
                            button.Click(simulateMove=False)
                            time.sleep(1.5)
                    except Exception:
                        pass
            elif action == "chat":
                wx.SwitchToChat()
                time.sleep(0.5)
            roots: list[tuple[str, Any]] = [("window", wx.UiaAPI)]
            if action in ("all", "contacts", "groups", "session"):
                roots.append(("session", wx.SessionBox))
            if action in ("all", "contacts", "groups", "chat_pane"):
                roots.append(("chat_pane", wx.ChatBox))
            if action in ("contact_manager", "recent_groups"):
                try:
                    from wxauto import uiautomation as uia

                    manager = uia.WindowControl(
                        ClassName="ContactManagerWindow", searchDepth=1
                    )
                    if manager.Exists(1.0):
                        roots = [("manager", manager)]
                except Exception:
                    pass
            lines: list[str] = []
            for label, root in roots:
                for control, level in self._walk_controls_with_depth(root, depth):
                    try:
                        name = str(control.Name or "").strip().replace("\n", " ")
                        if not name and control.ControlTypeName not in (
                            "ListControl",
                            "ListItemControl",
                            "ButtonControl",
                            "EditControl",
                            "PaneControl",
                            "ToolBarControl",
                        ):
                            continue
                        rect = control.BoundingRectangle
                        lines.append(
                            f"{label}|{level}|{control.ControlTypeName}|"
                            f"{name[:120]}|{control.ClassName}|"
                            f"{rect.left},{rect.top},{rect.right},{rect.bottom}"
                        )
                    except Exception:
                        continue
            return lines
        finally:
            if action in ("contact_manager", "recent_groups"):
                try:
                    from wxauto import uiautomation as uia

                    manager = uia.WindowControl(
                        ClassName="ContactManagerWindow", searchDepth=1
                    )
                    if manager.Exists(0.2):
                        manager.SendKeys("{Esc}")
                        time.sleep(0.4)
                except Exception:
                    pass
            if switched:
                try:
                    wx.SwitchToChat()
                except Exception:
                    pass

    @staticmethod
    def _walk_controls_with_depth(
        root: Any, max_depth: int = 8
    ) -> list[tuple[Any, int]]:
        result: list[tuple[Any, int]] = []
        stack: list[tuple[Any, int]] = [(root, 0)]
        guard = 0
        while stack and guard < 4000:
            control, depth = stack.pop(0)
            guard += 1
            result.append((control, depth))
            if depth >= max_depth:
                continue
            try:
                children = control.GetChildren()
            except Exception:
                continue
            for child in children:
                stack.append((child, depth + 1))
        return result

    # -------------------------------------------------------------- wechat exe
    def _find_wechat_exe(self) -> Path | None:
        configured = str(self.options.get("wechat_exe", "") or "").strip()
        if configured:
            path = Path(configured)
            if path.exists():
                return path
        return self._find_compatible_wechat()

    @staticmethod
    def _wechat_running() -> bool:
        if sys.platform != "win32":
            return False
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
            # 32-bit WeChat 3.9 on 64-bit Windows otherwise reports
            # "微信版本过低" and refuses to log in.
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

    @staticmethod
    def _window_state() -> str:
        if sys.platform != "win32":
            return "none"
        try:
            import win32gui

            login = win32gui.FindWindow("WeChatLoginWndForPC", None)
            if login and win32gui.IsWindowVisible(login):
                return "login"
            main = win32gui.FindWindow("WeChatMainWndForPC", None)
            if main:
                return "main"
        except Exception:
            return "none"
        return "none"

    # ------------------------------------------------------------------- misc
    @staticmethod
    def _room_name(room_id: str) -> str:
        value = str(room_id or "").strip()
        if value.startswith(GROUP_ID_PREFIX):
            return value[len(GROUP_ID_PREFIX) :]
        return value

    @staticmethod
    def _walk_controls(root: Any, max_depth: int = 8) -> list[Any]:
        result: list[Any] = []
        stack: list[tuple[Any, int]] = [(root, 0)]
        guard = 0
        while stack and guard < 4000:
            control, depth = stack.pop()
            guard += 1
            result.append(control)
            if depth >= max_depth:
                continue
            try:
                children = control.GetChildren()
            except Exception:
                continue
            for child in reversed(children):
                stack.append((child, depth + 1))
        return result

    def _find_control_by_name(self, root: Any, name: str) -> Any | None:
        fallback = None
        for control in self._walk_controls(root, max_depth=7):
            try:
                if str(control.Name or "").strip() != name:
                    continue
                if control.ControlTypeName in ("ListItemControl", "ButtonControl"):
                    return control
                if fallback is None and control.ControlTypeName in (
                    "TextControl",
                    "PaneControl",
                ):
                    fallback = control
                    if control.ControlTypeName == "TextControl":
                        try:
                            parent = control.GetParentControl()
                            guard = 0
                            while parent is not None and guard < 5:
                                if parent.ControlTypeName in (
                                    "ListItemControl",
                                    "ButtonControl",
                                ):
                                    return parent
                                parent = parent.GetParentControl()
                                guard += 1
                        except Exception:
                            pass
            except Exception:
                continue
        return fallback

    @staticmethod
    def _click_control(control: Any) -> None:
        try:
            control.Click(simulateMove=False)
            return
        except Exception:
            pass
        rect = control.BoundingRectangle
        if rect and rect.width() > 0 and rect.height() > 0:
            try:
                import win32api
                import win32con

                x = (rect.left + rect.right) // 2
                y = (rect.top + rect.bottom) // 2
                try:
                    from wxauto import uiautomation as uia

                    uia.MoveTo(x, y, waitTime=0)
                except Exception:
                    win32api.SetCursorPos((x, y))
                time.sleep(random.uniform(0.04, 0.12))
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            except Exception:
                pass

    def _log_control_sample(self, root: Any, label: str) -> None:
        try:
            sample: list[str] = []
            for control in self._walk_controls(root, max_depth=4)[:80]:
                try:
                    name = str(control.Name or "").strip()
                    if not name:
                        continue
                    sample.append(f"{control.ControlTypeName}:{name[:30]}")
                except Exception:
                    continue
            if sample:
                self.log(f"{label} 控件：{sample[:40]}")
        except Exception:
            pass

    def _configure_wxauto_logging(self) -> None:
        if self._wxlog_ready:
            return
        self._wxlog_ready = True
        try:
            import logging

            logging.getLogger("wxauto").setLevel(logging.WARNING)
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

    # ------------------------------------------------------- user takeover
    def _start_mouse_watch(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._mouse_watch_loop,
            daemon=True,
            name="uia-mouse-watch",
        )
        self._watch_thread.start()

    def _stop_mouse_watch(self) -> None:
        self._watch_stop.set()
        thread = self._watch_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        self._watch_thread = None

    def _mouse_watch_loop(self) -> None:
        try:
            import win32api
        except Exception:
            return
        last_pos: tuple[int, int] | None = None
        while not self._watch_stop.is_set():
            try:
                pos = win32api.GetCursorPos()
                now = time.time()
                if self._synthetic_motion or now - self._last_auto_at < 0.2:
                    # Our own smooth movement (or the short settle window
                    # after a click) must not be mistaken for a user takeover.
                    last_pos = pos
                else:
                    if last_pos is not None:
                        dx = pos[0] - last_pos[0]
                        dy = pos[1] - last_pos[1]
                        if dx * dx + dy * dy >= 900:  # 30px：轻微抖动不算用户接管
                            self._user_mouse_at = now
                    last_pos = pos
            except Exception:
                pass
            self._watch_stop.wait(0.08)

    def allow_ui_action(self, seconds: float = 8.0) -> None:
        """Mark an action as explicitly requested by the dashboard user."""
        self._user_consent_until = max(
            self._user_consent_until, time.time() + max(1.0, seconds)
        )
        self._user_mouse_at = 0.0

    def before_mouse_action(self) -> None:
        if self._detached:
            raise AutomationDetached(
                self._detach_reason or "群伴已脱离控制，请重新接管"
            )
        now = time.time()
        if now < self._user_consent_until:
            return
        if now - self._user_mouse_at < 0.8:
            self._set_detached("检测到用户正在使用鼠标")
            raise AutomationDetached(self._detach_reason)
        try:
            import win32api

            pos = win32api.GetCursorPos()
            if (
                self._last_auto_pos is not None
                and now - self._last_auto_at < 2.0
            ):
                dx = pos[0] - self._last_auto_pos[0]
                dy = pos[1] - self._last_auto_pos[1]
                if dx * dx + dy * dy > 900:  # 30px
                    self._user_mouse_at = now
                    self._set_detached("检测到鼠标被用户移动")
                    raise AutomationDetached(self._detach_reason)
        except AutomationDetached:
            raise
        except Exception:
            pass

    def begin_synthetic_motion(self) -> None:
        self._synthetic_motion = True

    def end_synthetic_motion(self) -> None:
        self._synthetic_motion = False

    def note_auto_action(self) -> None:
        self._last_auto_at = time.time()
        try:
            import win32api

            self._last_auto_pos = win32api.GetCursorPos()
        except Exception:
            self._last_auto_pos = None

    def check_move_takeover(
        self, started_at: float, expected_x: int, expected_y: int
    ) -> None:
        if self._detached:
            raise AutomationDetached(
                self._detach_reason or "群伴已脱离控制，请重新接管"
            )
        if self._user_mouse_at >= started_at:
            self._set_detached("检测到用户接管鼠标")
            raise AutomationDetached(self._detach_reason)
        try:
            import win32api

            pos = win32api.GetCursorPos()
            if abs(pos[0] - expected_x) > 40 or abs(pos[1] - expected_y) > 40:
                self._set_detached("检测到鼠标被用户移动")
                raise AutomationDetached(self._detach_reason)
        except AutomationDetached:
            raise
        except Exception:
            pass

    def _set_detached(self, reason: str) -> None:
        if self._detached:
            return
        self._detached = True
        self._detach_reason = reason or "检测到用户接管鼠标"
        self._user_mouse_at = time.time()
        self.log(
            f"{self._detach_reason}，已暂停自动操作，等待手动确认",
            level="warn",
        )
        self.emit("detached", reason=self._detach_reason, at=time.time())

    def detach(self, reason: str = "用户接管鼠标") -> None:
        self._set_detached(reason)

    def reattach(self) -> None:
        was_detached = self._detached
        self._detached = False
        self._detach_reason = ""
        self._user_mouse_at = 0.0
        self._user_consent_until = time.time() + 0.8
        if was_detached:
            self.log("已重新接管鼠标与微信界面")

    @property
    def is_detached(self) -> bool:
        return self._detached

    def _ensure_not_detached(self) -> None:
        if self._detached:
            raise AutomationDetached(
                self._detach_reason or "群伴已脱离控制，请重新接管"
            )

    def _report_error(self, message: str) -> None:
        now = time.time()
        if message == self._last_error and now - self._last_error_at < 30:
            return
        self._last_error = message
        self._last_error_at = now
        self.emit("log", message=message, level="warn")
