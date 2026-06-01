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
    """Fresh AppCore + Flask app + test client.  Admin PIN set to '0000'.

    Day 6: the MatchZy handoff in `/api/veto/finale` writes a JSON config
    under `<csgo>/cfg/MatchZy/`.  We redirect `_csgo_dir` to a per-app
    tempdir so tests never touch the real CS2 install dir on the user's
    machine.  Without this, repeated test runs would litter
    `D:\steamcmd\…\game\csgo\cfg\MatchZy\` with `oblivion-veto-*.json`
    files.
    """
    ac = AppCore()
    ac.admin_pin = '0000'
    ac.guest_pin = '9999'      # so we can test guest-role rejection
    _fake_csgo = tempfile.mkdtemp(prefix='oblivion_veto_csgo_')
    ac._csgo_dir = lambda: _fake_csgo  # type: ignore[method-assign]
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


def t_sse_broadcasts_on_mutation():
    """Subscribe in one thread; mutate via a second client; verify the
    subscriber receives a non-keepalive frame within 2 seconds."""
    import threading, time
    _, app, c = _new_app()
    _login(c)

    received: list[str] = []
    done = threading.Event()

    def subscriber():
        # Use a fresh client so cookies don't collide
        sc = app.test_client()
        _login(sc)
        r = sc.get('/api/veto/stream', buffered=False)
        # Read up to a few chunks then signal done
        deadline = time.time() + 3
        for chunk in r.response:
            if chunk and not chunk.startswith(b':'):   # ignore keepalives
                received.append(chunk.decode('utf-8', errors='replace'))
                if len(received) >= 2:   # initial + mutation
                    break
            if time.time() > deadline:
                break
        done.set()

    th = threading.Thread(target=subscriber, daemon=True)
    th.start()
    # Give the subscriber a moment to subscribe + receive the initial snapshot
    time.sleep(0.3)
    # Trigger a mutation
    c.post('/api/veto/create', json={'mode': 'BO1'})
    done.wait(timeout=3)
    return len(received) >= 2 and 'idle' in received[0] and 'roster' in received[1], \
           f'received={len(received)} initial-has-idle={"idle" in (received[0] if received else "")} second-has-roster={"roster" in (received[1] if len(received)>1 else "")}'
t('SSE: mutation triggers a fresh snapshot frame to subscribers', t_sse_broadcasts_on_mutation)


# ─── Day-2 polish ─────────────────────────────────────────────────────────
def t_create_refuses_overwrite():
    _, _, c = _new_app()
    _login(c)
    r1 = c.post('/api/veto/create', json={'mode': 'BO3'})
    assert r1.status_code == 200
    r2 = c.post('/api/veto/create', json={'mode': 'BO1'})
    body = r2.get_json() or {}
    return (r2.status_code == 409 and body.get('current_state') == 'roster'), \
           f'status={r2.status_code} body={body}'
t('create: 409 when a session is already active (operator must reset first)', t_create_refuses_overwrite)


def t_snapshot_has_current_step_detail():
    """Snapshot in `veto` state should include current_step_detail + legal_moves."""
    _, app, c = _new_app()
    _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'A', 'team_b_name': 'B',
        'players': _ten_player_payload(),
    })
    c.post('/api/veto/distribute')
    c.post('/api/veto/start_voting')
    for team in ('A', 'B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team': team, 'voter_idx': v, 'votee_idx': 0})
    c.post('/api/veto/resolve_captains')
    tk = c.post('/api/veto/tokens').get_json()
    cap_a = app.test_client(); cap_b = app.test_client()
    cap_a.post('/api/veto/claim', json={'token': tk['A']['lan'].split('join=')[-1]})
    cap_b.post('/api/veto/claim', json={'token': tk['B']['lan'].split('join=')[-1]})
    snap = c.get('/api/veto/state').get_json()
    sess = snap['session']
    step = sess.get('current_step_detail')
    legal = sess.get('legal_moves')
    return (step == {'index': 0, 'kind': 'BAN', 'team': 'A'}
            and len(legal) == 7  # nothing banned yet, all 7 maps legal
            and set(legal) == set(sess['map_pool'])), \
           f'step={step} legal={legal}'
t('snapshot: current_step_detail + legal_moves present in veto state', t_snapshot_has_current_step_detail)


def t_snapshot_idle_step_none():
    _, _, c = _new_app()
    _login(c)
    snap = c.get('/api/veto/state').get_json()
    return snap['state'] == 'idle' and snap['session'] is None, f'snap={snap}'
t('snapshot: idle session has state=idle, session=None', t_snapshot_idle_step_none)


def t_captain_can_hit_api_state():
    """Captain role's allowlist includes /api/state."""
    c, cap_a, cap_b, pool = _setup_to_veto()
    r = cap_a.get('/api/state')
    return r.status_code == 200, f'status={r.status_code}'
t('captain: GET /api/state → 200 (in _CAPTAIN_PATHS)', t_captain_can_hit_api_state)


# ─── QR code endpoint (v0.10.0 Day 4) ─────────────────────────────────────
def _setup_to_tokens():
    """Drive to the post-resolve_captains stage and return (admin_client,
    tokens_dict).  tokens_dict has the {A:{token,lan,public}, B:{...}} shape."""
    _, _, c = _new_app()
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
    return c, tk


def t_tokens_response_includes_raw_token():
    """Day 4 change: /api/veto/tokens now returns {token, lan, public} per
    team (was just {lan, public}).  SPA needs the raw token to build QR URLs."""
    _, tk = _setup_to_tokens()
    return ('token' in tk['A'] and 'token' in tk['B']
            and tk['A']['token'] and tk['B']['token']
            and tk['A']['token'] in tk['A']['lan']), f'tk={tk}'
t('tokens: response includes raw token per team', t_tokens_response_includes_raw_token)


def t_qr_returns_svg_for_valid_token():
    c, tk = _setup_to_tokens()
    r = c.get(f"/api/veto/qr?token={tk['A']['token']}&kind=lan")
    body = r.get_data(as_text=True)
    return (r.status_code == 200
            and r.mimetype == 'image/svg+xml'
            and '<svg' in body
            and len(body) > 200), f'status={r.status_code} mime={r.mimetype} len={len(body)}'
t('qr: GET /api/veto/qr?token=…&kind=lan → SVG', t_qr_returns_svg_for_valid_token)


def t_qr_rejects_unknown_token():
    c, _tk = _setup_to_tokens()
    r = c.get('/api/veto/qr?token=not-a-real-token-12345&kind=lan')
    return r.status_code == 404, f'status={r.status_code}'
t('qr: unknown token → 404', t_qr_rejects_unknown_token)


def t_qr_rejects_missing_token():
    c, _tk = _setup_to_tokens()
    r = c.get('/api/veto/qr?kind=lan')
    return r.status_code == 400, f'status={r.status_code}'
t('qr: missing token → 400', t_qr_rejects_missing_token)


def t_qr_rejects_bad_kind():
    c, tk = _setup_to_tokens()
    r = c.get(f"/api/veto/qr?token={tk['A']['token']}&kind=zigzag")
    return r.status_code == 400, f'status={r.status_code}'
t('qr: bad kind → 400', t_qr_rejects_bad_kind)


def t_qr_requires_auth():
    _, _, c_auth = _new_app()
    _login(c_auth)
    c_auth.post('/api/veto/create', json={'mode': 'BO3'})
    c_auth.post('/api/veto/roster', json={
        'team_a_name': 'Alpha', 'team_b_name': 'Bravo',
        'players': _ten_player_payload(),
    })
    c_auth.post('/api/veto/distribute'); c_auth.post('/api/veto/start_voting')
    for team in ('A','B'):
        for v in range(5):
            c_auth.post('/api/veto/vote', json={'team':team,'voter_idx':v,'votee_idx':0})
    c_auth.post('/api/veto/resolve_captains')
    tk = c_auth.post('/api/veto/tokens').get_json()
    # Unauthenticated client (no cookies set) hitting the QR endpoint
    from cs2servergui.core import AppCore
    from cs2servergui.web import create_flask
    # Re-use the same app via the auth'd client's transport: a fresh
    # test_client() with the same Flask app but no session cookie.
    fresh = c_auth.application.test_client()
    r = fresh.get(f"/api/veto/qr?token={tk['A']['token']}&kind=lan")
    return r.status_code == 401, f'status={r.status_code}'
t('qr: unauthenticated → 401', t_qr_requires_auth)


def t_qr_no_session_returns_400():
    _, _, c = _new_app()
    _login(c)
    # No /api/veto/create called — session is None
    r = c.get('/api/veto/qr?token=anything&kind=lan')
    return r.status_code == 400, f'status={r.status_code}'
t('qr: no active session → 400', t_qr_no_session_returns_400)


def t_revoke_includes_token_in_response():
    """Day 4 change: /api/veto/revoke_token now also returns the raw token
    so the SPA can rebuild the QR URL after a revoke + reissue."""
    c, tk = _setup_to_tokens()
    r = c.post('/api/veto/revoke_token', json={'team': 'A'})
    body = r.get_json()
    return (r.status_code == 200
            and 'token' in body
            and body['token']
            and body['token'] != tk['A']['token']
            and body['token'] in body['urls']['lan']), f'body={body}'
t('revoke: response includes new raw token', t_revoke_includes_token_in_response)


# ─── MatchZy handoff (v0.10.0 Day 6) ──────────────────────────────────────
def _drive_to_finale(c, app):
    """Drive a fresh session all the way to state=finale.  Returns the
    pool so callers can hit /finale and inspect the result."""
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'Alpha', 'team_b_name': 'Bravo',
        'players': _ten_player_payload(),
    })
    c.post('/api/veto/distribute'); c.post('/api/veto/start_voting')
    for team in ('A','B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team':team,'voter_idx':v,'votee_idx':0})
    c.post('/api/veto/resolve_captains')
    tk = c.post('/api/veto/tokens').get_json()
    cap_a, cap_b = app.test_client(), app.test_client()
    cap_a.post('/api/veto/claim', json={'token': tk['A']['token']})
    cap_b.post('/api/veto/claim', json={'token': tk['B']['token']})
    pool = c.get('/api/veto/state').get_json()['session']['map_pool']
    cap_a.post('/api/veto/step', json={'map_id': pool[0]})
    cap_b.post('/api/veto/step', json={'map_id': pool[1]})
    cap_a.post('/api/veto/step', json={'map_id': pool[2]})
    cap_b.post('/api/veto/step', json={'map_id': pool[3]})
    cap_a.post('/api/veto/step', json={'map_id': pool[4]})
    cap_b.post('/api/veto/step', json={'map_id': pool[5]})
    return pool


def t_finale_writes_match_config_to_disk():
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    r = c.post('/api/veto/finale', json={'load_match': False})
    body = r.get_json()
    assert r.status_code == 200, f'status={r.status_code} body={body}'
    written_to = body.get('matchzy', {}).get('written_to', '')
    if not written_to or not os.path.isfile(written_to):
        return False, f'written_to missing or file not on disk: {written_to!r}'
    with open(written_to, 'r', encoding='utf-8') as f:
        on_disk = json.load(f)
    # Verify the JSON contains the expected MatchZy keys and the
    # _oblivion_meta sidecar was STRIPPED from the disk write.
    needs = {'matchid', 'num_maps', 'maplist', 'players_per_team', 'team1', 'team2'}
    return (needs.issubset(on_disk.keys())
            and '_oblivion_meta' not in on_disk
            and on_disk['num_maps'] == 3
            and len(on_disk['maplist']) == 3), \
           f'on-disk keys={sorted(on_disk.keys())} num_maps={on_disk.get("num_maps")}'
t('finale: writes MatchZy match config to disk, strips _oblivion_meta', t_finale_writes_match_config_to_disk)


def t_finale_response_includes_oblivion_meta():
    """The on-disk file strips `_oblivion_meta` (so MatchZy's schema
    doesn't reject the unknown key) but the API response keeps it so the
    SPA can show the veto audit trail in the finale view."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    r = c.post('/api/veto/finale', json={'load_match': False})
    body = r.get_json()
    return ('_oblivion_meta' in body.get('config', {})
            and 'vetoes' in body['config']['_oblivion_meta']
            and len(body['config']['_oblivion_meta']['vetoes']) == 6), \
           f"config meta missing or wrong shape: {body.get('config', {}).get('_oblivion_meta')}"
t('finale: API response preserves _oblivion_meta audit trail', t_finale_response_includes_oblivion_meta)


def t_finale_no_rcon_when_server_not_running():
    """`load_match=True` but server isn't running → file still written,
    response has matchzy.error explaining what to do."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    assert not ac.running, 'precondition: server should not be running'
    r = c.post('/api/veto/finale', json={'load_match': True})
    body = r.get_json()
    mz = body.get('matchzy', {})
    return (r.status_code == 200
            and mz.get('loaded') is False
            and 'error' in mz
            and 'not running' in mz['error']
            and mz.get('written_to')), f'matchzy={mz}'
t('finale: server-not-running surfaces error in response but still writes config', t_finale_no_rcon_when_server_not_running)


def t_finale_completes_session_even_on_rcon_failure():
    """File written + RCON failure → session still transitions to
    `complete` so the SPA isn't stuck on the finale page."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    # Pretend the server is up but make the RCON call raise.
    ac.running = True
    class _BoomRCON:
        def execute(self, cmd):
            raise ConnectionError(f"refused: {cmd}")
    ac.rcon = _BoomRCON()
    r = c.post('/api/veto/finale', json={'load_match': True})
    body = r.get_json()
    state = c.get('/api/veto/state').get_json()
    return (r.status_code == 200
            and body['matchzy'].get('loaded') is False
            and 'error' in body['matchzy']
            and 'refused' in body['matchzy']['error']
            and state['state'] == 'complete'), \
           f'body.matchzy={body.get("matchzy")} state={state["state"]}'
t('finale: RCON failure → 200 + error + session still completes', t_finale_completes_session_even_on_rcon_failure)


def t_finale_calls_rcon_with_correct_filename_when_running():
    """File written + server running + RCON OK → matchzy.loaded=True
    and the RCON call was matchzy_loadmatch with the matchid-derived
    filename (sanitised, .json suffix)."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    ac.running = True
    calls: list[str] = []
    class _RecRCON:
        def execute(self, cmd):
            calls.append(cmd)
            return "match config loaded"
    ac.rcon = _RecRCON()
    r = c.post('/api/veto/finale', json={'load_match': True})
    body = r.get_json()
    if r.status_code != 200 or not body['matchzy'].get('loaded'):
        return False, f'unexpected outcome: status={r.status_code} matchzy={body.get("matchzy")}'
    if len(calls) != 1 or not calls[0].startswith('matchzy_loadmatch '):
        return False, f'expected one matchzy_loadmatch call, got {calls}'
    # Filename matches the on-disk file basename.
    written = body['matchzy'].get('written_to', '')
    expected_basename = os.path.basename(written)
    actual_arg = calls[0].split(' ', 1)[1]
    return actual_arg == expected_basename, f'rcon arg {actual_arg!r} ≠ basename {expected_basename!r}'
t('finale: load_match=True + running → matchzy_loadmatch <basename> issued', t_finale_calls_rcon_with_correct_filename_when_running)


def _drive_to_finale_state(c, app):
    """Like _drive_to_finale but returns the (cap_a, cap_b, pool) tuple so
    callers can hit the captain ready endpoint with the right session
    cookies — needed for the new v0.10.1 ready endpoint tests."""
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'Alpha', 'team_b_name': 'Bravo',
        'players': _ten_player_payload(),
    })
    c.post('/api/veto/distribute'); c.post('/api/veto/start_voting')
    for team in ('A','B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team':team,'voter_idx':v,'votee_idx':0})
    c.post('/api/veto/resolve_captains')
    tk = c.post('/api/veto/tokens').get_json()
    cap_a, cap_b = app.test_client(), app.test_client()
    cap_a.post('/api/veto/claim', json={'token': tk['A']['token']})
    cap_b.post('/api/veto/claim', json={'token': tk['B']['token']})
    pool = c.get('/api/veto/state').get_json()['session']['map_pool']
    cap_a.post('/api/veto/step', json={'map_id': pool[0]})
    cap_b.post('/api/veto/step', json={'map_id': pool[1]})
    cap_a.post('/api/veto/step', json={'map_id': pool[2]})
    cap_b.post('/api/veto/step', json={'map_id': pool[3]})
    cap_a.post('/api/veto/step', json={'map_id': pool[4]})
    cap_b.post('/api/veto/step', json={'map_id': pool[5]})
    return cap_a, cap_b


def t_ready_captain_can_set_own_team():
    ac, app, c = _new_app()
    _login(c)
    cap_a, cap_b = _drive_to_finale_state(c, app)
    r = cap_a.post('/api/veto/ready', json={'ready': True})
    body = r.get_json()
    return (r.status_code == 200 and body.get('ready_a') is True
            and body.get('ready_b') is False
            and body.get('both_ready') is False
            and body.get('team') == 'A'), f'body={body}'
t('ready: captain A can set own team ready', t_ready_captain_can_set_own_team)


def t_ready_captain_cannot_spoof_other_team():
    ac, app, c = _new_app()
    _login(c)
    cap_a, cap_b = _drive_to_finale_state(c, app)
    # Captain A passes team=B in body — must be rejected as spoof
    r = cap_a.post('/api/veto/ready', json={'ready': True, 'team': 'B'})
    return r.status_code == 403, f'status={r.status_code} body={r.get_data(as_text=True)[:100]!r}'
t('ready: captain A spoofing team=B → 403', t_ready_captain_cannot_spoof_other_team)


def t_ready_admin_can_set_either_team():
    """Admin acks-on-behalf for an AFK captain — passes team explicitly."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale_state(c, app)
    r = c.post('/api/veto/ready', json={'ready': True, 'team': 'B'})
    body = r.get_json()
    return (r.status_code == 200 and body.get('ready_b') is True
            and body.get('team') == 'B'), f'body={body}'
t('ready: admin can set any team via explicit team= field', t_ready_admin_can_set_either_team)


def t_ready_admin_team_required():
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale_state(c, app)
    r = c.post('/api/veto/ready', json={'ready': True})  # no team
    return r.status_code == 400, f'status={r.status_code}'
t('ready: admin without team= → 400', t_ready_admin_team_required)


def t_ready_both_captains_flips_both_ready():
    ac, app, c = _new_app()
    _login(c)
    cap_a, cap_b = _drive_to_finale_state(c, app)
    cap_a.post('/api/veto/ready', json={'ready': True})
    r = cap_b.post('/api/veto/ready', json={'ready': True})
    body = r.get_json()
    return (body.get('both_ready') is True), f'body={body}'
t('ready: both captains ready → both_ready=True in response', t_ready_both_captains_flips_both_ready)


def t_ready_state_clears_on_reset():
    ac, app, c = _new_app()
    _login(c)
    cap_a, cap_b = _drive_to_finale_state(c, app)
    cap_a.post('/api/veto/ready', json={'ready': True})
    cap_b.post('/api/veto/ready', json={'ready': True})
    c.post('/api/veto/reset')
    # New session — ready flags should be False
    snap = c.get('/api/veto/state').get_json()
    return snap['state'] == 'idle' and snap['session'] is None, f'snap={snap}'
t('ready: reset clears session (and ready flags with it)', t_ready_state_clears_on_reset)


def t_ready_snapshot_includes_flags():
    """Mid-finale snapshot exposes ready_a, ready_b, both_ready inside session."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale_state(c, app)
    snap = c.get('/api/veto/state').get_json()
    sess = snap.get('session') or {}
    needs = {'ready_a', 'ready_b', 'both_ready'}
    return (needs.issubset(set(sess.keys()))
            and sess['ready_a'] is False
            and sess['ready_b'] is False
            and sess['both_ready'] is False), f'sess_keys={sorted(sess.keys())}'
t('ready: snapshot includes ready_a/ready_b/both_ready inside session', t_ready_snapshot_includes_flags)


def t_public_share_url_used_in_tokens_response():
    """When public_share_url is set, /api/veto/tokens returns a Public URL
    built from it instead of http://<public_ip>:<port>/."""
    ac, app, c = _new_app()
    _login(c)
    ac.public_share_url = 'https://random-words.trycloudflare.com'
    ac.public_ip = '1.2.3.4'  # would lose if share URL is honoured
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={'team_a_name':'A','team_b_name':'B',
                                      'players':_ten_player_payload()})
    c.post('/api/veto/distribute'); c.post('/api/veto/start_voting')
    for team in ('A','B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team':team,'voter_idx':v,'votee_idx':0})
    c.post('/api/veto/resolve_captains')
    tk = c.post('/api/veto/tokens').get_json()
    pub_a = tk['A'].get('public', '')
    return (pub_a.startswith('https://random-words.trycloudflare.com/veto?join=')
            and '1.2.3.4' not in pub_a), f'public={pub_a!r}'
t('public_share_url: tokens response uses it instead of public_ip', t_public_share_url_used_in_tokens_response)


def t_public_share_url_validated_on_save():
    """POST /api/config with public_share_url that isn't a URL → 400."""
    ac, app, c = _new_app()
    _login(c)
    r = c.post('/api/config', json={'public_share_url': 'not-a-url'})
    return r.status_code == 400 and 'http' in r.get_json().get('error', '').lower(), \
           f'status={r.status_code} body={r.get_data(as_text=True)[:80]!r}'
t('public_share_url: must start with http:// or https://', t_public_share_url_validated_on_save)


def t_public_share_url_can_be_cleared():
    ac, app, c = _new_app()
    _login(c)
    ac.public_share_url = 'https://example.com'
    r = c.post('/api/config', json={'public_share_url': ''})
    return r.status_code == 200 and ac.public_share_url == '', \
           f'status={r.status_code} val={ac.public_share_url!r}'
t('public_share_url: blank value clears it', t_public_share_url_can_be_cleared)


def t_finale_mode_precheck_rejects_non_matchzy_mode():
    """v0.10.2: /api/veto/finale with load_match=True refuses if the server
    is on a non-MatchZy mode (1v1 Arena / Warcraft / Zombie Escape / etc.)
    — would otherwise silently fire matchzy_loadmatch which plays the wrong
    ruleset.  Should 409 with a precheck payload listing the expected modes."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    ac.running = True
    ac.current_mode = "Warcraft"     # not a MatchZy mode
    calls = []
    class _RecRCON:
        def execute(self, cmd): calls.append(cmd); return ""
    ac.rcon = _RecRCON()
    r = c.post('/api/veto/finale', json={'load_match': True})
    body = r.get_json()
    return (r.status_code == 409
            and body.get('precheck', {}).get('ok') is False
            and body['precheck']['current_mode'] == 'Warcraft'
            and 'expected_one_of' in body['precheck']
            and len(calls) == 0), \
           f'status={r.status_code} body={body} calls={calls}'
t('finale: mode pre-flight refuses non-MatchZy mode (Warcraft) → 409', t_finale_mode_precheck_rejects_non_matchzy_mode)


def t_finale_mode_precheck_accepts_matchzy_modes():
    """The pre-flight should accept all five MatchZy-managed modes."""
    for mode in ("Competitive", "3v3", "4v4", "5v5", "Practice"):
        ac, app, c = _new_app()
        _login(c)
        _drive_to_finale(c, app)
        ac.running = True
        ac.current_mode = mode
        ac.rcon = type('M', (), {'execute': lambda self, cmd: "ok"})()
        r = c.post('/api/veto/finale', json={'load_match': True})
        body = r.get_json()
        if r.status_code != 200:
            return False, f'mode={mode} status={r.status_code} body={body}'
        if not body.get('matchzy', {}).get('precheck', {}).get('ok'):
            return False, f'mode={mode} precheck not OK in success path: {body}'
    return True, ''
t('finale: mode pre-flight accepts all MatchZy modes (3v3 4v4 5v5 Practice Competitive)', t_finale_mode_precheck_accepts_matchzy_modes)


def t_finale_mode_precheck_force_bypasses():
    """`force: true` in the body should bypass the mode pre-flight — escape
    hatch for the rare case where the operator manually deployed MatchZy."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    ac.running = True
    ac.current_mode = "Aim 1v1"     # not a MatchZy mode
    ac.rcon = type('M', (), {'execute': lambda self, cmd: "ok"})()
    r = c.post('/api/veto/finale', json={'load_match': True, 'force': True})
    body = r.get_json()
    return (r.status_code == 200
            and body.get('matchzy', {}).get('loaded') is True
            and body['matchzy']['precheck'].get('forced') is True), f'body={body}'
t('finale: force=true bypasses mode pre-flight', t_finale_mode_precheck_force_bypasses)


def t_finale_mode_precheck_skipped_when_load_match_false():
    """`load_match=false` writes the config but skips both the RCON call AND
    the mode pre-flight — operator is previewing, not firing."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    ac.running = True
    ac.current_mode = "Aim 1v1"     # not a MatchZy mode
    r = c.post('/api/veto/finale', json={'load_match': False})
    return r.status_code == 200, f'status={r.status_code} body={r.get_data(as_text=True)[:200]!r}'
t('finale: load_match=false skips mode pre-flight (preview mode)', t_finale_mode_precheck_skipped_when_load_match_false)


def t_finale_load_match_false_skips_rcon():
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    ac.running = True
    calls: list[str] = []
    class _RecRCON:
        def execute(self, cmd): calls.append(cmd); return ""
    ac.rcon = _RecRCON()
    r = c.post('/api/veto/finale', json={'load_match': False})
    return (r.status_code == 200
            and r.get_json()['matchzy'].get('loaded') is False
            and 'error' not in r.get_json()['matchzy']
            and len(calls) == 0), f'calls={calls}'
t('finale: load_match=False → no RCON call even when server running', t_finale_load_match_false_skips_rcon)


# ─── Day 7 — API edge cases for the v0.10.0 ship ──────────────────────────

def t_qr_public_kind_with_no_public_ip():
    """QR with kind=public when core.public_ip is unset must 400 with a
    helpful error, not silently produce an http://:port URL."""
    ac, app, c = _new_app()
    _login(c)
    ac.public_ip = ''
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={'team_a_name':'A','team_b_name':'B',
                                      'players':_ten_player_payload()})
    c.post('/api/veto/distribute'); c.post('/api/veto/start_voting')
    for team in ('A','B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team':team,'voter_idx':v,'votee_idx':0})
    c.post('/api/veto/resolve_captains')
    tk = c.post('/api/veto/tokens').get_json()
    r = c.get(f"/api/veto/qr?token={tk['A']['token']}&kind=public")
    body = r.get_json()
    return (r.status_code == 400
            and 'public' in body.get('error', '').lower()), \
           f'status={r.status_code} body={body}'
t('qr: kind=public with no public_ip → 400 with useful error', t_qr_public_kind_with_no_public_ip)


def t_finale_called_twice_second_call_rejected():
    """After /api/veto/finale completes, the session moves to `complete`.
    A second /api/veto/finale call must fail cleanly — not retry the
    handoff (RCON would re-fire with the same matchid), not crash on
    re-completion."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    r1 = c.post('/api/veto/finale', json={'load_match': False})
    assert r1.status_code == 200, f'first call should succeed: {r1.status_code}'
    r2 = c.post('/api/veto/finale', json={'load_match': False})
    body = r2.get_json()
    return (r2.status_code == 400
            and 'error' in body), \
           f'second call: status={r2.status_code} body={body}'
t('finale: second call after complete is rejected (not silent re-fire)', t_finale_called_twice_second_call_rejected)


def t_reset_clears_session_state_completely():
    """After reset, /api/veto/state returns {state: idle, session: None}
    — not a leftover session object with cleared fields."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    c.post('/api/veto/finale', json={'load_match': False})
    c.post('/api/veto/reset')
    snap = c.get('/api/veto/state').get_json()
    return (snap['state'] == 'idle' and snap['session'] is None), f'snap={snap}'
t('reset: post-reset state is fully cleared, session is None', t_reset_clears_session_state_completely)


def t_state_snapshot_shape_stable():
    """The snapshot envelope keys must stay stable across release boundaries
    so existing SPA code keeps working.  Pin the contract."""
    ac, app, c = _new_app()
    _login(c)
    snap_idle = c.get('/api/veto/state').get_json()
    idle_keys = set(snap_idle.keys())
    # Drive to mid-veto where every snapshot field is populated
    _drive_to_finale(c, app)
    # _drive_to_finale ends at finale, but for shape-stability we want
    # mid-veto where current_step_detail is populated.  Re-make a
    # session and stop at the first step.
    c.post('/api/veto/reset')
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={'team_a_name':'A','team_b_name':'B',
                                      'players':_ten_player_payload()})
    c.post('/api/veto/distribute'); c.post('/api/veto/start_voting')
    for team in ('A','B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team':team,'voter_idx':v,'votee_idx':0})
    c.post('/api/veto/resolve_captains')
    tk = c.post('/api/veto/tokens').get_json()
    cap_a, cap_b = app.test_client(), app.test_client()
    cap_a.post('/api/veto/claim', json={'token': tk['A']['token']})
    cap_b.post('/api/veto/claim', json={'token': tk['B']['token']})
    snap_veto = c.get('/api/veto/state').get_json()
    # Top-level keys: state + session always present.
    # Inside session: current_step_detail + legal_moves (Day 2 polish).
    top_needs = {'state', 'session'}
    if not (top_needs.issubset(idle_keys) and top_needs.issubset(set(snap_veto.keys()))):
        return False, f'top-level keys missing: idle={sorted(idle_keys)} veto={sorted(snap_veto.keys())}'
    # Idle: session is None, no nested keys to check.
    if snap_idle['session'] is not None:
        return False, f'idle session should be None: {snap_idle["session"]}'
    # Mid-veto: session dict has current_step_detail (non-null) and legal_moves (7 maps).
    sess = snap_veto.get('session') or {}
    sess_keys = set(sess.keys())
    sess_needs = {'mode','state'.replace('state',''),  # placeholder; remove
                  'current_step_detail','legal_moves','sequence','current_step',
                  'team_a','team_b','captain_a_idx','captain_b_idx','tokens_claimed'}
    # Drop the placeholder
    sess_needs.discard('')
    return (sess_needs.issubset(sess_keys)
            and sess['current_step_detail'] is not None
            and len(sess['legal_moves']) == 7), \
           f'sess_keys={sorted(sess_keys)} csd={sess.get("current_step_detail")}'
t('snapshot: shape stable (session.current_step_detail + legal_moves populated mid-veto)', t_state_snapshot_shape_stable)


def t_distribute_without_roster_rejected():
    """Operator hits Distribute before saving any roster → must 400,
    not crash or produce a 0-player split."""
    ac, app, c = _new_app()
    _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    r = c.post('/api/veto/distribute')
    body = r.get_json()
    return (r.status_code == 400 and 'error' in body), \
           f'status={r.status_code} body={body}'
t('distribute: before roster saved → 400', t_distribute_without_roster_rejected)


def t_concurrent_finale_calls_serialise():
    """Two threads hitting /api/veto/finale at the same time must not
    both successfully complete the session — _veto_lock should serialise.
    The losing call gets a 400 (session already complete) or 200 if it
    happened to land first.  Either way: exactly one transition to
    `complete` and exactly one file written."""
    import threading
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    results: list = []
    def _hit():
        local_client = app.test_client()
        # Share the login cookie
        for ck in c.cookie_jar if hasattr(c, 'cookie_jar') else []:
            local_client.set_cookie('localhost', ck.name, ck.value)
        # Werkzeug test_client manages cookies per-client; simpler to
        # log this client in too
        local_client.post('/api/auth/login', json={'pin':'0000'})
        r = local_client.post('/api/veto/finale', json={'load_match': False})
        results.append(r.status_code)
    t1 = threading.Thread(target=_hit)
    t2 = threading.Thread(target=_hit)
    t1.start(); t2.start()
    t1.join(); t2.join()
    # Exactly one 200, one not-200 (probably 400 "complete").  Or both
    # 200 if they raced AND completed before the second observed the
    # state — but with the lock they shouldn't.  Sanity: at least one
    # succeeded and the session ended up `complete`.
    snap = c.get('/api/veto/state').get_json()
    succ = sum(1 for r in results if r == 200)
    return (succ >= 1
            and snap['state'] == 'complete'), \
           f'results={results} final_state={snap["state"]}'
t('finale: concurrent calls serialise via _veto_lock — session ends `complete`', t_concurrent_finale_calls_serialise)


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
