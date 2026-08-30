#!/usr/bin/env bash
# Pull latest main, refresh deps, restart the service, and verify health. Run as root.
set -euo pipefail
APP_DIR=/opt/botstrike/app
su - botstrike -c "
set -e
export PATH=\$HOME/.local/bin:\$PATH
cd $APP_DIR
git fetch -q origin main
git reset -q --hard origin/main
# Reproducible install: prefer the pinned lock (generated in the CT with
#   uv pip compile requirements.txt -o requirements.lock); fall back to the floating file.
if [ -f requirements.lock ]; then
  uv pip sync -q --python .venv/bin/python requirements.lock
else
  uv pip install -q --python .venv/bin/python -r requirements.txt
fi
git log --oneline -1
"
cp $APP_DIR/deploy/botstrike-bridge.service /etc/systemd/system/botstrike-bridge.service
systemctl daemon-reload
systemctl restart botstrike-bridge
sleep 6
curl -sf localhost:9420/api/health && echo && systemctl --no-pager --lines=5 status botstrike-bridge
