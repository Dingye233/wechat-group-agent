/* 云隐修仙录 · 联机层（群伴加的：世界聊天 / 组队副本 / 论武 / 排行榜 / 装备）
   不动原游戏存档与战斗，只是叠加一层社交与装备系统。
   授权提示：本文件属于群伴（MIT）；它寄宿的云隐修仙录本体是 CC BY-NC 4.0。 */
(() => {
  'use strict';
  if (window.__qunbanMP) return;
  window.__qunbanMP = true;

  const $ = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const api = async (path, body) => {
    const res = await fetch(path, body ? {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    } : undefined);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || '请求失败');
    return data;
  };

  const SLOTS = [['weapon', '兵刃'], ['head', '冠冕'], ['body', '衣甲'],
    ['wrist', '护腕'], ['belt', '腰带'], ['boots', '战靴']];
  const AFFIX_CN = { attack: '攻击', hit: '命中', crit: '会心', break: '破防',
    hp: '气血', def: '防御', block: '格挡', critres: '会心抗性' };
  const QUALITY = [['凡品', '#c8d3cf'], ['良品', '#7ce0a0'], ['上品', '#7fb6ff'],
    ['珍品', '#d59bff'], ['绝品', '#ffb45c'], ['神兵', '#ff6a5a']];

  const S = { name: '', state: null, tab: 'chat', ws: null, online: [], logLines: [] };

  // ------------------------------------------------------------ 样式
  const css = document.createElement('style');
  css.textContent = `
  #qmp-fab{position:fixed;right:16px;bottom:96px;z-index:99998;width:52px;height:52px;border-radius:50%;
    border:1px solid #8a6a2f;background:radial-gradient(circle at 40% 30%,#2b2418,#14100a 70%);
    color:#e8c06a;font:600 18px/1 "Songti SC",serif;display:grid;place-items:center;cursor:pointer;
    box-shadow:0 8px 24px #000a, inset 0 1px 0 #ffffff22}
  #qmp-fab span{transform:translateY(-1px)}
  #qmp-panel{position:fixed;right:16px;bottom:158px;z-index:99999;width:min(380px,92vw);max-height:min(70vh,620px);
    display:none;flex-direction:column;border-radius:16px;overflow:hidden;
    background:linear-gradient(180deg,#1a1710f2,#0e0c08f7);border:1px solid #6b5330;
    box-shadow:0 24px 60px #000b;color:#e9dcc4;font:14px/1.6 "Songti SC","Noto Serif SC",serif}
  #qmp-panel.open{display:flex}
  #qmp-head{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid #4a3a1f;
    background:linear-gradient(180deg,#2a2114,#191307)}
  #qmp-head b{color:#e8c06a;letter-spacing:.14em;font-size:15px}
  #qmp-head .cp{margin-left:auto;font-size:12px;color:#9fc6a8}
  #qmp-close{border:0;background:transparent;color:#c9b489;font-size:18px;cursor:pointer}
  #qmp-tabs{display:grid;grid-template-columns:repeat(5,1fr);border-bottom:1px solid #3b2f1a}
  #qmp-tabs button{border:0;background:transparent;color:#a99b7e;padding:8px 0;font:inherit;font-size:13px;cursor:pointer}
  #qmp-tabs button.on{box-shadow:inset 0 -2px 0 #c9a24a}
  #qmp-body{flex:1;overflow:auto;padding:10px 12px;display:flex;flex-direction:column;gap:8px}
  .qmp-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .qmp-btn{border:1px solid #6b5330;background:linear-gradient(180deg,#2c2415,#1a1409);color:#e9dcc4;
    padding:6px 12px;border-radius:9px;font:inherit;font-size:13px;cursor:pointer}
  .qmp-btn.primary{background:linear-gradient(180deg,#4a3a18,#2b2210);border-color:#a58333;color:#ffe9b0}
  .qmp-btn:disabled{opacity:.45}
  .qmp-input{flex:1;min-width:90px;padding:7px 10px;border-radius:9px;border:1px solid #4a3a1f;
    background:#0b0906;color:#e9dcc4;font:inherit;font-size:13px}
  .qmp-log{background:#0a0805;border:1px solid #3b2f1a;border-radius:10px;padding:8px;height:190px;overflow:auto;
    font-size:13px;white-space:pre-wrap}
  .qmp-chat{height:210px;overflow:auto;background:#0a0805;border:1px solid #3b2f1a;border-radius:10px;padding:8px;font-size:13px}
  .qmp-chat p{margin:0 0 3px}
  .qmp-chat .who{color:#9fd3b6}
  .qmp-muted{color:#8d8168;font-size:12px}
  .qmp-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
  .qmp-slot{border:1px solid #4a3a1f;border-radius:10px;padding:8px;background:#120e08;cursor:pointer}
  .qmp-slot .nm{font-size:13px}
  .qmp-slot .af{font-size:11.5px;color:#a99b7e;line-height:1.5}
  .qmp-list{display:flex;flex-direction:column;gap:6px}
  .qmp-item{display:flex;align-items:center;gap:8px;border:1px solid #3b2f1a;border-radius:9px;padding:6px 9px;background:#110d07}
  .qmp-item .grow{flex:1;font-size:13px}
  .qmp-tag{font-size:11px;border:1px solid #4a3a1f;border-radius:99px;padding:1px 7px;color:#c9b489}
  `;
  document.head.appendChild(css);

  // ------------------------------------------------------------ DOM
  const fab = el('div');
  fab.id = 'qmp-fab';
  fab.innerHTML = '<span>联</span>';
  const panel = el('div');
  panel.id = 'qmp-panel';
  panel.innerHTML = `
    <div id="qmp-head"><b>联机 · 问道</b><span class="cp" id="qmp-cp">未同步</span>
      <button id="qmp-close">×</button></div>
    <div id="qmp-tabs">
      <button data-tab="chat" class="on">世界</button>
      <button data-tab="team">队伍</button>
      <button data-tab="duel">论武</button>
      <button data-tab="rank">排行</button>
      <button data-tab="gear">装备</button>
    </div>
    <div id="qmp-body"></div>`;
  document.body.append(fab, panel);

  const body = $('#qmp-body', panel);
  const cpEl = $('#qmp-cp', panel);

  fab.addEventListener('click', () => {
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) ensureName().then(() => render());
  });
  $('#qmp-close', panel).addEventListener('click', () => panel.classList.remove('open'));
  panel.querySelectorAll('#qmp-tabs button').forEach((btn) => {
    btn.addEventListener('click', () => {
      S.tab = btn.dataset.tab;
      panel.querySelectorAll('#qmp-tabs button').forEach((b) => b.classList.toggle('on', b === btn));
      render();
    });
  });

  // ------------------------------------------------------------ 名字 / 同步
  function guessName() {
    const saved = localStorage.getItem('qmp.name');
    if (saved) return saved;
    try {
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (!/yunyin|xiuxian|save|player/i.test(key)) continue;
        const raw = localStorage.getItem(key) || '';
        const m = raw.match(/"(?:name|playerName|daohao)"\s*:\s*"([^"]{1,16})"/);
        if (m) return m[1];
      }
    } catch (e) { /* ignore */ }
    return '';
  }

  async function ensureName() {
    if (S.name && S.state) return;
    let name = guessName();
    if (!name) {
      name = (window.prompt('输入你在游戏里的道号（用于联机同步）') || '').trim().slice(0, 16);
    }
    if (!name) return;
    S.name = name;
    localStorage.setItem('qmp.name', name);
    try {
      const data = await api('/api/rpg/mp/sync', { name, realm: '', base_cp: 0 });
      S.state = data.state;
      cpEl.textContent = `战力 ${data.state.cp}`;
      connectWs();
    } catch (err) {
      cpEl.textContent = err.message;
    }
  }

  function connectWs() {
    if (S.ws && S.ws.readyState <= 1) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/api/rpg/mp/ws?name=${encodeURIComponent(S.name)}`);
    S.ws = ws;
    ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === 'chat') {
        pushChat(`${m.name}`, m.text);
      } else if (m.type === 'presence') {
        S.online = m.players || [];
        if (S.tab === 'duel') render();
      } else if (m.type === 'world' || m.type === 'dungeon' || m.type === 'duel' || m.type === 'party') {
        logLine(m.text || '');
        if (S.tab === 'rank' || S.tab === 'duel') render();
      }
    };
    ws.onclose = () => setTimeout(() => { if (S.name) connectWs(); }, 4000);
  }

  function pushChat(who, text) {
    const box = $('#qmp-chatlog');
    if (box) {
      const p = el('p');
      p.append(el('span', 'who', who + '：'), document.createTextNode(text));
      box.appendChild(p);
      box.scrollTop = box.scrollHeight;
    }
    S.logLines.push(`${who}：${text}`);
  }
  function logLine(text) {
    S.logLines.push(text);
    const box = $('#qmp-log');
    if (box) {
      box.textContent = S.logLines.slice(-40).join('\n');
      box.scrollTop = box.scrollHeight;
    }
  }

  // ------------------------------------------------------------ 各 tab
  async function render() {
    if (!S.name) {
      body.innerHTML = '';
      const tip = el('div', 'qmp-muted', '点一下「联」先同步你的道号。');
      const btn = el('button', 'qmp-btn primary', '同步道号');
      btn.addEventListener('click', () => ensureName().then(render));
      body.append(tip, btn);
      return;
    }
    if (S.tab === 'chat') renderChat();
    else if (S.tab === 'team') await renderTeam();
    else if (S.tab === 'duel') await renderDuel();
    else if (S.tab === 'rank') await renderRank();
    else renderGear();
  }

  function renderChat() {
    body.innerHTML = '';
    const log = el('div', 'qmp-chat');
    log.id = 'qmp-chatlog';
    S.logLines.slice(-40).forEach((line) => {
      const idx = line.indexOf('：');
      const p = el('p');
      if (idx > 0) p.append(el('span', 'who', line.slice(0, idx + 1)), document.createTextNode(line.slice(idx + 1)));
      else p.textContent = line;
      log.appendChild(p);
    });
    const row = el('div', 'qmp-row');
    const input = el('input', 'qmp-input');
    input.placeholder = '世界频道说点什么…';
    input.maxLength = 120;
    const send = el('button', 'qmp-btn primary', '传音');
    const fire = () => {
      const text = (input.value || '').trim();
      if (!text || !S.ws || S.ws.readyState !== 1) return;
      S.ws.send(JSON.stringify({ type: 'chat', text }));
      input.value = '';
    };
    send.addEventListener('click', fire);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') fire(); });
    row.append(input, send);
    body.append(log, row, el('div', 'qmp-muted', `在线：${S.online.join('、') || '只有你'}`));
    log.scrollTop = log.scrollHeight;
  }

  async function renderTeam() {
    body.innerHTML = '';
    const info = el('div', 'qmp-muted', '组队副本：单人也能开，人越多越稳。');
    const row1 = el('div', 'qmp-row');
    const createBtn = el('button', 'qmp-btn primary', '创建队伍');
    const codeInput = el('input', 'qmp-input');
    codeInput.placeholder = '队伍编号';
    codeInput.maxLength = 3;
    const joinBtn = el('button', 'qmp-btn', '加入');
    row1.append(createBtn, codeInput, joinBtn);
    const row2 = el('div', 'qmp-row');
    const startBtn = el('button', 'qmp-btn primary', '开始副本');
    const leaveBtn = el('button', 'qmp-btn', '离队');
    row2.append(startBtn, leaveBtn);
    const teamBox = el('div', 'qmp-list');
    const log = el('div', 'qmp-log');
    body.append(info, row1, row2, teamBox, log);

    const refresh = async () => {
      try {
        const d = await api('/api/rpg/mp/teams');
        teamBox.innerHTML = '';
        (d.teams || []).forEach((t) => {
          const item = el('div', 'qmp-item');
          item.append(el('span', 'grow', `${t.code} · 队长 ${t.leader} · ${t.members.length}/4`));
          teamBox.appendChild(item);
        });
        if (!(d.teams || []).length) teamBox.append(el('div', 'qmp-muted', '当前没有队伍'));
      } catch {}
    };
    createBtn.addEventListener('click', async () => {
      try {
        const d = await api('/api/rpg/mp/team/create', { name: S.name });
        codeInput.value = d.team.code;
        logLine(`🤝 建队成功：${d.team.code}`);
        refresh();
      } catch (err) { logLine(err.message); }
    });
    joinBtn.addEventListener('click', async () => {
      try {
        const d = await api('/api/rpg/mp/team/join', { name: S.name, code: codeInput.value.trim() });
        logLine(`➕ 已加入队伍 ${d.team.code}`);
        refresh();
      } catch (err) { logLine(err.message); }
    });
    leaveBtn.addEventListener('click', async () => {
      try {
        await api('/api/rpg/mp/team/leave', { name: S.name });
        logLine('👋 已离队');
        refresh();
      } catch (err) { logLine(err.message); }
    });
    startBtn.addEventListener('click', async () => {
      startBtn.disabled = true;
      try {
        const d = await api('/api/rpg/mp/dungeon', { name: S.name });
        (d.log || []).forEach(logLine);
        (d.rewards || []).forEach((r) => {
          if (r.item) logLine(`　${r.name} 获得【${r.item.name}】${r.item.attrs || ''} 灵石 +${r.stones}`);
          else logLine(`　${r.name} 无缘战利品`);
        });
        refreshState();
      } catch (err) { logLine(err.message); }
      finally { startBtn.disabled = false; }
    });
    refresh();
    log.textContent = S.logLines.slice(-30).join('\n');
  }

  async function renderDuel() {
    body.innerHTML = '';
    body.append(el('div', 'qmp-muted', '点在线道友的名字即可论武（战力高者未必赢，有浮动）。'));
    const list = el('div', 'qmp-list');
    body.append(list);
    let players = [];
    try {
      const d = await api('/api/rpg/mp/leaderboard?limit=20');
      players = d.players || [];
    } catch {}
    players.forEach((p) => {
      const item = el('div', 'qmp-item');
      item.append(el('span', 'grow', `${p.rank}. ${p.name} · ${p.realm} · 战力 ${p.cp}`));
      const btn = el('button', 'qmp-btn', p.name === S.name ? '自己' : '挑战');
      btn.disabled = p.name === S.name;
      btn.addEventListener('click', async () => {
        try {
          const r = await api('/api/rpg/mp/duel', { from: S.name, to: p.name });
          (r.log || []).forEach(logLine);
          logLine(r.winner === S.name ? '🏆 你赢了！' : '💀 惜败，再练练');
          refreshState();
          render();
        } catch (err) { logLine(err.message); }
      });
      item.appendChild(btn);
      list.appendChild(item);
    });
    if (!players.length) list.append(el('div', 'qmp-muted', '还没有人上榜'));
    const log = el('div', 'qmp-log');
    log.textContent = S.logLines.slice(-30).join('\n');
    body.append(log);
  }

  async function renderRank() {
    body.innerHTML = '';
    const list = el('div', 'qmp-list');
    let data = { players: [], records: [] };
    try { data = await api('/api/rpg/mp/leaderboard?limit=30'); } catch {}
    (data.players || []).forEach((p) => {
      const item = el('div', 'qmp-item');
      item.append(el('span', 'grow', `${p.rank}. ${p.name}`));
      item.append(el('span', 'qmp-tag', p.realm));
      item.append(el('span', 'qmp-tag', `战力 ${p.cp}`));
      item.append(el('span', 'qmp-tag', `${p.wins}胜${p.losses}负`));
      list.appendChild(item);
    });
    body.append(el('div', 'qmp-muted', '天骄榜'), list);
    const recs = el('div', 'qmp-log');
    recs.textContent = (data.records || []).map((r) => r.text).join('\n') || '暂无战报';
    body.append(el('div', 'qmp-muted', '最近战报'), recs);
  }

  function renderGear() {
    body.innerHTML = '';
    const st = S.state;
    if (!st) { body.append(el('div', 'qmp-muted', '先同步道号')); return; }
    const top = el('div', 'qmp-row');
    top.append(el('span', 'qmp-muted', `战力 ${st.cp}　灵石 ${st.stones}`));
    const drop = el('button', 'qmp-btn primary', '求一件装备');
    drop.addEventListener('click', async () => {
      try {
        const r = await api('/api/rpg/mp/gear/equip', { name: S.name });
        logLine(`✨ 得到【${r.item.name}】${fmtAffix(r.item)}`);
        S.state = r.state; refreshState();
        render();
      } catch (err) { logLine(err.message); }
    });
    top.append(drop);
    body.append(top);

    const grid = el('div', 'qmp-grid');
    SLOTS.forEach(([slot, cn]) => {
      const item = (st.gear || {})[slot];
      const box = el('div', 'qmp-slot');
      const nm = el('div', 'nm');
      if (item) {
        nm.textContent = `${item.name}${item.enhance ? ' +' + item.enhance : ''}`;
        nm.style.color = (QUALITY[item.quality] || QUALITY[0])[1];
      } else {
        nm.textContent = `${cn}（空）`;
        nm.style.color = '#8d8168';
      }
      box.append(nm, el('div', 'af', item ? fmtAffix(item) : '点「求一件装备」随机获得'));
      box.addEventListener('click', () => openSlot(slot, cn, item));
      grid.appendChild(box);
    });
    body.append(grid);
    const log = el('div', 'qmp-log');
    log.textContent = S.logLines.slice(-20).join('\n');
    body.append(log);
  }

  function fmtAffix(item) {
    return Object.entries(item.affixes || {})
      .map(([k, v]) => `${AFFIX_CN[k] || k}+${v}`).join('　');
  }

  function openSlot(slot, cn, item) {
    const box = $('#qmp-body', panel);
    box.innerHTML = '';
    const back = el('button', 'qmp-btn', '← 返回');
    back.addEventListener('click', render);
    box.append(back, el('div', 'qmp-muted', item ? `${cn}：${item.name}` : `${cn}：空`));
    if (!item) {
      const btn = el('button', 'qmp-btn primary', '随机获得一件');
      btn.addEventListener('click', async () => {
        const r = await api('/api/rpg/mp/gear/equip', { name: S.name, slot });
        logLine(`✨ 得到【${r.item.name}】`);
        S.state = r.state; refreshState(); render();
      });
      box.append(btn);
      return;
    }
    box.append(el('div', 'qmp-muted', fmtAffix(item) + `　评分 ${item.score}`));
    const row = el('div', 'qmp-row');
    const enh = el('button', 'qmp-btn primary', `强化（${(item.enhance || 0) >= 15 ? '已满' : '消耗灵石'}）`);
    enh.disabled = (item.enhance || 0) >= 15;
    enh.addEventListener('click', async () => {
      try {
        const r = await api('/api/rpg/mp/gear/enhance', { name: S.name, slot });
        logLine(r.message);
        if (r.state) { S.state = r.state; refreshState(); }
        render(); openSlot(slot, cn, (S.state.gear || {})[slot]);
      } catch (err) { logLine(err.message); }
    });
    const reroll = el('button', 'qmp-btn', '洗练词条（80 灵石）');
    reroll.addEventListener('click', async () => {
      try {
        const r = await api('/api/rpg/mp/gear/reroll', { name: S.name, slot });
        logLine(r.message);
        if (r.state) { S.state = r.state; refreshState(); }
        render(); openSlot(slot, cn, (S.state.gear || {})[slot]);
      } catch (err) { logLine(err.message); }
    });
    row.append(enh, reroll);
    box.append(row);
  }

  async function refreshState() {
    try {
      const d = await api(`/api/rpg/mp/state?name=${encodeURIComponent(S.name)}`);
      S.state = d.state;
      cpEl.textContent = `战力 ${d.state.cp}　灵石 ${d.state.stones}`;
    } catch {}
  }

  // 开局静默同步一次（如果本地记得名字）
  const cached = guessName();
  if (cached) {
    S.name = cached;
    api('/api/rpg/mp/sync', { name: cached }).then((d) => {
      S.state = d.state;
      cpEl.textContent = `战力 ${d.state.cp}`;
      connectWs();
    }).catch(() => {});
  }
})();
