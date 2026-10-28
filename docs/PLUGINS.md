# DSH 插件工作室

「插件」是群伴的顶层选项卡，**只保留一套 DSH 插件目录**：

- **DSH 插件**：Harness 内置插件、群伴原生插件、npm/git/本地导入包、DSH 现场生成的插件。
- **群伴工具**：工作区 skills、MCP 项目、外部接口由 `@qunban/dsh-native` 在 DSH 启动时动态注册成独立 DSH 工具，不再作为一套平行的「群伴能力插件」目录出现在页面里。

## 1. 页面结构

- **统计**：DSH 版本、内置插件数、当前启用数、导入/生成数。
- **DSH 目录**：从 DSH 的 `--dump-config` 读取真实组合结果，不是硬编码。支持关键词、分类和「只看启用」。
- **已安装 DSH 插件**：群伴原生插件、导入包、DSH 生成插件；每行有私聊/群聊两个开关。
- **导入 DSH 插件**：npm 包、GitHub 简写、git URL、本地绝对路径；导入时由 DSH 读 README 做校验和工具拆分。
- **DSH 创造模式**：填写插件名、说明、需求，以及私聊/群聊可用性；让 DSH Operator 写 `lib/index.js`，校验后按通道安装。
- **任务日志**：导入/创建是后台任务，页面每 1.5 秒轮询日志。
- 群伴原生插件卡片显示当前有多少 skills/MCP/接口会注册为 DSH 工具。

## 2. 内置插件从哪来

`GET /api/dsh/plugins` 调用：

```text
node <runtime>/lib/bin.js --profile qunban --dump-config
```

解析最终的 `- id` / `name` / `disabled` 行，再读取运行时 `node_modules/@deepseek-ai/<pkg>/package.json` 的版本和描述。因此升级 DSH 后目录会自动跟着变。

当前 runtime `0.2.1-rc.4` 的组合结果约 **98 个内置插件，87 个启用**。前端按核心、工具、技能、服务、运行时、集成、界面/API 分类。

每个内置插件都有 **私聊 / 群聊** 两个开关。关闭核心插件（category=核心 或关键运行时）时会先弹出风险确认；确认后写入 `plugin-policies.json`。原生 shell、群聊文件/网页/skill 等安全敏感能力会被锁定为对应通道关闭，避免绕过微信网关。

## 3. 群伴工具如何进入 DSH

「群伴能力」不再是一套独立插件目录。`@qunban/dsh-native` 在 DSH profile 启动时调用 `/api/capabilities`，按当前 scope 动态注册：

- `skill` → `qunban_skill_<slug>`
- `mcp` → `qunban_mcp_<slug>`
- `api` → `qunban_interface_<slug>`

工具名、描述、参数 schema 都来自群伴能力注册表；实际执行仍走群伴 HTTP 网关。因此模型在 DSH 里看到的是**独立工具**，不是 `qunban_run_skill(name=...)` 这种通用入口。

- 助手 scope 使用 `enabled_assistant`，群聊 scope 使用 `enabled_group`。
- 高风险能力（屏幕控制等）默认只给助手，群聊关闭。
- 修改能力开关后，需要重启对应 DSH 进程（DSH 面板「重启主链路」或下一次 headless 任务）。
- 群伴能力页仍保留在「助手 → 能力」，用于细粒度开关；插件页不再重复一份目录。

## 4. 导入插件

后端：

```text
POST /api/dsh/plugins/import
{ "spec": "@scope/pkg" | "github:user/repo" | "file:C:\\path" , "enabled": true }
→ { "job": { "id": ... } }
```

- 本地路径：读取 `package.json`，复制到 `data/dsh/home/profiles/qunban/node_modules/<name>`，写入 profile 依赖和 patch 行。
- npm/git：通过 `corepack pnpm add <spec>` 在 profile 目录安装，再把新增依赖挂到 patch。
- **导入审查**：安装前读取 README 和 package 信息，交给 DSH Operator 输出私聊/群聊默认策略、工具拆分和风险摘要。默认 `group=false`；不确定就只允许私聊。
- **功能分离**：策略写入 `plugin-policies.json`；`qunban` profile 只挂载 `private=true` 的插件，`qunban-primary` 只挂载 `group=true` 的插件；工具级 split 通过 `QUNBAN_DENY_TOOLS` + `ctx.tools.restrict` 执行，不改第三方源码。
- 安装后下一次 DSH 任务生效；`qunban-primary` 启动时会同步 qunban profile 的导入/生成插件。
- 导入的插件是任意 JavaScript，和 DSH 同权限执行。只安装可信来源。

> `pnpm` 不是全局命令；群伴使用 Node 自带的 `corepack pnpm`，不要求用户单独安装 pnpm。

## 5. 创造模式

```text
POST /api/dsh/plugins/create
{
  "name": "ticket-helper",
  "description": "点检表助手",
  "requirements": "接收 group_id，生成点检表并返回下载路径",
  "use_agent": true,
  "enabled": true,
  "private": true,
  "group": false
}
```

流程：

1. 在 `data/dsh/studio/<slug>` 生成合法脚手架（`package.json` + `lib/index.js` + `REQUIREMENTS.md`），并在 `qunban.channels` 记录通道要求。
2. `use_agent=true` 时，把目录、需求和「私聊/群聊可用」交给 DSH Operator：
   - DSH 用原生 `write` / `read` / `qunban_powershell` 编写实现；
   - 必须导出 `name`、`inject = ['tools']`、`apply(ctx)`；
   - 通过 `QUNBAN_DSH_RUNTIME_ROOT` 动态 import `@deepseek-ai/dsh-tools`；
   - 工具 schema 显式写 `additionalProperties`，output 必须提供 `render`；
   - **按通道拆分工具**：群聊工具不得包含微信发送、删除文件等高风险能力；
   - 完成后 `node --check lib/index.js` 自检。
3. 后端再次校验 package 名、入口和语法。
4. 复制到 `profiles/qunban/node_modules/@qunban/<slug>`，写 profile 依赖和 patch 行，并写入 `plugin-policies.json` 的私聊/群聊策略。
5. 记录到 `data/dsh/studio/index.json`，页面显示为「DSH 生成」。

`use_agent=false` 时只生成示例工具，适合先占位，之后再让 DSH 改。

### 让插件持续自我拓展

- 在创造模式里用相同插件名再提交一次，会重新生成并覆盖安装，相当于“更新插件”。
- 生成目录保留 `REQUIREMENTS.md`，DSH 下次可以读取需求继续改。
- **Operator 自扩展**：`qunban` profile 暴露 `qunban_plugin_install(path, enabled)`。用户明确要求“做一个插件”时，DSH 可以在 `data/dsh/studio/<slug>` 写文件、`node --check`，再调用该工具安装/更新；后端会再次校验并限制路径必须在 studio 目录下。
- 自动检测能力缺口并发起安装仍然保持关闭：当前由用户需求触发，避免模型在闲聊里自行安装任意代码。

## 6. 安全边界

- 只有用户点击「生成并安装 / 导入」才会安装代码。
- 创建任务使用 `qunban` Operator profile，权限为 `danger-full-access`，因此生成代码等同本机代码执行；不要在不可信需求下勾选“让 DSH 编写”。
- 生成插件会安装到 `qunban`（Operator/管理员私聊）profile，并按 `plugin-policies.json` 决定是否挂载到 `qunban-primary`（群聊）；`qunban-chat` / legacy 群聊仍不能安装或执行任意插件。
- `_preserved_plugin_rows()` 会在每次 DSH 任务重建 profile 时保留用户插件行，避免“下一次任务插件消失”。
- 内置插件和群伴原生插件也有私聊/群聊两个开关；核心/关键插件关闭前会先显示风险提示。
- 通道策略写入 `plugin-policies.json`，profile 重建时重新应用；导入/生成插件的工具级 split 通过 `QUNBAN_DENY_TOOLS` 注入原生插件，由 `ctx.tools.restrict` 执行。

## 7. 代码位置

| 文件 | 职责 |
| --- | --- |
| [`app/services/dsh_plugins.py`](../app/services/dsh_plugins.py) | 目录解析、导入/创建任务、安装、启用/停用、profile patch 合并 |
| [`app/api/dsh_plugins.py`](../app/api/dsh_plugins.py) | REST API |
| [`app/services/dsh_agent.py`](../app/services/dsh_agent.py) | profile 生成时保留用户插件行 |
| [`frontend/index.html`](../frontend/index.html) | 「插件」页 |
| [`frontend/app.js`](../frontend/app.js) | 目录渲染、导入/创建、任务轮询 |
| [`frontend/ui.css`](../frontend/ui.css) | 插件页样式 |
| `data/dsh/studio/` | 生成插件源码和 manifest |

## 8. API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/dsh/plugins` | 目录 + 已安装 + 生成 + 统计 |
| POST | `/api/dsh/plugins/import` | 导入插件，返回 job |
| POST | `/api/dsh/plugins/create` | 创建/更新插件，返回 job |
| POST | `/api/dsh/plugins/install` | 安装已写到 `data/dsh/studio` 的插件（Operator 工具调用） |
| GET | `/api/dsh/plugins/jobs/{id}` | 任务日志与结果 |
| POST | `/api/dsh/plugins/toggle` | 启用/停用非内置插件 |

## 9. 已知限制

- 任务日志只保存在内存，服务重启后丢失；源码和安装结果会保留。
- npm/git 导入依赖网络和 corepack 缓存，首次可能较慢。
- DSH 生成插件是“单文件工具”场景最稳；复杂多文件插件建议在 `data/dsh/studio/<slug>` 里继续手工开发。
- 生成任务和导入任务串行执行（全局锁），避免同时改 profile patch。
