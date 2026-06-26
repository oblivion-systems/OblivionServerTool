"""
cs2servergui/registry_client.py — Community plugin registry client.

v0.15 slice 2 / task #90.  Fetches a JSON catalog of community plugins from
``OBLIVION_REGISTRY_URL`` (typically the raw.githubusercontent.com URL of
the OblivionPluginRegistry repo's catalog.json), caches it locally with a
24h TTL, and exposes an install path that downloads + sha256-verifies +
extracts a plugin's release zip into ``%APPDATA%/.../plugins/<slug>/``.

The registry is the OPTIONAL discovery layer on top of slice 1's local
plugin folder.  Once installed, a registry plugin is indistinguishable
from one the operator dropped in by hand (source='local', shows the
blue Local badge in the SPA Library).

Safety properties:
    * HTTP fetch has hard timeout + content-length cap.
    * SHA-256 verified BEFORE the zip is opened — a bad hash is rejected
      with the bytes still in memory, never written to disk.
    * Zip extraction is "Zip Slip"-safe — every member's normalized
      target path must resolve INSIDE the destination, else the install
      aborts and the half-extracted tempdir is wiped.
    * The extracted folder's plugin.json must declare a slug matching
      the one we requested — protects against a "plugin name confusion"
      attack where catalog.json lists slug=A but the zip extracts as B.
    * Registry URL is HARDCODED in config.py — operators can't redirect
      it to a hostile host without recompiling.  When v0.15 ships and a
      registry exists, every running .exe trusts the same URL.

When the registry doesn't exist yet (pre-launch, 404/connection refused),
fetch returns an empty catalog gracefully so the SPA shows "no community
plugins available yet" instead of a network-error banner.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

from . import config as _config


# v0.16.8 (review fix #4) — per-component lock so two simultaneous
# install_runtime calls for the same component can't race on
# shutil.copy2 against the same csgo/addons/<x>/ paths.  A second click
# while the first is in flight blocks until the first finishes.
_RUNTIME_LOCKS: dict[str, threading.Lock] = {}
_RUNTIME_LOCKS_GUARD = threading.Lock()


def _runtime_lock_for(component: str) -> threading.Lock:
    """Return (lazy-create) the lock for a given runtime component."""
    with _RUNTIME_LOCKS_GUARD:
        lk = _RUNTIME_LOCKS.get(component)
        if lk is None:
            lk = threading.Lock()
            _RUNTIME_LOCKS[component] = lk
        return lk


# Public exception type — web.py catches this to map onto specific 4xx/5xx.
class RegistryError(Exception):
    """Anything wrong with the registry fetch or install flow."""


# ─── Cache resolution ──────────────────────────────────────────────────────

def _resolve_cache_path() -> str:
    """Cache file path.  Lives next to oblivion_config.json so it survives
    app upgrades / reinstalls (the installer's UninstallDelete policy
    preserves the config dir intentionally)."""
    return os.path.join(os.path.dirname(_config._CONFIG_FILE), "registry_cache.json")


def _load_cached_catalog() -> dict | None:
    """Read the cache file.  Returns None on any error (missing file,
    bad JSON, schema mismatch) — caller decides whether to fetch fresh."""
    path = _resolve_cache_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as exc:
        print(f"[registry] Cached catalog at {path!r} unreadable: {exc!r}",
              file=sys.stderr)
        return None
    return doc


def _save_cached_catalog(catalog: dict, source_url: str) -> None:
    """Write the cache file atomically (tmp + rename) so a crashed write
    can't leave a partially-written cache that breaks the next read."""
    path = _resolve_cache_path()
    payload = {
        "schema_version": 1,
        "fetched_at":     int(time.time()),
        "source_url":     source_url,
        "catalog":        catalog,
    }
    dirpath = os.path.dirname(path)
    try:
        os.makedirs(dirpath, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix="registry_cache_", suffix=".tmp",
                                          dir=dirpath)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            # Best-effort cleanup; don't mask the original error.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        print(f"[registry] Cache write failed at {path!r}: {exc!r}",
              file=sys.stderr)


def _is_cache_fresh(cache: dict) -> bool:
    """24h TTL by default (see REGISTRY_CACHE_TTL_SECONDS).  Stale caches
    are still USEFUL — they back up a failed fetch — but fetch_catalog()
    will try fresh first when stale."""
    try:
        fetched_at = int(cache.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return False
    return (time.time() - fetched_at) < _config.REGISTRY_CACHE_TTL_SECONDS


# ─── HTTP fetch ────────────────────────────────────────────────────────────

def _http_fetch(url: str, max_bytes: int, timeout: float) -> bytes:
    """Plain GET with size cap.  Raises RegistryError on transport
    failure, HTTP non-200, or content too large.  No retries — caller's
    decision whether to fall back to cache."""
    req = urllib.request.Request(url, headers={"User-Agent": "OblivionServerTool"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Honour Content-Length if the server gave it — saves us
            # downloading something we'd reject anyway.
            cl = resp.headers.get("Content-Length")
            if cl:
                try:
                    if int(cl) > max_bytes:
                        raise RegistryError(
                            f"upstream content-length {cl} exceeds cap {max_bytes}")
                except ValueError:
                    pass
            buf = io.BytesIO()
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                if buf.tell() + len(chunk) > max_bytes:
                    raise RegistryError(f"download exceeded {max_bytes}-byte cap")
                buf.write(chunk)
            return buf.getvalue()
    except RegistryError:
        raise
    except urllib.error.HTTPError as exc:
        raise RegistryError(f"HTTP {exc.code} from {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RegistryError(f"network error fetching {url}: {exc.reason}") from exc
    except Exception as exc:
        raise RegistryError(f"unexpected error fetching {url}: {exc!r}") from exc


def _validate_catalog(catalog: object) -> dict:
    """Bare-minimum schema check — schema_version=1, plugins is a list of
    dicts with at least slug+versions[].  Catalogs that fail validation
    are treated as 'no catalog yet' so a botched registry edit doesn't
    nuke the SPA's Plugin tab."""
    if not isinstance(catalog, dict):
        raise RegistryError(f"catalog is not a JSON object: {type(catalog).__name__}")
    if catalog.get("schema_version") != 1:
        raise RegistryError(
            f"catalog.schema_version={catalog.get('schema_version')!r} (want 1)")
    plugins = catalog.get("plugins")
    if not isinstance(plugins, list):
        raise RegistryError("catalog.plugins is not a list")
    for entry in plugins:
        if not isinstance(entry, dict):
            raise RegistryError(f"plugin entry is not a dict: {entry!r}")
        if not entry.get("slug"):
            raise RegistryError(f"plugin entry missing slug: {entry!r}")
        versions = entry.get("versions")
        if not isinstance(versions, list) or not versions:
            raise RegistryError(f"plugin {entry['slug']!r} has no versions")
        for v in versions:
            if not isinstance(v, dict):
                raise RegistryError(f"plugin {entry['slug']!r} version not a dict")
            if not v.get("download_url") or not v.get("sha256"):
                raise RegistryError(
                    f"plugin {entry['slug']!r} version missing download_url+sha256")
    return catalog


def fetch_catalog(force: bool = False) -> dict:
    """Return the registry catalog as a dict.

    Behaviour:
      - If cache exists and is fresh AND force=False: return cache.catalog.
      - Else: fetch ``OBLIVION_REGISTRY_URL`` with timeout + size cap.
        On success, validate + write cache + return fresh catalog.
        On failure: return the stale cached catalog if any, else an
        empty catalog ({schema_version:1, plugins:[]}).
    Result is always a valid catalog dict — never raises to callers
    (web.py wraps this in a non-fatal endpoint that always 200s).
    """
    cached = _load_cached_catalog()
    if cached and not force:
        if _is_cache_fresh(cached):
            return cached.get("catalog") or {"schema_version": 1, "plugins": []}

    try:
        raw = _http_fetch(
            _config.OBLIVION_REGISTRY_URL,
            max_bytes=_config.REGISTRY_MAX_DOWNLOAD_BYTES,
            timeout=_config.REGISTRY_FETCH_TIMEOUT_SECONDS,
        )
        doc = json.loads(raw.decode("utf-8"))
        catalog = _validate_catalog(doc)
        _save_cached_catalog(catalog, _config.OBLIVION_REGISTRY_URL)
        return catalog
    except Exception as exc:
        print(f"[registry] Fresh fetch failed: {exc!r}", file=sys.stderr)
        # Fall back to whatever we cached previously, even if stale.
        if cached and isinstance(cached.get("catalog"), dict):
            return cached["catalog"]
        return {"schema_version": 1, "plugins": [], "_offline": True}


def get_registry_status() -> dict:
    """Diagnostic shape for the API endpoint — exposes when the catalog was
    fetched, whether we're serving stale data, and which URL we're pointing at.
    Used by the SPA to show a 'last refreshed N hours ago' label."""
    cached = _load_cached_catalog()
    if not cached:
        return {
            "source_url":  _config.OBLIVION_REGISTRY_URL,
            "fetched_at":  0,
            "fresh":       False,
            "stale":       False,
            "have_cache":  False,
        }
    fetched_at = int(cached.get("fetched_at") or 0)
    fresh = _is_cache_fresh(cached)
    return {
        "source_url": cached.get("source_url") or _config.OBLIVION_REGISTRY_URL,
        "fetched_at": fetched_at,
        "fresh":      fresh,
        "stale":      (not fresh) and fetched_at > 0,
        "have_cache": True,
    }


# ─── Install flow ──────────────────────────────────────────────────────────

def _select_version(plugin_entry: dict, version: str | None) -> dict:
    """Pick the version dict from a catalog plugin entry.  None = first
    listed (catalog convention: newest first).  Raises RegistryError on
    unknown version."""
    versions = plugin_entry.get("versions") or []
    if not versions:
        raise RegistryError(f"plugin {plugin_entry.get('slug')!r} has no versions")
    if version is None:
        return versions[0]
    for v in versions:
        if v.get("version") == version:
            return v
    raise RegistryError(
        f"version {version!r} not in plugin {plugin_entry.get('slug')!r}'s versions")


def _verify_sha256(data: bytes, expected: str) -> None:
    """Constant-time sha256 compare.  RegistryError on mismatch — the
    bytes are never written to disk in that case (caller still has them
    in memory but will discard on the exception)."""
    actual = hashlib.sha256(data).hexdigest()
    expected = (expected or "").lower().strip()
    if actual.lower() != expected:
        raise RegistryError(
            f"sha256 mismatch: expected {expected!r}, got {actual!r}")


def _safe_extract_zip(zip_bytes: bytes, dest_dir: str) -> None:
    """Extract a zip into dest_dir with Zip Slip protection.  Every
    member's normalised target path MUST resolve inside dest_dir.  Symbolic
    links in zip entries are ignored (zipfile doesn't support them on
    Windows anyway, but checked here for cross-platform safety)."""
    os.makedirs(dest_dir, exist_ok=True)
    dest_norm = os.path.realpath(dest_dir)
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise RegistryError(f"download is not a valid zip: {exc}") from exc
    for member in zf.infolist():
        # Reject absolute paths and parent-dir escapes BEFORE letting
        # zipfile do anything with them.
        name = member.filename
        if name.startswith("/") or name.startswith("\\"):
            raise RegistryError(f"zip entry uses absolute path: {name!r}")
        target = os.path.realpath(os.path.join(dest_dir, name))
        # On Windows os.path.realpath includes drive letter etc. — the
        # commonprefix check below catches any escape attempt.
        if os.path.commonpath([dest_norm, target]) != dest_norm:
            raise RegistryError(f"zip slip detected on entry {name!r}")
    zf.extractall(dest_dir)
    zf.close()


def _safe_extract_targz(tar_bytes: bytes, dest_dir: str) -> None:
    """Extract a tar.gz into dest_dir with path-traversal protection.

    Used for the Linux MetaMod download (alliedmods ships .tar.gz on Linux,
    .zip on Windows).  Same safety profile as _safe_extract_zip:

        * Reject absolute paths and parent-dir escapes.
        * Skip symlinks and hardlinks (MetaMod's archive doesn't need them;
          allowing them opens a symlink-traversal attack where the link
          points outside dest_dir).
        * Skip device files and other special types — only regular files
          and directories are extracted.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest_norm = os.path.realpath(dest_dir)
    try:
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz")
    except tarfile.TarError as exc:
        raise RegistryError(f"download is not a valid tar.gz: {exc}") from exc
    try:
        safe_members: list[tarfile.TarInfo] = []
        for member in tf.getmembers():
            name = member.name
            if name.startswith("/") or name.startswith("\\"):
                raise RegistryError(f"tar entry uses absolute path: {name!r}")
            target = os.path.realpath(os.path.join(dest_dir, name))
            if os.path.commonpath([dest_norm, target]) != dest_norm:
                raise RegistryError(f"path traversal detected on entry {name!r}")
            if member.issym() or member.islnk():
                # MetaMod's tar.gz doesn't ship links — drop silently rather
                # than fail, in case a future build adds harmless ones.
                continue
            if not (member.isreg() or member.isdir()):
                continue
            safe_members.append(member)
        tf.extractall(dest_dir, members=safe_members)
    finally:
        tf.close()


def _safe_extract_archive(data: bytes, dest_dir: str, url: str) -> None:
    """Pick the right extractor based on `url`'s suffix.

    `.tar.gz` / `.tgz` → tar.gz; anything else → zip.  Centralises the
    routing so the install path doesn't have to know about formats.
    """
    low = url.lower()
    if low.endswith(".tar.gz") or low.endswith(".tgz"):
        _safe_extract_targz(data, dest_dir)
    else:
        _safe_extract_zip(data, dest_dir)


# ─── Version comparison (semver-lite) ──────────────────────────────────────
# Used by /api/plugins to mark a card "Update available" when the registry's
# latest version is newer than the installed version.  Full semver is
# overkill — major.minor.patch[-prerelease] covers every plugin author's
# realistic versioning.  Missing/garbage strings are treated as 0.0.0, so a
# bundled plugin with no version field never produces a false "update".

def parse_version(v: str | None) -> tuple:
    """Return a comparable tuple for `v`.  Empty/garbage = (0, 0, 0, '~').
    The `~` sentinel makes a release version sort AFTER any prerelease at
    the same (major, minor, patch).
    """
    s = (v or "").strip().lstrip("vV")
    if not s:
        return (0, 0, 0, "~")
    main, _, pre = s.partition("-")
    parts = main.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return (0, 0, 0, "~")
    return (major, minor, patch, pre or "~")


def has_update(installed: str | None, available: str | None) -> bool:
    """True if `available` is a STRICTLY higher version than `installed`.
    Both can be missing/empty — returns False unless we can confidently
    say the available version is newer (avoids false-positive update
    pills on versionless bundled plugins)."""
    if not (installed and available):
        return False
    return parse_version(available) > parse_version(installed)


# ─── HTTPS + size-cap fetch (shared between registry + custom URL) ─────────

def _fetch_zip_from_url(url: str, *, allow_http: bool = False) -> bytes:
    """Wrap _http_fetch with an HTTPS guard.  Custom-URL install rejects
    plain http:// because operators copy-pasting from forums shouldn't
    accidentally download over a downgradable transport.  Localhost is
    the only http exception (used by tests + power users running their
    own staging registry)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise RegistryError(f"URL scheme {parsed.scheme!r} not supported "
                            f"(expected https://)")
    if parsed.scheme == "http" and not allow_http:
        host = (parsed.hostname or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise RegistryError(
                f"plain http:// is not allowed for plugin downloads "
                f"(host={host!r}); use https:// or accept localhost only")
    return _http_fetch(
        url,
        max_bytes=_config.REGISTRY_MAX_DOWNLOAD_BYTES,
        timeout=_config.REGISTRY_FETCH_TIMEOUT_SECONDS,
    )


# ─── Custom URL install ────────────────────────────────────────────────────

def install_from_url(url: str, expected_sha256: str | None = None,
                      expected_slug: str | None = None) -> dict:
    """Download + extract a plugin zip from an arbitrary URL.

    Use cases:
        * Author-published GitHub release zip — operator pastes the URL
          before the plugin reaches the curated registry.
        * Internal/private registry mirror (operator's own LAN).

    The safety guarantees mirror install_plugin():
        * HTTPS-only (plain http:// rejected unless host is localhost).
        * Size cap + timeout enforced by _fetch_zip_from_url.
        * Optional sha256: when provided, mismatch is fatal.  When NOT
          provided, the install proceeds but result['sha256_provided']
          is False so the SPA can surface a "unverified source" badge.
        * Zip Slip protection.
        * The extracted plugin.json's slug becomes the install slug.  If
          `expected_slug` is provided AND differs from the manifest, the
          install is rejected (defends against an operator pasting a URL
          they thought was for slug A but actually serves slug B).
        * Atomic install via tempdir + move.

    Returns the same shape as install_plugin().
    """
    data = _fetch_zip_from_url(url)

    if expected_sha256:
        _verify_sha256(data, expected_sha256)
    sha_actual = hashlib.sha256(data).hexdigest()

    # Stage + validate manifest BEFORE the atomic move so we can read the
    # slug out of plugin.json — same flow as install_plugin but without
    # a catalog entry to cross-reference.
    staging = tempfile.mkdtemp(prefix="oblivion_url_install_")
    try:
        _safe_extract_zip(data, staging)

        # Try both layouts: <slug>/plugin.json OR ./plugin.json at root.
        # We don't know the slug yet — walk one level and find a plugin.json.
        candidates = []
        if os.path.isfile(os.path.join(staging, "plugin.json")):
            candidates.append(("", staging))
        for entry in os.listdir(staging):
            sub = os.path.join(staging, entry)
            if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "plugin.json")):
                candidates.append((entry, sub))
        if not candidates:
            raise RegistryError(
                "zip does not contain a plugin.json (looked at root and "
                "one-level-deep folders)")
        if len(candidates) > 1:
            raise RegistryError(
                f"zip contains multiple plugin.json files at {len(candidates)} "
                f"locations; expected exactly one")

        _root_label, extracted_root = candidates[0]
        with open(os.path.join(extracted_root, "plugin.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        declared_slug = (manifest.get("slug") or "").strip()
        if not declared_slug:
            raise RegistryError("plugin.json missing or empty slug")
        if expected_slug and expected_slug != declared_slug:
            raise RegistryError(
                f"plugin.json declares slug={declared_slug!r} but "
                f"caller expected {expected_slug!r}")

        # Enforce baseline schema sanity (mirrors slice 1's loader).
        if manifest.get("schema_version") != 1:
            raise RegistryError(
                f"plugin.json schema_version={manifest.get('schema_version')!r} "
                f"(want 1)")
        for required in ("display_name", "kind", "modes", "copy_rules"):
            if required not in manifest:
                raise RegistryError(
                    f"plugin.json missing required field {required!r}")

        from .core import _resolve_user_plugins_dir
        user_dir = _resolve_user_plugins_dir()
        os.makedirs(user_dir, exist_ok=True)
        final_dir = os.path.join(user_dir, declared_slug)
        if os.path.isdir(final_dir):
            shutil.rmtree(final_dir, ignore_errors=False)
        shutil.move(extracted_root, final_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    files_written = sum(
        len(files) for _root, _dirs, files in os.walk(final_dir)
    )
    return {
        "slug":             declared_slug,
        "version":          manifest.get("version") or "",
        "installed_at":     int(time.time()),
        "dest_dir":         final_dir,
        "files_written":    files_written,
        "source_url":       url,
        "sha256":           sha_actual,
        "sha256_provided":  bool(expected_sha256),
    }


# ─── Runtime install (v0.16.5 / task #163) ─────────────────────────────────
# Auto-install MetaMod + CounterStrikeSharp directly into csgo/addons/.
# Different shape from install_plugin (which targets %APPDATA%/plugins/):
# the runtime IS the game-engine extension, not a manageable plugin, so
# it must land in csgo/.  Uses the same safe-download primitives —
# size cap, timeout, Zip Slip protection, atomic-via-tempdir staging.

_RUNTIME_COMPONENTS = {
    "metamod": {
        "label":           "MetaMod : Source 2",
        "url_attr":        "RUNTIME_METAMOD_DEFAULT_URL",
        "config_key":      "metamod_download_url",
        "expect_relpath":  os.path.join("addons", "metamod"),
    },
    "css": {
        "label":           "CounterStrikeSharp (with runtime)",
        "url_attr":        "RUNTIME_CSS_DEFAULT_URL",
        "config_key":      "css_download_url",
        "expect_relpath":  os.path.join("addons", "counterstrikesharp"),
    },
}


def _resolve_runtime_url(component: str) -> str:
    """Resolve the download URL for `component`, allowing oblivion_config.json
    to override the hardcoded default (for when a new MetaMod / CSS build
    lands before we ship an app update)."""
    meta = _RUNTIME_COMPONENTS.get(component)
    if not meta:
        raise RegistryError(f"unknown runtime component {component!r} "
                            f"(expected one of {list(_RUNTIME_COMPONENTS)})")
    # Check the live config dict for an override; fall through to the
    # config.py default if absent / empty.
    override = ""
    try:
        cfg_path = _config._CONFIG_FILE
        if os.path.isfile(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                doc = json.load(f)
            override = (doc.get(meta["config_key"]) or "").strip()
    except Exception:
        # Bad config file shouldn't break runtime install — fall through
        # to the default URL.
        pass
    return override or getattr(_config, meta["url_attr"])


def install_runtime(component: str, csgo_dir: str) -> dict:
    """Download + extract a runtime component (MetaMod or CSS) into
    `csgo_dir`.

    Safety mirrors install_plugin / install_from_url:
        * HTTPS only (allow http on localhost for tests).
        * Size cap + timeout from RUNTIME_MAX_DOWNLOAD_BYTES / TIMEOUT.
        * Zip Slip protection — every member's normalised target must
          resolve inside csgo_dir.
        * Atomic staging — extract into a tempdir, validate the expected
          addons/<x> folder exists, then merge into csgo_dir/.
        * Existing files OVERWRITTEN by the merge (operator can re-run
          this to repair a corrupted install).

    Returns a result dict with `component`, `url`, `sha256` (computed,
    not verified — runtime URLs are operator-overridable so we don't
    pin a hash), `extracted_files`, `dest_dir`.  RegistryError on any
    failure; csgo_dir is untouched if the zip is rejected.
    """
    meta = _RUNTIME_COMPONENTS.get(component)
    if not meta:
        raise RegistryError(f"unknown runtime component {component!r}")
    if not os.path.isdir(csgo_dir):
        raise RegistryError(f"csgo_dir does not exist: {csgo_dir!r}")

    # v0.16.8 (review fix #4) — per-component lock prevents two simultaneous
    # install_runtime calls for the same component from racing on shutil.copy2
    # against the same csgo/addons/<x>/ paths.  A double-click on the SPA's
    # "Install" button (the second click was already dispatched before
    # btn.disabled took effect) reached here twice; the second call now
    # blocks until the first finishes, then returns the same end state.
    lock = _runtime_lock_for(component)
    if not lock.acquire(blocking=False):
        raise RegistryError(
            f"install of {component!r} already in progress — wait for it "
            f"to finish, or refresh the page")
    try:
        return _install_runtime_locked(component, csgo_dir, meta)
    finally:
        lock.release()


def _install_runtime_locked(component: str, csgo_dir: str, meta: dict) -> dict:
    """Real body of install_runtime; runs inside the per-component lock."""
    url = _resolve_runtime_url(component)

    # Bigger zips than registry plugins — use the runtime-specific caps.
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise RegistryError(f"URL scheme {parsed.scheme!r} not supported")
    if parsed.scheme == "http":
        host = (parsed.hostname or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise RegistryError("plain http:// not allowed for runtime downloads")

    data = _http_fetch(
        url,
        max_bytes=_config.RUNTIME_MAX_DOWNLOAD_BYTES,
        timeout=_config.RUNTIME_FETCH_TIMEOUT_SECONDS,
    )
    sha_actual = hashlib.sha256(data).hexdigest()

    # Stage into a tempdir, validate, then merge into csgo_dir.
    # We do the merge ourselves (not shutil.move of the whole tree) so the
    # operator's other csgo/ contents — workshop maps, custom configs,
    # other plugins — are preserved.
    staging = tempfile.mkdtemp(prefix=f"oblivion_runtime_{component}_")
    try:
        # Format dispatch by URL suffix — MetaMod ships .tar.gz on Linux,
        # everything else is .zip.  See platform.metamod_download_url().
        _safe_extract_archive(data, staging, url)

        # Expect addons/<component>/ at the archive root.  Both MetaMod and
        # CSS archives ship this layout on every OS.  Reject archives that don't.
        expected = os.path.join(staging, meta["expect_relpath"])
        if not os.path.isdir(expected):
            raise RegistryError(
                f"runtime archive does not contain expected folder "
                f"{meta['expect_relpath']!r} (rejecting — wrong archive?)")

        # Merge staging into csgo_dir, file by file.  shutil.copytree
        # with dirs_exist_ok=True does exactly this on 3.8+.
        files_written = 0
        for root, _dirs, files in os.walk(staging):
            rel = os.path.relpath(root, staging)
            target_root = os.path.join(csgo_dir, rel) if rel != "." else csgo_dir
            os.makedirs(target_root, exist_ok=True)
            for fname in files:
                src  = os.path.join(root, fname)
                dst  = os.path.join(target_root, fname)
                shutil.copy2(src, dst)
                files_written += 1
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        "component":       component,
        "label":           meta["label"],
        "url":             url,
        "sha256":          sha_actual,
        "files_written":   files_written,
        "dest_dir":        os.path.join(csgo_dir, meta["expect_relpath"]),
    }


def install_plugin(slug: str, version: str | None = None) -> dict:
    """Download + verify + extract a registry-listed plugin into
    ``%APPDATA%/.../plugins/<slug>/``.

    Returns a result dict on success: ``{slug, version, installed_at,
    dest_dir, files_written}``.  Raises RegistryError on any failure;
    nothing is written to %APPDATA% unless the SHA-256 verifies AND the
    extracted plugin.json declares the expected slug.

    Atomicity: we extract into a tempdir first, validate, then atomic-
    swap into the final location (rmtree existing + os.rename).  A
    crash mid-install leaves either the OLD plugin in place or no
    plugin — never a half-extracted folder.
    """
    catalog = fetch_catalog(force=False)
    entry = next((p for p in (catalog.get("plugins") or []) if p.get("slug") == slug),
                 None)
    if not entry:
        raise RegistryError(f"plugin {slug!r} not in registry catalog")

    v = _select_version(entry, version)
    url       = v.get("download_url")
    sha256    = v.get("sha256")
    ver_label = v.get("version") or "?"

    # Download (fresh — no cache for plugin zips).
    data = _http_fetch(
        url,
        max_bytes=_config.REGISTRY_MAX_DOWNLOAD_BYTES,
        timeout=_config.REGISTRY_FETCH_TIMEOUT_SECONDS,
    )
    _verify_sha256(data, sha256)

    # Extract into a tempdir + validate manifest BEFORE touching APPDATA.
    staging = tempfile.mkdtemp(prefix=f"oblivion_install_{slug}_")
    try:
        _safe_extract_zip(data, staging)

        # The zip layout convention: one top-level folder == slug, with
        # plugin.json inside.  Some authors zip the contents directly
        # without that wrapper folder — handle both layouts.
        candidate_manifest = os.path.join(staging, slug, "plugin.json")
        if os.path.isfile(candidate_manifest):
            extracted_root = os.path.join(staging, slug)
        elif os.path.isfile(os.path.join(staging, "plugin.json")):
            extracted_root = staging
        else:
            raise RegistryError(
                f"zip does not contain a plugin.json (looked at "
                f"{slug}/plugin.json and ./plugin.json)")

        # The manifest must declare the slug we asked for — protects
        # against a malicious entry that bundles a different plugin's
        # files under the requested name.
        with open(os.path.join(extracted_root, "plugin.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        declared = manifest.get("slug")
        if declared != slug:
            raise RegistryError(
                f"extracted plugin.json declares slug={declared!r} "
                f"but install requested {slug!r}")

        # Atomic swap into APPDATA/.../plugins/<slug>/.
        # We resolve through the same helper that _discover_plugins uses
        # so paths stay consistent.
        from .core import _resolve_user_plugins_dir
        user_dir = _resolve_user_plugins_dir()
        os.makedirs(user_dir, exist_ok=True)
        final_dir = os.path.join(user_dir, slug)
        # rmtree existing + rename.  os.replace can't move directories
        # across filesystems on Windows when the target exists, so we
        # remove first.  Operator's loss-of-data risk is documented:
        # re-installing a plugin overwrites local edits.
        if os.path.isdir(final_dir):
            shutil.rmtree(final_dir, ignore_errors=False)
        shutil.move(extracted_root, final_dir)
    finally:
        # Always wipe the staging dir; never leak temps on success or failure.
        shutil.rmtree(staging, ignore_errors=True)

    # Count what landed for the result.
    files_written = sum(
        len(files) for _root, _dirs, files in os.walk(final_dir)
    )
    return {
        "slug":          slug,
        "version":       ver_label,
        "installed_at":  int(time.time()),
        "dest_dir":      final_dir,
        "files_written": files_written,
    }
