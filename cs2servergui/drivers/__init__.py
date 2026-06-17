"""
cs2servergui/drivers/ — game-driver abstraction (v0.13.0, task #86).

The Oblivion Server Tool grew up as a CS2-specific app: every file
hardcodes ``cs2.exe`` / ``MatchZy`` / Source-RCON / MetaMod paths.
That made the tournament tooling fast to build but locked us into one
game.  This package introduces a **driver seam** so v0.13 can add a
TF2 driver, v0.15 can add a GTA-RP / FiveM driver, etc. — without
touching the Flask web layer, the SPA, the veto state machine, the
Discord bot, or the broadcast/SSE plumbing.

ARCHITECTURE
------------
- ``base.GameDriver``  — abstract base class.  Defines the interface
                         every driver must provide: identity (game
                         name, process image, default port), mode
                         configuration, and the operations the rest
                         of the app needs (process detection,
                         console-log path, status-line formatting).
- ``cs2.CS2Driver``    — concrete CS2 implementation.  For v0.13.0
                         this is a thin shell that captures CS2's
                         identity; the heavy lifting (start_server,
                         RCON, plugin deploy, MatchZy handoff) still
                         lives in ``core.py``.  Future versions move
                         those into the driver one seam at a time.
- ``AppCore.driver``    — the active driver, set at AppCore init
                         time.  ``core.driver`` is the official
                         accessor; new code reaches game-specific
                         knobs through ``core.driver.X`` instead
                         of hardcoding ``"cs2.exe"`` literals.

MIGRATION STRATEGY (strangler fig)
----------------------------------
This is the v1 seam.  Existing code still works untouched.  New code
and refactored code use ``core.driver.X``.  Over time, function-by-
function, the body of ``core.start_server`` (etc.) moves into
``CS2Driver.start_server``; the AppCore method becomes a thin
delegate.  When a TF2Driver lands in v0.13, only the methods that
have been moved into the driver need TF2 equivalents — anything
still in AppCore is the migration TODO list.

WHAT STAYS GENERIC (driver doesn't touch these)
-----------------------------------------------
- ``web.py``         — Flask routes, auth, SSE, broadcast
- ``static/js/*``    — entire SPA
- ``veto.py``        — VetoSession state machine, captain/voter tokens
- ``discord_bot.py`` — bot lifecycle, slash commands
- ``match_events.py``— round-summary poller (driver-mediated RCON only)
- ``rcon.py``        — Source RCON protocol (TF2 also uses it; FiveM
                       does not — FiveM driver will provide its own
                       command runner)

WHAT'S CS2-SPECIFIC (always in the driver)
------------------------------------------
- MetaMod + CounterStrikeSharp plugin layout
- MatchZy match-config JSON shape + ``matchzy_loadmatch`` RCON cmd
- ``csgo/cfg/MatchZy/`` write target
- ``cs2.exe -dedicated`` process detection
- ``de_dust2`` / ``de_mirage`` / etc. map pool

The driver abstraction is the seam that lets us add a second game
(TF2, L4D2, etc.) by subclassing ``GameDriver`` rather than rewriting
the app.  ``CS2Driver`` is the concrete implementation today.
"""
from .base import GameDriver
from .cs2 import CS2Driver

__all__ = ["GameDriver", "CS2Driver"]
