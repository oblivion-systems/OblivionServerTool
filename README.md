# Oblivion Server Tool

A desktop application for managing a **Counter-Strike 2 dedicated server** on Windows.  
Built with Python + Flask + pywebview (Edge WebView2). Ships as a single `.exe` with an optional installer.

> **Status: v0.9.2 candidate — work in progress.** Core features are stable. Post-v0.9.1
> shipped a remote-guest role, split team-size modes (1v1 / 2v2 / 3v3 / 4v4 / 5v5), Warcraft
> menu/chat dispatchers (recv-queue-overflow fix), a 20-bug audit sweep, and the workshop-maps
> root-cause fix. Tag-ready after a live smoke test.

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

## Versioning

This project follows [Semantic Versioning](https://semver.org).  
Versions below `1.0.0` are considered work-in-progress.  
`1.0.0` will be the first stable, fully tested release.

---

## License

MIT
