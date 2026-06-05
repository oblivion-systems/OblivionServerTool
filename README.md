# Oblivion Server Tool

A desktop application for managing a **Counter-Strike 2 dedicated server** on Windows.  
Built with Python + Flask + pywebview (Edge WebView2). Ships as a single `.exe` with an optional installer.

> **Status: v0.11.18 (released 2026-06-05).**  Eighteen releases shipped
> pushing the v0.11.x line to maturity.  Friday-eve adversarial sweep
> across veto, Discord, server/RCON, and SPA surfaced 12 real findings;
> all fixed.  **198/198 backend tests green.**
>
> **What's new since v0.11.0**:
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
> - **Status-bar version pill** *(v0.11.6)* — `v0.11.18` always visible
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
> **183/183 backend tests green** (28 v092 + 74 veto + 81 veto-api).
> Full per-release prose in [CHANGELOG.md](CHANGELOG.md); spec for the
> map-veto feature in [VETO.md](VETO.md); strategic roadmap to v1.0 in
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

- **v0.12** — driver abstraction + plugin registry: third-party
  plugins fetched on demand from upstream, not bundled
- **v0.13** — second game driver (TF2)
- **v0.14** — Linux + headless mode (systemd / Docker / multi-arch),
  no GUI required for server-grade unattended deployments
- **v0.15** — first non-Source game driver (Palworld / Valheim / TBD)
- **v1.0** — open-source under BSL (non-compete, reverts to Apache
  after 4 years), 2-3 games supported, donation-funded, Plugin
  Manager as the headline differentiator

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

MIT
