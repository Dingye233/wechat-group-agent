"""DeepSeek Harness as a peer agent runtime for the Operator and group tasks.

Why the headless profile instead of the SDK/long-running server:

* one task per process keeps a crash contained and needs no port;
* ``--json`` gives a stable newline-delimited event stream for progress;
* ``--session-id`` persists the conversation in ``$DSH_HOME/sessions`` so the
  next task resumes it, which maps directly onto the existing OpenCode session
  keys;
* there is nothing to keep alive between replies, so the tray/service
  lifecycle cannot leak a sidecar.

The process is started with a generated profile patch: ``agent-default-model``
points at the user's model, ``system-prompt`` carries the Operator/group system
prompt, and the profile itself mounts the qunban MCP server. The DeepSeek
Messages endpoint is the only provider the shipped DSH catalog serves; other
OpenAI-compatible endpoints fall back to OpenCode.
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
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from app.db import SessionLocal
from app.services.dsh_runtime import (
    DATA_DIR,
    HOME_DIR,
    PLUGIN_SOURCE_DIR,
    RUNTIME_DIR,
    SKILLS_DIR,
    WORKSPACE_DIR,
    DSHRuntime,
    _bin_path,
    _ensure_dirs,
    _node_exe,
    get_dsh_runtime,
)
from app.services.dsh_skills import sync_skills
from app.services.llm_usage import record_usage
from app.services.settings import get_settings

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SESSION_MAP_PATH = DATA_DIR / "sessions.json"
DEFAULT_TIMEOUT_SECONDS = 1800.0
DEFAULT_STALL_SECONDS = 600.0
ToolProgress = Callable[[dict[str, Any]], Awaitable[None]]


class DSHError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# provider / profile helpers
# ---------------------------------------------------------------------------


def deepseek_messages_base(base_url: str) -> str:
    """Map an OpenAI-compatible DeepSeek URL to its Messages root.

    DSH's shipped provider speaks the Anthropic-compatible Messages API at
    ``https://api.deepseek.com/anthropic`` and appends ``/v1/messages``. The
    qunban setting usually points at the OpenAI-compatible ``/v1`` root, which
    returns 404 for Messages requests, so translate the known DeepSeek hosts and
    leave everything else untouched (the caller gates those).
    """
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return "https://api.deepseek.com/anthropic"
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    if host in {"api.deepseek.com", "api.deepseek.com.cn"}:
        return "https://api.deepseek.com/anthropic"
    if parsed.path.rstrip("/").endswith("/anthropic"):
        return value
    return value


def provider_supported(base_url: str) -> bool:
    """The shipped profile only serves DeepSeek's Messages endpoint."""
    parsed = urlparse(str(base_url or "").strip())
    return parsed.netloc.lower() in {"api.deepseek.com", "api.deepseek.com.cn"}


def _profile_root(name: str) -> Path:
    return HOME_DIR / "profiles" / name


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _preserved_plugin_rows(profile: Path) -> list[dict[str, Any]]:
    """Keep imported/generated plugin rows across profile regeneration.

    ``_ensure_profile`` owns the built-in Qunban rows, but the Plugin Studio
    appends user plugins to the same patch file.  Those rows must survive every
    DSH task, otherwise an imported plugin silently disappears on the next run.
    """
    rows = _read_json(profile / "cordis.patch.yml", [])
    preserved: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return preserved
    managed = {
        "qunban-native",
        "skill-filesystem",
        "tool-pwsh",
        "tool-bash",
        "tool-terminal",
        "tool-workflow",
        "tool-subagent",
        "tool-subagent-control",
        "tool-subagent-list-agents",
        "tool-subagent-fork",
        "tool-fs",
        "tool-fs-search",
        "tool-web",
        "tool-jobs",
        "tool-todo",
        "tool-goal",
        "tool-working-directory",
        "tool-skill",
        "skill",
        "ptc-runtime",
        "workflow-ptc",
        "subagent",
        "subagent-spawn-in-process",
        "subagent-fork-in-process",
        "mcp-resources",
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        insert = row.get("insert")
        if isinstance(insert, list):
            for item in insert:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")
                if item.get("id") in managed or name in {
                    "@qunban/dsh-native",
                    "@deepseek-ai/dsh-mcp-client",
                }:
                    continue
                if name.startswith("@deepseek-ai/"):
                    continue
                preserved.append({"insert": [dict(item)]})
            continue
        name = str(row.get("name") or "")
        if row.get("id") in managed or name.startswith("@deepseek-ai/"):
            continue
        if name:
            preserved.append(dict(row))
    return preserved


def _session_key(profile: str, session_id: str) -> str:
    return f"{profile}:{session_id}"


def _load_session_map() -> dict[str, str]:
    try:
        data = json.loads(SESSION_MAP_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_session_map(data: dict[str, str]) -> None:
    _ensure_dirs()
    _write_json(SESSION_MAP_PATH, data)


def _safe_session_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "main")).strip("-")
    return cleaned[:120] or "main"


def _profile_patch(scope: str) -> list[dict[str, Any]]:
    """Compose the Qunban native plugin instead of an MCP wrapper.

    The plugin is a normal Cordis package mounted into the profile.  It exposes
    ``wechat_*`` / ``qunban_*`` tools that call the Qunban HTTP gateway; DSH and
    群伴 therefore share one process model, one session and one prompt prefix.
    The MCP bridge is intentionally gone: it added a child process, a second
    tool-name layer (``mcp__qunban__*``) and a large prompt prefix.
    """
    rows: list[dict[str, Any]] = []
    if scope != "chat":
        rows.extend(
            [
                {
                    "insert": [
                        {
                            "id": "qunban-native",
                            "name": "@qunban/dsh-native",
                        }
                    ]
                },
                {
                    "id": "skill-filesystem",
                    "config": {
                        "customSkillDirs": [str(SKILLS_DIR)],
                        "includeDefaultRoots": True,
                        "watch": True,
                    },
                },
            ]
        )
    # All shell access goes through qunban_powershell, whose gateway blocks
    # WeChat window manipulation.  Native shell/subagent tools are removed.
    rows.extend(
        [
            {"id": "tool-pwsh", "disabled": True},
            {"id": "tool-bash", "disabled": True},
            {"id": "tool-terminal", "disabled": True},
            {"id": "tool-workflow", "disabled": True},
            {"id": "tool-subagent", "disabled": True},
            {"id": "tool-subagent-control", "disabled": True},
            {"id": "tool-subagent-list-agents", "disabled": True},
            {"id": "tool-subagent-fork", "disabled": True},
        ]
    )
    if scope == "chat":
        # The chat lane is a pure text renderer: no tools, no skills, no file
        # access.  A smaller tool-free prompt is what keeps short replies cheap
        # and makes the stable prefix cacheable.
        for row in (
            "tool-fs",
            "tool-fs-search",
            "tool-web",
            "tool-jobs",
            "tool-todo",
            "tool-goal",
            "tool-working-directory",
            "tool-skill",
            "skill",
            "skill-filesystem",
            "ptc-runtime",
            "workflow-ptc",
            "subagent",
            "subagent-spawn-in-process",
            "subagent-fork-in-process",
            "mcp-resources",
        ):
            rows.append({"id": row, "disabled": True})
        return rows
    if scope == "assistant":
        return rows
    disabled = [
        "tool-fs",
        "tool-fs-search",
        "tool-web",
        "skill",
        "skill-filesystem",
        "ptc-runtime",
        "workflow-ptc",
        "subagent",
        "subagent-spawn-in-process",
        "subagent-fork-in-process",
    ]
    rows.extend({"id": row, "disabled": True} for row in disabled)
    return rows


def _ensure_native_plugin(profile: Path) -> None:
    """Copy the Qunban Cordis plugin into the profile's node_modules."""
    source = PLUGIN_SOURCE_DIR
    if not (source / "lib" / "index.js").exists():
        raise DSHError(
            f"缺少群伴 DSH 插件：{source}。请恢复 data/dsh/plugins/qunban-native。"
        )
    target = profile / "node_modules" / "@qunban" / "dsh-native"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)


def _ensure_profile(name: str, scope: str) -> Path:
    profile = _profile_root(name)
    profile.mkdir(parents=True, exist_ok=True)
    existing = _read_json(profile / "package.json", {})
    manifest = {
        "name": name,
        "private": True,
        "version": "1.0.0",
        "dsh": {
            "profile": {
                "bundles": [
                    "@deepseek-ai/dsh-base",
                    "@deepseek-ai/dsh-headless",
                ]
            }
        },
    }
    dependencies = existing.get("dependencies") if isinstance(existing, dict) else None
    if isinstance(dependencies, dict) and dependencies:
        manifest["dependencies"] = dependencies
    _write_json(profile / "package.json", manifest)
    rows = _profile_patch(scope)
    if scope == "assistant":
        rows.extend(_preserved_plugin_rows(profile))
    _write_json(profile / "cordis.patch.yml", rows)
    try:
        sync_skills()
    except Exception:
        logger.warning("同步群伴 skills 到 DSH 失败", exc_info=True)
    _ensure_native_plugin(profile)
    return profile


class DSHAgentRuntime:
    """Run one DSH headless task and translate its JSON stream."""

    def __init__(self) -> None:
        _ensure_dirs()
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._runtime: DSHRuntime = get_dsh_runtime()
        self._last_error = ""

    # ------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        base = self._runtime.status()
        return {
            "installed": base.get("installed", False),
            "version": base.get("version", ""),
            "running": len(self._processes),
            "sessions": len(_load_session_map()),
            "home": str(HOME_DIR),
            "workspace": str(WORKSPACE_DIR),
            "last_error": self._last_error or base.get("last_error", ""),
            "runtime": base,
        }

    async def stop(self) -> None:
        for key, process in list(self._processes.items()):
            await self._kill(process)
            self._processes.pop(key, None)

    # -------------------------------------------------------------- chat
    async def chat(
        self,
        message: str,
        *,
        session_id: str = "main",
        system_prompt: str = "",
        settings: dict[str, Any] | None = None,
        temperature: float | None = None,
        progress: ToolProgress | None = None,
        cancel_event: asyncio.Event | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        call_site: str = "dsh.operator",
        scope: str = "assistant",
        group_id: int = 0,
    ) -> dict[str, Any]:
        if settings is None:
            async with SessionLocal() as db:
                settings = await get_settings(db)
        version = self._runtime.installed_version()
        if not version:
            raise DSHError(
                "DeepSeek Harness 尚未安装。请在「助手 → DeepSeek Harness」里检查更新并安装。"
            )
        base_url = str(settings.get("llm_base_url") or "")
        if not provider_supported(base_url):
            raise DSHError(
                "当前模型接入不是 DeepSeek 官方 Messages 端点；DSH 引擎需要 "
                "https://api.deepseek.com。"
            )
        api_key = str(settings.get("llm_api_key") or "")
        if not api_key:
            raise DSHError("未配置 API Key")

        profile = {
            "assistant": "qunban",
            "chat": "qunban-chat",
        }.get(scope, "qunban-group")
        _ensure_profile(profile, scope)
        patch_path = DATA_DIR / f".patch-{secrets.token_hex(6)}.json"
        model = str(settings.get("llm_model") or "deepseek-chat")
        _write_json(
            patch_path,
            [
                {
                    "id": "agent-default-model",
                    "config": {"provider": "deepseek-official", "model": model},
                },
                {
                    "id": "system-prompt",
                    "config": {
                        "personaPrefix": str(system_prompt or "").strip()
                        or "你是群伴 Operator，一个可以操作本机的中文执行型 Agent。"
                    },
                },
            ],
        )
        key = _session_key(profile, session_id)
        mapping = _load_session_map()
        dsh_session = mapping.get(key, "")
        try:
            for attempt in range(2):
                result = await self._run_once(
                    message=message,
                    scope=scope,
                    profile=profile,
                    patch_path=patch_path,
                    dsh_session=dsh_session,
                    api_key=api_key,
                    base_url=deepseek_messages_base(base_url),
                    model=model,
                    key=key,
                    progress=progress,
                    cancel_event=cancel_event,
                    timeout=timeout,
                    call_site=call_site,
                    group_id=group_id,
                )
                if not result.get("session_missing"):
                    break
                # A persisted session can be gone after a manual cleanup; start
                # a fresh one and retry once.
                mapping.pop(key, None)
                _save_session_map(mapping)
                dsh_session = ""
        finally:
            with contextlib.suppress(Exception):
                patch_path.unlink()
        return result

    async def _run_once(
        self,
        *,
        message: str,
        scope: str,
        profile: str,
        patch_path: Path,
        dsh_session: str,
        api_key: str,
        base_url: str,
        model: str,
        key: str,
        progress: ToolProgress | None,
        cancel_event: asyncio.Event | None,
        timeout: float,
        call_site: str,
        group_id: int = 0,
    ) -> dict[str, Any]:
        node = _node_exe()
        if not node:
            raise DSHError("未找到 Node.js")
        bin_path = _bin_path(self._runtime.installed_version())
        if not bin_path.exists():
            raise DSHError("DSH 运行时文件缺失，请重新安装")
        args = [
            node,
            str(bin_path),
            "--profile",
            profile,
            "--patch",
            str(patch_path),
            "--json",
        ]
        if dsh_session:
            args.extend(["--session-id", dsh_session])
        args.append(str(message))

        env = os.environ.copy()
        from app.config import settings as process_settings
        from app.services.opencode_agent import tool_token

        env.update(
            {
                "DSH_HOME": str(HOME_DIR),
                "DEEPSEEK_API_KEY": api_key,
                "DEEPSEEK_BASE_URL": base_url,
                "DSH_PERMISSION_MODE": "danger-full-access"
                if scope == "assistant"
                else "read-only",
                "DSH_TELEMETRY_MODE": "DISABLED",
                "NO_COLOR": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                # Native Qunban plugin: one HTTP gateway, no MCP child process.
                "QUNBAN_API_BASE": f"http://127.0.0.1:{process_settings.port}",
                "QUNBAN_TOKEN": tool_token(),
                "QUNBAN_SCOPE": "admin" if scope == "assistant" else "group",
                "QUNBAN_GROUP_ID": str(group_id or ""),
                "QUNBAN_DSH_RUNTIME_ROOT": str(
                    RUNTIME_DIR / (self._runtime.installed_version() or "")
                ),
            }
        )
        # Do not leak a parent profile into the isolated home.
        env.pop("DSH_PROFILE", None)
        env.pop("DSH_PROFILE_DIR", None)

        if progress is not None:
            with contextlib.suppress(Exception):
                await progress(
                    {
                        "phase": "start",
                        "engine": "dsh",
                        "message": "DeepSeek Harness 已接管，开始执行",
                    }
                )

        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(WORKSPACE_DIR),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._processes[key] = process
        started = time.monotonic()
        last_output = started
        stderr_lines: deque[str] = deque(maxlen=40)
        stderr_task = asyncio.create_task(
            self._drain_stderr(process, stderr_lines), name="dsh-stderr"
        )
        final_text = ""
        error_text = ""
        session_event_id = ""
        trace: list[dict[str, Any]] = []
        pending: dict[str, dict[str, Any]] = {}
        usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_write_tokens": 0,
        }
        cancelled = False
        stalled = False
        try:
            assert process.stdout is not None
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    await self._kill(process)
                    break
                try:
                    line = await asyncio.wait_for(
                        process.stdout.readline(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if now - started > timeout:
                        await self._kill(process)
                        raise DSHError(
                            f"DSH 任务超过 {int(timeout // 60)} 分钟，已中止"
                        )
                    if now - last_output > DEFAULT_STALL_SECONDS:
                        await self._kill(process)
                        stalled = True
                        break
                    continue
                if not line:
                    break
                last_output = time.monotonic()
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except Exception:
                    logger.debug("DSH 非 JSON 输出：%s", text[:300])
                    continue
                kind = str(event.get("type") or "")
                if kind == "session":
                    session_event_id = str(event.get("sessionId") or "")
                    if session_event_id and not dsh_session:
                        mapping = _load_session_map()
                        mapping[key] = session_event_id
                        _save_session_map(mapping)
                elif kind == "status":
                    phase = str(event.get("phase") or "")
                    if phase == "step_end":
                        sample = event.get("usage") or {}
                        usage["prompt_tokens"] += int(
                            sample.get("inputTokens") or 0
                        )
                        usage["completion_tokens"] += int(
                            sample.get("outputTokens") or 0
                        )
                        usage["cache_hit_tokens"] += int(
                            sample.get("cacheReadTokens") or 0
                        )
                        usage["cache_write_tokens"] += int(
                            sample.get("cacheWriteTokens") or 0
                        )
                    if progress is not None and phase in {
                        "turn_start",
                        "step_start",
                        "step_end",
                    }:
                        with contextlib.suppress(Exception):
                            await progress(
                                {
                                    "phase": "thinking",
                                    "engine": "dsh",
                                    "step": event.get("step") or 0,
                                }
                            )
                elif kind == "tool_call":
                    call_id = str(event.get("callId") or "")
                    item = {
                        "tool": str(event.get("tool") or "tool"),
                        "args": event.get("input") or {},
                        "output": "",
                        "ok": True,
                    }
                    trace.append(item)
                    if call_id:
                        pending[call_id] = item
                    if progress is not None:
                        with contextlib.suppress(Exception):
                            await progress(
                                {
                                    "phase": "tool",
                                    "engine": "dsh",
                                    "tool": item["tool"],
                                    "args": item["args"],
                                    "thought": "",
                                }
                            )
                elif kind == "tool_result":
                    call_id = str(event.get("callId") or "")
                    item = pending.pop(call_id, None)
                    output = str(
                        event.get("result") or event.get("error") or ""
                    )
                    ok = str(event.get("status") or "") != "error"
                    if item is not None:
                        item["output"] = output[:12000]
                        item["ok"] = ok
                    if progress is not None:
                        with contextlib.suppress(Exception):
                            await progress(
                                {
                                    "phase": "tool_result",
                                    "engine": "dsh",
                                    "tool": str(
                                        event.get("tool")
                                        or (item or {}).get("tool")
                                        or "tool"
                                    ),
                                    "ok": ok,
                                    "message": output[:220],
                                }
                            )
                elif kind == "final":
                    final_text = str(event.get("text") or "")
                    reason = event.get("reason") or {}
                    if isinstance(reason, dict) and reason.get("kind") == "error":
                        error_text = json.dumps(
                            reason.get("error") or {}, ensure_ascii=False
                        )[:800]
                elif kind == "error":
                    error_text = str(event.get("message") or "DSH 运行失败")
                elif kind == "text" and not final_text:
                    # The terminal `final` repeats committed text; keep the last
                    # committed assistant text as a fallback.
                    final_text = str(event.get("text") or "")
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=10)
            stderr_task.cancel()
            with contextlib.suppress(Exception):
                await stderr_task
            self._processes.pop(key, None)

        stderr_tail = "\n".join(stderr_lines)[-2000:]
        returncode = int(process.returncode or 0)
        if cancelled:
            reply = "已按你的要求停止当前任务。"
        elif stalled:
            reply = (
                f"后台执行器超过 {int(DEFAULT_STALL_SECONDS)} 秒没有新进展，"
                "我已经自动中止，避免继续消耗 token。"
            )
        elif error_text or (returncode != 0 and not final_text):
            detail = error_text or stderr_tail or f"exit code {returncode}"
            missing = "does not exist" in detail and "session" in detail.lower()
            if missing:
                return {
                    "reply": "",
                    "session_missing": True,
                    "trace": trace,
                    "model": model,
                    "engine": "dsh",
                }
            self._last_error = detail[:500]
            raise DSHError(detail[:800])
        else:
            reply = final_text.strip() or "任务跑完了，但没有返回可读结果。"

        if usage["cache_hit_tokens"] > usage["prompt_tokens"]:
            # DSH reports ``inputTokens`` excluding the cache-read portion on
            # some providers; add it back so the hit rate stays <= 100%.
            usage["prompt_tokens"] += usage["cache_hit_tokens"]
        if usage["prompt_tokens"] or usage["completion_tokens"]:
            record_usage(
                call_site=call_site,
                model=model,
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                cache_hit_tokens=usage["cache_hit_tokens"],
                cache_miss_tokens=max(
                    usage["prompt_tokens"] - usage["cache_hit_tokens"], 0
                ),
                cache_prefix_chars=0,
                ok=not error_text,
            )
        return {
            "reply": reply,
            "trace": trace,
            "model": model,
            "engine": "dsh",
            "session_id": session_event_id or dsh_session,
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "cache_hit_tokens": usage["cache_hit_tokens"],
            "cancelled": cancelled,
            "stalled": stalled,
            "error": error_text,
        }

    async def _drain_stderr(
        self,
        process: asyncio.subprocess.Process,
        lines: deque[str],
    ) -> None:
        if process.stderr is None:
            return
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                lines.append(line.decode("utf-8", "replace").rstrip())
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _kill(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            with contextlib.suppress(Exception):
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(process.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
        with contextlib.suppress(Exception):
            process.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=10)

    # ------------------------------------------------------- sessions API
    async def abort(self, session_id: str = "main") -> bool:
        stopped = False
        for key in list(self._processes):
            if key.endswith(f":{session_id}"):
                await self._kill(self._processes[key])
                self._processes.pop(key, None)
                stopped = True
        return stopped

    async def reset_session(self, session_id: str = "main") -> None:
        mapping = _load_session_map()
        for key in list(mapping):
            if key.endswith(f":{session_id}"):
                mapping.pop(key, None)
        _save_session_map(mapping)

    async def sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = []
        for key, value in list(_load_session_map().items())[-limit:]:
            profile, _, session_id = key.partition(":")
            rows.append(
                {
                    "id": value,
                    "session_id": session_id,
                    "profile": profile,
                    "updated_at": "",
                }
            )
        return rows

    async def tools(self, settings: dict[str, Any] | None = None) -> list[dict]:
        return []

    async def plugins(self) -> list[dict]:
        return []

    async def mcp_status(self) -> dict[str, Any]:
        return {
            "qunban": {
                "configured": True,
                "transport": "stdio",
                "server": str(ROOT / "app" / "mcp_server.py"),
            }
        }


_dsh_agent: DSHAgentRuntime | None = None


def get_dsh_agent_runtime() -> DSHAgentRuntime:
    global _dsh_agent
    if _dsh_agent is None:
        _dsh_agent = DSHAgentRuntime()
    return _dsh_agent
