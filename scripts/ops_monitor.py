"""BotStrike ops monitor — runs INSIDE the CT every 15 min (systemd timer, user botstrike).

Watches what a human operator would otherwise have to check by hand and reports by Telegram:
  * bridge health (engine, WebSocket feed, tick age, Telegram failures)
  * the daily TREND_DAILY run (must be OK for today after 00:20 UTC)
  * risk halts (circuit breaker, drawdown halt, killed strategies)
  * journal errors / tracebacks in the last window, regime-change flood
  * once a day (first run after 00:20 UTC): a summary (equity, PnL, positions, run, flips, errors)
Alerts are de-duplicated (same key at most every ALERT_REPEAT_SEC) via data/ops_monitor_state.json.
The last evaluation is written to data/ops_monitor_last.json for the bridge/UI.

Pure logic lives in `evaluate()` so it is unit-testable without a bridge. Never raises: a monitor
that crashes is worse than no monitor. Exit code is always 0.
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

BRIDGE = os.getenv("BOTSTRIKE_MONITOR_BRIDGE", "http://127.0.0.1:9420")
APP_DIR = os.getenv("BOTSTRIKE_APP_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_PATH = os.path.join(APP_DIR, "data", "ops_monitor_state.json")
LAST_PATH = os.path.join(APP_DIR, "data", "ops_monitor_last.json")
WINDOW_MIN = int(os.getenv("BOTSTRIKE_MONITOR_WINDOW_MIN", "15"))
ALERT_REPEAT_SEC = 6 * 3600
TREND_DEADLINE_MIN = 20          # the run is scheduled 00:05 UTC; alert if not OK by 00:20
MAX_TICK_AGE_SEC = 120.0
MAX_REGIME_FLIPS_PER_HOUR = 8    # after the 15-min/30-min fix we measure 1-2/h in total
SERVICE = "botstrike-bridge"


@dataclass
class Report:
    alerts: List[Dict[str, str]] = field(default_factory=list)   # {key, text}
    summary: Optional[str] = None
    facts: Dict = field(default_factory=dict)


# ── data access ──────────────────────────────────────────────────

def get_json(path: str, timeout: float = 8.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(BRIDGE + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def journal_stats(since_min: int) -> Dict:
    """Counts from journalctl for the last `since_min` minutes (needs systemd-journal group)."""
    try:
        out = subprocess.run(
            ["journalctl", "-u", SERVICE, "--since", f"-{since_min}min", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    lines = out.splitlines()
    errors = [l for l in lines if "Traceback (most recent call last)" in l or "[error" in l.lower()]
    first_error = ""
    for l in lines:
        if "Error" in l and "Traceback" not in l and "[error" not in l.lower():
            first_error = l.strip()[:160]
            break
    return {
        "available": True,
        "errors": len(errors),
        "first_error": first_error,
        # startup transitions (old=UNKNOWN) are not flips: 4 per restart would look like a flood
        "regime_changed": sum(1 for l in lines if "regime_changed" in l and "old=UNKNOWN" not in l),
        "telegram_sent": sum(1 for l in lines if "telegram_sent" in l),
        "telegram_failed": sum(1 for l in lines if "telegram_send_failed" in l or "telegram_message_lost" in l),
        "restarts": sum(1 for l in lines if "Started server process" in l),
    }


# ── pure logic ───────────────────────────────────────────────────

def evaluate(now: datetime, health: Optional[dict], trend: Optional[dict], risk: Optional[dict],
             account: Optional[dict], journal_15: Dict, journal_60: Dict, journal_24h: Dict,
             state: Dict) -> Report:
    rep = Report()
    today = now.strftime("%Y-%m-%d")
    minutes = now.hour * 60 + now.minute

    def alert(key: str, text: str):
        rep.alerts.append({"key": key, "text": text})

    # bridge / engine
    if not health or "_error" in health:
        alert("bridge_down", f"Bridge no responde en {BRIDGE}: {(health or {}).get('_error', 'sin respuesta')}")
        rep.facts["bridge"] = "down"
    else:
        rep.facts["bridge"] = "ok"
        if health.get("engine_expected") and not health.get("engine_running"):
            alert("engine_down", "Engine parado aunque autostart lo espera (engine_running=false)")
        if health.get("engine_running") and not health.get("ws_connected"):
            alert("ws_down", "Feed de Binance desconectado (ws_connected=false)")
        age = health.get("last_tick_age_sec")
        if isinstance(age, (int, float)) and health.get("engine_running") and age > MAX_TICK_AGE_SEC:
            alert("stale_ticks", f"Sin ticks de mercado desde hace {age:.0f} s")
        if health.get("degraded"):
            alert("degraded", "Health degradado: " + ", ".join(map(str, health.get("reasons") or [])))
        tf = health.get("telegram_failures") or 0
        if tf and tf > (state.get("telegram_failures_seen") or 0):
            alert("telegram_failures", f"Telegram: {tf} mensajes perdidos desde el arranque")

    # daily trend run
    if trend and "_error" not in trend and trend.get("enabled"):
        last = str(trend.get("last_run_utc") or "")[:10]
        status = trend.get("last_run_status")
        rep.facts["trend_last_run"] = trend.get("last_run_utc")
        rep.facts["trend_status"] = status
        if status == "error":
            alert("trend_error", f"Run diario del trend con ERROR: {trend.get('last_error', '')[:200]}")
        elif minutes >= TREND_DEADLINE_MIN and last != today:
            alert("trend_missing", f"El run diario del trend de hoy ({today}) no se ha ejecutado "
                                   f"(último: {trend.get('last_run_utc') or 'nunca'})")
        if trend.get("killed"):
            alert("trend_killed", "TREND_DAILY está KILLED por el edge monitor")

    # risk
    if risk and "_error" not in risk:
        if risk.get("circuit_breaker"):
            alert("circuit_breaker", f"Circuit breaker ACTIVO (PnL día {risk.get('daily_pnl')}, límite {risk.get('daily_limit')})")
        if risk.get("drawdown_halted"):
            alert("drawdown_halt", f"HALT por drawdown máximo ({risk.get('drawdown_pct', 0) * 100:.2f} %)")
        killed = risk.get("killed_strategies") or {}
        if killed:
            alert("killed:" + ",".join(sorted(killed)), "Estrategias desactivadas por el edge monitor: "
                  + "; ".join(f"{k}: {v}" for k, v in killed.items()))

    # journal
    if journal_15.get("available"):
        if journal_15.get("errors", 0) > 0:
            alert("journal_errors", f"{journal_15['errors']} errores/tracebacks en los últimos {WINDOW_MIN} min"
                                    + (f": {journal_15['first_error']}" if journal_15.get("first_error") else ""))
        # deploys restart once (twice when install.sh re-enables); a loop is 3+ in the window
        if journal_15.get("restarts", 0) >= 3:
            alert("restart_loop", f"El bridge se ha reiniciado {journal_15['restarts']} veces en {WINDOW_MIN} min")
    if journal_60.get("available") and journal_60.get("regime_changed", 0) > MAX_REGIME_FLIPS_PER_HOUR:
        alert("regime_flood", f"{journal_60['regime_changed']} cambios de régimen en la última hora "
                              f"(umbral {MAX_REGIME_FLIPS_PER_HOUR})")

    # daily summary: first evaluation after 00:20 UTC, once per day
    if minutes >= TREND_DEADLINE_MIN and state.get("last_summary_date") != today:
        rep.summary = _summary(today, trend, risk, account, journal_24h)
    return rep


def _money(x) -> str:
    try:
        return f"{float(x):+.2f} $"
    except (TypeError, ValueError):
        return "n/a"


def _summary(today: str, trend, risk, account, j24: Dict) -> str:
    a = account if account and "_error" not in account else {}
    r = risk if risk and "_error" not in risk else {}
    t = trend if trend and "_error" not in trend else {}
    lines = [f"<b>BotStrike · resumen diario {today}</b>"]
    if a:
        lines.append(f"Equity <b>{float(a.get('equity', 0)):.2f} $</b> · realizado {_money(a.get('realized_pnl'))} · "
                     f"abierto {_money(a.get('unrealized_pnl'))} · posiciones {a.get('open_positions', 0)}")
        lines.append(f"Día {_money(a.get('daily_pnl'))} · semana {_money(a.get('weekly_pnl'))} · "
                     f"DD {float(a.get('drawdown_pct', 0)) * 100:.2f} % · exposición {float(a.get('exposure_pct', 0)) * 100:.1f} %")
    if t:
        pos = t.get("positions") or []
        lines.append(f"Trend diario: run {t.get('last_run_status', 'n/a')} a las {str(t.get('last_run_utc', ''))[11:16]} UTC · "
                     f"universo {', '.join(t.get('universe') or [])} · {len(pos)} posiciones"
                     + (" · TARDÍO" if t.get("last_run_late") else ""))
    if r.get("killed_strategies"):
        lines.append("Killed: " + ", ".join(r["killed_strategies"]))
    if j24.get("available"):
        lines.append(f"Últimas 24 h: {j24.get('regime_changed', 0)} cambios de régimen · {j24.get('errors', 0)} errores · "
                     f"{j24.get('telegram_sent', 0)} mensajes enviados · {j24.get('restarts', 0)} arranques")
    return "\n".join(html.escape(l, quote=False).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>") for l in lines)


# ── side effects ─────────────────────────────────────────────────

def load_state() -> Dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_json(path: str, data: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        print(f"ops_monitor: cannot write {path}: {e}", file=sys.stderr)


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("ops_monitor: telegram not configured; would send:\n" + text)
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status == 200
        except Exception as e:  # noqa: BLE001
            print(f"ops_monitor: telegram send failed ({attempt + 1}/3): {type(e).__name__}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    return False


def plan_sends(rep: Report, state: Dict, now_ts: float) -> List[Dict[str, str]]:
    """Pure: which messages to send now, given the de-dup state. Each item: {kind, key, text}.
    kind = alert | summary | recovered. Does not mutate `state`."""
    plan: List[Dict[str, str]] = []
    last_alerts: Dict[str, float] = state.get("last_alerts") or {}
    active = {a["key"] for a in rep.alerts}
    for a in rep.alerts:
        if now_ts - last_alerts.get(a["key"], 0.0) >= ALERT_REPEAT_SEC:
            plan.append({"kind": "alert", "key": a["key"],
                         "text": "⚠️ <b>BotStrike ops</b>\n" + html.escape(a["text"], quote=False)})
    if rep.summary:
        plan.append({"kind": "summary", "key": "summary", "text": rep.summary})
    notified = state.get("recovered_notified") or {}
    for key, ts in last_alerts.items():
        if key not in active and not key.startswith("killed:") and notified.get(key) != ts:
            plan.append({"kind": "recovered", "key": key,
                         "text": "✅ <b>BotStrike ops</b>\nResuelto: " + html.escape(key, quote=False)})
    return plan


def main() -> int:
    now = datetime.now(timezone.utc)
    state = load_state()
    health = get_json("/api/health")
    trend = get_json("/api/trend")
    risk = get_json("/api/risk")
    account = get_json("/api/account")
    j15 = journal_stats(WINDOW_MIN)
    j60 = journal_stats(60)
    j24 = journal_stats(24 * 60)
    rep = evaluate(now, health, trend, risk, account, j15, j60, j24, state)

    sent = []
    last_alerts: Dict[str, float] = state.get("last_alerts") or {}
    for item in plan_sends(rep, state, now.timestamp()):
        if not send_telegram(item["text"]):
            continue
        sent.append(item["key"])
        if item["kind"] == "alert":
            last_alerts[item["key"]] = now.timestamp()
        elif item["kind"] == "summary":
            state["last_summary_date"] = now.strftime("%Y-%m-%d")
        elif item["kind"] == "recovered":
            state.setdefault("recovered_notified", {})[item["key"]] = last_alerts[item["key"]]
    if health and "_error" not in health:
        state["telegram_failures_seen"] = health.get("telegram_failures") or 0
    state["last_alerts"] = last_alerts
    state["last_run"] = now.isoformat()
    save_json(STATE_PATH, state)
    save_json(LAST_PATH, {"ts": now.isoformat(), "alerts": rep.alerts, "sent": sent, "summary_sent": bool(rep.summary),
                          "facts": rep.facts, "journal_15": j15, "journal_60": j60})
    print(f"ops_monitor {now.isoformat()} alerts={len(rep.alerts)} sent={sent} summary={bool(rep.summary)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"ops_monitor: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)
