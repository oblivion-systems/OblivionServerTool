"""
cs2servergui/team_profiles.py — Persistent team rosters (task #160).

Operators running recurring tournaments save rosters under a name (e.g.
"Cobras") + reuse across sessions instead of re-pasting 10 SteamIDs every
week.  Same persistence pattern as MATCH_HISTORY_FILE — JSON file in
%APPDATA%, atomic writes via tmp + os.replace.

Schema (oblivion_teams.json):
    [
      {
        "id":         "<uuid>",
        "name":       "Cobras",
        "tag":        "COB",          (optional, displayed on roster card)
        "players":    [
            {"name": "...", "steam_id": "...", "discord_id": "..."},
            ...
        ],
        "created_at": <unix>,
        "updated_at": <unix>
      },
      ...
    ]

Players list is order-preserving; the operator's row order is kept.  Each
team is identified by a stable UUID so renaming is non-destructive
(history records by id).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid

from . import config as _config


def _read_all() -> list[dict]:
    try:
        with open(_config.TEAMS_FILE, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"[teams] read failed {_config.TEAMS_FILE!r}: {exc!r}",
              file=sys.stderr)
        return []
    if not isinstance(doc, list):
        return []
    out: list[dict] = []
    for entry in doc:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        out.append(entry)
    return out


def _write_all(teams: list[dict]) -> None:
    """Atomic write via tmp + os.replace — same pattern as save_config."""
    path = _config.TEAMS_FILE
    dirpath = os.path.dirname(path)
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="oblivion_teams_", suffix=".tmp",
                                dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(teams, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def list_teams() -> list[dict]:
    """Return the teams as they're stored — caller can filter/order in JS."""
    return _read_all()


def get_team(team_id: str) -> dict | None:
    for t in _read_all():
        if t.get("id") == team_id:
            return t
    return None


def save_team(*, team_id: str | None, name: str, tag: str,
              players: list[dict]) -> dict:
    """Create (team_id=None) or update (team_id set) a team.  Returns the
    persisted record.  Players are validated/normalised:
        - name (string, required)
        - steam_id (string, optional)
        - discord_id (string, optional — digits only when present)
    Raises ValueError on bad input."""
    name = (name or "").strip()
    if not name:
        raise ValueError("team name is required")
    tag = (tag or "").strip()
    if not isinstance(players, list):
        raise ValueError("players must be a list")
    normalised: list[dict] = []
    for p in players:
        if not isinstance(p, dict):
            raise ValueError("each player must be an object")
        pn = (p.get("name") or "").strip()
        ps = (p.get("steam_id") or "").strip()
        pd = (p.get("discord_id") or "").strip()
        if not pn:
            raise ValueError("each player needs a name")
        if pd and not pd.isdigit():
            raise ValueError(f"discord_id {pd!r} must be digits only")
        normalised.append({"name": pn, "steam_id": ps, "discord_id": pd})

    teams = _read_all()
    now = int(time.time())
    if team_id:
        for i, t in enumerate(teams):
            if t.get("id") == team_id:
                teams[i] = {
                    "id":         team_id,
                    "name":       name,
                    "tag":        tag,
                    "players":    normalised,
                    "created_at": t.get("created_at") or now,
                    "updated_at": now,
                }
                _write_all(teams)
                return teams[i]
        raise ValueError(f"team {team_id!r} not found")
    # Create new
    new_team = {
        "id":         uuid.uuid4().hex,
        "name":       name,
        "tag":        tag,
        "players":    normalised,
        "created_at": now,
        "updated_at": now,
    }
    teams.append(new_team)
    _write_all(teams)
    return new_team


def delete_team(team_id: str) -> bool:
    """Returns True if a team was removed."""
    teams = _read_all()
    out = [t for t in teams if t.get("id") != team_id]
    if len(out) == len(teams):
        return False
    _write_all(out)
    return True
