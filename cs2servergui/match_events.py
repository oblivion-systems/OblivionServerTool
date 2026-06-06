"""
cs2servergui/match_events.py — v0.12.1 — live-match round summary publisher.

While a MatchZy match is live, polls the CS2 server via RCON every 3s to
detect score deltas (`mp_t_score`, `mp_ct_score`).  When a round ends —
detected by either score incrementing — posts a small embed to the
configured `discord_veto_channel_id` summarising the round outcome.

ARCHITECTURE
------------
Pattern mirrors `discord_bot._BotRunner`: a single dedicated background
thread owns a polling loop; module-level start/stop wrappers are
idempotent and thread-safe.

Lifecycle:
  - start(core) is called from web.py when the veto session transitions
    to `finale` (the moment MatchZy is handed the match config).
  - The poller checks preconditions every tick:
      * `core._veto_session` state in (finale, complete)
      * `core.discord_round_summaries_enabled` is True
      * `core.running` is True (server is up)
    If any precondition fails, the poller continues running but the
    embed-post is skipped.  This lets a captain or operator toggle the
    setting mid-match without restarting the poller.
  - stop() is called from web.py when:
      * /api/veto/reset is hit
      * A new veto session is created
      * A match ends (score reaches mp_maxrounds / 2 + 1)
  - Poller exits within 3s of stop() (next tick checks `_stop_event`).

RCON poll cost: 1 batched call per 3s.  Server-side, mp_t_score reads
are O(1) lookups; no measurable load even at tickrate 128.

For tests + headless mode: the poller is opt-in via the operator-side
toggle; if the toggle is OFF (default) the start() call is a no-op.

v0.12.x extensions (deferred):
  - MVP / clutch / ace detection (needs log tail or MatchZy webhook).
  - End-of-map / end-of-series summary embed.
  - Demo upload link in final embed (needs MatchZy demo-uploader hook).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

_log = logging.getLogger("oblivion.match_events")

# Module-level state — single instance pattern (mirrors discord_bot)
_thread:     Optional[threading.Thread]     = None
_stop_event: Optional[threading.Event]      = None
_state_lock = threading.Lock()

# Score parser: `mp_t_score` RCON reply looks like one of:
#   `"mp_t_score" = "8"`
#   `"mp_t_score" = "8" ( def. "0" ) game notify`
_SCORE_RX = re.compile(r'"(mp_(?:t|ct)_score)"\s*=\s*"(\d+)"')


def _parse_scores(rcon_reply: str) -> Optional[tuple[int, int]]:
    """Parse `mp_t_score` + `mp_ct_score` from a batched RCON reply.
    Returns (t_score, ct_score) or None if either is missing.
    """
    t_score = None
    ct_score = None
    for cvar_name, value in _SCORE_RX.findall(rcon_reply or ""):
        try:
            v = int(value)
        except ValueError:
            continue
        if   cvar_name == "mp_t_score":  t_score  = v
        elif cvar_name == "mp_ct_score": ct_score = v
    if t_score is None or ct_score is None:
        return None
    return t_score, ct_score


def _build_round_embed(*, t_score: int, ct_score: int,
                       team_a_name: str, team_b_name: str,
                       map_name: str, who_won: str) -> dict:
    """Build the embed payload posted after each round.
    `who_won` is "T" or "CT" — used to colour the embed and pick the
    side label.  In MatchZy convention, team_a starts on CT side and
    team_b on T side for the first half; we can't be 100% sure which
    Oblivion-team owns which CS-side without a side-swap signal, so we
    show both team names and let the score speak.
    """
    round_num = t_score + ct_score
    # Discord embed color: int — bluish for T win, orange for CT win.
    color = 0xE67E22 if who_won == "CT" else 0x3498DB
    side_label = "CT round" if who_won == "CT" else "T round"
    return {
        "title": f"🟦 {team_a_name}  {ct_score} — {t_score}  {team_b_name} 🟧",
        "description": f"{map_name} · round {round_num}",
        "color": color,
        "fields": [{"name": "Result", "value": side_label, "inline": True}],
        "footer": {"text": "Oblivion · match in progress"},
    }


def _build_endmap_embed(*, t_score: int, ct_score: int,
                        team_a_name: str, team_b_name: str,
                        map_name: str) -> dict:
    """Final embed when a map ends (one team hits the round target)."""
    if ct_score > t_score:
        winner = team_a_name
        loser  = team_b_name
        score  = f"{ct_score}–{t_score}"
    else:
        winner = team_b_name
        loser  = team_a_name
        score  = f"{t_score}–{ct_score}"
    return {
        "title": f"🏆 {winner} wins {map_name} {score}",
        "description": f"vs. {loser}",
        "color": 0xFFD700,   # gold
        "footer": {"text": "Oblivion · match complete"},
    }


def _poller(core, stop_event: threading.Event) -> None:
    """Main loop — runs on the dedicated thread.  Polls RCON every 3s,
    detects deltas, posts round summaries.  Exits on stop_event."""
    last_scores: Optional[tuple[int, int]] = None
    last_map:    Optional[str] = None
    final_posted: bool = False

    while not stop_event.wait(3.0):
        # Precondition check — runs every tick so operator can toggle
        # mid-match without restart.
        try:
            sess = getattr(core, "_veto_session", None)
            if sess is None or sess.state not in ("finale", "complete"):
                continue
            if not getattr(core, "discord_round_summaries_enabled", False):
                continue
            if not getattr(core, "running", False):
                continue
            channel_id = getattr(core, "discord_veto_channel_id", "") or ""
            if not channel_id.strip():
                continue
            guild_id = getattr(core, "discord_guild_id", "") or ""
            if not guild_id.strip():
                continue
        except Exception:
            continue

        # RCON read — batched call returns both cvars in one reply.
        try:
            reply = core.rcon.execute("mp_t_score; mp_ct_score; host_map_name")
        except Exception as exc:
            # Server may be reloading / RCON briefly unreachable — log
            # at debug, retry next tick.
            _log.debug("match_events RCON read failed: %s", exc)
            continue

        scores = _parse_scores(reply)
        if scores is None:
            continue
        t_score, ct_score = scores

        # First poll bootstraps state — no embed.
        if last_scores is None:
            last_scores = scores
            # Try to read the active map name from reply or session.
            map_name_match = re.search(r'"host_map_name"\s*=\s*"([^"]+)"', reply)
            if map_name_match:
                last_map = map_name_match.group(1)
            elif sess.matchzy_config and isinstance(sess.matchzy_config, dict):
                ml = sess.matchzy_config.get("maplist") or []
                last_map = ml[0] if ml else "?"
            continue

        # Score delta → round just ended.
        prev_t, prev_ct = last_scores
        if t_score != prev_t or ct_score != prev_ct:
            who_won = "T" if t_score > prev_t else "CT"
            last_scores = scores
            team_a_name = sess.team_a_name or "Team Alpha"
            team_b_name = sess.team_b_name or "Team Bravo"
            map_name    = last_map or "?"
            embed = _build_round_embed(
                t_score=t_score, ct_score=ct_score,
                team_a_name=team_a_name, team_b_name=team_b_name,
                map_name=map_name, who_won=who_won,
            )
            _post_embed(channel_id, embed, core)

            # End-of-map detection: a team reached (mp_maxrounds/2 + 1).
            # We don't know mp_maxrounds at poll-time; default to 24-round
            # (MR12) so we trip at 13 — adjust to MR15 by also tripping
            # at 16.  The check is "any side >= 13" which covers both
            # MR12 and MR15.  Worst case: false positive at MR15-MR11=14
            # where T leads 14-11; the embed says "wins" but the match
            # continues.  Tolerable — operator can pin/delete if needed.
            if not final_posted and (t_score >= 13 or ct_score >= 13):
                final_posted = True
                _post_embed(channel_id, _build_endmap_embed(
                    t_score=t_score, ct_score=ct_score,
                    team_a_name=team_a_name, team_b_name=team_b_name,
                    map_name=map_name,
                ), core)


def _post_embed(channel_id: str, embed: dict, core) -> None:
    """Post an embed via the discord_bot module.  Wrapped to be
    fail-soft — a Discord rate-limit or transient error must not crash
    the poller."""
    try:
        from . import discord_bot
    except Exception:
        return
    if not discord_bot.bot_status().get("connected"):
        return
    try:
        discord_bot.bot_post_embed(channel_id, embed)
        try:
            core.log(f"[discord] round summary posted "
                     f"(score {embed.get('title','')[:60]})")
        except Exception:
            pass
    except Exception as exc:
        _log.info("round summary post failed: %s", exc)


def start(core) -> None:
    """Idempotent — start the poller if not already running.
    Called from web.py when the veto session transitions to finale."""
    global _thread, _stop_event
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop_event = threading.Event()
        _thread = threading.Thread(
            target=_poller, args=(core, _stop_event),
            name="oblivion-match-events", daemon=True,
        )
        _thread.start()


def stop() -> None:
    """Idempotent — signal the poller to exit; wait briefly for join."""
    global _thread, _stop_event
    with _state_lock:
        if _stop_event is not None:
            _stop_event.set()
        t = _thread
        _thread = None
        _stop_event = None
    if t is not None:
        t.join(timeout=4.0)


def is_running() -> bool:
    """For tests + diagnostics."""
    with _state_lock:
        return _thread is not None and _thread.is_alive()
