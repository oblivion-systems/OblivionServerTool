# Process Lasso — set-and-forget CPU pinning for CS2

This is the "install once, never think about it again" alternative
to `gaming-mode.ps1`.  Process Lasso runs in the tray, watches
process spawn events, and applies CPU affinity / priority rules
automatically — including across reboots, restarts, and "I just
started the server again."

The PowerShell script in this folder does the same thing but you
have to run it each time something starts.  Process Lasso is set
up once and forever.

## Install

1. Download from [bitsum.com/processlasso](https://bitsum.com/processlasso/)
2. Run the installer.  Default install path is fine.
3. After install, Process Lasso starts in the system tray
   (taskbar, near the clock).  Double-click the tray icon to open
   its window.

Process Lasso is **free for personal use forever** after the
30-day trial of Pro features — and you don't need any Pro
features for this setup.

## Step 1 — Turn on ProBalance (the lazy-mode win)

ProBalance auto-detects when a foreground app needs more resources
and temporarily lowers the priority of busy background processes.
It's the single most useful Process Lasso feature for "I'm playing
games on my admin PC."

1. In the Process Lasso main window: **Options menu → ProBalance →
   Enable ProBalance** (should already be on by default).
2. **Options → ProBalance → Restrain background apps' priority
   class** (this is what makes the magic work).
3. Done.  ProBalance now runs in the tray, automatically.

For most setups this alone makes alt-tab lag spikes disappear.  If
you want belt-and-suspenders, add the explicit rules below.

## Step 2 — Explicit rules for CS2 server, client, and Oblivion

Process Lasso lets you set "always do this for this process" rules
that survive reboots and process restarts.

### A. OblivionServerTool.exe (pin to one core, low impact)

1. Start Oblivion so the process exists.
2. In Process Lasso's main window, find `OblivionServerTool.exe`
   in the list (use the search box if needed).
3. **Right-click → CPU Affinity → Always... → Custom...**
4. Tick **ONLY core 0**.  Click **OK**.
5. **Right-click → CPU Priority Class → Always... → Below Normal**

Why: Oblivion is just an admin panel.  It doesn't need much CPU
but it shouldn't compete with the server or client.  Pinning to
core 0 + Below Normal priority gives it just enough to be
responsive without ever fighting the game processes.

### B. cs2.exe **server** (priority High, pinned to first 4 cores)

This is the tricky one because cs2.exe is also the client.  We
need to match on the **command line** (which contains `-dedicated`
for the server, doesn't for the client).

1. Make sure the server is running (start it through Oblivion).
2. In Process Lasso, **Main menu → View → Show Multiple Process
   Instances Separately** (enables command-line distinction).
3. Find `cs2.exe` with `-dedicated` in the Command Line column
   (you may need to right-click the column headers and add the
   **Command Line** column if it's not visible).
4. **Right-click → CPU Priority Class → Always... → High**
5. **Right-click → CPU Affinity → Always... → Custom...**
6. Tick **cores 0–3** (or 0–5 if you have a bigger CPU and want
   to give the server more).  Untick the rest.  Click **OK**.
7. **Right-click → I/O Priority Class → Always... → High**

These "Always" rules now persist — even after you stop and
restart the server through Oblivion, Process Lasso will
immediately re-apply them.

### C. cs2.exe **client** (affinity only — leave priority alone)

VAC / anti-cheat tolerates affinity changes (Process Lasso does
them routinely on every gaming PC).  Priority changes on the
client process are riskier, so we leave priority at Normal.

1. Launch CS2 (the game).
2. Find `cs2.exe` *without* `-dedicated` in the Command Line.
3. **Right-click → CPU Affinity → Always... → Custom...**
4. Tick cores **4–N** (everything the server *isn't* using).
   E.g., on a 16-thread CPU pick cores 4 through 15.
5. Click **OK**.

Do **NOT** change the CS2 client's priority class.  Leave it Normal.

## Step 3 — Verify it stuck

After both processes are running and you've set the rules:

1. Switch to Process Lasso's main window.
2. Look at the **CPU Affinity** column for each cs2.exe instance.
   Should show your assigned cores (e.g., `0-3` for server,
   `4-15` for client).
3. **CPU Priority Class** should be:
   - OblivionServerTool.exe → Below Normal
   - cs2.exe (server, `-dedicated`) → High
   - cs2.exe (client) → Normal
4. Alt-tab between Oblivion and CS2 a few times.  Watch the
   Process Lasso CPU graph.  No spikes.  No reshuffling.  Done.

## Step 4 — Reboot test

Restart your PC.  Open Oblivion + start the server + launch CS2.
Process Lasso should re-apply the rules automatically the moment
each process spawns.  Verify in the main window — affinity and
priority should match Step 3.

## Tuning per-CPU

The "first 4 cores for server, rest for client" split is sensible
on most machines.  But:

| Your CPU | Server cores | Client cores |
|---|---|---|
| 4-core / 8-thread (e.g., i5-11400) | 0–3 (all 4 cores) | also all — split doesn't help, you don't have enough |
| 6-core / 12-thread (e.g., R5 5600) | 0–3 (2 physical cores via SMT) | 4–11 (4 physical cores) |
| 8-core / 16-thread (e.g., R7 5800X / 7700X) | 0–3 (2 physical) | 4–15 (6 physical) |
| 12-core / 24-thread (e.g., R9 7900X) | 0–7 (4 physical) | 8–23 (8 physical) |

Physical core boundaries matter slightly more than logical cores
because SMT siblings share execution resources.  On an 8c/16t chip,
cores 0+1 are one physical core, 2+3 another, etc.  Pinning the
server to cores 0–3 = pinning to 2 physical cores worth of compute.

For 5v5 at 128-tick, 2 physical cores is comfortable headroom for
the server.

## When to skip Process Lasso

- You're hosting on a dedicated machine and just playing on this
  PC.  No contention, nothing to fix.
- You don't notice any alt-tab lag.  Don't fix what isn't broken.
- You prefer the `gaming-mode.ps1` script (it's lighter — no tray
  app — but you have to re-run it each session).

## Compared to `gaming-mode.ps1`

|  | Process Lasso | `gaming-mode.ps1` |
|---|---|---|
| Cost | Free (personal use) | Free |
| Install | Yes | No |
| Persistence | Across reboots / process restarts | Re-run each session |
| GUI | Yes | No |
| Power Plan / Game Mode / DVR | No (Windows settings) | Yes |
| Tray icon | Yes | No |
| Auto-applies on process spawn | Yes | No |

Use them together if you want: Process Lasso for the CPU pinning,
the PowerShell script (or just manual Windows settings) for the
Power Plan + Game Mode + DVR.

## Troubleshooting

**"My rules disappeared after a reboot."**
You set a one-time rule instead of "Always."  Right-click the
process → look at the menu items — the ones with the rules
should have "Always" in the menu name and a checkmark or asterisk.
Set them as Always.

**"VAC banned me!"**
Hasn't happened to anyone running affinity rules on the client.
But: never set priority changes on the client cs2.exe to be safe.
Affinity changes are not detected as cheating; priority changes
might trip heuristics on some games.  CS2 itself doesn't appear to
care but no need to push it.

**"Process Lasso is using too much CPU."**
The tray app uses ~0.1% on average.  If it's spiking, your
ProBalance settings might be too aggressive — open Options →
ProBalance → Configure ProBalance Settings → reduce the trigger
sensitivity.

**"The server doesn't get pinned, only the client does."**
You probably didn't enable "Show Multiple Process Instances
Separately."  Without that, Process Lasso treats both cs2.exe
instances as the same process and applies the same rule to both.
Enable it via View menu.
