# DeepSeek Harness 用于群聊 Agent 的评估

> **历史文档，已被 [DSH.md](DSH.md) 替代。** 当前实现已经用 `qunban-chat` 无工具 profile 接入群聊回复，wechatauto 作为消息源/发送端；不再采用本文建议的双通道。

结论：**技术上可以接入，但不建议把每条群聊短回复都替换成 DSH。**
更合适的是“双通道”：普通群聊继续走当前轻量 AgentEngine，复杂工具任务/Operator 走官方 DeepSeek Harness。

## 官方项目事实

- 仓库：<https://github.com/deepseek-ai/deepseek-harness>
- 许可证：MIT
- 架构：TypeScript + Cordis，一切皆插件（模型、工具、Session、Agent loop、沙箱、Web UI 都可替换）
- Python SDK：`deepseek-harness-sdk`，通过 stdio JSON-RPC 启动内置 `dsh` 进程
- 当前 SDK 版本参考：`0.2.1rc2`（developer preview，API 可能破坏性变化）
- Windows x64 运行时 wheel 约 84MB；SDK 运行不要求系统 Node.js
- 支持自定义 OpenAI-compatible provider、MCP client、Session 持久化、事件/通知、工具执行管线、权限/沙箱
- MCP 工具会暴露为 `mcp__<serverName>__<tool>`
- `sdk-minimal` 默认只有 shell、`read_image`、`working_directory`；完整 `sdk`/`web` 才带更多工具

## 能做什么

可以把 `app/mcp_server.py` 挂成 DSH 的 MCP server，让 DSH 调用群伴工具：

```text
DSH
  -> mcp__qunban__list_groups
  -> mcp__qunban__send_message
  -> mcp__qunban__bili_knowledge_search
  -> mcp__qunban__call_interface
```

模型供应商可以直接使用群伴设置里的 Base URL / API Key / model，不依赖 DeepSeek 官方账号。

## 为什么不建议全量替换群聊短回复

1. **延迟不占优**
   群聊回复通常 1–2 句；当前轻量循环只做一次模型请求 + 最多 4 步工具。DSH 每次要走完整 Agent loop、Session/日志、工具管线，短回复会变重。

2. **工具集偏编码**
   DSH 默认带 shell、文件、工作区等 coding 工具。群消息来自外部用户，必须做自定义 profile/patch 关闭 shell/文件工具，只保留群伴 MCP；否则权限面过大。

3. **人设、记忆、B站上下文不是它的主路径**
   当前群聊每轮注入人设、长期记忆、B站最近观点、群成员和上下文。DSH 的 Session 适合长期任务，但每轮动态系统提示/群级人设需要额外 prompt 插件或 `agent.inject()`，不能只靠 `run(prompt)`。

4. **多消息/表情包格式**
   当前群聊原生支持多段文字 + 表情包 + 冷却/限频。DSH 返回最终 assistant 文本，要做 JSON 解析，或者让 DSH 直接调用群伴 `send_message`；前者要约束输出格式，后者要避免最终文本再发一遍。

5. **稳定性和体积**
   developer preview、84MB Windows 运行时、额外的 SDK 进程；DSH 升级/崩溃需要回退到当前引擎。

6. **当前问题不等于 Agent loop 问题**
   刚修复的“答非所问”主要来自：工具参数 `{city}` 未替换、B站知识工具未启用、微信数据库快照过期、发送校验误判。这些问题换 Agent loop 也不会自动消失。

## 推荐架构：双通道

```text
微信群消息
  -> Runtime
      -> fast lane（默认）
           当前 AgentEngine：人设 + 记忆 + B站上下文 + 4 步工具
      -> deep lane（可选）
           DeepSeek Harness SDK
             provider = 群伴设置
             MCP = app/mcp_server.py
             session = 每个群一个 session
             prompt = 群上下文 + 人设 + 记忆 + B站观点
             output = 结构化 JSON（messages/sticker）
```

- fast lane：日常聊天、短回复、快速工具，保留现在已验证的链路。
- deep lane：用户明确要求深度研究、多步工具、长任务，或 Operator/管理员复杂任务。
- 设置项：`group_engine = legacy | dsh`，默认 `legacy`；DSH 失败自动回退 legacy。
- DSH 只挂 MCP，不启用 shell/文件/删除；发送仍走群伴 capability 网关、wechatauto 和待重发队列。
- session id 按群稳定映射；换微信账号/清空对话时重建。
- 用 SDK `RunResult.events/notifications` 推送到前端，显示工具进度。
- 未确认发送、暂停、`Alt+P` 接管等状态仍然由群伴 Runtime 管理，不交给 DSH。

## 建议的 POC（不改生产链路）

1. 建立隔离环境：临时 venv + `data/dsh/` Harness home，不写 `~/.dsh`。
2. 安装 `deepseek-harness-sdk`，使用 `sdk-minimal` 或自定义 profile。
3. 用群伴设置生成 provider 配置；MCP patch 指向 `app/mcp_server.py`。
4. 在测试群上下文里跑两个只读用例，不发送：
   - “查武夷山天气” → 是否调用 `qunban_call_interface`，参数是否为 `city`
   - “你刷视频学到了什么” → 是否调用 `qunban_bili_knowledge_search`
5. 记录：首字延迟、总耗时、工具调用次数、参数正确率、token 成本、失败回退。
6. 达标后再做 `group_engine=dsh` 和 A/B；不达标就只把 DSH 用于 Operator。

## 验收标准

- 普通群聊 P95 延迟不高于当前链路的 1.5 倍
- 工具参数正确率 ≥ 95%
- 不出现重复发送/未确认消息重复
- DSH 进程退出或超时能在 2 秒内回退当前 AgentEngine
- 群聊不会获得 shell/文件/删除权限
