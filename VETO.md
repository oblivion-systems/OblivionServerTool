# VETO — Map Veto / Match Setup

> Quick-reference spec for the in-app **Veto** feature.
> Status: **v0.11.1 RELEASED 2026-06-02.**
>
> Release history:
> - **v0.10.0** — core veto state machine + MatchZy handoff (atomic match-config
>   write + `matchzy_loadmatch` RCON).
> - **v0.10.0.1** — hotfix for QR bundling (`--collect-all segno` + multi-Python
>   `python -m PyInstaller`).
> - **v0.10.1** — Captain Ready button (replaces the broken admin-only button
>   captains couldn't use), Public Share URL override (Cloudflare tunnel base
>   for captain links), Copy-for-Discord (pre-addressed paste-ready DM),
>   `pull-latest.bat` self-service updater.
> - **v0.10.2** — online-primary audit closure: mobile-responsive SPA, captain
>   finale embeds the `connect <ip>` command + Copy buttons, mode pre-flight
>   on `/api/veto/finale`, role pill in header, unified `_oblivionSSE` transport
>   with re-arm, captain limbo screen, **rematch button** (preserves teams),
>   **last 10 matches persisted** to `oblivion_matches.json`, **Discord webhook**
>   on finale.
> - **v0.11.0** — optional Discord bot (Layer 1): auto-DM captain links,
>   voice-channel roster pull, live veto embed (see [DISCORD.md](DISCORD.md)).
> - **v0.11.1** — polish sweep: **📜 match history modal**, **"Go Online" banner**,
>   **bulk paste** (Name/SteamID/DiscordID columns), **roster presets**
>   (localStorage), **MatchZy cvar editor** (local-only), **📺 spectator URL**
>   (read-only `/spectate` page, token-gated, PII-stripped),
>   **Discord test buttons** (Test Embed / Test DM), real-device
>   [MOBILE_CHECK.md](MOBILE_CHECK.md) checklist.
>
> Implementation lives in [`cs2servergui/veto.py`](cs2servergui/veto.py) (state
> machine, now ~800 lines), the `/api/veto/*` routes in
> [`cs2servergui/web.py`](cs2servergui/web.py), and the Veto tab in
> [`cs2servergui/static/js/app.js`](cs2servergui/static/js/app.js) +
> [`cs2servergui/static/css/app.css`](cs2servergui/static/css/app.css).
> The original browser-only prototype is preserved at
> [`_prototypes/veto.html`](_prototypes/veto.html) for reference.  See also:
> [CHANGELOG.md](CHANGELOG.md), [ROADMAP.md](ROADMAP.md), [TODO.md](TODO.md),
> [INGEST.md](INGEST.md) → "API — map veto" + "Frontend — Veto tab".

---

## What it is

A guided **match-setup flow** that ends in a CS2 map veto. Five stages:

1. **Roster** — enter 10 players, name both teams.
2. **Teams** — random split into two teams of 5 (re-shuffle option).
3. **Captain vote** — each team votes 5×; most votes = captain; ties auto-revote.
4. **Captain links** — generate a one-time link per captain.
5. **Veto** — captains alternate BAN/PICK over the map pool (BO1 / BO3 / BO5), ending
   on a decider, then a match-lineup result panel.

The prototype is already in the **Oblivion theme** (same oklch tokens, clip-paths, fonts,
animations: ban stamp, decider reveal, confetti, turn badges).

---

## How it should integrate (the plan)

Decided direction: the tool **hosts the veto form** (captains veto from their **own
devices** via links), and the tool's own UI is a **live mirror** of that session. On
completion the tool plays a **"Get Ready to Battle"** finale, then **auto-launches the
first map and queues the rest** per the BO format. Core pieces:

- **Server-side session state** in `AppCore` — one active veto session (roster, teams,
  votes, captains, tokens, mode, veto steps/results). The browser stops being the source
  of truth.
- **API endpoints** in `web.py` for each transition (roster, distribute, vote, generate
  links, captain-join, ban/pick). The prototype's `localStorage` logic becomes thin client
  calls.
- **Live sync over SSE** — reuse the existing log-stream pattern to push veto state changes
  ("A banned Mirage → B's turn") to both captains' screens in real time.
- **Captain links** — `http://<host>:<flask_port>/veto?join=<token>`: a **scoped,
  single-use, no-PIN** credential that only unlocks that team's turns. Enforced server-side.
- **Frontend** — port the prototype into the SPA, reusing the bundled official map
  thumbnails instead of `assets/<map>.png`.
- **Finale / server handoff** — when the veto completes the tool shows a cinematic
  **"Get Ready to Battle"** moment, then **hands the lineup to MatchZy**: generate a MatchZy
  match config from the veto result (BO format + ordered map list + team names/players) and
  load it (e.g. RCON `matchzy_loadmatch` / match-config URL). MatchZy then runs the series
  natively — loads map 1, knife round, and advances to the next map on match end through the
  decider. The tool doesn't need its own "match over?" detection. (MatchZy is already bundled
  for Practice mode.)
  - To use MatchZy's **team/player assignment**, the config wants **Steam IDs per player** —
    so either collect Steam IDs at roster (or via the Discord layer mapping), or run MatchZy
    in a looser mode where players pick sides in-game. (Open sub-point.)

### Known gap in the prototype *(now resolved in the v0.10.0 live implementation)*
The captain `?join=TOKEN` gate read `state` from **localStorage** — i.e. the *host's*
browser. A captain opening the link on their own device had empty localStorage, so the
gate never matched. **Multi-device captain veto therefore required the shared server-side
state.** The live `cs2servergui/veto.py` + `_CAPTAIN_PATHS` allowlist + cookie-minting
`/api/veto/claim` flow fixes this; single-use token enforcement now lives server-side in
`claim_captain()` (idempotent for the same caller IP, rejects different caller).

---

## Link delivery

The tool generates two **scoped, single-use** tokens (one per captain); whoever opens a link
claims that captain role on their device, and the token is consumed. Delivery is the trust
boundary, so the tool just has to get each link to the right human:

- **Core (always available):** per-captain link cards with a **Copy** button (in the
  prototype already) **+ a QR code** for effortless LAN handoff (captain scans their own QR).
- **Enhancement:** Discord bot DMs the link privately (see below).
- *Not viable:* in-game delivery — CS2 has no reliable per-player private message and chat
  URLs aren't cleanly clickable.

## Discord bot (optional enhancement layer)

A bot that bolts onto the core veto without changing it — handles the two manual ends
(roster in, links out). Build the core first; Discord is purely additive and falls back to
manual when unconfigured.

**Flow:** read a voice channel → auto-fill the roster (display names + Discord user IDs) →
random teams → captain vote (tool already knows the captains' Discord IDs) → **bot DMs each
captain their veto link** privately. Captains still veto on the **web form** (the DM just
delivers the link).

**Requires:** a Discord bot application + token (one-time setup; stored in a new Discord
settings section), a persistent **gateway** connection (`discord.py` on a background thread),
the **Server Members + Voice States** intents enabled, and a **DM fallback** to the on-screen
copy/QR link (bots can't DM users who block server-member DMs).

**Out of scope (further stretch):** running the entire veto *inside* Discord (ban/pick via
bot buttons) — much larger; keep veto on the web form.

## Decisions

**All resolved (Days 1-5):**
- ✅ **Captains play from their own devices** via hosted links — the tool's UI is a **live
  mirror** of the session.  *Implemented:* server-side state in `cs2servergui/veto.py`,
  SSE pub/sub in `web.py` (`/api/veto/stream`).
- ✅ **Completion auto-launches + queues** the maps with a **"Get Ready to Battle"** finale.
  *Implemented Day 5:* cinematic finale (title rise, decider glow, confetti).  Day 6 wires
  the real `matchzy_loadmatch` handoff (currently the finale endpoint logs the config).
- ✅ **MatchZy runs the map series.**  *Implemented Day 1:* `build_matchzy_config()`
  produces the config dict (BO format, ordered maplist, team names + Steam IDs).  Day 6
  writes it to disk + RCON-loads it.
- ✅ **Mirror presentation: dedicated Veto tab** (not an overlay).  *Implemented Day 3:*
  `pages['veto']` in `app.js`, sidebar entry between Maps and Appearance.
- ✅ **Captain reachability: both LAN + Public.**  *Implemented Day 2:* `/api/veto/tokens`
  returns `{lan, public}` URLs per captain (Public only if `core.public_ip` is set —
  mirrors the Connect popover).  Day 4 adds a QR code per URL.
- ✅ **Map pool: per-veto override starting from the active-duty 7.**  *Implemented Day 1:*
  `create_session(mode, map_pool)` accepts any pool; `config.ACTIVE_DUTY_POOL` is the
  default.
- ✅ **Player Steam IDs collected at roster** (enables MatchZy strict assignment).
  *Implemented Day 1:* `RosterPlayer.steam_id` is optional; `/api/veto/roster` accepts
  `{name, steam_id}` per player.

## Layered build plan

Ship a simple, robust core first; each later layer is additive and optional.

**Layer 0 — Core veto (v0.10.0, in flight on master):**
1. ✅ Backend veto session + API endpoints — `veto.py` + 15 routes in `web.py` *(Day 1-2)*
2. ✅ Port the 5-stage UI into the SPA as a dedicated tab — `pages['veto']` + 8 stage
   renderers *(Day 3)*
3. ✅ SSE live mirror + real captain-link token gate (scoped, single-use) — `/api/veto/stream`
   + `_CAPTAIN_PATHS` allowlist + `claim_captain()` enforcement *(Day 2)*
4. ✅ Copy + **QR** link delivery — segno-backed `/api/veto/qr` returning SVG *(Day 4)*
5. ✅ Cinematic finale — title rise, decider glow, confetti *(Day 5)*
6. ✅ MatchZy match-config write + `matchzy_loadmatch` RCON handoff — `/api/veto/finale`
   atomically writes `<csgo>/cfg/MatchZy/<matchid>.json` (with `_oblivion_meta` stripped
   from the disk file but preserved in the API response for the SPA's audit trail), then
   issues `matchzy_loadmatch <basename>` via RCON.  Three-way outcome: file fails → 500;
   RCON fails → 200 + `matchzy.error` + session still completes so SPA isn't stuck;
   success → 200 + `matchzy.loaded: true`. *(Day 6)*
7. ✅ Polish + edge-case unit tests + tag — `APP_VERSION` bumped to 0.10.0; +15 unit
   tests + 6 API tests including a real bug found (finale double-call 500 → 400).
   108/108 tests green at release. *(Day 7)*

**Layer 1 — Discord bot (v0.11.0):** pull roster from a voice channel + DM the captain
links. Falls back to manual/QR when not configured. (See Discord section above.)

**Layer 2 — (stretch) full in-Discord veto:** ban/pick via bot buttons. Not planned.

## Prototype reference notes
- Map pool hardcoded: `de_mirage, de_inferno, de_ancient, de_anubis, de_nuke, de_overpass,
  de_vertigo`.
- Sequences: BO1 = 6 bans + decider; BO3 = ban/ban/pick/pick/ban/ban + decider; BO5 =
  ban/ban/pick/pick/pick/pick + decider.
- Names are run through `escapeHtml` (XSS-safe). Tokens via `crypto.getRandomValues`.
