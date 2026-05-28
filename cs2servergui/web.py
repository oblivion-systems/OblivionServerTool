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
import queue
import re
import secrets
import threading
import time
from collections.abc import Callable

from flask import (Flask, Response, abort, jsonify, redirect,
                   render_template, request, send_file)

from . import config as _config
from .config import (FLASK_PORT, GAME_MODES, OFFICIAL_MAPS, RCON_HOST,
                     RCON_PORT, MODE_MAPS, load_workshop)
from .core import AppCore


# ── Input validation ────────────────────────────────────────────────────────────
# CS2's console treats ';' and newlines as command separators, so any value
# interpolated into an RCON command line must be format-validated first.
_MAP_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")   # official map names
_DIGITS_RE   = re.compile(r"^[0-9]+$")          # workshop IDs, userids
_STEAMID_RE  = re.compile(r"^[A-Za-z0-9:\[\]_]+$")  # STEAM_/[U:..]/765... forms


# ── Session store ──────────────────────────────────────────────────────────────

_REMOTE_SESSION_TTL = 8 * 3600     # 8 hours for remote sessions
_sessions:      dict[str, dict] = {}   # token → {ip, is_local, created_at}
_sessions_lock  = threading.Lock()


def _create_session(ip: str, is_local: bool = False) -> str:
    token = secrets.token_hex(32)
    with _sessions_lock:
        _sessions[token] = {
            "ip":         ip,
            "is_local":   is_local,
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
_attempts:      dict[str, dict] = {}   # ip → {count, until}
_attempts_lock  = threading.Lock()


def _check_lockout(ip: str) -> int:
    now = time.time()
    with _attempts_lock:
        stale = [k for k, r in _attempts.items()
                 if r["count"] >= _MAX_ATTEMPTS and r["until"] <= now]
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
        rec = _attempts.setdefault(ip, {"count": 0, "until": 0.0})
        rec["count"] += 1
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
        if token and core.startup_token and secrets.compare_digest(token, core.startup_token):
            core.startup_token = ""    # invalidate immediately — single-use
            session_token = _create_session("127.0.0.1", is_local=True)
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
        pin = (request.get_json() or {}).get("pin", "")
        if secrets.compare_digest(str(pin), str(core.admin_pin)):
            _clear_attempts(ip)
            _clear_global()
            core.log(f"Web login from {ip}")
            session_token = _create_session(ip, is_local=False)
            resp = jsonify({"ok": True})
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
        return jsonify({
            "running":            core.running,
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
            "lan_ip":             RCON_HOST,
            "rcon_port":          RCON_PORT,
            "flask_port":         FLASK_PORT,
            "is_local":           is_local,
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
        msg = msg.replace("\r", " ").replace("\n", " ")   # block RCON line injection
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
        name   = str(d.get("name",   "")).strip()
        if not _DIGITS_RE.match(userid):
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
            "max_players_override":  core.max_players_override,
            "admin_pin":             core.admin_pin     if is_local else "***",
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
        if "max_players_override"  in d: core.max_players_override  = str(d["max_players_override"])

        # Local-only fields (security-sensitive). The admin PIN protects the whole
        # panel, so only the trusted local window may change it.
        if is_local:
            if "admin_pin" in d:
                new_pin = str(d["admin_pin"]).strip()
                if new_pin.isdigit() and len(new_pin) >= 4:
                    core.admin_pin    = new_pin
                    _config.ADMIN_PIN = new_pin
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
    @require_auth
    @require_local
    def workshop_download():
        wid = (request.get_json() or {}).get("id", "").strip()
        if not wid.isdigit():
            return jsonify({"error": "Invalid workshop ID — digits only"}), 400
        if not core.steam_username or not core.steam_password:
            return jsonify({
                "error": "Steam credentials required",
                "needs_steam": True,
            }), 400
        core.depotdl_download(
            wid,
            on_done=lambda ok: core.log(
                f"Workshop download {'complete' if ok else 'FAILED'}: {wid}"
            ),
        )
        return jsonify({"ok": True})

    @app.route("/api/workshop/cancel", methods=["POST"])
    @require_auth
    @require_local
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
    def setup_status():
        """Return whether first-run setup is still needed."""
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

    # ── Log SSE ────────────────────────────────────────────────────────────────

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

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
