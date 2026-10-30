"""WeChat 4.x version / UIA compatibility registry.

Keep this small and deterministic: a new WeChat build is not "try OCR and hope";
it is either a verified UIA profile or a blocked/unsupported build.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Manifest verified against real runs.  The gate RVA is the QAccessible active
# byte used by wechatauto; it is a runtime hint, not a write target here.
KNOWN_VERSIONS: dict[str, dict[str, Any]] = {
    "4.2.0.12": {"gate_rva": 0x0B507C08, "uia": True},
}

_CANDIDATES = (
    r"D:\Weixin\Weixin.exe",
    r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
)
GATE_CACHE = Path(os.path.expanduser("~")) / ".wechatauto" / "gate_cache.json"


def _file_version(path: Path) -> str:
    try:
        import win32api

        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return "%d.%d.%d.%d" % (
            ms >> 16,
            ms & 0xFFFF,
            ls >> 16,
            ls & 0xFFFF,
        )
    except Exception:
        return ""


def weixin_path() -> Path | None:
    for raw in _CANDIDATES:
        path = Path(raw)
        if path.is_file():
            return path
    return None


def gate_cache() -> dict[str, Any]:
    try:
        data = json.loads(GATE_CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def snapshot() -> dict[str, Any]:
    path = weixin_path()
    version = _file_version(path) if path else ""
    dll = path.parent / version / "Weixin.dll" if path and version else None
    dll_ok = bool(dll and dll.is_file())
    profile = KNOWN_VERSIONS.get(version, {})
    cache = gate_cache()
    cache_hit = any(key.startswith(version + "|") for key in cache) if version else False
    return {
        "path": str(path) if path else "",
        "version": version,
        "dll": str(dll) if dll else "",
        "dll_exists": dll_ok,
        "uia_profile": bool(profile.get("uia")),
        "gate_rva": hex(int(profile.get("gate_rva") or 0)) if profile else "",
        "gate_cache_hit": cache_hit,
        "known": bool(profile),
    }
