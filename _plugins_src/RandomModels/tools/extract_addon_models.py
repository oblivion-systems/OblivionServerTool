#!/usr/bin/env python3
"""
extract_addon_models.py — turn a downloaded Steam Workshop addon into
ready-to-paste RandomModels config.

The fiddly bottleneck in custom-model setup is finding each addon's internal
.vmdl paths.  Once MultiAddonManager (or DepotDownloader) has downloaded an
addon, its content lives under:

    <cs2>/game/csgo/../../steamapps/workshop/content/730/<addon_id>/

as one or more .vpk files.  This script opens those vpks, lists the player
model paths, and prints:

  1. The .vmdl paths — paste into RandomModels.json's "Models".
  2. A MultiAddonManager `mm_extra_addons` line with the addon IDs.

Usage:
    python extract_addon_models.py <workshop_content_dir> <id> [<id> ...]

    # example (this machine's server install):
    python extract_addon_models.py \
        "D:/steamcmd/steamapps/common/Counter-Strike Global Offensive/steamapps/workshop/content/730" \
        3157463861

Requires: pip install vpk
"""
from __future__ import annotations

import os
import sys

try:
    import vpk
except ImportError:
    sys.exit("Missing dependency — run:  pip install vpk")


# Heuristic: paths that look like player/character models.  Custom addons vary,
# so we surface anything under characters/models or models/player, plus any
# .vmdl whose name hints at a player model.  Review the output before using.
def _looks_like_player_model(path: str) -> bool:
    p = path.lower().replace("\\", "/")
    if not p.endswith(".vmdl"):
        return False
    if p.startswith("characters/models/"):
        return True
    if "/player/" in p or p.startswith("models/player"):
        return True
    # common custom-model roots
    if any(k in p for k in ("playermodel", "player_model", "/models/agents/")):
        return True
    return False


def _vmdl_paths_in_vpk(vpk_path: str) -> list[str]:
    out: list[str] = []
    try:
        pak = vpk.open(vpk_path)
    except Exception as exc:
        print(f"  ! could not open {vpk_path}: {exc}", file=sys.stderr)
        return out
    for entry in pak:
        p = entry.replace("\\", "/")
        # CS2 stores compiled models as .vmdl_c; the engine's SetModel wants .vmdl
        if p.endswith(".vmdl_c"):
            p = p[:-2]
        if p.endswith(".vmdl") and _looks_like_player_model(p):
            out.append(p)
    return out


def _scan_addon(content_dir: str, addon_id: str) -> list[str]:
    addon_dir = os.path.join(content_dir, addon_id)
    if not os.path.isdir(addon_dir):
        print(f"  ! addon {addon_id} not downloaded yet at {addon_dir}",
              file=sys.stderr)
        return []
    models: set[str] = set()
    for root, _dirs, files in os.walk(addon_dir):
        for f in files:
            if f.endswith("_dir.vpk") or (f.endswith(".vpk") and "_" not in f):
                models.update(_vmdl_paths_in_vpk(os.path.join(root, f)))
    return sorted(models)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    content_dir = sys.argv[1]
    ids = sys.argv[2:]

    all_models: list[str] = []
    for addon_id in ids:
        print(f"# addon {addon_id}")
        found = _scan_addon(content_dir, addon_id)
        if not found:
            print("#   (no player-model .vmdl found — check the addon, or widen "
                  "the heuristic in _looks_like_player_model)")
        for m in found:
            print(f'    "{m}",')
            all_models.append(m)
        print()

    print("=" * 70)
    print("Paste the quoted paths above into RandomModels.json -> \"Models\".")
    print()
    print("MultiAddonManager line (multiaddonmanager.cfg):")
    print(f'    mm_extra_addons "{",".join(ids)}"')
    print()
    print(f"Total player models found: {len(all_models)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
