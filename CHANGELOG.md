# Oblivion Server Tool — Changelog

---

## v1.1.5 — 2026-06-28 (Reachability + Linux desktop window)

Two operator-facing additions on the v1.1 line — neither requires a
v1.2 major bump because both are non-breaking, OS-gated, or
admin-only diagnostic surfaces.

### Remote reachability (Steam master server query)

Closes the "operator's port forward silently broken" diagnostic gap —
the failure mode that's invisible to local checks because most home
routers don't support NAT hairpinning.

Approach: query Valve's existing Steam master server via the public
`ISteamApps/GetServersAtAddress` endpoint.  Zero infrastructure to
host, no API key required, and authoritative for the actual question
("can Steam clients reach my server?").

- **`cs2servergui/reachability.py`** — `check_steam_master(public_ip)`
  + `interpret(result, gslt_set, server_running, server_uptime_secs,
  expected_port)` hint engine.  Five-tier hint resolution:
  server offline → GSLT missing → uptime < 90s → wrong port →
  invisible (forward broken / CGNAT).
- **`/api/reachability/check`** — admin-gated POST endpoint; returns
  `{target, ok, servers, hints, context}`.  503 only when public IP
  not yet detected (first ~5s after launch) or Steam Web API is down.
- **Status-tab panel** with severity-coded hint, "other CS2 servers
  Valve sees at this IP" list, and a Re-check button.

Catches **GSLT-missing** automatically — a server without GSLT can't
register with Valve, so it stays invisible, and the hint surfaces
that as the actual fix.

### Linux desktop window (non-breaking)

The Windows-style in-app window now works on Linux desktop sessions
too.  Windows behaviour is identical (still uses Edge WebView2);
headless servers still auto-detect and run without a window.

- `platform.webview_gui()` — pywebview backend per OS.
  Windows: `"edgechromium"`.  Linux: `"gtk"` (WebKitGTK).
  Override via `OBLIVION_WEBVIEW_GUI=qt`.
- `platform.has_display()` — `True` on Windows; on Linux, `True` iff
  `$DISPLAY` or `$WAYLAND_DISPLAY` is set.
- `main.py` — `webview.start(gui=...)` now uses
  `platform.webview_gui()`; auto-fallback to `--headless` on Linux
  when no display is detected; pywebview `ImportError` falls through
  to `_run_headless()` (was exit-with-error).

### Tests
- +14 reachability unit + 5 endpoint integration tests.
- +4 platform tests for the webview GUI selector + display detection.
- **337 pass** on Windows; CI matrix covers Linux equivalence.

---

## v1.2.0 (in progress) — Linux operator-flow parity

Closing the operator-facing Linux gaps the v1.1 unit suite doesn't
exercise.

### Fun Mode — custom random player models with a GSLT lockout

New game mode: **Fun** — MatchZy 5v5 rules + a random cartoon character
per round for every player (via PlayerModelChanger + MultiAddonManager).

The headline is the **safety mechanism**: custom player models can get a
server's GSLT token banned by Valve, so Fun Mode never touches your real
GSLT — the ban risk can't land on the token you use for normal matches:

- **Launch-level lockout on the real token** — `config.GSLT_SUPPRESSED_MODES`
  = `{Fun}`; the launch-arg builder never emits your saved `gslt_token` in
  Fun Mode, regardless of whether one is set.  Enforced in code, not just UI.
- **Optional throwaway GSLT** — set a separate burner-account token
  (`fun_mode_gslt`) to let friends connect over the internet in Fun Mode;
  if it's ever banned, only the disposable account is affected.  Leave it
  blank and Fun Mode runs LAN/private only.
- **Belt + suspenders** — a pre-flight warning fires if PlayerModelChanger
  is installed AND a real GSLT token would be emitted on a *non*-Fun mode
  (the exact footgun).
- Fun Mode adds `-disable_workshop_command_filtering` (MAM mounts the
  model packs as workshop addons) and runs on the competitive 5v5 ruleset.
- `/api/state` exposes `fun_mode`, `gslt_suppressed`, and `fun_mode_gslt_set`;
  the SPA banner explains whether Fun Mode is public (throwaway token set)
  or LAN-only (blank).

### Fun Mode hardening after the Jul-8 CS2 patch (2026-07-10)

The Jul-8 engine patch broke three things at once; Fun Mode now ships its
own model changer and survives all of them:

- **Game events are dead on the PR#1348 CSS build** (`[GameEventHandler]`
  and explicit `RegisterEventHandler` both silently never fire), which
  killed PMC's `@random` auto-apply.  Replacement: **RandomModels v2.3**
  (`_plugins_src/RandomModels/`, net10.0), a timer-driven CSS plugin —
  0.5 s `AddTimer` assigns a random model **and ability** (Tank / Moon
  Jump / Ghost / Speedster / Neon Glow) per life; `OnTick` drives the
  movement powers.  Bundled into the `funmodels` plugin so Fun Mode
  deploys/undeploys it with the mode — no manual install.
- **Player rendering now requires AG2 animation graphs** (`.vnmgraph`);
  legacy-graph models T-pose.  The mounted pack set was re-curated by
  binary fingerprint (`worldmodel.vnmgraph` marker) + live bot tests:
  `3759230500` (25 meme cartoons) + `3759622016` (Vader/CJ/cars) +
  `3759603654` (full-body anime).  Dropped: `3163629484` (pre-AG2,
  T-poses) and `3759306902` (its `*_ag2.vmdl` files are arms-stubs —
  floating arms, no hitboxes; the author's "Updated" pack has the real
  bodies).  Pool: 67 models in `RandomModels.json`, deployed with the
  bundle.
- **MAM's server-side workshop downloader is broken** (no new pack has
  downloaded since the patch; `appworkshop_730.acf` shows no attempt) —
  packs are pre-seeded into `game/bin/win64/steamapps/workshop/content/730/`
  via steamcmd and registered in the ACF with their real manifest IDs.
- **Bot takeover crashed the server** (pawn swaps owners mid-frame; the
  plugin's OnTick touching a mid-swap pawn = native AV).  Fixed twice:
  `bot_controllable 0` baked into the funmodels cfg, and a
  pawn↔controller alignment guard in RandomModels v2.3.

Also fixed a **latent test-isolation bug** surfaced by this work: the
v0.11.17 download-guard test's teardown deleted `AppCore.is_installed`
(MRO-walk excluded the class that defines it), corrupting the property
for any later test that hit `/api/state`.  Now restores the exact
original descriptor.  **350 tests pass.**

### Fun Mode game-night hotfixes → RandomModels v2.6.2 (2026-07-10 pm)

Live findings from the first real Fun Mode session, now baked in:

- **A bad model can crash every client rendering it.** `subway_jake`
  (Subway Surfers) took out multiple people; later `among_orange` (an Among
  Us recolor) cascaded a whole 7-player lobby to zero. Both removed →
  **65**-model pool (base `among_us` + the white/green/blue recolors kept —
  only the confirmed-bad one pulled). Static reference-integrity screening
  of the rest was
  **inconclusive** — subway_jake is structurally unremarkable, and the
  crash is a render-time defect (shader/texture/mesh) invisible to a
  file-grep.  So instead of guess-culling good models, the plugin now
  **logs the model every spawn** (`[RM] {name} spawned -> model=… ability=…`),
  making any future client crash traceable: cross-reference the crash
  time against the log to name the offending model, then pull it.
- **Ability roll trimmed** (24 → 20): dropped **Giant/Tiny** (model
  scaling can crash clients on complex rigs), **Flicker** (RenderFX),
  and **BottomlessMags/Vampire** (native VData/MatchStats reads — the
  suspected source of two early `accessviolation` server crashes).  Also
  dropped `weapon_shield` from the Loot Box pool.
- **PlayerModelChanger retired.** The legacy PMC plugin was a *manual*
  install (not app-managed), so it loaded in **every** mode and spilled
  custom models into K4-Arenas.  RandomModels fully replaces it, so PMC
  is disabled — mode-switching is now genuinely clean.  (The app-managed
  funmodels teardown was already correct: RandomModels + the MAM mount
  are torn out on mode switch.)

### Dependency maintenance (post-game-night)

- **MultiAddonManager 1.5.1 → 1.5.3** in the funmodels bundle. 1.5.2 fixed a
  Windows crash; 1.5.3 added a custom cfg parser to bypass the Season-5 ConVar
  whitelist. Added the new `mm_addon_connection_timeout` convar (60s) to the
  mount cfg. Does **not** fix the broken workshop downloader — the pre-seed +
  ACF-registration workaround stays.
- Note (not in-repo): the live server's **CounterStrikeSharp** was moved off the
  broken PR#1348 draft build onto stable **v1.0.371** (the finished Season-5 fix)
  — see the CSS-upgrade staging notes. The timer-driven RandomModels architecture
  still works either way.

The three hard blockers that made a Linux operator's first real
tournament fail outside the unit tests are fixed:

- **DepotDownloader Linux** — `platform.depotdownloader_filename()`
  (`DepotDownloader` vs `.exe`) + `depotdownloader_asset_os()`
  (`linux` vs `windows`).  `config.DEPOTDL_PATH` and the GitHub
  release-asset picker now target the Linux self-contained bundle
  (`DepotDownloader-linux-x64.zip`), and the extracted ELF is
  `chmod +x`'d.  **Workshop map downloads now work on Linux.**
- **Executable bit on zip extract** — `platform.make_executable()`
  (chmod +x, no-op on Windows) applied to the DepotDownloader binary;
  `registry_client._safe_extract_zip()` now re-applies the Unix mode
  bits stored in each entry's `external_attr`, so an executable inside
  a `.zip` (CSS-with-runtime Linux bundle) lands runnable instead of
  0644.
- **Linux zombie cleanup** — `main._OUR_PROCESS_NAMES` sourced from
  `platform.own_process_names()` (adds `python`/`python3`/onefile
  binary), and the killer uses `platform.kill_pid()` (SIGKILL on
  Linux) instead of a hardcoded `taskkill`.  Port-collision recovery
  works on Linux.

+7 tests.  **344 pass on Windows AND native Linux.**

### steamcmd Linux bootstrap (v1.2.0-alpha3)

The last hardcoded-Windows hole in the install flow.  `core`'s Step-1
bootstrap fetched `steamcmd.zip` and unzipped it unconditionally — on
Linux that has no valid download, and even mocked, a `.zip` can't carry
the executable bit `steamcmd.sh` needs.

- **`platform.steamcmd_download_url()`** — `steamcmd.zip` on Windows,
  `steamcmd_linux.tar.gz` on Linux (the canonical steamcdn URLs).
- **`core._extract_steamcmd_archive()`** — dispatches on the archive
  suffix: `.zip` → `zipfile`, `.tar.gz` → `tarfile`, which preserves
  Unix mode so `steamcmd.sh` and its `linux32/steamcmd` loader land
  executable.  Tar members are sanitised against path traversal via the
  stdlib `data` filter where the running Python supports it (3.11.4+ /
  3.12+), falling back to a plain extractall on older stdlib.
- Belt-and-suspenders `platform.make_executable(STEAMCMD_PATH)` after
  extraction (no-op on Windows).  **The CS2 server install now
  bootstraps steamcmd on Linux.**

Windows behaviour is byte-identical (same URL, same zip path, chmod is a
no-op).  +3 tests.

### Documentation (v1.2)

- **[LINUX_SMOKE.md](LINUX_SMOKE.md)** — a 10-phase manual smoke checklist
  exercising the real-binary Linux flows the unit suite can't reach
  (steamcmd bootstrap, CS2 download, MetaMod + CSS extraction, process
  markers, RCON, DepotDownloader workshop maps, zombie recovery), each
  phase tagged with the code path + commit it validates.  Covers both the
  Docker image and a bare-metal Ubuntu 22.04 host.
- **README** — corrected the stale "known Linux gaps" block (all three
  workflows are now fixed) and added Linux `cloudflared` install +
  detached quick-tunnel instructions to Off-LAN access.
- **TROUBLESHOOTING** — new "Linux: install & runtime gotchas" section
  (permission-denied on binaries, missing i386 libs, `ss`/iproute2,
  case-sensitivity, headless panel reachability) + a Linux note on the
  tunnel-URL entry.

### Linux packaging — AppImage + `.deb` (v1.2)

Recipe drafted under [`packaging/linux/`](packaging/linux/); both artifacts
wrap one PyInstaller onefile (`oblivion-server-tool`, headless):

- **onefile spec** (`oblivion-server-tool.spec`) — Linux/headless build:
  SecretService keyring backend, pywebview excluded (the GTK window needs
  un-freezable system WebKitGTK), console binary.
- **`.deb`** via nfpm (`nfpm.yaml` + maintainer scripts) — installs the
  binary to `/usr/bin`, a systemd unit, and an `oblivion` service user with
  config in `/var/lib/oblivion-server-tool`; `apt purge` keeps `/srv/cs2`.
- **AppImage** (`AppRun` + `.desktop` + appimagetool) — one portable file
  for non-Debian distros.
- **`build.sh`** orchestrates all three; **`.github/workflows/release-linux.yml`**
  builds + attaches them to every `v*.*.*` release alongside the Docker image.

**Build-verified in `v1.2.0-alpha4`:** the release workflow built both
artifacts clean on ubuntu-22.04 in ~80 s and attached them to the
prerelease — `oblivion-server-tool_1.2.0-alpha4_amd64.deb` (76 MB) +
`Oblivion_Server_Tool-1.2.0-alpha4-x86_64.AppImage` (77 MB).

### Remaining v1.2 work

| Priority | Item                                                                    |
|----------|-------------------------------------------------------------------------|
| **P1**   | Linux process-marker verification (manual smoke against real CS2)        |
| **P3**   | `.png` icon for GTK window                                               |

The steamcmd Linux bootstrap, the docs pass, and the AppImage/`.deb`
packaging are all done — packaging build-verified in the `v1.2.0-alpha4`
CI run.  v1.2.0 final now gates only on the manual CS2 smoke against a real
Ubuntu install ([LINUX_SMOKE.md](LINUX_SMOKE.md)); the `.png` GTK icon is
cosmetic P3.

---

## v1.1.0 — 2026-06-26 (Linux + headless)

First multi-OS release.  Windows still ships as the single `.exe` with
the Edge WebView2 desktop window; Linux operators get headless via
Docker (`ghcr.io/oblivion-systems/oblivion-server-tool:1.1.0`) or a
systemd unit, administered through the same web panel.

Tests: **314 pass on Windows AND native Linux** (was 306 at v1.0.1),
and CI runs the matrix on every push.

### Phases that landed under v1.1.0

| Phase   | Tag                | Slice                                  |
|---------|--------------------|----------------------------------------|
| A       | `v1.1.0-alpha1`    | Headless mode (`--headless` skips pywebview) |
| B       | `v1.1.0-alpha2`    | OS abstraction layer (`cs2servergui/platform.py`) |
| C       | `v1.1.0-alpha3`    | Linux runtime paths (`linuxsteamrt64`, `cs2` binary, `steamcmd.sh`) |
| D       | `v1.1.0-alpha4`    | Docker packaging (Dockerfile, compose, requirements-headless) |
| Polish  | `v1.1.0-alpha5`    | CI matrix, GHCR publish, systemd unit, MetaMod tar.gz, case-mismatch hint, keyring docs |

### What Linux operators get

- **Docker**: `docker compose up -d` against the published image.
- **systemd**: bare-metal install at `/opt/oblivion-server-tool` with
  hardening defaults — see [packaging/systemd/README.md](packaging/systemd/README.md).
- **Auto-install of MetaMod + CSS** picks the `.tar.gz` / `linux` artifact
  variants automatically.  No manual extraction.
- **Pre-flight diagnostics** call out case-mismatched CS2 install paths
  (`expected 'X' but found 'x' in /srv/cs2`) instead of just
  "CS2 is not installed".
- **Keyring fallback** to plaintext `oblivion_config.json` is documented
  and intentional on headless boxes — diagnostic snapshot still redacts.

### Windows operators

No behaviour change — same `.exe`, same installer, same WebView2 window.
The OS abstraction is a no-op on Windows.

---

## v1.1.0-alpha5 — 2026-06-26 (Linux packaging polish)

Closes the Linux-side gaps from Phases A-D so Linux operators reach
Windows parity where it matters.

### Added
- **CI matrix** ([`.github/workflows/test.yml`](.github/workflows/test.yml))
  runs pytest on `ubuntu-latest` + `windows-latest` × Python 3.11/3.12
  on every push and PR to master.  First time `platform.py`'s Linux
  branches execute against a real Ubuntu kernel.
- **Docker image publish** ([`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml))
  builds and pushes to `ghcr.io/oblivion-systems/oblivion-server-tool`
  on every `v*.*.*` tag.  Tags: full version, `MAJOR.MINOR`, and
  `latest` (skipped for pre-releases).
- **systemd unit** + install README at
  [`packaging/systemd/`](packaging/systemd/) — dedicated `oblivion` user,
  `/opt/oblivion-server-tool` install location, `/srv/cs2` CS2 dir,
  hardening defaults (`ProtectSystem=strict`, `ProtectHome=read-only`,
  `NoNewPrivileges`).
- **README Linux quickstart** — `docker compose up -d` recipe + bare-metal
  systemd one-liner sequence.

### Changed
- `platform.metamod_download_url()` / `css_download_url()` — return the
  per-OS archive URL.  MetaMod ships `.tar.gz` on Linux; CSS ships
  `.zip` with `-linux-` in the filename.
- `config.RUNTIME_METAMOD_DEFAULT_URL` / `RUNTIME_CSS_DEFAULT_URL` —
  delegate to the platform helpers instead of hardcoded Windows literals.
- `registry_client._safe_extract_archive(data, dest_dir, url)` —
  dispatches by URL suffix between `_safe_extract_zip()` and the new
  `_safe_extract_targz()`.  Same path-traversal / absolute-path
  protection in both extractors; tar entries that are symlinks or
  non-regular files are skipped.
- `platform.case_mismatch_hint(path)` — Linux-only diagnostic; the
  pre-flight uses it when `CS2_PATH` is missing so a wrong-case install
  path surfaces "expected 'X' but found 'x' in /srv/cs2" instead of
  the generic "CS2 is not installed" error.
- `TROUBLESHOOTING.md` — new "Linux: secret storage on headless servers"
  section explaining the keyring → plaintext fallback (no D-Bus = no
  Secret Service = `oblivion_config.json` holds secrets), why it's
  intentional, and how to keep the file safe.

### Tests
- +5 driver/platform tests: per-OS URL pickers, config-tracks-platform
  invariant, case-mismatch hint behaviour (existing path, no sibling,
  case-different sibling).
- +2 runtime install tests: tar.gz happy-path on Linux MetaMod fixture,
  tar.gz traversal rejection.
- Existing v0.16.5 runtime tests now monkey-patch `_resolve_runtime_url`
  so the zip-payload tests work regardless of host OS (Linux default
  is `.tar.gz`).

---

## v1.1.0-alpha4 — 2026-06-23 (Docker packaging — Phase D)

Fourth slice of the v1.1 roadmap.  Ships a self-contained Docker image
so Linux operators get the panel running with `docker compose up -d`,
no manual SteamCMD/CS2 wrangling on the host.

### Added
- **`Dockerfile`** — Ubuntu 22.04 base with i386 SteamCMD/CS2 deps
  (`lib32gcc-s1`, `libstdc++6:i386`), `iproute2` for `ss` listener
  parsing, `python3` + `pip`.  `XDG_CONFIG_HOME=/config` aligns with
  `platform.app_data_dir()` on Linux; `/config` and `/srv/cs2` are
  declared volumes.  Healthcheck hits `/api/state` on port 5050.
- **`docker-compose.yml`** — exposes `5050` (web panel), `27015/tcp`
  (RCON), `27015/udp` + `27016/udp` (CS2 game traffic); named volumes
  `cs2_data` and `oblivion_config`.
- **`requirements-headless.txt`** — Flask, segno, discord.py, keyring.
  `pywebview` intentionally excluded; the `--headless` mode (Phase A)
  skips the desktop window so no pywebview import runs.
- **`.dockerignore`** — excludes `__pycache__/`, `dist/`, `build/`,
  `Marketing/`, `*.spec`, `installer.iss`, `scripts/` from build context.

### Fixed
- Initial Dockerfile used `EXPOSE 5000` / healthcheck on port 5000;
  smoke test caught Flask actually listening on `0.0.0.0:5050` (the
  config default).  Corrected `EXPOSE` + healthcheck + compose port
  mappings to 5050.

### Smoke-tested
- Build succeeds on Docker Desktop (Windows host).
- Container starts; `GET /api/ping` returns
  `{"build":"dev","ok":true,"version":"1.1.0-alpha1"}` from the Linux
  container.

---

## v1.1.0-alpha3 — 2026-06-23 (Linux runtime paths — Phase C)

Third slice of the v1.1 roadmap.  Wires the OS abstraction from Phase B
into every place that constructs a path or identifies a process, so the
app correctly targets the Linux CS2 dedicated-server binary and MetaMod
layout out of the box.

### Changed
- **`platform.py`** — four new helpers: `server_binary_rel_path()`,
  `steamcmd_filename()`, `metamod_bin_arch()`, `server_process_name()`.
  Each returns the Windows value today; the Linux branch is live for
  when Phase D (packaging) runs on an actual Linux host.
- **`config.py`** — `CS2_PATH` and `STEAMCMD_PATH` now built via
  `platform.server_binary_rel_path()` / `platform.steamcmd_filename()`
  instead of hardcoded `win64/cs2.exe` / `steamcmd.exe`.
- **`drivers/cs2/driver.py`** — `process_image_name` is now a
  `@property` that calls `platform.server_process_name()` (`cs2.exe`
  on Windows, `cs2` on Linux).
- **`core.py` `_fix_metamod_dll_nesting()`** — arch folder (`win64` /
  `linuxsteamrt64`) resolved via `platform.metamod_bin_arch()`;
  extension filter switches between `.dll` and `.so` accordingly.
- **`core.py` `_preflight_checks()`** — port-27015 conflict check now
  uses `platform.list_pids()` instead of a Windows-only `tasklist`
  subprocess to determine if the holder is CS2 or a foreign process.

### Tests
- `tests/test_drivers.py` — 6 new platform Phase C cases; existing
  `process_image_name` assertion updated to use `server_process_name()`.

### Not yet
- No Linux packaging (Phase D — AppImage / Docker / systemd service).
- No Linux smoke test (blocked on first Linux host).

---

## v1.1.0-alpha2 — 2026-06-23 (OS abstraction layer — Phase B)

Second slice of the v1.1 roadmap.  Extracts every OS-specific call into
`cs2servergui/platform.py`.  Windows paths are unchanged and
battle-tested; the Linux paths are in place so Phase C only adds game
paths and Phase D only adds packaging.

### Added
- **`cs2servergui/platform.py`** (new) — Windows + Linux implementations
  for: `app_data_dir()`, `no_window_flags()`, `new_console_flags()`,
  `list_pids()`, `kill_pid()`, `process_running()`,
  `listeners_on_port()` (with `ss` + `/proc/net/tcp` fallback).

### Changed
- **`cs2servergui/_netutils.py`** — `listeners_on_port()` and
  `listener_of_port()` delegate to `platform.listeners_on_port()`;
  removed the duplicate Windows implementation.
- **`cs2servergui/core.py`** — all `taskkill`, PowerShell, and `wmic`
  calls replaced with `platform.kill_pid()` / `platform.list_pids()`.
- **`cs2servergui/config.py`** — `_APP_DIR` (frozen path) uses
  `platform.app_data_dir()` instead of raw `%APPDATA%`.

---

## v1.1.0-alpha1 — 2026-06-21 (Headless mode — Linux foundation, Phase A)

First slice of the v1.1 roadmap.  Adds a `--headless` CLI flag that
runs the app without the embedded pywebview window — the web panel
becomes the only UI.  Foundation for Linux support (Phase C); has no
effect on the default desktop experience.

### Added
- **`--headless` flag** in [main.py](main.py).  Skips pywebview entirely, holds
  the Flask process open via a `threading.Event` blocking on
  SIGINT/SIGTERM, calls `core.save_config()` on graceful exit, then
  `os._exit(0)`.
- **`_run_headless(core, port, flask_thread)`** helper — mirrors the
  desktop path's shutdown contract so an in-flight config write isn't
  truncated when an operator sends Ctrl+C.
- **Startup banner** — prints the local + LAN URLs to stdout so an
  operator running over SSH knows where to point a browser.
- **Startup-token gate** — skipped in headless mode (no desktop window
  to consume the auto-auth URL, so PIN auth is mandatory; `/auth/auto`
  already rejects when `startup_token` is empty, so no other code path
  breaks).

### Not yet
- No Linux-specific process management (Phase B — platform.py seam).
- No Linux srcds binary or path layout (Phase C — Linux runtime).
- No AppImage / Docker packaging (Phase D).
- No second `.exe` build flavour with `console=True` for stdout
  visibility on Windows — for alpha, headless users on Windows should
  run `python main.py --headless` from source.

### Tested on
- Windows 11 — `python main.py --headless` boots, serves the web panel,
  Ctrl+C exits cleanly with config saved.

This is a pre-release.  Default Windows desktop users should stay on
v1.0.1.

---

## v1.0.1 — 2026-06-21 (GSLT visibility — public hosting clarity)

Public-hosting failure mode that burned a real tournament evening: with
`+sv_lan 0` set but no GSLT, the server launches fine, accepts LAN
clients fine, and looks healthy on every diagnostic surface — but
Valve's auth backend silently rejects external client handshakes.  Zero
log entries on either side.

The SPA also lied about it: the Connect Popover badge read
"Public · GSLT verified" whenever `public_ip` was detected, regardless
of whether a GSLT was actually configured.

Fixes:
- **New `/api/state.gslt_set` field** — boolean, lets the SPA know the
  truth without exposing the token to remote sessions.
- **Truthful Connect Popover badge** — three states now: `GSLT set`
  (green), `GSLT MISSING — remote clients will fail` (red), or
  `detecting…` while waiting for public IP detection.
- **Pre-Start modal warning** — Status-page Start button and preset
  Start both surface a popup when GSLT is empty: explanation, link to
  Steam's GSLT page (App ID 730), and an explicit "Start anyway (LAN
  only)" escape hatch for operators who don't need public reach.
- **`_preflight_checks` log warning** — same situation surfaces in the
  log drawer too, with the GSLT registration URL inline.

Files: [web.py](cs2servergui/web.py) (+1 field), [app.js](cs2servergui/static/js/app.js) (badge + `withGsltGuard` + 2 wire-up sites),
[app.css](cs2servergui/static/css/app.css) (+1 line), [core.py](cs2servergui/core.py) (+5-line preflight warning).

---

## v0.16.15 — 2026-06-17 (Discord bot resilience hardening, #159)

Closes the last v1.0 "must" item.  When Discord wobbles mid-veto, the
operator needs to know WHY — perms revoked vs. channel deleted vs.
rate-limited vs. network blip.  v0.11.0's bot wrappers caught
everything as a generic `Exception` and logged at `info`, leaving the
operator staring at "bot_dm_user(123…) failed: …" with no hint.

New `_classify_discord_op_error(label, target, exc)` helper promotes
the four categories:
- `discord.Forbidden`     → WARNING with "check role/channel perms"
- `discord.NotFound`      → WARNING with "channel/message/user deleted"
- `discord.HTTPException` → WARNING with `.status` code (429s self-evident)
- anything else           → ERROR with full traceback

Applied to `bot_dm_user`, `bot_post_embed`, `bot_edit_embed`.  Plus
`bot_edit_embed` timeout 8s → 12s so discord.py's internal rate-limit
retry can complete on a fast-veto burst before our outer timer fires.

Tests: 300/300 green.

---

## v0.16.14 — 2026-06-17 (Spectator URL polish #170 — broadcast-grade)

Closes the last v1.0 wishlist item.  v0.11.1's `/spectate` worked for
caster checks but wasn't a broadcast-grade overlay — small fonts,
3 s polling lag, no OBS controls, no active-team highlight.

Backend
- New `/api/veto/spectator/stream` SSE endpoint, token-gated like
  `/state`, emits the SANITIZED snapshot (Discord IDs stripped,
  SteamIDs masked, captain tokens never sent).
- `_veto_spec_subs` parallel subscriber list — kept separate from the
  main `_veto_subs` so a stalled spectator stream can't backpressure
  the captain/admin stream.
- `_veto_broadcast()` now also dispatches the sanitized payload.

SPA (spectator page)
- Complete typographic rewrite: 56px team-name scoreline, 20px team
  names in roster cards, 14px sequence chips, 44px decider hero.
- Live updates via SSE primary + 5s polling fallback (corporate
  proxies that strip SSE fall through automatically).
- Active-team highlight: accent-coloured border + slow pulse on the
  team whose step is next.
- Query-param OBS controls: `?bg=transparent|green|blue|dark`,
  `?compact=1`, `?theme=light`.
- Foot text auto-hides on chroma backgrounds.
- Mobile/720p responsive: scoreline shrinks to 32px, teams stack.

Tests: 297/297 green (+3 new — HTML includes OBS controls + SSE
endpoint, SSE rejects bad token + no-session, sanitization strips
Discord IDs through both polling and streaming paths).

---

## v0.16.13 — 2026-06-17 (Warcraft plugin: 4 main-thread safety fixes)

Source-side re-audit of `D:\warcraft-build\src\` after CSS shipped v1.0.369.
Four bugs not covered by the June 1 patches.  Source edits applied,
WarcraftPlugin.dll rebuilt (net8.0/Release), bundle DLL refreshed.

- **Critical: `_commandCooldowns` Dictionary corruption** — plain
  `Dictionary<>` accessed from chat-command callbacks.  10-player
  tournament smashing `!skills`/`!class`/`!shop` in the same tick → rehash
  during concurrent read → silent main-thread freeze.  Switched to
  `ConcurrentDictionary<>` (matches the `WarcraftPlayers` pattern).
- **High: `Database.ResetClients` blocked main thread** — `.GetAwaiter().GetResult()`
  on the single-worker dispatcher queued behind any pending dirty-flush.
  Multi-hundred-ms map-start hitch with dirty players.  Split into
  `ResetClientsAsync()` + `FireAndForget` at both call sites.
- **Medium: `OnClientPutInServerAsync` race on `NativeAPI.GetEntityFromIndex`**
  — entity-list lookups on a worker thread race the engine's tick-time
  updates.  All entity work moved to the main-thread handler; async path
  now takes a pre-validated controller.
- **Medium (dormant): `MenuTypeManager.GetPlayerMenuType` blocked main thread
  on MySQL** — `.GetAwaiter().GetResult()` on every first-time menu open.
  Currently dormant (MySQL menu persistence off), but a footgun.  Now
  returns default immediately + populates cache async.

Build clean, 0 errors.  Bundle DLL 499 KB → 500 KB; PDB refreshed so stack
traces from this build show line numbers.

Tests: 294/294 green (no new tests — fixes target C# code in a separate
project; verification is the next real tournament).

---

## v0.16.12 — 2026-06-15 (Demolition map list: workshop classics + official fallback)

Follow-up to v0.16.11's "use OFFICIAL_MAPS" fix.  Right point from user:
CS:GO's Demolition maps were specifically SMALL by design (fast 6v6
rounds, weapon progression).  Running full-size de_dust2 in Demolition
mode works mechanically but loses the gameplay loop.

Hybrid list — workshop ports of the CS:GO classics first (preserves
design intent for operators who've subscribed to them), official CS2
maps as fallback (cold-install operators get something that boots):

```
"Demolition": [
    "125439738",   # Shorttrain (de_shortdust port)
    "125440342",   # Bank
    "125440847",   # Sugarcane
    "125441004",   # St. Marc
    "de_dust2", "de_overpass", "de_inferno",   # fallback
]
```

---

## v0.16.11 — 2026-06-15 (Demolition default-map list: CS:GO mini-maps removed)

Caught in a v0.16.10 smoke-test snapshot: switching to Demolition mode
picked `de_bank` as default, cs2.exe rejected it with "invalid map
name", server never bound port 27015, app showed "Port 27015 not
opening" forever.

Root cause: config listed the CS:GO-era Demolition mini-maps (`de_lake`,
`de_safehouse`, `de_shortdust`, `de_stmarc`, `de_bank`, `de_sugarcane`)
as defaults — Valve dropped all six from CS2's official rotation.

Quick fix: Demolition default list = `OFFICIAL_MAPS`.  Superseded by
v0.16.12's hybrid approach.

---

## v0.16.10 — 2026-06-15 (Pack + Template Apply buttons → accent CTAs)

User feedback after seeing the v0.16.9 rebuild: still "not great" — the
plugin pack APPLY button blended in with the tag chips because it was
using the SECONDARY outline style (`.btn`) when it should have been the
PRIMARY card CTA.

- Pack Apply: `.btn .btn-sm` → `.btn .btn-accent .btn-full`,
  copy "Apply" → "▶ Apply this pack"
- Template Apply: same accent treatment at sm-size
- Template Delete stays ghost (correctly secondary/destructive)

---

## v0.16.9 — 2026-06-15 (Widen visual gap between enabled and disabled buttons)

User flagged: some buttons unhovered looked greyed-out, ambiguous about
clickability.  Screenshot showed the pack Apply button reading as
disabled when actually fully active.

Root cause: `.btn` default state used `text-2` (dim) + `line-1` border,
and `:disabled` was only opacity 0.35.  Visual gap too narrow.

- `.btn` text-2 → text-1, line-1 → line-2, + `cursor:pointer`
- `.btn-ghost` text-3 → text-2, line-1 → line-2
- `.btn:disabled` opacity 0.35 → 0.28, + `grayscale(0.6)` + `cursor:not-allowed`

Pack-button-specific follow-up landed in v0.16.10.

---

## v0.16.8 — 2026-06-15 (Adversarial-review fixes)

A self-review pass on the v0.16.5–v0.16.7 first-run polish caught 5
real bugs.  All 5 fixed and shipped together.

### #1 — Critical: wrong method name (silent MetaMod failure)
`/api/plugins/install_runtime` called `core._gameinfo_patch_metamod()`,
which doesn't exist — the actual method is `core._patch_gameinfo`.  The
`AttributeError` was swallowed by `except Exception`, and the JSON
response still claimed `ok: true` with `metamod_installed: true`.  The
friend would have clicked Install, seen the green pill, started the
server, and watched MetaMod silently fail to load — because gameinfo.gi
was never patched.

Fixed: method name corrected, plus a post-install assertion that
`gameinfo_has_metamod()` is True before reporting success — when it's
not, the response now says `ok: false` with a clear warning so the SPA
can show yellow not green.

### #2 — High: build.bat didn't actually fetch WebView2 bootstrapper
The v0.16.5 installer comment claimed "build.bat downloads the
bootstrapper" but build.bat had no such step.  The obvious dev workflow
(`build.bat → ISCC installer.iss`) would ship an installer that skipped
WebView2 entirely, defeating the whole point of item A for clean Win10.

Fixed: new step `[1.6/3] Fetching WebView2 bootstrapper for installer
bundling` invokes `tools\fetch_webview2.ps1` before ISCC.  If the fetch
fails, a WARN is printed and the build continues — the operator can
still ship without WebView2 if they want.

### #3 — Medium: fetch_webview2.ps1 had no size validation
A truncated/0-byte download (CDN edge drop, network hiccup) was
accepted as valid; `Test-Path` returns true on a 0-byte file.  The
installer would have bundled a corrupt exe; install-time would fail
with `ERROR_BAD_EXE_FORMAT` and surface a mid-install error dialog.

Fixed: `MIN_SIZE_BYTES = 1MB` guard rejects any file under 1 MB (real
bootstrapper is ~1.6 MB).  Plus `-MaximumRedirection 5` and
`-TimeoutSec 60` on the `Invoke-WebRequest`.

### #4 — Medium: double-click race on Install button
Two rapid clicks on "📥 Install MetaMod" could both reach
`_runtimeInstall` before `btn.disabled = true` blocked re-entry (the
second click event was already dispatched before the first handler
ran).  Two concurrent backend calls each `tempfile.mkdtemp` and merge
into `csgo/addons/<x>/` simultaneously via `shutil.copy2`, racing on
the same dst paths and leaving a half-merged install.

Fixed:
- SPA: module-level `window._oblivionRuntimeInflight` Set; second
  click sees the flag and bails with a toast.
- Backend: per-component `threading.Lock` (`_runtime_lock_for`) in
  `registry_client`; a second `install_runtime` call returns
  `RegistryError("install of X already in progress")` rather than
  racing on the filesystem.

### #5 — Low: pre-install backup polluted ring on failure
`backup_config(reason="pre-runtime-X")` was called BEFORE the install.
If install failed for any reason (network error, bad zip, csgo not
writable), the backup still landed and consumed a slot in the 10-slot
ring.  Friend on shaky wifi clicking Install five times would have
evicted the operator's real pre-deploy snapshots.

Fixed: backup moved to AFTER `install_runtime` succeeds; reason
renamed `post-runtime-X` to match the new ordering.

### Tests
- 294 / 294 green (no new tests — fixes target existing behaviour).

---

## v0.16.7 — 2026-06-15 (Hotfix: refresh hardcoded MetaMod + CSS URLs)

The MetaMod URL pinned in v0.16.5 (`mmsource-2.0.0-git1331-windows.zip`)
and the CSS URL (`v378/counterstrikesharp-with-runtime-build-378-...`)
both returned 404 against today's mms.alliedmods.net + GitHub releases.
Friend clicking "Install MetaMod" would have hit a dead link.

### Fix
- `RUNTIME_METAMOD_DEFAULT_URL` updated to
  `mmsource-2.0.0-git1402-windows.zip` (HEAD 200, 6.5 MB; layout
  verified — addons/metamod/ at root).
- `RUNTIME_CSS_DEFAULT_URL` updated to
  `v1.0.369/counterstrikesharp-with-runtime-windows-1.0.369.zip`
  (HEAD 302 redirect, 49.5 MB compressed; layout verified — both
  addons/counterstrikesharp/ AND addons/metamod/counterstrikesharp.vdf
  at root, which is what makes MetaMod load CSS).

### Note
Both URLs go stale eventually as upstream cuts new builds.  Operators
can override via `metamod_download_url` / `css_download_url` in
`oblivion_config.json` without waiting for an app update — that fallback
shipped in v0.16.5.

### Tests
- 294 / 294 green.

---

## v0.16.6 — 2026-06-15 (First-run UX: Getting Started card + Discord one-button check + actionable Pre-flight)

Three coordinated SPA changes that surface the things a brand-new
operator actually needs to do, where they need to do them.  Triggered
by the v1.0 first-run audit (task #157) and the upcoming friend-hands-on
test.

### Getting Started card — Status page (item D)
- New card auto-renders at the top of Status when readiness shows
  csgo / runtime / plugins aren't all green yet.  Three actionable
  rows (install CS2 / install runtime / pick a pack) each with a
  button that navigates straight to the right tab.  Progress bar at
  top — visual proof of forward motion.
- Auto-hides when all three checks pass (returning operators see no
  card).  Manual dismiss for the session via the X button — refreshing
  the app brings it back, so an accidental dismiss isn't permanent.
- "Open full Pre-flight check →" link routes to the existing readiness
  page for deeper triage.

### Discord Connection check restructured (item E)
- "🎲 Run mock veto" (v0.16.3) promoted to the **primary** action with
  clearer copy: "🩺 Run Discord setup check — bot will post an embed,
  edit it 3×, then leave a 🟢 test-complete embed (safe to delete)".
  Accent-coloured, full-width button.
- Per-feature tests (test embed / test DM) moved into a `<details>`
  "Advanced: individual feature tests" expander — useful for triaging,
  but the primary verification path is now one click.
- Sub-copy explains what the smoke test actually does so a friend
  understands the friendly side-effect (an embed in their channel)
  before clicking.

### Pre-flight actionable "→ Fix" buttons (item F)
- Each readiness row now renders a per-key "Open Config → Install" /
  "Open Plugins → Set up runtime" / "Open Plugins → Pick a pack"
  button when the check is fail/warn.  Maps 9 of the 10 checks to a
  target tab (disk-low has no in-app fix).
- Clicking the button navigates to the relevant tab — the operator
  doesn't have to remember which tab a given problem lives on.

### Tests
- 294 / 294 green (no new tests — SPA-only).

---

## v0.16.5 — 2026-06-15 (Auto-install MetaMod + CSS runtime + WebView2 installer bundle)

Two changes that close the heaviest fresh-install friction points.

### Auto-install MetaMod + CounterStrikeSharp (task #163)
Tournament-mode plugins (MatchZy, Warcraft, Retakes) all need MetaMod +
CSS in csgo/.  Before this slice, the operator downloaded two zips,
found the addons/ folder inside each, and drag-extracted them by hand
without misnesting bin/win64/.  Now: one click per runtime in the
Plugin Runtime modal.

- `registry_client.install_runtime(component, csgo_dir)` reuses the
  registry's safe-download primitives — size cap (250 MB for CSS),
  HTTPS guard, Zip Slip protection — and merges the extracted addons/
  tree into csgo/ via per-file copy (preserves operator's other csgo/
  contents).  Rejects zips that don't contain the expected
  `addons/<component>/` layout BEFORE writing anything.
- Hardcoded URL defaults in config.py for a known-stable build of each;
  operators can override via `metamod_download_url` / `css_download_url`
  in `oblivion_config.json` when a newer build lands.
- New `POST /api/plugins/install_runtime {component}` endpoint
  (@require_local) — invokes install_runtime, runs MetaMod's existing
  bin/win64/win64/ nesting-fix + gameinfo.gi patcher when component is
  metamod, returns updated runtime status so the SPA can flip the pill
  to green immediately.
- Pre-action config backup fires before install (same discipline as
  registry / URL install paths).
- **SPA modal** — manual extraction instructions replaced with a primary
  "📥 Install" button per component.  Lifecycle: idle → "Downloading…"
  → "Installed" with file count + dest dir.  Manual fallback kept under
  a `<details>` expander.

### WebView2 installer bundle (item A)
- `installer.iss` now uses `#if FileExists("MicrosoftEdgeWebview2Setup.exe")`
  to conditionally bundle the ~2 MB bootstrapper at compile time.  The
  [Files] entry drops it to {tmp}, the [Run] entry invokes it with
  `/silent /install` and waits for completion before launching the app.
  Friends on Windows 10 (no preinstalled WebView2) no longer get a
  blank window on first launch.
- New `tools/fetch_webview2.ps1` downloads the bootstrapper from
  Microsoft's Evergreen URL.  Idempotent — exits early if already
  present.  Run before each installer build, or wire into a CI step.
- The bootstrapper exe stays out of git (`.gitignore` += `MicrosoftEdgeWebview2Setup.exe`).

### Tests
- 294 / 294 green (+7 new — @require_local, unknown component, URL
  override precedence, bad-zip rejection, happy-path extraction, csgo
  precondition).

---

## v0.16.4 — 2026-06-13 (Hotfix: force RCON to bind on all interfaces)

Caught from a live session: on a host with Hyper-V or WSL installed,
cs2.exe was binding its RCON TCP socket exclusively to the virtual
`vEthernet` adapter (e.g. `172.19.160.1:27015`) instead of the real
LAN IP.  Game traffic (UDP) still worked because UDP binds `0.0.0.0`
by default — but every RCON request from the app failed with WinError
10061 "connection refused", which then cascaded into "Port 27015 not
opening" warnings and the 90s optimistic-online fallback.

### Fix
- Launch args in [core.py:1504](cs2servergui/core.py:1504) now include
  `+ip 0.0.0.0`.  This binds RCON's TCP listener to ALL interfaces
  (loopback, real LAN, and vEthernet alike) so Source 2's "first
  interface Windows resolves as primary" heuristic can't trap the
  socket on a virtual NIC.
- Strictly more permissive than the previous behaviour — never worse
  for users without Hyper-V / WSL.

### Tests
- 287 / 287 green.

---

## v0.16.3 — 2026-06-12 (Tournament templates + demo browser + Discord mock-veto)

Wave 4 of the v1.0 wishlist. Three independent operator wins.

### Tournament templates (task #169)
- New `cs2servergui/template_store.py` with allowlist-filtered CRUD —
  `mode` / `map` / `pack_id` / `discord_*` / `team_a_id` / `team_b_id` /
  `description` survive the round trip; anything else is dropped at save
  time (defence against XSS / config-injection if the JSON is ever shared).
- `oblivion_templates.json` lives next to the existing teams file in
  `%APPDATA%/Oblivion Server Tool/`. Atomic writes via tempfile + os.replace,
  stable UUIDs across updates.
- Four endpoints — `GET /api/templates` (@require_auth), `POST
  /api/templates/save|delete|apply` (all @require_local). Apply pushes the
  template's payload into config with a backup beforehand.
- **Plugins tab: Templates strip + manager modal.** One click to save the
  current setup as a named template, one click on a chip to restore it.
  Modal lets the operator rename, edit, or delete.

### Demo browser (task #171)
- New `GET /api/demos` walks four well-known roots — `csgo/`,
  MatchZy demo dir, CSS demo output, MatchZy cfg dir — and returns every
  `.dem` it finds with size + mtime + relative label.
- `GET /api/demos/download?path=<label>/<rel>` streams the file with
  three-layer path safety: known-label allowlist, `.dem`-only extension
  check, and `commonpath` realpath check to reject `../` traversal.
- **History page: Demos card** showing the most-recent 50 demos with size,
  date, and one-click download. Empty state explains the four locations
  scanned.

### Discord mock-veto smoke button (task #165)
- New `POST /api/discord/mock_veto` posts a real embed to the configured
  veto channel, simulates the full Ban → Pick → Side → Move flow with
  reaction payloads, edits the embed at each step, then leaves a
  "🟢 Smoke test complete" final embed (operator can delete it). Refuses
  cleanly when the bot isn't connected (503) or the channel isn't
  configured (400) — no stack trace.
- **Discord Config card: 🧪 Mock-veto smoke test button** with per-step
  output (`✓ Initial embed posted`, `✓ Ban 1 reacted`, etc.) so the
  operator can verify their Discord setup without spinning up a real veto.

### SPA quality-of-life
- History page now bundles search + demo browser, no longer a stub.
- Setup wizard Step 3 unchanged (still the 3-step ordered guidance shipped
  in v0.16.0) — first-run UX audit (#157) is intentionally deferred until
  the last v1.0 wave so the surface area to audit is stable.

### Tests
- 287 / 287 green (5 new — template CRUD + allowlist, templates auth
  matrix, demos shape, demos path-traversal rejection, mock-veto error
  cases).

---

## v0.16.2 — 2026-06-12 (History/Pre-flight/Logs SPA pages + readiness audit)

Wave 3 of the v1.0 wishlist — three new SPA pages.

- **History page** with search + demo browser sub-section (placeholder for
  Wave 4 demo card).
- **Pre-flight page** (`/api/readiness`) — 10 audited checks across
  config, plugins, Discord, server install, and PIN security. Each check
  emits ok / warn / fail / info with a one-line explanation. Catches
  things like "admin_pin=1234" (warn — default PIN), "Discord bot token
  missing", "deploy never run", "active pack files missing".
- **Logs page** with search + source filter + auto-refresh + download.

### Tests
- 282 / 282 green (2 new — readiness shape, PIN default warning).

---

## v0.16.1 — 2026-06-12 (Persistent team profiles)

Wave 2 of the v1.0 wishlist — task #160 only.

- New `cs2servergui/team_profiles.py` — CRUD over `oblivion_teams.json`.
  Each team has a UUID, name, tag, and a list of `{discord_id, ign}`
  players. `discord_id` validated as digit-only string. Atomic writes via
  tempfile + os.replace.
- Three endpoints — `GET /api/teams` (@require_auth), `POST
  /api/teams/save|delete` (@require_local).
- **Veto roster stage: 📋 Team Profiles modal.** Save the current roster
  as a named team or restore a saved team into the roster slots.

### Tests
- 280 / 280 green (2 new — team CRUD + auth matrix).

---

## v0.16.0 — 2026-06-12 (Config backup/restore + plugin docs + first-run polish)

Wave 1 of the v1.0 wishlist — tasks #164, #158, #166, #167.

- **Auto config backups** (#158): `core.backup_config(reason)` writes a
  timestamped snapshot of `oblivion_config.json` into
  `%APPDATA%/.../backups/`. Auto-fires before plugin install / uninstall /
  pack apply. `list_config_backups()` keeps the last 10;
  `restore_config_backup(filename)` copies back. Three endpoints —
  `POST /api/config/backup`, `GET /api/config/backups`, `POST
  /api/config/restore` — all `@require_local`.
- **Setup wizard Step 3 rewrite** (#157 partial): replaced the wall of
  text with a 3-step ordered guidance ("1. Deploy 2. Connect 3. First
  match") so a first-time operator knows what to do after the wizard
  closes.
- **TROUBLESHOOTING.md: Security section** (#166): PIN auth + remote
  exposure threat table — what's protected, what isn't, how to recover
  from a leaked PIN.
- **`zombie` plugin: copy missing `.example` files into active
  counterparts** (#167) — admins.jsonc, discordbots.cfg, maplist.jsonc.
  Stops the "plugin loads but never starts" silent failure on first run.

### Tests
- 278 / 278 green.

---

## v0.15.2 — 2026-06-12 (Plugin Manager slice 3: uninstall + reload + URL install + updates + search)

Closes the "easy for anyone to add new plugins" thread. Five Plugin-tab
completeness wins, gated by the same safety patterns from v0.15.1.
Reviewed via 5-dimension adversarial workflow (security / correctness /
UX-edges / test-gaps / integration) before commit; 2 confirmed findings
folded in.

### Endpoints (all `@require_local`)
- **`POST /api/plugins/uninstall {slug}`** — `rmtree`s `%APPDATA%/.../plugins/<slug>/`
  and re-runs discovery. Refuses bundled plugins (live in the .exe).
  Refuses when the slug is bound to the active mode (returns 409 — protects
  the operator from booting into a mode whose plugins were just deleted).
- **`POST /api/plugins/reload`** — re-runs `_discover_plugins` +
  `_populate_plugin_tables` in place. Operator can drop a folder into
  `%APPDATA%/.../plugins/` and pick it up without an app restart.
- **`POST /api/plugins/install_from_url {url, sha256?, expected_slug?}`** —
  download a zip from any URL. Same safety pipeline as
  `install_from_registry`. Plain `http://` rejected except for localhost.
  Registry URL itself is refused — the dedicated endpoint must be used.

### Version + update notifications
- `plugin.json` gains an optional `version` field.
- `/api/plugins` entries now carry `installed_version` + `latest_version` +
  `update_available`. `has_update` is conservative — returns False unless
  available > installed can be proven. Garbage strings parse to `(0,0,0)`;
  prerelease ordering correct (`1.0.0-beta < 1.0.0`).
- SPA Library shows orange **Update v…** pill. Registry grid surfaces
  installed-but-updatable entries with button copy flipped Install→Update.

### SPA polish
- Single search input filters BOTH the Library and Registry grids by
  display_name / summary / author / slug. Survives re-renders.
- **↻ Reload** button next to the search input.
- **📥 Install from URL** modal with URL + SHA-256 + expected_slug inputs.
  Live-warns when SHA-256 is missing/malformed. Confirm required for
  unverified installs.
- **Remove** button on Local-source library cards. Bundled cards have no
  button (can't remove from disk).

### Adversarial review fixes
- **Medium**: registry card's Install button correctly flipped to Update
  but the click handler's confirm dialog hardcoded Install copy. Fixed via
  `data-action` attribute + conditional confirm + toast text.
- **High**: end-to-end wiring of `web.py`'s `reg_index` + `has_update` arg
  order + JSON propagation had zero coverage. Added integration test that
  catches a flipped `has_update(installed, latest)` regression.

**276/276 tests green** (+12 new).

---

## v0.15.1 — 2026-06-11 (Plugin Manager slice 2: OblivionPluginRegistry remote fetch + in-SPA install)

App-side machinery for community plugin discovery + one-click install.

### New module `cs2servergui/registry_client.py`
- `fetch_catalog(force=False)` — 24h TTL, atomic cache write
  (`%APPDATA%/.../registry_cache.json`), graceful fallback to stale cache
  on network failure, graceful empty-with-`_offline` catalog when the
  registry repo doesn't exist yet.
- `install_plugin(slug, version)` — download → SHA-256 verify → safe-extract
  → atomic move into `%APPDATA%/.../plugins/<slug>/`.
- `RegistryError` — public exception class for typed 4xx mapping.

### Three new endpoints
- `GET /api/plugins/registry` — catalog + freshness + installed-slug set
- `POST /api/plugins/registry/refresh` — cache-busting re-fetch
- `POST /api/plugins/install_from_registry {slug, version?}` — runs install
  + re-discovers in-place so the new plugin shows up without an app restart

### SPA — "Available to Install (Community)" card
States handled: offline / empty registry / all-installed / cached-but-stale
/ one-or-more-available. Each card shows version pill + summary + modes +
author + Install button. Header has ↻ Refresh + "refreshed Nh ago" label.

### Safety
HTTPS-only with 12s timeout + 50 MB content-length cap. SHA-256 verified
BEFORE the zip is opened. Zip Slip protection. Slug confusion protection
(extracted `plugin.json` slug must match catalog entry). Atomic install
via tempdir + `shutil.move`. Registry URL hardcoded in `config.py`.

**264/264 tests green** (+6 new).

---

## v0.15.0 — 2026-06-11 (Plugin Manager slice 1: self-describing plugins via plugin.json)

Each plugin folder now ships a `plugin.json` manifest declaring everything
the host needs: `kind`, `modes`, `load_order`, `copy_rules`, `verify_files`,
`cleanup`. The five hardcoded plugin tables in `core.py`
(`_PLUGIN_KIND` / `_MODE_PLUGIN_NAMES` / `_PLUGIN_COPY_RULES` /
`_PLUGIN_VERIFY_FILES` / `_PLUGIN_CLEANUP_ITEMS`) are now **derived** at
module load — ~150 lines of hardcoded constants replaced by ~80 lines of
discovery + 8 self-describing JSON files.

### Discovery scans two locations
- **Bundled** — `cs2servergui/plugins/<slug>/` (ships inside the .exe)
- **Local** — `%APPDATA%/Oblivion Server Tool/plugins/<slug>/`

Local plugins **override** bundled ones if slugs collide. Mismatched
folder/slug declarations rejected loudly to stderr.

### API + SPA
- `/api/plugins` entries carry a `source` field (`"bundled"` | `"local"`).
- SPA Library cards from `%APPDATA%/plugins/` get a blue **Local** pill.

### New top-level `PLUGINS.md`
Plugin author guide — schema, layout, required + optional fields, minimal
example, debugging tips, how to share.

Smoke-tested against live `%APPDATA%`: dropped a sample plugin in the real
APPDATA folder, ran discovery, confirmed it appears with `source=local` +
correct manifest + mode binding.

**258/258 tests green** (+5 new).

---

## v0.14.2 — 2026-06-10 (Config tab restructure + button hover polish)

### Single-column Config layout
Six clearly-separated sections in operator-mental-model order:
**Setup → Security → Server (+ Bots merged) → Match Flow → Discord
(+ webhook moved here) → Tools row** (Gaming Mode | Diagnostic | RCON).

Strong section separators: 2 px top border + accent-bar title + caption.
Inline `margin-top:14px` purged in favour of consistent CSS rules.

- **Bots** folded into **Server** (one Save button covers both).
- **Discord webhook URL** moved from Match Flow into the Discord card.
- **Tools row** at the bottom: three small cards side-by-side instead of
  pretending to be full sections.

### Button hover polish (whole app)
Non-purple buttons (`.btn` / `.btn-ghost` / `.btn-neutral`) get an
accent-tinted border + soft glow on hover. Active state drops the glow.

**253/253 tests still green.**

---

## v0.14.1 — 2026-06-10 (plugin actions auto-restart when server is running)

Plugin tab actions (Activate, Switch to vanilla, Apply Pack) **no longer
409 when the server is running**. They route through `change_map`'s
proven stop-deploy-restart cycle — the same path the Maps/Mode picker has
used since v0.10.x. Return `202 Accepted` with `restarting: true`.

- New `_resolve_live_swap_map` helper picks a sensible map (operator's
  preference → current_map if valid for new mode → `MODE_MAPS[mode][0]`).
  Workshop IDs detected via `_DIGITS_RE`.
- SPA banner now warns instead of disabling. Confirm prompts mention STOP
  and RESTART when running. Toasts switch to "Restarting into X — watch
  Status tab".

**253/253 tests green** (+2 swapping the "blocks when running → 409" tests
for "routes to restart → 202").

---

## v0.14.0 — 2026-06-10 (Plugin Manager: packs + JSON catalog + runtime bootstrap + audit fixes)

### Quick-Apply Packs (slice 3, task #91)
New strip at the top of the Plugins tab — five one-click recipes:
**Competitive 5v5** / **Warcraft Night** / **Casual Deathmatch** /
**Retakes** / **Vanilla Competitive**. Each click stages mode + map +
plugins under one `_lifecycle_lock` so half-applied state is impossible.

### File-based catalog (slice 4, task #90 partial)
Plugin display metadata moved out of code into
`cs2servergui/registry/catalog.json` (schema_version 1, registry-compatible
shape with `versions[]`). Bundled into the .exe via PyInstaller.

### Runtime bootstrap dialog (slice 5)
**🔧 Set up plugin runtime** button on Server Readiness card when MetaMod
or CSS is missing. Opens a modal with direct download links to sourcemm.net
+ CSS GitHub releases, step-by-step extract instructions targeting the
operator's actual `csgo/` path, and **✓ I've installed them — verify now**
button.

### Audit fixes (4 hardening passes)
- **#1**: `/api/plugins` is now `@require_local` — was leaking absolute
  `csgo_dir` path (and Windows username) to remote captain/voter sessions.
- **#2**: every operator-supplied string in the Plugins tab is now
  `esc()`'d. Defuses the v0.15 remote-registry XSS surface ahead of time.
- **#3**: `_load_plugin_catalog` logs loudly to stderr on parse failure.
- **#4**: inline `_PLUGIN_CATALOG_FALLBACK` dropped — JSON is the single
  source of truth.

**251/251 tests green** (+18 new).

---

## v0.13.2 — 2026-06-09 (Plugin Manager: tab + Activate/Vanilla actions)

### Slice 1 — Plugins tab (read-only)
New admin-only **Plugins** entry in the sidebar between Maps and Veto.
Three sections:
- **Server Readiness** — csgo/ resolved? MetaMod patched? CSS host present?
- **Currently Deployed** — manifest readout (mode + plugins + deployed_at)
- **Plugin Library** — eight cards (Warcraft, MatchZy, Retakes, Jailbreak,
  Deathmatch, Arenas, CS2Fixes, ZE) with display name, summary, author,
  modes used. Plugin tied to current mode shows Active pill.

`/api/plugins` GET endpoint backs the tab. `_PLUGIN_CATALOG` dict in
`core.py` is the seed of what becomes `catalog.json` in slice 4.

### Slice 2 — Activate / Switch-to-vanilla
- **Activate** button on Library cards (single-mode plugins) or dropdown
  picker (multi-mode like MatchZy → Practice/3v3/4v4/5v5).
- **Switch to vanilla** button on Currently Deployed card with confirm.
- Backend preflight (server stopped + no dl + veto idle + csgo/ exists)
  returns specific 409/503. Activate routes through
  `set_offline_mode_and_deploy` under `_lifecycle_lock` for atomicity.
- Yellow banner when server running or csgo/ missing — buttons disabled
  with the explanation.

**243/243 tests green** (+10 new).

---

## v0.13.1 — 2026-06-06 (PLATFORM.md + first method migration)

Two things land together: the design doc that informs every future
driver-migration commit, and the worked-example first migration that
proves the strangler-fig pattern.

### PLATFORM.md (task #84)
New top-level doc.  ~350 lines.  Covers:
1. Why the driver abstraction exists
2. What v0.13.0 already landed (the seam)
3. The boundary — what stays generic vs. CS2-only vs. contested
4. Driver interface (current + planned methods, in migration order)
5. The strangler-fig migration plan with worked example
6. Constraints + lessons from the tournament (mutation contract,
   cookie-without-redirects, no silent drops, game-specific timing,
   plugin verification)
7. v0.13 → v0.14 → v0.15 → v1.0 roadmap impact
8. Plugin Manager seam (how plugins relate to drivers — informs #92)
9. v1.0 readiness criteria for drivers
10. Open questions (multi-driver per process, driver versioning,
    third-party drivers, plugin registry mono vs. per-plugin repos)

Closes task #84 (was: "Write PLATFORM.md after first real session").
The tournament is done; the doc captures what we learned.

### First method migration: `install_root()` (task #86 ongoing)
The smallest CS2-specific method in `core.py` moves into the driver
as the worked example for every subsequent migration.

**Before:**
```python
# core.py
class AppCore:
    def _csgo_dir(self) -> str:
        return os.path.dirname(_config.CS2_ADDONS_DIR)
```

**After:**
```python
# drivers/cs2/driver.py
class CS2Driver(GameDriver):
    def install_root(self, core) -> str:
        return os.path.dirname(_cfg.CS2_ADDONS_DIR)

# core.py
class AppCore:
    def _csgo_dir(self) -> str:
        # v0.13.1 — thin shim; real impl lives on the driver
        return self.driver.install_root(self)
```

All ~8 existing call sites of `_csgo_dir()` keep working unchanged.
New code calls `self.driver.install_root(self)` directly.  When the
last call site migrates, the shim deletes.

Pattern established: **add abstract method to `GameDriver` → implement
in `CS2Driver` → reduce AppCore method to a delegate → test
extensively → ship.**  Every subsequent v0.13.x migration follows
this template.

### Tests
+2 new in `tests/test_drivers.py`:
- `cs2driver.install_root()` returns parent of CS2_ADDONS_DIR
- `AppCore._csgo_dir()` delegates to `driver.install_root()` (drift guard)
**233/233 green.**

### Next (v0.13.2+)
Per PLATFORM.md § 4, the migration order is:
- `addons_dir()`, `cfg_dir()`, `match_config_target()` (paths — small)
- `is_server_process()`, `process_kill_filter()` (process detection)
- `start_server()`, `stop_server()` (large — the real meat)
- `deploy_plugins_for_mode()` (depends on plugin registry #90)

---

## v0.13.0 — 2026-06-06 (driver abstraction seam — task #86)

First v0.13 release.  Opens the **driver abstraction** that v0.13.x +
v0.14 + v0.15 build on: a single `GameDriver` interface so future
versions can add TF2 / GTA-RP / FiveM drivers without touching the
Flask web layer, the SPA, the veto state machine, the Discord bot,
or the broadcast/SSE plumbing.

**Scope** — this is the seed of the driver layer, not the full
extraction.  The strangler-fig migration moves CS2-specific logic
out of `core.py` one method at a time.  v0.13.0 ships the interface
+ a CS2 implementation + the SPA snapshot section that surfaces
which driver is active.  v0.13.x will pull more methods (start/stop,
RCON, plugin deploy, MatchZy handoff) into the driver as the
TF2 driver work for v0.13.x reveals each seam.

### New
* **`cs2servergui/drivers/` package** (3 new files):
  - `drivers/base.py` — `GameDriver` abstract base class with the
    minimum-viable interface for v0.13: identity properties
    (game_name, short_name, default_port, process_image_name,
    process_args_marker, console_log_filename), `modes()` +
    `default_map()` abstract methods, default `console_log_dir()`
    + `console_log_path()` + `status_line()` + `describe()`.
  - `drivers/cs2/driver.py` — `CS2Driver` concrete implementation.
    Identity = Counter-Strike 2 / cs2.exe / port 27015 /
    `-dedicated` filter.  `modes()` proxies `config.MODE_SETTINGS`
    keys so adding a mode in config picks up everywhere without
    a driver edit.  `default_map()` walks `config.MODE_MAPS`.
    `status_line()` adds an MR12 hint for competitive-family modes.
  - `drivers/__init__.py` — exports `GameDriver` + `CS2Driver` and
    documents the architecture + migration strategy.
* **`AppCore.driver`** — instantiated in `__init__` with `CS2Driver()`.
  New code reaches game-specific knobs via `core.driver.X` instead
  of hardcoding `"cs2.exe"` / `"MatchZy"` literals.  Existing core
  code still uses the literals; those get migrated one seam at a time.
* **Diagnostic snapshot — new "Driver" section** above the Server
  Status block.  Operator sees game name, short_name, default_port,
  process image, plugin layer, match layer, mode count at a glance.

### Why a v0.13.0 (not v0.12.6)?
This is the start of the **driver abstraction series**.  v0.13.x will
fill in the rest of the migration (start/stop, RCON, plugin deploy,
MatchZy handoff each get a driver method); v0.13.1+ adds the TF2
driver as the proof point.  The minor bump signals "the codebase
just gained a structural seam"; future v0.13.x patches are the
migration work behind that seam.

### Migration strategy (strangler fig)
Existing CS2-hardcoded code in `core.py` / `web.py` / `veto.py` still
works untouched.  New code uses `core.driver.X`.  Over time,
function-by-function, the body of `core.start_server` (etc.) moves
into `CS2Driver.start_server`; the AppCore method becomes a thin
delegate.  When TF2Driver lands, only the methods that have been
moved into the driver need a TF2 equivalent — anything still in
AppCore is the migration TODO list.

### What stays generic (driver doesn't touch these)
- `web.py` Flask routes, auth, SSE, broadcast
- `static/js/*` SPA (entire frontend)
- `veto.py` VetoSession state machine, captain/voter tokens
- `discord_bot.py` bot lifecycle + slash commands
- `match_events.py` round-summary poller (driver mediates RCON only)
- `rcon.py` Source RCON protocol (TF2 also uses it; FiveM does not —
  FiveM driver will provide its own command runner)

### What's CS2-only forever (always in the driver)
- MetaMod + CounterStrikeSharp plugin layout
- MatchZy match-config JSON shape + `matchzy_loadmatch` RCON cmd
- `csgo/cfg/MatchZy/` write target
- `cs2.exe -dedicated` process detection
- `de_dust2` / `de_mirage` / etc. map pool

### Tests
+9 new in `tests/test_drivers.py`:
- CS2Driver identity props match legacy hardcoded values (regression-guard)
- modes() matches `config.MODE_SETTINGS` keys (drift guard)
- default_map() returns first of mode allow-list + falls back to de_dust2
- status_line() reports offline cleanly; adds (MR12) hint only for
  competitive-family modes
- describe() includes CS2-specific extras (plugin_layer + match_layer)
- AppCore instantiates with a `.driver` attribute (CS2Driver)
- GameDriver is abstract (cannot instantiate directly)
**231/231 green.**

### Next (v0.13.x)
- Migrate `core.start_server` body into `CS2Driver.start_server` (first
  real method extraction, not just identity)
- Plugin deploy logic per-mode (the `_PLUGIN_*` tables are pure CS2)
- MatchZy handoff
- Add `drivers/tf2/` skeleton + identity to validate the seam works
- v0.13.x.y eventually ships a working TF2 driver as the proof point

---

## v0.12.5 — 2026-06-06 (Gaming Mode toggle + scripts bundling)

Closes tasks #95 + #97 — the last two pending v0.12 polish items.
All v0.12 backlog is now drained.

### Gaming Mode toggle in Config card (task #95)
* New SPA section: **Gaming Mode (host + play perf)** with three
  buttons (⚡ ON / 💤 OFF / 📊 Status).  Wraps `scripts/gaming-mode.ps1`.
* `POST /api/system/gaming_mode` endpoint — `mode` in {on, off, status}.
  Local-only via `@require_local` (the script flips Windows Power
  Plan + cs2.exe core affinity; only meaningful on the operator's
  own machine).
* `api.gamingMode(mode)` wrapper.
* Output dumped into a collapsible `<pre>` so the operator sees
  exactly what the script reported.
* Pairs with `TROUBLESHOOTING.md`'s "Performance: hosting + playing
  on the same PC" section — same scripts, now reachable without
  opening a terminal.

### Installer bundles scripts/ (task #97)
* `installer.iss` now copies `scripts/*` to `<install_dir>/scripts/`
  with `recursesubdirs createallsubdirs`.
* Web layer's `_scripts_dir()` resolves `scripts/` correctly for
  both dev (`<repo>/scripts/`) and frozen (`<install_dir>/scripts/`
  via `sys.executable`).
* `PROCESS_LASSO_SETUP.md` + `README.md` ship alongside the .bat /
  .ps1 helpers.

### Tests
+2 new in `tests/test_veto_api.py`:
- gaming_mode 400 on invalid mode
- gaming_mode 403 for non-local admin (require_local enforcement)
**222/222 green.**

### v0.12 backlog status
**Drained.**  All tasks tagged for v0.12 closed.  Remaining open
tasks (#84, #85, #86, #87, #88, #89, #90, #91, #92, #93, #94) are
all driver-abstraction work or the v1.0 launch arc.

---

## v0.12.4 — 2026-06-06 (content-hashed static URLs)

Closes audit finding #6 / task #139.  All 10 audit findings now resolved.

### Fixed
* Replace v0.11.24's blanket `Cache-Control: no-store` on `/static/*`
  with the standard "cache-bust via URL change" pattern.
* Template injects `?v={{ app_version }}` into every `/static/*` URL
  (favicons, CSS, JS, emblem images, login emblem).
* Versioned URLs get `Cache-Control: public, max-age=31536000, immutable`.
* Each release ships new URLs → browser treats them as fresh resources
  → cache-bust on rebuild AND aggressive caching between rebuilds.
* Unversioned URLs (e.g. a stray request from a stale bookmark) fall
  back to Flask's default ETag-based behaviour — backwards compatible.

### Why
v0.11.24 was correct (it busted the WebView2 stale-JS cache that
caused half the tournament-night frustration) but a perf regression
— every page load re-downloaded the full ~600KB app.js even when
the .exe hadn't changed.  This release keeps the correctness AND
restores between-rebuild caching.

### Tests
+3 new.  **220/220 green.**

---

## v0.12.3 — 2026-06-06 (remote player voting via per-player tokens)

Closes task #135 — the last major v0.12 feature.  Extends the Layer 1A
captain-DM pattern to all 10 players: after Distribute, the operator
clicks "📨 DM voting links to all 10" on the Teams stage; the bot DMs
each rostered player a one-shot voting URL.  Player taps → HTML
interstitial sets a `role=voter` cookie → minimal voting page shows
their team's 5 names → tap one → vote cast.  No more
operator-walks-around-the-room voice-chat-driven vote collection.

### Data model (`veto.py`)
* `VoterToken` dataclass — single-use credential per (team, voter_idx).
* `VetoSession.voter_tokens` — `dict[str, VoterToken]` keyed by
  `"A:0" .. "A:4" / "B:0" .. "B:4"`.
* `issue_voter_tokens(session)` — legal only in `voting` state.
  Idempotent (matches captain `issue_tokens`'s rotate-protection: a
  second call returns the same values if any token has been claimed).
* `claim_voter(session, token, caller_id)` — validates + binds.
* `distribute_teams()` now clears `voter_tokens` (a reshuffle reorders
  the team rosters so any DM'd tokens point at the wrong person).
* `reset()` clears `voter_tokens`.
* `serialize_session` / `deserialize_session` round-trip them.

### Role gate + endpoints (`web.py`)
* New `_VOTER_PATHS` frozenset — strictly tighter than captain.  Voter
  reaches `/api/state`, `/api/capabilities`, `/api/veto/state`,
  `/api/veto/stream`, `/api/veto/vote`.  Nothing else.
* `/api/veto/voter_claim` is `_PUBLIC_PATHS` — token IS the credential.
* `POST /api/veto/voter_tokens` — admin only.  Mints 10 + auto-DMs.
* `POST /api/veto/voter_claim` — mints `role=voter` cookie scoped to
  `(voter_team, voter_idx)`.
* `GET /voter?join=<token>` — landing.  HTML interstitial (no 302) for
  Discord iOS WebView compat.  `Cache-Control: no-store, private`.
* `/api/veto/vote` rejects cross-slot writes from voter sessions (403).
* `/api/veto/reset`'s `_sessions` sweep now drops voter sessions too.

### Snapshot extensions
* `/api/state` exposes `voter_team` + `voter_idx` for voter sessions.
* `/api/veto/state` includes `voter_tokens_claimed` keyed by slot →
  bool so the SPA can show ✓ next to each player who has claimed.

### SPA (`app.js` + `api.js`)
* New `_renderVetoVoter()` — minimal "tap one of 5 names" page.
  Pre-voting stages show a "waiting on operator" message.
* New **📨 DM voting links to all 10** button on the Teams stage —
  auto-advances `teams → voting`, mints + DMs, toasts the result.
* `api.veto.voterTokens()` + `api.veto.voterClaim()` wrappers.

### Tests
+3 new in `tests/test_veto_api.py`.  **217/217 green.**

---

## v0.12.2 — 2026-06-06 (SSE broadcast observability)

Closes task #143 (audit finding #10).  Investigation, not a redesign.

The audit hypothesised that `_veto_broadcast()`'s `q.put_nowait` with
a silent `except` could be dropping events under burst load — and
that this might be the real reason v0.11.25's polling fallback was
needed.  We had no way to confirm or deny.

### Observability
* New module-level counter `_veto_broadcast_stats`:
  - `events_total` — broadcast() calls since process start
  - `drops_total` — `put_nowait` Full exceptions (silent drops)
  - `last_drop_at` — epoch timestamp of most recent drop
* First drop per process logs once.
* Diagnostic snapshot: new **SSE broadcast telemetry** section showing
  the counters + `active_subscribers` count.
* TL;DR auto-scan: `⚠ sse N broadcast event(s) dropped` when > 0
  (silent when zero — no clutter).

### Headroom
* Bumped per-subscriber queue maxsize from 32 → 256.  No real workflow
  bursts 32 broadcasts back-to-back, but the larger ceiling closes the
  speculative gap and gives a stalled WebView2 plenty of room to
  drain on resume.

### Next step
Once a production snapshot shows `drops_total > 0`, we have a smoking
gun to redesign around (event-seq + gap-driven catch-up).  Until then
the polling fallback stays.

### Tests
+1 new (snapshot contains the new section).  **214/214 green.**

---

## v0.12.1 — 2026-06-06 (round summaries + first slash commands)

Big v0.12 release.  Closes tasks #134 (`/round-summaries`) AND #145
(`/move-teams` slash command) in one shot — the slash-command tree
wiring was the blocker for both, so doing them together saved a release.

### New module — `cs2servergui/match_events.py`
Background RCON-poll daemon thread that detects `mp_t_score` /
`mp_ct_score` deltas every 3 s and posts a small embed to
`discord_veto_channel_id` after every round.  Final 🏆 embed when
either side reaches 13 (covers MR12 directly; MR15 trips when leader
hits 13 too — operator can pin/delete if false-positive).

Lifecycle:
- `match_events.start(core)` — called from `/api/veto/finale` once
  MatchZy is handed the config.
- `match_events.stop()` — called from `/api/veto/reset` and is
  idempotent (safe no-op when not running).
- Poller re-reads the toggle every tick → operator can flip
  `discord_round_summaries_enabled` mid-match without restarting
  anything.

Fail-soft: RCON / Discord errors are logged and skipped; the poller
never crashes the bot or blocks Flask.

### New slash-command tree (first time the bot has one)
- `discord.app_commands.CommandTree(client)` attached to `_BotRunner`.
- `on_ready` syncs **per-guild** when `discord_guild_id` is set
  (immediate propagation) or globally otherwise (~1 hr to land).
- `_register_app_commands()` is the extension point for future
  commands.

### Slash commands shipped
- `/round-summaries on | off | status`
- `/move-teams now`  (manual fire)
- `/move-teams auto on | off`  (persistent toggle)
- `/move-teams status`  (current config + active session)

Default permissions: `manage_guild + move_members` on both groups.

### Refactor — `_do_move_to_team_channels()` extracted
The move-to-team-channels logic is now a free async function so the
slash command can `await` it directly.  Calling the existing threaded
wrapper (`bot_move_to_team_channels`) from inside the bot's loop
would deadlock — submitting back to the loop you're running on, then
blocking on `.result()`.  Wrapper is now a thin shim around the free
function.

### New endpoint
- `POST /api/discord/round_summaries_toggle` — admin-only.  No
  precondition check (embed target reuses `discord_veto_channel_id`;
  blank channel = silent no-op same as live veto embed).

### Config (new field)
- `discord_round_summaries_enabled` (bool, False).  Mutated via the
  toggle endpoint OR the slash command — same field, two faces.

### SPA
- New "Post round summaries to the veto channel" checkbox in the
  Discord config card.  Hint mentions the slash command alternative.

### Tests
+5 new in `tests/test_veto_api.py`:
- `/api/discord/round_summaries_toggle`: both enable + disable persist
- `match_events._parse_scores`: happy path + None on missing cvars
- `match_events`: round embed colored by winning side (T → blue,
  CT → orange)
- `match_events.start/stop`: idempotent
**213/213 green.**

### Deferred to v0.12.2+
- MVP / clutch / ace detection (needs CSS log tail or MatchZy
  webhook).
- End-of-series summary embed for BO3/BO5 (currently per-map only).
- Demo upload link in final embed (needs MatchZy demo-uploader hook).

---

## v0.12.0 — 2026-06-06 (Discord-driven team voice splits)

First v0.12 minor — picks up where v0.11.26 audit cleanup left off and
adds the `/move-teams` infrastructure that paired with the existing
🎤 roster-pull from v0.11.15.  Lobby in → teams out, no operator dragging
members around in voice.

### New
* **`/api/discord/move_teams`** — POST endpoint, admin-only.  Reads the
  active veto session's `team_a` + `team_b` discord_ids, calls
  `bot_move_to_team_channels()`, returns
  `{moved_a, moved_b, skipped, errors, team_a_name, team_b_name}`.
* **`/api/discord/auto_move_toggle`** — POST endpoint, admin-only.
  Persists `discord_auto_move_on_distribute_enabled`.  Refuses to enable
  if either team VC is unconfigured — surfaces the precondition error
  instead of silently no-op'ing on tournament night.
* **Auto-fire on `/api/veto/distribute`** — when toggle ON + both VCs
  set + bot connected, fires a background `bot_move_to_team_channels`
  call ~2s after distribute (grace for late lobby joiners).  Wrapped
  in try/except so a move failure NEVER blocks the distribute
  response.  Default toggle OFF — opt-in.
* **`bot_move_to_team_channels(guild_id, a_vc, b_vc, a_ids, b_ids)`** —
  new helper in `discord_bot.py`.  Concurrent moves via
  `asyncio.gather` + `Semaphore(5)` for rate-limit safety.  Skips
  members not in voice (Discord API limitation).  Per-player
  `Forbidden` / `HTTPException` captured into errors list with the
  player's display name.
* **SPA Discord config card** — two new VC inputs with 🔍 Browse
  pickers (reuses the v0.11.15 voice-channel picker modal) + an
  Auto-move checkbox that goes through the toggle endpoint.
* **SPA Veto Teams stage** — new 🔀 **Move teams to VCs** button.
  Backend refuses cleanly if VCs unset / bot offline; toast surfaces
  the specific error.

### Config (new fields)
- `discord_team_a_voice_channel_id` (str, "")
- `discord_team_b_voice_channel_id` (str, "")
- `discord_auto_move_on_distribute_enabled` (bool, False)

All three persist through `load_config()` / `save_config()`.  Read-only
in the `/api/config` snapshot; the toggle is mutated via
`/api/discord/auto_move_toggle` so the precondition check lives in one
place.

### Discord permissions
Bot needs **Move Members** in the guild for any of these flows to work.
Documented inline in the SPA config card's hint text.

### Tests
+8 new in `tests/test_veto_api.py`:
- 400 when guild_id unset / VCs unset / no session / state=roster
  (teams not split) / no discord_ids on either team
- toggle refuses enable with VC missing, always allows disable,
  persists when both VCs set
**208/208 green.**

### Deferred to v0.12.1
- `/move-teams` Discord slash command (task #145) — the bot helper is
  ready; just needs `app_commands.Group` wiring.  Pairs with #134
  (`/round-summaries`) since both want the slash tree.

---

## v0.11.27 — 2026-06-06 (audit consolidation: findings #5/#7/#8/#9)

Single coherent change: `_vetoApply` becomes the SINGLE POINT OF TRUTH
for ALL snapshot ingestion.  Two guards live in one helper, applied
uniformly to mutation responses, SSE messages, the initial fetch, and
the 3s polling fallback.

### Fixed
* **Monotonicity guard** (findings #5 + #7).  If both incoming and
  current have a session AND same state AND incoming `updated_at`
  is OLDER than current, refuse the apply.  Defeats:
  - Initial veto-page fetch slow on cellular → SSE delivers snap_v2
    first → stale fetch overwrites it on resolve.
  - Poll fetch in flight → SSE delivers snap_v2 → stale poll response
    overwrites on resolve.
  State transitions (idle ↔ active, voting → links, etc.) always
  apply.
* **Idle short-circuit** (finding #9).  When both incoming and current
  are `state: idle`, skip apply.  Without this the 3s poll rebuilt
  `_renderVetoIdle` every tick → online-banner flashed "Checking…"
  every 3s → button focus lost.
* **Click-flag stuck-state** (finding #8).  Wrapped ENTIRE click body
  (sync visual setup + async API call) in one `try / finally` so a
  synchronous throw during DOM marking can no longer leave
  `_vetoBoardClickInFlight` stuck `True` forever.  Polling fallback
  was dead-locking on stuck-flag because it skips while flag is True.

### Refactor wins
* SSE handler: 3 lines → 1 (just `_vetoApply`).
* Initial fetch: `.then(snap => { _vetoState = snap; _renderVeto(); })`
  → `.then(_vetoApply)`.
* Polling tick: 11 lines of inline dedup → 1 line.  Logic that was
  half here, half in renderer's `_vetoLastRenderedState` guard, now
  lives entirely in `_vetoApply`.

### Audit progress
8 of 10 findings now closed (v0.11.26 + v0.11.27).  Remaining: #6
(content-hashed static URLs, task #139) and #10 (`_veto_broadcast`
queue-overflow investigation, task #143).  Both genuinely belong
with v0.12 driver-abstraction work.

### Tests
**200/200 green.**

---

## v0.11.26 — 2026-06-06 (audit cleanup: 4 fixes from v0.11.20-25 review)

Post-tournament code-review sweep of the v0.11.20-25 hotfix chain found
10 issues; the 4 with tournament-night failure modes ship now.  Remaining
6 deferred to v0.12 (see ROADMAP — audit findings #5-10).

### Fixed
* **Zombie captain race on /api/veto/reset** (web.py).  The reset path
  released `_veto_lock` before acquiring `_sessions_lock`.  A concurrent
  `/veto?join=<token>` captain claim that won `_veto_lock` first ran
  `_create_session` AFTER the sweep snapshot — producing a captain
  cookie that referenced `core._veto_session = None`.  Fixed by nesting
  `_sessions_lock` inside `_veto_lock` in `veto_reset` AND moving
  `_create_session` inside the same `_veto_lock` block on both
  `/api/veto/claim` and `/veto?join` handlers.  Lock order remains
  consistent across all paths (always `_veto_lock` outermost), so no
  deadlock risk.
* **Captain interstitial missing Cache-Control** (web.py).  v0.11.20's
  200 HTML interstitial had no `Cache-Control` header.  A proxy that
  cached it would deliver the body without `Set-Cookie` on the second
  request to the same one-shot-token URL — captain unauthenticated AND
  token consumed.  Added `Cache-Control: no-store, private` +
  `Pragma: no-cache`.
* **v0.11.25 poll timer leak on tab leave** (app.js).  The hashchange
  cleanup listener gated on `currentPage === 'veto'`, but `navigate()`
  had already updated `currentPage` by the time the listener ran
  (listener registration order).  `_vetoCleanup` never fired; the 3s
  polling timer kept running for the rest of the app's lifetime.
  Fixed by dropping the `currentPage` gate (`_vetoCleanup` is
  idempotent).
* **Veto board click double-render on success** (app.js).  `_vetoApply`
  rendered on success, then `finally` rendered again — two full board
  rebuilds per click, listeners re-attached, visible flicker, and
  rapid taps could land on detached DOM nodes.  Moved the error-path
  render into `catch` so success path runs exactly one render.

### Refuted (kept as-is)
* **SameSite=Lax weakening admin CSRF**.  Verifier found every mutating
  endpoint requires `application/json` — `request.get_json()` returns
  `None` for form-encoded bodies and cross-origin XHR triggers CORS
  preflight that Lax still blocks.  The relaxation is safe given the
  JSON-only API contract.

### Tests
**200/200 green.**

---

## v0.11.20 → v0.11.25 — 2026-06-05 (tournament-night hotfix chain)

> Shipped in ~75 minutes from first failure (17:35) to clean match start
> (18:48) during a live 10-player CS2 tournament.  See
> `RETROSPECTIVE_2026_06_05.md` for the full timeline and lessons.

### v0.11.20 — SameSite=Lax + HTML interstitial
* Captain link cookie was being dropped in Discord's iOS in-app browser
  (WKWebView ITP bounce-tracking + SameSite=Strict on a 302 redirect).
* Dropped all 4 session cookies to `SameSite=Lax`.
* Replaced the `/veto?join` 302 redirect with a 200 HTML interstitial
  that sets the cookie inline then JS-redirects to `/#veto`.  Defeats
  the bounce-tracking guard.

### v0.11.21 — Invalidate captain HTTP sessions on /api/veto/reset
* When operator hit Reset, the `VetoSession` struct was nuked but
  captain HTTP session cookies (with `role=captain` + `captain_team=A/B`)
  stayed in `_sessions`.  Captains reconnecting after reset appeared
  authenticated against tokens that no longer existed.
* On reset, sweep `_sessions` and drop every entry with `role=='captain'`.
* (v0.11.26 later fixed the race window in this sweep.)

### v0.11.22 — Stuff /api/veto/step response into _vetoState locally
* SPA click handler awaited the API response then discarded it and
  relied on SSE to update `_vetoState`.  On LAN the API response wins
  the race against SSE delivery — so the `finally` render redrew the
  board from the **stale pre-ban** snapshot.  Clicked card reverted
  from `.pending` pulse to "no ban shown".
* Capture the response, validate it looks like a snapshot, assign to
  `_vetoState` before render.

### v0.11.23 — `_vetoApply` helper through every mutation handler
* Generalised v0.11.22.  Every veto-mutation endpoint already returns
  the fresh snapshot; the SPA threw them away and relied on SSE.
* Added `_vetoApply(snap)` helper that stuffs valid snapshots into
  `_vetoState` and triggers a render.  Wired through:
  `step`, `vote`, `resolve`, `ready` (admin + captain), `distribute`,
  `rematch`, `startVoting`, `reset`, `create`, `roster`.
* Local clicker sees instant updates on every click.

### v0.11.24 — no-cache headers on /static/*
* WebView2's HTTP cache persisted `app.js` across .exe rebuilds.
  Server was v0.11.23 but JS in browser was v0.11.20 — fixes shipped
  but not loaded.
* `after_request` hook adds `Cache-Control: no-store, no-cache,
  must-revalidate, max-age=0` + `Pragma: no-cache` + `Expires: 0` to
  every `/static/*` response.
* (v0.12 task #139 will replace this with content-hashed asset URLs —
  no-store defeats ETag revalidation, so every page load re-downloads
  the full bundle.)

### v0.11.25 — 3s polling fallback alongside SSE
* Belt-and-braces against any cache / SSE delivery issue.  Every 3s
  while on the Veto tab + tab visible + no click in flight, refetch
  `/api/veto/state` and apply via `_vetoApply` if `updated_at` changed.
* This is the version the tournament completed on.
* (v0.12 task #143 will investigate the underlying `_veto_broadcast`
  queue overflow that the polling masked.)

### Tests
**200/200 green** across every release in the chain.

---

## v0.11.19 — 2026-06-05 (snapshot — plugin log diagnostics)

Fills the visibility gap surfaced during pre-tournament verification:
MatchZy + CounterStrikeSharp plugins suppress/redirect CS2's default
console.log writes, leaving the diagnostic snapshot's CS2 log section
blank exactly when the operator is running the real tournament workflow
(MatchZy 5v5).  The new plugin-logs section picks up the slack.

### New
* **Plugin logs section** in the snapshot, after the CS2 console.log
  block.  Tails the most-recently-modified file in each known plugin
  log location, anomaly-prefixes `[ERROR]` / `[FATAL]` / `Exception` /
  `System.*Exception` / stack-trace `at Foo.Bar(` lines with `>`.
* Tailed locations:
  - `csgo/addons/counterstrikesharp/logs/log-*.txt` (CSS host log —
    captures plugin load errors + C# exceptions across all plugins)
  - `csgo/logs/MatchZy/*.log|*.txt` (MatchZy per-match events)
  - `csgo/addons/counterstrikesharp/plugins/MatchZy/logs/*.log|*.txt`
    (alternative MatchZy location for older versions)
* **TL;DR `plugin_log` indicator** — ✓ if a CSS log was written in the
  last hour (plugin layer is alive), ⚠ if stale, · if no CSS log
  (vanilla mode or plugins haven't loaded yet).
* Per-file metadata in the section: source path, size, age, anomaly
  count.

### Why
MatchZy redirects CS2's stock `con_logfile` writes to keep the channel
output clean.  Pre-v0.11.19 snapshots running a MatchZy match showed
zero CS2-side data — invisible to triage.  Now CSS log + MatchZy
match log fill that gap.

### Tests
+2 new (section present + CSS file tailing with anomaly prefixing).
**200/200 green.**

### Migration
None.  Section appears automatically when CSS / MatchZy logs exist;
shows a clear "no plugin logs found" status otherwise.

---

## v0.11.18 — 2026-06-05 (🔍 Browse for Veto Embed Channel ID)

Tiny consistency add: the Veto Embed Channel ID field now has a 🔍
Browse button next to it, just like the v0.11.15 Default Voice Channel
ID field.  Operators no longer have to enable Discord Developer Mode +
right-click → Copy Channel ID to populate it — pick from the list.

### New
* `bot_text_channels(guild_id)` helper in `discord_bot.py` — returns
  `[{id, name}, ...]` for every text channel in the guild.
* `/api/discord/text_channels` endpoint mirroring `/voice_channels`.
* SPA: 🔍 Browse button next to the Veto Embed Channel ID field
  reuses the existing pull-modal in `kind: 'text'` mode.
* `_vetoOpenDiscordPullModal` now accepts `opts.kind = 'voice' | 'text'`
  (default `'voice'`).  Text mode is browse-only; voice mode is
  unchanged.

### Tests
+1 new (text_channels 400 without guild ID).  Existing 503-when-bot-not-
connected test extended to cover the new endpoint.  **198/198 green.**

### Migration
None.  Existing setups are unaffected.

---

## v0.11.17 — 2026-06-05 (Friday-eve thorough sweep, Tier A + Tier B fixes)

Four parallel adversarial audits of the whole codebase (veto lifecycle,
Discord integration, server/RCON control, SPA/captain UX) surfaced 12
real findings worth landing pre-tournament.  All fixed.  No
ship-blockers among them — all SHIP_RISK / NICE_TO_FIX category — but
each one is a known failure mode the operator would hit during a real
session.  **197/197 backend tests green (+10 new).**

### Tier A — high-impact small fixes

* **A1** (`veto.py` `set_roster`): reject duplicate non-empty SteamIDs.
  Pre-fix, two captains pasting the same ID silently produced a
  MatchZy team config with 9-and-5 players, which the plugin refuses
  to load.  Error message names the duplicated ID so the operator can
  fix it before voting starts.
* **A2** (`veto.py` `rematch`): clear `live_embed_msg_id`.  Pre-fix,
  the bot kept editing the prior series's "MATCH LOCKED IN" embed,
  showing yesterday's result during today's veto.
* **A3** (`web.py` four `set_cookie` sites): add `secure=True` on
  HTTPS-via-tunnel requests.  Pre-fix, captains clicking their DM'd
  token link from Discord/Slack in-app webviews sometimes silently
  dropped the cookie → "no active session" loop.  New
  `_request_is_https()` helper honours `X-Forwarded-Proto` so the
  flag is set correctly behind a Cloudflare tunnel.
* **A4** (`app.css`): `.veto-stage-actions { flex-wrap: wrap }` so the
  v0.11.16 🔀 Pick channel button doesn't overflow on iPhone SE.
* **A5** (`discord_bot.py`): drop `intents.message_content = True`.
  Privileged intent that the bot never used — leaving it enabled
  meant fresh-guild migrations silently failed to connect if the
  operator hadn't ticked the toggle in the Developer Portal.
* **A6** (`web.py` `/api/server/start`): return 409 when a workshop
  download is in flight.  Pre-fix, cs2.exe could boot against a
  half-extracted addon folder → silent fallback to dust2 or broken map.
* **A7** (`web.py` `config_set` Discord token branch): re-save with
  the same token now restarts the bot if it's disconnected, giving
  the operator a recovery lever instead of having to restart the
  whole app when the bot loop dies.

### Tier B — defended through real-tournament failure modes

* **B1** (`web.py` `_refresh_live_veto_embed`): coalescing serializer.
  Pre-fix, every step spawned a fresh thread → captains rage-clicking
  could produce out-of-order edits or duplicate embed posts.  Now a
  single in-flight worker drains the "latest pending snapshot" slot;
  rapid clicks coalesce so only the newest state hits Discord.
* **B2** (`core.py` `_load_active_veto_session`): tighter cutoff for
  past-`links` sessions.  Sessions in voting/veto/finale/complete
  states get a 1-hour resume window (was 12h).  Earlier-stage
  sessions still get the original 12h.  Prevents yesterday's
  half-played session from coming back as a "captain link from
  yesterday hijacks today's setup."
* **B3** (`core.py` + `web.py`): finale double-fire guard via
  `_finale_firing` flag set under `_veto_lock`.  Both the captain-
  ready auto-launch path and the admin Finale button check the flag
  first; concurrent triggers serialize through ONE
  `matchzy_loadmatch` call.  Cleared on success, error, and reset.
* **B4** (`app.js` `_renderVetoBoard`): captain-tap guard.  While an
  API call is in flight, suppress `_renderVeto()` rebuilds of the
  board AND visually mark the tapped card as `.pending` with the
  other cards `.locked-during-pending`.  Pre-fix, a fast-clicking
  captain on a 3G phone could lose their tap to an SSE-driven
  rebuild.
* **B5** (`app.js` `_renderVetoFinaleCaptain`): re-derive `myReady`
  from live `_vetoState` at click time, not the closure captured
  at render time.  Pre-fix, a captain who saw a fresh ✓ READY badge
  via SSE then tapped to un-ready could send `ready(true)` (no-op)
  because the closure still had the old value.

### Tests
* +10 backend tests for A1/A2/A6/B2/B3 (+3 in `test_veto.py`, +7 in
  `test_veto_api.py`).  Total **197/197 green**.
* No SPA tests added — A3/A4/A7/B1/B4/B5 are behavioural changes the
  Friday-night smoke battery will exercise end-to-end.

### Migration
None required.

---

## v0.11.16 — 2026-06-04 (v0.11.15 adversarial-review hotfixes)

Self-imposed code-review pass on v0.11.15 surfaced three ship-risks that
matter on tournament night.  All three fixed.  No new behaviour, no API
changes.

### Fixed
* **Double-click race on the 🎤 Pull from voice channel button.**  Two
  rapid clicks no longer fire two concurrent `voice_channel_info` +
  `voice_members` round-trips.  Disabled+try/finally guard ensures the
  second click is a no-op until the first completes.
* **Confusing two-error UX when default VC is set but bot is offline.**
  Previously: toast "Default VC unreachable" → then picker modal which
  also failed for the same reason.  Now: silent fallback to the picker
  for the predictable failure modes (no default set, guild ID not
  configured, bot not connected, bot module unavailable).  Toast still
  fires for genuinely unexpected errors so they aren't swallowed.
* **No mobile path to the picker** when a default VC is configured —
  Shift+click only worked on desktop.  Added a small 🔀 **Pick
  channel…** secondary button next to 🎤 that always opens the picker.
  Tablet/phone operators no longer get locked into the default.

### Changed
* Diagnostic snapshot's bot voice-channel lookup timeout dropped 3.0s
  → 1.5s.  If the bot is mid-reconnect during a triage, the snapshot
  no longer feels frozen for 3 seconds.
* Roster button tooltip clarified — "Uses your default voice channel
  if configured; otherwise opens the picker."

### Tests
187/187 green (no new tests — all SPA changes, behavioural).

---

## v0.11.15 — 2026-06-04 (default voice channel for one-click roster pull)

Recurring-tournament UX polish.  When you run the same event every Friday
in the same Discord, configuring a default voice channel turns the Veto
roster's "🎤 Pull from voice channel" button into a **one-click** action
that pulls members from that VC directly — no picker, no extra clicks.

The picker isn't gone; it's just demoted to fallback.  Shift+click on the
roster button forces the picker for tonight-only overrides (overflow VC,
testing in a different guild, etc.).  If the configured VC is empty or
unreachable, the picker auto-opens with a toast hint.

### New
* **Default Voice Channel ID** field in Config → Discord card.  Optional
  — blank keeps the original picker-each-session behaviour.  Includes a
  🔍 Browse button that reuses the existing voice-channel modal in
  "pick-only" mode to stamp an ID into the field without leaving the
  Config tab.
* **Live preview** under the field showing the configured VC's name +
  current connected count ("Default VC: **#Pre-Match Lobby** — 8
  connected").  Refreshes on save.
* **Diagnostic snapshot** Discord block now reports `voice_channel_id`,
  `voice_channel_name`, and `voice_channel_count` so "is the bot seeing
  my VC?" is a one-snapshot answer.
* New API endpoint `/api/discord/voice_channel_info` — lightweight
  single-VC info lookup (name + live member_count) without enumerating
  the entire guild.  Used by the live preview + the snapshot.
* New discord_bot helper `bot_voice_channel_info(guild_id, channel_id)`.

### Changed
* Veto roster "🎤 Pull from voice channel" button now honours the
  configured default VC when set: one-click direct pull from that VC,
  toast shows the count + VC name.  Shift+click forces the picker
  regardless.  No default configured → original picker behaviour.
* `_vetoOpenDiscordPullModal` accepts `{pickOnly, onPick}` opts so the
  Config-card 🔍 Browse button reuses the same modal shell.

### Tests
* `voice_channel_info`: 400 without any channel ID
* `voice_channel_info`: 400 without guild ID
* `voice_channel_info`: 503 when bot not connected (extends voice_* 503 test)
* `discord_voice_channel_id`: round-trips through `/api/config` (local)
* `discord_voice_channel_id`: remote write rejected (local-only gate)

### Migration

No action required.  The new field defaults to empty → existing behaviour
preserved.  Operators who want the one-click flow: Config → Discord →
"Default Voice Channel ID" → 🔍 Browse → pick the lobby → Save.

---

## v0.11.14 — 2026-06-03 (host-and-play perf tooling)

**Standalone scripts for the "I host the server AND play CS2 on the
same PC" alt-tab-lag problem.**  Real Way-3-paste session surfaced
operator hitting noticeable lag spikes on alt-tab between CS2,
Discord, and Oblivion.  Root cause: Windows reshuffles CPU/GPU
priorities on foreground change, and both `cs2.exe` processes
(server + client) end up briefly fighting for execution resources.

New `scripts/` folder ships with:

- **`gaming-mode.ps1`** — PowerShell engine.  `-Mode Gaming` applies
  CPU affinity pinning (server → first 4 cores at High priority,
  client → remaining cores, Oblivion → core 0), forces Power Plan
  to High Performance / Ultimate, disables Game Mode + DVR.
  `-Mode Default` reverts everything EXCEPT power plan (operator
  preference: High Perf at all times).  `-Mode Status` for inspection.
- **`gaming-mode-on.bat`** / **`gaming-mode-off.bat`** — convenience
  launchers, auto-elevate to admin via UAC.
- **`gaming-mode-status.bat`** — no-admin status check.
- **`install-shortcuts.ps1`** + **`install-shortcuts.bat`** — one-time
  setup that drops two desktop shortcuts ("Oblivion - Gaming Mode
  ON" / "OFF") so toggling is a single double-click.  Auto-elevate
  bit set in lnk binary.
- **`scripts/PROCESS_LASSO_SETUP.md`** — guide for the persistent
  alternative.  Process Lasso configured once survives reboots
  and process restarts; the script needs re-running each session.
- **`scripts/README.md`** — operator-facing overview of the problem
  + when to use each tool.

Companion changes:

- **`build.bat` rewritten** to always log + show last 50 lines of
  output + clear OK/FAIL banner + always pause.  No more silent-
  close on early error.  `build_log.txt` + `build_log.prev.txt`
  capture every run.
- **TROUBLESHOOTING.md** gains "I get a lag spike every time I
  alt-tab" section pointing at the scripts.
- **PLAN.md → Phase 2 → Move C** documents the v0.12 plan to bake
  this into Oblivion's Config tab as a single toggle so users
  don't have to run PowerShell.

No Python code changes — APP_VERSION bumped to mark the repo
state.  183/183 tests still green.

---

## v0.11.13 — 2026-06-03 (CS2 console.log freshness + frame-drop flagging)

Two more triage-quality wins from the Way-3 paste analysis:

**1. CS2 console.log staleness now obvious.**  Previously the section
header showed `mtime 23:19:50` with no age context.  A reader could
spend 30 seconds skimming what they thought was current-session log
lines, then realise the data was 2 days old.  Now:

  TL;DR line:   `⚠ cs2_log   2.6 days ago — NOT current session (read context carefully)`
  Section head: `source: ...console.log (285.2 KB, mtime 2026-06-01 23:19:50 — 2.6 days ago  ⚠ NOT current session)`

Age string is human-readable: `15s ago` / `12m ago` / `3.4h ago` /
`2.6 days ago`.  Sessions <1h count as current; older flags as stale.

**2. Frame-drop warnings now flagged + counted.**  The CS2 server
emits `UNEXPECTED LONG FRAME DETECTED: 29.8ms elapsed...` when it
falls behind tick rate (target ~7.8ms for 128-tick).  Previously
buried in the 200-line tail.  Now:

- Added to the CS2-specific anomaly regex → `>` prefix on each line
  for fast visual scan.
- Counted near the section top: `frame_drop_warnings: 47 'UNEXPECTED
  LONG FRAME' line(s) in last 200` — actionable number for triage
  (a handful on a healthy server is normal; 50+ during a session
  means the Warcraft v0.9.2.1 dispatcher needs another look or the
  host is starved).
- Also added `Cannot find map`, `host_workshop_map.*not found`, and
  `matchzy_loadmatch` to the CS2 regex — these are the high-value
  signals operators ask about when triaging.

App-log anomaly regex unchanged (these patterns are CS2-specific).
Total: 183/183 still green.

---

## v0.11.12 — 2026-06-03 (plugin-verification false positive on stale manifest)

Third bug surfaced by the first Way-3 real-run paste: the plugin
file verification section flagged the Warcraft DLLs as `⚠ MISSING`
on a freshly-rebooted box where the operator was in Competitive
mode but the manifest still recorded Warcraft as the last deploy.

Diagnosis: this is the correct, healthy state — Oblivion's
mode-switch logic undeploys the previous mode's plugins before
deploying the new one's.  Manifest's "last deploy" record was
stale-but-correct.  The verifier was naively reading the manifest
and reporting missing files as failures.

Fix: cross-check `manifest.mode` against `core.current_mode`.  If
they don't match, the operator has switched modes since the last
deploy, undeploy is the expected behaviour, and reporting
"MISSING" misleads triage.  Replace with a clear status line:
```
(manifest stale — last_deploy=Warcraft, current_mode=Competitive;
 undeploy on mode-switch is expected behaviour, not verifying)
```

If `current_mode == manifest.mode` the verifier still runs and
catches the actual deployed-but-missing failure mode (someone
deleted addons/, plugin update half-applied, etc.) — which is the
case it was built for in v0.11.9.

183/183 still green.  No new tests — change is in the snapshot's
status-string branch; existing tests cover the verifier-runs path.

---

## v0.11.11 — 2026-06-03 (two diag-snapshot bugs surfaced on first real run)

Caught by the Way-3 smoke-test paste from a real .exe deployment (the
whole point of v0.11.4-v0.11.10 was to make these defects findable
quickly — system worked).

1. **TL;DR disk scan silently "(could not check)"** — the scan ran
   before `_CONFIG_FILE` was imported into the function's local
   namespace, so the `os.path.dirname(_CONFIG_FILE)` raised
   `NameError`, got swallowed by the bare `except`, and produced the
   useless fallback message.  Bug-fix: local import at the top of the
   disk-check block.  Also raised the warn threshold from <1 GB to
   <5 GB (Windows misbehaves below 2 GB; 5 GB gives genuine headroom).

2. **Discord shows `connected as ?` instead of bot name** — the
   `bot_status()` return shape exposes a `user` key (joined
   `username#discrim` string), not `name`.  Snapshot's TL;DR + detail
   section were both looking for the wrong key.  Bug-fix: use `user`.
   Also use `'(name unresolved)'` as a friendlier fallback than `?`
   for the brief window between `bot.connected = True` and the
   Discord gateway resolving the bot user object.

Real-run snapshot before/after:
  before: `⚠ discord   connected as ?` + `· disk (could not check)`
  after:  `✓ discord   connected as Oblivion#1234` + `✓ disk 88.7 GB free`

No new tests — the existing TL;DR test in test_veto_api covers the
section header + recent-anomaly count; the Discord/disk lines aren't
test-assertable from the integration suite without forging the bot
runner state.  Manual end-to-end paste covered it.

183/183 still green.

---

## v0.11.10 — 2026-06-03 (diagnostic snapshot — triage optimization)

Reader-experience pass on the diagnostic snapshot.  Three changes
that materially speed up Friday-night triage:

1. **TL;DR auto-scan block at the top** — 6-line health summary
   with `✓ / ⚠ / ·` icons.  A reader can grok the snapshot's
   verdict in 2 seconds and skip to the relevant section:
   ```
   ─── TL;DR (auto-scan) ───
     ✓ app       running v0.11.10, frozen
     ⚠ server    booting on de_inferno (5v5) — not ready yet
     ⚠ veto      state=links, captain B unclaimed for 8min
     ✓ discord   connected as Oblivion#1234
     ✓ disk      82.3 GB free at config dir
     ⚠ recent    2 error/warn lines in last 50 app-log entries
   ```
   Auto-detects stuck-state heuristics: captain not claimed > 5min,
   stuck-at-finale > 5min, disk < 1 GB, error-marker count > 0.

2. **Log line anomaly prefixing** — both the app log (last 80) and
   CS2 console.log tail (last 200) now prefix lines matching
   `[error]|[warn]|[fail]|exception|traceback|failed|denied|crashed
   |timeout` (case-insensitive) with `> ` instead of the usual
   `  ` two-space indent.  Visual scan finds problems in ~1s
   instead of 30s of line-by-line reading.

3. **Empty-section collapse** — Discord-not-configured (was 6
   lines, now 1), no-active-session raw-JSON (was a multi-line
   header + read-attempt, now one line).  Healthy-state snapshots
   are noticeably shorter without losing detail when there IS
   something to report.

Tests +1 (TL;DR header + recent-error count + per-line flagging
verified against planted log lines).  test_veto_api 80 → 81.
Total: 182/182 → **183/183**.

---

## v0.11.9 — 2026-06-03 (diagnostic snapshot — fill the gaps)

Self-audit pass on the v0.11.4 diagnostic snapshot revealed real
gaps for Friday-grade troubleshooting.  Five new sections close
the gaps; two new tests pin the additions.

Added to `/api/diag/snapshot`:

1. **CS2 server console.log tail** (last 200 lines) — the #1 most
   useful artifact when the *server* (not the tool) is misbehaving.
   MatchZy errors, plugin crashes, RCON failures, map load failures
   all surface here.  Reads efficiently via 64 KB tail-seek.
2. **Plugin file verification** — calls `_verify_plugin_files()` on
   the currently-deployed plugins.  Catches the "deployed-but-
   missing" silent failure mode (manifest says X is deployed; X's
   files have actually been deleted by something).
3. **Active veto session raw JSON** — the decoded-view section was
   human-friendly but masked schema-corruption issues that round-
   trip through serialize/deserialize.  Raw form catches those.
   Captain token values are masked inline (`***REDACTED***`) so a
   pasted snapshot can never leak a live captain token.
4. **Disk free space** at config dir, csgo dir, server dir.  Low
   disk causes workshop downloads, log saves, and MatchZy config
   writes to fail silently.
5. **Request context** — User-Agent of the requesting browser
   (helps debug clipboard / popup / SSE issues that are
   browser-specific) plus remote_addr and confirmation the
   local-only gate passed.

Tests +2 (sections present + captain tokens redacted in raw JSON).
test_veto_api 78 → 80.  Total: 180/180 → **182/182**.

TROUBLESHOOTING.md updated to reference the new sections.

---

## v0.11.8 — 2026-06-03 (mode-select category tinting + plugin labels)

Companion to v0.11.7 (map dropdown tinting).  The 16-mode flat
list now reads as two intentional categories with plugin
attribution per option.

**Vanilla CS2** (5 modes — no plugins, runs pure Valve):
- Competitive, Casual, Wingman, Arms Race, Demolition
- 7% accent tint (strongest — canonical experience)

**Plugin-enhanced** (11 modes — Oblivion auto-deploys the plugin):
- Practice / 3v3 / 4v4 / 5v5 · MatchZy
- 1v1 / 2v2 · K4-Arenas
- Retakes · B3none
- Jailbreak · CSS-Jailbreak
- Warcraft · CS2-Warcraft
- Deathmatch · MetaMod (restart on switch)
- Zombie Escape · MetaMod (restart on switch)
- 4% accent tint (medium — Oblivion's added value)

The `· pluginName` suffix mirrors the map picker's recommended-mode
suffix pattern.  The `(restart on switch)` hint on MetaMod modes
surfaces the operational cost BEFORE picking, not as a surprise
toast after.

Framing rationale: avoided "Official" vs "Plugins installed"
because that demotes the plugin modes (Warcraft / ZE are the
marquee features) and implies an installation step that doesn't
exist (Oblivion auto-deploys).  "Vanilla CS2" + "Plugin-enhanced"
honours both.

Defensive: any backend mode not recognised by the client-side
`_MODE_CATEGORY` table falls into an "Other" optgroup with plain
tint, so adding a new mode server-side never breaks the SPA.
v0.12 plugin-registry refactor will replace this table with
`drivers/cs2/modes.json` data — see PLAN.md.

180/180 tests still green.  Pure visual + label change.

---

## v0.11.7 — 2026-06-03 (map-select category tinting)

Small visual hierarchy improvement to the Map picker dropdown.
Three optgroups (Official Maps, Workshop — Recommended for X,
Workshop — Other) now read at a glance instead of as a flat
single-column scroll of map names.

- Optgroup labels: bold + accent-coloured + subtle accent
  background tint.
- Per-category option tint, scaled by canonicity:
  - **Official Maps**: 7% accent tint (strongest)
  - **Workshop — Recommended for X**: 4% accent tint (medium)
  - **Workshop — Other**: plain `--bg-1` (visual default)

Uses `color-mix(in srgb, var(--accent) X%, var(--bg-1))` so the
operator's chosen accent (Appearance: Purple / Blue / Teal /
Green / Orange / Red) automatically retints the dropdown —
no extra plumbing per palette.

Scoped to `#map-select` so other native selects (mode picker,
preset dropdown, RCON history) stay unchanged.

No tests added — pure visual change.  180/180 still green.

---

## v0.11.6 — 2026-06-03 (running version visible in the status bar)

Companion to v0.11.5.  The version was visible via curl on
`/api/ping` (v0.11.5) and inside the diagnostic snapshot (v0.11.4),
but nowhere in the running SPA itself — surprisingly easy to lose
track of what build is actually running.

`index.html` template now receives `app_version` from the Flask
render context and pins it into the status-bar right corner as a
quiet monospace badge `v0.11.6`.  Always visible, no auth required
to see it.  Hover lifts the colour + ring to accent.  Sits next to
the existing remote-web-URL link without competing with the live
status data (map / mode / uptime / IPs).

The pre-existing `app-update-badge` ("⬆ App X.Y.Z") still appears
in the header when GitHub Releases has a newer tag — that's the
LATEST available, not the running.  Now both are visible without
ambiguity:
  status bar `v0.11.6` = what's running
  header `⬆ App 0.12.0` = what's available

180/180 tests still green.  No new tests — template variable
addition; existing `/` reachability test (test_v092) already
covers the SPA shell render.

---

## v0.11.5 — 2026-06-03 (version visible from /api/ping)

Tiny utility addition.  Previously, finding out which version of
Oblivion was running required either logging in (`/api/state` is
auth-gated) or local-admin access (`/api/diag/snapshot` is
`@require_local`).  Made it hard to answer the simple question "is
the .exe currently running the latest build?" from outside the
app.

`/api/ping` now returns:
```
{ "ok": true, "version": "0.11.5", "build": "frozen" }
```

`build` field distinguishes a frozen .exe deployment from a dev
`python main.py` run.  Version is not sensitive (it appears in
CHANGELOG, GitHub releases, the SPA header on a self-update).
Unauthenticated by design — anyone hitting the panel from
localhost or the tunnel can confirm the version with a one-line
curl.

No tests added (existing test_v092 covers /api/ping reachability;
the new fields are additive and don't break the existing
contract).  180/180 still green.

---

## v0.11.4 — 2026-06-03 (diagnostic snapshot + troubleshooting doc)

**One-click diagnostic snapshot.**  Pre-Friday tooling so when
something breaks during a live session, the operator can paste a
single text blob into a support channel and get fast triage.

- **`GET /api/diag/snapshot`** (admin local-only) returns a single
  `text/plain` blob with: app version + build type + OS, server
  status (running, boot state, map, mode, uptime, public IP),
  active veto session state fully decoded (mode, teams, captains,
  ban/pick sequence with position marker, decider, ready flags,
  token usage), plugin manifest, Discord bot status, persistence
  file inventory (paths + sizes + mtimes), **last 80 lines of the
  app log ring buffer**, and the config with sensitive values
  masked (PINs, passwords, bot token, webhook URL).
- **Config → Troubleshooting card** with a single **🔧 Copy
  diagnostic snapshot to clipboard** button.  Fetches via
  `api.diagSnapshot()`, copies to clipboard with toast feedback;
  falls back to opening the snapshot in a new tab when clipboard
  is blocked (Edge WebView2 occasionally does).
- **`TROUBLESHOOTING.md`** — new operator-facing doc with the
  one-button path, log locations table, quick triage guide for
  common breakages (captain link broken, server won't start,
  MatchZy didn't load, DM didn't arrive, embed didn't post, app
  crashed, tunnel rotated), and a list of greppable `[tag]`
  markers.

Tests: +3 integration tests (snapshot returns expected sections
with log marker; secrets redacted; @require_local gate enforced
for non-local sessions).  test_veto_api 75/75 → 78/78.  Total:
**177/177 → 180/180**.

---

## v0.11.3 — 2026-06-03 (session persistence)

**Active veto sessions now survive app restart.**

The risk it solves: operator accidentally Ctrl+Q's mid-session, or
Windows installs an update overnight, or pywebview crashes — the
in-progress veto state (claimed tokens, partial ban/pick sequence,
captain ready flags, MatchZy config) was previously gone.  Now the
app reopens to exactly where it was.

Implementation:
- `cs2servergui/veto.py`: `serialize_session(s) -> dict` +
  `deserialize_session(d) -> VetoSession`.  Uses `dataclasses.asdict`
  for serialization (future field additions survive automatically);
  defensive deserialization tolerates unknown / missing fields
  without crashing (forward-compat).
- `cs2servergui/web.py`: every state mutation already routes through
  `_veto_broadcast()`; that's now also the persistence choke-point.
  Atomic write (tmp + `os.replace` + `fsync`) mirrors `save_config`
  / `_save_to_match_history`.  Persistence failure logs + moves on —
  NEVER breaks a live session.
- `cs2servergui/core.py`: `_load_active_veto_session()` runs at
  `AppCore.__init__` (after `_load_config` so `self.log` is wired).
  Silently discards files older than 12h, idle-state files, or
  corrupt JSON — operator gets a log line, app starts clean.
- `cs2servergui/config.py`: `VETO_ACTIVE_FILE` under `%APPDATA%`,
  `VETO_ACTIVE_MAX_AGE_SECS = 12 * 3600`.

Token claim state survives the round-trip — a captain who claimed
their link before the restart is still bound to their `caller_id`,
they don't lose their session.

Tests: +4 unit tests for round-trip + token-claim preservation +
mid-veto sequence preservation + defensive deserialization.  167/167
total (28 v092 + 74 veto + 65 veto-api).

The active-session file is `oblivion_veto_active.json` under
`%APPDATA%\Oblivion Server Tool\` (alongside the existing match
history file).  Auto-cleaned on `/api/veto/reset` and on session
completion.

---

## v0.11.2 — 2026-06-03 (pre-Friday hotfix + strategy doc)

**Bug fix: `issue_tokens` idempotency.**  Previous behaviour: calling
`issue_tokens` a second time from `links` ROTATED both captain
tokens.  Realistic trigger — a captain refreshes their browser on
the links page, or the operator double-taps "Generate captain
links."  The captain who already opened their link kept working
(claim binds the token), but the OTHER captain's shared URL was now
dead with no warning — operator only found out when the second
captain reported "your link doesn't work."

Fix: `issue_tokens` now returns the existing dict unchanged when
tokens exist for the session, including when one captain has
already claimed theirs (rotating a claimed token would log that
captain out mid-flow).  Per-team rotation remains available via
`revoke_token('A')` / `revoke_token('B')` — those leave the other
captain's link alive.

Pattern mirrors v0.11.1's `issue_spectator_token` (idempotent) +
`rotate_spectator_token` (explicit) split.

**+2 unit tests**, original pinning test flipped to assert new
behaviour.  test_veto 70/70, total **163/163**.

**Also in this release: PLAN.md** — strategic roadmap to v1.0.
Names the two-audience strategy (Average Joe + Pro server hoster),
elevates Plugin Manager UX to the headline differentiator, adds
Linux + headless + Docker as Phase 3.5, locks in BSL license model
+ donations-only monetization for v1.0.  Doesn't dictate code;
sets direction.  See PLAN.md.

---

## v0.11.1 — 2026-06-02 (polish release)

Post-v0.11.0 polish sweep — Tuesday work toward Friday's live test.
Eight discrete enhancements + a mobile-validation checklist.  All
back-compat; no schema or state-machine changes.  Tests: 161/161
green (was 147 at v0.11.0).

  1. **Discord test buttons** — Config card gains "Test Embed" + "Test
     DM" buttons (local-only) so operator can verify bot wiring
     without running a full veto.  Backed by /api/discord/test_embed
     and /api/discord/test_dm.

  2. **Match history modal** — new "📜 History" button on the Veto
     header opens a modal listing the last 10 completed matches
     (date, teams, captains, decider-tagged maplist) from the
     /api/veto/history endpoint that already existed in v0.10.2.

  3. **"Go Online" banner** — Veto-idle stage gets a coloured banner
     reading the public_share_url config: green "Online" with masked
     URL + Copy/Open buttons, or yellow "LAN-only" with a
     one-click jump to Config that focuses the URL input.  Operator
     no longer has to remember whether the tunnel is wired.

  4. **Bulk paste** — "Paste 10 names" now accepts `Name`,
     `Name,SteamID64`, or `Name,SteamID64,DiscordID` per line
     (comma/tab/semicolon delimited).  Toast reports how many of
     each ID column got extracted, so a wrong column is visible
     without scanning the grid.

  5. **Roster presets** — Save/Load preset controls on the Roster
     stage backed by localStorage (per-browser; single-machine
     operator per MEMORY).  Save names a 10-player roster; Load
     overwrites the input; ⚠ Delete sentinel keeps the destructive
     action off the main UI.

  6. **MatchZy cvar editor** — Config tab adds a key/value row
     editor (local-only) for matchzy_* cvars.  Values merge over
     the built-in defaults (`mp_warmup_pausetimer=0`,
     `matchzy_minimum_ready_required=2`) at finale time; operator
     wins on conflicts; blank value actively suppresses a default.
     `veto.build_matchzy_config` gains optional `cvar_overrides=`
     param (old signature preserved).  4 new unit tests.

  7. **Spectator URL** — read-only veto link for casters/observers.
     POST /api/veto/spectator issues a per-session token (idempotent;
     {rotate:true} mints fresh).  GET /api/veto/spectator/state is
     token-gated (no cookie required — token IS the auth) and serves
     a sanitized snapshot: Discord IDs omitted, SteamIDs masked
     (first 4 + last 4), captain tokens + matchzy_config absent.
     GET /spectate serves a standalone HTML page that polls every
     3s — no SPA, no auth flow, works in OBS browser sources.  Token
     chars sanitised before HTML embed (XSS defense in depth).  New
     "📺 Spectator" button on the Veto header opens a modal with
     LAN + Public URLs + Copy + Rotate (with confirm).  10 new tests.

  8. **Mobile validation checklist** — `MOBILE_CHECK.md` ticks
     through what to verify on an actual phone (hamburger drawer,
     SSE re-arm on background/foreground, captain handoff via DM
     link, reduced-motion).  Pre-Friday gate, not a code change.

### What's parked

  * **Cinematic finale animation rewrite** — user-parked ("skip
     animation for now").  Still on the deferred list.

---

## v0.11.0 — 2026-06-02 (release)

**Discord bot integration (Layer 1).**  Four-day push for the optional
Discord bot.  Operator runs their own bot bound to their own Discord
server (see DISCORD.md for the 5-min setup).  When configured, the tool
gains three online-primary workflow wins:

  1. **Auto-DM captain links** (Layer 1A) — when /api/veto/tokens mints
     captain tokens, the bot DMs each elected captain their join URL.
     Operator no longer copy-pastes one link per captain into Discord.

  2. **Voice-channel roster pull** (Layer 1B) — new "🎤 Pull from voice
     channel" button on the Roster stage opens a modal listing every
     voice channel in the operator's server.  Pick a channel; roster
     grid auto-fills with the connected members' display names + Discord
     IDs.  Operator only types SteamIDs by hand.

  3. **Live veto embed** (Layer 1C) — when discord_veto_channel_id is
     configured, the bot posts an embed in that channel as soon as the
     veto starts, then EDITS the same message on every ban/pick.
     Spectators watch the match form in real time.  Embed turns green
     on finale with "✅ MATCH LOCKED IN".

The bot is fully optional — every feature degrades silently when no
token is configured, and the existing Copy-for-Discord / manual roster
entry workflows still work.

### Day-by-day

**Mon — Bot scaffolding** (`cs2servergui/discord_bot.py`, ~280 lines)
  * discord.py 2.3+ added to requirements + build.bat collect-all
  * Dedicated daemon thread owns the gateway connection + asyncio loop
  * Flask threads talk to it via `asyncio.run_coroutine_threadsafe`
    with per-call timeouts
  * 5 public actions: `start_bot`, `stop_bot`, `bot_status`,
    `bot_dm_user`, `bot_voice_members`, `bot_voice_channels`,
    `bot_post_embed`, `bot_edit_embed`
  * Lifecycle: starts on AppCore init if token configured; restarts on
    token change via Config.  LoginFailure → 30s retry loop.
  * New Config card "Discord (v0.11.0 bot integration)" — local-only.
    Token + guild ID + (optional) channel ID inputs + live status line
    "✓ Connected as botname#1234" / "… Connecting" / "○ Not configured"
  * **DISCORD.md** — 8-step operator runbook (create app, intents,
    OAuth URL, invite, dev mode, paste tokens, troubleshooting)

**Tue — Layer 1A (DM captain links)**
  * RosterPlayer gains optional `discord_id` field (32-char cap)
  * /api/veto/roster + snapshot serialise the new field
  * SPA roster grid: new 4th column "Discord ID (auto-DM, optional)"
    with digits-only constraint (`inputmode=numeric`, strip-on-input)
  * /api/veto/tokens internally calls `_attempt_captain_dms` after
    minting — resolves captain IDs from elected RosterPlayers, sends
    a captain-addressed DM per team via `bot_dm_user`
  * Response includes `dm_sent: bool` per team
  * SPA link card: new "📨 DM SENT" accent pill on the right when
    `dm_sent=true`, replaced by CLAIMED pill once captain claims
  * Mid-day fix: SPA roster hydration was dropping `discord_id` from
    the snapshot projection — SSE re-renders would clobber the
    operator's typed input.  Three call sites fixed (hydration +
    demo + paste buttons).
  * Same commit added diagnostic logging — every silent fall-through
    in `_attempt_captain_dms` now emits exactly one `[discord] Layer
    1A:` line so a missed DM is triagable from the log alone.

**Wed — Layer 1B (voice-channel roster pull)**
  * Two new HTTP routes:
    - `GET /api/discord/voice_channels` → `{channels: [...]}`
    - `GET /api/discord/voice_members?channel_id=…` → `{members: [...]}`
  * Admin-role (NOT local-only) — voice channel + member names are
    already public to anyone in the server
  * SPA: new "🎤 Pull from voice channel" button on Roster stage
    alongside "Demo names" / "Paste 10 names"
  * Modal lists every voice channel with live member counts
    - Disabled (greyed) for empty channels
    - .ready class + green border for channels with exactly 10 members
  * Pick a channel → roster grid overwrites with
    `[{display_name, discord_id, steam_id:''}, ...]` for the connected
    members.  SteamIDs still typed by hand (Discord doesn't expose them).

**Thu — Layer 1C (live veto embed) + ship**
  * VetoSession gains `live_embed_msg_id: str` field (cleared by
    reset).  Storing the message ID lets us EDIT the same Discord
    message on every step rather than spamming a new one per ban/pick.
  * New `_build_live_veto_embed(session)` renders a rich embed:
      Title:        "🎮 BO3 · Team Alpha vs Team Bravo"
      Description:  "⏳ Team Alpha to BAN  (step 1/6)"   ← yellow
                    "✅ MATCH LOCKED IN — get ready to battle"   ← green
      Field: Map veto
        ❌ `de_mirage    ` banned by **Team Alpha**
        ✅ `de_inferno   ` picked by **Team Bravo**
        🏁 `de_nuke      ` **decider**
        ⬜ `de_anubis    ` —
      Field: Captains
        **Team Alpha** — Phoenix
        **Team Bravo** — Cypher
      Footer:       matchid: oblivion-veto-1780...
                    (+ "maplist: A → B → C" on finale)
  * `_refresh_live_veto_embed()` helper called at three hooks:
    - Captain claim that flips state to `veto` (initial post)
    - Every `/api/veto/step` (edit with new map state)
    - `/api/veto/finale` (edit to "LOCKED IN" + maplist footer)
  * Fire-and-forget on a daemon thread; no-op when no channel
    configured or bot offline
  * Embed message ID survives state transitions until reset() — operator
    can leave the embed in the channel as match history after the BO

### Tests — 147/147 green at v0.11.0 release (was 144)
  test_v092.py:      28
  test_veto.py:      61  (+2 for live_embed_msg_id field + reset)
  test_veto_api.py:  58  (+1 for perform_step works without channel)

### Version + build
  APP_VERSION 0.10.2 → 0.11.0
  installer.iss MyAppVersion 0.10.2 → 0.11.0
  build.bat: --collect-all discord + --hidden-import cs2servergui.discord_bot

### Explicitly NOT in this release
  * Layer 2 (full in-Discord veto via bot buttons) — out of scope,
    fragments the auth model + doesn't actually improve captain UX
  * Per-operator bot hosting — every operator runs their own bot
    against their own Discord, by design (no shared infra)
  * Slash commands (`/veto-pull`, `/status`, etc.) — current SPA-driven
    flow doesn't need them; reserved for v0.11.x if a real use case
    surfaces

---

## v0.10.2 — 2026-06-01 (release)

Audit-driven online-primary polish phase.  Four focused days addressing
~35 findings from a five-agent audit (mobile responsiveness, online
workflow, feature integration, pre-v0.10.0 surface, cross-cutting
concerns).  137/137 backend tests green at release.

### Day 1 — Mobile + captain connect handoff + mode pre-flight
* Single `@media (max-width: 640px)` block (~190 lines): sidebar→
  hamburger drawer, popovers clamped to viewport, 44/48 px touch
  targets, `clamp()` on big finale titles, `visibilitychange` SSE
  re-arm, `@media (hover: none)` kills stuck-hover, `@media
  (prefers-reduced-motion)` kills the cinematics for vestibular users.
* Captain finale embeds `connect <ip>; password X` + Copy connect +
  Copy team-invite buttons — the workflow gap that left captains
  unable to tell their team where to join.
* `/api/veto/finale` mode pre-flight refuses 409 if server isn't on a
  MatchZy mode (Practice / 3v3 / 4v4 / 5v5 / Competitive).  `force:
  true` body field bypasses.  `load_match: false` skips the check
  (preview mode).

### Day 2 — Pre-flight errors + local-only signposting + role pill
* `_preflight_checks` now returns `(ok, errors)`.  `/api/server/start`
  returns 422 with `preflight_errors` list instead of silently 200 OK
  + log-only output.
* `boot_error` field in `/api/state`; SPA renders dismissable red
  banner at top of `#content` when set.
* Local-only UI guards: hide CS2-update / app-update badges + log
  drawer (for non-admin) + log Save button + LAN IP row for non-local
  viewers.
* App self-updater silently swallows GitHub 404 (private repo) instead
  of logging noise every check interval.
* New `#hdr-role-pill` next to state pill — shows admin / guest /
  captain with team letter; coloured per role.

### Day 3 — Unified SSE transport + /api/capabilities + retry layer
* New `_oblivionSSE` shared module replaces two divergent reconnect
  strategies (log capped at 12 retries; veto fixed 5 s).  Exponential
  backoff (1→2→4→8→16→30 s capped), online/visibilitychange re-arm,
  aggregate health status, header pill ("Live" / "Reconnecting…" /
  "✗ Offline") so users distinguish quiet from broken.
* Existing log + veto SSE refactored to use the shared module.
* New `/api/capabilities` returns `{role, is_local, can: [tags…]}` —
  single source of truth for role-aware UI.  Allowlisted for
  guest + captain.
* `api.js` retry layer: 10 s AbortController timeout, one retry on
  network failure or HTTP 502/503/504 (Cloudflare tunnel hiccup).
  Errors carry `.status / .body / .network`.
* `/api/state` poll interval 3 s → 10 s.  At 7 users that's 140 RTT/min
  → 42/min through the tunnel.  Plus immediate-poll on visibility
  restore for fast catch-up on phone wake.

### Day 4 — Polish + history + Discord webhook + ship
* **Captain limbo screen.**  Pre-veto stages get a contextual heading
  + progress (e.g. "Players in: 7/10" during roster, "Team votes: A
  3/5, B 5/5" during voting) instead of the generic "Current stage:
  voting" placeholder.
* **Rematch button.**  New `veto.rematch()` + `/api/veto/rematch`.
  Preserves team rosters + names + captains + map pool; clears tokens
  + sequence + ready flags.  State machine gains a
  `complete → links` transition specifically for this code path.
  Optional `mode` + `map_pool` overrides.  SPA Complete page shows
  "🔄 Rematch (same teams) →" alongside "Start a new session".
* **Match history.**  Last 10 finales persisted to
  `oblivion_matches.json` (atomic write + thread-safe append).
  `veto.archive_to_history()` serialises matchid + teams + players +
  maplist + decider + veto sequence.  New `GET /api/veto/history`
  returns the list.
* **Discord webhook on finale.**  New `discord_webhook_url` config
  field (local-only write; remote round-trip masked as `***`).  When
  set, finale POSTs an embed to the channel: title with mode + teams,
  maplist with decider tagged, captains, MatchZy status, connect
  command for spectators.  Background thread + 10 s timeout — failure
  doesn't block the finale.

### Version + build
* `APP_VERSION` 0.10.1 → 0.10.2
* `installer.iss` MyAppVersion 0.10.1 → 0.10.2
* `OblivionServerTool.spec` unchanged from v0.10.1 (segno still
  `collect_all`'d for QR codes)

### Test totals at v0.10.2 release
* `tests/test_v092.py` — 28/28 (Day 2 + 3 added 6 cases: preflight 422,
  boot_error, captain_team, last_start_error clear, capabilities admin,
  capabilities guest)
* `tests/test_veto.py` — 58/58 (Day 4 added 4: rematch preserves teams,
  rematch legal-only-from-complete, rematch mode switch, archive shape)
* `tests/test_veto_api.py` — 51/51 (Day 1 added 4 mode pre-flight cases)
* **All 137/137 green**

### Explicitly cut from v0.10.2 (deferred or won't-do)
* Animation rewrite (parked at operator's request)
* "Go Online" header panel with cloudflared command generator
* Public read-only spectator URL
* Roster presets save/load
* MatchZy cvar editor (overtime / max-rounds)
* Bulk SteamID paste
* Tournament brackets
* iOS Safari < 16.2 graceful fallbacks
* Full Discord bot (= v0.11.0; the webhook in Day 4 captures most of
  the immediate value without the gateway complexity)

---

## v0.10.2 — Build journal (the in-progress writeups before release tag)

**The "make-it-pleasant-online" phase.**  After v0.10.0 + v0.10.1 shipped the
veto feature and the Cloudflare-friendly captain handoff, a five-agent audit
(mobile responsiveness, online workflow, feature integration, pre-v0.10.0
surface, cross-cutting concerns) surfaced ~35 actionable findings: ~10 blockers,
~17 gaps, ~8 polish items.  v0.10.2 took the BLOCKERs from all five audits
+ the top three cross-cutting investments + the most-valuable workflow gaps
into a single release, scoped to four working days so Friday is real testing
not finishing.  Daily prose below; release rollup is at the top of the file.

### Day 1 (Mon) — Mobile + two workflow blockers
Mobile responsiveness was identified as **fundamentally broken** by the audit
(only 2 `@media` queries in 2856 lines of CSS, none below 700 px; sidebar
permanently 192 px wide; login card + connect popover overflow iPhone-width
viewports; captain READY button requires scroll on 375 px width).  Day 1
adds a focused `@media (max-width: 640px)` block:
* sidebar collapses to a hamburger drawer
* `.login-card`, `.connect-popover`, `.palette` clamped to
  `min(380px, calc(100vw - 16px))`
* all `.btn` min-height 44 px (Apple HIG touch target)
* `clamp()` on big finale titles so they don't overflow
* `visibilitychange` SSE-reconnect handler — without it, phone screen-lock
  permanently kills the veto stream after one minute

Plus two workflow gaps:
* **Captain finale page embeds `connect <ip:port>; password X`** with a
  one-click "Copy invite for team" button.  Was missing entirely — captains
  finished veto + ready but had no way to tell their team where to join.
* **`/api/veto/finale` refuses with helpful error if server isn't in
  MatchZy mode** (3v3 / 4v4 / 5v5 / Competitive).  Was silently writing
  the config and firing the RCON regardless; matches played under wrong
  ruleset.  New response shape carries `matchzy.precheck.{ok, mode, want}`.

### Day 2 (Tue) — Pre-flight error surfacing + local-only signposting + role pill
The audit caught a pattern: many endpoints return 200 OK before doing the
actual work, then log the real status to the in-app buffer.  Remote admin has
no signal.  Day 2 fixes the worst cases:
* `/api/server/start` returns 4xx with the preflight reason when the boot
  can't begin (port held / plugin missing / Steam creds expired / etc.)
* `boot_error` field in `/api/state` so a stuck post-spawn boot becomes
  visible to remote viewers
* App self-updater swallows GitHub Releases 404 silently (the repo is
  private; anonymous GitHub API returns 404; the badge was firing pointless
  log lines on every poll)
* CS2-update + App-update badges + CS2 server-update modal hidden from
  non-local sessions (clicking from remote hit `@require_local`'s 403)
* Log drawer hidden for guest-role users (their `EventSource` was 401-ing
  in a 12-retry loop, hammering the endpoint for ~60 s)
* **Role pill in header** showing `admin` / `guest` / `captain` so remote
  users have visual confirmation of their session role
* LAN IP row hidden in status bar + Connect popover for `!is_local`
  viewers (they'd otherwise see `connect 192.168.x.x:27015` that can't
  possibly work from the internet)

### Day 3 (Wed) — Cross-cutting investments (the structural fixes)
Three changes that touch multiple features at once and pay off across the
whole tool:

1. **Unified SSE transport** (`_oblivionSSE` shared module).  Replaces the
   two divergent SSE reconnect strategies (log drawer caps at 12 retries
   then dies; veto stream uses fixed 5 s and only while `currentPage ==
   'veto'`).  New module uses exponential backoff (1→2→4→8→30 s capped),
   re-arms on `online`/`visibilitychange` events, and emits a header
   status pill (`Live` / `Reconnecting…` / `Offline`) so users know the
   difference between "quiet" and "broken."
2. **`/api/capabilities` endpoint** + consistent role-aware affordances.
   Returns `{role, is_local, can: [...]}`.  Local-only buttons (Steam
   login, install, RCON console, log save, scan, cmdfilter override,
   directory picker) render `disabled` with a `title="Local only — ask
   the host"` tooltip instead of being clickable-but-403-on-click.
3. **`api.js` retry / timeout layer.**  Single bottleneck: 10 s
   `AbortController` timeout, one retry on network error (NOT on 4xx),
   sticky error toasts for failures, auto-dismiss only for success.
   Fixes the "one network blip = silent stuck UI" pattern caught by the
   cross-cutting audit.

Plus: push `/api/state` over SSE instead of polling.  Previously every
client polled every 3 s — 7 connected users meant 140 round-trips per
minute through the tunnel.  After Day 3 most clients receive state via
SSE and the polling interval moves to 30 s as a fallback only.

### Day 4 (Thu) — Workflow polish + history + Discord webhook + ship
Final batch of pleasant additions, then full regression + tag.

* **Captain limbo screen** — when a captain joins before voting is
  resolved, show "Operator is collecting votes — Team A: 3/5 in" instead
  of the previous "Current stage: voting" placeholder
* **Rematch (same teams)** button on the Complete page.  Preserves
  `team_a`, `team_b`, both team names; resets vote/links/veto/sequence
  state for a fresh BO with the same 10 players.  Saves retyping 10
  names for repeat-use evenings
* **Last-action attribution** in `/api/state` — `{who, what, when}`
  field showing the most recent state-changing API call + its caller IP
  + 60 s freshness window.  Free audit trail for "who changed the
  hostname" questions
* **Match history** — last 5 completed sessions persisted to
  `oblivion_matches.json` (teams, players, maplist, decider, matchid,
  timestamp).  New "History" pane in the Veto tab shows them; future
  Rematch can also load from history
* **Discord webhook on finale** — operator pastes a webhook URL in
  Config → Veto / Match Setup.  When finale completes, the tool POSTs
  an embed (teams, maplist, decider, connect string) to the channel.
  Captures most of the "spectators see results" value with 20 lines of
  code — full bot stays as v0.11.0
* Full regression (123/123 → target ~145 with new cases), rebuild
  .exe, tag v0.10.2, GitHub release with binary, refresh `pull-latest.bat`

### Explicitly cut from v0.10.2
* Animation rewrite (parked — operator confirmed skip for now)
* "Go Online" header panel with cloudflared command generator
  (operators already have TONIGHT.md)
* Public read-only spectator URL (deferred — adds testing surface)
* OBS-overlay broadcast view
* Roster presets save/load
* MatchZy cvar editor (overtime / max-rounds)
* Bulk SteamID paste
* Tournament brackets
* iOS Safari < 16.2 graceful fallbacks (document minimum instead)
* Magic-link auth replacing PINs
* Limited remote RCON

These either lack obvious value for the immediate online use case, add
significant scope, or are better paired with the v0.11.0 Discord bot.

---

## v0.10.1 — 2026-06-01 (online-primary improvements)

**Captain Ready button + Public URL override + Copy-for-Discord.**  After
v0.10.0 shipped, operator feedback clarified that LAN use is secondary —
primary use is online matches over a Cloudflare tunnel.  This release
addresses the three biggest gaps that surfaced from that lens.

### Captain Ready button (the gap that prompted the release)
v0.10.0's captain finale view showed the admin's "Hand to MatchZy" button,
which captains literally couldn't click — role gate blocked it.  They saw a
button that gave them an error toast.  Fixed:

* **Captain finale view** has its own renderer (`_renderVetoFinaleCaptain`).
  Shows the maplist + decider + opponent's ready state + a big READY toggle.
  Tick to ready up; click again to un-tick.
* **Admin finale view** shows both teams' ready slots in a row.  When both
  green-checkmark, the launch button changes class to `.veto-launch-armed`
  (pulsing green box-shadow) and the label flips to "⚡ Hand series to
  MatchZy →".  Until then it's disabled with "Waiting for both captains…"
* **Admin can ack-on-behalf** by clicking either ready slot — useful when
  a captain went AFK or is on a flaky phone
* **Shift+Click** on the launch button overrides the both-ready gate
* Optional **Config toggle: "Auto-launch when both captains ready"**
  (defaults OFF — admin clicks GO manually so they can verify the server
  is in the right mode first)

### Public URL override (the bigger online-primary fix)
v0.10.0's "Public" captain URL was built from `core.public_ip + port`,
which assumes port-forward.  Operators using Cloudflare tunnel (the
recommended remote-access path per TONIGHT.md) had a URL
that couldn't reach their captains at all.

* New config field `public_share_url` in the new "Veto / Match Setup"
  Config card.  Operator pastes their tunnel URL there (e.g.
  `https://random-words.trycloudflare.com`)
* When set, `/api/veto/tokens` + `/api/veto/revoke_token` + `/api/veto/qr`
  build captain links from THIS base instead of `public_ip + port`
* Falls back to `public_ip + port` when blank (existing behaviour)
* Validates `http://` or `https://` prefix on save; rejects malformed
* Empty string clears it

### Copy-for-Discord button
Each captain link card now has a second copy button that copies a
pre-formatted, captain-addressed message:

```
🎯 Captain Phoenix (Team Alpha) — your veto link:
https://random-words.trycloudflare.com/veto?join=AbC...
Single-use. Click to claim your captain seat.
```

Operator pastes straight into a Discord DM — already addressed, has
context, ready to send.  No actual Discord integration — that's
v0.11.0's job (real bot DM delivery).

### Test additions
* `test_veto.py` — 49 → 54 (+5 unit cases for ready state machine)
* `test_veto_api.py` — 37 → 47 (+10 API cases including captain
  spoof-protection, admin ack-on-behalf, public URL validation)
* 123/123 green total

### Build infrastructure also fixed
* `build.bat` now routes through `python -m PyInstaller` instead of
  bare `pyinstaller`.  Previous machines with multiple Python installs
  (e.g. 3.11 + 3.14) could land on the wrong Python whose site-packages
  doesn't have `segno`, and PyInstaller silently dropped it from the
  bundle.  This is the root cause of QR codes not rendering in the
  v0.10.0 first build.
* `--collect-all segno` (instead of `--hidden-import segno`) bundles
  all 6 segno submodules.  Top-level `import segno` succeeded but
  `segno.make()` raised `ImportError` for `segno.encoder` at runtime
* Defensive `try/except ImportError` around `import segno` in
  `web.py:veto_qr` so any future bundling regression returns useful
  JSON instead of a silent broken-image icon

### `pull-latest.bat` self-service updater (released with v0.10.1)
New repo-root script for operators to grab the latest .exe without
rebuilding.  Uses the operator's authenticated `gh` CLI to fetch from
the private repo's GitHub Release.  Safety: refuses to overwrite a
running .exe; always backs up to `.exe.bak`; uses single-line
`if X goto :label` instead of multi-line `if (..)` blocks (cmd.exe
parser fragility with `:` characters inside parenthesised blocks);
forced CRLF line endings via `.gitattributes`.

---

## v0.10.0.1 — 2026-06-01 (hotfix)

**One bug: captain-link QR codes failed to render in the frozen `.exe`.**

The v0.10.0 build used `--hidden-import segno` which only pulled the
top-level `segno/__init__.py` into the bundle.  When `/api/veto/qr` ran
`segno.make(url)`, segno tried to import `segno.encoder` (and consts /
utils / writers) at runtime — none of which were bundled — and raised
`ImportError`.  Flask returned a generic 500 with no body, the SPA's
`<img src="/api/veto/qr?…">` showed the broken-image placeholder, and
the operator was left guessing.

Two-part fix:
* `build.bat` + `OblivionServerTool.spec`: switched `--hidden-import segno`
  to `--collect-all segno` so all six submodules (`encoder`, `consts`,
  `helpers`, `utils`, `writers`, `cli`) ride along with the package.
* `web.py:veto_qr`: wrapped the `import segno` in a try/except that
  returns the actual error as JSON (`{"error": "QR generator not
  available in this build: ImportError(...)"}`), so future bundling
  regressions of any pure-Python dep show up as a 500-with-body rather
  than the silent broken-image icon.

Test totals unchanged from v0.10.0 (108/108) — the bug was bundling-
side, not test-discoverable without a frozen build to exercise.

---

## v0.10.0 — 2026-06-01 (release)

The **map-veto / match-setup feature** ([VETO.md](VETO.md)).  Seven-day build:
state machine + HTTP API + SPA Veto tab + QR codes + cinematic finale +
real MatchZy handoff + this release polish day.  `APP_VERSION` bumped
0.9.2.1 → 0.10.0.  Total: 108/108 backend tests green (22 v092 +
49 veto + 37 veto-api).

### Day 7 — Polish, edge-case unit tests, release

**Real bug found by adversarial unit tests:** `/api/veto/finale` called a
second time on an already-`complete` session returned 500 (uncaught
`InvalidVetoTransition` from the inner `complete()` call) instead of a
clean 400.  Now guarded at the top of the handler — second call returns
"session already complete — call /api/veto/reset" + 400.  The SPA never
naturally hits this (the launch button is gone on the complete page),
but a stale tab + double-click would have surfaced it.

**Documented (and worth-revisiting) behaviours pinned with tests:**
* `issue_tokens` ROTATES tokens on re-call from `links` — silently
  invalidates URLs already shared with captains if the operator
  refreshes the browser during the links stage.  Test pins the
  behaviour; TODO entry filed to make it idempotent in a follow-up.
* `revoke_token` from `links` before `issue_tokens` mints a token for
  the target team even though no token existed to revoke.  Acceptable
  (the SPA only exposes Revoke after Issue), now documented in test.

**New unit cases (test_veto.py: 34 → 49, +15):**
* BO5 sequence shape + final-map count (the Day 1 tests covered BO1 +
  BO3 only)
* `build_matchzy_config` excludes players without `steam_id` from the
  team dict (MatchZy can't address them; mixed rosters still produce a
  usable config)
* `build_matchzy_config` matchid format = `oblivion-veto-<ts>` prefix
* Revoke pre-issue documented behaviour
* Revoke leaves the other team's token untouched
* Revoke rejects unknown team values ('C', 'a', '', 'AB', 'team_a')
* Issue tokens rotation pin (with TODO)
* `perform_step` rejected after finale state reached
* `complete` rejected from every non-finale state (locks the gate)
* Long names (60 chars) accepted at model layer — display caps belong
  in callers
* Whitespace-name handling (documented — model accepts, HTTP layer's
  filled-count check rejects)
* `perform_step` signature is `(session, team, map_id)` — kind is
  server-derived, no spoofable parameter
* State graph reachability from `idle` to `complete` (regression guard
  for `_LEGAL_TRANSITIONS` edits)
* `reset` from `complete` clears state for a fresh session

**New API cases (test_veto_api.py: 31 → 37, +6):**
* `/api/veto/qr?kind=public` with no `public_ip` configured → 400 with
  useful error (was missing — would have produced `http://:port/...`)
* `/api/veto/finale` second call after complete → clean 400 (fixed
  bug noted above)
* `/api/veto/reset` post-reset state is fully cleared (`session: None`)
* Snapshot shape stability (top-level `state, session` + nested
  `current_step_detail, legal_moves` mid-veto)
* `/api/veto/distribute` before roster saved → 400 (not crash)
* Concurrent `/api/veto/finale` calls serialise via `_veto_lock` —
  session ends `complete` exactly once

**Version bumps:** `config.py:APP_VERSION = "0.10.0"`,
`installer.iss:MyAppVersion = "0.10.0"`.

**Build:** `OblivionServerTool.spec` + `build.bat` have segno and
`cs2servergui.veto` in `--hidden-import` (the lazy `import segno`
inside `/api/veto/qr` and the inline `from . import veto` are invisible
to PyInstaller's static analyser without explicit hints).

---

## v0.10.0 build journal (Days 1-6)

The **map-veto / match-setup feature** ([VETO.md](VETO.md) spec).  Five-stage
flow — roster → teams → captain vote → captain links → BO1/3/5 veto board → MatchZy
handoff — with captains vetoing from their own devices, the operator's UI mirroring
the session live.  Backend is authoritative; the prototype's browser-only state is gone.

Days 1-5 committed and pushed to master.  Day 6 (real `matchzy_loadmatch` RCON
handoff) and Day 7 (polish + smoke + tag) are the only remaining work before
v0.10.0 ships.

### Day 1 — `VetoSession` model + state machine (`c5bd7b8`)

`cs2servergui/veto.py` (365 lines) — the whole match lifecycle as a pure
state machine with no I/O, no Flask, no RCON.  Public surface mirrors the
prototype's transitions verbatim, but with server-side authority:

* States: `idle → roster → teams → voting → links → veto → finale → complete`,
  guarded by `_LEGAL_TRANSITIONS` (frozensets per state, mutation-rejected).
* Dataclasses: `RosterPlayer`, `VetoStep` (kind: BAN|PICK, team, map_id),
  `CaptainToken` (`secrets.token_urlsafe(32)`, single-use, idempotent same caller),
  `VetoSession` holds the lot.
* BO1/BO3/BO5 sequence templates in `_VETO_SEQUENCES` — BO1 = 6 bans + decider,
  BO3 = ban/ban/pick/pick/ban/ban + decider, BO5 = ban/ban/pick/pick/pick/pick + decider.
* Exception hierarchy `VetoError → InvalidVetoTransition / VetoStageError` so the
  HTTP layer maps cleanly to 400 vs. 409.
* `tests/test_veto.py` (34 cases) covers every legal/illegal transition, captain
  election ties, token reuse, single-use enforcement, full BO3 walkthrough.

### Day 2 — HTTP API + SSE live mirror + captain role (`8e1add4`, polish `9877b15`)

`cs2servergui/web.py` (15 new routes).  Every mutation runs under `core._veto_lock`
so SSE subscribers and concurrent admin/captain requests never see torn state.

| Route | Purpose |
|---|---|
| `GET /api/veto/state` | Read-only snapshot for the SPA's initial fetch |
| `GET /api/veto/stream` | SSE pub/sub via `queue.Queue` per subscriber (non-blocking `put_nowait`) |
| `POST /api/veto/create` | `create_session(mode, map_pool)`.  409 if a session is already active. |
| `POST /api/veto/roster` | 10 players (name + optional SteamID) + team names |
| `POST /api/veto/distribute` | Random 5+5 split (admin reshuffles) |
| `POST /api/veto/start_voting` | Locks teams, opens captain ballot |
| `POST /api/veto/vote` | Per-team votes (5 each); ties auto-revote |
| `POST /api/veto/resolve_captains` | Picks captains, transitions to `links` |
| `POST /api/veto/tokens` | Mints scoped single-use tokens (LAN + Public URLs returned per captain) |
| `POST /api/veto/revoke_token` | Revoke + reissue if a token leaked |
| `POST /api/veto/claim` | Public — token IS the credential; mints a captain session cookie |
| `POST /api/veto/step` | Captain bans/picks for their team (admin can act for either) |
| `POST /api/veto/finale` | `build_matchzy_config()` + `complete()` (Day 6 wires the real handoff) |
| `POST /api/veto/reset` | Clear and return to `idle` |
| `GET /veto?join=<token>` | Captain-link landing page — server-side claim + cookie + redirect to `/#veto` |

**New captain role.**  `_role_gate` allowlist `_CAPTAIN_PATHS = {/api/state,
/api/veto/state, /api/veto/stream, /api/veto/step}`.  Claim is PIN-free
(token IS the credential, single-use).  Wrong-turn = 400; team-spoof = 403.

**SSE snapshot enrichment** — every snapshot now carries `current_step_detail`
(index / kind / team) + `legal_moves`, so the SPA doesn't need to re-derive
the next action from the raw `sequence` array on every render.

`tests/test_veto_api.py` (17 cases initially, 25 after Day 4) covers the full
happy path, captain wrong-team rejection (400 vs 403), token reuse, SSE
broadcast verification, and the 409-on-existing-session create guard.

### Day 3 — SPA Veto tab + 8 stage renderers (`74c0f49`)

Frontend port of the prototype's 5-stage flow into the SPA as a dedicated
**Veto** tab in the sidebar (between Maps and Appearance).  Single
`pages['veto']` entry point in `cs2servergui/static/js/app.js`; state comes
from `/api/veto/state` + the SSE stream.

* Per-stage renderers: `_renderVetoIdle / Roster / Teams / Voting / Links /
  Board / Finale / Complete` + `_renderVetoCaptain` (captain-role simplified
  view).
* `api.veto.*` namespace in `api.js` (12 methods covering every endpoint).
* SSE subscribe on tab open, 5 s reconnect on error, cleanup on hashchange
  away from `#veto` (same pattern as the log drawer).
* `_vetoLocalRoster` buffer holds unsaved operator edits before the Save
  Roster button commits — survives in-flight SSE re-renders so the
  operator's typing isn't clobbered by snapshot pushes.
* ~270 lines of new CSS under "VETO (v0.10.0)" — reuses existing palette
  tokens (`--accent`, `--ok`, `--bad`, `--blue`).

### Day 4 — QR codes for captain links (`7561d1b`)

Captains on phones can scan instead of typing the URL.

* New dep: `segno >= 1.6` (pure-Python QR encoder, no Pillow / no native).
  Picked over `qrcode + Pillow` because Pillow is a 40 MB bundle for a 70 KB
  library's worth of features.
* New route `GET /api/veto/qr?token=…&kind=lan|public` returns SVG.
  Validates the token against the live session (refuses unknown so the
  endpoint isn't a free QR proxy for anyone with a session cookie);
  admin/local only; `Cache-Control: private, max-age=300`.
* `/api/veto/tokens` + `/api/veto/revoke_token` now return the raw `token`
  field alongside the LAN/Public URLs so the SPA can build QR URLs without
  re-parsing tokens out of the LAN link.
* SPA Links stage gains two QR slots per captain card (LAN + Public,
  Public only if a public IP is configured).  Mandatory white background —
  phone cameras need the high-contrast quiet zone against the dark veto
  page or they won't lock the code.
* 8 new tests added to `test_veto_api.py` (now 25/25): token shape, SVG
  return, unknown-token rejection, missing/bad-kind 400s, unauth 401,
  no-session 400, revoke includes new raw token.

### Day 5 — Cinematic finale animation (`b32be7e`)

Pure CSS animations layered on the existing renderers — no state-machine
change, no HTTP routes, no new tests.  Three independent JS gates ensure
each animation fires exactly once at the right moment, not on every SSE
re-render:

| Gate | What it controls |
|---|---|
| `_vetoLastRenderedState` | Stage-fade plays on state CHANGE only |
| `_vetoLastSeqLen` | Stamp slam-in + card shake only on the freshly-acted map |
| `_vetoFinaleShownThisSession` | Confetti + decider reveal play once per session; reset on `idle` |

The choreography (per arrival at finale): title slide-up + letter-spacing
expand (480 ms) → subtitle fade (380 ms, delay 220 ms) → maps pop in
staggered every 80 ms → **decider** gets a bigger pop + 1.8 s accent-glow
pulse × 2 (delay 700-900 ms) → launch button fade (delay 900 ms) → 30-piece
CSS confetti shower (2.6 s, 5-colour rotation, pointer-events: none so
the Hand-to-MatchZy button stays clickable beneath).

Decider glow uses `color-mix(in srgb, var(--accent) 55%, transparent)` to
alpha-fade the oklch accent token without converting to RGB.  Modern
WebView2 supports it natively; old WebView drops just the glow and keeps
the rest.

### Day 6 — MatchZy match-config write + RCON handoff

`/api/veto/finale` was a placeholder that built the config dict and
logged it.  Now it does the real handoff:

1. Strips `_oblivion_meta` (our SPA audit trail, unknown to MatchZy)
   from a copy of the config so MatchZy's schema doesn't complain.
2. Atomically writes the cleaned JSON to
   `<csgo>/cfg/MatchZy/<matchid>.json` (tmp + `os.replace` + fsync; the
   directory is auto-created on first use).
3. If `load_match: true` (default) AND `core.running`, issues
   `matchzy_loadmatch <basename>` via RCON.  Single attempt — RCON has
   retry logic but the operator is watching this in real time; better
   to surface a quick failure than wait 30 s through retries.
4. Response always carries `{ok, config, matchzy: {written_to, loaded,
   error?, rcon_response?}}` so the SPA can show the operator exactly
   what happened.

Three-way outcome design:
* **File write fails** → 500, session stays on `finale` so the operator
  can retry after fixing the disk issue.
* **File written, RCON fails (or server not running)** → 200 with
  `matchzy.error` describing what to do.  Session still transitions to
  `complete` so the SPA isn't stuck; the operator can copy
  `matchzy.written_to` and run `matchzy_loadmatch <file>` from the RCON
  console.  The launch button flips to "Retry handoff".
* **File written, RCON succeeded** → 200 with `matchzy.loaded: true`
  and a snippet of the RCON response.  SPA shows a green ✓ and the
  button locks to "Match handed off ✓".

SPA finale renderer updated: real-time status under the launch button
(yellow warning for needs-attention, green check for success), button
state machine (disabled during the call, "Retry handoff" enabled on
RCON failure, locked on success).

Test additions (+6 cases, now 31/31 in `test_veto_api.py`):
* File gets written + on-disk JSON has the expected MatchZy keys
* `_oblivion_meta` is **stripped from the disk file** but **preserved
  in the API response** (so the SPA can show the veto audit trail)
* Server-not-running → 200 + `matchzy.error` mentioning "not running"
* RCON `ConnectionError` → 200 + `matchzy.error` containing the
  exception text; session still transitions to `complete`
* `load_match: true` + running + RCON OK → exactly one
  `matchzy_loadmatch <basename>` call, where `<basename>` matches the
  on-disk filename
* `load_match: false` + running → zero RCON calls (preview mode)

Test fixture `_new_app()` now redirects `core._csgo_dir()` to a
per-test tempdir via `mkdtemp('oblivion_veto_csgo_')` so the test
batteries never write to the real CS2 install dir on the user's machine
(this would otherwise litter `D:\steamcmd\…\game\csgo\cfg\MatchZy\`
with `oblivion-veto-*.json` files on every run).

### Day 7 (pending) — Polish + smoke + tag v0.10.0

### Day 7 — Polish + extra unit tests + release

See top of v0.10.0 section above for the Day 7 detail.

### Final test totals at v0.10.0 release

* `tests/test_v092.py` — 22/22 (unchanged from v0.9.2.1)
* `tests/test_veto.py` — 49/49 (Day 1's 34 + Day 7's 15 edge-case additions)
* `tests/test_veto_api.py` — 37/37 (Day 2's 17 + Day 4's 8 QR + Day 6's 6 MatchZy + Day 7's 6 polish)
* **All 108/108 green**

---

## v0.9.2.1 — 2026-06-01 (hotfix)

A four-agent re-audit of the v0.9.2 fix code (not the original bugs) surfaced one
**critical regression** and four other issues worth fixing before any operator actually
runs the v0.9.2 binary in earnest. All landed here.

### 🚨 Critical: 5-second RCON command stall

`rcon.py`'s multi-packet sentinel had a speculative trailing `_recv(s)` that waited for a
"trailing empty-response packet some Source builds emit after the sentinel" — except CS2
doesn't emit it, so every `execute()` blocked for the full 5-second socket timeout waiting
for a phantom packet.  **Every RCON-touching path** — status polling, broadcasts, kicks,
bans, map changes — gained +5 s. The smoke test missed it because the mock socket pre-
queued the phantom packet that real CS2 doesn't send.  v0.9.2.1 drops the speculative
drain; the sentinel id arrival already proves the real response is complete.

### 🔒 Workshop-download race actually fixed

The v0.9.2 fix locked `cancel_download` but left the worker's assign/clear and the web
route's 409-check unlocked. Two clicks could both observe `None` and both spawn
workers. v0.9.2.1: web.py atomically check-and-reserves under `_dl_lock`; worker
swaps the reservation for the real Popen handle (also under lock); cancel-before-spawn
race correctly terminates the late-arriving process.

### 🌐 `_resolve_rcon_host` won't clobber good IP with `127.0.0.1`

If `_lan_ip()` momentarily falls back to its loopback default (UDP probe to 8.8.8.8
fails), v0.9.2 would overwrite `_config.RCON_HOST` with `127.0.0.1` and break the
very bug v0.9.2 was supposed to fix. v0.9.2.1 keeps the last-known-good value when
the fresh probe is the loopback fallback. `_post_launch_sanity_check`'s netstat-based
recovery remains the safety net.

### 🔐 Two more `current_map` writes under `_lifecycle_lock`

`_poll_rcon_ready:1474` (workshop trigger success) and `change_map:1534` were the two
remaining bare writes; v0.9.2 had locked the recovery path but not these. Now all four
sites are consistent.

### ⏸ Stop during crash-restart backoff: edge-window cancel

Stop pressed in the tiny window between `_stop_event.wait()` returning False and
`start_server`'s `clear()` was swallowed by the clear, so the unwanted respawn proceeded.
Now re-checks `_stop_event.is_set()` after the wait, before `start_server`.

### 🧙 Warcraft: SteamID equality instead of `ReferenceEquals`

The v0.9.2 Warcraft audit follow-ups used `ReferenceEquals(fresh, wcPlayer)` to verify
the queued menu was still for the same player. But `WarcraftPlugin.SetWcPlayer` legitimately
installs a brand-new `WarcraftPlayer` object on class change — same human, same slot, but
the reference comparison silently fails and the menu drops. v0.9.2.1 compares by SteamID
(`slotController.SteamID != capturedSteamId`) so a queued menu survives a class change.

Three sites fixed: `WarcraftPlugin.cs` (`!reset` follow-up), `Events/EventSystem.cs`
(round-start auto-open), `Menu/WarcraftMenu/SkillsMenu.cs` (recursive reopen after pick).

Rebuilt `WarcraftPlugin.dll` bundled.

### Test battery still 22/22

The v0.9.2 isolated-behaviour battery (`tests/test_v092.py`) is unchanged and continues
to pass under the hotfixed code. Mocks were correct; the bug was in the integration with
real CS2 — discoverable only via second-pass code review.

---

## Unreleased
*Post-v0.9.2.1 polish. Will fold into v0.9.3 when there's a meaningful change worth tagging.*

### 📦 Installer / Build Hardening

Defensive packaging tweaks — no behaviour change to the running app:

- **`build.bat`**: added `cs2servergui._netutils` to `--hidden-import` (the
  module is imported lazily inside `core.py` methods; without an explicit
  entry, PyInstaller's static analyser only finds it via `main.py`'s top-
  level alias re-export, which could break under future refactoring).
  Also added `--noconfirm` so the PyInstaller build never blocks on the
  existing-output prompt.
- **`requirements.txt`**: pinned `werkzeug>=3.0.0` explicitly. Comes in
  transitively via Flask today but `main.py` imports `werkzeug.serving
  .make_server` directly since v0.9.2 — pinning here makes the build
  stable if Flask ever swaps its server backend.
- **`installer.iss`**: documented WebView2 bootstrapper activation
  (download `MicrosoftEdgeWebview2Setup.exe`, place in repo root,
  uncomment 2 lines) with a clear note that Win10 needs it.  Also added
  explicit `IconFilename:` to the Start Menu + Desktop shortcuts so the
  emblem.ico shows even before Windows' shell-icon cache warms.

## v0.9.2 — 2026-05-30

### 🧹 Cleanup Pass — Dedup, Dead Code, Stale Markers

Small follow-up sweep with no behaviour changes — pure code hygiene:

- **`cs2servergui/_netutils.py` (NEW)** — single source of truth for the Windows
  port/process helpers (`port_in_use`, `holder_of_port`, `listeners_on_port`).
  Previously `_holder_of_port` had two near-identical implementations: one
  module-level in `main.py` for Flask port collisions, one as an `AppCore` method
  in `core.py` for CS2 port-conflict detection.  Both call sites now import from
  `_netutils`; `core.AppCore._holder_of_port` / `._listeners_on_port` stay as thin
  instance-method wrappers that pass `self.log` so the AppCore logger gets the
  diagnostic output.
- **`main.py:5` raw-string fix** — the build-output path in the module docstring
  used `dist\OblivionServerTool.exe` which triggered Python 3.12+
  `SyntaxWarning: invalid escape sequence '\O'` on every import.  Module docstring
  is now an r-string.  Zero warnings on import.
- **Dead `RCON_HOST` import removed from `web.py`** — the 20-bug sweep dropped
  every reader of the name but left the import.  Now gone, with a comment noting
  why future readers should always use `_config.RCON_HOST` at call time.
- **3 legacy plugin scrubs removed from `_PLUGIN_CLEANUP_ITEMS`** — `cfg/retakes.cfg`
  from the defunct MatchZy-retakes era, plus three `characters/models/`
  Barbarian-model paths from an earlier failed precache attempt.  All were "remove
  if leftover from an older install" entries that no current install carries.
- **Stale TODO closed** — the "verify auto-restart fires on a real crash" line is
  now obsolete: the post-friends-night resilience pass replaced the fixed 5 s
  backoff with exponential (5 → 15 → 45 s) + 5-min time-window reset + `Event.wait`-
  cancellable sleep so a Stop during the backoff is honoured.
- **Unused `socket` import removed from `main.py`** — `_port_in_use` moved to
  `_netutils` so `main.py` no longer needs its own socket import.

### 🛠️ Workshop Maps Root-Cause Fix + 20-Bug Audit Sweep

The workshop-map-loads-as-dust2 saga ended with an embarrassingly small root cause:
`from cs2servergui.config import RCON_HOST` was binding the LAN IP **at module import time**
inside `core.py`. `_resolve_rcon_host()` was diligently updating `_config.RCON_HOST` on every
server start, but the import-bound `RCON_HOST` name in `core.py` never updated — so
`_poll_rcon_ready`'s probe socket kept dialling the stale IP forever. A boot-time network
blip or any later DHCP/VPN/adapter change would silently break workshop maps for the rest
of the session. Now `_config.RCON_HOST` is read at call time everywhere; the netstat-based
auto-recovery from earlier in the day becomes a pure safety net instead of the primary path.

A parallel four-agent app-wide bug hunt (core.py, web.py + frontend, main.py + config.py +
rcon.py, Warcraft plugin) surfaced **20 actionable findings** — 7 critical, 8 serious,
5 minor — all landed in this release.

**Critical (7)**
- `core.py` import-bound `RCON_HOST` (above) — the actual root cause of the workshop bug.
- `save_config()` was non-atomic: two concurrent saves (Flask is threaded) could interleave,
  and a power-loss mid-write left a truncated file that `_load_config` silently treated as
  `{}` on the next launch — wiping every persisted setting + regenerating the RCON password.
  Now lock-guarded, tmp-write + `os.replace` + `fsync`.
- `os._exit(0)` at window close bypassed every pending save. Settings changed seconds before
  shutdown were lost. Now `core.save_config()` runs synchronously before the exit.
- Multiple lifecycle-state mutations outside `_lifecycle_lock` (`boot_state`/`running`/
  `current_map` in `probe_existing_server`, `_poll_rcon_ready` 90s timeout, `change_map`,
  `_post_launch_sanity_check`) could race a concurrent `stop_server` and leave inconsistent
  state. `_poll_rcon_ready` also read `self.proc` twice — `AttributeError` if cleared between
  reads. All wrapped now; `proc` snapshotted once.
- Stop pressed during the 5/15/45 s crash-restart backoff was silently ignored (the sleep
  ran to completion and respawned anyway). Replaced with `Event.wait()` so Stop cancels.
- `/api/workshop/download` had no concurrency check — two clicks spawned two `steamcmd`
  processes, orphaning the first and colliding on the staging dir. Now returns 409 when busy.
- `/api/server/broadcast` blocked `\r\n` but not `;` — CS2's console treats `;` as a command
  separator, so a `hello;sv_password pwn;quit` broadcast ran arbitrary RCON. Now stripped
  and capped at 200 chars.

**Serious (8)**
- `_post_launch_sanity_check` used a stale `proc` snapshot — could force-fire
  `host_workshop_map` on a server that was just stopped. Re-checks `running` after each sleep.
- `cancel_download` read `_active_dl_proc` without the download lock — a click between
  "worker finished" and "next started" could kill the new download. Snapshot+clear under lock.
- `rcon.py:execute_retry` only retried `ConnectionRefusedError` + `TimeoutError` — a flapping
  network produces `ConnectionResetError` / `OSError(WinError 10054)`, which were re-raised
  immediately. Widened to `(TimeoutError, OSError)` plus `ConnectionError` minus "auth failed".
- `rcon.py:execute` never handled Source RCON's multi-packet response trick (any body >4 KB
  splits across multiple packets, terminated by an empty-body sentinel). Long `status` /
  `cvarlist` output was silently truncated at the first 4 KB. Now sends a sentinel after
  the real command and concatenates every fragment until the sentinel id comes back.
- `config.py:_lan_ip()` did a fresh UDP `socket()` + `connect("8.8.8.8:80")` on every
  `/api/state` poll (every 2 s × connected clients) — wasted syscalls + a hard dependency
  on a route to 8.8.8.8 existing for the LAN IP to resolve, which serialised every state poll
  behind a wedged VPN/Hyper-V adapter. Now cached 30 s + 0.5 s socket timeout. `AppCore.
  _resolve_rcon_host` calls with `force_refresh=True` so server starts still see live values.
- `main.py` had a TOCTOU between `_pick_free_port()` and `flask_app.run()` — Flask binds
  inside its background thread, so a foreign process grabbing the port in those ~ms surfaced
  as a misleading "did not start in 10s" timeout. Now uses `werkzeug.serving.make_server`
  to bind synchronously in the main thread and retries up to 3 times on race-loss.
- `_fix_metamod_dll_nesting` used `shutil.copy2 + rmtree` — a failed rmtree left the DLL
  at BOTH the nested and parent paths, and MetaMod would load the wrong one. Now uses
  `shutil.move` (pre-removing any existing dst on Windows).
- `/api/log/save` used `%Y%m%d_%H%M%S` filenames — two saves in the same second silently
  truncated each other (opened with `"w"`); no `@require_local` so guests could spam saves
  to fill the host's disk; no empty-buffer guard. All three fixed: 6-hex random suffix,
  local-only, 400 on empty buffer.

**Minor (5)**
- `_STEAMID_RE` had no length anchor — a 1 MB string passed validation. Capped to 64 chars
  plus a dedicated `_NAME_MAX_LEN` cap on `players_kick` `name`.
- Per-IP `_attempts` auth-failure dict had no GC for entries below `_MAX_ATTEMPTS` — slow
  distributed brute force could grow the dict forever. Added `_ATTEMPT_TTL_SECS` prune.
- `/auth/auto` startup-token compare-and-clear was non-atomic — two simultaneous loopback
  hits could both pass `compare_digest` and mint two local sessions. Now lock-guarded.
- `/api/setup/status` was guest-accessible — leaked `pin_is_default` to remote guests.
  Now `@require_local` (the first-run wizard only ever shows in the local pywebview window).
- `_last_crash_mono` wasn't reset when the auto-restart cap was hit — the next crash would
  hit the stable-reset branch with a stale timestamp and log a misleading "stable for X s".

### 🧙 Warcraft — Menu & Chat-Broadcast Dispatchers v2

After the v1 per-player chat-command cooldown shipped, a live retest (2026-05-30, Casual +
Warcraft, 13 humans+bots on `de_cache`) showed the bug still happened: a single `!shop`
during a combat-heavy frame produced `recv queue overflow 100` on every connected client
plus `SteamNetworkingSockets lock held for 263 ms ... thread starvation`. The cooldown
stopped rapid spam from the same player but didn't address two collisions in the same tick.

- **Menu-open dispatcher**: every `!class` / `!skills` / `!shop` (and the programmatic
  `SkillsMenu.Show` at round-start after a level-up and after `!reset`) now enqueues through
  `WarcraftPlugin.EnqueueMenuOpen`. A 0.1 s repeat timer drains **one** queued open per tick,
  so ten concurrent opens fan out across ~1 second of frames instead of stacking onto one.
- **Chat-broadcast dispatcher**: `AbilityBenefitAnnouncer.SendRoundSummary` (called for every
  human at round start, ~5 `PrintToChat` per player) routes each broadcast through
  `EnqueueChatBroadcast`. A 0.05 s repeat timer drains 5 per tick — round-end bursts of 50+
  `PrintToChat` smooth across half a second.
- **Audit follow-ups** (from a parallel agent review of the patches):
  - `Unload` now kills the new timers and clears the queues. Hot-reload could otherwise leave
    the old timers firing into a disposed instance with the new instance's queues never drained.
  - `AbilityBenefitAnnouncer.SendRoundSummary` hoists `WarcraftPlugin.Instance` to a local
    at enqueue time — a hot-reload between enqueue and drain could otherwise route the burst
    into a different (or null) plugin instance's queue.
  - The three deferred `SkillsMenu.Show` sites (recursive reopen, round-start auto-open,
    `!reset` follow-up) re-resolve the WarcraftPlayer via the slot's controller at drain
    time. If a player disconnected and a new player took the same slot in the 100 ms drain
    window, the original profile would otherwise pop for the new occupant.
  - Both timers re-armed in `OnMapStartHandler` (`STOP_ON_MAPCHANGE` kills them at map end)
    and queues cleared in `OnMapEndHandler` so they can't accumulate stale lambdas.
- Built against the upstream toolchain (.NET 8 / CSS 1.0.368) — patched `WarcraftPlugin.dll`
  bundled in `cs2servergui/plugins/warcraft/`.

### 🛟 Log Drawer — Copy + Save Buttons

The in-app log drawer had no way to extract the buffer — operators were screen-grabbing log
panels to share diagnostic output. Added two buttons to the drawer bar:
- **Copy** uses `navigator.clipboard.writeText` with a hidden-textarea + `execCommand('copy')`
  fallback for environments where WebView2 silently rejects clipboard writes.
- **Save** posts to a new `/api/log/save` endpoint that writes a timestamped
  `oblivion_log_YYYYMMDD_HHMMSS_<6 hex>.txt` to the config directory (next to
  `oblivion_config.json`) and surfaces the path via toast + log line.

### 🔌 RCON Host — Stop Pinning to LAN IP at Import

Belt-and-braces follow-up to the workshop-maps fix. `_resolve_rcon_host()` re-resolves
`_config._lan_ip(force_refresh=True)` and updates `self.rcon.host` on every server start /
attach. Plus the post-launch sanity check (added earlier) keeps its netstat-based recovery
that switches `self.rcon.host` to whichever bind address actually answers — handles CS2
binding to an unexpected interface (VirtualBox / Hyper-V / Docker / VPN tap adapter that
sorts ahead of the primary LAN NIC in Windows' route table).

### 🔐 Two-Tier Remote Access — Guest vs Admin

The remote panel now has an optional **guest role** so you can hand friends limited control
without exposing full admin.

- **Guest PIN** (Config → Security, local-only to set; blank = disabled). A separate PIN from
  the admin PIN; admin wins if they collide.
- **Guest can:** view status, change map, change game mode, browse + **download workshop maps**.
  **Guest cannot:** start/stop the server, edit config, manage bots/bans/players, view logs, or
  anything else — and RCON/install/Steam stay strictly local as before.
- Enforcement is **fail-closed**: a single `before_request` gate allows only an explicit
  guest/public allowlist; every other `/api/*` route is admin-only by default (new routes are
  locked down automatically). The login assigns `session["role"]`; the local desktop window is
  always admin.
- The SPA hides admin-only UI for guests (Start/Stop, settings strip, Config & Players tabs,
  keybinds) and shows an "Admin only" notice on direct navigation; `/api/state` exposes `role`.
- **Keybinds disabled for guests** — the global shortcut handler no-ops for guest sessions so a
  friend can't trigger admin actions (stop/restart/bots) by keypress.
- *Verified live through a Cloudflare tunnel:* guest → 403 on admin routes / 200 on allowed ones;
  admin → full access; wrong PIN → 401; guest UI correctly stripped down.

### 🎮 Team-Size Modes — Arenas (1v1/2v2) + MatchZy (3v3/4v4/5v5)

Reworked the small-team modes so duels and team matches are cleanly separated, fixing
the long-standing gap where `3v3`/`4v4` were secretly K4-Arenas modes that never actually
configured a team size (they ran the plugin's default, i.e. mostly 1v1).

- **Arena duels capped at 2-per-side:** `1v1` and `2v2` are the K4-Arenas ladder modes.
  `1v1` uses the plugin's default rounds (already pure 1v1 — its bundled `2vs2`/`3vs3`
  rounds ship `EnabledByDefault=false`); `2v2` gets a **generated `round-settings` config**
  forcing `TeamSize: 2` across a small weapon rotation (written on deploy by
  `_apply_arena_size`; the generated config is scrubbed on switch-away so it can't linger).
- **Team matches via MatchZy:** `3v3`, `4v4`, `5v5` are now MatchZy-managed team matches
  (same plugin as Practice) on the competitive ruleset, bounded by `maxplayers` 6 / 8 / 10.
- **Mode list** gained `2v2` and `5v5`; `3v3`/`4v4` switched from arenas to MatchZy. The
  arena Use-bots toggle still applies to `1v1`/`2v2`.
- **Arena ladder bots fixed** — arena modes now launch with `+bot_quota_mode normal`. K4-Arenas-Bots
  reads that mode: `normal` adds exactly **one** bot to even an odd player count, and that bot joins
  the 1v1 ladder like a player (pairings rotate P-vs-B / P-vs-P / B-vs-P). The default `fill` set
  `bot_quota 2` — a second, unpaired bot that stuck onto a side as a **2v1**. Forcing `normal` at
  launch prevents that.
- **Identical guns per duel** — both arena modes (1v1 + 2v2) now generate an explicit-weapon
  round rotation so **both players get the exact same gun** each round. The plugin default uses
  per-player weapon *preferences*, which could hand opponents different guns within a category
  (AK vs M4); preferences are now disabled.
- **Rotation tightened to the classic 1v1 ladder set** — AK / M4 / AWP / Scout / Pistol (USP) /
  Deagle / Knife. The earlier SMG (MP9) and Shotgun (Nova) rounds were dropped — they felt out of
  place in a skill-based 1v1 ladder.
- ⚠️ *Needs in-game verification:* the generated 2v2 arena config and the MatchZy team-size
  matches couldn't be tested without a live server.

### 🗺️ Workshop Map Flagging — Recommended Modes

The map browser now tells you what each workshop map is *for*, instead of leaving you to
guess from its name. All derived from the Steam Workshop tags we already cache (no new API
calls).

- **Recommended-mode badges** on every workshop card — derived by inverting `MODE_WORKSHOP_TAGS`
  but ignoring generic tags (`classic`/`competitive`/…) so only *distinctive* tags drive them
  (a `ze_` map shows **Zombie Escape**, an `aim_` map shows **1v1 / 2v2**, etc.). Plain comp maps
  read "Competitive / Team".
- **Steam tag chips** shown (muted) under the badges for at-a-glance context.
- **Mode-mismatch guard** — starting or loading a map whose recommended modes don't include the
  selected mode pops a confirm ("looks made for *Zombie Escape*, you've selected *Competitive*").
  The confirm offers **Switch to &lt;mode&gt; & load** (fixes the mismatch in one click), *Load
  anyway* (keeps the current mode), or Cancel. Applies on the status page and the grid.
- **Sort + dim by current mode** — the card grid floats maps that suit the selected mode to the
  top and de-emphasises clear mismatches (they brighten on hover; nothing is hidden).

### 🎯 Unified Map Picker — No More Ambiguity

The status-page "Map & Mode" card had **two** map dropdowns (Official + Workshop) and silently
resolved `workshop || official` — so picking a workshop map secretly overrode the official one
with no visual cue which would actually launch.

- **One unified Map dropdown** now lists everything in optgroups: *Official Maps*,
  *Workshop — Recommended for &lt;mode&gt;*, *Workshop — Other*. One control = one selected map =
  zero ambiguity for both **Start** and **Change Map**. Each workshop option is also labelled
  with its recommended mode(s) (e.g. `ze_random · Zombie Escape`) so every option self-describes.
- **"Selected: &lt;map&gt; [Official|Workshop]" readout** under the picker confirms exactly what
  will load and from where.
- Start / Change Map read that single selection (and still run through the mode-mismatch guard);
  an empty selection now prompts "Select a map first" instead of failing silently.
- ⚠️ *Frontend-only change; worth an eyeball in the running app to confirm the picker renders.*

### 🎨 UI

- **Sidebar no longer looks washed out** — its background was the *lightest* large surface in the
  app (`bg-1 → bg-2`), reading as a pale slab next to the dark content. Re-graded it to fade
  `bg-1 → bg-0` (into the base tone) and softened the inset edge glow, so the tab rail now sits in
  the dark theme instead of floating above it.

### 🧟 Zombie Escape — Command-Filter Fix

- **Zombie Escape now always launches with `-disable_workshop_command_filtering`.** Mounting the
  ZombieReborn content addon (MultiAddonManager) turns on CS2's workshop command filtering for the
  whole session — even on official maps — which silently rejected `zm_enable` and every
  `cs2f_*`/`zr_*`/`zm_*` CVar (a wall of `DISALLOWED WORKSHOP CONVAR` in the log), so ZM never
  actually enabled. Added `_CMDFILTER_REQUIRED_MODES` so the flag is forced for the mode
  regardless of map. *Confirmed working in-game (2026-05-29).*

### 🧟 Zombie Escape — Missing ZM Configs (gun pickup + models)

- **Humans couldn't pick up guns in Zombie mode.** CS2Fixes' ZM reads a per-weapon whitelist from
  `addons/cs2fixes/configs/zm/weapons.cfg` (each weapon's `"enabled" "1"` = humans may use it), but
  the bundle only ever shipped `weapons.cfg.example` — so the active file was missing
  (`Failed to load … zm/weapons.cfg` in the boot log) and **no weapons were whitelisted → pickups
  blocked.** Now ship the active `weapons.cfg` (all 43 weapons enabled), plus `hitgroups.cfg` and
  `playerclass.jsonc` (the latter fixes zombies having no custom model), for both `zm/` and `zr/`.
  *Confirmed cause in-game; takes effect on map reload (CS2Fixes loads these at level init).*

### 🧙 Warcraft — Menu & Chat-Broadcast Dispatchers (recv-queue-overflow root-cause fix)

Friends-night Warcraft session under a full lobby choked: `recv queue overflow 100 messages
already queued` for every client, `Long frame (FreezePeriod): 55ms`, `thread starvation`, and
clients timing out — driven by `!class` / `!skills` / `!shop` / `!commands` (each menu open does
DB loads + HUD/WORLD_TEXT broadcasts → main-thread pressure → can't drain incoming packets).
Live retest (2026-05-30, `de_cache` with 13 humans+bots) showed the per-player cooldown helped
but didn't eliminate the bug: a single `!shop` during a combat-heavy frame still produced
`recv queue overflow` on every connected client.

- **Per-player chat-command cooldown** (already in v4.1.1+patch1): `AddUniqueCommand` wraps every
  chat command with a 1.5 s per-(player, command) cooldown; bots/console bypass. Stops rapid
  spam from the same player but doesn't prevent collisions across players.
- **Menu-open dispatcher (NEW)**: every `!class` / `!skills` / `!shop` (plus the programmatic
  `SkillsMenu.Show` auto-opens at round-start after a level-up and after `!reset`) enqueues
  through `WarcraftPlugin.EnqueueMenuOpen` instead of running inline. A 0.1 s repeat timer
  drains one queued open per tick, so ten concurrent opens fan out across roughly one second
  of frames instead of all hitting the engine on one tick.
- **Chat-broadcast dispatcher (NEW)**: `AbilityBenefitAnnouncer.SendRoundSummary` (called for
  every human at round start, ~5 PrintToChat per player) now routes each broadcast through
  `WarcraftPlugin.EnqueueChatBroadcast`. A 0.05 s repeat timer drains 5 broadcasts per tick
  (100/sec capacity), smoothing the round-end burst across half a second of frames.
- Built against the same toolchain as the upstream (.NET 8 / CSS 1.0.368) — bundled patched
  `WarcraftPlugin.dll` ships in `cs2servergui/plugins/warcraft/`.

### 🐛 CS2 Update / Disk Bloat — Critical Fix

- **Stopped the updater creating a duplicate ~64 GB install.** The steamcmd update ran with
  `+force_install_dir <CS2_SERVER_DIR>`. CS2's content root is a top-level `game/` folder, so
  steamcmd unpacked the whole install into `D:\steamcmd\game\` — a full duplicate, separate from
  the manifest-tracked `steamapps\common\…` install the server actually runs. Every update grew
  that orphan and never touched the real files (server dir had ballooned to ~149 GB). Dropping
  the flag lets steamcmd use its default library (the steamcmd dir) and update the real install
  **in place** via the existing `appmanifest_730.acf`. Reclaimed ~64 GB.
- **Update badge clears without a relaunch + self-verifies.** Both update badges now toggle on
  state (previously show-only, so the "⬆ CS2 Update" badge lingered until relaunch). After a
  successful update, `check_update` re-reads the updated `appmanifest` buildid and compares it to
  the latest public build — confirming the update actually landed rather than optimistically
  clearing the flag.
- **Update path hardened.** steamcmd.zip download uses `urlopen(timeout=60)` + `copyfileobj` so a
  stalled CDN can't hang the install thread.
- **Server update now runs steamcmd in its own console window** instead of capturing its output
  into the app. The captured-pipe path is what triggered steamcmd's "exit code 8" self-update
  failure and no-output hangs; a standalone console lets steamcmd self-update cleanly and shows
  native progress. The app still holds the process handle, waits for it to finish (heartbeat in
  the log), then re-verifies the build — so the badge still clears automatically on completion.
  *Confirmed working in-game (2026-05-29): update applied cleanly, no duplicate install, server
  rejoined the matching client build.*
- **Always-available "Update / Validate CS2" button** (Config → Server Installation, local-only).
  Previously the CS2 update was *only* reachable via the `⬆ CS2 Update` badge, which appears only
  when the mirror-based check (`api.steamcmd.net`, which can lag Valve) flags an update — leaving
  no way to force a steamcmd `app_update 730 validate` when the badge wasn't showing. The button
  runs the same in-place update on demand (refuses while the server is running). It also **pulses
  when an update is detected** (`update_available`) as a cue — while staying a normal,
  always-clickable forced-update button when it isn't pulsing, so a mirror miss never hides it.

### 🛡️ Resilience — Redundancies After Friends-Night Burns

A batch of small hardenings to address the failure modes from the live session: a foreign app
squatting our Flask port broke the desktop panel, the server died mid-mode-switch, and the
silent missing-config bug (zombie weapons whitelist) wasn't caught until a friend reported
empty hands. The fixes are all defensive — none of them change normal-path behaviour.

- **User-configurable Flask port** — `flask_port` is now a first-class field in
  `oblivion_config.json` (default `5050`). Resolves the prior TODO; config and main both honour it.
- **Port-collision survivor on Flask bind.** Identifies the holder of the configured port via
  `netstat -ano` + `tasklist`: if it's our own zombie (`OblivionServerTool.exe` / `python.exe`),
  it's killed; if it's foreign (CS_GO_Arx_Applet, etc.), it's left alone and Flask falls back to
  the next free port in `[configured+1..configured+3]`. The chosen port is logged and propagated
  via `_config.FLASK_PORT` so the status bar / tunnel hints reflect reality.
- **RCON_HOST re-resolved on every server attach/start.** `config.py` resolves the LAN IP once
  at import — so a DHCP change after the app boots left RCON pointed at a stale IP. The new
  `_resolve_rcon_host()` runs at the top of `start_server` and `probe_existing_server`, refreshes
  `_config.RCON_HOST`, and patches the live `RCONClient` instance.
- **Pre-flight checks before Start.** New `_preflight_checks()` runs before `deploy_plugins()`:
  blocks if CS2 isn't installed, port `27015` is held by a non-CS2 process, or the bundle's
  plugin source folders are missing for the chosen mode. Soft-warns if a workshop map is
  selected but Steam credentials aren't saved, or DepotDownloader is missing. Every finding is
  logged with a one-line fix hint.
- **Bundle config validation on deploy.** Walks each deployed plugin's bundle folder for any
  `*.example` files and warns when the implied active file is absent from both the bundle and
  the live `csgo/` tree. Catches the class of bug we hit with Zombie's `weapons.cfg` — shipped
  `weapons.cfg.example`, no active file → plugin loaded with no whitelist → gun pickup
  silently broken.
- **Crash auto-restart hardening.** Exponential backoff between attempts (`5 s → 15 s → 45 s`)
  so a persistent boot-loop config bug isn't hammered, and a **time-window reset**: if the
  server stayed up for 5+ minutes since the last crash, the consecutive-failure counter is
  forgiven. Previously a session that auto-restarted twice over hours would refuse the third
  recovery because the counter only reset on a clean stop. End-state messaging now points at
  log-checking and explicitly says the counter resets on manual Start.

### 🔌 Web Panel Port

- **Default Flask port moved `5000` → `5050`.** Port 5000 is heavily contested (Flask demos,
  macOS AirPlay, and CS applets like `CS_GO_Arx_Applet` that bind `127.0.0.1:5000`). A collision
  there makes the desktop panel unreachable on loopback — every API call fails with "failed to
  fetch" and in-app RCON breaks, even though the server itself is fine. 5050 is far less contested.
  *(Takes effect on the next build / source run; update any tunnel or port-forward to 5050.)*

### 🧹 Workshop Cleanup

- Removed an empty (0-byte) broken workshop folder and four obsolete CS:GO-era `.bsp` maps
  (`de_bank`, `cs_militia`, `de_stmarc`, `gd_rialto`) — confirmed via re-download they were intact
  but in the CS:GO format CS2 can't load. Disk free went ~16.5 GB → ~80 GB after the dedupe + this.

---

## v0.9.1 — 2026-05-29

A stability + features pass: Retakes rebuilt on B3none, the Jailbreak native crash fixed,
Warcraft Barbarian models fixed via a precacher plugin, a full workshop download overhaul
(progress + verify), workshop command-filter automation, and the Arena modes made
group-friendly with a dynamic player cap and a Use-bots toggle.

### 🕹️ Arena Modes & Bots

- **Dynamic player ceiling** — 1v1/3v3/4v4 (K4-Arenas) now launch with `maxplayers 16`. It's
  a ceiling, not a target: K4-Arenas only builds arenas for players actually present, so any
  turnout fits (4 → 2 arenas, 12 → 6) with no per-session tuning.
- **"Use bots" toggle** (Config → Bots, default off) — when off, K4-Arenas-Bots is excluded
  from the deploy so the ladder is humans-only (the odd player out waits at their rank for the
  next opponent); when on, bots fill empty arenas. (Currently gates Arena bot-fill; Retakes /
  Deathmatch to follow.)
- Fixed deploy verification falsely flagging `K4-Arenas-Bots.dll` as missing when bots are off.

### 🔌 Retakes — B3none cs2-retakes (not MatchZy)

An earlier plan to base Retakes on "MatchZy's built-in retakes mode" was **abandoned: MatchZy
has no retakes feature** (verified in its source and docs). Retakes now runs on **B3none's
dedicated [cs2-retakes](https://github.com/B3none/cs2-retakes)** `RetakesPlugin` paired with
**yonilerner's `RetakesAllocator`**.

- Bundled `RetakesPlugin` + `RetakesAllocator` + `RetakesPluginShared`; `retakes_config.json`
  sets `EnableFallbackAllocation=false` (the allocator owns weapons) and `RoundsToScramble=3`.
- **Spawn-coordinate fix** — B3none's bundled `map_config/*.json` used thousands-separator
  commas (`1,229.99`) that failed float-parsing and spawned players inside walls; stripped
  271 bad commas across the map configs.
- **Bot auto-fill** — a deployed `cfg/cs2-retakes/retakes.cfg` enables `bot_quota_mode fill`
  so retake rounds still form on a low-population server.
- `_MODE_PLUGIN_NAMES["Retakes"] = ["retakes_b3none"]`; competitive ruleset (`game_mode 1`).

### 🧙 Warcraft Fixes

- **Barbarian models fixed via a new `ModelPrecacher` plugin.** Barbarian assigns the
  non-default player models `tm_phoenix_heavy` / `ctm_heavy`, which exist in `pak01.vpk` but
  aren't auto-precached — so `SetModel` logged "requested but is not in the system" and the
  model failed. Loose `.vmdl_c` copies were proven *not* to fix this (CS2 only loads models in
  the precache manifest). A tiny bundled CounterStrikeSharp plugin (`ModelPrecacher`, source in
  `_plugins_src/`) now registers both via `OnServerPrecacheResources` → models render, all 14
  classes intact. *Confirmed working.*
- **`!buy` shop command fix** — removed `buy` from WarcraftPlugin's shop-menu triggers; it was
  shadowing CS2's native `buy <weapon>` console command, so buying a gun popped the Warcraft
  shop instead.
- **In-game menu theming** — added a CS2MenuManager `config.toml` (purple/white, WasdMenu,
  4:3-safe position) for menus that route through CS2MenuManager. Note: WarcraftPlugin's
  `!class`/`!skills`/`!shop` use its *own* compiled menu, which enlarges the highlighted item
  and can clip tall pages vertically — that's a compiled-in behaviour, deferred to a future
  recompile (tracked in TODO → Backlog).

### 🛑 Jailbreak Crash Fix

Jailbreak mode crashed with a native access violation ~1–2 s after the plugin loaded — every
time, while no other mode crashed. Cause: the mode loaded **CS2Fixes (a heavy native MetaMod
plugin) alongside the self-contained CSS Jailbreak plugin**, and the two conflict at the native
level. Dropped `zombie`/CS2Fixes from the mode (`_MODE_PLUGIN_NAMES["Jailbreak"] = ["jailbreak"]`).
*Confirmed working.*

### ⬇️ Workshop Download Overhaul

- **Real per-MB progress** — downloads report `X / Y MB (Z%)` against Steam's reported file size
  (`/api/state` → `dl_progress`); the UI bar is now a determinate fill, not an indeterminate stripe.
- **Stage → verify → promote** — DepotDownloader now writes to an `<id>.partial` folder; only
  after verifying a `.vpk` is present and the size matches Steam (≥99%) is it promoted to the live
  workshop dir. Failed/cancelled/partial downloads are deleted instead of leaving empty folders.
- **Fixed the download UI not updating live** — the progress bar/status only refreshed on a tab
  switch because the update code gated on `currentPage === 'workshop'` (the page is actually
  `maps`); removed the bad guard. Also fixed a stale grid id and a post-cancel flicker.

### 🚩 Workshop Command-Filter Automation

Some workshop maps need `-disable_workshop_command_filtering` (their map logic runs server
commands CS2 otherwise blocks). The tool now:
- **Auto-detects** the need by scanning each map's Steam description for the flag.
- Adds the launch flag **only for flagged workshop maps** (filter stays on for everything else).
- Provides a per-map override chip (auto → ON → OFF) and a "Scan command-filter needs" button.
- Persists results in the config (`cmdfilter_auto` / `cmdfilter_override`).

### 🧟 Zombie / Mode Plumbing

- **Zombie Escape ZM fix** — `zombie_ze`'s `cs2fixes.cfg` is now a full copy of the base config
  with `zm_enable 1` (the previous 3-line override clobbered the whole config). Zombie Escape now
  also allows official (non-workshop) maps.
- **Mode-switch hardening** — plugin-swapping mode changes route through a clean
  stop → wait-for-exit → start (`_restart_into`); a lifecycle `RLock` makes start/stop/boot/crash
  transitions atomic; `stop_server` is non-blocking (fixes the dropped-fetch "stop button" bug).

### 🎨 UI & Diagnostics

- **Keyboard cheat sheet** — `?` (or a header `?` button) opens a shortcuts overlay; `Esc` closes.
- **Richer empty states** — Players / Workshop / Presets / Bans now show an icon + title +
  call-to-action instead of plain text.
- **Darker theme** — base surfaces and ambient glow toned down a notch from the v0.9.0 lift;
  the top-left ambient glow further dimmed so it no longer washes out the sidebar.
- **Sharper app icon** — `emblem.ico` regenerated from the hi-res source, square-padded and
  LANCZOS-downscaled at every size (16–256), fixing the pixelated taskbar icon.
- **Status fixes** — Public IP click now copies `connect ip:port`; the Start button keeps a
  full border when it's the only control shown.
- **`-condebug`** added to the server launch so the full engine console (incl. native crash
  output) is captured to `csgo/console.log` — this is what finally pinned the Jailbreak crash.

### 📚 Documentation

- Added BIBLE.md, ROADMAP.md, TODO.md, and
  INGEST.md — project vision, phased plan, working checklist, and a structural
  index of the source tree.
- README plugin table reflects B3none Retakes and the full per-mode plugin set.

---

## v0.9.0 — 2026-05-26

This is the largest update yet. The UI has been comprehensively redesigned with theming support, a new Appearance & Settings section, fully configurable keybinds, and a raft of quality-of-life improvements to workshop management, map browsing, and day-to-day server operation.

### 🔌 Plugin Audit & Warcraft Mode

#### Removed deprecated / abandoned plugins
Eight plugins whose upstream repos were archived or had no meaningful update in 2+ years have been removed:

| Plugin | Reason |
|--------|--------|
| ZombieSharp | Repo archived Nov 2025 |
| SharpTimer | Repo archived Jun 2024 |
| LiteMapChooser (RockTheVote) | Last release Apr 2024, ~2 years stale |
| cs2-gungame | Last release May 2024, ~2 years stale |
| cs2-deathrun-manager | Last release Sep 2024, ~2 years stale |
| cs2-instaplant | Last release Dec 2023, abandoned |
| ScoutsNKnives | Single release Nov 2023, abandoned |
| cs2-OneInTheChamber | No traceable repository |

The following game modes were removed along with their core plugins: **Zombies**, **Surf**, **KZ / Climb**, **Gun Game**, **Deathrun**, **Scouts & Knives**, **One in the Chamber**.

#### Added Warcraft mode
A new **Warcraft** game mode backed by [CS2-Warcraft-Plugin v4.1.1](https://github.com/NightFuryPrime/CS2-Warcraft-Plugin) (released 2026-05-25). Features nine RPG character classes (Barbarian, Mage, Necromancer, Paladin, Ranger, Rogue, Shapeshifter, Tinker, ShadowBlade), XP-based levelling to 16, unlockable ultimates, and purchasable magical items. Runs on any standard map.

#### Plugin bundle updates
All bundled plugins have been audited for map coverage and updated to their latest releases:

- **cs2-retakes → v3.0.4** — re-pulled with the full map-configs release; spawn points are now pre-configured for all 10 official maps plus `de_ancient_night`
- **RetakesAllocator → v2.4.2** (yonilerner/cs2-retakes-allocator) — updated from the stale B3none build
- **MatchZy → v0.8.15** — refreshed bundle with latest coach-spawn configs
- **K4-Arenas-Bots → v2.0.8** — updated; corrected copy rule (no longer requires an `extracted/` staging folder)
- **CS2Fixes (MetaMod)** — assigned to Deathmatch and Jailbreak modes for engine-level stability and hit-registration improvements
- **Deathmatch map pool** — restricted to the four maps with pre-configured spawns (`de_dust2`, `de_inferno`, `de_mirage`, `de_vertigo`); remaining maps can be added using the in-game spawn editor

---

### ✨ New Features

#### Appearance & Settings Tab
A dedicated settings page accessible from the sidebar.

- **Theme selector** — Dark, Light, and System (follows OS preference)
- **Accent colours** — choose from Purple, Blue, Teal, Green, Orange, or Red; the accent flows through every button, glow, border highlight, and background radial gradient
- **Compact mode** — tighter spacing throughout the UI for smaller displays
- **Confirm before stopping** — optional confirmation dialog before shutting down the server
- **Auto-scroll log** — keep the live log pinned to the latest entry
- **Log line limit** — configurable memory cap (200 / 400 / 800 lines)
- **Browser notifications** — desktop alerts when the server starts, stops, or crashes

#### Keybinds
Configure keyboard shortcuts for any server action — ideal for private hosting where alt-tabbing is impractical.

- Bindable actions: **Stop Server**, **Quick Restart**, **Pause Match**, **Unpause Match**, **Restart Round**, **End Warmup**, **Add Bot**, **Kick All Bots**
- Click any keybind field → press your key (F1–F12 work unmodified; any key works with Ctrl / Alt / Shift)
- Backspace / Delete clears a binding; Escape cancels
- Conflict detection — warns if a key is already bound to another action
- Binds are saved to localStorage and survive app restarts
- Global handler never fires while typing in a text field or while a modal is open

#### Quick Restart
A new circular-arrow button sits between Start and Stop on the Status page.

- Saves the current map and game mode before stopping
- Stops the server, polls until the process exits (up to 30 s), then starts it again with the exact same settings
- No dropdowns to reconfigure — one click is all it takes
- Also available as a keybind

#### Map Search
A search box on the Maps page lets you filter by name in real time.

- Searches official maps by ID and workshop maps by name or ID simultaneously
- Section headings and the Workshop divider hide automatically when their section has no results
- Filter persists when switching game modes

#### Workshop Download Improvements
- **Live status bar** — replaces the plain 5 px progress stripe with a pulsing dot and real-time status text fed directly from DepotDownloader output (e.g. *Downloading workshop item…*, *… downloading (30s)*, *✓ Download complete*)
- **Automatic button reset** — when a download finishes, the Cancel button reverts to Download and the maps grid refreshes automatically to show the new map
- **Paste button** — a clipboard icon inside the Workshop Map ID input field; reads your clipboard, strips non-numeric characters (so pasting a full Steam URL works), and fills the field
- **Pre-flight credential check** — attempting to download without saved Steam credentials now returns an immediate error with a redirect to Config → Steam Account, instead of silently failing in the background log

#### Bundled Map Thumbnails
All official map thumbnail images are now shipped inside the application package.

- Eliminates all runtime CDN dependency — no Liquipedia requests, no network errors, no per-user hammering of a third-party server
- Falls back to the CS2 dedicated server's own panorama directory if the server is installed locally (higher resolution)

---

### 🎨 UI & UX Improvements

- **Neon glow background** — subtle layered radial gradients on the main app background that shift hue with the selected accent colour; light theme uses a much softer version
- **Session active indicator** — a pulsing green dot labelled "Session active" sits above the sign-out button in the sidebar, making the auth state visible at a glance
- **Sign Out** — the logout button is renamed "Sign Out" with a tooltip describing what it does; clicking it ends the PIN session and returns to the lock screen
- **Official / Workshop map divider** — a centred label with fading border lines separates the two map sections on both the Maps page and the Status page dropdowns
- **GSLT Token label** — renamed from the jargon abbreviation to **"Game Server Login Token (GSLT)"** with a descriptive hint linking to steamcommunity.com/dev/managegameservers and explicitly noting it can be added later if skipped during setup
- **Workshop map names** — the Workshop dropdown on the Status page now shows map names fetched from the Steam API rather than raw numeric IDs
- **Copy Log button** — a button in the live log header copies the entire visible log to the clipboard

---

### 🔧 Server Engine Fixes

#### gameinfo.gi Auto-Management
The tool now fully automates the `gameinfo.gi` patching lifecycle required by MetaMod/CounterStrikeSharp.

- **Auto-patch** — when starting a mode that requires MetaMod or CSS plugins, the MetaMod search path is added to `gameinfo.gi` automatically (restoring from a `.oblivion.bak` backup if one exists)
- **Auto-unpatch** — when switching to a vanilla mode (Competitive, Casual, Wingman), the MetaMod entry is removed from `gameinfo.gi` automatically; this fixes the `0xE0434352` CLR crash caused by an outdated CSS build loading on vanilla servers
- Idempotent — safe to call multiple times; skipped if the file is already in the correct state

---

### 🐛 Bug Fixes

- **Light theme subtitle colour** — `--sub` was set to an invalid 7-digit hex value (`#6060880`), causing all subtitle text to silently inherit the dark-theme colour; fixed to `#606088`
- **Map thumbnail path** — the panorama thumbnail lookup was constructed from the steamcmd root directory, skipping the `steamapps/common/Counter-Strike Global Offensive/` middle segment; the correct CS2 install root is now derived from `CS2_ADDONS_DIR`
- **Quick Restart race condition** — the background state poll interval is paused for the duration of a restart sequence so it cannot concurrently re-enable the Restart button while the shutdown wait is in progress
- **Workshop download status — mode guard** — `_updateDlStatusUI` no longer calls `loadWorkshopMapsGrid` before the first state poll has returned a game mode, preventing a silent fallback to Competitive mode for the map-click handler
- **Keybind localStorage merge** — `loadAppSettings` now deep-merges the `keybinds` sub-object so newly added keybind actions are not silently discarded when upgrading from an older settings snapshot
- **Keybind Space key** — binding the Space key previously stored an invisible character in localStorage; it is now stored and displayed as `Space`
- **Workshop section separator** — the Workshop divider no longer disappears prematurely while the workshop map grid is still loading
- **Quick Restart button height** — `.btn-icon` was missing an explicit height, causing the button to render shorter than the flanking Start/Stop buttons
- **Keybind row border** — the fragile `nth-last-child(2):nth-child(odd)` CSS rule incorrectly removed borders at narrow viewport widths; simplified to `:last-child` only

---

### 📦 Build / Installer

- Inno Setup architecture identifier updated from deprecated `x64` to `x64compatible`
- Added `UsedUserAreasWarning=no` to suppress the HKCU + admin install warning

---

*Previous release: v0.8.6*
