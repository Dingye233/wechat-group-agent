from __future__ import annotations

import asyncio
import base64
import binascii
import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

MAX_READ_CHARS = 40000
MAX_WRITE_CHARS = 400000
MAX_OUTPUT_CHARS = 20000
MAX_HTTP_BYTES = 200_000


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def workspace_root() -> Path:
    from app.services.settings import DEFAULT_SETTINGS

    # Runtime settings are read by the API layer; this fallback keeps the
    # service usable before the first settings row is written.
    root = Path(
        str(DEFAULT_SETTINGS.get("assistant_workspace") or "data/agent_workspace")
    )
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    for name in ("skills", "projects", "scripts", "downloads"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_path(value: str, base: Path | None = None) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("路径不能为空")
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        # Generated media paths are project-relative (data/media/...), while
        # ordinary assistant paths are workspace-relative. Prefer an existing
        # project-relative path for the former.
        cwd_candidate = Path.cwd() / path
        if raw.startswith(("data/", "data\\")) and cwd_candidate.exists():
            path = cwd_candidate
        else:
            path = (base or workspace_root()) / path
    return path.resolve()


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，共 {len(text)} 字符）"


def _is_probably_text(data: bytes) -> bool:
    if b"\x00" in data[:2048]:
        return False
    try:
        data[:2048].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def list_dir(path: str = ".", limit: int = 300) -> dict[str, Any]:
    target = resolve_path(path)
    if not target.exists():
        raise FileNotFoundError(f"目录不存在：{target}")
    if target.is_file():
        raise NotADirectoryError(f"不是目录：{target}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:limit]:
        try:
            stat = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size,
                    "modified": int(stat.st_mtime),
                }
            )
        except OSError:
            continue
    return {"path": str(target), "entries": entries}


def read_file(path: str, max_chars: int = MAX_READ_CHARS) -> dict[str, Any]:
    target = resolve_path(path)
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在：{target}")
    size = target.stat().st_size
    data = target.read_bytes()
    if not _is_probably_text(data):
        return {
            "path": str(target),
            "binary": True,
            "size": size,
            "base64": base64.b64encode(data[:MAX_HTTP_BYTES]).decode("ascii"),
            "truncated": size > MAX_HTTP_BYTES,
        }
    text = data.decode("utf-8", errors="replace")
    return {
        "path": str(target),
        "binary": False,
        "size": size,
        "content": _truncate(text, max_chars),
        "truncated": len(text) > max_chars,
    }


def write_file(
    path: str, content: str, append: bool = False, base64_data: bool = False
) -> dict[str, Any]:
    target = resolve_path(path)
    if not base64_data and len(content) > MAX_WRITE_CHARS:
        raise ValueError(f"文件内容超过上限 {MAX_WRITE_CHARS} 字符")
    target.parent.mkdir(parents=True, exist_ok=True)
    if base64_data:
        data = base64.b64decode(content, validate=True)
        if append:
            with target.open("ab") as handle:
                handle.write(data)
        else:
            target.write_bytes(data)
    else:
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8", newline="") as handle:
            handle.write(content)
    return {"path": str(target), "size": target.stat().st_size, "append": append}


def make_dir(path: str) -> dict[str, Any]:
    target = resolve_path(path)
    target.mkdir(parents=True, exist_ok=True)
    return {"path": str(target)}


def delete_path(path: str, recursive: bool = False) -> dict[str, Any]:
    target = resolve_path(path)
    if target.parent == target:
        raise ValueError("拒绝删除磁盘根目录")
    if not target.exists():
        raise FileNotFoundError(f"路径不存在：{target}")
    if target.is_dir():
        if any(target.iterdir()) and not recursive:
            raise ValueError("目录非空；需要 recursive=true 才会删除")
        shutil.rmtree(target) if recursive else target.rmdir()
    else:
        target.unlink()
    return {"path": str(target), "deleted": True}


def move_path(src: str, dst: str) -> dict[str, Any]:
    source = resolve_path(src)
    target = resolve_path(dst)
    if not source.exists():
        raise FileNotFoundError(f"路径不存在：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return {"from": str(source), "to": str(target)}


def search_files(pattern: str, root: str = ".", limit: int = 200) -> dict[str, Any]:
    base = resolve_path(root)
    if not base.is_dir():
        raise NotADirectoryError(f"不是目录：{base}")
    matches = []
    for item in base.rglob(pattern or "*"):
        matches.append(str(item))
        if len(matches) >= limit:
            break
    return {"root": str(base), "pattern": pattern, "matches": matches, "count": len(matches)}


def _powershell_command(command: str) -> list[str]:
    return [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        command,
    ]


def _collect_process(process: subprocess.Popen[str], timeout: float) -> tuple[int, str, str]:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        return -1, stdout or "", (stderr or "") + f"\n命令超时（{timeout:.0f}s），已终止"
    return int(process.returncode or 0), stdout or "", stderr or ""


def run_powershell(
    command: str,
    cwd: str | None = None,
    timeout: float = 60.0,
    admin: bool = False,
) -> dict[str, Any]:
    if not str(command or "").strip():
        raise ValueError("命令不能为空")
    workdir = resolve_path(cwd) if cwd else workspace_root()
    if not workdir.is_dir():
        workdir = workspace_root()
    started = time.time()
    elevated = bool(admin and not is_admin())
    if not elevated:
        process = subprocess.Popen(
            _powershell_command(command),
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        code, stdout, stderr = _collect_process(process, timeout)
        return {
            "admin": is_admin(),
            "elevated": False,
            "cwd": str(workdir),
            "exit_code": code,
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
            "seconds": round(time.time() - started, 2),
        }

    # The service is not elevated. Ask Windows for one elevated PowerShell
    # process and capture its output through a temporary file. This shows a
    # UAC prompt, which is expected and cannot be bypassed from user code.
    script_fd, script_path = tempfile.mkstemp(suffix=".ps1", prefix="group_agent_")
    out_fd, out_path = tempfile.mkstemp(suffix=".log", prefix="group_agent_")
    os.close(out_fd)
    try:
        script = (
            "$ErrorActionPreference='Continue'\r\n"
            f"$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new()\r\n"
            f"& {{\r\n{command}\r\n}} *> '{out_path.replace(chr(39), chr(39)*2)}'\r\n"
        )
        with open(script_path, "w", encoding="utf-8-sig") as handle:
            handle.write(script)
        launcher = (
            "Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait "
            "-ArgumentList '-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',"
            f"'{script_path}'"
        )
        process = subprocess.Popen(
            _powershell_command(launcher),
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        code, stdout, stderr = _collect_process(process, timeout)
        output = ""
        try:
            output = Path(out_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        return {
            "admin": False,
            "elevated": True,
            "cwd": str(workdir),
            "exit_code": code,
            "stdout": _truncate(output or stdout),
            "stderr": _truncate(stderr),
            "seconds": round(time.time() - started, 2),
        }
    finally:
        for item in (script_path, out_path):
            try:
                os.unlink(item)
            except OSError:
                pass


async def web_request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    body: str = "",
    timeout: float = 30.0,
) -> dict[str, Any]:
    method = (method or "GET").upper()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("只支持 http:// 或 https://")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.request(
            method,
            url,
            headers=headers or None,
            json=json_body if json_body is not None else None,
            content=body.encode("utf-8") if body and json_body is None else None,
        )
    content = response.content[:MAX_HTTP_BYTES]
    text = content.decode("utf-8", errors="replace")
    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "body": _truncate(text),
        "truncated": len(response.content) > MAX_HTTP_BYTES,
    }


# --------------------------------------------------------------------- skills
def _skill_root() -> Path:
    root = workspace_root() / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _skill_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", str(name or "")).strip("._")
    if not value:
        raise ValueError("技能名不能为空")
    return value[:80]


def list_skills() -> list[dict[str, Any]]:
    root = _skill_root()
    result = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        result.append(
            {
                "name": child.name,
                "path": str(child),
                "has_skill_md": skill_md.exists(),
                "updated": int(child.stat().st_mtime),
            }
        )
    return result


def read_skill(name: str) -> dict[str, Any]:
    root = _skill_root() / _skill_name(name)
    if not root.is_dir():
        raise FileNotFoundError(f"技能不存在：{name}")
    content = ""
    skill_md = root / "SKILL.md"
    if skill_md.exists():
        content = _truncate(skill_md.read_text(encoding="utf-8", errors="replace"))
    files = [
        str(item.relative_to(root)).replace("\\", "/")
        for item in root.rglob("*")
        if item.is_file()
    ]
    return {"name": root.name, "path": str(root), "skill_md": content, "files": files[:200]}


def write_skill(
    name: str, content: str, files: dict[str, str] | None = None
) -> dict[str, Any]:
    root = _skill_root() / _skill_name(name)
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(content or "", encoding="utf-8")
    for rel, file_content in (files or {}).items():
        target = (root / rel).resolve()
        if root.resolve() not in target.parents:
            raise ValueError(f"非法技能文件路径：{rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(file_content), encoding="utf-8")
    return {"name": root.name, "path": str(root)}


def delete_skill(name: str) -> dict[str, Any]:
    root = _skill_root() / _skill_name(name)
    if not root.is_dir():
        raise FileNotFoundError(f"技能不存在：{name}")
    shutil.rmtree(root)
    return {"name": name, "deleted": True}


def run_skill(name: str, args: str = "", timeout: float = 120.0) -> dict[str, Any]:
    root = _skill_root() / _skill_name(name)
    if not root.is_dir():
        raise FileNotFoundError(f"技能不存在：{name}")
    ps1 = root / "run.ps1"
    py = root / "run.py"
    # Prefer Python so the skill inherits the service venv (Pillow, httpx,
    # pyautogui all live there). run.ps1 is kept for PowerShell-only skills.
    if py.exists():
        process = subprocess.Popen(
            [os.sys.executable, str(py), *args.split()],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        code, stdout, stderr = _collect_process(process, timeout)
        return {
            "exit_code": code,
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
            "cwd": str(root),
        }
    if ps1.exists():
        command = f"& '{ps1}' {args}".strip()
        return run_powershell(command, cwd=str(root), timeout=timeout)
    return {
        "error": "技能没有 run.ps1 或 run.py",
        "skill_md": read_skill(name)["skill_md"],
    }



def read_image(path: str, backend: str = "auto") -> dict[str, Any]:
    target = resolve_path(path)
    if not target.is_file():
        raise FileNotFoundError(f"图片不存在：{target}")
    from app.services import desktop

    result = desktop.ocr_image(target, backend)
    return {
        "path": str(target),
        "size": target.stat().st_size,
        "text": _truncate(result.get("text") or "", 12000),
        "items": (result.get("items") or [])[:300],
        "backend": result.get("backend"),
    }


def display_image(path: str, caption: str = "") -> dict[str, Any]:
    source = resolve_path(path)
    if not source.is_file():
        raise FileNotFoundError(f"图片不存在：{source}")
    media_root = (Path("data") / "media" / "assistant").resolve()
    try:
        relative = source.resolve().relative_to(media_root)
    except ValueError:
        relative = None
    if relative is not None:
        # Screenshots and uploaded images are already served by the assistant
        # media mount. Reuse the URL instead of creating a duplicate copy.
        return {
            "path": str(source),
            "url": f"/media/assistant/{relative.as_posix()}",
            "caption": caption,
        }
    target_dir = Path("data") / "media" / "assistant" / "uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{int(time.time())}_{source.name}"
    shutil.copy2(source, target)
    return {
        "path": str(target),
        "url": f"/media/assistant/uploads/{target.name}",
        "caption": caption,
    }


def tool_catalog() -> list[dict[str, Any]]:
    """Compact catalog injected into the assistant prompt."""
    return [
        {"name": "list_dir", "args": {"path": "."}, "desc": "列出目录"},
        {"name": "read_file", "args": {"path": "a.txt"}, "desc": "读取文本文件"},
        {"name": "write_file", "args": {"path": "a.txt", "content": "..."}, "desc": "新建/覆盖文件"},
        {"name": "write_file", "args": {"path": "a.bin", "content": "...", "append": True}, "desc": "追加文件"},
        {"name": "make_dir", "args": {"path": "projects/demo"}, "desc": "创建目录"},
        {"name": "delete_path", "args": {"path": "tmp.txt", "recursive": False}, "desc": "删除文件/目录"},
        {"name": "move_path", "args": {"src": "a", "dst": "b"}, "desc": "移动/重命名"},
        {"name": "search_files", "args": {"pattern": "*.py", "root": "."}, "desc": "查找文件"},
        {"name": "run_powershell", "args": {"command": "Get-Date", "admin": False}, "desc": "执行 PowerShell"},
        {"name": "web_request", "args": {"method": "GET", "url": "https://..."}, "desc": "调用外部接口"},
        {"name": "list_skills", "args": {}, "desc": "列出技能"},
        {"name": "read_skill", "args": {"name": "demo"}, "desc": "读取技能"},
        {"name": "write_skill", "args": {"name": "demo", "content": "# SKILL...", "files": {}}, "desc": "创建/更新技能"},
        {"name": "run_skill", "args": {"name": "demo", "args": ""}, "desc": "运行技能脚本"},
        {"name": "delete_skill", "args": {"name": "demo"}, "desc": "删除技能"},
        {"name": "save_memory", "args": {"content": "...", "importance": 3}, "desc": "保存长期记忆"},
        {"name": "search_memories", "args": {"query": "..."}, "desc": "搜索记忆"},
        {"name": "create_task", "args": {"title": "..."}, "desc": "创建自我迭代任务"},
        {"name": "update_task", "args": {"task_id": 1, "status": "done", "result": "..."}, "desc": "更新任务"},
        {"name": "list_tasks", "args": {}, "desc": "列出任务"},
        {"name": "screen_capture", "args": {"region": None}, "desc": "截屏，并在助手对话中直接显示图片"},
        {"name": "screen_ocr", "args": {"region": None}, "desc": "屏幕 OCR，返回文字和坐标"},
        {"name": "screen_find_text", "args": {"text": "确定"}, "desc": "在屏幕文字中定位"},
        {"name": "mouse_move", "args": {"x": 100, "y": 200}, "desc": "平滑移动鼠标"},
        {"name": "mouse_click", "args": {"x": 100, "y": 200, "button": "left", "clicks": 1}, "desc": "鼠标点击"},
        {"name": "mouse_drag", "args": {"x1": 100, "y1": 200, "x2": 300, "y2": 400}, "desc": "鼠标拖拽"},
        {"name": "mouse_scroll", "args": {"amount": -500}, "desc": "滚轮"},
        {"name": "keyboard_type", "args": {"text": "hello"}, "desc": "模拟键盘输入"},
        {"name": "keyboard_press", "args": {"key": "enter", "presses": 1}, "desc": "按键"},
        {"name": "keyboard_hotkey", "args": {"keys": ["ctrl", "s"]}, "desc": "快捷键"},
        {"name": "list_capabilities", "args": {}, "desc": "列出已发现的能力和开关"},
        {"name": "discover_capabilities", "args": {}, "desc": "重新扫描技能/MCP/接口"},
        {"name": "read_image", "args": {"path": "image.png"}, "desc": "读取图片并 OCR"},
        {"name": "display_image", "args": {"path": "image.png", "caption": ""}, "desc": "在助手对话中显示图片"},
        {"name": "bili_status", "args": {}, "desc": "查看 B站账号登录状态"},
        {"name": "bili_search", "args": {"keyword": "编程", "limit": 10}, "desc": "搜索 B站视频"},
        {"name": "bili_recommend", "args": {"limit": 8}, "desc": "获取 B站热门推荐视频"},
        {"name": "bili_video_info", "args": {"bvid": "BV..."}, "desc": "读取 B站视频详情"},
        {"name": "bili_watch", "args": {"bvid": "BV...", "open_browser": True}, "desc": "打开并观看 B站视频，需登录"},
        {"name": "bili_like", "args": {"bvid": "BV...", "liked": True}, "desc": "B站点赞/取消点赞，需登录"},
        {"name": "bili_comment", "args": {"bvid": "BV...", "text": "..."}, "desc": "B站视频评论，需登录"},
        {"name": "bili_feed", "args": {"limit": 5, "open_browser": True}, "desc": "获取推荐并打开第一条视频"},
        {"name": "bili_transcribe", "args": {"bvid": "BV...", "model": "small"}, "desc": "下载 B站视频音频并转写文字"},
        {"name": "bili_favorite", "args": {"bvid": "BV..."}, "desc": "收藏 B站视频，需登录"},
        {"name": "bili_comments", "args": {"bvid": "BV...", "limit": 10}, "desc": "读取 B站视频热门评论"},
        {"name": "bili_reply_comment", "args": {"bvid": "BV...", "text": "...", "root": 123}, "desc": "回复 B站评论，需登录"},
        {"name": "bili_knowledge_search", "args": {"query": "主题", "limit": 5}, "desc": "检索 B站视频知识库"},
        {"name": "bili_knowledge_add", "args": {"bvid": "BV...", "model": "small"}, "desc": "按需转写 B站视频并记录到知识库"},
        {"name": "bili_last_opinion", "args": {}, "desc": "读取最近一次 B站视频分析中 Agent 自己的看法"},
        {"name": "send_contact_message", "args": {"contact_id": 72, "text": "..."}, "desc": "给微信联系人发送文本，必须走 wechatauto-replica"},
        {"name": "send_contact_image", "args": {"contact_id": 72, "path": "..."}, "desc": "给微信联系人发送图片，必须走 wechatauto-replica"},
    ]


def execute_tool(name: str, args: dict[str, Any] | None = None) -> Any:
    """Synchronous tool dispatcher used by the assistant loop."""
    args = args or {}
    if name == "list_dir":
        return list_dir(args.get("path") or ".", int(args.get("limit") or 300))
    if name == "read_file":
        return read_file(args.get("path") or "", int(args.get("max_chars") or MAX_READ_CHARS))
    if name == "write_file":
        return write_file(
            args.get("path") or "",
            str(args.get("content") or ""),
            bool(args.get("append")),
            bool(args.get("base64_data")),
        )
    if name == "make_dir":
        return make_dir(args.get("path") or "")
    if name == "delete_path":
        return delete_path(args.get("path") or "", bool(args.get("recursive")))
    if name == "move_path":
        return move_path(args.get("src") or "", args.get("dst") or "")
    if name == "search_files":
        return search_files(
            args.get("pattern") or "*",
            args.get("root") or ".",
            int(args.get("limit") or 200),
        )
    if name == "read_image":
        return read_image(
            str(args.get("path") or ""),
            str(args.get("backend") or "auto"),
        )
    if name == "display_image":
        return display_image(
            str(args.get("path") or ""),
            str(args.get("caption") or ""),
        )
    if name == "run_powershell":
        return run_powershell(
            args.get("command") or "",
            args.get("cwd"),
            float(args.get("timeout") or 60),
            bool(args.get("admin")),
        )
    if name == "list_skills":
        return list_skills()
    if name == "read_skill":
        return read_skill(args.get("name") or "")
    if name == "write_skill":
        return write_skill(
            args.get("name") or "",
            str(args.get("content") or ""),
            args.get("files") or {},
        )
    if name == "delete_skill":
        return delete_skill(args.get("name") or "")
    if name == "run_skill":
        return run_skill(
            args.get("name") or "",
            str(args.get("args") or ""),
            float(args.get("timeout") or 120),
        )
    raise ValueError(f"未知工具：{name}")
