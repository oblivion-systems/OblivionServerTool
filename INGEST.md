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

## `cs2servergui/_netutils.py` (98 lines)

| Symbol | Summary |
|--------|---------|
| `_default_log(msg)` | Fallback logger (`print`) for callers that don't provide one — used by `main.py`'s startup path before `AppCore.log` exists. |
| `port_in_use(port, host, timeout)` | Returns `True` if something is already listening on `host:port` (TCP connect probe). |
| `listeners_on_port(port, log)` | Returns every `(bound_address, pid, image_name_lower)` tuple listening on `port` — walks `netstat -ano` and resolves PIDs via `tasklist /FI`. Multiple entries returned when the port is bound to multiple addresses (IPv4 + IPv6, or explicit 0.0.0.0 + ::). |
| `holder_of_port(port, log)` | Thin wrapper over `listeners_on_port` that returns the first listener as `(pid, name)` or `None`. |

---

## `cs2servergui/config.py` (264 lines)

### Functions

| Symbol | Summary |
|--------|---------|
| `_lan_ip()` | Returns the machine's primary LAN IP by routing a UDP socket toward 8.8.8.8 without sending data; falls back to `127.0.0.1`. |
| `update_paths(server_dir)` | Recomputes all path globals (`CS2_SERVER_DIR`, `STEAMCMD_PATH`, `CS2_PATH`, `WORKSHOP_DIR`, `DEPOTDL_PATH`, `CS2_ADDONS_DIR`) from a new base directory. |
| `load_workshop()` | Returns a sorted list of downloaded workshop map IDs (folder names) from `WORKSHOP_DIR`. |

### Constants

| Symbol | Summary |
|--------|---------|
| `CS2_APP_ID` | Steam App ID for CS2 (`"730"`). |
| `DEPOTDL_RELEASE_URL` | GitHub API URL for the latest DepotDownloader release. |
| `APP_VERSION` | Current application version string (e.g. `"0.9.1"`). |
| `APP_REPO` | GitHub repository slug for the app (`"jacquesvniekerk-eng/OblivionServerTool"`). |
| `APP_RELEASES_URL` / `APP_API_URL` | Human and API GitHub release URLs derived from `APP_REPO`. |
| `CS2_SERVER_DIR` / `STEAMCMD_PATH` / `CS2_PATH` / `WORKSHOP_DIR` / `DEPOTDL_PATH` / `CS2_ADDONS_DIR` | Default path constants; all re-set by `update_paths()`. |
| `RCON_HOST` / `RCON_PORT` / `RCON_PASSWORD` / `FLASK_PORT` / `ADMIN_PIN` | Network/auth constants; `RCON_HOST` is set at module load from `_lan_ip()`. |
| `_CONFIG_FILE` | Absolute path to `oblivion_config.json`; under `%APPDATA%\Oblivion Server Tool\` when frozen, project root when dev. |
| `OFFICIAL_MAPS` | List of 10 official CS2 competitive maps. |
| `CS2_PANORAMA_THUMBS_SUBPATH` | Relative path from a CS2 install root to the 1080p map screenshot folder. |
| `GAME_MODES` | Ordered list of all 16 supported game mode strings. |
| `MODE_SETTINGS` | Maps mode name → `{game_type, game_mode, maxplayers}` — defines the CS2 ruleset per mode. |
| `MODE_MAPS` | Maps mode name → allowed official map list (or `None` if workshop-only). |
| `MODE_WORKSHOP_SEARCH` | Maps mode name → Steam Workshop search term string. |
| `MODE_WORKSHOP_TAGS` | Maps mode name → list of Steam Workshop tag strings used for filtering downloaded maps. |

---

## `cs2servergui/rcon.py` (134 lines)

| Symbol | Summary |
|--------|---------|
| `RCONClient` | Thread-safe RCON client that opens a fresh TCP connection for every command. |
| `RCONClient.__init__(host, port, password)` | Stores connection params and initialises an `itertools.count` for packet IDs. |
| `RCONClient._next_id()` | Returns the next auto-incremented packet ID (thread-safe under the GIL). |
| `RCONClient._pack(id, type_, body)` | Packs a Source RCON packet (little-endian size + id + type + body + two null bytes). |
| `RCONClient._recv(sock)` | Reads one complete RCON response packet from `sock`, handling partial reads. |
| `RCONClient.execute(command)` | Sends a single RCON command on a fresh TCP connection (auth then exec) and returns the response string. |
| `RCONClient.execute_many(commands)` | Sends multiple commands over a single authenticated connection, returning responses joined with newlines. |
| `RCONClient.execute_retry(command, retries, delay)` | Calls `execute()` up to `retries` times with `delay`-second back-off; re-raises on final failure. |

---

## `cs2servergui/web.py` (958 lines)

### Session store & lockout (module-level)

| Symbol | Summary |
|--------|---------|
| `_create_session(ip, is_local, role)` | Mints a random session token storing `ip`, `is_local`, `role` (`"admin"`/`"guest"`), and `created_at`. |
| `_get_session(token)` / `_clear_session(token)` | Look up a session (expiring remote ones past the 8 h TTL) / drop a session. |
| `_check_lockout(ip)` / `_record_fail(ip)` / `_clear_attempts(ip)` | Per-IP PIN brute-force lockout: 5 fails → 300 s. |
| `_check_global_lockout()` / `_record_global_fail()` / `_clear_global()` | Global backoff (20 fails → 300 s, decays after 600 s quiet) defending against distributed brute force. |

### Factory & auth (inner to `create_flask`)

| Symbol | Summary |
|--------|---------|
| `create_flask(core)` | Factory that builds and returns the complete Flask app wired to the given `AppCore`. |
| `_current_session()` | Returns the session dict for the request's `session` cookie, or `None`. |
| `require_auth(f)` | Decorator: 401 unless a valid session exists; binds remote sessions to their origin IP. |
| `require_local(f)` | Decorator (stacks on `require_auth`): 403 unless the session `is_local` — keeps RCON/install/Steam strictly on the desktop window. |
| `_role_gate()` | `@app.before_request` **fail-closed** role enforcer: only an explicit guest/public allowlist is reachable by the `guest` role; every other `/api/*` route is admin-only by default. |

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
| `GET /api/state` | Full state snapshot (running, boot_state, map, mode, players, IPs, update flags, `role`, `guest_pin_set`, dl_progress). **(guest-allowed)** |
| `POST /api/server/start` | Starts the server with map/mode/workshop flag. *(admin)* |
| `POST /api/server/stop` | Stops the running server. *(admin)* |
| `POST /api/server/map` | Changes map + game mode (RCON or `_restart_into` on plugin swap). **(guest-allowed)** |
| `POST /api/server/broadcast` | RCON `say` chat broadcast. *(admin)* |
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
| `POST /api/workshop/download` | DepotDownloader staged download. **(guest-allowed)** |
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
| `GET /api/setup/status` | First-run wizard state. **(guest-allowed)** |
| `POST /api/setup/complete` | Persist first-run setup. *(local-only)* |
| `GET /api/log/history` / `GET /api/log/stream` | Log history / live SSE stream. *(admin)* |

---

## `cs2servergui/core.py` (3619 lines)

> **Drift note (2026-05-30):** the file grew by ~470 lines since this section
> was first generated. Notable additions in the v0.9.2 candidate that aren't
> yet enumerated below:
>   - `AppCore._resolve_rcon_host()` — refreshes `_config.RCON_HOST` and
>     `self.rcon.host` from the live LAN IP on every server start / attach.
>   - `AppCore._preflight_checks(map, mode, is_workshop)` — runs before
>     `deploy_plugins()`; blocks Start on missing CS2, foreign port-27015
>     holder, or missing plugin bundle folders.
>   - `AppCore._post_launch_sanity_check()` — background thread that catches
>     immediate `cs2.exe` death AND enumerates `netstat` listeners on 27015
>     to switch `self.rcon.host` to the actual bind address, then force-fires
>     the workshop trigger.
>   - `AppCore._list_dedicated_pids()` — PowerShell `Get-CimInstance` first,
>     `wmic` fallback (deprecated/removed on Win 11 24H2).
>   - `AppCore._holder_of_port` / `._listeners_on_port` — thin wrappers over
>     `cs2servergui._netutils` so the AppCore logger picks up netstat output.
>   - `AppCore._validate_bundle_configs(deployed, csgo_dir)` — warns when a
>     plugin ships `*.example` without an active counterpart (Zombie
>     weapons.cfg bug class).
>   - Lock additions: `_config_save_lock` (atomic `save_config`), `_stop_event`
>     (cancellable crash-restart backoff).
> 
> Existing entries below remain accurate for the unchanged majority.

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
