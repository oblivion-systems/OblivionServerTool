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
// Escape untrusted text before it enters innerHTML or a double-quoted attribute.
// Player names, ban entries, workshop titles, preset names, etc. are attacker-
// controlled (a player picks their own in-game name).
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
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
  // Try the modern clipboard API first; fall back to a hidden textarea +
  // execCommand('copy') if it throws or isn't available (older WebView2 builds
  // and HTTP origins both break the modern API silently).
  const fallback = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) toast(`${label} copied`);
      else toast(`${label} copy failed — use Save instead`, 'var(--bad)');
    } catch (e) {
      toast(`${label} copy failed: ${e.message}`, 'var(--bad)');
    }
  };
  if (!navigator.clipboard || !navigator.clipboard.writeText) {
    fallback();
    return;
  }
  navigator.clipboard.writeText(text)
    .then(() => toast(`${label} copied`))
    .catch(fallback);
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

function modal(titleText, bodyHtml, onConfirm, confirmLabel = 'Confirm', opts = {}) {
  // opts.secondaryLabel + opts.onSecondary add a third (middle) button.
  const { secondaryLabel, onSecondary } = opts;
  const ov = h('div', 'modal-overlay');
  const secondaryBtn = secondaryLabel
    ? `<button class="btn" id="modal-secondary">${secondaryLabel}</button>`
    : '';
  ov.innerHTML = `
    <div class="modal">
      <div class="modal-title">${titleText}</div>
      <div class="modal-body">${bodyHtml}</div>
      <div class="modal-actions">
        <button class="btn btn-ghost" id="modal-cancel">Cancel</button>
        ${secondaryBtn}
        <button class="btn btn-accent" id="modal-ok">${confirmLabel}</button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  ov.querySelector('#modal-cancel').onclick = () => ov.remove();
  ov.querySelector('#modal-ok').onclick = () => { ov.remove(); onConfirm(ov); };
  if (secondaryLabel) {
    ov.querySelector('#modal-secondary').onclick = () => {
      ov.remove(); if (onSecondary) onSecondary(ov);
    };
  }
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

// v0.10.2 — Boot-error banner.  Renders a sticky top-of-content card
// when /api/state.boot_error is non-empty (= the most recent Start was
// blocked by a preflight failure).  Auto-clears when the next state
// snapshot has empty boot_error (Stop or successful Start).  Without
// this, a remote admin's Start click silently fails and they're left
// staring at a frozen "Offline" pill with no idea what to do.
let _lastBootError = '';
function _renderBootError(msg) {
  // Don't churn the DOM on every poll — only rebuild when the message changes
  if (msg === _lastBootError) return;
  _lastBootError = msg;
  let banner = el('boot-error-banner');
  if (!msg) {
    if (banner) banner.remove();
    return;
  }
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'boot-error-banner';
    banner.className = 'boot-error-banner';
    // Insert at the top of #content so it's the first thing the operator sees
    const content = el('content');
    if (content && content.parentNode) {
      content.parentNode.insertBefore(banner, content);
    }
  }
  // Split on '; ' so each preflight error becomes its own bullet line
  const parts = msg.split('; ').filter(Boolean);
  banner.innerHTML = `
    <div class="boot-error-head">
      <span class="boot-error-icon">⚠</span>
      <strong>Server start was blocked by pre-flight checks</strong>
      <button class="boot-error-dismiss" type="button" aria-label="Dismiss">×</button>
    </div>
    <ul class="boot-error-list">
      ${parts.map(p => `<li>${esc(p)}</li>`).join('')}
    </ul>
  `;
  banner.querySelector('.boot-error-dismiss').addEventListener('click', () => {
    banner.remove();
    _lastBootError = '';  // allow re-render if a fresh error arrives
  });
}

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
    _stateInterval = setInterval(pollState, 10000);
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

  // Role-based UI. Guests (remote, limited PIN) only get status + map/mode + the
  // workshop browser; everything tagged .admin-only is hidden via CSS. Local
  // window and the admin PIN are always full-access.
  const isAdmin = !!(s.is_local || s.role === 'admin');
  state.isAdmin = isAdmin;
  document.body.classList.toggle('role-guest', !isAdmin);

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

  // Update badges — toggle (not show-only), so they clear live after an update
  // completes instead of lingering until the app is relaunched.
  // v0.10.2: Hide BOTH badges for non-local sessions.  The underlying actions
  // (CS2 update via steamcmd, app self-update) are @require_local — a remote
  // admin clicking them gets a generic 403 toast with no recovery path.
  // The CS2-update flow is also a 15+ minute foreground download with the
  // server stopped; not safe to expose to remote.
  const appBadge = el('app-update-badge');
  const cs2Badge = el('cs2-update-badge');
  if (s.is_local && s.app_update) { appBadge.textContent = `⬆ App ${s.app_version}`; appBadge.classList.remove('hidden'); }
  else appBadge.classList.add('hidden');
  if (s.is_local && s.update_available) { cs2Badge.textContent = '⬆ CS2 Update'; cs2Badge.classList.remove('hidden'); }
  else cs2Badge.classList.add('hidden');

  // v0.10.2 — Hide log drawer entirely for guest role + captain role.
  // Captains shouldn't see log lines about other captains authenticating
  // (their IPs leak via the auth log); guests get the same EventSource
  // 401-then-12-retries hammer that the audit caught.  Admin/local: visible.
  const logDrawer = el('log-drawer');
  if (logDrawer) {
    const showLogs = (s.role === 'admin');
    logDrawer.style.display = showLogs ? '' : 'none';
  }
  // Hide the log Save button for non-local (it's @require_local backed).
  // Save writes to oblivion_log_*.txt in the operator's config dir; the
  // operator IS local, so saving from remote would just toast a 403.
  const logSave = el('log-drawer-save');
  if (logSave) logSave.style.display = s.is_local ? '' : 'none';

  // v0.10.2 — Role pill: small badge near the state pill showing the
  // current session's authentication level.  Captain/guest sessions
  // benefit most (they're remote and have no other visual cue of what
  // they're allowed to do).  Hidden until /api/state populates a role.
  const rolePill = el('hdr-role-pill');
  if (rolePill) {
    const role = (s.role || '').toLowerCase();
    if (!role) {
      rolePill.classList.add('hidden');
    } else {
      rolePill.classList.remove('hidden');
      rolePill.textContent = role;
      rolePill.classList.remove('role-admin', 'role-captain', 'role-guest');
      rolePill.classList.add(`role-${role}`);
      // captain-team suffix for captains so they remember which team
      if (role === 'captain' && s.captain_team) {
        rolePill.textContent = `captain ${s.captain_team}`;
      }
    }
  }

  // v0.10.2 — Hide the LAN row in the status bar for non-local viewers.
  // Remote captains/guests would otherwise see a `connect 192.168.x.x`
  // copy button that can't possibly work from outside the LAN.
  const sbLan = el('sb-lan');
  if (sbLan) sbLan.style.display = s.is_local ? '' : 'none';

  // v0.10.2 — Surface the latest preflight error to the operator if a
  // Start attempt was just refused.  Renders as a sticky top banner that
  // the operator can dismiss; auto-clears when next /api/state has empty
  // boot_error (e.g. after a successful Start or a Stop).
  _renderBootError(s.boot_error || '');

  // v0.11.0 — Discord bot status line on Config page (when the page is open).
  // No-op if the operator isn't on Config or hasn't configured a bot.
  const discordStatus = el('cfg-discord-status');
  if (discordStatus && s.discord_bot) {
    const b = s.discord_bot;
    if (!b.configured) {
      discordStatus.innerHTML = '<span style="color:var(--text-4)">○ Bot not configured</span>';
    } else if (b.connected) {
      discordStatus.innerHTML = `<span style="color:var(--ok)">✓ Connected as <strong>${esc(b.user || '?')}</strong></span>`;
    } else {
      discordStatus.innerHTML = '<span style="color:var(--accent)">… Connecting</span>';
    }
  }
  // Pulse the Config update button when an update was detected (it stays a
  // normal forced-update button when not pulsing — the glow is just a cue).
  el('cfg-update-btn')?.classList.toggle('update-pending', !!s.update_available);

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

  // Update Connect popover with latest state
  if (window.ConnectPopover) ConnectPopover.update(s);

  // Live workshop download progress (per-MB bar)
  _renderDlProgress(s.dl_progress);

  // RCON is local-only, so hide the palette's "Ctrl P · RCON only" hint for remote sessions.
  const rconHint = el('palette-rcon-hint');
  if (rconHint) rconHint.style.display = s.is_local ? '' : 'none';
}

/** Drive the workshop download bar from polled /api/state progress. */
function _renderDlProgress(p) {
  if (!p) return;                       // no active download — log-driven UI handles done/failed
  const wrap = el('ws-progress');       // null when the workshop tab isn't mounted — bail safely
  if (!wrap) return;
  const bar       = wrap.querySelector('.workshop-progress-bar');
  const txt       = el('ws-status-text');
  const dlBtn     = el('ws-dl-btn');
  const cancelBtn = el('ws-cancel-btn');

  _dlStatus.active = true;
  wrap.classList.remove('done');
  wrap.classList.add('active');
  if (dlBtn)     dlBtn.classList.add('hidden');
  if (cancelBtn) cancelBtn.classList.remove('hidden');

  const mb = b => (b / 1048576).toFixed(0);
  if (txt) {
    if (p.phase === 'verifying')   txt.textContent = 'Verifying download…';
    else if (p.total > 0)          txt.textContent = `${mb(p.downloaded)} / ${mb(p.total)} MB (${p.pct}%)`;
    else                           txt.textContent = `${mb(p.downloaded)} MB downloaded…`;
  }
  if (bar) {
    if (p.total > 0) {
      bar.classList.add('determinate');
      bar.style.width = Math.min(100, p.phase === 'verifying' ? 100 : p.pct) + '%';
    } else {
      bar.classList.remove('determinate');
      bar.style.width = '';
    }
  }
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
  const txt       = el('ws-status-text');
  const bar       = el('ws-progress');   // wrapper — null when workshop tab isn't mounted
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
    // Reset the bar so a fresh download doesn't start from the last fill width
    const fill = bar.querySelector('.workshop-progress-bar');
    if (fill) { fill.classList.remove('determinate'); fill.style.width = ''; }
    // Refresh the maps grid so the new map appears immediately
    const grid = el('workshop-maps-grid');
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
  } else if ((t.startsWith('[dd]') || t.includes('  [dd]')) && !state.server.dl_progress) {
    // Only used before the first state poll lands; per-MB progress takes over after.
    const msg = t.replace(/.*\[dd\]\s*/, '').trim();
    if (msg) _dlStatus = { active: true, text: msg };
  } else if (t.includes('… downloading') && !state.server.dl_progress) {
    _dlStatus = { active: true, text: t.replace(/^\s+/, '') };
  } else if (t.includes('DOWNLOAD COMPLETE')) {
    _dlStatus = { active: false, text: '✓ Download complete' };
  } else if (t.includes('download FAILED') || (t.includes('FAILED') && _dlStatus.active)) {
    _dlStatus = { active: false, text: '✗ Download failed — check the log' };
  } else if (t.includes('Download cancelled')) {
    _dlStatus = { active: false, text: '' };
  }
  _updateDlStatusUI();
  if (window.LogDrawer) LogDrawer.append(line);
}

// v0.10.2 — Shared SSE transport.  Replaces two divergent reconnect
// strategies (log: cap at 12 retries; veto: fixed 5 s only while on the
// veto tab).  Single helper with:
//
//   * Exponential backoff (1 → 2 → 4 → 8 → 16 → 30 s capped) so a flaky
//     cell connection doesn't hammer the tunnel
//   * Re-arm on `online` + `visibilitychange` so a phone screen-lock
//     followed by 2 min of background doesn't leave the stream
//     permanently dead
//   * Aggregate "health" status (live / connecting / reconnecting /
//     offline) used by the header status pill so users distinguish
//     "quiet" from "broken"
//   * `close()` returns a handle owners can use to tear down on tab
//     navigation (veto tab does this on hashchange-away)
//
// Public:
//   const handle = _oblivionSSE.connect(path, {onMessage, onOpen, onError, label});
//   handle.close();
//
// Where label is a short string (e.g. "log", "veto", "state") used in
// the aggregate status calculation and console diagnostics.
const _oblivionSSE = (() => {
  const _streams = new Map();   // label -> {es, retries, timer, opts}
  const BACKOFF  = [1000, 2000, 4000, 8000, 16000, 30000];

  // Aggregate status across all active streams.  Mapped to the header
  // status pill via _renderSSEStatus below.
  //   live          — all known streams have emitted at least one message
  //   connecting    — at least one stream is in initial connection
  //   reconnecting  — at least one stream is in backoff after a drop
  //   offline       — all streams have exhausted backoff (or no streams)
  let _aggStatus = 'live';
  function _recomputeStatus() {
    if (_streams.size === 0) { _setStatus('live'); return; }
    let any_connecting = false, any_reconnecting = false, any_live = false, all_dead = true;
    for (const s of _streams.values()) {
      if (s.dead) continue;
      all_dead = false;
      if (s.status === 'live') any_live = true;
      else if (s.status === 'reconnecting') any_reconnecting = true;
      else any_connecting = true;
    }
    if (all_dead) _setStatus('offline');
    else if (any_reconnecting) _setStatus('reconnecting');
    else if (any_live) _setStatus('live');
    else _setStatus('connecting');
  }
  function _setStatus(s) {
    if (_aggStatus === s) return;
    _aggStatus = s;
    _renderSSEStatus(s);
  }

  function connect(path, opts = {}) {
    const label = opts.label || path;
    // Close any prior stream with the same label (idempotent re-subscribe).
    _close(label);
    const state = {
      es: null, retries: 0, timer: null, opts, status: 'connecting',
      path, label, dead: false, closed: false,
    };
    _streams.set(label, state);
    _open(state);
    _recomputeStatus();
    return {
      close() {
        state.closed = true;
        _close(label);
      },
    };
  }

  function _open(state) {
    try {
      state.es = new EventSource(state.path);
    } catch (exc) {
      _scheduleReconnect(state, exc);
      return;
    }
    state.es.onopen = () => {
      // EventSource onopen fires when the connection is established;
      // the first message will flip status from "connecting" → "live".
    };
    state.es.onmessage = e => {
      state.retries = 0;
      state.status = 'live';
      _recomputeStatus();
      try { if (state.opts.onMessage) state.opts.onMessage(e); } catch (exc) {
        console.warn(`[sse:${state.label}] onMessage threw`, exc);
      }
    };
    state.es.onerror = () => {
      try { state.es.close(); } catch (_) {}
      state.es = null;
      if (state.closed) return;
      _scheduleReconnect(state);
    };
  }

  function _scheduleReconnect(state) {
    if (state.closed) return;
    const i = Math.min(state.retries, BACKOFF.length - 1);
    const delay = BACKOFF[i];
    state.retries++;
    state.status = 'reconnecting';
    _recomputeStatus();
    state.timer = setTimeout(() => {
      if (state.closed) return;
      _open(state);
    }, delay);
    // After many retries (~5 min real time at the 30 s cap), mark dead so
    // the aggregate goes to "offline" — but we KEEP retrying (it's cheap).
    // The visibilitychange/online handlers will reset retries on user
    // activity so a long-locked phone catches up fast on wake.
    if (state.retries > 10) state.dead = true;
  }

  function _close(label) {
    const s = _streams.get(label);
    if (!s) return;
    s.closed = true;
    if (s.timer) clearTimeout(s.timer);
    try { if (s.es) s.es.close(); } catch (_) {}
    _streams.delete(label);
    _recomputeStatus();
  }

  // Re-arm: reset retries + immediately re-open every still-alive stream.
  // Called on `online` event (network came back) and `visibilitychange`
  // when the page becomes visible after a screen-lock or tab-switch.
  function _reArmAll() {
    for (const s of _streams.values()) {
      if (s.closed) continue;
      s.retries = 0;
      s.dead = false;
      if (s.timer) { clearTimeout(s.timer); s.timer = null; }
      try { if (s.es) s.es.close(); } catch (_) {}
      s.es = null;
      _open(s);
    }
    _recomputeStatus();
  }
  // Global re-arm triggers — fired by _wireMobileSSEReconnect during init().
  // (We don't bind them here directly because they'd run on script load,
  // before init has a chance to set up other things.)
  window.addEventListener('online',         _reArmAll);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') _reArmAll();
  });
  // Tear all streams down on Ctrl+R / window close so the server-side
  // queues drop us immediately instead of waiting for the next keepalive
  // cycle to notice the dead socket.
  window.addEventListener('beforeunload', () => {
    for (const label of Array.from(_streams.keys())) _close(label);
  });

  return { connect, status: () => _aggStatus };
})();

// Header status pill renderer.  Mirrors _aggStatus from _oblivionSSE.
// "live" → hidden (no clutter when everything's fine); other states
// show a coloured pill so the user can distinguish quiet from broken.
function _renderSSEStatus(status) {
  const pill = document.getElementById('hdr-sse-status');
  if (!pill) return;
  pill.classList.remove('hidden', 'sse-live', 'sse-connecting', 'sse-reconnecting', 'sse-offline');
  if (status === 'live') {
    pill.classList.add('hidden');
    return;
  }
  pill.classList.add(`sse-${status}`);
  pill.textContent = {
    connecting:   'Connecting…',
    reconnecting: 'Reconnecting…',
    offline:      '✗ Offline',
  }[status] || status;
}

// Existing log SSE — refactored to use the shared module.  Preserves the
// previous behaviour: appendLog on each line.  No more 12-retry cap (the
// shared module handles backoff cleanly + re-arms on visibility/online).
let _logSseHandle = null;
function startSSE() {
  if (_logSseHandle) { _logSseHandle.close(); _logSseHandle = null; }
  _logSseHandle = _oblivionSSE.connect('/api/log/stream', {
    label: 'log',
    onMessage: e => appendLog(e.data),
  });
}

async function loadLogHistory() {
  try {
    const lines = await api.logHistory();
    logLines = lines.slice(-400);
  } catch (_) {}
}

/* ══════════════════════════════════════════════════════════════ STATUS PAGE v2 */

function renderStatusState() {
  const s = state.server;
  const cls = s.boot_state === 'ready'   ? 'online'
            : s.boot_state === 'booting' ? 'booting'
            : 'offline';
  const isOnline = cls === 'online';

  // ── Server panel ─────────────────────────────────────────────────────────
  const panel = el('server-panel');
  if (!panel) return;
  panel.className = `server-panel ${cls}`;

  const stateText = el('sp-state');
  if (stateText) stateText.textContent =
      cls === 'online'  ? 'Running'
    : cls === 'booting' ? 'Booting…'
    :                     'Offline';

  const setMeta = (id, val, accent = false) => {
    const e = el(id);
    if (!e) return;
    e.textContent = val;
    e.classList.toggle('accent', accent && isOnline);
    e.classList.toggle('dim', !isOnline);
  };
  setMeta('sp-uptime',  isOnline ? fmtUptime(state._localUptime) : '—', true);
  setMeta('sp-players', isOnline ? `${s.player_count || 0} / ${s.max_players || 10}` : '—');
  setMeta('sp-tick',    isOnline ? '64' : '—');

  // Install-needed banner
  const existing = el('install-banner');
  const showBanner = s.server_dir && !s.is_installed && !s.running;
  if (showBanner && !existing) {
    const banner = h('div', 'install-banner', `
      ${icon('<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>')}
      CS2 is not installed in the configured directory.
      <a href="#config" class="install-banner-link">Go to Config → Install</a>`);
    banner.id = 'install-banner';
    panel.parentNode.insertBefore(banner, panel);
  } else if (!showBanner && existing) {
    existing.remove();
  }

  // Start / Stop / Restart enabled states
  const startBtn   = el('sp-start-btn');
  const stopBtn    = el('sp-stop-btn');
  const restartBtn = el('sp-restart-btn');
  if (startBtn) {
    startBtn.disabled = s.running || !s.is_installed;
    startBtn.title    = !s.is_installed ? 'CS2 not installed — go to Config → Install' : '';
    startBtn.style.display = s.running ? 'none' : '';
  }
  if (stopBtn) {
    stopBtn.disabled = !s.running;
    stopBtn.style.display = s.running ? '' : 'none';
  }
  if (restartBtn) {
    restartBtn.disabled = !s.running;
    restartBtn.style.display = s.running ? '' : 'none';
  }

  // ── Match panel ──────────────────────────────────────────────────────────
  const matchPanel = el('match-panel');
  if (matchPanel) {
    if (!isOnline) {
      matchPanel.className = 'match-panel empty';
      matchPanel.innerHTML = 'Server is offline — start the server to see live match data.';
    } else {
      // Rebuild if previously in empty state
      if (matchPanel.classList.contains('empty')) {
        matchPanel.className = 'match-panel';
        matchPanel.innerHTML = _matchPanelTemplate();
        _wireMatchControls();
      }

      const mapKey = s.map || '';
      const thumbImg = el('mp-thumb-img');
      const thumbEmpty = el('mp-thumb-empty');
      if (thumbImg && (thumbImg.dataset.mapKey || '') !== mapKey) {
        thumbImg.dataset.mapKey = mapKey;
        if (mapKey) {
          thumbImg.style.display = '';
          thumbEmpty.style.display = 'none';
          thumbImg.src = `/api/maps/thumb/${mapKey}`;
          thumbImg.onerror = () => {
            thumbImg.style.display = 'none';
            thumbEmpty.style.display = 'flex';
          };
        } else {
          thumbImg.style.display = 'none';
          thumbEmpty.style.display = 'flex';
        }
      }

      const tag = el('mp-tag');
      if (tag) tag.textContent = mapKey.startsWith('cs_') ? 'CS' : 'DE';

      const mapName = el('mp-map-name');
      if (mapName) mapName.textContent = mapKey || '—';

      const mapMode = el('mp-map-mode');
      if (mapMode) mapMode.textContent =
        `${s.mode || 'competitive'} · ${s.player_count || 0} / ${s.max_players || 10}`;

      const round = el('mp-round');
      if (round) round.textContent =
        (s.round_total !== undefined && s.round_total !== null)
          ? `Round ${String(s.round_current || s.round_total || 0).padStart(2, '0')}`
          : '';

      const playersMeta = el('mp-players-meta');
      if (playersMeta) playersMeta.textContent = `${s.player_count || 0} of ${s.max_players || 10} players`;

      const tScore  = el('mp-t-score');
      const ctScore = el('mp-ct-score');
      if (tScore)  tScore.textContent  = String((s.t_score  ?? 0)).padStart(2, '0');
      if (ctScore) ctScore.textContent = String((s.ct_score ?? 0)).padStart(2, '0');
    }
  }

  // ── Settings strip ───────────────────────────────────────────────────────
  const ffToggle = el('ss-ff-toggle');
  const ffState  = el('ss-ff-state');
  if (ffToggle) {
    ffToggle.classList.toggle('on', !!s.ff_enabled);
    ffToggle.title = s.ff_enabled ? 'Friendly Fire ON — click to disable' : 'Friendly Fire OFF — click to enable';
  }
  if (ffState) {
    ffState.textContent = s.ff_enabled ? 'On' : 'Off';
    ffState.classList.toggle('on', !!s.ff_enabled);
  }
  const ssMode = el('ss-mode-name');
  if (ssMode) ssMode.textContent = s.mode || 'competitive';

  // ── Match controls disabled when offline ─────────────────────────────────
  document.querySelectorAll('.match-panel .mc').forEach(mc => {
    mc.disabled = !isOnline;
  });
}

/* ─── Remote Reachability (v1.2 — Steam master server query) ───────────────
 * Asks Valve's master server whether our CS2 server is registered + visible
 * to external players.  Renders a single severity-coded hint plus optional
 * detail about the matched server.  Zero infrastructure needed — Steam Web
 * API is free, no key, authoritative for the "can players reach me?"
 * question.
 */
async function _reachCheck(card, body, btn, silent = false) {
  if (btn) btn.disabled = true;
  body.innerHTML = `<div class="dim" style="font-size:13px">Asking Valve's master server…</div>`;
  try {
    const r = await api.reachability.check();
    card.classList.remove('hidden');
    body.innerHTML = _renderReachResult(r);
  } catch (err) {
    // 503 from our endpoint = either "no public IP yet" or "Steam Web API
    // unreachable".  Either is worth surfacing — on first load (silent),
    // leave the card hidden so a fresh launch isn't noisy; on user click,
    // surface the actual message.
    if (silent) return;
    card.classList.remove('hidden');
    body.innerHTML = `
      <div style="color:var(--bad);font-size:13px">
        ${esc(err.message || 'Reachability check failed.')}
      </div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function _renderReachResult(r) {
  const target  = r.target  || '(unknown)';
  const hints   = r.hints   || [];
  const servers = r.servers || [];
  const ctx     = r.context || {};

  const sevColor = (sev) =>
    sev === 'ok'   ? 'var(--ok)'
  : sev === 'warn' ? 'var(--warn)'
  : sev === 'info' ? 'var(--accent, #888)'
  :                  'var(--bad)';

  const hintBlock = hints.map(h => `
    <div class="reach-hint reach-sev-${h.severity}" style="border-left-color:${sevColor(h.severity)}">
      <div class="reach-hint-msg">${esc(h.message)}</div>
      ${h.fix ? `<div class="reach-hint-fix">${esc(h.fix)}</div>` : ''}
    </div>
  `).join('');

  const serverList = servers.length ? `
    <div class="dim" style="font-size:12px;margin-top:8px">
      Steam master sees ${servers.length === 1 ? '1 server' : servers.length + ' servers'} at this IP:
      <ul style="margin:4px 0 0 18px;padding:0">
        ${servers.map(s => `<li>
          <code>${esc(s.addr || '?')}</code>
          ${s.secure === false ? '<span class="reach-badge reach-warn" style="margin-left:6px">VAC: off</span>' : ''}
        </li>`).join('')}
      </ul>
    </div>` : '';

  return `
    <div class="reach-target dim" style="font-size:12px;margin-bottom:6px">
      Public IP: <code>${esc(target)}</code>
      ${ctx.expected_port ? ` · expected port <code>${ctx.expected_port}</code>` : ''}
    </div>
    ${hintBlock ? `<div class="reach-hints">${hintBlock}</div>` : ''}
    ${serverList}
  `;
}

function _matchPanelTemplate() {
  return `
    <div class="mp-thumb-side">
      <span class="mp-bracket"></span>
      <span class="mp-tag" id="mp-tag">DE</span>
      <img id="mp-thumb-img" src="" alt="" draggable="false">
      <div class="mp-thumb-empty" id="mp-thumb-empty">no map loaded</div>
      <div class="mp-map-mode" id="mp-map-mode">—</div>
      <div class="mp-map-name" id="mp-map-name">—</div>
    </div>
    <div class="mp-body">
      <div class="mp-head">
        <span class="mp-live-tag"><span class="mp-live-dot"></span>Live</span>
        <span class="mp-round" id="mp-round"></span>
        <span class="mp-players-meta" id="mp-players-meta"></span>
      </div>
      <div class="mp-score-line">
        <div class="mp-side t">
          <span class="lbl">T side</span>
          <span class="num" id="mp-t-score">00</span>
        </div>
        <span class="vs">— VS —</span>
        <div class="mp-side ct">
          <span class="lbl">CT side</span>
          <span class="num" id="mp-ct-score">00</span>
        </div>
      </div>
      <div class="mp-controls">
        <button class="mc" id="mc-warmup">
          ${icon('<polygon points="5 3 19 12 5 21 5 3"/>')}
          End warmup<span class="mc-kbd">F6</span>
        </button>
        <button class="mc" id="mc-restart">
          ${icon('<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.25"/>')}
          Restart round<span class="mc-kbd">⌃R</span>
        </button>
        <button class="mc" id="mc-pause">
          ${icon('<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>')}
          Pause<span class="mc-kbd">F7</span>
        </button>
        <button class="mc" id="mc-broadcast">
          ${icon('<path d="M3 11l18-5v12L3 13v-2z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>')}
          Broadcast<span class="mc-kbd">B</span>
        </button>
      </div>
    </div>
  `;
}

function _wireMatchControls() {
  const wrap = (fn, okMsg) => async () => {
    try { await fn(); if (okMsg) toast(okMsg); }
    catch (e) { toast(e.message, 'var(--bad)'); }
  };
  const wbtn = (id, handler) => {
    const b = el(id);
    if (b) b.addEventListener('click', handler);
  };
  wbtn('mc-warmup',  wrap(() => api.endWarmup(),   'Warmup ended'));
  wbtn('mc-restart', wrap(() => api.restartRound(),'Round restarted'));
  wbtn('mc-pause',   wrap(async () => {
    // toggle pause / unpause based on state we have
    if (state.server.paused) { await api.unpause(); toast('Match unpaused'); }
    else                     { await api.pause();   toast('Match paused');   }
  }));
  wbtn('mc-broadcast', () => {
    modal(
      'Broadcast Message',
      '<div class="field"><label>Message to all players</label>'
      + '<input class="input" id="broadcast-msg" placeholder="Server message…"></div>',
      async () => {
        const msg = document.getElementById('broadcast-msg').value.trim();
        if (!msg) return;
        try { await api.broadcast(msg); toast('Message sent'); }
        catch (e) { toast(e.message, 'var(--bad)'); }
      },
      'Send'
    );
    setTimeout(() => document.getElementById('broadcast-msg')?.focus(), 50);
  });
}

/* v0.16.5 / task #157 — item D — Getting Started card on Status page.
 * Surfaces the three first-run friction points (install CS2 / install
 * plugin runtime / pick a Quick-Apply Pack) as actionable rows.
 * Calls /api/readiness, maps the existing checks onto the three
 * first-run steps, and renders only if at least one is not green.
 * Auto-hides when all three steps are done. */
async function _renderGettingStartedCard(mountNode) {
  let data;
  try { data = await api.readiness(); }
  catch (e) {
    // If readiness is unreachable (server down, network), don't show
    // a half-rendered card — silently bail.  The friend's first-run
    // path doesn't depend on it being there.
    return;
  }
  const checks = (data && data.checks) || [];
  const get = (key) => checks.find(c => c.key === key) || { status: 'info' };
  const cs    = get('csgo');
  const rt    = get('runtime');
  const pl    = get('plugins');

  // Three rows.  Each row's `done` state derives from the existing
  // readiness checks — we don't add new ones.
  // - "Install CS2 server"          → csgo.status === 'ok'
  // - "Install plugin runtime"      → runtime.status === 'ok'
  // - "Pick a Quick-Apply Pack"     → plugins.status === 'ok' (a mode +
  //                                    its plugins are deployed; vanilla
  //                                    Competitive counts as a non-pack)
  const steps = [
    {
      id: 'cs2',
      done: cs.status === 'ok',
      title: 'Install CS2 dedicated server',
      hint:  cs.status === 'ok' ? cs.detail || 'csgo/ found.' :
             '~15 GB download via steamcmd.  Walk away for 10 minutes.',
      ctaLabel: cs.status === 'ok' ? '✓ Done' : 'Open Config → Install',
      onClick:  () => navigate('config'),
    },
    {
      id: 'runtime',
      done: rt.status === 'ok',
      title: 'Install plugin runtime (MetaMod + CounterStrikeSharp)',
      hint:  rt.status === 'ok' ? 'MetaMod + CSS detected.' :
             rt.status === 'warn' ? rt.detail || 'Partially installed.' :
             'Needed for MatchZy, Warcraft, Retakes etc.  One click each.',
      ctaLabel: rt.status === 'ok' ? '✓ Done' : 'Open Plugins → Set up runtime',
      onClick:  () => navigate('plugins'),
    },
    {
      id: 'pack',
      done: pl.status === 'ok',
      title: 'Pick a Quick-Apply Pack',
      hint:  pl.status === 'ok' ? pl.detail || 'Pack active.' :
             'Competitive 5v5 / Warcraft Night / Casual DM — one click stages mode + map + plugins.',
      ctaLabel: pl.status === 'ok' ? '✓ Done' : 'Open Plugins → Packs',
      onClick:  () => navigate('plugins'),
    },
  ];

  const doneCount = steps.filter(s => s.done).length;
  // All three green?  Don't render — friend is ready, no card.
  if (doneCount === steps.length) return;

  // Render.
  const card = h('div', 'getting-started-card');
  card.innerHTML = `
    <div class="gs-hdr">
      <span class="gs-title">🚀 Getting started — ${doneCount} of ${steps.length} done</span>
      <button class="btn btn-sm btn-ghost" id="gs-dismiss" title="Hide for this session">✕</button>
    </div>
    <div class="gs-progress">
      <div class="gs-progress-bar" style="width:${(doneCount / steps.length) * 100}%"></div>
    </div>
    <ol class="gs-steps">
      ${steps.map((s, i) => `
        <li class="gs-step ${s.done ? 'gs-step-done' : ''}">
          <span class="gs-step-num">${s.done ? '✓' : i + 1}</span>
          <div class="gs-step-body">
            <div class="gs-step-title">${esc(s.title)}</div>
            <div class="gs-step-hint">${esc(s.hint)}</div>
          </div>
          <button class="btn btn-sm ${s.done ? 'btn-ghost' : 'btn-accent'}"
                  data-step="${s.id}" ${s.done ? 'disabled' : ''}>
            ${esc(s.ctaLabel)}
          </button>
        </li>`).join('')}
    </ol>
    <div class="gs-footer text-sm">
      Once all three are green, you're ready for a tournament.
      Discord is optional — set it up later in Config → Discord.
      <a href="#" id="gs-preflight">Open full Pre-flight check →</a>
    </div>
  `;
  mountNode.appendChild(card);

  // Wire up the per-step action buttons.
  card.querySelectorAll('button[data-step]').forEach(btn => {
    const stepId = btn.dataset.step;
    const step   = steps.find(s => s.id === stepId);
    if (step && !step.done) btn.addEventListener('click', step.onClick);
  });

  el('gs-dismiss').addEventListener('click', () => {
    // Session-scoped (not permanent) — operator who refreshes the app
    // gets the card back, so it's easy to recover from an accidental
    // dismiss.  Permanent hide would need a Config toggle.
    sessionStorage.setItem('oblivion-getting-started-dismissed', '1');
    card.remove();
  });

  el('gs-preflight').addEventListener('click', (e) => {
    e.preventDefault();
    navigate('readiness');
  });
}

function buildStatusPage() {
  const s = state.server;
  const root = el('content');

  // ── Getting Started card (v0.16.5 / task #157 — item D) ─────────────────
  // First-time friend lands on this page after the wizard.  If CS2 isn't
  // installed yet, or the plugin runtime is missing, surface a 3-step
  // checklist with action buttons that navigate to the right tab.  Card
  // auto-hides once all three checks pass.  Operator can dismiss for
  // the session via the X button (sessionStorage flag).
  if (!sessionStorage.getItem('oblivion-getting-started-dismissed')) {
    const gsContainer = h('div');
    gsContainer.id = 'getting-started-mount';
    root.appendChild(gsContainer);
    _renderGettingStartedCard(gsContainer);   // async; fades in if needed
  }

  // ── Server panel (process state) ─────────────────────────────────────────
  const sp = h('div', 'server-panel offline');
  sp.id = 'server-panel';
  sp.innerHTML = `
    <div class="sp-pulse-wrap">
      <span class="sp-pulse"></span>
      <span class="sp-state" id="sp-state">Offline</span>
    </div>
    <div class="sp-meta">
      <div class="sp-m"><div class="k">Uptime</div><div class="v" id="sp-uptime">—</div></div>
      <div class="sp-m"><div class="k">Players</div><div class="v" id="sp-players">—</div></div>
      <div class="sp-m"><div class="k">Tick</div><div class="v" id="sp-tick">—</div></div>
    </div>
    <div class="sp-controls admin-only">
      <button class="btn btn-green" id="sp-start-btn">
        ${icon('<polygon points="5 3 19 12 5 21 5 3"/>')} Start
      </button>
      <button class="btn" id="sp-restart-btn" title="Quick restart — same map &amp; mode">
        ${icon('<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.25"/>')} Restart
      </button>
      <button class="btn btn-red" id="sp-stop-btn">
        ${icon('<rect x="5" y="5" width="14" height="14"/>')} Stop
      </button>
    </div>
  `;
  root.appendChild(sp);

  // ── Match panel (in-game state) ──────────────────────────────────────────
  const mp = h('div', 'match-panel empty');
  mp.id = 'match-panel';
  mp.innerHTML = 'Server is offline — start the server to see live match data.';
  root.appendChild(mp);

  // ── Settings strip (FF, bots, mode) — admin-only ─────────────────────────
  const strip = h('div', 'settings-strip admin-only');
  strip.innerHTML = `
    <div class="ss">
      <span class="ss-lbl">Friendly Fire<span class="desc">mp_friendlyfire</span></span>
      <span class="mini-toggle" id="ss-ff-toggle" title="Click to toggle"></span>
      <span class="mini-toggle-state" id="ss-ff-state">Off</span>
    </div>
    <div class="ss">
      <span class="ss-lbl">Bots<span class="desc">add or kick bots</span></span>
      <div class="bot-count">
        <button class="a" id="ss-bot-add1" title="+1 bot">+1</button>
        <button class="a" id="ss-bot-add5" title="+5 bots">+5</button>
        <button class="a danger" id="ss-bot-kick" title="Kick all bots">×</button>
      </div>
      <select class="select bot-diff-select" id="ss-bot-diff">
        ${['Easy','Normal','Hard','Expert'].map(d =>
          `<option ${d === (s.bot_difficulty||'Normal') ? 'selected' : ''}>${d}</option>`
        ).join('')}
      </select>
    </div>
    <div class="ss">
      <span class="ss-lbl">Mode<span class="desc">game mode</span></span>
      <span class="ss-mode-name" id="ss-mode-name">${s.mode || 'competitive'}</span>
      <button class="btn btn-sm" id="ss-change-btn" style="margin-left: auto;">
        ${icon('<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/>')}
        Change…
      </button>
    </div>
  `;
  root.appendChild(strip);

  // ── Map / mode picker card (collapsed below the strip) ───────────────────
  const mapCard = h('div', 'map-mode-card');
  mapCard.id = 'map-mode-card';
  mapCard.style.marginBottom = '14px';
  mapCard.innerHTML = `
    <div class="card-title">Map &amp; Mode</div>
    <div class="grid-2">
      <div class="field">
        <label>Game Mode</label>
        <select class="select" id="mode-select"></select>
        <div id="mode-hint" class="mode-hint hidden"></div>
      </div>
      <div class="field">
        <label>Map</label>
        <select class="select" id="map-select"></select>
      </div>
    </div>
    <div class="selected-map" id="selected-map"></div>
    <button class="btn btn-accent btn-full" id="map-change-btn" style="margin-top:4px">
      ${icon('<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/>')}
      Change Map
    </button>
  `;
  root.appendChild(mapCard);

  // ── Remote Reachability card (v1.2 — admin-only, hides if probe unset) ──
  // Calls /api/reachability/check on demand; renders per-port TCP/UDP
  // badges + one-line operator-facing hints.  The card is mounted hidden
  // and only un-hides if the first probe call returns 200 OR returns 503
  // with configured:true (meaning the probe URL is set but the service
  // is currently down — surface that distinctly).  503+configured:false
  // = feature off, card stays hidden.
  const reachCard = h('div', 'card admin-only mb-16 hidden');
  reachCard.id = 'reach-card';
  reachCard.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px">
      <span class="card-title" style="margin-bottom:0">Remote Reachability</span>
      <button class="btn btn-sm" id="reach-check-btn">
        ${icon('<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>')}
        Re-check
      </button>
    </div>
    <div class="reach-body" id="reach-body">
      <div class="dim" style="font-size:13px">
        Click "Re-check" to probe whether players can reach this server
        from outside the LAN.
      </div>
    </div>
  `;
  root.appendChild(reachCard);

  // Wire the button + initial auto-probe (deferred so the page renders first).
  const reachBtn  = el('reach-check-btn');
  const reachBody = el('reach-body');
  if (reachBtn && reachBody) {
    reachBtn.addEventListener('click', () => _reachCheck(reachCard, reachBody, reachBtn));
    // First-load probe — fire & forget, surfaces the card if configured.
    setTimeout(() => _reachCheck(reachCard, reachBody, reachBtn, true), 250);
  }

  // Populate mode select
  const modeSel = el('mode-select');
  _populateModeSelect(modeSel, state.modes, s.mode);

  // Populate maps (single unified picker: official + workshop)
  populateMapSelect(modeSel.value);
  updateModeHint(modeSel.value);

  // Apply current state
  if (s.boot_state === 'ready') {
    el('match-panel').className = 'match-panel';
    el('match-panel').innerHTML = _matchPanelTemplate();
    _wireMatchControls();
  }
  renderStatusState();

  // ── Wire server controls ─────────────────────────────────────────────────
  el('sp-start-btn').addEventListener('click', () => {
    const { map, ws } = getSelectedMap();
    const mode = el('mode-select').value;
    if (!map) { toast('Select a map first', 'var(--bad)'); return; }
    withModeMatchGuard(mode, map, ws, (useMode) => {
      withGsltGuard(() => {
        (async () => {
          try { await api.start(map, useMode, ws); toast('Server starting…', 'var(--ok)'); }
          catch (e) { toast(e.message, 'var(--bad)'); }
        })();
      });
    });
  });
  el('sp-restart-btn').addEventListener('click', doQuickRestart);
  el('sp-stop-btn').addEventListener('click', async () => {
    const doStop = async () => {
      try { await api.stop(); toast('Server stopping…'); }
      catch (e) { toast(e.message, 'var(--bad)'); }
    };
    if (appSettings.confirmStop) {
      modal('Stop Server', '<p style="color:var(--text-3);font-size:.9rem">Are you sure you want to stop the server?</p>',
        doStop, 'Stop');
    } else { doStop(); }
  });

  // ── Wire mode picker / map change ────────────────────────────────────────
  modeSel.addEventListener('change', e => {
    populateMapSelect(e.target.value);
    updateModeHint(e.target.value);
  });
  el('map-select').addEventListener('change', updateSelectedMap);
  el('map-change-btn').addEventListener('click', () => {
    const { map, ws } = getSelectedMap();
    const mode = el('mode-select').value;
    if (!map) { toast('Select a map first', 'var(--bad)'); return; }
    withModeMatchGuard(mode, map, ws, (useMode) => {
      (async () => {
        try { await api.map(map, useMode, ws); toast(`Changing to ${map}…`); }
        catch (e) { toast(e.message, 'var(--bad)'); }
      })();
    });
  });
  el('ss-change-btn').addEventListener('click', () => {
    el('map-mode-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    el('mode-select').focus();
  });

  // ── Wire settings strip ──────────────────────────────────────────────────
  el('ss-ff-toggle').addEventListener('click', async () => {
    const now = state.server.ff_enabled;
    try { await api.ff(!now); toast(`Friendly Fire ${!now ? 'enabled' : 'disabled'}`); }
    catch (e) { toast(e.message, 'var(--bad)'); }
  });
  el('ss-bot-diff').addEventListener('change', async e => {
    try { await api.setConfig({ bot_difficulty: e.target.value }); } catch (_) {}
  });
  el('ss-bot-add1').addEventListener('click', async () => {
    try { await api.addBots(1); toast('+1 bot added'); } catch (e) { toast(e.message, 'var(--bad)'); }
  });
  el('ss-bot-add5').addEventListener('click', async () => {
    try { await api.addBots(5); toast('+5 bots added'); } catch (e) { toast(e.message, 'var(--bad)'); }
  });
  el('ss-bot-kick').addEventListener('click', async () => {
    try { await api.kickBots(); toast('All bots kicked'); } catch (e) { toast(e.message, 'var(--bad)'); }
  });
}

const MODE_HINTS = {
  'Retakes': 'Players type <kbd>!r</kbd> in chat to ready up — game starts when everyone is ready.',
  'Fun': '🎭 <strong>Fun Mode</strong> — MatchZy 5v5 + random cartoon character every round. '
       + '<strong>GSLT is auto-disabled</strong> (custom models can get a GSLT token banned), so this runs '
       + '<strong>LAN / private only</strong> — not for ranked or public. Needs PlayerModelChanger + MultiAddonManager installed.',
};
function updateModeHint(mode) {
  const hint = el('mode-hint');
  if (!hint) return;
  const text = MODE_HINTS[mode];
  if (text) { hint.innerHTML = text; hint.classList.remove('hidden'); }
  else       { hint.innerHTML = '';   hint.classList.add('hidden');    }
}

/* v0.11.8 — Mode picker categorisation.
 *
 * Five "Vanilla CS2" modes (no plugins) split out from eleven
 * "Plugin-enhanced" modes (auto-deployed by Oblivion).  Mirrors the
 * map picker's optgroup pattern + tint.  Each plugin-enhanced option
 * gets a ` · pluginName` suffix so the operator knows what's powering
 * the mode; the two MetaMod modes additionally show `(restart on
 * switch)` so the operational cost is visible BEFORE picking, not as
 * a surprise toast after.
 *
 * Modes that aren't recognised here fall back to alphabetical order
 * inside an "Other" group — defensive against the backend adding a
 * new mode the SPA hasn't been updated to label.  ROADMAP for v0.12
 * has this moving into the plugin-registry (`drivers/cs2/modes.json`)
 * so this client-side table goes away. */
const _MODE_CATEGORY = {
  // Vanilla CS2 — no managed plugins, gameinfo.gi stays unpatched.
  'Competitive':    { group: 'Vanilla CS2' },
  'Casual':         { group: 'Vanilla CS2' },
  'Wingman':        { group: 'Vanilla CS2' },
  'Arms Race':      { group: 'Vanilla CS2' },
  'Demolition':     { group: 'Vanilla CS2' },
  // Plugin-enhanced — Oblivion auto-deploys the listed plugin on switch.
  // MetaMod modes get a restart-on-switch warning suffix because that's
  // the operationally relevant difference (CSS plugins hot-reload).
  'Practice':       { group: 'Plugin-enhanced', plugin: 'MatchZy' },
  '3v3':            { group: 'Plugin-enhanced', plugin: 'MatchZy' },
  '4v4':            { group: 'Plugin-enhanced', plugin: 'MatchZy' },
  '5v5':            { group: 'Plugin-enhanced', plugin: 'MatchZy' },
  '1v1':            { group: 'Plugin-enhanced', plugin: 'K4-Arenas' },
  '2v2':            { group: 'Plugin-enhanced', plugin: 'K4-Arenas' },
  'Retakes':        { group: 'Plugin-enhanced', plugin: 'B3none' },
  'Jailbreak':      { group: 'Plugin-enhanced', plugin: 'CSS-Jailbreak' },
  'Warcraft':       { group: 'Plugin-enhanced', plugin: 'CS2-Warcraft' },
  'Deathmatch':     { group: 'Plugin-enhanced', plugin: 'MetaMod', restart: true },
  'Zombie Escape':  { group: 'Plugin-enhanced', plugin: 'MetaMod', restart: true },
  'Fun':            { group: 'Plugin-enhanced', plugin: 'Random Models', restart: true },
};

function _populateModeSelect(sel, modes, selectedMode) {
  if (!sel) return;
  sel.innerHTML = '';
  // Bucket modes by category, preserving the order in `modes` (backend-defined).
  const groups = { 'Vanilla CS2': [], 'Plugin-enhanced': [], 'Other': [] };
  modes.forEach(m => {
    const meta = _MODE_CATEGORY[m];
    if (meta) groups[meta.group].push(m);
    else groups['Other'].push(m);
  });
  const addGroup = (label, list) => {
    if (!list.length) return;
    const grp = document.createElement('optgroup');
    grp.label = label;
    list.forEach(m => {
      const o = document.createElement('option');
      o.value = m;
      const meta = _MODE_CATEGORY[m];
      let txt = m;
      if (meta && meta.plugin) {
        txt += ` · ${meta.plugin}`;
        if (meta.restart) txt += ' (restart on switch)';
      }
      o.textContent = txt;
      if (m === selectedMode) o.selected = true;
      grp.appendChild(o);
    });
    sel.appendChild(grp);
  };
  addGroup('Vanilla CS2',     groups['Vanilla CS2']);
  addGroup('Plugin-enhanced', groups['Plugin-enhanced']);
  addGroup('Other',           groups['Other']);    // defensive: future backend modes
}

/* Single unified map picker: official maps + workshop maps in one <select>, each
 * option tagged with data-ws (0=official, 1=workshop). One control = one selected
 * map = no ambiguity about what Start / Change Map will use. */
function populateMapSelect(mode) {
  const sel = el('map-select');
  if (!sel) return;
  const prev = sel.value;   // preserve the user's pick across a mode change
  sel.innerHTML = '';

  // ── Official maps for this mode ──────────────────────────────────────────
  const official = (state.modeMaps[mode] || state.maps).slice().sort();
  if (official.length) {
    const grp = document.createElement('optgroup');
    grp.label = 'Official Maps';
    official.forEach(m => {
      const o = document.createElement('option');
      o.value = m; o.textContent = m; o.dataset.ws = '0';
      grp.appendChild(o);
    });
    sel.appendChild(grp);
  }

  // ── Workshop maps, split by whether they suit this mode ──────────────────
  // Use the SAME modeSuitsMap notion as the badges + mismatch guard, so the
  // group header and each option's recommended-mode suffix never contradict
  // (a map tagged classic+wingman shouldn't sit under "Recommended for
  // Competitive" while its suffix says "Wingman").
  const maps = state.workshopMaps || [];
  const sort = arr => arr.slice().sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
  const suitedMaps = sort(maps.filter(m => modeSuitsMap(mode, m.tags)));
  const otherMaps  = sort(maps.filter(m => !modeSuitsMap(mode, m.tags)));
  const addWs = (list, label) => {
    if (!list.length) return;
    const grp = document.createElement('optgroup');
    grp.label = label;
    list.forEach(m => {
      const o = document.createElement('option');
      o.value = m.id; o.dataset.ws = '1';
      // Append the map's recommended mode(s) so each option is self-describing
      // (e.g. "ze_random · Zombie Escape"). Only distinctive tags add a suffix.
      const rec = recommendedModes(m.tags);
      o.textContent = (m.name || m.id) + (rec.length ? ` · ${rec.join('/')}` : '');
      grp.appendChild(o);
    });
    sel.appendChild(grp);
  };
  addWs(suitedMaps, `Workshop — Recommended for ${mode}`);
  addWs(otherMaps,  'Workshop — Other');

  // ── Restore selection: keep prior pick, else reflect the running map ─────
  const has = v => [...sel.options].some(o => o.value === v);
  if (prev && has(prev))                    sel.value = prev;
  else if (state.server.map && has(state.server.map)) sel.value = state.server.map;
  updateSelectedMap();
}

/** The currently chosen map + whether it's a workshop item (from data-ws). */
function getSelectedMap() {
  const sel = el('map-select');
  const opt = sel && sel.selectedOptions && sel.selectedOptions[0];
  if (!opt) return { map: '', ws: false, label: '' };
  return { map: opt.value, ws: opt.dataset.ws === '1', label: opt.textContent };
}

/** Refresh the "Selected: <map> [Official|Workshop]" readout under the picker. */
function updateSelectedMap() {
  const box = el('selected-map');
  if (!box) return;
  const { map, ws, label } = getSelectedMap();
  if (!map) { box.innerHTML = '<span class="sm-empty">No map selected</span>'; return; }
  const src = ws ? 'Workshop' : 'Official';
  box.innerHTML = `Selected: <strong>${esc(label)}</strong>`
                + ` <span class="sm-src sm-${ws ? 'ws' : 'off'}">${src}</span>`;
}

pages['status'] = buildStatusPage;

/* ══════════════════════════════════════════════════════════════ PLAYERS PAGE v2
   Scoreboard layout: score bar (T/CT), filter tabs, real table with ping bars,
   compact ban-by-id row + tighter ban list. */

let _playersFilter = 'all';     // all | t | ct | bots | humans
let _playersSearch = '';

/** Render an "admin only" notice for guest-role sessions. Returns true if blocked. */
function _guestBlocked(root) {
  if (state.isAdmin) return false;
  root.innerHTML = `<div class="empty-state" style="padding:48px;text-align:center;color:var(--text-3)">
    <div style="font-size:1rem;color:var(--text-1);margin-bottom:8px">Admin only</div>
    <div style="font-size:.85rem;max-width:420px;margin:0 auto">This section needs the admin PIN.
    You're signed in with limited access — change maps &amp; modes and download workshop maps
    from the <strong>Status</strong> and <strong>Maps</strong> tabs.</div>
  </div>`;
  return true;
}

pages['players'] = function() {
  const root = el('content');
  if (_guestBlocked(root)) return;
  const s = state.server;
  const hasScore = (s.t_score != null && s.ct_score != null);

  root.innerHTML = `
    <div class="players-score-bar ${hasScore ? '' : 'hidden'}" id="players-score-bar">
      <div class="score-side t">
        <span class="side-tag">T side</span>
        <span class="side-score" id="ps-t-score">${(s.t_score ?? 0).toString().padStart(2,'0')}</span>
        <span class="side-grow"></span>
        <span class="side-count" id="ps-t-count">— <span class="of">/ ${s.max_players || 10}</span></span>
      </div>
      <div class="score-side ct">
        <span class="side-tag">CT side</span>
        <span class="side-score" id="ps-ct-score">${(s.ct_score ?? 0).toString().padStart(2,'0')}</span>
        <span class="side-grow"></span>
        <span class="side-count" id="ps-ct-count">— <span class="of">/ ${s.max_players || 10}</span></span>
      </div>
    </div>

    <div class="players-toolbar-v2">
      <div class="players-filter-tabs" id="players-filter-tabs">
        <span class="pf-tab" data-f="all">All <span class="ct" id="pf-c-all">0</span></span>
        <span class="pf-tab" data-f="humans">Humans <span class="ct" id="pf-c-humans">0</span></span>
        <span class="pf-tab" data-f="bots">Bots <span class="ct" id="pf-c-bots">0</span></span>
      </div>
      <div class="players-search-wrap">
        <svg class="ps-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input class="input" id="players-search" placeholder="filter by name or steamid…" autocomplete="off">
      </div>
      <span class="players-toolbar-spacer"></span>
      <button class="btn btn-ghost btn-sm" id="refresh-btn">
        ${icon('<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.25"/>')}
        Refresh
      </button>
    </div>

    <div class="ptable" id="players-ptable">
      <div class="prow head">
        <div></div>
        <div>Player</div>
        <div>SteamID</div>
        <div>Ping</div>
        <div style="justify-content: flex-end; padding-right: 14px;">Actions</div>
      </div>
      <div id="players-body"></div>
    </div>

    <div class="card mb-16">
      <div class="card-title">Ban by SteamID</div>
      <div class="ban-by-id">
        <div class="field">
          <label>SteamID / Steam64</label>
          <input class="input" id="ban-steamid" placeholder="STEAM_0:0:… or 7656119…">
        </div>
        <div class="field">
          <label>Duration · min · 0 = perm</label>
          <input class="input" id="ban-duration" type="number" min="0" value="0">
        </div>
        <button class="btn btn-red" id="ban-submit">
          ${icon('<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>')}
          Ban
        </button>
      </div>
    </div>

    <div class="card">
      <div class="section-hdr">
        <span class="card-title" style="margin-bottom: 0;">Ban List</span>
        <button class="btn btn-ghost btn-sm" id="bans-refresh">
          ${icon('<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.25"/>')}
          Refresh
        </button>
      </div>
      <div class="ban-table" id="ban-list">
        <div class="empty-state text-sm">Loading…</div>
      </div>
    </div>`;

  // Restore persisted filter
  document.querySelectorAll('.pf-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.f === _playersFilter);
    t.addEventListener('click', () => {
      _playersFilter = t.dataset.f;
      document.querySelectorAll('.pf-tab').forEach(x => x.classList.toggle('active', x.dataset.f === _playersFilter));
      _renderPlayersBody();
    });
  });

  const searchEl = el('players-search');
  searchEl.value = _playersSearch;
  searchEl.addEventListener('input', e => {
    _playersSearch = e.target.value;
    _renderPlayersBody();
  });

  el('refresh-btn').addEventListener('click', loadPlayers);
  el('bans-refresh').addEventListener('click', loadBans);

  el('ban-submit').addEventListener('click', async () => {
    const sid = el('ban-steamid').value.trim();
    const dur = parseInt(el('ban-duration').value) || 0;
    if (!sid) { toast('Enter a SteamID', 'var(--bad)'); return; }
    try {
      await api.ban(sid, '', dur);
      toast('Player banned');
      el('ban-steamid').value = '';
      loadBans();
    } catch (e) { toast(e.message, 'var(--bad)'); }
  });

  loadPlayers();
  loadBans();
};

// Cache last-known players so search/filter re-renders don't refetch
let _playersCache = [];

async function loadPlayers() {
  try {
    _playersCache = await api.players();
  } catch (e) {
    const body = el('players-body');
    if (body) body.innerHTML = `<div class="empty-row" style="color:var(--bad)">${e.message}</div>`;
    return;
  }
  _renderPlayersBody();
}

function _renderPlayersBody() {
  const body = el('players-body');
  if (!body) return;

  // Classify each player
  const tagged = _playersCache.map(p => {
    const isBot = !p.steamid || /^BOT/i.test(String(p.steamid)) || /^bot/i.test(p.name || '');
    return { ...p, _isBot: isBot };
  });

  // Filter
  const q = _playersSearch.toLowerCase().trim();
  const list = tagged.filter(p => {
    if (_playersFilter === 'bots'   && !p._isBot) return false;
    if (_playersFilter === 'humans' &&  p._isBot) return false;
    if (q && !(`${p.name || ''} ${p.steamid || ''}`).toLowerCase().includes(q)) return false;
    return true;
  });

  // Counts
  const cAll    = tagged.length;
  const cBots   = tagged.filter(p =>  p._isBot).length;
  const cHumans = cAll - cBots;
  const setCount = (id, n) => { const e = el(id); if (e) e.textContent = n; };
  setCount('pf-c-all', cAll);
  setCount('pf-c-bots', cBots);
  setCount('pf-c-humans', cHumans);

  // Score bar counts (we don't have team data — show humans+bots split on T side as best-effort)
  const sb = el('players-score-bar');
  if (sb && !sb.classList.contains('hidden')) {
    const cnt = state.server.player_count || cAll;
    const tCnt = Math.floor(cnt / 2);
    const ctCnt = cnt - tCnt;
    const tc = el('ps-t-count');  if (tc) tc.innerHTML = `${tCnt} <span class="of">/ ${(state.server.max_players || 10) / 2}</span>`;
    const cc = el('ps-ct-count'); if (cc) cc.innerHTML = `${ctCnt} <span class="of">/ ${(state.server.max_players || 10) / 2}</span>`;
  }

  if (!list.length) {
    const running = state.server.running;
    if (!_playersCache.length && running) {
      body.innerHTML = `
        <div class="empty-state-v2">
          <div class="es-glyph">${icon('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>')}</div>
          <div class="es-title">Server's running — no players yet</div>
          <div class="es-desc">Share the connect link from the header to invite people in.</div>
          <button class="btn btn-accent es-action" onclick="document.getElementById('hdr-connect-btn')?.click()">Open Connect →</button>
        </div>`;
    } else if (!running) {
      body.innerHTML = `
        <div class="empty-state-v2">
          <div class="es-glyph">${icon('<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>')}</div>
          <div class="es-title">Server is offline</div>
          <div class="es-desc">Start the server from the Status page to see live players here.</div>
          <button class="btn btn-accent es-action" onclick="navigate('status')">Go to Status →</button>
        </div>`;
    } else {
      body.innerHTML = `<div class="empty-row">No matches for "${_playersSearch}"</div>`;
    }
    return;
  }

  body.innerHTML = '';
  list.forEach(p => {
    const row = h('div', `prow ${p._isBot ? 'bot' : ''}`);
    const ping = p.ping != null ? Number(p.ping) : null;
    const pingCls = ping == null ? ''
                  : ping > 100   ? 'bad'
                  : ping > 60    ? 'warn'
                  :                '';
    const bars = ping == null
      ? '<span></span><span></span><span></span><span></span>'
      : ping > 100 ? '<span class="bad"></span><span></span><span></span><span></span>'
      : ping > 60  ? '<span class="on"></span><span class="warn"></span><span></span><span></span>'
      : ping > 35  ? '<span class="on"></span><span class="on"></span><span class="on"></span><span></span>'
      :              '<span class="on"></span><span class="on"></span><span class="on"></span><span class="on"></span>';

    const safeName = esc(p.name || 'Unknown');
    const sidStr   = esc(p._isBot ? 'BOT' : (p.steamid || '—'));
    const badge    = p._isBot ? '<span class="role-badge bot">Bot</span>' : '';

    row.innerHTML = `
      <div class="pside-mark"><span class="m"></span></div>
      <div class="pname"><span class="pname-text">${safeName}</span>${badge}</div>
      <div class="psid">${sidStr}</div>
      <div class="pping">
        <span class="pp-val ${pingCls}">${ping != null ? ping : '—'}</span>
        <span class="pp-bars">${bars}</span>
      </div>
      <div class="pactions">
        <button class="btn btn-ghost kick-btn" data-uid="${esc(p.userid)}" data-name="${safeName}">Kick</button>
        ${p._isBot ? '' : `<button class="btn btn-red ban-btn" data-sid="${esc(p.steamid)}" data-name="${safeName}">Ban</button>`}
      </div>`;
    body.appendChild(row);
  });

  body.querySelectorAll('.kick-btn').forEach(b => {
    b.addEventListener('click', async () => {
      try { await api.kick(b.dataset.uid, b.dataset.name); toast(`Kicked ${b.dataset.name}`); loadPlayers(); }
      catch (e) { toast(e.message, 'var(--bad)'); }
    });
  });
  body.querySelectorAll('.ban-btn').forEach(b => {
    b.addEventListener('click', async () => {
      try { await api.ban(b.dataset.sid, b.dataset.name, 0); toast(`Banned ${b.dataset.name}`); loadPlayers(); loadBans(); }
      catch (e) { toast(e.message, 'var(--bad)'); }
    });
  });
}

async function loadBans() {
  const list = el('ban-list');
  if (!list) return;
  try {
    const bans = await api.bans();
    if (!bans.length) {
      _renderEmptyState(list, {
        glyph: '<circle cx="12" cy="12" r="10"/><polyline points="22 4 12 14.01 9 11.01"/>',
        title: 'No bans yet',
        desc:  'Banned SteamIDs will show up here. Ban directly from the player list above.',
      });
      return;
    }
    list.innerHTML = '';
    bans.forEach(line => {
      const sidM = line.match(/(STEAM_\S+|\[U:[^\]]+\]|765\d{14,})/i);
      const sid  = sidM ? sidM[1] : line;
      const row  = h('div', 'ban-row-v2');
      row.innerHTML = `
        <span class="b-sid">${esc(line)}</span>
        <div class="b-actions"><button class="btn btn-ghost btn-sm unban-btn" data-sid="${esc(sid)}">Unban</button></div>`;
      list.appendChild(row);
    });
    list.querySelectorAll('.unban-btn').forEach(b => {
      b.addEventListener('click', async () => {
        try { await api.unban(b.dataset.sid); toast('Player unbanned'); loadBans(); }
        catch (e) { toast(e.message, 'var(--bad)'); }
      });
    });
  } catch (e) {
    list.innerHTML = `<div class="empty-state text-sm text-red">${e.message}</div>`;
  }
}

/* ══════════════════════════════════════════════════════════════ MAPS PAGE */

/* ══════════════════════════════════════════════════════════════ MAPS PAGE v2
   Three tabs: Official · Workshop · Presets.
   Folds in what used to be pages.workshop, and moves Presets out of Config. */

let _mapsTab = 'official';   // remembered between page rebuilds

pages['maps'] = function() {
  const root  = el('content');
  const isLocal = state.server.is_local;

  // Count maps for tab labels
  const officialCount = (state.modeMaps[state.server.mode] || state.maps).length;
  const workshopCount = state.workshopMaps?.length || 0;

  root.innerHTML = `
    <div class="maps-tabs">
      <div class="maps-tabs-strip" id="maps-tabs-strip">
        <span class="maps-tab" data-tab="official">Official <span class="c">${officialCount}</span></span>
        <span class="maps-tab" data-tab="workshop">Workshop <span class="c" id="ws-count">${workshopCount}</span></span>
        <span class="maps-tab" data-tab="presets">Presets <span class="c" id="presets-count">—</span></span>
      </div>
      <div class="maps-search-tab-wrap">
        <svg class="maps-search-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input class="input" id="maps-search" placeholder="Filter…" autocomplete="off" spellcheck="false">
      </div>
      <span class="maps-tab-spacer"></span>
      <select class="select" id="maps-mode-filter" style="width:160px">
        ${state.modes.map(m =>
          `<option value="${m}" ${m === state.server.mode ? 'selected' : ''}>${m}</option>`
        ).join('')}
      </select>
    </div>

    <!-- Official tab -->
    <div class="maps-pane" id="pane-official">
      <div class="maps-grid" id="official-maps-grid"></div>
    </div>

    <!-- Workshop tab -->
    <div class="maps-pane" id="pane-workshop">
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
          <button class="btn btn-ghost btn-sm" id="ws-cmdfilter-scan-btn" title="Check each downloaded map's Steam description for -disable_workshop_command_filtering and flag the ones that need it">⚑ Scan command-filter needs</button>
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
      <div class="workshop-maps-grid" id="workshop-maps-grid">
        <div class="empty-state text-sm">Loading…</div>
      </div>
    </div>

    <!-- Presets tab -->
    <div class="maps-pane" id="pane-presets">
      <div class="preset-save-row">
        <div class="field">
          <label>Save current setup as a preset</label>
          <input class="input" id="preset-name" placeholder="e.g. 5v5 Mirage">
        </div>
        <button class="btn btn-accent" id="preset-save-btn">
          ${icon('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>')}
          Save current
        </button>
        <span class="preset-save-hint">Captures map, mode, friendly fire, and bot difficulty. Load a preset to apply all of them in one tap.</span>
      </div>
      <div class="preset-grid" id="preset-grid">
        <div class="empty-state text-sm">Loading presets…</div>
      </div>
    </div>
  `;

  // ── Activate the persisted tab ───────────────────────────────────────────
  _activateMapsTab(_mapsTab);

  // ── Tab switching ────────────────────────────────────────────────────────
  document.querySelectorAll('.maps-tab').forEach(t => {
    t.addEventListener('click', () => _activateMapsTab(t.dataset.tab));
  });

  // ── Search filter (applies to currently active tab) ──────────────────────
  const applyFilter = (query) => {
    const q = query.toLowerCase().trim();
    const activePane = el(`pane-${_mapsTab}`);
    if (!activePane) return;
    activePane.querySelectorAll('.map-card, .preset-card').forEach(c => {
      const show = !q || (c.dataset.name || '').includes(q);
      c.style.display = show ? '' : 'none';
    });
  };
  el('maps-search').addEventListener('input', e => applyFilter(e.target.value));

  // ── Official grid ────────────────────────────────────────────────────────
  const renderOfficial = (mode) => {
    const grid  = el('official-maps-grid');
    const valid = state.modeMaps[mode] || state.maps;
    grid.innerHTML = '';
    valid.forEach(mapId => {
      const card = h('div', 'map-card' + (mapId === state.server.map ? ' active' : ''));
      card.dataset.name = mapId.toLowerCase();
      card.innerHTML = `
        <img class="map-thumb" src="/api/maps/thumb/${mapId}" loading="lazy"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
        <div class="map-thumb-placeholder" style="display:none">
          ${icon('<polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"/><line x1="9" y1="3" x2="9" y2="18"/><line x1="15" y1="6" x2="15" y2="21"/>')}
        </div>
        <div class="map-info"><div class="map-name">${mapId}</div></div>
      `;
      card.addEventListener('click', async () => {
        try { await api.map(mapId, mode, false); toast(`Changing to ${mapId}…`); }
        catch (e) { toast(e.message, 'var(--bad)'); }
      });
      grid.appendChild(card);
    });
  };
  renderOfficial(el('maps-mode-filter').value);

  el('maps-mode-filter').addEventListener('change', e => {
    renderOfficial(e.target.value);
    applyFilter(el('maps-search').value);
    loadWorkshopMapsGrid(el('workshop-maps-grid'), e.target.value).then(() => {
      el('ws-count').textContent = state.workshopMaps.length;
      applyFilter(el('maps-search').value);
    });
  });

  // ── Workshop grid + downloader ───────────────────────────────────────────
  loadWorkshopMapsGrid(el('workshop-maps-grid'), el('maps-mode-filter').value).then(() => {
    el('ws-count').textContent = state.workshopMaps.length;
  });
  _wireWorkshopDownloader(isLocal);

  // ── Presets grid + save ──────────────────────────────────────────────────
  loadPresetCards();
  el('preset-save-btn').addEventListener('click', async () => {
    const name = el('preset-name').value.trim();
    if (!name) { toast('Enter a preset name', 'var(--bad)'); return; }
    try {
      await api.savePreset(name);
      toast(`Preset "${name}" saved`);
      el('preset-name').value = '';
      loadPresetCards();
    } catch (e) { toast(e.message, 'var(--bad)'); }
  });
};

/** Switch the active tab. Updates DOM + persisted state + clears search. */
function _activateMapsTab(tab) {
  _mapsTab = tab;
  document.querySelectorAll('.maps-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  document.querySelectorAll('.maps-pane').forEach(p => {
    p.classList.toggle('active', p.id === `pane-${tab}`);
  });
  // Reset search when switching tabs — different content domains
  const search = el('maps-search');
  if (search) search.value = '';
}

/** Wire the workshop download form. Behaviour depends on local vs remote. */
function _wireWorkshopDownloader(isLocal) {
  // Paste button — shared between layouts
  el('ws-paste-btn')?.addEventListener('click', async () => {
    try {
      const text = await navigator.clipboard.readText();
      const digits = text.replace(/\D/g, '');
      if (digits) { el('ws-id-input').value = digits; el('ws-id-input').focus(); }
      else toast('Nothing numeric on the clipboard', 'var(--warn)');
    } catch { toast('Clipboard access denied', 'var(--bad)'); }
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
      progress.classList.add('done');
      el('ws-status-text').textContent = _dlStatus.text;
    }

    dlBtn.addEventListener('click', async () => {
      const id = el('ws-id-input').value.trim();
      if (!id) { toast('Enter a Workshop Map ID', 'var(--bad)'); return; }
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
        if (e.needs_steam) {
          toast('Steam credentials required — go to Config → Steam Account', 'var(--warn)');
          setTimeout(() => navigate('config'), 1200);
        } else {
          toast(e.message, 'var(--bad)');
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
      } catch (e) { toast(e.message, 'var(--bad)'); }
    });

    el('ws-update-btn').addEventListener('click', async () => {
      try { await api.workshopUpdate(); toast('Checking for map updates…'); }
      catch (e) { toast(e.message, 'var(--bad)'); }
    });

    el('ws-cmdfilter-scan-btn')?.addEventListener('click', async () => {
      const btn = el('ws-cmdfilter-scan-btn');
      const orig = btn.textContent;
      btn.disabled = true; btn.textContent = '⚑ Scanning…';
      try {
        const r = await api.workshopCmdfilterScan();
        const n = (r.flagged || []).length;
        toast(n ? `${n} map(s) need command-filter disabled` : 'No maps need the command-filter flag');
        const grid = el('workshop-maps-grid');
        if (grid && state.server.mode) loadWorkshopMapsGrid(grid, state.server.mode);
      } catch (e) { toast(e.message, 'var(--bad)'); }
      finally { btn.disabled = false; btn.textContent = orig; }
    });
  } else {
    el('ws-req-btn')?.addEventListener('click', async () => {
      const id = el('ws-id-input').value.trim();
      if (!id) { toast('Enter a Workshop Map ID', 'var(--bad)'); return; }
      try {
        await api.requestWorkshop(id);
        toast('Download request sent — awaiting approval');
      } catch (e) { toast(e.message, 'var(--bad)'); }
    });
  }
}

/** Render a v2 empty state inside any container. */
function _renderEmptyState(target, opts) {
  const { glyph, title, desc, btnLabel, btnAction } = opts;
  target.innerHTML = `
    <div class="empty-state-v2">
      <div class="es-glyph">${icon(glyph)}</div>
      <div class="es-title">${title}</div>
      <div class="es-desc">${desc}</div>
      ${btnLabel ? `<button class="btn btn-accent es-action" id="es-act-btn">${btnLabel}</button>` : ''}
    </div>`;
  if (btnLabel && btnAction) {
    target.querySelector('#es-act-btn').addEventListener('click', btnAction);
  }
}

/** Render the preset tab as a grid of thumbnail cards. */
async function loadPresetCards() {
  const grid = el('preset-grid');
  if (!grid) return;
  try {
    const names = await api.presets();
    const countEl = el('presets-count');
    if (countEl) countEl.textContent = names.length;

    if (!names.length) {
      _renderEmptyState(grid, {
        glyph: '<polyline points="20 6 9 17 4 12"/>',
        title: 'No presets saved yet',
        desc:  'Set up your favourite map + mode, then save it here for one-tap launches later.',
        btnLabel: 'Save current setup →',
        btnAction: () => document.getElementById('preset-name')?.focus(),
      });
      return;
    }

    grid.innerHTML = '';
    // Fetch each preset's details in parallel so we can show the thumbnail + mode
    const details = await Promise.all(names.map(n =>
      api.loadPreset(n).catch(() => null)
    ));

    names.forEach((name, i) => {
      const p = details[i] || { map: '', mode: '' };
      const card = h('div', 'preset-card');
      card.dataset.name = name.toLowerCase();
      const isWorkshop = /^\d+$/.test(p.map);
      const thumbUrl = !isWorkshop && p.map ? `/api/maps/thumb/${encodeURIComponent(p.map)}` : '';
      const metaBits = [];
      if (p.map)            metaBits.push(esc(p.map));
      if (p.mode)           metaBits.push(esc(p.mode));
      if (p.ff_enabled === true)  metaBits.push('FF on');
      if (p.ff_enabled === false) metaBits.push('FF off');

      card.innerHTML = `
        <div class="pc-thumb">
          <span class="pc-bracket"></span>
          ${p.mode ? `<span class="pc-mode-tag">${esc(p.mode)}</span>` : ''}
          ${thumbUrl
            ? `<img src="${esc(thumbUrl)}" alt="" loading="lazy"
                   onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
               <div class="pc-thumb-empty" style="display:none">${isWorkshop ? 'workshop' : 'no thumb'}</div>`
            : `<div class="pc-thumb-empty">${isWorkshop ? 'workshop' : 'no map'}</div>`
          }
        </div>
        <div class="pc-body">
          <span class="pc-name">${esc(name)}</span>
          <span class="pc-meta">${metaBits.join(' <span class="dot">·</span> ') || '—'}</span>
          <div class="pc-actions">
            <button class="btn btn-accent" data-act="load-start">Load &amp; start</button>
            <button class="btn" data-act="load" title="Load into selectors only">
              ${icon('<polyline points="20 6 9 17 4 12"/>')}
            </button>
            <button class="btn btn-red btn-icon" data-act="delete" title="Delete preset">×</button>
          </div>
        </div>
      `;

      // Load & start: apply the preset and start (or change map if running)
      card.querySelector('[data-act="load-start"]').addEventListener('click', async e => {
        e.stopPropagation();
        try {
          const preset = await api.loadPreset(name);
          if (state.server.running) {
            await api.map(preset.map, preset.mode, isWorkshop);
            toast(`Loaded "${name}" — changing map…`);
          } else {
            // v1.0.1: guard preset-driven Starts with the same GSLT warning
            // the main Start button fires — operator may load a preset they
            // saved months ago and click straight through without realising
            // the config no longer has a GSLT.
            withGsltGuard(async () => {
              try {
                await api.start(preset.map, preset.mode, isWorkshop);
                toast(`Loaded "${name}" — starting server…`);
              } catch (err) { toast(err.message, 'var(--bad)'); }
            });
          }
        } catch (err) { toast(err.message, 'var(--bad)'); }
      });

      // Load only: apply settings into the Status page selectors
      card.querySelector('[data-act="load"]').addEventListener('click', async e => {
        e.stopPropagation();
        try {
          await api.loadPreset(name);
          toast(`Preset "${name}" loaded — go to Status to apply`);
        } catch (err) { toast(err.message, 'var(--bad)'); }
      });

      // Delete
      card.querySelector('[data-act="delete"]').addEventListener('click', async e => {
        e.stopPropagation();
        modal(
          'Delete preset?',
          `<p style="color:var(--text-3);font-size:.9rem">This will permanently delete "<strong>${name}</strong>".</p>`,
          async () => {
            try {
              await api.deletePreset(name);
              toast(`Preset "${name}" deleted`);
              loadPresetCards();
            } catch (err) { toast(err.message, 'var(--bad)'); }
          },
          'Delete'
        );
      });

      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div class="empty-state text-sm text-red">${e.message}</div>`;
  }
}

/** Map a cmdfilter status {auto, override, effective} → chip label + class.
 *  "off" = -disable_workshop_command_filtering will be applied (filter disabled);
 *  a trailing dot means a manual override is set (vs auto-detected). */
function _cf(status) {
  const s      = status || { auto: false, override: null, effective: false };
  const forced = s.override !== null && s.override !== undefined;
  const label  = (s.effective ? 'cmd-filter off' : 'cmd-filter on') + (forced ? ' ●' : '');
  return { cls: s.effective ? 'cf-on' : 'cf-off', label };
}

/* ── Workshop map → recommended modes ──────────────────────────────────────────
 * A map's Steam Workshop tags imply which modes it suits. We derive that from the
 * existing MODE_WORKSHOP_TAGS (mode→tags) by inverting it, but ignore GENERIC tags
 * (classic/competitive/…) that match half the modes — only DISTINCTIVE tags
 * (wingman, aim, retake, zombie/ze, jailbreak, armsrace, …) drive recommendations. */
const GENERIC_WS_TAGS = new Set(['classic', 'competitive', 'casual', 'map']);

/** Modes whose DISTINCTIVE tags intersect this map's tags. [] = generic/untagged. */
function recommendedModes(tags) {
  const t = (tags || []).map(x => x.toLowerCase());
  if (!t.length) return [];
  const mwt   = state.modeWorkshopTags || {};
  const modes = state.modes || Object.keys(mwt);
  const out   = [];
  for (const mode of modes) {
    const sig = (mwt[mode] || [])
      .map(x => x.toLowerCase())
      .filter(x => !GENERIC_WS_TAGS.has(x));
    if (sig.length && sig.some(x => t.includes(x))) out.push(mode);
  }
  return out;
}

/** Recommended modes for display — falls back to a generic label for plain comp maps. */
function recommendedModesDisplay(tags) {
  const r = recommendedModes(tags);
  if (r.length) return r;
  const t = (tags || []).map(x => x.toLowerCase());
  if (t.some(x => x === 'classic' || x === 'competitive')) return ['Competitive / Team'];
  return [];
}

/** True if a map is fine in *mode*. Only flags when the map clearly targets other
 *  modes (distinctive tags present and mode not among them). Generic/untagged → ok. */
function modeSuitsMap(mode, tags) {
  const rec = recommendedModes(tags);
  return rec.length === 0 || rec.includes(mode);
}

/** Sync the visible mode control(s) to *mode* (status-page picker and/or maps filter). */
function setModeControl(mode) {
  const ms = el('mode-select');
  if (ms) { ms.value = mode; populateMapSelect(mode); updateModeHint(mode); }
  const mf = el('maps-mode-filter');
  if (mf) mf.value = mode;
}

/** Guard a map action with a mismatch confirm. `run(modeToUse)` performs the action.
 *  Official maps have no tags → run as-is. On a mismatch the user can switch to the
 *  recommended mode (and load), load anyway in the current mode, or cancel. */
function withModeMatchGuard(mode, mapId, isWorkshop, run) {
  if (!isWorkshop) { run(mode); return; }
  const m = (state.workshopMaps || []).find(x => x.id === mapId);
  if (!m || modeSuitsMap(mode, m.tags)) { run(mode); return; }
  const rec    = recommendedModes(m.tags);
  const target = rec[0];                       // first/most-specific recommended mode
  const recStr = rec.join(', ');
  modal('Mode mismatch',
    `<p style="color:var(--text-3);font-size:.9rem">` +
    `“${esc(m.name || mapId)}” looks made for <strong>${esc(recStr)}</strong>, ` +
    `but you've selected <strong>${esc(mode)}</strong>.<br><br>` +
    `Switch to <strong>${esc(target)}</strong> and load it, or load it as-is?</p>`,
    () => { setModeControl(target); run(target); },   // primary: switch & load
    `Switch to ${esc(target)} & load`,
    { secondaryLabel: 'Load anyway', onSecondary: () => run(mode) });
}

// v1.0.1: pre-flight modal for operators about to start the server without
// a GSLT (Steam Game Server Login Token).  Without one, Valve's auth backend
// silently rejects external client handshakes — LAN clients connect fine but
// remote players fail with no error logged on either side.  Burned a full
// evening of triage before this guard existed.  The modal is informational,
// not gating — the operator can still proceed if they want LAN-only.
function withGsltGuard(run) {
  if (state.server && state.server.gslt_set) { run(); return; }
  modal('Public hosting requires a GSLT',
    `<p style="color:var(--text-3);font-size:.95rem;line-height:1.5">` +
    `Your server has no <strong>Game Server Login Token (GSLT)</strong> set. ` +
    `It will start fine and accept connections from your <strong>local network</strong>, ` +
    `but <strong>remote players will silently fail to connect</strong> — Valve's auth ` +
    `backend rejects external handshakes when <code>+sv_setsteamaccount</code> isn't passed.` +
    `<br><br>` +
    `Get a free GSLT at <a href="https://steamcommunity.com/dev/managegameservers" ` +
    `target="_blank" rel="noopener" style="color:var(--accent)">steamcommunity.com/dev/managegameservers</a> ` +
    `(App ID <code>730</code>), then paste it into the Config tab.</p>`,
    () => { navigate('config'); setTimeout(() => {
      const f = el('cfg-gslt'); if (f) { f.focus(); f.scrollIntoView({behavior:'smooth', block:'center'}); }
    }, 100); },
    'Open Config',
    { secondaryLabel: 'Start anyway (LAN only)', onSecondary: () => run() });
}

async function loadWorkshopMapsGrid(grid, mode) {
  try {
    const maps = await api.workshopMaps();
    state.workshopMaps = maps;
    if (!maps.length) {
      _renderEmptyState(grid, {
        glyph: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
        title: 'No workshop maps yet',
        desc:  'Paste a Steam Workshop map ID above and hit Download. Maps end up here.',
      });
      return;
    }
    grid.innerHTML = '';
    const isLocal = state.server.is_local;
    // Surface maps that suit the selected mode first; dim clear mismatches.
    const suited = m => modeSuitsMap(mode, m.tags);
    maps.slice()
      .sort((a, b) => (suited(b) ? 1 : 0) - (suited(a) ? 1 : 0)
                   || (a.name || a.id).localeCompare(b.name || b.id))
      .forEach(m => {
      const card = h('div', 'map-card'
        + (m.id === state.server.map ? ' active' : '')
        + (suited(m) ? '' : ' mode-dim'));
      card.dataset.name = `${(m.name || '')} ${m.id}`.toLowerCase();
      const thumbHtml = m.preview_url
        ? `<img class="map-thumb" src="${esc(m.preview_url)}" loading="lazy" onerror="this.style.display='none'">`
        : `<div class="map-thumb-placeholder">${icon('<polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"/>')}</div>`;
      // Recommended-mode badges (derived from tags) + a few raw tag chips.
      const recs = recommendedModesDisplay(m.tags);
      const modesHtml = recs.length
        ? `<div class="map-modes">${recs.slice(0, 3)
            .map(md => `<span class="mode-badge">${esc(md)}</span>`).join('')}</div>`
        : '';
      const rawTags = (m.tags || []).filter(t => !GENERIC_WS_TAGS.has(t.toLowerCase()));
      const tagsHtml = rawTags.length
        ? `<div class="map-tags">${rawTags.slice(0, 4)
            .map(t => `<span class="tag-chip">${esc(t)}</span>`).join('')}</div>`
        : '';
      card.innerHTML = `
        ${thumbHtml}
        <div class="map-info">
          <div class="map-name">${esc(m.name || m.id)}</div>
          <div class="map-id">${esc(m.id)}</div>
          ${modesHtml}
          ${tagsHtml}
          ${isLocal ? `<button class="cmdfilter-chip ${_cf(m.cmdfilter).cls}" title="Command filtering for this map — click to change. Maps with custom server-command logic need this OFF.">${_cf(m.cmdfilter).label}</button>` : ''}
        </div>`;
      card.addEventListener('click', () => {
        withModeMatchGuard(mode, m.id, true, (useMode) => {
          (async () => {
            try { await api.map(m.id, useMode, true); toast(`Loading workshop map…`); }
            catch (e) { toast(e.message, 'var(--bad)'); }
          })();
        });
      });
      // Per-map command-filter override: cycle auto → ON → OFF → auto
      const chip = card.querySelector('.cmdfilter-chip');
      if (chip) chip.addEventListener('click', async e => {
        e.stopPropagation();
        const cur  = m.cmdfilter || { auto: false, override: null };
        const next = cur.override === null || cur.override === undefined ? true
                   : cur.override === true ? false
                   : null;   // false → back to auto
        try {
          const r = await api.workshopCmdfilterOverride(m.id, next);
          m.cmdfilter = r.status;
          const v = _cf(m.cmdfilter);
          chip.className = `cmdfilter-chip ${v.cls}`;
          chip.textContent = v.label;
        } catch (err) { toast(err.message, 'var(--bad)'); }
      });
      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div class="empty-state text-sm text-red">${e.message}</div>`;
  }
}

/* ══════════════════════════════════════════════════════════════ PLUGINS TAB */
/* v0.13.2 / task #92 — Plugin Manager.
 *
 * Slice 1: read-only inventory (runtime / deployed / library).
 * Slice 2: Activate / Switch-to-vanilla actions.
 *
 * Backend safety rails enforced in /api/plugins/{activate,vanilla}:
 *   - server stopped
 *   - no workshop download in flight
 *   - veto idle or complete
 *   - csgo/ exists
 * The SPA mirrors these by disabling buttons + showing a banner, but the
 * backend is the source of truth — it 409s if the SPA's view is stale.
 *
 * Future slices add: external registry merge (#90), curated packs (#91),
 * one-click runtime bootstrap, version updates, custom-source installs.
 */

/* Shared between the page render and post-action re-render so an
 * Activate / Switch-to-vanilla doesn't tear down + rebuild the whole tab. */
async function _renderPluginsTab() {
  let data, packsData, registryData;
  try {
    // v0.15.1 slice 2 — registry fetch is best-effort.  If it fails
    // (offline, registry repo not published yet, malformed cache) we
    // still want the rest of the tab to render.  Errors get swallowed
    // here and surfaced inline in the Available card.
    [data, packsData, registryData] = await Promise.all([
      api.plugins.list(),
      api.plugins.packs(),
      api.plugins.registry().catch(e => ({ _error: e.message })),
    ]);
  } catch (e) {
    el('plugins-runtime-body').innerHTML =
      `<div class="text-sm text-red">Failed to load: ${e.message}</div>`;
    return;
  }

  const serverRunning = !!state.server.running;
  const csgoReady     = !!(data.runtime && data.runtime.csgo_dir_exists);
  // v0.14.1: server-running is no longer blocking — plugin actions route
  // through change_map's stop-deploy-restart cycle when needed.  Only csgo/
  // missing keeps buttons disabled (no install root = nothing to deploy).
  const actionsBlocked = !csgoReady;

  // ─── Packs strip ───────────────────────────────────────────────
  // v0.14.0 — curated recipes above the library.  Click → confirm modal
  // (shows what mode+map will be staged) → applyPack().  Single backend
  // transaction, so half-applied state isn't possible.
  const packsBody = el('plugins-packs-body');
  if (packsBody) {
    const packs = (packsData && packsData.packs) || [];
    packsBody.innerHTML = `
      <div class="plugins-packs-grid">
        ${packs.map(p => {
          // v0.14.0 audit fix #2: escape every interpolation that originates
          // from catalog/pack data — today operator-trusted, tomorrow
          // remote-fetched (v0.15 OblivionPluginRegistry).  Cheap to do now.
          const tags = (p.tags || []).map(t => `<span class="chip">${esc(t)}</span>`).join(' ');
          const plugins = (p.plugins && p.plugins.length)
            ? (p.plugins.map(slug => {
                const meta = (data.bundled || []).find(b => b.slug === slug) || {};
                return esc(meta.display_name || slug);
              }).join(' + '))
            : 'vanilla';
          // Active pack = the one whose mode matches current_mode.  The vanilla
          // pack matches Competitive; if multiple packs share Competitive (none
          // do today), we just light up the first.
          const isActive = p.mode === (data.current_mode || '');
          return `
            <div class="plugin-pack-card ${isActive ? 'is-active' : ''}">
              <div class="plugin-card-head">
                <span class="plugin-card-title">${esc(p.name)}</span>
                ${isActive ? '<span class="pill pill-ok">Active</span>' : ''}
              </div>
              <div class="text-sm">${esc(p.summary || '')}</div>
              <div class="text-sm text-sub">
                Mode <strong>${esc(p.mode)}</strong>${p.default_map ? ` · Map <strong>${esc(p.default_map)}</strong>` : ''}
              </div>
              <div class="text-sm text-sub">Plugins: ${plugins}</div>
              ${tags ? `<div style="margin-top:4px">${tags}</div>` : ''}
              ${isActive ? '' : `
                <button class="btn btn-accent btn-full plugins-pack-btn"
                        data-pack-id="${esc(p.id)}"
                        data-name="${esc(p.name)}"
                        data-mode="${esc(p.mode)}"
                        data-map="${esc(p.default_map || '')}"
                        style="margin-top:10px"
                        ${actionsBlocked ? 'disabled' : ''}>
                  ▶ Apply this pack
                </button>`}
            </div>`;
        }).join('')}
      </div>
    `;
  }

  // ─── Action banner ─────────────────────────────────────────────
  // v0.14.1: server-running no longer blocks actions — the banner now
  // warns instead, so operators understand a click WILL restart the
  // server.  Maps tab already supports this same live-swap flow.
  const banner = el('plugins-action-banner');
  if (banner) {
    if (!csgoReady) {
      banner.innerHTML = `
        <div class="install-banner">
          <strong>CS2 install not found.</strong>
          Set the server directory in <a href="#config">Config</a>
          before installing plugins.
        </div>`;
    } else if (serverRunning) {
      banner.innerHTML = `
        <div class="install-banner">
          <strong>Server is online.</strong>
          Applying a pack or activating a plugin will
          stop, swap plugins, and restart the server.
        </div>`;
    } else {
      banner.innerHTML = '';
    }
  }

  // ─── Runtime card ──────────────────────────────────────────────
  // v0.14.0 slice 5: use the proper install detectors (metamod_installed +
  // css_installed) so an empty addons/counterstrikesharp/ folder doesn't
  // claim "installed".  Show a Bootstrap button when either is missing.
  const rt = data.runtime || {};
  const mmInstalled = !!rt.metamod_installed;
  const cssInstalled = !!rt.css_installed;
  const runtimeIncomplete = rt.csgo_dir_exists && (!mmInstalled || !cssInstalled);

  const mmIcon =
    rt.metamod_patched === true  ? '✓' :
    rt.metamod_patched === false ? '✗' :
                                    '?';
  const mmCls =
    rt.metamod_patched === true  ? 'ok'  :
    rt.metamod_patched === false ? 'off' :
                                    'unk';
  const csgoIcon = rt.csgo_dir_exists ? '✓' : '✗';
  const csgoCls  = rt.csgo_dir_exists ? 'ok' : 'off';

  el('plugins-runtime-body').innerHTML = `
    <div class="plugins-runtime-grid">
      <div class="plugins-runtime-row">
        <span class="plugins-runtime-dot ${csgoCls}">${csgoIcon}</span>
        <span class="plugins-runtime-label">CS2 server installed</span>
        <span class="plugins-runtime-value">${rt.csgo_dir ? esc(rt.csgo_dir) : '<em>not configured</em>'}</span>
      </div>
      <div class="plugins-runtime-row">
        <span class="plugins-runtime-dot ${mmInstalled ? 'ok' : 'off'}">${mmInstalled ? '✓' : '✗'}</span>
        <span class="plugins-runtime-label">MetaMod base installed</span>
        <span class="plugins-runtime-value">${
          mmInstalled ? 'addons/metamod/ present with loader files'
                      : 'MISSING — required for every plugin (auto-loads at server boot)'
        }</span>
      </div>
      <div class="plugins-runtime-row">
        <span class="plugins-runtime-dot ${cssInstalled ? 'ok' : 'off'}">${cssInstalled ? '✓' : '✗'}</span>
        <span class="plugins-runtime-label">CounterStrikeSharp installed</span>
        <span class="plugins-runtime-value">${
          cssInstalled ? 'addons/counterstrikesharp/{api,bin}/ present'
                       : 'MISSING — required for CSS plugins (Warcraft, MatchZy, Retakes…)'
        }</span>
      </div>
      <div class="plugins-runtime-row">
        <span class="plugins-runtime-dot ${mmCls}">${mmIcon}</span>
        <span class="plugins-runtime-label">MetaMod loaded by engine</span>
        <span class="plugins-runtime-value">${
          rt.metamod_patched === true  ? 'gameinfo.gi patched — engine will load MetaMod at boot' :
          rt.metamod_patched === false ? 'gameinfo.gi unpatched — auto-applied on next plugin deploy' :
                                          'unknown (gameinfo.gi unreadable)'
        }</span>
      </div>
    </div>
    ${runtimeIncomplete ? `
      <div style="margin-top:14px; padding-top:14px; border-top:1px solid var(--line-1)">
        <button class="btn" id="plugins-bootstrap-btn">
          🔧 Set up plugin runtime
        </button>
        <span class="text-sm text-sub" style="margin-left:10px">
          One-time setup — opens the install guide for MetaMod + CounterStrikeSharp
        </span>
      </div>` : ''}
  `;
  const bootBtn = el('plugins-bootstrap-btn');
  if (bootBtn) bootBtn.addEventListener('click', () => _pluginBootstrap(rt));

  // ─── Deployed card ─────────────────────────────────────────────
  const m = data.manifest || {};
  const vanillaBtn = (m.plugins && m.plugins.length)
    ? `<button class="btn btn-sm" id="plugins-vanilla-btn"
               ${actionsBlocked ? 'disabled' : ''}
               title="Switch to a vanilla Competitive server (no managed plugins)">
         Switch to vanilla
       </button>`
    : '';

  if (!m.plugins || m.plugins.length === 0) {
    el('plugins-deployed-body').innerHTML = `
      <div class="text-sm text-sub">
        Nothing deployed — current mode is
        <strong>${esc(data.current_mode || '(none)')}</strong>, which runs vanilla.
      </div>
    `;
  } else {
    const chips = m.plugins.map(slug => {
      const meta = (data.bundled || []).find(b => b.slug === slug) || {};
      return `<span class="chip">${esc(meta.display_name || slug)}</span>`;
    }).join(' ');
    el('plugins-deployed-body').innerHTML = `
      <div class="section-hdr">
        <div class="text-sm">
          Mode <strong>${esc(m.mode || '?')}</strong> deployed: ${chips}
        </div>
        ${vanillaBtn}
      </div>
      <div class="text-sm text-sub" style="margin-top:6px">
        Deployed at ${esc(m.deployed_at || '—')}
      </div>
    `;
  }

  // ─── Library grid ──────────────────────────────────────────────
  const items = (data.bundled || []).map(p => {
    // v0.15.0 slice 1 — Local plugins get a different badge so operators
    // can spot what they've added themselves vs what shipped with the .exe.
    // v0.15.2 slice 3 — Update Available pill when the registry advertises
    // a newer version than what's installed locally.
    const isLocal = p.source === 'local';
    const hasUpdate = !!p.update_available;
    const stateLabel = p.deployed
      ? '<span class="pill pill-ok">Active</span>'
      : (p.source_present
          ? (isLocal ? '<span class="pill pill-blue">Local</span>'
                     : '<span class="pill pill-mute">Available</span>')
          : '<span class="pill pill-warn">Source missing</span>');
    const updatePill = hasUpdate
      ? `<span class="pill pill-warn" title="Registry has v${esc(p.latest_version)}, you have v${esc(p.installed_version || '?')}">Update v${esc(p.latest_version)}</span>`
      : '';
    const versionLine = p.installed_version
      ? `<div class="text-sm text-sub">Installed: v${esc(p.installed_version)}</div>`
      : '';
    const modesTxt = p.modes && p.modes.length
      ? `<div class="text-sm text-sub">Used by: ${p.modes.map(esc).join(', ')}</div>`
      : '<div class="text-sm text-sub">Standalone — not bound to a mode</div>';

    // Action: only show on cards that aren't already active AND have source.
    // Single-mode plugins get a plain "Activate"; multi-mode get a dropdown
    // so the operator's intent is explicit (MatchZy on Practice vs 5v5 are
    // different match flows even though the plugin files are the same).
    let actionBtn = '';
    if (!p.deployed && p.source_present && p.modes && p.modes.length > 0) {
      const disAttr = actionsBlocked ? 'disabled' : '';
      if (p.modes.length === 1) {
        actionBtn = `
          <button class="btn btn-sm plugins-activate-btn"
                  data-slug="${esc(p.slug)}" data-mode="${esc(p.modes[0])}"
                  ${disAttr}>
            Activate (${esc(p.modes[0])})
          </button>`;
      } else {
        const opts = p.modes.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
        actionBtn = `
          <div class="plugin-card-actions">
            <select class="select plugins-activate-mode" data-slug="${esc(p.slug)}" ${disAttr}>
              ${opts}
            </select>
            <button class="btn btn-sm plugins-activate-btn"
                    data-slug="${esc(p.slug)}" data-mode=""
                    ${disAttr}>
              Activate
            </button>
          </div>`;
      }
    }

    // v0.15.2 slice 3 — Remove button on Local cards.  Bundled plugins
    // can't be removed (they live inside the .exe); they show no button.
    // The Remove confirm warns about deletion of operator-edited files.
    const removeBtn = isLocal && p.source_present
      ? `<button class="btn btn-sm btn-ghost plugins-uninstall-btn"
                 data-slug="${esc(p.slug)}"
                 data-name="${esc(p.display_name)}"
                 ${actionsBlocked ? 'disabled' : ''}
                 title="Delete %APPDATA%/.../plugins/${esc(p.slug)}/ — bundled plugins cannot be removed">
           Remove
         </button>`
      : '';
    return `
      <div class="plugin-card" data-slug="${esc(p.slug)}">
        <div class="plugin-card-head">
          <span class="plugin-card-title">${esc(p.display_name)}</span>
          <div style="display:flex; gap:6px; flex-wrap:wrap">
            ${updatePill}
            ${stateLabel}
          </div>
        </div>
        <div class="text-sm">${esc(p.summary || '')}</div>
        ${modesTxt}
        ${versionLine}
        ${p.author ? `<div class="text-sm text-sub">By ${esc(p.author)}</div>` : ''}
        <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:6px">
          ${actionBtn}
          ${removeBtn}
        </div>
      </div>
    `;
  }).join('');

  el('plugins-library-body').innerHTML = `
    <div class="plugins-grid">${items}</div>
  `;

  // ─── Registry section ──────────────────────────────────────────
  // v0.15.1 slice 2 — Community plugin registry.  Shows entries from
  // OblivionPluginRegistry that aren't already installed locally.
  // Click Install → backend downloads + sha256-verifies + extracts.
  const regBody = el('plugins-registry-body');
  if (regBody) {
    if (registryData && registryData._error) {
      regBody.innerHTML = `
        <div class="text-sm text-sub">
          Registry currently unreachable: ${esc(registryData._error)}
          — the locally bundled and installed plugins still work.
        </div>`;
    } else {
      const cat       = (registryData && registryData.catalog) || {};
      const status    = (registryData && registryData.status) || {};
      const installed = new Set((registryData && registryData.installed_slugs) || []);
      // v0.15.2 slice 3 — also surface entries that ARE installed but have
      // a newer registry version (update_available=true on the /api/plugins
      // entry).  The button copy becomes "Update" instead of "Install".
      const updatableSlugs = new Set(
        (data.bundled || []).filter(b => b.update_available).map(b => b.slug)
      );
      const available = (cat.plugins || []).filter(p =>
        !installed.has(p.slug) || updatableSlugs.has(p.slug)
      );
      const ageHours  = status.fetched_at
        ? Math.max(0, Math.round((Date.now() / 1000 - status.fetched_at) / 3600))
        : null;
      const ageLabel = ageHours == null
        ? '<span class="text-sub">never fetched</span>'
        : (status.stale
            ? `<span class="text-sub">refreshed ${ageHours}h ago (stale)</span>`
            : `<span class="text-sub">refreshed ${ageHours}h ago</span>`);
      if (cat._offline) {
        regBody.innerHTML = `
          <div class="text-sm text-sub">
            Registry is offline. The catalog repo isn't published yet, or
            this machine has no internet right now.
            <br>${ageLabel}
          </div>`;
      } else if (!available.length) {
        regBody.innerHTML = `
          <div class="text-sm text-sub">
            ${(cat.plugins || []).length === 0
                ? 'Registry is empty — no community plugins published yet.'
                : 'Everything in the registry is already installed locally.'}
            <br>${ageLabel}
          </div>`;
      } else {
        const items = available.map(p => {
          const v0       = (p.versions || [])[0] || {};
          const isUpdate = updatableSlugs.has(p.slug);
          const versionPill = isUpdate
            ? `<span class="pill pill-warn">Update → v${esc(v0.version || '?')}</span>`
            : `<span class="pill pill-mute">v${esc(v0.version || '?')}</span>`;
          return `
            <div class="plugin-card" data-slug="${esc(p.slug)}">
              <div class="plugin-card-head">
                <span class="plugin-card-title">${esc(p.display_name || p.slug)}</span>
                ${versionPill}
              </div>
              <div class="text-sm">${esc(p.summary || '')}</div>
              ${p.modes && p.modes.length
                  ? `<div class="text-sm text-sub">Used by: ${p.modes.map(esc).join(', ')}</div>`
                  : ''}
              ${p.author ? `<div class="text-sm text-sub">By ${esc(p.author)}</div>` : ''}
              <button class="btn btn-sm plugins-install-btn"
                      data-slug="${esc(p.slug)}"
                      data-name="${esc(p.display_name || p.slug)}"
                      data-version="${esc(v0.version || '')}"
                      data-action="${isUpdate ? 'update' : 'install'}"
                      ${actionsBlocked ? 'disabled' : ''}>
                ${isUpdate ? 'Update' : 'Install'}
              </button>
            </div>`;
        }).join('');
        regBody.innerHTML = `
          <div class="text-sm text-sub" style="margin-bottom:10px">${ageLabel}</div>
          <div class="plugins-grid">${items}</div>`;
      }
    }
  }

  // ─── Templates strip (v0.16.3 / task #169) ───────────────────────────
  // Fetched lazily — failure is non-fatal (registry-style empty state).
  const tplBody = el('plugins-templates-body');
  if (tplBody) {
    let tplResp;
    try { tplResp = await api.plugins.templates ? null : null; } catch (_) {}
    try { tplResp = await api.templates.list(); }
    catch (_) { tplResp = { templates: [] }; }
    const tpls = (tplResp && tplResp.templates) || [];
    if (!tpls.length) {
      tplBody.innerHTML = `
        <div class="text-sm text-sub">
          No templates yet. Click <strong>📋 Manage Templates</strong> to save a
          tournament format (mode + pack + Discord channels) under a name.
        </div>`;
    } else {
      tplBody.innerHTML = `
        <div class="plugins-grid">
          ${tpls.map(t => {
            const p = t.payload || {};
            const bits = [];
            if (p.mode)    bits.push(`Mode: ${esc(p.mode)}`);
            if (p.map)     bits.push(`Map: ${esc(p.map)}`);
            if (p.pack_id) bits.push(`Pack: ${esc(p.pack_id)}`);
            return `
              <div class="plugin-card" data-id="${esc(t.id)}">
                <div class="plugin-card-head">
                  <span class="plugin-card-title">${esc(t.name)}</span>
                  <span class="pill pill-mute">${tpls.length === 1 ? 'template' : 'tpl'}</span>
                </div>
                <div class="text-sm text-sub">${bits.join(' · ') || '(empty payload)'}</div>
                <button class="btn btn-sm plugins-tpl-apply"
                        data-id="${esc(t.id)}" data-name="${esc(t.name)}"
                        ${actionsBlocked ? 'disabled' : ''}
                        style="margin-top:6px">
                  Apply
                </button>
              </div>`;
          }).join('')}
        </div>`;
    }
  }

  // ─── Wire button handlers (delegated on the freshly-rendered DOM) ──
  document.querySelectorAll('.plugins-activate-btn').forEach(btn => {
    btn.addEventListener('click', () => _pluginActivate(btn));
  });
  document.querySelectorAll('.plugins-pack-btn').forEach(btn => {
    btn.addEventListener('click', () => _pluginApplyPack(btn));
  });
  document.querySelectorAll('.plugins-tpl-apply').forEach(btn => {
    btn.addEventListener('click', () => _pluginApplyTemplate(btn));
  });
  const tplManageBtn = el('plugins-templates-manage');
  if (tplManageBtn) tplManageBtn.addEventListener('click', _openTemplatesModal);
  document.querySelectorAll('.plugins-install-btn').forEach(btn => {
    btn.addEventListener('click', () => _pluginInstallFromRegistry(btn));
  });
  // v0.15.2 slice 3 — Remove button on Local-source library cards.
  document.querySelectorAll('.plugins-uninstall-btn').forEach(btn => {
    btn.addEventListener('click', () => _pluginUninstall(btn));
  });
  const vBtn = el('plugins-vanilla-btn');
  if (vBtn) vBtn.addEventListener('click', _pluginVanilla);
  const refreshBtn = el('plugins-registry-refresh');
  if (refreshBtn) refreshBtn.addEventListener('click', _pluginsRegistryRefresh);
  // Re-apply the search filter (page may have re-rendered after an action).
  if (typeof _applyPluginsSearchFilter === 'function') _applyPluginsSearchFilter();
}

async function _pluginUninstall(btn) {
  const slug = btn.dataset.slug;
  const name = btn.dataset.name || slug;
  if (!window.confirm(
    `Remove ${name}?\n\n`
    + `This deletes the folder %APPDATA%\\Oblivion Server Tool\\plugins\\${slug}\\ `
    + `from disk — including any operator edits to its files.\n\n`
    + `If the plugin is currently the active mode's plugin, the backend will refuse — `
    + `switch to a different mode first.\n\n`
    + `Proceed?`
  )) return;
  const allButtons = document.querySelectorAll(
    '#plugins-library-body button, #plugins-vanilla-btn, .plugins-pack-btn, '
    + '#plugins-registry-body button'
  );
  allButtons.forEach(b => b.disabled = true);
  toast(`Removing ${name}…`);
  try {
    const r = await api.plugins.uninstall(slug);
    toast(`✓ ${name} removed`, 'var(--ok)');
  } catch (e) {
    toast(`Remove failed: ${e.message}`, 'var(--bad)');
  } finally {
    await _renderPluginsTab();
  }
}

async function _pluginsRegistryRefresh() {
  const btn = el('plugins-registry-refresh');
  if (btn) { btn.disabled = true; btn.textContent = 'Refreshing…'; }
  try {
    await api.plugins.registryRefresh();
    toast('Registry refreshed');
  } catch (e) {
    toast(`Refresh failed: ${e.message}`, 'var(--bad)');
  } finally {
    await _renderPluginsTab();
  }
}

async function _pluginInstallFromRegistry(btn) {
  const slug    = btn.dataset.slug;
  const name    = btn.dataset.name;
  const version = btn.dataset.version || null;
  // v0.15.2 audit fix — the same handler serves Install and Update.
  // Confirm copy must match the action the operator actually clicked,
  // including the fact that Update OVERWRITES the existing folder.
  const isUpdate = btn.dataset.action === 'update';
  const confirmMsg = isUpdate
    ? `Update ${name} to v${version || '?'}?\n\n`
      + `This downloads the new version from the OblivionPluginRegistry and `
      + `OVERWRITES %APPDATA%\\Oblivion Server Tool\\plugins\\${slug}\\, `
      + `including any operator edits to that folder.\n\n`
      + `Your active mode binding is preserved — if the plugin is currently `
      + `deployed, restart the server (or hot-reload via mode switch) to pick `
      + `up the new files.`
    : `Install ${name}?\n\n`
      + `This downloads the plugin from the OblivionPluginRegistry and `
      + `extracts it into %APPDATA%\\Oblivion Server Tool\\plugins\\${slug}\\.\n\n`
      + `It will appear in the Library after install — click Activate to deploy it.`;
  if (!window.confirm(confirmMsg)) return;
  const allButtons = document.querySelectorAll(
    '#plugins-registry-body button, #plugins-library-body button, #plugins-vanilla-btn'
  );
  allButtons.forEach(b => b.disabled = true);
  toast(isUpdate ? `Updating ${name}…` : `Installing ${name}…`);
  try {
    const r = await api.plugins.installFromRegistry(slug, version);
    toast(isUpdate
            ? `✓ ${name} updated to v${r.result.version} (${r.result.files_written} files)`
            : `✓ ${name} v${r.result.version} installed (${r.result.files_written} files)`,
          'var(--ok)');
  } catch (e) {
    toast(`${isUpdate ? 'Update' : 'Install'} failed: ${e.message}`, 'var(--bad)');
  } finally {
    await _renderPluginsTab();
  }
}

/* v0.14.0 slice 5 — guided runtime bootstrap.
 *
 * Why this isn't an auto-downloader yet:
 *   - sourcemm.net's MetaMod URL changes per version (gitNNNN suffix)
 *   - CSS GitHub releases gate downloads behind unique asset names per tag
 *   - Both upstreams have shifted layout twice in the past 18 months.
 *
 * A manual-with-explicit-links flow is far more resilient — when an
 * upstream changes, the operator sees the new layout immediately instead
 * of getting a cryptic 404 from an auto-downloader.  Once both upstreams
 * have settled, v0.15 can add an auto path WITH this dialog as fallback.
 *
 * The dialog uses a full-screen overlay rather than window.alert/confirm
 * because the install instructions are several paragraphs of formatted text.
 */
function _pluginBootstrap(rt) {
  // v0.14.0 audit fix #2: csgo path is interpolated into <code> tags below;
  // escape before use so a path with HTML special chars can't break out of
  // the code element.  Same defense applies to the placeholder fallback.
  const csgoDir = esc(rt.csgo_dir || '<your csgo folder>');
  const mmNeeded  = !rt.metamod_installed;
  const cssNeeded = !rt.css_installed;

  const overlay = document.createElement('div');
  overlay.className = 'plugins-bootstrap-overlay';
  overlay.innerHTML = `
    <div class="plugins-bootstrap-modal">
      <div class="section-hdr">
        <span class="card-title" style="margin-bottom:0">Plugin Runtime Setup</span>
        <button class="btn btn-sm" id="plugins-bootstrap-close">Close</button>
      </div>

      <p class="text-sm">
        Plugins need two pieces of software installed into your CS2 server's
        <code>csgo/</code> folder before any plugin can load.
        Oblivion can install both for you — one click each.
      </p>

      ${mmNeeded ? `
        <div class="plugins-bootstrap-step" id="bs-mm-step">
          <h4>1. Install MetaMod — Source 2</h4>
          <p class="text-sm">
            MetaMod is the loader the engine reads to find plugins.
            Every plugin depends on it.
          </p>
          <button class="btn btn-accent" id="bs-mm-install">
            📥 Install MetaMod (auto-download ~5 MB)
          </button>
          <div id="bs-mm-status" class="text-sm" style="margin-top:8px;display:none"></div>
          <details style="margin-top:10px">
            <summary class="text-sm" style="cursor:pointer;color:var(--sub)">
              Download fails? Show manual install instructions
            </summary>
            <p class="text-sm" style="margin-top:6px">
              <a href="https://www.sourcemm.net/downloads.php?branch=master"
                 target="_blank" rel="noopener noreferrer"
                 class="plugins-bootstrap-link">
                ↗ Open MetaMod download page
              </a>
            </p>
            <ol class="text-sm">
              <li>Download the latest <strong>Windows</strong> zip
                  (e.g. <code>mmsource-2.0.0-git&lt;NNNN&gt;-windows.zip</code>).</li>
              <li>Open the zip — inside you'll see an <code>addons/</code> folder.</li>
              <li>Extract the <code>addons/</code> folder directly into
                  <code>${csgoDir}</code>.</li>
              <li>You should end up with
                  <code>${csgoDir}\\addons\\metamod\\bin\\win64\\metamod.2.cs2.dll</code>.</li>
            </ol>
          </details>
        </div>` : `
        <div class="plugins-bootstrap-step plugins-bootstrap-done">
          <h4>✓ MetaMod already installed</h4>
        </div>`}

      ${cssNeeded ? `
        <div class="plugins-bootstrap-step" id="bs-css-step">
          <h4>${mmNeeded ? '2.' : '1.'} Install CounterStrikeSharp</h4>
          <p class="text-sm">
            CounterStrikeSharp (CSS) is the C# host that runs most modern CS2
            plugins — MatchZy, Warcraft, Retakes, etc. all depend on it.
          </p>
          <button class="btn btn-accent" id="bs-css-install">
            📥 Install CounterStrikeSharp (auto-download ~150 MB)
          </button>
          <p class="text-sm" style="margin-top:4px;color:var(--sub)">
            Larger download — bundles its own .NET runtime so you don't need a
            separate .NET install.  Takes 30-90s on a fast connection.
          </p>
          <div id="bs-css-status" class="text-sm" style="margin-top:8px;display:none"></div>
          <details style="margin-top:10px">
            <summary class="text-sm" style="cursor:pointer;color:var(--sub)">
              Download fails? Show manual install instructions
            </summary>
            <p class="text-sm" style="margin-top:6px">
              <a href="https://github.com/roflmuffin/CounterStrikeSharp/releases/latest"
                 target="_blank" rel="noopener noreferrer"
                 class="plugins-bootstrap-link">
                ↗ Open CounterStrikeSharp releases
              </a>
            </p>
            <ol class="text-sm">
              <li>Download the <strong>with-runtime</strong> Windows zip
                  (e.g. <code>counterstrikesharp-with-runtime-build-NNN-windows.zip</code>).</li>
              <li>Open the zip — you'll see an <code>addons/</code> folder.</li>
              <li>Extract the <code>addons/</code> folder directly into
                  <code>${csgoDir}</code>.</li>
              <li>You should end up with
                  <code>${csgoDir}\\addons\\counterstrikesharp\\api\\</code>
                  AND <code>...\\counterstrikesharp\\bin\\</code>.</li>
            </ol>
          </details>
        </div>` : `
        <div class="plugins-bootstrap-step plugins-bootstrap-done">
          <h4>✓ CounterStrikeSharp already installed</h4>
        </div>`}

      <div class="plugins-bootstrap-step">
        <h4>Done? Verify install state</h4>
        <p class="text-sm">
          Click below to re-check.  When both runtimes are green, you can
          install MatchZy / Warcraft / Retakes etc. from the Library or
          Quick-Apply Packs.
        </p>
        <button class="btn" id="plugins-bootstrap-verify">
          ✓ Re-check install state
        </button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const close = () => overlay.remove();
  overlay.addEventListener('click', (e) => {
    // Click on the overlay backdrop (not the modal itself) closes.
    if (e.target === overlay) close();
  });
  el('plugins-bootstrap-close').addEventListener('click', close);

  // v0.16.5 / task #163: auto-install handler.  Shared between MM + CSS.
  // Tracks button state across the install lifecycle so the operator
  // always sees progress (download → extract → re-check → green pill).
  // v0.16.8 (review fix #4) — re-entry guard via module-level Set.  A
  // rapid second click was reaching this handler before btn.disabled
  // took effect; the second call now bails immediately.  Backend also
  // has a threading.Lock as defense-in-depth.
  if (!window._oblivionRuntimeInflight) window._oblivionRuntimeInflight = new Set();
  async function _runtimeInstall(component, btnId, statusId, origBtnText, sizeNote) {
    const btn    = el(btnId);
    const status = el(statusId);
    if (!btn || !status) return;
    if (window._oblivionRuntimeInflight.has(component)) {
      // Visible feedback so the friend understands why the click did nothing.
      toast(`Already installing ${component} — please wait`, 'var(--warn)');
      return;
    }
    window._oblivionRuntimeInflight.add(component);
    btn.disabled = true;
    btn.textContent = `Downloading ${sizeNote}…`;
    status.style.display = 'block';
    status.style.color = 'var(--sub)';
    status.textContent = 'Fetching the zip — this is the slow part.  Hold tight.';
    try {
      const r = await api.plugins.installRuntime(component);
      // Backend already extracted + patched gameinfo.gi for MetaMod.
      status.style.color = 'var(--ok)';
      const after  = r.runtime_after || {};
      const flagOk = component === 'metamod' ? after.metamod_installed : after.css_installed;
      if (flagOk) {
        const fileCount = r.result?.files_written ?? '?';
        status.textContent = `✓ Installed — ${fileCount} files written to ${esc(r.result?.dest_dir || 'csgo/addons')}`;
        btn.textContent = '✓ Installed';
        // Keep the button disabled — they don't need to click again.
        toast(`✓ ${component === 'metamod' ? 'MetaMod' : 'CounterStrikeSharp'} installed`, 'var(--ok)');
      } else {
        status.style.color = 'var(--warn)';
        status.textContent = '⚠ Download finished but verify check still says missing. ' +
                             'Open csgo/addons/ in Explorer to inspect, or use manual install below.';
        btn.disabled = false;
        btn.textContent = origBtnText;
      }
    } catch (e) {
      status.style.color = 'var(--bad)';
      status.textContent = `❌ ${e.message || e}`;
      btn.disabled = false;
      btn.textContent = origBtnText;
      toast(`Runtime install failed — see manual install fallback`, 'var(--bad)');
    } finally {
      // v0.16.8 (review fix #4) — release the in-flight flag so the
      // operator can retry after a failure (or after a successful install
      // of the *other* runtime that didn't auto-disable this button).
      window._oblivionRuntimeInflight.delete(component);
    }
  }

  if (mmNeeded) {
    const mmBtn = el('bs-mm-install');
    if (mmBtn) mmBtn.addEventListener('click', () =>
      _runtimeInstall('metamod', 'bs-mm-install', 'bs-mm-status',
                       '📥 Install MetaMod (auto-download ~5 MB)', '~5 MB'));
  }
  if (cssNeeded) {
    const cssBtn = el('bs-css-install');
    if (cssBtn) cssBtn.addEventListener('click', () =>
      _runtimeInstall('css', 'bs-css-install', 'bs-css-status',
                       '📥 Install CounterStrikeSharp (auto-download ~150 MB)',
                       '~150 MB'));
  }

  el('plugins-bootstrap-verify').addEventListener('click', async () => {
    close();
    toast('Re-checking install state…');
    await _renderPluginsTab();
    // Read the fresh state to give a useful confirmation toast.
    try {
      const fresh = await api.plugins.list();
      const r2 = fresh.runtime || {};
      if (r2.metamod_installed && r2.css_installed) {
        toast('✓ Runtime fully installed — ready for plugins', 'var(--ok)');
      } else {
        const missing = [];
        if (!r2.metamod_installed) missing.push('MetaMod');
        if (!r2.css_installed)     missing.push('CounterStrikeSharp');
        toast(`Still missing: ${missing.join(', ')}`, 'var(--warn)');
      }
    } catch (e) { /* re-render already toasted on failure */ }
  });
}

/* v0.16.3 / task #169 — Apply a tournament template.  Routes through
 * /api/templates/apply which: backs up config, persists Discord fields,
 * then runs the same plugin-pack apply path as /api/plugins/apply_pack.
 * Server-running case auto-restarts (mirrors the pack flow). */
async function _pluginApplyTemplate(btn) {
  const id   = btn.dataset.id;
  const name = btn.dataset.name || id;
  const running = !!state.server.running;
  if (!window.confirm(
    `Apply template "${name}"?\n\n`
    + `Discord channel + behaviour settings will be saved into your config.\n`
    + `The plugin pack will deploy${running ? ' (and the server will STOP + RESTART)' : ''}.\n\n`
    + `A pre-action config backup is taken so this is reversible via Tools → Restore.`
  )) return;
  const all = document.querySelectorAll(
    '#plugins-templates-body button, .plugins-pack-btn, .plugins-tpl-apply'
  );
  all.forEach(b => b.disabled = true);
  toast(running ? `Restarting into ${name}…` : `Applying ${name}…`);
  try {
    const r = await api.templates.apply(id);
    toast(`✓ ${name} applied — ${(r.applied || []).join(', ') || 'no-op'}`, 'var(--ok)');
  } catch (e) {
    toast(`Apply failed: ${e.message}`, 'var(--bad)');
  } finally {
    await _renderPluginsTab();
  }
}

/* v0.16.3 / task #169 — Templates manager modal.  Lists saved templates
 * with Apply/Delete buttons, and a "save current setup" form pre-filled
 * from AppCore's current Discord channel IDs + current_mode/current_map +
 * a manual pack picker. */
async function _openTemplatesModal() {
  let tplResp, packsResp, teamsResp;
  try {
    [tplResp, packsResp, teamsResp] = await Promise.all([
      api.templates.list(),
      api.plugins.packs(),
      api.teams.list().catch(() => ({ teams: [] })),
    ]);
  } catch (e) {
    toast(`Templates load failed: ${e.message}`, 'var(--bad)');
    return;
  }
  const tpls   = tplResp.templates || [];
  const packs  = packsResp.packs   || [];
  const teams  = teamsResp.teams   || [];

  const overlay = document.createElement('div');
  overlay.className = 'plugins-bootstrap-overlay';

  const render = () => {
    const list = tpls.length ? tpls.map(t => {
      const p = t.payload || {};
      const bits = [];
      if (p.mode)     bits.push(`Mode: <strong>${esc(p.mode)}</strong>`);
      if (p.map)      bits.push(`Map: <strong>${esc(p.map)}</strong>`);
      if (p.pack_id)  bits.push(`Pack: <strong>${esc(p.pack_id)}</strong>`);
      if (p.discord_veto_channel_id) bits.push(`Veto ch: ${esc(p.discord_veto_channel_id)}`);
      return `
        <div class="plugin-card" style="margin-bottom:8px">
          <div class="plugin-card-head">
            <span class="plugin-card-title">${esc(t.name)}</span>
            <span class="pill pill-mute">${t.id.slice(0,8)}</span>
          </div>
          <div class="text-sm">${bits.join(' · ') || '(empty)'}</div>
          ${p.description ? `<div class="text-sm text-sub">${esc(p.description)}</div>` : ''}
          <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px">
            <button class="btn btn-accent btn-sm tpl-modal-apply" data-id="${esc(t.id)}" data-name="${esc(t.name)}">▶ Apply</button>
            <button class="btn btn-sm btn-ghost tpl-modal-delete" data-id="${esc(t.id)}" data-name="${esc(t.name)}">Delete</button>
          </div>
        </div>`;
    }).join('') : '<div class="text-sm text-sub">No templates yet — fill the form below.</div>';

    return `
      <div class="plugins-bootstrap-modal" style="max-width:680px">
        <div class="section-hdr">
          <span class="card-title" style="margin-bottom:0">Tournament Templates</span>
          <button class="btn btn-sm" id="tpl-close">Close</button>
        </div>
        <p class="text-sm text-sub" style="margin-top:8px">
          Stored in <code>%APPDATA%\\Oblivion Server Tool\\oblivion_templates.json</code>.
          Apply runs <em>config backup</em> → <em>Discord fields save</em> →
          <em>plugin pack deploy</em> as one batch.
        </p>

        <div class="cfg-card-title" style="margin-top:18px">Saved templates (${tpls.length})</div>
        <div id="tpl-list">${list}</div>

        <hr class="cfg-card-divider">
        <div class="cfg-card-title">Save current setup as a template</div>
        <div class="field" style="margin-top:8px">
          <label>Template name</label>
          <input class="input" id="tpl-save-name" type="text" maxlength="40"
                 placeholder="e.g. Friday Night Pugs">
        </div>
        <div class="field">
          <label>Description <span class="text-sub">(optional)</span></label>
          <input class="input" id="tpl-save-desc" type="text" maxlength="120"
                 placeholder="What this template is for">
        </div>
        <div class="field">
          <label>Plugin pack</label>
          <select class="select" id="tpl-save-pack">
            <option value="">(none — mode only)</option>
            ${packs.map(p => `<option value="${esc(p.id)}">${esc(p.name)} → ${esc(p.mode)}${p.default_map ? ' · ' + esc(p.default_map) : ''}</option>`).join('')}
          </select>
        </div>
        <div class="field">
          <label>Map override <span class="text-sub">(optional — uses pack default if blank)</span></label>
          <input class="input" id="tpl-save-map" type="text" maxlength="40"
                 placeholder="e.g. de_dust2">
        </div>
        <div class="text-sm text-sub" style="margin-top:10px">
          Discord channel IDs + auto-move + round-summaries toggles are read
          from your <em>current</em> Config when you click Save — set them in
          Config → Discord first.
        </div>
        <div class="flex gap-8" style="margin-top:12px">
          <button class="btn btn-accent flex-1" id="tpl-save-btn">Save template</button>
        </div>
      </div>`;
  };
  overlay.innerHTML = render();
  document.body.appendChild(overlay);

  const close = () => overlay.remove();
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

  const rewire = () => {
    el('tpl-close').addEventListener('click', close);
    overlay.querySelectorAll('.tpl-modal-apply').forEach(btn => {
      btn.addEventListener('click', async () => {
        close();
        await _pluginApplyTemplate(btn);
      });
    });
    overlay.querySelectorAll('.tpl-modal-delete').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!window.confirm(`Delete template "${btn.dataset.name}"?`)) return;
        try {
          await api.templates.delete(btn.dataset.id);
          toast('Template deleted');
          close();
          await _openTemplatesModal();
        } catch (e) {
          toast(`Delete failed: ${e.message}`, 'var(--bad)');
        }
      });
    });
    el('tpl-save-btn').addEventListener('click', async () => {
      const name = el('tpl-save-name').value.trim();
      const desc = el('tpl-save-desc').value.trim();
      const pack = el('tpl-save-pack').value;
      const map  = el('tpl-save-map').value.trim();
      if (!name) { toast('Template name is required', 'var(--bad)'); return; }
      // Pull current Discord fields off /api/config so the persisted
      // payload reflects whatever's in Config right now.
      let cfg;
      try { cfg = await api.config(); }
      catch (e) { toast(`Config read failed: ${e.message}`, 'var(--bad)'); return; }
      const pickedPack = packs.find(p => p.id === pack);
      const payload = {
        description: desc,
        mode: pickedPack ? pickedPack.mode : '',
        map:  map,
        pack_id: pack || '',
        discord_veto_channel_id:           cfg.discord_veto_channel_id || '',
        discord_team_a_voice_channel_id:   cfg.discord_team_a_voice_channel_id || '',
        discord_team_b_voice_channel_id:   cfg.discord_team_b_voice_channel_id || '',
        discord_auto_move_on_distribute_enabled: !!cfg.discord_auto_move_on_distribute_enabled,
        discord_round_summaries_enabled:         !!cfg.discord_round_summaries_enabled,
      };
      try {
        await api.templates.save({ name, payload });
        toast(`✓ Saved ${name}`, 'var(--ok)');
        close();
        await _openTemplatesModal();
        await _renderPluginsTab();
      } catch (e) {
        toast(`Save failed: ${e.message}`, 'var(--bad)');
      }
    });
  };
  rewire();
}

async function _pluginApplyPack(btn) {
  const packId = btn.dataset.packId;
  const name   = btn.dataset.name;
  const mode   = btn.dataset.mode;
  const map    = btn.dataset.map;
  const running = !!state.server.running;
  const msg = `Apply pack: ${name}?\n\n`
            + `This will:\n`
            + `  • Switch to mode ${mode}\n`
            + (map ? `  • Load map ${map}\n` : '')
            + `  • Deploy the pack's plugins (or undeploy for vanilla)\n`
            + (running ? `  • STOP and RESTART the server\n` : '')
            + (running ? `\nServer is currently online — operator/players will see a brief downtime.` : '');
  if (!window.confirm(msg)) return;
  const allButtons = document.querySelectorAll(
    '#plugins-library-body button, #plugins-vanilla-btn, .plugins-pack-btn'
  );
  allButtons.forEach(b => b.disabled = true);
  toast(running ? `Restarting into ${name}…` : `Applying ${name}…`);
  try {
    const r = await api.plugins.applyPack(packId);
    if (r.restarting) {
      toast(`✓ Restarting into ${r.name} — watch Status tab`, 'var(--ok)');
    } else {
      toast(`✓ ${r.name} ready — mode ${r.mode}${r.map ? ` · ${r.map}` : ''}`, 'var(--ok)');
    }
  } catch (e) {
    toast(`Pack apply failed: ${e.message}`, 'var(--bad)');
  } finally {
    await _renderPluginsTab();
  }
}

async function _pluginActivate(btn) {
  const slug = btn.dataset.slug;
  let   mode = btn.dataset.mode;
  // Multi-mode card: pull the chosen mode from the sibling <select>.
  if (!mode) {
    const sel = btn.parentElement.querySelector('.plugins-activate-mode');
    mode = sel ? sel.value : '';
    if (!mode) { toast('Pick a mode first', 'var(--bad)'); return; }
  }
  // v0.14.1 — confirm a live restart when the server is online.  No
  // confirm when offline (matches the original behaviour — clicking
  // Activate offline is low-stakes).
  const running = !!state.server.running;
  if (running) {
    if (!window.confirm(
      `Activate ${slug} for mode ${mode}?\n\n`
      + `Server is currently online. This will STOP and RESTART it `
      + `to swap plugins cleanly.\n\nProceed?`
    )) return;
  }
  // Disable everything that could double-fire while the deploy is in flight.
  // The backend serializes via _lifecycle_lock, but a fast double-click would
  // race a stale tab-state read.
  const allButtons = document.querySelectorAll(
    '#plugins-library-body button, #plugins-vanilla-btn, .plugins-pack-btn'
  );
  allButtons.forEach(b => b.disabled = true);
  toast(running ? `Restarting into ${mode}…` : `Deploying ${slug} for mode ${mode}…`);
  try {
    const r = await api.plugins.activate(slug, mode);
    if (r.restarting) {
      toast(`✓ Restarting into ${r.mode} — watch Status tab`, 'var(--ok)');
    } else {
      toast(`✓ ${slug} active for ${r.mode}`, 'var(--ok)');
    }
  } catch (e) {
    toast(`Activate failed: ${e.message}`, 'var(--bad)');
  } finally {
    // Re-render reads fresh state and re-disables based on it.
    await _renderPluginsTab();
  }
}

async function _pluginVanilla() {
  const running = !!state.server.running;
  if (!window.confirm(
    'Switch to vanilla Competitive?\n\nThis removes ALL deployed plugins ' +
    '(MatchZy, Warcraft, Retakes, etc.) from the server. ' +
    'You can re-activate them anytime.' +
    (running ? '\n\nServer is currently online — this will STOP and RESTART it.' : '')
  )) return;
  const allButtons = document.querySelectorAll(
    '#plugins-library-body button, #plugins-vanilla-btn, .plugins-pack-btn'
  );
  allButtons.forEach(b => b.disabled = true);
  toast(running ? 'Restarting into vanilla…' : 'Undeploying plugins…');
  try {
    const r = await api.plugins.vanilla();
    if (r.restarting) {
      toast(`✓ Restarting into vanilla — watch Status tab`, 'var(--ok)');
    } else {
      toast(`✓ Switched to vanilla (${r.mode})`, 'var(--ok)');
    }
  } catch (e) {
    toast(`Switch failed: ${e.message}`, 'var(--bad)');
  } finally {
    await _renderPluginsTab();
  }
}

pages['plugins'] = async function() {
  const root = el('content');
  root.innerHTML = `
    <div id="plugins-action-banner"></div>

    <div class="card">
      <div class="card-title">Server Readiness</div>
      <div id="plugins-runtime-body">
        <div class="text-sm text-sub">Loading…</div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="section-hdr">
        <span class="card-title" style="margin-bottom:0">Quick-Apply Packs</span>
        <span class="text-sm text-sub">
          One click — pick a recipe, server is ready to start
        </span>
      </div>
      <div id="plugins-packs-body">
        <div class="text-sm text-sub">Loading…</div>
      </div>
    </div>

    <!-- v0.16.3 / task #169 — Tournament Templates: named bundles of mode +
         map + pack + Discord channels + team profile IDs.  Single Apply
         stages everything for a recurring tournament format. -->
    <div class="card" style="margin-top:14px">
      <div class="section-hdr">
        <span class="card-title" style="margin-bottom:0">Tournament Templates</span>
        <button class="btn btn-sm" id="plugins-templates-manage">📋 Manage Templates</button>
      </div>
      <div class="text-sm text-sub" style="margin-bottom:10px">
        Named bundles of mode + map + pack + Discord channels. Click a
        template to stage everything for a recurring tournament format.
      </div>
      <div id="plugins-templates-body">
        <div class="text-sm text-sub">Loading…</div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="card-title">Currently Deployed</div>
      <div id="plugins-deployed-body">
        <div class="text-sm text-sub">Loading…</div>
      </div>
    </div>

    <!-- v0.15.2 slice 3: search filter + Reload + Install-from-URL toolbar.
         Single search input filters BOTH the Library and the Registry grids
         so the operator types once.  Reload picks up dropped folders without
         restarting the app.  Install-from-URL is the advanced path. -->
    <div class="card" style="margin-top:14px">
      <div class="section-hdr" style="flex-wrap:wrap; gap:8px">
        <span class="card-title" style="margin-bottom:0">Plugin Library</span>
        <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center">
          <input class="input" id="plugins-search" type="text"
                 placeholder="Filter…"
                 style="width:200px"
                 autocomplete="off" spellcheck="false">
          <button class="btn btn-sm" id="plugins-reload-btn"
                  title="Re-scan bundled + %APPDATA%/plugins/ — picks up dropped folders without restarting">
            ↻ Rescan local plugins
          </button>
          <button class="btn btn-sm" id="plugins-install-url-btn"
                  title="Install a plugin from any zip URL (advanced)">
            📥 Install from URL
          </button>
        </div>
      </div>
      <div id="plugins-library-body">
        <div class="text-sm text-sub">Loading…</div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="section-hdr">
        <span class="card-title" style="margin-bottom:0">Available to Install (Community)</span>
        <button class="btn btn-sm" id="plugins-registry-refresh"
                title="Re-fetch OblivionPluginRegistry's catalog.json (24h cache otherwise)">
          ↻ Re-fetch catalog
        </button>
      </div>
      <div id="plugins-registry-body">
        <div class="text-sm text-sub">Loading registry…</div>
      </div>
    </div>
  `;
  // Preserve the search input value across re-renders by storing on the
  // page handler closure.  Each _renderPluginsTab() reads this back.
  if (typeof _pluginsSearchTerm === 'undefined') {
    window._pluginsSearchTerm = '';
  }
  await _renderPluginsTab();
  const searchEl = el('plugins-search');
  if (searchEl) {
    searchEl.value = window._pluginsSearchTerm || '';
    let debounce;
    searchEl.addEventListener('input', () => {
      window._pluginsSearchTerm = searchEl.value;
      clearTimeout(debounce);
      // Filter is pure DOM-level (no API call), so debounce is short.
      debounce = setTimeout(_applyPluginsSearchFilter, 80);
    });
    _applyPluginsSearchFilter();
  }
  const reloadBtn = el('plugins-reload-btn');
  if (reloadBtn) reloadBtn.addEventListener('click', _pluginsReload);
  const urlBtn = el('plugins-install-url-btn');
  if (urlBtn) urlBtn.addEventListener('click', _pluginsInstallFromUrlModal);
};

/* v0.15.2 slice 3 — case-insensitive substring filter across the Library
 * and Registry grids.  We hide cards in place instead of re-rendering so
 * focus stays in the search input.  Empty term = show all. */
function _applyPluginsSearchFilter() {
  const term = (window._pluginsSearchTerm || '').trim().toLowerCase();
  const cards = document.querySelectorAll(
    '#plugins-library-body .plugin-card, #plugins-registry-body .plugin-card'
  );
  cards.forEach(card => {
    if (!term) { card.style.display = ''; return; }
    const text = (card.textContent || '').toLowerCase();
    card.style.display = text.includes(term) ? '' : 'none';
  });
}

async function _pluginsReload() {
  const btn = el('plugins-reload-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Reloading…'; }
  try {
    const r = await api.plugins.reload();
    const s = r.stats || {};
    toast(`✓ Reloaded — ${s.total} plugins (${s.bundled} bundled + ${s.local} local)`, 'var(--ok)');
  } catch (e) {
    toast(`Reload failed: ${e.message}`, 'var(--bad)');
  } finally {
    await _renderPluginsTab();
    _applyPluginsSearchFilter();
  }
}

function _pluginsInstallFromUrlModal() {
  // Use a custom overlay rather than window.prompt because we need a
  // URL input + an optional sha256 input + an unverified-source warning.
  const overlay = document.createElement('div');
  overlay.className = 'plugins-bootstrap-overlay';
  overlay.innerHTML = `
    <div class="plugins-bootstrap-modal">
      <div class="section-hdr">
        <span class="card-title" style="margin-bottom:0">Install plugin from URL</span>
        <button class="btn btn-sm" id="plugins-url-close">Close</button>
      </div>
      <p class="text-sm">
        Advanced. Paste a direct link to a plugin's zip file
        (e.g. a GitHub release asset URL). The app downloads, extracts to
        <code>%APPDATA%\\Oblivion Server Tool\\plugins\\&lt;slug&gt;\\</code>,
        and the plugin appears in the Library.
      </p>
      <div class="field" style="margin-top:14px">
        <label>Zip URL <span class="text-sub">(required, https://)</span></label>
        <input class="input" id="plugins-url-input" type="url"
               placeholder="https://github.com/author/plugin/releases/download/v1.0.0/plugin.zip"
               spellcheck="false" autocomplete="off">
      </div>
      <div class="field" style="margin-top:10px">
        <label>SHA-256 <span class="text-sub">(strongly recommended — verifies you got the right file)</span></label>
        <input class="input" id="plugins-url-sha" type="text"
               placeholder="64 hex chars from the author's release notes"
               spellcheck="false" autocomplete="off" maxlength="64">
      </div>
      <div class="field" style="margin-top:10px">
        <label>Expected slug <span class="text-sub">(optional — match against the plugin.json inside)</span></label>
        <input class="input" id="plugins-url-slug" type="text"
               placeholder="my-plugin (must equal plugin.json's slug)"
               spellcheck="false" autocomplete="off">
      </div>
      <div id="plugins-url-warning" class="text-sm" style="margin-top:14px; color:var(--warn); min-height:18px"></div>
      <div class="flex gap-8" style="margin-top:18px">
        <button class="btn btn-accent flex-1" id="plugins-url-install">Download + Install</button>
      </div>
      <small class="text-sub text-sm" style="display:block; margin-top:10px; line-height:1.5">
        The download is capped at 50 MB and uses a 12-second timeout. Without a SHA-256,
        you're trusting the URL's TLS to deliver the file the author intended.
      </small>
    </div>
  `;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  el('plugins-url-close').addEventListener('click', close);

  // Live-warn when sha256 is missing or malformed.
  const shaEl = el('plugins-url-sha');
  const warnEl = el('plugins-url-warning');
  const updateWarning = () => {
    const v = (shaEl.value || '').trim();
    if (!v) {
      warnEl.textContent = '⚠ No SHA-256 provided — install will proceed but the file is unverified.';
    } else if (!/^[0-9a-f]{64}$/i.test(v)) {
      warnEl.textContent = '⚠ SHA-256 must be 64 hex characters.';
    } else {
      warnEl.textContent = '';
    }
  };
  shaEl.addEventListener('input', updateWarning);
  updateWarning();

  el('plugins-url-install').addEventListener('click', async () => {
    const url  = (el('plugins-url-input').value || '').trim();
    const sha  = (el('plugins-url-sha').value   || '').trim();
    const slug = (el('plugins-url-slug').value  || '').trim();
    if (!url) { toast('URL is required', 'var(--bad)'); return; }
    if (!/^https?:\/\//i.test(url)) {
      toast('URL must start with https:// (or http://localhost for testing)', 'var(--bad)');
      return;
    }
    if (sha && !/^[0-9a-f]{64}$/i.test(sha)) {
      toast('SHA-256 must be 64 hex chars (or blank)', 'var(--bad)');
      return;
    }
    if (!sha) {
      if (!window.confirm(
        'Install without SHA-256 verification?\n\n'
        + 'You\'re trusting that the URL delivers exactly the file the author intended. '
        + 'A man-in-the-middle (between the server and you) could swap the zip.\n\n'
        + 'Proceed anyway?'
      )) return;
    }
    const installBtn = el('plugins-url-install');
    installBtn.disabled = true; installBtn.textContent = 'Downloading…';
    try {
      const r = await api.plugins.installFromUrl(url, sha || null, slug || null);
      const result = r.result || {};
      toast(`✓ Installed ${result.slug} v${result.version || '?'} (${result.files_written} files)`, 'var(--ok)');
      close();
      await _renderPluginsTab();
    } catch (e) {
      toast(`Install failed: ${e.message}`, 'var(--bad)');
      installBtn.disabled = false; installBtn.textContent = 'Download + Install';
    }
  });
}

/* Legacy: #workshop hash routes here, switching to the Workshop tab. */
pages['workshop'] = function() {
  _mapsTab = 'workshop';
  // Make Maps appear active in the sidebar
  document.querySelectorAll('.nav-item').forEach(a => {
    a.classList.toggle('active', a.dataset.page === 'maps');
  });
  pages['maps']();
};

/* ══════════════════════════════════════════════════════════════ VETO (v0.10.0)
   Compact SPA port of the prototype's 5-stage flow.  Live state from
   /api/veto/state + /api/veto/stream (SSE).  All mutations go through the
   server — no localStorage, no client-side source-of-truth.

   Architecture:
     - pages['veto']() builds the shell, kicks off initial fetch + SSE subscribe
     - vetoState holds the latest snapshot
     - renderVeto() is the single render entrypoint; switches on snapshot.state
     - Each stage has its own _renderStage<X>() function for clarity
     - Reset on navigate-away or hashchange cleans up the SSE                */

let _vetoState = null;
let _vetoEs = null;
let _vetoLocalRoster = [];   // unsaved roster edits before Distribute commits

// v0.11.17 B4 — set true while a veto-board click is mid-flight to suppress
// _renderVeto() rebuilds of the board.  Without this guard, an SSE event
// arriving during the await (other captain's vote, operator config change,
// etc.) can rebuild the board DOM, replacing the card the captain just
// tapped with a detached node.  Cleared in the click handler's `finally`.
let _vetoBoardClickInFlight = false;

// v0.11.23 — every veto mutation endpoint returns the fresh snapshot.
// Apply it directly so the clicker sees instant feedback without
// waiting for the SSE event to arrive (which, on LAN, often arrives
// AFTER the API response — making click-driven UI feel laggy or
// "stuck until tab refresh"). Other connected clients still rely on
// SSE; this helper only ensures the LOCAL clicker is always in sync.
//
// v0.11.27 — single point of truth for ALL snapshot ingestion.  Routes:
//   - mutation API responses (step / vote / ready / etc.)
//   - SSE onMessage payloads
//   - initial /api/veto/state fetch on tab open
//   - 3s polling fallback tick
// Two guards live here, applied uniformly to every source:
//   - Idle short-circuit (audit finding #9): if both incoming and
//     current are state=idle, do nothing.  Without this the 3s poll
//     re-renders the Create form every tick → online-banner flickers,
//     `<button>` focus lost.
//   - Monotonicity guard (audit findings #5 + #7): if both have a
//     session AND same state AND incoming.updated_at is OLDER than
//     current, refuse the apply.  Defeats two races:
//       1. Initial fetch slow (cellular) → SSE delivers snap_v2 first
//          → stale fetch overwrites it on resolve.
//       2. Poll fetch in flight → SSE delivers snap_v2 → stale poll
//          response overwrites on resolve.
//     State transitions (idle ↔ active, voting → links, etc.) always
//     apply — different state strings imply the snapshot is the
//     intended new state, not a stale fetch.
function _vetoApply(snap) {
  if (!snap || typeof snap !== 'object' || !snap.state) return snap;
  // Idle short-circuit — finding #9.
  if (_vetoState && _vetoState.state === 'idle' && snap.state === 'idle') {
    return snap;
  }
  // Monotonicity guard — findings #5 + #7.
  if (_vetoState && _vetoState.session && snap.session
      && _vetoState.state === snap.state
      && typeof _vetoState.session.updated_at === 'number'
      && typeof snap.session.updated_at === 'number'
      && snap.session.updated_at < _vetoState.session.updated_at) {
    return snap;
  }
  _vetoState = snap;
  if (currentPage === 'veto') _renderVeto();
  return snap;
}

// ── Animation bookkeeping (Day 5) ────────────────────────────────────────
// All three are reset when the session goes back to `idle` (operator hit
// Reset, or a fresh tab open) so a new session gets a fresh round of
// theatrics.  Without this, the finale-already-played guard would suppress
// confetti the second time around.
let _vetoLastRenderedState = null;   // for stage-entry fade
let _vetoLastSeqLen        = 0;      // for "just-stamped" detection on the board
let _vetoFinaleShownThisSession = false;  // confetti + decider reveal play once

const VETO_MODES = ['BO1', 'BO3', 'BO5'];

function _vetoStageIndex(stateStr) {
  // Map server state to 0-4 for the stages-pill nav
  const m = { idle: -1, roster: 0, teams: 1, voting: 2, links: 3,
              veto: 4, finale: 4, complete: 4 };
  return m[stateStr] ?? -1;
}

// v0.11.25 — polling fallback timer.  Belt-and-braces: even if SSE is
// blocked by a webview/proxy or app.js is somehow stale, every 3s we
// refetch /api/veto/state and apply via _vetoApply.  Cheap (~1 KB),
// guarantees the UI catches up within ≤3s regardless of SSE health.
let _vetoPollTimer = null;
function _vetoStartPolling() {
  if (_vetoPollTimer) return;
  _vetoPollTimer = setInterval(async () => {
    if (currentPage !== 'veto') return;
    if (document.hidden) return;        // tab background — don't waste cycles
    if (_vetoBoardClickInFlight) return; // skip during in-flight click
    // v0.11.27 — _vetoApply owns dedup (idle short-circuit + monotonicity).
    // No inline check needed; was previously duplicating logic that lived
    // half here and half in the renderer's _vetoLastRenderedState guard.
    try { _vetoApply(await api.veto.state()); }
    catch (_) { /* network blip — try again next tick */ }
  }, 3000);
}
function _vetoStopPolling() {
  if (_vetoPollTimer) { clearInterval(_vetoPollTimer); _vetoPollTimer = null; }
}

function _vetoCleanup() {
  if (_vetoEs) { try { _vetoEs.close(); } catch (_) {} _vetoEs = null; }
  _vetoStopPolling();
  _vetoState = null;
  _vetoLocalRoster = [];
  _vetoLastRenderedState = null;
  _vetoLastSeqLen = 0;
  _vetoFinaleShownThisSession = false;
}

/* ═════════════════════════════════════════════════════════════════ LOGS TAB
 * v0.16.2 / task #162 — searchable in-app log viewer (beyond the drawer).
 * The drawer is great for live tail; this is the surface for grep-after-
 * the-fact.  Reads /api/log/history (the in-memory ring buffer), exposes:
 *   - Full-text search (case-insensitive, debounced)
 *   - Source filter chips (parsed from [tag] prefix on each line)
 *   - Download as .txt
 *   - Auto-refresh toggle (poll every 3s)
 * Persistent on-disk rotation is a future v0.17 thing if operator
 * demand exists — for now the existing log_save endpoint handles
 * "give me a one-shot dump" cases. */
pages['logs'] = async function() {
  const root = el('content');
  if (_guestBlocked && _guestBlocked(root)) return;
  if (typeof window._logsState === 'undefined') {
    window._logsState = { search: '', source: 'all', autoRefresh: false };
  }
  const SOURCES = [
    ['all',      'All'],
    ['plugins',  'Plugins'],
    ['server',   'Server'],
    ['discord',  'Discord'],
    ['veto',     'Veto'],
    ['workshop', 'Workshop'],
    ['rcon',     'RCON'],
    ['app',      'App / Startup'],
  ];
  // Map filter key → list of bracketed tags that count as matching.
  const SOURCE_TAGS = {
    plugins:  ['plugins', 'gameinfo', 'metamod', 'css', 'backup'],
    server:   ['server', 'preflight', 'crash', 'startup'],
    discord:  ['discord', 'bot'],
    veto:     ['veto', 'roster'],
    workshop: ['workshop', 'steam', 'steamcmd'],
    rcon:     ['rcon'],
    app:      ['app', 'config'],
  };
  root.innerHTML = `
    <div class="section-hdr">
      <span class="section-title">App Log</span>
      <div style="display:flex; gap:8px; align-items:center">
        <label style="display:flex; gap:6px; align-items:center; font-size:11px; color:var(--text-3)">
          <input type="checkbox" id="logs-auto"> Auto-refresh
        </label>
        <button class="btn btn-sm" id="logs-refresh">↻ Refresh</button>
        <button class="btn btn-sm" id="logs-download">⬇ Download .txt</button>
      </div>
    </div>
    <div class="text-sm text-sub" style="margin-bottom:10px">
      In-memory ring buffer.  Survives until the app exits.  Use
      <strong>↻ Refresh</strong> to pull the latest lines, or toggle auto-refresh.
    </div>
    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:10px">
      <input class="input" id="logs-search" type="text"
             placeholder="Search…" autocomplete="off" spellcheck="false"
             style="width:280px">
      <div style="display:flex; gap:4px; flex-wrap:wrap" id="logs-source-row">
        ${SOURCES.map(([k, label]) =>
          `<button class="btn btn-sm ${k === (window._logsState.source || 'all') ? 'btn-accent' : 'btn-ghost'} logs-src-btn" data-src="${k}">${esc(label)}</button>`
        ).join('')}
      </div>
    </div>
    <div id="logs-count" class="text-sm text-sub" style="margin-bottom:6px"></div>
    <pre id="logs-body" class="text-mono text-sm"
         style="background:var(--bg-1); padding:12px; border:1px solid var(--line-1); max-height:65vh; overflow:auto; white-space:pre-wrap; line-height:1.45"></pre>
  `;
  let lines = [];
  const render = () => {
    const term   = (window._logsState.search || '').toLowerCase().trim();
    const src    = window._logsState.source || 'all';
    const filter = (line) => {
      if (term && !line.toLowerCase().includes(term)) return false;
      if (src === 'all') return true;
      const tags = SOURCE_TAGS[src] || [];
      const lower = line.toLowerCase();
      return tags.some(t => lower.includes(`[${t}`));
    };
    const visible = lines.filter(filter);
    const cnt = el('logs-count');
    if (cnt) cnt.textContent =
      `${visible.length} of ${lines.length} line${lines.length === 1 ? '' : 's'} shown`;
    const body = el('logs-body');
    if (body) body.textContent = visible.join('\n') || '(no matching lines)';
  };
  const refresh = async () => {
    const btn = el('logs-refresh');
    if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }
    try {
      const r = await api.logHistory();
      lines = Array.isArray(r) ? r : (r.lines || r.log || []);
      render();
    } catch (e) {
      const body = el('logs-body');
      if (body) body.textContent = `Failed to load log: ${e.message}`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '↻ Refresh'; }
    }
  };
  await refresh();
  // Wire interactions
  const search = el('logs-search');
  if (search) {
    search.value = window._logsState.search || '';
    let dbc;
    search.addEventListener('input', () => {
      window._logsState.search = search.value;
      clearTimeout(dbc);
      dbc = setTimeout(render, 80);
    });
  }
  document.querySelectorAll('.logs-src-btn').forEach(b => {
    b.addEventListener('click', () => {
      window._logsState.source = b.dataset.src;
      document.querySelectorAll('.logs-src-btn').forEach(x => {
        x.classList.toggle('btn-accent', x.dataset.src === window._logsState.source);
        x.classList.toggle('btn-ghost',   x.dataset.src !== window._logsState.source);
      });
      render();
    });
  });
  el('logs-refresh').addEventListener('click', refresh);
  el('logs-download').addEventListener('click', () => {
    const term = (window._logsState.search || '').toLowerCase().trim();
    const src  = window._logsState.source || 'all';
    const filter = (line) => {
      if (term && !line.toLowerCase().includes(term)) return false;
      if (src === 'all') return true;
      const tags = SOURCE_TAGS[src] || [];
      const lower = line.toLowerCase();
      return tags.some(t => lower.includes(`[${t}`));
    };
    const text = lines.filter(filter).join('\n');
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const blob = new Blob([text], { type: 'text/plain' });
    const url  = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `oblivion_log_${stamp}.txt`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  // Auto-refresh
  const auto = el('logs-auto');
  if (auto) {
    auto.checked = !!window._logsState.autoRefresh;
    let timer;
    const tick = () => { if (window._logsState.autoRefresh) refresh().finally(schedule); };
    const schedule = () => { clearTimeout(timer); timer = setTimeout(tick, 3000); };
    auto.addEventListener('change', () => {
      window._logsState.autoRefresh = auto.checked;
      if (auto.checked) schedule();
      else clearTimeout(timer);
    });
    if (window._logsState.autoRefresh) schedule();
  }
};


/* ════════════════════════════════════════════════════════════ READINESS TAB
 * v0.16.2 / task #168 — one-button "is my server ready for tonight?" check.
 * Calls /api/readiness which audits ~10 conditions and returns each as
 * ok / warn / fail / info.  Operator hits ↻ Re-check before going live. */
pages['readiness'] = async function() {
  const root = el('content');
  if (_guestBlocked && _guestBlocked(root)) return;
  root.innerHTML = `
    <div class="section-hdr">
      <span class="section-title">Tournament Pre-flight</span>
      <button class="btn btn-sm" id="rd-recheck">↻ Re-check</button>
    </div>
    <div class="text-sm text-sub" style="margin-bottom:14px">
      Quick green/red audit of everything tonight's tournament will touch.
      Run this before going live; fix anything red, ideally clear all warnings.
    </div>
    <div id="rd-summary"></div>
    <div id="rd-body">
      <div class="empty-state text-sm text-sub">Running checks…</div>
    </div>
  `;
  const ICONS = { ok: '✓', warn: '⚠', fail: '✗', info: '·' };
  async function run() {
    const recheckBtn = el('rd-recheck');
    if (recheckBtn) { recheckBtn.disabled = true; recheckBtn.textContent = 'Running…'; }
    el('rd-body').innerHTML = '<div class="empty-state text-sm text-sub">Running checks…</div>';
    let data;
    try { data = await api.readiness(); }
    catch (e) {
      el('rd-body').innerHTML =
        `<div class="text-sm text-red">Failed: ${esc(e.message)}</div>`;
      if (recheckBtn) { recheckBtn.disabled = false; recheckBtn.textContent = '↻ Re-check'; }
      return;
    }
    const c = data.counts || {};
    const verdict =
      data.overall === 'fail' ? '✗ Not ready — fix the red items below'
    : data.overall === 'warn' ? '⚠ Mostly ready — warnings worth resolving'
    :                            '✓ Ready for tournament';
    const verdictColor =
      data.overall === 'fail' ? 'var(--bad)'
    : data.overall === 'warn' ? 'var(--warn)'
    :                            'var(--ok)';
    el('rd-summary').innerHTML = `
      <div class="card" style="margin-bottom:14px; border-left:4px solid ${verdictColor}">
        <div style="font-size:16px; font-weight:600; color:${verdictColor}; margin-bottom:6px">
          ${esc(verdict)}
        </div>
        <div class="text-sm text-sub">
          ${c.ok || 0} ok · ${c.warn || 0} warn · ${c.fail || 0} fail · ${c.info || 0} info
        </div>
      </div>`;
    // v0.16.5 / task #157 item F: map each check key to a "→ Fix" action
    // (target tab + button copy).  Only renders when status is fail/warn,
    // so green/info rows stay clean.
    const FIX_ACTIONS = {
      csgo:      { tab: 'config',   label: 'Open Config → Install' },
      runtime:   { tab: 'plugins',  label: 'Open Plugins → Set up runtime' },
      plugins:   { tab: 'plugins',  label: 'Open Plugins → Pick a pack' },
      disk:      null,   // operator-only fix; no in-app navigation helps
      pin:       { tab: 'config',   label: 'Open Config → Security' },
      discord:   { tab: 'config',   label: 'Open Config → Discord' },
      vc:        { tab: 'config',   label: 'Open Config → Discord (VC)' },
      registry:  { tab: 'plugins',  label: 'Open Plugins → ↻ Re-fetch catalog' },
      veto:      { tab: 'veto',     label: 'Open Veto → Reset' },
      share_url: { tab: 'config',   label: 'Open Config → Match Flow' },
    };
    const rows = (data.checks || []).map((check, idx) => {
      const color = check.status === 'fail' ? 'var(--bad)'
                  : check.status === 'warn' ? 'var(--warn)'
                  : check.status === 'ok'   ? 'var(--ok)'
                  :                            'var(--text-3)';
      const action = FIX_ACTIONS[check.key];
      const showFix = action && (check.status === 'fail' || check.status === 'warn');
      const fixBtn = showFix
        ? `<button class="btn btn-sm" data-fix-idx="${idx}"
                   data-fix-tab="${esc(action.tab)}">${esc(action.label)} →</button>`
        : '';
      return `
        <div class="card" style="margin-bottom:8px; display:flex; gap:14px; align-items:center">
          <div style="font-size:20px; color:${color}; min-width:24px; text-align:center">
            ${esc(ICONS[check.status] || '?')}
          </div>
          <div style="flex:1">
            <div style="font-size:13px; color:var(--text-1); font-weight:500">
              ${esc(check.label || check.key || '?')}
            </div>
            ${check.detail ? `<div class="text-sm text-sub" style="margin-top:4px">${esc(check.detail)}</div>` : ''}
          </div>
          ${fixBtn}
        </div>`;
    }).join('');
    el('rd-body').innerHTML = rows || '<div class="text-sm text-sub">No checks returned.</div>';
    // Wire up the per-check "→ Fix" buttons after re-render.
    document.querySelectorAll('button[data-fix-idx]').forEach(btn => {
      btn.addEventListener('click', () => navigate(btn.dataset.fixTab));
    });
    if (recheckBtn) { recheckBtn.disabled = false; recheckBtn.textContent = '↻ Re-check'; }
  }
  run();
  el('rd-recheck').addEventListener('click', run);
};


/* ══════════════════════════════════════════════════════════════ HISTORY TAB
 * v0.16.2 / task #161 — dedicated page for past tournaments.  The modal
 * version (👈 _vetoOpenHistoryModal) stays as a quick peek from the Veto
 * header; this page is the full surface with search/filter so operators
 * can dig into "what did Team A play last month?". */
pages['history'] = async function() {
  const root = el('content');
  if (typeof window._historySearch === 'undefined') window._historySearch = '';
  root.innerHTML = `
    <div class="section-hdr">
      <span class="section-title">Match History</span>
      <input class="input" id="hist-search" type="text"
             placeholder="Filter by team name / mode / map…"
             style="width:280px" autocomplete="off" spellcheck="false">
    </div>
    <div class="text-sm text-sub" style="margin-bottom:14px">
      Past completed veto sessions, newest first.  Stored in
      <code>%APPDATA%\\Oblivion Server Tool\\oblivion_matches.json</code>.
      Last 10 retained.
    </div>
    <div id="hist-body">
      <div class="empty-state text-sm text-sub">Loading…</div>
    </div>

    <!-- v0.16.3 / task #171 — Demo browser sub-section.
         Lists every .dem file under csgo/ + MatchZy demo dir + CSS demo
         output.  Click to download via /api/demos/download. -->
    <div style="margin-top:32px">
      <div class="section-hdr">
        <span class="section-title">Demos</span>
        <button class="btn btn-sm" id="demos-refresh">↻ Refresh</button>
      </div>
      <div class="text-sm text-sub" style="margin-bottom:14px">
        Server-recorded <code>.dem</code> files found under
        <code>csgo/</code>, <code>cfg/MatchZy/</code>, and the
        CSS plugin demo dirs.  Click to download.
      </div>
      <div id="demos-body">
        <div class="empty-state text-sm text-sub">Scanning…</div>
      </div>
    </div>
  `;
  let matches;
  try {
    const r = await api.veto.history();
    matches = (r.matches || []).slice().reverse();   // newest first
  } catch (e) {
    el('hist-body').innerHTML =
      `<div class="text-sm text-red">Failed to load: ${esc(e.message)}</div>`;
    return;
  }
  const renderList = () => {
    const body = el('hist-body');
    const term = (window._historySearch || '').toLowerCase().trim();
    const visible = !term ? matches : matches.filter(m => {
      const blob = [
        m.mode,
        (m.team_a || {}).name, (m.team_b || {}).name,
        ...((m.final_maps || []).map(x => typeof x === 'string' ? x : x.map)),
        m.decider,
      ].join(' ').toLowerCase();
      return blob.includes(term);
    });
    if (!visible.length) {
      body.innerHTML = `
        <div class="empty-state text-sm text-sub">
          ${matches.length === 0
              ? 'No matches in history yet.'
              : `No matches match <em>${esc(term)}</em>.`}
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="text-sm text-sub" style="margin-bottom:12px">
        Showing ${visible.length} of ${matches.length} match${matches.length === 1 ? '' : 'es'}.
      </div>
      <div class="veto-history-list">
        ${visible.map(m => _renderHistoryEntry(m)).join('')}
      </div>
    `;
  };
  renderList();
  const search = el('hist-search');
  if (search) {
    search.value = window._historySearch || '';
    let debounce;
    search.addEventListener('input', () => {
      window._historySearch = search.value;
      clearTimeout(debounce);
      debounce = setTimeout(renderList, 80);
    });
  }
  // v0.16.3 / task #171 — Demo browser
  const renderDemos = async () => {
    const body = el('demos-body');
    if (!body) return;
    body.innerHTML = '<div class="empty-state text-sm text-sub">Scanning…</div>';
    let demoResp;
    try { demoResp = await api.demos.list(); }
    catch (e) {
      body.innerHTML = `<div class="text-sm text-red">Failed: ${esc(e.message)}</div>`;
      return;
    }
    const demos = demoResp.demos || [];
    if (!demos.length) {
      body.innerHTML = `
        <div class="empty-state text-sm text-sub">
          No demos found. CS2 server records .dem files automatically when
          configured; MatchZy stores them under
          <code>cfg/MatchZy/</code> or its plugin demos dir.
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="text-sm text-sub" style="margin-bottom:10px">
        ${demos.length} demo${demos.length === 1 ? '' : 's'} found, newest first.
      </div>
      <div style="display:flex; flex-direction:column; gap:6px">
        ${demos.map(d => `
          <div class="card" style="display:flex; gap:14px; align-items:center; padding:10px 14px">
            <div style="flex:1; min-width:0">
              <div style="font-family:var(--font-mono); font-size:12px; color:var(--text-1); word-break:break-all">
                ${esc(d.name)}
              </div>
              <div class="text-sm text-sub">
                ${esc(d.mtime_iso)} · ${(d.size / 1024 / 1024).toFixed(2)} MB · source: ${esc(d.source)}
              </div>
            </div>
            <a class="btn btn-sm" href="${esc(api.demos.downloadUrl(d.rel_path))}" download="${esc(d.name)}">
              ⬇ Download
            </a>
          </div>`).join('')}
      </div>`;
  };
  await renderDemos();
  const demoBtn = el('demos-refresh');
  if (demoBtn) demoBtn.addEventListener('click', renderDemos);
};

pages['veto'] = function() {
  _vetoLocalRoster = [];   // fresh roster buffer each time the tab opens
  const content = el('content');
  content.innerHTML = `
    <div class="veto-hdr">
      <h1>Veto</h1>
      <span class="veto-sub" id="veto-mode-label">match setup</span>
      <div class="veto-spacer"></div>
      <button class="btn btn-ghost btn-sm" id="veto-history-btn"
              title="Show the last 10 completed matches">📜 History</button>
      <button class="btn btn-ghost btn-sm" id="veto-spectator-btn"
              title="Generate a read-only spectator URL for casters / observers">📺 Spectator</button>
      <button class="btn btn-ghost" id="veto-reset-btn" style="display:none">Reset session</button>
    </div>
    <div class="veto-pill" id="veto-pill">
      <div class="veto-pill-cell" data-s="0"><span class="veto-pill-num">1</span><span>Roster</span></div>
      <div class="veto-pill-cell" data-s="1"><span class="veto-pill-num">2</span><span>Teams</span></div>
      <div class="veto-pill-cell" data-s="2"><span class="veto-pill-num">3</span><span>Vote</span></div>
      <div class="veto-pill-cell" data-s="3"><span class="veto-pill-num">4</span><span>Links</span></div>
      <div class="veto-pill-cell" data-s="4"><span class="veto-pill-num">5</span><span>Veto</span></div>
    </div>
    <div id="veto-content"><div class="card" style="text-align:center;padding:40px;color:var(--text-3)">Loading...</div></div>
  `;

  el('veto-history-btn').addEventListener('click', _vetoOpenHistoryModal);
  el('veto-spectator-btn').addEventListener('click', _vetoOpenSpectatorModal);
  el('veto-reset-btn').addEventListener('click', async () => {
    if (!confirm('Reset the active veto session? All roster and progress will be lost.')) return;
    try { _vetoApply(await api.veto.reset()); toast('Session reset'); }
    catch (e) { toast(e.message, 'var(--bad)'); }
  });

  // Initial state fetch + SSE subscribe (SSE delivers initial snapshot on
  // connect, so we could skip the explicit fetch — but the fetch gives us a
  // synchronous render, which feels snappier on tab open).
  // v0.11.27 — route initial fetch through _vetoApply so the monotonicity
  // guard catches a slow fetch that resolves AFTER SSE delivered a newer
  // snap (finding #5).
  api.veto.state().then(_vetoApply)
                 .catch(e => toast(e.message, 'var(--bad)'));
  _vetoSubscribe();
};

function _vetoSubscribe() {
  // v0.10.2: refactored to use _oblivionSSE.  Exponential backoff +
  // visibility/online re-arm + aggregate health status now come for free.
  // _vetoEs becomes a handle (with .close()) instead of the raw EventSource.
  if (_vetoEs) { try { _vetoEs.close(); } catch (_) {} }
  _vetoEs = _oblivionSSE.connect('/api/veto/stream', {
    label: 'veto',
    onMessage: (e) => {
      // v0.11.27 — route SSE through _vetoApply so monotonicity guard
      // catches a re-delivered older snap after EventSource reconnect.
      try { _vetoApply(JSON.parse(e.data)); } catch (_) {}
    },
  });
  _vetoStartPolling();   // v0.11.25 — 3s fallback poll alongside SSE
}

function _renderVeto() {
  if (!_vetoState) return;
  const root = el('veto-content');
  if (!root) return;
  const state = _vetoState.state;
  const sess  = _vetoState.session;

  // Defence against keystroke loss: if the user is actively focused on
  // a text input inside the veto tree (roster names, team names, SteamIDs,
  // anywhere), don't rebuild the DOM out from under them.  The snapshot
  // is already cached in _vetoState — the next render trigger (user click
  // elsewhere, stage change, SSE ping after blur) will reflect it.
  const ae = document.activeElement;
  if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')
      && root.contains(ae)) {
    return;
  }

  // v0.11.17 B4 — same defence for the veto-board stage: if a captain (or
  // admin) just tapped a map card and the API call is in flight, skip the
  // re-render so the tap doesn't get lost to a fresh DOM mid-await.  The
  // SSE event(s) arriving during the request are coalesced into the next
  // re-render that fires AFTER the in-flight click completes (success or
  // error path clears the flag).  Tournament impact: captains on 3G
  // phones tapping a card sometimes saw the board rebuild before their
  // request completed, leading to a confusing "did my pick land?" toast
  // even though it had.
  if (state === 'veto' && _vetoBoardClickInFlight) {
    return;
  }

  // ── Day 5: session-scoped animation state resets on idle ──────────────
  // When the operator hits Reset, the snapshot flips back to state=idle.
  // Clear the finale-already-played + seq-len trackers so a fresh session
  // gets fresh confetti and fresh "just-stamped" stamps.
  if (state === 'idle') {
    _vetoLastSeqLen = 0;
    _vetoFinaleShownThisSession = false;
  }
  // Detect state transition so we can play the stage-fade-in animation
  // exactly once per state change (not on every SSE re-render of the same
  // stage).  _vetoLastRenderedState is updated AFTER the renderer runs.
  const stateChanged = (_vetoLastRenderedState !== state);

  // Stage-pill nav update
  const stageIdx = _vetoStageIndex(state);
  document.querySelectorAll('.veto-pill-cell').forEach(cell => {
    const idx = parseInt(cell.dataset.s, 10);
    cell.classList.toggle('active', idx === stageIdx);
    cell.classList.toggle('done',   idx >= 0 && idx < stageIdx);
  });
  // Show Reset button when there's an active session
  el('veto-reset-btn').style.display = (state === 'idle') ? 'none' : '';
  // Mode label
  if (sess) el('veto-mode-label').textContent = `${sess.mode} · ${sess.team_a_name} vs ${sess.team_b_name}`;
  else      el('veto-mode-label').textContent = 'match setup';

  // Captain view: simplified — only the captain's actionable stages
  const isCap = (state_.server && state_.server.role === 'captain');
  if (isCap) return _renderVetoCaptain(root, state, sess);

  // v0.12.3 / task #135 — Voter view: minimal voting screen showing this
  // voter's team's 5 names + a single click to cast.  Strictly smaller
  // than the captain view (voter can only ever do one thing).
  const isVoter = (state_.server && state_.server.role === 'voter');
  if (isVoter) return _renderVetoVoter(root, state, sess);

  // Admin / local view: full flow
  switch (state) {
    case 'idle':     _renderVetoIdle(root); break;
    case 'roster':   _renderVetoRoster(root, sess); break;
    case 'teams':    _renderVetoTeams(root, sess); break;
    case 'voting':   _renderVetoVoting(root, sess); break;
    case 'links':    _renderVetoLinks(root, sess); break;
    case 'veto':     _renderVetoBoard(root, sess); break;
    case 'finale':   _renderVetoFinale(root, sess); break;
    case 'complete': _renderVetoComplete(root, sess); break;
    default:         root.innerHTML = `<div class="card">Unknown state: ${esc(state)}</div>`;
  }
  // ── Day 5: stage-entry fade-in ────────────────────────────────────────
  // Tag the freshly-rendered .veto-stage so the CSS animation runs exactly
  // once (when it enters the DOM tree) and doesn't re-fire on SSE pings
  // that arrive while the operator is on the same stage.
  if (stateChanged) {
    const stageEl = root.querySelector('.veto-stage');
    if (stageEl) stageEl.classList.add('veto-stage-enter');
  }
  _vetoLastRenderedState = state;
}

// Shortcut to the global SPA state (different from _vetoState — that's the
// veto-session snapshot; this is the server-status snapshot which carries `role`).
const state_ = state;

/* ── Idle / Create ─────────────────────────────────────────────────────── */
let _vetoCreateMode = 'BO3';
function _renderVetoIdle(root) {
  root.innerHTML = `
    <div class="veto-stage">
      <div id="veto-online-banner" class="veto-online-banner veto-online-banner--loading">
        Checking public share URL…
      </div>
      <div class="veto-stage-head"><h2>Start a new match setup</h2>
        <span class="sub">10 players, captain vote, ban/pick over 7 maps</span></div>
      <div class="veto-create-card">
        <div class="field">
          <label>Match format</label>
          <div class="veto-mode-pills" id="veto-mode-pills">
            ${VETO_MODES.map(m => `<div class="pill ${m===_vetoCreateMode?'active':''}" data-mode="${m}">${m}</div>`).join('')}
          </div>
        </div>
        <button class="btn btn-accent" id="veto-create-btn">Create session →</button>
      </div>
    </div>
  `;
  _vetoRenderOnlineBanner();   // fire-and-forget — paints async, OK if Idle re-renders
  document.querySelectorAll('#veto-mode-pills .pill').forEach(p => {
    p.addEventListener('click', () => {
      _vetoCreateMode = p.dataset.mode;
      document.querySelectorAll('#veto-mode-pills .pill').forEach(x =>
        x.classList.toggle('active', x.dataset.mode === _vetoCreateMode));
    });
  });
  el('veto-create-btn').addEventListener('click', async () => {
    try { _vetoApply(await api.veto.create(_vetoCreateMode)); toast('Session created'); }
    catch (e) { toast(e.message, 'var(--bad)'); }
  });
}

/* ── Roster ────────────────────────────────────────────────────────────── */
/* v0.16.1 / task #160 — Persistent team profiles modal.
 * Server-backed (oblivion_teams.json in %APPDATA%) so operators running
 * recurring tournaments don't re-paste 10 SteamIDs every week.
 * Each team = 5 players. Roster needs 10 — two teams. */
async function _openTeamProfilesModal() {
  let teams;
  try { teams = (await api.teams.list()).teams || []; }
  catch (e) { toast(`Teams list failed: ${e.message}`, 'var(--bad)'); return; }

  const overlay = document.createElement('div');
  overlay.className = 'plugins-bootstrap-overlay';
  const renderBody = () => {
    const slotsA = _vetoLocalRoster.slice(0, 5);
    const slotsB = _vetoLocalRoster.slice(5, 10);
    const sideHasNames = (s) => s.some(p => (p.name || '').trim());
    return `
      <div class="plugins-bootstrap-modal" style="max-width:680px">
        <div class="section-hdr">
          <span class="card-title" style="margin-bottom:0">Team Profiles</span>
          <button class="btn btn-sm" id="tp-close">Close</button>
        </div>
        <p class="text-sm text-sub" style="margin-top:8px">
          Stored in <code>%APPDATA%\\Oblivion Server Tool\\oblivion_teams.json</code>.
          Each team is 5 players. Roster has 10 (Team A = slots 1-5, Team B = slots 6-10).
        </p>

        <div class="cfg-card-title" style="margin-top:18px">Saved teams (${teams.length})</div>
        <div id="tp-list">
          ${teams.length ? teams.map(t => `
            <div class="plugin-card" style="margin-bottom:8px" data-id="${esc(t.id)}">
              <div class="plugin-card-head">
                <span class="plugin-card-title">
                  ${esc(t.name)}${t.tag ? ` <span class="text-sub">[${esc(t.tag)}]</span>` : ''}
                </span>
                <span class="pill pill-mute">${t.players.length} players</span>
              </div>
              <div class="text-sm text-sub">
                ${t.players.map(p => esc(p.name)).join(', ')}
              </div>
              <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px">
                <button class="btn btn-sm tp-load-a" data-id="${esc(t.id)}">Load → Team A (1-5)</button>
                <button class="btn btn-sm tp-load-b" data-id="${esc(t.id)}">Load → Team B (6-10)</button>
                <button class="btn btn-sm btn-ghost tp-delete" data-id="${esc(t.id)}">Delete</button>
              </div>
            </div>`).join('')
            : '<div class="text-sm text-sub">No teams saved yet. Save a side below.</div>'}
        </div>

        <hr class="cfg-card-divider">
        <div class="cfg-card-title">Save current side as a team</div>
        <div class="field" style="margin-top:8px">
          <label>Team name</label>
          <input class="input" id="tp-save-name" type="text" maxlength="40"
                 placeholder="e.g. Team Alpha">
        </div>
        <div class="field">
          <label>Tag <span class="text-sub">(optional, short label)</span></label>
          <input class="input" id="tp-save-tag" type="text" maxlength="8" placeholder="COB">
        </div>
        <div class="flex gap-8" style="margin-top:8px">
          <button class="btn btn-ghost flex-1" id="tp-save-a"
                  ${sideHasNames(slotsA) ? '' : 'disabled title="Type names in slots 1-5 first"'}>
            Save Team A (slots 1-5)
          </button>
          <button class="btn btn-ghost flex-1" id="tp-save-b"
                  ${sideHasNames(slotsB) ? '' : 'disabled title="Type names in slots 6-10 first"'}>
            Save Team B (slots 6-10)
          </button>
        </div>
      </div>`;
  };
  overlay.innerHTML = renderBody();
  document.body.appendChild(overlay);

  const close = () => overlay.remove();
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  el('tp-close').addEventListener('click', close);

  const rewireAfter = async () => {
    try { teams = (await api.teams.list()).teams || []; } catch (_) {}
    overlay.innerHTML = renderBody();
    document.body.appendChild(overlay);
    wireButtons();
  };
  const wireButtons = () => {
    el('tp-close').addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    overlay.querySelectorAll('.tp-load-a, .tp-load-b').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.id;
        const target = btn.classList.contains('tp-load-a') ? 'A' : 'B';
        const t = teams.find(t => t.id === id);
        if (!t) { toast('Team vanished', 'var(--bad)'); return; }
        if (!window.confirm(
          `Load ${t.name} into Team ${target} (slots ${target === 'A' ? '1-5' : '6-10'})?\n\n`
          + `This OVERWRITES whatever is currently typed in those slots.`
        )) return;
        const startSlot = target === 'A' ? 0 : 5;
        for (let i = 0; i < 5; i++) {
          const src = t.players[i] || { name: '', steam_id: '', discord_id: '' };
          _vetoLocalRoster[startSlot + i] = {
            name:       src.name       || '',
            steam_id:   src.steam_id   || '',
            discord_id: src.discord_id || '',
          };
        }
        close();
        // Force the roster card to re-render with the loaded rows.
        _renderVeto();
        toast(`✓ Loaded ${t.name} into Team ${target}`, 'var(--ok)');
      });
    });
    overlay.querySelectorAll('.tp-delete').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        const t  = teams.find(t => t.id === id);
        if (!window.confirm(`Delete saved team "${t ? t.name : id}"?`)) return;
        try {
          await api.teams.delete(id);
          toast('Team deleted');
          await rewireAfter();
        } catch (e) {
          toast(`Delete failed: ${e.message}`, 'var(--bad)');
        }
      });
    });
    const saveOne = async (sliceStart) => {
      const name = el('tp-save-name').value.trim();
      const tag  = el('tp-save-tag').value.trim();
      if (!name) { toast('Team name is required', 'var(--bad)'); return; }
      const players = _vetoLocalRoster.slice(sliceStart, sliceStart + 5)
                                       .map(p => ({
                                          name: (p.name || '').trim(),
                                          steam_id: (p.steam_id || '').trim(),
                                          discord_id: (p.discord_id || '').trim(),
                                       }))
                                       .filter(p => p.name);
      if (!players.length) { toast('No named players in that side', 'var(--bad)'); return; }
      try {
        await api.teams.save({ name, tag, players });
        toast(`✓ Saved ${name}`, 'var(--ok)');
        await rewireAfter();
      } catch (e) {
        toast(`Save failed: ${e.message}`, 'var(--bad)');
      }
    };
    const saveABtn = el('tp-save-a');
    if (saveABtn && !saveABtn.disabled) saveABtn.addEventListener('click', () => saveOne(0));
    const saveBBtn = el('tp-save-b');
    if (saveBBtn && !saveBBtn.disabled) saveBBtn.addEventListener('click', () => saveOne(5));
  };
  wireButtons();
}

function _renderVetoRoster(root, sess) {
  // Pull existing roster from server if local buffer is empty (e.g. SSE
  // arrived first OR we revisited the tab mid-roster).
  if (_vetoLocalRoster.length === 0) {
    // v0.11.0 fix: include discord_id when hydrating from the server
    // snapshot.  Without this, a tab-revisit / SSE re-render after
    // partial typing would drop the operator's Discord ID input.
    _vetoLocalRoster = (sess.roster && sess.roster.length === 10)
      ? sess.roster.map(p => ({
          name: p.name,
          steam_id: p.steam_id || '',
          discord_id: p.discord_id || ''
        }))
      : Array.from({length: 10}, () => ({ name: '', steam_id: '', discord_id: '' }));
  }
  const filled = _vetoLocalRoster.filter(p => p.name.trim()).length;
  const ready  = filled === 10;
  root.innerHTML = `
    <div class="veto-stage">
      <div class="veto-stage-head"><h2>Roster</h2>
        <span class="sub">Enter 10 players · names required, SteamIDs enable MatchZy strict team mode</span></div>
      <div class="veto-roster-team-names">
        <div class="veto-tn a">
          <label>Team A</label>
          <input id="veto-team-a-name" value="${esc(sess.team_a_name || 'Team Alpha')}" maxlength="32">
        </div>
        <div class="veto-tn b">
          <label>Team B</label>
          <input id="veto-team-b-name" value="${esc(sess.team_b_name || 'Team Bravo')}" maxlength="32">
        </div>
      </div>
      <div class="veto-roster-grid" id="veto-roster-grid">
        ${_vetoLocalRoster.map((p, i) => `
          <div class="veto-roster-slot ${p.name?'filled':''}" data-i="${i}">
            <span class="veto-slot-num">${String(i+1).padStart(2,'0')}</span>
            <input class="veto-name" placeholder="Player name" value="${esc(p.name)}" data-i="${i}" maxlength="32">
            <input class="veto-steam" placeholder="SteamID (optional)" value="${esc(p.steam_id||'')}" data-i="${i}" maxlength="64">
            <input class="veto-discord" placeholder="Discord ID (auto-DM, optional)" value="${esc(p.discord_id||'')}" data-i="${i}" maxlength="32"
                   inputmode="numeric" pattern="[0-9]*">
          </div>
        `).join('')}
      </div>
      <div class="veto-stage-actions">
        <button class="btn btn-ghost" id="veto-roster-demo">Demo names</button>
        <button class="btn btn-ghost" id="veto-roster-paste"
                title="Paste 10 lines from clipboard. Each line: 'Name' OR 'Name,SteamID64' OR 'Name,SteamID64,DiscordID' (comma/tab/semicolon delimited)">
          Paste 10 names
        </button>
        <button class="btn btn-ghost" id="veto-roster-preset-save"
                title="Save the current 10-player roster as a named preset (stored in this browser)">
          💾 Save preset
        </button>
        <select class="select" id="veto-roster-preset-load"
                title="Load a saved roster preset (overwrites current input)">
          <option value="">— Load preset —</option>
        </select>
        <!-- v0.16.1 / task #160 — Persistent server-backed team profiles.
             Survives across machines (lives in %APPDATA%, not localStorage). -->
        <button class="btn btn-ghost" id="veto-roster-teams"
                title="Save / load 5-player teams (server-side persistence)">
          👥 Teams
        </button>
        <button class="btn btn-ghost" id="veto-roster-discord"
                title="Pull connected members.  Uses your default voice channel if configured (Config → Discord); otherwise opens the picker.  Shift+click forces the picker.">
          🎤 Pull from voice channel
        </button>
        <!-- v0.11.16 — mobile-safe path to the picker (no shift+click).
             Always opens the picker modal so tablet / phone operators can
             override the configured default VC without keyboard shortcuts. -->
        <button class="btn btn-ghost" id="veto-roster-discord-pick"
                title="Pick a voice channel from a list (skips the configured default)">
          🔀 Pick channel…
        </button>
        <div class="spacer"></div>
        <div class="veto-roster-progress">
          <span class="veto-ring ${ready?'full':''}">${filled}</span>
          <span>of 10 ready</span>
        </div>
        <button class="btn btn-accent" id="veto-roster-save" ${ready?'':'disabled'}>Save roster →</button>
        <button class="btn btn-accent" id="veto-distribute-btn" style="display:none">Distribute teams →</button>
      </div>
    </div>
  `;
  // Wire input handlers.
  // CRITICAL: do NOT call _renderVeto() on every keystroke — innerHTML
  // rebuilds blow away the input element + the user's caret position,
  // so they can only type one character before focus is lost.  Instead
  // we update the counter ring + slot class + Save button in place.
  document.querySelectorAll('#veto-roster-grid input.veto-name').forEach(inp => {
    inp.addEventListener('input', (e) => {
      const i = parseInt(e.target.dataset.i, 10);
      _vetoLocalRoster[i].name = e.target.value;
      // Targeted DOM updates so focus + caret stay put.
      const slot = e.target.closest('.veto-roster-slot');
      if (slot) slot.classList.toggle('filled', !!e.target.value.trim());
      const filledNow = _vetoLocalRoster.filter(p => p.name.trim()).length;
      const readyNow  = (filledNow === 10);
      const ring = document.querySelector('.veto-roster-progress .veto-ring');
      if (ring) {
        ring.textContent = String(filledNow);
        ring.classList.toggle('full', readyNow);
      }
      const saveBtn = el('veto-roster-save');
      if (saveBtn) saveBtn.disabled = !readyNow;
    });
  });
  document.querySelectorAll('#veto-roster-grid input.veto-steam').forEach(inp => {
    inp.addEventListener('input', (e) => {
      const i = parseInt(e.target.dataset.i, 10);
      _vetoLocalRoster[i].steam_id = e.target.value;
    });
  });
  // v0.11.0 — Discord ID column: digits only, optional, used by Layer 1A
  // auto-DM on /api/veto/tokens.  Same in-place update pattern as SteamID.
  document.querySelectorAll('#veto-roster-grid input.veto-discord').forEach(inp => {
    inp.addEventListener('input', (e) => {
      const i = parseInt(e.target.dataset.i, 10);
      _vetoLocalRoster[i].discord_id = e.target.value.replace(/[^0-9]/g, '');
      // Keep input value in sync after the strip
      if (e.target.value !== _vetoLocalRoster[i].discord_id) {
        e.target.value = _vetoLocalRoster[i].discord_id;
      }
    });
  });
  el('veto-roster-demo').addEventListener('click', () => {
    const demo = ['Phoenix','Vortex','Stryker','Talon','Reaver',
                  'Wraith','Cypher','Onyx','Raven','Echo'];
    _vetoLocalRoster = demo.map((n, i) => ({ name: n, steam_id: `STEAM_DEMO_${i}`, discord_id: '' }));
    _renderVeto();
  });
  el('veto-roster-paste').addEventListener('click', async () => {
    try {
      const txt = await navigator.clipboard.readText();
      const lines = txt.split(/[\r\n]+/).map(s => s.trim()).filter(Boolean);
      if (lines.length < 10) { toast(`Need 10 entries, clipboard has ${lines.length}`, 'var(--bad)'); return; }
      // v0.11.0 polish — Bulk paste now recognises three formats:
      //   1. `Name`                                  (legacy)
      //   2. `Name,SteamID64`                        (Discord copy-out)
      //   3. `Name,SteamID64,DiscordSnowflake`       (full)
      // SteamID64 = 17-digit decimal starting with "765611".  Discord
      // snowflake = 17-19 digit decimal.  Comma OR tab OR semicolon
      // delimits.  Quotes around fields are stripped.
      const SID_RE  = /^7656\d{13}$/;
      const SNOW_RE = /^\d{17,19}$/;
      _vetoLocalRoster = lines.slice(0, 10).map(line => {
        const parts = line.split(/[\t,;]/).map(s => s.trim().replace(/^["']|["']$/g, ''));
        const out = { name: parts[0] || '', steam_id: '', discord_id: '' };
        for (const p of parts.slice(1)) {
          if (SID_RE.test(p))                       out.steam_id   = p;
          else if (SNOW_RE.test(p) && !out.discord_id) out.discord_id = p;
        }
        return out;
      });
      _renderVeto();
      const withSid = _vetoLocalRoster.filter(p => p.steam_id).length;
      const withDid = _vetoLocalRoster.filter(p => p.discord_id).length;
      toast(`Pasted 10 (${withSid} SteamIDs, ${withDid} DiscordIDs)`);
    } catch (e) { toast(`Clipboard read failed: ${e.message}`, 'var(--bad)'); }
  });

  // v0.11.0 polish — Roster presets (localStorage-backed; per-browser).
  // Operator running recurring teams (weekly Tuesday match etc.) can save
  // the current 10-player roster + reload it in one click.  Stored as
  // {presetName: [{name, steam_id, discord_id} x 10]} under
  // localStorage['oblivion.roster_presets'].  Per-browser is fine — per
  // user memory, operator runs from one machine.
  const _PRESETS_KEY = 'oblivion.roster_presets';
  const _readPresets = () => {
    try { return JSON.parse(localStorage.getItem(_PRESETS_KEY) || '{}'); }
    catch (_e) { return {}; }
  };
  const _writePresets = (obj) => {
    try { localStorage.setItem(_PRESETS_KEY, JSON.stringify(obj)); }
    catch (e) { toast(`Preset save failed: ${e.message}`, 'var(--bad)'); }
  };
  const _populatePresetDropdown = () => {
    const sel = el('veto-roster-preset-load');
    if (!sel) return;
    const presets = _readPresets();
    const names = Object.keys(presets).sort();
    sel.innerHTML = `<option value="">— Load preset (${names.length}) —</option>` +
      names.map(n => `<option value="${esc(n)}">${esc(n)} (${(presets[n] || []).length})</option>`).join('') +
      (names.length ? `<option value="__delete__">⚠ Delete a preset…</option>` : '');
  };
  _populatePresetDropdown();

  el('veto-roster-preset-save').addEventListener('click', () => {
    const filled = _vetoLocalRoster.filter(p => p.name.trim()).length;
    if (filled === 0) { toast('Nothing to save — roster is empty', 'var(--bad)'); return; }
    const name = (prompt(`Preset name? (${filled}/10 filled — will save as-is)`) || '').trim();
    if (!name) return;
    const presets = _readPresets();
    if (presets[name] && !confirm(`Overwrite preset "${name}"?`)) return;
    presets[name] = _vetoLocalRoster.map(p => ({
      name: p.name || '', steam_id: p.steam_id || '', discord_id: p.discord_id || ''
    }));
    _writePresets(presets);
    _populatePresetDropdown();
    toast(`Saved preset "${name}"`);
  });

  el('veto-roster-preset-load').addEventListener('change', (e) => {
    const v = e.target.value;
    if (!v) return;
    if (v === '__delete__') {
      const presets = _readPresets();
      const names = Object.keys(presets);
      const which = (prompt(`Delete which preset?\n\n${names.join('\n')}`) || '').trim();
      if (!which) { e.target.value = ''; return; }
      if (!presets[which]) { toast(`No preset named "${which}"`, 'var(--bad)'); e.target.value = ''; return; }
      if (!confirm(`Delete preset "${which}" permanently?`)) { e.target.value = ''; return; }
      delete presets[which];
      _writePresets(presets);
      _populatePresetDropdown();
      toast(`Deleted "${which}"`);
      e.target.value = '';
      return;
    }
    const presets = _readPresets();
    const r = presets[v];
    if (!Array.isArray(r) || r.length === 0) { toast(`Preset "${v}" is empty`, 'var(--bad)'); e.target.value = ''; return; }
    // Pad / trim to exactly 10
    _vetoLocalRoster = Array.from({length: 10}, (_, i) => r[i] || { name: '', steam_id: '', discord_id: '' });
    _renderVeto();
    toast(`Loaded preset "${v}"`);
  });

  // v0.11.0 Layer 1B — Pull roster from a Discord voice channel.  Opens a
  // modal listing every voice channel in the operator's guild (with live
  // connected counts); operator picks one + the modal fetches the members
  // and overwrites _vetoLocalRoster with their {display_name, discord_id}
  // pairs.  SteamIDs still typed by hand (Discord doesn't expose them).
  //
  // v0.11.15: If a default voice channel is configured
  // (discord_voice_channel_id), this becomes ONE CLICK — we pull directly
  // from that VC.  The picker only opens as a fallback (no default set,
  // or the configured channel is unreachable).  Shift-click forces the
  // v0.16.1 / task #160 — Server-backed Teams modal.  Save current 5
  // players from Team A's column OR Team B's column under a name; load
  // a saved team into either column.  Survives across machines because
  // %APPDATA% persists; localStorage presets don't.
  const teamsBtn = el('veto-roster-teams');
  if (teamsBtn) teamsBtn.addEventListener('click', () => _openTeamProfilesModal());

  const rosterDiscordBtn = el('veto-roster-discord');
  if (rosterDiscordBtn) rosterDiscordBtn.addEventListener('click', async (ev) => {
    if (rosterDiscordBtn.disabled) return;       // belt-and-braces
    rosterDiscordBtn.disabled = true;
    try {
      const forcePicker = ev.shiftKey === true;
      if (forcePicker) { await _vetoOpenDiscordPullModal(); return; }
      await _vetoPullFromConfiguredVoiceOrPicker();
    } finally {
      rosterDiscordBtn.disabled = false;
    }
  });
  // v0.11.16 — mobile-safe path: an always-picker button that does NOT
  // touch the configured default.  Same disabled guard so it can't race
  // with itself or the primary 🎤 button.
  const rosterDiscordPickBtn = el('veto-roster-discord-pick');
  if (rosterDiscordPickBtn) rosterDiscordPickBtn.addEventListener('click', async () => {
    if (rosterDiscordPickBtn.disabled) return;
    rosterDiscordPickBtn.disabled = true;
    if (rosterDiscordBtn) rosterDiscordBtn.disabled = true;  // serialise with 🎤
    try {
      await _vetoOpenDiscordPullModal();
    } finally {
      rosterDiscordPickBtn.disabled = false;
      if (rosterDiscordBtn) rosterDiscordBtn.disabled = false;
    }
  });
  const saveBtn = el('veto-roster-save');
  if (saveBtn) saveBtn.addEventListener('click', async () => {
    try {
      _vetoApply(await api.veto.roster(
        el('veto-team-a-name').value,
        el('veto-team-b-name').value,
        _vetoLocalRoster,
      ));
      toast('Roster saved');
      // Reveal Distribute button (kept as a separate click so the operator
      // can review the saved roster before the random split)
      el('veto-distribute-btn').style.display = '';
      saveBtn.style.display = 'none';
    } catch (e) { toast(e.message, 'var(--bad)'); }
  });
  el('veto-distribute-btn').addEventListener('click', async () => {
    try { _vetoApply(await api.veto.distribute()); toast('Teams distributed'); }
    catch (e) { toast(e.message, 'var(--bad)'); }
  });
}

/* ── v0.11.15 — One-click roster pull from configured default VC ─────── */
//
// Tournament-night happy path: operator has configured discord_voice_channel_id
// once.  This function pulls members directly from it — no picker, no extra
// click.  Falls back to the picker modal in any failure case (no VC
// configured, channel unreachable, 0 members, fetch error) so the operator
// is never stuck.  Shift-click on the roster button bypasses this entirely
// and goes straight to the picker for tonight-only overrides.
async function _vetoPullFromConfiguredVoiceOrPicker() {
  let info;
  try {
    const r = await api.discord.voiceChannelInfo();   // no arg → server uses configured ID
    info = r.channel;
  } catch (e) {
    // v0.11.16: widen the silent-fallback regex.  The picker will fail
    // for the same reason anyway, so a toast PLUS a modal that also
    // errors is just noise.  Cases that fall through silently:
    //   "No channel ID — pass channel_id or configure …"  (no default set)
    //   "Discord guild ID not configured — set it in Config → Discord."
    //   "Discord bot is not connected"
    //   "bot module unavailable: …"
    // Anything else → still toast (genuinely unexpected error, worth surfacing).
    const msg = e.message || '';
    const expected = /No channel ID|guild ID not configured|bot is not connected|bot module unavailable/i;
    if (!expected.test(msg)) {
      toast(`Default VC unreachable — opening picker (${msg})`, 'var(--accent)');
    }
    await _vetoOpenDiscordPullModal();
    return;
  }
  if (!info || !info.id) { await _vetoOpenDiscordPullModal(); return; }
  // 0 members → fall back to picker so the operator can pick a different VC
  if (!info.member_count) {
    toast(`#${info.name} is empty — opening picker`, 'var(--accent)');
    await _vetoOpenDiscordPullModal();
    return;
  }
  // Direct pull
  try {
    const r2 = await api.discord.voiceMembers(info.id);
    const members = r2.members || [];
    if (members.length === 0) {
      toast(`#${info.name} reported empty after second check — opening picker`, 'var(--accent)');
      await _vetoOpenDiscordPullModal();
      return;
    }
    if (members.length > 10) {
      toast(`#${info.name} has ${members.length} members — only first 10 used`, 'var(--accent)');
    }
    _vetoLocalRoster = Array.from({length: 10}, (_, i) => {
      const m = members[i];
      return m
        ? { name: m.display_name || '', steam_id: m.steam_id || '',
            discord_id: m.discord_id || '' }
        : { name: '', steam_id: '', discord_id: '' };
    });
    _renderVeto();
    toast(`Pulled ${Math.min(members.length, 10)} from #${info.name} (default VC)`, 'var(--ok)');
  } catch (e) {
    toast(`Pull failed: ${e.message} — opening picker`, 'var(--bad)');
    await _vetoOpenDiscordPullModal();
  }
}

/* ── v0.11.15 — Live preview of configured default voice channel ─────── */
//
// Called on Config-tab render + after Save Discord Settings.  Asks the
// server to resolve discord_voice_channel_id via the bot and prints a
// "[name] — N connected" line so the operator gets immediate feedback
// that the bot can reach the channel.  Silent if no VC is configured.
async function _refreshVoiceChannelPreview() {
  const inp    = el('cfg-discord-voice-channel-id');
  const status = el('cfg-discord-voice-status');
  if (!inp || !status) return;
  const id = (inp.value || '').trim();
  if (!id) {
    status.textContent = 'No default VC set — Veto roster modal will show the channel picker each session.';
    return;
  }
  status.textContent = 'Looking up channel…';
  try {
    const r = await api.discord.voiceChannelInfo(id);
    const ch = r.channel || {};
    const ok = (ch.member_count === 10) ? ' ✓' : '';
    status.innerHTML =
      `Default VC: <strong>#${esc(ch.name || '?')}</strong> — ${ch.member_count || 0} connected${ok}`;
    status.style.color = (ch.member_count === 10) ? 'var(--ok)' : 'var(--text-3)';
  } catch (e) {
    status.innerHTML =
      `<span style="color:var(--bad)">Could not reach channel</span> — ${esc(e.message || 'unknown error')}`;
  }
}

/* ── v0.11.0 Layer 1B — Discord voice-channel roster pull modal ───────── */
//
// v0.11.15: opts.pickOnly + opts.onPick let the Config card reuse this same
// modal as an "ID browser" (clicking a channel returns its {id, name,
// member_count} via onPick instead of pulling members).  Defaults preserve
// the original Layer 1B roster-pull behaviour.
//
// v0.11.18: opts.kind = 'voice' (default) | 'text'.  With kind='text', the
// modal fetches text channels via api.discord.textChannels() and renders
// them without member counts.  pickOnly is enforced for text mode (you
// can't "pull members" from a text channel).
async function _vetoOpenDiscordPullModal(opts) {
  opts = opts || {};
  const kind = opts.kind === 'text' ? 'text' : 'voice';
  // Text mode is browse-only by construction
  const pickOnly = (kind === 'text') ? true : !!opts.pickOnly;
  const onPick   = typeof opts.onPick === 'function' ? opts.onPick : null;
  // Build the modal shell.  Removed on close to keep the DOM clean.
  let modal = el('veto-discord-pull-modal');
  if (modal) modal.remove();
  modal = document.createElement('div');
  modal.id = 'veto-discord-pull-modal';
  modal.className = 'veto-modal-backdrop';
  let title;
  if (kind === 'text')   title = '🔍 Pick a text channel';
  else if (pickOnly)     title = '🔍 Pick a voice channel';
  else                   title = '🎤 Pull from voice channel';
  modal.innerHTML = `
    <div class="veto-modal" role="dialog" aria-label="${title}">
      <div class="veto-modal-head">
        <h2>${title}</h2>
        <button class="veto-modal-close" aria-label="Close">×</button>
      </div>
      <div class="veto-modal-body" id="veto-pull-body">
        <div style="text-align:center;padding:30px;color:var(--text-3)">
          Loading channels…
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  const close = () => { try { modal.remove(); } catch(_){} };
  modal.querySelector('.veto-modal-close').addEventListener('click', close);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) close();      // backdrop click closes
  });

  // Fetch + render channel list — pick the right API by kind
  try {
    const r = (kind === 'text')
      ? await api.discord.textChannels()
      : await api.discord.voiceChannels();
    const channels = r.channels || [];
    const body = el('veto-pull-body');
    if (channels.length === 0) {
      const what = (kind === 'text') ? 'text' : 'voice';
      body.innerHTML = `
        <div style="padding:24px;text-align:center;color:var(--text-3)">
          No ${what} channels found in this server.  Check that the bot has
          <strong>View Channels</strong> permission.
        </div>
      `;
      return;
    }
    let intro;
    if (kind === 'text') {
      intro = 'Pick the text channel where the bot should post the live veto embed.';
    } else if (pickOnly) {
      intro = 'Pick a voice channel to set as the default for one-click roster pull.  Member counts shown are live.';
    } else {
      intro = 'Pick a voice channel.  The connected members will overwrite your current roster (names + Discord IDs).  Need exactly 10 connected.';
    }
    body.innerHTML = `
      <div style="color:var(--text-3);font-size:13px;margin-bottom:14px">
        ${intro}
      </div>
      <div class="veto-pull-channel-list">
        ${channels.map(ch => {
          if (kind === 'text') {
            // v0.11.18 — text channels: no member_count, always enabled.
            // Use a leading # to make Discord conventions obvious.
            return `
              <button class="veto-pull-channel"
                      data-channel-id="${esc(ch.id)}"
                      data-channel-name="${esc(ch.name)}"
                      data-channel-count="0">
                <span class="veto-pull-channel-name">#${esc(ch.name)}</span>
              </button>
            `;
          }
          // Voice channels: keep the existing renderer with live counts.
          return `
            <button class="veto-pull-channel ${ch.member_count === 10 ? 'ready' : (ch.member_count >= 1 ? 'has' : '')}"
                    data-channel-id="${esc(ch.id)}"
                    data-channel-name="${esc(ch.name)}"
                    data-channel-count="${ch.member_count}"
                    ${(!pickOnly && ch.member_count === 0) ? 'disabled' : ''}>
              <span class="veto-pull-channel-name">${esc(ch.name)}</span>
              <span class="veto-pull-channel-count">
                ${ch.member_count} ${ch.member_count === 1 ? 'member' : 'members'}
                ${ch.member_count === 10 ? ' ✓' : ''}
              </span>
            </button>
          `;
        }).join('')}
      </div>
    `;
    // Wire each channel button.
    //   pickOnly mode → invoke onPick({id, name, member_count}) + close.
    //   roster-pull mode (default) → fetch members + overwrite roster + close.
    body.querySelectorAll('.veto-pull-channel').forEach(btn => {
      btn.addEventListener('click', async () => {
        const channelId    = btn.dataset.channelId;
        const channelName  = btn.dataset.channelName;
        const channelCount = parseInt(btn.dataset.channelCount, 10) || 0;
        if (pickOnly) {
          if (onPick) onPick({ id: channelId, name: channelName, member_count: channelCount });
          close();
          return;
        }
        btn.textContent = '… loading members';
        try {
          const r2 = await api.discord.voiceMembers(channelId);
          const members = r2.members || [];
          if (members.length === 0) {
            toast('Channel has no connected members', 'var(--bad)');
            close();
            return;
          }
          if (members.length > 10) {
            toast(`Channel has ${members.length} members — only the first 10 will be used`,
                  'var(--accent)');
          }
          // Overwrite local roster.  Pad to 10 with empty slots if fewer
          // than 10 are connected so the operator can still see all rows.
          _vetoLocalRoster = Array.from({length: 10}, (_, i) => {
            const m = members[i];
            return m
              ? { name: m.display_name || '', steam_id: m.steam_id || '',
                  discord_id: m.discord_id || '' }
              : { name: '', steam_id: '', discord_id: '' };
          });
          close();
          _renderVeto();
          toast(`Pulled ${Math.min(members.length, 10)} members from #${channelName}`,
                'var(--ok)');
        } catch (err) {
          toast(`Pull failed: ${err.message}`, 'var(--bad)');
          close();
        }
      });
    });
  } catch (err) {
    el('veto-pull-body').innerHTML = `
      <div style="padding:24px;text-align:center;color:var(--bad)">
        ${esc(err.message || 'Failed to fetch voice channels')}
      </div>
      <div style="padding:0 24px 24px;text-align:center;color:var(--text-4);font-size:11px">
        Need help?  See <a href="https://github.com/oblivion-systems/OblivionServerTool/blob/master/DISCORD.md">DISCORD.md</a>
        for permissions + setup.
      </div>
    `;
  }
}

/* ── v0.11.0 polish — "Go Online" banner ─────────────────────────────────
 * Renders inside the Veto-idle stage.  Pulls /api/config to check whether
 * the operator has set `public_share_url` (their Cloudflare tunnel URL,
 * typically).  Without it, captain links are LAN-only — fine on a LAN
 * party, fatal when the captain is at home.
 *
 * Three states:
 *   - online: green + masked URL + copy button + "open" link
 *   - lan-only: yellow + "Configure" jump-to-config link
 *   - error: red + the error (only on /api/config failure — rare)
 *
 * Fire-and-forget; if the Veto tab re-renders while we're loading, the
 * old element is just orphaned (the next call paints into the new node).
 */
async function _vetoRenderOnlineBanner() {
  const node = el('veto-online-banner');
  if (!node) return;
  try {
    const cfg = await api.config();
    const url = (cfg.public_share_url || '').trim();
    if (url) {
      node.className = 'veto-online-banner veto-online-banner--online';
      node.innerHTML = `
        <div class="veto-online-row">
          <span class="veto-online-icon">🌐</span>
          <span class="veto-online-label">Online · captain links use</span>
          <code class="veto-online-url">${esc(url)}</code>
          <button class="btn btn-ghost btn-sm" id="veto-online-copy">Copy</button>
          <a class="btn btn-ghost btn-sm" href="${esc(url)}" target="_blank" rel="noopener">Open ↗</a>
        </div>
      `;
      const copyBtn = el('veto-online-copy');
      if (copyBtn) copyBtn.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(url);
          toast('Public URL copied');
        } catch (_e) {
          toast('Copy failed (HTTPS required for clipboard)', 'var(--bad)');
        }
      });
    } else {
      node.className = 'veto-online-banner veto-online-banner--lan';
      node.innerHTML = `
        <div class="veto-online-row">
          <span class="veto-online-icon">📡</span>
          <span class="veto-online-label">
            <strong>LAN-only.</strong>
            Captain links will use your local IP — fine for a LAN party,
            but won't work if a captain is at home.
          </span>
          <button class="btn btn-ghost btn-sm" id="veto-online-configure">
            Configure public URL →
          </button>
        </div>
      `;
      const cfgBtn = el('veto-online-configure');
      if (cfgBtn) cfgBtn.addEventListener('click', () => {
        // Switch to the Config tab; the input is `cfg-public-share-url`.
        const cfgTab = document.querySelector('.nav-item[data-tab="config"]');
        if (cfgTab) cfgTab.click();
        setTimeout(() => {
          const inp = el('cfg-public-share-url');
          if (inp) { inp.focus(); inp.scrollIntoView({behavior: 'smooth', block: 'center'}); }
        }, 200);
      });
    }
  } catch (err) {
    node.className = 'veto-online-banner veto-online-banner--err';
    node.textContent = `Couldn't read share-URL config: ${err.message || err}`;
  }
}

/* ── v0.11.0 polish — Spectator URL modal ────────────────────────────────
 * Issues a read-only spectator token via POST /api/veto/spectator and
 * shows the URL + Copy + Rotate buttons.  Token is per-session; the
 * URL stays valid until reset() or rotate.
 */
async function _vetoOpenSpectatorModal() {
  let modal = el('veto-spectator-modal');
  if (modal) modal.remove();
  modal = document.createElement('div');
  modal.id = 'veto-spectator-modal';
  modal.className = 'veto-modal-backdrop';
  modal.innerHTML = `
    <div class="veto-modal" role="dialog" aria-label="Spectator URL">
      <div class="veto-modal-head">
        <h2>📺 Spectator URL</h2>
        <button class="veto-modal-close" aria-label="Close">×</button>
      </div>
      <div class="veto-modal-body" id="veto-spectator-body">
        <div style="text-align:center;padding:30px;color:var(--text-3)">Issuing…</div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  const close = () => { try { modal.remove(); } catch(_){} };
  modal.querySelector('.veto-modal-close').addEventListener('click', close);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });

  async function _issue(rotate) {
    const body = el('veto-spectator-body');
    if (!body) return;
    body.innerHTML = `<div style="text-align:center;padding:30px;color:var(--text-3)">${rotate?'Rotating':'Issuing'}…</div>`;
    try {
      const r = await api.veto.issueSpectator(rotate);
      const u = r.urls || {};
      body.innerHTML = `
        <div style="color:var(--text-3);font-size:12px;margin-bottom:14px;line-height:1.5">
          A read-only link your casters / observers can load.  Refreshes every 3s.
          ${rotate ? '<br><strong style="color:var(--ok)">Old link is now dead.</strong>' : ''}
        </div>
        ${u.public ? `
          <div class="veto-spec-row">
            <label>Public (share-URL based)</label>
            <input class="input" type="text" id="veto-spec-public" value="${esc(u.public)}" readonly>
            <button class="btn btn-ghost btn-sm" data-copy="veto-spec-public">Copy</button>
            <a class="btn btn-ghost btn-sm" href="${esc(u.public)}" target="_blank" rel="noopener">Open ↗</a>
          </div>
        ` : ''}
        <div class="veto-spec-row">
          <label>LAN</label>
          <input class="input" type="text" id="veto-spec-lan" value="${esc(u.lan || '')}" readonly>
          <button class="btn btn-ghost btn-sm" data-copy="veto-spec-lan">Copy</button>
          <a class="btn btn-ghost btn-sm" href="${esc(u.lan || '')}" target="_blank" rel="noopener">Open ↗</a>
        </div>
        <div style="margin-top:16px;display:flex;gap:8px;justify-content:flex-end">
          <button class="btn btn-ghost btn-sm" id="veto-spec-rotate"
                  title="Mint a fresh token; the old link will stop working">
            🔄 Rotate token
          </button>
        </div>
        <div style="font-size:11px;color:var(--text-4);margin-top:14px;line-height:1.5">
          The spectator view masks SteamIDs (first 4 + last 4) and omits
          Discord IDs.  It does NOT include captain claim tokens — viewers
          can watch but cannot interact.
        </div>
      `;
      body.querySelectorAll('button[data-copy]').forEach(btn => {
        btn.addEventListener('click', async () => {
          const inp = el(btn.dataset.copy);
          if (!inp) return;
          try { await navigator.clipboard.writeText(inp.value); toast('Copied'); }
          catch (_e) { inp.select(); document.execCommand('copy'); toast('Copied (fallback)'); }
        });
      });
      const rotateBtn = el('veto-spec-rotate');
      if (rotateBtn) rotateBtn.addEventListener('click', () => {
        if (confirm('Rotate the spectator token? The current link will stop working immediately.')) _issue(true);
      });
    } catch (err) {
      body.innerHTML = `<div style="color:var(--bad);padding:20px;text-align:center">${esc(err.message || 'Failed to issue token')}</div>`;
    }
  }
  _issue(false);
}

/* ── v0.11.0 polish — Match history modal ────────────────────────────────
 * Reads /api/veto/history (last 10 completed sessions, newest last) +
 * renders a scrollable list.  Each entry shows date + teams + mode +
 * decider + final maplist.  Useful for "what did we play last week"
 * and as a base for future per-captain stats. */
async function _vetoOpenHistoryModal() {
  let modal = el('veto-history-modal');
  if (modal) modal.remove();
  modal = document.createElement('div');
  modal.id = 'veto-history-modal';
  modal.className = 'veto-modal-backdrop';
  modal.innerHTML = `
    <div class="veto-modal" role="dialog" aria-label="Match history">
      <div class="veto-modal-head">
        <h2>📜 Match history</h2>
        <button class="veto-modal-close" aria-label="Close">×</button>
      </div>
      <div class="veto-modal-body" id="veto-history-body">
        <div style="text-align:center;padding:30px;color:var(--text-3)">
          Loading…
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  const close = () => { try { modal.remove(); } catch(_){} };
  modal.querySelector('.veto-modal-close').addEventListener('click', close);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });

  try {
    const r = await api.veto.history();
    const matches = (r.matches || []).slice().reverse();      // newest first
    const body = el('veto-history-body');
    if (matches.length === 0) {
      body.innerHTML = `
        <div style="padding:24px;text-align:center;color:var(--text-3)">
          No matches in history yet.<br>
          <small style="color:var(--text-4);font-size:11px;display:block;margin-top:8px">
            Completed BO sessions are persisted to
            <code>%APPDATA%\\Oblivion Server Tool\\oblivion_matches.json</code>
            (last 10 kept).
          </small>
        </div>
      `;
      return;
    }
    body.innerHTML = `
      <div style="color:var(--text-3);font-size:12px;margin-bottom:14px">
        ${matches.length} match${matches.length === 1 ? '' : 'es'} on record (newest first).
      </div>
      <div class="veto-history-list">
        ${matches.map(m => _renderHistoryEntry(m)).join('')}
      </div>
    `;
  } catch (err) {
    el('veto-history-body').innerHTML = `
      <div style="padding:24px;text-align:center;color:var(--bad)">
        ${esc(err.message || 'Failed to load history')}
      </div>
    `;
  }
}

function _renderHistoryEntry(m) {
  // m: {matchid, created_at, mode, team_a:{name,players}, team_b:{...},
  //     captain_a, captain_b, final_maps, decider, sequence}
  const when = m.created_at
    ? new Date(m.created_at * 1000).toLocaleString()
    : '(unknown date)';
  const teamAName = (m.team_a || {}).name || 'A';
  const teamBName = (m.team_b || {}).name || 'B';
  const maps = m.final_maps || [];
  const decider = m.decider || '';
  return `
    <div class="veto-history-entry">
      <div class="veto-history-head">
        <div class="veto-history-mode">${esc(m.mode || '?')}</div>
        <div class="veto-history-when">${esc(when)}</div>
      </div>
      <div class="veto-history-teams">
        <strong>${esc(teamAName)}</strong>
        <span style="color:var(--text-4);margin:0 8px">vs</span>
        <strong>${esc(teamBName)}</strong>
      </div>
      <div class="veto-history-captains">
        Captains: <code>${esc(m.captain_a || '?')}</code> / <code>${esc(m.captain_b || '?')}</code>
      </div>
      <div class="veto-history-maps">
        ${maps.map((map, i) => `
          <span class="veto-history-map ${map === decider ? 'decider' : ''}">
            ${map === decider ? '🏁 ' : `${i+1}. `}${esc(map)}
          </span>
        `).join('')}
      </div>
      <div class="veto-history-matchid">${esc(m.matchid || '?')}</div>
    </div>
  `;
}

/* ── Teams ─────────────────────────────────────────────────────────────── */
function _renderVetoTeams(root, sess) {
  const playerRow = (p) => `<div class="veto-team-player">${esc(p.name)}</div>`;
  root.innerHTML = `
    <div class="veto-stage">
      <div class="veto-stage-head"><h2>Teams</h2>
        <span class="sub">Random 5-5 split · re-shuffle if you want a different draw</span></div>
      <div class="veto-teams">
        <div class="veto-team-col a">
          <h3>${esc(sess.team_a_name)}</h3>
          <div class="veto-team-list">${(sess.team_a || []).map(playerRow).join('')}</div>
        </div>
        <div class="veto-team-col b">
          <h3>${esc(sess.team_b_name)}</h3>
          <div class="veto-team-list">${(sess.team_b || []).map(playerRow).join('')}</div>
        </div>
      </div>
      <div class="veto-stage-actions">
        <button class="btn btn-ghost" id="veto-reshuffle-btn">Re-shuffle</button>
        <!-- v0.12.0 — Move teams to Discord VCs.  Button always visible
             on the Teams stage; backend refuses cleanly if VCs aren't
             configured or bot isn't connected, surfaced via the catch's
             toast.  Saves the operator from having to leave the Veto tab
             to verify Discord config. -->
        <button class="btn btn-ghost" id="veto-move-teams-btn"
                title="Move every rostered player with a discord_id into their team's voice channel">
          🔀 Move teams to VCs
        </button>
        <div class="spacer"></div>
        <button class="btn btn-accent" id="veto-to-vote-btn">Vote for captains →</button>
      </div>
      <!-- v0.12.3 / task #135 — Remote voting links.  Only meaningful
           AFTER advancing to the voting stage (issue_voter_tokens checks
           state).  Button always visible here so operator can stage the
           tokens + DM all players in one click before clicking "Vote for
           captains →".  Backend refuses with a clean error if state
           isn't voting yet. -->
      <div class="veto-stage-actions" style="margin-top:8px">
        <span style="color:var(--text-4);font-family:var(--font-mono);font-size:11px">
          Remote players can vote via DM — click after advancing to the voting stage:
        </span>
        <div class="spacer"></div>
        <button class="btn btn-ghost" id="veto-voter-dm-btn"
                title="DM each rostered player with a discord_id a personal one-shot voting link">
          📨 DM voting links to all 10
        </button>
      </div>
    </div>
  `;
  el('veto-reshuffle-btn').addEventListener('click', async () => {
    try { _vetoApply(await api.veto.distribute()); toast('Re-shuffled'); }
    catch (e) { toast(e.message, 'var(--bad)'); }
  });
  el('veto-to-vote-btn').addEventListener('click', async () => {
    try { _vetoApply(await api.veto.startVoting()); }
    catch (e) { toast(e.message, 'var(--bad)'); }
  });
  // v0.12.3 / task #135 — DM voter links to all 10 players.  Behind the
  // scenes: starts voting (if needed) → mints 10 tokens → DMs each
  // discord_id → SPA refreshes via _vetoApply with the new voter_tokens
  // map so the operator can see who's claimed.
  el('veto-voter-dm-btn').addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true; const orig = btn.textContent;
    btn.textContent = 'DM-ing…';
    try {
      // If we're on `teams`, advance to `voting` first — voter_tokens
      // only mints in `voting` state.  Auto-advance saves the operator
      // a click; if the operator wanted to delay, they can hit Reset.
      const curState = (_vetoState && _vetoState.state) || '';
      if (curState === 'teams') {
        _vetoApply(await api.veto.startVoting());
      }
      const r = await api.veto.voterTokens();
      const voters = r && r.voters || {};
      const total = Object.keys(voters).length;
      const sent  = Object.values(voters).filter(v => v.dm_sent).length;
      const noid  = Object.values(voters).filter(v => !v.discord_id).length;
      const msg = `DM'd ${sent}/${total} voters`
                  + (noid ? ` (${noid} skipped — no discord_id)` : '');
      toast(msg, sent > 0 ? 'var(--ok)' : 'var(--accent)');
      // Force a state refresh so the voter_tokens_claimed flags land.
      _vetoApply(await api.veto.state());
    } catch (e) {
      toast(e.message, 'var(--bad)');
    } finally {
      btn.disabled = false; btn.textContent = orig;
    }
  });

  // v0.12.0 — Move teams to Discord VCs.
  el('veto-move-teams-btn').addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true; const orig = btn.textContent;
    btn.textContent = 'Moving…';
    try {
      const r = await api.discord.moveTeams();
      const skippedMsg = r.skipped ? `, ${r.skipped} not in VC` : '';
      const errMsg     = r.errors && r.errors.length
                          ? ` — ${r.errors.length} error(s)` : '';
      toast(`Moved A ${r.moved_a}, B ${r.moved_b}${skippedMsg}${errMsg}`,
            r.errors && r.errors.length ? 'var(--accent)' : 'var(--ok)');
      if (r.errors && r.errors.length) {
        // Surface individual error messages in the log so the operator
        // can chase up specific players ("X: missing Move Members perm")
        r.errors.forEach(e => console.warn('[move_teams]', e));
      }
    } catch (e) {
      toast(e.message, 'var(--bad)');
    } finally {
      btn.disabled = false; btn.textContent = orig;
    }
  });
}

/* ── Voting ────────────────────────────────────────────────────────────── */
function _renderVetoVoting(root, sess) {
  const teamVoteCard = (team, teamLetter) => {
    const players = team === 'A' ? sess.team_a : sess.team_b;
    const votes   = team === 'A' ? sess.votes_a : sess.votes_b;
    const name    = team === 'A' ? sess.team_a_name : sess.team_b_name;
    const rows = players.map((voter, vi) => {
      const buttons = players.map((votee, ti) => `
        <div class="veto-vote-btn ${votes[vi]===ti?'voted':''}" data-team="${team}" data-vi="${vi}" data-ti="${ti}">
          ${esc(votee.name)}
        </div>
      `).join('');
      return `<div class="veto-vote-row">
        <span class="veto-voter">${esc(voter.name)}:</span>
        <div class="veto-vote-btns">${buttons}</div>
      </div>`;
    }).join('');
    return `
      <div class="veto-team-col ${teamLetter}">
        <h3>${esc(name)} — votes (${Object.keys(votes).length}/5)</h3>
        <div class="veto-vote-card">${rows}</div>
      </div>
    `;
  };
  const allIn = Object.keys(sess.votes_a).length === 5 && Object.keys(sess.votes_b).length === 5;
  root.innerHTML = `
    <div class="veto-stage">
      <div class="veto-stage-head"><h2>Captain vote</h2>
        <span class="sub">Each player picks their captain · ties trigger a revote on that side</span></div>
      <div class="veto-teams">
        ${teamVoteCard('A', 'a')}
        ${teamVoteCard('B', 'b')}
      </div>
      ${sess.revote_count > 0 ? `<div class="veto-vote-tally" style="text-align:center;color:var(--accent)">Revote #${sess.revote_count}</div>` : ''}
      <div class="veto-stage-actions">
        <div class="spacer"></div>
        <button class="btn btn-accent" id="veto-resolve-btn" ${allIn?'':'disabled'}>Resolve captains →</button>
      </div>
    </div>
  `;
  document.querySelectorAll('.veto-vote-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      const t = e.target.dataset.team;
      const vi = parseInt(e.target.dataset.vi, 10);
      const ti = parseInt(e.target.dataset.ti, 10);
      try { _vetoApply(await api.veto.vote(t, vi, ti)); }
      catch (err) { toast(err.message, 'var(--bad)'); }
    });
  });
  el('veto-resolve-btn').addEventListener('click', async () => {
    try {
      const r = _vetoApply(await api.veto.resolve());
      if (r.outcome === 'elected') toast('Captains elected');
      else toast(`Tie — ${r.outcome} (revote required)`, 'var(--accent)');
    } catch (e) { toast(e.message, 'var(--bad)'); }
  });
}

/* ── Links ─────────────────────────────────────────────────────────────── */
let _vetoTokenUrls = null;   // cache so re-renders don't drop the URLs
function _renderVetoLinks(root, sess) {
  const claimedA = !!(sess.tokens_claimed && sess.tokens_claimed.A);
  const claimedB = !!(sess.tokens_claimed && sess.tokens_claimed.B);
  const captainA = sess.team_a[sess.captain_a_idx];
  const captainB = sess.team_b[sess.captain_b_idx];

  if (!_vetoTokenUrls) {
    // Hasn't issued tokens yet
    root.innerHTML = `
      <div class="veto-stage">
        <div class="veto-stage-head"><h2>Captains elected</h2>
          <span class="sub">${esc(captainA?.name || 'A')} and ${esc(captainB?.name || 'B')} will run the veto</span></div>
        <div style="text-align:center;padding:18px">
          <button class="btn btn-accent" id="veto-issue-tokens">Generate captain links →</button>
        </div>
      </div>
    `;
    el('veto-issue-tokens').addEventListener('click', async () => {
      try { _vetoTokenUrls = await api.veto.tokens(); _renderVeto(); }
      catch (e) { toast(e.message, 'var(--bad)'); }
    });
    return;
  }

  const card = (team, captain, claimed) => {
    const teamLetter = team.toLowerCase();
    const urls = _vetoTokenUrls[team];
    // QR codes: SVG fetched from /api/veto/qr (server caches via Cache-Control).
    // The same token URL is stable for the life of the token, so the browser
    // re-uses the SVG across re-renders.  We show the LAN QR by default and
    // the Public QR only if a public IP is configured — captain on phone
    // scans whichever applies to their network.
    const qrLan    = api.veto.qrUrl(urls.token, 'lan');
    const qrPublic = urls.public ? api.veto.qrUrl(urls.token, 'public') : null;
    // v0.11.0: dm_sent comes from /api/veto/tokens response when the bot
    // auto-DM'd the captain.  Shown as a small "DM sent" pill so the
    // operator knows whether to also paste the link manually.
    const dmSent = !!urls.dm_sent;
    return `
      <div class="veto-link-card ${teamLetter}">
        ${claimed ? '<div class="veto-claimed-pill">CLAIMED</div>' : ''}
        ${dmSent && !claimed ? '<div class="veto-dm-pill">📨 DM SENT</div>' : ''}
        <h3>${esc(team === 'A' ? sess.team_a_name : sess.team_b_name)}</h3>
        <div class="veto-cap-name">Captain: <strong>${esc(captain?.name || '—')}</strong></div>
        <div class="veto-qr-row">
          <div class="veto-qr-slot">
            <img class="veto-qr" src="${qrLan}" alt="LAN QR" loading="lazy">
            <div class="veto-qr-label">LAN</div>
          </div>
          ${qrPublic ? `
          <div class="veto-qr-slot">
            <img class="veto-qr" src="${qrPublic}" alt="Public QR" loading="lazy">
            <div class="veto-qr-label">Public</div>
          </div>` : ''}
        </div>
        <div class="veto-link-url-row">
          <label>LAN</label>
          <div class="veto-url" title="${esc(urls.lan)}">${esc(urls.lan)}</div>
          <button class="btn btn-ghost btn-sm" data-copy="${esc(urls.lan)}">Copy</button>
        </div>
        ${urls.public ? `
        <div class="veto-link-url-row">
          <label>Public</label>
          <div class="veto-url" title="${esc(urls.public)}">${esc(urls.public)}</div>
          <button class="btn btn-ghost btn-sm" data-copy="${esc(urls.public)}">Copy</button>
        </div>` : ''}
        <div class="veto-link-actions">
          <button class="btn btn-ghost btn-sm" data-copy-discord="${team}"
                  title="Copy a formatted message ready to paste into Discord DM">
            ${urls.public ? '📋 Copy for Discord' : '📋 Copy for Discord (LAN only)'}
          </button>
          <button class="btn btn-ghost btn-sm" data-revoke="${team}">Revoke + reissue</button>
        </div>
      </div>
    `;
  };
  root.innerHTML = `
    <div class="veto-stage">
      <div class="veto-stage-head"><h2>Captain links</h2>
        <span class="sub">Send each captain their link · single-use · LAN for in-house, Public if remote</span></div>
      <div class="veto-link-cards">
        ${card('A', captainA, claimedA)}
        ${card('B', captainB, claimedB)}
      </div>
      <div class="veto-stage-actions">
        <div class="spacer"></div>
        <span style="color:var(--text-3);font-family:var(--font-mono);font-size:11px">
          Waiting for both captains to claim (${(claimedA?1:0)+(claimedB?1:0)}/2)...
        </span>
      </div>
    </div>
  `;
  document.querySelectorAll('[data-copy]').forEach(b => {
    b.addEventListener('click', () => copyText(b.dataset.copy, 'Captain link'));
  });
  // v0.10.1: Discord-friendly copy — bundles team + captain + URL + a short
  // intro in one paste-ready block.  Prefers the Public URL (online captains)
  // but falls back to LAN if no public is configured (lets operators still
  // use it on LAN).  No actual Discord integration here — operator pastes
  // into whatever DM channel they want.  Real Discord-bot delivery is the
  // v0.11.0 Layer 1 work.
  document.querySelectorAll('[data-copy-discord]').forEach(b => {
    b.addEventListener('click', () => {
      const team = b.dataset.copyDiscord;
      const urls = _vetoTokenUrls[team];
      const teamName = (team === 'A' ? sess.team_a_name : sess.team_b_name);
      const capObj   = (team === 'A' ? captainA : captainB);
      const capName  = capObj?.name || `Captain ${team}`;
      const url      = urls.public || urls.lan;
      const text = `🎯 ${capName} (${teamName}) — your veto link:\n${url}\nSingle-use. Click to claim your captain seat.`;
      copyText(text, 'Discord-ready message');
    });
  });
  document.querySelectorAll('[data-revoke]').forEach(b => {
    b.addEventListener('click', async (e) => {
      if (!confirm(`Revoke captain ${e.target.dataset.revoke}'s token and issue a new one?`)) return;
      try {
        const r = await api.veto.revokeToken(e.target.dataset.revoke);
        _vetoTokenUrls = _vetoTokenUrls || {};
        // Merge token + urls into the same shape as /api/veto/tokens so
        // the renderer (which reads urls.token for the QR) keeps working.
        _vetoTokenUrls[r.team] = { token: r.token, ...r.urls };
        _renderVeto();
        toast(`New token issued for team ${r.team}`);
      } catch (err) { toast(err.message, 'var(--bad)'); }
    });
  });
}

/* ── Veto board ────────────────────────────────────────────────────────── */
function _renderVetoBoard(root, sess) {
  const step = sess.current_step_detail;
  const legal = new Set(sess.legal_moves || []);
  const banned = new Set();
  const picked = new Map();   // map -> 'pick'
  const seq = sess.sequence || [];
  seq.forEach(st => {
    if (!st.map_id) return;
    if (st.kind === 'BAN') banned.add(st.map_id);
    else if (st.kind === 'PICK') picked.set(st.map_id, st.team);
  });

  // ── Day 5: detect the most-recently-acted-on map ─────────────────────
  // If the sequence has grown since the last render, the new last filled
  // entry is the fresh stamp.  Only that map's stamp + card get the
  // slam-in animation — every other already-banned map stays static.
  // After this render we update _vetoLastSeqLen so a subsequent re-render
  // (e.g. an SSE ping that doesn't include a new step) won't re-trigger.
  const filledCount = seq.filter(st => st.map_id).length;
  let justStampedMap = null;
  if (filledCount > _vetoLastSeqLen) {
    // Walk the filled steps to find the last one's map.
    for (let i = seq.length - 1; i >= 0; i--) {
      if (seq[i].map_id) { justStampedMap = seq[i].map_id; break; }
    }
  }
  _vetoLastSeqLen = filledCount;

  const teamName = (t) => t === 'A' ? sess.team_a_name : sess.team_b_name;

  const card = (m) => {
    const isBanned = banned.has(m);
    const isPicked = picked.has(m);
    const isLegal  = legal.has(m);
    const isFresh  = (m === justStampedMap);
    const cls = ['veto-map-card'];
    if (isBanned) cls.push('banned');
    if (isPicked) cls.push('picked');
    if (!isBanned && !isPicked && !isLegal) cls.push('illegal');
    if (isFresh) cls.push('just-stamped');
    const stampCls = isFresh ? 'just-stamped' : '';
    return `
      <div class="${cls.join(' ')}" data-map="${esc(m)}">
        <img class="veto-map-thumb" src="/api/maps/thumb/${esc(m)}"
             onerror="this.style.display='none'">
        <div class="veto-map-name">${esc(m)}</div>
        ${isBanned ? `<div class="veto-map-stamp ban ${stampCls}">BAN</div>` : ''}
        ${isPicked ? `<div class="veto-map-stamp pick ${stampCls}">PICK ${esc(picked.get(m))}</div>` : ''}
      </div>
    `;
  };

  root.innerHTML = `
    <div class="veto-stage">
      ${step ? `
      <div class="veto-turn-banner">
        <div class="veto-turn-team">${esc(teamName(step.team))}</div>
        <div class="veto-turn-kind">${esc(step.kind)} a map · step ${step.index + 1} of ${sess.sequence.length}</div>
      </div>` : ''}
      <div class="veto-board">${(sess.map_pool || []).map(card).join('')}</div>
      <div class="veto-stage-actions">
        <span style="color:var(--text-3);font-family:var(--font-mono);font-size:11px">
          Admins can click for any team; captains can only click during their own turn.
        </span>
      </div>
    </div>
  `;
  document.querySelectorAll('.veto-map-card').forEach(c => {
    if (c.classList.contains('banned') || c.classList.contains('picked')
        || c.classList.contains('illegal')) return;
    c.addEventListener('click', async () => {
      // v0.11.17 B4 — guard against the SSE-rebuild-mid-click race:
      //   1. Set the global flag so _renderVeto() skips the board.
      //   2. Mark the tapped card as "pending" + visually disable all
      //      other cards so a frustrated double-tap can't land on a
      //      different map.
      //   3. Clear the flag in `finally` so any state arriving via SSE
      //      during the request gets rendered on the next trigger.
      if (_vetoBoardClickInFlight) return;     // belt-and-braces
      _vetoBoardClickInFlight = true;
      // v0.11.27 — wrap EVERYTHING (sync visual setup + async API call)
      // in one try/finally so a synchronous throw during DOM marking
      // (e.g. forEach raising in an extension-instrumented webview) can
      // no longer leave _vetoBoardClickInFlight stuck True forever.
      // Audit finding #8.  Without this, the polling fallback dead-locks
      // (it skips while flag is True) and every subsequent click no-ops.
      try {
        // Visual: dim siblings, mark this card as pending.
        document.querySelectorAll('.veto-map-card').forEach(other => {
          if (other === c) other.classList.add('pending');
          else              other.classList.add('locked-during-pending');
        });
        const mapId = c.dataset.map;
        const team  = step?.team;
        if (!team) return;
        try {
          _vetoApply(await api.veto.step(team, mapId));
          // success path: _vetoApply already re-rendered with the fresh snap
        } catch (e) {
          toast(e.message, 'var(--bad)');
          // error path: state didn't change, but we must re-render to clear
          // the .pending pulse + .locked-during-pending dim on siblings.
          // v0.11.26 — was previously in `finally`, causing a double-render
          // on the success path (audit finding #4).
          _renderVeto();
        }
      } finally {
        _vetoBoardClickInFlight = false;
      }
    });
  });
}

/* ── Finale ────────────────────────────────────────────────────────────── */
function _renderVetoFinale(root, sess) {
  // v0.10.1: captain role sees a different finale — own Ready toggle,
  // status of opponent, NO matchzy launch button (admin owns the trigger).
  // state_ is the global server-status snapshot exposing role.
  const isCap = (state_ && state_.server && state_.server.role === 'captain');
  if (isCap) return _renderVetoFinaleCaptain(root, sess);

  const maps = sess.final_maps || [];
  const decider = sess.decider;
  // v0.10.1: ready state lives on the snapshot (ready_a / ready_b / both_ready)
  const readyA = !!sess.ready_a;
  const readyB = !!sess.ready_b;
  const bothReady = !!sess.both_ready;
  // v0.10.2: connect details for the admin's preview of what captains see
  const conn = sess.match_connect;
  // ── Day 5: cinematic entry only the first time we land on finale ─────
  // SSE pings on the same state would otherwise re-trigger the confetti
  // and decider zoom every few seconds — once is exactly enough.
  const playEntry = !_vetoFinaleShownThisSession;
  _vetoFinaleShownThisSession = true;
  const entryCls = playEntry ? 'veto-finale-enter' : '';
  // v0.10.0.1 vibe rewrite: streaks not party-confetti.  18 narrow
  // accent-coloured vertical tracers falling straight down at varying
  // speeds.  Fewer pieces than the rainbow version because each carries
  // more visual weight via its gradient comet-tail and the variance
  // baked into nth-child rules in CSS.  Reads as digital tracer rounds /
  // data stream — gamier than party confetti, lighter on GPU than 30
  // rotating coloured rectangles.
  const confetti = playEntry ? `
    <div class="veto-confetti" aria-hidden="true">
      ${Array.from({length: 18}).map((_, i) =>
        `<div class="veto-confetti-piece" style="left:${(i*5.5 + (i*13) % 7).toFixed(1)}%;animation-delay:${(i*0.13).toFixed(2)}s"></div>`
      ).join('')}
    </div>` : '';
  root.innerHTML = `
    <div class="veto-stage veto-finale ${entryCls}">
      ${confetti}
      <div class="veto-finale-title">${esc(sess.mode)} &middot; LOCKED IN</div>
      <div class="veto-finale-sub">${esc(sess.team_a_name)} vs ${esc(sess.team_b_name)}</div>
      <div class="veto-finale-maps">
        ${maps.map((m, i) => `
          <div class="veto-finale-map ${m === decider ? 'decider' : ''}">
            <span class="veto-finale-label">${m === decider ? 'DECIDER' : `MAP ${i+1}`}</span>
            ${esc(m)}
          </div>
        `).join('')}
      </div>
      ${conn ? `
      <div class="veto-captain-connect" style="margin-bottom:18px">
        <div class="veto-connect-label">What captains see — server connect:</div>
        <div class="veto-connect-cmd">${esc(conn.command)}</div>
        <div class="veto-connect-actions">
          <button class="btn btn-ghost btn-sm" id="veto-admin-copy-cmd">📋 Copy</button>
        </div>
      </div>` : ''}
      <div class="veto-ready-row">
        <div class="veto-ready-slot ${readyA ? 'ready' : ''}">
          <div class="veto-ready-team">${esc(sess.team_a_name)}</div>
          <div class="veto-ready-state">${readyA ? '✓ READY' : '… waiting'}</div>
        </div>
        <div class="veto-ready-slot ${readyB ? 'ready' : ''}">
          <div class="veto-ready-team">${esc(sess.team_b_name)}</div>
          <div class="veto-ready-state">${readyB ? '✓ READY' : '… waiting'}</div>
        </div>
      </div>
      <button class="btn ${bothReady ? 'btn-accent veto-launch-armed' : 'btn-ghost'}"
              id="veto-launch-btn"
              style="font-size:14px;padding:14px 32px"
              title="${bothReady ? 'Both captains ready — fire matchzy_loadmatch' : 'Waiting for both captains to ready up'}">
        ${bothReady ? '⚡ Hand series to MatchZy →' : 'Waiting for both captains…'}
      </button>
      <div id="veto-matchzy-status" style="margin-top:14px;font-family:var(--font-mono);font-size:11px;color:var(--text-4)">
        Writes a MatchZy match config under <code>csgo/cfg/MatchZy/</code> and issues
        <code>matchzy_loadmatch</code> via RCON.
      </div>
    </div>
  `;
  el('veto-launch-btn').addEventListener('click', async (ev) => {
    const btn = el('veto-launch-btn');
    const status = el('veto-matchzy-status');
    // Guard: refuse to fire unless both captains have ticked Ready.
    // Shift+Click overrides for the "captain went AFK, I'm acking on
    // their behalf" case — admin can also just click each team's ready
    // slot to mark them ready manually, but this is the quick path.
    if (!bothReady && !ev.shiftKey) {
      toast('Waiting for both captains to tick Ready. Hold Shift+Click to override.', 'var(--accent)');
      return;
    }
    btn.disabled = true; btn.textContent = 'Handing off…';
    try {
      const r = await api.veto.finale(true);
      const mz = r.matchzy || {};
      console.log('MatchZy config:', r.config, 'outcome:', mz);
      if (mz.loaded) {
        toast('MatchZy loaded the match — knife round + LIVE will follow', 'var(--ok)');
        status.innerHTML = `
          <div style="color:var(--ok)">✓ matchzy_loadmatch succeeded.</div>
          <div style="margin-top:4px;color:var(--text-3)">Config: <code>${esc(mz.written_to || '')}</code></div>
          ${mz.rcon_response ? `<div style="margin-top:4px;color:var(--text-4)">Server: ${esc(mz.rcon_response)}</div>` : ''}
        `;
        btn.textContent = 'Match handed off ✓';
      } else if (mz.error) {
        // File written, but RCON failed or server wasn't running.  Don't
        // toast as error — the operator's not blocked; they can copy the
        // path and run `matchzy_loadmatch <file>` manually from the RCON
        // console.  The yellow warning style signals "action needed".
        toast('Config written but MatchZy handoff needs attention', 'var(--warn, var(--accent))');
        status.innerHTML = `
          <div style="color:var(--warn, var(--accent))">⚠ ${esc(mz.error)}</div>
          ${mz.written_to ? `<div style="margin-top:4px;color:var(--text-3)">Config saved to: <code>${esc(mz.written_to)}</code></div>` : ''}
        `;
        btn.textContent = 'Retry handoff';
        btn.disabled = false;
      } else {
        // No load_match requested — just confirm the config dropped.
        status.innerHTML = `<div style="color:var(--ok)">✓ Config written to <code>${esc(mz.written_to || '')}</code></div>`;
      }
    } catch (e) {
      // True error from the API — file write failed, no usable config.
      toast(e.message, 'var(--bad)');
      btn.textContent = 'Hand series to MatchZy →';
      btn.disabled = false;
    }
  });

  // v0.10.2 admin-side copy of the connect command (preview of what
  // captains see; also useful if the admin needs to paste it themselves)
  const adminCopyBtn = el('veto-admin-copy-cmd');
  if (adminCopyBtn && conn) {
    adminCopyBtn.addEventListener('click', () => copyText(conn.command, 'Connect command'));
  }

  // Admin can ack-on-behalf by clicking a team's ready slot.  Toggles
  // that team's ready flag (so re-clicking un-acks too — useful if you
  // misclicked).  The API has team-spoof protection for captains; admins
  // can pass any team explicitly.
  document.querySelectorAll('.veto-ready-slot').forEach((slot, idx) => {
    slot.addEventListener('click', async () => {
      const team = idx === 0 ? 'A' : 'B';
      const currently = (team === 'A') ? readyA : readyB;
      try {
        _vetoApply(await api.veto.ready(!currently, team));
        toast(`${team === 'A' ? sess.team_a_name : sess.team_b_name}: ${currently ? 'un-readied' : 'READY'} (by admin)`);
      } catch (err) { toast(err.message, 'var(--bad)'); }
    });
    slot.style.cursor = 'pointer';
    slot.title = 'Click to toggle this team\'s ready state (admin ack-on-behalf)';
  });
}

/* ── Captain finale view (v0.10.1) ────────────────────────────────────── */
// Simpler than the admin's: just the maplist + decider + a big Ready toggle
// + status of the OTHER captain.  No matchzy launch button (admin owns the
// trigger).  When both captains are ready, captain sees "Waiting for admin
// to launch" / when admin fires, the snapshot flips to complete and the
// SPA renders the Complete page.
function _renderVetoFinaleCaptain(root, sess) {
  const maps    = sess.final_maps || [];
  const decider = sess.decider;
  // Determine which team this captain is on, from their server-status role
  const myTeam  = (state_ && state_.server && state_.server.captain_team) || '';
  const myReady    = myTeam === 'A' ? sess.ready_a : sess.ready_b;
  const otherReady = myTeam === 'A' ? sess.ready_b : sess.ready_a;
  const otherName  = myTeam === 'A' ? sess.team_b_name : sess.team_a_name;
  const myName     = myTeam === 'A' ? sess.team_a_name : sess.team_b_name;
  // v0.10.2: match connect details surfaced at finale state so captains
  // can copy the connect command into their team's Discord without
  // pestering the operator.  Backend exposes this only at finale/complete
  // states (security: the token IS the credential; captains are already
  // authorised to know the game-server password).
  const conn = sess.match_connect;
  root.innerHTML = `
    <div class="veto-stage veto-finale">
      <div class="veto-finale-title">${esc(sess.mode)} &middot; LOCKED IN</div>
      <div class="veto-finale-sub">${esc(sess.team_a_name)} vs ${esc(sess.team_b_name)}</div>
      <div class="veto-finale-maps">
        ${maps.map((m, i) => `
          <div class="veto-finale-map ${m === decider ? 'decider' : ''}">
            <span class="veto-finale-label">${m === decider ? 'DECIDER' : `MAP ${i+1}`}</span>
            ${esc(m)}
          </div>
        `).join('')}
      </div>

      ${conn ? `
      <div class="veto-captain-connect">
        <div class="veto-connect-label">Server connect — share with your team:</div>
        <div class="veto-connect-cmd">${esc(conn.command)}</div>
        <div class="veto-connect-actions">
          <button class="btn btn-accent" id="veto-cap-copy-cmd"
                  style="min-height:44px;padding:10px 18px">
            📋 Copy connect command
          </button>
          <button class="btn btn-ghost" id="veto-cap-copy-team-invite"
                  style="min-height:44px;padding:10px 18px"
                  title="Pre-formatted message to drop in your team's Discord">
            📋 Copy team invite
          </button>
        </div>
        <div class="veto-connect-hint">
          ${conn.password_set
            ? 'Server is password-protected. The command above includes the password.'
            : 'Server has no password — anyone with the IP can join. Share carefully.'}
        </div>
      </div>` : ''}

      <div class="veto-captain-ready-block">
        <div class="veto-captain-opponent">
          <strong>${esc(otherName)}</strong>:
          <span class="${otherReady ? 'is-ready' : 'is-waiting'}">
            ${otherReady ? '✓ READY' : '… not ready yet'}
          </span>
        </div>
        <button class="btn ${myReady ? 'btn-ghost' : 'btn-accent'}"
                id="veto-cap-ready-btn"
                style="font-size:14px;padding:14px 32px;margin-top:14px;min-height:48px">
          ${myReady ? '✓ READY — click to un-ready' : `READY UP (${esc(myName)})`}
        </button>
        <div style="margin-top:14px;font-family:var(--font-mono);font-size:11px;color:var(--text-4)">
          ${(myReady && otherReady)
            ? 'Both ready — operator will start the match shortly.'
            : 'Tick READY when you and your team are in the server and prepared.'}
        </div>
      </div>
    </div>
  `;
  el('veto-cap-ready-btn').addEventListener('click', async () => {
    // v0.11.17 B5 — re-derive `myReady` from the LIVE state at click time,
    // not from the closure captured at render.  The render closure can go
    // stale when the SPA suppresses a render (focused input) or when SSE
    // arrives between renders.  Captain saw the ✓ READY badge from a fresh
    // SSE state, tapped to un-ready, but the closure had `myReady=false`
    // from the old render → sent `ready(true)` (no-op).  Looks dead to
    // the captain.  Re-derive ensures the toggle always inverts the truth.
    const sNow      = _vetoState && _vetoState.session ? _vetoState.session : sess;
    const myReadyNow = myTeam === 'A' ? !!sNow.ready_a : !!sNow.ready_b;
    try {
      _vetoApply(await api.veto.ready(!myReadyNow));
      toast(myReadyNow ? 'Un-readied' : 'Ready! Waiting for opponent / operator.');
    } catch (err) { toast(err.message, 'var(--bad)'); }
  });
  if (conn) {
    el('veto-cap-copy-cmd').addEventListener('click', () => {
      copyText(conn.command, 'Connect command');
    });
    el('veto-cap-copy-team-invite').addEventListener('click', () => {
      // Pre-formatted block for the captain's own team Discord channel.
      // Includes the mode + maplist so teammates know what's about to be
      // played; useful when 5+ people need the same info at once.
      const mapList = maps.map((m, i) => m === decider
        ? `  • ${m} (decider)`
        : `  • Map ${i+1}: ${m}`).join('\n');
      const text =
        `🎮 ${myName} — match incoming!\n` +
        `Mode: ${sess.mode}\n` +
        `Maps:\n${mapList}\n\n` +
        `Connect:\n${conn.command}`;
      copyText(text, 'Team invite');
    });
  }
}

/* ── Complete ──────────────────────────────────────────────────────────── */
function _renderVetoComplete(root, sess) {
  // v0.10.2: Captain role sees a read-only summary (no rematch / reset
  // buttons — those are operator decisions).
  const isCap = (state_ && state_.server && state_.server.role === 'captain');
  root.innerHTML = `
    <div class="veto-stage veto-finale">
      <div class="veto-finale-title">Series Complete</div>
      <div class="veto-finale-sub">${esc(sess.team_a_name)} vs ${esc(sess.team_b_name)} · ${esc(sess.mode)}</div>
      <div class="veto-finale-maps">
        ${(sess.final_maps || []).map((m, i) => `
          <div class="veto-finale-map ${m === sess.decider ? 'decider' : ''}">
            <span class="veto-finale-label">${m === sess.decider ? 'DECIDER' : `MAP ${i+1}`}</span>
            ${esc(m)}
          </div>
        `).join('')}
      </div>
      ${isCap ? `
        <div style="color:var(--text-3);font-family:var(--font-mono);font-size:11px;margin-top:10px">
          Operator will start the next match or reset the session.
        </div>
      ` : `
        <div class="veto-complete-actions">
          <button class="btn btn-accent" id="veto-rematch-btn"
                  style="min-height:48px">
            🔄 Rematch (same teams) →
          </button>
          <button class="btn btn-ghost" id="veto-new-btn"
                  style="min-height:48px">
            Start a new session →
          </button>
        </div>
        <div style="color:var(--text-4);font-family:var(--font-mono);font-size:11px;margin-top:14px;max-width:480px;margin-left:auto;margin-right:auto">
          <strong>Rematch</strong> keeps the 10 players + same captains + map pool; you'll re-issue captain links + run a fresh veto in seconds.
          <strong>New session</strong> clears everything back to idle.
        </div>
      `}
    </div>
  `;
  if (!isCap) {
    el('veto-rematch-btn').addEventListener('click', async () => {
      try {
        _vetoApply(await api.veto.rematch());
        toast('Rematch — same teams, fresh BO. Click "Generate captain links" to mint new tokens.', 'var(--accent)');
      } catch (e) { toast(e.message, 'var(--bad)'); }
    });
    el('veto-new-btn').addEventListener('click', async () => {
      try { _vetoApply(await api.veto.reset()); toast('Ready for a new session'); }
      catch (e) { toast(e.message, 'var(--bad)'); }
    });
  }
}

/* ── Captain view (simplified — just shows the relevant action) ───────── */
function _renderVetoCaptain(root, state, sess) {
  if (state === 'idle' || !sess) {
    root.innerHTML = `<div class="veto-stage" style="text-align:center;padding:40px">
      <div style="color:var(--text-3)">No active veto session.</div></div>`;
    return;
  }
  if (state === 'veto') return _renderVetoBoard(root, sess);
  if (state === 'finale' || state === 'complete') return _renderVetoFinale(root, sess);
  // v0.10.2 — Pre-veto limbo screen: give the captain useful context about
  // what's blocking instead of the generic "Current stage: voting"
  // placeholder.  Per-state messages name the blocker + show progress so
  // captains know whether to wait 30 seconds or 5 minutes.
  const myTeam = (state_ && state_.server && state_.server.captain_team) || '';
  const myTeamName = myTeam === 'A' ? sess.team_a_name
                   : myTeam === 'B' ? sess.team_b_name
                   : '';
  const stageMessages = {
    roster: {
      heading: 'Operator is setting up the roster',
      detail:  `Players in: ${(sess.roster || []).length}/10. Once all 10 are entered the operator will split into teams.`,
    },
    teams: {
      heading: 'Teams have been split — captain vote next',
      detail:  'Operator will start the captain vote shortly. You\'ll see your team\'s players when voting begins.',
    },
    voting: {
      heading: 'Captain vote in progress',
      detail:  (() => {
        const va = Object.keys(sess.votes_a || {}).length;
        const vb = Object.keys(sess.votes_b || {}).length;
        return `Team votes: A ${va}/5, B ${vb}/5. The veto board appears once both captains are elected + claim their links.`;
      })(),
    },
    links: {
      heading: 'Waiting for the other captain to claim their link',
      detail:  (() => {
        const claimedA = sess.tokens_claimed?.A;
        const claimedB = sess.tokens_claimed?.B;
        const myClaimed = (myTeam === 'A') ? claimedA : claimedB;
        const otherClaimed = (myTeam === 'A') ? claimedB : claimedA;
        if (myClaimed && !otherClaimed) {
          return 'You\'re in. The veto board will appear as soon as the other captain claims their link.';
        }
        return 'Both links need to be claimed before the veto board appears.';
      })(),
    },
  };
  const m = stageMessages[state] || {
    heading: 'Waiting on operator',
    detail:  `Current stage: ${esc(state)}.`,
  };
  root.innerHTML = `
    <div class="veto-stage veto-captain-greeting">
      <h2>${esc(m.heading)}</h2>
      ${myTeamName
        ? `<div class="veto-captain-team">${esc(myTeamName)} · captain</div>`
        : `<div class="veto-captain-team">Team — pending</div>`}
      <div style="color:var(--text-3);font-family:var(--font-mono);font-size:12px;line-height:1.6;max-width:480px;margin:0 auto">
        ${esc(m.detail)}
      </div>
      <div style="margin-top:18px;font-family:var(--font-mono);font-size:10px;letter-spacing:0.12em;color:var(--text-4)">
        STAGE: ${esc(state).toUpperCase()} · LIVE
      </div>
    </div>
  `;
}

/* ── Voter view (v0.12.3 / task #135) ──────────────────────────────────── */
// Minimal screen for a rostered player who claimed their voter token.
// Shows: their team's 5 names + a click target per teammate.  Click =
// cast vote.  Vote is final-ish — they can change it until the operator
// hits Resolve, but once Resolve fires the captains are elected and the
// voter screen flips to a "thanks, captain elected" confirmation.
function _renderVetoVoter(root, state, sess) {
  // Hide the operator-side reset + history buttons; voter has no business there.
  const resetBtn = el('veto-reset-btn');
  if (resetBtn) resetBtn.style.display = 'none';

  const server   = state_ && state_.server || {};
  const myTeam   = server.voter_team || '';
  const myIdx    = (typeof server.voter_idx === 'number') ? server.voter_idx : -1;
  if (!sess) {
    root.innerHTML = `<div class="veto-stage" style="text-align:center;padding:40px">
      <div style="color:var(--text-3)">No active veto session.</div></div>`;
    return;
  }

  // Pre-vote stages — show waiting message.
  if (state !== 'voting') {
    const headings = {
      idle: 'No active session yet',
      roster: 'Operator is finalising the roster',
      teams: 'Teams have been split — voting starts shortly',
      links: 'Voting is complete · captains have their links',
      veto: 'Captains are picking maps now',
      finale: 'Match is being set up',
      complete: 'Match complete',
    };
    root.innerHTML = `
      <div class="veto-stage veto-captain-greeting">
        <h2>${esc(headings[state] || 'Waiting on operator')}</h2>
        <div class="veto-captain-team">${esc(myTeam === 'A' ? sess.team_a_name : sess.team_b_name)} · voter</div>
        <div style="margin-top:18px;font-family:var(--font-mono);font-size:10px;letter-spacing:0.12em;color:var(--text-4)">
          STAGE: ${esc(state).toUpperCase()}
        </div>
      </div>
    `;
    return;
  }

  // voting stage — render team list + click targets
  const teamRoster = (myTeam === 'A') ? sess.team_a : sess.team_b;
  const teamName   = (myTeam === 'A') ? sess.team_a_name : sess.team_b_name;
  const votesMap   = (myTeam === 'A') ? sess.votes_a   : sess.votes_b;
  const myVote     = votesMap ? votesMap[myIdx] : undefined;

  if (!Array.isArray(teamRoster) || teamRoster.length === 0) {
    root.innerHTML = `<div class="veto-stage" style="text-align:center;padding:40px">
      <div style="color:var(--text-3)">Loading your team…</div></div>`;
    return;
  }

  const buttons = teamRoster.map((votee, ti) => `
    <div class="veto-vote-btn ${myVote === ti ? 'voted' : ''}"
         data-ti="${ti}" style="font-size:1.05rem;padding:14px 12px">
      ${esc(votee.name)}${myVote === ti ? ' ✓' : ''}
    </div>
  `).join('');

  root.innerHTML = `
    <div class="veto-stage veto-captain-greeting">
      <h2>Vote for your captain</h2>
      <div class="veto-captain-team">${esc(teamName)} · ${esc(teamRoster[myIdx]?.name || 'voter')}</div>
      <div style="color:var(--text-3);font-family:var(--font-mono);font-size:12px;line-height:1.5;max-width:480px;margin:6px auto 18px">
        Tap the player you want as captain.  You can change your pick until the operator resolves the votes.
      </div>
      <div class="veto-vote-btns" style="display:flex;flex-direction:column;gap:10px;max-width:360px;margin:0 auto">
        ${buttons}
      </div>
    </div>
  `;
  document.querySelectorAll('.veto-vote-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const ti = parseInt(btn.dataset.ti, 10);
      try {
        _vetoApply(await api.veto.vote(myTeam, myIdx, ti));
      } catch (e) { toast(e.message, 'var(--bad)'); }
    });
  });
}

/* Tear down SSE when leaving the tab */
// v0.11.26 — drop the `currentPage === 'veto'` gate.  navigate() runs FIRST
// on hashchange (listener registration order at app load), and updates
// currentPage synchronously before this listener fires.  By the time we
// read currentPage here it's already the new page, so the old guard never
// matched on leave — _vetoCleanup never ran — and v0.11.25's poll timer
// leaked forever.  _vetoCleanup is idempotent (no-ops if timer/ES are
// already null), so firing it on every non-veto hash is safe.
// Audit finding #3.
window.addEventListener('hashchange', () => {
  if (location.hash.replace('#','') !== 'veto') {
    _vetoCleanup();
  }
});

/* ══════════════════════════════════════════════════════════════ CONFIG PAGE */

pages['config'] = async function() {
  const root = el('content');
  if (_guestBlocked(root)) return;
  root.innerHTML = '<div class="empty-state">Loading config…</div>';

  let cfg;
  try { cfg = await api.config(); }
  catch (e) { root.innerHTML = `<div class="empty-state text-red">${e.message}</div>`; return; }

  const isLocal = cfg.is_local;

  // v0.14.2 — single-column Config restructure (task #153).
  // Section order follows operator's mental model:
  //   Setup (Steam + Install)  →  Security  →  Server (+ Bots merged)
  //   →  Match Flow  →  Discord (+ webhook moved here)  →  Tools row
  // Each section is a <section class="cfg-section"> with a strong top
  // divider + accent-bar title — see app.css ".cfg-section*".
  root.innerHTML = `
    <div id="config-root">

      ${isLocal ? `
      <!-- ── Setup ─────────────────────────────────────────────────── -->
      <section class="cfg-section">
        <h2 class="cfg-section-title">Setup</h2>
        <p class="cfg-section-sub">
          First-run essentials. Steam credentials, dedicated server install, and updates.
        </p>

        <div class="card">
          <div class="cfg-card-title">Steam Account</div>
          <div class="field"><label>Username</label>
            <input class="input" id="cfg-steam-user" value="${esc(cfg.steam_username||'')}"></div>
          <div class="field"><label>Password</label>
            <input class="input" id="cfg-steam-pw" type="password" placeholder="Stored securely"></div>
          <div class="text-sub text-sm" style="margin-top:10px">
            Use a dedicated Steam account, not your personal one.
            ${cfg.steam_session_active ? '<span class="text-green">✓ Active session</span>' : ''}
          </div>
          <div class="flex gap-8 btn-full">
            <button class="btn btn-ghost flex-1" id="cfg-steam-save">Save Credentials</button>
            <button class="btn btn-accent flex-1" id="cfg-steam-login">Login (Interactive)</button>
          </div>
        </div>

        <div class="card" style="margin-top:14px">
          <div class="cfg-card-title">Server Installation</div>
          <div class="field">
            <label>Server Directory</label>
            <div class="flex gap-8">
              <input class="input flex-1" id="cfg-server-dir" value="${esc(cfg.server_dir||'')}">
              <button class="btn btn-ghost btn-sm" id="cfg-browse-btn">Browse…</button>
            </div>
          </div>
          <div class="flex gap-8 btn-full">
            <button class="btn btn-ghost flex-1" id="cfg-dir-save">Set Directory</button>
            <button class="btn btn-accent flex-1" id="cfg-install-btn">Install / Reinstall</button>
          </div>
          <button class="btn btn-ghost btn-full" id="cfg-update-btn" style="margin-top:10px">
            ↻ Update / Validate CS2 (steamcmd)
          </button>
          <small class="text-sub text-sm" style="display:block;margin-top:8px;line-height:1.5">
            Forces a steamcmd <code>app_update 730 validate</code> in place — use this when you need
            to update even if no update badge is showing (the badge relies on a mirror that can lag
            Valve). Stop the server first.
          </small>
        </div>
      </section>

      <!-- ── Security ──────────────────────────────────────────────── -->
      <section class="cfg-section">
        <h2 class="cfg-section-title">Security</h2>
        <p class="cfg-section-sub">
          PINs gate web access; the RCON password authenticates the in-game console.
        </p>

        <div class="card">
          <div class="field"><label>Admin PIN <span class="text-sub">(4+ digits, full web-panel access)</span></label>
            <input class="input" id="cfg-pin" type="password"
                   value="" maxlength="8"
                   placeholder="${cfg.admin_pin ? '(unchanged — leave blank to keep)' : '(set a PIN)'}"></div>
          <div class="field"><label>Guest PIN <span class="text-sub">(optional — limited remote access: maps, modes &amp; workshop downloads only. Blank = off)</span></label>
            <input class="input" id="cfg-guest-pin" type="password"
                   value="" maxlength="8"
                   placeholder="${cfg.guest_pin ? '(unchanged — leave blank to keep, type DISABLE to remove)' : '(disabled)'}"></div>
          <div class="field"><label>RCON Password <span class="text-sub">(auto-generated, change if needed)</span></label>
            <input class="input" id="cfg-rcon-pw" type="password"
                   value=""
                   placeholder="${cfg.rcon_password ? '(unchanged — leave blank to keep)' : '(none set)'}"></div>
          <button class="btn btn-accent btn-full" id="cfg-security-save">Save Security Settings</button>
        </div>
      </section>
      ` : ''}

      <!-- ── Server ────────────────────────────────────────────────── -->
      <section class="cfg-section">
        <h2 class="cfg-section-title">Server</h2>
        <p class="cfg-section-sub">
          How the dedicated server runs and behaves. Includes bot fill.
        </p>

        <div class="card">
          <div class="field"><label>Hostname</label>
            <input class="input" id="cfg-hostname" value="${esc(cfg.hostname || '')}"></div>
          <div class="field"><label>Server Password (sv_password)</label>
            <input class="input" id="cfg-svpassword" type="password"
                   value="${esc(cfg.sv_password || '')}"></div>
          ${isLocal ? `
          <div class="field">
            <label>Game Server Login Token <span class="text-sub">(GSLT)</span></label>
            <input class="input" id="cfg-gslt" placeholder="Leave blank for LAN / private use"
                   value="${esc(cfg.gslt_token || '')}">
            <small class="text-sub text-sm" style="display:block;margin-top:6px;line-height:1.5">
              Makes your server visible in the public browser.
              Get one free at <strong>steamcommunity.com/dev/managegameservers</strong>
              (App&nbsp;ID&nbsp;730). Left blank during setup? Paste it here.
            </small>
          </div>
          <div class="field"><label>Max Players Override <span class="text-sub">(blank = mode default)</span></label>
            <input class="input" id="cfg-maxplayers" placeholder="e.g. 16"
                   value="${esc(cfg.max_players_override || '')}"></div>
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

          <hr class="cfg-card-divider">

          <div class="toggle-row">
            <div class="toggle-info">
              <strong>Use bots</strong>
              <small>Fill empty Arena slots with bots. Off = humans-only ladder.</small>
            </div>
            <label class="toggle">
              <input type="checkbox" id="cfg-bots-enabled" ${cfg.bots_enabled?'checked':''}>
              <span class="toggle-track"></span><span class="toggle-thumb"></span>
            </label>
          </div>
          <div class="field">
            <label>Default bot difficulty</label>
            <select class="select" id="cfg-bot-diff">
              ${['Easy','Normal','Hard','Expert'].map(d =>
                `<option ${d===cfg.bot_difficulty?'selected':''}>${d}</option>`).join('')}
            </select>
          </div>

          <button class="btn btn-accent btn-full" id="cfg-server-save">Save Server Settings</button>
        </div>
      </section>

      <!-- ── Match Flow ────────────────────────────────────────────── -->
      <section class="cfg-section">
        <h2 class="cfg-section-title">Match Flow</h2>
        <p class="cfg-section-sub">
          Veto and MatchZy match-controller settings.
        </p>

        <div class="card">
          <div class="field">
            <label>Public Share URL <span class="text-sub">(for captain links on the internet)</span></label>
            <input class="input" id="cfg-public-share-url" type="text"
                   value="${esc(cfg.public_share_url || '')}"
                   placeholder="https://random-words.trycloudflare.com">
            <small class="text-sub text-sm" style="display:block;margin-top:6px;line-height:1.5">
              When set, captain join URLs use this base instead of <code>http://&lt;public_ip&gt;:&lt;port&gt;</code>.
              Paste your Cloudflare tunnel URL here — run <code>cloudflared tunnel --url http://localhost:5050</code> and copy the printed HTTPS URL.
              Leave blank to fall back to <code>public_ip + port</code> (which requires a port-forward).
            </small>
          </div>
          <div class="toggle-row">
            <div class="toggle-info">
              <strong>Auto-launch when both captains ready</strong>
              <small>Fire <code>matchzy_loadmatch</code> automatically when both captains tick Ready on the finale page. Off by default — operator clicks GO manually so they can verify mode/server first.</small>
            </div>
            <label class="toggle">
              <input type="checkbox" id="cfg-veto-auto-launch" ${cfg.veto_auto_launch_on_ready?'checked':''}>
              <span class="toggle-track"></span><span class="toggle-thumb"></span>
            </label>
          </div>
          ${isLocal ? `
          <div class="field">
            <label>MatchZy cvars <span class="text-sub">(override the defaults baked into the match-config)</span></label>
            <div id="cfg-matchzy-cvars-list" class="cfg-cvar-list">
              <!-- rendered below via _renderMatchzyCvars() -->
            </div>
            <button class="btn btn-ghost btn-sm" id="cfg-matchzy-cvar-add" type="button" style="margin-top:6px">
              + Add cvar
            </button>
            <small class="text-sub text-sm" style="display:block;margin-top:6px;line-height:1.5">
              Merged on top of the built-in defaults
              (<code>mp_warmup_pausetimer=0</code>, <code>matchzy_minimum_ready_required=2</code>)
              when the finale writes the MatchZy config. Your row wins on conflicts. Leave the
              <em>value</em> blank to actively suppress a default cvar.
              Examples: <code>matchzy_knife_enabled_default 1</code>,
              <code>matchzy_pause_after_warmup 1</code>.
            </small>
          </div>
          ` : ''}
          <button class="btn btn-accent btn-full" id="cfg-veto-save">Save Match Settings</button>
        </div>
      </section>

      ${isLocal ? `
      <!-- ── Discord ───────────────────────────────────────────────── -->
      <section class="cfg-section">
        <h2 class="cfg-section-title">Discord</h2>
        <p class="cfg-section-sub">
          Bot integration: DM captain links, voice-channel roster pull, live veto embed, auto-move
          to team VCs, round summaries. See
          <a href="https://github.com/oblivion-systems/OblivionServerTool/blob/master/DISCORD.md">DISCORD.md</a>
          for the 5-minute setup guide.
        </p>

        <div class="card">
          <div class="cfg-card-title">Connection</div>
          <div class="field">
            <label>Bot Token <span class="text-sub">(local-only — never sent to remote sessions)</span></label>
            <input class="input" id="cfg-discord-bot-token" type="password"
                   value=""
                   placeholder="${cfg.discord_bot_token ? '(unchanged — leave blank to keep, type CLEAR to remove)' : 'MTAxxxxxx.xxxxxxx.xxxxxxx...'}">
          </div>
          <div class="field">
            <label>Server (Guild) ID</label>
            <input class="input" id="cfg-discord-guild-id" type="text" inputmode="numeric"
                   value="${esc(cfg.discord_guild_id||'')}"
                   placeholder="123456789012345678">
          </div>

          <hr class="cfg-card-divider">
          <div class="cfg-card-title">Channels</div>
          <div class="field">
            <label>Veto Embed Channel ID <span class="text-sub">(optional — blank skips live embeds)</span></label>
            <div class="flex gap-8">
              <input class="input flex-1" id="cfg-discord-channel-id" type="text" inputmode="numeric"
                     value="${esc(cfg.discord_veto_channel_id||'')}"
                     placeholder="234567890123456789">
              <button class="btn btn-ghost" id="cfg-discord-channel-browse"
                      title="Browse the bot's text channels and pick one">
                🔍 Browse
              </button>
            </div>
          </div>
          <div class="field">
            <label>Default Voice Channel ID <span class="text-sub">(optional — blank shows picker each session)</span></label>
            <div class="flex gap-8">
              <input class="input flex-1" id="cfg-discord-voice-channel-id" type="text" inputmode="numeric"
                     value="${esc(cfg.discord_voice_channel_id||'')}"
                     placeholder="345678901234567890">
              <button class="btn btn-ghost" id="cfg-discord-voice-browse"
                      title="Browse the bot's voice channels and pick one">
                🔍 Browse
              </button>
            </div>
            <div id="cfg-discord-voice-status" class="text-sm text-sub" style="margin-top:6px;min-height:18px"></div>
          </div>
          <div class="field">
            <label>Team A Voice Channel ID <span class="text-sub">(optional — needed for /move-teams)</span></label>
            <div class="flex gap-8">
              <input class="input flex-1" id="cfg-discord-team-a-vc-id" type="text" inputmode="numeric"
                     value="${esc(cfg.discord_team_a_voice_channel_id||'')}"
                     placeholder="456789012345678901">
              <button class="btn btn-ghost" id="cfg-discord-team-a-vc-browse"
                      title="Browse the bot's voice channels and pick one">
                🔍 Browse
              </button>
            </div>
          </div>
          <div class="field">
            <label>Team B Voice Channel ID <span class="text-sub">(optional — needed for /move-teams)</span></label>
            <div class="flex gap-8">
              <input class="input flex-1" id="cfg-discord-team-b-vc-id" type="text" inputmode="numeric"
                     value="${esc(cfg.discord_team_b_voice_channel_id||'')}"
                     placeholder="567890123456789012">
              <button class="btn btn-ghost" id="cfg-discord-team-b-vc-browse"
                      title="Browse the bot's voice channels and pick one">
                🔍 Browse
              </button>
            </div>
          </div>

          <hr class="cfg-card-divider">
          <div class="cfg-card-title">Behaviour</div>
          <div class="field">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
              <input type="checkbox" id="cfg-discord-auto-move"
                     ${cfg.discord_auto_move_on_distribute_enabled ? 'checked' : ''}>
              <span>Auto-move teams to their VCs after Distribute</span>
            </label>
            <small class="text-sub text-sm" style="display:block;margin-top:6px;line-height:1.5">
              Requires both team VCs above to be set. Bot needs the
              <strong>Move Members</strong> permission. Default OFF.
            </small>
          </div>
          <div class="field">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
              <input type="checkbox" id="cfg-discord-round-summaries"
                     ${cfg.discord_round_summaries_enabled ? 'checked' : ''}>
              <span>Post round summaries to the veto channel during a live match</span>
            </label>
            <small class="text-sub text-sm" style="display:block;margin-top:6px;line-height:1.5">
              Polls the server every 3s and posts an embed after each round. Can also
              be toggled from Discord via <code>/round-summaries on|off</code>. Default OFF.
            </small>
          </div>
          <div class="field">
            <label>Discord Webhook URL <span class="text-sub">(post finale results to a channel)</span></label>
            <input class="input" id="cfg-discord-webhook" type="password"
                   value=""
                   placeholder="${cfg.discord_webhook_url ? '(unchanged — leave blank to keep, type CLEAR to remove)' : 'https://discord.com/api/webhooks/...'}">
            <small class="text-sub text-sm" style="display:block;margin-top:6px;line-height:1.5">
              When set, the tool POSTs an embed (teams + maplist + decider + connect string)
              to this Discord channel when a finale completes. Captures most of the bot's value
              without the gateway setup. <strong>Create one:</strong> Discord channel → ⚙ → Integrations → Webhooks → New Webhook → Copy URL.
            </small>
          </div>
          <div id="cfg-discord-status" class="text-sub text-sm" style="margin-top:8px"></div>

          <button class="btn btn-accent btn-full" id="cfg-discord-save">Save Discord Settings</button>

          <!-- Connection check — v0.16.5 / task #157 item E:
               primary "Run setup check" button drives the full mock-veto
               smoke test (embed lifecycle + VC reachability + reactions).
               Per-feature tests (test embed / test DM) tucked into an
               Advanced expander so a new operator can verify the wiring
               in one click. -->
          <div class="cfg-conn-check">
            <div class="cfg-conn-check-label">Verify your Discord setup</div>
            <small class="text-sub text-sm" style="display:block;margin-bottom:10px;line-height:1.5">
              Click the button below to run a full smoke test — the bot will
              post an embed to your veto channel, edit it 3× to simulate a
              veto, then leave a 🟢 "test complete" embed (safe to delete).
              Confirms everything is wired correctly before your first real
              tournament.
            </small>
            <button class="btn btn-accent btn-full" id="cfg-discord-mock-veto"
                    title="Walks the full embed lifecycle (post + 3 edits + final state) + VC reachability check.">
              🩺 Run Discord setup check
            </button>
            <pre id="cfg-discord-mock-out" class="text-mono text-sm"
                 style="display:none; margin-top:10px; max-height:240px; overflow:auto; background:var(--bg-1); padding:10px; white-space:pre-wrap"></pre>

            <details style="margin-top:14px">
              <summary class="text-sm" style="cursor:pointer;color:var(--sub)">
                Advanced: individual feature tests
              </summary>
              <small class="text-sub text-sm" style="display:block;margin-top:8px;line-height:1.5">
                Each verifies ONE feature in isolation — use these to
                triage when the full setup check fails.
              </small>
              <div class="flex gap-8" style="margin-top:8px">
                <button class="btn btn-ghost flex-1" id="cfg-discord-test-embed"
                        title="Post a sample embed to your configured veto channel">
                  📤 Send test embed
                </button>
                <button class="btn btn-ghost flex-1" id="cfg-discord-test-dm"
                        title="DM a sample message to a Discord user (paste your own ID for a self-test)">
                  📨 Send test DM
                </button>
              </div>
            </details>
          </div>
        </div>
      </section>

      <!-- ── Tools (3-card row) ────────────────────────────────────── -->
      <section class="cfg-section">
        <h2 class="cfg-section-title">Tools</h2>
        <p class="cfg-section-sub">
          Operator utilities — Windows performance, in-game RCON, and the diagnostic snapshot.
        </p>

        <div class="cfg-tools-row">
          <div class="card">
            <div class="cfg-card-title">Gaming Mode</div>
            <small class="text-sub text-sm" style="display:block;margin-bottom:12px;line-height:1.5">
              Flips Power Plan + Game Mode/DVR + pins cs2.exe to cores 0-3 so the server keeps a
              stable tickrate while you play. Runs <code>scripts\\gaming-mode.ps1</code>.
            </small>
            <div class="flex gap-8" style="flex-wrap:wrap">
              <button class="btn btn-accent" id="cfg-gaming-on" title="Switch to High Performance + pin cs2.exe to cores 0-3">⚡ ON</button>
              <button class="btn btn-ghost" id="cfg-gaming-off" title="Restore Balanced power plan, drop core pinning">💤 OFF</button>
              <button class="btn btn-ghost" id="cfg-gaming-status" title="Show current Power Plan / Game Mode / affinity">📊 Status</button>
            </div>
            <pre id="cfg-gaming-output" class="text-mono text-sm"
                 style="margin-top:12px;max-height:240px;overflow:auto;background:var(--bg-1);padding:10px;display:none"></pre>
          </div>

          <div class="card">
            <div class="cfg-card-title">Diagnostic Snapshot</div>
            <small class="text-sub text-sm" style="display:block;margin-bottom:12px;line-height:1.5">
              Single text snapshot covering app state, active veto session, plugin manifest, Discord bot
              status, and the last 80 log lines. Secrets masked. Copy + paste into your support channel
              when something breaks.
            </small>
            <button class="btn btn-accent btn-full" id="cfg-diag-snapshot">
              🔧 Copy to clipboard
            </button>
          </div>

          <div class="card">
            <div class="cfg-card-title">RCON Console</div>
            <div class="rcon-output" id="rcon-output">Ready. Type a command below.</div>
            <div class="rcon-row" style="margin-top:10px">
              <input class="input" id="rcon-cmd" placeholder="e.g. status" spellcheck="false">
              <button class="btn btn-accent" id="rcon-send">Send</button>
            </div>
          </div>

          <!-- v0.16.0 / task #158 — Config Backup / Restore -->
          <div class="card">
            <div class="cfg-card-title">Config Backup</div>
            <small class="text-sub text-sm" style="display:block;margin-bottom:12px;line-height:1.5">
              Snapshots <code>oblivion_config.json</code> to <code>backups/</code>.
              Auto-runs before plugin install / uninstall / vanilla switch.
              Keeps the most recent 10.
            </small>
            <button class="btn btn-accent btn-full" id="cfg-backup-now">
              💾 Backup config now
            </button>
            <button class="btn btn-ghost btn-full" id="cfg-backup-list" style="margin-top:8px">
              📂 Browse + restore backups
            </button>
          </div>
        </div>
      </section>
      ` : ''}

    </div>`;

  // v0.11.0 polish — MatchZy cvar editor (local-only).  Operator adds
  // key/value rows that get merged into the match-config at finale time.
  // Kept as a transient _cvarRows array so we can re-render on add/remove
  // without losing in-flight edits in the live inputs.
  let _cvarRows = Array.isArray(cfg.matchzy_cvars)
    ? cfg.matchzy_cvars
    : Object.entries(cfg.matchzy_cvars || {}).map(([k, v]) => [k, String(v ?? '')]);
  function _renderMatchzyCvars() {
    const host = el('cfg-matchzy-cvars-list');
    if (!host) return;
    if (_cvarRows.length === 0) {
      host.innerHTML = `<div class="cfg-cvar-empty">No overrides — defaults only.</div>`;
      return;
    }
    host.innerHTML = _cvarRows.map((row, i) => `
      <div class="cfg-cvar-row">
        <input class="input cfg-cvar-key"   type="text" data-i="${i}"
               value="${esc(row[0])}" placeholder="cvar_name" maxlength="64">
        <input class="input cfg-cvar-value" type="text" data-i="${i}"
               value="${esc(row[1])}" placeholder="value (blank = suppress)" maxlength="128">
        <button class="btn btn-ghost btn-sm cfg-cvar-del" data-i="${i}" type="button"
                title="Remove this cvar">×</button>
      </div>
    `).join('');
    host.querySelectorAll('.cfg-cvar-key').forEach(inp => {
      inp.addEventListener('input', (e) => {
        const i = parseInt(e.target.dataset.i, 10);
        _cvarRows[i][0] = e.target.value;
      });
    });
    host.querySelectorAll('.cfg-cvar-value').forEach(inp => {
      inp.addEventListener('input', (e) => {
        const i = parseInt(e.target.dataset.i, 10);
        _cvarRows[i][1] = e.target.value;
      });
    });
    host.querySelectorAll('.cfg-cvar-del').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const i = parseInt(e.target.dataset.i, 10);
        _cvarRows.splice(i, 1);
        _renderMatchzyCvars();
      });
    });
  }
  _renderMatchzyCvars();
  const cvarAddBtn = el('cfg-matchzy-cvar-add');
  if (cvarAddBtn) cvarAddBtn.addEventListener('click', () => {
    _cvarRows.push(['', '']);
    _renderMatchzyCvars();
    // Focus the new key input
    const host = el('cfg-matchzy-cvars-list');
    const inputs = host && host.querySelectorAll('.cfg-cvar-key');
    if (inputs && inputs.length) inputs[inputs.length - 1].focus();
  });

  // Wire up saves
  const vetoSaveBtn = el('cfg-veto-save');
  if (vetoSaveBtn) vetoSaveBtn.addEventListener('click', async () => {
    // Light client-side validation: must be http:// or https:// or blank.
    // Server validates again — this is just to catch the typo before the
    // round-trip.
    const raw = el('cfg-public-share-url').value.trim();
    if (raw && !/^https?:\/\//i.test(raw)) {
      toast('Public Share URL must start with http:// or https:// (or be blank)', 'var(--bad)');
      return;
    }
    const data = {
      public_share_url:          raw,
      veto_auto_launch_on_ready: el('cfg-veto-auto-launch').checked,
    };
    // v0.11.0 polish — only include matchzy_cvars when the editor was
    // rendered (local sessions).  Convert _cvarRows back to {k: v} and
    // drop rows where the key is blank (operator added then forgot).
    if (el('cfg-matchzy-cvars-list')) {
      const obj = {};
      for (const [k, v] of _cvarRows) {
        const key = (k || '').trim();
        if (key) obj[key] = (v == null) ? '' : String(v);
      }
      data.matchzy_cvars = obj;
    }
    // v0.14.2 — Discord webhook moved to the Discord card's save handler.
    try {
      await api.setConfig(data);
      toast('Match settings saved');
    } catch (e) { toast(e.message, 'var(--bad)'); }
  });

  // v0.11.0 — Discord bot settings save (local-only, conditional)
  const discordSaveBtn = el('cfg-discord-save');
  if (discordSaveBtn) discordSaveBtn.addEventListener('click', async () => {
    const tokenVal = el('cfg-discord-bot-token').value.trim();
    const guild    = el('cfg-discord-guild-id').value.trim();
    const channel  = el('cfg-discord-channel-id').value.trim();
    // v0.11.15 — default voice channel for one-click roster pull
    const voiceCh  = (el('cfg-discord-voice-channel-id')?.value || '').trim();
    // v0.12.0 — per-team VCs for /move-teams
    const teamAVc  = (el('cfg-discord-team-a-vc-id')?.value || '').trim();
    const teamBVc  = (el('cfg-discord-team-b-vc-id')?.value || '').trim();
    const data = {
      discord_guild_id:                         guild,
      discord_veto_channel_id:                  channel,
      discord_voice_channel_id:                 voiceCh,
      discord_team_a_voice_channel_id:          teamAVc,
      discord_team_b_voice_channel_id:          teamBVc,
    };
    if (tokenVal === 'CLEAR') data.discord_bot_token = '';
    else if (tokenVal) data.discord_bot_token = tokenVal;
    // else leave undefined → server keeps existing value
    // v0.14.2 — webhook moved from cfg-veto-save into this handler so all
    // Discord-flavoured settings save together.  "CLEAR" magic word empties
    // the field; blank keeps the existing value.
    const webhookEl = el('cfg-discord-webhook');
    if (webhookEl) {
      const wv = webhookEl.value.trim();
      if (wv === 'CLEAR') data.discord_webhook_url = '';
      else if (wv) data.discord_webhook_url = wv;
    }
    try {
      await api.setConfig(data);
      toast('Discord settings saved — bot will connect in a moment if a token is set');
      // Refresh the live VC preview after save so the operator sees
      // immediate feedback that the configured channel is reachable.
      _refreshVoiceChannelPreview();
    } catch (e) { toast(e.message, 'var(--bad)'); }
  });

  // v0.11.15 — Browse voice channels picker (writes ID into the input).
  // Same picker shape as the Veto roster modal, but its click handler
  // stamps the channel ID + name into the Config field instead of pulling
  // members.  Saves a round-trip to Discord for the IDs.
  const voiceBrowseBtn = el('cfg-discord-voice-browse');
  if (voiceBrowseBtn) voiceBrowseBtn.addEventListener('click', async () => {
    await _vetoOpenDiscordPullModal({ pickOnly: true, kind: 'voice',
      onPick: (ch) => {
        const inp = el('cfg-discord-voice-channel-id');
        if (inp) {
          inp.value = ch.id;
          // Show immediate label without waiting for next save
          const status = el('cfg-discord-voice-status');
          if (status) status.innerHTML =
            `Selected: <strong>${esc(ch.name)}</strong> — ${ch.member_count} connected (unsaved — click Save Discord Settings)`;
        }
      }
    });
  });

  // v0.11.18 — Browse text channels picker for the Veto Embed Channel ID.
  // Same picker shape, kind='text' → calls api.discord.textChannels().
  const textChannelBrowseBtn = el('cfg-discord-channel-browse');
  if (textChannelBrowseBtn) textChannelBrowseBtn.addEventListener('click', async () => {
    await _vetoOpenDiscordPullModal({ pickOnly: true, kind: 'text',
      onPick: (ch) => {
        const inp = el('cfg-discord-channel-id');
        if (inp) {
          inp.value = ch.id;
          // No live status div for text-channel picks; toast as feedback.
          toast(`Veto embed channel set to #${ch.name} (unsaved — click Save Discord Settings)`,
                'var(--accent)');
        }
      }
    });
  });

  // v0.12.0 — Team A/B VC browse buttons (same picker as the default-VC).
  ['a', 'b'].forEach(side => {
    const btn = el(`cfg-discord-team-${side}-vc-browse`);
    if (!btn) return;
    btn.addEventListener('click', async () => {
      await _vetoOpenDiscordPullModal({ pickOnly: true, kind: 'voice',
        onPick: (ch) => {
          const inp = el(`cfg-discord-team-${side}-vc-id`);
          if (inp) {
            inp.value = ch.id;
            toast(`Team ${side.toUpperCase()} VC set to #${ch.name} (unsaved — click Save Discord Settings)`,
                  'var(--accent)');
          }
        }
      });
    });
  });

  // v0.12.0 — Auto-move toggle.  Goes through its own endpoint so the
  // server-side precondition check (both VCs configured) runs every time.
  const autoMoveCb = el('cfg-discord-auto-move');
  if (autoMoveCb) autoMoveCb.addEventListener('change', async () => {
    const want = autoMoveCb.checked;
    try {
      const r = await api.discord.autoMoveToggle(want);
      toast(r.enabled
              ? 'Auto-move teams after Distribute: ON'
              : 'Auto-move teams after Distribute: OFF',
            'var(--accent)');
    } catch (e) {
      // Revert checkbox on rejection (typical: VCs not configured yet).
      autoMoveCb.checked = !want;
      toast(e.message, 'var(--bad)');
    }
  });

  // v0.12.1 — Round-summaries toggle.  No precondition check (the embed
  // target = discord_veto_channel_id which is already required for the
  // live veto embed; if it's blank, the post helper silently no-ops).
  const roundCb = el('cfg-discord-round-summaries');
  if (roundCb) roundCb.addEventListener('change', async () => {
    const want = roundCb.checked;
    try {
      const r = await api.discord.roundSummariesToggle(want);
      toast(r.enabled
              ? 'Round summaries: ON'
              : 'Round summaries: OFF',
            'var(--accent)');
    } catch (e) {
      roundCb.checked = !want;
      toast(e.message, 'var(--bad)');
    }
  });

  // Populate the VC preview on Config tab render
  _refreshVoiceChannelPreview();

  // v0.11.0 polish — Discord connection-check buttons
  // v0.12.5 / task #95 — Gaming Mode buttons.  Same handler shape for
  // all three modes; output dumped into the <pre> for transparency.
  ['on', 'off', 'status'].forEach(mode => {
    const btn = el(`cfg-gaming-${mode}`);
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const out = el('cfg-gaming-output');
      btn.disabled = true; const orig = btn.textContent;
      btn.textContent = 'Running…';
      try {
        const r = await api.gamingMode(mode);
        if (out) {
          out.style.display = '';
          out.textContent =
            (r.stdout || '(no stdout)') +
            (r.stderr ? `\n--- stderr ---\n${r.stderr}` : '') +
            `\n--- returncode: ${r.returncode} ---`;
        }
        toast(r.ok
                ? `Gaming Mode ${mode.toUpperCase()} ✓`
                : `Gaming Mode ${mode}: exited ${r.returncode}`,
              r.ok ? 'var(--ok)' : 'var(--bad)');
      } catch (e) {
        toast(e.message, 'var(--bad)');
      } finally {
        btn.disabled = false; btn.textContent = orig;
      }
    });
  });

  const testEmbedBtn = el('cfg-discord-test-embed');
  if (testEmbedBtn) testEmbedBtn.addEventListener('click', async () => {
    testEmbedBtn.disabled = true; testEmbedBtn.textContent = 'Posting…';
    try {
      const r = await api.discord.testEmbed();
      toast(`Test embed posted ✓ (msg ${r.message_id})`, 'var(--ok)');
    } catch (e) {
      toast(e.message, 'var(--bad)');
    } finally {
      testEmbedBtn.disabled = false; testEmbedBtn.textContent = '📤 Send test embed';
    }
  });
  const testDmBtn = el('cfg-discord-test-dm');
  if (testDmBtn) testDmBtn.addEventListener('click', async () => {
    const did = prompt('Enter a Discord User ID to send the test DM to (your own ID is fine for a self-test):');
    if (!did) return;
    if (!/^\d+$/.test(did.trim())) {
      toast('Discord User ID must be digits only', 'var(--bad)');
      return;
    }
    testDmBtn.disabled = true; testDmBtn.textContent = 'Sending…';
    try {
      await api.discord.testDm(did.trim());
      toast('Test DM sent ✓ — check your Discord', 'var(--ok)');
    } catch (e) {
      toast(e.message, 'var(--bad)');
    } finally {
      testDmBtn.disabled = false; testDmBtn.textContent = '📨 Send test DM';
    }
  });

  // v0.16.3 / task #165 — Run mock veto (full bot lifecycle smoke test)
  const mockVetoBtn = el('cfg-discord-mock-veto');
  if (mockVetoBtn) mockVetoBtn.addEventListener('click', async () => {
    mockVetoBtn.disabled = true;
    const orig = mockVetoBtn.textContent;
    mockVetoBtn.textContent = 'Running smoke test (~3s)…';
    const out = el('cfg-discord-mock-out');
    if (out) { out.style.display = 'block'; out.textContent = 'Posting initial embed…\n'; }
    try {
      const r = await api.discord.mockVeto();
      const lines = (r.steps || []).map(s =>
        `${s.ok ? '✓' : '✗'} ${s.name}${s.detail ? ' — ' + s.detail : ''}`
      );
      if (out) out.textContent = (lines.join('\n') || '(no steps reported)')
        + `\n\n${r.summary || ''}\n\n`
        + `Channel: ${r.channel_id}\nMessage: ${r.message_id}\n(safe to delete)`;
      toast(r.ok ? `✓ ${r.summary}` : `⚠ ${r.summary}`, r.ok ? 'var(--ok)' : 'var(--warn)');
    } catch (e) {
      if (out) out.textContent = `Mock veto failed: ${e.message}`;
      toast(`Mock veto failed: ${e.message}`, 'var(--bad)');
    } finally {
      mockVetoBtn.disabled = false; mockVetoBtn.textContent = orig;
    }
  });

  // v0.14.2 — Bots merged into Server card.  Server save now also handles
  // bots_enabled + bot_difficulty (drops the standalone cfg-bot-save button).
  el('cfg-server-save').addEventListener('click', async () => {
    const data = {
      hostname:              el('cfg-hostname').value,
      sv_password:           el('cfg-svpassword').value,
      tickrate_128:          el('cfg-tickrate').checked,
      auto_start:            el('cfg-autostart').checked,
      auto_restart_on_crash: el('cfg-autorestart').checked,
      bots_enabled:          el('cfg-bots-enabled').checked,
      bot_difficulty:        el('cfg-bot-diff').value,
    };
    if (isLocal) {
      data.gslt_token        = el('cfg-gslt').value;
      data.max_players_override = el('cfg-maxplayers').value;
    }
    try { await api.setConfig(data); toast('Server settings saved'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });

  const secSaveBtn = el('cfg-security-save');   // only rendered for local sessions
  if (secSaveBtn) secSaveBtn.addEventListener('click', async () => {
    // Only POST fields the user actually filled in.  The inputs render BLANK
    // (with a placeholder showing current status) so the operator sees an
    // explicit "leave blank to keep" rather than the round-trip pattern that
    // silently truncated long PINs via maxlength="8" and round-tripped the
    // raw RCON secret on every save.
    const data = {};
    const adminVal = el('cfg-pin').value;
    const guestVal = el('cfg-guest-pin').value.trim();
    const rconVal  = el('cfg-rcon-pw').value;
    if (adminVal) data.admin_pin = adminVal;
    // Guest PIN convention: "DISABLE" sentinel clears it; any other non-empty
    // sets it; empty = no change.
    if (guestVal === 'DISABLE') data.guest_pin = '';
    else if (guestVal)          data.guest_pin = guestVal;
    if (rconVal)  data.rcon_password = rconVal;
    if (Object.keys(data).length === 0) {
      toast('Nothing to change (all fields blank)', 'var(--sub)');
      return;
    }
    try { await api.setConfig(data); toast('Security settings saved'); }
    catch (e) { toast(e.message, 'var(--red)'); }
  });

  // v0.11.4 — Diagnostic snapshot button (local-only)
  const diagBtn = el('cfg-diag-snapshot');
  if (diagBtn) diagBtn.addEventListener('click', async () => {
    const original = diagBtn.textContent;
    diagBtn.disabled = true;
    diagBtn.textContent = 'Generating…';
    try {
      const text = await api.diagSnapshot();
      try {
        await navigator.clipboard.writeText(text);
        toast(`Diagnostic snapshot copied (${text.length.toLocaleString()} chars). Paste into your support channel.`);
      } catch (clipErr) {
        // Clipboard write blocked (HTTP context, permissions denied).
        // Fall back to opening a window so operator can copy manually.
        const w = window.open('', '_blank');
        if (w) {
          w.document.write('<pre style="font-family:monospace;font-size:12px;white-space:pre-wrap;padding:16px">' +
            text.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) +
            '</pre>');
          w.document.close();
          toast('Clipboard blocked; opened snapshot in a new window for manual copy.');
        } else {
          toast(`Clipboard blocked + popup blocked. Snapshot size: ${text.length}`, 'var(--bad)');
        }
      }
    } catch (e) {
      toast(`Snapshot failed: ${e.message}`, 'var(--bad)');
    } finally {
      diagBtn.disabled = false;
      diagBtn.textContent = original;
    }
  });

  // v0.16.0 / task #158 — Backup + Restore (local-only)
  const backupBtn = el('cfg-backup-now');
  if (backupBtn) backupBtn.addEventListener('click', async () => {
    backupBtn.disabled = true; const orig = backupBtn.textContent;
    backupBtn.textContent = 'Backing up…';
    try {
      const r = await api.config_backup('manual');
      toast(`✓ Saved ${r.result.filename} (${r.result.bytes} B)`, 'var(--ok)');
    } catch (e) {
      toast(`Backup failed: ${e.message}`, 'var(--bad)');
    } finally {
      backupBtn.disabled = false; backupBtn.textContent = orig;
    }
  });
  const backupListBtn = el('cfg-backup-list');
  if (backupListBtn) backupListBtn.addEventListener('click', async () => {
    let r;
    try { r = await api.config_backups(); }
    catch (e) { toast(`List failed: ${e.message}`, 'var(--bad)'); return; }
    const backups = r.backups || [];
    const overlay = document.createElement('div');
    overlay.className = 'plugins-bootstrap-overlay';
    const rows = backups.length
      ? backups.map(b => `
          <div class="plugin-card" style="margin-bottom:8px">
            <div class="plugin-card-head">
              <span class="plugin-card-title">${esc(b.mtime_iso)}</span>
              <span class="pill pill-mute">${esc(b.reason || 'manual')}</span>
            </div>
            <div class="text-sm text-sub">${esc(b.filename)} (${b.bytes} B)</div>
            <button class="btn btn-sm cfg-restore-pick" data-name="${esc(b.filename)}"
                    style="margin-top:6px">
              Restore this backup
            </button>
          </div>`).join('')
      : '<div class="text-sm text-sub">No backups yet.</div>';
    overlay.innerHTML = `
      <div class="plugins-bootstrap-modal" style="max-width:560px">
        <div class="section-hdr">
          <span class="card-title" style="margin-bottom:0">Config Backups</span>
          <button class="btn btn-sm" id="cfg-backup-close">Close</button>
        </div>
        <p class="text-sm text-sub" style="margin-top:10px">
          Restoring writes the picked snapshot into <code>oblivion_config.json</code>
          (your current config is auto-snapshotted as a pre-restore backup first).
          Restart the app to pick up the restored config.
        </p>
        <div style="margin-top:14px">${rows}</div>
      </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    el('cfg-backup-close').addEventListener('click', close);
    overlay.querySelectorAll('.cfg-restore-pick').forEach(btn => {
      btn.addEventListener('click', async () => {
        const name = btn.dataset.name;
        if (!window.confirm(
          `Restore config from ${name}?\n\n`
          + `This overwrites your current oblivion_config.json. `
          + `Your current config will be saved as a pre-restore backup first, `
          + `so this is reversible.\n\nThe app will need a restart to load the restored config.`
        )) return;
        btn.disabled = true;
        try {
          const out = await api.config_restore(name);
          toast(`✓ Restored. Pre-restore backup: ${out.pre_restore_backup}. Restart the app.`,
                'var(--ok)');
          close();
        } catch (e) {
          toast(`Restore failed: ${e.message}`, 'var(--bad)');
          btn.disabled = false;
        }
      });
    });
  });

  // v0.14.2 — cfg-bot-save removed; Bots are now part of cfg-server-save above.

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

    // Reflect a detected update immediately (don't wait for the next poll).
    el('cfg-update-btn')?.classList.toggle('update-pending', !!state.server.update_available);
    el('cfg-update-btn')?.addEventListener('click', () => {
      if (state.server.running) { toast('Stop the server before updating', 'var(--bad)'); return; }
      modal(
        'Update / Validate CS2 Server',
        '<p style="color:var(--text-3);font-size:.86rem">Runs steamcmd <code>app_update 730 validate</code> against the live install (in place — no duplicate). Use this to force an update/repair even when no update badge is shown. Progress appears in the live log.</p>',
        async () => {
          try { await api.updateCs2(); toast('CS2 update started — check the log'); }
          catch (e) { toast(e.message, 'var(--red)'); }
        },
        'Update / Validate'
      );
    });
  }

  // RCON console (local sessions only)
  const rconSendBtn = el('rcon-send');
  if (rconSendBtn) {
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
    rconSendBtn.addEventListener('click', rconSend);
    el('rcon-cmd').addEventListener('keydown', e => { if (e.key === 'Enter') rconSend(); });
  }
};

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
        <div class="setup-emblem-frame">
          <span class="lb-bracket lb-tl"></span>
          <span class="lb-bracket lb-tr"></span>
          <span class="lb-bracket lb-bl"></span>
          <span class="lb-bracket lb-br"></span>
          <img src="/static/images/emblem.png" class="setup-emblem" alt="Oblivion">
        </div>
        <div class="setup-brand">OBLIVION</div>
        <div class="setup-sub">SERVER · TOOL — FIRST-TIME SETUP</div>

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

  /* ── Step 3: All done — with operator guided next-steps (v0.16.0 / task #167) ── */
  function renderStep3(body, footer) {
    body.innerHTML = `
      <div class="setup-done-icon">✓</div>
      <div class="setup-section-title" style="text-align:center;margin-top:12px">You're all set!</div>
      <p class="setup-hint" style="text-align:center">
        Your server directory and PIN are saved.<br>
        Here's the typical path to your first tournament — three clicks:
      </p>
      <ol class="setup-next-steps">
        <li>
          <strong>Install CS2 server</strong>
          <small>Open the <em>Config</em> tab → <strong>Install / Reinstall</strong>.
          ~15 GB via steamcmd. Walk away for 10 minutes.</small>
        </li>
        <li>
          <strong>Pick a Quick-Apply Pack</strong>
          <small>Open the <em>Plugins</em> tab → choose
          <strong>Competitive 5v5</strong> (MatchZy + de_dust2), or
          <strong>Warcraft Night</strong>, or any of the 5 packs.
          One click stages mode + map + plugins.</small>
        </li>
        <li>
          <strong>Start the server + roll out captain links</strong>
          <small>Open the <em>Status</em> tab → <strong>Start Server</strong>.
          Then go to <em>Veto</em>, paste in your roster, hit
          <strong>Distribute</strong>. Captain links DM out (if Discord configured) or
          copy-paste to share.</small>
        </li>
      </ol>
      <p class="setup-hint" style="text-align:center;margin-top:14px">
        Discord integration is optional — set it up from
        <em>Config</em> → <strong>Discord</strong> when you're ready.
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

    <!-- Keybinds — full width below the two columns (admin only) -->
    <div class="appearance-section keybinds-section admin-only">
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

// Idempotency guard — init() is bound to DOMContentLoaded both here and at
// the bottom of the file (the shell-modules block at ~line 3108).  Both
// listeners fire once today so no real bug, but any future "re-login without
// page reload" or other re-entry path would double every keydown handler
// and SSE subscription.  This flag closes that door cheaply.
let _initialised = false;

// v0.10.2 — Hamburger drawer for the sidebar on phones.  Activates only
// when the .veto-stage CSS @media query has put the sidebar in slide-out
// position (below 640px).  On desktop the button is display:none so the
// listener never fires there.
function _wireMobileHamburger() {
  const btn = el('hamburger');
  const sidebar = el('sidebar');
  if (!btn || !sidebar) return;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    sidebar.classList.toggle('is-open');
  });
  // Tap anywhere else (including the dim backdrop) closes the drawer
  document.addEventListener('click', (e) => {
    if (!sidebar.classList.contains('is-open')) return;
    if (sidebar.contains(e.target) || btn.contains(e.target)) return;
    sidebar.classList.remove('is-open');
  });
  // Closing the drawer when a nav item is tapped (so the user lands on
  // the page and sees their content, not the drawer over it)
  sidebar.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => sidebar.classList.remove('is-open'));
  });
  // Hashchange (nav arrived from elsewhere) also closes
  window.addEventListener('hashchange', () => sidebar.classList.remove('is-open'));
}

// v0.10.2 NOTE: the dedicated `_wireMobileSSEReconnect()` from earlier
// in this release is gone — the shared `_oblivionSSE` module now handles
// visibilitychange + online re-arm centrally for every registered stream.
// init() no longer calls _wireMobileSSEReconnect.
function _wireMobileSSEReconnect() { /* superseded by _oblivionSSE */ }

async function init() {
  if (_initialised) return;
  _initialised = true;
  // Apply saved appearance settings before anything renders
  loadAppSettings();
  // v0.10.2 — mobile wiring
  _wireMobileHamburger();
  _wireMobileSSEReconnect();

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
    if (!state.isAdmin) return;                              // guests: keybinds disabled
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
    const s = state.server;
    if (s.public_ip) copyText(`connect ${s.public_ip}:${s.rcon_port}`, 'Connect string');
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
  // v0.10.2: poll cadence dropped from 3 s → 10 s.  With 7 connected users
  // (admin + 2 captains + 4 spectators) at 3 s, the tunnel was carrying ~140
  // round-trips/min just for state polling.  At 10 s that's ~42/min — still
  // live-feeling for slow-moving fields (uptime, player count) without
  // hammering metered mobile data.  Fast-moving state (veto + log) already
  // arrives via SSE so the polling interval doesn't affect them.
  _stateInterval = setInterval(pollState, 10000);
  // Fire an immediate extra poll whenever the page becomes visible (phone
  // unlock / tab-switch back) so the UI catches up instantly instead of
  // waiting up to 10 s for the next interval tick.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') pollState();
  });

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

/* ════════════════════════════════════════════════════════════════ SHELL MODULES
   New v2 shell — log drawer, connect popover, ⌘K palette.
   These are page-agnostic and live above the page handlers.            */

/* ── Log drawer ───────────────────────────────────────────────────────────── */
window.LogDrawer = {
  expanded: false,
  init() {
    const bar = el('log-drawer-bar');
    if (!bar) return;
    bar.addEventListener('click', e => {
      // Clicks on Copy/Save (or any explicit button inside the bar) must NOT
      // toggle the drawer — they have their own handlers below.
      if (e.target.closest('button')) return;
      this.toggle();
    });

    // Copy: pull the full server-side history (not just what's in the SSE
    // buffer), join with newlines, push to clipboard. Falls back to a textarea
    // if navigator.clipboard isn't available (see copyText).
    const copyBtn = el('log-drawer-copy');
    if (copyBtn) copyBtn.addEventListener('click', async e => {
      e.stopPropagation();
      try {
        const lines = await api.logHistory();
        copyText((lines || []).join('\n'), 'Log');
      } catch (err) {
        // Fall back to whatever the client has buffered.
        copyText(logLines.join('\n'), 'Log');
      }
    });

    // Save: ask the server to write the log to a known file and report the path.
    const saveBtn = el('log-drawer-save');
    if (saveBtn) saveBtn.addEventListener('click', async e => {
      e.stopPropagation();
      try {
        const r = await api.logSave();
        // Show the filename in the toast; the full path is logged via the
        // [log] line so the user can find it from the log itself.
        const fname = (r.path || '').split(/[\\/]/).pop() || 'log file';
        toast(`Saved → ${fname}`, 'var(--ok)');
      } catch (err) {
        toast(`Save failed: ${err.message}`, 'var(--bad)');
      }
    });

    // Backtick to toggle (skipped when typing)
    document.addEventListener('keydown', e => {
      if (e.key === '`' && !e.target.matches('input,textarea,select')) {
        if (document.querySelector('.modal-overlay,.setup-overlay,.palette-overlay:not(.hidden)')) return;
        e.preventDefault();
        this.toggle();
      }
    });

    this._renderLast();
  },
  toggle() {
    this.expanded = !this.expanded;
    el('log-drawer').classList.toggle('expanded', this.expanded);
    el('log-drawer-body').classList.toggle('hidden', !this.expanded);
    el('log-drawer-meta').classList.toggle('hidden', !this.expanded);
    el('log-drawer-chev').textContent = this.expanded ? '▾' : '▸';
    if (this.expanded) this._renderFull();
  },
  _renderLast() {
    const last = el('log-drawer-last');
    if (last && logLines.length) {
      last.textContent = logLines[logLines.length - 1];
    }
  },
  _renderFull() {
    const body = el('log-drawer-body');
    if (!body) return;
    body.innerHTML = '';
    logLines.forEach(l => {
      const d = document.createElement('div');
      d.className = 'ld-line';
      d.textContent = l;
      body.appendChild(d);
    });
    if (appSettings.autoScroll) body.scrollTop = body.scrollHeight;
    const count = el('log-drawer-count');
    if (count) count.textContent = logLines.length;
  },
  append(line) {
    this._renderLast();
    if (!this.expanded) return;
    const body = el('log-drawer-body');
    if (!body) return;
    const d = document.createElement('div');
    d.className = 'ld-line';
    d.textContent = line;
    body.appendChild(d);
    const limit = appSettings.logLines || 400;
    while (body.children.length > limit) body.removeChild(body.firstChild);
    if (appSettings.autoScroll) body.scrollTop = body.scrollHeight;
    const count = el('log-drawer-count');
    if (count) count.textContent = logLines.length;
  }
};

/* ── Connect popover ──────────────────────────────────────────────────────── */
window.ConnectPopover = {
  init() {
    const btn = el('hdr-connect-btn');
    if (!btn) return;

    btn.addEventListener('click', e => {
      e.stopPropagation();
      this.toggle();
    });

    document.addEventListener('click', e => {
      const pop = el('connect-popover');
      if (!pop || pop.classList.contains('hidden')) return;
      if (!pop.contains(e.target) && !btn.contains(e.target)) this.hide();
    });

    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && !el('connect-popover').classList.contains('hidden')) {
        this.hide();
      }
    });

    document.querySelectorAll('.cp-copy').forEach(b => {
      b.addEventListener('click', () => {
        const target = b.dataset.target;
        const val = (target === 'lan' ? el('cp-lan-val') : el('cp-pub-val')).textContent;
        if (val && val !== '—') copyText(val.trim(), 'Connect string');
      });
    });
  },
  update(s) {
    const btn = el('hdr-connect-btn');
    if (!btn) return;
    const running = s.boot_state === 'ready' || s.boot_state === 'booting';
    btn.classList.toggle('hidden', !running);
    if (!running) { this.hide(); return; }

    const lan = s.lan_ip ? `connect ${s.lan_ip}:${s.rcon_port || 27015}` : '—';
    const pub = s.public_ip ? `connect ${s.public_ip}` : '—';
    el('cp-lan-val').textContent = lan;
    el('cp-pub-val').textContent = pub;

    const pubK = el('cp-pub-k');
    // v1.0.1: badge previously read s.public_ip as a proxy for "GSLT verified"
    // which was wrong — public_ip is just "did we manage to detect our WAN IP",
    // it says nothing about whether +sv_setsteamaccount was passed at launch.
    // Operators saw a confident green badge while remote clients silently failed
    // to connect (Valve auth rejects external handshakes without a GSLT).
    if (s.public_ip && s.gslt_set) {
      pubK.textContent = 'Public · GSLT set';
      pubK.className = 'cp-k ok';
    } else if (s.public_ip) {
      pubK.textContent = 'Public · GSLT MISSING — remote clients will fail';
      pubK.className = 'cp-k bad';
    } else {
      pubK.textContent = 'Public · detecting…';
      pubK.className = 'cp-k';
    }

    const warn = el('cp-warn');
    // /api/state doesn't expose the raw sv_password (would leak the secret to
    // any guest polling state).  It DOES expose `sv_password_set: bool` —
    // read that instead.  Before this fix, `s.sv_password` was always
    // undefined and the warning displayed on every poll regardless of the
    // actual configured password.
    if (!s.sv_password_set) {
      warn.classList.remove('hidden');
      el('cp-warn-l').textContent = 'No password set · anyone with the IP can join.';
      el('cp-warn-a').onclick = () => { this.hide(); navigate('config'); };
    } else {
      warn.classList.add('hidden');
    }
  },
  toggle() {
    const pop = el('connect-popover');
    pop.classList.contains('hidden') ? this.show() : this.hide();
  },
  show() {
    const pop = el('connect-popover');
    const btn = el('hdr-connect-btn');
    pop.classList.remove('hidden');
    const r = btn.getBoundingClientRect();
    // v0.10.2 — clamp the popover's left edge so it can't overflow the
    // right side of the viewport on narrow phones (was hard-pinned to
    // r.left which on a 375px iPhone pushed it ~100-200px off-screen).
    // Measure the popover's actual width AFTER making it visible, then
    // clamp left = max(8, min(r.left, vw - popWidth - 8)).
    const vw = document.documentElement.clientWidth;
    const popWidth = Math.min(pop.offsetWidth || 380, vw - 16);
    const leftMax = vw - popWidth - 8;
    const left = Math.max(8, Math.min(r.left, leftMax));
    pop.style.left = left + 'px';
    pop.style.right = 'auto';
    pop.style.margin = '0';
  },
  hide() { el('connect-popover').classList.add('hidden'); }
};

/* ── ⌘K Command palette ───────────────────────────────────────────────────── */
window.Palette = {
  results: [],
  selected: 0,
  rconOnly: false,
  init() {
    const trigger = el('hdr-cmd-trigger');
    if (trigger) trigger.addEventListener('click', () => this.show());

    document.addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault(); this.show();
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'p' && !e.target.matches('input,textarea,select')) {
        e.preventDefault(); if (state.server.is_local) this.show(true);   // RCON is local-only
      }
      if (e.key === 'Escape' && !el('palette-overlay').classList.contains('hidden')) {
        e.preventDefault(); this.hide();
      }
    });

    const input = el('palette-input');
    if (input) {
      input.addEventListener('input', () => this.update(input.value));
      input.addEventListener('keydown', e => {
        if (e.key === 'ArrowDown')      { e.preventDefault(); this.move(1); }
        else if (e.key === 'ArrowUp')   { e.preventDefault(); this.move(-1); }
        else if (e.key === 'Enter')     { e.preventDefault(); this.execute(); }
      });
    }
    const dim = el('palette-dim');
    if (dim) dim.addEventListener('click', () => this.hide());
  },
  show(rconOnly = false) {
    this.rconOnly = !!rconOnly && !!state.server.is_local;   // RCON is local-only
    el('palette-overlay').classList.remove('hidden');
    const input = el('palette-input');
    input.value = '';
    input.placeholder = this.rconOnly
      ? 'rcon command…'
      : 'type a command, name, map, or RCON …';
    input.focus();
    this.update('');
  },
  hide() {
    el('palette-overlay').classList.add('hidden');
    this.rconOnly = false;
  },
  _allCommands() {
    const cmds = [];

    // Navigation
    [['Status','status'], ['Players','players'], ['Maps','maps'], ['Appearance','appearance'], ['Config','config']]
      .forEach(([label, hash]) => {
        cmds.push({ kind: 'goto', label: `Go to ${label}`, run: () => navigate(hash) });
      });

    // Match controls (only when running)
    if (state.server.running) {
      cmds.push({ kind: 'match', label: 'Restart round', run: async () => { await api.restartRound(); toast('Round restarted'); } });
      cmds.push({ kind: 'match', label: 'End warmup',    run: async () => { await api.endWarmup();    toast('Warmup ended'); } });
      cmds.push({ kind: 'match', label: 'Pause match',   run: async () => { await api.pause();        toast('Match paused'); } });
      cmds.push({ kind: 'match', label: 'Unpause match', run: async () => { await api.unpause();      toast('Match unpaused'); } });
      cmds.push({ kind: 'server', label: 'Stop server',    run: async () => { await api.stop();        toast('Server stopping…'); } });
      cmds.push({ kind: 'server', label: 'Restart server', run: doQuickRestart });
    }

    // Bots
    cmds.push({ kind: 'bot', label: 'Add 1 bot',     run: async () => { await api.addBots(1); toast('+1 bot');    } });
    cmds.push({ kind: 'bot', label: 'Add 5 bots',    run: async () => { await api.addBots(5); toast('+5 bots');   } });
    cmds.push({ kind: 'bot', label: 'Kick all bots', run: async () => { await api.kickBots(); toast('Bots kicked'); } });

    // Maps — up to 30
    (state.maps || []).slice(0, 30).forEach(m => {
      cmds.push({
        kind: 'map',
        label: `Change map to ${m}`,
        hint: state.server.mode || 'competitive',
        run: async () => { await api.map(m, state.server.mode || 'competitive', false); toast(`Changing to ${m}…`); }
      });
    });

    return cmds;
  },
  update(query) {
    const q = query.toLowerCase().trim();

    // RCON-only mode: every input is a raw RCON command
    if (this.rconOnly) {
      this.results = q
        ? [{ kind: 'rcon', label: query, hint: 'raw RCON', run: () => this._sendRcon(query) }]
        : [];
      this.selected = 0;
      this.render();
      return;
    }

    let results = q
      ? this._allCommands().filter(c => c.label.toLowerCase().includes(q))
      : this._allCommands();

    // RCON fallthrough: `rcon ...` or `/...` prefix (local sessions only)
    if (state.server.is_local && (q.startsWith('rcon ') || q.startsWith('/'))) {
      const raw = query.replace(/^(rcon\s+|\/)/i, '');
      if (raw.trim()) {
        results = [
          { kind: 'rcon', label: raw, hint: 'raw RCON', run: () => this._sendRcon(raw) },
          ...results
        ];
      }
    }

    this.results = results.slice(0, 60);
    this.selected = 0;
    this.render();
  },
  async _sendRcon(cmd) {
    try {
      const r = await api.rcon(cmd);
      toast((r.response || '(no output)').slice(0, 100));
    } catch (e) { toast(e.message, 'var(--bad)'); }
  },
  render() {
    const c = el('palette-results');
    c.innerHTML = '';

    if (!this.results.length) {
      const empty = document.createElement('div');
      empty.className = 'palette-empty';
      empty.textContent = this.rconOnly ? 'type a raw RCON command' : 'no matches';
      c.appendChild(empty);
      el('palette-count').textContent = '0 results';
      return;
    }

    // Group by kind
    const groups = {};
    this.results.forEach((r, i) => {
      (groups[r.kind] = groups[r.kind] || []).push({ ...r, idx: i });
    });
    const order  = ['rcon', 'goto', 'match', 'server', 'bot', 'map'];
    const labels = { rcon: 'RCON', goto: 'Navigate', match: 'Match', server: 'Server', bot: 'Bots', map: 'Maps' };

    order.forEach(k => {
      if (!groups[k]) return;
      const sec = document.createElement('div');
      sec.className = 'palette-section';
      const grp = document.createElement('div');
      grp.className = 'palette-grp';
      grp.textContent = labels[k] || k;
      sec.appendChild(grp);
      groups[k].forEach(r => {
        const row = document.createElement('div');
        row.className = 'palette-row' + (r.idx === this.selected ? ' active' : '');

        const kind = document.createElement('span');
        kind.className = 'palette-kind';
        kind.textContent = k;
        row.appendChild(kind);

        const label = document.createElement('span');
        label.className = 'palette-label';
        label.textContent = r.label;
        if (r.hint) {
          const dim = document.createElement('span');
          dim.className = 'dim';
          dim.textContent = ' · ' + r.hint;
          label.appendChild(dim);
        }
        row.appendChild(label);

        if (r.idx === this.selected) {
          const ent = document.createElement('span');
          ent.className = 'palette-ent';
          ent.textContent = '↵';
          row.appendChild(ent);
        }

        row.addEventListener('click', () => { this.selected = r.idx; this.execute(); });
        sec.appendChild(row);
      });
      c.appendChild(sec);
    });

    el('palette-count').textContent = this.results.length + (this.results.length === 1 ? ' result' : ' results');
  },
  move(d) {
    if (!this.results.length) return;
    this.selected = (this.selected + d + this.results.length) % this.results.length;
    this.render();
  },
  execute() {
    const r = this.results[this.selected];
    if (!r) return;
    this.hide();
    try { Promise.resolve(r.run()).catch(e => toast(e.message, 'var(--bad)')); }
    catch (e) { toast(e.message, 'var(--bad)'); }
  }
};

/* ── Keyboard cheat sheet (?) ─────────────────────────────────────────────── */
window.CheatSheet = {
  init() {
    const trigger = el('hdr-help-btn');
    if (trigger) trigger.addEventListener('click', () => this.show());

    document.addEventListener('keydown', e => {
      if (e.key === '?' && !e.target.matches('input,textarea,select')) {
        if (document.querySelector('.modal-overlay,.setup-overlay,.palette-overlay:not(.hidden)')) return;
        e.preventDefault();
        this.show();
      }
      if (e.key === 'Escape' && !el('cheatsheet-overlay').classList.contains('hidden')) {
        e.preventDefault(); this.hide();
      }
    });

    const dim = el('cheatsheet-dim');
    if (dim) dim.addEventListener('click', () => this.hide());
  },
  show() { el('cheatsheet-overlay')?.classList.remove('hidden'); },
  hide() { el('cheatsheet-overlay')?.classList.add('hidden'); }
};

/* ── Wire shell modules after init has set up DOM + state ─────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  // run after init() finishes its first state poll (modest delay)
  setTimeout(() => {
    LogDrawer.init();
    ConnectPopover.init();
    Palette.init();
    CheatSheet.init();
  }, 200);
});
