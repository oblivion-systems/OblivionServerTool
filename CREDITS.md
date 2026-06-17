# CREDITS — Oblivion Server Tool

> Every plugin and runtime this app bundles, downloads, or hard-depends on
> belongs to the people listed below.  None of this work is mine.  My
> contribution sits at the **management layer** — discovering, deploying,
> configuring, cleaning up.  When you use this app and run any of these
> plugins on your server, **you are running their work**.
>
> If you are one of the authors below and something here is wrong —
> missing, misspelled, the wrong license, the wrong link, or you want
> your name listed differently — please open an issue or a PR.  I will
> fix it the same day.

---

## CS2 server runtime (auto-installed by the app)

| Project | Author / Maintainer | Upstream | License | Role |
|---|---|---|---|---|
| **MetaMod : Source 2** | AlliedMods team | <https://www.sourcemm.net/> | GPLv3 | Engine-level plugin loader.  Every CSS plugin below depends on it. |
| **CounterStrikeSharp** | roflmuffin et al. | <https://github.com/roflmuffin/CounterStrikeSharp> | MIT | C# host that runs every modern CS2 plugin in this bundle. |

The app downloads both at install time from the canonical upstream URLs
(see `cs2servergui/config.py` → `RUNTIME_METAMOD_DEFAULT_URL` and
`RUNTIME_CSS_DEFAULT_URL`).  Operators can override either to pin a
different build.  Nothing is repackaged; the upstream zip is extracted
verbatim into `csgo/addons/`.

---

## Plugins bundled in this repository

These ship as binary blobs inside `cs2servergui/plugins/<slug>/` and are
deployed into the operator's CS2 server when they activate the
corresponding mode.  Every blob is the upstream author's compiled
release unless explicitly noted otherwise.

| Mode(s) | Plugin | Author | Upstream | License | Notes |
|---|---|---|---|---|---|
| 5v5 / 4v4 / 3v3 / Practice | **MatchZy** | shobhit-pathak | <https://github.com/shobhit-pathak/MatchZy> | MIT | Competitive match controller.  Bundled binary is the upstream release. |
| Deathmatch | **DeathmatchPlugin** | shobhit-pathak (Charles Thomson) | <https://github.com/shobhit-pathak/DeathmatchPlugin> | MIT | Instant respawn + spawn protection.  Originally published as `CS2-Deathmatch`. |
| Retakes | **CS2 Retakes** | B3none | <https://github.com/B3none/cs2-retakes> | GPL-3.0 | Bombsite retake controller. |
| Retakes | **RetakesAllocator** | yonilerner | <https://github.com/yonilerner/cs2-retakes-allocator> | MIT | Loadout allocator for Retakes. |
| 1v1 / 2v2 | **K4-Arenas** | K4ryuu | <https://github.com/K4ryuu/K4-Arenas-SwiftlyS2> (originally `CS2-K4-Arenas`; upstream archived 2025) | GPL-3.0 | Duel ladder with elo + queue.  The bundled binary is the CSS-targeting build from the original upstream; the maintainer has since archived it and moved development to the SwiftlyS2 branch. |
| 1v1 / 2v2 | **K4-Arenas-Bots** | K4ryuu | upstream archived/removed | GPL-3.0 (inferred — author's other plugins are GPL-3.0) | Bot adapter for K4-Arenas.  Bundled binary preserved from prior K4ryuu release.  If author prefers different attribution, please open an issue. |
| 1v1 / 2v2 | **K4-ArenaSharedApi** | K4ryuu | upstream archived/removed | GPL-3.0 (inferred) | Shared API surface for the Arenas plugin family. |
| 1v1 / 2v2 | **KitsuneMenu** | Kxnrl / Kitsune team | upstream not currently locatable on GitHub | unknown (please open an issue if you are the author) | Menu library K4-Arenas depends on.  Bundled binary is the upstream release pinned at the time K4-Arenas was packaged. |
| Jailbreak | **CSS-Jailbreak** | EdgeGamers community | <https://github.com/edgegamers/Jailbreak> | GPL-3.0 | T-vs-CT prison roleplay.  Upstream is community-maintained at edgegamers/Jailbreak; per the upstream README, the binary was built for EdgeGamers infrastructure and may need configuration adjustments for independent deployment. |
| Warcraft | **WarcraftPlugin** | **NightFuryPrime** (fork of Wngui/CS2WarcraftMod) | <https://github.com/NightFuryPrime/CS2-Warcraft-Plugin> | GPL-3.0 (inherited from original Wngui/CS2WarcraftMod) | **Custom-patched build by Oblivion** — see "Modifications" below.  NightFuryPrime's fork does not ship a LICENSE file; the GPL-3.0 terms inherit from the original at <https://github.com/Wngui/CS2WarcraftMod>. |
| Warcraft | **ModelPrecacher** | Oblivion (this project) | this repo | BSL 1.1 (pre-v1.0 commits remain MIT per [LICENSE.md](LICENSE.md)) | Original work to precache class models server-side. |
| Zombie modes | **CS2Fixes** | Source2ZE | <https://github.com/Source2ZE/CS2Fixes> | GPL-3.0 | Engine fixes that unlock ZombieMod-style gameplay. |
| Zombie modes | **ZombieMod** | JayCroghan (fork of Source2ZE/CS2Fixes) | <https://github.com/JayCroghan/ZombieMod> | GPL-3.0 (inherited from CS2Fixes) | The actual zombie-game variant bundled with the app — JayCroghan's content fork on top of CS2Fixes.  Attribution to JayCroghan is in `cs2servergui/plugins/zombie/README.txt` and `LICENSE.md`. |
| Zombie Escape | **MultiAddonManager** | Source2ZE | <https://github.com/Source2ZE/MultiAddonManager> | GPL-3.0 | Mounts the ZombieReborn workshop pack. |
| Zombie Escape | **ZombieReborn** | Source2ZE | <https://github.com/Source2ZE/ZombieReborn> | GPL-3.0 | Loaded as a workshop addon, not bundled directly. |

**License confidence — please verify before relying.**  I have made a
best-effort match between plugin and upstream, but I'm not a lawyer and
not every CS2 plugin author publishes a clean SPDX header in their repo.
If you are an author and the license column above is wrong for your
project, **please file an issue and I will correct it immediately**.

---

## Modifications by Oblivion

### WarcraftPlugin (NightFuryPrime fork, custom-patched build)

The `WarcraftPlugin.dll` shipped at
`cs2servergui/plugins/warcraft/addons/counterstrikesharp/plugins/WarcraftPlugin/`
is **not** a direct upstream release.  It is built from a local fork
with the following patches on top of NightFuryPrime's v4.1.1 base:

| Patch | What it fixes |
|---|---|
| Chat-command 1.5 s cooldown per `(SteamID, command)` | Prevents recv-queue overflow + `SteamNetworkingSockets lock held for 263 ms ... thread starvation` under chat-spam |
| Menu-open dispatcher with frame-time budget guard | Defers menu opens when previous frame ran >20 ms, avoids stacking long-frames |
| `AbilityBenefitAnnouncer` chat-broadcast throttle | Rate-limited from every-kill to every-N to prevent recv overflow |
| `Database.ResetClients` no longer blocks main thread | `ResetClientsAsync()` + `FireAndForget` at map-start / map-end |
| `OnClientPutInServerAsync` entity-list race fix | Native entity lookups moved back to main thread |
| `MenuTypeManager.GetPlayerMenuType` MySQL block removed | Fire-and-forget cache populate instead of `.GetAwaiter().GetResult()` |
| `_commandCooldowns` race fix | `Dictionary<>` → `ConcurrentDictionary<>` |
| `Plugin.Unload` hardening | Timers + queues cleared deterministically |
| Localised English help text | `lang/en.json` updated to list all `!class !skills !shop !ult !info !reset` |

**The source for these patches is published as a fork at**
<https://github.com/oblivion-systems/CS2-Warcraft-Plugin> on the
`oblivion-patches-4.1.1` branch, against NightFuryPrime's upstream
commit `fda4fa3` ("Hotfix 4.1.1 #2").  Each patch is also documented
in this repo's [`CHANGELOG.md`](CHANGELOG.md) under the v0.16.13 entry,
and in the fork's commit message.  If NightFuryPrime prefers a different
distribution arrangement — patches merged upstream, attribution moved
entirely off this repo, the fork taken down, or any other ask —
please open an issue and we will accommodate.

The bundled DLL ships under the same license as NightFuryPrime's
upstream.

### ModelPrecacher

This is **original work** developed by Oblivion (this project) for the
Warcraft pack.  Pre-caches class-specific player models at map start so
class changes don't cause first-spawn stutters.  Licensed under the
same terms as this repository (currently MIT pre-v1.0, BSL 1.1 v1.0+).
Source at `_plugins_src/ModelPrecacher/` in this repo.

### All other plugins

Shipped as their upstream author's compiled release.  No source
modifications.

---

## Third-party .NET dependencies bundled with WarcraftPlugin

The `cs2servergui/plugins/warcraft/addons/counterstrikesharp/plugins/WarcraftPlugin/`
folder ships ~60 .NET dependency DLLs alongside `WarcraftPlugin.dll`,
inherited from NightFuryPrime's release zip.  Notable licenses:

- **Newtonsoft.Json.dll** — © James Newton-King, MIT
- **Dapper.dll** — © Stack Exchange, Apache 2.0
- **MySqlConnector.dll** — © Bradley Grainger et al., MIT
- **Serilog.dll**, **Serilog.Sinks.Console.dll**, **Serilog.Sinks.File.dll** — © Serilog Contributors, Apache 2.0
- **Microsoft.\*.dll** — © Microsoft, MIT (most) / Apache 2.0 (some)
- **SQLitePCLRaw.\*.dll** — © Eric Sink & SourceGear LLC, Apache 2.0
- **McMaster.NETCore.Plugins.dll** — © Nate McMaster, Apache 2.0
- **Tomlyn.dll** — © Alexandre Mutel, BSD 2-clause
- **System.\*.dll** — © .NET Foundation, MIT / Apache 2.0
- **e_sqlite3.dll** (native, `runtimes/win-x64/native/`) — © D. Richard Hipp, **public domain (SQLite)**

This list is non-exhaustive; the canonical list is the file contents of
the WarcraftPlugin folder.  The NightFuryPrime upstream release zip
preserves each DLL's bundled license metadata (where the author embeds
it); the same DLLs are shipped here verbatim.  No re-signing, no
re-packaging.

---

## Other dependencies

Python packages (`requirements.txt`):

- **Flask** — © Armin Ronacher & Pallets, BSD 3-clause
- **werkzeug**, **jinja2**, **itsdangerous**, **click**, **markupsafe** — Pallets, BSD 3-clause
- **pywebview** — © Roman Sirokov, BSD 3-clause
- **discord.py** — © Rapptz, MIT
- **segno** — © Lars Heuer, BSD 3-clause
- **keyring** — © Jason R. Coombs et al., MIT

JavaScript / browser:
- No bundled JS dependencies — the SPA is hand-written vanilla JS + CSS
  with no npm chain.

Tools used during build:
- **PyInstaller** — © PyInstaller Development Team, GPL with exception
- **Inno Setup** — © Jordan Russell, Inno Setup License (BSD-style)
- **Microsoft Edge WebView2 Evergreen Bootstrapper** — © Microsoft,
  redistributable per Microsoft's distribution agreement.  Downloaded
  on demand by `tools/fetch_webview2.ps1`; never modified, never
  re-signed.

---

## Visual assets + typefaces

- **Counter-Strike 2 map thumbnails** (`cs2servergui/static/images/map_thumbs/*`)
  — screenshots of official CS2 maps © Valve Corporation, used to
  visually identify each map in the picker.  Valve permits fan use of
  CS2 assets under their content usage rules; if Valve prefer a
  different arrangement, open an issue and we will swap or remove
  them.
- **App logo / emblem / favicon** (`emblem.ico`, `cs2servergui/static/favicon.*`,
  `cs2servergui/static/images/{emblem,logo}.png`) — original artwork
  produced for this project by the maintainer.  BSL 1.1 / Apache 2.0
  per the repo LICENSE.
- **Typefaces** — the SPA loads two open-licensed fonts from Google
  Fonts CDN:
  - **Space Grotesk** — © Florian Karsten, SIL Open Font License 1.1
  - **JetBrains Mono** — © JetBrains s.r.o., SIL Open Font License 1.1

  Neither typeface is bundled in this repo; both are fetched on demand
  from `fonts.googleapis.com` at runtime.

---

## How to report a credit problem

Open an issue at the project's GitHub repo with the title
**"Attribution fix: \<your project name\>"** and I will respond within
24 hours.  Acceptable resolutions include:

- Correcting the upstream URL
- Correcting the license
- Correcting the author name or preferred display
- Adding a previously-missed contributor
- Removing the plugin entirely from this distribution if you prefer not
  to be associated with it

The maintainer's intent is for every plugin author to feel **clearly
credited** and to have **easy recourse if anything here misrepresents
their work**.
