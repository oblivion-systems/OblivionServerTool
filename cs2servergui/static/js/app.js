/**
 * app.js — Oblivion Server Tool SPA
 * Hash-based routing (#status, #players, #maps, #workshop, #config).
 * All state polling runs on intervals; SSE feeds the live log.
 */

/* ══════════════════════════════════════════════════════════════ UTILITIES */

function el(id) { return document.getElementById(id); }
function h(tag, cls, content = '') {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (content) e.innerHTML = content;
  return e;
}
function icon(d) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
}

function toast(msg, color = 'var(--accent)') {
  const t = el('toast');
  t.textContent = msg;
  t.style.background = color;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}

function copyText(text, label = 'Copied') {
  navigator.clipboard.writeText(text).then(() => toast(`${label} copied`));
}

function fmtUptime(secs) {
  if (!secs) return '—';
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

function modal(titleText, bodyHtml, onConfirm, confirmLabel = 'Confirm') {
  const ov = h('div', 'modal-overlay');
  ov.innerHTML = `
    <div class="modal">
      <div class="modal-title">${titleText}</div>
      <div class="modal-body">${bodyHtml}</div>
      <div class="modal-actions">
        <button class="btn btn-ghost" id="modal-cancel">Cancel</button>
        <button class="btn btn-accent" id="modal-ok">${confirmLabel}</button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  ov.querySelector('#modal-cancel').onclick = () => ov.remove();
  ov.querySelector('#modal-ok').onclick = () => { ov.remove(); onConfirm(ov); };
  ov.addEventListener('click', e => { if (e.target === ov) ov.remove(); });
  return ov;
}

/* ══════════════════════════════════════════════════════════════ APP SETTINGS */

const _SETTINGS_KEY = 'oblivion_app_settings';

const appSettings = {
  theme:         'dark',    // 'dark' | 'light' | 'system'
  accent:        'purple',  // 'purple' | 'blue' | 'teal' | 'green' | 'orange' | 'red'
  compact:       false,
  confirmStop:   true,
  autoScroll:    true,
  logLines:      400,
  notifications: false,
  keybinds: {
    stop:         '',
    quickRestart: '',
    pause:        '',
    unpause:      '',
    restartRound: '',
    endWarmup:    '',
    addBot:       '',
    kickBots:     '',
  },
};

function loadAppSettings() {
  try {
    const s = JSON.parse(localStorage.getItem(_SETTINGS_KEY) || '{}');
    // Deep-merge keybinds so newly-added actions survive old localStorage data
    const { keybinds: storedKb, ...rest } = s;
    Object.assign(appSettings, rest);
    if (storedKb && typeof storedKb === 'object') {
      Object.assign(appSettings.keybinds, storedKb);
    }
  } catch (_) {}
  applyAppSettings();
}

function saveAppSettings() {
  localStorage.setItem(_SETTINGS_KEY, JSON.stringify(appSettings));
}

function _resolvedTheme() {
  if (appSettings.theme === 'system')
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  return appSettings.theme;
}

function applyAppSettings() {
  const body = document.body;

  // Theme
  body.classList.toggle('theme-light', _resolvedTheme() === 'light');

  // Accent
  ['purple','blue','teal','green','orange','red'].forEach(a =>
    body.classList.toggle('accent-' + a, appSettings.accent === a && a !== 'purple')
  );

  // Compact
  body.classList.toggle('compact', appSettings.compact);
}

/* ══════════════════════════════════════════════════════════════ KEYBINDS */

const KB_ACTIONS = [
  { id: 'stop',         label: 'Stop Server'   },
  { id: 'quickRestart', label: 'Quick Restart' },
  { id: 'pause',        label: 'Pause Match'   },
  { id: 'unpause',      label: 'Unpause Match' },
  { id: 'restartRound', label: 'Restart Round' },
  { id: 'endWarmup',    label: 'End Warmup'    },
  { id: 'addBot',       label: 'Add 1 Bot'     },
  { id: 'kickBots',     label: 'Kick All Bots' },
];

/** Turns a KeyboardEvent into a stable string like "F2" or "Ctrl+Shift+R". */
function _keyStr(e) {
  if (['Control','Alt','Shift','Meta'].includes(e.key)) return '';
  const parts = [];
  if (e.ctrlKey)  parts.push('Ctrl');
  if (e.altKey)   parts.push('Alt');
  if (e.shiftKey) parts.push('Shift');
  const keyName = e.key === ' ' ? 'Space' : (e.key.length === 1 ? e.key.toUpperCase() : e.key);
  parts.push(keyName);
  return parts.join('+');
}

/** Restart the server using the same map/mode it was running. */
async function doQuickRestart() {
  if (!state.server.running) { toast('Server is not running', 'var(--sub)'); return; }
  const map        = state.server.map;
  const mode       = state.server.mode;
  const isWorkshop = state.workshopMaps.some(m => m.id === map);
  const btn        = el('status-restart-btn');
  if (btn) { btn.disabled = true; btn.innerHTML = icon('<circle cx="12" cy="12" r="10"/>'); }

  // Pause the background poll so it can't re-enable the button mid-restart
  clearInterval(_stateInterval);
  _stateInterval = null;

  try {
    await api.stop();
    toast('Restarting — waiting for shutdown…', 'var(--blue)');
    // Poll until offline (up to 30 s)
    for (let i = 0; i < 20; i++) {
      await new Promise(r => setTimeout(r, 1500));
      await pollState();
      if (!state.server.running) break;
    }
    if (state.server.running) { toast('Server did not stop in time', 'var(--red)'); return; }
    await api.start(map, mode, isWorkshop);
    toast('Server restarted ↺', 'var(--green)');
  } catch (e) {
    toast(e.message, 'var(--red)');
  } finally {
    // Always restore the poll interval
    _stateInterval = setInterval(pollState, 3000);
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = icon('<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.25"/>');
    }
  }
}

/** Execute a keybind action by id. */
async function _runKeybind(action) {
  try {
    switch (action) {
      case 'stop':
        if (appSettings.confirmStop) {
          modal('Stop Server?', 'This will disconnect all players.', async () => {
            try { await api.stop(); toast('Server stopping…'); }
            catch (e) { toast(e.message, 'var(--red)'); }
          }, 'Stop');
        } else {
          await api.stop();
          toast('⌨ Stop Server');
        }
        return;
      case 'quickRestart': await doQuickRestart();       return;
      case 'pause':        await api.pause();            break;
      case 'unpause':      await api.unpause();          break;
      case 'restartRound': await api.restartRound();     break;
      case 'endWarmup':    await api.endWarmup();        break;
      case 'addBot':       await api.addBots(1);         break;
      case 'kickBots':     await api.kickBots();         break;
      default: return;
    }
    const label = KB_ACTIONS.find(a => a.id === action)?.label || action;
    toast(`⌨ ${label}`, 'var(--accent)');
  } catch (e) {
    toast(e.message, 'var(--red)');
  }
}

/* ══════════════════════════════════════════════════════════════ STATE */

const state = {
  server: {
    running: false, is_installed: false, boot_state: 'offline', player_count: 0,
    map: '', mode: '', uptime: 0, ff_enabled: false, update_available: false,
    public_ip: '', lan_ip: '', rcon_port: 27015, flask_port: 5000,
    is_local: false, dl_active: false,
  },
  modes: [],
  maps:  [],
  modeMaps: {},
  modeWorkshopTags: {},
  workshopMaps: [],
  _uptimeTimer: null,
  _localUptime: 0,
};

/* ══════════════════════════════════════════════════════════════ ROUTER */

const pages = {};
let currentPage = '';

function navigate(page) {
  if (!page || !pages[page]) page = 'status';
  if (currentPage === page) return;
  currentPage = page;

  // Update nav
  document.querySelectorAll('.nav-item').forEach(a => {
    a.classList.toggle('active', a.dataset.page === page);
  });

  // Render
  const content = el('content');
  content.innerHTML = '';
  pages[page]();
}

window.addEventListener('hashchange', () => {
  navigate(location.hash.replace('#', '') || 'status');
});

/* ══════════════════════════════════════════════════════════════ STATE POLLING */

function _notify(title, body) {
  if (!appSettings.notifications) return;
  if (Notification.permission !== 'granted') return;
  new Notification(title, { body, icon: '/static/images/emblem.png' });
}

function applyState(s) {
  const old    = state.server.running;
  const oldBoot = state.server.boot_state;
  Object.assign(state.server, s);

  // Browser notifications on state transitions
  if (s.boot_state === 'ready'   && oldBoot !== 'ready')   _notify('Server Online',  `Map: ${s.map || '—'}  Mode: ${s.mode || '—'}`);
  if (s.boot_state === 'offline' && oldBoot !== 'offline' && old)  _notify('Server Offline', 'The server has stopped.');

  // Header dot + label
  const dot   = el('hdr-dot');
  const label = el('hdr-state-label');
  if (s.boot_state === 'ready')   { dot.className = 'state-dot online';  label.textContent = 'Online'; }
  else if (s.boot_state === 'booting') { dot.className = 'state-dot booting'; label.textContent = 'Booting…'; }
  else                            { dot.className = 'state-dot offline'; label.textContent = 'Offline'; }

  // Status bar
  el('sb-map').textContent   = s.map  || '—';
  el('sb-mode').textContent  = s.mode || '—';
  el('sb-uptime').textContent = fmtUptime(s.uptime);

  if (s.lan_ip) {
    const lan = `${s.lan_ip}:${s.rcon_port}`;
    el('sb-lan-val').textContent = lan;
    el('sb-lan').onclick = () => copyText(`connect ${lan}`, 'Connect string');
  }
  if (s.public_ip) el('sb-pub-val').textContent = s.public_ip;

  const webUrl = `http://${s.lan_ip}:${s.flask_port}`;
  el('sb-web-url').textContent = webUrl;
  el('sb-web-url').onclick = () => window.open(webUrl, '_blank');

  // Update badges
  const appBadge = el('app-update-badge');
  const cs2Badge = el('cs2-update-badge');
  if (s.app_update) { appBadge.textContent = `⬆ App ${s.app_version}`; appBadge.classList.remove('hidden'); }
  if (s.update_available) { cs2Badge.textContent = '⬆ CS2 Update'; cs2Badge.classList.remove('hidden'); }

  // Local uptime counter (client-side increments between polls)
  clearInterval(state._uptimeTimer);
  if (s.boot_state === 'ready' && s.uptime > 0) {
    state._localUptime = s.uptime;
    state._uptimeTimer = setInterval(() => {
      state._localUptime++;
      el('sb-uptime').textContent = fmtUptime(state._localUptime);
    }, 1000);
  }

  // Re-render status page if it's active
  if (currentPage === 'status') renderStatusState();
}

async function pollState() {
  try {
    const s = await api.state();
    applyState(s);
  } catch (e) {
    // Server unreachable — just keep going
  }
}

/* ══════════════════════════════════════════════════════════════ LOG / SSE */

let logLines      = [];
let logEs         = null;
let _dlStatus     = { active: false, text: '' };   // live workshop download state
let _stateInterval = null;                          // stored so doQuickRestart can pause it

function _updateDlStatusUI() {
  if (currentPage !== 'workshop') return;
  const txt       = el('ws-status-text');
  const bar       = el('ws-progress');
  const dlBtn     = el('ws-dl-btn');
  const cancelBtn = el('ws-cancel-btn');
  if (!txt || !bar) return;
  if (_dlStatus.text) txt.textContent = _dlStatus.text;
  if (_dlStatus.active) {
    bar.classList.remove('done');
    bar.classList.add('active');
  } else if (_dlStatus.text) {
    // Download finished — show result, restore Download button
    bar.classList.remove('active');
    bar.classList.add('done');
    if (dlBtn)     dlBtn.classList.remove('hidden');
    if (cancelBtn) cancelBtn.classList.add('hidden');
    // Refresh the maps grid so the new map appears immediately
    const grid = el('ws-maps-grid');
    if (grid && state.server.mode) loadWorkshopMapsGrid(grid, state.server.mode);
  }
}

function appendLog(line) {
  const limit = appSettings.logLines || 400;
  logLines.push(line);
  if (logLines.length > limit) logLines.shift();
  if (currentPage === 'status') {
    const lb = el('log-body');
    if (lb) {
      const div = document.createElement('div');
      div.className = 'log-line';
      div.textContent = line;
      lb.appendChild(div);
      if (lb.children.length > limit) lb.removeChild(lb.firstChild);
      if (appSettings.autoScroll) lb.scrollTop = lb.scrollHeight;
    }
  }
  // ── Track workshop download status ──────────────────────────────────────
  const t = line.trim();
  if (t.includes('WORKSHOP DOWNLOAD') || t.includes('workshop ID →')) {
    _dlStatus = { active: true, text: 'Starting download…' };
  } else if (t.startsWith('[dd]') || t.includes('  [dd]')) {
    const msg = t.replace(/.*\[dd\]\s*/, '').trim();
    if (msg) _dlStatus = { active: true, text: msg };
  } else if (t.includes('… downloading')) {
    _dlStatus = { active: true, text: t.replace(/^\s+/, '') };
  } else if (t.includes('DOWNLOAD COMPLETE')) {
    _dlStatus = { active: false, text: '✓ Download complete' };
  } else if (t.includes('download FAILED') || (t.includes('FAILED') && _dlStatus.active)) {
    _dlStatus = { active: false, text: '✗ Download failed — check the log' };
  } else if (t.includes('Download cancelled')) {
    _dlStatus = { active: false, text: '' };
  }
  _updateDlStatusUI();
}

function startSSE() {
  if (logEs) logEs.close();
  logEs = new EventSource('/api/log/stream');
  logEs.onmessage = e => appendLog(e.data);
  logEs.onerror   = () => { logEs.close(); setTimeout(startSSE, 5000); };
}

async function loadLogHistory() {
  try {
    const lines = await api.logHistory();
    logLines = lines.slice(-400);
  } catch (_) {}
}

/* ══════════════════════════════════════════════════════════════ STATUS PAGE */

function renderStatusState() {
  const s = state.server;
  const circle = el('status-circle');
  const ring   = el('status-ring');
  const lbl    = el('status-label');
  const upt    = el('status-uptime');
  if (!circle) return;

  const cls = s.boot_state === 'ready'   ? 'online'
            : s.boot_state === 'booting' ? 'booting'
            : 'offline';
  circle.className = `status-circle ${cls}`;
  if (ring) ring.className = `status-glow-ring ${cls}`;
  lbl.className    = `status-label ${cls}`;
  lbl.textContent  = cls === 'online' ? 'Online'
                   : cls === 'booting' ? 'Booting…'
                   : 'Offline';
  upt.textContent  = s.boot_state === 'ready' ? fmtUptime(state._localUptime) : '';

  // Install-needed banner
  const existingBanner = el('install-banner');
  const showBanner = s.server_dir && !s.is_installed && !s.running;
  if (showBanner && !existingBanner) {
    const banner = h('div', 'install-banner', `
      ${icon('<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>')}
      CS2 is not installed in the configured directory.
      <a href="#config" class="install-banner-link">Go to Config → Install</a>`);
    banner.id = 'install-banner';
    const hero = document.querySelector('.status-hero');
    if (hero) hero.parentNode.insertBefore(banner, hero);
  } else if (!showBanner && existingBanner) {
    existingBanner.remove();
  }

  // Start / Stop / Restart buttons
  const startBtn   = el('status-start-btn');
  const stopBtn    = el('status-stop-btn');
  const restartBtn = el('status-restart-btn');
  if (startBtn) {
    const cantStart = s.running || !s.is_installed;
    startBtn.disabled   = cantStart;
    startBtn.title      = !s.is_installed ? 'CS2 not installed — go to Config → Install' : '';
    stopBtn.disabled    = !s.running;
    if (restartBtn) restartBtn.disabled = !s.running;
  }

  // Action tiles enabled only when running
  document.querySelectorAll('.action-tile').forEach(t => {
    t.style.opacity = s.running ? '1' : '.4';
    t.style.pointerEvents = s.running ? '' : 'none';
  });

  // FF tile
  const ffTile = el('ff-tile');
  if (ffTile) {
    ffTile.classList.toggle('accent', s.ff_enabled);
    ffTile.title = s.ff_enabled ? 'Friendly Fire ON — click to disable' : 'Friendly Fire OFF — click to enable';
  }

  // Map ghost + live info
  const ghostImg  = el('status-map-ghost-img');
  const liveInfo  = el('live-map-info');
  const liveMapEl = el('live-map-name');
  const livePcEl  = el('live-player-count');
  if (ghostImg && liveInfo) {
    const isReady = s.boot_state === 'ready';
    const mapKey  = isReady && s.map ? s.map : '';
    // Only swap the image when the map actually changes (avoids reload flicker)
    if ((ghostImg.dataset.mapKey || '') !== mapKey) {
      ghostImg.dataset.mapKey = mapKey;
      ghostImg.classList.remove('loaded');
      if (mapKey) {
        ghostImg.onload  = () => ghostImg.classList.add('loaded');
        ghostImg.onerror = () => { /* leave faded-out */ };
        ghostImg.src = `/api/maps/thumb/${mapKey}`;
      } else {
        ghostImg.src = '';
      }
    }
    // Text overlay
    if (liveMapEl)  liveMapEl.textContent  = mapKey;
    if (livePcEl) {
      const pc = s.player_count || 0;
      livePcEl.textContent = isReady ? `${pc} player${pc !== 1 ? 's' : ''}` : '';
    }
    liveInfo.classList.toggle('visible', isReady);
  }
}

function buildStatusPage() {
  const s = state.server;
  const root = el('content');

  // ── Server control + map/mode ──────────────────────────────────────────
  const hero = h('div', 'status-hero');

  // Left: status indicator
  const indCard = h('div', 'status-indicator-card');
  indCard.innerHTML = `
    <div class="map-ghost" id="status-map-ghost">
      <img id="status-map-ghost-img" src="" alt="" draggable="false">
    </div>
    <div class="status-glow-ring offline" id="status-ring">
      <div class="status-circle offline" id="status-circle">
        ${icon('<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>')}
      </div>
    </div>
    <div class="status-label offline" id="status-label">Offline</div>
    <div class="status-uptime" id="status-uptime"></div>
    <div class="status-controls">
      <button class="btn btn-green flex-1" id="status-start-btn">
        ${icon('<polygon points="5 3 19 12 5 21 5 3"/>')} Start
      </button>
      <button class="btn btn-blue btn-icon" id="status-restart-btn" title="Quick Restart — same map &amp; mode">
        ${icon('<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.25"/>')}
      </button>
      <button class="btn btn-neutral flex-1" id="status-stop-btn">
        ${icon('<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>')} Stop
      </button>
    </div>
    <div class="live-map-info" id="live-map-info">
      <span class="live-map-name" id="live-map-name"></span>
      <span class="live-player-count" id="live-player-count"></span>
    </div>`;
  hero.appendChild(indCard);

  // Right: map + mode selectors
  const mapCard = h('div', 'map-mode-card');
  const modeOpts = state.modes.map(m =>
    `<option value="${m}" ${m === s.mode ? 'selected' : ''}>${m}</option>`
  ).join('');

  mapCard.innerHTML = `
    <div class="card-title">Map & Mode</div>
    <div class="field">
      <label>Game Mode</label>
      <select class="select" id="mode-select">${modeOpts}</select>
    </div>
    <div class="field">
      <label>Official Map</label>
      <select class="select" id="map-select"></select>
    </div>
    <div class="field">
      <label>Workshop Map</label>
      <select class="select" id="ws-map-select"></select>
    </div>
    <div style="display:flex;gap:8px;margin-top:4px">
      <button class="btn btn-accent flex-1" id="map-change-btn">
        ${icon('<shuffle/><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/>')}
        Change Map
      </button>
    </div>`;
  hero.appendChild(mapCard);
  root.appendChild(hero);

  // ── Quick actions ──────────────────────────────────────────────────────
  const actGrid = h('div', 'actions-grid');
  const actions = [
    { id: 'act-broadcast', cls: 'accent', svg: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.63 3.42 2 2 0 0 1 3.6 1h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>', label: 'Broadcast' },
    { id: 'ff-tile',       cls: '',       svg: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>', label: 'Friendly Fire' },
    { id: 'act-restart',   cls: 'blue',   svg: '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.25"/>', label: 'Restart Round' },
    { id: 'act-warmup',    cls: 'green',  svg: '<polygon points="5 3 19 12 5 21 5 3"/>', label: 'End Warmup' },
    { id: 'act-pause',     cls: 'yellow', svg: '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>', label: 'Pause' },
    { id: 'act-unpause',   cls: '',       svg: '<polygon points="5 3 19 12 5 21 5 3"/>', label: 'Unpause' },
  ];
  actions.forEach(a => {
    const tile = h('div', `action-tile ${a.cls}`, `${icon(a.svg)}<span>${a.label}</span>`);
    tile.id = a.id;
    actGrid.appendChild(tile);
  });
  root.appendChild(actGrid);

  // ── Bot row ────────────────────────────────────────────────────────────
  const botRow = h('div', 'bots-row');
  botRow.innerHTML = `
    <span class="bots-label">Bots</span>
    <button class="btn btn-ghost btn-sm" id="bot-add1">+1 Bot</button>
    <button class="btn btn-ghost btn-sm" id="bot-add5">+5 Bots</button>
    <button class="btn btn-red btn-sm"   id="bot-kick">Kick All</button>
    <div style="margin-left:auto">
      <select class="select" id="bot-diff" style="font-size:.78rem;padding:5px 28px 5px 8px">
        ${['Easy','Normal','Hard','Expert'].map(d =>
          `<option ${d === (s.bot_difficulty||'Normal') ? 'selected' : ''}>${d}</option>`
        ).join('')}
      </select>
    </div>`;
  root.appendChild(botRow);

  // ── Log panel ──────────────────────────────────────────────────────────
  const logPanel = h('div', 'log-panel');
  logPanel.innerHTML = `
    <div class="log-header">
      <span class="log-title">Live Log</span>
      <button class="btn btn-ghost btn-sm" id="log-copy-btn">Copy Log</button>
    </div>
    <div class="log-body" id="log-body"></div>`;
  root.appendChild(logPanel);

  // Populate log
  const lb = el('log-body');
  logLines.forEach(line => {
    const d = document.createElement('div');
    d.className = 'log-line'; d.textContent = line;
    lb.appendChild(d);
  });
  lb.scrollTop = lb.scrollHeight;

  // Copy log to clipboard
  el('log-copy-btn').addEventListener('click', () => {
    navigator.clipboard.writeText(logLines.join('\n')).then(() => {
      const btn = el('log-copy-btn');
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = 'Copy Log'; }, 2000);
    });
  });

  // Populate map selectors
  populateModeOfficialMaps(el('mode-select').value);
  populateModeWorkshopMaps(el('mode-select').value);

  // Apply current state
  renderStatusState();

  // ── Wire up events ─────────────────────────────────────────────────────
  el('mode-select').addEventListener('change', e => {
    populateModeOfficialMaps(e.target.value);
    populateModeWorkshopMaps(e.target.value);
  });

  el('status-start-btn').addEventListener('click', async () => {
    const wsId   = el('ws-map-select').value.trim();
    const mapSel = el('map-select').value;
    const mode   = el('mode-select').value;
    const map    = wsId || mapSel;
    const ws     = !!wsId;
    try {
      await api.start(map, mode, ws);
      toast('Server starting…', 'var(--green)');
    } catch (e) { toast(e.message, 'var(--red)'); }
  });

  el('status-restart-btn').addEventListener('click', doQuickRestart);

  el('status-stop-btn').addEventListener('click', async () => {
    const doStop = async () => {
      try { await api.stop(); toast('Server stopping…'); }
      catch (e) { toast(e.message, 'var(--red)'); }
    };
    if (appSettings.confirmStop) {
      modal('Stop Server', '<p style="color:var(--sub);font-size:.9rem">Are you sure you want to stop the server?</p>',
        doStop, 'Stop');
    } else {
      doStop();
    }
  });

  el('map-change-btn').addEventListener('click', async () => {
    const wsId = el('ws-map-select').value.trim();
    const map  = wsId || el('map-select').value;
    const mode = el('mode-select').value;
    const ws   = !!wsId;
    try {
      await api.map(map, mode, ws);
      toast(`Changing to ${map}…`);
    } catch (e) { toast(e.message, 'var(--red)'); }
  });

  el('act-broadcast').addEventListener('click', () => {
    const ov = modal(
      'Broadcast Message',
      '<div class="field"><label>Message to all players</label>'
      + '<input class="input" id="broadcast-msg" placeholder="Server message…"></div>',
      async () => {
        const msg = document.getElementById('broadcast-msg').value.trim();
        if (!msg) return;
        try { await api.broadcast(msg); toast('Message sent'); }
        catch (e) { toast(e.message, 'var(--red)'); }
      },
      'Send'
    );
    setTimeout(() => document.getElementById('broadcast-msg')?.focus(), 50);
  });

  el('ff-tile').addEventListener('click', async () => {
    const now = state.server.ff_enabled;
    try {
      await api.ff(!now);
      toast(`Friendly Fire ${!now ? 'enabled' : 'disabled'}`);
    } catch (e) { toast(e.message, 'var(--red)'); }
  });

  el('act-restart').addEventListener('click', async () => {
    try { await api.restartRound(); toast('Round restarted'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });
  el('act-warmup').addEventListener('click', async () => {
    try { await api.endWarmup(); toast('Warmup ended'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });
  el('act-pause').addEventListener('click', async () => {
    try { await api.pause(); toast('Match paused'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });
  el('act-unpause').addEventListener('click', async () => {
    try { await api.unpause(); toast('Match unpaused'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });

  el('bot-diff').addEventListener('change', async e => {
    try { await api.setConfig({ bot_difficulty: e.target.value }); }
    catch (_) {}
  });
  el('bot-add1').addEventListener('click', async () => {
    try { await api.addBots(1); toast('+1 bot added'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });
  el('bot-add5').addEventListener('click', async () => {
    try { await api.addBots(5); toast('+5 bots added'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });
  el('bot-kick').addEventListener('click', async () => {
    try { await api.kickBots(); toast('All bots kicked'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });
}

function populateModeOfficialMaps(mode) {
  const sel = el('map-select');
  if (!sel) return;
  const valid = (state.modeMaps[mode] || state.maps).slice().sort();
  sel.innerHTML = valid.map(m => `<option value="${m}">${m}</option>`).join('');
}

function populateModeWorkshopMaps(mode) {
  const sel = el('ws-map-select');
  if (!sel) return;

  const maps     = state.workshopMaps;
  const modeTags = (state.modeWorkshopTags[mode] || []).map(t => t.toLowerCase());
  const sort     = arr => arr.slice().sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));

  const matched = sort(maps.filter(m =>
    !m.tags || m.tags.length === 0 || m.tags.some(t => modeTags.includes(t.toLowerCase()))
  ));
  const others = sort(maps.filter(m =>
    m.tags && m.tags.length > 0 && !m.tags.some(t => modeTags.includes(t.toLowerCase()))
  ));

  sel.innerHTML = '<option value="">— None (use official map) —</option>';

  if (!maps.length) {
    const opt = document.createElement('option');
    opt.disabled = true;
    opt.textContent = 'No workshop maps downloaded yet';
    sel.appendChild(opt);
    return;
  }

  const addGroup = (list, label) => {
    if (!list.length) return;
    const grp = document.createElement('optgroup');
    grp.label = label;
    list.forEach(m => {
      const opt = document.createElement('option');
      opt.value       = m.id;
      opt.textContent = m.name || m.id;
      grp.appendChild(opt);
    });
    sel.appendChild(grp);
  };

  addGroup(matched, mode + ' Maps');
  addGroup(others,  'Other Maps');
}

pages['status'] = buildStatusPage;

/* ══════════════════════════════════════════════════════════════ PLAYERS PAGE */

pages['players'] = function() {
  const root = el('content');
  root.innerHTML = `
    <div class="section-hdr mb-16">
      <span class="section-title">Players</span>
      <div style="display:flex;gap:8px">
        <button class="btn btn-ghost btn-sm" id="refresh-btn">↺ Refresh</button>
        <span class="player-count" id="player-count"></span>
      </div>
    </div>
    <div class="players-grid" id="players-grid">
      <div class="empty-state">Loading players…</div>
    </div>

    <div class="card mt-16">
      <div class="card-title">Ban Player by SteamID</div>
      <div class="ban-input-row">
        <div class="field">
          <label>SteamID / Steam64</label>
          <input class="input" id="ban-steamid" placeholder="STEAM_0:0:… or 7656119…">
        </div>
        <div class="field" style="width:120px">
          <label>Duration (min, 0=perm)</label>
          <input class="input" id="ban-duration" type="number" min="0" value="0">
        </div>
        <button class="btn btn-red" id="ban-submit">Ban</button>
      </div>
    </div>

    <div class="card mt-16">
      <div class="card-title">Ban List</div>
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <button class="btn btn-ghost btn-sm" id="bans-refresh">↺ Refresh Bans</button>
      </div>
      <div class="ban-list" id="ban-list">
        <div class="empty-state text-sm">Loading…</div>
      </div>
    </div>`;

  loadPlayers();
  loadBans();

  el('refresh-btn').addEventListener('click', loadPlayers);
  el('bans-refresh').addEventListener('click', loadBans);

  el('ban-submit').addEventListener('click', async () => {
    const sid = el('ban-steamid').value.trim();
    const dur = parseInt(el('ban-duration').value) || 0;
    if (!sid) { toast('Enter a SteamID', 'var(--red)'); return; }
    try {
      await api.ban(sid, '', dur);
      toast('Player banned');
      el('ban-steamid').value = '';
      loadBans();
    } catch (e) { toast(e.message, 'var(--red)'); }
  });
};

async function loadPlayers() {
  const grid = el('players-grid');
  if (!grid) return;
  try {
    const pl = await api.players();
    el('player-count').textContent = `${pl.length} player${pl.length !== 1 ? 's' : ''}`;
    if (!pl.length) {
      grid.innerHTML = '<div class="empty-state">No players online</div>';
      return;
    }
    grid.innerHTML = '';
    pl.forEach(p => {
      const row = h('div', 'player-row');
      row.innerHTML = `
        <span class="player-name">${p.name || 'Unknown'}</span>
        <span class="player-ping">${p.ping != null ? p.ping + ' ms' : '—'}</span>
        <div class="player-actions">
          <button class="btn btn-ghost btn-sm kick-btn" data-uid="${p.userid}" data-name="${p.name}">Kick</button>
          <button class="btn btn-red btn-sm ban-btn"  data-sid="${p.steamid}" data-name="${p.name}">Ban</button>
        </div>`;
      grid.appendChild(row);
    });
    grid.querySelectorAll('.kick-btn').forEach(b => {
      b.addEventListener('click', async () => {
        try { await api.kick(b.dataset.uid, b.dataset.name); toast('Player kicked'); loadPlayers(); }
        catch (e) { toast(e.message, 'var(--red)'); }
      });
    });
    grid.querySelectorAll('.ban-btn').forEach(b => {
      b.addEventListener('click', async () => {
        try { await api.ban(b.dataset.sid, b.dataset.name, 0); toast('Player banned'); loadPlayers(); loadBans(); }
        catch (e) { toast(e.message, 'var(--red)'); }
      });
    });
  } catch (e) {
    grid.innerHTML = `<div class="empty-state text-red">${e.message}</div>`;
  }
}

async function loadBans() {
  const list = el('ban-list');
  if (!list) return;
  try {
    const bans = await api.bans();
    if (!bans.length) {
      list.innerHTML = '<div class="empty-state text-sm">No bans</div>';
      return;
    }
    list.innerHTML = '';
    bans.forEach(line => {
      const sidM = line.match(/(STEAM_\S+|\[U:[^\]]+\]|765\d{14,})/i);
      const sid  = sidM ? sidM[1] : line;
      const row  = h('div', 'ban-row');
      row.innerHTML = `
        <span class="ban-steamid">${line}</span>
        <button class="btn btn-ghost btn-sm unban-btn" data-sid="${sid}">Unban</button>`;
      list.appendChild(row);
    });
    list.querySelectorAll('.unban-btn').forEach(b => {
      b.addEventListener('click', async () => {
        try { await api.unban(b.dataset.sid); toast('Player unbanned'); loadBans(); }
        catch (e) { toast(e.message, 'var(--red)'); }
      });
    });
  } catch (e) {
    list.innerHTML = `<div class="empty-state text-sm text-red">${e.message}</div>`;
  }
}

/* ══════════════════════════════════════════════════════════════ MAPS PAGE */

pages['maps'] = function() {
  const root = el('content');
  root.innerHTML = `
    <div class="maps-mode-bar mb-16">
      <span class="section-title">Maps</span>
      <div class="maps-search-wrap">
        <svg class="maps-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input class="input maps-search-input" id="maps-search"
               placeholder="Search maps…" autocomplete="off" spellcheck="false">
      </div>
      <select class="select" id="maps-mode-filter" style="width:160px">
        ${state.modes.map(m => `<option value="${m}" ${m===state.server.mode?'selected':''}>${m}</option>`).join('')}
      </select>
    </div>
    <div class="section-title mb-16" id="official-section-title">Official Maps</div>
    <div class="maps-grid" id="official-maps-grid"></div>
    <div class="maps-section-sep" id="workshop-section-sep"><span>Workshop Maps</span></div>
    <div class="maps-grid" id="workshop-maps-grid">
      <div class="empty-state text-sm">Loading…</div>
    </div>`;

  // ── Filter: show/hide cards that match the query ─────────────────────────
  const applyFilter = (query) => {
    const q = query.toLowerCase().trim();
    let nOff = 0, nWs = 0;

    el('official-maps-grid').querySelectorAll('.map-card').forEach(c => {
      const show = !q || (c.dataset.name || '').includes(q);
      c.style.display = show ? '' : 'none';
      if (show) nOff++;
    });
    const wsCards = el('workshop-maps-grid').querySelectorAll('.map-card');
    wsCards.forEach(c => {
      const show = !q || (c.dataset.name || '').includes(q);
      c.style.display = show ? '' : 'none';
      if (show) nWs++;
    });

    // Show/hide section headings based on results
    // Don't hide the workshop separator while the grid is still loading (no cards yet)
    const offTitle = el('official-section-title');
    const wsSep    = el('workshop-section-sep');
    if (offTitle) offTitle.style.display = (nOff || !q) ? '' : 'none';
    if (wsSep)    wsSep.style.display    = (nWs || !q || wsCards.length === 0) ? '' : 'none';

    // Empty-state messages within each grid
    const offGrid = el('official-maps-grid');
    if (offGrid) {
      let emp = offGrid.querySelector('.search-empty');
      if (!nOff && q) {
        if (!emp) { emp = h('div', 'empty-state text-sm search-empty', 'No maps match'); offGrid.appendChild(emp); }
      } else { emp?.remove(); }
    }
    const wsGrid = el('workshop-maps-grid');
    if (wsGrid) {
      let emp = wsGrid.querySelector('.search-empty');
      if (!nWs && q && wsGrid.querySelector('.map-card')) {
        if (!emp) { emp = h('div', 'empty-state text-sm search-empty', 'No maps match'); wsGrid.appendChild(emp); }
      } else { emp?.remove(); }
    }
  };

  // ── Official maps render ─────────────────────────────────────────────────
  const renderOfficial = (mode) => {
    const grid  = el('official-maps-grid');
    const valid = state.modeMaps[mode] || state.maps;
    grid.innerHTML = '';
    valid.forEach(mapId => {
      const card = h('div', 'map-card' + (mapId === state.server.map ? ' active' : ''));
      card.dataset.name = mapId.toLowerCase();
      const thumbHtml = `<img class="map-thumb" src="/api/maps/thumb/${mapId}" loading="lazy"
               onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
             ><div class="map-thumb-placeholder" style="display:none">
               ${icon('<polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"/><line x1="9" y1="3" x2="9" y2="18"/><line x1="15" y1="6" x2="15" y2="21"/>')}
             </div>`;
      card.innerHTML = `${thumbHtml}<div class="map-info"><div class="map-name">${mapId}</div></div>`;
      card.addEventListener('click', async () => {
        try { await api.map(mapId, mode, false); toast(`Changing to ${mapId}…`); }
        catch (e) { toast(e.message, 'var(--red)'); }
      });
      grid.appendChild(card);
    });
  };

  renderOfficial(el('maps-mode-filter').value);

  el('maps-mode-filter').addEventListener('change', e => {
    renderOfficial(e.target.value);
    applyFilter(el('maps-search')?.value || '');   // filter official cards immediately
    loadWorkshopMapsGrid(el('workshop-maps-grid'), e.target.value)
      .then(() => applyFilter(el('maps-search')?.value || ''));
  });

  el('maps-search').addEventListener('input', e => applyFilter(e.target.value));

  loadWorkshopMapsGrid(el('workshop-maps-grid'), el('maps-mode-filter').value)
    .then(() => applyFilter(el('maps-search')?.value || ''));
};

async function loadWorkshopMapsGrid(grid, mode) {
  try {
    const maps = await api.workshopMaps();
    state.workshopMaps = maps;
    if (!maps.length) {
      grid.innerHTML = '<div class="empty-state text-sm">No workshop maps downloaded yet</div>';
      return;
    }
    grid.innerHTML = '';
    maps.forEach(m => {
      const card = h('div', 'map-card' + (m.id === state.server.map ? ' active' : ''));
      card.dataset.name = `${(m.name || '')} ${m.id}`.toLowerCase();
      const thumbHtml = m.preview_url
        ? `<img class="map-thumb" src="${m.preview_url}" loading="lazy" onerror="this.style.display='none'">`
        : `<div class="map-thumb-placeholder">${icon('<polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"/>')}</div>`;
      card.innerHTML = `
        ${thumbHtml}
        <div class="map-info">
          <div class="map-name">${m.name || m.id}</div>
          <div class="map-id">${m.id}</div>
        </div>`;
      card.addEventListener('click', async () => {
        try {
          await api.map(m.id, mode, true);
          toast(`Loading workshop map…`);
        } catch (e) { toast(e.message, 'var(--red)'); }
      });
      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div class="empty-state text-sm text-red">${e.message}</div>`;
  }
}

/* ══════════════════════════════════════════════════════════════ WORKSHOP PAGE */

pages['workshop'] = function() {
  const root = el('content');
  const isLocal = state.server.is_local;

  root.innerHTML = `
    <div class="section-title mb-16">Workshop Maps</div>

    ${isLocal ? `
    <div class="card workshop-dl-card mb-16">
      <div class="card-title">Download Map</div>
      <div class="workshop-dl-row">
        <div class="field">
          <label>Steam Workshop Map ID</label>
          <div class="input-paste-wrap">
            <input class="input" id="ws-id-input" placeholder="e.g. 3070720081"
                   oninput="this.value=this.value.replace(/\\D/g,'')">
            <button class="input-paste-btn" id="ws-paste-btn" title="Paste from clipboard">
              <svg viewBox="0 0 24 24" width="14" height="14"><rect x="9" y="2" width="6" height="4" rx="1"/><path d="M9 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2h-2"/></svg>
            </button>
          </div>
        </div>
        <button class="btn btn-accent" id="ws-dl-btn">Download</button>
        <button class="btn btn-red hidden" id="ws-cancel-btn">Cancel</button>
      </div>
      <div class="workshop-progress" id="ws-progress">
        <div class="ws-progress-status">
          <span class="ws-progress-dot"></span>
          <span class="ws-progress-text" id="ws-status-text">Downloading…</span>
        </div>
        <div class="workshop-progress-track">
          <div class="workshop-progress-bar"></div>
        </div>
      </div>
    </div>
    <div class="flex gap-8 mb-16">
      <button class="btn btn-ghost btn-sm" id="ws-update-btn">↺ Update All Maps</button>
    </div>
    ` : `
    <div class="card mb-16">
      <div class="card-title">Request Workshop Download</div>
      <div class="workshop-dl-row">
        <div class="field">
          <label>Steam Workshop Map ID</label>
          <div class="input-paste-wrap">
            <input class="input" id="ws-id-input" placeholder="e.g. 3070720081"
                   oninput="this.value=this.value.replace(/\\D/g,'')">
            <button class="input-paste-btn" id="ws-paste-btn" title="Paste from clipboard">
              <svg viewBox="0 0 24 24" width="14" height="14"><rect x="9" y="2" width="6" height="4" rx="1"/><path d="M9 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2h-2"/></svg>
            </button>
          </div>
        </div>
        <button class="btn btn-accent" id="ws-req-btn">Request Download</button>
      </div>
      <div class="text-sub text-sm mt-8">
        The server operator will receive an approval request on the desktop app.
      </div>
    </div>
    `}

    <div class="section-title mb-16">Downloaded Maps</div>
    <div class="workshop-maps-grid" id="ws-maps-grid">
      <div class="empty-state text-sm">Loading…</div>
    </div>`;

  // Paste button — shared between local and remote layouts
  el('ws-paste-btn')?.addEventListener('click', async () => {
    try {
      const text = await navigator.clipboard.readText();
      const digits = text.replace(/\D/g, '');
      if (digits) { el('ws-id-input').value = digits; el('ws-id-input').focus(); }
      else toast('Nothing numeric on the clipboard', 'var(--orange)');
    } catch { toast('Clipboard access denied', 'var(--red)'); }
  });

  if (isLocal) {
    const dlBtn     = el('ws-dl-btn');
    const cancelBtn = el('ws-cancel-btn');
    const progress  = el('ws-progress');

    // Restore live state on page open
    if (state.server.dl_active || _dlStatus.active) {
      dlBtn.classList.add('hidden');
      cancelBtn.classList.remove('hidden');
      progress.classList.add('active');
      if (_dlStatus.text) el('ws-status-text').textContent = _dlStatus.text;
    } else if (_dlStatus.text) {
      // show last completed/failed message briefly
      progress.classList.add('done');
      el('ws-status-text').textContent = _dlStatus.text;
    }

    dlBtn.addEventListener('click', async () => {
      const id = el('ws-id-input').value.trim();
      if (!id) { toast('Enter a Workshop Map ID', 'var(--red)'); return; }
      try {
        await api.workshopDownload(id);
        _dlStatus = { active: true, text: 'Starting download…' };
        el('ws-status-text').textContent = _dlStatus.text;
        dlBtn.classList.add('hidden');
        cancelBtn.classList.remove('hidden');
        progress.classList.remove('done');
        progress.classList.add('active');
        toast('Download started…');
      } catch (e) {
        // If Steam credentials are missing, surface a clear action prompt
        if (e.needs_steam) {
          toast('Steam credentials required — go to Config → Steam Account', 'var(--orange)');
          setTimeout(() => navigate('config'), 1200);
        } else {
          toast(e.message, 'var(--red)');
        }
      }
    });

    cancelBtn.addEventListener('click', async () => {
      try {
        await api.workshopCancel();
        _dlStatus = { active: false, text: '' };
        dlBtn.classList.remove('hidden');
        cancelBtn.classList.add('hidden');
        progress.classList.remove('active');
        progress.classList.remove('done');
        toast('Download cancelled');
      } catch (e) { toast(e.message, 'var(--red)'); }
    });

    el('ws-update-btn').addEventListener('click', async () => {
      try { await api.workshopUpdate(); toast('Checking for map updates…'); }
      catch (e) { toast(e.message, 'var(--red)'); }
    });
  } else {
    el('ws-req-btn')?.addEventListener('click', async () => {
      const id = el('ws-id-input').value.trim();
      if (!id) { toast('Enter a Workshop Map ID', 'var(--red)'); return; }
      try {
        await api.requestWorkshop(id);
        toast('Download request sent — awaiting approval');
      } catch (e) { toast(e.message, 'var(--red)'); }
    });
  }

  loadWorkshopMapsGrid(el('ws-maps-grid'), state.server.mode);
};

/* ══════════════════════════════════════════════════════════════ CONFIG PAGE */

pages['config'] = async function() {
  const root = el('content');
  root.innerHTML = '<div class="empty-state">Loading config…</div>';

  let cfg;
  try { cfg = await api.config(); }
  catch (e) { root.innerHTML = `<div class="empty-state text-red">${e.message}</div>`; return; }

  const isLocal = cfg.is_local;

  root.innerHTML = `
    <div class="grid-2">

      <!-- Left column -->
      <div>
        <div class="config-label">Server Settings</div>
        <div class="card mb-16">
          <div class="flex-col gap-8">
            <div class="field"><label>Hostname</label>
              <input class="input" id="cfg-hostname" value="${cfg.hostname || ''}"></div>
            <div class="field"><label>Server Password (sv_password)</label>
              <input class="input" id="cfg-svpassword" type="password"
                     value="${cfg.sv_password || ''}"></div>
            ${isLocal ? `
            <div class="field">
              <label>Game Server Login Token <span class="field-label-note">(GSLT)</span></label>
              <input class="input" id="cfg-gslt" placeholder="Leave blank for LAN / private use"
                     value="${cfg.gslt_token || ''}">
              <div class="field-hint">Makes your server visible in the public browser.
                Get one free at <strong>steamcommunity.com/dev/managegameservers</strong>
                (App&nbsp;ID&nbsp;730). Left blank during setup? Paste it here.</div>
            </div>
            <div class="field"><label>Max Players Override (blank = mode default)</label>
              <input class="input" id="cfg-maxplayers" placeholder="e.g. 16"
                     value="${cfg.max_players_override || ''}"></div>
            ` : ''}
            <div class="toggle-row">
              <div class="toggle-info">
                <strong>Tickrate 128</strong>
                <small>-tickrate 128 on server launch</small>
              </div>
              <label class="toggle">
                <input type="checkbox" id="cfg-tickrate" ${cfg.tickrate_128?'checked':''}>
                <span class="toggle-track"></span><span class="toggle-thumb"></span>
              </label>
            </div>
            <div class="toggle-row">
              <div class="toggle-info">
                <strong>Auto-start on launch</strong>
                <small>Start server when tool opens</small>
              </div>
              <label class="toggle">
                <input type="checkbox" id="cfg-autostart" ${cfg.auto_start?'checked':''}>
                <span class="toggle-track"></span><span class="toggle-thumb"></span>
              </label>
            </div>
            <div class="toggle-row">
              <div class="toggle-info">
                <strong>Auto-restart on crash</strong>
                <small>Up to 3 consecutive restarts</small>
              </div>
              <label class="toggle">
                <input type="checkbox" id="cfg-autorestart" ${cfg.auto_restart_on_crash?'checked':''}>
                <span class="toggle-track"></span><span class="toggle-thumb"></span>
              </label>
            </div>
          </div>
          <button class="btn btn-accent btn-full mt-16" id="cfg-server-save">Save Server Settings</button>
        </div>

        <div class="config-label">Security</div>
        <div class="card mb-16">
          <div class="flex-col gap-8">
            <div class="field"><label>Admin PIN (4+ digits, web panel login)</label>
              <input class="input" id="cfg-pin" type="password"
                     value="${cfg.admin_pin || ''}" maxlength="8"></div>
            ${isLocal ? `
            <div class="field"><label>RCON Password (auto-generated, change if needed)</label>
              <input class="input" id="cfg-rcon-pw" type="password"
                     value="${cfg.rcon_password || ''}"></div>
            ` : ''}
          </div>
          <button class="btn btn-accent btn-full mt-16" id="cfg-security-save">Save Security Settings</button>
        </div>

        <div class="config-label">Bot Difficulty</div>
        <div class="card mb-16">
          <div class="field">
            <label>Default bot difficulty</label>
            <select class="select" id="cfg-bot-diff">
              ${['Easy','Normal','Hard','Expert'].map(d =>
                `<option ${d===cfg.bot_difficulty?'selected':''}>${d}</option>`).join('')}
            </select>
          </div>
          <button class="btn btn-accent btn-full mt-16" id="cfg-bot-save">Save</button>
        </div>
      </div>

      <!-- Right column -->
      <div>
        <div class="config-label">Presets</div>
        <div class="card mb-16">
          <div class="presets-row">
            <div class="field flex-1">
              <label>Preset name</label>
              <input class="input" id="preset-name" placeholder="e.g. 5v5 Mirage">
            </div>
            <button class="btn btn-accent" id="preset-save-btn">Save Current</button>
          </div>
          <div class="preset-list" id="preset-list">Loading…</div>
        </div>

        ${isLocal ? `
        <div class="config-label">Steam Account</div>
        <div class="card mb-16">
          <div class="flex-col gap-8">
            <div class="field"><label>Username</label>
              <input class="input" id="cfg-steam-user" value="${cfg.steam_username||''}"></div>
            <div class="field"><label>Password</label>
              <input class="input" id="cfg-steam-pw" type="password" placeholder="Stored securely"></div>
            <div class="text-sub text-sm">
              Use a dedicated Steam account, not your personal one.
              ${cfg.steam_session_active ? '<span class="text-green">✓ Active session</span>' : ''}
            </div>
          </div>
          <div class="flex gap-8 mt-16">
            <button class="btn btn-ghost flex-1" id="cfg-steam-save">Save Credentials</button>
            <button class="btn btn-accent flex-1" id="cfg-steam-login">Login (Interactive)</button>
          </div>
        </div>

        <div class="config-label">Server Installation</div>
        <div class="card mb-16">
          <div class="field mb-16">
            <label>Server Directory</label>
            <div class="flex gap-8">
              <input class="input flex-1" id="cfg-server-dir" value="${cfg.server_dir||''}">
              <button class="btn btn-ghost btn-sm" id="cfg-browse-btn">Browse…</button>
            </div>
          </div>
          <div class="flex gap-8">
            <button class="btn btn-ghost flex-1" id="cfg-dir-save">Set Directory</button>
            <button class="btn btn-accent flex-1" id="cfg-install-btn">Install / Reinstall</button>
          </div>
        </div>
        ` : ''}

        <div class="config-label">RCON Console</div>
        <div class="card">
          <div class="rcon-output" id="rcon-output">Ready. Type a command below.</div>
          <div class="rcon-row">
            <input class="input" id="rcon-cmd" placeholder="e.g. status" spellcheck="false">
            <button class="btn btn-accent" id="rcon-send">Send</button>
          </div>
        </div>
      </div>

    </div>`;

  // Load presets
  loadPresets();

  // Wire up saves
  el('cfg-server-save').addEventListener('click', async () => {
    const data = {
      hostname:              el('cfg-hostname').value,
      sv_password:           el('cfg-svpassword').value,
      tickrate_128:          el('cfg-tickrate').checked,
      auto_start:            el('cfg-autostart').checked,
      auto_restart_on_crash: el('cfg-autorestart').checked,
    };
    if (isLocal) {
      data.gslt_token        = el('cfg-gslt').value;
      data.max_players_override = el('cfg-maxplayers').value;
    }
    try { await api.setConfig(data); toast('Server settings saved'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });

  el('cfg-security-save').addEventListener('click', async () => {
    const data = { admin_pin: el('cfg-pin').value };
    if (isLocal) data.rcon_password = el('cfg-rcon-pw').value;
    try { await api.setConfig(data); toast('Security settings saved'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });

  el('cfg-bot-save').addEventListener('click', async () => {
    try {
      await api.setConfig({ bot_difficulty: el('cfg-bot-diff').value });
      toast('Bot difficulty saved');
    } catch (e) { toast(e.message, 'var(--red)'); }
  });

  el('preset-save-btn').addEventListener('click', async () => {
    const name = el('preset-name').value.trim();
    if (!name) { toast('Enter a preset name', 'var(--red)'); return; }
    try { await api.savePreset(name); toast(`Preset "${name}" saved`); loadPresets(); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });

  if (isLocal) {
    el('cfg-steam-save').addEventListener('click', async () => {
      const data = {
        steam_username: el('cfg-steam-user').value,
        steam_password: el('cfg-steam-pw').value,
      };
      try { await api.setConfig(data); toast('Steam credentials saved'); }
      catch (e) { toast(e.message, 'var(--red)'); }
    });

    el('cfg-steam-login').addEventListener('click', async () => {
      try { await api.steamLogin(); toast('Steam login started — check the log'); }
      catch (e) { toast(e.message, 'var(--red)'); }
    });

    el('cfg-browse-btn').addEventListener('click', async () => {
      try {
        const r = await api.pickDirectory();
        if (r.path) el('cfg-server-dir').value = r.path;
      } catch (e) { toast(e.message, 'var(--red)'); }
    });

    el('cfg-dir-save').addEventListener('click', async () => {
      const dir = el('cfg-server-dir').value.trim();
      if (!dir) { toast('Enter a directory path', 'var(--red)'); return; }
      try { await api.setConfig({ server_dir: dir }); toast('Server directory updated'); }
      catch (e) { toast(e.message, 'var(--red)'); }
    });

    el('cfg-install-btn').addEventListener('click', () => {
      modal(
        'Install / Reinstall CS2 Server',
        '<p style="color:var(--sub);font-size:.86rem">This will download and install the CS2 dedicated server (~15 GB) via steamcmd. The process will be shown in the live log. Existing files will be updated.</p>',
        async () => {
          try { await api.install(); toast('Installation started — check the log'); }
          catch (e) { toast(e.message, 'var(--red)'); }
        },
        'Install'
      );
    });
  }

  // RCON console
  const rconSend = async () => {
    const cmd = el('rcon-cmd').value.trim();
    if (!cmd) return;
    const out = el('rcon-output');
    out.textContent += `\n> ${cmd}\n`;
    try {
      const r = await api.rcon(cmd);
      out.textContent += r.response || '(no output)';
    } catch (e) {
      out.textContent += `Error: ${e.message}`;
    }
    out.textContent += '\n';
    out.scrollTop = out.scrollHeight;
    el('rcon-cmd').value = '';
  };
  el('rcon-send').addEventListener('click', rconSend);
  el('rcon-cmd').addEventListener('keydown', e => { if (e.key === 'Enter') rconSend(); });
};

async function loadPresets() {
  const list = el('preset-list');
  if (!list) return;
  try {
    const names = await api.presets();
    if (!names.length) { list.textContent = 'No presets saved yet'; return; }
    list.innerHTML = '';
    names.forEach(name => {
      const chip = h('div', 'preset-chip');
      chip.innerHTML = `<span>${name}</span><span class="preset-chip-del" title="Delete">×</span>`;
      chip.querySelector('span').addEventListener('click', async () => {
        try {
          const p = await api.loadPreset(name);
          toast(`Preset "${name}" loaded — use Change Map to apply`);
          // update selectors if on status page
          const modeS = el('mode-select');
          const mapS  = el('map-select');
          if (modeS) { modeS.value = p.mode; populateModeOfficialMaps(p.mode); populateModeWorkshopMaps(p.mode); }
          if (mapS)  mapS.value = p.map;
        } catch (e) { toast(e.message, 'var(--red)'); }
      });
      chip.querySelector('.preset-chip-del').addEventListener('click', async e => {
        e.stopPropagation();
        try { await api.deletePreset(name); toast(`Preset "${name}" deleted`); loadPresets(); }
        catch (err) { toast(err.message, 'var(--red)'); }
      });
      list.appendChild(chip);
    });
  } catch (e) {
    list.textContent = 'Could not load presets';
  }
}

/* ══════════════════════════════════════════════════════════════ SETUP WIZARD */

/**
 * Multi-step first-run setup wizard.
 * Shown to local sessions only when server_dir is unset or PIN is still default.
 * Steps: 1 = Server Directory, 2 = Security, 3 = Done.
 */
function showSetupWizard(status) {
  // Dim the shell — wizard sits on top of everything
  const overlay = h('div', 'setup-overlay');
  document.body.appendChild(overlay);

  let step   = 1;
  const data = { server_dir: '', admin_pin: '', confirm_pin: '', gslt_token: '' };

  function render() {
    overlay.innerHTML = `
      <div class="setup-card">
        <div class="setup-brand">OBLIVION</div>
        <div class="setup-sub">SERVER TOOL — FIRST-TIME SETUP</div>

        <div class="setup-steps">
          ${[1,2,3].map(n => `
            <div class="setup-step ${n < step ? 'done' : n === step ? 'active' : ''}">
              <div class="setup-step-dot">${n < step ? '✓' : n}</div>
              <div class="setup-step-label">${['Directory','Security','Ready'][n-1]}</div>
            </div>
            ${n < 3 ? '<div class="setup-step-line"></div>' : ''}
          `).join('')}
        </div>

        <div class="setup-body" id="setup-body"></div>
        <div class="setup-footer" id="setup-footer"></div>
      </div>`;

    const body   = el('setup-body');
    const footer = el('setup-footer');

    if (step === 1) renderStep1(body, footer);
    else if (step === 2) renderStep2(body, footer);
    else renderStep3(body, footer);
  }

  /* ── Step 1: Server Directory ──────────────────────────────────────── */
  function renderStep1(body, footer) {
    body.innerHTML = `
      <div class="setup-section-title">Where should CS2 be installed?</div>
      <p class="setup-hint">Choose an empty folder (or an existing install). steamcmd will download
         ~15 GB here. The folder must be on a drive with enough space.</p>
      <div class="field mt-16">
        <label>Server Directory</label>
        <div class="flex gap-8">
          <input class="input flex-1" id="sw-dir" placeholder="e.g. C:\\CS2Server"
                 value="${data.server_dir}">
          <button class="btn btn-ghost btn-sm" id="sw-browse">Browse…</button>
        </div>
      </div>
      <div class="setup-err hidden" id="sw-err"></div>`;

    el('sw-browse').addEventListener('click', async () => {
      try {
        const r = await api.pickDirectory();
        if (r.path) { el('sw-dir').value = r.path; data.server_dir = r.path; }
      } catch (e) { /* browse cancelled */ }
    });

    footer.innerHTML = `
      <div></div>
      <button class="btn btn-accent" id="sw-next1">Next →</button>`;

    el('sw-next1').addEventListener('click', () => {
      const val = el('sw-dir').value.trim();
      if (!val) { showErr('sw-err', 'Please choose a directory'); return; }
      data.server_dir = val;
      step = 2; render();
    });
  }

  /* ── Step 2: Security ──────────────────────────────────────────────── */
  function renderStep2(body, footer) {
    const pinWarn = status.pin_is_default
      ? '<div class="setup-warn">The default PIN (1234) must be changed before continuing.</div>'
      : '';

    body.innerHTML = `
      <div class="setup-section-title">Secure your panel</div>
      ${pinWarn}

      <div class="field mt-16">
        <label>New Admin PIN <span class="text-sub">(4–8 digits, used to log in remotely)</span></label>
        <input class="input" id="sw-pin" type="password" maxlength="8" placeholder="••••"
               inputmode="numeric" pattern="[0-9]*">
      </div>
      <div class="field">
        <label>Confirm PIN</label>
        <input class="input" id="sw-pin2" type="password" maxlength="8" placeholder="••••"
               inputmode="numeric" pattern="[0-9]*">
      </div>

      <div class="setup-section-title mt-24">Steam Game Server Login Token <span class="text-sub">(optional)</span></div>
      <p class="setup-hint">Required to make your server visible in the public matchmaking browser.
        Get one free at <strong>steamcommunity.com/dev/managegameservers</strong> (App ID 730).</p>
      <div class="field">
        <label>Game Server Login Token <span class="field-label-note">(GSLT)</span></label>
        <input class="input" id="sw-gslt" placeholder="Leave blank to skip — add later in Config"
               value="${data.gslt_token}">
      </div>
      <div class="setup-err hidden" id="sw-err"></div>`;

    footer.innerHTML = `
      <button class="btn btn-ghost" id="sw-back2">← Back</button>
      <button class="btn btn-accent" id="sw-next2">Next →</button>`;

    el('sw-back2').addEventListener('click', () => { step = 1; render(); });
    el('sw-next2').addEventListener('click', async () => {
      const pin  = el('sw-pin').value.trim();
      const pin2 = el('sw-pin2').value.trim();
      const gslt = el('sw-gslt').value.trim();

      if (!/^\d{4,8}$/.test(pin))      { showErr('sw-err', 'PIN must be 4–8 digits'); return; }
      if (pin !== pin2)                  { showErr('sw-err', 'PINs do not match');      return; }
      if (pin === '1234')                { showErr('sw-err', 'Please choose a PIN that isn\'t 1234'); return; }

      data.admin_pin   = pin;
      data.gslt_token  = gslt;

      const btn = el('sw-next2');
      btn.disabled = true;
      btn.textContent = 'Saving…';

      try {
        await api.setupComplete({
          server_dir: data.server_dir,
          admin_pin:  data.admin_pin,
          gslt_token: data.gslt_token,
        });
        step = 3; render();
      } catch (e) {
        btn.disabled = false;
        btn.textContent = 'Next →';
        showErr('sw-err', e.message);
      }
    });
  }

  /* ── Step 3: All done ──────────────────────────────────────────────── */
  function renderStep3(body, footer) {
    body.innerHTML = `
      <div class="setup-done-icon">✓</div>
      <div class="setup-section-title" style="text-align:center;margin-top:12px">You're all set!</div>
      <p class="setup-hint" style="text-align:center">
        Your server directory and PIN have been saved.<br>
        Head to the <strong>Config</strong> page to install CS2 via steamcmd,
        or jump straight to <strong>Status</strong> to explore the panel.
      </p>`;

    footer.innerHTML = `
      <div></div>
      <button class="btn btn-accent" id="sw-launch">Launch App →</button>`;

    el('sw-launch').addEventListener('click', () => {
      overlay.remove();
      navigate('status');
    });
  }

  /* ── helpers ───────────────────────────────────────────────────────── */
  function showErr(id, msg) {
    const e = el(id);
    e.textContent = msg;
    e.classList.remove('hidden');
    setTimeout(() => e.classList.add('hidden'), 4000);
  }

  render();
}

/* ══════════════════════════════════════════════════════════════ APPEARANCE PAGE */

pages['appearance'] = function() {
  const root = el('content');

  const ACCENTS = [
    { id: 'purple', color: '#a03af5', label: 'Purple' },
    { id: 'blue',   color: '#4e9aff', label: 'Blue'   },
    { id: 'teal',   color: '#14b8a6', label: 'Teal'   },
    { id: 'green',  color: '#22c55e', label: 'Green'  },
    { id: 'orange', color: '#f59e0b', label: 'Orange' },
    { id: 'red',    color: '#e05c6b', label: 'Red'    },
  ];

  const toggle = (id, checked, onChange) => `
    <label class="toggle" id="tgl-wrap-${id}">
      <input type="checkbox" id="tgl-${id}" ${checked ? 'checked' : ''}>
      <span class="toggle-track"></span>
    </label>`;

  root.innerHTML = `
    <div class="section-hdr mb-16"><span class="section-title">Appearance & Settings</span></div>

    <div class="appearance-grid">

      <!-- Left column -->
      <div>
        <div class="appearance-section">
          <div class="appearance-section-title">Theme</div>
          <div class="theme-options">
            <button class="theme-btn ${appSettings.theme==='dark'   ? 'active':''}" data-theme="dark">
              <svg viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
              Dark
            </button>
            <button class="theme-btn ${appSettings.theme==='light'  ? 'active':''}" data-theme="light">
              <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
              Light
            </button>
            <button class="theme-btn ${appSettings.theme==='system' ? 'active':''}" data-theme="system">
              <svg viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
              System
            </button>
          </div>
        </div>

        <div class="appearance-section">
          <div class="appearance-section-title">Accent Colour</div>
          <div class="accent-swatches">
            ${ACCENTS.map(a => `
              <div class="accent-swatch ${appSettings.accent===a.id?'active':''}"
                   data-accent="${a.id}" title="${a.label}"
                   style="background:${a.color}"></div>
            `).join('')}
          </div>
        </div>

        <div class="appearance-section">
          <div class="appearance-section-title">Layout</div>
          <div class="card" style="padding:0 16px">
            <div class="setting-row">
              <div><div class="setting-label">Compact Mode</div>
                <div class="setting-desc">Tighter spacing throughout the UI</div></div>
              ${toggle('compact', appSettings.compact)}
            </div>
          </div>
        </div>
      </div>

      <!-- Right column -->
      <div>
        <div class="appearance-section">
          <div class="appearance-section-title">Behaviour</div>
          <div class="card" style="padding:0 16px">
            <div class="setting-row">
              <div><div class="setting-label">Confirm before stopping server</div>
                <div class="setting-desc">Show a prompt before shutting down</div></div>
              ${toggle('confirmStop', appSettings.confirmStop)}
            </div>
            <div class="setting-row">
              <div><div class="setting-label">Auto-scroll log</div>
                <div class="setting-desc">Keep the log pinned to the bottom</div></div>
              ${toggle('autoScroll', appSettings.autoScroll)}
            </div>
            <div class="setting-row">
              <div><div class="setting-label">Log line limit</div>
                <div class="setting-desc">Lines kept in memory</div></div>
              <select class="select" id="log-lines-sel" style="width:90px;flex-shrink:0">
                ${[200,400,800].map(n => `<option value="${n}" ${appSettings.logLines===n?'selected':''}>${n}</option>`).join('')}
              </select>
            </div>
          </div>
        </div>

        <div class="appearance-section">
          <div class="appearance-section-title">Notifications</div>
          <div class="card" style="padding:0 16px">
            <div class="setting-row">
              <div><div class="setting-label">Browser notifications</div>
                <div class="setting-desc">Alert when server starts, stops, or crashes</div></div>
              ${toggle('notifications', appSettings.notifications)}
            </div>
          </div>
        </div>
      </div>

    </div>

    <!-- Keybinds — full width below the two columns -->
    <div class="appearance-section keybinds-section">
      <div class="appearance-section-title">
        Keybinds
        <span class="appearance-section-hint">Active when focus is not in a text field · F1–F12, or Ctrl / Alt / Shift + any key</span>
      </div>
      <div class="card keybind-card">
        <div class="keybind-grid">
          ${KB_ACTIONS.map(a => `
            <div class="keybind-row">
              <span class="keybind-action-label">${a.label}</span>
              <input class="input keybind-input" type="text" readonly
                     placeholder="Click to set…" data-action="${a.id}"
                     value="${appSettings.keybinds[a.id] || ''}">
            </div>
          `).join('')}
        </div>
      </div>
    </div>`;

  // Theme buttons
  root.querySelectorAll('.theme-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      appSettings.theme = btn.dataset.theme;
      root.querySelectorAll('.theme-btn').forEach(b => b.classList.toggle('active', b === btn));
      applyAppSettings(); saveAppSettings();
    });
  });

  // Accent swatches
  root.querySelectorAll('.accent-swatch').forEach(sw => {
    sw.addEventListener('click', () => {
      appSettings.accent = sw.dataset.accent;
      root.querySelectorAll('.accent-swatch').forEach(s => s.classList.toggle('active', s === sw));
      applyAppSettings(); saveAppSettings();
    });
  });

  // Toggles
  const wireToggle = (id, key) => {
    const inp = el('tgl-' + id);
    if (!inp) return;
    inp.addEventListener('change', () => {
      appSettings[key] = inp.checked;
      if (key === 'notifications' && inp.checked) {
        Notification.requestPermission().then(p => {
          if (p !== 'granted') { inp.checked = false; appSettings.notifications = false; }
          saveAppSettings();
        });
        return;
      }
      applyAppSettings(); saveAppSettings();
    });
  };
  wireToggle('compact',     'compact');
  wireToggle('confirmStop', 'confirmStop');
  wireToggle('autoScroll',  'autoScroll');
  wireToggle('notifications','notifications');

  // Log line limit
  el('log-lines-sel').addEventListener('change', e => {
    appSettings.logLines = parseInt(e.target.value);
    saveAppSettings();
  });

  // Keybind capture inputs
  root.querySelectorAll('.keybind-input').forEach(inp => {
    let prevVal = '';
    inp.addEventListener('focus', () => {
      prevVal = inp.value;
      inp.value = '';
      inp.placeholder = 'Press a key…';
      inp.classList.add('capturing');
    });
    inp.addEventListener('blur', () => {
      if (!inp.value) inp.value = prevVal;  // cancelled — restore
      inp.placeholder = 'Click to set…';
      inp.classList.remove('capturing');
    });
    inp.addEventListener('keydown', e => {
      e.preventDefault();
      e.stopPropagation();
      const action = inp.dataset.action;

      if (e.key === 'Escape') { inp.value = prevVal; inp.blur(); return; }
      if (e.key === 'Backspace' || e.key === 'Delete') {
        inp.value = '';
        appSettings.keybinds[action] = '';
        saveAppSettings();
        prevVal = '';
        inp.blur();
        return;
      }

      const key = _keyStr(e);
      if (!key) return;   // modifier-only keypress — keep waiting

      // Conflict check
      const conflict = Object.entries(appSettings.keybinds)
        .find(([k, v]) => v === key && k !== action);
      if (conflict) {
        const conflictLabel = KB_ACTIONS.find(a => a.id === conflict[0])?.label || conflict[0];
        toast(`Already bound to "${conflictLabel}"`, 'var(--orange)');
        inp.value = prevVal;
        inp.blur();
        return;
      }

      inp.value = key;
      appSettings.keybinds[action] = key;
      saveAppSettings();
      prevVal = key;
      inp.blur();
    });
  });
};

/* ══════════════════════════════════════════════════════════════ INIT */

async function init() {
  // Apply saved appearance settings before anything renders
  loadAppSettings();

  // Load static game data
  try {
    const [modes, maps, modeMaps, modeWorkshopTags, workshopMaps] = await Promise.all([
      api.modes(), api.maps(), api.modeMaps(), api.modeWorkshopTags(), api.workshopMaps(),
    ]);
    state.modes             = modes;
    state.maps              = maps;
    state.modeMaps          = modeMaps;
    state.modeWorkshopTags  = modeWorkshopTags;
    state.workshopMaps      = workshopMaps;
  } catch (_) {}

  // Log
  await loadLogHistory();
  startSSE();

  // Global keybind handler
  document.addEventListener('keydown', e => {
    if (e.target.matches('input,textarea,select')) return;  // never fire while typing
    if (document.querySelector('.modal-overlay,.setup-overlay')) return; // modal open
    const key = _keyStr(e);
    if (!key) return;
    for (const [action, bound] of Object.entries(appSettings.keybinds)) {
      if (bound && bound === key) { e.preventDefault(); _runKeybind(action); return; }
    }
  });

  // Navigation
  el('nav-logout').addEventListener('click', async e => {
    e.preventDefault();
    await api.logout();
    location.reload();
  });
  document.querySelectorAll('.nav-item[data-page]').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      location.hash = a.dataset.page;
    });
  });

  // Status bar copy clicks
  el('sb-lan').onclick = () => {
    const s = state.server;
    if (s.lan_ip) copyText(`connect ${s.lan_ip}:${s.rcon_port}`, 'Connect string');
  };
  el('sb-pub').onclick = () => {
    if (state.server.public_ip) copyText(state.server.public_ip, 'Public IP');
  };

  // CS2 update badge — clicking triggers a steamcmd update
  el('cs2-update-badge').addEventListener('click', () => {
    if (state.server.running) {
      toast('Stop the server before updating', 'var(--red)');
      return;
    }
    modal(
      'Update CS2 Server',
      '<p style="color:var(--sub);font-size:.86rem">This will run steamcmd to download the latest CS2 server files. The process will be shown in the live log.</p>',
      async () => {
        try { await api.updateCs2(); toast('CS2 update started — check the log'); }
        catch (e) { toast(e.message, 'var(--red)'); }
      },
      'Update'
    );
  });

  // Initial state poll — must happen before setup check so is_local is known
  await pollState();
  _stateInterval = setInterval(pollState, 3000);

  // ── First-run setup check (local window only) ──────────────────────────
  if (state.server.is_local) {
    try {
      const setup = await api.setupStatus();
      if (setup.needs_setup) {
        showSetupWizard(setup);
        return;   // wizard will call navigate() when done
      }
    } catch (_) {}
  }

  // Route to initial page
  navigate(location.hash.replace('#', '') || 'status');
}

document.addEventListener('DOMContentLoaded', init);
