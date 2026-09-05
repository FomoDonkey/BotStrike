"""Portfolio analytics for the Strike-style Portfolio page (UI spec v2.16 §5.1).

Pure function over trade records (trade DB rows) + open position rows. Every number the page shows
comes from here, so the page cannot disagree with /api/trades or /api/account.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from analytics.alltime import SHARPE_MIN_DAYS, SHARPE_MIN_TRADES

DAY = 86400.0
T_STAT_MIN_TRADES = 20      # below this a per-strategy t-stat is noise and is reported as null


def _day(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _sharpe_daily(pnls_by_day: List[float], basis: float) -> Optional[float]:
    if len(pnls_by_day) < 2 or basis <= 0:
        return None
    rets = [p / basis for p in pnls_by_day]
    sd = statistics.pstdev(rets)
    if sd <= 0:
        return None
    return statistics.mean(rets) / sd * math.sqrt(365.0)


def _max_dd(curve: Iterable[float], basis: float) -> float:
    peak = -math.inf
    dd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > -math.inf and basis > 0:
            dd = max(dd, (peak - v) / basis)
    return dd


def _trading_style(median_hold_sec: float, n: int) -> str:
    if n == 0:
        return "n/a"
    if median_hold_sec < 3600:
        return "Scalper"
    if median_hold_sec < DAY:
        return "Day trader"
    if median_hold_sec < 7 * DAY:
        return "Swing"
    return "Position"


def compute_portfolio(trades: List[Any], initial_capital: float, positions: List[Dict[str, Any]], now_ts: float,
                      equity: float, margin_used: float, unrealized_pnl: float,
                      fees_taker: float = 0.0004, fees_maker: float = 0.0002) -> Dict[str, Any]:
    initial = _f(initial_capital)
    rows = sorted(trades, key=lambda t: _f(getattr(t, "timestamp", 0.0)))
    closes = [t for t in rows if (getattr(t, "trade_type", "") or "ENTRY") not in ("ENTRY", "FUNDING")]
    funding_rows = [t for t in rows if (getattr(t, "trade_type", "") or "") == "FUNDING"]
    funding_paid = sum(_f(t.pnl) for t in funding_rows)
    fills_notional = [(_f(getattr(t, "price", 0.0)) * _f(getattr(t, "quantity", 0.0))
                       if (getattr(t, "trade_type", "") or "") != "FUNDING" else 0.0) for t in rows]
    # the balance and the fees follow the cash rules every surface shares (trade_database.models)
    from trade_database.models import cash_effect, fee_paid as _fee_paid
    realized = sum(cash_effect(t) for t in rows)
    fees_paid = sum(_fee_paid(t) for t in rows)
    volume = sum(fills_notional)
    since_ts = _f(rows[0].timestamp) if rows else now_ts
    cutoff_30 = now_ts - 30 * DAY

    # ── per-day aggregates (closes → pnl/trades, all fills → volume/fees) ──
    by_day: Dict[str, Dict[str, float]] = {}

    def day_row(d: str) -> Dict[str, float]:
        return by_day.setdefault(d, {"pnl": 0.0, "volume": 0.0, "trades": 0, "fees": 0.0})

    for t, notional in zip(rows, fills_notional):
        r = day_row(_day(t.timestamp))
        r["volume"] += notional
        r["fees"] += _fee_paid(t)
        ttype = getattr(t, "trade_type", "") or "ENTRY"
        r["pnl"] += cash_effect(t)         # the day's move of the balance: fees, exits, funding
        if ttype not in ("ENTRY", "FUNDING"):
            r["trades"] += 1

    start = datetime.fromtimestamp(since_ts, tz=timezone.utc).date()
    end = datetime.fromtimestamp(now_ts, tz=timezone.utc).date()
    daily: List[Dict[str, Any]] = []
    cum = 0.0
    d = start
    while d <= end:
        key = d.strftime("%Y-%m-%d")
        r = by_day.get(key, {"pnl": 0.0, "volume": 0.0, "trades": 0, "fees": 0.0})
        cum += r["pnl"]
        eq = initial + cum + (unrealized_pnl if d == end else 0.0)
        daily.append({"date": key, "equity": round(eq, 4), "pnl": round(r["pnl"], 4), "volume": round(r["volume"], 2),
                      "trades": int(r["trades"]), "fees": round(r["fees"], 4)})
        d += timedelta(days=1)

    # ── win-day dots: last 18 calendar days, oldest first ──
    win_days = []
    for i in range(17, -1, -1):
        dd = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        r = by_day.get(dd)
        pnl = r["pnl"] if r else 0.0
        n = int(r["trades"]) if r else 0
        result = "flat" if n == 0 or abs(pnl) < 1e-9 else ("win" if pnl > 0 else "loss")
        win_days.append({"date": dd, "pnl": round(pnl, 4), "trades": n, "result": result})

    # longest streak of consecutive trading days with positive pnl
    streak = best = 0
    for key in sorted(k for k, v in by_day.items() if v["trades"] > 0):
        if by_day[key]["pnl"] > 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0

    # ── hold-time analysis ──
    holds = [_f(getattr(t, "duration_sec", 0.0)) for t in closes if _f(getattr(t, "duration_sec", 0.0)) > 0]
    avg_hold = statistics.mean(holds) if holds else 0.0
    med_hold = statistics.median(holds) if holds else 0.0

    # ── 30-day window ──
    c30 = [t for t in closes if _f(t.timestamp) >= cutoff_30]
    wins30 = sum(1 for t in c30 if _f(t.pnl) > 0)
    pnl30_by_day = [v["pnl"] for k, v in sorted(by_day.items()) if v["trades"] > 0
                    and datetime.strptime(k, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() >= cutoff_30]
    basis30 = initial + sum(_f(t.pnl) for t in closes if _f(t.timestamp) < cutoff_30)
    curve30 = []
    acc = basis30
    for t in c30:
        acc += _f(t.pnl)
        curve30.append(acc)
    days30 = (c30[-1].timestamp - c30[0].timestamp) / DAY if len(c30) >= 2 else 0.0
    sharpe_ok = len(c30) >= SHARPE_MIN_TRADES and days30 >= SHARPE_MIN_DAYS
    sharpe30 = _sharpe_daily(pnl30_by_day, basis30) if sharpe_ok else None
    perf_30d = {
        "drawdown": round(_max_dd([basis30] + curve30, basis30), 6) if curve30 else 0.0,
        "win_rate": round(wins30 / len(c30), 4) if c30 else 0.0,
        "sharpe": (round(sharpe30, 3) if sharpe30 is not None else None),
        "sharpe_valid": sharpe30 is not None,
        "sharpe_reason": "" if sharpe30 is not None else f"needs {SHARPE_MIN_TRADES} trades and {SHARPE_MIN_DAYS} days",
        "trades": len(c30),
        "pnl": round(sum(_f(t.pnl) for t in c30), 4),
        "volume": round(sum(n for t, n in zip(rows, fills_notional) if _f(t.timestamp) >= cutoff_30), 2),
    }

    # ── direction bias from open positions ──
    long_n = sum(_f(p.get("notional")) for p in positions if str(p.get("side")) == "BUY")
    short_n = sum(_f(p.get("notional")) for p in positions if str(p.get("side")) == "SELL")
    tot = long_n + short_n
    bias = {"long_notional": round(long_n, 4), "short_notional": round(short_n, 4),
            "long_pct": round(long_n / tot, 4) if tot > 0 else None}

    # ── per strategy ──
    strategies = sorted({str(getattr(t, "strategy", "") or "") for t in rows} |
                        {str(p.get("strategy") or "") for p in positions} - {""})
    by_strategy = []
    for s in strategies:
        srows = [t for t in rows if str(getattr(t, "strategy", "") or "") == s]
        sc = [t for t in srows if (getattr(t, "trade_type", "") or "ENTRY") not in ("ENTRY", "FUNDING")]
        pnls = [_f(t.pnl) for t in sc]
        # Funding is paid by THIS strategy's positions: without it the card read +9.99 while the
        # account read +10.09 at the same instant (2026-09-05).
        s_funding = sum(_f(t.pnl) for t in srows if (getattr(t, "trade_type", "") or "") == "FUNDING")
        # A rebalance trim is not a trade for the statistics (analytics/edge.py), and a t-stat
        # needs a sample: 15.88 off three rows was noise dressed as evidence.
        from analytics.edge import is_rebalance_row
        stat_rows = [t for t in sc if not is_rebalance_row(t)]
        stat_pnls = [_f(t.pnl) for t in stat_rows]
        gp = sum(p for p in pnls if p > 0)
        gl = -sum(p for p in pnls if p < 0)
        spos = [p for p in positions if str(p.get("strategy") or "") == s]
        s_unreal = sum(_f(p.get("unrealized_pnl")) for p in spos)
        curve = []
        acc = 0.0
        for t in sc:
            acc += _f(t.pnl)
            curve.append([round(_f(t.timestamp), 3), round(acc, 4)])
        if len(curve) > 200:
            step = len(curve) // 200 + 1
            curve = curve[::step] + [curve[-1]]
        sd = statistics.pstdev(stat_pnls) if len(stat_pnls) > 1 else 0.0
        t_stat = (statistics.mean(stat_pnls) / sd * math.sqrt(len(stat_pnls))
                  if sd > 0 and len(stat_pnls) >= T_STAT_MIN_TRADES else None)
        # daily pnl for this strategy
        sday: Dict[str, float] = {}
        for t in sc:
            sday[_day(t.timestamp)] = sday.get(_day(t.timestamp), 0.0) + _f(t.pnl)
        s_span = (sc[-1].timestamp - sc[0].timestamp) / DAY if len(sc) >= 2 else 0.0
        s_sharpe = _sharpe_daily(list(sday.values()), initial) if (len(sc) >= SHARPE_MIN_TRADES and s_span >= SHARPE_MIN_DAYS) else None
        by_strategy.append({
            "strategy": s, "trades": len(sc), "open_positions": len(spos),
            "realized": round(sum(pnls) + s_funding, 4), "unrealized": round(s_unreal, 4),
            "pnl": round(sum(pnls) + s_funding + s_unreal, 4), "funding": round(s_funding, 6),
            "trims": len(sc) - len(stat_rows),
            "volume": round(sum(_f(t.price) * _f(t.quantity) for t in srows), 2),
            "fees": round(sum(_f(getattr(t, "fee", 0.0)) for t in srows), 4),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
            "profit_factor": round(gp / gl, 3) if gl > 0 else (float("inf") if gp > 0 else 0.0),
            "sharpe": (round(s_sharpe, 3) if s_sharpe is not None else None),
            "max_drawdown": round(_max_dd([0.0] + [c[1] for c in curve], initial), 6) if curve else 0.0,
            "t_stat": (round(t_stat, 3) if t_stat is not None else None),
            "first_trade_ts": round(_f(srows[0].timestamp), 3) if srows else None,
            "equity_curve": curve,
            "return_30d": round(sum(_f(t.pnl) for t in sc if _f(t.timestamp) >= cutoff_30) / initial, 6) if initial > 0 else 0.0,
        })

    trend_book = sum(_f(p.get("notional")) for p in positions if str(p.get("strategy") or "") == "TREND_DAILY")
    return {
        "initial_capital": initial, "since_ts": round(since_ts, 3),
        "equity": round(_f(equity), 4), "cash": round(_f(equity) - _f(margin_used), 4), "margin_used": round(_f(margin_used), 4),
        "unrealized_pnl": round(_f(unrealized_pnl), 4), "realized_pnl": round(realized, 4),
        "alltime_pnl": round(realized + _f(unrealized_pnl), 4), "alltime_volume": round(volume, 2), "fees_paid": round(fees_paid, 4),
        "funding_paid": round(funding_paid, 6),
        "leverage": round(sum(_f(p.get("notional")) for p in positions) / _f(equity), 6) if _f(equity) > 0 else 0.0,
        "margin_usage": round(_f(margin_used) / _f(equity), 6) if _f(equity) > 0 else 0.0,
        "trend_book_notional": round(trend_book, 4),
        "volume_30d": perf_30d["volume"], "fees_taker": fees_taker, "fees_maker": fees_maker,
        "analysis": {"longest_win_streak_days": best, "trading_style": _trading_style(med_hold, len(closes)),
                     "avg_hold_sec": round(avg_hold, 1), "median_hold_sec": round(med_hold, 1), "closed_trades": len(closes)},
        "perf_30d": perf_30d, "win_days": win_days, "bias": bias, "daily": daily, "by_strategy": by_strategy,
    }
