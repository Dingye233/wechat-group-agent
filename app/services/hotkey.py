"""Global pause hotkey for the Qunban automation.

The hotkey is intentionally handled by Windows itself (``RegisterHotKey``)
instead of a keyboard hook: it works while WeChat is in front, does not
inject keys, and cannot swallow normal typing.  When pressed it only asks the
runtime to pause; resuming always requires the explicit dashboard button.

Examples: ``Alt+P``, ``Ctrl+Alt+Space``, ``F9``.  An empty string disables
the hotkey.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIERS = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
}

_KEYS = {
    "space": 0x20,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "enter": 0x0D,
    "return": 0x0D,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def parse_hotkey(text: str) -> tuple[int, int, str]:
    """Return ``(modifiers, virtual_key, canonical_name)``.

    Raises ``ValueError`` for an unsupported combination.
    """

    raw = str(text or "").strip().replace("＋", "+")
    if not raw:
        raise ValueError("快捷键不能为空")
    parts = [part.strip() for part in raw.split("+") if part.strip()]
    if not parts:
        raise ValueError("快捷键格式无效")
    key = parts[-1].lower()
    modifiers = 0
    names: list[str] = []
    for part in parts[:-1]:
        name = part.lower()
        modifier = _MODIFIERS.get(name)
        if modifier is None:
            raise ValueError(f"不支持的修饰键：{part}")
        if modifiers & modifier:
            continue
        modifiers |= modifier
        names.append(
            {"control": "Ctrl", "ctrl": "Ctrl", "win": "Win", "meta": "Win"}.get(
                name, name.capitalize()
            )
        )

    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
        key_name = key.upper()
    elif key in _KEYS:
        vk = _KEYS[key]
        key_name = key.capitalize()
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
        key_name = f"F{int(key[1:])}"
    else:
        raise ValueError(f"不支持的按键：{parts[-1]}")

    canonical = "+".join([*names, key_name])
    return modifiers, vk, canonical


class GlobalHotkey:
    """Own a single ``RegisterHotKey`` registration on a worker thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread_id = 0
        self._registered = False
        self._error = ""
        self._canonical = ""
        self._callback: Callable[[], None] | None = None

    @property
    def active(self) -> bool:
        return bool(self._registered and self._thread and self._thread.is_alive())

    @property
    def error(self) -> str:
        return self._error

    @property
    def canonical(self) -> str:
        return self._canonical

    def set(self, text: str, callback: Callable[[], None] | None) -> dict:
        """Replace the current registration.  Empty text disables it."""

        self.stop()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._callback = callback
        self._error = ""
        self._canonical = ""
        raw = str(text or "").strip()
        if not raw:
            return {"ok": True, "active": False, "hotkey": "", "error": ""}
        try:
            modifiers, vk, canonical = parse_hotkey(raw)
        except ValueError as exc:
            self._error = str(exc)
            return {"ok": False, "active": False, "hotkey": raw, "error": self._error}

        self._modifiers = modifiers | MOD_NOREPEAT
        self._vk = vk
        self._canonical = canonical
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="qunban-hotkey"
        )
        self._thread.start()
        self._ready.wait(1.5)
        if self._registered:
            return {"ok": True, "active": True, "hotkey": canonical, "error": ""}
        if not self._error:
            self._error = "快捷键注册失败，可能已被其他程序占用"
        return {"ok": False, "active": False, "hotkey": canonical, "error": self._error}

    def stop(self) -> None:
        thread = self._thread
        self._stop.set()
        if thread and thread.is_alive():
            if self._thread_id:
                try:
                    ctypes.windll.user32.PostThreadMessageW(
                        self._thread_id, WM_QUIT, 0, 0
                    )
                except Exception:
                    pass
            thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = 0
        self._registered = False

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        if self._stop.is_set():
            return
        ok = user32.RegisterHotKey(
            None, 1, int(self._modifiers), int(self._vk)
        )
        if not ok:
            self._error = "快捷键注册失败，可能已被其他程序占用"
            self._ready.set()
            return
        self._registered = True
        self._ready.set()
        try:
            message = MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == WM_HOTKEY and int(message.wParam) == 1:
                    callback = self._callback
                    if callback is not None:
                        try:
                            callback()
                        except Exception:
                            pass
        finally:
            self._registered = False
            try:
                user32.UnregisterHotKey(None, 1)
            except Exception:
                pass
