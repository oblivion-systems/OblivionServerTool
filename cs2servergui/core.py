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
import urllib.error
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
    # RCON_HOST is INTENTIONALLY NOT imported by name — _resolve_rcon_host
    # rebinds _config.RCON_HOST on every server start, but a `from .config
    # import RCON_HOST` would capture the import-time value forever in this
    # module's namespace, leaving _poll_rcon_ready (and any other reader) on
    # the stale IP after a DHCP change or VPN/adapter shuffle.  Always read
    # `_config.RCON_HOST` at call time.
    RCON_PORT, RCON_PASSWORD,
    MODE_SETTINGS, MODE_MAPS, _DEFAULT_MODE,
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

# v0.15.0 slice 1 — Self-describing plugins.
#
# Each plugin folder ships a `plugin.json` (schema_version 1) that declares
# everything the host needs: kind, modes, load_order, copy_rules, verify
# files, and cleanup paths.  The five tables below (_PLUGIN_KIND, etc.) are
# now BUILT from those manifests at module-load time — no per-plugin code
# edits required to add a new one.
#
# Discovery scans:
#   1. cs2servergui/plugins/<slug>/plugin.json    (bundled)
#   2. %APPDATA%/Oblivion Server Tool/plugins/<slug>/plugin.json   (user-installed)
#
# Local plugins override bundled ones if they share a slug — operators can
# patch a built-in plugin without recompiling the .exe.  A plugin folder
# without a plugin.json is skipped with a loud stderr warning so corrupted
# drops don't silently disappear.


def _resolve_user_plugins_dir() -> str:
    """Where operators drop their own plugin folders.  Same APPDATA root as
    the config file.  Stable across reinstalls (per the uninstaller policy
    in installer.iss — config dir is preserved on uninstall)."""
    return os.path.join(os.path.dirname(_CONFIG_FILE), "plugins")


def _load_plugin_manifest_file(plugin_dir: str, slug: str, source: str) -> dict | None:
    """Read a single plugin's plugin.json.  Returns None + logs to stderr
    on any failure (missing file, bad JSON, schema mismatch, missing required
    fields).  Required fields: slug, display_name, kind, modes, copy_rules.
    Optional with sensible defaults: summary, author, load_order, verify_files,
    cleanup."""
    manifest_path = os.path.join(plugin_dir, "plugin.json")
    if not os.path.isfile(manifest_path):
        # Silent skip — bundled plugin folders without manifests just don't
        # show up.  This is the migration safety net; once every bundled
        # plugin has a manifest, _MIGRATION_SNAPSHOT enforces full coverage.
        return None
    try:
        with open(manifest_path, encoding="utf-8") as f:
            m = json.load(f)
    except Exception as exc:
        print(f"[plugins] Failed to load {manifest_path!r}: {exc!r} — "
              f"plugin {slug!r} will be ignored.",
              file=sys.stderr)
        return None
    if m.get("schema_version") != 1:
        print(f"[plugins] {manifest_path!r} schema_version={m.get('schema_version')!r} "
              f"(want 1) — plugin {slug!r} will be ignored.",
              file=sys.stderr)
        return None
    declared_slug = m.get("slug") or ""
    if declared_slug != slug:
        print(f"[plugins] {manifest_path!r} declares slug={declared_slug!r} but lives "
              f"in folder {slug!r} — mismatch, plugin ignored.",
              file=sys.stderr)
        return None
    required = ("display_name", "kind", "modes", "copy_rules")
    missing = [f for f in required if f not in m]
    if missing:
        print(f"[plugins] {manifest_path!r} missing required fields {missing!r} — "
              f"plugin {slug!r} will be ignored.",
              file=sys.stderr)
        return None
    # Attach source + filesystem location for downstream consumers
    # (API surfaces this; deploy_plugins uses plugin_dir as the copy root).
    m["_plugin_dir"] = plugin_dir
    m["_source"]    = source
    return m


def _discover_plugins() -> dict[str, dict]:
    """Scan bundled + user plugin folders.  Returns slug -> manifest dict.

    Local plugins OVERRIDE bundled ones if slugs collide — explicit log
    line so the operator notices when they've shadowed a built-in.
    Called once at module load.  A future Plugin tab "Reload" button will
    re-run this and replace the derived tables in place.
    """
    discovered: dict[str, dict] = {}

    # 1. Bundled plugins (cs2servergui/plugins/<slug>/)
    if os.path.isdir(_PLUGINS_BASE):
        for entry in sorted(os.listdir(_PLUGINS_BASE)):
            plugin_dir = os.path.join(_PLUGINS_BASE, entry)
            if not os.path.isdir(plugin_dir):
                continue
            m = _load_plugin_manifest_file(plugin_dir, entry, source="bundled")
            if m:
                discovered[entry] = m

    # 2. User plugins (%APPDATA%/.../plugins/<slug>/)
    user_dir = _resolve_user_plugins_dir()
    if os.path.isdir(user_dir):
        for entry in sorted(os.listdir(user_dir)):
            plugin_dir = os.path.join(user_dir, entry)
            if not os.path.isdir(plugin_dir):
                continue
            m = _load_plugin_manifest_file(plugin_dir, entry, source="local")
            if m:
                if entry in discovered:
                    print(f"[plugins] Local plugin {entry!r} overrides bundled version.",
                          file=sys.stderr)
                discovered[entry] = m

    return discovered


def _populate_plugin_tables(plugins: dict[str, dict]) -> tuple[
        dict[str, str],           # kind
        dict[str, list[str]],     # verify_files
        dict[str, list[tuple]],   # copy_rules
        dict[str, list[str]],     # cleanup_items
        dict[str, list[str]],     # mode_plugin_names
]:
    """Derive the five plugin tables from the discovered manifests.

    mode_plugin_names is sorted by each plugin's load_order so MetaMod
    plugins (load_order 10) deploy before CSS plugins (load_order 20) —
    matters because metamod plugins overlay cfg files that later metamod
    plugins (like zombie_ze, load_order 15) can override.  Within the
    same load_order, sort is stable (slug order)."""
    kind: dict[str, str]                  = {}
    verify_files: dict[str, list[str]]    = {}
    copy_rules: dict[str, list[tuple]]    = {}
    cleanup_items: dict[str, list[str]]   = {}
    mode_plugin_names: dict[str, list[str]] = {}

    for slug, m in plugins.items():
        kind[slug] = m.get("kind", "css")
        # Normalise path separators — manifests use forward slashes for
        # cross-platform readability; convert to OS-native here.
        verify_files[slug] = [os.path.normpath(p) for p in m.get("verify_files", [])]
        cleanup_items[slug] = [os.path.normpath(p) for p in m.get("cleanup", [])]
        rules: list[tuple] = []
        for r in m.get("copy_rules", []):
            src = r.get("src", "")
            dst = r.get("dst", "")
            exclude = r.get("exclude")
            if exclude:
                rules.append((src, dst, frozenset(exclude)))
            else:
                rules.append((src, dst))
        copy_rules[slug] = rules
        for mode in m.get("modes", []):
            mode_plugin_names.setdefault(mode, []).append(slug)

    # Stable sort by load_order so metamod loads before css.
    for mode, slugs in mode_plugin_names.items():
        slugs.sort(key=lambda s: plugins[s].get("load_order", 20))

    return kind, verify_files, copy_rules, cleanup_items, mode_plugin_names


# Discover at module load (mirrors _PLUGIN_CATALOG's pattern).  A future
# slice may add a "Reload plugins" button that re-runs discovery and swaps
# the derived tables; for now, restart-app-to-pick-up-changes is fine.
_DISCOVERED_PLUGINS: dict[str, dict] = _discover_plugins()
(_PLUGIN_KIND,
 _PLUGIN_VERIFY_FILES,
 _PLUGIN_COPY_RULES,
 _PLUGIN_CLEANUP_ITEMS,
 _MODE_PLUGIN_NAMES) = _populate_plugin_tables(_DISCOVERED_PLUGINS)

# Plugin catalog — display metadata for the Plugin Manager tab.
#
# v0.13.2 (task #92): hardcoded inline dict.
# v0.14.0 slice 4 (task #90): moved to cs2servergui/registry/catalog.json
# so new plugin entries don't need an app release.
# v0.14.0 audit fix #4: inline fallback dict removed — silent drift between
# the inline copy and the JSON was the real risk (audit caught that the
# test only compared slugs, not display strings).  Now the JSON is the
# sole source of truth.  If loading fails, an empty dict is returned and
# the failure is logged loudly to stderr (also shows up in the .exe
# console + Windows debug log) so a botched JSON edit is visible
# immediately, not after the next deploy.
#
# Future v0.15.x layer: catalog.json gets fetched from a remote URL (the
# OblivionPluginRegistry repo's raw.githubusercontent.com), merged with
# the bundled file, cached locally.  The shape below is registry-
# compatible — the "versions" array is empty for bundled-only plugins
# and populated for registry-fetched plugins with downloadable artifacts.


def _resolve_catalog_path() -> str:
    """Mirror of _resolve_plugins_base for the registry/ folder.  In a
    PyInstaller frozen .exe, sys._MEIPASS holds the temp extract root."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, "registry", "catalog.json")
    if os.path.isfile(candidate):
        return candidate
    # Frozen .exe: PyInstaller extracts to sys._MEIPASS/<pkg>/registry/
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        fz = os.path.join(meipass, "cs2servergui", "registry", "catalog.json")
        if os.path.isfile(fz):
            return fz
    return candidate  # return for diagnostic — load step will report not-found


def _load_plugin_catalog() -> dict[str, dict]:
    """Load registry/catalog.json into the same slug→meta shape the rest
    of the codebase consumes.

    On any failure (file missing, bad JSON, no plugins array), log the
    error to stderr and return an empty dict.  The Plugin Manager tab
    will then show "Unknown" cards instead of crashing, and the operator
    sees the failure in the app's log drawer / diag snapshot rather than
    silently getting stale inline-fallback data.
    """
    path = _resolve_catalog_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as exc:
        # Plain print to stderr — no logger available at module-import time,
        # and AppCore.log doesn't exist yet.  Wraps in the same "[catalog]"
        # prefix the runtime uses so operators can grep the .exe console.
        print(f"[catalog] Failed to load {path!r}: {exc!r} — "
              f"Plugin Manager will show unnamed cards until the JSON is fixed.",
              file=sys.stderr)
        return {}
    out: dict[str, dict] = {}
    for entry in (doc.get("plugins") or []):
        slug = entry.get("slug")
        if not slug:
            continue
        out[slug] = {
            "display_name": entry.get("display_name") or slug,
            "summary":      entry.get("summary") or "",
            "author":       entry.get("author") or "",
        }
    if not out:
        print(f"[catalog] {path!r} parsed OK but no usable 'plugins' entries — "
              f"Plugin Manager will show unnamed cards.",
              file=sys.stderr)
    return out


_PLUGIN_CATALOG: dict[str, dict] = _load_plugin_catalog()

# Curated packs surfaced at the top of the Plugin Manager tab (v0.14.0 — task #91).
# Each pack is a one-click recipe: switching to its mode auto-deploys the right
# plugins (via _MODE_PLUGIN_NAMES) and stages the operator's most likely map.
# The plugin list is derived, not stored — keeps packs in sync with mode→plugin
# changes without a separate edit.  Order = SPA display order (left to right).
#
# Field reference:
#   id           — stable slug used by /api/plugins/apply_pack
#   name         — display name on the card
#   mode         — must be a key in MODE_SETTINGS
#   default_map  — staged as current_map on apply; must be in MODE_MAPS[mode]
#                  (or None to skip the map stage — Jailbreak's workshop-only)
#   summary      — one-line description shown on the card
#   tags         — informational chips beneath the title (display-only)
_PLUGIN_PACKS: list[dict] = [
    {
        "id":          "competitive_5v5",
        "name":        "Competitive 5v5",
        "mode":        "5v5",
        "default_map": "de_dust2",
        "summary":     "Tournament-style 5v5 — MatchZy knife round, pauses, demos, stats.",
        "tags":        ["match", "tournament"],
    },
    {
        "id":          "warcraft_night",
        "name":        "Warcraft Night",
        "mode":        "Warcraft",
        "default_map": "de_mirage",
        "summary":     "RPG overlay with 9 classes, XP, and abilities. Best for lobby nights.",
        "tags":        ["casual", "rpg"],
    },
    {
        "id":          "casual_deathmatch",
        "name":        "Casual Deathmatch",
        "mode":        "Deathmatch",
        "default_map": "de_dust2",
        "summary":     "Instant respawn deathmatch with spawn protection. Aim warm-up.",
        "tags":        ["casual", "warmup"],
    },
    {
        "id":          "retakes_inferno",
        "name":        "Retakes",
        "mode":        "Retakes",
        "default_map": "de_inferno",
        "summary":     "B3none bombsite retakes — curated spawns + RetakesAllocator loadouts.",
        "tags":        ["practice"],
    },
    {
        "id":          "vanilla_competitive",
        "name":        "Vanilla Competitive",
        "mode":        "Competitive",
        "default_map": "de_dust2",
        "summary":     "Stock CS2 with no managed plugins. Cleanest baseline for testing.",
        "tags":        ["vanilla", "baseline"],
    },
]

# NOTE: _MODE_PLUGIN_NAMES is now DERIVED from each plugin's plugin.json
# "modes" field (see _populate_plugin_tables above).  Per-mode comments
# previously inlined here are now embedded in their plugin's plugin.json
# manifest or in the plugin folder's README.  All modes not declared by
# any plugin (Competitive, Casual, Wingman, Arms Race, Demolition, ...) =
# vanilla server (deploy_plugins runs the empty-plugin branch).

# Modes that MUST launch with -disable_workshop_command_filtering regardless of map.
# Mounting a workshop content addon (MultiAddonManager) turns CS2's workshop command
# filtering ON for the whole session — even on official maps — which then rejects the
# mode's own server CVars. Zombie Escape mounts the ZombieReborn pack and relies on
# zm_enable + cs2f_*/zr_*/zm_* CVars, so without the flag ZM silently never enables.
_CMDFILTER_REQUIRED_MODES: frozenset[str] = frozenset({"Zombie Escape"})

# NOTE: _PLUGIN_COPY_RULES and _PLUGIN_CLEANUP_ITEMS are now DERIVED from
# each plugin's plugin.json (copy_rules / cleanup fields), see
# _populate_plugin_tables above.  Per-plugin operational comments
# previously inlined here have moved into the respective plugin.json or
# its sibling README.txt (warcraft's ModelPrecacher rationale is in
# cs2servergui/plugins/warcraft/README.txt).


class AppCore:
    """Single source of truth shared between the local GUI and Flask."""

    def __init__(self) -> None:
        # v0.13.0 / task #86 — game driver.  The active driver
        # is the single point of truth for game-specific identity
        # (process name, log path, modes, port).  New code reaches
        # game-specific knobs via `core.driver.X` instead of
        # hardcoding "cs2.exe" / "MatchZy" literals.  Existing code
        # in this file still uses the literals — those get migrated
        # one seam at a time (strangler-fig).
        # See cs2servergui/drivers/__init__.py for the architecture.
        from .drivers import CS2Driver
        self.driver = CS2Driver()

        self.proc:         subprocess.Popen | None = None
        self.running:      bool = False
        self.boot_state:   str  = "offline"   # "offline" | "booting" | "ready"
        self.player_count: int  = 0            # live count; updated every ~15 s by monitor
        self.current_map:  str  = "de_dust2"
        self.current_mode: str  = "Competitive"

        # RCONClient created with empty password; updated after _load_config()
        # so the runtime-configured password (not the config.py default) is used.
        # NB: host is re-resolved via _resolve_rcon_host() on every server start /
        # attach — config.py's RCON_HOST is computed once at import time and goes
        # stale if the LAN IP changes (network blip during boot → 127.0.0.1 fallback
        # → all RCON fails for the whole session even after the network recovers).
        self.rcon = RCONClient(_config.RCON_HOST, RCON_PORT, "")

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

        # Serialises save_config() against concurrent callers (Flask is
        # threaded; the auto-save thread + a user Save click can race) and is
        # held across the tmp-write + os.replace so the swap is atomic.
        self._config_save_lock = threading.Lock()

        # Signal raised by stop_server / Unload paths to break a sleep inside
        # the crash auto-restart backoff (5s → 15s → 45s) instead of forcing
        # the operator to wait for the timer to tick down.  Set during stop,
        # cleared at the top of every fresh start_server.
        self._stop_event = threading.Event()

        # ── Map-veto session (v0.10.0) ──────────────────────────────────────
        # At most ONE active session at a time per AppCore (one server, one
        # match-setup flow at a time).  `_veto_lock` serialises every public
        # operation on the session because Flask is threaded and the SSE
        # mirror means concurrent state reads happen continuously.  The
        # session itself is None until create_veto_session() is called.
        from . import veto as _veto_module
        self._veto = _veto_module           # bound for callers; avoids re-import per method
        self._veto_session: _veto_module.VetoSession | None = None
        self._veto_lock = threading.Lock()
        # v0.11.17 B3 — single-shot guard against double-firing the
        # matchzy_loadmatch handoff.  Set under _veto_lock when a finale
        # is committed; checked by both the admin /api/veto/finale button
        # and the captain-ready auto-launch path so concurrent triggers
        # serialize through one MatchZy load.  Cleared on reset/rematch.
        self._finale_firing: bool = False
        # v0.11.3 — active-session persistence load happens AFTER
        # _load_config() so self.log() / self.on_log are wired up first.

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
        self.admin_pin:     str = ""   # PIN for the web panel (full admin access)
        self.guest_pin:     str = ""   # optional PIN for the limited guest role
                                       # (maps/modes/workshop downloads); "" = off
        self.flask_port:    int = 5050 # user-overridable in oblivion_config.json;
                                       # config.py reads it at import time

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
        # v0.10.1 — online-primary veto support
        # public_share_url: when set, captain link cards build the Public
        #   URL from this base instead of `http://<public_ip>:<port>`.  Use
        #   case: operator is running cloudflared (or any reverse proxy);
        #   their captains live on the internet not the LAN; the
        #   port-forward URL doesn't reach them.  Operator pastes the
        #   tunnel URL (e.g. https://random-words.trycloudflare.com) and
        #   the SPA serves that as the captain join URL.
        self.public_share_url:     str  = ""
        # veto_auto_launch_on_ready: when both captains tick Ready on the
        #   finale page, fire matchzy_loadmatch automatically.  Off by
        #   default — admin clicks GO manually so they can verify the
        #   server's in the right mode first.
        self.veto_auto_launch_on_ready: bool = False
        # v0.11.0 polish — operator-configurable MatchZy cvars (Config tab
        # → 'MatchZy cvars').  Merged on top of veto.DEFAULT_MATCHZY_CVARS
        # at build_matchzy_config() time; operator wins on conflicts; an
        # empty-string value actively suppresses a default cvar.
        # Stored as {str: str} for clean JSON round-tripping.
        self.matchzy_cvars:             dict[str, str] = {}
        # v0.10.2 — Discord webhook URL.  When set, the tool POSTs an
        # embed to this channel on every finale (teams + maplist + decider
        # + connect string).  Captures most of the "spectators see results"
        # value from a full Discord bot with 20 lines of code.  Operator
        # creates a webhook in Discord channel settings + pastes the URL
        # in Config → Veto / Match Setup.
        self.discord_webhook_url:   str  = ""
        # v0.11.0 — Discord bot integration (Layer 1).  When `discord_bot_token`
        # is set the tool starts a background gateway connection to Discord
        # via discord.py.  Operator configures these in Config → Discord:
        #   discord_bot_token  — secret bot token from developer.discord.com
        #   discord_guild_id   — operator's Discord server ID (numeric)
        #   discord_veto_channel_id — channel where live veto embeds are posted
        #                              (Layer 1C); blank = no live embed
        #   discord_voice_channel_id — v0.11.15 — default VC for "Pull from
        #                              voice channel" roster import.  When
        #                              set, the roster modal pulls members
        #                              from THIS VC directly (one-click);
        #                              when blank, the picker modal opens
        #                              and the operator chooses each session.
        # The bot lifecycle is owned by cs2servergui/discord_bot.py — see
        # the prose there for the asyncio-on-thread architecture.
        self.discord_bot_token:           str = ""
        self.discord_guild_id:            str = ""
        self.discord_veto_channel_id:     str = ""
        self.discord_voice_channel_id:    str = ""
        # v0.12.0 — per-team voice channels for bot-driven team splits.
        # When both team_a/b VCs are configured AND auto_move toggle is ON,
        # /api/veto/distribute fires a background move that drags every
        # rostered player with a discord_id into their team's VC.  Same
        # behaviour available on-demand via the SPA "Move teams now"
        # button and the `/move-teams now` slash command.  Toggle is
        # explicit (not implicit-if-both-set) so the operator can keep
        # both VC IDs configured but pause auto-moves during testing.
        self.discord_team_a_voice_channel_id: str = ""
        self.discord_team_b_voice_channel_id: str = ""
        self.discord_auto_move_on_distribute_enabled: bool = False
        # v0.12.1 — round-summary embeds posted to discord_veto_channel_id
        # during a live MatchZy match.  RCON-poll-based score-delta
        # detection (every 3s on `mp_t_score` + `mp_ct_score`); on change
        # a round summary embed posts to the same channel that hosts the
        # live veto embed.  Default OFF — opt-in.  Toggle owned by the
        # /api/discord/round_summaries_toggle endpoint AND the
        # `/round-summaries on|off` slash command — same field, two faces.
        self.discord_round_summaries_enabled: bool = False

        # Runtime state
        self.public_ip:           str                      = ""
        # v0.10.2: last user-visible "why did Start fail" — joined preflight
        # error strings, cleared on successful start or stop.  Surfaced in
        # /api/state so a remote admin's Start click that gets silently
        # blocked at preflight has a visible reason.
        self.last_start_error:    str                      = ""
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

        # v0.11.3 — resume any in-flight veto session.  Called now because
        # self.log / self.on_log are wired up after _load_config above.
        self._load_active_veto_session()

        # v0.11.0 — Start the Discord bot if a token is configured.  Safe
        # no-op when no token; the bot module imports cleanly without
        # discord.py if it's missing from the bundle (source-mode runs
        # without the dep installed will see "[discord] discord.py not
        # installed").
        try:
            from . import discord_bot
            discord_bot.start_bot(self)
        except Exception as exc:
            self.log(f"[discord] bot start skipped: {exc}")

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
        # Guest PIN: optional limited-access PIN; empty string disables guest login.
        self.guest_pin = cfg.get("guest_pin", "")
        # Flask port: persisted for the round-trip (config.py reads this same key
        # at module-import time so main.py's `from config import FLASK_PORT` sees
        # the user's value before Flask binds).
        self.flask_port = int(cfg.get("flask_port", 5050))

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
        # v0.10.1 — online-primary veto support (see __init__ for prose)
        self.public_share_url            = cfg.get("public_share_url", "")
        self.veto_auto_launch_on_ready   = bool(cfg.get("veto_auto_launch_on_ready", False))
        # v0.11.0 polish — defensive load: must be a dict of str→str, else drop.
        raw_cv = cfg.get("matchzy_cvars", {}) or {}
        if isinstance(raw_cv, dict):
            self.matchzy_cvars = {str(k): str(v) for k, v in raw_cv.items()}
        else:
            self.matchzy_cvars = {}
        # v0.10.2 — Discord webhook (see __init__ for prose)
        self.discord_webhook_url         = cfg.get("discord_webhook_url", "")
        # v0.11.0 — Discord bot (Layer 1)
        self.discord_bot_token           = cfg.get("discord_bot_token", "")
        self.discord_guild_id            = cfg.get("discord_guild_id", "")
        self.discord_veto_channel_id     = cfg.get("discord_veto_channel_id", "")
        # v0.11.15 — default voice channel for one-click roster pull
        self.discord_voice_channel_id    = cfg.get("discord_voice_channel_id", "")
        # v0.12.0 — per-team VCs + auto-move toggle (see __init__ for prose)
        self.discord_team_a_voice_channel_id = cfg.get("discord_team_a_voice_channel_id", "")
        self.discord_team_b_voice_channel_id = cfg.get("discord_team_b_voice_channel_id", "")
        self.discord_auto_move_on_distribute_enabled = bool(
            cfg.get("discord_auto_move_on_distribute_enabled", False))
        # v0.12.1 — round summaries (see __init__ for prose)
        self.discord_round_summaries_enabled = bool(
            cfg.get("discord_round_summaries_enabled", False))

        # Workshop command-filter detection results + manual overrides (wid → bool).
        self._cmdfilter_auto       = dict(cfg.get("cmdfilter_auto", {}))
        self._cmdfilter_override   = dict(cfg.get("cmdfilter_override", {}))

        # Persist immediately if we just auto-generated the RCON password so
        # that the next startup (and the server launch) uses the same value.
        if not cfg.get("rcon_password"):
            self.save_config()

    def _load_active_veto_session(self) -> None:
        """v0.11.3 — Resume an in-flight veto session from disk if one
        exists.  Called at AppCore construction (after _load_config).

        Discards the file silently if:
          - The file doesn't exist or can't be parsed
          - The session is older than VETO_ACTIVE_MAX_AGE_SECS (operator
            opened the app the next day, doesn't want yesterday's stale
            finale)
          - The session state is 'idle' (nothing to resume)

        Otherwise loads it into self._veto_session; the SPA's Veto tab
        will render the resumed state on first load, and operator's
        Reset button is the escape hatch if they don't want it."""
        from .config import VETO_ACTIVE_FILE, VETO_ACTIVE_MAX_AGE_SECS
        if not os.path.isfile(VETO_ACTIVE_FILE):
            return
        try:
            with open(VETO_ACTIVE_FILE, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
            if not isinstance(snapshot, dict):
                raise ValueError("snapshot is not a JSON object")
            age = time.time() - float(snapshot.get("updated_at", 0))
            if age > VETO_ACTIVE_MAX_AGE_SECS:
                self.log(f"[veto] active-session file is {age/3600:.1f}h "
                         f"old (cutoff {VETO_ACTIVE_MAX_AGE_SECS/3600:.0f}h); "
                         "discarding without resume")
                try: os.remove(VETO_ACTIVE_FILE)
                except OSError: pass
                return
            sess = self._veto.deserialize_session(snapshot)
            if sess.state == "idle":
                # Nothing meaningful to resume; clean up
                try: os.remove(VETO_ACTIVE_FILE)
                except OSError: pass
                return
            # v0.11.17 B2 — tighter cutoff for sessions PAST the captain-
            # links stage.  The original 12h window made sense for "I
            # built a roster last night, finishing today" but was way too
            # generous for actively-played stages: a session left at
            # `voting`/`veto`/`finale`/`complete` from yesterday's test
            # run would resume today with its captain tokens still live,
            # and yesterday's tunnel URL could hijack today's setup.
            # Sessions in early stages (idle/roster/teams/links) still get
            # the full window; sessions past `links` get only 1 hour.
            _PAST_LINKS = ("voting", "veto", "finale", "complete")
            _PAST_LINKS_MAX_AGE = 3600.0     # 1 hour
            if sess.state in _PAST_LINKS and age > _PAST_LINKS_MAX_AGE:
                self.log(f"[veto] active-session is in state={sess.state} "
                         f"and {age/3600:.1f}h old (past-links cutoff "
                         f"{_PAST_LINKS_MAX_AGE/3600:.1f}h); discarding "
                         "without resume so yesterday's tokens can't "
                         "hijack today's session")
                try: os.remove(VETO_ACTIVE_FILE)
                except OSError: pass
                return
            self._veto_session = sess
            self.log(f"[veto] resumed active session — state={sess.state} "
                     f"mode={sess.mode} age={age/60:.1f}min")
        except Exception as exc:
            # Corrupt file or schema mismatch — start fresh.  Operator gets
            # a log line; the corrupted file is left in place so they can
            # inspect / report.
            self.log(f"[veto] active-session resume failed ({exc}); "
                     "starting with fresh session state")

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
                "guest_pin":             self.guest_pin,
                "flask_port":            self.flask_port,
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
                # v0.10.1 online-primary veto config
                "public_share_url":              self.public_share_url,
                "veto_auto_launch_on_ready":     self.veto_auto_launch_on_ready,
                "matchzy_cvars":                 dict(self.matchzy_cvars),
                # v0.10.2 — Discord webhook
                "discord_webhook_url":           self.discord_webhook_url,
                # v0.11.0 — Discord bot
                "discord_bot_token":             self.discord_bot_token,
                "discord_guild_id":              self.discord_guild_id,
                "discord_veto_channel_id":       self.discord_veto_channel_id,
                # v0.11.15 — default VC for one-click roster pull
                "discord_voice_channel_id":      self.discord_voice_channel_id,
                # v0.12.0 — per-team VCs + auto-move toggle
                "discord_team_a_voice_channel_id":            self.discord_team_a_voice_channel_id,
                "discord_team_b_voice_channel_id":            self.discord_team_b_voice_channel_id,
                "discord_auto_move_on_distribute_enabled":    self.discord_auto_move_on_distribute_enabled,
                # v0.12.1 — round summaries
                "discord_round_summaries_enabled":            self.discord_round_summaries_enabled,
            }
            # Atomic write: serialize via _config_save_lock so concurrent
            # save_config calls don't interleave (Flask is threaded), and use
            # tmp+os.replace so a power-loss / crash mid-write can't leave a
            # truncated file that _load_config would then silently treat as
            # `{}` — wiping every persisted setting and regenerating the RCON
            # password on the next launch.
            with self._config_save_lock:
                tmp = _CONFIG_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
                    f.flush()
                    try:
                        os.fsync(f.fileno())   # durability against power loss
                    except OSError:
                        pass                   # not all FS support fsync
                os.replace(tmp, _CONFIG_FILE)
            self.log(f"Config saved  ({_CONFIG_FILE})")
        except Exception as exc:
            self.log(f"Config save failed: {exc}")

    # ── Config backup / restore (v0.16.0 / task #158) ─────────────────────────
    # Operator-facing safety net for "I just messed up my config" / "the .exe
    # update wrote something weird" / "I want to roll back this plugin install".
    # Backups live under %APPDATA%/Oblivion Server Tool/backups/ with the
    # timestamp + reason in the filename so the SPA picker can show what each
    # snapshot is.  We keep the most recent 10 and prune older ones.

    def _backups_dir(self) -> str:
        return os.path.join(os.path.dirname(_CONFIG_FILE), "backups")

    def backup_config(self, reason: str = "manual") -> dict:
        """Snapshot oblivion_config.json to backups/oblivion_config_<ts>_<reason>.json.
        Returns {filename, path, bytes, reason}.  Quiet no-op when the live
        config file doesn't exist yet (e.g. first run).
        """
        if not os.path.isfile(_CONFIG_FILE):
            return {"filename": "", "path": "", "bytes": 0,
                    "reason": reason, "ok": False, "error": "config file missing"}
        # Sanitise the reason — only [a-z0-9_-] survive so the filename is safe.
        safe_reason = "".join(c if c.isalnum() or c in "-_" else "-"
                              for c in (reason or "manual"))[:40] or "manual"
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_name = f"oblivion_config_{ts}_{safe_reason}.json"
        out_dir = self._backups_dir()
        try:
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, out_name)
            shutil.copy2(_CONFIG_FILE, out_path)
            size = os.path.getsize(out_path)
        except Exception as exc:
            self.log(f"[backup] {reason} → failed: {exc!r}")
            return {"filename": "", "path": "", "bytes": 0,
                    "reason": reason, "ok": False, "error": str(exc)}

        # Prune to the most recent 10.  Sort by mtime descending.
        try:
            entries = []
            for n in os.listdir(out_dir):
                if not n.startswith("oblivion_config_") or not n.endswith(".json"):
                    continue
                p = os.path.join(out_dir, n)
                try:
                    entries.append((os.path.getmtime(p), p))
                except OSError:
                    pass
            entries.sort(reverse=True)
            for _mt, p in entries[10:]:
                try:
                    os.remove(p)
                except OSError:
                    pass
        except Exception:
            pass    # pruning failure is non-fatal — operator can clean by hand

        self.log(f"[backup] {reason} → {out_name} ({size} bytes)")
        return {"filename": out_name, "path": out_path, "bytes": size,
                "reason": reason, "ok": True}

    def list_config_backups(self) -> list[dict]:
        """Return the recent backups newest-first.  Each entry has
        {filename, bytes, mtime_iso, reason}.  reason is parsed from
        the filename suffix."""
        out_dir = self._backups_dir()
        out: list[dict] = []
        if not os.path.isdir(out_dir):
            return out
        for n in os.listdir(out_dir):
            if not n.startswith("oblivion_config_") or not n.endswith(".json"):
                continue
            p = os.path.join(out_dir, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            # Parse: oblivion_config_<ts>_<reason>.json
            stem = n[len("oblivion_config_"):-len(".json")]
            parts = stem.split("_", 2)    # ts_date, ts_time, reason
            reason = parts[2] if len(parts) >= 3 else ""
            out.append({
                "filename":  n,
                "bytes":     st.st_size,
                "mtime":     int(st.st_mtime),
                "mtime_iso": time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(st.st_mtime)),
                "reason":    reason,
            })
        out.sort(key=lambda e: e["mtime"], reverse=True)
        return out

    def restore_config_backup(self, filename: str) -> dict:
        """Atomic restore: take the named backup, snapshot the CURRENT config
        as a 'pre-restore' backup, then swap the backup file into place.
        Caller is expected to reload (re-read config) after — config is read
        once at AppCore() construction, so a full app restart is the cleanest
        UX.  Returns {ok, restored_from, pre_restore_backup}."""
        # Filename safety: must be one of our own backups, NOT an absolute
        # path or anything outside the backups dir.  We resolve through the
        # backups directory to defend against ".." escapes.
        if "/" in filename or "\\" in filename or filename.startswith(".."):
            return {"ok": False, "error": "filename must be a bare basename"}
        backup_dir = self._backups_dir()
        target = os.path.normpath(os.path.join(backup_dir, filename))
        # The normalized path must STILL be inside backup_dir.
        if os.path.commonpath([os.path.realpath(target),
                               os.path.realpath(backup_dir)]) \
                != os.path.realpath(backup_dir):
            return {"ok": False, "error": "filename escapes backup dir"}
        if not os.path.isfile(target):
            return {"ok": False, "error": f"backup {filename!r} not found"}

        # Snapshot the live config first so a botched restore is itself
        # reversible.  Tagged 'pre-restore' so the operator can see what was
        # in place when they hit Restore.
        pre = self.backup_config(reason="pre-restore")

        try:
            tmp = _CONFIG_FILE + ".restore.tmp"
            shutil.copy2(target, tmp)
            with self._config_save_lock:
                os.replace(tmp, _CONFIG_FILE)
            self.log(f"[backup] restored config from {filename!r}; "
                     f"previous saved as {pre.get('filename') or '?'}")
            return {"ok":                   True,
                    "restored_from":        filename,
                    "pre_restore_backup":   pre.get("filename") or ""}
        except Exception as exc:
            self.log(f"[backup] restore failed: {exc!r}")
            return {"ok": False, "error": str(exc)}

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
        # shutil.move (not copy2+rmtree) so a failed remove can't leave the
        # DLL at BOTH paths, which would let MetaMod load the wrong one and
        # silently break plugin discovery on the next launch.
        for fname in dlls:
            src = os.path.join(nested, fname)
            dst = os.path.join(mm_bin, fname)
            try:
                # Pre-remove any existing copy at dst — shutil.move on Windows
                # raises if the destination exists and refuses to overwrite.
                if os.path.isfile(dst):
                    os.remove(dst)
                shutil.move(src, dst)
                moved += 1
            except Exception as exc:
                self.log(f"[pre-launch]   ✗ Could not move {fname}: {exc}")
        try:
            shutil.rmtree(nested)
        except Exception as exc:
            self.log(f"[pre-launch]   ✗ Could not remove nested dir (DLLs already moved): {exc}")
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

    def _list_dedicated_pids(self) -> list[int]:
        """Return PIDs of cs2.exe processes whose command line contains `-dedicated`.

        Used by the pre-launch cleanup to kill stale dedicated servers that
        survived a previous crash and would otherwise hold port 27015.  Uses
        PowerShell's Get-CimInstance first (works on every Windows 10/11 build
        including 24H2 where wmic was removed), falls back to wmic for older
        systems.  Skips the user's CS2 game client (no `-dedicated` arg).
        """
        pids: list[int] = []

        # Strategy 1 — PowerShell Get-CimInstance.  One-line script: filter
        # processes named cs2.exe whose CommandLine mentions -dedicated, print PIDs.
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='cs2.exe'\" | "
            "Where-Object { $_.CommandLine -like '*-dedicated*' } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        ps_failed = False
        try:
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=8,
                # CREATE_NO_WINDOW so the brief PS spawn doesn't flash a console.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if ps.returncode == 0:
                for line in ps.stdout.splitlines():
                    s = line.strip()
                    if s.isdigit():
                        pids.append(int(s))
                return pids   # success path — even an empty list is valid
            ps_failed = True
        except Exception as exc:
            ps_failed = True
            self.log(f"[!] PowerShell process enumeration failed: {exc} — trying wmic")

        # Strategy 2 — wmic fallback (deprecated; missing on Win 11 24H2).
        wmic_failed = False
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
                        pids.append(int(pid))
        except FileNotFoundError:
            wmic_failed = True
            self.log("[!] Neither PowerShell nor wmic available — cannot identify "
                     "stale dedicated cs2.exe.  Manually close any running "
                     "dedicated server before starting.")
        except Exception as exc:
            wmic_failed = True
            self.log(f"[!] wmic process enumeration failed: {exc}")
        # If BOTH strategies failed and we found nothing, the operator gets
        # zero context for why a stale server can't be killed.  Log it loudly.
        if ps_failed and wmic_failed and not pids:
            self.log("[!] Both PowerShell and wmic process enumeration failed — "
                     "stale `cs2.exe -dedicated` (if any) WILL NOT be killed at pre-launch.")
        return pids

    # Port-holder enumeration lives in cs2servergui._netutils as plain
    # module-level functions.  We expose them as instance methods so the
    # caller can use `self._holder_of_port(...)` / `self._listeners_on_port(...)`
    # without importing _netutils, and they get the AppCore logger for free.
    def _holder_of_port(self, port: int) -> tuple[int, str] | None:
        from ._netutils import holder_of_port
        return holder_of_port(port, log=self.log)

    def _listeners_on_port(self, port: int) -> list[tuple[str, int, str]]:
        from ._netutils import listeners_on_port
        return listeners_on_port(port, log=self.log)

    def _resolve_rcon_host(self) -> None:
        """Re-resolve the primary LAN IP and update self.rcon.host accordingly.

        CS2 binds its RCON listener to the LAN IP (not loopback), so we need
        to track which IP the OS would route through.  Refreshing on every
        server start / attach handles DHCP changes, adapter add/remove, and
        the case where the app started before the network was fully up
        (`_lan_ip()` would have returned 127.0.0.1 then).  If the LAN IP turns
        out to be unreachable too, _post_launch_sanity_check will discover the
        actual bind address via netstat and override self.rcon.host.
        """
        try:
            # force_refresh bypasses the 30s state-poll cache — we genuinely
            # need the live value at server start time.
            fresh = _config._lan_ip(force_refresh=True)
        except Exception as exc:
            self.log(f"[rcon] could not re-resolve LAN IP: {exc}")
            return
        # If _lan_ip() couldn't reach 8.8.8.8 it falls back to 127.0.0.1.
        # CS2 doesn't bind RCON to loopback, so writing 127.0.0.1 here would
        # break the very bug v0.9.2 fixed (workshop maps stuck on dust2).
        # Keep the previous value when the fresh probe is the loopback fallback
        # — _post_launch_sanity_check's netstat-based recovery is the actual
        # safety net for when the primary LAN IP doesn't work either.
        if fresh == "127.0.0.1" and _config.RCON_HOST != "127.0.0.1":
            self.log(f"[rcon] _lan_ip() returned loopback fallback "
                     f"(network blip?) — keeping last-known {_config.RCON_HOST}")
            return
        if fresh != _config.RCON_HOST:
            self.log(f"[rcon] LAN IP changed {_config.RCON_HOST} → {fresh} (re-resolving)")
            _config.RCON_HOST = fresh
        if self.rcon.host != fresh:
            self.log(f"[rcon] updating self.rcon.host {self.rcon.host} → {fresh}")
            self.rcon.host = fresh

    def _preflight_checks(self, map_name: str, mode: str,
                          is_workshop: bool) -> tuple[bool, list[str]]:
        """Pre-flight: validate prerequisites before we light up cs2.exe.

        v0.10.2: returns `(ok, errors)` — was just `bool`.  The errors list
        carries every hard-fail reason as a one-line operator-friendly
        string so the HTTP layer can surface them to a remote admin who
        otherwise wouldn't see why their Start click did nothing.  Logs
        still emit as before (for the log drawer + history).

        Hard failures (each becomes one entry in `errors`):
        - Port 27015 already held by a non-cs2.exe process (binding will fail).
        - CS2 not installed (CS2_PATH missing).
        - Mode's plugin source folders missing from the bundle.

        Soft warnings (logged only — not in `errors`):
        - Workshop map but no Steam credentials saved.
        - DepotDownloader missing (auto-downloads on first workshop dl, but warn).
        """
        errors: list[str] = []

        # ── CS2 install present ──────────────────────────────────────────────
        if not os.path.isfile(_config.CS2_PATH):
            msg = f"CS2 is not installed (expected at {_config.CS2_PATH})"
            self.log(f"[preflight] ✗ {msg}")
            self.log("[preflight]   → Config → Server Installation → Install / Reinstall")
            errors.append(msg)

        # ── Port 27015 conflict ──────────────────────────────────────────────
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex(("127.0.0.1", RCON_PORT)) == 0:
                    # Held by SOMETHING. Check if it's our existing cs2.exe
                    # (which the pre-launch cleanup will taskkill) or foreign.
                    tl = subprocess.run(
                        ["tasklist", "/FI", "IMAGENAME eq cs2.exe", "/NH", "/FO", "CSV"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if "cs2.exe" not in tl.stdout.lower():
                        msg = (f"Port {RCON_PORT} is held by a non-CS2 process — "
                               f"close it first (run `netstat -ano | findstr :{RCON_PORT}` "
                               "to identify the owner)")
                        self.log(f"[preflight] ✗ {msg}")
                        errors.append(msg)
        except Exception as exc:
            self.log(f"[preflight] ⚠  Port check failed: {exc}")

        # ── Plugin source folders present in the bundle ─────────────────────
        needed = _MODE_PLUGIN_NAMES.get(mode, [])
        for name in needed:
            src = os.path.join(_PLUGINS_BASE, name)
            if not os.path.isdir(src):
                msg = f"Plugin bundle '{name}' missing — reinstall the app"
                self.log(f"[preflight] ✗ {msg}: {src}")
                errors.append(msg)

        # ── Soft warnings ────────────────────────────────────────────────────
        if is_workshop and not (self.steam_username and self.steam_password):
            self.log("[preflight] ⚠  Workshop map but Steam credentials not saved — "
                     "downloads/refreshes won't work")
        if not os.path.isfile(_config.DEPOTDL_PATH):
            self.log(f"[preflight] ⚠  DepotDownloader missing: {_config.DEPOTDL_PATH} "
                     "(will auto-download on first workshop request)")
        return (not errors), errors

    def start_server(self, map_name: str, mode: str,
                     is_workshop: bool = False) -> None:
        # Clear the stop signal — a previous stop_server may have set it to
        # break a crash-restart backoff sleep.  Without resetting here, the
        # NEXT crash backoff would short-circuit immediately and the operator
        # would lose the cooldown that's supposed to throttle a boot loop.
        self._stop_event.clear()
        # ── Pre-flight checks (port, install, bundle, creds) ─────────────────
        ok, _errors = self._preflight_checks(map_name, mode, is_workshop)
        if not ok:
            self.log("[!] Pre-flight checks failed — fix the issues above and try again.")
            # v0.10.2: store the errors so a remote-admin Start click can
            # surface them in /api/state.boot_error.  Cleared on next successful
            # start or on stop.
            self.last_start_error = "; ".join(_errors) or "Pre-flight checks failed"
            return
        self.last_start_error = ""        # clear stale error on successful start
        # Refresh the LAN IP so RCON connects to the right interface (see
        # _resolve_rcon_host).
        self._resolve_rcon_host()
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
        stale_pids = self._list_dedicated_pids()
        if stale_pids:
            self.log(f"[!] Found {len(stale_pids)} lingering dedicated cs2.exe "
                     f"(PIDs: {', '.join(map(str, stale_pids))}) — killing…")
            killed = 0
            for pid in stale_pids:
                try:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=5)
                    killed += 1
                except Exception as exc:
                    self.log(f"[!]   Could not kill PID {pid}: {exc}")
            if killed:
                time.sleep(1.5)   # give the OS a moment to release port 27015
                self.log(f"[!] Killed {killed} lingering dedicated cs2.exe — proceeding with fresh start")

        # ── Final port check: who (if anyone) holds 27015 right now? ─────────
        # If the port is held by a non-cs2 process, the bind below will fail
        # silently and RCON never comes up.  Surface the holder in the log so
        # the operator can act, rather than spending 90s on "RCON not reachable".
        holder = self._holder_of_port(RCON_PORT)
        if holder:
            self.log(f"[!] Port {RCON_PORT} still held after cleanup: "
                     f"PID {holder[0]} ({holder[1]}). "
                     f"Server bind will fail. Close that process and retry.")


        cmd  = [
            _config.CS2_PATH, "-dedicated",
            # Mirror the full engine console (incl. native crash output) to
            # csgo/console.log.  CSS/MetaMod logs live elsewhere, so without this
            # a native access violation leaves no trace — which is exactly what
            # made the Jailbreak crash hard to diagnose.
            "-condebug",
            "-port",          str(RCON_PORT),
            # Force RCON's TCP socket to bind on ALL interfaces.  Without this,
            # Source 2 picks "first interface Windows resolves as primary",
            # which on a host with Hyper-V / WSL installed is the vEthernet
            # virtual adapter (172.19.x.x).  Result: RCON listens on the WSL
            # NIC, the app tries to reach it via the real LAN IP, and gets
            # WinError 10061 forever.  Binding 0.0.0.0 makes RCON reachable
            # from loopback, LAN, AND vEthernet — strictly more permissive,
            # never worse.  UDP game traffic already binds 0.0.0.0 by default.
            "+ip",            "0.0.0.0",
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
        # CS2 blocks unless launched with this flag.  Added for: (a) workshop maps
        # flagged (auto-detected from the Steam description or a manual override),
        # and (b) modes that MOUNT a workshop content addon — mounting any addon
        # turns on workshop command filtering for the whole session (even on
        # official maps), which would otherwise reject the mode's own convars
        # (e.g. Zombie Escape's zm_enable + all cs2f_*/zr_*/zm_* CVars).
        mode_needs_nofilter = mode in _CMDFILTER_REQUIRED_MODES
        if (is_workshop and self.cmdfilter_effective(map_name)) or mode_needs_nofilter:
            cmd.append("-disable_workshop_command_filtering")
            reason = mode if mode_needs_nofilter else map_name
            self.log("[workshop] Launching with -disable_workshop_command_filtering "
                     f"for {reason}")
        # K4-Arenas ladder bots: force bot_quota_mode "normal" so the bots plugin
        # adds exactly ONE bot to even an odd player count and that bot joins the
        # 1v1 ladder like a player. The default "fill" sets bot_quota 2 — a second,
        # unpaired bot that sticks onto a side as a 2v1. Set at launch for arenas.
        if "arenas" in _MODE_PLUGIN_NAMES.get(mode, []):
            cmd += ["+bot_quota_mode", "normal"]
            self.log("[arenas] bot_quota_mode normal (ladder bots fill odd slots 1v1)")
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
        self.log(f"Polling RCON at {_config.RCON_HOST}:{RCON_PORT} — waiting for server…")
        if self.on_state_change:
            self.on_state_change()
        threading.Thread(target=self._poll_rcon_ready, daemon=True).start()
        threading.Thread(target=self._post_launch_sanity_check, daemon=True).start()

    def _post_launch_sanity_check(self) -> None:
        """Run shortly after Popen — catch immediate cs2.exe death and surface
        WHO is holding 27015 if the port doesn't open within 8 s.

        The default RCON probe waits 90 s before logging anything actionable.
        That's far too late when the real failure is "another process holds
        27015" or "cs2.exe exited code 1 in the first second".  This runs on
        its own thread so it never blocks anything else.
        """
        proc = self.proc
        if proc is None:
            return
        # 1. Did cs2.exe survive the first 3 s? Process-death within seconds
        #    almost always means a missing file, bad arg, or port-bind failure
        #    — not a slow boot.
        time.sleep(3)
        # Re-check running — a user Stop in the first 3 s makes the rest of
        # this function useless (would force-fire host_workshop_map on a
        # server that's about to die).
        if not self.running:
            return
        if proc.poll() is not None:
            self.log(f"[!] cs2.exe exited within 3 s (code {proc.returncode}). "
                     "Open csgo\\console.log for the engine's reason.")
            return
        # 2. Watch for port 27015 to come up over the next ~8 s.  If it never
        #    does, identify the holder so the operator sees the conflict.
        for _ in range(8):
            time.sleep(1)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    if s.connect_ex(("127.0.0.1", RCON_PORT)) == 0:
                        return   # listener up — _poll_rcon_ready will take it from here
            except Exception:
                pass
        # Port still not listening on loopback after ~11 s total.  Diagnose:
        # enumerate every listener for the port and probe each bind address.
        # If we find one that's reachable, switch self.rcon.host to it so RCON
        # commands (and the workshop trigger) start working without a restart.
        listeners = self._listeners_on_port(RCON_PORT)
        if not listeners:
            self.log(f"[!] Port {RCON_PORT} not opening — nothing listening yet. "
                     "cs2.exe may still be initialising, or it failed to bind silently. "
                     "Open csgo\\console.log for clues.")
            return
        # Log every listener so the operator can see exactly what's bound where.
        for addr, pid, name in listeners:
            self.log(f"[!] Port {RCON_PORT}: {addr} held by {name} (PID {pid})")
        # Try each distinct host portion (the bit before the final ':<port>')
        # for a reachable TCP listener.  IPv6 forms like [::]:27015 → "::"
        # and [::1]:27015 → "::1"; IPv4 0.0.0.0:27015 → "0.0.0.0".
        tried: set[str] = set()
        for addr, _pid, _name in listeners:
            host = addr.rsplit(":", 1)[0].strip("[]")
            # 0.0.0.0 and [::] are wildcards — map to loopback for probing.
            probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
            if probe_host in tried:
                continue
            tried.add(probe_host)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    if s.connect_ex((probe_host, RCON_PORT)) == 0:
                        self.log(f"[!] RCON reachable on {probe_host}:{RCON_PORT} — "
                                 f"switching self.rcon.host to it for this session.")
                        self.rcon.host = probe_host
                        # Promote to "ready" — the regular RCON poll is still
                        # banging on the wrong host and would take 90 s to fall
                        # through to its timeout branch.  Atomic flip mirrors
                        # the same pattern _poll_rcon_ready uses to avoid
                        # racing a concurrent stop_server().
                        with self._lifecycle_lock:
                            if self.running and self.boot_state == "booting":
                                self.boot_state    = "ready"
                                self._uptime_start = time.time()
                                became_ready = True
                            else:
                                became_ready = False
                        if became_ready:
                            self.log("Server ready — RCON is responding (via recovery)")
                            if self.on_state_change:
                                self.on_state_change()
                        # Force-fire workshop trigger now if still pending.
                        # Re-check running once more — if a Stop fired during
                        # the lifecycle-lock window above, don't issue the
                        # host_workshop_map to a server that's terminating.
                        if not self.running:
                            return
                        wk = self._pending_workshop_map
                        if wk:
                            self._pending_workshop_map = None
                            try:
                                self.rcon.execute_retry(f"host_workshop_map {wk}")
                                # Lock around current_map mutation — _poll_rcon_ready
                                # may also be writing this field after its own RCON
                                # success.
                                with self._lifecycle_lock:
                                    self.current_map = wk
                                self.log(f"[workshop] Workshop map {wk} loaded via "
                                         f"recovered host {probe_host} ✓")
                            except Exception as exc:
                                self.log(f"[workshop] Recovered-host load failed: {exc}")
                        return
            except Exception as exc:
                self.log(f"[!]   probe {probe_host}:{RCON_PORT} → {exc}")
        self.log(f"[!] No reachable RCON address found among the {len(listeners)} "
                 "listener(s).  Likely Windows Firewall blocking loopback/LAN "
                 "to the bind interface, or RCON listener silently failed.")

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
            # v0.10.2: clear any stale preflight-error from the previous failed
            # Start.  The /api/state.boot_error field only shows fresh errors.
            self.last_start_error = ""
        # Wake any crash-restart backoff sleeping on `_stop_event` so a
        # user-initiated Stop during a 5/15/45s delay cancels the restart
        # instead of being silently overridden by the timer firing later.
        self._stop_event.set()
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

        Refreshes RCON_HOST first (see _resolve_rcon_host) so the probe uses
        the current LAN IP, not whatever config.py resolved at import time.

        Workflow:
          1. tasklist — fast OS check; bail immediately if cs2.exe isn't there.
          2. RCON status — confirm it's our server and pull the current map.
          3. If both succeed → mark running/ready and fire on_state_change.

        Runs on a daemon thread so it never blocks the UI.
        """
        def _do() -> None:
            # Refresh the LAN IP before RCON polls (see _resolve_rcon_host).
            self._resolve_rcon_host()
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
            # All lifecycle mutations under the lock so a concurrent stop_server
            # (which sets running=False / boot_state=offline) can't interleave
            # between these two writes and leave us in {running=True,
            # boot_state=offline}.  Also stamp _uptime_start so the UI shows a
            # non-zero uptime for the reattached server.
            with self._lifecycle_lock:
                self.running       = True
                self.boot_state    = "ready"
                self._uptime_start = time.time()

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
            # Snapshot proc once — a concurrent stop_server clears self.proc to
            # None between the two reads below, which would AttributeError on
            # the .poll() call.  Snapshot keeps the timeout branch consistent.
            proc_snap = self.proc
            if elapsed >= 90 and proc_snap is not None and proc_snap.poll() is None:
                with self._lifecycle_lock:
                    # Recheck running under the lock — a stop_server racing
                    # the timeout would otherwise revive `boot_state=ready`
                    # for a server that was just shut down.
                    if not self.running or self.boot_state != "booting":
                        return
                    self.boot_state    = "ready"
                    self._uptime_start = time.time()
                self.log(
                    "Server marked ONLINE after 90 s "
                    "(RCON TCP unreachable — check Windows Firewall for port 27015)"
                )
                # Best-effort: even though probe.connect timed out, RCON may
                # still answer on a different path (loopback vs LAN IP edge
                # cases).  If we have a pending workshop map, TRY to load it
                # via RCON anyway — silent failure is better than silently
                # leaving the server on the de_dust2 placeholder.
                wk = self._pending_workshop_map
                if wk:
                    self._pending_workshop_map = None
                    try:
                        self.rcon.execute_retry(f"host_workshop_map {wk}")
                        # Lock around the write — _post_launch_sanity_check
                        # writes the same field under the lock; v0.9.2.1 makes
                        # this path consistent (was bare assign).
                        with self._lifecycle_lock:
                            self.current_map = wk
                        self.log(f"[workshop] Workshop map {wk} loaded via fallback ✓")
                    except Exception as exc:
                        self.log(f"[workshop] Fallback auto-load failed: {exc}")
                if self.on_state_change:
                    self.on_state_change()
                return
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.settimeout(2)
                    probe.connect((_config.RCON_HOST, RCON_PORT))
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
                            # Lock the write for consistency with the sanity-
                            # check + fallback paths (v0.9.2.1).
                            with self._lifecycle_lock:
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
            # Snapshot lifecycle state under the lock so a concurrent stop_server
            # can't flip running=False between the check and _restart_into
            # (which would kick off a restart against a server the operator
            # just shut down).
            with self._lifecycle_lock:
                running_snap = self.running
                current_mode_snap = self.current_mode
            mode_changed     = (mode != current_mode_snap)
            new_managed      = _MODE_PLUGIN_NAMES.get(mode, [])
            old_managed      = _MODE_PLUGIN_NAMES.get(current_mode_snap, [])
            plugins_involved = bool(new_managed or old_managed)

            # A mode change that adds/removes/swaps plugins is done as a clean
            # restart: deploy_plugins() then runs while the server is offline, so
            # no loaded CSS DLL can be file-locked (Windows), the old mode's
            # plugins can't reload alongside the new ones, and MetaMod modes load
            # at boot.  Same-mode map changes and vanilla↔vanilla switches (no
            # plugins on either side) stay live below.
            if mode_changed and plugins_involved and running_snap:
                self.log(f"[{caller}] Mode change {current_mode_snap} → {mode}: "
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
                # Lock the writes for consistency with the other sites
                # (v0.9.2.1 — was bare assigns).
                with self._lifecycle_lock:
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
            except urllib.error.HTTPError as he:
                # v0.10.2: private GitHub repo returns 404 to anonymous API
                # calls.  Quietly suppress THAT specific case — the badge
                # would never fire anyway, and a noisy log line every
                # update-check interval is just clutter.  Other HTTP errors
                # (rate limit, transient 5xx) still log so we can debug.
                if he.code == 404:
                    return    # repo private OR no releases yet — quietly stop
                self.log(f"App update check skipped: HTTP {he.code}: {he.reason}")
            except Exception as exc:
                # Silently swallow — offline / DNS / etc.
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
                # Run steamcmd in its OWN console window rather than capturing its
                # output into the app. A standalone console lets steamcmd self-update
                # cleanly (the captured-pipe path is what triggers the "exit code 8"
                # self-update failure and no-output hangs) and shows native progress.
                # We still hold the process handle and wait on it, so the app detects
                # completion and re-verifies the build afterwards.
                proc = subprocess.Popen(
                    [_config.STEAMCMD_PATH,
                     "+login", "anonymous",
                     "+app_update", CS2_APP_ID, "validate", "+quit"],
                    creationflags=(subprocess.CREATE_NEW_CONSOLE
                                   if sys.platform == "win32" else 0),
                )
                self.log("  steamcmd is running in its own window — watch progress there.")
                self.log("  (Standalone so it can self-update cleanly; the app will verify")
                self.log("   the build automatically once the window finishes & closes.)")
                start = time.time()
                next_mark = 30
                while proc.poll() is None:
                    time.sleep(1)
                    if time.time() - start >= next_mark:
                        self.log(f"  … still updating ({next_mark}s) — see the steamcmd window")
                        next_mark += 30
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
        """Return the game/csgo/ directory (parent of addons/).

        v0.13.1 / task #86 — first method migration.  The real
        implementation now lives on ``CS2Driver.install_root()``; this
        is a thin backward-compat shim so the dozens of existing call
        sites in this file keep working unchanged.  New code should
        call ``self.driver.install_root(self)`` directly.  Each call
        site migrates over time; when the last one switches over the
        shim disappears.  See PLATFORM.md § 5 for the migration
        pattern.
        """
        return self.driver.install_root(self)

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

    def _validate_bundle_configs(self, deployed: list[str], csgo_dir: str) -> None:
        """Warn when a plugin ships only a *.example config without an active copy.

        Background: the Zombie weapons whitelist (`configs/zm/weapons.cfg`) was
        only present as `weapons.cfg.example` in the bundle, so the plugin
        loaded with NO weapons allowed and gun pickup silently broke. This
        sweep walks each deployed plugin's bundle folder, finds every
        ``*.example``/``*.example.<ext>`` file, computes the active path it
        implies, and warns if the active file exists in neither the bundle
        nor the live csgo/ tree.

        Warning-only — does not block startup or fail the deploy.
        """
        for name in deployed:
            src_base = os.path.join(_PLUGINS_BASE, name)
            if not os.path.isdir(src_base):
                continue
            for root, _dirs, files in os.walk(src_base):
                for fname in files:
                    lower = fname.lower()
                    # Match ".example" or ".example.<ext>" anywhere in the name.
                    if ".example" not in lower:
                        continue
                    # Compute the active filename: strip the ".example" segment.
                    parts = fname.split(".")
                    try:
                        idx = [p.lower() for p in parts].index("example")
                    except ValueError:
                        continue
                    active_name = ".".join(parts[:idx] + parts[idx + 1:])
                    if not active_name:
                        continue
                    # Check the bundle first, then the live deploy target.
                    bundle_active = os.path.join(root, active_name)
                    rel = os.path.relpath(root, src_base)
                    live_active = os.path.join(csgo_dir, rel, active_name) \
                        if rel != "." else os.path.join(csgo_dir, active_name)
                    if os.path.isfile(bundle_active) or os.path.isfile(live_active):
                        continue
                    rel_disp = os.path.relpath(
                        os.path.join(root, active_name), src_base)
                    self.log(f"[plugins] ⚠  {name}: '{rel_disp}' is missing "
                             f"(only '{fname}' shipped) — plugin may run with defaults")

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

    def _apply_arena_size(self, csgo_dir: str, mode: str) -> None:
        """Write the K4-Arenas round config for the deployed Arena mode.

        K4-Arenas picks rounds from its ``round-settings`` list; each round's
        ``TeamSize`` is the arena size, and when the list is non-empty the plugin
        *replaces* its built-in rounds with it (Plugin.cs: RoundSettings.Count > 0
        → ClearRoundTypes + AddRoundType per entry).

        We generate an **explicit-weapon** rotation so BOTH players in a duel get
        the *same exact gun* each round.  The plugin's default rounds use per-player
        weapon *preferences* (``UsePreferredPrimary`` + a ``PrimaryPreference``
        category), which can hand the two opponents different guns within the same
        category (e.g. AK vs M4).  Setting an explicit ``PrimaryWeapon`` with no
        preference gives an identical loadout to both — and explicit strings also
        dodge any enum-serialisation mismatch that would make the plugin reject +
        regenerate the config.

        Sizes: ``1v1`` → ``TeamSize 1``, ``2v2`` → ``TeamSize 2``.  Any other mode
        clears the config so the plugin's own defaults apply.
        """
        cfg_path = os.path.join(
            csgo_dir, "addons", "counterstrikesharp", "configs",
            "plugins", "K4-Arenas", "K4-Arenas.json")

        sizes = {"1v1": 1, "2v2": 2}
        team_size = sizes.get(mode)
        if team_size is None:
            # Not a generated-config arena mode — clear any stale config so the
            # plugin's defaults apply.
            if os.path.isfile(cfg_path):
                try:
                    os.remove(cfg_path)
                    self.log("[plugins]   arenas: cleared generated config")
                except Exception as exc:
                    self.log(f"[plugins]   arenas: could not clear config: {exc}")
            return

        ts = team_size
        # Identical guns for both sides each round (explicit weapon, no preference).
        # Classic 1v1 ladder rotation — rifles (AK + M4 for variety), AWP, Scout,
        # pistol, Deagle, knife. SMG and shotgun are intentionally OMITTED: in a
        # 1v1 arena they feel out of place vs the skill-based rifle/sniper/pistol
        # progression players expect.
        rounds = [
            {"TranslationName": "k4.rounds.rifle",   "TeamSize": ts,
             "PrimaryWeapon": "weapon_ak47",  "SecondaryWeapon": "weapon_deagle",
             "Armor": True, "Helmet": True},
            {"TranslationName": "k4.rounds.rifle",   "TeamSize": ts,
             "PrimaryWeapon": "weapon_m4a1",  "SecondaryWeapon": "weapon_deagle",
             "Armor": True, "Helmet": True},
            {"TranslationName": "k4.rounds.awp",     "TeamSize": ts,
             "PrimaryWeapon": "weapon_awp",   "SecondaryWeapon": "weapon_deagle",
             "Armor": True, "Helmet": True},
            {"TranslationName": "k4.rounds.scout",   "TeamSize": ts,
             "PrimaryWeapon": "weapon_ssg08", "SecondaryWeapon": "weapon_deagle",
             "Armor": True, "Helmet": True},
            {"TranslationName": "k4.rounds.pistol",  "TeamSize": ts,
             "SecondaryWeapon": "weapon_usp_silencer",
             "Armor": True, "Helmet": False},
            {"TranslationName": "k4.rounds.deagle",  "TeamSize": ts,
             "SecondaryWeapon": "weapon_deagle",
             "Armor": False, "Helmet": False},
            {"TranslationName": "k4.rounds.knife",   "TeamSize": ts,
             "Armor": False, "Helmet": False},
        ]
        # Full config mirroring the plugin's defaults (all sections present so the
        # plugin doesn't treat it as incomplete) with round-settings overridden.
        # Weapon preferences are disabled — every round is a fixed, identical loadout.
        cfg = {
            "use-predefined-config": True,
            "database-settings": {
                "host": "localhost", "username": "root", "database": "database",
                "password": "password", "port": 3306, "sslmode": "preferred",
                "table-prefix": "", "table-purge-days": 30,
            },
            "command-settings": {
                "gun-pref-commands": ["guns", "gunpref", "weaponpref"],
                "round-pref-commands": ["rounds", "roundpref"],
                "queue-commands": ["queue"], "afk-commands": ["afk"],
                "challenge-commands": ["challenge", "duel"],
                "challenge-accept-commands": ["caccept", "capprove"],
                "challenge-decline-commands": ["cdecline", "cdeny"],
                "center-menu-mode": True, "center-announce-mode": True,
                "freeze-in-center-menu": True, "show-menu-credits": True,
            },
            "round-settings": rounds,
            "compatibility-settings": {
                "force-arena-clantags": False,
                "block-flash-of-not-opponent": False,
                "block-damage-of-not-opponent": False,
                "give-knife-by-default": True, "disable-clantags": False,
                "prevent-draw-rounds": True,
            },
            "default-weapon-settings": {
                "default-rifle": None, "default-sniper": None, "default-smg": None,
                "default-lmg": None, "default-shotgun": None, "default-pistol": None,
                "default-round": "k4.rounds.rifle",
            },
            "allowed-weapon-prefs": {
                "rifle": False, "sniper": False, "smg": False, "lmg": False,
                "shotgun": False, "pistol": False,
            },
            "ConfigVersion": 10,
        }
        try:
            os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
            with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(cfg, fh, indent=2)
            self.log(f"[plugins]   arenas: wrote {mode} round config "
                     f"(identical guns both sides, TeamSize {ts})")
        except Exception as exc:
            self.log(f"[plugins]   arenas: could not write arena config: {exc}")

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

        # Arenas: set the arena size for this mode (1v1 default vs generated 2v2).
        if "arenas" in new_plugins:
            self._apply_arena_size(csgo_dir, mode)

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

        # ── 3b. Validate bundled plugin configs ───────────────────────────────
        # Catches the Zombie-weapons.cfg bug (and similar): a *.example file
        # shipped without an active counterpart, so the plugin silently runs
        # with no whitelist.  Warning-only — does not block startup.
        self._validate_bundle_configs(actually_deployed, csgo_dir)

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

    # ── Offline mode-switch + deploy ──────────────────────────────────────────
    # v0.13.2 / task #92 — Plugin Manager actions.  Called from the
    # /api/plugins/activate + /api/plugins/vanilla endpoints, which
    # have already verified the server is stopped.
    #
    # change_map() is the canonical mode-switch path BUT it requires a
    # running server and routes through RCON.  This is the offline
    # equivalent: same lock discipline, same plugin-deploy step, no
    # RCON.  The next /api/server/start will boot with the new mode +
    # plugins already on disk.
    # v0.16.0 / task #158 — auto-backup config before any plugin deploy
    # so an operator can roll back if a deploy goes sideways.
    def set_offline_mode_and_deploy_with_backup(self, mode: str,
                                                  caller: str = "plugin-tab",
                                                  map_name: str | None = None) -> dict:
        """Wrap set_offline_mode_and_deploy with a pre-action config backup."""
        self.backup_config(reason=f"pre-deploy-{mode.replace(' ', '-')}")
        return self.set_offline_mode_and_deploy(mode, caller, map_name)

    def set_offline_mode_and_deploy(self, mode: str, caller: str = "plugin-tab",
                                     map_name: str | None = None) -> dict:
        """Stage a new mode for the next server start, deploying its
        plugins synchronously.  Optionally stage a map at the same time.

        Returns ``{"ok": bool, "mode": str, "map": str, "plugins": list[str],
        "error": str | None}``.  ``deploy_plugins`` already logs its
        own progress to the log drawer.

        ``map_name`` is set under ``_lifecycle_lock`` after a successful
        deploy, matching ``change_map``'s discipline.  Validated against
        ``MODE_MAPS[mode]`` so a pack can't stage an off-mode map.
        """
        if self.running:
            return {"ok": False, "error": "server is running",
                    "mode": self.current_mode or "",
                    "map":  self.current_map or "", "plugins": []}

        if mode not in MODE_SETTINGS:
            return {"ok": False, "error": f"unknown mode {mode!r}",
                    "mode": self.current_mode or "",
                    "map":  self.current_map or "", "plugins": []}

        # Validate map against mode (None means "any workshop map" — Jailbreak).
        if map_name:
            mode_pool = MODE_MAPS.get(mode)
            if mode_pool is not None and map_name not in mode_pool:
                return {"ok": False,
                        "error": f"map {map_name!r} not in {mode!r} pool",
                        "mode": self.current_mode or "",
                        "map":  self.current_map or "", "plugins": []}

        with self._lifecycle_lock:
            # Re-check inside the lock — a concurrent /api/server/start
            # could have flipped running to True between the call and now.
            if self.running:
                return {"ok": False, "error": "server is running",
                        "mode": self.current_mode or "",
                        "map":  self.current_map or "", "plugins": []}

            prev_mode = self.current_mode or "(none)"
            self.log(f"[{caller}] Staging mode {prev_mode} → {mode}…")

            try:
                ok = self.deploy_plugins(mode)
            except Exception as exc:
                self.log(f"[{caller}] deploy_plugins({mode}) raised: {exc!r}")
                return {"ok": False, "error": f"deploy raised: {exc}",
                        "mode": self.current_mode or "",
                        "map":  self.current_map or "", "plugins": []}

            if not ok:
                # deploy_plugins() logs the reason; don't blow away the
                # current_mode on failure — operator can retry.
                return {"ok": False, "error": "deploy_plugins returned False",
                        "mode": self.current_mode or "",
                        "map":  self.current_map or "", "plugins": []}

            # Only after a successful deploy do we mark this as the active mode.
            self.current_mode = mode
            if map_name:
                self.current_map = map_name
                self.log(f"[{caller}] Staged map {map_name} for next start.")
            self.log(f"[{caller}] Active mode is now {mode}.  Next start will "
                     "boot with these plugins.")

            return {
                "ok":      True,
                "mode":    mode,
                "map":     self.current_map or "",
                "plugins": list(_MODE_PLUGIN_NAMES.get(mode, [])),
                "error":   None,
            }

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
        # Snapshot AND clear under the lock so two near-simultaneous Cancel
        # clicks can't both grab the same proc, and so a click between
        # "worker finished" and "next download started" doesn't kill the
        # NEW download.  The worker thread's finally block re-clears
        # _active_dl_proc under the same lock.
        with self._dl_lock:
            proc = self._active_dl_proc
            if proc is None:
                self.log("No download in progress")
                return
            # Clear progress + the proc handle here so a state poll can't
            # briefly re-show the bar before the worker's finally runs.
            self._dl_progress    = {}
            self._active_dl_proc = None
        # `proc` may be the `True` sentinel reservation set by
        # workshop_download (web.py) when a cancel races a brand-new
        # download before the worker has had a chance to swap in its real
        # Popen handle.  In that case there's no process to terminate yet —
        # the reservation clear above is enough; the worker will see
        # _active_dl_proc=None on its first lock acquire and bail.
        if not hasattr(proc, "terminate"):
            self.log("Cancelling download — pre-spawn reservation cleared")
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

                # Lock the assign so a concurrent cancel_download / workshop_download
                # 409-check sees a consistent value (v0.9.2.1 hotfix — the v0.9.2 fix
                # for the workshop-download race only locked cancel, leaving the
                # worker's assign + clear unlocked, so two clicks could both observe
                # None and both spawn workers).
                # ALSO check the reservation hasn't been cancelled: web.py
                # reserves _active_dl_proc with a `True` sentinel before we get
                # here.  If cancel_download ran in the meantime it cleared the
                # reservation to None — at which point we need to terminate the
                # process we just spawned and bail, otherwise the user sees
                # "Cancel" do nothing and a runaway steamcmd in the background.
                with self._dl_lock:
                    if self._active_dl_proc is None:
                        cancelled_pre_spawn = True
                    else:
                        cancelled_pre_spawn = False
                        self._active_dl_proc = proc
                if cancelled_pre_spawn:
                    self.log("  Cancelled before spawn — terminating new process.")
                    try: proc.terminate(); proc.wait(timeout=5)
                    except Exception:
                        try: proc.kill()
                        except Exception: pass
                    return

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
                # Lock the clear — see the assign at 2884.  v0.9.2.1.
                with self._dl_lock:
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
        _MAX_RESTARTS         = 3
        _STABLE_RESET_SECONDS = 300   # 5 min stable → forgive prior crash burst
        _BACKOFFS             = (5, 15, 45)   # exponential restart delay
        _restart_count    = 0
        _last_crash_mono  = 0.0

        def _handle_crash(exit_code: int | None = None) -> None:
            """Shared teardown + optional auto-restart logic.

            Restart counter is forgiving: if the previous crash was more than
            ``_STABLE_RESET_SECONDS`` ago (i.e. the server ran cleanly for
            5+ minutes after the last auto-restart), the counter resets so a
            long-running session can absorb a fresh burst of failures without
            being locked out by ancient history.

            Backoff between attempts is exponential (5 s → 15 s → 45 s) so a
            persistent boot-loop config bug isn't hammered, and the operator
            has a chance to intervene before the limit is hit.
            """
            nonlocal _restart_count, _last_crash_mono
            now = time.monotonic()
            if _restart_count and (now - _last_crash_mono) > _STABLE_RESET_SECONDS:
                self.log(
                    f"[monitor] Server was stable for "
                    f"{int(now - _last_crash_mono)} s — resetting restart counter."
                )
                _restart_count = 0
            _last_crash_mono = now

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
                delay = _BACKOFFS[min(_restart_count, len(_BACKOFFS) - 1)]
                _restart_count += 1
                self.log(
                    f"[!] Auto-restart #{_restart_count}/{_MAX_RESTARTS} "
                    f"in {delay} s…"
                )
                # Event.wait so a user Stop during the backoff cancels the
                # restart immediately instead of being silently queued behind
                # the sleep.  Returns True if Stop was requested.
                if self._stop_event.wait(timeout=delay):
                    self.log("[!] Auto-restart cancelled — Stop requested during backoff.")
                    _restart_count = 0
                    return
                # Re-check the event AFTER the wait too (v0.9.2.1): Stop
                # pressed in the tiny window between wait-returning-False and
                # start_server's clear() would otherwise be swallowed by the
                # clear and the unwanted respawn would proceed.
                if self._stop_event.is_set():
                    self.log("[!] Auto-restart cancelled — Stop requested at wait-edge.")
                    _restart_count = 0
                    return
                self.log(
                    f"[!] Restarting server — "
                    f"map: {self.current_map}  mode: {self.current_mode}"
                )
                wk = self.current_map.isdigit()
                self.start_server(self.current_map, self.current_mode,
                                  is_workshop=wk)
            elif _restart_count >= _MAX_RESTARTS:
                self.log(
                    f"[!] Auto-restart limit reached "
                    f"({_MAX_RESTARTS} consecutive failures). "
                    "Check the recent server log for the root cause "
                    "(missing plugin, port conflict, bad map, etc.) "
                    "before starting again. Counter resets after a manual Start."
                )
                _restart_count   = 0
                _last_crash_mono = 0.0   # don't let next crash hit the stable-
                                         # reset branch with this stale value
                                         # (would log a misleading "stable" line)

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
