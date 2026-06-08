# PLATFORM.md — Oblivion as a multi-game platform

> The design document for the v0.13+ driver abstraction.  Written
> after the first real tournament (2026-06-05, see
> `RETROSPECTIVE_2026_06_05.md`) and the v0.13.0 driver-seam release.
>
> Audience: future-me + any contributor adding a TF2 / GTA-RP / FiveM
> driver.  Reading time: ~10 min.

---

## 1. Why this exists

Oblivion shipped its first real production night as a **CS2-only**
tool.  10-player tournament, remote captains over Discord, MatchZy
handoff, full veto state machine.  It worked.

But "CS2-only" is leaking everywhere: `cs2.exe` literals in process
detection, `MatchZy` references in the match handoff, `csgo/cfg/`
write paths in the plugin deploy, `-dedicated` as a hardcoded
process filter, 16 mode names in `config.MODE_SETTINGS` that all
assume Source-engine round structure.

This doc captures the **plan** to lift those out into a `GameDriver`
abstraction so v0.13 can add a TF2 driver (proof point — different
game, same Source-RCON, same MetaMod ecosystem, different match
plugin) and v0.15 can add a FiveM driver (different game, different
RCON, different match concept, different process model).

We DO NOT plan to abstract everything.  Some of the codebase is
intentionally game-agnostic and should stay that way (Flask routes,
SPA, veto state machine, Discord bot, SSE plumbing).  Some pieces
are genuinely CS2-only forever (MatchZy match-config JSON shape,
CounterStrikeSharp plugin layout, the cs2fixes-vs-ZombieMod choice).
The driver seam is the **boundary** between those.

---

## 2. What v0.13.0 already landed

The seam.  Not the extraction.

```
cs2servergui/
├── drivers/
│   ├── __init__.py          ← exports + architecture docs
│   ├── base.py              ← GameDriver ABC
│   └── cs2/
│       ├── __init__.py
│       └── driver.py        ← CS2Driver (identity + thin helpers)
├── core.py                  ← AppCore.driver = CS2Driver() (one-line)
└── web.py                   ← diagnostic snapshot "Driver" section
```

**Identity** (game_name, short_name, default_port,
process_image_name, process_args_marker, console_log_filename) +
**enumeration** (modes(), default_map(mode)) + **formatting**
(status_line(), describe()).

Existing code in `core.py` still uses hardcoded `"cs2.exe"` /
`"MatchZy"` literals — those will be migrated method-by-method as
the strangler-fig refactor proceeds.  See §5.

**231/231 tests green**, including 9 new driver tests in
`tests/test_drivers.py` (regression guard against future identity
drift).

---

## 3. The boundary

### Stays generic (driver doesn't touch these — same code for every game)

| Layer | File | What it does |
|---|---|---|
| Flask web | `web.py` | HTTP routes, auth, SSE broadcast, sessions, snapshot |
| SPA | `static/js/*` | Entire frontend — never names a game |
| Veto state machine | `veto.py` | Roster → teams → vote → links → veto → finale.  Generic |
| Discord bot | `discord_bot.py` | DMs, embeds, voice channels, slash commands |
| SSE broadcast | (inline in `web.py`) | Queue-per-subscriber fanout |
| Match events | `match_events.py` | RCON-poll daemon — talks to **driver**, not `cs2.exe` directly |
| RCON protocol | `rcon.py` | Source RCON wire protocol.  TF2 reuses it; FiveM needs its own |

A reader of `veto.py` should be able to swap in a TF2 driver without
opening that file.  That's the contract.

### Always in the driver (CS2-only forever)

| Concept | Today's location | Driver-owned in v0.13.x+ |
|---|---|---|
| `cs2.exe` process name | `core.py` literals + `_PLUGIN_*` tables | `driver.process_image_name` |
| `-dedicated` filter | `core.py` kill logic | `driver.process_args_marker` |
| `csgo/` install layout | `_csgo_dir()` on AppCore | `driver.console_log_dir(core)` |
| `csgo/cfg/MatchZy/` write target | `core.write_matchzy_config()` | `driver.write_match_config(...)` (TBD) |
| MetaMod + CSS plugin layout | `core._PLUGIN_KIND_*` + deploy logic | `driver.deploy_plugins_for_mode(...)` (TBD) |
| `matchzy_loadmatch` RCON cmd | `core.send_match_load_rcon()` | `driver.load_match(rcon, config_path)` (TBD) |
| MR12/MR15/competitive ruleset hint | (none today; hardcoded in SPA) | `driver.status_line()` (already moved) |
| Mode catalogue + map list | `config.MODE_SETTINGS` + `MODE_MAPS` | `driver.modes()` + `driver.default_map()` (already moved) |

### What's genuinely contested

| Concept | Argument for generic | Argument for driver |
|---|---|---|
| `MatchZy` veto session shape | Veto is a competitive-shooter concept — TF2 has the same flow | TF2 doesn't use MatchZy; it has its own match plugins (TF2DB).  The JSON shape is bespoke per match plugin |
| Captain tokens | Token mint + claim is generic; team-of-5 vs team-of-6 is a parameter | Game-specific team size + captain count belongs to driver |
| Mode → plugin set mapping | "Competitive needs MatchZy + cs2fixes" is one mapping | TF2's "Competitive needs TF2DB + SoapDM" is another.  Each driver owns its own |

**Verdict for v0.13:** veto stays generic (`team_size_per_team` becomes a `driver.captain_voting_config()` parameter); plugin sets are driver-owned (each driver returns its own per-mode list).

---

## 4. Driver interface

### v0.13.0 (shipped)

```python
class GameDriver(abc.ABC):
    # Identity (class attributes)
    game_name: str             = "Unknown Game"
    short_name: str            = "unknown"
    default_port: int          = 27015
    process_image_name: str    = "server.exe"
    process_args_marker: str   = "-dedicated"
    console_log_filename: str  = "console.log"

    # Abstract — every driver implements
    def modes(self) -> list[str]: ...
    def default_map(self, mode: str) -> str: ...

    # Default impls (override if needed)
    def console_log_dir(self, core: AppCore) -> str | None: ...
    def console_log_path(self, core: AppCore) -> str | None: ...
    def status_line(self, core: AppCore) -> str: ...
    def describe(self) -> dict: ...
```

### v0.13.x planned additions (in migration order)

Each lands as one focused commit.  Each turns a hardcoded `core.py`
method into a driver method + thin AppCore delegate.  Each is fully
tested against the existing CS2 behaviour before the AppCore delegate
gets simplified.

| Method (driver) | Source (core.py) | Effort | Priority |
|---|---|---|---|
| `install_root(core)` | `_csgo_dir()` | Trivial | High — first migration, proves the pattern |
| `addons_dir(core)` | `os.path.join(_csgo_dir(), "addons")` literals everywhere | Small | High |
| `cfg_dir(core)` | Several `os.path.join(_csgo_dir(), "cfg")` literals | Small | High |
| `match_config_target(core)` | `cfg/MatchZy/` literal | Small | Medium |
| `is_server_process(proc)` | kill-by-image filter | Small | Medium |
| `process_kill_filter()` | `"cs2.exe -dedicated"` filter | Small | Medium |
| `start_server(core, map, mode, ...)` | `AppCore.start_server()` body | Large | Medium — big extraction |
| `stop_server(core)` | `AppCore.stop_server()` body | Medium | Medium |
| `current_state(rcon)` | `core.poll_status()` body | Medium | Medium |
| `deploy_plugins_for_mode(mode)` | `_PLUGIN_KIND_*` tables + `deploy()` | Large | Low — depends on plugin registry (#90) |
| `write_match_config(session, target)` | MatchZy JSON shape | Medium | Low — TF2 needs different shape |
| `load_match(rcon, path)` | `matchzy_loadmatch X` | Small | Low |
| `gameinfo_patch(core)` | `gameinfo.gi` MetaMod patch | Small | Low — Source-engine-only |

### Beyond v0.13 (TF2 + FiveM driver work)

```python
class TF2Driver(GameDriver):
    game_name           = "Team Fortress 2"
    short_name          = "tf2"
    default_port        = 27015
    process_image_name  = "srcds.exe"
    process_args_marker = "-game tf"
    console_log_filename = "console.log"
    # modes() returns ["Casual", "Competitive", "MGE", "Highlander"...]
    # default_map() returns "cp_dustbowl" / "pl_upward" / etc.
    # Uses TF2DB instead of MatchZy
    # Same Source RCON, same MetaMod
```

```python
class FiveMDriver(GameDriver):
    game_name           = "FiveM (GTA V)"
    short_name          = "fivem"
    default_port        = 30120          # different
    process_image_name  = "FXServer.exe"
    process_args_marker = ""             # FiveM doesn't co-host client
    console_log_filename = "server.log"
    # modes() = ["Roleplay", "Racing", "Freeroam"]
    # NO MatchZy / MetaMod / Source-RCON.  Custom resource layer.
```

The TF2 driver is the **proof point** that the abstraction is right.
If TF2 needs more than identity overrides, the boundary is wrong.

---

## 5. Migration plan (strangler fig)

The pattern, with the first migration as worked example.

### Step 1 (v0.13.1) — `install_root()`

Today:
```python
# core.py
class AppCore:
    def _csgo_dir(self) -> str:
        return os.path.dirname(_config.CS2_ADDONS_DIR)
```

After v0.13.1:
```python
# drivers/cs2/driver.py
class CS2Driver(GameDriver):
    def install_root(self, core) -> str:
        """csgo/ directory (parent of addons/)."""
        return os.path.dirname(_config.CS2_ADDONS_DIR)

# core.py
class AppCore:
    def _csgo_dir(self) -> str:
        """Backward-compat shim — call sites migrate to
        `core.driver.install_root(core)` over time."""
        return self.driver.install_root(self)
```

Other call sites in `core.py` keep working unchanged.  New code uses
`core.driver.install_root(core)` directly.  When the last call site
migrates, the shim deletes.

### Step 2-N

Each subsequent method follows the same shape:

1. Add the abstract method to `GameDriver.base`
2. Implement in `CS2Driver`
3. Reduce the AppCore method to `return self.driver.X(...)`
4. Add tests (driver tests + integration test that AppCore delegate still works)
5. Ship as v0.13.x+1
6. Repeat

No method moves until its **tests can be written against the driver
in isolation**.  This is the protection: if a method is too entangled
to test on the driver, that's the signal to refactor it first, not to
yolo the migration.

### TF2 driver (v0.13.x.y)

Once ~70% of the CS2-specific methods are in `CS2Driver` (the start /
stop / kill / log / plugin deploy chain), drop a `TF2Driver` next
to it.  Same module shape:

```
drivers/
├── base.py
├── cs2/
│   ├── __init__.py
│   └── driver.py
└── tf2/
    ├── __init__.py
    └── driver.py     ← inherits from GameDriver
```

`TF2Driver` overrides identity + provides its own `modes()` /
`default_map()` / `start_server()`.  The rest of the codebase
(web, SPA, veto, Discord) is unchanged.

Driver selection becomes a config option:
```python
# AppCore.__init__
driver_name = cfg.get("driver", "cs2")
if   driver_name == "cs2": self.driver = CS2Driver()
elif driver_name == "tf2": self.driver = TF2Driver()
else: raise ValueError(...)
```

---

## 6. Constraints + lessons from the tournament

From `RETROSPECTIVE_2026_06_05.md`, here's what the driver layer
should bake in to avoid re-learning:

### Mutation contract

Every state-mutating endpoint returns the fresh snapshot.  The SPA
applies the response locally via `_vetoApply` instead of waiting for
SSE.  **Generic principle, codified in the SPA — drivers don't
touch it.**  But: when a driver method mutates state (start_server,
load_match), the AppCore wrapper that calls it must broadcast +
return a snapshot per this contract.

### Cookie-without-redirects auth

External-origin URLs (captain links, voter links) should never
redirect-chain — iOS WKWebView strips Set-Cookie.  Render a 200
HTML interstitial.  **Generic principle, codified in web.py.**
TF2/FiveM captain links inherit it for free.

### No silent drops

`_veto_broadcast` queue overflow now has a drops counter +
diagnostic-snapshot section.  Drivers that add their own
broadcasting (round summaries, future event types) should mirror
this pattern.

### Game-specific timing

CS2 takes ~5 s after `Server ready` before RCON is reliable.
`_poll_rcon_ready` is per-driver.  TF2 boots ~10 s.  FiveM boots
~30 s.  **`driver.poll_rcon_ready_timeout` belongs on the driver.**

### Plugin verification on deploy

The current `_verify_plugin_files()` is CS2-specific (CSS layout).
Driver-owned (`driver.verify_deployment(deployed_kinds)`).

---

## 7. Roadmap impact

The driver work doesn't change the v1.0 destination — it changes
the path.

```
v0.13.0  ── driver seam open (shipped 2026-06-06)
v0.13.1  ── first method migration (install_root) ── you are here
v0.13.x  ── progressively migrate the CS2-specific methods
v0.13.x.y ─ TF2Driver skeleton (identity only)
v0.13.x.y+ ─ TF2Driver real (own start_server, own match plugin)
v0.14    ── Linux + headless (driver abstraction makes Linux easier
            — no UI code is platform-conditional, just driver impl)
v0.15    ── FiveM driver (first non-Source.  Validates that the
            abstraction holds beyond Source-engine games)
v1.0     ── public launch, BSL license, donations live, repo public
```

The v0.12 Discord features (round summaries, /move-teams, remote
voting) are **driver-agnostic** — they all live in the generic
layer (`discord_bot.py`, `web.py`, `match_events.py`).  TF2 + FiveM
tournaments will get all the Discord automation for free.

---

## 8. Plugin Manager seam (#90/#91/#92)

The plugin install system is **driver-aware but not driver-owned**.

| Concept | Owner |
|---|---|
| Plugin catalog (what's available) | Separate `OblivionPluginRegistry` repo (#90).  Each plugin entry tags its required driver(s) |
| Plugin file layout (where to drop files) | Driver (`driver.addons_dir()`, `driver.cfg_dir()`) |
| Per-mode plugin selection | Driver (`driver.plugins_for_mode(mode)`) — CS2's set ≠ TF2's set |
| Curated packs (#91) | Catalog metadata.  Pack = list of plugin ids |
| Install / verify / remove ops | Generic Plugin Manager module, uses driver helpers for paths |
| UX (browse / install / toggle) | Generic SPA Plugin tab |

A plugin tagged `driver: cs2` shows up in the catalog only when
`AppCore.driver.short_name == "cs2"`.  Tagged `driver: any` is a
universal helper (e.g. a log-tail tool that works on any
Source-engine driver via the generic `console_log_path()`).

The UX brief (#92) is the next design milestone after this doc.
Skeleton:

- **Browse** — categories (Match, Gameplay, Admin, Aesthetic)
- **Install** — single-click, shows progress + post-install checklist
- **Manage** — list of installed, per-mode enable/disable, "Update"
  if registry version > installed
- **Packs** — "Apply Competitive 5v5 preset" = install N plugins
- **Conflicts** — two plugins claim the same gameinfo.gi patch slot

---

## 9. v1.0 readiness criteria (driver layer)

For the v1.0 launch to claim "multi-game ready":

- [ ] **Two drivers in production** — CS2 + at least one other
      (TF2 most likely).  Either should be selectable at install time.
- [ ] **Driver tests are exhaustive** — every method on `GameDriver`
      has at least one test per implementation.  Adding a new driver
      starts by satisfying the test contract.
- [ ] **Plugin Manager + catalog live** (#90/#91/#92 shipped).
- [ ] **No `cs2.exe` / `MatchZy` literals in generic code** — grep
      catches any regression.
- [ ] **PLATFORM.md still matches reality** — this doc is the
      contract; if it drifts, the next contributor builds on lies.
- [ ] **TF2Driver shipped as a real production proof** — at least
      one TF2 tournament successfully completed on it.

When all six tick, v1.0 ships.  Until then, the driver layer is
"v0.x — actively migrating."

---

## 10. Open questions

Decisions that are not yet made.  Document the trade-off; defer to
"the moment we actually need to choose."

### A. Multi-driver in one process?

Today's plan: one driver per running `OblivionServerTool.exe`.
Operator chooses CS2 OR TF2 at install/config time.

Alternative: a single process hosts multiple drivers, with a driver
picker in the SPA.  Useful for a server hosting company that runs
both.  More code, harder testing.

**Defer until someone asks.**  Most operators run one game.

### B. Driver versioning?

A `CS2Driver` v1 today vs. v2 next year (e.g. when MatchZy is
replaced by something new).  Do we version the driver alongside
the app?

**Defer.**  Today the driver moves in lockstep with the app version;
no compelling reason to decouple.

### C. Third-party drivers?

Long-term: someone writes a `MordhauDriver` and ships it as a
separate pip-installable package.  Plugin to a plugin.

**Defer past v1.0.**  Get two first-party drivers shipping first.

### D. Plugin registry: monorepo or per-plugin repos?

#90 says "separate `OblivionPluginRegistry` repo + seed catalog."
Open question whether each plugin is its own folder in that repo
or its own git repo.

**Decide in the UX brief (#92).**

---

*Filed by Claude Sonnet 4.6 (1M context), under operator direction.*
*Last updated: 2026-06-06 (v0.13.0).*
*Related: RETROSPECTIVE_2026_06_05.md, ROADMAP.md, BIBLE.md, AUDIT.md.*
