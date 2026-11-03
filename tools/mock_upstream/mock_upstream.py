# -*- coding: utf-8 -*-
"""模拟上层服务端（Mock Upstream Server）

给 AGV / RCS 对接联调用：把「上层系统」那一侧先造出来，方便下层往上打接口、
也能从上往下发请求。单文件、零额外依赖（FastAPI + uvicorn，项目虚拟环境里已有）。

功能
1. 可自定义监听端口（改完在页面点“重启服务”即可）
2. 可自定义接口路径（支持精确路径和通配符），每个路径可配不同返回 JSON / 状态码 / 延迟
3. 收到的每个请求都写日志（方法、路径、来源 IP、请求头、请求体、命中规则、返回内容）
4. 日志页面可按关键字过滤、展开看原始报文、导出 JSONL
5. “下发”页面可自定义目标地址、方法、请求头、JSON 报文，向下层发请求并记录响应

启动：python mock_upstream.py --port 8020 [--host 0.0.0.0]
页面：http://127.0.0.1:8020/
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RULES_PATH = DATA_DIR / "rules.json"
LOG_PATH = DATA_DIR / "requests.jsonl"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULT_CONFIG = {"host": "0.0.0.0", "port": 8020, "default_status": 200,
                  "default_body": {"code": 0, "message": "ok", "data": None}}

DEFAULT_RULES = [
    {
        "id": "demo-task-callback",
        "name": "任务状态回调（示例）",
        "method": "*",
        "path": "/rcs/task/status",
        "match": "exact",
        "status": 200,
        "delay_ms": 0,
        "headers": {"content-type": "application/json"},
        "body": {"code": 0, "message": "received", "data": {"taskId": "T0001", "accept": True}},
        "enabled": True,
    }
]

_lock = threading.Lock()


# ------------------------------------------------------------------ 配置
def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_config() -> dict[str, Any]:
    conf = dict(DEFAULT_CONFIG)
    conf.update(load_json(CONFIG_PATH, {}) or {})
    return conf


def get_rules() -> list[dict[str, Any]]:
    if not RULES_PATH.exists():
        save_json(RULES_PATH, DEFAULT_RULES)
        return list(DEFAULT_RULES)
    rules = load_json(RULES_PATH, [])
    return rules if isinstance(rules, list) else []


def append_log(entry: dict[str, Any]) -> None:
    with _lock:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_logs(limit: int = 200, keyword: str = "") -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    if keyword:
        kw = keyword.lower()
        lines = [l for l in lines if kw in l.lower()]
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(out))


# ------------------------------------------------------------------ 规则匹配
def match_rule(rules: list[dict[str, Any]], method: str, path: str) -> dict[str, Any] | None:
    method = (method or "").upper()
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if rule.get("method") not in ("*", None, "") and str(rule["method"]).upper() != method:
            continue
        pattern = str(rule.get("path") or "")
        if rule.get("match") == "regex":
            try:
                if not re.search(pattern, path):
                    continue
            except re.error:
                continue
        elif rule.get("match") == "prefix":
            if not path.startswith(pattern):
                continue
        else:
            if path != pattern:
                continue
        return rule
    return None


# ------------------------------------------------------------------ 应用
def create_app() -> FastAPI:
    app = FastAPI(title="Mock Upstream Server", docs_url="/docs", redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    # ---------------- 管理接口
    @app.get("/__admin/config")
    async def admin_config() -> dict:
        return {"ok": True, "config": get_config(), "pid": os.getpid(),
                "hostname": socket.gethostname()}

    @app.post("/__admin/config")
    async def admin_config_save(payload: dict) -> dict:
        conf = get_config()
        for key in ("host", "port"):
            if payload.get(key):
                conf[key] = payload[key]
        for key in ("default_status",):
            if payload.get(key) is not None:
                conf[key] = int(payload[key])
        if isinstance(payload.get("default_body"), (dict, list)):
            conf["default_body"] = payload["default_body"]
        save_json(CONFIG_PATH, conf)
        return {"ok": True, "config": conf, "note": "端口改动需要点“重启服务”才生效"}

    @app.get("/__admin/rules")
    async def admin_rules() -> dict:
        return {"ok": True, "rules": get_rules()}

    @app.post("/__admin/rules")
    async def admin_rules_save(payload: dict) -> dict:
        rules = payload.get("rules")
        if not isinstance(rules, list):
            return {"ok": False, "error": "rules 必须是数组"}
        for rule in rules:
            rule.setdefault("id", uuid.uuid4().hex[:8])
            rule.setdefault("method", "*")
            rule.setdefault("match", "exact")
            rule.setdefault("status", 200)
            rule.setdefault("enabled", True)
        save_json(RULES_PATH, rules)
        return {"ok": True, "count": len(rules)}

    @app.get("/__admin/logs")
    async def admin_logs(limit: int = 200, keyword: str = "") -> dict:
        return {"ok": True, "logs": read_logs(max(1, min(1000, limit)), keyword)}

    @app.post("/__admin/logs/clear")
    async def admin_logs_clear() -> dict:
        if LOG_PATH.exists():
            LOG_PATH.unlink()
        return {"ok": True}

    @app.get("/__admin/logs/export")
    async def admin_logs_export() -> Response:
        text = LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else ""
        return Response(content=text, media_type="application/x-ndjson",
                        headers={"content-display": "attachment; filename=requests.jsonl"})

    # ---------------- 向下层发请求
    @app.post("/__admin/outbound")
    async def admin_outbound(payload: dict) -> dict:
        method = str(payload.get("method") or "POST").upper()
        url = str(payload.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "url 必填"}
        headers = payload.get("headers") or {}
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except Exception:
                headers = {}
        body = payload.get("body")
        timeout = float(payload.get("timeout") or 15)
        started = time.time()
        result: dict[str, Any] = {"ok": False}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if isinstance(body, (dict, list)):
                    resp = await client.request(method, url, json=body, headers=headers)
                elif isinstance(body, str) and body.strip():
                    resp = await client.request(method, url, content=body.encode(), headers=headers)
                else:
                    resp = await client.request(method, url, headers=headers)
            text = resp.text
            try:
                parsed = json.loads(text) if text else None
            except Exception:
                parsed = None
            result = {"ok": True, "status": resp.status_code, "body": parsed if parsed is not None else text[:4000],
                      "elapsed_ms": round((time.time() - started) * 1000)}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                      "elapsed_ms": round((time.time() - started) * 1000)}
        entry = {
            "id": uuid.uuid4().hex[:10],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "outbound",
            "method": method,
            "path": url,
            "client": "本机（模拟上层→下层）",
            "status": result.get("status"),
            "matched": None,
            "request_body": body,
            "response_body": result.get("body") or result.get("error"),
        }
        append_log(entry)
        return result

    # ---------------- 重启服务（换端口）
    @app.post("/__admin/restart")
    async def admin_restart() -> dict:
        def _later() -> None:
            time.sleep(0.8)
            os.execv(sys.executable, [sys.executable, *sys.argv])
        threading.Thread(target=_later, daemon=True).start()
        return {"ok": True, "note": "正在重启，稍等 2 秒刷新页面"}

    # ---------------- 兜底：接收下层上报
    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def catch_all(full_path: str, request: Request) -> Response:
        raw = await request.body()
        try:
            body_text = raw.decode("utf-8", "replace")
        except Exception:
            body_text = ""
        parsed: Any = None
        try:
            parsed = json.loads(body_text) if body_text else None
        except Exception:
            parsed = None
        path = "/" + full_path
        conf = get_config()
        rule = match_rule(get_rules(), request.method, path)
        if rule:
            delay = float(rule.get("delay_ms") or 0) / 1000
            if delay > 0:
                import asyncio
                await asyncio.sleep(delay)
            status = int(rule.get("status") or 200)
            payload = rule.get("body")
            headers = rule.get("headers") if isinstance(rule.get("headers"), dict) else {"content-type": "application/json"}
            matched = rule.get("name") or rule.get("id")
        else:
            status = int(conf.get("default_status") or 200)
            payload = conf.get("default_body")
            headers = {"content-type": "application/json"}
            matched = "默认响应（未命中规则）"
        append_log({
            "id": uuid.uuid4().hex[:10],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "inbound",
            "method": request.method,
            "path": path,
            "client": request.client.host if request.client else "",
            "status": status,
            "matched": matched,
            "query": dict(request.query_params),
            "request_headers": {k: v for k, v in request.headers.items() if k.lower() not in ("authorization", "cookie")},
            "request_body": parsed if parsed is not None else body_text[:4000],
            "response_body": payload,
        })
        if isinstance(payload, (dict, list)):
            return JSONResponse(content=payload, status_code=status, headers=headers)
        return Response(content=str(payload or ""), status_code=status, media_type="text/plain")

    return app


INDEX_HTML = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>模拟上层服务端 · Mock Upstream</title>
<style>
:root{--bg:#0b1113;--panel:#121a1d;--line:#1f2b30;--txt:#e6f1f2;--dim:#8fa8ad;--acc:#37c98b;--warn:#e2a33c;--err:#e2654f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.55 -apple-system,"Microsoft YaHei",sans-serif}
header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{font-size:16px;margin:0;letter-spacing:.06em}
.tag{font-size:12px;color:var(--dim)}
.wrap{padding:16px;max-width:1280px;margin:0 auto}
.tabs{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.tabs button{background:var(--panel);color:var(--txt);border:1px solid var(--line);padding:7px 14px;border-radius:8px;cursor:pointer}
.tabs button.on{background:#173b2d;border-color:var(--acc);color:#c9ffe6}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
label{font-size:12px;color:var(--dim);display:flex;flex-direction:column;gap:4px}
input,select,textarea{background:#0b1416;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font:13px/1.4 ui-monospace,Consolas,monospace}
textarea{min-height:120px;width:100%;resize:vertical}
button.primary{background:#1c5c44;border:1px solid var(--acc);color:#d8ffee;border-radius:8px;padding:8px 16px;cursor:pointer}
button.ghost{background:#16232a;border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:7px 12px;cursor:pointer}
button.danger{background:#3a1d18;border:1px solid var(--err);color:#ffd9d2;border-radius:8px;padding:7px 12px;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:500}
code{color:#8fe6c0}
.log{border:1px solid var(--line);border-radius:8px;margin-bottom:8px;background:#0d1518}
.log summary{padding:8px 10px;cursor:pointer;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.log pre{margin:0;padding:10px;border-top:1px solid var(--line);overflow:auto;max-height:320px;font-size:12px}
.mono{font-family:ui-monospace,Consolas,monospace}
.pill{font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);color:var(--dim)}
.pill.ok{color:#9df3c9;border-color:#2c6b52}.pill.warn{color:#ffd79a;border-color:#6b5228}.pill.err{color:#ffb9ae;border-color:#6b2f28}
.hint{color:var(--dim);font-size:12px}
</style></head><body>
<header>
  <h1>模拟上层服务端</h1>
  <span class="tag" id="svc">读取中…</span>
  <span style="flex:1"></span>
  <span class="tag">本机 IP 给下层填：<code id="lanip">—</code></span>
</header>
<div class="wrap">
  <div class="tabs">
    <button data-tab="logs" class="on">上报日志</button>
    <button data-tab="rules">接口规则</button>
    <button data-tab="out">向下发请求</button>
    <button data-tab="cfg">设置</button>
  </div>

  <section id="tab-logs">
    <div class="card row">
      <label>关键字<input id="kw" placeholder="路径 / 报文内容" style="width:220px"></label>
      <label>条数<select id="lim"><option>50</option><option selected>200</option><option>500</option></select></label>
      <button class="ghost" onclick="loadLogs()">刷新</button>
      <button class="ghost" onclick="auto=!auto;this.textContent=auto?'自动刷新：开':'自动刷新：关'">自动刷新：关</button>
      <button class="ghost" onclick="location.href='/__admin/logs/export'">导出 JSONL</button>
      <button class="danger" onclick="clearLogs()">清空</button>
      <span class="hint" id="logCount"></span>
    </div>
    <div id="logs"></div>
  </section>

  <section id="tab-rules" hidden>
    <div class="card">
      <div class="row" style="margin-bottom:10px">
        <button class="primary" onclick="addRule()">新增规则</button>
        <button class="ghost" onclick="saveRules()">保存全部</button>
        <span class="hint">匹配方式：exact 精确 / prefix 前缀 / regex 正则；method 用 * 表示任意</span>
      </div>
      <div id="rules"></div>
    </div>
  </section>

  <section id="tab-out" hidden>
    <div class="card">
      <div class="row">
        <label>方法<select id="o-method"><option>POST</option><option>GET</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select></label>
        <label style="flex:1;min-width:320px">目标地址<input id="o-url" placeholder="http://192.168.1.10:8080/rcs/task/dispatch" style="width:100%"></label>
      </div>
      <label style="margin-top:10px">请求头（JSON）<textarea id="o-headers" style="min-height:70px">{"content-type":"application/json"}</textarea></label>
      <label>请求体（JSON）<textarea id="o-body">{"taskId":"T0001","robotCode":"AGV-01","from":"BUFFER-01","to":"LINE-02"}</textarea></label>
      <div class="row" style="margin-top:10px"><button class="primary" onclick="sendOut()">发送</button><span class="hint">发出去的请求和回来的报文也会进日志</span></div>
    </div>
    <div class="card"><div class="hint">响应</div><pre class="mono" id="o-result" style="max-height:360px;overflow:auto">—</pre></div>
  </section>

  <section id="tab-cfg" hidden>
    <div class="card">
      <div class="row">
        <label>监听地址<input id="c-host" style="width:150px"></label>
        <label>端口<input id="c-port" style="width:110px"></label>
        <label>默认状态码<input id="c-status" style="width:100px"></label>
      </div>
      <label style="margin-top:10px">未命中规则时的默认返回（JSON）<textarea id="c-body" style="min-height:110px"></textarea></label>
      <div class="row" style="margin-top:10px">
        <button class="primary" onclick="saveCfg()">保存</button>
        <button class="ghost" onclick="restart()">重启服务（换端口生效）</button>
      </div>
      <p class="hint">重启会断开当前页面 2 秒左右；同一条命令不会丢配置，规则和日志都落在 data/ 目录。</p>
    </div>
  </section>
</div>
<script>
let auto=false, timer=null;
const $=s=>document.querySelector(s);
const ip=async()=>{try{const r=await fetch('/__admin/config');const d=await r.json();
  $('#svc').textContent=`运行中 · ${d.config.host}:${d.config.port} · pid ${d.pid}`;
  $('#lanip').textContent=location.hostname; return d;}catch(e){$('#svc').textContent='服务异常';}};
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('on',x===b));
  for(const t of ['logs','rules','out','cfg']) $('#tab-'+t).hidden = t!==b.dataset.tab;
  if(b.dataset.tab==='logs') loadLogs();
  if(b.dataset.tab==='rules') loadRules();
  if(b.dataset.tab==='cfg') loadCfg();
});
async function loadLogs(){
  const r=await fetch('/__admin/logs?limit='+$('#lim').value+'&keyword='+encodeURIComponent($('#kw').value));
  const d=await r.json(); const logs=d.logs||[];
  $('#logCount').textContent=`共 ${logs.length} 条`;
  $('#logs').innerHTML = logs.map(l=>{
    const cls = l.status>=500?'err':(l.status>=400?'warn':'ok');
    const dir = l.kind==='outbound'?'上层→下层':'下层→上层';
    return `<details class="log"><summary><span class="pill ${cls}">${l.status??'-'}</span>
      <b>${l.method}</b> <span class="mono">${esc(l.path)}</span>
      <span class="pill">${dir}</span><span class="pill">${esc(l.matched||'')}</span>
      <span class="tag">${l.client||''}</span><span style="flex:1"></span><span class="tag">${l.time}</span></summary>
      <pre>${esc(JSON.stringify({query:l.query,request:l.request_body,response:l.response_body,headers:l.request_headers},null,2))}</pre></details>`;
  }).join('')||'<p class="hint">还没有记录，让下层往上打一个请求试试。</p>';
}
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
async function clearLogs(){if(!confirm('清空日志？'))return;await fetch('/__admin/logs/clear',{method:'POST'});loadLogs();}
let RULES=[];
async function loadRules(){const d=await (await fetch('/__admin/rules')).json();RULES=d.rules||[];renderRules();}
function renderRules(){
  $('#rules').innerHTML = RULES.map((r,i)=>`<div class="card" style="background:#0f191c">
    <div class="row">
      <label>名称<input data-i="${i}" data-k="name" value="${esc(r.name||'')}" style="width:180px"></label>
      <label>方法<input data-i="${i}" data-k="method" value="${esc(r.method||'*')}" style="width:80px"></label>
      <label>路径<input data-i="${i}" data-k="path" value="${esc(r.path||'')}" style="width:260px"></label>
      <label>匹配<select data-i="${i}" data-k="match">${['exact','prefix','regex'].map(m=>`<option ${r.match===m?'selected':''}>${m}</option>`).join('')}</select></label>
      <label>状态码<input data-i="${i}" data-k="status" value="${r.status||200}" style="width:80px"></label>
      <label>延迟(ms)<input data-i="${i}" data-k="delay_ms" value="${r.delay_ms||0}" style="width:90px"></label>
      <label style="flex-direction:row;align-items:center;gap:6px">启用<input type="checkbox" data-i="${i}" data-k="enabled" ${r.enabled!==false?'checked':''}></label>
      <button class="danger" onclick="delRule(${i})">删除</button>
    </div>
    <label style="margin-top:8px">返回体（JSON）<textarea data-i="${i}" data-k="body">${esc(JSON.stringify(r.body??{code:0,message:'ok'},null,2))}</textarea></label>
  </div>`).join('');
  document.querySelectorAll('#rules [data-k]').forEach(el=>{
    el.onchange=()=>{const i=+el.dataset.i,k=el.dataset.k;let v=el.value;
      if(k==='enabled')v=el.checked;
      if(k==='status'||k==='delay_ms')v=parseInt(v||0,10);
      if(k==='body'){try{v=JSON.parse(v)}catch(e){alert('返回体不是合法 JSON');return}}
      RULES[i][k]=v;};
  });
}
function addRule(){RULES.unshift({id:Math.random().toString(16).slice(2,10),name:'新规则',method:'*',path:'/new/path',match:'exact',status:200,delay_ms:0,body:{code:0,message:'ok'},enabled:true});renderRules();}
function delRule(i){RULES.splice(i,1);renderRules();}
async function saveRules(){const r=await fetch('/__admin/rules',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({rules:RULES})});const d=await r.json();alert(d.ok?('已保存 '+d.count+' 条规则'):('失败：'+d.error));}
async function sendOut(){
  const payload={method:$('#o-method').value,url:$('#o-url').value,headers:$('#o-headers').value,body:$('#o-body').value};
  $('#o-result').textContent='发送中…';
  const r=await fetch('/__admin/outbound',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)});
  $('#o-result').textContent=JSON.stringify(await r.json(),null,2);
}
async function loadCfg(){const d=await (await fetch('/__admin/config')).json();const c=d.config;
  $('#c-host').value=c.host;$('#c-port').value=c.port;$('#c-status').value=c.default_status;
  $('#c-body').value=JSON.stringify(c.default_body,null,2);}
async function saveCfg(){
  let body;try{body=$('#c-body').value?JSON.parse($('#c-body').value):null}catch(e){alert('默认返回不是合法 JSON');return}
  const r=await fetch('/__admin/config',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({host:$('#c-host').value,port:$('#c-port').value,default_status:$('#c-status').value,default_body:body})});
  const d=await r.json();alert(d.ok?'已保存（端口改动请点重启服务）':('失败：'+d.error));ip();}
async function restart(){if(!confirm('重启服务？页面会短暂断连'))return;await fetch('/__admin/restart',{method:'POST'});setTimeout(()=>location.reload(),2500);}
setInterval(()=>{if(auto && !$('#tab-logs').hidden) loadLogs();},3000);
ip();loadLogs();
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    conf = get_config()
    host = args.host or conf.get("host") or "0.0.0.0"
    port = args.port or int(conf.get("port") or 8020)
    print(f"模拟上层服务端启动：http://{host}:{port}/  （Ctrl+C 停止）")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
