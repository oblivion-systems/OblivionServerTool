"""
smoke.py — fast, server-free sanity checks for the Oblivion Server Tool.

Run:  python tests/smoke.py     (exit 0 = all pass, 1 = failures)

Catches the kind of regression that only shows up at deploy/launch time:
broken imports, config round-trip, the Flask app booting + enforcing auth, and
**plugin-table integrity** (every mode's plugins exist across the copy / verify /
cleanup / kind tables and actually ship in the bundle).

IMPORTANT: config is redirected to a temp file before AppCore() is created, so
this never touches the real oblivion_config.json.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_failures: list[tuple[str, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"  PASS  {name}")
    except Exception as exc:  # noqa: BLE001 - smoke test wants every failure
        _failures.append((name, str(exc)))
        print(f"  FAIL  {name}: {exc}")


# ── 1. Imports (no webview / no main.py) ─────────────────────────────────────
import cs2servergui.config as config   # noqa: E402
import cs2servergui.rcon as rcon       # noqa: E402,F401
import cs2servergui.core as core       # noqa: E402
import cs2servergui.web as web         # noqa: E402
print("imports OK (config, rcon, core, web)")

# ── Redirect config to a throwaway file BEFORE any AppCore() ──────────────────
_tmpdir = tempfile.mkdtemp(prefix="oblivion_smoke_")
_tmpcfg = os.path.join(_tmpdir, "oblivion_config.json")
core._CONFIG_FILE = _tmpcfg
config._CONFIG_FILE = _tmpcfg


# ── 2. Plugin-table integrity ────────────────────────────────────────────────
def t_integrity() -> None:
    base = core._PLUGINS_BASE
    problems: list[str] = []
    for mode, plugins in core._MODE_PLUGIN_NAMES.items():
        for p in plugins:
            if p not in core._PLUGIN_KIND:
                problems.append(f"{mode} -> {p}: not in _PLUGIN_KIND")
            if p not in core._PLUGIN_COPY_RULES:
                problems.append(f"{mode} -> {p}: no _PLUGIN_COPY_RULES entry")
            src = os.path.join(base, p)
            if not os.path.isdir(src):
                problems.append(f"{mode} -> {p}: bundle source dir missing ({src})")
                continue
            for vf in core._PLUGIN_VERIFY_FILES.get(p, []):
                if not os.path.exists(os.path.join(base, p, vf)):
                    problems.append(f"{p}: verify marker not in bundle: {vf}")
            # Every copy rule's source sub-path must exist in the bundle.
            for rule in core._PLUGIN_COPY_RULES.get(p, []):
                sub = rule[0]
                if not os.path.exists(os.path.join(src, sub)):
                    problems.append(f"{p}: copy-rule source missing: {sub or '(root)'}")
    if problems:
        raise AssertionError("\n    " + "\n    ".join(problems))


check("plugin-table integrity (modes -> plugins -> bundle)", t_integrity)


# ── 3. Config round-trip (isolated) ──────────────────────────────────────────
def t_config() -> None:
    c = core.AppCore()
    c.hostname = "SMOKE_TEST_HOST"
    c.bots_enabled = True
    c.tickrate_128 = True
    c.save_config()
    assert os.path.exists(_tmpcfg), "save_config did not write the temp config"
    d = json.load(open(_tmpcfg, encoding="utf-8"))
    assert d["hostname"] == "SMOKE_TEST_HOST", "hostname not persisted"
    assert d["bots_enabled"] is True, "bots_enabled not persisted"
    assert d["tickrate_128"] is True, "tickrate_128 not persisted"


check("config round-trip (isolated temp file)", t_config)


# ── 4. Flask boots + auth is enforced ────────────────────────────────────────
def t_flask() -> None:
    c = core.AppCore()
    app = web.create_flask(c)
    client = app.test_client()
    root = client.get("/")
    assert root.status_code in (200, 302), f"/ returned {root.status_code}"
    state = client.get("/api/state")
    assert state.status_code != 200, (
        f"/api/state must require auth but returned {state.status_code}")


check("Flask boot + /api/state auth gate", t_flask)


# ── Cleanup + report ─────────────────────────────────────────────────────────
shutil.rmtree(_tmpdir, ignore_errors=True)

print()
if _failures:
    print(f"SMOKE: {len(_failures)} FAILURE(S)")
    sys.exit(1)
print("SMOKE: ALL PASS")
