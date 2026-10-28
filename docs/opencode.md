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
  写成 OpenCode 的 OpenAI-compatible provider。
- Agent：群伴只通过 OpenCode 的 `system` 参数注入人设、记忆、通道规则；
  工具调用、步骤、重试、压缩和会话历史都由 OpenCode 自己负责。
- 群伴能力：`app/mcp_server.py` 作为本地 MCP server 启动，把微信、B站、
  记忆、文件、终端工具暴露给 OpenCode。MCP 工具最终仍走群伴的
  capability 权限网关。
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
