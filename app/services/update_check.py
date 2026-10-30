"""Check and safely fast-forward the Qunban GitHub main branch.

DeepSeek Harness uses release tags; Qunban currently has no tags, so main's
commit is the source of truth.  Checking is read-only.  Applying an update
backs up user state first and refuses to run with tracked local changes.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings as app_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_URL = "https://github.com/Dingye233/wechat-group-agent"
BRANCH = "main"
STATE_PATH = PROJECT_ROOT / "data" / "update-check.json"
BACKUP_ROOT = PROJECT_ROOT / "data" / "update-backups"
_TIMEOUT = 20.0
_APPLY_LOCK = asyncio.Lock()


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


def _backup_update() -> str:
    """Back up user state, but not multi-GB media/runtime caches."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_ROOT / stamp
    dest.mkdir(parents=True, exist_ok=True)

    def copy_path(relative: str) -> None:
        source = PROJECT_ROOT / relative
        if not source.exists():
            return
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)

    for relative in (
        "data/agent.db",
        "data/wechatauto-queue.db",
        "data/knowledge",
        "data/dsh/studio",
        ".env",
    ):
        copy_path(relative)

    profiles = PROJECT_ROOT / "data" / "dsh" / "home" / "profiles"
    if profiles.is_dir():
        for profile in profiles.iterdir():
            if not profile.is_dir():
                continue
            target = dest / "data" / "dsh" / "home" / "profiles" / profile.name
            shutil.copytree(
                profile,
                target,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    "node_modules",
                    "sessions",
                    "logs",
                    "*.log",
                    "__pycache__",
                ),
            )
            qunban_plugins = profile / "node_modules" / "@qunban"
            if qunban_plugins.is_dir():
                shutil.copytree(
                    qunban_plugins,
                    target / "node_modules" / "@qunban",
                    dirs_exist_ok=True,
                )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "head": _read_state().get("current") or "",
        "selected": [
            "data/agent.db",
            "data/wechatauto-queue.db",
            "data/knowledge",
            "data/dsh/studio",
            "data/dsh/home/profiles (without node_modules/sessions/logs)",
            "profiles/*/node_modules/@qunban",
            ".env",
        ],
    }
    (dest / "BACKUP.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(dest)


async def _tracked_dirty() -> str:
    return await _run_git("status", "--porcelain", "--untracked-files=no")


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
        "backup_dir": state.get("backup_dir") or "",
        "applied_at": state.get("applied_at") or "",
        "restart_required": bool(state.get("restart_required")),
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
        state.update(result)
        _write_state(state)
        return state
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


async def apply_update() -> dict[str, Any]:
    """Fast-forward to origin/main after snapshotting user state.

    The caller must restart Qunban to load the new Python code.  This function
    deliberately does not run git reset/clean or overwrite data/.
    """
    async with _APPLY_LOCK:
        state = _read_state()
        try:
            branch = await _run_git("rev-parse", "--abbrev-ref", "HEAD", timeout=8.0)
            if branch.strip() != BRANCH:
                raise RuntimeError(f"当前分支是 {branch.strip()}，只允许从 {BRANCH} 更新")
            dirty = await _tracked_dirty()
            if dirty.strip():
                raise RuntimeError(
                    "检测到未提交的 tracked 文件修改，已停止更新。"
                    "请先提交/暂存这些修改，data/ 等 ignored 文件不影响。"
                )
            await _run_git("fetch", "origin", BRANCH, timeout=60.0)
            latest = await _run_git("rev-parse", "FETCH_HEAD", timeout=8.0)
            current = await current_commit()
            if not latest or latest == current:
                state.update(
                    {
                        "update_available": False,
                        "backup_dir": "",
                        "applied_at": "",
                        "restart_required": False,
                        "error": "",
                    }
                )
                _write_state(state)
                return {**state, "applied": False, "reason": "already-up-to-date"}
            if not await _is_ancestor(current, latest):
                raise RuntimeError("当前 HEAD 不是 origin/main 的祖先，无法 fast-forward")
            backup_dir = await asyncio.to_thread(_backup_update)
            await _run_git("merge", "--ff-only", "FETCH_HEAD", timeout=60.0)
            new_head = await current_commit()
            applied_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            state.update(
                {
                    "current": new_head,
                    "current_short": new_head[:12],
                    "latest": latest,
                    "latest_short": latest[:12],
                    "update_available": False,
                    "applied_at": applied_at,
                    "backup_dir": backup_dir,
                    "restart_required": True,
                    "error": "",
                }
            )
            _write_state(state)
            return {**state, "applied": True, "restart_required": True}
        except Exception as exc:
            state.update(
                {
                    "applied_at": "",
                    "restart_required": False,
                    "error": str(exc)[:1000],
                }
            )
            _write_state(state)
            return {**state, "applied": False, "error": str(exc)[:1000]}
