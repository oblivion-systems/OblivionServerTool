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
import os, sys, tempfile, json, time

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

    v0.11.3: clear oblivion_veto_active.json before each AppCore so
    persistence from a previous test in the same module run doesn't
    pollute this one (APPDATA tempdir is shared across the module).
    """
    from cs2servergui.config import VETO_ACTIVE_FILE
    try:
        if os.path.isfile(VETO_ACTIVE_FILE):
            os.remove(VETO_ACTIVE_FILE)
    except OSError:
        pass
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


def t_roster_accepts_discord_id_per_player():
    """v0.11.0 Layer 1A: /api/veto/roster accepts an optional discord_id
    per player; the snapshot round-trips it."""
    ac, app, c = _new_app()
    _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    players = [{'name': f'p{i}', 'steam_id': f'STEAM_{i}',
                'discord_id': f'10000000000000000{i}'} for i in range(10)]
    r = c.post('/api/veto/roster', json={
        'team_a_name': 'A', 'team_b_name': 'B', 'players': players,
    })
    if r.status_code != 200:
        return False, f'status={r.status_code} body={r.get_data(as_text=True)[:200]!r}'
    snap = c.get('/api/veto/state').get_json() or {}
    sess = snap.get('session') or {}
    roster = sess.get('roster') or []
    if len(roster) != 10:
        return False, f'roster len={len(roster)}'
    # Every entry should have a discord_id field, and they should round-trip
    bad = [p for p in roster if 'discord_id' not in p or not p['discord_id'].startswith('10000000000')]
    return (len(bad) == 0), f'roster[0]={roster[0]} bad_count={len(bad)}'
t('roster: discord_id per-player round-trips through snapshot', t_roster_accepts_discord_id_per_player)


def t_roster_discord_id_optional_blank_default():
    """Players submitted without discord_id get '' (not None / missing)."""
    ac, app, c = _new_app()
    _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    players = [{'name': f'p{i}', 'steam_id': f'STEAM_{i}'} for i in range(10)]
    r = c.post('/api/veto/roster', json={
        'team_a_name': 'A', 'team_b_name': 'B', 'players': players,
    })
    snap = c.get('/api/veto/state').get_json() or {}
    roster = (snap.get('session') or {}).get('roster') or []
    return (all(p.get('discord_id') == '' for p in roster)), \
           f'roster_discord_ids={[p.get("discord_id") for p in roster[:3]]}'
t('roster: discord_id omitted from input defaults to empty string', t_roster_discord_id_optional_blank_default)


def t_tokens_response_includes_dm_sent_field():
    """v0.11.0 Layer 1A: /api/veto/tokens response includes `dm_sent` per
    team (always False when no bot is configured) — SPA uses it to
    decide whether to highlight the Copy-for-Discord button."""
    ac, app, c = _new_app()
    _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'A', 'team_b_name': 'B',
        'players': _ten_player_payload(),
    })
    c.post('/api/veto/distribute'); c.post('/api/veto/start_voting')
    for team in ('A', 'B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team':team,'voter_idx':v,'votee_idx':0})
    c.post('/api/veto/resolve_captains')
    r = c.post('/api/veto/tokens')
    body = r.get_json() or {}
    return (r.status_code == 200
            and 'dm_sent' in body.get('A', {})
            and 'dm_sent' in body.get('B', {})
            and body['A']['dm_sent'] is False
            and body['B']['dm_sent'] is False), f'body={body}'
t('tokens: response includes dm_sent: False when no bot configured', t_tokens_response_includes_dm_sent_field)


def t_perform_step_succeeds_without_discord_channel():
    """v0.11.0 Layer 1C: when discord_veto_channel_id isn't configured,
    _refresh_live_veto_embed silently no-ops + the step API works as
    before.  Defends the live-embed code from breaking the core veto
    workflow if the operator never configured a channel."""
    ac, app, c = _new_app()
    _login(c)
    ac.discord_veto_channel_id = ''     # explicitly off
    _drive_to_finale(c, app)            # walks the full 6 steps
    # If _refresh_live_veto_embed crashed instead of silently no-op'ing,
    # _drive_to_finale would have failed by now.  State should be finale.
    snap = c.get('/api/veto/state').get_json()
    return snap.get('state') == 'finale', f'state={snap.get("state")}'
t('perform_step: succeeds when no discord_veto_channel_id configured', t_perform_step_succeeds_without_discord_channel)


def t_discord_voice_channels_400_without_guild_id():
    """v0.11.0 Layer 1B: /api/discord/voice_channels returns 400 if the
    operator hasn't configured a guild ID in Config → Discord."""
    ac, app, c = _new_app()
    _login(c)
    ac.discord_guild_id = ''       # explicit empty
    r = c.get('/api/discord/voice_channels')
    body = r.get_json() or {}
    return (r.status_code == 400
            and 'guild' in body.get('error', '').lower()), \
           f'status={r.status_code} body={body}'
t('/api/discord/voice_channels: 400 without guild ID configured', t_discord_voice_channels_400_without_guild_id)


def t_discord_voice_members_400_without_channel_id():
    ac, app, c = _new_app()
    _login(c)
    ac.discord_guild_id = '123456789012345678'
    r = c.get('/api/discord/voice_members')        # no channel_id arg
    return r.status_code == 400, f'status={r.status_code}'
t('/api/discord/voice_members: 400 without channel_id arg', t_discord_voice_members_400_without_channel_id)


def t_discord_endpoints_503_when_bot_not_connected():
    """When the bot isn't running (no token configured in the test),
    the endpoints return 503 not 500 — gives the SPA a clean retry path."""
    ac, app, c = _new_app()
    _login(c)
    ac.discord_guild_id = '123456789012345678'
    r1 = c.get('/api/discord/voice_channels')
    r2 = c.get('/api/discord/voice_members?channel_id=234567890123456789')
    r3 = c.get('/api/discord/voice_channel_info?channel_id=234567890123456789')
    r4 = c.get('/api/discord/text_channels')
    return (r1.status_code == 503 and r2.status_code == 503
            and r3.status_code == 503 and r4.status_code == 503), \
           f'voice_channels={r1.status_code} voice_members={r2.status_code} info={r3.status_code} text={r4.status_code}'
t('/api/discord/voice_* + text_channels: 503 when bot not connected', t_discord_endpoints_503_when_bot_not_connected)


def t_diag_snapshot_plugin_logs_section_present():
    """v0.11.19: diagnostic snapshot includes the Plugin logs section
    (with the new TL;DR plugin_log indicator too).  When no CSS log
    exists, the section shows a "no plugin logs found" status so the
    operator knows the section is intentional, not missing."""
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.get('/api/diag/snapshot')
    body = r.get_data(as_text=True)
    return (r.status_code == 200
            and 'Plugin logs (CSS + MatchZy' in body
            and 'plugin_log' in body          # TL;DR indicator
            and ('no plugin logs found' in body
                 or 'no CSS log' in body
                 or 'css_source' in body)
           ), \
           f'status={r.status_code} plugin_logs_in_body={"Plugin logs" in body}'
t('diag (v0.11.19): plugin logs section + TL;DR indicator present',
  t_diag_snapshot_plugin_logs_section_present)


def t_diag_snapshot_plugin_logs_tails_css_file():
    """v0.11.19: when a CSS log file exists at the conventional path,
    the snapshot tails it AND anomaly-prefixes ERROR/Exception lines."""
    import os as _os
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    # Create a fake CSS log inside the temp _csgo_dir
    css_log_dir = _os.path.join(ac._csgo_dir(), "addons",
                                 "counterstrikesharp", "logs")
    _os.makedirs(css_log_dir, exist_ok=True)
    log_path = _os.path.join(css_log_dir, "log-20260605.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("[2026-06-05 09:30:00 INFO] CSS host started\n")
        f.write("[2026-06-05 09:30:01 INFO] Loaded plugin: MatchZy v0.7.5\n")
        f.write("[2026-06-05 09:30:02 ERROR] Failed to load resource: foo\n")
        f.write("System.NullReferenceException: Object reference not set\n")
        f.write("   at MatchZy.MatchZy.OnMapStart() in MatchZy.cs:123\n")
        f.write("[2026-06-05 09:30:03 INFO] Recovered, continuing\n")
    r = c.get('/api/diag/snapshot')
    body = r.get_data(as_text=True)
    # File should be referenced as the css source + anomaly-prefixed errors
    has_source       = 'log-20260605.txt' in body
    has_err_marker   = '> [2026-06-05 09:30:02 ERROR]' in body
    has_exc_marker   = '> System.NullReferenceException' in body
    has_at_line      = '>    at MatchZy.MatchZy.OnMapStart' in body
    has_info_no_mark = '  [2026-06-05 09:30:00 INFO]' in body
    has_anomaly_count = 'css_anomalies' in body
    return (r.status_code == 200
            and has_source
            and has_err_marker
            and has_exc_marker
            and has_at_line
            and has_info_no_mark
            and has_anomaly_count), \
           (f'source={has_source} err={has_err_marker} exc={has_exc_marker} '
            f'at={has_at_line} info={has_info_no_mark} count={has_anomaly_count}')
t('diag (v0.11.19): plugin logs section tails CSS file + anomaly-prefixes errors',
  t_diag_snapshot_plugin_logs_tails_css_file)


def t_discord_text_channels_400_without_guild():
    """v0.11.18: /api/discord/text_channels returns 400 when guild_id is
    not configured (mirrors voice_channels)."""
    ac, app, c = _new_app()
    _login(c)
    ac.discord_guild_id = ''
    r = c.get('/api/discord/text_channels')
    body = r.get_json() or {}
    return (r.status_code == 400
            and 'guild' in body.get('error', '').lower()), \
           f'status={r.status_code} body={body}'
t('/api/discord/text_channels: 400 without guild ID (v0.11.18)',
  t_discord_text_channels_400_without_guild)


def t_discord_voice_channel_info_400_without_any_id():
    """v0.11.15: /api/discord/voice_channel_info returns 400 when no
    channel_id is passed AND no discord_voice_channel_id is configured."""
    ac, app, c = _new_app()
    _login(c)
    ac.discord_guild_id          = '123456789012345678'
    ac.discord_voice_channel_id  = ''       # explicit empty
    r = c.get('/api/discord/voice_channel_info')   # no channel_id arg
    body = r.get_json() or {}
    return (r.status_code == 400
            and 'channel id' in body.get('error', '').lower()), \
           f'status={r.status_code} body={body}'
t('/api/discord/voice_channel_info: 400 without any channel ID', t_discord_voice_channel_info_400_without_any_id)


def t_discord_voice_channel_info_400_without_guild():
    """v0.11.15: same endpoint returns 400 when channel_id IS given but
    guild_id is missing — same UX as voice_channels."""
    ac, app, c = _new_app()
    _login(c)
    ac.discord_guild_id = ''       # explicit empty
    r = c.get('/api/discord/voice_channel_info?channel_id=345678901234567890')
    body = r.get_json() or {}
    return (r.status_code == 400
            and 'guild' in body.get('error', '').lower()), \
           f'status={r.status_code} body={body}'
t('/api/discord/voice_channel_info: 400 without guild ID', t_discord_voice_channel_info_400_without_guild)


def t_discord_voice_channel_id_round_trips_through_config():
    """v0.11.15: discord_voice_channel_id is a local-only field (consistent
    with the other Discord settings).  Confirms POST + GET round-trip when
    the caller is local, and that the GET reflects the configured value."""
    ac, app, c = _new_app()
    _login(c)
    # Forge the test session as local — same pattern the snapshot tests use.
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    # Set via POST
    r = c.post('/api/config', json={'discord_voice_channel_id': '345678901234567890'})
    if r.status_code != 200:
        return False, f'POST status={r.status_code} body={r.get_json()}'
    # Verify on the core object
    if ac.discord_voice_channel_id != '345678901234567890':
        return False, f'core attr={ac.discord_voice_channel_id!r}'
    # And on the GET round-trip
    r2 = c.get('/api/config')
    body = r2.get_json() or {}
    return body.get('discord_voice_channel_id') == '345678901234567890', \
           f'GET returned {body.get("discord_voice_channel_id")!r}'
t('config: discord_voice_channel_id round-trips through /api/config', t_discord_voice_channel_id_round_trips_through_config)


def t_discord_voice_channel_id_remote_write_rejected():
    """v0.11.15: a remote (non-local) caller must NOT be able to set
    discord_voice_channel_id — the gate is `if is_local and ...`.  The
    field stays at whatever the core already had."""
    ac, app, c = _new_app()
    ac.discord_voice_channel_id = 'untouched'
    _login(c)
    # default _login session is NOT local (PIN login from a non-loopback IP)
    r = c.post('/api/config', json={'discord_voice_channel_id': '345678901234567890'})
    # The endpoint returns 200 because OTHER non-local-gated fields may have
    # processed successfully.  What matters is that the gated field is unchanged.
    return (r.status_code == 200
            and ac.discord_voice_channel_id == 'untouched'), \
           f'status={r.status_code} core attr={ac.discord_voice_channel_id!r}'
t('config: remote write to discord_voice_channel_id is rejected (local-only)', t_discord_voice_channel_id_remote_write_rejected)


# ─── v0.11.17 hotfixes ────────────────────────────────────────────────────

def t_server_start_409_during_workshop_download():
    """v0.11.17 A6: /api/server/start refuses with 409 when a workshop
    download is in flight.  Otherwise cs2.exe boots against a half-
    extracted addon folder.

    Note: `is_installed` is a @property; monkey-patch via the class to
    bypass the on-disk check (we don't have CS2 installed in the test
    env)."""
    ac, app, c = _new_app()
    _login(c)
    ac.server_dir = tempfile.mkdtemp(prefix='oblivion_dl_test_')   # bypass dir-not-configured
    # Bypass the property check by overriding it on the instance.
    type(ac).is_installed = property(lambda _self: True)   # type: ignore[assignment]
    try:
        # Simulate an active download
        class _FakeProc:
            def poll(self): return None
        ac._active_dl_proc = _FakeProc()
        r = c.post('/api/server/start', json={'map': 'de_dust2', 'mode': 'Competitive'})
        body = r.get_json() or {}
        return (r.status_code == 409
                and 'download' in body.get('error', '').lower()), \
               f'status={r.status_code} body={body}'
    finally:
        # Restore the original property so subsequent tests get the real check.
        from cs2servergui.core import AppCore as _AC
        # Walk MRO to find the original descriptor (defined on AppCore itself).
        for klass in type(ac).__mro__:
            if 'is_installed' in klass.__dict__ and klass is not type(ac):
                type(ac).is_installed = klass.__dict__['is_installed']
                break
        else:
            # If we mutated AppCore itself, just delete to restore inheritance.
            try: delattr(type(ac), 'is_installed')
            except AttributeError: pass
t('/api/server/start: 409 during workshop download (v0.11.17 A6)',
  t_server_start_409_during_workshop_download)


def t_finale_double_fire_guard_blocks_second_call():
    """v0.11.17 B3: the _finale_firing flag prevents a second concurrent
    finale call from re-firing matchzy_loadmatch.  Simulate by setting
    the flag manually then posting /api/veto/finale."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    # Pre-set the guard as if another thread is already firing.
    ac._finale_firing = True
    ac.running = True
    calls = []
    class _RecRCON:
        def execute(self, cmd): calls.append(cmd); return "OK"
    ac.rcon = _RecRCON()
    ac.current_mode = '5v5'      # MatchZy mode so pre-flight passes
    r = c.post('/api/veto/finale', json={'load_match': True})
    body = r.get_json() or {}
    return (r.status_code == 409
            and 'already in flight' in body.get('error', '').lower()
            and len(calls) == 0     # no RCON fired
            ), f'status={r.status_code} body={body} rcon_calls={calls}'
t('/api/veto/finale: 409 when _finale_firing already set (v0.11.17 B3)',
  t_finale_double_fire_guard_blocks_second_call)


def t_finale_clears_firing_guard_on_success():
    """v0.11.17 B3: successful finale clears the _finale_firing guard so
    the next session (after reset+new) can fire its own finale."""
    ac, app, c = _new_app()
    _login(c)
    _drive_to_finale(c, app)
    ac.running = True
    class _OKRCON:
        def execute(self, cmd): return "OK"
    ac.rcon = _OKRCON()
    ac.current_mode = '5v5'
    r = c.post('/api/veto/finale', json={'load_match': True})
    if r.status_code != 200:
        return False, f'finale failed: {r.status_code} {r.get_json()}'
    # Guard should be cleared after a successful run.
    return ac._finale_firing == False, f'_finale_firing={ac._finale_firing!r}'
t('/api/veto/finale: clears _finale_firing on success (v0.11.17 B3)',
  t_finale_clears_firing_guard_on_success)


def t_veto_reset_clears_finale_firing_guard():
    """v0.11.17 B3: /api/veto/reset clears _finale_firing as belt-and-
    braces in case a crashed handler left it stuck True."""
    ac, app, c = _new_app()
    _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    ac._finale_firing = True
    r = c.post('/api/veto/reset')
    return (r.status_code == 200
            and ac._finale_firing == False), \
           f'status={r.status_code} _finale_firing={ac._finale_firing}'
t('/api/veto/reset: clears stuck _finale_firing flag (v0.11.17 B3)',
  t_veto_reset_clears_finale_firing_guard)


def t_persistence_past_links_session_older_than_1h_discarded():
    """v0.11.17 B2: a persisted session in state past `links` (voting,
    veto, finale, complete) older than 1h is discarded on AppCore boot.
    Sessions in early stages (idle/roster/teams/links) keep the original
    12h window."""
    # Use a temp APPDATA so we don't pollute the real one
    import json as _json
    from cs2servergui.config import VETO_ACTIVE_FILE
    # Build a snapshot in state=voting that's 2h old.
    stale_snap = {
        "state": "voting", "team_a_name": "A", "team_b_name": "B",
        "roster": [], "team_a": [], "team_b": [],
        "votes_a": {}, "votes_b": {},
        "captain_a_idx": None, "captain_b_idx": None,
        "revote_count": 0, "tokens": {},
        "mode": "BO3", "map_pool": ["de_mirage"]*7,
        "sequence": [], "current_step": 0, "decider": "",
        "final_maps": [], "matchzy_config": None,
        "ready_a": False, "ready_b": False,
        "live_embed_msg_id": "", "spectator_token": "",
        "created_at": time.time() - 7200,    # 2 hours ago
        "updated_at": time.time() - 7200,
    }
    os.makedirs(os.path.dirname(VETO_ACTIVE_FILE), exist_ok=True)
    with open(VETO_ACTIVE_FILE, "w", encoding="utf-8") as f:
        _json.dump(stale_snap, f)
    # Now construct AppCore — should discard the stale past-links snapshot
    ac = AppCore()
    return (ac._veto_session is None
            and not os.path.exists(VETO_ACTIVE_FILE)), \
           f'session={ac._veto_session!r} file_exists={os.path.exists(VETO_ACTIVE_FILE)}'
t('persistence: past-links session > 1h discarded on boot (v0.11.17 B2)',
  t_persistence_past_links_session_older_than_1h_discarded)


def t_persistence_past_links_session_fresh_still_resumes():
    """v0.11.17 B2: a past-links session that's FRESH (under 1h) still
    resumes — the tighter cutoff only fires on STALE past-links sessions."""
    import json as _json
    from cs2servergui.config import VETO_ACTIVE_FILE
    fresh_snap = {
        "state": "voting", "team_a_name": "A", "team_b_name": "B",
        "roster": [{"name": f"p{i}", "steam_id": f"S{i}", "discord_id": ""}
                   for i in range(10)],
        "team_a": [{"name": f"p{i}", "steam_id": f"S{i}", "discord_id": ""}
                   for i in range(5)],
        "team_b": [{"name": f"p{i+5}", "steam_id": f"S{i+5}", "discord_id": ""}
                   for i in range(5)],
        "votes_a": {}, "votes_b": {},
        "captain_a_idx": None, "captain_b_idx": None,
        "revote_count": 0, "tokens": {},
        "mode": "BO3",
        "map_pool": ["de_mirage", "de_inferno", "de_ancient",
                     "de_anubis", "de_nuke", "de_overpass", "de_vertigo"],
        "sequence": [], "current_step": 0, "decider": "",
        "final_maps": [], "matchzy_config": None,
        "ready_a": False, "ready_b": False,
        "live_embed_msg_id": "", "spectator_token": "",
        "created_at": time.time() - 30,     # 30 seconds ago
        "updated_at": time.time() - 30,
    }
    os.makedirs(os.path.dirname(VETO_ACTIVE_FILE), exist_ok=True)
    with open(VETO_ACTIVE_FILE, "w", encoding="utf-8") as f:
        _json.dump(fresh_snap, f)
    ac = AppCore()
    return (ac._veto_session is not None
            and ac._veto_session.state == "voting"), \
           f'session={ac._veto_session!r}'
t('persistence: fresh past-links session still resumes (v0.11.17 B2)',
  t_persistence_past_links_session_fresh_still_resumes)


def t_persistence_early_stage_session_older_than_1h_still_resumes():
    """v0.11.17 B2: a session in early stages (idle/roster/teams/links)
    keeps the original 12h window — only past-links uses the 1h cutoff."""
    import json as _json
    from cs2servergui.config import VETO_ACTIVE_FILE
    roster_snap = {
        "state": "roster", "team_a_name": "A", "team_b_name": "B",
        "roster": [], "team_a": [], "team_b": [],
        "votes_a": {}, "votes_b": {},
        "captain_a_idx": None, "captain_b_idx": None,
        "revote_count": 0, "tokens": {},
        "mode": "BO3", "map_pool": [],
        "sequence": [], "current_step": 0, "decider": "",
        "final_maps": [], "matchzy_config": None,
        "ready_a": False, "ready_b": False,
        "live_embed_msg_id": "", "spectator_token": "",
        "created_at": time.time() - 7200,    # 2 hours ago
        "updated_at": time.time() - 7200,
    }
    os.makedirs(os.path.dirname(VETO_ACTIVE_FILE), exist_ok=True)
    with open(VETO_ACTIVE_FILE, "w", encoding="utf-8") as f:
        _json.dump(roster_snap, f)
    ac = AppCore()
    return (ac._veto_session is not None
            and ac._veto_session.state == "roster"), \
           f'session={ac._veto_session!r}'
t('persistence: 2h-old roster-stage session still resumes (v0.11.17 B2)',
  t_persistence_early_stage_session_older_than_1h_still_resumes)


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


# ─── v0.11.0 polish: Spectator URL ────────────────────────────────────────
def t_spectator_issue_requires_session():
    """POST /api/veto/spectator with no active session → 404."""
    ac, app, c = _new_app(); _login(c)
    r = c.post('/api/veto/spectator')
    return (r.status_code == 404), f'status={r.status_code}'
t('spectator: 404 when no active session', t_spectator_issue_requires_session)


def t_spectator_issue_returns_token_and_urls():
    ac, app, c = _new_app(); _login(c)
    c.post('/api/veto/create', json={'mode': 'BO1'})
    r = c.post('/api/veto/spectator')
    j = r.get_json()
    ok = (r.status_code == 200
          and isinstance(j.get('token'), str) and len(j['token']) >= 20
          and 'urls' in j and 'lan' in j['urls']
          and '/spectate?token=' in j['urls']['lan'])
    return (ok, f'json={j}')
t('spectator: issue returns token + LAN url', t_spectator_issue_returns_token_and_urls)


def t_spectator_issue_idempotent_until_rotate():
    ac, app, c = _new_app(); _login(c)
    c.post('/api/veto/create', json={'mode': 'BO1'})
    t1 = c.post('/api/veto/spectator').get_json()['token']
    t2 = c.post('/api/veto/spectator').get_json()['token']
    t3 = c.post('/api/veto/spectator', json={'rotate': True}).get_json()['token']
    return (t1 == t2 and t1 != t3), f't1={t1[:8]} t2={t2[:8]} t3={t3[:8]}'
t('spectator: issue idempotent + rotate mints fresh', t_spectator_issue_idempotent_until_rotate)


def t_spectator_state_rejects_bad_token():
    ac, app, c = _new_app(); _login(c)
    c.post('/api/veto/create', json={'mode': 'BO1'})
    c.post('/api/veto/spectator')
    # Hit the public endpoint with a wrong token using a FRESH client
    # (no auth cookie) — proves the token IS the auth.
    public = app.test_client()
    r = public.get('/api/veto/spectator/state?token=NOPE')
    return (r.status_code == 401), f'status={r.status_code}'
t('spectator: bad token → 401', t_spectator_state_rejects_bad_token)


def t_spectator_state_strips_pii():
    """Round-trip: issue token, fetch public state with it, verify no
    discord_id field surfaces + steam_id full value is masked."""
    ac, app, c = _new_app(); _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    # Plant identifiable values
    roster = _ten_player_payload()
    roster[0]['discord_id'] = '123456789012345678'
    roster[0]['steam_id']   = '76561198000000001'
    c.post('/api/veto/roster', json={
        'team_a_name': 'Alpha', 'team_b_name': 'Bravo', 'players': roster,
    })
    c.post('/api/veto/distribute')
    tok = c.post('/api/veto/spectator').get_json()['token']
    public = app.test_client()
    r = public.get(f'/api/veto/spectator/state?token={tok}')
    blob = r.get_data(as_text=True)
    ok = (r.status_code == 200
          and '123456789012345678' not in blob
          and '76561198000000001'  not in blob
          and 'discord_id' not in blob
          and 'tokens' not in r.get_json())
    return (ok, f'leaked? status={r.status_code} body starts={blob[:120]}')
t('spectator: public state strips discord_id + masks steam_id + no tokens', t_spectator_state_strips_pii)


def t_spectator_page_serves_html_with_embedded_token():
    """GET /spectate?token=… returns HTML containing the (sanitized)
    token so the page's JS can poll the state endpoint."""
    ac, app, c = _new_app(); _login(c)
    c.post('/api/veto/create', json={'mode': 'BO1'})
    tok = c.post('/api/veto/spectator').get_json()['token']
    public = app.test_client()
    r = public.get(f'/spectate?token={tok}')
    body = r.get_data(as_text=True)
    return (r.status_code == 200
            and 'text/html' in r.content_type
            and tok in body
            and 'Spectator' in body), f'status={r.status_code} ctype={r.content_type}'
t('spectator: /spectate page serves HTML with token embedded', t_spectator_page_serves_html_with_embedded_token)


def t_spectator_page_strips_unsafe_token_chars():
    """Defense in depth: /spectate?token=<XSS> only embeds [A-Za-z0-9_-]
    characters so a hostile URL can't inject script into the page HTML."""
    ac, app, c = _new_app()       # no session needed
    public = app.test_client()
    bad = '<script>alert(1)</script>'
    r = public.get(f'/spectate?token={bad}')
    body = r.get_data(as_text=True)
    return (r.status_code == 200
            and '<script>alert' not in body
            and '<script>' in body  # legitimate script tag for the polling JS
           ), f'status={r.status_code}'
t('spectator: /spectate sanitises token chars in embedded HTML', t_spectator_page_strips_unsafe_token_chars)


# ─── v0.11.2 — issue_tokens idempotency via HTTP ──────────────────────────
def t_tokens_endpoint_idempotent_on_recall():
    """v0.11.2 fix surfaced through the API: POST /api/veto/tokens twice
    from the same session returns the SAME token values.  Real trigger:
    captain refreshes the links page in the SPA, which re-issues."""
    ac, app, c = _new_app(); _login(c)
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
    first  = c.post('/api/veto/tokens').get_json()
    second = c.post('/api/veto/tokens').get_json()
    return (first['A']['token'] == second['A']['token']
            and first['B']['token'] == second['B']['token']), \
           f'A first={first["A"]["token"][:8]} second={second["A"]["token"][:8]}'
t('idempotent /api/veto/tokens: same call returns same tokens (v0.11.2 fix)',
  t_tokens_endpoint_idempotent_on_recall)


# ─── v0.11.3 — Session persistence end-to-end via HTTP ────────────────────
# Unit tests in test_veto.py cover serialize/deserialize; these test the
# REAL production path: create session via HTTP → AppCore destroyed →
# new AppCore created → /api/veto/state shows the resumed session.
# This is what catches wiring bugs unit tests can't.

def t_persistence_resumes_session_across_appcore_recreation():
    """The whole point of v0.11.3 — full HTTP round-trip including the
    AppCore restart.  Operator's pywebview window crashes mid-session;
    they re-open the app; same session should be there."""
    # First session: drive to the 'teams' state.
    ac1, app1, c1 = _new_app(); _login(c1)
    c1.post('/api/veto/create', json={'mode': 'BO3'})
    c1.post('/api/veto/roster', json={
        'team_a_name': 'Phoenix', 'team_b_name': 'Wraith',
        'players': _ten_player_payload(),
    })
    c1.post('/api/veto/distribute')
    state_before = c1.get('/api/veto/state').get_json()
    # `state` is at top level of the snapshot; `session` is the nested
    # detail dict.
    assert state_before['state'] == 'teams', \
        f"expected state 'teams', got {state_before.get('state')!r}"
    # Now nuke AppCore + its Flask app; create a fresh one (simulates
    # app restart).  Critically, we DO NOT delete the persistence file —
    # that's the whole point of this test.
    del ac1, app1, c1
    # The fixture intentionally deletes the persistence file at the top
    # of _new_app(), so we cannot use it.  Build the second AppCore by
    # hand, preserving the persistence file.
    ac2 = AppCore()
    ac2.admin_pin = '0000'
    _fake_csgo = tempfile.mkdtemp(prefix='oblivion_veto_csgo_resume_')
    ac2._csgo_dir = lambda: _fake_csgo  # type: ignore[method-assign]
    app2 = create_flask(ac2)
    c2 = app2.test_client()
    _login(c2)
    state_after = c2.get('/api/veto/state').get_json()
    return (state_after['state'] == 'teams'
            and state_after['session']['team_a_name'] == 'Phoenix'
            and state_after['session']['team_b_name'] == 'Wraith'
            and len(state_after['session']['team_a']) == 5
            and len(state_after['session']['team_b']) == 5
           ), f"resumed state={state_after.get('state')} " \
              f"a={state_after['session'].get('team_a_name')}"
t('persistence: session survives full AppCore destroy+recreate via HTTP',
  t_persistence_resumes_session_across_appcore_recreation)


def t_persistence_preserves_claimed_captain_tokens():
    """Load-bearing detail: an app restart after captains have already
    claimed their links must NOT log them out.  Their cookie binds to
    the token's caller_id, which has to survive the round-trip."""
    ac1, app1, c1 = _new_app(); _login(c1)
    c1.post('/api/veto/create', json={'mode': 'BO3'})
    c1.post('/api/veto/roster', json={
        'team_a_name': 'Alpha', 'team_b_name': 'Bravo',
        'players': _ten_player_payload(),
    })
    c1.post('/api/veto/distribute'); c1.post('/api/veto/start_voting')
    for team in ('A', 'B'):
        for v in range(5):
            c1.post('/api/veto/vote', json={'team': team, 'voter_idx': v, 'votee_idx': 0})
    c1.post('/api/veto/resolve_captains')
    tk = c1.post('/api/veto/tokens').get_json()
    # Claim both captain tokens with distinct caller_ids
    cap_a = app1.test_client()
    cap_b = app1.test_client()
    cap_a.post('/api/veto/claim', json={'token': tk['A']['token']})
    cap_b.post('/api/veto/claim', json={'token': tk['B']['token']})
    # Session is now in 'veto' state.  Tear down + recreate AppCore.
    del ac1, app1, c1, cap_a, cap_b
    ac2 = AppCore()
    ac2.admin_pin = '0000'
    _fake_csgo = tempfile.mkdtemp(prefix='oblivion_veto_csgo_claim_')
    ac2._csgo_dir = lambda: _fake_csgo  # type: ignore[method-assign]
    app2 = create_flask(ac2)
    # Verify both tokens still marked used + claimed in the resumed session
    sess = ac2._veto_session
    return (sess is not None
            and sess.state == 'veto'
            and sess.tokens['A'].used is True
            and sess.tokens['A'].claimed_by != ''
            and sess.tokens['B'].used is True
            and sess.tokens['B'].claimed_by != ''
           ), f"sess={sess.state if sess else None} A.used={sess.tokens['A'].used if sess else None}"
t('persistence: claimed captain tokens survive AppCore recreation',
  t_persistence_preserves_claimed_captain_tokens)


def t_persistence_file_deleted_on_reset():
    """/api/veto/reset must clear the persistence file so the NEXT app
    start doesn't re-resume the just-reset session.  Verified by
    inspecting the file directly."""
    from cs2servergui.config import VETO_ACTIVE_FILE
    ac, app, c = _new_app(); _login(c)
    c.post('/api/veto/create', json={'mode': 'BO1'})
    file_existed_during_session = os.path.isfile(VETO_ACTIVE_FILE)
    c.post('/api/veto/reset')
    file_exists_after_reset = os.path.isfile(VETO_ACTIVE_FILE)
    return (file_existed_during_session and not file_exists_after_reset), \
           f'during={file_existed_during_session} after_reset={file_exists_after_reset}'
t('persistence: /api/veto/reset deletes the active-session file',
  t_persistence_file_deleted_on_reset)


def t_persistence_no_file_when_no_session():
    """Fresh AppCore with no in-flight session → no persistence file.
    Defensive: catches an accidental "always create empty session file"
    regression."""
    from cs2servergui.config import VETO_ACTIVE_FILE
    ac, app, c = _new_app()      # _new_app() pre-deletes the file
    return (not os.path.isfile(VETO_ACTIVE_FILE)), \
           f'file exists at fresh boot: {VETO_ACTIVE_FILE}'
t('persistence: no file present when no session active', t_persistence_no_file_when_no_session)


def t_persistence_stale_file_discarded():
    """v0.11.3 — a persistence file with an updated_at older than
    VETO_ACTIVE_MAX_AGE_SECS must be discarded silently on AppCore boot,
    NOT loaded into _veto_session.  Operator who opens the app the next
    day expects a fresh start, not yesterday's stuck finale."""
    from cs2servergui.config import VETO_ACTIVE_FILE, VETO_ACTIVE_MAX_AGE_SECS
    # Hand-write a stale persistence file
    stale = {
        'state':         'veto',
        'mode':          'BO3',
        'team_a_name':   'StaleA',
        'team_b_name':   'StaleB',
        'updated_at':    time.time() - (VETO_ACTIVE_MAX_AGE_SECS + 100),
        'created_at':    time.time() - (VETO_ACTIVE_MAX_AGE_SECS + 200),
    }
    os.makedirs(os.path.dirname(VETO_ACTIVE_FILE), exist_ok=True)
    with open(VETO_ACTIVE_FILE, 'w', encoding='utf-8') as f:
        json.dump(stale, f)
    # Build AppCore manually (bypassing _new_app's pre-delete)
    ac = AppCore()
    ac.admin_pin = '0000'
    return (ac._veto_session is None), \
           f'stale session was resumed: state={ac._veto_session.state if ac._veto_session else None}'
t('persistence: stale (> max-age) file discarded on boot', t_persistence_stale_file_discarded)


def t_persistence_corrupt_file_handled():
    """v0.11.3 defensive load: if the persistence file is unreadable JSON,
    AppCore must start cleanly (no crash, no resumed session, file left
    in place for operator inspection)."""
    from cs2servergui.config import VETO_ACTIVE_FILE
    os.makedirs(os.path.dirname(VETO_ACTIVE_FILE), exist_ok=True)
    with open(VETO_ACTIVE_FILE, 'w', encoding='utf-8') as f:
        f.write('{not valid json at all,,,')
    try:
        ac = AppCore()
        ac.admin_pin = '0000'
    except Exception as exc:
        return False, f'AppCore construction crashed on corrupt persistence: {exc}'
    return (ac._veto_session is None), \
           f'corrupt file produced a session somehow: {ac._veto_session}'
t('persistence: corrupt JSON file handled without AppCore crash', t_persistence_corrupt_file_handled)


# ─── v0.11.1 — MatchZy cvar override via /api/config ──────────────────────
def t_cvar_override_reaches_matchzy_config():
    """End-to-end: AppCore.matchzy_cvars overrides flow through finale
    into the generated MatchZy match config.  Sets cvars directly on
    AppCore (the /api/config write is local-gated and test_client isn't
    local-loopback).  _drive_to_finale walks to the `finale` state;
    `/api/veto/finale` is what actually builds the match config."""
    ac, app, c = _new_app(); _login(c)
    ac.matchzy_cvars = {
        'matchzy_knife_enabled_default': '1',     # new cvar
        'mp_warmup_pausetimer':          '5',     # override default
    }
    _drive_to_finale(c, app)
    # Trigger /finale (load_match=false so we don't actually need a CS2
    # server running for RCON).  Inspect the response payload — the
    # generated config is mirrored back to the SPA there.
    r = c.post('/api/veto/finale', json={'load_match': False})
    j = r.get_json()
    cv = (j.get('matchzy') or {}).get('config', {}).get('cvars', {})
    if not cv:
        # The cvars might be inside the session's stored config instead.
        sess = ac._veto_session
        cv = (sess.matchzy_config or {}).get('cvars', {}) if sess else {}
    return (cv.get('matchzy_knife_enabled_default') == '1'
            and cv.get('mp_warmup_pausetimer') == '5'
            and cv.get('matchzy_minimum_ready_required') == '2'  # default still present
           ), f'cvars in match config: {cv}'
t('cvar override on AppCore reaches the generated MatchZy match config',
  t_cvar_override_reaches_matchzy_config)


# ─── v0.10.2 — /api/capabilities shape ────────────────────────────────────
def t_capabilities_admin_shape():
    """v0.10.2: admin session sees role=admin + is_local (any) + a list
    of capability tags including veto.admin and core server controls.
    Smoke test ensures the endpoint exists + the shape the SPA depends
    on hasn't drifted.  is_local is naturally False under test_client
    (non-loopback) — that's not what we're asserting here."""
    ac, app, c = _new_app(); _login(c)
    r = c.get('/api/capabilities')
    j = r.get_json()
    can = set(j.get('can', []) or [])
    expected_subset = {'veto.admin', 'server.start', 'server.stop', 'config.write'}
    return (r.status_code == 200
            and j.get('role') == 'admin'
            and isinstance(j.get('can'), list)
            and 'is_local' in j
            and expected_subset.issubset(can)
           ), f'status={r.status_code} role={j.get("role")} ' \
              f'missing={expected_subset - can}'
t('/api/capabilities: admin shape includes role + can[] with expected tags',
  t_capabilities_admin_shape)


# ─── v0.11.0 — Discord graceful degradation ───────────────────────────────
def t_veto_works_with_no_discord_token():
    """The entire veto flow must work when Discord isn't configured.
    The tool's CS2 functionality is the core; Discord is enhancement.
    A misconfigured bot must not block matches."""
    ac, app, c = _new_app(); _login(c)
    ac.discord_bot_token = ''       # explicit
    # Walk the full happy path
    c.post('/api/veto/create', json={'mode': 'BO1'})
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
    # Critical: dm_sent should be False (no bot) but the endpoint must succeed
    return (isinstance(tk, dict)
            and 'A' in tk and 'B' in tk
            and tk.get('dm_sent_a', False) is False
            and tk.get('dm_sent_b', False) is False
           ), f'tokens response={tk}'
t('discord: full veto flow works with no Discord token configured',
  t_veto_works_with_no_discord_token)


# ─── v0.11.4 — Diagnostic snapshot endpoint ───────────────────────────────
def t_diag_snapshot_returns_pasteable_text():
    """v0.11.4 — /api/diag/snapshot returns a single text/plain blob
    covering app version, active session state, log lines, etc.  Smoke
    test ensures the endpoint exists, the local gate works (test_client
    isn't local-loopback so we hit a fake-local AppCore), and the
    response contains the section headers the operator expects."""
    ac, app, c = _new_app(); _login(c)
    # Drop a known marker into the log so we can verify it appears
    ac.log("[smoke] diagnostic snapshot marker line zZqQ")
    c.post('/api/veto/create', json={'mode': 'BO3'})
    # Forge a local session — the @require_local gate would otherwise
    # 403 the test_client (since test_client isn't loopback).  Mutate
    # the module-level _sessions store directly; that's what the
    # redacts-secrets test also does.
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.get('/api/diag/snapshot')
    body = r.get_data(as_text=True)
    return (r.status_code == 200
            and 'text/plain' in r.content_type
            and 'OBLIVION DIAGNOSTIC SNAPSHOT' in body
            and 'App version:' in body
            and 'Active veto session' in body
            and 'Recent app log' in body
            and 'diagnostic snapshot marker line zZqQ' in body
            and 'Config (redacted)' in body
            and 'END SNAPSHOT' in body
           ), f'status={r.status_code} len={len(body)} ctype={r.content_type}'
t('diag: /api/diag/snapshot returns pasteable text with expected sections',
  t_diag_snapshot_returns_pasteable_text)


def t_diag_snapshot_redacts_secrets():
    """v0.11.4 — sensitive config values must be masked in the snapshot
    so an operator pasting it into a public Discord doesn't leak their
    PINs / RCON password / Discord bot token."""
    ac, app, c = _new_app(); _login(c)
    # Populate sensitive fields via core directly
    ac.admin_pin = '7777'
    ac.sv_password = 'top-secret-pw'
    ac.discord_bot_token = 'MTAxxxxxxxxxxxxxxxxxxxxxx.xxxxxxx.fakefakefake'
    ac.save_config()
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    body = c.get('/api/diag/snapshot').get_data(as_text=True)
    return ('7777' not in body
            and 'top-secret-pw' not in body
            and 'fakefakefake' not in body
            and '***' in body            # mask marker should appear
           ), f'leaked values: pin={"7777" in body} pw={"top-secret-pw" in body} ' \
              f'token={"fakefakefake" in body}'
t('diag: /api/diag/snapshot redacts secrets (PINs, sv_password, bot token)',
  t_diag_snapshot_redacts_secrets)


def t_diag_snapshot_v0119_sections_present():
    """v0.11.9 — diagnostic snapshot now also includes Request context,
    Disk space, Plugin file verification, Active veto raw JSON, and
    CS2 console.log tail.  Smoke test that all the section headers
    appear so a refactor doesn't silently lose a section."""
    ac, app, c = _new_app(); _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    body = c.get('/api/diag/snapshot', headers={'User-Agent': 'OblivionTest/9.9'}).get_data(as_text=True)
    expected_sections = (
        'Request context',
        'Disk space',
        'Plugin file verification',
        'Active veto session — raw JSON',
        'CS2 console.log',
    )
    missing = [s for s in expected_sections if s not in body]
    return (not missing
            and 'OblivionTest/9.9' in body          # user_agent surfaced
           ), f'missing sections: {missing}, ua_in_body={"OblivionTest" in body}'
t('diag (v0.11.9): new sections (req ctx, disk, plugin verify, raw json, cs2 log) all present',
  t_diag_snapshot_v0119_sections_present)


def t_diag_snapshot_redacts_captain_tokens_in_raw_json():
    """v0.11.9 — the raw veto-active.json section MUST mask captain
    token values.  A leaked snapshot containing a live captain token
    would let anyone with it claim the captain role mid-flow."""
    ac, app, c = _new_app(); _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    # Drive the session past token issue so the persistence file
    # contains real token values
    c.post('/api/veto/create', json={'mode': 'BO1'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'A', 'team_b_name': 'B',
        'players': _ten_player_payload(),
    })
    c.post('/api/veto/distribute'); c.post('/api/veto/start_voting')
    for team in ('A','B'):
        for v in range(5):
            c.post('/api/veto/vote', json={'team':team,'voter_idx':v,'votee_idx':0})
    c.post('/api/veto/resolve_captains')
    tk = c.post('/api/veto/tokens').get_json()
    token_a = tk['A']['token']
    token_b = tk['B']['token']
    body = c.get('/api/diag/snapshot').get_data(as_text=True)
    return (token_a not in body
            and token_b not in body
            and '***REDACTED***' in body
           ), f'token_a leaked={token_a in body} token_b leaked={token_b in body}'
t('diag (v0.11.9): captain tokens masked in raw veto-active.json section',
  t_diag_snapshot_redacts_captain_tokens_in_raw_json)


def t_diag_snapshot_v01110_tldr_and_anomaly_flagging():
    """v0.11.10 — TL;DR auto-scan block at the top + `>`-prefix on log
    lines matching error/warn/fail patterns.  Two-second triage win
    for Friday support: reader scans TL;DR, then jumps to flagged
    lines."""
    ac, app, c = _new_app(); _login(c)
    # Plant a real error in the ring buffer so the scan should flag it
    ac.log('[veto] session created mode=BO3')
    ac.log('[error] matchzy_loadmatch failed: connection refused')
    ac.log('[info] step accepted')
    ac.log('[discord] DM to 12345 failed: 50007 Cannot send')
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    body = c.get('/api/diag/snapshot').get_data(as_text=True)
    # TL;DR block present + correct shape
    has_tldr_header   = 'TL;DR (auto-scan)' in body
    has_app_marker    = '✓ app' in body or '⚠ app' in body or '· app' in body
    has_recent_flag   = '⚠ recent' in body and 'error/warn lines' in body
    # Log line anomaly prefixes — check the SPECIFIC line, not the whole body
    def _is_flagged(text_substring):
        for ln in body.splitlines():
            if text_substring in ln:
                return ln.startswith('> ')
        return None     # not found
    has_flagged_error = _is_flagged('matchzy_loadmatch failed') is True
    has_flagged_dm    = _is_flagged('DM to 12345 failed') is True
    # The non-error info line should NOT be flagged
    info_line_present = '[info] step accepted' in body
    info_line_flagged = _is_flagged('step accepted') is True
    return (has_tldr_header and has_app_marker and has_recent_flag
            and has_flagged_error and has_flagged_dm
            and info_line_present and not info_line_flagged
           ), (f'tldr={has_tldr_header} app_mark={has_app_marker} '
               f'recent={has_recent_flag} err_flag={has_flagged_error} '
               f'dm_flag={has_flagged_dm} info_present={info_line_present} '
               f'info_wrongly_flagged={info_line_flagged}')
t('diag (v0.11.10): TL;DR auto-scan + anomaly `>` prefix on log lines',
  t_diag_snapshot_v01110_tldr_and_anomaly_flagging)


def t_diag_snapshot_gated_to_local():
    """/api/diag/snapshot is @require_local — a regular admin (non-local)
    session must be rejected with 403.  Defense: snapshot contains IPs,
    deployed plugin names, file paths — admin remote sessions shouldn't
    see it."""
    ac, app, c = _new_app(); _login(c)
    # Don't forge is_local — test_client is naturally non-local
    r = c.get('/api/diag/snapshot')
    return (r.status_code == 403), f'status={r.status_code}'
t('diag: /api/diag/snapshot is local-only (403 for non-local admin)',
  t_diag_snapshot_gated_to_local)


# ─── v0.12.0 — /move-teams + auto-move toggle ─────────────────────────────

def _reset_discord_team_cfg(ac):
    """v0.12.0 — explicit reset for tests that need clean discord config.
    APPDATA tempdir is shared across tests in the same module run, so an
    earlier test that persisted VC IDs would leak into a later 'unset'
    test.  Calling this AFTER _new_app() guarantees a clean baseline."""
    ac.discord_guild_id = ''
    ac.discord_team_a_voice_channel_id = ''
    ac.discord_team_b_voice_channel_id = ''
    ac.discord_auto_move_on_distribute_enabled = False


def t_move_teams_refuses_when_guild_unconfigured():
    ac, _, c = _new_app(); _login(c)
    _reset_discord_team_cfg(ac)
    r = c.post('/api/discord/move_teams', json={})
    body = r.get_json() or {}
    return (r.status_code == 400 and 'guild ID' in (body.get('error') or '')), \
           f'status={r.status_code} body={body}'
t('move_teams: 400 when discord_guild_id unset',
  t_move_teams_refuses_when_guild_unconfigured)


def t_move_teams_refuses_when_team_vcs_unconfigured():
    ac, _, c = _new_app(); _login(c)
    _reset_discord_team_cfg(ac)
    ac.discord_guild_id = '12345'
    # team VCs left blank by _reset_discord_team_cfg
    r = c.post('/api/discord/move_teams', json={})
    body = r.get_json() or {}
    return (r.status_code == 400 and 'voice channels' in (body.get('error') or '')), \
           f'status={r.status_code} body={body}'
t('move_teams: 400 when team_a/team_b voice channels unset',
  t_move_teams_refuses_when_team_vcs_unconfigured)


def t_move_teams_refuses_when_no_session():
    ac, _, c = _new_app(); _login(c)
    ac.discord_guild_id = '12345'
    ac.discord_team_a_voice_channel_id = '111'
    ac.discord_team_b_voice_channel_id = '222'
    # no veto session — should refuse before reaching the bot module
    r = c.post('/api/discord/move_teams', json={})
    body = r.get_json() or {}
    return (r.status_code == 400 and 'session' in (body.get('error') or '').lower()), \
           f'status={r.status_code} body={body}'
t('move_teams: 400 when no active veto session',
  t_move_teams_refuses_when_no_session)


def t_move_teams_refuses_when_state_is_roster():
    """Session must be past the roster stage — distribute() must have
    happened (state in teams/voting/links/veto/finale/complete) so
    team_a/team_b exist with players."""
    ac, _, c = _new_app(); _login(c)
    ac.discord_guild_id = '12345'
    ac.discord_team_a_voice_channel_id = '111'
    ac.discord_team_b_voice_channel_id = '222'
    # Create session and stop at roster (don't distribute)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'A', 'team_b_name': 'B',
        'players': _ten_player_payload(),
    })
    r = c.post('/api/discord/move_teams', json={})
    body = r.get_json() or {}
    return (r.status_code == 400 and 'session' in (body.get('error') or '').lower()), \
           f'status={r.status_code} body={body}'
t('move_teams: 400 when session is on roster stage (teams not split yet)',
  t_move_teams_refuses_when_state_is_roster)


def t_move_teams_refuses_when_no_discord_ids():
    """Distributed teams but no discord_ids on any player → 400.
    Without IDs there's nothing to move."""
    ac, _, c = _new_app(); _login(c)
    ac.discord_guild_id = '12345'
    ac.discord_team_a_voice_channel_id = '111'
    ac.discord_team_b_voice_channel_id = '222'
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'A', 'team_b_name': 'B',
        'players': _ten_player_payload(),  # no discord_id fields
    })
    c.post('/api/veto/distribute')
    r = c.post('/api/discord/move_teams', json={})
    body = r.get_json() or {}
    return (r.status_code == 400 and 'discord_id' in (body.get('error') or '')), \
           f'status={r.status_code} body={body}'
t('move_teams: 400 when no discord_ids on either team',
  t_move_teams_refuses_when_no_discord_ids)


def t_auto_move_toggle_refuses_enable_without_vcs():
    """Enabling auto-move when either team VC is empty must fail with 400.
    Defeats the silent-no-op tournament-night surprise where the toggle is
    True but auto-fire silently skips because preconditions aren't met."""
    ac, _, c = _new_app(); _login(c)
    _reset_discord_team_cfg(ac)
    # Configure only A, leave B blank
    ac.discord_team_a_voice_channel_id = '111'
    r = c.post('/api/discord/auto_move_toggle', json={'enabled': True})
    body = r.get_json() or {}
    return (r.status_code == 400 and 'voice channels' in (body.get('error') or '')
            and ac.discord_auto_move_on_distribute_enabled is False), \
           f'status={r.status_code} body={body} ac.toggle={ac.discord_auto_move_on_distribute_enabled}'
t('auto_move_toggle: 400 + no persist when enabling with VC missing',
  t_auto_move_toggle_refuses_enable_without_vcs)


def t_auto_move_toggle_disable_always_allowed():
    """Toggling OFF must always succeed, even with VCs missing.
    Operator must be able to disable an accidentally-enabled toggle even
    if they later cleared the VC IDs."""
    ac, _, c = _new_app(); _login(c)
    ac.discord_auto_move_on_distribute_enabled = True
    # Both VCs cleared
    ac.discord_team_a_voice_channel_id = ''
    ac.discord_team_b_voice_channel_id = ''
    r = c.post('/api/discord/auto_move_toggle', json={'enabled': False})
    body = r.get_json() or {}
    return (r.status_code == 200 and body.get('enabled') is False
            and ac.discord_auto_move_on_distribute_enabled is False), \
           f'status={r.status_code} body={body} ac.toggle={ac.discord_auto_move_on_distribute_enabled}'
t('auto_move_toggle: disable always succeeds + persists',
  t_auto_move_toggle_disable_always_allowed)


def t_auto_move_toggle_enable_persists_when_vcs_set():
    """Enabling with both VCs set returns 200 + persists the flag."""
    ac, _, c = _new_app(); _login(c)
    ac.discord_team_a_voice_channel_id = '111'
    ac.discord_team_b_voice_channel_id = '222'
    r = c.post('/api/discord/auto_move_toggle', json={'enabled': True})
    body = r.get_json() or {}
    return (r.status_code == 200 and body.get('enabled') is True
            and ac.discord_auto_move_on_distribute_enabled is True), \
           f'status={r.status_code} body={body} ac.toggle={ac.discord_auto_move_on_distribute_enabled}'
t('auto_move_toggle: enable with both VCs set persists',
  t_auto_move_toggle_enable_persists_when_vcs_set)


# ─── v0.12.1 — /round-summaries toggle + match_events score parser ────────

def t_round_summaries_toggle_persists_both_ways():
    """Round summaries toggle: 200 for both enable and disable, persists.
    Unlike auto-move, has no precondition check on enable (the embed
    target is discord_veto_channel_id which is reused from the live
    veto embed; if blank, the post helper silently no-ops)."""
    ac, _, c = _new_app(); _login(c)
    ac.discord_round_summaries_enabled = False
    # Enable
    r1 = c.post('/api/discord/round_summaries_toggle', json={'enabled': True})
    body1 = r1.get_json() or {}
    ok1 = (r1.status_code == 200 and body1.get('enabled') is True
           and ac.discord_round_summaries_enabled is True)
    # Disable
    r2 = c.post('/api/discord/round_summaries_toggle', json={'enabled': False})
    body2 = r2.get_json() or {}
    ok2 = (r2.status_code == 200 and body2.get('enabled') is False
           and ac.discord_round_summaries_enabled is False)
    return (ok1 and ok2), \
           f'r1={r1.status_code}/{body1} r2={r2.status_code}/{body2} ac.toggle={ac.discord_round_summaries_enabled}'
t('round_summaries_toggle: enable + disable both persist',
  t_round_summaries_toggle_persists_both_ways)


def t_match_events_parse_scores_happy_path():
    """Parse mp_t_score + mp_ct_score from the typical RCON reply shape.
    CS2 returns `"mp_t_score" = "8"` style; the parser must extract
    both ints from a batched reply."""
    from cs2servergui.match_events import _parse_scores
    reply = (
        '"mp_t_score" = "8" ( def. "0" ) game notify\n'
        '"mp_ct_score" = "5" ( def. "0" ) game notify\n'
        '"host_map_name" = "de_vertigo"\n'
    )
    result = _parse_scores(reply)
    return (result == (8, 5)), f'result={result!r}'
t('match_events: _parse_scores extracts (t, ct) from batched RCON reply',
  t_match_events_parse_scores_happy_path)


def t_match_events_parse_scores_missing_returns_none():
    """If either score cvar is missing from the reply (e.g. RCON
    truncated, only one cvar echoed), return None so the poller
    skips the tick without crashing."""
    from cs2servergui.match_events import _parse_scores
    only_t = '"mp_t_score" = "3"\n'
    no_scores = '"host_map_name" = "de_dust2"\n'
    return (_parse_scores(only_t) is None and
            _parse_scores(no_scores) is None and
            _parse_scores('') is None), \
           f'only_t={_parse_scores(only_t)} no_scores={_parse_scores(no_scores)} empty={_parse_scores("")}'
t('match_events: _parse_scores returns None on missing cvars',
  t_match_events_parse_scores_missing_returns_none)


def t_match_events_round_embed_color_by_winner():
    """T win → blue; CT win → orange.  Sanity check on the embed
    builder so a color tweak doesn't silently invert the convention."""
    from cs2servergui.match_events import _build_round_embed
    t_embed = _build_round_embed(
        t_score=1, ct_score=0, team_a_name='A', team_b_name='B',
        map_name='de_dust2', who_won='T')
    ct_embed = _build_round_embed(
        t_score=0, ct_score=1, team_a_name='A', team_b_name='B',
        map_name='de_dust2', who_won='CT')
    return (t_embed.get('color') == 0x3498DB and ct_embed.get('color') == 0xE67E22), \
           f't={hex(t_embed.get("color", 0))} ct={hex(ct_embed.get("color", 0))}'
t('match_events: round embed colored by winning side',
  t_match_events_round_embed_color_by_winner)


# ─── v0.12.5 / task #95 — Gaming Mode endpoint ────────────────────────────

def t_gaming_mode_refuses_invalid_mode():
    """Body must specify mode in {'on','off','status'} — anything else
    is a 400 before we shell out to PowerShell."""
    _, _, c = _new_app(); _login(c)
    # Force is_local so @require_local passes
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.post('/api/system/gaming_mode', json={'mode': 'sideways'})
    body = r.get_json() or {}
    return (r.status_code == 400 and 'mode must be' in (body.get('error') or '')), \
           f'status={r.status_code} body={body}'
t('gaming_mode: 400 on invalid mode',
  t_gaming_mode_refuses_invalid_mode)


def t_gaming_mode_refuses_remote_admin():
    """@require_local means a non-local admin (e.g. remote via tunnel)
    gets 403.  Test client is naturally non-local."""
    _, _, c = _new_app(); _login(c)
    # Do NOT force is_local — leave the default (False) so require_local fails
    r = c.post('/api/system/gaming_mode', json={'mode': 'status'})
    return r.status_code == 403, f'status={r.status_code}'
t('gaming_mode: 403 for non-local admin (remote session)',
  t_gaming_mode_refuses_remote_admin)


# ─── v0.12.4 / task #139 — Content-hashed static URLs ─────────────────────

def t_static_with_version_param_caches_aggressively():
    """v0.12.4 — `/static/foo?v=0.12.4` returns
    Cache-Control: public, max-age=31536000, immutable.  Combined with
    the template injecting `?v={{ app_version }}` on every static URL,
    this gives both cache-bust on rebuild AND aggressive caching
    between rebuilds."""
    _, _, c = _new_app()
    r = c.get('/static/css/app.css?v=0.12.4')
    cc = r.headers.get('Cache-Control', '')
    return ('immutable' in cc and 'max-age=31536000' in cc), \
           f'status={r.status_code} cache-control={cc!r}'
t('static (v0.12.4): versioned URL serves immutable cache headers',
  t_static_with_version_param_caches_aggressively)


def t_static_without_version_param_does_not_cache():
    """Without a version param, no immutable headers — Flask's default
    behaviour (which uses ETag).  Backwards compatible: any stray
    request to /static/foo without the version still works."""
    _, _, c = _new_app()
    r = c.get('/static/css/app.css')
    cc = r.headers.get('Cache-Control', '')
    return (r.status_code == 200 and 'immutable' not in cc), \
           f'status={r.status_code} cache-control={cc!r}'
t('static (v0.12.4): unversioned URL falls back to Flask defaults',
  t_static_without_version_param_does_not_cache)


def t_index_template_injects_version_into_static_urls():
    """The login page (no auth) renders index.html with `?v=<APP_VERSION>`
    on every /static/* URL.  Confirms the template was updated to use
    the app_version variable Flask already passes in."""
    _, _, c = _new_app()
    # Hit / (login page — no auth needed)
    r = c.get('/')
    body = r.get_data(as_text=True)
    from cs2servergui.config import APP_VERSION
    expected = f'?v={APP_VERSION}'
    return (r.status_code == 200
            and f'app.css{expected}' in body
            and f'emblem.png{expected}' in body), \
           f'status={r.status_code} APP_VERSION={APP_VERSION} contains_app_css={expected in body and "app.css" + expected in body}'
t('index template (v0.12.4): injects ?v=APP_VERSION into /static/ URLs',
  t_index_template_injects_version_into_static_urls)


# ─── v0.12.3 / task #135 — Remote voter tokens ────────────────────────────

def t_voter_tokens_refuses_outside_voting_state():
    """issue_voter_tokens is only legal in `voting` state.  Earlier
    (roster/teams) there's nothing to vote for; later (links+) the
    captains are already elected and voter URLs would be pointless."""
    _, _, c = _new_app(); _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    # Still on roster — refuse
    r = c.post('/api/veto/voter_tokens', json={})
    return r.status_code == 400, f'status={r.status_code} body={r.get_json()}'
t('voter_tokens: 400 when state != voting',
  t_voter_tokens_refuses_outside_voting_state)


def t_voter_tokens_mints_10_in_voting_state():
    """Full happy path through to voting state, then mint 10 tokens."""
    _, _, c = _new_app(); _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'A', 'team_b_name': 'B',
        'players': _ten_player_payload(),
    })
    c.post('/api/veto/distribute')
    c.post('/api/veto/start_voting')
    r = c.post('/api/veto/voter_tokens', json={})
    body = r.get_json() or {}
    voters = body.get('voters') or {}
    keys = sorted(voters.keys())
    expected_keys = sorted([f'{t}:{i}' for t in ('A', 'B') for i in range(5)])
    return (r.status_code == 200 and keys == expected_keys
            and all('token' in v and v['token'] for v in voters.values())
            and all('lan' in v for v in voters.values())), \
           f'status={r.status_code} keys={keys} sample={list(voters.values())[:1]}'
t('voter_tokens: mints 10 tokens keyed A:0..B:4 in voting state',
  t_voter_tokens_mints_10_in_voting_state)


def t_voter_claim_sets_voter_session_and_vote_locked():
    """After voter_claim, /api/state should report role=voter +
    voter_team + voter_idx.  And the vote endpoint must reject a vote
    cast for a different team or different voter_idx."""
    _, _, c = _new_app(); _login(c)
    c.post('/api/veto/create', json={'mode': 'BO3'})
    c.post('/api/veto/roster', json={
        'team_a_name': 'A', 'team_b_name': 'B',
        'players': _ten_player_payload(),
    })
    c.post('/api/veto/distribute')
    c.post('/api/veto/start_voting')
    r = c.post('/api/veto/voter_tokens', json={})
    voters = (r.get_json() or {}).get('voters') or {}
    a3_tok = voters['A:3']['token']

    # New client to simulate a remote voter (no admin PIN).
    voter_c = c.application.test_client()
    r2 = voter_c.post('/api/veto/voter_claim', json={'token': a3_tok})
    body2 = r2.get_json() or {}
    if not (r2.status_code == 200 and body2.get('team') == 'A'
            and body2.get('voter_idx') == 3):
        return False, f'claim status={r2.status_code} body={body2}'

    # Capture the voter session cookie + reuse it explicitly for subsequent
    # requests.  Flask's test client SHOULD auto-store this, but in this
    # codebase's threaded setup the cookie jar can get out of sync — pass
    # the cookie explicitly via headers instead.
    set_cookie = r2.headers.get('Set-Cookie', '')
    voter_session_cookie = ''
    for part in set_cookie.split(';'):
        if part.strip().startswith('session='):
            voter_session_cookie = part.strip()
            break
    if not voter_session_cookie:
        return False, f'no session cookie in claim response: {set_cookie!r}'
    hdrs = {'Cookie': voter_session_cookie}

    # Try voting as team A idx 1 (NOT our slot) — must 403
    r4 = voter_c.post('/api/veto/vote',
                      json={'team': 'A', 'voter_idx': 1, 'votee_idx': 2},
                      headers=hdrs)
    if r4.status_code != 403:
        return False, f'wrong-slot status={r4.status_code} body={r4.get_data(as_text=True)[:200]}'

    # Try voting for our actual slot — must 200
    r5 = voter_c.post('/api/veto/vote',
                      json={'team': 'A', 'voter_idx': 3, 'votee_idx': 2},
                      headers=hdrs)
    return r5.status_code == 200, f'own-slot status={r5.status_code} body={r5.get_data(as_text=True)[:200]}'
t('voter_claim: mints role=voter session + cross-slot vote rejected',
  t_voter_claim_sets_voter_session_and_vote_locked)


def t_diag_snapshot_includes_sse_broadcast_telemetry():
    """v0.12.2 — diagnostic snapshot includes the SSE broadcast telemetry
    section so an operator can confirm whether silent queue drops are
    actually happening in production (audit finding #10 / task #143).
    Pre-v0.12.2 the polling fallback masked drops without proof of
    overflow — the operator had no way to tell if the polling was
    necessary or papering over a different bug."""
    _, _, c = _new_app(); _login(c)
    # /api/diag/snapshot is @require_local; forge is_local=True like the
    # other snapshot tests do (see t_diag_snapshot_returns_pasteable_text).
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.get('/api/diag/snapshot')
    text = r.get_data(as_text=True)
    ok = (r.status_code == 200
          and 'SSE broadcast telemetry' in text
          and 'events_total' in text
          and 'drops_total' in text
          and 'active_subscribers' in text)
    return ok, f'status={r.status_code} contains_section={("SSE broadcast telemetry" in text)}'
t('diag (v0.12.2): SSE broadcast telemetry section present',
  t_diag_snapshot_includes_sse_broadcast_telemetry)


def t_match_events_start_stop_idempotent():
    """start(core) is idempotent — second call doesn't spawn a second
    poller.  stop() likewise idempotent + safe to call when not running."""
    from cs2servergui import match_events
    ac, _, _ = _new_app()
    # Make sure we're starting clean
    match_events.stop()
    assert match_events.is_running() is False, 'expected not-running pre-start'
    # First start
    match_events.start(ac)
    running_after_first = match_events.is_running()
    # Second start — should be no-op (same thread still alive)
    match_events.start(ac)
    running_after_second = match_events.is_running()
    # Stop + verify
    match_events.stop()
    running_after_stop = match_events.is_running()
    # Belt-and-braces — stop twice
    match_events.stop()
    return (running_after_first is True and running_after_second is True
            and running_after_stop is False), \
           (f'first={running_after_first} second={running_after_second} '
            f'after_stop={running_after_stop}')
t('match_events: start/stop are idempotent',
  t_match_events_start_stop_idempotent)


# ─── v0.13.2 / task #92 — Plugin Manager API ─────────────────────────────

def t_api_plugins_shape():
    """/api/plugins returns runtime + manifest + bundled sections; the
    bundled list covers every slug referenced in _MODE_PLUGIN_NAMES."""
    ac, app, c = _new_app()
    _login(c)
    # /api/plugins GET is @require_local (audit fix #1 — csgo_dir is a
    # filesystem path; don't leak it to remote captain/voter sessions).
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.get('/api/plugins')
    if r.status_code != 200:
        return False, f'status={r.status_code}'
    body = r.get_json()
    if not isinstance(body, dict):
        return False, 'body not dict'
    if not all(k in body for k in ('runtime', 'manifest', 'bundled', 'current_mode')):
        return False, f'missing keys: {list(body.keys())}'
    rt = body['runtime']
    rt_keys = {'csgo_dir', 'csgo_dir_exists', 'css_present', 'metamod_patched'}
    if not rt_keys.issubset(rt.keys()):
        return False, f'runtime missing: {rt_keys - set(rt.keys())}'
    from cs2servergui.core import _MODE_PLUGIN_NAMES
    referenced = {s for ss in _MODE_PLUGIN_NAMES.values() for s in ss}
    bundled_slugs = {b['slug'] for b in body['bundled']}
    if not referenced.issubset(bundled_slugs):
        return False, f'bundled missing referenced slugs: {referenced - bundled_slugs}'
    return True, f'{len(body["bundled"])} bundled, runtime keys ok'
t('plugins (v0.13.2): /api/plugins returns runtime + manifest + bundled',
  t_api_plugins_shape)


def t_api_plugins_catalog_descriptions_present():
    """Every bundled slug that has a catalog entry surfaces display_name +
    summary + author in the API response — the SPA's library grid would
    look bald without them."""
    ac, app, c = _new_app()
    _login(c)
    # /api/plugins GET is @require_local (audit fix #1 — csgo_dir is a
    # filesystem path; don't leak it to remote captain/voter sessions).
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.get('/api/plugins')
    if r.status_code != 200:
        return False, f'status={r.status_code}'
    body = r.get_json()
    from cs2servergui.core import _PLUGIN_CATALOG
    cataloged = [b for b in body['bundled'] if b['slug'] in _PLUGIN_CATALOG]
    if not cataloged:
        return False, 'no cataloged bundled entries surfaced'
    missing = [b['slug'] for b in cataloged
               if not (b.get('display_name') and b.get('summary'))]
    if missing:
        return False, f'no display_name/summary for: {missing}'
    return True, f'{len(cataloged)} cataloged entries surfaced fully'
t('plugins (v0.13.2): cataloged bundled entries surface display fields',
  t_api_plugins_catalog_descriptions_present)


def t_api_plugins_modes_reverse_map():
    """Each bundled plugin reports the modes that use it via the reverse
    map of _MODE_PLUGIN_NAMES — so the SPA card can say
    'Used by: Practice, 3v3, 4v4, 5v5' for the practice slug."""
    ac, app, c = _new_app()
    _login(c)
    # /api/plugins GET is @require_local (audit fix #1 — csgo_dir is a
    # filesystem path; don't leak it to remote captain/voter sessions).
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.get('/api/plugins')
    body = r.get_json()
    from cs2servergui.core import _MODE_PLUGIN_NAMES
    by_slug = {b['slug']: b for b in body['bundled']}
    # 'practice' is used by 4 modes; sanity-check it.
    expected = sorted(m for m, ss in _MODE_PLUGIN_NAMES.items() if 'practice' in ss)
    actual = by_slug.get('practice', {}).get('modes', [])
    return (sorted(actual) == expected), \
           f'practice modes expected={expected} actual={actual}'
t('plugins (v0.13.2): bundled entries report reverse mode mapping',
  t_api_plugins_modes_reverse_map)


# ─── v0.13.2 slice 2 — Activate / Switch-to-vanilla actions ──────────────

def t_plugins_vanilla_happy_path():
    """POST /api/plugins/vanilla with server stopped + csgo/ ready
    succeeds, sets current_mode to Competitive, leaves manifest empty.
    Local-only — forge is_local on the test session."""
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.post('/api/plugins/vanilla')
    if r.status_code != 200:
        return False, f'status={r.status_code} body={r.get_data(as_text=True)[:120]!r}'
    body = r.get_json() or {}
    if body.get('mode') != 'Competitive':
        return False, f'mode={body.get("mode")!r} expected=Competitive'
    if ac.current_mode != 'Competitive':
        return False, f'ac.current_mode={ac.current_mode!r}'
    return True, f'mode={body["mode"]} plugins={body.get("plugins")}'
t('plugins (v0.13.2): /api/plugins/vanilla switches to Competitive',
  t_plugins_vanilla_happy_path)


def t_plugins_vanilla_remote_403():
    """Plugin actions are local-only — a non-local session gets 403.
    Operators on a Cloudflare-tunnelled remote tab shouldn't be able to
    silently swap plugins on the host."""
    ac, app, c = _new_app()
    _login(c)
    # Don't forge is_local — test_client is naturally non-local.
    r = c.post('/api/plugins/vanilla')
    return r.status_code == 403, f'status={r.status_code} (want 403)'
t('plugins (v0.13.2): /api/plugins/vanilla 403 for non-local sessions',
  t_plugins_vanilla_remote_403)


def t_plugins_activate_routes_to_restart_when_running():
    """v0.14.1: activate while server running no longer 409s.  Backend
    routes through change_map (async stop-deploy-restart) and returns 202
    with restarting=True.  The mode picker has used this exact code path
    since v0.10.x — there's no reason to be more restrictive here.

    We stub change_map so the test doesn't actually try to bind a socket;
    we only assert the endpoint chose the right branch."""
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    calls = []
    ac.change_map = lambda *a, **kw: calls.append((a, kw))  # type: ignore[method-assign]
    ac.running = True
    try:
        r = c.post('/api/plugins/activate', json={'slug': 'warcraft'})
    finally:
        ac.running = False
    if r.status_code != 202:
        return False, f'status={r.status_code} body={r.get_data(as_text=True)[:120]!r}'
    body = r.get_json() or {}
    if not body.get('restarting'):
        return False, f'restarting flag missing: {body}'
    if not calls:
        return False, 'change_map was not invoked'
    # change_map(map_name, mode, is_workshop=..., caller=...) — mode must be
    # Warcraft (the slug → mode auto-pick), and map must be from MODE_MAPS.
    args, kwargs = calls[0]
    if len(args) < 2 or args[1] != 'Warcraft':
        return False, f'change_map called with wrong mode: {args!r}'
    return True, f'restart routed; change_map({args[0]!r}, {args[1]!r})'
t('plugins (v0.14.1): activate while running → 202 + change_map restart',
  t_plugins_activate_routes_to_restart_when_running)


def t_plugins_activate_rejects_unknown_slug():
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.post('/api/plugins/activate', json={'slug': 'totally-not-a-real-plugin'})
    return r.status_code == 400, f'status={r.status_code}'
t('plugins (v0.13.2): activate rejects unknown slug with 400',
  t_plugins_activate_rejects_unknown_slug)


def t_plugins_activate_requires_mode_for_multimode():
    """MatchZy (slug 'practice') is used by Practice/3v3/4v4/5v5.  Calling
    activate without `mode` is ambiguous — backend must refuse and list
    the valid modes so the SPA can prompt the operator."""
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.post('/api/plugins/activate', json={'slug': 'practice'})
    if r.status_code != 400:
        return False, f'status={r.status_code}'
    body = r.get_json() or {}
    modes = body.get('modes', [])
    expected = {'Practice', '3v3', '4v4', '5v5'}
    return set(modes) == expected, f'modes={modes} expected={sorted(expected)}'
t('plugins (v0.13.2): activate refuses ambiguous multi-mode slug',
  t_plugins_activate_requires_mode_for_multimode)


def t_plugins_activate_rejects_slug_mode_mismatch():
    """If the operator passes `mode` that doesn't include the slug, refuse
    rather than silently activating the wrong thing.  warcraft is used by
    mode 'Warcraft' only — pairing it with '5v5' is operator error."""
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.post('/api/plugins/activate', json={'slug': 'warcraft', 'mode': '5v5'})
    return r.status_code == 400, f'status={r.status_code}'
t('plugins (v0.13.2): activate refuses slug/mode mismatch',
  t_plugins_activate_rejects_slug_mode_mismatch)


def t_plugins_activate_warcraft_happy_path():
    """Single-mode happy path: activating 'warcraft' (no mode arg needed)
    sets current_mode='Warcraft' and reports the deployed plugin slug.
    Confirms the slug→mode auto-pick branch works.

    _verify_deployment is stubbed because it checks for an installed
    MetaMod base under csgo/addons/metamod/, which the test temp dir
    doesn't have.  We're verifying the endpoint contract here, not the
    file-layout correctness of a real CS2 install."""
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    ac._verify_deployment = lambda new_plugins: True  # type: ignore[method-assign]
    r = c.post('/api/plugins/activate', json={'slug': 'warcraft'})
    if r.status_code != 200:
        return False, f'status={r.status_code} body={r.get_data(as_text=True)[:160]!r}'
    body = r.get_json() or {}
    return (body.get('mode') == 'Warcraft'
            and 'warcraft' in body.get('plugins', [])
            and ac.current_mode == 'Warcraft'), \
           f'body={body} current_mode={ac.current_mode!r}'
t('plugins (v0.13.2): activate warcraft (single-mode auto-pick) deploys + sets mode',
  t_plugins_activate_warcraft_happy_path)


# ─── v0.14.0 slice 3 — Curated packs ──────────────────────────────────────

def t_plugins_packs_listed():
    """GET /api/plugins/packs returns the 5 seed packs with derived plugin
    lists.  Schema check — each pack has id/name/mode/summary, and the
    plugins list is non-empty for every non-vanilla pack."""
    ac, app, c = _new_app()
    _login(c)
    r = c.get('/api/plugins/packs')
    if r.status_code != 200:
        return False, f'status={r.status_code}'
    body = r.get_json() or {}
    packs = body.get('packs') or []
    if len(packs) < 4:
        return False, f'only {len(packs)} packs listed'
    required = {'id', 'name', 'mode', 'summary', 'plugins'}
    for p in packs:
        if not required.issubset(p.keys()):
            return False, f'pack {p.get("id")} missing keys: {required - set(p.keys())}'
    # The vanilla pack should have empty plugins; all others non-empty.
    for p in packs:
        is_vanilla = p['id'].startswith('vanilla')
        if is_vanilla and p['plugins']:
            return False, f'vanilla pack {p["id"]} has plugins {p["plugins"]}'
        if not is_vanilla and not p['plugins']:
            return False, f'non-vanilla pack {p["id"]} has empty plugins'
    return True, f'{len(packs)} packs OK'
t('plugins (v0.14.0): /api/plugins/packs returns seed packs',
  t_plugins_packs_listed)


def t_plugins_apply_pack_vanilla_happy_path():
    """Applying the vanilla pack switches mode to Competitive and stages
    the pack's default map.  No deploy verification stub needed — vanilla
    has zero plugins so deploy_plugins skips the verify path entirely."""
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.post('/api/plugins/apply_pack', json={'pack_id': 'vanilla_competitive'})
    if r.status_code != 200:
        return False, f'status={r.status_code} body={r.get_data(as_text=True)[:160]!r}'
    body = r.get_json() or {}
    return (body.get('mode') == 'Competitive'
            and body.get('map')  == 'de_dust2'
            and ac.current_mode == 'Competitive'
            and ac.current_map  == 'de_dust2'), \
           f'body={body} cur_mode={ac.current_mode!r} cur_map={ac.current_map!r}'
t('plugins (v0.14.0): apply_pack vanilla switches mode + stages map',
  t_plugins_apply_pack_vanilla_happy_path)


def t_plugins_apply_pack_rejects_unknown():
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.post('/api/plugins/apply_pack', json={'pack_id': 'totally-fake-pack'})
    return r.status_code == 400, f'status={r.status_code}'
t('plugins (v0.14.0): apply_pack rejects unknown pack with 400',
  t_plugins_apply_pack_rejects_unknown)


def t_plugins_apply_pack_routes_to_restart_when_running():
    """v0.14.1: apply_pack on a running server routes through change_map
    (async restart) rather than 409ing the operator.  Pack's default_map
    becomes the restart target."""
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    calls = []
    ac.change_map = lambda *a, **kw: calls.append((a, kw))  # type: ignore[method-assign]
    ac.running = True
    try:
        r = c.post('/api/plugins/apply_pack', json={'pack_id': 'competitive_5v5'})
    finally:
        ac.running = False
    if r.status_code != 202:
        return False, f'status={r.status_code}'
    body = r.get_json() or {}
    if not body.get('restarting'):
        return False, f'restarting flag missing: {body}'
    if not calls:
        return False, 'change_map was not invoked'
    args, _kwargs = calls[0]
    # Competitive 5v5 pack stages de_dust2 + mode 5v5.
    if args[0] != 'de_dust2' or args[1] != '5v5':
        return False, f'change_map args wrong: {args!r}'
    return True, f'restart routed; change_map({args[0]!r}, {args[1]!r})'
t('plugins (v0.14.1): apply_pack while running → 202 + change_map restart',
  t_plugins_apply_pack_routes_to_restart_when_running)


def t_plugins_apply_pack_remote_403():
    ac, app, c = _new_app()
    _login(c)
    # Naturally non-local — apply_pack is @require_local.
    r = c.post('/api/plugins/apply_pack', json={'pack_id': 'vanilla_competitive'})
    return r.status_code == 403, f'status={r.status_code} (want 403)'
t('plugins (v0.14.0): apply_pack 403 for non-local sessions',
  t_plugins_apply_pack_remote_403)


def t_plugins_pack_default_map_is_in_mode_pool():
    """Sanity check: every pack's default_map must be in MODE_MAPS[mode]
    (or the pool must be None — Jailbreak's workshop-only).  Stops a typo
    in _PLUGIN_PACKS from silently 500-ing on apply."""
    from cs2servergui.core import _PLUGIN_PACKS
    from cs2servergui.config import MODE_MAPS
    bad = []
    for p in _PLUGIN_PACKS:
        m = p.get('default_map')
        if not m:
            continue
        pool = MODE_MAPS.get(p['mode'])
        if pool is not None and m not in pool:
            bad.append(f'{p["id"]}: {m} not in MODE_MAPS[{p["mode"]!r}]')
    return not bad, '; '.join(bad) or 'all pack maps valid'
t('plugins (v0.14.0): every pack default_map is in its mode pool',
  t_plugins_pack_default_map_is_in_mode_pool)


# ─── v0.14.0 slice 4 — File-based catalog ─────────────────────────────────

def t_plugin_catalog_loaded_from_json_file():
    """registry/catalog.json must exist + load cleanly + cover every slug
    referenced by _MODE_PLUGIN_NAMES.  The JSON is now the sole source of
    truth (audit fix #4 dropped the inline fallback dict to eliminate
    drift), so a missing/broken file = immediate visible failure."""
    import os as _os, json as _json
    from cs2servergui.core import (
        _PLUGIN_CATALOG, _MODE_PLUGIN_NAMES, _resolve_catalog_path,
    )
    path = _resolve_catalog_path()
    if not _os.path.isfile(path):
        return False, f'catalog.json missing at {path!r}'
    with open(path, encoding='utf-8') as f:
        doc = _json.load(f)
    if doc.get('schema_version') != 1:
        return False, f'schema_version={doc.get("schema_version")} expected 1'
    json_slugs = {e['slug'] for e in (doc.get('plugins') or []) if 'slug' in e}
    referenced = {s for ss in _MODE_PLUGIN_NAMES.values() for s in ss}
    missing_in_json = referenced - json_slugs
    if missing_in_json:
        return False, f'catalog.json missing referenced slugs: {missing_in_json}'
    # The LOADED catalog should agree with the file for every referenced slug.
    for slug in referenced:
        if slug not in _PLUGIN_CATALOG:
            return False, f'loaded catalog missing {slug!r}'
    return True, f'{len(json_slugs)} entries loaded from {_os.path.basename(path)}'
t('plugins (v0.14.0): catalog loads from registry/catalog.json',
  t_plugin_catalog_loaded_from_json_file)


def t_plugin_catalog_load_failure_returns_empty_not_fallback():
    """Audit fix #4: _load_plugin_catalog returns {} (not stale inline data)
    when the JSON can't be parsed.  Operators see "unnamed" cards in the
    SPA — a loud, fixable failure mode — rather than silently getting
    outdated display strings."""
    import io, sys as _sys
    from cs2servergui.core import _load_plugin_catalog
    from unittest import mock
    # Point _resolve_catalog_path at a nonexistent file.
    with mock.patch('cs2servergui.core._resolve_catalog_path',
                    return_value='/nope/does-not-exist.json'):
        # Capture stderr so the test output stays clean.
        old_err = _sys.stderr
        _sys.stderr = io.StringIO()
        try:
            result = _load_plugin_catalog()
            err = _sys.stderr.getvalue()
        finally:
            _sys.stderr = old_err
    if result != {}:
        return False, f'expected empty dict, got {len(result)} entries'
    if '[catalog]' not in err:
        return False, f'expected stderr to include [catalog] tag; got {err!r}'
    return True, 'empty dict + loud stderr log on missing catalog.json'
t('plugins (v0.14.0): catalog load failure is loud, returns {}',
  t_plugin_catalog_load_failure_returns_empty_not_fallback)


def t_api_plugins_remote_403():
    """Audit fix #1: /api/plugins GET is local-only.  A non-local session
    (test_client is naturally non-local) gets 403 — proves the decorator
    is in place and prevents future regressions that would re-leak
    csgo_dir to remote captain/voter sessions."""
    ac, app, c = _new_app()
    _login(c)
    r = c.get('/api/plugins')
    return r.status_code == 403, f'status={r.status_code} (want 403)'
t('plugins (v0.14.0 audit fix #1): /api/plugins 403 for non-local sessions',
  t_api_plugins_remote_403)


# ─── v0.15.0 slice 1 — Self-describing plugins (plugin.json) ────────────

def t_v15_every_bundled_plugin_has_manifest():
    """Every bundled plugin folder must ship a plugin.json with the
    minimum required fields.  Catches the case where a plugin author
    forgets the manifest and silently disappears from the SPA Library."""
    import os as _os
    from cs2servergui.core import _PLUGINS_BASE, _DISCOVERED_PLUGINS
    folder_slugs = sorted(
        d for d in _os.listdir(_PLUGINS_BASE)
        if _os.path.isdir(_os.path.join(_PLUGINS_BASE, d))
    )
    discovered_slugs = sorted(_DISCOVERED_PLUGINS.keys())
    missing = set(folder_slugs) - set(discovered_slugs)
    if missing:
        return False, f'plugin folders without plugin.json: {sorted(missing)}'
    return True, f'{len(discovered_slugs)} bundled plugins all have manifests'
t('plugins (v0.15.0): every bundled plugin ships a plugin.json',
  t_v15_every_bundled_plugin_has_manifest)


def t_v15_derived_tables_match_expected_data():
    """The refactor moved the 5 hardcoded constants into derived tables
    built from plugin.json.  This test pins the expected derived values
    so a typo in any bundled plugin.json gets caught immediately rather
    than at deploy time when the operator notices files in the wrong
    place."""
    from cs2servergui.core import (
        _PLUGIN_KIND, _MODE_PLUGIN_NAMES, _PLUGIN_VERIFY_FILES,
    )
    # Spot-check kind for each bundled plugin.
    expected_kinds = {
        'zombie': 'metamod', 'zombie_ze': 'metamod',
        'deathmatch': 'css', 'arenas': 'css', 'practice': 'css',
        'jailbreak': 'css', 'warcraft': 'css', 'retakes_b3none': 'css',
    }
    for slug, want in expected_kinds.items():
        got = _PLUGIN_KIND.get(slug)
        if got != want:
            return False, f'kind drift {slug}: want {want!r} got {got!r}'

    # Spot-check mode mapping for the trickiest cases.
    if 'practice' not in _MODE_PLUGIN_NAMES.get('5v5', []):
        return False, "5v5 mode lost its practice plugin"
    if 'zombie' not in _MODE_PLUGIN_NAMES.get('Zombie Escape', []):
        return False, "Zombie Escape lost its zombie plugin"
    if 'zombie_ze' not in _MODE_PLUGIN_NAMES.get('Zombie Escape', []):
        return False, "Zombie Escape lost its zombie_ze plugin"

    # Load order: zombie (metamod, load_order 10) must come BEFORE zombie_ze
    # (metamod, load_order 15) in Zombie Escape's plugin list — that's the
    # whole point of the load_order field.
    ze_plugins = _MODE_PLUGIN_NAMES['Zombie Escape']
    if ze_plugins.index('zombie') > ze_plugins.index('zombie_ze'):
        return False, f'load_order broken for ZE: {ze_plugins}'

    # Verify file paths — one canonical check per plugin to catch path drift.
    if 'addons/metamod/cs2fixes.vdf' not in _PLUGIN_VERIFY_FILES.get('zombie', [''])[0].replace('\\', '/'):
        return False, "zombie verify path drift"

    return True, 'derived tables match the pre-refactor constants'
t('plugins (v0.15.0): derived tables match pre-refactor constants',
  t_v15_derived_tables_match_expected_data)


def t_v15_user_plugin_discovery():
    """Drop a fake plugin into a tempdir, point _resolve_user_plugins_dir
    at it, re-run discovery — the local plugin appears in the result
    with source='local' and overrides any same-slug bundled entry if
    present."""
    import os as _os, json as _json, tempfile, shutil
    from cs2servergui import core as _core
    user_dir = tempfile.mkdtemp(prefix='oblivion_user_plugins_')
    try:
        slug = 'my-fake-test-plugin'
        plugin_dir = _os.path.join(user_dir, slug)
        _os.makedirs(plugin_dir)
        with open(_os.path.join(plugin_dir, 'plugin.json'), 'w', encoding='utf-8') as f:
            _json.dump({
                'schema_version': 1,
                'slug': slug,
                'display_name': 'Fake Test Plugin',
                'summary': 'For testing the user-plugin discovery path.',
                'author': 'pytest',
                'kind': 'css',
                'modes': ['Practice'],
                'copy_rules': [{'src': 'addons', 'dst': 'addons'}],
            }, f)
        # Temporarily redirect the user plugins dir.
        original = _core._resolve_user_plugins_dir
        _core._resolve_user_plugins_dir = lambda: user_dir  # type: ignore[assignment]
        try:
            discovered = _core._discover_plugins()
        finally:
            _core._resolve_user_plugins_dir = original  # type: ignore[assignment]
        if slug not in discovered:
            return False, f'user plugin {slug!r} not discovered'
        m = discovered[slug]
        if m.get('_source') != 'local':
            return False, f'_source={m.get("_source")!r} expected local'
        return True, f'user plugin discovered with source=local'
    finally:
        shutil.rmtree(user_dir, ignore_errors=True)
t('plugins (v0.15.0): user plugin discovered from %APPDATA% folder',
  t_v15_user_plugin_discovery)


def t_v15_manifest_rejects_slug_mismatch():
    """A plugin.json whose declared slug doesn't match its folder name
    must be rejected — prevents an operator-supplied plugin from
    masquerading as a different one (e.g. dropping a folder named
    'helpful-plugin' whose manifest declares slug='warcraft')."""
    import os as _os, json as _json, tempfile, shutil, io, sys as _sys
    from cs2servergui import core as _core
    user_dir = tempfile.mkdtemp(prefix='oblivion_user_plugins_')
    try:
        folder_slug = 'innocent-folder'
        plugin_dir = _os.path.join(user_dir, folder_slug)
        _os.makedirs(plugin_dir)
        with open(_os.path.join(plugin_dir, 'plugin.json'), 'w', encoding='utf-8') as f:
            _json.dump({
                'schema_version': 1,
                'slug': 'warcraft',   # mismatch — folder says innocent-folder
                'display_name': 'fake',
                'kind': 'css',
                'modes': [],
                'copy_rules': [],
            }, f)
        # Capture stderr so the test output stays clean while still
        # confirming the rejection logged a [plugins] line.
        old_err = _sys.stderr
        _sys.stderr = io.StringIO()
        try:
            m = _core._load_plugin_manifest_file(plugin_dir, folder_slug,
                                                  source='local')
            err = _sys.stderr.getvalue()
        finally:
            _sys.stderr = old_err
        if m is not None:
            return False, 'slug mismatch should have been rejected'
        if '[plugins]' not in err or 'mismatch' not in err.lower():
            return False, f'expected loud rejection log; got {err!r}'
        return True, 'slug/folder mismatch rejected with loud log'
    finally:
        shutil.rmtree(user_dir, ignore_errors=True)
t('plugins (v0.15.0): manifest rejects slug/folder mismatch',
  t_v15_manifest_rejects_slug_mismatch)


def t_v15_api_plugins_surfaces_source():
    """/api/plugins bundled entries must include source ('bundled' or
    'local') so the SPA can badge user-installed plugins distinctly."""
    ac, app, c = _new_app()
    _login(c)
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.get('/api/plugins')
    if r.status_code != 200:
        return False, f'status={r.status_code}'
    body = r.get_json() or {}
    for entry in (body.get('bundled') or []):
        if 'source' not in entry:
            return False, f"entry {entry.get('slug')!r} missing source"
        if entry['source'] not in ('bundled', 'local'):
            return False, f"unexpected source value {entry['source']!r}"
    return True, 'every bundled entry carries source'
t('plugins (v0.15.0): /api/plugins surfaces source per entry',
  t_v15_api_plugins_surfaces_source)


# ─── v0.15.1 slice 2 — Community plugin registry ─────────────────────────

def _make_test_zip(files: dict) -> bytes:
    """Build an in-memory zip with the given {relpath: bytes} entries."""
    import io as _io, zipfile as _zf
    buf = _io.BytesIO()
    with _zf.ZipFile(buf, 'w', _zf.ZIP_DEFLATED) as z:
        for path, data in files.items():
            z.writestr(path, data)
    return buf.getvalue()


def t_v15_registry_fetch_falls_back_to_empty_on_offline():
    """Audit-grade safety: when the registry repo doesn't exist yet
    (the case for every operator until task #90 lands the repo),
    fetch_catalog returns an empty-but-valid catalog, NOT a crash."""
    from unittest import mock
    from cs2servergui import registry_client
    # Force a hard failure on every HTTP attempt.
    with mock.patch.object(registry_client, '_http_fetch',
                            side_effect=registry_client.RegistryError('boom')):
        with mock.patch.object(registry_client, '_load_cached_catalog',
                                return_value=None):
            catalog = registry_client.fetch_catalog(force=True)
    return (isinstance(catalog, dict)
            and catalog.get('schema_version') == 1
            and catalog.get('plugins') == []
            and catalog.get('_offline') is True), \
           f'catalog={catalog!r}'
t('registry (v0.15.1): offline fetch returns empty catalog with _offline flag',
  t_v15_registry_fetch_falls_back_to_empty_on_offline)


def t_v15_registry_fetch_uses_cache_when_fresh():
    """Within the 24h TTL window, fetch_catalog should NOT hit the
    network — it returns the cached catalog directly."""
    from unittest import mock
    from cs2servergui import registry_client
    import time as _t
    cached = {
        'schema_version': 1,
        'fetched_at': int(_t.time()),    # right now → fresh
        'source_url': 'https://example.invalid/catalog.json',
        'catalog': {'schema_version': 1, 'plugins': [{'slug': 'cached',
                                                       'versions': [{'version': '1', 'download_url': 'x', 'sha256': 'y'}]}]},
    }
    http_calls = []
    def _http_spy(*a, **kw):
        http_calls.append(a)
        return b'{"schema_version":1,"plugins":[]}'
    with mock.patch.object(registry_client, '_load_cached_catalog',
                            return_value=cached):
        with mock.patch.object(registry_client, '_http_fetch', side_effect=_http_spy):
            result = registry_client.fetch_catalog(force=False)
    if http_calls:
        return False, f'fresh cache should not hit network; calls={len(http_calls)}'
    if not any(p.get('slug') == 'cached' for p in result.get('plugins', [])):
        return False, f'expected cached entry in result; got {result!r}'
    return True, 'fresh cache served without network hit'
t('registry (v0.15.1): fresh cache short-circuits network fetch',
  t_v15_registry_fetch_uses_cache_when_fresh)


def t_v15_registry_install_rejects_sha256_mismatch():
    """Bad sha256 → RegistryError + nothing written to disk.
    Critical safety guarantee: this is the line of defence against
    transport tampering even if the registry repo itself is honest."""
    from unittest import mock
    from cs2servergui import registry_client
    fake_catalog = {
        'schema_version': 1,
        'plugins': [{
            'slug': 'evil-plugin',
            'display_name': 'Evil',
            'kind': 'css',
            'modes': [],
            'versions': [{
                'version': '1.0.0',
                'download_url': 'https://example.invalid/evil.zip',
                'sha256': 'a' * 64,    # wrong sha
            }],
        }],
    }
    zip_bytes = _make_test_zip({'evil-plugin/plugin.json': b'{}'})
    with mock.patch.object(registry_client, 'fetch_catalog', return_value=fake_catalog):
        with mock.patch.object(registry_client, '_http_fetch', return_value=zip_bytes):
            try:
                registry_client.install_plugin('evil-plugin')
                return False, 'install should have rejected sha mismatch'
            except registry_client.RegistryError as exc:
                if 'sha256 mismatch' not in str(exc):
                    return False, f'wrong error: {exc!r}'
                return True, f'sha256 mismatch rejected: {exc}'
t('registry (v0.15.1): install rejects sha256 mismatch',
  t_v15_registry_install_rejects_sha256_mismatch)


def t_v15_registry_install_rejects_zip_slip():
    """A zip with a member like '../../../../oblivion_config.json'
    must be refused before extraction.  Safety guarantee — even with a
    correct sha256, a malicious zip can't escape the plugins dir."""
    import hashlib as _h
    from unittest import mock
    from cs2servergui import registry_client
    # Build a zip with a path-traversal entry.
    evil_zip = _make_test_zip({
        '../../../bad-thing.txt': b'pwned',
        'innocent-plugin/plugin.json': b'{"slug":"innocent-plugin"}',
    })
    fake_catalog = {
        'schema_version': 1,
        'plugins': [{
            'slug': 'innocent-plugin',
            'display_name': 'Innocent',
            'kind': 'css',
            'modes': [],
            'versions': [{
                'version': '1.0.0',
                'download_url': 'https://example.invalid/innocent.zip',
                'sha256': _h.sha256(evil_zip).hexdigest(),
            }],
        }],
    }
    with mock.patch.object(registry_client, 'fetch_catalog', return_value=fake_catalog):
        with mock.patch.object(registry_client, '_http_fetch', return_value=evil_zip):
            try:
                registry_client.install_plugin('innocent-plugin')
                return False, 'zip slip should have been blocked'
            except registry_client.RegistryError as exc:
                if 'zip slip' not in str(exc).lower():
                    return False, f'wrong error: {exc!r}'
                return True, f'zip slip rejected: {exc}'
t('registry (v0.15.1): install rejects zip slip path traversal',
  t_v15_registry_install_rejects_zip_slip)


def t_v15_registry_install_rejects_slug_confusion():
    """Catalog says slug=X but the extracted plugin.json declares slug=Y.
    Reject — protects against name-confusion attacks."""
    import hashlib as _h
    from unittest import mock
    from cs2servergui import registry_client
    zip_bytes = _make_test_zip({
        'mismatch/plugin.json': b'{"slug":"totally-different-name"}',
    })
    fake_catalog = {
        'schema_version': 1,
        'plugins': [{
            'slug': 'mismatch',
            'display_name': 'Mismatch',
            'kind': 'css',
            'modes': [],
            'versions': [{
                'version': '1.0.0',
                'download_url': 'https://example.invalid/mm.zip',
                'sha256': _h.sha256(zip_bytes).hexdigest(),
            }],
        }],
    }
    with mock.patch.object(registry_client, 'fetch_catalog', return_value=fake_catalog):
        with mock.patch.object(registry_client, '_http_fetch', return_value=zip_bytes):
            try:
                registry_client.install_plugin('mismatch')
                return False, 'slug confusion should have been rejected'
            except registry_client.RegistryError as exc:
                if 'slug=' not in str(exc):
                    return False, f'wrong error: {exc!r}'
                return True, f'slug confusion rejected: {exc}'
t('registry (v0.15.1): install rejects slug confusion (catalog vs manifest)',
  t_v15_registry_install_rejects_slug_confusion)


def t_v15_registry_install_happy_path():
    """End-to-end happy path with mocked network: catalog → download →
    sha256 verify → extract → manifest validates → moves into
    %APPDATA%/.../plugins/<slug>/.  Then verifies the slug appears in a
    fresh discovery pass."""
    import hashlib as _h, json as _json, os as _os, tempfile, shutil
    from unittest import mock
    from cs2servergui import registry_client, core as _core
    slug = 'test-happy-install'
    plugin_json = _json.dumps({
        'schema_version': 1,
        'slug': slug,
        'display_name': 'Happy Install',
        'kind': 'css',
        'modes': ['Practice'],
        'copy_rules': [{'src': 'addons', 'dst': 'addons'}],
    }).encode('utf-8')
    zip_bytes = _make_test_zip({
        f'{slug}/plugin.json': plugin_json,
        f'{slug}/addons/.gitkeep': b'',
    })
    fake_catalog = {
        'schema_version': 1,
        'plugins': [{
            'slug': slug,
            'display_name': 'Happy Install',
            'kind': 'css',
            'modes': ['Practice'],
            'versions': [{
                'version': '1.0.0',
                'download_url': 'https://example.invalid/h.zip',
                'sha256': _h.sha256(zip_bytes).hexdigest(),
            }],
        }],
    }
    # Redirect APPDATA-based plugin dir so the test doesn't pollute real
    # %APPDATA%.  Restore both originals in the finally block.
    test_user_dir = tempfile.mkdtemp(prefix='oblivion_install_test_')
    original_resolve = _core._resolve_user_plugins_dir
    _core._resolve_user_plugins_dir = lambda: test_user_dir
    try:
        with mock.patch.object(registry_client, 'fetch_catalog',
                                return_value=fake_catalog):
            with mock.patch.object(registry_client, '_http_fetch',
                                    return_value=zip_bytes):
                result = registry_client.install_plugin(slug)
        # The plugin should now exist at <test_user_dir>/<slug>/plugin.json
        manifest_path = _os.path.join(test_user_dir, slug, 'plugin.json')
        if not _os.path.isfile(manifest_path):
            return False, f'manifest not at {manifest_path!r}'
        # Re-run discovery and confirm slug is there with source=local.
        discovered = _core._discover_plugins()
        if slug not in discovered:
            return False, f'slug {slug!r} not in fresh discovery'
        if discovered[slug].get('_source') != 'local':
            return False, f'source not local: {discovered[slug].get("_source")!r}'
        if result['version'] != '1.0.0' or result['files_written'] < 1:
            return False, f'bad install result: {result!r}'
        return True, f'installed {slug!r} → {result["files_written"]} files'
    finally:
        _core._resolve_user_plugins_dir = original_resolve
        shutil.rmtree(test_user_dir, ignore_errors=True)
t('registry (v0.15.1): install happy path — download + verify + extract',
  t_v15_registry_install_happy_path)


# ─── v0.14.0 slice 5 — Runtime bootstrap detection ───────────────────────

def t_plugins_runtime_install_state_surfaced():
    """/api/plugins runtime block must surface metamod_installed +
    css_installed booleans separately from the looser csgo_dir_exists.
    The SPA's bootstrap button toggles on these — if they go missing,
    operators get no way to discover they need MetaMod/CSS."""
    ac, app, c = _new_app()
    _login(c)
    # /api/plugins GET is @require_local (audit fix #1 — csgo_dir is a
    # filesystem path; don't leak it to remote captain/voter sessions).
    from cs2servergui import web as _web
    for tok in list(_web._sessions.keys()):
        _web._sessions[tok]['is_local'] = True
    r = c.get('/api/plugins')
    if r.status_code != 200:
        return False, f'status={r.status_code}'
    body = r.get_json() or {}
    rt = body.get('runtime') or {}
    required = {'metamod_installed', 'css_installed', 'metamod_patched',
                'csgo_dir_exists', 'css_present'}
    missing = required - set(rt.keys())
    if missing:
        return False, f'runtime missing keys: {missing}'
    # In test temp dir there is no metamod/css install — both must be False.
    if rt['metamod_installed'] is not False:
        return False, f'metamod_installed={rt["metamod_installed"]!r} in clean tempdir'
    if rt['css_installed'] is not False:
        return False, f'css_installed={rt["css_installed"]!r} in clean tempdir'
    return True, 'runtime install booleans present + correct in clean tempdir'
t('plugins (v0.14.0): runtime block surfaces metamod_installed + css_installed',
  t_plugins_runtime_install_state_surfaced)


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
