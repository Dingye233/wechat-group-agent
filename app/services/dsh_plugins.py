"""DSH plugin catalog, import jobs and the Qunban plugin studio.

The catalog is read from the *composed* DSH profile (``--dump-config``) rather
than from a hard-coded list, so it follows runtime updates.  Imports use the
profile's own package manager (pnpm through corepack) and local/generated
plugins are copied into the profile's ``node_modules`` and mounted with a
single patch row.

Creation mode has two paths:

* deterministic scaffold -- always available, produces a valid Cordis plugin;
* DSH-authored -- the Operator profile writes the plugin implementation from
  the user's requirements, then the same validation/install pipeline runs.

Generated plugins are arbitrary JavaScript executed by DSH, so installation is
only ever triggered by the user from the Plugin Studio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.dsh_runtime import (
    DATA_DIR,
    HOME_DIR,
    RUNTIME_DIR,
    _bin_path,
    _node_exe,
    get_dsh_runtime,
)

logger = logging.getLogger(__name__)

PROFILE = "qunban"
PROFILE_DIR = HOME_DIR / "profiles" / PROFILE
PATCH_PATH = PROFILE_DIR / "cordis.patch.yml"
STUDIO_DIR = DATA_DIR / "studio"
MANIFEST_PATH = STUDIO_DIR / "index.json"
CACHE_TTL = 20.0

_DESCRIPTIONS: dict[str, str] = {
    "agent": "Agent 注册表与生命周期，是 DSH 的会话主体。",
    "agent-loop": "Agent 主循环：把消息、工具调用和模型回复串起来。",
    "session": "会话持久化与恢复，决定 session-id 能否续聊。",
    "system-prompt": "系统提示词组装，决定 personaPrefix 和工具说明的位置。",
    "tools": "工具注册表与执行流水线，所有 tool calling 都经过这里。",
    "llm": "模型接入层，负责 Provider、流式和 token 统计。",
    "llm-deepseek": "DeepSeek 官方模型接入。",
    "tool-fs": "原生文件读写工具：read / write / edit。",
    "tool-fs-search": "原生文件搜索工具：glob / grep。",
    "tool-pwsh": "原生 PowerShell 工具；群伴 profile 已禁用以保证微信安全。",
    "tool-bash": "原生 Bash 工具；群伴 profile 已禁用。",
    "tool-web": "网页搜索与抓取工具。",
    "tool-skill": "加载 skills 的原生工具。",
    "skill": "Skill 注册表。",
    "skill-filesystem": "从本地目录发现 SKILL.md，群伴 skills 通过它接入。",
    "tool-todo": "任务清单工具。",
    "tool-goal": "长目标工具，支持跨轮继续。",
    "tool-jobs": "后台任务与输出流工具。",
    "tool-subagent": "子 Agent 工具。",
    "tool-workflow": "多 Agent 工作流编排工具。",
    "mcp-client": "MCP 客户端桥：把 MCP server 的工具注册到 DSH。",
    "web-search-deepseek": "DeepSeek 官方联网搜索。",
    "qunban-native": "群伴原生插件：微信、记忆、B站、技能和命令工具。",
    "tool-fs-search": "原生文件搜索：glob / grep。",
    "tool-bash": "原生 Bash 工具；群伴默认禁用。",
    "tool-pwsh": "原生 PowerShell 工具；群伴默认禁用。",
    "tool-web": "原生网页搜索/抓取工具。",
    "tool-todo": "任务清单工具。",
    "tool-goal": "长目标工具，支持跨轮继续。",
    "tool-jobs": "后台任务与输出流工具。",
    "tool-subagent": "子 Agent 工具。",
    "tool-workflow": "多 Agent 工作流编排。",
    "tool-skill": "加载 skills 的原生工具。",
    "skill-filesystem": "本地 SKILL.md 发现器；群伴 skills 通过它接入。",
    "agent-instructions": "加载 AGENTS.md / CLAUDE.md 等仓库指令。",
    "compaction-basic": "上下文压缩策略；长会话会自动摘要。",
    "deepseek-llm-api-extensions": "DeepSeek 官方 API 的扩展请求字段。",
    "headless": "一次性 headless 启动器；群伴每个任务用它。",
    "agent-default-model": "默认模型选择。",
    "session-title": "自动生成会话标题。",
    "approval": "人类审批 seam（当前 qunban profile 使用 full access）。",
    "config-editor": "通过 profile patch 持久化插件配置。",
    "command-goal": "`/goal` 命令。",
    "command-compact": "`/compact` 命令。",
    "attachment-local": "DSH_HOME 下的附件存储。",
    "web-fetch-http": "HTTP 网页抓取 provider。",
    "mcp-resources": "MCP 资源目录。",
}

_CATEGORIES: dict[str, str] = {
    "agent": "核心",
    "agent-loop": "核心",
    "session": "核心",
    "system-prompt": "核心",
    "tools": "核心",
    "llm": "核心",
    "llm-deepseek": "核心",
    "tool-fs": "工具",
    "tool-fs-search": "工具",
    "tool-pwsh": "工具",
    "tool-bash": "工具",
    "tool-web": "工具",
    "tool-skill": "工具",
    "tool-todo": "工具",
    "tool-goal": "工具",
    "tool-jobs": "工具",
    "tool-subagent": "工具",
    "tool-workflow": "工具",
    "skill": "技能",
    "skill-filesystem": "技能",
    "mcp-client": "集成",
    "web-search-deepseek": "集成",
    "qunban-native": "群伴",
}

_OPTIONAL = [
    {
        "id": "mcp-client",
        "name": "@deepseek-ai/dsh-mcp-client",
        "description": "连接 MCP server，把外部工具注册成 DSH 原生工具。",
        "category": "集成",
    },
    {
        "id": "tool-bash",
        "name": "@deepseek-ai/dsh-tool-bash",
        "description": "Bash 执行工具；群伴默认禁用，避免绕过微信安全网关。",
        "category": "工具",
    },
    {
        "id": "tool-subagent",
        "name": "@deepseek-ai/dsh-tool-subagent",
        "description": "派生子 Agent 并行处理独立任务。",
        "category": "工具",
    },
    {
        "id": "web-search-deepseek",
        "name": "@deepseek-ai/dsh-web-search-deepseek",
        "description": "DeepSeek 官方联网搜索 provider。",
        "category": "集成",
    },
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return text or f"plugin-{uuid.uuid4().hex[:6]}"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _patch_rows() -> list[dict[str, Any]]:
    rows = _read_json(PATCH_PATH, [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _write_patch(rows: list[dict[str, Any]]) -> None:
    _write_json(PATCH_PATH, rows)


def _patch_entries(rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for row in rows if rows is not None else _patch_rows():
        insert = row.get("insert")
        if isinstance(insert, list):
            for item in insert:
                if isinstance(item, dict) and item.get("name"):
                    entries.append(item)
        elif row.get("id") and row.get("name"):
            entries.append(row)
    return entries


def _runtime_version() -> str:
    return str(get_dsh_runtime().installed_version() or "")


def _runtime_root() -> Path:
    return RUNTIME_DIR / _runtime_version()


def _node() -> str:
    node = _node_exe()
    if not node:
        raise RuntimeError("未找到 Node.js")
    return node


def _bin() -> Path:
    return _bin_path(_runtime_version())


def _dump_config() -> str:
    node = _node()
    bin_path = _bin()
    env = _profile_env()
    proc = subprocess.run(
        [node, str(bin_path), "--profile", PROFILE, "--dump-config"],
        cwd=str(PROFILE_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout)[-1200:])
    return proc.stdout


def _profile_env() -> dict[str, str]:
    env = dict(os.environ)
    env["DSH_HOME"] = str(HOME_DIR)
    env["NO_COLOR"] = "1"
    env["DSH_TELEMETRY_MODE"] = "DISABLED"
    return env


_DUMP_CACHE: tuple[float, str] | None = None


def _cached_dump() -> str:
    global _DUMP_CACHE
    now = time.monotonic()
    if _DUMP_CACHE and now - _DUMP_CACHE[0] < CACHE_TTL:
        return _DUMP_CACHE[1]
    text = _dump_config()
    _DUMP_CACHE = (now, text)
    return text


def _parse_dump(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    layer = "base"
    for line in text.splitlines():
        if line.startswith("# =="):
            layer = line[3:].strip() or layer
            continue
        match = re.match(r"^- id:\s*(\S+)\s*$", line)
        if match:
            if current:
                entries.append(current)
            current = {
                "id": match.group(1),
                "name": "",
                "disabled": False,
                "source": layer,
            }
            continue
        if current is None:
            continue
        name_match = re.match(r"^  name:\s*['\"]?([^'\"\n]+)['\"]?\s*$", line)
        if name_match and not current["name"]:
            current["name"] = name_match.group(1).strip()
        elif re.match(r"^  disabled:\s*true\s*$", line):
            current["disabled"] = True
    if current:
        entries.append(current)
    # Last override wins, matching DSH's patch semantics.
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        by_id[entry["id"]] = entry
    return list(by_id.values())


def _package_json(name: str) -> dict[str, Any]:
    if not name.startswith("@"):
        return {}
    path = _runtime_root() / "node_modules" / name / "package.json"
    return _read_json(path, {})


def _package_installed(name: str) -> bool:
    if name.startswith("@qunban/"):
        return (PROFILE_DIR / "node_modules" / name).exists()
    return (_runtime_root() / "node_modules" / name).exists()


def _category(entry: dict[str, Any]) -> str:
    plugin_id = str(entry.get("id") or "")
    if plugin_id in _CATEGORIES:
        return _CATEGORIES[plugin_id]
    name = str(entry.get("name") or "")
    if "qunban" in name:
        return "群伴"
    if plugin_id.startswith("tool-") or name.endswith("-tools"):
        return "工具"
    if plugin_id.startswith("skill"):
        return "技能"
    if plugin_id.startswith(("client-", "api-", "web-client", "hmr", "module", "locale", "brand", "typert")):
        return "界面/API"
    if plugin_id.startswith(("mcp", "web", "deepseek", "remote")):
        return "集成"
    if plugin_id.startswith(
        (
            "agent",
            "session",
            "llm",
            "system",
            "tools",
            "config",
            "compaction",
            "headless",
            "current",
            "prompt",
        )
    ):
        return "核心"
    if plugin_id.startswith(
        (
            "bash",
            "sandbox",
            "fs",
            "terminal",
            "pty",
            "workspace",
            "attachment",
            "job",
            "subagent",
            "workflow",
            "goal",
            "todo",
            "approval",
            "command",
            "commands",
            "settings",
            "telemetry",
        )
    ):
        return "服务"
    if plugin_id.startswith(
        (
            "image-",
            "otel",
            "permission",
            "plan-mode",
            "plugin-",
            "ptc-",
            "pwsh-",
            "repeat-",
            "shell-",
            "spill-",
        )
    ):
        return "运行时"
    return "其他"


_CATEGORY_ORDER = {
    "核心": 0,
    "工具": 1,
    "技能": 2,
    "服务": 3,
    "运行时": 4,
    "集成": 5,
    "界面/API": 6,
    "群伴": 7,
    "其他": 8,
}


def _describe(entry: dict[str, Any]) -> str:
    plugin_id = str(entry.get("id") or "")
    if plugin_id in _DESCRIPTIONS:
        return _DESCRIPTIONS[plugin_id]
    package = _package_json(str(entry.get("name") or ""))
    description = str(package.get("description") or "").strip()
    return description[:260] or "DSH 内置插件。"


def catalog() -> dict[str, Any]:
    text = _cached_dump()
    parsed = _parse_dump(text)
    builtin: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for entry in parsed:
        name = str(entry.get("name") or "")
        if not name.startswith("@deepseek-ai/"):
            continue
        if name in seen_names:
            continue
        seen_names.add(name)
        package = _package_json(name)
        builtin.append(
            {
                **entry,
                "description": _describe(entry),
                "category": _category(entry),
                "version": package.get("version", ""),
                "kind": "bundle"
                if isinstance(package.get("dsh"), dict)
                and "bundle" in (package.get("dsh") or {})
                else "plugin",
                "active": not entry.get("disabled"),
            }
        )
    for item in _OPTIONAL:
        if item["name"] in seen_names:
            continue
        builtin.append(
            {
                **item,
                "disabled": False,
                "source": "optional",
                "version": _package_json(item["name"]).get("version", ""),
                "kind": "plugin",
                "active": False,
                "installed": _package_installed(item["name"]),
            }
        )
    builtin.sort(
        key=lambda row: (
            _CATEGORY_ORDER.get(row["category"], 99),
            row["disabled"],
            row["id"],
        )
    )

    dependencies = _read_json(PROFILE_DIR / "package.json", {}).get("dependencies") or {}
    entries = {str(row.get("name")): row for row in _patch_entries()}
    installed: list[dict[str, Any]] = []
    native_entry = PROFILE_DIR / "node_modules" / "@qunban" / "dsh-native"
    if native_entry.exists():
        installed.append(
            {
                "id": "qunban-native",
                "name": "@qunban/dsh-native",
                "version": "0.1.0",
                "description": "群伴原生插件：wechat_* / qunban_* 工具，走群伴 HTTP 网关。",
                "disabled": False,
                "source": "native",
                "installed": True,
            }
        )
    for name in sorted(dependencies):
        if name.startswith("@deepseek-ai/"):
            continue
        package = _read_json(PROFILE_DIR / "node_modules" / name / "package.json", {})
        row = entries.get(name, {})
        installed.append(
            {
                "id": row.get("id") or _slug(name.split("/")[-1]),
                "name": name,
                "version": package.get("version") or str(dependencies.get(name) or ""),
                "description": package.get("description")
                or "用户导入的 DSH 插件。",
                "disabled": bool(row.get("disabled")),
                "source": "imported",
                "installed": _package_installed(name),
            }
        )

    studio = _read_json(MANIFEST_PATH, {}).get("plugins", [])
    if isinstance(studio, dict):
        studio = list(studio.values())
    generated: list[dict[str, Any]] = []
    for item in studio if isinstance(studio, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        row = entries.get(name, {})
        generated.append(
            {
                **item,
                "disabled": bool(row.get("disabled")),
                "enabled": not bool(row.get("disabled")),
                "installed": _package_installed(name),
                "source": "studio",
            }
        )

    return {
        "runtime": {
            "version": _runtime_version(),
            "profile": PROFILE,
            "profile_dir": str(PROFILE_DIR),
            "packages": len(list((_runtime_root() / "node_modules" / "@deepseek-ai").glob("*")))
            if (_runtime_root() / "node_modules" / "@deepseek-ai").exists()
            else 0,
        },
        "builtin": builtin,
        "installed": installed,
        "generated": generated,
        "counts": {
            "builtin": len(builtin),
            "active": sum(1 for row in builtin if row.get("active")),
            "imported": len(installed),
            "generated": len(generated),
        },
        "updated_at": _utcnow(),
    }


# ------------------------------------------------------------------ jobs


@dataclass
class PluginJob:
    id: str
    kind: str
    title: str
    status: str = "running"
    log: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=_utcnow)
    finished_at: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "log": self.log[-120:],
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


_JOBS: dict[str, PluginJob] = {}
_TASKS: set[asyncio.Task] = set()
_LOCK = asyncio.Lock()


def _new_job(kind: str, title: str) -> PluginJob:
    job = PluginJob(id=uuid.uuid4().hex[:12], kind=kind, title=title)
    _JOBS[job.id] = job
    return job


def _log(job: PluginJob, message: str) -> None:
    job.log.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")


def _finish(job: PluginJob, *, result: dict[str, Any] | None = None, error: str = "") -> None:
    job.status = "error" if error else "done"
    job.error = error
    job.result = result or {}
    job.finished_at = _utcnow()
    if error:
        _log(job, f"失败：{error}")
    else:
        _log(job, "完成")


def job(job_id: str) -> dict[str, Any] | None:
    item = _JOBS.get(job_id)
    return item.public() if item else None


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _run_command(
    job: PluginJob,
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 300.0,
) -> tuple[int, str]:
    _log(job, "$ " + " ".join(str(arg) for arg in args))
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        env=_profile_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"命令超过 {int(timeout)} 秒")
    text = (output or b"").decode("utf-8", errors="replace")
    for line in text.splitlines()[-40:]:
        _log(job, line[:500])
    return int(proc.returncode or 0), text


def _package_name_from_spec(spec: str) -> str:
    spec = spec.strip()
    if spec.startswith(("file:", "link:")):
        path = Path(spec.split(":", 1)[1]).expanduser()
        package = _read_json(path / "package.json", {})
        return str(package.get("name") or "")
    if spec.startswith((".", "/")) or re.match(r"^[A-Za-z]:[\\/]", spec):
        package = _read_json(Path(spec) / "package.json", {})
        return str(package.get("name") or "")
    if spec.startswith("@"):
        body = spec[1:]
        return "@" + (body.rsplit("@", 1)[0] if "@" in body else body)
    return spec.split("@", 1)[0].split("#", 1)[0]


def _ensure_patch_entry(name: str, *, enabled: bool) -> str:
    slug = _slug(name.split("/")[-1])
    rows = _patch_rows()
    entries = _patch_entries(rows)
    for entry in entries:
        if entry.get("name") == name:
            entry["id"] = entry.get("id") or f"plugin-{slug}"
            entry["disabled"] = not enabled
            _write_patch(rows)
            return str(entry["id"])
    plugin_id = f"plugin-{slug}"
    rows.append(
        {
            "insert": [
                {
                    "id": plugin_id,
                    "name": name,
                    **({"disabled": True} if not enabled else {}),
                }
            ]
        }
    )
    _write_patch(rows)
    return plugin_id


def start_import(spec: str, *, enabled: bool = True) -> dict[str, Any]:
    spec = str(spec or "").strip()
    if not spec:
        raise ValueError("插件 spec 不能为空")
    if len(spec) > 240:
        raise ValueError("插件 spec 过长")
    job = _new_job("import", f"导入 {spec}")
    _spawn(_import_job(job, spec, enabled))
    return job.public()


async def _import_job(job: PluginJob, spec: str, enabled: bool) -> None:
    async with _LOCK:
        try:
            package_name = _package_name_from_spec(spec)
            local = spec.startswith(("file:", "link:")) or spec.startswith((".", "/")) or bool(
                re.match(r"^[A-Za-z]:[\\/]", spec)
            )
            if local:
                source = Path(spec.split(":", 1)[1] if spec.startswith(("file:", "link:")) else spec)
                source = source.expanduser().resolve()
                package = _read_json(source / "package.json", {})
                package_name = str(package.get("name") or "")
                if not package_name or not (source / "lib" / "index.js").exists():
                    raise RuntimeError("本地插件缺少 package.json 或 lib/index.js")
                _log(job, f"本地插件：{package_name}")
                target = PROFILE_DIR / "node_modules" / package_name
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target)
                deps = _read_json(PROFILE_DIR / "package.json", {})
                deps.setdefault("dependencies", {})[package_name] = f"file:{source.as_posix()}"
                _write_json(PROFILE_DIR / "package.json", deps)
            else:
                corepack = shutil.which("corepack.cmd") or shutil.which("corepack")
                if not corepack:
                    raise RuntimeError("未找到 corepack/pnpm，无法从 npm/git 导入")
                before = _read_json(PROFILE_DIR / "package.json", {}).get("dependencies") or {}
                code, _ = await _run_command(
                    job,
                    [corepack, "pnpm", "add", spec],
                    cwd=PROFILE_DIR,
                    timeout=420.0,
                )
                if code != 0:
                    raise RuntimeError("pnpm add 失败，查看上方日志")
                after = _read_json(PROFILE_DIR / "package.json", {}).get("dependencies") or {}
                added = [name for name in after if name not in before]
                package_name = added[0] if added else package_name
            if not package_name:
                raise RuntimeError("无法解析插件包名")
            plugin_id = _ensure_patch_entry(package_name, enabled=enabled)
            _DUMP_CACHE = None
            _finish(
                job,
                result={
                    "package": package_name,
                    "id": plugin_id,
                    "enabled": enabled,
                    "next": "下一次 DSH 任务会自动加载；如未出现，请在面板刷新或重启服务。",
                },
            )
        except Exception as exc:
            logger.warning("DSH 插件导入失败：%s", exc)
            _finish(job, error=str(exc))


def _scaffold_js(slug: str, description: str) -> str:
    tool_name = "studio_" + re.sub(r"[^a-zA-Z0-9_]+", "_", slug).strip("_")
    return f"""import {{ pathToFileURL }} from 'node:url'

const runtimeRoot = process.env.QUNBAN_DSH_RUNTIME_ROOT || ''
if (!runtimeRoot) throw new Error('QUNBAN_DSH_RUNTIME_ROOT is required')
const {{ defineTool }} = await import(
  pathToFileURL(`${{runtimeRoot}}/node_modules/@deepseek-ai/dsh-tools/lib/index.js`).href
)

export const name = 'studio-{slug}'
export const inject = ['tools']

export function apply(ctx) {{
  ctx.tools.register(
    defineTool({{
      name: '{tool_name}',
      description: {json.dumps(description or f"群伴插件 {slug} 的示例工具。", ensure_ascii=False)},
      parameters: {{
        input: {{ type: 'string', description: '输入文本' }},
      }},
      output: {{
        schema: {{ type: 'string' }},
        render: (_args, value) => [{{ type: 'text', text: String(value) }}],
      }},
      async execute(args) {{
        return JSON.stringify({{ ok: true, plugin: '{slug}', input: String(args.input || '') }})
      }},
    }}),
  )
}}
"""


def _write_scaffold(slug: str, description: str, requirements: str) -> Path:
    target = STUDIO_DIR / slug
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    (target / "lib").mkdir(parents=True, exist_ok=True)
    _write_json(
        target / "package.json",
        {
            "name": f"@qunban/{slug}",
            "version": "0.1.0",
            "private": True,
            "type": "module",
            "main": "lib/index.js",
            "exports": {".": "./lib/index.js"},
            "description": description,
        },
    )
    (target / "lib" / "index.js").write_text(
        _scaffold_js(slug, description), encoding="utf-8"
    )
    (target / "REQUIREMENTS.md").write_text(
        f"# {slug}\n\n{description}\n\n## 需求\n{requirements or '（未填写）'}\n",
        encoding="utf-8",
    )
    return target


def _validate_plugin(target: Path) -> None:
    package = _read_json(target / "package.json", {})
    if not str(package.get("name") or "").startswith("@qunban/"):
        raise RuntimeError("插件 package.json 的 name 必须是 @qunban/<slug>")
    entry = target / "lib" / "index.js"
    if not entry.exists():
        raise RuntimeError("缺少 lib/index.js")
    source = entry.read_text(encoding="utf-8")
    if "apply" not in source or "export" not in source:
        raise RuntimeError("插件入口必须导出 apply")
    proc = subprocess.run(
        [_node(), "--check", str(entry)],
        cwd=str(target),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout)[-1200:])


def _install_generated(slug: str, target: Path, *, enabled: bool = True) -> str:
    package = _read_json(target / "package.json", {})
    name = str(package.get("name") or f"@qunban/{slug}")
    plugin_id = f"studio-{slug}"
    profile_target = PROFILE_DIR / "node_modules" / name
    if profile_target.exists():
        shutil.rmtree(profile_target, ignore_errors=True)
    profile_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(target, profile_target)
    deps = _read_json(PROFILE_DIR / "package.json", {})
    deps.setdefault("dependencies", {})[name] = f"file:{target.as_posix()}"
    _write_json(PROFILE_DIR / "package.json", deps)
    rows = _patch_rows()
    entries = _patch_entries(rows)
    found = next((row for row in entries if row.get("name") == name), None)
    if found:
        found["id"] = found.get("id") or plugin_id
        found["disabled"] = not enabled
        _write_patch(rows)
    else:
        rows.append({"insert": [{"id": plugin_id, "name": name}]})
        _write_patch(rows)
    manifest = _read_json(MANIFEST_PATH, {"plugins": []})
    items = manifest.get("plugins") if isinstance(manifest, dict) else []
    if not isinstance(items, list):
        items = []
    record = {
        "slug": slug,
        "name": name,
        "description": str(package.get("description") or ""),
        "path": str(target),
        "updated_at": _utcnow(),
        "enabled": enabled,
    }
    items = [item for item in items if not (isinstance(item, dict) and item.get("slug") == slug)]
    items.append(record)
    _write_json(MANIFEST_PATH, {"plugins": items})
    _DUMP_CACHE = None
    return plugin_id


def start_create(
    name: str,
    description: str,
    requirements: str,
    *,
    use_agent: bool = True,
    enabled: bool = True,
) -> dict[str, Any]:
    slug = _slug(name)
    if len(slug) < 2:
        raise ValueError("插件名至少 2 个字符")
    job = _new_job("create", f"创建插件 @qunban/{slug}")
    _spawn(_create_job(job, slug, description, requirements, use_agent, enabled))
    return job.public()


async def _create_job(
    job: PluginJob,
    slug: str,
    description: str,
    requirements: str,
    use_agent: bool,
    enabled: bool,
) -> None:
    async with _LOCK:
        try:
            _log(job, f"创建脚手架：{STUDIO_DIR / slug}")
            target = _write_scaffold(slug, description, requirements)
            if use_agent:
                _log(job, "交给 DSH Operator 编写实现（可能需要 1–3 分钟）…")
                from app.services.dsh_agent import get_dsh_agent_runtime

                system_prompt = f"""你是群伴的 DSH 插件工程师。请在目录：
{target}

创建一个可运行的 DeepSeek Harness Cordis 插件。
- package.json 的 name 必须是 @qunban/{slug}，type=module，main=lib/index.js。
- lib/index.js 必须 `export const name`、`export const inject = ['tools']`、`export function apply(ctx)`。
- 用 `defineTool` 注册工具；从 `process.env.QUNBAN_DSH_RUNTIME_ROOT` 拼接绝对路径，动态 import：
  `${{runtimeRoot}}/node_modules/@deepseek-ai/dsh-tools/lib/index.js`
- 工具参数、输出 schema 必须显式写 `additionalProperties`，output 必须提供 `render`。
- 只写这个目录，不要安装、不要修改群伴其他文件、不要直接操作微信。
- 完成后运行 `node --check lib/index.js` 自检，最后回复 DONE。
用户需求：
{requirements}
"""
                runtime = get_dsh_agent_runtime()
                result = await runtime.chat(
                    "请按系统提示实现插件并自检。",
                    session_id=f"plugin-studio-{slug}",
                    system_prompt=system_prompt,
                    scope="assistant",
                    timeout=420.0,
                    call_site="dsh.plugin_studio",
                )
                _log(job, f"DSH：{str(result.get('reply') or '')[:500]}")
                if not (target / "lib" / "index.js").exists():
                    raise RuntimeError("DSH 没有生成 lib/index.js")
            _log(job, "校验插件…")
            _validate_plugin(target)
            _log(job, "安装到 qunban profile…")
            plugin_id = _install_generated(slug, target, enabled=enabled)
            _finish(
                job,
                result={
                    "slug": slug,
                    "package": f"@qunban/{slug}",
                    "id": plugin_id,
                    "path": str(target),
                    "authored_by_dsh": use_agent,
                    "next": "下一次 DSH 任务会加载；在插件页可停用/重新生成。",
                },
            )
        except Exception as exc:
            logger.warning("DSH 插件创建失败：%s", exc)
            _finish(job, error=str(exc))


def install_existing(path: str, *, enabled: bool = True) -> dict[str, Any]:
    """Validate and install a plugin already written under ``data/dsh/studio``.

    This is the tool-facing entry used by the DSH Operator when the user asks it
    to create or update a plugin.  Restricting the path to the studio directory
    keeps the operation scoped to files the Operator authored for Qunban.
    """
    target = Path(str(path or "")).expanduser().resolve()
    studio = STUDIO_DIR.resolve()
    if studio not in target.parents and target != studio:
        raise ValueError("插件目录必须位于 data/dsh/studio 下")
    if not target.is_dir():
        raise ValueError("插件目录不存在")
    _validate_plugin(target)
    package = _read_json(target / "package.json", {})
    slug = _slug(str(package.get("name") or target.name).split("/")[-1])
    plugin_id = _install_generated(slug, target, enabled=enabled)
    return {
        "ok": True,
        "id": plugin_id,
        "package": f"@qunban/{slug}",
        "path": str(target),
        "enabled": enabled,
    }


def set_enabled(plugin_id: str, enabled: bool) -> dict[str, Any]:
    plugin_id = str(plugin_id or "").strip()
    if not plugin_id:
        raise ValueError("plugin id 不能为空")
    rows = _patch_rows()
    entries = _patch_entries(rows)
    found = next((row for row in entries if row.get("id") == plugin_id), None)
    if found is None:
        raise ValueError("未找到该插件条目")
    name = str(found.get("name") or "")
    if name in {"@qunban/dsh-native"} or (
        name.startswith("@deepseek-ai/") and found.get("source") != "optional"
    ):
        raise ValueError("DSH 内置插件和群伴原生插件不允许在群伴里停用")
    found["disabled"] = not enabled
    _write_patch(rows)
    _DUMP_CACHE = None
    manifest = _read_json(MANIFEST_PATH, {"plugins": []})
    items = manifest.get("plugins") if isinstance(manifest, dict) else []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("name") == name:
                item["enabled"] = enabled
                item["updated_at"] = _utcnow()
        _write_json(MANIFEST_PATH, {"plugins": items})
    return {"id": plugin_id, "name": name, "enabled": enabled}
