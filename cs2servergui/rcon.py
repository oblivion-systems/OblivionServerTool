"""
rcon.py — thread-safe Source RCON client.

Opens a fresh TCP connection per command so there are no persistent socket
lifetime issues in a multi-threaded environment.
"""
from __future__ import annotations

import socket
import struct
import threading
import time


class RCONClient:
    """Thread-safe Source RCON client. Opens a fresh TCP connection per command."""

    def __init__(self, host: str, port: int, password: str) -> None:
        self.host     = host
        self.port     = port
        self.password = password
        self._id      = 1
        self._id_lock = threading.Lock()

    def _next_id(self) -> int:
        with self._id_lock:
            val = self._id
            self._id += 1
        return val

    @staticmethod
    def _pack(pkt_id: int, pkt_type: int, body: str) -> bytes:
        data = body.encode("utf-8") + b"\x00\x00"
        return struct.pack("<iii", 8 + len(data), pkt_id, pkt_type) + data

    @staticmethod
    def _recv(sock: socket.socket) -> tuple[int, int, str]:
        """Read one RCON packet → (pkt_id, pkt_type, body)."""
        raw = bytearray()
        while len(raw) < 4:
            chunk = sock.recv(4 - len(raw))
            if not chunk:
                raise ConnectionError("RCON socket closed unexpectedly")
            raw += chunk
        size = struct.unpack("<i", raw)[0]
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("RCON socket closed unexpectedly")
            data += chunk
        pkt_id   = struct.unpack("<i", data[0:4])[0]
        pkt_type = struct.unpack("<i", data[4:8])[0]
        body     = data[8:-2].decode("utf-8", errors="replace")
        return pkt_id, pkt_type, body

    def execute(self, command: str) -> str:
        """Send one RCON command and return the response body."""
        aid = self._next_id()
        cid = self._next_id()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((self.host, self.port))

            # Auth
            s.sendall(self._pack(aid, 3, self.password))
            pkt_id, pkt_type, _ = self._recv(s)
            # CS:GO sends a junk type-0 packet first; CS2 sends type-2 directly.
            if pkt_type == 0:
                pkt_id, pkt_type, _ = self._recv(s)
            if pkt_id == -1:
                raise ConnectionError("RCON auth failed — wrong rcon_password?")

            # Command
            s.sendall(self._pack(cid, 2, command))
            _, _, body = self._recv(s)
        return body

    def execute_many(self, commands: list[str]) -> list[str]:
        """Execute multiple commands over a single authenticated RCON connection.

        Authenticates once, then sends each command in sequence and collects
        the response bodies.  Far cheaper than opening a new TCP connection
        per command (e.g. bot_add × N, mp_friendlyfire + mp_autokick).

        Returns a list of response bodies in the same order as *commands*.
        Raises the same exceptions as execute().
        """
        if not commands:
            return []
        aid = self._next_id()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((self.host, self.port))

            # Auth once
            s.sendall(self._pack(aid, 3, self.password))
            pkt_id, pkt_type, _ = self._recv(s)
            if pkt_type == 0:
                pkt_id, pkt_type, _ = self._recv(s)
            if pkt_id == -1:
                raise ConnectionError("RCON auth failed — wrong rcon_password?")

            # Commands — one packet each, responses collected in order
            results: list[str] = []
            for cmd in commands:
                cid = self._next_id()
                s.sendall(self._pack(cid, 2, cmd))
                _, _, body = self._recv(s)
                results.append(body)
        return results

    def execute_retry(self, command: str,
                      retries: int = 6, delay: float = 5.0) -> str:
        """execute() with retry on transient connection failures.

        CS2 takes 30-60 s to boot before RCON accepts connections.
        Retries on ConnectionRefusedError (port not listening yet) and
        TimeoutError (server overloaded / still initialising).
        Auth errors (ConnectionError with "auth failed") are NOT retried —
        they indicate a wrong password and would never succeed.
        Each failed attempt waits `delay` seconds before retrying.
        Raises the last exception if all attempts are exhausted.
        """
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(retries):
            try:
                return self.execute(command)
            except (ConnectionRefusedError, TimeoutError) as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(delay)
        raise last_exc
