"""
cs2servergui/reachability.py — "can players actually reach my server?"

v1.2 polish.  Operators routinely forward a port to the wrong LAN IP
(stale DHCP lease, typo) and discover it only when a tournament can't
start.  The local pre-flight can't catch this — NAT hairpinning means
the server can't probe its own public IP from inside its own LAN.

Strategy: query **Valve's Steam master server** instead of running our
own probe service.  This is better than a custom probe because:

    1. Zero infrastructure to host — Steam Web API is free, no key
       required, exists indefinitely.
    2. Authoritative.  What we actually want to know is "can Steam
       clients reach my server."  Valve's master server IS the system
       that answers that question for the entire CS2 player population.
    3. Catches a class of failures a port-probe wouldn't: GSLT missing
       (server can't authenticate with Valve → can't register).

Trade-offs accepted:
    * Master server takes 30-90s to register a newly-started server.
      The hint engine surfaces "give it a minute…" on fresh boots.
    * Requires GSLT set.  But GSLT is required for external players
       anyway — the diagnostic correctly says "set GSLT first" when
       absent.
    * Doesn't distinguish "TCP forwarded but UDP missing" — Valve's
       check IS the UDP check (master server reaches via UDP 27015).
       Same-port forwards are the common case anyway.

Public API
----------
check_steam_master(public_ip)               -> dict
    Raw Steam Web API response, normalised.
interpret(result, *, gslt_set, server_running, server_uptime_secs,
          expected_port)                    -> list[Hint]
    Operator-facing hints derived from the raw result + local state.

Hint
----
{ "severity": "ok" | "warn" | "fail" | "info",
  "message":  str,
  "fix":      str | None }   # one-line suggestion, or None
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from cs2servergui import config as _config


STEAM_MASTER_URL = (
    "https://api.steampowered.com/ISteamApps/GetServersAtAddress/v0001/?addr={ip}"
)
CS2_APPID = 730

# How long after start before we expect Valve's master to know about us.
# Empirically ~30-60s; we use 90 to give the operator a buffer before we
# start telling them something's wrong.
MASTER_REGISTER_GRACE_SECS = 90


class ReachabilityError(Exception):
    """Steam Web API unreachable or returned an unexpected payload."""


def check_steam_master(public_ip: str, *, timeout: float = 8.0) -> dict:
    """Query Valve's master server for CS2 servers registered at `public_ip`.

    Returns a normalised dict:
        {
          "target":   "<public_ip>",
          "ok":       bool,                 # Steam responded successfully
          "servers":  [<entry>, ...],        # CS2 servers (appid=730) at this IP
        }

    Each entry is the master server's view of one server:
        {"addr": "x.x.x.x:27015", "gameport": 27015, "secure": bool, ...}

    Raises ReachabilityError only on HTTP / parse failure.  An "empty
    list" response (no servers visible) is a SUCCESSFUL response —
    `ok=True`, `servers=[]` — and is what interpret() uses to detect
    the "invisible to Valve" case.
    """
    if not public_ip or not isinstance(public_ip, str):
        raise ReachabilityError("public_ip is required")
    url = STEAM_MASTER_URL.format(ip=public_ip)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"oblivion-server-tool/{_config.APP_VERSION}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise ReachabilityError(
            f"Steam Web API returned HTTP {exc.code}: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ReachabilityError(
            f"Steam Web API unreachable: {exc.reason}"
        ) from exc
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReachabilityError(
            f"Steam Web API returned non-JSON ({len(raw)} bytes): {exc}"
        ) from exc

    body = (doc or {}).get("response") or {}
    raw_servers = body.get("servers") or []
    # Filter to CS2 only (this endpoint sometimes returns CS:GO legacy
    # registrations or other Source games on a multi-server box).
    cs2_servers = [
        s for s in raw_servers
        if isinstance(s, dict) and s.get("appid") == CS2_APPID
    ]
    return {
        "target":  public_ip,
        "ok":      bool(body.get("success", True)),
        "servers": cs2_servers,
    }


# ── Hint engine ─────────────────────────────────────────────────────

def interpret(
    result: dict,
    *,
    gslt_set: bool,
    server_running: bool,
    server_uptime_secs: int,
    expected_port: int = 27015,
) -> list[dict]:
    """Map raw Steam-master result + local state to operator-facing hints.

    Order of checks matters — earlier (more fundamental) issues short-
    circuit later ones.  We only return the first applicable hint, so the
    operator sees the one thing they actually need to fix.
    """
    # 1. Server not running — no point checking anything else.
    if not server_running:
        return [{
            "severity": "info",
            "message":  "Server is offline — start it before checking reachability.",
            "fix":      None,
        }]

    # 2. GSLT missing — server CANNOT register with Valve regardless of
    #    port forward state.  This is the silent killer the pre-flight
    #    already warns about; surfacing it here turns it into an
    #    actionable fix for the reachability question.
    if not gslt_set:
        return [{
            "severity": "fail",
            "message":  "No GSLT token — Valve's auth backend silently rejects "
                        "external clients, and your server can't register with "
                        "the master server.",
            "fix":      "Generate a token at "
                        "https://steamcommunity.com/dev/managegameservers "
                        "(App ID 730) and paste it into Config → GSLT.",
        }]

    # 3. Newly-started — give Valve's master server time to discover us.
    if server_uptime_secs < MASTER_REGISTER_GRACE_SECS:
        wait = MASTER_REGISTER_GRACE_SECS - server_uptime_secs
        return [{
            "severity": "info",
            "message":  f"Server started recently ({server_uptime_secs}s ago). "
                        f"Valve's master server can take up to "
                        f"{MASTER_REGISTER_GRACE_SECS}s to register a new "
                        f"server.",
            "fix":      f"Wait ~{wait}s, then re-check.",
        }]

    # 4. Master server response.  Look for our exact port.
    servers   = result.get("servers") or []
    on_port   = [s for s in servers if s.get("gameport") == expected_port]
    target_ip = result.get("target", "")

    if on_port:
        # Found us at the expected port — players will see this server in
        # the browser, and direct-connects will reach it.
        secure = on_port[0].get("secure", False)
        return [{
            "severity": "ok",
            "message":  f"Valve sees your server at {target_ip}:{expected_port} — "
                        f"players can connect."
                        + ("" if secure else "  (VAC disabled.)"),
            "fix":      None,
        }]

    if servers:
        # Something at this IP is registered, but on a different port —
        # operator may be hosting multiple servers / forwarded the wrong one.
        other_ports = sorted({s.get("gameport") for s in servers if s.get("gameport")})
        return [{
            "severity": "warn",
            "message":  f"Valve sees CS2 servers at {target_ip} on port(s) "
                        f"{other_ports}, but NOT on {expected_port}.",
            "fix":      "Either you're hosting multiple servers and your "
                        f"forward targets a different one, or your CS2 "
                        f"server is listening on a non-standard port.  "
                        f"Check `port` in your CS2 launch args / config.",
        }]

    # 5. Truly invisible — server up, GSLT set, master server doesn't know
    #    about us.  Almost always router-side: forward broken, wrong LAN
    #    IP, or CGNAT.
    return [{
        "severity": "fail",
        "message":  f"Valve's master server cannot see your server at "
                    f"{target_ip}:{expected_port}.  External players will "
                    f"NOT be able to connect.",
        "fix":      "Most common: the port forward in your router targets "
                    "the wrong LAN IP (stale DHCP lease) or doesn't include "
                    "UDP.  Verify the forward points at THIS machine's "
                    "current LAN IP for BOTH TCP and UDP on port 27015, "
                    "then re-check.  If that's correct and it still fails, "
                    "you may be behind CGNAT — compare your router's WAN "
                    "IP to the Public IP shown in the status bar.",
    }]
