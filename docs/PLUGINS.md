# DSH 插件工作室

「插件」是群伴新增的顶层选项卡，用来查看 DeepSeek Harness 的内置插件、导入外部插件，以及让 DSH 按需求现场写一个群伴插件。

## 1. 页面结构

- **统计**：DSH 版本、内置插件数、当前启用数、导入/生成数。
- **目录**：从 DSH 的 `--dump-config` 读取真实组合结果，不是硬编码。支持关键词、分类和「只看启用」。
- **已安装**：群伴原生插件、导入包、DSH 生成插件；非内置项可以启用/停用。
- **导入插件**：npm 包、GitHub 简写、git URL、本地绝对路径。
- **创造模式**：填写插件名、说明和需求，让 DSH Operator 写 `lib/index.js`，校验后安装。
- **任务日志**：导入/创建是后台任务，页面每 1.5 秒轮询日志。

## 2. 内置插件从哪来

`GET /api/dsh/plugins` 调用：

```text
node <runtime>/lib/bin.js --profile qunban --dump-config
```

解析最终的 `- id` / `name` / `disabled` 行，再读取运行时 `node_modules/@deepseek-ai/<pkg>/package.json` 的版本和描述。因此升级 DSH 后目录会自动跟着变。

当前 runtime `0.2.1-rc.4` 的组合结果约 **98 个内置插件，86 个启用**。前端按核心、工具、技能、服务、运行时、集成、界面/API 分类。

内置 DSH 插件在群伴里是**只读**的：不建议从网页停用核心插件。需要高级 profile 调整时直接改 `cordis.patch.yml`。

## 3. 导入插件

后端：

```text
POST /api/dsh/plugins/import
{ "spec": "@scope/pkg" | "github:user/repo" | "file:C:\\path" , "enabled": true }
→ { "job": { "id": ... } }
```

- 本地路径：读取 `package.json`，复制到 `data/dsh/home/profiles/qunban/node_modules/<name>`，写入 profile 依赖和 patch 行。
- npm/git：通过 `corepack pnpm add <spec>` 在 profile 目录安装，再把新增依赖挂到 patch。
- 安装后下一次 DSH 任务生效；不需要重启常驻进程，因为 DSH 是每个任务启动一次。
- 导入的插件是任意 JavaScript，和 DSH 同权限执行。只安装可信来源。

> `pnpm` 不是全局命令；群伴使用 Node 自带的 `corepack pnpm`，不要求用户单独安装 pnpm。

## 4. 创造模式

```text
POST /api/dsh/plugins/create
{
  "name": "ticket-helper",
  "description": "点检表助手",
  "requirements": "接收 group_id，生成点检表并返回下载路径",
  "use_agent": true,
  "enabled": true
}
```

流程：

1. 在 `data/dsh/studio/<slug>` 生成合法脚手架（`package.json` + `lib/index.js` + `REQUIREMENTS.md`）。
2. `use_agent=true` 时，把目录和需求交给 DSH Operator：
   - DSH 用原生 `write` / `read` / `qunban_powershell` 编写实现；
   - 必须导出 `name`、`inject = ['tools']`、`apply(ctx)`；
   - 通过 `QUNBAN_DSH_RUNTIME_ROOT` 动态 import `@deepseek-ai/dsh-tools`；
   - 工具 schema 显式写 `additionalProperties`，output 必须提供 `render`；
   - 完成后 `node --check lib/index.js` 自检。
3. 后端再次校验 package 名、入口和语法。
4. 复制到 `profiles/qunban/node_modules/@qunban/<slug>`，写 profile 依赖和 patch 行。
5. 记录到 `data/dsh/studio/index.json`，页面显示为「DSH 生成」。

`use_agent=false` 时只生成示例工具，适合先占位，之后再让 DSH 改。

### 让插件持续自我拓展

- 在创造模式里用相同插件名再提交一次，会重新生成并覆盖安装，相当于“更新插件”。
- 生成目录保留 `REQUIREMENTS.md`，DSH 下次可以读取需求继续改。
- **Operator 自扩展**：`qunban` profile 暴露 `qunban_plugin_install(path, enabled)`。用户明确要求“做一个插件”时，DSH 可以在 `data/dsh/studio/<slug>` 写文件、`node --check`，再调用该工具安装/更新；后端会再次校验并限制路径必须在 studio 目录下。
- 自动检测能力缺口并发起安装仍然保持关闭：当前由用户需求触发，避免模型在闲聊里自行安装任意代码。

## 5. 安全边界

- 只有用户点击「生成并安装 / 导入」才会安装代码。
- 创建任务使用 `qunban` Operator profile，权限为 `danger-full-access`，因此生成代码等同本机代码执行；不要在不可信需求下勾选“让 DSH 编写”。
- 生成插件只挂到 `qunban`（Operator/管理员私聊）profile，不进入 `qunban-group` / `qunban-chat`；群聊仍不能安装或执行任意插件。
- `_preserved_plugin_rows()` 会在每次 DSH 任务重建 profile 时保留用户插件行，避免“下一次任务插件消失”。
- 内置插件只读；群伴原生插件 `@qunban/dsh-native` 固定挂载。

## 6. 代码位置

| 文件 | 职责 |
| --- | --- |
| [`app/services/dsh_plugins.py`](../app/services/dsh_plugins.py) | 目录解析、导入/创建任务、安装、启用/停用、profile patch 合并 |
| [`app/api/dsh_plugins.py`](../app/api/dsh_plugins.py) | REST API |
| [`app/services/dsh_agent.py`](../app/services/dsh_agent.py) | profile 生成时保留用户插件行 |
| [`frontend/index.html`](../frontend/index.html) | 「插件」页 |
| [`frontend/app.js`](../frontend/app.js) | 目录渲染、导入/创建、任务轮询 |
| [`frontend/ui.css`](../frontend/ui.css) | 插件页样式 |
| `data/dsh/studio/` | 生成插件源码和 manifest |

## 7. API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/dsh/plugins` | 目录 + 已安装 + 生成 + 统计 |
| POST | `/api/dsh/plugins/import` | 导入插件，返回 job |
| POST | `/api/dsh/plugins/create` | 创建/更新插件，返回 job |
| POST | `/api/dsh/plugins/install` | 安装已写到 `data/dsh/studio` 的插件（Operator 工具调用） |
| GET | `/api/dsh/plugins/jobs/{id}` | 任务日志与结果 |
| POST | `/api/dsh/plugins/toggle` | 启用/停用非内置插件 |

## 8. 已知限制

- 任务日志只保存在内存，服务重启后丢失；源码和安装结果会保留。
- npm/git 导入依赖网络和 corepack 缓存，首次可能较慢。
- DSH 生成插件是“单文件工具”场景最稳；复杂多文件插件建议在 `data/dsh/studio/<slug>` 里继续手工开发。
- 生成任务和导入任务串行执行（全局锁），避免同时改 profile patch。
