# Official OpenCode integration

群伴的 Operator 现在直接运行 GitHub 官方项目
[anomalyco/opencode](https://github.com/anomalyco/opencode)，不再在群伴里
复刻一套 agent loop。

## 接入方式

- 二进制来源：官方 GitHub Release（Windows x64），当前固定为 `v1.19.4`。
- 启动命令：`opencode serve --hostname 127.0.0.1 --port <动态端口>`。
- 数据隔离：OpenCode 使用自己的
  `data/opencode/opencode.db` 和 `data/opencode/xdg/`，不会污染用户已有的
  OpenCode 会话。
- 模型：把群伴设置里的 `llm_base_url`、`llm_api_key`、`llm_model`
  写成 OpenCode 的自定义 OpenAI-compatible provider。每个群伴用户配置自己的
  服务端 API；群伴不内置 Key，也不读取 OpenCode 官方账号或云端免费模型。
  该 API 需要支持 OpenAI-compatible Chat Completions 和 tool calling。
- Agent：群伴只通过 OpenCode 的 `system` 参数注入人设、记忆、通道规则；
  工具调用、步骤、重试、压缩和会话历史都由 OpenCode 自己负责。
- 群伴能力：`app/mcp_server.py` 作为本地 MCP server 启动，把微信、B站、
  记忆、文件、终端工具暴露给 OpenCode。每个群伴 tool 都会注册成独立 MCP
  工具（如 `qunban_read_file`、`qunban_run_powershell`），同时保留通用入口
  `qunban_tool(name, args)`。MCP 工具最终仍走群伴的 capability 权限网关。
- 原生工具清单：`data/opencode/tools.json` 由 `app/services/tool_registry.py`
  管理，OpenCode 可以调用 `qunban_tool_admin` 执行
  `list/get/upsert/remove/enable/disable/sync/reload/refresh`。
- 真正的新功能：`handler_type="native"` 只能引用已有原生工具；要新增实际能力，
  用 `handler_type="script"`，把 `.py` / `.ps1` 写到
  `data/opencode/tools/` 并注册。脚本从 stdin 读取 JSON 参数、输出结果，
  由群伴以 admin 范围执行。新脚本工具可以立即通过
  `qunban_tool(name="工具名", args={...})` 调用；当前任务结束后会自动
  重连 MCP，下一轮直接入口 `qunban_<工具名>` 可用。也可以显式调用
  `qunban_tool_admin(action="refresh")`，它会等当前任务空闲后再刷新，不会
  切断正在执行的步骤。
- 群伴 MCP 项目 / 技能 / 接口：分别通过 `qunban_run_mcp`、
  `qunban_run_skill`、`qunban_call_interface` 调用；`qunban_capabilities`
  会返回可直接使用的工具名、示例参数和 `call_tool` / `call_args` 提示。
- 微信发送：必须走 wechatauto-replica 的 `qunban_send_message` /
  `send_contact_message` 链路。OpenCode 默认不注册 `keyboard_*` / `mouse_*`；
  wechatauto 发送前会先打开目标会话并确认输入框，确认不了就取消，不会盲打键盘。
- OpenCode 的 `run_powershell` 会拦截包含 `SendKeys`、`wechatauto`、`WeChat`、
  `微信`、`SendMsg` 等微信操作关键词的命令，防止再用 PowerShell 模拟回复。
- B站看法 → 评论：自动刷视频会生成 `opinion`；评论动作通过
  `bili_comment` 工具发送，若模型未填写 `comment_text`，会直接使用
  `opinion`。Agent 也可以先调用 `bili_last_opinion`，再调用
  `bili_comment`，把最近一次看法发到对应视频评论区。
- 事件：群伴订阅 OpenCode 的 `/event`，把 `message.part.updated` 中的
  tool running/completed 事件同步到后台和微信管理员进度。
- 停止：微信管理员发「停止」时，群伴调用 OpenCode 的
  `/session/{id}/abort`。

## 工具与插件展示

「助手 → OpenCode」面板会显示：

- OpenCode 内置工具（`/experimental/tool/ids`）
- 群伴 MCP 连接状态（`/mcp`）
- 配置插件和 `data/opencode/plugins/` 下的本地插件
- OpenCode 版本、端口和会话数

OpenCode 插件是官方插件格式，直接放在 `data/opencode/plugins/<plugin>/`
即可；下次启动/重启 OpenCode 时会自动加载。官方工具、插件和 MCP 工具都
会出现在 OpenCode 自己的工具列表里，群伴只做展示和事件转发。

## 兜底

设置里关闭「使用官方 OpenCode 作为 Operator 引擎」后，群伴会回到原来的
内置执行循环。这样 OpenCode 升级、二进制损坏或模型暂时不可用时，群伴仍
能继续处理消息。

## 手动安装

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-opencode.ps1
```

也可以设置 `OPENCODE_BIN` 指向任意官方 `opencode.exe`。
