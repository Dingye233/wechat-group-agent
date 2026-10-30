"""Check the Qunban GitHub repository for a newer main commit.

DeepSeek Harness uses release tags; Qunban currently has no tags, so main's
commit is the source of truth.  This is detection only: it never pulls or
mutates the checkout.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings as app_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_URL = "https://github.com/Dingye233/wechat-group-agent"
BRANCH = "main"
STATE_PATH = PROJECT_ROOT / "data" / "update-check.json"
_TIMEOUT = 20.0


def _git_exe() -> str:
    return shutil.which("git") or ""


async def _run_git(*args: str, timeout: float = _TIMEOUT) -> str:
    git = _git_exe()
    if not git:
        raise RuntimeError("未找到 git，无法检查群伴更新")
    proc = await asyncio.create_subprocess_exec(
        git,
        *args,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("检查更新超时（GitHub 网络或代理不可用）") from exc
    if proc.returncode != 0:
        text = (err or out).decode("utf-8", errors="replace").strip()
        raise RuntimeError(text[:1000] or "git 检查更新失败")
    return (out or b"").decode("utf-8", errors="replace").strip()


async def current_commit() -> str:
    try:
        return await _run_git("rev-parse", "HEAD", timeout=8.0)
    except Exception:
        return ""


async def latest_remote_commit() -> str:
    text = await _run_git(
        "ls-remote",
        REPO_URL,
        f"refs/heads/{BRANCH}",
        timeout=_TIMEOUT,
    )
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].endswith(f"/{BRANCH}"):
            return parts[0]
    raise RuntimeError(f"GitHub 没有返回 {BRANCH} 分支")


async def _is_ancestor(ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            _git_exe(),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=8.0)
        return proc.returncode == 0
    except Exception:
        return False


async def _commit_meta(commit: str) -> dict[str, Any]:
    if not commit:
        return {}
    try:
        text = await _run_git(
            "show",
            "-s",
            "--format=%ct%x00%s",
            commit,
            timeout=8.0,
        )
        stamp, _, subject = text.partition("\x00")
        return {
            "timestamp": int(stamp) if stamp.strip().isdigit() else 0,
            "message": subject.strip()[:300],
        }
    except Exception:
        return {}


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(STATE_PATH)
    except Exception:
        pass


def status() -> dict[str, Any]:
    state = _read_state()
    return {
        "repo": REPO_URL,
        "branch": BRANCH,
        "current_version": app_settings.version,
        "current": state.get("current") or "",
        "current_short": (state.get("current") or "")[:12],
        "latest": state.get("latest") or "",
        "latest_short": (state.get("latest") or "")[:12],
        "update_available": bool(state.get("update_available")),
        "latest_message": state.get("latest_message") or "",
        "latest_time": state.get("latest_time") or "",
        "checked_at": state.get("checked_at") or "",
        "error": state.get("error") or "",
    }


async def check_update() -> dict[str, Any]:
    state = _read_state()
    try:
        current = await current_commit()
        latest = await latest_remote_commit()
        if not latest:
            raise RuntimeError("无法读取远程 main commit")
        update_available = bool(current and latest != current)
        if update_available:
            # A local commit newer than main is not an update.
            if await _is_ancestor(latest, current):
                update_available = False
        meta = await _commit_meta(latest)
        checked = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        result = {
            "repo": REPO_URL,
            "branch": BRANCH,
            "current": current,
            "current_short": current[:12],
            "latest": latest,
            "latest_short": latest[:12],
            "current_version": app_settings.version,
            "update_available": update_available,
            "latest_message": meta.get("message") or "",
            "latest_time": (
                datetime.fromtimestamp(meta["timestamp"], timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
                if meta.get("timestamp")
                else ""
            ),
            "checked_at": checked,
            "error": "",
        }
        _write_state(result)
        return result
    except Exception as exc:
        result = {
            **state,
            "repo": REPO_URL,
            "branch": BRANCH,
            "update_available": False,
            "checked_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "error": str(exc)[:1000],
        }
        _write_state(result)
        return result
