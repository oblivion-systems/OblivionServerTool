# LICENSE — Oblivion Server Tool

**Status: DRAFT for v1.0 launch.** Currently MIT in README; flipping to
Business Source License 1.1 (BSL) is task #89.

---

## Plan

Adopt **BSL 1.1** with these terms (TBD by maintainer):

| Field | Proposed value | Decision needed |
|---|---|---|
| Licensor | Jacques van Niekerk | confirm name/handle |
| Licensed Work | Oblivion Server Tool v1.0+ | confirm starting version |
| Additional Use Grant | "non-commercial use is unrestricted; commercial use up to N CS2 servers per organisation" | pick N (typical: 1 or 3) |
| Change Date | 2030-06-17 (4 years from v1.0) | typical 4y; could be 3y |
| Change License | Apache License, Version 2.0 | confirm |

When filled in, replace this file with the full BSL 1.1 text from
<https://mariadb.com/bsl11/> with the four parameters substituted.

---

## Why BSL not MIT for v1.0

BSL stops a SaaS clone from undercutting the maintainer during the
non-compete window while still letting:
- individual operators use it freely (commercial or not, under the cap)
- the source remain readable + auditable
- the project auto-revert to a permissive license (Apache 2.0) at the
  Change Date so it never disappears behind a paywall.

This mirrors the approach Sentry, MariaDB, Cockroach, and Sourcegraph
use.

---

## Pre-v1.0 (everything before this file is committed)

All commits prior to the BSL-effective tag remain under MIT (the README's
existing terms).  No retroactive relicensing — anyone who forked while
MIT was in force keeps MIT rights to that snapshot.
