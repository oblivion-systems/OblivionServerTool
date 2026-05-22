# Oblivion Server Tool

A desktop GUI for managing a **Counter-Strike 2 dedicated server** on Windows.  
Built with Python + CustomTkinter. No installer required — single `.exe` you drop on any machine.

> **Status: v0.7.5 — work in progress.** Core features are working; expect rough edges.

---

## What it does

Running a CS2 dedicated server normally means juggling command-line arguments, steamcmd windows, and RCON clients. Oblivion Server Tool puts everything in one place — start the server, manage players, change maps, download workshop content, and administer remotely from your phone, all from a single window.

---

## Features

### Server Control
- **Start / Stop** the CS2 dedicated server with one click
- **Change map and game mode** live without restarting
- Animated status indicator — Offline / Booting / Online
- Server **uptime counter** in the status bar
- Crash detection — automatically marks the server offline if the process dies unexpectedly
- **Auto-start** option: server launches automatically when the tool opens

### Map & Mode Selection
- Pick from all **official CS2 maps**, filtered per game mode
- Full **game mode support**: Competitive, Casual, Wingman, 3v3, 4v4, 1v1, Arms Race, Demolition, Deathmatch, Zombies, Surf, KZ / Climb, Retakes
- **Workshop map picker** — shows downloaded maps by real name, not just ID
- **Browse Steam Workshop** button, pre-filtered by the currently selected game mode

### Workshop Maps
- Download any workshop map by **Steam Workshop ID**
- Uses **DepotDownloader** under the hood — no Steam client interference, no buffering issues
- Auto-downloads DepotDownloader on first use (no manual setup)
- Credentials cached after first login — no re-auth on every download
- **Cancel** an in-progress download at any time
- **Check for map updates** to keep downloaded maps current

### Player Management
- Live **player list** with names and ping
- **Kick** or **ban** any player directly from the list
- **Manual ban** by SteamID
- Full **ban list viewer** with one-click unban
- Auto-refresh every 10 seconds (optional toggle)

### Quick Actions
- **Broadcast a message** to all connected players
- **Friendly fire** toggle (on/off with live RCON)
- **Restart round** / **End warmup**
- **Pause** / **Unpause** match

### Server Configuration
- Server **hostname** and **password**
- **Max players** override (per-mode defaults applied automatically)
- **Tickrate 128** toggle
- **Bot management** — add 1 or 5 bots, kick all, set difficulty
- **Config presets** — save, load, and delete named server configurations

### Remote Web Panel
- Built-in Flask web server accessible from any device on your LAN
- **PIN-protected** admin interface (4-digit keypad)
- Remotely change map, mode, and workshop map
- Request workshop map downloads (requires desktop approval)
- Broadcast messages to players
- Real-time log feed via Server-Sent Events

### Status Bar
- Current **map** and **game mode**
- Server **uptime**
- CS2 **build number** (orange when an update is available)
- **LAN connect string** — click to copy to clipboard
- **Public / external IP** — fetched automatically, click to copy
- Remote admin URL

### CS2 Server Updates
- Checks Steam API on launch for a newer CS2 server build
- Orange **"⬆ Update!"** button when an update is available
- One-click update via steamcmd (server stops automatically)

### App Self-Updates
- Checks GitHub Releases on launch for a newer version of this tool
- Orange **"⬆ App vX.X.X available"** label in the header when found
- Click it to open the releases page and download

### First-Run & Installation
- **Setup wizard** on first launch — just point it at your server folder
- If CS2 server isn't installed yet, one click downloads steamcmd and installs the full server (~15 GB)
- **Install / Reinstall** button in Config for any machine

### RCON Console
- Full **RCON command console** — send any command, see the response
- **RCON diagnostic** tool — tests TCP connectivity and auth, shows actionable error messages

---

## Getting Started

### Option A — Pre-built executable (recommended)
1. Download `OblivionServerTool.exe` from [Releases](https://github.com/jacquesvniekerk-eng/OblivionServerTool/releases)
2. Run it — no installer needed
3. On first launch a setup dialog will ask for your CS2 server directory
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
build.bat
# Output: dist\OblivionServerTool.exe
```

---

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10 / 11 | 64-bit |
| CS2 dedicated server | Can be installed by the tool if missing |
| Steam account (dedicated) | For workshop downloads — **use a separate account**, not your personal one |
| Port 27015 open (TCP + UDP) | For players to connect |
| Port 5000 open (TCP, LAN only) | For the remote web panel |

> **Why a dedicated Steam account?**  
> steamcmd signs into Steam to download workshop maps. If it uses your main account, it will disconnect your Steam desktop client. CS2 is free — create a second account at [store.steampowered.com](https://store.steampowered.com) and enter it under **🔑 Steam** in the tool.

---

## Remote Web Panel

The tool runs a local web server on port 5000. To use it from another device on the same network:

1. Click **🌐 Web Panel** in the tool, or open `http://<server-LAN-ip>:5000` in any browser
2. Enter the admin PIN (default: `1234` — change it in `config.py` before distributing)
3. Control the server from your phone, tablet, or any browser on the LAN

---

## Versioning

This project follows [Semantic Versioning](https://semver.org).  
Versions below `1.0.0` are considered work-in-progress.  
`1.0.0` will be the first stable, fully tested release.

---

## License

MIT
