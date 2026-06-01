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
import queue
import re
import secrets
import threading
import time
from collections.abc import Callable

from flask import (Flask, Response, abort, jsonify, redirect,
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


# ── Flask app factory ──────────────────────────────────────────────────────────

def create_flask(core: AppCore) -> Flask:
    app = Flask(__name__)   # static_folder=<pkg>/static, template_folder=<pkg>/templates

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
        "/api/veto/state",
        "/api/veto/stream",
        "/api/veto/step",
    })
    _PUBLIC_PATHS = frozenset({
        "/api/ping", "/api/auth/login", "/api/auth/logout",
        "/api/veto/claim",        # token IS the credential; PIN-free entry
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
        return jsonify({"error": f"{role or 'unknown'} role cannot access {p}"}), 403

    # ── SPA shell ──────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        session = _current_session()
        return render_template(
            "index.html",
            authed=bool(session),
            pin_len=len(core.admin_pin),
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
                httponly=True, samesite="Strict",
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
                httponly=True, samesite="Strict",
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
        return jsonify({"ok": True})

    # ── Server state ───────────────────────────────────────────────────────────

    @app.route("/api/state")
    @require_auth
    def api_state():
        session  = _current_session()
        is_local = bool(session and session.get("is_local"))
        role     = "admin" if is_local else ((session or {}).get("role") or "guest")
        return jsonify({
            "running":            core.running,
            "role":               role,
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
            "dl_active":          core._active_dl_proc is not None,
            "dl_progress":        core._dl_progress or None,
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
        return {
            "state": s.state,
            "session": {
                "mode":          s.mode,
                "map_pool":      list(s.map_pool),
                "team_a_name":   s.team_a_name,
                "team_b_name":   s.team_b_name,
                "roster":        [{"name": p.name, "steam_id": p.steam_id}
                                  for p in s.roster],
                "team_a":        [{"name": p.name, "steam_id": p.steam_id}
                                  for p in s.team_a],
                "team_b":        [{"name": p.name, "steam_id": p.steam_id}
                                  for p in s.team_b],
                "votes_a":       dict(s.votes_a),
                "votes_b":       dict(s.votes_b),
                "captain_a_idx": s.captain_a_idx,
                "captain_b_idx": s.captain_b_idx,
                "revote_count":  s.revote_count,
                "tokens_claimed": {team: tok.used
                                   for team, tok in s.tokens.items()},
                "sequence":      [{"kind": st.kind, "team": st.team, "map_id": st.map_id}
                                  for st in s.sequence],
                "current_step":  s.current_step,
                # NEW: pre-computed for the frontend (Day 3 SPA).
                "current_step_detail": step_detail,
                "legal_moves":   list(legal),
                "decider":       s.decider,
                "final_maps":    list(s.final_maps),
                "updated_at":    s.updated_at,
            },
        }

    def _veto_broadcast() -> None:
        """Push a fresh snapshot to every SSE subscriber.  Non-blocking —
        if a subscriber's queue is full, drop the message for that client."""
        snap = _veto_snapshot()
        payload = "data: " + __import__("json").dumps(snap) + "\n\n"
        with _veto_subs_lock:
            subs = list(_veto_subs)
        for q in subs:
            try: q.put_nowait(payload)
            except Exception: pass

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
        q: queue.Queue = queue.Queue(maxsize=32)
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
            except Exception as e:
                return _veto_error_response(e)
        _veto_broadcast()
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
        # Build the LAN + Public links per captain — mirrors the Connect
        # popover's dual-display so the operator can hand the right link to
        # the right captain (LAN for in-house, Public if the captain is on
        # the internet via the existing port-forward).
        lan_ip   = _config._lan_ip()
        public   = core.public_ip or ""
        port     = _config.FLASK_PORT
        def _urls(token: str) -> dict:
            urls = {"lan": f"http://{lan_ip}:{port}/veto?join={token}"}
            if public:
                urls["public"] = f"http://{public}:{port}/veto?join={token}"
            return urls
        _veto_broadcast()
        # Include the raw token alongside the URLs so the SPA can build
        # /api/veto/qr?token=… without re-parsing it out of the LAN URL.
        return jsonify({
            "A": {"token": tokens["A"], **_urls(tokens["A"])},
            "B": {"token": tokens["B"], **_urls(tokens["B"])},
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
        lan_ip = _config._lan_ip()
        public = core.public_ip or ""
        port   = _config.FLASK_PORT
        urls   = {"lan": f"http://{lan_ip}:{port}/veto?join={new_token}"}
        if public:
            urls["public"] = f"http://{public}:{port}/veto?join={new_token}"
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
        with core._veto_lock:
            if core._veto_session is None:
                return jsonify({"error": "no active veto session"}), 400
            try:
                team = _veto.claim_captain(core._veto_session, token, caller_id=caller_ip)
            except Exception as e:
                return _veto_error_response(e)
        # Mint a captain session.  Reuse _create_session but extend with the
        # `captain_team` field so /api/veto/step can authorise per-team.
        session_token = _create_session(caller_ip, is_local=False, role="captain")
        # Annotate the session record with the captain team.
        sess = _get_session(session_token)
        if sess is not None:
            sess["captain_team"] = team
        core.log(f"[veto] captain {team} claimed from {caller_ip}")
        _veto_broadcast()
        resp = jsonify({"ok": True, "team": team})
        resp.set_cookie("session", session_token, httponly=True, samesite="Strict")
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
                _veto_broadcast()
                resp = redirect("/#veto")
                resp.set_cookie("session", session_token, httponly=True, samesite="Strict")
                return resp
        # Fall through — render the SPA shell; the frontend handles the rest.
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
        return jsonify(snap)

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
            try:
                cfg = _veto.build_matchzy_config(core._veto_session)
            except Exception as e:
                return _veto_error_response(e)
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
        with core._veto_lock:
            if core._veto_session is not None:
                _veto.complete(core._veto_session)
        _veto_broadcast()
        return jsonify({"ok": True, "config": cfg, "matchzy": matchzy_result})

    @app.route("/api/veto/reset", methods=["POST"])
    @require_auth
    def veto_reset():
        with core._veto_lock:
            if core._veto_session is not None:
                _veto.reset(core._veto_session)
            core._veto_session = None
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
        import segno
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
        port = _config.FLASK_PORT
        if kind == "lan":
            host = _config._lan_ip()
        else:
            host = core.public_ip or ""
            if not host:
                return jsonify({"error": "no public IP — cannot build URL"}), 400
        url = f"http://{host}:{port}/veto?join={token}"
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

    return app
