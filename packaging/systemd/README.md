# systemd unit for bare-metal Linux

Run Oblivion Server Tool as a system service on Debian/Ubuntu/Arch/RHEL.
For Docker, use [docker-compose.yml](../../docker-compose.yml) instead.

## Install

```bash
# 1. Create the service user (no shell, no login)
sudo useradd --system --home /opt/oblivion-server-tool \
    --shell /usr/sbin/nologin oblivion

# 2. Clone the repo to /opt/oblivion-server-tool
sudo git clone https://github.com/oblivion-systems/OblivionServerTool.git \
    /opt/oblivion-server-tool
sudo chown -R oblivion:oblivion /opt/oblivion-server-tool

# 3. Install Python deps (system-wide or use a venv — your call)
sudo pip3 install -r /opt/oblivion-server-tool/requirements-headless.txt

# 4. Give the service user write access to the CS2 server dir
sudo mkdir -p /srv/cs2
sudo chown oblivion:oblivion /srv/cs2

# 5. Drop the unit file in place and reload systemd
sudo cp /opt/oblivion-server-tool/packaging/systemd/oblivion-server-tool.service \
    /etc/systemd/system/
sudo systemctl daemon-reload

# 6. Enable + start
sudo systemctl enable --now oblivion-server-tool
```

## Operate

```bash
# Status
sudo systemctl status oblivion-server-tool

# Logs (follow)
sudo journalctl -u oblivion-server-tool -f

# Restart after config changes
sudo systemctl restart oblivion-server-tool
```

Web panel: `http://<host>:5050` (or whatever `flask_port` is set to in
`/home/oblivion/.config/oblivion-server-tool/oblivion_config.json`).

## Notes

- Config path: `~oblivion/.config/oblivion-server-tool/` — edit as the
  `oblivion` user (`sudo -u oblivion vim ...`) so file ownership stays correct.
- CS2 server install dir: defaults to `/srv/cs2` — change in the web UI's
  Config tab after first launch if you want it elsewhere, and update
  `ReadWritePaths=` in the unit file to match.
- The unit ships with hardening (`ProtectSystem=strict`, `ProtectHome=read-only`,
  etc.). If you put the CS2 install somewhere unusual, add that path to
  `ReadWritePaths=` or systemd will block writes.
- No D-Bus on a headless box means `keyring` falls back to plaintext storage
  in `oblivion_config.json` — that's by design and matches the Docker image.
