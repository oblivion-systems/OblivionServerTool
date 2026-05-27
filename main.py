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


def _port_in_use(port: int) -> bool:
    """Return True if something is already listening on localhost:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_zombie_instance(port: int) -> None:
    """Kill any previous app instance that is still holding our Flask port.

    When pywebview's Edge WebView2 runtime leaves non-daemon threads alive,
    the Python process does not exit on window close.  The zombie holds
    port 5000, making re-launches silently fail (Flask can't bind, the new
    AppCore is never started, the window loads the zombie's stale state).

    This runs before Flask starts.  If the port is already in use we find
    the owning PID via netstat and taskkill it, then wait for the port to
    be released.
    """
    if not _port_in_use(port):
        return
    print(f"[startup] Port {port} already in use — killing zombie instance…")
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            cols = line.split()
            # Format: Proto  LocalAddress  ForeignAddress  State  PID
            if len(cols) >= 5 and f":{port}" in cols[1] and cols[3] == "LISTENING":
                pid = cols[4]
                if pid.isdigit() and int(pid) > 0:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, timeout=5,
                    )
                    print(f"[startup] Killed PID {pid}")
                    # Wait up to 3 s for the port to be released
                    for _ in range(15):
                        time.sleep(0.2)
                        if not _port_in_use(port):
                            break
                    break
    except Exception as exc:
        print(f"[startup] Could not kill zombie: {exc}")


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

from cs2servergui.config import FLASK_PORT, APP_VERSION
from cs2servergui.core   import AppCore
from cs2servergui.web    import create_flask


def main() -> None:
    # ── Bootstrap AppCore ─────────────────────────────────────────────────────
    core = AppCore()
    core.log(f"Oblivion Server Tool v{APP_VERSION}")
    core.log(f"Remote web panel → http://localhost:{FLASK_PORT}")

    # One-time token for pywebview's auto-auth URL — invalidated on first use
    core.startup_token = secrets.token_hex(32)

    # Start crash monitor (moved from gui.py; now lives in AppCore)
    core.start_monitor()

    # Probe for an already-running server before the UI opens
    core.probe_existing_server()

    # ── Kill any zombie instance before binding the port ─────────────────────
    # If the previous session's Python/Edge process didn't exit cleanly it may
    # still hold our Flask port, causing the new Flask thread to silently fail.
    _kill_zombie_instance(FLASK_PORT)

    # ── Start Flask ───────────────────────────────────────────────────────────
    flask_app = create_flask(core)

    flask_thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0",
            port=FLASK_PORT,
            use_reloader=False,
            threaded=True,
        ),
        daemon=True,
        name="flask",
    )
    flask_thread.start()

    if not _wait_for_flask(FLASK_PORT, timeout=10.0):
        print(f"[!] Flask did not start on port {FLASK_PORT} within 10 s — exiting.")
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
            f"http://localhost:{FLASK_PORT}"
        )
        # Fall back to keeping Flask alive so the remote web panel still works
        try:
            flask_thread.join()
        except KeyboardInterrupt:
            pass
        return

    auto_url = (
        f"http://127.0.0.1:{FLASK_PORT}/auth/auto"
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
