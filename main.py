"""
main.py — Oblivion Server Tool entry point.

Run:  python main.py
Build: see build.bat
"""
from __future__ import annotations

import threading

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
