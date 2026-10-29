# 当前架构与 DSH 的职责边界

> 这份文档描述的是当前实际运行的 DSH 融合架构，不是早期 OpenCode/MCP 版本的架构。
> 现有旧文 [ARCHITECTURE.md](ARCHITECTURE.md) 里仍有 Runtime、登录状态机和适配器协议的通用说明，但 Agent 部分以本文为准。

## 1. 一句话理解

这个项目不是“DSH 接管微信”，而是三层：

```text
wechatauto 负责微信读写
Qunban 负责通道、规则、状态、业务工具和发送
DSH 负责 Agent 循环、模型调用、工具选择和会话
```

DSH 是**大脑的一部分**，不是微信接入层，也不是发送层。它看不见微信窗口，也不能直接读微信数据库。

---

## 2. 三个系统的职责

| 能力 | wechatauto / Replica | Qunban | DSH |
| --- | --- | --- | --- |
| 读微信数据库 | ✅ | 调用适配器 | ❌ |
| 解密 / 增量监听 | ✅ | 不关心 | ❌ |
| 打开微信会话、点输入框、发送 | ✅ | 通过 Runtime 调用 | ❌ |
| 消息入库、去重、群配置 | ❌ | ✅ | ❌ |
| 读取/回复/@/概率/冷却/静默规则 | ❌ | ✅ | ❌ |
| 上下文、记忆、表情、人设 | ❌ | ✅ 提供 | 消费 |
| Agent 循环、模型调用 | ❌ | 不直接负责 | ✅ |
| 工具调用决策 | ❌ | 提供工具定义 | ✅ |
| 工具真正执行 | ❌ | ✅ HTTP 网关/业务实现 | 只发起调用 |
| 最终发送校验、outbox、重试 | ❌ | ✅ | ❌ |
| 插件运行时 | ❌ | 管理 profile/策略 | ✅ Cordis 插件 |
| 前端控制台 | ❌ | ✅ | ❌ |

---

## 3. 总体分层

```text
┌──────────────────────────────────────────────────────────┐
│ 浏览器控制台 frontend/                                    │
│ 会话 / 助手 / 插件 / 表情包 / 设置 / 工具任务             │
└──────────────────────────┬───────────────────────────────┘
                           │ REST + WebSocket
┌──────────────────────────▼───────────────────────────────┐
│ FastAPI app/api/                                          │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│ RuntimeManager app/core/runtime.py                        │
│ · 适配器生命周期      · 消息入库/去重                     │
│ · 群配置/读取调度     · 发送/outbox/失败重试              │
│ · WebSocket 广播      · 管理员私聊入口                    │
└───────┬───────────────────────────────┬──────────────────┘
        │                               │
┌───────▼──────────────────┐    ┌───────▼──────────────────┐
│ AgentEngine               │    │ ReplicaAdapter            │
│ app/core/agent.py         │    │ app/adapters/replica.py   │
│ · 规则门                  │    │ · FastListener 读 message.db│
│ · 群聊/私聊 Agent 编排    │    │ · wechatauto UIA 发送      │
│ · DSH/legacy 切换         │    │ · 数据库确认               │
└───────┬──────────────────┘    └───────┬──────────────────┘
        │                               │
┌───────▼──────────────────┐    ┌───────▼──────────────────┐
│ DSH 集成层                │    │ 微信 4.x 客户端/数据库    │
│ dsh_primary / dsh_agent   │    └──────────────────────────┘
│ AssistantService          │
└───────┬──────────────────┘
        │ ACP / headless JSON
┌───────▼──────────────────────────────────────────────────┐
│ DSH 运行时                                                │
│ · Agent loop / 模型调用 / 工具调用                         │
│ · session / prompt cache / compaction                     │
│ · Cordis 插件系统                                          │
│ · @qunban/dsh-native → 调用 Qunban HTTP 工具网关           │
└───────┬──────────────────────────────────────────────────┘
        │ HTTP /api/opencode/tool
┌───────▼──────────────────────────────────────────────────┐
│ Qunban 工具与能力层                                       │
│ 微信 / B站 / 记忆 / 知识库 / 天气接口 / 技能 / MCP        │
│ app/tools/ app/services/capabilities.py                   │
└──────────────────────────────────────────────────────────┘
```

---

## 4. DSH 具体做了什么

### 4.1 Agent 循环

DSH 负责：

```text
系统提示 + 工具 schema + 对话历史
  → 模型
  → 模型决定调用某个工具
  → DSH 执行工具
  → 工具结果回到模型
  → 模型继续或给出最终回答
```

这一层是 Qunban 自己不完整具备的：Qunban 有业务工具，但没有完整的 DSH Agent loop 和 Cordis 插件运行时。

### 4.2 会话与缓存

DSH 维护会话：

- 群聊主链路：一个群一个持久 ACP session
- 私聊/助手：按 session_id 的 headless 会话
- 同一 session 的 system prompt、工具清单和历史形成稳定前缀，提升缓存命中率

### 4.3 工具选择

DSH 看得到 `@qunban/dsh-native` 注册的工具：

```text
wechat_status / wechat_read_group / ...
qunban_memory_search / qunban_memory_save
qunban_bili_search / qunban_bili_knowledge
qunban_interface_<name>
qunban_skill_<name>
qunban_mcp_<name>
```

但工具执行不在 DSH 内部：

```text
DSH tool call
  → @qunban/dsh-native
  → POST /api/opencode/tool
  → Qunban 权限/目标解析/业务实现
  → 结果返回 DSH
```

### 4.4 群聊结构化决策

群聊 primary profile 不是让 DSH 直接发微信，而是要求 DSH 返回：

```json
{"action":"none"}
{"action":"reply","messages":[{"text":"..."}],"sticker":null}
{"action":"task","ack":"...","messages":[...]}
```

Qunban 解析后再通过 `RuntimeManager.send_agent_message()` 发送。这样发送始终经过群伴的微信安全边界。

### 4.5 插件运行时

DSH 负责加载 Cordis 插件：

- 内置插件
- 导入的 npm/git/本地插件
- DSH 创造模式生成的插件
- 群伴原生插件 `@qunban/dsh-native`

Qunban 负责插件目录、导入任务、通道策略、profile 生成和重启。

---

## 5. 当前有几条真实运行链路

### 5.1 群聊主链路：`dsh_primary`

```text
微信 DB
  → ReplicaAdapter FastListener
  → RuntimeManager 入库
  → AgentEngine 规则门
  → DSHPrimaryService.prompt()
  → 常驻 DSH ACP 进程（qunban-primary）
  → DSH Agent loop
  → 结构化 none/reply/task
  → RuntimeManager.send_agent_message()
  → wechatauto 打开会话/发送
  → message.db 确认
```

特点：

- DSH 自己决定是否参与
- DSH 自己决定是否调用工具
- Qunban 只做规则门、上下文、发送和状态
- 一个群一个持久 session
- `wechat_send_message` 在 primary 工具集中被移除

### 5.2 私聊/助手链路：`AssistantService` + `dsh_agent`

```text
管理员私聊 / 助手对话
  → AssistantService.chat()
  → DSHAgentRuntime.chat()
  → 每个请求启动/使用 headless DSH 进程
  → DSH 工具调用
  → 工具任务记录到 assistant_tool_tasks
```

特点：

- 也是 DSH 做 Agent loop
- 但**不是常驻 ACP**，仍是 per-message headless
- 工具调用会显示在「助手 → 任务」，按任务类型分组
- 需要工具/操作时用 Operator profile

### 5.3 legacy 链路：`legacy`

```text
AgentEngine 内置意图/任务编排
  → qunban-chat（纯文本）/ qunban-group（群任务）
```

这是 `group_channel_mode=legacy` 时的应急路径，不是当前默认。

---

## 6. 为什么实际体验还是有问题

### 6.1 DSH 只接管了群聊主链路

这是当前最大的不一致：

```text
群聊：DSH 常驻 ACP，自己决策
私聊/助手：DSH headless，每个请求重新开始
legacy：Python 意图编排
```

同一个“Agent”，实际有 3 种行为模式。用户会感觉：

- 群聊和私聊回答风格/速度不一致
- 私聊没有群聊那种持续会话感
- 任务面板/插件策略看起来统一，实际生效链路不同

### 6.2 DSH 不是微信层，微信延迟它解决不了

实际回复时间由这些部分组成：

```text
DB 轮询
+ 规则/队列
+ 上下文/记忆
+ DSH 首轮模型延迟
+ 工具 HTTP 往返
+ 打开微信会话/搜索
+ UIA 输入发送
+ 数据库确认
```

其中 DSH 只占“模型 + 工具循环”这一段。发送搜索/UIA/确认仍然由 wechatauto 和 Qunban 决定，DSH 再强也不能让微信窗口操作变快。

### 6.3 链路层数多

一次群聊工具调用实际经过：

```text
DSH
  → qunban-native
  → /api/opencode/tool
  → Qunban 权限
  → 具体 capability
  → HTTP/业务返回
  → DSH
  → 结构化 JSON
  → Qunban 解析
  → Runtime 发送
  → 微信 UI
```

每层都有超时、异常、状态和重试语义。可观测性不足时，用户只会看到“慢”或“没反应”，很难知道卡在哪一层。

### 6.4 群聊输出协议仍是“模型输出 JSON，再由 Qunban 解析”

这不是 DSH 原生结构化输出，而是：

- system prompt 要求 JSON
- 模型自由生成
- Qunban `parse_json_object()` 容错
- 失败则把文本当 reply

优点是兼容性高；缺点是模型可能输出代码块、解释文字、错字段，解析失败会退化成文本。

### 6.5 插件管理是 Qunban 外层策略，不是 DSH 原生概念

DSH 插件只知道“我被挂载/没被挂载”。当前私聊/群聊/总开关是 Qunban 在 profile 组合层实现的：

- `plugin-policies.json`
- `enabled` / `private` / `group`
- 工具级 `ctx.tools.restrict`
- profile 重建
- 重启主链路才完全生效

所以插件页看起来像 DSH 的能力，实际开关逻辑在 Qunban。链路一复杂，就会出现“我改了但没生效”的体验。

### 6.6 工具任务面板是“旁路记录”

工具任务来自 DSH progress 事件，不是 DSH 原生任务模型。它能展示“调用了什么”，但不能替代 DSH 自己的任务状态，也不能保证和 DSH 内部执行完全一致。

### 6.7 仍有功能绕过 DSH

部分功能仍直接调用模型或业务链路：

- B站自动刷
- 记忆总结
- 部分 legacy 任务
- 群聊规则门

所以“所有智能都归 DSH”目前不是事实。DSH 是主 Agent，但周边仍有独立智能/编排。

---

## 7. 各层代码位置

| 层 | 文件 |
| --- | --- |
| 微信读取/发送 | [`app/adapters/replica.py`](../app/adapters/replica.py)、[`app/adapters/fast_listener.py`](../app/adapters/fast_listener.py) |
| 运行时/消息/发送 | [`app/core/runtime.py`](../app/core/runtime.py) |
| 群聊/私聊编排 | [`app/core/agent.py`](../app/core/agent.py) |
| DSH 常驻群聊 | [`app/services/dsh_primary.py`](../app/services/dsh_primary.py) |
| DSH headless | [`app/services/dsh_agent.py`](../app/services/dsh_agent.py) |
| 私聊/助手 | [`app/services/assistant.py`](../app/services/assistant.py) |
| 群伴工具插件 | [`dsh/qunban-native/lib/index.js`](../dsh/qunban-native/lib/index.js) |
| 工具网关/能力 | [`app/api/opencode.py`](../app/api/opencode.py)、[`app/services/capabilities.py`](../app/services/capabilities.py) |
| 插件管理 | [`app/services/dsh_plugins.py`](../app/services/dsh_plugins.py)、[`app/services/plugin_policy.py`](../app/services/plugin_policy.py) |
| 前端 | [`frontend/app.js`](../frontend/app.js)、[`frontend/index.html`](../frontend/index.html) |

---

## 8. 建议的目标边界

如果目标是“DSH 真正成为 Agent 主架构”，建议收敛为：

```text
Qunban：
  微信读写、消息状态、规则门、上下文/记忆、
  工具实现、权限、发送、outbox、前端

DSH：
  Agent loop、模型、会话、工具选择、
  插件运行时、结构化决策

wechatauto：
  只做微信协议/UI
```

具体动作：

1. 私聊也改成常驻 ACP，不再 per-message headless。
2. 群聊和私聊使用同一套 DSH session 生命周期，只换 profile。
3. 只保留一套 prompt/输出契约，删除重复的 legacy 意图编排。
4. 工具调用统一走 Qunban gateway，但增加每层耗时/错误追踪。
5. 插件策略简化为“挂载/不挂载 + 通道”，工具级 split 只在确有需要时使用。
6. 先做阶段计时：读取、规则、DSH 首 token、工具、发送，避免继续凭感觉优化。
7. 用一组真实群聊/私聊样例做回归，不只看单测和假运行时。

---

## 9. 当前结论

- **DSH 是 Agent 大脑/工具循环，但不是微信接入、发送和业务执行层。**
- **群聊主链路已经由 DSH 接管；私聊/助手仍是 headless；legacy 仍保留 Python 编排。**
- **实际体验问题大多来自三层边界没有完全收敛，而不是 DSH 本身“不会”。**
- 下一步最值得做的是：统一私聊/群聊的 DSH 会话生命周期 + 建立全链路阶段计时 + 收敛重复的 prompt/意图链路。

---

## 10. 已开始落地：transport 边界

第一阶段已经把「Agent 发送」从直接调用适配器改为明确的任务边界：

```text
Agent / Runtime
  → send_agent_message()
  → WechatautoTransport.submit()
  → transport_jobs（SQLite）
  → transport worker
  → RuntimeManager._send_agent_message_now()
  → ReplicaAdapter / wechatauto
```

新增内容：

- `transport_jobs` 表：幂等键、目标、payload、状态、重试次数、错误、耗时
- [`app/services/wechatauto_transport.py`](../app/services/wechatauto_transport.py)：持久队列和 worker
- [`app/api/transport.py`](../app/api/transport.py)：`/api/transport/jobs` 查询
- `RuntimeManager.send_agent_message()` 统一投递到 transport
- transport 未启动时自动回退直发，避免测试/早期启动发送断裂

当前限制：

- worker 仍在 Qunban 进程内，还不是独立 wechatauto 守护进程
- 读取事件仍由 RuntimeManager 轮询适配器
- outbox 补发仍走原链路，未完全迁移

下一阶段：

1. 把 worker 和 ReplicaAdapter 的发送/激活/确认抽到独立 wechatauto 服务
2. 增加 `POST /v1/outbound`、`GET /v1/outbound/{id}`、事件流
3. 把读取监听也迁到该服务的 `message.created` 事件
4. 接入持久任务状态机和完成验证
5. 再拆 DSH Chat / Task lane 与 Router

---

## 11. 已开始落地：持久任务

针对凌晨交互里“Agent 答应了但任务丢了”“标 done 但没有真正删除”的问题，新增：

- `agent_task_runs` 表
- [`app/services/task_runs.py`](../app/services/task_runs.py)
- [`app/api/tasks.py`](../app/api/tasks.py)

任务有：

```text
queued / running / done / failed / cancelled
```

以及显式步骤：

```text
ack → execute → send → verify
```

群聊 DSH 返回 `action=task` 时，不再只发一句确认：

1. 创建持久任务
2. 发送 ack
3. 后台用独立 DSH session 继续执行
4. 把最终结果发回群
5. 标记步骤和任务状态
6. 服务启动/上线后恢复未完成任务

当前限制：

- `verify` 目前只验证最终发送成功，还没有任务类型级的外部事实校验
- 任务 API 还没有完整的前端管理页
- 任务插件生成/晋升还未接入
- 任务级 session 还没有和 Chat lane 的 session 策略统一

---

## 12. 已开始落地：wechatauto 服务契约

在 worker 真正抽出独立进程之前，先固定了服务契约：

```text
GET  /api/wechatauto/status
GET  /api/wechatauto/events?limit=&type=
POST /api/wechatauto/outbound
GET  /api/transport/jobs
```

事件：

- `message.created`：新群消息入库
- `outbound.queued / sending / sent / failed`：发送任务状态

`POST /api/wechatauto/outbound` 已支持：

- `group_id`
- `text`
- `sticker_id`
- `files`
- `meta`
- `reuse_chat`
- `idempotency_key`
- `wait`

目前仍是 Qunban 进程内实现；下一阶段把同一个契约搬到独立 wechatauto 服务。

---

## 13. 已开始落地：轻量 Router

新增确定性 Router：

- [`app/services/agent_router.py`](../app/services/agent_router.py)
- `POST /api/router/classify`

输出：

```json
{"route":{"lane":"chat|task|ignore","confidence":0.82,"reason":"..."},"scope":"group"}
```

规则只做分流，不生成回复：

- 命中任务/工具动作词 → task
- 命中闲聊词或短消息 → chat
- 无工具 → 只能 chat
- 空消息 → ignore

当前它先作为观测和后续 lane 分流的依据，还没有替换主链路：群聊仍由 DSH 决定，私聊简单消息仍走 `quick_chat`。下一步才会把 Router 结果接到 Chat lane / Task lane。

---

## 14. 已落地：DSH Chat lane（群聊简单消息）

新增 profile：

```text
qunban-chat-lane
  · base + acp-app
  · 无 @qunban/dsh-native
  · 无原生工具
  · 无 qunban 工具
  · 持久 ACP session，按群复用
```

链路：

```text
群消息 → 规则门 → Router
  ├─ lane=chat → DSH Chat lane（无工具）
  └─ lane=task → DSH primary（带工具）
```

- Chat lane 使用 `chat-group-<id>` session
- 第一条消息冷启动，后续复用
- 失败自动回退 primary
- 私聊 quick_chat 暂未迁移到 Chat lane
- 默认开关：`dsh_chat_lane_enabled=true`
- 可查询 `/api/dsh/status` 的 `chat_lane`

实测：

```text
首条 Chat lane 约 3.7 秒
同 session 后续约 0.66–0.8 秒
```

这解决了“所有群聊消息都带全工具”的 token 和首轮问题。

---

## 15. 私聊 Chat lane

管理员私聊的简单闲聊现在优先使用同一个无工具 Chat lane：

```text
私聊简单消息
  → Chat lane 已运行？
      是：chat-admin-<wxid> session
      否：先 quick_chat 快速回复，同时后台预热 Chat lane
  → 下一轮起走 DSH Chat lane
```

这样：

- 首条私聊不会因为 Chat lane 冷启动而变慢
- 后续私聊和群聊统一到 DSH Chat
- 失败自动回退 quick_chat
- 复杂私聊仍进入 AssistantService / DSH Task

当前限制：私聊的工具任务没有独立 Task lane，仍走 headless DSH。

---

## 16. Chat lane 的知识检索

Chat lane 现在会在生成前尝试取少量 B站知识：

```text
当前消息
  → bili_knowledge.search(top 3)
  → 2.5 秒超时
  → 距离阈值 0.92
  → 拼入 context capsule 的“相关知识”
```

- 知识是外部检索片段，不写入 session 历史
- 超时/索引不可用时不阻塞聊天，直接省略
- 只在 Chat lane 使用；Task lane 仍可通过 `qunban_bili_knowledge` 深挖
- 知识库路径改为绝对路径 `settings.data_dir/knowledge/chroma`，不再依赖进程 cwd
- 已从 `bili_videos` 回填现有视频：当前 collection 约 389 个 chunk
- 重建脚本：`scripts/rebuild_bili_knowledge.py --limit N`

---

## 17. 已落地：独立 Task lane

新增 profile：

```text
qunban-task-lane
  · base + acp-app
  · @qunban/dsh-native（task toolset）
  · 独立持久 ACP session
  · 只处理 durable task 的后台继续执行
```

- Chat lane、primary、Task lane 现在彼此独立
- Task lane 使用 `task-<id>` session，不再污染群聊 primary 历史
- `TOOLSET=task` 隐藏 `wechat_send_message`，最终发送仍由 Qunban 完成
- `/api/dsh/status` 返回 `task_lane`
- Task 的最终结果仍经 transport 队列发送

验证：

```text
qunban-task-lane 启动成功
测试 prompt 返回 {"action":"none"}
```

“按任务加载工具”目前仍是静态 task toolset，还没有做到每个任务单独生成 allowlist。

---

## 18. 任务面板支持重试/取消

新增：

```text
POST /api/tasks/{id}/retry
POST /api/tasks/{id}/cancel
```

- 失败/取消/完成的任务可以重新执行
- 排队/执行中的任务可以取消
- 任务面板直接提供按钮
- 取消会取消对应的后台 DSH task job，并标记 `cancelled`

---

## 19. 按任务加载工具（第一版）

每个 durable task 现在会：

1. 根据任务请求生成工具 allowlist
2. 创建独立的 `qunban-task-<id>` profile
3. 用 `QUNBAN_ALLOW_TOOLS` 启动 Task lane
4. 执行完立即停止该 ACP 进程

规则示例：

- 基础：`wechat_status/list_groups/read_group`、`qunban_memory_search/save`
- B站：增加 `qunban_bili_search/knowledge`
- 查询/接口：增加 `qunban_call_interface`
- 表格：增加 `qunban_create_excel`
- 技能/MCP：增加对应工具和动态前缀

优点：

- 每次任务只加载需要的工具
- 不再让后台任务继承整个群聊工具集
- 任务结束进程即退出，不会污染长期 session

当前限制：

- allowlist 还是关键词规则，不是模型自己规划的精确工具集
- 插件安装类任务不在群聊 Task lane 的权限内

---

## 20. Chat lane 发送预热

Chat lane 在调用 DSH 的同时，会并行执行 `preflight_group(group_id)`：

- 提前打开/定位目标群窗口
- DSH 回复后最多等 80ms 复用预热结果
- 预热失败则回退正常发送路径
- 日志记录 `窗口预热=是/否`

这样简单消息的发送等待可以和模型推理重叠，而不是串行等待。

---

## 21. 发送失败不再自动重发

发送异常现在默认：

```text
status=failed
result.uncertain=true
result.retryable=true
```

停止自动重试，避免“第一次其实已经发出去，第二次又发一遍”。

手动重试：

```text
POST /api/transport/jobs/{id}/retry
```

只有失败任务可以重试；重试前仍应结合 outbox 验证/人工确认。

这比原来的“失败自动重试 3 次”更保守，但更符合微信群发场景。

---

## 22. 统一诊断接口

新增：

```text
GET /api/diagnostics
```

返回：

- runtime 在线状态/适配器
- transport 是否运行、队列统计、是否仍在主进程
- primary / chat / task lane 状态
- B站知识索引数量与路径
- durable task 状态计数
- wechatauto 事件缓冲数量（当前仍非持久化）

这个接口用于以后排查“卡在哪一步”，不需要同时查四五个端点。

---

## 23. 任务完成验证

任务不再把“发送成功”直接当作“任务完成”。

新增 `app/services/task_verify.py`：

- 查找带 `meta.task_id` 的 transport job 是否为 `sent`
- 检查结果/请求中出现的 Windows 文件路径是否真实存在
- 检查结果/请求中的 BV 号是否存在于 `bili_videos`
- 输出 check 明细和 summary

任务面板显示：

```text
已完成 + 已验证
已完成 + 未验证
```

未验证不会自动重试发送，避免重复消息；需要人工确认或重新执行。
