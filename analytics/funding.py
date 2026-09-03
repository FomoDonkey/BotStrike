"""Funding accrual for perpetual positions (roadmap P0.1).

A long-only trend book on perpetuals PAYS funding to shorts whenever the rate is positive, which on
the measured venues is 53–75 % of the 8-hour periods (Binance 166 d: BTC +3.2 %/yr, ETH +2.3 %,
ADA +1.2 % of notional; +4–7.5 %/yr in the last 30 days). Paper equity ignored this entirely, so
every paper result and the trend validation were optimistic by 1–4 % of equity per year.

Model (matches Binance/Strike): every `interval_hours` the exchange settles
    payment = position_notional * funding_rate       (positive = LONGS pay)
so the cash flow for a position is `-payment` for a long and `+payment` for a short.

`FundingAccrual` is pure and testable: it takes positions + rates + a timestamp and returns the
payments due, and remembers which settlement timestamps it has already charged (persisted so a
restart never double-charges or silently skips a settlement).
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_INTERVAL_HOURS = 8
# Sanity cap: a single settlement above this is treated as a bad tick and skipped (0.75 % per 8 h
# would be ~820 %/yr; real venues cap funding well below that).
MAX_ABS_RATE = 0.0075


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def settlement_boundaries(prev_ts: float, now_ts: float, interval_hours: int = DEFAULT_INTERVAL_HOURS) -> List[float]:
    """UTC settlement timestamps in (prev_ts, now_ts]: 00:00, 08:00, 16:00 for an 8 h interval."""
    if now_ts <= prev_ts or interval_hours <= 0:
        return []
    step = interval_hours * 3600
    first = (int(prev_ts) // step + 1) * step
    return [float(t) for t in range(first, int(now_ts) + 1, step)]


@dataclass
class FundingPayment:
    symbol: str
    side: str                 # BUY (long) or SELL (short)
    strategy: str
    notional: float
    rate: float
    amount: float             # signed cash flow for the account: negative = paid
    ts: float
    mark_price: float = 0.0
    periods: int = 1          # settlements collapsed into this payment

    def as_row(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "side": self.side, "strategy": self.strategy, "notional": round(self.notional, 6),
                "rate": self.rate, "amount": round(self.amount, 8), "ts": self.ts,
                "mark_price": self.mark_price, "periods": self.periods}


@dataclass
class FundingAccrual:
    """Tracks settled funding periods and computes what is due."""
    interval_hours: int = DEFAULT_INTERVAL_HOURS
    path: Optional[str] = None
    last_settled_ts: float = 0.0
    total_paid: float = 0.0                       # negative = the account paid
    by_symbol: Dict[str, float] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)
    max_history: int = 500

    # ── pure logic ────────────────────────────────────────────────
    def due(self, now_ts: float) -> List[float]:
        """Settlement timestamps not yet charged. The first run charges nothing (no history)."""
        if not self.last_settled_ts:
            return []
        return settlement_boundaries(self.last_settled_ts, now_ts, self.interval_hours)

    def compute(self, positions: List[Dict[str, Any]], rates: Dict[str, float], now_ts: float) -> List[FundingPayment]:
        """One payment per open position per pending settlement (collapsed into a single row per
        position when several settlements are pending, e.g. after downtime)."""
        periods = len(self.due(now_ts))
        if periods <= 0 or not positions:
            return []
        out: List[FundingPayment] = []
        for p in positions:
            symbol = p.get("symbol") or ""
            rate = _f(rates.get(symbol), 0.0)
            if rate == 0.0 or abs(rate) > MAX_ABS_RATE:
                continue
            size = abs(_f(p.get("size") or p.get("positionAmt")))
            mark = _f(p.get("mark_price") or p.get("markPrice") or p.get("entry_price"))
            notional = _f(p.get("notional")) or size * mark
            if notional <= 0:
                continue
            side = str(p.get("side") or ("SELL" if _f(p.get("positionAmt")) < 0 else "BUY")).upper()
            payment = notional * rate * periods
            amount = -payment if side in ("BUY", "LONG") else payment
            out.append(FundingPayment(symbol=symbol, side=side, strategy=str(p.get("strategy") or ""),
                                      notional=notional, rate=rate, amount=amount, ts=now_ts,
                                      mark_price=mark, periods=periods))
        return out

    # ── state ─────────────────────────────────────────────────────
    def mark_settled(self, payments: List[FundingPayment], now_ts: float) -> float:
        """Record payments, advance the settled clock, return the total cash flow (negative = paid)."""
        total = 0.0
        for p in payments:
            total += p.amount
            self.by_symbol[p.symbol] = round(self.by_symbol.get(p.symbol, 0.0) + p.amount, 8)
            self.history.append(p.as_row())
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        self.total_paid = round(self.total_paid + total, 8)
        self.last_settled_ts = now_ts
        self.save()
        return total

    def start(self, now_ts: Optional[float] = None) -> None:
        """Arm the clock without charging anything (first run / fresh install)."""
        if not self.last_settled_ts:
            self.last_settled_ts = float(now_ts if now_ts is not None else time.time())
            self.save()

    # ── persistence ───────────────────────────────────────────────
    @classmethod
    def load(cls, path: Optional[str] = None, interval_hours: int = DEFAULT_INTERVAL_HOURS) -> "FundingAccrual":
        path = path or os.getenv("BOTSTRIKE_FUNDING_STATE",
                                 os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                              "data", "funding_state.json"))
        obj = cls(interval_hours=interval_hours, path=path)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            obj.last_settled_ts = _f(d.get("last_settled_ts"))
            obj.total_paid = _f(d.get("total_paid"))
            obj.by_symbol = {k: _f(v) for k, v in (d.get("by_symbol") or {}).items()}
            obj.history = list(d.get("history") or [])[-obj.max_history:]
        except Exception:  # noqa: BLE001 — missing/corrupt state = fresh start
            pass
        return obj

    def save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"last_settled_ts": self.last_settled_ts, "total_paid": self.total_paid,
                           "by_symbol": self.by_symbol, "history": self.history[-self.max_history:],
                           "interval_hours": self.interval_hours}, f)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001 — funding bookkeeping must never break trading
            pass

    def status(self) -> Dict[str, Any]:
        return {"enabled": True, "interval_hours": self.interval_hours,
                "last_settled_utc": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.last_settled_ts))
                                     if self.last_settled_ts else None),
                "next_settlement_utc": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(
                    (int(time.time()) // (self.interval_hours * 3600) + 1) * self.interval_hours * 3600))),
                "total_paid": round(self.total_paid, 6), "by_symbol": {k: round(v, 6) for k, v in self.by_symbol.items()},
                "recent": self.history[-20:][::-1]}


def annualized_pct(rate_per_interval: float, interval_hours: int = DEFAULT_INTERVAL_HOURS) -> float:
    """Funding rate per settlement → annualized fraction of notional (365 d)."""
    return rate_per_interval * (24 / interval_hours) * 365


def record_rates(rates: Dict[str, float], now_ts: float, path: Optional[str] = None) -> int:
    """Append one row per market to data/funding_rates.csv at every settlement.

    Needed to validate funding-aware sizing later: no venue publishes a long funding history for
    every market, so the only way to get one is to record it from today (roadmap P0.2). Returns the
    number of rows written; never raises.
    """
    path = path or os.getenv("BOTSTRIKE_FUNDING_RATES",
                             os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                          "data", "funding_rates.csv"))
    rows = [(sym, rate) for sym, rate in sorted(rates.items())
            if isinstance(rate, (int, float)) and math.isfinite(float(rate))]
    if not rows:
        return 0
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write("ts,utc,symbol,rate,annualized_pct\n")
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
            for sym, rate in rows:
                f.write(f"{now_ts:.0f},{stamp},{sym},{float(rate):.10f},{annualized_pct(float(rate)):.6f}\n")
        return len(rows)
    except Exception:  # noqa: BLE001
        return 0
