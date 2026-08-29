#!/usr/bin/env bash
# Post-deploy verification. Run as root INSIDE the container: bash /opt/botstrike/app/deploy/verify.sh
# Exit code != 0 if anything critical is wrong. Prints a compact report.
APP_DIR=/opt/botstrike/app
PORT=${BOTSTRIKE_PORT:-9420}
fail=0
ok()   { echo "  [OK]   $*"; }
bad()  { echo "  [FAIL] $*"; fail=1; }
warn() { echo "  [WARN] $*"; }

echo "== BotStrike verify @ $(hostname) $(date -u +%FT%TZ) =="
echo "-- system"
echo "  os: $(. /etc/os-release && echo "$PRETTY_NAME")  ip: $(hostname -I | awk '{print $1}')  ts: $(tailscale ip -4 2>/dev/null || echo none)"
echo "  mem: $(free -m | awk '/Mem:/{print $3"/"$2" MB"}')  disk: $(df -h / | awk 'NR==2{print $3"/"$2" ("$5")"}')"
if chronyc tracking >/dev/null 2>&1; then
  off=$(chronyc tracking | awk -F': ' '/System time/{print $2}'); ok "chrony synced (offset: $off)"
else warn "chrony not available (LXC usually inherits host clock)"; fi

echo "-- service"
if systemctl is-enabled botstrike-bridge >/dev/null 2>&1; then ok "unit enabled"; else bad "unit NOT enabled"; fi
if systemctl is-active  botstrike-bridge >/dev/null 2>&1; then ok "unit active (since $(systemctl show -p ActiveEnterTimestamp --value botstrike-bridge))"; else bad "unit NOT active"; fi
echo "  restarts: $(systemctl show -p NRestarts --value botstrike-bridge)"

echo "-- code"
cd "$APP_DIR" && echo "  commit: $(git log --oneline -1)  branch: $(git rev-parse --abbrev-ref HEAD)"
[ -f "$APP_DIR/.env" ] && ok ".env present ($(stat -c '%U %a' $APP_DIR/.env))" || bad ".env missing"
grep -q '^BINANCE_API_KEY=.\+' "$APP_DIR/.env" 2>/dev/null && ok "BINANCE_API_KEY set" || warn "BINANCE_API_KEY empty (paper still works with public data)"

echo "-- bridge http"
H=$(curl -sf -m 5 "http://127.0.0.1:$PORT/api/health") && ok "health: $H" || bad "health endpoint unreachable on :$PORT"
S=$(curl -sf -m 5 "http://127.0.0.1:$PORT/api/bot/status" | sed 's/"auth_token": *"[^"]*"/"auth_token":"***"/')
echo "  status: $S"
echo "$H" | grep -q '"engine_running":true' && ok "engine running" || bad "engine NOT running (autostart failed?)"
echo "$S" | grep -q '"mode":"paper"' && ok "mode=paper" || warn "mode is not paper: check BOTSTRIKE_AUTOSTART"

echo "-- data feed (journal, last 5 min)"
J=$(journalctl -u botstrike-bridge --since "-5min" --no-pager 2>/dev/null)
echo "$J" | grep -q "binance_ws_connected" && ok "binance WS connected: $(echo "$J" | grep -o 'streams=[0-9]*' | tail -1)" || warn "no binance_ws_connected in last 5 min (may be older than window)"
errs=$(echo "$J" | grep -ciE "\[error|error_|traceback|exception" ); [ "$errs" -eq 0 ] && ok "0 error lines" || warn "$errs error lines in last 5 min (journalctl -u botstrike-bridge -p err)"
echo "  last: $(echo "$J" | tail -1 | cut -c1-160)"

echo "-- storage"
ls -la "$APP_DIR/data" 2>/dev/null | grep -E "trade_database|klines|binance" | awk '{print "  "$5"\t"$9}'
[ -d "$APP_DIR/logs" ] && ok "logs/ exists ($(du -sh $APP_DIR/logs | cut -f1))" || warn "logs/ missing"

echo "-- firewall"
ufw status 2>/dev/null | head -1 | grep -q active && ok "ufw active: $(ufw status | grep -c ALLOW) allow rules" || warn "ufw inactive"

echo "== RESULT: $([ $fail -eq 0 ] && echo PASS || echo FAIL) =="
exit $fail
