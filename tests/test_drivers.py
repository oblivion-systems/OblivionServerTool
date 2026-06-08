"""
Driver layer tests (v0.13.0, task #86).

Verifies the GameDriver base class contract + the CS2Driver
concrete implementation.  Kept thin on purpose — the driver layer
is mostly identity + a couple of pure helpers right now, so the
tests are mostly "did the seam land?" checks.

Two ways to run (same dual mode as the other test files):
    python tests/test_drivers.py
    pytest tests/test_drivers.py
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('APPDATA', tempfile.mkdtemp(prefix='oblivion_drivers_'))

from cs2servergui.drivers import GameDriver, CS2Driver
from cs2servergui import config as _cfg

results = []
def t(name, fn):
    try:
        ok, detail = fn()
        results.append((ok, name, detail))
    except Exception as e:
        results.append((False, name, f'EXC: {type(e).__name__}: {e}'))


def t_cs2driver_identity_matches_legacy_hardcoded_values():
    """CS2Driver's identity props match what the rest of the codebase
    has been hardcoding.  Regression-guard: a future identity change
    here is a deliberate decision, not a typo."""
    d = CS2Driver()
    return (d.game_name == "Counter-Strike 2"
            and d.short_name == "cs2"
            and d.default_port == 27015
            and d.process_image_name == "cs2.exe"
            and d.process_args_marker == "-dedicated"
            and d.console_log_filename == "console.log"), \
           f'd.game_name={d.game_name!r} d.short_name={d.short_name!r}'
t('cs2driver: identity props match legacy hardcoded values',
  t_cs2driver_identity_matches_legacy_hardcoded_values)


def t_cs2driver_modes_match_config_mode_settings():
    """modes() must return the same keys as config.MODE_SETTINGS so
    the SPA mode picker, the driver, and the deploy table stay in
    sync.  Pre-v0.13.0 each layer had its own hardcoded list — drift
    risk on every mode addition."""
    d = CS2Driver()
    return (sorted(d.modes()) == sorted(_cfg.MODE_SETTINGS.keys())), \
           f'driver={sorted(d.modes())} config={sorted(_cfg.MODE_SETTINGS.keys())}'
t('cs2driver: modes() matches config.MODE_SETTINGS keys',
  t_cs2driver_modes_match_config_mode_settings)


def t_cs2driver_default_map_returns_first_of_mode_allow_list():
    """default_map('Competitive') is the first entry of MODE_MAPS['Competitive']."""
    d = CS2Driver()
    expected_first = _cfg.MODE_MAPS['Competitive'][0]
    return (d.default_map('Competitive') == expected_first), \
           f'expected={expected_first} got={d.default_map("Competitive")}'
t('cs2driver: default_map() returns first of mode allow-list',
  t_cs2driver_default_map_returns_first_of_mode_allow_list)


def t_cs2driver_default_map_fallback_for_unknown_mode():
    """An unknown mode (workshop-required, or typo) falls back to
    de_dust2 — the always-installed safe default."""
    d = CS2Driver()
    return (d.default_map('NotARealMode') == 'de_dust2'), \
           f'got={d.default_map("NotARealMode")}'
t('cs2driver: default_map() falls back to de_dust2 for unknown mode',
  t_cs2driver_default_map_fallback_for_unknown_mode)


def t_cs2driver_status_line_offline():
    """Offline servers report 'Counter-Strike 2 · offline' regardless
    of any stale map/mode/player_count fields on core."""
    d = CS2Driver()
    class FakeCore:
        running = False
        current_map = 'de_vertigo'  # stale field — should be ignored
        current_mode = '5v5'
        player_count = 10
    return (d.status_line(FakeCore()) == 'Counter-Strike 2 · offline'), \
           f'got={d.status_line(FakeCore())!r}'
t('cs2driver: status_line() reports offline cleanly',
  t_cs2driver_status_line_offline)


def t_cs2driver_status_line_running_with_mr_hint():
    """Running competitive modes get the MR12 suffix; casual modes don't."""
    d = CS2Driver()
    class FakeCoreCompetitive:
        running = True
        current_map = 'de_vertigo'
        current_mode = '5v5'
        player_count = 10
    class FakeCoreCasual:
        running = True
        current_map = 'de_dust2'
        current_mode = 'Arms Race'
        player_count = 16
    s_comp = d.status_line(FakeCoreCompetitive())
    s_cas  = d.status_line(FakeCoreCasual())
    return ('(MR12)' in s_comp and '(MR12)' not in s_cas), \
           f'competitive={s_comp!r} casual={s_cas!r}'
t('cs2driver: status_line() adds (MR12) hint for competitive modes only',
  t_cs2driver_status_line_running_with_mr_hint)


def t_cs2driver_describe_includes_cs2_specific_extras():
    """describe() over GameDriver baseline adds plugin_layer +
    match_layer + mode_count.  Useful for the diagnostic snapshot."""
    d = CS2Driver()
    info = d.describe()
    return (info.get('plugin_layer') == 'MetaMod + CounterStrikeSharp'
            and info.get('match_layer') == 'MatchZy'
            and isinstance(info.get('mode_count'), int)
            and info['mode_count'] >= 10), \
           f'info={info}'
t('cs2driver: describe() includes CS2-specific extras',
  t_cs2driver_describe_includes_cs2_specific_extras)


def t_appcore_has_driver_attribute():
    """AppCore must instantiate with a `driver` attribute pointing at
    a GameDriver subclass.  This is the seam other code reaches
    through; if it's missing we silently fall back to literals."""
    from cs2servergui.core import AppCore
    ac = AppCore()
    return (hasattr(ac, 'driver') and isinstance(ac.driver, GameDriver)
            and isinstance(ac.driver, CS2Driver)), \
           f'has_driver={hasattr(ac, "driver")} type={type(getattr(ac, "driver", None)).__name__}'
t('appcore: instantiates with .driver attribute (CS2Driver)',
  t_appcore_has_driver_attribute)


def t_cs2driver_install_root_returns_parent_of_addons_dir():
    """v0.13.1 — first method migration.  install_root() returns
    csgo/, which is the parent of CS2_ADDONS_DIR (=csgo/addons)."""
    import os as _os
    d = CS2Driver()
    expected = _os.path.dirname(_cfg.CS2_ADDONS_DIR)
    # Driver doesn't actually need core for this method (lazy import
    # of config), so a stub is fine.
    class FakeCore: pass
    got = d.install_root(FakeCore())
    return (got == expected), f'expected={expected!r} got={got!r}'
t('cs2driver: install_root() returns parent of CS2_ADDONS_DIR',
  t_cs2driver_install_root_returns_parent_of_addons_dir)


def t_appcore_csgo_dir_delegates_to_driver_install_root():
    """v0.13.1 — AppCore._csgo_dir() is now a thin shim that delegates
    to driver.install_root().  Same return value as the direct call —
    catches drift if someone re-implements _csgo_dir.
    """
    from cs2servergui.core import AppCore
    ac = AppCore()
    return (ac._csgo_dir() == ac.driver.install_root(ac)), \
           f'_csgo_dir={ac._csgo_dir()!r} install_root={ac.driver.install_root(ac)!r}'
t('appcore: _csgo_dir() delegates to driver.install_root()',
  t_appcore_csgo_dir_delegates_to_driver_install_root)


def t_base_gamedriver_is_abstract():
    """GameDriver shouldn't be instantiable directly — subclasses
    must declare modes() + default_map().  Catches the case where
    someone adds a new driver but forgets to implement an abstract
    method (Python raises TypeError on instantiation)."""
    try:
        GameDriver()  # type: ignore[abstract]
        return False, 'GameDriver was instantiable — abstract enforcement missing'
    except TypeError as exc:
        return ('abstract' in str(exc).lower()), f'expected abstract TypeError, got: {exc}'
t('base: GameDriver is abstract (cannot instantiate directly)',
  t_base_gamedriver_is_abstract)


# ─── Auto-generated pytest cases ──────────────────────────────────────────
def _slug(name):
    out = ''.join(c if c.isalnum() else '_' for c in name).strip('_').lower()
    while '__' in out: out = out.replace('__', '_')
    return 'test_' + out

def _make_pytest_case(_ok, _detail):
    def _case():
        assert _ok, _detail
    return _case

for _ok, _name, _detail in results:
    _slug_name = _slug(_name)
    _i = 1
    while _slug_name in globals():
        _i += 1
        _slug_name = f'{_slug(_name)}_{_i}'
    globals()[_slug_name] = _make_pytest_case(_ok, _detail)


if __name__ == '__main__':
    print()
    print('=' * 70)
    passes = sum(1 for ok, _, _ in results if ok)
    fails  = sum(1 for ok, _, _ in results if not ok)
    for ok, name, detail in results:
        mark = '[+]' if ok else '[X]'
        print(f'{mark} {name}')
        if not ok:
            print(f'    {detail}')
    print('=' * 70)
    print(f'  {passes} passed, {fails} failed')
    sys.exit(0 if fails == 0 else 1)
