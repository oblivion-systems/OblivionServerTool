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

def _lan_ip() -> str:
    """Return the machine's primary LAN IP.

    CS2 dedicated server binds its RCON TCP socket to the LAN IP, not
    127.0.0.1.  Opening a UDP socket toward an external host (no data sent)
    asks the OS which source IP it would route through.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


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
APP_VERSION      = "0.9.0"
APP_REPO         = "jacquesvniekerk-eng/OblivionServerTool"
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


# ── Network ────────────────────────────────────────────────────────────────────

RCON_HOST     = _lan_ip()
RCON_PORT     = 27015
RCON_PASSWORD = ""        # auto-generated at first run; stored in oblivion_config.json
FLASK_PORT    = 5000
ADMIN_PIN     = ""        # set at first run; stored in oblivion_config.json


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


# ── Game data ──────────────────────────────────────────────────────────────────

OFFICIAL_MAPS = [
    "de_dust2", "de_mirage", "de_inferno", "de_nuke",
    "de_ancient", "de_anubis", "de_vertigo", "de_cache",
    "de_overpass", "de_train",
]

# Panorama sub-path within any CS2 install (server or game client).
# Flask serves these via GET /api/maps/thumb/<map_name> → falls back to 404
# so the browser shows the placeholder icon when files aren't present.
CS2_PANORAMA_THUMBS_SUBPATH = os.path.join(
    "game", "csgo", "panorama", "images",
    "map_icons", "screenshots", "1080p",
)

GAME_MODES = [
    "Competitive", "Casual", "Wingman", "3v3", "4v4", "1v1",
    "Arms Race", "Demolition", "Deathmatch",
    "Retakes", "Jailbreak", "Practice", "Warcraft", "Zombie Escape",
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
    "1v1":         None,
    "Arms Race":   ["ar_shoots", "ar_baggage", "ar_dizzy"],
    "Demolition":  ["de_lake", "de_safehouse", "de_shortdust",
                    "de_stmarc", "de_bank", "de_sugarcane"],
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
    "1v1":         "1v1 aim",
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
    "1v1":         ["aim", "1v1"],
    "Arms Race":   ["armsrace", "arms race"],
    "Demolition":  ["demolition"],
    "Deathmatch":  ["deathmatch"],
    "Retakes":     ["retake", "classic", "competitive"],
    "Jailbreak":   ["jailbreak", "jb"],
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
