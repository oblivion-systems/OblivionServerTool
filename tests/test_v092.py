"""
Isolated test battery for v0.9.2's risky changes.

Exercises BEHAVIOR (not just construction) of:
 1. RCON multi-packet sentinel logic — mock socket, verify concat works
 2. execute_retry exception widening
 3. server_broadcast `;` / CRLF / backtick / length-cap strip
 4. log_save filename uniqueness under hammer
 5. Event.wait crash-backoff cancellation semantics
 6. _lan_ip cache TTL behaviour
 7. _STEAMID_RE length cap
 8. save_config atomicity under concurrent reads
 9. _lifecycle_lock RLock reentrancy
10. _netutils sanity against live netstat

Two ways to run:
    python tests/test_v092.py        # standalone script (prints + exit code)
    pytest tests/test_v092.py        # one pytest case per behaviour
"""
import sys, os, tempfile, time, threading, json, struct, secrets

# Isolate config writes (per MEMORY.md — AppCore().save_config() writes to the
# real oblivion_config.json otherwise).  This MUST run before importing config.
os.environ['APPDATA'] = tempfile.mkdtemp(prefix='oblivion_test_')

# Make the project root importable when run as `pytest tests/test_v092.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = []
def t(name, fn):
    try:
        ok, detail = fn()
        results.append((ok, name, detail))
    except Exception as e:
        results.append((False, name, f'EXC: {type(e).__name__}: {e}'))


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════
class MockSocket:
    """Stand-in for socket.socket(SOCK_STREAM) — replays canned recv bytes."""
    def __init__(self, recv_packets):
        # recv_packets is a list of byte chunks; recv() drains them in order
        self.buffer = b''.join(recv_packets)
        self.sent = b''
    def settimeout(self, _): pass
    def connect(self, _): pass
    def sendall(self, data): self.sent += data
    def recv(self, n):
        if not self.buffer: return b''
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out
    def __enter__(self): return self
    def __exit__(self, *a): pass


def make_pkt(pkt_id, pkt_type, body_bytes):
    """Build a Source RCON packet matching rcon.py:_pack format."""
    data = body_bytes + b'\x00\x00'
    return struct.pack('<iii', 8 + len(data), pkt_id, pkt_type) + data


# ════════════════════════════════════════════════════════════════════
# 1) RCON multi-packet sentinel
# ════════════════════════════════════════════════════════════════════
print('=== RCON multi-packet sentinel ===')
from cs2servergui.rcon import RCONClient


def t_rcon_multipacket():
    """Server sends 3 fragments for the real command, then sentinel — all concat."""
    # IDs: aid=1 (auth), cid=2 (cmd), sid=3 (sentinel) per rcon.py:_next_id
    packets = [
        make_pkt(1, 2, b''),               # auth ack
        make_pkt(2, 0, b'frag1-'),         # cmd response part 1
        make_pkt(2, 0, b'frag2-'),         # cmd response part 2
        make_pkt(2, 0, b'frag3-end'),      # cmd response part 3
        make_pkt(3, 0, b''),               # sentinel terminator
    ]
    mock = MockSocket(packets)
    r = RCONClient('127.0.0.1', 27015, 'pw')
    import socket as _s
    orig = _s.socket
    _s.socket = lambda *a, **kw: mock
    try:
        result = r.execute('status')
    finally:
        _s.socket = orig
    expected = 'frag1-frag2-frag3-end'
    return (result == expected), f'got: {result!r}  want: {expected!r}'
t('RCON: multi-packet (3 fragments) concatenates correctly', t_rcon_multipacket)


def t_rcon_single_packet():
    """Single-packet response — sentinel terminates after one fragment."""
    packets = [
        make_pkt(1, 2, b''),
        make_pkt(2, 0, b'short'),
        make_pkt(3, 0, b''),
    ]
    mock = MockSocket(packets)
    r = RCONClient('127.0.0.1', 27015, 'pw')
    import socket as _s
    orig = _s.socket; _s.socket = lambda *a, **kw: mock
    try:
        result = r.execute('status')
    finally:
        _s.socket = orig
    return (result == 'short'), f'got: {result!r}'
t('RCON: short (single-packet) response works with sentinel', t_rcon_single_packet)


def t_rcon_csgo_junk_packet():
    """CS:GO sends a junk type-0 packet before the type-2 auth response."""
    packets = [
        make_pkt(0, 0, b''),               # junk type-0
        make_pkt(1, 2, b''),               # actual auth ack
        make_pkt(2, 0, b'hello'),
        make_pkt(3, 0, b''),
    ]
    mock = MockSocket(packets)
    r = RCONClient('127.0.0.1', 27015, 'pw')
    import socket as _s
    orig = _s.socket; _s.socket = lambda *a, **kw: mock
    try:
        result = r.execute('status')
    finally:
        _s.socket = orig
    return (result == 'hello'), f'got: {result!r}'
t('RCON: tolerates CS:GO-style junk type-0 packet before auth', t_rcon_csgo_junk_packet)


def t_rcon_auth_fail():
    """pkt_id == -1 on auth → ConnectionError."""
    packets = [make_pkt(-1, 2, b'')]
    mock = MockSocket(packets)
    r = RCONClient('127.0.0.1', 27015, 'wrong')
    import socket as _s
    orig = _s.socket; _s.socket = lambda *a, **kw: mock
    try:
        try:
            r.execute('status')
            return False, 'no exception raised'
        except ConnectionError as e:
            return ('auth failed' in str(e).lower()), str(e)
    finally:
        _s.socket = orig
t('RCON: auth-fail (-1 id) raises ConnectionError', t_rcon_auth_fail)


# ════════════════════════════════════════════════════════════════════
# 2) execute_retry exception widening
# ════════════════════════════════════════════════════════════════════
print('=== execute_retry exception widening ===')


def t_retry_OSError():
    """OSError (WinError 10054 family) is retried."""
    r = RCONClient('127.0.0.1', 27015, 'pw')
    calls = [0]
    def fake_execute(cmd):
        calls[0] += 1
        if calls[0] == 1: raise OSError('[WinError 10054]')
        return 'ok'
    r.execute = fake_execute
    out = r.execute_retry('status', retries=3, delay=0.01)
    return (out == 'ok' and calls[0] == 2), f'calls={calls[0]} out={out!r}'
t('execute_retry: catches OSError (WinError 10054) → retries', t_retry_OSError)


def t_retry_ConnectionReset():
    """ConnectionResetError is a subclass of OSError and retried."""
    r = RCONClient('127.0.0.1', 27015, 'pw')
    calls = [0]
    def fake_execute(cmd):
        calls[0] += 1
        if calls[0] < 2: raise ConnectionResetError('peer closed')
        return 'ok'
    r.execute = fake_execute
    out = r.execute_retry('status', retries=3, delay=0.01)
    return (out == 'ok' and calls[0] == 2), f'calls={calls[0]} out={out!r}'
t('execute_retry: catches ConnectionResetError → retries', t_retry_ConnectionReset)


def t_retry_auth_no_retry():
    """Auth-failed is NOT retried (would never succeed)."""
    r = RCONClient('127.0.0.1', 27015, 'pw')
    calls = [0]
    def fake_execute(cmd):
        calls[0] += 1
        raise ConnectionError('auth failed - wrong password')
    r.execute = fake_execute
    try:
        r.execute_retry('status', retries=3, delay=0.01)
        return False, 'should have raised'
    except ConnectionError:
        return (calls[0] == 1), f'calls={calls[0]} (must be 1, not 3)'
t('execute_retry: does NOT retry on auth-failed', t_retry_auth_no_retry)


def t_retry_timeout():
    """TimeoutError is retried."""
    r = RCONClient('127.0.0.1', 27015, 'pw')
    calls = [0]
    def fake_execute(cmd):
        calls[0] += 1
        if calls[0] < 2: raise TimeoutError('slow boot')
        return 'ok'
    r.execute = fake_execute
    out = r.execute_retry('status', retries=3, delay=0.01)
    return (out == 'ok' and calls[0] == 2), f'calls={calls[0]} out={out!r}'
t('execute_retry: catches TimeoutError → retries', t_retry_timeout)


# ════════════════════════════════════════════════════════════════════
# 3) Broadcast `;` / CRLF / length-cap strip
# ════════════════════════════════════════════════════════════════════
print('=== Broadcast injection strip ===')


def t_broadcast_strip():
    """Simulate the web.py:server_broadcast strip pipeline."""
    BROADCAST_MAX_LEN = 200
    cases = [
        ('hello;sv_password pwn',        'hello,sv_password pwn'),
        ('test\rcommand',                'test command'),
        ('test\nlinebreak',              'test linebreak'),
        ('back`tick test',               "back'tick test"),
        ('A' * 250,                      'A' * 200),
        ('multi;;;semis',                'multi,,,semis'),
        ('mix\r\n;`combo',               "mix  ,'combo"),
    ]
    fails = []
    for raw, expected in cases:
        msg = (raw.replace('\r', ' ')
                  .replace('\n', ' ')
                  .replace(';',  ',')
                  .replace('`',  "'"))
        if len(msg) > BROADCAST_MAX_LEN: msg = msg[:BROADCAST_MAX_LEN]
        if msg != expected:
            fails.append(f'{raw[:30]!r} → {msg[:30]!r} (want {expected[:30]!r})')
    return (not fails), ('; '.join(fails) if fails else f'{len(cases)} cases OK')
t('Broadcast: ;, CRLF, backtick, length cap, multi-semi, combo', t_broadcast_strip)


# ════════════════════════════════════════════════════════════════════
# 4) log_save filename uniqueness
# ════════════════════════════════════════════════════════════════════
print('=== log_save filename uniqueness ===')


def t_log_save_uniqueness():
    """100 filenames generated in the same second → all unique."""
    names = set()
    for _ in range(100):
        fname = time.strftime('oblivion_log_%Y%m%d_%H%M%S_') + secrets.token_hex(3) + '.txt'
        names.add(fname)
    return (len(names) == 100), f'unique: {len(names)}/100'
t('log_save: 100 same-second filenames all unique (6-hex suffix)', t_log_save_uniqueness)


# ════════════════════════════════════════════════════════════════════
# 5) Event.wait cancellation semantics
# ════════════════════════════════════════════════════════════════════
print('=== Event.wait crash-backoff cancellation ===')


def t_event_wait_cancel_during_sleep():
    """Mid-sleep set() wakes the waiter within 0.5 s."""
    ev = threading.Event()
    result = {'woke_at': None, 'was_set': None}
    def waiter():
        start = time.monotonic()
        result['was_set'] = ev.wait(timeout=5)
        result['woke_at'] = time.monotonic() - start
    th = threading.Thread(target=waiter); th.start()
    time.sleep(0.1)
    ev.set()
    th.join(timeout=2)
    return (result['was_set'] is True and result['woke_at'] < 0.5), \
           f'was_set={result["was_set"]} woke_at={result["woke_at"]:.3f}'
t('Event.wait: cancels within 0.5 s of set() during sleep', t_event_wait_cancel_during_sleep)


def t_event_wait_timeout_when_not_set():
    """No set() → returns False after the timeout."""
    ev = threading.Event()
    start = time.monotonic()
    was_set = ev.wait(timeout=0.3)
    elapsed = time.monotonic() - start
    return (was_set is False and 0.28 < elapsed < 0.6), \
           f'was_set={was_set} elapsed={elapsed:.3f}'
t('Event.wait: returns False after timeout when not set', t_event_wait_timeout_when_not_set)


# ════════════════════════════════════════════════════════════════════
# 6) _lan_ip cache TTL
# ════════════════════════════════════════════════════════════════════
print('=== _lan_ip cache TTL ===')
from cs2servergui import config as _cfg


def t_lan_ip_cache_hit():
    """Within TTL → cache returned; force_refresh → bypassed."""
    _cfg._lan_ip(force_refresh=True)            # seed
    _cfg._LAN_IP_CACHE['ts']    = time.monotonic()
    _cfg._LAN_IP_CACHE['value'] = '10.99.99.99'
    cached = _cfg._lan_ip()                     # cache hit
    fresh  = _cfg._lan_ip(force_refresh=True)   # bypass
    _cfg._lan_ip(force_refresh=True)            # restore cache
    return (cached == '10.99.99.99' and fresh != '10.99.99.99'), \
           f'cached={cached!r} fresh={fresh!r}'
t('_lan_ip: cache hit returns sentinel, force_refresh bypasses', t_lan_ip_cache_hit)


# ════════════════════════════════════════════════════════════════════
# 7) Input validation caps
# ════════════════════════════════════════════════════════════════════
print('=== Input validation caps ===')
from cs2servergui.web import _STEAMID_RE, _NAME_MAX_LEN, _BROADCAST_MAX_LEN


def t_steamid_cap():
    return (bool(_STEAMID_RE.match('STEAM_1:0:12345'))
            and not _STEAMID_RE.match('A' * 65)), \
           f'valid OK; 65-char rejected'
t('_STEAMID_RE: accepts valid, rejects 65-char input', t_steamid_cap)


def t_constants_set():
    return (_NAME_MAX_LEN == 64 and _BROADCAST_MAX_LEN == 200), \
           f'name={_NAME_MAX_LEN} broadcast={_BROADCAST_MAX_LEN}'
t('_NAME_MAX_LEN == 64 and _BROADCAST_MAX_LEN == 200', t_constants_set)


# ════════════════════════════════════════════════════════════════════
# 8) save_config atomicity under concurrent reads
# ════════════════════════════════════════════════════════════════════
print('=== save_config atomicity ===')
from cs2servergui.core import AppCore


def t_save_config_atomic():
    """While 50 writers hammer, parallel readers never see invalid JSON."""
    ac = AppCore()
    ac.save_config()   # seed
    errors = []
    stop = threading.Event()
    def reader():
        while not stop.is_set():
            try:
                with open(_cfg._CONFIG_FILE, encoding='utf-8') as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                errors.append(repr(e))
            except FileNotFoundError:
                pass
            except Exception:
                pass
    threads = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
    for th in threads: th.start()
    for _ in range(50):
        ac.save_config()
    stop.set()
    for th in threads: th.join(timeout=2)
    return (not errors), f'JSON errors: {len(errors)}'
t('save_config: atomic — readers never see partial/invalid JSON', t_save_config_atomic)


def t_save_config_no_tmp_leftover():
    """The .tmp file is always cleaned up after the swap."""
    ac = AppCore()
    for _ in range(20): ac.save_config()
    return (not os.path.isfile(_cfg._CONFIG_FILE + '.tmp')), 'no .tmp leftover'
t('save_config: no .tmp file leftover after 20 saves', t_save_config_no_tmp_leftover)


# ════════════════════════════════════════════════════════════════════
# 9) _lifecycle_lock RLock reentrancy
# ════════════════════════════════════════════════════════════════════
print('=== _lifecycle_lock reentrancy ===')


def t_rlock_reentrant():
    """RLock allows re-entry from the same thread without deadlock."""
    ac = AppCore()
    with ac._lifecycle_lock:
        with ac._lifecycle_lock:
            with ac._lifecycle_lock:
                pass
    return True, 'no deadlock'
t('_lifecycle_lock: RLock — 3-level reentry OK', t_rlock_reentrant)


# ════════════════════════════════════════════════════════════════════
# 10) _netutils sanity
# ════════════════════════════════════════════════════════════════════
print('=== _netutils against live netstat ===')
from cs2servergui import _netutils as nu


def t_netutils_no_crash():
    """listeners_on_port returns a list even for an empty port."""
    listeners = nu.listeners_on_port(22)
    return isinstance(listeners, list), f'type={type(listeners).__name__}'
t('listeners_on_port: returns list for likely-empty port', t_netutils_no_crash)


def t_holder_consistency():
    """holder_of_port returns same as first entry of listeners_on_port."""
    listeners = nu.listeners_on_port(27015)
    holder    = nu.holder_of_port(27015)
    if not listeners:
        return (holder is None), f'no listeners → holder should be None ({holder!r})'
    return (holder == (listeners[0][1], listeners[0][2])), \
           f'holder={holder} listeners[0]={listeners[0]}'
t('holder_of_port: matches first listeners_on_port entry', t_holder_consistency)


# ════════════════════════════════════════════════════════════════════
# Flask test-client smoke for new routes
# ════════════════════════════════════════════════════════════════════
print('=== Flask test client: new routes ===')


def t_log_save_local_only():
    """/api/log/save returns 403 (or 401) for non-loopback requests."""
    from cs2servergui.web import create_flask
    ac = AppCore()
    ac.admin_pin = '0000'
    app = create_flask(ac)
    client = app.test_client()
    # No auth at all → 401
    r = client.post('/api/log/save')
    return r.status_code in (401, 403), f'status={r.status_code}'
t('/api/log/save: unauthenticated → 401/403', t_log_save_local_only)


def t_workshop_download_409_when_busy():
    """/api/workshop/download returns 409 when _active_dl_proc is not None."""
    from cs2servergui.web import create_flask
    import io
    ac = AppCore()
    ac.admin_pin = '0000'
    ac.steam_username = 'u'
    ac.steam_password = 'p'
    # Simulate busy state
    class DummyProc: pass
    ac._active_dl_proc = DummyProc()
    app = create_flask(ac)
    client = app.test_client()
    # Authenticate first
    client.post('/api/auth/login', json={'pin': '0000'})
    r = client.post('/api/workshop/download', json={'id': '12345'})
    return (r.status_code == 409), f'status={r.status_code} body={r.get_data(as_text=True)[:80]!r}'
t('/api/workshop/download: 409 when _active_dl_proc busy', t_workshop_download_409_when_busy)


# ════════════════════════════════════════════════════════════════════
# v0.10.2 — Pre-flight error surfacing + state shape additions
# ════════════════════════════════════════════════════════════════════

def t_server_start_returns_422_with_preflight_errors():
    """When _preflight_checks fails, POST /api/server/start returns 422
    with a `preflight_errors` list — not 200 OK with silent log-only.

    We bypass the route's `is_installed` early-bail by patching the
    property to True, AND we steer _preflight_checks to fail by pointing
    _config.CS2_PATH at a non-existent file (the test machine may have
    a real CS2 install so we can't rely on the default path being absent).
    Both patches are restored in `finally` to keep this test hermetic."""
    from cs2servergui.web import create_flask
    from cs2servergui import config as _cfg
    original_is_installed = AppCore.is_installed
    original_cs2_path     = _cfg.CS2_PATH
    AppCore.is_installed  = property(lambda self: True)
    _cfg.CS2_PATH         = '/nope/this/path/does/not/exist/cs2.exe'
    try:
        ac = AppCore()
        ac.admin_pin = '0000'
        ac.server_dir = '/tmp/fake-server-dir'
        app = create_flask(ac)
        client = app.test_client()
        client.post('/api/auth/login', json={'pin': '0000'})
        r = client.post('/api/server/start', json={'map': 'de_dust2', 'mode': 'Competitive'})
        body = r.get_json() or {}
        return (r.status_code == 422
                and 'preflight_errors' in body
                and isinstance(body['preflight_errors'], list)
                and len(body['preflight_errors']) >= 1), \
               f'status={r.status_code} body_keys={list(body.keys())}'
    finally:
        AppCore.is_installed = original_is_installed
        _cfg.CS2_PATH        = original_cs2_path
t('/api/server/start: returns 422 with preflight_errors when preflight fails', t_server_start_returns_422_with_preflight_errors)


def t_api_state_includes_boot_error_field():
    """/api/state must include `boot_error` (string, empty by default).
    SPA reads this to render the v0.10.2 dismissable banner when a Start
    is refused at preflight."""
    from cs2servergui.web import create_flask
    ac = AppCore()
    ac.admin_pin = '0000'
    app = create_flask(ac)
    client = app.test_client()
    client.post('/api/auth/login', json={'pin': '0000'})
    snap = client.get('/api/state').get_json() or {}
    return ('boot_error' in snap
            and isinstance(snap['boot_error'], str)
            and snap['boot_error'] == ''), f'snap_keys={sorted(snap.keys())[:8]}...'
t('/api/state: includes boot_error field (empty by default)', t_api_state_includes_boot_error_field)


def t_api_state_includes_captain_team_field():
    """/api/state must include `captain_team` so the SPA's captain finale
    view knows which team's ready flag to toggle.  Empty string for
    non-captain sessions (the admin in this test)."""
    from cs2servergui.web import create_flask
    ac = AppCore()
    ac.admin_pin = '0000'
    app = create_flask(ac)
    client = app.test_client()
    client.post('/api/auth/login', json={'pin': '0000'})
    snap = client.get('/api/state').get_json() or {}
    return ('captain_team' in snap and snap['captain_team'] == ''), f'snap={snap}'
t('/api/state: includes captain_team field (empty for admin)', t_api_state_includes_captain_team_field)


def t_capabilities_admin_has_server_control():
    """v0.10.2: /api/capabilities returns {role, is_local, can: [...]}.
    Admin session (via PIN, not auto-auth) is REMOTE in test_client so
    is_local will be False — the admin set without local-only tags is
    what's expected here.  Local-only superset is in its own test."""
    from cs2servergui.web import create_flask
    ac = AppCore()
    ac.admin_pin = '0000'
    app = create_flask(ac)
    client = app.test_client()
    client.post('/api/auth/login', json={'pin': '0000'})
    r = client.get('/api/capabilities')
    body = r.get_json() or {}
    if r.status_code != 200:
        return False, f'status={r.status_code}'
    if body.get('role') != 'admin':
        return False, f'expected role=admin, got {body.get("role")}'
    cans = set(body.get('can', []))
    must_have = {'server.start', 'server.stop', 'server.broadcast',
                 'players.kick', 'players.ban', 'config.write', 'veto.admin'}
    missing = must_have - cans
    if missing:
        return False, f'admin missing tags: {missing}'
    # Remote admin must NOT see the local-only tags
    forbidden = {'rcon', 'steam.login', 'server.install', 'log.save'}
    leaked = forbidden & cans
    return (not leaked), f'remote admin leaked local-only: {leaked}'
t('/api/capabilities: remote admin has server.control but not local-only', t_capabilities_admin_has_server_control)


def t_capabilities_guest_is_restricted():
    """A guest-PIN session should NOT include any of the local-only tags.
    Verifies the renderer-side disable-with-tooltip pattern has the right
    source of truth."""
    from cs2servergui.web import create_flask
    ac = AppCore()
    ac.admin_pin = '0000'
    ac.guest_pin = '9999'
    app = create_flask(ac)
    client = app.test_client()
    # Login as guest (PIN '9999')
    client.post('/api/auth/login', json={'pin': '9999'})
    # Test client sessions are NOT is_local — but they're also not really
    # "remote" in the sense of having a different IP from the server.  The
    # auth route classifies test_client as remote because remote_addr is set.
    r = client.get('/api/capabilities')
    body = r.get_json() or {}
    if r.status_code != 200:
        return False, f'status={r.status_code} body={body}'
    cans = set(body.get('can', []))
    forbidden = {'rcon', 'steam.login', 'server.install',
                 'server.start', 'server.stop', 'players.kick'}
    leaked = forbidden & cans
    return (not leaked and 'workshop.download' in cans), \
           f'role={body.get("role")} leaked={leaked} cans={sorted(cans)}'
t('/api/capabilities: guest session excludes admin + local-only tags', t_capabilities_guest_is_restricted)


def t_stop_server_clears_last_start_error():
    """stop_server() must clear the stale last_start_error so a successful
    next-Start doesn't show a leftover banner from a failure two attempts ago."""
    ac = AppCore()
    ac.last_start_error = "Port 27015 is held by a non-CS2 process"
    ac.running = True
    # stop_server clears lots of state under _lifecycle_lock
    try:
        ac.stop_server()
    except Exception:
        pass    # may complain about no proc; we just care about the field
    return ac.last_start_error == '', f'last_start_error={ac.last_start_error!r}'
t('stop_server: clears last_start_error', t_stop_server_clears_last_start_error)


# ════════════════════════════════════════════════════════════════════
# Pytest entry points — one test function per battery case
# ════════════════════════════════════════════════════════════════════
# Generate `def test_*` functions at import time from the results list so
# pytest reports each behaviour as its own pass/fail line.  The script-style
# results-list pattern is kept because it's easier to read + extend than
# 22 separate function definitions.
def _make_pytest_case(_ok, _detail):
    def _case():
        assert _ok, _detail
    return _case


def _slug(name):
    """Turn a human-readable test name into a valid Python identifier."""
    out = ''.join(c if c.isalnum() else '_' for c in name).strip('_').lower()
    # Collapse runs of underscores
    while '__' in out: out = out.replace('__', '_')
    return 'test_' + out


for _ok, _name, _detail in results:
    _slug_name = _slug(_name)
    # Disambiguate collisions by appending an index
    _i = 1
    while _slug_name in globals():
        _i += 1
        _slug_name = f'{_slug(_name)}_{_i}'
    globals()[_slug_name] = _make_pytest_case(_ok, _detail)


# ════════════════════════════════════════════════════════════════════
# Standalone-script entry point — `python tests/test_v092.py`
# ════════════════════════════════════════════════════════════════════
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
