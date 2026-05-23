"""
main.py — Oblivion Server Tool entry point.

Run:  python main.py
Build: see build.bat
"""
from __future__ import annotations

import sys
import threading


def _enable_high_dpi() -> None:
    """Make the process per-monitor DPI aware (Windows).

    Without this, Windows uses bitmap-stretching virtualisation on every
    DPI-context switch — including alt-tabbing from a fullscreen game at a
    low resolution back to a high-DPI desktop.  That virtualisation is what
    produces the "materialise" delay (the window re-renders pixel-by-pixel
    instead of letting Tk redraw widgets natively).

    PER_MONITOR_AWARE_V2 (Win10 1703+) is the modern preferred mode.  We
    fall through to PER_MONITOR_AWARE (Win8.1+) and then the legacy
    SetProcessDPIAware (Vista+) if the newer APIs aren't available.

    MUST run BEFORE any tkinter / customtkinter import — Windows only
    honours the first DPI-awareness call per process.  customtkinter
    sets DPI awareness itself on first widget creation, so we must beat it.
    """
    if sys.platform != "win32":
        return
    import ctypes
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)
        )
        return
    except (AttributeError, OSError):
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_high_dpi()

from cs2servergui.config import FLASK_PORT, ADMIN_PIN
from cs2servergui.core   import AppCore
from cs2servergui.web    import create_flask
from cs2servergui.gui    import CS2GUI


def main() -> None:
    core = AppCore()
    core.log("CS2 Panel initialised")
    core.log(f"Remote admin → http://localhost:{FLASK_PORT}  (PIN: {ADMIN_PIN})")

    # Flask runs in a background daemon thread — dies when the GUI exits
    threading.Thread(
        target=lambda: create_flask(core).run(
            host="0.0.0.0", port=FLASK_PORT,
            use_reloader=False, threaded=True,
        ),
        daemon=True,
    ).start()

    gui = CS2GUI(core)
    gui._refresh_wk()        # initial workshop scan (callbacks already registered)
    core.check_update()      # auto-check CS2 server version after GUI is built
    core.check_app_update()  # auto-check OblivionTool version (GitHub releases)
    core.check_public_ip()   # async — updates status bar when result arrives
    if core.auto_start:
        gui.root.after(500, gui._start)
    gui.run()


if __name__ == "__main__":
    main()
