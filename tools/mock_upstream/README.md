# 模拟上层服务端（Mock Upstream Server）

给 AGV / RCS / WMS 对接联调用的“假上层”：把上层系统那一侧先造出来，
既能让下层（RCS / 机器人 / 线体）往上打接口，也能从上往下发请求。

单文件、零额外依赖（只要项目虚拟环境里的 fastapi + uvicorn + httpx）。

## 启动

```powershell
# 用项目虚拟环境的 python
cd C:\Users\17924\Documents\默认项目
.\.venv312\Scripts\python.exe tools\mock_upstream\mock_upstream.py --port 8020
```

或双击 `start.bat`（默认 8020）。

打开 <http://127.0.0.1:8020/>。
下层要填的地址是本机局域网 IP + 端口，例如 `http://192.168.124.16:8020`。

## 四个页面

### 1. 上报日志
- 下层打进来的每个请求都会记录：方法、路径、来源 IP、query、请求头、请求体、命中的规则、返回的报文
- 支持关键字过滤、条数切换、自动刷新、导出 JSONL、一键清空

### 2. 接口规则
每条规则 = 一个接口：

| 字段 | 说明 |
| --- | --- |
| 名称 | 方便看日志 |
| 方法 | `*` 表示任意，也可写 GET / POST… |
| 路径 | 如 `/rcs/task/status` |
| 匹配 | `exact` 精确 / `prefix` 前缀 / `regex` 正则 |
| 状态码 | 返回的 HTTP 状态码 |
| 延迟(ms) | 模拟慢接口，便于测超时重试 |
| 返回体 | 任意 JSON，原样返回给下层 |
| 启用 | 临时停用某条规则 |

没命中任何规则时，用「设置」里的默认返回。

### 3. 向下发请求
填方法、目标地址、请求头、请求体，点发送。
发出的请求和回来的响应都会进日志，标记为「上层→下层」。

### 4. 设置
- 监听地址 / 端口：端口改完点「重启服务」生效
- 未命中规则时的默认状态码与默认 JSON

## 数据落在哪

```
tools/mock_upstream/data/
  config.json     监听地址、端口、默认响应
  rules.json      接口规则
  requests.jsonl  请求日志（一行一条，可直接给同事或丢给分析脚本）
```

删掉 `data/requests.jsonl` 就等于清日志（页面上也有清空按钮）。

## 管理接口（脚本化也能用）

| 接口 | 用途 |
| --- | --- |
| `GET  /__admin/config` | 读配置 |
| `POST /__admin/config` | 改配置（host/port/default_status/default_body） |
| `GET  /__admin/rules` | 读规则 |
| `POST /__admin/rules` | 覆盖写规则（`{"rules":[...]}`） |
| `GET  /__admin/logs?limit=200&keyword=xxx` | 查日志 |
| `POST /__admin/logs/clear` | 清日志 |
| `GET  /__admin/logs/export` | 导出 JSONL |
| `POST /__admin/outbound` | 向下层发请求 |
| `POST /__admin/restart` | 重启服务（换端口用） |

## 注意

- 只做联调，不建议暴露到公网；要暴露请自己加鉴权和 HTTPS
- 日志里不保存 `authorization` / `cookie` 头
- 端口被占用时换一个，或先停掉占用进程
