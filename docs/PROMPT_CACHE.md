# 提示词分层与缓存

群伴的模型调用统一遵循「稳定内容在前、易变内容在后」的分层提示词结构。目的不是省几个字，而是让 DeepSeek / OpenAI 兼容服务的**前缀缓存（context caching）**真正命中：同一段前缀第二次出现时按缓存价计费，首字延迟也更低。

## 1. 为什么之前的命中率低

DeepSeek 按「最长公共前缀」判断缓存。旧版群聊回复的系统提示词顺序是：

```text
人设 → 群聊信息（含当前时间）→ 长期记忆 → B站上下文 → 意图 → 规则 → 表情包 → JSON 协议 → 工具
```

当前时间在第 3 行，记忆和意图每轮都变，规则、JSON 协议、工具列表（通常占 60%–80% 的 token）全部排在这些易变内容之后，所以几乎每轮都是整段冷启动。

Operator（助手 / 微信管理员私聊）也是同样的结构：人设和长期记忆排在工具目录与输出协议之前，工具目录（很大）每轮都被记忆变化作废。

## 2. 三层结构

所有提示词都由 [`app/services/prompting.py`](../app/services/prompting.py) 的 `PromptBuilder` 组装：

| 层 | 内容 | 变化频率 | 缓存表现 |
| --- | --- | --- | --- |
| `static` | 角色、规则、输出协议、工具使用约束、示例 | 只有改代码才会变 | 全模型、全群共享 |
| `context` | 人设、群名、触发方式、表情包目录、工具目录、技能/MCP、自定义提示词 | 改设置/改群配置才变 | 同群/同配置内命中 |
| `dynamic` | 当前时间、长期记忆、B站上下文、意图/情绪、成员昵称、本轮消息 | 每轮都变 | 不参与缓存，永远在最后 |

`Prompt.cache_prefix_chars` 记录 `static + context` 的长度，随请求一起写入 `llm_usage.cache_prefix_chars`，方便对照「设计上的可缓存部分」与「provider 实际命中的 token」。

### 关键规则

1. 绝不在 `dynamic` 之前放 `datetime.now()`、随机数、记忆、消息历史或本轮计划。
2. 可选块（例如工具目录）放在所在层的**末尾**：开关它只改变尾部，不会移动稳定前缀。
3. 改提示词文案会冷启动一次缓存；调整顺序会让每个模型都冷启动一次，所以顺序只在必要时动。
4. 群级人设、群名属于 `context`：同一个群连续对话能命中，跨群则从人设处分叉；全局 `static` 仍然跨群共享。

## 3. 各模块的布局

| 调用点 | `call_site` | 稳定前缀 | 易变尾部 |
| --- | --- | --- | --- |
| 群聊意图分类 | `group.intent` | 分类器规则 + 输出字段 + 示例（一直静态） | 群、上下文、记忆、B站、工具 |
| 群聊回复 | `group.reply` | 回复规则 + JSON 协议 + 输出风格 + 人设/群信息/表情包/B站背景/工具 | 时间、记忆、意图、成员、本轮消息 |
| 主动发起话题 | `group.proactive` | 任务规则 + 协议 + 人设/群信息/表情包 | 时间、记忆、成员、最近消息 |
| Operator 原生循环 | `assistant.operator` | Operator 核心 + 输出协议 + 通道/工作区/工具目录/步数 | 长期记忆 |
| Operator OpenCode | `opencode.assistant` | 核心 + OpenCode 运行规则 + 工具示例 + 工作区/工具/资源 | 长期记忆 |
| 长任务侧聊 | `assistant.quick_chat` | 聊天人格规则 | 人设、后台状态 |
| 记忆总结 | `memory.summary` | 记忆提取规则（一直静态） | 群名、已有记忆、聊天记录 |
| B站选题/选视频/理解 | `bili.topic` / `bili.pick` / `bili.analyze` | 各任务规则 | Agent 上下文、历史、候选、转写 |
| OpenCode 群任务 | `opencode.group_task` | 由 OpenCode 会话自行维护 | 任务简报 |

### OpenCode 会话的记忆快照

OpenCode 把对话历史保存在它自己的会话里，系统提示词排在整个历史前面。如果每轮都把新的长期记忆写进系统提示词，记忆之后的历史（可能几千 token）就会整段失效。

因此 `AssistantService._cached_opencode_system_prompt()` 按 `session_id` 冻结记忆快照，默认 15 分钟（`OPENCODE_PROMPT_TTL`）才刷新一次；人设、通道、能力开关或自定义提示词变化时会立即重建。这样同一会话内系统提示词保持字节级一致，历史前缀可以持续命中。

## 4. 查看命中率

```text
GET /api/llm/usage?hours=24
```

```json
{
  "hours": 24,
  "calls": 120,
  "prompt_tokens": 345000,
  "cache_hit_tokens": 250000,
  "cache_miss_tokens": 95000,
  "cache_hit_rate": 0.7246,
  "sites": [
    {"call_site": "group.reply", "calls": 40, "prompt_tokens": 180000,
     "cache_hit_tokens": 150000, "cache_miss_tokens": 30000,
     "cache_hit_rate": 0.8333}
  ]
}
```

前端「设置 → 数据概览」也会显示 24 小时命中率。首次调用、改完提示词后的第一轮、以及 provider 清理缓存后都会是 0，属于正常冷启动；看稳定运行后的按站点命中率才有意义。

本地实测（同一 system 前缀、不同 user 文案，DeepSeek）：

```text
第 1 次：prompt=1015  cache_hit=0    cache_miss=1015
第 2 次：prompt=1016  cache_hit=768  cache_miss=248   (75.6%)
```

两次请求的 system 只有 1.6k 字符，第二轮已经命中 12 个 64-token 块；群聊回复的 system 通常 3–6k 字符，稳定后的提升会更明显。

数据写入本地 `llm_usage` 表（每请求一行，保留 30 天），不包含提示词正文，只保留 token 数和调用点。

## 5. 自测

```powershell
.\.venv312\Scripts\python.exe scripts\check_prompt_cache.py
```

覆盖：

- 只改时间/记忆/成员时，缓存前缀必须逐字节一致；
- 时间、记忆不能出现在前缀里；
- 工具目录开关只影响尾部；
- Operator / OpenCode / 主动发言 / 侧聊的前缀稳定性；
- DeepSeek 与 OpenAI 两种缓存字段的解析。

## 6. 新增提示词的检查清单

- [ ] 用 `PromptBuilder`，不要手拼 f-string。
- [ ] 所有 `datetime.now()`、随机值、数据库查询结果放进 `dynamic()`。
- [ ] 工具目录、资源列表、表情包目录放进 `context()` 的末尾。
- [ ] 给 `complete()` 传 `call_site` 和 `cache_prefix_chars`。
- [ ] 跑一遍 `scripts/check_prompt_cache.py`。
- [ ] 上线后看 `/api/llm/usage` 对应站点的命中率，低于 50% 说明还有动态内容漏在前缀里。
