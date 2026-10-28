# Tool architecture

群伴的 OpenCode 工具不再堆在一个文件里，而是由独立组件组合：

```text
app/mcp_server.py           # 薄 MCP 入口，只负责创建 server + 组合组件
app/tools/wechat.py         # 群列表、群消息、发送消息
app/tools/bili.py           # B站搜索、知识库
app/tools/memory.py         # 长期记忆
app/tools/system.py         # 通用网关、MCP/技能/接口、tool_admin、原生 manifest
app/tools/common.py         # 共享 HTTP 网关和结果压缩
app/tools/__init__.py       # register_all()
```

每个组件只通过 `register(server)` 注册自己的工具，互不直接依赖。OpenCode
仍然只需要连接一个本地 MCP 入口，工具名和行为保持兼容；以后可以把某个组件
拆成独立 MCP server，而不改动其他组件。

## 原生工具 manifest

`data/opencode/tools.json` 保存原生工具定义。OpenCode 通过
`qunban_tool_admin` 增删/改描述/启停/同步。脚本工具放在
`data/opencode/tools/`，`handler_type="script"`，从 stdin 读取 JSON 参数。

## 微信发送与群名校验

- 群/联系人查找只使用显示名，不使用 `@chatroom` 原始 ID 作为搜索词。
- `wechatauto` 打开目标会话后，会校验解析出的 wxid 是否与数据库目标一致；
  同名群打开错目标时直接取消发送。
- 发送前必须确认输入框可用；确认不了就取消，不会盲打键盘。
- `keyboard_*` / `mouse_*` 默认不注册给 OpenCode；包含微信关键词的
  `run_powershell` 也会被拦截。
