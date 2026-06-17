"""
config.py — all paths, constants, and game-data tables.

This is the single place to edit when you move steamcmd, change the RCON
password, add game modes, etc.  Nothing here imports from other project
modules, so it can always be imported safely without side-effects.
"""
from __future__ import annotations

import os
import socket
import sys


# ── LAN IP helper ──────────────────────────────────────────────────────────────

# Cache for _lan_ip() — the primary LAN IP rarely changes within a session
# but is queried on every /api/state poll (every 2s × connected clients), and
# each call does a fresh UDP socket + connect() that can stall on a flapping
# VPN/virtual adapter.  30-second TTL is more than fine for an IP that
# basically never changes; AppCore._resolve_rcon_host can force-refresh by
# calling _lan_ip(force_refresh=True) on every server start/attach.
_LAN_IP_CACHE: dict[str, object] = {"value": "", "ts": 0.0}
_LAN_IP_TTL_SECS = 30.0


def _lan_ip(force_refresh: bool = False) -> str:
    """Return the machine's primary LAN IP (cached, 30 s TTL).

    CS2 dedicated server binds its RCON TCP socket to the LAN IP, not
    127.0.0.1.  Opening a UDP socket toward an external host (no data sent)
    asks the OS which source IP it would route through.

    Cached because /api/state polls this every 2 s per client; without the
    cache, a Hyper-V/Docker/VPN tap adapter flapping briefly serialises every
    state poll behind a routing-table lookup.  Pass force_refresh=True from
    code paths that genuinely need the live value (e.g. server start).
    """
    import time as _time
    if not force_refresh:
        cached = _LAN_IP_CACHE.get("value", "")
        if cached and (_time.monotonic() - float(_LAN_IP_CACHE["ts"])) < _LAN_IP_TTL_SECS:
            return str(cached)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)  # don't hang for >500 ms on a wedged adapter
            s.connect(("8.8.8.8", 80))
            value = s.getsockname()[0]
    except Exception:
        value = "127.0.0.1"
    _LAN_IP_CACHE["value"] = value
    _LAN_IP_CACHE["ts"]    = _time.monotonic()
    return value


# ── Paths ──────────────────────────────────────────────────────────────────────
# All paths derive from CS2_SERVER_DIR (the folder where steamcmd.exe lives).
# Call update_paths() at startup after reading server_dir from config so that
# every module that imported these constants sees the updated values.

CS2_APP_ID          = "730"
DEPOTDL_RELEASE_URL = (
    "https://api.github.com/repos/SteamRE/DepotDownloader/releases/latest"
)

# ── App self-update ────────────────────────────────────────────────────────────
# Bump APP_VERSION before each release tag, then push and create a GitHub
# release tagged "v<APP_VERSION>" — all connected clients will see the update.
APP_VERSION      = "0.16.15"

# ── Plugin registry (v0.15 slice 2) ────────────────────────────────────────────
# Where the community plugin catalog lives.  Repo: OblivionPluginRegistry.
# Pointed at GitHub raw so the app reads the file directly without a build step.
# If the URL changes (rename, move to another mirror), bump this and ship —
# the app will fall back to its last cached catalog while operators upgrade.
#
# When the registry repo doesn't exist yet (pre-launch), the fetch step
# returns an empty catalog gracefully so the SPA's "Available to install"
# section is empty rather than showing a network error.
OBLIVION_REGISTRY_URL = (
    "https://raw.githubusercontent.com/oblivion-systems/"
    "OblivionPluginRegistry/main/catalog.json"
)
# Catalog cache lives next to oblivion_config.json so it persists across
# app restarts and survives in-place upgrades.  TTL = 24 h — refreshed
# on-demand via "Refresh registry" button (added below) or on startup if
# the cache is missing/expired.
REGISTRY_CACHE_TTL_SECONDS = 86400
# Hard ceiling on registry-served plugin download size.  A hostile or
# misconfigured catalog can't drain disk / OOM the app this way.  50 MiB
# is comfortably above every bundled plugin's footprint (warcraft is the
# biggest at ~3 MB) but small enough that a malicious entry can't ship
# a hundred-MB payload.
REGISTRY_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
REGISTRY_FETCH_TIMEOUT_SECONDS = 12   # full fetch (connect + read)

# ─── Runtime install (v0.16.5 / task #163) ────────────────────────────────────
# Auto-install MetaMod + CounterStrikeSharp into the operator's CS2 server
# from the Plugins tab modal.  Without this, the operator had to download
# both zips manually and extract the addons/ folder by hand — the most
# common stuck-point for a new operator setting up tournament mode.
#
# URLs point at known-good stable builds.  Operators can override either
# via `oblivion_config.json` (keys: "metamod_download_url",
# "css_download_url") when a newer build lands before we ship an update.
#
# MetaMod's "latest snapshot" URL pattern: alliedmods publishes per-build
# zips under mms.alliedmods.net.  Pin a known-stable git build rather
# than a moving "latest" symlink — the friend benefits from reproducibility.
#
# CSS picks the "with-runtime" flavour: ships a bundled .NET 8 runtime so
# the operator doesn't need a separate .NET install.  ~150 MB unpacked.
RUNTIME_METAMOD_DEFAULT_URL = (
    "https://mms.alliedmods.net/mmsdrop/2.0/mmsource-2.0.0-git1402-windows.zip"
)
RUNTIME_CSS_DEFAULT_URL = (
    "https://github.com/roflmuffin/CounterStrikeSharp/releases/download/"
    "v1.0.369/counterstrikesharp-with-runtime-windows-1.0.369.zip"
)
# Runtime zips are larger than regular plugin zips (CSS with-runtime is ~150 MB
# unpacked), so the registry's 50 MB cap won't fit.  Use a 250 MB ceiling +
# 90s timeout — comfortably above today's known builds.
RUNTIME_MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
RUNTIME_FETCH_TIMEOUT_SECONDS = 90

APP_REPO         = "oblivion-systems/OblivionServerTool"
APP_RELEASES_URL = f"https://github.com/{APP_REPO}/releases/latest"
APP_API_URL      = f"https://api.github.com/repos/{APP_REPO}/releases/latest"

# Defaults — overridden at runtime by AppCore.update_server_dir()
CS2_SERVER_DIR = r"D:\steamcmd"
STEAMCMD_PATH  = os.path.join(CS2_SERVER_DIR, "steamcmd.exe")
CS2_PATH       = os.path.join(CS2_SERVER_DIR, "steamapps", "common",
                               "Counter-Strike Global Offensive",
                               "game", "bin", "win64", "cs2.exe")
WORKSHOP_DIR   = os.path.join(CS2_SERVER_DIR, "steamapps", "workshop", "content", "730")
DEPOTDL_PATH   = os.path.join(CS2_SERVER_DIR, "depotdownloader", "DepotDownloader.exe")
CS2_ADDONS_DIR = os.path.join(CS2_SERVER_DIR, "steamapps", "common",
                               "Counter-Strike Global Offensive",
                               "game", "csgo", "addons")


def update_paths(server_dir: str) -> None:
    """Recompute all path constants from a new server base directory.

    Must be called before any module uses the path constants (i.e. right after
    loading the config, before starting the server or any downloads).
    """
    global CS2_SERVER_DIR, STEAMCMD_PATH, CS2_PATH, WORKSHOP_DIR, \
           DEPOTDL_PATH, CS2_ADDONS_DIR
    CS2_SERVER_DIR = server_dir
    STEAMCMD_PATH  = os.path.join(server_dir, "steamcmd.exe")
    CS2_PATH       = os.path.join(server_dir, "steamapps", "common",
                                   "Counter-Strike Global Offensive",
                                   "game", "bin", "win64", "cs2.exe")
    WORKSHOP_DIR   = os.path.join(server_dir, "steamapps", "workshop",
                                   "content", "730")
    DEPOTDL_PATH   = os.path.join(server_dir, "depotdownloader",
                                   "DepotDownloader.exe")
    CS2_ADDONS_DIR = os.path.join(server_dir, "steamapps", "common",
                                   "Counter-Strike Global Offensive",
                                   "game", "csgo", "addons")


# ── Config file ────────────────────────────────────────────────────────────────
# Frozen (installed): %APPDATA%\Oblivion Server Tool\  — always user-writable,
#   survives upgrades, and is never inside the read-only Program Files directory.
# Dev mode: project root (one level above this file).

if getattr(sys, "frozen", False):
    _APP_DIR = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")),
        "Oblivion Server Tool",
    )
    os.makedirs(_APP_DIR, exist_ok=True)
else:
    _APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_CONFIG_FILE = os.path.join(_APP_DIR, "oblivion_config.json")
# v0.10.2: match history persists the last N completed veto sessions so the
# operator has a "what did we play last week" reference + can rematch from
# history if the in-memory state was lost (e.g. app restart between matches).
MATCH_HISTORY_FILE = os.path.join(_APP_DIR, "oblivion_matches.json")
MATCH_HISTORY_KEEP = 10        # how many recent matches to retain on disk

# v0.16.1 / task #160 — Persistent team profiles.  Operators running recurring
# tournaments save rosters under a name (e.g. "Cobras") + reuse across sessions
# instead of re-pasting 10 SteamIDs every week.  Each entry is a dict:
#   {name, players: [{name, steam_id, discord_id}], created_at, updated_at}
# Stored as a top-level array.
TEAMS_FILE = os.path.join(_APP_DIR, "oblivion_teams.json")

# v0.16.1 / task #169 — Tournament templates.  A named bundle of mode + map
# pool + Discord channel + plugin pack + (optionally) two team IDs from
# TEAMS_FILE.  One click stages everything for a recurring tournament format.
TEMPLATES_FILE = os.path.join(_APP_DIR, "oblivion_templates.json")

# v0.11.3 — Active veto session persists across app restarts so an accidental
# Ctrl+Q / Windows update / crash mid-veto doesn't evaporate the captains'
# claimed tokens + partial ban/pick sequence.  Snapshot written on every state
# mutation; loaded on AppCore startup; deleted on /api/veto/reset.  Sessions
# older than VETO_ACTIVE_MAX_AGE_SECS are discarded on load (operator opened
# the app the next day, doesn't want yesterday's stale finale).
VETO_ACTIVE_FILE          = os.path.join(_APP_DIR, "oblivion_veto_active.json")
VETO_ACTIVE_MAX_AGE_SECS  = 12 * 3600      # 12h cutoff for resume-on-load


def _load_int_from_config(key: str, default: int) -> int:
    """Read a single integer setting from oblivion_config.json at import time.

    Used so module-level constants (FLASK_PORT) can reflect the user's saved value
    *before* main.py imports them at the top of the file.  Any error (file absent,
    malformed JSON, missing/invalid key) silently falls back to the default — this
    must never block startup, since the config file is auto-created on first run.
    """
    try:
        import json
        with open(_CONFIG_FILE, encoding="utf-8") as fh:
            v = json.load(fh).get(key, default)
        return int(v) if 1 <= int(v) <= 65535 else default
    except Exception:
        return default


# ── Network ────────────────────────────────────────────────────────────────────

# RCON_HOST is the default address for LOCAL RCON connections (this app → its
# own dedicated server).  Despite intuition, CS2 binds RCON to the primary LAN
# adapter, NOT 0.0.0.0 — verified 2026-05-30 on a clean install (post-VirtualBox-
# uninstall, no virtual NICs) where netstat showed `192.168.0.103:27015 LISTENING`
# and `127.0.0.1:27015` was refused.  So the LAN IP is the right initial choice.
# If `_lan_ip()` returns 127.0.0.1 (no internet / DHCP blip) or the LAN IP turns
# out to be unreachable (Hyper-V/Docker/VPN adapter shuffles the routes),
# AppCore._post_launch_sanity_check enumerates the actual bind address via
# netstat and switches `self.rcon.host` automatically.
RCON_HOST     = _lan_ip()
RCON_PORT     = 27015
RCON_PASSWORD = ""        # auto-generated at first run; stored in oblivion_config.json
# 5050 default, but read from oblivion_config.json so the user can move it
# without a rebuild if anything (CS_GO_Arx_Applet, AirPlay, another Flask app)
# claims the port.  See _load_int_from_config above.  Port-collision fallback
# at bind time may push the *actual* port higher; main.py reports that back
# via _config.FLASK_PORT = <chosen> after Flask successfully binds.
FLASK_PORT    = _load_int_from_config("flask_port", 5050)
ADMIN_PIN     = ""        # set at first run; stored in oblivion_config.json


# ── Game data ──────────────────────────────────────────────────────────────────

OFFICIAL_MAPS = [
    "de_dust2", "de_mirage", "de_inferno", "de_nuke",
    "de_ancient", "de_anubis", "de_vertigo", "de_cache",
    "de_overpass", "de_train",
]

# Competitive active-duty pool — the seven maps a default veto board starts
# with.  Operators can swap any slot to a workshop map at veto-create time
# (per-veto override starting from active-duty); the default mirrors the
# Valve competitive matchmaking pool.  Used by `VetoSession` in `veto.py`.
ACTIVE_DUTY_POOL = [
    "de_mirage", "de_inferno", "de_ancient", "de_anubis",
    "de_nuke", "de_overpass", "de_vertigo",
]

# Panorama sub-path within any CS2 install (server or game client).
# Flask serves these via GET /api/maps/thumb/<map_name> → falls back to 404
# so the browser shows the placeholder icon when files aren't present.
CS2_PANORAMA_THUMBS_SUBPATH = os.path.join(
    "game", "csgo", "panorama", "images",
    "map_icons", "screenshots", "1080p",
)

GAME_MODES = [
    # Team matches (MatchZy-managed): one team vs one team at a fixed size.
    "Competitive", "Casual", "Wingman", "3v3", "4v4", "5v5",
    # Arena duels (K4-Arenas ladder): capped at 2-per-side by choice.
    "1v1", "2v2",
    "Arms Race", "Demolition", "Deathmatch",
    "Retakes", "Jailbreak", "Practice", "Warcraft", "Zombie Escape",
]

# game_type + game_mode together define CS2's ruleset
MODE_SETTINGS: dict[str, dict[str, str]] = {
    "Competitive": {"game_type": "0", "game_mode": "1", "maxplayers": "10"},
    "Casual":      {"game_type": "0", "game_mode": "0", "maxplayers": "12"},
    "Wingman":     {"game_type": "0", "game_mode": "2", "maxplayers": "4"},
    # Team matches (MatchZy): competitive ruleset; maxplayers caps total slots so
    # the lobby self-limits to the team size (N-per-side → maxplayers 2N).
    "3v3":         {"game_type": "0", "game_mode": "1", "maxplayers": "6"},
    "4v4":         {"game_type": "0", "game_mode": "1", "maxplayers": "8"},
    "5v5":         {"game_type": "0", "game_mode": "1", "maxplayers": "10"},
    # Arena duels (K4-Arenas): maxplayers is a generous CEILING, not a target —
    # the plugin only builds arenas for players actually present, so one high cap
    # fits any turnout (4 → 2 arenas, 12 → 6) without per-session tuning. Capped
    # at 2-per-side by choice (1v1 = plugin default; 2v2 = generated round config).
    "1v1":         {"game_type": "0", "game_mode": "1", "maxplayers": "16"},
    "2v2":         {"game_type": "0", "game_mode": "1", "maxplayers": "16"},
    "Arms Race":   {"game_type": "1", "game_mode": "0", "maxplayers": "16"},
    "Demolition":  {"game_type": "1", "game_mode": "1", "maxplayers": "10"},
    "Deathmatch":  {"game_type": "1", "game_mode": "2", "maxplayers": "20"},
    # Retakes: B3none RetakesPlugin + yonilerner allocator, competitive ruleset.
    "Retakes":     {"game_type": "0", "game_mode": "1", "maxplayers": "10"},
    # Jailbreak: hostage-style ruleset (game_type 0 / game_mode 2) gives the
    # CT-warden / T-prisoner scoring that the Jailbreak plugin expects.
    "Jailbreak":   {"game_type": "0", "game_mode": "2", "maxplayers": "32"},
    # Practice/MatchZy: runs on competitive ruleset; MatchZy drives match flow.
    "Practice":    {"game_type": "0", "game_mode": "1", "maxplayers": "10"},
    # Warcraft: RPG overlay — 9 character classes, XP system, purchasable items.
    # Runs on a casual base ruleset; the WarcraftPlugin CSS plugin drives all
    # RPG logic.  Works on any standard map.
    "Warcraft":        {"game_type": "0", "game_mode": "0", "maxplayers": "20"},
    # Zombie Escape: large-team cooperative mode (CTs escape, Ts are zombies).
    # Casual ruleset gives the relaxed spawning behaviour ZE maps expect.
    # ZombieMod (cs2fixes fork) + MultiAddonManager + ZombieReborn content pack.
    "Zombie Escape": {"game_type": "0", "game_mode": "0", "maxplayers": "64"},
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
    "5v5":         OFFICIAL_MAPS,
    # Arenas (K4-Arenas) auto-detect spawns on any map — official maps work out
    # of the box; aim_/1v1 workshop maps can still be picked via the workshop tab.
    "1v1":         OFFICIAL_MAPS,
    "2v2":         OFFICIAL_MAPS,
    "Arms Race":   ["ar_shoots", "ar_baggage", "ar_dizzy"],
    # v0.16.12 — Demolition's design intent is SMALL maps for fast 6v6
    # rounds with weapon progression; Valve dropped the CS:GO mini-maps
    # (de_lake, de_safehouse, de_shortdust, de_stmarc, de_bank,
    # de_sugarcane) from CS2's official rotation but the community
    # workshop ports preserve them.  Order matters here: workshop IDs
    # FIRST so an operator who's subscribed to them gets the intended
    # mini-Demolition vibe; OFFICIAL_MAPS as fallback for a cold-install
    # operator who hasn't grabbed the workshop classics yet (full-size
    # Demolition is awkward but functional).
    "Demolition":  [
        # Workshop ports of the CS:GO Demolition mini-maps (small, fast).
        "125439738",   # Shorttrain (de_shortdust)
        "125440342",   # Bank
        "125440847",   # Sugarcane
        "125441004",   # St. Marc
        # Official CS2 maps — fallback for operators who haven't
        # subscribed to the workshop classics.
        "de_dust2", "de_overpass", "de_inferno",
    ],
    # Deathmatch: limited to maps that have pre-configured spawn points in our
    # bundle (de_dust2, de_inferno, de_mirage, de_vertigo).  Other official maps
    # can be added after running the in-game spawn editor on the server.
    "Deathmatch":  ["de_dust2", "de_inferno", "de_mirage", "de_vertigo"],
    "Retakes":     OFFICIAL_MAPS,   # B3none ships per-map spawn configs
    "Jailbreak":   None,           # jb_* maps come from the workshop
    "Practice":    OFFICIAL_MAPS,  # MatchZy supports any standard comp map
    "Warcraft":        OFFICIAL_MAPS,  # RPG overlay — works on any standard map
    # Official maps run ZR zombie infection (ZM); ze_* workshop maps run full
    # Zombie Escape.  Both are selectable — official via this picker, ze_ via the
    # workshop tab.
    "Zombie Escape":   OFFICIAL_MAPS,
}

# Search terms for Steam Workshop URL filtering per mode
MODE_WORKSHOP_SEARCH: dict[str, str] = {
    "Competitive": "bomb defusal",
    "Casual":      "bomb defusal",
    "Wingman":     "wingman 2v2",
    "3v3":         "3v3",
    "4v4":         "4v4",
    "5v5":         "competitive 5v5",
    "1v1":         "1v1 aim",
    "2v2":         "2v2 aim",
    "Arms Race":   "arms race ar_",
    "Demolition":  "demolition",
    "Deathmatch":  "deathmatch",
    "Retakes":     "retake",
    "Jailbreak":   "jailbreak jb_",
    "Practice":    "competitive practice",
    "Warcraft":        "warcraft rpg",
    "Zombie Escape":   "zombie escape ze_",
}
_WS_BROWSE = "https://steamcommunity.com/workshop/browse/?appid=730&browsesort=trend"

# Steam Workshop tags used to filter downloaded maps per mode.
# Matching is case-insensitive against the tags returned by GetPublishedFileDetails.
# Maps with no tags are always included (can't exclude what isn't labelled).
# If none of the downloaded maps match the mode's tags, all maps are shown.
MODE_WORKSHOP_TAGS: dict[str, list[str]] = {
    # Tags are matched against the raw tag strings returned by the Steam Workshop
    # GetPublishedFileDetails API (lowercased).  Common CS2 workshop tags:
    #   "classic" — standard bomb/hostage maps
    #   "competitive", "casual", "deathmatch", "wingman", "surf", "kz",
    #   "aim", "zombie", "retake", "demolition", "armsrace"
    "Competitive": ["classic", "competitive"],
    "Casual":      ["classic", "competitive", "casual"],
    "Wingman":     ["wingman"],
    "3v3":         ["classic", "competitive"],
    "4v4":         ["classic", "competitive"],
    "5v5":         ["classic", "competitive"],
    "1v1":         ["aim", "1v1"],
    "2v2":         ["aim", "2v2"],
    "Arms Race":   ["armsrace", "arms race"],
    "Demolition":  ["demolition"],
    "Deathmatch":  ["deathmatch"],
    "Retakes":     ["retake", "classic", "competitive"],
    "Jailbreak":   ["jailbreak", "jb", "classic"],  # most jb_ maps are tagged Classic/Map, not jailbreak/jb
    "Practice":    ["classic", "competitive"],
    "Warcraft":        ["classic", "competitive", "casual"],
    "Zombie Escape":   ["zombie", "ze"],
}


# ── Workshop map scanner ───────────────────────────────────────────────────────

def load_workshop() -> list[str]:
    """Return sorted list of downloaded workshop map IDs (folder names)."""
    if not os.path.exists(WORKSHOP_DIR):
        return []
    return sorted(
        f for f in os.listdir(WORKSHOP_DIR)
        if os.path.isdir(os.path.join(WORKSHOP_DIR, f))
    )
