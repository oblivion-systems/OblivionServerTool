# Oblivion Server Tool — Changelog

---

## Unreleased
*Post-v0.9.1 fixes (committed + pushed, not yet tagged). Bump `APP_VERSION` → `0.9.2`
and tag `v0.9.2` when ready to cut a release.*

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

- Added [BIBLE.md](BIBLE.md), [ROADMAP.md](ROADMAP.md), [TODO.md](TODO.md), and
  [INGEST.md](INGEST.md) — project vision, phased plan, working checklist, and a structural
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
