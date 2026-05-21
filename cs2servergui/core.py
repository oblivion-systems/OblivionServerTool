"""
core.py — AppCore: single source of truth for server state.

Shared between the local GUI (gui.py) and the Flask web panel (web.py).
All blocking work runs on daemon threads; results are delivered via typed
callbacks that callers register after construction.
"""
from __future__ import annotations

import collections
import json
import os
import queue
import re
import socket
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable

from . import config as _config
from .config import (
    CS2_PATH, CS2_SERVER_DIR, CS2_APP_ID, CS2_ADDONS_DIR,
    STEAMCMD_PATH, WORKSHOP_DIR,
    DEPOTDL_PATH, DEPOTDL_RELEASE_URL,
    RCON_HOST, RCON_PORT, RCON_PASSWORD,
    MODE_SETTINGS, _DEFAULT_MODE,
    _CONFIG_FILE,
)
from .rcon import RCONClient


class AppCore:
    """Single source of truth shared between the local GUI and Flask."""

    def __init__(self) -> None:
        self.proc:         subprocess.Popen | None = None
        self.running:      bool = False
        self.boot_state:   str  = "offline"   # "offline" | "booting" | "ready"
        self.current_map:  str  = "de_dust2"
        self.current_mode: str  = "Competitive"
        self.rcon = RCONClient(RCON_HOST, RCON_PORT, RCON_PASSWORD)

        self._log_buf  = collections.deque(maxlen=300)
        self._log_lock = threading.Lock()

        self._sse_qs:  list[queue.Queue] = []
        self._sse_lock = threading.Lock()

        self._dl_reqs: list[dict]        = []
        self._dl_lock  = threading.Lock()

        self.update_available: bool = False

        # Install location — all other paths are derived from this
        self.server_dir: str = ""

        # Steam credentials
        self.steam_username: str = ""
        self.steam_password: str = ""

        # Server config (persisted)
        self.hostname:             str  = "CS2 Dedicated Server"
        self.sv_password:          str  = ""
        self.tickrate_128:         bool = False
        self.auto_start:           bool = False
        self.bot_difficulty:       str  = "Normal"
        self.max_players_override: str  = ""
        self.presets:              dict[str, dict] = {}

        # Runtime state
        self.public_ip:           str                      = ""
        self._map_name_cache:     dict[str, str]           = {}
        self._ff_enabled:         bool                     = False
        self._active_dl_proc:     subprocess.Popen | None  = None
        self.steam_session_active: bool                    = False

        # fired (no args) when steam_session_active changes
        self.on_steam_session_change: Callable[[], None] | None = None

        # fired with (prompt_type, submit_callback) when steamcmd asks for Guard
        self.on_steam_guard: Callable[[str, Callable[[str], None]], None] | None = None
        self._load_config()

        # GUI / web callbacks — registered after construction
        self.on_log:                Callable[[str], None] | None                   = None
        self.on_dl_request:         Callable[[str, str], None] | None              = None
        self.on_state_change:       Callable[[], None] | None                      = None
        self.on_update_checked:     Callable[[bool, str, str], None] | None        = None
        self.on_public_ip:          Callable[[str], None] | None                   = None
        # (available, current_ver, latest_ver, download_url)
        self.on_app_update_checked: Callable[[bool, str, str, str], None] | None   = None

    # ── logging ───────────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        entry = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self._log_lock:
            self._log_buf.append(entry)
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

    # ── config persistence ────────────────────────────────────────────────────

    def update_server_dir(self, path: str) -> None:
        """Change the server directory and recompute all derived paths."""
        self.server_dir = path
        _config.update_paths(path)
        self.log(f"Server directory set: {path}")

    def _load_config(self) -> None:
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            # Load server_dir first — everything else depends on it
            saved_dir = cfg.get("server_dir", "")
            if saved_dir:
                self.server_dir = saved_dir
                _config.update_paths(saved_dir)
            self.steam_username        = cfg.get("steam_username", "")
            self.steam_password        = cfg.get("steam_password", "")
            self.steam_session_active  = bool(cfg.get("steam_session_active", False))
            self.hostname              = cfg.get("hostname", "CS2 Dedicated Server")
            self.sv_password           = cfg.get("sv_password", "")
            self.tickrate_128          = bool(cfg.get("tickrate_128", False))
            self.auto_start            = bool(cfg.get("auto_start", False))
            self.bot_difficulty        = cfg.get("bot_difficulty", "Normal")
            self.max_players_override  = cfg.get("max_players_override", "")
            self.presets               = cfg.get("presets", {})
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.log(f"Config load warning: {exc}")

    def save_config(self) -> None:
        try:
            cfg = {
                "server_dir":           self.server_dir,
                "steam_username":       self.steam_username,
                "steam_password":       self.steam_password,
                "steam_session_active": self.steam_session_active,
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

    # ── server control ────────────────────────────────────────────────────────

    def start_server(self, map_name: str, mode: str,
                     is_workshop: bool = False) -> None:
        s    = MODE_SETTINGS.get(mode, _DEFAULT_MODE)
        maxp = self.max_players_override.strip() or s["maxplayers"]
        cmd  = [
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
        cmd += (["+host_workshop_map", map_name]
                if is_workshop else ["+map", map_name])
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
        self.log(f"Polling RCON at {RCON_HOST}:{RCON_PORT} — waiting for server…")
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
            self.proc = None
        self.running    = False
        self.boot_state = "offline"
        self.log("Server stopped")
        if self.on_state_change:
            self.on_state_change()

    def _poll_rcon_ready(self) -> None:
        """Probe RCON every 3 s; mark server ready when it responds."""
        start    = time.time()
        last_log = 0.0
        while self.running and self.boot_state == "booting":
            elapsed = time.time() - start
            if elapsed >= 90 and self.proc and self.proc.poll() is None:
                self.boot_state = "ready"
                self.log(
                    "Server marked ONLINE after 90 s "
                    "(RCON TCP unreachable — use TEST RCON to diagnose)"
                )
                if self.on_state_change:
                    self.on_state_change()
                return
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.settimeout(2)
                    probe.connect((RCON_HOST, RCON_PORT))
            except Exception:
                now = time.time()
                if now - last_log >= 30:
                    self.log(
                        f"RCON not reachable ({int(elapsed)}s) — server still loading"
                    )
                    last_log = now
                time.sleep(3)
                continue
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
                self.log("RCON error: connection refused after retries")
            except Exception as exc:
                self.log(f"RCON error: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── server update ─────────────────────────────────────────────────────────

    def _installed_build(self) -> str | None:
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

    def check_update(self) -> None:
        import json as _json
        def _do() -> None:
            manifest = os.path.join(
                CS2_SERVER_DIR, "steamapps", f"appmanifest_{CS2_APP_ID}.acf"
            )
            self.log(f"Update check: reading {manifest}")
            build = self._installed_build()
            if not build:
                self.log("Update check: appmanifest not found")
                if self.on_update_checked:
                    self.on_update_checked(False, "unknown", "unknown")
                return
            self.log(f"Update check: installed build = {build}")
            try:
                url = (
                    "https://api.steampowered.com/ISteamApps/UpToDateCheck/v0001/"
                    f"?appid={CS2_APP_ID}&version={build}&format=json"
                )
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = _json.loads(resp.read())
                r = data.get("response", {})
                self.log(f"Update check: API → {r}")
                if not r.get("success"):
                    self.log("Update check: Steam API returned success=false")
                    if self.on_update_checked:
                        self.on_update_checked(False, build, "unknown")
                    return
                listed = r.get("version_is_listable", False)
                if not listed:
                    self.log(f"Update check: build {build} not in Steam version list")
                up_to_date = r.get("up_to_date", True)
                latest     = str(r.get("required_version") or build)
                if up_to_date:
                    self.log(f"Update check: up to date (build {build})")
                    self.update_available = False
                    if self.on_update_checked:
                        self.on_update_checked(False, build, build)
                else:
                    self.log("Update check: UPDATE AVAILABLE")
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

    def check_app_update(self) -> None:
        """Check GitHub Releases for a newer version of OblivionServerTool itself.

        Compares APP_VERSION against the latest GitHub release tag (semver).
        Calls on_app_update_checked(available, current, latest, url) on completion.
        Fails silently — the app repo may be private or the machine may be offline.
        """
        from .config import APP_VERSION, APP_API_URL, APP_RELEASES_URL
        import json as _json

        def _ver(v: str) -> tuple[int, ...]:
            """Parse semver into a comparable tuple.

            Stable release sorts above a pre-release with the same number:
              1.0.0         → (1, 0, 0, 1)   # stable
              1.0.0-beta    → (1, 0, 0, 0)   # pre-release < same stable
              1.0.0-beta.2  → (1, 0, 0, 0)   # still pre-release
            """
            try:
                clean  = v.strip().lstrip("v")
                parts  = clean.split("-", 1)          # ["1.0.0"] or ["1.0.0","beta"]
                nums   = tuple(int(x) for x in parts[0].split("."))
                stable = 1 if len(parts) == 1 else 0  # stable > pre-release
                return nums + (stable,)
            except ValueError:
                return (0,)

        def _do() -> None:
            try:
                req = urllib.request.Request(
                    APP_API_URL,
                    headers={"User-Agent": f"OblivionServerTool/{APP_VERSION}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = _json.loads(resp.read())
                tag = data.get("tag_name", "").strip().lstrip("v")
                url = data.get("html_url", APP_RELEASES_URL)
                if not tag:
                    return
                available = _ver(tag) > _ver(APP_VERSION)
                self.log(
                    f"App update check: current=v{APP_VERSION}  "
                    f"latest=v{tag}  "
                    f"{'UPDATE AVAILABLE' if available else 'up to date'}"
                )
                if self.on_app_update_checked:
                    self.on_app_update_checked(available, APP_VERSION, tag, url)
            except Exception as exc:
                # Silently swallow — private repo / offline / etc.
                self.log(f"App update check skipped: {exc}")

        threading.Thread(target=_do, daemon=True).start()

    def install_server(self, on_done: Callable | None = None) -> None:
        """Fresh install: download steamcmd then install the CS2 dedicated server.

        Safe to call on an existing install — steamcmd will just verify/update.
        """
        import zipfile

        def _do() -> None:
            self.log("═" * 48)
            self.log("  CS2 SERVER INSTALL")
            self.log(f"  target → {_config.CS2_SERVER_DIR}")
            self.log("═" * 48)

            # ── Step 1: steamcmd ──────────────────────────────────────────────
            if os.path.isfile(_config.STEAMCMD_PATH):
                self.log("Step 1/2 — steamcmd already present, skipping download")
            else:
                self.log("Step 1/2 — Downloading steamcmd from Valve…")
                try:
                    os.makedirs(_config.CS2_SERVER_DIR, exist_ok=True)
                    zip_path = os.path.join(_config.CS2_SERVER_DIR, "steamcmd.zip")
                    url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
                    urllib.request.urlretrieve(url, zip_path)
                    with zipfile.ZipFile(zip_path) as zf:
                        zf.extractall(_config.CS2_SERVER_DIR)
                    os.remove(zip_path)
                    self.log("  steamcmd downloaded and extracted ✓")
                except Exception as exc:
                    self.log(f"  ✗ steamcmd download failed: {exc}")
                    if on_done:
                        on_done()
                    return

            # ── Step 2: CS2 server via steamcmd ──────────────────────────────
            self.log("Step 2/2 — Installing CS2 dedicated server…")
            self.log("  This downloads ~15 GB — expect 10–30 minutes.")
            self.log("  Progress will appear below as steamcmd runs.")
            self.run_update(on_done=on_done)   # handles logging + on_done

        threading.Thread(target=_do, daemon=True).start()

    def run_update(self, on_done: Callable | None = None) -> None:
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
            self.log("─" * 48)
            self.log("Launching steamcmd…")
            self.log("  Phase 1 — steamcmd initialises itself  (10–30 s, no output)")
            self.log("  Phase 2 — login anonymous")
            self.log("  Phase 3 — download changed files  (progress below)")
            try:
                proc = subprocess.Popen(
                    [STEAMCMD_PATH, "+login", "anonymous",
                     "+force_install_dir", CS2_SERVER_DIR,
                     "+app_update", CS2_APP_ID, "+quit"],
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
                    out_q.put(None)

                threading.Thread(target=_reader, daemon=True).start()
                last_output = time.time()
                warned_at: set[int] = set()
                while True:
                    try:
                        line = out_q.get(timeout=0.25)
                    except queue.Empty:
                        silence = int(time.time() - last_output)
                        for t in (15, 30, 60, 120):
                            if t not in warned_at and silence >= t:
                                warned_at.add(t)
                                self.log(f"  … still initialising ({silence}s)")
                        continue
                    if line is None:
                        break
                    last_output = time.time()
                    s = line.strip()
                    if s:
                        self.log(f"[steamcmd] {s}")
                proc.wait()
                self.log("─" * 48)
                if proc.returncode == 0:
                    self.log("  UPDATE COMPLETE.")
                    self.update_available = False
                elif proc.returncode == 8:
                    self.log("  Exit code 8 — update failed (run steamcmd manually once to self-update)")
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

    # ── workshop update check ─────────────────────────────────────────────────

    def check_workshop_updates(self) -> None:
        import json as _json
        def _do() -> None:
            if not os.path.exists(WORKSHOP_DIR):
                self.log("Workshop update check: directory not found")
                return
            ids = [f for f in os.listdir(WORKSHOP_DIR)
                   if os.path.isdir(os.path.join(WORKSHOP_DIR, f)) and f.isdigit()]
            if not ids:
                self.log("Workshop update check: no maps downloaded")
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
                    data = _json.loads(resp.read())
                items    = data.get("response", {}).get("publishedfiledetails", [])
                outdated = []
                up_to_date = 0
                for item in items:
                    wid      = item.get("publishedfileid", "")
                    title    = item.get("title") or wid
                    steam_ts = item.get("time_updated", 0)
                    local_ts = (int(os.path.getmtime(os.path.join(WORKSHOP_DIR, wid)))
                                if os.path.exists(os.path.join(WORKSHOP_DIR, wid)) else 0)
                    if steam_ts > local_ts:
                        outdated.append((wid, title))
                    else:
                        up_to_date += 1
                if not outdated:
                    self.log(f"Workshop maps: all {len(ids)} map(s) up to date")
                else:
                    self.log(f"Workshop maps: {len(outdated)} update(s) available:")
                    for wid, title in outdated:
                        self.log(f"  ⬆  {title}  (ID: {wid})")
                    if up_to_date:
                        self.log(f"  ✓  {up_to_date} other map(s) current")
            except Exception as exc:
                self.log(f"Workshop update check failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    # ── plugin checker ────────────────────────────────────────────────────────

    def check_plugins(self) -> None:
        import json as _json
        def _do() -> None:
            self.log("Plugin check: scanning addons directory…")
            css_dir = os.path.join(CS2_ADDONS_DIR, "counterstrikesharp")
            if os.path.exists(css_dir):
                self.log("CounterStrikeSharp: ✓ installed")
                plugins_dir = os.path.join(css_dir, "plugins")
                if os.path.exists(plugins_dir):
                    plugins = sorted(
                        p for p in os.listdir(plugins_dir)
                        if os.path.isdir(os.path.join(plugins_dir, p))
                    )
                    self.log(f"  Plugins ({len(plugins)}): {', '.join(plugins)}"
                             if plugins else "  No plugins installed")
                try:
                    req = urllib.request.Request(
                        "https://api.github.com/repos/"
                        "roflmuffin/CounterStrikeSharp/releases/latest",
                        headers={"User-Agent": "OblivionServerTool/1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        rel = _json.loads(resp.read())
                    tag  = rel.get("tag_name", "?")
                    date = rel.get("published_at", "")[:10]
                    self.log(f"  Latest release: {tag}  ({date})")
                except Exception as exc:
                    self.log(f"  GitHub check failed: {exc}")
            else:
                self.log("CounterStrikeSharp: not found in addons/")
            mm_dir = os.path.join(CS2_ADDONS_DIR, "metamod")
            self.log("Metamod:Source: " + ("✓ installed" if os.path.exists(mm_dir)
                                            else "not found in addons/"))
            self.log(f"Plugin check complete  (addons: {CS2_ADDONS_DIR})")
        threading.Thread(target=_do, daemon=True).start()

    # ── workshop download ─────────────────────────────────────────────────────

    def request_workshop_download(self, workshop_id: str,
                                   requester: str = "remote") -> None:
        with self._dl_lock:
            if any(r["id"] == workshop_id for r in self._dl_reqs):
                self.log(f"Download already pending: {workshop_id}")
                return
            self._dl_reqs.append({"id": workshop_id, "requester": requester})
        self.log(f"[{requester}] Requested download of workshop map {workshop_id}")
        if self.on_dl_request:
            self.on_dl_request(workshop_id, requester)

    def approve_download(self, workshop_id: str,
                          on_done: Callable[[bool], None] | None = None) -> None:
        """Download a workshop map. Delegates entirely to DepotDownloader."""
        self.depotdl_download(workshop_id, on_done=on_done)

    def reject_download(self, workshop_id: str, requester: str = "") -> None:
        with self._dl_lock:
            self._dl_reqs = [r for r in self._dl_reqs if r["id"] != workshop_id]
        note = f" (by {requester})" if requester else ""
        self.log(f"Download rejected: {workshop_id}{note}")

    def cancel_download(self) -> None:
        """Kill the currently running steamcmd download process, if any."""
        proc = self._active_dl_proc
        if proc is None:
            self.log("No download in progress")
            return
        self.log("Cancelling download — terminating steamcmd…")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def steam_login_interactive(self) -> None:
        """Open steamcmd in a real console window for one-time 2FA setup.

        Monitors the process: if steamcmd exits cleanly (code 0) the session
        token has been saved and steam_session_active is set True, turning the
        Steam button green.
        """
        if not self.steam_username:
            self.log("[!] Set a Steam username in Steam Account settings first")
            return
        self.log("─" * 48)
        self.log("  INTERACTIVE STEAM LOGIN")
        self.log(f"  Opening steamcmd console for: {self.steam_username}")
        self.log("  ⚠  Use a dedicated server account — NOT your personal Steam")
        self.log("     account. steamcmd will disconnect your Steam desktop client")
        self.log("     if both use the same account.")
        self.log("  Complete 2FA in the window, then let it close on its own.")
        self.log("  Steam button turns green automatically on success.")
        self.log("─" * 48)
        try:
            import sys as _sys
            args = [STEAMCMD_PATH,
                    "+login", self.steam_username, self.steam_password,
                    "+quit"]
            if _sys.platform == "win32":
                proc = subprocess.Popen(args,
                                        creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                proc = subprocess.Popen(["x-terminal-emulator", "-e"] + args)

            def _monitor() -> None:
                proc.wait()
                if proc.returncode == 0:
                    self.steam_session_active = True
                    self.save_config()
                    self.log("  ✓ Steam session established — downloads will be silent")
                    if self.on_steam_session_change:
                        self.on_steam_session_change()
                else:
                    self.log(f"  Interactive login exited with code {proc.returncode}")
                    self.log("  Session not confirmed — try again if downloads fail")

            threading.Thread(target=_monitor, daemon=True).start()
        except Exception as exc:
            self.log(f"Interactive login failed to launch: {exc}")

    # ── DepotDownloader ───────────────────────────────────────────────────────

    def _ensure_depotdownloader(self) -> bool:
        """Download DepotDownloader if not already present. Returns True on success."""
        import json as _json
        import zipfile
        if os.path.isfile(DEPOTDL_PATH):
            return True
        self.log("DepotDownloader not found — downloading from GitHub…")
        try:
            dest_dir = os.path.dirname(DEPOTDL_PATH)
            os.makedirs(dest_dir, exist_ok=True)
            req = urllib.request.Request(
                DEPOTDL_RELEASE_URL,
                headers={"User-Agent": "OblivionServerTool/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                release = _json.loads(r.read())
            # Find the Windows x64 zip asset specifically
            assets = release.get("assets", [])
            asset_url = next(
                (a["browser_download_url"] for a in assets
                 if "windows" in a["name"].lower()
                 and "x64" in a["name"].lower()
                 and a["name"].endswith(".zip")),
                None,
            )
            if not asset_url:
                # Fallback: any windows zip (log the name so we can debug)
                for a in assets:
                    if "windows" in a["name"].lower() and a["name"].endswith(".zip"):
                        self.log(f"  No x64 asset found — using: {a['name']}")
                        asset_url = a["browser_download_url"]
                        break
            if not asset_url:
                self.log("  ✗ Could not find a Windows release asset.")
                return False
            self.log(f"  Downloading: {asset_url}")
            zip_path = os.path.join(dest_dir, "depotdownloader.zip")
            urllib.request.urlretrieve(asset_url, zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(dest_dir)
            os.remove(zip_path)
            if os.path.isfile(DEPOTDL_PATH):
                self.log("  DepotDownloader installed ✓")
                return True
            self.log(f"  ✗ Extracted but {DEPOTDL_PATH} not found.")
            return False
        except Exception as exc:
            self.log(f"  DepotDownloader install failed: {exc}")
            return False

    def depotdl_download(self, workshop_id: str,
                         on_done: Callable[[bool], None] | None = None) -> None:
        """Download a workshop item via DepotDownloader (more reliable than steamcmd)."""
        def _run() -> None:
            success = False
            self.log("─" * 48)
            self.log("  WORKSHOP DOWNLOAD  (DepotDownloader)")
            self.log(f"  workshop ID → {workshop_id}")
            self.log("─" * 48)

            if not self.steam_username or not self.steam_password:
                self.log("  ✗ DepotDownloader requires saved credentials.")
                self.log("  → Open Steam Account and save username + password.")
                if on_done:
                    on_done(False)
                return

            if not self._ensure_depotdownloader():
                if on_done:
                    on_done(False)
                return

            dest = os.path.join(WORKSHOP_DIR, workshop_id)
            os.makedirs(dest, exist_ok=True)

            session_ok = self.steam_session_active and bool(self.steam_username)

            if session_ok:
                # Cached token — no password needed
                login_args = ["-username", self.steam_username]
                self.log("  Using cached session — no login prompt expected.")
            else:
                # First time: supply password and ask DepotDownloader to save the token
                login_args = [
                    "-username",          self.steam_username,
                    "-password",          self.steam_password,
                    "-remember-password",
                ]
                self.log("  Logging in and saving session token for future downloads.")

            cmd = [
                DEPOTDL_PATH,
                "-app",     CS2_APP_ID,
                "-pubfile", workshop_id,
                "-dir",     dest,
            ] + login_args
            self.log("  Launching DepotDownloader — "
                     "enter 2FA/Guard code in the console if prompted (first time only).")
            try:
                import sys as _sys
                if _sys.platform == "win32":
                    proc = subprocess.Popen(
                        cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
                else:
                    proc = subprocess.Popen(
                        ["x-terminal-emulator", "-e"] + cmd)

                self._active_dl_proc = proc
                TIMEOUT   = 600
                start     = time.time()
                warned_at: set[int] = set()
                while proc.poll() is None:
                    time.sleep(2)
                    elapsed = int(time.time() - start)
                    if elapsed >= TIMEOUT:
                        self.log("  Timed out — cancelling")
                        try:
                            proc.terminate()
                            proc.wait(timeout=5)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        break
                    for t in (30, 60, 120, 180, 300, 480):
                        if t not in warned_at and elapsed >= t:
                            warned_at.add(t)
                            self.log(f"  … downloading ({elapsed}s)")

                self.log("─" * 48)
                if os.path.isdir(dest) and os.listdir(dest):
                    self.log(f"  DOWNLOAD COMPLETE — {workshop_id}")
                    if not session_ok:
                        self.steam_session_active = True
                        self.save_config()
                        self.log("  Session token saved — future downloads won't need login")
                        if self.on_steam_session_change:
                            self.on_steam_session_change()
                    success = True
                else:
                    self.log(f"  DepotDownloader exit code {proc.returncode}")
                    self.log(f"  No files found at: {dest}")
                self.log("─" * 48)
            except FileNotFoundError:
                self.log(f"  DepotDownloader not found: {DEPOTDL_PATH}")
            except Exception as exc:
                self.log(f"  DepotDownloader error: {exc}")
            finally:
                self._active_dl_proc = None
                with self._dl_lock:
                    self._dl_reqs = [r for r in self._dl_reqs
                                     if r["id"] != workshop_id]
                if on_done:
                    on_done(success)
        threading.Thread(target=_run, daemon=True).start()

    # ── public IP ─────────────────────────────────────────────────────────────

    def check_public_ip(self) -> None:
        import json as _json
        def _do() -> None:
            try:
                with urllib.request.urlopen(
                        "https://api.ipify.org?format=json", timeout=8) as r:
                    ip = _json.loads(r.read()).get("ip", "")
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
        players: list[dict] = []
        pattern = re.compile(
            r"^#\s+(\d+)\s+"
            r'"([^"]*)"'
            r"\s+(STEAM_\S+|\[U:\S+)"
            r"\s+(\S+)"
            r"\s+(\d+)",
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
                self.log(f"Kicked: {name or userid}")
            except Exception as exc:
                self.log(f"Kick failed: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def ban_player(self, steamid: str, name: str = "",
                   duration: int = 0) -> None:
        def _do() -> None:
            try:
                self.rcon.execute(f"banid {duration} {steamid}")
                self.rcon.execute("writeid")
                self.log(f"Banned: {name or steamid} "
                         f"({'permanent' if duration == 0 else f'{duration} min'})")
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
                out   = self.rcon.execute("listid")
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
        def _do() -> None:
            try:
                self.rcon.execute(f"mp_friendlyfire {1 if enabled else 0}")
                self.rcon.execute(f"mp_autokick {0 if enabled else 1}")
                self._ff_enabled = enabled
                self.log("Friendly fire "
                         + ("ENABLED (autokick off)" if enabled else "DISABLED (autokick on)"))
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

    # ── workshop name fetching ────────────────────────────────────────────────

    def fetch_workshop_names(self, ids: list[str],
                              on_done: Callable | None = None) -> None:
        import json as _json
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
                    data = _json.loads(resp.read())
                for item in data.get("response", {}).get("publishedfiledetails", []):
                    wid   = item.get("publishedfileid", "")
                    title = item.get("title", "").strip()
                    if wid and title:
                        self._map_name_cache[wid] = title
                        self.log(f"  Workshop name: {wid} → {title}")
                    elif wid:
                        self.log(f"  Workshop name: {wid} → (no title returned)")
            except Exception as exc:
                self.log(f"Workshop name fetch failed: {exc}")
            finally:
                if on_done:
                    on_done()
        threading.Thread(target=_do, daemon=True).start()
