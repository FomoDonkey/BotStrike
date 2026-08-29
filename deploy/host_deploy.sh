#!/usr/bin/env bash
# Runs ON THE PROXMOX HOST (root). Deploys latest `main` into CT 104 and verifies.
# Usage (from host):  bash host_deploy.sh          (or piped: ssh root@host 'bash -s' < host_deploy.sh)
set -euo pipefail
CT=${CT:-104}
APP=/opt/botstrike/app
echo "== host: $(hostname) == CT $CT =="
pct status "$CT" | grep -q running || { echo "starting CT $CT"; pct start "$CT"; sleep 5; }
echo "-- CT hostname: $(pct exec "$CT" -- hostname)"
echo "-- [1/3] update code + deps + restart"
pct exec "$CT" -- bash "$APP/deploy/update.sh"
echo "-- [2/3] ensure unit enabled + firewall (idempotent install)"
pct exec "$CT" -- bash "$APP/deploy/install.sh"
echo "-- [3/3] verify (waits 25s for engine autostart + WS warmup)"
sleep 25
pct exec "$CT" -- bash "$APP/deploy/verify.sh"
