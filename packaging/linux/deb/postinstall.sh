#!/bin/sh
# Runs after the .deb unpacks. Create the service user + state/data dirs, then
# reload systemd. Idempotent — safe on upgrade as well as first install.
set -e

# System group + user (no login, no home creation — home is the state dir).
if ! getent group oblivion >/dev/null 2>&1; then
    addgroup --system oblivion
fi
if ! getent passwd oblivion >/dev/null 2>&1; then
    adduser --system --ingroup oblivion \
        --home /var/lib/oblivion-server-tool --no-create-home \
        --disabled-login --gecos "Oblivion Server Tool" oblivion
fi

# Config/state dir (nfpm created it; re-assert ownership after user exists).
mkdir -p /var/lib/oblivion-server-tool
chown oblivion:oblivion /var/lib/oblivion-server-tool
chmod 0750 /var/lib/oblivion-server-tool

# CS2 install target. The operator sets server_dir=/srv/cs2 in the panel;
# pre-create it owned by the service user so the first install can write.
mkdir -p /srv/cs2
chown oblivion:oblivion /srv/cs2

# Pick up the new unit.
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
fi

cat <<'EOF'

Oblivion Server Tool installed.

  Enable + start:   sudo systemctl enable --now oblivion-server-tool
  Then open:        http://<this-host>:5050   (set your admin PIN on first load)
  Logs:             journalctl -u oblivion-server-tool -f

To host CS2 you also need the 32-bit runtime:
  sudo dpkg --add-architecture i386 && sudo apt-get update
  sudo apt-get install -y lib32gcc-s1 libstdc++6:i386

EOF

exit 0
