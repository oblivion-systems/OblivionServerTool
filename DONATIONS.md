# DONATIONS — Oblivion Server Tool

**Status: DRAFT for v1.0 launch.** Task #89.

The app is donation-funded — no ads, no paid tiers, no telemetry.  If
you run a regular tournament on it and want to keep development going,
the options below are how to chip in.

---

## Platform comparison

| Platform | One-off | Recurring | Fee | Tax handling | Anonymous? |
|---|---|---|---|---|---|
| **GitHub Sponsors** | ✓ | ✓ | 0% (GitHub absorbs Stripe fees) | GitHub handles US tax form | no — Github username visible |
| **Ko-fi** | ✓ | ✓ | 0% one-off · 5% recurring | maintainer self-reports | yes (guest mode) |
| **Liberapay** | — | ✓ | 0% (relies on Stripe/PayPal) | maintainer self-reports | yes |
| **Stripe direct** | ✓ | ✓ | 2.9% + 30¢ | maintainer self-reports | no |
| **PayPal.me** | ✓ | — | 2.9% + 30¢ | maintainer self-reports | no |

---

## Recommendation

**GitHub Sponsors as the primary link** + **Ko-fi as the fallback** for
people who don't have a Github account.  Both link from the README
right under the badges section.

GitHub Sponsors covers the 80% case (developers already have Github
accounts, the Stripe fee absorption is real money over a year), and
Ko-fi catches the rest (CS2 operators who're more comfortable with a
gaming-adjacent tip jar than a developer-tool platform).

---

## Decisions to confirm before v1.0

1. **Primary platform**: GitHub Sponsors? Ko-fi? Both?
2. **Suggested tiers** (if any): $3 / $5 / $10 / $25 / "Pay what works"?
3. **Perks** (if any): name in CONTRIBUTORS.md? early-access builds?
   custom plugin requests?
4. **Cadence**: recurring-only? one-off-only? both?
5. **Goals page**: list specific funding goals (e.g. "$50/mo unlocks
   Linux build") or keep it vague?

Once decided, populate the README badge block:

```markdown
[![GitHub Sponsors](https://img.shields.io/github/sponsors/{username}?label=Sponsor&logo=GitHub)](https://github.com/sponsors/{username})
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Tip-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/{username})
```

And add a `.github/FUNDING.yml` so the "Sponsor" button appears on the
repo page:

```yaml
github: [{username}]
ko_fi: {username}
```

---

## Pre-v1.0

Don't enable any donation links before v1.0 ships under BSL.  Soliciting
money for an MIT project that the operator can't legally restrict from
SaaS clones is a bad look — wait until the license + posture is set.
