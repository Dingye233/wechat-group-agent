# 群聊工具调用开关

## 入口

群聊聊天页顶部：

```text
读取 [开关]  回复 [开关]  工具 [开关]
```

也可以在右侧「Agent 设置」里使用：

```text
允许工具调用
```

关闭后：

```text
仍然可以读取消息
仍然可以做普通聊天回复
不进入任务链路
不调用 DSH 工具
不触发关键词任务
不生成群任务
```

## 字段

`GroupConfig.tools_enabled`，默认 `true`。

API：

```http
PATCH /api/groups/{group_id}/config
Content-Type: application/json

{"tools_enabled": false}
```

## 后端执行顺序

```text
读取配置
→ tools_enabled=false
→ Router 强制 lane=chat
→ 跳过 DSH Router
→ 跳过 DSH primary（primary 有工具）
→ 走 DSH chat lane（toolset=none）
→ 如果 chat lane 不可用，legacy plan 强制：
   need_tools=false
   tools=[]
   delegate_to_opencode=false
```

额外保护：

- `_handle_task_route` 直接拒绝
- `_handle_dsh_primary` 直接返回 False
- `_generate_llm` 强制 `force_tools=False`

## 注意

开关只阻止新消息进入工具/任务流程。  
已经创建的历史任务不会自动取消，需要到任务页单独停止。
