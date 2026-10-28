# 架构设计

## 1. 设计目标

- **能跑**：不装微信、不填 API Key 也能完整演示登录、群聊、Agent 回复与表情包。
- **可换**：微信协议、LLM、存储、触发规则都通过清晰边界隔离。
- **可扩展**：新增适配器或业务规则时，不修改前端与消息主链路。
- **可观测**：登录状态、Agent 决策、错误都通过 REST / WebSocket 暴露。

非目标（当前版本）：

- 多账号同时在线（Runtime 单活跃适配器）
- 分布式部署
- 官方微信 API 封装
- 多租户鉴权

---

## 2. 分层

```text
┌────────────────────────────────────────────────────┐
│ frontend/  index.html + app.js + styles.css + ui.css │
│ 无构建、同源 REST、WebSocket、所有动态内容 textContent │
└──────────────────────────┬─────────────────────────┘
                           │ HTTP / WS
┌──────────────────────────▼─────────────────────────┐
│ app/api/  FastAPI 路由                              │
│ status / auth / groups / stickers / settings / ws  │
└──────────────────────────┬─────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────┐
│ core/RuntimeManager                                 │
│ · 登录状态机  · 适配器生命周期  · 事件队列           │
│ · 群/成员/消息入库  · 广播  · 发送                  │
└───────┬───────────────────────────┬────────────────┘
        │                           │
┌───────▼──────────┐      ┌─────────▼──────────────┐
│ AgentEngine      │      │ adapters/BaseAdapter    │
│ 触发/上下文/LLM/表情│      │ mock / wcf / 自定义     │
└───────┬──────────┘      └─────────┬──────────────┘
        │                           │
┌───────▼──────────┐      ┌─────────▼──────────────┐
│ services/llm.py  │      │ 微信 / 演示事件源        │
│ OpenAI Compatible│      └────────────────────────┘
└──────────────────┘
        │
┌───────▼────────────────────────────────────────────┐
│ SQLite: messages / groups / members / configs /    │
│ stickers / settings / agent_logs / accounts        │
└────────────────────────────────────────────────────┘
```

---

## 3. 登录状态机

```text
offline
  │ login(adapter)
  ▼
starting ──失败──► error
  │                 │ login 重试
  │ QR 生成/截图     ▼
  ▼               starting
qr
  │ 扫码成功 / mock 自动确认
  ▼
online ──消息通道断开──► qr（等待重新登录）
  │
  │ logout
  ▼
offline
```

关键实现：

- `RuntimeManager.login()` 持有异步锁，防止重复登录。
- 每次启动适配器递增 `_generation`。事件携带 generation，旧适配器的延迟事件会被丢弃，避免退出后又被旧事件改回 online。
- 二维码只保存在内存（`qr_bytes`），登录成功后清空。
- 账号信息持久化到 `accounts` 表，退出时全部置为 inactive。

---

## 4. 适配器事件协议

适配器通过 `emit(event_type, **data)` 抛出事件，Runtime 在独立消费者协程中串行处理。
适配器可以在任意线程调用 emit；Runtime 内部通过 `loop.call_soon_threadsafe` 投递到 asyncio 队列。

| 事件 | 数据 | 说明 |
| --- | --- | --- |
| `status` | `state`, `message` | offline / starting / qr / online / error |
| `qr` | `image_b64`, `mime` | 登录二维码 |
| `account` | `wxid`, `name`, `avatar` | 登录账号 |
| `groups` | `groups: list[GroupInfo]` | 群列表 |
| `message` | `message: dict` | 标准消息 |
| `log` | `message`, `level` | 适配器日志 |
| `error` | `message` | 不可恢复/需要提示的错误 |

标准消息字段（`IncomingMessage`）：

```python
room_id: str          # 群 ID，以 @chatroom 结尾
sender_id: str        # 发送者 wxid
sender_name: str      # 已解析的昵称（适配器可留空，Runtime 会用缓存补齐）
content: str          # 文本或占位文本（[图片]/[表情]…）
kind: str             # text/image/voice/video/sticker/app/system/other
timestamp: float      # epoch 秒
external_id: str|None # 协议消息 ID，用于去重
is_self: bool         # 是否当前账号发出
is_group: bool
meta: dict            # 附加信息（如 msg_type/group_name）
```

约束：

- `emit` 不得阻塞；WCF 适配器把阻塞 SDK 放在独立线程。
- 消息可以乱序/重复，Runtime 用 `external_id` 去重，前端按时间排序。
- 适配器只负责协议，不直接写数据库，也不调用 Agent。

---

## 5. 消息入库

`RuntimeManager._store_incoming()` 在一个数据库会话内完成：

1. 按 `external_id`（群 ID）查找/创建群；同时补齐默认 `GroupConfig`（默认关闭 Agent）。
2. 按 `external_id` 去重消息，重复消息直接返回。
3. 按 `(group_id, wxid)` upsert 群成员昵称。
4. 写入 `messages`，更新 `groups.last_message_at`（取最大值，防止历史消息回填把时间改旧）。
5. 广播 `message.new`。
6. 如果消息不是自己发出的，且类型为 text/app，则调度 `AgentEngine.handle_message()`。

消息发送人昵称策略：

1. 适配器给出昵称且不是 wxid → 使用；
2. 数据库已有成员昵称 → 使用；
3. 未知 → 暂时显示 wxid，后续同步成员后补齐。

---

## 6. Agent 决策流水线

```text
新消息
  │
  ├─ 非群消息 / 自己 / Agent 消息 / 非文本 ──► 忽略
  ├─ 群配置未启用 ──► 忽略
  ├─ 触发方式判定（mention / keyword / all）
  ├─ 概率、冷却、每小时上限、静默时段
  │
  ▼
获取群锁（同群串行）
  │
  ├─ 重新读取配置（等待锁期间可能被修改）
  ├─ 构建上下文：最近 N 条消息 + 昵称 + 时间
  ├─ 读取表情库目录（id/名称/标签/描述）
  ├─ 广播 agent.typing
  ├─ 延迟 1–2 秒（更像真人）
  │
  ▼
模型调用
  ├─ 有 API Key → OpenAI Compatible JSON 模式
  └─ 无 API Key → 内置演示回复器
  │
  ▼
解析 JSON
  ├─ action=none 或空内容 → 仅记录日志
  └─ reply / sticker → 调 Runtime 发送
        │
        ├─ 文字消息入库 + 广播
        ├─ 表情消息（图片文件）入库 + 广播
        └─ 增加表情使用次数 / 写 agent_logs
```

并发控制：

- 全局 Semaphore 限制同时处理的群数量。
- 每个群一把 `asyncio.Lock`，避免同群并发生成多条回复。
- `_processed` 集合按消息 ID 去重，避免重复调度。
- 冷却和小时配额在发送成功后才写入。

上下文与提示词：

- 群名、Bot 名、时区、当前时间、成员昵称；
- 触发方式；
- 人设（本群覆盖 > 全局）；
- 最近消息（带发送者与时间）；
- 表情包目录；
- JSON 输出协议与“不要每条都发表情”等约束。

---

## 7. 数据模型

| 表 | 用途 | 关键字段 |
| --- | --- | --- |
| `accounts` | 登录过的账号 | `adapter`, `wxid`, `name`, `active` |
| `groups` | 群聊 | `external_id` 唯一, `name`, `member_count`, `last_message_at` |
| `group_members` | 群成员昵称缓存 | `(group_id, wxid)` 唯一 |
| `messages` | 消息 | `external_id` 唯一（可空）, `kind`, `content`, `is_from_agent`, `created_at` |
| `group_configs` | 每群 Agent 配置 | 触发模式、关键词、概率、冷却、人设、静默时段 |
| `stickers` | 表情库 | `filename`, `tags` JSON, `description`, `usage_count` |
| `settings` | 运行时设置 | `key` 主键, `value` JSON |
| `agent_logs` | Agent 决策记录 | `group_id`, `level`, `event`, `detail` |
| `llm_usage` | LLM 用量与缓存命中率 | `call_site`, `prompt_tokens`, `cache_hit_tokens`, `cache_miss_tokens`, `latency_ms`, `ok` |

时间约定：

- SQLite 的 `DateTime` 不保存时区，本项目统一保存 **UTC naive**；
- 序列化时补上 UTC 标识，前端 `new Date()` 转本地时间；
- Agent 的静默时段按设置中的 IANA 时区（默认 `Asia/Shanghai`）判断。

---

## 8. WebSocket

`EventBus` 维护多个有界队列（每个浏览器连接一个）。发布事件时：

- 队列满则丢弃最旧的一条，保证慢客户端不会阻塞主链路；
- WebSocket 每 20 秒发送一次 ping；
- 前端断线后指数退避重连（1.5s → 10s），重连后重新拉取状态与群列表。

事件类型见 `docs/API.md`。

---

## 9. 可拓展点

| 需求 | 修改位置 |
| --- | --- |
| 新微信协议 | `app/adapters/base.py` + 新适配器 + `adapters/__init__.py` 注册 |
| 新触发模式 | `AgentEngine._triggered()` + `schemas.GroupConfigUpdate` + 前端下拉框 |
| 工具调用 / 工作流 | `AgentEngine._generate_llm()` 附近，可复用 Runtime 发送接口 |
| 新消息类型 | 适配器 `kind` + 前端 `buildMessageNode()` 渲染 |
| 新表情来源 | `services/stickers.py` + 上传接口 |
| 多账号 | 将单例 Runtime 改为 `dict[account_id, Runtime]`，API 增加账号维度 |
| 向量记忆 | 在 Agent 构建上下文时检索并注入 |
| 定时任务 | 增加独立调度器，通过 Runtime 发送消息 |
| 新工作 Agent | `services/agent_engines.py` + 新适配器；参考 `dsh_agent.py` 的 headless 进程/会话/事件映射 |

---

## 10. 故障与一致性

- 适配器线程异常：`_worker` 捕获并重试；WCF 消息循环断开后自动回到 qr/重连。
- 模型失败：写 `agent_logs`，广播错误，不在群里发送错误提示。
- 发送失败：异常回到 Agent 顶层，记录错误；文字发送成功、表情失败时文字已入库。
- 数据库：SQLite WAL + busy_timeout；单进程内使用 async session。
- 日志：应用日志写入 `data/server.log`；后台脚本的进程输出在 `data/server.out.log` / `data/server.err.log`；前台运行时同时输出到控制台。

---

## 11. 安全边界

- 默认只监听 `127.0.0.1`。
- 无鉴权；如果需要远程访问，请用带认证的反向代理。
- API Key 明文保存在 SQLite；本机文件权限即安全边界。
- 上传只接受栅格图片，大小 5MB，文件名 UUID。
- 前端不使用 `innerHTML` 渲染用户数据；图标是固定常量。
- 个人微信自动化存在账号风险，默认回复频率应保守。
