"""
core.py — AppCore: single source of truth for server state.

Consumed by the Flask web panel (web.py).  All blocking work runs on daemon
threads; results are delivered via typed callbacks that callers register after
construction, and via instance attributes polled by the REST API.
"""
from __future__ import annotations

import collections
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import shutil
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable

from . import config as _config
from .config import (
    # Non-path constants — safe to bind by name (never change at runtime)
    CS2_APP_ID,
    DEPOTDL_RELEASE_URL,
    OFFICIAL_MAPS,
    RCON_HOST, RCON_PORT, RCON_PASSWORD,
    MODE_SETTINGS, _DEFAULT_MODE,
    _CONFIG_FILE,
    APP_VERSION, APP_API_URL, APP_RELEASES_URL,
    # Path constants (CS2_SERVER_DIR, CS2_PATH, STEAMCMD_PATH, WORKSHOP_DIR,
    # DEPOTDL_PATH, CS2_ADDONS_DIR) are accessed via _config.* so that
    # update_paths() changes are always picked up at call time.
)
from .rcon import RCONClient


# ── Plugin deployment tables ───────────────────────────────────────────────────

# Bundled plugins live next to this file inside cs2servergui/plugins/
def _resolve_plugins_base() -> str:
    """Find the bundled plugins/ folder across dev and packaged layouts.

    Tries, in order:
      1. <this_file>/../plugins         — source layout (python -m / IDE run)
      2. sys._MEIPASS/cs2servergui/plugins — PyInstaller --onefile temp extract
      3. <exe_dir>/cs2servergui/plugins — PyInstaller --onedir, nested layout
      4. <exe_dir>/plugins              — PyInstaller --onedir, flat layout

    Returns the first existing path, or the source-layout fallback so error
    messages still point somewhere sensible.
    """
    candidates: list[str] = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins"),
    ]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "cs2servergui", "plugins"))
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, "cs2servergui", "plugins"))
        candidates.append(os.path.join(exe_dir, "plugins"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[0]

_PLUGINS_BASE = _resolve_plugins_base()

# Hard upper bound for DepotDownloader workshop downloads (seconds).
# Named here so it appears in one place — change this to extend/shorten the window.
_DL_TIMEOUT_SECS: int = 600

# Workshop maps that run server commands from their own map logic get those
# commands blocked unless the server launches with -disable_workshop_command_filtering.
# Map authors that need it almost always say so in the Workshop description, so we
# auto-detect by matching the flag name there.
_CMDFILTER_RE = re.compile(r"-?disable_workshop_command_filtering", re.IGNORECASE)


def _semver_tuple(v: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple.

    Stable release sorts above a same-numbered pre-release:
      "1.0.0"       → (1, 0, 0, 1)   # stable
      "1.0.0-beta"  → (1, 0, 0, 0)   # pre-release < same stable
    """
    try:
        clean  = v.strip().lstrip("v")
        parts  = clean.split("-", 1)          # ["1.0.0"] or ["1.0.0","beta"]
        nums   = tuple(int(x) for x in parts[0].split("."))
        stable = 1 if len(parts) == 1 else 0  # stable > pre-release
        return nums + (stable,)
    except ValueError:
        return (0,)

# DLL filenames that are part of the CounterStrikeSharp host process and must
# NOT be copied into a plugin folder.  Plugin release ZIPs often bundle the
# entire CSS SDK alongside the plugin DLL; loading duplicates inside a plugin's
# AssemblyLoadContext causes type-identity conflicts and can crash the plugin or
# the entire CSS host.  These are always resolved from the host's already-loaded
# assemblies, so having them in the plugin folder is both wrong and harmful.
_CSS_HOST_DLLS: frozenset[str] = frozenset({
    "counterstrikesharp.api.dll",
    "mcmaster.netcore.plugins.dll",
    "scrutor.dll",
    # Serilog — CSS's logging stack
    "serilog.dll",
    "serilog.extensions.logging.dll",
    "serilog.sinks.console.dll",
    "serilog.sinks.file.dll",
    # Roslyn / compilation — only needed by CSS for script plugins
    "microsoft.codeanalysis.dll",
    "microsoft.codeanalysis.csharp.dll",
    # Microsoft.Extensions family — already provided by the CSS host runtime
    "microsoft.extensions.configuration.abstractions.dll",
    "microsoft.extensions.configuration.binder.dll",
    "microsoft.extensions.configuration.commandline.dll",
    "microsoft.extensions.configuration.dll",
    "microsoft.extensions.configuration.environmentvariables.dll",
    "microsoft.extensions.configuration.fileextensions.dll",
    "microsoft.extensions.configuration.json.dll",
    "microsoft.extensions.configuration.usersecrets.dll",
    "microsoft.extensions.dependencyinjection.abstractions.dll",
    "microsoft.extensions.dependencyinjection.dll",
    "microsoft.extensions.dependencymodel.dll",
    "microsoft.extensions.diagnostics.abstractions.dll",
    "microsoft.extensions.diagnostics.dll",
    "microsoft.extensions.fileproviders.abstractions.dll",
    "microsoft.extensions.fileproviders.physical.dll",
    "microsoft.extensions.filesystemglobbing.dll",
    "microsoft.extensions.hosting.abstractions.dll",
    "microsoft.extensions.hosting.dll",
    "microsoft.extensions.localization.abstractions.dll",
    "microsoft.extensions.logging.abstractions.dll",
    "microsoft.extensions.logging.configuration.dll",
    "microsoft.extensions.logging.console.dll",
    "microsoft.extensions.logging.debug.dll",
    "microsoft.extensions.logging.dll",
    "microsoft.extensions.logging.eventsource.dll",
    "microsoft.extensions.logging.eventlog.dll",
    "microsoft.extensions.options.configurationextensions.dll",
    "microsoft.extensions.options.dll",
    "microsoft.extensions.primitives.dll",
    "microsoft.dotnet.platformabstractions.dll",
    # System extras already in the .NET 8 runtime shipped with CS2
    "system.collections.immutable.dll",
    "system.diagnostics.eventlog.dll",
    "system.io.pipelines.dll",
    "system.reflection.metadata.dll",
    "system.text.encodings.web.dll",
    # NOTE: "system.text.json.dll" intentionally NOT listed here.
    # WarcraftPlugin bundles System.Text.Json v10.0.8 which is newer than the
    # v8.x shipped with the CS2 .NET 8 runtime. Listing it here would cause
    # CSS to skip deploying the plugin's copy, resulting in a
    # FileNotFoundException when WarcraftPlugin tries to load.
})

# How each plugin loads into CS2.
#   "metamod" — loaded at engine init, can ONLY activate after a server restart
#   "css"     — CounterStrikeSharp; can be hot-reloaded via `css_plugins reload`
_PLUGIN_KIND: dict[str, str] = {
    "zombie":    "metamod",   # ZombieMod (CS2Fixes fork) — engine-level ZE/ZM fixes, zm_enable 0
    "zombie_ze": "metamod",   # ZE layer: MultiAddonManager + zm_enable 1 override cfg
    "deathmatch":"css",
    "arenas":    "css",
    "practice":  "css",
    "jailbreak": "css",
    "warcraft":  "css",       # RPG classes, XP, items overlay
    "retakes_b3none": "css",  # Retakes: B3none RetakesPlugin + yonilerner allocator
}

# Canonical post-deploy markers: files that MUST exist relative to csgo/ for
# the plugin to actually be loadable.  Used to verify the deploy was real and
# not just "files copied to nowhere".
_PLUGIN_VERIFY_FILES: dict[str, list[str]] = {
    "zombie": [
        os.path.join("addons", "metamod", "cs2fixes.vdf"),
        os.path.join("addons", "cs2fixes", "bin", "win64", "cs2fixes.dll"),
    ],
    "zombie_ze": [
        os.path.join("addons", "metamod", "multiaddonmanager.vdf"),
        os.path.join("addons", "multiaddonmanager", "bin", "multiaddonmanager.dll"),
    ],
    "deathmatch": [
        os.path.join("addons", "counterstrikesharp", "plugins",
                     "Deathmatch", "Deathmatch.dll"),
    ],
    "arenas": [
        os.path.join("addons", "counterstrikesharp", "plugins",
                     "K4-Arenas", "K4-Arenas.dll"),
        os.path.join("addons", "counterstrikesharp", "plugins",
                     "K4-Arenas-Bots", "K4-Arenas-Bots.dll"),
    ],
    "practice": [
        os.path.join("addons", "counterstrikesharp", "plugins",
                     "MatchZy", "MatchZy.dll"),
    ],
    "jailbreak": [
        os.path.join("addons", "counterstrikesharp", "plugins",
                     "Jailbreak", "Jailbreak.dll"),
    ],
    "warcraft": [
        os.path.join("addons", "counterstrikesharp", "plugins",
                     "WarcraftPlugin", "WarcraftPlugin.dll"),
        os.path.join("addons", "counterstrikesharp", "plugins",
                     "ModelPrecacher", "ModelPrecacher.dll"),
    ],
    "retakes_b3none": [
        os.path.join("addons", "counterstrikesharp", "plugins",
                     "RetakesPlugin", "RetakesPlugin.dll"),
        os.path.join("addons", "counterstrikesharp", "plugins",
                     "RetakesAllocator", "RetakesAllocator.dll"),
    ],
}

# Modes that need managed plugins; modes not listed → vanilla server.
_MODE_PLUGIN_NAMES: dict[str, list[str]] = {
    # Retakes: B3none's dedicated RetakesPlugin (curated per-map spawns +
    # in-game spawn editor) with yonilerner's RetakesAllocator.  (MatchZy has no
    # retakes feature, so the earlier MatchZy-based Retakes mode was dropped.)
    "Retakes":             ["retakes_b3none"],
    "Deathmatch":          ["zombie", "deathmatch"],
    "1v1":                 ["arenas"],     # fixed-map dueling
    "3v3":                 ["arenas"],
    "4v4":                 ["arenas"],
    # Jailbreak runs the self-contained CSS Jailbreak plugin ALONE.  It used to
    # also deploy "zombie" (CS2Fixes), but loading that heavy native MetaMod
    # plugin alongside Jailbreak crashed the server with a native access
    # violation ~1-2s after the plugin loaded — reliably, on every jb start
    # (3/3 crash dumps on 2026-05-28), while no other mode ever crashed.  The
    # Jailbreak plugin needs nothing from CS2Fixes, so it's dropped.
    "Jailbreak":           ["jailbreak"],
    "Practice":            ["practice"],   # MatchZy controls match flow
    # Warcraft: RPG overlay — 9 classes, XP system, purchasable items.
    "Warcraft":            ["warcraft"],
    # Zombie Escape: ZombieMod engine fixes (zm_enable 0 from base zombie plugin)
    # + zombie_ze layer that activates zm_enable 1 and loads ZombieReborn workshop
    # content pack (ID 3157463861) via MultiAddonManager.
    "Zombie Escape":       ["zombie", "zombie_ze"],
    # All other modes (Competitive, Casual, Wingman, Arms Race, Demolition, …)
    # are not listed here — _MODE_PLUGIN_NAMES.get(mode, []) returns [] for them,
    # meaning deploy_plugins() runs a vanilla server with no managed plugins.
}

# Copy rules per plugin: list of (src_subdir, dst_subdir_relative_to_csgo[, exclude_subdirs]).
# The CONTENTS of src_subdir are merged into dst_subdir.
# Optional third element: frozenset of immediate subdirectory names to skip during the walk.
_PLUGIN_COPY_RULES: dict[str, list[tuple]] = {
    "zombie": [
        # zombie/ root mirrors csgo/ layout — metamod-only, no CSS needed
        ("addons",      "addons"),
        ("cfg",         "cfg"),
        ("materials",   "materials"),
        ("particles",   "particles"),
        ("soundevents", "soundevents"),
        ("sounds",      "sounds"),
    ],
    "deathmatch": [
        ("addons", "addons"),
    ],
    "arenas": [
        ("addons", "addons"),
    ],
    "practice": [
        ("addons", "addons"),
        ("cfg", "cfg"),
    ],
    "jailbreak": [
        ("addons", "addons"),
    ],
    # WarcraftPlugin's Barbarian class assigns the non-default player models
    # tm_phoenix_heavy (T) / ctm_heavy (CT).  They live in pak01.vpk but the
    # engine only auto-precaches the DEFAULT team models, so SetModel on them
    # logged "requested but is not in the system (Missing from a manifest?)" and
    # Barbarian rendered the error model.  Loose .vmdl_c copies do NOT help — CS2
    # only loads models listed in the precache manifest — so the bundled
    # ModelPrecacher plugin (under addons/) AddResource()s both models in
    # OnServerPrecacheResources instead.  Hence addons/ only; no characters/.
    "warcraft": [
        ("addons", "addons"),
    ],
    # zombie_ze: MultiAddonManager MetaMod plugin + cfg overrides.
    # Mirrors csgo/ layout exactly (addons/ and cfg/).  Deployed AFTER zombie so
    # the cs2fixes.cfg override (zm_enable 1) wins over zombie's default (zm_enable 0).
    "zombie_ze": [
        ("addons", "addons"),
        ("cfg",    "cfg"),
    ],
    # Retakes (B3none) — addons/ (RetakesPlugin + RetakesAllocator +
    # RetakesPluginShared + retakes_config.json that disables fallback allocation
    # so the allocator owns weapons) and cfg/ (our cs2-retakes/retakes.cfg that
    # auto-fills bots instead of B3none's default bot_kick / bot_quota 0, so
    # retake rounds form on a low-population server; RetakesPlugin execs it).
    "retakes_b3none": [
        ("addons", "addons"),
        ("cfg",    "cfg"),
    ],
}

# Items relative to csgo/ that are fully owned by each plugin.
# Directories are rmtree'd, files are unlinked on undeploy.
_PLUGIN_CLEANUP_ITEMS: dict[str, list[str]] = {
    "zombie": [
        os.path.join("addons", "cs2fixes"),
        os.path.join("addons", "metamod", "cs2fixes.vdf"),
        os.path.join("cfg", "cs2fixes"),
        os.path.join("materials", "cs2fixes"),
        os.path.join("particles", "cs2fixes"),
        os.path.join("soundevents", "cs2fixes"),
        os.path.join("sounds", "cs2fixes"),      # zombie deploys sounds/ too
    ],
    "deathmatch": [
        os.path.join("addons", "counterstrikesharp", "plugins", "Deathmatch"),
        os.path.join("addons", "counterstrikesharp", "shared", "DeathmatchAPI"),
    ],
    "arenas": [
        os.path.join("addons", "counterstrikesharp", "plugins", "K4-Arenas"),
        os.path.join("addons", "counterstrikesharp", "plugins", "K4-Arenas-Bots"),
        os.path.join("addons", "counterstrikesharp", "shared", "K4-ArenaSharedApi"),
        os.path.join("addons", "counterstrikesharp", "shared", "KitsuneMenu"),
    ],
    "practice": [
        os.path.join("addons", "counterstrikesharp", "plugins", "MatchZy"),
        os.path.join("cfg", "MatchZy"),
        # Legacy scrub: older builds deployed this for the (defunct) MatchZy
        # retakes mode.  We no longer ship it; keep the entry so existing installs
        # get the orphaned file removed on the next mode switch.
        os.path.join("cfg", "retakes.cfg"),
    ],
    "jailbreak": [
        os.path.join("addons", "counterstrikesharp", "plugins", "Jailbreak"),
    ],
    "warcraft": [
        os.path.join("addons", "counterstrikesharp", "plugins", "WarcraftPlugin"),
        os.path.join("addons", "counterstrikesharp", "plugins", "ModelPrecacher"),
        os.path.join("addons", "counterstrikesharp", "configs", "plugins", "WarcraftPlugin"),
        # Legacy scrubs: earlier attempts shipped loose Barbarian model files
        # here (and a wrong ctm_st6 variant).  Loose models don't fix precache,
        # so we no longer deploy any characters/ files; keep these entries so
        # installs that still carry them get the orphans removed on the next
        # mode switch.  These paths only ever held our own files, never stock
        # game assets, so removing them is safe.
        os.path.join("characters", "models", "tm_phoenix_heavy"),
        os.path.join("characters", "models", "ctm_heavy"),
        os.path.join("characters", "models", "ctm_st6", "ctm_st6_variantn.vmdl_c"),
    ],
    "zombie_ze": [
        os.path.join("addons", "multiaddonmanager"),
        os.path.join("addons", "metamod", "multiaddonmanager.vdf"),
        os.path.join("cfg", "multiaddonmanager"),
        # Remove zm_enable 1 override so DM/JB don't run in ZE mode after switching.
        # If zombie stays in new_plugins (e.g. switch to Deathmatch), it re-deploys
        # its own cs2fixes.cfg (zm_enable 0), restoring the correct default.
        # If switching to vanilla (zombie also unneeded), the whole cfg/cs2fixes/
        # folder is wiped by zombie's cleanup items anyway.
        os.path.join("cfg", "cs2fixes", "cs2fixes.cfg"),
    ],
    # EXPERIMENTAL B3none retakes — remove every piece so switching back to the
    # MatchZy "Retakes" mode (or anything else) leaves no cross-contamination.
    "retakes_b3none": [
        os.path.join("addons", "counterstrikesharp", "plugins", "RetakesPlugin"),
        os.path.join("addons", "counterstrikesharp", "plugins", "RetakesAllocator"),
        os.path.join("addons", "counterstrikesharp", "shared", "RetakesPluginShared"),
        os.path.join("addons", "counterstrikesharp", "configs", "plugins", "RetakesPlugin"),
        os.path.join("addons", "counterstrikesharp", "configs", "plugins", "RetakesAllocator"),
        os.path.join("cfg", "cs2-retakes"),
    ],
}


class AppCore:
    """Single source of truth shared between the local GUI and Flask."""

    def __init__(self) -> None:
        self.proc:         subprocess.Popen | None = None
        self.running:      bool = False
        self.boot_state:   str  = "offline"   # "offline" | "booting" | "ready"
        self.player_count: int  = 0            # live count; updated every ~15 s by monitor
        self.current_map:  str  = "de_dust2"
        self.current_mode: str  = "Competitive"

        # RCONClient created with empty password; updated after _load_config()
        # so the runtime-configured password (not the config.py default) is used.
        self.rcon = RCONClient(RCON_HOST, RCON_PORT, "")

        self._log_buf  = collections.deque(maxlen=300)
        self._log_lock = threading.Lock()

        self._sse_qs:  list[queue.Queue] = []
        self._sse_lock = threading.Lock()

        self._dl_reqs: list[dict]        = []
        self._dl_lock  = threading.Lock()

        # Guards the server-lifecycle state block (proc / running / boot_state /
        # _uptime_start) so the stop, boot-ready, and crash transitions can't
        # interleave — e.g. stop flipping running=False between the poller's
        # "is it still running?" check and its "mark ready" write. Held only for
        # the brief state mutations, never across blocking I/O.
        self._lifecycle_lock = threading.RLock()

        self.update_available:      bool = False
        self.app_update_available:  bool = False
        self.app_latest_version:    str  = ""

        # Pending workshop map to load via RCON once the server is ready.
        # Set by start_server() when is_workshop=True; cleared by _poll_rcon_ready().
        self._pending_workshop_map: str | None = None

        # Install location — all other paths are derived from this
        self.server_dir: str = ""

        # Steam credentials (password stored in OS keyring when available)
        self.steam_username: str = ""
        self.steam_password: str = ""

        # Security — loaded from config; auto-generated on first run
        self.rcon_password: str = ""   # used as CS2's +rcon_password arg
        self.admin_pin:     str = ""   # 4-digit PIN for the web panel

        # One-time token set by main.py so the local pywebview window can
        # auto-authenticate without entering the PIN.  Cleared after first use.
        self.startup_token: str = ""

        # Server config (persisted)
        self.hostname:             str  = "CS2 Dedicated Server"
        self.sv_password:          str  = ""
        self.gslt_token:           str  = ""   # Steam Game Server Login Token
        self.tickrate_128:         bool = False
        self.auto_start:           bool = False
        self.auto_restart_on_crash: bool = False
        self.bot_difficulty:       str  = "Normal"
        self.max_players_override: str  = ""
        self.presets:              dict[str, dict] = {}

        # Runtime state
        self.public_ip:           str                      = ""
        self._uptime_start:       float | None             = None   # set when server is ready
        self._map_name_cache:     dict[str, str]           = {}
        self._map_tag_cache:      dict[str, list[str]]    = {}  # wid → lowercase tags
        self._preview_url_cache:  dict[str, str]          = {}  # wid → Steam preview URL
        # Workshop command-filter handling (wid → bool).  _auto is derived from the
        # Steam description; _override is the manual GUI choice and wins when set.
        # Both persist in the config.  Effective value gates the launch flag
        # -disable_workshop_command_filtering for that map.
        self._cmdfilter_auto:     dict[str, bool]          = {}
        self._cmdfilter_override: dict[str, bool]          = {}
        self._ff_enabled:         bool                     = False
        # Host's per-session choice: fill empty slots with bots (Arena bot-fill,
        # etc.). Default off → humans-only. See deploy_plugins (K4-Arenas-Bots).
        self.bots_enabled:        bool                     = False
        self._active_dl_proc:     subprocess.Popen | None  = None
        # Live workshop-download progress, surfaced via /api/state for the UI.
        # Empty dict = no download in flight.  While downloading:
        #   {"id", "downloaded", "total", "pct", "phase"}  (bytes; phase is one
        #   of "downloading" | "verifying").
        self._dl_progress:        dict                     = {}
        self.steam_session_active: bool                    = False

        # fired (no args) when steam_session_active changes
        self.on_steam_session_change: Callable[[], None] | None = None

        # GUI / web callbacks — must be initialised before _load_config() because
        # _load_config() calls self.log() which checks self.on_log.
        self.on_log:                Callable[[str], None] | None                   = None
        self.on_state_change:       Callable[[], None] | None                      = None
        self.on_update_checked:     Callable[[bool, str, str], None] | None        = None
        self.on_public_ip:          Callable[[str], None] | None                   = None
        # (available, current_ver, latest_ver, download_url)
        self.on_app_update_checked: Callable[[bool, str, str, str], None] | None   = None

        self._load_config()

        # After config is loaded, sync the RCON password into the client and
        # the module-level constant so existing code that reads _config.RCON_PASSWORD
        # gets the runtime value (not the empty-string placeholder in config.py).
        self.rcon.password = self.rcon_password
        _config.RCON_PASSWORD = self.rcon_password
        _config.ADMIN_PIN     = self.admin_pin

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

    # ── keyring helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _keyring_save(username: str, password: str) -> bool:
        """Save *password* in the OS credential store. Returns True on success."""
        try:
            import keyring  # type: ignore
            keyring.set_password("OblivionServerTool", username, password)
            return True
        except Exception:
            return False

    @staticmethod
    def _keyring_load(username: str) -> str:
        """Return the password for *username* from the OS credential store, or ''."""
        try:
            import keyring  # type: ignore
            return keyring.get_password("OblivionServerTool", username) or ""
        except Exception:
            return ""

    def _load_config(self) -> None:
        import secrets as _secrets

        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            cfg = {}
        except Exception as exc:
            self.log(f"Config load warning: {exc}")
            cfg = {}

        # server_dir first — everything else depends on it
        saved_dir = cfg.get("server_dir", "")
        if saved_dir:
            self.server_dir = saved_dir
            _config.update_paths(saved_dir)

        # ── Security fields ────────────────────────────────────────────────
        # RCON password: load from config, or auto-generate and save on first run.
        self.rcon_password = cfg.get("rcon_password", "").strip()
        if not self.rcon_password:
            self.rcon_password = _secrets.token_urlsafe(16)
            self.log("Generated new RCON password (first run)")

        # Admin PIN: default to "1234" if never configured, prompt user to change.
        self.admin_pin = cfg.get("admin_pin", "1234")

        # ── Steam credentials ──────────────────────────────────────────────
        self.steam_username = cfg.get("steam_username", "")
        self.steam_session_active = bool(cfg.get("steam_session_active", False))

        # Password: prefer OS keyring; fall back to plaintext in config.
        stored_pw = cfg.get("steam_password", "")
        if stored_pw == "__keyring__" and self.steam_username:
            self.steam_password = self._keyring_load(self.steam_username)
        else:
            self.steam_password = stored_pw  # legacy plaintext or empty

        # ── Server settings ────────────────────────────────────────────────
        self.hostname              = cfg.get("hostname", "CS2 Dedicated Server")
        self.sv_password           = cfg.get("sv_password", "")
        self.gslt_token            = cfg.get("gslt_token", "")
        self.tickrate_128          = bool(cfg.get("tickrate_128", False))
        self.auto_start            = bool(cfg.get("auto_start", False))
        self.auto_restart_on_crash = bool(cfg.get("auto_restart_on_crash", False))
        self.bot_difficulty        = cfg.get("bot_difficulty", "Normal")
        self.bots_enabled          = bool(cfg.get("bots_enabled", False))
        self.max_players_override  = cfg.get("max_players_override", "")
        self.presets               = cfg.get("presets", {})

        # Workshop command-filter detection results + manual overrides (wid → bool).
        self._cmdfilter_auto       = dict(cfg.get("cmdfilter_auto", {}))
        self._cmdfilter_override   = dict(cfg.get("cmdfilter_override", {}))

        # Persist immediately if we just auto-generated the RCON password so
        # that the next startup (and the server launch) uses the same value.
        if not cfg.get("rcon_password"):
            self.save_config()

    def save_config(self) -> None:
        try:
            # Steam password: try to store in OS keyring; fall back to plaintext.
            if self.steam_username and self.steam_password:
                if self._keyring_save(self.steam_username, self.steam_password):
                    steam_pw_stored = "__keyring__"
                else:
                    steam_pw_stored = self.steam_password  # keyring unavailable
            else:
                steam_pw_stored = ""

            cfg = {
                "server_dir":            self.server_dir,
                "rcon_password":         self.rcon_password,
                "admin_pin":             self.admin_pin,
                "steam_username":        self.steam_username,
                "steam_password":        steam_pw_stored,
                "steam_session_active":  self.steam_session_active,
                "hostname":              self.hostname,
                "sv_password":           self.sv_password,
                "gslt_token":            self.gslt_token,
                "tickrate_128":          self.tickrate_128,
                "auto_start":            self.auto_start,
                "auto_restart_on_crash": self.auto_restart_on_crash,
                "bot_difficulty":        self.bot_difficulty,
                "bots_enabled":          self.bots_enabled,
                "max_players_override":  self.max_players_override,
                "presets":               self.presets,
                "cmdfilter_auto":        self._cmdfilter_auto,
                "cmdfilter_override":    self._cmdfilter_override,
            }
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            self.log(f"Config saved  ({_CONFIG_FILE})")
        except Exception as exc:
            self.log(f"Config save failed: {exc}")

    # ── server control ────────────────────────────────────────────────────────

    def _fix_metamod_dll_nesting(self) -> None:
        """Fix MetaMod bin/win64/win64/ nesting caused by incorrect zip extraction.

        When MetaMod is installed from a GitHub zip that internally uses a bin/win64/
        path, some extraction tools create an extra win64/ subfolder, placing all the
        per-game DLLs (especially metamod.2.cs2.dll) one level too deep.  MetaMod's
        server.dll searches for metamod.2.cs2.dll in the SAME directory as itself
        (bin/win64/), so the nested path causes the fatal-log error:
            "Detected engine 26 but could not load: The specified module could not be found."
        The server then runs vanilla — no MetaMod, no CSS, no plugins.

        This method detects the nesting and silently moves DLLs up one level.
        """
        mm_bin = os.path.join(self._csgo_dir(), "addons", "metamod", "bin", "win64")
        nested = os.path.join(mm_bin, "win64")
        if not os.path.isdir(nested):
            return  # layout already correct

        dlls = [f for f in os.listdir(nested) if f.lower().endswith(".dll")]
        if not dlls:
            return

        self.log("[pre-launch] Detected MetaMod bin/win64/win64/ nesting — fixing…")
        moved = 0
        for fname in dlls:
            src = os.path.join(nested, fname)
            dst = os.path.join(mm_bin, fname)
            try:
                shutil.copy2(src, dst)
                moved += 1
            except Exception as exc:
                self.log(f"[pre-launch]   ✗ Could not move {fname}: {exc}")
        try:
            shutil.rmtree(nested)
        except Exception as exc:
            self.log(f"[pre-launch]   ✗ Could not remove nested dir: {exc}")
        self.log(f"[pre-launch] ✓ Moved {moved} DLL(s) to correct path — MetaMod will now load")

    def _ensure_dota_gameinfo(self) -> None:
        """Create game/dota/gameinfo.gi if missing.

        The CS2 dedicated server binary (Source 2 engine) looks for a Dota 2
        gameinfo.gi alongside csgo/ at startup.  When the file is absent the
        engine logs "Could not read file: …/dota/gameinfo.gi" and exits with
        code 1 before loading anything else — including plugins, RCON, or maps.
        This is a known issue with every fresh CS2 dedicated server install;
        Valve does not ship the file in the server package.

        The file content is a minimal-but-valid gameinfo.gi stub; the engine
        only needs to be able to open and parse it.
        """
        game_dir  = os.path.dirname(self._csgo_dir())   # …/game/
        dota_dir  = os.path.join(game_dir, "dota")
        dota_gi   = os.path.join(dota_dir, "gameinfo.gi")
        if os.path.isfile(dota_gi):
            return   # already present — nothing to do
        self.log("[pre-launch] dota/gameinfo.gi missing — creating stub…")
        try:
            os.makedirs(dota_dir, exist_ok=True)
            with open(dota_gi, "w", encoding="utf-8") as f:
                f.write(
                    '"GameInfo"\n'
                    "{\n"
                    '\tgame\t"Dota 2"\n'
                    '\ttitle\t"Dota 2"\n'
                    "\n"
                    "\tFileSystem\n"
                    "\t{\n"
                    "\t\tSteamAppId\t\t570\n"
                    "\n"
                    "\t\tSearchPaths\n"
                    "\t\t{\n"
                    "\t\t\tGame\t\tdota\n"
                    "\t\t}\n"
                    "\t}\n"
                    "}\n"
                )
            self.log("[pre-launch] ✓ dota/gameinfo.gi created")
        except Exception as exc:
            self.log(f"[pre-launch] ✗ Could not create dota/gameinfo.gi: {exc}")
            self.log("[pre-launch]   Create it manually — see README or docs.")

    def start_server(self, map_name: str, mode: str,
                     is_workshop: bool = False) -> None:
        # Deploy plugins synchronously before launching cs2.exe so files are in
        # place when the engine initialises MetaMod / CounterStrikeSharp.
        self.deploy_plugins(mode)

        # Fix MetaMod bin/win64/win64/ nesting from bad zip extraction — if
        # metamod.2.cs2.dll landed one level too deep, MetaMod writes to its
        # fatal log and the server runs vanilla (no plugins) on every start.
        self._fix_metamod_dll_nesting()

        # Ensure the Source 2 engine cross-game stub exists — the dedicated
        # server exits with code 1 immediately if this file is missing.
        self._ensure_dota_gameinfo()

        s    = MODE_SETTINGS.get(mode, _DEFAULT_MODE)
        maxp = self.max_players_override.strip() or s["maxplayers"]

        # CS2 dedicated servers don't reliably honour +host_workshop_map at
        # startup — the map either fails to load or the server crashes before
        # the engine downloads it.  The reliable pattern is to start on any
        # standard map, wait for RCON, then issue host_workshop_map via RCON.
        # We save the workshop ID and let _poll_rcon_ready fire it automatically.
        if is_workshop:
            startup_map = OFFICIAL_MAPS[0]
            self._pending_workshop_map = map_name
            self.log(f"[workshop] Will auto-load {map_name} via RCON once server is ready")
        else:
            startup_map = map_name
            self._pending_workshop_map = None

        # ── Pre-launch: kill any lingering dedicated cs2.exe ─────────────────
        # A previous crash or hard-kill may leave a zombie dedicated server that
        # still holds port 27015.  The new instance can't bind the port → it
        # exits within seconds before RCON ever opens.  Kill it proactively.
        # IMPORTANT: only kill processes launched with -dedicated so we never
        # accidentally close the user's CS2 game client (also named cs2.exe).
        try:
            res = subprocess.run(
                ["wmic", "process", "where", "name='cs2.exe'",
                 "get", "ProcessId,CommandLine", "/format:csv"],
                capture_output=True, text=True, timeout=5,
            )
            killed = 0
            for line in res.stdout.splitlines():
                if "-dedicated" in line.lower():
                    # Extract PID — last comma-separated field on CSV rows
                    parts = line.strip().split(",")
                    pid = parts[-1].strip()
                    if pid.isdigit():
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=5,
                        )
                        killed += 1
            if killed:
                time.sleep(1.5)   # give the OS a moment to release port 27015
                self.log(f"[!] Killed {killed} lingering dedicated cs2.exe — proceeding with fresh start")
        except Exception as exc:
            self.log(f"[!] Pre-launch process check failed: {exc}")

        cmd  = [
            _config.CS2_PATH, "-dedicated",
            # Mirror the full engine console (incl. native crash output) to
            # csgo/console.log.  CSS/MetaMod logs live elsewhere, so without this
            # a native access violation leaves no trace — which is exactly what
            # made the Jailbreak crash hard to diagnose.
            "-condebug",
            "-port",          str(RCON_PORT),
            "+sv_lan",        "0",
            "+game_type",     s["game_type"],
            "+game_mode",     s["game_mode"],
            "-maxplayers",    maxp,
            "+rcon_password", self.rcon_password,
            "+hostname",      self.hostname or "CS2 Dedicated Server",
        ]
        if self.sv_password:
            cmd += ["+sv_password", self.sv_password]
        if self.tickrate_128:
            cmd += ["-tickrate", "128"]
        if self.gslt_token:
            cmd += ["+sv_setsteamaccount", self.gslt_token]
        # Some workshop maps run server commands from their own map logic, which
        # CS2 blocks unless launched with this flag.  Only added for workshop maps
        # that are flagged (auto-detected from the Steam description or a manual
        # override) so the command filter stays ON for everything else.
        if is_workshop and self.cmdfilter_effective(map_name):
            cmd.append("-disable_workshop_command_filtering")
            self.log("[workshop] Launching with -disable_workshop_command_filtering "
                     f"for {map_name}")
        cmd += ["+map", startup_map]
        _server_env = os.environ.copy()

        try:
            self.proc = subprocess.Popen(cmd, env=_server_env)
        except FileNotFoundError:
            self.log(f"CS2 executable not found: {_config.CS2_PATH}")
            return
        with self._lifecycle_lock:
            self.running      = True
            self.boot_state   = "booting"
            self.current_map  = startup_map   # updated to workshop ID by _poll_rcon_ready on success
            self.current_mode = mode
        self.log(f"Server started  |  map: {map_name}  |  mode: {mode}")
        if self.tickrate_128:
            self.log("  Tickrate 128 enabled")
        if self.sv_password:
            self.log("  Server password set")
        if self.gslt_token:
            self.log("  GSLT token set (+sv_setsteamaccount)")
        self.log(f"Polling RCON at {RCON_HOST}:{RCON_PORT} — waiting for server…")
        if self.on_state_change:
            self.on_state_change()
        threading.Thread(target=self._poll_rcon_ready, daemon=True).start()

    def stop_server(self) -> None:
        # Flip lifecycle state to "stopped" synchronously, then do the actual
        # (potentially slow) process termination on a background thread.
        #
        # Why state-first: the crash monitor and RCON-ready poller key off
        # running/proc; clearing them up-front stops a spurious "crashed"
        # auto-restart and the poller re-marking a just-stopped server "ready".
        #
        # Why terminate off-thread: killing cs2.exe and waiting for it to exit
        # can take several seconds — especially when a CS2 game client is running
        # on the same machine (heavy CPU/GPU load).  Doing that inside the Flask
        # request thread holds the HTTP response open long enough that the
        # WebView2/browser fetch() can drop the connection ("Failed to fetch").
        # Returning immediately keeps the UI responsive; the kill finishes in the
        # background and on_state_change has already updated the UI.
        with self._lifecycle_lock:
            proc               = self.proc
            was_running        = self.running
            self.proc          = None
            self.running       = False
            self.boot_state    = "offline"
            self.player_count  = 0
            self._uptime_start = None
        if self.on_state_change:
            self.on_state_change()

        def _terminate() -> None:
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.log("Server did not exit cleanly — killing process")
                    proc.kill()
            elif was_running:
                # Reattached session — no Popen handle.
                # Ask the server to quit via RCON; fall back to targeted kill if needed.
                self.log("Stopping reattached server…")
                try:
                    self.rcon.execute("quit")
                    time.sleep(1)
                except Exception:
                    pass
                # Confirm it's gone; if a dedicated cs2.exe is still running, kill it.
                # IMPORTANT: filter on CommandLine containing "-dedicated" so we never
                # accidentally kill the user's CS2 game client (same binary name) when
                # both run on the same machine.
                try:
                    res = subprocess.run(
                        ["wmic", "process", "where", "name='cs2.exe'",
                         "get", "ProcessId,CommandLine", "/format:csv"],
                        capture_output=True, text=True, timeout=5,
                    )
                    for line in res.stdout.splitlines():
                        if "-dedicated" in line.lower():
                            parts = line.strip().split(",")
                            pid = parts[-1].strip()
                            if pid.isdigit():
                                subprocess.run(
                                    ["taskkill", "/F", "/PID", pid],
                                    capture_output=True, timeout=5,
                                )
                except Exception:
                    pass
            self.log("Server stopped")

        threading.Thread(target=_terminate, daemon=True).start()

    def _dedicated_running(self) -> bool:
        """True if a dedicated cs2.exe (our server, launched with -dedicated) is
        still alive.  Filters on the -dedicated command line so the user's CS2
        game client (same binary name) is never mistaken for the server."""
        try:
            res = subprocess.run(
                ["wmic", "process", "where", "name='cs2.exe'",
                 "get", "CommandLine", "/format:csv"],
                capture_output=True, text=True, timeout=5,
            )
            return any("-dedicated" in line.lower()
                       for line in res.stdout.splitlines())
        except Exception:
            # If we can't tell, assume it's gone so a restart isn't blocked.
            return False

    def _restart_into(self, map_name: str, mode: str,
                      is_workshop: bool, caller: str) -> None:
        """Stop the server, wait for the process to fully exit, then start it in
        the new mode.

        Used for live mode changes that swap plugins.  Deleting a CSS plugin's
        DLL while the server has it loaded can fail on Windows (file lock),
        leaving the old plugin to reload alongside the new one.  Restarting means
        deploy_plugins() runs while the server is offline — no locked files, no
        cross-contamination, and MetaMod modes load cleanly at boot.
        """
        self.stop_server()
        # Wait for the dedicated process to actually exit so its plugin DLLs
        # unlock before start_server() redeploys.
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(1)
            if not self._dedicated_running():
                break
        else:
            self.log("[plugins] ⚠  Old server still running after 30s — starting "
                     "anyway (pre-launch cleanup will force-kill leftovers).")
        self.start_server(map_name, mode, is_workshop=is_workshop)

    def probe_existing_server(self) -> None:
        """Detect a CS2 server that was already running when the GUI launched.

        Workflow:
          1. tasklist — fast OS check; bail immediately if cs2.exe isn't there.
          2. RCON status — confirm it's our server and pull the current map.
          3. If both succeed → mark running/ready and fire on_state_change.

        Runs on a daemon thread so it never blocks the UI.
        """
        def _do() -> None:
            # ── 1. OS process check ───────────────────────────────────────────
            try:
                res = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq cs2.exe",
                     "/NH", "/FO", "CSV"],
                    capture_output=True, text=True, timeout=5,
                )
                if "cs2.exe" not in res.stdout.lower():
                    return   # nothing running — normal cold start
            except Exception:
                return

            # ── 2. RCON handshake ─────────────────────────────────────────────
            self.log("Detected running CS2 process — attempting to reconnect…")
            try:
                out = self.rcon.execute("status")
            except Exception as exc:
                self.log(f"CS2 process found but RCON not reachable: {exc}")
                return

            # ── 3. Mark as running and parse current map + mode ──────────────
            self.running    = True
            self.boot_state = "ready"

            map_m = re.search(r"^map\s*:\s*(\S+)", out,
                              re.MULTILINE | re.IGNORECASE)
            if map_m:
                self.current_map = map_m.group(1).split()[0]  # strip trailing junk

            # RCON 'status' doesn't expose game_type/game_mode in a parseable
            # form.  The plugin manifest records the last deployed mode which is
            # the best available proxy — far better than leaving it stale as
            # "Competitive" after a reconnect to a Zombies or Retakes server.
            manifest = self._load_plugin_manifest()
            if manifest.get("mode"):
                self.current_mode = manifest["mode"]

            self.log(
                f"Reconnected to existing server  |  map: {self.current_map}"
                f"  |  mode: {self.current_mode}"
            )
            if self.on_state_change:
                self.on_state_change()

        threading.Thread(target=_do, daemon=True).start()

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
                    "(RCON TCP unreachable — check Windows Firewall for port 27015)"
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
                # Atomically confirm the server is still meant to be running and
                # claim the boot→ready transition, so a concurrent stop_server()
                # can't be undone here (it would have set running=False first).
                with self._lifecycle_lock:
                    became_ready = self.running and self.boot_state == "booting"
                    if became_ready:
                        self.boot_state    = "ready"
                        self._uptime_start = time.time()
                if became_ready:
                    self.log("Server ready — RCON is responding")
                    wk = self._pending_workshop_map
                    if wk:
                        # Switch to the workshop map now that RCON is live.
                        # brief pause lets the engine fully settle before changelevel.
                        self._pending_workshop_map = None
                        self.log(f"[workshop] Loading workshop map {wk}…")
                        time.sleep(1)
                        try:
                            self.rcon.execute_retry(f"host_workshop_map {wk}")
                            self.current_map = wk
                            self.log(f"[workshop] Workshop map {wk} loaded ✓")
                        except Exception as exc:
                            self.log(f"[workshop] Auto map switch failed: {exc}")
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
            mode_changed     = (mode != self.current_mode)
            new_managed      = _MODE_PLUGIN_NAMES.get(mode, [])
            old_managed      = _MODE_PLUGIN_NAMES.get(self.current_mode, [])
            plugins_involved = bool(new_managed or old_managed)

            # A mode change that adds/removes/swaps plugins is done as a clean
            # restart: deploy_plugins() then runs while the server is offline, so
            # no loaded CSS DLL can be file-locked (Windows), the old mode's
            # plugins can't reload alongside the new ones, and MetaMod modes load
            # at boot.  Same-mode map changes and vanilla↔vanilla switches (no
            # plugins on either side) stay live below.
            if mode_changed and plugins_involved and self.running:
                self.log(f"[{caller}] Mode change {self.current_mode} → {mode}: "
                         "restarting for a clean plugin swap…")
                self._restart_into(map_name, mode, is_workshop, caller)
                return

            # Offline + plugin mode change (not normally reachable via the web
            # route, which requires a running server): stage plugins so the next
            # start is correct.
            if mode_changed and plugins_involved:
                self.deploy_plugins(mode)

            s = MODE_SETTINGS.get(mode, _DEFAULT_MODE)
            try:
                self.log(f"[{caller}] Sending map change → {map_name} ({mode})…")
                self.rcon.execute_retry(f"game_type {s['game_type']}")
                self.rcon.execute_retry(f"game_mode {s['game_mode']}")
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

    def _read_appmanifest(self) -> tuple[str | None, str]:
        """Read appmanifest_730.acf and return (buildid, beta_key).

        buildid  — None if the file is absent or buildid is not present.
        beta_key — '' if on the public branch or the file is unreadable.

        Opens the file once so callers don't repeat the disk I/O.
        """
        manifest = os.path.join(
            _config.CS2_SERVER_DIR, "steamapps", f"appmanifest_{CS2_APP_ID}.acf"
        )
        try:
            with open(manifest, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return None, ""
        build_m = re.search(r'"buildid"\s+"(\d+)"', content)
        beta_m  = re.search(r'"BetaKey"\s+"([^"]*)"', content)
        build   = build_m.group(1).strip() if build_m else None
        beta    = beta_m.group(1).strip()  if beta_m  else ""
        return build, beta

    def check_update(self) -> None:
        def _do() -> None:
            build, beta_key = self._read_appmanifest()
            if not build:
                self.log("Update check: appmanifest not found — is the server installed?")
                if self.on_update_checked:
                    self.on_update_checked(False, "unknown", "unknown")
                return
            branch_label = f"beta:{beta_key}" if beta_key else "public"
            self.log(f"Update check: installed build = {build}  branch = {branch_label}")
            if beta_key:
                self.log(
                    f"  ⚠  Server is on beta branch '{beta_key}' — "
                    "this will cause 'client out of date' errors.  "
                    "Run Update to switch to the public branch."
                )
            try:
                # api.steamcmd.net mirrors steamcmd's own app-info cache and
                # returns the public-branch buildid in the same units as the
                # appmanifest.  The ISteamApps/UpToDateCheck endpoint returns
                # the *network protocol version* (e.g. 14163), which is a
                # completely different number and cannot be compared to buildids.
                req = urllib.request.Request(
                    "https://api.steamcmd.net/v1/info/730",
                    headers={"User-Agent": f"OblivionServerTool/{APP_VERSION}"},  # module-level constant
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                latest = str(
                    data.get("data", {})
                        .get("730", {})
                        .get("depots", {})
                        .get("branches", {})
                        .get("public", {})
                        .get("buildid", "")
                ).strip()
                if not latest:
                    self.log("Update check: buildid not found in API response")
                    if self.on_update_checked:
                        self.on_update_checked(False, build, "unknown")
                    return
                self.log(f"Update check: installed={build}  latest={latest}")
                if build == latest:
                    self.log("Update check: server is up to date")
                    self.update_available = False
                    if self.on_update_checked:
                        self.on_update_checked(False, build, latest)
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
        def _do() -> None:
            try:
                req = urllib.request.Request(
                    APP_API_URL,
                    headers={"User-Agent": f"OblivionServerTool/{APP_VERSION}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                tag = data.get("tag_name", "").strip().lstrip("v")
                url = data.get("html_url", APP_RELEASES_URL)
                if not tag:
                    return
                available = _semver_tuple(tag) > _semver_tuple(APP_VERSION)
                self.log(
                    f"App update check: current=v{APP_VERSION}  "
                    f"latest=v{tag}  "
                    f"{'UPDATE AVAILABLE' if available else 'up to date'}"
                )
                self.app_update_available = available
                self.app_latest_version   = tag
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
                    # urlopen(timeout) instead of urlretrieve so a stalled CDN can't
                    # hang the install thread indefinitely.
                    req = urllib.request.Request(
                        url, headers={"User-Agent": f"OblivionServerTool/{APP_VERSION}"})
                    with urllib.request.urlopen(req, timeout=60) as resp, \
                            open(zip_path, "wb") as fh:
                        shutil.copyfileobj(resp, fh)
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

    # ── appmanifest branch helper ─────────────────────────────────────────────

    def _fix_appmanifest_branch(self) -> str:
        """Read appmanifest_<appid>.acf, report and clear any non-public BetaKey.

        steamcmd respects the BetaKey stored in the appmanifest even when no
        -beta flag is passed on the command line.  If the manifest was ever
        written with a beta key (by a prior manual steamcmd session, a Valve
        default, or any other tool), every subsequent update silently stays on
        that beta branch.  Clearing it here before steamcmd runs guarantees we
        get the public release.

        Returns a human-readable status string for the log.
        """
        manifest = os.path.join(
            _config.CS2_SERVER_DIR, "steamapps",
            f"appmanifest_{CS2_APP_ID}.acf",
        )
        if not os.path.exists(manifest):
            return "appmanifest not found — will be created on install"
        try:
            with open(manifest, encoding="utf-8") as f:
                content = f.read()
            m = re.search(r'"BetaKey"\s+"([^"]*)"', content)
            beta_key = m.group(1).strip() if m else ""
            if beta_key:
                # Clear the value in-place so steamcmd sees an empty branch key
                content = re.sub(
                    r'("BetaKey"\s+")[^"]*(")',
                    r"\1\2",
                    content,
                )
                with open(manifest, "w", encoding="utf-8") as f:
                    f.write(content)
                return (
                    f"⚠  BetaKey was '{beta_key}' — cleared. "
                    "steamcmd will now pull the public branch."
                )
            return f"BetaKey is empty — already targeting public branch"
        except Exception as exc:
            return f"appmanifest read/write failed: {exc}"

    def run_update(self, on_done: Callable | None = None) -> None:
        if self.running:
            self.log("Stop the server before updating.")
            if on_done:
                on_done()
            return

        def _do() -> None:
            self.log("─" * 48)
            self.log("  CS2 SERVER UPDATE")
            self.log(f"  steamcmd    → {_config.STEAMCMD_PATH}")
            self.log(f"  install dir → {_config.CS2_SERVER_DIR}")
            self.log("─" * 48)

            # ── Step 0: guarantee the public branch ──────────────────────────
            # steamcmd honours the BetaKey inside appmanifest_730.acf even if
            # no -beta flag is given on the command line.  Patch it first so
            # the update always installs from the public (stable) channel.
            branch_status = self._fix_appmanifest_branch()
            self.log(f"  Branch check: {branch_status}")

            self.log("Launching steamcmd…")
            self.log("  Phase 1 — steamcmd initialises itself  (10–30 s, no output)")
            self.log("  Phase 2 — login anonymous")
            self.log("  Phase 3 — download changed files  (progress below)")
            try:
                # NO +force_install_dir.  steamcmd's default library is its own
                # directory (CS2_SERVER_DIR), where the manifest-tracked install
                # already lives (steamapps/common/Counter-Strike Global Offensive/,
                # the path CS2_PATH points to) — so it updates that install IN PLACE.
                #
                # Passing "+force_install_dir <CS2_SERVER_DIR>" was WRONG: CS2's
                # content root is a top-level "game/" folder, so steamcmd unpacked it
                # directly into the server dir as "<CS2_SERVER_DIR>/game/" — a full
                # ~64 GB DUPLICATE install separate from the one the server runs.
                # Every update grew that orphan and never touched the real files.
                proc = subprocess.Popen(
                    [_config.STEAMCMD_PATH,
                     "+login", "anonymous",
                     "+app_update", CS2_APP_ID, "validate", "+quit"],
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
                                self.log(f"  … still working ({silence}s, no steamcmd output)")
                        continue
                    if line is None:
                        break
                    last_output = time.time()
                    warned_at.clear()   # re-arm the silence warnings for the next gap
                    s = line.strip()
                    if s:
                        self.log(f"[steamcmd] {s}")
                proc.wait()
                self.log("─" * 48)
                if proc.returncode == 0:
                    self.log("  UPDATE COMPLETE — verifying build…")
                    self.update_available = False   # optimistic clear (steamcmd reported OK)
                    # Re-read the now-updated appmanifest and compare to the latest
                    # public buildid: confirms the update actually landed and corrects
                    # the badge if somehow still behind. Also clears the badge live.
                    self.check_update()
                elif proc.returncode == 8:
                    self.log("  Exit code 8 — update failed (run steamcmd manually once to self-update)")
                else:
                    self.log(f"  steamcmd exited with code {proc.returncode}")
                self.log("─" * 48)
            except FileNotFoundError:
                self.log(f"steamcmd not found: {_config.STEAMCMD_PATH}")
            except Exception as exc:
                self.log(f"Update error: {exc}")
            finally:
                if on_done:
                    on_done()

        threading.Thread(target=_do, daemon=True).start()

    # ── workshop update check ─────────────────────────────────────────────────

    def check_workshop_updates(self) -> None:
        def _do() -> None:
            wdir = _config.WORKSHOP_DIR
            if not os.path.exists(wdir):
                self.log("Workshop update check: directory not found")
                return
            ids = [f for f in os.listdir(wdir)
                   if os.path.isdir(os.path.join(wdir, f)) and f.isdigit()]
            if not ids:
                self.log("Workshop update check: no maps downloaded")
                return
            self.log(f"Workshop update check: checking {len(ids)} map(s)…")
            try:
                params: dict[str, str | int] = {"itemcount": len(ids)}
                for i, wid in enumerate(ids):
                    params[f"publishedfileids[{i}]"] = wid
                req = urllib.request.Request(
                    "https://api.steampowered.com"
                    "/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                    data=urllib.parse.urlencode(params).encode(),
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
                    local_ts = (int(os.path.getmtime(os.path.join(wdir, wid)))
                                if os.path.exists(os.path.join(wdir, wid)) else 0)
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

    # ── plugin deployment ─────────────────────────────────────────────────────

    def _csgo_dir(self) -> str:
        """Return the game/csgo/ directory (parent of addons/)."""
        return os.path.dirname(_config.CS2_ADDONS_DIR)

    # ── Plugin infrastructure helpers ─────────────────────────────────────────
    # MetaMod must be referenced in csgo/gameinfo.gi for the engine to load it.
    # Without this patch, MetaMod (and therefore CS2Fixes + CounterStrikeSharp,
    # which are MetaMod plugins) silently never loads, even with files in place.

    def _gameinfo_path(self) -> str:
        return os.path.join(self._csgo_dir(), "gameinfo.gi")

    def _gameinfo_has_metamod(self) -> bool | None:
        """True if patched, False if not, None if file missing/unreadable."""
        try:
            with open(self._gameinfo_path(), encoding="utf-8",
                      errors="replace") as f:
                content = f.read()
        except FileNotFoundError:
            return None
        except Exception:
            return None
        return "csgo/addons/metamod" in content

    def _unpatch_gameinfo(self) -> bool:
        """Remove the MetaMod search path from gameinfo.gi.

        Prefers restoring the clean Valve backup saved on first patch.
        Falls back to manually stripping the MetaMod line if no backup exists.
        Idempotent — safe to call when MetaMod is already absent.
        """
        path   = self._gameinfo_path()
        backup = path + ".oblivion.bak"

        # Fast path: restore clean original from backup
        if os.path.isfile(backup):
            try:
                shutil.copy2(backup, path)
                self.log("[gameinfo] ✓ Restored original gameinfo.gi "
                         "(MetaMod entry removed — vanilla mode)")
                return True
            except Exception as exc:
                self.log(f"[gameinfo] Backup restore failed: {exc} "
                         "— attempting manual unpatch")

        # Fallback: strip the MetaMod line in-place
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as exc:
            self.log(f"[gameinfo] Read failed during unpatch: {exc}")
            return False

        new_lines = [l for l in lines if "csgo/addons/metamod" not in l]
        if len(new_lines) == len(lines):
            return True  # already clean

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            self.log("[gameinfo] ✓ Removed MetaMod search path from gameinfo.gi")
            return True
        except Exception as exc:
            self.log(f"[gameinfo] Unpatch write failed: {exc}")
            return False

    def _patch_gameinfo(self) -> bool:
        """Insert the MetaMod search path into gameinfo.gi. Idempotent.

        Inserts a new `Game\\tcsgo/addons/metamod` line just before the first
        `Game\\tcsgo` entry inside the SearchPaths block.  Backs up the
        original to gameinfo.gi.oblivion.bak on first patch.
        """
        path = self._gameinfo_path()
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            self.log(f"[gameinfo] Not found at {path} — cannot patch")
            return False
        except Exception as exc:
            self.log(f"[gameinfo] Read failed: {exc}")
            return False

        if any("csgo/addons/metamod" in l for l in lines):
            return True

        new_lines: list[str] = []
        inserted = False
        for line in lines:
            if not inserted and re.match(r"^\s+Game\s+csgo\s*$", line):
                indent = line[:len(line) - len(line.lstrip())]
                new_lines.append(f"{indent}Game\tcsgo/addons/metamod\n")
                inserted = True
            new_lines.append(line)

        if not inserted:
            self.log("[gameinfo] Could not locate the SearchPaths 'Game csgo' "
                     "entry — manual patch required")
            return False

        try:
            backup = path + ".oblivion.bak"
            if not os.path.exists(backup):
                shutil.copy2(path, backup)
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            self.log(f"[gameinfo] ✓ Patched — MetaMod search path added "
                     f"(backup: {os.path.basename(backup)})")
            return True
        except Exception as exc:
            self.log(f"[gameinfo] Write failed: {exc}")
            return False

    def _metamod_installed(self) -> bool:
        """MetaMod base = the addons/metamod folder exists with real content."""
        mm = os.path.join(self._csgo_dir(), "addons", "metamod")
        if not os.path.isdir(mm):
            return False
        # Distinguish a real install (binaries / loader files) from a folder
        # that only holds plugin-registration VDFs.
        for entry in os.listdir(mm):
            full = os.path.join(mm, entry)
            if os.path.isdir(full):                    # bin/, etc.
                return True
            if entry.lower() == "metaplugins.ini":      # loader manifest
                return True
        return False

    def _css_installed(self) -> bool:
        """CounterStrikeSharp base needs both api/ and bin/ subdirectories."""
        css = os.path.join(self._csgo_dir(), "addons", "counterstrikesharp")
        return (os.path.isdir(os.path.join(css, "api")) and
                os.path.isdir(os.path.join(css, "bin")))

    def _verify_plugin_files(self, name: str) -> list[str]:
        """Return relative paths of expected output files that are missing."""
        csgo = self._csgo_dir()
        expected = _PLUGIN_VERIFY_FILES.get(name, [])
        # K4-Arenas-Bots is only deployed when the host enables bots; don't flag it
        # as missing when bots are off (it's intentionally excluded then).
        if name == "arenas" and not self.bots_enabled:
            expected = [r for r in expected if "K4-Arenas-Bots" not in r]
        return [rel for rel in expected
                if not os.path.exists(os.path.join(csgo, rel))]

    def _verify_deployment(self, new_plugins: list[str]) -> bool:
        """Run post-deploy diagnostics; log results; return True if all OK."""
        if not new_plugins:
            return True

        self.log("[plugins] ── Verifying deployment ──")
        all_ok = True

        for name in new_plugins:
            missing = self._verify_plugin_files(name)
            kind = _PLUGIN_KIND.get(name, "?").upper()
            if missing:
                all_ok = False
                self.log(f"[plugins]   ✗ {name} [{kind}]: missing expected file(s):")
                for m in missing:
                    self.log(f"[plugins]      - {m}")
            else:
                self.log(f"[plugins]   ✓ {name} [{kind}]: all expected files present")

        kinds = {_PLUGIN_KIND.get(p) for p in new_plugins}
        if "metamod" in kinds or "css" in kinds:
            if self._metamod_installed():
                self.log("[plugins]   ✓ MetaMod base is installed")
            else:
                all_ok = False
                self.log("[plugins]   ✗ MetaMod base is NOT installed — "
                         "plugins will NOT load")
                self.log("[plugins]      → Get it from "
                         "https://www.sourcemm.net/downloads.php?branch=master")
                self.log("[plugins]      → Extract into csgo/ "
                         "(creates addons/metamod/)")
        if "css" in kinds:
            if self._css_installed():
                self.log("[plugins]   ✓ CounterStrikeSharp base is installed")
            else:
                all_ok = False
                self.log("[plugins]   ✗ CounterStrikeSharp base is NOT installed "
                         "— CSS plugins will NOT load")
                self.log("[plugins]      → Get it from "
                         "https://github.com/roflmuffin/CounterStrikeSharp/releases")

        gi = self._gameinfo_has_metamod()
        if gi is True:
            self.log("[plugins]   ✓ gameinfo.gi includes MetaMod search path")
        elif gi is False:
            all_ok = False
            self.log("[plugins]   ✗ gameinfo.gi is NOT patched — MetaMod won't load")
        else:
            all_ok = False
            self.log(f"[plugins]   ✗ gameinfo.gi unreadable at {self._gameinfo_path()}")

        if all_ok:
            self.log("[plugins] ✓✓ All checks passed — plugins should be functional in-game")
        else:
            self.log("[plugins] ⚠  Verification found issues (see ✗ lines above)")
        return all_ok

    @property
    def _ban_file(self) -> str:
        """Absolute path to csgo/cfg/banned_user.cfg."""
        return os.path.join(self._csgo_dir(), "cfg", "banned_user.cfg")

    def _read_ban_lines(self) -> list[str]:
        """Read banned_user.cfg and return its non-blank lines. Returns [] if absent."""
        try:
            with open(self._ban_file, encoding="utf-8", errors="replace") as f:
                return [l.rstrip("\n") for l in f if l.strip()]
        except FileNotFoundError:
            return []

    def _write_ban_lines(self, lines: list[str]) -> None:
        """Write lines back to banned_user.cfg, creating the directory if needed."""
        os.makedirs(os.path.dirname(self._ban_file), exist_ok=True)
        with open(self._ban_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))

    def _plugin_manifest_path(self) -> str:
        return os.path.join(os.path.dirname(_CONFIG_FILE), "oblivion_plugins.json")

    def _load_plugin_manifest(self) -> dict:
        try:
            with open(self._plugin_manifest_path(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_plugin_manifest(self, mode: str, plugins: list[str]) -> None:
        try:
            with open(self._plugin_manifest_path(), "w", encoding="utf-8") as f:
                json.dump({
                    "mode":        mode,
                    "plugins":     plugins,
                    "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, f, indent=2)
        except Exception as exc:
            self.log(f"[plugins] Could not save manifest: {exc}")

    def _undeploy_plugins(self, plugin_names: list[str], csgo_dir: str) -> None:
        """Remove previously deployed plugin files from csgo_dir."""
        for name in plugin_names:
            for item in _PLUGIN_CLEANUP_ITEMS.get(name, []):
                full = os.path.join(csgo_dir, item)
                if os.path.isdir(full):
                    try:
                        shutil.rmtree(full)
                        self.log(f"[plugins] Removed dir: {item}")
                    except Exception as exc:
                        self.log(f"[plugins] Could not remove {item}: {exc}")
                elif os.path.isfile(full):
                    try:
                        os.remove(full)
                        self.log(f"[plugins] Removed file: {item}")
                    except Exception as exc:
                        self.log(f"[plugins] Could not remove {item}: {exc}")

    def _apply_retakes_bots(self, csgo_dir: str) -> None:
        """Honour the Use-bots toggle for Retakes by editing the deployed retakes.cfg.

        The bundled cfg defaults to bot auto-fill (bots ON); each deploy copies that
        fresh, so we only rewrite it when bots are OFF: bot_quota 0 + bot_kick = no
        fill. (Deathmatch has no plugin bot-fill, so the toggle doesn't apply there.)
        """
        if self.bots_enabled:
            return  # bundled default already = fill
        cfg = os.path.join(csgo_dir, "cfg", "cs2-retakes", "retakes.cfg")
        if not os.path.isfile(cfg):
            return
        try:
            txt = open(cfg, encoding="utf-8").read()
            txt = re.sub(r"(?m)^bot_quota\s+\S+", "bot_quota               0", txt)
            txt = re.sub(r"(?m)^bot_quota_mode\s+\S+", "bot_quota_mode          normal", txt)
            if not re.search(r"(?m)^bot_kick\b", txt):
                txt = re.sub(r"(?m)^(bot_quota_mode.*)$", r"bot_kick\n\1", txt, count=1)
            open(cfg, "w", encoding="utf-8", newline="\n").write(txt)
            self.log("[plugins]   retakes: bots OFF — bot_quota 0 + bot_kick")
        except Exception as exc:
            self.log(f"[plugins]   retakes: could not apply bots toggle: {exc}")

    def deploy_plugins(self, mode: str) -> bool:
        """Deploy bundled plugins for *mode* into the CS2 server's csgo/ directory.

        1. Removes plugins from the previous mode (reads the manifest).
        2. Copies the new mode's plugin files in.
        3. Saves an updated manifest so the next switch can clean up correctly.

        Returns True on success, False if the csgo/ directory doesn't exist yet.
        Always runs synchronously so the server is not launched before files land.
        """
        csgo_dir = self._csgo_dir()
        if not os.path.isdir(csgo_dir):
            self.log(f"[plugins] csgo/ not found: {csgo_dir}")
            self.log("[plugins] Is the server installed? Use Config → Install CS2 Server.")
            return False

        new_plugins = _MODE_PLUGIN_NAMES.get(mode, [])

        # Read the manifest BEFORE we overwrite it — used later to compute
        # which plugin kinds were genuinely active in the previous session.
        prev_manifest = self._load_plugin_manifest()

        # ── 1. Remove every managed plugin not required for this mode ────────
        # Intentionally manifest-independent: we always clean every slot we
        # own that isn't needed, regardless of what any previous session recorded.
        # This prevents leftover plugins from stale manifests or dirty states
        # loading alongside the new mode's plugins inside CounterStrikeSharp.
        unneeded = sorted(set(_PLUGIN_CLEANUP_ITEMS) - set(new_plugins))
        if unneeded:
            self.log(f"[plugins] Clearing unneeded: {', '.join(unneeded)}")
            self._undeploy_plugins(unneeded, csgo_dir)

        # ── 2. Deploy new plugins ─────────────────────────────────────────────
        if not new_plugins:
            self.log(f"[plugins] No managed plugins for {mode} — vanilla CS2")
            self._save_plugin_manifest(mode, [])
            # Remove MetaMod from gameinfo.gi so CSS never loads on a vanilla
            # server (an outdated CSS throws a CLR exception that crashes cs2.exe).
            if self._gameinfo_has_metamod() is True:
                self.log("[plugins] Switching to vanilla — removing MetaMod from gameinfo.gi…")
                self._unpatch_gameinfo()
            return True

        self.log(f"[plugins] Deploying for {mode}: {', '.join(new_plugins)}")
        self.log(f"[plugins] Source root: {_PLUGINS_BASE}")
        per_plugin_count: dict[str, int] = {n: 0 for n in new_plugins}
        per_plugin_dirs:  dict[str, list[str]] = {n: [] for n in new_plugins}
        any_failed = False
        for name in new_plugins:
            src_base = os.path.join(_PLUGINS_BASE, name)
            if not os.path.isdir(src_base):
                self.log(f"[plugins]   ✗ {name}: source folder missing — SKIPPED")
                self.log(f"[plugins]      Expected at: {src_base}")
                if os.path.isdir(_PLUGINS_BASE):
                    siblings = sorted(os.listdir(_PLUGINS_BASE))
                    self.log(f"[plugins]      Source root contents: "
                             f"{', '.join(siblings) if siblings else '(empty)'}")
                else:
                    self.log(f"[plugins]      Source root does not exist: {_PLUGINS_BASE}")
                    self.log("[plugins]      → If running as packaged .exe, the plugins/ "
                             "folder must be alongside the exe (or bundled via PyInstaller)")
                any_failed = True
                continue
            # Bots toggle: when the host has bots disabled, skip the K4-Arenas-Bots
            # plugin folder so the Arena ladder runs humans-only.
            excluded_dirs: set[str] = set()
            if name == "arenas" and not self.bots_enabled:
                excluded_dirs.add("K4-Arenas-Bots")
                self.log("[plugins]   arenas: bots OFF — excluding K4-Arenas-Bots")
            for rule in _PLUGIN_COPY_RULES.get(name, []):
                src_sub, dst_sub = rule[0], rule[1]
                # Optional third element: frozenset of immediate subdir names to exclude.
                exclude_subdirs: frozenset[str] = rule[2] if len(rule) > 2 else frozenset()
                src = os.path.join(src_base, src_sub)
                dst = os.path.join(csgo_dir, dst_sub)
                if not os.path.exists(src):
                    self.log(f"[plugins]   ✗ {name}: missing source piece {src_sub or '(root)'}")
                    any_failed = True
                    continue
                os.makedirs(dst, exist_ok=True)
                skipped_host = 0
                for root, dirs, files in os.walk(src):
                    # Prune excluded subdirectories (only at the top level of src).
                    if exclude_subdirs and os.path.normpath(root) == os.path.normpath(src):
                        dirs[:] = [d for d in dirs if d not in exclude_subdirs]
                    # Prune excluded plugin folders anywhere in the tree (bots toggle).
                    if excluded_dirs:
                        dirs[:] = [d for d in dirs if d not in excluded_dirs]
                    rel     = os.path.relpath(root, src)
                    tgt_dir = os.path.join(dst, rel) if rel != "." else dst
                    os.makedirs(tgt_dir, exist_ok=True)
                    for fname in files:
                        # Skip CSS-host-provided DLLs — they are already loaded
                        # by the CSS host process and must not be duplicated inside
                        # a plugin's AssemblyLoadContext.
                        if fname.lower() in _CSS_HOST_DLLS:
                            skipped_host += 1
                            continue
                        shutil.copy2(os.path.join(root, fname),
                                     os.path.join(tgt_dir, fname))
                        per_plugin_count[name] += 1
                if skipped_host:
                    self.log(f"[plugins]   {name}: skipped {skipped_host} CSS-host DLL(s)")
                per_plugin_dirs[name].append(dst)

            # ── Per-plugin verification: confirm files actually landed ────────
            n = per_plugin_count[name]
            kind = _PLUGIN_KIND.get(name, "?").upper()
            if n > 0:
                self.log(f"[plugins]   ✓ {name} [{kind}]: {n} file(s) → "
                         f"{', '.join(os.path.relpath(d, csgo_dir) for d in per_plugin_dirs[name])}")
            else:
                self.log(f"[plugins]   ✗ {name} [{kind}]: NO files copied — check source folder")
                any_failed = True

        # Bots OFF: scrub any K4-Arenas-Bots left on disk by a previous bots-on
        # deploy (the copy above skips it, but doesn't delete an existing copy).
        if "arenas" in new_plugins and not self.bots_enabled:
            shutil.rmtree(os.path.join(csgo_dir, "addons", "counterstrikesharp",
                                       "plugins", "K4-Arenas-Bots"), ignore_errors=True)

        # Retakes follows the same Use-bots toggle (bot_quota in its cfg).
        if "retakes_b3none" in new_plugins:
            self._apply_retakes_bots(csgo_dir)

        total = sum(per_plugin_count.values())
        if any_failed:
            self.log(f"[plugins] Copy phase finished with WARNINGS — {total} file(s) copied")
        else:
            self.log(f"[plugins] Copy phase complete — {total} file(s) → {csgo_dir}")
        # Manifest records only plugins that actually got files on disk — so the
        # next mode switch's cleanup matches reality and the diagnostic doesn't
        # lie about "deployed" plugins that were never copied.
        actually_deployed = [p for p in new_plugins if per_plugin_count[p] > 0]
        self._save_plugin_manifest(mode, actually_deployed)

        # ── 3. Auto-patch gameinfo.gi so MetaMod actually loads ───────────────
        # Only patch if at least one MetaMod/CSS plugin was actually deployed.
        # Patching when no plugins landed would cause MetaMod (if installed) to
        # load CSS on the next server start, which can crash the server with a
        # CLR exception if CSS is absent or out of date.
        actually_needs_metamod = any(
            _PLUGIN_KIND.get(p) in ("metamod", "css")
            for p in actually_deployed
        )
        if actually_needs_metamod and self._gameinfo_has_metamod() is False:
            self.log("[plugins] gameinfo.gi missing MetaMod search path — patching…")
            self._patch_gameinfo()

        # ── 4. Verify deployment actually produced a working install ─────────
        verified_ok = self._verify_deployment(new_plugins)

        # ── 5. Tell the user what to do next ─────────────────────────────────
        # Derive "what was actually running before" from the saved manifest,
        # NOT from the full unneeded list.  The unneeded list includes every
        # plugin that exists in _PLUGIN_CLEANUP_ITEMS but isn't needed for the
        # new mode — even plugins that were never deployed (e.g. zombie when
        # switching Retakes→Warcraft).  Using unneeded caused needs_metamod_restart
        # to fire falsely for CSS→CSS mode switches (zombie kind=metamod appearing
        # in unneeded), which suppressed the hot-reload and left the server vanilla.
        prev_deployed = set(prev_manifest.get("plugins", []))
        old_kinds = {_PLUGIN_KIND.get(p) for p in prev_deployed if p not in set(new_plugins)}
        new_kinds = {_PLUGIN_KIND.get(p) for p in new_plugins}
        needs_metamod_restart = "metamod" in (old_kinds | new_kinds)
        has_css_changes       = "css" in (old_kinds | new_kinds)

        if not verified_ok:
            self.log("[plugins] ⚠  Fix the issues above before relying on plugin features.")
        elif not self.running:
            self.log("[plugins] Plugins will activate when you start the server.")
        elif needs_metamod_restart:
            self.log("[plugins] ⚠  RESTART REQUIRED: MetaMod plugin only loads at server boot.")
        elif has_css_changes:
            self.log("[plugins] CSS plugins changed — hot-reloading via RCON…")
            self._hot_reload_css()
        return not any_failed and verified_ok

    def _hot_reload_css(self) -> None:
        """Tell a running CS2 server to reload its CounterStrikeSharp plugins.

        CSS exposes `css_plugins reload` which re-scans
        addons/counterstrikesharp/plugins/ and reloads everything without a
        server restart.  Inspects the response: an "unknown command" reply means
        CSS itself isn't loaded yet and a restart is needed instead.
        """
        if not self.running:
            return
        try:
            resp = self.rcon.execute("css_plugins reload")
            shown = (resp.strip() or "(no output)")[:300]
            self.log(f"[plugins] css_plugins reload → {shown}")
            if "unknown command" in resp.lower() or "unknown cmd" in resp.lower():
                self.log("[plugins] ⚠  css_plugins not recognised — "
                         "CounterStrikeSharp may not be loaded yet.")
                self.log("[plugins]    Restart the server to activate plugin changes.")
            else:
                self.log("[plugins] ✓ CounterStrikeSharp plugins hot-reloaded — "
                         "no restart needed")
        except Exception as exc:
            self.log(f"[plugins] Hot-reload failed ({exc}). Restart the server "
                     "to activate plugin changes.")

    def deploy_plugins_async(self, mode: str,
                              on_done: Callable[[bool], None] | None = None) -> None:
        """Non-blocking wrapper around deploy_plugins() for GUI use."""
        def _do() -> None:
            ok = self.deploy_plugins(mode)
            if on_done:
                on_done(ok)
        threading.Thread(target=_do, daemon=True).start()

    # ── plugin checker ────────────────────────────────────────────────────────

    def check_plugins(self) -> None:
        """Full diagnostic: infrastructure, gameinfo patch, and per-plugin files."""
        def _do() -> None:
            self.log("─── Plugin install diagnostic ───")
            csgo = self._csgo_dir()
            self.log(f"csgo/ = {csgo}")

            # Infrastructure
            self.log("MetaMod base:           "
                     + ("✓ installed" if self._metamod_installed()
                        else "✗ NOT installed (download from sourcemm.net)"))
            self.log("CounterStrikeSharp:     "
                     + ("✓ installed" if self._css_installed()
                        else "✗ NOT installed (github roflmuffin/CounterStrikeSharp)"))
            gi = self._gameinfo_has_metamod()
            self.log("gameinfo.gi MetaMod:    "
                     + ("✓ patched" if gi is True
                        else "✗ NOT patched (run a plugin deploy to auto-fix)" if gi is False
                        else "⚠  file unreadable"))

            # What's currently installed in the plugins folder
            plugins_dir = os.path.join(csgo, "addons", "counterstrikesharp", "plugins")
            if os.path.isdir(plugins_dir):
                installed = sorted(
                    p for p in os.listdir(plugins_dir)
                    if os.path.isdir(os.path.join(plugins_dir, p))
                )
                self.log(f"CSS plugins on disk:    {len(installed)}"
                         + (f" — {', '.join(installed)}" if installed else " (none)"))

            # Plugin source root (where deploy reads from)
            src_root_ok = os.path.isdir(_PLUGINS_BASE)
            self.log(f"Plugin source root:     {_PLUGINS_BASE}")
            self.log(f"Source root exists:     "
                     + ("✓ yes" if src_root_ok
                        else "✗ NO — re-deploy will not find any plugin files"))

            # Per-managed-plugin: file presence
            manifest      = self._load_plugin_manifest()
            current_mode  = manifest.get("mode", "(none)")
            current_names = manifest.get("plugins", [])
            deployed_at   = manifest.get("deployed_at", "")
            ts_note = f"  (deployed {deployed_at})" if deployed_at else ""
            self.log(f"Last deployed mode:     {current_mode}{ts_note}"
                     + (f" → {', '.join(current_names)}" if current_names else " (vanilla)"))
            for name in current_names:
                src_dir = os.path.join(_PLUGINS_BASE, name)
                src_ok  = os.path.isdir(src_dir)
                missing = self._verify_plugin_files(name)
                kind = _PLUGIN_KIND.get(name, "?").upper()
                src_note = " (source: ✓)" if src_ok else f" (source: ✗ NOT FOUND — redeploy won't fix this)"
                if missing:
                    self.log(f"  ✗ {name} [{kind}]: missing {len(missing)} file(s){src_note}")
                    for m in missing:
                        self.log(f"      - {m}")
                    if src_ok:
                        self.log(f"      → Click 'Deploy Plugins for Current Mode' to fix")
                else:
                    self.log(f"  ✓ {name} [{kind}]: all expected files present")

            # Latest CSS release info
            try:
                req = urllib.request.Request(
                    "https://api.github.com/repos/"
                    "roflmuffin/CounterStrikeSharp/releases/latest",
                    headers={"User-Agent": f"OblivionServerTool/{APP_VERSION}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    rel = json.loads(resp.read())
                self.log(f"CSS latest release:     {rel.get('tag_name', '?')} "
                         f"({rel.get('published_at', '')[:10]})")
            except Exception as exc:
                self.log(f"CSS latest release:     check failed ({exc})")

            self.log("─── End of diagnostic ───")
        threading.Thread(target=_do, daemon=True).start()

    # ── workshop download ─────────────────────────────────────────────────────

    def request_workshop_download(self, workshop_id: str,
                                   requester: str = "remote") -> None:
        with self._dl_lock:
            if any(r["id"] == workshop_id for r in self._dl_reqs):
                self.log(f"Download already pending: {workshop_id}")
                return
            self._dl_reqs.append({"id": workshop_id, "requester": requester})
        self.log(f"[{requester}] Requested download of workshop map {workshop_id}"
                 " — approve via the web panel Workshop tab")

    def cancel_download(self) -> None:
        """Kill the currently running steamcmd download process, if any."""
        proc = self._active_dl_proc
        if proc is None:
            self.log("No download in progress")
            return
        self.log("Cancelling download — terminating steamcmd…")
        # Clear progress now so a state poll can't briefly re-show the bar
        # before the worker thread's finally block clears it.
        self._dl_progress = {}
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
            args = [_config.STEAMCMD_PATH,
                    "+login", self.steam_username, self.steam_password,
                    "+quit"]
            if sys.platform == "win32":
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
        if os.path.isfile(_config.DEPOTDL_PATH):
            return True
        self.log("DepotDownloader not found — downloading from GitHub…")
        try:
            dest_dir = os.path.dirname(_config.DEPOTDL_PATH)
            os.makedirs(dest_dir, exist_ok=True)
            req = urllib.request.Request(
                DEPOTDL_RELEASE_URL,
                headers={"User-Agent": f"OblivionServerTool/{APP_VERSION}"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                release = json.loads(r.read())
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
            if os.path.isfile(_config.DEPOTDL_PATH):
                self.log("  DepotDownloader installed ✓")
                return True
            self.log(f"  ✗ Extracted but {_config.DEPOTDL_PATH} not found.")
            return False
        except Exception as exc:
            self.log(f"  DepotDownloader install failed: {exc}")
            return False

    @staticmethod
    def _dir_size(path: str) -> int:
        """Total size in bytes of every file under *path* (0 if missing)."""
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    def _fetch_workshop_size(self, workshop_id: str) -> int:
        """Steam's reported file_size (bytes) for a workshop item, 0 on failure.

        Used as the denominator for the download progress bar and as the
        expected size for the post-download verification.
        """
        try:
            params = {"itemcount": 1, "publishedfileids[0]": workshop_id}
            req = urllib.request.Request(
                "https://api.steampowered.com"
                "/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                data=urllib.parse.urlencode(params).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            details = data.get("response", {}).get("publishedfiledetails", [])
            if details:
                return int(details[0].get("file_size", 0) or 0)
        except Exception as exc:
            self.log(f"  Could not fetch expected size (progress % unavailable): {exc}")
        return 0

    def depotdl_download(self, workshop_id: str,
                         on_done: Callable[[bool], None] | None = None) -> None:
        """Download a workshop item via DepotDownloader (more reliable than steamcmd).

        Downloads into a sibling ``<id>.partial`` staging folder, streams real
        per-MB progress (vs Steam's reported size) into ``self._dl_progress``,
        verifies the finished size before promoting the staging folder to the
        live ``<id>`` workshop dir.  A failed/cancelled download therefore never
        leaves a half-written map in the maps list.
        """
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

            # Download into a staging folder, not the live <id> dir — so a
            # failed/cancelled/partial download never appears as a usable map.
            dest      = os.path.join(_config.WORKSHOP_DIR, workshop_id)
            dest_tmp  = dest + ".partial"
            if os.path.isdir(dest_tmp):
                shutil.rmtree(dest_tmp, ignore_errors=True)
            os.makedirs(dest_tmp, exist_ok=True)

            # Expected size (bytes) drives the progress %% and the final verify.
            expected = self._fetch_workshop_size(workshop_id)
            if expected > 0:
                self.log(f"  Expected size: {expected / 1048576:.1f} MB")
            self._dl_progress = {"id": workshop_id, "downloaded": 0,
                                 "total": expected, "pct": 0.0,
                                 "phase": "downloading"}

            session_ok = self.steam_session_active and bool(self.steam_username)

            # Always pass the full credentials so DepotDownloader can
            # re-authenticate when its own cached session token has expired.
            # Passing username-only (token-only) caused "first download always
            # fails": the silent path produced no files → session invalidated →
            # second attempt re-added the password → worked.  Alternated every
            # single time.  With credentials present DepotDownloader refreshes
            # the token itself; the console is only still opened on the first
            # run (session_ok=False) in case Steam Guard needs a 2FA code.
            login_args = [
                "-username",          self.steam_username,
                "-password",          self.steam_password,
                "-remember-password",
            ]
            if session_ok:
                self.log("  Using cached session (with credential fallback)…")
            else:
                self.log("  Logging in and saving session token for future downloads.")

            cmd = [
                _config.DEPOTDL_PATH,
                "-app",     CS2_APP_ID,
                "-pubfile", workshop_id,
                "-dir",     dest_tmp,
            ] + login_args
            try:
                # ── Launch ────────────────────────────────────────────────────
                # Cached-session path runs hidden so we can stream stdout/stderr
                # into our log — otherwise the new console flashes & closes and
                # the user never sees DepotDownloader's error.
                # First-time path needs a real console for 2FA / Steam Guard input.
                if session_ok:
                    self.log("  Launching DepotDownloader (silent — output captured below)…")
                    flags = (subprocess.CREATE_NO_WINDOW
                             if sys.platform == "win32" else 0)
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        creationflags=flags,
                    )
                else:
                    self.log("  Launching DepotDownloader — "
                             "enter 2FA/Guard code in the console if prompted (first time only).")
                    if sys.platform == "win32":
                        proc = subprocess.Popen(
                            cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    else:
                        proc = subprocess.Popen(
                            ["x-terminal-emulator", "-e"] + cmd)

                self._active_dl_proc = proc

                # ── Drain captured output (silent path only) ─────────────────
                # On a background thread so the main wait-loop below can still
                # enforce TIMEOUT.
                if session_ok and proc.stdout is not None:
                    def _drain(pipe) -> None:
                        for raw in pipe:
                            line = raw.rstrip()
                            if line:
                                self.log(f"  [dd] {line}")
                    threading.Thread(target=_drain, args=(proc.stdout,),
                                     daemon=True).start()

                # ── Wait, with real per-MB progress and a hard timeout ───────
                start       = time.time()
                last_logged = -1.0   # last MB value we emitted a log line for
                while proc.poll() is None:
                    time.sleep(2)
                    elapsed = int(time.time() - start)
                    if elapsed >= _DL_TIMEOUT_SECS:
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
                    done_b = self._dir_size(dest_tmp)
                    pct    = (done_b / expected * 100.0) if expected > 0 else 0.0
                    self._dl_progress = {"id": workshop_id, "downloaded": done_b,
                                         "total": expected, "pct": round(pct, 1),
                                         "phase": "downloading"}
                    done_mb = done_b / 1048576
                    # Log on every ~10 MB of progress so the text log isn't spammy
                    # but still moves; the UI bar updates every poll regardless.
                    if done_mb - last_logged >= 10 or last_logged < 0:
                        last_logged = done_mb
                        if expected > 0:
                            self.log(f"  … {done_mb:.0f} / {expected / 1048576:.0f} MB "
                                     f"({pct:.0f}%)  [{elapsed}s]")
                        else:
                            self.log(f"  … {done_mb:.0f} MB downloaded  [{elapsed}s]")

                # ── Verify before promoting staging → live maps dir ───────────
                self.log("─" * 48)
                self.log("  Verifying download…")
                actual    = self._dir_size(dest_tmp)
                has_vpk   = any(f.lower().endswith(".vpk")
                                for _r, _d, fs in os.walk(dest_tmp) for f in fs)
                self._dl_progress = {"id": workshop_id, "downloaded": actual,
                                     "total": expected,
                                     "pct": (round(actual / expected * 100.0, 1)
                                             if expected > 0 else 0.0),
                                     "phase": "verifying"}
                # A complete download is at least the Steam-reported size (it can
                # be slightly larger because of DepotDownloader's manifest cache).
                # 1%% slack absorbs rounding; a partial download falls well short.
                size_ok = (expected <= 0) or (actual >= expected * 0.99)
                if has_vpk and size_ok:
                    if expected > 0:
                        self.log(f"  ✓ Verified {actual / 1048576:.1f} MB "
                                 f"(expected {expected / 1048576:.1f} MB)")
                    else:
                        self.log(f"  ✓ Verified {actual / 1048576:.1f} MB "
                                 "(no expected size to compare — .vpk present)")
                    # Promote: replace any existing copy with the staged one.
                    try:
                        if os.path.isdir(dest):
                            shutil.rmtree(dest, ignore_errors=True)
                        os.replace(dest_tmp, dest)
                    except Exception as exc:
                        self.log(f"  ✗ Could not move into place: {exc}")
                        shutil.rmtree(dest_tmp, ignore_errors=True)
                        raise
                    self.log(f"  DOWNLOAD COMPLETE — {workshop_id}")
                    if not session_ok:
                        self.steam_session_active = True
                        self.save_config()
                        self.log("  Session token saved — future downloads won't need login")
                        if self.on_steam_session_change:
                            self.on_steam_session_change()
                    success = True
                else:
                    if not has_vpk:
                        self.log(f"  ✗ Verify FAILED — no .vpk in download "
                                 f"(exit code {proc.returncode})")
                    else:
                        self.log(f"  ✗ Verify FAILED — incomplete: got "
                                 f"{actual / 1048576:.1f} MB of "
                                 f"{expected / 1048576:.1f} MB "
                                 f"(exit code {proc.returncode})")
                    # Drop the partial so it never shows up as a usable map.
                    shutil.rmtree(dest_tmp, ignore_errors=True)
                    # A failure on the cached-session path almost always means
                    # the saved token expired.  Invalidate it so the next
                    # download attempt forces a fresh interactive login.
                    if session_ok and self.steam_session_active:
                        self.steam_session_active = False
                        self.save_config()
                        self.log("  ⚠  Cached Steam session looks expired — invalidated.")
                        self.log("     Next download will prompt for password / Steam Guard.")
                        if self.on_steam_session_change:
                            self.on_steam_session_change()
                self.log("─" * 48)
            except FileNotFoundError:
                self.log(f"  DepotDownloader not found: {_config.DEPOTDL_PATH}")
            except Exception as exc:
                self.log(f"  DepotDownloader error: {exc}")
            finally:
                self._active_dl_proc = None
                self._dl_progress = {}
                # Belt-and-braces: never leave a staging folder behind.
                if not success:
                    shutil.rmtree(os.path.join(_config.WORKSHOP_DIR,
                                               workshop_id + ".partial"),
                                  ignore_errors=True)
                with self._dl_lock:
                    self._dl_reqs = [r for r in self._dl_reqs
                                     if r["id"] != workshop_id]
                if on_done:
                    on_done(success)
        threading.Thread(target=_run, daemon=True).start()

    # ── public IP ─────────────────────────────────────────────────────────────

    def check_public_ip(self) -> None:
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
        """Parse 'status' RCON output into a list of player dicts.

        CS2 status lines look like:
          #  <userid>  "<name>"  <steamid>  <connected>  <ping>  <loss>  <state>  <rate>

        The steamid field has changed across CS2 builds and can appear as:
          STEAM_X:X:XXXXXXXX  — legacy Steam2 format
          [U:1:XXXXXXXX]      — Steam3 format
          76561XXXXXXXXXXXX   — Steam64 17-digit format
          BOT                 — bot placeholder (kept in list)
        """
        players: list[dict] = []
        pattern = re.compile(
            r"^#\s+(\d+)\s+"    # #  <userid>
            r'"([^"]*)"'         # "<name>"
            r"\s+(\S+)"          # <steamid — any token>
            r"\s+(\S+)"          # <connected time>
            r"\s+(\d+)",         # <ping>
            re.MULTILINE,
        )
        for m in pattern.finditer(status_output):
            steamid = m.group(3)
            if steamid == "uniqueid":   # header row — skip
                continue
            players.append({
                "userid":  m.group(1),
                "name":    m.group(2),
                "steamid": steamid,
                "time":    m.group(4),
                "ping":    m.group(5),
            })
        return players

    def get_players(self, callback: Callable[[list[dict]], None]) -> None:
        def _do() -> None:
            try:
                out     = self.rcon.execute("status")
                players = self._parse_players(out)
                # Diagnostic: if the server returned output but we parsed nothing,
                # log the raw text so a format change can be spotted quickly.
                if out.strip() and not players:
                    self.log("[players] status returned output but no players "
                             f"could be parsed — raw:\n{out[:600]}")
                callback(players)
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
                existing = self._read_ban_lines()
                if any(steamid in line for line in existing):
                    self.log(f"[bans] {steamid} is already in the ban list")
                else:
                    existing.append(f"banid {duration} {steamid}")
                    self._write_ban_lines(existing)
                    dur_str = "permanent" if duration == 0 else f"{duration} min"
                    self.log(f"Banned: {name or steamid} ({dur_str})")
            except Exception as exc:
                self.log(f"Ban (file write) failed: {exc}")
            if self.running:
                try:
                    self.rcon.execute(f"banid {duration} {steamid}")
                except Exception as exc:
                    self.log(f"Ban RCON: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def unban_player(self, steamid: str) -> None:
        def _do() -> None:
            try:
                lines = self._read_ban_lines()
                new_lines = [l for l in lines if steamid not in l]
                removed = len(lines) - len(new_lines)
                if removed:
                    self._write_ban_lines(new_lines)
                    self.log(f"Unbanned: {steamid} "
                             f"(removed {removed} entr{'ies' if removed != 1 else 'y'})")
                else:
                    self.log(f"[bans] {steamid} not found in ban list")
            except Exception as exc:
                self.log(f"Unban (file write) failed: {exc}")
            if self.running:
                try:
                    self.rcon.execute(f"removeid {steamid}")
                except Exception as exc:
                    self.log(f"Unban RCON: {exc}")
        threading.Thread(target=_do, daemon=True).start()

    def get_ban_list(self, callback: Callable[[list[str]], None]) -> None:
        """Return the server's current ban list.

        Strategy (in order):
          1. Read csgo/cfg/banned_user.cfg from disk — works offline, always
             current.  Each line has the form:  banid <duration> <steamid>
          2. Fall back to RCON 'listid' if the file doesn't exist yet.
        """
        def _do() -> None:
            # 1. Disk file — works offline, always current
            try:
                raw = self._read_ban_lines()
                lines = [s for l in raw if (s := l.strip()) and not s.startswith("//")]
                if lines or os.path.exists(self._ban_file):
                    self.log(f"[bans] Read {len(lines)} ban(s) from {self._ban_file}")
                    callback(lines)
                    return
            except Exception as exc:
                self.log(f"[bans] Could not read ban file: {exc}")

            # 2. Fall back to RCON listid when file is absent
            if not self.running:
                self.log("[bans] Server offline and no ban file found on disk")
                callback([])
                return
            try:
                out = self.rcon.execute("listid")
                self.log(f"[bans] listid raw output:\n{out.strip() or '(empty)'}")
                lines = [
                    s for l in out.splitlines()
                    if (s := l.strip()) and re.search(
                        r"(STEAM_|\[U:|765\d{14,}|BOT)", s, re.IGNORECASE
                    )
                ]
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
                # Two cvars — batch over one connection instead of opening two
                self.rcon.execute_many([
                    f"mp_friendlyfire {1 if enabled else 0}",
                    f"mp_autokick {0 if enabled else 1}",
                ])
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
                # Batch into one connection: bot_difficulty + N × bot_add
                # (each execute() call previously opened its own TCP socket)
                cmds = [f"bot_difficulty {diff}"] + ["bot_add"] * count
                self.rcon.execute_many(cmds)
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
        if not ids:
            if on_done:
                on_done()
            return

        def _do() -> None:
            try:
                params: dict[str, str | int] = {"itemcount": len(ids)}
                for i, wid in enumerate(ids):
                    params[f"publishedfileids[{i}]"] = wid
                req = urllib.request.Request(
                    "https://api.steampowered.com"
                    "/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                    data=urllib.parse.urlencode(params).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                changed = False
                for item in data.get("response", {}).get("publishedfiledetails", []):
                    wid   = item.get("publishedfileid", "")
                    title = item.get("title", "").strip()
                    tags  = [t.get("tag", "").lower()
                             for t in item.get("tags", []) if t.get("tag")]
                    preview = item.get("preview_url", "")
                    if wid:
                        if title:
                            self._map_name_cache[wid] = title
                        if tags:
                            self._map_tag_cache[wid] = tags
                        if preview:
                            self._preview_url_cache[wid] = preview
                        # Detect command-filter need from the description.
                        needs = bool(_CMDFILTER_RE.search(item.get("description", "")))
                        if self._cmdfilter_auto.get(wid) != needs:
                            self._cmdfilter_auto[wid] = needs
                            changed = True
                        tag_str = f"  [{', '.join(tags[:4])}]" if tags else ""
                        flag_str = "  (needs -disable_workshop_command_filtering)" if needs else ""
                        self.log(f"  Workshop: {wid} → {title or '(no title)'}{tag_str}{flag_str}")
                if changed:
                    self.save_config()
            except Exception as exc:
                self.log(f"Workshop name fetch failed: {exc}")
            finally:
                if on_done:
                    on_done()
        threading.Thread(target=_do, daemon=True).start()

    # ── workshop command-filter handling ────────────────────────────────────────

    def cmdfilter_effective(self, wid: str) -> bool:
        """Whether map *wid* should launch with -disable_workshop_command_filtering.

        Manual override (set in the GUI) wins; otherwise the auto-detected value
        from the Steam description; default False (filter stays on).
        """
        if wid in self._cmdfilter_override:
            return bool(self._cmdfilter_override[wid])
        return bool(self._cmdfilter_auto.get(wid, False))

    def cmdfilter_status(self, wid: str) -> dict:
        """Per-map status for the UI: auto-detected, override, effective."""
        return {
            "auto":      bool(self._cmdfilter_auto.get(wid, False)),
            "override":  self._cmdfilter_override.get(wid),  # None | True | False
            "effective": self.cmdfilter_effective(wid),
        }

    def set_cmdfilter_override(self, wid: str, value: bool | None) -> None:
        """Set (True/False) or clear (None → back to auto) the manual override."""
        if value is None:
            self._cmdfilter_override.pop(wid, None)
        else:
            self._cmdfilter_override[wid] = bool(value)
        self.save_config()

    def scan_cmdfilter(self, on_done: Callable[[list[str]], None] | None = None) -> None:
        """Re-fetch descriptions for every downloaded workshop map and refresh the
        auto-detected command-filter flags.  Reports the list of flagged wids."""
        def _do() -> None:
            try:
                wsdir = _config.WORKSHOP_DIR
                ids = []
                if os.path.isdir(wsdir):
                    for name in os.listdir(wsdir):
                        p = os.path.join(wsdir, name)
                        if (name.isdigit() and os.path.isdir(p)
                                and any(os.scandir(p))):
                            ids.append(name)
                if not ids:
                    self.log("[cmdfilter] No downloaded workshop maps to scan")
                    if on_done:
                        on_done([])
                    return
                self.log(f"[cmdfilter] Scanning {len(ids)} downloaded map(s)…")
                flagged: list[str] = []
                # Steam caps detail requests; batch in chunks of 50.
                for start in range(0, len(ids), 50):
                    chunk = ids[start:start + 50]
                    params: dict[str, str | int] = {"itemcount": len(chunk)}
                    for i, wid in enumerate(chunk):
                        params[f"publishedfileids[{i}]"] = wid
                    req = urllib.request.Request(
                        "https://api.steampowered.com"
                        "/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                        data=urllib.parse.urlencode(params).encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        data = json.loads(resp.read())
                    for item in data.get("response", {}).get("publishedfiledetails", []):
                        wid = item.get("publishedfileid", "")
                        if not wid:
                            continue
                        title = item.get("title", "").strip()
                        if title:
                            self._map_name_cache[wid] = title
                        needs = bool(_CMDFILTER_RE.search(item.get("description", "")))
                        self._cmdfilter_auto[wid] = needs
                        if needs:
                            flagged.append(wid)
                self.save_config()
                if flagged:
                    self.log(f"[cmdfilter] {len(flagged)} map(s) need "
                             "-disable_workshop_command_filtering:")
                    for wid in flagged:
                        self.log(f"  • {self._map_name_cache.get(wid, wid)} ({wid})")
                else:
                    self.log("[cmdfilter] No maps document needing the flag")
                if on_done:
                    on_done(flagged)
            except Exception as exc:
                self.log(f"[cmdfilter] Scan failed: {exc}")
                if on_done:
                    on_done([])
        threading.Thread(target=_do, daemon=True).start()

    # ── install / setup state ─────────────────────────────────────────────────

    @property
    def is_installed(self) -> bool:
        """True when cs2.exe is present at the configured server directory.

        Checked before every start attempt so the UI can show an "Install first"
        prompt rather than silently failing when the binary is missing.
        """
        return bool(self.server_dir) and os.path.isfile(_config.CS2_PATH)

    @property
    def needs_setup(self) -> bool:
        """True when the app still needs first-run configuration.

        Conditions:
          • No server directory has been chosen yet, OR
          • The admin PIN is still the factory default ("1234").
        """
        return not self.server_dir or self.admin_pin == "1234"

    # ── uptime ────────────────────────────────────────────────────────────────

    @property
    def uptime_seconds(self) -> int:
        """Seconds since the server became RCON-ready; 0 if offline or booting."""
        if self._uptime_start is None:
            return 0
        return max(0, int(time.time() - self._uptime_start))

    # ── crash monitor ──────────────────────────────────────────────────────────

    def start_monitor(self) -> None:
        """Daemon thread: detects unexpected server process death.

        Polls every 2 s.  When cs2.exe exits without us calling stop_server()
        the state is cleaned up and on_state_change is fired.

        Two detection paths:
          • Popen-started  — proc.poll() returns an exit code (fast, 2 s latency)
          • Probe-reattached — proc is None; tasklist check every ~10 s

        If auto_restart_on_crash is True the server is automatically restarted
        with the last known map / mode (up to 3 consecutive attempts).
        """
        _MAX_RESTARTS = 3
        _restart_count = 0

        def _handle_crash(exit_code: int | None = None) -> None:
            """Shared teardown + optional auto-restart logic."""
            nonlocal _restart_count
            with self._lifecycle_lock:
                self.proc          = None
                self.running       = False
                self.boot_state    = "offline"
                self.player_count  = 0
                self._uptime_start = None
            if exit_code is not None:
                self.log(
                    f"[!] Server process exited unexpectedly "
                    f"(exit code: {exit_code})"
                )
            else:
                self.log(
                    "[!] Server process disappeared unexpectedly "
                    "(exit code: unknown — was probe-reattached)"
                )
            if self.on_state_change:
                self.on_state_change()

            if self.auto_restart_on_crash and _restart_count < _MAX_RESTARTS:
                _restart_count += 1
                self.log(
                    f"[!] Auto-restart #{_restart_count}/{_MAX_RESTARTS} "
                    f"in 5 s…"
                )
                time.sleep(5)
                self.log(
                    f"[!] Restarting server — "
                    f"map: {self.current_map}  mode: {self.current_mode}"
                )
                wk = self.current_map.isdigit()
                self.start_server(self.current_map, self.current_mode,
                                  is_workshop=wk)
            elif _restart_count >= _MAX_RESTARTS:
                self.log(
                    "[!] Auto-restart limit reached — "
                    "manual intervention required."
                )
                _restart_count = 0

        def _watch() -> None:
            nonlocal _restart_count
            _probe_tick = 0   # counts 2-s ticks; tasklist fires every 5 (≈10 s)
            while True:
                try:
                    time.sleep(2)
                    proc = self.proc           # snapshot under GIL

                    if not self.running:
                        _restart_count = 0
                        _probe_tick    = 0
                        continue

                    if proc is None:
                        # ── Probe-reattached server: no Popen handle ──────────
                        # Fall back to a periodic tasklist check so we still
                        # detect crashes even though we don't own the process.
                        _probe_tick += 1
                        if _probe_tick < 5:    # 5 × 2 s = 10 s between checks
                            continue
                        _probe_tick = 0
                        try:
                            res = subprocess.run(
                                ["tasklist", "/FI", "IMAGENAME eq cs2.exe",
                                 "/NH", "/FO", "CSV"],
                                capture_output=True, text=True, timeout=5,
                            )
                            if "cs2.exe" in res.stdout.lower():
                                continue       # still alive — nothing to do
                        except Exception:
                            continue           # can't determine — assume alive
                        # cs2.exe has disappeared
                        _handle_crash(exit_code=None)
                        continue

                    # ── Popen-started server ──────────────────────────────────
                    _probe_tick = 0
                    if proc.poll() is None:
                        continue               # still running — nothing to do

                    exit_code = proc.poll()
                    _handle_crash(exit_code=exit_code)

                except Exception as _exc:
                    # Never let the monitor thread die — log and keep looping.
                    try:
                        self.log(f"[monitor] Unexpected error: {_exc}")
                    except Exception:
                        pass

        threading.Thread(target=_watch, daemon=True).start()

        # ── Player-count poller ───────────────────────────────────────────────
        # Runs on its own daemon thread so the crash-detection loop (_watch)
        # is never blocked by a slow / failing RCON call.
        def _player_count_loop() -> None:
            while True:
                time.sleep(15)
                if self.boot_state != "ready":
                    if self.player_count != 0:
                        self.player_count = 0
                    continue
                try:
                    out = self.rcon.execute("status")
                    self.player_count = len(self._parse_players(out))
                except Exception:
                    pass   # keep the last known count; don't spam the log

        threading.Thread(target=_player_count_loop, daemon=True).start()

    # ── RCON execute (passthrough for the web API) ────────────────────────────

    def rcon_execute(self, command: str,
                     callback: Callable[[str, str | None], None] | None = None
                     ) -> None:
        """Execute an arbitrary RCON command on a background thread.

        *callback* is called with (response_text, error_message_or_None).
        """
        def _do() -> None:
            try:
                resp = self.rcon.execute(command)
                self.log(f"[rcon] > {command}  → {resp.strip()[:120]}")
                if callback:
                    callback(resp, None)
            except Exception as exc:
                self.log(f"[rcon] > {command}  ✗ {exc}")
                if callback:
                    callback("", str(exc))
        threading.Thread(target=_do, daemon=True).start()
