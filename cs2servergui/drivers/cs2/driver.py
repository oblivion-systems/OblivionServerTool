"""
cs2servergui/drivers/cs2/driver.py — Counter-Strike 2 game driver.

For v0.13.0 this is a thin identity shell + a couple of pure
helpers.  Heavy logic (start_server, plugin deploy, RCON, MatchZy
handoff) still lives in ``cs2servergui/core.py`` and is gradually
being migrated here as the strangler-fig refactor proceeds.

What's HERE today:
  - Identity (game name, process image, port, log filename)
  - Mode list (from config.MODE_SETTINGS keys)
  - Default-map-per-mode (from config.MODE_MAPS)
  - Status-line formatting (mode-aware MR12/MR15 hint for competitive)

What stays in core.py for now (migration TODO):
  - start_server / stop_server / restart_server
  - _resolve_rcon_host
  - _poll_rcon_ready
  - install_server / install_metamod / etc.
  - Plugin deploy logic (per-mode _PLUGIN_KIND tables)
  - MatchZy match-config writer + matchzy_loadmatch RCON call
  - All workshop / cmdfilter logic
  - Crash auto-restart loop

Each of these gets a dedicated method on the driver as it's pulled
out — at which point the AppCore version becomes a thin delegate.
"""
from __future__ import annotations

from ..base import GameDriver
from ... import config as _cfg


class CS2Driver(GameDriver):
    """Counter-Strike 2 (Source 2 engine, Windows dedicated server).

    Operator's machine layout (Windows-only for v0.13):
      <server_dir>/
        steamcmd.exe
        steamapps/common/Counter-Strike Global Offensive/
          game/
            bin/win64/cs2.exe         ← the dedicated-server binary
            csgo/
              console.log              ← runtime log
              cfg/                     ← server.cfg, gamemode_*.cfg
              cfg/MatchZy/             ← match config target
              addons/                  ← MetaMod + CounterStrikeSharp
              addons/counterstrikesharp/plugins/   ← CSS plugins
    """

    # ─── Identity ──────────────────────────────────────────────────
    game_name             = "Counter-Strike 2"
    short_name            = "cs2"
    default_port          = 27015
    process_image_name    = "cs2.exe"
    # CRITICAL: -dedicated distinguishes the dedicated server process
    # from a client process with the same image name.  The operator
    # often runs both on the same machine; a broad "kill cs2.exe" by
    # image would kill their playing client too.  Filter on this
    # substring before any kill.  See MEMORY.md user_setup.md.
    process_args_marker   = "-dedicated"
    console_log_filename  = "console.log"

    # ─── Mode + map enumeration ────────────────────────────────────

    def modes(self) -> list[str]:
        """Return the mode list — sourced from config.MODE_SETTINGS so
        the SPA, deploy tables, and driver stay in sync from one
        place.  When config adds a new mode the driver picks it up
        automatically (no driver edit needed)."""
        return list(_cfg.MODE_SETTINGS.keys())

    def default_map(self, mode: str) -> str:
        """First map in the per-mode allow-list, or de_dust2 as the
        safe fallback (always installed with CS2)."""
        per_mode = _cfg.MODE_MAPS.get(mode)
        if per_mode and isinstance(per_mode, list) and per_mode:
            return per_mode[0]
        # None (workshop-required mode) or empty list → operator picks
        # the map separately, but we need a hard default for the
        # "Start without a map selected" code path.
        return "de_dust2"

    # ─── Console-log path (override default) ───────────────────────

    def console_log_dir(self, core):
        """CS2's console log lives under csgo/, not the parent
        Counter-Strike Global Offensive/ dir.  Use core._csgo_dir()
        which already resolves to the right place."""
        try:
            return core._csgo_dir() if hasattr(core, "_csgo_dir") else None
        except Exception:
            return None

    # ─── Status-line formatter (mode-aware) ────────────────────────

    def status_line(self, core) -> str:
        """Like the base implementation but adds a per-mode hint when
        the operator is running competitive (MR12 default) so the
        snapshot reads cleanly: "Counter-Strike 2 · de_vertigo · 5v5
        (MR12) · 10 player(s)"."""
        running = bool(getattr(core, "running", False))
        if not running:
            return f"{self.game_name} · offline"
        map_name  = getattr(core, "current_map", "") or "?"
        mode_name = getattr(core, "current_mode", "") or "?"
        try:
            players = int(getattr(core, "player_count", 0) or 0)
        except (TypeError, ValueError):
            players = 0
        # Competitive-family modes default to MR12.  Casual / Arms Race
        # / Deathmatch have their own round structure — leave bare.
        mr_hint = ""
        if mode_name in ("Competitive", "Wingman", "3v3", "4v4", "5v5",
                         "Retakes", "Practice"):
            mr_hint = " (MR12)"
        return (f"{self.game_name} · {map_name} · {mode_name}{mr_hint}"
                f" · {players} player(s)")

    # ─── Self-description (extended for CS2 specifics) ─────────────

    def describe(self) -> dict:
        d = super().describe()
        d["mode_count"]   = len(d["modes"])
        d["plugin_layer"] = "MetaMod + CounterStrikeSharp"
        d["match_layer"]  = "MatchZy"
        return d
