# -*- mode: python ; coding: utf-8 -*-
#
# Linux headless onefile spec — produces dist/oblivion-server-tool.
#
# Mirrors the Windows OblivionServerTool.spec, with three Linux differences:
#   1. keyring backend is SecretService (+ chainer/fail), not Windows.
#   2. pywebview is NOT bundled — the GTK desktop window needs system
#      WebKitGTK + gobject-introspection typelibs, which PyInstaller can't
#      freeze portably.  The frozen binary runs headless (main.py catches the
#      `import webview` ImportError and falls back).  Desktop-window users run
#      from source (see README "Linux desktop window").
#   3. console=True — it's a server tool; stdout goes to the terminal / journal.
#
# Paths are resolved from SPECPATH so the spec runs regardless of CWD.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir, os.pardir))


def _root(rel: str) -> str:
    return os.path.join(ROOT, *rel.split("/"))


datas = [
    (_root("cs2servergui/plugins"),   "cs2servergui/plugins"),
    (_root("cs2servergui/registry"),  "cs2servergui/registry"),
    (_root("cs2servergui/templates"), "cs2servergui/templates"),
    (_root("cs2servergui/static"),    "cs2servergui/static"),
]
binaries = []
hiddenimports = [
    "werkzeug", "werkzeug.serving", "werkzeug.routing", "werkzeug.exceptions",
    "keyring", "keyring.backends",
    "keyring.backends.SecretService", "keyring.backends.chainer",
    "keyring.backends.fail",
    "cs2servergui.config", "cs2servergui.rcon", "cs2servergui.core",
    "cs2servergui.web", "cs2servergui._netutils", "cs2servergui.veto",
    "cs2servergui.discord_bot", "cs2servergui.platform",
    "cs2servergui.reachability", "cs2servergui.registry_client",
]

from PyInstaller.utils.hooks import collect_all
for pkg in ("flask", "jinja2", "segno", "discord"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h


a = Analysis(
    [_root("main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["webview"],   # headless binary — see header note (2)
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="oblivion-server-tool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # upx often absent on CI runners; skip for reproducible builds
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
