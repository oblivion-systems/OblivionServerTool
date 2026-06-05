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
    `bot_post_embed`, `bot_edit_embed`).
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

        @self.bot.event
        async def on_ready():
            self.ready.set()
            user = self.bot.user
            self.core.log(f"[discord] Bot connected as {user} (id={user.id})")

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
        _log.info("bot_dm_user(%s) failed: %s", discord_id, exc)
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
        _log.info("bot_post_embed(%s) failed: %s", channel_id, exc)
        return None


def bot_edit_embed(channel_id: str, message_id: str, embed_dict: dict, *,
                   timeout: float = 8.0) -> bool:
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
        _log.info("bot_edit_embed(%s/%s) failed: %s", channel_id, message_id, exc)
        return False
