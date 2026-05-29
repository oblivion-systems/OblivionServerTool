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

## Pending / In-Flight (working memory)
*Everything below **shipped in v0.9.1** (committed, pushed, released 2026-05-29). What
remains is **in-game verification** of the items that couldn't be tested here, plus a few
deferred loose ends. Full prose in [CHANGELOG.md](CHANGELOG.md) → v0.9.1.*

### Post-v0.9.1 — committed + pushed, awaiting a v0.9.2 tag
*Full prose in [CHANGELOG.md](CHANGELOG.md) → Unreleased.*
- [x] **CS2 update no longer creates a 64 GB duplicate install** — dropped `+force_install_dir`;
  steamcmd now updates the manifest-tracked `steamapps\common` install in place. Reclaimed ~64 GB.
- [x] **Update badge self-clears (no relaunch) + verifies** the new buildid against the public build.
- [x] **Update path hardened** — timeout on steamcmd.zip download; "still working" warnings re-arm.
- [ ] **Cut the v0.9.2 release** — bump `APP_VERSION` → `0.9.2` in `config.py`, tag `v0.9.2`, push,
  create the GitHub release. (Deferred until you decide to ship; harmless to sit on while private.)

### Shipped in v0.9.1 — confirmed in-game
- [x] **Jailbreak crash fixed** — dropped CS2Fixes (`zombie`) from the Jailbreak mode; native
  AV from CS2Fixes + CSS Jailbreak conflict. *Confirmed in-game.*
- [x] **Warcraft Barbarian models fixed** — new bundled `ModelPrecacher` CSS plugin precaches
  `tm_phoenix_heavy` / `ctm_heavy` (source in `_plugins_src/ModelPrecacher/`). Loose-file
  approach proven insufficient and reverted. *Confirmed in-game.*
- [x] **Warcraft `!buy` fix** — removed the `buy` shop-trigger alias that shadowed native `buy`.

### Shipped in v0.9.1 — NOT yet verified in-game (play-test these)
- [~] **Warcraft menu** — shipped a CS2MenuManager `config.toml` (WasdMenu, purple, 4:3-safe
  position). **Finding:** the `!class`/`!skills`/`!shop` menus are rendered by the plugin's own
  compiled menu (not CS2MenuManager), so most of this config has limited effect on them. The
  remaining vertical clip (highlighted item's font expands → gap before description) is
  **compiled-in and can't be fixed by config** — deferred to Backlog (recompile). The
  `config.toml` is harmless; left in place for any menu that does route through CS2MenuManager.
- [~] **Workshop download tracker + verify** — staged `<id>.partial` → verify (vpk + ≥99% size)
  → promote; per-MB `dl_progress`; determinate bar. Needs a real download to exercise end-to-end.
- [~] **Workshop command-filter automation** — auto-detect from Steam description + per-map
  override chip + Scan button + launch flag. Needs the user to click **Scan** (or rebuild) to
  populate flags. Scan already found **2** maps that need it: `3699317461` (VERSUS),
  `3728657716` (Lake Flying Scoutsman).
- [~] **Retakes = B3none** (spawn comma-fix, bot fill, scramble=3), **Zombie ZM fix**,
  **mode-switch hardening**, **cheat sheet + empty states**, **theme darkening** — all landed,
  awaiting a play-test pass.

### Loose ends / deferred
- [ ] **When making the repo public:** the in-app self-updater is **dormant while private** —
  it fetches the public GitHub releases API (`APP_API_URL`) and links to the releases page,
  both of which 404 for users without repo access. On going public, verify the "⬆ App update"
  badge fires and the release download opens. (Repo is private for now, by choice.)
- [x] **"Use bots" toggle — global.** Gates Arenas (excludes K4-Arenas-Bots when off) and
  Retakes (rewrites the deployed cs2-retakes.cfg to `bot_quota 0` + `bot_kick` when off).
  Deathmatch has no plugin bot-fill in our bundle, so the toggle doesn't apply there (base
  server bots only). Toggle lives in Config → Bots.
- [x] **Commit + release the batch** — shipped as v0.9.1 (committed, pushed, GitHub release).
- [ ] **`-condebug` log growth** — `csgo/console.log` now grows across sessions; consider
  trimming/rotating, or make it a toggle.
- [x] **Broken workshop folders cleaned (2026-05-29)** — removed empty `233903603` and four
  obsolete CS:GO-era `.bsp` maps (verified via re-download they were CS:GO-format, unloadable in
  CS2). `3326291211` / `3604289538` were healthy CS2 `.vpk` maps and left in place.
- [ ] **WebView2 accelerator keys** & **palette footer hint for remote** — see Phase 5.1 below.
- [x] **Jailbreak workshop tag filter** — loosened: added `classic` to `MODE_WORKSHOP_TAGS["Jailbreak"]`
  so jb_ maps (tagged Classic/Map) pass the filter.
- [ ] **`.plugin-downloads/vrf/`** — Source2Viewer-CLI kept for future VPK extraction (gitignored).

---

## Phase 0 — Groundwork ✅
*Completed this session. Recorded here for traceability.*

- [x] Remove all traces of the old CS2Retake plugin (DLLs, spawn JSONs, SQLite, configs)
- [x] Retakes runs on **B3none cs2-retakes + RetakesAllocator** — note: an interim plan to use
  "MatchZy retakes mode" was dropped (MatchZy has no retakes feature)
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
- [x] Update README plugin table — Retakes is **B3none cs2-retakes + RetakesAllocator**
- [x] Verify every other row of the README plugin table against `_MODE_PLUGIN_NAMES` — note: added missing Arenas (1v1/3v3/4v4) row; corrected Deathmatch & Jailbreak to show CS2Fixes + CSS plugin
- [x] Add CHANGELOG entry: retakes→MatchZy migration + bundle restructure
- [ ] Confirm README feature list matches actual `web.py` routes (no phantom features)

### 1.2 Code/data integrity audit
- [x] Grep the whole tree for residual `CS2Retake` / SQLite.Interop refs — none in code (clean)
- [x] Confirm `_PLUGIN_COPY_RULES` keys all have matching source dirs under `plugins/` (smoke test)
- [ ] Confirm `_PLUGIN_CLEANUP_ITEMS` covers every path any plugin writes into `csgo/` — *needs a real deploy to enumerate; partial*
- [x] Confirm `_PLUGIN_VERIFY_FILES` marker files actually ship in each bundle (smoke test)
- [x] Sanity-check `MODE_SETTINGS` rulesets for all 14 modes — all present with game_type/game_mode/maxplayers
- [ ] Confirm `_CSS_HOST_DLLS` filter list is still accurate vs. current CSS host — *runtime check, deferred*

### 1.3 Repo hygiene
- [ ] Review uncommitted/untracked items from git status (`build_log.txt`, stray DLLs, `cfg/`, `data/`, `characters/`)
- [ ] Decide what belongs in the bundle vs. `.gitignore` vs. deletion
- [ ] Commit the Phase 0 + Phase 1 changes with a clear message

---

## Phase 2 — Verify Every Mode ⬜
**Exit:** all 14 modes confirmed working on a real server (matrix fully checked).

> **Static side is green** (via [`tests/smoke.py`](tests/smoke.py)): every mode's plugins exist
> across the copy/verify/kind tables and ship in the bundle, so deploys won't fail on missing
> files. What's left is **in-game runtime** verification (a human must join) — the matrix below.
> For the friends night, prioritise the modes you'll actually play first.

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
- [ ] Retakes — B3none RetakesPlugin + RetakesAllocator; retake rounds form (bot fill), scramble after 3 T wins
- [ ] Jailbreak — CSS Jailbreak plugin **only** (CS2Fixes removed — caused native crash); warden/prisoner ruleset
- [ ] Practice — MatchZy; practice/match flow
- [ ] Warcraft — WarcraftPlugin + ModelPrecacher; classes/XP/items, Barbarian models render, menus readable on 4:3 & 16:9
- [ ] Zombie Escape — ZombieMod + MultiAddonManager + ZombieReborn; `zm_enable 1` wins over zombie base

### 2.3 Plugin lifecycle correctness
- [ ] gameinfo.gi auto-**patch** fires when entering a plugin mode
- [ ] gameinfo.gi auto-**unpatch** fires when returning to a vanilla mode (no `0xE0434352` crash)
- [ ] CSS modes hot-reload via `css_plugins reload` (no restart)
- [ ] MetaMod modes correctly report "restart required"
- [ ] Mode-switch cleanup removes the *previous* mode's files (manifest-driven)
- [ ] Plugin-swapping mode switches restart cleanly via `_restart_into` (stop → wait-for-exit → start)
- [ ] B3none retakes `cfg/cs2-retakes/retakes.cfg` is exec'd by RetakesPlugin (bot fill applies)

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
> Harness: [`tests/smoke.py`](tests/smoke.py) — run `python tests/smoke.py` (config isolated to a temp file).
- [x] Import all modules without side effects
- [x] Config load + save round-trip (isolated temp config)
- [x] Flask app boots and serves `/`; `/api/state` enforces auth
- [x] Plugin table integrity test (every mode's plugins exist in copy/verify/kind tables + ship in the bundle)
- [ ] deploy/undeploy dry-run against a temp `csgo/` fixture — *not yet (needs a temp csgo + path redirection)*

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
- [ ] **WebView2 accelerator keys** — F5/F12/Ctrl+R/Ctrl+P are owned by the Edge WebView2 host and JS `preventDefault` can't reliably suppress them, so binding those keys may trigger the host action (reload/devtools/print) instead. *Deferred:* pywebview 5.x doesn't expose `AreBrowserAcceleratorKeysEnabled`; reaching the underlying CoreWebView2.Settings is fragile + needs desktop testing. Revisit at a desktop session (or just mark those keys reserved in the keybind UI).
- [x] **Palette footer hint (remote)** — the ⌘K palette footer "Ctrl P · RCON only" hint is now hidden for non-local sessions (RCON is local-gated).

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

- [ ] **Map Veto / Match Setup tab** — full spec + open decisions in [VETO.md](VETO.md);
  prototype at `_prototypes/veto.html`. Self-contained Veto tab; needs server-side session +
  SSE for the captain links to work across devices. Resolve the 4 open decisions first.
- [ ] **Arena team size per mode (3v3/4v4 actually 3v3/4v4).** Today 1v1/3v3/4v4 all deploy the
  same "arenas" bundle and don't configure K4-Arenas' arena *team size*, so all three run the
  plugin's default arena behaviour — only the player cap differed (now all 16). K4-Arenas
  supports 2v2/3v3/etc., so the fix is to ship/generate a per-mode K4-Arenas config setting the
  arena size (1/3/4 per side) when deploying each mode. Latent label/config gap, not a fault.
- [ ] Scheduled tasks (cron-style auto-restart, nightly map updates)
- [ ] Server metrics / history dashboard
- [ ] Multi-server management
- [ ] Re-evaluate adding back modes whose upstream plugins regain active maintenance
- [ ] Optional secure off-LAN remote tunnel (carefully)
- [ ] **Custom Warcraft menu — recompile the plugin** *(config can't fix this; deferred by choice)*
  - **Root cause (confirmed 2026-05-28):** the `!class`/`!skills`/`!shop` menus are rendered by
    the plugin's **own** menu code (the installed DLL contains both CS2MenuManager *and* a custom
    menu — `OptionDisplay`/`SubOptionDisplay`/`FontSizes`/`OpenMainMenu`). The custom menu
    **enlarges the highlighted item's font**, which opens a gap before its description and makes
    the page taller → the last item's description clips. This is **compiled in** — not reachable
    via `en.json` (no item font-size markup) or CS2MenuManager `config.toml` (color/position only,
    and these menus largely don't even route through CS2MenuManager). So our config tuning had
    limited effect on these specific menus.
  - **Smallest fix (preferred):** fork the plugin and make the highlighted item use the **same
    font size** as the rest (and/or tighten name↔description spacing). Tiny change, no library swap.
  - Installed plugin is **NightFuryPrime's fork v4.1.1** (added CS2MenuManager on Wngui's base) —
    build from *that* fork's source to match the DLL.
  - [ ] Confirm NightFuryPrime/CS2-Warcraft-Plugin source is public + builds against CSS 1.0.368
  - [ ] Change the highlighted-item font size to match unselected; verify 5 items + descriptions fit
  - [ ] (Optional, bigger) swap the menu to [CS2ScreenMenuAPI](https://github.com/T3Marius/CS2ScreenMenuAPI)
        for `Size`/`Spacing`/`Background` control — de-risk flicker vs the XP HUD first
  - [ ] Bundle the forked DLL + record the patch so upstream updates can be re-applied
  - Note: the CS2MenuManager `config.toml` we shipped (WasdMenu, purple, 4:3-safe position) is
    harmless and may still affect any menu that *does* route through CS2MenuManager — leave it.
