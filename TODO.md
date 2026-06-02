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
- [x] **Team-size modes reworked** — arenas capped at 1v1/2v2 (K4-Arenas; 2v2 via a generated
  `round-settings` config), team matches added as MatchZy modes 3v3/4v4/5v5 (maxplayers 6/8/10).
  Fixes the old gap where 3v3/4v4 were arenas that never set a team size. *(needs in-game verify)*
- [x] **Workshop map flagging** — recommended-mode badges + tag chips on cards, self-describing
  dropdown options, mode-mismatch guard ("switch & load"), unified single map picker with a
  "Selected: …" readout. *(browser-verified)*
- [x] **Two-tier remote access** — optional **guest PIN** (Config → Security) grants a limited
  role: maps/modes + workshop downloads only; everything else admin-only via a fail-closed
  `before_request` gate. Guest UI stripped down (incl. keybinds disabled). *(tunnel-verified)*
- [x] **Remote-access docs** — Cloudflare quick-tunnel steps in [TONIGHT.md](TONIGHT.md)
  (cloudflared installed via winget); guest-PIN section for handing out limited access.
- [x] **Workshop maps root-cause fix (2026-05-30)** — `from .config import RCON_HOST` in core.py
  was binding the IP at import time, so `_resolve_rcon_host` updated `_config.RCON_HOST` but
  the import-local name never changed and `_poll_rcon_ready` kept probing the stale IP.
  Dropped the by-name import; all reads go through `_config.RCON_HOST` at call time.
  `_post_launch_sanity_check` keeps the netstat-based auto-recovery as a safety net for
  cs2.exe binding to an unexpected interface.
- [x] **Warcraft v2 — menu + chat-broadcast dispatchers (2026-05-30)** — the v0.9.1 cooldown
  helped but didn't stop the recv-queue-overflow when a single `!shop` hit a combat frame.
  Two queues now drain 1 menu open / 100 ms and 5 chat broadcasts / 50 ms.
  *Needs full-lobby live verification — Warcraft #29 below.*
- [x] **20-bug app-wide audit sweep (2026-05-30)** — four parallel agents → 7 critical, 8
  serious, 5 minor real bugs. All fixed.  Atomic `save_config` (lock+tmp+`os.replace`+fsync),
  Stop-during-backoff via `Event.wait`, RCON multi-packet sentinel, `_lan_ip` 30 s cache,
  `werkzeug.serving.make_server` to remove TOCTOU, `cancel_download` lock,
  `server_broadcast` `;`-strip, `log_save` collision-proof filename, lockout dict GC, etc.
- [x] **Resilience pass (2026-05-29/30)** — user-configurable Flask port, port-collision
  survivor (only kills our own zombies), preflight checks, bundle `.example` validation,
  exponential crash auto-restart with time-window reset.
- [x] **Log drawer Copy + Save buttons** — `navigator.clipboard` with textarea fallback;
  Save writes a timestamped + random-suffixed `oblivion_log_*.txt` to the config dir.
- [x] **Code hygiene (2026-05-30)** — `_holder_of_port` deduplicated into
  `cs2servergui/_netutils.py`, `\O` SyntaxWarning fixed, unused `RCON_HOST` import dropped
  from `web.py`, legacy plugin scrubs removed from `_PLUGIN_CLEANUP_ITEMS`.
- [x] **Behavioural test battery (2026-05-30)** — `tests/test_v092.py` covering RCON multi-
  packet sentinel, execute_retry exception widening, broadcast injection block, log_save
  uniqueness, Event.wait cancellation, _lan_ip cache TTL, input caps, save_config atomicity,
  _lifecycle_lock reentrancy, _netutils sanity, Flask route auth + 409.  22/22 passing.
- [x] **Cut v0.9.2 release (2026-05-30)** — tagged, pushed, GitHub release with binary.
- [x] **v0.9.2.1 hotfix release (2026-06-01)** — fixes the 5-second RCON regression that
  v0.9.2's multi-packet sentinel introduced (speculative trailing `_recv()` waited the full
  socket timeout for a phantom packet CS2 doesn't send), plus the workshop-download lock
  race, `_resolve_rcon_host` loopback clobber, two `current_map` writes missing the lifecycle
  lock, the `_stop_event` edge-window cancel race, and the Warcraft `ReferenceEquals` → SteamID
  equality fix in three deferred-menu sites.

### Shipped — v0.11.0 (Discord bot integration, released 2026-06-02)

Layer 1 of the Discord bot — operator-run bot bound to operator's own Discord
server.  See [DISCORD.md](DISCORD.md) for setup.  All features degrade silently
when no token configured.

- [x] Mon — Discord bot scaffolding (`cs2servergui/discord_bot.py`, gateway
      thread + queue bridge); Config card; DISCORD.md operator runbook.
- [x] Tue — Layer 1A: per-player `discord_id` on roster; `/api/veto/tokens`
      auto-DMs each elected captain their join URL; "📨 DM SENT" pill on
      link card.  Mid-day fix: SPA hydration was dropping `discord_id`
      from snapshot projection.
- [x] Wed — Layer 1B: voice-channel roster pull — modal lists every voice
      channel with member counts; click fills 10 roster slots with
      `{display_name, discord_id}`.
- [x] Thu — Layer 1C: live veto embed in operator-chosen channel; updates
      on every ban/pick; "✅ MATCH LOCKED IN" on finale.  Version bump
      0.10.2 → 0.11.0; tag + release.

147/147 tests green (28 v092 + 61 veto + 58 veto-api).

---

### Shipped — v0.10.2 (online-primary polish phase, released 2026-06-01)

**Audit-driven release.**  Five agents audited the tool against online-primary use
(mobile responsiveness, online workflow gaps, feature integrations, pre-v0.10.0 surface,
cross-cutting concerns) and produced ~35 findings.  v0.10.2 ships the BLOCKERs +
the three cross-cutting investments + the highest-leverage workflow gaps in one
release, scoped to four working days so Friday is real testing not finishing.

**Day 1 — Mon (mobile + workflow blockers):**
- [ ] CSS responsive pass — single `@media (max-width: 640px)` block:
      - sidebar collapses to hamburger drawer
      - `.login-card`, `.connect-popover`, `.palette` clamped to `min(380px, calc(100vw - 16px))`
      - all `.btn` min-height 44 px (Apple HIG)
      - `clamp()` on big finale titles
      - `visibilitychange` SSE-reconnect handler
- [ ] Captain finale embeds `connect <ip:port>; password X` + Copy button (the
      workflow handoff that was missing)
- [ ] `/api/veto/finale` refuses if `core.current_mode` not in MatchZy modes
      (3v3 / 4v4 / 5v5 / Competitive); response includes `matchzy.precheck`

**Day 2 — Tue (pre-flight errors + local-only + role pill):**
- [ ] `/api/server/start` returns 4xx with preflight reason (port held / plugin
      missing / Steam creds expired)
- [ ] `boot_error` field in `/api/state` (stuck boots visible remotely)
- [ ] Hide CS2-update + App-update badges + CS2 server-update modal for non-local
- [ ] App self-updater swallows GitHub Releases 404 silently (private repo)
- [ ] Log drawer hidden for guest role (kills the 12-retry SSE hammer)
- [ ] Role pill in header (admin / guest / captain)
- [ ] LAN IP row hidden in status bar + Connect popover for `!is_local`

**Day 3 — Wed (cross-cutting investments):**
- [ ] Unified SSE transport module — exponential backoff (1→2→4→8→30 s capped),
      `online`/`visibilitychange` re-arm, header status pill
- [ ] `/api/capabilities` endpoint returning `{role, is_local, can: [...]}`
- [ ] Local-only buttons render `disabled` + tooltip "Local only — ask the host"
      instead of click-then-403
- [ ] `api.js` retry/timeout layer (10 s AbortController, one retry on network
      error, sticky error toasts)
- [ ] Push `/api/state` over SSE so 3 s polling dies (140 RTTs/min → ~1)

**Day 4 — Thu (polish + history + webhook + ship):**
- [ ] Captain limbo screen ("Operator collecting votes — Team A: 3/5 in")
- [ ] Rematch button on Complete page (preserves teams, resets veto)
- [ ] Last-action attribution in `/api/state` (`{who, what, when}`)
- [ ] Match history — last 5 completed sessions to `oblivion_matches.json`
- [ ] Discord webhook on finale (operator pastes webhook URL → finale POSTs
      embed to channel)
- [ ] Full regression (123/123 → target ~145 with new cases)
- [ ] Rebuild .exe via `build.bat` (now correctly using `python -m PyInstaller`)
- [ ] Tag v0.10.2, GitHub release with binary, update `pull-latest.bat` references

**Explicitly cut from v0.10.2 (deferred or won't-do):**
- [ ] Animation rewrite — parked at operator's request
- [ ] "Go Online" header panel with cloudflared generator — defer to v0.10.3
- [ ] Public read-only spectator URL — defer
- [ ] Roster presets save/load — defer
- [ ] MatchZy cvar editor (overtime / max-rounds) — defer
- [ ] Bulk SteamID paste — defer
- [ ] Browser push notifications (service worker) — defer
- [ ] Tournament brackets — won't-do until 5+ matches felt the pain
- [ ] In-app chat — won't-do (Discord exists)
- [ ] Magic-link auth — won't-do (no email infra)
- [ ] Public REST/webhook API — won't-do until external consumer asks

---

### Shipped — v0.10.1 (online-primary improvements, released 2026-06-01)

- [x] **Captain Ready button** — `_renderVetoFinaleCaptain` SPA renderer with
      READY toggle + opponent status display; admin's launch button arms green
      when both ready; admin can ack-on-behalf by clicking ready slot;
      Shift+Click overrides both-ready gate; optional auto-launch config toggle
- [x] **Public Share URL override** — `core.public_share_url` config field;
      when set, captain join URLs build from this base instead of
      `public_ip + port` (the Cloudflare-tunnel fix)
- [x] **Copy-for-Discord button** — pre-formatted captain-addressed message
      ready to paste into a DM; prefers Public URL, falls back to LAN
- [x] Build fix: `python -m PyInstaller` + `--collect-all segno` so QR codes
      actually render in the frozen .exe (multi-Python env was using the
      wrong interpreter and dropping segno)
- [x] Defensive `try/except ImportError` around `import segno` in `web.py:veto_qr`
- [x] **`pull-latest.bat`** self-service updater for grabbing the latest .exe
      from the private repo's GitHub Release via `gh` CLI

123/123 tests green (22 v092 + 54 veto + 47 veto-api).

---

### Shipped — v0.10.0 + v0.10.0.1 hotfix (map-veto match setup, released 2026-06-01)

**v0.10.0.1 hotfix (same day):** captain-link QR codes failed to render
in the frozen `.exe` because `--hidden-import segno` only grabbed the
top-level module, not its 6 submodules.  Fixed by switching to
`--collect-all segno` in `build.bat` + `OblivionServerTool.spec`, plus
defensive `try/except ImportError` around `import segno` inside the
`/api/veto/qr` handler so any future bundling regression of a
pure-Python dep returns useful JSON rather than the silent broken-image
icon.

---


*Full spec in [VETO.md](VETO.md); detailed prose in [CHANGELOG.md](CHANGELOG.md) → v0.10.0.
Layered build plan with v0.10.0 = Layer 0 (core veto), v0.11.0 = Layer 1 (Discord bot).*

- [x] **`VetoSession` model + state machine** (`cs2servergui/veto.py`, 365 lines) — roster,
  teams, votes, captain tokens, mode (BO1/BO3/BO5), map pool, sequence, results, with
  `_LEGAL_TRANSITIONS`-enforced state machine. *(Day 1 — `c5bd7b8`)*
- [x] **API endpoints** in `web.py` — 15 routes covering roster / distribute / vote /
  generate-links / captain-join / ban / pick / finale / reset; SSE live mirror via
  `queue.Queue` per subscriber; captain role added to `_role_gate` allowlist. *(Day 2 —
  `8e1add4` + polish `9877b15`)*
- [x] **Frontend port** — `_prototypes/veto.html`'s 5-stage flow into the SPA as a dedicated
  Veto tab; 8 stage renderers + `api.veto.*` namespace + SSE subscribe/cleanup; bundled map
  thumbnails via existing `/api/maps/thumb/<name>` proxy. *(Day 3 — `74c0f49`)*
- [x] **Captain link delivery** — LAN + Public link per captain (mirrors Connect popover),
  Copy button, **QR code** via segno-backed `/api/veto/qr` endpoint (single-use, scoped
  tokens enforced server-side; unknown-token rejection blocks proxy abuse). *(Day 4 —
  `7561d1b`)*
- [x] **Cinematic finale** — title slide-up, staggered map reveal, accent-glow pulse on
  decider, 30-piece CSS confetti shower; three JS gates ensure each animation fires
  exactly once. *(Day 5 — `b32be7e`)*
- [x] **`tests/test_veto.py`** — state-machine unit tests (34/34). *(Day 1)*
- [x] **`tests/test_veto_api.py`** — Flask test_client integration tests (31/31 after
  Day 4's 8 QR cases + Day 6's 6 MatchZy cases). *(Day 2 + Day 4 + Day 6)*
- [x] **Day 6: MatchZy handoff** — `/api/veto/finale` atomically writes
  `<csgo>/cfg/MatchZy/<matchid>.json` (with `_oblivion_meta` stripped from disk
  but preserved in API response), then `matchzy_loadmatch <basename>` via RCON.
  Three-way outcome (file fail → 500; RCON fail → 200 + `matchzy.error` + session
  still completes; full success → 200 + `matchzy.loaded`).  SPA finale updated
  with real-time status + retry button.
- [x] **Day 7: Polish + extra unit tests + tag v0.10.0** — `APP_VERSION` 0.9.2.1 →
  0.10.0, `installer.iss` version bumped, +15 edge-case unit tests in `test_veto.py`
  (BO5 sequence, steamidless players, matchid format, revoke edge cases, perform_step
  post-finale, complete-state gate, state-graph reachability) and +6 in
  `test_veto_api.py` (QR public-no-IP, finale double-call, snapshot shape, distribute
  pre-roster, concurrent finale).  Real bug fixed by adversarial test: finale called
  twice on `complete` session was 500 (uncaught `InvalidVetoTransition`) — now clean
  400.  108/108 tests green.
- [x] **Build hardening:** `OblivionServerTool.spec` + `build.bat` now include `segno`
  and `cs2servergui.veto` in `--hidden-import`.  PyInstaller's static analyser misses
  both (segno is lazy-imported inside `/api/veto/qr`; `cs2servergui.veto` enters via
  `from . import veto as _veto` inside a function in `web.py`).
- [ ] **Follow-up: `issue_tokens` idempotency.**  Currently re-calling
  `issue_tokens` from `links` ROTATES both tokens, silently invalidating
  any URL already shared with captains.  The SPA only calls it once per
  session, but a browser refresh during the links stage would trigger
  the rotation.  Make it return the existing tokens if already issued
  (test_veto.py pins the current rotating behaviour so the fix shows up
  cleanly in the diff).

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
- [x] **Restart-server-on-crash — hardened (2026-05-30).** The post-friends-night resilience
  pass replaced the fixed 5 s backoff with exponential (5 → 15 → 45 s), added a 5-minute
  time-window reset so a long-stable session forgives the prior burst, and made the backoff
  cancellable via `Event.wait()` so a user Stop during the delay is honoured. Still surfaces
  via the `auto_restart_on_crash` config toggle; consider adding a UI badge for "last restart"
  history if the cap-hit becomes a regular issue.
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
- [x] Sanity-check `MODE_SETTINGS` rulesets for all 16 modes — all present with game_type/game_mode/maxplayers
- [ ] Confirm `_CSS_HOST_DLLS` filter list is still accurate vs. current CSS host — *runtime check, deferred*

### 1.3 Repo hygiene
- [ ] Review uncommitted/untracked items from git status (`build_log.txt`, stray DLLs, `cfg/`, `data/`, `characters/`)
- [ ] Decide what belongs in the bundle vs. `.gitignore` vs. deletion
- [ ] Commit the Phase 0 + Phase 1 changes with a clear message

---

## Phase 2 — Verify Every Mode ⬜
**Exit:** all 16 modes confirmed working on a real server (matrix fully checked).

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
- [ ] Arms Race
- [ ] Demolition

Plugin-backed modes (verify deploy, verify markers, verify defining behaviour):
- [ ] 1v1 — K4-Arenas; pure 1v1 ladder (plugin default rounds)
- [ ] 2v2 — K4-Arenas; arenas run **2-per-side** (generated `round-settings`, TeamSize 2)
- [ ] 3v3 / 4v4 / 5v5 — MatchZy team matches; lobby caps at maxplayers 6 / 8 / 10
- [ ] Deathmatch — CS2Fixes (MetaMod); spawns work on the 4 supported maps
- [ ] Retakes — B3none RetakesPlugin + RetakesAllocator; retake rounds form (bot fill), scramble after 3 T wins
- [ ] Jailbreak — CSS Jailbreak plugin **only** (CS2Fixes removed — caused native crash); warden/prisoner ruleset
- [ ] Practice — MatchZy; practice/match flow
- [ ] Warcraft — WarcraftPlugin + ModelPrecacher; classes/XP/items, Barbarian models render, menus readable on 4:3 & 16:9
- [x] Zombie Escape — ZombieMod + MultiAddonManager + ZombieReborn; `zm_enable 1` wins over zombie
  base. *Confirmed in-game 2026-05-29 (needed `-disable_workshop_command_filtering` — now forced for the mode).*

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
- [x] **Arena team size per mode — RESOLVED (Unreleased).** Arenas are now capped at 1v1/2v2:
  `1v1` uses the plugin's default (pure 1v1), `2v2` deploys a generated `round-settings` config
  forcing TeamSize 2 (`_apply_arena_size`). The old 3v3/4v4 became MatchZy team matches instead
  of arenas. (3v3+ *arenas* are intentionally not offered.)
- [ ] Scheduled tasks (cron-style auto-restart, nightly map updates)
- [ ] Server metrics / history dashboard
- [ ] Multi-server management
- [ ] Re-evaluate adding back modes whose upstream plugins regain active maintenance
- [~] Optional secure off-LAN remote tunnel — Cloudflare quick-tunnel flow documented in
  [TONIGHT.md](TONIGHT.md) + README; a built-in/managed tunnel (stable URL) is still future work.
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
