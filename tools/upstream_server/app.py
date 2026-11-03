# -*- coding: utf-8 -*-
"""上层调度服务（Upstream Dispatch Service）

按“正常上层系统”的完整逻辑来做，不只是回包玩具：

- 任务生命周期：创建 → 下发 → 下层受理 → 执行中 → 到达 → 完成 / 取消 / 失败
- 状态机校验：不允许的回退/跳转会被拒绝并记录
- 下发重试：失败按指数退避重试 N 次，超时/异常都进日志
- 幂等：同一 taskNo 重复创建不会生成两条
- 设备心跳：机器人/车辆在线状态、电量、位置、离线判定
- 报警上报：按等级记录，可在页面确认
- 接口鉴权：可选 X-Api-Key（对下层回调与任务接口生效）
- 数据落 SQLite：任务 / 设备 / 报警 / 调用日志 / 配置
- 自带 Swagger：/docs

启动：python app.py --port 8030
页面：http://127.0.0.1:8030/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "upstream.db"

TASK_STATUS = [
    "created",     # 已创建
    "dispatched",  # 已下发
    "accepted",    # 下层已受理
    "moving",      # 执行中
    "arrived",     # 已到达
    "completed",   # 已完成
    "cancelled",   # 已取消
    "failed",      # 失败
]
TASK_STATUS_CN = {
    "created": "已创建", "dispatched": "已下发", "accepted": "已受理", "moving": "执行中",
    "arrived": "已到达", "completed": "已完成", "cancelled": "已取消", "failed": "失败",
}
ALLOWED = {
    "created": {"dispatched", "cancelled", "failed"},
    "dispatched": {"accepted", "moving", "cancelled", "failed"},
    "accepted": {"moving", "arrived", "cancelled", "failed"},
    "moving": {"arrived", "completed", "failed", "cancelled"},
    "arrived": {"completed", "failed"},
    "completed": set(),
    "cancelled": set(),
    "failed": {"dispatched"},  # 允许重下发
}
TERMINAL = {"completed", "cancelled", "failed"}

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


# ------------------------------------------------------------------ 存储
def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_no TEXT PRIMARY KEY,
                from_point TEXT NOT NULL,
                to_point TEXT NOT NULL,
                robot_code TEXT DEFAULT '',
                priority INTEGER DEFAULT 5,
                status TEXT NOT NULL,
                progress REAL DEFAULT 0,
                message TEXT DEFAULT '',
                retries INTEGER DEFAULT 0,
                last_error TEXT DEFAULT '',
                payload TEXT DEFAULT '{}',
                created_at TEXT, updated_at TEXT, finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS robots (
                code TEXT PRIMARY KEY, online INTEGER DEFAULT 0, battery REAL DEFAULT 0,
                x REAL DEFAULT 0, y REAL DEFAULT 0, status TEXT DEFAULT 'idle',
                last_seen TEXT, extra TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS alarms (
                id INTEGER PRIMARY KEY AUTOINCREMENT, robot_code TEXT, code TEXT,
                level TEXT DEFAULT 'warn', message TEXT DEFAULT '', acked INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, direction TEXT,
                method TEXT, path TEXT, status INTEGER, summary TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
            """
        )
        _conn.commit()
    return _conn


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_setting(key: str, default: Any = None) -> Any:
    row = db().execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["v"])
    except Exception:
        return row["v"]


def set_setting(key: str, value: Any) -> None:
    with _lock:
        db().execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (key, json.dumps(value, ensure_ascii=False)))
        db().commit()


def log_call(direction: str, method: str, path: str, status: int, summary: str) -> None:
    with _lock:
        db().execute("INSERT INTO calls(ts,direction,method,path,status,summary) VALUES(?,?,?,?,?,?)",
                     (now(), direction, method, path, status, summary[:600]))
        db().commit()


# ------------------------------------------------------------------ 鉴权
async def require_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_setting("api_key", "") or ""
    if not expected:
        return
    if (x_api_key or "") != expected:
        raise HTTPException(status_code=401, detail="X-Api-Key 不正确")


# ------------------------------------------------------------------ 任务逻辑
def task_row(task_no: str) -> dict[str, Any] | None:
    row = db().execute("SELECT * FROM tasks WHERE task_no=?", (task_no,)).fetchone()
    return dict(row) if row else None


def list_tasks(status: str = "", limit: int = 100) -> list[dict[str, Any]]:
    sql = "SELECT * FROM tasks"
    args: list[Any] = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in db().execute(sql, args)]


def create_task(payload: dict[str, Any]) -> dict[str, Any]:
    task_no = str(payload.get("taskNo") or payload.get("task_no") or "").strip() or f"T{int(time.time()*1000)%10000000}"
    exist = task_row(task_no)
    if exist:
        return exist  # 幂等
    from_point = str(payload.get("from") or payload.get("fromPoint") or "").strip()
    to_point = str(payload.get("to") or payload.get("toPoint") or "").strip()
    if not from_point or not to_point:
        raise HTTPException(status_code=400, detail="from / to 必填")
    robot = str(payload.get("robotCode") or "").strip()
    ts = now()
    with _lock:
        db().execute(
            "INSERT INTO tasks(task_no,from_point,to_point,robot_code,priority,status,payload,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (task_no, from_point, to_point, robot, int(payload.get("priority") or 5),
             "created", json.dumps(payload, ensure_ascii=False), ts, ts),
        )
        db().commit()
    return task_row(task_no) or {}


def transit(task_no: str, target: str, *, message: str = "", progress: float | None = None) -> dict[str, Any]:
    row = task_row(task_no)
    if not row:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_no}")
    cur = row["status"]
    if target == cur:
        return row
    if target not in ALLOWED.get(cur, set()):
        raise HTTPException(status_code=409, detail=f"状态不允许：{cur} → {target}")
    fields = {"status": target, "updated_at": now()}
    if message:
        fields["message"] = message[:300]
    if progress is not None:
        fields["progress"] = max(0.0, min(1.0, float(progress)))
    if target in TERMINAL:
        fields["finished_at"] = now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        db().execute(f"UPDATE tasks SET {sets} WHERE task_no=?", (*fields.values(), task_no))
        db().commit()
    return task_row(task_no) or {}


async def dispatch(task_no: str, force: bool = False) -> dict[str, Any]:
    """把任务推给下层（RCS/机器人），失败按指数退避重试。"""
    conf = {
        "url": get_setting("downstream_base", "") or "",
        "path": get_setting("dispatch_path", "/rcs/task/dispatch") or "/rcs/task/dispatch",
        "key": get_setting("downstream_key", "") or "",
        "timeout": float(get_setting("timeout", 8) or 8),
        "retry": int(get_setting("retry", 3) or 3),
    }
    row = task_row(task_no)
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not conf["url"]:
        return {"ok": False, "error": "还没配置下层地址（设置页填）"}
    if row["status"] in TERMINAL and not force:
        return {"ok": False, "error": f"任务已结束：{row['status']}"}
    url = conf["url"].rstrip("/") + conf["path"]
    body = {
        "taskNo": row["task_no"], "from": row["from_point"], "to": row["to_point"],
        "robotCode": row["robot_code"], "priority": row["priority"],
        "callback": "/api/v1/callback/task",
    }
    headers = {"content-type": "application/json"}
    if conf["key"]:
        headers["X-Api-Key"] = conf["key"]
    attempt = 0
    last_error = ""
    while attempt <= conf["retry"]:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=conf["timeout"]) as client:
                resp = await client.post(url, json=body, headers=headers)
            text = resp.text[:400]
            log_call("down", "POST", url, resp.status_code, text)
            if 200 <= resp.status_code < 300:
                with _lock:
                    db().execute("UPDATE tasks SET retries=?, last_error='' WHERE task_no=?", (attempt - 1, task_no))
                    db().commit()
                if row["status"] in ("created", "failed"):
                    transit(task_no, "dispatched", message=f"已下发（第 {attempt} 次）")
                return {"ok": True, "status": resp.status_code, "response": text}
            last_error = f"HTTP {resp.status_code}: {text[:160]}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            log_call("down", "POST", url, 0, last_error)
        await asyncio.sleep(min(8, 0.6 * (2 ** (attempt - 1))))
    with _lock:
        db().execute("UPDATE tasks SET retries=?, last_error=? WHERE task_no=?", (attempt, last_error[:300], task_no))
        db().commit()
    return {"ok": False, "error": last_error}


# ------------------------------------------------------------------ 应用
@asynccontextmanager
async def lifespan(app: FastAPI):
    db()
    yield


app = FastAPI(title="上层调度服务", version="1.0.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


# ---------------- 下层回调（RCS/机器人 → 上层）
@app.post("/api/v1/callback/task", dependencies=[Depends(require_key)])
async def callback_task(payload: dict, request: Request) -> dict:
    task_no = str(payload.get("taskNo") or payload.get("task_no") or "")
    status = str(payload.get("status") or "").strip()
    log_call("up", "POST", str(request.url.path), 200, json.dumps(payload, ensure_ascii=False)[:500])
    if not task_no or not status:
        raise HTTPException(status_code=400, detail="taskNo / status 必填")
    if status not in TASK_STATUS:
        raise HTTPException(status_code=400, detail=f"未知状态：{status}")
    row = transit(task_no, status,
                  message=str(payload.get("message") or ""),
                  progress=payload.get("progress"))
    return {"ok": True, "task": row}


@app.post("/api/v1/callback/heartbeat", dependencies=[Depends(require_key)])
async def callback_heartbeat(payload: dict, request: Request) -> dict:
    code = str(payload.get("robotCode") or payload.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="robotCode 必填")
    log_call("up", "POST", str(request.url.path), 200, f"{code} battery={payload.get('battery')}")
    with _lock:
        db().execute(
            "INSERT INTO robots(code,online,battery,x,y,status,last_seen,extra) VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(code) DO UPDATE SET online=1,battery=excluded.battery,x=excluded.x,y=excluded.y,"
            " status=excluded.status,last_seen=excluded.last_seen,extra=excluded.extra",
            (code, 1, float(payload.get("battery") or 0), float(payload.get("x") or 0),
             float(payload.get("y") or 0), str(payload.get("status") or "idle"), now(),
             json.dumps(payload, ensure_ascii=False)),
        )
        db().commit()
    return {"ok": True}


@app.post("/api/v1/callback/alarm", dependencies=[Depends(require_key)])
async def callback_alarm(payload: dict, request: Request) -> dict:
    with _lock:
        db().execute("INSERT INTO alarms(robot_code,code,level,message,created_at) VALUES(?,?,?,?,?)",
                     (str(payload.get("robotCode") or ""), str(payload.get("code") or ""),
                      str(payload.get("level") or "warn"), str(payload.get("message") or ""), now()))
        db().commit()
    log_call("up", "POST", str(request.url.path), 200, json.dumps(payload, ensure_ascii=False)[:300])
    return {"ok": True}


@app.get("/api/v1/ping")
async def ping() -> dict:
    return {"ok": True, "service": "upstream-dispatch", "time": now()}


# ---------------- 上层管理接口
@app.post("/api/v1/tasks", dependencies=[Depends(require_key)])
async def api_create_task(payload: dict) -> dict:
    task = create_task(payload)
    result = await dispatch(task["task_no"])
    fresh = task_row(task["task_no"]) or task
    return {"ok": True, "task": fresh, "dispatch": result}


@app.get("/api/v1/tasks", dependencies=[Depends(require_key)])
async def api_tasks(status: str = Query(default=""), limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {"ok": True, "tasks": list_tasks(status, limit)}


@app.get("/api/v1/tasks/{task_no}", dependencies=[Depends(require_key)])
async def api_task(task_no: str) -> dict:
    row = task_row(task_no)
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "task": row}


@app.post("/api/v1/tasks/{task_no}/cancel", dependencies=[Depends(require_key)])
async def api_cancel(task_no: str) -> dict:
    row = transit(task_no, "cancelled", message="上层取消")
    log_call("down", "POST", f"/api/v1/tasks/{task_no}/cancel", 200, "cancelled")
    return {"ok": True, "task": row}


@app.post("/api/v1/tasks/{task_no}/redispatch", dependencies=[Depends(require_key)])
async def api_redispatch(task_no: str) -> dict:
    return {"ok": True, "result": await dispatch(task_no, force=True)}


@app.get("/api/v1/robots", dependencies=[Depends(require_key)])
async def api_robots() -> dict:
    rows = [dict(r) for r in db().execute("SELECT * FROM robots ORDER BY code")]
    for r in rows:
        try:
            delta = (datetime.now() - datetime.strptime(r["last_seen"] or now(), "%Y-%m-%d %H:%M:%S")).seconds
        except Exception:
            delta = 9999
        r["online"] = 1 if delta < 90 else 0
    return {"ok": True, "robots": rows}


@app.get("/api/v1/alarms", dependencies=[Depends(require_key)])
async def api_alarms(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    rows = [dict(r) for r in db().execute("SELECT * FROM alarms ORDER BY id DESC LIMIT ?", (limit,))]
    return {"ok": True, "alarms": rows}


@app.post("/api/v1/alarms/{alarm_id}/ack", dependencies=[Depends(require_key)])
async def api_alarm_ack(alarm_id: int) -> dict:
    with _lock:
        db().execute("UPDATE alarms SET acked=1 WHERE id=?", (alarm_id,))
        db().commit()
    return {"ok": True}


@app.get("/api/v1/calls", dependencies=[Depends(require_key)])
async def api_calls(limit: int = Query(default=200, ge=1, le=1000), direction: str = "") -> dict:
    sql = "SELECT * FROM calls"
    args: list[Any] = []
    if direction:
        sql += " WHERE direction=?"
        args.append(direction)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return {"ok": True, "calls": [dict(r) for r in db().execute(sql, args)]}


@app.get("/api/v1/settings")
async def api_settings() -> dict:
    keys = ("downstream_base", "dispatch_path", "downstream_key", "timeout", "retry", "api_key")
    return {"ok": True, "settings": {k: get_setting(k, "") for k in keys}}


@app.post("/api/v1/settings")
async def api_settings_save(payload: dict) -> dict:
    for k in ("downstream_base", "dispatch_path", "downstream_key", "api_key"):
        if k in payload:
            set_setting(k, str(payload.get(k) or ""))
    for k in ("timeout", "retry"):
        if k in payload:
            set_setting(k, int(payload.get(k) or 0))
    return {"ok": True, "settings": (await api_settings())["settings"]}


@app.get("/api/v1/stats")
async def api_stats() -> dict:
    def count(sql: str, args: tuple = ()) -> int:
        return db().execute(sql, args).fetchone()[0]
    return {"ok": True, "stats": {
        "tasks": count("SELECT COUNT(*) FROM tasks"),
        "running": count("SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed','cancelled','failed')"),
        "completed": count("SELECT COUNT(*) FROM tasks WHERE status='completed'"),
        "failed": count("SELECT COUNT(*) FROM tasks WHERE status='failed'"),
        "robots": count("SELECT COUNT(*) FROM robots"),
        "alarms": count("SELECT COUNT(*) FROM alarms WHERE acked=0"),
    }}


INDEX_HTML = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>上层调度服务</title>
<style>
:root{--bg:#0a1114;--panel:#111a1e;--line:#1e2b31;--txt:#e6f1f2;--dim:#8fa8ad;--acc:#3ad39a;--warn:#e2b13c;--err:#e2654f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.55 -apple-system,"Microsoft YaHei",sans-serif}
header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
h1{font-size:16px;margin:0;letter-spacing:.05em}
.wrap{padding:16px;max-width:1320px;margin:0 auto}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:14px}
.kpi div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:20px;color:var(--acc)}
.kpi span{font-size:12px;color:var(--dim)}
.tabs{display:flex;gap:8px;margin:0 0 14px;flex-wrap:wrap}
.tabs button{background:var(--panel);border:1px solid var(--line);color:var(--txt);padding:7px 14px;border-radius:8px;cursor:pointer}
.tabs button.on{background:#153a2d;border-color:var(--acc);color:#c9ffe6}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:500}
input,select,textarea{background:#0b1417;border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:7px 9px;font:13px/1.4 inherit}
textarea{width:100%;min-height:110px;font-family:ui-monospace,Consolas,monospace}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--dim)}
button.primary{background:#1c5c44;border:1px solid var(--acc);color:#d8ffee;border-radius:8px;padding:8px 16px;cursor:pointer}
button.ghost{background:#16232a;border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:7px 12px;cursor:pointer}
.pill{font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);color:var(--dim)}
.pill.run{color:#9df3c9;border-color:#2c6b52}.pill.done{color:#9ad4ff;border-color:#2b4d6b}
.pill.bad{color:#ffb9ae;border-color:#6b2f28}.pill.warn{color:#ffd79a;border-color:#6b5228}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.hint{color:var(--dim);font-size:12px}
</style></head><body>
<header><h1>上层调度服务</h1><span class="hint" id="clock"></span><span style="flex:1"></span>
<span class="hint">Swagger：<a href="/docs" target="_blank" style="color:#8fe6c0">/docs</a></span></header>
<div class="wrap">
  <div class="kpi">
    <div><b id="k-tasks">-</b><span>任务总数</span></div>
    <div><b id="k-run">-</b><span>进行中</span></div>
    <div><b id="k-done">-</b><span>已完成</span></div>
    <div><b id="k-fail">-</b><span>失败</span></div>
    <div><b id="k-robots">-</b><span>登记设备</span></div>
    <div><b id="k-alarms">-</b><span>未确认报警</span></div>
  </div>
  <div class="tabs">
    <button data-t="tasks" class="on">任务</button>
    <button data-t="robots">设备</button>
    <button data-t="alarms">报警</button>
    <button data-t="calls">调用日志</button>
    <button data-t="cfg">设置</button>
  </div>

  <section id="p-tasks">
    <div class="card">
      <div class="row">
        <label>起点<input id="t-from" value="BUFFER-01" style="width:140px"></label>
        <label>终点<input id="t-to" value="LINE-02" style="width:140px"></label>
        <label>机器人（可选）<input id="t-robot" placeholder="AGV-01" style="width:130px"></label>
        <label>优先级<input id="t-pri" value="5" style="width:80px"></label>
        <button class="primary" onclick="createTask()">创建并下发</button>
        <button class="ghost" onclick="loadTasks()">刷新</button>
      </div>
      <p class="hint">创建后自动推给下层：<code id="d-url">未配置</code>；下层回调 /api/v1/callback/task 推进状态。</p>
    </div>
    <div class="card"><table><thead><tr><th>任务号</th><th>起点→终点</th><th>机器人</th><th>状态</th><th>进度</th><th>重试</th><th>更新时间</th><th>操作</th></tr></thead><tbody id="task-rows"></tbody></table></div>
  </section>

  <section id="p-robots" hidden><div class="card"><table><thead><tr><th>设备</th><th>在线</th><th>电量</th><th>位置</th><th>状态</th><th>最后心跳</th></tr></thead><tbody id="robot-rows"></tbody></table></div></section>

  <section id="p-alarms" hidden><div class="card"><table><thead><tr><th>时间</th><th>设备</th><th>代码</th><th>等级</th><th>描述</th><th>确认</th></tr></thead><tbody id="alarm-rows"></tbody></table></div></section>

  <section id="p-calls" hidden><div class="card">
    <div class="row"><button class="ghost" onclick="loadCalls()">刷新</button><span class="hint">上行=下层调我们，下行=我们调下层</span></div>
    <table style="margin-top:8px"><thead><tr><th>时间</th><th>方向</th><th>方法</th><th>路径</th><th>状态</th><th>摘要</th></tr></thead><tbody id="call-rows"></tbody></table>
  </div></section>

  <section id="p-cfg" hidden><div class="card">
    <div class="row">
      <label>下层地址（如 http://192.168.124.16:8020）<input id="s-base" style="width:320px"></label>
      <label>下发路径<input id="s-path" value="/rcs/task/dispatch" style="width:220px"></label>
      <label>下层 Key（可空）<input id="s-dkey" style="width:200px"></label>
    </div>
    <div class="row" style="margin-top:10px">
      <label>超时(秒)<input id="s-timeout" value="8" style="width:90px"></label>
      <label>重试次数<input id="s-retry" value="3" style="width:90px"></label>
      <label>本服务校验 Key（可空，填了就要求 X-Api-Key）<input id="s-key" style="width:260px"></label>
      <button class="primary" onclick="saveCfg()">保存</button>
    </div>
    <p class="hint">改完立即生效，不用重启。</p>
  </div></section>
</div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const pill=s=>{const c=(s==='completed'?'done':s==='failed'?'bad':['created','cancelled'].includes(s)?'':'run');
  return `<span class="pill ${c}">${esc(s)}</span>`};
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('on',x===b));
  for(const t of ['tasks','robots','alarms','calls','cfg']) $('#p-'+t).hidden = t!==b.dataset.t;
  ({tasks:loadTasks,robots:loadRobots,alarms:loadAlarms,calls:loadCalls,cfg:loadCfg})[b.dataset.t]();
});
async function j(url,opt){const r=await fetch(url,opt);return await r.json();}
async function loadStats(){const d=await j('/api/v1/stats');const s=d.stats;
  $('#k-tasks').textContent=s.tasks;$('#k-run').textContent=s.running;$('#k-done').textContent=s.completed;
  $('#k-fail').textContent=s.failed;$('#k-robots').textContent=s.robots;$('#k-alarms').textContent=s.alarms;}
async function loadTasks(){const d=await j('/api/v1/tasks?limit=100');
  $('#task-rows').innerHTML=(d.tasks||[]).map(t=>`<tr>
    <td class="mono">${esc(t.task_no)}</td><td>${esc(t.from_point)} → ${esc(t.to_point)}</td>
    <td>${esc(t.robot_code||'-')}</td><td>${pill(t.status)}</td>
    <td>${Math.round((t.progress||0)*100)}%</td><td>${t.retries||0}</td><td class="mono">${esc(t.updated_at||'')}</td>
    <td><button class="ghost" onclick="cancelTask('${esc(t.task_no)}')">取消</button>
        <button class="ghost" onclick="redispatch('${esc(t.task_no)}')">重下发</button></td></tr>`).join('')
    ||'<tr><td colspan="8" class="hint">还没有任务</td></tr>';
  loadStats();}
async function createTask(){
  const body={from:$('#t-from').value,to:$('#t-to').value,robotCode:$('#t-robot').value,priority:+$('#t-pri').value||5};
  const d=await j('/api/v1/tasks',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  alert(d.dispatch&&d.dispatch.ok?('已下发：'+d.task.task_no):('任务已建但下发失败：'+(d.dispatch?d.dispatch.error:'未知')));
  loadTasks();}
async function cancelTask(n){if(!confirm('取消 '+n+' ?'))return;await j('/api/v1/tasks/'+n+'/cancel',{method:'POST'});loadTasks();}
async function redispatch(n){const d=await j('/api/v1/tasks/'+n+'/redispatch',{method:'POST'});alert(JSON.stringify(d.result));loadTasks();}
async function loadRobots(){const d=await j('/api/v1/robots');
  $('#robot-rows').innerHTML=(d.robots||[]).map(r=>`<tr><td class="mono">${esc(r.code)}</td>
    <td>${r.online?'<span class="pill run">在线</span>':'<span class="pill bad">离线</span>'}</td>
    <td>${r.battery??0}%</td><td class="mono">${r.x??0}, ${r.y??0}</td><td>${esc(r.status)}</td>
    <td class="mono">${esc(r.last_seen||'')}</td></tr>`).join('')||'<tr><td colspan="6" class="hint">还没有心跳</td></tr>';}
async function loadAlarms(){const d=await j('/api/v1/alarms');
  $('#alarm-rows').innerHTML=(d.alarms||[]).map(a=>`<tr><td class="mono">${esc(a.created_at)}</td>
    <td>${esc(a.robot_code)}</td><td class="mono">${esc(a.code)}</td>
    <td><span class="pill ${a.level==='error'?'bad':'warn'}">${esc(a.level)}</span></td>
    <td>${esc(a.message)}</td><td>${a.acked?'已确认':`<button class="ghost" onclick="ackAlarm(${a.id})">确认</button>`}</td></tr>`).join('')
    ||'<tr><td colspan="6" class="hint">暂无报警</td></tr>';}
async function ackAlarm(id){await j('/api/v1/alarms/'+id+'/ack',{method:'POST'});loadAlarms();}
async function loadCalls(){const d=await j('/api/v1/calls?limit=200');
  $('#call-rows').innerHTML=(d.calls||[]).map(c=>`<tr><td class="mono">${esc(c.ts)}</td>
    <td>${c.direction==='up'?'上行':'下行'}</td><td>${esc(c.method)}</td><td class="mono">${esc(c.path)}</td>
    <td>${esc(c.status)}</td><td class="mono">${esc((c.summary||'').slice(0,120))}</td></tr>`).join('')
    ||'<tr><td colspan="6" class="hint">暂无调用</td></tr>';}
async function loadCfg(){const d=await j('/api/v1/settings');const s=d.settings;
  $('#s-base').value=s.downstream_base||'';$('#s-path').value=s.dispatch_path||'/rcs/task/dispatch';
  $('#s-dkey').value=s.downstream_key||'';$('#s-timeout').value=s.timeout||8;$('#s-retry').value=s.retry||3;
  $('#s-key').value=s.api_key||'';$('#d-url').textContent=(s.downstream_base||'未配置')+(s.dispatch_path||'');}
async function saveCfg(){
  const body={downstream_base:$('#s-base').value,dispatch_path:$('#s-path').value,downstream_key:$('#s-dkey').value,
    timeout:+$('#s-timeout').value||8,retry:+$('#s-retry').value||3,api_key:$('#s-key').value};
  await j('/api/v1/settings',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  alert('已保存');loadCfg();}
setInterval(()=>{$('#clock').textContent=new Date().toLocaleString('zh-CN');},1000);
loadTasks();loadCfg();
setInterval(()=>{if(!$('#p-tasks').hidden){loadTasks();}},5000);
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8030)
    args = ap.parse_args()
    print(f"上层调度服务：http://127.0.0.1:{args.port}/  （Swagger: /docs）")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
