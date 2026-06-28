"""
tests/test_reachability.py — v1.2 reachability probe client + hint engine.

Doesn't hit the real probe service; monkey-patches urlopen so the test
suite stays hermetic.  Covers:
    * URL resolution (explicit > config > default)
    * Empty-URL error path (feature off until probe is configured)
    * HTTP error / non-JSON / network-error mapping
    * Hint engine: every (tcp_status, udp_status) combination
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest


# ── _resolve_probe_url ─────────────────────────────────────────────────

def test_resolve_probe_url_prefers_explicit_override(monkeypatch):
    from cs2servergui import reachability, config as _cfg
    monkeypatch.setattr(_cfg, "REACHABILITY_PROBE_URL", "https://from-config.example/")
    assert reachability._resolve_probe_url("https://override.example/check") == \
        "https://override.example/check"


def test_resolve_probe_url_falls_back_to_config(monkeypatch):
    from cs2servergui import reachability, config as _cfg
    monkeypatch.setattr(_cfg, "REACHABILITY_PROBE_URL", "  https://from-config.example/  ")
    assert reachability._resolve_probe_url(None) == "https://from-config.example/"


def test_resolve_probe_url_empty_when_unset(monkeypatch):
    from cs2servergui import reachability, config as _cfg
    monkeypatch.setattr(_cfg, "REACHABILITY_PROBE_URL", "")
    assert reachability._resolve_probe_url(None) == ""


# ── check_reachability error paths ─────────────────────────────────────

def test_check_reachability_raises_when_no_url_configured(monkeypatch):
    from cs2servergui import reachability, config as _cfg
    monkeypatch.setattr(_cfg, "REACHABILITY_PROBE_URL", "")
    with pytest.raises(reachability.ReachabilityError) as exc:
        reachability.check_reachability([27015])
    assert "probe URL" in str(exc.value).lower() or "configured" in str(exc.value).lower()


def test_check_reachability_validates_port_count(monkeypatch):
    from cs2servergui import reachability
    monkeypatch.setattr(reachability, "_resolve_probe_url",
                        lambda override: "https://probe.example/check")
    with pytest.raises(reachability.ReachabilityError):
        reachability.check_reachability([])
    with pytest.raises(reachability.ReachabilityError):
        reachability.check_reachability([1, 2, 3, 4, 5])


def _stub_urlopen_returning(body: bytes):
    class _FakeResp:
        def __init__(self, b): self._b = b
        def __enter__(self):   return self
        def __exit__(self, *_): return False
        def read(self):        return self._b
    def _fake(req, timeout=None):
        return _FakeResp(body)
    return _fake


def test_check_reachability_happy_path(monkeypatch):
    from cs2servergui import reachability
    payload = {"target": "1.2.3.4", "results": [
        {"port": 27015, "tcp": {"status": "open"}, "udp": {"status": "open"}},
    ]}
    monkeypatch.setattr(reachability, "_resolve_probe_url",
                        lambda override: "https://probe.example/check")
    monkeypatch.setattr(reachability.urllib.request, "urlopen",
                        _stub_urlopen_returning(json.dumps(payload).encode()))
    result = reachability.check_reachability([27015])
    assert result == payload


def test_check_reachability_surfaces_429_rate_limit(monkeypatch):
    from cs2servergui import reachability
    monkeypatch.setattr(reachability, "_resolve_probe_url",
                        lambda override: "https://probe.example/check")
    err_body = json.dumps({"error": "rate limit (10/min per IP)"}).encode()
    def _fake(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 429, "Too Many Requests", {}, io.BytesIO(err_body)
        )
    monkeypatch.setattr(reachability.urllib.request, "urlopen", _fake)
    with pytest.raises(reachability.ReachabilityError) as exc:
        reachability.check_reachability([27015])
    assert "429" in str(exc.value) and "rate limit" in str(exc.value).lower()


def test_check_reachability_surfaces_network_error(monkeypatch):
    from cs2servergui import reachability
    monkeypatch.setattr(reachability, "_resolve_probe_url",
                        lambda override: "https://probe.example/check")
    def _fake(req, timeout=None):
        raise urllib.error.URLError("Name or service not known")
    monkeypatch.setattr(reachability.urllib.request, "urlopen", _fake)
    with pytest.raises(reachability.ReachabilityError) as exc:
        reachability.check_reachability([27015])
    assert "unreachable" in str(exc.value).lower()


def test_check_reachability_surfaces_non_json(monkeypatch):
    from cs2servergui import reachability
    monkeypatch.setattr(reachability, "_resolve_probe_url",
                        lambda override: "https://probe.example/check")
    monkeypatch.setattr(reachability.urllib.request, "urlopen",
                        _stub_urlopen_returning(b"<html>oops</html>"))
    with pytest.raises(reachability.ReachabilityError) as exc:
        reachability.check_reachability([27015])
    assert "non-json" in str(exc.value).lower()


# ── Hint engine ────────────────────────────────────────────────────────

def _interpret(tcp_status, udp_status, port=27015):
    """Helper: run interpret() against a single-port synthetic result."""
    from cs2servergui.reachability import interpret
    return interpret({"results": [
        {"port": port,
         "tcp": {"status": tcp_status} if tcp_status else None,
         "udp": {"status": udp_status} if udp_status else None}
    ]})[0]


def test_hint_both_open_is_ok():
    h = _interpret("open", "open")
    assert h["severity"] == "ok"
    assert h["fix"] is None


def test_hint_tcp_open_udp_unknown_warns_about_tcp_only_forward():
    h = _interpret("open", "unknown")
    assert h["severity"] == "warn"
    assert "udp" in h["message"].lower()


def test_hint_udp_open_tcp_closed_explains_rcon_only_impact():
    h = _interpret("closed", "open")
    assert h["severity"] == "warn"
    assert "rcon" in (h["fix"] or "").lower()


def test_hint_filtered_blames_router_or_isp():
    h = _interpret("filtered", "unknown")
    assert h["severity"] == "fail"
    assert "router" in (h["fix"] or "").lower() or \
           "isp" in (h["fix"] or "").lower()


def test_hint_tcp_closed_suggests_forward_rule():
    h = _interpret("closed", "unknown")
    # closed+unknown is the "common stale DHCP forward" case — fail+router fix.
    assert h["severity"] == "fail"
    assert "forward" in (h["fix"] or "").lower()


def test_hint_handles_missing_protocol_blocks():
    """If the probe couldn't run one protocol (None status), still emit a hint."""
    from cs2servergui.reachability import interpret
    out = interpret({"results": [{"port": 27015,
                                   "tcp": {"status": "open"},
                                   "udp": None}]})
    assert len(out) == 1
    assert out[0]["port"] == 27015


def test_hint_skips_entries_without_port():
    from cs2servergui.reachability import interpret
    out = interpret({"results": [
        {"port": 27015, "tcp": {"status": "open"}, "udp": {"status": "open"}},
        {"port": "bogus", "tcp": {"status": "open"}, "udp": {"status": "open"}},
        {"tcp": {"status": "open"}},
    ]})
    assert len(out) == 1
    assert out[0]["port"] == 27015


def test_hint_severity_set_matches_doc():
    """Every hint must have severity ∈ {ok, warn, fail}.  Frontend uses
    this to pick badge colour; an unknown value would render blank."""
    from cs2servergui.reachability import interpret
    pairs = [("open", "open"), ("open", "unknown"), ("closed", "open"),
             ("filtered", "unknown"), ("closed", "unknown"),
             ("error", "error"), (None, None)]
    for tcp, udp in pairs:
        out = interpret({"results": [{"port": 27015,
                                       "tcp": {"status": tcp} if tcp else None,
                                       "udp": {"status": udp} if udp else None}]})
        assert out[0]["severity"] in {"ok", "warn", "fail"}, \
            f"unknown severity for (tcp={tcp}, udp={udp}): {out[0]}"
