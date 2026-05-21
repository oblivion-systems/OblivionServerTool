"""
web.py — Flask remote admin panel.

Provides a PIN-protected web interface for basic server control and
a server-sent-events (SSE) log stream.  Runs on a daemon thread in main.py.
"""
from __future__ import annotations

import functools
import queue
import threading
import time
from collections.abc import Callable

from flask import Flask, Response, jsonify, render_template_string, request

from .config import ADMIN_PIN, GAME_MODES, OFFICIAL_MAPS, load_workshop
from .core import AppCore


# ── HTML template ──────────────────────────────────────────────────────────────

_WEB = r"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oblivion Server Tool</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#09090e;color:#e8e8f4;font-family:'Segoe UI',sans-serif;min-height:100vh}
.hdr{background:#0f0f16;border-bottom:2px solid #a78bfa;padding:0 24px;height:56px;display:flex;align-items:center;gap:8px}
.hdr-brand{font-size:1.1rem;font-weight:700;color:#a78bfa;letter-spacing:2px}
.hdr-sub{font-size:.72rem;color:#6b6b80;letter-spacing:1px;padding-top:6px}
.badge{background:#a78bfa;color:#09090e;font-size:.68rem;padding:2px 9px;border-radius:10px;text-transform:uppercase;font-weight:700;margin-left:auto}
.wrap{max-width:860px;margin:28px auto;padding:0 16px;display:grid;grid-template-columns:1fr 1fr;gap:20px}
.card{background:#0f0f16;border-radius:12px;padding:20px;border:1px solid #1c1c28;transition:border-color .2s}
.card:hover{border-color:#2a2a40}
.card h2{font-size:.75rem;color:#6b6b80;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:14px}
label{display:block;font-size:.82rem;color:#6b6b80;margin:10px 0 3px}
select,input[type=text]{width:100%;background:#060609;color:#e8e8f4;border:1px solid #1c1c28;border-radius:6px;padding:8px 10px;font-size:.88rem;outline:none;margin-top:3px;transition:border-color .15s}
select:focus,input[type=text]:focus{border-color:#a78bfa}
.btn{width:100%;margin-top:14px;padding:10px;border:none;border-radius:8px;font-size:.88rem;font-weight:700;cursor:pointer;transition:background .18s,transform .08s}
.btn-red{background:#a78bfa;color:#09090e}.btn-red:hover{background:#8b5cf6}
.btn-red:active{transform:scale(.97)}
.sb{grid-column:1/-1;background:#0f0f16;border-radius:12px;padding:14px 20px;border:1px solid #1c1c28;display:flex;gap:28px;align-items:center}
.dot{width:10px;height:10px;border-radius:50%}.on{background:#22c55e;box-shadow:0 0 8px #22c55e70}.off{background:#ef4444}
.sl{font-size:.82rem;color:#6b6b80}.sv{color:#e8e8f4;font-weight:500}
.lp{grid-column:1/-1}
.lb{background:#060609;border-radius:8px;padding:12px;height:190px;overflow-y:auto;font-family:Consolas,monospace;font-size:.78rem;color:#6b9080;border:1px solid #1c1c28}
.lb::-webkit-scrollbar{width:4px}.lb::-webkit-scrollbar-track{background:#09090e}.lb::-webkit-scrollbar-thumb{background:#2a2a40;border-radius:2px}
.le{padding:1px 0;border-bottom:1px solid #1c1c2820}
.toast{position:fixed;bottom:22px;right:22px;background:#a78bfa;color:#09090e;padding:10px 18px;border-radius:8px;font-size:.82rem;font-weight:600;display:none}
.req-st{font-size:.78rem;margin-top:8px;min-height:1.1em}
.req-ok{color:#22c55e}.req-err{color:#ef4444}.req-pend{color:#f59e0b}
.login{display:flex;align-items:center;justify-content:center;min-height:100vh;background:#09090e}
.lc{background:#0f0f16;border-radius:16px;padding:36px 28px;width:310px;border:1px solid #1c1c28;text-align:center;position:relative;overflow:hidden}
.lc::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:#a78bfa}
.lc-brand{color:#a78bfa;font-size:1.3rem;font-weight:700;letter-spacing:3px;margin-bottom:4px}
.lc-sub{color:#6b6b80;font-size:.7rem;letter-spacing:2px;margin-bottom:24px}
.pin-dots{display:flex;justify-content:center;gap:14px;margin-bottom:24px}
.pin-dot{width:13px;height:13px;border-radius:50%;background:#060609;border:2px solid #1c1c28;transition:background .15s,border-color .15s,box-shadow .15s}
.pin-dot.filled{background:#a78bfa;border-color:#a78bfa;box-shadow:0 0 8px #a78bfa80}
.pin-dot.shake{animation:shake .3s}
@keyframes shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-5px)}75%{transform:translateX(5px)}}
.keypad{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
.key{background:#060609;border:1px solid #1c1c28;color:#e8e8f4;border-radius:10px;padding:14px 0;font-size:1.1rem;font-weight:600;cursor:pointer;transition:background .12s,border-color .12s,transform .08s;user-select:none}
.key:hover{background:#13131e;border-color:#a78bfa}
.key:active{transform:scale(.93)}
.key.del{color:#a78bfa}
.err{color:#ef4444;font-size:.78rem;min-height:1.1em;margin-top:4px}
.lockout{color:#f59e0b;font-size:.8rem;margin-top:6px}
</style></head><body>
{% if not authed %}
<div class="login"><div class="lc">
<div class="lc-brand">OBLIVION</div><div class="lc-sub">SERVER TOOL</div>
<div class="pin-dots">
  {% for i in range(pin_len) %}<div class="pin-dot" id="d{{i}}"></div>{% endfor %}
</div>
<div class="keypad">
  <button class="key" onclick="press('7')">7</button>
  <button class="key" onclick="press('8')">8</button>
  <button class="key" onclick="press('9')">9</button>
  <button class="key" onclick="press('4')">4</button>
  <button class="key" onclick="press('5')">5</button>
  <button class="key" onclick="press('6')">6</button>
  <button class="key" onclick="press('1')">1</button>
  <button class="key" onclick="press('2')">2</button>
  <button class="key" onclick="press('3')">3</button>
  <button class="key del" onclick="del()">⌫</button>
  <button class="key" onclick="press('0')">0</button>
  <button class="key" onclick="submit()">↵</button>
</div>
<div class="err" id="err"></div>
<div class="lockout" id="lk"></div>
</div></div>
<script>
const PIN_LEN = {{ pin_len }};
let pin = '', locked = false;
function updateDots() {
  for (let i = 0; i < PIN_LEN; i++)
    document.getElementById('d' + i).className = 'pin-dot' + (i < pin.length ? ' filled' : '');
}
function press(d) {
  if (locked || pin.length >= PIN_LEN) return;
  pin += d; updateDots();
  if (pin.length === PIN_LEN) setTimeout(submit, 120);
}
function del() { if (!locked && pin.length > 0) { pin = pin.slice(0, -1); updateDots(); } }
function shake() {
  document.querySelectorAll('.pin-dot').forEach(d => {
    d.classList.add('shake');
    setTimeout(() => d.classList.remove('shake'), 350);
  });
}
function submit() {
  if (!pin.length) return;
  fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pin }) })
  .then(r => r.json()).then(d => {
    if (d.ok) { location.reload(); return; }
    shake(); pin = ''; updateDots();
    document.getElementById('err').textContent = d.error || 'Wrong PIN';
    if (d.locked_for) {
      locked = true;
      document.getElementById('lk').textContent = 'Too many attempts. Try again in ' + d.locked_for + 's';
      setTimeout(() => {
        locked = false;
        document.getElementById('lk').textContent = '';
        document.getElementById('err').textContent = '';
      }, d.locked_for * 1000);
    }
  });
}
document.addEventListener('keydown', e => {
  if (e.key >= '0' && e.key <= '9') press(e.key);
  else if (e.key === 'Backspace') del();
  else if (e.key === 'Enter') submit();
});
</script>
{% else %}
<div class="hdr"><span class="hdr-brand">OBLIVION</span><span class="hdr-sub">SERVER TOOL</span><span class="badge">Remote Admin</span></div>
<div class="wrap">
  <div class="sb">
    <div class="dot off" id="sdot"></div>
    <div><div class="sl">Status <span class="sv" id="sst">—</span></div></div>
    <div><div class="sl">Map <span class="sv" id="smp">—</span></div></div>
    <div><div class="sl">Mode <span class="sv" id="smd">—</span></div></div>
  </div>
  <div class="card">
    <h2>Official Maps</h2>
    <label>Map</label>
    <select id="om">{% for m in official_maps %}<option>{{m}}</option>{% endfor %}</select>
    <label>Mode</label>
    <select id="omode">{% for m in modes %}<option>{{m}}</option>{% endfor %}</select>
    <button class="btn btn-red" onclick="go(false)">Change Map</button>
  </div>
  <div class="card">
    <h2>Workshop Maps</h2>
    <label>Workshop folder</label>
    <select id="wm">
      {% if workshop_maps %}{% for m in workshop_maps %}<option>{{m}}</option>{% endfor %}
      {% else %}<option value="">No workshop maps found</option>{% endif %}
    </select>
    <label>Mode</label>
    <select id="wmode">{% for m in modes %}<option>{{m}}</option>{% endfor %}</select>
    <button class="btn btn-red" onclick="go(true)">Change Map</button>
  </div>
  <div class="card" style="grid-column:1/-1">
    <h2>Request Workshop Map Download</h2>
    <label>Steam Workshop Map ID</label>
    <input type="text" id="wsid" placeholder="e.g. 3070720081" maxlength="20"
           oninput="this.value=this.value.replace(/\D/g,'')">
    <button class="btn btn-red" style="margin-top:10px" onclick="reqWS()">Request Download</button>
    <div class="req-st" id="req-st"></div>
  </div>
  <div class="card lp"><h2>Live Log</h2><div class="lb" id="lb"></div></div>
</div>
<div class="toast" id="toast"></div>
<script>
function toast(m) {
  const t = document.getElementById('toast');
  t.textContent = m; t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 2600);
}
function go(wk) {
  const map  = document.getElementById(wk ? 'wm'    : 'om').value;
  const mode = document.getElementById(wk ? 'wmode' : 'omode').value;
  if (!map) return;
  fetch('/api/change_map', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ map, mode, workshop: wk }) })
  .then(r => r.json()).then(d => toast(d.ok ? 'Map change sent' : 'Error: ' + d.error));
}
function poll() {
  fetch('/api/status').then(r => r.json()).then(d => {
    document.getElementById('sdot').className = 'dot ' + (d.running ? 'on' : 'off');
    document.getElementById('sst').textContent = d.running ? 'Online' : 'Offline';
    document.getElementById('smp').textContent = d.map  || '—';
    document.getElementById('smd').textContent = d.mode || '—';
  }).catch(() => {});
}
setInterval(poll, 3000); poll();
const es = new EventSource('/api/log/stream');
const lb = document.getElementById('lb');
es.onmessage = e => {
  const d = document.createElement('div');
  d.className = 'le'; d.textContent = e.data;
  lb.appendChild(d); lb.scrollTop = lb.scrollHeight;
};
fetch('/api/log/history').then(r => r.json()).then(lines => {
  lines.forEach(l => {
    const d = document.createElement('div');
    d.className = 'le'; d.textContent = l; lb.appendChild(d);
  });
  lb.scrollTop = lb.scrollHeight;
});
function reqWS() {
  const id = document.getElementById('wsid').value.trim();
  const st = document.getElementById('req-st');
  if (!id) { st.className = 'req-st req-err'; st.textContent = 'Enter a workshop ID first'; return; }
  st.className = 'req-st req-pend'; st.textContent = 'Sending request…';
  fetch('/api/request_workshop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workshop_id: id }) })
  .then(r => r.json()).then(d => {
    if (d.ok) { st.className = 'req-st req-ok'; st.textContent = 'Request sent — waiting for approval'; }
    else { st.className = 'req-st req-err'; st.textContent = d.error || 'Error'; }
  }).catch(() => { st.className = 'req-st req-err'; st.textContent = 'Network error'; });
}
</script>
{% endif %}
</body></html>"""


# ── PIN rate limiter ───────────────────────────────────────────────────────────

_MAX_ATTEMPTS = 5
_LOCKOUT_SECS = 300
_attempts:      dict[str, dict] = {}
_attempts_lock  = threading.Lock()


def _prune_attempts() -> None:
    now = time.time()
    with _attempts_lock:
        stale = [ip for ip, r in _attempts.items()
                 if r["count"] < _MAX_ATTEMPTS or r["until"] <= now]
        for ip in stale:
            del _attempts[ip]


def _check_lockout(ip: str) -> int:
    _prune_attempts()
    with _attempts_lock:
        rec = _attempts.get(ip)
        if rec and rec["count"] >= _MAX_ATTEMPTS:
            remaining = int(rec["until"] - time.time())
            if remaining > 0:
                return remaining
            del _attempts[ip]
    return 0


def _record_fail(ip: str) -> None:
    with _attempts_lock:
        rec = _attempts.setdefault(ip, {"count": 0, "until": 0.0})
        rec["count"] += 1
        if rec["count"] >= _MAX_ATTEMPTS:
            rec["until"] = time.time() + _LOCKOUT_SECS


def _clear_attempts(ip: str) -> None:
    with _attempts_lock:
        _attempts.pop(ip, None)


# ── Flask app factory ──────────────────────────────────────────────────────────

def create_flask(core: AppCore) -> Flask:
    app = Flask(__name__)

    def require_auth(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if request.cookies.get("adm") != ADMIN_PIN:
                return jsonify({"error": "unauthorized"}), 401
            return f(*args, **kwargs)
        return wrapper

    @app.route("/")
    def index():
        return render_template_string(
            _WEB,
            authed        = (request.cookies.get("adm") == ADMIN_PIN),
            official_maps = OFFICIAL_MAPS,
            workshop_maps = load_workshop(),
            modes         = GAME_MODES,
            pin_len       = len(ADMIN_PIN),
        )

    @app.route("/api/login", methods=["POST"])
    def login():
        ip   = request.remote_addr
        wait = _check_lockout(ip)
        if wait:
            return jsonify({"ok": False, "error": "Too many attempts",
                            "locked_for": wait}), 429
        pin = (request.get_json() or {}).get("pin", "")
        if pin == ADMIN_PIN:
            _clear_attempts(ip)
            core.log(f"Web login from {ip}")
            resp = jsonify({"ok": True})
            resp.set_cookie("adm", ADMIN_PIN, httponly=True, samesite="Lax")
            return resp
        _record_fail(ip)
        remaining = max(0, _MAX_ATTEMPTS - _attempts.get(ip, {}).get("count", 0))
        core.log(f"Failed web login from {ip} ({remaining} attempt(s) left)")
        out  = {"ok": False, "error": "Wrong PIN"}
        wait = _check_lockout(ip)
        if wait:
            out["locked_for"] = wait
        return jsonify(out), 401

    @app.route("/api/status")
    @require_auth
    def status():
        return jsonify({
            "running": core.running,
            "map":     core.current_map,
            "mode":    core.current_mode,
        })

    @app.route("/api/change_map", methods=["POST"])
    @require_auth
    def change_map():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        d = request.get_json() or {}
        m = d.get("map", "").strip()
        if not m:
            return jsonify({"error": "No map specified"}), 400
        core.change_map(m, d.get("mode", "Competitive"),
                        bool(d.get("workshop")), caller=request.remote_addr)
        return jsonify({"ok": True})

    @app.route("/api/request_workshop", methods=["POST"])
    @require_auth
    def request_workshop():
        wid = (request.get_json() or {}).get("workshop_id", "").strip()
        if not wid.isdigit():
            return jsonify({"error": "Invalid workshop ID — digits only"}), 400
        core.request_workshop_download(wid, requester=request.remote_addr)
        return jsonify({"ok": True})

    @app.route("/api/log/history")
    @require_auth
    def log_history():
        return jsonify(core.get_log())

    @app.route("/api/log/stream")
    @require_auth
    def log_stream():
        q = core.sse_subscribe()
        def gen():
            try:
                while True:
                    try:
                        yield f"data: {q.get(timeout=25)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                core.sse_unsubscribe(q)
        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    return app
