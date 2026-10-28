from __future__ import annotations

import asyncio
import base64
import ctypes
import ctypes.wintypes
import hashlib
import importlib.util
import io
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from app.adapters.base import (
    AccountInfo,
    BaseAdapter,
    GroupInfo,
    IncomingMessage,
    MemberInfo,
)

GROUP_ID_PREFIX = "replica:"
SUPPORTED_VERSION = (4, 1, 12)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# The guard is process-wide because wechatauto's click helpers are module-level
# functions. Only one replica adapter runs at a time in this service.
_ACTIVE_GUARD: "ReplicaGuard | None" = None
_GUARD_INSTALLED = False


class AutomationDetached(RuntimeError):
    """Raised when the real user takes over mouse/keyboard input."""


def _version_tuple(value: str) -> tuple[int, int, int, int]:
    match = _VERSION_RE.search(value or "")
    if not match:
        return (0, 0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def _version_supported(value: str) -> bool:
    return _version_tuple(value)[:3] >= SUPPORTED_VERSION


def _file_version(path: Path) -> str:
    try:
        import win32api

        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms = int(info["FileVersionMS"])
        ls = int(info["FileVersionLS"])
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return ""


def _default_weixin_paths() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("REPLICA_WEIXIN_EXE")
    if env:
        candidates.append(Path(env))
    candidates.extend(
        [
            Path(r"D:\Weixin\Weixin.exe"),
            Path(r"C:\Program Files\Tencent\Weixin\Weixin.exe"),
            Path(r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe"),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Tencent"
            / "Weixin"
            / "Weixin.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Tencent"
            / "Weixin"
            / "Weixin.exe",
        ]
    )
    try:
        import psutil

        for process in psutil.process_iter(["name", "exe"]):
            try:
                if (process.info.get("name") or "").lower() == "weixin.exe":
                    exe = process.info.get("exe")
                    if exe:
                        candidates.insert(0, Path(exe))
            except Exception:
                continue
    except Exception:
        pass
    try:
        import winreg

        for root, key in (
            (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\Weixin"),
        ):
            try:
                with winreg.OpenKey(root, key) as handle:
                    install, _ = winreg.QueryValueEx(handle, "InstallPath")
                    candidates.append(Path(str(install)) / "Weixin.exe")
            except Exception:
                continue
    except Exception:
        pass
    result: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        try:
            normalized = str(item.resolve())
        except Exception:
            normalized = str(item)
        if normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        result.append(item)
    return result


def find_weixin_exe() -> Path | None:
    for candidate in _default_weixin_paths():
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def _weixin_pids() -> list[int]:
    pids: list[int] = []
    try:
        import psutil

        for process in psutil.process_iter(["name"]):
            try:
                if (process.info.get("name") or "").lower() == "weixin.exe":
                    pids.append(int(process.pid))
            except Exception:
                continue
    except Exception:
        pass
    return pids


def _visible_weixin_windows() -> list[tuple[int, int, str, str, tuple[int, int, int, int]]]:
    import win32gui
    import win32process

    pids = set(_weixin_pids())
    if not pids:
        return []
    rows: list[tuple[int, int, str, str, tuple[int, int, int, int]]] = []

    def callback(hwnd: int, _extra: Any) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if int(pid) not in pids:
                return
            rect = win32gui.GetWindowRect(hwnd)
            width = max(0, rect[2] - rect[0])
            height = max(0, rect[3] - rect[1])
            if width < 120 or height < 120:
                return
            rows.append(
                (
                    hwnd,
                    int(pid),
                    win32gui.GetWindowText(hwnd) or "",
                    win32gui.GetClassName(hwnd) or "",
                    rect,
                )
            )
        except Exception:
            return

    win32gui.EnumWindows(callback, None)
    return rows


def _capture_window_png(hwnd: int) -> bytes:
    """Capture a window through PrintWindow, with a screen-grab fallback."""
    import win32gui
    import win32ui
    from PIL import Image, ImageGrab

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = max(1, right - left)
    height = max(1, bottom - top)
    hwnd_dc = None
    mfc_dc = None
    save_dc = None
    bitmap = None
    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
        if result:
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
            output = io.BytesIO()
            image.save(output, format="PNG")
            if len(output.getvalue()) > 4096:
                return output.getvalue()
    except Exception:
        pass
    finally:
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        try:
            if save_dc is not None:
                save_dc.DeleteDC()
        except Exception:
            pass
        try:
            if mfc_dc is not None:
                mfc_dc.DeleteDC()
        except Exception:
            pass
        try:
            if hwnd_dc is not None:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass
    image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ReplicaGuard:
    """Stop UI automation when the real user takes over.

    Reading messages is database-only and never needs this guard. It only
    applies to sending / opening chats, where wechatauto drives the real
    WeChat window. If the user moves the cursor or touches the keyboard, the
    adapter emits ``detached`` and all further UI actions are refused until
    the dashboard user explicitly reattaches.
    """

    def __init__(self, adapter: "ReplicaAdapter") -> None:
        self.adapter = adapter
        self.detached = False
        self.reason = ""
        self._user_mouse_at = 0.0
        self._consent_until = 0.0
        self._synthetic_depth = 0
        self._expected: tuple[int, int] | None = None
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_loop, daemon=True, name="replica-mouse-watch"
        )
        self._watch_thread.start()

    def stop(self) -> None:
        self._watch_stop.set()
        thread = self._watch_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        self._watch_thread = None

    def _watch_loop(self) -> None:
        try:
            import win32api
        except Exception:
            return
        last_pos: tuple[int, int] | None = None
        while not self._watch_stop.is_set():
            try:
                pos = tuple(win32api.GetCursorPos())
                now = time.time()
                if self._synthetic_depth > 0 and self._expected is not None:
                    last_pos = pos
                else:
                    if last_pos is not None:
                        dx = pos[0] - last_pos[0]
                        dy = pos[1] - last_pos[1]
                        if dx * dx + dy * dy >= 25:
                            self._user_mouse_at = now
                    last_pos = pos
            except Exception:
                pass
            self._watch_stop.wait(0.08)

    def allow(self, seconds: float = 8.0) -> None:
        self._consent_until = max(
            self._consent_until, time.time() + max(1.0, float(seconds))
        )
        self._user_mouse_at = 0.0

    def before_action(self) -> None:
        if self.detached:
            raise AutomationDetached(self.reason or "群伴已脱离控制，请重新接管")
        now = time.time()
        if now < self._consent_until:
            return
        if now - self._user_mouse_at < 1.2:
            self._set_detached("检测到用户正在使用鼠标")
            raise AutomationDetached(self.reason)
        try:
            last_input = self._last_input_age()
            if last_input is not None and last_input < 1.2:
                self._set_detached("检测到用户正在使用键盘或鼠标")
                raise AutomationDetached(self.reason)
        except AutomationDetached:
            raise
        except Exception:
            pass

    @staticmethod
    def _last_input_age() -> float | None:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (int(tick) - int(info.dwTime)) / 1000.0)

    def begin_synthetic(self, expected: tuple[int, int] | None = None) -> None:
        self._synthetic_depth += 1
        if expected is not None:
            self._expected = expected

    def end_synthetic(self) -> None:
        self._synthetic_depth = max(0, self._synthetic_depth - 1)
        if self._synthetic_depth == 0:
            self._expected = None

    def note_action(self) -> None:
        try:
            import win32api

            self._expected = tuple(win32api.GetCursorPos())
        except Exception:
            self._expected = None

    def check_expected(self, expected: tuple[int, int] | None = None) -> None:
        if self.detached:
            raise AutomationDetached(self.reason or "群伴已脱离控制，请重新接管")
        expected = expected or self._expected
        if expected is None:
            return
        try:
            import win32api

            pos = win32api.GetCursorPos()
            if abs(pos[0] - expected[0]) > 12 or abs(pos[1] - expected[1]) > 12:
                self._set_detached("检测到鼠标被用户移动")
                raise AutomationDetached(self.reason)
        except AutomationDetached:
            raise
        except Exception:
            pass

    def move_cursor(
        self,
        user32: Any,
        x: int,
        y: int,
        start: tuple[int, int] | None = None,
    ) -> int:
        self.before_action()
        if start is None:
            try:
                point = ctypes.wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(point))
                start = (int(point.x), int(point.y))
            except Exception:
                start = (int(x), int(y))
        sx, sy = int(start[0]), int(start[1])
        dx, dy = int(x) - sx, int(y) - sy
        distance = max(abs(dx), abs(dy))
        if distance < 8:
            user32.SetCursorPos(int(x), int(y))
            self.note_action()
            return 1
        steps = max(10, min(60, int(distance / 18) + random.randint(6, 14)))
        mid_x = (sx + x) / 2.0 + random.uniform(-0.06, 0.06) * abs(dy)
        mid_y = (sy + y) / 2.0 + random.uniform(-0.06, 0.06) * abs(dx)
        expected = (sx, sy)
        self.begin_synthetic(expected)
        try:
            for index in range(1, steps + 1):
                self.check_expected(expected)
                t = index / steps
                ease = t * t * (3.0 - 2.0 * t)
                px = int(
                    (1 - ease) * (1 - ease) * sx
                    + 2 * (1 - ease) * ease * mid_x
                    + ease * ease * x
                )
                py = int(
                    (1 - ease) * (1 - ease) * sy
                    + 2 * (1 - ease) * ease * mid_y
                    + ease * ease * y
                )
                user32.SetCursorPos(px, py)
                self._expected = (px, py)
                expected = (px, py)
                time.sleep(random.uniform(0.005, 0.016))
            self.check_expected(expected)
            user32.SetCursorPos(int(x), int(y))
            self._expected = (int(x), int(y))
            return steps + 1
        finally:
            self.end_synthetic()
            self.note_action()

    def _set_detached(self, reason: str) -> None:
        if self.detached:
            return
        self.detached = True
        self.reason = reason or "检测到用户接管鼠标"
        self._user_mouse_at = time.time()
        self.adapter._set_detached(self.reason)

    def detach(self, reason: str) -> None:
        self._set_detached(reason)

    def reattach(self) -> None:
        self.detached = False
        self.reason = ""
        self._user_mouse_at = 0.0
        self._expected = None
        self._consent_until = time.time() + 0.8


def _install_guard(guard: ReplicaGuard) -> None:
    """Patch wechatauto's mouse helpers once per process."""
    global _ACTIVE_GUARD, _GUARD_INSTALLED
    _ACTIVE_GUARD = guard
    if _GUARD_INSTALLED:
        return
    try:
        import wechatauto.guia as guia
        import wechatauto.rhythm as rhythm
    except Exception:
        return

    original_move_to = rhythm.move_to
    original_real_click = guia.WinInput.real_click
    original_send_input_click = guia.WinInput.send_input_click
    original_bring_to_front = guia.WeChatGUI.bring_to_front

    def guarded_move_to(user32: Any, x: int, y: int, start: Any = None) -> int:
        active = _ACTIVE_GUARD
        if active is None:
            return original_move_to(user32, x, y, start)
        return active.move_cursor(user32, int(x), int(y), start)

    def guarded_real_click(self: Any, x: int, y: int, right: bool = False):
        active = _ACTIVE_GUARD
        if active is None:
            return original_real_click(self, x, y, right)
        active.before_action()
        return original_real_click(self, x, y, right)

    def guarded_send_input_click(self: Any, x: int, y: int, right: bool = False):
        active = _ACTIVE_GUARD
        if active is None:
            return original_send_input_click(self, x, y, right)
        active.before_action()
        return original_send_input_click(self, x, y, right)

    def guarded_bring_to_front(self: Any, keep_topmost: bool = False) -> bool:
        active = _ACTIVE_GUARD
        if active is not None:
            active.before_action()
        # Never let the automation keep WeChat above every other app.
        return original_bring_to_front(self, keep_topmost=False)

    def ensure_visible(self: Any, keep_topmost: bool = True) -> bool:
        active = _ACTIVE_GUARD
        if active is not None:
            active.before_action()
        # Do not minimize the user's other windows. We only need WeChat in
        # front for the duration of a send, and release it immediately after.
        original_bring_to_front(self, keep_topmost=False)
        try:
            self._update_render_rect()
        except Exception:
            pass
        try:
            self._last_visible_ok = True
            self._last_visible_ts = time.time()
        except Exception:
            pass
        return bool(self.is_alive())

    rhythm.move_to = guarded_move_to
    guia.WinInput.real_click = guarded_real_click
    guia.WinInput.send_input_click = guarded_send_input_click
    guia.WeChatGUI.bring_to_front = guarded_bring_to_front
    guia.WeChatGUI.ensure_visible = ensure_visible
    _GUARD_INSTALLED = True


_EXPIRED_ROOM_RE = re.compile(r"^\d+@chatroom$", re.I)


def _is_usable_group(username: str, name: str) -> bool:
    """Filter expired temporary chatrooms that only expose their raw id.

    WeChat keeps old numeric ``<id>@chatroom`` rows in contact.db long after a
    temporary group has expired. They have no display name and no messages;
    showing them pollutes the group list and encourages the old UI search
    mechanism we are retiring.
    """
    username = str(username or "").strip()
    name = str(name or "").strip()
    if not username.endswith("@chatroom"):
        return False
    if not name or name == username:
        return False
    # Any group whose display name still exposes the raw chatroom id is a
    # temporary chat. Do not list, read, persist or reply to it.
    if "@chatroom" in name.lower():
        return False
    if _EXPIRED_ROOM_RE.match(name):
        return False
    if name.lower() == username.lower():
        return False
    return True


def _msg_kind(mtype: str) -> str:
    if mtype == "文本":
        return "text"
    if mtype == "图片":
        return "image"
    if mtype == "动画表情":
        return "sticker"
    if mtype == "语音":
        return "voice"
    if mtype == "视频":
        return "video"
    if mtype == "系统消息":
        return "system"
    if mtype in ("文件/链接/卡片", "位置", "红包", "音视频通话"):
        return "app"
    return "text"


def _clean_group_content(content: str, sender_wxid: str) -> str:
    text = str(content or "")
    if not text:
        return ""
    head, sep, tail = text.partition(":\n")
    if sep and len(head) <= 96:
        candidate = head.strip()
        if candidate and (
            candidate == sender_wxid
            or candidate.startswith("wxid_")
            or candidate.endswith("@chatroom")
            or re.fullmatch(r"[A-Za-z0-9_@.\-]{4,96}", candidate)
        ):
            return tail
    return text


def _xml_title(content: str) -> str:
    match = re.search(r"<title>(.*?)</title>", content or "", re.S)
    if match:
        return re.sub(r"<[^>]+>", "", match.group(1)).strip()
    return ""


def _safe_json(value: Any, depth: int = 0) -> Any:
    """Convert WxResponse / feed dicts into JSON-safe structures."""
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _safe_json(item, depth + 1)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(item, depth + 1) for item in value]
    if hasattr(value, "BoundingRectangle") or hasattr(value, "ControlTypeName"):
        return str(value)
    try:
        return str(value)
    except Exception:
        return None


class ReplicaAdapter(BaseAdapter):
    """WeChat 4.x adapter backed by wechatauto-replica.

    Message reading uses the decrypted local SQLCipher databases, so it works
    while WeChat is covered/minimized and does not move the cursor. Sending
    uses wechatauto-replica's UIA/OCR driver and is guarded so a real user
    taking over the mouse pauses automation instead of being overridden.
    """

    adapter_id = "replica"
    display_name = "微信 PC 4.x（wechatauto-replica）"
    description = (
        "读取微信 4.x 本地数据库，支持后台实时收消息、群成员、媒体下载；"
        "发送走 UIA/OCR 混合驱动。"
    )

    def __init__(self, emit, options: dict[str, Any] | None = None) -> None:
        super().__init__(emit, options)
        self._worker: threading.Thread | None = None
        self._scheduler: threading.Thread | None = None
        self._ui_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="replica-ui"
        )
        self._lock = threading.RLock()
        self._db: Any = None
        self._wx: Any = None
        self._listener: Any = None
        self._listener_users: set[str] = set()
        self._listener_dm_users: set[str] = set()
        self._listener_lock = threading.Lock()
        self._admin_contacts: set[str] = set()
        self._media: Any = None
        self._self_wxid = ""
        self._self_name = ""
        self._account: AccountInfo | None = None
        self._groups: dict[str, GroupInfo] = {}
        self._resolved_group_names: dict[str, str] = {}
        self._readable_rooms: set[str] = set()
        self._read_schedule: dict[str, float] = {}
        self._next_check: dict[str, float] = {}
        self._poll_interval = 1.0
        self._pinned_room = ""
        self._history_done: set[str] = set()
        self._pending: dict[str, list[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._media_download_enabled = False
        self._image_key_checked = False
        self._media_lock = threading.Lock()
        self._last_qr_hash = ""
        self._last_qr_at = 0.0
        self._last_group_sync = 0.0
        self._last_error = ""
        self._last_error_at = 0.0
        self._detached = False
        self._detach_reason = ""
        self._consent_until = 0.0
        self._guard = ReplicaGuard(self)
        self._stop_scheduler = threading.Event()
        self._online_once = False
        self._download_dir = Path("data") / "media" / "replica"

    # ---------------------------------------------------------------- metadata
    @classmethod
    def availability(cls) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "仅支持 Windows"
        if platform.architecture()[0] != "64bit":
            return False, "wechatauto-replica 需要 64 位 Python（当前为 32 位）"
        if importlib.util.find_spec("wechatauto") is None:
            return (
                False,
                "缺少 wechatauto-replica，请运行 pip install -r requirements-replica.txt",
            )
        exe = find_weixin_exe()
        if exe is None:
            return (
                False,
                "未找到微信 4.x（Weixin.exe）。请安装微信 4.1.12+，默认路径 D:\\Weixin",
            )
        version = _file_version(exe)
        if version and not _version_supported(version):
            return (
                False,
                f"当前微信版本为 {version}，wechatauto-replica 需要 4.1.12+",
            )
        return True, ""

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._stop.clear()
        self._stop_scheduler.clear()
        if getattr(self._ui_executor, "_shutdown", False):
            self._ui_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="replica-ui"
            )
        self._guard.start()
        _install_guard(self._guard)
        self.emit(
            "status",
            state="starting",
            message="正在启动微信 4.x 与本地数据库读取器…",
        )
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name="wechat-replica-worker"
        )
        self._worker.start()
        self._scheduler = threading.Thread(
            target=self._scheduler_loop, daemon=True, name="replica-scheduler"
        )
        self._scheduler.start()

    async def stop(self) -> None:
        self._stop.set()
        self._stop_scheduler.set()
        listener = self._listener
        self._listener = None
        with self._listener_lock:
            self._listener_users.clear()
            self._listener_dm_users.clear()
        if listener is not None:
            try:
                await asyncio.to_thread(listener.stop)
            except Exception:
                pass
        worker = self._worker
        if worker and worker.is_alive():
            await asyncio.to_thread(worker.join, 6.0)
        self._worker = None
        scheduler = self._scheduler
        if scheduler and scheduler.is_alive():
            await asyncio.to_thread(scheduler.join, 2.0)
        self._scheduler = None
        self._guard.stop()
        try:
            self._ui_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        with self._lock:
            self._wx = None
            self._db = None
            self._media = None
        self.emit("status", state="offline", message="微信 4.x 适配器已停止")

    # ------------------------------------------------------------------ login
    def _worker_loop(self) -> None:
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            while not self._stop.is_set():
                if self._db is None:
                    if not self._connect():
                        self._stop.wait(2.0)
                        continue
                self._monitor()
        finally:
            try:
                ctypes.windll.ole32.CoUninitialize()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _weixin_exe(self) -> Path | None:
        configured = str(self.options.get("replica_wechat_exe") or "").strip()
        if configured:
            candidate = Path(configured)
            if candidate.is_file():
                return candidate
        return find_weixin_exe()

    def _ensure_weixin_running(self) -> bool:
        exe = self._weixin_exe()
        if exe is None:
            self.emit(
                "status",
                state="error",
                message="未找到微信 4.x（Weixin.exe），请先安装 4.1.12+",
            )
            return False
        if _weixin_pids():
            return True
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
            self.log(f"已启动微信 4.x：{exe}")
        except Exception as exc:
            self._report_error(f"启动微信 4.x 失败：{exc}")
            return False
        deadline = time.time() + 20
        while time.time() < deadline and not self._stop.is_set():
            if _weixin_pids():
                return True
            time.sleep(0.5)
        return False

    def _connect(self) -> bool:
        from wechatauto import WeChatDB

        if not self._ensure_weixin_running():
            return False
        # The library prints a full key diagnosis on every failed attempt.
        # We retry once per couple of seconds while waiting for a scan, so
        # keep just our own concise status message.
        try:
            WeChatDB._auto_diagnose_key_failure = lambda *_args, **_kwargs: None
        except Exception:
            pass
        try:
            db = WeChatDB(
                db_dir=self.options.get("replica_db_dir") or None,
                account=self.options.get("replica_account") or None,
            )
            info = db.get_self_info()
            wxid = str(info.get("username") or db.wxid or "").strip()
            if not wxid:
                raise RuntimeError("未读取到当前微信账号")
        except Exception as exc:
            self._show_login_qr()
            self.emit(
                "status",
                state="qr",
                message="请用手机微信扫码登录 4.x 客户端…",
            )
            self._report_error(f"读取微信数据库失败：{exc}", quiet=True)
            return False
        self._bootstrap(db, info)
        return True

    def _bootstrap(self, db: Any, info: dict[str, Any]) -> None:
        from wechatauto import MediaDownloader
        from wechatauto.db import Listener

        self._db = db
        self._media = MediaDownloader(db, save_dir=str(self._download_dir))
        self._self_wxid = str(info.get("username") or db.wxid or "").strip()
        self._self_name = str(
            info.get("nick_name") or info.get("remark") or self._self_wxid
        ).strip()
        self._account = AccountInfo(
            wxid=f"{GROUP_ID_PREFIX}{self._self_wxid}",
            name=self._self_name or self._self_wxid,
        )
        self.emit(
            "account",
            wxid=self._account.wxid,
            name=self._account.name,
            avatar="",
        )
        self._refresh_groups(force=True)
        self._last_qr_hash = ""
        self.emit(
            "status",
            state="online",
            message="微信 4.x 已连接，正在后台读取数据库消息",
        )
        try:
            watermark = str(Path("data") / f"replica_watermark_{self._safe_name()}.json")
            listener = Listener(
                db,
                interval=1.0,
                watermark_file=watermark,
            )
            listener.add_all(self._on_db_message, discover=True)
            listener.start()
            self._listener = listener
            self.log("已启动 4.x 本地数据库实时监听（无需保持微信前台）")
        except Exception as exc:
            self._report_error(f"启动消息监听失败：{exc}")
        self._online_once = True

    def _safe_name(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", self._self_wxid or "account")

    def _show_login_qr(self) -> None:
        now = time.time()
        if now - self._last_qr_at < 1.2:
            return
        self._last_qr_at = now
        windows = _visible_weixin_windows()
        if not windows:
            return
        # Login windows are the only visible top-level window before login.
        hwnd = max(windows, key=lambda item: (item[4][2] - item[4][0]) * (item[4][3] - item[4][1]))[0]
        try:
            data = _capture_window_png(hwnd)
        except Exception as exc:
            self._report_error(f"截取微信登录二维码失败：{exc}", quiet=True)
            return
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

    def _monitor(self) -> None:
        if self._db is None:
            return
        try:
            self._refresh_groups()
            info = self._db.get_self_info()
            current = str(info.get("username") or "").strip()
            if current and self._self_wxid and current != self._self_wxid:
                self.log("检测到微信账号切换，正在重新连接", level="warn")
                self._reset_connection()
                return
        except Exception as exc:
            self._report_error(f"微信数据库读取异常：{exc}", quiet=True)
            if not _weixin_pids():
                self._reset_connection()
                return
        self._stop.wait(1.0)

    def _reset_connection(self) -> None:
        listener = self._listener
        self._listener = None
        with self._listener_lock:
            self._listener_users.clear()
            self._listener_dm_users.clear()
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        with self._lock:
            self._db = None
            self._wx = None
            self._media = None
        self.emit("status", state="qr", message="微信 4.x 连接已断开，请重新登录")

    # ----------------------------------------------------------------- groups
    def _refresh_groups(self, force: bool = False) -> None:
        if self._db is None:
            return
        now = time.time()
        if not force and now - self._last_group_sync < 30:
            return
        self._last_group_sync = now
        try:
            rows = self._db.get_groups() or []
        except Exception as exc:
            self._report_error(f"读取群列表失败：{exc}", quiet=True)
            return
        groups: list[GroupInfo] = []
        for row in rows:
            username = str(row.get("username") or "").strip()
            name = str(row.get("name") or username).strip()
            if not _is_usable_group(username, name):
                continue
            groups.append(
                GroupInfo(
                    external_id=f"{GROUP_ID_PREFIX}{username}",
                    name=name,
                    member_count=int(row.get("member_count") or 0),
                    meta={
                        "source": "replica",
                        "username": username,
                        "owner": row.get("owner") or "",
                    },
                )
            )
        if not groups:
            return
        changed = {item.external_id for item in groups} != set(self._groups)
        self._groups = {item.external_id: item for item in groups}
        if changed or force:
            self.emit("groups", groups=groups)
            self.log(f"已从微信 4.x 数据库读取 {len(groups)} 个群聊")

    async def list_contacts(self) -> list[dict[str, Any]]:
        if self._db is None:
            return []

        def load() -> list[dict[str, Any]]:
            conn = self._db._contact_conn()
            if conn is None:
                return []
            try:
                rows = conn.execute(
                    "SELECT username, nick_name, remark, alias FROM contact "
                    "WHERE delete_flag=0 AND local_type=1 "
                    "AND username NOT LIKE '%@chatroom' "
                    "AND username NOT LIKE 'gh_%' "
                    "AND username != ? "
                    "ORDER BY COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username)",
                    (self._self_wxid,),
                ).fetchall()
            finally:
                conn.close()
            blocked = {
                "notifymessage",
                "weixin",
                "medianote",
                "floatbottle",
                "qmessage",
                "filehelper",
                "fmessage",
                "qqmail",
                "tmessage",
                "weibo",
                "brandsessionholder",
                "brandservicesessionholder",
            }
            result = []
            for row in rows:
                wxid = str(row["username"] or "").strip()
                if not wxid or wxid in blocked:
                    continue
                nickname = str(row["nick_name"] or "").strip()
                remark = str(row["remark"] or "").strip()
                result.append(
                    {
                        "wxid": wxid,
                        "name": remark or nickname or wxid,
                        "nickname": nickname,
                        "remark": remark,
                        "alias": str(row["alias"] or "").strip(),
                    }
                )
            return result

        return await asyncio.to_thread(load)

    def set_admin_contacts(self, wxids: set[str]) -> None:
        self._admin_contacts = {str(item) for item in (wxids or set()) if item}
        threading.Thread(
            target=self._sync_listeners,
            daemon=True,
            name="replica-dm-listener-sync",
        ).start()

    async def get_contact_messages(
        self, wxid: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        raw = str(wxid or "").strip()
        rows = await asyncio.to_thread(self._db.get_messages, raw, max(1, int(limit)))
        payloads = []
        for row in reversed(rows or []):
            item = dict(row)
            item["username"] = raw
            payload = self._row_to_direct_payload(item, raw, historical=True)
            if payload:
                payloads.append(payload)
        return payloads

    def _row_to_direct_payload(
        self, row: dict[str, Any], contact_wxid: str, historical: bool
    ) -> dict[str, Any] | None:
        local_id = row.get("local_id")
        if not contact_wxid or local_id is None:
            return None
        mtype = str(row.get("type") or "")
        content_raw = row.get("content")
        if isinstance(content_raw, bytes):
            content_raw = content_raw.decode("utf-8", errors="ignore")
        content_raw = str(content_raw or "")
        sender_wxid = str(row.get("sender_username") or "").strip()
        if not sender_wxid:
            sender_wxid = self._self_wxid if str(row.get("sender_id")) == str(self._self_wxid) else contact_wxid
        is_self = bool(self._self_wxid and sender_wxid == self._self_wxid)
        kind = _msg_kind(mtype)
        if kind == "text":
            content = content_raw.strip()
        elif kind == "image":
            content = "[图片]"
        elif kind == "sticker":
            content = "表情包"
        elif kind == "voice":
            content = "[语音]"
        elif kind == "video":
            content = "[视频]"
        elif kind == "system":
            match = re.search(r"<content>(.*?)</content>", content_raw, re.S)
            content = (
                re.sub(r"<[^>]+>", "", match.group(1)).strip()
                if match
                else (content_raw.strip() or "系统消息")
            )
        else:
            title = _xml_title(content_raw)
            content = title or content_raw.strip() or f"[{mtype or '消息'}]"
        if len(content) > 4000:
            content = content[:4000]
        sender_name = (
            self._self_name or self._display_name(sender_wxid)
            if is_self
            else self._display_name(sender_wxid)
        )
        media_path = ""
        if kind == "image":
            media_path = self._download_media(contact_wxid, row)
        elif self._media_download_enabled and kind in ("voice", "video", "file"):
            media_path = self._download_media(contact_wxid, row)
        return {
            "contact_wxid": contact_wxid,
            "sender_wxid": sender_wxid,
            "sender_name": sender_name,
            "content": content,
            "kind": kind,
            "timestamp": float(row.get("create_time") or time.time()),
            "external_id": f"replica:dm:{contact_wxid}:{row.get('shard') or 'shard'}:{local_id}",
            "is_self": is_self,
            "media_path": media_path,
            "meta": {
                "source": "replica-dm",
                "historical": bool(historical),
                "local_id": local_id,
                "shard": row.get("shard") or "",
                "msg_type": mtype,
            },
        }

    def _on_dm_message(self, row: dict[str, Any], _listener: Any) -> None:
        raw = str(row.get("username") or "").strip()
        if not raw or raw.endswith("@chatroom"):
            return
        payload = self._row_to_direct_payload(row, raw, historical=False)
        if payload is not None:
            self.emit("direct_message", message=payload)

    # --------------------------------------------------------------- listener
    def _on_db_message(self, row: dict[str, Any], _listener: Any) -> None:
        raw = str(row.get("username") or "").strip()
        if not raw.endswith("@chatroom"):
            return
        room_id = f"{GROUP_ID_PREFIX}{raw}"
        if room_id not in self._readable_rooms:
            return
        payload = self._row_to_payload(row, historical=False)
        if payload is None:
            return
        if room_id == self._pinned_room:
            self.emit("message", message=payload)
            return
        with self._pending_lock:
            self._pending.setdefault(room_id, []).append(payload)

    def _row_to_payload(
        self, row: dict[str, Any], historical: bool
    ) -> dict[str, Any] | None:
        raw = str(row.get("username") or "").strip()
        local_id = row.get("local_id")
        if not raw or local_id is None:
            return None
        mtype = str(row.get("type") or "")
        content_raw = row.get("content")
        if isinstance(content_raw, bytes):
            content_raw = content_raw.decode("utf-8", errors="ignore")
        content_raw = str(content_raw or "")
        sender_wxid = str(row.get("sender_username") or "").strip()
        if not sender_wxid:
            head = content_raw.partition(":\n")[0].strip()
            if head.startswith("wxid_") or head.endswith("@chatroom"):
                sender_wxid = head
        if not sender_wxid:
            sender_wxid = f"unknown-{row.get('sender_id') or '0'}"
        is_self = bool(self._self_wxid and sender_wxid == self._self_wxid)
        kind = _msg_kind(mtype)
        if kind == "text":
            content = _clean_group_content(content_raw, sender_wxid).strip()
        elif kind == "image":
            content = "[图片]"
        elif kind == "sticker":
            content = "表情包"
        elif kind == "voice":
            content = "[语音]"
        elif kind == "video":
            content = "[视频]"
        elif kind == "file":
            title = _xml_title(content_raw)
            content = title or "[文件]"
        elif kind == "app":
            title = _xml_title(content_raw)
            content = title or content_raw.strip() or f"[{mtype or '消息'}]"
        elif kind == "system":
            match = re.search(r"<content>(.*?)</content>", content_raw, re.S)
            if match:
                content = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            else:
                content = content_raw.strip() or "[系统消息]"
            if content.startswith("<?xml"):
                content = "系统消息"
        else:
            content = content_raw.strip() or f"[{mtype or '消息'}]"
        if len(content) > 4000:
            content = content[:4000]
        sender_name = self._display_name(sender_wxid)
        if is_self:
            sender_name = self._self_name or sender_name
        external_id = (
            f"{GROUP_ID_PREFIX}{raw}:{row.get('shard') or 'shard'}:{local_id}"
        )
        media_path = ""
        # Images are always decrypted so the dashboard can render them inline.
        # Stickers are intentionally not downloaded at all: they are shown as
        # a plain "表情包" hint, per the product requirement.
        if kind == "image":
            media_path = self._download_media(raw, row)
        elif self._media_download_enabled and kind in ("voice", "video", "file"):
            media_path = self._download_media(raw, row)
        return {
            "room_id": f"{GROUP_ID_PREFIX}{raw}",
            "sender_id": sender_wxid,
            "sender_name": sender_name,
            "content": content,
            "kind": kind,
            "timestamp": float(row.get("create_time") or time.time()),
            "external_id": external_id,
            "is_self": is_self,
            "is_group": True,
            "media_path": media_path,
            "meta": {
                "source": "replica",
                "historical": bool(historical),
                "group_name": self._group_name(raw),
                "username": raw,
                "local_id": local_id,
                "shard": row.get("shard") or "",
                "msg_type": mtype,
            },
        }

    def _display_name(self, wxid: str) -> str:
        if not wxid:
            return "未知成员"
        if wxid == self._self_wxid:
            return self._self_name or wxid
        try:
            mapping = self._db.nickname_map() if self._db is not None else {}
            name = mapping.get(wxid) or ""
            if name:
                return str(name)
        except Exception:
            pass
        try:
            name = self._db.get_nickname(wxid) if self._db is not None else ""
            if name and name != wxid:
                return str(name)
        except Exception:
            pass
        return wxid

    def _group_name(self, username: str) -> str:
        info = self._groups.get(f"{GROUP_ID_PREFIX}{username}")
        if info is not None and info.name:
            return info.name
        cached = self._resolved_group_names.get(username)
        if cached:
            return cached
        if self._db is not None:
            try:
                name = str(self._db.group_id_to_name(username) or "").strip()
            except Exception:
                name = ""
            if name and name != username:
                self._resolved_group_names[username] = name
                return name
        return username

    def _download_media(self, raw: str, row: dict[str, Any]) -> str:
        media = self._media
        if media is None:
            return ""
        local_id = int(row.get("local_id"))
        with self._media_lock:
            try:
                self._download_dir.mkdir(parents=True, exist_ok=True)
                path = media.download_media(
                    raw, local_id, save_dir=str(self._download_dir)
                )
                if not path and str(row.get("type") or "") == "图片":
                    # The image AES key is transient. Try one memory scan per
                    # process, then retry the same message.
                    if not self._image_key_checked:
                        self._image_key_checked = True
                        try:
                            keys = media.detect_image_key()
                        except Exception:
                            keys = None
                        if keys:
                            aes_key, xor_key = keys
                            path = media.download_image(
                                raw,
                                local_id,
                                save_dir=str(self._download_dir),
                                aes_key=aes_key,
                                xor_key=xor_key,
                            )
                if not path:
                    return ""
                absolute = Path(path).resolve()
                root = (Path("data") / "media").resolve()
                try:
                    return absolute.relative_to(root).as_posix()
                except ValueError:
                    return ""
            except Exception as exc:
                self._report_error(f"下载媒体失败：{exc}", quiet=True)
                return ""

    # --------------------------------------------------------------- schedule
    def set_readable_groups(self, room_ids: set[str]) -> None:
        self._readable_rooms = {str(item) for item in room_ids if item}
        with self._pending_lock:
            for room_id in list(self._pending):
                if room_id not in self._readable_rooms:
                    self._pending.pop(room_id, None)
        threading.Thread(
            target=self._sync_listeners,
            daemon=True,
            name="replica-listener-sync",
        ).start()
        now = time.time()
        for room_id in self._readable_rooms:
            self._next_check.setdefault(room_id, now)
            if room_id not in self._history_done:
                self._history_done.add(room_id)
                threading.Thread(
                    target=self._sync_history,
                    args=(room_id,),
                    daemon=True,
                    name="replica-history",
                ).start()

    def _sync_listeners(self) -> None:
        listener = self._listener
        if listener is None:
            return
        desired = {
            self._room_raw(room_id)
            for room_id in self._readable_rooms
            if room_id
        }
        desired.discard("")
        desired_dms = {str(item) for item in self._admin_contacts if item}
        with self._listener_lock:
            for raw in sorted(desired - self._listener_users):
                try:
                    listener.add_listener(raw, self._on_db_message)
                    self._listener_users.add(raw)
                    self.log(f"开始实时监听群：{self._group_name(raw)}")
                except Exception as exc:
                    self._report_error(f"添加群监听失败：{exc}", quiet=True)
            for raw in sorted(self._listener_users - desired):
                try:
                    listener.remove_listener(raw, self._on_db_message)
                except Exception:
                    pass
                self._listener_users.discard(raw)
            for raw in sorted(desired_dms - self._listener_dm_users):
                try:
                    listener.add_listener(raw, self._on_dm_message)
                    self._listener_dm_users.add(raw)
                    self.log(f"开始实时监听管理员私聊：{self._display_name(raw)}")
                except Exception as exc:
                    self._report_error(f"添加管理员私聊监听失败：{exc}", quiet=True)
            for raw in sorted(self._listener_dm_users - desired_dms):
                try:
                    listener.remove_listener(raw, self._on_dm_message)
                except Exception:
                    pass
                self._listener_dm_users.discard(raw)

    def set_read_schedule(self, schedule: dict[str, float]) -> None:
        self._read_schedule = {
            str(room_id): max(1.0, float(interval or self._poll_interval))
            for room_id, interval in (schedule or {}).items()
            if room_id
        }
        removed = set(self._readable_rooms) - set(self._read_schedule)
        self._readable_rooms = set(self._read_schedule)
        for room_id in removed:
            self._history_done.discard(room_id)
            self._next_check.pop(room_id, None)
        now = time.time()
        for room_id in self._readable_rooms:
            self._next_check.setdefault(room_id, now)
            if room_id not in self._history_done:
                self._history_done.add(room_id)
                threading.Thread(
                    target=self._sync_history,
                    args=(room_id,),
                    daemon=True,
                    name="replica-history",
                ).start()
        with self._pending_lock:
            for room_id in list(self._pending):
                if room_id not in self._readable_rooms:
                    self._pending.pop(room_id, None)
        threading.Thread(
            target=self._sync_listeners,
            daemon=True,
            name="replica-listener-sync",
        ).start()

    def _schedule_interval(self, room_id: str) -> float:
        if room_id == self._pinned_room:
            return 0.5
        return max(
            1.0,
            float(self._read_schedule.get(room_id) or self._poll_interval),
        )

    def _sync_history(self, room_id: str) -> None:
        if self._db is None:
            return
        raw = (
            room_id[len(GROUP_ID_PREFIX) :]
            if room_id.startswith(GROUP_ID_PREFIX)
            else room_id
        )
        try:
            rows = self._db.get_messages(raw, limit=30) or []
        except Exception as exc:
            self._report_error(f"同步历史消息失败：{exc}", quiet=True)
            return
        for row in reversed(rows):
            row = dict(row)
            row["username"] = raw
            payload = self._row_to_payload(row, historical=True)
            if payload is not None:
                self.emit("message", message=payload)
        self.log(f"已同步「{self._group_name(raw)}」最近 {len(rows)} 条历史消息")

    def _scheduler_loop(self) -> None:
        while not self._stop_scheduler.is_set():
            now = time.time()
            due: list[tuple[str, list[dict[str, Any]]]] = []
            with self._pending_lock:
                for room_id, items in list(self._pending.items()):
                    if room_id not in self._readable_rooms:
                        self._pending.pop(room_id, None)
                        continue
                    if not items:
                        self._pending.pop(room_id, None)
                        continue
                    if now < self._next_check.get(room_id, 0):
                        continue
                    due.append((room_id, items))
                    self._pending[room_id] = []
                    self._next_check[room_id] = now + self._schedule_interval(
                        room_id
                    )
            for _room_id, items in due:
                for payload in items:
                    self.emit("message", message=payload)
            self._stop_scheduler.wait(0.1)

    def set_poll_interval(self, seconds: float) -> None:
        self._poll_interval = max(1.0, float(seconds or 1.0))

    def set_media_download_enabled(self, enabled: bool) -> None:
        self._media_download_enabled = bool(enabled)

    def set_pinned_group(self, room_id: str) -> None:
        room_id = str(room_id or "")
        if room_id == self._pinned_room:
            return
        self._pinned_room = room_id
        if room_id:
            self._next_check[room_id] = 0.0
        # Pinning is dashboard-only. Reading comes from the local database,
        # so there is no reason to search the WeChat window / 通讯录 for it.

    def _open_chat_ui(self, room_id: str) -> None:
        if self._detached or self._db is None:
            return
        raw = (
            room_id[len(GROUP_ID_PREFIX) :]
            if room_id.startswith(GROUP_ID_PREFIX)
            else room_id
        )
        try:
            self._run_ui(
                lambda: self._ensure_wx()._gui.open_chat(raw),
                timeout=90.0,
                restore_foreground=True,
            )
            self.log(f"已在微信中打开置顶群聊：{self._group_name(raw)}")
        except Exception as exc:
            self._report_error(f"打开置顶群聊失败：{exc}", quiet=True)

    # ----------------------------------------------------------------- sending
    def _room_raw(self, room_id: str) -> str:
        return (
            room_id[len(GROUP_ID_PREFIX) :]
            if room_id.startswith(GROUP_ID_PREFIX)
            else room_id
        )

    def _find_main_weixin_hwnd(self) -> int:
        """Find the real main chat window, including a window hidden to tray."""
        import win32gui
        import win32process

        pids = set(_weixin_pids())
        if not pids:
            return 0
        candidates: list[tuple[int, int, int]] = []

        def callback(hwnd: int, _extra: Any) -> None:
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if int(pid) not in pids:
                    return
                cls = win32gui.GetClassName(hwnd) or ""
                if "QWindowIcon" not in cls:
                    return
                rect = win32gui.GetWindowRect(hwnd)
                width = max(0, rect[2] - rect[0])
                height = max(0, rect[3] - rect[1])
                title = win32gui.GetWindowText(hwnd) or ""
                # A minimized window can report a tiny/negative rect. Keep the
                # real WeChat window as a candidate so we can restore it.
                if width < 400 or height < 400:
                    if title not in ("微信", "Weixin"):
                        return
                visible = 1 if win32gui.IsWindowVisible(hwnd) else 0
                score = max(0, width) * max(0, height)
                if title in ("微信", "Weixin"):
                    score += 10_000_000
                if visible:
                    score += 5_000_000
                candidates.append((score, hwnd, visible))
            except Exception:
                return

        win32gui.EnumWindows(callback, None)
        if not candidates:
            return 0
        candidates.sort(reverse=True)
        _, hwnd, visible = candidates[0]
        try:
            iconic = bool(win32gui.IsIconic(hwnd))
        except Exception:
            iconic = False
        if not visible or iconic:
            try:
                import win32con

                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
        return int(hwnd)

    def recover_window(self) -> int:
        """Restore a hidden/minimized WeChat window and rebuild the GUI handle."""
        hwnd = 0
        try:
            hwnd = self._find_main_weixin_hwnd()
        except Exception:
            hwnd = 0
        if hwnd:
            try:
                import win32con
                import win32gui

                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
        with self._lock:
            self._wx = None
        return int(hwnd)

    def _ensure_wx(self) -> Any:
        if self._db is None:
            raise RuntimeError("微信 4.x 数据库尚未就绪")
        if self._wx is None:
            from wechatauto import WeChat
            from wechatauto.guia import WeChatGUI

            hwnd = self._find_main_weixin_hwnd()
            try:
                gui = WeChatGUI(hwnd=hwnd) if hwnd else WeChatGUI()
                self._wx = WeChat(gui=gui, db=self._db)
            except Exception:
                self._wx = WeChat(db=self._db)
        return self._wx

    def _run_ui(
        self,
        func: Callable[[], Any],
        timeout: float = 120.0,
        restore_foreground: bool = False,
    ) -> Any:
        self._guard.before_action()

        def task() -> Any:
            previous = 0
            if restore_foreground:
                try:
                    previous = ctypes.windll.user32.GetForegroundWindow()
                except Exception:
                    previous = 0
            try:
                return func()
            finally:
                if previous:
                    try:
                        wx_hwnd = getattr(getattr(self._wx, "_gui", None), "main_hwnd", 0)
                        if previous != wx_hwnd:
                            ctypes.windll.user32.SetForegroundWindow(previous)
                    except Exception:
                        pass

        return self._ui_executor.submit(task).result(timeout=timeout)

    def _check_response(self, response: Any, action: str) -> None:
        if response is None:
            return
        success = getattr(response, "is_success", None)
        if success is False:
            message = ""
            if isinstance(response, dict):
                message = str(response.get("message") or "")
            raise RuntimeError(message or f"{action}失败")

    def _latest_seq(self, raw: str) -> int:
        try:
            rows = self._db.get_messages(raw, limit=1) if self._db is not None else []
            return int(rows[0].get("sort_seq") or 0) if rows else 0
        except Exception:
            return 0

    def _confirm_own_message(
        self,
        raw: str,
        baseline: int,
        *,
        content: str = "",
        media: bool = False,
        timeout: float = 3.0,
    ) -> bool:
        if self._db is None:
            return False
        deadline = time.time() + max(0.5, timeout)
        while time.time() < deadline:
            try:
                rows = self._db.get_new_messages(raw, since_seq=baseline, limit=50)
            except Exception:
                rows = []
            for row in rows or []:
                sender = str(row.get("sender_username") or "")
                if sender and sender != self._self_wxid:
                    continue
                if media:
                    if str(row.get("type") or "") in (
                        "图片",
                        "动画表情",
                        "语音",
                        "视频",
                        "文件/链接/卡片",
                    ):
                        return True
                    continue
                text = _clean_group_content(
                    str(row.get("content") or ""), self._self_wxid
                ).strip()
                if text == content.strip():
                    return True
            time.sleep(0.4)
        return False

    @staticmethod
    def _restore_foreground(hwnd: int) -> None:
        if not hwnd:
            return
        try:
            ctypes.windll.user32.SetForegroundWindow(int(hwnd))
        except Exception:
            pass

    def _send_with_retry(
        self,
        build: Callable[[], Any],
        raw: str,
        *,
        content: str = "",
        media: bool = False,
        timeout: float = 150.0,
        action: str = "发送消息",
    ) -> None:
        baseline = self._latest_seq(raw)
        last_error: Exception | None = None
        recovered_from = 0
        for attempt in range(2):
            self._ensure_attached()
            try:
                response = self._run_ui(
                    build, timeout=timeout, restore_foreground=True
                )
                self._check_response(response, action)
                self._restore_foreground(recovered_from)
                return
            except AutomationDetached:
                self._restore_foreground(recovered_from)
                raise
            except Exception as exc:
                last_error = exc
                # A COM/UIA failure can happen before the text was submitted.
                # Check the database first: if the message did land, never
                # retry and risk a duplicate.
                if self._confirm_own_message(
                    raw, baseline, content=content, media=media, timeout=3.5
                ):
                    self.log(
                        f"{action}返回异常，但数据库已确认消息落库，按成功处理"
                    )
                    self._restore_foreground(recovered_from)
                    return
                text = str(exc)
                recoverable = (
                    "事件无法调用任何订户" in text
                    or "-2147220991" in text
                    or "UIA" in text
                    or "超时" in text
                    or "窗口不可见" in text
                    or "不可见" in text
                    or "会话断开" in text
                    or "锁屏" in text
                )
                if attempt == 0 and recoverable:
                    self.log(
                        f"{action}遇到界面异常，正在恢复微信窗口并重试：{exc}",
                        level="warn",
                    )
                    try:
                        recovered_from = int(
                            ctypes.windll.user32.GetForegroundWindow()
                        )
                    except Exception:
                        recovered_from = 0
                    try:
                        self.recover_window()
                    except Exception:
                        with self._lock:
                            self._wx = None
                    time.sleep(1.5)
                    continue
                self._restore_foreground(recovered_from)
                break
        raise RuntimeError(f"{action}失败：{last_error}")

    async def send_text(self, room_id: str, text: str) -> None:
        await asyncio.to_thread(self._send_text_sync, room_id, text)

    def _prepare_send_gui(self, raw: str) -> Any:
        """Bind wechatauto to the current WeChat window and pre-open the chat.

        This is deliberately done before ``SendMsg``: if the UIA/OCR driver
        cannot open the target chat or find the input box, abort instead of
        letting wechatauto blind-type into whatever window has focus.
        """
        self._ensure_attached()
        try:
            self.recover_window()
        except Exception:
            with self._lock:
                self._wx = None
        wx = self._ensure_wx()
        gui = getattr(wx, "_gui", None)
        if gui is not None:
            try:
                gui.ensure_visible()
            except Exception:
                pass
            try:
                # Re-assert the UIA accessibility gate after a WeChat restart.
                gui._get_uia(refresh=True)
            except Exception:
                pass
        if not wx.ChatWith(raw, exact=False):
            raise RuntimeError(
                "wechatauto 无法打开目标会话或输入框，已取消发送，"
                "避免键盘误输入到其他窗口"
            )
        if gui is not None and not gui.get_input_box():
            raise RuntimeError(
                "wechatauto 未识别到微信输入框，已取消发送，"
                "避免键盘误输入到其他窗口"
            )
        return wx

    def _send_text_sync(self, room_id: str, text: str) -> None:
        self._ensure_attached()
        raw = self._room_raw(room_id)

        def task() -> Any:
            wx = self._prepare_send_gui(raw)
            return wx.SendMsg(text, who=raw)

        self._send_with_retry(
            task,
            raw,
            content=text,
            timeout=150.0,
            action="发送消息",
        )
        self.log(f"已发送文本到「{self._group_name(raw)}」")

    async def send_sticker(self, room_id: str, path: Path) -> None:
        await asyncio.to_thread(self._send_sticker_sync, room_id, path.resolve())

    def _send_sticker_sync(self, room_id: str, path: Path) -> None:
        self._ensure_attached()
        if not path.exists():
            raise RuntimeError(f"表情文件不存在：{path}")
        raw = self._room_raw(room_id)

        def task() -> Any:
            wx = self._prepare_send_gui(raw)
            # Use the replica public API for sticker/image delivery. WeChat
            # stores the resulting message as an image row, which the DB
            # listener reads back normally.
            return wx.SendFiles(str(path), who=raw)

        self._send_with_retry(
            task,
            raw,
            media=True,
            timeout=180.0,
            action="发送表情",
        )
        self.log(f"已发送表情到「{self._group_name(raw)}」")

    # ----------------------------------------------------------------- queries
    async def list_groups(self) -> list[GroupInfo]:
        await asyncio.to_thread(self._refresh_groups, True)
        return list(self._groups.values())

    async def get_members(
        self, room_id: str, include_history: bool = True
    ) -> list[MemberInfo]:
        if self._db is None:
            return []
        raw = self._room_raw(room_id)
        rows = await asyncio.to_thread(self._db.get_group_members, raw)
        members: list[MemberInfo] = []
        for row in rows or []:
            username = str(row.get("username") or "").strip()
            if not username:
                continue
            nickname = str(row.get("remark") or row.get("nick_name") or username)
            members.append(
                MemberInfo(
                    wxid=username,
                    nickname=nickname,
                    alias=str(row.get("nick_name") or ""),
                    is_owner=bool(row.get("is_owner")),
                )
            )
        return members

    async def get_account(self) -> AccountInfo | None:
        return self._account

    async def refresh(self) -> None:
        await asyncio.to_thread(self._refresh_groups, True)

    # -------------------------------------------------------------- guard API
    def allow_ui_action(self, seconds: float = 8.0) -> None:
        self._guard.allow(seconds)

    def detach(self, reason: str = "用户接管鼠标") -> None:
        self._guard.detach(reason)

    def reattach(self) -> None:
        self._guard.reattach()
        self._detached = False
        self._detach_reason = ""
        self.log("已重新接管微信 4.x 自动操作")

    def _ensure_attached(self) -> None:
        if self._detached or self._guard.detached:
            raise AutomationDetached(
                self._detach_reason
                or self._guard.reason
                or "群伴已脱离控制，请重新接管"
            )

    def _set_detached(self, reason: str) -> None:
        if self._detached:
            return
        self._detached = True
        self._detach_reason = reason or "检测到用户接管鼠标"
        self.log(
            f"{self._detach_reason}，已暂停发送与界面操作，等待手动确认",
            level="warn",
        )
        self.emit("detached", reason=self._detach_reason, at=time.time())

    @property
    def is_detached(self) -> bool:
        return self._detached or self._guard.detached

    # ------------------------------------------------------------------ errors
    def _report_error(self, message: str, quiet: bool = False) -> None:
        now = time.time()
        if message == self._last_error and now - self._last_error_at < 30:
            return
        self._last_error = message
        self._last_error_at = now
        if quiet:
            self.log(message, level="warn")
        else:
            self.log(message, level="error")

    # ------------------------------------------------------------ 4.x tools
    def _ui_call(
        self,
        func: Callable[[], Any],
        *,
        timeout: float = 180.0,
        allow: float = 30.0,
        restore_foreground: bool = True,
    ) -> Any:
        self._guard.allow(allow)
        return self._run_ui(
            func, timeout=timeout, restore_foreground=restore_foreground
        )

    def _wx_method(
        self,
        name: str,
        *args: Any,
        timeout: float = 180.0,
        allow: float = 30.0,
        **kwargs: Any,
    ) -> Any:
        def task() -> Any:
            wx = self._ensure_wx()
            return getattr(wx, name)(*args, **kwargs)

        return self._ui_call(task, timeout=timeout, allow=allow)

    async def sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        rows = await asyncio.to_thread(self._db.get_sessions, max(1, int(limit)))
        out = []
        for row in rows or []:
            item = dict(row)
            username = str(item.get("username") or "")
            try:
                item["name"] = self._db.get_nickname(username) or username
            except Exception:
                item["name"] = username
            item["is_group"] = username.endswith("@chatroom")
            out.append(_safe_json(item))
        return out

    async def search_contacts(self, keyword: str) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        rows = await asyncio.to_thread(self._db.search_contact, keyword or "")
        return [_safe_json(row) for row in rows or []]

    async def export_history(
        self,
        room_id: str,
        *,
        fmt: str = "json",
        limit: int | None = None,
    ) -> dict[str, Any]:
        if self._db is None:
            raise RuntimeError("微信 4.x 数据库尚未就绪")
        raw = self._room_raw(room_id)
        name = self._group_name(raw)
        safe = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", name)[:60] or "chat"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        fmt = "sqlite" if fmt == "sqlite" else "json"
        suffix = "sqlite" if fmt == "sqlite" else "json"
        out_dir = Path("data") / "exports"
        await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
        out_path = out_dir / f"replica_{safe}_{stamp}.{suffix}"
        result = await asyncio.to_thread(
            self._db.export_history,
            str(out_path),
            fmt,
            [raw],
            limit,
        )
        return {"path": str(out_path), "url": f"/api/replica/exports/{out_path.name}", **_safe_json(result or {})}

    async def list_accounts(self) -> list[dict[str, Any]]:
        from wechatauto import list_accounts

        rows = await asyncio.to_thread(list_accounts)
        return [_safe_json(row) for row in rows or []]

    async def moments(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        username: str = "",
        keyword: str = "",
        since: int | None = None,
        until: int | None = None,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        from wechatauto import MomentDB

        db = MomentDB(self._db)
        rows = await asyncio.to_thread(
            db.get_moments,
            limit,
            offset,
            username or None,
            since,
            until,
            keyword or None,
        )
        return [_safe_json(row) for row in rows or []]

    async def my_moments(self, limit: int = 20) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        from wechatauto import MomentDB

        rows = await asyncio.to_thread(MomentDB(self._db).get_my_moments, limit)
        return [_safe_json(row) for row in rows or []]

    async def moment_count(self) -> int:
        if self._db is None:
            return 0
        from wechatauto import MomentDB

        return int(await asyncio.to_thread(MomentDB(self._db).count))

    async def moment_interactions(self, limit: int = 50) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        from wechatauto import MomentDB

        rows = await asyncio.to_thread(
            MomentDB(self._db).get_interactions, limit, 0, False
        )
        return [_safe_json(row) for row in rows or []]

    async def download_moment_media(
        self, tid: int | str, *, images: bool = True, videos: bool = True
    ) -> list[str]:
        if self._db is None:
            return []
        from wechatauto import MomentDB

        db = MomentDB(self._db)
        feed = None
        try:
            feed = await asyncio.to_thread(db.get_moment, int(tid))
        except (TypeError, ValueError):
            feed = None
        if not feed:
            # MomentDB.parse_feed exposes the feed id (not the SQLite tid) in
            # ``id``; accept it so the tools page can use the listed value.
            rows = await asyncio.to_thread(db.get_moments, 500, 0, None, None, None, None)
            for row in rows or []:
                if str(row.get("id")) == str(tid):
                    feed = row
                    break
        if not feed:
            raise RuntimeError("朋友圈不存在")
        target = self._download_dir / "moments" / str(tid)
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        return await asyncio.to_thread(
            db.download_moment_media,
            feed,
            str(target),
            images,
            videos,
            True,
        )

    async def like_moment(self, index: int, cancel: bool = False) -> Any:
        def task() -> Any:
            wx = self._ensure_wx()
            moment = wx.Moment
            if moment is None:
                raise RuntimeError("UIA 朋友圈不可用")
            wx.SwitchToMoments()
            items = moment.GetMoments() or []
            if index < 0 or index >= len(items):
                raise RuntimeError("朋友圈序号超出范围")
            return moment.Like(items[index], cancel=cancel)

        return _safe_json(
            await asyncio.to_thread(
                self._ui_call, task, timeout=180.0, allow=60.0
            )
        )

    async def comment_moment(
        self, index: int, text: str, reply_to: str = ""
    ) -> Any:
        def task() -> Any:
            wx = self._ensure_wx()
            moment = wx.Moment
            if moment is None:
                raise RuntimeError("UIA 朋友圈不可用")
            wx.SwitchToMoments()
            items = moment.GetMoments() or []
            if index < 0 or index >= len(items):
                raise RuntimeError("朋友圈序号超出范围")
            if reply_to:
                return moment.Comment(items[index], text, reply_to=reply_to)
            return moment.Comment(items[index], text)

        return _safe_json(
            await asyncio.to_thread(
                self._ui_call, task, timeout=180.0, allow=60.0
            )
        )

    async def list_labels(self, prefer: str = "db") -> list[dict[str, Any]]:
        if self._db is None:
            return []
        if prefer != "ui":
            rows = await asyncio.to_thread(self._db.list_labels)
            return [_safe_json(row) for row in rows or []]
        result = await asyncio.to_thread(
            self._wx_method,
            "ListLabels",
            prefer="ui",
            timeout=180.0,
            allow=60.0,
        )
        data = _safe_json(result)
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"].get("labels", [])
        return data.get("labels", []) if isinstance(data, dict) else []

    async def create_label(self, name: str) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method, "CreateLabel", name, timeout=180.0, allow=60.0
            )
        )

    async def rename_label(self, old: str, new: str) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method,
                "RenameLabel",
                old,
                new,
                timeout=180.0,
                allow=60.0,
            )
        )

    async def delete_label(self, name: str) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method, "DeleteLabel", name, timeout=180.0, allow=60.0
            )
        )

    async def add_label_members(self, label: str, members: list[str]) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method,
                "AddLabelMembers",
                label,
                members,
                timeout=240.0,
                allow=90.0,
            )
        )

    async def remove_label_members(self, label: str, members: list[str]) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method,
                "RemoveLabelMembers",
                label,
                members,
                timeout=240.0,
                allow=90.0,
            )
        )

    async def label_members(self, label: str, limit: int | None = None) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method,
                "LabelMembers",
                label,
                limit,
                timeout=180.0,
                allow=60.0,
            )
        )

    async def send_to_label(
        self,
        label: str,
        text: str,
        *,
        members: list[str] | None = None,
        dry_run: bool = False,
        limit: int | None = None,
    ) -> Any:
        def task() -> Any:
            wx = self._ensure_wx()
            return wx.SendToLabel(
                text,
                label,
                members=members,
                dry_run=dry_run,
                limit=limit,
                verify=not dry_run,
            )

        return _safe_json(
            await asyncio.to_thread(
                self._ui_call, task, timeout=300.0, allow=180.0
            )
        )

    async def forward_message(
        self,
        to: list[str] | str,
        *,
        chat: str = "",
        match: str = "",
        dry_run: bool = False,
        text: str = "",
    ) -> Any:
        targets = [to] if isinstance(to, str) else list(to or [])
        def task() -> Any:
            wx = self._ensure_wx()
            return wx.ForwardMessage(
                targets,
                chat=chat or None,
                match=match or None,
                dry_run=dry_run,
                verify=not dry_run,
                text=text or None,
            )

        return _safe_json(
            await asyncio.to_thread(
                self._ui_call, task, timeout=300.0, allow=180.0
            )
        )

    async def forward_to_label(
        self,
        label: str,
        *,
        chat: str = "",
        match: str = "",
        dry_run: bool = True,
        limit: int | None = None,
        text: str = "",
    ) -> Any:
        def task() -> Any:
            wx = self._ensure_wx()
            return wx.ForwardToLabel(
                label,
                chat=chat or None,
                match=match or None,
                dry_run=dry_run,
                limit=limit,
                verify=not dry_run,
                text=text or None,
            )

        return _safe_json(
            await asyncio.to_thread(
                self._ui_call, task, timeout=300.0, allow=180.0
            )
        )

    async def rename_member(
        self, who: str, new_remark: str, allow_empty: bool = False
    ) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method,
                "RenameMember",
                who,
                new_remark,
                verify=True,
                allow_empty=allow_empty,
                timeout=240.0,
                allow=90.0,
            )
        )

    async def probe_remark(self, who: str) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method,
                "ProbeRemark",
                who,
                timeout=180.0,
                allow=60.0,
            )
        )

    async def probe_labels(self) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method, "ProbeLabels", timeout=240.0, allow=90.0
            )
        )

    async def add_friend_from_group(
        self,
        group: str,
        member: str,
        *,
        message: str = "",
        dry_run: bool = False,
    ) -> Any:
        def task() -> Any:
            wx = self._ensure_wx()
            return wx.AddFriendFromGroup(
                group,
                member,
                message=message or None,
                dry_run=dry_run,
            )

        return _safe_json(
            await asyncio.to_thread(
                self._ui_call, task, timeout=300.0, allow=180.0
            )
        )

    async def probe_add_friend(self, group: str, member: str) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method,
                "ProbeGroupAddFriend",
                group,
                member,
                timeout=240.0,
                allow=90.0,
            )
        )

    async def poke(self, who: str) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method, "Poke", who, timeout=120.0, allow=30.0
            )
        )

    async def voice_call(self, who: str, video: bool = False) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method,
                "VoiceCall",
                who,
                video=video,
                timeout=180.0,
                allow=60.0,
            )
        )

    async def recall_last(self, who: str) -> Any:
        return _safe_json(
            await asyncio.to_thread(
                self._wx_method,
                "RecallLastMessage",
                who,
                timeout=120.0,
                allow=30.0,
            )
        )

    async def chat_info(self) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self._ui_call,
            lambda: self._ensure_wx().ChatInfo(),
            timeout=120.0,
            allow=30.0,
        )
        return _safe_json(result or {})

    async def send_file(self, room_id: str, path: Path) -> None:
        path = path.resolve()
        if not path.exists():
            raise RuntimeError(f"文件不存在：{path}")
        raw = self._room_raw(room_id)

        def task() -> Any:
            wx = self._ensure_wx()
            return wx.SendFiles(str(path), who=raw)

        self._send_with_retry(
            task,
            raw,
            media=True,
            timeout=300.0,
            action="发送文件",
        )

    async def send_image(self, room_id: str, path: Path) -> None:
        path = path.resolve()
        if not path.exists():
            raise RuntimeError(f"图片不存在：{path}")
        raw = self._room_raw(room_id)

        def task() -> Any:
            wx = self._ensure_wx()
            return wx._gui.send_image(str(path), raw, verify=True)

        self._send_with_retry(
            task,
            raw,
            media=True,
            timeout=300.0,
            action="发送图片",
        )

    async def send_at(self, room_id: str, text: str, members: list[str]) -> Any:
        raw = self._room_raw(room_id)
        member = members[0] if members else ""

        def task() -> Any:
            wx = self._ensure_wx()
            return wx.SendMsg(text, who=raw, at=member)

        baseline = self._latest_seq(raw)
        try:
            return _safe_json(
                await asyncio.to_thread(
                    self._ui_call, task, timeout=300.0, allow=180.0
                )
            )
        except Exception:
            if self._confirm_own_message(raw, baseline, timeout=3.0):
                return {"status": "成功", "message": "已发送 @ 消息"}
            raise

    async def quote(
        self, room_id: str, text: str, target_text: str = ""
    ) -> Any:
        raw = self._room_raw(room_id)

        def task() -> Any:
            wx = self._ensure_wx()
            return wx._gui.quote_msg(
                text, raw, target_text=target_text or None, verify=True
            )

        return _safe_json(
            await asyncio.to_thread(
                self._ui_call, task, timeout=300.0, allow=180.0
            )
        )

    async def reply(self, room_id: str, text: str, target_text: str = "") -> Any:
        return await self.quote(room_id, text, target_text)

    async def media_status(
        self, group_id_external: str, message_meta: dict[str, Any]
    ) -> dict[str, Any]:
        if self._media is None or self._db is None:
            return {}
        raw = self._room_raw(group_id_external)
        local_id = message_meta.get("local_id")
        if local_id is None:
            return {}
        shard = message_meta.get("shard") or None
        mtype = str(message_meta.get("msg_type") or "")
        try:
            if mtype == "图片":
                return await asyncio.to_thread(
                    self._media.image_status, raw, int(local_id), False, shard
                )
            if mtype == "语音":
                return await asyncio.to_thread(
                    self._media.voice_status, raw, int(local_id)
                )
        except Exception as exc:
            return {"error": str(exc)}
        return {}

    async def download_media(
        self,
        group_id_external: str,
        message_meta: dict[str, Any],
        *,
        tier: str = "best",
        original: bool = False,
    ) -> dict[str, Any]:
        if self._media is None or self._db is None:
            raise RuntimeError("微信 4.x 媒体下载器尚未就绪")
        raw = self._room_raw(group_id_external)
        local_id = message_meta.get("local_id")
        if local_id is None:
            raise RuntimeError("这条消息没有本地媒体编号")
        local_id = int(local_id)
        shard = message_meta.get("shard") or None
        mtype = str(message_meta.get("msg_type") or "")
        await asyncio.to_thread(self._download_dir.mkdir, parents=True, exist_ok=True)
        self._guard.allow(60.0)
        if mtype == "图片" and not self._image_key_checked:
            self._image_key_checked = True
            try:
                await asyncio.to_thread(self._media.detect_image_key)
            except Exception:
                pass
        if mtype == "图片":
            if original:
                path = await asyncio.to_thread(
                    self._ui_call,
                    lambda: self._media.download_image_original(
                        raw,
                        local_id,
                        str(self._download_dir),
                        chat_name=self._group_name(raw),
                        shard=shard,
                    ),
                    timeout=300.0,
                    allow=60.0,
                )
            else:
                path = await asyncio.to_thread(
                    self._media.download_image,
                    raw,
                    local_id,
                    str(self._download_dir),
                    None,
                    None,
                    tier if tier in ("original", "best", "mid", "thumb", "full") else "best",
                    shard,
                )
        elif mtype == "语音":
            path = await asyncio.to_thread(
                self._media.download_voice,
                raw,
                local_id,
                str(self._download_dir),
                shard,
            )
        elif mtype == "视频":
            path = await asyncio.to_thread(
                self._media.download_video,
                raw,
                local_id,
                str(self._download_dir),
                shard,
            )
        else:
            path = await asyncio.to_thread(
                self._media.download_media,
                raw,
                local_id,
                str(self._download_dir),
            )
        if not path:
            raise RuntimeError("媒体下载失败：本机没有可用文件或密钥")
        absolute = Path(path).resolve()
        root = (Path("data") / "media").resolve()
        try:
            relative = absolute.relative_to(root).as_posix()
        except ValueError:
            relative = ""
        return {
            "path": str(path),
            "media_path": relative,
            "url": f"/api/replica/files/{Path(path).name}",
        }

    async def list_image_status(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._media is None:
            return []
        rows = await asyncio.to_thread(
            self._media.list_image_status, self._self_wxid, limit
        )
        return [_safe_json(row) for row in rows or []]

    async def list_voice_status(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._media is None:
            return []
        rows = await asyncio.to_thread(
            self._media.list_voice_status, self._self_wxid, limit
        )
        return [_safe_json(row) for row in rows or []]

    async def forward_voice(
        self, who: str = "", target: str = ""
    ) -> Any:
        def task() -> Any:
            wx = self._ensure_wx()
            return wx.ForwardVoiceMessage(
                who or None,
                target or None,
                str(self._download_dir / "voice"),
            )

        return _safe_json(
            await asyncio.to_thread(
                self._ui_call, task, timeout=300.0, allow=180.0
            )
        )

    async def group_members_watch(
        self, room_id: str, action: str = "capture"
    ) -> dict[str, Any]:
        if self._db is None:
            return {}
        raw = self._room_raw(room_id)
        watchers = getattr(self, "_member_watchers", None)
        if watchers is None:
            watchers = self._member_watchers = {}
        watcher = watchers.get(raw)
        if watcher is None:
            watcher = await asyncio.to_thread(
                self._db.get_group_member_watcher, raw
            )
            watchers[raw] = watcher
        if action == "poll":
            result = await asyncio.to_thread(watcher.poll)
        else:
            snapshot = await asyncio.to_thread(watcher.capture)
            result = {"count": len(snapshot or set())}
        return _safe_json(result or {})

    async def detect_image_key(self, refresh: bool = False) -> Any:
        if self._media is None:
            raise RuntimeError("媒体下载器尚未就绪")
        result = await asyncio.to_thread(self._media.detect_image_key, refresh)
        return result

    async def doctor(self) -> dict[str, Any]:
        data = self.diagnostics()
        if self._db is not None:
            try:
                info = await asyncio.to_thread(self._db.get_self_info)
                data["self_info"] = _safe_json(info)
                data["db_dir"] = str(getattr(self._db, "db_dir", ""))
                data["account_dir"] = str(getattr(self._db, "account_dir", ""))
                data["keys"] = len(getattr(self._db, "_keys", {}) or {})
                data["db_files"] = len(getattr(self._db, "_db_files", []) or [])
            except Exception as exc:
                data["db_error"] = str(exc)
        data["weixin_exe"] = str(self._weixin_exe() or "")
        data["weixin_version"] = _file_version(self._weixin_exe()) if self._weixin_exe() else ""
        data["image_key_cached"] = False
        if self._media is not None:
            try:
                data["image_key_cached"] = bool(self._media._load_persisted_key())
            except Exception:
                pass
        return data

    def diagnostics(self) -> dict[str, Any]:
        return {
            "account": self._account.wxid if self._account else "",
            "self_wxid": self._self_wxid,
            "groups": len(self._groups),
            "readable": len(self._readable_rooms),
            "pinned": self._pinned_room,
            "detached": self.is_detached,
            "media": bool(self._media_download_enabled),
            "listener": self._listener is not None,
            "pending": {k: len(v) for k, v in self._pending.items() if v},
            "timers": {
                k: round(max(0.0, v - time.time()), 1)
                for k, v in self._next_check.items()
                if k in self._readable_rooms
            },
        }
