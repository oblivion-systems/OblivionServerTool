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
    import sys
    from cs2servergui.platform import server_process_name
    d = CS2Driver()
    expected_image = server_process_name()   # "cs2.exe" on Windows, "cs2" on Linux
    return (d.game_name == "Counter-Strike 2"
            and d.short_name == "cs2"
            and d.default_port == 27015
            and d.process_image_name == expected_image
            and d.process_args_marker == "-dedicated"
            and d.console_log_filename == "console.log"), \
           f'd.process_image_name={d.process_image_name!r} expected={expected_image!r}'
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


# ─── platform.py Phase C tests ────────────────────────────────────────────

def t_platform_server_binary_rel_path_matches_cs2_path():
    """server_binary_rel_path() must match the relative portion of
    config.CS2_PATH when joined with server_dir — ensures config.py and
    platform.py agree on where the binary lives."""
    import os as _os, sys as _sys
    from cs2servergui.platform import server_binary_rel_path
    rel = server_binary_rel_path()
    if _sys.platform == "win32":
        assert "win64" in rel and rel.endswith("cs2.exe"), rel
    else:
        assert "linuxsteamrt64" in rel and rel.endswith("/cs2"), rel
    return True, f'rel={rel!r}'
t('platform: server_binary_rel_path() matches expected OS layout',
  t_platform_server_binary_rel_path_matches_cs2_path)


def t_platform_steamcmd_filename():
    """steamcmd_filename() returns the OS-appropriate launcher name."""
    import sys as _sys
    from cs2servergui.platform import steamcmd_filename
    name = steamcmd_filename()
    if _sys.platform == "win32":
        return name == "steamcmd.exe", f'got={name!r}'
    return name == "steamcmd.sh", f'got={name!r}'
t('platform: steamcmd_filename() is OS-appropriate',
  t_platform_steamcmd_filename)


def t_platform_metamod_bin_arch():
    """metamod_bin_arch() returns "win64" on Windows, "linuxsteamrt64" on Linux."""
    import sys as _sys
    from cs2servergui.platform import metamod_bin_arch
    arch = metamod_bin_arch()
    if _sys.platform == "win32":
        return arch == "win64", f'got={arch!r}'
    return arch == "linuxsteamrt64", f'got={arch!r}'
t('platform: metamod_bin_arch() is OS-appropriate',
  t_platform_metamod_bin_arch)


def t_platform_server_process_name():
    """server_process_name() is "cs2.exe" on Windows, "cs2" on Linux."""
    import sys as _sys
    from cs2servergui.platform import server_process_name
    name = server_process_name()
    if _sys.platform == "win32":
        return name == "cs2.exe", f'got={name!r}'
    return name == "cs2", f'got={name!r}'
t('platform: server_process_name() is OS-appropriate',
  t_platform_server_process_name)


def t_config_cs2_path_uses_platform_binary():
    """config.CS2_PATH must embed the platform-correct binary name so
    that os.path.isfile() checks in _preflight_checks look at the
    right path on both OSes."""
    import sys as _sys
    from cs2servergui import config as _cfg
    from cs2servergui.platform import server_binary_rel_path
    # join with a fake server_dir and compare
    fake_dir = "/srv/cs2" if _sys.platform != "win32" else "C:\\cs2"
    from cs2servergui.config import update_paths
    update_paths(fake_dir)
    import os as _os
    expected = _os.path.join(fake_dir, server_binary_rel_path())
    got = _cfg.CS2_PATH
    # restore to empty string so later tests aren't affected
    update_paths("")
    return (got == expected), f'expected={expected!r} got={got!r}'
t('config: CS2_PATH uses platform.server_binary_rel_path()',
  t_config_cs2_path_uses_platform_binary)


def t_cs2driver_process_image_name_is_platform_correct():
    """CS2Driver.process_image_name is now a property; it must return
    the same value as platform.server_process_name()."""
    from cs2servergui.platform import server_process_name
    d = CS2Driver()
    return d.process_image_name == server_process_name(), \
           f'd.process_image_name={d.process_image_name!r}'
t('cs2driver: process_image_name property matches platform.server_process_name()',
  t_cs2driver_process_image_name_is_platform_correct)


def t_platform_metamod_download_url_is_os_appropriate():
    """metamod_download_url() returns a .zip URL on Windows and a .tar.gz
    URL on Linux — alliedmods ships different formats per OS."""
    import sys as _sys
    from cs2servergui.platform import metamod_download_url
    url = metamod_download_url()
    if not url.startswith("https://mms.alliedmods.net/"):
        return False, f'wrong host: {url!r}'
    if _sys.platform == "win32":
        ok = url.endswith("-windows.zip")
    else:
        ok = url.endswith("-linux.tar.gz")
    return ok, f'url={url!r}'
t('platform: metamod_download_url() picks correct archive per OS',
  t_platform_metamod_download_url_is_os_appropriate)


def t_platform_css_download_url_is_os_appropriate():
    """css_download_url() returns a windows or linux variant — both are
    .zip but the filename's OS tag must match the current platform."""
    import sys as _sys
    from cs2servergui.platform import css_download_url
    url = css_download_url()
    if "github.com/roflmuffin/CounterStrikeSharp" not in url:
        return False, f'wrong repo: {url!r}'
    if not url.endswith(".zip"):
        return False, f'CSS download must be a .zip on both OSes: {url!r}'
    if _sys.platform == "win32":
        ok = "-windows-" in url
    else:
        ok = "-linux-" in url
    return ok, f'url={url!r}'
t('platform: css_download_url() picks correct OS tag',
  t_platform_css_download_url_is_os_appropriate)


def t_config_runtime_urls_match_platform():
    """config.RUNTIME_METAMOD_DEFAULT_URL and RUNTIME_CSS_DEFAULT_URL must
    track platform.metamod_download_url() / css_download_url() — this is
    what closes the "Slice 5 was Windows-only" gap.  If the runtime URL
    constants ever drift away from the platform functions, this catches it."""
    from cs2servergui import config as _cfg
    from cs2servergui.platform import metamod_download_url, css_download_url
    if _cfg.RUNTIME_METAMOD_DEFAULT_URL != metamod_download_url():
        return False, (f'metamod: cfg={_cfg.RUNTIME_METAMOD_DEFAULT_URL!r} '
                        f'platform={metamod_download_url()!r}')
    if _cfg.RUNTIME_CSS_DEFAULT_URL != css_download_url():
        return False, (f'css: cfg={_cfg.RUNTIME_CSS_DEFAULT_URL!r} '
                        f'platform={css_download_url()!r}')
    return True, 'config URLs delegate to platform.*'
t('config: runtime URLs match platform.metamod_download_url() / css_download_url()',
  t_config_runtime_urls_match_platform)


def t_platform_case_mismatch_hint_returns_none_when_path_exists():
    """case_mismatch_hint() returns None for an existing path — only
    fires when the path is missing AND a same-name-different-case sibling
    exists."""
    import tempfile, os as _os
    from cs2servergui.platform import case_mismatch_hint
    root = tempfile.mkdtemp(prefix='oblivion_case_ok_')
    try:
        existing = _os.path.join(root, 'real_dir')
        _os.makedirs(existing)
        return case_mismatch_hint(existing) is None, 'hint on existing path'
    finally:
        import shutil; shutil.rmtree(root, ignore_errors=True)
t('platform: case_mismatch_hint() returns None for existing paths',
  t_platform_case_mismatch_hint_returns_none_when_path_exists)


def t_platform_case_mismatch_hint_returns_none_when_no_sibling():
    """When a path component truly doesn't exist (no case-different
    sibling), return None — don't fabricate hints."""
    import tempfile, os as _os
    from cs2servergui.platform import case_mismatch_hint
    root = tempfile.mkdtemp(prefix='oblivion_case_miss_')
    try:
        bogus = _os.path.join(root, 'no_such_dir', 'no_such_file')
        return case_mismatch_hint(bogus) is None, 'hint without sibling'
    finally:
        import shutil; shutil.rmtree(root, ignore_errors=True)
t('platform: case_mismatch_hint() returns None when no case-sibling exists',
  t_platform_case_mismatch_hint_returns_none_when_no_sibling)


def t_platform_webview_gui_default_per_os():
    """webview_gui() returns 'edgechromium' on Windows, 'gtk' on Linux,
    when no OBLIVION_WEBVIEW_GUI override is set."""
    import sys as _sys, os as _os
    from cs2servergui.platform import webview_gui
    saved = _os.environ.pop("OBLIVION_WEBVIEW_GUI", None)
    try:
        got = webview_gui()
        expected = "edgechromium" if _sys.platform == "win32" else "gtk"
        return got == expected, f'got={got!r} expected={expected!r}'
    finally:
        if saved is not None:
            _os.environ["OBLIVION_WEBVIEW_GUI"] = saved
t('platform: webview_gui() picks edgechromium on Windows, gtk on Linux',
  t_platform_webview_gui_default_per_os)


def t_platform_webview_gui_respects_env_override():
    """Operators can override the GUI backend via OBLIVION_WEBVIEW_GUI
    (e.g. forcing 'qt' on Linux instead of GTK)."""
    import os as _os
    from cs2servergui.platform import webview_gui
    saved = _os.environ.get("OBLIVION_WEBVIEW_GUI", "")
    _os.environ["OBLIVION_WEBVIEW_GUI"] = "qt"
    try:
        return webview_gui() == "qt", f'override ignored: got={webview_gui()!r}'
    finally:
        if saved:
            _os.environ["OBLIVION_WEBVIEW_GUI"] = saved
        else:
            _os.environ.pop("OBLIVION_WEBVIEW_GUI", None)
t('platform: webview_gui() honours OBLIVION_WEBVIEW_GUI env override',
  t_platform_webview_gui_respects_env_override)


def t_platform_has_display_windows_always_true():
    """has_display() is always True on Windows — there's no equivalent of
    SSH-only / no-display servers in the Windows model."""
    import sys as _sys
    from cs2servergui.platform import has_display
    if _sys.platform != "win32":
        return True, 'skip: Linux host'
    return has_display() is True, f'got={has_display()!r}'
t('platform: has_display() returns True on Windows',
  t_platform_has_display_windows_always_true)


def t_platform_has_display_linux_checks_display_envs():
    """On Linux, has_display() must return True iff $DISPLAY or
    $WAYLAND_DISPLAY is set.  Skip on Windows (always True there)."""
    import sys as _sys, os as _os
    from cs2servergui.platform import has_display
    if _sys.platform == "win32":
        return True, 'skip: Windows host'
    # Save + clear both env vars to test the no-display path.
    saved_x   = _os.environ.pop("DISPLAY", None)
    saved_w   = _os.environ.pop("WAYLAND_DISPLAY", None)
    try:
        if has_display():
            return False, 'expected False with DISPLAY/WAYLAND_DISPLAY unset'
        _os.environ["DISPLAY"] = ":0"
        if not has_display():
            return False, 'expected True with DISPLAY=:0'
        _os.environ.pop("DISPLAY")
        _os.environ["WAYLAND_DISPLAY"] = "wayland-0"
        if not has_display():
            return False, 'expected True with WAYLAND_DISPLAY set'
        return True, 'has_display() matches env state'
    finally:
        _os.environ.pop("DISPLAY", None)
        _os.environ.pop("WAYLAND_DISPLAY", None)
        if saved_x is not None: _os.environ["DISPLAY"]         = saved_x
        if saved_w is not None: _os.environ["WAYLAND_DISPLAY"] = saved_w
t('platform: has_display() on Linux follows $DISPLAY / $WAYLAND_DISPLAY',
  t_platform_has_display_linux_checks_display_envs)


def t_platform_case_mismatch_hint_finds_sibling_on_linux():
    """When the expected component differs only by case from an existing
    sibling, the hint must mention both names so the operator can see
    the exact mismatch.  No-op on Windows (case-insensitive FS) — assert
    None there to confirm we don't false-positive on the host OS."""
    import sys as _sys, tempfile, os as _os
    from cs2servergui.platform import case_mismatch_hint
    root = tempfile.mkdtemp(prefix='oblivion_case_sib_')
    try:
        # Real folder is lowercase; operator-typed path uses capital.
        _os.makedirs(_os.path.join(root, 'counter-strike global offensive'))
        wrong = _os.path.join(root, 'Counter-Strike Global Offensive', 'game')
        hint = case_mismatch_hint(wrong)
        if _sys.platform == 'win32':
            # Windows FS is case-insensitive — Path 'Counter-Strike...' would
            # actually resolve to the lowercase one, so no hint should fire.
            return hint is None, f'unexpected hint on Windows: {hint!r}'
        if hint is None:
            return False, 'expected a case-mismatch hint on Linux, got None'
        if 'case mismatch' not in hint.lower():
            return False, f'hint missing "case mismatch": {hint!r}'
        if 'counter-strike global offensive' not in hint.lower():
            return False, f'hint missing sibling name: {hint!r}'
        return True, f'hint: {hint}'
    finally:
        import shutil; shutil.rmtree(root, ignore_errors=True)
t('platform: case_mismatch_hint() pinpoints case-different sibling on Linux',
  t_platform_case_mismatch_hint_finds_sibling_on_linux)


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
