# PLAN — Strategic Roadmap

> Forward plan as of 2026-06-03.  Distilled from a strategic conversation
> covering hosting, monetization, distribution, license model, platform
> architecture, and plugin bundling strategy.
>
> This is the **strategy** doc.  Phase-level granular tasks live in
> [TODO.md](TODO.md); release notes in [CHANGELOG.md](CHANGELOG.md);
> code map in [INGEST.md](INGEST.md).

---

## Single-sentence vision

**Oblivion: the easiest way to add plugins to your self-hosted game
server.  Pick from a curated catalog, click install, done — across
multiple games, on Windows or Linux, with conflict detection and
automatic updates.  Free and open-source.**

The veto / match-setup / Discord-bot / SPA layers are all real and
valuable, but they're not the lead.  **Plugin management UX is the
headline differentiator**, because that's the pain point that
actually drives people to paid managed hosting.

---

## The two-audience strategy

Oblivion serves two distinct audiences, and the same engineering
work satisfies both for different reasons.  This is intentional and
strategic — not an accident of scope.

### Audience 1 — "Average Joe" (the founding audience)

The friend-group host.  Wants to run a CS2 / TF2 / Palworld server
for their crew on weekends.  Doesn't want to learn RCON, what a
`.vdf` file is, or why their server crashes when they install three
plugins.

What they value:
- **"It just works"** out of the box
- **Curated picks**: "give me something good for casual play"
- **No jargon** — plain-language descriptions, no
  `mp_warmup_pausetimer` exposed by default
- **Forgiving** — they'll click the wrong button; the tool shouldn't
  punish them
- **Free** — they're hosting for friends, not paying customers

### Audience 2 — "Pro server hoster" (the climb-up market)

Tournament organizer, community admin team, scrim-organizer for a
local league, semi-commercial host.  Already comfortable with RCON,
plugins, Linux.

What they value:
- **Reliability** — uptime, predictable behavior, no silent
  failures
- **Automation** — scheduled tasks, multi-server management,
  templated deployments
- **Auditability** — who changed what when, configuration history,
  rollback
- **Headless / Docker / Linux** — proper server-grade deployment
  shape
- **Time savings** — willing to pay for things that save 30 min/week

### Why one product can serve both

The same engineering decisions land differently for each audience:

| Feature | Average Joe gets | Pro gets |
|---|---|---|
| Plugin Manager with curated packs | "I don't know what plugins I want — give me the Competitive 5v5 pack" | "Push a tested pack to 5 servers in one click" |
| One-click factory reset | "Let me start over without uninstalling the tool" | "Spin up a fresh test environment" |
| Auto-restart on crash | "I don't have to babysit the server during game night" | "Acceptable uptime without on-call rotation" |
| Plain-language descriptions | "I finally understand what MatchZy does" | "Onboarding new junior admins takes 10 min, not 2 hours" |
| Pre-flight panel | "Big green ✓ tells me I'm ready to start" | "Catch config drift before going live for a tournament" |
| Discord bot integration | "Captain links auto-DM, no copy-paste" | "Match results post to community channel automatically" |
| Atomic config saves + rollback | "I clicked the wrong thing and nothing got lost" | "Survive power cuts during a 50-server fleet deploy" |
| Headless / Linux daemon | (rarely uses) | "Runs on the VPS, managed from phone" |
| Scheduled tasks (Pro tier) | (rarely needs) | "Daily map updates at 5am, weekly auto-restart Sundays" |
| Multi-server management (Pro tier) | (one server is enough) | "Promotes Oblivion from a tool to infrastructure" |

**The discipline this implies for engineering**: "consumer-grade UX,
pro-grade reliability."  Every feature must clear two bars:

1. **Can the average Joe figure it out without docs?**
2. **Does it behave deterministically under the load a pro would
   put on it?**

Failing either bar = the feature isn't done.

### Why this is a strategic moat

The competitive landscape:

| Tool | Average Joe | Pro |
|---|---|---|
| **AMP (Cube Coders)** | ❌ too technical | ✅ established |
| **Pterodactyl** | ❌ Docker + Linux + reverse proxy required | ✅ industry standard for hosts |
| **Managed hosting** (Nitrado, GameServerKings) | ✅ trivial | ❌ no control, expensive at scale |
| **Hand-rolled scripts** | ❌ requires sysadmin skill | ⚠️ each org rebuilds the wheel |
| **Oblivion (target)** | ✅ first-class UX | ✅ headless + automation |

**Nobody is currently serving both audiences well.**  Each tool is
locked into its corner.  Oblivion's deliberate two-audience design
hits the gap.

### The natural monetization shape this implies

The average Joe stays on the free tier — that's the audience that
generates word-of-mouth, GitHub stars, and trust signals.  Charging
them is counterproductive.

The pro is the natural Pro-tier customer.  Features they want — and
average Joe doesn't need — make obvious Pro tier candidates:
- Multi-server management
- Scheduled tasks (cron-style)
- Audit log + permission groups (already started with v0.10.x guest PIN)
- Premium curated packs (vetted, supported, breaking-change tested)
- Cloud config sync (manage from any machine)
- Priority Discord support queue
- Health monitoring + alerting (webhook on RCON failure, email on
  uptime drop)
- API / webhook integration (programmatic control)
- Backup / restore automation
- Templates ("every new server gets this base config")

This is the **natural Notion / Linear playbook**: free tier acquires
the consumer audience, pro tier monetizes the team / commercial
audience.  Same product, two pricing tiers.

### The marketing arc that follows from this

The story to tell, by phase:

- **v0.12** (Plugin Manager lands): "Oblivion finally makes running
  a CS2 server feel like installing a phone app."
- **v0.13** (TF2): "Now for TF2 too — same UX, same pack model."
- **v0.14** (Linux + headless + Docker): "Pros: run it on your VPS,
  manage from your phone."
- **v0.15** (first non-Source game): "Multi-game.  Self-hosted.
  One UI."
- **v1.0**: "Self-hosted game server platform.  Accessible enough
  for first-timers, reliable enough for tournament operators."

Each release earns one new audience segment without losing the
previous one.  That's the climb.

---

## The "noob-friendly plugin attachment" thesis

Every plugin-managed self-hosting tool today is built for sysadmins:

| Tool | Plugin UX |
|---|---|
| **Pterodactyl** | None.  You SFTP plugins to the right directory yourself. |
| **AMP** | Per-template config files, technical, no curated catalog. |
| **Hand-rolled** | Find on GitHub → download → extract → copy to right paths → edit config → restart → hope. |

**The market gap**: there is no Steam-Workshop-for-server-plugins
experience.  Operators who want to add plugins either pay a managed
host (R200+/month per server, plus zero control) or learn enough
Linux+RCON+plugin-format to do it manually.  Most give up and pay.

**Oblivion's wedge**: make plugin install feel like installing a
phone app.  Browse → install → done.  Update notifications.  Conflict
detection.  Plain-language descriptions.  One-click factory reset
when things break.

If you nail this for CS2, the same UX trivially extends to TF2,
Palworld, Valheim, etc. — every game with a plugin/mod ecosystem
inherits it.  The plugin UX is the franchise.

### What "noob-friendly" actually means — feature shortlist

These are the features that materially move the needle from
"power tool" to "anyone can do this."  Grouped by priority.

#### Table stakes (v0.12 — must ship for the wedge to mean anything)

1. **Plugin catalog browser inside the SPA** — searchable list of
   known-good plugins with name, author, license, description,
   homepage link.
2. **One-click install with automatic dependency resolution** — picks
   MatchZy, tool fetches CSS + MetaMod + MatchZy in correct order;
   patches gameinfo.gi if needed; verifies install.
3. **Plain-language plugin descriptions** — not "CS#-based competitive
   matchmaking framework" but "Adds knife rounds, scoring, and
   pause/unpause for tournament-style matches."
4. **Update notifications** — when upstream releases a new version,
   banner in the SPA: "MatchZy v2.5 → v2.6 available. [Update now]
   [Skip]".
5. **One-click factory reset** — nuke all plugins, return to vanilla
   CS2.  For "let me start over without uninstalling the whole tool."
6. **License + author display per plugin** — credit upstream + trust
   posture in one stroke.

#### Magic features (v0.13-v0.14 — the differentiators)

7. **Mode-aware plugin packs** — "Competitive 5v5" pack installs
   MatchZy + CSStats + InGameInfo at known-good versions in one
   click.  "Casual Deathmatch" pack installs CS2-Deathmatch + good
   defaults.  "Warcraft" pack installs the right Warcraft variant +
   ModelPrecacher + tuned configs.
8. **Conflict detection** — registry knows MatchZy and SimpleAdmin
   both register `!pause`.  Tell the operator BEFORE install, offer
   to remap.
9. **Onboarding wizard** — first run, asks "I want to play
   [Competitive / Casual / Modded] with [N] friends" → auto-installs
   the canonical plugin combo, configures the server, done.
10. **Smart troubleshooter** — server fails to start: parse the log,
    identify which plugin is failing, suggest "disable this plugin?"
    Like Windows reliability monitor for game servers.
11. **Disable-don't-uninstall toggle** — flip a plugin off for one
    session without losing its config.  Lets operators isolate
    crashes ("is it CSStats causing the crash?").
12. **Visual mode→plugin dependency map** — diagram in the SPA
    showing what plugins each mode needs and why.

#### Compounding magic (v1.0+ — the ecosystem plays)

13. **Shareable plugin packs** — operator exports current setup as a
    `.oblivion-pack` JSON; another operator one-click imports.
14. **Community-curated packs** — "Phoenix League Pack" maintained by
    a tournament organizer, used by 50 servers.
15. **Plugin presets per friend group** — save "my Tuesday-night
    setup" + "my Friday-night setup" + switch with a click.
16. **Plugin health monitor** — periodic checks: all files present?
    Has upstream had a security advisory?  Version still supported by
    current CS2?
17. **Plain-language conflict reasoning** — not "ConVar collision
    detected"; instead "MatchZy and CS2-Casual both want to set
    mp_warmuptime.  They'll fight over it.  Pick which one wins."
18. **Try-before-promote** — install plugin in "preview" mode,
    auto-uninstall in 24h if not actively kept.  Lets nervous
    operators experiment.

#### Pro-tier candidates (Year 2+ monetization)

- Multi-server plugin push (deploy a pack to 5 servers at once)
- Staging environments (test on dev box, promote to prod)
- Premium curated packs (vetted, supported, breaking-change tested)
- Custom plugin pack development (consultancy)

### Concrete UX bar

When a non-technical operator visits the Plugin Manager tab, they
should be able to answer all of these without reading docs:

- **"What's installed?"** → status panel at the top
- **"What's broken?"** → red ⚠ badges on troubled plugins
- **"What needs updating?"** → blue ⬆ badges with one-click update
- **"What plugins exist for what I'm trying to do?"** → catalog
  filterable by mode
- **"What does this plugin actually do?"** → plain-language
  description, not jargon
- **"Will this conflict with what I already have?"** → conflict check
  on install
- **"How do I undo this?"** → one-click rollback / disable
- **"How do I start over?"** → factory reset

If any of those answers requires the operator to read a README, the
UX failed.

### What this implies for the architecture

- The plugin registry isn't a hidden internal data structure —
  **it's the headline product surface**.
- The Plugin Manager tab in the SPA needs **first-class design
  attention**, not "wire it up and ship."
- Plain-language descriptions must be authored per-plugin (you write
  them, since plugin authors don't think in operator terms).
- The catalog itself becomes a **community-contributable asset**
  (separate `OblivionPluginRegistry` repo, PRs welcome from anyone).
- Dependency resolution gets a proper solver (probably a tiny
  topological sort over the registry — not heavy, but real code).
- Plugin packs need their own data shape (a pack is a curated list
  of {plugin, version pin, config defaults}).

---

## Phase 0 — Pre-Friday (this week)

**Goal:** ship v0.11.1 to real users without surprises.  Freeze
feature work; validate what we already shipped.

| Task | Owner | Status |
|---|---|---|
| Push `fadfef0` (issue_tokens idempotency hotfix) | me | open — push as v0.11.2 tagged release |
| Rebuild `.exe` via `build.bat`; cut GitHub release | you | open |
| Cloudflare-tunnel smoke test (captain link → claim → veto over real LTE on phone) | you | open |
| Discord Test Embed + Test DM buttons exercise | you | open |
| 5v5 dress rehearsal with bots — full flow through `matchzy_loadmatch` | you | open |
| `MOBILE_CHECK.md` walkthrough on actual phone | you | open |

**Explicitly NOT in scope for Phase 0:**
- Session persistence feature (queued for Phase 1)
- Pre-flight panel
- FRIDAY.md runbook
- Any new feature

**Friday fallback:** if anything breaks during pre-flight, run Friday
on the current setup.  Don't bet game night on an untested move.

---

## Phase 1 — Post-Friday validation (2-4 weeks)

**Goal:** learn what actually matters from real-user feedback; prep
for the platform pivot without committing to code yet.

- Run 2-3 sessions.  Collect: what broke, what users asked for, what
  flows felt friction-y.
- Write **`PLATFORM.md`** — planning doc for v0.12 covering both the
  driver abstraction AND the plugin registry as one architectural
  move.  Defines: driver interface, registry schema, plugin fetch
  flow, caching, license-display, migration plan.
- Draft **monetization infrastructure** in a branch (don't enable
  yet): GitHub Sponsors profile copy, affiliate-link plan, donate
  button mockups.  Sits behind a `monetization` branch until v1.0.

Optional small features if they emerge from real use (not committed):
- **Session persistence** — `oblivion_veto_active.json` survives app
  restart, prompts "resume in-flight session?" on startup.  Solves
  loadshedding / accidental-close scenarios.
- **Pre-flight panel** — one button = green ticks across CS2/RCON/
  ports/MatchZy/share-URL/Discord/disk.  Replaces "operator manually
  checks five things before each session."
- **Operator undo last veto step** — captain misclick recovery.

---

## Phase 2 — v0.12 architecture refactor (3-5 weeks)

**Goal:** prepare for multi-game without changing user-visible behavior.
Two combined moves in one cycle:

### Move A — Driver abstraction
- `cs2servergui/` → `oblivion/`
- `oblivion/core/` — game-agnostic: Flask, auth, SSE, config, Discord
  bot, session store, brute-force protection, app self-update
- `oblivion/drivers/cs2/` — everything CS2-specific: modes, plugins,
  veto, MatchZy, workshop, gameinfo.gi patching, RCON Source-protocol
  specifics
- **Stub the OS abstraction**: introduce `oblivion/core/platform.py`
  but only with the Windows implementation.  Every OS-specific
  call (`taskkill`, `wmic`, `netstat` parsing, `%APPDATA%`)
  funnels through this module.  Linux implementation lands in
  Phase 3.5, but the seam exists from v0.12.  This is cheap to do
  now (5-10 functions, mostly already isolated) and expensive to
  do later (audit every call site).
- Existing **163/163 tests stay green**
- User-visible change: **none**

### Move B — Plugin registry + first-class Plugin Manager UX
**This is the headline feature, not a footnote.**  See the
"noob-friendly plugin attachment" thesis above for full feature
shortlist + UX bar.

- Bundle only your **original work**: `ModelPrecacher`, Warcraft
  patches, generated configs (K4-Arenas 2v2 round-settings, retakes
  bot-quota rewriter), glue scripts
- Everything third-party → `plugins.json` registry, fetched on-demand
  via `direct` / `github_release` / `composed` sources
- **Plugin Manager tab** with first-class design — searchable
  catalog, plain-language descriptions, one-click install with
  dependency resolution, update notifications, factory reset,
  per-plugin license + author display
- **Plugin packs** as a first-class concept (curated bundles like
  "Competitive 5v5" or "Casual Deathmatch" + operator-saved
  "my Tuesday-night setup")
- **Conflict detection** at install time (ConVar collisions, chat
  command duplicates, port clashes)
- **Disable-don't-uninstall toggle** per plugin (one-session
  troubleshooting without losing config)
- First mode-deploy fetches the relevant plugins (~30 sec, one-time);
  cached locally afterwards
- Plugin author relationships stay clean — Oblivion is an
  orchestrator, not a re-distributor
- The catalog is a **separate community-contributable repo**
  (`OblivionPluginRegistry`) — PRs welcome from anyone, including
  plugin authors themselves

The registry data shape isn't optional polish — it IS the product
surface for the wedge.  Design it that way from day one.

### Why combined?
- Both touch `core.py` deeply; doing them sequentially = touching
  the same hot code path twice
- Both are "introduce an abstraction layer without changing
  user-visible behavior" — one cohesive refactor cycle
- Sets up v0.13 to be straightforward

### Warcraft patches: distribution decision (deferred to Phase 2 start)
Three options, decide once we're refactoring:
1. **Submit upstream** to NightFuryPrime's repo (cleanest if merged)
2. **Ship as separate `OblivionWarcraftPatches` GitHub release**,
   composed onto NightFuryPrime's release via the registry
3. **Bundle as derivative work** — requires NightFuryPrime's license
   permits

Leaning #2 — keeps everything outside the main binary, plays well
with the "no bundled third-party" principle.

---

## Phase 3 — v0.13 second game / TF2 (1-2 weeks)

**Goal:** prove the v0.12 abstraction holds.

- Add `drivers/tf2/` — same Source engine, same RCON, same steamcmd
- Most code = copy-modify-from-CS2.  Cheap proof point.
- If TF2 lands smoothly → abstraction works → move to Phase 4.
- If TF2 fights the abstraction → abstraction was wrong → redo before
  going further.  Better to find out now with a near-twin.

**No veto/match-setup port** to TF2 — veto stays a CS2-only tab.
Niche depth is a feature.

---

## Phase 3.5 — v0.14 Linux support + headless mode (3-4 weeks)

**Goal:** ship a real Linux build.  Not "pywebview-on-Linux as an
afterthought" — proper **headless daemon** shape that matches how
serious self-hosters actually deploy game servers.

### Why Linux matters

- **CS2 dedicated servers run primarily on Linux** in real
  deployments.  Almost every commercial host (Pterodactyl-based or
  otherwise) uses it.  Cloud VPSes are Linux by default.  Saying
  "self-hostable platform" while being Windows-only is a credibility
  hit with the audience that drives r/selfhosted recommendations.
- **The headless deployment shape is cleaner than Windows pywebview.**
  Operator runs Oblivion as a systemd daemon on their Linux box;
  accesses the SPA from their phone/laptop browser via the existing
  Cloudflare tunnel.  No local window to crash, no desktop session
  required, restart-on-boot just works.  This is *Pterodactyl /
  AMP shape*, and it's the right architecture for unattended
  server use.
- **Plugin UX wedge generalizes immediately.**  Linux operators have
  the same "find on GitHub → SFTP → hope" pain Windows operators do.
  Maybe more — they're more likely to be running 2-3 servers
  simultaneously.
- **Linux hobbyists are the core open-source advocate population.**
  Half-star reviews for "Windows-only" tools come from this group.
  Linux support is the easiest single-feature trust win.

### The three Linux deployment shapes

| Shape | Audience | Priority |
|---|---|---|
| **Headless daemon** (systemd unit, no GUI, SPA over LAN/tunnel) | Self-hosters with a Linux server / VPS | ⭐ primary |
| **Docker container** (wraps the headless daemon) | Standard self-hosting deployment style; r/selfhosted norm | ⭐ ships with v0.14 |
| **Linux desktop (pywebview + QtWebEngine)** | Linux desktop users running server on same machine | nice-to-have, not critical |

**Don't try to ship all three at once.** Headless daemon + Docker is
the v0.14 scope.  Linux desktop pywebview comes for free if the
backend is clean, but it's not a target — the headless mode is the
target.

### Architecture changes

The driver abstraction from v0.12 lays half the groundwork; v0.14
adds the OS abstraction on top.

- `oblivion/core/platform.py` — abstract OS-specific calls behind
  an interface.  Windows + Linux implementations.  Covers:
  process listing (`tasklist` / `wmic` vs `ps`), kill-process
  (`taskkill` vs `kill`), netstat parsing (different output
  formats), config dir (`%APPDATA%` vs `~/.config/oblivion`),
  service registration (`sc.exe` vs `systemctl --user`).
- `--headless` CLI flag — boots Flask + Discord bot + crash
  monitor, skips pywebview entirely.  Existing auth + role gate
  + SPA handle the remote-admin case (they were already designed
  for it).
- **Plugin registry gains `os` field per asset** — MetaMod /
  CSS ship per-OS binaries.  Registry knows which to fetch.
  `github_release` source gains asset-name pattern matching that
  accepts an `{os}` placeholder.
- **Build pipeline produces three artifacts**:
  - `OblivionServerTool.exe` (Windows desktop, current)
  - `oblivion-server-tool` (Linux headless binary, PyInstaller
    onefile)
  - `ghcr.io/.../oblivion:0.14` (Docker image wrapping the Linux
    binary; multi-arch for amd64 + arm64 so people running it
    on a Raspberry Pi 5 work)
- **systemd unit file** shipped in the Linux binary's
  `share/` directory with install docs.
- **CI on GitHub Actions** running tests on both Windows + Ubuntu
  (already easy to add given existing test suite).

### What Linux unlocks for the product story

- **"Install Oblivion on your home Linux box / Raspberry Pi / VPS,
  manage from any browser"** — a clean one-liner that lands.
- **Docker compose snippets** that drop into existing self-hosting
  stacks (already running Jellyfin / Nextcloud / Pi-hole? Drop
  Oblivion in).
- **No display-required dependency** for unattended deployments —
  the Linux daemon doesn't need an X server or a logged-in user.
- **Matches the deployment shape paid managed hosts use** — makes
  the "host this yourself instead" pitch credible.

### Open question for Phase 3.5 planning

- **Discord bot dependency** (`discord.py`) is cross-platform Python,
  no issue.  But the bot only makes sense if the daemon has an
  internet connection — confirm during planning that the
  headless-on-an-internal-LAN edge case degrades cleanly.
- **Update flow on Linux** — Windows has the in-app self-update
  badge.  Linux probably ships through package managers (apt repo?
  AUR?  just GitHub release tarballs?) or Docker tag bumps.  Don't
  over-engineer; "you'll see a `[oblivion] new release v0.15
  available — check GitHub`" log line is enough for v0.14.

---

## Phase 4 — v0.15 first non-Source game (3-4 weeks)

**Goal:** stress-test the abstraction with a fundamentally different game.

Candidates, in order of fit:
- **Palworld** — REST admin API, growing audience, decent docs
- **Valheim** — BepInEx mod ecosystem, active community
- **Project Zomboid** — small but committed audience

**Not Minecraft** for v0.14.  Minecraft's mod ecosystem
(Spigot/Paper/Forge/Fabric + CurseForge/Modrinth) is its own product;
doing it right needs more cycles than we have here.  Revisit
post-1.0.

Decision (which non-Source game) → defer to Phase 1.

---

## Phase 5 — v1.0 public launch (1-2 weeks)

**Now with Linux + Docker + Windows distributions at launch.**

**Goal:** flip to public + monetization-ready posture.

- **Add BSL 1.1 LICENSE** — Business Source License, non-compete
  clause, reverts to Apache 2.0 after 4 years.  Source visible,
  competing commercial forks restricted.
- **Scrub git history** (`git filter-repo`) of any embedded secrets
  before going public; verify with a thorough scan.
- **Flip repo public** on GitHub.
- **`PLUGINS.md`** — license-audit table crediting every upstream
  plugin author with name + license + homepage.  Trust posture, plus
  defense-in-depth.
- **Enable GitHub Sponsors** + Ko-fi backup; sponsors badge in
  README; ❤ Support link in SPA footer.
- **Affiliate links** in docs where naturally helpful (hosting
  recommendations etc.).
- **Distribution**: GitHub Releases primary; optional
  `oblivionservertool.com` landing page — donations only at v1.0,
  no payment processor.
- **NOT Steam.**
- **2-3 games supported** at launch (CS2 + TF2 + one non-Source).
- **3 distribution artifacts at launch**: Windows `.exe`, Linux
  headless binary, Docker image (multi-arch amd64 + arm64).
- **Plugin Manager is the lead in marketing copy.**  Landing page,
  README, and screenshots foreground the install-a-plugin-in-3-clicks
  experience.  Veto / Discord / SPA shell are secondary value props.
- **Curated plugin packs ready at launch** — at minimum one canonical
  pack per supported game.  These are the operator's "I have no idea
  what I'm doing, just give me something good" button.
- **Public plugin catalog repo live** (`OblivionPluginRegistry`) with
  contribution docs so plugin authors can add themselves on day one.

---

## License model: Business Source License (BSL) 1.1

**Why BSL not closed source:**
- Python `.exe` decompilation is trivial (pyinstxtractor +
  uncompyle6) — closed source via PyInstaller buys ~1 hour of
  inconvenience against any motivated party.  Real protection comes
  from the **license**, not source visibility.
- Closed source costs **trust** — an admin tool handling PINs + RCON
  passwords needs to be auditable for the open-source security crowd.
- Closed source costs **community contributions** (PRs, bug reports,
  translations).
- BSL preserves visibility while restricting commercial competing
  forks — same protection, none of the downsides.

**Why BSL not pure open source (MIT/Apache):**
- Permissive licenses let someone fork, rebrand, and sell.  BSL's
  non-compete clause prevents this for the protection window
  (typically 4 years).

**Precedents:** MariaDB, CockroachDB, Sentry — all built real
businesses on BSL.

---

## Monetization model

**Tier 1 only at v1.0** — donations + affiliate.

| Phase | Model | Realistic ZAR/month |
|---|---|---|
| Now → v1.0 | Nothing | R0 |
| v1.0 launch | GitHub Sponsors + Ko-fi + affiliate | R200–1,500 by Year 1 end |
| Year 2 IF audience emerges | Revisit Pro tier ($4/mo USD, ~R72) | R3–10k |
| Year 3+ IF growth continues | Pro tier + consultancy gigs | R20–80k |

**Why Tier 1 only initially:**
- Product is a hobby with light monetization goal — Tier 2+ turns
  hobby into small business
- No product-market fit data yet (you have ~1 user + friends)
- Year 1 effective hourly under Tier 2+ is dire (~R20–30/hr); Tier 1
  costs nearly nothing
- Tier 1 → Tier 2 is a smooth upgrade path if the audience emerges;
  Tier 2 → Tier 1 (refunds, etc.) is much harder

**Pro tier (Year 2+ IF revisited)** would gate NEW features only,
never retroactively paywall what v0.11.1 / v1.0 users already had.
These features all serve the **Pro audience** (see "Two-audience
strategy" above) — features the average Joe doesn't need but the
tournament organizer / community admin / semi-commercial host
genuinely will:
- **Multi-server management** (deploy a pack to N servers at once;
  promotes Oblivion from a tool to infrastructure)
- **Scheduled tasks** (cron-style auto-restart, nightly map updates,
  weekly maintenance windows)
- **Audit log + permission groups** (who-changed-what; granular
  per-admin scopes — extends v0.10.x guest PIN concept)
- **Match history analytics dashboard**
- **Cloud-synced config** (login from any machine, settings travel)
- **Curated premium plugin bundle** (vetted, supported,
  breaking-change tested)
- **Health monitoring + alerting** (webhook on RCON failure, email
  on uptime drop)
- **API / webhook integration** (programmatic control for
  automation pipelines)
- **Backup / restore automation** (scheduled config + data backup)
- **Templates** ("every new server gets the Tuesday League base
  config")
- **Priority Discord support queue**

The pattern: **average Joe stays free forever**, pro pays for the
features that turn the tool into infrastructure.  Notion / Linear
playbook.

**Pricing if Tier 2 happens:** $4–5/mo USD, or $40/year, or $25 lifetime.
Mixed models common.

**Currency arbitrage:** USD revenue + ZAR cost-base is structurally
favourable.  $4/mo per Pro sub = ~R72/mo; 100 subs = R7,200/mo gross.

---

## Distribution

| Channel | Used? | Why |
|---|---|---|
| **GitHub Releases** | ✅ primary | Free, fast, hobby-friendly; v0.11.1 already there |
| **Own website** (`oblivionservertool.com`) | ✅ v1.0+ | Landing page + donate link; no payment processor at v1.0 |
| **itch.io** | ⚪ optional v1.0+ | Friendly to indie utilities; secondary channel |
| **Steam Software** | ❌ never (for this product) | $100 entry, 30% cut, Valve-policy risk, decompilation defeats DRM; CS2 admin tools = max-radar to Valve |
| **Pterodactyl / AMP marketplaces** | ❌ wrong audience | Those are commercial sysadmin tools |

---

## Plugin bundling principle

**Oblivion is an orchestration layer, not a re-distributor.**

- Operator chooses Oblivion → Oblivion fetches plugins from upstream
  on demand → plugins land in `csgo/addons/` → server starts.
- Eliminates the plugin license audit landmine for commercial
  distribution (you're not redistributing — you're managing).
- Plugin authors stay in control: upstream releases reach users
  same-day, not "wait for Oblivion update."
- Slimmer `.exe` (currently chunky bundle → ~15 MB orchestrator).
- Trust posture: "tool that helps install verified upstream plugins"
  beats "tool that includes 50 MB of unknown DLLs."
- Multi-game future-proof: Minecraft / Palworld / Valheim mod
  ecosystems don't bundle either.

**What stays bundled** (your own original work):
- `ModelPrecacher` CSS plugin
- Warcraft patches (or shipped as composed overlay; see Phase 2
  decision)
- Generated configs (`K4-Arenas/2v2.json`, `retakes.cfg`
  bot-rewrite, ZE workshop-command-filter flag)
- Glue + setup wizards + SPA + Discord bot — these ARE Oblivion

---

## Open decisions (need input before Phase 2)

1. **Phase 0 hotfix path**: push `fadfef0` as-is, or bump v0.11.2
   tag first.  *Recommendation: v0.11.2 tag — 30 sec, clean release
   for `pull-latest.bat` users.*
2. **Phase 1 features**: session persistence + pre-flight panel
   — yes / no / wait-for-feedback?  *Recommendation: wait-for-feedback,
   don't pre-commit.*
3. **Phase 2 Warcraft-patches distribution**: upstream PR / separate
   overlay repo / bundled derivative.  *Recommendation: separate
   `OblivionWarcraftPatches` repo + composed overlay in registry.*
4. **Phase 4 first non-Source game**: Palworld / Valheim / Project
   Zomboid / other.  *Defer to Phase 1.*

---

## What's explicitly NOT in the plan

- Closed-source distribution
- Steam as a distribution channel
- Hosted SaaS / managed game hosting
- Tournament management features (not the same audience)
- In-app chat (Discord exists; v0.11.0 wires it)
- Magic-link auth (no email infra)
- Public REST/webhook API (build when an external consumer asks)
- Minecraft support before v1.0
- The cinematic finale animation rewrite (parked at operator request)

---

## Reminder: the bottleneck is audience, not features

> "The product isn't the bottleneck — the audience is. You can code
> another five releases this month; getting 100 strangers to install
> something is the hard part."

Plan accordingly.  Every Phase 2+ feature should be measured against:
"does this matter to someone other than me?"  And every monetization
decision should wait until there are 50+ active strangers using the
tool.

Until then: ship, validate, iterate.

## Engineering discipline

The two-audience strategy implies a hard engineering rule:

**Consumer-grade UX, pro-grade reliability.**

Every feature must clear both bars before it's considered done:

1. **Can the average Joe figure it out without docs?**  No jargon,
   plain language, sensible defaults, forgiving error recovery.
2. **Does it behave deterministically under the load a pro would
   put on it?**  Atomic operations, rollback on failure, audit
   trail, no silent failures, no race conditions.

Failing either bar = the feature isn't done.  This is the
non-negotiable quality floor.
