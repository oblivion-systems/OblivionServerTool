# RandomModels

A CounterStrikeSharp plugin that gives **every player a random character
model at the start of each round** — server-side, so everyone sees it, no
per-client install.

## How it works

- On `OnServerPrecacheResources` it precaches every model in the pool (without
  this the engine renders the error/default model).
- On round start it rolls one model per player and remembers it.
- On (re)spawn it applies that model after a short delay, so the engine's own
  default-agent assignment on spawn doesn't clobber it.

Same `SetModel` mechanism the WarcraftPlugin "Barbarian" class uses.

## Requirements

- MetaMod:Source + CounterStrikeSharp (built against **CSS 1.0.368**).
- The server must be running a **CSS-active mode**.  In vanilla Competitive
  the Oblivion tool restores `gameinfo.gi` (no MetaMod search path), which
  disables ALL CSS plugins.  Use a mode that keeps CSS loaded — **MatchZy /
  Practice / Warcraft / Deathmatch** etc.

## Config

`addons/counterstrikesharp/configs/plugins/RandomModels/RandomModels.json`

| Key | Meaning |
|---|---|
| `Models` | Pool of `.vmdl` paths.  Each player gets a random one per round. |
| `IncludeBots` | Apply to bots too (default `true`) — handy for solo testing. |
| `ApplyDelaySeconds` | Delay after spawn before applying (default `0.10`). |

The default pool is **14 Valve agent models** verified present in `pak01.vpk`
— every client already has them, zero downloads.

## Using custom (cartoon) models

1. Publish/find the models as a **Steam Workshop content addon** (models must
   be rigged to the CS2 player skeleton or animations break).
2. Force-mount the addon on join via **MultiAddonManager** (add the workshop
   ID to its config).
3. Add the addon's internal `.vmdl` paths to `Models` here.

That's the only change — the plugin is identical for Valve agents and custom
models; it just reads a different pool.

## Build

```bash
dotnet build -c Release
# → bin/Release/net8.0/RandomModels.dll
# Override the CSS API path if needed:
dotnet build -c Release /p:CssApi="D:\path\to\counterstrikesharp\api"
```

Deploy `RandomModels.dll` to
`addons/counterstrikesharp/plugins/RandomModels/`.
