r"""
main.py — Oblivion Server Tool entry point.

Run:   python main.py
Build: build.bat  →  dist\OblivionServerTool.exe
"""
from __future__ import annotations

import argparse
import os
import secrets
import signal
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


# Port/process helpers live in cs2servergui/_netutils.py — single source of
# truth for both this module (Flask port collisions) and core.py (CS2 port
# conflict detection in _preflight_checks / _post_launch_sanity_check).
from cs2servergui._netutils import port_in_use as _port_in_use
from cs2servergui._netutils import holder_of_port as _holder_of_port
from cs2servergui import platform as _plat

# Image names that could be a stale copy of THIS app.  Per-OS (v1.2):
# Windows = the frozen .exe / a python launcher; Linux = python[3] or the
# onefile binary name.
_OUR_PROCESS_NAMES = _plat.own_process_names()


def _kill_zombie_instance(port: int) -> bool:
    """Kill a prior Oblivion process holding our Flask port. Returns True on kill.

    pywebview / Edge WebView2 (Windows) occasionally leaves non-daemon threads
    alive so the Python process survives window close.  The zombie holds our
    port, making re-launches silently fail.  Only processes whose image matches
    our own are killed — anything else (e.g. CS_GO_Arx_Applet) is left alone and
    the caller falls back to a different port.

    v1.2: cross-platform.  Kill uses platform.kill_pid (taskkill on Windows,
    SIGKILL on Linux) instead of a hardcoded taskkill.
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
        _plat.kill_pid(pid)
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


def _run_headless(core, port: int, flask_thread: threading.Thread) -> None:
    """Hold the process open until SIGINT/SIGTERM, then save_config and exit.

    The headless counterpart to pywebview's blocking event loop.  Same
    shutdown contract as the desktop path: save_config runs once before
    os._exit so an in-flight config write isn't truncated.  This is the
    foundation for v1.1 Linux support — when the platform seam (Phase B)
    + Linux runtime (Phase C) land, this same code path runs under
    systemd / Docker / VPS without a desktop session.
    """
    print()
    print(f"  Oblivion Server Tool is running headless (web panel is the UI).")
    print(f"  Local:  http://localhost:{port}")
    lan = _config._lan_ip()
    if lan and lan != "127.0.0.1":
        print(f"  LAN:    http://{lan}:{port}")
    print(f"  PIN required — set in the SPA on first run, then sign in.")
    print(f"  Press Ctrl+C to stop.")
    print()

    stop = threading.Event()

    def _handle_signal(signum, _frame):
        core.log(f"Received signal {signum} — shutting down…")
        stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    try:
        stop.wait()
    except KeyboardInterrupt:
        # On Windows, Ctrl+C raises here BEFORE the signal handler fires;
        # on Linux/macOS the handler fires first and stop.wait() returns
        # normally.  Either way we fall through to save_config + exit.
        pass

    try:
        core.save_config()
    except Exception as exc:
        print(f"[shutdown] Final save_config failed: {exc}")
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="OblivionServerTool",
        description="CS2 dedicated server manager.  Default is desktop window; "
                    "--headless skips the window and uses the web panel only.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without the embedded desktop window. Web panel at "
             "http://<host>:<port> becomes the only UI. PIN auth required. "
             "Foundation for v1.1 Linux support — desktop users typically don't "
             "need this flag.",
    )
    args = parser.parse_args()

    # ── Bootstrap AppCore ─────────────────────────────────────────────────────
    core = AppCore()
    core.log(f"Oblivion Server Tool v{APP_VERSION}{' (headless)' if args.headless else ''}")

    # One-time token for pywebview's auto-auth URL — invalidated on first use.
    # Headless mode has no desktop window to consume it, so skip; /auth/auto
    # already rejects when startup_token is empty so no other code path breaks.
    if not args.headless:
        core.startup_token = secrets.token_hex(32)

    # Start crash monitor (moved from gui.py; now lives in AppCore)
    core.start_monitor()

    # Probe for an already-running server before the UI opens
    core.probe_existing_server()

    # ── Pick a Flask port (survive collisions) ───────────────────────────────
    # Try _select_flask_port → make_server bind in the main thread.  If a
    # foreign process grabs the port between check and bind (TOCTOU), the
    # make_server call raises OSError; we retry once with a fresh port
    # selection.  Binding here (not inside the thread) makes the failure
    # synchronous and recoverable, instead of a silent "did not start in 10s".
    flask_app = create_flask(core)
    from werkzeug.serving import make_server
    server = None
    port   = None
    for attempt in range(3):
        port = _select_flask_port(_config.FLASK_PORT)
        if port is None:
            sys.exit(1)
        try:
            server = make_server("0.0.0.0", port, flask_app, threaded=True)
            break
        except OSError as exc:
            print(f"[startup] Bind to port {port} lost the race: {exc} — retry {attempt + 1}/3")
            server = None
            time.sleep(0.5)
    if server is None:
        print(f"[!] Could not bind any port in the {_config.FLASK_PORT}+0..3 range. Exiting.")
        sys.exit(1)
    core.log(f"Remote web panel → http://localhost:{port}")

    flask_thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="flask",
    )
    flask_thread.start()

    if not _wait_for_flask(port, timeout=10.0):
        print(f"[!] Flask did not respond on port {port} within 10 s — exiting.")
        sys.exit(1)

    # Background tasks (same as before, minus the GUI-specific ones)
    core.check_update()       # CS2 server version check
    core.check_app_update()   # OblivionTool GitHub release check
    core.check_public_ip()    # async public IP fetch
    if core.auto_start:
        from cs2servergui.config import OFFICIAL_MAPS
        core.start_server(OFFICIAL_MAPS[0], core.current_mode)

    # ── Headless branch (v1.1) ────────────────────────────────────────────────
    # No desktop window — hold the process open, wait for Ctrl+C / SIGTERM,
    # save config on the way out.  Same Flask + AppCore are already running;
    # the only difference is what blocks the main thread.
    #
    # v1.2: also auto-fall-back to headless on Linux when $DISPLAY /
    # $WAYLAND_DISPLAY are both unset — typical of SSH-only / systemd boxes
    # where opening a window would silently fail.
    from cs2servergui import platform as _plat
    if not args.headless and not _plat.has_display():
        core.log("[startup] No desktop session detected ($DISPLAY/$WAYLAND_DISPLAY unset) "
                 "— falling back to headless mode.")
        args.headless = True
    if args.headless:
        _run_headless(core, port, flask_thread)
        return

    # ── Open pywebview window ─────────────────────────────────────────────────
    # The window loads the local Flask server so auth / API / SSE all share
    # the same origin.  The auto-auth URL creates a privileged local session
    # so the PIN keypad is bypassed for the desktop window.
    try:
        import webview  # type: ignore
    except ImportError:
        # v1.2: graceful fallback — keep Flask alive so the operator can
        # still reach the web panel, instead of asking them to relaunch.
        print(
            "[!] pywebview is not installed — falling back to web-panel-only.\n"
            f"    Open http://localhost:{port} in any browser, or install"
            " pywebview (and on Linux, python3-gi + gir1.2-webkit2-4.1)"
            " for the desktop window."
        )
        _run_headless(core, port, flask_thread)
        return

    auto_url = (
        f"http://127.0.0.1:{port}/auth/auto"
        f"?token={core.startup_token}"
    )

    # Resolve icon path: works both in source layout and PyInstaller --onefile.
    # sys._MEIPASS is the temp-extract root when frozen; fall back to the
    # directory that contains this script when running from source.  The
    # filename is per-OS — .ico for Edge WebView2 (Windows), .png for
    # GTK/WebKitGTK (Linux), which won't render a .ico.
    _ico_base = (getattr(sys, "_MEIPASS", "") if getattr(sys, "frozen", False)
                 else os.path.dirname(os.path.abspath(__file__)))
    _ico_path = os.path.join(_ico_base, _plat.window_icon_filename())

    window = webview.create_window(
        title      = f"Oblivion Server Tool  v{APP_VERSION}",
        url        = auto_url,
        width      = 1280,
        height     = 840,
        min_size   = (1000, 700),
        resizable  = True,
        confirm_close = False,
    )

    # Start the webview event loop (blocks until the window is closed).
    # GUI backend is per-OS: Edge WebView2 on Windows (ships with Win 11,
    # 1-click install on Win 10), GTK / WebKitGTK on Linux.  Override the
    # default Linux pick to "qt" by exporting OBLIVION_WEBVIEW_GUI=qt.
    webview.start(
        gui          = _plat.webview_gui(),
        debug        = False,
        http_server  = False,            # we already have Flask
        icon         = _ico_path if os.path.isfile(_ico_path) else None,
    )

    # Flush any in-flight config changes BEFORE os._exit — without this, a
    # toggle the user changed seconds before closing the window can be lost
    # because os._exit bypasses atexit handlers and threading shutdown.
    # Worse, if a background save was mid-write at exit, the file is left
    # truncated and _load_config silently treats it as empty {} on next launch,
    # resetting every setting and regenerating the RCON password.
    try:
        core.save_config()
    except Exception as exc:
        print(f"[shutdown] Final save_config failed: {exc}")

    # Force-exit so that Edge WebView2 child processes and any pywebview-internal
    # non-daemon threads are fully cleaned up.  Without this the Python process
    # can survive after the window closes, holding port 5000 and making the next
    # launch silently fail (the zombie Flask responds to pings so _wait_for_flask
    # returns True, but the new AppCore never gets to bind its own Flask instance).
    os._exit(0)


if __name__ == "__main__":
    main()
