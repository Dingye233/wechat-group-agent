/**
 * Clash Verge control tools for Qunban's DSH Agent.
 *
 * Clash Verge exposes mihomo's REST API over a Windows named pipe by default
 * (`external-controller-pipe` in clash-verge.yaml).  A named pipe accepts
 * plain HTTP, so this plugin talks to the local controller without enabling
 * the TCP controller or changing the user's proxy configuration.
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const runtimeRoot = process.env.QUNBAN_DSH_RUNTIME_ROOT || ''
if (!runtimeRoot) {
  throw new Error('QUNBAN_DSH_RUNTIME_ROOT is required to load @qunban/clash-verge')
}
const { defineTool } = await import(
  pathToFileURL(`${runtimeRoot}/node_modules/@deepseek-ai/dsh-tools/lib/index.js`).href
)

export const name = 'clash-verge'
export const inject = ['tools']

const SCOPE = process.env.QUNBAN_SCOPE === 'group' ? 'group' : 'admin'
const HOME = process.env.CLASH_VERGE_HOME || path.join(
  process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'),
  'io.github.clash-verge-rev.clash-verge-rev',
)
const EXE_CANDIDATES = [
  process.env.CLASH_VERGE_EXE || '',
  'D:\\Clash Verge\\clash-verge.exe',
  path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Clash Verge', 'clash-verge.exe'),
].filter(Boolean)

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
    async execute(args) {
      try {
        return await handler(args || {})
      } catch (error) {
        return textResult({ ok: false, error: String(error?.message || error) })
      }
    },
  })
}

function yamlField(text, key) {
  const match = String(text || '').match(new RegExp(`^${key}:\\s*(.*)$`, 'm'))
  if (!match) return ''
  let value = match[1].trim()
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    value = value.slice(1, -1)
  }
  return value
}

function clashConfig() {
  const files = ['clash-verge.yaml', 'config.yaml']
  let text = ''
  for (const file of files) {
    try {
      text = fs.readFileSync(path.join(HOME, file), 'utf8')
      if (text) break
    } catch {
      // try next file
    }
  }
  if (!text) {
    throw new Error(`找不到 Clash Verge 配置：${HOME}`)
  }
  const pipe = yamlField(text, 'external-controller-pipe')
  const controller = yamlField(text, 'external-controller')
  const secret = yamlField(text, 'secret')
  if (!pipe && !controller) {
    throw new Error('Clash Verge 没有配置 external-controller-pipe 或 external-controller')
  }
  return { pipe, controller, secret, home: HOME }
}

function parseHttpResponse(raw) {
  const marker = raw.indexOf(Buffer.from('\r\n\r\n'))
  if (marker < 0) throw new Error('Clash API 返回了非 HTTP 响应')
  const headerText = raw.subarray(0, marker).toString('utf8')
  let bodyBuffer = raw.subarray(marker + 4)
  const lines = headerText.split('\r\n')
  const statusLine = lines[0] || ''
  const status = Number(statusLine.split(' ')[1] || 0)
  const headers = {}
  for (const line of lines.slice(1)) {
    const index = line.indexOf(':')
    if (index > 0) headers[line.slice(0, index).trim().toLowerCase()] = line.slice(index + 1).trim()
  }
  if (String(headers['transfer-encoding'] || '').toLowerCase().includes('chunked')) {
    const parts = []
    let rest = bodyBuffer
    while (rest.length) {
      const end = rest.indexOf(Buffer.from('\r\n'))
      if (end < 0) break
      const size = parseInt(rest.subarray(0, end).toString('ascii'), 16)
      if (!Number.isFinite(size) || size <= 0) break
      parts.push(rest.subarray(end + 2, end + 2 + size))
      rest = rest.subarray(end + 2 + size + 2)
    }
    bodyBuffer = Buffer.concat(parts)
  } else if (headers['content-length']) {
    const expected = Number(headers['content-length'])
    if (Number.isFinite(expected) && bodyBuffer.length > expected) {
      bodyBuffer = bodyBuffer.subarray(0, expected)
    }
  }
  const body = bodyBuffer.toString('utf8')
  let data = body
  const contentType = String(headers['content-type'] || '')
  if (contentType.includes('json') || /^[\[{]/.test(body.trim())) {
    try {
      data = JSON.parse(body)
    } catch {
      // keep text
    }
  }
  return { status, data }
}

function pipeRequest(pipe, method, apiPath, body, secret) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ path: pipe })
    const chunks = []
    let settled = false
    const finish = (error, value) => {
      if (settled) return
      settled = true
      socket.destroy()
      if (error) reject(error)
      else resolve(value)
    }
    socket.setTimeout(10000, () => finish(new Error('Clash named pipe 请求超时')))
    socket.on('connect', () => {
      const payload = body === undefined || body === null ? '' : JSON.stringify(body)
      const headers = [
        `${method} ${apiPath} HTTP/1.1`,
        'Host: localhost',
        'Accept: application/json',
        'Connection: close',
      ]
      if (secret) headers.push(`Authorization: Bearer ${secret}`)
      if (payload) {
        headers.push('Content-Type: application/json', `Content-Length: ${Buffer.byteLength(payload)}`)
      }
      socket.write(`${headers.join('\r\n')}\r\n\r\n${payload}`)
    })
    socket.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
    socket.on('error', (error) => finish(error))
    socket.on('end', () => {
      try {
        const parsed = parseHttpResponse(Buffer.concat(chunks))
        if (parsed.status >= 400) {
          throw new Error(`Clash API ${parsed.status}: ${JSON.stringify(parsed.data).slice(0, 500)}`)
        }
        finish(null, parsed.data)
      } catch (error) {
        finish(error)
      }
    })
  })
}

async function clashRequest(method, apiPath, body) {
  const config = clashConfig()
  if (config.pipe) {
    return pipeRequest(config.pipe, method, apiPath, body, config.secret)
  }
  const headers = { accept: 'application/json' }
  if (config.secret) headers.authorization = `Bearer ${config.secret}`
  if (body !== undefined) headers['content-type'] = 'application/json'
  const response = await fetch(`http://${config.controller}${apiPath}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(10000),
  })
  const text = await response.text()
  let data = text
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    // keep text
  }
  if (!response.ok) {
    throw new Error(`Clash API ${response.status}: ${String(text).slice(0, 500)}`)
  }
  return data
}

function assertAdmin(action) {
  if (SCOPE === 'group') {
    throw new Error(`${action} 只允许管理员私聊调用，群聊场景为只读`)
  }
}

function compactProxies(data, requestedGroup = '') {
  const groups = []
  const entries = data?.proxies && typeof data.proxies === 'object' ? data.proxies : {}
  for (const [groupName, item] of Object.entries(entries)) {
    if (!item || !Array.isArray(item.all)) continue
    if (requestedGroup && groupName !== requestedGroup) continue
    groups.push({
      name: groupName,
      type: item.type || '',
      now: item.now || '',
      nodes: item.all.slice(0, 30),
      node_count: item.all.length,
    })
    if (groups.length >= 40) break
  }
  return groups
}

const tools = []

tools.push(
  tool(
    'clash_verge_status',
    '检查 Clash Verge / mihomo 是否在运行，返回核心版本、代理模式、监听端口和当前控制器。',
    {},
    async () => {
      const [version, configs] = await Promise.all([
        clashRequest('GET', '/version'),
        clashRequest('GET', '/configs'),
      ])
      const cfg = clashConfig()
      return textResult({
        ok: true,
        version: version?.version || version?.meta?.version || '',
        mode: configs?.mode || '',
        mixed_port: configs?.['mixed-port'] ?? null,
        allow_lan: Boolean(configs?.['allow-lan']),
        controller: cfg.pipe ? 'named-pipe' : cfg.controller,
        secret_set: Boolean(cfg.secret),
      })
    },
  ),
  tool(
    'clash_verge_proxies',
    '列出 Clash Verge 的代理分组、当前选中节点和可选节点。可用 group 精确查看一个分组。',
    {
      group: { type: 'string', description: '可选：代理分组名，例如 节点选择' },
    },
    async (args) => {
      const data = await clashRequest('GET', '/proxies')
      const group = String(args.group || '').trim()
      return textResult({ ok: true, groups: compactProxies(data, group) })
    },
  ),
  tool(
    'clash_verge_select_proxy',
    '在指定 Clash Verge 代理分组中切换节点。切换前先用 clash_verge_proxies 确认分组名和节点名。',
    {
      group: { type: 'string', required: true, description: '代理分组名' },
      name: { type: 'string', required: true, description: '要切换到的节点名' },
    },
    async (args) => {
      assertAdmin('切换代理节点')
      const group = String(args.group || '').trim()
      const name = String(args.name || '').trim()
      if (!group || !name) throw new Error('group 和 name 不能为空')
      await clashRequest('PUT', `/proxies/${encodeURIComponent(group)}`, { name })
      return textResult({ ok: true, group, selected: name })
    },
  ),
  tool(
    'clash_verge_set_mode',
    '切换 Clash Verge 代理模式：rule（规则）、global（全局）、direct（直连）。',
    {
      mode: { type: 'string', required: true, enum: ['rule', 'global', 'direct'], description: '代理模式' },
    },
    async (args) => {
      assertAdmin('切换代理模式')
      const mode = String(args.mode || '').trim()
      if (!['rule', 'global', 'direct'].includes(mode)) {
        throw new Error('mode 必须是 rule、global 或 direct')
      }
      await clashRequest('PATCH', '/configs', { mode })
      return textResult({ ok: true, mode })
    },
  ),
  tool(
    'clash_verge_delay',
    '测试一个 Clash Verge 节点的延迟（毫秒）。',
    {
      name: { type: 'string', required: true, description: '节点名' },
      url: { type: 'string', description: '测速 URL，默认 gstatic generate_204' },
      timeout: { type: 'number', description: '超时毫秒，默认 5000，最大 10000' },
    },
    async (args) => {
      const name = String(args.name || '').trim()
      if (!name) throw new Error('name 不能为空')
      const url = String(args.url || 'http://www.gstatic.com/generate_204')
      const timeout = Math.max(1000, Math.min(10000, Number(args.timeout || 5000)))
      const data = await clashRequest(
        'GET',
        `/proxies/${encodeURIComponent(name)}/delay?timeout=${timeout}&url=${encodeURIComponent(url)}`,
      )
      return textResult({ ok: true, name, delay: data?.delay ?? null })
    },
  ),
  tool(
    'clash_verge_start',
    '启动 Clash Verge（如果已经在运行则不重复启动），随后检查核心是否可连接。',
    {},
    async () => {
      assertAdmin('启动 Clash Verge')
      try {
        const version = await clashRequest('GET', '/version')
        return textResult({ ok: true, already_running: true, version: version?.version || '' })
      } catch {
        // not running or controller not ready; start it
      }
      const exe = EXE_CANDIDATES.find((item) => item && fs.existsSync(item))
      if (!exe) throw new Error('找不到 clash-verge.exe')
      const child = spawn(exe, [], {
        detached: true,
        windowsHide: false,
        stdio: 'ignore',
      })
      child.unref()
      for (let i = 0; i < 20; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000))
        try {
          const version = await clashRequest('GET', '/version')
          return textResult({ ok: true, started: true, version: version?.version || '' })
        } catch {
          // keep waiting for the named pipe
        }
      }
      throw new Error('Clash Verge 已启动，但控制器在 20 秒内没有就绪')
    },
  ),
)

export { tools }
export function apply(ctx) {
  for (const item of tools) ctx.tools.register(item)
}
