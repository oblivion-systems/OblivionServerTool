"""
_netutils.py — small Windows port/process helpers shared by main.py + core.py.

Pulled out so the netstat-based listener enumeration lives in exactly one
place: previously `_holder_of_port` had two near-identical copies (Flask
port-collision survivor at module level in main.py, and CS2 port-conflict
detection as an AppCore method in core.py).  Two copies meant two places to
fix bugs and two places to keep behaviour in sync; one canonical
implementation is the lower-maintenance shape.

All functions here are pure-stdlib, Windows-targeted (netstat + tasklist),
and never raise — they swallow subprocess errors and log via the optional
`log` callback.  AppCore passes `self.log`; main.py passes plain `print`.
"""
from __future__ import annotations

import socket
import subprocess
from typing import Callable

# Default logger when none is provided — used by main.py's startup path
# before AppCore.log exists.
def _default_log(msg: str) -> None:
    print(msg)


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """Return True if something is already listening on host:port.

    A successful TCP connect proves a listener exists; failure (refused or
    timeout) means the port is free for binding from this host's perspective.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def listeners_on_port(
    port: int,
    log: Callable[[str], None] = _default_log,
) -> list[tuple[str, int, str]]:
    """Return every (bound_address, pid, image_name_lower) listening on `port`.

    Walks `netstat -ano` for lines in LISTENING state where the LocalAddress
    ends with `:<port>`, then resolves each PID to its image name via
    `tasklist /FI`.  Multiple entries are returned when the same port is
    bound to multiple addresses (e.g. IPv4 + IPv6, or a server that explicitly
    binds 0.0.0.0 AND ::).

    Never raises — logs the failure and returns whatever was collected.
    """
    listeners: list[tuple[str, int, str]] = []
    try:
        net = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5,
        )
        for line in net.stdout.splitlines():
            cols = line.split()
            # Format: Proto  LocalAddress  ForeignAddress  State  PID
            if len(cols) < 5 or cols[3] != "LISTENING":
                continue
            addr = cols[1]
            # Strict suffix match so we don't pick up :270150 / :270159 etc.
            if not addr.endswith(f":{port}"):
                continue
            pid_s = cols[4]
            if not (pid_s.isdigit() and int(pid_s) > 0):
                continue
            tl = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid_s}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            first = tl.stdout.splitlines()[0] if tl.stdout.strip() else ""
            name = first.split('","', 1)[0].strip('"').lower() \
                if first.startswith('"') else "?"
            listeners.append((addr, int(pid_s), name))
    except Exception as exc:
        log(f"[netutils] listeners_on_port({port}) failed: {exc}")
    return listeners


def holder_of_port(
    port: int,
    log: Callable[[str], None] = _default_log,
) -> tuple[int, str] | None:
    """Return (pid, image_name_lower) of the FIRST process LISTENING on `port`,
    or None if nothing is listening.

    Thin wrapper over `listeners_on_port` for callers that only care about
    "who's holding this port" — Flask port-collision survivor in main.py,
    pre-launch port check in AppCore._preflight_checks.  For full diagnostic
    output (e.g. dump every IPv4+IPv6 bind), use `listeners_on_port` directly.
    """
    listeners = listeners_on_port(port, log=log)
    if not listeners:
        return None
    _addr, pid, name = listeners[0]
    return pid, name
