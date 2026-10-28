'use strict';

/* ============================================================
   群伴 · WeChat Group Agent — frontend
   Vanilla JS, no build step. All API calls are same-origin.
   ============================================================ */

const ICONS = {
  logo: `<path d="M5 7a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v5a4 4 0 0 1-4 4h-3.6L7 19.7V16a4 4 0 0 1-2-3.5Z" fill="currentColor" stroke="none"/><circle cx="9.4" cy="9.6" r="1.1" fill="#07130e"/><circle cx="14.6" cy="9.6" r="1.1" fill="#07130e"/><path d="M9.3 12.8c.7.65 1.65.98 2.7.98s2-.33 2.7-.98" fill="none" stroke="#07130e" stroke-width="1.4" stroke-linecap="round"/>`,
  plug: `<path d="M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0V8Z"/><path d="M12 17v5"/>`,
  chat: `<path d="M21 11.5a8.4 8.4 0 0 1-12.3 7.4L4 20l1.2-4A8.4 8.4 0 1 1 21 11.5Z"/><path d="M8 10h8M8 13.5h5"/>`,
  smile: `<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c.9 1.35 2.1 2 3.5 2s2.6-.65 3.5-2"/><path d="M9 9.5h.01M15 9.5h.01"/>`,
  settings: `<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.9 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 8.9 4.6 1.7 1.7 0 0 0 9.93 3.04V3a2 2 0 1 1 4 0v.09A1.7 1.7 0 0 0 15 4.6a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 8.9c.15.64.7 1.1 1.56 1.1H21a2 2 0 1 1 0 4h-.09c-.8 0-1.36.4-1.51 1Z"/>`,
  refresh: `<path d="M20 11a8 8 0 1 0-2.3 6.1"/><path d="M20 4v7h-7"/>`,
  search: `<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>`,
  users: `<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>`,
  sliders: `<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M1 14h6M9 8h6M17 16h6"/>`,
  close: `<path d="M18 6 6 18M6 6l12 12"/>`,
  logout: `<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>`,
  plus: `<path d="M12 5v14M5 12h14"/>`,
  upload: `<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5-5 5 5M12 5v12"/>`,
  shield: `<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>`,
  info: `<circle cx="12" cy="12" r="9"/><path d="M12 16v-5M12 8h.01"/>`,
  sparkles: `<path d="m12 3 1.4 4.1L17.5 8.5l-4.1 1.4L12 14l-1.4-4.1-4.1-1.4 4.1-1.4Z"/><path d="m19 14 .8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8Z"/><path d="m5 15 .6 1.7L7.3 17l-1.7.6L5 19l-.6-1.4L2.7 17l1.7-.3Z"/>`,
  layers: `<path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/>`,
  qr: `<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v3M14 20h3M20 20h1"/>`,
  lock: `<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>`,
  check: `<path d="m5 12 4 4L19 6"/>`,
  play: `<path d="m8 5 11 7-11 7Z"/>`,
  wechat: `<path d="M7.5 4C4.46 4 2 6.1 2 8.7c0 1.5.8 2.85 2.06 3.72L3.5 14.5l2.2-1.05c.57.16 1.18.25 1.8.25.25 0 .5-.01.74-.04"/><path d="M22 13.3c0-2.4-2.24-4.3-5-4.3s-5 1.9-5 4.3 2.24 4.3 5 4.3c.6 0 1.17-.08 1.7-.23L20.5 18.5l-.55-1.75A4.2 4.2 0 0 0 22 13.3Z"/><path d="M5.5 7.5h.01M9.5 7.5h.01M14.5 12.5h.01M18 12.5h.01"/>`,
  trash: `<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>`,
  edit: `<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>`,
  send: `<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>`,
  image: `<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>`,
  alert: `<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>`,
  pin: `<path d="M9 4h6l-1 6 4 3v2H6v-2l4-3-1-6Z"/><path d="M12 15v6"/>`,
};

function icon(name, size = 18) {
  const body = ICONS[name] || ICONS.chat;
  return `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  status: null,
  settings: null,
  groups: [],
  selectedGroupId: null,
  sidebarMode: 'groups',
  contacts: [],
  selectedContactId: null,
  contactMessages: [],
  messages: [],
  members: [],
  logs: [],
  stickers: [],
  stickerTags: [],
  stickerFilter: 'all',
  groupQuery: '',
  stickerQuery: '',
  plugins: null,
  pluginQuery: '',
  pluginCategory: '全部',
  pluginActiveOnly: false,
  pluginJobId: '',
  pluginJobTimer: null,
  toolMoments: [],
  toolLabels: [],
  toolContacts: [],
  assistantHistory: [],
  assistantProgressId: '',
  assistantProgressLines: [],
  assistantSkills: [],
  assistantMemories: [],
  assistantTasks: [],
  assistantCapabilities: [],
  assistantPanel: 'workspace',
  assistantBusy: false,
  assistantAttachment: null,
  assistantContacts: [],
  assistantContactId: null,
  assistantContactQuery: '',
  assistantConversation: 'main',
  assistantConversationContactId: null,
  biliVideos: [],
  biliAccount: null,
  biliAuto: null,
  biliAutoLog: [],
  biliQrTimer: null,
  biliPasswordTimer: null,
  biliPasswordSession: '',
  chatTasks: [],
  opencodeTasks: [],
  opencodeTaskTimer: null,
  detached: false,
  detachReason: '',
  unread: new Map(),
  seenMessageIds: new Set(),
  hasMoreMessages: false,
  loadingMore: false,
  ws: null,
  wsConnected: false,
  wsAttempts: 0,
  wsTimer: null,
  loginPoll: null,
  qrPoll: null,
  loginPending: false,
  selectedAdapter: 'replica',
  pinnedGroupId: null,
  appReady: false,
  entering: false,
  typingTimer: null,
  currentTab: 'chats',
  stats: null,
  uploadFile: null,
  uploadObjectUrl: null,
  groupsReloadTimer: null,
  lastMessagesReload: 0,
  catalogSignature: '',
};

const ADAPTER_FALLBACK = [
  { id: 'mock', name: '演示模式', description: '无需微信，直接体验。', available: true, reason: '' },
  { id: 'replica', name: '微信 PC 4.x（wechatauto-replica）', description: '读取本地数据库，后台实时收消息。', available: false, reason: '仅支持 Windows 64 位 + 微信 4.1.12+' },
  { id: 'wcf', name: '微信 PC（WeChatFerry）', description: 'WeChatFerry 协议接入。', available: false, reason: '仅支持 Windows' },
];

/* ------------------------------------------------------------ basic helpers */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function initial(name) {
  const text = String(name || '').trim();
  return Array.from(text)[0] || '?';
}

function avatarStyle(name) {
  let hash = 0;
  for (const ch of String(name || '?')) {
    hash = (hash * 31 + (ch.codePointAt(0) || 0)) >>> 0;
  }
  const hue = 105 + (hash % 150);
  return `linear-gradient(135deg, hsl(${hue} 46% 26%), hsl(${(hue + 24) % 360} 52% 45%))`;
}

function paintAvatar(node, name) {
  if (!node) return;
  node.textContent = initial(name);
  node.style.background = avatarStyle(name);
}

function truncate(text, max) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function parseIntSafe(value, fallback = 0) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseFloatSafe(value, fallback = 0) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function toDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatClock(value) {
  const date = toDate(value);
  if (!date) return '';
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function formatRelative(value) {
  const date = toDate(value);
  if (!date) return '';
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return formatClock(date);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return '昨天';
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}/${date.getDate()}`;
  }
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
}

function dayKey(value) {
  const date = toDate(value);
  if (!date) return 'unknown';
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function dayLabel(value) {
  const date = toDate(value);
  if (!date) return '';
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return '今天';
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return '昨天';
  const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()];
  return `${date.getMonth() + 1}月${date.getDate()}日 ${week}`;
}

function setRangeFill(input) {
  if (!input) return;
  const min = Number(input.min || 0);
  const max = Number(input.max || 100);
  const value = Number(input.value || min);
  const percent = max === min ? 100 : ((value - min) / (max - min)) * 100;
  input.style.setProperty('--range', `${percent}%`);
}

function toast(message, type = 'info', timeout = 3600) {
  const root = $('#toast-root');
  if (!root) return;
  while (root.children.length >= 4) root.firstElementChild.remove();
  const node = el('div', `toast ${type}`);
  node.textContent = String(message || '');
  root.appendChild(node);
  window.setTimeout(() => {
    node.classList.add('leaving');
    window.setTimeout(() => node.remove(), 220);
  }, timeout);
}

async function api(path, options = {}) {
  const opts = { ...options };
  const isForm = typeof FormData !== 'undefined' && opts.body instanceof FormData;
  opts.headers = {
    ...(isForm ? {} : { 'Content-Type': 'application/json' }),
    ...(opts.headers || {}),
  };
  const response = await fetch(`/api${path}`, opts);
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      if (typeof data.detail === 'string') {
        detail = data.detail;
      } else if (Array.isArray(data.detail)) {
        detail = data.detail.map((item) => item.msg || item.message || String(item)).join('；');
      }
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function initIcons() {
  $$('[data-icon]').forEach((node) => {
    const name = node.getAttribute('data-icon');
    const size = node.classList.contains('brand-mark') ? 23 : 18;
    node.innerHTML = icon(name, size);
  });
}

function setIcon(selector, name, size = 18) {
  const node = $(selector);
  if (node) node.innerHTML = icon(name, size);
}

/* ------------------------------------------------------------ boot */
document.addEventListener('DOMContentLoaded', async () => {
  initIcons();
  bindEvents();
  bindPluginEvents();
  buildImageViewer();
  const savedAdapter = localStorage.getItem('wechat-agent-adapter');
  if (['mock', 'replica', 'uia', 'wcf'].includes(savedAdapter)) state.selectedAdapter = savedAdapter;
  connectWS();
  const status = await refreshStatus();
  if (!status) {
    showLogin();
    setLoginError('无法连接后端服务，请确认服务已启动。');
    return;
  }
  if (status.state === 'online') {
    await enterApp();
  } else {
    showLogin();
    startLoginPolling();
  }
});

/* ------------------------------------------------------------ status / login */
async function refreshStatus(silent = false) {
  try {
    const data = await api('/status');
    applyStatus(data);
    return data;
  } catch (error) {
    if (!silent) console.warn('status error', error);
    renderTopbarStatus();
    return null;
  }
}

function applyDetachedState(detached, reason = '') {
  const next = Boolean(detached);
  state.detached = next;
  state.detachReason = reason || '';
  const reasonNode = $('#detached-reason');
  if (reasonNode) {
    reasonNode.textContent = next
      ? `${state.detachReason || '自动操作已暂停'}。`
      : '';
  }
  const dialog = $('#detached-dialog');
  if (next && state.appReady && dialog && !dialog.open) {
    try {
      dialog.showModal();
    } catch {
      /* already open */
    }
  } else if (!next && dialog?.open) {
    dialog.close();
  }
  updateComposerState();
  renderTopbarStatus();
  const toolsPill = $('#tools-adapter');
  if (toolsPill && next) {
    toolsPill.dataset.state = 'error';
    const span = toolsPill.querySelector('span');
    if (span) span.textContent = '已脱离 · 等待接管';
  }
}

async function reattachAgent() {
  const button = $('#reattach-btn');
  if (button) {
    button.disabled = true;
    button.textContent = '正在重新接管…';
  }
  try {
    const data = await api('/control/reattach', { method: 'POST' });
    if (data?.status) applyStatus(data.status);
    else applyDetachedState(false);
    toast(
      data?.outbox_retry_scheduled
        ? '已重新接管，正在校验并重发未发送消息'
        : '已重新接管，微信 4.x 自动操作继续',
    );
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = '重新接管';
    }
  }
}

function applyStatus(data) {
  if (!data) return;
  const previousState = state.status?.state;
  const previousAdapter = state.selectedAdapter;
  const previousAccountKey = state.status?.account?.wxid || '';
  state.status = data;
  applyDetachedState(
    data.state === 'online' && Boolean(data.detached),
    data.detach_reason || '',
  );
  const nextAccountKey = data.account?.wxid || '';
  if (previousAccountKey && nextAccountKey && previousAccountKey !== nextAccountKey) {
    resetForAccountSwitch();
  }
  if (data.adapter && ['starting', 'qr', 'online'].includes(data.state)) {
    state.selectedAdapter = data.adapter;
    try { localStorage.setItem('wechat-agent-adapter', state.selectedAdapter); } catch { /* ignore */ }
  }
  const nextPinned = data.pinned_group_id || null;
  if (nextPinned !== state.pinnedGroupId) {
    state.pinnedGroupId = nextPinned;
    renderGroupList();
    renderChatHeader();
  }
  renderAccount();
  renderTopbarStatus();
  renderSidebarStatus();
  updateComposerState();
  if ($('#login-view') && !$('#login-view').hidden) {
    updateLoginUI();
    if (previousAdapter !== state.selectedAdapter) renderAdapters();
  }
  if (data.state === 'qr') {
    refreshQrImage();
  }
  if (data.state === 'online' && previousState && previousState !== 'online' && state.appReady) {
    loadGroups().catch(() => {});
    reloadSelectedMessages().catch(() => {});
  }
  if (data.state === 'online' && !state.appReady && !state.entering) {
    enterApp();
  }
  const catalogSignature = (data.available_adapters || [])
    .map((item) => `${item.id}:${item.available}`)
    .join(',');
  if (!state.appReady && catalogSignature && catalogSignature !== state.catalogSignature) {
    state.catalogSignature = catalogSignature;
    renderAdapters();
  }
}

async function enterApp() {
  if (state.entering || state.appReady) return;
  state.entering = true;
  const loginView = $('#login-view');
  const mainView = $('#main-view');
  loginView.hidden = true;
  mainView.hidden = false;
  stopLoginPolling();
  try {
    await Promise.all([loadGroups(), loadStickers().catch(() => {}), loadSettings().catch(() => {})]);
    state.appReady = true;
    applyDetachedState(
      Boolean(state.status?.detached),
      state.status?.detach_reason || '',
    );
    const route = routeFromHash();
    if (!state.selectedGroupId && state.groups.length) {
      await selectGroup(state.groups[0].id);
    }
    if (route.tab === 'assistant' && route.panel) {
      switchAssistantPanel(route.panel, { syncHash: false });
    }
    showTab(route.tab, { syncHash: false });
    renderAccount();
    renderTopbarStatus();
    renderSidebarStatus();
    updateComposerState();
  } catch (error) {
    toast(error.message || '加载工作台失败', 'error');
    state.appReady = true;
  } finally {
    state.entering = false;
  }
}

function showLogin() {
  state.appReady = false;
  state.entering = false;
  const loginView = $('#login-view');
  const mainView = $('#main-view');
  if (loginView) loginView.hidden = false;
  if (mainView) mainView.hidden = true;
  renderAdapters();
  updateLoginUI();
  startLoginPolling();
}

function renderAdapters() {
  const container = $('#login-adapters');
  if (!container) return;
  const adapters = state.status?.available_adapters?.length
    ? state.status.available_adapters
    : ADAPTER_FALLBACK;
  if (!adapters.some((item) => item.id === state.selectedAdapter && item.available)) {
    const firstAvailable =
      adapters.find((item) => item.id === 'replica' && item.available) ||
      adapters.find((item) => item.id === 'uia' && item.available) ||
      adapters.find((item) => item.id === 'wcf' && item.available) ||
      adapters.find((item) => item.available);
    if (firstAvailable) state.selectedAdapter = firstAvailable.id;
  }
  container.replaceChildren();
  for (const adapter of adapters) {
    const button = el('button', `adapter-option${adapter.id === state.selectedAdapter ? ' active' : ''}`);
    button.type = 'button';
    button.dataset.adapter = adapter.id;
    button.disabled = !adapter.available;
    const iconBox = el('span', 'adapter-icon');
    iconBox.innerHTML = icon(
      adapter.id === 'replica' || adapter.id === 'uia' || adapter.id === 'wcf'
        ? 'wechat'
        : 'play',
      16,
    );
    const body = el('span');
    body.appendChild(el('strong', null, adapter.name));
    body.appendChild(el('small', null, adapter.available ? adapter.description : adapter.reason || '不可用'));
    const check = el('span', 'adapter-check');
    check.innerHTML = icon('check', 10);
    button.append(iconBox, body, check);
    container.appendChild(button);
  }
  const selected = adapters.find((item) => item.id === state.selectedAdapter);
  const help = $('#login-adapter-help');
  if (help) {
    help.textContent = state.selectedAdapter === 'replica'
      ? '推荐：读取微信 4.1.12+ 本地数据库，微信在后台/最小化也能实时收消息；发送时短暂唤起微信，检测到你操作鼠标会自动暂停。'
      : state.selectedAdapter === 'uia'
        ? '读取微信 3.9 客户端界面，无需注入；扫码后请保持微信窗口打开，发送消息时会短暂切换到微信。'
        : state.selectedAdapter === 'wcf'
          ? '需要 Windows 微信 3.9.12.51 与 WeChatFerry；二维码来自微信登录窗口截图。'
          : '演示模式无需微信账号，几秒后自动确认登录，适合先体验完整流程。';
  }
  const badge = $('#qr-badge');
  if (badge && state.status?.state === 'qr') {
    badge.textContent = state.selectedAdapter === 'mock' ? '演示二维码 · 自动确认' : '请用手机微信扫码';
    badge.hidden = false;
  }
  const note = $('#wcf-note');
  if (note) {
    const unavailable = adapters.find(
      (item) => item.id === state.selectedAdapter && !item.available,
    );
    if (unavailable) {
      note.hidden = false;
      note.replaceChildren();
      note.appendChild(el('strong', null, '当前接入方式暂未就绪'));
      note.appendChild(el('p', null, unavailable.reason || '当前环境不满足该适配器的运行条件。'));
      note.appendChild(
        el(
          'small',
          null,
          '推荐使用微信 4.x 适配器：Windows 10/11 64 位 + 微信 4.1.12 以上。旧版界面自动化需要微信 3.9.x，WeChatFerry 仅支持 3.9.12.51。安装依赖后重启服务；演示模式不受影响。',
        ),
      );
    } else {
      note.hidden = true;
    }
  }
  void selected;
}

function updateLoginUI() {
  const status = state.status;
  const stateName = status?.state || 'offline';
  const pill = $('#login-status-pill');
  if (pill) {
    pill.dataset.state = stateName;
    const text = pill.querySelector('span');
    if (text) {
      text.textContent = {
        offline: '未连接',
        starting: '正在启动',
        qr: '等待扫码',
        online: '已连接',
        error: '连接失败',
      }[stateName] || stateName;
    }
  }
  const title = $('#login-state-title');
  const copy = $('#login-state-text');
  const btn = $('#login-btn');
  const errorBox = $('#login-error');
  const qrImage = $('#qr-image');
  const placeholder = $('#qr-placeholder');
  const badge = $('#qr-badge');
  const stateCopy = {
    offline: '选择一种接入方式，然后开始登录。',
    starting: '微信或演示环境正在启动，请稍候…',
    qr: state.selectedAdapter === 'mock'
      ? '演示模式会自动确认登录，正在准备控制台…'
      : '请用手机微信扫描左侧二维码。登录成功后会自动进入控制台。',
    online: '连接成功，正在进入工作台…',
    error: '登录失败，请根据下方提示处理后重试。',
  };
  const titles = {
    offline: '尚未连接',
    starting: '正在启动…',
    qr: '用手机微信扫码',
    online: '连接成功',
    error: '连接失败',
  };
  if (title) title.textContent = titles[stateName] || '尚未连接';
  if (copy) {
    const message = stateName === 'offline' ? '' : status?.message;
    copy.textContent = message || stateCopy[stateName] || '';
  }
  if (errorBox) {
    if (stateName === 'error') {
      errorBox.hidden = false;
      errorBox.textContent = status?.last_error || status?.message || '未知错误';
    } else {
      errorBox.hidden = true;
    }
  }
  if (btn) {
    btn.hidden = stateName === 'online';
    btn.disabled = state.loginPending || ['starting', 'qr'].includes(stateName);
    btn.textContent = state.loginPending || stateName === 'starting'
      ? '正在连接…'
      : stateName === 'qr'
        ? '等待扫码…'
        : stateName === 'error'
          ? '重新尝试'
          : '开始扫码登录';
  }
  const showQr = stateName === 'qr' && status?.has_qr;
  if (qrImage && placeholder && badge) {
    if (showQr) {
      placeholder.hidden = true;
      qrImage.hidden = false;
      badge.hidden = false;
      badge.textContent = state.selectedAdapter === 'mock' ? '演示二维码 · 自动确认' : '请用手机微信扫码';
      if (!qrImage.getAttribute('src')) refreshQrImage(true);
    } else {
      qrImage.hidden = true;
      qrImage.removeAttribute('src');
      placeholder.hidden = false;
      badge.hidden = true;
      placeholder.innerHTML = `${icon('qr', 48)}<small>${
        stateName === 'starting' ? '正在生成登录二维码…' : '二维码将在这里出现'
      }</small>`;
    }
  }
  if (stateName === 'qr' && status?.has_qr) refreshQrImage();
}

function refreshQrImage(force = false) {
  const status = state.status;
  if (!status || status.state !== 'qr' || !status.has_qr) return;
  const img = $('#qr-image');
  if (!img) return;
  const version = `${status.qr_version || 0}`;
  if (!force && img.dataset.version === version && img.dataset.ts) {
    const age = Date.now() - Number(img.dataset.ts);
    if (age < 2500) return;
  }
  img.dataset.version = version;
  img.dataset.ts = String(Date.now());
  img.src = `/api/login/qr?t=${Date.now()}`;
  img.hidden = false;
  const placeholder = $('#qr-placeholder');
  if (placeholder) placeholder.hidden = true;
  const badge = $('#qr-badge');
  if (badge) {
    badge.hidden = false;
    badge.textContent = state.selectedAdapter === 'mock' ? '演示二维码 · 自动确认' : '请用手机微信扫码';
  }
}

function setLoginError(message) {
  const box = $('#login-error');
  if (!box) return;
  box.hidden = false;
  box.textContent = message;
}

async function startLogin() {
  if (state.loginPending) return;
  if (state.status && ['starting', 'qr'].includes(state.status.state)) return;
  state.loginPending = true;
  updateLoginUI();
  try {
    const loginResult = await api('/login', {
      method: 'POST',
      body: JSON.stringify({ adapter: state.selectedAdapter }),
    });
    if (loginResult?.status) applyStatus(loginResult.status);
    startLoginPolling();
    window.setTimeout(() => { if (!state.appReady) refreshStatus(true); }, 500);
    window.setTimeout(() => { if (!state.appReady) refreshStatus(true); }, 1500);
  } catch (error) {
    setLoginError(error.message);
    toast(error.message, 'error', 5000);
  } finally {
    state.loginPending = false;
    updateLoginUI();
  }
}

async function logout() {
  if (!window.confirm('退出后 Agent 将停止自动回复，后台实时连接也会断开。确定退出吗？')) return;
  const wasReady = state.appReady;
  try {
    await api('/logout', { method: 'POST' });
    resetAppState();
    showLogin();
    await refreshStatus(true);
    toast('已退出登录');
  } catch (error) {
    toast(error.message, 'error');
    if (!wasReady) showLogin();
  }
}

function resetAppState() {
  state.appReady = false;
  state.selectedGroupId = null;
  state.messages = [];
  state.members = [];
  state.logs = [];
  state.groups = [];
  state.detached = false;
  state.detachReason = '';
  state.hasMoreMessages = false;
  state.loadingMore = false;
  updateLoadMoreButton();
  state.unread.clear();
  state.groupsReloadTimer = null;
  state.stats = null;
  if (state.status) {
    state.status = { ...state.status, state: 'offline', account: null, has_qr: false, message: '已退出登录' };
  }
  hideTyping();
  closeStickerPopover();
  $('#members-dialog')?.close();
  $('#detached-dialog')?.close();
  $('#inspector')?.classList.remove('open');
  $('#main-view').hidden = true;
}

function startLoginPolling() {
  stopLoginPolling();
  state.loginPoll = window.setInterval(async () => {
    if (state.appReady) return;
    const data = await refreshStatus(true);
    if (data?.state === 'online') {
      stopLoginPolling();
      enterApp();
    }
  }, 1000);
  state.qrPoll = window.setInterval(() => {
    if (state.status?.state === 'qr') refreshQrImage();
  }, 2000);
}

function stopLoginPolling() {
  if (state.loginPoll) window.clearInterval(state.loginPoll);
  if (state.qrPoll) window.clearInterval(state.qrPoll);
  state.loginPoll = null;
  state.qrPoll = null;
}

/* ------------------------------------------------------------ topbar */
function renderAccount() {
  const account = state.status?.account;
  const name = account?.name || '微信助手';
  const avatar = $('#account-avatar');
  const label = $('#account-name');
  if (avatar) paintAvatar(avatar, name);
  if (label) label.textContent = account?.name || (state.status?.state === 'online' ? '微信助手' : '未登录');
}

function resetForAccountSwitch() {
  state.groups = [];
  state.selectedGroupId = null;
  state.messages = [];
  state.members = [];
  state.logs = [];
  state.unread.clear();
  state.seenMessageIds.clear();
  state.pinnedGroupId = null;
  state.detached = false;
  state.detachReason = '';
  state.hasMoreMessages = false;
  const membersDialog = $('#members-dialog');
  if (membersDialog?.open) membersDialog.close();
  $('#detached-dialog')?.close();
  renderGroupList();
  renderChatHeader();
  syncWorkspaceLayout(Boolean(state.selectedGroupId));
  if (state.appReady) loadGroups().catch(() => {});
}

function renderTopbarStatus() {
  const status = state.status;
  const pill = $('#ws-pill');
  const wsText = $('#ws-text');
  if (pill) {
    pill.classList.toggle('connected', state.wsConnected);
    pill.classList.toggle('disconnected', !state.wsConnected);
  }
  if (wsText) wsText.textContent = state.wsConnected ? '实时已连接' : '实时连接中断';
  const settingsPill = $('#settings-adapter');
  if (settingsPill) {
    const stateName = status?.state || 'offline';
    settingsPill.dataset.state = stateName;
    const span = settingsPill.querySelector('span');
    if (span) {
      const adapter = state.status?.adapter === 'replica'
        ? '微信 PC 4.x · 本地数据库'
        : state.status?.adapter === 'uia'
          ? '微信 PC 3.9 · 界面自动化'
          : state.status?.adapter === 'wcf'
            ? '微信 PC · WeChatFerry'
            : state.status?.adapter === 'mock'
              ? '演示模式'
              : 'Adapter';
      span.textContent = `${adapter} · ${
        { offline: '未连接', starting: '启动中', qr: '待扫码', online: '运行中', error: '异常' }[stateName] || stateName
      }`;
    }
  }
  const settingsState = $('#settings-state');
  if (settingsState) settingsState.textContent = status?.state || 'offline';
  const settingsAccount = $('#settings-account');
  if (settingsAccount) settingsAccount.textContent = status?.account?.name || '未登录';
  const toolsPill = $('#tools-adapter');
  if (toolsPill) {
    toolsPill.dataset.state = status?.state || 'offline';
    const span = toolsPill.querySelector('span');
    if (span) {
      span.textContent = state.status?.detached
        ? '已脱离 · 等待接管'
        : status?.adapter === 'replica'
          ? '微信 4.x · 已连接'
          : '仅微信 4.x 适配器可用';
    }
  }
}

function renderSidebarStatus() {
  const dot = $('#sidebar-status-dot');
  const text = $('#sidebar-status-text');
  const sub = $('#sidebar-status-sub');
  const status = state.status;
  const stateName = status?.state || 'offline';
  if (dot) dot.className = `status-dot ${stateName}`;
  if (text) {
    text.textContent = {
      online: 'Agent 运行中',
      qr: '等待扫码登录',
      starting: '正在启动适配器',
      error: '连接异常',
      offline: '未连接',
    }[stateName] || stateName;
  }
  if (sub) {
    if (stateName === 'online') {
      sub.textContent = `${status?.account?.name || '微信助手'} · ${state.groups.length} 个群`;
    } else if (stateName === 'error') {
      sub.textContent = truncate(status?.last_error || '请查看设置', 34);
    } else {
      sub.textContent = status?.adapter === 'replica'
        ? '本地数据库 · 微信 PC 4.x'
        : status?.adapter === 'uia'
          ? '界面自动化 · 微信 PC 3.9'
          : status?.adapter === 'wcf'
            ? 'WeChatFerry · 微信 PC'
            : '演示适配器';
    }
  }
}

function updateComposerState() {
  const online = state.status?.state === 'online' && state.status?.account;
  const attached = Boolean(online) && !state.detached;
  const wrap = $('.composer-wrap');
  const input = $('#composer-input');
  const send = $('#send-btn');
  const stickerBtn = $('#sticker-pick-btn');
  const attachmentBtn = $('#attachment-btn');
  if (wrap) wrap.classList.toggle('offline', !attached);
  if (input) {
    input.disabled = !attached;
    input.placeholder = state.detached
      ? '群伴已脱离控制，请先点击「重新接管」'
      : online
        ? '以账号身份发送消息…（Enter 发送，Shift + Enter 换行）'
        : '登录微信后可发送消息';
  }
  if (send) send.disabled = !attached;
  if (stickerBtn) stickerBtn.disabled = !attached;
  if (attachmentBtn) attachmentBtn.disabled = !attached;
  const offlineText = $('#composer-offline');
  if (offlineText) {
    offlineText.textContent = state.detached
      ? '检测到你正在操作鼠标/键盘，自动读取和回复已暂停'
      : online
        ? '消息会实时同步到微信群'
        : '当前连接不可用，消息发送已暂停';
  }
}

/* ------------------------------------------------------------ groups */
async function loadGroups() {
  try {
    const data = await api('/groups');
    applyGroups(data.groups || []);
  } catch (error) {
    console.warn('loadGroups', error);
    throw error;
  }
}

function applyGroups(groups) {
  state.groups = groups;
  sortGroups();
  if (state.selectedGroupId && !groups.some((group) => group.id === state.selectedGroupId)) {
    state.selectedGroupId = null;
    state.messages = [];
    state.members = [];
    state.logs = [];
    renderMessages(true);
    renderChatHeader();
    renderInspector(null);
  }
  renderGroupList();
  renderSidebarStatus();
  if (state.appReady && !state.selectedGroupId && state.groups.length) {
    selectGroup(state.groups[0].id).catch((error) => console.warn('auto select', error));
  }
  if (state.selectedGroupId) {
    const current = currentGroup();
    if (current) {
      renderChatHeader();
      // Do not overwrite the inspector form while the user is editing it.
      if (!document.activeElement?.closest?.('#inspector')) renderInspector(current);
    }
  }
}

function currentGroup() {
  return state.groups.find((group) => group.id === state.selectedGroupId) || null;
}

function sortGroups() {
  state.groups.sort((a, b) => {
    const ta = toDate(a.last_message_at)?.getTime() || 0;
    const tb = toDate(b.last_message_at)?.getTime() || 0;
    return tb - ta;
  });
}

function previewOf(group) {
  const last = group.last_message;
  if (!last) return '还没有消息';
  let content = last.content || '';
  if (last.kind === 'sticker') content = '[表情包]';
  else if (last.kind === 'image') content = '[图片]';
  else if (last.kind === 'voice') content = '[语音]';
  else if (last.kind === 'video') content = '[视频]';
  else if (!content) content = '[消息]';
  const prefix = last.is_from_agent ? 'Agent' : last.sender_name || '';
  return prefix ? `${prefix}：${content}` : content;
}

function setSidebarMode(mode) {
  state.sidebarMode = mode === 'contacts' ? 'contacts' : 'groups';
  $$('#sidebar-mode button').forEach((button) => {
    button.classList.toggle('active', button.dataset.sidebarMode === state.sidebarMode);
  });
  const search = $('#group-search');
  if (search) {
    search.placeholder = state.sidebarMode === 'contacts' ? '搜索联系人' : '搜索群聊';
    search.value = state.groupQuery;
  }
  if (state.sidebarMode === 'contacts') {
    if (state.contacts.length) renderContactList();
    loadContacts().catch((error) => toast(error.message, 'error'));
  } else {
    renderGroupList();
  }
}

async function loadContacts() {
  try {
    const data = await api('/contacts');
    state.contacts = data.contacts || [];
    renderContactList();
  } catch (error) {
    console.warn('contacts', error);
    throw error;
  }
}

function currentContact() {
  return state.contacts.find((item) => item.id === state.selectedContactId) || null;
}

function renderContactList() {
  // An in-flight contact sync must not overwrite the group list after the
  // user has already switched back to 群聊.
  if (state.sidebarMode !== 'contacts') return;
  const container = $('#group-list');
  if (!container) return;
  const query = state.groupQuery.trim().toLowerCase();
  const contacts = state.contacts.filter((item) => {
    if (!query) return true;
    return [item.name, item.remark, item.wxid].some((value) =>
      String(value || '').toLowerCase().includes(query),
    );
  });
  container.replaceChildren();
  if (!contacts.length) {
    container.appendChild(
      el(
        'div',
        'group-list-empty',
        state.contacts.length ? '没有匹配的联系人' : '暂无联系人\n点击右上角刷新',
      ),
    );
    return;
  }
  for (const contact of contacts) {
    const button = el('div', `group-item contact-item${contact.id === state.selectedContactId ? ' active' : ''}`);
    button.setAttribute('role', 'button');
    button.tabIndex = 0;
    button.dataset.contactId = String(contact.id);
    button.addEventListener('keydown', (event) => {
      if (event.target.closest('.contact-mini-switch')) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openContactChat(contact.id).catch((error) => toast(error.message, 'error'));
      }
    });
    const avatar = el('span', 'avatar');
    paintAvatar(avatar, contact.name);
    const main = el('div', 'contact-item-main');
    const top = el('div', 'group-item-top');
    top.appendChild(el('strong', null, contact.name || contact.wxid));
    if (contact.is_admin) top.appendChild(el('span', 'contact-admin-badge', '管理员'));
    const preview = contact.last_message
      ? `${contact.last_message.is_self ? '我：' : ''}${contact.last_message.content || '[消息]'}`
      : contact.remark || contact.wxid;
    main.append(top, el('small', null, truncate(preview, 32)));
    const admin = el('label', 'contact-mini-switch');
    admin.title = contact.is_admin ? '取消管理员' : '设为管理员';
    const checkbox = el('input');
    checkbox.type = 'checkbox';
    checkbox.checked = Boolean(contact.is_admin);
    admin.appendChild(checkbox);
    // The switch lives inside the contact row. Handle it here and stop the
    // click from bubbling, otherwise opening the contact dialog re-renders
    // the row before the checkbox can issue its PATCH.
    admin.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const next = !Boolean(contact.is_admin);
      checkbox.checked = next;
      toggleContactAdmin(contact.id, next);
    });
    button.append(avatar, main, admin);
    container.appendChild(button);
  }
}

async function toggleContactAdmin(contactId, enabled) {
  try {
    const data = await api(`/contacts/${contactId}`, {
      method: 'PATCH',
      body: JSON.stringify({ is_admin: enabled }),
    });
    const contact = state.contacts.find((item) => item.id === contactId);
    if (contact && data.contact) Object.assign(contact, data.contact);
    const assistantContact = state.assistantContacts.find((item) => item.id === contactId);
    if (assistantContact && data.contact) Object.assign(assistantContact, data.contact);
    renderContactList();
    const target = currentContact();
    if (target) renderContactHeader();
    toast(enabled ? '已设为管理员：可直接与 Agent 助手对话' : '已取消管理员');
  } catch (error) {
    toast(error.message, 'error');
    loadContacts().catch(() => {});
  }
}

function renderContactHeader() {
  const contact = currentContact();
  if (!contact) return;
  paintAvatar($('#contact-avatar'), contact.name);
  $('#contact-name').textContent = contact.name || contact.wxid;
  $('#contact-meta').textContent = contact.is_admin
    ? '管理员 · 私聊消息由 Agent 直接处理'
    : '普通联系人 · 可手动发送';
  const toggle = $('#contact-admin-toggle');
  if (toggle) toggle.checked = Boolean(contact.is_admin);
}

function contactMessageNode(message) {
  const wrapper = el('div', `contact-msg${message.is_self ? ' self' : ''}`);
  const meta = el('div', 'contact-msg-meta');
  const source = messageSourceLabel(message);
  meta.textContent = `${message.is_self ? '我' : message.sender_name || '联系人'} · ${formatRelative(
    message.created_at || (message.timestamp ? new Date(message.timestamp * 1000) : null),
  )}${source ? ` · ${source}` : ''}`;
  const bubble = el('div', `contact-msg-bubble${message.kind === 'sticker' ? ' sticker' : ''}`);
  if (message.kind === 'sticker') {
    bubble.textContent = '表情包';
  } else if (message.kind === 'image' && message.media_url) {
    const img = el('img');
    img.src = message.media_url;
    img.alt = message.content || '图片';
    img.loading = 'lazy';
    img.addEventListener('click', () => openImageViewer(message.media_url, message.content));
    img.addEventListener('error', () => {
      img.replaceWith(el('span', 'placeholder-media', message.content || '[图片]'));
    });
    bubble.appendChild(img);
  } else {
    bubble.textContent = message.content || (message.kind === 'image' ? '[图片]' : '…');
  }
  wrapper.append(meta, bubble);
  return wrapper;
}

function renderContactMessages() {
  const roots = [$('#contact-messages'), $('#assistant-contact-messages')].filter(Boolean);
  if (!roots.length) return;
  for (const root of roots) {
    root.replaceChildren();
    if (!state.contactMessages.length) {
      root.appendChild(el('div', 'contact-empty', '还没有读取到私聊消息。'));
      continue;
    }
    for (const message of state.contactMessages) {
      root.appendChild(contactMessageNode(message));
    }
    root.scrollTop = root.scrollHeight;
  }
}

async function openContactChat(contactId) {
  state.selectedContactId = contactId;
  renderContactList();
  renderContactHeader();
  const dialog = $('#contact-chat-dialog');
  if (dialog && !dialog.open) dialog.showModal();
  await loadContactMessages(contactId);
}

async function loadContactMessages(contactId = state.selectedContactId) {
  if (!contactId) return;
  try {
    const data = await api(`/contacts/${contactId}/messages?limit=120`);
    if (state.selectedContactId !== contactId) return;
    state.contactMessages = data.messages || [];
    renderContactMessages();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function sendContactMessage() {
  const contact = currentContact();
  const input = $('#contact-input');
  const text = input?.value.trim() || '';
  if (!contact || !text) return;
  const button = $('#contact-send');
  if (button) button.disabled = true;
  try {
    await api(`/contacts/${contact.id}/send`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    input.value = '';
    await loadContactMessages(contact.id);
    await loadContacts();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

function messageSourceLabel(message) {
  const source = String(message?.meta?.source || '');
  if (source === 'dashboard' || source === 'assistant') return '本地';
  if (source === 'admin-agent' || source === 'llm') return '微信 Agent';
  if (
    source === 'replica' ||
    source === 'replica-dm' ||
    source === 'uia' ||
    source === 'uia-session'
  )
    return '微信';
  if (message?.is_from_agent) return '微信 Agent';
  return '';
}

function renderGroupList() {
  if (state.sidebarMode === 'contacts') {
    renderContactList();
    return;
  }
  const container = $('#group-list');
  if (!container) return;
  const query = state.groupQuery.trim().toLowerCase();
  const groups = state.groups.filter((group) => !query || String(group.name || '').toLowerCase().includes(query));
  container.replaceChildren();
  if (!groups.length) {
    const empty = el('div', 'group-list-empty');
    empty.append(
      el('strong', null, state.groups.length ? '没有匹配的群聊' : '正在同步群聊'),
      el(
        'p',
        null,
        state.groups.length
          ? '换个关键词试试。'
          : '群列表会在登录后自动出现；也可以手动刷新。',
      ),
    );
    if (!state.groups.length) {
      const retry = el('button', 'btn btn-ghost btn-sm', '刷新群列表');
      retry.type = 'button';
      retry.addEventListener('click', () => loadGroups());
      empty.append(retry);
    }
    container.append(empty);
    return;
  }
  for (const group of groups) {
    const button = el('button', `group-item${group.id === state.selectedGroupId ? ' active' : ''}`);
    button.type = 'button';
    button.dataset.groupId = String(group.id);
    const avatar = el('span', 'avatar');
    paintAvatar(avatar, group.name);
    const main = el('div', 'group-item-main');
    const top = el('div', 'group-item-top');
    top.appendChild(el('span', 'group-item-name', group.name || '未命名群聊'));
    if (group.id === state.pinnedGroupId) {
      top.appendChild(el('span', 'group-pin-flag', '置顶'));
    }
    if (state.unread.has(group.id)) top.appendChild(el('span', 'group-unread'));
    top.appendChild(el('span', 'group-item-time', formatRelative(group.last_message_at)));
    const preview = el('div', 'group-item-preview', previewOf(group));
    const bottom = el('div', 'group-item-preview-row');
    bottom.style.display = 'flex';
    bottom.style.alignItems = 'center';
    bottom.style.gap = '5px';
    const readFlag = el(
      'span',
      `group-flag read${group.agent?.read_enabled ? ' on' : ''}`,
      '读取',
    );
    readFlag.title = group.agent?.read_enabled ? '正在读取本群消息' : '未读取本群消息';
    const replyFlag = el(
      'span',
      `group-flag reply${group.agent?.read_enabled && group.agent?.agent_enabled ? ' on' : ''}`,
      '回复',
    );
    replyFlag.title = group.agent?.read_enabled
      ? group.agent?.agent_enabled ? '自动回复已开启' : '只读模式'
      : '未读取，不会回复';
    bottom.append(readFlag, replyFlag, preview);
    main.append(top, bottom);
    button.append(avatar, main);
    container.appendChild(button);
  }
}

async function selectGroup(groupId) {
  if (!state.groups.some((group) => group.id === groupId)) return;
  state.selectedGroupId = groupId;
  state.unread.delete(groupId);
  closeStickerPopover();
  renderGroupList();
  const group = currentGroup();
  renderChatHeader();
  renderInspector(group);
  switchInspectorTab('config');
  $('#chat-empty').hidden = true;
  $('#chat-active').hidden = false;
  const list = $('#message-list');
  if (list) list.innerHTML = '<div class="message-empty">正在加载消息…</div>';
  state.messages = [];
  state.members = [];
  state.logs = [];
  state.chatTasks = [];
  state.hasMoreMessages = false;
  state.loadingMore = false;
  updateLoadMoreButton();
  renderLogs();
  renderChatTasks();
  loadChatTasks(groupId).catch(() => {});
  try {
    const [messages, logs] = await Promise.all([
      api(`/groups/${groupId}/messages?limit=80`),
      api(`/groups/${groupId}/logs?limit=30`).catch(() => ({ logs: [] })),
    ]);
    if (state.selectedGroupId !== groupId) return;
    const fetched = (messages.messages || []).sort(compareMessages);
    const fetchedIds = new Set(fetched.map((message) => message.id));
    const live = state.messages.filter(
      (message) => message.group_id === groupId && !fetchedIds.has(message.id),
    );
    state.messages = fetched.concat(live).sort(compareMessages);
    state.hasMoreMessages = Boolean(messages.has_more);
    state.logs = logs.logs || [];
    for (const message of state.messages) state.seenMessageIds.add(message.id);
    renderMessages(true);
    updateLoadMoreButton();
    renderLogs();
    scrollMessages(true);
    loadMembers(groupId).catch(() => {});
  } catch (error) {
    if (state.selectedGroupId !== groupId) return;
    toast(error.message, 'error');
    renderMessages(true);
  }
}

async function reloadSelectedMessages() {
  const groupId = state.selectedGroupId;
  if (!groupId) return;
  try {
    const data = await api(`/groups/${groupId}/messages?limit=80`);
    if (state.selectedGroupId !== groupId) return;
    const fetched = (data.messages || []).sort(compareMessages);
    const fetchedIds = new Set(fetched.map((message) => message.id));
    const live = state.messages.filter((message) => !fetchedIds.has(message.id));
    state.messages = fetched.concat(live).sort(compareMessages);
    state.hasMoreMessages = Boolean(data.has_more);
    for (const message of state.messages) state.seenMessageIds.add(message.id);
    renderMessages(true);
    updateLoadMoreButton();
    scrollMessages(true);
  } catch {
    /* ignore */
  }
}

function renderChatHeader() {
  const group = currentGroup();
  if (!group) {
    $('#chat-empty').hidden = false;
    $('#chat-active').hidden = true;
    const summaryName = $('#config-group-name');
    if (summaryName) summaryName.textContent = '未选择群聊';
    return;
  }
  $('#chat-empty').hidden = true;
  $('#chat-active').hidden = false;
  const avatar = $('#chat-avatar');
  paintAvatar(avatar, group.name);
  $('#chat-name').textContent = group.name || '未命名群聊';
  $('#chat-name').title = group.name || '';
  const readEnabled = Boolean(group.agent?.read_enabled);
  const readToggle = $('#chat-read-toggle');
  if (readToggle) readToggle.checked = readEnabled;
  readToggle?.closest('.chat-toggle')?.classList.toggle('on', readEnabled);
  const toggle = $('#chat-agent-toggle');
  if (toggle) {
    toggle.checked = Boolean(group.agent?.agent_enabled) && readEnabled;
    toggle.disabled = !readEnabled;
  }
  toggle?.closest('.chat-toggle')?.classList.toggle(
    'on',
    Boolean(group.agent?.agent_enabled) && readEnabled,
  );
  const pinButton = $('#chat-pin-btn');
  if (pinButton) {
    const pinned = state.pinnedGroupId === group.id;
    pinButton.classList.toggle('active', pinned);
    pinButton.title = pinned ? '取消置顶当前聊天' : '置顶当前聊天（只允许一个）';
  }
  const badge = $('#chat-agent-badge');
  if (badge) {
    badge.hidden = !readEnabled;
    badge.textContent = group.agent?.agent_enabled ? 'Agent' : '只读';
    badge.title = readEnabled
      ? group.agent?.agent_enabled ? '读取中，自动回复已开启' : '读取中，仅记录不回复'
      : '未读取本群消息';
  }
  const meta = $('#chat-meta');
  if (meta) {
    const names = state.members
      .filter((member) => member.wxid !== state.status?.account?.wxid)
      .slice(0, 3)
      .map((member) => member.nickname)
      .filter(Boolean);
    const count = group.member_count || state.members.length || 0;
    const namePart = names.length
      ? `${names.join('、')}${count > names.length ? ` 等 ${count} 人` : ''}`
      : `${count || 0} 位成员`;
    const agentPart = !readEnabled
      ? ' · 未读取'
      : group.agent?.agent_enabled
        ? ' · 自动回复中'
        : ' · 只读';
    meta.textContent = `${namePart}${agentPart}`;
    meta.title = state.members.map((member) => member.nickname).filter(Boolean).join('、');
  }
  renderToolGroupHint?.();
}

async function loadMembers(groupId, manual = false) {
  try {
    const data = await api(`/groups/${groupId}/members`);
    if (state.selectedGroupId !== groupId) return;
    state.members = data.members || [];
    const group = currentGroup();
    if (group) group.member_count = data.count ?? state.members.length;
    renderChatHeader();
    renderGroupList();
    renderMembersDialog();
    if (manual) toast(`已同步 ${data.count ?? state.members.length} 位成员`);
  } catch (error) {
    if (manual) toast(error.message, 'warn');
  }
}

function renderMembersDialog() {
  const dialog = $('#members-dialog');
  if (!dialog || !dialog.open) return;
  const group = currentGroup();
  const list = $('#members-list');
  const title = $('#members-title');
  const count = $('#members-count');
  if (!list) return;
  if (title) title.textContent = group ? `${group.name} · 群成员` : '群成员';
  if (count) count.textContent = `${state.members.length} 位成员`;
  list.replaceChildren();
  if (!state.members.length) {
    list.appendChild(el('div', 'group-list-empty', '还没有成员信息\n点击右下角「刷新成员」从微信同步'));
    return;
  }
  for (const member of state.members) {
    const item = el('div', 'member-item');
    const avatar = el('span', 'avatar');
    paintAvatar(avatar, member.nickname || member.wxid);
    const main = el('div', 'member-main');
    main.appendChild(el('strong', null, member.nickname || member.wxid));
    const detail = [member.alias ? `群昵称：${member.alias}` : '', member.wxid].filter(Boolean).join(' · ');
    main.appendChild(el('small', null, detail));
    item.append(avatar, main);
    if (member.is_owner) item.appendChild(el('span', 'member-role', '群主'));
    else if (member.is_admin) item.appendChild(el('span', 'member-role admin', '管理员'));
    list.appendChild(item);
  }
}

function openMembersDialog() {
  const group = currentGroup();
  if (!group) return toast('请先选择一个群聊', 'warn');
  const dialog = $('#members-dialog');
  if (!dialog) return;
  dialog.showModal();
  renderMembersDialog();
  loadMembers(group.id).catch(() => {});
  if (state.members.length) renderMembersDialog();
}

function updateLoadMoreButton() {
  const button = $('#load-more');
  if (!button) return;
  button.hidden = !state.selectedGroupId || !state.hasMoreMessages;
  button.disabled = state.loadingMore;
  button.textContent = state.loadingMore ? '正在加载…' : '加载更早消息';
}

async function loadOlderMessages() {
  const groupId = state.selectedGroupId;
  if (!groupId || state.loadingMore || !state.hasMoreMessages) return;
  const first = state.messages[0];
  if (!first) return;
  state.loadingMore = true;
  updateLoadMoreButton();
  const area = $('#message-area');
  const previousHeight = area?.scrollHeight || 0;
  const previousTop = area?.scrollTop || 0;
  try {
    const data = await api(`/groups/${groupId}/messages?limit=80&before_id=${first.id}`);
    if (state.selectedGroupId !== groupId) return;
    const older = (data.messages || []).sort(compareMessages);
    const existing = new Set(state.messages.map((message) => message.id));
    state.messages = older.filter((message) => !existing.has(message.id)).concat(state.messages).sort(compareMessages);
    state.hasMoreMessages = Boolean(data.has_more);
    for (const message of state.messages) state.seenMessageIds.add(message.id);
    renderMessages(false);
    if (area) {
      requestAnimationFrame(() => {
        area.scrollTop = area.scrollHeight - previousHeight + previousTop;
      });
    }
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    state.loadingMore = false;
    updateLoadMoreButton();
  }
}

/* ------------------------------------------------------------ messages */
function isNearBottom() {
  const area = $('#message-area');
  if (!area) return true;
  return area.scrollHeight - area.scrollTop - area.clientHeight < 120;
}

function compareMessages(a, b) {
  const ta = toDate(a.created_at)?.getTime() || 0;
  const tb = toDate(b.created_at)?.getTime() || 0;
  if (ta !== tb) return ta - tb;
  return (a.id || 0) - (b.id || 0);
}

function scrollMessages(force = false) {
  const area = $('#message-area');
  if (!area) return;
  if (force || isNearBottom()) {
    requestAnimationFrame(() => {
      area.scrollTop = area.scrollHeight;
    });
  }
}

function renderMessages(forceScroll = false) {
  const list = $('#message-list');
  if (!list) return;
  list.replaceChildren();
  if (!state.messages.length) {
    list.appendChild(el('div', 'message-empty', '还没有消息\n发一条消息，或等待群友开口。'));
  } else {
    let lastDay = null;
    for (const message of state.messages) {
      const key = dayKey(message.created_at);
      if (key !== lastDay) {
        list.appendChild(el('div', 'date-divider', dayLabel(message.created_at)));
        lastDay = key;
      }
      list.appendChild(buildMessageNode(message));
    }
  }
  if (forceScroll) scrollMessages(true);
}

function buildMessageNode(message) {
  if (message.kind === 'system') {
    return el('div', 'msg-system', message.content || '[系统消息]');
  }
  const row = el('div', `message-row${message.is_self ? ' self' : ''}${message.is_from_agent ? ' agent' : ''}`);
  row.dataset.messageId = String(message.id);
  const avatar = el('span', 'avatar');
  paintAvatar(avatar, message.sender_name || '?');
  const main = el('div', 'msg-main');
  const meta = el('div', 'msg-meta');
  meta.appendChild(el('strong', null, message.sender_name || '未知'));
  if (message.is_from_agent) meta.appendChild(el('span', 'msg-agent-badge', 'AGENT'));
  const source = messageSourceLabel(message);
  if (source) meta.appendChild(el('span', 'msg-source-badge', source));
  const sendState = String(message.meta?._send_state || '');
  if (sendState === 'failed' || sendState === 'unconfirmed') {
    meta.appendChild(
      el('span', 'msg-failed-badge', sendState === 'unconfirmed' ? '结果未确认' : '发送失败'),
    );
  }
  meta.appendChild(el('time', null, formatClock(message.created_at)));
  const bubble = el(
    'div',
    `msg-bubble${message.kind === 'sticker' ? ' sticker' : message.kind === 'image' ? ' image' : ''}`,
  );
  if (message.kind === 'sticker') {
    // Stickers are intentionally text-only in the dashboard. Downloading the
    // original sticker is slow and the requirement is a simple hint.
    bubble.appendChild(el('span', 'placeholder-media', '表情包'));
  } else if (message.kind === 'image') {
    if (message.media_url) {
      const img = el('img');
      img.src = message.media_url;
      img.alt = message.content || '图片';
      img.loading = 'lazy';
      img.addEventListener('click', () => openImageViewer(message.media_url, message.content));
      img.addEventListener('error', () => {
        img.replaceWith(el('span', 'placeholder-media', message.content || '[图片]'));
      });
      bubble.appendChild(img);
    } else {
      bubble.appendChild(el('span', 'placeholder-media', message.content || '[图片]'));
    }
  } else {
    bubble.textContent = message.content || '…';
  }
  if (sendState === 'failed' || sendState === 'unconfirmed') {
    const retry = el(
      'button',
      'msg-retry-btn',
      sendState === 'unconfirmed' ? '结果未确认 · 校验并重发' : '发送失败 · 点击重发',
    );
    retry.title = String(message.meta?._send_error || '');
    retry.addEventListener('click', async (event) => {
      event.stopPropagation();
      retry.disabled = true;
      retry.textContent = '校验并重发中…';
      try {
        const data = await api(`/outbox/${message.id}/retry`, { method: 'POST' });
        if (data?.retried) toast('已重新发送');
        else if (data?.duplicates) toast('已确认这条消息之前其实发送成功');
        else toast('仍未发送成功，请检查微信窗口状态', 'warn');
      } catch (error) {
        toast(error.message, 'error');
        retry.disabled = false;
        retry.textContent =
          sendState === 'unconfirmed' ? '结果未确认 · 校验并重发' : '发送失败 · 点击重发';
      }
    });
    bubble.appendChild(retry);
  }
  main.append(meta, bubble);
  row.append(avatar, main);
  return row;
}

function appendMessageNode(message, forceScroll = false) {
  const list = $('#message-list');
  if (!list) return;
  const empty = list.querySelector('.message-empty');
  if (empty) empty.remove();
  const previous = state.messages[state.messages.length - 2];
  if (!previous || dayKey(previous.created_at) !== dayKey(message.created_at)) {
    list.appendChild(el('div', 'date-divider', dayLabel(message.created_at)));
  }
  list.appendChild(buildMessageNode(message));
  if (forceScroll || isNearBottom()) scrollMessages(true);
}

async function handleNewMessage(message) {
  if (!message || message.id == null) return;
  if (state.seenMessageIds.has(message.id)) return;
  state.seenMessageIds.add(message.id);
  if (state.seenMessageIds.size > 6000) {
    state.seenMessageIds = new Set(Array.from(state.seenMessageIds).slice(-4000));
  }
  let group = state.groups.find((item) => item.id === message.group_id);
  if (!group) {
    try {
      await loadGroups();
    } catch {
      return;
    }
    group = state.groups.find((item) => item.id === message.group_id);
    if (!group) return;
  }
  const messageTime = toDate(message.created_at)?.getTime() || Date.now();
  const previousTime = toDate(group.last_message_at)?.getTime() || 0;
  if (messageTime >= previousTime) {
    group.last_message = {
      id: message.id,
      sender_name: message.sender_name,
      content: message.content,
      kind: message.kind,
      is_from_agent: message.is_from_agent,
      created_at: message.created_at,
    };
    group.last_message_at = message.created_at;
  }
  group.message_count = (group.message_count || 0) + 1;
  if (state.selectedGroupId === message.group_id) {
    const previous = state.messages[state.messages.length - 1];
    const previousMessageTime = toDate(previous?.created_at)?.getTime() || 0;
    const wasNearBottom = isNearBottom();
    state.messages.push(message);
    if (previous && messageTime < previousMessageTime) {
      state.messages.sort(compareMessages);
      renderMessages(false);
      if (wasNearBottom) scrollMessages(true);
    } else {
      appendMessageNode(message, message.is_self || wasNearBottom);
    }
    if (message.is_from_agent) hideTyping();
  } else if (!message.is_self && !message.is_from_agent) {
    state.unread.set(group.id, (state.unread.get(group.id) || 0) + 1);
  }
  sortGroups();
  renderGroupList();
  if (state.selectedGroupId === group.id) {
    renderChatHeader();
  }
}

function applyMessageUpdate(message) {
  if (!message || message.id == null) return;
  const index = state.messages.findIndex((item) => item.id === message.id);
  if (index < 0) return;
  state.messages[index] = { ...state.messages[index], ...message };
  const node = document.querySelector(`[data-message-id="${message.id}"]`);
  if (node) node.replaceWith(buildMessageNode(state.messages[index]));
}

function removeMessage(message) {
  if (!message || message.id == null) return;
  const id = Number(message.id);
  state.seenMessageIds.delete(id);
  state.messages = state.messages.filter((item) => item.id !== id);
  document.querySelector(`[data-message-id="${id}"]`)?.remove();
  const group = state.groups.find((item) => item.id === message.group_id);
  if (group) group.message_count = Math.max(0, (group.message_count || 1) - 1);
}

function handleDirectMessage(message) {
  if (!message || message.contact_id == null) return;
  let contact = state.contacts.find((item) => item.id === message.contact_id);
  if (!contact) {
    loadContacts().catch(() => {});
    return;
  }
  contact.last_message = {
    content: message.content,
    kind: message.kind,
    sender_name: message.sender_name,
    is_self: message.is_self,
    created_at: message.created_at,
  };
  if (state.selectedContactId === message.contact_id) {
    const assistantChatActive =
      state.currentTab === 'assistant' && state.assistantPanel === 'contacts';
    if (!$('#contact-chat-dialog')?.open && !assistantChatActive) return;
    state.contactMessages.push(message);
    renderContactMessages();
    if (message.is_self && message.meta?.source === 'admin-agent') hideTyping();
  }
  if (state.sidebarMode === 'contacts') renderContactList();
}

function showTyping(groupId) {
  if (state.selectedGroupId !== groupId) return;
  const indicator = $('#typing-indicator');
  if (!indicator) return;
  $('#typing-name').textContent = state.status?.bot_name || 'Agent';
  indicator.hidden = false;
  scrollMessages();
  if (state.typingTimer) window.clearTimeout(state.typingTimer);
  state.typingTimer = window.setTimeout(hideTyping, 9000);
}

function hideTyping() {
  const indicator = $('#typing-indicator');
  if (indicator) indicator.hidden = true;
  if (state.typingTimer) window.clearTimeout(state.typingTimer);
  state.typingTimer = null;
}

async function sendMessage() {
  const input = $('#composer-input');
  const text = input?.value.trim() || '';
  if (!text) return;
  const groupId = state.selectedGroupId;
  if (!groupId) return toast('请先选择一个群聊', 'warn');
  if (state.status?.state !== 'online') return toast('微信账号尚未登录', 'warn');
  const button = $('#send-btn');
  button.disabled = true;
  try {
    const response = await api(`/groups/${groupId}/send`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    input.value = '';
    autoResizeComposer();
    for (const message of response.messages || []) await handleNewMessage(message);
    hideTyping();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    updateComposerState();
  }
}

async function sendAttachment(file) {
  const groupId = state.selectedGroupId;
  if (!groupId) return toast('请先选择一个群聊', 'warn');
  if (state.status?.state !== 'online') return toast('微信账号尚未登录', 'warn');
  if (state.detached) return toast('群伴已脱离控制，请先重新接管', 'warn');
  if (!file) return;
  const asImage = /\.(png|jpe?g|gif|webp|bmp)$/i.test(file.name || '');
  const form = new FormData();
  form.append('file', file);
  const button = $('#attachment-btn');
  if (button) button.disabled = true;
  try {
    await api(`/replica/groups/${groupId}/send-file?as_image=${asImage}`, {
      method: 'POST',
      body: form,
    });
    toast(asImage ? '图片已发送' : '文件已发送');
    await new Promise((resolve) => setTimeout(resolve, 900));
    await reloadSelectedMessages();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    const input = $('#attachment-input');
    if (input) input.value = '';
    updateComposerState();
  }
}

function autoResizeComposer() {
  const input = $('#composer-input');
  if (!input) return;
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
}

async function sendStickerNow(stickerId) {
  const groupId = state.selectedGroupId;
  if (!groupId) return toast('请先选择一个群聊', 'warn');
  if (state.status?.state !== 'online') return toast('微信账号尚未登录', 'warn');
  try {
    const response = await api(`/stickers/${stickerId}/send`, {
      method: 'POST',
      body: JSON.stringify({ group_id: groupId }),
    });
    closeStickerPopover();
    for (const message of response.messages || []) await handleNewMessage(message);
    toast('表情包已发送');
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* ------------------------------------------------------------ inspector */
function syncWorkspaceLayout(hasChat) {
  document.body.classList.toggle('no-chat', !hasChat);
  const toggle = $('#inspector-toggle');
  if (toggle) toggle.disabled = !hasChat;
}

function switchInspectorTab(name = 'config') {
  $$('.inspector-tabs button').forEach((button) => {
    button.classList.toggle('active', button.dataset.inspectorTab === name);
  });
  $$('[data-inspector-pane]').forEach((pane) => {
    pane.hidden = pane.dataset.inspectorPane !== name;
  });
}

function renderInspector(group) {
  syncWorkspaceLayout(Boolean(group));
  const disabled = !group;
  const fields = ['#cfg-read', '#cfg-read-interval', '#cfg-enabled', '#cfg-trigger', '#cfg-keywords', '#cfg-probability', '#cfg-cooldown', '#cfg-max', '#cfg-quiet-start', '#cfg-quiet-end', '#cfg-stickers', '#cfg-multi-message', '#cfg-max-messages', '#cfg-proactive', '#cfg-proactive-interval', '#cfg-mention', '#cfg-persona', '#cfg-model', '#save-config-btn'];
  for (const selector of fields) {
    const node = $(selector);
    if (node) node.disabled = disabled;
  }
  const name = $('#config-group-name');
  const hint = $('#config-mode-hint');
  if (!group) {
    if (name) name.textContent = '未选择群聊';
    if (hint) hint.textContent = '选择群聊后可配置';
    return;
  }
  const config = group.agent || {};
  const readEnabled = Boolean(config.read_enabled);
  if (name) name.textContent = group.name || '当前群聊';
  if (hint) {
    hint.textContent = !readEnabled
      ? '未读取本群消息'
      : config.agent_enabled
        ? `只读 + 自动回复 · ${triggerLabel(config.trigger_mode)} · ${config.sticker_enabled ? '可发表情' : '仅文字'}`
        : '只读模式 · 记录消息但不自动回复';
  }
  $('#cfg-read').checked = readEnabled;
  $('#cfg-read-interval').value = String(config.read_interval_seconds ?? 0);
  $('#cfg-enabled').checked = Boolean(config.agent_enabled) && readEnabled;
  $('#cfg-enabled').disabled = disabled || !readEnabled;
  $('#cfg-trigger').value = config.trigger_mode || 'mention';
  $('#cfg-keywords').value = (config.keywords || []).join(', ');
  $('#cfg-probability').value = String(Math.round((config.probability ?? 1) * 100));
  $('#cfg-probability-value').textContent = `${Math.round((config.probability ?? 1) * 100)}%`;
  $('#cfg-cooldown').value = String(config.cooldown_seconds ?? 30);
  $('#cfg-max').value = String(config.max_replies_per_hour ?? 30);
  $('#cfg-quiet-start').value = config.quiet_start || '';
  $('#cfg-quiet-end').value = config.quiet_end || '';
  $('#cfg-stickers').checked = config.sticker_enabled !== false;
  $('#cfg-multi-message').checked = Boolean(config.multi_message_enabled);
  $('#cfg-max-messages').value = String(config.max_messages ?? 3);
  $('#cfg-proactive').checked = Boolean(config.proactive_enabled);
  $('#cfg-proactive-interval').value = String(config.proactive_interval_minutes ?? 90);
  $('#cfg-mention').checked = Boolean(config.mention_on_reply);
  $('#cfg-persona').value = config.persona || '';
  $('#cfg-model').value = config.model || '';
  setRangeFill($('#cfg-probability'));
  updateKeywordVisibility();
}

function triggerLabel(mode) {
  return { mention: '@ 提及', keyword: '关键词', all: '所有消息' }[mode] || mode || '—';
}

function updateKeywordVisibility() {
  const mode = $('#cfg-trigger')?.value;
  const wrap = $('#cfg-keywords-wrap');
  if (wrap) wrap.hidden = mode !== 'keyword';
}

function collectGroupConfig() {
  const quietStart = $('#cfg-quiet-start').value;
  const quietEnd = $('#cfg-quiet-end').value;
  if ((quietStart && !quietEnd) || (!quietStart && quietEnd)) {
    throw new Error('静默开始和结束时间需要同时填写');
  }
  const readEnabled = $('#cfg-read').checked;
  const agentEnabled = $('#cfg-enabled').checked;
  if (agentEnabled && !readEnabled) {
    throw new Error('开启自动回复前请先开启读取');
  }
  return {
    read_enabled: readEnabled,
    read_interval_seconds: parseIntSafe($('#cfg-read-interval').value, 0),
    agent_enabled: agentEnabled,
    trigger_mode: $('#cfg-trigger').value,
    keywords: $('#cfg-keywords').value.split(/[,，、\n]+/).map((item) => item.trim()).filter(Boolean),
    probability: parseIntSafe($('#cfg-probability').value, 100) / 100,
    cooldown_seconds: parseIntSafe($('#cfg-cooldown').value, 30),
    max_replies_per_hour: parseIntSafe($('#cfg-max').value, 30),
    quiet_start: quietStart,
    quiet_end: quietEnd,
    sticker_enabled: $('#cfg-stickers').checked,
    multi_message_enabled: $('#cfg-multi-message').checked,
    max_messages: parseIntSafe($('#cfg-max-messages').value, 3),
    proactive_enabled: $('#cfg-proactive').checked,
    proactive_interval_minutes: parseIntSafe($('#cfg-proactive-interval').value, 90),
    mention_on_reply: $('#cfg-mention').checked,
    persona: $('#cfg-persona').value,
    model: $('#cfg-model').value.trim(),
  };
}

async function patchGroupConfig(payload, successMessage = '本群配置已保存') {
  const group = currentGroup();
  if (!group) return;
  const button = $('#save-config-btn');
  const oldText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = '保存中…';
  }
  try {
    const response = await api(`/groups/${group.id}/config`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
    const updated = response.group;
    const index = state.groups.findIndex((item) => item.id === group.id);
    if (index >= 0) state.groups[index] = updated;
    sortGroups();
    renderGroupList();
    renderChatHeader();
    if (!document.activeElement?.closest?.('#inspector') || successMessage === '本群配置已保存') {
      renderInspector(updated);
    }
    toast(successMessage);
    return updated;
  } catch (error) {
    toast(error.message, 'error');
    throw error;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = oldText || '保存本群配置';
    }
  }
}

async function saveGroupConfig() {
  try {
    const payload = collectGroupConfig();
    await patchGroupConfig(payload);
  } catch (error) {
    if (error?.message) toast(error.message, 'warn');
  }
}

async function togglePinGroup() {
  const group = currentGroup();
  if (!group) return;
  const pinned = state.pinnedGroupId === group.id;
  try {
    const response = await api(`/groups/${group.id}/pin`, {
      method: pinned ? 'DELETE' : 'POST',
    });
    state.pinnedGroupId = response.pinned_group_id || null;
    if (response.status) applyStatus(response.status);
    await loadGroups();
    renderChatHeader();
    renderGroupList();
    toast(
      pinned
        ? '已取消置顶'
        : '已置顶当前聊天，数据库读取已开启',
    );
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function toggleGroupRead(enabled) {
  const group = currentGroup();
  if (!group) return;
  try {
    const payload = enabled
      ? { read_enabled: true }
      : { read_enabled: false, agent_enabled: false };
    await patchGroupConfig(payload, enabled ? '已开启读取本群消息' : '已关闭读取本群消息');
    if (enabled) loadMembers(group.id).catch(() => {});
  } catch {
    const toggle = $('#chat-read-toggle');
    if (toggle) toggle.checked = Boolean(currentGroup()?.agent?.read_enabled);
  }
}

async function toggleGroupAgent(enabled) {
  const group = currentGroup();
  if (!group) return;
  try {
    const payload = enabled && !group.agent?.read_enabled
      ? { read_enabled: true, agent_enabled: true }
      : { agent_enabled: enabled };
    await patchGroupConfig(
      payload,
      enabled ? '已开启本群自动回复' : '已暂停本群自动回复',
    );
    if (enabled && !group.agent?.read_enabled) loadMembers(group.id).catch(() => {});
  } catch {
    const toggle = $('#chat-agent-toggle');
    if (toggle) toggle.checked = Boolean(currentGroup()?.agent?.agent_enabled);
  }
}

async function loadChatTasks(groupId) {
  try {
    const data = await api(`/groups/${groupId}/tasks?limit=30`);
    if (state.selectedGroupId !== groupId) return;
    state.chatTasks = data.tasks || [];
    renderChatTasks();
  } catch {
    /* ignore */
  }
}

const CHAT_TASK_LABELS = {
  pending: '排队中',
  running: '处理中',
  done: '已完成',
  error: '失败',
  cancelled: '已取消',
};

function renderChatTasks() {
  const container = $('#chat-task-list');
  if (!container) return;
  container.replaceChildren();
  if (!state.chatTasks.length) {
    container.appendChild(el('p', 'muted', '暂无任务；需要查资料时会自动挂到这里的后台执行。'));
    return;
  }
  for (const task of state.chatTasks.slice(0, 10)) {
    const item = el('div', `chat-task-item ${task.status || 'pending'}`);
    const head = el('div', 'chat-task-head');
    head.appendChild(el('strong', null, `#${task.id} ${task.user_name || '群友'}`));
    const badges = el('div', 'chat-task-badges');
    if (task.engine === 'dsh') {
      const dshBadge = el('button', 'chat-task-engine', 'DeepSeek Harness');
      dshBadge.type = 'button';
      dshBadge.title = '查看 DSH 任务';
      dshBadge.addEventListener('click', () => {
        document.querySelector('button[data-tab="assistant"]')?.click();
        switchAssistantPanel('dsh');
        loadDSH();
      });
      badges.appendChild(dshBadge);
    }
    badges.appendChild(
      el(
        'span',
        `chat-task-status ${task.status || 'pending'}`,
        CHAT_TASK_LABELS[task.status] || task.status,
      ),
    );
    head.appendChild(badges);
    item.appendChild(head);
    item.appendChild(el('p', 'chat-task-query', task.query || ''));
    if (task.result) item.appendChild(el('p', 'chat-task-result', `结果：${task.result.slice(0, 180)}`));
    if (task.error) item.appendChild(el('p', 'chat-task-error', task.error.slice(0, 180)));
    container.appendChild(item);
  }
}

async function loadLogs(groupId) {
  try {
    const data = await api(`/groups/${groupId}/logs?limit=30`);
    if (state.selectedGroupId !== groupId) return;
    state.logs = data.logs || [];
    renderLogs();
  } catch {
    /* ignore */
  }
}

function renderLogs() {
  const container = $('#config-logs');
  if (!container) return;
  container.replaceChildren();
  if (!state.logs.length) {
    container.appendChild(el('p', 'muted', '暂无运行记录'));
    return;
  }
  for (const log of state.logs.slice(0, 12)) {
    const item = el('div', `log-item ${log.level === 'error' ? 'error' : log.event === 'skip' ? 'skip' : ''}`);
    item.appendChild(el('span', 'log-dot'));
    const body = el('div', 'log-body');
    body.appendChild(el('p', null, log.detail || log.event));
    const time = el('time', null, `${formatClock(log.created_at)} · ${log.event}`);
    body.appendChild(time);
    item.appendChild(body);
    container.appendChild(item);
  }
}

/* ------------------------------------------------------------ stickers */
async function loadStickers() {
  const data = await api('/stickers');
  state.stickers = data.stickers || [];
  state.stickerTags = data.tags || [];
  if (state.stickerFilter !== 'all' && !state.stickerTags.includes(state.stickerFilter)) {
    state.stickerFilter = 'all';
  }
  renderStickerTags();
  renderStickers();
  renderStickerPicker();
}

function stickerMatches(sticker) {
  if (state.stickerFilter !== 'all' && !(sticker.tags || []).includes(state.stickerFilter)) return false;
  const query = state.stickerQuery.trim().toLowerCase();
  if (!query) return true;
  return (
    (sticker.name || '').toLowerCase().includes(query) ||
    (sticker.description || '').toLowerCase().includes(query) ||
    (sticker.tags || []).some((tag) => String(tag).toLowerCase().includes(query))
  );
}

function renderStickerTags() {
  const container = $('#sticker-tags');
  if (!container) return;
  container.replaceChildren();
  const tags = ['all', ...state.stickerTags];
  for (const tag of tags) {
    const button = el('button', `tag-chip${state.stickerFilter === tag ? ' active' : ''}`, tag === 'all' ? '全部' : tag);
    button.type = 'button';
    button.dataset.tag = tag;
    container.appendChild(button);
  }
}

function renderStickers() {
  const grid = $('#sticker-grid');
  const empty = $('#sticker-empty');
  const count = $('#sticker-count');
  if (!grid) return;
  const stickers = state.stickers.filter(stickerMatches);
  grid.replaceChildren();
  if (count) count.textContent = `${stickers.length} 个表情`;
  if (empty) empty.hidden = stickers.length > 0;
  for (const sticker of stickers) {
    const card = el('div', 'sticker-card');
    const thumb = el('button', 'sticker-thumb');
    thumb.type = 'button';
    thumb.title = '发送这个表情包';
    thumb.dataset.action = 'send';
    thumb.dataset.id = String(sticker.id);
    const img = el('img');
    img.src = sticker.url;
    img.alt = sticker.name || '表情包';
    img.loading = 'lazy';
    thumb.appendChild(img);
    const actions = el('div', 'sticker-actions');
    const sendBtn = el('button', 'icon-btn');
    sendBtn.type = 'button';
    sendBtn.title = '发送';
    sendBtn.dataset.action = 'send';
    sendBtn.dataset.id = String(sticker.id);
    sendBtn.innerHTML = icon('send', 13);
    const editBtn = el('button', 'icon-btn');
    editBtn.type = 'button';
    editBtn.title = '编辑';
    editBtn.dataset.action = 'edit';
    editBtn.dataset.id = String(sticker.id);
    editBtn.innerHTML = icon('edit', 13);
    const deleteBtn = el('button', 'icon-btn');
    deleteBtn.type = 'button';
    deleteBtn.title = '删除';
    deleteBtn.dataset.action = 'delete';
    deleteBtn.dataset.id = String(sticker.id);
    deleteBtn.innerHTML = icon('trash', 13);
    actions.append(sendBtn, editBtn, deleteBtn);
    const info = el('div', 'sticker-info');
    info.appendChild(el('strong', null, sticker.name || '未命名'));
    const tags = el('div', 'sticker-tags');
    for (const tag of (sticker.tags || []).slice(0, 4)) tags.appendChild(el('span', null, tag));
    info.appendChild(tags);
    info.appendChild(el('div', 'sticker-usage', `使用 ${sticker.usage_count || 0} 次`));
    card.append(thumb, actions, info);
    grid.appendChild(card);
  }
}

function renderStickerPicker() {
  const grid = $('#sticker-popover-grid');
  const count = $('#popover-sticker-count');
  if (!grid) return;
  grid.replaceChildren();
  if (count) count.textContent = `${state.stickers.length} 个`;
  if (!state.stickers.length) {
    grid.appendChild(el('p', 'muted', '还没有表情包'));
    return;
  }
  for (const sticker of state.stickers.slice(0, 24)) {
    const button = el('button');
    button.type = 'button';
    button.title = `${sticker.name || ''} ${(sticker.tags || []).join(' ')}`;
    button.dataset.stickerId = String(sticker.id);
    const img = el('img');
    img.src = sticker.url;
    img.alt = sticker.name || '表情';
    img.loading = 'lazy';
    button.appendChild(img);
    grid.appendChild(button);
  }
}

function openStickerPicker() {
  if (state.status?.state !== 'online') return toast('微信账号尚未登录', 'warn');
  const popover = $('#sticker-popover');
  if (!popover) return;
  const willShow = popover.hidden;
  popover.hidden = !willShow;
  if (willShow) {
    renderStickerPicker();
    const first = popover.querySelector('.sticker-popover-grid button');
    if (!first) toast('表情库为空，先去上传一张吧', 'info');
  }
}

function closeStickerPopover() {
  const popover = $('#sticker-popover');
  if (popover) popover.hidden = true;
}

function openUploadDialog() {
  resetUploadDialog();
  const dialog = $('#upload-dialog');
  if (dialog) dialog.showModal();
}

function resetUploadDialog() {
  state.uploadFile = null;
  if (state.uploadObjectUrl) {
    URL.revokeObjectURL(state.uploadObjectUrl);
    state.uploadObjectUrl = null;
  }
  const preview = $('#upload-preview');
  if (preview) {
    preview.hidden = true;
    preview.removeAttribute('src');
  }
  $('#upload-file').value = '';
  $('#upload-name').value = '';
  $('#upload-tags').value = '';
  $('#upload-desc').value = '';
}

function setUploadFile(file) {
  if (!file) return;
  const allowed = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
  if (!allowed.includes(file.type) && !/\.(png|jpe?g|gif|webp)$/i.test(file.name)) {
    toast('仅支持 PNG / JPG / GIF / WEBP', 'warn');
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    toast('图片不能超过 5MB', 'warn');
    return;
  }
  state.uploadFile = file;
  if (state.uploadObjectUrl) URL.revokeObjectURL(state.uploadObjectUrl);
  state.uploadObjectUrl = URL.createObjectURL(file);
  const preview = $('#upload-preview');
  preview.src = state.uploadObjectUrl;
  preview.hidden = false;
  const name = $('#upload-name');
  if (!name.value) name.value = file.name.replace(/\.[^.]+$/, '');
}

async function submitUpload(event) {
  event.preventDefault();
  if (!state.uploadFile) return toast('请选择一张图片', 'warn');
  const button = $('#upload-submit');
  button.disabled = true;
  button.textContent = '上传中…';
  try {
    const form = new FormData();
    form.append('file', state.uploadFile);
    form.append('name', $('#upload-name').value.trim());
    form.append('tags', $('#upload-tags').value.trim());
    form.append('description', $('#upload-desc').value.trim());
    await api('/stickers', { method: 'POST', body: form });
    $('#upload-dialog').close();
    resetUploadDialog();
    await loadStickers();
    toast('表情包已加入库');
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '上传';
  }
}

function openEditDialog(sticker) {
  $('#edit-id').value = String(sticker.id);
  $('#edit-name').value = sticker.name || '';
  $('#edit-tags').value = (sticker.tags || []).join(', ');
  $('#edit-desc').value = sticker.description || '';
  $('#edit-dialog').showModal();
}

async function submitEdit(event) {
  event.preventDefault();
  const id = parseIntSafe($('#edit-id').value);
  try {
    await api(`/stickers/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        name: $('#edit-name').value.trim(),
        tags: $('#edit-tags').value.split(/[,，、]+/).map((item) => item.trim()).filter(Boolean),
        description: $('#edit-desc').value.trim(),
      }),
    });
    $('#edit-dialog').close();
    await loadStickers();
    toast('表情包信息已更新');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function deleteSticker(sticker) {
  if (!window.confirm(`删除表情包「${sticker.name}」？`)) return;
  try {
    await api(`/stickers/${sticker.id}`, { method: 'DELETE' });
    await loadStickers();
    toast('已删除');
  } catch (error) {
    toast(error.message, 'error');
  }
}

function openSendDialog(sticker) {
  $('#send-sticker-id').value = String(sticker.id);
  const preview = $('#send-preview');
  preview.src = sticker.url;
  preview.hidden = false;
  const select = $('#send-group');
  select.replaceChildren();
  if (!state.groups.length) {
    select.appendChild(new Option('暂无群聊', ''));
  } else {
    for (const group of state.groups) {
      const option = new Option(`${group.name}${group.agent?.agent_enabled ? ' · Agent 已启用' : ''}`, String(group.id));
      select.appendChild(option);
    }
    if (state.selectedGroupId) select.value = String(state.selectedGroupId);
  }
  $('#send-dialog').showModal();
}

async function submitSend(event) {
  event.preventDefault();
  const stickerId = parseIntSafe($('#send-sticker-id').value);
  const groupId = parseIntSafe($('#send-group').value);
  if (!groupId) return toast('请选择一个群聊', 'warn');
  try {
    const response = await api(`/stickers/${stickerId}/send`, {
      method: 'POST',
      body: JSON.stringify({ group_id: groupId }),
    });
    $('#send-dialog').close();
    for (const message of response.messages || []) await handleNewMessage(message);
    toast('表情包已发送');
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* ------------------------------------------------------------ settings */
async function loadSettings() {
  const data = await api('/settings');
  state.settings = data.settings;
  fillSettings(state.settings);
  const demoBanner = $('#demo-banner');
  if (demoBanner) demoBanner.hidden = !state.settings.demo_mode;
  return state.settings;
}

function fillSettings(settings) {
  if (!settings) return;
  $('#set-base-url').value = settings.llm_base_url || '';
  $('#set-model').value = settings.llm_model || '';
  $('#set-api-key').value = '';
  $('#set-api-key').placeholder = settings.llm_api_key_set
    ? `已保存 ${settings.llm_api_key_hint}，留空不修改`
    : 'sk-...';
  $('#set-key-hint').textContent = settings.llm_api_key_set
    ? '填写新 Key 会覆盖已保存的 Key；留空保持不变。'
    : '尚未配置 API Key';
  const clearKeyButton = $('#clear-key-btn');
  if (clearKeyButton) clearKeyButton.disabled = !settings.llm_api_key_set;
  $('#set-temperature').value = String(settings.llm_temperature ?? 0.8);
  $('#set-temperature-value').textContent = String(settings.llm_temperature ?? 0.8);
  setRangeFill($('#set-temperature'));
  $('#set-max-tokens').value = String(settings.llm_max_tokens ?? 300);
  $('#set-json-mode').checked = settings.llm_json_mode !== false;
  $('#set-persona').value = settings.default_persona || '';
  $('#set-operator-persona').value = settings.operator_persona || '';
  $('#set-operator-extra').value = settings.operator_prompt_extra || '';
  $('#set-group-extra').value = settings.group_prompt_extra || '';
  $('#set-memory-template').value = settings.memory_prompt_template || '';
  $('#set-memory-always').checked = settings.memory_always_include !== false;
  $('#set-operator-system').value = settings.operator_system_prompt || '';
  $('#set-group-system').value = settings.group_system_prompt || '';
  $('#set-default-trigger').value = settings.default_trigger_mode || 'mention';
  $('#set-context').value = String(settings.context_messages ?? 18);
  $('#set-delay').value = String(settings.reply_delay_seconds ?? 2.5);
  $('#set-delay-random').value = String(settings.reply_delay_random_seconds ?? 4);
  $('#set-global-interval').value = String(settings.reply_global_interval_seconds ?? 8);
  $('#set-cooldown-jitter').value = String(settings.reply_cooldown_jitter_seconds ?? 20);
  $('#set-poll-interval').value = String(settings.read_poll_interval_seconds ?? 1);
  $('#set-media-download').checked = settings.read_media_download_enabled !== false;
  $('#set-memory-enabled').checked = settings.memory_enabled !== false;
  $('#set-memory-engine').value = settings.memory_engine || 'auto';
  $('#set-mem0-collection').value = settings.mem0_collection || 'agent_memory';
  $('#set-memory-forgetting').checked = settings.memory_forgetting_enabled !== false;
  $('#set-memory-forgetting-days').value = String(settings.memory_forgetting_days ?? 180);
  const mem0Status = $('#mem0-status');
  if (mem0Status) {
    mem0Status.textContent = `Mem0 引擎：${settings.memory_engine || 'auto'} · 遗忘 ${settings.memory_forgetting_days ?? 180} 天`;
  }
  $('#set-memory-input').value = String(settings.memory_summary_max_input_chars ?? 6000);
  $('#set-memory-tokens').value = String(settings.memory_summary_max_tokens ?? 500);
  $('#set-memory-items').value = String(settings.memory_summary_max_items ?? 5);
  $('#set-memory-context-items').value = String(settings.memory_context_items ?? 8);
  $('#set-memory-context-chars').value = String(settings.memory_context_max_chars ?? 1800);
  $('#set-assistant-steps').value = String(settings.assistant_max_steps ?? 0);
  $('#set-assistant-tokens').value = String(settings.assistant_max_tokens ?? 900);
  $('#set-aliases').value = settings.bot_aliases || '';
  $('#set-auto-login').value = settings.auto_login || '';
  $('#set-tray-close').value = settings.tray_close_action || 'tray';
  $('#set-takeover-hotkey').value = settings.takeover_hotkey || 'Alt+P';
  $('#set-auto-retry-sends').checked = settings.auto_retry_failed_sends !== false;
  $('#set-replica-exe').value = settings.replica_wechat_exe || '';
  $('#set-replica-db-dir').value = settings.replica_db_dir || '';
  $('#set-wcf-exe').value = settings.wcf_wechat_exe || '';
  const timezone = $('#set-timezone');
  timezone.replaceChildren();
  const options = settings.timezone_options || [];
  if (options.length) {
    for (const item of options) timezone.appendChild(new Option(item.label, item.value));
  }
  if (settings.timezone && !Array.from(timezone.options).some((option) => option.value === settings.timezone)) {
    timezone.appendChild(new Option(settings.timezone, settings.timezone));
  }
  timezone.value = settings.timezone || 'Asia/Shanghai';
  const demoBanner = $('#demo-banner');
  if (demoBanner) demoBanner.hidden = !settings.demo_mode;
}

async function saveSettings() {
  const button = $('#save-settings-btn');
  const result = $('#settings-save-result');
  button.disabled = true;
  button.textContent = '保存中…';
  if (result) {
    result.textContent = '';
    result.className = 'inline-result';
  }
  try {
    const payload = {
      llm_base_url: $('#set-base-url').value.trim(),
      llm_model: $('#set-model').value.trim(),
      llm_temperature: parseFloatSafe($('#set-temperature').value, 0.8),
      llm_max_tokens: parseIntSafe($('#set-max-tokens').value, 300),
      llm_json_mode: $('#set-json-mode').checked,
      default_persona: $('#set-persona').value,
      operator_persona: $('#set-operator-persona').value,
      operator_prompt_extra: $('#set-operator-extra').value,
      group_prompt_extra: $('#set-group-extra').value,
      memory_prompt_template: $('#set-memory-template').value,
      memory_always_include: $('#set-memory-always').checked,
      operator_system_prompt: $('#set-operator-system').value,
      group_system_prompt: $('#set-group-system').value,
      default_trigger_mode: $('#set-default-trigger').value,
      context_messages: parseIntSafe($('#set-context').value, 18),
      reply_delay_seconds: parseFloatSafe($('#set-delay').value, 0.4),
      reply_delay_random_seconds: parseFloatSafe($('#set-delay-random').value, 0.9),
      reply_global_interval_seconds: parseFloatSafe($('#set-global-interval').value, 3),
      reply_cooldown_jitter_seconds: parseFloatSafe($('#set-cooldown-jitter').value, 20),
      read_poll_interval_seconds: parseFloatSafe($('#set-poll-interval').value, 0.5),
      read_media_download_enabled: $('#set-media-download').checked,
      memory_enabled: $('#set-memory-enabled').checked,
      memory_engine: $('#set-memory-engine').value,
      mem0_collection: $('#set-mem0-collection').value.trim() || 'agent_memory',
      memory_forgetting_enabled: $('#set-memory-forgetting').checked,
      memory_forgetting_days: parseIntSafe($('#set-memory-forgetting-days').value, 180),
      memory_summary_max_input_chars: parseIntSafe($('#set-memory-input').value, 6000),
      memory_summary_max_tokens: parseIntSafe($('#set-memory-tokens').value, 500),
      memory_summary_max_items: parseIntSafe($('#set-memory-items').value, 5),
      memory_context_items: parseIntSafe($('#set-memory-context-items').value, 8),
      memory_context_max_chars: parseIntSafe($('#set-memory-context-chars').value, 1800),
      assistant_max_steps: parseIntSafe($('#set-assistant-steps').value, 0),
      assistant_max_tokens: parseIntSafe($('#set-assistant-tokens').value, 900),
      timezone: $('#set-timezone').value,
      bot_aliases: $('#set-aliases').value.trim(),
      wcf_wechat_exe: $('#set-wcf-exe').value.trim(),
      replica_wechat_exe: $('#set-replica-exe').value.trim(),
      replica_db_dir: $('#set-replica-db-dir').value.trim(),
      auto_login: $('#set-auto-login').value,
      tray_close_action: $('#set-tray-close').value,
      takeover_hotkey: $('#set-takeover-hotkey').value.trim(),
      auto_retry_failed_sends: $('#set-auto-retry-sends').checked,
    };
    const apiKey = $('#set-api-key').value.trim();
    if (apiKey) payload.llm_api_key = apiKey;
    const saved = await api('/settings', { method: 'PUT', body: JSON.stringify(payload) });
    await loadSettings();
    await loadStats();
    if (result) {
      result.textContent = '已保存';
      result.classList.add('ok');
    }
    if (saved?.hotkey && saved.hotkey.ok === false) {
      const restored = saved.hotkey.restored ? `；已恢复 ${saved.hotkey.restored}` : '';
      toast(`设置已保存，但暂停快捷键不可用：${saved.hotkey.error || '注册失败'}${restored}`, 'warn');
    } else {
      toast('设置已保存');
    }
  } catch (error) {
    if (result) {
      result.textContent = error.message;
      result.classList.add('err');
    }
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '保存全部设置';
  }
}

async function testLLM() {
  const result = $('#llm-test-result');
  const button = $('#test-llm-btn');
  button.disabled = true;
  if (result) {
    result.textContent = '测试中…';
    result.className = 'inline-result';
  }
  try {
    const data = await api('/settings/test-llm', { method: 'POST' });
    const info = data.result;
    if (result) {
      result.textContent = `连接成功 · ${info.model} · ${info.latency_ms}ms · ${info.reply}`;
      result.className = 'inline-result ok';
    }
    toast('模型连接正常');
  } catch (error) {
    if (result) {
      result.textContent = error.message;
      result.className = 'inline-result err';
    }
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function clearApiKey() {
  if (!window.confirm('清除已保存的 API Key？清除后会回退到演示回复模式。')) return;
  try {
    await api('/settings', { method: 'PUT', body: JSON.stringify({ clear_api_key: true }) });
    await loadSettings();
    toast('API Key 已清除');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function forgetMemories() {
  const input = $('#set-memory-forgetting-days');
  const days = Math.max(7, Math.min(3650, parseIntSafe(input?.value, 180)));
  if (!window.confirm(`清理 Mem0 中超过 ${days} 天的旧记忆？此操作不可撤销。`)) return;
  const button = $('#forget-memories-btn');
  if (button) {
    button.disabled = true;
    button.textContent = '清理中…';
  }
  try {
    const data = await api('/assistant/memories/forget', {
      method: 'POST',
      body: JSON.stringify({ days, dry_run: false }),
    });
    const status = $('#mem0-status');
    if (status) {
      status.textContent = data.available
        ? `已清理 ${data.deleted || 0} 条 · 扫描 ${data.scanned || 0} 条 · 阈值 ${data.days || days} 天`
        : 'Mem0 未安装，当前使用 SQLite 记忆回退';
    }
    toast(`已清理 ${data.deleted || 0} 条旧记忆`);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = '立即执行遗忘清理';
    }
  }
}

async function loadStats() {
  try {
    const data = await api('/stats');
    state.stats = data;
    $('#stat-groups').textContent = String(data.groups ?? 0);
    $('#stat-messages').textContent = String(data.messages ?? 0);
    $('#stat-stickers').textContent = String(data.stickers ?? 0);
    $('#stat-replies').textContent = String(data.agent_replies ?? 0);
    $('#stat-db').textContent = formatBytes(data.db_size_bytes);
  } catch (error) {
    console.warn('stats', error);
  }
  try {
    const cache = await api('/llm/usage?hours=24');
    const prompt = Number(cache.prompt_tokens || 0);
    const hit = Number(cache.cache_hit_tokens || 0);
    const rate = Math.round((cache.cache_hit_rate || 0) * 100);
    $('#stat-llm-cache').textContent = prompt
      ? `${rate}%（${hit.toLocaleString('zh-CN')}/${prompt.toLocaleString('zh-CN')}）`
      : '暂无调用';
  } catch (error) {
    console.warn('llm usage', error);
  }
}

async function seedStickers() {
  try {
    const data = await api('/stickers/seed', { method: 'POST' });
    await loadStickers();
    toast(data.created ? `已补齐 ${data.created} 个内置表情` : '已有表情包，未重复生成');
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* ------------------------------------------------------------ wechatauto tools */
function openChatTools() {
  const dialog = $('#chat-tools-dialog');
  if (!dialog) return;
  renderToolGroupHint();
  try {
    if (!dialog.open) dialog.showModal();
  } catch {
    /* already open */
  }
  loadToolLabels().catch(() => {});
  loadToolMoments().catch(() => {});
}

function toolGroup() {
  return currentGroup();
}

function toolRawWho(group) {
  if (!group) return '';
  const external = String(group.external_id || '');
  return external.startsWith('replica:') ? external.slice('replica:'.length) : group.name;
}

function toolPrint(value) {
  const output = $('#tool-output');
  if (!output) return;
  if (typeof value === 'string') {
    output.textContent = value;
    return;
  }
  try {
    output.textContent = JSON.stringify(value, null, 2);
  } catch {
    output.textContent = String(value);
  }
}

async function toolRequest(path, options = {}, successMessage = '') {
  toolPrint(`请求中…\n${path}`);
  try {
    const data = await api(path, options);
    toolPrint(data);
    if (successMessage) toast(successMessage);
    return data;
  } catch (error) {
    toolPrint({ error: error.message });
    toast(error.message, 'error');
    throw error;
  }
}

function toolRequireGroup() {
  const group = toolGroup();
  if (!group) {
    toast('请先在「会话」中选择一个群聊', 'warn');
    return null;
  }
  return group;
}

function renderToolGroupHint() {
  const label = $('#tool-current-group');
  if (!label) return;
  const group = toolGroup();
  label.textContent = group
    ? `当前群：${group.name}（${group.member_count || 0} 人）`
    : '请先在「会话」中选择一个群聊。';
}

function renderToolMoments() {
  const root = $('#tool-moments-list');
  if (!root) return;
  root.replaceChildren();
  if (!state.toolMoments.length) {
    const empty = el('p', 'tool-empty', '没有读取到朋友圈，或当前账号没有可见动态。');
    root.appendChild(empty);
    return;
  }
  state.toolMoments.forEach((feed, index) => {
    const item = el('div', 'tool-item');
    const head = el('div', 'tool-item-head');
    head.appendChild(el('strong', null, feed.nickname || feed.username || '未知发布者'));
    head.appendChild(
      el('span', 'tool-meta', feed.create_time ? formatRelative(new Date(feed.create_time * 1000)) : ''),
    );
    item.appendChild(head);
    item.appendChild(el('p', null, feed.text || '（无正文）'));
    if (feed.location) {
      item.appendChild(el('p', 'tool-meta', `位置：${feed.location.latitude}, ${feed.location.longitude}`));
    }
    const counts = [];
    if (feed.images?.length) counts.push(`图片 ${feed.images.length}`);
    if (feed.videos?.length) counts.push(`视频 ${feed.videos.length}`);
    if (feed.likes?.length) counts.push(`赞 ${feed.likes.length}`);
    if (feed.comments?.length) counts.push(`评论 ${feed.comments.length}`);
    if (counts.length) item.appendChild(el('p', 'tool-meta', counts.join(' · ')));
    if (feed.comments?.length) {
      const comments = feed.comments
        .slice(0, 4)
        .map((comment) => `${comment.nickname || comment.username || '成员'}：${comment.content || ''}`)
        .join('\n');
      item.appendChild(el('p', null, comments));
    }
    const actions = el('div', 'tool-item-actions');
    const like = el('button', 'btn btn-ghost', '赞');
    like.type = 'button';
    like.addEventListener('click', () => toolMomentAction(index, 'like'));
    actions.appendChild(like);
    const unlike = el('button', 'btn btn-ghost', '取消赞');
    unlike.type = 'button';
    unlike.addEventListener('click', () => toolMomentAction(index, 'unlike'));
    actions.appendChild(unlike);
    const comment = el('button', 'btn btn-ghost', '评论');
    comment.type = 'button';
    comment.addEventListener('click', () => toolMomentAction(index, 'comment'));
    actions.appendChild(comment);
    if (feed.images?.length || feed.videos?.length) {
      const download = el('button', 'btn btn-ghost', '下载媒体');
      download.type = 'button';
      download.addEventListener('click', () => toolMomentAction(index, 'download'));
      actions.appendChild(download);
    }
    item.appendChild(actions);
    root.appendChild(item);
  });
}

async function loadToolMoments() {
  try {
    const data = await api('/replica/moments?limit=20');
    state.toolMoments = data.moments || [];
    renderToolMoments();
    toolPrint({ moments: state.toolMoments.length });
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function toolMomentAction(index, action) {
  const feed = state.toolMoments[index];
  if (!feed) return;
  try {
    if (action === 'like' || action === 'unlike') {
      await toolRequest(
        `/replica/moments/${index}/like`,
        {
          method: 'POST',
          body: JSON.stringify({ cancel: action === 'unlike' }),
        },
        action === 'like' ? '已点赞' : '已取消赞',
      );
    } else if (action === 'comment') {
      const text = window.prompt('评论内容');
      if (!text?.trim()) return;
      const replyTo = window.prompt('回复谁（留空表示直接评论）', '') || '';
      await toolRequest(
        `/replica/moments/${index}/comment`,
        {
          method: 'POST',
          body: JSON.stringify({ text: text.trim(), reply_to: replyTo.trim() }),
        },
        '评论已发送',
      );
    } else if (action === 'download') {
      const data = await toolRequest(
        `/replica/moments/${feed.id}/download`,
        { method: 'POST', body: JSON.stringify({ images: true, videos: true }) },
        '朋友圈媒体已下载',
      );
      if (data?.urls?.length) window.open(data.urls[0], '_blank');
    }
  } catch {
    /* handled by toolRequest */
  }
}

function renderToolLabels() {
  const root = $('#tool-labels-list');
  if (!root) return;
  root.replaceChildren();
  if (!state.toolLabels.length) {
    root.appendChild(el('p', 'tool-empty', '还没有标签。'));
    return;
  }
  for (const label of state.toolLabels) {
    const item = el('div', 'tool-item');
    const head = el('div', 'tool-item-head');
    head.appendChild(el('strong', null, label.name || `标签 ${label.label_id || ''}`));
    head.appendChild(el('span', 'tool-meta', label.count ? `${label.count} 人` : ''));
    item.appendChild(head);
    const actions = el('div', 'tool-item-actions');
    const members = el('button', 'btn btn-ghost', '成员');
    members.type = 'button';
    members.addEventListener('click', async () => {
      try {
        const data = await api(`/replica/labels/${encodeURIComponent(label.name)}/members`);
        toolPrint(data.result || data);
      } catch (error) {
        toast(error.message, 'error');
      }
    });
    actions.appendChild(members);
    const remove = el('button', 'btn btn-ghost', '删除');
    remove.type = 'button';
    remove.addEventListener('click', async () => {
      if (!window.confirm(`删除标签「${label.name}」？`)) return;
      try {
        await api(`/replica/labels/${encodeURIComponent(label.name)}`, { method: 'DELETE' });
        toast('标签已删除');
        await loadToolLabels();
      } catch (error) {
        toast(error.message, 'error');
      }
    });
    actions.appendChild(remove);
    item.appendChild(actions);
    root.appendChild(item);
  }
}

async function loadToolLabels() {
  try {
    const data = await api('/replica/labels');
    state.toolLabels = data.labels || [];
    renderToolLabels();
    toolPrint({ labels: state.toolLabels.length });
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderToolContacts() {
  const root = $('#tool-contacts-list');
  if (!root) return;
  root.replaceChildren();
  if (!state.toolContacts.length) return;
  for (const contact of state.toolContacts) {
    const item = el('div', 'tool-item');
    const head = el('div', 'tool-item-head');
    head.appendChild(el('strong', null, contact.remark || contact.nick_name || contact.username));
    head.appendChild(el('span', 'tool-meta', contact.username || ''));
    item.appendChild(head);
    const actions = el('div', 'tool-item-actions');
    const pick = el('button', 'btn btn-ghost', '填到备注对象');
    pick.type = 'button';
    pick.addEventListener('click', () => {
      $('#tool-remark-who').value = contact.username || contact.nick_name || '';
      $('#tool-remark-text').value = contact.remark || '';
    });
    actions.appendChild(pick);
    const friendGroup = el('button', 'btn btn-ghost', '填入加好友群');
    friendGroup.type = 'button';
    friendGroup.addEventListener('click', () => {
      $('#tool-friend-member').value = contact.nick_name || contact.remark || '';
    });
    actions.appendChild(friendGroup);
    item.appendChild(actions);
    root.appendChild(item);
  }
}

async function toolSearchContacts() {
  const q = $('#tool-contact-query').value.trim();
  try {
    const data = await api(`/replica/contacts?q=${encodeURIComponent(q)}`);
    state.toolContacts = data.contacts || [];
    renderToolContacts();
    toolPrint({ contacts: state.toolContacts });
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function toolSendFile(asImage) {
  const group = toolRequireGroup();
  if (!group) return;
  const input = $('#tool-send-file');
  const file = input?.files?.[0];
  if (!file) return toast('请选择文件', 'warn');
  const form = new FormData();
  form.append('file', file);
  try {
    await toolRequest(
      `/replica/groups/${group.id}/send-file?as_image=${asImage ? 'true' : 'false'}`,
      { method: 'POST', body: form },
      asImage ? '图片已发送' : '文件已发送',
    );
  } catch {
    /* handled */
  }
}

function bindToolActions() {
  $('#tool-doctor')?.addEventListener('click', async () => {
    try {
      toolPrint(await api('/replica/doctor'));
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#tool-accounts')?.addEventListener('click', async () => {
    try {
      toolPrint(await api('/replica/accounts'));
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#tool-image-key')?.addEventListener('click', async () => {
    try {
      toolPrint(await api('/replica/media/detect-key', { method: 'POST', body: '{}' }));
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#tool-export-json')?.addEventListener('click', () => toolExport('json'));
  $('#tool-export-sqlite')?.addEventListener('click', () => toolExport('sqlite'));
  $('#tool-send-file-btn')?.addEventListener('click', () => toolSendFile(false));
  $('#tool-send-image-btn')?.addEventListener('click', () => toolSendFile(true));
  $('#tool-at-btn')?.addEventListener('click', async () => {
    const group = toolRequireGroup();
    if (!group) return;
    const members = $('#tool-at-members').value.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
    const text = $('#tool-at-text').value.trim();
    if (!members.length || !text) return toast('请填写成员和内容', 'warn');
    try {
      await toolRequest(
        `/replica/groups/${group.id}/send-at`,
        { method: 'POST', body: JSON.stringify({ members, text }) },
        '@ 消息已发送',
      );
    } catch {
      /* handled */
    }
  });
  $('#tool-quote-btn')?.addEventListener('click', async () => {
    const group = toolRequireGroup();
    if (!group) return;
    const text = $('#tool-quote-text').value.trim();
    if (!text) return toast('请填写回复内容', 'warn');
    try {
      await toolRequest(
        `/replica/groups/${group.id}/quote`,
        {
          method: 'POST',
          body: JSON.stringify({ text, target_text: $('#tool-quote-target').value.trim() }),
        },
        '引用消息已发送',
      );
    } catch {
      /* handled */
    }
  });
  $('#tool-poke')?.addEventListener('click', () => toolSimpleAction('poke'));
  $('#tool-recall')?.addEventListener('click', () => toolSimpleAction('recall'));
  $('#tool-chat-info')?.addEventListener('click', async () => {
    try {
      toolPrint(await api('/replica/chat-info'));
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#tool-forward-btn')?.addEventListener('click', async () => {
    const group = toolRequireGroup();
    if (!group) return;
    const to = $('#tool-forward-to').value.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
    if (!to.length) return toast('请填写转发目标', 'warn');
    try {
      await toolRequest(
        '/replica/forward',
        {
          method: 'POST',
          body: JSON.stringify({
            to,
            chat: toolRawWho(group),
            match: $('#tool-forward-match').value.trim(),
          }),
        },
        '转发请求已提交',
      );
    } catch {
      /* handled */
    }
  });
  $('#tool-moments-refresh')?.addEventListener('click', loadToolMoments);
  $('#tool-interactions')?.addEventListener('click', async () => {
    try {
      toolPrint(await api('/replica/moments/interactions?limit=50'));
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#tool-labels-refresh')?.addEventListener('click', loadToolLabels);
  $('#tool-label-create')?.addEventListener('click', async () => {
    const name = $('#tool-label-name').value.trim();
    if (!name) return toast('请填写标签名', 'warn');
    try {
      await toolRequest('/replica/labels', { method: 'POST', body: JSON.stringify({ name }) }, '标签已创建');
      await loadToolLabels();
    } catch {
      /* handled */
    }
  });
  $('#tool-label-send')?.addEventListener('click', async () => {
    const label = $('#tool-label-send-name').value.trim();
    const text = $('#tool-label-send-text').value.trim();
    if (!label || !text) return toast('请填写标签和内容', 'warn');
    try {
      await toolRequest(
        `/replica/labels/${encodeURIComponent(label)}/send`,
        { method: 'POST', body: JSON.stringify({ text }) },
        '已发送到标签',
      );
    } catch {
      /* handled */
    }
  });
  $('#tool-contact-search')?.addEventListener('click', toolSearchContacts);
  $('#tool-contact-query')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') toolSearchContacts();
  });
  $('#tool-remark-btn')?.addEventListener('click', async () => {
    const who = $('#tool-remark-who').value.trim();
    if (!who) return toast('请填写备注对象', 'warn');
    const clear = $('#tool-remark-clear').checked;
    try {
      await toolRequest(
        '/replica/remark',
        {
          method: 'POST',
          body: JSON.stringify({
            who,
            remark: clear ? '' : $('#tool-remark-text').value.trim(),
            allow_empty: clear,
          }),
        },
        '备注修改请求已提交',
      );
    } catch {
      /* handled */
    }
  });
  $('#tool-friend-btn')?.addEventListener('click', async () => {
    const group = $('#tool-friend-group').value.trim();
    const member = $('#tool-friend-member').value.trim();
    if (!group || !member) return toast('请填写群名和成员', 'warn');
    try {
      await toolRequest(
        '/replica/add-friend',
        { method: 'POST', body: JSON.stringify({ group, member }) },
        '好友申请已提交',
      );
    } catch {
      /* handled */
    }
  });
  $('#tool-voice')?.addEventListener('click', () => toolCallPrompt('voice-call', false));
  $('#tool-video')?.addEventListener('click', () => toolCallPrompt('voice-call', true));
  $('#tool-probe-labels')?.addEventListener('click', async () => {
    try {
      toolPrint(await api('/replica/labels/probe', { method: 'POST', body: '{}' }));
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#tool-probe-remark')?.addEventListener('click', async () => {
    const who = window.prompt('要探测的联系人（昵称/wxid）', $('#tool-remark-who').value.trim());
    if (!who) return;
    try {
      toolPrint(await api('/replica/remark/probe', { method: 'POST', body: JSON.stringify({ who }) }));
    } catch (error) {
      toast(error.message, 'error');
    }
  });
}

async function toolExport(fmt) {
  const group = toolRequireGroup();
  if (!group) return;
  const limit = parseIntSafe($('#tool-export-limit').value, 0);
  const query = new URLSearchParams({ fmt });
  if (limit > 0) query.set('limit', String(limit));
  try {
    const data = await api(`/replica/groups/${group.id}/export?${query.toString()}`);
    toolPrint(data);
    if (data.url) window.open(data.url, '_blank');
    toast('聊天记录已导出');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function toolSimpleAction(action) {
  const group = toolRequireGroup();
  if (!group) return;
  try {
    await toolRequest(
      `/replica/${action}`,
      { method: 'POST', body: JSON.stringify({ who: toolRawWho(group) }) },
      action === 'poke' ? '已拍一拍' : '撤回请求已提交',
    );
  } catch {
    /* handled */
  }
}

async function toolCallPrompt(action, video) {
  const who = window.prompt(
    video ? '视频通话对象（昵称/wxid）' : '语音通话对象（昵称/wxid）',
    toolRawWho(toolGroup()) || '',
  );
  if (!who) return;
  try {
    toast(video ? '正在发起视频通话…' : '正在发起语音通话…');
    const data = await api(`/replica/${action}`, {
      method: 'POST',
      body: JSON.stringify({ who, video }),
    });
    toolPrint(data);
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* ------------------------------------------------------------ operator agent */
function assistantRichContent(bubble, text) {
  const value = String(text || '').replace(
    /(?<!\]\()(\/media\/assistant\/[^\s)]+?\.(?:png|jpe?g|gif|webp))/gi,
    '![图片]($1)',
  );
  const imageRe = /!\[([^\]]*)\]\(([^)]+)\)/g;
  let last = 0;
  let match;
  while ((match = imageRe.exec(value)) !== null) {
    if (match.index > last) {
      bubble.appendChild(document.createTextNode(value.slice(last, match.index)));
    }
    const url = match[2].trim();
    if (url.startsWith('/media/') || url.startsWith('http://') || url.startsWith('https://')) {
      const img = el('img', 'assistant-image');
      img.src = url;
      img.alt = match[1] || '图片';
      img.loading = 'eager';
      img.addEventListener('click', () => openImageViewer(url, match[1] || '图片'));
      bubble.appendChild(img);
    } else {
      bubble.appendChild(document.createTextNode(match[0]));
    }
    last = imageRe.lastIndex;
  }
  if (last < value.length) bubble.appendChild(document.createTextNode(value.slice(last)));
}

function assistantMessageNode(row) {
  if (row.role === 'tool') {
    const tool = el('div', 'assistant-tool');
    tool.textContent = `⚙ ${row.tool_name || 'tool'}\n${row.tool_output || row.content || ''}`;
    return tool;
  }
  const wrapper = el('div', `assistant-msg ${row.role === 'user' ? 'user' : 'assistant'}`);
  const avatar = el('span', 'assistant-avatar', row.role === 'user' ? '你' : 'AI');
  const bubble = el('div', 'assistant-bubble');
  if (row.meta?.source === 'wechat-contact') {
    bubble.appendChild(el('span', 'assistant-source', '微信私聊'));
  }
  if (row.kind === 'image' && row.media_url) {
    const img = el('img', 'assistant-image');
    img.src = row.media_url;
    img.alt = '上传的图片';
    img.loading = 'eager';
    img.addEventListener('click', () => openImageViewer(row.media_url, '图片'));
    bubble.appendChild(img);
    bubble.appendChild(document.createElement('br'));
  }
  assistantRichContent(bubble, row.content || '');
  wrapper.append(avatar, bubble);
  return wrapper;
}

function renderAssistantMessages() {
  const root = $('#assistant-messages');
  if (!root) return;
  if (!state.assistantHistory.length) {
    const empty = el('div', 'assistant-empty');
    if (state.assistantConversation !== 'main') {
      empty.innerHTML = `${icon('chat', 26)}<h3>还没有私聊记录</h3><p>在微信里给这个联系人发一条消息，或切换到其他私聊。</p>`;
    } else {
      empty.innerHTML = `${icon('sparkles', 26)}<h3>可以直接下命令</h3><p>例如：“检查工作区”“创建一个 MCP 项目”“写一个技能整理群聊记忆”。</p>`;
    }
    root.replaceChildren(empty);
    return;
  }
  root.replaceChildren(...state.assistantHistory.map(assistantMessageNode));
  root.scrollTop = root.scrollHeight;
}

async function loadAssistantStatus() {
  try {
    const data = await api('/assistant/status');
    const pill = $('#assistant-admin-pill');
    if (pill) {
      pill.dataset.state = data.admin ? 'online' : 'qr';
      const text = pill.querySelector('span');
      if (text) text.textContent = data.admin ? '管理员权限' : '普通权限 · 可 UAC 提权';
    }
    const workspace = $('#assistant-workspace-path');
    if (workspace) workspace.textContent = `工作区：${data.workspace}`;
    const model = $('#assistant-model-hint');
    if (model) {
      model.textContent = data.llm_configured
        ? `模型：${data.model || '未设置'} · 记忆 ${data.memories} 条 · 技能 ${data.skills} 个`
        : '未配置 API Key，先到设置里填写模型';
    }
    const tasks = $('#assistant-task-count');
    if (tasks) tasks.textContent = `${data.tasks || 0} 个任务`;
    const mem0 = $('#mem0-status');
    if (mem0 && data.memory) {
      const memory = data.memory;
      mem0.textContent = memory.available
        ? `Mem0 ${memory.enabled ? '已启用' : '已安装但未启用'} · ${memory.engine} · 遗忘 ${memory.forgetting_days} 天`
        : 'Mem0 未安装，当前使用 SQLite 记忆回退';
    }
    const maxSteps = $('#assistant-max-steps');
    if (maxSteps && document.activeElement !== maxSteps) {
      maxSteps.value = String(data.max_steps ?? 0);
    }
    const shellMode = $('#assistant-shell-mode');
    if (shellMode) shellMode.textContent = data.admin ? '管理员权限' : '普通权限 · 勾选提权';
    const restart = $('#assistant-admin-restart');
    if (restart) restart.hidden = Boolean(data.admin);
    state.assistantStatus = data;
  } catch (error) {
    console.warn('assistant status', error);
  }
}

async function saveAssistantMaxSteps() {
  const input = $('#assistant-max-steps');
  if (!input) return;
  const value = Math.max(0, Math.min(500, parseIntSafe(input.value, 0)));
  input.value = String(value);
  try {
    await api('/settings', {
      method: 'PUT',
      body: JSON.stringify({ assistant_max_steps: value }),
    });
    toast(
      value === 0
        ? '最大步数已设为不限制（安全上限 500 步 / 30 分钟）'
        : `最大调用步数已设为 ${value}`,
    );
  } catch (error) {
    toast(error.message, 'error');
    await loadAssistantStatus();
  }
}

async function loadAssistantHistory() {
  try {
    const conversation = state.assistantConversation;
    const data = await api('/assistant/history?session_id=main&limit=100');
    if (conversation !== 'main' || state.assistantConversation !== 'main') return;
    state.assistantHistory = data.messages || [];
    renderAssistantMessages();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function assistantContactById(contactId) {
  return (
    state.assistantContacts.find((item) => item.id === contactId) ||
    state.contacts.find((item) => item.id === contactId) ||
    null
  );
}

function populateAssistantConversationOptions() {
  const select = $('#assistant-conversation-select');
  if (!select) return;
  const current = state.assistantConversation || 'main';
  const contacts = state.assistantContacts.length
    ? state.assistantContacts
    : state.contacts;
  const options = [new Option('主对话（Operator）', 'main')];
  const currentContactId = state.assistantConversationContactId;
  const all = contacts || [];
  const selected = all.find((contact) => contact.id === currentContactId);
  const recent = all
    .filter(
      (contact) =>
        contact.id !== currentContactId &&
        (contact.last_message || contact.is_admin),
    )
    .sort((left, right) => {
      const a = left.last_message?.created_at
        ? new Date(left.last_message.created_at).getTime()
        : 0;
      const b = right.last_message?.created_at
        ? new Date(right.last_message.created_at).getTime()
        : 0;
      return b - a;
    })
    .slice(0, 29);
  if (selected) recent.unshift(selected);
  for (const contact of recent) {
    const name = contact.name || contact.remark || contact.wxid || `联系人 ${contact.id}`;
    options.push(new Option(`私聊 · ${name}`, `contact:${contact.id}`));
  }
  select.replaceChildren(...options);
  if (Array.from(select.options).some((option) => option.value === current)) {
    select.value = current;
  } else {
    select.value = 'main';
    state.assistantConversation = 'main';
    state.assistantConversationContactId = null;
  }
}

async function loadAssistantConversationOptions() {
  populateAssistantConversationOptions();
  if (state.assistantContacts.length || state.contacts.length) return;
  try {
    const data = await api('/contacts');
    state.assistantContacts = data.contacts || [];
    state.contacts = state.assistantContacts;
    populateAssistantConversationOptions();
  } catch (error) {
    console.warn('assistant conversations', error);
  }
}

async function loadContactConversation(contactId) {
  const contact = assistantContactById(contactId);
  const input = $('#assistant-input');
  if (input) {
    input.placeholder = `发送微信私聊给 ${contact?.name || contact?.remark || contact?.wxid || '联系人'}…`;
  }
  const send = $('#assistant-send');
  if (send) send.textContent = '发送到微信';
  const stop = $('#assistant-stop');
  if (stop) stop.hidden = true;
  const data = await api(`/contacts/${contactId}/messages?limit=150`);
  if (
    state.assistantConversation !== `contact:${contactId}` ||
    state.assistantConversationContactId !== Number(contactId)
  ) {
    return;
  }
  const rows = (data.messages || []).map((message) => {
    const kind = message.kind || 'text';
    let content = message.content || '';
    if (!content) {
      content = kind === 'sticker' ? '[表情包]' : kind === 'image' ? '[图片]' : `[${kind}]`;
    }
    return {
      role: message.is_self ? 'assistant' : 'user',
      content,
      kind,
      media_url: message.media_url || '',
      created_at: message.created_at || null,
      meta: {
        ...(message.meta || {}),
        source: message.is_self ? (message.meta?.source || 'wechat') : 'wechat-contact',
        sender_name: message.sender_name || '',
      },
    };
  });
  rows.sort((left, right) => {
    const a = left.created_at ? new Date(left.created_at).getTime() : 0;
    const b = right.created_at ? new Date(right.created_at).getTime() : 0;
    return a - b;
  });
  state.assistantHistory = rows;
  renderAssistantMessages();
  const root = $('#assistant-messages');
  if (root) root.scrollTop = root.scrollHeight;
}

async function switchAssistantConversation(value) {
  const match = /^contact:(\d+)$/.exec(String(value || ''));
  state.assistantConversation = match ? `contact:${match[1]}` : 'main';
  state.assistantConversationContactId = match ? Number(match[1]) : null;
  const step = document.querySelector('.assistant-step-control');
  if (step) step.hidden = Boolean(match);
  const attach = $('#assistant-attach');
  if (attach) attach.hidden = Boolean(match);
  const stop = $('#assistant-stop');
  if (match) {
    if (stop) stop.hidden = true;
    try {
      await loadContactConversation(Number(match[1]));
    } catch (error) {
      toast(error.message, 'error');
    }
    return;
  }
  if (step) step.hidden = false;
  if (attach) attach.hidden = false;
  const input = $('#assistant-input');
  if (input) input.placeholder = '告诉 Agent 要做什么…（Enter 发送，Shift + Enter 换行）';
  const send = $('#assistant-send');
  if (send) send.textContent = state.assistantBusy ? '插入' : '发送';
  if (stop) stop.hidden = !state.assistantBusy;
  await loadAssistantHistory();
}

async function uploadAssistantImage(file) {
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  try {
    const data = await api('/assistant/upload', { method: 'POST', body: form });
    state.assistantAttachment = data;
    toast(
      data.ocr?.text
        ? `图片已附加，OCR 识别到 ${data.ocr.text.length} 字`
        : '图片已附加，但未识别到文字',
    );
    const input = $('#assistant-input');
    if (input && !input.value.trim()) {
      input.placeholder = '图片已附加；补充你的问题，或直接发送让 Agent 读图…';
    }
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderAssistantProgress() {
  const root = $('#assistant-progress');
  if (!root) return;
  root.replaceChildren();
  if (!state.assistantProgressLines.length) {
    root.hidden = true;
    return;
  }
  root.hidden = false;
  for (const line of state.assistantProgressLines) {
    const item = el('div', `assistant-progress-line${line.cls ? ` ${line.cls}` : ''}`);
    item.append(el('b', null, '›'), el('span', null, line.text));
    root.appendChild(item);
  }
  root.scrollTop = root.scrollHeight;
}

function addAssistantProgress(text, cls = '') {
  state.assistantProgressLines.push({ text, cls });
  if (state.assistantProgressLines.length > 200) {
    state.assistantProgressLines.splice(0, state.assistantProgressLines.length - 200);
  }
  renderAssistantProgress();
}

function assistantStepLabel(data) {
  return data?.unlimited
    ? `第 ${data.step} 步`
    : `第 ${data.step}/${data.max_steps} 步`;
}

function handleAssistantProgress(data) {
  if (!data || data.request_id !== state.assistantProgressId) return;
  let text = data.message || '';
  let cls = '';
  if (data.phase === 'thinking') {
    text = `${assistantStepLabel(data)}：规划下一步`;
  } else if (data.phase === 'tool') {
    text = `${assistantStepLabel(data)}：调用 ${data.tool}`;
    if (data.thought) text += ` · ${data.thought}`;
  } else if (data.phase === 'tool_result') {
    text = `${assistantStepLabel(data)}：${data.ok === false ? '失败' : '完成'} ${data.tool}`;
    if (data.ok === false) cls = 'error';
  } else if (data.phase === 'queued') {
    text = data.message || '收到新要求，已插入当前任务';
    cls = 'done';
  } else if (data.phase === 'final') {
    text = '任务完成，正在整理结果';
    cls = 'done';
  } else if (data.phase === 'start') {
    text =
      data.message ||
      (data.unlimited ? '开始处理，步数不限' : `开始处理，最多 ${data.max_steps} 步`);
  }
  if (text) addAssistantProgress(text, cls);
}

async function sendAssistantMessage() {
  const input = $('#assistant-input');
  const text = input?.value.trim() || '';
  if (!text) return;
  const button = $('#assistant-send');
  const stopButton = $('#assistant-stop');

  if (state.assistantConversation !== 'main') {
    const contactId = state.assistantConversationContactId;
    if (!contactId) return;
    input.value = '';
    try {
      await api(`/contacts/${contactId}/send`, {
        method: 'POST',
        body: JSON.stringify({ text }),
      });
      await loadContactConversation(contactId);
      toast('已发送到微信私聊');
    } catch (error) {
      input.value = text;
      toast(error.message, 'error');
    }
    return;
  }

  // While a task is running, keep the same session and queue the message.
  // The backend treats it as a mid-task inserted requirement, not a new task.
  if (state.assistantBusy) {
    try {
      const data = await api('/assistant/chat', {
        method: 'POST',
        body: JSON.stringify({
          message: text,
          session_id: 'main',
          attachment_path: state.assistantAttachment?.path || '',
        }),
      });
      input.value = '';
      state.assistantAttachment = null;
      if (data.queued) {
        addAssistantProgress('已插入当前任务', 'done');
        toast('已插入当前任务，Agent 下一步会看到');
      } else {
        state.assistantBusy = false;
        const send = $('#assistant-send');
        if (send) {
          send.disabled = false;
          send.textContent = '发送';
        }
        if (stopButton) stopButton.hidden = true;
        addAssistantProgress('上一任务已结束，已作为新任务处理', 'done');
        toast('上一任务已结束，已作为新任务处理');
      }
      await loadAssistantHistory();
    } catch (error) {
      toast(error.message, 'error', 6000);
    }
    return;
  }

  let queuedRun = false;
  state.assistantBusy = true;
  if (button) {
    // Keep the send button usable: clicking it while a task is running
    // inserts the new message into the current task instead of starting
    // a second one.
    button.disabled = false;
    button.textContent = '插入';
  }
  if (stopButton) {
    stopButton.hidden = false;
    stopButton.disabled = false;
    stopButton.textContent = '停止';
  }
  input.value = '';
  const progressId =
    (window.crypto && crypto.randomUUID && crypto.randomUUID()) ||
    `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  state.assistantProgressId = progressId;
  state.assistantProgressLines = [];
  renderAssistantProgress();
  addAssistantProgress('已收到任务，开始处理');
  const root = $('#assistant-messages');
  if (root) {
    const thinking = el('div', 'assistant-tool', 'Agent 正在规划并执行工具调用…');
    thinking.id = 'assistant-thinking';
    root.appendChild(thinking);
    root.scrollTop = root.scrollHeight;
  }
  try {
    const data = await api('/assistant/chat', {
      method: 'POST',
      body: JSON.stringify({
        message: text,
        session_id: 'main',
        progress_id: progressId,
        attachment_path: state.assistantAttachment?.path || '',
      }),
    });
    if (data.queued) {
      queuedRun = true;
      addAssistantProgress('已插入当前任务', 'done');
      toast('已插入当前任务，Agent 下一步会看到');
      await loadAssistantHistory();
      return;
    }
    state.assistantAttachment = null;
    await loadAssistantHistory();
    await loadAssistantStatus();
    addAssistantProgress(
      data.trace?.length ? `完成，共 ${data.trace.length} 次工具调用` : '完成',
      'done',
    );
    if (data.trace?.length) toast(`完成 ${data.trace.length} 次工具调用`);
  } catch (error) {
    $('#assistant-thinking')?.remove();
    addAssistantProgress(`失败：${error.message}`, 'error');
    toast(error.message, 'error', 6000);
  } finally {
    const inContact = state.assistantConversation !== 'main';
    if (queuedRun) {
      state.assistantBusy = true;
      if (button) {
        button.disabled = false;
        button.textContent = inContact ? '发送到微信' : '插入';
      }
      if (stopButton) stopButton.hidden = inContact;
    } else {
      state.assistantBusy = false;
      if (button) {
        button.disabled = false;
        button.textContent = inContact ? '发送到微信' : '发送';
      }
      if (stopButton) {
        stopButton.hidden = true;
        stopButton.disabled = false;
        stopButton.textContent = '停止';
      }
    }
  }
}

async function stopAssistant() {
  const stopButton = $('#assistant-stop');
  if (stopButton) {
    stopButton.disabled = true;
    stopButton.textContent = '停止中…';
  }
  try {
    const data = await api('/assistant/stop', {
      method: 'POST',
      body: JSON.stringify({ session_id: 'main' }),
    });
    toast(data.message || (data.stopped ? '已请求停止' : '当前没有任务'));
    if (data.message) {
      addAssistantProgress(data.message, data.stopped ? 'done' : 'error');
    }
    if (!data.stopped) {
      state.assistantBusy = false;
      const send = $('#assistant-send');
      if (send) {
        send.disabled = false;
        send.textContent = '发送';
      }
      if (stopButton) stopButton.hidden = true;
    }
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (stopButton) {
      stopButton.disabled = false;
      stopButton.textContent = '停止';
    }
  }
}

function switchAssistantPanel(name, options = {}) {
  if (!document.querySelector(`[data-assistant-panel-body="${name}"]`)) {
    name = 'workspace';
  }
  state.assistantPanel = name;
  $$('.assistant-side-tabs button').forEach((button) => {
    button.classList.toggle('active', button.dataset.assistantPanel === name);
  });
  $$('[data-assistant-panel-body]').forEach((panel) => {
    panel.hidden = panel.dataset.assistantPanelBody !== name;
  });
  if (
    options.syncHash !== false &&
    state.appReady &&
    state.currentTab === 'assistant'
  ) {
    const next = routeFor('assistant');
    if (location.hash !== next) history.replaceState(null, '', next);
  }
  if (state.opencodeTaskTimer) {
    clearInterval(state.opencodeTaskTimer);
    state.opencodeTaskTimer = null;
  }
  if (name === 'opencode') {
    state.opencodeTaskTimer = setInterval(() => {
      if (state.assistantPanel === 'opencode' && state.currentTab === 'assistant') {
        loadOpenCodeTasks().catch(() => {});
      }
    }, 4000);
  }
}

let dshPollTimer = null;

async function loadDSH() {
  const statusLine = $('#dsh-status-line');
  const jobLine = $('#dsh-job-line');
  const logRoot = $('#dsh-log');
  try {
    const data = await api('/dsh/status');
    const st = data.dsh || {};
    const runtime = st.runtime || {};
    if (!state.settings) {
      try {
        const settingsData = await api('/settings');
        state.settings = settingsData.settings || {};
      } catch {
        /* ignore */
      }
    }
    const enabled = $('#dsh-enabled');
    if (enabled) enabled.checked = state.settings?.dsh_enabled === true;
    const channel = $('#dsh-channel');
    if (channel) channel.value = state.settings?.dsh_channel || data.channel || 'stable';
    const groupChat = $('#dsh-group-chat-engine');
    if (groupChat) groupChat.value = state.settings?.group_chat_engine || 'dsh';
    if (statusLine) {
      const version = st.version || runtime.version || '';
      const parts = [];
      if (st.installed) parts.push(`已安装 v${version}`);
      else parts.push('未安装');
      parts.push(`当前引擎：${data.engine || '—'}`);
      if (st.running) parts.push(`运行中 ${st.running}`);
      if (st.update_available) parts.push(`可更新到 ${st.latest}`);
      statusLine.textContent = parts.join(' · ');
    }
    const job = st.job || runtime.job;
    if (jobLine) {
      if (job) {
        jobLine.textContent = `安装任务：${job.state || ''}${job.version ? ` · ${job.version}` : ''}${job.error ? ` · ${job.error}` : ''}`;
      } else if (st.last_error) {
        jobLine.textContent = `最近错误：${st.last_error}`;
      } else {
        jobLine.textContent = '';
      }
    }
    if (logRoot) {
      logRoot.replaceChildren();
      const lines = (job && job.log) || [];
      if (!lines.length) {
        logRoot.appendChild(el('p', 'muted', '暂无安装日志。'));
      } else {
        for (const line of lines.slice(-40)) {
          logRoot.appendChild(el('p', 'muted', String(line)));
        }
      }
    }
    if (dshPollTimer) {
      clearTimeout(dshPollTimer);
      dshPollTimer = null;
    }
    if (st.installing) {
      dshPollTimer = setTimeout(loadDSH, 3000);
    }
  } catch (error) {
    if (statusLine) statusLine.textContent = `读取失败：${error.message}`;
  }
}

async function loadPlugins() {
  if (!$('#plugin-catalog')) return;
  const catalog = await api('/dsh/plugins');
  state.plugins = catalog;
  renderPluginStats();
  renderPluginCategories();
  renderPluginCatalog();
  renderInstalledPlugins();
}

function renderPluginStats() {
  const data = state.plugins || {};
  const counts = data.counts || {};
  const runtime = data.runtime || {};
  const set = (id, value) => {
    const node = $(id);
    if (node) node.textContent = value;
  };
  set('#plugin-stat-version', runtime.version ? `v${runtime.version}` : '—');
  set('#plugin-stat-builtin', String(counts.builtin ?? '—'));
  set('#plugin-stat-active', String(counts.active ?? '—'));
  set(
    '#plugin-stat-custom',
    `${counts.imported ?? 0} / ${counts.generated ?? 0}`,
  );
}

function renderPluginCategories() {
  const root = $('#plugin-categories');
  if (!root) return;
  const rows = (state.plugins?.builtin || []).map((row) => row.category || '其他');
  const categories = ['全部', ...Array.from(new Set(rows))];
  root.replaceChildren();
  for (const category of categories) {
    const button = el(
      'button',
      `tag-chip${state.pluginCategory === category ? ' active' : ''}`,
      category === '全部' ? '全部' : category,
    );
    button.type = 'button';
    button.addEventListener('click', () => {
      state.pluginCategory = category;
      renderPluginCategories();
      renderPluginCatalog();
    });
    root.appendChild(button);
  }
}

function pluginMatches(row) {
  if (state.pluginActiveOnly && !row.active) return false;
  if (state.pluginCategory !== '全部' && (row.category || '其他') !== state.pluginCategory) {
    return false;
  }
  const query = state.pluginQuery.trim().toLowerCase();
  if (!query) return true;
  return `${row.id || ''} ${row.name || ''} ${row.description || ''}`
    .toLowerCase()
    .includes(query);
}

function pluginCard(row, options = {}) {
  const card = el('article', `plugin-row${row.disabled ? ' is-disabled' : ''}`);
  const iconWrap = el('div', 'plugin-row-icon');
  iconWrap.innerHTML = icon('plug', 18);
  card.appendChild(iconWrap);
  const body = el('div', 'plugin-row-body');
  const title = el('div', 'plugin-row-title');
  title.appendChild(el('strong', null, row.id || 'plugin'));
  title.appendChild(
    el(
      'span',
      `plugin-badge${row.active ? ' accent' : row.disabled ? ' muted' : ''}`,
      row.active ? '启用中' : row.disabled ? '已停用' : '可选',
    ),
  );
  body.appendChild(title);
  body.appendChild(el('code', 'plugin-package', row.name || ''));
  if (row.description) body.appendChild(el('p', 'plugin-desc', row.description));
  card.appendChild(body);
  const meta = el('div', 'plugin-meta');
  meta.appendChild(el('span', 'plugin-cat', row.category || '其他'));
  if (row.version) meta.appendChild(el('span', 'plugin-version', `v${row.version}`));
  if (options.toggle) {
    const button = el(
      'button',
      'text-btn plugin-toggle',
      row.disabled ? '启用' : '停用',
    );
    button.type = 'button';
    button.addEventListener('click', () => togglePlugin(row.id, Boolean(row.disabled)));
    meta.appendChild(button);
  } else {
    meta.appendChild(el('span', 'plugin-readonly', '内置只读'));
  }
  card.appendChild(meta);
  return card;
}

function renderPluginCatalog() {
  const root = $('#plugin-catalog');
  if (!root) return;
  const rows = (state.plugins?.builtin || []).filter(pluginMatches);
  root.replaceChildren();
  if (!rows.length) {
    const empty = $('#plugin-empty');
    if (empty) empty.hidden = false;
    return;
  }
  const empty = $('#plugin-empty');
  if (empty) empty.hidden = true;
  for (const row of rows) root.appendChild(pluginCard(row));
}

function renderInstalledPlugins() {
  const root = $('#plugin-installed');
  if (!root) return;
  const installed = state.plugins?.installed || [];
  const generated = state.plugins?.generated || [];
  const generatedNames = new Set(generated.map((item) => item.name));
  const customInstalled = installed.filter((row) => !generatedNames.has(row.name));
  const count = $('#plugin-installed-count');
  if (count) count.textContent = String(generated.length + customInstalled.length);
  root.replaceChildren();
  if (!installed.length && !generated.length) {
    root.appendChild(el('p', 'muted', '还没有导入或生成的插件。'));
    return;
  }
  const rows = [
    ...generated.map((row) => ({ ...row, source: 'studio' })),
    ...customInstalled,
  ];
  for (const row of rows) {
    const item = el('div', 'plugin-mini');
    const info = el('div', 'plugin-mini-info');
    info.appendChild(el('strong', null, row.name || row.slug || 'plugin'));
    info.appendChild(
      el(
        'span',
        'plugin-mini-desc',
        `${row.source === 'studio' ? 'DSH 生成' : row.source === 'native' ? '群伴原生' : '导入'}${row.version ? ` · v${row.version}` : ''}`,
      ),
    );
    item.appendChild(info);
    if (row.source !== 'native') {
      const button = el(
        'button',
        'text-btn',
        row.disabled ? '启用' : '停用',
      );
      button.type = 'button';
      button.disabled = !row.installed;
      button.addEventListener('click', () => togglePlugin(row.id, Boolean(row.disabled)));
      item.appendChild(button);
    }
    root.appendChild(item);
  }
}

async function togglePlugin(id, enabled) {
  try {
    await api('/dsh/plugins/toggle', {
      method: 'POST',
      body: JSON.stringify({ id, enabled }),
    });
    toast(enabled ? '插件已启用，下次 DSH 任务生效' : '插件已停用');
    await loadPlugins();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function setPluginJob(job) {
  state.pluginJobId = job?.id || '';
  const card = $('#plugin-job-card');
  if (!card) return;
  if (!job) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const title = $('#plugin-job-title');
  const status = $('#plugin-job-status');
  const log = $('#plugin-job-log');
  if (title) title.textContent = job.title || '插件任务';
  if (status) {
    status.textContent =
      job.status === 'done' ? '完成' : job.status === 'error' ? '失败' : '运行中';
    status.className = `plugin-badge${job.status === 'error' ? ' danger' : job.status === 'done' ? ' accent' : ''}`;
  }
  if (log) {
    log.replaceChildren();
    for (const line of job.log || []) {
      log.appendChild(el('p', 'plugin-log-line', line));
    }
    if (job.error) log.appendChild(el('p', 'plugin-log-line danger', job.error));
  }
}

async function pollPluginJob(jobId) {
  if (state.pluginJobTimer) {
    clearTimeout(state.pluginJobTimer);
    state.pluginJobTimer = null;
  }
  try {
    const data = await api(`/dsh/plugins/jobs/${encodeURIComponent(jobId)}`);
    const job = data.job || {};
    setPluginJob(job);
    if (job.status === 'running') {
      state.pluginJobTimer = setTimeout(() => pollPluginJob(jobId), 1500);
      return;
    }
    if (job.status === 'done') {
      toast('插件任务完成');
      await loadPlugins();
    } else {
      toast(job.error || '插件任务失败', 'error');
    }
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function startPluginImport() {
  const spec = $('#plugin-import-spec')?.value.trim() || '';
  if (!spec) {
    toast('请填写包名或本地路径', 'error');
    return;
  }
  const button = $('#plugin-import-btn');
  if (button) button.disabled = true;
  try {
    const data = await api('/dsh/plugins/import', {
      method: 'POST',
      body: JSON.stringify({
        spec,
        enabled: $('#plugin-import-enabled')?.checked !== false,
      }),
    });
    setPluginJob(data.job);
    pollPluginJob(data.job.id);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function startPluginCreate() {
  const name = $('#plugin-create-name')?.value.trim() || '';
  const description = $('#plugin-create-desc')?.value.trim() || '';
  const requirements = $('#plugin-create-requirements')?.value.trim() || '';
  if (!name) {
    toast('请填写插件名', 'error');
    return;
  }
  if (!requirements) {
    toast('请描述插件需求', 'error');
    return;
  }
  const button = $('#plugin-create-btn');
  if (button) button.disabled = true;
  try {
    const data = await api('/dsh/plugins/create', {
      method: 'POST',
      body: JSON.stringify({
        name,
        description,
        requirements,
        use_agent: $('#plugin-create-agent')?.checked !== false,
        enabled: $('#plugin-create-enabled')?.checked !== false,
      }),
    });
    setPluginJob(data.job);
    pollPluginJob(data.job.id);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

function bindPluginEvents() {
  $('#plugins-refresh')?.addEventListener('click', () => {
    loadPlugins().catch((error) => toast(error.message, 'error'));
  });
  $('#plugin-search')?.addEventListener('input', (event) => {
    state.pluginQuery = event.target.value;
    renderPluginCatalog();
  });
  $('#plugin-active-only')?.addEventListener('change', (event) => {
    state.pluginActiveOnly = Boolean(event.target.checked);
    renderPluginCatalog();
  });
  $('#plugin-import-btn')?.addEventListener('click', startPluginImport);
  $('#plugin-create-btn')?.addEventListener('click', startPluginCreate);
}

async function loadOpenCode() {
  const statusLine = $('#opencode-status-line');
  const toolRoot = $('#opencode-tool-list');
  const pluginRoot = $('#opencode-plugin-list');
  const mcpRoot = $('#opencode-mcp-list');
  try {
    const statusData = await api('/opencode/status');
    const status = statusData.opencode || {};
    if (!state.settings) {
      try {
        const settingsData = await api('/settings');
        state.settings = settingsData.settings || {};
      } catch {
        /* ignore */
      }
    }
    const enabled = $('#opencode-enabled');
    if (enabled) enabled.checked = state.settings?.opencode_enabled !== false;
    if (statusLine) {
      const version = status.version ? `v${status.version}` : '版本未知';
      statusLine.textContent = status.running
        ? `运行中 · ${version} · 端口 ${status.port} · 会话 ${status.sessions ?? 0} 个`
        : status.installed
          ? `已安装 · ${version} · 当前未启动（首次对话会自动启动）`
          : '未找到官方 OpenCode，请运行 scripts/setup-opencode.ps1';
    }
    const data = await api('/opencode/tools');
    if (data.error && statusLine) {
      statusLine.textContent = `未启动：${data.error}`;
    }
    const tools = [
      ...(data.tools || []),
      ...(data.mcp_tools || []).map((tool) => ({ ...tool, source: 'qunban-mcp' })),
    ];
    if (toolRoot) {
      toolRoot.replaceChildren();
      if (!tools.length) {
        toolRoot.appendChild(el('p', 'muted', status.running ? '暂无工具' : 'OpenCode 尚未启动，发一条消息后再刷新。'));
      }
      for (const tool of tools.slice(0, 120)) {
        const item = el('div', 'opencode-tool-item');
        item.append(
          el('strong', null, tool.id || tool.name || 'tool'),
          el(
            'small',
            null,
            `${tool.description || 'OpenCode 工具'}${tool.enabled === false ? ' · 已停用' : ''}`,
          ),
        );
        toolRoot.appendChild(item);
      }
    }
    const toolCount = $('#opencode-tool-count');
    if (toolCount) toolCount.textContent = String(tools.length);
    const plugins = data.plugins || [];
    if (pluginRoot) {
      pluginRoot.replaceChildren();
      if (!plugins.length) pluginRoot.appendChild(el('p', 'muted', '尚未加载插件；可放入 data/opencode/plugins。'));
      for (const plugin of plugins) {
        const item = el('div', 'opencode-tool-item');
        item.append(
          el('strong', null, plugin.id || plugin.path || 'plugin'),
          el('small', null, plugin.source === 'local' ? '本地插件' : '配置插件'),
        );
        pluginRoot.appendChild(item);
      }
    }
    const pluginCount = $('#opencode-plugin-count');
    if (pluginCount) pluginCount.textContent = String(plugins.length);
    if (mcpRoot) {
      mcpRoot.replaceChildren();
      const rows = Object.entries(data.mcp || {});
      if (!rows.length) mcpRoot.appendChild(el('p', 'muted', '暂无 MCP 连接。'));
      for (const [name, value] of rows) {
        const item = el('div', 'opencode-tool-item');
        item.append(
          el('strong', null, name),
          el('small', null, typeof value === 'object' ? value.status || JSON.stringify(value) : String(value)),
        );
        mcpRoot.appendChild(item);
      }
    }
    const qunbanRoot = $('#opencode-qunban-projects');
    if (qunbanRoot) {
      qunbanRoot.replaceChildren();
      const projects = data.qunban_projects || {};
      const rows = [
        ...(projects.mcps || []).map((item) => ({ ...item, kind: 'MCP' })),
        ...(projects.skills || []).map((item) => ({ ...item, kind: '技能' })),
        ...(projects.apis || []).map((item) => ({ ...item, kind: '接口' })),
      ];
      if (!rows.length) qunbanRoot.appendChild(el('p', 'muted', '尚未发现群伴 MCP / 技能 / 接口。'));
      for (const item of rows) {
        const row = el('div', 'opencode-tool-item');
        row.append(
          el('strong', null, `${item.kind} · ${item.name}`),
          el('small', null, item.description || item.key || ''),
        );
        qunbanRoot.appendChild(row);
      }
      const count = $('#opencode-project-count');
      if (count) count.textContent = String(rows.length);
    }
    await loadOpenCodeTasks();
  } catch (error) {
    if (statusLine) statusLine.textContent = `读取失败：${error.message}`;
    toast(error.message, 'error');
  }
}

const OPENCODE_TASK_LABELS = {
  pending: '排队中',
  running: '运行中',
  done: '已完成',
  error: '失败',
  cancelled: '已取消',
  idle: '空闲',
};

async function loadOpenCodeTasks() {
  const root = $('#opencode-task-list');
  if (!root) return;
  try {
    const data = await api('/opencode/tasks?limit=12');
    state.opencodeTasks = data.tasks || [];
    const count = $('#opencode-task-count');
    if (count) count.textContent = String(state.opencodeTasks.length);
    root.replaceChildren();
    if (!state.opencodeTasks.length) {
      root.appendChild(
        el('p', 'muted', data.running ? '暂无 OpenCode 任务' : 'OpenCode 尚未启动'),
      );
      return;
    }
    for (const task of state.opencodeTasks) {
      const item = el('div', `opencode-task-item ${task.status || 'idle'}`);
      const head = el('div', 'opencode-task-head');
      head.appendChild(el('strong', null, task.label || task.title || 'OpenCode 任务'));
      head.appendChild(
        el(
          'span',
          `chat-task-status ${task.status || 'idle'}`,
          OPENCODE_TASK_LABELS[task.status] || task.status || '空闲',
        ),
      );
      item.appendChild(head);
      const meta = [
        task.group_name,
        task.user_name,
        task.tool_count ? `${task.tool_count} 个工具` : '',
      ]
        .filter(Boolean)
        .join(' · ');
      if (meta) item.appendChild(el('p', 'opencode-task-meta', meta));
      const query = String(task.query || task.last_user || '').trim();
      if (query) item.appendChild(el('p', 'opencode-task-query', query.slice(0, 220)));
      const result = String(task.error || task.result || task.last_assistant || '').trim();
      if (result) {
        item.appendChild(
          el(
            'p',
            task.error ? 'opencode-task-error' : 'opencode-task-result',
            result.slice(0, 280),
          ),
        );
      }
      if (task.updated_at) {
        let timeText = '';
        try {
          timeText = new Date(task.updated_at).toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          });
        } catch {
          timeText = '';
        }
        if (timeText) item.appendChild(el('p', 'opencode-task-time', timeText));
      }
      root.appendChild(item);
    }
  } catch (error) {
    root.replaceChildren(el('p', 'muted', `读取失败：${error.message}`));
  }
}

async function loadAssistantFiles(path = '.') {
  const target = path || $('#assistant-file-path')?.value.trim() || '.';
  try {
    const data = await api(`/assistant/files?path=${encodeURIComponent(target)}`);
    const root = $('#assistant-file-list');
    if (!root) return;
    root.replaceChildren();
    for (const entry of data.entries || []) {
      const button = el('button', null, `${entry.type === 'dir' ? '📁' : '📄'} ${entry.name}`);
      button.type = 'button';
      button.title = entry.path;
      button.addEventListener('click', () => {
        const input = $('#assistant-file-path');
        if (input) input.value = entry.path;
        if (entry.type === 'dir') loadAssistantFiles(entry.path);
        else openAssistantFile(entry.path);
      });
      root.appendChild(button);
    }
    const input = $('#assistant-file-path');
    if (input) input.value = data.path || target;
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function openAssistantFile(path = '') {
  const target = path || $('#assistant-file-path')?.value.trim() || '';
  if (!target) return toast('请输入文件路径', 'warn');
  try {
    const data = await api(`/assistant/file?path=${encodeURIComponent(target)}`);
    const editor = $('#assistant-file-content');
    if (editor) editor.value = data.binary ? `[二进制文件 ${data.size} 字节]\n${data.base64 || ''}` : data.content || '';
    const input = $('#assistant-file-path');
    if (input) input.value = data.path || target;
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function saveAssistantFile() {
  const path = $('#assistant-file-path')?.value.trim();
  if (!path) return toast('请输入文件路径', 'warn');
  try {
    await api('/assistant/file', {
      method: 'PUT',
      body: JSON.stringify({ path, content: $('#assistant-file-content')?.value || '' }),
    });
    toast('文件已保存');
    await loadAssistantFiles(path);
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function deleteAssistantFile() {
  const path = $('#assistant-file-path')?.value.trim();
  if (!path) return;
  if (!window.confirm(`删除「${path}」？`)) return;
  try {
    await api(`/assistant/file?path=${encodeURIComponent(path)}&recursive=true`, { method: 'DELETE' });
    toast('已删除');
    $('#assistant-file-content').value = '';
    await loadAssistantFiles('.');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function runAssistantShell() {
  const command = $('#assistant-shell-command')?.value.trim();
  if (!command) return;
  const output = $('#assistant-shell-output');
  if (output) output.textContent = '执行中…';
  try {
    const data = await api('/assistant/shell', {
      method: 'POST',
      body: JSON.stringify({
        command,
        admin: $('#assistant-shell-admin')?.checked || false,
        timeout: 60,
      }),
    });
    if (output) {
      output.textContent = [
        `exit=${data.exit_code} · ${data.seconds}s · ${data.elevated ? 'UAC 提权' : data.admin ? '管理员' : '普通权限'}`,
        data.stdout || '',
        data.stderr ? `STDERR:\n${data.stderr}` : '',
      ].filter(Boolean).join('\n');
    }
  } catch (error) {
    if (output) output.textContent = error.message;
    toast(error.message, 'error');
  }
}

async function loadAssistantSkills() {
  try {
    const data = await api('/assistant/skills');
    state.assistantSkills = data.skills || [];
    const root = $('#assistant-skill-list');
    if (!root) return;
    root.replaceChildren();
    for (const skill of state.assistantSkills) {
      const button = el('button', null, `🧩 ${skill.name}${skill.has_skill_md ? '' : '（缺少 SKILL.md）'}`);
      button.type = 'button';
      button.addEventListener('click', async () => {
        try {
          const detail = await api(`/assistant/skills/${encodeURIComponent(skill.name)}`);
          $('#assistant-skill-name').value = skill.name;
          $('#assistant-skill-content').value = detail.skill_md || '';
        } catch (error) {
          toast(error.message, 'error');
        }
      });
      root.appendChild(button);
    }
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function saveAssistantSkill() {
  const name = $('#assistant-skill-name')?.value.trim();
  if (!name) return toast('请输入技能名', 'warn');
  try {
    await api(`/assistant/skills/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ content: $('#assistant-skill-content')?.value || '' }),
    });
    toast('技能已保存');
    await loadAssistantSkills();
    await loadAssistantStatus();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function runAssistantSkill() {
  const name = $('#assistant-skill-name')?.value.trim();
  if (!name) return;
  const output = $('#assistant-shell-output');
  try {
    const data = await api(`/assistant/skills/${encodeURIComponent(name)}/run`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    const output = $('#assistant-shell-output');
    if (output) output.textContent = JSON.stringify(data, null, 2);
    toast(data.exit_code === 0 ? '技能执行完成' : '技能执行结束，请查看输出');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function loadAssistantMemories() {
  try {
    const q = $('#assistant-memory-query')?.value.trim() || '';
    const data = await api(`/assistant/memories?q=${encodeURIComponent(q)}&limit=120`);
    state.assistantMemories = data.memories || [];
    const root = $('#assistant-memory-list');
    if (!root) return;
    root.replaceChildren();
    if (!state.assistantMemories.length) {
      root.appendChild(el('p', 'tool-empty', '还没有长期记忆。'));
      return;
    }
    for (const memory of state.assistantMemories) {
      const item = el('div', 'assistant-memory-item');
      item.appendChild(el('strong', null, `${'★'.repeat(memory.importance)} ${memory.title || '记忆'}`));
      item.appendChild(el('p', null, memory.content || ''));
      item.appendChild(el('span', 'tool-meta', `${memory.category} · ${memory.scope}`));
      const remove = el('button', null, '删除');
      remove.type = 'button';
      remove.addEventListener('click', async () => {
        try {
          await api(`/assistant/memories/${memory.id}`, { method: 'DELETE' });
          toast('记忆已删除');
          await loadAssistantMemories();
        } catch (error) {
          toast(error.message, 'error');
        }
      });
      item.appendChild(remove);
      root.appendChild(item);
    }
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function summarizeAssistantMemories() {
  try {
    toast('正在总结已读群聊…');
    const data = await api('/assistant/memories/summarize', {
      method: 'POST',
      body: JSON.stringify({}),
    });
    const counts = (data.results || []).reduce((sum, item) => sum + (item.count || 0), 0);
    toast(`已新增 ${counts} 条记忆`);
    await loadAssistantMemories();
    await loadAssistantStatus();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function loadAssistantTasks() {
  try {
    const data = await api('/assistant/tasks');
    state.assistantTasks = data.tasks || [];
    const root = $('#assistant-task-list');
    if (!root) return;
    root.replaceChildren();
    if (!state.assistantTasks.length) {
      root.appendChild(el('p', 'tool-empty', '还没有自我迭代任务。'));
      return;
    }
    for (const task of state.assistantTasks) {
      const item = el('div', 'assistant-memory-item');
      item.appendChild(el('strong', null, `#${task.id} ${task.title}`));
      item.appendChild(el('p', null, task.detail || ''));
      item.appendChild(el('span', 'tool-meta', `${task.status} · ${task.updated_at || ''}`));
      if (task.result) item.appendChild(el('p', null, task.result));
      root.appendChild(item);
    }
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function loadAssistantCapabilities(sync = false) {
  try {
    const data = await api(sync ? '/capabilities/sync' : '/capabilities', {
      method: sync ? 'POST' : 'GET',
    });
    state.assistantCapabilities = data.capabilities || [];
    const root = $('#assistant-capability-list');
    if (!root) return;
    root.replaceChildren();
    const groups = { tool: '工具', skill: '技能', mcp: 'MCP / 项目', api: '外部接口' };
    for (const [type, label] of Object.entries(groups)) {
      const rows = state.assistantCapabilities.filter((item) => item.type === type);
      if (!rows.length) continue;
      root.appendChild(el('div', 'capability-group-title', label));
      for (const item of rows) {
        const row = el('div', 'capability-item');
        const info = el('div');
        const name = el('strong', null, item.name || item.key);
        name.title = item.description || '';
        if (item.risk) {
          const risk = el('span', `capability-risk ${item.risk}`, item.risk);
          name.appendChild(risk);
        }
        info.appendChild(name);
        info.appendChild(el('small', null, item.description || item.key));
        const assistant = el('input');
        assistant.type = 'checkbox';
        assistant.checked = Boolean(item.enabled_assistant);
        assistant.title = '用户与 Agent 直接对话可用';
        assistant.addEventListener('change', () => toggleCapability(item, 'assistant', assistant));
        const group = el('input');
        group.type = 'checkbox';
        group.checked = Boolean(item.enabled_group);
        group.title = '群聊 Agent 可用';
        group.addEventListener('change', () => toggleCapability(item, 'group', group));
        row.append(info, assistant, group);
        root.appendChild(row);
      }
    }
    const status = await api('/assistant/status').catch(() => null);
    if (status) {
      const model = $('#assistant-model-hint');
      if (model) {
        model.textContent = status.llm_configured
          ? `模型：${status.model || '未设置'} · 记忆 ${status.memories} 条 · 技能 ${status.skills} 个`
          : '未配置 API Key，先到设置里填写模型';
      }
    }
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function toggleCapability(item, scope, input) {
  const field = scope === 'assistant' ? 'enabled_assistant' : 'enabled_group';
  try {
    const data = await api('/capabilities', {
      method: 'PATCH',
      body: JSON.stringify({ key: item.key, [field]: input.checked }),
    });
    item[field] = data[field] ?? input.checked;
    toast(`${item.name}：${scope === 'assistant' ? '助手' : '群聊'}${input.checked ? '已启用' : '已关闭'}`);
  } catch (error) {
    input.checked = !input.checked;
    toast(error.message, 'error');
  }
}

async function loadAssistantContacts() {
  try {
    const data = await api('/contacts');
    state.assistantContacts = data.contacts || [];
    state.contacts = state.assistantContacts;
    populateAssistantConversationOptions();
    renderAssistantContacts();
    const current = state.assistantContacts.find(
      (item) => item.id === state.assistantContactId,
    );
    if (current) {
      fillAssistantContact(current);
      loadContactMessages(current.id).catch((error) => toast(error.message, 'error'));
    } else if (state.assistantContactId) {
      state.assistantContactId = null;
      const config = $('#assistant-contact-config');
      if (config) config.hidden = true;
    }
    if (state.sidebarMode === 'contacts') renderContactList();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderAssistantContacts() {
  const root = $('#assistant-contact-list');
  if (!root) return;
  const query = state.assistantContactQuery.trim().toLowerCase();
  const contacts = state.assistantContacts
    .filter((item) => {
      if (!query) return true;
      return [item.name, item.remark, item.wxid].some((value) =>
        String(value || '').toLowerCase().includes(query),
      );
    })
    .slice(0, 120);
  root.replaceChildren();
  if (!contacts.length) {
    root.appendChild(el('div', 'group-list-empty', '没有匹配的联系人'));
    return;
  }
  for (const contact of contacts) {
    const button = el(
      'button',
      contact.id === state.assistantContactId ? 'active' : null,
      `${contact.is_admin ? '👑 ' : ''}${contact.name || contact.wxid}`,
    );
    button.type = 'button';
    button.title = contact.wxid;
    button.addEventListener('click', () => selectAssistantContact(contact.id));
    root.appendChild(button);
  }
}

function selectAssistantContact(contactId) {
  const contact = state.assistantContacts.find((item) => item.id === contactId);
  if (!contact) return;
  state.assistantContactId = contactId;
  state.selectedContactId = contactId;
  fillAssistantContact(contact);
  renderAssistantContacts();
  loadContactMessages(contactId).catch((error) => toast(error.message, 'error'));
}

function fillAssistantContact(contact) {
  const config = $('#assistant-contact-config');
  if (!config) return;
  config.hidden = false;
  $('#assistant-contact-name').textContent = contact.name || contact.wxid;
  $('#assistant-contact-wxid').textContent = contact.wxid;
  $('#assistant-contact-admin').checked = Boolean(contact.is_admin);
  $('#assistant-contact-trigger').value = contact.trigger_mode || 'all';
  $('#assistant-contact-keywords').value = (contact.keywords || []).join(', ');
  const rawTemperature = Number(contact.temperature);
  const temperature = Number.isFinite(rawTemperature) ? rawTemperature : 0.3;
  $('#assistant-contact-temperature').value = String(temperature);
  $('#assistant-contact-temperature-value').textContent = temperature.toFixed(2);
  $('#assistant-contact-persona').value = contact.persona || '';
}

async function saveAssistantContact() {
  const contactId = state.assistantContactId;
  if (!contactId) return toast('请先选择联系人', 'warn');
  const trigger = $('#assistant-contact-trigger')?.value || 'all';
  const keywords = $('#assistant-contact-keywords')?.value.trim() || '';
  if (trigger === 'keyword' && !keywords) {
    return toast('关键词触发需要填写至少一个关键词', 'warn');
  }
  const payload = {
    is_admin: Boolean($('#assistant-contact-admin')?.checked),
    trigger_mode: trigger,
    keywords,
    temperature: Number.parseFloat($('#assistant-contact-temperature')?.value || '0.3'),
    persona: $('#assistant-contact-persona')?.value.trim() || '',
  };
  try {
    const data = await api(`/contacts/${contactId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
    const updated = data.contact;
    for (const list of [state.assistantContacts, state.contacts]) {
      const index = list.findIndex((item) => item.id === contactId);
      if (index >= 0) list[index] = { ...list[index], ...updated };
    }
    renderAssistantContacts();
    if (state.sidebarMode === 'contacts') renderContactList();
    fillAssistantContact(updated);
    toast('联系人 Agent 设置已保存');
  } catch (error) {
    toast(error.message, 'error');
  }
}

function openAssistantContactChat() {
  if (!state.assistantContactId) return toast('请先选择联系人', 'warn');
  openContactChat(state.assistantContactId).catch((error) => toast(error.message, 'error'));
}

async function sendAssistantContactMessage() {
  const contactId = state.assistantContactId;
  const input = $('#assistant-contact-input');
  const text = input?.value.trim() || '';
  if (!contactId || !text) return;
  const button = $('#assistant-contact-send');
  if (button) button.disabled = true;
  try {
    await api(`/contacts/${contactId}/send`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    input.value = '';
    await loadContactMessages(contactId);
    await loadContacts();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

function biliRenderStatus(account) {
  state.biliAccount = account || null;
  const line = $('#bili-status-line');
  if (line) {
    const cookieHint = account?.cookie_saved
      ? ` · Cookie 已保存 ${account.cookie_count || 0} 项`
      : '';
    if (account?.logged_in) {
      line.textContent = `已登录：${account.name || account.mid || 'B站账号'} ${account.sessdata_hint || ''}${cookieHint}`;
    } else if (account?.expired) {
      line.textContent = `登录已失效：${account.name || account.mid || 'B站账号'}${cookieHint} · 请重新扫码`;
    } else {
      line.textContent = '未登录 · 扫码或填写 Cookie 后可用点赞/评论';
    }
  }
  const logout = $('#bili-logout');
  if (logout) logout.hidden = !account?.logged_in;
}

async function loadBiliStatus() {
  try {
    const data = await api('/bili/status');
    biliRenderStatus(data.account);
  } catch (error) {
    toast(error.message, 'error');
  }
}

function biliQrStatusText(status) {
  return {
    scan: '等待扫码…',
    confirm: '已扫码，请在手机上确认',
    done: '登录成功',
    timeout: '二维码已过期，请重新生成',
    expired: '二维码已过期，请重新生成',
  }[status] || `状态：${status}`;
}

async function biliStartQr() {
  if (state.biliQrTimer) window.clearInterval(state.biliQrTimer);
  const box = $('#bili-qr-box');
  const image = $('#bili-qr-image');
  const status = $('#bili-qr-status');
  try {
    const data = await api('/bili/login/qr', { method: 'POST', body: '{}' });
    if (image) image.src = data.url;
    if (box) box.hidden = false;
    if (status) status.textContent = biliQrStatusText(data.status);
    state.biliQrTimer = window.setInterval(() => biliPollQr(data.key), 2000);
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function biliPollQr(key) {
  const status = $('#bili-qr-status');
  try {
    const data = await api(`/bili/login/qr/${encodeURIComponent(key)}`);
    if (status) status.textContent = biliQrStatusText(data.status);
    if (data.status === 'done' && data.account?.logged_in) {
      if (state.biliQrTimer) window.clearInterval(state.biliQrTimer);
      state.biliQrTimer = null;
      biliRenderStatus(data.account);
      $('#bili-qr-box')?.setAttribute('hidden', '');
      toast(`B站登录成功：${data.account.name || data.account.mid || '账号'}`);
    } else if (data.status === 'done') {
      if (state.biliQrTimer) window.clearInterval(state.biliQrTimer);
      state.biliQrTimer = null;
      if (status) status.textContent = '登录返回异常：没有拿到有效凭据，请重新扫码';
      toast('B站登录返回异常，请重新扫码', 'error', 6000);
    } else if (['timeout', 'expired', 'error'].includes(data.status)) {
      if (state.biliQrTimer) window.clearInterval(state.biliQrTimer);
      state.biliQrTimer = null;
      if (data.message) {
        if (status) status.textContent = data.message;
        toast(data.message, 'error', 6000);
      }
    }
  } catch (error) {
    if (state.biliQrTimer) window.clearInterval(state.biliQrTimer);
    state.biliQrTimer = null;
    toast(error.message, 'error');
  }
}

async function biliLogout() {
  if (!window.confirm('退出 B站账号？')) return;
  try {
    if (state.biliQrTimer) window.clearInterval(state.biliQrTimer);
    if (state.biliPasswordTimer) window.clearInterval(state.biliPasswordTimer);
    state.biliQrTimer = null;
    state.biliPasswordTimer = null;
    state.biliPasswordSession = '';
    await api('/bili/account', { method: 'DELETE' });
    biliRenderStatus(null);
    $('#bili-qr-box')?.setAttribute('hidden', '');
    toast('已退出 B站账号');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function biliSaveCookie() {
  const payload = {
    sessdata: $('#bili-sessdata')?.value.trim() || '',
    bili_jct: $('#bili-jct')?.value.trim() || '',
    buvid3: $('#bili-buvid3')?.value.trim() || '',
    dedeuserid: $('#bili-dedeuserid')?.value.trim() || '',
  };
  try {
    const data = await api('/bili/account/cookie', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    biliRenderStatus(data.account);
    toast('B站 Cookie 已保存');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function biliPasswordStart() {
  const username = $('#bili-username')?.value.trim() || '';
  const password = $('#bili-password')?.value || '';
  if (!username || !password) return toast('请输入 B站账号和密码', 'warn');
  try {
    const data = await api('/bili/login/password/start', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    state.biliPasswordSession = data.session_id || '';
    const link = $('#bili-captcha-link');
    if (link) {
      link.href = data.captcha_url || '#';
      link.hidden = !data.captcha_url;
    }
    const status = $('#bili-password-status');
    if (status) status.textContent = data.message || '请完成验证';
    if (state.biliPasswordTimer) window.clearInterval(state.biliPasswordTimer);
    state.biliPasswordTimer = window.setInterval(() => biliPasswordPoll(), 2500);
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function biliPasswordPoll() {
  if (!state.biliPasswordSession) return;
  const status = $('#bili-password-status');
  try {
    const data = await api('/bili/login/password/poll', {
      method: 'POST',
      body: JSON.stringify({ session_id: state.biliPasswordSession }),
    });
    if (status) status.textContent = data.message || data.status;
    if (data.status === 'done') {
      if (state.biliPasswordTimer) window.clearInterval(state.biliPasswordTimer);
      state.biliPasswordTimer = null;
      state.biliPasswordSession = '';
      biliRenderStatus(data.account);
      const password = $('#bili-password');
      if (password) password.value = '';
      toast('B站登录成功');
    } else if (['error', 'verify', 'expired'].includes(data.status)) {
      if (state.biliPasswordTimer) window.clearInterval(state.biliPasswordTimer);
      state.biliPasswordTimer = null;
    }
  } catch (error) {
    if (state.biliPasswordTimer) window.clearInterval(state.biliPasswordTimer);
    state.biliPasswordTimer = null;
    toast(error.message, 'error');
  }
}

function biliRenderVideos(videos) {
  state.biliVideos = videos || [];
  const root = $('#bili-video-list');
  if (!root) return;
  root.replaceChildren();
  if (!state.biliVideos.length) {
    root.appendChild(el('div', 'group-list-empty', '没有视频，先搜索或刷新推荐'));
    return;
  }
  for (const video of state.biliVideos) {
    const row = el('div', 'bili-video-row');
    const main = el('div', 'bili-video-main');
    main.append(
      el('strong', null, video.title || video.bvid),
      el('small', null, `${video.author || '未知 UP'} · ${video.play || 0} 播放`),
    );
    const watch = el('button', 'text-btn', '看');
    watch.type = 'button';
    watch.addEventListener('click', () => biliWatch(video.bvid));
    const like = el('button', 'text-btn', '赞');
    like.type = 'button';
    like.addEventListener('click', () => biliLike(video.bvid));
    const cmt = el('button', 'text-btn', '评');
    cmt.type = 'button';
    cmt.addEventListener('click', () => biliComment(video.bvid));
    const remember = el('button', 'text-btn', '记');
    remember.type = 'button';
    remember.title = '按需转写并记录到知识库';
    remember.addEventListener('click', () => biliRemember(video.bvid));
    row.append(main, watch, like, cmt, remember);
    root.appendChild(row);
  }
}

async function biliSearch() {
  const keyword = $('#bili-search-input')?.value.trim() || '';
  if (!keyword) return toast('请输入搜索关键词', 'warn');
  try {
    const data = await api(`/bili/search?q=${encodeURIComponent(keyword)}&limit=10`);
    biliRenderVideos(data.videos || []);
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function biliWatch(bvid) {
  try {
    const data = await api('/bili/watch', {
      method: 'POST',
      body: JSON.stringify({ bvid, open_browser: true }),
    });
    toast(data.opened ? `已打开：${data.video?.title || bvid}` : '视频已读取，但浏览器未打开');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function biliRemember(bvid) {
  if (
    !window.confirm(
      '将下载音频并转写，可能耗时 1–3 分钟；完成后会写入本地知识库。继续？',
    )
  ) {
    return;
  }
  toast('正在转写并记录到知识库…', 'info', 12000);
  try {
    const data = await api('/bili/knowledge/add', {
      method: 'POST',
      body: JSON.stringify({ bvid, model: 'small' }),
    });
    toast(`已记录：${data.title || bvid}（${data.chunks || 0} 段）`);
    loadBiliAutopilot().catch(() => {});
  } catch (error) {
    toast(error.message, 'error', 6000);
  }
}

async function biliLike(bvid) {
  try {
    const data = await api('/bili/like', {
      method: 'POST',
      body: JSON.stringify({ bvid, liked: true }),
    });
    toast(data.liked ? '已点赞' : '已取消点赞');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function biliComment(bvid) {
  const text = window.prompt('输入 B站评论内容（会以账号身份发送）');
  if (!text?.trim()) return;
  try {
    await api('/bili/comment', {
      method: 'POST',
      body: JSON.stringify({ bvid, text: text.trim() }),
    });
    toast('评论已发送');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function biliFeed() {
  try {
    const data = await api('/bili/feed', {
      method: 'POST',
      body: JSON.stringify({ limit: 5, open_browser: true }),
    });
    biliRenderVideos(data.videos || []);
    toast(data.opened?.opened ? '已打开推荐视频' : '已获取推荐视频');
  } catch (error) {
    toast(error.message, 'error');
  }
}

function biliFillAutoTargets(config) {
  const groupSelect = $('#bili-auto-group');
  const contactSelect = $('#bili-auto-contact');
  if (groupSelect && !groupSelect.dataset.filled) {
    const groups = state.groups || [];
    for (const group of groups) {
      const option = el('option', null, group.name || `群 ${group.id}`);
      option.value = String(group.id);
      groupSelect.appendChild(option);
    }
    groupSelect.dataset.filled = '1';
  }
  if (contactSelect && !contactSelect.dataset.filled) {
    const contacts = state.contacts || [];
    for (const contact of contacts) {
      const option = el('option', null, contact.name || contact.wxid);
      option.value = String(contact.id);
      contactSelect.appendChild(option);
    }
    contactSelect.dataset.filled = '1';
  }
  if (groupSelect) groupSelect.value = config?.forward_group_id ? String(config.forward_group_id) : '';
  if (contactSelect) contactSelect.value = config?.forward_contact_id ? String(config.forward_contact_id) : '';
}

function biliRenderAutoHistory(videos) {
  const root = $('#bili-auto-history');
  if (!root) return;
  root.replaceChildren();
  if (!videos?.length) {
    root.appendChild(el('div', 'group-list-empty', '还没有自动刷过视频'));
    return;
  }
  for (const video of videos) {
    const item = el('div', 'bili-video-history');
    const title = el('strong', null, video.title || video.bvid);
    title.title = video.summary || video.title || '';
    const meta = el(
      'small',
      null,
      `${video.author || ''} · ${video.status || ''}${
        video.actions?.knowledge ? ' · 已入知识库' : ''
      }${video.actions?.comment ? ' · 已评论' : ''}${
        video.actions?.comment_from_opinion ? '（来自看法）' : ''
      }`,
    );
    const summary = el('small', null, (video.summary || video.error || '').slice(0, 120));
    item.append(title, meta, summary);
    const opinion = video.analysis?.opinion;
    if (opinion) {
      item.append(el('small', 'bili-agent-opinion', `Agent 看法：${String(opinion).slice(0, 140)}`));
    }
    if (video.url) {
      item.style.cursor = 'pointer';
      item.addEventListener('click', () => window.open(video.url, '_blank', 'noreferrer'));
    }
    root.appendChild(item);
  }
}

function biliRenderAutoLog() {
  const root = $('#bili-auto-log');
  if (!root) return;
  root.replaceChildren();
  for (const line of state.biliAutoLog.slice(-100)) {
    const item = el(
      'div',
      `bili-auto-line${line.phase === 'error' ? ' error' : line.phase === 'done' ? ' done' : ''}`,
    );
    item.append(el('time', null, line.time || ''), el('span', null, line.message || ''));
    root.appendChild(item);
  }
  root.scrollTop = root.scrollHeight;
}

function biliAppendAutoLog(data) {
  if (!data) return;
  state.biliAutoLog.push({
    time: new Date().toLocaleTimeString(),
    phase: data.phase || '',
    message: data.message || data.phase || '',
  });
  if (state.biliAutoLog.length > 120) state.biliAutoLog = state.biliAutoLog.slice(-120);
  biliRenderAutoLog();
}

async function loadBiliAutopilot() {
  try {
    if (!state.groups?.length) {
      const data = await api('/groups');
      state.groups = data.groups || [];
    }
    if (!state.contacts?.length) {
      const data = await api('/contacts');
      state.contacts = data.contacts || [];
    }
    const data = await api('/bili/autopilot');
    const config = data.config || {};
    state.biliAuto = config;
    biliFillAutoTargets(config);
    $('#bili-auto-enabled').checked = Boolean(config.enabled);
    $('#bili-auto-use-context').checked = config.use_agent_context !== false;
    $('#bili-auto-interval').value = String(config.interval_minutes ?? 60);
    $('#bili-auto-topics').value = (config.topics || []).join(', ');
    $('#bili-auto-asr').value = config.asr_model || 'small';
    $('#bili-auto-like').checked = config.like_enabled !== false;
    $('#bili-auto-favorite').checked = config.favorite_enabled !== false;
    $('#bili-auto-comment').checked = config.comment_enabled !== false;
    $('#bili-auto-reply').checked = Boolean(config.reply_comments_enabled);
    $('#bili-auto-state').textContent = config.enabled
      ? `已开启 · ${config.interval_minutes || 60} 分钟 · 知识库 ${data.knowledge?.count || 0} 条`
      : `已关闭 · 知识库 ${data.knowledge?.count || 0} 条`;
    biliRenderAutoHistory(data.videos || []);
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function saveBiliAutopilot() {
  const payload = {
    enabled: Boolean($('#bili-auto-enabled')?.checked),
    use_agent_context: Boolean($('#bili-auto-use-context')?.checked),
    interval_minutes: parseIntSafe($('#bili-auto-interval')?.value, 60),
    topics: $('#bili-auto-topics')?.value.trim() || '',
    asr_model: $('#bili-auto-asr')?.value || 'small',
    like_enabled: Boolean($('#bili-auto-like')?.checked),
    favorite_enabled: Boolean($('#bili-auto-favorite')?.checked),
    comment_enabled: Boolean($('#bili-auto-comment')?.checked),
    reply_comments_enabled: Boolean($('#bili-auto-reply')?.checked),
    forward_group_id: parseIntSafe($('#bili-auto-group')?.value, 0) || null,
    forward_contact_id: parseIntSafe($('#bili-auto-contact')?.value, 0) || null,
  };
  try {
    const data = await api('/bili/autopilot', {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
    state.biliAuto = data.config || payload;
    await loadBiliAutopilot();
    toast(payload.enabled ? '自动刷 B站已开启' : '自动刷 B站已关闭');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function runBiliAutopilot() {
  try {
    const data = await api('/bili/autopilot/run', { method: 'POST', body: '{}' });
    toast(data.message || '已开始自动刷任务');
    biliAppendAutoLog({ phase: 'start', message: '已手动触发一次自动刷任务' });
  } catch (error) {
    toast(error.message, 'error');
  }
}

function bindAssistantActions() {
  $('#assistant-send')?.addEventListener('click', sendAssistantMessage);
  $('#assistant-stop')?.addEventListener('click', stopAssistant);
  $('#assistant-conversation-select')?.addEventListener('change', (event) => {
    switchAssistantConversation(event.target.value);
  });
  $('#assistant-max-steps')?.addEventListener('change', saveAssistantMaxSteps);
  $('#assistant-max-steps')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      saveAssistantMaxSteps();
    }
  });
  $('#assistant-attach')?.addEventListener('click', () => $('#assistant-attach-input')?.click());
  $('#assistant-attach-input')?.addEventListener('change', (event) => {
    uploadAssistantImage(event.target.files?.[0]);
    event.target.value = '';
  });
  $('#assistant-input')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendAssistantMessage();
    }
  });
  $('#assistant-admin-restart')?.addEventListener('click', async () => {
    if (
      !window.confirm(
        '将以管理员权限重启群伴服务。会先停止当前服务，UAC 确认后自动重启。继续？',
      )
    )
      return;
    try {
      const data = await api('/assistant/admin/restart', { method: 'POST' });
      toast(data.message || '已请求提权，请稍候刷新页面', 'info', 8000);
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#assistant-clear')?.addEventListener('click', async () => {
    if (!window.confirm('清空 Agent 对话记录？')) return;
    try {
      await api('/assistant/history?session_id=main', { method: 'DELETE' });
      state.assistantHistory = [];
      renderAssistantMessages();
      toast('对话已清空');
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $$('#assistant-quick [data-assistant-prompt]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = $('#assistant-input');
      if (input) input.value = button.dataset.assistantPrompt || '';
      sendAssistantMessage();
    });
  });
  $$('.assistant-side-tabs button').forEach((button) => {
    button.addEventListener('click', () => {
      const name = button.dataset.assistantPanel;
      switchAssistantPanel(name);
      if (name === 'workspace') loadAssistantFiles();
      if (name === 'skills') loadAssistantSkills();
      if (name === 'memory') loadAssistantMemories();
      if (name === 'tasks') loadAssistantTasks();
      if (name === 'contacts') loadAssistantContacts();
      if (name === 'bili') {
        loadBiliStatus();
        loadBiliAutopilot();
      }
      if (name === 'capabilities') loadAssistantCapabilities();
      if (name === 'opencode') loadOpenCode();
      if (name === 'dsh') loadDSH();
    });
  });
  $('#opencode-refresh')?.addEventListener('click', loadOpenCode);
  $('#opencode-tasks-refresh')?.addEventListener('click', loadOpenCodeTasks);
  $('#opencode-restart')?.addEventListener('click', async () => {
    try {
      await api('/opencode/restart', { method: 'POST' });
      toast('OpenCode 已重启');
      loadOpenCode();
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#opencode-reset')?.addEventListener('click', async () => {
    try {
      await api('/opencode/reset', {
        method: 'POST',
        body: JSON.stringify({ session_id: 'main' }),
      });
      toast('当前 OpenCode 会话已重置');
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#opencode-enabled')?.addEventListener('change', async (event) => {
    try {
      const data = await api('/settings', {
        method: 'PUT',
        body: JSON.stringify({ opencode_enabled: Boolean(event.target.checked) }),
      });
      state.settings = data.settings || state.settings;
      toast(event.target.checked ? '已启用官方 OpenCode 引擎' : '已切回内置执行循环');
    } catch (error) {
      toast(error.message, 'error');
      event.target.checked = !event.target.checked;
    }
  });
  $('#dsh-refresh')?.addEventListener('click', loadDSH);
  $('#dsh-enabled')?.addEventListener('change', async (event) => {
    try {
      const enabled = Boolean(event.target.checked);
      const data = await api('/settings', {
        method: 'PUT',
        body: JSON.stringify({
          dsh_enabled: enabled,
          operator_engine: 'dsh',
        }),
      });
      state.settings = data.settings || state.settings;
      toast(enabled ? '已启用 DeepSeek Harness Agent' : '已暂停 DSH（内置兜底）');
      loadDSH();
    } catch (error) {
      toast(error.message, 'error');
      event.target.checked = !event.target.checked;
    }
  });
  $('#dsh-channel')?.addEventListener('change', async (event) => {
    try {
      const data = await api('/settings', {
        method: 'PUT',
        body: JSON.stringify({ dsh_channel: event.target.value }),
      });
      state.settings = data.settings || state.settings;
      toast(`更新渠道：${event.target.value === 'alpha' ? 'Alpha' : '稳定版'}`);
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#dsh-group-chat-engine')?.addEventListener('change', async (event) => {
    try {
      const data = await api('/settings', {
        method: 'PUT',
        body: JSON.stringify({ group_chat_engine: event.target.value }),
      });
      state.settings = data.settings || state.settings;
      toast(
        event.target.value === 'dsh'
          ? '群聊回复已交给 DSH'
          : '群聊回复已切到内置生成（应急）',
      );
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#dsh-check')?.addEventListener('click', async () => {
    try {
      const channel = $('#dsh-channel')?.value || 'stable';
      const data = await api('/dsh/check', {
        method: 'POST',
        body: JSON.stringify({ channel }),
      });
      const info = data.update || {};
      toast(
        info.update_available
          ? `发现新版本 ${info.latest}（当前 ${info.current || '未安装'}）`
          : `已是最新：${info.current || info.latest}`,
      );
      loadDSH();
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#dsh-install')?.addEventListener('click', async () => {
    try {
      const channel = $('#dsh-channel')?.value || 'stable';
      await api('/settings', {
        method: 'PUT',
        body: JSON.stringify({ dsh_channel: channel }),
      });
      const data = await api('/dsh/update', {
        method: 'POST',
        body: JSON.stringify({ channel }),
      });
      toast(data.started ? '已开始安装/更新 DeepSeek Harness' : '已有安装任务在运行');
      loadDSH();
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#dsh-stop')?.addEventListener('click', async () => {
    try {
      await api('/dsh/stop', { method: 'POST' });
      toast('已停止 DeepSeek Harness 任务');
      loadDSH();
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#dsh-reset')?.addEventListener('click', async () => {
    try {
      await api('/dsh/reset', {
        method: 'POST',
        body: JSON.stringify({ session_id: 'main' }),
      });
      toast('DeepSeek Harness 会话已重置');
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#assistant-contacts-refresh')?.addEventListener('click', () => loadAssistantContacts());  $('#bili-refresh')?.addEventListener('click', () => loadBiliStatus());
  $('#bili-qr-start')?.addEventListener('click', biliStartQr);
  $('#bili-logout')?.addEventListener('click', biliLogout);
  $('#bili-cookie-save')?.addEventListener('click', biliSaveCookie);
  $('#bili-password-start')?.addEventListener('click', biliPasswordStart);
  $('#bili-password-finish')?.addEventListener('click', biliPasswordPoll);
  $('#bili-search-btn')?.addEventListener('click', biliSearch);
  $('#bili-search-input')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      biliSearch();
    }
  });
  $('#bili-feed-btn')?.addEventListener('click', biliFeed);
  $('#bili-auto-save')?.addEventListener('click', saveBiliAutopilot);
  $('#bili-auto-run')?.addEventListener('click', runBiliAutopilot);
  $('#bili-auto-refresh')?.addEventListener('click', loadBiliAutopilot);
  $('#assistant-contact-search')?.addEventListener('input', (event) => {
    state.assistantContactQuery = event.target.value;
    renderAssistantContacts();
  });
  $('#assistant-contact-temperature')?.addEventListener('input', (event) => {
    const value = Number.parseFloat(event.target.value || '0');
    const label = $('#assistant-contact-temperature-value');
    if (label) label.textContent = value.toFixed(2);
  });
  $('#assistant-contact-save')?.addEventListener('click', saveAssistantContact);
  $('#assistant-contact-open')?.addEventListener('click', openAssistantContactChat);
  $('#assistant-contact-send')?.addEventListener('click', sendAssistantContactMessage);
  $('#assistant-contact-input')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendAssistantContactMessage();
    }
  });
  $('#assistant-refresh-files')?.addEventListener('click', () => loadAssistantFiles());
  $('#assistant-open-file')?.addEventListener('click', () => openAssistantFile());
  $('#assistant-save-file')?.addEventListener('click', saveAssistantFile);
  $('#assistant-new-file')?.addEventListener('click', () => {
    const path = window.prompt('新文件路径（相对工作区或绝对路径）', 'scripts/new.ps1');
    if (!path) return;
    $('#assistant-file-path').value = path;
    $('#assistant-file-content').value = '';
    $('#assistant-file-content').focus();
  });
  $('#assistant-delete-file')?.addEventListener('click', deleteAssistantFile);
  $('#assistant-run-shell')?.addEventListener('click', runAssistantShell);
  $('#assistant-clear-shell')?.addEventListener('click', () => {
    $('#assistant-shell-output').textContent = '等待命令…';
  });
  $('#assistant-refresh-skills')?.addEventListener('click', loadAssistantSkills);
  $('#assistant-save-skill')?.addEventListener('click', saveAssistantSkill);
  $('#assistant-run-skill')?.addEventListener('click', runAssistantSkill);
  $('#assistant-refresh-memory')?.addEventListener('click', loadAssistantMemories);
  $('#assistant-summarize-memory')?.addEventListener('click', summarizeAssistantMemories);
  $('#assistant-memory-query')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') loadAssistantMemories();
  });
  $('#assistant-refresh-tasks')?.addEventListener('click', loadAssistantTasks);
  $('#assistant-refresh-capabilities')?.addEventListener('click', () =>
    loadAssistantCapabilities(true),
  );
}

/* ------------------------------------------------------------ tabs */
const PAGE_META = {
  chats: { title: '会话', eyebrow: 'WORKSPACE' },
  assistant: { title: '助手', eyebrow: 'OPERATOR' },
  plugins: { title: '插件', eyebrow: 'PLUGIN STUDIO' },
  stickers: { title: '表情包', eyebrow: 'STICKER LIBRARY' },
  settings: { title: '设置', eyebrow: 'SETTINGS' },
};

function routeFromHash() {
  const raw = String(location.hash || '').replace(/^#\/?/, '');
  const [tab, panel] = raw.split('/');
  return { tab: PAGE_META[tab] ? tab : 'chats', panel: panel || '' };
}

function routeFor(tab) {
  const panel =
    tab === 'assistant' && state.assistantPanel
      ? `/${state.assistantPanel}`
      : '';
  return `#/${tab}${panel}`;
}

function updatePageHeading(tab) {
  const meta = PAGE_META[tab] || PAGE_META.chats;
  const title = $('#page-title');
  const eyebrow = $('#page-eyebrow');
  if (title) title.textContent = meta.title;
  if (eyebrow) eyebrow.textContent = meta.eyebrow;
}

function showTab(tab, options = {}) {
  if (!PAGE_META[tab]) tab = 'chats';
  state.currentTab = tab;
  $$('.nav-tab').forEach((button) => button.classList.toggle('active', button.dataset.tab === tab));
  $$('.tab-panel').forEach((panel) => {
    panel.hidden = panel.id !== `tab-${tab}`;
  });
  updatePageHeading(tab);
  if (options.syncHash !== false) {
    const next = routeFor(tab);
    if (location.hash !== next) {
      history.pushState({ tab }, '', next);
    }
  }
  $('#inspector')?.classList.remove('open');
  if (tab === 'stickers') {
    loadStickers().catch((error) => toast(error.message, 'error'));
  }
  if (tab === 'plugins') {
    loadPlugins().catch((error) => toast(error.message, 'error'));
  }
  if (tab === 'settings') {
    loadSettings().catch((error) => toast(error.message, 'error'));
    loadStats();
  }
  if (tab === 'assistant') {
    loadAssistantStatus().catch(() => {});
    if (
      state.assistantConversation !== 'main' &&
      state.assistantConversationContactId
    ) {
      loadContactConversation(state.assistantConversationContactId).catch(() => {});
    } else {
      loadAssistantHistory().catch(() => {});
    }
    loadAssistantConversationOptions().catch(() => {});
    loadAssistantFiles().catch(() => {});
  }
  if (tab === 'chats') {
    syncWorkspaceLayout(Boolean(state.selectedGroupId));
    renderGroupList();
    renderChatHeader();
  }
}

function applyRoute() {
  const { tab, panel } = routeFromHash();
  if (panel && tab === 'assistant') {
    switchAssistantPanel(panel, { syncHash: false });
  }
  showTab(tab, { syncHash: false });
}

window.addEventListener('popstate', () => {
  if (state.appReady) applyRoute();
});

/* ------------------------------------------------------------ chrome helpers */
function setSettingsNavActive(targetId) {
  $$('#settings-nav button').forEach((button) => {
    button.classList.toggle('active', button.dataset.settingsTarget === targetId);
  });
}

function bindSettingsNavigation() {
  const nav = $('#settings-nav');
  if (!nav) return;
  nav.addEventListener('click', (event) => {
    const button = event.target.closest('[data-settings-target]');
    if (!button) return;
    const target = document.getElementById(button.dataset.settingsTarget);
    if (!target) return;
    setSettingsNavActive(button.dataset.settingsTarget);
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  const panel = $('#tab-settings');
  if (!panel) return;
  panel.addEventListener(
    'scroll',
    () => {
      const ids = $$('#settings-nav [data-settings-target]').map(
        (button) => button.dataset.settingsTarget,
      );
      let active = ids[0];
      for (const id of ids) {
        const node = document.getElementById(id);
        if (node && node.getBoundingClientRect().top <= 170) active = id;
      }
      if (active) setSettingsNavActive(active);
    },
    { passive: true },
  );
}

const ASSISTANT_PANEL_LABELS = {
  workspace: '工作区',
  terminal: '终端',
  skills: '技能',
  memory: '记忆',
  tasks: '任务',
  contacts: '联系人',
  bili: 'B站',
  capabilities: '能力',
  dsh: 'DeepSeek Harness',
};

function commandPaletteItems() {
  const items = [
    { label: '打开会话', hint: '页面', run: () => showTab('chats') },
    { label: '打开助手', hint: '页面', run: () => showTab('assistant') },
    { label: '打开插件', hint: '页面', run: () => showTab('plugins') },
    { label: '打开表情包', hint: '页面', run: () => showTab('stickers') },
    { label: '打开设置', hint: '页面', run: () => showTab('settings') },
    { label: '刷新群列表', hint: '操作', run: () => loadGroups() },
    { label: '上传表情包', hint: '操作', run: () => openUploadDialog() },
  ];
  for (const [panel, label] of Object.entries(ASSISTANT_PANEL_LABELS)) {
    items.push({
      label: `助手 · ${label}`,
      hint: '面板',
      run: () => {
        showTab('assistant');
        switchAssistantPanel(panel);
      },
    });
  }
  for (const group of state.groups) {
    items.push({
      label: group.name || '未命名群聊',
      hint: '群聊',
      run: async () => {
        showTab('chats');
        await selectGroup(group.id);
      },
    });
  }
  return items;
}

function renderCommandPalette(query = '') {
  const list = $('#command-list');
  if (!list) return;
  const needle = String(query || '').trim().toLowerCase();
  const matched = commandPaletteItems()
    .filter((item) =>
      !needle
        ? true
        : `${item.label} ${item.hint || ''}`.toLowerCase().includes(needle),
    )
    .slice(0, 24);
  state.commandItems = matched;
  state.commandIndex = 0;
  list.replaceChildren();
  if (!matched.length) {
    list.append(el('div', 'command-empty', '没有匹配的页面、群聊或操作'));
    return;
  }
  matched.forEach((item, index) => {
    const button = el('button', `command-item${index === 0 ? ' active' : ''}`);
    button.type = 'button';
    button.append(
      el('span', 'command-item-label', item.label),
      el('span', 'command-item-hint', item.hint || ''),
    );
    button.addEventListener('click', () => runCommandItem(item));
    button.addEventListener('mouseenter', () => {
      state.commandIndex = index;
      updateCommandActive();
    });
    list.append(button);
  });
}

function updateCommandActive() {
  $$('#command-list .command-item').forEach((node, index) => {
    node.classList.toggle('active', index === state.commandIndex);
  });
}

function runCommandItem(item) {
  const dialog = $('#command-dialog');
  if (dialog?.open) dialog.close();
  if (!item) return;
  Promise.resolve()
    .then(() => item.run())
    .catch((error) => toast(error.message || '操作失败', 'error'));
}

function openCommandPalette() {
  const dialog = $('#command-dialog');
  if (!dialog) return;
  if (!dialog.open) dialog.showModal();
  const input = $('#command-input');
  if (input) input.value = '';
  renderCommandPalette('');
  window.setTimeout(() => input?.focus(), 10);
}

function bindCommandPalette() {
  const dialog = $('#command-dialog');
  if (!dialog) return;
  const input = $('#command-input');
  $('#command-trigger')?.addEventListener('click', openCommandPalette);
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      openCommandPalette();
      return;
    }
    if (!dialog.open) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      state.commandIndex = Math.min(
        state.commandItems.length - 1,
        state.commandIndex + 1,
      );
      updateCommandActive();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      state.commandIndex = Math.max(0, state.commandIndex - 1);
      updateCommandActive();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      runCommandItem(state.commandItems[state.commandIndex]);
    }
  });
  input?.addEventListener('input', (event) =>
    renderCommandPalette(event.target.value),
  );
  dialog.addEventListener('close', () => {
    if (input) input.value = '';
  });
}

function bindDialogBackdrops() {
  $$('dialog.modal').forEach((dialog) => {
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
}

/* ------------------------------------------------------------ websocket */
function connectWS() {
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) return;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let socket;
  try {
    socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
  } catch {
    setWs(false);
    scheduleReconnect();
    return;
  }
  state.ws = socket;
  socket.addEventListener('open', () => {
    state.wsAttempts = 0;
    setWs(true);
    refreshStatus(true);
    if (state.appReady) {
      loadGroups().catch(() => {});
      if (state.selectedGroupId) reloadSelectedMessages().catch(() => {});
    }
  });
  socket.addEventListener('close', () => {
    setWs(false);
    scheduleReconnect();
  });
  socket.addEventListener('error', () => {
    try { socket.close(); } catch { /* ignore */ }
  });
  socket.addEventListener('message', (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    handleWsEvent(payload);
  });
}

function setWs(connected) {
  state.wsConnected = connected;
  renderTopbarStatus();
}

function scheduleReconnect() {
  if (state.wsTimer) return;
  const delay = Math.min(1500 * 2 ** state.wsAttempts, 10000);
  state.wsAttempts += 1;
  state.wsTimer = window.setTimeout(() => {
    state.wsTimer = null;
    connectWS();
  }, delay);
}

function handleWsEvent(event) {
  const data = event?.data || {};
  switch (event?.type) {
    case 'hello':
    case 'status':
      applyStatus(data);
      break;
    case 'account.updated': {
      const previousKey = state.status?.account?.wxid || '';
      const nextKey = data?.wxid || '';
      if (state.status) state.status.account = data;
      if (previousKey && nextKey && previousKey !== nextKey) resetForAccountSwitch();
      renderAccount();
      renderTopbarStatus();
      break;
    }
    case 'assistant.progress':
      handleAssistantProgress(data);
      break;
    case 'bili.autopilot':
      biliAppendAutoLog(data);
      if (data?.phase === 'done' || data?.phase === 'error') {
        window.setTimeout(() => loadBiliAutopilot().catch(() => {}), 1200);
      }
      break;
    case 'message.new':
      handleNewMessage(data).catch((error) => console.warn('message', error));
      break;
    case 'message.updated':
      applyMessageUpdate(data);
      break;
    case 'message.deleted':
      removeMessage(data);
      break;
    case 'groups.updated':
      scheduleGroupsReload();
      break;
    case 'direct.message':
      handleDirectMessage(data);
      break;
    case 'contact.updated': {
      const contact = state.contacts.find((item) => item.id === data.id);
      if (contact) Object.assign(contact, data);
      const assistantContact = state.assistantContacts.find((item) => item.id === data.id);
      if (assistantContact) Object.assign(assistantContact, data);
      if (state.sidebarMode === 'contacts') renderContactList();
      renderContactHeader();
      if (
        state.assistantContactId === data.id &&
        state.assistantPanel === 'contacts' &&
        assistantContact
      ) {
        fillAssistantContact(assistantContact);
      }
      break;
    }
    case 'contacts.updated':
      if (state.sidebarMode === 'contacts') loadContacts().catch(() => {});
      break;
    case 'pinned.updated':
      state.pinnedGroupId = data.group_id || null;
      renderGroupList();
      renderChatHeader();
      break;
    case 'members.updated': {
      const group = state.groups.find((item) => item.id === data.group_id);
      if (group && data.count) group.member_count = data.count;
      renderGroupList();
      if (state.selectedGroupId === data.group_id) {
        renderChatHeader();
        renderMembersDialog();
      }
      break;
    }
    case 'agent.typing':
      showTyping(data.group_id);
      break;
    case 'agent.log':
      if (state.selectedGroupId === data.group_id) {
        state.logs.unshift(data);
        state.logs = state.logs.slice(0, 30);
        renderLogs();
        if (data.level === 'error') toast(`Agent：${data.detail}`, 'warn', 4500);
      }
      break;
    case 'chat_task.updated':
      if (state.selectedGroupId === data.group_id) {
        const index = state.chatTasks.findIndex((item) => item.id === data.id);
        if (index >= 0) state.chatTasks[index] = data;
        else state.chatTasks.unshift(data);
        state.chatTasks = state.chatTasks.slice(0, 30);
        renderChatTasks();
        if (data.status === 'done' && data.result) toast(`任务 #${data.id} 已完成`, 'ok', 2500);
        if (data.status === 'error') toast(`任务 #${data.id} 失败：${data.error || ''}`, 'warn', 4000);
      }
      break;
    case 'adapter.detached':
      applyDetachedState(true, data.reason || '检测到用户接管鼠标');
      break;
    case 'adapter.reattached':
      applyDetachedState(false);
      break;
    case 'adapter.error':
      toast(data.message || '适配器错误', 'error', 5000);
      break;
    case 'adapter.log':
      if (data.level === 'error') toast(data.message, 'error', 5000);
      break;
    case 'settings.updated':
      if (state.appReady) loadSettings().catch(() => {});
      break;
    case 'ping':
      break;
    default:
      break;
  }
}

function scheduleGroupsReload() {
  if (!state.appReady) return;
  if (state.groupsReloadTimer) window.clearTimeout(state.groupsReloadTimer);
  state.groupsReloadTimer = window.setTimeout(async () => {
    state.groupsReloadTimer = null;
    try {
      await loadGroups();
    } catch {
      /* ignore */
    }
  }, 500);
}

/* ------------------------------------------------------------ image viewer */
function buildImageViewer() {
  if ($('#image-viewer')) return;
  const dialog = el('dialog', 'image-viewer');
  dialog.id = 'image-viewer';
  const img = el('img');
  img.alt = '表情包预览';
  dialog.appendChild(img);
  dialog.addEventListener('click', () => dialog.close());
  document.body.appendChild(dialog);
}

function openImageViewer(url, alt) {
  const dialog = $('#image-viewer');
  if (!dialog) return;
  const img = dialog.querySelector('img');
  img.src = url;
  img.alt = alt || '表情包预览';
  dialog.showModal();
}

/* ------------------------------------------------------------ events */
function bindEvents() {
  $('#login-adapters')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-adapter]');
    if (!button || button.disabled) return;
    state.selectedAdapter = button.dataset.adapter;
    try { localStorage.setItem('wechat-agent-adapter', state.selectedAdapter); } catch { /* ignore */ }
    renderAdapters();
    updateLoginUI();
  });
  $('#login-btn')?.addEventListener('click', startLogin);
  $('#logout-btn')?.addEventListener('click', logout);
  $('#refresh-groups')?.addEventListener('click', async () => {
    try {
      if (state.sidebarMode === 'contacts') {
        await loadContacts();
        toast('联系人已从微信同步');
        return;
      }
      const data = await api('/groups/refresh', { method: 'POST' });
      applyGroups(data.groups || []);
      toast('群列表已从微信刷新');
      if (!(data.groups || []).length) {
        toast('暂未读到群聊，请稍后再试或保持微信窗口打开', 'warn', 5000);
      }
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $$('#sidebar-mode button').forEach((button) => {
    button.addEventListener('click', () => setSidebarMode(button.dataset.sidebarMode));
  });
  $('#group-search')?.addEventListener('input', (event) => {
    state.groupQuery = event.target.value;
    if (state.sidebarMode === 'contacts') renderContactList();
    else renderGroupList();
  });
  $('#group-list')?.addEventListener('click', (event) => {
    const contactItem = event.target.closest('[data-contact-id]');
    if (contactItem) {
      openContactChat(parseIntSafe(contactItem.dataset.contactId)).catch((error) =>
        toast(error.message, 'error'),
      );
      return;
    }
    const item = event.target.closest('[data-group-id]');
    if (!item) return;
    selectGroup(parseIntSafe(item.dataset.groupId)).catch((error) => toast(error.message, 'error'));
  });
  $('#contact-send')?.addEventListener('click', sendContactMessage);
  $('#contact-input')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendContactMessage();
    }
  });
  $('#contact-admin-toggle')?.addEventListener('change', (event) => {
    if (state.selectedContactId) toggleContactAdmin(state.selectedContactId, event.target.checked);
  });
  $('#contact-agent-settings')?.addEventListener('click', () => {
    const contactId = state.selectedContactId;
    if (!contactId) return;
    $('#contact-chat-dialog')?.close();
    showTab('assistant');
    switchAssistantPanel('contacts');
    state.assistantContactId = contactId;
    loadAssistantContacts().catch((error) => toast(error.message, 'error'));
  });
  $('#chat-pin-btn')?.addEventListener('click', togglePinGroup);
  $('#chat-tools-btn')?.addEventListener('click', openChatTools);
  $('#reattach-btn')?.addEventListener('click', reattachAgent);
  $('#detached-dialog')?.addEventListener('cancel', (event) => event.preventDefault());
  $('#chat-read-toggle')?.addEventListener('change', (event) => toggleGroupRead(event.target.checked));
  $('#chat-agent-toggle')?.addEventListener('change', (event) => toggleGroupAgent(event.target.checked));
  $('#cfg-read')?.addEventListener('change', (event) => {
    const agent = $('#cfg-enabled');
    if (!agent) return;
    if (!event.target.checked) agent.checked = false;
    agent.disabled = !event.target.checked;
  });
  $('#members-refresh')?.addEventListener('click', openMembersDialog);
  $('#members-dialog-refresh')?.addEventListener('click', () => {
    if (state.selectedGroupId) loadMembers(state.selectedGroupId, true);
  });
  $('#load-more')?.addEventListener('click', loadOlderMessages);
  $('#inspector-toggle')?.addEventListener('click', () => $('#inspector')?.classList.toggle('open'));
  $('#inspector-close')?.addEventListener('click', () => $('#inspector')?.classList.remove('open'));
  $('#cfg-trigger')?.addEventListener('change', updateKeywordVisibility);
  $('#cfg-probability')?.addEventListener('input', (event) => {
    $('#cfg-probability-value').textContent = `${event.target.value}%`;
    setRangeFill(event.target);
  });
  $('#save-config-btn')?.addEventListener('click', saveGroupConfig);
  $('#refresh-logs')?.addEventListener('click', () => {
    if (state.selectedGroupId) loadLogs(state.selectedGroupId);
  });
  $('#refresh-chat-tasks')?.addEventListener('click', () => {
    if (state.selectedGroupId) loadChatTasks(state.selectedGroupId);
  });

  $('#composer-input')?.addEventListener('input', autoResizeComposer);
  $('#composer-input')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendMessage();
    }
  });
  $('#send-btn')?.addEventListener('click', sendMessage);
  $('#attachment-btn')?.addEventListener('click', () => $('#attachment-input')?.click());
  $('#attachment-input')?.addEventListener('change', (event) => {
    sendAttachment(event.target.files?.[0]);
  });
  $('#sticker-pick-btn')?.addEventListener('click', (event) => {
    event.stopPropagation();
    openStickerPicker();
  });
  $('#sticker-popover-grid')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-sticker-id]');
    if (!button) return;
    sendStickerNow(parseIntSafe(button.dataset.stickerId));
  });
  document.addEventListener('click', (event) => {
    const popover = $('#sticker-popover');
    if (!popover || popover.hidden) return;
    if (event.target.closest('#sticker-popover') || event.target.closest('#sticker-pick-btn')) return;
    closeStickerPopover();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeStickerPopover();
      $('#inspector')?.classList.remove('open');
    }
  });

  $$('.nav-tab').forEach((button) => {
    button.addEventListener('click', () => showTab(button.dataset.tab));
  });

  $$('.inspector-tabs button').forEach((button) => {
    button.addEventListener('click', () =>
      switchInspectorTab(button.dataset.inspectorTab),
    );
  });

  bindSettingsNavigation();
  bindCommandPalette();
  bindDialogBackdrops();

  $('#upload-sticker-btn')?.addEventListener('click', openUploadDialog);
  $('#sticker-empty-upload')?.addEventListener('click', openUploadDialog);
  $('#seed-stickers-btn')?.addEventListener('click', seedStickers);
  $('#sticker-search')?.addEventListener('input', (event) => {
    state.stickerQuery = event.target.value;
    renderStickers();
  });
  $('#sticker-tags')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-tag]');
    if (!button) return;
    state.stickerFilter = button.dataset.tag;
    renderStickerTags();
    renderStickers();
  });
  $('#sticker-grid')?.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const sticker = state.stickers.find((item) => item.id === parseIntSafe(target.dataset.id));
    if (!sticker) return;
    if (target.dataset.action === 'send') openSendDialog(sticker);
    else if (target.dataset.action === 'edit') openEditDialog(sticker);
    else if (target.dataset.action === 'delete') deleteSticker(sticker);
  });

  const fileInput = $('#upload-file');
  fileInput?.addEventListener('change', () => setUploadFile(fileInput.files?.[0]));
  const drop = $('#upload-drop');
  ['dragenter', 'dragover'].forEach((type) => {
    drop?.addEventListener(type, (event) => {
      event.preventDefault();
      drop.classList.add('dragging');
    });
  });
  ['dragleave', 'drop'].forEach((type) => {
    drop?.addEventListener(type, (event) => {
      event.preventDefault();
      drop.classList.remove('dragging');
    });
  });
  drop?.addEventListener('drop', (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) setUploadFile(file);
  });
  $('#upload-form')?.addEventListener('submit', submitUpload);
  $('#edit-form')?.addEventListener('submit', submitEdit);
  $('#send-form')?.addEventListener('submit', submitSend);
  $$('[data-close-dialog]').forEach((button) => {
    button.addEventListener('click', () => {
      const dialog = document.getElementById(button.dataset.closeDialog);
      if (dialog) dialog.close();
      if (dialog?.id === 'upload-dialog') resetUploadDialog();
    });
  });
  $$('dialog.modal').forEach((dialog) => {
    // Contact chat stays open until the user clicks its close button. A
    // second click on the contact row lands on the modal backdrop and used to
    // close the dialog immediately, which looked like "clicking does nothing".
    if (dialog.id === 'detached-dialog' || dialog.id === 'contact-chat-dialog') return;
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) {
        dialog.close();
        if (dialog.id === 'upload-dialog') resetUploadDialog();
      }
    });
  });

  $('#set-temperature')?.addEventListener('input', (event) => {
    $('#set-temperature-value').textContent = event.target.value;
    setRangeFill(event.target);
  });
  $('#save-settings-btn')?.addEventListener('click', saveSettings);
  $('#test-llm-btn')?.addEventListener('click', testLLM);
  $('#clear-key-btn')?.addEventListener('click', clearApiKey);
  $('#forget-memories-btn')?.addEventListener('click', forgetMemories);
  $('#refresh-stats')?.addEventListener('click', loadStats);
  bindToolActions();
  bindAssistantActions();

  window.addEventListener('online', () => connectWS());
}
