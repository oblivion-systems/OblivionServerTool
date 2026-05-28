# INGEST — Structural Index

A complete structural index of every class and function across the source tree,
each with a one-sentence summary. No code, no behavioural changes — reference only.

---

## `main.py` (218 lines)

| Symbol | Summary |
|--------|---------|
| `_kill_zombie_instances()` | Finds and terminates any other `cs2servergui` processes already running so only one instance runs at a time. |
| `_start_flask(core)` | Builds the Flask app, starts it on `0.0.0.0:5000` in a daemon thread, returns the app object. |
| `_open_browser(url)` | Opens the default browser to `url` if pywebview is not available (headless/remote mode). |
| `_main()` | Top-level bootstrap: loads config, kills zombies, launches Flask, starts pywebview window (or browser), blocks until window closes, calls `os._exit(0)`. |
| `if __name__ == "__main__":` | Calls `_main()`. |

---

## `cs2servergui/__init__.py` (1 line)

| Symbol | Summary |
|--------|---------|
| *(module)* | Package marker — single comment line, no exports. |

---

## `cs2servergui/config.py` (242 lines)

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
| `GAME_MODES` | Ordered list of all 14 supported game mode strings. |
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

## `cs2servergui/web.py` (788 lines)

### Factory

| Symbol | Summary |
|--------|---------|
| `create_flask(core)` | Factory that builds and returns the complete Flask application wired to the given `AppCore` instance. |

### Auth helpers (inner)

| Symbol | Summary |
|--------|---------|
| `_is_local(request)` | Returns `True` when the request comes from localhost (127.0.0.1 or ::1). |
| `_require_auth()` | Before-request hook that enforces PIN login; redirects unauthenticated non-local requests to `/login`. |

### Auth routes

| Route | Summary |
|-------|---------|
| `GET /login` | Renders PIN entry page. |
| `POST /login` | Validates PIN, sets session cookie; enforces 5-attempt / 300s lockout per IP. |
| `GET /auth/auto` | One-time token endpoint used by pywebview to auto-login the desktop window without showing a PIN prompt. |
| `GET /logout` | Clears session and redirects to `/login`. |

### Page routes

| Route | Summary |
|-------|---------|
| `GET /` | Renders main SPA shell (`index.html`). |

### API — server control

| Route | Summary |
|-------|---------|
| `POST /api/start` | Starts the CS2 server with provided map, mode, GSLT, and workshop-map flag. |
| `POST /api/stop` | Stops the running CS2 server. |
| `POST /api/change_map` | Changes map on the running server (RCON `changelevel` or workshop map). |
| `POST /api/deploy_plugins` | Deploys plugin files for the given mode asynchronously. |
| `POST /api/check_plugins` | Runs the plugin diagnostic on a background thread. |
| `GET /api/state` | Returns full server state snapshot as JSON (running, map, mode, players, RCON status, etc.). |
| `GET /api/log/stream` | SSE endpoint streaming live log lines to the browser. |

### API — install / update

| Route | Summary |
|-------|---------|
| `POST /api/install` | Launches SteamCMD CS2 install/update on a background thread. |
| `POST /api/check_update` | Triggers a CS2 update check (compares installed vs. Steam manifest build IDs). |
| `POST /api/check_app_update` | Checks GitHub releases for a newer app version. |

### API — settings

| Route | Summary |
|-------|---------|
| `GET /api/settings` | Returns all user-configurable settings as JSON (server dir, RCON, Steam, bots, etc.). |
| `POST /api/settings` | Validates and saves updated settings to `oblivion_config.json`. |
| `POST /api/change_password` | Changes the admin PIN (validates current PIN first). |

### API — game controls

| Route | Summary |
|-------|---------|
| `POST /api/restart_round` | Sends `mp_restartgame 1` via RCON. |
| `POST /api/end_warmup` | Sends `mp_warmup_end` via RCON. |
| `POST /api/pause_match` | Sends `mp_pause_match` via RCON. |
| `POST /api/unpause_match` | Sends `mp_unpause_match` via RCON. |
| `POST /api/friendly_fire` | Toggles `mp_friendlyfire` and `mp_autokick` via RCON. |
| `POST /api/server_say` | Broadcasts a message to server chat via RCON `say`. |
| `POST /api/rcon` | Executes an arbitrary RCON command and returns the response (remote sessions see credentials masked). |

### API — players

| Route | Summary |
|-------|---------|
| `GET /api/players` | Returns current player list parsed from RCON `status`. |
| `POST /api/kick` | Kicks a player by userid. |
| `POST /api/ban` | Bans a player by SteamID and optionally duration. |
| `GET /api/bans` | Returns the current ban list from disk or RCON. |
| `POST /api/unban` | Removes a ban by SteamID. |
| `POST /api/add_bot` / `POST /api/kick_bots` | Adds one bot or kicks all bots via RCON. |

### API — workshop

| Route | Summary |
|-------|---------|
| `GET /api/workshop` | Returns list of downloaded workshop map IDs with cached names/tags. |
| `POST /api/workshop/download` | Queues a workshop map download request. |
| `GET /api/workshop/pending` | Returns pending download requests awaiting approval. |
| `POST /api/workshop/approve` | Approves and starts a pending DepotDownloader download. |
| `POST /api/workshop/cancel` | Cancels the active download. |
| `GET /api/workshop/status` | Returns current download state (idle/downloading/done). |

### API — maps

| Route | Summary |
|-------|---------|
| `GET /api/maps` | Returns mode-specific official and workshop map lists with metadata. |
| `GET /api/maps/thumb/<map_name>` | Proxies map thumbnail from the local CS2 panorama folder; returns 404 if absent. |

### API — Steam

| Route | Summary |
|-------|---------|
| `POST /api/steam/login` | Launches interactive steamcmd console for 2FA setup. |
| `GET /api/steam/session` | Returns current Steam session state (active/inactive). |

### API — public IP

| Route | Summary |
|-------|---------|
| `GET /api/public_ip` | Returns cached public IP or triggers a fresh `ipify.org` lookup. |

---

## `cs2servergui/core.py` (2583 lines)

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
| `AppCore.load_config()` | Reads `oblivion_config.json` and populates all settings; calls `update_paths()` and generates a random RCON password on first run. |
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
| `AppCore.deploy_plugins(mode)` | Full deploy pipeline: undeploy old plugins, copy new plugin files skipping CSS host DLLs, patch gameinfo.gi, verify, then hot-reload CSS or log restart requirement. |
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
