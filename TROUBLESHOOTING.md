# Troubleshooting — Friday-night fast path

Stuff goes wrong.  This doc gets you from "something's broken" to a
pasteable artifact in 10 seconds, plus a quick checklist of "what
common breakages usually mean."

---

## The one-button path

When **anything** misbehaves:

1. Open Oblivion → **Config tab** → scroll to **Troubleshooting**.
2. Click **🔧 Copy diagnostic snapshot to clipboard**.
3. Paste into chat / Discord support channel.

The snapshot contains everything needed to debug 95% of issues:
- App version + build type + OS
- Server status (running, boot state, map, mode, uptime, public IP)
- Full active veto session state (mode, teams, captains, ban/pick
  sequence with position marker, decider, ready flags, token usage)
- Plugin manifest (what's currently deployed) **+ file-presence
  verification** *(v0.11.9: catches "deployed-but-missing" silent
  failures)*
- Discord bot status (configured, connected, error if any)
- Persistence file inventory (config, match history, active veto —
  paths + sizes + mtimes)
- **Active veto session raw JSON** *(v0.11.9: catches schema-corruption
  issues that the decoded-view masks; captain tokens redacted)*
- **Last 80 lines** of the app log ring buffer
- **Last 200 lines of CS2 `console.log`** *(v0.11.9: the #1 file when
  the server itself is the problem — MatchZy errors, plugin crashes,
  RCON failures, map load failures)*
- **Disk free space** *(v0.11.9)* at config dir, csgo dir, server dir
- **Request context** *(v0.11.9)* — your User-Agent (helps debug
  browser-specific issues like clipboard / popup blocking)
- Config (sensitive values masked: PINs, passwords, bot token,
  webhook URL)

Secrets are masked.  Safe to paste publicly.

> If clipboard write fails (Edge WebView2 occasionally blocks it),
> the snapshot opens in a new tab — copy from there manually.

---

## Where else logs live

| System | Location | When to grab it |
|---|---|---|
| **App ring buffer** | The snapshot button (above) — last 80 lines | First port of call for everything |
| **Saved app log** | `%APPDATA%\Oblivion Server Tool\oblivion_log_<ts>_<6hex>.txt` via Log drawer → 💾 Save | When you need MORE than 80 lines |
| **CS2 console.log** | `<server_dir>\game\csgo\console.log` | When the server itself is misbehaving — MatchZy errors, plugin crashes, RCON failures, map load failures |
| **Active veto state** | `%APPDATA%\Oblivion Server Tool\oblivion_veto_active.json` | Already in the snapshot, but the file itself if you need it |
| **Match history** | `%APPDATA%\Oblivion Server Tool\oblivion_matches.json` | Last 10 completed sessions |
| **Plugin manifest** | `%APPDATA%\Oblivion Server Tool\oblivion_plugins.json` | What's currently deployed |
| **Cloudflare tunnel** | The terminal where you ran `cloudflared` | Tunnel connection issues — copy the terminal output |
| **MatchZy match config** | `<csgo>\cfg\MatchZy\<matchid>.json` | When matchzy_loadmatch fails — verify the file was written correctly |
| **Workshop download** | `<server_dir>\depotdownloader\` working files | When a workshop map won't download |

---

## Quick triage guide

### "Captain link doesn't work"

1. Check the snapshot's **Active veto session** → are the tokens
   issued?  Has the captain claimed yet?
2. Check **public_share_url** — set?  If LAN-only, captains on the
   internet won't reach it.
3. Check the cloudflared terminal — tunnel still up?
4. Captain on a phone — try the QR code (Veto → Captain Links → QR)
   instead of the typed link.

### "Server won't start"

1. Snapshot's **last_start_error** field — what does it say?
2. Snapshot's **Server status → boot_state** — stuck on `booting`?
3. CS2 `console.log` — search for `Error` or `Could not load`.
4. Plugin manifest — what's currently deployed?  Mismatch with the
   mode you're trying to start?

### "MatchZy didn't load the match"

1. Snapshot's **Active veto session → matchzy_config_built** —
   should be True after finale.
2. Check `<csgo>\cfg\MatchZy\<matchid>.json` exists.
3. CS2 console.log — search for `matchzy_loadmatch`.  The RCON
   response is logged.
4. Did the operator click "Hand to MatchZy" or did auto-launch fire?
   Check `veto_auto_launch_on_ready` in config.

### "Discord bot DM didn't arrive"

1. Snapshot's **Discord bot** section — `connected: True`?
2. Captain's `discord_id` filled in at roster time?
3. Does the captain have **DMs from server members** enabled in
   their Discord privacy settings?  (Common cause.)
4. Test with the Config → Discord → **Test DM** button against the
   captain's user ID.

### "Live veto embed didn't post / didn't update"

1. Snapshot's `veto_channel_id` — set?
2. Bot has Send Messages + Embed Links + Read Message History
   permissions in that channel?
3. Test with Config → Discord → **Test Embed** button.

### "App crashed / closed mid-session"

1. Reopen Oblivion → check Veto tab — should auto-resume (v0.11.3
   feature).
2. Captain claims are preserved across restart, but live Discord
   embed message ID is cleared — bot will post a fresh embed if
   configured.
3. If the resume looks wrong, hit Veto → Reset to start clean
   (`oblivion_veto_active.json` will be cleared).

### "Tunnel URL stopped working"

1. Cloudflared terminal — still running?  Quick tunnels rotate URLs.
2. Re-paste the new URL into Config → Veto / Match Setup →
   **Public Share URL** → Save.
3. Re-share new captain links (the old ones bind to the old URL
   base; captain tokens themselves still work).

### "I get a lag spike every time I alt-tab to Discord / browser / Oblivion"

You're running CS2 server + game client on the same PC.  Windows
reshuffles CPU/GPU priorities every time the foreground changes,
and both `cs2.exe` processes end up briefly fighting for execution
resources.  This is fixable without new hardware.

**Quick fix** (one-time setup, then double-click before each session):
1. Run `scripts/install-shortcuts.bat` from the repo (one-time).
2. Before a hosting + playing session: double-click **Oblivion -
   Gaming Mode ON** on your desktop.  UAC prompt; ~3 seconds.
3. When done playing: double-click **Oblivion - Gaming Mode OFF**.

What that does: pins the CS2 server to first 4 cores at High
priority, client to remaining cores, Oblivion to core 0.  Once
pinned, Windows can't reshuffle anything on alt-tab because every
process has been explicitly told which cores it's allowed to use.

**Persistent across reboots** — install Process Lasso and follow
`scripts/PROCESS_LASSO_SETUP.md`.  Sets up the same pinning to
re-apply automatically on every process spawn forever.

**Future**: v0.12 bakes this into Oblivion's Config tab as a
single toggle — no scripts, no Process Lasso, just a checkbox.

### "Server stuck in a weird state, just reset everything"

1. Veto: `/api/veto/reset` (Reset button on the Veto header).
2. Plugins: restart the server through the tool — `_undeploy_plugins`
   fires automatically when switching modes.
3. Nuclear: stop server, manually delete `<csgo>\addons\`, restart
   server in your target mode.  Oblivion redeploys.

---

## When sending logs to a maintainer

**Prefer the diagnostic snapshot** over raw log file dumps.  It
contains structured context (versions, state, file inventory) that's
much faster to triage than a 2000-line text file.

If 80 log lines isn't enough, **also** save a full log file:
1. Open Log drawer (📋 icon, bottom-right)
2. Click 💾 Save
3. Attach the resulting `oblivion_log_*.txt` from
   `%APPDATA%\Oblivion Server Tool\`.

Include:
- What you were trying to do
- What you expected to happen
- What actually happened
- Whether it's reproducible
- The diagnostic snapshot

---

## Common log markers to grep for

Every meaningful event in the app logs with a `[tag]` prefix.  Useful
greps:

```
[veto]        — veto session state transitions
[discord]     — bot connection, DMs, embeds
[matchzy]     — match-config writes, loadmatch RCON calls
[workshop]    — DepotDownloader activity
[rcon]        — RCON connectivity events
[startup]     — app boot + Flask port selection
[crash]       — crash detection + auto-restart
[preflight]   — pre-Start checks (port held, plugin missing, etc.)
[steam]       — steamcmd activity
```

In Oblivion's log drawer you can Ctrl+F your browser's find on the
log text.  Or save to file and grep there.

---

## Security: PIN auth + remote exposure

Oblivion's web panel uses a **4-8 digit PIN** for authentication.  That's
fine for the **default LAN-only deployment**, but the threat model
changes when you expose the panel to the public internet via a
Cloudflare quick tunnel or port-forward.

### What the PIN protects

- **Local sessions** (running on the host machine, `127.0.0.1`): the PIN
  is mostly a tab-rip-off defender — anyone who can run the .exe can
  also read the config file, so the PIN doesn't gate anything they
  couldn't otherwise reach.
- **Remote sessions** (over a Cloudflare tunnel / port-forward): the
  PIN is the ONLY thing between your dedicated server and the
  internet at large.

### Threat model

| Attack | Mitigated? | Notes |
|---|---|---|
| Brute-force a weak PIN over the tunnel | partially | 5 failed attempts per IP triggers a 60-second lockout (`_login_lockouts` GC + sliding window). 4-digit PINs (10 000 combinations) are still brute-force-able over hours if the attacker has patience and rotates IPs. |
| Captain token theft via shoulder-surf | yes | Captain tokens are single-use and bound to one team. Reset clears them. |
| Captain link replay after match ends | yes | Tokens get invalidated on `/api/veto/reset` (v0.11.21). |
| Session hijack via stolen cookie | partially | SameSite=Lax + Secure-on-HTTPS (v0.11.20). HTTP-only is set; no JS access. But there's no rotation on sensitive actions. |
| Guest PIN privilege escalation | yes | Guest role is gated to maps/modes/workshop downloads only; all destructive endpoints are admin-gated server-side. |
| RCON password leak via XSS | yes | All operator strings in the SPA are `esc()`'d. |
| CSRF on admin endpoints | yes | All admin endpoints require a same-origin session cookie. |
| Steam password theft from `oblivion_config.json` | partially | Stored in Windows keyring when available; falls back to plaintext in the config file. Backups (v0.16.0) DO contain whatever was in the live file. |

### Recommendations by exposure level

**LAN only** (`http://127.0.0.1:5050` and `http://<LAN IP>:5050`):
- A 4-digit admin PIN is fine.
- Guest PIN can stay blank.

**Public tunnel** (Cloudflare quick tunnel / port-forward):
- Use an **8-digit** admin PIN.  Even with lockouts, 4 digits is too few.
- If you're sharing access with captains, use the guest PIN for them —
  never share the admin PIN.
- **Rotate the admin PIN** after each tournament.  Old captain links
  reference the same session cookie format; rotating revokes them.
- Stop the tunnel when not in use (`Ctrl+C` on the `cloudflared` window).
  An unused public endpoint is an attack surface that adds zero value.

**Post-v1.0 we may add** TOTP / magic-link / OAuth for the admin role.
Until then, the above is the honest read.

### What the diagnostic snapshot shows about your security posture

The snapshot's `Config (redacted)` section shows `admin_pin: ***` and
`guest_pin: ***` so you can confirm the PINs are SET without leaking
them.  If either shows `(none)` or empty, fix that before exposing
the panel.
