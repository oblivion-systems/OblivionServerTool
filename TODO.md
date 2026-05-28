# TODO — Oblivion Server Tool

> The **working checklist** toward v1.0.0. Phases mirror [ROADMAP.md](ROADMAP.md);
> principles come from [BIBLE.md](BIBLE.md); the code map is [INGEST.md](INGEST.md).
>
> **Legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked
>
> Check items off *as you do them*. Keep notes inline (`— note:`). When every box in a
> phase is `[x]` and its exit criteria are met, mark the phase header `✅`.

---

## Progress Overview

| Phase | Title | Status |
|-------|-------|--------|
| 0 | Groundwork (this session) | ✅ done |
| 1 | Stabilise the foundation | ⬜ not started |
| 2 | Verify every mode | ⬜ not started |
| 3 | Harden & secure | ⬜ not started |
| 4 | Test & release engineering | ⬜ not started |
| 5 | Polish & 1.0 launch | ⬜ not started |

---

## Phase 0 — Groundwork ✅
*Completed this session. Recorded here for traceability.*

- [x] Remove all traces of CS2Retake plugin (DLLs, spawn JSONs, SQLite, configs)
- [x] Replace Retakes with MatchZy `matchzy_retakes_mode 1` via `retakes.cfg`
- [x] Fix `MODE_SETTINGS["Retakes"]` ruleset to competitive (`game_mode 1`)
- [x] Normalise all plugin bundles to mirror the `csgo/` layout
- [x] Simplify `_PLUGIN_COPY_RULES` to the uniform `("addons","addons")` pattern
- [x] Remove orphaned `_plugins_src/` and SQLite.Interop deployment hacks
- [x] Audit `_PLUGIN_KIND`, `_PLUGIN_VERIFY_FILES`, `_MODE_PLUGIN_NAMES`, cleanup items
- [x] Produce full structural index → [INGEST.md](INGEST.md)
- [x] Author BIBLE / ROADMAP / TODO planning docs

---

## Phase 1 — Stabilise the Foundation ⬜
**Exit:** README, CHANGELOG, and code agree; no dead plugin references remain.

### 1.1 Documentation reconciliation
- [x] Update README plugin table — Retakes is **MatchZy**, not "CS2Retake + RetakesAllocator"
- [x] Verify every other row of the README plugin table against `_MODE_PLUGIN_NAMES` — note: added missing Arenas (1v1/3v3/4v4) row; corrected Deathmatch & Jailbreak to show CS2Fixes + CSS plugin
- [x] Add CHANGELOG entry: retakes→MatchZy migration + bundle restructure
- [ ] Confirm README feature list matches actual `web.py` routes (no phantom features)

### 1.2 Code/data integrity audit
- [ ] Grep the whole tree for residual `CS2Retake` / `RetakesAllocator` / `retakes` plugin refs
- [ ] Confirm `_PLUGIN_COPY_RULES` keys all have matching source dirs under `plugins/`
- [ ] Confirm `_PLUGIN_CLEANUP_ITEMS` covers every path any plugin writes into `csgo/`
- [ ] Confirm `_PLUGIN_VERIFY_FILES` marker files actually ship in each bundle
- [ ] Sanity-check `MODE_SETTINGS` rulesets for all 14 modes (game_type/game_mode/maxplayers)
- [ ] Confirm `_CSS_HOST_DLLS` filter list is still accurate vs. current CSS host

### 1.3 Repo hygiene
- [ ] Review uncommitted/untracked items from git status (`build_log.txt`, stray DLLs, `cfg/`, `data/`, `characters/`)
- [ ] Decide what belongs in the bundle vs. `.gitignore` vs. deletion
- [ ] Commit the Phase 0 + Phase 1 changes with a clear message

---

## Phase 2 — Verify Every Mode ⬜
**Exit:** all 14 modes confirmed working on a real server (matrix fully checked).

### 2.1 Test harness setup
- [ ] Stand up a clean local CS2 server pointed at by the tool
- [ ] Confirm MetaMod + CounterStrikeSharp base installs are present/installable
- [ ] Establish a repeatable per-mode test script: deploy → start → join → verify → switch away → verify cleanup

### 2.2 Per-mode verification matrix
Vanilla modes (no managed plugins; verify `gameinfo.gi` is *unpatched*):
- [ ] Competitive
- [ ] Casual
- [ ] Wingman
- [ ] 3v3
- [ ] 4v4
- [ ] 1v1
- [ ] Arms Race
- [ ] Demolition

Plugin-backed modes (verify deploy, verify markers, verify defining behaviour):
- [ ] Deathmatch — CS2Fixes (MetaMod); spawns work on the 4 supported maps
- [ ] Retakes — MatchZy; `matchzy_retakes_mode 1` active, retake rounds play correctly
- [ ] Jailbreak — CS2Fixes (MetaMod); warden/prisoner ruleset
- [ ] Practice — MatchZy; practice/match flow
- [ ] Warcraft — WarcraftPlugin; classes, XP, items, SQLite (`e_sqlite3.dll`) loads
- [ ] Zombie Escape — ZombieMod + MultiAddonManager + ZombieReborn; `zm_enable 1` wins over zombie base

### 2.3 Plugin lifecycle correctness
- [ ] gameinfo.gi auto-**patch** fires when entering a plugin mode
- [ ] gameinfo.gi auto-**unpatch** fires when returning to a vanilla mode (no `0xE0434352` crash)
- [ ] CSS modes hot-reload via `css_plugins reload` (no restart)
- [ ] MetaMod modes correctly report "restart required"
- [ ] Mode-switch cleanup removes the *previous* mode's files (manifest-driven)
- [ ] `+exec retakes` at launch and RCON `exec retakes` on live switch both apply

---

## Phase 3 — Harden & Secure ⬜
**Exit:** no unhandled failure leaves the tool in a confusing or silent state.

> 📋 Full findings in [AUDIT.md](AUDIT.md) (2026-05-28). Critical/High items below mirror it.

### 3.0 Audit fixes — Critical & High (do first)
- [x] **C1** Player-triggerable XSS — added `esc()` helper; escaped player names, ban entries, workshop titles/preview URLs, preset names, and config field values before DOM insertion
- [x] **C2/C3** Mask `admin_pin` & `sv_password` for remote clients (config_get); skip `***` sentinel on save so remote saves don't clobber sv_password
- [x] **H2** Move PIN change into `is_local` block; Security card + RCON console gated to local sessions in the SPA
- [x] **H1** Loopback-restrict `/auth/auto` (reject non-127.0.0.1/::1 callers)
- [x] **H3** `@require_local` on `/api/rcon`; validate map_name / userid / steamid; strip newlines from chat broadcast
- [x] **C4** `secrets.compare_digest` for PIN + startup-token compares
- [ ] **C4b** Enforce longer minimum PIN at setup (product decision — still 4 digits)
- [ ] **H4** Steam creds via stdin/temp-file, not argv — DEFERRED: interactive path uses a separate console (can't pipe stdin); DepotDownloader stdin-password support unverified and downloads can't be tested here. Local-only disclosure; revisit with a real Steam test account.
- [x] **H5** Frontend: on 401 → reload to login (login call excluded); SSE reconnects capped at 12 (~1 min)
- [x] **H6** Global brute-force backoff (20 fails → 300s) + remote sessions bound to origin IP. TLS for remote panel still open (product decision).
- [x] **H7** `_lifecycle_lock` (RLock) makes the start / stop / boot-ready / crash state transitions atomic
- [x] **H8** `stop_server` flips `running=False`/clears `proc` before terminate (kills spurious auto-restart)
- [x] **H9** Deleted stale `OblivionServerTool/` duplicate tree (999 MB, gitignored, broken build.bat)

### 3.1 Error-path coverage (AppCore)
- [ ] Start with no/invalid server dir → clear "Install first" prompt
- [ ] Workshop download with no Steam creds → immediate actionable error
- [ ] Expired Steam session → invalidated and re-prompts cleanly
- [ ] RCON timeout / server not ready → graceful, logged, no hang
- [ ] Port 27015 / 5000 already in use → detected and reported
- [ ] Crash + auto-restart → respects 3-attempt cap, both Popen & probe-reattached paths
- [ ] DepotDownloader missing → auto-downloads, or fails with a clear message

### 3.2 Web/remote surface
- [ ] PIN lockout: 5 fails → 300s lockout per IP, verified
- [ ] Session expiry after 8h, verified
- [ ] Remote sessions: `gslt_token` and `rcon_password` masked as `***`
- [ ] No business logic in routes — all delegate to `AppCore`
- [ ] Desktop auto-auth token is one-time and not reusable remotely

### 3.3 First-run on a clean machine
- [ ] No config file → setup wizard appears
- [ ] PIN still `1234` → `needs_setup` true, wizard enforced
- [ ] One-click CS2 install path works from scratch (~15 GB)

---

## Phase 4 — Test & Release Engineering ⬜
**Exit:** one command builds a release; an automated smoke run gates it.

### 4.1 Smoke / regression checks
- [ ] Import all modules without side effects
- [ ] Config load + save round-trip
- [ ] Flask app boots and serves `/` + `/api/state`
- [ ] Plugin table integrity test (every mode's plugins exist in copy/verify/cleanup/kind tables)
- [ ] deploy/undeploy dry-run against a temp `csgo/` fixture

### 4.2 Build pipeline
- [ ] `build.bat` produces a working `--onefile` exe
- [ ] `_resolve_plugins_base()` resolves correctly in the frozen layout (plugins bundled)
- [ ] Inno Setup installer builds and installs end-to-end
- [ ] Config writes to `%APPDATA%\Oblivion Server Tool\` when frozen (not Program Files)

### 4.3 Release flow
- [ ] Decide on code-signing (SmartScreen friction) — sign or document the warning
- [ ] GitHub release artifacts match what the in-app self-updater expects
- [ ] `check_app_update` correctly detects a newer published tag

---

## Phase 5 — Polish & 1.0 Launch ⬜
**Exit:** v1.0.0 tagged and published; BIBLE.md §7 satisfied.

### 5.1 UX pass
- [ ] Walk the golden path as a new user: download → wizard → install → start → connect
- [ ] Remote web panel pass on a phone (responsive, touch targets, auth)
- [ ] Theme/accent pass (dark/light/system) for visual regressions
- [ ] Confirm every keybind and quick action works
- [ ] **WebView2 accelerator keys** — F5/F12/Ctrl+R/Ctrl+P are owned by the Edge WebView2 host and JS `preventDefault` can't reliably suppress them, so binding those keys may trigger the host action (reload/devtools/print) instead. Disable browser accelerator keys at window creation in `main.py` (WebView2 `AreBrowserAcceleratorKeysEnabled=false` / pywebview equivalent) so configurable keybinds behave predictably on the desktop.
- [ ] **Palette footer hint (remote)** — the ⌘K palette footer still shows "Ctrl P · RCON only" to remote sessions even though RCON is now local-gated; hide that hint for non-local sessions.

### 5.2 Final docs
- [ ] README final sweep (features, requirements, getting started all accurate)
- [ ] CHANGELOG entry for 1.0.0
- [ ] Update version status notes (drop "work in progress")

### 5.3 Ship
- [ ] Bump `APP_VERSION` → `1.0.0` in `config.py`
- [ ] Update installer version string
- [ ] Tag `v1.0.0`, build, publish GitHub release
- [ ] Verify in-app update badge appears for users on older versions

---

## Backlog / Post-1.0 (unscheduled)
*Not blocking 1.0. Pull into a phase only when prioritised.*

- [ ] Scheduled tasks (cron-style auto-restart, nightly map updates)
- [ ] Server metrics / history dashboard
- [ ] Multi-server management
- [ ] Re-evaluate adding back modes whose upstream plugins regain active maintenance
- [ ] Optional secure off-LAN remote tunnel (carefully)
