# INGEST — Structural Index

A complete structural index of every class and function across the source tree,
each with a one-sentence summary. No code, no behavioural changes — reference only.

---

## `main.py` (270 lines)

| Symbol | Summary |
|--------|---------|
| `_enable_high_dpi()` | Makes the process per-monitor DPI-aware on Windows before any UI is created. |
| `_port_in_use` (alias) | Re-exported from `cs2servergui._netutils.port_in_use` — single source of truth for the port-in-use TCP probe. |
| `_holder_of_port` (alias) | Re-exported from `cs2servergui._netutils.holder_of_port` — netstat+tasklist lookup of the PID listening on a port. |
| `_kill_zombie_instance(port)` | If the Flask port is held by a previous instance (zombie Edge/pywebview process), finds the PID via `_holder_of_port` and `taskkill`s it (only if the image name is one of ours), then waits for release. |
| `_pick_free_port(start, count)` | Returns the first free port in `[start, start+count)`, or `None` if all are taken. |
| `_wait_for_flask(port, timeout)` | Polls `/api/ping` until Flask accepts connections or the timeout expires. |
| `_select_flask_port(configured)` | Kills our own zombie on `configured` if any, returns it if free; otherwise falls back to the first free port in `[configured+1, configured+4)`. |
| `main()` | Bootstrap: builds `AppCore`, sets a one-time `startup_token`, starts the crash monitor, probes for an existing server, picks a Flask port via `_select_flask_port`, binds via `werkzeug.serving.make_server` in the main thread (no TOCTOU), starts `serve_forever` in a daemon thread, fires update/IP checks, opens the pywebview window at the `/auth/auto` URL, calls `core.save_config()` synchronously on close, then `os._exit(0)`. |
| `if __name__ == "__main__":` | Calls `main()`. |

---

## `cs2servergui/__init__.py` (1 line)

| Symbol | Summary |
|--------|---------|
| *(module)* | Package marker — single comment line, no exports. |

---

## `cs2servergui/veto.py` (NEW — 614 lines)

Backend state machine for the v0.10.0 map-veto / match-setup feature.
AppCore owns at most one `VetoSession` at a time, serialised by
`AppCore._veto_lock`.  Web routes in `web.py` are thin wrappers over the
functions here; SSE streams state changes to the SPA mirror.

### Dataclasses

| Symbol | Summary |
|--------|---------|
| `RosterPlayer(name, steam_id="")` | One roster slot.  `steam_id` collected at roster time for MatchZy strict team assignment. |
| `VetoStep(kind, team, map_id="")` | One slot in the veto sequence.  `kind` is `"BAN"` / `"PICK"`; `map_id` filled when the captain acts. |
| `CaptainToken(team, value, issued_at, claimed_by="", used=False)` | Scoped, single-use credential for a captain's web link.  `secrets.token_urlsafe(32)` value (~256 bits entropy). |
| `VetoSession` | Whole match-setup session.  States: `idle → roster → teams → voting → links → veto → finale → complete`. |

### State-machine functions

| Symbol | Summary |
|--------|---------|
| `create_session(mode="BO3", map_pool=None) -> VetoSession` | Builds a fresh session in `roster` state.  Default pool = `config.ACTIVE_DUTY_POOL` (7 maps); per-veto override accepted. |
| `set_roster(s, a_name, b_name, players)` | Saves the 10-player roster.  Validates non-empty unique names. |
| `distribute_teams(s, rng=None)` | Random 5-5 split.  Re-callable in `teams` state to reshuffle (clears any pending votes). |
| `start_voting(s)` | Move `teams` → `voting`.  Operator-driven. |
| `cast_vote(s, team, voter_idx, votee_idx)` | Record one captain vote.  Re-cast overwrites previous. |
| `voting_complete(s) -> bool` | True if both teams have 5 votes in. |
| `resolve_captains(s) -> str` | Tally → either `'elected'` (state advances to `links`) or `'revote_a'` / `'revote_b'` / `'revote_both'`.  Clears tied side's votes; preserves the clean side. |
| `issue_tokens(s) -> dict[str, str]` | Mint two single-use captain tokens. |
| `claim_captain(s, token, caller_id="") -> str` | Validate + bind a token to its caller.  Idempotent for the same `caller_id`; rejected for anyone else once used.  Builds the veto sequence + advances to `veto` when BOTH tokens are claimed. |
| `revoke_token(s, team) -> str` | Re-issue a fresh token for `team` (operator action when a captain loses their link).  Drops state back to `links` if in `veto`. |
| `current_step(s) -> VetoStep \| None` | The next step in the sequence, or `None` if veto is complete. |
| `remaining_maps(s) -> list[str]` | Maps still in the pool after bans so far. |
| `perform_step(s, team, map_id)` | Captain performs the current step.  Validates: state, team's turn, map still legal.  Identifies the decider + advances to `finale` on the last step. |
| `build_matchzy_config(s) -> dict` | Generates MatchZy `match.json` shape — `matchid`, `maplist`, `players_per_team`, `team1` / `team2` with SteamID→name maps, `cvars`, `_oblivion_meta` audit trail. |
| `complete(s)` | Mark session complete (after MatchZy hand-off). |
| `reset(s)` | Return to `idle` with all per-session state cleared.  Legal from any state. |

### Exceptions

| Symbol | Summary |
|--------|---------|
| `VetoError` | Base class — web.py returns 400 on any subclass. |
| `InvalidVetoTransition` | State machine refused the requested transition. |
| `VetoStageError` | Stage-specific validation failure (incomplete roster, wrong turn, etc.). |

### Module-level constants

| Symbol | Summary |
|--------|---------|
| `_VETO_SEQUENCES` | Maps mode (`"BO1"`/`"BO3"`/`"BO5"`) → list of `(kind, team)` tuples.  Decider computed at runtime, not stored. |
| `_LEGAL_TRANSITIONS` | Maps current state → frozenset of legal next states.  Enforced by `VetoSession._transition()`. |

---

## `cs2servergui/_netutils.py` (98 lines)

| Symbol | Summary |
|--------|---------|
| `_default_log(msg)` | Fallback logger (`print`) for callers that don't provide one — used by `main.py`'s startup path before `AppCore.log` exists. |
| `port_in_use(port, host, timeout)` | Returns `True` if something is already listening on `host:port` (TCP connect probe). |
| `listeners_on_port(port, log)` | Returns every `(bound_address, pid, image_name_lower)` tuple listening on `port` — walks `netstat -ano` and resolves PIDs via `tasklist /FI`. Multiple entries returned when the port is bound to multiple addresses (IPv4 + IPv6, or explicit 0.0.0.0 + ::). |
| `holder_of_port(port, log)` | Thin wrapper over `listeners_on_port` that returns the first listener as `(pid, name)` or `None`. |

---

## `cs2servergui/config.py` (319 lines)

### Functions

| Symbol | Summary |
|--------|---------|
| `_lan_ip(force_refresh=False)` | Returns the machine's primary LAN IP by routing a UDP socket toward 8.8.8.8 (no data sent); falls back to `127.0.0.1`.  Cached 30 s; `force_refresh=True` bypasses the cache (used by `AppCore._resolve_rcon_host` at server start). |
| `_load_int_from_config(key, default)` | Reads one integer from `oblivion_config.json` at module-import time so module-level constants (e.g. `FLASK_PORT`) can pick up user overrides before `AppCore` exists. |
| `update_paths(server_dir)` | Recomputes all path globals (`CS2_SERVER_DIR`, `STEAMCMD_PATH`, `CS2_PATH`, `WORKSHOP_DIR`, `DEPOTDL_PATH`, `CS2_ADDONS_DIR`) from a new base directory. |
| `load_workshop()` | Returns a sorted list of downloaded workshop map IDs (folder names) from `WORKSHOP_DIR`. |

### Constants

| Symbol | Summary |
|--------|---------|
| `CS2_APP_ID` | Steam App ID for CS2 (`"730"`). |
| `DEPOTDL_RELEASE_URL` | GitHub API URL for the latest DepotDownloader release. |
| `APP_VERSION` | Current application version string (currently `"0.9.2.1"`). |
| `APP_REPO` | GitHub repository slug for the app (`"jacquesvniekerk-eng/OblivionServerTool"`). |
| `APP_RELEASES_URL` / `APP_API_URL` | Human and API GitHub release URLs derived from `APP_REPO`. |
| `CS2_SERVER_DIR` / `STEAMCMD_PATH` / `CS2_PATH` / `WORKSHOP_DIR` / `DEPOTDL_PATH` / `CS2_ADDONS_DIR` | Default path constants; all re-set by `update_paths()`. |
| `RCON_HOST` / `RCON_PORT` / `RCON_PASSWORD` | RCON network/auth constants.  `RCON_HOST` is the LAN IP from `_lan_ip()` at import time; mutated at runtime by `AppCore._resolve_rcon_host()` and the netstat-based safety net in `_post_launch_sanity_check`.  Modules MUST read `_config.RCON_HOST` at call time — `from .config import RCON_HOST` captures the stale import-time value. |
| `FLASK_PORT` | Default 5050; reads `flask_port` from `oblivion_config.json` via `_load_int_from_config()` so user overrides survive a build. |
| `_LAN_IP_CACHE` / `_LAN_IP_TTL_SECS` | Internal: state-poll cache (30 s TTL) for `_lan_ip()` to stop hammering UDP probes on every `/api/state`. |
| `_CONFIG_FILE` | Absolute path to `oblivion_config.json`; under `%APPDATA%\Oblivion Server Tool\` when frozen, project root when dev. |
| `OFFICIAL_MAPS` | List of 10 official CS2 competitive maps. |
| `CS2_PANORAMA_THUMBS_SUBPATH` | Relative path from a CS2 install root to the 1080p map screenshot folder. |
| `GAME_MODES` | Ordered list of all 16 supported game mode strings. |
| `MODE_SETTINGS` | Maps mode name → `{game_type, game_mode, maxplayers}` — defines the CS2 ruleset per mode. |
| `MODE_MAPS` | Maps mode name → allowed official map list (or `None` if workshop-only). |
| `MODE_WORKSHOP_SEARCH` | Maps mode name → Steam Workshop search term string. |
| `MODE_WORKSHOP_TAGS` | Maps mode name → list of Steam Workshop tag strings used for filtering downloaded maps. |

---

## `cs2servergui/rcon.py` (173 lines)

| Symbol | Summary |
|--------|---------|
| `RCONClient` | Thread-safe Source RCON client that opens a fresh TCP connection per command. |
| `RCONClient.__init__(host, port, password)` | Stores connection params; per-instance ID counter under a `threading.Lock`. |
| `RCONClient._next_id()` | Returns the next packet ID (thread-safe via `_id_lock`). |
| `RCONClient._pack(pkt_id, pkt_type, body)` | Builds a Source RCON packet: little-endian `(size, id, type)` + body + two null terminators. |
| `RCONClient._recv(sock)` | Reads one complete RCON packet from `sock`, handling partial reads; returns `(pkt_id, pkt_type, body)`. |
| `RCONClient.execute(command)` | Authenticates, sends the command **plus a sentinel empty command** so multi-packet responses (Source RCON's >4 KB split) are concatenated correctly, returns the joined response.  Tolerates the CSGO-era junk type-0 prelude packet. |
| `RCONClient.execute_many(commands)` | Single-auth batch sender for many short commands (does NOT use the multi-packet sentinel; intended for short cvar/bot_add commands where 4 KB truncation is impossible). |
| `RCONClient.execute_retry(command, retries=6, delay=5.0)` | Calls `execute()` with retry on `TimeoutError`, `OSError` (covers `ConnectionResetError` / `ConnectionAbortedError` / `BrokenPipeError` / WinError 10054), and `ConnectionError` except for "auth failed" (never retried). |

---

## `cs2servergui/web.py` (1568 lines)

### Module-level constants

| Symbol | Summary |
|--------|---------|
| `_MAP_NAME_RE` / `_DIGITS_RE` / `_STEAMID_RE` | Input validators.  `_STEAMID_RE` is length-capped to 64 chars to defang 1 MB-input DoS attempts. |
| `_NAME_MAX_LEN` | 64 — applied to `players_kick` `name` before RCON. |
| `_BROADCAST_MAX_LEN` | 200 — applied to `server_broadcast` `message` after `;`/CRLF/backtick strip. |
| `_REMOTE_SESSION_TTL` | 8 hours for remote sessions; local sessions never expire. |
| `_MAX_ATTEMPTS` / `_LOCKOUT_SECS` / `_ATTEMPT_TTL_SECS` | Per-IP PIN brute-force: 5 fails → 300 s lockout; GC entries past 600 s of inactivity. |
| `_GLOBAL_MAX_ATTEMPTS` / `_GLOBAL_LOCKOUT_SECS` / `_GLOBAL_DECAY_SECS` | Global PIN brute-force (defends against distributed attacks): 20 fails → 300 s; resets after 600 s of quiet. |
| `_startup_token_lock` | Serialises the `/auth/auto` compare-and-clear so two simultaneous loopback hits can't both mint a local session. |

### Session store & lockout (module-level)

| Symbol | Summary |
|--------|---------|
| `_create_session(ip, is_local, role)` | Mints a random session token storing `ip`, `is_local`, `role` (`"admin"`/`"guest"`), and `created_at`. |
| `_get_session(token)` / `_clear_session(token)` | Look up a session (expiring remote ones past `_REMOTE_SESSION_TTL`) / drop a session. |
| `_check_lockout(ip)` / `_record_fail(ip)` / `_clear_attempts(ip)` | Per-IP PIN brute-force lockout (5 fails → 300 s), with TTL-based GC so the dict can't grow unboundedly under a slow attack. |
| `_check_global_lockout()` / `_record_global_fail()` / `_clear_global()` | Global backoff defending against distributed brute force. |

### Factory & auth (inner to `create_flask`)

| Symbol | Summary |
|--------|---------|
| `create_flask(core)` | Factory that builds and returns the complete Flask app wired to the given `AppCore`. |
| `_current_session()` | Returns the session dict for the request's `session` cookie, or `None`. |
| `require_auth(f)` | Decorator: 401 unless a valid session exists; binds remote sessions to their origin IP. |
| `require_local(f)` | Decorator (stacks on `require_auth`): 403 unless the session `is_local` — keeps RCON/install/Steam strictly on the desktop window. |
| `_role_gate()` | `@app.before_request` **fail-closed** role enforcer.  Allowlists per role: `_PUBLIC_PATHS` (no auth), `_GUEST_PATHS` (guest PIN), `_CAPTAIN_PATHS` (v0.10.0 — scoped session minted by `/api/veto/claim`).  Admin / `is_local` pass everything.  Any role hitting an off-allowlist `/api/*` route → 403. |

### Auth routes

| Route | Summary |
|-------|---------|
| `GET /auth/auto` | One-time `startup_token` endpoint (loopback only) that mints a local admin session for the pywebview window. |
| `POST /api/auth/login` | Validates the PIN against the admin PIN (→ `admin`) or guest PIN (→ `guest`); sets the session cookie; enforces per-IP + global lockout. |
| `POST /api/auth/logout` | Clears the session and deletes the cookie. |
| `GET /api/ping` | Unauthenticated health check. |
| `GET /` | Renders the SPA shell (`index.html`). |

### API — server state & control

| Route | Summary |
|-------|---------|
| `GET /api/state` | Full state snapshot (running, boot_state, map, mode, players, public/lan IPs, `flask_port`, `rcon_port`, update flags, `role`, `sv_password_set`, `dl_active`, `dl_progress`). `lan_ip` is the live primary LAN IP (not RCON_HOST). **(guest-allowed)** |
| `POST /api/server/start` | Starts the server with map/mode/workshop flag. *(admin)* |
| `POST /api/server/stop` | Stops the running server. *(admin)* |
| `POST /api/server/map` | Changes map + game mode (RCON or `_restart_into` on plugin swap). **(guest-allowed)** |
| `POST /api/server/broadcast` | RCON `say` chat broadcast.  Strips `;` (Source 2 command separator), CRLF, backtick; caps at 200 chars. *(admin)* |
| `POST /api/server/ff` | Toggles friendly fire. *(admin)* |
| `POST /api/server/round/restart` / `/round/warmup` | `mp_restartgame 1` / `mp_warmup_end`. *(admin)* |
| `POST /api/server/match/pause` / `/match/unpause` | Pause / unpause the match. *(admin)* |
| `POST /api/server/install` / `/update_cs2` | SteamCMD install / in-place update. *(local-only)* |

### API — bots / players / bans  *(all admin; player list is guest-readable)*

| Route | Summary |
|-------|---------|
| `POST /api/bots/add` / `/bots/kick` | Add N bots / kick all bots. |
| `GET /api/players` | Roster parsed from RCON `status`. **(guest-allowed, read-only)** |
| `POST /api/players/kick` / `/players/ban` | Kick by userid / ban by SteamID. |
| `GET /api/bans` / `POST /api/bans/remove` | List bans / unban by SteamID. |

### API — config / presets  *(admin; secrets local-only)*

| Route | Summary |
|-------|---------|
| `GET /api/config` | Returns settings; secrets (`sv_password`, `gslt_token`, `admin_pin`, `guest_pin`, `rcon_password`, `steam_username`) masked as `***` for non-local sessions. |
| `POST /api/config` | Saves settings; security fields (admin/guest PIN, RCON pw, server dir, Steam creds) only writable by the local window. |
| `GET /api/presets` · `POST /api/presets/save` · `/presets/load` · `DELETE /api/presets/<name>` | List / save / load / delete launch presets. |
| `POST /api/rcon` | Arbitrary RCON command. *(local-only)* |

### API — workshop & maps

| Route | Summary |
|-------|---------|
| `GET /api/workshop/maps` | Downloaded maps with cached name/tags/preview/cmdfilter. **(guest-allowed)** |
| `POST /api/workshop/download` | DepotDownloader staged download.  Atomic check-and-reserve under `_dl_lock` — returns 409 if another download is in flight. **(guest-allowed)** |
| `POST /api/workshop/cancel` | Cancel the active download. **(guest-allowed)** |
| `POST /api/workshop/update` | Re-pull subscribed maps that changed. *(local-only)* |
| `POST /api/workshop/cmdfilter/scan` / `/cmdfilter/override` | Scan descriptions for the command-filter flag / per-map override. *(local-only)* |
| `POST /api/request_workshop` | Log a remote download request. **(guest-allowed)** |
| `GET /api/data/modes` · `/data/maps` · `/data/mode_maps` · `/data/mode_workshop_tags` | Static reference data for the SPA. **(public)** |
| `GET /api/maps/thumb/<map_name>` | Proxy a map thumbnail from the local panorama folder. **(public)** |

### API — Steam / setup / system / logs

| Route | Summary |
|-------|---------|
| `POST /api/steam/login` | Interactive steamcmd console for 2FA. *(local-only)* |
| `GET /api/system/pick_directory` | Native folder picker for the server dir. *(local-only)* |
| `GET /api/setup/status` | First-run wizard state. *(local-only — leaked `pin_is_default` to guests before v0.9.2)* |
| `POST /api/setup/complete` | Persist first-run setup. *(local-only)* |
| `GET /api/log/history` / `GET /api/log/stream` | Log history / live SSE stream. *(admin)* |
| `POST /api/log/save` | Writes the in-memory log buffer to a `oblivion_log_<ts>_<6 hex>.txt` in the config dir (collision-proof + size-capped). *(local-only)* |

### API — map veto (v0.10.0)

Thin HTTP wrappers over `cs2servergui/veto.py`.  Every mutation acquires
`core._veto_lock` and broadcasts a snapshot to SSE subscribers.  All
admin-only unless noted.  Captains get a scoped session via `/api/veto/claim`
(no PIN — single-use token is the credential) and can ONLY hit `state`,
`stream`, and `step`.

| Route | Summary |
|-------|---------|
| `GET /api/veto/state` | Current session snapshot (state, roster, teams, votes, sequence, decider, `current_step_detail`, `legal_moves`).  Tokens redacted. **(admin + captain)** |
| `GET /api/veto/stream` | SSE pub/sub for live mirror — pushes a JSON snapshot on every state change.  Initial snapshot delivered immediately on subscribe. **(admin + captain)** |
| `POST /api/veto/create` | `create_session(mode, map_pool)` — starts a fresh session in `roster` state.  Returns 409 if a session is already active (operator must call `/api/veto/reset` first). |
| `POST /api/veto/roster` | `set_roster(team_a_name, team_b_name, players)` — players are `[{name, steam_id}]`. |
| `POST /api/veto/distribute` | Random 5-5 split.  Self-callable in `teams` to reshuffle. |
| `POST /api/veto/start_voting` | `teams` → `voting`. |
| `POST /api/veto/vote` | `cast_vote(team, voter_idx, votee_idx)`. |
| `POST /api/veto/resolve_captains` | Tally → `elected` (→ `links`) or `revote_*` (clears tied side). |
| `POST /api/veto/tokens` | Issue both captain tokens; returns LAN + Public URLs per captain (mirrors Connect popover dual-display). |
| `POST /api/veto/revoke_token` | Re-issue a fresh token for a team (operator action). |
| `POST /api/veto/claim` | Public — token IS the credential.  Mints a captain session cookie. **(public)** |
| `POST /api/veto/step` | `perform_step(team, map_id)`.  Captains can only act for their own team; admins can act for either. **(captain + admin)** |
| `POST /api/veto/finale` | `build_matchzy_config()` + `complete()`.  Logs the planned `matchzy_loadmatch` (full handoff lands on Day 6). |
| `POST /api/veto/reset` | Clear the active session and return to `idle`. |
| `GET /api/veto/qr?token=…&kind=lan\|public` | (v0.10.0 Day 4) SVG QR code for a captain join URL.  Validates token against the live session; admin/local only; cached `private, max-age=300`. |
| `GET /veto?join=<token>` | Captain-link landing page — server-side claim + cookie set, then redirect to `/#veto`. **(public)** |
| `GET /veto` (no token) | Redirects to `/#veto` so the SPA can render the live-mirror page. **(public)** |

Day 4 also extended `POST /api/veto/tokens` and `POST /api/veto/revoke_token` to
include the raw `token` field alongside the LAN/Public URLs so the SPA can build
QR URLs without re-parsing tokens out of links.

### Frontend — Veto tab (v0.10.0 Day 3)

The SPA renders the veto session as a dedicated tab.  Single `pages['veto']`
entry point in `app.js`; state comes from `/api/veto/state` + the SSE stream
(`/api/veto/stream`).  Each session state has its own render function:

| Function | Stage |
|---|---|
| `_renderVetoIdle(root)` | Create-session card (mode pills: BO1/BO3/BO5) |
| `_renderVetoRoster(root, sess)` | 10-slot input grid + paste / demo / save / distribute |
| `_renderVetoTeams(root, sess)` | A/B columns with captain badge after election + reshuffle |
| `_renderVetoVoting(root, sess)` | Per-player vote buttons + revote indicator |
| `_renderVetoLinks(root, sess)` | Captain link cards (LAN + Public URLs + Copy + Revoke + QR codes from `/api/veto/qr`) |
| `_renderVetoBoard(root, sess)` | 7 map cards + turn banner + click-to-act (filtered by `legal_moves`) |
| `_renderVetoFinale(root, sess)` | "Get Ready to Battle" + final maplist + Hand to MatchZy |
| `_renderVetoComplete(root, sess)` | Series summary + Start a new session |
| `_renderVetoCaptain(root, state, sess)` | Captain-role simplified view — only the board + finale |

SSE cleanup on tab-leave hashchange; auto-reconnect on error (5 s backoff,
matches the existing log-stream pattern).

**Cinematics (v0.10.0 Day 5).**  Three layers of animation, all
JS-gated so re-renders from SSE pings on the same state don't restart them:

* `_vetoLastRenderedState` — when the state actually changes, the new
  `.veto-stage` gets a `.veto-stage-enter` class → 320 ms fade-in.
* `_vetoLastSeqLen` — when a new ban/pick lands, only the freshly-acted
  map gets `.just-stamped` → stamp slam-in (520 ms scale + rotate bounce)
  plus a 360 ms card shake.
* `_vetoFinaleShownThisSession` — first arrival at `finale` triggers the
  full reveal: title-rise, sub-fade, staggered map-pop (260 + 80×idx ms),
  decider glow pulse (twice, 900 ms onwards), launch-button fade-in, and
  a 30-piece CSS confetti shower (2.6 s).  All counters reset when state
  flips back to `idle`, so a re-run gets fresh theatrics.

---

## `cs2servergui/core.py` (3692 lines)

### v0.9.2 / v0.9.2.1 additions worth knowing about

The file has grown ~540 lines since this section was first generated.  The
new public-ish surface area on `AppCore` (and a few module-level helpers):

| Symbol | Summary |
|--------|---------|
| `AppCore._resolve_rcon_host()` | Re-resolves `_config._lan_ip(force_refresh=True)` on every server start / attach.  Won't clobber a good IP with `127.0.0.1` if the UDP probe falls back to loopback (v0.9.2.1 guard). |
| `AppCore._preflight_checks(map_name, mode, is_workshop) -> bool` | Runs before `deploy_plugins()`.  Blocks Start on missing CS2, foreign holder of 27015, or missing plugin bundle folders.  Soft-warns on missing Steam creds + DepotDownloader. |
| `AppCore._post_launch_sanity_check()` | Background thread: catches immediate `cs2.exe` death (proc.poll within 3 s) AND enumerates `netstat` listeners on 27015 to switch `self.rcon.host` to whichever bind address actually answers (handles CS2 binding to Hyper-V/Docker/VPN adapters).  Re-checks `self.running` before mutating state. |
| `AppCore._list_dedicated_pids() -> list[int]` | PowerShell `Get-CimInstance` first, `wmic` fallback (deprecated/removed on Win 11 24H2).  Logs loudly when both strategies fail. |
| `AppCore._holder_of_port(port)` / `._listeners_on_port(port)` | Thin instance-method wrappers over `cs2servergui._netutils.holder_of_port` / `listeners_on_port` that pass `self.log` so AppCore picks up netstat diagnostics. |
| `AppCore._validate_bundle_configs(deployed, csgo_dir)` | Walks each deployed plugin's bundle and warns when a `*.example` config ships without an active counterpart (Zombie weapons.cfg bug class). |
| `AppCore._fix_metamod_dll_nesting()` | Repairs the `addons/metamod/bin/win64/win64/` extraction bug by `shutil.move`-ing the nested DLLs up a level (v0.9.2 switched from copy+rmtree to atomic-move). |
| `AppCore.save_config()` | **Atomic write**: lock-guarded, tmp + `os.replace` + `fsync`.  Replaces v0.9.1's bare open-truncate-write that could corrupt config under concurrent saves or power loss. |
| `AppCore._config_save_lock` | Serialises concurrent `save_config()` calls (Flask is threaded). |
| `AppCore._stop_event` | `threading.Event`; set by `stop_server()` to cancel the crash-restart backoff sleep.  Cleared at the top of every fresh `start_server()`.  v0.9.2.1 adds a re-check after `wait()` returns False to close the edge-window race. |

### Module-level constants & helpers

| Symbol | Summary |
|--------|---------|
| `_resolve_plugins_base()` | Locates the `plugins/` bundle directory for dev, `--onefile`, and `--onedir` PyInstaller layouts. |
| `_PLUGINS_BASE` | Absolute path to the plugin bundle root (set once at import time). |
| `_PLUGIN_KIND` | Dict mapping plugin key → `"metamod"` or `"css"` (determines restart vs. hot-reload). |
| `_MODE_PLUGIN_NAMES` | Dict mapping game mode → ordered list of plugin keys required for that mode. |
| `_PLUGIN_VERIFY_FILES` | Dict mapping plugin key → list of `csgo/`-relative paths that must exist after deploy. |
| `_PLUGIN_COPY_RULES` | Dict mapping plugin key → list of `(src_subdir, dst_subdir)` tuples describing directory copies. |
| `_PLUGIN_CLEANUP_ITEMS` | Dict mapping plugin key → list of `csgo/`-relative paths to delete on undeploy. |
| `_CSS_HOST_DLLS` | Frozenset of lowercased DLL names provided by the CSS host that must be skipped during plugin copy. |
| `_DL_TIMEOUT_SECS` | Hard timeout (seconds) for DepotDownloader subprocess calls. |

### `AppCore` class

#### Construction & config persistence

| Symbol | Summary |
|--------|---------|
| `AppCore.__init__()` | Initialises all state attributes (server dir, RCON, Steam, bot config, caches) and loads `oblivion_config.json` if present. |
| `AppCore.load_config()` | Reads `oblivion_config.json` and populates all settings (incl. `admin_pin`, optional `guest_pin`, `bots_enabled`, cmdfilter overrides); calls `update_paths()` and generates a random RCON password on first run. |
| `AppCore.save_config()` | Serialises current settings to `oblivion_config.json`; stores Steam password in OS keyring if available. |
| `AppCore.update_server_dir(path)` | Updates `server_dir` in memory and on disk, recomputes path constants. |

#### Logging

| Symbol | Summary |
|--------|---------|
| `AppCore.log(msg)` | Appends a timestamped message to the in-memory ring buffer and calls `on_log` callback if set. |
| `AppCore.get_log()` | Returns the full log buffer as a newline-joined string. |
| `AppCore.subscribe_log(callback)` | Registers a callback for new log lines and immediately replays the current buffer to it. |
| `AppCore.unsubscribe_log(callback)` | Removes a previously registered log callback. |

#### CS2 install & app self-update

| Symbol | Summary |
|--------|---------|
| `AppCore.install_server(on_done)` | Launches SteamCMD to install/update CS2 on a daemon thread; streams output to the log. |
| `AppCore.check_for_cs2_update(on_done)` | Compares the installed CS2 build ID against Steam's manifest to determine if an update is available. |
| `AppCore.check_app_update(on_done)` | Queries GitHub releases API and fires `on_app_update_checked` with the latest tag and URL. |

#### Server lifecycle

| Symbol | Summary |
|--------|---------|
| `AppCore.start_server(map_name, mode, gslt, is_workshop)` | Assembles the CS2 launch command line (adds `-condebug`, and `-disable_workshop_command_filtering` for flagged workshop maps), deploys plugins, spawns the process, starts the boot-state poller. |
| `AppCore._boot_poller(proc)` | Daemon thread that probes RCON every 2 s after launch; transitions `boot_state` to `"ready"` and fires `on_state_change`. |
| `AppCore.stop_server()` | Non-blocking: flips state under the lifecycle lock, then terminates on a daemon thread. |
| `AppCore.change_map(map_name, mode, is_workshop, caller)` | Changes map via RCON (`changelevel` / `host_workshop_map`); restarts via `_restart_into` when the mode change swaps plugins. |
| `AppCore.depotdl_download(wid)` | Staged workshop download: `<id>.partial` → per-MB `_dl_progress` → verify (vpk + size) → promote to the live workshop dir. |
| `AppCore.cmdfilter_effective / scan_cmdfilter / set_cmdfilter_override` | Per-map `-disable_workshop_command_filtering` detection (from Steam description), scan, and manual override. |

#### gameinfo.gi patching & infrastructure checks

| Symbol | Summary |
|--------|---------|
| `AppCore._csgo_dir()` | Returns the absolute path to the `csgo/` game directory inside the server install. |
| `AppCore._gameinfo_path()` | Returns the path to `gameinfo.gi` inside the server install. |
| `AppCore._gameinfo_has_metamod()` | Returns `True`/`False`/`None` — whether gameinfo already contains the MetaMod search path (or is unreadable). |
| `AppCore._patch_gameinfo()` | Inserts the MetaMod search path into `gameinfo.gi` so MetaMod loads at next server start. |
| `AppCore._unpatch_gameinfo()` | Removes the MetaMod search path from `gameinfo.gi` (used when undeploying all metamod plugins). |
| `AppCore._metamod_installed()` | Returns `True` if MetaMod's `vdf` file exists in the expected `csgo/addons/` location. |
| `AppCore._css_installed()` | Returns `True` if the CounterStrikeSharp core DLL exists in `csgo/addons/`. |

#### Plugin manifest & deploy helpers

| Symbol | Summary |
|--------|---------|
| `AppCore._load_plugin_manifest()` | Reads `oblivion_plugins.json` from disk; returns empty dict on missing/corrupt. |
| `AppCore._save_plugin_manifest(mode, plugins)` | Writes mode and plugin list to `oblivion_plugins.json` with a UTC timestamp. |
| `AppCore._verify_plugin_files(name)` | Returns a list of `csgo/`-relative paths from `_PLUGIN_VERIFY_FILES[name]` that are currently absent. |
| `AppCore._verify_deployment(plugins)` | Calls `_verify_plugin_files` for each plugin and logs pass/fail; returns `True` if all verified. |
| `AppCore._undeploy_plugins(plugin_names)` | Deletes all `_PLUGIN_CLEANUP_ITEMS` paths for the given plugins and unpatches gameinfo.gi when no metamod plugins remain. |

#### Plugin deploy (main pipeline)

| Symbol | Summary |
|--------|---------|
| `AppCore.deploy_plugins(mode)` | Full deploy pipeline: undeploy old plugins, copy new plugin files skipping CSS host DLLs (excluding K4-Arenas-Bots when bots off), patch gameinfo.gi, apply per-mode config writers, verify, then hot-reload CSS or log restart requirement. |
| `AppCore._apply_retakes_bots(csgo_dir)` | Honours the Use-bots toggle for Retakes by rewriting the deployed `retakes.cfg` (`bot_quota 0` + `bot_kick` when bots off). |
| `AppCore._apply_arena_size(csgo_dir, mode)` | Sets the K4-Arenas arena size: clears any generated config for `1v1` (plugin default = pure 1v1), or writes a `round-settings` config forcing `TeamSize 2` for `2v2`. |
| `AppCore._hot_reload_css()` | Sends `css_plugins reload` via RCON; detects "unknown command" and logs that a restart is needed instead. |
| `AppCore.deploy_plugins_async(mode, on_done)` | Non-blocking wrapper that runs `deploy_plugins()` on a daemon thread. |
| `AppCore.check_plugins()` | Logs a full infrastructure + per-plugin file presence diagnostic on a daemon thread. |

#### Workshop downloads & Steam

| Symbol | Summary |
|--------|---------|
| `AppCore.request_workshop_download(workshop_id, requester)` | Queues a workshop download request (shown in the panel for admin approval). |
| `AppCore.cancel_download()` | Terminates the active DepotDownloader subprocess. |
| `AppCore.steam_login_interactive()` | Opens steamcmd in a new console window for interactive 2FA/Steam Guard setup; monitors exit to mark session active. |
| `AppCore._ensure_depotdownloader()` | Downloads and extracts DepotDownloader from GitHub if not already present; returns `True` on success. |
| `AppCore.depotdl_download(workshop_id, on_done)` | Downloads a workshop map via DepotDownloader on a daemon thread; streams output to log; invalidates expired session tokens on failure. |

#### Public IP

| Symbol | Summary |
|--------|---------|
| `AppCore.check_public_ip()` | Fetches the machine's public IP from `ipify.org` on a background thread and fires `on_public_ip`. |

#### Player management

| Symbol | Summary |
|--------|---------|
| `AppCore._parse_players(status_output)` | Parses CS2 `status` RCON output into a list of player dicts (`userid`, `name`, `steamid`, `time`, `ping`). |
| `AppCore.get_players(callback)` | Runs RCON `status`, parses players, and delivers the list to `callback` on a daemon thread. |
| `AppCore.kick_player(userid, name)` | Sends `kickid <userid>` via RCON on a daemon thread. |
| `AppCore.ban_player(steamid, name, duration)` | Writes a `banid` entry to the ban file and fires RCON `banid` on a daemon thread. |
| `AppCore.unban_player(steamid)` | Removes a ban entry from the ban file and fires RCON `removeid` on a daemon thread. |
| `AppCore.get_ban_list(callback)` | Reads bans from `banned_user.cfg` on disk, falling back to RCON `listid`; delivers the list to `callback`. |

#### Server chat, friendly fire, round controls

| Symbol | Summary |
|--------|---------|
| `AppCore.server_say(msg)` | Sends a `say` RCON command on a daemon thread. |
| `AppCore.set_friendly_fire(enabled)` | Batches `mp_friendlyfire` + `mp_autokick` CVars into one RCON connection. |
| `AppCore.restart_round()` | Sends `mp_restartgame 1` via RCON. |
| `AppCore.end_warmup()` | Sends `mp_warmup_end` via RCON. |
| `AppCore.pause_match()` | Sends `mp_pause_match` via RCON. |
| `AppCore.unpause_match()` | Sends `mp_unpause_match` via RCON. |

#### Bot management

| Symbol | Summary |
|--------|---------|
| `AppCore._BOT_DIFF` | Class-level dict mapping difficulty name → CS2 `bot_difficulty` integer string. |
| `AppCore.add_bots(count)` | Batches `bot_difficulty` + N × `bot_add` into one RCON connection. |
| `AppCore.kick_bots()` | Sends `bot_kick` via RCON. |

#### Workshop metadata

| Symbol | Summary |
|--------|---------|
| `AppCore.fetch_workshop_names(ids, on_done)` | Calls the Steam `GetPublishedFileDetails` API to populate `_map_name_cache`, `_map_tag_cache`, and `_preview_url_cache`. |

#### Properties

| Symbol | Summary |
|--------|---------|
| `AppCore.is_installed` | `True` when `cs2.exe` exists at the configured server directory. |
| `AppCore.needs_setup` | `True` when no server directory is set or the admin PIN is still `"1234"`. |
| `AppCore.uptime_seconds` | Seconds since RCON became ready; `0` if offline or booting. |

#### Crash monitor & RCON passthrough

| Symbol | Summary |
|--------|---------|
| `AppCore.start_monitor()` | Starts two daemon threads: `_watch` (crash detector via `proc.poll()` + periodic `tasklist` fallback, with optional auto-restart up to 3 times) and `_player_count_loop` (polls RCON `status` every 15 s to update `player_count`). |
| `AppCore.rcon_execute(command, callback)` | Executes an arbitrary RCON command on a daemon thread and delivers `(response, error)` to `callback`. |
