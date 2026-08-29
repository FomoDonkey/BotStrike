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
uv pip install -q --python .venv/bin/python -r requirements.txt
git log --oneline -1
"
cp $APP_DIR/deploy/botstrike-bridge.service /etc/systemd/system/botstrike-bridge.service
systemctl daemon-reload
systemctl restart botstrike-bridge
sleep 6
curl -sf localhost:9420/api/health && echo && systemctl --no-pager --lines=5 status botstrike-bridge
