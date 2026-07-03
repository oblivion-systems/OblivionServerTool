"""
platform.py — OS abstraction seam for v1.1 Linux support (Phase B + C).

Every call that differs between Windows and Linux lives here.  Windows
callers see the same API as Linux callers; only the implementations differ.

Windows is the current production target — the Windows paths are battle-tested
across hundreds of server sessions.  Linux paths are implemented now so Phase D
(packaging) only needs to add the installer/service file, not re-plumb OS calls.

Public API
----------
app_data_dir()                    → str
no_window_flags()                 → int
new_console_flags()               → int
list_pids(image_name, args_marker, log) → list[int]
kill_pid(pid)                     → bool
process_running(image_name, args_marker) → bool
listeners_on_port(port, log)      → list[tuple[str, int, str]]
server_binary_rel_path()          → str   # relative from server_dir
steamcmd_filename()               → str
metamod_bin_arch()                → str   # "win64" | "linuxsteamrt64"
server_process_name()             → str   # "cs2.exe" | "cs2"
metamod_download_url()            → str   # alliedmods MetaMod default
css_download_url()                → str   # CounterStrikeSharp default
case_mismatch_hint(path)          → str | None  # Linux-only case diagnostic
webview_gui()                     → str   # pywebview backend name
has_display()                     → bool  # desktop session available?
depotdownloader_filename()        → str   # "DepotDownloader.exe" | "DepotDownloader"
depotdownloader_asset_os()        → str   # "windows" | "linux"
make_executable(path)             → None  # chmod +x on Linux; no-op on Windows
own_process_names()               → set[str]  # our own image names for zombie kill
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Callable

_IS_WINDOWS = sys.platform == "win32"


# ── Config directory ──────────────────────────────────────────────────────────

def app_data_dir() -> str:
    """Per-user app-data root.

    Windows: %APPDATA%  (C:\\Users\\<name>\\AppData\\Roaming)
    Linux:   $XDG_CONFIG_HOME or ~/.config
    """
    if _IS_WINDOWS:
        return os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.environ.get("XDG_CONFIG_HOME",
                          os.path.join(os.path.expanduser("~"), ".config"))


# ── subprocess creation flags ─────────────────────────────────────────────────

def no_window_flags() -> int:
    """CREATE_NO_WINDOW on Windows; 0 on Linux (no console to hide)."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def new_console_flags() -> int:
    """CREATE_NEW_CONSOLE on Windows; 0 on Linux (caller uses a terminal
    emulator instead when a visible window is needed)."""
    if _IS_WINDOWS:
        return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return 0


# ── Process enumeration and termination ──────────────────────────────────────

def list_pids(
    image_name: str,
    args_marker: str,
    log: Callable[[str], None] = print,
) -> list[int]:
    """PIDs of processes named `image_name` whose command line contains
    `args_marker`.

    Used to find dedicated-server instances while ignoring the client
    (same binary name on the same machine — see user_setup memory).

    Windows: PowerShell Get-CimInstance → wmic fallback.
    Linux:   /proc/<pid>/cmdline scan.
    """
    if _IS_WINDOWS:
        return _list_pids_windows(image_name, args_marker, log)
    return _list_pids_linux(image_name, args_marker)


def kill_pid(pid: int) -> bool:
    """Force-kill process `pid`. Returns True if the signal was sent without
    raising (the process may not have exited yet when this returns)."""
    try:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
        else:
            os.kill(pid, signal.SIGKILL)
        return True
    except Exception:
        return False


def process_running(image_name: str, args_marker: str) -> bool:
    """True if at least one process matching image_name + args_marker is alive."""
    return bool(list_pids(image_name, args_marker))


# ── Game-path constants ───────────────────────────────────────────────────────

def server_binary_rel_path() -> str:
    """Relative path from server_dir to the CS2 dedicated-server binary.

    Windows: steamapps/.../game/bin/win64/cs2.exe
    Linux:   steamapps/.../game/bin/linuxsteamrt64/cs2
    """
    base = os.path.join("steamapps", "common",
                        "Counter-Strike Global Offensive",
                        "game", "bin")
    if _IS_WINDOWS:
        return os.path.join(base, "win64", "cs2.exe")
    return os.path.join(base, "linuxsteamrt64", "cs2")


def steamcmd_filename() -> str:
    """SteamCMD launcher filename — differs by OS."""
    return "steamcmd.exe" if _IS_WINDOWS else "steamcmd.sh"


def depotdownloader_filename() -> str:
    """DepotDownloader executable filename inside its release bundle.

    SteamRE ships self-contained per-OS builds: the Windows bundle
    contains DepotDownloader.exe, the Linux bundle contains a bare
    DepotDownloader ELF binary.  We invoke it directly (not via
    `dotnet`), so the filename has to match the OS.
    """
    return "DepotDownloader.exe" if _IS_WINDOWS else "DepotDownloader"


def depotdownloader_asset_os() -> str:
    """OS token used in SteamRE/DepotDownloader release asset filenames
    (e.g. DepotDownloader-windows-x64.zip / DepotDownloader-linux-x64.zip)."""
    return "windows" if _IS_WINDOWS else "linux"


def make_executable(path: str) -> None:
    """Ensure `path` has the executable bit set.  No-op on Windows (which
    has no Unix mode bits); on Linux, adds u+x/g+x/o+x so a freshly
    extracted binary (DepotDownloader, steamcmd.sh) can actually be run.

    zipfile.extractall does NOT preserve Unix mode on many archives, so
    an executable pulled from a .zip lands as 0644 and refuses to launch
    with 'Permission denied'.  Callers chmod it back to runnable here.
    Silently ignores a missing file — caller already handles that case.
    """
    if _IS_WINDOWS:
        return
    import stat
    try:
        st = os.stat(path)
    except OSError:
        return
    os.chmod(
        path,
        st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )


def own_process_names() -> set[str]:
    """Lower-cased image names that could be a stale copy of THIS app
    holding our Flask port.  Used by main.py's zombie-instance killer to
    avoid killing an unrelated process that merely grabbed the port.

    Windows: the frozen .exe or a python launcher.
    Linux:   python / python3, or the PyInstaller onefile binary name.
    """
    if _IS_WINDOWS:
        return {"oblivionservertool.exe", "python.exe", "pythonw.exe"}
    return {"oblivion-server-tool", "oblivionservertool", "python", "python3"}


def metamod_bin_arch() -> str:
    """Architecture subfolder name inside MetaMod's bin/ directory.

    MetaMod mirrors the engine's binary layout: win64 on Windows,
    linuxsteamrt64 on Linux.  Used by the DLL-nesting fix in core.py.
    """
    return "win64" if _IS_WINDOWS else "linuxsteamrt64"


def server_process_name() -> str:
    """CS2 dedicated-server process image name."""
    return "cs2.exe" if _IS_WINDOWS else "cs2"


# ── Runtime download URLs (auto-install MetaMod + CSS) ────────────────────────
# Pinned per OS so we ship a known-good combination.  Operators can override
# in oblivion_config.json (keys: metamod_download_url, css_download_url) when
# a newer build lands before we ship an app update.

_METAMOD_BUILD = "2.0.0-git1402"
_CSS_VERSION   = "1.0.369"


def metamod_download_url() -> str:
    """MetaMod : Source 2 archive URL for the current OS.

    Windows: .zip  ·  Linux: .tar.gz  (alliedmods convention).
    """
    if _IS_WINDOWS:
        return (f"https://mms.alliedmods.net/mmsdrop/2.0/"
                f"mmsource-{_METAMOD_BUILD}-windows.zip")
    return (f"https://mms.alliedmods.net/mmsdrop/2.0/"
            f"mmsource-{_METAMOD_BUILD}-linux.tar.gz")


def css_download_url() -> str:
    """CounterStrikeSharp 'with-runtime' archive URL for the current OS.

    Both OSes ship .zip — Linux variant just has 'linux' in the filename.
    """
    os_tag = "windows" if _IS_WINDOWS else "linux"
    return (f"https://github.com/roflmuffin/CounterStrikeSharp/releases/"
            f"download/v{_CSS_VERSION}/"
            f"counterstrikesharp-with-runtime-{os_tag}-{_CSS_VERSION}.zip")


# ── Case-mismatch diagnostic (Linux only) ─────────────────────────────────────
# Windows is case-insensitive — a path lookup always works regardless of how
# the operator typed it.  Linux is case-sensitive, so a CS2 install at
# /srv/cs2/steamapps/Common/Counter-Strike Global Offensive (capital C in
# Common) fails our os.path.isfile() pre-flight with a generic
# "CS2 is not installed" error.  This helper walks the path components,
# finds the FIRST one that doesn't exist but does exist with a different
# case in its parent dir, and returns a one-line hint pointing the
# operator at the exact mismatch.  Returns None on Windows or when there
# is no case-different match (i.e. the path really is missing).

# ── Desktop window backend (v1.2 Linux non-headless) ─────────────────────────
# pywebview wraps different native webview engines per OS.  Windows ships
# Edge WebView2 with Win 11 (or 1-click install on Win 10); Linux operators
# install WebKitGTK once (`apt install python3-gi gir1.2-webkit2-4.1`) and
# pywebview's GTK backend uses it.  The single hardcoded "edgechromium" in
# main.py becomes a platform-aware pick — Windows callers keep WebView2
# unchanged.

def webview_gui() -> str:
    """pywebview's GUI backend name for `webview.start(gui=...)`.

    Windows: "edgechromium"  ·  Linux: "gtk"
    Override-able via `OBLIVION_WEBVIEW_GUI=<name>` for operators who want
    to force "qt" instead of the GTK default on Linux.
    """
    override = os.environ.get("OBLIVION_WEBVIEW_GUI", "").strip()
    if override:
        return override
    return "edgechromium" if _IS_WINDOWS else "gtk"


def has_display() -> bool:
    """True if a desktop session is available for a GUI window.

    Windows: always True (the desktop is always there).
    Linux:   True iff `$DISPLAY` or `$WAYLAND_DISPLAY` is set — SSH-only
             headless boxes have neither.

    Used to auto-fall-back to `--headless` behaviour when the operator
    launches without `--headless` on a server that can't open a window.
    """
    if _IS_WINDOWS:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def case_mismatch_hint(path: str) -> str | None:
    """If `path` doesn't exist but does exist under a different case in
    its parent dir, return a one-line operator-friendly hint.  Otherwise
    None.  No-op on Windows."""
    if _IS_WINDOWS or not path:
        return None
    if os.path.exists(path):
        return None
    # Walk from the root forwards, looking for the first missing component.
    parts = []
    head = path
    while True:
        head, tail = os.path.split(head)
        if not tail:
            if head:
                parts.insert(0, head)
            break
        parts.insert(0, tail)
    if not parts:
        return None
    cur = parts[0] if parts[0].startswith("/") else os.path.sep
    if not parts[0].startswith("/"):
        cur = parts[0]
        rest = parts[1:]
    else:
        rest = parts[1:]
    for part in rest:
        candidate = os.path.join(cur, part)
        if os.path.exists(candidate):
            cur = candidate
            continue
        # Component is missing — scan siblings for case-insensitive match.
        try:
            siblings = os.listdir(cur)
        except OSError:
            return None
        lower = part.lower()
        for sib in siblings:
            if sib != part and sib.lower() == lower:
                return (f"case mismatch — expected {part!r} but found "
                        f"{sib!r} in {cur} (Linux is case-sensitive)")
        return None
    return None


# ── Port listener resolution ──────────────────────────────────────────────────

def listeners_on_port(
    port: int,
    log: Callable[[str], None] = print,
) -> list[tuple[str, int, str]]:
    """(bound_address, pid, image_name_lower) for every process listening on
    `port`.  Multiple entries when the same port is bound to several addresses
    (IPv4 + IPv6, or 0.0.0.0 AND a specific IP).

    Windows: netstat -ano → tasklist.
    Linux:   ss -tlnp → /proc/<pid>/status.

    Never raises — swallows subprocess errors and returns whatever was found.
    """
    if _IS_WINDOWS:
        return _listeners_windows(port, log)
    return _listeners_linux(port, log)


# ── Windows implementations ───────────────────────────────────────────────────

def _list_pids_windows(
    image_name: str,
    args_marker: str,
    log: Callable[[str], None],
) -> list[int]:
    pids: list[int] = []

    # Strategy 1 — PowerShell Get-CimInstance (works on all Windows 10/11,
    # including 24H2 where wmic was removed).
    ps_cmd = (
        f"Get-CimInstance Win32_Process -Filter \"Name='{image_name}'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{args_marker}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=8,
            creationflags=no_window_flags(),
        )
        if ps.returncode == 0:
            for line in ps.stdout.splitlines():
                s = line.strip()
                if s.isdigit():
                    pids.append(int(s))
            return pids
    except Exception as exc:
        log(f"[platform] PowerShell process enumeration failed: {exc} — trying wmic")

    # Strategy 2 — wmic fallback (deprecated; absent on Win 11 24H2).
    try:
        res = subprocess.run(
            ["wmic", "process", "where", f"name='{image_name}'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=5,
        )
        for line in res.stdout.splitlines():
            if args_marker.lower() in line.lower():
                parts = line.strip().split(",")
                pid = parts[-1].strip()
                if pid.isdigit():
                    pids.append(int(pid))
    except FileNotFoundError:
        log(f"[platform] Neither PowerShell nor wmic available — cannot identify "
            f"stale {image_name}.  Close any running dedicated server manually "
            "before starting.")
    except Exception as exc:
        log(f"[platform] wmic fallback failed: {exc}")
    return pids


def _listeners_windows(
    port: int,
    log: Callable[[str], None],
) -> list[tuple[str, int, str]]:
    listeners: list[tuple[str, int, str]] = []
    try:
        net = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5,
        )
        for line in net.stdout.splitlines():
            cols = line.split()
            if len(cols) < 5 or cols[3] != "LISTENING":
                continue
            addr = cols[1]
            if not addr.endswith(f":{port}"):
                continue
            pid_s = cols[4]
            if not (pid_s.isdigit() and int(pid_s) > 0):
                continue
            tl = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid_s}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            first = tl.stdout.splitlines()[0] if tl.stdout.strip() else ""
            name = (first.split('","', 1)[0].strip('"').lower()
                    if first.startswith('"') else "?")
            listeners.append((addr, int(pid_s), name))
    except Exception as exc:
        log(f"[platform] listeners_on_port({port}) failed: {exc}")
    return listeners


# ── Linux implementations ─────────────────────────────────────────────────────

def _list_pids_linux(image_name: str, args_marker: str) -> list[int]:
    """Scan /proc for processes matching image_name + args_marker."""
    pids: list[int] = []
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                cmdline_path = os.path.join(entry.path, "cmdline")
                with open(cmdline_path, "rb") as f:
                    cmdline = f.read().replace(b"\x00", b" ").decode(
                        "utf-8", errors="replace"
                    )
                if image_name in cmdline and args_marker in cmdline:
                    pids.append(int(entry.name))
            except (PermissionError, FileNotFoundError, ProcessLookupError):
                continue
    except Exception:
        pass
    return pids


def _listeners_linux(
    port: int,
    log: Callable[[str], None],
) -> list[tuple[str, int, str]]:
    """Use `ss -tlnp` to find listeners on `port`, then resolve PIDs via
    /proc/<pid>/status for the image name.

    ss output (relevant columns):
      Netid State  Local Address:Port  Process
      tcp   LISTEN 0.0.0.0:5000       users:(("python",pid=12345,fd=3))
    """
    listeners: list[tuple[str, int, str]] = []
    try:
        res = subprocess.run(
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True, text=True, timeout=5,
        )
        import re
        pid_re = re.compile(r'pid=(\d+)')
        for line in res.stdout.splitlines():
            if "LISTEN" not in line:
                continue
            cols = line.split()
            # Local address is the 4th column (0-indexed col 3)
            addr = cols[3] if len(cols) > 3 else f"?:{port}"
            for m in pid_re.finditer(line):
                pid = int(m.group(1))
                name = _proc_name(pid)
                listeners.append((addr, pid, name))
    except FileNotFoundError:
        # ss not available — fall back to /proc/net/tcp parsing
        listeners = _listeners_linux_proc(port, log)
    except Exception as exc:
        log(f"[platform] ss listeners_on_port({port}) failed: {exc}")
    return listeners


def _proc_name(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("Name:"):
                    return line.split(":", 1)[1].strip().lower()
    except Exception:
        pass
    return "?"


def _listeners_linux_proc(
    port: int,
    log: Callable[[str], None],
) -> list[tuple[str, int, str]]:
    """Pure-Python fallback: parse /proc/net/tcp{,6} for the port,
    then resolve inodes to PIDs via /proc/<pid>/fd/."""
    import socket as _socket
    listeners: list[tuple[str, int, str]] = []
    port_hex = f"{port:04X}"
    inode_to_pid: dict[str, int] = {}

    # Build inode → pid map by scanning /proc/<pid>/fd/
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            fd_dir = os.path.join(entry.path, "fd")
            try:
                for fd in os.scandir(fd_dir):
                    try:
                        target = os.readlink(fd.path)
                        if target.startswith("socket:["):
                            inode = target[8:-1]
                            inode_to_pid[inode] = int(entry.name)
                    except Exception:
                        continue
            except PermissionError:
                continue
    except Exception:
        pass

    for net_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(net_file) as f:
                for line in f:
                    cols = line.split()
                    if len(cols) < 10:
                        continue
                    local = cols[1]          # hex "addr:port"
                    state = cols[3]
                    inode = cols[9]
                    if state != "0A":        # 0A = LISTEN
                        continue
                    lport = local.rsplit(":", 1)[-1]
                    if lport.upper() != port_hex:
                        continue
                    pid = inode_to_pid.get(inode, 0)
                    name = _proc_name(pid) if pid else "?"
                    listeners.append((local, pid, name))
        except FileNotFoundError:
            continue
        except Exception as exc:
            log(f"[platform] /proc/net/tcp parse failed: {exc}")
    return listeners
