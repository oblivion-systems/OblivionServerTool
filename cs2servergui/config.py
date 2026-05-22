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
APP_VERSION      = "0.7.5"
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
RCON_PASSWORD = "qweewq"
FLASK_PORT    = 5000
ADMIN_PIN     = "1234"   # digits only (web keypad); change before deploying


# ── Config file ────────────────────────────────────────────────────────────────
# Stored next to the .exe when packaged (PyInstaller), or at the project root
# in dev mode.  The config.py file itself lives inside cs2servergui/, so we
# go one level up to reach the project root in dev mode.

_APP_DIR = os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else
    os.path.join(os.path.dirname(__file__), "..")
))
_CONFIG_FILE = os.path.join(_APP_DIR, "oblivion_config.json")


# ── Game data ──────────────────────────────────────────────────────────────────

OFFICIAL_MAPS = [
    "de_dust2", "de_mirage", "de_inferno", "de_nuke",
    "de_ancient", "de_anubis", "de_vertigo", "de_cache",
]

GAME_MODES = [
    "Competitive", "Casual", "Wingman", "3v3", "4v4", "1v1",
    "Arms Race", "Demolition", "Deathmatch",
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
    "1v1":         None,
    "Arms Race":   ["ar_shoots", "ar_baggage", "ar_dizzy"],
    "Demolition":  ["de_lake", "de_safehouse", "de_shortdust",
                    "de_stmarc", "de_bank", "de_sugarcane"],
    "Deathmatch":  OFFICIAL_MAPS,
    "Zombies":     None,
    "Surf":        None,
    "KZ / Climb":  None,
    "Retakes":     OFFICIAL_MAPS,
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
    "Zombies":     "zombie escape",
    "Surf":        "surf",
    "KZ / Climb":  "kz climb",
    "Retakes":     "retake",
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
    "Zombies":     ["zombie"],
    "Surf":        ["surf"],
    "KZ / Climb":  ["kz", "climb"],
    "Retakes":     ["retake", "classic", "competitive"],
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
