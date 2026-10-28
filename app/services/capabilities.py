from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Capability, utcnow
from app.services import assistant_tools, desktop

_ASSETS_READY = False
_LAST_SYNC_AT = 0.0

BUILTIN_TOOLS: list[dict[str, Any]] = [
    {"name": "list_dir", "label": "列出目录", "desc": "列出文件/目录", "risk": "low"},
    {"name": "read_file", "label": "读取文件", "desc": "读取文本或返回二进制元数据", "risk": "low"},
    {"name": "write_file", "label": "写入文件", "desc": "新建/覆盖/追加文件", "risk": "medium"},
    {"name": "make_dir", "label": "创建目录", "desc": "创建目录", "risk": "low"},
    {"name": "delete_path", "label": "删除路径", "desc": "删除文件或目录", "risk": "high"},
    {"name": "move_path", "label": "移动/重命名", "desc": "移动文件或重命名", "risk": "medium"},
    {"name": "search_files", "label": "搜索文件", "desc": "按 glob 搜索工作区", "risk": "low"},
    {"name": "run_powershell", "label": "PowerShell", "desc": "执行 PowerShell 命令，可选 UAC 提权", "risk": "high"},
    {"name": "web_request", "label": "外部 HTTP", "desc": "调用 http/https 接口", "risk": "medium"},
    {"name": "list_skills", "label": "技能列表", "desc": "列出可用技能", "risk": "low"},
    {"name": "read_skill", "label": "读取技能", "desc": "读取 SKILL.md 和文件列表", "risk": "low"},
    {"name": "write_skill", "label": "创建/更新技能", "desc": "自我迭代：写 SKILL.md/run 脚本", "risk": "high"},
    {"name": "delete_skill", "label": "删除技能", "desc": "删除技能目录", "risk": "high"},
    {"name": "run_skill", "label": "运行技能", "desc": "执行技能的 run.ps1/run.py", "risk": "high"},
    {"name": "save_memory", "label": "保存记忆", "desc": "保存长期记忆", "risk": "low"},
    {"name": "search_memories", "label": "搜索记忆", "desc": "检索长期记忆", "risk": "low"},
    {"name": "create_task", "label": "创建任务", "desc": "创建自我迭代任务", "risk": "low"},
    {"name": "update_task", "label": "更新任务", "desc": "更新任务状态/结果", "risk": "low"},
    {"name": "list_tasks", "label": "任务列表", "desc": "列出自我迭代任务", "risk": "low"},
    {"name": "screen_capture", "label": "截屏", "desc": "截取全屏或区域并保存", "risk": "medium"},
    {"name": "screen_ocr", "label": "屏幕 OCR", "desc": "识别屏幕文字和坐标", "risk": "medium"},
    {"name": "screen_find_text", "label": "屏幕找字", "desc": "在屏幕 OCR 结果中定位文字", "risk": "medium"},
    {"name": "mouse_move", "label": "鼠标移动", "desc": "平滑移动鼠标", "risk": "high"},
    {"name": "mouse_click", "label": "鼠标点击", "desc": "模拟真实鼠标点击", "risk": "high"},
    {"name": "mouse_drag", "label": "鼠标拖拽", "desc": "模拟鼠标拖拽", "risk": "high"},
    {"name": "mouse_scroll", "label": "鼠标滚动", "desc": "模拟滚轮", "risk": "medium"},
    {"name": "keyboard_type", "label": "键盘输入", "desc": "模拟键入文本（支持中文）", "risk": "high"},
    {"name": "keyboard_press", "label": "按键", "desc": "模拟单键/重复按键", "risk": "high"},
    {"name": "keyboard_hotkey", "label": "快捷键", "desc": "模拟组合键", "risk": "high"},
    {"name": "run_mcp", "label": "运行 MCP", "desc": "在已启用的 MCP 项目目录执行命令", "risk": "high"},
    {"name": "call_interface", "label": "调用已登记接口", "desc": "调用 interfaces/ 下已启用的接口", "risk": "medium"},
    {"name": "list_capabilities", "label": "能力列表", "desc": "列出已发现的能力和开关状态", "risk": "low"},
    {"name": "discover_capabilities", "label": "重新识别能力", "desc": "重新扫描 skills/projects/interfaces", "risk": "low"},
    {"name": "read_image", "label": "读取图片 OCR", "desc": "读取本地图片并做 OCR", "risk": "medium"},
    {"name": "display_image", "label": "在对话中显示图片", "desc": "把图片复制到媒体目录并在助手对话中展示", "risk": "low"},
    {"name": "bili_status", "label": "B站账号状态", "desc": "查看 B站登录状态", "risk": "low"},
    {"name": "bili_search", "label": "B站搜索", "desc": "搜索 B站视频", "risk": "low"},
    {"name": "bili_recommend", "label": "B站推荐", "desc": "获取 B站热门/推荐视频", "risk": "low"},
    {"name": "bili_video_info", "label": "B站视频详情", "desc": "读取 B站视频标题、UP 主、播放/点赞数据", "risk": "low"},
    {"name": "bili_watch", "label": "B站刷视频", "desc": "打开视频观看并上报观看，需登录", "risk": "medium"},
    {"name": "bili_like", "label": "B站点赞", "desc": "点赞/取消点赞 B站视频，需登录", "risk": "medium"},
    {"name": "bili_comment", "label": "B站评论", "desc": "在 B站视频下发表评论，需登录", "risk": "high"},
    {"name": "bili_feed", "label": "B站刷推荐", "desc": "获取推荐视频并打开第一条", "risk": "medium"},
    {"name": "bili_transcribe", "label": "B站音频转文字", "desc": "下载视频音频并转写为文字", "risk": "medium"},
    {"name": "bili_favorite", "label": "B站收藏", "desc": "收藏 B站视频，需登录", "risk": "medium"},
    {"name": "bili_comments", "label": "B站评论列表", "desc": "读取视频热门评论", "risk": "low"},
    {"name": "bili_reply_comment", "label": "B站回复评论", "desc": "回复指定评论，需登录", "risk": "high"},
    {"name": "bili_knowledge_search", "label": "B站知识库", "desc": "检索已转写视频的语义知识库", "risk": "low"},
    {"name": "bili_knowledge_add", "label": "B站知识库写入", "desc": "按需转写视频并写入知识库", "risk": "medium"},
]

DESKTOP_NAMES = {
    "screen_capture", "screen_ocr", "screen_find_text", "mouse_move",
    "mouse_click", "mouse_drag", "mouse_scroll", "keyboard_type",
    "keyboard_press", "keyboard_hotkey",
}


def _skill_description(path: Path) -> str:
    skill = path / "SKILL.md"
    if not skill.exists():
        return "本地技能"
    try:
        for line in skill.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip("# ").strip()
            if line:
                return line[:300]
    except OSError:
        pass
    return "本地技能"


def _manifest_for_project(path: Path) -> dict[str, Any]:
    for name in ("manifest.json", "project.json"):
        file = path / name
        if not file.exists():
            continue
        try:
            value = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    for name in ("APP.md", "README.md"):
        file = path / name
        if file.exists():
            try:
                first = next(
                    (line.strip("# ").strip() for line in file.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip("# ").strip()),
                    "",
                )
                if first:
                    return {"name": path.name, "description": first[:300]}
            except OSError:
                pass
    return {}


def ensure_builtin_assets() -> None:
    """Create the screen-control skill and MCP sample on first run."""
    global _ASSETS_READY
    if _ASSETS_READY:
        return
    root = assistant_tools.workspace_root()
    skill = root / "skills" / "screen-control"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        """# SKILL: screen-control

Recognize screen text and simulate mouse/keyboard input.

## Capabilities
- screen capture / OCR / locate text
- mouse move, click, drag, scroll
- keyboard typing (Unicode), key press, hotkeys

## Usage
```powershell
python run.py capture
python run.py ocr
python run.py find OK
python run.py click 600 400
python run.py type hello
python run.py hotkey ctrl s
```

Screen control affects the real desktop. Enable it only when needed.
        """,
        encoding="utf-8",
    )
    (skill / "run.py").write_text(
        """from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from app.services import desktop  # noqa: E402

def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"usage": "capture|ocr|find|click|type|hotkey|move|scroll"}, ensure_ascii=False))
        return
    action = args[0]
    if action == "capture":
        result = desktop.capture_screen()
    elif action == "ocr":
        result = desktop.ocr_screen()
    elif action == "find":
        result = desktop.find_text_on_screen(args[1] if len(args) > 1 else "")
    elif action == "click":
        result = desktop.mouse_click(int(args[1]), int(args[2]), clicks=1)
    elif action == "move":
        result = desktop.mouse_move(int(args[1]), int(args[2]))
    elif action == "scroll":
        result = desktop.mouse_scroll(int(args[1]))
    elif action == "type":
        result = desktop.keyboard_type(" ".join(args[1:]))
    elif action == "press":
        result = desktop.keyboard_press(args[1])
    elif action == "hotkey":
        result = desktop.keyboard_hotkey(args[1:])
    else:
        result = {"error": f"unknown action: {action}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    (skill / "run.ps1").write_text(
        "param([Parameter(ValueFromRemainingArguments=$true)]$Args)\n"
        "& python \"$PSScriptRoot\\run.py\" @Args\n",
        encoding="utf-8",
    )

    interfaces = root / "interfaces"
    interfaces.mkdir(parents=True, exist_ok=True)
    sample = interfaces / "example_http.json"
    if not sample.exists():
        sample.write_text(
            json.dumps(
                {
                    "name": "httpbin_get",
                    "description": "Example external GET interface (httpbin.org)",
                    "method": "GET",
                    "url": "https://httpbin.org/get",
                    "risk": "medium",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    project = root / "projects" / "screen_control_mcp"
    project.mkdir(parents=True, exist_ok=True)
    (project / "manifest.json").write_text(
        json.dumps(
            {
                "name": "Screen Control MCP",
                "description": "Local FastAPI MCP console for screen OCR, mouse clicks and keyboard input",
                "entry": "app.py",
                "run": "python -m uvicorn app:app --host 127.0.0.1 --port 8010",
                "risk": "high",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text(
"# Screen Control MCP\n\nRun: `python -m uvicorn app:app --host 127.0.0.1 --port 8010`\n\n"
        "Endpoints: `GET /screen`, `POST /ocr`, `POST /click`, `POST /type`, `POST /key`.\n",
        encoding="utf-8",
    )
    _ASSETS_READY = True
    (project / "app.py").write_text(
        """from __future__ import annotations
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.services import desktop  # noqa: E402

app = FastAPI(title="Group Agent Screen Control MCP")

class ClickBody(BaseModel):
    x: int | None = None
    y: int | None = None
    button: str = "left"
    clicks: int = 1

class TypeBody(BaseModel):
    text: str

class KeyBody(BaseModel):
    key: str = ""
    keys: list[str] | None = None

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return '<h1>Group Agent Screen Control MCP</h1><p>GET /screen ? POST /ocr ? POST /click ? POST /type ? POST /key</p>'

@app.get("/screen")
def screen() -> dict[str, Any]:
    return desktop.capture_screen()

@app.post("/ocr")
def ocr() -> dict[str, Any]:
    return desktop.ocr_screen()

@app.post("/click")
def click(body: ClickBody) -> dict[str, Any]:
    return desktop.mouse_click(body.x, body.y, body.button, body.clicks)

@app.post("/type")
def type_text(body: TypeBody) -> dict[str, Any]:
    return desktop.keyboard_type(body.text)

@app.post("/key")
def key(body: KeyBody) -> dict[str, Any]:
    if body.keys:
        return desktop.keyboard_hotkey(body.keys)
    return desktop.keyboard_press(body.key)
""",
        encoding="utf-8",
    )


async def sync_capabilities(force: bool = False) -> None:
    global _LAST_SYNC_AT
    now = time.monotonic()
    if not force and now - _LAST_SYNC_AT < 3.0:
        return
    _LAST_SYNC_AT = now
    try:
        ensure_builtin_assets()
    except Exception:
        pass
    async with SessionLocal() as db:
        existing = {
            row.key: row
            for row in (await db.execute(select(Capability))).scalars().all()
        }

        def upsert(
            *,
            key: str,
            name: str,
            type_: str,
            description: str,
            source: str = "",
            risk: str = "medium",
            builtin: bool = False,
            enabled_assistant: bool = True,
            meta: dict[str, Any] | None = None,
        ) -> None:
            row = existing.get(key)
            if row is None:
                row = Capability(
                    key=key,
                    name=name,
                    type=type_,
                    description=description,
                    source=source,
                    risk=risk,
                    builtin=builtin,
                    enabled_assistant=enabled_assistant,
                    enabled_group=False,
                    meta=meta or {},
                )
                db.add(row)
                existing[key] = row
                return
            row.name = name
            row.type = type_
            row.description = description
            row.source = source
            row.risk = risk
            row.builtin = builtin
            row.meta = {**(row.meta or {}), **(meta or {})}
            row.updated_at = utcnow()

        for item in BUILTIN_TOOLS:
            upsert(
                key=f"tool:{item['name']}",
                name=item["label"],
                type_="tool",
                description=f"{item['desc']}（工具名：{item['name']}）",
                source="builtin",
                risk=item["risk"],
                builtin=True,
                meta={"tool": item["name"], "present": True},
            )

        root = assistant_tools.workspace_root()
        for skill in assistant_tools.list_skills():
            name = skill["name"]
            path = Path(skill["path"])
            upsert(
                key=f"skill:{name}",
                name=name,
                type_="skill",
                description=_skill_description(path),
                source=str(path),
                risk="high",
                meta={"present": True, "skill": name},
            )
        projects = root / "projects"
        if projects.is_dir():
            for path in sorted(projects.iterdir()):
                if not path.is_dir():
                    continue
                manifest = _manifest_for_project(path)
                if not manifest and not (path / "app.py").exists():
                    continue
                name = str(manifest.get("name") or path.name)
                upsert(
                    key=f"mcp:{path.name}",
                    name=name,
                    type_="mcp",
                    description=str(manifest.get("description") or f"MCP/项目：{path.name}"),
                    source=str(path),
                    risk=str(manifest.get("risk") or "high"),
                    meta={
                        "present": True,
                        "project": path.name,
                        "path": str(path),
                        "run": manifest.get("run") or "",
                        "entry": manifest.get("entry") or "",
                    },
                )
        interfaces = root / "interfaces"
        if interfaces.is_dir():
            for path in sorted(interfaces.glob("*.json")):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                rows = value if isinstance(value, list) else [value]
                for item in rows:
                    if not isinstance(item, dict) or not item.get("url"):
                        continue
                    name = str(item.get("name") or path.stem)
                    upsert(
                        key=f"api:{name}",
                        name=name,
                        type_="api",
                        description=str(item.get("description") or f"{item.get('method', 'GET')} {item['url']}"),
                        source=str(path),
                        risk=str(item.get("risk") or "medium"),
                        enabled_assistant=False,
                        meta={
                            "present": True,
                            "method": item.get("method") or "GET",
                            "url": item["url"],
                            "headers": item.get("headers") or {},
                        },
                    )
        await db.commit()


async def list_capabilities() -> list[dict[str, Any]]:
    await sync_capabilities()
    async with SessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(Capability).order_by(
                        Capability.type.asc(), Capability.name.asc()
                    )
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": row.id,
            "key": row.key,
            "name": row.name,
            "type": row.type,
            "description": row.description,
            "source": row.source,
            "risk": row.risk,
            "enabled_assistant": bool(row.enabled_assistant),
            "enabled_group": bool(row.enabled_group),
            "builtin": bool(row.builtin),
            "meta": row.meta or {},
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


async def set_capability(key: str, *, assistant: bool | None = None, group: bool | None = None) -> dict[str, Any]:
    await sync_capabilities()
    async with SessionLocal() as db:
        row = (
            await db.execute(select(Capability).where(Capability.key == key))
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("能力不存在")
        if assistant is not None:
            row.enabled_assistant = bool(assistant)
        if group is not None:
            row.enabled_group = bool(group)
        await db.commit()
        await db.refresh(row)
    return {
        "key": row.key,
        "enabled_assistant": bool(row.enabled_assistant),
        "enabled_group": bool(row.enabled_group),
    }


async def enabled_map(scope: str) -> dict[str, bool]:
    await sync_capabilities()
    async with SessionLocal() as db:
        rows = list((await db.execute(select(Capability))).scalars().all())
    if scope == "admin":
        return {row.key: True for row in rows}
    field = "enabled_assistant" if scope == "assistant" else "enabled_group"
    return {row.key: bool(getattr(row, field)) for row in rows}


async def catalog_for_scope(scope: str) -> dict[str, Any]:
    await sync_capabilities()
    async with SessionLocal() as db:
        rows = list((await db.execute(select(Capability))).scalars().all())
    if scope == "admin":
        enabled = {row.key: True for row in rows}
    else:
        field = "enabled_assistant" if scope == "assistant" else "enabled_group"
        enabled = {row.key: bool(getattr(row, field)) for row in rows}
    tools = []
    seen_tools: set[str] = set()
    for item in assistant_tools.tool_catalog():
        name = item["name"]
        if name in seen_tools:
            continue
        if not enabled.get(f"tool:{name}", False):
            continue
        seen_tools.add(name)
        tools.append(item)
    skills = [
        {"name": row.name, "description": row.description}
        for row in rows
        if row.type == "skill" and enabled.get(row.key)
    ]
    mcps = [
        {
            "name": row.name,
            "key": row.key,
            "description": row.description,
            "path": row.source,
        }
        for row in rows
        if row.type == "mcp" and enabled.get(row.key)
    ]
    apis = [
        {
            "name": row.name,
            "key": row.key,
            "description": row.description,
        }
        for row in rows
        if row.type == "api" and enabled.get(row.key)
    ]
    if skills and "run_skill" not in seen_tools:
        tools.append(
            {
                "name": "run_skill",
                "args": {"name": skills[0]["name"], "args": ""},
                "desc": "运行已启用的技能；可用技能：" + "、".join(item["name"] for item in skills),
            }
        )
    if mcps and "run_mcp" not in seen_tools:
        tools.append(
            {
                "name": "run_mcp",
                "args": {"name": mcps[0]["key"].split(":", 1)[-1], "command": ""},
                "desc": "在已启用的 MCP 项目目录执行命令；可用：" + "、".join(item["name"] for item in mcps),
            }
        )
    if apis and "call_interface" not in seen_tools:
        tools.append(
            {
                "name": "call_interface",
                "args": {"name": apis[0]["name"], "args": {}},
                "desc": "调用已登记接口；可用：" + "、".join(item["name"] for item in apis),
            }
        )
    if scope == "admin":
        tools.extend(
            [
                {"name": "list_groups", "args": {}, "desc": "列出群聊和读取/回复开关"},
                {"name": "read_group_messages", "args": {"group": "群名或 id", "limit": 50}, "desc": "读取群聊消息"},
                {"name": "send_group_message", "args": {"group": "群名或 id", "text": "..."}, "desc": "以账号身份发送群消息"},
                {"name": "set_group_access", "args": {"group": "群名或 id", "read_enabled": True, "agent_enabled": False}, "desc": "修改群读取/回复开关"},
                {"name": "pin_group", "args": {"group": "群名或 id"}, "desc": "置顶群聊"},
                {"name": "summarize_group", "args": {"group": "群名或 id"}, "desc": "立即总结群聊记忆"},
            ]
        )
    return {"tools": tools, "skills": skills, "mcps": mcps, "apis": apis, "enabled": enabled}


async def check_capability(key: str, scope: str) -> bool:
    if scope == "admin":
        return True
    enabled = await enabled_map(scope)
    return bool(enabled.get(key, False))


async def check_tool_allowed(name: str, args: dict[str, Any], scope: str) -> None:
    if name == "run_skill":
        skill = str(args.get("name") or "")
        if not await check_capability(f"skill:{skill}", scope):
            raise PermissionError(f"技能未在当前范围启用：{skill}")
        return
    if name == "read_skill":
        skill = str(args.get("name") or "")
        if not await check_capability(f"skill:{skill}", scope):
            raise PermissionError(f"技能未在当前范围启用：{skill}")
        return
    if name == "run_mcp":
        project = str(args.get("name") or "")
        if not await check_capability(f"mcp:{project}", scope):
            raise PermissionError(f"MCP 未在当前范围启用：{project}")
        return
    if name == "call_interface":
        api = str(args.get("name") or "")
        if not await check_capability(f"api:{api}", scope):
            raise PermissionError(f"接口未在当前范围启用：{api}")
        return
    if not await check_capability(f"tool:{name}", scope):
        raise PermissionError(f"工具未在当前范围启用：{name}")


async def execute_capability(name: str, args: dict[str, Any], scope: str) -> Any:
    await check_tool_allowed(name, args, scope)
    if name == "run_mcp":
        project = str(args.get("name") or "")
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(Capability).where(Capability.key == f"mcp:{project}")
                )
            ).scalar_one_or_none()
        if row is None:
            raise ValueError("MCP 不存在")
        command = str(args.get("command") or (row.meta or {}).get("run") or "")
        if not command:
            raise ValueError("这个 MCP 没有登记启动命令；请在 manifest.json 写 run")
        return await asyncio.to_thread(
            assistant_tools.run_powershell,
            command,
            row.source,
            float(args.get("timeout") or 120),
            bool(args.get("admin")),
        )
    if name == "call_interface":
        api = str(args.get("name") or "")
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(Capability).where(Capability.key == f"api:{api}")
                )
            ).scalar_one_or_none()
        if row is None:
            raise ValueError("接口不存在")
        meta = row.meta or {}
        params = args.get("args") if isinstance(args.get("args"), dict) else {}
        url = str(meta.get("url") or "")
        for key, value in params.items():
            url = url.replace("{" + key + "}", str(value))
        return await assistant_tools.web_request(
            str(meta.get("method") or "GET"),
            url,
            meta.get("headers") if isinstance(meta.get("headers"), dict) else None,
            params if str(meta.get("method") or "GET").upper() not in ("GET", "DELETE") else None,
            "",
            float(args.get("timeout") or 30),
        )
    if name.startswith("bili_"):
        from app.services import bilibili

        if name == "bili_status":
            return await bilibili.status()
        if name == "bili_search":
            return {
                "videos": await bilibili.search_videos(
                    str(args.get("keyword") or args.get("q") or ""),
                    int(args.get("limit") or 10),
                )
            }
        if name == "bili_recommend":
            return {
                "videos": await bilibili.recommend_videos(
                    int(args.get("limit") or 8)
                )
            }
        if name == "bili_video_info":
            return {
                "video": await bilibili.video_info(
                    str(args.get("bvid") or "")
                )
            }
        if name == "bili_watch":
            return await bilibili.watch_video(
                str(args.get("bvid") or ""),
                open_browser=bool(args.get("open_browser", True)),
            )
        if name == "bili_like":
            return await bilibili.like_video(
                str(args.get("bvid") or ""),
                bool(args.get("liked", True)),
            )
        if name == "bili_comment":
            return await bilibili.comment_video(
                str(args.get("bvid") or ""),
                str(args.get("text") or ""),
            )
        if name == "bili_feed":
            return await bilibili.feed_videos(
                int(args.get("limit") or 5),
                open_browser=bool(args.get("open_browser", True)),
            )
        if name == "bili_transcribe":
            return await bilibili.transcribe_video(
                str(args.get("bvid") or ""),
                str(args.get("model") or "small"),
            )
        if name == "bili_favorite":
            return await bilibili.favorite_video(
                str(args.get("bvid") or "")
            )
        if name == "bili_comments":
            return {
                "comments": await bilibili.get_video_comments(
                    str(args.get("bvid") or ""),
                    int(args.get("limit") or 10),
                )
            }
        if name == "bili_reply_comment":
            return await bilibili.reply_video_comment(
                str(args.get("bvid") or ""),
                str(args.get("text") or ""),
                int(args.get("root") or 0),
                int(args["parent"]) if args.get("parent") else None,
            )
        if name == "bili_knowledge_search":
            from app.services import bili_knowledge

            return {
                "results": await bili_knowledge.search_async(
                    str(args.get("query") or args.get("q") or ""),
                    int(args.get("limit") or 5),
                )
            }
        if name == "bili_knowledge_add":
            return await bilibili.record_video_to_knowledge(
                str(args.get("bvid") or ""),
                str(args.get("model") or "small"),
            )
    if name in DESKTOP_NAMES:
        return await asyncio.to_thread(desktop.execute, name, args)
    return await asyncio.to_thread(
        assistant_tools.execute_tool, name, args
    )
