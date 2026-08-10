#!/bin/sh
# Runs after the package is removed. Reload systemd; on a full purge, drop the
# state dir + service user. /srv/cs2 (the ~15 GB CS2 install) is left intact —
# that's operator data, not ours to delete.
set -e

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || true
fi

if [ "$1" = "purge" ]; then
    rm -rf /var/lib/oblivion-server-tool
    if getent passwd oblivion >/dev/null 2>&1; then
        deluser --system oblivion >/dev/null 2>&1 || true
    fi
    echo "Purged Oblivion config/state. /srv/cs2 (CS2 install) left untouched."
fi

exit 0
