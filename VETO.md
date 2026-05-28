# VETO — Map Veto / Match Setup (planned feature)

> Quick-reference spec for the in-app **Veto** feature, targeted for the full release.
> Status: **planned / not started.** Working prototype lives at
> [`_prototypes/veto.html`](_prototypes/veto.html) (open in a browser to see the flow).
> See also: [ROADMAP.md](ROADMAP.md), [TODO.md](TODO.md).

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

### Known gap in the prototype
The captain `?join=TOKEN` gate reads `state` from **localStorage** — i.e. the *host's*
browser. A captain opening the link on their own device has empty localStorage, so the gate
never matches. **Multi-device captain veto therefore requires the shared server-side state
above.** Single-use token enforcement also can't live in localStorage.

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

**Resolved:**
- ✅ **Captains play from their own devices** via hosted links — the tool's UI is a **live
  mirror** of the session (needs server-side state + SSE).
- ✅ **Completion auto-launches + queues** the maps with a **"Get Ready to Battle"** finale
  (no manual map loading, no "just show the lineup").

- ✅ **MatchZy runs the map series** — the veto generates a MatchZy match config and loads
  it; MatchZy handles map order, knife rounds, scoring, and map-end → next. (Sub-point still
  open: whether to collect Steam IDs for strict team/player assignment, or run loose.)

**Still open:**
1. **Mirror presentation** — a **dedicated "Veto" tab** that reflects the live session, OR
   **overlay it on the Status page** (and let that overlay become the "Get Ready to Battle"
   finale). Could also be both: tab during veto, status-overlay for the finale.
2. **Captain reachability** — LAN only (LAN IP links), over the internet (public IP +
   existing port-forward), or both (mirror the Connect popover).
3. **Map pool** — competitive active-duty (the fixed 7 the prototype hardcodes), a
   host-configurable pool (incl. workshop), or the full official list.
4. **Player Steam IDs** — collect them at roster (enables MatchZy strict team assignment +
   could feed the Discord mapping), or skip and let players pick sides in-game.

## Layered build plan

Ship a simple, robust core first; each later layer is additive and optional.

**Layer 0 — Core veto (the robust base):**
1. Backend veto session + API endpoints in `AppCore`/`web.py`.
2. Port the 5-stage UI into the SPA (tab and/or Status overlay) talking to the API.
3. SSE live mirror + real captain-link token gate (scoped, single-use).
4. Copy + **QR** link delivery.
5. "Get Ready to Battle" finale → generate a MatchZy match config from the veto result and
   load it; MatchZy runs the best-of series (map order, knife, scoring, map-end → next).

**Layer 1 — Discord bot (optional):** pull roster from a voice channel + DM the captain
links. Falls back to manual/QR when not configured. (See Discord section above.)

**Layer 2 — (stretch) full in-Discord veto:** ban/pick via bot buttons. Not planned.

## Prototype reference notes
- Map pool hardcoded: `de_mirage, de_inferno, de_ancient, de_anubis, de_nuke, de_overpass,
  de_vertigo`.
- Sequences: BO1 = 6 bans + decider; BO3 = ban/ban/pick/pick/ban/ban + decider; BO5 =
  ban/ban/pick/pick/pick/pick + decider.
- Names are run through `escapeHtml` (XSS-safe). Tokens via `crypto.getRandomValues`.
