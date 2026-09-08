"""Mark-to-market equity history (2026-09-08).

Until this module existed every HISTORICAL surface — the Portfolio "Account Value" chart, the
30-day drawdown, the win/loss days, the win streak, the strategy card's sparkline and max DD — was
computed from the realised cash chain (initial capital + every fill's cash effect), because that is
all the trade DB holds. A trend book carries its result in OPEN positions for days, so the cash
chain sat flat at ~1,008 while the account had been at 1,038 and back: the chart's maximum was
always "now" and the 30-day drawdown read 2 % under a live peak-to-trough of 3 % (Edgar, 2026-09-08).

Two things live here:
  * `EquityHistory` — one mark-to-market sample a minute (equity, realised, unrealised), persisted
    to `data/equity_history.json`, pruned at 120 days, with the daily closes / highs / lows and the
    peak-to-trough drawdown derived from it.
  * `reconstruct_daily_mtm` — for the days BEFORE the first real sample, the equity at each day's
    end rebuilt from the fills (positions at their fill-price average) priced at the daily source
    close. Those points are flagged `est` and every consumer labels them as estimates: the source
    close is Binance spot / Yahoo, not the venue's mark, and a day's intraday peak is not in them.
"""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

SAMPLE_SEC = 60.0
KEEP_DAYS = 120
DAY = 86400.0


def _default_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.getenv("BOTSTRIKE_EQUITY_HISTORY", os.path.join(root, "data", "equity_history.json"))


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _day(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


class EquityHistory:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _default_path()
        self._rows: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._dirty = 0
        self._load()

    # ── persistence ──────────────────────────────────────────────
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                rows = json.load(f)
            if isinstance(rows, list):
                self._rows = [r for r in rows if isinstance(r, dict) and "ts" in r and "equity" in r]
                self._rows.sort(key=lambda r: float(r["ts"]))
        except Exception:  # noqa: BLE001 — missing / corrupt file = empty history
            self._rows = []

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._rows, f, separators=(",", ":"))
            os.replace(tmp, self.path)
            self._dirty = 0
        except Exception:  # noqa: BLE001 — the history must never break trading
            pass

    def save(self) -> None:
        with self._lock:
            self._save()

    # ── writing ──────────────────────────────────────────────────
    def sample(self, ts: float, equity: float, realised: float = 0.0, unrealised: float = 0.0,
               force: bool = False) -> bool:
        """Append a real sample unless the last real one is younger than SAMPLE_SEC. Returns True when written."""
        ts = float(ts)
        equity = _f(equity)
        if equity <= 0:
            return False
        with self._lock:
            last = self._last_real()
            if not force and last is not None and ts - float(last["ts"]) < SAMPLE_SEC:
                return False
            self._rows.append({"ts": round(ts, 3), "equity": round(equity, 4), "realised": round(_f(realised), 4),
                               "unrealised": round(_f(unrealised), 4)})
            self._prune(ts)
            self._dirty += 1
            if self._dirty >= 1:          # one write a minute is cheap; a crash never loses more than that
                self._save()
            return True

    def add_estimated(self, points: Iterable[Dict[str, Any]]) -> int:
        """Insert estimated day-end points for days that carry no point yet and precede the first
        real sample. Idempotent. Returns the number inserted."""
        with self._lock:
            first_real = self._last_or_first_real(first=True)
            have_days = {_day(float(r["ts"])) for r in self._rows}
            n = 0
            for p in points:
                ts = float(p.get("ts") or 0.0)
                eq = _f(p.get("equity"))
                if ts <= 0 or eq <= 0:
                    continue
                if first_real is not None and ts >= float(first_real["ts"]):
                    continue
                if _day(ts) in have_days:
                    continue
                self._rows.append({"ts": round(ts, 3), "equity": round(eq, 4), "realised": round(_f(p.get("realised")), 4),
                                   "unrealised": round(_f(p.get("unrealised")), 4), "est": True})
                have_days.add(_day(ts))
                n += 1
            if n:
                self._rows.sort(key=lambda r: float(r["ts"]))
                self._save()
            return n

    def _prune(self, now_ts: float) -> None:
        cutoff = now_ts - KEEP_DAYS * DAY
        if self._rows and float(self._rows[0]["ts"]) < cutoff:
            self._rows = [r for r in self._rows if float(r["ts"]) >= cutoff]

    # ── reading ──────────────────────────────────────────────────
    def _last_real(self) -> Optional[Dict[str, Any]]:
        for r in reversed(self._rows):
            if not r.get("est"):
                return r
        return None

    def _last_or_first_real(self, first: bool) -> Optional[Dict[str, Any]]:
        it = self._rows if first else reversed(self._rows)
        for r in it:
            if not r.get("est"):
                return r
        return None

    def points(self, since_ts: Optional[float] = None) -> List[Dict[str, Any]]:
        with self._lock:
            rows = list(self._rows)
        if since_ts is not None:
            rows = [r for r in rows if float(r["ts"]) >= since_ts]
        return rows

    def series(self, since_ts: Optional[float] = None) -> List[List[float]]:
        return [[float(r["ts"]), float(r["equity"])] for r in self.points(since_ts)]

    def first_real_ts(self) -> Optional[float]:
        with self._lock:
            r = self._last_or_first_real(first=True)
        return float(r["ts"]) if r else None

    def last_estimated_ts(self) -> Optional[float]:
        with self._lock:
            est = [float(r["ts"]) for r in self._rows if r.get("est")]
        return max(est) if est else None

    def count(self) -> int:
        with self._lock:
            return len(self._rows)

    def daily(self) -> Dict[str, Dict[str, Any]]:
        """Per UTC day: close (last sample), high, low, first, and whether the day is an estimate."""
        out: Dict[str, Dict[str, Any]] = {}
        for r in self.points():
            d = _day(float(r["ts"]))
            eq = float(r["equity"])
            row = out.get(d)
            if row is None:
                out[d] = {"close": eq, "high": eq, "low": eq, "first": eq, "est": bool(r.get("est")), "samples": 1}
            else:
                row["close"] = eq
                row["high"] = max(row["high"], eq)
                row["low"] = min(row["low"], eq)
                row["est"] = row["est"] and bool(r.get("est"))
                row["samples"] += 1
        return out

    def max_drawdown(self, since_ts: Optional[float] = None, live_equity: Optional[float] = None,
                     start_equity: Optional[float] = None) -> float:
        """Worst peak-to-trough on the sampled path (as a share of the peak), the live equity
        appended as the last point and `start_equity` (the equity at the window's start) as the first."""
        vals = [float(r["equity"]) for r in self.points(since_ts)]
        if start_equity is not None and start_equity > 0:
            vals = [float(start_equity)] + vals
        if live_equity is not None and live_equity > 0:
            vals.append(float(live_equity))
        peak = -math.inf
        dd = 0.0
        for v in vals:
            peak = max(peak, v)
            if peak > 0:
                dd = max(dd, (peak - v) / peak)
        return dd

    def peak(self) -> float:
        vals = [float(r["equity"]) for r in self.points()]
        return max(vals) if vals else 0.0


# ── backfill ─────────────────────────────────────────────────────

def reconstruct_daily_mtm(trades: List[Any], initial_capital: float, close_fn: Callable[[str, str], Optional[float]],
                          first_day: str, last_day: str) -> List[Dict[str, Any]]:
    """Equity at the END of each UTC day in [first_day, last_day], rebuilt from the fills.

    `trades` are trade-DB records (trade_type, symbol, side, quantity, price = the FILL, timestamp,
    plus what `trade_database.models.cash_effect` reads). `close_fn(ui_symbol, "YYYY-MM-DD")`
    returns the day's source close or None; a day with a position whose close is unknown is skipped
    (an estimate that prices a leg at zero is worse than no estimate). Every point is `est`.
    """
    from trade_database.models import cash_effect

    rows = sorted((t for t in trades if t is not None), key=lambda t: _f(getattr(t, "timestamp", 0.0)))
    start = datetime.strptime(first_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(last_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    out: List[Dict[str, Any]] = []
    i = 0
    cash = 0.0
    book: Dict[str, Dict[str, float]] = {}       # symbol -> {"qty": signed, "cost": Σ qty×price of the open quantity}
    d = start
    while d <= end:
        day_end = (d + timedelta(days=1)).timestamp()
        while i < len(rows) and _f(getattr(rows[i], "timestamp", 0.0)) < day_end:
            t = rows[i]
            i += 1
            ttype = str(getattr(t, "trade_type", "") or "ENTRY").upper()
            cash += _f(cash_effect(t))
            if ttype == "FUNDING":
                continue
            sym = str(getattr(t, "symbol", "") or "")
            qty = abs(_f(getattr(t, "quantity", 0.0)))
            px = _f(getattr(t, "price", 0.0))
            b = book.setdefault(sym, {"qty": 0.0, "cost": 0.0})
            if ttype == "ENTRY":
                side = str(getattr(t, "side", "") or "").upper()
                signed = qty if side in ("BUY", "LONG") else -qty
                if b["qty"] == 0.0 or (b["qty"] > 0) == (signed > 0):
                    b["qty"] += signed
                    b["cost"] += signed * px
                else:                                  # an entry against an open position closes part of it
                    avg = b["cost"] / b["qty"] if b["qty"] else px
                    b["cost"] -= signed * avg
                    b["qty"] += signed
            else:                                      # EXIT / trim: the quantity leaves at the average cost
                if b["qty"] != 0.0:
                    avg = b["cost"] / b["qty"]
                    sign = 1.0 if b["qty"] > 0 else -1.0
                    leave = min(qty, abs(b["qty"]))
                    b["qty"] -= sign * leave
                    b["cost"] -= sign * leave * avg
                if abs(b["qty"]) < 1e-12:
                    b["qty"] = 0.0
                    b["cost"] = 0.0
        day = d.strftime("%Y-%m-%d")
        unreal = 0.0
        priced = True
        for sym, b in book.items():
            if b["qty"] == 0.0:
                continue
            close = close_fn(sym, day)
            if close is None or _f(close) <= 0:
                priced = False
                break
            avg = b["cost"] / b["qty"]
            unreal += b["qty"] * (_f(close) - avg)     # signed qty: a short gains when the close is below
        if priced:
            out.append({"ts": round(day_end - 1.0, 3), "equity": round(_f(initial_capital) + cash + unreal, 4),
                        "realised": round(cash, 4), "unrealised": round(unreal, 4), "est": True})
        d += timedelta(days=1)
    return out


def close_on_or_before(df: Any, day: str, max_gap_days: int = 4) -> Optional[float]:
    """The daily source close for `day`, or the last settled close before it when that day has no
    bar — a weekend or a holiday on a TradFi market, or the newest TradFi day not cached yet — as
    long as it is not more than `max_gap_days` old. Without this the reconstruction skipped every
    Saturday and the last Monday of the book (2026-09-08)."""
    try:
        import pandas as pd
        ts = pd.Timestamp(day)
        sub = df.loc[:ts]
        if sub.empty:
            return None
        last_ts = sub.index[-1]
        if (ts - last_ts).days > max_gap_days:
            return None
        v = float(sub["close"].iloc[-1])
        return v if v > 0 else None
    except Exception:  # noqa: BLE001
        return None


_HISTORY: Optional[EquityHistory] = None
_HISTORY_LOCK = threading.Lock()


def get_equity_history() -> EquityHistory:
    global _HISTORY
    with _HISTORY_LOCK:
        if _HISTORY is None:
            _HISTORY = EquityHistory()
        return _HISTORY
