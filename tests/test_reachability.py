"""
tests/test_reachability.py — v1.2 reachability via Steam master server.

Doesn't hit Valve's real Web API; monkey-patches urlopen so the suite
stays hermetic.  Covers:
    * Steam Web API call + JSON parse
    * CS2-only filtering (appid==730)
    * Network / HTTP / non-JSON error paths
    * Hint engine: every state combo (running, gslt, uptime, server-present)
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest


# ── check_steam_master ──────────────────────────────────────────────────

def _stub_urlopen_returning(body: bytes):
    class _FakeResp:
        def __init__(self, b): self._b = b
        def __enter__(self):   return self
        def __exit__(self, *_): return False
        def read(self):        return self._b
    def _fake(req, timeout=None):
        return _FakeResp(body)
    return _fake


def test_check_requires_public_ip():
    from cs2servergui import reachability
    with pytest.raises(reachability.ReachabilityError):
        reachability.check_steam_master("")


def test_check_happy_path_filters_to_cs2(monkeypatch):
    from cs2servergui import reachability
    payload = {"response": {"success": True, "servers": [
        {"addr": "1.2.3.4:27015", "gameport": 27015, "appid": 730, "secure": True},
        {"addr": "1.2.3.4:27016", "gameport": 27016, "appid":  10, "secure": True},  # CS:S, ignored
        {"addr": "1.2.3.4:27017", "gameport": 27017, "appid": 730, "secure": False},
    ]}}
    monkeypatch.setattr(reachability.urllib.request, "urlopen",
                        _stub_urlopen_returning(json.dumps(payload).encode()))
    out = reachability.check_steam_master("1.2.3.4")
    assert out["target"] == "1.2.3.4"
    assert out["ok"] is True
    assert {s["gameport"] for s in out["servers"]} == {27015, 27017}


def test_check_empty_servers_is_success(monkeypatch):
    """No servers registered at this IP is a SUCCESSFUL response — the
    interpret() function uses servers=[] to detect 'invisible to Valve'."""
    from cs2servergui import reachability
    payload = {"response": {"success": True, "servers": []}}
    monkeypatch.setattr(reachability.urllib.request, "urlopen",
                        _stub_urlopen_returning(json.dumps(payload).encode()))
    out = reachability.check_steam_master("1.2.3.4")
    assert out["ok"] is True
    assert out["servers"] == []


def test_check_surfaces_http_error(monkeypatch):
    from cs2servergui import reachability
    def _fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable",
                                       {}, io.BytesIO(b""))
    monkeypatch.setattr(reachability.urllib.request, "urlopen", _fake)
    with pytest.raises(reachability.ReachabilityError) as exc:
        reachability.check_steam_master("1.2.3.4")
    assert "503" in str(exc.value)


def test_check_surfaces_network_error(monkeypatch):
    from cs2servergui import reachability
    def _fake(req, timeout=None):
        raise urllib.error.URLError("Name or service not known")
    monkeypatch.setattr(reachability.urllib.request, "urlopen", _fake)
    with pytest.raises(reachability.ReachabilityError) as exc:
        reachability.check_steam_master("1.2.3.4")
    assert "unreachable" in str(exc.value).lower()


def test_check_surfaces_non_json(monkeypatch):
    from cs2servergui import reachability
    monkeypatch.setattr(reachability.urllib.request, "urlopen",
                        _stub_urlopen_returning(b"<html>steam down</html>"))
    with pytest.raises(reachability.ReachabilityError) as exc:
        reachability.check_steam_master("1.2.3.4")
    assert "non-json" in str(exc.value).lower()


# ── Hint engine ─────────────────────────────────────────────────────────

def _hint(servers, *, gslt_set=True, running=True, uptime=600, port=27015):
    """Convenience: build a synthetic raw result + run interpret."""
    from cs2servergui.reachability import interpret
    raw = {"target": "1.2.3.4", "ok": True, "servers": servers}
    return interpret(raw, gslt_set=gslt_set, server_running=running,
                     server_uptime_secs=uptime, expected_port=port)[0]


def test_hint_server_offline_is_info():
    h = _hint([], running=False)
    assert h["severity"] == "info"
    assert "offline" in h["message"].lower()


def test_hint_gslt_missing_fail():
    """Running but no GSLT — fatal for external visibility."""
    h = _hint([], running=True, gslt_set=False)
    assert h["severity"] == "fail"
    assert "gslt" in h["message"].lower()
    assert "steamcommunity.com/dev/managegameservers" in (h["fix"] or "")


def test_hint_recently_started_is_info():
    """Just started — Valve master server has 30-90s discovery lag."""
    h = _hint([], running=True, gslt_set=True, uptime=15)
    assert h["severity"] == "info"
    assert "wait" in (h["fix"] or "").lower()


def test_hint_visible_in_master_is_ok():
    h = _hint(
        [{"addr": "1.2.3.4:27015", "gameport": 27015, "appid": 730, "secure": True}],
    )
    assert h["severity"] == "ok"
    assert "players can connect" in h["message"].lower()


def test_hint_vac_disabled_flagged_on_ok():
    h = _hint(
        [{"addr": "1.2.3.4:27015", "gameport": 27015, "appid": 730, "secure": False}],
    )
    assert h["severity"] == "ok"
    assert "vac disabled" in h["message"].lower()


def test_hint_wrong_port_is_warn():
    """Valve sees us, but on a port that isn't what we expected."""
    h = _hint(
        [{"addr": "1.2.3.4:27020", "gameport": 27020, "appid": 730}],
        port=27015,
    )
    assert h["severity"] == "warn"
    assert "27020" in h["message"]
    assert "27015" in h["message"]


def test_hint_invisible_blames_forward_then_cgnat():
    h = _hint([], running=True, gslt_set=True, uptime=600)
    assert h["severity"] == "fail"
    msg_lower = h["message"].lower()
    fix_lower = (h["fix"] or "").lower()
    assert "cannot see" in msg_lower or "invisible" in msg_lower
    assert "forward" in fix_lower
    assert "cgnat" in fix_lower


def test_hint_severity_always_valid():
    """Every hint must have severity ∈ {ok, warn, fail, info}."""
    cases = [
        ([], {"running": False}),
        ([], {"gslt_set": False}),
        ([], {"uptime": 10}),
        ([], {}),
        ([{"addr": "1.2.3.4:27015", "gameport": 27015, "appid": 730, "secure": True}], {}),
        ([{"addr": "1.2.3.4:27020", "gameport": 27020, "appid": 730}], {}),
    ]
    for servers, kwargs in cases:
        h = _hint(servers, **kwargs)
        assert h["severity"] in {"ok", "warn", "fail", "info"}, h
