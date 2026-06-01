# Oblivion Server Tool — Changelog

---

## v0.10.0.1 — 2026-06-01 (hotfix)

**One bug: captain-link QR codes failed to render in the frozen `.exe`.**

The v0.10.0 build used `--hidden-import segno` which only pulled the
top-level `segno/__init__.py` into the bundle.  When `/api/veto/qr` ran
`segno.make(url)`, segno tried to import `segno.encoder` (and consts /
utils / writers) at runtime — none of which were bundled — and raised
`ImportError`.  Flask returned a generic 500 with no body, the SPA's
`<img src="/api/veto/qr?…">` showed the broken-image placeholder, and
the operator was left guessing.

Two-part fix:
* `build.bat` + `OblivionServerTool.spec`: switched `--hidden-import segno`
  to `--collect-all segno` so all six submodules (`encoder`, `consts`,
  `helpers`, `utils`, `writers`, `cli`) ride along with the package.
* `web.py:veto_qr`: wrapped the `import segno` in a try/except that
  returns the actual error as JSON (`{"error": "QR generator not
  available in this build: ImportError(...)"}`), so future bundling
  regressions of any pure-Python dep show up as a 500-with-body rather
  than the silent broken-image icon.

Test totals unchanged from v0.10.0 (108/108) — the bug was bundling-
side, not test-discoverable without a frozen build to exercise.

---

## v0.10.0 — 2026-06-01 (release)

The **map-veto / match-setup feature** ([VETO.md](VETO.md)).  Seven-day build:
state machine + HTTP API + SPA Veto tab + QR codes + cinematic finale +
real MatchZy handoff + this release polish day.  `APP_VERSION` bumped
0.9.2.1 → 0.10.0.  Total: 108/108 backend tests green (22 v092 +
49 veto + 37 veto-api).

### Day 7 — Polish, edge-case unit tests, release

**Real bug found by adversarial unit tests:** `/api/veto/finale` called a
second time on an already-`complete` session returned 500 (uncaught
`InvalidVetoTransition` from the inner `complete()` call) instead of a
clean 400.  Now guarded at the top of the handler — second call returns
"session already complete — call /api/veto/reset" + 400.  The SPA never
naturally hits this (the launch button is gone on the complete page),
but a stale tab + double-click would have surfaced it.

**Documented (and worth-revisiting) behaviours pinned with tests:**
* `issue_tokens` ROTATES tokens on re-call from `links` — silently
  invalidates URLs already shared with captains if the operator
  refreshes the browser during the links stage.  Test pins the
  behaviour; TODO entry filed to make it idempotent in a follow-up.
* `revoke_token` from `links` before `issue_tokens` mints a token for
  the target team even though no token existed to revoke.  Acceptable
  (the SPA only exposes Revoke after Issue), now documented in test.

**New unit cases (test_veto.py: 34 → 49, +15):**
* BO5 sequence shape + final-map count (the Day 1 tests covered BO1 +
  BO3 only)
* `build_matchzy_config` excludes players without `steam_id` from the
  team dict (MatchZy can't address them; mixed rosters still produce a
  usable config)
* `build_matchzy_config` matchid format = `oblivion-veto-<ts>` prefix
* Revoke pre-issue documented behaviour
* Revoke leaves the other team's token untouched
* Revoke rejects unknown team values ('C', 'a', '', 'AB', 'team_a')
* Issue tokens rotation pin (with TODO)
* `perform_step` rejected after finale state reached
* `complete` rejected from every non-finale state (locks the gate)
* Long names (60 chars) accepted at model layer — display caps belong
  in callers
* Whitespace-name handling (documented — model accepts, HTTP layer's
  filled-count check rejects)
* `perform_step` signature is `(session, team, map_id)` — kind is
  server-derived, no spoofable parameter
* State graph reachability from `idle` to `complete` (regression guard
  for `_LEGAL_TRANSITIONS` edits)
* `reset` from `complete` clears state for a fresh session

**New API cases (test_veto_api.py: 31 → 37, +6):**
* `/api/veto/qr?kind=public` with no `public_ip` configured → 400 with
  useful error (was missing — would have produced `http://:port/...`)
* `/api/veto/finale` second call after complete → clean 400 (fixed
  bug noted above)
* `/api/veto/reset` post-reset state is fully cleared (`session: None`)
* Snapshot shape stability (top-level `state, session` + nested
  `current_step_detail, legal_moves` mid-veto)
* `/api/veto/distribute` before roster saved → 400 (not crash)
* Concurrent `/api/veto/finale` calls serialise via `_veto_lock` —
  session ends `complete` exactly once

**Version bumps:** `config.py:APP_VERSION = "0.10.0"`,
`installer.iss:MyAppVersion = "0.10.0"`.

**Build:** `OblivionServerTool.spec` + `build.bat` have segno and
`cs2servergui.veto` in `--hidden-import` (the lazy `import segno`
inside `/api/veto/qr` and the inline `from . import veto` are invisible
to PyInstaller's static analyser without explicit hints).

---

## v0.10.0 build journal (Days 1-6)

The **map-veto / match-setup feature** ([VETO.md](VETO.md) spec).  Five-stage
flow — roster → teams → captain vote → captain links → BO1/3/5 veto board → MatchZy
handoff — with captains vetoing from their own devices, the operator's UI mirroring
the session live.  Backend is authoritative; the prototype's browser-only state is gone.

Days 1-5 committed and pushed to master.  Day 6 (real `matchzy_loadmatch` RCON
handoff) and Day 7 (polish + smoke + tag) are the only remaining work before
v0.10.0 ships.

### Day 1 — `VetoSession` model + state machine (`c5bd7b8`)

`cs2servergui/veto.py` (365 lines) — the whole match lifecycle as a pure
state machine with no I/O, no Flask, no RCON.  Public surface mirrors the
prototype's transitions verbatim, but with server-side authority:

* States: `idle → roster → teams → voting → links → veto → finale → complete`,
  guarded by `_LEGAL_TRANSITIONS` (frozensets per state, mutation-rejected).
* Dataclasses: `RosterPlayer`, `VetoStep` (kind: BAN|PICK, team, map_id),
  `CaptainToken` (`secrets.token_urlsafe(32)`, single-use, idempotent same caller),
  `VetoSession` holds the lot.
* BO1/BO3/BO5 sequence templates in `_VETO_SEQUENCES` — BO1 = 6 bans + decider,
  BO3 = ban/ban/pick/pick/ban/ban + decider, BO5 = ban/ban/pick/pick/pick/pick + decider.
* Exception hierarchy `VetoError → InvalidVetoTransition / VetoStageError` so the
  HTTP layer maps cleanly to 400 vs. 409.
* `tests/test_veto.py` (34 cases) covers every legal/illegal transition, captain
  election ties, token reuse, single-use enforcement, full BO3 walkthrough.

### Day 2 — HTTP API + SSE live mirror + captain role (`8e1add4`, polish `9877b15`)

`cs2servergui/web.py` (15 new routes).  Every mutation runs under `core._veto_lock`
so SSE subscribers and concurrent admin/captain requests never see torn state.

| Route | Purpose |
|---|---|
| `GET /api/veto/state` | Read-only snapshot for the SPA's initial fetch |
| `GET /api/veto/stream` | SSE pub/sub via `queue.Queue` per subscriber (non-blocking `put_nowait`) |
| `POST /api/veto/create` | `create_session(mode, map_pool)`.  409 if a session is already active. |
| `POST /api/veto/roster` | 10 players (name + optional SteamID) + team names |
| `POST /api/veto/distribute` | Random 5+5 split (admin reshuffles) |
| `POST /api/veto/start_voting` | Locks teams, opens captain ballot |
| `POST /api/veto/vote` | Per-team votes (5 each); ties auto-revote |
| `POST /api/veto/resolve_captains` | Picks captains, transitions to `links` |
| `POST /api/veto/tokens` | Mints scoped single-use tokens (LAN + Public URLs returned per captain) |
| `POST /api/veto/revoke_token` | Revoke + reissue if a token leaked |
| `POST /api/veto/claim` | Public — token IS the credential; mints a captain session cookie |
| `POST /api/veto/step` | Captain bans/picks for their team (admin can act for either) |
| `POST /api/veto/finale` | `build_matchzy_config()` + `complete()` (Day 6 wires the real handoff) |
| `POST /api/veto/reset` | Clear and return to `idle` |
| `GET /veto?join=<token>` | Captain-link landing page — server-side claim + cookie + redirect to `/#veto` |

**New captain role.**  `_role_gate` allowlist `_CAPTAIN_PATHS = {/api/state,
/api/veto/state, /api/veto/stream, /api/veto/step}`.  Claim is PIN-free
(token IS the credential, single-use).  Wrong-turn = 400; team-spoof = 403.

**SSE snapshot enrichment** — every snapshot now carries `current_step_detail`
(index / kind / team) + `legal_moves`, so the SPA doesn't need to re-derive
the next action from the raw `sequence` array on every render.

`tests/test_veto_api.py` (17 cases initially, 25 after Day 4) covers the full
happy path, captain wrong-team rejection (400 vs 403), token reuse, SSE
broadcast verification, and the 409-on-existing-session create guard.

### Day 3 — SPA Veto tab + 8 stage renderers (`74c0f49`)

Frontend port of the prototype's 5-stage flow into the SPA as a dedicated
**Veto** tab in the sidebar (between Maps and Appearance).  Single
`pages['veto']` entry point in `cs2servergui/static/js/app.js`; state comes
from `/api/veto/state` + the SSE stream.

* Per-stage renderers: `_renderVetoIdle / Roster / Teams / Voting / Links /
  Board / Finale / Complete` + `_renderVetoCaptain` (captain-role simplified
  view).
* `api.veto.*` namespace in `api.js` (12 methods covering every endpoint).
* SSE subscribe on tab open, 5 s reconnect on error, cleanup on hashchange
  away from `#veto` (same pattern as the log drawer).
* `_vetoLocalRoster` buffer holds unsaved operator edits before the Save
  Roster button commits — survives in-flight SSE re-renders so the
  operator's typing isn't clobbered by snapshot pushes.
* ~270 lines of new CSS under "VETO (v0.10.0)" — reuses existing palette
  tokens (`--accent`, `--ok`, `--bad`, `--blue`).

### Day 4 — QR codes for captain links (`7561d1b`)

Captains on phones can scan instead of typing the URL.

* New dep: `segno >= 1.6` (pure-Python QR encoder, no Pillow / no native).
  Picked over `qrcode + Pillow` because Pillow is a 40 MB bundle for a 70 KB
  library's worth of features.
* New route `GET /api/veto/qr?token=…&kind=lan|public` returns SVG.
  Validates the token against the live session (refuses unknown so the
  endpoint isn't a free QR proxy for anyone with a session cookie);
  admin/local only; `Cache-Control: private, max-age=300`.
* `/api/veto/tokens` + `/api/veto/revoke_token` now return the raw `token`
  field alongside the LAN/Public URLs so the SPA can build QR URLs without
  re-parsing tokens out of the LAN link.
* SPA Links stage gains two QR slots per captain card (LAN + Public,
  Public only if a public IP is configured).  Mandatory white background —
  phone cameras need the high-contrast quiet zone against the dark veto
  page or they won't lock the code.
* 8 new tests added to `test_veto_api.py` (now 25/25): token shape, SVG
  return, unknown-token rejection, missing/bad-kind 400s, unauth 401,
  no-session 400, revoke includes new raw token.

### Day 5 — Cinematic finale animation (`b32be7e`)

Pure CSS animations layered on the existing renderers — no state-machine
change, no HTTP routes, no new tests.  Three independent JS gates ensure
each animation fires exactly once at the right moment, not on every SSE
re-render:

| Gate | What it controls |
|---|---|
| `_vetoLastRenderedState` | Stage-fade plays on state CHANGE only |
| `_vetoLastSeqLen` | Stamp slam-in + card shake only on the freshly-acted map |
| `_vetoFinaleShownThisSession` | Confetti + decider reveal play once per session; reset on `idle` |

The choreography (per arrival at finale): title slide-up + letter-spacing
expand (480 ms) → subtitle fade (380 ms, delay 220 ms) → maps pop in
staggered every 80 ms → **decider** gets a bigger pop + 1.8 s accent-glow
pulse × 2 (delay 700-900 ms) → launch button fade (delay 900 ms) → 30-piece
CSS confetti shower (2.6 s, 5-colour rotation, pointer-events: none so
the Hand-to-MatchZy button stays clickable beneath).

Decider glow uses `color-mix(in srgb, var(--accent) 55%, transparent)` to
alpha-fade the oklch accent token without converting to RGB.  Modern
WebView2 supports it natively; old WebView drops just the glow and keeps
the rest.

### Day 6 — MatchZy match-config write + RCON handoff

`/api/veto/finale` was a placeholder that built the config dict and
logged it.  Now it does the real handoff:

1. Strips `_oblivion_meta` (our SPA audit trail, unknown to MatchZy)
   from a copy of the config so MatchZy's schema doesn't complain.
2. Atomically writes the cleaned JSON to
   `<csgo>/cfg/MatchZy/<matchid>.json` (tmp + `os.replace` + fsync; the
   directory is auto-created on first use).
3. If `load_match: true` (default) AND `core.running`, issues
   `matchzy_loadmatch <basename>` via RCON.  Single attempt — RCON has
   retry logic but the operator is watching this in real time; better
   to surface a quick failure than wait 30 s through retries.
4. Response always carries `{ok, config, matchzy: {written_to, loaded,
   error?, rcon_response?}}` so the SPA can show the operator exactly
   what happened.

Three-way outcome design:
* **File write fails** → 500, session stays on `finale` so the operator
  can retry after fixing the disk issue.
* **File written, RCON fails (or server not running)** → 200 with
  `matchzy.error` describing what to do.  Session still transitions to
  `complete` so the SPA isn't stuck; the operator can copy
  `matchzy.written_to` and run `matchzy_loadmatch <file>` from the RCON
  console.  The launch button flips to "Retry handoff".
* **File written, RCON succeeded** → 200 with `matchzy.loaded: true`
  and a snippet of the RCON response.  SPA shows a green ✓ and the
  button locks to "Match handed off ✓".

SPA finale renderer updated: real-time status under the launch button
(yellow warning for needs-attention, green check for success), button
state machine (disabled during the call, "Retry handoff" enabled on
RCON failure, locked on success).

Test additions (+6 cases, now 31/31 in `test_veto_api.py`):
* File gets written + on-disk JSON has the expected MatchZy keys
* `_oblivion_meta` is **stripped from the disk file** but **preserved
  in the API response** (so the SPA can show the veto audit trail)
* Server-not-running → 200 + `matchzy.error` mentioning "not running"
* RCON `ConnectionError` → 200 + `matchzy.error` containing the
  exception text; session still transitions to `complete`
* `load_match: true` + running + RCON OK → exactly one
  `matchzy_loadmatch <basename>` call, where `<basename>` matches the
  on-disk filename
* `load_match: false` + running → zero RCON calls (preview mode)

Test fixture `_new_app()` now redirects `core._csgo_dir()` to a
per-test tempdir via `mkdtemp('oblivion_veto_csgo_')` so the test
batteries never write to the real CS2 install dir on the user's machine
(this would otherwise litter `D:\steamcmd\…\game\csgo\cfg\MatchZy\`
with `oblivion-veto-*.json` files on every run).

### Day 7 (pending) — Polish + smoke + tag v0.10.0

### Day 7 — Polish + extra unit tests + release

See top of v0.10.0 section above for the Day 7 detail.

### Final test totals at v0.10.0 release

* `tests/test_v092.py` — 22/22 (unchanged from v0.9.2.1)
* `tests/test_veto.py` — 49/49 (Day 1's 34 + Day 7's 15 edge-case additions)
* `tests/test_veto_api.py` — 37/37 (Day 2's 17 + Day 4's 8 QR + Day 6's 6 MatchZy + Day 7's 6 polish)
* **All 108/108 green**

---

## v0.9.2.1 — 2026-06-01 (hotfix)

A four-agent re-audit of the v0.9.2 fix code (not the original bugs) surfaced one
**critical regression** and four other issues worth fixing before any operator actually
runs the v0.9.2 binary in earnest. All landed here.

### 🚨 Critical: 5-second RCON command stall

`rcon.py`'s multi-packet sentinel had a speculative trailing `_recv(s)` that waited for a
"trailing empty-response packet some Source builds emit after the sentinel" — except CS2
doesn't emit it, so every `execute()` blocked for the full 5-second socket timeout waiting
for a phantom packet.  **Every RCON-touching path** — status polling, broadcasts, kicks,
bans, map changes — gained +5 s. The smoke test missed it because the mock socket pre-
queued the phantom packet that real CS2 doesn't send.  v0.9.2.1 drops the speculative
drain; the sentinel id arrival already proves the real response is complete.

### 🔒 Workshop-download race actually fixed

The v0.9.2 fix locked `cancel_download` but left the worker's assign/clear and the web
route's 409-check unlocked. Two clicks could both observe `None` and both spawn
workers. v0.9.2.1: web.py atomically check-and-reserves under `_dl_lock`; worker
swaps the reservation for the real Popen handle (also under lock); cancel-before-spawn
race correctly terminates the late-arriving process.

### 🌐 `_resolve_rcon_host` won't clobber good IP with `127.0.0.1`

If `_lan_ip()` momentarily falls back to its loopback default (UDP probe to 8.8.8.8
fails), v0.9.2 would overwrite `_config.RCON_HOST` with `127.0.0.1` and break the
very bug v0.9.2 was supposed to fix. v0.9.2.1 keeps the last-known-good value when
the fresh probe is the loopback fallback. `_post_launch_sanity_check`'s netstat-based
recovery remains the safety net.

### 🔐 Two more `current_map` writes under `_lifecycle_lock`

`_poll_rcon_ready:1474` (workshop trigger success) and `change_map:1534` were the two
remaining bare writes; v0.9.2 had locked the recovery path but not these. Now all four
sites are consistent.

### ⏸ Stop during crash-restart backoff: edge-window cancel

Stop pressed in the tiny window between `_stop_event.wait()` returning False and
`start_server`'s `clear()` was swallowed by the clear, so the unwanted respawn proceeded.
Now re-checks `_stop_event.is_set()` after the wait, before `start_server`.

### 🧙 Warcraft: SteamID equality instead of `ReferenceEquals`

The v0.9.2 Warcraft audit follow-ups used `ReferenceEquals(fresh, wcPlayer)` to verify
the queued menu was still for the same player. But `WarcraftPlugin.SetWcPlayer` legitimately
installs a brand-new `WarcraftPlayer` object on class change — same human, same slot, but
the reference comparison silently fails and the menu drops. v0.9.2.1 compares by SteamID
(`slotController.SteamID != capturedSteamId`) so a queued menu survives a class change.

Three sites fixed: `WarcraftPlugin.cs` (`!reset` follow-up), `Events/EventSystem.cs`
(round-start auto-open), `Menu/WarcraftMenu/SkillsMenu.cs` (recursive reopen after pick).

Rebuilt `WarcraftPlugin.dll` bundled.

### Test battery still 22/22

The v0.9.2 isolated-behaviour battery (`tests/test_v092.py`) is unchanged and continues
to pass under the hotfixed code. Mocks were correct; the bug was in the integration with
real CS2 — discoverable only via second-pass code review.

---

## Unreleased
*Post-v0.9.2.1 polish. Will fold into v0.9.3 when there's a meaningful change worth tagging.*

### 📦 Installer / Build Hardening

Defensive packaging tweaks — no behaviour change to the running app:

- **`build.bat`**: added `cs2servergui._netutils` to `--hidden-import` (the
  module is imported lazily inside `core.py` methods; without an explicit
  entry, PyInstaller's static analyser only finds it via `main.py`'s top-
  level alias re-export, which could break under future refactoring).
  Also added `--noconfirm` so the PyInstaller build never blocks on the
  existing-output prompt.
- **`requirements.txt`**: pinned `werkzeug>=3.0.0` explicitly. Comes in
  transitively via Flask today but `main.py` imports `werkzeug.serving
  .make_server` directly since v0.9.2 — pinning here makes the build
  stable if Flask ever swaps its server backend.
- **`installer.iss`**: documented WebView2 bootstrapper activation
  (download `MicrosoftEdgeWebview2Setup.exe`, place in repo root,
  uncomment 2 lines) with a clear note that Win10 needs it.  Also added
  explicit `IconFilename:` to the Start Menu + Desktop shortcuts so the
  emblem.ico shows even before Windows' shell-icon cache warms.

## v0.9.2 — 2026-05-30

### 🧹 Cleanup Pass — Dedup, Dead Code, Stale Markers

Small follow-up sweep with no behaviour changes — pure code hygiene:

- **`cs2servergui/_netutils.py` (NEW)** — single source of truth for the Windows
  port/process helpers (`port_in_use`, `holder_of_port`, `listeners_on_port`).
  Previously `_holder_of_port` had two near-identical implementations: one
  module-level in `main.py` for Flask port collisions, one as an `AppCore` method
  in `core.py` for CS2 port-conflict detection.  Both call sites now import from
  `_netutils`; `core.AppCore._holder_of_port` / `._listeners_on_port` stay as thin
  instance-method wrappers that pass `self.log` so the AppCore logger gets the
  diagnostic output.
- **`main.py:5` raw-string fix** — the build-output path in the module docstring
  used `dist\OblivionServerTool.exe` which triggered Python 3.12+
  `SyntaxWarning: invalid escape sequence '\O'` on every import.  Module docstring
  is now an r-string.  Zero warnings on import.
- **Dead `RCON_HOST` import removed from `web.py`** — the 20-bug sweep dropped
  every reader of the name but left the import.  Now gone, with a comment noting
  why future readers should always use `_config.RCON_HOST` at call time.
- **3 legacy plugin scrubs removed from `_PLUGIN_CLEANUP_ITEMS`** — `cfg/retakes.cfg`
  from the defunct MatchZy-retakes era, plus three `characters/models/`
  Barbarian-model paths from an earlier failed precache attempt.  All were "remove
  if leftover from an older install" entries that no current install carries.
- **Stale TODO closed** — the "verify auto-restart fires on a real crash" line is
  now obsolete: the post-friends-night resilience pass replaced the fixed 5 s
  backoff with exponential (5 → 15 → 45 s) + 5-min time-window reset + `Event.wait`-
  cancellable sleep so a Stop during the backoff is honoured.
- **Unused `socket` import removed from `main.py`** — `_port_in_use` moved to
  `_netutils` so `main.py` no longer needs its own socket import.

### 🛠️ Workshop Maps Root-Cause Fix + 20-Bug Audit Sweep

The workshop-map-loads-as-dust2 saga ended with an embarrassingly small root cause:
`from cs2servergui.config import RCON_HOST` was binding the LAN IP **at module import time**
inside `core.py`. `_resolve_rcon_host()` was diligently updating `_config.RCON_HOST` on every
server start, but the import-bound `RCON_HOST` name in `core.py` never updated — so
`_poll_rcon_ready`'s probe socket kept dialling the stale IP forever. A boot-time network
blip or any later DHCP/VPN/adapter change would silently break workshop maps for the rest
of the session. Now `_config.RCON_HOST` is read at call time everywhere; the netstat-based
auto-recovery from earlier in the day becomes a pure safety net instead of the primary path.

A parallel four-agent app-wide bug hunt (core.py, web.py + frontend, main.py + config.py +
rcon.py, Warcraft plugin) surfaced **20 actionable findings** — 7 critical, 8 serious,
5 minor — all landed in this release.

**Critical (7)**
- `core.py` import-bound `RCON_HOST` (above) — the actual root cause of the workshop bug.
- `save_config()` was non-atomic: two concurrent saves (Flask is threaded) could interleave,
  and a power-loss mid-write left a truncated file that `_load_config` silently treated as
  `{}` on the next launch — wiping every persisted setting + regenerating the RCON password.
  Now lock-guarded, tmp-write + `os.replace` + `fsync`.
- `os._exit(0)` at window close bypassed every pending save. Settings changed seconds before
  shutdown were lost. Now `core.save_config()` runs synchronously before the exit.
- Multiple lifecycle-state mutations outside `_lifecycle_lock` (`boot_state`/`running`/
  `current_map` in `probe_existing_server`, `_poll_rcon_ready` 90s timeout, `change_map`,
  `_post_launch_sanity_check`) could race a concurrent `stop_server` and leave inconsistent
  state. `_poll_rcon_ready` also read `self.proc` twice — `AttributeError` if cleared between
  reads. All wrapped now; `proc` snapshotted once.
- Stop pressed during the 5/15/45 s crash-restart backoff was silently ignored (the sleep
  ran to completion and respawned anyway). Replaced with `Event.wait()` so Stop cancels.
- `/api/workshop/download` had no concurrency check — two clicks spawned two `steamcmd`
  processes, orphaning the first and colliding on the staging dir. Now returns 409 when busy.
- `/api/server/broadcast` blocked `\r\n` but not `;` — CS2's console treats `;` as a command
  separator, so a `hello;sv_password pwn;quit` broadcast ran arbitrary RCON. Now stripped
  and capped at 200 chars.

**Serious (8)**
- `_post_launch_sanity_check` used a stale `proc` snapshot — could force-fire
  `host_workshop_map` on a server that was just stopped. Re-checks `running` after each sleep.
- `cancel_download` read `_active_dl_proc` without the download lock — a click between
  "worker finished" and "next started" could kill the new download. Snapshot+clear under lock.
- `rcon.py:execute_retry` only retried `ConnectionRefusedError` + `TimeoutError` — a flapping
  network produces `ConnectionResetError` / `OSError(WinError 10054)`, which were re-raised
  immediately. Widened to `(TimeoutError, OSError)` plus `ConnectionError` minus "auth failed".
- `rcon.py:execute` never handled Source RCON's multi-packet response trick (any body >4 KB
  splits across multiple packets, terminated by an empty-body sentinel). Long `status` /
  `cvarlist` output was silently truncated at the first 4 KB. Now sends a sentinel after
  the real command and concatenates every fragment until the sentinel id comes back.
- `config.py:_lan_ip()` did a fresh UDP `socket()` + `connect("8.8.8.8:80")` on every
  `/api/state` poll (every 2 s × connected clients) — wasted syscalls + a hard dependency
  on a route to 8.8.8.8 existing for the LAN IP to resolve, which serialised every state poll
  behind a wedged VPN/Hyper-V adapter. Now cached 30 s + 0.5 s socket timeout. `AppCore.
  _resolve_rcon_host` calls with `force_refresh=True` so server starts still see live values.
- `main.py` had a TOCTOU between `_pick_free_port()` and `flask_app.run()` — Flask binds
  inside its background thread, so a foreign process grabbing the port in those ~ms surfaced
  as a misleading "did not start in 10s" timeout. Now uses `werkzeug.serving.make_server`
  to bind synchronously in the main thread and retries up to 3 times on race-loss.
- `_fix_metamod_dll_nesting` used `shutil.copy2 + rmtree` — a failed rmtree left the DLL
  at BOTH the nested and parent paths, and MetaMod would load the wrong one. Now uses
  `shutil.move` (pre-removing any existing dst on Windows).
- `/api/log/save` used `%Y%m%d_%H%M%S` filenames — two saves in the same second silently
  truncated each other (opened with `"w"`); no `@require_local` so guests could spam saves
  to fill the host's disk; no empty-buffer guard. All three fixed: 6-hex random suffix,
  local-only, 400 on empty buffer.

**Minor (5)**
- `_STEAMID_RE` had no length anchor — a 1 MB string passed validation. Capped to 64 chars
  plus a dedicated `_NAME_MAX_LEN` cap on `players_kick` `name`.
- Per-IP `_attempts` auth-failure dict had no GC for entries below `_MAX_ATTEMPTS` — slow
  distributed brute force could grow the dict forever. Added `_ATTEMPT_TTL_SECS` prune.
- `/auth/auto` startup-token compare-and-clear was non-atomic — two simultaneous loopback
  hits could both pass `compare_digest` and mint two local sessions. Now lock-guarded.
- `/api/setup/status` was guest-accessible — leaked `pin_is_default` to remote guests.
  Now `@require_local` (the first-run wizard only ever shows in the local pywebview window).
- `_last_crash_mono` wasn't reset when the auto-restart cap was hit — the next crash would
  hit the stable-reset branch with a stale timestamp and log a misleading "stable for X s".

### 🧙 Warcraft — Menu & Chat-Broadcast Dispatchers v2

After the v1 per-player chat-command cooldown shipped, a live retest (2026-05-30, Casual +
Warcraft, 13 humans+bots on `de_cache`) showed the bug still happened: a single `!shop`
during a combat-heavy frame produced `recv queue overflow 100` on every connected client
plus `SteamNetworkingSockets lock held for 263 ms ... thread starvation`. The cooldown
stopped rapid spam from the same player but didn't address two collisions in the same tick.

- **Menu-open dispatcher**: every `!class` / `!skills` / `!shop` (and the programmatic
  `SkillsMenu.Show` at round-start after a level-up and after `!reset`) now enqueues through
  `WarcraftPlugin.EnqueueMenuOpen`. A 0.1 s repeat timer drains **one** queued open per tick,
  so ten concurrent opens fan out across ~1 second of frames instead of stacking onto one.
- **Chat-broadcast dispatcher**: `AbilityBenefitAnnouncer.SendRoundSummary` (called for every
  human at round start, ~5 `PrintToChat` per player) routes each broadcast through
  `EnqueueChatBroadcast`. A 0.05 s repeat timer drains 5 per tick — round-end bursts of 50+
  `PrintToChat` smooth across half a second.
- **Audit follow-ups** (from a parallel agent review of the patches):
  - `Unload` now kills the new timers and clears the queues. Hot-reload could otherwise leave
    the old timers firing into a disposed instance with the new instance's queues never drained.
  - `AbilityBenefitAnnouncer.SendRoundSummary` hoists `WarcraftPlugin.Instance` to a local
    at enqueue time — a hot-reload between enqueue and drain could otherwise route the burst
    into a different (or null) plugin instance's queue.
  - The three deferred `SkillsMenu.Show` sites (recursive reopen, round-start auto-open,
    `!reset` follow-up) re-resolve the WarcraftPlayer via the slot's controller at drain
    time. If a player disconnected and a new player took the same slot in the 100 ms drain
    window, the original profile would otherwise pop for the new occupant.
  - Both timers re-armed in `OnMapStartHandler` (`STOP_ON_MAPCHANGE` kills them at map end)
    and queues cleared in `OnMapEndHandler` so they can't accumulate stale lambdas.
- Built against the upstream toolchain (.NET 8 / CSS 1.0.368) — patched `WarcraftPlugin.dll`
  bundled in `cs2servergui/plugins/warcraft/`.

### 🛟 Log Drawer — Copy + Save Buttons

The in-app log drawer had no way to extract the buffer — operators were screen-grabbing log
panels to share diagnostic output. Added two buttons to the drawer bar:
- **Copy** uses `navigator.clipboard.writeText` with a hidden-textarea + `execCommand('copy')`
  fallback for environments where WebView2 silently rejects clipboard writes.
- **Save** posts to a new `/api/log/save` endpoint that writes a timestamped
  `oblivion_log_YYYYMMDD_HHMMSS_<6 hex>.txt` to the config directory (next to
  `oblivion_config.json`) and surfaces the path via toast + log line.

### 🔌 RCON Host — Stop Pinning to LAN IP at Import

Belt-and-braces follow-up to the workshop-maps fix. `_resolve_rcon_host()` re-resolves
`_config._lan_ip(force_refresh=True)` and updates `self.rcon.host` on every server start /
attach. Plus the post-launch sanity check (added earlier) keeps its netstat-based recovery
that switches `self.rcon.host` to whichever bind address actually answers — handles CS2
binding to an unexpected interface (VirtualBox / Hyper-V / Docker / VPN tap adapter that
sorts ahead of the primary LAN NIC in Windows' route table).

### 🔐 Two-Tier Remote Access — Guest vs Admin

The remote panel now has an optional **guest role** so you can hand friends limited control
without exposing full admin.

- **Guest PIN** (Config → Security, local-only to set; blank = disabled). A separate PIN from
  the admin PIN; admin wins if they collide.
- **Guest can:** view status, change map, change game mode, browse + **download workshop maps**.
  **Guest cannot:** start/stop the server, edit config, manage bots/bans/players, view logs, or
  anything else — and RCON/install/Steam stay strictly local as before.
- Enforcement is **fail-closed**: a single `before_request` gate allows only an explicit
  guest/public allowlist; every other `/api/*` route is admin-only by default (new routes are
  locked down automatically). The login assigns `session["role"]`; the local desktop window is
  always admin.
- The SPA hides admin-only UI for guests (Start/Stop, settings strip, Config & Players tabs,
  keybinds) and shows an "Admin only" notice on direct navigation; `/api/state` exposes `role`.
- **Keybinds disabled for guests** — the global shortcut handler no-ops for guest sessions so a
  friend can't trigger admin actions (stop/restart/bots) by keypress.
- *Verified live through a Cloudflare tunnel:* guest → 403 on admin routes / 200 on allowed ones;
  admin → full access; wrong PIN → 401; guest UI correctly stripped down.

### 🎮 Team-Size Modes — Arenas (1v1/2v2) + MatchZy (3v3/4v4/5v5)

Reworked the small-team modes so duels and team matches are cleanly separated, fixing
the long-standing gap where `3v3`/`4v4` were secretly K4-Arenas modes that never actually
configured a team size (they ran the plugin's default, i.e. mostly 1v1).

- **Arena duels capped at 2-per-side:** `1v1` and `2v2` are the K4-Arenas ladder modes.
  `1v1` uses the plugin's default rounds (already pure 1v1 — its bundled `2vs2`/`3vs3`
  rounds ship `EnabledByDefault=false`); `2v2` gets a **generated `round-settings` config**
  forcing `TeamSize: 2` across a small weapon rotation (written on deploy by
  `_apply_arena_size`; the generated config is scrubbed on switch-away so it can't linger).
- **Team matches via MatchZy:** `3v3`, `4v4`, `5v5` are now MatchZy-managed team matches
  (same plugin as Practice) on the competitive ruleset, bounded by `maxplayers` 6 / 8 / 10.
- **Mode list** gained `2v2` and `5v5`; `3v3`/`4v4` switched from arenas to MatchZy. The
  arena Use-bots toggle still applies to `1v1`/`2v2`.
- **Arena ladder bots fixed** — arena modes now launch with `+bot_quota_mode normal`. K4-Arenas-Bots
  reads that mode: `normal` adds exactly **one** bot to even an odd player count, and that bot joins
  the 1v1 ladder like a player (pairings rotate P-vs-B / P-vs-P / B-vs-P). The default `fill` set
  `bot_quota 2` — a second, unpaired bot that stuck onto a side as a **2v1**. Forcing `normal` at
  launch prevents that.
- **Identical guns per duel** — both arena modes (1v1 + 2v2) now generate an explicit-weapon
  round rotation so **both players get the exact same gun** each round. The plugin default uses
  per-player weapon *preferences*, which could hand opponents different guns within a category
  (AK vs M4); preferences are now disabled.
- **Rotation tightened to the classic 1v1 ladder set** — AK / M4 / AWP / Scout / Pistol (USP) /
  Deagle / Knife. The earlier SMG (MP9) and Shotgun (Nova) rounds were dropped — they felt out of
  place in a skill-based 1v1 ladder.
- ⚠️ *Needs in-game verification:* the generated 2v2 arena config and the MatchZy team-size
  matches couldn't be tested without a live server.

### 🗺️ Workshop Map Flagging — Recommended Modes

The map browser now tells you what each workshop map is *for*, instead of leaving you to
guess from its name. All derived from the Steam Workshop tags we already cache (no new API
calls).

- **Recommended-mode badges** on every workshop card — derived by inverting `MODE_WORKSHOP_TAGS`
  but ignoring generic tags (`classic`/`competitive`/…) so only *distinctive* tags drive them
  (a `ze_` map shows **Zombie Escape**, an `aim_` map shows **1v1 / 2v2**, etc.). Plain comp maps
  read "Competitive / Team".
- **Steam tag chips** shown (muted) under the badges for at-a-glance context.
- **Mode-mismatch guard** — starting or loading a map whose recommended modes don't include the
  selected mode pops a confirm ("looks made for *Zombie Escape*, you've selected *Competitive*").
  The confirm offers **Switch to &lt;mode&gt; & load** (fixes the mismatch in one click), *Load
  anyway* (keeps the current mode), or Cancel. Applies on the status page and the grid.
- **Sort + dim by current mode** — the card grid floats maps that suit the selected mode to the
  top and de-emphasises clear mismatches (they brighten on hover; nothing is hidden).

### 🎯 Unified Map Picker — No More Ambiguity

The status-page "Map & Mode" card had **two** map dropdowns (Official + Workshop) and silently
resolved `workshop || official` — so picking a workshop map secretly overrode the official one
with no visual cue which would actually launch.

- **One unified Map dropdown** now lists everything in optgroups: *Official Maps*,
  *Workshop — Recommended for &lt;mode&gt;*, *Workshop — Other*. One control = one selected map =
  zero ambiguity for both **Start** and **Change Map**. Each workshop option is also labelled
  with its recommended mode(s) (e.g. `ze_random · Zombie Escape`) so every option self-describes.
- **"Selected: &lt;map&gt; [Official|Workshop]" readout** under the picker confirms exactly what
  will load and from where.
- Start / Change Map read that single selection (and still run through the mode-mismatch guard);
  an empty selection now prompts "Select a map first" instead of failing silently.
- ⚠️ *Frontend-only change; worth an eyeball in the running app to confirm the picker renders.*

### 🎨 UI

- **Sidebar no longer looks washed out** — its background was the *lightest* large surface in the
  app (`bg-1 → bg-2`), reading as a pale slab next to the dark content. Re-graded it to fade
  `bg-1 → bg-0` (into the base tone) and softened the inset edge glow, so the tab rail now sits in
  the dark theme instead of floating above it.

### 🧟 Zombie Escape — Command-Filter Fix

- **Zombie Escape now always launches with `-disable_workshop_command_filtering`.** Mounting the
  ZombieReborn content addon (MultiAddonManager) turns on CS2's workshop command filtering for the
  whole session — even on official maps — which silently rejected `zm_enable` and every
  `cs2f_*`/`zr_*`/`zm_*` CVar (a wall of `DISALLOWED WORKSHOP CONVAR` in the log), so ZM never
  actually enabled. Added `_CMDFILTER_REQUIRED_MODES` so the flag is forced for the mode
  regardless of map. *Confirmed working in-game (2026-05-29).*

### 🧟 Zombie Escape — Missing ZM Configs (gun pickup + models)

- **Humans couldn't pick up guns in Zombie mode.** CS2Fixes' ZM reads a per-weapon whitelist from
  `addons/cs2fixes/configs/zm/weapons.cfg` (each weapon's `"enabled" "1"` = humans may use it), but
  the bundle only ever shipped `weapons.cfg.example` — so the active file was missing
  (`Failed to load … zm/weapons.cfg` in the boot log) and **no weapons were whitelisted → pickups
  blocked.** Now ship the active `weapons.cfg` (all 43 weapons enabled), plus `hitgroups.cfg` and
  `playerclass.jsonc` (the latter fixes zombies having no custom model), for both `zm/` and `zr/`.
  *Confirmed cause in-game; takes effect on map reload (CS2Fixes loads these at level init).*

### 🧙 Warcraft — Menu & Chat-Broadcast Dispatchers (recv-queue-overflow root-cause fix)

Friends-night Warcraft session under a full lobby choked: `recv queue overflow 100 messages
already queued` for every client, `Long frame (FreezePeriod): 55ms`, `thread starvation`, and
clients timing out — driven by `!class` / `!skills` / `!shop` / `!commands` (each menu open does
DB loads + HUD/WORLD_TEXT broadcasts → main-thread pressure → can't drain incoming packets).
Live retest (2026-05-30, `de_cache` with 13 humans+bots) showed the per-player cooldown helped
but didn't eliminate the bug: a single `!shop` during a combat-heavy frame still produced
`recv queue overflow` on every connected client.

- **Per-player chat-command cooldown** (already in v4.1.1+patch1): `AddUniqueCommand` wraps every
  chat command with a 1.5 s per-(player, command) cooldown; bots/console bypass. Stops rapid
  spam from the same player but doesn't prevent collisions across players.
- **Menu-open dispatcher (NEW)**: every `!class` / `!skills` / `!shop` (plus the programmatic
  `SkillsMenu.Show` auto-opens at round-start after a level-up and after `!reset`) enqueues
  through `WarcraftPlugin.EnqueueMenuOpen` instead of running inline. A 0.1 s repeat timer
  drains one queued open per tick, so ten concurrent opens fan out across roughly one second
  of frames instead of all hitting the engine on one tick.
- **Chat-broadcast dispatcher (NEW)**: `AbilityBenefitAnnouncer.SendRoundSummary` (called for
  every human at round start, ~5 PrintToChat per player) now routes each broadcast through
  `WarcraftPlugin.EnqueueChatBroadcast`. A 0.05 s repeat timer drains 5 broadcasts per tick
  (100/sec capacity), smoothing the round-end burst across half a second of frames.
- Built against the same toolchain as the upstream (.NET 8 / CSS 1.0.368) — bundled patched
  `WarcraftPlugin.dll` ships in `cs2servergui/plugins/warcraft/`.

### 🐛 CS2 Update / Disk Bloat — Critical Fix

- **Stopped the updater creating a duplicate ~64 GB install.** The steamcmd update ran with
  `+force_install_dir <CS2_SERVER_DIR>`. CS2's content root is a top-level `game/` folder, so
  steamcmd unpacked the whole install into `D:\steamcmd\game\` — a full duplicate, separate from
  the manifest-tracked `steamapps\common\…` install the server actually runs. Every update grew
  that orphan and never touched the real files (server dir had ballooned to ~149 GB). Dropping
  the flag lets steamcmd use its default library (the steamcmd dir) and update the real install
  **in place** via the existing `appmanifest_730.acf`. Reclaimed ~64 GB.
- **Update badge clears without a relaunch + self-verifies.** Both update badges now toggle on
  state (previously show-only, so the "⬆ CS2 Update" badge lingered until relaunch). After a
  successful update, `check_update` re-reads the updated `appmanifest` buildid and compares it to
  the latest public build — confirming the update actually landed rather than optimistically
  clearing the flag.
- **Update path hardened.** steamcmd.zip download uses `urlopen(timeout=60)` + `copyfileobj` so a
  stalled CDN can't hang the install thread.
- **Server update now runs steamcmd in its own console window** instead of capturing its output
  into the app. The captured-pipe path is what triggered steamcmd's "exit code 8" self-update
  failure and no-output hangs; a standalone console lets steamcmd self-update cleanly and shows
  native progress. The app still holds the process handle, waits for it to finish (heartbeat in
  the log), then re-verifies the build — so the badge still clears automatically on completion.
  *Confirmed working in-game (2026-05-29): update applied cleanly, no duplicate install, server
  rejoined the matching client build.*
- **Always-available "Update / Validate CS2" button** (Config → Server Installation, local-only).
  Previously the CS2 update was *only* reachable via the `⬆ CS2 Update` badge, which appears only
  when the mirror-based check (`api.steamcmd.net`, which can lag Valve) flags an update — leaving
  no way to force a steamcmd `app_update 730 validate` when the badge wasn't showing. The button
  runs the same in-place update on demand (refuses while the server is running). It also **pulses
  when an update is detected** (`update_available`) as a cue — while staying a normal,
  always-clickable forced-update button when it isn't pulsing, so a mirror miss never hides it.

### 🛡️ Resilience — Redundancies After Friends-Night Burns

A batch of small hardenings to address the failure modes from the live session: a foreign app
squatting our Flask port broke the desktop panel, the server died mid-mode-switch, and the
silent missing-config bug (zombie weapons whitelist) wasn't caught until a friend reported
empty hands. The fixes are all defensive — none of them change normal-path behaviour.

- **User-configurable Flask port** — `flask_port` is now a first-class field in
  `oblivion_config.json` (default `5050`). Resolves the prior TODO; config and main both honour it.
- **Port-collision survivor on Flask bind.** Identifies the holder of the configured port via
  `netstat -ano` + `tasklist`: if it's our own zombie (`OblivionServerTool.exe` / `python.exe`),
  it's killed; if it's foreign (CS_GO_Arx_Applet, etc.), it's left alone and Flask falls back to
  the next free port in `[configured+1..configured+3]`. The chosen port is logged and propagated
  via `_config.FLASK_PORT` so the status bar / tunnel hints reflect reality.
- **RCON_HOST re-resolved on every server attach/start.** `config.py` resolves the LAN IP once
  at import — so a DHCP change after the app boots left RCON pointed at a stale IP. The new
  `_resolve_rcon_host()` runs at the top of `start_server` and `probe_existing_server`, refreshes
  `_config.RCON_HOST`, and patches the live `RCONClient` instance.
- **Pre-flight checks before Start.** New `_preflight_checks()` runs before `deploy_plugins()`:
  blocks if CS2 isn't installed, port `27015` is held by a non-CS2 process, or the bundle's
  plugin source folders are missing for the chosen mode. Soft-warns if a workshop map is
  selected but Steam credentials aren't saved, or DepotDownloader is missing. Every finding is
  logged with a one-line fix hint.
- **Bundle config validation on deploy.** Walks each deployed plugin's bundle folder for any
  `*.example` files and warns when the implied active file is absent from both the bundle and
  the live `csgo/` tree. Catches the class of bug we hit with Zombie's `weapons.cfg` — shipped
  `weapons.cfg.example`, no active file → plugin loaded with no whitelist → gun pickup
  silently broken.
- **Crash auto-restart hardening.** Exponential backoff between attempts (`5 s → 15 s → 45 s`)
  so a persistent boot-loop config bug isn't hammered, and a **time-window reset**: if the
  server stayed up for 5+ minutes since the last crash, the consecutive-failure counter is
  forgiven. Previously a session that auto-restarted twice over hours would refuse the third
  recovery because the counter only reset on a clean stop. End-state messaging now points at
  log-checking and explicitly says the counter resets on manual Start.

### 🔌 Web Panel Port

- **Default Flask port moved `5000` → `5050`.** Port 5000 is heavily contested (Flask demos,
  macOS AirPlay, and CS applets like `CS_GO_Arx_Applet` that bind `127.0.0.1:5000`). A collision
  there makes the desktop panel unreachable on loopback — every API call fails with "failed to
  fetch" and in-app RCON breaks, even though the server itself is fine. 5050 is far less contested.
  *(Takes effect on the next build / source run; update any tunnel or port-forward to 5050.)*

### 🧹 Workshop Cleanup

- Removed an empty (0-byte) broken workshop folder and four obsolete CS:GO-era `.bsp` maps
  (`de_bank`, `cs_militia`, `de_stmarc`, `gd_rialto`) — confirmed via re-download they were intact
  but in the CS:GO format CS2 can't load. Disk free went ~16.5 GB → ~80 GB after the dedupe + this.

---

## v0.9.1 — 2026-05-29

A stability + features pass: Retakes rebuilt on B3none, the Jailbreak native crash fixed,
Warcraft Barbarian models fixed via a precacher plugin, a full workshop download overhaul
(progress + verify), workshop command-filter automation, and the Arena modes made
group-friendly with a dynamic player cap and a Use-bots toggle.

### 🕹️ Arena Modes & Bots

- **Dynamic player ceiling** — 1v1/3v3/4v4 (K4-Arenas) now launch with `maxplayers 16`. It's
  a ceiling, not a target: K4-Arenas only builds arenas for players actually present, so any
  turnout fits (4 → 2 arenas, 12 → 6) with no per-session tuning.
- **"Use bots" toggle** (Config → Bots, default off) — when off, K4-Arenas-Bots is excluded
  from the deploy so the ladder is humans-only (the odd player out waits at their rank for the
  next opponent); when on, bots fill empty arenas. (Currently gates Arena bot-fill; Retakes /
  Deathmatch to follow.)
- Fixed deploy verification falsely flagging `K4-Arenas-Bots.dll` as missing when bots are off.

### 🔌 Retakes — B3none cs2-retakes (not MatchZy)

An earlier plan to base Retakes on "MatchZy's built-in retakes mode" was **abandoned: MatchZy
has no retakes feature** (verified in its source and docs). Retakes now runs on **B3none's
dedicated [cs2-retakes](https://github.com/B3none/cs2-retakes)** `RetakesPlugin` paired with
**yonilerner's `RetakesAllocator`**.

- Bundled `RetakesPlugin` + `RetakesAllocator` + `RetakesPluginShared`; `retakes_config.json`
  sets `EnableFallbackAllocation=false` (the allocator owns weapons) and `RoundsToScramble=3`.
- **Spawn-coordinate fix** — B3none's bundled `map_config/*.json` used thousands-separator
  commas (`1,229.99`) that failed float-parsing and spawned players inside walls; stripped
  271 bad commas across the map configs.
- **Bot auto-fill** — a deployed `cfg/cs2-retakes/retakes.cfg` enables `bot_quota_mode fill`
  so retake rounds still form on a low-population server.
- `_MODE_PLUGIN_NAMES["Retakes"] = ["retakes_b3none"]`; competitive ruleset (`game_mode 1`).

### 🧙 Warcraft Fixes

- **Barbarian models fixed via a new `ModelPrecacher` plugin.** Barbarian assigns the
  non-default player models `tm_phoenix_heavy` / `ctm_heavy`, which exist in `pak01.vpk` but
  aren't auto-precached — so `SetModel` logged "requested but is not in the system" and the
  model failed. Loose `.vmdl_c` copies were proven *not* to fix this (CS2 only loads models in
  the precache manifest). A tiny bundled CounterStrikeSharp plugin (`ModelPrecacher`, source in
  `_plugins_src/`) now registers both via `OnServerPrecacheResources` → models render, all 14
  classes intact. *Confirmed working.*
- **`!buy` shop command fix** — removed `buy` from WarcraftPlugin's shop-menu triggers; it was
  shadowing CS2's native `buy <weapon>` console command, so buying a gun popped the Warcraft
  shop instead.
- **In-game menu theming** — added a CS2MenuManager `config.toml` (purple/white, WasdMenu,
  4:3-safe position) for menus that route through CS2MenuManager. Note: WarcraftPlugin's
  `!class`/`!skills`/`!shop` use its *own* compiled menu, which enlarges the highlighted item
  and can clip tall pages vertically — that's a compiled-in behaviour, deferred to a future
  recompile (tracked in TODO → Backlog).

### 🛑 Jailbreak Crash Fix

Jailbreak mode crashed with a native access violation ~1–2 s after the plugin loaded — every
time, while no other mode crashed. Cause: the mode loaded **CS2Fixes (a heavy native MetaMod
plugin) alongside the self-contained CSS Jailbreak plugin**, and the two conflict at the native
level. Dropped `zombie`/CS2Fixes from the mode (`_MODE_PLUGIN_NAMES["Jailbreak"] = ["jailbreak"]`).
*Confirmed working.*

### ⬇️ Workshop Download Overhaul

- **Real per-MB progress** — downloads report `X / Y MB (Z%)` against Steam's reported file size
  (`/api/state` → `dl_progress`); the UI bar is now a determinate fill, not an indeterminate stripe.
- **Stage → verify → promote** — DepotDownloader now writes to an `<id>.partial` folder; only
  after verifying a `.vpk` is present and the size matches Steam (≥99%) is it promoted to the live
  workshop dir. Failed/cancelled/partial downloads are deleted instead of leaving empty folders.
- **Fixed the download UI not updating live** — the progress bar/status only refreshed on a tab
  switch because the update code gated on `currentPage === 'workshop'` (the page is actually
  `maps`); removed the bad guard. Also fixed a stale grid id and a post-cancel flicker.

### 🚩 Workshop Command-Filter Automation

Some workshop maps need `-disable_workshop_command_filtering` (their map logic runs server
commands CS2 otherwise blocks). The tool now:
- **Auto-detects** the need by scanning each map's Steam description for the flag.
- Adds the launch flag **only for flagged workshop maps** (filter stays on for everything else).
- Provides a per-map override chip (auto → ON → OFF) and a "Scan command-filter needs" button.
- Persists results in the config (`cmdfilter_auto` / `cmdfilter_override`).

### 🧟 Zombie / Mode Plumbing

- **Zombie Escape ZM fix** — `zombie_ze`'s `cs2fixes.cfg` is now a full copy of the base config
  with `zm_enable 1` (the previous 3-line override clobbered the whole config). Zombie Escape now
  also allows official (non-workshop) maps.
- **Mode-switch hardening** — plugin-swapping mode changes route through a clean
  stop → wait-for-exit → start (`_restart_into`); a lifecycle `RLock` makes start/stop/boot/crash
  transitions atomic; `stop_server` is non-blocking (fixes the dropped-fetch "stop button" bug).

### 🎨 UI & Diagnostics

- **Keyboard cheat sheet** — `?` (or a header `?` button) opens a shortcuts overlay; `Esc` closes.
- **Richer empty states** — Players / Workshop / Presets / Bans now show an icon + title +
  call-to-action instead of plain text.
- **Darker theme** — base surfaces and ambient glow toned down a notch from the v0.9.0 lift;
  the top-left ambient glow further dimmed so it no longer washes out the sidebar.
- **Sharper app icon** — `emblem.ico` regenerated from the hi-res source, square-padded and
  LANCZOS-downscaled at every size (16–256), fixing the pixelated taskbar icon.
- **Status fixes** — Public IP click now copies `connect ip:port`; the Start button keeps a
  full border when it's the only control shown.
- **`-condebug`** added to the server launch so the full engine console (incl. native crash
  output) is captured to `csgo/console.log` — this is what finally pinned the Jailbreak crash.

### 📚 Documentation

- Added [BIBLE.md](BIBLE.md), [ROADMAP.md](ROADMAP.md), [TODO.md](TODO.md), and
  [INGEST.md](INGEST.md) — project vision, phased plan, working checklist, and a structural
  index of the source tree.
- README plugin table reflects B3none Retakes and the full per-mode plugin set.

---

## v0.9.0 — 2026-05-26

This is the largest update yet. The UI has been comprehensively redesigned with theming support, a new Appearance & Settings section, fully configurable keybinds, and a raft of quality-of-life improvements to workshop management, map browsing, and day-to-day server operation.

### 🔌 Plugin Audit & Warcraft Mode

#### Removed deprecated / abandoned plugins
Eight plugins whose upstream repos were archived or had no meaningful update in 2+ years have been removed:

| Plugin | Reason |
|--------|--------|
| ZombieSharp | Repo archived Nov 2025 |
| SharpTimer | Repo archived Jun 2024 |
| LiteMapChooser (RockTheVote) | Last release Apr 2024, ~2 years stale |
| cs2-gungame | Last release May 2024, ~2 years stale |
| cs2-deathrun-manager | Last release Sep 2024, ~2 years stale |
| cs2-instaplant | Last release Dec 2023, abandoned |
| ScoutsNKnives | Single release Nov 2023, abandoned |
| cs2-OneInTheChamber | No traceable repository |

The following game modes were removed along with their core plugins: **Zombies**, **Surf**, **KZ / Climb**, **Gun Game**, **Deathrun**, **Scouts & Knives**, **One in the Chamber**.

#### Added Warcraft mode
A new **Warcraft** game mode backed by [CS2-Warcraft-Plugin v4.1.1](https://github.com/NightFuryPrime/CS2-Warcraft-Plugin) (released 2026-05-25). Features nine RPG character classes (Barbarian, Mage, Necromancer, Paladin, Ranger, Rogue, Shapeshifter, Tinker, ShadowBlade), XP-based levelling to 16, unlockable ultimates, and purchasable magical items. Runs on any standard map.

#### Plugin bundle updates
All bundled plugins have been audited for map coverage and updated to their latest releases:

- **cs2-retakes → v3.0.4** — re-pulled with the full map-configs release; spawn points are now pre-configured for all 10 official maps plus `de_ancient_night`
- **RetakesAllocator → v2.4.2** (yonilerner/cs2-retakes-allocator) — updated from the stale B3none build
- **MatchZy → v0.8.15** — refreshed bundle with latest coach-spawn configs
- **K4-Arenas-Bots → v2.0.8** — updated; corrected copy rule (no longer requires an `extracted/` staging folder)
- **CS2Fixes (MetaMod)** — assigned to Deathmatch and Jailbreak modes for engine-level stability and hit-registration improvements
- **Deathmatch map pool** — restricted to the four maps with pre-configured spawns (`de_dust2`, `de_inferno`, `de_mirage`, `de_vertigo`); remaining maps can be added using the in-game spawn editor

---

### ✨ New Features

#### Appearance & Settings Tab
A dedicated settings page accessible from the sidebar.

- **Theme selector** — Dark, Light, and System (follows OS preference)
- **Accent colours** — choose from Purple, Blue, Teal, Green, Orange, or Red; the accent flows through every button, glow, border highlight, and background radial gradient
- **Compact mode** — tighter spacing throughout the UI for smaller displays
- **Confirm before stopping** — optional confirmation dialog before shutting down the server
- **Auto-scroll log** — keep the live log pinned to the latest entry
- **Log line limit** — configurable memory cap (200 / 400 / 800 lines)
- **Browser notifications** — desktop alerts when the server starts, stops, or crashes

#### Keybinds
Configure keyboard shortcuts for any server action — ideal for private hosting where alt-tabbing is impractical.

- Bindable actions: **Stop Server**, **Quick Restart**, **Pause Match**, **Unpause Match**, **Restart Round**, **End Warmup**, **Add Bot**, **Kick All Bots**
- Click any keybind field → press your key (F1–F12 work unmodified; any key works with Ctrl / Alt / Shift)
- Backspace / Delete clears a binding; Escape cancels
- Conflict detection — warns if a key is already bound to another action
- Binds are saved to localStorage and survive app restarts
- Global handler never fires while typing in a text field or while a modal is open

#### Quick Restart
A new circular-arrow button sits between Start and Stop on the Status page.

- Saves the current map and game mode before stopping
- Stops the server, polls until the process exits (up to 30 s), then starts it again with the exact same settings
- No dropdowns to reconfigure — one click is all it takes
- Also available as a keybind

#### Map Search
A search box on the Maps page lets you filter by name in real time.

- Searches official maps by ID and workshop maps by name or ID simultaneously
- Section headings and the Workshop divider hide automatically when their section has no results
- Filter persists when switching game modes

#### Workshop Download Improvements
- **Live status bar** — replaces the plain 5 px progress stripe with a pulsing dot and real-time status text fed directly from DepotDownloader output (e.g. *Downloading workshop item…*, *… downloading (30s)*, *✓ Download complete*)
- **Automatic button reset** — when a download finishes, the Cancel button reverts to Download and the maps grid refreshes automatically to show the new map
- **Paste button** — a clipboard icon inside the Workshop Map ID input field; reads your clipboard, strips non-numeric characters (so pasting a full Steam URL works), and fills the field
- **Pre-flight credential check** — attempting to download without saved Steam credentials now returns an immediate error with a redirect to Config → Steam Account, instead of silently failing in the background log

#### Bundled Map Thumbnails
All official map thumbnail images are now shipped inside the application package.

- Eliminates all runtime CDN dependency — no Liquipedia requests, no network errors, no per-user hammering of a third-party server
- Falls back to the CS2 dedicated server's own panorama directory if the server is installed locally (higher resolution)

---

### 🎨 UI & UX Improvements

- **Neon glow background** — subtle layered radial gradients on the main app background that shift hue with the selected accent colour; light theme uses a much softer version
- **Session active indicator** — a pulsing green dot labelled "Session active" sits above the sign-out button in the sidebar, making the auth state visible at a glance
- **Sign Out** — the logout button is renamed "Sign Out" with a tooltip describing what it does; clicking it ends the PIN session and returns to the lock screen
- **Official / Workshop map divider** — a centred label with fading border lines separates the two map sections on both the Maps page and the Status page dropdowns
- **GSLT Token label** — renamed from the jargon abbreviation to **"Game Server Login Token (GSLT)"** with a descriptive hint linking to steamcommunity.com/dev/managegameservers and explicitly noting it can be added later if skipped during setup
- **Workshop map names** — the Workshop dropdown on the Status page now shows map names fetched from the Steam API rather than raw numeric IDs
- **Copy Log button** — a button in the live log header copies the entire visible log to the clipboard

---

### 🔧 Server Engine Fixes

#### gameinfo.gi Auto-Management
The tool now fully automates the `gameinfo.gi` patching lifecycle required by MetaMod/CounterStrikeSharp.

- **Auto-patch** — when starting a mode that requires MetaMod or CSS plugins, the MetaMod search path is added to `gameinfo.gi` automatically (restoring from a `.oblivion.bak` backup if one exists)
- **Auto-unpatch** — when switching to a vanilla mode (Competitive, Casual, Wingman), the MetaMod entry is removed from `gameinfo.gi` automatically; this fixes the `0xE0434352` CLR crash caused by an outdated CSS build loading on vanilla servers
- Idempotent — safe to call multiple times; skipped if the file is already in the correct state

---

### 🐛 Bug Fixes

- **Light theme subtitle colour** — `--sub` was set to an invalid 7-digit hex value (`#6060880`), causing all subtitle text to silently inherit the dark-theme colour; fixed to `#606088`
- **Map thumbnail path** — the panorama thumbnail lookup was constructed from the steamcmd root directory, skipping the `steamapps/common/Counter-Strike Global Offensive/` middle segment; the correct CS2 install root is now derived from `CS2_ADDONS_DIR`
- **Quick Restart race condition** — the background state poll interval is paused for the duration of a restart sequence so it cannot concurrently re-enable the Restart button while the shutdown wait is in progress
- **Workshop download status — mode guard** — `_updateDlStatusUI` no longer calls `loadWorkshopMapsGrid` before the first state poll has returned a game mode, preventing a silent fallback to Competitive mode for the map-click handler
- **Keybind localStorage merge** — `loadAppSettings` now deep-merges the `keybinds` sub-object so newly added keybind actions are not silently discarded when upgrading from an older settings snapshot
- **Keybind Space key** — binding the Space key previously stored an invisible character in localStorage; it is now stored and displayed as `Space`
- **Workshop section separator** — the Workshop divider no longer disappears prematurely while the workshop map grid is still loading
- **Quick Restart button height** — `.btn-icon` was missing an explicit height, causing the button to render shorter than the flanking Start/Stop buttons
- **Keybind row border** — the fragile `nth-last-child(2):nth-child(odd)` CSS rule incorrectly removed borders at narrow viewport widths; simplified to `:last-child` only

---

### 📦 Build / Installer

- Inno Setup architecture identifier updated from deprecated `x64` to `x64compatible`
- Added `UsedUserAreasWarning=no` to suppress the HKCU + admin install warning

---

*Previous release: v0.8.6*
