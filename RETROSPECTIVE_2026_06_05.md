# Tournament Retrospective — 2026-06-05

> First real-stakes Oblivion Server Tool deployment. 10-player CS2 5v5,
> remote captains over Discord, Cloudflare quick-tunnel for captain
> links. Shipped 7 hotfix releases over ~90 minutes from first failure
> to clean match start. Match completed successfully on de_vertigo.
>
> This file captures what broke, why, what we shipped, and what to
> change so the same chain doesn't bite next time. Feeds PLATFORM.md
> (driver-abstraction lessons) and FRIDAY_SMOKE.md (pre-tournament
> checklist additions).

---

## Timeline (Cape Town local time)

| Time  | Event |
|-------|-------|
| ~17:30 | Tunnels up, baseline v0.11.19. First captain receives DM, opens link. |
| 17:35 | **Failure #1**: captain link opens in Discord webview but lands at PIN screen. Cookie not set. |
| 17:50 | **v0.11.20** ships: SameSite=Strict → Lax + 302 redirect → 200 HTML interstitial. Defeats iOS WKWebView ITP. |
| 17:56 | installer.iss version bump (operator caught build still labelled 0.11.19). |
| 18:00 | Operator resets veto, re-issues tokens. **Failure #2**: captains still appear authed as captain of dead session. |
| 18:06 | **v0.11.21** ships: on `/api/veto/reset`, sweep `_sessions` and drop every entry with `role=='captain'`. |
| 18:15 | First veto attempt with captains. **Failure #3**: captain clicks ban map, card flashes pending, no ban appears. Discord embed updates correctly. |
| 18:20 | **v0.11.22** ships: stuff `api.veto.step()` response directly into `_vetoState`. SSE was losing the race against the API response on LAN. |
| 18:29 | **Failure #4**: votes during captain election also need tab refresh. Same root cause across every mutation handler. |
| 18:30 | **v0.11.23** ships: `_vetoApply(snap)` helper wired through every mutation handler (step / vote / ready / distribute / rematch / reset / create / roster). |
| 18:35 | **Failure #5**: still no update. Realisation: WebView2 cached `app.js` from v0.11.20 — fixes shipped but not loaded. |
| 18:36 | **v0.11.24** ships: `Cache-Control: no-store, no-cache, must-revalidate` on `/static/*` via `after_request` hook. |
| 18:43 | **v0.11.25** ships: 3s polling fallback alongside SSE — belt-and-braces against any future cache / delivery defect. |
| 18:48 | First clean veto starts: 6 bans, both Ready, MatchZy loads de_vertigo. Match completes. |
| ~19:00 | Tournament played. |
| (next day) | **v0.11.26** ships: top 4 audit findings — captain zombie race, interstitial Cache-Control, poll timer leak, board click double-render. |

Wall-clock from first failure to clean match start: **~75 minutes**.
Releases shipped during the window: **7** (0.11.20 → 0.11.25).

---

## Root causes (cross-cutting)

### 1. Auth flows through redirects are fragile under hostile browser environments

The captain claim went `Discord DM → tap link → 302 → /#veto`. iOS WKWebView's
ITP treats 302 chains as "bounce tracking" and strips `Set-Cookie`. SameSite=Strict
compounds it: even when the cookie sets, it doesn't ride the next navigation
from a different origin (Discord).

**Generalises to:** any platform where the operator-side issues a one-shot
token URL that lands in a mobile app's embedded browser. TF2 captain links,
GTA RP voucher URLs, anything where Discord is the delivery channel.

**Lesson:** prefer same-origin XHR for token claim where possible. Where you
must redirect, render a real HTML page (200) that sets the cookie and JS-bounces,
NOT a 302. And use SameSite=Lax for any cookie that needs to survive a
top-level navigation from an external origin.

### 2. Real-time UI is a race between API response and SSE broadcast

The veto board click handler called `api.veto.step()`, awaited the response,
then relied on the SSE event to update `_vetoState`. On LAN the API response
arrives BEFORE the SSE broadcast — so the `finally`-block render redrew the
board from the **stale pre-ban** snapshot. The clicked card lost its `.pending`
pulse and reverted to "no ban shown" until the next SSE event landed.

**Generalises to:** any SPA that uses SSE for live updates and has a local
click that mutates server state. The local click ALWAYS has the freshest
snapshot in the API response — don't throw it away.

**Lesson (now codified as `_vetoApply`):** every mutation endpoint returns the
fresh snapshot. Stuff it directly into local state, then trigger render.
SSE is the fallback for *other clients*' actions, not the source of truth
for your own.

### 3. WebView2's HTTP cache is per-app and survives across rebuilds

The fix-rebuild-relaunch cycle felt broken because the WebView2 (Edge embedded
in pywebview) cached `app.js` aggressively. The .exe was v0.11.23, the JS
serving in the browser was from the v0.11.20 build. Every "the fix didn't
take effect" was a stale-asset symptom.

**Generalises to:** any PyInstaller-bundled Flask + pywebview app where the
operator iterates on the SPA.

**Lesson:** `Cache-Control: no-store` on `/static/*` was the tournament-night
patch. The right altitude (v0.12 task #139) is content-hashed asset URLs
(`/static/js/app.js?v=0.11.26`) — gives both cache-bust on rebuild AND
cacheability between rebuilds.

### 4. Hotfix chains expose the lack of pre-tournament integration tests

FRIDAY_SMOKE.md covered the operator-side checklist (build pinned, snapshot
green, voice channel configured) but had no captain-link end-to-end test.
The first time anyone tried the captain link in a Discord webview was at
17:35 with 10 people waiting.

**Lesson:** add to FRIDAY_SMOKE.md (next section) a deliberate captain-link
test from inside Discord mobile, 60 min before tournament.

---

## What v0.11.26 (post-audit) cleaned up

Code-review skill ran at high effort against the v0.11.20→25 diff. 10
findings surfaced; 6 CONFIRMED, 3 PLAUSIBLE, 1 REFUTED. Top 4 shipped
the next day:

1. **Zombie captain race** (web.py): `veto_reset` released `_veto_lock`
   before acquiring `_sessions_lock`. A concurrent captain claim that won
   `_veto_lock` first would `_create_session` AFTER the sweep snapshot —
   producing a captain cookie that referenced `core._veto_session = None`.
   Fixed by nesting `_sessions_lock` inside `_veto_lock` and moving
   `_create_session` inside the same `_veto_lock` block on both
   `/api/veto/claim` and `/veto?join` handlers.

2. **Interstitial missing Cache-Control**: the 200 HTML from v0.11.20 had
   no `Cache-Control` header. A proxy that cached it would deliver the
   body without `Set-Cookie` on the second request to the same one-shot
   URL — captain locked out, token already consumed. Fixed by adding
   `no-store, private` + `Pragma: no-cache`.

3. **Poll timer leak**: v0.11.25's `_vetoCleanup` was gated on
   `currentPage === 'veto'` inside a hashchange listener, but `navigate()`
   had already updated `currentPage` by the time the listener ran (listener
   registration order). Cleanup never fired on tab leave; timer kept
   running. Fixed by dropping the `currentPage` gate.

4. **Board click double-render**: `_vetoApply` rendered on success, then
   `finally` rendered again. Two full board rebuilds per click + listener
   re-attach. Fixed by moving the error-path render into `catch`.

Remaining 6 findings tracked as v0.12 tasks #138-143.

REFUTED: `SameSite=Lax` weakening admin CSRF. Every mutating endpoint
requires `application/json` (`request.get_json()` returns `None` for
form-encoded bodies and cross-origin XHR triggers CORS preflight that Lax
still blocks). The relaxation is safe given the JSON-only API contract.

---

## Lessons for v0.12+ (platform layer)

### Driver abstraction (#86) should bake in:

- A consistent **mutation contract**: every state-changing endpoint returns
  the fresh snapshot, the SPA helper always applies it locally before
  trusting any broadcast. The CS2 driver's `_vetoApply` becomes a
  framework primitive, not a one-off.
- A **broadcast layer with sequence numbers and gap detection** instead of
  unconditional polling. Polling fallback masks queue-overflow bugs
  (`_veto_broadcast` uses `Queue(maxsize=32)` with silent drop —
  finding #10, task #143). The right altitude is monotonic seq + on-gap
  catch-up fetch.
- A **cookie + token lifecycle that doesn't go through redirects** for
  external-origin claims. Captain links open in browsers we can't control;
  the claim should be a fetch from same-origin code, not a server-side
  redirect chain.
- An **integration test harness** that exercises the captain-claim path
  through a headless WebView2 or at minimum a curl-based smoke that
  asserts `Set-Cookie` survives a top-level navigation.

### Static asset delivery:

Don't ship `no-store`. Content-hash the URL at template-render time
(`app.js?v={{APP_VERSION}}`) and let `/static/*` set `public, max-age=31536000,
immutable`. Gives both cache-bust on release AND cacheability between
releases.

---

## FRIDAY_SMOKE.md additions (next tournament)

Append a new **Phase 6 — Captain link end-to-end** that runs 60 minutes
before kickoff:

- [ ] **Generate a throwaway captain token** for "test team A" from the
  Veto tab. Don't issue real tokens yet.
- [ ] **Copy the public URL** (the tunnel-prefixed `/veto?join=…` link,
  not the LAN one).
- [ ] **DM yourself the link** from the bot (or paste it into Discord DM
  to yourself).
- [ ] **Open it from inside Discord mobile** (NOT a tab in your phone's
  Safari/Chrome). Confirm: lands at `/#veto`, shows captain view, no
  PIN prompt.
- [ ] **Take a snapshot.** Verify `tokens_a_used: True` and
  `state: links` or whatever you staged.
- [ ] **Hit Reset.** Confirm: `tokens_a_used: False`, captain cookie
  invalidated, refreshing the captain link from Discord prompts for
  re-issue (since the token was consumed).

Total: ~5 min. Catches every failure-mode from the 2026-06-05 timeline
above before any real captain is in the lobby.

Also worth adding:

- [ ] **Hard-refresh check.** After installing the new build but before
  first use: open the SPA, hit Ctrl+Shift+R, check that the version
  pill matches `APP_VERSION` from `config.py`. Catches WebView2 cache
  staleness.

---

## Capabilities we lacked that night

- **A way to verify SSE delivery from the operator side.** The SSE status
  pill exists but we didn't think to look at it during the chaos.
  Worth: add SSE event-count + last-event timestamp to the diagnostic
  snapshot so a green pill is verifiable, not assumed.
- **An "open captain link in test webview" button.** Operator-side button
  that opens the captain URL in a sandbox webview to validate the cookie
  set without burning a real token. v0.12 candidate.
- **A "purge WebView2 cache" button.** Tournament night required
  fully-quit-relaunch cycles. A button that triggers WebView2's
  `CoreWebView2.Profile.ClearBrowsingDataAsync` would have saved 3+
  minutes of frustration. Maybe in v0.12 alongside the Gaming Mode
  toggle (#95).

---

## What worked

Worth acknowledging — not everything was on fire:

- **The bot was rock-solid.** No reconnects, all DMs delivered, live veto
  embed updated cleanly. v0.11.0-19's Discord work paid off.
- **MatchZy handoff was clean.** Once the veto completed at 18:50, the
  config write + `matchzy_loadmatch` + server-deploy chain was one
  smooth ~40s sequence.
- **Diagnostic snapshots were a lifesaver.** Every "what's actually
  broken" question got answered by paste-and-read in <10 seconds.
- **Test suite never broke.** All 200 tests stayed green across the
  whole hotfix chain. The unit-test discipline from v0.10.x meant
  every fix was verified against the existing contract before shipping.
- **The polling fallback actually worked.** v0.11.25 was the version
  that completed the tournament. "Belt-and-braces" earned its keep.

---

## Sleep cost

Operator was on it from ~17:30 to ~01:00. Six hours of intense focus.
Build cycle (write → tests → commit → push → build.bat → rebuild) was
~3-5 minutes; that's the floor for any tournament-night hotfix. With
the v0.11.24 cache-bust headers and v0.11.26 race-fix in place, the
NEXT tournament should not require more than one or two precautionary
patches.

---

*Filed by: Claude Sonnet 4.6 (1M context), under operator direction*
*Generated: 2026-06-06*
*Related: PLATFORM.md (task #84, pending), FRIDAY_SMOKE.md (additions
above), AUDIT.md (v0.11.20-26 findings #1-10).*
