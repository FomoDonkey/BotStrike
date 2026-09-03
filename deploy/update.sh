#!/usr/bin/env bash
# Pull latest main, refresh deps, RUN THE TEST SUITE, and only then restart + verify. Run as root.
#
# The test gate (audit R2 security_supply-02) exists because the deployed dependency set is NOT the
# one developers run locally (CT: pandas 3.0.5 / starlette 1.6 / fastapi 0.141). Until 2026-08-31 the
# suite could not even be COLLECTED in the CT (starlette >= 1.0 needs httpx2 for TestClient), so
# "100/100 tests" covered nothing that actually runs here. A trading bot must never be restarted
# onto code that fails its own tests: on failure this script aborts and LEAVES THE OLD PROCESS
# RUNNING (the working tree is already updated, so re-run after fixing).
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
# Test-only deps AFTER the sync: 'uv pip sync' prunes anything absent from the lock.
[ -f requirements-dev.txt ] && uv pip install -q --python .venv/bin/python -r requirements-dev.txt
git log --oneline -1
"

echo '-- test gate (suite must pass on the DEPLOYED dependency set)'
if ! su - botstrike -c "cd $APP_DIR && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider" ; then
  echo '!! TESTS FAILED — service NOT restarted; the previous process keeps running.'
  echo '   Fix, push, and re-run deploy/update.sh.'
  exit 1
fi

cp $APP_DIR/deploy/botstrike-bridge.service /etc/systemd/system/botstrike-bridge.service
systemctl daemon-reload
# Tell the ops monitor these restarts are planned, so a deploy cannot look like a crash loop.
# git must run as botstrike inside the repo: as root, outside it, it returned an empty commit.
MAINT_COMMIT=$(su - botstrike -c "cd $APP_DIR && git rev-parse --short HEAD" 2>/dev/null || echo unknown)
su - botstrike -c "cd $APP_DIR && printf '{\"ts\": \"%s\", \"commit\": \"%s\"}'   \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" \"$MAINT_COMMIT\" > data/maintenance.json"
systemctl restart botstrike-bridge
sleep 6
curl -sf localhost:9420/api/health && echo && systemctl --no-pager --lines=5 status botstrike-bridge
