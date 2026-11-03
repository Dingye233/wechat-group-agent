# 上层调度服务

按正常上层系统的业务逻辑实现的调度服务：任务创建 → 下发 → 下层受理 → 执行 → 完成，全程有状态机、重试、幂等、鉴权、日志和报警。

单文件实现（FastAPI + SQLite），依赖只有项目虚拟环境里已有的 fastapi / uvicorn / httpx。

## 启动

```powershell
cd C:\Users\17924\Documents\默认项目
.\.venv312\Scripts\python.exe tools\upstream_server\app.py --port 8030
```

或双击 `start.bat`（默认 8030）。

- 控制台：<http://127.0.0.1:8030/>
- 接口文档：<http://127.0.0.1:8030/docs>

## 业务流程

```
上层创建任务 ──► 下发(HTTP POST) ──► 下层(RCS/机器人)
      ▲                                   │
      └──── 回调上报状态（状态机校验）◄────┘
```

任务状态：

```
created ─► dispatched ─► accepted ─► moving ─► arrived ─► completed
    │            │            │          │          │
    └────────────┴────────────┴──────────┴──────────┴──► cancelled / failed
```

- 非法跳转（比如 completed → moving）直接返回 409，并记录到调用日志
- 同一 `taskNo` 重复创建不会生成第二条（幂等）
- 下发失败按指数退避重试（默认 3 次），最终失败写进 `last_error`，可在页面点「重下发」

## 接口

### 下层 → 上层（回调）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/callback/task` | 任务状态上报 `{taskNo, status, progress, message}` |
| POST | `/api/v1/callback/heartbeat` | 心跳 `{robotCode, battery, x, y, status}`，超过 90 秒无心跳判离线 |
| POST | `/api/v1/callback/alarm` | 报警 `{robotCode, code, level, message}` |
| GET | `/api/v1/ping` | 探活 |

### 上层 → 下层（下发）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `{downstream_base}{dispatch_path}` | 下发给下层，报文含 taskNo/from/to/robotCode/priority/callback |

### 上层管理接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/tasks` | 创建任务并立即下发 |
| GET | `/api/v1/tasks?status=&limit=` | 任务列表 |
| GET | `/api/v1/tasks/{taskNo}` | 任务详情 |
| POST | `/api/v1/tasks/{taskNo}/cancel` | 取消任务 |
| POST | `/api/v1/tasks/{taskNo}/redispatch` | 强制重下发 |
| GET | `/api/v1/robots` | 设备列表（含在线判定） |
| GET | `/api/v1/alarms` | 报警列表 |
| POST | `/api/v1/alarms/{id}/ack` | 确认报警 |
| GET | `/api/v1/calls?direction=up\|down` | 调用日志 |
| GET/POST | `/api/v1/settings` | 读/写下层地址、超时、重试、鉴权 Key |
| GET | `/api/v1/stats` | 看板统计 |

## 鉴权

设置页可填「本服务校验 Key」。填了之后，`/api/v1/callback/*` 与任务相关接口都要求请求头：

```
X-Api-Key: <你设的值>
```

不填则不校验（内网联调默认不填）。

## 数据

```
tools/upstream_server/data/upstream.db   SQLite：tasks / robots / alarms / calls / settings
```

删库即清空；`calls` 表就是完整调用日志。

## 联调示例

```bash
# 1. 配下层地址（指向 RCS 或模拟下层）
curl -X POST http://127.0.0.1:8030/api/v1/settings -H "content-type: application/json" \
  -d '{"downstream_base":"http://192.168.124.16:8020","dispatch_path":"/rcs/task/dispatch"}'

# 2. 建任务并下发
curl -X POST http://127.0.0.1:8030/api/v1/tasks -H "content-type: application/json" \
  -d '{"from":"BUFFER-01","to":"LINE-02","robotCode":"AGV-01","priority":5}'

# 3. 下层回调推进
curl -X POST http://127.0.0.1:8030/api/v1/callback/task -H "content-type: application/json" \
  -d '{"taskNo":"T1234567","status":"accepted"}'
```

配套的「模拟下层」在 `tools/mock_upstream/`，两个可以互相打通做闭环测试。
