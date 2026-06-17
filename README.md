# Oblivion Server Tool

A desktop application for managing a **Counter-Strike 2 dedicated server** on Windows.  
Built with Python + Flask + pywebview (Edge WebView2). Ships as a single `.exe` with an optional installer.

> **Status: v0.16.13 (released 2026-06-17).**  v1.0 first-run UX audit
> (task #157) shipped — v0.16.5 → v0.16.13 closes the heaviest fresh-install
> friction points: one-click MetaMod + CSS runtime install (no more "find
> the addons/ folder and extract it yourself"), Edge WebView2 bundled into
> the installer (Win10 friends no longer get a blank window), a Getting
> Started card on the Status page that walks a brand-new operator through
> the three things they need to do before their first tournament, a
> one-click Discord setup check, actionable "→ Fix" buttons on every
> Pre-flight row, and a 5-bug self-review pass on the same stream (1
> critical: missing `_patch_gameinfo` call that would have silently broken
> MetaMod for fresh installs).  v0.16.13 also lands 4 main-thread safety
> fixes in the Warcraft plugin source — caught by an adversarial re-audit
> of the C# code, rebuilt + bundled.  The
> [`OblivionPluginRegistry`](https://github.com/jacquesvniekerk-eng/OblivionPluginRegistry)
> repo is live and every running .exe fetches its `catalog.json` on a
> 24h TTL.  **294/294 backend tests green.**
>
> **What's new since v0.11.0**:
> - **Warcraft plugin: 4 main-thread safety fixes** *(v0.16.13)* —
>   source-side adversarial audit of the patched WarcraftPlugin against
>   CSS v1.0.369 caught a Critical chat-command Dictionary corruption
>   (Friday lobby smashing `!skills`/`!class`/`!shop` would have rehashed
>   mid-read → silent freeze), `ResetClients` blocking the main thread,
>   `NativeAPI.GetEntityFromIndex` racing on a worker thread during
>   tournament fill, and a dormant MySQL block in the menu manager.
>   Source patched, DLL rebuilt, bundled.
> - **Demolition map list** *(v0.16.11 → v0.16.12)* — CS:GO's mini-maps
>   (`de_bank`, `de_lake`, etc.) were dropped from CS2's official rotation;
>   the app picked `de_bank` as Demolition default, cs2.exe returned
>   "invalid map name", server never bound 27015.  Now: workshop ports
>   of the CS:GO classics first (preserves the small-map design intent for
>   operators who've subscribed), CS2 official maps as fallback.
> - **Button contrast pass** *(v0.16.9 → v0.16.10)* — pack/template Apply
>   buttons now use accent-filled CTAs instead of outline buttons that
>   read as "greyed out" to a friend trying the app cold; `.btn`/`.btn-ghost`
>   default state brightened, `:disabled` darkened + grayscale-filtered
>   to widen the visual gap.
> - **5 review fixes** *(v0.16.8)* — self-review pass on v0.16.5–v0.16.7
>   caught 1 Critical (`_gameinfo_patch_metamod` didn't exist; the
>   AttributeError was swallowed and the response still said `ok:true` —
>   friend would have installed MetaMod, seen the green pill, started the
>   server, and watched MetaMod silently fail to load), 1 High
>   (build.bat didn't actually fetch the WebView2 bootstrapper), 2 Medium,
>   1 Low.  All five fixed, plus the response now surfaces warnings.
> - **First-run UX polish** *(v0.16.6)* — Status page gains a
>   "🚀 Getting started — N of 3 done" card with action buttons that
>   navigate to each step (install CS2 → install runtime → pick a
>   pack); auto-hides when all green.  Config → Discord card gains a
>   primary **🩺 Run Discord setup check** button (full embed-lifecycle
>   smoke test in one click); per-feature tests moved to an "Advanced"
>   expander.  Every Pre-flight row gets a per-key "→ Fix" button that
>   routes to the right tab.
> - **Auto-install MetaMod + CSS runtime** *(v0.16.5)* — Plugin Runtime
>   modal swaps manual zip-extraction instructions for "📥 Install"
>   buttons that download, verify, extract, and patch gameinfo.gi in
>   one click each.  Reuses the registry's safe-download primitives
>   (HTTPS, size cap, Zip Slip, atomic staging).  Manual fallback kept
>   under a `<details>` expander if the auto-download ever fails.
> - **Edge WebView2 bundled into installer** *(v0.16.5)* — `installer.iss`
>   conditionally bundles the ~2 MB Microsoft bootstrapper via
>   `#if FileExists`.  Friends on Windows 10 (no preinstalled WebView2)
>   no longer get a blank window on first launch.  Pre-build helper at
>   `tools/fetch_webview2.ps1` fetches it from Microsoft's Evergreen URL.
> - **RCON binds to all interfaces** *(v0.16.4 hotfix)* — adds
>   `+ip 0.0.0.0` to the cs2.exe launch args so RCON's TCP socket
>   doesn't get trapped on the Hyper-V vEthernet adapter on hosts with
>   WSL / Hyper-V installed.  Fixes the silent "connection refused"
>   loop that affected operators with a virtual NIC.
> - **Wave 4 — tournament templates + demo browser + Discord mock-veto**
>   *(v0.16.3)* — Plugins tab gains a **Templates strip**: save a complete
>   recurring config (mode + map + pack + Discord channels + team IDs)
>   under a name, restore it with one click; allowlist-filtered persistence
>   so the JSON is safe to share.  History page gains a **Demos card**
>   that walks `csgo/` + MatchZy/CSS demo dirs + MatchZy cfg, surfacing
>   every `.dem` with size + date + one-click download (three-layer path
>   safety: label allowlist + `.dem`-only + `commonpath` realpath check).
>   Discord Config card gains a **🧪 Mock-veto smoke test** button —
>   posts a real embed, simulates the full Ban → Pick → Side → Move flow
>   with reaction payloads, leaves a "smoke test complete" embed (safe to
>   delete) so operators can verify Discord setup without spinning up a
>   real veto.
> - **Wave 3 — History + Pre-flight + Logs pages** *(v0.16.2)* — three
>   new dedicated sidebar entries.  **Pre-flight** runs 10 audited
>   readiness checks (config, plugins, Discord, server install, PIN
>   security) and emits ok/warn/fail/info with a one-line explanation —
>   catches "admin_pin=1234" (default), "Discord bot token missing",
>   "deploy never run", "active pack files missing".  **History** lifts
>   match history out of the modal into a real page with search.
>   **Logs** gives source-filtered, searchable, auto-refreshing access
>   to backend logs + a download button.
> - **Wave 2 — persistent team profiles** *(v0.16.1)* — Veto roster
>   stage gains a **📋 Team Profiles** modal.  Save the current 5
>   players as a named team (with tag + optional Discord IDs) for
>   recurring tournaments; restore a saved team into either roster slot
>   with one click.  Stored in `oblivion_teams.json` with atomic writes
>   + stable UUIDs across edits.
> - **Wave 1 — config backup/restore + wizard polish + PIN docs**
>   *(v0.16.0)* — `core.backup_config(reason)` writes timestamped
>   snapshots of `oblivion_config.json` to `%APPDATA%/.../backups/`,
>   keeps the last 10, and auto-fires before risky plugin / pack
>   actions; SPA gets `📥 Backup` / `📂 Restore` buttons in Config →
>   Tools row.  First-run wizard Step 3 rewritten with a 3-step ordered
>   guidance ("Deploy → Connect → First match").  TROUBLESHOOTING.md
>   gains a **Security: PIN auth + remote exposure** section with a
>   what-is/isn't-protected threat table.
> - **Plugin Manager slice 3** *(v0.15.2)* — Uninstall + Reload + custom
>   URL install + Update notifications + search filter.  Local-source
>   library cards get a **Remove** button.  Plugins drop into
>   `%APPDATA%/.../plugins/` and **↻ Reload** picks them up without an
>   app restart.  Updated plugins flash an orange **Update v…** pill.
>   See the [v0.15.2 release notes](https://github.com/jacquesvniekerk-eng/OblivionServerTool/releases/tag/v0.15.2).
> - **Community plugin registry** *(v0.15.1)* —
>   [`OblivionPluginRegistry`](https://github.com/jacquesvniekerk-eng/OblivionPluginRegistry)
>   fetched via raw URL; new SPA section **"Available to Install (Community)"**;
>   one-click install with SHA-256 verification + Zip-Slip protection +
>   atomic tempdir-then-move.  HTTPS-only, 50 MB cap, 12s timeout.
> - **Self-describing plugins** *(v0.15.0)* — every plugin folder ships
>   a `plugin.json` manifest (`kind`, `modes`, `load_order`, `copy_rules`,
>   `verify_files`, `cleanup`).  The five hardcoded plugin tables in
>   `core.py` are now derived from the manifests at module load.
>   Discovery scans bundled (`cs2servergui/plugins/`) AND local
>   (`%APPDATA%/.../plugins/`).  See
>   [PLUGINS.md](PLUGINS.md) for the plugin-author schema.
> - **Config tab restructure** *(v0.14.2)* — single-column layout with six
>   sections in operator-mental-model order: **Setup → Security → Server
>   (+ Bots) → Match Flow → Discord → Tools row**.  Strong section
>   separators (accent bar + 2px top border).  Discord webhook moved
>   into the Discord card.  Bots folded into Server.  Whole-app button
>   hover polish (accent-tinted border + soft glow on non-purple buttons).
> - **Live mode swap on running server** *(v0.14.1)* — Plugin tab actions
>   no longer 409 when the server is running.  They route through
>   `change_map`'s stop-deploy-restart cycle.  Banner warns instead of
>   disabling; confirm prompts mention STOP + RESTART; toasts say
>   "Restarting into X — watch Status tab".
> - **Plugin Manager: packs + runtime bootstrap** *(v0.14.0)* — five
>   Quick-Apply Packs (Competitive 5v5 / Warcraft Night / Casual DM /
>   Retakes / Vanilla Competitive); JSON catalog file; **🔧 Set up plugin
>   runtime** modal with direct sourcemm.net + CSS GitHub links when
>   MetaMod or CSS is missing.  Plus 4 audit fixes (csgo_dir leak gated,
>   XSS surface escaped, catalog load errors loud, inline fallback dict
>   dropped).
> - **Plugins tab (read-only) + Activate/Vanilla** *(v0.13.2)* — new
>   admin-only **Plugins** entry in the sidebar between Maps and Veto.
>   Server Readiness card + Currently Deployed card + Plugin Library grid
>   with Activate buttons (single-mode auto-pick or multi-mode dropdown)
>   + Switch-to-vanilla button.
> - **PLATFORM.md + worked-example migration** *(v0.13.1)* — the design
>   doc + the first concrete method extraction (`install_root()`).
>   AppCore's `_csgo_dir()` is now a thin shim that delegates to the
>   driver.  Every subsequent v0.13.x migration follows this template.
> - **Driver abstraction seam** *(v0.13.0)* — `cs2servergui/drivers/`
>   package with `GameDriver` ABC + `CS2Driver` subclass.  Diagnostic
>   snapshot now has a "Driver" section showing game name, port,
>   process image, plugin layer.  v0.13.x will migrate `core.py`
>   methods into the driver one seam at a time; v0.13.x.y adds the
>   TF2 driver as the proof point.
> - **Gaming Mode toggle + scripts/ bundling** *(v0.12.5)* — new SPA
>   section under Config: ⚡ ON / 💤 OFF / 📊 Status buttons wrap
>   `scripts/gaming-mode.ps1` (Power Plan + cs2.exe core affinity).
>   Installer now ships `scripts/` alongside the .exe.
> - **Content-hashed `/static/*` URLs** *(v0.12.4)* — replace v0.11.24's
>   blanket `no-store` with `?v=APP_VERSION` query strings +
>   `Cache-Control: immutable`.  Cache-bust on rebuild AND aggressive
>   caching between rebuilds.  Closes the last audit finding (#6).
> - **Remote player voting** *(v0.12.3)* — bot DMs each rostered
>   player a one-shot URL after Distribute.  Player taps → minimal
>   voting page shows their team's 5 names → one click casts.  No
>   more operator-walks-around-the-room voice-chat vote collection.
> - **SSE broadcast observability** *(v0.12.2)* — diagnostic snapshot
>   gains a new "SSE broadcast telemetry" section.  Drops counter +
>   TL;DR `⚠ sse` indicator when overflow happens.  Closes audit
>   finding #10 (investigation pass — see CHANGELOG).
> - **Round summaries + slash commands** *(v0.12.1)* — background
>   RCON-poll daemon detects score deltas every 3 s and posts a
>   per-round embed to the veto channel during a live match.  Bot
>   now has a slash-command tree: `/round-summaries on|off|status`
>   and `/move-teams now / auto on|off / status`.  Per-guild
>   sync = immediate propagation.
> - **`/move-teams` + auto-move on Distribute** *(v0.12.0)* — bot
>   moves rostered players with `discord_id` from the lobby VC into
>   their team's configured VC.  Three triggers: SPA button on the
>   Teams stage, persistent toggle in Discord config, or auto-fire
>   after `/api/veto/distribute` when toggle ON.  Default OFF.
>   Bot needs **Move Members** perm.
> - **`_vetoApply` consolidation** *(v0.11.27)* — single helper now
>   owns ALL snapshot ingestion (mutation responses, SSE, initial
>   fetch, polling).  Adds monotonicity guard + idle short-circuit.
>   Closes audit findings #5/#7/#8/#9.
> - **Audit cleanup** *(v0.11.26)* — zombie captain race fix, captain
>   interstitial Cache-Control, poll timer leak fix, board click
>   double-render fix.  Code-review skill ran high-effort against the
>   v0.11.20-25 diff; 4 of 10 findings shipped here, 4 more in
>   v0.11.27, 2 remain for the v0.12 driver-abstraction work.
> - **Tournament-night hotfix chain** *(v0.11.20-25)* — captain link
>   in Discord webview (SameSite=Lax + HTML interstitial), captain
>   session sweep on reset, mutation response stuffed into local state
>   bypassing SSE race, no-cache on `/static/*` for WebView2, 3s
>   polling fallback alongside SSE.
> - **Snapshot plugin log diagnostics** *(v0.11.19)* — CSS + MatchZy
>   log tail with anomaly prefixing fills the visibility gap
>   left when MatchZy redirects CS2's console.log writes.  Plus a
>   TL;DR `plugin_log` health indicator.
> - **🔍 Browse for Veto Embed Channel ID** *(v0.11.18)* — text-channel
>   picker in the Discord Config card, mirroring the v0.11.15
>   default-VC Browse.  No more Developer-Mode-right-click-Copy-ID.
> - **Friday-eve thorough sweep** *(v0.11.17)* — Tier A (duplicate
>   SteamID rejection, rematch embed clear, secure cookie on tunnel,
>   mobile flex-wrap, drop privileged intent, server-start during dl,
>   bot recovery via re-save) + Tier B (live-embed coalescing,
>   tightened resume window, finale double-fire guard, captain board
>   click race, captain Ready closure fix).  +10 backend tests.
> - **v0.11.15 adversarial-review hotfixes** *(v0.11.16)* — double-click
>   guard on the 🎤 button, mobile-safe 🔀 Pick channel button, silent
>   fallback when default VC is unreachable, snapshot triage timeout
>   3s→1.5s.  Self-review caught three ship-risks; all fixed.
> - **Default voice channel for one-click roster pull** *(v0.11.15)* —
>   configure your tournament's regular VC once → Veto roster's "🎤 Pull
>   from voice channel" becomes one click instead of pick-from-modal.
>   Picker still available as fallback (shift+click) and for first-run
>   guilds.  Diagnostic snapshot now shows "default VC: #foo (8
>   connected)" so triage is one snapshot, not a live check.
> - **Host + Play perf scripts** *(v0.11.14)* — `scripts/gaming-mode.ps1`
>   + desktop shortcuts solve the "alt-tab lag spike when I host AND
>   play CS2 on the same PC" problem.  Pins server / client to separate
>   CPU cores so Windows can't reshuffle on foreground change.  See
>   [scripts/README.md](scripts/README.md) and
>   [scripts/PROCESS_LASSO_SETUP.md](scripts/PROCESS_LASSO_SETUP.md).
> - **CS2 console.log freshness + frame-drop detection** *(v0.11.13)* —
>   diag snapshot now shows "2.6 days ago — NOT current session" when
>   log is stale, counts `UNEXPECTED LONG FRAME` warnings.
> - **Plugin-verifier fix** *(v0.11.12)* — stale-manifest "MISSING" false
>   positive when current mode ≠ last deploy mode.
> - **Two diag-snapshot bugs from real Way-3 paste** *(v0.11.11)* —
>   TL;DR disk "could not check" and Discord "connected as ?" both fixed.
> - **Diagnostic snapshot triage optimization** *(v0.11.10)* — TL;DR
>   auto-scan block at top, anomaly `>` prefix on log lines, empty-
>   section collapse.  2-second triage from clipboard paste.
> - **Diagnostic snapshot — fill the gaps** *(v0.11.9)* — added CS2
>   console.log tail, plugin file verification, disk free space, request
>   User-Agent, active session raw JSON dump (tokens redacted).
> - **Mode dropdown category tinting** *(v0.11.8)* — Vanilla CS2 vs
>   Plugin-enhanced split with `· pluginName` suffix + `(restart on
>   switch)` hint for MetaMod modes.
> - **Map dropdown category tinting** *(v0.11.7)* — Official / Workshop
>   Recommended / Workshop Other split with accent tint per category.
> - **Status-bar version pill** *(v0.11.6)* — `v0.11.19` always visible
>   bottom-right; no more "what version am I running?"
> - **Version on /api/ping** *(v0.11.5)* — unauthenticated `curl
>   localhost:5050/api/ping` returns `{version, build}` for fast remote
>   verification.
> - **Diagnostic snapshot + TROUBLESHOOTING.md** *(v0.11.4)* — one
>   button in Config → Troubleshooting → clipboard text blob with app
>   state, veto state, log, config (secrets masked).  Friday-night
>   support shortcut.
> - **Active session persistence** *(v0.11.3)* — accidental Ctrl+Q,
>   Windows update, or app crash mid-session no longer evaporates the
>   in-progress veto.  Captain claims, partial ban/pick, ready flags
>   all survive restart.  Atomic write to `oblivion_veto_active.json`;
>   12 h cutoff.
> - **`issue_tokens` idempotency fix** *(v0.11.2)* — captain browser
>   refresh no longer silently invalidates the other captain's link.
> - **v0.11.1 polish sweep**: Discord Test Embed / Test DM buttons,
>   📜 Match history modal, 🌐 "Go Online" banner, bulk paste with
>   SteamID + Discord ID columns, roster presets (localStorage),
>   MatchZy cvar editor (local), 📺 Spectator URL (read-only
>   `/spectate` page, OBS-friendly, PII stripped, XSS-defended),
>   [MOBILE_CHECK.md](MOBILE_CHECK.md) real-device checklist.
> - **v0.11.0 (Discord bot Layer 1)**: operator runs their own bot
>   bound to their own server
>   ([5-min setup in DISCORD.md](DISCORD.md)).  Auto-DM captain links,
>   voice-channel roster pull, live veto embed.  Degrades silently
>   when no token is configured.
>
> **294/294 backend tests green** across the veto state machine, the
> API surface, plugin manifest/registry/install paths, team-profile +
> template CRUD, readiness audits, and demo / mock-veto edge cases.
> Full per-release prose in [CHANGELOG.md](CHANGELOG.md); spec for the
> map-veto feature in [VETO.md](VETO.md); plugin-author guide in
> [PLUGINS.md](PLUGINS.md); driver-abstraction design doc in
> [PLATFORM.md](PLATFORM.md); strategic roadmap to v1.0 in
> [PLAN.md](PLAN.md); operator-facing runbook in
> [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## What it does

Running a CS2 dedicated server normally means juggling command-line arguments, steamcmd windows, and RCON clients. Oblivion Server Tool puts everything in one place — start the server, manage players, change maps, download workshop content, and administer remotely from your phone, all from a single window.

The desktop app and the remote web panel are the same interface: a Flask SPA rendered in a local pywebview window (Edge WebView2). Any device on your LAN can open the same panel in a browser with PIN authentication.

---

## Features

### Server Control
- **Start / Stop / Quick Restart** with one click
- **Change map and game mode** live without restarting
- Animated status indicator — Offline / Booting / Online
- Server **uptime counter** in the status bar
- Crash detection — automatically marks the server offline if the process dies unexpectedly
- **Auto-start** option: server launches automatically when the tool opens

### Map & Mode Selection
- Pick from all **official CS2 maps**, filtered per game mode
- **16 game modes**: Competitive, Casual, Wingman, 3v3, 4v4, 5v5, 1v1, 2v2, Arms Race, Demolition, Deathmatch, Retakes, Jailbreak, Practice, Warcraft, Zombie Escape
- **Workshop map picker** — shows downloaded maps by real name, not just ID
- **Map search** — filter official and workshop maps by name or ID in real time
- **Browse Steam Workshop** button, pre-filtered by the currently selected game mode

### Mode-Specific Plugin Deployment
Oblivion automatically deploys the correct CounterStrikeSharp / MetaMod plugins for each mode and cleans them up when switching:

| Mode | Plugins deployed |
|---|---|
| Retakes | B3none cs2-retakes + RetakesAllocator |
| Practice / 3v3 / 4v4 / 5v5 | MatchZy (3v3/4v4/5v5 = team matches capped at maxplayers 6/8/10) |
| 1v1 / 2v2 | K4-Arenas duel ladder, capped at 2-per-side (2v2 via a generated round config; K4-Arenas-Bots optional, via the **Use bots** toggle) |
| Deathmatch | CS2Fixes (MetaMod) + CS2-Deathmatch |
| Jailbreak | Jailbreak (CS2Fixes removed in v0.9.1 — it crashed the server) |
| Warcraft | CS2-Warcraft-Plugin + ModelPrecacher |
| Zombie Escape | ZombieMod (CS2Fixes fork) + MultiAddonManager + ZombieReborn addon |

Vanilla modes (Competitive, Casual, Wingman, etc.) run with no managed plugins and have `gameinfo.gi` automatically restored to avoid CSS CLR crashes.

### Workshop Maps
- Download any workshop map by **Steam Workshop ID or URL**
- Uses **DepotDownloader** under the hood — no Steam client interference
- Auto-downloads DepotDownloader on first use
- Credentials cached after first login — no re-auth on every download
- **Real per-MB progress bar** — shows `X / Y MB (Z%)` against Steam's reported size
- **Verify before use** — downloads stage to a temp folder and are size/`.vpk`-checked before being promoted, so a failed/partial download never leaves a broken map
- **Cancel** an in-progress download at any time
- **Check for map updates** to keep downloaded maps current
- **Command-filter automation** — auto-detects maps that need `-disable_workshop_command_filtering` (from the Steam description) and applies the launch flag only for those, with a per-map override + Scan button
- **Paste button** — paste a full Steam Workshop URL; the field strips non-numeric characters automatically

### Remote Access
- **Local pywebview window** + **remote web panel** are the same Flask SPA
- **Two-tier access**: admin PIN unlocks everything; optional **guest PIN** gives friends
  limited remote (change map, change mode, download workshop maps — no Start/Stop, no config,
  no logs, no bans)
- **PIN brute-force protection**: per-IP lockout after 5 fails + global decay backoff
- **Cloudflare quick tunnel** documented in `TONIGHT.md` runbook for one-night-only HTTPS access

### Player Management
- Live **player list** with names and ping
- **Kick** or **ban** any player directly from the list
- **Manual ban** by SteamID
- Full **ban list viewer** with one-click unban
- Auto-refresh every 10 seconds

### Quick Actions
- **Broadcast a message** to all connected players — RCON command-separator (`;`) injection
  defanged, length-capped at 200 chars
- **Friendly fire** toggle
- **Restart round** / **End warmup**
- **Pause** / **Unpause** match

### Server Configuration
- Server **hostname** and **password**
- **Max players** override (per-mode defaults applied automatically)
- **Tickrate 128** toggle
- **Bot management** — add bots, kick all, set difficulty; **Use bots** toggle gates Arena bot-fill (humans-only ladder when off)
- **Config presets** — save, load, and delete named server configurations
- **Game Server Login Token (GSLT)** — set token for VAC-secured public servers
- **Steam account** — credentials for workshop downloads (separate account recommended)

### Appearance & Settings
- **Theme** — Dark, Light, or System (follows OS preference)
- **Accent colour** — Purple, Blue, Teal, Green, Orange, or Red
- **Compact mode** — tighter spacing for smaller displays
- **Confirm before stopping** toggle
- **Auto-scroll log** and configurable log line limit
- **Browser notifications** on server start / stop / crash
- **Keybinds** — configurable keyboard shortcuts for any server action (Stop, Quick Restart, Pause, Restart Round, End Warmup, bots, etc.)

### Status Bar
- Current **map** and **game mode**
- Server **uptime**
- **LAN connect string** — click to copy
- **Public / external IP** — fetched automatically, click to copy
- Update badges when a newer CS2 server build or app release is available

### CS2 Server Updates
- Checks Steam API on launch for a newer CS2 server build
- One-click update via steamcmd (server stops automatically)

### App Self-Updates
- Checks GitHub Releases on launch
- Update badge in the header links to the releases page

### First-Run Setup
- **Setup wizard** on first launch — point it at your server folder and set your admin PIN
- If CS2 server isn't installed yet, one click downloads steamcmd and installs the full server (~15 GB)
- **Install / Reinstall** button in Config for any machine

### RCON Console
- Full **RCON command console** — send any command, see the response
- **RCON diagnostic** tool — tests TCP connectivity and auth, shows actionable error messages

### Map Veto / Match Setup *(v0.10.0)*
A guided match-setup flow ending in a CS2 map veto, with **captains vetoing from their own
devices** while the operator's UI mirrors the session live over SSE:
- **Five stages:** roster (10 players + team names + optional SteamIDs) → random 5+5 teams
  (re-shuffle option) → captain election (each team votes 5×, ties auto-revote) → captain
  links (LAN + Public URLs + **QR codes** for phone scanning) → BO1/BO3/BO5 veto board.
- **Dedicated Veto tab** in the sidebar, with stage-pill navigation and a Reset button for
  the operator; captains see a simplified view scoped to their actionable stages.
- **Single-use scoped captain tokens** — `secrets.token_urlsafe(32)`, mints a captain
  session cookie on claim; revoke + reissue if a token leaks.
- **Cinematic finale** — title slide-up, staggered map reveal, accent-glow pulse on the
  decider, 30-piece confetti shower.  Plays exactly once per session.
- **MatchZy handoff** — auto-generates a MatchZy match config from the veto result,
  writes it atomically to `csgo/cfg/MatchZy/<matchid>.json`, and issues
  `matchzy_loadmatch <basename>` via RCON.  MatchZy then runs the series natively
  (map order, knife, scoring, map-end → next).  Three-way outcome: file write fails →
  500; RCON fails → 200 + status panel with the file path so the operator can run
  the load manually; full success → 200 + green ✓.
- **Match history** *(v0.10.2)* — the last 10 completed sessions are
  persisted to `oblivion_matches.json` and viewable via the **📜 History**
  button on the Veto header (mode, teams, captains, decider-tagged
  maplist).
- **MatchZy cvar editor** *(v0.11.1, local-only)* — Config tab adds an
  editable key/value row list; values merge over the built-in defaults
  (`mp_warmup_pausetimer=0`, `matchzy_minimum_ready_required=2`) at
  finale time; operator wins on conflicts; **blank value actively
  suppresses** a default cvar so it's not sent at all.
- **Roster presets** *(v0.11.1)* — Save/Load named 10-player rosters
  (localStorage; per-browser).  Useful for recurring teams.
- **Spectator URL** *(v0.11.1)* — operator generates a per-session
  token; `/spectate?token=…` serves a standalone, auto-refreshing
  read-only view for casters/observers.  Sanitized (Discord IDs
  omitted, SteamIDs masked first-4 + last-4, captain tokens never
  included).  No SPA, no auth flow — works as an OBS browser source.
- **Captain-token idempotency** *(v0.11.2)* — re-calling `issue_tokens`
  (e.g. a captain refreshes the links page mid-issue) no longer
  silently invalidates the other captain's URL.  Per-team rotation
  via `revoke_token('A')` / `revoke_token('B')` is the explicit
  escape hatch.
- **Session persistence** *(v0.11.3)* — accidental Ctrl+Q, Windows
  update, or pywebview crash mid-session no longer evaporates the
  in-progress veto.  Captain claim bindings, partial ban/pick
  sequence, ready flags all survive an app restart via atomic write
  to `oblivion_veto_active.json` (12 h cutoff for stale sessions).

Full spec in [VETO.md](VETO.md); implementation map in [INGEST.md](INGEST.md) → "API — map
veto" + "Frontend — Veto tab".  See [DISCORD.md](DISCORD.md) for the optional
v0.11.0 bot integration, and [MOBILE_CHECK.md](MOBILE_CHECK.md) for the
real-device checklist run before live sessions.

---

## Getting Started

### Option A — Pre-built executable (recommended)
1. Download `OblivionServerTool.exe` from [Releases](https://github.com/jacquesvniekerk-eng/OblivionServerTool/releases)
2. Run it — no installer needed (or use the `OblivionServerToolSetup-v*.exe` installer for a Start Menu entry)
3. On first launch a setup wizard will ask for your CS2 server directory and admin PIN
4. If you don't have a CS2 server yet, click **Install Now** — it downloads everything automatically

### Option B — Run from source
```bash
git clone https://github.com/jacquesvniekerk-eng/OblivionServerTool.git
cd OblivionServerTool
pip install -r requirements.txt
python main.py
```

### Building the executable yourself
```bash
# Produces dist\OblivionServerTool.exe
build.bat

# Optional: build the installer (requires Inno Setup)
# https://jrsoftware.org/isinfo.php
ISCC installer.iss
# Output: dist\OblivionServerToolSetup-v<version>.exe
```

---

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10 / 11 (64-bit) | Edge WebView2 runtime required — ships with Windows 11, 1-click install on Windows 10 |
| CS2 dedicated server | Can be installed by the tool if missing |
| Steam account (dedicated) | For workshop downloads — **use a separate account**, not your personal one |
| Port 27015 open (TCP + UDP) | For players to connect |
| Port 5050 open (TCP, LAN only) | For the remote web panel |

> **Why a dedicated Steam account?**  
> steamcmd signs into Steam to download workshop maps. If it uses your main account, it will disconnect your Steam desktop client. CS2 is free — create a second account at [store.steampowered.com](https://store.steampowered.com) and enter it under **Steam Account** in Config.

> **Edge WebView2** is included with Windows 11 and most up-to-date Windows 10 installs. If the app fails to open a window, download the runtime from [microsoft.com/en-us/edge/download/webview2](https://developer.microsoft.com/en-us/microsoft-edge/webview2/).

---

## Remote Web Panel

The tool runs a local Flask server on port 5050. The same interface you see in the desktop window is accessible from any device on your network:

1. Open `http://<server-LAN-ip>:5050` in any browser (the LAN IP is shown in the status bar)
2. Enter your admin PIN
3. Control the server from your phone, tablet, or any browser on the LAN

Remote sessions authenticate with a PIN and expire after 8 hours. The desktop window gets an automatic session and never prompts for the PIN.

### Access tiers

Two PINs, two levels of access (set both in Config → Security; the guest PIN is optional):

- **Admin PIN** — full control of everything the panel exposes.
- **Guest PIN** — limited role for friends: change map, change game mode, and download workshop
  maps. Guests can't start/stop the server, edit config, manage bots/bans, or use keybinds.
  RCON, CS2 install/update, and Steam login stay local-window-only regardless.

Enforcement is fail-closed (an allowlist of guest-reachable routes; everything else is admin-only).

### Off-LAN access (optional)

To let friends reach the panel over the internet, run a Cloudflare quick tunnel
(`cloudflared tunnel --url http://localhost:5050`) and share the printed HTTPS URL + a PIN — no
router changes, encrypted transport. See [TONIGHT.md](TONIGHT.md) for the full steps.

---

## Where this is going (post-1.0 direction)

CS2 was the founding game; it isn't the destination.  The strategic
roadmap to v1.0 is in [PLAN.md](PLAN.md), but the headline:

- **v0.12** *(shipped)* — driver abstraction + plugin registry seam
- **v0.13** *(shipped)* — PLATFORM.md design doc + first method
  migration into the driver
- **v0.14** *(shipped)* — Plugin Manager packs + runtime bootstrap
  modal + live mode swap on running server
- **v0.15** *(shipped)* — Plugin Manager community arc: self-describing
  plugins (`plugin.json`), `OblivionPluginRegistry` remote fetch,
  uninstall + URL install + update notifications + search
- **v0.16** *(shipping)* — v1.0 prep waves: config backup/restore,
  team profiles, History + Pre-flight + Logs pages, tournament
  templates, demo browser, Discord mock-veto smoke button; v0.16.5
  closed the heaviest first-run friction (auto-install MetaMod + CSS
  runtime, Edge WebView2 bundled into installer); v0.16.6 added the
  Getting Started card + one-click Discord setup check + actionable
  Pre-flight "→ Fix" buttons
- **v1.0** — open-source under BSL (non-compete, reverts to Apache
  after 4 years), donation-funded, Plugin Manager + tournament
  workflow as the headline differentiators.  Remaining work: spectator
  URL polish, Discord bot resilience soak
- **Post-1.0** — second game driver (TF2 driver paused at v0.13;
  resumes here), Linux + headless mode, first non-Source game driver

The two-audience pitch: **consumer-grade UX, pro-grade reliability.**
Approachable enough for first-time CS2 server hosts, automated and
reliable enough for tournament operators.  Both audiences served
by the same engineering work.

---

## Versioning

This project follows [Semantic Versioning](https://semver.org).  
Versions below `1.0.0` are considered work-in-progress.  
`1.0.0` will be the first stable, fully tested release.

---

## License

- **Pre-v1.0** — MIT (everything currently in this repo)
- **v1.0+** — Business Source License 1.1, reverting to Apache 2.0 on
  the Change Date (~4 years post-release).  See [LICENSE.md](LICENSE.md)
  for the rationale + parameters under review.

The relicense is driven by task #89 (v1.0 launch posture); MIT
remains in force on every commit before the BSL-effective tag, and
forks made under MIT keep MIT rights to that snapshot.

## Donations

Once v1.0 ships, GitHub Sponsors + Ko-fi will be the two links — see
[DONATIONS.md](DONATIONS.md) for the platform comparison + decisions
still pending.  No links live yet; soliciting money for an MIT-only
project that can't legally restrict SaaS clones is a bad look — we
wait for the license + posture flip together.
