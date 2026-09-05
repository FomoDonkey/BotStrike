"""Activity feed (UI spec v2.16 §5.2): the operator-facing timeline of what the bot did.

Sources:
  * fills — the bridge records them where it intercepts paper/live fills (`record_fill`);
  * engine events — a structlog processor (`activity_processor`) whitelists a few event names
    (regime changes, daily trend run, kills, risk halts, config changes) and turns them into rows;
  * system — the bridge adds start/stop rows directly.
Persisted to data/activity.json (last MAXLEN rows) so the feed survives a restart.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

MAXLEN = 300
KINDS = ("fill", "run", "regime", "risk", "kill", "system", "config", "signal")


def _default_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.getenv("BOTSTRIKE_ACTIVITY_PATH", os.path.join(root, "data", "activity.json"))


class ActivityLog:
    def __init__(self, path: Optional[str] = None, maxlen: int = MAXLEN):
        self.path = path or _default_path()
        self._rows: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._load()

    # ── persistence ──────────────────────────────────────────────
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                rows = json.load(f)
            if isinstance(rows, list):
                for r in rows[-self._rows.maxlen:]:
                    if isinstance(r, dict) and "ts" in r:
                        self._rows.append(r)
        except Exception:  # noqa: BLE001 — missing/corrupt file = empty feed
            pass

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self._rows), f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001 — the feed must never break trading
            pass

    # ── API ──────────────────────────────────────────────────────
    def add(self, kind: str, title: str, detail: str = "", *, level: str = "info", symbol: Optional[str] = None,
            side: Optional[str] = None, pnl: Optional[float] = None, roe_pct: Optional[float] = None,
            ts: Optional[float] = None, strategy: Optional[str] = None) -> Dict[str, Any]:
        row = {
            "ts": float(ts if ts is not None else time.time()),
            "kind": kind if kind in KINDS else "system",
            "level": level, "symbol": symbol, "side": side, "strategy": strategy,
            "title": str(title)[:120], "detail": str(detail)[:240],
            "pnl": (float(pnl) if pnl is not None else None),
            "roe_pct": (float(roe_pct) if roe_pct is not None else None),
        }
        with self._lock:
            self._rows.append(row)
            self._save()
        return row

    def list(self, limit: int = 100, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            rows = list(self._rows)
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return rows[: max(1, min(int(limit), self._rows.maxlen))]

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
            self._save()

    # ── fills ────────────────────────────────────────────────────
    def record_fill(self, t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """`t` is a serialized trade (bridge serialize_trade / /api/trades row)."""
        try:
            symbol = t.get("symbol") or ""
            side = str(t.get("side") or "")
            ttype = str(t.get("trade_type") or "ENTRY").upper()
            qty = float(t.get("quantity") or 0.0)
            price = float(t.get("price") or t.get("exit_price") or t.get("entry_price") or 0.0)
            strategy = str(t.get("strategy") or "")
            nice = strategy.replace("_", " ").title() if strategy else ""
            notional = qty * price
            base = symbol.split("-")[0] if symbol else ""
            if ttype == "ENTRY":
                pos = "LONG" if side == "BUY" else "SHORT"
                return self.add("fill", f"Opened {pos} {symbol}",
                                f"{qty:.6g} {base} (${notional:,.2f})" + (f" · {nice}" if nice else ""),
                                symbol=symbol, side=side, strategy=strategy, ts=t.get("timestamp"))
            pos = "LONG" if side == "BUY" else "SHORT"          # exits carry the POSITION side
            pnl = float(t.get("pnl") or 0.0)
            roe = t.get("roe_pct")
            reason = str(t.get("exit_reason") or ttype.lower()).replace("_", " ")
            return self.add("fill", f"Closed {pos} {symbol}",
                            f"{qty:.6g} {base} (${notional:,.2f}) · {reason}" + (f" · {nice}" if nice else ""),
                            symbol=symbol, side=side, strategy=strategy, pnl=pnl,
                            roe_pct=(float(roe) if roe is not None else None), ts=t.get("timestamp"),
                            level="info" if pnl >= 0 else "warning")
        except Exception:  # noqa: BLE001
            return None


# ── structlog processor ──────────────────────────────────────────

def _fmt_targets(v: Any) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{k} {float(x) * 100:.1f}%" for k, x in list(v.items())[:6])
    return str(v)


def build_event_row(event: str, ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a whitelisted structlog event to an activity row (kind/title/detail). None = ignore."""
    sym = ev.get("symbol")
    if event == "regime_changed":
        if str(ev.get("old")) == "UNKNOWN":
            return None                                      # startup, not a change
        return {"kind": "regime", "title": f"Regime {ev.get('new')}", "detail": f"was {ev.get('old')}", "symbol": sym}
    if event == "trend_daily_run_ok":
        pos = ev.get("positions")
        return {"kind": "run", "title": "Trend daily run OK",
                "detail": f"{pos} positions · targets {_fmt_targets(ev.get('targets'))}"[:240]}
    if event == "trend_late_run_fills_at_current_price":
        return {"kind": "run", "title": "Trend daily late run", "detail": "fills at current price", "level": "warning"}
    if event == "trend_adds_blocked_by_risk":
        return {"kind": "risk", "title": "Trend adds held by risk",
                "detail": f"{ev.get('reason', '')} · {', '.join(ev.get('blocked') or [])}"[:200], "level": "warning"}
    if event == "trend_book_flattened":
        return {"kind": "risk", "title": "Trend book flattened", "detail": str(ev.get("reason", ""))[:200], "level": "warning"}
    if event == "strategy_disabled_by_performance":
        return {"kind": "kill", "title": f"{ev.get('strategy')} killed by edge monitor",
                "detail": str(ev.get("reason", ""))[:200], "level": "warning", "strategy": str(ev.get("strategy") or "")}
    if event in ("strategy_kill_lifted", "strategy_reenabled_after_cooldown", "strategy_performance_recovered"):
        return {"kind": "kill", "title": f"{ev.get('strategy')} re-enabled", "detail": event.replace("_", " "),
                "strategy": str(ev.get("strategy") or "")}
    if event in ("circuit_breaker_triggered", "max_drawdown_reached", "daily_loss_limit_reached",
                 "weekly_loss_limit_reached", "consecutive_loss_pause"):
        detail = ", ".join(f"{k}={v}" for k, v in ev.items() if k not in ("event", "timestamp", "level", "logger"))[:200]
        return {"kind": "risk", "title": event.replace("_", " ").capitalize(), "detail": detail, "level": "warning"}
    if event == "config_updated":
        applied = ev.get("applied")
        det = ", ".join(map(str, applied)) if isinstance(applied, (list, tuple)) else str(applied or "")
        return {"kind": "config", "title": "Config changed", "detail": det[:240]}
    if event == "signal_validated":
        return {"kind": "signal", "title": f"Signal {ev.get('side')} {sym}", "detail": str(ev.get("strategy", ""))[:100],
                "symbol": sym, "side": ev.get("side"), "strategy": str(ev.get("strategy") or "")}
    return None


_LOG: Optional[ActivityLog] = None


def get_activity_log() -> ActivityLog:
    global _LOG
    if _LOG is None:
        _LOG = ActivityLog()
    return _LOG


def activity_processor(logger, method_name, event_dict):  # structlog signature
    """Append whitelisted events to the activity feed; never alters or drops the log line."""
    try:
        event = event_dict.get("event")
        if isinstance(event, str):
            row = build_event_row(event, event_dict)
            if row:
                get_activity_log().add(row["kind"], row["title"], row.get("detail", ""), level=row.get("level", "info"),
                                       symbol=row.get("symbol"), side=row.get("side"), strategy=row.get("strategy"))
    except Exception:  # noqa: BLE001
        pass
    return event_dict
