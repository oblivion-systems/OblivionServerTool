"""
cs2servergui/template_store.py — Persistent tournament templates (task #169).

Operators running recurring tournaments save a complete bundle under a name:
  - mode (e.g. "5v5")
  - map (e.g. "de_dust2")
  - plugin pack id (one of _PLUGIN_PACKS — Competitive 5v5, Warcraft Night, ...)
  - Discord channel IDs (veto embed + per-team voice)
  - auto-move + round-summaries toggles
  - optional team profile IDs from team_profiles.py for one-click roster load

Click Apply → everything stages.  "Friday Night Pugs", "Saturday Warcraft Night",
"Sunday Practice" become single-click selections.

Persistence mirrors team_profiles.py: atomic JSON file in %APPDATA% via
tmp + os.replace; stable UUIDs per entry; created_at / updated_at metadata.
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
        with open(_config.TEMPLATES_FILE, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"[templates] read failed {_config.TEMPLATES_FILE!r}: {exc!r}",
              file=sys.stderr)
        return []
    if not isinstance(doc, list):
        return []
    return [e for e in doc if isinstance(e, dict) and e.get("id")]


def _write_all(templates: list[dict]) -> None:
    path = _config.TEMPLATES_FILE
    dirpath = os.path.dirname(path)
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="oblivion_templates_", suffix=".tmp",
                                dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(templates, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# Fields that survive a round-trip through save_template; anything not in this
# allowlist is stripped so an operator-supplied payload can't smuggle arbitrary
# keys into the persisted file (defence-in-depth — caller is local-only).
_PERSISTED_FIELDS = (
    "mode", "map", "pack_id",
    "discord_veto_channel_id",
    "discord_team_a_voice_channel_id",
    "discord_team_b_voice_channel_id",
    "discord_auto_move_on_distribute_enabled",
    "discord_round_summaries_enabled",
    "team_a_id", "team_b_id",
    "description",
)


def list_templates() -> list[dict]:
    return _read_all()


def get_template(template_id: str) -> dict | None:
    for t in _read_all():
        if t.get("id") == template_id:
            return t
    return None


def save_template(*, template_id: str | None, name: str, payload: dict) -> dict:
    """Create (template_id=None) or update (template_id set) a template.
    Filters the payload through _PERSISTED_FIELDS allowlist.  Returns the
    persisted record.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("template name is required")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    # Normalise: keep only allowlisted fields, strip whitespace on string values.
    filtered: dict = {}
    for k in _PERSISTED_FIELDS:
        v = payload.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
        filtered[k] = v

    templates = _read_all()
    now = int(time.time())
    if template_id:
        for i, t in enumerate(templates):
            if t.get("id") == template_id:
                templates[i] = {
                    "id":         template_id,
                    "name":       name,
                    "payload":    filtered,
                    "created_at": t.get("created_at") or now,
                    "updated_at": now,
                }
                _write_all(templates)
                return templates[i]
        raise ValueError(f"template {template_id!r} not found")
    new_entry = {
        "id":         uuid.uuid4().hex,
        "name":       name,
        "payload":    filtered,
        "created_at": now,
        "updated_at": now,
    }
    templates.append(new_entry)
    _write_all(templates)
    return new_entry


def delete_template(template_id: str) -> bool:
    templates = _read_all()
    out = [t for t in templates if t.get("id") != template_id]
    if len(out) == len(templates):
        return False
    _write_all(out)
    return True
