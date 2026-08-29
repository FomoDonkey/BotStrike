#!/usr/bin/env bash
# Runs ON THE DEV PC (Git Bash). One command to deploy the pushed `main` to CT 104 and verify.
#   bash deploy/remote_deploy.sh                 # via Tailscale (default)
#   HOST=root@192.168.1.200 bash deploy/remote_deploy.sh   # via LAN
# Requires: git push origin main done; SSH access to the Proxmox host.
set -euo pipefail
HOST=${HOST:-root@100.68.139.93}
cd "$(dirname "$0")/.."
if [ -n "$(git status --porcelain | grep -v '^??')" ]; then echo "!! uncommitted changes — commit & push first"; exit 1; fi
if [ "$(git rev-parse main)" != "$(git rev-parse origin/main 2>/dev/null)" ]; then echo "!! local main != origin/main — push first"; exit 1; fi
echo "deploying $(git log --oneline -1) via $HOST"
tr -d '\r' < deploy/host_deploy.sh | ssh -o ConnectTimeout=15 "$HOST" 'bash -s'
