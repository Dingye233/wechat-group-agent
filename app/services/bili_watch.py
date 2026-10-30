# -*- coding: utf-8 -*-
"""B 站监听后台：定时轮询 + 自动回复 + 管理员指令。

配置存 data/bili_watch.json；已处理游标由 bili_listen.py 维护（bili_seen.json）。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "data" / "agent_workspace" / "bili_listen.py"
PYTHON = ROOT / ".venv312" / "Scripts" / "python.exe"
CONFIG = ROOT / "data" / "bili_watch.json"
LOG = ROOT / "data" / "bili_watch_log.jsonl"

PERSONA_REPLIES = [
    "这条本鱼收下了，回头再细说🐟",
    "说得有道理，本鱼记在小本本上了",
    "哈，这条被你说透了，鱼生也有这一面",
    "本鱼点头，海水都晃了一下",
    "记下了，下期就照这个方向讲",
    "你这么一讲，本鱼也想再翻一遍",
]

DEFAULT = {
    "enabled": False,
    "interval_minutes": 10,
    "admin_name": "nanbei123",
    "admin_uid": 3493087403838091,
    "reply_others": True,
    "reply_admin": True,
}

_loop_task: asyncio.Task | None = None
_last_run: dict[str, Any] = {"at": 0.0, "summary": ""}


def load_config() -> dict[str, Any]:
    conf = dict(DEFAULT)
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            conf.update({k: v for k, v in data.items() if k in DEFAULT})
    except Exception:
        pass
    return conf


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    conf = load_config()
    for key, value in (patch or {}).items():
        if key in DEFAULT:
            conf[key] = value
    conf["interval_minutes"] = max(1, min(1440, int(conf.get("interval_minutes") or 10)))
    conf["admin_uid"] = int(conf.get("admin_uid") or 0)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
    return conf


def _run(args: list[str], timeout: int = 60) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
        )
        line = (proc.stdout or "").strip().splitlines()
        return json.loads(line[-1]) if line else {"ok": False, "error": proc.stderr[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _log(entry: dict[str, Any]) -> None:
    entry["at"] = time.time()
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _commands(text: str) -> dict[str, Any]:
    """从管理员消息里解析指令：三连 / 回复 / 发动态（动态先只记录）。"""
    body = (text or "").strip()
    plans: dict[str, Any] = {"triple": None, "reply": None, "post": None}
    m = re.search(r"BV[0-9A-Za-z]{10}", body)
    if m and re.search(r"三连|点赞|投币|收藏", body):
        plans["triple"] = {"bvid": m.group(0), "like": True, "coin": True, "fav": True}
    m2 = re.match(r"^(?:回复|回)[:：\s]*(.+)$", body)
    if m2:
        plans["reply"] = m2.group(1).strip()
    if re.search(r"发个?动态|发条动态|发动态", body):
        plans["post"] = body
    return plans


def poll_once(manual: bool = False) -> dict[str, Any]:
    conf = load_config()
    admin = str(conf.get("admin_name") or DEFAULT["admin_name"])
    data = _run(["new", "--admin", admin])
    if not data.get("ok"):
        _last_run.update({"at": time.time(), "summary": f"拉取失败：{data.get('error')}"})
        _log({"kind": "error", "error": data.get("error"), "manual": manual})
        return data

    actions: list[dict[str, Any]] = []
    for item in data.get("at", []) + data.get("reply", []):
        is_admin = bool(item.get("is_admin"))
        if is_admin and not conf.get("reply_admin", True):
            continue
        if not is_admin and not conf.get("reply_others", True):
            continue
        text = str(item.get("text") or "")
        oid, rpid = item.get("oid"), item.get("rpid")
        if is_admin:
            plans = _commands(text)
            if plans["triple"]:
                p = plans["triple"]
                args = ["triple", "--bvid", p["bvid"]]
                for flag in ("like", "coin", "fav"):
                    if p[flag]:
                        args.append(f"--{flag}")
                res = _run(args)
                actions.append({"type": "triple", "bvid": p["bvid"], "result": res})
            if plans["reply"] and oid and rpid:
                msg = plans["reply"][:120]
                res = _run(["reply", "--oid", str(oid), "--rpid", str(rpid), "--text", msg])
                actions.append({"type": "reply_admin", "text": msg, "result": res})
            elif not plans["triple"] and oid and rpid:
                msg = random.choice(PERSONA_REPLIES)
                res = _run(["reply", "--oid", str(oid), "--rpid", str(rpid), "--text", msg])
                actions.append({"type": "reply_admin_default", "text": msg, "result": res})
        else:
            if oid and rpid:
                msg = random.choice(PERSONA_REPLIES)
                res = _run(["reply", "--oid", str(oid), "--rpid", str(rpid), "--text", msg])
                actions.append({"type": "reply_other", "to": item.get("uname"), "text": msg, "result": res})

    summary = f"新消息 {data.get('new_counts')}｜动作 {len(actions)}"
    _last_run.update({"at": time.time(), "summary": summary})
    _log({"kind": "poll", "manual": manual, "counts": data.get("new_counts"), "actions": actions})
    return {"ok": True, "counts": data.get("new_counts"), "actions": actions, "summary": summary}


async def loop() -> None:
    while True:
        conf = load_config()
        interval = max(60, int(conf.get("interval_minutes") or 10) * 60)
        try:
            if conf.get("enabled"):
                await asyncio.to_thread(poll_once)
        except Exception as exc:  # noqa: BLE001
            _log({"kind": "loop_error", "error": str(exc)})
        await asyncio.sleep(interval)


def start_loop() -> None:
    global _loop_task
    if _loop_task is None or _loop_task.done():
        _loop_task = asyncio.create_task(loop())


def status() -> dict[str, Any]:
    conf = load_config()
    recent: list[dict[str, Any]] = []
    try:
        lines = LOG.read_text(encoding="utf-8").strip().splitlines()[-12:]
        recent = [json.loads(line) for line in lines if line.strip()]
    except Exception:
        pass
    return {
        "ok": True,
        "config": conf,
        "last_run": _last_run,
        "running": bool(_loop_task and not _loop_task.done()),
        "recent": recent,
    }
