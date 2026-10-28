/**
 * Qunban native DSH plugin.
 *
 * This is the fusion point between DeepSeek Harness and 群伴: the model sees
 * first-class native tools (`wechat_send_message`, `qunban_memory_search`, …)
 * instead of MCP-qualified `mcp__qunban__*` names.  Every call goes through the
 * Qunban HTTP tool gateway, which owns wechatauto-replica, capability scope,
 * send confirmation and the outbox.  The plugin never touches WeChat windows,
 * keyboard or mouse.
 */
import { pathToFileURL } from 'node:url'

const runtimeRoot = process.env.QUNBAN_DSH_RUNTIME_ROOT || ''
if (!runtimeRoot) {
  throw new Error('QUNBAN_DSH_RUNTIME_ROOT is required to load @qunban/dsh-native')
}
const { defineTool } = await import(
  pathToFileURL(`${runtimeRoot}/node_modules/@deepseek-ai/dsh-tools/lib/index.js`).href
)

export const name = 'qunban-native'
export const inject = ['tools']

const API_BASE = (process.env.QUNBAN_API_BASE || 'http://127.0.0.1:8000').replace(/\/+$/, '')
const TOKEN = process.env.QUNBAN_TOKEN || ''
const SCOPE = process.env.QUNBAN_SCOPE === 'group' ? 'group' : 'admin'
const TOOLSET = process.env.QUNBAN_TOOLSET || 'full'
const BOUND_GROUP = Number(process.env.QUNBAN_GROUP_ID || 0)

function textResult(value) {
  return JSON.stringify(value, null, 0)
}

function tool(name, description, parameters, handler) {
  return defineTool({
    name,
    description,
    parameters,
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: String(value) }],
    },
    async execute(args, exec) {
      try {
        return await handler(args || {}, exec)
      } catch (error) {
        return textResult({ ok: false, error: String(error?.message || error) })
      }
    },
  })
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'content-type': 'application/json',
      'x-qunban-token': TOKEN,
      ...(options.headers || {}),
    },
    signal: AbortSignal.timeout(options.timeoutMs || 180000),
  })
  const raw = await response.text()
  let data
  try {
    data = raw ? JSON.parse(raw) : {}
  } catch {
    data = { ok: false, error: raw.slice(0, 2000) }
  }
  if (!response.ok) {
    return { ok: false, error: String(data?.detail || data?.error || raw).slice(0, 2000) }
  }
  return data
}

async function callQunban(name, args = {}) {
  const data = await request('/api/opencode/tool', {
    method: 'POST',
    body: JSON.stringify({ name, args, scope: SCOPE }),
  })
  if (data?.ok === false) {
    throw new Error(String(data?.error || data?.detail || '群伴工具调用失败'))
  }
  return data?.result ?? data
}

function compactGroups(data) {
  const rows = Array.isArray(data) ? data : data?.groups || []
  return rows.slice(0, 60).map((row) => ({
    id: row.id,
    name: row.name || row.remark || '',
    agent_enabled: Boolean(
      row.agent?.agent_enabled ?? row.config?.agent_enabled ?? row.agent_enabled,
    ),
    message_count: row.message_count ?? row.messages ?? 0,
    last_message_at: row.last_message_at || '',
  }))
}

function compactMessages(data) {
  const rows = Array.isArray(data) ? data : data?.messages || []
  return rows.slice(-50).map((row) => ({
    id: row.id,
    sender: row.sender || row.sender_name || '',
    content: String(row.content || '').slice(0, 1500),
    kind: row.kind || 'text',
    is_agent: Boolean(row.is_agent ?? row.is_from_agent),
    created_at: row.created_at || '',
  }))
}

const tools = []

// ---------------------------------------------------------------- WeChat
tools.push(
  tool(
    'wechat_status',
    '检查群伴与微信的连接状态、当前账号、是否已暂停接管。发送前可先调用一次。',
    {},
    async () => {
      const status = await request('/api/status')
      return textResult({
        ok: status.state === 'online',
        state: status.state,
        adapter: status.adapter,
        account: status.account?.name || '',
        detached: Boolean(status.detached),
        message: status.message || '',
        last_error: status.last_error || '',
      })
    },
  ),
  tool(
    'wechat_list_groups',
    '列出群伴已读取的微信群，返回 group_id、群名和 Agent 开关。发送群消息前先用它拿到准确的 group_id；可用 query 按群名筛选。',
    {
      query: { type: 'string', description: '按群名筛选，可选' },
      limit: { type: 'number', description: '返回数量，默认 20，最大 50' },
    },
    async (args) => {
      const rows = compactGroups(await callQunban('list_groups'))
      const needle = String(args.query || '').trim().toLowerCase()
      const filtered = needle
        ? rows.filter((row) =>
            String(row.name || '').toLowerCase().includes(needle),
          )
        : rows
      return textResult(
        filtered.slice(0, Math.max(1, Math.min(50, Number(args.limit || 20)))),
      )
    },
  ),
  tool(
    'wechat_read_group',
    '读取指定群最近消息。group_id 必须是 wechat_list_groups 返回的数字 ID。',
    {
      group_id: { type: 'number', required: true, description: '群伴群 ID' },
      limit: { type: 'number', description: '读取条数，默认 30，最大 200' },
      since_id: { type: 'number', description: '只读取该消息 ID 之后的消息' },
    },
    async (args) => {
      const groupId = Number(args.group_id)
      if (SCOPE === 'group' && BOUND_GROUP && groupId !== BOUND_GROUP) {
        return textResult({ ok: false, error: '群任务只能读取触发它的那个群' })
      }
      const payload = {
        group_id: groupId,
        limit: Math.max(1, Math.min(200, Number(args.limit || 30))),
      }
      if (args.since_id) payload.since_id = Number(args.since_id)
      return textResult(compactMessages(await callQunban('read_group_messages', payload)))
    },
  ),
  tool(
    'wechat_send_message',
    '发送微信消息的唯一入口。群聊传 group_id，私聊传 contact_id，二者只能选一个。文本发送由 wechatauto-replica 完成并校验落库；严禁用键盘、鼠标、PowerShell 或 shell 模拟发送。',
    {
      text: { type: 'string', required: true, description: '要发送的文字' },
      group_id: { type: 'number', description: '群聊 ID（群聊必填）' },
      contact_id: { type: 'number', description: '联系人 ID（私聊必填）' },
      client_msg_id: {
        type: 'string',
        description: '可选的幂等键；同一个任务重试时复用，避免重复发送',
      },
    },
    async (args) => {
      const text = String(args.text || '').trim()
      const groupId = Number(args.group_id || 0)
      const contactId = Number(args.contact_id || 0)
      if (!text) return textResult({ ok: false, error: 'text 不能为空' })
      if (groupId && contactId) {
        return textResult({ ok: false, error: 'group_id 和 contact_id 只能提供一个' })
      }
      if (!groupId && !contactId) {
        return textResult({ ok: false, error: '必须提供 group_id 或 contact_id' })
      }
      if (SCOPE === 'group' && contactId) {
        return textResult({ ok: false, error: '群任务不能给联系人发私聊' })
      }
      if (SCOPE === 'group' && BOUND_GROUP && groupId !== BOUND_GROUP) {
        return textResult({ ok: false, error: '群任务只能回复触发它的那个群' })
      }
      if (text.length > 4000) {
        return textResult({ ok: false, error: 'text 超过 4000 字，请拆分' })
      }
      const name = groupId ? 'send_group_message' : 'send_contact_message'
      const payload = groupId
        ? { group_id: groupId, text }
        : { contact_id: contactId, text }
      if (args.client_msg_id) payload.client_msg_id = String(args.client_msg_id)
      const result = await callQunban(name, payload)
      return textResult({
        ok: result?.ok !== false,
        target: groupId ? { group_id: groupId } : { contact_id: contactId },
        result,
      })
    },
  ),
)

if (SCOPE === 'admin') {
  tools.push(
    tool(
      'wechat_list_contacts',
      '列出微信联系人，返回 contact_id、昵称、是否管理员。给联系人发私聊前用它拿 contact_id。',
      {
        limit: { type: 'number', description: '返回数量，默认 50' },
        query: { type: 'string', description: '按昵称/备注过滤' },
      },
      async (args) => {
        const data = await request('/api/contacts')
        const needle = String(args.query || '').trim().toLowerCase()
        const rows = (data?.contacts || [])
          .filter((row) => {
            if (!needle) return true
            return `${row.name || ''} ${row.remark || ''} ${row.wxid || ''}`
              .toLowerCase()
              .includes(needle)
          })
          .slice(0, Math.max(1, Math.min(200, Number(args.limit || 50))))
          .map((row) => ({
            contact_id: row.id,
            name: row.name || row.remark || row.wxid,
            wxid: row.wxid,
            is_admin: Boolean(row.is_admin),
          }))
        return textResult(rows)
      },
    ),
  )
}

// ---------------------------------------------------------------- memory
tools.push(
  tool(
    'qunban_memory_search',
    '搜索群伴长期记忆。回答“之前说过/记得吗/上次”等问题前先搜索。',
    {
      query: { type: 'string', required: true, description: '检索关键词或原句' },
      limit: { type: 'number', description: '默认 8 条' },
    },
    async (args) => {
      const data = await callQunban('search_memories', {
        query: String(args.query || ''),
        limit: Math.max(1, Math.min(30, Number(args.limit || 8))),
      })
      const rows = Array.isArray(data) ? data : data?.memories || []
      return textResult(
        rows.slice(0, 30).map((row) => ({
          title: row.title || '',
          content: String(row.content || '').slice(0, 1200),
          category: row.category || '',
          importance: row.importance ?? 3,
        })),
      )
    },
  ),
  tool(
    'qunban_memory_save',
    '保存一条长期记忆。只保存以后仍然有用的事实、偏好或约定。',
    {
      content: { type: 'string', required: true, description: '记忆正文' },
      title: { type: 'string', description: '简短标题' },
      category: { type: 'string', description: '分类，默认 operator' },
      importance: { type: 'number', description: '1-5，默认 3' },
    },
    async (args) =>
      textResult(
        await callQunban('save_memory', {
          content: String(args.content || ''),
          title: String(args.title || ''),
          category: String(args.category || 'operator'),
          importance: Math.max(1, Math.min(5, Number(args.importance || 3))),
        }),
      ),
  ),
)

// ---------------------------------------------------------------- Bilibili
tools.push(
  tool(
    'qunban_bili_search',
    '搜索 B站视频，返回 bvid、标题、作者、时长和链接。',
    {
      keyword: { type: 'string', required: true, description: '搜索关键词' },
      limit: { type: 'number', description: '默认 10' },
    },
    async (args) => {
      const data = await callQunban('bili_search', {
        keyword: String(args.keyword || ''),
        limit: Math.max(1, Math.min(20, Number(args.limit || 10))),
      })
      const rows = Array.isArray(data) ? data : data?.videos || []
      return textResult(rows.slice(0, 20))
    },
  ),
  tool(
    'qunban_bili_knowledge',
    '检索群伴 B站知识库中已经看过的视频内容。',
    {
      query: { type: 'string', required: true, description: '检索关键词' },
      limit: { type: 'number', description: '默认 5' },
    },
    async (args) => {
      const data = await callQunban('bili_knowledge_search', {
        query: String(args.query || ''),
        limit: Math.max(1, Math.min(20, Number(args.limit || 5))),
      })
      const rows = Array.isArray(data) ? data : data?.results || []
      return textResult(rows.slice(0, 20))
    },
  ),
)

// ---------------------------------------------------------------- interfaces
tools.push(
  tool(
    'qunban_call_interface',
    '调用群伴已登记的 HTTP 接口（例如天气、内部系统）。必须使用接口定义里的参数名。',
    {
      name: { type: 'string', required: true, description: '接口名' },
      args: { type: 'object', additionalProperties: true, description: '接口参数对象' },
    },
    async (args) =>
      textResult(
        await callQunban('call_interface', {
          name: String(args.name || ''),
          args: args.args && typeof args.args === 'object' ? args.args : {},
        }),
      ),
  ),
)

if (SCOPE === 'group') {
  tools.push(
    tool(
      'qunban_create_excel',
      '生成 Excel 表格并自动发送到当前群。传 sheets，每张表第一行是表头。',
      {
        sheets: { type: 'array', required: true, description: '表格定义数组', items: { type: 'object', additionalProperties: true } },
        filename: { type: 'string', description: '文件名，可选' },
      },
      async (args) =>
        textResult(
          await callQunban('create_excel', {
            sheets: args.sheets,
            filename: args.filename || '',
          }),
        ),
    ),
  )
}

if (SCOPE === 'admin') {
  tools.push(
    tool(
      'qunban_run_skill',
      '运行群伴技能（data/agent_workspace/skills 下的技能）。',
      {
        name: { type: 'string', required: true, description: '技能名' },
        args: { type: 'string', description: '传给技能脚本的参数' },
      },
      async (args) =>
        textResult(
          await callQunban('run_skill', {
            name: String(args.name || ''),
            args: String(args.args || ''),
          }),
        ),
    ),
    tool(
      'qunban_run_mcp',
      '运行群伴已启用的 MCP 项目（例如 screen_control_mcp）。',
      {
        name: { type: 'string', required: true, description: 'MCP 项目名' },
        command: { type: 'string', description: '可选命令；留空使用 manifest' },
        timeout: { type: 'number', description: '超时秒数，默认 120' },
      },
      async (args) =>
        textResult(
          await callQunban('run_mcp', {
            name: String(args.name || ''),
            command: String(args.command || ''),
            timeout: Math.max(5, Math.min(600, Number(args.timeout || 120))),
          }),
        ),
    ),
    tool(
      'qunban_plugin_install',
      '安装/更新已经写到 data/dsh/studio/<slug> 的 DSH 插件。仅当用户明确要求新增或修改插件时使用；安装前会校验 package.json、lib/index.js 和 node --check。',
      {
        path: {
          type: 'string',
          required: true,
          description: '插件目录，必须是 data/dsh/studio 下的绝对路径',
        },
        enabled: {
          type: 'boolean',
          description: '安装后是否立即启用，默认 true',
        },
      },
      async (args) => {
        const data = await request('/api/dsh/plugins/install', {
          method: 'POST',
          body: JSON.stringify({
            path: String(args.path || ''),
            enabled: args.enabled !== false,
          }),
        })
        return textResult(data)
      },
    ),
    tool(
      'qunban_powershell',
      '在 Windows 上执行 PowerShell。禁止用于操作微信窗口或模拟发送；微信消息只能用 wechat_send_message。',
      {
        command: { type: 'string', required: true, description: 'PowerShell 命令' },
        admin: { type: 'boolean', description: '是否请求管理员权限' },
      },
      async (args) =>
        textResult(
          await callQunban('run_powershell', {
            command: String(args.command || ''),
            admin: Boolean(args.admin),
          }),
        ),
    ),
  )
}

async function loadCapabilityTools(attempt = 0) {
  try {
    const data = await request('/api/capabilities', { timeoutMs: 2500 })
    const rows = Array.isArray(data?.capabilities) ? data.capabilities : []
    const out = []
    for (const row of rows) {
      const allowed =
        SCOPE === 'group' ? Boolean(row.enabled_group) : Boolean(row.enabled_assistant)
      if (!allowed) continue
      const kind = String(row.type || '')
      const rawName =
        kind === 'mcp'
          ? String(row.key || '').replace(/^mcp:/, '')
          : String(row.name || row.key || '')
      const slug = rawName
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '')
      if (!slug) continue
      const prefix = kind === 'skill' ? 'qunban_skill' : kind === 'mcp' ? 'qunban_mcp' : 'qunban_interface'
      const toolName = `${prefix}_${slug}`.slice(0, 64)
      const description = String(row.description || row.name || '').slice(0, 240)
      if (kind === 'skill') {
        out.push(
          tool(
            toolName,
            `${description}（群伴技能，参数是传给技能脚本的字符串）`,
            {
              args: { type: 'string', description: '传给技能脚本的参数' },
            },
            async (args) =>
              textResult(
                await callQunban('run_skill', {
                  name: rawName,
                  args: String(args.args || ''),
                }),
              ),
          ),
        )
      } else if (kind === 'mcp') {
        out.push(
          tool(
            toolName,
            `${description}（群伴 MCP 项目）`,
            {
              timeout: { type: 'number', description: '超时秒数，默认 120' },
            },
            async (args) =>
              textResult(
                await callQunban('run_mcp', {
                  name: rawName,
                  timeout: Math.max(5, Math.min(600, Number(args.timeout || 120))),
                }),
              ),
          ),
        )
      } else if (kind === 'api') {
        out.push(
          tool(
            toolName,
            `${description}（群伴 HTTP 接口）`,
            {
              args: { type: 'object', additionalProperties: true, description: '接口参数对象' },
            },
            async (args) =>
              textResult(
                await callQunban('call_interface', {
                  name: rawName,
                  args: args.args && typeof args.args === 'object' ? args.args : {},
                }),
              ),
          ),
        )
      }
    }
    return out
  } catch {
    if (attempt < 2) {
      await new Promise((resolve) => setTimeout(resolve, 800))
      return loadCapabilityTools(attempt + 1)
    }
    return []
  }
}

const capabilityTools = await loadCapabilityTools()

const DENY_TOOLS = String(process.env.QUNBAN_DENY_TOOLS || '')
  .split(',')
  .map((item) => item.trim())
  .filter(Boolean)

export function apply(ctx) {
  if (DENY_TOOLS.length && typeof ctx.tools?.restrict === 'function') {
    ctx.tools.restrict({ deny: DENY_TOOLS })
  }
  for (const item of [...tools, ...capabilityTools]) {
    if (TOOLSET === 'primary' && item.name === 'wechat_send_message') continue
    ctx.tools.register(item)
  }
}
