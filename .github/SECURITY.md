# Security policy

## Reporting a vulnerability

If you find a security issue, **don't open a public issue**.  Open a
GitHub Security Advisory at
<https://github.com/oblivion-systems/OblivionServerTool/security/advisories/new>
or message the maintainer privately via the GitHub profile linked from
the [README](README.md).

The maintainer aims to acknowledge within 72 hours and either fix or
publish a workaround within 7 days for anything that affects the live
attack surface below.

---

## Surface

This app handles the following sensitive material:

| Surface | Storage | Risk |
|---|---|---|
| Admin PIN | `%APPDATA%/.../oblivion_config.json` | Compromises remote web-panel access |
| Guest PIN | same | Limited-role remote access |
| RCON password | same | Server admin via RCON |
| Steam workshop password | same (encrypted by steamcmd's own store) | Workshop downloads |
| Discord bot token | same | Bot impersonation in any joined guild |
| GSLT token | same | VAC-server identity |
| Cloudflare tunnel URLs | not persisted by the app; pasted by operator | If shared, internet-exposed access |
| Per-captain veto session tokens | RAM + atomic `oblivion_veto_active.json` | Captain seat hijack |
| Per-player vote tokens | RAM + atomic write | Vote impersonation |

The app itself is a Windows desktop application listening on `127.0.0.1`
and on the LAN IP only.  No remote attack surface exists unless the
operator deliberately exposes it (e.g. via a Cloudflare quick tunnel
documented in `TONIGHT.md`).

The threat-model walkthrough lives in **[TROUBLESHOOTING.md](../TROUBLESHOOTING.md)**
under "Security: PIN auth + remote exposure".

---

## What we will (and won't) call a vulnerability

**In scope:**
- PIN brute-force lockout bypass
- Captain token reuse / theft
- Privilege escalation (guest → admin, captain → operator)
- Cross-session token confusion
- Path traversal / Zip Slip in plugin install paths
- RCON command injection via SPA endpoints
- Sensitive material leaking into logs / diagnostic snapshots / spectator
  payloads
- Anything that lets a network-adjacent attacker run code or read secrets
  off the operator's machine

**Out of scope:**
- Exposing the panel publicly without auth — operator's responsibility
- Sharing the PIN — operator's responsibility
- Vulnerabilities in **bundled plugins** (MetaMod, CounterStrikeSharp,
  MatchZy, etc.); please report those upstream.  See **[CREDITS.md](CREDITS.md)**
  for each plugin's upstream URL.
- Vulnerabilities in CS2 itself — report to Valve
- Issues that require the attacker to already have admin access to the
  operator's machine

---

## Supported versions

Security fixes ship in the next patch release of the current minor
(`v1.x.y` → `v1.x.(y+1)`).  No backports to pre-v1.0 versions; the
launch tag `v1.0.0` is the first formally-supported line.
