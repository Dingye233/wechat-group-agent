"""Official OpenCode integration.

Qunban does not implement its own agent loop for this path.  It starts the
upstream `opencode serve` process (https://github.com/anomalyco/opencode),
loads an isolated OpenCode configuration, exposes Qunban capabilities through
MCP, and mirrors OpenCode's native tool events into the Qunban UI.

The legacy loop in ``assistant.py`` remains as a fallback so an OpenCode
upgrade or a missing binary cannot take the whole group assistant down.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from app.db import SessionLocal
from app.services.settings import get_settings

logger = logging.getLogger("opencode")

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "opencode"
WORKSPACE = DATA_DIR / "workspace"
CONFIG_PATH = DATA_DIR / "opencode.json"
SESSIONS_PATH = DATA_DIR / "sessions.json"
TOKEN_PATH = DATA_DIR / "token"
LOG_PATH = DATA_DIR / "server.log"
ERR_PATH = DATA_DIR / "server.err.log"
PID_PATH = DATA_DIR / "server.pid"

_START_TIMEOUT = 30.0
_PROMPT_TIMEOUT = 1800.0

_BINARY_CANDIDATES = [
    DATA_DIR / "bin" / "opencode.exe",
    ROOT / "vendor" / "opencode" / "bin" / "opencode.exe",
    Path(r"D:\dev\nodejs\node_modules\opencode-ai\bin\opencode.exe"),
]


def _find_binary() -> Path | None:
    explicit = os.environ.get("OPENCODE_BIN", "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(_BINARY_CANDIDATES)
    found = shutil.which("opencode")
    if found:
        candidates.append(Path(found))
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size > 1_000_000:
                return path
        except OSError:
            continue
    return None


def _ensure_token() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.exists():
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_hex(24)
    TOKEN_PATH.write_text(token, encoding="utf-8")
    return token


def tool_token() -> str:
    return _ensure_token()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _clean(value: Any) -> str:
    return str(value or "").strip()


class OpenCodeError(RuntimeError):
    pass


class OpenCodeRuntime:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.port = 0
        self.url = ""
        self.last_error = ""
        self.started_at = 0.0
        self._event_task: asyncio.Task[None] | None = None
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._sessions: dict[str, str] = {}
        self._sessions_loaded = False
        self._lock = asyncio.Lock()
        self._tools_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._task_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._message_cache: dict[
            str, tuple[float, list[dict[str, Any]]]
        ] = {}

    # --------------------------------------------------------------- lifecycle
    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    async def status(self) -> dict[str, Any]:
        binary = _find_binary()
        result: dict[str, Any] = {
            "installed": binary is not None,
            "binary": str(binary) if binary else "",
            "running": self.running,
            "port": self.port,
            "url": self.url,
            "workspace": str(WORKSPACE),
            "config": str(CONFIG_PATH),
            "last_error": self.last_error,
            "source": "https://github.com/anomalyco/opencode",
        }
        if binary:
            try:
                result["version"] = await asyncio.to_thread(
                    self._version, binary
                )
            except Exception:
                result["version"] = ""
        if self.running:
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    response = await client.get(f"{self.url}/config")
                if response.status_code == 200:
                    result["config_ok"] = True
            except Exception as exc:
                result["config_ok"] = False
                result["last_error"] = str(exc)
        try:
            result["sessions"] = len(self._load_sessions())
        except Exception:
            result["sessions"] = 0
        return result

    @staticmethod
    def _version(binary: Path) -> str:
        try:
            proc = subprocess.run(
                [str(binary), "--version"],
                capture_output=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            text = (proc.stdout or proc.stderr or b"").decode("utf-8", "replace")
            return text.strip().splitlines()[-1][:80] if text.strip() else ""
        except Exception:
            return ""

    async def ensure_started(
        self, settings: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with self._lock:
            if self.running:
                return {"url": self.url, "port": self.port}
            binary = _find_binary()
            if binary is None:
                raise OpenCodeError(
                    "未找到官方 OpenCode 可执行文件。请运行 scripts/setup-opencode.ps1，"
                    "或设置 OPENCODE_BIN 指向 opencode.exe。"
                )
            if settings is None:
                async with SessionLocal() as db:
                    settings = await get_settings(db)
            if not (
                _clean(settings.get("llm_base_url"))
                and _clean(settings.get("llm_api_key"))
                and _clean(settings.get("llm_model"))
            ):
                raise OpenCodeError(
                    "请先在「设置」中配置群伴自己的模型 API：Base URL、API Key 和模型名。"
                )
            self._prepare_dirs()
            token = _ensure_token()
            self.port = _free_port()
            self.url = f"http://127.0.0.1:{self.port}"
            self._write_config(settings, token)
            env = os.environ.copy()
            env.update(
                {
                    "OPENCODE_DB": str(DATA_DIR / "opencode.db"),
                    "XDG_DATA_HOME": str(DATA_DIR / "xdg" / "data"),
                    "XDG_CONFIG_HOME": str(DATA_DIR / "xdg" / "config"),
                    "XDG_STATE_HOME": str(DATA_DIR / "xdg" / "state"),
                    "XDG_CACHE_HOME": str(DATA_DIR / "xdg" / "cache"),
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                }
            )
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            out = LOG_PATH.open("ab")
            err = ERR_PATH.open("ab")
            self.last_error = ""
            try:
                self.process = subprocess.Popen(
                    [
                        str(binary),
                        "serve",
                        "--port",
                        str(self.port),
                        "--hostname",
                        "127.0.0.1",
                    ],
                    cwd=str(WORKSPACE),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            finally:
                out.close()
                err.close()
            try:
                PID_PATH.write_text(str(self.process.pid), encoding="utf-8")
            except Exception:
                pass
            deadline = time.monotonic() + _START_TIMEOUT
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    self.last_error = self._tail_errors()
                    raise OpenCodeError(
                        "官方 OpenCode 启动失败：" + (self.last_error or "进程已退出")
                    )
                try:
                    async with httpx.AsyncClient(timeout=3) as client:
                        response = await client.get(f"{self.url}/config")
                    if response.status_code == 200:
                        self.started_at = time.time()
                        self._event_task = asyncio.create_task(
                            self._event_reader(), name="opencode-events"
                        )
                        return {"url": self.url, "port": self.port}
                except Exception:
                    pass
                await asyncio.sleep(0.4)
            self.last_error = self._tail_errors()
            await self.stop()
            raise OpenCodeError(
                "官方 OpenCode 在 30 秒内没有就绪：" + (self.last_error or "超时")
            )

    def _tail_errors(self) -> str:
        try:
            text = ERR_PATH.read_text(encoding="utf-8", errors="replace")
            text += "\n" + LOG_PATH.read_text(encoding="utf-8", errors="replace")
            lines = [line for line in text.splitlines() if line.strip()]
            return "\n".join(lines[-12:])[:4000]
        except Exception:
            return ""

    async def stop(self) -> None:
        if self._event_task is not None:
            self._event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_task
            self._event_task = None
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                with contextlib.suppress(Exception):
                    self.process.wait(timeout=5)
        self.process = None
        self.port = 0
        self.url = ""
        self._task_cache = (0.0, [])
        self._message_cache.clear()
        with contextlib.suppress(Exception):
            PID_PATH.unlink()

    async def restart(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.stop()
        return await self.ensure_started(settings)

    def _prepare_dirs(self) -> None:
        for path in (
            DATA_DIR,
            WORKSPACE,
            DATA_DIR / "xdg" / "data",
            DATA_DIR / "xdg" / "config",
            DATA_DIR / "xdg" / "state",
            DATA_DIR / "xdg" / "cache",
            DATA_DIR / "plugins",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _write_config(self, settings: dict[str, Any], token: str) -> None:
        self._prepare_dirs()
        base_url = _clean(settings.get("llm_base_url"))
        api_key = _clean(settings.get("llm_api_key"))
        model = _clean(settings.get("llm_model"))
        provider_id = "qunban"
        qunban_api = os.environ.get("QUNBAN_API_BASE", "http://127.0.0.1:8000")
        plugin_entries: list[Any] = []
        plugins_dir = DATA_DIR / "plugins"
        for child in sorted(plugins_dir.iterdir(), key=lambda p: p.name):
            if child.is_dir() and (
                (child / "package.json").exists()
                or (child / "index.ts").exists()
                or (child / "server.ts").exists()
            ):
                plugin_entries.append(str(child))
        config: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json",
            "model": f"{provider_id}/{model}",
            "default_agent": "build",
            "provider": {
                provider_id: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Qunban Provider",
                    "options": {"baseURL": base_url, "apiKey": api_key},
                    "models": {
                        model: {
                            "name": model,
                            "tool_call": True,
                            "temperature": True,
                            "limit": {"context": 128000, "output": 16384},
                        }
                    },
                }
            },
            "agent": {
                "build": {
                    "prompt": (
                        "你是群伴 Operator，由官方 OpenCode 运行。"
                        "直接使用工具完成任务，不要输出自定义 JSON 动作协议。"
                        "最终用简洁中文说明真实结果。"
                    ),
                    "tools": {
                        "bash": True,
                        "edit": True,
                        "write": True,
                        "patch": True,
                        "task": True,
                    },
                }
            },
            "permission": {
                "read": "allow",
                "edit": "allow",
                "glob": "allow",
                "grep": "allow",
                "list": "allow",
                "bash": "allow",
                "task": "allow",
                "external_directory": "allow",
                "todowrite": "allow",
                "question": "allow",
                "webfetch": "allow",
                "websearch": "allow",
                "lsp": "allow",
                "doom_loop": "allow",
                "skill": "allow",
            },
            "mcp": {
                "qunban": {
                    "type": "local",
                    "command": [sys.executable, str(ROOT / "app" / "mcp_server.py")],
                    "cwd": str(ROOT),
                    "environment": {
                        "QUNBAN_API_BASE": qunban_api,
                        "QUNBAN_OPENCODE_TOKEN": token,
                        "QUNBAN_SCOPE": "admin",
                        "PYTHONIOENCODING": "utf-8",
                        "PYTHONUTF8": "1",
                    },
                    "enabled": True,
                    "timeout": 30000,
                }
            },
            "plugin": plugin_entries,
            "share": "disabled",
            "autoupdate": False,
        }
        CONFIG_PATH.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------------------------------------------------------------- sessions
    def _load_sessions(self) -> dict[str, str]:
        if self._sessions_loaded:
            return self._sessions
        self._sessions_loaded = True
        try:
            data = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._sessions = {
                    str(k): str(v) for k, v in data.items() if str(v).startswith("ses")
                }
        except Exception:
            self._sessions = {}
        return self._sessions

    def _save_sessions(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SESSIONS_PATH.write_text(
            json.dumps(self._sessions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _ensure_session(
        self, client: httpx.AsyncClient, session_id: str, settings: dict[str, Any]
    ) -> str:
        sessions = self._load_sessions()
        existing = sessions.get(session_id)
        if existing:
            response = await client.get(f"{self.url}/session/{existing}")
            if response.status_code == 200:
                return existing
        response = await client.post(
            f"{self.url}/session",
            json={"title": f"群伴 {session_id}"},
        )
        if response.status_code != 200:
            raise OpenCodeError(
                f"OpenCode 创建会话失败（{response.status_code}）：{response.text[:1000]}"
            )
        sid = str(response.json().get("id") or "")
        if not sid.startswith("ses"):
            raise OpenCodeError("OpenCode 返回了无效的会话 ID")
        sessions[session_id] = sid
        self._save_sessions()
        return sid

    async def reset_session(self, session_id: str = "main") -> None:
        sessions = self._load_sessions()
        sid = sessions.pop(session_id, None)
        self._save_sessions()
        self._task_cache = (0.0, [])
        self._message_cache.clear()
        if sid and self.running:
            with contextlib.suppress(Exception):
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.delete(f"{self.url}/session/{sid}")

    # ----------------------------------------------------------------- events
    async def _event_reader(self) -> None:
        while self.running:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        "GET",
                        f"{self.url}/event",
                        params={"directory": str(WORKSPACE)},
                    ) as response:
                        if response.status_code != 200:
                            await asyncio.sleep(1)
                            continue
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            try:
                                event = json.loads(line[5:].strip())
                            except Exception:
                                continue
                            raw_data = event.get("data")
                            raw_props = event.get("properties")
                            payload = {
                                **(raw_data if isinstance(raw_data, dict) else {}),
                                **(
                                    raw_props
                                    if isinstance(raw_props, dict)
                                    else {}
                                ),
                            }
                            sid = str(payload.get("sessionID") or "")
                            queue = self._queues.get(sid)
                            if queue is not None:
                                with contextlib.suppress(asyncio.QueueFull):
                                    queue.put_nowait(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("OpenCode 事件流断开：%s", exc)
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------- chat
    async def chat(
        self,
        message: str,
        *,
        session_id: str = "main",
        system_prompt: str = "",
        settings: dict[str, Any] | None = None,
        temperature: float | None = None,
        progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        cancel_event: asyncio.Event | None = None,
        timeout: float = _PROMPT_TIMEOUT,
        permission_handler: (
            Callable[[dict[str, Any]], Awaitable[str]] | None
        ) = None,
        question_handler: (
            Callable[[dict[str, Any]], Awaitable[list[list[str]]]] | None
        ) = None,
    ) -> dict[str, Any]:
        if settings is None:
            async with SessionLocal() as db:
                settings = await get_settings(db)
        await self.ensure_started(settings)
        model = _clean(settings.get("llm_model"))
        async with httpx.AsyncClient(timeout=30) as client:
            sid = await self._ensure_session(client, session_id, settings)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._queues[sid] = queue
        while not queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()

        started_ms = int(time.time() * 1000)
        started = time.monotonic()
        last_event_at = started
        try:
            raw_stall = settings.get("opencode_stall_timeout_seconds")
            stall_timeout = (
                600.0 if raw_stall in (None, "") else float(raw_stall)
            )
        except (TypeError, ValueError):
            stall_timeout = 600.0
        if stall_timeout > 0:
            stall_timeout = max(60.0, min(3600.0, stall_timeout))
        last_request_poll = 0.0
        texts: dict[str, str] = {}
        tool_states: dict[str, str] = {}
        trace: list[dict[str, Any]] = []
        error = ""
        cancelled = False
        timed_out = False
        stalled = False

        async def emit(event: dict[str, Any]) -> None:
            if progress is None:
                return
            try:
                await progress(event)
            except Exception:
                pass

        payload: dict[str, Any] = {
            "parts": [{"type": "text", "text": message}],
            "model": {"providerID": "qunban", "modelID": model},
        }
        if system_prompt:
            payload["system"] = system_prompt
        if temperature is not None:
            payload["options"] = {"temperature": float(temperature)}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.url}/session/{sid}/prompt_async", json=payload
                )
            if response.status_code not in (200, 202, 204):
                raise OpenCodeError(
                    f"OpenCode 接受任务失败（{response.status_code}）：{response.text[:1500]}"
                )
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    await self.abort(session_id)
                    break
                if time.monotonic() - started > timeout:
                    timed_out = True
                    await self.abort(session_id)
                    break
                if (
                    stall_timeout > 0
                    and time.monotonic() - last_event_at > stall_timeout
                ):
                    stalled = True
                    await self.abort(session_id)
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if time.monotonic() - last_request_poll >= 2.0:
                        last_request_poll = time.monotonic()
                        await self._drain_pending_requests(
                            sid, emit, permission_handler, question_handler
                        )
                    continue
                last_event_at = time.monotonic()
                etype = str(event.get("type") or "")
                raw_data = event.get("data")
                raw_props = event.get("properties")
                props = {
                    **(raw_data if isinstance(raw_data, dict) else {}),
                    **(raw_props if isinstance(raw_props, dict) else {}),
                }
                if etype in {
                    "permission.asked",
                    "permission.v2.asked",
                    "permission.updated",
                }:
                    await self._handle_permission(
                        props, emit, permission_handler
                    )
                    continue
                if etype in {"question.asked", "question.v2.asked"}:
                    await self._handle_question(
                        props, emit, question_handler
                    )
                    continue
                if etype == "message.part.updated":
                    part = props.get("part") or {}
                    ptype = str(part.get("type") or "")
                    if ptype == "tool":
                        call_id = str(part.get("callID") or part.get("id") or "")
                        state = part.get("state") or {}
                        status = str(state.get("status") or "")
                        tool = str(part.get("tool") or "tool")
                        previous = tool_states.get(call_id)
                        if status == "running" and previous != "running":
                            tool_states[call_id] = "running"
                            await emit(
                                {
                                    "phase": "tool",
                                    "tool": tool,
                                    "thought": self._describe_input(state.get("input")),
                                    "input": state.get("input") or {},
                                }
                            )
                        elif status == "completed":
                            tool_states[call_id] = "completed"
                            entry = {
                                "tool": tool,
                                "input": state.get("input") or {},
                                "output": str(state.get("output") or "")[:12000],
                                "title": str(state.get("title") or ""),
                                "ok": True,
                            }
                            trace.append(entry)
                            await emit(
                                {
                                    "phase": "tool_result",
                                    "tool": tool,
                                    "ok": True,
                                    "message": str(state.get("title") or "")[:200],
                                    "output": entry["output"],
                                }
                            )
                        elif status == "error":
                            tool_states[call_id] = "error"
                            entry = {
                                "tool": tool,
                                "input": state.get("input") or {},
                                "output": str(state.get("error") or "")[:12000],
                                "ok": False,
                            }
                            trace.append(entry)
                            await emit(
                                {
                                    "phase": "tool_result",
                                    "tool": tool,
                                    "ok": False,
                                    "message": entry["output"][:300],
                                }
                            )
                    elif ptype == "text":
                        text = str(part.get("text") or "")
                        if text:
                            texts[str(part.get("id") or "")] = text
                elif etype == "session.error":
                    err = props.get("error") or {}
                    error = str(err.get("message") or err.get("name") or err)
                elif etype == "session.idle":
                    break
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self.abort(session_id)
            raise
        except Exception as exc:
            error = str(exc)
        finally:
            self._queues.pop(sid, None)

        if cancelled:
            return {
                "reply": "已按你的要求停止当前任务。",
                "trace": trace,
                "model": model,
                "cancelled": True,
                "error": "",
            }
        if stalled:
            return {
                "reply": (
                    f"后台任务超过 {int(stall_timeout)} 秒没有新进展，"
                    "我已经自动中止，避免继续消耗 token。"
                ),
                "trace": trace,
                "model": model,
                "stalled": True,
                "error": "",
            }
        if timed_out:
            return {
                "reply": (
                    f"任务已运行超过 {int(timeout // 60)} 分钟，我先停下来，"
                    "避免无限循环。你可以发「继续」让我接着做。"
                ),
                "trace": trace,
                "model": model,
                "timed_out": True,
                "error": "",
            }

        reply, usage, final_trace = await self._read_final(sid, started_ms, texts)
        if final_trace:
            trace = final_trace
        if not reply and error:
            raise OpenCodeError(error)
        if not reply:
            reply = "OpenCode 没有返回文本结果，请重试或把任务拆小一点。"
        return {
            "reply": reply,
            "trace": trace,
            "model": model,
            "error": error,
            **usage,
        }

    async def _drain_pending_requests(
        self,
        session_id: str,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        permission_handler: (
            Callable[[dict[str, Any]], Awaitable[str]] | None
        ),
        question_handler: (
            Callable[[dict[str, Any]], Awaitable[list[list[str]]]] | None
        ),
    ) -> None:
        """Fallback poll for permission/question requests missed by events."""

        try:
            async with httpx.AsyncClient(timeout=8) as client:
                for path, kind in (
                    ("/permission", "permission"),
                    ("/question", "question"),
                ):
                    response = await client.get(
                        f"{self.url}{path}",
                        params={"directory": str(WORKSPACE)},
                    )
                    if response.status_code != 200:
                        continue
                    rows = response.json()
                    for row in rows if isinstance(rows, list) else []:
                        if not isinstance(row, dict):
                            continue
                        if str(row.get("sessionID") or "") != session_id:
                            continue
                        if kind == "permission":
                            await self._handle_permission(
                                row, emit, permission_handler
                            )
                        else:
                            await self._handle_question(
                                row, emit, question_handler
                            )
        except Exception as exc:
            logger.debug("OpenCode pending request poll failed: %s", exc)

    async def _handle_permission(
        self,
        payload: dict[str, Any],
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        handler: Callable[[dict[str, Any]], Awaitable[str]] | None,
    ) -> None:
        request_id = str(
            payload.get("id")
            or payload.get("requestID")
            or payload.get("permissionID")
            or ""
        ).strip()
        session_id = str(payload.get("sessionID") or "").strip()
        permission = str(
            payload.get("permission")
            or payload.get("action")
            or "permission"
        )
        patterns = payload.get("patterns") or payload.get("resources") or []
        decision = "always"
        if handler is not None:
            try:
                candidate = str(await handler(payload) or "").strip()
                if candidate in {"once", "always", "reject"}:
                    decision = candidate
            except Exception as exc:
                logger.debug("OpenCode permission handler failed: %s", exc)
        ok = False
        if request_id.startswith("per"):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(
                        f"{self.url}/permission/{request_id}/reply",
                        json={"reply": decision},
                        params={"directory": str(WORKSPACE)},
                    )
                    if response.status_code != 200 and session_id:
                        response = await client.post(
                            f"{self.url}/session/{session_id}/permissions/{request_id}",
                            json={"response": decision},
                            params={"directory": str(WORKSPACE)},
                        )
                ok = response.status_code == 200
            except Exception as exc:
                logger.debug("OpenCode permission reply failed: %s", exc)
        await emit(
            {
                "phase": "permission",
                "tool": permission,
                "patterns": patterns,
                "reply": decision,
                "ok": ok,
            }
        )

    async def _handle_question(
        self,
        payload: dict[str, Any],
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        handler: Callable[[dict[str, Any]], Awaitable[list[list[str]]]] | None,
    ) -> None:
        request_id = str(
            payload.get("id") or payload.get("requestID") or ""
        ).strip()
        questions = payload.get("questions") or []
        answers: list[list[str]] = []
        if handler is not None:
            try:
                candidate = await handler(payload)
                if isinstance(candidate, list):
                    answers = [
                        [str(item) for item in row]
                        if isinstance(row, (list, tuple))
                        else [str(row)]
                        for row in candidate
                    ]
            except Exception as exc:
                logger.debug("OpenCode question handler failed: %s", exc)
        if not answers:
            answers = self._default_question_answers(questions)
        ok = False
        if request_id.startswith("que"):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(
                        f"{self.url}/question/{request_id}/reply",
                        json={"answers": answers},
                        params={"directory": str(WORKSPACE)},
                    )
                ok = response.status_code == 200
            except Exception as exc:
                logger.debug("OpenCode question reply failed: %s", exc)
        await emit(
            {
                "phase": "question",
                "questions": questions,
                "answers": answers,
                "ok": ok,
            }
        )

    @staticmethod
    def _default_question_answers(questions: Any) -> list[list[str]]:
        answers: list[list[str]] = []
        for item in questions if isinstance(questions, list) else []:
            if not isinstance(item, dict):
                continue
            labels: list[str] = []
            for option in item.get("options") or []:
                if isinstance(option, dict):
                    label = str(
                        option.get("label")
                        or option.get("value")
                        or option.get("text")
                        or ""
                    ).strip()
                else:
                    label = str(option).strip()
                if label:
                    labels.append(label)
            preferred = ""
            for label in labels:
                low = label.lower()
                if (
                    any(
                        word in label
                        for word in ("继续", "允许", "确定", "同意", "好的")
                    )
                    or low
                    in {
                        "yes",
                        "allow",
                        "approve",
                        "always",
                        "continue",
                        "ok",
                    }
                ):
                    preferred = label
                    break
            if preferred:
                answers.append([preferred])
            elif labels:
                answers.append([labels[0]])
            else:
                answers.append(["按你的最佳判断继续"])
        return answers

    @staticmethod
    def _describe_input(value: Any) -> str:
        if not isinstance(value, dict) or not value:
            return ""
        for key in (
            "name",
            "command",
            "description",
            "query",
            "keyword",
            "filePath",
            "path",
            "text",
        ):
            item = value.get(key)
            if item:
                return str(item).replace("\n", " ")[:140]
        try:
            return json.dumps(value, ensure_ascii=False)[:140]
        except Exception:
            return ""

    async def _read_final(
        self,
        sid: str,
        started_ms: int,
        fallback_texts: dict[str, str],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        usage: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        parts_text: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f"{self.url}/session/{sid}/message")
            rows = response.json() if response.status_code == 200 else []
            for row in rows if isinstance(rows, list) else []:
                info = row.get("info") or {}
                if info.get("role") != "assistant":
                    continue
                created = int((info.get("time") or {}).get("created") or 0)
                if created and created < started_ms - 2000:
                    continue
                for part in row.get("parts") or []:
                    if part.get("type") == "text":
                        text = str(part.get("text") or "").strip()
                        if text:
                            parts_text.append(text)
                    elif part.get("type") == "tool":
                        state = part.get("state") or {}
                        trace.append(
                            {
                                "tool": str(part.get("tool") or "tool"),
                                "input": state.get("input") or {},
                                "output": str(
                                    state.get("output") or state.get("error") or ""
                                )[:12000],
                                "title": str(state.get("title") or ""),
                                "ok": str(state.get("status") or "") != "error",
                            }
                        )
                tokens = info.get("tokens") or {}
                if tokens:
                    usage = {
                        "prompt_tokens": int(tokens.get("input") or 0),
                        "completion_tokens": int(tokens.get("output") or 0),
                    }
        except Exception as exc:
            logger.debug("读取 OpenCode 最终消息失败：%s", exc)
        if parts_text:
            reply = parts_text[-1] if len(parts_text) > 1 else parts_text[0]
            if len(parts_text) > 1 and len(parts_text[-1]) < 20:
                reply = "\n\n".join(parts_text)
        else:
            reply = "\n".join(
                text for text in fallback_texts.values() if str(text).strip()
            ).strip()
        return reply, usage, trace

    async def abort(self, session_id: str) -> bool:
        sessions = self._load_sessions()
        sid = sessions.get(session_id)
        if not sid or not self.running:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(f"{self.url}/session/{sid}/abort")
            return response.status_code == 200
        except Exception:
            return False

    async def sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        if not self.running:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{self.url}/session",
                    params={"directory": str(WORKSPACE)},
                )
            rows = response.json() if response.status_code == 200 else []
        except Exception as exc:
            logger.debug("OpenCode session list failed: %s", exc)
            return []
        rows = [row for row in rows if isinstance(row, dict)]
        rows.sort(
            key=lambda row: int((row.get("time") or {}).get("updated") or 0),
            reverse=True,
        )
        return rows[: max(1, int(limit))]

    async def session_messages(
        self, session_id: str, limit: int = 80
    ) -> list[dict[str, Any]]:
        now = time.monotonic()
        cached = self._message_cache.get(session_id)
        if cached and now - cached[0] < 5.0:
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{self.url}/session/{session_id}/message",
                    params={"directory": str(WORKSPACE)},
                )
            rows = response.json() if response.status_code == 200 else []
        except Exception as exc:
            logger.debug("OpenCode session messages failed: %s", exc)
            rows = []
        rows = [row for row in rows if isinstance(row, dict)]
        rows = rows[-max(1, int(limit)) :]
        self._message_cache[session_id] = (now, rows)
        return rows

    @staticmethod
    def _summarize_session(
        session: dict[str, Any], messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        title = str(session.get("title") or "")
        user_text = ""
        assistant_text = ""
        tool_count = 0
        last_role = ""
        last_completed = True
        for row in messages:
            info = row.get("info") or {}
            role = str(info.get("role") or "")
            parts = row.get("parts") or []
            texts = [
                str(part.get("text") or "").strip()
                for part in parts
                if part.get("type") == "text"
                and str(part.get("text") or "").strip()
            ]
            if role == "user":
                last_role = "user"
                if texts:
                    user_text = "\n".join(texts)
            elif role == "assistant":
                last_role = "assistant"
                tool_count += sum(
                    1 for part in parts if part.get("type") == "tool"
                )
                if texts:
                    assistant_text = "\n".join(texts)
                last_completed = bool(
                    (info.get("time") or {}).get("completed")
                )
        if not messages:
            status = "idle"
        elif last_role == "user":
            status = "running"
        elif last_role == "assistant" and not last_completed:
            status = "running"
        else:
            status = "done"
        updated_ms = int((session.get("time") or {}).get("updated") or 0)
        updated_at = (
            datetime.fromtimestamp(
                updated_ms / 1000, timezone.utc
            ).isoformat()
            if updated_ms
            else ""
        )
        return {
            "session_id": str(session.get("id") or ""),
            "title": title,
            "label": title or "OpenCode 任务",
            "status": status,
            "last_user": user_text[-800:],
            "last_assistant": assistant_text[-1000:],
            "tool_count": tool_count,
            "updated_at": updated_at,
            "tokens": session.get("tokens") or {},
        }

    async def task_snapshots(
        self, limit: int = 12, cache_seconds: float = 4.0
    ) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._task_cache[0] and now - self._task_cache[0] < cache_seconds:
            return self._task_cache[1]
        sessions = await self.sessions(limit=max(int(limit), 12))
        sessions = [
            session
            for session in sessions
            if str(session.get("title") or "").startswith("群伴")
            or "group-task-" in str(session.get("title") or "")
        ]
        semaphore = asyncio.Semaphore(5)

        async def one(session: dict[str, Any]) -> dict[str, Any] | None:
            session_id = str(session.get("id") or "")
            if not session_id:
                return None
            async with semaphore:
                messages = await self.session_messages(session_id)
            return self._summarize_session(session, messages)

        rows = await asyncio.gather(
            *(one(session) for session in sessions),
            return_exceptions=True,
        )
        tasks = [row for row in rows if isinstance(row, dict)]
        self._task_cache = (now, tasks)
        return tasks

    # ------------------------------------------------------------- inspection
    async def tools(self, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        now = time.monotonic()
        if now - self._tools_cache[0] < 15 and self._tools_cache[1]:
            return self._tools_cache[1]
        if not self.running:
            return []
        if settings is None:
            async with SessionLocal() as db:
                settings = await get_settings(db)
        model = _clean(settings.get("llm_model"))
        tools: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                ids = await client.get(
                    f"{self.url}/experimental/tool/ids",
                    params={"directory": str(WORKSPACE)},
                )
                if ids.status_code == 200:
                    payload = ids.json()
                    for item in payload if isinstance(payload, list) else []:
                        tools.append(
                            {
                                "id": str(item),
                                "name": str(item),
                                "description": "",
                                "source": "opencode",
                            }
                        )
                detail = await client.get(
                    f"{self.url}/experimental/tool",
                    params={
                        "directory": str(WORKSPACE),
                        "provider": "qunban",
                        "model": model,
                    },
                )
                if detail.status_code == 200:
                    payload = detail.json()
                    rows = payload if isinstance(payload, list) else []
                    by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}
                    for item in tools:
                        row = by_id.get(item["id"])
                        if row:
                            item["description"] = str(row.get("description") or "")
                            item["parameters"] = row.get("parameters") or {}
                    known = {item["id"] for item in tools}
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        if row.get("id") not in known:
                            tools.append(
                                {
                                    "id": str(row.get("id") or ""),
                                    "name": str(row.get("name") or row.get("id") or ""),
                                    "description": str(row.get("description") or ""),
                                    "parameters": row.get("parameters") or {},
                                    "source": "opencode",
                                }
                            )
        except Exception as exc:
            logger.debug("读取 OpenCode 工具失败：%s", exc)
        self._tools_cache = (now, tools)
        return tools

    async def plugins(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for item in config.get("plugin") or []:
                if isinstance(item, dict):
                    rows.append(
                        {
                            "id": str(item.get("package") or item),
                            "source": "config",
                            "options": item.get("options") or {},
                        }
                    )
                else:
                    rows.append({"id": str(item), "source": "config"})
        except Exception:
            pass
        plugins_dir = DATA_DIR / "plugins"
        if plugins_dir.exists():
            for child in sorted(plugins_dir.iterdir(), key=lambda p: p.name):
                if not child.is_dir():
                    continue
                if child.name in {row["id"] for row in rows}:
                    continue
                rows.append({"id": child.name, "source": "local", "path": str(child)})
        return rows

    async def refresh_mcp(self, name: str = "qunban") -> dict[str, Any]:
        """Ask OpenCode to reconnect an MCP server and refresh its tool list."""
        if not self.running:
            return {"ok": False, "error": "OpenCode 未运行"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                disconnected = await client.post(
                    f"{self.url}/mcp/{name}/disconnect"
                )
                connected = await client.post(f"{self.url}/mcp/{name}/connect")
            return {
                "ok": disconnected.status_code == 200
                and connected.status_code == 200,
                "disconnect": disconnected.status_code,
                "connect": connected.status_code,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def mcp_status(self) -> dict[str, Any]:
        if not self.running:
            return {}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{self.url}/mcp", params={"directory": str(WORKSPACE)}
                )
            return response.json() if response.status_code == 200 else {}
        except Exception:
            return {}


_RUNTIME: OpenCodeRuntime | None = None


def get_opencode_runtime() -> OpenCodeRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = OpenCodeRuntime()
    return _RUNTIME
