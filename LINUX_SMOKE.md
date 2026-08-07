# Linux Smoke Test — v1.2 operator-flow parity

The last gate before **v1.2.0 final**. The unit suite (`353 pass` on both
OSes) proves the platform seam returns the right *values* on Linux, but it
mocks every real binary. This walkthrough exercises the flows that only
break against **real** steamcmd / CS2 / MetaMod / CSS binaries on a real
Linux host — the gaps a genuine Linux operator hits.

Run it once on Ubuntu 22.04 (bare metal or the shipped Docker image). All
phases PASS → cut `v1.2.0-beta`. Any FAIL → capture the phase + the app log
(`/config/logs/` or the panel's log drawer) and stop.

Each phase names the **code path** and the **commit** it validates, so a
failure points straight at the culprit.

---

## What each phase validates

| Phase | Validates | Code path | Commit |
|---|---|---|---|
| 0 | Automated pre-checks | `tests/smoke.py`, `test_drivers.py` | — |
| 1 | Headless boot + web panel | `--headless`, `platform.has_display()` | v1.1 |
| 2 | **steamcmd bootstrap** | `steamcmd_download_url()`, `_extract_steamcmd_archive()` | **alpha3 `25a3b23`** |
| 3 | CS2 server download | `server_binary_rel_path()` (linuxsteamrt64) | v1.1 |
| 4 | MetaMod + CSS runtime | `_safe_extract_zip` external_attr, `_safe_extract_targz`, `metamod_bin_arch()` | **alpha2 `e9f8d76`** + v1.1 |
| 5 | Server boot + process markers | `list_pids()` (/proc scan), `server_process_name()` | v1.1 |
| 6 | RCON round-trip | `RCONClient`, `_resolve_rcon_host` | v1.1 + `195c321` |
| 7 | **Workshop map download** | `depotdownloader_asset_os()`, `make_executable()` on the ELF | **alpha2 `e9f8d76`** |
| 8 | Zombie / port-collision recovery | `own_process_names()`, `kill_pid()` (SIGKILL) | **alpha2 `e9f8d76`** |
| 9 | Case-mismatch diagnostic | `case_mismatch_hint()` | v1.1 |

---

## Prerequisites

- **Ubuntu 22.04** host (or the Docker image — see below). Other distros
  work but 22.04 is what CI + the image target.
- **i386 multiarch** + runtime deps (bare metal only — the image bakes these):
  ```bash
  sudo dpkg --add-architecture i386
  sudo apt-get update
  sudo apt-get install -y python3 python3-pip lib32gcc-s1 libstdc++6 libstdc++6:i386 iproute2 ca-certificates curl
  ```
- **~20 GB free disk** (CS2 dedicated server is ~15 GB).
- Network egress to Valve CDN + GitHub.
- Steam credentials for Phase 7 (workshop). A **throwaway** Steam account is
  fine and recommended — never the primary.
- *(Optional, Phase 10)* a **disposable GSLT** + a forwarded UDP 27015 to
  test internet reachability. Leave unset for a LAN-only smoke.

> **Secrets:** set the RCON password, Steam login, and any GSLT **in the web
> panel**, not in this file or in shell history. Never paste the real
> production RCON password or GSLT onto a shared box.

### Two ways to run

**A — Docker (fastest, no host pollution):**
```bash
git clone <repo> oblivion && cd oblivion
docker compose up -d --build      # uncomment `build: .` in docker-compose.yml first
docker compose logs -f            # watch boot
# panel → http://localhost:5050 ; set server_dir = /srv/cs2 in the UI
```

**B — Bare metal:**
```bash
git clone <repo> oblivion && cd oblivion
pip3 install -r requirements-headless.txt
python3 main.py --headless        # panel → http://localhost:5050
# set server_dir = /srv/cs2 (or any path with ~20 GB) in the UI
```

---

## Phase 0 — Automated pre-checks (server-free)

Proves imports, config round-trip, Flask boot + auth gate, and every
`platform.py` seam value resolve correctly on Linux **before** a real
server is involved.

```bash
python3 tests/smoke.py            # → SMOKE: ALL PASS
python3 tests/test_drivers.py     # → 42 passed, 0 failed
```

- [ ] `smoke.py` prints **SMOKE: ALL PASS**
- [ ] `test_drivers.py` prints **42 passed, 0 failed** (includes the alpha2 +
      alpha3 Linux-parity tests, now running on *actual* Linux)

---

## Phase 1 — Headless boot + web panel

```bash
python3 main.py --headless        # bare metal
# (Docker already running headless via CMD)
```

- [ ] Log shows `Oblivion Server Tool vX.Y.Z (headless)` and
      `Remote web panel → http://localhost:5050`
- [ ] On an **SSH-only box** (no `$DISPLAY`), launching **without**
      `--headless` still auto-falls-back to headless — proves
      `platform.has_display()` returns `False` correctly
- [ ] `curl -fsS http://localhost:5050/api/ping` → succeeds
- [ ] `curl -s -o /dev/null -w '%{http_code}' http://localhost:5050/api/state`
      → **not** `200` (auth gate holds; expect 401/403)
- [ ] Open the panel in a browser, complete PIN setup, log in

---

## Phase 2 — steamcmd bootstrap  ⭐ alpha3

Set `server_dir = /srv/cs2` in the panel, then trigger **Install CS2 Server**
(or Start, which installs first). Watch the log for Step 1/2.

- [ ] Log: `Step 1/2 — Downloading steamcmd from Valve…` →
      `steamcmd downloaded and extracted ✓`
- [ ] The launcher landed **executable** (this is the whole point of alpha3):
      ```bash
      ls -l /srv/cs2/steamcmd.sh            # → -rwxr-xr-x ... steamcmd.sh
      ls -l /srv/cs2/linux32/steamcmd       # → -rwxr-xr-x ... steamcmd  (loader also +x)
      ```
- [ ] No leftover archive: `ls /srv/cs2/steamcmd.tar.gz` → **No such file**
      (removed after extraction)

> If `steamcmd.sh` is `-rw-r--r--` (not executable), the tarfile mode
> preservation or the `make_executable()` belt-and-suspenders regressed —
> that's the exact alpha3 failure mode.

---

## Phase 3 — CS2 dedicated server download (~15 GB, the long pole)

Step 2/2 runs `steamcmd.sh +app_update 730`. This also proves Phase 2's
`+x` actually took — a non-executable `steamcmd.sh` fails here.

- [ ] Log streams steamcmd progress, ends with a success/validated line
- [ ] The Linux server binary exists **and is executable**:
      ```bash
      ls -l "/srv/cs2/steamapps/common/Counter-Strike Global Offensive/game/bin/linuxsteamrt64/cs2"
      # → -rwxr-xr-x ... cs2
      ```
      (validates `platform.server_binary_rel_path()` → `linuxsteamrt64/cs2`)

---

## Phase 4 — MetaMod + CSS runtime  ⭐ alpha2 (+ v1.1)

Deploy a mode that needs CounterStrikeSharp (e.g. a MatchZy/competitive
pack), or install MetaMod + CSS from the Plugin Manager.

- [ ] MetaMod extracted from its **.tar.gz** into
      `.../csgo/addons/metamod` (validates `_safe_extract_targz` +
      `metamod_bin_arch()` → `linuxsteamrt64`)
- [ ] CSS-with-runtime **.zip** extracted; runtime files kept their exec
      bit (the alpha2 `external_attr` fix). Spot-check:
      ```bash
      CSS="/srv/cs2/steamapps/common/Counter-Strike Global Offensive/game/csgo/addons/counterstrikesharp"
      find "$CSS" -type f -perm -u+x | head    # expect dotnet runtime bins / *.so with +x
      ```
- [ ] `gameinfo.gi` was patched to load MetaMod (search it for the
      `csgo/addons/metamod` Game entry)

---

## Phase 5 — Server boot + process markers

Start the server from the panel.

- [ ] The panel status flips to **Running** (proves the `/proc/<pid>/cmdline`
      scan in `list_pids()` + `probe_existing_server()` find it on Linux)
- [ ] The dedicated-server process is discoverable by name + marker:
      ```bash
      pgrep -af -- '-dedicated'   # → a `cs2 ... -dedicated ...` line
      ```
      (validates `server_process_name()` = `cs2` + args marker `-dedicated`)
- [ ] It's listening on the game/RCON port (validates `listeners_on_port()`
      via `ss`):
      ```bash
      ss -tlnp 'sport = :27015'   # → LISTEN, users:(("cs2",pid=...))
      ```

---

## Phase 6 — RCON round-trip

From the panel's console, send `status` (or `hostname`).

- [ ] RCON connects to `127.0.0.1:27015` and returns output in the panel
      (proves `RCONClient` + `_resolve_rcon_host` + the `195c321`
      "stop discarding RCON output" fix work on Linux)
- [ ] With CSS deployed, `meta list` shows **CounterStrikeSharp** loaded and
      `css_plugins list` shows the mode's plugins — the functional proof that
      Phase 4's extraction actually produced a loadable runtime

---

## Phase 7 — Workshop map download  ⭐ alpha2

Save Steam credentials in the panel, then switch to a **workshop** map.

- [ ] Log: `DepotDownloader not found — downloading…` → picks
      `DepotDownloader-linux-x64.zip` → `DepotDownloader installed ✓`
      (validates `depotdownloader_asset_os()` = `linux`)
- [ ] The ELF landed **executable** (validates `make_executable()` on the
      DepotDownloader binary — without it, the next line fails):
      ```bash
      ls -l /srv/cs2/depotdownloader/DepotDownloader   # → -rwxr-xr-x
      ```
- [ ] Log: `WORKSHOP DOWNLOAD (DepotDownloader)` completes, the map's `.vpk`
      lands under the workshop content dir, and the map loads on the server

---

## Phase 8 — Zombie / port-collision recovery  ⭐ alpha2

With the app running on 5050, occupy the port with a foreign process, then
launch a second app instance.

- [ ] **Foreign holder** — the second launch leaves it alone and falls back:
      ```bash
      python3 -c 'import socket,time; s=socket.socket(); s.bind(("0.0.0.0",5050)); s.listen(); time.sleep(120)' &
      python3 main.py --headless
      # → "Port 5050 held by '<name>' (PID …) — not ours, leaving it alone"
      # → binds 5051 instead
      ```
- [ ] **Our own zombie** — if a stale `python3`/onefile instance holds the
      port, the launcher kills it via SIGKILL and reclaims 5050:
      `"Port 5050 held by our own 'python3' (PID …) — killing zombie…"`
      (validates `own_process_names()` incl. `python3` + `kill_pid()` SIGKILL)

---

## Phase 9 — Case-mismatch diagnostic (Linux-only)

Point `server_dir` at a path with a wrong-case component, e.g.
`/srv/cs2` where the real dir is `steamapps` but you type `SteamApps`.

- [ ] Pre-flight surfaces a hint like:
      `case mismatch — expected 'steamapps' but found 'SteamApps' in … (Linux is case-sensitive)`
      (validates `case_mismatch_hint()` — a generic "not installed" here = regression)

---

## Phase 10 — (optional) Reachability + clean shutdown

- [ ] *(if GSLT set + UDP 27015 forwarded)* the Status tab's **reachability**
      panel reports the server visible to the Steam master server
- [ ] **Stop** from the panel → the `cs2 -dedicated` process is gone
      (`pgrep -af -- '-dedicated'` empty) — validates `kill_pid()` on the
      server PID
- [ ] Stop the app (Ctrl-C, or `docker compose down`, or
      `systemctl --user stop oblivion-server-tool`) → clean exit, config
      saved (`/config/oblivion_config.json` intact)

---

## Result

| Phase | PASS/FAIL | Notes |
|---|---|---|
| 0  Automated pre-checks |  |  |
| 1  Headless boot |  |  |
| 2  steamcmd bootstrap ⭐ |  |  |
| 3  CS2 download |  |  |
| 4  MetaMod + CSS ⭐ |  |  |
| 5  Process markers |  |  |
| 6  RCON |  |  |
| 7  Workshop dl ⭐ |  |  |
| 8  Zombie recovery ⭐ |  |  |
| 9  Case mismatch |  |  |
| 10 Reachability/shutdown (opt) |  |  |

**All PASS** → the install flow is proven on real Linux; cut `v1.2.0-beta`
and move to the P2 docs pass + packaging (AppImage/.deb, GTK icon).

**Any FAIL** → note the phase, the code path from the table above, and the
relevant app-log excerpt; that's a tight repro to fix against.
