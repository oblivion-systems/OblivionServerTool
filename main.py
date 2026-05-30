"""
main.py — Oblivion Server Tool entry point.

Run:   python main.py
Build: build.bat  →  dist\OblivionServerTool.exe
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request


def _enable_high_dpi() -> None:
    """Make the process per-monitor DPI aware before any UI is created."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_OUR_PROCESS_NAMES = {"oblivionservertool.exe", "python.exe", "pythonw.exe"}


def _port_in_use(port: int) -> bool:
    """Return True if something is already listening on localhost:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _holder_of_port(port: int) -> tuple[int, str] | None:
    """Return (pid, image_name_lower) of the process LISTENING on the port, or None.

    Looks at both 127.0.0.1:<port> and 0.0.0.0:<port> matches.  Used to decide
    whether a port collision is our own zombie (safe to kill) or someone else's
    app (must fall back to a different port instead of murdering it).
    """
    try:
        net = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5,
        )
        for line in net.stdout.splitlines():
            cols = line.split()
            # Proto  LocalAddress  ForeignAddress  State  PID
            if len(cols) >= 5 and f":{port}" in cols[1] and cols[3] == "LISTENING":
                pid = cols[4]
                if not (pid.isdigit() and int(pid) > 0):
                    continue
                # Resolve PID -> image name
                tl = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                first = tl.stdout.splitlines()[0] if tl.stdout.strip() else ""
                if first.startswith('"'):
                    name = first.split('","', 1)[0].strip('"').lower()
                    return int(pid), name
    except Exception as exc:
        print(f"[startup] _holder_of_port({port}) failed: {exc}")
    return None


def _kill_zombie_instance(port: int) -> bool:
    """Kill a prior Oblivion process holding our Flask port. Returns True on kill.

    pywebview's Edge WebView2 runtime occasionally leaves non-daemon threads alive
    so the Python process survives window close.  The zombie holds our port,
    making re-launches silently fail.  Only processes whose image matches our own
    (OblivionServerTool.exe / python.exe) are killed — anything else (e.g.
    CS_GO_Arx_Applet) is left alone and the caller falls back to a different port.
    """
    holder = _holder_of_port(port)
    if not holder:
        return False
    pid, name = holder
    if name not in _OUR_PROCESS_NAMES:
        print(f"[startup] Port {port} held by '{name}' (PID {pid}) — not ours, leaving it alone")
        return False
    print(f"[startup] Port {port} held by our own '{name}' (PID {pid}) — killing zombie…")
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=5)
        for _ in range(15):
            time.sleep(0.2)
            if not _port_in_use(port):
                print(f"[startup] Killed PID {pid} — port {port} free")
                return True
    except Exception as exc:
        print(f"[startup] Could not kill zombie: {exc}")
    return False


def _pick_free_port(start: int, count: int = 4) -> int | None:
    """Try `start`, `start+1`, … `start+count-1`. Return the first that's free.

    Survives port collisions with foreign apps (the CS_GO_Arx_Applet case).
    """
    for p in range(start, start + count):
        if not _port_in_use(p):
            return p
    return None


def _wait_for_flask(port: int, timeout: float = 10.0) -> bool:
    """Poll localhost until Flask is accepting connections or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/ping", timeout=0.5
            )
            return True
        except Exception:
            time.sleep(0.1)
    return False


_enable_high_dpi()

from cs2servergui import config as _config
from cs2servergui.config import APP_VERSION
from cs2servergui.core   import AppCore
from cs2servergui.web    import create_flask


def _select_flask_port(configured: int) -> int | None:
    """Pick a port for Flask: configured if free (or holding our zombie),
    otherwise the next free port in [configured+1..configured+3].

    Returns None if nothing in that range is free.  Updates _config.FLASK_PORT
    so the rest of the app (status bar, tunnel hints, etc.) sees the chosen port.
    """
    # If our own zombie is squatting the configured port, kill it and take it.
    _kill_zombie_instance(configured)
    if not _port_in_use(configured):
        _config.FLASK_PORT = configured
        return configured
    # Held by a foreign app — log who, fall back to a nearby port.
    holder = _holder_of_port(configured)
    if holder:
        pid, name = holder
        print(f"[startup] Port {configured} held by '{name}' (PID {pid}) — falling back…")
    chosen = _pick_free_port(configured + 1, count=3)
    if chosen is None:
        print(f"[!] No free port in {configured}–{configured + 3}. "
              f"Close whatever's on {configured} or change flask_port in oblivion_config.json.")
        return None
    print(f"[startup] Flask will bind to fallback port {chosen} instead of {configured}")
    _config.FLASK_PORT = chosen
    return chosen


def main() -> None:
    # ── Bootstrap AppCore ─────────────────────────────────────────────────────
    core = AppCore()
    core.log(f"Oblivion Server Tool v{APP_VERSION}")

    # One-time token for pywebview's auto-auth URL — invalidated on first use
    core.startup_token = secrets.token_hex(32)

    # Start crash monitor (moved from gui.py; now lives in AppCore)
    core.start_monitor()

    # Probe for an already-running server before the UI opens
    core.probe_existing_server()

    # ── Pick a Flask port (survive collisions) ───────────────────────────────
    port = _select_flask_port(_config.FLASK_PORT)
    if port is None:
        sys.exit(1)
    core.log(f"Remote web panel → http://localhost:{port}")

    # ── Start Flask ───────────────────────────────────────────────────────────
    flask_app = create_flask(core)

    flask_thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0",
            port=port,
            use_reloader=False,
            threaded=True,
        ),
        daemon=True,
        name="flask",
    )
    flask_thread.start()

    if not _wait_for_flask(port, timeout=10.0):
        print(f"[!] Flask did not start on port {port} within 10 s — exiting.")
        sys.exit(1)

    # Background tasks (same as before, minus the GUI-specific ones)
    core.check_update()       # CS2 server version check
    core.check_app_update()   # OblivionTool GitHub release check
    core.check_public_ip()    # async public IP fetch
    if core.auto_start:
        from cs2servergui.config import OFFICIAL_MAPS
        core.start_server(OFFICIAL_MAPS[0], core.current_mode)

    # ── Open pywebview window ─────────────────────────────────────────────────
    # The window loads the local Flask server so auth / API / SSE all share
    # the same origin.  The auto-auth URL creates a privileged local session
    # so the PIN keypad is bypassed for the desktop window.
    try:
        import webview  # type: ignore
    except ImportError:
        print(
            "[!] pywebview is not installed.\n"
            "    Run:  pip install pywebview\n"
            "    Or open the web panel manually at "
            f"http://localhost:{port}"
        )
        # Fall back to keeping Flask alive so the remote web panel still works
        try:
            flask_thread.join()
        except KeyboardInterrupt:
            pass
        return

    auto_url = (
        f"http://127.0.0.1:{port}/auth/auto"
        f"?token={core.startup_token}"
    )

    # Resolve icon path: works both in source layout and PyInstaller --onefile.
    # sys._MEIPASS is the temp-extract root when frozen; fall back to the
    # directory that contains this script when running from source.
    if getattr(sys, "frozen", False):
        _ico_path = os.path.join(getattr(sys, "_MEIPASS", ""), "emblem.ico")
    else:
        _ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emblem.ico")

    window = webview.create_window(
        title      = f"Oblivion Server Tool  v{APP_VERSION}",
        url        = auto_url,
        width      = 1280,
        height     = 840,
        min_size   = (1000, 700),
        resizable  = True,
        confirm_close = False,
    )

    # Start the webview event loop (blocks until the window is closed)
    webview.start(
        gui          = "edgechromium",   # Edge WebView2 — ships with Windows 11,
                                         # 1-click install on Win 10
        debug        = False,
        http_server  = False,            # we already have Flask
        icon         = _ico_path if os.path.isfile(_ico_path) else None,
    )

    # Force-exit so that Edge WebView2 child processes and any pywebview-internal
    # non-daemon threads are fully cleaned up.  Without this the Python process
    # can survive after the window closes, holding port 5000 and making the next
    # launch silently fail (the zombie Flask responds to pings so _wait_for_flask
    # returns True, but the new AppCore never gets to bind its own Flask instance).
    os._exit(0)


if __name__ == "__main__":
    main()
