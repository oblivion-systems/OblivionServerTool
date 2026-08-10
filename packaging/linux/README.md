# Linux packaging — AppImage + `.deb`

Three ways to run Oblivion on Linux, one binary underneath:

| Shape | Who it's for | Ships via |
|---|---|---|
| **Docker image** | Self-hosters already running a container stack | `docker-compose.yml` → GHCR (v1.1) |
| **`.deb`** | Debian/Ubuntu server operators who want a native systemd service | `packaging/linux/nfpm.yaml` |
| **AppImage** | Any distro (Fedora, Arch, …) — one portable file, no root, no deps | `packaging/linux/AppRun` + appimagetool |

All three wrap the **same PyInstaller onefile** (`dist/oblivion-server-tool`),
a self-contained headless binary that bundles its own Python runtime, deps,
plugins, and static assets. It runs **headless** — the GTK desktop window
needs system WebKitGTK that can't be frozen portably, so desktop-window users
run from source (see the README's *Linux desktop window* note). Servers don't
want a window anyway.

## Build locally

Run on **Ubuntu 22.04** (the onefile's build-host glibc becomes its minimum
runtime glibc; 22.04 gives broad compatibility and matches the Docker base).

```bash
packaging/linux/build.sh all        # binary + AppImage + .deb
packaging/linux/build.sh binary     # just the onefile
packaging/linux/build.sh appimage   # just the .AppImage
packaging/linux/build.sh deb        # just the .deb
```

Outputs land in `dist/`:

- `oblivion-server-tool` — the onefile
- `Oblivion_Server_Tool-<version>-x86_64.AppImage`
- `oblivion-server-tool_<version>_amd64.deb`

`appimagetool` and `nfpm` are fetched to `packaging/linux/.build-tools/` if
they're not already on `PATH` (gitignored). The version is read from
`cs2servergui/config.py:APP_VERSION` — bump that before tagging a release.

## Install targets

### `.deb`

```bash
sudo apt install ./oblivion-server-tool_<version>_amd64.deb
sudo systemctl enable --now oblivion-server-tool
# → http://<host>:5050  (set your admin PIN on first load)
```

Lays down `/usr/bin/oblivion-server-tool`, a systemd unit at
`/lib/systemd/system/oblivion-server-tool.service`, and a service user
`oblivion` with config/state in `/var/lib/oblivion-server-tool`. Purge
(`apt purge`) removes the user + state but **leaves `/srv/cs2`** (your CS2
install) intact.

To actually host CS2 you also need the 32-bit runtime (a `recommends`, so apt
won't pull it automatically):

```bash
sudo dpkg --add-architecture i386 && sudo apt-get update
sudo apt-get install -y lib32gcc-s1 libstdc++6:i386
```

### AppImage

```bash
chmod +x Oblivion_Server_Tool-<version>-x86_64.AppImage
./Oblivion_Server_Tool-<version>-x86_64.AppImage      # prints the panel URL
```

No install, no root. Config lands under `$XDG_CONFIG_HOME` / `~/.config`.
For an always-on service, prefer the `.deb` (proper systemd integration).

## CI

[`.github/workflows/release-linux.yml`](../../.github/workflows/release-linux.yml)
builds both on every `v*.*.*` tag and attaches them to that tag's GitHub
release — alongside the Docker image from `docker-publish.yml`.

## Files

| File | Role |
|---|---|
| `oblivion-server-tool.spec` | PyInstaller onefile spec (Linux/headless) |
| `AppRun`, `oblivion-server-tool.desktop` | AppImage entry point + menu entry |
| `nfpm.yaml` | `.deb` package definition (also builds rpm/apk unchanged) |
| `deb/oblivion-server-tool.service` | systemd unit for the installed binary |
| `deb/{postinstall,preremove,postremove}.sh` | user + service lifecycle |
| `build.sh` | orchestrator for all three artifacts |

> The from-source systemd unit in [`../systemd/`](../systemd/) is a **separate**
> unit that runs `python3 main.py` from `/opt` — for operators who clone the
> repo instead of installing the `.deb`. Don't confuse the two.
