# Oblivion Reachability Probe

Tiny public service that probes back to the requester's own IP/ports
so the Oblivion Server Tool can answer "can players actually reach my
server from outside?" without false negatives from NAT hairpinning.

**~120 lines of Python, Flask + stdlib only, no database.** Deployable
on Fly.io's free tier, Railway, a $5 VPS, or anything that runs Docker.

## Security model

- **Probes only the source IP of the HTTP request.** The probe service
  reads the requester's IP from `X-Forwarded-For` (single hop) or
  `REMOTE_ADDR` and uses that as the only target. Operators can't
  use it to scan other hosts — it's useless for reconnaissance.
- **Per-source-IP rate limit**: 10 checks per minute, 60-second
  sliding window, in-memory. Light backstop, not security.
- **Stateless**: no probe results retained; the rate-limiter forgets
  IPs after 60 seconds.

## Wire protocol

```
POST /check
Content-Type: application/json
Body:
  {
    "ports": [27015, 27016],   // 1-4 ints in [1, 65535]
    "protocol": "both"          // "tcp" | "udp" | "both" (default)
  }

200 OK:
  {
    "target": "197.95.177.243",
    "results": [
      {
        "port": 27015,
        "tcp": {"status": "open",    "reason": "handshake completed"},
        "udp": {"status": "open",    "reason": "Source A2S response"}
      },
      {
        "port": 27016,
        "tcp": {"status": "closed",  "reason": "connection refused"},
        "udp": {"status": "unknown", "reason": "no UDP response"}
      }
    ]
  }

429 Rate limited:
  {"error": "rate limit (10/min per IP)"}

400 Bad request:
  {"error": "invalid 'ports' (1-4 integers, 1-65535)"}
```

`GET /health` returns `{"ok": true}` for uptime checks.

## TCP status semantics

| status     | meaning                                                                |
|------------|------------------------------------------------------------------------|
| `open`     | three-way handshake completed — port is forwarded and a process listens|
| `closed`   | TCP RST received — port not forwarded, or no process listening         |
| `filtered` | timeout — almost always firewall drop (router or ISP)                  |
| `error`    | OS-level error (rare; details in `reason`)                             |

## UDP status semantics

UDP is connectionless, so true "is this port open?" is undecidable.
The probe uses a Source A2S_INFO query (`\xff\xff\xff\xff TSource Engine
Query\x00`) — every CS2 / Source dedicated server responds to it.

| status    | meaning                                                                |
|-----------|------------------------------------------------------------------------|
| `open`    | Got a UDP response — port is reachable AND a process answered         |
| `unknown` | No response within 3s — could be filtered, closed, or non-Source       |

## Deploying on Fly.io (recommended)

```bash
cd probe
fly auth login            # one-time
fly launch --no-deploy    # name: oblivion-probe, region: jnb (or yours)
fly deploy
fly status                # confirm machine is up
curl https://oblivion-probe.fly.dev/health
```

Then in Oblivion Server Tool's `oblivion_config.json`:

```json
{
  "reachability_probe_url": "https://oblivion-probe.fly.dev/check"
}
```

Free tier: shared-cpu-1x / 256MB / auto-stop when idle. Cold-start
is ~1 second; first request after idle takes ~2s instead of ~6s.

## Self-hosting (paranoid operators)

```bash
docker build -t oblivion-probe .
docker run -d -p 8080:8080 --restart unless-stopped oblivion-probe
```

Put it behind your reverse proxy of choice (Caddy / nginx / Traefik)
with TLS. Then set `reachability_probe_url` to your own domain.

## Local development

```bash
cd probe
pip install flask
python probe.py
curl -X POST http://localhost:8080/check \
     -H 'Content-Type: application/json' \
     -d '{"ports":[27015],"protocol":"tcp"}'
```

Probes itself (`127.0.0.1:27015`) which lets you smoke-test against
a local CS2 server.
