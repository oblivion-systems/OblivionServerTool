"""
cs2servergui/discord_bot.py — v0.11.0 Discord bot integration (Layer 1).

Maintains a single discord.py gateway connection to the operator's Discord
server.  Used by the veto feature for three things:

  Layer 1A — DM captain links to elected captains when /api/veto/tokens
             mints them.  Roster carries an optional `discord_id` per
             player; bot does `bot.fetch_user(id).send(text)` for each
             captain.  Failure (DM blocked, ID missing) is non-fatal —
             the existing Copy-for-Discord button remains the fallback.

  Layer 1B — Voice-channel roster pull.  Operator triggers a roster
             import; bot reads the connected members of the chosen
             voice channel + returns {display_name, discord_id}[].

  Layer 1C — Live veto embed.  Bot posts an embed in a configured
             channel + edits it on every ban/pick + on finale.

ARCHITECTURE
------------
discord.py is async-first; the Flask app is threaded.  Bridging:

  * One dedicated background thread owns the discord asyncio event loop
    (`_BotRunner`).  Started by `start_bot(core)` when a token exists.
  * Flask-side code communicates with the loop via thread-safe wrappers
    (`bot_dm_user`, `bot_voice_members`, `bot_voice_channel_info`,
    `bot_voice_channels`, `bot_text_channels`, `bot_post_embed`,
    `bot_edit_embed`).
    Each wrapper schedules a coroutine on the bot's loop via
    `asyncio.run_coroutine_threadsafe` and waits for the result with a
    timeout.
  * Lifecycle: `start_bot` is idempotent + safe to call after token change.
    `stop_bot` shuts the loop down cleanly.  On token change the operator
    saves Config; the web layer calls stop+start to pick up the new token.

DEPENDENCIES
------------
  discord.py >= 2.3.0

INTENTS
-------
  members  — required for voice-channel member enumeration + DM resolve

  v0.11.17 A5 — dropped `message_content` (privileged, requires Dev
  Portal toggle).  We never read message content, only post embeds and
  enumerate voice-channel members.  Leaving it enabled meant a
  fresh-guild migration could silently fail to connect if the operator
  hadn't enabled the toggle in the Developer Portal.

The bot does NOT need the presence intent (we don't track online status).

FAILURE MODES
-------------
  * No token configured       — module imports cleanly, start_bot is a no-op
  * Invalid token             — discord.py raises LoginFailure on connect;
                                we log + retry every 30s
  * Network drop              — discord.py auto-reconnects
  * DM blocked by user        — bot_dm_user returns False; caller falls
                                back to the SPA's Copy-for-Discord button
  * Operator missing guild    — bot_voice_members raises GuildNotFound;
                                roster pull falls back to manual entry
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable

try:
    import discord                                  # type: ignore
    DISCORD_AVAILABLE = True
except ImportError:
    discord = None                                  # type: ignore
    DISCORD_AVAILABLE = False


# ─── Module state ──────────────────────────────────────────────────────────
# We keep state at module level (not a class) so:
#   - `start_bot(core)` can be called repeatedly without re-allocating
#   - Flask threads import a stable name space (`from . import discord_bot`)
# Mutation goes through the public functions below.

_runner: "_BotRunner | None" = None
_runner_lock = threading.Lock()
# Logger separate from AppCore.log so noisy discord.py internals (heartbeat,
# reconnect chatter) don't pollute the operator-facing log drawer.  Tag
# operator-visible events with core.log() inside the call-site wrappers.
_log = logging.getLogger("oblivion.discord_bot")


# ─── Runner: the gateway connection on its own thread ─────────────────────
class _BotRunner:
    """Owns a single discord.py Client + its asyncio loop, both pinned to
    a dedicated daemon thread so the main Flask process can shut down
    cleanly without joining a discord.py thread that might be mid-reconnect.

    Public surface (used only by the module-level functions below):
      run()       — call once on the dedicated thread; blocks until stop()
      stop()      — thread-safe shutdown; idempotent
      submit(coro) — schedule coroutine on the loop, return Future
      ready       — threading.Event; set when the bot is `on_ready`
      bot         — the discord.Client (None until thread starts)
    """

    def __init__(self, token: str, core, intents):
        self.token = token
        self.core  = core            # AppCore — for log() callbacks
        self.intents = intents
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bot: "discord.Client | None" = None
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run(self) -> None:
        """Top-of-thread entry point.  Creates the asyncio loop + Client,
        runs until stop() flips the event.  Reconnects automatically
        (discord.py handles transient drops); we wrap LoginFailure in a
        30s-retry loop so a wrong token can be fixed via config edit
        without restarting the app."""
        asyncio.set_event_loop(asyncio.new_event_loop())
        self.loop = asyncio.get_event_loop()
        self.bot = discord.Client(intents=self.intents)
        # v0.12.1 — slash command tree.  Commands are registered ONCE per
        # bot start (in _register_app_commands below) and sync'd to the
        # operator's guild in on_ready.  We sync per-guild (not globally)
        # so changes show up immediately — global sync can take up to an
        # hour for Discord to propagate.
        self.tree = discord.app_commands.CommandTree(self.bot)
        self._register_app_commands()

        @self.bot.event
        async def on_ready():
            self.ready.set()
            user = self.bot.user
            self.core.log(f"[discord] Bot connected as {user} (id={user.id})")
            # Sync slash commands to the configured guild if any.  A blank
            # guild_id falls back to global sync (slower propagation but
            # the operator gets the commands eventually).
            try:
                gid_str = (self.core.discord_guild_id or "").strip()
                if gid_str:
                    guild = discord.Object(id=int(gid_str))
                    cmds = await self.tree.sync(guild=guild)
                    self.core.log(
                        f"[discord] Synced {len(cmds)} slash command(s) "
                        f"to guild {gid_str}")
                else:
                    cmds = await self.tree.sync()
                    self.core.log(
                        f"[discord] Synced {len(cmds)} slash command(s) "
                        "globally (no guild configured)")
            except Exception as exc:
                self.core.log(
                    f"[discord] slash-command sync failed: {exc}")

        @self.bot.event
        async def on_disconnect():
            # discord.py will reconnect automatically; we just clear the
            # ready flag so callers know to fail-fast during the gap.
            self.ready.clear()

        try:
            self.loop.run_until_complete(self._run_with_retry())
        finally:
            # Shutdown: close the bot + the loop cleanly.  Suppress
            # CancelledError noise from the abrupt close.
            try:
                if self.bot and not self.bot.is_closed():
                    self.loop.run_until_complete(self.bot.close())
            except Exception:
                pass
            self.loop.close()
            self.ready.clear()

    def _register_app_commands(self) -> None:
        """v0.12.1 — register slash commands on the tree.  Called once per
        bot start, before connection.  Commands are sync'd in on_ready.

        Registered:
          /round-summaries  on | off | status
          /move-teams       now | auto on | auto off | status
        """
        import discord as _d
        from discord import app_commands as _ac
        core = self.core

        # Permissions: manage_guild for any toggle/fire — anyone with
        # that perm in Discord can already run the operator's life
        # remotely via the existing live veto embed setup.
        admin_perms = _d.Permissions(manage_guild=True, move_members=True)

        # ── /round-summaries ───────────────────────────────────────────
        round_group = _ac.Group(
            name="round-summaries",
            description="Toggle per-round match summary embeds.",
            default_permissions=admin_perms,
        )

        @round_group.command(name="on", description="Enable round summaries.")
        async def _round_on(itx: _d.Interaction):
            if not (core.discord_veto_channel_id or "").strip():
                await itx.response.send_message(
                    "❌ Set the veto channel in the Oblivion config first — "
                    "round summaries post there.",
                    ephemeral=True)
                return
            core.discord_round_summaries_enabled = True
            core.save_config()
            await itx.response.send_message(
                "✓ Round summaries: **ON**", ephemeral=True)

        @round_group.command(name="off", description="Disable round summaries.")
        async def _round_off(itx: _d.Interaction):
            core.discord_round_summaries_enabled = False
            core.save_config()
            await itx.response.send_message(
                "✓ Round summaries: **OFF**", ephemeral=True)

        @round_group.command(name="status", description="Show round-summaries state.")
        async def _round_status(itx: _d.Interaction):
            on = bool(core.discord_round_summaries_enabled)
            ch = (core.discord_veto_channel_id or "").strip() or "(none)"
            await itx.response.send_message(
                f"Round summaries: **{'ON' if on else 'OFF'}**\n"
                f"Target channel: `{ch}`", ephemeral=True)

        self.tree.add_command(round_group)

        # ── /move-teams ────────────────────────────────────────────────
        # Closes task #145 in the same release as the helper itself.
        move_group = _ac.Group(
            name="move-teams",
            description="Bot-driven team voice channel splits.",
            default_permissions=admin_perms,
        )

        @move_group.command(name="now", description="Move rostered players into their team VCs.")
        async def _move_now(itx: _d.Interaction):
            await itx.response.defer(ephemeral=True, thinking=True)
            # Run the move on the bot's loop directly — same as the
            # bot_move_to_team_channels wrapper, but inline so we can
            # await it without the threading bridge.
            a_vc = (core.discord_team_a_voice_channel_id or "").strip()
            b_vc = (core.discord_team_b_voice_channel_id or "").strip()
            gid  = (core.discord_guild_id or "").strip()
            if not gid:
                await itx.followup.send("❌ No Discord guild ID configured.", ephemeral=True)
                return
            if not a_vc or not b_vc:
                await itx.followup.send(
                    "❌ Configure both Team A and Team B voice channels in Oblivion.",
                    ephemeral=True)
                return
            sess = getattr(core, "_veto_session", None)
            if sess is None or sess.state in ("idle", "roster"):
                await itx.followup.send(
                    "❌ No team-split session — distribute teams first.",
                    ephemeral=True)
                return
            a_ids = [p.discord_id for p in sess.team_a if (p.discord_id or "").strip()]
            b_ids = [p.discord_id for p in sess.team_b if (p.discord_id or "").strip()]
            if not a_ids and not b_ids:
                await itx.followup.send(
                    "❌ No `discord_id`s on either team — fill them in on the Roster stage.",
                    ephemeral=True)
                return
            # Call the async core directly — calling the threaded wrapper
            # from inside the bot loop would deadlock.
            try:
                result = await _do_move_to_team_channels(
                    self.bot, int(gid), int(a_vc), int(b_vc), a_ids, b_ids,
                )
            except (TypeError, ValueError):
                result = None
            if result is None:
                await itx.followup.send(
                    "❌ Move failed — check the bot has Move Members "
                    "permission in your server.",
                    ephemeral=True)
                return
            skipped = result["skipped"]
            errs    = len(result["errors"])
            msg = (f"✓ Moved A **{result['moved_a']}/{len(a_ids)}**, "
                   f"B **{result['moved_b']}/{len(b_ids)}**"
                   + (f", {skipped} not in VC" if skipped else "")
                   + (f", {errs} error(s)" if errs else ""))
            await itx.followup.send(msg, ephemeral=True)

        # /move-teams auto on|off subgroup
        auto_sub = _ac.Group(
            name="auto",
            description="Auto-move toggle (fires after Distribute).",
            parent=move_group,
        )

        @auto_sub.command(name="on", description="Enable auto-move after Distribute.")
        async def _auto_on(itx: _d.Interaction):
            if (not (core.discord_team_a_voice_channel_id or "").strip()
                    or not (core.discord_team_b_voice_channel_id or "").strip()):
                await itx.response.send_message(
                    "❌ Configure both team VCs in Oblivion before enabling auto-move.",
                    ephemeral=True)
                return
            core.discord_auto_move_on_distribute_enabled = True
            core.save_config()
            await itx.response.send_message(
                "✓ Auto-move after Distribute: **ON**", ephemeral=True)

        @auto_sub.command(name="off", description="Disable auto-move after Distribute.")
        async def _auto_off(itx: _d.Interaction):
            core.discord_auto_move_on_distribute_enabled = False
            core.save_config()
            await itx.response.send_message(
                "✓ Auto-move after Distribute: **OFF**", ephemeral=True)

        @move_group.command(name="status", description="Show move-teams state.")
        async def _move_status(itx: _d.Interaction):
            a_vc = (core.discord_team_a_voice_channel_id or "").strip() or "(none)"
            b_vc = (core.discord_team_b_voice_channel_id or "").strip() or "(none)"
            auto = bool(core.discord_auto_move_on_distribute_enabled)
            sess = getattr(core, "_veto_session", None)
            sess_line = "(no active session)"
            if sess is not None:
                a_ids = sum(1 for p in sess.team_a if (p.discord_id or "").strip())
                b_ids = sum(1 for p in sess.team_b if (p.discord_id or "").strip())
                sess_line = (f"state=**{sess.state}**, "
                             f"A has {a_ids} `discord_id`s, B has {b_ids}")
            await itx.response.send_message(
                f"Auto-move after Distribute: **{'ON' if auto else 'OFF'}**\n"
                f"Team A VC: `{a_vc}`\nTeam B VC: `{b_vc}`\n"
                f"Active session: {sess_line}",
                ephemeral=True)

        self.tree.add_command(move_group)

    async def _run_with_retry(self) -> None:
        """Login + connect with reconnect-on-LoginFailure retry.  If the
        token is wrong the operator can paste a new one in Config; we'll
        pick it up on the next start_bot() call (after stop_bot() runs)."""
        while not self.stop_event.is_set():
            try:
                await self.bot.start(self.token)
                return                # graceful exit (stop() called)
            except discord.LoginFailure:
                self.core.log("[discord] Login failed — check your bot token in Config. "
                              "Will retry in 30s.")
                # Sleep in 1s ticks so stop() can break us out faster
                for _ in range(30):
                    if self.stop_event.is_set():
                        return
                    await asyncio.sleep(1)
            except Exception as exc:
                self.core.log(f"[discord] Bot loop error: {type(exc).__name__}: {exc}")
                for _ in range(30):
                    if self.stop_event.is_set():
                        return
                    await asyncio.sleep(1)

    def stop(self) -> None:
        """Thread-safe shutdown.  Sets the stop event + schedules a close
        on the loop (so a blocked-on-await coroutine wakes up)."""
        self.stop_event.set()
        if self.loop and self.bot:
            try:
                asyncio.run_coroutine_threadsafe(self.bot.close(), self.loop)
            except RuntimeError:
                pass    # loop already closed

    def submit(self, coro):
        """Schedule a coroutine onto the bot loop from a Flask worker
        thread.  Returns a concurrent.futures.Future the caller can wait
        on with a timeout."""
        if not self.loop or self.loop.is_closed():
            raise RuntimeError("bot loop not running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def start_thread(self) -> None:
        self._thread = threading.Thread(
            target=self.run, name="oblivion-discord-bot", daemon=True
        )
        self._thread.start()


# ─── Public API: lifecycle ────────────────────────────────────────────────

def start_bot(core) -> bool:
    """Start (or restart) the bot.  Idempotent.  Reads token + guild from
    core.discord_bot_token / core.discord_guild_id.  Returns True if the
    bot is starting / already running; False if no token (silent no-op).

    Safe to call from the Flask request thread — the actual gateway work
    runs on a dedicated daemon thread.
    """
    global _runner
    if not DISCORD_AVAILABLE:
        core.log("[discord] discord.py not installed — bot disabled.  "
                 "Run: pip install discord.py>=2.3.0")
        return False
    token = (core.discord_bot_token or "").strip()
    if not token:
        return False                                # quietly no-op

    with _runner_lock:
        # Stop existing instance if running (token change scenario)
        if _runner is not None:
            _runner.stop()
            # Give it a brief moment to release the loop
            if _runner._thread:
                _runner._thread.join(timeout=3)
            _runner = None

        # Minimal intents — members for voice-channel reads + DM resolve.
        # v0.11.17 A5: message_content intent dropped.  It's privileged
        # (must be enabled in Discord Dev Portal) and we don't actually
        # read message content anywhere — only post embeds + enumerate VC
        # members.  Leaving it enabled meant fresh-guild migrations could
        # silently fail to connect if the operator hadn't ticked the
        # toggle in the Dev Portal (raises PrivilegedIntentsRequired
        # which our outer except swallows into the 30s retry loop).
        intents = discord.Intents.default()
        intents.members = True

        _runner = _BotRunner(token, core, intents)
        _runner.start_thread()
        core.log("[discord] Bot starting…  (connect status will follow)")
        return True


def stop_bot(core=None) -> None:
    """Stop the bot if running.  Safe to call when not running."""
    global _runner
    with _runner_lock:
        if _runner is not None:
            try:
                _runner.stop()
                if _runner._thread:
                    _runner._thread.join(timeout=3)
            except Exception:
                pass
            _runner = None
            if core: core.log("[discord] Bot stopped")


def bot_status() -> dict:
    """Snapshot for /api/state and /api/capabilities.  Reports whether
    the bot is configured + connected + the user it's logged in as."""
    if _runner is None:
        return {"configured": False, "connected": False, "user": None}
    return {
        "configured": True,
        "connected":  _runner.ready.is_set(),
        "user":       (str(_runner.bot.user) if _runner.bot and _runner.bot.user
                       else None),
    }


# ─── Public API: actions (Flask -> bot loop) ──────────────────────────────
# Each action submits a coroutine to the bot's loop + waits for the
# result with a short timeout.  Failure modes are returned as bool/None;
# we never raise from these (Flask layer handles the fallback path).

def _classify_discord_op_error(label: str, target: str, exc: BaseException) -> None:
    """v0.16.15 / task #159 — promote Discord-op failures from `info`
    swallow-everything to typed `warning`/`error` so the operator can
    actually tell what's wrong from the log drawer.  Categories:

      - discord.Forbidden     → bot lacks permission in this guild/channel.
                                Actionable (fix perms in Discord server).
      - discord.NotFound      → channel/message/user deleted (or wrong ID).
                                Actionable (re-pick channel in Config).
      - discord.HTTPException → other API failures, incl. rate limits that
                                outlived discord.py's internal retry.  The
                                .status code goes into the log so 429s are
                                self-evident.
      - Anything else         → unexpected; logged at ERROR with traceback.
    """
    if discord is not None:
        if isinstance(exc, discord.Forbidden):
            _log.warning("[discord] %s(%s) FORBIDDEN — bot lacks permission "
                         "(check role/channel perms in Discord): %s",
                         label, target, exc)
            return
        if isinstance(exc, discord.NotFound):
            _log.warning("[discord] %s(%s) NOT FOUND — channel/message/user "
                         "deleted or ID wrong: %s", label, target, exc)
            return
        if isinstance(exc, discord.HTTPException):
            status = getattr(exc, "status", "?")
            _log.warning("[discord] %s(%s) HTTP %s: %s",
                         label, target, status, exc)
            return
    # Unexpected — full traceback so we can debug it later.
    _log.exception("[discord] %s(%s) unexpected: %s", label, target, exc)


def bot_dm_user(discord_id: str, message: str, *, timeout: float = 8.0) -> bool:
    """Send a DM to a Discord user by ID.  Returns True on success, False
    on any failure (bot not running, ID invalid, DM blocked, network).
    The caller's fallback is the Copy-for-Discord button — this is best-
    effort delivery, not a guarantee."""
    if _runner is None or not _runner.ready.is_set():
        return False
    try:
        uid = int(str(discord_id).strip())
    except (TypeError, ValueError):
        return False
    async def _do():
        user = await _runner.bot.fetch_user(uid)
        await user.send(message)
        return True
    try:
        fut = _runner.submit(_do())
        return bool(fut.result(timeout=timeout))
    except Exception as exc:
        # v0.16.15 — classified logging.  Operator can see the Forbidden
        # case in the log drawer and know to grant the bot DM perms /
        # advise the captain to enable DMs from server members.
        _classify_discord_op_error("bot_dm_user", str(discord_id), exc)
        return False


def bot_voice_members(guild_id: str, channel_id: str, *,
                      timeout: float = 8.0) -> list[dict] | None:
    """Return {display_name, discord_id, steam_id (best-effort)}[] for
    members currently connected to the given voice channel.  Returns
    None on failure (bot not running, guild/channel not found, no
    permission)."""
    if _runner is None or not _runner.ready.is_set():
        return None
    try:
        gid = int(str(guild_id).strip())
        cid = int(str(channel_id).strip())
    except (TypeError, ValueError):
        return None
    async def _do():
        guild = _runner.bot.get_guild(gid)
        if guild is None:
            guild = await _runner.bot.fetch_guild(gid)
        channel = guild.get_channel(cid)
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            return None
        out = []
        for m in channel.members:
            out.append({
                "display_name": m.display_name,
                "discord_id":   str(m.id),
                # SteamID lookup intentionally not attempted — Discord
                # doesn't expose Steam profile data via the bot API.
                # Operator fills it in after import.
                "steam_id":     "",
            })
        return out
    try:
        fut = _runner.submit(_do())
        return fut.result(timeout=timeout)
    except Exception as exc:
        _log.info("bot_voice_members(%s/%s) failed: %s", guild_id, channel_id, exc)
        return None


def bot_voice_channels(guild_id: str, *, timeout: float = 8.0) -> list[dict] | None:
    """Return {id, name, member_count}[] for every voice channel in the
    given guild.  Populates the SPA's "🎤 Pull from voice" channel picker."""
    if _runner is None or not _runner.ready.is_set():
        return None
    try:
        gid = int(str(guild_id).strip())
    except (TypeError, ValueError):
        return None
    async def _do():
        guild = _runner.bot.get_guild(gid)
        if guild is None:
            guild = await _runner.bot.fetch_guild(gid)
        out = []
        for ch in guild.voice_channels:
            out.append({
                "id":           str(ch.id),
                "name":         ch.name,
                "member_count": len(ch.members),
            })
        return out
    try:
        fut = _runner.submit(_do())
        return fut.result(timeout=timeout)
    except Exception as exc:
        _log.info("bot_voice_channels(%s) failed: %s", guild_id, exc)
        return None


async def _do_move_to_team_channels(
    bot,
    gid: int, a_vcid: int, b_vcid: int,
    a_ids: list[str], b_ids: list[str],
) -> dict | None:
    """v0.12.1 — async core of the move-to-team-channels flow.  Exposed
    as a free function so it can be `await`-ed directly from inside the
    bot loop (slash command handler) without going through the threaded
    wrapper (which would deadlock — submitting back to the loop you're
    running on, then blocking on the result).

    Used by:
      - bot_move_to_team_channels (threaded wrapper, Flask handlers)
      - the /move-teams now slash command handler
    """
    guild = bot.get_guild(gid)
    if guild is None:
        try:
            guild = await bot.fetch_guild(gid)
        except Exception:
            return None
    a_ch = guild.get_channel(a_vcid)
    b_ch = guild.get_channel(b_vcid)
    if a_ch is None or not isinstance(a_ch, discord.VoiceChannel):
        return None
    if b_ch is None or not isinstance(b_ch, discord.VoiceChannel):
        return None

    sem = asyncio.Semaphore(5)
    moved_a = 0
    moved_b = 0
    skipped = 0
    errors: list[str] = []

    async def _move_one(member_id_str: str, target_ch, target_label: str):
        nonlocal moved_a, moved_b, skipped
        async with sem:
            try:
                mid = int(member_id_str)
            except ValueError:
                skipped += 1
                return
            member = guild.get_member(mid)
            if member is None:
                try:
                    member = await guild.fetch_member(mid)
                except Exception:
                    skipped += 1
                    return
            if member.voice is None or member.voice.channel is None:
                skipped += 1
                return
            if member.voice.channel.id == target_ch.id:
                if target_label == "A": moved_a += 1
                else:                   moved_b += 1
                return
            try:
                await member.move_to(target_ch, reason=f"Oblivion team-split → team {target_label}")
                if target_label == "A": moved_a += 1
                else:                   moved_b += 1
            except discord.Forbidden:
                errors.append(f"{member.display_name}: bot lacks Move Members permission")
            except discord.HTTPException as exc:
                errors.append(f"{member.display_name}: {exc}")

    tasks = (
        [_move_one(mid, a_ch, "A") for mid in a_ids] +
        [_move_one(mid, b_ch, "B") for mid in b_ids]
    )
    await asyncio.gather(*tasks)
    return {
        "moved_a": moved_a, "moved_b": moved_b,
        "skipped": skipped, "errors": errors,
        "team_a_name": a_ch.name, "team_b_name": b_ch.name,
    }


def bot_move_to_team_channels(
    guild_id: str,
    team_a_vc_id: str,
    team_b_vc_id: str,
    team_a_discord_ids: list[str],
    team_b_discord_ids: list[str],
    *,
    timeout: float = 15.0,
) -> dict | None:
    """v0.12.0 — Move every rostered player with a discord_id into their
    team's voice channel.

    Returns: {"moved_a": int, "moved_b": int, "skipped": int,
              "errors": [str, ...]}
    - moved_a / moved_b — successful moves per team
    - skipped — discord_id was empty, OR the user isn't currently in any
      VC in this guild (Discord's `Member.move_to()` only works on
      connected members), OR the user isn't a member of this guild
    - errors — per-player failure strings ("PlayerName: Missing Permissions"
      etc.)

    Returns None on coarse failure: bot not running, guild not found,
    either VC not found or not a VoiceChannel.

    Concurrency: moves run via asyncio.gather() with a semaphore of 5,
    respecting Discord's rate limits without serializing 10 sequential
    HTTP calls.
    """
    if _runner is None or not _runner.ready.is_set():
        return None
    try:
        gid    = int(str(guild_id).strip())
        a_vcid = int(str(team_a_vc_id).strip())
        b_vcid = int(str(team_b_vc_id).strip())
    except (TypeError, ValueError):
        return None

    # Normalise + dedupe per team (the caller may pass empty strings
    # for un-mapped roster slots; those should silently skip, not error)
    a_ids = [s for s in (str(x).strip() for x in team_a_discord_ids) if s]
    b_ids = [s for s in (str(x).strip() for x in team_b_discord_ids) if s]

    try:
        fut = _runner.submit(_do_move_to_team_channels(
            _runner.bot, gid, a_vcid, b_vcid, a_ids, b_ids,
        ))
        return fut.result(timeout=timeout)
    except Exception as exc:
        _log.info("bot_move_to_team_channels(%s) failed: %s", guild_id, exc)
        return None


def bot_text_channels(guild_id: str, *, timeout: float = 8.0) -> list[dict] | None:
    """v0.11.18 — Return {id, name}[] for every TEXT channel in the guild.

    Used by the Discord Config card's 🔍 Browse helper next to the Veto
    Embed Channel ID field, so the operator can pick a channel without
    leaving the SPA to fish a channel ID out of Discord's right-click
    menu.  Mirrors `bot_voice_channels` shape (id, name) but omits
    member_count — text channels don't have a "connected" notion.

    Returns None on failure (bot not running, guild not found, no
    permission).
    """
    if _runner is None or not _runner.ready.is_set():
        return None
    try:
        gid = int(str(guild_id).strip())
    except (TypeError, ValueError):
        return None
    async def _do():
        guild = _runner.bot.get_guild(gid)
        if guild is None:
            guild = await _runner.bot.fetch_guild(gid)
        out = []
        for ch in guild.text_channels:
            out.append({
                "id":   str(ch.id),
                "name": ch.name,
            })
        return out
    try:
        fut = _runner.submit(_do())
        return fut.result(timeout=timeout)
    except Exception as exc:
        _log.info("bot_text_channels(%s) failed: %s", guild_id, exc)
        return None


def bot_voice_channel_info(guild_id: str, channel_id: str, *,
                           timeout: float = 8.0) -> dict | None:
    """v0.11.15 — Return {id, name, member_count} for a single VC.

    Lightweight helper used by the diagnostic snapshot and the Config card's
    "default voice channel" preview, so the operator can see the configured
    VC's live member count without populating the full picker list (which
    requires enumerating every VC in the guild).  Returns None on any
    failure (bot not running, guild/channel not found, not a voice channel,
    fetch error).
    """
    if _runner is None or not _runner.ready.is_set():
        return None
    try:
        gid = int(str(guild_id).strip())
        cid = int(str(channel_id).strip())
    except (TypeError, ValueError):
        return None
    async def _do():
        guild = _runner.bot.get_guild(gid)
        if guild is None:
            guild = await _runner.bot.fetch_guild(gid)
        channel = guild.get_channel(cid)
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            return None
        return {
            "id":           str(channel.id),
            "name":         channel.name,
            "member_count": len(channel.members),
        }
    try:
        fut = _runner.submit(_do())
        return fut.result(timeout=timeout)
    except Exception as exc:
        _log.info("bot_voice_channel_info(%s/%s) failed: %s", guild_id, channel_id, exc)
        return None


def bot_post_embed(channel_id: str, embed_dict: dict, *,
                   timeout: float = 8.0) -> str | None:
    """Post an embed to a Discord channel.  Returns the message ID on
    success (so callers can edit it later via `bot_edit_embed`), or
    None on failure."""
    if _runner is None or not _runner.ready.is_set():
        return None
    try:
        cid = int(str(channel_id).strip())
    except (TypeError, ValueError):
        return None
    async def _do():
        channel = _runner.bot.get_channel(cid)
        if channel is None:
            channel = await _runner.bot.fetch_channel(cid)
        embed = discord.Embed.from_dict(embed_dict)
        msg = await channel.send(embed=embed)
        return str(msg.id)
    try:
        fut = _runner.submit(_do())
        return fut.result(timeout=timeout)
    except Exception as exc:
        # v0.16.15 — classified logging (Forbidden vs NotFound vs HTTP vs
        # unexpected) so the operator knows whether to fix perms, re-pick
        # the channel, or just wait out a rate limit.
        _classify_discord_op_error("bot_post_embed", str(channel_id), exc)
        return None


def bot_edit_embed(channel_id: str, message_id: str, embed_dict: dict, *,
                   timeout: float = 12.0) -> bool:
    """Replace the embed on a previously-posted message.  Used by the
    live veto embed (Layer 1C) — one message per session, edited on
    each ban/pick.  Returns True on success."""
    if _runner is None or not _runner.ready.is_set():
        return False
    try:
        cid = int(str(channel_id).strip())
        mid = int(str(message_id).strip())
    except (TypeError, ValueError):
        return False
    async def _do():
        channel = _runner.bot.get_channel(cid)
        if channel is None:
            channel = await _runner.bot.fetch_channel(cid)
        msg = await channel.fetch_message(mid)
        embed = discord.Embed.from_dict(embed_dict)
        await msg.edit(embed=embed)
        return True
    try:
        fut = _runner.submit(_do())
        return bool(fut.result(timeout=timeout))
    except Exception as exc:
        # v0.16.15 — bumped timeout 8s → 12s above so discord.py's internal
        # rate-limit retry can complete on a fast-veto burst before our
        # outer timer fires.  Classified logging surfaces the actual cause
        # (Forbidden vs NotFound vs HTTP 429 vs network) instead of
        # everything looking like a generic "failed".
        _classify_discord_op_error(
            "bot_edit_embed", f"{channel_id}/{message_id}", exc)
        return False
