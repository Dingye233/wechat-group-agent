"""DeepSeek Harness runtime installation and update management.

The GitHub repository is the source of truth for versions: we read its tags
through ``git ls-remote`` and install the matching published npm package into a
versioned runtime directory.  Builds from source are intentionally not the
default path -- installing a published package is deterministic, fast, and can
be rolled back, while ``pnpm build`` of the full monorepo is minutes long and
needs a native toolchain.

Layout under ``data/dsh``::

    home/                 DSH_HOME (profiles, sessions, patches)
    runtimes/<version>/   node_modules/@deepseek-ai/dsh (one installed version)
    workspace/            agent workspace (stable cwd for session adoption)
    state.json            current version, git tag/commit, last update check
    logs/                 install logs

The runtime manager never starts DSH itself; ``app.services.dsh_agent`` owns
the process lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings as process_settings

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/deepseek-ai/deepseek-harness"
TAG_PREFIX = "dsh-v"
NPM_PACKAGE = "@deepseek-ai/dsh"
INSTALL_TIMEOUT_SECONDS = 1800
GIT_TIMEOUT_SECONDS = 90
VERIFY_TIMEOUT_SECONDS = 120
LOG_LIMIT = 240

DATA_DIR = process_settings.data_dir / "dsh"
HOME_DIR = DATA_DIR / "home"
RUNTIME_DIR = DATA_DIR / "runtimes"
WORKSPACE_DIR = DATA_DIR / "workspace"
LOG_DIR = DATA_DIR / "logs"
SKILLS_DIR = DATA_DIR / "skills"
PROJECT_ROOT = process_settings.data_dir.parent
PLUGIN_SOURCE_DIR = PROJECT_ROOT / "dsh" / "qunban-native"
STATE_PATH = DATA_DIR / "state.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    for path in (DATA_DIR, HOME_DIR, RUNTIME_DIR, WORKSPACE_DIR, LOG_DIR, SKILLS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _read_state() -> dict[str, Any]:
    _ensure_dirs()
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return state
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("读取 DSH 状态失败：%s", exc)
    return {}


def _write_state(state: dict[str, Any]) -> None:
    _ensure_dirs()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(STATE_PATH)


def _node_exe() -> str | None:
    return shutil.which("node") or shutil.which("node.exe")


def _npm_exe() -> str | None:
    if os.name == "nt":
        return shutil.which("npm.cmd") or shutil.which("npm")
    return shutil.which("npm")


def _git_exe() -> str | None:
    return shutil.which("git")


def _version_key(version: str) -> tuple[Any, ...]:
    match = re.match(
        r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$", str(version).strip()
    )
    if not match:
        return (0, 0, 0, -1, ())
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    pre = match.group(4)
    if not pre:
        return (major, minor, patch, 9, ())
    # alpha < beta < rc < final; numeric identifiers compare numerically.
    rank = 0 if pre.startswith("alpha") else 1 if pre.startswith("beta") else 2
    parts = tuple(
        (0, int(piece)) if piece.isdigit() else (1, piece)
        for piece in re.split(r"[.-]", pre)
    )
    return (major, minor, patch, rank, parts)


def _pick_version(versions: list[str], channel: str) -> str:
    candidates = [
        version
        for version in versions
        if channel == "alpha" or "-alpha" not in version
    ]
    if not candidates:
        raise RuntimeError(f"没有可用的 DSH 版本（channel={channel}）")
    return max(candidates, key=_version_key)


def _bin_path(version: str) -> Path:
    return (
        RUNTIME_DIR
        / version
        / "node_modules"
        / "@deepseek-ai"
        / "dsh"
        / "lib"
        / "bin.js"
    )


class DSHRuntime:
    """Install, verify, update and report the DSH CLI runtime."""

    def __init__(self) -> None:
        _ensure_dirs()
        self._install_job: dict[str, Any] = {}
        self._install_task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------- state
    def installed_version(self) -> str:
        state = _read_state()
        version = str(state.get("version") or "")
        if version and _bin_path(version).exists():
            return version
        return ""

    def state(self) -> dict[str, Any]:
        return _read_state()

    def log(self, message: str) -> None:
        job = self._install_job
        if not job:
            return
        lines = job.setdefault("log", [])
        lines.append(f"{time.strftime('%H:%M:%S')} {message}")
        del lines[:-LOG_LIMIT]

    # ------------------------------------------------------------- query
    async def git_tags(self) -> dict[str, dict[str, str]]:
        """Return ``{version: {tag, commit}}`` from the GitHub repository."""
        git = _git_exe()
        if not git:
            raise RuntimeError("未找到 git，无法检查 DeepSeek Harness 更新")
        proc = await asyncio.create_subprocess_exec(
            git,
            "ls-remote",
            "--tags",
            REPO_URL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=GIT_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            with contextlib.suppress(Exception):
                proc.kill()
            raise RuntimeError("检查 DSH 更新超时（git ls-remote）") from exc
        if proc.returncode != 0:
            detail = (stderr or b"").decode("utf-8", "replace").strip()[:500]
            raise RuntimeError(f"git ls-remote 失败：{detail}")

        raw: dict[str, str] = {}
        peeled: dict[str, str] = {}
        for line in (stdout or b"").decode("utf-8", "replace").splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            commit, ref = parts
            if not ref.startswith("refs/tags/"):
                continue
            name = ref[len("refs/tags/") :]
            if not name.startswith(TAG_PREFIX):
                continue
            version = name[len(TAG_PREFIX) :]
            if name.endswith("^{}"):
                peeled[version] = commit
            else:
                raw[version] = commit
        result: dict[str, dict[str, str]] = {}
        for version, commit in raw.items():
            if not re.match(r"^\d+\.\d+\.\d+", version):
                continue
            result[version] = {
                "tag": f"{TAG_PREFIX}{version}",
                "commit": peeled.get(version, commit),
            }
        return result

    async def check_update(self, channel: str = "stable") -> dict[str, Any]:
        tags = await self.git_tags()
        if not tags:
            raise RuntimeError("仓库没有可用的 dsh-v* tag")
        latest = _pick_version(list(tags), channel)
        state = _read_state()
        current = str(state.get("version") or "")
        info = {
            "channel": channel,
            "current": current,
            "latest": latest,
            "tag": tags[latest]["tag"],
            "commit": tags[latest]["commit"],
            "update_available": bool(
                latest and (not current or _version_key(latest) > _version_key(current))
            ),
            "checked_at": _utcnow(),
        }
        state["last_check"] = info
        _write_state(state)
        return info

    def status(self) -> dict[str, Any]:
        state = _read_state()
        version = self.installed_version()
        last = state.get("last_check") or {}
        latest = str(last.get("latest") or "")
        running = bool(
            self._install_task is not None and not self._install_task.done()
        )
        return {
            "installed": bool(version),
            "version": version or "",
            "tag": state.get("tag") or "",
            "commit": state.get("commit") or "",
            "channel": state.get("channel") or "stable",
            "home": str(HOME_DIR),
            "workspace": str(WORKSPACE_DIR),
            "runtime_dir": str(RUNTIME_DIR),
            "node": _node_exe() or "",
            "npm": _npm_exe() or "",
            "git": _git_exe() or "",
            "latest": latest,
            "latest_tag": last.get("tag") or "",
            "latest_commit": last.get("commit") or "",
            "update_available": bool(
                latest
                and version
                and _version_key(latest) > _version_key(version)
            ),
            "checked_at": last.get("checked_at") or "",
            "last_error": state.get("last_error") or "",
            "job": dict(self._install_job) if self._install_job else None,
            "installing": running,
        }

    # ----------------------------------------------------------- install
    async def _run(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout: float,
        log_path: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        self.log("$ " + " ".join(args))
        if os.name == "nt" and args and args[0].lower().endswith((".cmd", ".bat")):
            args = ["cmd.exe", "/c", *args]
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            raise RuntimeError(f"命令超时（{int(timeout)}s）：{args[0]}")
        text = (output or b"").decode("utf-8", "replace")
        if log_path is not None:
            try:
                log_path.write_text(text[-200_000:], encoding="utf-8")
            except Exception:
                pass
        for line in text.splitlines()[-20:]:
            self.log(line[:300])
        return subprocess.CompletedProcess(args, proc.returncode or 0, output, b"")

    async def verify(self, version: str) -> dict[str, Any]:
        node = _node_exe()
        bin_path = _bin_path(version)
        if not node:
            raise RuntimeError("未找到 Node.js（DSH 运行时需要 Node 24+）")
        if not bin_path.exists():
            raise RuntimeError(f"DSH {version} 文件不完整：{bin_path}")
        proc = await asyncio.create_subprocess_exec(
            node,
            str(bin_path),
            "--version",
            cwd=str(WORKSPACE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=VERIFY_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            with contextlib.suppress(Exception):
                proc.kill()
            raise RuntimeError("DSH --version 检查超时") from exc
        text = (stdout or b"").decode("utf-8", "replace").strip()
        err = (stderr or b"").decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            raise RuntimeError(f"DSH --version 失败：{(err or text)[:500]}")
        return {"ok": True, "output": text[:200], "version": version}

    async def install(
        self,
        version: str,
        *,
        tag: str = "",
        commit: str = "",
        channel: str = "stable",
    ) -> dict[str, Any]:
        node = _node_exe()
        npm = _npm_exe()
        if not node:
            raise RuntimeError("未找到 Node.js（DSH 运行时需要 Node 24+）")
        if not npm:
            raise RuntimeError("未找到 npm，无法安装 DeepSeek Harness")
        _ensure_dirs()
        target = RUNTIME_DIR / version
        if _bin_path(version).exists():
            await self.verify(version)
            self._activate(version, tag=tag, commit=commit, channel=channel)
            return {"installed": True, "version": version, "reused": True}

        temp = RUNTIME_DIR / f".install-{version}-{secrets.token_hex(4)}"
        temp.mkdir(parents=True, exist_ok=True)
        (temp / "package.json").write_text(
            json.dumps(
                {
                    "name": "qunban-dsh-runtime",
                    "private": True,
                    "version": "1.0.0",
                }
            ),
            encoding="utf-8",
        )
        log_path = LOG_DIR / f"install-{version}.log"
        try:
            result = await self._run(
                [
                    npm,
                    "install",
                    f"{NPM_PACKAGE}@{version}",
                    "--no-audit",
                    "--no-fund",
                    "--loglevel=error",
                ],
                cwd=temp,
                timeout=INSTALL_TIMEOUT_SECONDS,
                log_path=log_path,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"npm install {NPM_PACKAGE}@{version} 失败，见 {log_path}"
                )
            staged = temp
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            staged.replace(target)
            await self.verify(version)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        self._activate(version, tag=tag, commit=commit, channel=channel)
        return {"installed": True, "version": version, "reused": False}

    def _activate(
        self, version: str, *, tag: str, commit: str, channel: str
    ) -> None:
        state = _read_state()
        previous = {
            key: state.get(key)
            for key in ("version", "tag", "commit")
            if state.get(key)
        }
        state.update(
            {
                "version": version,
                "tag": tag,
                "commit": commit,
                "channel": channel,
                "installed_at": _utcnow(),
                "last_error": "",
            }
        )
        if previous and previous.get("version") != version:
            state["previous"] = previous
        _write_state(state)

    async def update(self, channel: str = "stable") -> dict[str, Any]:
        info = await self.check_update(channel)
        if not info["update_available"]:
            return {**info, "updated": False}
        await self.install(
            info["latest"],
            tag=info["tag"],
            commit=info["commit"],
            channel=channel,
        )
        return {**info, "updated": True}

    async def ensure_installed(self, channel: str = "stable") -> str:
        version = self.installed_version()
        if version:
            return version
        await self.update(channel)
        version = self.installed_version()
        if not version:
            raise RuntimeError("DeepSeek Harness 安装失败")
        return version

    # -------------------------------------------------------- background
    def start_update(self, channel: str = "stable") -> dict[str, Any]:
        if self._install_task is not None and not self._install_task.done():
            return {"started": False, "job": dict(self._install_job)}
        self._install_job = {
            "state": "running",
            "channel": channel,
            "started_at": _utcnow(),
            "log": [],
        }
        self._install_task = asyncio.create_task(
            self._background_update(channel), name="dsh-update"
        )
        return {"started": True, "job": dict(self._install_job)}

    async def _background_update(self, channel: str) -> None:
        job = self._install_job
        try:
            self.log(f"检查 {channel} 渠道更新…")
            info = await self.check_update(channel)
            if not info["update_available"]:
                job["state"] = "current"
                job["info"] = info
                self.log(f"已是最新版本 {info['current'] or '未安装'}（{info['latest']}）")
                return
            self.log(f"安装 {info['latest']}（{info['tag']}）…")
            job["version"] = info["latest"]
            result = await self.install(
                info["latest"],
                tag=info["tag"],
                commit=info["commit"],
                channel=channel,
            )
            job["state"] = "done"
            job["info"] = {**info, **result}
            self.log(f"完成：{info['latest']}")
            state = _read_state()
            state["last_error"] = ""
            _write_state(state)
        except Exception as exc:
            logger.exception("DSH 更新失败")
            job["state"] = "error"
            job["error"] = str(exc)
            self.log(f"失败：{exc}")
            state = _read_state()
            state["last_error"] = str(exc)[:500]
            _write_state(state)


_runtime: DSHRuntime | None = None


def get_dsh_runtime() -> DSHRuntime:
    global _runtime
    if _runtime is None:
        _runtime = DSHRuntime()
    return _runtime
