"""
_netutils.py — port/process helpers shared by main.py + core.py.

Pulled out so the listener enumeration lives in exactly one place.
OS-specific implementations live in platform.py; this module is the
thin, stable API that callers import.
"""
from __future__ import annotations

import socket
from typing import Callable

from cs2servergui import platform as _plat

# Default logger when none is provided — used by main.py's startup path
# before AppCore.log exists.
def _default_log(msg: str) -> None:
    print(msg)


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """Return True if something is already listening on host:port.

    A successful TCP connect proves a listener exists; failure (refused or
    timeout) means the port is free for binding from this host's perspective.
    Pure socket — no OS-specific calls, works on Windows and Linux.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def listeners_on_port(
    port: int,
    log: Callable[[str], None] = _default_log,
) -> list[tuple[str, int, str]]:
    """(bound_address, pid, image_name_lower) for every process listening on
    `port`.  Delegates to platform.listeners_on_port() for OS-specific impl.
    """
    return _plat.listeners_on_port(port, log=log)


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
