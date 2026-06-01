"""
veto.py — VetoSession state machine for the v0.10.0 map-veto feature.

A guided five-stage match-setup flow.  The browser is NOT the source of truth;
this module owns the session state and the SPA mirrors it.  Web routes in
`web.py` are thin wrappers over the methods here; the SSE log-stream pattern
pushes state changes to both captains in real time (Layer 0 plan in VETO.md).

State machine
─────────────
    idle
      │ create_session(mode, map_pool)
      ▼
    roster                 — operator fills 10 (name, steam_id) entries
      │ distribute_teams()
      ▼
    teams                  — randomly split into A/B of 5 (reshuffle allowed)
      │ start_voting()
      ▼
    voting                 — each player votes 5× within their team
      │ all votes in → resolve_captains() → tie? revote : ↓
      ▼
    links                  — generate scoped, single-use captain tokens
      │ both tokens claimed
      ▼
    veto                   — captains alternate BAN/PICK per the sequence
      │ all steps complete → identify decider → ↓
      ▼
    finale                 — "Get Ready to Battle" + MatchZy config generation
      │ matchzy_loadmatch acknowledged
      ▼
    complete

`reset()` returns to `idle` from any state.

Thread safety
─────────────
Every public method is short and pure-Python data mutation.  AppCore holds
a `_veto_lock: threading.Lock` and serialises calls; callers should NOT mutate
fields directly — go through the methods so the lock is held.

Captain tokens
──────────────
Two tokens (one per team) issued at the `links` stage.  Each is a
`secrets.token_urlsafe(32)` string — ~256 bits of entropy.  Single-use: the
first valid `claim_captain(token)` call binds it to that captain seat and
subsequent attempts with the same token are rejected.  Token can be revoked
via `revoke_token(team)` to issue a fresh one (e.g. captain lost the link).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Iterable


# ─── Veto sequences per BO format ─────────────────────────────────────────
# Each entry: ("BAN" | "PICK", team_actor)
#   - team_actor = "A" or "B": that team's captain performs the action
#   - decider is computed as the last map remaining; not in the sequence
#
# Source: standard competitive map-veto sequences (the prototype matches).
_VETO_SEQUENCES: dict[str, list[tuple[str, str]]] = {
    "BO1": [
        ("BAN",  "A"), ("BAN",  "B"),
        ("BAN",  "A"), ("BAN",  "B"),
        ("BAN",  "A"), ("BAN",  "B"),
        # 7th map = decider
    ],
    "BO3": [
        ("BAN",  "A"), ("BAN",  "B"),
        ("PICK", "A"), ("PICK", "B"),
        ("BAN",  "A"), ("BAN",  "B"),
        # 7th map = decider (3rd map of the series)
    ],
    "BO5": [
        ("BAN",  "A"), ("BAN",  "B"),
        ("PICK", "A"), ("PICK", "B"),
        ("PICK", "A"), ("PICK", "B"),
        # 7th map = decider (5th map of the series)
    ],
}


# ─── Data model ───────────────────────────────────────────────────────────
@dataclass
class RosterPlayer:
    """One slot in the 10-player roster.

    `steam_id` is collected at roster time to enable MatchZy's strict team
    assignment in the finale (per the v0.10.0 spec decision).  Optional for
    now to let the operator skip in informal scenarios; a future flag could
    enforce non-empty for strict mode.
    """
    name: str
    steam_id: str = ""

    def __post_init__(self) -> None:
        # Defensive: strip whitespace at the boundary so equality / display
        # don't depend on trailing spaces from copy-paste rosters.
        self.name = self.name.strip()
        self.steam_id = self.steam_id.strip()


@dataclass
class VetoStep:
    """One slot in the veto sequence.

    `map_id` is filled when the captain commits the action.  None until then.
    For BAN steps the map is removed from the pool; for PICK it's added to
    `picked_maps` in order.  The decider is computed (not stored as a step)
    once all sequence steps complete.
    """
    kind: str          # "BAN" or "PICK"
    team:  str         # "A" or "B"
    map_id: str = ""


@dataclass
class CaptainToken:
    """Scoped, single-use credential for a captain's web link."""
    team:       str                              # "A" or "B"
    value:      str                              # the actual token string
    issued_at:  float                            # time.time()
    claimed_by: str = ""                         # captured at first valid use
    used:       bool = False


# Allowed state transitions, declared up-front for cheap legal-move checks.
# Maps current state -> set of legal next states.
_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "idle":     frozenset({"roster"}),
    "roster":   frozenset({"teams", "idle"}),
    "teams":    frozenset({"teams", "roster", "voting", "idle"}),  # reshuffle = teams→teams; "Edit roster" backs to roster
    "voting":   frozenset({"voting", "links", "idle"}),    # tie → revote re-enters voting; idle on reset
    "links":    frozenset({"veto", "links", "idle"}),      # links state re-entered if a token is revoked
    "veto":     frozenset({"veto", "finale", "idle"}),     # each ban/pick stays in veto until last step
    "finale":   frozenset({"complete", "idle"}),
    "complete": frozenset({"idle"}),
}


@dataclass
class VetoSession:
    """The whole match-setup session.  One active session at a time per AppCore."""

    state: str = "idle"

    # Stage 0 — Roster
    team_a_name: str = "Team Alpha"
    team_b_name: str = "Team Bravo"
    roster:      list[RosterPlayer] = field(default_factory=list)

    # Stage 1 — Teams (after distribute)
    team_a: list[RosterPlayer] = field(default_factory=list)
    team_b: list[RosterPlayer] = field(default_factory=list)

    # Stage 2 — Captain votes
    # Each dict maps voter_index -> votee_index (both indices into team_a / team_b).
    votes_a:        dict[int, int] = field(default_factory=dict)
    votes_b:        dict[int, int] = field(default_factory=dict)
    captain_a_idx:  int | None     = None    # set after resolve_captains()
    captain_b_idx:  int | None     = None
    revote_count:   int            = 0       # how many ties triggered a revote so far

    # Stage 3 — Links
    tokens: dict[str, CaptainToken] = field(default_factory=dict)   # "A" / "B" → CaptainToken

    # Stage 4 — Veto
    mode:          str           = "BO3"     # "BO1" | "BO3" | "BO5"
    map_pool:      list[str]     = field(default_factory=list)
    sequence:      list[VetoStep] = field(default_factory=list)
    current_step:  int           = 0
    decider:       str           = ""        # the map that remains; filled when veto completes

    # Stage 5 — Result
    final_maps:    list[str]    = field(default_factory=list)   # ordered list MatchZy will load
    matchzy_config: dict | None  = None                          # generated at finale

    # Audit
    created_at:  float = field(default_factory=time.time)
    updated_at:  float = field(default_factory=time.time)

    # ── State-transition helper ───────────────────────────────────────
    def _transition(self, to_state: str) -> None:
        """Raise on illegal transition; otherwise commit + touch `updated_at`.

        Internal helper.  Every public method that changes state goes through
        here so we get a single audit point and a single legal-move enforcer.
        """
        if to_state not in _LEGAL_TRANSITIONS.get(self.state, frozenset()):
            raise InvalidVetoTransition(
                f"Cannot transition {self.state} → {to_state} "
                f"(legal next states: {sorted(_LEGAL_TRANSITIONS.get(self.state, frozenset()))})"
            )
        self.state = to_state
        self.updated_at = time.time()


# ─── Errors ───────────────────────────────────────────────────────────────
class VetoError(Exception):
    """Base class for veto-session errors — separated out so web.py can
    return 400 on any of them without leaking other exception types."""


class InvalidVetoTransition(VetoError):
    """State-machine refused the requested transition."""


class VetoStageError(VetoError):
    """Stage-specific validation failure (incomplete roster, wrong turn, etc.)."""


# ─── Session operations (the API web.py wraps) ────────────────────────────
def create_session(
    mode: str = "BO3",
    map_pool: Iterable[str] | None = None,
) -> VetoSession:
    """Construct a fresh session in the `roster` state.

    `mode` must be one of BO1/BO3/BO5.  `map_pool` defaults to the seven
    `config.ACTIVE_DUTY_POOL` maps; pass a 7-entry list to override (per the
    per-veto override decision from VETO.md).  Validates pool size matches
    what the veto sequence expects.
    """
    if mode not in _VETO_SEQUENCES:
        raise VetoError(f"Invalid mode {mode!r} — must be one of "
                        f"{sorted(_VETO_SEQUENCES.keys())}")
    # Default to active-duty pool; import inside the function to avoid a
    # module-level dependency cycle if config ever ends up importing veto.
    from .config import ACTIVE_DUTY_POOL
    pool = list(map_pool) if map_pool is not None else list(ACTIVE_DUTY_POOL)
    if len(pool) != 7:
        raise VetoError(f"Map pool must have exactly 7 entries (got {len(pool)})")
    if len(set(pool)) != 7:
        raise VetoError("Map pool must not contain duplicate entries")

    s = VetoSession(mode=mode, map_pool=pool)
    s._transition("roster")
    return s


def set_roster(
    session: VetoSession,
    team_a_name: str,
    team_b_name: str,
    players: list[RosterPlayer],
) -> None:
    """Save the 10-player roster.  Must be in `roster` state."""
    if session.state != "roster":
        raise InvalidVetoTransition(f"Roster can only be set in `roster` state (now {session.state})")
    if len(players) != 10:
        raise VetoStageError(f"Roster must have exactly 10 players (got {len(players)})")
    names = [p.name for p in players if p.name]
    if len(names) != 10:
        raise VetoStageError("Every roster slot must have a non-empty name")
    if len(set(names)) != 10:
        raise VetoStageError("Roster names must be unique")
    session.team_a_name = team_a_name.strip() or "Team Alpha"
    session.team_b_name = team_b_name.strip() or "Team Bravo"
    session.roster = list(players)
    session.updated_at = time.time()


def distribute_teams(session: VetoSession, rng: secrets.SystemRandom | None = None) -> None:
    """Randomly split the 10-player roster into two teams of 5.

    Crypto-RNG by default so a re-shuffle isn't seedable by an observer.
    Caller passes `rng` for deterministic tests.
    """
    if session.state == "teams":
        # Reshuffle is a self-transition on `teams`; legal per our table.
        session._transition("teams")
    elif session.state == "roster":
        if len(session.roster) != 10:
            raise VetoStageError("Cannot distribute — roster has fewer than 10 players")
        session._transition("teams")
    else:
        raise InvalidVetoTransition(
            f"distribute_teams legal only from roster or teams (now {session.state})"
        )
    r = rng or secrets.SystemRandom()
    pool = list(session.roster)
    r.shuffle(pool)
    session.team_a = pool[:5]
    session.team_b = pool[5:]
    # A reshuffle invalidates any previously cast votes.
    session.votes_a.clear()
    session.votes_b.clear()
    session.captain_a_idx = None
    session.captain_b_idx = None
    session.revote_count = 0


def start_voting(session: VetoSession) -> None:
    """Move from `teams` → `voting`.  Operator-driven."""
    if session.state != "teams":
        raise InvalidVetoTransition(f"start_voting legal only from teams (now {session.state})")
    if len(session.team_a) != 5 or len(session.team_b) != 5:
        raise VetoStageError("Both teams must have exactly 5 players before voting")
    session.votes_a.clear()
    session.votes_b.clear()
    session._transition("voting")


def cast_vote(session: VetoSession, team: str, voter_idx: int, votee_idx: int) -> None:
    """Record one captain vote.  `team` = 'A' or 'B'.

    A player can change their vote (votes_x[voter] is overwritten on re-cast).
    Voting only legal in `voting` state.  Indices must be in [0..4].
    """
    if session.state != "voting":
        raise InvalidVetoTransition(f"cast_vote legal only in voting state (now {session.state})")
    if team not in ("A", "B"):
        raise VetoStageError(f"team must be 'A' or 'B' (got {team!r})")
    if not (0 <= voter_idx < 5):
        raise VetoStageError(f"voter_idx must be 0..4 (got {voter_idx})")
    if not (0 <= votee_idx < 5):
        raise VetoStageError(f"votee_idx must be 0..4 (got {votee_idx})")
    target = session.votes_a if team == "A" else session.votes_b
    target[voter_idx] = votee_idx
    session.updated_at = time.time()


def voting_complete(session: VetoSession) -> bool:
    """True if every player on both teams has cast a vote."""
    return (session.state == "voting"
            and len(session.votes_a) == 5
            and len(session.votes_b) == 5)


def _tally(votes: dict[int, int]) -> tuple[int, list[int]]:
    """Return (max_count, [tied_indices]).  Pure helper for resolve_captains."""
    counts: dict[int, int] = {}
    for votee in votes.values():
        counts[votee] = counts.get(votee, 0) + 1
    if not counts:
        return 0, []
    top = max(counts.values())
    tied = sorted(k for k, v in counts.items() if v == top)
    return top, tied


def resolve_captains(session: VetoSession) -> str:
    """Tally both teams' votes and either elect captains (→ links state) OR
    trigger a revote (state stays in `voting`, votes cleared on the tied team).

    Returns one of:
      - 'elected'        — both captains set; state advanced to `links`
      - 'revote_a'       — team A had a tie; team A's votes cleared, retry
      - 'revote_b'       — team B had a tie
      - 'revote_both'    — both teams tied
    """
    if not voting_complete(session):
        raise VetoStageError("Cannot resolve captains — not all votes are in")

    top_a, tied_a = _tally(session.votes_a)
    top_b, tied_b = _tally(session.votes_b)
    a_tied = len(tied_a) > 1
    b_tied = len(tied_b) > 1

    if not a_tied and not b_tied:
        session.captain_a_idx = tied_a[0]
        session.captain_b_idx = tied_b[0]
        session._transition("links")
        return "elected"

    # Reset whichever side(s) tied.  The OTHER side keeps their result so the
    # operator doesn't get yelled at by the side that already settled it.
    if a_tied:
        session.votes_a.clear()
    if b_tied:
        session.votes_b.clear()
    session.revote_count += 1
    session.updated_at = time.time()

    if a_tied and b_tied:
        return "revote_both"
    if a_tied:
        return "revote_a"
    return "revote_b"


def issue_tokens(session: VetoSession) -> dict[str, str]:
    """Mint scoped single-use tokens for both captains.  Returns the raw
    values keyed by team.  Caller delivers them to the captains via copy /
    QR / DM (Layer 1).  Must be in `links` state.
    """
    if session.state != "links":
        raise InvalidVetoTransition(f"issue_tokens legal only in links (now {session.state})")
    now = time.time()
    session.tokens = {
        "A": CaptainToken("A", secrets.token_urlsafe(32), now),
        "B": CaptainToken("B", secrets.token_urlsafe(32), now),
    }
    session.updated_at = now
    return {"A": session.tokens["A"].value, "B": session.tokens["B"].value}


def claim_captain(session: VetoSession, token_value: str, caller_id: str = "") -> str:
    """Validate `token_value`; bind it to the caller; advance state when both
    captains have claimed.  Returns the team letter the token belongs to.

    Raises on: wrong state, unknown token, already-used token.  `caller_id`
    is recorded for audit (could be an IP, a SteamID, a Discord ID — caller
    decides).
    """
    if session.state not in ("links", "veto"):
        raise InvalidVetoTransition(
            f"claim_captain legal only in links/veto (now {session.state})"
        )
    match = None
    for team, tok in session.tokens.items():
        if secrets.compare_digest(tok.value, token_value):
            match = (team, tok)
            break
    if match is None:
        raise VetoStageError("Unknown captain token")
    team, tok = match
    if tok.used:
        # Same caller re-opening their link is OK and idempotent
        if caller_id and tok.claimed_by == caller_id:
            return team
        raise VetoStageError("Captain token already claimed")
    tok.used = True
    tok.claimed_by = caller_id
    session.updated_at = time.time()
    # Once both tokens are used, build the sequence and enter `veto` state.
    if (all(t.used for t in session.tokens.values())
            and session.state == "links"
            and not session.sequence):
        _build_sequence(session)
        session._transition("veto")
    return team


def revoke_token(session: VetoSession, team: str) -> str:
    """Re-issue a fresh single-use token for `team` (operator action when a
    captain loses or shares their link)."""
    if session.state not in ("links", "veto"):
        raise InvalidVetoTransition(f"revoke_token legal only in links/veto (now {session.state})")
    if team not in ("A", "B"):
        raise VetoStageError(f"team must be 'A' or 'B' (got {team!r})")
    new_tok = CaptainToken(team, secrets.token_urlsafe(32), time.time())
    session.tokens[team] = new_tok
    if session.state == "veto":
        # Drop back to `links` so the captain has to re-claim before vetoing.
        session.state = "links"
    session.updated_at = time.time()
    return new_tok.value


def _build_sequence(session: VetoSession) -> None:
    """Build the VetoStep list from the mode's template + the current pool."""
    template = _VETO_SEQUENCES[session.mode]
    session.sequence = [VetoStep(kind=k, team=t) for (k, t) in template]
    session.current_step = 0
    session.final_maps = []
    session.decider = ""


def current_step(session: VetoSession) -> VetoStep | None:
    """The next step to be acted on, or None if veto is complete."""
    if session.state != "veto":
        return None
    if session.current_step >= len(session.sequence):
        return None
    return session.sequence[session.current_step]


def remaining_maps(session: VetoSession) -> list[str]:
    """Maps still in the pool after the bans / picks so far.

    Bans REMOVE from the pool; picks DO NOT remove (the picked map remains
    in `final_maps` and can still appear as a decider candidate at the end —
    standard veto rules, though if your sequence calls all picks before all
    bans, the decider logic still works).
    """
    used = {step.map_id for step in session.sequence[:session.current_step]
            if step.map_id and step.kind == "BAN"}
    return [m for m in session.map_pool if m not in used]


def perform_step(session: VetoSession, team: str, map_id: str) -> None:
    """`team` performs the current step on `map_id`.  Validates: state is
    `veto`, it's that team's turn, and the map is still legally available.

    On the last step, identifies the decider (the single remaining map) and
    transitions to `finale`.
    """
    if session.state != "veto":
        raise InvalidVetoTransition(f"perform_step legal only in veto (now {session.state})")
    step = current_step(session)
    if step is None:
        raise VetoStageError("Veto already complete — no current step")
    if step.team != team:
        raise VetoStageError(f"Not team {team!r}'s turn — current step is team {step.team!r}")
    # The map must still be available (not banned earlier).  Picks don't
    # remove from the pool but you can't pick a map already banned.
    legal = remaining_maps(session)
    if step.kind == "PICK":
        # PICK can't repeat a previous pick either.
        already_picked = {s.map_id for s in session.sequence[:session.current_step]
                          if s.kind == "PICK" and s.map_id}
        legal = [m for m in legal if m not in already_picked]
    if map_id not in legal:
        raise VetoStageError(f"Map {map_id!r} is not in the legal-move set {legal}")
    step.map_id = map_id
    if step.kind == "PICK":
        session.final_maps.append(map_id)
    session.current_step += 1
    session.updated_at = time.time()
    # Last step done?
    if session.current_step >= len(session.sequence):
        # Decider = the only map left after all bans (regardless of which
        # picks have already filled `final_maps`).  For BO1 this is the
        # single match map; for BO3/BO5 it's the deciding map.
        leftover = remaining_maps(session)
        # Picks don't remove from the pool but they're already in final_maps;
        # the decider is whichever map is in `leftover` but NOT already picked.
        picked = set(session.final_maps)
        decider_candidates = [m for m in leftover if m not in picked]
        if len(decider_candidates) != 1:
            # Defensive: shouldn't happen for well-formed sequences.
            raise VetoError(
                f"Decider resolution failed — expected 1 candidate, got "
                f"{decider_candidates!r}.  Sequence: {session.sequence}"
            )
        session.decider = decider_candidates[0]
        session.final_maps.append(session.decider)
        session._transition("finale")


def build_matchzy_config(session: VetoSession) -> dict:
    """Generate a MatchZy match-config dict from the completed veto.

    Output shape mirrors MatchZy's `match.json` schema (matchid, maplist,
    num_maps, players_per_team, team1, team2, etc.).  Caller writes this
    to disk or pipes via RCON `matchzy_loadmatch` to hand the series over
    to MatchZy at the finale.
    """
    if session.state not in ("finale", "complete"):
        raise InvalidVetoTransition(
            f"build_matchzy_config legal only in finale/complete (now {session.state})"
        )

    def team_players(team_list: list[RosterPlayer]) -> dict[str, str]:
        # MatchZy wants {steamid: display_name} — players with no SteamID
        # are omitted (they can still join in loose mode and pick a side).
        return {p.steam_id: p.name for p in team_list if p.steam_id}

    cfg = {
        "matchid":           f"oblivion-veto-{int(session.created_at)}",
        "num_maps":          len(session.final_maps),
        "maplist":           list(session.final_maps),
        "players_per_team":  5,
        "team1": {
            "name":    session.team_a_name,
            "players": team_players(session.team_a),
        },
        "team2": {
            "name":    session.team_b_name,
            "players": team_players(session.team_b),
        },
        "cvars": {
            # Conservative defaults — operator can override post-hoc.
            "mp_warmup_pausetimer": 0,
            "matchzy_minimum_ready_required": "2",
        },
        # Decider + bans/picks audit trail; useful for the SPA finale page.
        "_oblivion_meta": {
            "mode":     session.mode,
            "decider":  session.decider,
            "vetoes":   [
                {"team": s.team, "kind": s.kind, "map": s.map_id}
                for s in session.sequence
            ],
        },
    }
    session.matchzy_config = cfg
    return cfg


def complete(session: VetoSession) -> None:
    """Mark the session complete (MatchZy has been handed the config).  After
    this the operator typically calls `reset()` to allow a new session."""
    if session.state != "finale":
        raise InvalidVetoTransition(f"complete legal only from finale (now {session.state})")
    session._transition("complete")


def reset(session: VetoSession) -> None:
    """Return the session to `idle` and clear all per-session state.  Legal
    from any state — the operator can abort at any time."""
    session.state = "idle"
    session.team_a_name = "Team Alpha"
    session.team_b_name = "Team Bravo"
    session.roster.clear()
    session.team_a.clear()
    session.team_b.clear()
    session.votes_a.clear()
    session.votes_b.clear()
    session.captain_a_idx = None
    session.captain_b_idx = None
    session.revote_count = 0
    session.tokens.clear()
    session.map_pool.clear()
    session.sequence.clear()
    session.current_step = 0
    session.decider = ""
    session.final_maps.clear()
    session.matchzy_config = None
    session.updated_at = time.time()
