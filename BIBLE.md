# BIBLE — Oblivion Server Tool

> The single source of truth for **what** this project is, **why** it exists, and the
> **principles** that govern every decision. ROADMAP.md says *how* we get there;
> TODO.md tracks *where we are*. When in doubt, this file wins.

---

## 1. The One-Sentence Goal

**Make running a Counter-Strike 2 dedicated server on Windows as simple as running a
desktop app — no command lines, no steamcmd windows, no third-party RCON clients.**

---

## 2. The Problem

Hosting a CS2 dedicated server today means juggling:

- Hand-written `+exec` command lines and a wall of console CVars
- A separate `steamcmd` window for installs and workshop downloads
- A standalone RCON client to talk to the running server
- Manual `gameinfo.gi` edits to load MetaMod / CounterStrikeSharp
- Hunting down, version-matching, and hand-copying plugin DLLs per game mode
- No safe way to administer the server remotely from a phone

Every one of these is a place where a casual host gives up. The barrier to entry is
operational friction, not game knowledge.

---

## 3. The Solution

A single Windows executable that wraps the entire lifecycle of a CS2 server behind one
interface:

- **One window, one click** — install, start, stop, restart, change map and mode live.
- **The same UI everywhere** — the desktop window (pywebview / Edge WebView2) and the
  remote web panel are the *identical* Flask SPA. Learn it once, use it from any device
  on the LAN with a PIN.
- **Plugins that just work** — pick a game mode and the correct MetaMod / CSS plugins are
  deployed, verified, and hot-reloaded automatically; switching away cleans them up and
  restores `gameinfo.gi` so vanilla modes never crash.
- **Workshop without the pain** — download any map by ID or URL via DepotDownloader,
  with cached credentials and live progress.
- **Day-to-day admin built in** — player list, kick/ban, bots, chat broadcast, round and
  match controls, RCON console.

---

## 4. Who It Is For

| Audience | What they get |
|----------|---------------|
| **Casual / private hosts** | Spin up a server for friends without reading a wiki. Auto-install, setup wizard, sane defaults. |
| **Community / scrim admins** | Live mode switching, plugin-backed modes (Retakes, Practice, Jailbreak, Warcraft, ZE), remote phone admin. |
| **Tinkerers** | Full RCON console, diagnostics, config presets, and an honest log of everything the tool does. |

The tool optimises for the **casual host first**. Power features must never compromise the
"download exe → click Start → playing in minutes" path.

---

## 5. Design Principles

These are non-negotiable. New work is judged against them.

1. **Zero-CLI promise.** If a task can be done in the GUI, it must be. The user should
   never *need* to open a terminal or edit a file by hand.
2. **Single source of truth.** `AppCore` (in `core.py`) owns all server state and side
   effects. The web layer and desktop window are thin views over it. Never duplicate
   state or business logic in the UI.
3. **Safe by default, honest always.** Destructive or surprising actions confirm first.
   Everything the tool does is written to the visible log — no silent failures, no
   mystery state.
4. **The bundle mirrors `csgo/`.** Every plugin bundle is laid out exactly like the CS2
   `csgo/` directory (`addons/`, `cfg/`, `characters/`, …) so deployment is a uniform
   copy, not a per-plugin special case. (Enforced this session; see INGEST.md
   `_PLUGIN_COPY_RULES`.)
5. **Windows-only, on purpose.** This is a Windows desktop tool. We do not carry
   cross-platform baggage (e.g. non-Windows SQLite runtimes were pruned). Simplicity over
   theoretical portability.
6. **Self-contained.** Ships as one `.exe`. Dependencies it needs (DepotDownloader, the
   CS2 server itself) are fetched on demand, not assumed.
7. **Secure remote surface.** The web panel is LAN-facing. PIN auth, lockout on brute
   force, session expiry, and credential masking for remote sessions are mandatory, not
   optional.
8. **No premature abstraction.** Three similar lines beat a clever framework. Plugins are
   described by plain data tables, not a plugin SDK.

---

## 6. Architecture in Brief

```
main.py            → bootstrap: kills zombies, starts Flask, opens pywebview window
  └─ cs2servergui/
       core.py     → AppCore: the brain. Server lifecycle, plugins, workshop, players,
                     bans, RCON orchestration, crash monitor, self-update.
       web.py      → create_flask(core): the SPA + JSON API. A thin view over AppCore.
       rcon.py     → RCONClient: thread-safe Source RCON (fresh TCP per command).
       config.py   → all paths, constants, and game-data tables (no project imports).
       plugins/    → per-mode bundles, each mirroring the csgo/ layout.
       static/     → the SPA (app.js, app.css) and templates.
```

The contract: **the UI asks `AppCore` to do things and renders what `AppCore` reports.**
Business logic lives in `core.py` and nowhere else.

For the full symbol-level map, see [INGEST.md](INGEST.md).

---

## 7. What "Done" Looks Like (v1.0.0)

`1.0.0` is the first release we are willing to call **stable and fully tested**. It is
reached when:

- Every advertised feature in the README works on a clean Windows 10/11 machine with no
  manual intervention.
- Every game mode boots, loads its plugins, and is verified end-to-end on a real server.
- Documentation (README plugin table, CHANGELOG) matches reality.
- There is an automated smoke path that catches regressions before release.
- A first-time user can go from "downloaded the exe" to "friends connected" without
  touching a command line or reading external docs.

---

## 8. Explicit Non-Goals

- **Not** a Linux / macOS server manager.
- **Not** a match-making, league, or stats platform — match flow is delegated to plugins
  like MatchZy.
- **Not** a general game-server panel (no support for non-CS2 games).
- **Not** a plugin marketplace or installer for arbitrary third-party plugins — we ship a
  curated, version-matched set per mode.
- **Not** a public, internet-exposed control panel — the web surface is designed for LAN
  use behind a PIN.

---

*Companion docs:* [ROADMAP.md](ROADMAP.md) (the plan) · [TODO.md](TODO.md) (the checklist) · [INGEST.md](INGEST.md) (the code map)
