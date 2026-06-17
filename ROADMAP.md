# ROADMAP — Oblivion Server Tool

> The **plan**: how we get from where we are (v0.9.2.1 shipped) to a stable, fully
> tested **v1.0.0** and beyond.  This is intentionally rough — it sets direction and
> sequence, not exact dates.  The granular, checkable steps live in
> [TODO.md](TODO.md); the *why* behind it all lives in [BIBLE.md](BIBLE.md).

---

## Where We Are — v0.16.13 (released 2026-06-17)

**Forty-nine releases.**  Two big arcs closed since v0.15.2:

### v0.16 — first-run UX audit (task #157)
- **v0.16.0–v0.16.3** prep waves: config backup/restore, persistent team
  profiles, dedicated History + Pre-flight + Logs pages, tournament
  templates, in-app demo browser, Discord mock-veto smoke button.
- **v0.16.4 hotfix** — `+ip 0.0.0.0` so RCON binds on all interfaces
  even on hosts with Hyper-V / WSL virtual NICs.
- **v0.16.5 / item #163** — auto-install MetaMod + CSS runtime.  No more
  "find the addons/ folder and extract it yourself".
- **v0.16.5 / item A** — Edge WebView2 bundled into the installer
  (`#if FileExists` + `tools/fetch_webview2.ps1`).  Fresh Windows 10
  friends no longer get a blank window on first launch.
- **v0.16.6** — Getting Started card on Status page, primary "Run
  Discord setup check" button, actionable "→ Fix" buttons on every
  Pre-flight row.
- **v0.16.7** — URL hotfix: the v0.16.5 hardcoded MetaMod + CSS URLs
  were 404 against current upstream.  Refreshed to today's known-good
  builds; operator override via `oblivion_config.json` still available.
- **v0.16.8** — 5 self-review fixes (1 Critical — `_gameinfo_patch_metamod`
  doesn't exist; the AttributeError was swallowed and `ok:true` returned;
  a fresh-install friend would have watched MetaMod silently never load).
- **v0.16.9 / v0.16.10** — button contrast pass after user feedback that
  unhovered Apply buttons looked greyed-out; pack/template Apply buttons
  promoted to filled accent CTAs.
- **v0.16.11 / v0.16.12** — Demolition default-map list: CS:GO mini-maps
  removed from CS2 official rotation; hybrid list now puts workshop ports
  first (Shorttrain / Bank / Sugarcane / St. Marc) with CS2 official maps
  as fallback.

### v0.16.13 — Warcraft plugin main-thread safety
Source-side adversarial re-audit of the patched WarcraftPlugin against
CSS v1.0.369 caught 4 bugs not covered by the June 1 patches:
Critical chat-command Dictionary corruption (silent freeze killer
under tournament chat-spam), dispatcher backpressure on map start,
`NativeAPI.GetEntityFromIndex` racing on a worker thread during
tournament fill, and a dormant MySQL block in the menu manager.
Source patched + DLL rebuilt + bundle refreshed.

---

## v0.15.2 milestone — "easy for anyone to add new plugins"

The Plugin Manager now covers the whole arc:
- **Self-describing plugins** via `plugin.json` (v0.15.0) — drop a folder
  into `%APPDATA%`, restart, plugin appears in the Library.
- **Community registry** (v0.15.1) — every running .exe fetches
  [`OblivionPluginRegistry`](https://github.com/jacquesvniekerk-eng/OblivionPluginRegistry)'s
  `catalog.json` on a 24h TTL; one-click install with SHA-256 + Zip-Slip
  + atomic move.
- **Uninstall + reload + URL install + update notifications + search**
  (v0.15.2) — every operator action symmetric, registered authors can
  publish without registry curation via the **📥 Install from URL** modal.

Plus the **Config tab restructure** (v0.14.2) — single-column layout with
six clearly-separated sections in operator-mental-model order, big visual
dividers, button-hover polish across the whole app.

Driver-abstraction seam from v0.13.0 still holds; v0.13.x method
migrations are paused while the Plugin Manager arc lands.  `PLATFORM.md`
documents the migration template.  See `RETROSPECTIVE_2026_06_05.md` for
the tournament post-mortem that informed the v0.12 work.

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
| v0.12.1  | Round summaries + slash-command tree — RCON-poll daemon posts per-round embeds during live MatchZy match; `/round-summaries` + `/move-teams` slash commands (closes #134 + #145) |
| v0.12.2  | SSE broadcast observability — drops counter, telemetry section in diag snapshot, queue maxsize 32 → 256 (closes audit finding #10) |
| v0.12.3  | Remote player voting via per-player tokens — bot DMs each rostered player a one-shot voting URL; minimal voter SPA view (closes #135) |
| v0.12.4  | Content-hashed `/static/*` URLs — `?v=APP_VERSION` + `Cache-Control: immutable` (closes audit finding #6 / task #139) |
| v0.12.5  | Gaming Mode toggle in Config card + scripts/ bundled into installer (closes #95 + #97) |
| v0.13.0  | **Driver abstraction seam** — `GameDriver` ABC + `CS2Driver` + `AppCore.driver` + diagnostic snapshot "Driver" section (closes #86) |
| v0.13.1  | **PLATFORM.md design doc** + worked-example migration: `install_root()` from `_csgo_dir()` (closes #84) |
| v0.13.2  | **Plugins tab** (read-only) + **Activate/Switch-to-vanilla** actions (closes #92 design phase) |
| v0.14.0  | **Plugin Manager**: Quick-Apply Packs + JSON catalog + runtime bootstrap modal + 4 audit fixes (closes #91) |
| v0.14.1  | **Live mode swap on running server** — plugin actions route through `change_map`'s restart cycle |
| v0.14.2  | **Config tab restructure** (single-column, 6 sections) + whole-app button hover polish |
| v0.15.0  | **Self-describing plugins** via `plugin.json` — derived plugin tables, bundled + local discovery |
| v0.15.1  | **OblivionPluginRegistry** fetch + in-SPA install with SHA-256 + Zip-Slip + atomic move |
| v0.15.2  | **Uninstall + Reload + URL install + Updates + Search** (closes Plugin Manager arc) |

**276/276 backend tests green** through v0.15.2.

Full prose in [CHANGELOG.md](CHANGELOG.md).

---

## Path to v1.0 (updated 2026-06-12)

The Plugin Manager arc closed; what's left is **finishing the operator
workflow**, not adding more features.  Whole-project review yielded
15 new tasks (#157-#171) layered onto the existing pre-v1.0 set.  See
[TODO.md § v1.0 Wishlist](TODO.md#v10-wishlist-added-2026-06-12-after-whole-project-review)
for the full tiered list.

### Must-have before v1.0 ships
| Task | Title |
|---|---|
| #29  | Live stress test — 10 humans + Warcraft + active match |
| #157 | First-run UX audit — fresh-operator-to-tournament walkthrough |
| #158 | Backup/restore for `oblivion_config.json` + auto-snapshot |
| #159 | Discord bot resilience soak |
| #89  | v1.0 launch posture (BSL license, donations, flip repo public) |

### Should-have (ships v1.0 noticeably weaker without)
| Task | Title |
|---|---|
| #160 | Persistent team profiles — single biggest stickiness feature |
| #161 | Match history promoted from modal to dedicated tab |
| #162 | Searchable in-app log viewer (beyond the drawer) |
| #163 | Auto-install MetaMod + CSS runtime (finish slice 5) |

### Polish (small but visible)
| Task | Title |
|---|---|
| #164 | v0.16 polish sweep — reload buttons, `deployed_at: ?`, stale docs |
| #165 | Discord "Run mock veto" smoke button |
| #166 | Document PIN auth threat model for remote sessions |
| #167 | Setup wizard learns about the Plugin Manager |

### Ideas (consider for v1.0 or push to v1.1)
| Task | Title |
|---|---|
| #168 | Tournament readiness pre-flight dashboard |
| #169 | Tournament templates — named recurring configs |
| #170 | Spectator URL polish for streamers |
| #171 | Demo browser |

### Roadmap items remaining (lower urgency)
| Task | Title |
|---|---|
| #85  | Monetization sketch (branch, don't ship) |
| #87  | TF2 driver (paused per operator) |
| #88  | FiveM driver — first non-Source game proof point |
| #93  | Linux support + headless mode |
| #94  | `oblivion/core/platform.py` seam for cross-OS support |

---

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
