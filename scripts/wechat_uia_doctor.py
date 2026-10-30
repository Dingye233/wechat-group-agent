"""Safe WeChat UIA health check after a WeChat minor update.

Reads process/window state and materializes the UIA tree.  It never clicks,
types, opens a chat or sends a message.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from wechatauto.uia_driver import WeChatUIA
from app.services.wechat_compat import snapshot as wechat_snapshot


def main() -> int:
    print("=== WeChat compatibility ===")
    print(wechat_snapshot())
    try:
        import psutil
    except Exception:
        psutil = None
    print("=== WeChat processes ===")
    if psutil is not None:
        rows = []
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            name = (proc.info.get("name") or "").lower()
            if name in {"weixin.exe", "wechat.exe"}:
                rows.append(proc.info)
        for row in rows:
            print(row)
        if not rows:
            print("no Weixin.exe running")
    else:
        print("psutil not installed; skipping process scan")

    print("=== UIA check (no clicks, no sends) ===")
    started = time.monotonic()
    uia = WeChatUIA(timeout=25)
    try:
        ok = uia.ensure_window(wake=True, timeout=25)
        print("ensure_window:", ok, f"({time.monotonic() - started:.2f}s)")
        win = getattr(uia, "_win", None)
        if win is not None:
            print("root:", win.ClassName, repr(win.Name), repr(win.AutomationId))
            try:
                children = win.GetChildren()
                print(
                    "children:",
                    [(item.ClassName, item.Name) for item in children[:8]],
                )
            except Exception as exc:
                print("children error:", exc)
        else:
            print("root: none")
    except Exception as exc:
        print("UIA error:", type(exc).__name__, str(exc)[:1000])
        return 1
    print("=== done; no window was clicked ===")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
