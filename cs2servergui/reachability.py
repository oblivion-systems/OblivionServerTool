"""
cs2servergui/reachability.py — "can players actually reach my server?"

v1.2 polish (#168 follow-on).  Operators routinely forward a port to
the wrong LAN IP (stale DHCP lease, typo) and discover it only when
a live tournament can't start.  The local pre-flight can't detect this
because most home routers don't support NAT hairpinning — the server
can't probe its own public IP from inside its own LAN.

This module calls a tiny external probe service (see probe/probe.py)
that connects back to the operator's source IP.  By design the probe
ONLY probes the source — operators can't aim it at someone else.

Public API
----------
check_reachability(ports, *, probe_url=None, timeout=12.0) -> dict
    Raw probe result (see probe/probe.py wire protocol).
interpret(result) -> list[Hint]
    Operator-facing hints derived from the raw result.

Hint
----
{ "severity": "ok" | "warn" | "fail",
  "port":      int,
  "message":   str,
  "fix":       str | None }   # one-line suggestion
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from cs2servergui import config as _config


class ReachabilityError(Exception):
    """Probe service unreachable or returned an error response."""


# Default URL — operators override via oblivion_config.json's
# "reachability_probe_url".  Empty default means "feature off"; only
# the official-deploy / self-hosted URL turns it on.
_DEFAULT_PROBE_URL = ""


def _resolve_probe_url(override: str | None) -> str:
    """Resolve the probe URL — explicit override > config file > default.
    Returns "" if no URL is configured anywhere (feature disabled)."""
    if override:
        return override.strip()
    cfg = getattr(_config, "REACHABILITY_PROBE_URL", "") or ""
    return cfg.strip() or _DEFAULT_PROBE_URL


def check_reachability(
    ports: list[int],
    *,
    probe_url: str | None = None,
    timeout: float = 12.0,
) -> dict:
    """POST to the probe service; return its raw JSON response.

    Raises ReachabilityError if no probe URL is configured, the service
    is unreachable, returns non-200, or returns non-JSON.
    """
    url = _resolve_probe_url(probe_url)
    if not url:
        raise ReachabilityError(
            "No reachability probe URL configured. Set "
            "reachability_probe_url in oblivion_config.json — see "
            "probe/README.md for self-host or Fly.io deploy steps."
        )
    if not (1 <= len(ports) <= 4):
        raise ReachabilityError(
            f"check_reachability: 1-4 ports required, got {len(ports)}"
        )
    body = json.dumps({"ports": list(ports), "protocol": "both"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent":   f"oblivion-server-tool/{_config.APP_VERSION}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        # 429 (rate limit) and 400 (bad input) both return JSON we want to surface.
        try:
            detail = json.loads(exc.read().decode("utf-8", errors="replace"))
            msg = detail.get("error") or str(exc)
        except Exception:
            msg = str(exc)
        raise ReachabilityError(
            f"Probe service returned HTTP {exc.code}: {msg}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ReachabilityError(f"Probe service unreachable: {exc.reason}") from exc
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReachabilityError(
            f"Probe service returned non-JSON ({len(data)} bytes): {exc}"
        ) from exc


# ── Hint engine ──────────────────────────────────────────────────────

# Operator-facing rules.  Order matters: first matching rule wins.
# Each rule receives (tcp_status, udp_status) and returns
# (severity, message, fix) or None to skip.

def _classify(tcp: str | None, udp: str | None, port: int) -> dict:
    """Map raw per-protocol statuses to an operator-facing hint."""
    # tcp ∈ {open, closed, filtered, error, None}; udp ∈ {open, unknown, error, None}.
    if tcp == "open" and udp == "open":
        return {"severity": "ok", "port": port,
                "message": f"Port {port}: TCP and UDP both open.",
                "fix": None}
    if tcp == "open" and udp == "unknown":
        return {"severity": "warn", "port": port,
                "message": f"Port {port}: TCP open, UDP not responding.",
                "fix": ("Either the forward rule is TCP-only (players can't connect "
                        "to the game), or CS2 isn't actually running. Add a UDP rule "
                        "for this port in your router admin, then re-check.")}
    if tcp != "open" and udp == "open":
        return {"severity": "warn", "port": port,
                "message": f"Port {port}: UDP reaches CS2, but TCP is closed.",
                "fix": ("Players can connect, but RCON-from-outside won't work. "
                        "If you don't expose RCON externally, you can ignore this. "
                        "Otherwise add a TCP rule for this port in your router.")}
    if tcp == "filtered" or udp == "unknown":
        return {"severity": "fail", "port": port,
                "message": f"Port {port}: timed out — router or ISP is dropping packets.",
                "fix": ("Most common: the port forward in your router targets the "
                        "wrong LAN IP (stale DHCP lease). Verify the forward points "
                        "at THIS machine's current LAN IP, then re-check. "
                        "If that's right and it still fails, your ISP may be blocking "
                        "the port (less common) or you're behind CGNAT.")}
    if tcp == "closed":
        return {"severity": "fail", "port": port,
                "message": f"Port {port}: closed — nothing forwarded, or forward points elsewhere.",
                "fix": ("Add a port-forward rule in your router for both TCP and UDP "
                        "on this port, targeting THIS machine's LAN IP. Set a DHCP "
                        "reservation so the IP can't change.")}
    # Catch-all for partial / error results.
    return {"severity": "warn", "port": port,
            "message": f"Port {port}: probe returned an unusual combination "
                       f"(tcp={tcp!r}, udp={udp!r}).",
            "fix": "Re-run the check; if it persists, file an issue with this status pair."}


def interpret(result: dict) -> list[dict]:
    """Convert the probe service's raw result into operator-facing hints.

    Returns one hint per probed port.  Each hint:
        {"severity": "ok" | "warn" | "fail",
         "port":      int,
         "message":   str,
         "fix":       str | None}
    """
    hints: list[dict] = []
    for port_result in (result.get("results") or []):
        port = port_result.get("port")
        if not isinstance(port, int):
            continue
        tcp = (port_result.get("tcp") or {}).get("status")
        udp = (port_result.get("udp") or {}).get("status")
        hints.append(_classify(tcp, udp, port))
    return hints
