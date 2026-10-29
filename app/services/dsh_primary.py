"""Persistent DeepSeek Harness ACP service for the primary group channel.

One long-lived DSH ACP process owns the group sessions.  Qunban writes a prompt
over JSON-RPC stdio, receives semantic updates, and sends the resulting reply
through the existing wechatauto transport.  The service deliberately does not
expose ``wechat_send_message`` in the primary toolset: Qunban remains the only
writer to WeChat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import settings as process_settings
from app.services import plugin_policy
from app.services.dsh_agent import (
    _ensure_native_plugin,
    _preserved_plugin_rows,
    _profile_patch,
    _write_json,
    deepseek_messages_base,
)
from app.services.dsh_runtime import (
    HOME_DIR,
    RUNTIME_DIR,
    WORKSPACE_DIR,
    _bin_path,
    _node_exe,
    get_dsh_runtime,
)
from app.services.opencode_agent import tool_token

logger = logging.getLogger(__name__)

PROFILE = "qunban-primary"
PROFILE_DIR = HOME_DIR / "profiles" / PROFILE
ACP_PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT = 180.0

Progress = Callable[[dict[str, Any]], Awaitable[None] | None]


class DSHPrimaryError(RuntimeError):
    pass


class DSHPrimaryService:
    def __init__(
        self,
        *,
        profile: str = PROFILE,
        profile_scope: str = "group_primary",
        toolset: str = "primary",
        env_scope: str = "group",
        channel: str = "group",
        use_native: bool = True,
        system_prompt: str = "",
        native_prompt: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.profile = profile
        self.profile_dir = HOME_DIR / "profiles" / profile
        self.profile_scope = profile_scope
        self.toolset = toolset
        self.env_scope = env_scope
        self.channel = channel
        self.use_native = use_native
        self.native_prompt = native_prompt
        self.system_prompt = (
            system_prompt
            if system_prompt
            else ("" if native_prompt else self._default_system_prompt())
        )
        self.extra_env = dict(extra_env or {})
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._active: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, str] = {}
        self._counter = 0
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._last_error = ""
        self._started_at = 0.0

    # ------------------------------------------------------------- status
    @property
    def running(self) -> bool:
        return bool(self._process and self._process.returncode is None)

    def _default_system_prompt(self) -> str:
        return (
            "你是群伴的群聊主 Agent，由 DeepSeek Harness 驱动。"
            "你直接阅读群消息、判断是否参与、调用工具并给出最终结果。"
            "群伴只负责通道和发送，不会替你做意图分流。\n\n"
            "# 输出协议（必须遵守）\n"
            "只输出一个 JSON 对象，不要 Markdown 代码块：\n"
            '{"action":"none"}\n'
            '{"action":"reply","messages":[{"text":"..."}],"sticker":null}\n'
            '{"action":"task","ack":"收到，我处理一下","messages":[{"text":"..."}]}\n'
            "action=none 表示不参与；reply 表示直接回复；"
            "task 表示需要继续处理，ack/messages 会由群伴发送。\n"
            "不要调用 wechat_send_message；最终发送由群伴统一完成。"
        )

    def status(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "running": bool(self._process and self._process.returncode is None),
            "sessions": len(self._sessions),
            "pid": self._process.pid if self._process else None,
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------- profile
    def _ensure_profile(self, settings: dict[str, Any]) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "name": self.profile,
            "private": True,
            "version": "1.0.0",
            "dsh": {
                "profile": {
                    "bundles": [
                        "@deepseek-ai/dsh-base",
                        "@deepseek-ai/dsh-acp-app",
                    ]
                }
            },
        }
        model = str(settings.get("llm_model") or "deepseek-chat")
        source_profile = HOME_DIR / "profiles" / "qunban"
        dependencies: dict[str, Any] = {}
        rows = _profile_patch(self.profile_scope)
        if self.use_native:
            try:
                source_manifest = json.loads(
                    (source_profile / "package.json").read_text(encoding="utf-8")
                )
                dependencies = dict(source_manifest.get("dependencies") or {})
            except Exception:
                dependencies = {}
            imported_ids: set[str] = set()
            try:
                preserved = _preserved_plugin_rows(source_profile)
                rows.extend(plugin_policy.filter_rows(preserved, self.channel))
                imported_ids = {
                    str(item.get("id") or "")
                    for row in preserved
                    if isinstance(row.get("insert"), list)
                    for item in row["insert"]
                    if isinstance(item, dict)
                }
            except Exception:
                pass
            for plugin_id, policy in plugin_policy.all_policies().items():
                if not isinstance(policy, dict) or plugin_id in imported_ids:
                    continue
                rows.append(
                    {
                        "id": str(plugin_id),
                        "disabled": not (
                            bool(policy.get("enabled", True))
                            and bool(policy.get(self.channel, True))
                        ),
                    }
                )
            try:
                toggles = json.loads(
                    (source_profile / "plugin-toggles.json").read_text(encoding="utf-8")
                )
                if isinstance(toggles, dict):
                    for plugin_id, enabled in toggles.items():
                        if plugin_policy.get(str(plugin_id)) is None:
                            rows.append(
                                {"id": str(plugin_id), "disabled": not bool(enabled)}
                            )
            except Exception:
                pass
        rows.extend(
            [
                {
                    "id": "acp",
                    "config": {
                        "provider": "deepseek-official",
                        "model": model,
                    },
                },
                {
                    "id": "agent-default-model",
                    "config": {
                        "provider": "deepseek-official",
                        "model": model,
                    },
                },
                {
                    "id": "system-prompt",
                    "config": {"personaPrefix": self.system_prompt},
                },
            ]
        )
        if dependencies:
            manifest["dependencies"] = dependencies
            for name in dependencies:
                source = source_profile / "node_modules" / name
                target = self.profile_dir / "node_modules" / name
                if source.exists() and not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(source, target)
        _write_json(self.profile_dir / "package.json", manifest)
        _write_json(self.profile_dir / "cordis.patch.yml", rows)
        if self.use_native:
            _ensure_native_plugin(self.profile_dir)

    # ------------------------------------------------------------- process
    def _env(self, settings: dict[str, Any]) -> dict[str, str]:
        version = get_dsh_runtime().installed_version()
        env = os.environ.copy()
        env.update(
            {
                "DSH_HOME": str(HOME_DIR),
                "DEEPSEEK_API_KEY": str(settings.get("llm_api_key") or ""),
                "DEEPSEEK_BASE_URL": deepseek_messages_base(
                    str(settings.get("llm_base_url") or "")
                ),
                "DSH_TELEMETRY_MODE": "DISABLED",
                "NO_COLOR": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "QUNBAN_API_BASE": f"http://127.0.0.1:{process_settings.port}",
                "QUNBAN_TOKEN": tool_token(),
                "QUNBAN_SCOPE": self.env_scope,
                "QUNBAN_CHANNEL": self.channel,
                "QUNBAN_DENY_TOOLS": ",".join(
                    plugin_policy.deny_tools(self.channel)
                )
                if self.use_native
                else "",
                "QUNBAN_TOOLSET": self.toolset,
                "QUNBAN_DSH_RUNTIME_ROOT": str(RUNTIME_DIR / version),
            }
        )
        env.update({str(key): str(value) for key, value in self.extra_env.items()})
        return env

    async def start(self, settings: dict[str, Any]) -> None:
        async with self._start_lock:
            if self._process and self._process.returncode is None:
                return
            version = get_dsh_runtime().installed_version()
            if not version:
                raise DSHPrimaryError("DSH 尚未安装")
            node = _node_exe()
            if not node:
                raise DSHPrimaryError("未找到 Node.js")
            bin_path = _bin_path(version)
            if not bin_path.exists():
                raise DSHPrimaryError("DSH 运行时文件缺失")
            self._ensure_profile(settings)
            self._sessions.clear()
            self._active.clear()
            self._process = await asyncio.create_subprocess_exec(
                node,
                str(bin_path),
                "--profile",
                self.profile,
                cwd=str(WORKSPACE_DIR),
                env=self._env(settings),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._reader_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._read_stderr())
            try:
                result = await self._request(
                    "initialize",
                    {
                        "protocolVersion": ACP_PROTOCOL_VERSION,
                        "clientCapabilities": {},
                    },
                    timeout=30.0,
                )
                if int(result.get("protocolVersion") or 0) != ACP_PROTOCOL_VERSION:
                    raise DSHPrimaryError("DSH ACP 协议版本不匹配")
            except Exception:
                await self.stop()
                raise
            self._started_at = asyncio.get_running_loop().time()

    async def stop(self) -> None:
        process = self._process
        self._process = None
        self._sessions.clear()
        if process and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
        self._reader_task = None
        self._stderr_task = None

    async def _read_stderr(self) -> None:
        process = self._process
        if not process or not process.stderr:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                logger.info("DSH ACP: %s", text[-500:])

    async def _read_stdout(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                raw = line.decode("utf-8", errors="replace").strip()
                if not raw:
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("DSH ACP non-JSON stdout: %s", raw[:500])
                    continue
                if "id" in message and ("result" in message or "error" in message):
                    future = self._pending.pop(int(message["id"]), None)
                    if future and not future.done():
                        if message.get("error"):
                            future.set_exception(
                                DSHPrimaryError(str(message["error"]).strip()[:1200])
                            )
                        else:
                            future.set_result(message.get("result") or {})
                    continue
                method = str(message.get("method") or "")
                if method == "session/update":
                    self._on_update(message.get("params") or {})
                elif method == "session/request_permission":
                    await self._answer_permission(message)
                elif message.get("id") is not None and method:
                    await self._write(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {
                                "code": -32601,
                                "message": f"unsupported client method: {method}",
                            },
                        }
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("DSH ACP stdout reader stopped: %s", exc)
        finally:
            self._sessions.clear()
            self._active.clear()
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(DSHPrimaryError("DSH ACP 进程已退出"))
            self._pending.clear()

    def _on_update(self, params: dict[str, Any]) -> None:
        session_id = str(params.get("sessionId") or "")
        update = params.get("update") or {}
        active = self._active.get(session_id)
        if not active:
            return
        kind = str(update.get("sessionUpdate") or "")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text":
                active["parts"].append(str(content.get("text") or ""))
        elif kind == "usage_update":
            active["usage"] = {
                "used": update.get("used"),
                "size": update.get("size"),
            }
        elif kind in {"tool_call", "tool_call_update"}:
            active["tools"].append(
                {
                    "title": update.get("title"),
                    "status": update.get("status"),
                    "kind": update.get("kind"),
                }
            )
            callback = active.get("callback")
            if callback:
                payload = {
                    "phase": "tool" if kind == "tool_call" else "tool_result",
                    "tool": update.get("title") or update.get("toolCallId") or "",
                    "status": update.get("status"),
                    "kind": kind,
                }
                try:
                    result = callback(payload)
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                except Exception:
                    pass

    async def _answer_permission(self, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        options = params.get("options") or []
        choice = next(
            (
                option
                for option in options
                if "allow" in str(option.get("kind") or "").lower()
            ),
            options[0] if options else None,
        )
        if choice and choice.get("optionId"):
            result = {
                "outcome": {
                    "outcome": "selected",
                    "optionId": choice["optionId"],
                }
            }
        else:
            result = {"outcome": {"outcome": "cancelled"}}
        await self._write(
            {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
        )

    async def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if not process or not process.stdin:
            raise DSHPrimaryError("DSH ACP 未运行")
        async with self._write_lock:
            process.stdin.write(
                (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            )
            await process.stdin.drain()

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        if not self._process or self._process.returncode is not None:
            raise DSHPrimaryError("DSH ACP 进程未运行")
        self._counter += 1
        request_id = self._counter
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise DSHPrimaryError(f"DSH ACP 请求超时：{method}") from exc

    async def _session(self, key: str) -> str:
        existing = self._sessions.get(key)
        if existing:
            return existing
        async with self._session_lock:
            existing = self._sessions.get(key)
            if existing:
                return existing
            result = await self._request(
                "session/new",
                {
                    "cwd": str(WORKSPACE_DIR),
                    "mcpServers": [],
                },
                timeout=60.0,
            )
            session_id = str(result.get("sessionId") or "")
            if not session_id:
                raise DSHPrimaryError("DSH ACP 没有返回 sessionId")
            self._sessions[key] = session_id
            return session_id

    # -------------------------------------------------------------- prompt
    async def prompt(
        self,
        key: str,
        text: str,
        *,
        settings: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        on_update: Progress | None = None,
    ) -> dict[str, Any]:
        if settings is None:
            raise DSHPrimaryError("DSH primary 需要 settings")
        if not text.strip():
            raise DSHPrimaryError("prompt 不能为空")
        await self.start(settings)
        session_id = await self._session(key)
        collector: dict[str, Any] = {
            "parts": [],
            "tools": [],
            "usage": {},
            "callback": on_update,
        }
        if session_id in self._active:
            raise DSHPrimaryError("同一群会话正在处理上一条消息")
        self._active[session_id] = collector
        try:
            response = await self._request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": text}],
                },
                timeout=timeout,
            )
        finally:
            self._active.pop(session_id, None)
        return {
            "text": "".join(collector["parts"]).strip(),
            "tools": collector["tools"],
            "usage": collector["usage"],
            "stop_reason": response.get("stopReason"),
            "session_id": session_id,
        }

    async def close_session(self, key: str) -> None:
        session_id = self._sessions.pop(key, None)
        if not session_id or not self._process:
            return
        try:
            await self._request(
                "session/close", {"sessionId": session_id}, timeout=30.0
            )
        except Exception:
            pass


_service: DSHPrimaryService | None = None


def get_dsh_primary_service() -> DSHPrimaryService:
    global _service
    if _service is None:
        _service = DSHPrimaryService()
    return _service


CHAT_LANE_PROMPT = """你是群伴的 Chat Agent，由 DeepSeek Harness 驱动。
你负责像真人一样接话，不调用工具，不操作系统，不发送微信。

# 风格
- 简短、自然、有语境感，通常 1–2 句，最多 80 个汉字。
- 可以幽默、接梗，但不要油腻、不要长篇说教。
- 不要复述整段聊天，不要输出 Markdown、JSON、工具名或分析过程。
- 不确定的事实不要编造；需要工具或复杂操作时，用一句自然话说明需要后台处理。
- 不要声称已经派任务、装插件、截屏或调用工具；只有上下文里明确存在的后台任务才能提。没有就直说当前做不到。
- 复杂任务不要说“做不到”就结束：可以告诉用户会拆步骤处理，或先问清目标和授权。
- 登录、验证码、账号权限、删除文件等敏感操作，先向用户确认，不要替用户做决定。

# 输出
直接输出要发给群友/用户的文字。
"""


class DSHChatLaneService(DSHPrimaryService):
    """Persistent, tool-free ACP chat lane."""

    def __init__(self) -> None:
        super().__init__(
            profile="qunban-chat-lane",
            profile_scope="chat",
            toolset="none",
            env_scope="group",
            channel="group",
            use_native=False,
            system_prompt=CHAT_LANE_PROMPT,
        )


_chat_lane: DSHChatLaneService | None = None


def get_dsh_chat_lane_service() -> DSHChatLaneService:
    global _chat_lane
    if _chat_lane is None:
        _chat_lane = DSHChatLaneService()
    return _chat_lane


TASK_LANE_PROMPT = """你是群伴的 Task Agent，由 DeepSeek Harness 驱动。
你负责把群聊/私聊里的复杂请求拆解并执行到底，不负责闲聊。

# 工作方式
1. 先理解目标、约束、成功标准；不清楚就先问一个最关键的问题。
2. 盘点可用工具和已有能力；不要因为“没有现成插件”就直接拒绝。
3. 把任务拆成步骤：检查环境 → 取得授权 → 读取/登录 → 处理 → 验证 → 汇报。
4. 可以把多个工具串成工具链，把上一步结果传给下一步；必要时写临时脚本。
5. 每完成一步都检查结果；失败要换路径或说明真实原因，不能假装完成。
6. 事实类任务必须先调用工具拿到结果，禁止凭记忆编造天气、B站、文件、账号内容。
7. 用户要原始输出时引用工具原文/关键字段；拿不到截图或原文要明确说明。

# 安全与授权
- 登录、验证码、二步验证、Cookie、密码、支付、删除文件、修改系统设置等敏感操作，
  先向用户说明风险和范围，取得明确确认后再做；需要本人登录时必须请用户手动完成。
- 禁止收集/保存密码、Cookie、验证码，禁止绕过验证码、风控或账号权限。
- 只访问用户授权的账号和数据；只读优先，写操作先确认。
- 不确定是否有权限时停下来问，不要猜着执行。

# 脚本与插件生命周期
- 临时脚本写到 data/dsh/studio/tasks/<task_id>/，先做基本检查再运行。
- 写完后做泛用性评级：
  * temporary：只服务当前任务，汇报后在任务结束时删除脚本和临时文件。
  * reusable：参数化、可复用、可测试，保留并调用 qunban_plugin_install 安装/更新插件。
- 不修改 data/dsh/home/profiles 下的现有 profile；安装/更新失败要报告真实错误。
- 最终说明：做了什么步骤、是否需要用户继续授权、脚本保留还是清理。

# 输出
- 不要调用 wechat_send_message；最终发送由群伴完成。
- 完成后只输出一个 JSON 对象：
  {"action":"reply","messages":[{"text":"..."}],"sticker":null}
- 如果确实需要用户补充授权/信息，也用 reply 提出一个明确问题。
- 只有在你已经尝试并确认无法完成时才输出 {"action":"none"}，不要伪造结果。
- 不要输出 Markdown 代码块或工具名。
"""


class DSHTaskLaneService(DSHPrimaryService):
    """Persistent task lane with the Qunban toolset."""

    def __init__(self) -> None:
        super().__init__(
            profile="qunban-task-lane",
            profile_scope="group_primary",
            toolset="task",
            env_scope="group",
            channel="group",
            use_native=True,
            system_prompt=TASK_LANE_PROMPT,
        )


_task_lane: DSHTaskLaneService | None = None


def get_dsh_task_lane_service() -> DSHTaskLaneService:
    global _task_lane
    if _task_lane is None:
        _task_lane = DSHTaskLaneService()
    return _task_lane
