"""Mirror 群伴 skills into the format DeepSeek Harness discovers.

DSH's filesystem skill provider scans directories for ``<name>/SKILL.md`` with
YAML frontmatter (``name`` + ``description`` are required).  群伴 skills use a
plain ``# SKILL: name`` heading, so they are converted into a generated
``data/dsh/skills`` root instead of being edited in place.  The original skill
directory remains the source of truth; code and scripts keep working through
``qunban_run_skill``.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.config import settings as process_settings
from app.services.dsh_runtime import DATA_DIR, SKILLS_DIR

ROOT = process_settings.data_dir.parent
SOURCE_DIR = ROOT / "data" / "agent_workspace" / "skills"
SYNC_DIR = SKILLS_DIR
STATE_FILE = DATA_DIR / ".skills-state"

_COPY_NAMES = {"run.py", "run.ps1", "run.cmd", "requirements.txt", "README.md"}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug or "skill"


def _parse_skill(path: Path) -> tuple[str, str, str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines()
    title = ""
    body_start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^#\s*(?:SKILL\s*[:：]\s*)?(.+)$", stripped, re.I)
        if match:
            title = match.group(1).strip()
        body_start = index + 1
        break
    body = "\n".join(lines[body_start:]).strip()
    if not body:
        return None
    description = ""
    for paragraph in re.split(r"\n\s*\n", body):
        candidate = " ".join(paragraph.split())
        if candidate and not candidate.startswith("#"):
            description = candidate[:240]
            break
    if not description:
        description = f"群伴技能 {title or path.parent.name}"
    return title or path.parent.name, description, body


def _signature() -> tuple:
    if not SOURCE_DIR.exists():
        return ()
    rows = []
    for path in sorted(SOURCE_DIR.glob("*/SKILL.md")):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(rows)


def sync_skills(*, force: bool = False) -> list[str]:
    """Convert ``data/agent_workspace/skills`` into ``data/dsh/skills``."""
    signature = _signature()
    if not signature:
        return []
    cached = ""
    try:
        cached = STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        pass
    stamp = repr(signature)
    if not force and cached == stamp:
        return [path.parent.name for path in sorted(SOURCE_DIR.glob("*/SKILL.md"))]

    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    wanted: set[str] = set()
    for source in sorted(SOURCE_DIR.glob("*/SKILL.md")):
        parsed = _parse_skill(source)
        if parsed is None:
            continue
        title, description, body = parsed
        name = _slug(source.parent.name or title)
        wanted.add(name)
        target = SYNC_DIR / name
        target.mkdir(parents=True, exist_ok=True)
        frontmatter = (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"whenToUse: 当任务涉及「{title}」时使用。\n"
            "user-invocable: true\n"
            "---\n\n"
        )
        footer = (
            "\n\n## 在群伴中执行\n"
            f"通过 MCP 工具 `qunban_run_skill(name=\"{source.parent.name}\")` 运行；"
            "不要绕过群伴的能力网关直接操作微信。\n"
        )
        (target / "SKILL.md").write_text(
            frontmatter + body + footer, encoding="utf-8"
        )
        for item in source.parent.iterdir():
            if item.is_file() and item.name in _COPY_NAMES:
                try:
                    shutil.copy2(item, target / item.name)
                except OSError:
                    pass
        names.append(name)

    for child in SYNC_DIR.iterdir():
        if child.is_dir() and child.name not in wanted:
            shutil.rmtree(child, ignore_errors=True)
    try:
        STATE_FILE.write_text(stamp, encoding="utf-8")
    except OSError:
        pass
    return names
