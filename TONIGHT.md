# Tonight — Friends-Night Runbook

A one-page operational guide: remote access for friends + per-mode "what good looks like".
Keep this open on a second monitor.

---

## 0. Remote access (let friends change maps/modes from their place)

The remote web panel is **already built into the app** (Flask on port `5000`, PIN-gated). You
just expose it. Friends can: **change map, change mode, start/restart**. They **cannot** run raw
RCON or install/update (those are local-only) — exactly what you want.

### Quick tunnel (recommended — HTTPS, no router changes)
1. **Set a non-default PIN** first: Config → Security → change the admin PIN (don't leave `1234`).
2. Make sure the app (and its Flask server) is running.
3. Open a **new** terminal (PowerShell) and run:
   ```
   cloudflared tunnel --url http://localhost:5050
   ```
   *(installed at `C:\Program Files (x86)\cloudflared\cloudflared.exe` — a fresh terminal has it on PATH)*
4. It prints a URL like `https://random-words.trycloudflare.com`. **Share that link + the PIN.**
5. Friends open the link, enter the PIN, and they're in.
6. **When done:** press `Ctrl+C` in that terminal — the URL dies instantly.

### Alternative (no tunnel): port-forward `5050`
Forward TCP `5050` on the router to this PC (like you did `27015`). Friends go to
`http://<your-public-ip>:5050`. Simpler, but **plaintext HTTP** (PIN sent in clear) and exposes
the panel to the internet. Fine for one night; tear it down after.

> Safety: the PIN is 4 digits. Behind the HTTPS tunnel that's acceptable for a friends night.
> Brute-force backoff (20 fails → 5 min lockout) and IP-bound sessions are already in place.

### Optional: hand out a GUEST PIN (limited access)
Config → Security → set a **Guest PIN** (separate from the admin PIN; blank disables it). Share
the **guest** PIN with friends and keep the admin PIN to yourself:
- **Guest can:** change map, change game mode, download workshop maps.
- **Guest can't:** start/stop the server, edit config, manage bots/bans, view logs.

So you can let people remix the map rotation without risk of someone stopping the server or
changing settings. The admin PIN still grants everything (and RCON/install stay local-only).

---

## 1. Pre-flight (solo, before friends arrive)

- [ ] Pre-download the workshop maps you'll want — at least one `ze_` map for Zombie Escape, an
      `aim_`/`1v1` map for arenas. (Maps tab → paste ID → Download.)
- [ ] Maps tab → **Scan command-filter needs** (so flagged workshop maps launch correctly).
- [ ] Config → **Bots**: set the Use-bots toggle how you want (off = humans-only arenas/retakes).
- [ ] Solo-test the two least-proven modes below: **Zombie Escape** and a **MatchZy** team match.

---

## 2. Per-mode runbook

For every mode: pick **Mode** + **Map** in the unified picker → **Start** (or **Change Map** if
already running). The "Selected: …" line confirms what will load. If you pick a workshop map that
doesn't suit the mode, the popup offers **Switch to &lt;mode&gt; & load**.

| Mode | What "good" looks like | Host / player chat cmds | Most likely failure → fix |
|---|---|---|---|
| **Competitive / Casual / Wingman** | Normal match, no plugins. | — | If a plugin lingers from a prior mode, the log shows the clean switch to vanilla. |
| **1v1** (K4-Arenas) | Players queue into duels, winner climbs the ladder. | `!guns` `!rounds` `!queue` `!afk` `!challenge` | Odd player waits at their rank (humans-only) → turn **Use-bots ON** to fill. |
| **2v2** (K4-Arenas) | Arenas run **2-per-side**. | same as 1v1 | Runs as 1v1 → generated `configs/plugins/K4-Arenas/K4-Arenas.json` was rejected; re-deploy the mode, or accept 1v1 for the night. |
| **3v3 / 4v4 / 5v5** (MatchZy) | Warmup → players `.ready` → knife round → LIVE. | `.ready` · `.forcestart` (skip ready) · `.knife off` (skip knife) · `.pause`/`.unpause` · `.stop` | Match won't start → not enough ready → `.forcestart`. Unexpected knife round → `.knife off` before readying. Uneven teams → players switch team manually. |
| **Zombie Escape** ⚠️ *MetaMod — restart on switch* | Round starts, first-infected turns zombie, humans flee; zombies have **models/sounds**. | mostly automatic (ZR/cs2fixes) | Zombies T-posing / no models → content pack didn't mount; check server console for `mm_extra_addons 3157463861` downloading, have the friend **reconnect once**. No `ze_` maps in list → download one first. |
| **Warcraft** | `!class` to pick a class, gain XP, `!shop` for items; **Barbarian renders** (not invisible). | `!class` `!skills` `!shop` `!reset` | Barbarian invisible → ModelPrecacher didn't deploy (re-deploy mode). Menu text clips → known cosmetic, still usable. |
| **Retakes** | Bomb planted each round, attackers retake the site. | `!guns` (allocator prefs) | Players spawning in walls → spawn config (already fixed). No rounds on low pop → **Use-bots ON**. |

---

## 3. Live ops cheatsheet

- **Switch modes:** use the picker. **Zombie Escape & Deathmatch are MetaMod** → the app will say
  "RESTART REQUIRED" and restart the server. Plan to do those in their own block to limit restarts.
- **Mismatch popup:** picked a `ze_`/`jb_`/`aim_` map under the wrong mode? Hit
  **Switch to &lt;mode&gt; & load** — it fixes the mode and loads the map in one click.
- **Crash:** the server auto-restarts (up to 3 attempts). Watch the log.
- **Remote friend acting up:** change the PIN in Config (kicks remote sessions), or `Ctrl+C` the
  tunnel to cut all remote access instantly.
- **End of night:** `Ctrl+C` the cloudflared terminal; stop the server from the panel.
