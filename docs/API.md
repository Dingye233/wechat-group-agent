# API 与 WebSocket

基础地址：`http://127.0.0.1:8000`

自动文档：`/api/docs`（Swagger UI），OpenAPI：`/api/openapi.json`

错误格式：

```json
{"detail": "错误说明"}
```

Pydantic 校验错误会是数组，前端会拼接展示。

---

## 1. 系统与登录

### `GET /api/status`

```json
{
  "state": "online",
  "adapter": "mock",
  "account": {"wxid": "wxid_agent_demo", "name": "小助手", "avatar": ""},
  "has_qr": false,
  "qr_version": 1,
  "last_error": "",
  "message": "微信已连接，Agent 正在后台运行",
  "online_seconds": 120,
  "service_seconds": 300,
  "bot_name": "小助手",
  "available_adapters": [
    {"id": "mock", "name": "演示模式", "description": "...", "available": true, "reason": ""},
    {"id": "wcf", "name": "微信 PC（WeChatFerry）", "description": "...", "available": false, "reason": "缺少 wcferry，请安装 requirements-wcf.txt"}
  ],
  "ws_clients": 1,
  "version": "0.1.0",
  "llm_configured": false,
  "demo_mode": true,
  "llm_model": "deepseek-chat",
  "timezone": "Asia/Shanghai"
}
```

`state`：`offline` / `starting` / `qr` / `online` / `error`

### `POST /api/login`

```json
{"adapter": "mock"}
```

返回 `{"ok": true, "status": {...}}`。适配器不可用时返回 400。

### `GET /api/login/qr`

返回 PNG/JPEG 二进制，`Cache-Control: no-store`。没有二维码时 404。

### `POST /api/logout`

停止适配器并回到 offline。

### `GET /api/account`

```json
{"account": {...} | null, "state": "online"}
```

### `GET /api/health`

```json
{"ok": true, "version": "0.1.0"}
```

### `GET /api/stats`

```json
{
  "groups": 3,
  "messages": 128,
  "stickers": 6,
  "members": 12,
  "agent_replies": 8,
  "agent_logs": 10,
  "db_size_bytes": 733176
}
```

---

## 2. 群聊

### `GET /api/groups`

```json
{
  "groups": [
    {
      "id": 1,
      "external_id": "room_demo_product@chatroom",
      "name": "产品摸鱼群",
      "member_count": 5,
      "last_message_at": "2026-10-27T11:20:00Z",
      "agent": {
        "agent_enabled": true,
        "trigger_mode": "mention",
        "keywords": [],
        "probability": 1.0,
        "cooldown_seconds": 10,
        "max_replies_per_hour": 30,
        "persona": "",
        "model": "",
        "sticker_enabled": true,
        "quiet_start": "",
        "quiet_end": "",
        "mention_on_reply": false
      },
      "last_message": {
        "id": 12,
        "sender_name": "小助手",
        "content": "收到",
        "kind": "text",
        "is_from_agent": true,
        "created_at": "2026-10-27T11:20:00Z"
      },
      "message_count": 42
    }
  ]
}
```

### `GET /api/groups/{id}/messages`

Query：

- `limit`：1–200，默认 60
- `before_id`：向前加载

```json
{
  "messages": [
    {
      "id": 42,
      "group_id": 1,
      "external_id": "mock-xxx",
      "sender_id": "wxid_azhe",
      "sender_name": "阿哲",
      "is_from_agent": false,
      "is_self": false,
      "kind": "text",
      "content": "早上好",
      "sticker_url": "",
      "created_at": "2026-10-27T11:20:00Z",
      "meta": {}
    }
  ],
  "has_more": true
}
```

`kind`：`text` / `image` / `voice` / `video` / `sticker` / `app` / `system` / `other`

`sticker_url` 仅在 `kind=sticker` 时可作为图片地址。

### `GET /api/groups/{id}/members`

会尝试向当前适配器刷新成员（WCF 有 10 分钟缓存），并更新数据库。

```json
{
  "members": [
    {"wxid": "wxid_azhe", "nickname": "阿哲", "alias": "", "is_owner": false, "is_admin": false}
  ],
  "count": 5
}
```

### `PATCH /api/groups/{id}/config`

只传需要修改的字段：

```json
{
  "agent_enabled": true,
  "trigger_mode": "keyword",
  "keywords": ["总结", "待办"],
  "probability": 0.8,
  "cooldown_seconds": 30,
  "max_replies_per_hour": 20,
  "quiet_start": "23:30",
  "quiet_end": "08:00",
  "sticker_enabled": true,
  "persona": "你是群里的产品经理，说话简短务实。",
  "model": "",
  "mention_on_reply": false
}
```

`trigger_mode`：`mention` / `keyword` / `all`

`quiet_start` 与 `quiet_end` 必须同时为空或同时设置，格式 `HH:MM`。

### `POST /api/groups/{id}/send`

```json
{"text": "大家好", "sticker_id": 3}
```

至少提供文字或表情之一；返回入库后的消息数组。

### `GET /api/groups/{id}/logs?limit=20`

```json
{
  "logs": [
    {"id": 1, "group_id": 1, "level": "info", "event": "reply", "detail": "回复：…｜模型：deepseek-chat｜耗时：900ms", "created_at": "…"}
  ]
}
```

`event`：`reply` / `skip` / `error` / `info`

---

## 3. 表情包

### `GET /api/stickers?tag=开心`

```json
{
  "stickers": [
    {
      "id": 1,
      "name": "收到",
      "tags": ["收到", "OK", "好的"],
      "description": "认真点头，表示收到",
      "url": "/media/stickers/builtin-ok.png",
      "filename": "builtin-ok.png",
      "mime": "image/png",
      "width": 512,
      "height": 512,
      "size_bytes": 12345,
      "usage_count": 3,
      "created_at": "…"
    }
  ],
  "tags": ["OK", "收到", "开心"]
}
```

### `POST /api/stickers`

`multipart/form-data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| file | file | PNG/JPG/GIF/WEBP，≤5MB |
| name | text | 名称 |
| tags | text | 逗号分隔 |
| description | text | 模型选择依据 |

### `PATCH /api/stickers/{id}`

```json
{"name": "新名字", "tags": ["开心"], "description": "描述"}
```

### `DELETE /api/stickers/{id}`

### `POST /api/stickers/{id}/send`

```json
{"group_id": 1}
```

### `POST /api/stickers/seed`

如果表情库为空，生成 6 个内置示例；否则返回 `created: 0`。

---

## 4. 设置

### `GET /api/settings`

```json
{
  "settings": {
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_model": "deepseek-chat",
    "llm_temperature": 0.8,
    "llm_max_tokens": 300,
    "llm_json_mode": true,
    "llm_api_key_set": false,
    "llm_api_key_hint": "",
    "llm_configured": false,
    "demo_mode": true,
    "default_persona": "…",
    "default_trigger_mode": "mention",
    "default_keywords": [],
    "context_messages": 18,
    "reply_delay_seconds": 1.2,
    "timezone": "Asia/Shanghai",
    "bot_aliases": "",
    "wcf_wechat_exe": "",
    "auto_login": "",
    "seed_sample_stickers": true,
    "timezone_options": [{"value": "Asia/Shanghai", "label": "北京时间 (UTC+8)"}]
  }
}
```

API Key 不会明文返回，只返回是否设置与末四位提示。

### `PUT /api/settings`

只传需要修改的字段。`llm_api_key` 为空表示保留旧 Key；`clear_api_key: true` 表示删除。

```json
{
  "llm_base_url": "https://api.deepseek.com/v1",
  "llm_model": "deepseek-chat",
  "llm_api_key": "sk-...",
  "llm_temperature": 0.8,
  "context_messages": 18,
  "reply_delay_seconds": 1.2,
  "timezone": "Asia/Shanghai",
  "bot_aliases": "小助手,助手",
  "wcf_wechat_exe": "",
  "auto_login": ""
}
```

### `POST /api/settings/test-llm`

使用已保存的设置请求模型，返回：

```json
{
  "result": {"ok": true, "reply": "连接成功", "model": "deepseek-chat", "latency_ms": 320}
}
```

未配置 Key 返回 400。

---

## 5. WebSocket

连接：`ws://127.0.0.1:8000/ws`

服务端统一事件格式：

```json
{"type": "message.new", "data": {...}, "ts": 1730000000.0}
```

| type | data | 说明 |
| --- | --- | --- |
| `hello` | status | 连接建立，发送当前状态 |
| `status` | status | 登录/适配器状态变化 |
| `account.updated` | account | 账号信息 |
| `message.new` | message | 新消息（含 Agent 消息） |
| `groups.updated` | `{count,new?,reason?}` | 群列表变化，客户端应重新拉取 |
| `members.updated` | `{group_id,count}` | 成员数量变化 |
| `agent.typing` | `{group_id}` | Agent 正在生成回复 |
| `agent.log` | agent_log | Agent 决策日志 |
| `adapter.log` | `{level,message}` | 适配器日志 |
| `adapter.error` | `{message}` | 适配器错误 |
| `settings.updated` | `{llm_configured}` | 设置变化 |
| `ping` | `{}` | 心跳 |

前端收到 `groups.updated` 会防抖重新拉群列表；收到 `message.new` 会按消息 ID 去重并按时间排序。

---

## 6. Agent JSON 协议

系统提示词要求模型只输出一个 JSON 对象：

```json
{"action": "reply", "reply": "收到，我看看", "sticker": 3}
```

```json
{"action": "reply", "reply": "", "sticker": "笑死"}
```

```json
{"action": "none"}
```

- `reply`：要发送的文字，可为空
- `sticker`：表情包 id / 名称 / 标签，可为 `null`
- `action=none` 或两者都为空：不发送
- 模型返回 Markdown 代码块时，解析器会尝试提取第一个 JSON 对象
- `json_mode=false` 时会关闭 `response_format`，仍按 JSON 解析

模型失败会记录 `agent_logs` 并通过 WebSocket 推送，不会向群里发送错误提示。
