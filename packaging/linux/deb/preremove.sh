#!/bin/sh
# Runs before the package is removed. Stop + disable the service so we don't
# leave a dangling unit or a running daemon after the binary is gone.
set -e

if command -v systemctl >/dev/null 2>&1; then
    systemctl stop oblivion-server-tool >/dev/null 2>&1 || true
    systemctl disable oblivion-server-tool >/dev/null 2>&1 || true
fi

exit 0
