# DeepSeek Harness 融合架构

群伴现在只有一套 Agent 运行时：**DeepSeek Harness（DSH）**。OpenCode 不再是同级引擎，也不再是回退路径。

融合不是“把群伴包成一个 MCP 服务”，而是三层：

1. **wechatauto 是消息源和发送端**：`RuntimeManager` 通过 replica 适配器读取 `message.db` / `contact.db`，把新消息交给 Agent；回复仍由 `send_agent_message` / `send_contact_message` 调用 wechatauto-replica 发出，落库确认后才算成功。
2. **群伴是业务与安全网关**：Python 侧保留消息入库、群配置、意图、记忆、任务、能力权限、发送校验和重试队列。所有副作用都必须经过 `/api/opencode/tool` 网关。
3. **DSH 是唯一的 Agent 大脑**：通过原生 Cordis 插件 `@qunban/dsh-native` 暴露 `wechat_*` / `qunban_*` 工具；用 `qunban-chat` profile 生成群聊回复，用 `qunban` / `qunban-group` 执行任务。没有 MCP 子进程，也没有 `mcp__qunban__*` 名称层。

## 1. 代码结构

| 文件 | 职责 |
| --- | --- |
| [`dsh/qunban-native/`](../dsh/qunban-native/) | 原生 DSH Cordis 插件源码；工具定义、HTTP 网关客户端、scope 裁剪 |
| [`app/services/dsh_runtime.py`](../app/services/dsh_runtime.py) | 版本发现、安装、校验、更新；`data/dsh/state.json` |
| [`app/services/dsh_agent.py`](../app/services/dsh_agent.py) | 每个任务启动一个 `dsh --profile ... --json` 进程；profile 生成、插件安装、事件解析、会话映射、用量上报 |
| [`app/services/dsh_skills.py`](../app/services/dsh_skills.py) | 把 `data/agent_workspace/skills` 转成 DSH 的 `<name>/SKILL.md` 格式，写入 `data/dsh/skills` |
| [`app/services/agent_engines.py`](../app/services/agent_engines.py) | `resolve_agent_engine()`：DSH；仅当 DSH 明确禁用时才用内置循环 |
| [`app/core/agent.py`](../app/core/agent.py) | `_generate_dsh_chat()`：wechatauto 消息 → DSH `qunban-chat` profile → 回复文本；失败回退旧生成链路 |
| [`app/api/dsh.py`](../app/api/dsh.py) | 状态、检查更新、更新、会话重置、停止 |
| [`app/services/dsh_plugins.py`](../app/services/dsh_plugins.py) | 插件目录、导入、创造模式和 profile patch 合并；见 [PLUGINS.md](PLUGINS.md) |
| [`frontend/index.html`](../frontend/index.html) | 「助手 → DeepSeek Harness」面板；顶层「插件」页 |

## 2. 运行链路

### 2.1 群聊读取与回复

```text
微信数据库 (wechatauto-replica 监听)
  → RuntimeManager 入库 Message
  → AgentEngine._handle()
      → 意图/任务判断
      → _generate_dsh_chat()
          → dsh --profile qunban-chat --json
          → 返回 {"action":"reply","messages":[...]}
      → _build_segments()
      → RuntimeManager.send_agent_message()
          → wechatauto-replica 打开目标会话、确认输入框、发送
          → 轮询 message.db 确认落库
          → 失败进入 outbox，等待重发
```

`qunban-chat` **没有任何工具**：不能读文件、不能跑 shell、不能调用微信发送。这样群聊回复阶段不可能绕过 wechatauto，也不会把工具调用误当成聊天内容。需要工具/复杂任务时，仍由群伴的任务链路转交 `qunban-group`。

### 2.2 Operator / 管理员私聊

`qunban` profile 加载原生插件，工具分为：

- 微信：`wechat_status`、`wechat_list_groups`、`wechat_read_group`、`wechat_send_message`、`wechat_list_contacts`
- 记忆：`qunban_memory_search`、`qunban_memory_save`
- B站：`qunban_bili_search`、`qunban_bili_knowledge`
- 接口：`qunban_call_interface`
- 技能/MCP：`qunban_run_skill`、`qunban_run_mcp`
- 插件：`qunban_plugin_install`（安装 DSH Operator 刚写到 `data/dsh/studio` 的插件）
- 命令：`qunban_powershell`（服务端拦截微信关键词）

DSH 原生 `read` / `write` / `edit` / `glob` / `grep` / `webfetch` 仍可用；原生 `pwsh` / `bash` / `terminal` / `workflow` / `subagent` 被禁用，避免绕过群伴的微信关键词拦截和审计。

### 2.3 群任务

`qunban-group` profile 只保留原生插件的 group scope 工具，DSH 原生 shell/fs/web/skill/subagent 全部禁用，权限为 `read-only`。群任务通过 `qunban_run_skill` / `qunban_run_mcp` 之外的专用工具工作；`run_skill` / `run_mcp` 在 group scope 会被网关拒绝。

## 3. 原生插件，而不是 MCP

旧版把 `app/mcp_server.py` 注册成 `@deepseek-ai/dsh-mcp-client`，模型看到的是：

```text
mcp__qunban__send_message
mcp__qunban__read_group
mcp__qunban__list_groups
...
```

问题：

- 多一个 Python MCP 子进程，启动慢、生命周期多一层；
- 工具名多一层前缀，模型更容易选错；
- MCP 的 15+ 工具全部暴露，group profile 还会看到被网关拒绝的通用工具；
- system prompt 必须额外解释 `mcp__qunban__*` 映射。

现在插件直接 `ctx.tools.register()`：

```js
wechat_send_message
wechat_read_group
wechat_list_groups
qunban_memory_search
...
```

工具定义在 [`dsh/qunban-native/lib/index.js`](../dsh/qunban-native/lib/index.js)，所有调用仍走：

```text
POST /api/opencode/tool
  { name, args, scope }
  x-qunban-token: <token>
```

网关继续执行 capability 检查、群/联系人解析、`RuntimeManager` 发送校验和 outbox。插件本身不直接访问微信数据库，也不模拟键鼠。

## 4. Skills 融合

DSH 的 `dsh-skill-filesystem` 支持 `customSkillDirs`。群伴启动/首次聊天时：

```text
data/agent_workspace/skills/<name>/SKILL.md
  → data/dsh/skills/<name>/SKILL.md  (补 YAML frontmatter)
  → profile patch 的 customSkillDirs
```

因此 DSH 的 skill 目录里能直接看到群伴技能；`qunban_run_skill` 仍负责在群伴权限网关上执行脚本。转换逻辑在 [`app/services/dsh_skills.py`](../app/services/dsh_skills.py)，原始技能目录仍是唯一真源。

## 5. Profile 与 patch

| profile | 用途 | 插件 scope | DSH 原生工具 | 权限 |
| --- | --- | --- | --- | --- |
| `qunban` | Operator / 管理员私聊 | admin | fs/web/skill，无 shell | `danger-full-access` |
| `qunban-group` | 群任务 | group | 全部禁用 | `read-only` |
| `qunban-chat` | 群聊回复 | 不加载插件 | 全部禁用 | `read-only` |

每次启动会：

1. 写入 profile 的 `package.json` 和 `cordis.patch.yml`；
2. 把 `dsh/qunban-native` 复制到 `<profile>/node_modules/@qunban/dsh-native`；
3. 同步 skills；
4. 用 `--patch` 注入本次 system prompt（`personaPrefix`）。

## 6. 会话与缓存

| 会话 key | 用途 |
| --- | --- |
| `main` | Operator / 管理员私聊 |
| `group-<id>` | 某个群的群任务，跨任务复用 |
| `chat-<id>` | 某个群的聊天回复，跨消息复用 |

稳定会话是缓存命中的关键。DSH 的 prefix 包含 system prompt + 工具清单 + 历史；同一会话的第二轮开始，`cacheReadTokens` 通常达到 90% 以上。实测：

- `qunban` 第二轮：约 9.7k prompt，9.1k cache read；
- `qunban-group`：约 5.8k prompt，5.2k cache read；
- `qunban-chat`：约 2–5k prompt，随历史增长，第二轮起明显命中。

群任务以前用 `group-task-<task>-<msg>`，每个任务都是新前缀；现在改为 `group-<id>`，这是“命中率低”的主要原因之一。

## 7. 微信可靠性

- **唯一发送入口**：`wechat_send_message`。group_id / contact_id 二选一，参数在插件侧先校验。
- **服务端再校验**：网关解析群/联系人，`RuntimeManager` 确认已接管、未暂停、目标会话存在；发送后轮询数据库确认落库。
- **幂等**：`client_msg_id` 相同的成功发送在 10 分钟内返回缓存结果，避免模型超时重试导致重复发送。
- **失败队列**：发送失败进入 outbox，重新接管时先查数据库确认是否已送达，再补发。
- **禁止旁路**：group/chat profile 没有 shell/键鼠；assistant 的原生 shell 被禁用，只保留带微信关键词拦截的 `qunban_powershell`。`keyboard_*` / `mouse_*` 默认不暴露。
- **不直接操作 UI**：DSH 永远不调用 UIA、键鼠或微信窗口；所有 UI 操作都在 `ReplicaAdapter` 内，由 `RuntimeManager` 串行化。

## 8. 设置项

| key | 默认 | 说明 |
| --- | --- | --- |
| `dsh_enabled` | `true` | DSH 总开关 |
| `operator_engine` | `dsh` | 兼容旧库；现在只解析 DSH |
| `dsh_channel` | `stable` | `stable` / `alpha` |
| `group_chat_engine` | `dsh` | `dsh` 或 `legacy`；`legacy` 只用于应急 |
| `opencode_enabled` | `false` | 兼容旧库，已不参与引擎选择 |

## 9. 版本与更新

DSH 是 TypeScript/Cordis 单体仓库，完整 `pnpm build` 不适合作为生产更新路径。群伴采用：

1. `git ls-remote --tags https://github.com/deepseek-ai/deepseek-harness` 读取 `dsh-v*` tag；
2. 按 `stable` / `alpha` 选择版本；
3. 安装同版本官方 npm 包到 `data/dsh/runtimes/<version>`；
4. `node .../lib/bin.js --version` 校验后写 `state.json`；
5. 失败保留旧版本。

原生插件在 `dsh/qunban-native`，不依赖 DSH 源码构建；插件用绝对路径加载运行时的 `@deepseek-ai/dsh-tools`，所以升级 DSH 后无需重新编译插件。

## 10. 限制

- **Provider**：当前 shipped profile 只支持 DeepSeek Messages 端点，`llm_base_url` 必须是 `api.deepseek.com`。其他 provider 会报错，不会静默切 OpenCode。
- **Node**：需要 Node.js 24.9+。
- **延迟**：每个任务仍是一个 `dsh` 进程，群聊回复约 3–5 秒；`qunban-chat` 已尽量缩小 prompt，但没有常驻进程。
- **成本**：第一次会话要付 system prompt + 工具清单；稳定会话后缓存命中率高。群聊建议保持 `qunban-chat` 无工具。
- **权限**：`qunban` 是 `danger-full-access`，管理员私聊等于本机代码执行；这是 Operator 的设计取舍。
- **更新风险**：alpha 是 pre-release；生产用 stable。

## 11. 排查

```powershell
# 检查/安装
.\.venv312\Scripts\python.exe scripts\setup_dsh.py --check
.\.venv312\Scripts\python.exe scripts\setup_dsh.py --channel stable

# 直接跑一次 headless
$env:DSH_HOME="C:\...\data\dsh\home"
node data\dsh\runtimes\0.2.1-rc.4\node_modules\@deepseek-ai\dsh\lib\bin.js --version
```

| 现象 | 处理 |
| --- | --- |
| `未安装` | 点「安装 / 更新」，或运行 `scripts/setup_dsh.py` |
| `不是 DeepSeek 官方端点` | 设置 → 模型接入改回 `https://api.deepseek.com/v1` |
| 插件未加载 | 看 `data/server.err.log` 的 `qunban-native ... failed to import`；确认 `dsh/qunban-native/lib/index.js` 存在，且 profile 的 `node_modules/@qunban/dsh-native` 已复制 |
| 工具未在当前范围启用 | 检查 `app/services/capabilities.py` 的 group 核心工具白名单和 `QUNBAN_SCOPE` |
| 群聊不回复 | 看 `data/server.err.log` 是否有 `DSH 群聊回复失败，回退内置链路`；检查 `group_chat_engine` |
| 会话恢复失败 | 面板「重置当前会话」，或删除 `data/dsh/sessions.json` 对应项 |
| 任务无输出 | 看 `data/dsh/home/sessions` 的 JSONL 和 `data/server.err.log` |
