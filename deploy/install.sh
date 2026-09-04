#!/usr/bin/env bash
# One-shot installer for a Debian 13 host/LXC. Run as root inside the container.
# Idempotent: safe to re-run. Assumes the repo is reachable via the deploy key of user `botstrike`.
set -euo pipefail
APP_DIR=/opt/botstrike/app
REPO_SSH=git@github.com:FomoDonkey/BotStrike.git

echo "[1/6] system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates git chrony ufw sqlite3 logrotate >/dev/null
systemctl enable --now chrony >/dev/null 2>&1 || true

echo "[2/6] service user"
id botstrike >/dev/null 2>&1 || useradd -m -s /bin/bash -d /opt/botstrike botstrike

echo "[3/6] uv + python 3.12"
su - botstrike -c 'command -v ~/.local/bin/uv >/dev/null || (curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1); ~/.local/bin/uv python install 3.12 >/dev/null 2>&1 || true'

echo "[4/6] code + venv"
su - botstrike -c "
set -e
export PATH=\$HOME/.local/bin:\$PATH
[ -d $APP_DIR/.git ] || git clone -q $REPO_SSH $APP_DIR
cd $APP_DIR
[ -d .venv ] || uv venv --python 3.12 .venv -q
uv pip install -q --python .venv/bin/python -r requirements.txt
mkdir -p data logs
"

echo "[5/6] .env"
if [ ! -f $APP_DIR/.env ]; then
  cp $APP_DIR/.env.example $APP_DIR/.env
  echo "  >> Edit $APP_DIR/.env with your API keys (Binance read-only keys are enough for paper)."
fi
chown botstrike:botstrike $APP_DIR/.env && chmod 600 $APP_DIR/.env

echo "[6/6] systemd + firewall + logrotate"
cp $APP_DIR/deploy/logrotate-botstrike /etc/logrotate.d/botstrike && chmod 644 /etc/logrotate.d/botstrike
cp $APP_DIR/deploy/botstrike-bridge.service /etc/systemd/system/botstrike-bridge.service
# Ops monitor (scripts/ops_monitor.py): every 15 min, Telegram alerts + daily summary. Reads the journal.
cp $APP_DIR/deploy/botstrike-monitor.service /etc/systemd/system/botstrike-monitor.service
cp $APP_DIR/deploy/botstrike-monitor.timer /etc/systemd/system/botstrike-monitor.timer
usermod -aG systemd-journal botstrike
# Cap the journal. Measured 2026-09-04: 472 MB of journal against 88 MB of market data and 57 MB of
# app logs — by far the biggest consumer, and uncapped it drifts to systemd's default ceiling of 10 %
# of the filesystem (2 GB here). 300 MB is weeks of history for a bot that logs a line every 15 s.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/botstrike.conf <<'JCONF'
[Journal]
SystemMaxUse=300M
SystemKeepFree=1G
MaxRetentionSec=30day
JCONF
systemctl restart systemd-journald || true
systemctl daemon-reload
systemctl enable --now botstrike-bridge
systemctl enable --now botstrike-monitor.timer
# Firewall: bridge reachable ONLY from LAN + Tailscale. Nothing else inbound.
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow from 192.168.1.0/24 to any port 9420 proto tcp >/dev/null
ufw allow from 100.64.0.0/10 to any port 9420 proto tcp >/dev/null
ufw allow from 192.168.1.0/24 to any port 22 proto tcp >/dev/null
ufw allow from 100.64.0.0/10 to any port 22 proto tcp >/dev/null
ufw --force enable >/dev/null
echo "Done. Check: systemctl status botstrike-bridge; curl -s localhost:9420/api/health"
