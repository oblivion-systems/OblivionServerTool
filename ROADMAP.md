# ROADMAP — Oblivion Server Tool

> The **plan**: how we get from where we are (v0.9.2.1 shipped) to a stable, fully
> tested **v1.0.0** and beyond.  This is intentionally rough — it sets direction and
> sequence, not exact dates.  The granular, checkable steps live in
> [TODO.md](TODO.md); the *why* behind it all lives in [BIBLE.md](BIBLE.md).

---

## Where We Are — v0.12.0 (released 2026-06-06)

**Twenty-eight releases**, with v0.11.x proven in production and the
v0.12 line opened with Discord-driven team voice splits.  Day-after of
the **first tournament (2026-06-05)** delivered v0.11.26 + v0.11.27 +
v0.12.0 — closing 8 of 10 audit findings AND landing the first new v0.12
feature.  Two audit findings remain (#139 content-hashed static URLs,
#143 broadcast queue-overflow investigation) — both genuinely belong
with the v0.12 driver-abstraction work.  See
`RETROSPECTIVE_2026_06_05.md` for the tournament post-mortem.

Highlights since v0.11.0:

| Release | Theme |
|---|---|
| v0.11.1 | 8-feature polish sweep (history, "Go Online", presets, cvar editor, spectator, etc.) |
| v0.11.2 | `issue_tokens` idempotency — captain refresh doesn't kill the other captain's link |
| v0.11.3 | Active session persistence — Ctrl+Q / Windows update no longer evaporates a veto mid-flow |
| v0.11.4 | Diagnostic snapshot button + `TROUBLESHOOTING.md` |
| v0.11.5 | `/api/ping` exposes version + build flag |
| v0.11.6 | Version pill in status bar (always visible) |
| v0.11.7 | Map dropdown category tinting |
| v0.11.8 | Mode dropdown category tinting + plugin labels |
| v0.11.9 | Diag snapshot gap-fill (CS2 console.log tail, plugin verify, disk, UA, raw JSON) |
| v0.11.10 | Diag snapshot triage optimization (TL;DR auto-scan + anomaly `>` prefix) |
| v0.11.11 | Two diag bugs from real Way-3 paste (disk could-not-check + Discord `?`) |
| v0.11.12 | Plugin-verifier false positive on stale manifest |
| v0.11.13 | CS2 console.log freshness in TL;DR + frame-drop flagging |
| v0.11.14 | Host + Play perf scripts (alt-tab anti-lag toolkit) |
| v0.11.15 | Default voice channel — one-click roster pull for recurring tournaments |
| v0.11.16 | v0.11.15 adversarial-review hotfixes (double-click guard, mobile picker button, silent fallback) |
| v0.11.17 | Friday-eve thorough sweep — Tier A + B fixes (12 findings across veto/Discord/server/SPA) |
| v0.11.18 | 🔍 Browse for Veto Embed Channel ID (text-channel picker) |
| v0.11.19 | Snapshot plugin log diagnostics — CSS + MatchZy log tail + TL;DR plugin_log indicator |
| v0.11.20 | SameSite=Lax + HTML interstitial — captain link works in Discord iOS in-app browser |
| v0.11.21 | Invalidate captain HTTP sessions on /api/veto/reset |
| v0.11.22 | Stuff /api/veto/step response into _vetoState — defeat SSE-vs-API race |
| v0.11.23 | `_vetoApply` helper through every mutation handler — instant local feedback |
| v0.11.24 | No-cache headers on /static/* — bust WebView2 stale-JS cache |
| v0.11.25 | 3s polling fallback alongside SSE — belt-and-braces; the version the tournament completed on |
| v0.11.26 | Post-tournament audit cleanup — zombie captain race, interstitial cache, poll timer leak, board click double-render |
| v0.11.27 | `_vetoApply` consolidation — single point of truth for snapshot ingestion; closes audit findings #5/#7/#8/#9 |
| v0.12.0  | `/move-teams` + auto-move on Distribute — bot drags rostered players from lobby VC into their team's VC; persistent toggle, default OFF |

**208/208 backend tests green** through v0.12.0.

Full prose in [CHANGELOG.md](CHANGELOG.md).

## Previously — v0.11.1 (released 2026-06-02)

**Post-v0.11.0 polish sweep.**  Eight discrete operator-facing wins
shipped in one day on top of the Discord bot release, plus a
real-device validation checklist for pre-session.  All back-compat —
no schema or state-machine changes.  **161/161 tests green** (28 v092
+ 68 veto + 65 veto-api; +14 from v0.11.0).

| # | Item | Layer |
|---|---|---|
| 1 | Discord test buttons (verify bot wiring) | Config card |
| 2 | 📜 Match history modal | Veto header |
| 3 | "Go Online" banner (LAN-only / online-with-URL state) | Veto-idle |
| 4 | Bulk paste: `Name,SteamID,DiscordID` columns | Roster |
| 5 | Roster presets (localStorage) | Roster |
| 6 | MatchZy cvar editor (key/value rows; blank suppresses) | Config |
| 7 | 📺 Spectator URL (`/spectate` standalone page) | Veto header |
| 8 | `MOBILE_CHECK.md` — real-device validation checklist | Docs |

Parked at operator request: cinematic finale animation rewrite.

Full prose in [CHANGELOG.md](CHANGELOG.md) → v0.11.1.

## Previously — v0.11.0 (released 2026-06-02)

**Discord bot integration (Layer 1).**  Four-day push.  Operator runs
their own bot bound to their own Discord server (DISCORD.md has the
5-min setup runbook).  When configured:

  Mon — Bot scaffolding (gateway thread + queue bridge + Config card)
  Tue — Layer 1A: auto-DM captain links on /api/veto/tokens
  Wed — Layer 1B: "🎤 Pull from voice channel" fills 10 roster slots
  Thu — Layer 1C: live veto embed updates as captains ban/pick

147/147 tests at release.  All Discord features degrade silently when
no token is configured — existing Copy-for-Discord / manual roster /
spectator-via-tunnel workflows still work.

Full prose in [CHANGELOG.md](CHANGELOG.md) → v0.11.0.

## Previously — v0.10.2 (released 2026-06-01)

**Online-primary polish phase complete.**  After a five-agent audit of online-use
readiness surfaced ~35 actionable findings, v0.10.2 took the BLOCKERs + the top
three cross-cutting investments + the most-valuable workflow gaps into one
release.  Four focused days, 137/137 tests green.

| Day | Landed |
|---|---|
| Mon | Mobile responsive pass + captain connect-string handoff + mode pre-flight |
| Tue | Pre-flight error surfacing + local-only UI signposting + role pill |
| Wed | Unified SSE transport + `/api/capabilities` + `api.js` retry/timeout layer |
| Thu | Captain limbo screen + rematch + match history + Discord webhook + ship |

Explicitly cut from scope: animation rewrite (parked), "Go Online" panel,
public spectator URL, roster presets, MatchZy cvar editor, bulk SteamID
paste, tournament brackets, magic-link auth, limited remote RCON.
These either lack obvious value for the immediate online use case, add
significant scope, or are better paired with the v0.11.0 Discord bot.

Full prose in [CHANGELOG.md](CHANGELOG.md) → v0.10.2.

## Previously — v0.10.1 (released 2026-06-01)

## Previously — v0.10.1 (released earlier 2026-06-01)

Online-primary improvements addressing the realisation that LAN use is
secondary.  Captain Ready button on the finale page (replaces the broken
admin-only button captains couldn't actually use), Public Share URL config
field (Cloudflare tunnel URL base for captain links), Copy-for-Discord
button (pre-formatted captain-addressed message ready to paste into a DM).
Build infrastructure also fixed (`python -m PyInstaller` + `--collect-all
segno` so QR codes actually render in the frozen .exe).  123/123 tests.

## Previously — v0.10.0 (released 2026-06-01)

The **map-veto / match-setup feature** is live: server-side state machine
+ HTTP API + SPA Veto tab + QR captain links + cinematic finale + real
`matchzy_loadmatch` RCON handoff.  108/108 backend tests green (22 v092
+ 49 veto + 37 veto-api).  Full prose in [CHANGELOG.md](CHANGELOG.md);
spec in [VETO.md](VETO.md).

## Previously — v0.9.2.1 (released 2026-06-01, hotfix on v0.9.2)

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

**Shipped 2026-06-01 — v0.10.0:**

Seven-day build of the map-veto / match-setup feature.  All days complete:

| Day | Scope |
|---|---|
| 1 | `VetoSession` state machine + 34 unit tests (`cs2servergui/veto.py`) |
| 2 | 15 HTTP routes + SSE live mirror + captain role + 17 integration tests |
| 3 | SPA Veto tab + 8 stage renderers + `api.veto.*` namespace |
| 4 | QR codes for captain links (segno, `/api/veto/qr`) + 8 more tests |
| 5 | Cinematic finale (title rise, decider glow pulse, 30-piece confetti) |
| 6 | MatchZy match config to disk + `matchzy_loadmatch` RCON handoff + 6 tests |
| 7 | Polish + 15+6 edge-case unit tests + finale double-call bug fix + tag |

All decisions from VETO.md resolved: dedicated Veto tab, LAN + Public
captain links, per-veto pool override starting from active-duty 7,
Steam IDs collected at roster for MatchZy strict mode.  108/108
backend tests green (22 v092 + 49 veto + 37 veto-api).

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

- ~~Map Veto / Match Setup tab~~ — **promoted out of backlog**; in flight as v0.10.0
  (see "Currently shipping" above).
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
