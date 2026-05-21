from __future__ import annotations

import collections
import customtkinter as ctk
import functools
import json
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import tkinter.filedialog
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from flask import Flask, render_template_string, request, jsonify, Response

def _lan_ip() -> str:
    """Return the machine's primary LAN IP — the address CS2 binds its TCP listener to.

    CS2 dedicated server opens its RCON TCP socket on the LAN IP, NOT on
    127.0.0.1, so we must connect to the same address.  This trick opens a
    UDP socket toward an external address (no data is sent) purely to ask
    the OS which local IP it would route through.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


# ── Config ─────────────────────────────────────────────────────────────────────
CS2_PATH       = r"D:\steamcmd\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe"
WORKSHOP_DIR   = r"D:\steamcmd\steamapps\workshop\content\730"
RCON_HOST      = _lan_ip()   # auto-detects LAN IP (CS2 binds RCON TCP to this, not 127.0.0.1)
RCON_PORT      = 27015
RCON_PASSWORD  = "qweewq"
FLASK_PORT     = 5000
ADMIN_PIN      = "1234"   # ← digits only (web keypad); change before deploying

STEAMCMD_PATH  = r"D:\steamcmd\steamcmd.exe"
CS2_SERVER_DIR = r"D:\steamcmd"   # steamcmd force_install_dir;
                                   # workshop maps land at <dir>\steamapps\workshop\content\730\<id>\
CS2_APP_ID     = "730"

OFFICIAL_MAPS = [
    "de_dust2", "de_mirage", "de_inferno", "de_nuke",
    "de_ancient", "de_anubis", "de_vertigo", "de_cache",
]

GAME_MODES = [
    # ── Standard ──────────────────────────
    "Competitive", "Casual", "Wingman", "3v3", "4v4", "1v1",
    # ── Gun Game ──────────────────────────
    "Arms Race", "Demolition", "Deathmatch",
    # ── Custom / Workshop ─────────────────
    "Zombies", "Surf", "KZ / Climb", "Retakes",
]

# game_type + game_mode together define CS2's ruleset
MODE_SETTINGS: dict[str, dict[str, str]] = {
    "Competitive": {"game_type": "0", "game_mode": "1", "maxplayers": "10"},
    "Casual":      {"game_type": "0", "game_mode": "0", "maxplayers": "12"},
    "Wingman":     {"game_type": "0", "game_mode": "2", "maxplayers": "4"},
    "3v3":         {"game_type": "0", "game_mode": "1", "maxplayers": "6"},
    "4v4":         {"game_type": "0", "game_mode": "1", "maxplayers": "8"},
    "1v1":         {"game_type": "0", "game_mode": "1", "maxplayers": "2"},
    "Arms Race":   {"game_type": "1", "game_mode": "0", "maxplayers": "16"},
    "Demolition":  {"game_type": "1", "game_mode": "1", "maxplayers": "10"},
    "Deathmatch":  {"game_type": "1", "game_mode": "2", "maxplayers": "20"},
    "Zombies":     {"game_type": "6", "game_mode": "0", "maxplayers": "10"},
    "Surf":        {"game_type": "6", "game_mode": "0", "maxplayers": "20"},
    "KZ / Climb":  {"game_type": "6", "game_mode": "0", "maxplayers": "10"},
    "Retakes":     {"game_type": "0", "game_mode": "0", "maxplayers": "10"},
}
_DEFAULT_MODE = MODE_SETTINGS["Competitive"]

# Maps valid for each game mode.
#   list[str] → show only these maps in the Official Map picker
#   None      → workshop map required; official picker is disabled
MODE_MAPS: dict[str, list[str] | None] = {
    "Competitive": OFFICIAL_MAPS,
    "Casual":      OFFICIAL_MAPS + ["cs_office", "cs_italy"],
    "Wingman":     ["de_vertigo", "de_inferno", "de_nuke", "de_cache",
                    "de_ancient", "de_anubis", "de_overpass"],
    "3v3":         OFFICIAL_MAPS,
    "4v4":         OFFICIAL_MAPS,
    "1v1":         None,   # aim maps — use a workshop map
    "Arms Race":   ["ar_shoots", "ar_baggage", "ar_dizzy"],
    "Demolition":  ["de_lake", "de_safehouse", "de_shortdust",
                    "de_stmarc", "de_bank", "de_sugarcane"],
    "Deathmatch":  OFFICIAL_MAPS,
    "Zombies":     None,   # workshop only
    "Surf":        None,   # workshop only
    "KZ / Climb":  None,   # workshop only
    "Retakes":     OFFICIAL_MAPS,
}

# CS2_PATH is  …\game\bin\win64\cs2.exe
# Go up 3 levels (win64 → bin → game) to reach the game root, then into csgo\addons
_CS2_GAME_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(CS2_PATH)))
CS2_ADDONS_DIR = os.path.join(_CS2_GAME_ROOT, "csgo", "addons")

# Search terms used as  searchtext=  in the Steam Workshop URL.
# requiredtags[] only works with Valve-predefined tags; community tags like
# "Surf" or "KZ" are free-text creator tags and return zero results that way.
MODE_WORKSHOP_SEARCH: dict[str, str] = {
    "Competitive": "bomb defusal",
    "Casual":      "bomb defusal",
    "Wingman":     "wingman 2v2",
    "3v3":         "3v3",
    "4v4":         "4v4",
    "1v1":         "1v1 aim",
    "Arms Race":   "arms race ar_",
    "Demolition":  "demolition",
    "Deathmatch":  "deathmatch",
    "Zombies":     "zombie escape",
    "Surf":        "surf",
    "KZ / Climb":  "kz climb",
    "Retakes":     "retake",
}
_WS_BROWSE = "https://steamcommunity.com/workshop/browse/?appid=730&browsesort=trend"

# Config file — stored next to the .exe when packaged, or next to this script in dev.
# Persists Steam credentials between sessions so the user doesn't re-enter them.
_APP_DIR     = os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else __file__
))
_CONFIG_FILE = os.path.join(_APP_DIR, "oblivion_config.json")


# ── RCON Client ────────────────────────────────────────────────────────────────
class RCONClient:
    """Thread-safe Source RCON client. Opens a fresh TCP connection per command."""

    def __init__(self, host: str, port: int, password: str) -> None:
        self.host     = host
        self.port     = port
        self.password = password
        self._id      = 1
        self._id_lock = threading.Lock()   # guards _id counter

    def _next_id(self) -> int:
        with self._id_lock:
            val = self._id
            self._id += 1
        return val

    @staticmethod
    def _pack(pkt_id: int, pkt_type: int, body: str) -> bytes:
        data = body.encode("utf-8") + b"\x00\x00"
        return struct.pack("<iii", 8 + len(data), pkt_id, pkt_type) + data

    @staticmethod
    def _recv(sock: socket.socket) -> tuple[int, int, str]:
        """Read one RCON packet → (pkt_id, pkt_type, body)."""
        raw = b""
        while len(raw) < 4:
            chunk = sock.recv(4 - len(raw))
            if not chunk:
                raise ConnectionError("RCON socket closed unexpectedly")
            raw += chunk
        size = struct.unpack("<i", raw)[0]
        data = b""
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("RCON socket closed unexpectedly")
            data += chunk
        pkt_id   = struct.unpack("<i", data[0:4])[0]
        pkt_type = struct.unpack("<i", data[4:8])[0]
        body     = data[8:-2].decode("utf-8", errors="replace")
        return pkt_id, pkt_type, body

    def execute(self, command: str) -> str:
        aid = self._next_id()
        cid = self._next_id()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((self.host, self.port))

            # ── auth ──────────────────────────────────────────────────────
            s.sendall(self._pack(aid, 3, self.password))
            pkt_id, pkt_type, _ = self._recv(s)
            # CS:GO sends a junk SERVERDATA_RESPONSE_VALUE (type 0) first,
            # then the real auth response (type 2).
            # CS2 skips the junk packet and sends only the auth response
            # (type 2) directly.  Handle both.
            if pkt_type == 0:
                pkt_id, pkt_type, _ = self._recv(s)   # discard junk, read auth
            if pkt_id == -1:
                raise ConnectionError("RCON auth failed — wrong rcon_password?")

            # ── command ───────────────────────────────────────────────────
            s.sendall(self._pack(cid, 2, command))
            _, _, body = self._recv(s)
        return body

    def execute_retry(self, command: str,
                      retries: int = 6, delay: float = 5.0) -> str:
        """Like execute() but retries on ConnectionRefused.

        CS2 takes 30-60 s to boot before RCON starts accepting connections.
        Each failed attempt waits `delay` seconds before the next try.
        Raises the last exception if all attempts are exhausted.
        """
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(retries):
            try:
                return self.execute(command)
            except ConnectionRefusedError as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(delay)
        raise last_exc


# ── Shared App State ───────────────────────────────────────────────────────────
class AppCore:
    """Single source of truth shared between the local GUI and Flask."""

    def __init__(self) -> None:
        self.proc:         subprocess.Popen | None = None
        self.running:      bool = False
        self.boot_state:   str  = "offline"   # "offline" | "booting" | "ready"
        self.current_map:  str  = OFFICIAL_MAPS[0]
        self.current_mode: str  = "Competitive"
        self.rcon = RCONClient(RCON_HOST, RCON_PORT, RCON_PASSWORD)

        self._log_buf   = collections.deque(maxlen=300)   # O(1) append+evict
        self._log_lock  = threading.Lock()

        self._sse_qs:   list[queue.Queue] = []
        self._sse_lock  = threading.Lock()

        self._dl_reqs:  list[dict]        = []
        self._dl_lock   = threading.Lock()

        self.update_available: bool = False   # set by check_update()

        # Steam credentials — used for workshop downloads when anonymous fails.
        # Server updates always use anonymous (no account needed for app_update).
        self.steam_username: str = ""
        self.steam_password: str = ""

        # Server config
        self.hostname:             str  = "CS2 Dedicated Server"
        self.sv_password:          str  = ""
        self.tickrate_128:         bool = False
        self.auto_start:           bool = False
        self.bot_difficulty:       str  = "Normal"
        self.max_players_override: str  = ""   # empty → use mode default
        self.presets:              dict[str, dict] = {}

        # Runtime state
        self.public_ip:       str  = ""
        self._map_name_cache: dict[str, str] = {}
        self._ff_enabled:     bool = False

        # fired with (prompt_type, submit_callback) when steamcmd asks for a Guard code
        self.on_steam_guard: Callable[[str, Callable[[str], None]], None] | None = None
        self._load_config()

        # GUI callbacks — registered by CS2GUI after widgets are built
        self.on_log:            Callable[[str], None] | None           = None
        self.on_dl_request:     Callable[[str, str], None] | None      = None
        self.on_state_change:   Callable[[], None] | None              = None
        # called with (update_available, installed_build, latest_build)
        self.on_update_checked: Callable[[bool, str, str], None] | None = None
        # called with the machine's public IP string
        self.on_public_ip:      Callable[[str], None] | None           = None

    # ── logging ───────────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        entry = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self._log_lock:
            self._log_buf.append(entry)   # deque evicts oldest automatically
        # copy before releasing lock so subscribers don't hold up writers
        with self._sse_lock:
            qs = list(self._sse_qs)
        for q in qs:
            q.put(entry)
        if self.on_log:
            self.on_log(entry)

    def get_log(self) -> list[str]:
        with self._log_lock:
            return list(self._log_buf)

    # ── SSE pub/sub ───────────────────────────────────────────────────────────

    def sse_subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._sse_lock:
            self._sse_qs.append(q)
        return q

    def sse_unsubscribe(self, q: queue.Queue) -> None:
        with self._sse_lock:
            try:
                self._sse_qs.remove(q)
            except ValueError:
                pass

    # ── server control ────────────────────────────────────────────────────────

    def start_server(self, map_name: str, mode: str, is_workshop: bool = False) -> None:
        s = MODE_SETTINGS.get(mode, _DEFAULT_MODE)
        maxp = self.max_players_override.strip() or s["maxplayers"]
        cmd = [
            CS2_PATH, "-dedicated",
            "-port",          str(RCON_PORT),
            "+sv_lan",        "0",
            "+game_type",     s["game_type"],
            "+game_mode",     s["game_mode"],
            "+maxplayers",    maxp,
            "+rcon_password", RCON_PASSWORD,
            "+hostname",      self.hostname or "CS2 Dedicated Server",
        ]
        if self.sv_password:
            cmd += ["+sv_password", self.sv_password]
        if self.tickrate_128:
            cmd += ["-tickrate", "128"]
        cmd += ["+host_workshop_map", map_name] if is_workshop else ["+map", map_name]
        try:
            self.proc = subprocess.Popen(cmd)
        except FileNotFoundError:
            self.log(f"CS2 executable not found: {CS2_PATH}")
            return
        self.running      = True
        self.boot_state   = "booting"
        self.current_map  = map_name
        self.current_mode = mode
        self.log(f"Server started  |  map: {map_name}  |  mode: {mode}")
        if self.tickrate_128:
            self.log("  Tickrate 128 enabled")
        if self.sv_password:
            self.log("  Server password set")
        self.log(f"Polling RCON at {RCON_HOST}:{RCON_PORT} — waiting for server to open port…")
        if self.on_state_change:
            self.on_state_change()
        threading.Thread(target=self._poll_rcon_ready, daemon=True).start()

    def stop_server(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.log("Server did not exit cleanly — killing process")
                self.proc.kill()
            self.proc       = None
        self.running    = False
        self.boot_state = "offline"
        self.log("Server stopped")
        if self.on_state_change:
            self.on_state_change()

    def _poll_rcon_ready(self) -> None:
        """Background thread: probe RCON every 3 s until it responds.

        If RCON never responds but the process stays alive for 90 s we
        assume the server is up (CS2 may not expose a TCP RCON listener
        depending on version / config).  Log noise is throttled to one
        message every 30 s so the log panel doesn't flood.
        """
        start     = time.time()
        last_log  = 0.0

        while self.running and self.boot_state == "booting":
            elapsed = time.time() - start

            # ── fallback: process alive after 90 s → mark ready anyway ───
            if elapsed >= 90 and self.proc and self.proc.poll() is None:
                self.boot_state = "ready"
                self.log(
                    "Server marked ONLINE after 90 s "
                    "(RCON TCP unreachable — map/mode changes via RCON may not work; "
                    "use TEST RCON button to diagnose)"
                )
                if self.on_state_change:
                    self.on_state_change()
                return

            # ── phase 1: raw TCP probe ─────────────────────────────────────
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.settimeout(2)
                    probe.connect((RCON_HOST, RCON_PORT))
            except Exception:
                # throttle log to once per 30 s
                now = time.time()
                if now - last_log >= 30:
                    self.log(
                        f"RCON not reachable at {RCON_HOST}:{RCON_PORT} "
                        f"({int(elapsed)}s elapsed) — server still loading "
                        "or TCP blocked. Server will be marked online after 90 s."
                    )
                    last_log = now
                time.sleep(3)
                continue

            # ── phase 2: port open — try RCON auth ────────────────────────
            try:
                self.rcon.execute("status")
                if self.running:
                    self.boot_state = "ready"
                    self.log("Server ready — RCON is responding")
                    if self.on_state_change:
                        self.on_state_change()
                return
            except ConnectionError as exc:
                now = time.time()
                if now - last_log >= 30:
                    self.log(f"RCON port open but handshake failed: {exc}")
                    last_log = now
                time.sleep(3)
            except Exception:
                time.sleep(3)

    def change_map(self, map_name: str, mode: str,
                   is_workshop: bool = False, caller: str = "local") -> None:
        def _do() -> None:
            s = MODE_SETTINGS.get(mode, _DEFAULT_MODE)
            try:
                # Use retry variant — server may still be loading (RCON not ready yet)
                self.log(f"[{caller}] Sending map change → {map_name} ({mode})…")
                self.rcon.execute_retry(f"game_type {s['game_type']}")
                self.rcon.execute_retry(f"game_mode {s['game_mode']}")
                self.rcon.execute_retry(f"maxplayers {s['maxplayers']}")
                rcon_cmd = (f"host_workshop_map {map_name}"
                            if is_workshop else f"changelevel {map_name}")
                resp = self.rcon.execute_retry(rcon_cmd)
                self.current_map  = map_name
                self.current_mode = mode
                self.log(f"[{caller}] Map → {map_name} ({mode})  {resp.strip() or 'OK'}")
                if self.on_state_change:
                    self.on_state_change()
            except ConnectionRefusedError:
                self.log("RCON error: server did not accept connection after retries "
                         "— is it still loading?")
            except Exception as exc:
                self.log(f"RCON error: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── server update ─────────────────────────────────────────────────────────

    def _installed_build(self) -> str | None:
        """Read the current build ID from steamapps/appmanifest_<id>.acf."""
        manifest = os.path.join(
            CS2_SERVER_DIR, "steamapps", f"appmanifest_{CS2_APP_ID}.acf"
        )
        try:
            with open(manifest, encoding="utf-8") as f:
                for line in f:
                    if '"buildid"' in line:
                        return line.split('"')[3].strip()
        except Exception:
            pass
        return None

    def _load_config(self) -> None:
        """Load persisted settings from oblivion_config.json."""
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            self.steam_username        = cfg.get("steam_username", "")
            self.steam_password        = cfg.get("steam_password", "")
            self.hostname              = cfg.get("hostname", "CS2 Dedicated Server")
            self.sv_password           = cfg.get("sv_password", "")
            self.tickrate_128          = bool(cfg.get("tickrate_128", False))
            self.auto_start            = bool(cfg.get("auto_start", False))
            self.bot_difficulty        = cfg.get("bot_difficulty", "Normal")
            self.max_players_override  = cfg.get("max_players_override", "")
            self.presets               = cfg.get("presets", {})
        except FileNotFoundError:
            pass   # first run — config doesn't exist yet
        except Exception as exc:
            self.log(f"Config load warning: {exc}")

    def save_config(self) -> None:
        """Persist all settings to oblivion_config.json next to the executable."""
        try:
            cfg = {
                "steam_username":       self.steam_username,
                "steam_password":       self.steam_password,
                "hostname":             self.hostname,
                "sv_password":          self.sv_password,
                "tickrate_128":         self.tickrate_128,
                "auto_start":           self.auto_start,
                "bot_difficulty":       self.bot_difficulty,
                "max_players_override": self.max_players_override,
                "presets":              self.presets,
            }
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            self.log(f"Config saved  ({_CONFIG_FILE})")
        except Exception as exc:
            self.log(f"Config save failed: {exc}")

    def check_update(self) -> None:
        """Query the Steam Web API to see if a server update is available.

        Runs on a daemon thread.  Logs the full API response so mismatches
        are visible.  Fires on_update_checked(available, installed, latest)
        on completion so the GUI can react appropriately.
        """
        def _do() -> None:
            manifest = os.path.join(
                CS2_SERVER_DIR, "steamapps", f"appmanifest_{CS2_APP_ID}.acf"
            )
            self.log(f"Update check: reading {manifest}")
            build = self._installed_build()
            if not build:
                self.log("Update check: appmanifest not found — server may not be installed")
                if self.on_update_checked:
                    self.on_update_checked(False, "unknown", "unknown")
                return
            self.log(f"Update check: installed build ID = {build}")
            self.log("Update check: querying Steam API…")
            try:
                url = (
                    "https://api.steampowered.com/ISteamApps/UpToDateCheck/v0001/"
                    f"?appid={CS2_APP_ID}&version={build}&format=json"
                )
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = json.loads(resp.read())
                r = data.get("response", {})
                self.log(f"Update check: API response → {r}")
                if not r.get("success"):
                    self.log("Update check: Steam API returned success=false — try again later")
                    if self.on_update_checked:
                        self.on_update_checked(False, build, "unknown")
                    return
                listed = r.get("version_is_listed", False)
                if not listed:
                    self.log(
                        f"Update check: build {build} is not in Steam's version list "
                        "(server may be ahead of the public branch, or build ID is wrong)"
                    )
                up_to_date = r.get("up_to_date", True)
                latest     = str(r.get("required_version") or build)
                if up_to_date:
                    self.log(f"Update check: up to date (build {build})")
                    self.update_available = False
                    if self.on_update_checked:
                        self.on_update_checked(False, build, build)
                else:
                    self.log(f"Update check: ⬆  UPDATE AVAILABLE")
                    self.log(f"  Installed : {build}")
                    self.log(f"  Latest    : {latest}")
                    self.update_available = True
                    if self.on_update_checked:
                        self.on_update_checked(True, build, latest)
            except Exception as exc:
                self.log(f"Update check failed: {exc}")
                if self.on_update_checked:
                    self.on_update_checked(False, build, "unknown")
        threading.Thread(target=_do, daemon=True).start()

    def run_update(self, on_done: Callable | None = None) -> None:
        """Run steamcmd +app_update and stream output to the log.

        Uses a dedicated reader thread + queue so silence periods between
        steamcmd output lines are detected and reported rather than just
        causing an invisible stall.
        """
        if self.running:
            self.log("Stop the server before updating.")
            if on_done:
                on_done()
            return

        def _do() -> None:
            self.log("─" * 48)
            self.log("  CS2 SERVER UPDATE")
            self.log(f"  steamcmd    → {STEAMCMD_PATH}")
            self.log(f"  install dir → {CS2_SERVER_DIR}")
            self.log(f"  app ID      → {CS2_APP_ID}")
            self.log("─" * 48)
            self.log("Launching steamcmd…")
            self.log("  Phase 1 — steamcmd initialises itself  (10–30 s, no output)")
            self.log("  Phase 2 — login anonymous")
            self.log("  Phase 3 — download changed files only  (progress shown below)")
            self.log("  First output line will appear once Phase 1 completes.")
            try:
                proc = subprocess.Popen(
                    [
                        STEAMCMD_PATH,
                        "+login",             "anonymous",
                        "+force_install_dir",  CS2_SERVER_DIR,
                        "+app_update",         CS2_APP_ID,   # no validate — changed files only
                        "+quit",
                    ],
                    # Raw bytes — steamcmd uses \r for progress bars so
                    # text-mode readline() would block mid-progress-bar.
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )

                out_q: queue.Queue = queue.Queue()

                def _reader() -> None:
                    buf = b""
                    while True:
                        chunk = proc.stdout.read(256)
                        if not chunk:
                            break
                        buf += chunk
                        while True:
                            for sep in (b"\r\n", b"\n", b"\r"):
                                idx = buf.find(sep)
                                if idx != -1:
                                    out_q.put(buf[:idx].decode("utf-8", errors="replace"))
                                    buf = buf[idx + len(sep):]
                                    break
                            else:
                                break
                    if buf:
                        out_q.put(buf.decode("utf-8", errors="replace"))
                    out_q.put(None)   # EOF sentinel

                threading.Thread(target=_reader, daemon=True).start()

                last_output   = time.time()
                warned_at: set[int] = set()

                while True:
                    try:
                        line = out_q.get(timeout=0.25)
                    except queue.Empty:
                        silence = int(time.time() - last_output)
                        for threshold in (15, 30, 60, 120):
                            if threshold not in warned_at and silence >= threshold:
                                warned_at.add(threshold)
                                self.log(
                                    f"  … still initialising "
                                    f"({silence}s without output) — steamcmd is working"
                                )
                        continue

                    if line is None:
                        break

                    last_output = time.time()
                    stripped    = line.strip()
                    if stripped:
                        self.log(f"[steamcmd] {stripped}")

                proc.wait()
                self.log("─" * 48)
                if proc.returncode == 0:
                    self.log("  UPDATE COMPLETE — server is up to date.")
                    self.update_available = False
                elif proc.returncode == 8:
                    self.log("  steamcmd exit code 8 — update failed.")
                    self.log("  Common causes:")
                    self.log("    • steamcmd needs to self-update: run steamcmd.exe")
                    self.log("      manually once with no arguments, then retry.")
                    self.log("    • Temporary Steam network issue — wait and retry.")
                    self.log("    • Corrupted steamcmd — re-download steamcmd.exe.")
                else:
                    self.log(f"  steamcmd exited with code {proc.returncode}")
                self.log("─" * 48)
            except FileNotFoundError:
                self.log(f"steamcmd not found: {STEAMCMD_PATH}")
            except Exception as exc:
                self.log(f"Update error: {exc}")
            finally:
                if on_done:
                    on_done()

        threading.Thread(target=_do, daemon=True).start()

    # ── workshop map update checker ───────────────────────────────────────────

    def check_workshop_updates(self) -> None:
        """Batch-query the Steam API for every downloaded workshop map.

        Compares each map's Steam  time_updated  timestamp against the local
        directory mtime.  Any map where Steam is newer is flagged as outdated.
        Runs on a daemon thread; results go to the log.
        """
        def _do() -> None:
            if not os.path.exists(WORKSHOP_DIR):
                self.log("Workshop update check: workshop directory not found")
                return
            ids = [f for f in os.listdir(WORKSHOP_DIR)
                   if os.path.isdir(os.path.join(WORKSHOP_DIR, f)) and f.isdigit()]
            if not ids:
                self.log("Workshop update check: no downloaded maps to check")
                return
            self.log(f"Workshop update check: checking {len(ids)} map(s)…")
            try:
                body = f"itemcount={len(ids)}"
                for i, wid in enumerate(ids):
                    body += f"&publishedfileids%5B{i}%5D={wid}"
                req = urllib.request.Request(
                    "https://api.steampowered.com"
                    "/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                    data=body.encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                items    = data.get("response", {}).get("publishedfiledetails", [])
                outdated = []
                up_to_date = 0
                for item in items:
                    wid      = item.get("publishedfileid", "")
                    title    = item.get("title") or wid
                    steam_ts = item.get("time_updated", 0)
                    local_dir = os.path.join(WORKSHOP_DIR, wid)
                    local_ts  = (int(os.path.getmtime(local_dir))
                                 if os.path.exists(local_dir) else 0)
                    if steam_ts > local_ts:
                        outdated.append((wid, title))
                    else:
                        up_to_date += 1
                if not outdated:
                    self.log(
                        f"Workshop maps: all {len(ids)} map(s) are up to date"
                    )
                else:
                    self.log(
                        f"Workshop maps: {len(outdated)} update(s) available "
                        f"— re-download via the DL button to update:"
                    )
                    for wid, title in outdated:
                        self.log(f"  ⬆  {title}  (ID: {wid})")
                    if up_to_date:
                        self.log(f"  ✓  {up_to_date} other map(s) are current")
            except Exception as exc:
                self.log(f"Workshop update check failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── plugin checker ────────────────────────────────────────────────────────

    def check_plugins(self) -> None:
        """Scan the CS2 addons directory for known plugin frameworks.

        Detects CounterStrikeSharp and Metamod:Source, lists installed CS#
        plugins, and queries GitHub for the latest CS# release.
        Runs on a daemon thread; results go to the log.
        """
        def _do() -> None:
            self.log("Plugin check: scanning addons directory…")

            # ── CounterStrikeSharp ────────────────────────────────────────
            css_dir = os.path.join(CS2_ADDONS_DIR, "counterstrikesharp")
            if os.path.exists(css_dir):
                self.log("CounterStrikeSharp: ✓ installed")
                plugins_dir = os.path.join(css_dir, "plugins")
                if os.path.exists(plugins_dir):
                    plugins = sorted(
                        p for p in os.listdir(plugins_dir)
                        if os.path.isdir(os.path.join(plugins_dir, p))
                    )
                    if plugins:
                        self.log(f"  Plugins ({len(plugins)}): {', '.join(plugins)}")
                    else:
                        self.log("  No plugins installed")
                try:
                    req = urllib.request.Request(
                        "https://api.github.com/repos/"
                        "roflmuffin/CounterStrikeSharp/releases/latest",
                        headers={"User-Agent": "OblivionServerTool/1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        rel = json.loads(resp.read())
                    tag  = rel.get("tag_name", "?")
                    date = rel.get("published_at", "")[:10]
                    self.log(f"  Latest release: {tag}  ({date})")
                    self.log(
                        "  → github.com/roflmuffin/"
                        "CounterStrikeSharp/releases/latest"
                    )
                except Exception as exc:
                    self.log(f"  GitHub check failed: {exc}")
            else:
                self.log("CounterStrikeSharp: not found in addons/")

            # ── Metamod:Source ────────────────────────────────────────────
            mm_dir = os.path.join(CS2_ADDONS_DIR, "metamod")
            if os.path.exists(mm_dir):
                self.log("Metamod:Source: ✓ installed")
            else:
                self.log("Metamod:Source: not found in addons/")

            self.log(f"Plugin check complete  (addons: {CS2_ADDONS_DIR})")
        threading.Thread(target=_do, daemon=True).start()

    # ── workshop download requests ─────────────────────────────────────────────

    def request_workshop_download(self, workshop_id: str,
                                   requester: str = "remote") -> None:
        with self._dl_lock:
            if any(r["id"] == workshop_id for r in self._dl_reqs):
                self.log(f"Download already pending: workshop map {workshop_id}")
                return
            self._dl_reqs.append({"id": workshop_id, "requester": requester})
        self.log(f"[{requester}] Requested download of workshop map {workshop_id}")
        if self.on_dl_request:
            self.on_dl_request(workshop_id, requester)

    def approve_download(self, workshop_id: str,
                          on_done: Callable[[bool], None] | None = None) -> None:
        def _run() -> None:
            has_creds  = bool(self.steam_username and self.steam_password)
            login_mode = f"user '{self.steam_username}'" if has_creds else "anonymous"

            self.log("─" * 48)
            self.log("  WORKSHOP MAP DOWNLOAD")
            self.log(f"  workshop ID → {workshop_id}")
            self.log(f"  steamcmd    → {STEAMCMD_PATH}")
            self.log(f"  install dir → {CS2_SERVER_DIR}")
            self.log(f"  login       → {login_mode}")
            self.log("─" * 48)
            self.log("Launching steamcmd…")
            self.log("  Phase 1 — steamcmd initialises itself  (10–30 s, no output)")
            self.log(f"  Phase 2 — login {login_mode}")
            self.log("  Phase 3 — downloading  (progress shown below)")
            self.log("  First output line will appear once Phase 1 completes.")
            if not has_creds:
                self.log("  i  No Steam account configured — using anonymous login.")
                self.log("     If this times out, add credentials via Steam Account.")
            TIMEOUT   = 300
            timed_out = False
            success   = False

            # Primitives for Steam Guard code handoff (background -> GUI -> back)
            guard_event                    = threading.Event()
            guard_code_holder: list[str]   = []
            guard_wait_start:  list[float] = []

            def _provide_code(code: str) -> None:
                guard_code_holder.clear()
                guard_code_holder.append(code)
                guard_event.set()

            try:
                login_args = (
                    ["+login", self.steam_username, self.steam_password]
                    if has_creds else
                    ["+login", "anonymous"]
                )
                proc = subprocess.Popen(
                    [STEAMCMD_PATH] + login_args + [
                        "+force_install_dir",      CS2_SERVER_DIR,
                        "+workshop_download_item", CS2_APP_ID, workshop_id,
                        "+quit",
                    ],
                    # Raw bytes — steamcmd uses \r for progress bars so
                    # text-mode readline() blocks forever mid-download.
                    # stdin pipe lets us write Steam Guard codes when prompted.
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                )

                out_q: queue.Queue = queue.Queue()

                def _reader() -> None:
                    # Read in small chunks and split on both \r and \n so
                    # steamcmd's carriage-return progress lines are visible.
                    buf = b""
                    while True:
                        chunk = proc.stdout.read(256)
                        if not chunk:
                            break
                        buf += chunk
                        # split on \r\n, \n, or lone \r
                        while True:
                            for sep in (b"\r\n", b"\n", b"\r"):
                                idx = buf.find(sep)
                                if idx != -1:
                                    line = buf[:idx]
                                    buf  = buf[idx + len(sep):]
                                    out_q.put(line.decode("utf-8", errors="replace"))
                                    break
                            else:
                                break   # no separator found — wait for more data
                    if buf:
                        out_q.put(buf.decode("utf-8", errors="replace"))
                    out_q.put(None)

                threading.Thread(target=_reader, daemon=True).start()

                start              = time.time()
                last_output        = time.time()
                warned_at: set[int] = set()
                waiting_for_guard  = False

                while True:
                    try:
                        line = out_q.get(timeout=0.25)
                    except queue.Empty:
                        if waiting_for_guard:
                            if guard_event.is_set():
                                # Code arrived from GUI dialog — send to steamcmd stdin
                                waiting_for_guard = False
                                code = guard_code_holder[0] if guard_code_holder else ""
                                if code:
                                    try:
                                        proc.stdin.write((code + "\n").encode())
                                        proc.stdin.flush()
                                    except Exception:
                                        pass
                                    self.log("  Steam Guard code submitted — continuing…")
                                else:
                                    self.log("  No code provided — cancelling download")
                                    proc.terminate()
                                    try:
                                        proc.wait(timeout=5)
                                    except Exception:
                                        proc.kill()
                                    timed_out = True
                                    break
                            elif (guard_wait_start
                                  and time.time() - guard_wait_start[0] >= 120):
                                self.log(
                                    "  Steam Guard code not entered after 120s"
                                    " — cancelling download"
                                )
                                proc.terminate()
                                try:
                                    proc.wait(timeout=5)
                                except Exception:
                                    proc.kill()
                                timed_out = True
                                break
                            continue   # don't apply TIMEOUT while waiting for Guard

                        elapsed = int(time.time() - start)
                        if elapsed >= TIMEOUT:
                            timed_out = True
                            self.log(
                                f"  ⚠  steamcmd timed out after {TIMEOUT}s "
                                "— killing process"
                            )
                            try:
                                proc.terminate()
                                proc.wait(timeout=5)
                            except Exception:
                                proc.kill()
                            break
                        silence = int(time.time() - last_output)
                        for threshold in (15, 30, 60, 120):
                            if threshold not in warned_at and silence >= threshold:
                                warned_at.add(threshold)
                                self.log(
                                    f"  … still working ({silence}s without output)"
                                )
                        continue

                    if line is None:
                        break

                    last_output = time.time()
                    stripped    = line.strip()
                    lower       = stripped.lower()

                    # Detect Steam Guard / 2FA prompt and hand off to the GUI
                    is_guard = "steam guard code:" in lower
                    is_2fa   = "two-factor code:" in lower
                    if (is_guard or is_2fa) and self.on_steam_guard:
                        prompt_type = "2fa" if is_2fa else "email"
                        self.log(f"  Steam Guard required ({prompt_type})")
                        self.log("  A dialog has appeared — enter your code to continue.")
                        waiting_for_guard = True
                        guard_event.clear()
                        guard_code_holder.clear()
                        guard_wait_start.clear()
                        guard_wait_start.append(time.time())
                        self.on_steam_guard(prompt_type, _provide_code)
                    elif stripped:
                        self.log(f"[steamcmd] {stripped}")

                if not timed_out:
                    proc.wait()

                self.log("─" * 48)
                dest = os.path.join(WORKSHOP_DIR, workshop_id)
                if timed_out:
                    self.log("  Download timed out — steamcmd was killed.")
                    if not has_creds:
                        self.log("  Possible causes:")
                        self.log(
                            "    • Workshop item requires a Steam account"
                            " (anonymous login not accepted)"
                        )
                        self.log("    • Steam servers throttling / temporary outage")
                        self.log("    • Wrong workshop ID")
                        self.log(
                            "  -> Add Steam credentials via Steam Account button"
                            " and retry."
                        )
                    else:
                        self.log("  Possible causes:")
                        self.log("    • Steam servers throttling / temporary outage")
                        self.log("    • Wrong workshop ID")
                elif os.path.isdir(dest) and os.listdir(dest):
                    self.log(f"  DOWNLOAD COMPLETE — workshop map {workshop_id}")
                    self.log(f"  Files saved to: {dest}")
                    success = True
                elif proc.returncode == 0:
                    # steamcmd said OK but files aren't where expected
                    self.log(f"  steamcmd exited OK but no files found at: {dest}")
                    self.log("  The map may have downloaded to a different path,")
                    self.log(f"  or the workshop ID {workshop_id} may be invalid.")
                else:
                    self.log(f"  steamcmd exited with code {proc.returncode}")
                    self.log("  Check the workshop ID is correct and try again.")
                self.log("─" * 48)
            except FileNotFoundError:
                success = False
                self.log(f"steamcmd not found: {STEAMCMD_PATH}")
            except Exception as exc:
                success = False
                self.log(f"Download error: {exc}")
            finally:
                with self._dl_lock:
                    self._dl_reqs = [r for r in self._dl_reqs
                                     if r["id"] != workshop_id]
                if on_done:
                    on_done(success)
        threading.Thread(target=_run, daemon=True).start()

    def reject_download(self, workshop_id: str, requester: str = "") -> None:
        with self._dl_lock:
            self._dl_reqs = [r for r in self._dl_reqs if r["id"] != workshop_id]
        note = f" (by {requester})" if requester else ""
        self.log(f"Download rejected: workshop map {workshop_id}{note}")

    # ── public IP ─────────────────────────────────────────────────────────────

    def check_public_ip(self) -> None:
        """Async: fetch machine's public IP from ipify and fire on_public_ip."""
        def _do() -> None:
            try:
                with urllib.request.urlopen(
                        "https://api.ipify.org?format=json", timeout=8) as r:
                    ip = json.loads(r.read()).get("ip", "")
                if ip:
                    self.public_ip = ip
                    if self.on_public_ip:
                        self.on_public_ip(ip)
            except Exception as exc:
                self.log(f"Public IP fetch failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── player management ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_players(status_output: str) -> list[dict]:
        """Parse RCON 'status' output into a list of player dicts."""
        players: list[dict] = []
        # CS2 status line format:
        #  #  3 "PlayerName"  STEAM_1:1:12345  00:01:02  64  0  active
        pattern = re.compile(
            r"^#\s+(\d+)\s+"           # slot/userid
            r'"([^"]*)"'               # name (quoted)
            r"\s+(STEAM_\S+|\[U:\S+)"  # steamid
            r"\s+(\S+)"                # time
            r"\s+(\d+)",               # ping
            re.MULTILINE,
        )
        for m in pattern.finditer(status_output):
            players.append({
                "userid":  m.group(1),
                "name":    m.group(2),
                "steamid": m.group(3),
                "time":    m.group(4),
                "ping":    m.group(5),
            })
        return players

    def get_players(self, callback: Callable[[list[dict]], None]) -> None:
        """Async RCON 'status' → parse → callback(list[dict])."""
        def _do() -> None:
            try:
                out = self.rcon.execute("status")
                callback(self._parse_players(out))
            except Exception as exc:
                self.log(f"get_players error: {exc}")
                callback([])
        threading.Thread(target=_do, daemon=True).start()

    def kick_player(self, userid: str, name: str = "") -> None:
        def _do() -> None:
            try:
                self.rcon.execute(f"kickid {userid}")
                self.log(f"Kicked player: {name or userid}")
            except Exception as exc:
                self.log(f"Kick failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def ban_player(self, steamid: str, name: str = "",
                   duration: int = 0) -> None:
        """Ban by SteamID.  duration=0 is permanent."""
        def _do() -> None:
            try:
                self.rcon.execute(f"banid {duration} {steamid}")
                self.rcon.execute("writeid")
                self.log(
                    f"Banned: {name or steamid} "
                    f"({'permanent' if duration == 0 else f'{duration} min'})"
                )
            except Exception as exc:
                self.log(f"Ban failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def unban_player(self, steamid: str) -> None:
        def _do() -> None:
            try:
                self.rcon.execute(f"removeid {steamid}")
                self.rcon.execute("writeid")
                self.log(f"Unbanned: {steamid}")
            except Exception as exc:
                self.log(f"Unban failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def get_ban_list(self, callback: Callable[[list[str]], None]) -> None:
        def _do() -> None:
            try:
                out = self.rcon.execute("listid")
                lines = [l.strip() for l in out.splitlines() if l.strip()]
                callback(lines)
            except Exception as exc:
                self.log(f"get_ban_list error: {exc}")
                callback([])
        threading.Thread(target=_do, daemon=True).start()

    # ── server chat ───────────────────────────────────────────────────────────

    def server_say(self, msg: str) -> None:
        def _do() -> None:
            try:
                self.rcon.execute(f"say {msg}")
                self.log(f"[chat] {msg}")
            except Exception as exc:
                self.log(f"server_say error: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── friendly fire ─────────────────────────────────────────────────────────

    def set_friendly_fire(self, enabled: bool) -> None:
        """Toggle mp_friendlyfire + mp_autokick so FF works without kick."""
        def _do() -> None:
            try:
                self.rcon.execute(f"mp_friendlyfire {1 if enabled else 0}")
                # autokick 0 = don't kick for team damage
                self.rcon.execute(f"mp_autokick {0 if enabled else 1}")
                self._ff_enabled = enabled
                self.log(
                    f"Friendly fire {'ENABLED (autokick off)' if enabled else 'DISABLED (autokick on)'}"
                )
            except Exception as exc:
                self.log(f"Friendly fire toggle failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── round controls ────────────────────────────────────────────────────────

    def restart_round(self) -> None:
        def _do() -> None:
            try:
                self.rcon.execute("mp_restartgame 1")
                self.log("Round restarted")
            except Exception as exc:
                self.log(f"Restart round failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def end_warmup(self) -> None:
        def _do() -> None:
            try:
                self.rcon.execute("mp_warmup_end")
                self.log("Warmup ended")
            except Exception as exc:
                self.log(f"End warmup failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def pause_match(self) -> None:
        def _do() -> None:
            try:
                self.rcon.execute("mp_pause_match")
                self.log("Match paused")
            except Exception as exc:
                self.log(f"Pause failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def unpause_match(self) -> None:
        def _do() -> None:
            try:
                self.rcon.execute("mp_unpause_match")
                self.log("Match unpaused")
            except Exception as exc:
                self.log(f"Unpause failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── bot management ────────────────────────────────────────────────────────

    _BOT_DIFF = {"Easy": "0", "Normal": "1", "Hard": "2", "Expert": "3"}

    def add_bots(self, count: int = 1) -> None:
        def _do() -> None:
            diff = self._BOT_DIFF.get(self.bot_difficulty, "1")
            try:
                self.rcon.execute(f"bot_difficulty {diff}")
                for _ in range(count):
                    self.rcon.execute("bot_add")
                self.log(f"Added {count} bot(s) — difficulty: {self.bot_difficulty}")
            except Exception as exc:
                self.log(f"Add bot failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def kick_bots(self) -> None:
        def _do() -> None:
            try:
                self.rcon.execute("bot_kick")
                self.log("All bots kicked")
            except Exception as exc:
                self.log(f"Kick bots failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── workshop map name fetching ────────────────────────────────────────────

    def fetch_workshop_names(self, ids: list[str],
                              on_done: Callable | None = None) -> None:
        """Async: batch-query Steam API for human-readable map titles.

        Results are stored in _map_name_cache.  on_done() is called when
        complete so the GUI can re-populate its dropdown.
        """
        if not ids:
            if on_done:
                on_done()
            return

        def _do() -> None:
            try:
                body = f"itemcount={len(ids)}"
                for i, wid in enumerate(ids):
                    body += f"&publishedfileids%5B{i}%5D={wid}"
                req = urllib.request.Request(
                    "https://api.steampowered.com"
                    "/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                    data=body.encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                for item in data.get("response", {}).get("publishedfiledetails", []):
                    wid   = item.get("publishedfileid", "")
                    title = item.get("title", "").strip()
                    if wid and title:
                        self._map_name_cache[wid] = title
            except Exception as exc:
                self.log(f"Workshop name fetch failed: {exc}")
            finally:
                if on_done:
                    on_done()
        threading.Thread(target=_do, daemon=True).start()


# ── Web template ───────────────────────────────────────────────────────────────
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
/* PIN login */
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
    if (d.ok) { st.className = 'req-st req-ok'; st.textContent = 'Request sent — waiting for server owner approval'; }
    else { st.className = 'req-st req-err'; st.textContent = d.error || 'Error'; }
  }).catch(() => { st.className = 'req-st req-err'; st.textContent = 'Network error'; });
}
</script>
{% endif %}
</body></html>"""


# ── Workshop map loader ────────────────────────────────────────────────────────
def _load_workshop() -> list[str]:
    if not os.path.exists(WORKSHOP_DIR):
        return []
    return sorted(
        f for f in os.listdir(WORKSHOP_DIR)
        if os.path.isdir(os.path.join(WORKSHOP_DIR, f))
    )


# ── PIN rate limiter ───────────────────────────────────────────────────────────
_MAX_ATTEMPTS  = 5
_LOCKOUT_SECS  = 300
_attempts:      dict[str, dict] = {}
_attempts_lock  = threading.Lock()   # ← was missing; Flask runs threaded


def _prune_attempts() -> None:
    """Drop cleared or expired lockout records to prevent unbounded growth."""
    now = time.time()
    with _attempts_lock:
        stale = [ip for ip, r in _attempts.items()
                 if r["count"] < _MAX_ATTEMPTS or r["until"] <= now]
        for ip in stale:
            del _attempts[ip]


def _check_lockout(ip: str) -> int:
    """Return seconds remaining in lockout, or 0 if clear."""
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


# ── Flask app ──────────────────────────────────────────────────────────────────
def create_flask(core: AppCore) -> Flask:
    app = Flask(__name__)

    def require_auth(f: Callable) -> Callable:
        """Decorator: return 401 if the request has no valid session cookie."""
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if request.cookies.get("adm") != ADMIN_PIN:
                return jsonify({"error": "unauthorized"}), 401
            return f(*args, **kwargs)
        return wrapper

    # ── routes ────────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return render_template_string(
            _WEB,
            authed        = (request.cookies.get("adm") == ADMIN_PIN),
            official_maps = OFFICIAL_MAPS,
            workshop_maps = _load_workshop(),
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


# ── GUI ────────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


class CS2GUI:
    # ── Colour palette ────────────────────────────────────────────────────────
    BG       = "#09090e"
    CARD     = "#0f0f16"
    DEEP     = "#060609"
    BORDER   = "#1c1c28"
    ACCENT   = "#a78bfa"
    ACCENT_H = "#8b5cf6"
    BLUE     = "#4e9aff"
    BLUE_H   = "#3b82f6"
    STOP     = "#e05c6b"
    STOP_H   = "#be2a3e"
    GREEN    = "#22c55e"
    ORANGE   = "#f59e0b"
    RED      = "#ef4444"
    TEXT     = "#e8e8f4"
    SUB      = "#9090aa"   # lifted from #6b6b80 — readable at small sizes

    def __init__(self, core: AppCore) -> None:
        self.core = core
        self._uptime_start:        float | None = None
        self._pulse_step:          int          = 0
        self._manual_update_check: bool         = False
        self._ff_btn:              ctk.CTkButton | None = None   # friendly fire toggle

        self.root = ctk.CTk()
        self.root.title("Oblivion Server Tool")
        self.root.geometry("1060x800")
        self.root.configure(fg_color=self.BG)
        self.root.resizable(True, True)
        self.root.minsize(860, 700)

        self._build()
        self._start_monitor()
        self._tick_uptime()

        # Register callbacks after widgets are built
        self.core.on_log            = lambda e:           self.root.after(0, self._append_log, e)
        self.core.on_dl_request     = lambda wid, ip:     self.root.after(0, self._show_dl_dialog, wid, ip)
        self.core.on_state_change   = lambda:             self.root.after(0, self._on_core_state_change)
        self.core.on_update_checked = lambda av, ins, lat: self.root.after(
            0, self._on_update_checked, av, ins, lat
        )
        self.core.on_steam_guard    = lambda t, cb: self.root.after(
            0, self._show_guard_dialog, t, cb
        )
        self.core.on_public_ip      = lambda ip: self.root.after(
            0, self._on_public_ip, ip
        )

    # ── top-level layout ──────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── thin accent stripe ──
        ctk.CTkFrame(self.root, fg_color=self.ACCENT,
                     corner_radius=0, height=2).pack(fill="x")

        # ── header bar ──
        hdr = ctk.CTkFrame(self.root, fg_color=self.CARD, corner_radius=0, height=54)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        brand = ctk.CTkFrame(hdr, fg_color="transparent")
        brand.pack(side="left", padx=20, fill="y")
        ctk.CTkLabel(brand, text="OBLIVION",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=self.ACCENT).pack(side="left")
        ctk.CTkLabel(brand, text="  SERVER TOOL",
                     font=ctk.CTkFont(size=12),
                     text_color=self.SUB).pack(side="left", pady=(6, 0))
        self._dot = ctk.CTkLabel(hdr, text="⬤  OFFLINE",
                                  font=ctk.CTkFont(size=12), text_color=self.RED)
        self._dot.pack(side="right", padx=20)

        # ── status bar ──
        sb = ctk.CTkFrame(self.root, fg_color=self.DEEP, corner_radius=0, height=34)
        sb.pack(fill="x")
        sb.pack_propagate(False)
        sf = ctk.CTkFont(size=12)
        ctk.CTkLabel(sb, text="Map:",    text_color=self.SUB, font=sf).pack(side="left", padx=(16, 3))
        self._sb_map = ctk.CTkLabel(sb, text="—", text_color=self.TEXT, font=sf)
        self._sb_map.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(sb, text="Mode:",   text_color=self.SUB, font=sf).pack(side="left", padx=(0, 3))
        self._sb_mode = ctk.CTkLabel(sb, text="—", text_color=self.TEXT, font=sf)
        self._sb_mode.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(sb, text="Uptime:", text_color=self.SUB, font=sf).pack(side="left", padx=(0, 3))
        self._sb_uptime = ctk.CTkLabel(sb, text="—", text_color=self.TEXT, font=sf)
        self._sb_uptime.pack(side="left")
        ctk.CTkLabel(sb,
                     text=f"Remote admin → http://localhost:{FLASK_PORT}",
                     text_color=self.SUB, font=sf).pack(side="right", padx=(4, 16))

        # Clickable connect string — copies to clipboard on click
        conn_lbl = ctk.CTkLabel(sb,
                                text=f"connect {RCON_HOST}:{RCON_PORT}",
                                text_color=self.SUB, font=sf, cursor="hand2")
        conn_lbl.pack(side="right", padx=(16, 4))
        conn_lbl.bind("<Button-1>", lambda _e: self._copy_connect_string())

        # Public IP label (fetched async; clickable to copy)
        self._pub_ip_lbl = ctk.CTkLabel(sb, text="ext: fetching…",
                                         text_color=self.SUB, font=sf, cursor="hand2")
        self._pub_ip_lbl.pack(side="right", padx=(16, 4))
        self._pub_ip_lbl.bind("<Button-1>", lambda _e: self._copy_public_ip())

        # ── log panel — packed FIRST to side="bottom" so it always gets its
        #    full height.  The content area (expand=True) then fills what's left.
        lp = ctk.CTkFrame(self.root, fg_color=self.CARD, corner_radius=12)
        lp.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

        # Log header row: section label on left, Export + Clear buttons on right
        log_hdr = ctk.CTkFrame(lp, fg_color="transparent")
        log_hdr.pack(fill="x", padx=14, pady=(14, 4))
        ctk.CTkLabel(log_hdr, text="LOG",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear", width=52, height=22,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=11),
            corner_radius=6, command=self._clear_log,
        ).pack(side="right")
        ctk.CTkButton(
            log_hdr, text="Export", width=60, height=22,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=11),
            corner_radius=6, command=self._export_log,
        ).pack(side="right", padx=(0, 6))

        self._logbox = ctk.CTkTextbox(
            lp, fg_color=self.DEEP, text_color="#a8c4bf",
            font=ctk.CTkFont(family="Consolas", size=12),
            height=140, state="disabled",
        )
        self._logbox.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        # ── main two-column area ──
        content = ctk.CTkFrame(self.root, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=10)
        content.columnconfigure(0, weight=2, minsize=300)
        content.columnconfigure(1, weight=3, minsize=420)
        content.rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color=self.CARD, corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._build_config_panel(left)

        right = ctk.CTkFrame(content, fg_color=self.CARD, corner_radius=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._build_controls_panel(right)

    # ── left panel: maps & config ─────────────────────────────────────────────

    def _build_config_panel(self, parent: ctk.CTkFrame) -> None:
        _cb = dict(
            fg_color=self.DEEP, button_color=self.BORDER,
            border_color=self.BORDER, dropdown_fg_color=self.CARD,
            dropdown_hover_color=self.BORDER, text_color=self.TEXT,
            dropdown_text_color=self.TEXT, button_hover_color="#2a2a40",
            font=ctk.CTkFont(size=13),
        )

        self._sec(parent, "MAPS & MODE")

        self._lbl(parent, "Official Map")
        self._off_var = ctk.StringVar(value=OFFICIAL_MAPS[0])
        self._off_cb = ctk.CTkComboBox(
            parent, values=OFFICIAL_MAPS, variable=self._off_var,
            command=lambda _: self._wk_var.set(""), **_cb)
        self._off_cb.pack(fill="x", padx=14, pady=(0, 4))

        self._lbl(parent, "Workshop Map")
        wkrow = ctk.CTkFrame(parent, fg_color="transparent")
        wkrow.pack(fill="x", padx=14, pady=(0, 4))
        self._wk_var = ctk.StringVar(value="")
        self._wk_cb = ctk.CTkComboBox(
            wkrow, values=[], variable=self._wk_var,
            command=lambda _: self._off_var.set(""), **_cb)
        self._wk_cb.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            wkrow, text="↺", width=36, height=34,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.TEXT, font=ctk.CTkFont(size=15),
            command=self._refresh_wk,
        ).pack(side="right", padx=(6, 0))

        self._lbl(parent, "Game Mode")
        self._mode_var = ctk.StringVar(value="Competitive")
        self._mode_cb = ctk.CTkComboBox(
            parent, values=GAME_MODES, variable=self._mode_var,
            command=self._on_mode_change, **_cb,
        )
        self._mode_cb.pack(fill="x", padx=14, pady=(0, 4))

        # hint: shown when mode has non-standard or no official maps
        self._mode_hint_lbl = ctk.CTkLabel(
            parent, text="", text_color=self.SUB,
            font=ctk.CTkFont(size=12), anchor="w",
        )
        self._mode_hint_lbl.pack(fill="x", padx=14, pady=(0, 4))

        # browse Steam Workshop — label updates with the selected mode
        self._browse_btn = ctk.CTkButton(
            parent, text="🔍  Browse Workshop Maps",
            height=30, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._browse_workshop,
        )
        self._browse_btn.pack(fill="x", padx=14, pady=(0, 12))

        # divider
        ctk.CTkFrame(parent, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=14, pady=(0, 10))

        # local workshop download
        self._sec_sub(parent, "DOWNLOAD WORKSHOP MAP")
        self._lbl(parent, "Steam Workshop ID")
        ws_row = ctk.CTkFrame(parent, fg_color="transparent")
        ws_row.pack(fill="x", padx=14, pady=(0, 4))
        self._wsid_var = ctk.StringVar()
        ctk.CTkEntry(
            ws_row, textvariable=self._wsid_var,
            placeholder_text="e.g. 3070720081",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            ws_row, text="DL", width=52, height=34,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._local_dl,
        ).pack(side="right", padx=(6, 0))
        self._wsid_lbl = ctk.CTkLabel(
            parent, text="", text_color=self.SUB,
            font=ctk.CTkFont(size=12))
        self._wsid_lbl.pack(anchor="w", padx=14, pady=(0, 6))

        ctk.CTkButton(
            parent, text="↻  Check Map Updates",
            height=30, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._check_map_updates,
        ).pack(fill="x", padx=14, pady=(0, 12))

    # ── right panel: tabbed controls ─────────────────────────────────────────

    def _build_controls_panel(self, parent: ctk.CTkFrame) -> None:
        tabs = ctk.CTkTabView(
            parent,
            fg_color=self.CARD,
            segmented_button_fg_color=self.DEEP,
            segmented_button_selected_color=self.ACCENT,
            segmented_button_selected_hover_color=self.ACCENT_H,
            segmented_button_unselected_color=self.DEEP,
            segmented_button_unselected_hover_color=self.BORDER,
            text_color=self.TEXT,
            text_color_disabled=self.SUB,
        )
        tabs.pack(fill="both", expand=True, padx=0, pady=0)
        tabs.add("Controls")
        tabs.add("Players")
        tabs.add("Config")
        tabs.add("Console")

        self._build_tab_controls(tabs.tab("Controls"))
        self._build_tab_players(tabs.tab("Players"))
        self._build_tab_config(tabs.tab("Config"))
        self._build_tab_console(tabs.tab("Console"))

    # ── TAB: Controls ─────────────────────────────────────────────────────────

    def _build_tab_controls(self, parent: ctk.CTkFrame) -> None:
        _bf = {"font": ctk.CTkFont(size=12, weight="bold"), "height": 36, "corner_radius": 8}

        self._start_btn = ctk.CTkButton(
            parent, text="▶   START SERVER",
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", command=self._start, **_bf)
        self._start_btn.pack(fill="x", padx=12, pady=(10, 5))

        self._stop_btn = ctk.CTkButton(
            parent, text="■   STOP SERVER",
            fg_color=self.STOP, hover_color=self.STOP_H,
            state="disabled", command=self._stop, **_bf)
        self._stop_btn.pack(fill="x", padx=12, pady=(0, 5))

        self._chg_btn = ctk.CTkButton(
            parent, text="⟳   CHANGE MAP / MODE",
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            state="disabled", command=self._change, **_bf)
        self._chg_btn.pack(fill="x", padx=12, pady=(0, 5))

        self._upd_btn = ctk.CTkButton(
            parent, text="⟳   CHECK FOR UPDATE",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, command=self._check_update_btn, **_bf)
        self._upd_btn.pack(fill="x", padx=12, pady=(0, 5))

        ctk.CTkButton(
            parent, text="⚙  Check Plugins",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, command=self._check_plugins, **_bf,
        ).pack(fill="x", padx=12, pady=(0, 5))

        ctk.CTkButton(
            parent, text="🔑  Steam Account",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, command=self._show_steam_account_dialog, **_bf,
        ).pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkFrame(parent, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(parent, text="QUICK ACTIONS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 6))

        # Friendly fire toggle — colour reflects current state
        self._ff_btn = ctk.CTkButton(
            parent, text="Friendly Fire: OFF",
            height=32, corner_radius=8,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12, weight="bold"),
            command=self._toggle_ff,
        )
        self._ff_btn.pack(fill="x", padx=12, pady=(0, 5))

        # Chat broadcast
        chat_row = ctk.CTkFrame(parent, fg_color="transparent")
        chat_row.pack(fill="x", padx=12, pady=(0, 5))
        self._chat_var = ctk.StringVar()
        chat_ent = ctk.CTkEntry(
            chat_row, textvariable=self._chat_var,
            placeholder_text="Broadcast chat message…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=12),
        )
        chat_ent.pack(side="left", fill="x", expand=True)
        chat_ent.bind("<Return>", lambda _e: self._send_chat())
        ctk.CTkButton(
            chat_row, text="Send", width=60, height=32,
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._send_chat,
        ).pack(side="right", padx=(5, 0))

        # Round control row
        rc = ctk.CTkFrame(parent, fg_color="transparent")
        rc.pack(fill="x", padx=12, pady=(0, 5))
        _rb = {"height": 30, "corner_radius": 6,
               "fg_color": self.BORDER, "hover_color": "#2a2a40",
               "text_color": self.SUB, "font": ctk.CTkFont(size=11)}
        ctk.CTkButton(rc, text="Restart Round", command=lambda: self.core.restart_round(),
                      **_rb).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(rc, text="End Warmup", command=lambda: self.core.end_warmup(),
                      **_rb).pack(side="left", fill="x", expand=True)

        rc2 = ctk.CTkFrame(parent, fg_color="transparent")
        rc2.pack(fill="x", padx=12, pady=(0, 5))
        ctk.CTkButton(rc2, text="Pause", command=lambda: self.core.pause_match(),
                      **_rb).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(rc2, text="Unpause", command=lambda: self.core.unpause_match(),
                      **_rb).pack(side="left", fill="x", expand=True)

    # ── TAB: Players ──────────────────────────────────────────────────────────

    def _build_tab_players(self, parent: ctk.CTkFrame) -> None:
        # Header row: refresh + auto-refresh
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkButton(
            hdr, text="↺ Refresh", width=90, height=28,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            corner_radius=6, command=self._refresh_players,
        ).pack(side="left")
        self._auto_refresh_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            hdr, text="Auto (10s)", variable=self._auto_refresh_var,
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            border_color=self.BORDER, checkmark_color="#0d0d14",
            command=self._toggle_auto_refresh,
        ).pack(side="left", padx=(10, 0))

        self._player_status_lbl = ctk.CTkLabel(
            parent, text="", text_color=self.SUB, font=ctk.CTkFont(size=12))
        self._player_status_lbl.pack(anchor="w", padx=12, pady=(0, 4))

        # Scrollable player list
        self._player_scroll = ctk.CTkScrollableFrame(
            parent, fg_color=self.DEEP, corner_radius=8, height=140)
        self._player_scroll.pack(fill="x", padx=12, pady=(0, 8))
        self._player_rows: list[ctk.CTkFrame] = []

        ctk.CTkFrame(parent, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(parent, text="BAN MANAGEMENT",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))

        # Manual ban row
        ban_row = ctk.CTkFrame(parent, fg_color="transparent")
        ban_row.pack(fill="x", padx=12, pady=(0, 4))
        self._ban_id_var = ctk.StringVar()
        ctk.CTkEntry(
            ban_row, textvariable=self._ban_id_var,
            placeholder_text="SteamID to ban…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            ban_row, text="Ban", width=60, height=32,
            fg_color=self.STOP, hover_color=self.STOP_H,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._manual_ban,
        ).pack(side="right", padx=(5, 0))

        ctk.CTkButton(
            parent, text="↺ Refresh Ban List", height=28,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            corner_radius=6, command=self._refresh_ban_list,
        ).pack(fill="x", padx=12, pady=(0, 4))

        self._ban_scroll = ctk.CTkScrollableFrame(
            parent, fg_color=self.DEEP, corner_radius=8, height=100)
        self._ban_scroll.pack(fill="x", padx=12, pady=(0, 8))
        self._ban_rows: list[ctk.CTkFrame] = []
        self._auto_refresh_after: str | None = None

    # ── TAB: Config ───────────────────────────────────────────────────────────

    def _build_tab_config(self, parent: ctk.CTkFrame) -> None:
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=0, pady=0)
        p = scroll   # alias

        ctk.CTkLabel(p, text="SERVER SETTINGS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(10, 4))

        ctk.CTkLabel(p, text="Server Hostname",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12)
        self._hostname_var = ctk.StringVar(value=self.core.hostname)
        ctk.CTkEntry(p, textvariable=self._hostname_var,
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, font=ctk.CTkFont(size=12),
                     ).pack(fill="x", padx=12, pady=(2, 6))

        ctk.CTkLabel(p, text="Server Password  (blank = public)",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12)
        pw_row = ctk.CTkFrame(p, fg_color="transparent")
        pw_row.pack(fill="x", padx=12, pady=(2, 6))
        self._svpw_var = ctk.StringVar(value=self.core.sv_password)
        ctk.CTkEntry(pw_row, textvariable=self._svpw_var, show="●",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, font=ctk.CTkFont(size=12),
                     ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            pw_row, text="Set Live", width=72, height=30,
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._set_sv_password_live,
        ).pack(side="right", padx=(5, 0))

        ctk.CTkLabel(p, text="Max Players Override  (blank = mode default)",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12)
        self._maxp_var = ctk.StringVar(value=self.core.max_players_override)
        ctk.CTkEntry(p, textvariable=self._maxp_var,
                     placeholder_text="e.g. 16",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, placeholder_text_color=self.SUB,
                     font=ctk.CTkFont(size=12),
                     ).pack(fill="x", padx=12, pady=(2, 6))

        # Checkboxes row
        chk_row = ctk.CTkFrame(p, fg_color="transparent")
        chk_row.pack(fill="x", padx=12, pady=(0, 6))
        self._tick128_var = ctk.BooleanVar(value=self.core.tickrate_128)
        ctk.CTkCheckBox(
            chk_row, text="Tickrate 128  (legacy, subtick handles timing)", variable=self._tick128_var,
            text_color=self.TEXT, font=ctk.CTkFont(size=12),
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            border_color=self.BORDER, checkmark_color="#0d0d14",
        ).pack(side="left", padx=(0, 20))
        self._autostart_var = ctk.BooleanVar(value=self.core.auto_start)
        ctk.CTkCheckBox(
            chk_row, text="Auto-start on launch", variable=self._autostart_var,
            text_color=self.TEXT, font=ctk.CTkFont(size=12),
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            border_color=self.BORDER, checkmark_color="#0d0d14",
        ).pack(side="left")

        ctk.CTkButton(
            p, text="💾  Save Settings", height=34, corner_radius=8,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", font=ctk.CTkFont(size=12, weight="bold"),
            command=self._save_server_settings,
        ).pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkFrame(p, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(p, text="BOTS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))

        bot_row = ctk.CTkFrame(p, fg_color="transparent")
        bot_row.pack(fill="x", padx=12, pady=(0, 4))
        _bb = {"height": 30, "corner_radius": 6,
               "fg_color": self.BORDER, "hover_color": "#2a2a40",
               "text_color": self.TEXT, "font": ctk.CTkFont(size=12, weight="bold")}
        ctk.CTkButton(bot_row, text="+1 Bot",
                      command=lambda: self.core.add_bots(1), **_bb,
                      ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(bot_row, text="+5 Bots",
                      command=lambda: self.core.add_bots(5), **_bb,
                      ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(bot_row, text="Kick All",
                      fg_color=self.STOP, hover_color=self.STOP_H,
                      text_color=self.TEXT, font=ctk.CTkFont(size=12, weight="bold"),
                      height=30, corner_radius=6,
                      command=self.core.kick_bots,
                      ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(p, text="Bot Difficulty",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=12, pady=(4, 2))
        self._bot_diff_var = ctk.StringVar(value=self.core.bot_difficulty)
        ctk.CTkComboBox(
            p, values=["Easy", "Normal", "Hard", "Expert"],
            variable=self._bot_diff_var,
            fg_color=self.DEEP, button_color=self.BORDER,
            border_color=self.BORDER, dropdown_fg_color=self.CARD,
            dropdown_hover_color=self.BORDER, text_color=self.TEXT,
            dropdown_text_color=self.TEXT, button_hover_color="#2a2a40",
            font=ctk.CTkFont(size=12),
            command=lambda v: setattr(self.core, "bot_difficulty", v),
        ).pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkFrame(p, fg_color=self.BORDER, height=1,
                     corner_radius=0).pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(p, text="CONFIG PRESETS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=12, pady=(0, 4))

        preset_save_row = ctk.CTkFrame(p, fg_color="transparent")
        preset_save_row.pack(fill="x", padx=12, pady=(0, 4))
        self._preset_name_var = ctk.StringVar()
        ctk.CTkEntry(
            preset_save_row, textvariable=self._preset_name_var,
            placeholder_text="Preset name…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            preset_save_row, text="Save", width=60, height=30,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14", font=ctk.CTkFont(size=12, weight="bold"),
            command=self._save_preset,
        ).pack(side="right", padx=(5, 0))

        preset_load_row = ctk.CTkFrame(p, fg_color="transparent")
        preset_load_row.pack(fill="x", padx=12, pady=(0, 6))
        preset_names = list(self.core.presets.keys()) or [""]
        self._preset_sel_var = ctk.StringVar(value=preset_names[0])
        self._preset_cb = ctk.CTkComboBox(
            preset_load_row, values=preset_names,
            variable=self._preset_sel_var,
            fg_color=self.DEEP, button_color=self.BORDER,
            border_color=self.BORDER, dropdown_fg_color=self.CARD,
            dropdown_hover_color=self.BORDER, text_color=self.TEXT,
            dropdown_text_color=self.TEXT, button_hover_color="#2a2a40",
            font=ctk.CTkFont(size=12),
        )
        self._preset_cb.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            preset_load_row, text="Load", width=55, height=30,
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._load_preset,
        ).pack(side="right", padx=(5, 0))
        ctk.CTkButton(
            preset_load_row, text="Del", width=40, height=30,
            fg_color=self.STOP, hover_color=self.STOP_H,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._delete_preset,
        ).pack(side="right", padx=(5, 0))

    # ── TAB: Console ──────────────────────────────────────────────────────────

    def _build_tab_console(self, parent: ctk.CTkFrame) -> None:
        self._rcon_box = ctk.CTkTextbox(
            parent, fg_color=self.DEEP, text_color="#a8c4bf",
            font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled", wrap="word",
        )
        self._rcon_box.pack(fill="both", expand=True, padx=12, pady=(10, 6))

        cmd_row = ctk.CTkFrame(parent, fg_color="transparent")
        cmd_row.pack(fill="x", padx=12, pady=(0, 6))
        self._rcon_var = ctk.StringVar()
        rcon_ent = ctk.CTkEntry(
            cmd_row, textvariable=self._rcon_var,
            placeholder_text="Enter RCON command…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=13),
        )
        rcon_ent.pack(side="left", fill="x", expand=True)
        rcon_ent.bind("<Return>", lambda _e: self._send_rcon())
        ctk.CTkButton(
            cmd_row, text="SEND", width=72, height=34,
            fg_color=self.BLUE, hover_color=self.BLUE_H,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._send_rcon,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            parent, text=f"⚑  TEST RCON  ({RCON_HOST}:{RCON_PORT})",
            height=30, corner_radius=6,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=self._test_rcon,
        ).pack(fill="x", padx=12, pady=(0, 10))

    # ── widget helpers ────────────────────────────────────────────────────────

    def _sec(self, parent: ctk.CTkFrame, title: str) -> None:
        ctk.CTkLabel(parent, text=title,
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=14, pady=(14, 4))

    def _sec_sub(self, parent: ctk.CTkFrame, title: str) -> None:
        """Section header with no top padding — used for second section in same card."""
        ctk.CTkLabel(parent, text=title,
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=self.SUB).pack(anchor="w", padx=14, pady=(0, 4))

    def _lbl(self, parent: ctk.CTkFrame, text: str) -> None:
        ctk.CTkLabel(parent, text=text,
                     font=ctk.CTkFont(size=13),
                     text_color=self.TEXT).pack(anchor="w", padx=14, pady=(8, 2))

    # ── log / RCON output ─────────────────────────────────────────────────────

    def _append_log(self, entry: str) -> None:
        self._logbox.configure(state="normal")
        self._logbox.insert("end", entry + "\n")
        self._logbox.see("end")
        self._logbox.configure(state="disabled")

    def _clear_log(self) -> None:
        self._logbox.configure(state="normal")
        self._logbox.delete("1.0", "end")
        self._logbox.configure(state="disabled")

    def _copy_connect_string(self) -> None:
        """Copy 'connect <ip>:<port>' to the clipboard and confirm in the log."""
        s = f"connect {RCON_HOST}:{RCON_PORT}"
        self.root.clipboard_clear()
        self.root.clipboard_append(s)
        self.core.log(f"Copied to clipboard: {s}")

    def _append_rcon(self, line: str) -> None:
        self._rcon_box.configure(state="normal")
        self._rcon_box.insert("end", line + "\n")
        self._rcon_box.see("end")
        self._rcon_box.configure(state="disabled")

    # ── map helpers ───────────────────────────────────────────────────────────

    def _on_mode_change(self, mode: str) -> None:
        """Update Official Map picker whenever the game mode changes.

        Modes with their own map pool → filter the dropdown to those maps.
        Workshop-only modes (None)    → disable the official picker, show hint.
        Standard pool modes           → restore full OFFICIAL_MAPS list.
        """
        maps = MODE_MAPS.get(mode, OFFICIAL_MAPS)

        if maps is None:
            # Workshop map required — disable the official selector
            self._off_cb.configure(values=[""], state="disabled")
            self._off_var.set("")
            self._mode_hint_lbl.configure(
                text=f"⚑  {mode} requires a workshop map — select or download one below",
                text_color=self.ORANGE,
            )
        else:
            # Enable picker; restrict to mode-compatible maps
            self._off_cb.configure(values=maps, state="normal")
            if self._off_var.get() not in maps:
                self._off_var.set(maps[0])   # snap to first valid map
            if maps == OFFICIAL_MAPS:
                self._mode_hint_lbl.configure(text="")
            else:
                self._mode_hint_lbl.configure(
                    text=f"✓  {len(maps)} compatible maps for {mode}",
                    text_color=self.SUB,
                )

        # Update browse button to reflect the selected mode
        self._browse_btn.configure(
            text=f"🔍  Browse {mode} Maps on Workshop"
        )

    def _refresh_wk(self) -> None:
        ids = _load_workshop()
        self.core.log(f"Workshop scan: {len(ids)} map(s) found")
        # Fetch human-readable names then update dropdown
        def _on_names_done() -> None:
            labels = []
            for wid in ids:
                name = self.core._map_name_cache.get(wid, "")
                labels.append(f"{name}  [{wid}]" if name else wid)
            self.root.after(0, self._wk_cb.configure, {"values": labels or [""]})
        self.core.fetch_workshop_names(ids, on_done=_on_names_done)
        # Show plain IDs immediately while names load
        self._wk_cb.configure(values=ids or [""])

    def _selected_map(self) -> tuple[str, bool]:
        wk = self._wk_var.get().strip()
        if wk:
            # Extract bare numeric ID from "Map Name  [123456]" format
            m = re.search(r'\[(\d+)\]', wk)
            raw_id = m.group(1) if m else wk
            return (raw_id, True)
        return (self._off_var.get().strip(), False)

    def _sync_status_bar(self) -> None:
        """Update status-bar labels from AppCore state (main-thread only)."""
        self._sb_map.configure( text=self.core.current_map  if self.core.running else "—")
        self._sb_mode.configure(text=self.core.current_mode if self.core.running else "—")

    def _on_core_state_change(self) -> None:
        """Called on the main thread whenever AppCore.boot_state changes."""
        self._set_state(self.core.boot_state)

    def _boot_pulse(self) -> None:
        """Animate the header dot while the server is booting.

        Cycles through three dot-fill patterns every 500 ms.  Stops
        automatically once the server leaves the 'booting' state.
        """
        if self.core.boot_state != "booting":
            return
        frames = ["⬤  BOOTING ·  ", "⬤  BOOTING ·· ", "⬤  BOOTING ···"]
        self._dot.configure(text=frames[self._pulse_step % 3],
                            text_color=self.ORANGE)
        self._pulse_step += 1
        self.root.after(500, self._boot_pulse)

    # ── uptime ticker ─────────────────────────────────────────────────────────

    def _tick_uptime(self) -> None:
        if self._uptime_start is not None and self.core.running:
            secs  = int(time.time() - self._uptime_start)
            h, r  = divmod(secs, 3600)
            m, s  = divmod(r, 60)
            self._sb_uptime.configure(text=f"{h:02d}:{m:02d}:{s:02d}")
        self.root.after(1000, self._tick_uptime)

    # ── button handlers ───────────────────────────────────────────────────────

    def _start(self) -> None:
        m, is_wk = self._selected_map()
        self.core.start_server(m, self._mode_var.get(), is_wk)
        if self.core.running:
            self._uptime_start = time.time()
            self._set_state("booting")
            self._boot_pulse()

    def _stop(self) -> None:
        self.core.stop_server()
        self._uptime_start = None
        self._set_state("offline")

    def _change(self) -> None:
        m, is_wk = self._selected_map()
        self.core.change_map(m, self._mode_var.get(), is_wk, caller="local")

    def _check_update_btn(self) -> None:
        """User clicked CHECK FOR UPDATE — run the check and show result."""
        self._manual_update_check = True
        self._upd_btn.configure(
            state="disabled", text="CHECKING…",
            fg_color=self.BORDER, text_color=self.SUB,
        )
        self.core.check_update()

    def _on_update_checked(self, available: bool,
                            installed: str, latest: str) -> None:
        """Fires on the main thread when check_update() finishes.

        Auto-check (on launch): just recolour the button if an update exists.
        Manual check (button click): also pop the update dialog.
        """
        if available:
            self._upd_btn.configure(
                state="normal",
                fg_color="#d97706", hover_color=self.ORANGE,
                text_color="#0d0d14", text="⬆   UPDATE AVAILABLE",
            )
            if self._manual_update_check:
                self._show_update_dialog(installed, latest)
        else:
            self._upd_btn.configure(
                state="normal",
                fg_color=self.BORDER, hover_color="#2a2a40",
                text_color=self.SUB, text="⟳   CHECK FOR UPDATE",
            )
        self._manual_update_check = False

    def _show_update_dialog(self, installed: str, latest: str) -> None:
        """Modal dialog: shows version info and offers to update now."""
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Update Available")
        dlg.geometry("400x230")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.ORANGE,
                     corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="UPDATE AVAILABLE",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.ORANGE).pack(pady=(20, 10))
        ctk.CTkLabel(dlg,
                     text=f"Installed build :  {installed}",
                     font=ctk.CTkFont(size=12),
                     text_color=self.SUB).pack()
        ctk.CTkLabel(dlg,
                     text=f"Latest build    :  {latest}",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self.TEXT).pack(pady=(2, 24))

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack()

        def _do_update() -> None:
            dlg.destroy()
            self._run_update_now()

        ctk.CTkButton(
            row, text="⬇  Update Now",
            fg_color=self.ORANGE, hover_color="#d97706",
            text_color="#0d0d14", width=160,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_do_update,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            row, text="Later",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, width=80,
            font=ctk.CTkFont(size=12),
            command=dlg.destroy,
        ).pack(side="left")

    def _run_update_now(self) -> None:
        """Kick off steamcmd update after the user confirmed."""
        self._upd_btn.configure(
            state="disabled", text="⬇   UPDATING…",
            fg_color=self.BORDER, text_color=self.SUB,
        )
        self.core.run_update(
            on_done=lambda: self.root.after(0, self._on_update_done)
        )

    def _on_update_done(self) -> None:
        """Re-enable the button after steamcmd finishes."""
        self._upd_btn.configure(
            state="normal",
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, text="⟳   CHECK FOR UPDATE",
        )

    # ── Steam account dialogs ─────────────────────────────────────────────────

    def _show_steam_account_dialog(self) -> None:
        """Settings dialog for storing Steam credentials used by workshop downloads."""
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Steam Account")
        dlg.geometry("420x340")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.ACCENT,
                     corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="STEAM ACCOUNT",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.ACCENT).pack(pady=(18, 4))
        ctk.CTkLabel(dlg,
                     text="Used for workshop downloads that need a logged-in account.\n"
                          "Server updates always use anonymous login.",
                     font=ctk.CTkFont(size=12), text_color=self.SUB,
                     justify="center").pack(pady=(0, 14))

        # Username
        ctk.CTkLabel(dlg, text="Steam Username",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=24)
        user_var = ctk.StringVar(value=self.core.steam_username)
        ctk.CTkEntry(dlg, textvariable=user_var,
                     placeholder_text="Your Steam username",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, placeholder_text_color=self.SUB,
                     font=ctk.CTkFont(size=13),
                     ).pack(fill="x", padx=24, pady=(2, 10))

        # Password
        ctk.CTkLabel(dlg, text="Steam Password",
                     font=ctk.CTkFont(size=12), text_color=self.TEXT,
                     anchor="w").pack(fill="x", padx=24)
        pass_var = ctk.StringVar(value=self.core.steam_password)
        ctk.CTkEntry(dlg, textvariable=pass_var, show="●",
                     placeholder_text="Your Steam password",
                     fg_color=self.DEEP, border_color=self.BORDER,
                     text_color=self.TEXT, placeholder_text_color=self.SUB,
                     font=ctk.CTkFont(size=13),
                     ).pack(fill="x", padx=24, pady=(2, 14))

        status_lbl = ctk.CTkLabel(dlg, text="", font=ctk.CTkFont(size=12))
        if self.core.steam_username:
            status_lbl.configure(
                text=f"Saved: '{self.core.steam_username}'",
                text_color=self.GREEN)
        else:
            status_lbl.configure(
                text="No credentials — anonymous login will be used",
                text_color=self.SUB)
        status_lbl.pack(pady=(0, 12))

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack()

        def _save() -> None:
            self.core.steam_username = user_var.get().strip()
            self.core.steam_password = pass_var.get()
            self.core.save_config()
            if self.core.steam_username:
                status_lbl.configure(
                    text=f"Saved: '{self.core.steam_username}'",
                    text_color=self.GREEN)
            else:
                status_lbl.configure(
                    text="Cleared — anonymous login will be used",
                    text_color=self.SUB)

        def _clear() -> None:
            user_var.set("")
            pass_var.set("")
            self.core.steam_username = ""
            self.core.steam_password = ""
            self.core.save_config()
            status_lbl.configure(
                text="Cleared — anonymous login will be used",
                text_color=self.SUB)

        ctk.CTkButton(
            row, text="Save", width=100,
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            text_color="#0d0d14",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_save,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Clear", width=80,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=_clear,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Close", width=80,
            fg_color=self.BORDER, hover_color="#2a2a40",
            text_color=self.SUB, font=ctk.CTkFont(size=12),
            command=dlg.destroy,
        ).pack(side="left")

    def _show_guard_dialog(self, prompt_type: str,
                            submit: Callable[[str], None]) -> None:
        """Modal dialog for entering a Steam Guard or 2FA code.

        Called on the main thread by the on_steam_guard callback.
        Calls submit(code) with whatever the user typed (empty string cancels).
        """
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Steam Guard")
        dlg.geometry("380x250")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.ORANGE,
                     corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="STEAM GUARD REQUIRED",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.ORANGE).pack(pady=(20, 8))

        if prompt_type == "2fa":
            desc = "Open your Steam mobile app and enter\nthe two-factor authenticator code."
        else:
            desc = "Check your email for a Steam Guard code\nand enter it below."
        ctk.CTkLabel(dlg, text=desc,
                     font=ctk.CTkFont(size=12), text_color=self.SUB,
                     justify="center").pack(pady=(0, 14))

        code_var   = ctk.StringVar()
        code_entry = ctk.CTkEntry(
            dlg, textvariable=code_var,
            placeholder_text="Enter code…",
            fg_color=self.DEEP, border_color=self.BORDER,
            text_color=self.TEXT, placeholder_text_color=self.SUB,
            font=ctk.CTkFont(size=16, weight="bold"),
            justify="center", width=180,
        )
        code_entry.pack(pady=(0, 18))
        code_entry.focus_set()

        def _submit() -> None:
            submit(code_var.get().strip())
            dlg.destroy()

        code_entry.bind("<Return>", lambda _e: _submit())
        ctk.CTkButton(
            dlg, text="Submit Code", width=140,
            fg_color=self.ORANGE, hover_color="#d97706",
            text_color="#0d0d14",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_submit,
        ).pack()

    def _set_state(self, state: str) -> None:
        """Update every piece of UI that reflects server state.

        state: "offline" | "booting" | "ready"
        """
        running = state != "offline"
        self._start_btn.configure(state="disabled" if running     else "normal")
        self._stop_btn.configure( state="normal"   if running     else "disabled")
        self._chg_btn.configure(  state="normal"   if state == "ready" else "disabled")
        if state == "offline":
            self._dot.configure(text="⬤  OFFLINE",  text_color=self.RED)
            if self._ff_btn:
                self._ff_btn.configure(state="disabled")
        elif state == "booting":
            self._dot.configure(text="⬤  BOOTING…", text_color=self.ORANGE)
            if self._ff_btn:
                self._ff_btn.configure(state="disabled")
        else:
            self._dot.configure(text="⬤  ONLINE",   text_color=self.GREEN)
            if self._ff_btn:
                self._ff_btn.configure(state="normal")
        self._sync_status_bar()
        if not running:
            self._sb_uptime.configure(text="—")

    # ── RCON console ──────────────────────────────────────────────────────────

    def _send_rcon(self) -> None:
        cmd = self._rcon_var.get().strip()
        if not cmd:
            return
        self._rcon_var.set("")
        if not self.core.running:
            self._append_rcon("[!] Server is not running")
            return
        self._append_rcon(f"› {cmd}")
        def _do() -> None:
            try:
                resp = self.core.rcon.execute(cmd)
                self.root.after(0, self._append_rcon, resp.strip() or "(no output)")
            except ConnectionRefusedError:
                self.root.after(0, self._append_rcon,
                                "[!] RCON not ready — server is still loading, "
                                "wait ~30 s and try again")
            except ConnectionError as exc:
                self.root.after(0, self._append_rcon, f"[!] RCON: {exc}")
            except Exception as exc:
                self.root.after(0, self._append_rcon, f"[err] {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── RCON diagnostic ───────────────────────────────────────────────────────

    def _test_rcon(self) -> None:
        """Full two-phase RCON diagnostic — runs on a background thread."""
        self._append_rcon(f"— Testing RCON at {RCON_HOST}:{RCON_PORT} —")
        def _do() -> None:
            # Phase 1: raw TCP probe
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(3)
                    s.connect((RCON_HOST, RCON_PORT))
            except ConnectionRefusedError:
                self.root.after(0, self._append_rcon,
                    f"[✗] Port {RCON_PORT} REFUSED\n"
                    "    → Server is not running, or Windows Firewall is\n"
                    "      blocking TCP on this port.\n"
                    "    Fix: Start the server, or add a Windows Firewall\n"
                    f"      inbound rule for TCP port {RCON_PORT}.")
                return
            except OSError as exc:
                self.root.after(0, self._append_rcon, f"[✗] TCP error: {exc}")
                return

            self.root.after(0, self._append_rcon,
                            f"[✓] Port {RCON_PORT} is OPEN")

            # Phase 2: RCON auth
            try:
                resp = self.core.rcon.execute("status")
                self.root.after(0, self._append_rcon,
                    f"[✓] RCON auth OK — server is ready\n"
                    + (resp.strip()[:300] if resp.strip() else "(no status output)"))
            except ConnectionError as exc:
                self.root.after(0, self._append_rcon,
                    f"[✗] Port open but RCON handshake failed: {exc}\n"
                    "    → Wrong rcon_password, or server still initialising.")
            except Exception as exc:
                self.root.after(0, self._append_rcon, f"[✗] RCON error: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── local workshop download ───────────────────────────────────────────────

    def _local_dl(self) -> None:
        wid = self._wsid_var.get().strip()
        if not wid or not wid.isdigit():
            self._wsid_lbl.configure(
                text="⚠  Enter a numeric Workshop ID", text_color=self.RED)
            return
        self._wsid_var.set("")
        self._wsid_lbl.configure(text=f"Downloading {wid}…", text_color=self.SUB)
        self.core.approve_download(
            wid,
            on_done=lambda ok: self.root.after(0, self._on_dl_done, wid, ok),
        )

    def _on_dl_done(self, wid: str, success: bool) -> None:
        if success:
            self._wsid_lbl.configure(text=f"✓  {wid} downloaded", text_color=self.GREEN)
            self._refresh_wk()
        else:
            self._wsid_lbl.configure(text=f"✗  {wid} failed — see log", text_color=self.RED)

    # ── workshop browser / update / plugin handlers ───────────────────────────

    def _browse_workshop(self) -> None:
        """Open Steam Workshop in the default browser, filtered by mode search term."""
        mode   = self._mode_var.get()
        search = MODE_WORKSHOP_SEARCH.get(mode, "")
        url    = _WS_BROWSE
        if search:
            url += "&searchtext=" + urllib.parse.quote(search)
        self.core.log(f"Opening Steam Workshop ({mode}): {url}")
        webbrowser.open(url)

    def _check_map_updates(self) -> None:
        self.core.check_workshop_updates()

    def _check_plugins(self) -> None:
        self.core.check_plugins()

    # ── process monitor ───────────────────────────────────────────────────────

    def _start_monitor(self) -> None:
        """Daemon thread: detects unexpected server process death."""
        def _watch() -> None:
            while True:
                time.sleep(2)
                if (self.core.running
                        and self.core.proc is not None
                        and self.core.proc.poll() is not None):
                    self.core.proc       = None
                    self.core.running    = False
                    self.core.boot_state = "offline"
                    self._uptime_start   = None
                    self.core.log("Server process exited unexpectedly")
                    self.root.after(0, self._set_state, "offline")
                    # Crash notification: bell + bring window to front
                    self.root.after(100, self.root.bell)
                    self.root.after(200, self.root.lift)
        threading.Thread(target=_watch, daemon=True).start()

    # ── workshop download approval dialog (from web requests) ─────────────────

    def _show_dl_dialog(self, workshop_id: str, requester: str) -> None:
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Workshop Download Request")
        dlg.geometry("420x240")
        dlg.configure(fg_color=self.CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        ctk.CTkFrame(dlg, fg_color=self.ACCENT, corner_radius=0, height=2).pack(fill="x")
        ctk.CTkLabel(dlg, text="WORKSHOP DOWNLOAD REQUEST",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self.ACCENT).pack(pady=(20, 4))
        ctk.CTkLabel(dlg, text=f"Requested by:  {requester}",
                     font=ctk.CTkFont(size=11), text_color=self.SUB).pack()
        ctk.CTkLabel(dlg, text=f"Workshop ID:  {workshop_id}",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.TEXT).pack(pady=(4, 20))

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack()

        def _approve() -> None:
            dlg.destroy()
            self.core.approve_download(
                workshop_id,
                on_done=lambda _ok: self.root.after(0, self._refresh_wk),
            )

        def _reject() -> None:
            self.core.reject_download(workshop_id, requester=requester)
            dlg.destroy()

        ctk.CTkButton(
            row, text="✓  Approve & Download",
            fg_color=self.ACCENT, hover_color=self.ACCENT_H,
            width=190, font=ctk.CTkFont(size=12, weight="bold"),
            command=_approve,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            row, text="✕  Reject",
            fg_color="#333", hover_color="#555",
            width=100, font=ctk.CTkFont(size=12),
            command=_reject,
        ).pack(side="left")

    # ── public IP ─────────────────────────────────────────────────────────────

    def _on_public_ip(self, ip: str) -> None:
        self._pub_ip_lbl.configure(text=f"ext: {ip}")

    def _copy_public_ip(self) -> None:
        ip = self.core.public_ip
        if not ip:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(ip)
        self.core.log(f"Copied public IP: {ip}")

    # ── log export ────────────────────────────────────────────────────────────

    def _export_log(self) -> None:
        path = tkinter.filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"oblivion_log_{time.strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if not path:
            return
        lines = self.core.get_log()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.core.log(f"Log exported: {path}")
        except Exception as exc:
            self.core.log(f"Log export failed: {exc}")

    # ── friendly fire toggle ──────────────────────────────────────────────────

    def _toggle_ff(self) -> None:
        new_state = not self.core._ff_enabled
        self.core.set_friendly_fire(new_state)
        if new_state:
            self._ff_btn.configure(
                text="Friendly Fire: ON",
                fg_color=self.ORANGE, hover_color="#d97706",
                text_color="#0d0d14",
            )
        else:
            self._ff_btn.configure(
                text="Friendly Fire: OFF",
                fg_color=self.BORDER, hover_color="#2a2a40",
                text_color=self.SUB,
            )

    # ── server chat broadcast ─────────────────────────────────────────────────

    def _send_chat(self) -> None:
        msg = self._chat_var.get().strip()
        if not msg:
            return
        self._chat_var.set("")
        if not self.core.running:
            self.core.log("[!] Server not running — cannot broadcast chat")
            return
        self.core.server_say(msg)

    # ── player list ───────────────────────────────────────────────────────────

    def _refresh_players(self) -> None:
        if not self.core.running:
            self._player_status_lbl.configure(text="Server offline", text_color=self.SUB)
            return
        self._player_status_lbl.configure(text="Refreshing…", text_color=self.SUB)
        self.core.get_players(
            lambda players: self.root.after(0, self._populate_players, players)
        )

    def _populate_players(self, players: list[dict]) -> None:
        # Clear old rows
        for row in self._player_rows:
            row.destroy()
        self._player_rows.clear()

        if not players:
            self._player_status_lbl.configure(
                text="No players connected", text_color=self.SUB)
            return

        self._player_status_lbl.configure(
            text=f"{len(players)} player(s) connected", text_color=self.GREEN)

        for p in players:
            row = ctk.CTkFrame(self._player_scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)

            ctk.CTkLabel(
                row, text=p["name"][:24],
                text_color=self.TEXT, font=ctk.CTkFont(size=12), anchor="w",
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                row, text=f"{p['ping']}ms",
                text_color=self.SUB, font=ctk.CTkFont(size=11), width=46,
            ).pack(side="left")
            ctk.CTkButton(
                row, text="Kick", width=46, height=26,
                fg_color=self.ORANGE, hover_color="#d97706",
                text_color="#0d0d14", font=ctk.CTkFont(size=11, weight="bold"),
                command=functools.partial(
                    self.core.kick_player, p["userid"], p["name"]),
            ).pack(side="left", padx=(4, 2))
            ctk.CTkButton(
                row, text="Ban", width=40, height=26,
                fg_color=self.STOP, hover_color=self.STOP_H,
                text_color=self.TEXT, font=ctk.CTkFont(size=11, weight="bold"),
                command=functools.partial(
                    self.core.ban_player, p["steamid"], p["name"]),
            ).pack(side="left", padx=(2, 0))

            self._player_rows.append(row)

    def _toggle_auto_refresh(self) -> None:
        if self._auto_refresh_var.get():
            self._schedule_auto_refresh()
        elif self._auto_refresh_after:
            self.root.after_cancel(self._auto_refresh_after)
            self._auto_refresh_after = None

    def _schedule_auto_refresh(self) -> None:
        if not self._auto_refresh_var.get():
            return
        self._refresh_players()
        self._auto_refresh_after = self.root.after(10000, self._schedule_auto_refresh)

    # ── ban list management ───────────────────────────────────────────────────

    def _manual_ban(self) -> None:
        steamid = self._ban_id_var.get().strip()
        if not steamid:
            self.core.log("[!] Enter a SteamID to ban")
            return
        self._ban_id_var.set("")
        if not self.core.running:
            self.core.log("[!] Server not running")
            return
        self.core.ban_player(steamid, duration=0)

    def _refresh_ban_list(self) -> None:
        if not self.core.running:
            return
        self.core.get_ban_list(
            lambda entries: self.root.after(0, self._populate_ban_list, entries)
        )

    def _populate_ban_list(self, entries: list[str]) -> None:
        for row in self._ban_rows:
            row.destroy()
        self._ban_rows.clear()

        if not entries:
            row = ctk.CTkFrame(self._ban_scroll, fg_color="transparent")
            row.pack(fill="x")
            ctk.CTkLabel(row, text="No bans on record",
                         text_color=self.SUB, font=ctk.CTkFont(size=12)).pack()
            self._ban_rows.append(row)
            return

        for entry in entries:
            row = ctk.CTkFrame(self._ban_scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=entry[:40],
                         text_color=self.TEXT, font=ctk.CTkFont(size=11),
                         anchor="w").pack(side="left", fill="x", expand=True)
            # Try to extract STEAM id from entry string
            sid_match = re.search(r'STEAM_\S+', entry)
            sid = sid_match.group(0) if sid_match else entry.split()[0]
            ctk.CTkButton(
                row, text="Unban", width=56, height=24,
                fg_color=self.GREEN, hover_color="#16a34a",
                text_color="#0d0d14", font=ctk.CTkFont(size=11, weight="bold"),
                command=functools.partial(self.core.unban_player, sid),
            ).pack(side="right")
            self._ban_rows.append(row)

    # ── config tab handlers ───────────────────────────────────────────────────

    def _save_server_settings(self) -> None:
        self.core.hostname              = self._hostname_var.get().strip()
        self.core.sv_password           = self._svpw_var.get()
        self.core.tickrate_128          = self._tick128_var.get()
        self.core.auto_start            = self._autostart_var.get()
        self.core.max_players_override  = self._maxp_var.get().strip()
        self.core.save_config()
        self.core.log("Server settings saved — will apply on next server start")

    def _set_sv_password_live(self) -> None:
        pw = self._svpw_var.get()
        if not self.core.running:
            self.core.log("[!] Server not running — password will apply on next start")
            return
        def _do() -> None:
            try:
                self.core.rcon.execute(f"sv_password {pw}")
                self.core.sv_password = pw
                self.core.log(f"sv_password updated live {'(public)' if not pw else '(password set)'}")
            except Exception as exc:
                self.core.log(f"sv_password live update failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── preset management ─────────────────────────────────────────────────────

    def _current_config_snapshot(self) -> dict:
        return {
            "hostname":             self.core.hostname,
            "sv_password":          self.core.sv_password,
            "tickrate_128":         self.core.tickrate_128,
            "auto_start":           self.core.auto_start,
            "bot_difficulty":       self.core.bot_difficulty,
            "max_players_override": self.core.max_players_override,
        }

    def _save_preset(self) -> None:
        name = self._preset_name_var.get().strip()
        if not name:
            self.core.log("[!] Enter a preset name")
            return
        self.core.presets[name] = self._current_config_snapshot()
        self.core.save_config()
        self._refresh_preset_list()
        self._preset_name_var.set("")
        self.core.log(f"Preset saved: {name}")

    def _load_preset(self) -> None:
        name = self._preset_sel_var.get()
        cfg  = self.core.presets.get(name)
        if not cfg:
            self.core.log(f"[!] Preset not found: {name}")
            return
        self.core.hostname             = cfg.get("hostname", self.core.hostname)
        self.core.sv_password          = cfg.get("sv_password", "")
        self.core.tickrate_128         = cfg.get("tickrate_128", False)
        self.core.auto_start           = cfg.get("auto_start", False)
        self.core.bot_difficulty       = cfg.get("bot_difficulty", "Normal")
        self.core.max_players_override = cfg.get("max_players_override", "")
        # Sync UI fields
        self._hostname_var.set(self.core.hostname)
        self._svpw_var.set(self.core.sv_password)
        self._tick128_var.set(self.core.tickrate_128)
        self._autostart_var.set(self.core.auto_start)
        self._bot_diff_var.set(self.core.bot_difficulty)
        self._maxp_var.set(self.core.max_players_override)
        self.core.log(f"Preset loaded: {name}")

    def _delete_preset(self) -> None:
        name = self._preset_sel_var.get()
        if name in self.core.presets:
            del self.core.presets[name]
            self.core.save_config()
            self._refresh_preset_list()
            self.core.log(f"Preset deleted: {name}")

    def _refresh_preset_list(self) -> None:
        names = list(self.core.presets.keys()) or [""]
        self._preset_cb.configure(values=names)
        self._preset_sel_var.set(names[0])

    def run(self) -> None:
        self.root.mainloop()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    core = AppCore()
    core.log("CS2 Panel initialised")
    core.log(f"Remote admin → http://localhost:{FLASK_PORT}  (PIN: {ADMIN_PIN})")

    threading.Thread(
        target=lambda: create_flask(core).run(
            host="0.0.0.0", port=FLASK_PORT,
            use_reloader=False, threaded=True,
        ),
        daemon=True,
    ).start()

    gui = CS2GUI(core)
    gui._refresh_wk()    # initial workshop scan — runs after callbacks are registered
    core.check_update()  # auto-check AFTER GUI is built so on_update_checked is wired up
    core.check_public_ip()   # async — updates status bar when result arrives
    if core.auto_start:
        gui.root.after(500, gui._start)
    gui.run()
