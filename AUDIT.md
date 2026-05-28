# AUDIT — Oblivion Server Tool

> Full read-only code audit, 2026-05-28. Findings are grouped by severity and
> deduplicated across the Python core, the Flask/RCON layer, the frontend SPA, and the
> supporting/build files. `✓ verified` means the finding was confirmed by direct read of
> the cited lines; others are agent-reported and should be confirmed before fixing.
>
> Feeds [ROADMAP.md](ROADMAP.md) Phase 3 (Harden & Secure). Track fixes in [TODO.md](TODO.md).

---

## Threat model note

The single most important context: **the Flask panel binds `0.0.0.0:5000` over plain HTTP,
and a connected CS2 game player controls their own in-game name.** That means two distinct
untrusted actors exist — anyone on the LAN who can reach the panel, *and any player who
joins the game server*. Player names flow from the game into the admin's browser, so a
player who never touches the panel can still attack the admin. This elevates the frontend
XSS findings to the top of the list.

---

## CRITICAL

### C1 — Player-triggerable stored XSS via in-game names ✓ verified
- **Location:** [app.js:873, 876-877](cs2servergui/static/js/app.js:873) (`loadPlayers`); same pattern in `loadBans` (app.js:912-914) and `loadWorkshopMapsGrid` (app.js:1050, 1045).
- **Issue:** `p.name` is interpolated raw into `row.innerHTML` and into `data-name="${p.name}"`. A player named `<img src=x onerror=...>` executes arbitrary JS in the admin's session the moment the Players page renders. Ban entries (persisted names) and workshop titles (third-party) are the same bug. This is exploitable by **any player who joins the server** — no panel access needed.
- **Fix:** Build rows with `textContent` / `createElement` and set `dataset.name` via property assignment; never interpolate untrusted strings into `innerHTML`. Add a shared `escapeHtml()` helper and route all dynamic content through it.

### C2 — `admin_pin` returned in cleartext to every authenticated client ✓ verified
- **Location:** [web.py:429](cs2servergui/web.py:429) (`config_get`).
- **Issue:** `"admin_pin": core.admin_pin` is **not** gated on `is_local` (unlike `gslt_token`/`rcon_password`/`steam_username` right beside it). The PIN — the only credential protecting the whole panel — is sent to any remote session and rendered into the remote DOM ([app.js:1289](cs2servergui/static/js/app.js:1289)).
- **Fix:** Never return `admin_pin`. The template already passes `pin_len`; the config page needs at most a "set/unset" boolean.

### C3 — `sv_password` returned unmasked to remote clients ✓ verified
- **Location:** [web.py:422](cs2servergui/web.py:422) (`config_get`); rendered at [app.js:1234-1236](cs2servergui/static/js/app.js:1234).
- **Issue:** The server join password is returned to all authenticated clients with no `is_local` gate.
- **Fix:** `core.sv_password if is_local else "***"`.

### C4 — PIN and startup-token compared with non-constant-time `==` ✓ verified
- **Location:** [web.py:176](cs2servergui/web.py:176) (`auth_login`), [web.py:154](cs2servergui/web.py:154) (`auto_auth`).
- **Issue:** `pin == core.admin_pin` short-circuits per character, leaking a timing oracle. With a 4-digit numeric PIN (keyspace 10k) and per-IP-only lockout (H6), this is a real weakening of the only credential.
- **Fix:** `secrets.compare_digest(...)` for both comparisons; enforce a longer minimum PIN.

---

## HIGH

### H1 — `/auth/auto` grants a permanent local-privileged session to any caller with the token ✓ verified
- **Location:** [web.py:150-164](cs2servergui/web.py:150).
- **Issue:** The token is honored regardless of `request.remote_addr`; the resulting session is `is_local=True` and never expires. The token is passed in a URL ([main.py](main.py)), which can land in logs/history. If it leaks, a remote client gets full local powers (install, Steam creds, server-dir picker, unmasked secrets).
- **Fix:** Reject `/auth/auto` unless `request.remote_addr` is loopback (`127.0.0.1`/`::1`) before honoring the token.

### H2 — `admin_pin` can be changed by any authenticated (incl. remote) client ✓ verified
- **Location:** [web.py:451-455](cs2servergui/web.py:451) (`config_set`) — the PIN write is in the "any authenticated client may change" block, not the `is_local` block.
- **Issue:** A remote session can silently reset the PIN (and with C2, read the old one first) — owner lockout / persistent access.
- **Fix:** Move `admin_pin` changes into the `is_local`-only block, and/or require the current PIN to be re-supplied.

### H3 — `/api/rcon` passthrough is remote-reachable + RCON command injection
- **Location:** [web.py:512](cs2servergui/web.py:512) (`rcon_exec`) → `core.rcon_execute`; map/say/kick/ban paths interpolate raw strings into RCON command lines at core.py:943-944 (`changelevel {map}`/`host_workshop_map {map}`), 2271 (`say {msg}`), 2200 (`banid {dur} {steamid}`).
- **Issue:** Any authenticated remote session can run arbitrary console commands. Separately, CS2's console treats `;` and newlines as command separators, so an unvalidated `map_name` like `de_x; sv_cheats 1` chains extra commands. `userid`/`steamid` are only `.strip()`-ed.
- **Fix:** Make `/api/rcon` `@require_local`; validate `map_name` (`^[a-z0-9_]+$` or against `OFFICIAL_MAPS`), `userid` (`^\d+$`), `steamid` (SteamID format); strip `;`/newlines from chat.

### H4 — Steam username + password passed as process arguments
- **Location:** core.py:1879-1884 (`steam_login_interactive`), 1989-1993 / 2026-2028 (`depotdl_download`).
- **Issue:** Credentials on the command line are visible to any local process via the process list (the app itself reads `CommandLine` via WMIC elsewhere). Local plaintext credential disclosure.
- **Fix:** Feed credentials via stdin or a restricted-ACL temp file rather than argv.

### H5 — No client handling of 401 / expired session
- **Location:** api.js `req` (~lines 10-20); `pollState` swallows errors (app.js:319-326); SSE reconnects forever (app.js:392-397).
- **Issue:** When the session expires, every call 401s, the SPA appears frozen, and the SSE stream enters an uncapped 5s reconnect storm. The user is never redirected to login.
- **Fix:** In `api.req`, on `r.status === 401` do `location.reload()` (server renders login); cap SSE reconnects.

### H6 — Brute-force defense is per-IP only; transport is cleartext
- **Location:** web.py lockout keyed on `request.remote_addr` (web.py:170-188); cookies set without `Secure` (web.py:159-162, 181-184); bind plain HTTP on `0.0.0.0` (main.py).
- **Issue:** Lockout is the only brute-force barrier and is per-IP; the cleartext LAN transport also exposes the PIN, all secrets, and the session cookie to passive sniffing. Sessions are not re-validated against their origin IP, so a stolen cookie works from any IP for 8h.
- **Fix:** Add a global attempt/backoff counter; serve the remote panel over TLS (self-signed OK) with `secure=True` cookies; bind the session to its creation IP.

### H7 — No lock guards lifecycle state shared across daemon threads
- **Location:** core.py — `running`, `proc`, `boot_state`, `player_count`, `_uptime_start`, `current_map/mode` written from `start_server`, `stop_server`, `_poll_rcon_ready`, `probe_existing_server`, `_handle_crash`, `_player_count_loop`, `change_map`.
- **Issue:** Concrete race: Stop sets `running=False`/`proc=None` while the boot poller is between its `running` check and its `boot_state="ready"` assignment, re-marking a just-stopped server "ready". Multiple such windows exist.
- **Fix:** A single `threading.RLock` guarding the lifecycle state block, taken in every start/stop/poll/crash path.

### H8 — `stop_server` can trigger a spurious crash + auto-restart
- **Location:** core.py:752-796 vs the crash monitor `_watch`/`_handle_crash` (2493-2534).
- **Issue:** `stop_server` calls `proc.terminate()` but only sets `running=False` *after* `wait(timeout=5)`. In that up-to-5s window the monitor sees `proc.poll() != None` while `running` is still True → logs "exited unexpectedly" → **auto-restarts a server the user just stopped**.
- **Fix:** Set `running=False` and snapshot/clear `proc` at the very start of `stop_server`, before terminate.

### H9 — Stale duplicate `OblivionServerTool/` tree with a broken build script
- **Location:** `OblivionServerTool/` subtree (its `build.bat`, `.spec`, `main.py`).
- **Issue:** A divergent full copy of the project whose `build.bat` still `--collect-all customtkinter`/`PIL` and imports `cs2servergui.gui` — modules the current pywebview app no longer uses. Building from there yields a broken artefact; it's a packaging trap. (It's also gitignored per `.gitignore`, so it's untracked clutter.)
- **Fix:** Delete the stale subtree (or move it out of the repo).

---

## MEDIUM

### M1 — RCON `_recv` does not handle multi-packet responses
- **Location:** rcon.py:36-55 (`_recv`), used by `execute`/`execute_many`.
- **Issue:** Source RCON splits responses >4096 bytes across multiple packets; `_recv` reads exactly one, silently truncating large output (`status` on a full server, `cvarlist`) and desyncing `execute_many`.
- **Fix:** Implement the empty-sentinel-packet read loop, accumulating type-0 bodies until the sentinel id returns.

### M2 — `change_map` commits state even when `changelevel` silently failed
- **Location:** core.py:943-948.
- **Issue:** RCON returns a body (not an exception) on an unknown map; the code unconditionally sets `current_map`/`current_mode` and logs success, so the UI shows a map that never loaded.
- **Fix:** Inspect the response for failure markers before committing state.

### M3 — Auto-restart infers workshop-vs-official from `current_map.isdigit()`
- **Location:** core.py:2483 (`_handle_crash`).
- **Issue:** Fragile heuristic; a stale workshop ID or odd map name picks the wrong launch path. Restart also runs synchronously inside the monitor thread, blocking crash detection for its duration.
- **Fix:** Track `is_workshop` explicitly in state; run the restart on its own thread.

### M4 — `start_server` leaves inconsistent state if launch fails after deploy
- **Location:** core.py:647-735.
- **Issue:** Plugins/`gameinfo.gi` are modified and lingering cs2.exe killed *before* `Popen`; if `Popen` raises, `running`/`boot_state` were never set, no rollback, no `on_state_change`. Non-`FileNotFoundError` exceptions aren't caught at all.
- **Fix:** Wrap the launch broadly; reset state and log on any failure.

### M5 — `_kill_zombie_instance` force-kills whatever owns port 5000
- **Location:** main.py:70-77.
- **Issue:** Finds the PID listening on 5000 via netstat and `taskkill /F` with no check it's a prior instance — kills any unrelated app holding the port.
- **Fix:** Verify the owning process image name before killing.

### M6 — Flask bind failure is silent (only surfaces as a 10s timeout)
- **Location:** main.py:132-146.
- **Issue:** `flask_app.run` runs in a daemon thread; a bind error dies silently, surfacing only as a generic timeout + `sys.exit(1)`.
- **Fix:** Capture the exception from the thread and report it in the timeout branch.

### M7 — `os._exit(0)` can truncate an in-flight config write
- **Location:** main.py:213.
- **Issue:** Deliberate (to kill WebView2), but skips atexit/flush; a config save on another thread at window-close can be truncated.
- **Fix:** Make config writes atomic (temp file + `os.replace`); flush pending save before exit.

### M8 — `rcon_password` and `admin_pin` stored plaintext in `oblivion_config.json`
- **Location:** core.py `save_config` (~542-543); only the Steam password uses the keyring.
- **Issue:** Two more secrets sit in plaintext on disk.
- **Fix:** Store them in the keyring (or hash the PIN) like the Steam password.

### M9 — Hardcoded `D:\steamcmd` default server dir
- **Location:** config.py:51.
- **Issue:** Machine-specific absolute default; all derived paths point at a non-existent drive on a clean install until setup runs.
- **Fix:** Default to empty (forcing setup) or a `%USERPROFILE%`-relative path.

### M10 — Unescaped config field values and preset names in the DOM
- **Location:** app.js config page `value="${cfg.x || ''}"` (1233-1350); preset chips (1485); error messages via innerHTML (894, 924, 1062).
- **Issue:** A hostname/server_dir/preset name containing `"` breaks the attribute and can inject a handler; preset names round-trip through saved config (stored XSS, admin-self-inflicted).
- **Fix:** Set `input.value` via property; render preset names and errors via `textContent`.

### M11 — Background poll re-enables buttons mid-action; no CSRF token
- **Location:** app.js `pollState`/`renderStatusState` (319, 442-452); all state-changing POSTs.
- **Issue:** Only Quick Restart pauses the 3s poll; a plain Start/Stop can be clobbered by a mid-flight poll (flicker / double-click). CSRF risk is largely mitigated by `SameSite=Strict` but there's no defense-in-depth token.
- **Fix:** Optimistically disable buttons on click (or pause the interval during the request); optionally add a CSRF header.

### M12 — `loadAppSettings` merges localStorage with no type validation
- **Location:** app.js:85-96.
- **Issue:** `Object.assign(appSettings, rest)` copies any keys; a corrupted blob can set `logLines` to a non-number used in slices, or `accent` to an unknown value.
- **Fix:** Coerce/validate known fields after merge.

### M13 — Map thumbnail path not URL-encoded
- **Location:** app.js:482, 1003 (`/api/maps/thumb/${mapKey}`).
- **Issue:** Workshop keys dropped into a URL path without `encodeURIComponent`.
- **Fix:** `encodeURIComponent(mapKey)`. (Server side validates `^[a-z0-9_]+$`, so impact is limited.)

---

## LOW

- **L1** — `urllib.request.urlretrieve` for steamcmd.zip / DepotDownloader.zip has no timeout and can hang (core.py:1096, 1941); `zf.extractall` without member validation (Zip Slip, trusted sources) at 1098, 1943.
- **L2** — `start_monitor` has no singleton guard; if called twice, duplicate `_watch`/`_player_count_loop` and racing restart counters (core.py:2435+).
- **L3** — `_semver_tuple` treats `1.0` as older than `1.0.0` (variable-length tuple compare); catches only `ValueError` (core.py:80-94).
- **L4** — `_lan_ip()` is computed once at import; stale after VPN/NIC change (config.py:87).
- **L5** — `requirements.txt` is lower-bound-only (`flask>=3.0.0`, …); a future major bump could break builds.
- **L6** — RCON id counter is unbounded (overflows `<i` after ~2.1B commands) and response id is never asserted to match the request id (rcon.py:22-29, 71).
- **L7** — CSS: `.toggle`/`.toggle-thumb` defined twice with different widths/mechanisms; `@keyframes pulse-dot` defined twice; `--accent-rgb` referenced but never defined so `.mode-hint` background never follows the accent (app.css:410-427/1149-1163, 168/831, 374).
- **L8** — `sb-lan.onclick` reassigned every poll (app.js:291 vs 1957); harmless but wasteful and the `init` binding is dead.
- **L9** — Possibly-stale `'Retakes'` entry in client `MODE_HINTS` (app.js:737-739) — verify it still matches server mode data after the MatchZy migration.
- **L10** — `/api/rcon`, `/api/players`, `/api/bans` hold a Flask worker up to 8-10s on `done.wait`; a few concurrent slow RCON calls can stall the UI (web.py:361, 400, 528).
- **L11** — Various kill/keyring/tasklist blocks swallow exceptions silently (core.py:462, 471, 705, 788, 818, 1853); WMIC CSV PID parsing via `parts[-1]` is fragile.

---

## Confirmed CLEAN (no action)

- **Plugin/mode table consistency** — All 14 `GAME_MODES` appear with matching case in `MODE_SETTINGS`/`MODE_MAPS`/`MODE_WORKSHOP_SEARCH`/`MODE_WORKSHOP_TAGS`. Every plugin key in `_MODE_PLUGIN_NAMES` (zombie, zombie_ze, deathmatch, arenas, practice, jailbreak, warcraft) is fully populated across `_PLUGIN_KIND`/`_PLUGIN_COPY_RULES`/`_PLUGIN_VERIFY_FILES`/`_PLUGIN_CLEANUP_ITEMS`, has a matching folder on disk with the expected `addons/` layout. **No leftover `retakes`/`CS2Retake` plugin key** — the Retakes→`practice` (MatchZy) migration is internally consistent; `retakes.cfg` is deployed and cleaned up under `practice`.
- **`MODE_SETTINGS` rulesets** — game_type/game_mode combos correct; the unusual ones (Jailbreak 0/2, ZE 0/0, Warcraft 0/0) are documented.
- **Flask debug mode** — off (`debug=False`); no `app.run(debug=True)`.
- **No Flask `SECRET_KEY` weakness** — the app uses a custom server-side session store, not signed cookies; session tokens are `secrets.token_hex(32)`; startup token is single-use and unguessable.
- **Path traversal** — `/api/maps/thumb` validates `^[a-z0-9_]+$` before any join; workshop download/request endpoints enforce `wid.isdigit()`.
- **OS-shell injection** — `start_server` and WMIC/taskkill use argv lists, not `shell=True` (the RCON-string injection in H3 is separate).
- **RCON socket hygiene** — `settimeout(5)` + `with socket(...)`; no hang-forever, no leaked sockets; `execute_retry` doesn't retry auth failures.
- **SSE server-side cleanup** — `log_stream` unsubscribes in `finally`.
- **Log buffer** — bounded via `deque(maxlen=300)`.
- **Version consistency** — `APP_VERSION "0.9.1"` matches `installer.iss`.
- **PyInstaller bundling** — plugins/templates/static/icon bundled; `_resolve_plugins_base()` handles the frozen `_MEIPASS` path.
- **No leftover `console.log`/`TODO`/`FIXME`** in the frontend.

---

## Suggested fix order

1. **C1** (player XSS) — exploitable by any game player; central `escapeHtml` + `textContent` everywhere untrusted data renders.
2. **C2 / C3 / H2** — stop returning `admin_pin`/`sv_password` to remote clients and stop letting remote clients change the PIN (small edits to `config_get`/`config_set`).
3. **H1** — loopback-restrict `/auth/auto`.
4. **H3** — `@require_local` on `/api/rcon` + validate map/userid/steamid.
5. **C4** — `secrets.compare_digest` + longer PIN.
6. **H7 / H8** — lifecycle lock + fix the stop→spurious-restart window.
7. **H5** — frontend 401 → login redirect.
8. Remainder by severity.
