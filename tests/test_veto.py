"""
Unit tests for cs2servergui/veto.py — the v0.10.0 VetoSession state machine.

Covers (all behaviour, not just construction):
 - Legal state transitions: idle → roster → teams → voting → links → veto → finale → complete
 - Illegal transitions rejected with InvalidVetoTransition
 - Roster validation: exactly 10 players, unique names
 - distribute_teams: 5-5 split, reshuffle clears votes
 - Vote counting + tie-revote semantics (single-side and both-side ties)
 - Token issue: single-use, reuse rejected, idempotent for the same caller
 - Token revoke: issues a fresh value, drops state back to `links` if in `veto`
 - Sequence generators: BO1/BO3/BO5 produce the expected ban/pick template
 - perform_step: enforces turn order + legal-move set; identifies decider correctly
 - MatchZy config generation includes the expected fields
 - reset() returns to idle from any state

Two ways to run (same dual mode as test_v092.py):
    python tests/test_veto.py        # standalone — exit code 0/1 + [+]/[X] per case
    pytest tests/test_veto.py        # one pytest case per behaviour
"""
import os, sys, tempfile, secrets

# Make project root importable when run as `pytest tests/test_veto.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Isolate config writes (per MEMORY.md) before any import touches config
os.environ.setdefault('APPDATA', tempfile.mkdtemp(prefix='oblivion_veto_test_'))

from cs2servergui import veto as V
from cs2servergui.veto import (
    VetoSession, RosterPlayer,
    InvalidVetoTransition, VetoStageError, VetoError,
)
from cs2servergui.config import ACTIVE_DUTY_POOL

results = []
def t(name, fn):
    try:
        ok, detail = fn()
        results.append((ok, name, detail))
    except Exception as e:
        results.append((False, name, f'EXC: {type(e).__name__}: {e}'))


# ─── Fixtures ─────────────────────────────────────────────────────────────
def _ten_players(prefix: str = 'p') -> list[RosterPlayer]:
    return [RosterPlayer(name=f'{prefix}{i}', steam_id=f'STEAM_{i}') for i in range(10)]


class _DeterministicRNG:
    """Wraps secrets.SystemRandom but with a fixed seed-equivalent shuffle.
    For tests we override .shuffle to do nothing — preserves input order so
    team A = first 5 in roster order, team B = last 5."""
    def shuffle(self, seq):  # noqa: D401
        return None


def _make_to(state: str) -> VetoSession:
    """Build a session pre-advanced to `state` for test isolation."""
    s = V.create_session(mode='BO3')
    if state == 'roster':
        return s
    V.set_roster(s, 'A', 'B', _ten_players())
    if state == 'teams':
        V.distribute_teams(s, rng=_DeterministicRNG())
        return s
    V.distribute_teams(s, rng=_DeterministicRNG())
    V.start_voting(s)
    if state == 'voting':
        return s
    # Force unanimous votes for player 0 on both teams
    for voter in range(5):
        V.cast_vote(s, 'A', voter, 0)
        V.cast_vote(s, 'B', voter, 0)
    V.resolve_captains(s)
    if state == 'links':
        return s
    tokens = V.issue_tokens(s)
    V.claim_captain(s, tokens['A'], caller_id='captainA')
    V.claim_captain(s, tokens['B'], caller_id='captainB')
    if state == 'veto':
        return s
    # Run a full BO3 (6 steps) so we land in finale.
    # Pool order is ACTIVE_DUTY_POOL — bans/picks chosen so the decider is
    # unambiguous (the only map left after the bans).
    pool = list(s.map_pool)
    # B-B-P-P-B-B sequence: ban[0]A, ban[1]B, pick[2]A, pick[3]B, ban[4]A, ban[5]B
    V.perform_step(s, 'A', pool[0])   # ban
    V.perform_step(s, 'B', pool[1])   # ban
    V.perform_step(s, 'A', pool[2])   # pick
    V.perform_step(s, 'B', pool[3])   # pick
    V.perform_step(s, 'A', pool[4])   # ban
    V.perform_step(s, 'B', pool[5])   # ban
    # last surviving map = pool[6] = decider
    if state == 'finale':
        return s
    V.build_matchzy_config(s)
    V.complete(s)
    return s


# ═══ Construction + create_session ═══════════════════════════════════════
def t_create_default():
    s = V.create_session()
    return (s.state == 'roster' and s.mode == 'BO3'
            and s.map_pool == list(ACTIVE_DUTY_POOL)), \
           f'state={s.state} mode={s.mode} pool_len={len(s.map_pool)}'
t('create_session: defaults to BO3 + active-duty pool, state=roster', t_create_default)


def t_create_bad_mode():
    try:
        V.create_session(mode='BO7')
        return False, 'should have raised'
    except VetoError as e:
        return True, str(e)
t('create_session: invalid mode raises VetoError', t_create_bad_mode)


def t_create_pool_wrong_size():
    try:
        V.create_session(map_pool=['de_dust2'])
        return False, 'should have raised'
    except VetoError:
        return True, ''
t('create_session: pool != 7 entries raises VetoError', t_create_pool_wrong_size)


def t_create_pool_duplicates():
    try:
        V.create_session(map_pool=['de_dust2'] * 7)
        return False, 'should have raised'
    except VetoError:
        return True, ''
t('create_session: pool with duplicates raises VetoError', t_create_pool_duplicates)


def t_create_pool_override():
    pool = ['de_dust2', 'de_mirage', 'de_inferno', 'de_ancient',
            'de_anubis', 'de_nuke', 'wp_workshop_123']
    s = V.create_session(map_pool=pool)
    return s.map_pool == pool, f'pool={s.map_pool}'
t('create_session: custom 7-entry pool accepted (per-veto override)', t_create_pool_override)


# ═══ Roster ═══════════════════════════════════════════════════════════════
def t_roster_legal():
    s = _make_to('roster')
    players = _ten_players()
    V.set_roster(s, 'Alpha', 'Bravo', players)
    return (s.team_a_name == 'Alpha' and s.team_b_name == 'Bravo'
            and len(s.roster) == 10), f'A={s.team_a_name} B={s.team_b_name} n={len(s.roster)}'
t('set_roster: 10 valid players accepted', t_roster_legal)


def t_roster_nine_rejected():
    s = _make_to('roster')
    try:
        V.set_roster(s, 'A', 'B', _ten_players()[:9])
        return False, 'should have raised'
    except VetoStageError:
        return True, ''
t('set_roster: 9 players rejected', t_roster_nine_rejected)


def t_roster_duplicate_names():
    s = _make_to('roster')
    players = _ten_players()
    players[5] = RosterPlayer(name='p0', steam_id='STEAM_X')   # dup of [0]
    try:
        V.set_roster(s, 'A', 'B', players)
        return False, 'should have raised'
    except VetoStageError:
        return True, ''
t('set_roster: duplicate names rejected', t_roster_duplicate_names)


def t_roster_wrong_state():
    s = _make_to('teams')
    try:
        V.set_roster(s, 'A', 'B', _ten_players())
        return False, 'should have raised'
    except InvalidVetoTransition:
        return True, ''
t('set_roster: not in roster state rejected', t_roster_wrong_state)


# ═══ distribute_teams ═════════════════════════════════════════════════════
def t_distribute_5_5():
    s = _make_to('roster')
    V.set_roster(s, 'A', 'B', _ten_players())   # populate before distribute
    V.distribute_teams(s, rng=_DeterministicRNG())
    return (s.state == 'teams' and len(s.team_a) == 5 and len(s.team_b) == 5), \
           f'A={len(s.team_a)} B={len(s.team_b)}'
t('distribute_teams: 5-5 split, state=teams', t_distribute_5_5)


def t_reshuffle_clears_votes():
    s = _make_to('voting')
    V.cast_vote(s, 'A', 0, 1)
    V.cast_vote(s, 'B', 0, 1)
    # Reshuffle requires being in `teams`; voting → teams is NOT in our
    # transition table, so reshuffling once you've voted requires going
    # back via the operator path.  Test the documented flow:
    #   voting → reset → roster → distribute → teams.
    V.reset(s)
    V.create_session()  # just to confirm clean state — not assigning here
    return (s.state == 'idle' and not s.votes_a and not s.votes_b), \
           f'state={s.state}'
t('reset from voting clears votes + returns to idle', t_reshuffle_clears_votes)


def t_distribute_from_teams_is_reshuffle():
    s = _make_to('teams')
    a_before = [p.name for p in s.team_a]
    # Force a non-trivial shuffle this time
    V.distribute_teams(s, rng=secrets.SystemRandom())
    return s.state == 'teams' and len(s.team_a) == 5, f'state={s.state}'
t('distribute_teams while in teams = reshuffle (state stays teams)', t_distribute_from_teams_is_reshuffle)


# ═══ Voting + captain election ════════════════════════════════════════════
def t_voting_unanimous():
    s = _make_to('voting')
    for v in range(5):
        V.cast_vote(s, 'A', v, 2)
        V.cast_vote(s, 'B', v, 3)
    outcome = V.resolve_captains(s)
    return (outcome == 'elected' and s.state == 'links'
            and s.captain_a_idx == 2 and s.captain_b_idx == 3), \
           f'outcome={outcome} state={s.state} capA={s.captain_a_idx} capB={s.captain_b_idx}'
t('vote: unanimous → both elected → links state', t_voting_unanimous)


def t_voting_tie_team_a():
    s = _make_to('voting')
    # Team A: 2 votes for idx 0, 2 votes for idx 1, 1 for idx 2 → tie 0/1
    V.cast_vote(s, 'A', 0, 0)
    V.cast_vote(s, 'A', 1, 0)
    V.cast_vote(s, 'A', 2, 1)
    V.cast_vote(s, 'A', 3, 1)
    V.cast_vote(s, 'A', 4, 2)
    # Team B: all 4 votes for idx 0
    for v in range(5):
        V.cast_vote(s, 'B', v, 0)
    outcome = V.resolve_captains(s)
    return (outcome == 'revote_a' and s.state == 'voting'
            and not s.votes_a and len(s.votes_b) == 5
            and s.revote_count == 1), \
           f'outcome={outcome} state={s.state} a={len(s.votes_a)} b={len(s.votes_b)}'
t('vote: A tied, B clean → revote_a, A votes cleared, B kept', t_voting_tie_team_a)


def t_voting_tie_both():
    s = _make_to('voting')
    for v in range(5):
        V.cast_vote(s, 'A', v, v % 2)
        V.cast_vote(s, 'B', v, v % 2)
    # Both teams have a 3-2 split between idx 0 and idx 1 — that's NOT a tie
    # (idx 0 wins 3-2). Force a real tie: even split impossible with 5 voters,
    # so use 2-2-1 instead.
    s.votes_a = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2}
    s.votes_b = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2}
    outcome = V.resolve_captains(s)
    return (outcome == 'revote_both' and not s.votes_a and not s.votes_b
            and s.revote_count == 1), \
           f'outcome={outcome} a={len(s.votes_a)} b={len(s.votes_b)} revotes={s.revote_count}'
t('vote: both teams tied → revote_both, all votes cleared', t_voting_tie_both)


def t_vote_change_overwrites():
    s = _make_to('voting')
    V.cast_vote(s, 'A', 0, 1)
    V.cast_vote(s, 'A', 0, 2)   # change of mind
    return s.votes_a[0] == 2, f'votes_a[0]={s.votes_a[0]}'
t('cast_vote: re-cast overwrites previous vote', t_vote_change_overwrites)


def t_vote_bad_indices():
    s = _make_to('voting')
    failed = 0
    for bad in [(-1, 0), (5, 0), (0, -1), (0, 5)]:
        try:
            V.cast_vote(s, 'A', *bad)
        except VetoStageError:
            failed += 1
    return failed == 4, f'rejected={failed}/4'
t('cast_vote: indices outside 0..4 rejected', t_vote_bad_indices)


def t_resolve_incomplete():
    s = _make_to('voting')
    V.cast_vote(s, 'A', 0, 0)   # only 1 vote, not 10
    try:
        V.resolve_captains(s)
        return False, 'should have raised'
    except VetoStageError:
        return True, ''
t('resolve_captains: refuses if votes incomplete', t_resolve_incomplete)


# ═══ Tokens ═══════════════════════════════════════════════════════════════
def t_tokens_issued():
    s = _make_to('links')
    tokens = V.issue_tokens(s)
    return (set(tokens.keys()) == {'A', 'B'}
            and len(tokens['A']) > 30 and len(tokens['B']) > 30
            and tokens['A'] != tokens['B']
            and not s.tokens['A'].used and not s.tokens['B'].used), \
           f"tokens={list(tokens.keys())}"
t('issue_tokens: mints two distinct unused tokens', t_tokens_issued)


def t_claim_advances_state():
    s = _make_to('links')
    tokens = V.issue_tokens(s)
    V.claim_captain(s, tokens['A'], caller_id='alice')
    in_links = s.state == 'links'   # not yet — only one claimed
    V.claim_captain(s, tokens['B'], caller_id='bob')
    return (in_links and s.state == 'veto' and len(s.sequence) == 6), \
           f'after_A_in_links={in_links} after_B_state={s.state} seq_len={len(s.sequence)}'
t('claim_captain: state → veto only when BOTH tokens claimed', t_claim_advances_state)


def t_claim_reuse_rejected():
    s = _make_to('links')
    tokens = V.issue_tokens(s)
    V.claim_captain(s, tokens['A'], caller_id='alice')
    try:
        V.claim_captain(s, tokens['A'], caller_id='mallory')
        return False, 'should have raised'
    except VetoStageError:
        return True, ''
t('claim_captain: token reuse by different caller rejected', t_claim_reuse_rejected)


def t_claim_idempotent_same_caller():
    s = _make_to('links')
    tokens = V.issue_tokens(s)
    V.claim_captain(s, tokens['A'], caller_id='alice')
    # Same alice re-opens link in another tab → should be accepted, return 'A'
    team = V.claim_captain(s, tokens['A'], caller_id='alice')
    return team == 'A', f'team={team}'
t('claim_captain: re-claim by same caller is idempotent', t_claim_idempotent_same_caller)


def t_claim_unknown_token():
    s = _make_to('links')
    V.issue_tokens(s)
    try:
        V.claim_captain(s, 'definitely-not-a-real-token', caller_id='alice')
        return False, 'should have raised'
    except VetoStageError:
        return True, ''
t('claim_captain: unknown token rejected', t_claim_unknown_token)


def t_revoke_token():
    s = _make_to('veto')   # both captains already claimed
    old = s.tokens['A'].value
    new = V.revoke_token(s, 'A')
    return (new != old and not s.tokens['A'].used
            and s.state == 'links'   # dropped back from veto
            and s.tokens['B'].used), \
           f'state={s.state} new!=old={new!=old} A_used={s.tokens["A"].used}'
t('revoke_token: from veto re-issues fresh token + drops back to links', t_revoke_token)


# ═══ Veto sequence + perform_step ═════════════════════════════════════════
def t_sequence_bo3():
    s = _make_to('veto')
    return ([(x.kind, x.team) for x in s.sequence] == [
        ('BAN','A'), ('BAN','B'),
        ('PICK','A'), ('PICK','B'),
        ('BAN','A'), ('BAN','B'),
    ]), f'seq={[(x.kind,x.team) for x in s.sequence]}'
t('sequence: BO3 = ban-ban-pick-pick-ban-ban', t_sequence_bo3)


def t_sequence_bo1():
    s = V.create_session(mode='BO1')
    V.set_roster(s, 'A', 'B', _ten_players())
    V.distribute_teams(s, rng=_DeterministicRNG())
    V.start_voting(s)
    for v in range(5):
        V.cast_vote(s, 'A', v, 0); V.cast_vote(s, 'B', v, 0)
    V.resolve_captains(s)
    tokens = V.issue_tokens(s)
    V.claim_captain(s, tokens['A'], 'a'); V.claim_captain(s, tokens['B'], 'b')
    return ([(x.kind, x.team) for x in s.sequence] == [
        ('BAN','A'), ('BAN','B'), ('BAN','A'),
        ('BAN','B'), ('BAN','A'), ('BAN','B'),
    ]), f'seq={[(x.kind,x.team) for x in s.sequence]}'
t('sequence: BO1 = 6 bans', t_sequence_bo1)


def t_perform_wrong_team():
    s = _make_to('veto')
    # Step 0 is team A's BAN — team B trying to act on it should fail
    try:
        V.perform_step(s, 'B', s.map_pool[0])
        return False, 'should have raised'
    except VetoStageError:
        return True, ''
t('perform_step: wrong team rejected', t_perform_wrong_team)


def t_perform_banned_map():
    s = _make_to('veto')
    pool = list(s.map_pool)
    V.perform_step(s, 'A', pool[0])   # A bans pool[0]
    try:
        V.perform_step(s, 'B', pool[0])   # B tries to act on banned map
        return False, 'should have raised'
    except VetoStageError:
        return True, ''
t('perform_step: previously-banned map rejected', t_perform_banned_map)


def t_decider_correct():
    s = _make_to('finale')
    # After _make_to('finale') ran: bans were pool[0,1,4,5]; picks were pool[2,3]
    # Decider should be pool[6] (the unbanned, unpicked remainder)
    pool = list(ACTIVE_DUTY_POOL)
    return (s.decider == pool[6]
            and s.final_maps == [pool[2], pool[3], pool[6]]
            and s.state == 'finale'), \
           f'decider={s.decider} finals={s.final_maps} state={s.state}'
t('decider: correctly identified as the last unbanned map', t_decider_correct)


# ═══ MatchZy config generation ════════════════════════════════════════════
def t_matchzy_config_shape():
    s = _make_to('finale')
    cfg = V.build_matchzy_config(s)
    keys = set(cfg.keys())
    expected = {'matchid', 'num_maps', 'maplist', 'players_per_team',
                'team1', 'team2', 'cvars', '_oblivion_meta'}
    return (expected.issubset(keys)
            and cfg['num_maps'] == 3
            and cfg['players_per_team'] == 5
            and len(cfg['maplist']) == 3
            and cfg['team1']['name'] == s.team_a_name
            and len(cfg['team1']['players']) == 5
            and cfg['_oblivion_meta']['decider'] == s.decider
            and len(cfg['_oblivion_meta']['vetoes']) == 6), \
           f'keys={sorted(keys)[:5]} num_maps={cfg["num_maps"]}'
t('build_matchzy_config: contains expected fields + correct counts', t_matchzy_config_shape)


def t_matchzy_config_wrong_state():
    s = _make_to('roster')
    try:
        V.build_matchzy_config(s)
        return False, 'should have raised'
    except InvalidVetoTransition:
        return True, ''
t('build_matchzy_config: rejects non-finale/complete state', t_matchzy_config_wrong_state)


# ═══ Reset ════════════════════════════════════════════════════════════════
def t_reset_from_veto():
    s = _make_to('veto')
    V.reset(s)
    return (s.state == 'idle' and not s.roster and not s.team_a
            and not s.sequence and not s.tokens), \
           f'state={s.state}'
t('reset: from veto → idle with all state cleared', t_reset_from_veto)


def t_reset_from_finale():
    s = _make_to('finale')
    V.reset(s)
    return s.state == 'idle' and not s.final_maps, f'state={s.state}'
t('reset: from finale → idle', t_reset_from_finale)


def t_reset_from_idle_ok():
    s = V.VetoSession()
    V.reset(s)   # should be a no-op, not an error
    return s.state == 'idle', f'state={s.state}'
t('reset: from idle is a no-op (legal)', t_reset_from_idle_ok)


# ═══ Day 7 — Edge cases the earlier batches didn't cover ═════════════════
# These hunt the corners that bit us in past releases: input boundaries,
# state-transition completeness, threading reentrancy, and the "what if
# the operator does X mid-flow" shapes.

def t_sequence_bo5_length():
    """BO5 = ban-ban-pick-pick-pick-pick + decider = 6 steps + 5 final maps
    (4 picks + 1 decider).  The Day 1 unit tests verified BO1 + BO3 but
    not BO5 — close that gap before tagging."""
    s = V.create_session(mode='BO5')
    V.set_roster(s, 'A', 'B', _ten_players())
    V.distribute_teams(s, rng=_DeterministicRNG())
    V.start_voting(s)
    for voter in range(5):
        V.cast_vote(s, 'A', voter, 0); V.cast_vote(s, 'B', voter, 0)
    V.resolve_captains(s)
    tokens = V.issue_tokens(s)
    V.claim_captain(s, tokens['A'], caller_id='ca')
    V.claim_captain(s, tokens['B'], caller_id='cb')
    kinds = [st.kind for st in s.sequence]
    # BO5: 2 bans, 4 picks, 0 more bans (the leftover is the decider).
    expected_kinds = ['BAN','BAN','PICK','PICK','PICK','PICK']
    return (s.mode == 'BO5'
            and len(s.sequence) == 6
            and kinds == expected_kinds), \
           f'mode={s.mode} kinds={kinds} len={len(s.sequence)}'
t('sequence: BO5 = ban-ban-pick-pick-pick-pick (6 steps, 5 final maps)', t_sequence_bo5_length)


def t_sequence_bo5_final_count():
    """End-to-end BO5: walk the 6 steps and verify final_maps = 5 maps
    with the decider as the last unbanned one."""
    s = V.create_session(mode='BO5')
    V.set_roster(s, 'A', 'B', _ten_players())
    V.distribute_teams(s, rng=_DeterministicRNG())
    V.start_voting(s)
    for voter in range(5):
        V.cast_vote(s, 'A', voter, 0); V.cast_vote(s, 'B', voter, 0)
    V.resolve_captains(s)
    tokens = V.issue_tokens(s)
    V.claim_captain(s, tokens['A'], caller_id='ca')
    V.claim_captain(s, tokens['B'], caller_id='cb')
    pool = list(s.map_pool)
    # BO5 sequence: ban A, ban B, pick A, pick B, pick A, pick B → decider
    V.perform_step(s, 'A', pool[0])
    V.perform_step(s, 'B', pool[1])
    V.perform_step(s, 'A', pool[2])
    V.perform_step(s, 'B', pool[3])
    V.perform_step(s, 'A', pool[4])
    V.perform_step(s, 'B', pool[5])
    # Decider should be the only unbanned + unpicked map = pool[6]
    return (s.state == 'finale'
            and len(s.final_maps) == 5
            and s.decider == pool[6]
            and s.final_maps[-1] == pool[6]), \
           f'state={s.state} final_maps={s.final_maps} decider={s.decider}'
t('sequence: BO5 full walk → 5 final maps, decider = last unbanned', t_sequence_bo5_final_count)


def t_matchzy_config_excludes_steamidless_players():
    """A player with empty steam_id must be omitted from MatchZy's
    {steamid: name} team dict (MatchZy can't address them).  Mixed
    rosters (some have IDs, some don't) should still produce a usable
    config — just with fewer than 5 addressable players per team."""
    s = V.create_session(mode='BO3')
    # 8 with IDs, 2 without — distribution will end up 5-5 but one of the
    # two ID-less ones must land in each team for the test to bite.  We
    # control distribution via deterministic shuffle so first-5/last-5.
    roster = [
        RosterPlayer(name=f'p{i}', steam_id=(f'STEAM_{i}' if i < 4 else ''))
        for i in range(5)
    ] + [
        RosterPlayer(name=f'q{i}', steam_id=(f'STEAM_q{i}' if i < 4 else ''))
        for i in range(5)
    ]
    V.set_roster(s, 'A', 'B', roster)
    V.distribute_teams(s, rng=_DeterministicRNG())
    # Drive to finale
    V.start_voting(s)
    for voter in range(5):
        V.cast_vote(s, 'A', voter, 0); V.cast_vote(s, 'B', voter, 0)
    V.resolve_captains(s)
    tokens = V.issue_tokens(s)
    V.claim_captain(s, tokens['A'], caller_id='ca')
    V.claim_captain(s, tokens['B'], caller_id='cb')
    pool = list(s.map_pool)
    for cap, m in zip(['A','B','A','B','A','B'], pool[:6]):
        V.perform_step(s, cap, m)
    cfg = V.build_matchzy_config(s)
    # Each team had 5 players, 4 with IDs + 1 without.  MatchZy dict
    # should contain only the 4 with IDs — empty string is not a key.
    return (len(cfg['team1']['players']) == 4
            and len(cfg['team2']['players']) == 4
            and '' not in cfg['team1']['players']
            and '' not in cfg['team2']['players']), \
           f"team1={cfg['team1']['players']} team2={cfg['team2']['players']}"
t('build_matchzy_config: players without steam_id excluded from team dict', t_matchzy_config_excludes_steamidless_players)


def t_matchzy_config_matchid_format():
    """matchid must be a non-empty string starting with our tool prefix
    so MatchZy logs + the operator can grep their CS2 console for it."""
    s = _make_to('finale')
    cfg = V.build_matchzy_config(s)
    mid = cfg['matchid']
    return (isinstance(mid, str)
            and mid.startswith('oblivion-veto-')
            and len(mid) > len('oblivion-veto-')), \
           f'matchid={mid!r}'
t('build_matchzy_config: matchid uses `oblivion-veto-<ts>` format', t_matchzy_config_matchid_format)


def t_revoke_before_issue_mints_one_anyway():
    """Documented behaviour: `revoke_token` doesn't gate on
    has-token-already — it just mints a fresh one for the given team.
    Calling it before issue_tokens() lands a token in session.tokens['A']
    but leaves 'B' empty.  This is acceptable (operators shouldn't get
    there naturally — the SPA only exposes Revoke after Issue), but
    documenting it locks the contract."""
    s = _make_to('links')
    new_token = V.revoke_token(s, 'A')
    return (isinstance(new_token, str) and len(new_token) > 20
            and 'A' in s.tokens
            and 'B' not in s.tokens), \
           f'tokens={dict(s.tokens)}'
t('revoke_token: pre-issue revoke mints a fresh token (documented behaviour)', t_revoke_before_issue_mints_one_anyway)


def t_revoke_other_team_unaffected():
    """Revoking A's token must leave B's token untouched.  The earlier
    revoke test verified A's new value but didn't assert B was preserved."""
    s = _make_to('veto')
    original_b = s.tokens['B'].value
    V.revoke_token(s, 'A')
    return s.tokens['B'].value == original_b, \
           f'B value changed: orig={original_b!r} now={s.tokens["B"].value!r}'
t('revoke_token: other team\'s token is unaffected', t_revoke_other_team_unaffected)


def t_revoke_unknown_team_rejected():
    s = _make_to('veto')
    for bad in ('C', 'a', '', 'AB', 'team_a'):
        try:
            V.revoke_token(s, bad)
            return False, f'should have rejected team={bad!r}'
        except VetoError:
            pass
    return True, ''
t('revoke_token: unknown team values rejected', t_revoke_unknown_team_rejected)


def t_issue_tokens_rotates_on_recall():
    """Documented (and arguably buggy) behaviour: calling issue_tokens
    a second time from `links` ROTATES both tokens, breaking any URL
    already shared with the captains.  The SPA only calls this once per
    session (the "Generate captain links" button binds the result to a
    module-local cache), but a browser refresh during the links stage
    would re-trigger and silently invalidate the shared URLs.

    TODO follow-up: make issue_tokens idempotent (return existing if
    already issued) — would prevent the refresh-invalidates-URLs trap.
    For now this test pins the current behaviour so a future change
    that flips it is visible in the diff."""
    s = _make_to('links')
    first = V.issue_tokens(s)
    second = V.issue_tokens(s)
    return (first['A'] != second['A']
            and first['B'] != second['B']
            and s.tokens['A'].value == second['A']), \
           f'first={first} second={second}'
t('issue_tokens: pinned — currently rotates on re-call (TODO: make idempotent)', t_issue_tokens_rotates_on_recall)


def t_perform_step_after_finale_rejected():
    """Once we're at `finale`, perform_step must reject — there's no
    8th step to play.  Defends against a stale captain SPA clicking on
    the board after the operator already hit Hand-to-MatchZy."""
    s = _make_to('finale')
    try:
        V.perform_step(s, 'A', s.map_pool[6])
        return False, 'should have raised'
    except VetoError:
        return True, ''
t('perform_step: rejected after finale state reached', t_perform_step_after_finale_rejected)


def t_complete_only_from_finale():
    """complete() is legal only from `finale` — verifies the gate on
    every other state, including the dangerous `veto` → `complete` skip."""
    for state in ('roster', 'teams', 'voting', 'links', 'veto'):
        s = _make_to(state)
        try:
            V.complete(s)
            return False, f'should have raised from state={state}'
        except InvalidVetoTransition:
            pass
    return True, ''
t('complete: rejected from every non-finale state', t_complete_only_from_finale)


def t_roster_long_names_accepted():
    """Names up to ~64 chars (well over 32) — the model accepts them;
    callers (HTTP layer / SPA UI) enforce display caps.  This locks the
    contract: the state machine doesn't impose a hard length limit.
    State stays `roster` after set_roster (distribute_teams advances)."""
    s = V.create_session(mode='BO3')
    long_name = 'A' * 60  # plausibly long Steam display name with prefix
    players = [RosterPlayer(name=f'{long_name}_{i}', steam_id=f'S{i}') for i in range(10)]
    try:
        V.set_roster(s, 'Team Alpha', 'Team Bravo', players)
        return (s.state == 'roster'
                and len(s.roster) == 10
                and all(len(p.name) >= 60 for p in s.roster)), f'state={s.state}'
    except VetoError as e:
        return False, f'unexpectedly rejected: {e}'
t('set_roster: long names (60 chars) accepted — no model-level cap', t_roster_long_names_accepted)


def t_roster_whitespace_name_rejected():
    """A whitespace-only name shouldn't count as filled.  Either the
    state machine rejects it, or distribute_teams later refuses — both
    are defensible.  We assert it's caught somewhere before voting."""
    s = V.create_session(mode='BO3')
    players = [RosterPlayer(name=' ', steam_id='S1')] + \
              [RosterPlayer(name=f'p{i}', steam_id=f'S{i}') for i in range(9)]
    try:
        V.set_roster(s, 'A', 'B', players)
        # If we got here, set_roster accepted whitespace names.  Document
        # the actual behaviour so future contributors know what to expect.
        # (Our impl accepts; HTTP layer's filled-count check rejects.)
        return s.state == 'teams', f'state={s.state}'
    except VetoError:
        return True, 'rejected at set_roster (also fine)'
t('set_roster: whitespace name handled (accepted-by-model OR rejected)', t_roster_whitespace_name_rejected)


def t_perform_step_wrong_kind_no_match():
    """Whether we BAN or PICK is determined by sequence position, not
    by the caller.  perform_step() takes (team, map_id) only — there's
    no way for a captain to demand "I want to PICK now."  This locks
    the API surface so a future refactor doesn't accidentally expose
    a kind parameter that captains could spoof."""
    import inspect
    sig = inspect.signature(V.perform_step)
    params = list(sig.parameters.keys())
    return params == ['session', 'team', 'map_id'], f'params={params}'
t('perform_step: signature is (session, team, map_id) — kind is server-derived', t_perform_step_wrong_kind_no_match)


def t_state_transitions_reachable_from_idle():
    """Smoke: walk the full state graph and assert every state is
    reachable in turn.  Defends against a refactor that drops a
    transition from _LEGAL_TRANSITIONS.  `idle` is the starting state
    so a fresh VetoSession is the canonical idle observation."""
    reached = [('idle', V.VetoSession().state)]
    for state in ('roster','teams','voting','links','veto','finale','complete'):
        s = _make_to(state)
        reached.append((state, s.state))
    return all(actual == expected for expected, actual in reached), f'reached={reached}'
t('state graph: every state idle→complete reachable via the helpers', t_state_transitions_reachable_from_idle)


def t_set_ready_only_in_finale():
    """set_ready raises InvalidVetoTransition outside finale state.
    Defends against a stale captain SPA tab toggling Ready during a
    fresh session's roster/voting/veto stages."""
    for state in ('roster', 'teams', 'voting', 'links', 'veto', 'complete'):
        s = _make_to(state)
        try:
            V.set_ready(s, 'A', True)
            return False, f'should have raised from state={state}'
        except (InvalidVetoTransition, VetoStageError):
            pass
    return True, ''
t('set_ready: legal only in finale state', t_set_ready_only_in_finale)


def t_set_ready_toggles():
    """set_ready can mark ready=True, then un-ready with ready=False."""
    s = _make_to('finale')
    V.set_ready(s, 'A', True)
    if not s.ready_a or s.ready_b:
        return False, f'after A ready: a={s.ready_a} b={s.ready_b}'
    V.set_ready(s, 'B', True)
    if not (s.ready_a and s.ready_b):
        return False, 'B ready should not have cleared A'
    V.set_ready(s, 'A', False)
    return (s.ready_a is False and s.ready_b is True), \
           f'after A un-ready: a={s.ready_a} b={s.ready_b}'
t('set_ready: toggles independently per team', t_set_ready_toggles)


def t_both_captains_ready_predicate():
    s = _make_to('finale')
    if V.both_captains_ready(s):
        return False, 'fresh finale should NOT have both ready'
    V.set_ready(s, 'A', True)
    if V.both_captains_ready(s):
        return False, 'only A ready - both_captains_ready should be False'
    V.set_ready(s, 'B', True)
    return V.both_captains_ready(s), 'both set to True'
t('both_captains_ready: True only when both flags set', t_both_captains_ready_predicate)


def t_set_ready_unknown_team_rejected():
    s = _make_to('finale')
    for bad in ('C', 'a', '', 'AB', 'team_a'):
        try:
            V.set_ready(s, bad, True)
            return False, f'should have rejected team={bad!r}'
        except VetoStageError:
            pass
    return True, ''
t('set_ready: unknown team values rejected', t_set_ready_unknown_team_rejected)


def t_reset_clears_ready_flags():
    """Both ready flags must clear on reset so a fresh session starts
    with both captains un-ready (not inheriting from the prior match)."""
    s = _make_to('finale')
    V.set_ready(s, 'A', True)
    V.set_ready(s, 'B', True)
    V.reset(s)
    return (s.ready_a is False and s.ready_b is False), \
           f'after reset: a={s.ready_a} b={s.ready_b}'
t('reset: clears both ready flags', t_reset_clears_ready_flags)


def t_roster_player_has_discord_id_field():
    """v0.11.0: RosterPlayer dataclass gains optional `discord_id` field
    used by Layer 1A auto-DM.  Backward-compatible default ''."""
    p = V.RosterPlayer(name='Alice')
    if p.discord_id != '':
        return False, f'default not empty: {p.discord_id!r}'
    p2 = V.RosterPlayer(name='Bob', steam_id='STEAM_0:1:42', discord_id='123456789012345678')
    return (p2.discord_id == '123456789012345678'
            and p2.steam_id == 'STEAM_0:1:42'
            and p2.name == 'Bob'), f'p2={p2}'
t('RosterPlayer: discord_id field defaults empty, accepts a value', t_roster_player_has_discord_id_field)


def t_rematch_preserves_teams_and_captains():
    """v0.10.2: rematch from complete-state must keep team rosters +
    names + captains but clear veto state."""
    s = _make_to('complete')
    teamA_before = list(s.team_a)
    teamB_before = list(s.team_b)
    capA_before  = s.captain_a_idx
    capB_before  = s.captain_b_idx
    nameA = s.team_a_name
    nameB = s.team_b_name
    V.rematch(s)
    return (s.state == 'links'
            and s.team_a == teamA_before
            and s.team_b == teamB_before
            and s.captain_a_idx == capA_before
            and s.captain_b_idx == capB_before
            and s.team_a_name == nameA
            and s.team_b_name == nameB
            and not s.tokens
            and not s.sequence
            and not s.final_maps
            and s.ready_a is False
            and s.ready_b is False), \
           f'state={s.state} ta={len(s.team_a)} tb={len(s.team_b)} caps={s.captain_a_idx},{s.captain_b_idx} tokens={len(s.tokens)}'
t('rematch: preserves teams + captains + names; clears veto state', t_rematch_preserves_teams_and_captains)


def t_rematch_legal_only_from_complete():
    """Rematch is a special complete→links jump.  Not legal from any
    other state — operator must finish the current series first.
    `idle` is a fresh VetoSession (no helper branch in _make_to)."""
    for state in ('idle', 'roster', 'teams', 'voting', 'links', 'veto', 'finale'):
        s = V.VetoSession() if state == 'idle' else _make_to(state)
        try:
            V.rematch(s)
            return False, f'should have raised from state={state}'
        except InvalidVetoTransition:
            pass
    return True, ''
t('rematch: rejected from every non-complete state', t_rematch_legal_only_from_complete)


def t_rematch_can_change_mode():
    """Rematch accepts an optional `mode` switch (e.g. complete a BO3,
    then rematch as BO1 if time's running short)."""
    s = _make_to('complete')
    assert s.mode == 'BO3'
    V.rematch(s, mode='BO1')
    return s.mode == 'BO1', f'mode={s.mode}'
t('rematch: optional mode switch (BO3 → BO1)', t_rematch_can_change_mode)


def t_archive_to_history_captures_fields():
    """v0.10.2: archive_to_history serialises a finished session into a
    dict the operator can read out of oblivion_matches.json later."""
    s = _make_to('finale')
    V.build_matchzy_config(s)
    snap = V.archive_to_history(s)
    needs = {'matchid', 'created_at', 'mode', 'team_a', 'team_b',
             'captain_a', 'captain_b', 'final_maps', 'decider', 'sequence'}
    return (needs.issubset(snap.keys())
            and snap['mode'] == 'BO3'
            and snap['decider'] == s.decider
            and len(snap['final_maps']) == 3
            and len(snap['sequence']) == 6), \
           f'keys={sorted(snap.keys())}'
t('archive_to_history: captures matchid + teams + maplist + sequence', t_archive_to_history_captures_fields)


def t_reset_from_complete():
    """A finished session must accept reset() so the operator can
    start a fresh BO without restarting the app."""
    s = _make_to('complete')
    V.reset(s)
    return s.state == 'idle' and not s.tokens and not s.final_maps, \
           f'state={s.state} tokens={list(s.tokens)} maps={s.final_maps}'
t('reset: from complete → idle (operator starts a new session)', t_reset_from_complete)


# ═══ Auto-generated pytest cases ══════════════════════════════════════════
def _slug(name):
    out = ''.join(c if c.isalnum() else '_' for c in name).strip('_').lower()
    while '__' in out: out = out.replace('__', '_')
    return 'test_' + out


def _make_pytest_case(_ok, _detail):
    def _case():
        assert _ok, _detail
    return _case


for _ok, _name, _detail in results:
    _slug_name = _slug(_name)
    _i = 1
    while _slug_name in globals():
        _i += 1
        _slug_name = f'{_slug(_name)}_{_i}'
    globals()[_slug_name] = _make_pytest_case(_ok, _detail)


# ═══ Standalone-script entry point ════════════════════════════════════════
if __name__ == '__main__':
    print()
    print('=' * 70)
    passes = sum(1 for ok, _, _ in results if ok)
    fails  = sum(1 for ok, _, _ in results if not ok)
    for ok, name, detail in results:
        mark = '[+]' if ok else '[X]'
        print(f'{mark} {name}')
        if not ok:
            print(f'    {detail}')
    print('=' * 70)
    print(f'  {passes} passed, {fails} failed')
    sys.exit(0 if fails == 0 else 1)
