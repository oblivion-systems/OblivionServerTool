# DISCORD — Bot setup guide (v0.11.0)

> One-page operator runbook for setting up the Discord bot integration.
> 5-minute one-time setup.  After this you'll have a bot token to paste
> into Config → Discord, and an invite link to add the bot to your
> Discord server.
>
> The v0.10.2 webhook is the no-setup alternative (server → channel,
> one direction).  This guide is for v0.11.0 which adds: **DM captain
> links automatically**, **voice-channel roster pull**, **live veto
> embed** that updates as captains ban/pick.

---

## Step 1 — Create the Discord application

1. Open https://discord.com/developers/applications
2. Click **New Application** (top right)
3. Name it (e.g. *Oblivion*).  This is the bot's display name in your server
4. Accept the terms

You're now on the application's **General Information** page.

---

## Step 2 — Convert to a bot + grab the token

1. Left sidebar → **Bot**
2. *(If prompted to "Reset Token" — that means a token already exists; reset it to get a fresh one)*
3. Under **Token**, click **Reset Token** → confirm → **copy the token**

> **This token is a secret.**  Anyone with it can act as your bot.
> Treat it like a password.  Oblivion stores it in `oblivion_config.json`
> on your local machine only — never sent to remote sessions.

If you lose the token, come back here and Reset Token again — the old one stops working immediately.

---

## Step 3 — Enable the privileged intents

Still on the **Bot** page, scroll down to **Privileged Gateway Intents**:

- ✅ **Server Members Intent** — needed to read voice-channel member lists for roster pull
- ✅ **Message Content Intent** — needed for `/slash` command argument parsing
- ❌ **Presence Intent** — leave disabled (we don't track online status)

Click **Save Changes** at the bottom.

---

## Step 4 — Build the invite URL

1. Left sidebar → **OAuth2** → **URL Generator**
2. Under **Scopes**, check:
   - ✅ `bot`
   - ✅ `applications.commands` *(for the slash commands the bot will register)*
3. The **Bot Permissions** section appears below.  Check:
   - ✅ **View Channels**
   - ✅ **Send Messages**
   - ✅ **Send Messages in Threads** *(optional — only if you use threads)*
   - ✅ **Embed Links** *(required for the rich finale embed)*
   - ✅ **Read Message History** *(required for editing the live veto embed)*
   - ✅ **Connect** *(voice — needed to read voice-channel member lists)*
4. Copy the generated URL at the bottom

The URL looks like `https://discord.com/oauth2/authorize?client_id=...&scope=bot+applications.commands&permissions=...`

---

## Step 5 — Invite the bot to your server

1. Paste the URL into a browser
2. **Add to server** → pick your Discord server from the dropdown
3. Authorise the permissions

The bot should now appear in your server's member list (shown as offline until Oblivion connects it).

---

## Step 6 — Find your Discord Server ID + Veto-Embed Channel ID

Discord IDs are 17-19 digit numbers.  Two ways to get them:

**Easy (Developer Mode):**
1. Discord settings → **Advanced** → **Developer Mode** ✅
2. Right-click your server name → **Copy Server ID**.  This is your `discord_guild_id`.
3. Right-click the channel you want live veto embeds in → **Copy Channel ID**.  This is your `discord_veto_channel_id` (Layer 1C — optional).

---

## Step 7 — Paste everything into Oblivion

1. Open Oblivion Server Tool → **Config** tab → scroll to **Discord**
2. Paste:
   - **Bot Token** (from Step 2)
   - **Server (Guild) ID** (from Step 6)
   - **Veto Embed Channel ID** (from Step 6, optional — leave blank to skip live embeds)
3. Click **Save Discord Settings**
4. The bot connects within ~5 seconds.  Watch the log drawer for:
   ```
   [discord] Bot starting…
   [discord] Bot connected as Oblivion#1234 (id=...)
   ```
   The header role pill won't change (that's your session role, not the bot).

If you see `[discord] Login failed — check your bot token in Config.`, the token is wrong or was reset since you copied it.  Reset it again and re-paste.

---

## Step 8 — Optional: per-player Discord ID at roster time (Layer 1A)

For the DM-captain-links feature, each rostered player needs an optional
`discord_id`.  Two ways to fill it:

- **Manual** — operator types it in the new roster column.  Right-click a Discord user → **Copy User ID** to get a 17-19 digit number.
- **Voice-channel pull** (Layer 1B) — operator clicks **🎤 Pull from voice channel** on the roster page → picks a channel → bot reads connected members + auto-populates names + Discord IDs.

If a captain has no `discord_id`, the DM step is skipped silently and the operator's existing Copy-for-Discord button is the fallback.

---

## Troubleshooting

**"Login failed — check your bot token"**
The token is wrong.  Reset it in Developer Portal → paste again.

**"Bot connected" but DMs silently fail**
The captain has DMs disabled from non-friends in their Discord privacy
settings.  Workflow: Copy-for-Discord button instead.

**Voice-channel pull returns "Couldn't read voice channel"**
1. Bot doesn't have **Connect** permission on the channel (server settings → role for the bot → grant Connect)
2. The channel ID is wrong
3. The Server Members intent isn't enabled (Step 3)

**Live veto embed doesn't post**
1. `discord_veto_channel_id` is blank (skip-by-default)
2. Bot doesn't have **Send Messages** + **Embed Links** in that channel
3. Channel ID is wrong

---

## Security notes

- **The bot token is the most sensitive value**.  Anyone with it can post as your bot in any server it's joined to.  Oblivion stores it local-only (`is_local` gate) — remote admin sessions see `***`, can't change it.
- **The bot does not autoreply** to messages, DMs, or `@mentions` — every action is operator-initiated from the Oblivion SPA.
- **Per-user bot vs shared bot**: each operator runs their own bot instance with their own token bound to their own server.  No shared infrastructure.

---

## What lives in `oblivion_config.json` after setup

```json
{
  "discord_bot_token":         "MTAxxxxxxxxxxxxxxxxxxxxxx.xxxxxxx.xxxxxxx...",
  "discord_guild_id":          "123456789012345678",
  "discord_veto_channel_id":   "234567890123456789"
}
```

That file is in `%APPDATA%\OblivionServerTool\` on Windows (your local user profile).  Don't sync it to a public drive.
