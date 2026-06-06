# FRIDAY_SMOKE

> **Pre-tournament smoke checklist.**  Run top-to-bottom on the operator
> machine 30–60 min before kickoff.  Tick boxes physically (pen on
> printout) or mentally (scroll-through).  Every ❌ has a "what to do"
> right next to it — no diagnostic re-research mid-prep.
>
> **Goal:** by the bottom of this list, you have a fresh diagnostic
> snapshot that's all-green, the server is warm, the bot is in the right
> guild, the captains' links work, and Gaming Mode is on.  No surprises
> when the first captain joins voice.
>
> Estimated wall-clock: **15–25 minutes** if everything's nominal.

---

## Phase 1 — Install + version pin (2 min)

- [ ] **Close any running Oblivion window.**  Task Manager → confirm no
  `OblivionServerTool.exe` running.  *Why:* the file lock on the .exe
  would block the upgrade.
- [ ] **Replace the .exe.**  Move/install the fresh `dist\OblivionServerTool.exe`
  to your usual location, OR run the new installer.
- [ ] **Launch the new build.**  Confirm the version pill in the bottom-right
  status bar reads exactly **v0.11.26** (or whatever the current
  release is per `cs2servergui/config.py` `APP_VERSION`).
  - ❌ Reads anything else → you didn't replace the .exe.  Close + retry.
- [ ] **`/api/ping` sanity check** (optional belt-and-braces).  Open browser to
  `http://localhost:5050/api/ping` → expect `{"ok":true,"version":"0.11.26","build":"frozen"}`.
- [ ] **Hard-refresh the SPA.**  Ctrl+Shift+R inside the Oblivion window.
  *Why:* WebView2's HTTP cache CAN persist `app.js` across .exe upgrades
  on some Windows builds.  v0.11.24's `no-store` headers on `/static/*`
  prevent this for fresh installs but a pre-existing cache entry from a
  prior run still wins.  Hard-refresh forces a clean fetch.
  - 🔎 Confirm version pill STILL reads v0.11.26 after the refresh.
    If the pill changed (e.g. "0.11.23" → "0.11.26") the cache WAS stale.
    Tournament-night 2026-06-05 lost ~10 min on this exact symptom.

---

## Phase 2 — Diagnostic snapshot baseline (3 min)

- [ ] **Take a fresh snapshot.**  Config tab → Troubleshooting → 🔧 Copy
  diagnostic snapshot.  Paste somewhere readable.
- [ ] **TL;DR all ✓ (5 ticks)**:
  - `✓ app       running v0.11.26, frozen`
  - `✓ server    running on … (Mode), Nh Nm, … players`  *(or stop signal if you haven't started yet — that's fine, see Phase 4)*
  - `✓ veto      state=idle` *(or whatever you've staged)*
  - `✓ discord   connected as oblivion#8731`
  - `✓ disk      X.X GB free at config dir`
  - `✓ cs2_log   current session (Ns ago)` *(only after server started)*
  - `· recent    no error markers in recent app log`
- [ ] **No `>` lines in the recent app log.**  Any `>` is the auto-anomaly
  flag worth eyeballing — usually benign CS2 dedicated lightmap noise,
  but read each one to confirm.
- [ ] **Discord block shows the right guild + channels**:
  - `guild_id: <target tournament guild>`
  - `veto_channel_id: <text channel for live embed>`
  - `voice_channel_id: <pre-match lobby VC>` *(v0.11.15+)*
  - `voice_channel_name: #<your VC name>`
  - `voice_channel_count: 0 connected` *(no one in yet; expected)*
  - ❌ Any of these blank or wrong → Config → Discord Bot card, fix +
    Save Discord Settings, retake snapshot.

---

## Phase 3 — Discord bot end-to-end (3 min)

- [ ] **Bot is in the right guild.**  Open the target Discord; bot member
  list dot is **green**.
- [ ] **Test embed lands in the configured veto text channel.**  Config →
  Discord card → 📤 Send test embed.  Embed appears within ~2s.
  - ❌ Toast says "channel cannot post" → bot needs Send Messages +
    Embed Links on that channel.  Re-invite with full perms OR add
    role override.
- [ ] **Test DM reaches an operator account.**  Config → 📨 Send test DM →
  paste your own Discord ID.  DM arrives.
  - ❌ "Cannot send messages to this user" → captain has DMs disabled.
    Plan to use the SPA's Copy-for-Discord button as fallback.
- [ ] **🎤 Pull from voice channel — one-click flow.**  Veto tab → New
  Session (BO3) → Roster stage → 🎤 button.  Should pull DIRECTLY from
  configured VC (no picker modal).
  - ❌ Picker modal opens → either no default VC configured, or it's
    unreachable.  See Phase 2 snapshot Discord block.
- [ ] **🔀 Pick channel… button opens picker.**  (v0.11.16 mobile-safe path.)
  Click the secondary button → picker modal shows guild's VCs.  Close
  without picking.
- [ ] Reset the test session (Veto → Reset).

---

## Phase 4 — Server lifecycle + RCON (4 min)

- [ ] **Start the server.**  Pick your tournament map + mode, click Start.
  Expect within ~10 s:
  - Top-right pill goes green "Running"
  - Log shows `Server ready — RCON is responding`
- [ ] **No workshop download in flight.**  v0.11.17 A6 will refuse Start
  with 409 if one is.  Confirm Downloads tab shows nothing active before
  Phase 4.
- [ ] **RCON sanity command.**  In the log, look for the `status` RCON poll
  succeeding.  Should be auto-running every ~15 s.
- [ ] **Server visible on Steam.**  Snapshot's `public_ip` matches what the
  console.log reports for `udp/ip: ... (public 1.2.3.4:27015)`.
- [ ] **Map is correct.**  CS2 console.log tail in the snapshot shows
  `Spawn Server: <your map>`.
- [ ] **GC + VAC active.**  Snapshot's CS2 log section shows
  `GC Connection established` and `VAC secure mode is activated`.
- [ ] **Disk free** ≥ 5 GB at server_dir and config_dir.

---

## Phase 5 — Gaming Mode (host-and-play perf) (1 min)

- [ ] **Run `scripts\gaming-mode-status.bat`.**  Verify:
  - Power Plan: High Performance OR Ultimate Performance
  - Game Mode: Off
  - Game DVR: Off
  - cs2.exe -dedicated pinned to cores 0–3
  - cs2.exe (client, if running) pinned to cores 4–N
- [ ] ❌ If any line is wrong → run `scripts\gaming-mode-on.bat`,
  re-check status.

---

## Phase 6 — Veto end-to-end dry run (5 min)

> Walk a complete veto with yourself + a teammate using two browsers.
> 60% of issues that bite on a real session surface here in 5 minutes.

- [ ] **Create session.**  Veto → New Session → BO3 (or your format).
- [ ] **Roster (deliberate A1 test).**  Paste 10 fake players; deliberately
  use the same SteamID twice.  Save Roster → expect:
  - **Error toast**: "Duplicate SteamID(s) in roster: 76561… Each
    SteamID may appear at most once."
  - ✅ Behaviour confirmed.  Fix the duplicate, Save again, expect OK.
- [ ] **Distribute teams.**  Click Distribute → 5-5 split shown.
- [ ] **Generate captain links.**  /api/veto/tokens (Tokens & Links stage).
  Captain A token + Captain B token displayed.
- [ ] **Captain claim from a second browser** (or phone via tunnel).
  Click the captain A link → claims successfully → see captain-only UI.
- [ ] **Captain Ready toggle (B5 regression test).**  Click Ready → SSE
  updates show ✓ READY.  Click again → un-readies cleanly.  *(Pre-B5
  this could fail silently.)*
- [ ] **Drive through the ban/pick sequence.**  Alternate between captain A
  and captain B browser, click maps.
  - ✅ Selected card briefly shows **pulsing accent border (.pending)**;
    others lock out for the round-trip duration.  *(v0.11.17 B4)*
- [ ] **Live veto embed updates in Discord.**  As ban/pick happens, the
  text channel embed edits in-place (no duplicate posts).
- [ ] **Finale state reached.**  Final 3 maps shown.  Cinematic finale
  animation plays once.
- [ ] **MatchZy handoff.**  Click Finale (or both captains ready up if
  auto-launch enabled):
  - `matchzy_result.loaded: True`
  - Server console.log shows `matchzy_loadmatch` accepted
  - Session state advances to `complete`
- [ ] **Reset the test session.**  Veto → Reset.  State goes to `idle`,
  active-session file cleared.

---

## Phase 7 — Mobile captain end-to-end (5 min, REQUIRED)

> Tournament-night 2026-06-05 learned this lesson the hard way: the FIRST
> time any captain tried the link from inside Discord mobile was at
> kickoff with 10 people waiting.  Run this now, with yourself in both
> roles, on a real phone via the real tunnel.

- [ ] **Public URL is set.**  Veto tab → "Public URL override" field is
  populated with the current tunnel URL (`https://<sub>.trycloudflare.com`
  or your reverse proxy).  Snapshot's `public_share_url` should match.
- [ ] **Create a throwaway session.**  Veto → New Session (BO1) → name
  "smoke test" → fake roster → distribute → resolve captains (vote
  anyone) → Generate captain links.
- [ ] **DM yourself the captain A link** via the bot.  Use 📨 Send test DM
  with your own Discord ID, OR just paste the URL into a DM to yourself
  from a teammate's account.
- [ ] **CRITICAL: open the link from INSIDE Discord mobile.**  NOT in your
  phone's Safari/Chrome — Discord's in-app browser (WKWebView on iOS,
  WebView on Android) is the failure mode v0.11.20-26 fixed.
  - ✅ Expected: lands at `/#veto`, shows captain view, no PIN prompt,
    veto board visible if both captains claimed.
  - ❌ Lands at PIN screen → cookie was dropped.  Re-check on the
    operator: snapshot's `public_share_url` is HTTPS (not HTTP), and the
    .exe is v0.11.26.  Without HTTPS the `Secure` cookie flag prevents
    the cookie from setting on mobile webviews.
- [ ] **Confirm token is consumed.**  Take a snapshot; veto block shows
  `tokens_a_used: True`.
- [ ] **Click a map ban.**  ✅ The card pulses (`.pending`), then turns
  RED with `BAN` stamp within ~500 ms.  No tab refresh needed.
  - ❌ Card pulses but no BAN stamp → you're on a build before v0.11.23.
    Re-check version pill.  This is the "stuck UI" symptom.
- [ ] **Watch the Discord embed.**  Live veto embed in the configured text
  channel edits in-place with each ban.  No duplicate embeds posted.
- [ ] **Reset the test session.**  Veto → Reset.
  - ✅ Captain's phone reload (within 3s due to polling fallback) bounces
    to PIN screen — proves v0.11.21's captain-session-sweep + v0.11.26's
    race fix are doing their job.
- [ ] **Mobile UX polish (cosmetic check).**
  - Roster modal action row **wraps** (doesn't overflow).  v0.11.17 A4.
  - 🔀 Pick channel button is visible and tappable (if default VC set).
  - Captain finale Ready button is large + tappable.

---

## Phase 8 — Final snapshot before kickoff (1 min)

- [ ] **One last diagnostic snapshot** with the server warm + bot
  connected + Gaming Mode on + veto reset to idle.
- [ ] All TL;DR ticks green, no `>` anomalies, no `(could not check)` strings.
- [ ] **Save the snapshot somewhere accessible** (Discord channel
  dedicated to the session, notes app, etc.) — it's your baseline
  for any mid-session triage.

---

## During the session — quick triage cheats

If something feels off during the real session, paste a fresh snapshot
and look at:

| Symptom | First snapshot field to check |
|---|---|
| Captain says "link doesn't work" | Discord `voice_channel_count` (are they actually in voice?) + active veto `tokens` block |
| Embed not updating | Discord `bot.connected` — if False, save Discord Settings to retry (v0.11.17 A7) |
| `matchzy_loadmatch` failed | `current_mode` matches MatchZy modes?  `running: True`?  RCON `last 200 lines` for actual error |
| Server stutter / lag spikes | Gaming Mode status — if Off, run `scripts\gaming-mode-on.bat` mid-session, no restart needed |
| Captain re-clicks link, "no session" | Token was already consumed.  Re-issue from operator: Veto → Tokens & Links → 🔄 Re-issue captain token.  v0.11.20-26 covers cookie set in Discord webview, but tokens are still ONE-SHOT — a second click of the same URL fails by design |
| Captain sees stale UI (clicked but no change) | Polling fallback should self-heal within 3s (v0.11.25).  If still stuck → operator hits Reset, captain re-claims with fresh token |
| Operator: "I rebuilt but version pill is old" | WebView2 served cached `app.js`.  Hard-refresh Ctrl+Shift+R inside Oblivion window.  If pill stays old → delete `%LOCALAPPDATA%\Oblivion Server Tool\EBWebView` and relaunch.  v0.11.24 prevents this for fresh installs |
| Duplicate live embed in channel | v0.11.17 B1 should prevent — if it happens, delete the older + paste a fresh snapshot for post-mortem |

---

## Hard limits — STOP and reset if you see

- **Server doesn't reach `ready` in 60 s.**  Stop, check Steam status,
  check `last_start_error` in `/api/state`, restart.
- **Bot `connected: True` flaps False repeatedly** in three consecutive
  snapshots.  Token may have been rotated by accident.
- **Disk free < 1 GB anywhere.**  Workshop downloads will fail
  silently; clear `Downloads` + retry.
- **`server_dir` mismatch** between snapshot and where you expect CS2
  installed.  Fix in Config → Server Directory before doing anything else.

---

*This checklist will evolve every session.  Add a "post-mortem" section
after Friday with what surprised you, then promote those into Phase 6
for next time.*
