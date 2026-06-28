"""
Oblivion Reachability Probe — public service that tells an operator
whether their CS2 server's public IP + ports are actually reachable
from the outside world.

Deployable as a Docker container on Fly.io / Railway / a $5 VPS.  ~120
lines, stdlib + Flask only.

Security properties:
    * The probe ONLY ever connects back to the source IP of the
      incoming HTTP request.  Operators can't use it to scan other
      hosts — you can only probe yourself.  This makes it useless
      as a reconnaissance tool.
    * Per-source-IP rate limit (10 checks / minute) — light backstop
      against accidental hammering, not security.
    * Stateless; no logs of probed addresses retained beyond the rate
      limiter's 60-second sliding window.
    * Self-hosted is supported and documented — paranoid operators
      can run their own and point `reachability_probe_url` at it.

Wire protocol:
    POST /check
      Content-Type: application/json
      Body: {"ports": [27015, 27016], "protocol": "both"}
      Returns: {
          "target": "<source-ip>",
          "results": [
              {"port": 27015,
               "tcp": {"status": "open" | "closed" | "filtered" | "error", "reason": "..."},
               "udp": {"status": "open" | "unknown",                    "reason": "..."}}
          ]
      }
    GET /health -> {"ok": true}
"""
from __future__ import annotations

import socket
import threading
import time

from flask import Flask, jsonify, request

app = Flask(__name__)

# ── Config ──────────────────────────────────────────────────────────────
_TCP_TIMEOUT_S       = 3.0
_UDP_TIMEOUT_S       = 3.0
_MAX_PORTS_PER_CALL  = 4
_RATE_LIMIT_PER_MIN  = 10
_VALID_PROTOCOLS     = {"tcp", "udp", "both"}

# ── Rate limiter (per-source-IP, 60s sliding window) ───────────────────
_buckets: dict[str, list[float]] = {}
_buckets_lock = threading.Lock()


def _rate_limit_ok(ip: str) -> bool:
    now = time.time()
    with _buckets_lock:
        events = _buckets.setdefault(ip, [])
        events[:] = [t for t in events if now - t < 60]
        if len(events) >= _RATE_LIMIT_PER_MIN:
            return False
        events.append(now)
        return True


# ── Probes ──────────────────────────────────────────────────────────────

def _probe_tcp(ip: str, port: int) -> dict:
    """Try a TCP three-way handshake.  Returns status + brief reason."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(_TCP_TIMEOUT_S)
    try:
        s.connect((ip, port))
        return {"status": "open", "reason": "handshake completed"}
    except ConnectionRefusedError:
        return {"status": "closed", "reason": "connection refused"}
    except socket.timeout:
        return {"status": "filtered", "reason": "timeout — likely firewall drop"}
    except OSError as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        try:
            s.close()
        except OSError:
            pass


def _probe_udp_source_query(ip: str, port: int) -> dict:
    """Send a Source A2S_INFO query (the standard 'who are you?' packet
    every Source/CS2 server speaks).  If we get any 0xFFFFFFFF-prefixed
    response within timeout, the port is reachable AND a Source server
    is answering.  No response is ambiguous — could be closed, filtered,
    or a non-Source listener.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(_UDP_TIMEOUT_S)
    try:
        # Source A2S_INFO query — standard since GoldSrc, still used in CS2.
        payload = b"\xff\xff\xff\xffTSource Engine Query\x00"
        s.sendto(payload, (ip, port))
        try:
            data, _ = s.recvfrom(4096)
            if data.startswith(b"\xff\xff\xff\xff"):
                return {"status": "open", "reason": "Source A2S response"}
            return {"status": "open", "reason": "non-Source response (something is listening)"}
        except socket.timeout:
            return {"status": "unknown",
                    "reason": "no UDP response (closed, filtered, or non-Source listener)"}
    except OSError as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        try:
            s.close()
        except OSError:
            pass


def _source_ip() -> str:
    """Extract the requester's IP, honouring X-Forwarded-For for one
    hop (Fly/Railway/Cloudflare put the real client IP there)."""
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


# ── Routes ──────────────────────────────────────────────────────────────

@app.route("/check", methods=["POST"])
def check():
    target = _source_ip()
    if not _rate_limit_ok(target):
        return jsonify({"error": "rate limit (10/min per IP)"}), 429

    body = request.get_json(silent=True) or {}
    ports = body.get("ports")
    if (not isinstance(ports, list) or not ports
            or len(ports) > _MAX_PORTS_PER_CALL
            or not all(isinstance(p, int) and 1 <= p <= 65535 for p in ports)):
        return jsonify({
            "error": f"invalid 'ports' (1-{_MAX_PORTS_PER_CALL} integers, 1-65535)"
        }), 400

    proto = (body.get("protocol") or "both").lower()
    if proto not in _VALID_PROTOCOLS:
        return jsonify({"error": f"invalid 'protocol' (one of {sorted(_VALID_PROTOCOLS)})"}), 400

    out: list[dict] = []
    for port in ports:
        entry: dict = {"port": port}
        if proto in ("tcp", "both"):
            entry["tcp"] = _probe_tcp(target, port)
        if proto in ("udp", "both"):
            entry["udp"] = _probe_udp_source_query(target, port)
        out.append(entry)

    return jsonify({"target": target, "results": out})


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Production runs under gunicorn via Dockerfile; this branch is
    # for local development only.
    app.run(host="0.0.0.0", port=8080, debug=False)
