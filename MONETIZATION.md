# MONETIZATION

> Strategy + implementation plan for funding Oblivion's ongoing
> development and infrastructure costs.
>
> Author philosophy: **helpful and subtle, never in-your-face.**
> Tools that beg for money lose trust.  Tools that quietly accept
> support from people who want to give earn it.

---

## TL;DR

| Phase | Model | Realistic ZAR/mo | Visibility |
|---|---|---|---|
| Now to v1.0 | Nothing | R0 | n/a |
| v1.0 launch | Donations + invisible affiliate | R200-1,500 by Year 1 end | One Sponsor button + one footer line per docs page |
| Year 2 if audience emerges | Optional Pro tier ($4/mo) | R3-10k | New Config card; existing free features stay free |
| Year 3+ if it grows | Pro tier + consultancy gigs | R20-80k | Same |

**Hobby income, not a business.**  Goal: cover the domain, the tunnel,
the testing VPS, and a coffee budget.  Pro tier is optional Year-2
work if audience materialises.

---

## The "helpful and subtle" rule

Every monetisation decision goes through this filter:

> If a stranger reading the docs for the first time can't tell
> we monetise, we're doing it right.

That means:

- **No banners.**  Not in the SPA, not at the top of READMEs.
- **No pop-ups.**  Ever.
- **No "support us" CTA on every page.**  One footer link, total.
- **No telemetry / ads / tracking.**  Period.
- **No disclosure paragraphs interrupting recommendations.**  Affiliate
  disclosure lives in ONE muted footnote per docs page, not inline
  next to every link.
- **No paywalling info behind affiliate clicks.**  Docs work fine for
  anyone who clicks nothing.

The opposite — visible, pushy, aggressive — converts WORSE because it
reads as untrustworthy.  Quiet recommendations from a friend convert;
shouting at users in their workflow does not.

---

## Layer 1 — Donations (primary visible ask, v1.0+)

**One** Sponsor button.  That's the entire visible monetisation
footprint of the project.

### Where it lives

- **README.md footer**: small `❤ Sponsor` badge linking to
  GitHub Sponsors.  No paragraph explaining why, no goals, no
  guilt-trip.  Just the badge.
- **SPA footer** (optional, v1.0+): single line `Like Oblivion? ❤
  Support development` linking to the Sponsors page.  Right-aligned,
  small font, status-bar territory.  Easy to miss if you're not
  looking for it.

### Where it does NOT live

- Anywhere inside the actual veto / Discord / server-management
  workflow
- In the diagnostic snapshot
- In TROUBLESHOOTING.md
- In the installer
- In any error toast

### Realistic income

Donations are sporadic and lumpy.  GitHub Sponsors / Ko-fi /
Buy Me A Coffee combined: maybe **R200-1,500/month** at 100-300
active users.  Higher if a single tournament organiser likes the
tool enough to back monthly.  Lower if Year-1 adoption is slow.

---

## Layer 2 — Affiliate links (invisible passive layer, v1.0+)

The principle: **the link IS the helpful thing.**  We were going to
recommend Hetzner / DigitalOcean / DeskMini parts anyway.  The
affiliate kickback is invisible to the reader and costs them
nothing.

### Where it lives

In existing documentation where we recommend hardware or hosting:

- **`HOSTING.md`** (future doc, post-v1.0): VPS recommendations
  with affiliate URLs swapped in.  Same wording, same
  recommendations.
- **`HARDWARE.md`** (future doc): DeskMini build guide, recommended
  laptop builds, etc.  Amazon Associates tags on parts links.
- **`TONIGHT.md` / Cloudflare tunnel section**: domain registrar
  recommendation (Porkbun, Namecheap) — affiliate IDs.

### What gets an affiliate URL

| Category | Programs | Per-event payout |
|---|---|---|
| Cloud VPS / hosting | Hetzner, DigitalOcean, Vultr, Linode | $25-100 per qualified signup |
| Hardware | Amazon Associates | 1-4% of purchase |
| Domain registrars | Porkbun, Namecheap, Cloudflare | $5-10 per signup |

### What does NOT get one

- **Cloudflare tunnels** — free tier, no personal affiliate program
- **Steam / Steam accounts** — no program
- **MetaMod / CounterStrikeSharp / MatchZy / community plugins** —
  open-source projects funded by their own donations, affiliate
  doesn't apply
- **Anything inside the SPA itself** — never

### Disclosure approach

Each docs page with affiliate links gets ONE footnote at the
bottom, in muted text:

```markdown
---
*Some links in this doc are referral links — they cost you nothing
and help fund Oblivion's domain + testing infrastructure.*
```

That's the entire disclosure footprint.  No per-link disclaimers,
no banner at top.  FTC-compliant (US), ethically honest
(everywhere), invisible to anyone not looking for it.

### Realistic income at 100-300 active users

| Source | Annual estimate |
|---|---|
| Hetzner affiliate signups | $100-500 (~R1.8k-9k) |
| Amazon Associates hardware referrals | $50-200 (~R900-3.6k) |
| Domain registrars | $20-100 (~R360-1.8k) |
| **Total affiliate** | **$170-800/yr (~R3-15k/yr)** |

Combined with Layer 1 donations: **~R5-25k/year at modest scale.**
Hobby income, covers infrastructure, nothing life-changing.

### Strict rule

**Never change a recommendation based on payout.**  If OVH is the
best fit for a South African user but doesn't pay, recommend OVH
anyway with a direct link.  Trust over commission, always.  The
moment a doc steers users toward inferior options for a kickback,
the entire project's reputation is at risk.

---

## Layer 3 — Pro tier subscription (Year 2+, OPTIONAL)

**Only if Year-1 adoption proves an audience exists.**  Don't
build this on speculation.

### Pricing if it happens

- $4/mo USD (~R72/mo) recurring
- OR $40/year
- OR $25 one-time lifetime

(Mixed model — let users pick.)

### What goes in Pro

Features average-Joe operators don't need but pros (tournament
organisers, community admin teams, semi-commercial hosts) will:

- Multi-server management (deploy plugin pack to N servers)
- Scheduled tasks (cron-style auto-restart, nightly map updates)
- Audit log + permission groups (extends v0.10.x guest PIN)
- Match history analytics dashboard
- Cloud-synced config (login from any machine)
- Curated premium plugin bundle (vetted, supported,
  breaking-change tested)
- Health monitoring + alerting (webhook on RCON failure)
- API / webhook integration (programmatic control)
- Backup / restore automation
- Templates ("every new server gets the Tuesday League base config")
- Priority Discord support queue

### What stays free FOREVER

**Everything in v0.11.x and v1.0.**  Including:
- Veto + MatchZy handoff
- Discord bot (Layer 1)
- Diagnostic snapshot
- Session persistence
- Captain link generation
- Spectator URL
- Roster presets
- Match history (basic)
- Plugin Manager (basic)
- Mobile-responsive SPA
- All 16 game modes
- Workshop downloads
- Multi-game support

**Average Joe stays free forever.**  Pro is purely additive — features
that turn the tool from a hobby helper into infrastructure.

### Visibility (when/if Layer 3 ships)

- One new card in Config tab: "Oblivion Pro" with a feature list
  and an Upgrade button
- Pro features are gated behind license check; non-Pro UI shows
  them disabled with "Pro feature" badge
- That's it.  No nag screens, no "30 days left" countdowns, no
  upsell modals

### Realistic income

At 100 Pro subscribers: $400/month (~R7.2k/mo).  At 300: $1,200/mo
(~R21k/mo).  Genuine side income territory, but requires real
adoption first.

---

## What we will NEVER do

Categorical no-go list, regardless of revenue impact:

| Forbidden | Why |
|---|---|
| In-app banners / pop-ups | Destroys workflow trust |
| Telemetry / analytics | Privacy + trust |
| Ads (display, sponsored content) | Wrong audience, wrong product |
| Selling user data | Should not need explanation |
| Tiered features that were once free | Trust costs years to build, seconds to lose |
| Sponsored plugin placement in the registry | Plugin Manager neutrality is sacred |
| Paid early access | Forks the community |
| Crypto / NFT integration | No |
| Auto-renewal traps / hidden cancellation | No |
| Affiliate links inside the SPA workflow | Wrong place |
| Steam Software listing | Wrong distribution channel (see PLAN.md) |
| Closed-source commercial product | Wrong license model (see PLAN.md) |

---

## Implementation checklist (v1.0+)

When v1.0 ships (post Phase 5 of PLAN.md):

- [ ] Enable GitHub Sponsors on the repo
- [ ] Add Ko-fi page as backup
- [ ] Add `❤ Sponsor` badge to README.md footer
- [ ] Add single-line `❤ Support development` link to SPA footer
- [ ] Sign up for Hetzner affiliate program
- [ ] Sign up for DigitalOcean / Vultr affiliate programs (whichever
      pays best at time of v1.0)
- [ ] Sign up for Amazon Associates (region: ZA if available, US
      otherwise)
- [ ] Sign up for Porkbun / Namecheap affiliate programs
- [ ] Write `HOSTING.md` with recommendations + affiliate URLs
- [ ] Write `HARDWARE.md` with build guides + Amazon Associates tags
- [ ] Add muted footnote to each docs page that contains affiliate
      links (one line, bottom of page)
- [ ] Do NOT add affiliate links anywhere inside the SPA
- [ ] Defer Pro tier decision until Year 2 audience data is real

---

## The trust calculation

Counterintuitively, restraint earns more:

|  | Loud monetisation | Subtle monetisation |
|---|---|---|
| 1,000 docs visits | 1,000 | 1,000 |
| Click-through rate | ~2% (perceived as ads) | ~5% (perceived as recommendations) |
| Conversion rate | ~15% | ~16% |
| Revenue per 1,000 visits | $60 | $160 |
| User sentiment | "this feels commercial" | "this just works" |
| Word-of-mouth recommendation | suppressed | amplified |
| Long-term audience growth | slower | faster |

Loud monetisation optimises short-term clicks at the cost of
long-term audience.  Subtle monetisation does the opposite.  For an
open-source hobby tool that lives or dies by recommendations from
self-hosters to other self-hosters, the math overwhelmingly favours
restraint.

---

## Cross-references

- Full strategic context: [PLAN.md](PLAN.md) - especially the
  "Monetisation model" section
- Tier 1 monetisation prep task: see TODO.md / task tracker (#85)
- Pro tier features brainstorm lives in [PLAN.md](PLAN.md) under
  Phase 5 / monetisation
- License model rationale (why BSL not closed-source): [PLAN.md](PLAN.md)

---

*Last updated: 2026-06-03 (drafted at the "scripts/" era; no
revenue active yet, plan for v1.0 launch).*
