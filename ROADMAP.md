# ROADMAP — Oblivion Server Tool

> The **plan**: how we get from where we are (v0.9.2.1 shipped) to a stable, fully
> tested **v1.0.0** and beyond.  This is intentionally rough — it sets direction and
> sequence, not exact dates.  The granular, checkable steps live in
> [TODO.md](TODO.md); the *why* behind it all lives in [BIBLE.md](BIBLE.md).

---

## Where We Are — v0.9.2.1 (released 2026-06-01, hotfix on v0.9.2)

Core features are stable.  Since v0.9.1 the focus has shifted from feature work to
**correctness, observability, and resilience under real load**.

**Headline post-v0.9.1 work:**

- **Workshop maps fix (the actual root cause)** — `from .config import RCON_HOST`
  was binding the IP at import time inside `core.py`, so `_resolve_rcon_host`
  updated `_config.RCON_HOST` but the import-local name never changed and
  `_poll_rcon_ready` kept probing the stale IP forever.  Dropped the by-name import;
  every read is now `_config.RCON_HOST` at call time.  A netstat-based auto-recovery
  in `_post_launch_sanity_check` stays as a safety net for cs2.exe binding to an
  unexpected interface (Hyper-V / Docker / VPN tap adapter).
- **Warcraft menu + chat-broadcast dispatchers** — the v0.9.1 per-player cooldown
  helped but didn't stop the recv-queue-overflow when a single `!shop` collided with
  a combat-heavy frame.  Two new queues drain at 1 menu open / 100 ms and 5 chat
  broadcasts / 50 ms, fanning bursts across multiple frames.  Audit follow-ups: kill
  the new timers in `Unload`, hoist `WarcraftPlugin.Instance` into a local before
  enqueue, re-resolve `WarcraftPlayer` from the slot controller at drain time so a
  recycled player slot doesn't pop the previous occupant's profile.
- **20-bug app-wide audit sweep** — four parallel review agents (core.py, web.py +
  frontend, main.py + config.py + rcon.py, Warcraft patches) surfaced 7 critical +
  8 serious + 5 minor real bugs.  All fixed.  Highlights: atomic `save_config`
  (lock + tmp + `os.replace` + fsync), Stop-during-backoff via `Event.wait`,
  `werkzeug.serving.make_server` to remove the port-bind TOCTOU, RCON multi-packet
  sentinel for long `status` output, `cancel_download` lock, `_lan_ip` 30s cache,
  `server_broadcast` semicolon stripping, log-save filename collision fix.
- **Two-tier remote access** — guest role (maps + modes + workshop downloads only)
  separate from admin (full control).  Brute-force lockout per-IP + global decay.
- **Team-size modes** — `1v1`/`2v2` (K4-Arenas duels capped at 2-per-side),
  `3v3`/`4v4`/`5v5` (MatchZy team matches bounded by maxplayers).  Arena ladder
  bots forced to `bot_quota_mode normal` so they fill odd slots like players.
- **Resilience pass** — user-configurable Flask port, port-collision survivor that
  only kills our own zombies, preflight checks before Start, bundle-config
  `.example` validation (caught the Zombie weapons.cfg bug), exponential 5→15→45s
  crash auto-restart with 5-min time-window reset.
- **Log drawer Copy + Save buttons** — robust clipboard with textarea fallback;
  Save writes a timestamped+random-suffixed `oblivion_log_*.txt` to the config dir.
- **Code hygiene** — `_holder_of_port` deduplicated into `cs2servergui/_netutils.py`,
  unused imports removed, SyntaxWarning fixed, legacy plugin scrubs dropped.

**Shipped 2026-06-01 — what's done:**

- ✅ v0.9.2 tagged and released to GitHub (binary + tag + notes)
- ✅ v0.9.2.1 hotfix tagged and released — fixes the 5-second RCON regression
  the multi-packet sentinel introduced, plus five other re-audit findings
  (workshop-download lock race, `_resolve_rcon_host` loopback clobber,
  `current_map` lock consistency, `_stop_event` edge-window cancel, Warcraft
  `ReferenceEquals` → SteamID equality)
- ✅ `tests/test_v092.py` behavioural battery (22/22 passing)
- ✅ Packaging polish: `_netutils` hidden-import, werkzeug pin, WebView2
  bootstrapper docs, explicit icons, `--noconfirm`

**Next milestone — v0.10.0 (map veto + match setup):**

This week's focus.  See [VETO.md](VETO.md) for the full spec.  Five-stage
match-setup flow ending in a CS2 veto board, with MatchZy handoff at the
finale.  Decisions locked in: dedicated "Veto" tab, LAN + Public captain
links, per-veto override starting from active-duty 7, Steam IDs collected
at roster for MatchZy strict mode.

**v0.11.0+ — Discord bot integration:**

User's-own-bot model (no shared hosting): voice-channel roster pull,
captain DM delivery, match-result announce, live veto embed.  Falls back
to manual + QR when not configured.  Detailed plan in [VETO.md](VETO.md)
§ Discord bot.

**Live stress test still pending (#29):**

A full-lobby Warcraft session (real players) to validate the v2 menu /
chat-broadcast dispatchers under the conditions the v1 cooldown couldn't
cover.  Can be retroactive — the dispatchers already exist in v0.9.2.1.

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
