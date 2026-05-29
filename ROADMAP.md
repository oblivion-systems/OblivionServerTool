# ROADMAP — Oblivion Server Tool

> The **plan**: how we get from where we are (v0.9.1) to a stable, fully tested
> **v1.0.0** and beyond. This is intentionally rough — it sets direction and sequence,
> not exact dates. The granular, checkable steps live in [TODO.md](TODO.md); the *why*
> behind it all lives in [BIBLE.md](BIBLE.md).

---

## Where We Are — v0.9.1 (current)

Core features are stable and the UI has been comprehensively redesigned (theming,
keybinds, settings, workshop UX). Recent foundational work:

- **Retakes rebuilt on B3none cs2-retakes + RetakesAllocator** — the abandoned CS2Retake
  plugin was ripped out; an interim "MatchZy retakes" plan was dropped (MatchZy has no
  retakes feature).
- **Plugin bundles normalised** — every bundle now mirrors the `csgo/` layout, reducing
  all copy rules to a uniform pattern.
- **Codebase indexed** — full structural map captured in [INGEST.md](INGEST.md).
- **Live-fire fixes** — Jailbreak native-crash fix, Warcraft Barbarian model precacher,
  workshop download progress+verify, and command-filter automation (see
  [CHANGELOG.md](CHANGELOG.md) → Unreleased and [TODO.md](TODO.md) → Pending / In-Flight).

Known gap: a large batch of fixes is **uncommitted and partly unverified in-game**, and
several modes have not been verified end-to-end on a live server.

---

## The Destination — v1.0.0

The first release we will call **stable and fully tested**. See BIBLE.md §7 for the
definition of "done". Getting there is the entire focus of the phases below.

---

## Phase Map

The road to 1.0 is five phases. They are roughly sequential but Phase 1 and 2 can overlap.

```
 P1 ─ Stabilise the foundation   (correctness: code + docs match reality)
 P2 ─ Verify every mode          (each game mode boots & plays on a real server)
 P3 ─ Harden & secure            (error paths, remote surface, edge cases)
 P4 ─ Test & release engineering (smoke path, build, installer, signing)
 P5 ─ Polish & 1.0 launch        (UX pass, docs, ship)
        │
        └─▶ Post-1.0 (future, non-blocking)
```

---

### Phase 1 — Stabilise the Foundation
**Goal:** the code and the docs tell the same true story.

- Keep docs in sync with the code: README/CHANGELOG/TODO now describe **B3none** Retakes
  (done), the Jailbreak fix, the Warcraft `ModelPrecacher`, and the workshop changes.
- Audit the remaining `_PLUGIN_*` tables in `core.py` for leftover references to removed
  plugins or paths.
- Confirm `MODE_SETTINGS` rulesets are correct per mode (Retakes already fixed to
  competitive `game_mode 1`).
- Commit the in-flight batch once play-tested (see TODO → Pending / In-Flight).

**Exit criteria:** README, CHANGELOG, and code agree; no dead plugin references remain.

---

### Phase 2 — Verify Every Mode
**Goal:** every one of the 16 game modes boots, deploys its plugins, and is playable on a
real CS2 server.

- Stand up a clean local server and walk each mode: deploy → start → join → confirm the
  mode's defining behaviour works → switch away → confirm cleanup.
- Pay special attention to plugin-backed modes: Retakes, Practice, Jailbreak, Deathmatch,
  Warcraft, Zombie Escape.
- Verify `gameinfo.gi` auto-patch on entering a plugin mode and auto-unpatch on returning
  to vanilla (the `0xE0434352` CLR crash guard).
- Verify hot-reload vs. restart-required logic fires correctly per `_PLUGIN_KIND`.

**Exit criteria:** a checked-off matrix of all 16 modes, each confirmed working.

---

### Phase 3 — Harden & Secure
**Goal:** the tool behaves well when things go wrong, and the remote surface is safe.

- Walk every error path in `AppCore`: missing server dir, no Steam creds, expired session,
  RCON timeout, port in use, crash + auto-restart.
- Audit the web layer: PIN lockout, session expiry, credential masking for remote
  sessions, no business logic leaking into routes.
- Validate first-run setup on a truly clean machine (no config, no server installed).
- Confirm crash monitor handles both Popen-started and probe-reattached servers.

**Exit criteria:** no unhandled failure leaves the tool in a confusing/silent state.

---

### Phase 4 — Test & Release Engineering
**Goal:** regressions get caught before users do, and builds are reproducible.

- Establish an automated smoke path (at minimum: import, config load, Flask boot, plugin
  table integrity, deploy/undeploy dry-run).
- Verify the PyInstaller build (`build.bat`) produces a working `--onefile` and the
  `_resolve_plugins_base()` paths resolve in the frozen layout.
- Verify the Inno Setup installer end-to-end.
- Decide on code-signing for the exe/installer (SmartScreen friction).
- Wire up the GitHub release flow that the in-app self-updater reads.

**Exit criteria:** one command builds a release; a smoke run gates it.

---

### Phase 5 — Polish & 1.0 Launch
**Goal:** the first-run experience is clean enough to ship to strangers.

- Full UX pass on the golden path (download → setup wizard → install → start → connect).
- Responsive/remote panel pass on a phone.
- Final docs sweep; bump `APP_VERSION` to `1.0.0`; tag and release.

**Exit criteria:** v1.0.0 tagged and published; BIBLE.md §7 satisfied.

---

## Post-1.0 — Future Directions (non-blocking, unscheduled)

Candidate ideas to revisit only after 1.0 ships. None are commitments.

- **Map Veto / Match Setup tab** — guided roster → teams → captain vote → captain links →
  BO1/3/5 veto, served by the tool so captains can veto from their own devices. Full spec +
  open decisions in [VETO.md](VETO.md); working prototype at `_prototypes/veto.html`.
- Scheduled tasks (auto-restart on a cron, nightly map updates).
- More plugin-backed modes, re-evaluated for upstream health each release.
- Server metrics / lightweight dashboard (player count over time, uptime history).
- Multi-server management from one panel.
- Optional secure tunnel for true off-LAN remote admin (carefully — see BIBLE.md §5.7).
  *(Cloudflare quick-tunnel flow now documented in [TONIGHT.md](TONIGHT.md); a managed/stable
  tunnel and the two-tier guest/admin role split are done.)*
- **Custom Warcraft menu (recompile).** The in-game `!class`/`!skills`/`!shop` menus are
  rendered by the plugin's **own** menu code, which **enlarges the highlighted item's font** —
  opening a gap before its description and clipping tall pages. This is **compiled in**, not
  reachable via `en.json` or CS2MenuManager config (those menus largely don't route through
  CS2MenuManager). The only fix is a recompile; the smallest version is making the highlighted
  item the same font size as the rest (the installed plugin is NightFuryPrime's fork v4.1.1).
  Deferred by choice — accepted as-is for now. Full detail + checklist in TODO → Backlog.

---

## How This Roadmap Is Used

- **BIBLE.md** sets the principles every phase must respect.
- **ROADMAP.md** (this file) sets the sequence of phases and their exit criteria.
- **TODO.md** breaks each phase into checkable items and is updated as work lands.

When a phase's exit criteria are met, mark it complete in TODO.md and move to the next.
