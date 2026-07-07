"""
web.py — Flask server: serves the SPA frontend and the REST API.

Shared between the local pywebview window and the remote web panel.
The local window is auto-authenticated via a one-time startup token.
Remote clients must enter the admin PIN.

Auth model:
  - On login (POST /api/auth/login), a random session token is generated
    and stored server-side.  The token is sent to the browser as an
    httpOnly cookie named "session".  The raw PIN is never stored anywhere.
  - The auto-auth endpoint (/auth/auto?token=...) creates a privileged
    "local" session so the pywebview window never shows the PIN keypad.
  - Local sessions never expire; remote sessions expire after 8 hours.
  - Some endpoints (server install, Steam credentials) are local-only.
"""
from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import queue
import re
import secrets
import threading
import time
from collections.abc import Callable

from flask import (Flask, Response, abort, jsonify, make_response, redirect,
                   render_template, request, send_file)

from . import config as _config
# RCON_HOST intentionally NOT imported by name — it's mutated by AppCore.
# _resolve_rcon_host() at runtime; binding the import-time value here would
# go stale exactly like the bug we fixed in core.py.  Read _config.RCON_HOST
# at call time if ever needed (today no remaining web.py path needs it).
from .config import (FLASK_PORT, GAME_MODES, OFFICIAL_MAPS,
                     RCON_PORT, MODE_MAPS, load_workshop)
from .core import AppCore


# ── Input validation ────────────────────────────────────────────────────────────
# CS2's console treats ';' and newlines as command separators, so any value
# interpolated into an RCON command line must be format-validated first.
_MAP_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")   # official map names
_DIGITS_RE   = re.compile(r"^[0-9]+$")          # workshop IDs, userids
_STEAMID_RE  = re.compile(r"^[A-Za-z0-9:\[\]_]{1,64}$")  # length-capped STEAM_/[U:..]/765... forms
_NAME_MAX_LEN = 64                              # cap on player names sent to RCON
_BROADCAST_MAX_LEN = 200                        # cap on say/broadcast bodies


# ── Session store ──────────────────────────────────────────────────────────────

_REMOTE_SESSION_TTL = 8 * 3600     # 8 hours for remote sessions
_sessions:      dict[str, dict] = {}   # token → {ip, is_local, created_at}
_sessions_lock  = threading.Lock()


def _create_session(ip: str, is_local: bool = False, role: str = "admin") -> str:
    token = secrets.token_hex(32)
    with _sessions_lock:
        _sessions[token] = {
            "ip":         ip,
            "is_local":   is_local,
            "role":       role,        # "admin" (full) | "guest" (maps/modes/downloads)
            "created_at": time.time(),
        }
    return token


def _request_is_https() -> bool:
    """v0.11.17 A3 — was the original client request HTTPS?

    `request.is_secure` only knows about the immediate hop's scheme.
    For tunneled traffic the chain is:
        captain's browser (HTTPS)
          → Cloudflare edge
          → cloudflared on this machine (terminates TLS)
          → Werkzeug socket (plain HTTP loopback)
    So `is_secure` reports False even when the captain is on HTTPS.
    Cloudflare quick tunnels set `X-Forwarded-Proto: https`, so check
    that explicitly.  Returns True if either the direct hop is HTTPS or
    a trusted Cloudflare-style header reports the original was HTTPS.

    Used to gate the `Secure` flag on session cookies — without it,
    captains clicking their DM'd token link from Discord's in-app
    browser sometimes silently drop the cookie (samesite=Strict + no
    Secure on an HTTPS page is treated inconsistently by mobile
    webviews), leaving the captain in an unauthed loop.
    """
    if request.is_secure:
        return True
    return request.headers.get("X-Forwarded-Proto", "").lower() == "https"


def _get_session(token: str) -> dict | None:
    now = time.time()
    with _sessions_lock:
        session = _sessions.get(token)
        if session is None:
            return None
        if not session["is_local"]:
            if now - session["created_at"] > _REMOTE_SESSION_TTL:
                del _sessions[token]
                return None
        return session


def _clear_session(token: str) -> None:
    with _sessions_lock:
        _sessions.pop(token, None)


# ── PIN brute-force lockout ────────────────────────────────────────────────────

_MAX_ATTEMPTS  = 5
_LOCKOUT_SECS  = 300
_ATTEMPT_TTL_SECS = 600        # garbage-collect attempts older than this
_attempts:      dict[str, dict] = {}   # ip → {count, until, last}
_attempts_lock  = threading.Lock()

# Atomic compare-and-clear lock for /auth/auto's single-use startup token.
_startup_token_lock = threading.Lock()


def _check_lockout(ip: str) -> int:
    now = time.time()
    with _attempts_lock:
        # GC: both stale-locked entries AND any entry whose last activity is
        # past the TTL.  Without the TTL prune, a slow drip of distinct-IP
        # failures (each below _MAX_ATTEMPTS) leaves the dict growing forever.
        stale = [k for k, r in _attempts.items()
                 if (r["count"] >= _MAX_ATTEMPTS and r["until"] <= now)
                 or (r.get("last", 0.0) and now - r["last"] > _ATTEMPT_TTL_SECS)]
        for k in stale:
            del _attempts[k]
        rec = _attempts.get(ip)
        if rec and rec["count"] >= _MAX_ATTEMPTS:
            remaining = int(rec["until"] - now)
            if remaining > 0:
                return remaining
            del _attempts[ip]
    return 0


def _record_fail(ip: str) -> None:
    with _attempts_lock:
        rec = _attempts.setdefault(ip, {"count": 0, "until": 0.0, "last": 0.0})
        rec["count"] += 1
        rec["last"]   = time.time()    # touch for the TTL prune above
        if rec["count"] >= _MAX_ATTEMPTS:
            rec["until"] = time.time() + _LOCKOUT_SECS


def _clear_attempts(ip: str) -> None:
    with _attempts_lock:
        _attempts.pop(ip, None)


# Global backoff — defends against a distributed brute force that spreads
# attempts across many source IPs to dodge the per-IP lockout above.
_GLOBAL_MAX_ATTEMPTS = 20
_GLOBAL_LOCKOUT_SECS = 300
_GLOBAL_DECAY_SECS   = 600     # forget the running count after this much quiet
_global: dict[str, float] = {"count": 0.0, "until": 0.0, "last": 0.0}
_global_lock = threading.Lock()


def _check_global_lockout() -> int:
    now = time.time()
    with _global_lock:
        if _global["count"] >= _GLOBAL_MAX_ATTEMPTS and _global["until"] > now:
            return int(_global["until"] - now)
        if _global["last"] and now - _global["last"] > _GLOBAL_DECAY_SECS:
            _global["count"] = 0.0
    return 0


def _record_global_fail() -> None:
    now = time.time()
    with _global_lock:
        _global["count"] += 1
        _global["last"]   = now
        if _global["count"] >= _GLOBAL_MAX_ATTEMPTS:
            _global["until"] = now + _GLOBAL_LOCKOUT_SECS


def _clear_global() -> None:
    with _global_lock:
        _global["count"] = 0.0
        _global["until"] = 0.0


# v0.11.0 polish — Standalone spectator page.  Served by /spectate; polls
# /api/veto/spectator/state every 3s with the embedded token.  Kept as a
# string literal (not a Jinja template) so it has zero external deps and
# works inside an OBS browser source with no auth flow.  Token marker
# __TOKEN__ is replaced at request time.
SPECTATOR_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Veto · Spectator</title>
<style>
  /* v0.16.14 / task #170 — broadcast-grade spectator surface.
     OBS-friendly query params:
       ?bg=transparent  →  no background (chroma over OBS scene)
       ?bg=green        →  solid #00b140 chroma key
       ?bg=blue         →  solid #0047bb chroma key
       ?bg=dark         →  the default dark UI (also bare /spectate)
       ?compact=1       →  tighter spacing for 720p / smaller overlays
       ?theme=light     →  light theme (for printable / projector use)
  */
  :root {
    --bg: #0b0e14;
    --panel: #131822;
    --panel-2: #1a2030;
    --line: #2a2f3a;
    --text-1: #ffffff;
    --text-2: #cfd3dc;
    --text-3: #7d8595;
    --accent: #a03af5;
    --ok: #4ade80;
    --warn: #fbbf24;
    --bad: #ef4444;
    --ban-bg: rgba(239, 68, 68, 0.18);
    --ban-fg: #f87171;
    --pick-bg: rgba(74, 222, 128, 0.18);
    --pick-fg: #86efac;
    --decider-bg: linear-gradient(135deg, #4ade80 0%, #a03af5 100%);
    --pulse: 0 0 0 0 rgba(160, 58, 245, 0.55);
  }
  html, body { margin:0; padding:0; min-height:100vh; }
  body {
    font-family: 'Inter', 'Space Grotesk', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text-1);
    padding: 32px;
    min-height: 100vh;
    box-sizing: border-box;
  }
  body[data-bg="transparent"] { background: transparent; }
  body[data-bg="green"]       { background: #00b140; }
  body[data-bg="blue"]        { background: #0047bb; }
  body[data-theme="light"] {
    --bg: #f4f5f7;
    --panel: #ffffff;
    --panel-2: #f8f9fb;
    --line: #d4d8df;
    --text-1: #1a1d23;
    --text-2: #4a4f5a;
    --text-3: #7d8595;
    background: #f4f5f7;
  }
  body[data-compact="1"] { padding: 16px; }
  body[data-compact="1"] .scoreline { font-size: 36px; }
  body[data-compact="1"] .team-card { padding: 14px 18px; }
  body[data-compact="1"] .player { font-size: 14px; padding: 4px 0; }
  body[data-compact="1"] .seq-step { font-size: 13px; padding: 6px 10px; }

  .scoreline {
    display: flex; align-items: center; justify-content: center;
    gap: 24px;
    font-size: 56px; font-weight: 700; letter-spacing: -0.01em;
    text-align: center; margin-bottom: 8px;
    line-height: 1.1;
  }
  .scoreline .vs {
    font-size: 0.5em; color: var(--text-3); font-weight: 400;
    letter-spacing: 0.4em;
  }
  .scoreline .team-name { color: var(--text-1); }
  .submeta {
    display: flex; align-items: center; justify-content: center;
    gap: 12px; margin-bottom: 32px;
    color: var(--text-3); font-size: 13px;
    letter-spacing: 0.08em; text-transform: uppercase;
  }
  .mode-pill {
    display: inline-flex; align-items: center;
    background: var(--accent); color: white;
    padding: 4px 12px; border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; font-weight: 600;
    letter-spacing: 0.1em;
  }
  .state-pill {
    color: var(--text-2); font-family: 'JetBrains Mono', monospace;
    font-size: 11px; font-weight: 500;
  }
  .state-pill.active::before {
    content: "● "; color: var(--ok);
    animation: pulse-dot 1.4s ease-in-out infinite;
  }
  @keyframes pulse-dot {
    0%, 100% { opacity: 1; }
    50%      { opacity: 0.4; }
  }

  .teams {
    display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
    max-width: 1100px; margin: 0 auto 28px;
  }
  .team-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 18px 24px;
    transition: border-color 0.4s ease, box-shadow 0.4s ease;
    position: relative;
  }
  .team-card.active {
    border-color: var(--accent);
    box-shadow: 0 0 32px -8px rgba(160, 58, 245, 0.5);
    animation: pulse-card 2.4s ease-in-out infinite;
  }
  @keyframes pulse-card {
    0%, 100% { box-shadow: 0 0 32px -8px rgba(160, 58, 245, 0.4); }
    50%      { box-shadow: 0 0 32px -8px rgba(160, 58, 245, 0.7); }
  }
  .team-card .team-hdr {
    display: flex; align-items: baseline; justify-content: space-between;
    margin-bottom: 12px; padding-bottom: 10px;
    border-bottom: 1px solid var(--line);
  }
  .team-card .team-hdr-name {
    font-size: 20px; font-weight: 700; color: var(--text-1);
  }
  .team-card .team-hdr-cap {
    font-size: 11px; color: var(--text-3);
    letter-spacing: 0.08em; text-transform: uppercase;
  }
  .team-card .team-hdr-cap strong {
    color: var(--accent); font-family: 'JetBrains Mono', monospace;
  }
  .roster { list-style: none; padding: 0; margin: 0; }
  .player {
    font-size: 15px; padding: 6px 0;
    color: var(--text-2);
    display: flex; align-items: baseline; gap: 8px;
  }
  .player.cap {
    color: var(--text-1); font-weight: 600;
  }
  .player .cap-badge {
    display: inline-block; width: 16px;
    color: var(--accent); font-weight: 700; text-align: center;
  }
  .player .steam-id {
    color: var(--text-3); font-family: 'JetBrains Mono', monospace;
    font-size: 11px; margin-left: auto;
  }

  .section {
    max-width: 1100px; margin: 0 auto 24px;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 18px 24px;
  }
  .section-title {
    font-size: 11px; font-weight: 600; color: var(--text-3);
    letter-spacing: 0.14em; text-transform: uppercase;
    margin-bottom: 12px;
  }
  .seq-list { display: flex; flex-wrap: wrap; gap: 8px; }
  .seq-step {
    font-family: 'JetBrains Mono', monospace;
    font-size: 14px; font-weight: 500;
    padding: 8px 12px; border-radius: 6px;
    background: var(--panel-2);
    color: var(--text-3);
    display: inline-flex; align-items: center; gap: 6px;
    transition: transform 0.2s ease;
  }
  .seq-step.ban  { background: var(--ban-bg);  color: var(--ban-fg); }
  .seq-step.pick { background: var(--pick-bg); color: var(--pick-fg); }
  .seq-step.current {
    transform: scale(1.06);
    box-shadow: 0 0 0 2px var(--accent);
  }
  .seq-step .step-n {
    font-size: 11px; color: inherit; opacity: 0.7;
    margin-right: 2px;
  }

  .decider {
    max-width: 1100px; margin: 0 auto 24px;
    text-align: center; padding: 28px;
    background: var(--decider-bg);
    border-radius: 12px;
    color: white;
    animation: decider-in 0.6s ease-out;
  }
  @keyframes decider-in {
    from { transform: scale(0.92); opacity: 0; }
    to   { transform: scale(1);    opacity: 1; }
  }
  .decider .label {
    font-size: 11px; font-weight: 700; letter-spacing: 0.2em;
    text-transform: uppercase; opacity: 0.85; margin-bottom: 6px;
  }
  .decider .map-name {
    font-size: 44px; font-weight: 700; letter-spacing: -0.01em;
    font-family: 'JetBrains Mono', monospace;
  }

  .err {
    max-width: 1100px; margin: 0 auto;
    color: var(--bad); padding: 18px 22px;
    border: 1px solid var(--bad); border-radius: 8px;
    font-size: 14px; text-align: center;
  }
  .foot {
    color: var(--text-3); font-size: 10px;
    letter-spacing: 0.16em; text-transform: uppercase;
    margin-top: 24px; text-align: center; opacity: 0.6;
  }
  body[data-bg="transparent"] .foot,
  body[data-bg="green"] .foot,
  body[data-bg="blue"] .foot { display: none; }

  @media (max-width: 720px) {
    body { padding: 16px; }
    .scoreline { font-size: 32px; }
    .teams { grid-template-columns: 1fr; }
    .decider .map-name { font-size: 32px; }
  }
</style>
</head><body>
<div class="scoreline" id="scoreline">
  <span class="team-name" id="team-a-name">—</span>
  <span class="vs">VS</span>
  <span class="team-name" id="team-b-name">—</span>
</div>
<div class="submeta" id="submeta">
  <span class="mode-pill" id="mode-pill">—</span>
  <span class="state-pill" id="state-pill">loading…</span>
</div>
<div id="decider-holder"></div>
<div class="teams" id="teams"></div>
<div id="sequence-holder"></div>
<div id="final-holder"></div>
<div id="err-holder"></div>
<div class="foot" id="foot">Read-only spectator view · live updates via SSE</div>
<script>
const TOKEN = "__TOKEN__";
// v0.16.14 — apply OBS query params before anything renders.
(function applyQueryParams() {
  const qs = new URLSearchParams(location.search);
  ["bg", "theme", "compact"].forEach(k => {
    const v = qs.get(k);
    if (v) document.body.setAttribute("data-" + k, v);
  });
})();
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function renderErr(msg) {
  document.getElementById("err-holder").innerHTML = '<div class="err">' + esc(msg) + '</div>';
  document.getElementById("state-pill").textContent = "error";
  document.getElementById("state-pill").classList.remove("active");
}
function render(s) {
  document.getElementById("err-holder").innerHTML = "";
  document.getElementById("team-a-name").textContent = s.team_a_name || "Team A";
  document.getElementById("team-b-name").textContent = s.team_b_name || "Team B";
  document.getElementById("mode-pill").textContent = s.mode || "—";
  const statePill = document.getElementById("state-pill");
  statePill.textContent = s.state || "—";
  // Pulse the "● live" dot when a veto is actively in progress.
  const live = s.state && !["idle","finalized","completed"].includes(s.state);
  statePill.classList.toggle("active", !!live);

  // Determine which team is "on the clock" — the team whose step is next.
  const cur = (s.current_step != null && s.sequence && s.current_step < s.sequence.length)
              ? s.sequence[s.current_step] : null;
  const activeTeam = (cur && cur.team) || null;

  // Teams panel.
  const teamCard = (label, name, capName, team, isActive) => {
    const players = (team || []).map(p => {
      const isCap = (p.name === capName);
      return '<li class="player ' + (isCap ? "cap" : "") + '">' +
        (isCap ? '<span class="cap-badge">★</span>' : '<span class="cap-badge"></span>') +
        '<span>' + esc(p.name) + '</span>' +
        (p.steam_id ? '<span class="steam-id">' + esc(p.steam_id) + '</span>' : '') +
        '</li>';
    }).join("");
    return '<div class="team-card ' + (isActive ? "active" : "") + '">' +
      '<div class="team-hdr">' +
        '<span class="team-hdr-name">' + esc(name) + '</span>' +
        (capName ? '<span class="team-hdr-cap">captain <strong>' + esc(capName) + '</strong></span>' : '') +
      '</div>' +
      '<ul class="roster">' + (players || '<li class="player">(no roster)</li>') + '</ul>' +
    '</div>';
  };
  document.getElementById("teams").innerHTML =
    teamCard("A", s.team_a_name, s.captain_a, s.team_a, activeTeam === "A") +
    teamCard("B", s.team_b_name, s.captain_b, s.team_b, activeTeam === "B");

  // Veto sequence.
  if (s.sequence && s.sequence.length) {
    const seq = s.sequence.map((st, i) => {
      const cls = (st.kind === "ban" ? "ban" : "pick") +
                  (i === s.current_step ? " current" : "");
      const mapLabel = st.map ? (' · ' + esc(st.map)) : (i === s.current_step ? ' · ?' : '');
      return '<span class="seq-step ' + cls + '">' +
        '<span class="step-n">' + (i + 1) + '</span>' +
        esc(st.team) + ' ' + esc(st.kind) + mapLabel +
      '</span>';
    }).join("");
    document.getElementById("sequence-holder").innerHTML =
      '<div class="section">' +
        '<div class="section-title">Veto sequence</div>' +
        '<div class="seq-list">' + seq + '</div>' +
      '</div>';
  } else {
    document.getElementById("sequence-holder").innerHTML = "";
  }

  // Final map list (post-veto) — keep visible even after finalize.
  if (s.final_maps && s.final_maps.length) {
    const fin = s.final_maps.map((m, i) => {
      const isDec = (m === s.decider);
      return '<span class="seq-step ' + (isDec ? "pick current" : "pick") + '">' +
        '<span class="step-n">' + (i + 1) + '</span>' +
        (isDec ? "🏁 " : "") + esc(m) +
      '</span>';
    }).join("");
    document.getElementById("final-holder").innerHTML =
      '<div class="section">' +
        '<div class="section-title">Final maplist</div>' +
        '<div class="seq-list">' + fin + '</div>' +
      '</div>';
  } else {
    document.getElementById("final-holder").innerHTML = "";
  }

  // Decider hero (only for BO1 finalize) — big celebratory banner.
  if (s.decider && (s.mode === "BO1" || s.state === "finalized")) {
    document.getElementById("decider-holder").innerHTML =
      '<div class="decider">' +
        '<div class="label">Decider</div>' +
        '<div class="map-name">' + esc(s.decider) + '</div>' +
      '</div>';
  } else {
    document.getElementById("decider-holder").innerHTML = "";
  }
}

// v0.16.14 — prefer SSE for instant updates; fall back to 5s polling
// if the EventSource fails (corporate proxy stripping SSE, etc.).
let pollTimer = null;
async function pollOnce() {
  try {
    const r = await fetch("/api/veto/spectator/state?token=" + encodeURIComponent(TOKEN),
                          {cache:"no-store"});
    if (r.status === 401) { renderErr("Spectator link is invalid or has been rotated."); return; }
    if (r.status === 404) { renderErr("No active veto session yet."); return; }
    if (!r.ok) { renderErr("HTTP " + r.status); return; }
    render(await r.json());
  } catch (e) { renderErr("Network: " + e.message); }
}
function startPolling() {
  if (pollTimer) return;
  pollOnce();
  pollTimer = setInterval(pollOnce, 5000);
}
function startSSE() {
  let es;
  try {
    es = new EventSource("/api/veto/spectator/stream?token=" + encodeURIComponent(TOKEN));
  } catch (e) { startPolling(); return; }
  es.onmessage = (ev) => {
    try { render(JSON.parse(ev.data)); }
    catch (e) { /* ignore malformed frame */ }
  };
  es.onerror = () => {
    // EventSource auto-reconnects on transient drops; only fall through
    // to polling if it can't establish a connection at all.
    if (es.readyState === EventSource.CLOSED) {
      if (pollTimer == null) startPolling();
    }
  };
}
// Kick off SSE.  pollOnce primes the page in case SSE is slow to connect.
pollOnce();
if ("EventSource" in window) startSSE(); else startPolling();
// Refresh immediately when the OBS browser source / phone gets focus.
document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
</script>
</body></html>
"""


# ── Flask app factory ──────────────────────────────────────────────────────────

def create_flask(core: AppCore) -> Flask:
    app = Flask(__name__)   # static_folder=<pkg>/static, template_folder=<pkg>/templates

    # v0.12.4 (audit finding #6 / task #139) — content-hashed static URLs.
    # The template injects `?v={{ app_version }}` into every /static/* URL
    # it emits, so each release ships a new URL and the browser treats
    # the asset as a fresh resource.  Combined with `immutable`, this is
    # the standard "cache-busting via URL change" pattern — gives both
    # cache-bust on rebuild AND aggressive caching between rebuilds.
    #
    # Replaces v0.11.24's blanket Cache-Control: no-store, which was
    # correct but a perf regression (every page load re-downloaded the
    # full ~600KB app.js even when the .exe hadn't changed).
    #
    # The `v` query param doesn't need to be parsed by Flask — it's
    # purely a URL-uniqueness key for the browser cache.  Requests with
    # NO version param (an old cached HTML pointing at /static/js/app.js)
    # still serve the current asset, but get no cache headers.  That's
    # safe: only the embedded SPA template emits versioned URLs, and the
    # template is re-rendered every page load.
    @app.after_request
    def _static_cache_headers(resp):
        try:
            if request.path.startswith("/static/") and request.args.get("v"):
                # Versioned URL → cache aggressively.  1-year max-age +
                # immutable signals "this URL's content will never change."
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                # Drop the no-cache markers that would have been inherited
                # from a previous request via response template defaults.
                resp.headers.pop("Pragma", None)
                resp.headers.pop("Expires", None)
        except Exception:
            pass
        return resp

    # ── Discord bot status helper (v0.11.0) ───────────────────────────────────
    # Wraps discord_bot.bot_status() so a missing discord.py doesn't crash
    # /api/state.  The bot module itself has a DISCORD_AVAILABLE flag for
    # graceful degradation.
    def _discord_bot_status() -> dict:
        try:
            from . import discord_bot
            return discord_bot.bot_status()
        except Exception:
            return {"configured": False, "connected": False, "user": None}

    # ── Auth helpers ───────────────────────────────────────────────────────────

    def _current_session() -> dict | None:
        token = request.cookies.get("session")
        return _get_session(token) if token else None

    def require_auth(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            session = _current_session()
            if not session:
                return jsonify({"error": "unauthorized"}), 401
            # Bind remote sessions to their origin IP: a stolen cookie replayed
            # from a different address is rejected (the local pywebview session
            # is exempt — it's always loopback).
            if (not session.get("is_local")
                    and session.get("ip") != (request.remote_addr or "")):
                token = request.cookies.get("session")
                if token:
                    _clear_session(token)
                return jsonify({"error": "unauthorized"}), 401
            request.session = session       # type: ignore[attr-defined]
            return f(*args, **kwargs)
        return wrapper

    def require_local(f: Callable) -> Callable:
        """Must be stacked on top of require_auth."""
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            session = getattr(request, "session", None)
            if not session or not session.get("is_local"):
                return jsonify({"error": "local access only"}), 403
            return f(*args, **kwargs)
        return wrapper

    # ── Role gate (fail-closed) ──────────────────────────────────────────────────
    # The "guest" role may only touch an explicit allowlist (status, map/mode
    # change, workshop browse/download). EVERYTHING else under /api is admin-only.
    # New routes are admin-only by default unless added to the allowlist here.
    # require_local routes layer on top (admin includes local; remote admin still
    # 403s on those), so install/RCON/Steam stay strictly local.
    _GUEST_PATHS = frozenset({
        "/api/state",
        "/api/capabilities",      # v0.10.2: every session reads its own caps
        "/api/server/map",        # change map + game mode
        "/api/players",           # read-only roster for the status view
        "/api/workshop/maps",     # browse downloaded maps
        "/api/workshop/download", # download a new workshop map
        "/api/workshop/cancel",   # cancel a download they started
        "/api/request_workshop",
        "/api/setup/status",
    })
    # Captain role (v0.10.0): only the veto live-mirror + step endpoints.
    # Created by /api/veto/claim with a valid single-use token (no PIN).
    _CAPTAIN_PATHS = frozenset({
        "/api/state",             # captains see basic server status too
        "/api/capabilities",      # v0.10.2: every session reads its own caps
        "/api/veto/state",
        "/api/veto/stream",
        "/api/veto/step",
        "/api/veto/ready",        # v0.10.1: captains toggle their ready flag
    })
    # v0.12.3 / task #135 — Voter role: a rostered player who claimed
    # their voter token.  Strictly tighter than captain — can only see
    # the live mirror + cast their own vote.  Created by /api/veto/voter_claim
    # with a valid single-use voter token (no PIN).
    _VOTER_PATHS = frozenset({
        "/api/state",
        "/api/capabilities",
        "/api/veto/state",
        "/api/veto/stream",
        "/api/veto/vote",         # voters only act on this; per-team/idx
                                  # enforcement lives inside the handler
    })
    _PUBLIC_PATHS = frozenset({
        "/api/ping", "/api/auth/login", "/api/auth/logout",
        "/api/veto/claim",        # token IS the credential; PIN-free entry
        "/api/veto/voter_claim",  # v0.12.3 / task #135 — same pattern for voters
    })

    @app.before_request
    def _role_gate():
        p = request.path
        if not p.startswith("/api/"):
            return None                      # SPA shell, static, /auth/auto, /veto
        if p in _PUBLIC_PATHS:
            return None
        if p.startswith("/api/data/") or p.startswith("/api/maps/thumb/"):
            return None                      # static reference data + thumbnails
        session = _current_session()
        if not session:
            return None                      # let require_auth issue 401
        if session.get("is_local") or session.get("role") == "admin":
            return None                      # admins / local pass everything
        role = session.get("role")
        if role == "guest" and p in _GUEST_PATHS:
            return None
        if role == "captain" and p in _CAPTAIN_PATHS:
            return None
        if role == "voter" and p in _VOTER_PATHS:        # v0.12.3 / task #135
            return None
        return jsonify({"error": f"{role or 'unknown'} role cannot access {p}"}), 403

    # ── SPA shell ──────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        session = _current_session()
        return render_template(
            "index.html",
            authed=bool(session),
            pin_len=len(core.admin_pin),
            app_version=_config.APP_VERSION,
        )

    @app.route("/auth/auto")
    def auto_auth():
        """One-time auto-login for the local pywebview window."""
        # Only the loopback pywebview window may use the startup token; a remote
        # caller who learns the token (e.g. from a leaked URL) must not be able
        # to mint a privileged local session.
        if (request.remote_addr or "") not in ("127.0.0.1", "::1"):
            return redirect("/")
        token = request.args.get("token", "")
        # Atomic compare-and-clear: two simultaneous loopback hits could both
        # pass compare_digest before either ran the clear, minting two local
        # sessions instead of one.  The lock collapses that race.
        with _startup_token_lock:
            ok = bool(token and core.startup_token
                      and secrets.compare_digest(token, core.startup_token))
            if ok:
                core.startup_token = ""    # invalidate immediately — single-use
        if ok:
            session_token = _create_session("127.0.0.1", is_local=True, role="admin")
            core.log("Local window authenticated (auto-auth)")
            resp = redirect("/")
            resp.set_cookie(
                "session", session_token,
                httponly=True, samesite="Lax",
                secure=_request_is_https(),     # v0.11.17 A3
            )
            return resp
        return redirect("/")

    # ── Auth API ───────────────────────────────────────────────────────────────

    @app.route("/api/auth/login", methods=["POST"])
    def auth_login():
        ip   = request.remote_addr or "unknown"
        wait = _check_lockout(ip) or _check_global_lockout()
        if wait:
            return jsonify({"ok": False, "error": "Too many attempts",
                            "locked_for": wait}), 429
        pin = str((request.get_json() or {}).get("pin", ""))
        # Admin PIN wins (checked first); a separate guest PIN, if set, grants the
        # limited role (maps/modes/workshop downloads only).
        role = None
        if secrets.compare_digest(pin, str(core.admin_pin)):
            role = "admin"
        elif core.guest_pin and secrets.compare_digest(pin, str(core.guest_pin)):
            role = "guest"
        if role:
            _clear_attempts(ip)
            _clear_global()
            core.log(f"Web login from {ip} (role={role})")
            session_token = _create_session(ip, is_local=False, role=role)
            resp = jsonify({"ok": True, "role": role})
            resp.set_cookie(
                "session", session_token,
                httponly=True, samesite="Lax",
                secure=_request_is_https(),     # v0.11.17 A3
            )
            return resp
        _record_fail(ip)
        _record_global_fail()
        remaining = max(0, _MAX_ATTEMPTS - _attempts.get(ip, {}).get("count", 0))
        core.log(f"Failed web login from {ip} ({remaining} attempt(s) left)")
        out  = {"ok": False, "error": "Wrong PIN"}
        wait = _check_lockout(ip)
        if wait:
            out["locked_for"] = wait
        return jsonify(out), 401

    @app.route("/api/auth/logout", methods=["POST"])
    def auth_logout():
        token = request.cookies.get("session")
        if token:
            _clear_session(token)
        resp = jsonify({"ok": True})
        resp.delete_cookie("session")
        return resp

    # ── Health check (no auth) ─────────────────────────────────────────────────

    @app.route("/api/ping")
    def ping():
        """Unauthenticated health check.  v0.11.5: now also exposes the
        running app version so an external observer can detect a stale
        deployment without needing to log in or run the diagnostic
        snapshot (which is local-only).  Version is not sensitive — it
        appears in CHANGELOG, GitHub releases, and the SPA header.
        Build flag indicates frozen .exe vs dev mode so a hot-reload
        situation is also visible at a glance."""
        return jsonify({
            "ok":      True,
            "version": _config.APP_VERSION,
            "build":   "frozen" if getattr(sys, "frozen", False) else "dev",
        })

    # ── Server state ───────────────────────────────────────────────────────────

    @app.route("/api/state")
    @require_auth
    def api_state():
        session  = _current_session()
        is_local = bool(session and session.get("is_local"))
        role     = "admin" if is_local else ((session or {}).get("role") or "guest")
        # v0.10.1: surface the captain's team letter for captain-role sessions
        # so the SPA's captain-finale view knows which team's ready flag it's
        # toggling.  Empty string for non-captain sessions.
        captain_team = (session or {}).get("captain_team", "") if role == "captain" else ""
        # v0.12.3 / task #135 — voter session carries team + voter_idx so the
        # SPA can render the right team's 5 names + lock to the right slot.
        voter_team    = (session or {}).get("voter_team",    "") if role == "voter" else ""
        voter_idx     = (session or {}).get("voter_idx",     -1) if role == "voter" else -1
        return jsonify({
            "running":            core.running,
            "role":               role,
            "captain_team":       captain_team,
            "voter_team":         voter_team,
            "voter_idx":          voter_idx,
            "guest_pin_set":      bool(core.guest_pin),
            "is_installed":       core.is_installed,
            "boot_state":         core.boot_state,
            "player_count":       core.player_count,
            "map":                core.current_map,
            "mode":               core.current_mode,
            "uptime":             core.uptime_seconds,
            "ff_enabled":         core._ff_enabled,
            "update_available":   core.update_available,
            "app_update":         core.app_update_available,
            "app_version":        core.app_latest_version,
            "public_ip":          core.public_ip,
            # `lan_ip` is the live primary LAN IP for the Connect popover
            # ("share this IP with friends to join").  Refreshed every call so
            # a network change is reflected without restarting the app.
            # flask_port reads from _config so a port-collision fallback at
            # startup is mirrored back to the UI.
            "lan_ip":             _config._lan_ip(),
            "rcon_port":          RCON_PORT,
            "flask_port":         _config.FLASK_PORT,
            "is_local":           is_local,
            # Boolean instead of the raw value so a guest can see "password
            # protected? yes/no" for the Connect popover without leaking the
            # password itself.  The ConnectPopover UI reads this.
            "sv_password_set":    bool(core.sv_password),
            # v1.0.1 — boolean so the SPA can warn when the operator is about
            # to host publicly without a GSLT.  Valve's auth backend silently
            # rejects external client handshakes when +sv_setsteamaccount is
            # missing — LAN connects still work, but remote players fail with
            # no error logged on either side.  Burned a full evening of triage
            # before this gap was caught — clear UX matters here.
            "gslt_set":           bool(core.gslt_token),
            # v1.2: Fun Mode auto-suppresses GSLT (custom models risk a GSLT
            # ban).  The SPA uses this to render the "GSLT off, LAN only" banner
            # and to explain why the GSLT badge shows suppressed in this mode.
            "gslt_suppressed":    core.current_mode in _config.GSLT_SUPPRESSED_MODES,
            "fun_mode":           core.current_mode == "Fun",
            "dl_active":          core._active_dl_proc is not None,
            "dl_progress":        core._dl_progress or None,
            # v0.10.2: last "why did Start fail" string from the most recent
            # failed preflight; empty when nothing relevant has happened or
            # after the next successful start / stop.  SPA reads this and
            # renders a banner so remote admins see why their Start did
            # nothing instead of staring at a frozen "Offline" pill.
            "boot_error":         core.last_start_error or "",
            # v0.11.0 — Discord bot status (best-effort; safe no-op when
            # discord.py isn't installed or no token configured)
            "discord_bot":        _discord_bot_status(),
        })

    @app.route("/api/capabilities")
    @require_auth
    def api_capabilities():
        """v0.10.2 — Tell the SPA exactly what the current session can do.

        The audit's "local-only restrictions surfaced as 'X failed: local
        access only'" pattern came from the SPA having to guess which
        buttons to disable based on `is_local` + `role`.  Now there's a
        single endpoint that returns the canonical list, and the SPA
        renders local-only / admin-only controls as `disabled` + tooltip
        instead of clickable-but-403-on-click.

        Schema:
          {
            "role":     "admin" | "guest" | "captain",
            "is_local": bool,
            "can": [<allowed-capability-tags>...],
          }

        Capability tags (in lieu of brittle url-path matching):
          server.start         server.stop           server.map
          server.broadcast     server.ff             server.round
          server.match
          players.kick         players.ban           players.unban
          workshop.download    workshop.cancel
          workshop.update      workshop.scan         workshop.override
          config.read          config.write          config.write_secrets
          rcon                 steam.login           server.install
          server.update_cs2    log.read              log.save
          veto.admin           veto.captain
        """
        session  = _current_session()
        is_local = bool(session and session.get("is_local"))
        role     = "admin" if is_local else ((session or {}).get("role") or "guest")

        # Build the capability set per role.  Local sessions get the
        # superset (everything admin can do PLUS local-only secret/install
        # operations).  We explicitly enumerate rather than negate so a
        # future-added endpoint isn't accidentally exposed.
        cap: set[str] = set()

        if role in ("admin", "captain"):
            cap.add("log.read")            # admin + captain can stream the log

        if role == "admin":
            cap.update({
                "server.start", "server.stop", "server.map",
                "server.broadcast", "server.ff", "server.round", "server.match",
                "players.kick", "players.ban", "players.unban",
                "workshop.download", "workshop.cancel",
                "config.read", "config.write",
                "veto.admin",
            })
        elif role == "guest":
            cap.update({
                "server.map",
                "workshop.download", "workshop.cancel",
            })
        elif role == "captain":
            cap.add("veto.captain")

        # Local-only superset: install / Steam creds / RCON / log save /
        # workshop update + scan + override + directory picker.  These map
        # 1:1 to the @require_local routes in this file — if you add a new
        # @require_local route, add the matching cap tag here.
        if is_local:
            cap.update({
                "config.write_secrets",
                "rcon",
                "steam.login",
                "server.install", "server.update_cs2",
                "workshop.update", "workshop.scan", "workshop.override",
                "log.save",
                "system.pick_directory",
                # v0.14.0 — Plugin Manager (read + write).  Read is local-only
                # because it exposes csgo_dir (filesystem path).  Write covers
                # activate / vanilla / apply_pack — same lifecycle_lock.
                "plugins.read", "plugins.write",
            })

        return jsonify({
            "role":     role,
            "is_local": is_local,
            "can":      sorted(cap),
        })

    # ── Plugin Manager (v0.13.2 — task #92) ────────────────────────────────────
    # Read-only inventory endpoint backing the Plugins tab.  Returns:
    #   - runtime: MetaMod patched? CSS host present? csgo/ resolved?
    #   - manifest: what was last deployed + when
    #   - bundled: every plugin we ship inside the .exe, with display
    #     metadata and which modes use it.  The "registry plugins" key
    #     is intentionally absent in this slice — task #90 introduces
    #     OblivionPluginRegistry and the merge happens then.

    @app.route("/api/plugins")
    @require_auth
    @require_local
    def api_plugins():
        # v0.14.0 audit fix #1: gated to local sessions.  Returns the
        # absolute csgo_dir path, which would otherwise leak the host
        # operator's Windows filesystem layout (and username on standard
        # installs) to remote captain/voter sessions.
        from .core import _MODE_PLUGIN_NAMES, _PLUGIN_CATALOG, _PLUGINS_BASE

        csgo_dir = core._csgo_dir() if core.server_dir else None
        csgo_exists = bool(csgo_dir and os.path.isdir(csgo_dir))

        # v0.14.0 slice 5: use the proper install detectors instead of mere
        # directory existence — addons/counterstrikesharp/ can exist while
        # being completely empty after a failed install or aggressive cleanup.
        try:
            metamod_installed = core._metamod_installed() if csgo_exists else False
        except Exception:
            metamod_installed = False
        try:
            css_installed = core._css_installed() if csgo_exists else False
        except Exception:
            css_installed = False

        # css_present kept for SPA backward-compat (slice 1 used the looser
        # "directory exists" semantic).  Maps onto css_installed now so older
        # SPA cache reads don't flip-flop the UI.
        css_present = css_installed

        try:
            metamod_patched = core._gameinfo_has_metamod()
        except Exception:
            metamod_patched = None

        manifest = core._load_plugin_manifest() or {}
        deployed_slugs = set(manifest.get("plugins", []) or [])

        # Reverse-map slug → modes that use it, so each card can say
        # "Used by: Warcraft" / "Used by: 3v3, 4v4, 5v5, Practice".
        modes_for_slug: dict[str, list[str]] = {}
        for mode, slugs in _MODE_PLUGIN_NAMES.items():
            for slug in slugs:
                modes_for_slug.setdefault(slug, []).append(mode)

        # v0.15.0 slice 1 — use the discovered manifests so the SPA sees
        # local-installed plugins, and so the "source" badge reflects the
        # real folder (bundled vs %APPDATA%/.../plugins/).  Falls back to
        # the catalog entry for slugs that aren't in _DISCOVERED_PLUGINS
        # (defensive: shouldn't happen for bundled plugins now they all
        # have manifests, but keeps the endpoint working if one is removed).
        from .core import _DISCOVERED_PLUGINS
        # v0.15.2 slice 3 — also reach into the registry so we can mark
        # "update available" when a local plugin has an older version
        # than what the registry advertises.  Cached fetch is cheap
        # (24h TTL) and gracefully empty if the registry is offline.
        try:
            from . import registry_client as _reg
            reg_catalog = _reg.fetch_catalog(force=False) or {}
            reg_index = {
                p["slug"]: ((p.get("versions") or [{}])[0].get("version") or "")
                for p in (reg_catalog.get("plugins") or [])
                if p.get("slug")
            }
        except Exception:
            reg_index = {}
            _reg = None  # type: ignore[assignment]
        bundled: list[dict] = []
        all_slugs = sorted(set(_DISCOVERED_PLUGINS.keys())
                           | set(_PLUGIN_CATALOG.keys())
                           | {s for ss in _MODE_PLUGIN_NAMES.values() for s in ss})
        for slug in all_slugs:
            manifest = _DISCOVERED_PLUGINS.get(slug) or {}
            meta = _PLUGIN_CATALOG.get(slug, {})
            # Prefer manifest's display fields (v0.15.0); catalog.json
            # entries are now an artifact for the future remote registry.
            display_name = manifest.get("display_name") or meta.get("display_name") or slug
            summary      = manifest.get("summary")      or meta.get("summary") or ""
            author       = manifest.get("author")       or meta.get("author") or ""
            source       = manifest.get("_source")      or "bundled"
            plugin_dir   = manifest.get("_plugin_dir")  or os.path.join(_PLUGINS_BASE, slug)
            installed_ver = manifest.get("version") or ""
            latest_ver    = reg_index.get(slug, "")
            update = False
            if _reg is not None and latest_ver:
                update = _reg.has_update(installed_ver, latest_ver)
            bundled.append({
                "slug":              slug,
                "display_name":      display_name,
                "summary":           summary,
                "author":            author,
                "source":            source,    # "bundled" | "local"
                "source_present":    os.path.isdir(plugin_dir),
                "modes":             sorted(modes_for_slug.get(slug, [])),
                "deployed":          slug in deployed_slugs,
                "installed_version": installed_ver,
                "latest_version":    latest_ver,
                "update_available":  update,
            })

        return jsonify({
            "runtime": {
                "csgo_dir":          csgo_dir or "",
                "csgo_dir_exists":   csgo_exists,
                "css_present":       css_present,
                "css_installed":     css_installed,
                "metamod_installed": metamod_installed,
                # None means gameinfo.gi couldn't be read (server not installed
                # yet, or file missing) — distinct from "definitely off".
                "metamod_patched":   metamod_patched,
            },
            "current_mode": core.current_mode or "",
            "manifest": {
                "mode":        manifest.get("mode", "") or "",
                "plugins":     list(manifest.get("plugins", []) or []),
                "deployed_at": manifest.get("deployed_at", "") or "",
            },
            "bundled": bundled,
        })

    # Preflight helper shared by activate + vanilla + apply_pack.  Returns
    # (ok, http_status, error_dict_or_None).
    #
    # v0.14.1 — running-server is NO LONGER a hard 409.  The maps/mode picker
    # has supported live mode swaps (via _restart_into) since v0.10.x and the
    # plugin tab was being artificially restrictive.  Callers branch on
    # core.running themselves to pick the right path:
    #   - running → change_map (async; stops server, redeploys, restarts)
    #   - offline → set_offline_mode_and_deploy (sync)
    # Refusals that remain are state where the operation simply can't
    # complete safely:
    #   - workshop download in flight (modifying csgo/ races SteamCMD)
    #   - veto mid-flow (operator wants their roster intact through the swap)
    #   - csgo/ missing (nowhere to deploy into)
    def _plugin_action_preflight() -> tuple[bool, int, dict | None]:
        if getattr(core, "_active_dl_proc", None) is not None:
            return False, 409, {"error": "A workshop download is in progress. Wait for it to finish."}
        sess = getattr(core, "_veto_session", None)
        if sess is not None and getattr(sess, "state", "idle") not in ("idle", "complete"):
            return False, 409, {"error": f"A veto session is active (state={sess.state}). Reset or complete it first."}
        # csgo/ existence is what actually matters for deploy_plugins() —
        # don't gate on server_dir separately since drivers compute csgo/
        # differently per game (each GameDriver.install_root impl).
        if not os.path.isdir(core._csgo_dir() or ""):
            return False, 503, {"error": "CS2 install not found. Set the server directory in Config first."}
        return True, 200, None

    # v0.14.1 helper — pick a safe map for a live mode swap.
    # change_map() requires a map_name.  When operator hasn't supplied one
    # (activate has no map arg; vanilla just changes mode), we prefer the
    # current map if it's valid for the new mode, else fall through to the
    # mode's first map in MODE_MAPS.  Workshop maps (digit IDs) carry their
    # is_workshop flag so the restart launches with -disable_workshop_command_filtering
    # when needed.
    def _resolve_live_swap_map(new_mode: str, preferred: str = "") -> tuple[str, bool]:
        from .config import MODE_MAPS, OFFICIAL_MAPS
        cur = (preferred or core.current_map or "").strip()
        pool = MODE_MAPS.get(new_mode)
        is_workshop = bool(cur and _DIGITS_RE.match(cur))
        # Workshop maps work on most modes — keep the operator's pick if set.
        if is_workshop and cur:
            return cur, True
        if cur and (pool is None or cur in pool):
            return cur, False
        if pool:
            return pool[0], False
        return (OFFICIAL_MAPS[0] if OFFICIAL_MAPS else "de_dust2"), False

    @app.route("/api/plugins/activate", methods=["POST"])
    @require_auth
    @require_local
    def api_plugins_activate():
        """Stage a mode and deploy its plugins for the next server start.

        Body: ``{"slug": "<plugin>", "mode": "<optional explicit mode>"}``.
        If ``mode`` is omitted, the catalog's reverse map picks the single
        mode that uses this slug; for multi-mode slugs (MatchZy → Practice/
        3v3/4v4/5v5; arenas → 1v1/2v2) the caller MUST supply ``mode`` so
        the operator's intent is explicit, not guessed.
        """
        from .core import _MODE_PLUGIN_NAMES, _PLUGIN_CATALOG

        d    = request.get_json(silent=True) or {}
        slug = (d.get("slug") or "").strip()
        mode = (d.get("mode") or "").strip()

        if not slug:
            return jsonify({"error": "slug is required"}), 400

        # Discover which modes use this slug.
        modes_using = sorted(m for m, ss in _MODE_PLUGIN_NAMES.items() if slug in ss)
        if not modes_using:
            return jsonify({
                "error": f"Plugin {slug!r} is not bound to any mode. "
                         "No-op modes (Competitive, Casual, etc.) cannot host plugins.",
            }), 400

        if mode:
            if mode not in modes_using:
                return jsonify({
                    "error": f"Plugin {slug!r} is not used by mode {mode!r}. "
                             f"Valid modes for this plugin: {', '.join(modes_using)}",
                }), 400
        else:
            if len(modes_using) > 1:
                return jsonify({
                    "error": f"Plugin {slug!r} is used by multiple modes "
                             f"({', '.join(modes_using)}). "
                             "Pass 'mode' to disambiguate.",
                    "modes": modes_using,
                }), 400
            mode = modes_using[0]

        ok, status, err = _plugin_action_preflight()
        if not ok:
            return jsonify(err), status

        # v0.14.1: branch on running.  If online → reuse change_map's proven
        # stop-deploy-restart cycle.  If offline → stage and let next start
        # pick it up.
        if core.running:
            target_map, is_workshop = _resolve_live_swap_map(mode)
            core.change_map(target_map, mode, is_workshop=is_workshop,
                            caller="plugin-tab/activate")
            return jsonify({
                "ok":          True,
                "slug":        slug,
                "mode":        mode,
                "restarting":  True,
                "target_map":  target_map,
            }), 202

        result = core.set_offline_mode_and_deploy(mode, caller="plugin-tab/activate")
        if not result.get("ok"):
            return jsonify({"error": result.get("error") or "deploy failed"}), 500
        return jsonify({
            "ok":         True,
            "slug":       slug,
            "mode":       result["mode"],
            "plugins":    result["plugins"],
            "restarting": False,
        })

    # v0.15.1 slice 2 — Community plugin registry.
    # Read: returns the cached catalog + freshness metadata.
    # Refresh: forces a re-fetch (POST so it's idempotent-ish but cache-busting).
    # Install: downloads + sha256-verifies + extracts a registry plugin into
    #          %APPDATA%/.../plugins/<slug>/ and re-runs discovery.
    @app.route("/api/plugins/registry")
    @require_auth
    @require_local
    def api_plugins_registry():
        from . import registry_client
        catalog = registry_client.fetch_catalog(force=False)
        status  = registry_client.get_registry_status()
        # SPA wants to know which registry plugins are already installed
        # locally so it can hide them (or show "Update available" in a
        # later slice).  We just include the installed-slug set here and
        # let the SPA do the filtering.
        from .core import _DISCOVERED_PLUGINS
        installed_slugs = sorted(_DISCOVERED_PLUGINS.keys())
        return jsonify({
            "catalog":         catalog,
            "status":          status,
            "installed_slugs": installed_slugs,
        })

    @app.route("/api/plugins/registry/refresh", methods=["POST"])
    @require_auth
    @require_local
    def api_plugins_registry_refresh():
        from . import registry_client
        catalog = registry_client.fetch_catalog(force=True)
        status  = registry_client.get_registry_status()
        return jsonify({"catalog": catalog, "status": status})

    # v0.15.2 slice 3 — re-discover bundled + local plugins WITHOUT an app
    # restart.  Called after install_from_registry / install_from_url /
    # uninstall, and exposed as a "Reload" button in the SPA so operators
    # can drop a folder into %APPDATA%/.../plugins/ and pick it up live.
    def _reload_plugin_tables() -> dict:
        from . import core as _core
        fresh = _core._discover_plugins()
        (_core._PLUGIN_KIND,
         _core._PLUGIN_VERIFY_FILES,
         _core._PLUGIN_COPY_RULES,
         _core._PLUGIN_CLEANUP_ITEMS,
         _core._MODE_PLUGIN_NAMES) = _core._populate_plugin_tables(fresh)
        _core._DISCOVERED_PLUGINS = fresh
        bundled = sum(1 for m in fresh.values() if m.get("_source") == "bundled")
        local   = sum(1 for m in fresh.values() if m.get("_source") == "local")
        return {"total": len(fresh), "bundled": bundled, "local": local}

    @app.route("/api/plugins/reload", methods=["POST"])
    @require_auth
    @require_local
    def api_plugins_reload():
        stats = _reload_plugin_tables()
        return jsonify({"ok": True, "stats": stats})

    # v0.15.2 slice 3 — Uninstall a LOCAL plugin (bundled plugins live in
    # the .exe and can't be removed from disk).  Refuses if the plugin is
    # bound to the current mode (operator should switch first) — prevents
    # the next /api/server/start from booting into a mode whose plugins
    # were just deleted.
    @app.route("/api/plugins/uninstall", methods=["POST"])
    @require_auth
    @require_local
    def api_plugins_uninstall():
        import shutil
        d    = request.get_json(silent=True) or {}
        slug = (d.get("slug") or "").strip()
        if not slug:
            return jsonify({"error": "slug is required"}), 400

        ok, status, err = _plugin_action_preflight()
        if not ok:
            return jsonify(err), status

        from .core import _DISCOVERED_PLUGINS, _MODE_PLUGIN_NAMES, _resolve_user_plugins_dir
        manifest = _DISCOVERED_PLUGINS.get(slug)
        if not manifest:
            return jsonify({"error": f"plugin {slug!r} not found"}), 404
        if manifest.get("_source") != "local":
            return jsonify({
                "error": f"plugin {slug!r} is bundled inside the .exe and "
                         "cannot be uninstalled.  It will not appear on disk.",
            }), 400

        # If this plugin is bound to the currently-active mode, refuse so
        # we don't strand the operator with a half-functional server.
        cur_mode = (core.current_mode or "").strip()
        if cur_mode and slug in _MODE_PLUGIN_NAMES.get(cur_mode, []):
            return jsonify({
                "error": f"plugin {slug!r} is part of the active mode "
                         f"{cur_mode!r}.  Switch to another mode (Vanilla "
                         f"works) before uninstalling.",
            }), 409

        # The slug-keyed folder lives under user plugins dir.  We
        # deliberately rmtree only that one folder — never anything else
        # under %APPDATA%/.../plugins/.
        target = os.path.join(_resolve_user_plugins_dir(), slug)
        if not os.path.isdir(target):
            return jsonify({
                "error": f"plugin folder {target!r} not on disk; nothing to "
                         "uninstall",
            }), 404

        # v0.16.0 / task #158 — snapshot config before destructive op.
        core.backup_config(reason=f"pre-uninstall-{slug}")
        try:
            shutil.rmtree(target, ignore_errors=False)
        except Exception as exc:
            return jsonify({"error": f"rmtree failed: {exc!r}"}), 500

        stats = _reload_plugin_tables()
        return jsonify({
            "ok":     True,
            "slug":   slug,
            "stats":  stats,
            "removed_path": target,
        })

    # v0.15.2 slice 3 — Custom URL install (advanced).  Operator pastes a
    # zip URL; backend downloads + verifies + extracts.  Same safety as
    # install_from_registry minus the catalog cross-check; the SHA-256 is
    # optional but recommended.  The SPA warns when the operator submits
    # without a hash.
    @app.route("/api/plugins/install_from_url", methods=["POST"])
    @require_auth
    @require_local
    def api_plugins_install_from_url():
        from . import registry_client
        d        = request.get_json(silent=True) or {}
        url      = (d.get("url") or "").strip()
        sha256   = (d.get("sha256") or "").strip() or None
        exp_slug = (d.get("expected_slug") or "").strip() or None
        if not url:
            return jsonify({"error": "url is required"}), 400
        # The registry URL has its own dedicated endpoint — disallow
        # passing it here so operators don't bypass the catalog cross-check.
        if url == _config.OBLIVION_REGISTRY_URL:
            return jsonify({
                "error": "Registry URL must be installed via "
                         "/api/plugins/install_from_registry",
            }), 400

        # v0.16.0 / task #158 — snapshot config before risky operation so
        # operator can roll back via Settings → Tools → Restore backup.
        core.backup_config(reason="pre-url-install")
        try:
            result = registry_client.install_from_url(
                url, expected_sha256=sha256, expected_slug=exp_slug,
            )
        except registry_client.RegistryError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # pragma: no cover — defensive
            return jsonify({"error": f"unexpected: {exc!r}"}), 500

        stats = _reload_plugin_tables()
        return jsonify({"ok": True, "result": result, "stats": stats})

    @app.route("/api/plugins/install_from_registry", methods=["POST"])
    @require_auth
    @require_local
    def api_plugins_install_from_registry():
        from . import registry_client
        d       = request.get_json(silent=True) or {}
        slug    = (d.get("slug") or "").strip()
        version = (d.get("version") or "").strip() or None
        if not slug:
            return jsonify({"error": "slug is required"}), 400
        # v0.16.0 / task #158 — snapshot config before risky operation.
        core.backup_config(reason=f"pre-registry-install-{slug}")
        try:
            result = registry_client.install_plugin(slug, version=version)
        except registry_client.RegistryError as exc:
            # Differentiate "operator-fixable" errors (404, sha mismatch,
            # bad zip) from infrastructure errors.  Both are 400 here —
            # the message tells the operator what to retry.
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # pragma: no cover — defensive
            return jsonify({"error": f"unexpected: {exc!r}"}), 500

        # The plugin is now on disk under %APPDATA%/.../plugins/<slug>/.
        # Re-run discovery so subsequent /api/plugins reflects it without
        # an app restart.  Shared with /api/plugins/reload and uninstall.
        stats = _reload_plugin_tables()
        return jsonify({
            "ok":     True,
            "result": result,
            "stats":  stats,
        })

    # v0.16.5 / task #163 — Auto-install MetaMod or CounterStrikeSharp.
    # Eliminates the most common stuck-point for a fresh operator: "download
    # this zip from sourcemm.net, find the addons/ folder inside, drag it
    # to csgo/, make sure you don't double-nest bin/win64/win64/".  Now
    # one click in the SPA runtime modal → backend downloads → extracts
    # → patches gameinfo.gi → done.  @require_local because runtime install
    # writes into the game server tree.
    @app.route("/api/plugins/install_runtime", methods=["POST"])
    @require_auth
    @require_local
    def api_plugins_install_runtime():
        from . import registry_client
        d         = request.get_json(silent=True) or {}
        component = (d.get("component") or "").strip().lower()
        if component not in ("metamod", "css"):
            return jsonify({"error": "component must be 'metamod' or 'css'"}), 400
        if not core.server_dir:
            return jsonify({"error": "Server directory not configured. "
                                       "Set it in the Setup wizard or Config."}), 400
        csgo = core._csgo_dir()
        if not os.path.isdir(csgo):
            return jsonify({"error": f"csgo/ not found at {csgo}. "
                                       "Install or repair CS2 first."}), 400

        try:
            result = registry_client.install_runtime(component, csgo)
        except registry_client.RegistryError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # pragma: no cover — defensive
            return jsonify({"error": f"unexpected: {exc!r}"}), 500

        # v0.16.8 (review fix #5) — backup only AFTER install succeeds.
        # Previously this fired before the install even on failure, which
        # over time pushed legitimate pre-deploy backups out of the 10-slot
        # ring as failed attempts (bad URL, shaky wifi) accumulated.
        core.backup_config(reason=f"post-runtime-{component}")

        # Post-install fixups:
        # 1. MetaMod's zip historically has a bin/win64/win64/ nesting
        #    bug — fix in place if detected.  Idempotent — no-op if clean.
        # 2. MetaMod needs the search path patched into gameinfo.gi.  CSS
        #    doesn't need this (it's a MetaMod plugin and rides MetaMod's
        #    own search path).
        # v0.16.8 (review fix #1) — method name corrected to `_patch_gameinfo`.
        # The original `_gameinfo_patch_metamod` doesn't exist on AppCore;
        # the AttributeError was swallowed by `except Exception`, and the
        # JSON response still claimed `ok: true` with `metamod_installed:
        # true`.  Friend would have seen the green pill, started the server,
        # and watched MetaMod silently never load.
        gameinfo_patched = False
        warnings = []
        if component == "metamod":
            try:
                core._fix_metamod_dll_nesting()
            except Exception as exc:
                msg = f"DLL nesting fix failed: {exc!r}"
                warnings.append(msg)
                core.log(f"[runtime] {msg}")
            try:
                gameinfo_patched = core._patch_gameinfo()
            except Exception as exc:
                msg = f"gameinfo.gi patch failed: {exc!r}"
                warnings.append(msg)
                core.log(f"[runtime] {msg}")

        # Re-check the live install status so the SPA can render a green
        # "✓ installed" pill immediately.
        gameinfo_now = bool(core._gameinfo_has_metamod())
        runtime_status = {
            "metamod_installed": core._metamod_installed(),
            "css_installed":     core._css_installed(),
            "gameinfo_patched":  gameinfo_now,
        }

        # If we installed MetaMod but gameinfo.gi is STILL not patched, the
        # engine won't load any of it — surface that as a warning so the SPA
        # can show yellow not green and the friend knows to retry.
        ok_for_caller = True
        if component == "metamod" and not gameinfo_now:
            warnings.append("gameinfo.gi is NOT patched — MetaMod will not "
                            "load even though files are in place")
            ok_for_caller = False

        core.log(f"[runtime] ✓ Installed {result['label']} "
                 f"({result['files_written']} files from {result['url'][:60]}…)")
        return jsonify({
            "ok":                     ok_for_caller,
            "result":                 result,
            "runtime_after":          runtime_status,
            "gameinfo_patched_now":   gameinfo_patched,
            "warnings":               warnings,
        })

    @app.route("/api/plugins/packs")
    @require_auth
    def api_plugins_packs():
        """Curated packs strip — one-click recipes (mode + map + plugins).
        Read-only; the apply endpoint actually mutates state."""
        from .core import _PLUGIN_PACKS, _MODE_PLUGIN_NAMES
        out = []
        for p in _PLUGIN_PACKS:
            out.append({
                "id":           p["id"],
                "name":         p["name"],
                "mode":         p["mode"],
                "default_map":  p.get("default_map") or "",
                "summary":      p.get("summary") or "",
                "tags":         list(p.get("tags") or []),
                # Derived — saves the SPA a join against the mode table.
                "plugins":      list(_MODE_PLUGIN_NAMES.get(p["mode"], [])),
            })
        return jsonify({"packs": out})

    @app.route("/api/plugins/apply_pack", methods=["POST"])
    @require_auth
    @require_local
    def api_plugins_apply_pack():
        """Apply a curated pack: switch to its mode, deploy plugins, stage map.

        Single transaction under ``_lifecycle_lock`` (via set_offline_mode_and_deploy)
        — a half-applied pack would be worse than nothing.
        """
        from .core import _PLUGIN_PACKS

        d       = request.get_json(silent=True) or {}
        pack_id = (d.get("pack_id") or "").strip()
        if not pack_id:
            return jsonify({"error": "pack_id is required"}), 400

        pack = next((p for p in _PLUGIN_PACKS if p["id"] == pack_id), None)
        if not pack:
            return jsonify({"error": f"unknown pack {pack_id!r}"}), 400

        ok, status, err = _plugin_action_preflight()
        if not ok:
            return jsonify(err), status

        # v0.14.1: packs that have a default_map use it as the swap target;
        # otherwise we resolve via _resolve_live_swap_map.
        if core.running:
            target_map, is_workshop = _resolve_live_swap_map(
                pack["mode"], preferred=pack.get("default_map", ""))
            core.change_map(target_map, pack["mode"], is_workshop=is_workshop,
                            caller=f"plugin-tab/pack:{pack_id}")
            return jsonify({
                "ok":         True,
                "pack_id":    pack_id,
                "name":       pack["name"],
                "mode":       pack["mode"],
                "map":        target_map,
                "restarting": True,
            }), 202

        result = core.set_offline_mode_and_deploy(
            pack["mode"],
            caller=f"plugin-tab/pack:{pack_id}",
            map_name=pack.get("default_map") or None,
        )
        if not result.get("ok"):
            return jsonify({"error": result.get("error") or "deploy failed"}), 500
        return jsonify({
            "ok":         True,
            "pack_id":    pack_id,
            "name":       pack["name"],
            "mode":       result["mode"],
            "map":        result["map"],
            "plugins":    result["plugins"],
            "restarting": False,
        })

    @app.route("/api/plugins/vanilla", methods=["POST"])
    @require_auth
    @require_local
    def api_plugins_vanilla():
        """Switch to a vanilla mode and undeploy all managed plugins.

        Uses ``Competitive`` as the canonical vanilla target — it's
        guaranteed to be in MODE_SETTINGS, has no plugins in
        ``_MODE_PLUGIN_NAMES``, and is the closest to a "stock CS2"
        configuration the operator can recognise.
        """
        ok, status, err = _plugin_action_preflight()
        if not ok:
            return jsonify(err), status

        # v0.14.1: live restart if running, offline stage otherwise.
        if core.running:
            target_map, is_workshop = _resolve_live_swap_map("Competitive")
            core.change_map(target_map, "Competitive", is_workshop=is_workshop,
                            caller="plugin-tab/vanilla")
            return jsonify({
                "ok":         True,
                "mode":       "Competitive",
                "restarting": True,
                "target_map": target_map,
            }), 202

        result = core.set_offline_mode_and_deploy("Competitive",
                                                   caller="plugin-tab/vanilla")
        if not result.get("ok"):
            return jsonify({"error": result.get("error") or "deploy failed"}), 500
        return jsonify({
            "ok":         True,
            "mode":       result["mode"],
            "plugins":    result["plugins"],
            "restarting": False,
        })

    # ── Server control ─────────────────────────────────────────────────────────

    @app.route("/api/server/start", methods=["POST"])
    @require_auth
    def server_start():
        if core.running:
            return jsonify({"error": "Server is already running"}), 400
        if not core.server_dir:
            return jsonify({"error": "Server directory not configured"}), 400
        if not core.is_installed:
            return jsonify({"error": "CS2 is not installed — use Config → Install to download it first"}), 400
        # v0.11.17 A6 — refuse Start while a workshop download is in flight.
        # Otherwise cs2.exe boots against a half-extracted addon folder and
        # either silently falls back to dust2 OR loads a broken map that
        # crashes mid-match.  Worse: the download process keeps writing to
        # files the server has open, producing Windows file-lock errors and
        # potentially corrupting both the server's view and the download.
        # 409 (Conflict) is the right HTTP code for "wait for the other
        # in-progress operation to finish."
        if getattr(core, "_active_dl_proc", None) is not None:
            return jsonify({
                "error": ("A workshop download is still running.  Wait for it "
                          "to finish (or cancel it) before starting the server."),
                "dl_progress": dict(getattr(core, "_dl_progress", {})),
            }), 409
        d        = request.get_json() or {}
        map_name = d.get("map", "de_dust2").strip()
        mode     = d.get("mode", "Competitive")
        workshop = bool(d.get("workshop", False))
        if mode not in GAME_MODES:
            return jsonify({"error": "Invalid game mode"}), 400
        if workshop:
            if not _DIGITS_RE.match(map_name):
                return jsonify({"error": "Invalid workshop map ID — digits only"}), 400
        elif not _MAP_NAME_RE.match(map_name):
            return jsonify({"error": "Invalid map name"}), 400
        # v0.10.2 — pre-flight first so a remote admin's failed Start gets a
        # useful error instead of a 200 OK + silently-stuck "Booting" pill.
        # start_server() ALSO runs the preflight internally as defence-in-
        # depth; calling it twice is cheap (port socket + tasklist + fs
        # stat).  On hard fail we return 422 (semantically: request
        # well-formed but the system can't fulfil it right now).
        preflight_ok, preflight_errors = core._preflight_checks(map_name, mode, workshop)
        if not preflight_ok:
            return jsonify({
                "error": "Pre-flight checks failed — fix the issues below and try again.",
                "preflight_errors": preflight_errors,
            }), 422
        core.start_server(map_name, mode, is_workshop=workshop)
        return jsonify({"ok": True})

    @app.route("/api/server/stop", methods=["POST"])
    @require_auth
    def server_stop():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        try:
            core.stop_server()
        except Exception as exc:
            core.log(f"[stop] server_stop failed: {exc!r}")
            return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})

    @app.route("/api/server/map", methods=["POST"])
    @require_auth
    def server_map():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        d        = request.get_json() or {}
        map_name = d.get("map", "").strip()
        mode     = d.get("mode", core.current_mode)
        workshop = bool(d.get("workshop", False))
        if not map_name:
            return jsonify({"error": "No map specified"}), 400
        if mode not in GAME_MODES:
            return jsonify({"error": "Invalid game mode"}), 400
        if workshop:
            if not _DIGITS_RE.match(map_name):
                return jsonify({"error": "Invalid workshop map ID — digits only"}), 400
        elif not _MAP_NAME_RE.match(map_name):
            return jsonify({"error": "Invalid map name"}), 400
        core.change_map(map_name, mode, workshop, caller=request.remote_addr or "web")
        return jsonify({"ok": True})

    @app.route("/api/server/broadcast", methods=["POST"])
    @require_auth
    def server_broadcast():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        msg = (request.get_json() or {}).get("message", "").strip()
        if not msg:
            return jsonify({"error": "Empty message"}), 400
        # Defang RCON command injection: newlines AND semicolons are both
        # statement separators on the Source 2 console, so `hello;sv_password x`
        # would otherwise be two commands.  Cap length too — RCON has no
        # legitimate use for a 1 MB chat broadcast.
        msg = (msg.replace("\r", " ")
                  .replace("\n", " ")
                  .replace(";",  ",")
                  .replace("`",  "'"))     # backtick = command-sub in some shells
        if len(msg) > _BROADCAST_MAX_LEN:
            msg = msg[:_BROADCAST_MAX_LEN]
        core.server_say(msg)
        return jsonify({"ok": True})

    @app.route("/api/server/ff", methods=["POST"])
    @require_auth
    def server_ff():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        enabled = bool((request.get_json() or {}).get("enabled", False))
        core.set_friendly_fire(enabled)
        return jsonify({"ok": True})

    @app.route("/api/server/round/restart", methods=["POST"])
    @require_auth
    def server_restart_round():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        core.restart_round()
        return jsonify({"ok": True})

    @app.route("/api/server/round/warmup", methods=["POST"])
    @require_auth
    def server_end_warmup():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        core.end_warmup()
        return jsonify({"ok": True})

    @app.route("/api/server/match/pause", methods=["POST"])
    @require_auth
    def server_pause():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        core.pause_match()
        return jsonify({"ok": True})

    @app.route("/api/server/match/unpause", methods=["POST"])
    @require_auth
    def server_unpause():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        core.unpause_match()
        return jsonify({"ok": True})

    # ── Bots ───────────────────────────────────────────────────────────────────

    @app.route("/api/bots/add", methods=["POST"])
    @require_auth
    def bots_add():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        count = int((request.get_json() or {}).get("count", 1))
        core.add_bots(max(1, min(count, 20)))
        return jsonify({"ok": True})

    @app.route("/api/bots/kick", methods=["POST"])
    @require_auth
    def bots_kick():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        core.kick_bots()
        return jsonify({"ok": True})

    # ── Players ────────────────────────────────────────────────────────────────

    @app.route("/api/players")
    @require_auth
    def api_players():
        result_holder: list = []
        done = threading.Event()

        def on_players(pl: list) -> None:
            result_holder.extend(pl)
            done.set()

        core.get_players(on_players)
        done.wait(timeout=8)
        return jsonify(result_holder)

    @app.route("/api/players/kick", methods=["POST"])
    @require_auth
    def players_kick():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        d      = request.get_json() or {}
        userid = str(d.get("userid", "")).strip()
        name   = str(d.get("name",   "")).strip()[:_NAME_MAX_LEN]   # cap before use
        if not _DIGITS_RE.match(userid) or len(userid) > 10:
            return jsonify({"error": "Invalid userid"}), 400
        core.kick_player(userid, name)
        return jsonify({"ok": True})

    @app.route("/api/players/ban", methods=["POST"])
    @require_auth
    def players_ban():
        d        = request.get_json() or {}
        steamid  = str(d.get("steamid",  "")).strip()
        name     = str(d.get("name",     "")).strip()
        duration = int(d.get("duration", 0))
        if not _STEAMID_RE.match(steamid):
            return jsonify({"error": "Invalid steamid"}), 400
        core.ban_player(steamid, name, duration)
        return jsonify({"ok": True})

    @app.route("/api/bans")
    @require_auth
    def bans_list():
        result_holder: list = []
        done = threading.Event()

        def on_bans(lines: list) -> None:
            result_holder.extend(lines)
            done.set()

        core.get_ban_list(on_bans)
        done.wait(timeout=8)
        return jsonify(result_holder)

    @app.route("/api/bans/remove", methods=["POST"])
    @require_auth
    def bans_remove():
        steamid = (request.get_json() or {}).get("steamid", "").strip()
        if not _STEAMID_RE.match(steamid):
            return jsonify({"error": "Invalid steamid"}), 400
        core.unban_player(steamid)
        return jsonify({"ok": True})

    # ── Config ─────────────────────────────────────────────────────────────────

    @app.route("/api/config")
    @require_auth
    def config_get():
        session  = _current_session()
        is_local = bool(session and session.get("is_local"))
        return jsonify({
            "server_dir":            core.server_dir,
            "hostname":              core.hostname,
            "sv_password":           core.sv_password   if is_local else "***",
            "gslt_token":            core.gslt_token    if is_local else "***",
            "tickrate_128":          core.tickrate_128,
            "auto_start":            core.auto_start,
            "auto_restart_on_crash": core.auto_restart_on_crash,
            "bot_difficulty":        core.bot_difficulty,
            "bots_enabled":          core.bots_enabled,
            "max_players_override":  core.max_players_override,
            # v0.10.1 online-primary veto config (safe to expose to remote —
            # the tunnel URL isn't a secret, the captain join URL would
            # leak it anyway, and auto-launch is a UX toggle not a credential)
            "public_share_url":             core.public_share_url,
            "veto_auto_launch_on_ready":    core.veto_auto_launch_on_ready,
            # v0.11.0 polish — operator-configurable MatchZy cvars.  Not
            # secret (echoed both local + remote); only local can write.
            "matchzy_cvars":                dict(getattr(core, "matchzy_cvars", {})),
            # v0.10.2 — Discord webhook (treated as a secret-ish URL: only
            # the local admin sees it.  Remote admins get "***" so they
            # can see "webhook is configured" without leaking the URL —
            # webhooks are unauth'd post tokens and shouldn't leak).
            "discord_webhook_url":          core.discord_webhook_url if is_local else ("***" if core.discord_webhook_url else ""),
            # v0.11.0 — Discord bot config.  Token is local-only (always
            # masked for remote).  Guild ID + channel ID are not secrets
            # (they're visible in any Discord invite) so they're exposed
            # for remote admins to read but only LOCAL admins can change
            # any of them.
            "discord_bot_token":            core.discord_bot_token if is_local else ("***" if core.discord_bot_token else ""),
            "discord_guild_id":             core.discord_guild_id,
            "discord_veto_channel_id":      core.discord_veto_channel_id,
            # v0.11.15 — default VC for one-click roster pull
            "discord_voice_channel_id":     core.discord_voice_channel_id,
            # v0.12.0 — per-team VCs + auto-move toggle (read-only here;
            # the toggle is mutated via /api/discord/auto_move_toggle so
            # the precondition check fires server-side)
            "discord_team_a_voice_channel_id":          core.discord_team_a_voice_channel_id,
            "discord_team_b_voice_channel_id":          core.discord_team_b_voice_channel_id,
            "discord_auto_move_on_distribute_enabled":  core.discord_auto_move_on_distribute_enabled,
            # v0.12.1 — round summaries (mutated via /api/discord/round_summaries_toggle)
            "discord_round_summaries_enabled":          core.discord_round_summaries_enabled,
            "admin_pin":             core.admin_pin     if is_local else "***",
            "guest_pin":             core.guest_pin     if is_local else "***",
            "rcon_password":         core.rcon_password  if is_local else "***",
            "steam_username":        core.steam_username if is_local else "***",
            "steam_session_active":  core.steam_session_active,
            "is_local":              is_local,
        })

    @app.route("/api/config", methods=["POST"])
    @require_auth
    def config_set():
        d        = request.get_json() or {}
        session  = _current_session()
        is_local = bool(session and session.get("is_local"))

        # Fields any authenticated client may change.
        # sv_password is masked as "***" for remote sessions in config_get, so a
        # remote save that echoes the mask back must not overwrite the real value.
        if "hostname"              in d: core.hostname              = str(d["hostname"])
        if "sv_password" in d and d["sv_password"] != "***":
            core.sv_password = str(d["sv_password"])
        if "tickrate_128"          in d: core.tickrate_128          = bool(d["tickrate_128"])
        if "auto_start"            in d: core.auto_start            = bool(d["auto_start"])
        if "auto_restart_on_crash" in d: core.auto_restart_on_crash = bool(d["auto_restart_on_crash"])
        if "bot_difficulty"        in d: core.bot_difficulty        = str(d["bot_difficulty"])
        if "bots_enabled"          in d: core.bots_enabled          = bool(d["bots_enabled"])
        if "max_players_override"  in d: core.max_players_override  = str(d["max_players_override"])
        # v0.10.1 — online-primary veto knobs
        if "public_share_url" in d:
            # Light validation: strip trailing slashes, accept blank to clear.
            v = str(d["public_share_url"]).strip()
            if v and not (v.startswith("http://") or v.startswith("https://")):
                return jsonify({
                    "error": "public_share_url must start with http:// or https:// "
                             "(or be blank to clear)"
                }), 400
            core.public_share_url = v.rstrip("/")
        if "veto_auto_launch_on_ready" in d:
            core.veto_auto_launch_on_ready = bool(d["veto_auto_launch_on_ready"])
        # v0.11.0 polish — MatchZy cvars editor.  Local admin only (the
        # cvar list can do things like disable demo recording or open RCON
        # commands; treat it like server config not chat).
        if "matchzy_cvars" in d:
            if not is_local:
                return jsonify({"error": "matchzy_cvars: local admin only"}), 403
            raw = d["matchzy_cvars"]
            if not isinstance(raw, dict):
                return jsonify({"error": "matchzy_cvars must be an object"}), 400
            # Coerce everything to str; drop empty-key entries (a blank row
            # in the SPA editor that the operator forgot to delete).
            core.matchzy_cvars = {
                str(k).strip(): str(v) if v is not None else ""
                for k, v in raw.items()
                if str(k).strip()
            }
        # v0.11.0 — Discord bot config: local-only writes (token is a secret).
        # When the token / guild changes we restart the bot so the new
        # value is picked up.  Guild + channel can change without
        # restart (looked up per-call).
        if is_local and "discord_bot_token" in d:
            v = str(d["discord_bot_token"]).strip()
            if v != "***":
                old = core.discord_bot_token
                core.discord_bot_token = v
                # v0.11.17 A7 — restart on token CHANGE (original behaviour) OR
                # when the operator re-saves the SAME token while the bot is
                # disconnected.  Previously a dead bot could only be revived
                # by restarting the whole app: `if v != old:` skipped the
                # restart, so re-saving did nothing.  Now Save Discord
                # Settings is also a "reconnect bot" lever.
                try:
                    from . import discord_bot
                    if not v:
                        # Token cleared → stop the bot regardless of prior state.
                        if v != old:
                            discord_bot.stop_bot(core)
                    elif v != old:
                        # New token → always (re)start.
                        discord_bot.start_bot(core)
                    else:
                        # Same token re-saved — only restart if the bot is
                        # actually disconnected.  Avoids needlessly bouncing
                        # a healthy bot when the operator clicked Save just
                        # to change guild_id / channel_id / voice_channel_id
                        # (which are also processed in this same handler).
                        status = discord_bot.bot_status()
                        if not status.get("connected"):
                            core.log("[discord] re-save with disconnected bot — restarting")
                            discord_bot.start_bot(core)
                except Exception as exc:
                    core.log(f"[discord] bot lifecycle on token change failed: {exc}")
        if is_local and "discord_guild_id" in d:
            core.discord_guild_id = str(d["discord_guild_id"]).strip()
        if is_local and "discord_veto_channel_id" in d:
            core.discord_veto_channel_id = str(d["discord_veto_channel_id"]).strip()
        # v0.11.15 — default voice channel for one-click roster pull.
        # Not a secret (anyone in the guild can see channel IDs), but the
        # write is local-only for consistency with the other Discord fields.
        # Bot looks it up per-call so no restart needed on change.
        if is_local and "discord_voice_channel_id" in d:
            core.discord_voice_channel_id = str(d["discord_voice_channel_id"]).strip()
        # v0.12.0 — per-team voice channels for /move-teams.
        if is_local and "discord_team_a_voice_channel_id" in d:
            core.discord_team_a_voice_channel_id = str(d["discord_team_a_voice_channel_id"]).strip()
        if is_local and "discord_team_b_voice_channel_id" in d:
            core.discord_team_b_voice_channel_id = str(d["discord_team_b_voice_channel_id"]).strip()
        # NOTE: discord_auto_move_on_distribute_enabled is NOT writable here;
        # it's mutated via POST /api/discord/auto_move_toggle so the
        # precondition check (both VCs configured before enable) lives in
        # one place.
        # v0.10.2 — Discord webhook URL: local-only write (it's a secret-ish
        # URL).  Mask-aware so a remote admin's accidental round-trip of
        # "***" doesn't blank the real value.
        if is_local and "discord_webhook_url" in d:
            v = str(d["discord_webhook_url"]).strip()
            if v == "***":
                pass    # mask round-trip, ignore
            elif v and not v.startswith("https://discord.com/api/webhooks/"):
                return jsonify({
                    "error": "discord_webhook_url must start with "
                             "https://discord.com/api/webhooks/ (or be blank to clear)"
                }), 400
            else:
                core.discord_webhook_url = v

        # Local-only fields (security-sensitive). The admin PIN protects the whole
        # panel, so only the trusted local window may change it.
        if is_local:
            if "admin_pin" in d:
                new_pin = str(d["admin_pin"]).strip()
                if new_pin.isdigit() and len(new_pin) >= 4:
                    core.admin_pin    = new_pin
                    _config.ADMIN_PIN = new_pin
            if "guest_pin" in d:
                # Empty string disables guest login; otherwise 4+ digits and must
                # differ from the admin PIN (admin wins at login, so equal = useless).
                new_gpin = str(d["guest_pin"]).strip()
                if new_gpin == "" or (new_gpin.isdigit() and len(new_gpin) >= 4
                                       and new_gpin != core.admin_pin):
                    core.guest_pin = new_gpin
            if "gslt_token" in d:
                core.gslt_token = str(d["gslt_token"])
            if "rcon_password" in d:
                new_pw = str(d["rcon_password"]).strip()
                if new_pw:
                    core.rcon_password    = new_pw
                    core.rcon.password    = new_pw
                    _config.RCON_PASSWORD = new_pw
            if "server_dir" in d:
                core.update_server_dir(str(d["server_dir"]))
            if "steam_username" in d: core.steam_username = str(d["steam_username"])
            if "steam_password" in d: core.steam_password = str(d["steam_password"])

        core.save_config()
        return jsonify({"ok": True})

    # ── Presets ────────────────────────────────────────────────────────────────

    @app.route("/api/presets")
    @require_auth
    def presets_list():
        return jsonify(list(core.presets.keys()))

    @app.route("/api/presets/save", methods=["POST"])
    @require_auth
    def presets_save():
        name = (request.get_json() or {}).get("name", "").strip()
        if not name:
            return jsonify({"error": "Preset name required"}), 400
        core.presets[name] = {"map": core.current_map, "mode": core.current_mode}
        core.save_config()
        return jsonify({"ok": True})

    @app.route("/api/presets/load", methods=["POST"])
    @require_auth
    def presets_load():
        name = (request.get_json() or {}).get("name", "").strip()
        p    = core.presets.get(name)
        if not p:
            return jsonify({"error": "Preset not found"}), 404
        return jsonify(p)

    @app.route("/api/presets/<name>", methods=["DELETE"])
    @require_auth
    def presets_delete(name: str):
        if name not in core.presets:
            return jsonify({"error": "Not found"}), 404
        del core.presets[name]
        core.save_config()
        return jsonify({"ok": True})

    # ── RCON console ───────────────────────────────────────────────────────────

    @app.route("/api/rcon", methods=["POST"])
    @require_auth
    @require_local
    def rcon_exec():
        if not core.running:
            return jsonify({"error": "Server is not running"}), 400
        cmd = (request.get_json() or {}).get("command", "").strip()
        if not cmd:
            return jsonify({"error": "No command"}), 400
        result: list = []
        done  = threading.Event()

        def cb(resp: str, err: str | None) -> None:
            result.append({"response": resp, "error": err})
            done.set()

        core.rcon_execute(cmd, callback=cb)
        done.wait(timeout=10)
        if result:
            return jsonify(result[0])
        return jsonify({"response": "", "error": "timeout"})

    # ── Workshop ───────────────────────────────────────────────────────────────

    @app.route("/api/workshop/maps")
    @require_auth
    def workshop_maps_list():
        import threading as _threading
        ids     = load_workshop()
        unknown = [wid for wid in ids if wid not in core._map_name_cache]
        if unknown:
            # Wait for names before returning so callers always get real titles.
            # Subsequent calls are instant (cache hit); only the first call per
            # session (or after new maps are downloaded) pays the ~1-2 s latency.
            done = _threading.Event()
            core.fetch_workshop_names(unknown, on_done=done.set)
            done.wait(timeout=12)
        return jsonify([
            {
                "id":          wid,
                "name":        core._map_name_cache.get(wid, ""),
                "tags":        core._map_tag_cache.get(wid, []),
                "preview_url": core._preview_url_cache.get(wid, ""),
                "cmdfilter":   core.cmdfilter_status(wid),
            }
            for wid in ids
        ])

    @app.route("/api/workshop/download", methods=["POST"])
    @require_auth   # guest-allowed (see _GUEST_PATHS) — friends can pull new maps
    def workshop_download():
        wid = (request.get_json() or {}).get("id", "").strip()
        if not wid.isdigit():
            return jsonify({"error": "Invalid workshop ID — digits only"}), 400
        if not core.steam_username or not core.steam_password:
            return jsonify({
                "error": "Steam credentials required",
                "needs_steam": True,
            }), 400
        # Reject concurrent downloads — depotdl_download spawns a thread per
        # call and overwrites core._active_dl_proc, so two simultaneous clicks
        # (or two guests) would orphan the first process; cancel_download
        # would only kill the latest and the staging dirs would collide.
        # Atomic check-and-reserve under core._dl_lock so two clicks can't
        # both observe None concurrently (v0.9.2.1 — v0.9.2's bare check had
        # the TOCTOU the lock was supposed to fix).  We set a sentinel here
        # and depotdl_download's worker overwrites with the real Popen handle.
        with core._dl_lock:
            if core._active_dl_proc is not None:
                return jsonify({
                    "error": "A download is already in progress — cancel it first or wait.",
                }), 409
            # Reserve the slot — `True` is a sentinel; the worker swaps in
            # the real subprocess.Popen handle once it's spawned.  Both the
            # worker's assign and `cancel_download` re-acquire the same lock.
            core._active_dl_proc = True  # type: ignore[assignment]
        core.depotdl_download(
            wid,
            on_done=lambda ok: core.log(
                f"Workshop download {'complete' if ok else 'FAILED'}: {wid}"
            ),
        )
        return jsonify({"ok": True})

    @app.route("/api/workshop/cancel", methods=["POST"])
    @require_auth   # guest-allowed — cancel a download they started
    def workshop_cancel():
        core.cancel_download()
        return jsonify({"ok": True})

    @app.route("/api/workshop/update", methods=["POST"])
    @require_auth
    @require_local
    def workshop_update():
        core.check_workshop_updates()
        return jsonify({"ok": True})

    @app.route("/api/workshop/cmdfilter/scan", methods=["POST"])
    @require_auth
    @require_local
    def workshop_cmdfilter_scan():
        """Re-scan all downloaded maps' descriptions for the command-filter flag."""
        import threading as _threading
        done   = _threading.Event()
        result = {}
        core.scan_cmdfilter(on_done=lambda flagged: (result.update(flagged=flagged),
                                                     done.set()))
        done.wait(timeout=30)
        return jsonify({"ok": True, "flagged": result.get("flagged", [])})

    @app.route("/api/workshop/cmdfilter/override", methods=["POST"])
    @require_auth
    @require_local
    def workshop_cmdfilter_override():
        """Set/clear the manual per-map command-filter override.

        Body: {"id": "<wid>", "value": true|false|null}  (null → revert to auto)
        """
        d   = request.get_json() or {}
        wid = str(d.get("id", "")).strip()
        if not wid.isdigit():
            return jsonify({"error": "Invalid workshop ID — digits only"}), 400
        value = d.get("value", None)
        if value is not None and not isinstance(value, bool):
            return jsonify({"error": "value must be true, false, or null"}), 400
        core.set_cmdfilter_override(wid, value)
        return jsonify({"ok": True, "status": core.cmdfilter_status(wid)})

    @app.route("/api/request_workshop", methods=["POST"])
    @require_auth
    def request_workshop():
        """Remote-safe: queues a download request for local approval."""
        wid = (request.get_json() or {}).get("workshop_id", "").strip()
        if not wid.isdigit():
            return jsonify({"error": "Invalid workshop ID — digits only"}), 400
        core.request_workshop_download(wid, requester=request.remote_addr or "remote")
        return jsonify({"ok": True})

    # ── Gaming Mode (v0.12.5 / task #95) ───────────────────────────────────
    # The "Host + Play" perf toolkit lives in scripts/.  Operator can run
    # gaming-mode-on / off / status from the SPA Config card without
    # opening a terminal.  Local-only by design — we shell out to
    # PowerShell with a 20s timeout; the operator's machine is implicitly
    # trusted (we're already running on it).
    def _scripts_dir():
        """Return absolute path to scripts/.  Works in dev + frozen.
        - Dev: <repo_root>/scripts/
        - Frozen: <install_dir>/scripts/ (installer.iss bundles it)
        """
        if getattr(sys, "frozen", False):
            # PyInstaller .exe — scripts ship alongside via installer.iss
            return os.path.join(os.path.dirname(sys.executable), "scripts")
        # Dev — cs2servergui/web.py → ../scripts/
        return os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "scripts"))

    def _run_gaming_mode(mode: str) -> tuple[int, str, str]:
        """Run gaming-mode.ps1 -Mode <mode>; return (returncode, stdout, stderr)."""
        ps1 = os.path.join(_scripts_dir(), "gaming-mode.ps1")
        if not os.path.isfile(ps1):
            return -1, "", f"gaming-mode.ps1 not found at {ps1}"
        try:
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", ps1, "-Mode", mode],
                capture_output=True, text=True, timeout=20.0,
            )
            return res.returncode, res.stdout or "", res.stderr or ""
        except subprocess.TimeoutExpired:
            return -1, "", "gaming-mode.ps1 timed out (>20s)"
        except Exception as exc:
            return -1, "", f"{type(exc).__name__}: {exc}"

    @app.route("/api/system/gaming_mode", methods=["POST"])
    @require_auth
    @require_local
    def system_gaming_mode():
        """Run scripts/gaming-mode.ps1 with the requested action.
        Body: {"mode": "on" | "off" | "status"}.
        Returns: {ok, mode, returncode, stdout, stderr}.
        """
        d = request.get_json() or {}
        mode = str(d.get("mode", "")).strip().lower()
        if mode not in ("on", "off", "status"):
            return jsonify({"error": "mode must be 'on', 'off', or 'status'"}), 400
        # Capitalise to match ps1 -Mode param values (On / Off / Status)
        ps_mode = {"on": "On", "off": "Off", "status": "Status"}[mode]
        rc, stdout, stderr = _run_gaming_mode(ps_mode)
        core.log(f"[gaming_mode] {mode} → rc={rc}")
        return jsonify({
            "ok":         rc == 0,
            "mode":       mode,
            "returncode": rc,
            "stdout":     stdout,
            "stderr":     stderr,
        })

    # ── Server installation (local only) ───────────────────────────────────────

    @app.route("/api/server/install", methods=["POST"])
    @require_auth
    @require_local
    def server_install():
        core.install_server()
        return jsonify({"ok": True})

    @app.route("/api/server/update_cs2", methods=["POST"])
    @require_auth
    @require_local
    def server_update():
        core.run_update()
        return jsonify({"ok": True})

    # ── Steam account (local only) ─────────────────────────────────────────────

    @app.route("/api/steam/login", methods=["POST"])
    @require_auth
    @require_local
    def steam_login():
        core.steam_login_interactive()
        return jsonify({"ok": True})

    # ── Directory picker (local only) ──────────────────────────────────────────

    @app.route("/api/system/pick_directory")
    @require_auth
    @require_local
    def system_pick_directory():
        import subprocess
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.Description = 'Select CS2 Server Directory'; "
            "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath } else { '' }"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=60,
            )
            path = r.stdout.strip()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"path": path})

    # ── First-run setup ────────────────────────────────────────────────────────

    @app.route("/api/setup/status")
    @require_auth
    @require_local   # leaks `pin_is_default` to guests otherwise — a remote
                     # guest learning the admin PIN is still "1234" is half a
                     # PIN brute-force away from full admin.
    def setup_status():
        """Return whether first-run setup is still needed.

        Local-only: the first-run wizard only ever runs in the pywebview window.
        """
        return jsonify({
            "needs_setup":     core.needs_setup,
            "server_dir_set":  bool(core.server_dir),
            "pin_is_default":  core.admin_pin == "1234",
        })

    @app.route("/api/setup/complete", methods=["POST"])
    @require_auth
    @require_local
    def setup_complete():
        """Persist first-run choices and clear the setup flag."""
        import os as _os
        d          = request.get_json() or {}
        session_   = _current_session()
        is_local_  = bool(session_ and session_.get("is_local"))

        # ── Server directory (required) ────────────────────────────────────
        server_dir = d.get("server_dir", "").strip()
        if not server_dir:
            return jsonify({"error": "Server directory is required"}), 400
        if not _os.path.isdir(server_dir):
            return jsonify({"error": "Directory does not exist"}), 400
        core.update_server_dir(server_dir)

        # ── Admin PIN (required — must differ from default) ────────────────
        new_pin = str(d.get("admin_pin", "")).strip()
        if not new_pin.isdigit() or len(new_pin) < 4:
            return jsonify({"error": "PIN must be 4 or more digits"}), 400
        core.admin_pin    = new_pin
        _config.ADMIN_PIN = new_pin

        # ── GSLT token (optional) ──────────────────────────────────────────
        gslt = d.get("gslt_token", "").strip()
        if gslt:
            core.gslt_token = gslt

        core.save_config()
        core.log("First-run setup complete")
        return jsonify({"ok": True})

    # ── Game data (no auth — static lists) ────────────────────────────────────

    @app.route("/api/data/modes")
    def data_modes():
        return jsonify(GAME_MODES)

    @app.route("/api/data/maps")
    def data_maps():
        return jsonify(OFFICIAL_MAPS)

    @app.route("/api/data/mode_maps")
    def data_mode_maps():
        return jsonify({k: (v or []) for k, v in MODE_MAPS.items()})

    @app.route("/api/data/mode_workshop_tags")
    def data_mode_workshop_tags():
        return jsonify(_config.MODE_WORKSHOP_TAGS)

    # ── Map thumbnails ─────────────────────────────────────────────────────────
    # Thumbnails are bundled as static files in static/images/map_thumbs/.
    # The endpoint also checks the dedicated server's panorama directory first
    # so that locally-installed high-res textures are preferred when available.

    @app.route("/api/maps/thumb/<map_name>")
    def map_thumb(map_name: str):
        """Serve a map thumbnail.

        Priority:
          1. Dedicated server panorama directory (local PNG, highest quality)
          2. Bundled static thumbnail (shipped with the app, always present)
        """
        import os as _os
        import re
        import sys as _sys

        if not re.match(r"^[a-z0-9_]+$", map_name):
            abort(404)

        # 1. Dedicated server install — prefer local panorama PNGs when present
        # CS2_ADDONS_DIR = .../game/csgo/addons  →  go up 3 levels to reach the
        # CS2 install root (.../Counter-Strike Global Offensive), then re-append
        # the panorama subpath which starts with "game/csgo/panorama/...".
        if core.server_dir:
            cs2_root  = _os.path.dirname(_os.path.dirname(_os.path.dirname(_config.CS2_ADDONS_DIR)))
            candidate = _os.path.join(cs2_root, _config.CS2_PANORAMA_THUMBS_SUBPATH, f"{map_name}.png")
            if _os.path.isfile(candidate):
                return send_file(candidate, mimetype="image/png")

        # 2. Bundled static thumbnails (static/images/map_thumbs/<map>.(png|jpg))
        if getattr(_sys, "frozen", False):
            # PyInstaller unpacks data files relative to sys._MEIPASS
            static_base = _os.path.join(_sys._MEIPASS, "cs2servergui", "static")
        else:
            static_base = _os.path.join(_os.path.dirname(__file__), "static")

        thumbs_dir = _os.path.join(static_base, "images", "map_thumbs")
        for ext, mime in (("png", "image/png"), ("jpg", "image/jpeg")):
            p = _os.path.join(thumbs_dir, f"{map_name}.{ext}")
            if _os.path.isfile(p):
                return send_file(p, mimetype=mime)

        abort(404)

    # ── Map-veto session (v0.10.0) ────────────────────────────────────────────
    # Thin HTTP wrappers over cs2servergui.veto.  Every mutation acquires
    # core._veto_lock so SSE listeners + concurrent admin/captain requests
    # can't tear the session state.  Veto exceptions translate to 400.
    from . import veto as _veto
    from .veto import VetoError, RosterPlayer

    # Pub/sub for the live-mirror SSE stream.  Same pattern as the existing
    # log-stream queues: subscribers register a Queue; every state-change
    # broadcasts a JSON snapshot to all of them.
    _veto_subs: list[queue.Queue] = []
    _veto_subs_lock = threading.Lock()
    # v0.16.14 / task #170 — parallel subscriber list for the spectator
    # SSE stream.  Same pattern but receives the SANITIZED snapshot
    # (Discord IDs stripped, SteamIDs masked, captain claim tokens never
    # sent).  Kept separate from _veto_subs so a spectator stream stall
    # can't backpressure the captain/admin stream.
    _veto_spec_subs: list[queue.Queue] = []
    _veto_spec_subs_lock = threading.Lock()
    # v0.12.2 — broadcast observability (audit finding #10 / task #143).
    # Track total events dropped due to subscriber-queue overflow + total
    # broadcast attempts.  Surfaces in the diagnostic snapshot so a real
    # production overflow gets caught — pre-v0.12.2 a silent drop was the
    # leading hypothesis for the v0.11.25 polling fallback's existence,
    # but we had no way to confirm or deny.  With this counter visible
    # in /api/diag/snapshot the next stuck-UI complaint either has a
    # smoking gun ("broadcast_drops=47, that's the bug") or rules out
    # this layer ("broadcast_drops=0, look elsewhere").
    _veto_broadcast_stats_lock = threading.Lock()
    _veto_broadcast_stats = {
        "events_total":   0,   # broadcast() calls since process start
        "drops_total":    0,   # put_nowait Full exceptions (silent drops)
        "last_drop_at":   0.0, # epoch time of most recent drop, or 0.0
    }

    # v0.10.2: match history persistence — guards concurrent appends so two
    # operators clicking Hand-to-MatchZy at the same time can't corrupt the
    # JSON.  Reads are lock-free (the file is overwritten atomically via
    # tmp + os.replace).
    _match_history_lock = threading.Lock()

    def _save_to_match_history(entry: dict) -> None:
        """Append `entry` to oblivion_matches.json, keep last
        MATCH_HISTORY_KEEP entries.  Atomic write (tmp + os.replace) so a
        crash mid-save can't truncate the file."""
        from .config import MATCH_HISTORY_FILE, MATCH_HISTORY_KEEP
        try:
            with _match_history_lock:
                # Load existing — empty list if file missing / unreadable
                existing: list = []
                if os.path.isfile(MATCH_HISTORY_FILE):
                    try:
                        with open(MATCH_HISTORY_FILE, "r", encoding="utf-8") as f:
                            existing = json.load(f) or []
                            if not isinstance(existing, list):
                                existing = []
                    except (OSError, ValueError):
                        existing = []
                existing.append(entry)
                # Keep the most recent N (the list is append-only, so the
                # newest is at the end).  Drop older ones.
                if len(existing) > MATCH_HISTORY_KEEP:
                    existing = existing[-MATCH_HISTORY_KEEP:]
                tmp = MATCH_HISTORY_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)
                    f.flush()
                    try: os.fsync(f.fileno())
                    except OSError: pass
                os.replace(tmp, MATCH_HISTORY_FILE)
            core.log(f"[veto] match archived to history (last {len(existing)} kept)")
        except Exception as exc:
            core.log(f"[veto] history write failed: {exc}")

    def _load_match_history() -> list:
        """Read oblivion_matches.json.  Returns [] if file missing / corrupt
        (no recovery dance — operator can re-run a match and overwrite)."""
        from .config import MATCH_HISTORY_FILE
        try:
            if not os.path.isfile(MATCH_HISTORY_FILE):
                return []
            with open(MATCH_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or []
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _attempt_captain_dms(tokens: dict, urls_builder) -> dict:
        """v0.11.0 Layer 1A — Try to DM each captain their join URL via the
        Discord bot.  Returns {team: True} per team that received a DM.

        Best-effort: every fall-through path now logs ONE diagnostic line
        (v0.11.0 Tue fix — silent skips were untriageable).  Possible
        outcomes per team:
          - bot not running / not connected   → "Layer 1A: bot not connected"
          - no veto session                   → "Layer 1A: no session"
          - captain index unset               → "Layer 1A: captain X not elected"
          - captain has no discord_id         → "Layer 1A: captain X (Name) has no discord_id"
          - URL build returned empty          → "Layer 1A: no URL for X"
          - DM call failed                    → "DM to captain X failed ..."
          - DM call succeeded                 → "DM'd captain X ..."

        Operator can read the log drawer right after Generate captain
        links and know exactly which path the code took.
        """
        results = {}
        try:
            from . import discord_bot
            status = discord_bot.bot_status()
            if not status.get("connected"):
                core.log(f"[discord] Layer 1A: bot not connected "
                         f"(configured={status.get('configured')}) — skipping DM")
                return results
        except Exception as exc:
            core.log(f"[discord] Layer 1A: bot module unavailable: {exc}")
            return results
        s = core._veto_session
        if s is None:
            core.log("[discord] Layer 1A: no active veto session — skipping DM")
            return results
        # Resolve captain Discord IDs from the live session
        captains = {}
        if s.captain_a_idx is not None and 0 <= s.captain_a_idx < len(s.team_a):
            captains["A"] = s.team_a[s.captain_a_idx]
        else:
            core.log("[discord] Layer 1A: captain A index unset — skipping team A DM")
        if s.captain_b_idx is not None and 0 <= s.captain_b_idx < len(s.team_b):
            captains["B"] = s.team_b[s.captain_b_idx]
        else:
            core.log("[discord] Layer 1A: captain B index unset — skipping team B DM")
        for team, player in captains.items():
            did = (player.discord_id or "").strip()
            if not did:
                core.log(f"[discord] Layer 1A: captain {team} ({player.name}) has no "
                         "discord_id in roster — skipping DM (operator: paste their "
                         "Discord User ID into the Discord ID column of their roster slot)")
                continue
            token = tokens.get(team, "")
            url_obj = urls_builder(token)
            link = url_obj.get("public") or url_obj.get("lan") or ""
            if not link:
                core.log(f"[discord] Layer 1A: no URL for {team} — skipping DM")
                continue
            team_name = (s.team_a_name if team == "A" else s.team_b_name)
            msg = (
                f"🎯 **{player.name}** ({team_name}) — your veto link:\n"
                f"{link}\n"
                f"Single-use. Click to claim your captain seat."
            )
            core.log(f"[discord] Layer 1A: DMing captain {team} ({player.name}) at id={did}…")
            try:
                ok = discord_bot.bot_dm_user(did, msg)
                if ok:
                    results[team] = True
                    core.log(f"[discord] DM'd captain {team} ({player.name}) at id={did} ✓")
                else:
                    core.log(f"[discord] DM to captain {team} ({player.name}) failed "
                             f"(blocked? unknown id? bot offline?) — falling back to "
                             "Copy-for-Discord")
            except Exception as exc:
                core.log(f"[discord] DM raise: {type(exc).__name__}: {exc}")
        return results

    # ─── v0.11.0 Layer 1C — Live veto embed in Discord channel ────────────
    def _build_live_veto_embed(session) -> dict:
        """Render the current veto session as a Discord embed dict.  Called
        on every state transition that the bot should reflect (veto entry,
        each ban/pick, finale).

        Embed shape:
          * Title    — "{mode} — {team_a} vs {team_b}"  (yellow if active, green if locked)
          * Description — turn indicator OR "✅ MATCH LOCKED IN" on finale
          * Field "Maps" — bullet list of maps with state
                ✅ Mirage         picked by Team Alpha
                ❌ Inferno        banned by Team Bravo
                🏁 Anubis         decider
                ⬜ Nuke           remaining
          * Field "Captains" — each team's elected captain
          * Footer  — matchid; on finale also includes "matchzy_loadmatch X"
        """
        s = session
        mode = s.mode
        teamA, teamB = s.team_a_name, s.team_b_name
        # Build map state lookup from sequence
        banned = {}        # map_id → team that banned
        picked = {}        # map_id → team that picked
        for st in s.sequence:
            if not st.map_id:
                continue
            if st.kind == "BAN":  banned[st.map_id] = st.team
            elif st.kind == "PICK": picked[st.map_id] = st.team

        # Current step + whose turn
        current = None
        if 0 <= s.current_step < len(s.sequence):
            cs = s.sequence[s.current_step]
            if not cs.map_id:    # not yet performed
                current = cs

        # Build the maps section
        team_name_of = lambda t: teamA if t == "A" else teamB
        lines = []
        for m in s.map_pool:
            if m in banned:
                lines.append(f"❌ `{m:<14}` banned by **{team_name_of(banned[m])}**")
            elif m in picked:
                lines.append(f"✅ `{m:<14}` picked by **{team_name_of(picked[m])}**")
            elif m == s.decider:
                lines.append(f"🏁 `{m:<14}` **decider**")
            else:
                lines.append(f"⬜ `{m:<14}` —")

        # Description: turn indicator OR finale message
        if s.state == "complete" or s.state == "finale":
            desc = "✅ **MATCH LOCKED IN** — get ready to battle."
            color = 0x57F287   # discord green
        elif current:
            verb = "BAN" if current.kind == "BAN" else "PICK"
            desc = f"⏳ **{team_name_of(current.team)}** to {verb}  (step {s.current_step + 1}/{len(s.sequence)})"
            color = 0xFEE75C   # discord yellow
        else:
            desc = "Veto starting…"
            color = 0x5865F2   # discord blurple

        cap_a_name = (s.team_a[s.captain_a_idx].name if s.captain_a_idx is not None and 0 <= s.captain_a_idx < len(s.team_a) else "?")
        cap_b_name = (s.team_b[s.captain_b_idx].name if s.captain_b_idx is not None and 0 <= s.captain_b_idx < len(s.team_b) else "?")

        matchid = ((s.matchzy_config or {}).get("matchid")
                   or f"oblivion-veto-{int(s.created_at)}")
        footer_text = f"matchid: {matchid}"
        if s.state in ("finale", "complete") and s.final_maps:
            footer_text += f" · maplist: {' → '.join(s.final_maps)}"

        return {
            "title":       f"🎮 {mode} · {teamA} vs {teamB}",
            "description": desc,
            "color":       color,
            "fields": [
                {"name": "Map veto",  "value": "\n".join(lines) or "(no maps yet)", "inline": False},
                {"name": "Captains",  "value": f"**{teamA}** — {cap_a_name}\n**{teamB}** — {cap_b_name}",
                 "inline": False},
            ],
            "footer": {"text": footer_text},
        }

    # v0.11.17 B1 — coalescing serializer for live embed refreshes.
    #
    # Original behaviour: every `_refresh_live_veto_embed(...)` call spawned
    # a fresh daemon thread that immediately raced into the Discord API.
    # Two failure modes during rapid clicks (captain rage-clicks during the
    # ban phase, BO5 with 7+ steps, etc.):
    #
    #   1. Out-of-order edits: thread for step N+1 acquires its Discord
    #      socket before thread for step N completes → spectators see
    #      step N's state AFTER step N+1.
    #   2. Duplicate posts: when the existing message edit fails, BOTH
    #      threads (each having snapshotted `existing_msg_id == ""`)
    #      decide to post a fresh embed → two embeds in the channel.
    #
    # Fix: serialize via a single in-flight worker + a "pending" slot that
    # always holds the LATEST snapshot.  The worker drains the slot in a
    # loop, so a new refresh request arriving mid-send doesn't spawn a new
    # thread; the in-flight worker picks it up after the current API call
    # finishes.  Stale snapshots get coalesced away — only the newest
    # snapshot since the last send actually hits Discord.
    _embed_send_lock     = threading.Lock()
    _embed_pending_lock  = threading.Lock()
    _embed_pending: dict = {"snap": None}

    def _refresh_live_veto_embed(reason: str = "step") -> None:
        """Post or edit the live veto embed.  Called from /api/veto/step
        and /api/veto/finale + the captain-claim path that flips state to
        `veto`.  Coalesces concurrent calls (v0.11.17 B1) so spectators
        always see the LATEST state and rapid clicks can't produce
        duplicate posts or out-of-order edits.  Silent no-op when the
        target channel isn't configured or the bot isn't connected.
        """
        try:
            from . import discord_bot
        except Exception:
            return
        if not core.discord_veto_channel_id:
            return    # operator hasn't configured a target channel
        if not discord_bot.bot_status().get("connected"):
            return
        # Snapshot the session under lock so the embed reflects a consistent
        # state (mid-step mutations elsewhere can't interleave).
        with core._veto_lock:
            s = core._veto_session
            if s is None:
                return
            if s.state not in ("veto", "finale", "complete"):
                return     # not yet at a stage worth posting about
            embed_dict = _build_live_veto_embed(s)
            existing_msg_id = s.live_embed_msg_id or ""
            channel_id = core.discord_veto_channel_id

        # Hand off the latest snapshot to the worker.  Drops any older
        # pending snapshot — only the newest matters because the embed
        # message itself is what the operator sees; intermediate states
        # are irrelevant once a newer one is available.
        with _embed_pending_lock:
            _embed_pending["snap"] = (channel_id, embed_dict, existing_msg_id, reason)

        # Try to become the in-flight worker.  If someone else is already
        # in flight, they'll drain our snapshot on their next loop and we
        # have nothing more to do.
        if not _embed_send_lock.acquire(blocking=False):
            return

        def _do() -> None:
            try:
                while True:
                    with _embed_pending_lock:
                        snap = _embed_pending["snap"]
                        _embed_pending["snap"] = None
                    if snap is None:
                        return    # nothing more queued; exit worker
                    cid, ed, mid, rsn = snap
                    try:
                        if mid:
                            ok = discord_bot.bot_edit_embed(cid, mid, ed)
                            if ok:
                                core.log(f"[discord] live veto embed edited ({rsn})")
                            else:
                                # Edit failed — message may have been deleted manually
                                # or perms revoked.  Try posting a fresh one.
                                core.log(f"[discord] live veto embed edit failed — "
                                         f"posting a fresh one")
                                msg_id = discord_bot.bot_post_embed(cid, ed)
                                if msg_id:
                                    with core._veto_lock:
                                        if core._veto_session is not None:
                                            core._veto_session.live_embed_msg_id = msg_id
                        else:
                            msg_id = discord_bot.bot_post_embed(cid, ed)
                            if msg_id:
                                with core._veto_lock:
                                    if core._veto_session is not None:
                                        core._veto_session.live_embed_msg_id = msg_id
                                core.log(f"[discord] live veto embed posted (msg {msg_id})")
                            else:
                                core.log(f"[discord] live veto embed post failed — "
                                         f"check bot permissions on channel {cid}")
                    except Exception as exc:
                        core.log(f"[discord] live veto embed failed: "
                                 f"{type(exc).__name__}: {exc}")
                    # Loop: if another refresh request arrived while we
                    # were in the Discord API call, drain that one too.
                    # Otherwise the next loop iteration's pop returns None
                    # and we exit.
            finally:
                _embed_send_lock.release()
        threading.Thread(target=_do, daemon=True).start()

    def _post_discord_finale_webhook(history_entry: dict, matchzy_result: dict) -> None:
        """v0.10.2 — POST a Discord embed to the operator's webhook URL
        when a finale completes.  Fire-and-forget.  10-second timeout;
        silent failure (logs once).  Webhook URL is stored in
        core.discord_webhook_url; format must be the standard
        https://discord.com/api/webhooks/<id>/<token>.

        Embed format keeps to a single accent colour + structured fields
        so the channel reads cleanly.  Fields:
          • Mode (BO1/BO3/BO5)
          • Map list (with the decider tagged)
          • Teams + captains
          • MatchZy status (loaded / pending / file-only)
          • Connect command (so spectators can join to watch live)
        """
        import urllib.request, urllib.error
        url = core.discord_webhook_url
        if not url:
            return
        # Build the embed.  Discord limits: 256-char title, 2048-char
        # description, 25 fields, each field 256-char name + 1024-char value.
        # We're well under all of those.
        team_a = history_entry.get("team_a", {})
        team_b = history_entry.get("team_b", {})
        maps_list = history_entry.get("final_maps") or []
        decider = history_entry.get("decider")
        mode = history_entry.get("mode", "")
        maplines = []
        for i, m in enumerate(maps_list):
            if m == decider:
                maplines.append(f"  🏁 **{m}** (decider)")
            else:
                maplines.append(f"  • Map {i+1}: {m}")
        # MatchZy status line
        if matchzy_result.get("loaded"):
            mz_status = "✅ MatchZy: loaded + ready"
        elif matchzy_result.get("error"):
            mz_status = f"⚠ MatchZy: {matchzy_result['error']}"
        else:
            mz_status = "ℹ MatchZy: config written, manual load needed"
        # Connect command — pull from the live session's match_connect block
        connect_cmd = ""
        with core._veto_lock:
            if core._veto_session is not None:
                # Reproduce the snapshot logic for match_connect (state may
                # have moved to `complete` but the IP/password are the same)
                game_host = core.public_ip or _config._lan_ip()
                connect_cmd = f"connect {game_host}:{RCON_PORT}"
                if core.sv_password:
                    connect_cmd += f"; password {core.sv_password}"
        embed = {
            "title": f"🎮 {mode} match locked in — {team_a.get('name','A')} vs {team_b.get('name','B')}",
            "color": 0xFF6B35,           # accent-ish orange; tweak via theme later
            "fields": [
                {"name": "Maps",     "value": "\n".join(maplines) or "(none)", "inline": False},
                {"name": "Captains", "value": (
                    f"**{team_a.get('name','A')}** — {history_entry.get('captain_a','?')}\n"
                    f"**{team_b.get('name','B')}** — {history_entry.get('captain_b','?')}"
                ), "inline": False},
                {"name": "MatchZy",  "value": mz_status, "inline": False},
            ],
            "footer": {"text": f"matchid: {history_entry.get('matchid','?')}"},
        }
        if connect_cmd:
            embed["fields"].append({
                "name":   "Connect (for your team)",
                "value":  f"```\n{connect_cmd}\n```",
                "inline": False,
            })
        payload = {"embeds": [embed]}
        body = json.dumps(payload).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json",
                         "User-Agent": "OblivionServerTool"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    core.log(f"[veto] Discord webhook returned HTTP {resp.status}")
                else:
                    core.log("[veto] Discord finale webhook posted ✓")
        except urllib.error.HTTPError as he:
            core.log(f"[veto] Discord webhook HTTP {he.code}: {he.reason} "
                     f"(check that the webhook URL is still valid)")
        except Exception as exc:
            core.log(f"[veto] Discord webhook failed: {type(exc).__name__}: {exc}")

    def _veto_snapshot() -> dict:
        """Serialise the current session to a JSON-safe dict.  Tokens are
        REDACTED from the snapshot — captains learn their token via the
        one-time tokens response or by clicking their join URL; the snapshot
        only ever exposes which tokens are claimed, not their values.

        Includes pre-computed `current_step_detail` + `legal_moves` so the
        SPA frontend doesn't have to re-derive them on every render.
        """
        s = core._veto_session
        if s is None:
            return {"state": "idle", "session": None}
        # Pre-compute the active step + legal-move set for the frontend.
        step = _veto.current_step(s)
        if step is not None:
            already_picked = {st.map_id for st in s.sequence[:s.current_step]
                              if st.kind == "PICK" and st.map_id}
            legal = _veto.remaining_maps(s)
            if step.kind == "PICK":
                legal = [m for m in legal if m not in already_picked]
            step_detail = {
                "index": s.current_step,
                "kind":  step.kind,
                "team":  step.team,
            }
        else:
            legal = []
            step_detail = None
        # v0.10.2: at finale/complete states, surface the game-server
        # connect string + password so captains can copy it into their
        # team's Discord.  Captains are authenticated via single-use token
        # so they're already authorized to know the password — without
        # this they can't get their teammates into the match.  The CS2
        # game-server runs on UDP:27015 — that's NOT the Cloudflare
        # tunnel (which is HTTPS for the web panel only); captains' team
        # members connect direct to the operator's public IP + 27015,
        # which means the operator must port-forward 27015 separately.
        match_connect = None
        if s.state in ("finale", "complete"):
            game_host = core.public_ip or _config._lan_ip()
            game_port = RCON_PORT          # CS2 listens on the same port for game + RCON
            pw        = core.sv_password or ""
            cmd       = f"connect {game_host}:{game_port}"
            if pw:
                cmd += f"; password {pw}"
            match_connect = {
                "host":     game_host,
                "port":     game_port,
                "password": pw,
                "password_set": bool(pw),
                "command":  cmd,
            }
        return {
            "state": s.state,
            "session": {
                "mode":          s.mode,
                "map_pool":      list(s.map_pool),
                "team_a_name":   s.team_a_name,
                "team_b_name":   s.team_b_name,
                # v0.11.0: include discord_id so the SPA roster grid can
                # round-trip the field on re-render.  Players without one
                # serialise as empty string.
                "roster":        [{"name": p.name, "steam_id": p.steam_id,
                                   "discord_id": p.discord_id}
                                  for p in s.roster],
                "team_a":        [{"name": p.name, "steam_id": p.steam_id,
                                   "discord_id": p.discord_id}
                                  for p in s.team_a],
                "team_b":        [{"name": p.name, "steam_id": p.steam_id,
                                   "discord_id": p.discord_id}
                                  for p in s.team_b],
                "votes_a":       dict(s.votes_a),
                "votes_b":       dict(s.votes_b),
                "captain_a_idx": s.captain_a_idx,
                "captain_b_idx": s.captain_b_idx,
                "revote_count":  s.revote_count,
                "tokens_claimed": {team: tok.used
                                   for team, tok in s.tokens.items()},
                # v0.12.3 / task #135 — voter token claim status.  Map
                # of "A:0" .. "B:4" → bool.  SPA uses this to render
                # ✓ next to each player who's claimed (so the operator
                # can see who's missing without scrolling the snapshot).
                "voter_tokens_claimed": {key: tok.used
                                         for key, tok in s.voter_tokens.items()},
                "sequence":      [{"kind": st.kind, "team": st.team, "map_id": st.map_id}
                                  for st in s.sequence],
                "current_step":  s.current_step,
                # NEW: pre-computed for the frontend (Day 3 SPA).
                "current_step_detail": step_detail,
                "legal_moves":   list(legal),
                "decider":       s.decider,
                "final_maps":    list(s.final_maps),
                # v0.10.1: captain ready flags + convenience derived bool so
                # the SPA admin button doesn't have to AND them client-side
                "ready_a":       s.ready_a,
                "ready_b":       s.ready_b,
                "both_ready":    _veto.both_captains_ready(s),
                # v0.10.2 — non-null only at finale/complete; the captain
                # finale + admin finale renderers use this to surface the
                # CS2 server connect command + password (captains need to
                # tell their team where to join)
                "match_connect": match_connect,
                "updated_at":    s.updated_at,
            },
        }

    def _veto_broadcast() -> None:
        """Push a fresh snapshot to every SSE subscriber.  Non-blocking —
        if a subscriber's queue is full, drop the message for that client.

        v0.11.3 also persists the session to disk here so an accidental
        Ctrl+Q / app crash mid-session survives.  Persistence is cheap
        (~5 KB atomic write) and runs synchronously — broadcast is the
        natural choke-point because EVERY state mutation already routes
        through it."""
        snap = _veto_snapshot()
        payload = "data: " + __import__("json").dumps(snap) + "\n\n"
        with _veto_subs_lock:
            subs = list(_veto_subs)
        drops_this_event = 0
        for q in subs:
            try:
                q.put_nowait(payload)
            except Exception:
                drops_this_event += 1
        # v0.16.14 / task #170 — also broadcast the sanitized snapshot to
        # spectator subscribers.  Sanitization is cheap (single dict walk)
        # so we do it on the broadcast thread rather than per-subscriber.
        # Wrapped in try because veto.build_spectator_snapshot needs a
        # session and we want to fail-soft if state is mid-mutation.
        try:
            sess = core._veto_session
            if sess is not None:
                spec = _veto.build_spectator_snapshot(sess)
                spec_payload = "data: " + __import__("json").dumps(spec) + "\n\n"
                with _veto_spec_subs_lock:
                    spec_subs = list(_veto_spec_subs)
                for q in spec_subs:
                    try:
                        q.put_nowait(spec_payload)
                    except Exception:
                        drops_this_event += 1
        except Exception as exc:
            core.log(f"[veto] spectator broadcast failed: {exc!r}")
        # v0.12.2 — record drops for /api/diag/snapshot visibility.
        with _veto_broadcast_stats_lock:
            _veto_broadcast_stats["events_total"] += 1
            if drops_this_event:
                _veto_broadcast_stats["drops_total"] += drops_this_event
                _veto_broadcast_stats["last_drop_at"] = time.time()
                # Log on FIRST drop only per process — don't spam if a
                # subscriber keeps drifting.  The counter still increments.
                if _veto_broadcast_stats["drops_total"] == drops_this_event:
                    core.log(
                        f"[veto] SSE broadcast queue overflow — {drops_this_event} "
                        f"event(s) dropped (subscriber not draining fast enough).  "
                        f"Diagnostic snapshot will show running totals.")
        # v0.11.3 — persist the active session.  Cheap, atomic, fail-soft.
        _persist_active_veto()

    # v0.11.3 — active-session persistence ────────────────────────────────
    _veto_persist_lock = threading.Lock()

    def _persist_active_veto() -> None:
        """Atomic write of core._veto_session to VETO_ACTIVE_FILE so an
        app restart can resume.  If no session is active, the file is
        deleted (clean state)."""
        from .config import VETO_ACTIVE_FILE
        try:
            with _veto_persist_lock:
                # Read state under the veto lock then drop it — we don't
                # need to hold _veto_lock during the disk write.
                with core._veto_lock:
                    sess = core._veto_session
                    snapshot = _veto.serialize_session(sess) if sess else None
                if snapshot is None:
                    # No active session → ensure file is gone
                    try:
                        if os.path.isfile(VETO_ACTIVE_FILE):
                            os.remove(VETO_ACTIVE_FILE)
                    except OSError:
                        pass
                    return
                tmp = VETO_ACTIVE_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, indent=2, ensure_ascii=False)
                    f.flush()
                    try: os.fsync(f.fileno())
                    except OSError: pass
                os.replace(tmp, VETO_ACTIVE_FILE)
        except Exception as exc:
            # Persistence failure must NEVER break a live session.  Log + move on.
            core.log(f"[veto] persistence write failed: {exc}")

    def _veto_error_response(exc: Exception):
        """Map VetoError subclasses to 400; everything else to 500."""
        if isinstance(exc, VetoError):
            return jsonify({"error": str(exc), "type": type(exc).__name__}), 400
        core.log(f"[veto] unexpected error: {type(exc).__name__}: {exc}")
        return jsonify({"error": "internal server error"}), 500

    @app.route("/api/veto/state")
    @require_auth        # captain-allowed (see _CAPTAIN_PATHS)
    def veto_state():
        with core._veto_lock:
            return jsonify(_veto_snapshot())

    @app.route("/api/veto/stream")
    @require_auth        # captain-allowed
    def veto_stream():
        # v0.12.2 — bump queue depth 32 → 256.  Each event is ~3-5 KB so
        # 256 events ≈ 1-1.5 MB per stalled subscriber, well within
        # tolerance.  Audit finding #10 (task #143) flagged the 32-event
        # ceiling as the theoretical cause of the polling-fallback need;
        # in practice no real workflow bursts 32 broadcasts back-to-back,
        # but the larger ceiling closes the speculative gap and gives a
        # stalled WebView2 plenty of room to drain on resume.  See
        # _veto_broadcast_stats for live drop telemetry.
        q: queue.Queue = queue.Queue(maxsize=256)
        # Push the current state immediately so a fresh subscriber renders
        # without waiting for the next event.
        try: q.put_nowait("data: " + __import__("json").dumps(_veto_snapshot()) + "\n\n")
        except Exception: pass
        with _veto_subs_lock: _veto_subs.append(q)
        def gen():
            try:
                while True:
                    try: yield q.get(timeout=25)
                    except queue.Empty: yield ": keepalive\n\n"
            finally:
                with _veto_subs_lock:
                    if q in _veto_subs: _veto_subs.remove(q)
        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    @app.route("/api/veto/create", methods=["POST"])
    @require_auth
    def veto_create():
        d = request.get_json() or {}
        mode = d.get("mode", "BO3")
        map_pool = d.get("map_pool")
        with core._veto_lock:
            # Refuse to silently clobber an in-flight session.  If the
            # operator means to start over, they call /api/veto/reset first.
            # This protects against accidental double-clicks on the SPA's
            # Create button AND against a guest captain unknowingly nuking
            # an active veto by re-claiming a stale URL.
            if core._veto_session is not None and core._veto_session.state != "idle":
                return jsonify({
                    "error": "A veto session is already active — call /api/veto/reset first.",
                    "current_state": core._veto_session.state,
                }), 409
            try:
                core._veto_session = _veto.create_session(mode=mode, map_pool=map_pool)
            except Exception as e:
                return _veto_error_response(e)
            snap = _veto_snapshot()
        _veto_broadcast()
        core.log(f"[veto] session created — mode={mode} pool=({len(snap['session']['map_pool'])} maps)")
        return jsonify(snap)

    # ── Discord bot — voice-channel roster pull (v0.11.0 Layer 1B) ────────────
    # Two thin endpoints over discord_bot:
    #   GET /api/discord/voice_channels                — list channels in guild
    #   GET /api/discord/voice_members?channel_id=...  — connected members
    # The SPA Roster page calls the first to populate a picker, then the
    # second to fill in the 10 player slots with {display_name, discord_id}.
    # Operator still types SteamIDs by hand (Discord doesn't expose them).
    # Admin-only via the role gate (NOT in _GUEST_PATHS / _CAPTAIN_PATHS).
    # The data returned (channel names + connected member display names) is
    # already public to anyone in the server, so admin-only is the right
    # level — the bot token itself stays in local-only config writes.
    @app.route("/api/discord/voice_channels")
    @require_auth
    def discord_voice_channels():
        if not core.discord_guild_id:
            return jsonify({
                "error": "Discord guild ID not configured — set it in Config → Discord."
            }), 400
        try:
            from . import discord_bot
        except Exception as exc:
            return jsonify({"error": f"bot module unavailable: {exc}"}), 503
        if not discord_bot.bot_status().get("connected"):
            return jsonify({"error": "Discord bot is not connected"}), 503
        channels = discord_bot.bot_voice_channels(core.discord_guild_id)
        if channels is None:
            return jsonify({
                "error": "Could not read voice channels — verify the bot has "
                         "View Channels + Connect permissions on the server, "
                         "and that the guild ID matches."
            }), 502
        return jsonify({"channels": channels})

    # v0.11.18 — Text-channel picker: lists every text channel in the guild
    # so the operator's Discord Config card can offer a 🔍 Browse helper
    # next to the Veto Embed Channel ID field.  Same auth + gates as
    # /api/discord/voice_channels.  Returns {channels: [{id, name}, ...]}.
    @app.route("/api/discord/text_channels")
    @require_auth
    def discord_text_channels():
        if not core.discord_guild_id:
            return jsonify({
                "error": "Discord guild ID not configured — set it in Config → Discord."
            }), 400
        try:
            from . import discord_bot
        except Exception as exc:
            return jsonify({"error": f"bot module unavailable: {exc}"}), 503
        if not discord_bot.bot_status().get("connected"):
            return jsonify({"error": "Discord bot is not connected"}), 503
        channels = discord_bot.bot_text_channels(core.discord_guild_id)
        if channels is None:
            return jsonify({
                "error": "Could not read text channels — verify the bot has "
                         "View Channels permission on the server, and that "
                         "the guild ID matches."
            }), 502
        return jsonify({"channels": channels})

    # v0.11.15 — Single-VC info lookup (id, name, live member_count).
    # Used by the Discord Config card to show "configured default VC: #foo
    # (N connected)" without enumerating the entire guild, and by the Veto
    # roster modal to label the one-click pull button with the live count.
    # Query string: channel_id (optional — falls back to configured
    # discord_voice_channel_id; useful for the Config-card live preview).
    @app.route("/api/discord/voice_channel_info")
    @require_auth
    def discord_voice_channel_info():
        cid = (request.args.get("channel_id") or
               core.discord_voice_channel_id or "").strip()
        if not cid:
            return jsonify({
                "error": "No channel ID — pass channel_id or configure "
                         "discord_voice_channel_id."
            }), 400
        if not core.discord_guild_id:
            return jsonify({
                "error": "Discord guild ID not configured — set it in Config → Discord."
            }), 400
        try:
            from . import discord_bot
        except Exception as exc:
            return jsonify({"error": f"bot module unavailable: {exc}"}), 503
        if not discord_bot.bot_status().get("connected"):
            return jsonify({"error": "Discord bot is not connected"}), 503
        info = discord_bot.bot_voice_channel_info(core.discord_guild_id, cid)
        if info is None:
            return jsonify({
                "error": "Could not read that voice channel — verify the ID is "
                         "correct, the bot has View Channels permission, and "
                         "the channel still exists."
            }), 502
        return jsonify({"channel": info})

    # ── v0.12.0 — Bot-driven team voice splits ──────────────────────────────
    # Operator config:
    #   discord_team_a_voice_channel_id
    #   discord_team_b_voice_channel_id
    #   discord_auto_move_on_distribute_enabled  (default False — opt-in)
    #
    # Triggers:
    #   1. POST /api/discord/move_teams       — manual fire (button or
    #                                            slash command's `now`)
    #   2. POST /api/discord/auto_move_toggle — set the persistent toggle
    #   3. veto_distribute() — auto-fire when toggle=True + both VCs set
    #                          + bot connected (fire-and-forget thread,
    #                          must not block /api/veto/distribute)
    #
    # Bot needs Move Members + View Channels + Connect on the target VCs.

    @app.route("/api/discord/move_teams", methods=["POST"])
    @require_auth
    def discord_move_teams():
        a_vc = (core.discord_team_a_voice_channel_id or "").strip()
        b_vc = (core.discord_team_b_voice_channel_id or "").strip()
        if not core.discord_guild_id:
            return jsonify({"error": "Discord guild ID not configured."}), 400
        if not a_vc or not b_vc:
            return jsonify({
                "error": "Both Team A and Team B voice channels must be "
                         "configured in Config → Discord."
            }), 400
        with core._veto_lock:
            sess = core._veto_session
            if sess is None or sess.state in ("idle", "roster"):
                return jsonify({
                    "error": "No team-split veto session — distribute teams "
                             "first (or the session is still on the roster stage)."
                }), 400
            a_ids = [p.discord_id for p in sess.team_a if (p.discord_id or "").strip()]
            b_ids = [p.discord_id for p in sess.team_b if (p.discord_id or "").strip()]
        if not a_ids and not b_ids:
            return jsonify({
                "error": "No discord_ids on either team — fill them in the "
                         "Roster stage before moving."
            }), 400
        try:
            from . import discord_bot
        except Exception as exc:
            return jsonify({"error": f"bot module unavailable: {exc}"}), 503
        if not discord_bot.bot_status().get("connected"):
            return jsonify({"error": "Discord bot is not connected"}), 503
        result = discord_bot.bot_move_to_team_channels(
            core.discord_guild_id, a_vc, b_vc, a_ids, b_ids,
        )
        if result is None:
            return jsonify({
                "error": "Move failed — verify guild + both VCs exist, bot has "
                         "Move Members permission, and bot is connected."
            }), 502
        core.log(f"[discord] move_teams — moved A={result['moved_a']}/"
                 f"{len(a_ids)}, B={result['moved_b']}/{len(b_ids)}, "
                 f"skipped {result['skipped']}, errors {len(result['errors'])}")
        return jsonify(result)

    @app.route("/api/discord/round_summaries_toggle", methods=["POST"])
    @require_auth
    def discord_round_summaries_toggle():
        """v0.12.1 — flip discord_round_summaries_enabled.
        Mirrors auto_move_toggle's shape.  No preconditions to enforce
        on enable — the embed target is `discord_veto_channel_id` which
        is already in use for the live veto embed, so if that's set the
        round summaries have a target too; if it's blank the embed-post
        helper silently no-ops (same fallback as the live veto embed).
        """
        d = request.get_json() or {}
        want_enabled = bool(d.get("enabled", False))
        core.discord_round_summaries_enabled = want_enabled
        core.save_config()
        core.log(f"[discord] round_summaries_enabled = {want_enabled}")
        return jsonify({"enabled": want_enabled})

    @app.route("/api/discord/auto_move_toggle", methods=["POST"])
    @require_auth
    def discord_auto_move_toggle():
        d = request.get_json() or {}
        want_enabled = bool(d.get("enabled", False))
        if want_enabled:
            # Refuse to enable if either VC is missing — saves the operator
            # from a silent-no-op tournament-night surprise.
            if (not (core.discord_team_a_voice_channel_id or "").strip() or
                    not (core.discord_team_b_voice_channel_id or "").strip()):
                return jsonify({
                    "error": "Configure both Team A and Team B voice channels "
                             "before enabling auto-move."
                }), 400
        core.discord_auto_move_on_distribute_enabled = want_enabled
        core.save_config()
        core.log(f"[discord] auto_move_on_distribute = {want_enabled}")
        return jsonify({"enabled": want_enabled})

    @app.route("/api/discord/test_embed", methods=["POST"])
    @require_auth
    def discord_test_embed():
        """v0.11.0 polish — Post a sample embed to the configured veto
        channel.  Lets the operator verify channel ID + bot permissions
        + embed rendering WITHOUT walking a full veto.  Optional body:
        {channel_id: "..."} overrides the configured channel for a one-
        off test.  Returns 200 on success with the posted message ID;
        4xx with a useful error otherwise."""
        d = request.get_json() or {}
        cid = (d.get("channel_id") or core.discord_veto_channel_id or "").strip()
        if not cid:
            return jsonify({
                "error": "No channel ID — set discord_veto_channel_id in "
                         "Config → Discord, or pass channel_id in the body."
            }), 400
        try:
            from . import discord_bot
        except Exception as exc:
            return jsonify({"error": f"bot module unavailable: {exc}"}), 503
        if not discord_bot.bot_status().get("connected"):
            return jsonify({"error": "Discord bot is not connected"}), 503
        # Sample embed that mirrors the real live-veto shape so the
        # operator sees exactly what spectators will see during a match.
        embed = {
            "title": "🧪 Oblivion test embed — bot connection OK",
            "description": ("This is a one-off test message from the Oblivion Server Tool "
                            "to verify your Discord configuration.  If you can see this, "
                            "the live veto embed (Layer 1C) will work during real matches."),
            "color": 0x5865F2,
            "fields": [
                {"name": "What this confirms", "value": (
                    "✓ Bot token valid + connected\n"
                    "✓ Channel ID points to a channel the bot can post in\n"
                    "✓ Bot has Embed Links permission\n"
                    "✓ Discord rendering of the embed format works"
                ), "inline": False},
                {"name": "What this does NOT confirm", "value": (
                    "Live ban/pick updates (edits) — those need a real veto session\n"
                    "Captain DM delivery — use \"Send test DM\" for that"
                ), "inline": False},
            ],
            "footer": {"text": "Safe to delete this message."},
        }
        msg_id = discord_bot.bot_post_embed(cid, embed)
        if not msg_id:
            return jsonify({
                "error": f"Post failed — verify the bot has Send Messages + Embed "
                         f"Links permissions on channel {cid}.  See log drawer for details."
            }), 502
        core.log(f"[discord] test embed posted to channel {cid} (msg {msg_id})")
        return jsonify({"ok": True, "channel_id": cid, "message_id": msg_id})

    # v0.16.3 / task #165 — Discord mock-veto smoke test.
    # The existing test_embed + test_dm buttons each verify ONE bot
    # capability.  This walks the FULL embed lifecycle (post → 3 edits
    # → final "complete" state) plus a voice-channel reachability
    # check, all against the configured channels — no real veto needed.
    # Operator gets a list of "what worked / what didn't" so they can
    # spot wiring issues before the first real captain DM goes out.
    @app.route("/api/discord/mock_veto", methods=["POST"])
    @require_auth
    @require_local
    def discord_mock_veto():
        import time as _time
        try:
            from . import discord_bot
        except Exception as exc:
            return jsonify({"error": f"bot module unavailable: {exc}"}), 503
        if not discord_bot.bot_status().get("connected"):
            return jsonify({"error": "Discord bot is not connected"}), 503

        cid = (core.discord_veto_channel_id or "").strip()
        if not cid:
            return jsonify({
                "error": "discord_veto_channel_id is not set in Config → Discord",
            }), 400

        steps: list[dict] = []
        def step(name: str, ok: bool, detail: str = ""):
            steps.append({"name": name, "ok": ok, "detail": detail})

        # 1. Post the initial "veto starting" embed.
        embed = {
            "title": "🧪 Mock veto — bot smoke test",
            "description": ("Starting a simulated 5v5 veto.  This will edit itself "
                            "through 3 stages over ~3s, then settle on a final "
                            "state.  Safe to delete after."),
            "color": 0x5865F2,
            "fields": [{"name": "Stage", "value": "🟦 Initialising", "inline": False}],
            "footer": {"text": "Oblivion mock-veto / safe to delete"},
        }
        msg_id = discord_bot.bot_post_embed(cid, embed)
        if not msg_id:
            step("post_initial", False,
                 f"Failed to post initial embed to channel {cid}. "
                 "Verify Send Messages + Embed Links perms.")
            return jsonify({"ok": False, "steps": steps,
                             "summary": "Bot cannot post; aborting."}), 502
        step("post_initial", True, f"Posted message {msg_id}")

        # 2. Edit through three simulated stages.  Embed edits use the
        # same code path as Layer 1C live updates during real matches.
        stages = [
            ("Teams formed", "Team Alpha (5) vs Team Bravo (5)"),
            ("Map veto in progress", "Team Alpha ban de_overpass · Team Bravo ban de_nuke · Team Alpha pick de_inferno"),
            ("Veto complete",
             "Final maps: de_inferno, de_mirage, de_anubis (decider)\n"
             "Connect: connect 192.168.0.103:27015"),
        ]
        for i, (title, body) in enumerate(stages, start=1):
            _time.sleep(1.0)
            embed_i = {
                "title": embed["title"],
                "description": embed["description"],
                "color":  0x5865F2 if i < 3 else 0x57F287,
                "fields": [{"name": "Stage", "value": f"🟢 {title}", "inline": False},
                           {"name": "Detail", "value": body, "inline": False}],
                "footer": embed["footer"],
            }
            edit_ok = discord_bot.bot_edit_embed(cid, msg_id, embed_i)
            step(f"edit_stage_{i}", bool(edit_ok),
                 f"{title}" if edit_ok
                 else "edit failed — bot may have lost Manage Messages perm")
            if not edit_ok:
                # Don't bail; report the failure but continue to give
                # the operator the full picture.
                pass

        # 3. Voice channel reachability (if any VC configured).
        gid = (core.discord_guild_id or "").strip()
        vc  = (core.discord_voice_channel_id or "").strip()
        if gid and vc:
            try:
                info = discord_bot.bot_voice_channel_info(gid, vc, timeout=2.0)
            except Exception as exc:
                info = None
                step("vc_reach", False, f"voice channel lookup raised: {exc!r}")
            if info and info.get("name"):
                step("vc_reach", True,
                     f"Default VC reachable: {info['name']} "
                     f"({info.get('member_count', 0)} connected)")
            elif info is None:
                pass  # error step already added above
            else:
                step("vc_reach", False,
                     "Default VC lookup returned nothing — bot may lack View Channels perm")
        else:
            step("vc_reach", True, "No default VC configured (optional)")

        ok_count = sum(1 for s in steps if s["ok"])
        return jsonify({
            "ok":          ok_count == len(steps),
            "steps":       steps,
            "channel_id":  cid,
            "message_id":  msg_id,
            "summary":     f"{ok_count} of {len(steps)} checks passed",
        })

    @app.route("/api/discord/test_dm", methods=["POST"])
    @require_auth
    def discord_test_dm():
        """v0.11.0 polish — DM a one-off test message to a Discord user.
        Lets the operator verify the auto-DM path (Layer 1A) WITHOUT
        having to walk a full veto + vote themselves captain.  Body:
        {discord_id: "..."} — typically the operator's own user ID."""
        d = request.get_json() or {}
        did = (d.get("discord_id") or "").strip()
        if not did:
            return jsonify({
                "error": "discord_id required (your own Discord User ID is fine for testing)"
            }), 400
        try:
            from . import discord_bot
        except Exception as exc:
            return jsonify({"error": f"bot module unavailable: {exc}"}), 503
        if not discord_bot.bot_status().get("connected"):
            return jsonify({"error": "Discord bot is not connected"}), 503
        msg = (
            "🧪 **Oblivion test DM** — Layer 1A check\n\n"
            "If you can read this, the auto-DM captain-link flow will work "
            "during real matches.  During a veto, this is where you'd see "
            "your captain join URL.\n\n"
            "Safe to ignore this message."
        )
        ok = discord_bot.bot_dm_user(did, msg)
        if not ok:
            return jsonify({
                "error": (
                    f"DM to {did} failed.  Most likely cause: the user has "
                    "\"Allow direct messages from server members\" disabled "
                    "in Discord (User Settings → Privacy & Safety).  The "
                    "bot can't bypass that.  Verify the User ID is correct "
                    "and that the user has DMs enabled."
                )
            }), 502
        core.log(f"[discord] test DM sent to user {did}")
        return jsonify({"ok": True, "discord_id": did})

    @app.route("/api/discord/voice_members")
    @require_auth
    def discord_voice_members():
        channel_id = request.args.get("channel_id", "").strip()
        if not channel_id:
            return jsonify({"error": "channel_id required"}), 400
        if not core.discord_guild_id:
            return jsonify({
                "error": "Discord guild ID not configured — set it in Config → Discord."
            }), 400
        try:
            from . import discord_bot
        except Exception as exc:
            return jsonify({"error": f"bot module unavailable: {exc}"}), 503
        if not discord_bot.bot_status().get("connected"):
            return jsonify({"error": "Discord bot is not connected"}), 503
        members = discord_bot.bot_voice_members(core.discord_guild_id, channel_id)
        if members is None:
            return jsonify({
                "error": "Could not read channel members — verify the channel ID "
                         "is correct, the bot has access, and Server Members "
                         "intent is enabled in Developer Portal."
            }), 502
        return jsonify({"members": members})

    @app.route("/api/veto/roster", methods=["POST"])
    @require_auth
    def veto_roster():
        d = request.get_json() or {}
        # Validate inputs before grabbing the lock so we don't hold it for
        # bad-input parsing.
        team_a_name = str(d.get("team_a_name", "")).strip()[:64]
        team_b_name = str(d.get("team_b_name", "")).strip()[:64]
        raw_players = d.get("players", [])
        if not isinstance(raw_players, list):
            return jsonify({"error": "players must be a list"}), 400
        try:
            players = [
                RosterPlayer(
                    name=str(p.get("name", "")).strip()[:_NAME_MAX_LEN],
                    steam_id=str(p.get("steam_id", "")).strip()[:64],
                    # v0.11.0: per-player Discord user ID for auto-DM of
                    # captain links.  Optional; blank if operator didn't
                    # collect it.  Capped + digits-only validation in
                    # discord_bot.bot_dm_user before any API call.
                    discord_id=str(p.get("discord_id", "")).strip()[:32],
                )
                for p in raw_players
            ]
        except Exception:
            return jsonify({"error": "malformed player entry"}), 400
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                _veto.set_roster(core._veto_session, team_a_name, team_b_name, players)
                snap = _veto_snapshot()
            except Exception as e:
                return _veto_error_response(e)
        _veto_broadcast()
        return jsonify(snap)

    @app.route("/api/veto/distribute", methods=["POST"])
    @require_auth
    def veto_distribute():
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                _veto.distribute_teams(core._veto_session)
                snap = _veto_snapshot()
                # v0.12.0 — capture discord_ids for the optional auto-move
                # below; do it inside the lock to avoid TOCTOU vs reset.
                team_a_ids = [p.discord_id for p in core._veto_session.team_a
                              if (p.discord_id or "").strip()]
                team_b_ids = [p.discord_id for p in core._veto_session.team_b
                              if (p.discord_id or "").strip()]
            except Exception as e:
                return _veto_error_response(e)
        _veto_broadcast()
        # v0.12.0 — auto-move players to their team VCs.  Three preconditions
        # must all be true (no implicit triggers — operator opted in):
        #   - discord_auto_move_on_distribute_enabled (persistent toggle)
        #   - both team VCs configured
        #   - bot connected
        # Background thread so the distribute response is not delayed by
        # Discord API roundtrips.  Failure is logged + non-fatal — teams
        # are split server-side regardless.
        if (core.discord_auto_move_on_distribute_enabled
                and core.discord_guild_id
                and (core.discord_team_a_voice_channel_id or "").strip()
                and (core.discord_team_b_voice_channel_id or "").strip()):
            def _auto_move():
                # ~2s grace so a player who joined the lobby late isn't
                # missed by the move.  Cheap; the operator's already
                # looking at the teams panel.
                time.sleep(2.0)
                try:
                    from . import discord_bot
                except Exception:
                    return
                if not discord_bot.bot_status().get("connected"):
                    return
                try:
                    result = discord_bot.bot_move_to_team_channels(
                        core.discord_guild_id,
                        core.discord_team_a_voice_channel_id,
                        core.discord_team_b_voice_channel_id,
                        team_a_ids, team_b_ids,
                    )
                except Exception as exc:
                    core.log(f"[discord] auto-move after distribute failed: {exc}")
                    return
                if result is None:
                    core.log("[discord] auto-move after distribute: no result "
                             "(guild/VCs/perms?)")
                    return
                core.log(f"[discord] auto-moved teams after distribute: "
                         f"A={result['moved_a']}/{len(team_a_ids)}, "
                         f"B={result['moved_b']}/{len(team_b_ids)}, "
                         f"skipped {result['skipped']}, "
                         f"errors {len(result['errors'])}")
            threading.Thread(target=_auto_move, daemon=True,
                             name="oblivion-auto-move").start()
        return jsonify(snap)

    @app.route("/api/veto/start_voting", methods=["POST"])
    @require_auth
    def veto_start_voting():
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                _veto.start_voting(core._veto_session)
                snap = _veto_snapshot()
            except Exception as e:
                return _veto_error_response(e)
        _veto_broadcast()
        return jsonify(snap)

    @app.route("/api/veto/vote", methods=["POST"])
    @require_auth
    def veto_vote():
        d = request.get_json() or {}
        team = str(d.get("team", ""))
        try:
            voter_idx = int(d.get("voter_idx", -1))
            votee_idx = int(d.get("votee_idx", -1))
        except (TypeError, ValueError):
            return jsonify({"error": "voter_idx/votee_idx must be integers"}), 400
        # v0.12.3 / task #135 — voter role can only vote for their own
        # team + their own voter_idx.  Admin / local can vote for anyone
        # (operator override / dev testing).  Captains never reach this
        # endpoint (not in _CAPTAIN_PATHS).
        session = _current_session() or {}
        is_admin = session.get("is_local") or session.get("role") == "admin"
        if not is_admin and session.get("role") == "voter":
            v_team = session.get("voter_team", "")
            v_idx  = session.get("voter_idx", -1)
            if team != v_team or voter_idx != v_idx:
                return jsonify({
                    "error": "voters can only cast their own vote — "
                             f"session is bound to team {v_team} idx {v_idx}"
                }), 403
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                _veto.cast_vote(core._veto_session, team, voter_idx, votee_idx)
                snap = _veto_snapshot()
            except Exception as e:
                return _veto_error_response(e)
        _veto_broadcast()
        return jsonify(snap)

    @app.route("/api/veto/resolve_captains", methods=["POST"])
    @require_auth
    def veto_resolve_captains():
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                outcome = _veto.resolve_captains(core._veto_session)
                snap = _veto_snapshot()
            except Exception as e:
                return _veto_error_response(e)
        _veto_broadcast()
        snap["outcome"] = outcome
        return jsonify(snap)

    @app.route("/api/veto/tokens", methods=["POST"])
    @require_auth
    def veto_tokens():
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                tokens = _veto.issue_tokens(core._veto_session)
            except Exception as e:
                return _veto_error_response(e)
        # Build the LAN + Public links per captain.  v0.10.1: when the
        # operator has set `public_share_url` (typically their Cloudflare
        # tunnel URL like https://random-words.trycloudflare.com), the
        # Public link is built from THAT base instead of
        # http://<public_ip>:<port>/.  This is the difference between a
        # working URL (cloudflared / reverse proxy) and a dead one
        # (port-forward but operator didn't set it up).  For online matches
        # the tunnel URL is what reaches the captain.
        lan_ip       = _config._lan_ip()
        public_ip    = core.public_ip or ""
        port         = _config.FLASK_PORT
        share_base   = (getattr(core, "public_share_url", "") or "").rstrip("/")
        def _urls(token: str) -> dict:
            urls = {"lan": f"http://{lan_ip}:{port}/veto?join={token}"}
            if share_base:
                urls["public"] = f"{share_base}/veto?join={token}"
            elif public_ip:
                urls["public"] = f"http://{public_ip}:{port}/veto?join={token}"
            return urls
        # v0.11.0 Layer 1A — auto-DM captain links via Discord bot.  Both
        # captains' Discord IDs come from RosterPlayer.discord_id; the
        # share-base URL is preferred (online use) with LAN fallback.
        # Failure (bot not running, ID missing, DM blocked) is non-fatal:
        # the SPA still gets the URLs in the response so the operator's
        # existing Copy-for-Discord button works as before.
        dm_results = _attempt_captain_dms(tokens, _urls)

        _veto_broadcast()
        # Include the raw token alongside the URLs so the SPA can build
        # /api/veto/qr?token=… without re-parsing it out of the LAN URL.
        # `dm_sent` per team tells the SPA whether to dim the Copy-for-
        # Discord button (already delivered) or keep it primary.
        return jsonify({
            "A": {"token": tokens["A"], "dm_sent": dm_results.get("A", False),
                  **_urls(tokens["A"])},
            "B": {"token": tokens["B"], "dm_sent": dm_results.get("B", False),
                  **_urls(tokens["B"])},
        })

    @app.route("/api/veto/revoke_token", methods=["POST"])
    @require_auth
    def veto_revoke_token():
        d = request.get_json() or {}
        team = str(d.get("team", ""))
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                new_token = _veto.revoke_token(core._veto_session, team)
            except Exception as e:
                return _veto_error_response(e)
        lan_ip     = _config._lan_ip()
        public_ip  = core.public_ip or ""
        port       = _config.FLASK_PORT
        share_base = (getattr(core, "public_share_url", "") or "").rstrip("/")
        urls   = {"lan": f"http://{lan_ip}:{port}/veto?join={new_token}"}
        if share_base:
            urls["public"] = f"{share_base}/veto?join={new_token}"
        elif public_ip:
            urls["public"] = f"http://{public_ip}:{port}/veto?join={new_token}"
        _veto_broadcast()
        # Include raw token (mirrors /api/veto/tokens) so the SPA can build
        # the QR URL without parsing the token out of the LAN link.
        return jsonify({"team": team, "token": new_token, "urls": urls})

    @app.route("/api/veto/claim", methods=["POST"])
    def veto_claim():
        """Public endpoint — token IS the credential.  On success, mints a
        captain session cookie scoped to the team the token belongs to."""
        d = request.get_json() or {}
        token = str(d.get("token", "")).strip()
        if not token:
            return jsonify({"error": "missing token"}), 400
        # caller_id = client IP so re-opens from the same browser are idempotent.
        caller_ip = request.remote_addr or ""
        # v0.11.26 — hold _veto_lock through BOTH claim_captain AND the
        # captain-session mint.  Without this, a concurrent veto_reset can
        # land between the lock release and _create_session, producing a
        # captain cookie that references core._veto_session=None (a zombie
        # session that survives reset and silently auto-authenticates against
        # the next session's team A).  Audit finding #1.
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                team = _veto.claim_captain(core._veto_session, token, caller_id=caller_ip)
            except Exception as e:
                return _veto_error_response(e)
            session_token = _create_session(caller_ip, is_local=False, role="captain")
            sess = _get_session(session_token)
            if sess is not None:
                sess["captain_team"] = team
        core.log(f"[veto] captain {team} claimed from {caller_ip}")
        _veto_broadcast()
        # v0.11.0 Layer 1C: if this claim flipped state to `veto`, post the
        # initial live embed.  No-op when discord_veto_channel_id isn't
        # configured or the bot isn't connected.
        _refresh_live_veto_embed(reason="captain claim")
        resp = jsonify({"ok": True, "team": team})
        resp.set_cookie("session", session_token, httponly=True, samesite="Lax",
                        secure=_request_is_https())    # v0.11.17 A3
        return resp

    @app.route("/veto")
    def veto_share_landing():
        """Captain-link landing page.  `/veto?join=<token>` performs the
        claim server-side, sets the cookie, and renders the SPA shell which
        navigates itself to the veto board.  Without `?join=`, just renders
        the SPA (the admin / a guest can still reach the live mirror)."""
        token = request.args.get("join", "").strip()
        if token:
            caller_ip = request.remote_addr or ""
            # v0.11.26 — hold _veto_lock through claim + _create_session; see
            # audit finding #1 and the /api/veto/claim handler above.
            session_token = None
            with core._veto_lock:
                try:
                    if core._veto_session is not None:
                        team = _veto.claim_captain(core._veto_session, token, caller_id=caller_ip)
                    else:
                        team = None
                except Exception as e:
                    core.log(f"[veto] share-link claim failed: {e}")
                    team = None
                if team is not None:
                    session_token = _create_session(caller_ip, is_local=False, role="captain")
                    sess = _get_session(session_token)
                    if sess is not None:
                        sess["captain_team"] = team
            if team is not None and session_token is not None:
                _veto_broadcast()
                # Serve a real HTML page (not a 302 redirect) so that iOS
                # WKWebView / Discord in-app browser doesn't treat this as a
                # "bounce redirect" and strip the Set-Cookie header via ITP.
                # The JS redirect to /#veto happens same-origin after the
                # cookie is safely stored.
                html = (
                    "<!doctype html><html><head>"
                    "<meta charset=utf-8>"
                    "<meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>Connecting…</title>"
                    "<style>body{margin:0;display:flex;align-items:center;"
                    "justify-content:center;min-height:100vh;"
                    "background:#0d0d0f;color:#ccc;font-family:sans-serif;font-size:1.1rem}"
                    "</style></head>"
                    "<body><span>Connecting to veto…</span>"
                    "<script>window.location.replace('/#veto');</script>"
                    "</body></html>"
                )
                resp = make_response(html, 200)
                resp.set_cookie("session", session_token, httponly=True, samesite="Lax",
                                secure=_request_is_https())    # v0.11.17 A3 / v0.11.20
                # v0.11.26 — refuse intermediary caching.  The URL contains a
                # one-shot token; if Cloudflare / a carrier proxy caches this
                # 200 HTML, the SECOND request with the same URL would get the
                # body without the Set-Cookie, leaving the captain at /#veto
                # unauthenticated AND the token already consumed server-side.
                # Audit finding #2.
                resp.headers["Cache-Control"] = "no-store, private"
                resp.headers["Pragma"] = "no-cache"
                return resp
        # Fall through — render the SPA shell; the frontend handles the rest.
        return redirect("/#veto")

    # ─── v0.12.3 / task #135 — Remote player voting ──────────────────────
    # Parallel infrastructure to the captain flow.  After Distribute,
    # operator clicks "DM voting links" on the SPA Teams stage; the bot
    # DMs each of the 10 rostered players (with discord_id set) a
    # personal URL like /voter?join=<token>.  Voter clicks → HTML
    # interstitial sets cookie → voter sees a minimal voting screen
    # showing their own team's 5 names → clicks one → vote cast.
    #
    # No PIN involved — the token IS the credential.  Single-use per
    # token, but a voter may cast / change their vote multiple times
    # while their session lives.  Voter sessions are sweep'd on reset
    # along with captain sessions.

    @app.route("/api/veto/voter_tokens", methods=["POST"])
    @require_auth
    def veto_voter_tokens():
        """Admin-only.  Mint 10 voter tokens + auto-DM them via the bot
        (Layer 1A pattern extended to all players).  Idempotent — same
        protection as /api/veto/tokens (if any voter has claimed, return
        existing values rather than rotate everyone)."""
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                tokens_by_key = _veto.issue_voter_tokens(core._veto_session)
            except Exception as e:
                return _veto_error_response(e)
            # Snapshot roster pointers under lock for DM build
            team_a_slots = [(p.name, p.discord_id) for p in core._veto_session.team_a]
            team_b_slots = [(p.name, p.discord_id) for p in core._veto_session.team_b]
        # Build per-slot URLs.  Same share-base rules as captain tokens.
        lan_ip       = _config._lan_ip()
        public_ip    = core.public_ip or ""
        port         = _config.FLASK_PORT
        share_base   = (getattr(core, "public_share_url", "") or "").rstrip("/")
        def _urls(token: str) -> dict:
            urls = {"lan": f"http://{lan_ip}:{port}/voter?join={token}"}
            if share_base:
                urls["public"] = f"{share_base}/voter?join={token}"
            elif public_ip:
                urls["public"] = f"http://{public_ip}:{port}/voter?join={token}"
            return urls

        # Build response per slot + attempt DMs (fail-soft — operator can
        # always copy-paste from the response).
        out = {}
        dm_attempts = []
        for team_letter, slots in (("A", team_a_slots), ("B", team_b_slots)):
            for i, (name, discord_id) in enumerate(slots):
                key = f"{team_letter}:{i}"
                tok = tokens_by_key.get(key, "")
                urls = _urls(tok)
                dm_sent = False
                if discord_id and tok:
                    # Try DM via existing helper.  Fire-and-forget would
                    # race with the response — block briefly so the
                    # `dm_sent` flag reflects actual delivery.
                    try:
                        from . import discord_bot
                        if discord_bot.bot_status().get("connected"):
                            # Prefer public URL if available
                            link = urls.get("public") or urls.get("lan")
                            text = (f"You're voting in **{core._veto_session.team_a_name if team_letter == 'A' else core._veto_session.team_b_name}**.\n"
                                    f"Open this one-shot link to cast your captain vote: {link}\n"
                                    "*This link is personal — don't share it.*")
                            dm_sent = bool(discord_bot.bot_dm_user(discord_id, text))
                            dm_attempts.append((team_letter, i, name, dm_sent))
                    except Exception as exc:
                        core.log(f"[veto] voter DM to {name} failed: {exc}")
                out[key] = {
                    "team":      team_letter,
                    "voter_idx": i,
                    "player_name": name,
                    "discord_id":  discord_id,
                    "token":       tok,
                    "dm_sent":     dm_sent,
                    **urls,
                }
        if dm_attempts:
            sent = sum(1 for *_, ok in dm_attempts if ok)
            core.log(f"[veto] voter DMs sent: {sent}/{len(dm_attempts)}")
        _veto_broadcast()
        return jsonify({"voters": out})

    @app.route("/api/veto/voter_claim", methods=["POST"])
    def veto_voter_claim():
        """Public endpoint — token IS the credential.  Mints a voter
        session cookie scoped to (team, voter_idx).  Mirrors veto_claim
        for captains."""
        d = request.get_json() or {}
        token = str(d.get("token", "")).strip()
        if not token:
            return jsonify({"error": "missing token"}), 400
        caller_ip = request.remote_addr or ""
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                team, voter_idx = _veto.claim_voter(
                    core._veto_session, token, caller_id=caller_ip)
            except Exception as e:
                return _veto_error_response(e)
            session_token = _create_session(caller_ip, is_local=False, role="voter")
            sess = _get_session(session_token)
            if sess is not None:
                sess["voter_team"] = team
                sess["voter_idx"]  = voter_idx
        core.log(f"[veto] voter team {team} idx {voter_idx} claimed from {caller_ip}")
        _veto_broadcast()
        resp = jsonify({"ok": True, "team": team, "voter_idx": voter_idx})
        resp.set_cookie("session", session_token, httponly=True, samesite="Lax",
                        secure=_request_is_https())
        return resp

    @app.route("/voter")
    def voter_share_landing():
        """Voter-link landing page.  /voter?join=<token> claims server-side
        + sets the cookie + serves the SPA-bouncing HTML interstitial.
        Mirror of veto_share_landing for captains."""
        token = request.args.get("join", "").strip()
        if token:
            caller_ip = request.remote_addr or ""
            session_token = None
            team = None
            voter_idx = -1
            with core._veto_lock:
                try:
                    if core._veto_session is not None:
                        team, voter_idx = _veto.claim_voter(
                            core._veto_session, token, caller_id=caller_ip)
                except Exception as e:
                    core.log(f"[veto] voter share-link claim failed: {e}")
                    team = None
                if team is not None:
                    session_token = _create_session(
                        caller_ip, is_local=False, role="voter")
                    sess = _get_session(session_token)
                    if sess is not None:
                        sess["voter_team"] = team
                        sess["voter_idx"]  = voter_idx
            if team is not None and session_token is not None:
                _veto_broadcast()
                html = (
                    "<!doctype html><html><head>"
                    "<meta charset=utf-8>"
                    "<meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>Voting…</title>"
                    "<style>body{margin:0;display:flex;align-items:center;"
                    "justify-content:center;min-height:100vh;"
                    "background:#0d0d0f;color:#ccc;font-family:sans-serif;font-size:1.1rem}"
                    "</style></head>"
                    "<body><span>Opening voting page…</span>"
                    "<script>window.location.replace('/#veto');</script>"
                    "</body></html>"
                )
                resp = make_response(html, 200)
                resp.set_cookie("session", session_token, httponly=True,
                                samesite="Lax", secure=_request_is_https())
                resp.headers["Cache-Control"] = "no-store, private"
                resp.headers["Pragma"] = "no-cache"
                return resp
        return redirect("/#veto")

    @app.route("/api/veto/step", methods=["POST"])
    @require_auth        # captain-allowed
    def veto_step():
        d = request.get_json() or {}
        map_id = str(d.get("map_id", "")).strip()[:64]
        if not map_id:
            return jsonify({"error": "missing map_id"}), 400
        session = _current_session() or {}
        # Captains can only act on their own team's turn.  Admins can act
        # for either team (operator override / local testing).
        is_admin = session.get("is_local") or session.get("role") == "admin"
        captain_team = session.get("captain_team")
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            step = _veto.current_step(core._veto_session)
            if step is None:
                return jsonify({"error": "veto already complete"}), 400
            team_to_act = (str(d.get("team", "")) or captain_team or "").strip()
            if not is_admin and team_to_act != captain_team:
                return jsonify({"error": "captains can only act for their own team"}), 403
            if team_to_act != step.team:
                return jsonify({
                    "error": f"not team {team_to_act}'s turn — current step is team {step.team}",
                }), 400
            try:
                _veto.perform_step(core._veto_session, team_to_act, map_id)
                snap = _veto_snapshot()
            except Exception as e:
                return _veto_error_response(e)
        _veto_broadcast()
        # v0.11.0 Layer 1C — refresh the live Discord embed after every
        # ban/pick.  Fire-and-forget background thread; no-op when no
        # channel configured or bot offline.
        _refresh_live_veto_embed(reason="perform_step")
        return jsonify(snap)

    @app.route("/api/veto/ready", methods=["POST"])
    @require_auth
    def veto_ready():
        """Set the calling captain's ready flag.  v0.10.1.

        Body: {"ready": true|false}.  Team is INFERRED from the session
        cookie's captain_team so a captain can't toggle the other team's
        ready state.  Admins can pass an explicit `team` to set either.

        Returns the updated snapshot fragment {ready_a, ready_b, both_ready}.
        Broadcasts via SSE so both screens update live.

        If `core.config_get("veto_auto_launch_on_ready")` is True and both
        flags are now True after this set, the handler also fires the
        finale handoff inline (calling the same code path as
        POST /api/veto/finale).  Otherwise the admin must hit the button
        manually — which is the recommended default.
        """
        d = request.get_json() or {}
        ready_val = bool(d.get("ready", True))
        sess_role = request.session.get("role")  # type: ignore[attr-defined]
        # Resolve which team's flag we're setting.
        # - Admin/local: can pass "team": "A"|"B" explicitly (e.g. to ack on
        #   behalf of a captain who can't reach their phone).
        # - Captain role: team is locked to the team they claimed.
        if sess_role == "captain":
            team = request.session.get("captain_team", "")  # type: ignore[attr-defined]
            if not team:
                return jsonify({"error": "captain session has no team"}), 403
            # If body included a different team, REFUSE — that's a spoof
            # attempt and we want it visible in the logs.
            body_team = str(d.get("team", "")).upper()
            if body_team and body_team != team:
                core.log(f"[veto] ready: captain {team} tried to spoof team={body_team!r}")
                return jsonify({"error": "captains can only set their own team's ready"}), 403
        else:
            team = str(d.get("team", "")).upper()
            if team not in ("A", "B"):
                return jsonify({"error": "team must be 'A' or 'B'"}), 400

        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                _veto.set_ready(core._veto_session, team, ready_val)
            except Exception as e:
                return _veto_error_response(e)
            both = _veto.both_captains_ready(core._veto_session)
            ra, rb = core._veto_session.ready_a, core._veto_session.ready_b
        _veto_broadcast()
        core.log(f"[veto] team {team} ready={ready_val} (A={ra}, B={rb})")

        # Auto-launch path — config opt-in only.  Same lock + handoff as the
        # admin's /api/veto/finale button, but only when BOTH flags are now
        # True AND the operator turned the auto-launch toggle on.  Refuses
        # silently otherwise (operator hits the button manually).
        auto = bool(getattr(core, "veto_auto_launch_on_ready", False))
        if auto and both:
            core.log("[veto] both captains ready + auto-launch enabled → firing finale")
            # Build the finale call by reusing the existing handler body — we
            # don't want to duplicate the matchzy_loadmatch logic in two places.
            # Easiest is to internally redirect, but Flask's test_client mode
            # makes this awkward.  Instead we call the underlying veto.py +
            # file-write + RCON logic directly here.  Kept short — full
            # three-way outcome handling stays in /api/veto/finale for the
            # admin button.
            try:
                # v0.11.17 B3 — check-and-set _finale_firing under the lock.
                # If another thread already claimed the right to fire (admin
                # finale button, or another ready-toggle thread), we bail
                # cleanly without writing a second config file or firing a
                # second matchzy_loadmatch.
                with core._veto_lock:
                    if core._veto_session is None:
                        return jsonify({"team": team, "ready": ready_val,
                                        "ready_a": ra, "ready_b": rb, "both_ready": both,
                                        "auto_launch": "session vanished"})
                    if core._veto_session.state != "finale":
                        return jsonify({"team": team, "ready": ready_val,
                                        "ready_a": ra, "ready_b": rb, "both_ready": both,
                                        "auto_launch": f"wrong state {core._veto_session.state}"})
                    if core._finale_firing:
                        core.log("[veto] auto-launch: another thread is already "
                                 "firing finale — skipping duplicate")
                        return jsonify({"team": team, "ready": ready_val,
                                        "ready_a": ra, "ready_b": rb, "both_ready": both,
                                        "auto_launch": "already firing"})
                    core._finale_firing = True
                    cfg = _veto.build_matchzy_config(
                        core._veto_session,
                        cvar_overrides=getattr(core, "matchzy_cvars", None),
                    )
                disk_cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
                matchid = str(cfg.get("matchid", f"oblivion-veto-{int(time.time())}"))
                safe_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', matchid) + ".json"
                cfg_dir = os.path.join(core._csgo_dir(), "cfg", "MatchZy")
                os.makedirs(cfg_dir, exist_ok=True)
                target = os.path.join(cfg_dir, safe_filename)
                tmp = target + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(disk_cfg, f, indent=2, ensure_ascii=False)
                    f.flush()
                    try: os.fsync(f.fileno())
                    except OSError: pass
                os.replace(tmp, target)
                core.log(f"[veto] auto-launch wrote MatchZy config → {target}")
                if core.running:
                    try:
                        resp = core.rcon.execute(f"matchzy_loadmatch {safe_filename}")
                        core.log(f"[veto] auto-launch matchzy_loadmatch → {resp.strip()[:80]}")
                    except Exception as exc:
                        core.log(f"[veto] auto-launch RCON failed: {exc}")
                else:
                    core.log("[veto] auto-launch: server not running, config written only")
                with core._veto_lock:
                    if core._veto_session is not None and core._veto_session.state == "finale":
                        _veto.complete(core._veto_session)
                    core._finale_firing = False    # v0.11.17 B3 — release the guard
                _veto_broadcast()
            except Exception as exc:
                core.log(f"[veto] auto-launch failed: {exc}")
                # Release the guard on any failure so the admin Finale
                # button (or a future retry) can take over.  Otherwise a
                # stuck `_finale_firing = True` from a crashed handler
                # would lock everyone out until app restart.
                with core._veto_lock:
                    core._finale_firing = False

        return jsonify({
            "team": team, "ready": ready_val,
            "ready_a": ra, "ready_b": rb, "both_ready": both,
        })

    @app.route("/api/veto/finale", methods=["POST"])
    @require_auth
    def veto_finale():
        """Generate the MatchZy config, write it to disk under the server's
        `csgo/cfg/MatchZy/` directory, and issue `matchzy_loadmatch <file>`
        via RCON so MatchZy takes over the series.

        Three-way outcome in the response under `matchzy`:
          * `written_to`: absolute path of the JSON we wrote (always set
            on a successful write — operator can inspect it).
          * `loaded`: True if the RCON call returned without raising.
          * `error`:  if anything went sideways AFTER a successful file
            write (RCON down, MatchZy plugin not loaded, server not
            running), the operator gets a 200 with the error here — the
            veto state still transitions to `complete` so the SPA isn't
            stuck, and the operator can copy the file path + re-issue
            `matchzy_loadmatch` from the RCON console manually.

        Caller chooses whether to actually fire the RCON via `load_match`
        (default True).  `{load_match: false}` is useful for previewing the
        config in dev or when the operator wants to hand the JSON to a
        different match-host workflow.
        """
        d = request.get_json() or {}
        load_match = bool(d.get("load_match", True))
        # v0.10.2: optional `force` override so the operator can power
        # through the mode pre-flight if they know the server is set up
        # correctly (e.g. running 5v5 with vanilla settings + MatchZy
        # plugin manually loaded).  Default False.
        force = bool(d.get("force", False))
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            # Guard against a second /api/veto/finale after the session has
            # already transitioned to `complete` — without this the handler
            # would re-write the file (fine), re-issue the RCON call (also
            # mostly fine — MatchZy ignores reload of the same matchid),
            # but then crash with InvalidVetoTransition inside the final
            # `complete()` call → 500.  Clean rejection is friendlier.
            if core._veto_session.state == "complete":
                return jsonify({
                    "error": "session already complete — call /api/veto/reset "
                             "before starting a new finale handoff."
                }), 400
            # v0.11.17 B3 — check-and-set the finale-firing guard.  If the
            # captain-ready auto-launch path (or another concurrent click of
            # this same button) is already in flight, refuse cleanly so we
            # don't write a second config + fire a second matchzy_loadmatch.
            if core._finale_firing:
                return jsonify({
                    "error": "another finale handoff is already in flight — "
                             "wait a moment, then check the SPA state."
                }), 409
            core._finale_firing = True
            try:
                cfg = _veto.build_matchzy_config(
                        core._veto_session,
                        cvar_overrides=getattr(core, "matchzy_cvars", None),
                    )
            except Exception as e:
                # v0.11.17 B3 — release the guard on any failure path so
                # the operator can retry (the admin button or auto-launch
                # would otherwise stay locked out until app restart).
                core._finale_firing = False
                return _veto_error_response(e)

        # v0.10.2 — Mode pre-flight.  matchzy_loadmatch only works on
        # MatchZy-managed modes (Practice + the team-match modes 3v3/4v4/5v5
        # use the same plugin bundle and the same RCON command).  If the
        # operator's server is currently on Aim 1v1, Warcraft, Zombie
        # Escape, etc., the loadmatch RCON would either succeed-but-
        # play-wrong-ruleset or be silently ignored by the plugin.
        # Refuse with 409 + the wanted/got mode so the SPA can offer a
        # one-click switch.  `force=True` bypasses for the rare "operator
        # knows what they're doing" case.
        _MATCHZY_MODES = {"Practice", "3v3", "4v4", "5v5", "Competitive"}
        current_mode = (core.current_mode or "").strip()
        if load_match and not force and current_mode not in _MATCHZY_MODES:
            # v0.11.17 B3 — operator's going to switch mode + retry, so
            # release the guard.
            with core._veto_lock:
                core._finale_firing = False
            return jsonify({
                "error": (
                    f"server is on mode {current_mode or '(unknown)'} which "
                    f"isn't a MatchZy mode — matchzy_loadmatch will misbehave. "
                    f"Switch the server to 5v5 (or 3v3 / 4v4 / Practice / "
                    f"Competitive) first, OR retry with force=true to skip "
                    f"this check."
                ),
                "precheck": {
                    "ok": False,
                    "current_mode": current_mode,
                    "expected_one_of": sorted(_MATCHZY_MODES),
                    "server_running":  bool(core.running),
                },
            }), 409
        # Snapshot cfg for the response, then prepare the on-disk variant.
        # MatchZy doesn't know about `_oblivion_meta` — strip it from the
        # written file so MatchZy's schema validator doesn't complain
        # (the field is purely for our SPA's audit trail in the response).
        disk_cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
        matchid = str(cfg.get("matchid", f"oblivion-veto-{int(time.time())}"))
        # Filesystem-safe filename — matchid is already URL-safe (only
        # the int timestamp suffix varies) but defend against an operator
        # ever passing custom matchids through.
        safe_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', matchid) + ".json"

        matchzy_result: dict = {"loaded": False}
        write_error: str | None = None
        try:
            cfg_dir = os.path.join(core._csgo_dir(), "cfg", "MatchZy")
            os.makedirs(cfg_dir, exist_ok=True)
            target = os.path.join(cfg_dir, safe_filename)
            # Atomic write: tmp + os.replace.  Avoids MatchZy reading a
            # half-written file in the gap between open() and the final
            # flush — vanishingly unlikely in practice (the load is RCON-
            # triggered AFTER this returns) but cheap insurance.
            tmp = target + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(disk_cfg, f, indent=2, ensure_ascii=False)
                f.flush()
                try: os.fsync(f.fileno())
                except OSError: pass   # network drives sometimes refuse fsync
            os.replace(tmp, target)
            matchzy_result["written_to"] = target
            core.log(f"[veto] wrote MatchZy config → {target}")
        except Exception as exc:
            write_error = f"failed to write match config: {type(exc).__name__}: {exc}"
            core.log(f"[veto] {write_error}")
        # If the file write itself failed, stop here with 500 — the operator
        # needs to know they can't proceed.  Don't complete the session so
        # they can retry after fixing the disk issue.
        if write_error:
            # v0.11.17 B3 — operator may fix disk + retry, so release guard.
            with core._veto_lock:
                core._finale_firing = False
            return jsonify({"error": write_error}), 500

        # File write OK — try the RCON handoff if requested and possible.
        if load_match:
            if not core.running:
                matchzy_result["error"] = (
                    "server not running — wrote config but skipped "
                    "matchzy_loadmatch; start the server and run "
                    f"`matchzy_loadmatch {safe_filename}` manually."
                )
                core.log(f"[veto] {matchzy_result['error']}")
            else:
                try:
                    # Single attempt — RCON has retry logic but the operator
                    # is watching this in real time; better to surface a
                    # quick failure than wait 30 s through retries.
                    resp = core.rcon.execute(f"matchzy_loadmatch {safe_filename}")
                    matchzy_result["loaded"] = True
                    matchzy_result["rcon_response"] = resp.strip()[:300]
                    core.log(f"[veto] matchzy_loadmatch {safe_filename} → "
                             f"{resp.strip()[:80]}")
                except Exception as exc:
                    matchzy_result["error"] = (
                        f"matchzy_loadmatch RCON call failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    core.log(f"[veto] {matchzy_result['error']}")

        # Transition the session to `complete` regardless — the file is on
        # disk, the operator can recover from any RCON hiccup manually.
        # v0.10.2: also archive a snapshot to oblivion_matches.json so we
        # have a "last 10 matches" reference + rematch base if the app
        # restarts before the operator hit Rematch.
        with core._veto_lock:
            if core._veto_session is not None:
                snapshot = _veto.archive_to_history(core._veto_session)
                _veto.complete(core._veto_session)
            else:
                snapshot = None
            # v0.11.17 B3 — release the finale-firing guard now that
            # we've reached the success path.  Clearing on the way out
            # only (not on RCON failure earlier) is intentional: the
            # session is now `complete`, so a duplicate finale call from
            # auto-launch will hit the `state == "complete"` guard and
            # bail cleanly — and clearing here lets a Reset+new session
            # fire a new finale eventually.
            core._finale_firing = False
        # v0.12.1 — start the match-events poller now that the match is
        # live.  The poller's internal precondition check re-reads the
        # toggle every tick so the operator can flip it mid-match without
        # restart.  Fail-soft: start() catches its own errors so a poller
        # init failure NEVER blocks the finale response.
        try:
            from . import match_events
            match_events.start(core)
        except Exception as exc:
            core.log(f"[match_events] start failed: {exc}")
        if snapshot is not None:
            _save_to_match_history(snapshot)
            # v0.10.2 — Discord webhook (operator-configured).  Fire-and-
            # forget on a background thread — webhook POST shouldn't block
            # the operator's finale click.
            if core.discord_webhook_url:
                threading.Thread(
                    target=_post_discord_finale_webhook,
                    args=(snapshot, matchzy_result),
                    daemon=True,
                ).start()
        # v0.11.0 Layer 1C — flip the live embed to "MATCH LOCKED IN"
        _refresh_live_veto_embed(reason="finale")
        _veto_broadcast()
        # v0.10.2 — also return the precheck snapshot in the success path
        # so the SPA can show "✓ mode was 5v5" alongside the matchzy result.
        matchzy_result["precheck"] = {
            "ok": True,
            "current_mode": current_mode,
            "server_running": bool(core.running),
            "forced": force,
        }
        return jsonify({"ok": True, "config": cfg, "matchzy": matchzy_result})

    @app.route("/api/veto/history")
    @require_auth
    def veto_history():
        """v0.10.2 — Last N completed matches (newest last)."""
        return jsonify({"matches": _load_match_history()})

    # v0.11.0 polish — Spectator URL: read-only caster/observer link
    @app.route("/api/veto/spectator", methods=["POST"])
    @require_auth
    def veto_spectator_issue():
        """Issue (or return) the read-only spectator token + URLs.
        Admin-only because giving someone the URL = giving them perm to
        watch every map in the session as it lands.  Rotate by including
        {rotate:true} in the body."""
        d = request.get_json(silent=True) or {}
        rotate = bool(d.get("rotate"))
        with core._veto_lock:
            sess = core._veto_session
            if sess is None:
                return jsonify({"error": "no active veto session"}), 404
            token = (_veto.rotate_spectator_token(sess) if rotate
                     else _veto.issue_spectator_token(sess))
        lan_ip     = _config._lan_ip()
        port       = _config.FLASK_PORT
        share_base = (getattr(core, "public_share_url", "") or "").rstrip("/")
        urls = {"lan": f"http://{lan_ip}:{port}/spectate?token={token}"}
        if share_base:
            urls["public"] = f"{share_base}/spectate?token={token}"
        # SSE on the SPA broadcast so the operator's own UI updates.
        _veto_broadcast()
        return jsonify({"token": token, "urls": urls, "rotated": rotate})

    # v0.16.14 / task #170 — Spectator SSE stream.  Same shape as
    # /api/veto/stream but token-gated and sanitized.  HTML page prefers
    # this over polling; polling stays as a 5s fallback in case SSE is
    # blocked by a reverse proxy / corporate firewall.
    @app.route("/api/veto/spectator/stream")
    def veto_spectator_stream():
        token = request.args.get("token", "").strip()
        with core._veto_lock:
            sess = core._veto_session
            if sess is None or not token:
                return jsonify({"error": "no session"}), 404
            if not secrets.compare_digest(sess.spectator_token or "", token):
                return jsonify({"error": "invalid spectator token"}), 401
            initial = _veto.build_spectator_snapshot(sess)
        q: queue.Queue = queue.Queue(maxsize=128)
        try:
            q.put_nowait("data: " + __import__("json").dumps(initial) + "\n\n")
        except Exception:
            pass
        with _veto_spec_subs_lock:
            _veto_spec_subs.append(q)
        def gen():
            try:
                while True:
                    try: yield q.get(timeout=25)
                    except queue.Empty: yield ": keepalive\n\n"
            finally:
                with _veto_spec_subs_lock:
                    if q in _veto_spec_subs: _veto_spec_subs.remove(q)
        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    @app.route("/api/veto/spectator/state")
    def veto_spectator_state():
        """Token-gated, sanitized snapshot.  No auth cookie required —
        the token IS the auth (spectators won't have admin/guest PINs).
        Returns 401 on bad/expired token to make the front-end's empty
        state clean."""
        token = request.args.get("token", "").strip()
        with core._veto_lock:
            sess = core._veto_session
            if sess is None or not token:
                return jsonify({"error": "no session"}), 404
            if not secrets.compare_digest(sess.spectator_token or "", token):
                return jsonify({"error": "invalid spectator token"}), 401
            snap = _veto.build_spectator_snapshot(sess)
        return jsonify(snap)

    @app.route("/spectate")
    def veto_spectate_page():
        """Tiny standalone HTML page that polls
        /api/veto/spectator/state?token=… every 3s and renders the
        sanitized snapshot.  Deliberately NOT the full SPA — casters
        get a fast-loading, distraction-free view that works in OBS
        browser sources without needing the admin login flow."""
        # Token is validated server-side on each state poll; here we
        # just embed it into the page so the JS can use it.
        token = request.args.get("token", "").strip()
        # esc the token defensively even though token_urlsafe is safe.
        safe_token = re.sub(r'[^A-Za-z0-9_\-]', '', token)[:64]
        html = SPECTATOR_HTML.replace("__TOKEN__", safe_token)
        return Response(html, mimetype="text/html")

    @app.route("/api/veto/rematch", methods=["POST"])
    @require_auth
    def veto_rematch():
        """v0.10.2: rematch with same teams.  After a finished BO the
        operator hits this to start a new series with the same 10 players
        + the same captains.  Saves retyping 10 names + re-running the
        captain vote.

        Legal only from `complete` state.  Captains keep their election
        but get fresh single-use tokens (the operator must call
        /api/veto/tokens after this to mint them).  Captain ready flags
        reset (each team must ready up again for the new series).

        Optional body fields:
          mode      — BO1 / BO3 / BO5 (defaults to current session's mode)
          map_pool  — 7-element list (defaults to current pool)
        """
        d = request.get_json() or {}
        mode     = d.get("mode")  # None = keep current
        map_pool = d.get("map_pool")
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                _veto.rematch(core._veto_session, mode=mode, map_pool=map_pool)
                snap = _veto_snapshot()
            except Exception as e:
                return _veto_error_response(e)
        _veto_broadcast()
        core.log(f"[veto] rematch — same teams, fresh BO{(mode or core._veto_session.mode)[2:]}")
        return jsonify(snap)

    @app.route("/api/veto/reset", methods=["POST"])
    @require_auth
    def veto_reset():
        # v0.11.26 — sweep _sessions while still holding _veto_lock.  The
        # captain-claim path (above) now mints _create_session under the
        # same _veto_lock; this nesting closes the race window where a
        # concurrent claim landed AFTER the sweep snapshot but BEFORE the
        # captain saw the reset, producing a zombie captain session that
        # auto-authenticated against the next session's team A.
        # Audit finding #1.
        with core._veto_lock:
            if core._veto_session is not None:
                _veto.reset(core._veto_session)
            core._veto_session = None
            # v0.11.17 B3 — reset clears the finale-firing guard so the
            # next session's finale can fire normally.  Belt-and-braces:
            # the success path of veto_finale already clears it, but a
            # crashed handler that exited before reaching that point
            # might have left it stuck True.
            core._finale_firing = False
            # v0.11.20 — invalidate every captain HTTP session.  Without
            # this, captains who claimed tokens for the previous session
            # keep their cookie with captain_team set and appear auth'd
            # as captain of a team whose tokens are already dead.
            with _sessions_lock:
                # v0.12.3 / task #135 — also sweep voter sessions on reset.
                # Same staleness reason: a voter cookie bound to the
                # previous session's roster slot would silently
                # auto-authenticate into the next session's voting stage.
                dropped = [tok for tok, s in _sessions.items()
                           if s.get("role") in ("captain", "voter")]
                for tok in dropped:
                    _sessions.pop(tok, None)
        if dropped:
            core.log(f"[veto] invalidated {len(dropped)} captain/voter session(s)")
        # v0.12.1 — stop the match-events poller (idempotent no-op if not
        # running).  Resetting mid-match must stop the round-summary spam.
        try:
            from . import match_events
            match_events.stop()
        except Exception as exc:
            core.log(f"[match_events] stop failed: {exc}")
        _veto_broadcast()
        core.log("[veto] session reset")
        return jsonify({"ok": True, "state": "idle"})

    # ── Captain-link QR codes (v0.10.0 Day 4) ─────────────────────────────────
    # Returns an SVG QR code for a captain's join URL.  The SPA's Links stage
    # embeds <img src="/api/veto/qr?token=…&kind=lan"> alongside each Copy
    # button so captains on their phones can scan instead of typing.
    #
    # Why server-side: pure-Python segno avoids bundling ~10 KB of QR JS in
    # static/ and keeps the SVG cacheable (the URL is stable for the life of
    # the token).  Auth: admin/local only — captains have already received
    # their URL via the operator, they don't need to request QR codes.
    #
    # Token validation: the endpoint refuses unknown tokens so it can't be
    # used as a generic QR proxy by anyone who somehow got a session cookie.
    @app.route("/api/veto/qr")
    @require_auth
    def veto_qr():
        # Import inside the handler so the rest of the app keeps working
        # even if segno is somehow not present in the bundle.  Surface a
        # *useful* error to the SPA — without this catch, ImportError bubbles
        # up as a generic Flask 500 with no body and the operator sees the
        # broken-image icon with zero diagnostic info (this exact failure
        # mode bit us between v0.10.0 and v0.10.0.1 when --hidden-import
        # segno only grabbed the top-level module, not its submodules).
        try:
            import segno
        except ImportError as e:
            core.log(f"[veto] QR endpoint hit but segno missing: {e}")
            return jsonify({
                "error": f"QR generator not available in this build: {e}.  "
                         "Rebuild with `--collect-all segno` in build.bat."
            }), 500
        from io import BytesIO
        token = request.args.get("token", "").strip()
        kind  = request.args.get("kind", "lan").strip().lower()
        if not token:
            return jsonify({"error": "token required"}), 400
        if kind not in ("lan", "public"):
            return jsonify({"error": "kind must be 'lan' or 'public'"}), 400
        with core._veto_lock:
            s = core._veto_session
            if s is None:
                return jsonify({"error": "no active veto session"}), 400
            # Match against currently-issued tokens.  Single-use semantics:
            # the QR endpoint is OK to call even after a captain has claimed
            # — the URL stays the same and the operator may want to re-share.
            valid = any(t.value == token for t in (s.tokens or {}).values())
            if not valid:
                return jsonify({"error": "unknown token"}), 404
        # Build the URL the same way veto_tokens / veto_revoke_token do.
        # v0.10.1: kind=public prefers the operator-set public_share_url
        # (e.g. Cloudflare tunnel) over the raw public IP + port.
        port = _config.FLASK_PORT
        if kind == "lan":
            url = f"http://{_config._lan_ip()}:{port}/veto?join={token}"
        else:
            share_base = (getattr(core, "public_share_url", "") or "").rstrip("/")
            if share_base:
                url = f"{share_base}/veto?join={token}"
            elif core.public_ip:
                url = f"http://{core.public_ip}:{port}/veto?join={token}"
            else:
                return jsonify({
                    "error": "no public URL configured — set public_share_url "
                             "in Config (Cloudflare tunnel URL) or wait for "
                             "public IP detection."
                }), 400
        # error='M' gives ~15% damage tolerance — fine for a phone scan at
        # close range; lower error level would shrink the QR but a wet/glare
        # phone screen is the realistic enemy here.  scale=8 (~200 px) is the
        # sweet spot for both desktop popovers and printable share cards.
        try:
            qr = segno.make(url, error='m')
        except Exception as e:
            return jsonify({"error": f"QR encode failed: {e}"}), 500
        buf = BytesIO()
        # SVG output: vector, scales perfectly, no Pillow dep.  xmldecl=False
        # so it embeds cleanly into an <img src>.
        qr.save(buf, kind='svg', scale=8, border=2, xmldecl=False, svgns=True)
        from flask import Response
        return Response(
            buf.getvalue(),
            mimetype='image/svg+xml',
            # Cache aggressively — the token+kind combo maps to a stable URL
            # for the life of the veto session.  Token rotation (revoke +
            # reissue) produces a different ?token= value, so cache key
            # differs naturally.  No-store on errors via the early returns
            # above keeps stale-error responses out of the cache.
            headers={"Cache-Control": "private, max-age=300"},
        )

    # ── Log SSE ────────────────────────────────────────────────────────────────

    @app.route("/api/log/history")
    @require_auth
    def log_history():
        return jsonify(core.get_log())

    # ── v0.11.4 — Diagnostic snapshot ─────────────────────────────────────
    # Single endpoint that returns one text blob covering everything an
    # operator would need to paste into a Discord support channel.  Local
    # admin only — contains IPs, deployed plugin names, redacted config.
    # SPA button copies the result to clipboard.

    # ─── Demo browser (v0.16.3 / task #171) ──────────────────────────────────
    # Scan the CS2 server's csgo/ + MatchZy demo dir + counterstrikesharp
    # demo output for .dem files.  List with timestamps + sizes so the
    # operator can see "last 10 matches' demos" without leaving the app.
    # Download routes the file through Flask so the operator's browser
    # gets a Save-As prompt instead of needing to know the on-disk path.

    @app.route("/api/demos")
    @require_auth
    @require_local
    def api_demos_list():
        import time as _time
        csgo = core._csgo_dir() if core.server_dir else None
        roots: list[tuple[str, str]] = []   # (label, abs_path)
        if csgo and os.path.isdir(csgo):
            roots.append(("csgo",      csgo))
            roots.append(("matchzy",
                          os.path.join(csgo, "addons", "counterstrikesharp",
                                       "plugins", "MatchZy", "demos")))
            roots.append(("css_logs",  os.path.join(csgo, "addons",
                                                    "counterstrikesharp", "demos")))
            roots.append(("cfg_mz",    os.path.join(csgo, "cfg", "MatchZy")))
        demos: list[dict] = []
        seen_paths = set()
        for label, root in roots:
            if not os.path.isdir(root):
                continue
            try:
                walker = os.walk(root)
            except OSError:
                continue
            for cwd, _dirs, files in walker:
                for fn in files:
                    if not fn.lower().endswith(".dem"):
                        continue
                    p = os.path.join(cwd, fn)
                    if p in seen_paths:
                        continue
                    seen_paths.add(p)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    demos.append({
                        "name":      fn,
                        # rel_path is what /api/demos/download uses to find
                        # the file again.  Encoded as label + slash + path
                        # relative to that root so the download endpoint
                        # can resolve back to the actual csgo/... path
                        # without trusting the operator with absolute paths.
                        "rel_path":  f"{label}/{os.path.relpath(p, root)}",
                        "size":      st.st_size,
                        "mtime":     int(st.st_mtime),
                        "mtime_iso": _time.strftime("%Y-%m-%d %H:%M:%S",
                                                     _time.localtime(st.st_mtime)),
                        "source":    label,
                    })
        # Newest first.
        demos.sort(key=lambda d: d["mtime"], reverse=True)
        return jsonify({"demos": demos, "scanned_roots": [r[0] for r in roots]})

    @app.route("/api/demos/download")
    @require_auth
    @require_local
    def api_demos_download():
        from flask import send_file, abort
        rel = (request.args.get("path") or "").strip()
        if not rel or "/" not in rel:
            return jsonify({"error": "path required as <label>/<relative>"}), 400
        label, _slash, rel_inside = rel.partition("/")
        # Validate label BEFORE checking csgo existence so callers can
        # distinguish "bad request" from "server install missing" cleanly.
        valid_labels = {"csgo", "matchzy", "css_logs", "cfg_mz"}
        if label not in valid_labels:
            return jsonify({"error": f"unknown label {label!r}"}), 400
        csgo = core._csgo_dir() if core.server_dir else None
        if not csgo or not os.path.isdir(csgo):
            return jsonify({"error": "csgo/ not found"}), 404
        root_map = {
            "csgo":     csgo,
            "matchzy":  os.path.join(csgo, "addons", "counterstrikesharp",
                                      "plugins", "MatchZy", "demos"),
            "css_logs": os.path.join(csgo, "addons", "counterstrikesharp",
                                      "demos"),
            "cfg_mz":   os.path.join(csgo, "cfg", "MatchZy"),
        }
        root = root_map[label]
        target = os.path.realpath(os.path.join(root, rel_inside))
        root_real = os.path.realpath(root)
        # Path traversal guard — target must resolve inside its labelled root.
        if os.path.commonpath([target, root_real]) != root_real:
            return jsonify({"error": "path escapes root"}), 400
        if not os.path.isfile(target):
            return jsonify({"error": "not found"}), 404
        if not target.lower().endswith(".dem"):
            return jsonify({"error": "only .dem files served"}), 400
        return send_file(target, as_attachment=True)

    # ─── Tournament templates (v0.16.3 / task #169) ───────────────────────────
    # Named bundles of mode + map + pack + Discord channels + team profile IDs.
    # apply() walks the persisted payload and routes each piece through the
    # endpoint that would have handled it interactively, so behaviour stays in
    # one place and the same safety rails fire (preflight / atomic deploy).

    @app.route("/api/templates")
    @require_auth
    def api_templates_list():
        from . import template_store
        return jsonify({"templates": template_store.list_templates()})

    @app.route("/api/templates/save", methods=["POST"])
    @require_auth
    @require_local
    def api_templates_save():
        from . import template_store
        d = request.get_json(silent=True) or {}
        try:
            saved = template_store.save_template(
                template_id=(d.get("id") or None),
                name=d.get("name") or "",
                payload=d.get("payload") or {},
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "template": saved})

    @app.route("/api/templates/delete", methods=["POST"])
    @require_auth
    @require_local
    def api_templates_delete():
        from . import template_store
        d = request.get_json(silent=True) or {}
        tid = (d.get("id") or "").strip()
        if not tid:
            return jsonify({"error": "id is required"}), 400
        if not template_store.delete_template(tid):
            return jsonify({"error": "template not found"}), 404
        return jsonify({"ok": True, "id": tid})

    @app.route("/api/templates/apply", methods=["POST"])
    @require_auth
    @require_local
    def api_templates_apply():
        """Apply a saved template:
            1. Backup config (so the whole batch is rollbackable).
            2. Persist Discord channel + behaviour toggles via core.config_set.
            3. Apply the plugin pack (mode + map + plugins) via the same
               code path as /api/plugins/apply_pack — preflight + lock.

        Returns {ok, applied: [...steps...]}.
        Team profile IDs are surfaced in the response; the SPA loads them
        into the local roster buffer client-side (no server-side roster state).
        """
        from . import template_store, core as _core
        d   = request.get_json(silent=True) or {}
        tid = (d.get("id") or "").strip()
        if not tid:
            return jsonify({"error": "id is required"}), 400
        tpl = template_store.get_template(tid)
        if not tpl:
            return jsonify({"error": "template not found"}), 404
        payload = tpl.get("payload") or {}
        applied: list[str] = []

        # 1. Snapshot so the whole batch is reversible if something goes wrong.
        core.backup_config(reason=f"pre-template-{tpl.get('name', '?')[:20]}")

        # 2. Save the Discord-side fields directly onto AppCore + persist.
        discord_fields = (
            "discord_veto_channel_id",
            "discord_team_a_voice_channel_id",
            "discord_team_b_voice_channel_id",
            "discord_auto_move_on_distribute_enabled",
            "discord_round_summaries_enabled",
        )
        wrote_any = False
        for f in discord_fields:
            if f in payload:
                v = payload[f]
                # Booleans must stay booleans; channel IDs are strings (digits).
                if "enabled" in f:
                    v = bool(v)
                else:
                    v = (v or "").strip() if isinstance(v, str) else ""
                setattr(core, f, v)
                wrote_any = True
        if wrote_any:
            core.save_config()
            applied.append("discord_config")

        # 3. Apply the pack (if any).  Preflight covers running / dl / veto state.
        pack_id = (payload.get("pack_id") or "").strip()
        if pack_id:
            from .core import _PLUGIN_PACKS
            pack = next((p for p in _PLUGIN_PACKS if p["id"] == pack_id), None)
            if not pack:
                return jsonify({"error": f"pack {pack_id!r} not found",
                                "applied": applied}), 400
            ok, status, err = _plugin_action_preflight()
            if not ok:
                # We may have already written discord fields above; the operator
                # can roll back via Settings → Tools → Restore backup.
                return jsonify({**err, "applied": applied,
                                "note": "Discord settings already applied; "
                                        "rerun once preflight passes."}), status
            # Honour the template's explicit map if set; else default_map.
            target_map = (payload.get("map") or "").strip() or \
                          (pack.get("default_map") or None)
            if core.running:
                # Live restart path mirrors apply_pack's running branch.
                tm, is_workshop = _resolve_live_swap_map(
                    pack["mode"], preferred=target_map or "")
                core.change_map(tm, pack["mode"], is_workshop=is_workshop,
                                caller=f"template/{tpl.get('name', '?')[:20]}")
                applied.append("pack_apply_restart")
            else:
                result = core.set_offline_mode_and_deploy(
                    pack["mode"],
                    caller=f"template/{tpl.get('name', '?')[:20]}",
                    map_name=target_map,
                )
                if not result.get("ok"):
                    return jsonify({"error": result.get("error") or "deploy failed",
                                    "applied": applied}), 500
                applied.append("pack_apply_offline")
        elif (payload.get("mode") or "").strip() and not core.running:
            # No pack but explicit mode + maybe map → stage it without plugins.
            mode = payload["mode"].strip()
            tm   = (payload.get("map") or "").strip() or None
            result = core.set_offline_mode_and_deploy(
                mode,
                caller=f"template/{tpl.get('name', '?')[:20]}",
                map_name=tm,
            )
            if result.get("ok"):
                applied.append("mode_only_apply")

        return jsonify({
            "ok":          True,
            "template_id": tid,
            "name":        tpl.get("name"),
            "applied":     applied,
            "team_a_id":   payload.get("team_a_id") or "",
            "team_b_id":   payload.get("team_b_id") or "",
        })

    # ─── Persistent team profiles (v0.16.1 / task #160) ───────────────────────
    @app.route("/api/teams")
    @require_auth
    def api_teams_list():
        from . import team_profiles
        return jsonify({"teams": team_profiles.list_teams()})

    @app.route("/api/teams/save", methods=["POST"])
    @require_auth
    @require_local
    def api_teams_save():
        from . import team_profiles
        d = request.get_json(silent=True) or {}
        try:
            saved = team_profiles.save_team(
                team_id=(d.get("id") or None),
                name=d.get("name") or "",
                tag=d.get("tag") or "",
                players=d.get("players") or [],
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "team": saved})

    @app.route("/api/teams/delete", methods=["POST"])
    @require_auth
    @require_local
    def api_teams_delete():
        from . import team_profiles
        d = request.get_json(silent=True) or {}
        team_id = (d.get("id") or "").strip()
        if not team_id:
            return jsonify({"error": "id is required"}), 400
        removed = team_profiles.delete_team(team_id)
        if not removed:
            return jsonify({"error": "team not found"}), 404
        return jsonify({"ok": True, "id": team_id})

    # ─── Tournament readiness pre-flight (v0.16.2 / task #168) ────────────────
    # Single endpoint that audits ~10 things every tournament needs working.
    # Operator hits ✓ Pre-flight before going live; gets a green/red dashboard
    # that turns "something's broken — paste a snapshot" into "let me check
    # before tonight."  Backs the Status tab's Pre-flight button.

    @app.route("/api/readiness")
    @require_auth
    @require_local
    def api_readiness():
        import shutil as _shutil
        from . import registry_client as _reg
        checks: list[dict] = []

        def add(key, status, label, detail=""):
            # status: "ok" | "warn" | "fail" | "info"
            checks.append({"key": key, "status": status,
                            "label": label, "detail": detail})

        # 1. CS2 install present
        csgo_dir = core._csgo_dir() if core.server_dir else None
        if csgo_dir and os.path.isdir(csgo_dir):
            add("csgo", "ok", "CS2 server installed",
                detail=csgo_dir)
        else:
            add("csgo", "fail", "CS2 server not installed",
                detail="Open Config → Server Installation → Install / Reinstall")

        # 2. MetaMod + CSS runtime present
        try:
            mm = core._metamod_installed()
            css = core._css_installed()
        except Exception:
            mm = False; css = False
        if mm and css:
            add("runtime", "ok", "MetaMod + CSS installed")
        elif mm or css:
            missing = " + ".join(x for x, v in
                                  (("MetaMod", mm), ("CSS", css)) if not v)
            add("runtime", "warn", f"Plugin runtime partial — {missing} missing",
                detail="Plugins tab → Set up plugin runtime")
        else:
            add("runtime", "fail",
                "Plugin runtime missing (MetaMod + CSS)",
                detail="Plugins tab → Set up plugin runtime")

        # 3. Current mode's plugins deployed cleanly
        try:
            manifest = core._load_plugin_manifest() or {}
            deployed = manifest.get("plugins", []) or []
            cur_mode = core.current_mode or ""
            expected = _MODE_PLUGIN_NAMES.get(cur_mode, [])
            missing_plugins = sorted(set(expected) - set(deployed))
            if cur_mode and missing_plugins:
                add("plugins", "fail",
                    f"Mode {cur_mode!r} missing plugins",
                    detail=f"{', '.join(missing_plugins)} not deployed — re-apply the mode")
            elif cur_mode and expected:
                add("plugins", "ok",
                    f"Mode {cur_mode!r}: {', '.join(deployed) or '(vanilla)'}")
            else:
                add("plugins", "info",
                    f"Mode {cur_mode or '(none)'}: vanilla — no plugins to verify")
        except Exception as exc:
            add("plugins", "warn", "Could not verify plugins",
                detail=str(exc))

        # 4. Disk space at csgo/ (CS2 demos + workshop maps eat space)
        try:
            check_dir = csgo_dir if (csgo_dir and os.path.isdir(csgo_dir)) \
                                  else os.path.dirname(_CONFIG_FILE)
            free = _shutil.disk_usage(check_dir).free
            free_gb = free / (1024 ** 3)
            if free_gb >= 5:
                add("disk", "ok", f"Disk space ok ({free_gb:.1f} GB free)")
            elif free_gb >= 1:
                add("disk", "warn", f"Disk space low ({free_gb:.1f} GB free)",
                    detail="<5 GB free — workshop downloads may stall")
            else:
                add("disk", "fail", f"Disk almost full ({free_gb:.2f} GB free)",
                    detail="Free up space before going live")
        except Exception as exc:
            add("disk", "warn", "Could not check disk", detail=str(exc))

        # 5. PIN is set + non-default
        if not core.admin_pin:
            add("pin", "fail", "Admin PIN not set",
                detail="Config → Security → Admin PIN")
        elif core.admin_pin == "1234":
            add("pin", "warn", "Admin PIN is still the default 1234",
                detail="Rotate it before exposing to the internet")
        else:
            add("pin", "ok", "Admin PIN set")

        # 6. Discord bot status (optional but warn if configured-but-broken)
        if not core.discord_bot_token:
            add("discord", "info", "Discord bot not configured (optional)",
                detail="Skip if you don't need DM captain links or live embeds")
        else:
            try:
                bot_status = _discord_bot_status()
                if bot_status.get("connected"):
                    add("discord", "ok",
                        f"Discord bot connected as {bot_status.get('user') or '?'}")
                else:
                    add("discord", "fail",
                        "Discord bot configured but not connected",
                        detail="Check token + guild ID in Config → Discord")
            except Exception as exc:
                add("discord", "warn",
                    "Discord bot status unknown", detail=str(exc))

        # 7. Voice channel reachable (only relevant if VCs configured)
        try:
            vc_id = (core.discord_voice_channel_id or "").strip()
            gid   = (core.discord_guild_id or "").strip()
            if vc_id and gid:
                # Cheap reachable test via Discord lib if available.
                try:
                    from . import discord_bot as _db
                    info = _db.bot_voice_channel_info(int(gid), int(vc_id),
                                                       timeout=1.5)
                except Exception:
                    info = None
                if info and info.get("name"):
                    add("vc", "ok",
                        f"Default VC reachable: {info['name']}")
                else:
                    add("vc", "warn",
                        "Default VC configured but lookup failed",
                        detail="Bot may lack View Channels perm")
            else:
                add("vc", "info", "Default voice channel not set (optional)")
        except Exception as exc:
            add("vc", "warn", "VC check failed", detail=str(exc))

        # 8. Registry catalog freshness (informational)
        try:
            st = _reg.get_registry_status()
            if not st.get("have_cache"):
                add("registry", "info", "Plugin registry never fetched",
                    detail="Not blocking — registry is optional")
            elif st.get("fresh"):
                add("registry", "ok", "Plugin registry cache is fresh")
            else:
                add("registry", "info", "Plugin registry cache is stale",
                    detail="Plugins → ↻ Re-fetch catalog to refresh")
        except Exception:
            add("registry", "info", "Registry unreachable (offline)")

        # 9. Veto session NOT stuck mid-flow (would block a new match)
        sess = getattr(core, "_veto_session", None)
        state = getattr(sess, "state", "idle") if sess else "idle"
        if state in ("idle", "complete"):
            add("veto", "ok", "No stuck veto session")
        else:
            add("veto", "warn", f"Veto session active (state={state})",
                detail="Open Veto tab → Reset if you're done with this match")

        # 10. Public share URL configured (for remote captains over tunnel)
        if core.public_share_url:
            add("share_url", "ok", "Public share URL set",
                detail=core.public_share_url)
        else:
            add("share_url", "info",
                "Public share URL not set (LAN-only sessions)",
                detail="Config → Match Flow → Public Share URL — needed for remote captains")

        # Roll-up
        counts = {"ok": 0, "warn": 0, "fail": 0, "info": 0}
        for c in checks:
            counts[c["status"]] = counts.get(c["status"], 0) + 1
        overall = "fail" if counts["fail"] else \
                  "warn" if counts["warn"] else "ok"
        return jsonify({
            "checks":  checks,
            "counts":  counts,
            "overall": overall,
        })

    # ─── Config backup / restore (v0.16.0 / task #158) ────────────────────────
    # Operator-facing safety net.  Backups live next to the live config under
    # %APPDATA%/Oblivion Server Tool/backups/.  Auto-snapshot fires before
    # every risky plugin action (install/uninstall/deploy); manual snapshot
    # available from the SPA Tools card.

    @app.route("/api/config/backup", methods=["POST"])
    @require_auth
    @require_local
    def api_config_backup():
        d = request.get_json(silent=True) or {}
        reason = (d.get("reason") or "manual").strip()
        result = core.backup_config(reason=reason)
        if not result.get("ok"):
            return jsonify({"error": result.get("error") or "backup failed"}), 500
        return jsonify({"ok": True, "result": result})

    @app.route("/api/config/backups")
    @require_auth
    @require_local
    def api_config_backups():
        return jsonify({"backups": core.list_config_backups()})

    @app.route("/api/config/restore", methods=["POST"])
    @require_auth
    @require_local
    def api_config_restore():
        d = request.get_json(silent=True) or {}
        filename = (d.get("filename") or "").strip()
        if not filename:
            return jsonify({"error": "filename is required"}), 400
        # Defensive: don't restore while a server is running — the operator
        # might have RCON connections open against the current config.
        if core.running:
            return jsonify({"error": "Stop the server before restoring a "
                                      "config backup."}), 409
        result = core.restore_config_backup(filename)
        if not result.get("ok"):
            return jsonify({"error": result.get("error") or "restore failed"}), 400
        return jsonify({
            "ok":               True,
            "restored_from":    result["restored_from"],
            "pre_restore_backup": result["pre_restore_backup"],
            "note": "Restart the app to load the restored config into memory.",
        })

    @app.route("/api/diag/snapshot")
    @require_auth
    @require_local
    def diag_snapshot():
        """Returns text/plain.  Pasteable into chat without editing."""
        import platform, datetime
        lines: list[str] = []
        def hr(title: str):
            lines.append("")
            lines.append(f"─── {title} ───")
        def kv(k, v):
            lines.append(f"  {k}: {v}")

        lines.append("═══ OBLIVION DIAGNOSTIC SNAPSHOT ═══")
        lines.append(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"App version: {_config.APP_VERSION}")
        lines.append(f"Build:       {'frozen .exe' if getattr(sys, 'frozen', False) else 'dev (python)'}")
        try:
            lines.append(f"OS:          {platform.system()} {platform.release()} "
                         f"({platform.version()})")
            lines.append(f"Python:      {platform.python_version()}")
        except Exception as exc:
            lines.append(f"OS/Python info unavailable: {exc}")

        # ─── v0.11.10 — TL;DR auto-scan ────────────────────────────────────
        # 6-10 line health summary that the reader can grok in 2 seconds
        # before deciding which detail section to dive into.  Anything
        # marked ⚠ deserves attention; everything ✓ can be skipped if
        # the issue points elsewhere.  Designed so a Friday-night
        # maintainer can triage 5 pasted snapshots in 30 seconds total.
        tldr: list[tuple[str, str, str]] = []   # (icon, label, detail)
        # App
        tldr.append(("✓", "app",
                     f"running v{_config.APP_VERSION}, "
                     f"{'frozen' if getattr(sys, 'frozen', False) else 'dev'}"))
        # Server
        if core.running:
            try:
                up = int(core.uptime_seconds)
                h, r = divmod(up, 3600); m = r // 60
                up_str = f"{h}h {m}m" if up > 0 else "(booting)"
            except Exception:
                up_str = "(unknown uptime)"
            cm = getattr(core, "current_map", "") or "(none)"
            cmode = getattr(core, "current_mode", "") or "(none)"
            try: pc = core.player_count
            except Exception: pc = "?"
            srv_icon = "✓"
            if getattr(core, "boot_state", "") == "booting":
                srv_icon = "⚠"
                tldr.append(("⚠", "server",
                             f"booting on {cm} ({cmode}) — not ready yet"))
            else:
                tldr.append((srv_icon, "server",
                             f"running on {cm} ({cmode}), {up_str}, {pc} players"))
        else:
            last_err = getattr(core, "last_start_error", "") or ""
            if last_err:
                tldr.append(("⚠", "server",
                             f"offline — last start error: {str(last_err)[:80]}"))
            else:
                tldr.append(("·", "server", "offline"))
        # Veto session
        with core._veto_lock:
            sess = core._veto_session
            if sess is None:
                tldr.append(("·", "veto", "idle (no session)"))
            else:
                age_min = (time.time() - sess.updated_at) / 60.0
                icon = "✓"
                detail = f"state={sess.state} mode={sess.mode}"
                # Look for stuck-state heuristics
                if sess.state == "links":
                    # Both tokens issued but not both claimed for >5 min
                    a_used = sess.tokens.get("A", None)
                    b_used = sess.tokens.get("B", None)
                    if a_used and b_used:
                        if a_used.used and not b_used.used and age_min > 5:
                            icon = "⚠"
                            detail += f", captain B unclaimed for {age_min:.0f}min"
                        elif b_used.used and not a_used.used and age_min > 5:
                            icon = "⚠"
                            detail += f", captain A unclaimed for {age_min:.0f}min"
                        elif not a_used.used and not b_used.used and age_min > 10:
                            icon = "⚠"
                            detail += f", both unclaimed for {age_min:.0f}min"
                elif sess.state == "finale" and age_min > 5:
                    icon = "⚠"
                    detail += f", at finale for {age_min:.0f}min (matchzy stuck?)"
                tldr.append((icon, "veto", detail))
        # Discord
        try:
            from . import discord_bot as _dbot
            if not getattr(core, "discord_bot_token", ""):
                tldr.append(("·", "discord", "not configured"))
            else:
                st = _dbot.bot_status()
                if st.get("connected"):
                    tldr.append(("✓", "discord",
                                 f"connected as {st.get('user') or '(name unresolved)'}"))
                else:
                    err = st.get("error", "") or "not connected"
                    tldr.append(("⚠", "discord",
                                 f"token set but not connected: {str(err)[:60]}"))
        except Exception as exc:
            tldr.append(("⚠", "discord", f"status query failed: {exc}"))
        # Disk — v0.11.11 fix: pull _CONFIG_FILE locally so the TL;DR
        # scan doesn't NameError silently (the Persistence-files section
        # below imports it, but that's AFTER us in the function body).
        # 5 GB warn threshold because Windows starts misbehaving at 2 GB
        # (no Recycle Bin, no Volume Shadow Copies, no temp room).
        try:
            from .config import _CONFIG_FILE as _CFG
            import shutil as _shutil
            free_gb = _shutil.disk_usage(os.path.dirname(_CFG) or ".").free / 1e9
            if free_gb < 5:
                tldr.append(("⚠", "disk", f"only {free_gb:.1f} GB free at config dir"))
            else:
                tldr.append(("✓", "disk", f"{free_gb:.1f} GB free at config dir"))
        except Exception as exc:
            tldr.append(("·", "disk", f"(could not check: {exc})"))
        # v0.11.13 — CS2 server log freshness in TL;DR.  Reader can tell
        # at a glance whether the console.log section is from the CURRENT
        # session or stale leftovers from days ago.  Saves them from
        # wasting time reading data that doesn't apply to "now."
        try:
            _cs2_log = core.driver.console_log_path(core)
            if os.path.isfile(_cs2_log):
                _age_s = time.time() - os.path.getmtime(_cs2_log)
                if   _age_s < 90:     _age = f"{int(_age_s)}s ago"
                elif _age_s < 3600:   _age = f"{int(_age_s/60)}m ago"
                elif _age_s < 86400:  _age = f"{_age_s/3600:.1f}h ago"
                else:                 _age = f"{_age_s/86400:.1f} days ago"
                if _age_s > 3600:
                    tldr.append(("⚠", "cs2_log",
                                 f"{_age} — NOT current session (read context carefully)"))
                else:
                    tldr.append(("✓", "cs2_log", f"current session ({_age})"))
            else:
                tldr.append(("·", "cs2_log", "(no console.log — server never started or -condebug missing)"))
        except Exception:
            tldr.append(("·", "cs2_log", "(could not check)"))
        # Recent log error count (last 50 lines)
        _log_lines = core.get_log() or []
        _recent = _log_lines[-50:]
        _err_re = re.compile(r"\[(error|warn(?:ing)?|fail)\]|EXCEPTION|TRACEBACK|"
                             r"\bfailed\b|\bdenied\b|\bcrashed\b|\btimeout\b",
                             re.IGNORECASE)
        _err_count = sum(1 for ln in _recent if _err_re.search(ln))
        if _err_count > 0:
            tldr.append(("⚠", "recent",
                         f"{_err_count} error/warn lines in last 50 app-log entries"))
        else:
            tldr.append(("·", "recent", "no error markers in recent app log"))

        # v0.11.19 — Plugin log health.  Surfaces when CSS/MatchZy logs
        # exist (plugin layer is active) vs when they don't (vanilla
        # mode).  Useful tournament-night signal because MatchZy
        # redirects CS2's console.log, so a healthy CSS log is the
        # replacement for "is the plugin layer happy?"
        try:
            _css_dir = os.path.join(core._csgo_dir(), "addons",
                                     "counterstrikesharp", "logs")
            _has_css_log = False
            _css_age_s   = None
            if os.path.isdir(_css_dir):
                import glob as _g
                _matches = (_g.glob(os.path.join(_css_dir, "log-*.txt"))
                            + _g.glob(os.path.join(_css_dir, "*.log")))
                if _matches:
                    _has_css_log = True
                    _css_age_s = time.time() - max(
                        os.path.getmtime(p) for p in _matches
                    )
            if _has_css_log and _css_age_s is not None:
                if   _css_age_s < 90:    _css_age = f"{int(_css_age_s)}s ago"
                elif _css_age_s < 3600:  _css_age = f"{int(_css_age_s/60)}m ago"
                elif _css_age_s < 86400: _css_age = f"{_css_age_s/3600:.1f}h ago"
                else:                    _css_age = f"{_css_age_s/86400:.1f} days ago"
                _fresh = _css_age_s < 3600
                tldr.append(("✓" if _fresh else "⚠", "plugin_log",
                             f"CSS log {_css_age}"
                             + (" — stale (no plugin activity since)"
                                if not _fresh else "")))
            else:
                # No CSS log → either vanilla, or plugins haven't loaded
                tldr.append(("·", "plugin_log",
                             "no CSS log (vanilla mode or plugins not yet loaded)"))
        except Exception:
            tldr.append(("·", "plugin_log", "(could not check)"))

        # v0.12.2 — SSE broadcast drops indicator (audit finding #10).  Only
        # interesting when > 0; otherwise a single line clutters the TL;DR.
        try:
            with _veto_broadcast_stats_lock:
                drops = _veto_broadcast_stats["drops_total"]
            if drops > 0:
                tldr.append(("⚠", "sse",
                             f"{drops} broadcast event(s) dropped — "
                             "subscriber too slow to drain.  See "
                             "'SSE broadcast telemetry' section."))
        except Exception:
            pass

        # Render TL;DR
        hr("TL;DR (auto-scan)")
        for icon, label, detail in tldr:
            lines.append(f"  {icon} {label:9} {detail}")

        # ─── Driver (v0.13.0 / task #86) ───
        # First thing the operator sees after TL;DR — confirms which
        # game driver the build is running.  Will become more relevant
        # when v0.13 adds TF2 + v0.15 adds FiveM and one .exe can
        # potentially load multiple drivers.
        try:
            hr("Driver")
            d = core.driver.describe()
            kv("game",                d.get("game_name", "?"))
            kv("short_name",          d.get("short_name", "?"))
            kv("default_port",        d.get("default_port", "?"))
            kv("process_image_name",  d.get("process_image_name", "?"))
            kv("process_args_marker", d.get("process_args_marker", "?"))
            kv("plugin_layer",        d.get("plugin_layer", "?"))
            kv("match_layer",         d.get("match_layer", "?"))
            kv("mode_count",          d.get("mode_count", "?"))
        except Exception as exc:
            kv("driver_status", f"(unavailable: {exc})")

        # ─── Server status ───
        hr("Server status")
        kv("running",       core.running)
        kv("boot_state",    getattr(core, "boot_state", "?"))
        kv("current_map",   getattr(core, "current_map", "") or "(none)")
        kv("current_mode",  getattr(core, "current_mode", "") or "(none)")
        try:
            up = core.uptime_seconds
            if up > 0:
                h, r = divmod(int(up), 3600); m = r // 60
                kv("uptime", f"{h}h {m}m")
            else:
                kv("uptime", "(offline)")
        except Exception:
            kv("uptime", "(unavailable)")
        kv("lan_ip",        _config._lan_ip())
        kv("public_ip",     getattr(core, "public_ip", "") or "(not detected)")
        kv("public_share_url",
           getattr(core, "public_share_url", "") or "(not set — captain links use public_ip)")
        try: kv("player_count", core.player_count)
        except Exception: kv("player_count", "(unavailable)")
        kv("last_start_error",
           getattr(core, "last_start_error", "") or "(none)")

        # ─── Active veto session ───
        hr("Active veto session")
        with core._veto_lock:
            sess = core._veto_session
            if sess is None:
                kv("state", "idle (no session)")
            else:
                kv("state",         sess.state)
                kv("mode",          sess.mode)
                kv("created_at",
                   datetime.datetime.fromtimestamp(sess.created_at).strftime('%H:%M:%S'))
                kv("updated_at",
                   datetime.datetime.fromtimestamp(sess.updated_at).strftime('%H:%M:%S'))
                kv("team_a", f"{sess.team_a_name} ({len(sess.team_a)} players)")
                kv("team_b", f"{sess.team_b_name} ({len(sess.team_b)} players)")
                # Captain identification (without leaking SteamIDs in plain)
                def _cap_name(team, idx):
                    if idx is None or not (0 <= idx < len(team)):
                        return "(not elected)"
                    return f"{team[idx].name} (idx={idx})"
                kv("captain_a", _cap_name(sess.team_a, sess.captain_a_idx))
                kv("captain_b", _cap_name(sess.team_b, sess.captain_b_idx))
                kv("ready_a/b", f"{sess.ready_a}/{sess.ready_b}")
                kv("tokens_a/b_used", f"{sess.tokens.get('A').used if 'A' in sess.tokens else 'n/a'}"
                                      f"/{sess.tokens.get('B').used if 'B' in sess.tokens else 'n/a'}")
                kv("revote_count", sess.revote_count)
                kv("map_pool", ", ".join(sess.map_pool) if sess.map_pool else "(empty)")
                kv("current_step", f"{sess.current_step}/{len(sess.sequence)}")
                if sess.sequence:
                    lines.append("  sequence:")
                    for i, st in enumerate(sess.sequence):
                        marker = "→" if i == sess.current_step else " "
                        m = st.map_id if st.map_id else "(pending)"
                        lines.append(f"    {marker} {i+1}. {st.team} {st.kind:4} → {m}")
                kv("decider",       sess.decider or "(not yet)")
                kv("final_maps",    ", ".join(sess.final_maps) if sess.final_maps else "(none)")
                kv("matchzy_config_built", sess.matchzy_config is not None)
                kv("spectator_token", "(issued)" if sess.spectator_token else "(none)")

        # ─── SSE broadcast telemetry (v0.12.2 — audit finding #10) ───
        hr("SSE broadcast telemetry")
        with _veto_broadcast_stats_lock:
            ev = _veto_broadcast_stats["events_total"]
            dr = _veto_broadcast_stats["drops_total"]
            last_drop = _veto_broadcast_stats["last_drop_at"]
        kv("events_total", ev)
        kv("drops_total",  dr)
        if last_drop > 0:
            age = max(0, int(time.time() - last_drop))
            kv("last_drop_at",
               f"{datetime.datetime.fromtimestamp(last_drop).strftime('%H:%M:%S')}"
               f" ({age}s ago)")
        else:
            kv("last_drop_at", "(no drops since process start)")
        with _veto_subs_lock:
            kv("active_subscribers", len(_veto_subs))

        # ─── Discord bot ───
        # v0.11.10: collapse to one-liner when not configured.  The full
        # detail block is noise when there's no bot to debug.
        hr("Discord bot")
        if not getattr(core, "discord_bot_token", ""):
            kv("status", "(not configured — token absent, all Discord features no-op)")
            if getattr(core, "discord_webhook_url", ""):
                kv("webhook", "configured (separate from bot)")
        else:
            try:
                from . import discord_bot as _dbot
                kv("library_available", getattr(_dbot, "DISCORD_AVAILABLE", False))
            except Exception as exc:
                kv("library_available", f"(import failed: {exc})")
            kv("token_configured", True)
            kv("guild_id",         getattr(core, "discord_guild_id", "") or "(not set)")
            kv("veto_channel_id",  getattr(core, "discord_veto_channel_id", "") or "(not set)")
            # v0.11.15 — default voice channel for one-click roster pull.
            # Show the configured ID + live member count when the bot can
            # resolve it; "(not set)" when unconfigured (roster modal will
            # fall back to the picker).  member_count == "N/A" means the bot
            # couldn't reach the channel (gone, perms changed, bot offline).
            _vc_id = getattr(core, "discord_voice_channel_id", "")
            kv("voice_channel_id", _vc_id or "(not set — roster pull uses picker)")
            if _vc_id and getattr(core, "discord_guild_id", ""):
                try:
                    from . import discord_bot as _dbot
                    if _dbot.bot_status().get("connected"):
                        # v0.11.16: 1.5s timeout (was 3.0s).  If the bot is
                        # mid-reconnect or wedged, "snapshot feels frozen for
                        # 3 seconds" is exactly the opposite of what an
                        # operator hitting the triage button needs.  Half a
                        # tick of bot latency is fine; longer → mark unknown
                        # and let the operator move on.
                        _info = _dbot.bot_voice_channel_info(
                            core.discord_guild_id, _vc_id, timeout=1.5
                        )
                        if _info:
                            kv("voice_channel_name",  _info.get("name", "?"))
                            kv("voice_channel_count", f"{_info.get('member_count', 0)} connected")
                        else:
                            kv("voice_channel_name",  "(bot cannot resolve — check ID/perms)")
                    else:
                        kv("voice_channel_name", "(bot not connected — count unavailable)")
                except Exception as exc:
                    kv("voice_channel_name", f"(lookup failed: {exc})")
            try:
                from . import discord_bot as _dbot
                status = _dbot.bot_status()
                # bot_status returns: configured, connected, user, error
                # v0.11.11 fix: was looking for the wrong keys (name/id);
                # the actual return shape exposes `user` as the joined
                # username#discrim string.
                for k in ("connected", "user", "error"):
                    if k in status:
                        kv(f"bot.{k}", status[k])
            except Exception as exc:
                kv("bot.status_query", f"(failed: {exc})")
            kv("webhook_configured", bool(getattr(core, "discord_webhook_url", "")))

        # ─── Plugin deployment (current mode) ───
        hr("Plugin manifest")
        try:
            manifest = core._load_plugin_manifest()
            if manifest:
                kv("last_deploy_mode", manifest.get("mode", "?"))
                kv("plugins",           ", ".join(manifest.get("plugins", [])) or "(none)")
                # v0.16.0 / task #164a — was reading "at" but the writer
                # in core._save_plugin_manifest stores "deployed_at" (since
                # v0.10.x).  Stale key mismatch produced "deployed_at: ?"
                # on every diag snapshot since that field was renamed.
                kv("deployed_at",       manifest.get("deployed_at", "?"))
            else:
                kv("manifest", "(no oblivion_plugins.json — nothing deployed yet)")
        except Exception as exc:
            kv("manifest", f"(read failed: {exc})")

        # ─── Persistence files on disk ───
        hr("Persistence files")
        from .config import MATCH_HISTORY_FILE, VETO_ACTIVE_FILE, _CONFIG_FILE
        for label, path in (
            ("config",          _CONFIG_FILE),
            ("match_history",   MATCH_HISTORY_FILE),
            ("veto_active",     VETO_ACTIVE_FILE),
        ):
            try:
                if os.path.isfile(path):
                    sz = os.path.getsize(path)
                    mt = datetime.datetime.fromtimestamp(os.path.getmtime(path))
                    kv(label, f"{path} ({sz} bytes, mtime {mt.strftime('%H:%M:%S')})")
                else:
                    kv(label, f"{path} (NOT PRESENT)")
            except Exception as exc:
                kv(label, f"{path} (stat failed: {exc})")

        # ─── Recent app log (last 80 lines) ───
        # v0.11.10: lines matching error/warn/fail patterns get a `>`
        # prefix instead of the usual two-space indent.  Makes anomalies
        # jump out on a visual scan instead of hiding in 80 lines of
        # noise.  Same `_err_re` as the TL;DR scan above for consistency.
        hr("Recent app log (last 80 lines, anomalies prefixed `>`)")
        log_lines = core.get_log()
        for ln in log_lines[-80:]:
            prefix = "> " if _err_re.search(ln) else "  "
            lines.append(f"{prefix}{ln}")
        if not log_lines:
            lines.append("  (log buffer empty)")

        # ─── Redacted config ───
        hr("Config (redacted)")
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg_data = json.load(f)
            SENSITIVE = {
                "admin_pin", "guest_pin", "rcon_password", "sv_password",
                "gslt_token", "steam_password", "discord_bot_token",
                "discord_webhook_url",
            }
            for k, v in sorted(cfg_data.items()):
                if k in SENSITIVE:
                    kv(k, "***" if v else "(empty)")
                elif isinstance(v, (dict, list)):
                    import json as _json
                    short = _json.dumps(v)
                    if len(short) > 100:
                        short = short[:97] + "..."
                    kv(k, short)
                else:
                    kv(k, v)
        except Exception as exc:
            kv("config_read", f"(failed: {exc})")

        # ─── v0.11.9 — Browser / request context ───────────────────────────
        hr("Request context")
        try:
            ua = request.headers.get("User-Agent", "(missing)")
            kv("user_agent", ua[:200] + ("…" if len(ua) > 200 else ""))
            kv("remote_addr", request.remote_addr or "(unknown)")
            kv("snapshot_local_only_gate", "passed (this caller is local)")
        except Exception as exc:
            kv("request_context", f"(failed: {exc})")

        # ─── v0.11.9 — Disk free space at known write paths ────────────────
        hr("Disk space")
        try:
            import shutil
            for label, path in (
                ("config_dir",  os.path.dirname(_CONFIG_FILE) or "."),
                ("csgo_dir",    core._csgo_dir()),
                ("server_dir",  getattr(core, "server_dir", "") or "(unset)"),
            ):
                if path and path != "(unset)" and os.path.isdir(path):
                    try:
                        total, used, free = shutil.disk_usage(path)
                        kv(label, f"{path} — free {free / 1e9:.1f} GB / total {total / 1e9:.1f} GB")
                    except Exception as exc:
                        kv(label, f"{path} (disk_usage failed: {exc})")
                else:
                    kv(label, f"{path} (path does not exist)")
        except Exception as exc:
            kv("disk_space", f"(failed: {exc})")

        # ─── v0.11.9 — Plugin file verification (current mode) ─────────────
        # Manifest tells us what was deployed; this verifies the files are
        # actually still on disk.  Catches the "deployed-but-missing" silent
        # failure mode (someone deleted addons/, plugin update half-applied).
        #
        # v0.11.12 fix: if the manifest's mode != current_mode, the operator
        # has switched modes since the last deploy and Oblivion correctly
        # undeployed the manifest's plugins.  Missing files in that case
        # are the EXPECTED healthy state, NOT a failure to flag.  Honest
        # framing avoids the false-positive ⚠ that misled triage on the
        # first real Way-3 paste.
        hr("Plugin file verification")
        try:
            from .core import _PLUGIN_VERIFY_FILES, _MODE_PLUGIN_NAMES
            manifest = core._load_plugin_manifest()
            deployed = manifest.get("plugins", []) if manifest else []
            manifest_mode = (manifest.get("mode", "") if manifest else "") or ""
            current_mode  = getattr(core, "current_mode", "") or ""
            stale_manifest = bool(
                deployed and current_mode and manifest_mode
                and manifest_mode != current_mode
            )
            if not deployed:
                kv("status", "(nothing deployed — skipping verification)")
            elif stale_manifest:
                # Manifest reflects a previous mode whose plugins were
                # correctly undeployed when current_mode was selected.
                # Reporting "MISSING" here is misleading; report the
                # mismatch instead and skip the verify.
                kv("status",
                   f"(manifest stale — last_deploy={manifest_mode}, "
                   f"current_mode={current_mode}; "
                   f"undeploy on mode-switch is expected behaviour, "
                   f"not verifying)")
            else:
                any_missing = False
                for plug in deployed:
                    missing = core._verify_plugin_files(plug)
                    if missing:
                        any_missing = True
                        kv(f"{plug}", f"⚠ MISSING {len(missing)} file(s):")
                        for path in missing[:5]:    # cap output
                            lines.append(f"      - {path}")
                        if len(missing) > 5:
                            lines.append(f"      ... and {len(missing) - 5} more")
                    else:
                        kv(plug, "✓ all expected files present")
                if not any_missing:
                    kv("overall", "✓ all deployed plugins verified clean")
        except Exception as exc:
            kv("verification", f"(failed: {exc})")

        # ─── v0.11.9 — Active veto session raw JSON (if any) ───────────────
        # The decoded-view section above is human-friendly.  This is the
        # raw on-disk form, which catches schema-corruption issues that
        # round-trip through serialize/deserialize masking.
        # v0.11.10: collapsed when no session present.
        from .config import VETO_ACTIVE_FILE
        hr("Active veto session — raw JSON")
        if not os.path.isfile(VETO_ACTIVE_FILE):
            kv("status", "(no session — file not present, nothing to dump)")
        else:
            try:
                with open(VETO_ACTIVE_FILE, "r", encoding="utf-8") as f:
                    raw = f.read()
                # Cap at 4 KB — a real session is ~2-3 KB; over 4 KB
                # signals something weird, truncation actually useful as
                # a tell.  Captain tokens are sensitive but already
                # excluded from the user-friendly "Active veto session"
                # section above — they DO appear here.  Mask them inline.
                import re as _re
                raw_masked = _re.sub(
                    r'"value":\s*"[^"]+"',
                    '"value": "***REDACTED***"',
                    raw,
                )
                if len(raw_masked) > 4096:
                    raw_masked = raw_masked[:4096] + "\n  ... (truncated at 4 KB)"
                for ln in raw_masked.splitlines():
                    lines.append(f"  {ln}")
            except Exception as exc:
                kv("raw_json_read", f"(failed: {exc})")

        # ─── v0.11.9 — CS2 server console.log tail ─────────────────────────
        # The #1 most useful artifact when the *server* (not the tool) is
        # the problem.  Started via `-condebug`; sits at <csgo>/console.log.
        #
        # v0.11.13: human-readable age in the header ("2.6 days ago") so
        # the reader knows immediately whether this is current-session
        # data or last-week's leftovers.  Frame-drop warnings now also
        # match _csgo_err_re for `>` flagging — they're noise on a healthy
        # server but they're the signal under load (Warcraft v0.9.2.1
        # dispatcher fix territory).
        hr("CS2 console.log (last 200 lines, anomalies + frame-drops prefixed `>`)")
        try:
            csgo_log = core.driver.console_log_path(core)
            if os.path.isfile(csgo_log):
                sz   = os.path.getsize(csgo_log)
                mtss = os.path.getmtime(csgo_log)
                mt   = datetime.datetime.fromtimestamp(mtss)
                # Friendly age string — load is days/hours/minutes ago
                age_s = max(0, time.time() - mtss)
                if   age_s < 90:           age_str = f"{int(age_s)}s ago"
                elif age_s < 3600:         age_str = f"{int(age_s/60)}m ago"
                elif age_s < 86400:        age_str = f"{age_s/3600:.1f}h ago"
                else:                      age_str = f"{age_s/86400:.1f} days ago"
                staleness_hint = ""
                if age_s > 3600:           # older than an hour
                    staleness_hint = "  ⚠ NOT current session"
                kv("source",
                   f"{csgo_log} ({sz / 1024:.1f} KB, "
                   f"mtime {mt.strftime('%Y-%m-%d %H:%M:%S')} — {age_str}{staleness_hint})")
                # CS2-specific anomaly regex extends the app-log one with
                # the frame-drop pattern.  Counted separately for the TL;DR
                # surface line below.
                _csgo_err_re = re.compile(
                    _err_re.pattern + r"|UNEXPECTED LONG FRAME|"
                    r"Cannot find map|host_workshop_map.*not found|"
                    r"matchzy_loadmatch",
                    re.IGNORECASE
                )
                # Tail efficiently — read last 64 KB then take last 200 lines
                with open(csgo_log, "rb") as f:
                    if sz > 64 * 1024:
                        f.seek(-64 * 1024, 2)
                        f.readline()    # skip partial line
                    tail = f.read().decode("utf-8", errors="replace")
                tail_lines = tail.splitlines()[-200:]
                if not tail_lines:
                    lines.append("  (file present but empty)")
                else:
                    # Surface count of frame-drop warnings near the top
                    _frame_drops = sum(1 for ln in tail_lines
                                       if "UNEXPECTED LONG FRAME" in ln)
                    if _frame_drops > 0:
                        kv("frame_drop_warnings",
                           f"{_frame_drops} 'UNEXPECTED LONG FRAME' "
                           f"line(s) in last 200")
                    lines.append("")
                    for ln in tail_lines:
                        prefix = "> " if _csgo_err_re.search(ln) else "  "
                        lines.append(f"{prefix}{ln}")
            else:
                kv("source", f"{csgo_log} (NOT PRESENT)")
                kv("note", "server may not have been started yet, or "
                            "-condebug isn't applying — check launch args")
        except Exception as exc:
            kv("console_log_tail", f"(failed: {exc})")

        # ─── v0.11.19 — Plugin logs (CSS + MatchZy) ─────────────────────────
        # MatchZy + CounterStrikeSharp plugins suppress / redirect CS2's
        # default console.log writes, so the section above goes blank when
        # the actual tournament workflow (MatchZy 5v5) is running.  That's
        # exactly when we need server-side visibility most.  This block
        # picks up the slack by tailing the plugin layer's own log files:
        #
        #   csgo/addons/counterstrikesharp/logs/log-YYYYMMDD.txt
        #     — CSS host log; captures plugin LOAD ERRORS + C# exceptions
        #       across all plugins.  Most useful for "MatchZy crashed".
        #
        #   csgo/logs/MatchZy/<latest>.log  (or .txt)
        #     — MatchZy's per-match events: roster join, ready, knife,
        #       round end, demo upload, etc.  Useful for "match started
        #       weird" triage.
        #
        # Anomaly regex matches CSS/C# error patterns: [ERROR], [FATAL],
        # `Exception`, `System.`, `at SomeClass.Method(` stack-trace lines.
        hr("Plugin logs (CSS + MatchZy — anomalies prefixed `>`)")
        try:
            import glob as _glob
            _plugin_err_re = re.compile(
                r"\[(?:ERROR|EROR|FATAL|FATL|CRIT|WARN(?:ING)?)\]|"
                r"\bException(?:\s|:)|"
                r"\bSystem\.[A-Z]\w*Exception\b|"
                r"^\s*at\s+[A-Z]\w*(?:\.[A-Z]\w*)+\(|"
                r"Stack trace:|"
                r"Failed to (?:load|start|connect|initialize)",
                re.IGNORECASE | re.MULTILINE
            )
            _found_any = False
            _csgo_dir = core._csgo_dir()

            def _tail_plugin_log(label: str, directory: str,
                                 patterns: list[str], n_lines: int = 80) -> bool:
                """Tail the most-recently-modified file matching any pattern in
                directory.  Returns True if a file was tailed (so the caller
                can flip the _found_any flag).  Adds its own header line into
                `lines` plus a kv() with the source path + size + age."""
                nonlocal lines
                if not os.path.isdir(directory):
                    return False
                matches: list[str] = []
                for pat in patterns:
                    matches.extend(_glob.glob(os.path.join(directory, pat)))
                if not matches:
                    return False
                matches.sort(key=os.path.getmtime, reverse=True)
                latest = matches[0]
                try:
                    sz   = os.path.getsize(latest)
                    mtss = os.path.getmtime(latest)
                    mt   = datetime.datetime.fromtimestamp(mtss)
                    age_s = max(0, time.time() - mtss)
                    if   age_s < 90:     age_str = f"{int(age_s)}s ago"
                    elif age_s < 3600:   age_str = f"{int(age_s/60)}m ago"
                    elif age_s < 86400:  age_str = f"{age_s/3600:.1f}h ago"
                    else:                age_str = f"{age_s/86400:.1f} days ago"
                    staleness = "  ⚠ pre-tournament file" if age_s > 3600 else ""
                    kv(f"{label}_source",
                       f"{latest} ({sz/1024:.1f} KB, {age_str}{staleness})")
                    # Cap at 32 KB read for a section that may contain
                    # many files — keep snapshot size reasonable.
                    with open(latest, "rb") as f:
                        if sz > 32 * 1024:
                            f.seek(-32 * 1024, 2)
                            f.readline()      # discard partial first line
                        tail = f.read().decode("utf-8", errors="replace")
                    tail_lines = tail.splitlines()[-n_lines:]
                    if not tail_lines:
                        lines.append(f"  ({label}: file present but empty)")
                        return True
                    _err_n = sum(1 for ln in tail_lines if _plugin_err_re.search(ln))
                    if _err_n > 0:
                        kv(f"{label}_anomalies",
                           f"{_err_n} error/exception line(s) in last {n_lines}")
                    lines.append("")
                    lines.append(f"  -- {label}: {os.path.basename(latest)} "
                                 f"(last {len(tail_lines)} lines) --")
                    for ln in tail_lines:
                        prefix = "> " if _plugin_err_re.search(ln) else "  "
                        lines.append(f"{prefix}{ln}")
                    return True
                except Exception as inner_exc:
                    kv(f"{label}_tail", f"(failed: {inner_exc})")
                    return True

            # 1. CounterStrikeSharp host log — plugin load errors,
            #    C# exceptions, anything any CSS plugin reports.
            css_log_dir = os.path.join(_csgo_dir, "addons",
                                        "counterstrikesharp", "logs")
            if _tail_plugin_log("css", css_log_dir,
                                ["log-*.txt", "*.log", "*.txt"]):
                _found_any = True

            # 2. MatchZy match log — per-match events written by the
            #    plugin itself.  Path varies between MatchZy versions
            #    (some write to logs/MatchZy/, some to
            #    addons/counterstrikesharp/plugins/MatchZy/logs/).
            matchzy_dirs = [
                os.path.join(_csgo_dir, "logs", "MatchZy"),
                os.path.join(_csgo_dir, "addons", "counterstrikesharp",
                             "plugins", "MatchZy", "logs"),
            ]
            for mzd in matchzy_dirs:
                if _tail_plugin_log("matchzy", mzd,
                                    ["*.log", "*.txt"]):
                    _found_any = True
                    break       # only tail one MatchZy log location

            if not _found_any:
                kv("status",
                   "no plugin logs found — vanilla CS2 mode, or plugins "
                   "haven't been loaded yet (start the server first)")
        except Exception as exc:
            kv("plugin_logs", f"(failed: {exc})")

        lines.append("")
        lines.append("═══ END SNAPSHOT ═══")

        body = "\n".join(lines)
        return Response(body, mimetype="text/plain; charset=utf-8")

    @app.route("/api/log/save", methods=["POST"])
    @require_auth
    @require_local   # writes to the user's local config dir — never remote
    def log_save():
        """Dump the in-memory log buffer to a timestamped file in the user's
        config directory so the operator can open / share it without needing
        clipboard access (Edge WebView2 clipboard occasionally fails silently).

        Local-only: remote guests/admins could otherwise spam saves to fill
        the host's disk.  Filename includes a 6-hex random suffix so two saves
        in the same second don't silently overwrite each other.
        """
        import os
        import time as _time
        # Refuse empty saves — produces a misleading 0-byte file that the
        # operator then has to debug ("where's my log?").
        lines = core.get_log()
        if not lines:
            return jsonify({"error": "Log buffer is empty — nothing to save."}), 400
        cfg_dir = os.path.dirname(_config._CONFIG_FILE) or "."
        try:
            os.makedirs(cfg_dir, exist_ok=True)
        except Exception:
            pass
        # %Y%m%d_%H%M%S resolution is 1 second; bolt on 6 random hex so two
        # near-simultaneous saves never collide and silently overwrite.
        fname = _time.strftime("oblivion_log_%Y%m%d_%H%M%S_") + secrets.token_hex(3) + ".txt"
        path  = os.path.join(cfg_dir, fname)
        try:
            # Snapshot `lines` (taken above) — don't re-fetch inside the with
            # block so the file size matches the count we'll log.
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        core.log(f"[log] Saved {len(lines)} lines → {path}")
        return jsonify({"ok": True, "path": path})

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

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Reachability check (v1.2 — Steam master server query) ─────────────
    # "Can players actually reach this server from outside?"
    #
    # Strategy: ask Valve's master server whether it sees our server
    # registered.  No external infrastructure to host, free, and
    # authoritative — Valve's master server IS the discovery layer
    # players' Steam clients use.  See cs2servergui/reachability.py.
    #
    # Admin-gated read-only diagnostic.
    @app.route("/api/reachability/check", methods=["POST"])
    @require_auth
    def api_reachability_check():
        from cs2servergui import reachability
        session  = _current_session() or {}
        is_admin = session.get("is_local") or session.get("role") == "admin"
        if not is_admin:
            return jsonify({"error": "admin only"}), 403

        public_ip = (core.public_ip or "").strip()
        if not public_ip:
            return jsonify({
                "error": "Public IP not yet detected — wait a moment after "
                         "launch and try again.",
            }), 503

        # Gather local state the hint engine needs.
        import time as _time
        running  = bool(getattr(core, "running", False))
        ustart   = getattr(core, "_uptime_start", None)
        uptime   = int(_time.time() - ustart) if (running and ustart) else 0
        gslt_set = bool((getattr(core, "gslt_token", "") or "").strip())

        try:
            raw = reachability.check_steam_master(public_ip)
        except reachability.ReachabilityError as exc:
            return jsonify({"error": str(exc)}), 503

        hints = reachability.interpret(
            raw,
            gslt_set            = gslt_set,
            server_running      = running,
            server_uptime_secs  = uptime,
            expected_port       = _config.RCON_PORT,
        )
        return jsonify({
            "target":   raw.get("target"),
            "ok":       raw.get("ok"),
            "servers":  raw.get("servers", []),
            "hints":    hints,
            "context": {
                "gslt_set":       gslt_set,
                "server_running": running,
                "uptime_secs":    uptime,
                "expected_port":  _config.RCON_PORT,
            },
        })

    return app
