# tests/

Behavioural test batteries for v0.9.2+.  Each battery isolates the changes from a release into one file that runs both as a standalone script (no pytest needed) AND under pytest (one case per behaviour).

## Running

```bash
# Standalone — exit code 0/1, prints a [+]/[X] line per test
python tests/test_v092.py

# pytest — one named case per behaviour, with -v for verbose output
pytest tests/test_v092.py -v
```

Either invocation isolates config writes to a fresh `tempfile.mkdtemp()` so the real `oblivion_config.json` is never touched (per the rule in `~/.claude/projects/.../MEMORY.md`).

## What each file covers

| File | Release | Scope |
|---|---|---|
| `test_v092.py` | v0.9.2 | RCON multi-packet sentinel, execute_retry exception widening, broadcast `;` injection block, log_save filename uniqueness, Event.wait crash-backoff cancel, _lan_ip cache TTL, _STEAMID_RE cap, save_config atomicity, _lifecycle_lock RLock reentrancy, _netutils, Flask route auth + 409 |

## Adding tests

The script uses a `t(name, callable)` pattern where the callable returns `(ok: bool, detail: str)`.  Both forms work:

```python
def my_test():
    return some_thing == expected, f'got: {some_thing!r}'

t('Description shown in pytest + the standalone log', my_test)
```

At the bottom of the file, every `t(...)` call gets auto-wrapped into a `def test_*` function that pytest can discover.  The slug is generated from the description so collisions are extremely unlikely; if two descriptions happen to slug-collide, the second gets a `_2` suffix.

## What these tests don't cover

- Anything that needs a running CS2 dedicated server (workshop map switch, crash recovery, RCON against real cs2.exe — those are play-test items)
- pywebview window lifecycle (no headless mode for Edge WebView2)
- Long-running thread interactions beyond the localised hammer tests
- DotNet plugin behaviour (the Warcraft DLL would need a CS2 + CSS load to exercise)
