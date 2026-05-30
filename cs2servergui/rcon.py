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
        """Send one RCON command and return the response body.

        Handles Source RCON's multi-packet response trick: any single response
        body >4096 bytes gets split across multiple type-0 packets, and the
        only reliable way to know we've drained them all is to send a sentinel
        empty command immediately after the real one and concatenate every
        body that comes back with the real command's pkt_id, stopping when we
        see the sentinel's id.  Without this loop, `status` / `cvarlist` /
        `say_team`-spammed chatter and similar long outputs get silently
        truncated at the first 4 KB.
        """
        aid = self._next_id()
        cid = self._next_id()
        sid = self._next_id()   # sentinel id — marks end of multi-packet response
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

            # Real command + sentinel (empty body, type-2).  Server processes
            # them in order; once we see a response with `sid` we know every
            # fragment of the real command's response has been delivered.
            s.sendall(self._pack(cid, 2, command) + self._pack(sid, 2, ""))
            chunks: list[str] = []
            while True:
                rid, _rtype, body = self._recv(s)
                if rid == sid:
                    # Drain one trailing empty-response packet some Source
                    # builds emit after the sentinel; treat any error as "done".
                    try:
                        self._recv(s)
                    except Exception:
                        pass
                    break
                if rid == cid:
                    chunks.append(body)
        return "".join(chunks)

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

        CS2 takes 30-60 s to boot before RCON accepts connections, and a
        flapping network can drop the connection mid-handshake.  Retries on
        the full family of transient socket errors:
          - ConnectionRefusedError    (port not listening yet)
          - TimeoutError              (server overloaded / still initialising)
          - ConnectionResetError      (peer closed mid-conversation)
          - ConnectionAbortedError    (local stack reset)
          - socket.timeout            (alias of TimeoutError on Py3.10+)
          - OSError                   (everything else short of a programmer bug)
            *except* the ConnectionError from _recv("auth failed") which would
            never succeed on retry.
        Each failed attempt waits `delay` seconds before retrying.
        Raises the last exception if all attempts are exhausted.
        """
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(retries):
            try:
                return self.execute(command)
            except ConnectionError as exc:
                # Don't retry an auth failure — bad rcon_password won't fix itself.
                if "auth failed" in str(exc).lower():
                    raise
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(delay)
            except (TimeoutError, OSError) as exc:
                # OSError covers ConnectionResetError / ConnectionAbortedError /
                # WinError 10054 / EPIPE / network-blip transients.
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(delay)
        raise last_exc
