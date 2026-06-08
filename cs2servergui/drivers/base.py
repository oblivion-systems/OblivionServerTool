"""
cs2servergui/drivers/base.py — GameDriver abstract base class.

The minimal interface every driver must implement.  Kept deliberately
small for v0.13.0 — the goal is to seed the seam, not to factor every
CS2-specific line out of the existing code in one go.  New abstract
methods get added as the strangler-fig refactor pulls more of
``core.py`` into per-driver implementations.

Design principle: prefer **properties over methods** for static
identity (game_name, default_port).  Reserve methods for things that
need runtime state or arguments.  A v0.13 TF2Driver should be ~30
lines of constants + a couple of trivial method overrides.
"""
from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Forward ref only — drivers reference AppCore for stateful
    # operations but the import would be circular at module load.
    from ..core import AppCore


class GameDriver(abc.ABC):
    """Identity + per-game operations for one dedicated-server target.

    Concrete subclasses live in ``cs2servergui/drivers/<game>/``.  The
    active driver is held on ``AppCore.driver`` and reached as
    ``core.driver.X`` from anywhere in the codebase that needs a
    game-specific knob.

    Subclassing checklist (v0.13.0):
        1. Inherit from ``GameDriver``
        2. Set the class-level identity properties (game_name,
           short_name, process_image_name, etc.)
        3. Override ``modes()`` to return your game's mode list
        4. Override ``default_map()`` for the per-mode default
        5. Anything else is the migration TODO — start with no-ops
           that raise NotImplementedError and fill in as you go.
    """

    # ─── Identity ──────────────────────────────────────────────────
    # These are class attributes (not abstractproperties) so a driver
    # can declare them as plain assignments at the top of its class
    # body — most concise possible subclass shape.

    #: Human-readable game name shown in the SPA header, snapshot, etc.
    #: Example: "Counter-Strike 2"
    game_name: str = "Unknown Game"

    #: Short slug for paths, logs, RCON markers.  All-lowercase, no
    #: spaces.  Example: "cs2", "tf2", "fivem".
    short_name: str = "unknown"

    #: Default UDP port the dedicated server listens on.  CS2/TF2/etc.
    #: use 27015; FiveM uses 30120.  Operator can override via config.
    default_port: int = 27015

    #: Process image name the OS sees.  Used by core's "is the server
    #: actually running?" check + ``netstat``-based recovery.
    #: NEVER broad-kill this image by name (operator may be playing the
    #: client too); filter by ``process_args_marker`` first.
    process_image_name: str = "server.exe"

    #: Substring that distinguishes a DEDICATED-SERVER process from a
    #: CLIENT process with the same image name.  ``-dedicated`` for
    #: Source-engine games; ``+server.cfg`` for some others.  If empty,
    #: the driver can't tell client from server — operator must be on
    #: a dedicated host (no playing on the same machine).
    process_args_marker: str = "-dedicated"

    #: Filename the dedicated server writes its console log to,
    #: relative to ``console_log_dir(core)``.
    console_log_filename: str = "console.log"

    # ─── Mode + map enumeration ────────────────────────────────────

    @abc.abstractmethod
    def modes(self) -> list[str]:
        """Return the ordered list of mode identifiers this game
        supports.  These strings appear in the SPA mode picker, the
        veto state machine, and the deploy table."""
        ...

    @abc.abstractmethod
    def default_map(self, mode: str) -> str:
        """Return the default map for a given mode.  Used when the
        operator clicks Start without picking a map first.  Should
        return a map id valid for that mode (e.g. ``de_dust2`` for
        CS2 Competitive, ``cp_dustbowl`` for TF2 Payload)."""
        ...

    # ─── Console-log discovery ─────────────────────────────────────

    def console_log_dir(self, core: "AppCore") -> str | None:
        """Return the absolute path of the directory containing the
        dedicated-server console log, or None if the install isn't
        configured / locatable.

        Default implementation: ``core._csgo_dir()`` for Source-engine
        games.  Override in non-Source drivers."""
        try:
            csgo = core._csgo_dir() if hasattr(core, "_csgo_dir") else None
        except Exception:
            csgo = None
        return csgo

    def console_log_path(self, core: "AppCore") -> str | None:
        """Convenience: absolute path of the console log file.
        Default = ``console_log_dir() / console_log_filename``."""
        import os
        d = self.console_log_dir(core)
        if not d:
            return None
        return os.path.join(d, self.console_log_filename)

    # ─── Status-line formatter ─────────────────────────────────────

    def status_line(self, core: "AppCore") -> str:
        """One-line human summary of the server state, suitable for
        the diagnostic snapshot + the SPA header tooltip.
        Default implementation = "<game_name> · <map> · <mode> · <count> players"."""
        running = bool(getattr(core, "running", False))
        if not running:
            return f"{self.game_name} · offline"
        map_name  = getattr(core, "current_map", "") or "?"
        mode_name = getattr(core, "current_mode", "") or "?"
        try:
            players = int(getattr(core, "player_count", 0) or 0)
        except (TypeError, ValueError):
            players = 0
        return f"{self.game_name} · {map_name} · {mode_name} · {players} player(s)"

    # ─── Self-description for diag snapshot ────────────────────────

    def describe(self) -> dict:
        """Driver identity as a flat dict — surfaced in the
        ``/api/diag/snapshot`` Driver section so the operator can
        confirm at a glance which driver the app is running."""
        return {
            "game_name":             self.game_name,
            "short_name":            self.short_name,
            "default_port":          self.default_port,
            "process_image_name":    self.process_image_name,
            "process_args_marker":   self.process_args_marker,
            "console_log_filename":  self.console_log_filename,
            "modes":                 self.modes(),
        }
