# Mobile real-device checklist

Owner: operator (you).  Goal: validate that the captain-link experience
on a phone is actually usable before Friday's test session.

Setup:

- Cloudflare tunnel up + Oblivion Server Tool reachable at your
  public URL.
- A captain link issued from the desktop SPA (Veto → Resolve →
  copy team-A captain link, or scan its QR).
- Open the link on your phone in **Chrome on Android** and
  **Safari on iOS** — both, ideally on different devices.

Tick as you go.  If something fails, screenshot + paste path into the
session and a follow-up task will get spun off.

## Layout

- [ ] Header sidebar collapses to a hamburger button (no horizontal
      scroll).
- [ ] Tapping the hamburger reveals the drawer with the Veto tab
      reachable.
- [ ] Tapping outside the drawer closes it.
- [ ] No element overflows the viewport (rotate to landscape and
      back, check both).
- [ ] Font sizes scale via `clamp()` — no 8px micro-text or 28px
      monster headlines.
- [ ] Buttons are tap-target sized (≥ 40px tall).

## Roster + voting (captain role)

- [ ] Roster slots show name + steam_id without wrapping awkwardly.
- [ ] The discord_id column collapses or stays usable on narrow
      screens.
- [ ] Tapping a teammate to cast a vote actually registers (look
      for the highlighted ring + the "voted" pill).
- [ ] Voting closes when the timer hits 0 — no stuck loader.

## Captain veto flow

- [ ] When the captain's turn comes up, the map grid is tappable —
      no double-tap-to-zoom required.
- [ ] Pick / ban buttons fire on first tap (test 5 in a row).
- [ ] Mode banner stays readable above the map grid.
- [ ] Decider screen renders without overflow.

## Live updates (SSE)

- [ ] Lock the phone for 60s, unlock — the veto state catches up
      within 2s (visibilitychange re-arm in `_oblivionSSE`).
- [ ] Background the tab for 30s, foreground — same.
- [ ] SSE pill in the header eventually reads "live", not "offline"
      after backgrounding.

## Captain Ready / launch

- [ ] Captain Ready toggle is reachable + clearly indicates state.
- [ ] When both captains ready and auto-launch fires, the captain
      screen shows the connect string + Copy button.
- [ ] Copy button actually copies (paste into a notes app to verify
      — `navigator.clipboard` is gated on HTTPS, but the Cloudflare
      tunnel is HTTPS so it should work).

## Discord handoff

- [ ] DM from the bot arrives with the captain link (if bot
      configured).
- [ ] The link tapped in Discord opens the captain page directly
      (no PIN prompt, token claims the session).

## Reduced-motion / hover-none

- [ ] On a phone where "Reduce Motion" is on in OS settings, the
      animations soften but the flow still completes.
- [ ] No hover-only tooltips needed to operate the flow (we have a
      `@media (hover: none)` block — verify nothing is hover-gated).

## Edge cases worth a glance

- [ ] PWA-like behaviour — add-to-home-screen still works (icon,
      no broken splash).
- [ ] Pull-to-refresh doesn't kill an in-progress vote.
- [ ] Pinch-zoom is disabled where it should be (map grid) and
      allowed elsewhere.

---

If everything ticks: mark Friday's plan green.  If anything fails:
note the screen + browser + OS version in TONIGHT.md so the next
fix pass has the repro.
