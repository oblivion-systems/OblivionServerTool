# Adding plugins to Oblivion Server Tool

The Plugin Manager scans two locations on every app start:

1. **Bundled** — `cs2servergui/plugins/<slug>/` (ships inside the .exe)
2. **Local** — `%APPDATA%\Oblivion Server Tool\plugins\<slug>\` (per-operator install)

A plugin folder is anything containing a valid `plugin.json` manifest. Drop the folder, restart the app, and it appears in the **Plugins** tab's Library with a **Local** badge.

Local plugins **override** bundled ones if the slug matches — handy for patching a built-in without recompiling.

---

## Folder layout

```
%APPDATA%\Oblivion Server Tool\plugins\my-admin-tool\
├── plugin.json                       (REQUIRED — manifest)
├── addons/                           (mirrors csgo/addons/)
│   └── counterstrikesharp/
│       └── plugins/
│           └── MyAdminTool/
│               ├── MyAdminTool.dll
│               └── MyAdminTool.deps.json
└── cfg/                              (optional — mirrors csgo/cfg/)
    └── my-admin-tool/
        └── config.cfg
```

The top-level folder name **must equal the slug declared in `plugin.json`** — the loader rejects mismatches loudly.

---

## `plugin.json` schema

```json
{
  "schema_version": 1,
  "slug": "my-admin-tool",
  "display_name": "My Admin Tool",
  "summary": "Short one-line description shown on the SPA card.",
  "author": "your name or handle",
  "kind": "css",
  "load_order": 20,
  "modes": ["Competitive", "5v5"],
  "copy_rules": [
    { "src": "addons", "dst": "addons" },
    { "src": "cfg",    "dst": "cfg" }
  ],
  "verify_files": [
    "addons/counterstrikesharp/plugins/MyAdminTool/MyAdminTool.dll"
  ],
  "cleanup": [
    "addons/counterstrikesharp/plugins/MyAdminTool",
    "cfg/my-admin-tool"
  ]
}
```

### Required fields

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | Must be `1`. Bumped when the schema breaks compatibility. |
| `slug` | string | Lower-snake-case. Must equal the folder name. |
| `display_name` | string | Shown in the Library card title. |
| `kind` | `"css"` or `"metamod"` | `metamod` plugins load at engine init; `css` plugins are hot-reloadable. |
| `modes` | string[] | Which modes deploy this plugin. Use existing modes from CS2 (e.g. `"5v5"`, `"Warcraft"`, `"Deathmatch"`). |
| `copy_rules` | object[] | What to copy from the plugin folder into `csgo/`. See below. |

### Optional fields

| Field | Default | Notes |
|---|---|---|
| `summary` | `""` | Subtitle on the card. Keep under ~80 chars. |
| `author` | `""` | Attribution shown beneath the card. |
| `load_order` | `20` | Lower = earlier. Use `10` for metamod plugins that must load first, `15` for metamod overlays (like `zombie_ze`), `20` for css plugins. |
| `verify_files` | `[]` | Paths (relative to `csgo/`) that MUST exist after a deploy. Used by the post-deploy verifier — missing files trigger a warning in the log. |
| `cleanup` | `[]` | Paths (relative to `csgo/`) to remove on undeploy or mode switch. Directories are recursively deleted; files are unlinked. Use forward slashes. |

### `copy_rules` entry

```json
{ "src": "addons", "dst": "addons" }
```

- `src` — subdirectory of YOUR plugin folder
- `dst` — destination relative to `csgo/`
- `exclude` (optional) — array of subdirectory names to skip during the walk

Each rule copies the **contents** of `src` into `dst`, merging with anything already there.

---

## Example: a minimal CSS plugin

```
%APPDATA%\Oblivion Server Tool\plugins\hello-world\
├── plugin.json
└── addons/
    └── counterstrikesharp/
        └── plugins/
            └── HelloWorld/
                └── HelloWorld.dll
```

```json
{
  "schema_version": 1,
  "slug": "hello-world",
  "display_name": "Hello World",
  "summary": "Says hi in chat.",
  "author": "you",
  "kind": "css",
  "modes": ["Competitive", "Casual"],
  "copy_rules": [
    { "src": "addons", "dst": "addons" }
  ],
  "verify_files": [
    "addons/counterstrikesharp/plugins/HelloWorld/HelloWorld.dll"
  ],
  "cleanup": [
    "addons/counterstrikesharp/plugins/HelloWorld"
  ]
}
```

Drop the folder, restart Oblivion Server Tool, open the **Plugins** tab → your plugin appears as **Hello World** with a **Local** badge. Click **Activate (Competitive)** to deploy it.

---

## Debugging

- **Plugin doesn't appear in the Library** — check the Oblivion Server Tool console. The loader prints `[plugins] Failed to load ...` for any manifest it rejects, with the specific reason (bad JSON, missing required field, slug/folder mismatch, wrong schema_version).
- **Deploy succeeds but plugin doesn't load in-game** — check `csgo/addons/counterstrikesharp/logs/log-all*.txt` for CSS load errors. Most common cause: the plugin's `.deps.json` references DLLs the CSS host doesn't ship — those need to live in your plugin folder alongside the main DLL.
- **`cleanup` missed something on mode switch** — list every path your plugin owns under `csgo/`. The cleanup is exact-path: `cfg/my-tool` won't delete `cfg/my-tool-v2`. Wildcards are not supported in this slice.

---

## Sharing a plugin with other operators

For now: zip up the folder (the `plugin.json` + the `addons/` and `cfg/` subdirs) and share the zip. The recipient drops it into their `%APPDATA%\Oblivion Server Tool\plugins\` and restarts.

A future slice will add an in-app **Browse Community Plugins** tab fetching from a separate `OblivionPluginRegistry` repo, with one-click install. Until that ships, manual distribution is the path.
