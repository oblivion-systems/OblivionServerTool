# Scripts

Helper scripts that live outside the Python app.

## Gaming-mode toggle

**Problem**: running `cs2.exe -dedicated` (server) + `cs2.exe` (game
client) + Oblivion on the same PC, alt-tabbing between them causes
brief in-game lag spikes.

**Cause**: Windows reshuffles CPU/GPU/power priorities every time the
foreground process changes.  Server + client end up competing for
the same execution resources.

**Fix**: pin them to different cores so they can't compete, plus a
few power/Game-Mode tweaks that reduce reshuffling.

### How to use

| File | What it does |
|---|---|
| `gaming-mode-on.bat` | Double-click before a play session.  Auto-elevates to admin, applies all tweaks. |
| `gaming-mode-off.bat` | Double-click after the session.  Restores Windows defaults. |
| `gaming-mode-status.bat` | Show current state without changing anything (no admin needed). |
| `gaming-mode.ps1` | The PowerShell engine that does the work.  Both .bat files invoke it. |

### What "gaming-mode-on" actually changes

1. **Power Plan** → Ultimate Performance (or High Perf fallback).
   Stops CPU from down-clocking on foreground change.
2. **Game Mode** → Off.  Windows' Game Mode tries to "help" by
   shuffling resources to whichever app is foreground — exactly
   what we *don't* want.
3. **Game DVR** → Off.  Background recording overhead removed.
4. **`cs2.exe -dedicated` (server)** → priority `High`, CPU affinity
   pinned to cores 0..3 (auto-scales if you have > 16 logical cores).
5. **`cs2.exe` (client)** → CPU affinity pinned to cores 4..N
   (priority left alone — VAC/anti-cheat doesn't love being touched).
6. **`OblivionServerTool.exe`** → CPU affinity pinned to core 0.

After this, alt-tabbing has no effect on CPU allocation because
everything is already explicitly pinned.  Lag spike eliminated.

### Things to know

- **Run order matters slightly**: the script pins whatever processes
  are *currently running*.  If you start the server / client AFTER
  running the script, re-run it to pin the new processes.
- **Both .bat files self-elevate to admin** (some `powercfg`
  operations need it).  `gaming-mode-status.bat` does NOT — you can
  check state any time without elevation.
- **The 4/4 split** is a sensible default but you can override with
  `.\gaming-mode.ps1 -ServerCores 6` if you want a different split.
- **Anti-cheat**: the script never touches the client process's
  *priority*, only its affinity.  Affinity changes are routinely
  done by Process Lasso etc. and don't trigger VAC.
- **Idempotent**: safe to run multiple times.

### When you'd want this

- You're playing on the same PC that's hosting the server, AND
- You feel any lag/hitching when alt-tabbing to Oblivion / Discord
  / a browser

### When you wouldn't

- You're hosting on a dedicated machine and just playing on this
  PC (no contention to fix)
- Your gaming PC has so many cores you never notice the contention

### Related future work

Oblivion's Config tab includes an in-app **Gaming Mode** toggle
(v0.12) that wraps these scripts — one click for ON / OFF / Status.
The standalone scripts here remain available for operators who prefer
running them outside the app (e.g. from a desktop shortcut or scheduled
task).
