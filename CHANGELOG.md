# Oblivion Server Tool — Changelog

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
