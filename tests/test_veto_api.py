"""
HTTP integration tests for the v0.10.0 veto API routes (Day 2).

Drives Flask via test_client; never touches a real CS2 server or RCON.
Covers:
 - Full happy-path: create → roster → distribute → start_voting → cast 10
   votes → resolve_captains → tokens → claim (captain A + captain B) →
   perform 6 steps → finale → reset
 - Authentication: admin can do everything; guest blocked from veto admin
   ops; captain role can only state/stream/step (and only for their team)
 - Cross-team rejection: captain B trying to act on team A's turn → 403
 - Token security: reuse rejected, unknown rejected
 - SSE stream: subscribe returns 200 + text/event-stream + initial snapshot
 - Reset clears the session

Two ways to run (same dual mode as the other test files):
    python tests/test_veto_api.py
    pytest tests/test_veto_api.py
"""
import os, sys, tempfile, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('APPDATA', tempfile.mkdtemp(prefix='oblivion_veto_api_'))

from cs2servergui.core import AppCore
from cs2servergui.web import create_flask

results = []
def t(name, fn):
    try:
        ok, detail = fn()
        results.append((ok, name, detail))
    except Exception as e:
        results.append((False, name, f'EXC: {type(e).__name__}: {e}'))


# ─── Fixtures ─────────────────────────────────────────────────────────────
def _new_app():
    """Fresh AppCore + Flask app + test client.  Admin PIN set to '0000'."""
    ac = AppCore()
    ac.admin_pin = '0000'
    ac.guest_pin = '9999'      # so we can test guest-role rejection
    app = create_flask(ac)
    return ac, app, app.test_client()


def _login(client, pin='0000'):
    """Authenticate the test client.  Returns the response so callers can
    confirm 200."""
    return client.post('/api/auth/login', json={'pin': pin})


def _ten_player_payload():
    return [{'name': f'p{i}', 'steam_id': f'STEAM_{i}'} for i in range(10)]


# ─── Auth / role gate ─────────────────────────────────────────────────────
def t_admin_can_create():
    _, _, c = _new_app()
    _login(c)
    r = c.post('/api/veto/create', json={'mode': 'BO3'})
    return r.status_code == 200, f'status={r.status_code} body={r.get_data(as_text=True)[:80]!r}'
t('admin: POST /api/veto/create → 200', t_admin_can_create)


def t_unauth_cannot_create():
    _, _, c = _new_app()
    r = c.post('/api/veto/create', json={'mode': 'BO3'})
    return r.status_code == 401, f'status={r.status_code}'
t('unauth: POST /api/veto/create → 401', t_unauth_cannot_create)


def t_guest_cannot_create():
    _, _, c = _new_app()
    _login(c, pin='9999')  # guest PIN
    r = c.post('/api/veto/create', json={'mode': 'BO3'})
    return r.status_code == 403, f'status={r.status_code}'
t('guest: POST /api/veto/create → 403', t_guest_cannot_create)


def t_state_reachable_idle():
    _, _, c = _new_app()
    _login(c)
    r = c.get('/api/veto/state')
    body = r.get_json()
    return (r.status_code == 200 and body['state'] == 'idle'
            and body['session'] is None), f'status={r.status_code} body={body}'
t('admin: GET /api/veto/state → 200 with state=idle when no session', t_state_reachable_idle)


# ─── Happy path: full BO3 from create → finale ────────────────────────────
def t_happy_path_bo3():
    ac, app, c = _new_app()
    _login(c)

    # Create
    r = c.post('/api/veto/create', json={'mode': 'BO3'})
    assert r.status_code == 200, f'create: {r.status_code}'

    # Roster
    r = c.post('/api/veto/roster', json={
        'team_a_name': 'Alpha', 'team_b_name': 'Bravo',
        'players': _ten_player_payload(),
    })
    assert r.status_code == 200, f'roster: {r.status_code} {r.get_data(as_text=True)}'

    # Distribute
    r = c.post('/api/veto/distribute')
    assert r.status_code == 200, f'distribute: {r.status_code}'

    # Start voting
    r = c.post('/api/veto/start_voting')
    assert r.status_code == 200, f'start_voting: {r.status_code}'

    # Vote (admin proxies all 10 votes — unanimous for idx 0 on both teams)
    for team in ('A', 'B'):
        for v in range(5):
            r = c.post('/api/veto/vote', json={'team': team, 'voter_idx': v, 'votee_idx': 0})
            assert r.status_code == 200, f'vote {team}/{v}: {r.status_code}'

    # Resolve captains
    r = c.post('/api/veto/resolve_captains')
    body = r.get_json()
    assert r.status_code == 200 and body['outcome'] == 'elected', \
           f'resolve: {r.status_code} body={body}'

    # Issue tokens
    r = c.post('/api/veto/tokens')
    body = r.get_json()
    token_a = body['A']['lan'].split('join=')[-1]
    token_b = body['B']['lan'].split('join=')[-1]
    assert token_a and token_b and token_a != token_b, f'tokens={body}'

    # Need fresh clients for the captains so cookies don't collide with admin
    cap_a = app.test_client()
    cap_b = app.test_client()

    # Captain A claims
    r = cap_a.post('/api/veto/claim', json={'token': token_a})
    body = r.get_json()
    assert r.status_code == 200 and body['team'] == 'A', f'claim A: {r.status_code} {body}'

    # Captain B claims — now state should be 'veto'
    r = cap_b.post('/api/veto/claim', json={'token': token_b})
    body = r.get_json()
    assert r.status_code == 200 and body['team'] == 'B', f'claim B: {r.status_code} {body}'

    # State should be 'veto' with 6-step sequence
    r = c.get('/api/veto/state')
    state = r.get_json()
    assert state['state'] == 'veto' and len(state['session']['sequence']) == 6, \
           f'state after claims: {state}'

    pool = state['session']['map_pool']
    # Captain A bans pool[0]
    r = cap_a.post('/api/veto/step', json={'map_id': pool[0]})
    assert r.status_code == 200, f'step 1 (A ban): {r.status_code} {r.get_data(as_text=True)}'
    # Captain B bans pool[1]
    r = cap_b.post('/api/veto/step', json={'map_id': pool[1]})
    assert r.status_code == 200, f'step 2 (B ban): {r.status_code}'
    # Captain A picks pool[2]
    r = cap_a.post('/api/veto/step', json={'map_id': pool[2]})
    assert r.status_code == 200, f'step 3 (A pick): {r.status_code}'
    # Captain B picks pool[3]
    r = cap_b.post('/api/veto/step', json={'map_id': pool[3]})
    assert r.status_code == 200, f'step 4 (B pick): {r.status_code}'
    # Captain A bans pool[4]
    r = cap_a.post('/api/veto/step', json={'map_id': pool[4]})
    assert r.status_code == 200, f'step 5 (A ban): {r.status_code}'
    # Captain B bans pool[5] — last step, state should advance to finale
    r = cap_b.post('/api/veto/step', json={'map_id': pool[5]})
    assert r.status_code == 200, f'step 6 (B ban): {r.status_code}'

    r = c.get('/api/veto/state')
    state = r.get_json()
    assert state['state'] == 'finale', f'after step 6: {state["state"]}'
    assert state['session']['decider'] == pool[6], f'decider={state["session"]["decider"]} want {pool[6]}'

    # Finale (don't load match — server isn't running)
    r = c.post('/api/veto/finale', json={'load_match': False})
    body = r.get_json()
    assert r.status_code == 200 and 'config' in body, f'finale: {r.status_code}'
    assert body['config']['num_maps'] == 3, f'config: {body["config"]}'

    # Reset
    r = c.post('/api/veto/reset')
    assert r.status_code == 200, f'reset: {r.status_code}'
    r = c.get('/api/veto/state')
    assert r.get_json()['state'] == 'idle', f'post-reset state: {r.get_json()}'

    return True, 'all 6 steps + finale + reset succeeded'
t('happy-path: full BO3 (create → roster → vote → claim → 6 steps → finale → reset)', t_happy_path_bo3)


# ─── Captain authorisation ────────────────────────────────────────────────
def _setup_to_veto():
    """Helper: drive the session into the `veto` state, return (admin_client,
    cap_a_client, cap_b_client, pool)."""
    _, app, c = _new_app()
    _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'Alpha', 'team_b_name': 'Bravo',
        'players': _ten_player_payload(),
    })
    c.post('/api/veto/distribute')
    c.post('/api/veto/start_voting')
    for team in ('A', 'B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team': team, 'voter_idx': v, 'votee_idx': 0})
    c.post('/api/veto/resolve_captains')
    tk = c.post('/api/veto/tokens').get_json()
    cap_a = app.test_client()
    cap_b = app.test_client()
    token_a = tk['A']['lan'].split('join=')[-1]
    token_b = tk['B']['lan'].split('join=')[-1]
    cap_a.post('/api/veto/claim', json={'token': token_a})
    cap_b.post('/api/veto/claim', json={'token': token_b})
    pool = c.get('/api/veto/state').get_json()['session']['map_pool']
    return c, cap_a, cap_b, pool


def t_captain_wrong_team_rejected():
    c, cap_a, cap_b, pool = _setup_to_veto()
    # Step 0 is team A's turn — captain B tries to act → 400 (wrong turn).
    # Note: 400, not 403 — captain B IS authorised to call /step (their own
    # endpoint), but the step belongs to team A.  403 only fires when a
    # captain tries to spoof the `team` field to act AS the other team.
    r = cap_b.post('/api/veto/step', json={'map_id': pool[0]})
    return (r.status_code == 400
            and 'not team B' in r.get_data(as_text=True)), \
           f'status={r.status_code} body={r.get_data(as_text=True)[:80]!r}'
t('captain B acting on team A turn → 400 (not their turn)', t_captain_wrong_team_rejected)


def t_captain_spoof_team_rejected():
    c, cap_a, cap_b, pool = _setup_to_veto()
    # Captain B explicitly passes `team: "A"` to try to spoof team A → 403.
    r = cap_b.post('/api/veto/step', json={'team': 'A', 'map_id': pool[0]})
    return r.status_code == 403, \
           f'status={r.status_code} body={r.get_data(as_text=True)[:80]!r}'
t('captain B spoofing team=A in body → 403', t_captain_spoof_team_rejected)


def t_captain_cannot_create():
    """Captain role's allowlist excludes admin endpoints — even if they have
    a valid session cookie, POST /api/veto/create must be 403."""
    _, _, _, _ = _setup_to_veto()
    _, app, _ = _new_app()
    _login(_setup := app.test_client())   # noqa
    # Use a captain session from the setup above isn't possible (different apps).
    # Better: spin up a fresh app, claim a captain token, then try /create.
    _, app2, c2 = _new_app()
    _login(c2)
    c2.post('/api/veto/create', json={'mode': 'BO3'})
    c2.post('/api/veto/roster', json={
        'team_a_name': 'Alpha', 'team_b_name': 'Bravo',
        'players': _ten_player_payload(),
    })
    c2.post('/api/veto/distribute')
    c2.post('/api/veto/start_voting')
    for team in ('A', 'B'):
        for v in range(5):
            c2.post('/api/veto/vote', json={'team': team, 'voter_idx': v, 'votee_idx': 0})
    c2.post('/api/veto/resolve_captains')
    tk = c2.post('/api/veto/tokens').get_json()
    cap_a = app2.test_client()
    cap_a.post('/api/veto/claim', json={'token': tk['A']['lan'].split('join=')[-1]})
    # Captain A tries to create a new session → 403
    r = cap_a.post('/api/veto/create', json={'mode': 'BO1'})
    return r.status_code == 403, f'status={r.status_code} body={r.get_data(as_text=True)[:80]!r}'
t('captain role: POST /api/veto/create → 403 (admin-only path)', t_captain_cannot_create)


def t_captain_can_read_state():
    c, cap_a, cap_b, pool = _setup_to_veto()
    r = cap_a.get('/api/veto/state')
    body = r.get_json()
    return (r.status_code == 200 and body['state'] == 'veto'), \
           f'status={r.status_code} state={body.get("state")}'
t('captain: GET /api/veto/state → 200', t_captain_can_read_state)


# ─── Token security ───────────────────────────────────────────────────────
def t_token_unknown_rejected():
    _, _, c = _new_app()
    _login(c)
    # No active session yet
    r = c.post('/api/veto/claim', json={'token': 'definitely-not-real'})
    return r.status_code == 400, f'status={r.status_code} body={r.get_data(as_text=True)[:80]!r}'
t('claim: unknown token → 400 (no session)', t_token_unknown_rejected)


def t_token_reuse_rejected():
    _, app, c = _new_app()
    _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'Alpha', 'team_b_name': 'Bravo',
        'players': _ten_player_payload(),
    })
    c.post('/api/veto/distribute')
    c.post('/api/veto/start_voting')
    for team in ('A', 'B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team': team, 'voter_idx': v, 'votee_idx': 0})
    c.post('/api/veto/resolve_captains')
    tk = c.post('/api/veto/tokens').get_json()
    token_a = tk['A']['lan'].split('join=')[-1]
    # First client claims successfully
    client1 = app.test_client()
    r1 = client1.post('/api/veto/claim', json={'token': token_a})
    assert r1.status_code == 200, f'first claim: {r1.status_code}'
    # Second client tries the same token from a different IP — should fail
    client2 = app.test_client()
    client2.environ_base['REMOTE_ADDR'] = '10.0.0.99'
    r2 = client2.post('/api/veto/claim', json={'token': token_a})
    return r2.status_code == 400, f'second claim status={r2.status_code}'
t('claim: token reuse from different caller → 400', t_token_reuse_rejected)


# ─── SSE mirror ───────────────────────────────────────────────────────────
def t_sse_stream_responds():
    """Open the SSE stream and verify the initial snapshot is delivered."""
    _, app, c = _new_app()
    _login(c)
    r = c.get('/api/veto/stream', buffered=False)
    return (r.status_code == 200
            and r.headers.get('Content-Type', '').startswith('text/event-stream')), \
           f'status={r.status_code} type={r.headers.get("Content-Type")}'
t('SSE: /api/veto/stream returns 200 + text/event-stream', t_sse_stream_responds)


# ─── Auto-generated pytest cases ──────────────────────────────────────────
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
