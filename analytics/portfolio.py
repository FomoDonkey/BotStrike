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


MTM_FLAT_EPS = 0.005        # a day whose mark-to-market move is under half a cent is flat


def compute_portfolio(trades: List[Any], initial_capital: float, positions: List[Dict[str, Any]], now_ts: float,
                      equity: float, margin_used: float, unrealized_pnl: float,
                      fees_taker: float = 0.0004, fees_maker: float = 0.0002,
                      equity_history: Any = None) -> Dict[str, Any]:
    """`equity_history` (analytics.equity_history.EquityHistory) is the MARK-TO-MARKET path. When it
    is given, every historical figure on the page follows it: the daily equity, the day's PnL, the
    win/loss days and streak, the 30-day drawdown and Sharpe, the strategy's max drawdown. Without
    it everything falls back to the realised cash chain — flat while positions are open, which is
    why a trend book's chart used to peak at "now" whatever the account had been through."""
    initial = _f(initial_capital)
    hist_daily: Dict[str, Dict[str, Any]] = equity_history.daily() if equity_history is not None else {}
    hist_n = int(equity_history.count()) if equity_history is not None else 0
    has_hist = hist_n > 0
    rows = sorted(trades, key=lambda t: _f(getattr(t, "timestamp", 0.0)))
    from analytics.edge import is_rebalance_row
    # statistics (trades, win rate, holds, Sharpe) count round trips; a rebalance trim is money
    # (in the balance, the days and the volume) but not a trade
    closes = [t for t in rows if (getattr(t, "trade_type", "") or "ENTRY") not in ("ENTRY", "FUNDING")
              and not is_rebalance_row(t)]
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
        if ttype not in ("ENTRY", "FUNDING") and not is_rebalance_row(t):
            r["trades"] += 1

    start = datetime.fromtimestamp(since_ts, tz=timezone.utc).date()
    end = datetime.fromtimestamp(now_ts, tz=timezone.utc).date()
    daily: List[Dict[str, Any]] = []
    cum = 0.0
    prev_mtm = initial
    mtm_by_day: Dict[str, float] = {}
    d = start
    while d <= end:
        key = d.strftime("%Y-%m-%d")
        r = by_day.get(key, {"pnl": 0.0, "volume": 0.0, "trades": 0, "fees": 0.0})
        cum += r["pnl"]
        eq_cash = initial + cum + (unrealized_pnl if d == end else 0.0)
        # The equity the chart draws is MARK-TO-MARKET: the day's last sample of the history,
        # today's live figure, and — for a day the history does not cover — the previous close
        # carried forward and flagged. Without a history at all, the cash chain as before.
        hd = hist_daily.get(key)
        if not has_hist:
            eq_mtm, est = eq_cash, False
        elif d == end:
            eq_mtm, est = _f(equity), False
        elif hd is not None:
            eq_mtm, est = _f(hd.get("close")), bool(hd.get("est"))
        else:
            eq_mtm, est = prev_mtm, True
        pnl_mtm = eq_mtm - prev_mtm
        prev_mtm = eq_mtm
        mtm_by_day[key] = pnl_mtm
        daily.append({"date": key, "equity": round(eq_mtm, 4), "equity_realised": round(eq_cash, 4), "equity_est": est,
                      "pnl": round(r["pnl"], 4), "pnl_mtm": round(pnl_mtm, 4), "volume": round(r["volume"], 2),
                      "trades": int(r["trades"]), "fees": round(r["fees"], 4)})
        d += timedelta(days=1)

    # ── win-day dots: last 18 calendar days, oldest first ──
    # With a history a day is won or lost by what the ACCOUNT did (mark-to-market); without one,
    # by the realised cash of the day's closes, as before.
    win_days = []
    for i in range(17, -1, -1):
        dd = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        r = by_day.get(dd)
        pnl_cash = r["pnl"] if r else 0.0
        n = int(r["trades"]) if r else 0
        if has_hist and dd in mtm_by_day:
            pnl = mtm_by_day[dd]
            result = "flat" if abs(pnl) < MTM_FLAT_EPS else ("win" if pnl > 0 else "loss")
        else:
            pnl = pnl_cash
            result = "flat" if n == 0 or abs(pnl) < 1e-9 else ("win" if pnl > 0 else "loss")
        win_days.append({"date": dd, "pnl": round(pnl, 4), "pnl_realised": round(pnl_cash, 4), "trades": n, "result": result})

    # longest streak of consecutive winning days: mark-to-market days with a history, else
    # trading days with positive realised pnl
    streak = best = 0
    if has_hist:
        for key in sorted(mtm_by_day):
            if mtm_by_day[key] > MTM_FLAT_EPS:
                streak += 1
                best = max(best, streak)
            else:
                streak = 0
    else:
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
    sharpe_reason = "" if sharpe30 is not None else f"needs {SHARPE_MIN_TRADES} trades and {SHARPE_MIN_DAYS} days"
    dd30 = round(_max_dd([basis30] + curve30, basis30), 6) if curve30 else 0.0
    if has_hist:
        # the 30-day drawdown is the worst peak-to-trough of the MARKED path inside the window
        # (the cash chain cannot fall while the positions are open, so it read 2 % under a live 3 %)
        cutoff_day = _day(cutoff_30)
        before = [v["close"] for k, v in sorted(hist_daily.items()) if k < cutoff_day]
        dd30 = round(equity_history.max_drawdown(since_ts=cutoff_30, live_equity=_f(equity),
                                                 start_equity=(before[-1] if before else None)), 6)
        # Sharpe of the marked daily returns, once there are 30 days of them; a Sharpe off three
        # round trips was never a statistic
        mtm_rets = []
        prev = None
        for k in sorted(mtm_by_day):
            close = _f(hist_daily.get(k, {}).get("close")) if k in hist_daily else None
            if close is None:
                prev = None
                continue
            if prev and prev > 0 and datetime.strptime(k, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() >= cutoff_30:
                mtm_rets.append(close / prev - 1.0)
            prev = close
        if len(mtm_rets) >= SHARPE_MIN_DAYS:
            sd = statistics.pstdev(mtm_rets)
            sharpe30 = (statistics.mean(mtm_rets) / sd * math.sqrt(365.0)) if sd > 0 else None
            sharpe_reason = "" if sharpe30 is not None else "flat history"
        else:
            sharpe30 = None
            sharpe_reason = f"needs {SHARPE_MIN_DAYS} days of mark-to-market history (have {len(mtm_rets)})"
    perf_30d = {
        "drawdown": dd30,
        "drawdown_mtm": bool(has_hist),
        "win_rate": round(wins30 / len(c30), 4) if c30 else 0.0,
        "sharpe": (round(sharpe30, 3) if sharpe30 is not None else None),
        "sharpe_valid": sharpe30 is not None,
        "sharpe_reason": sharpe_reason,
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
        sc_all = [t for t in srows if (getattr(t, "trade_type", "") or "ENTRY") not in ("ENTRY", "FUNDING")]
        # A rebalance trim is not a trade for the statistics (analytics/edge.py): the card read
        # 60 % / PF 7.97 on five rows while the edge monitor read 33 % / 3.04 on the three real
        # exits. Trims stay in the money (realised, curve) and are counted apart.
        sc = [t for t in sc_all if not is_rebalance_row(t)]
        pnls = [_f(t.pnl) for t in sc]
        # Funding is paid by THIS strategy's positions: without it the card read +9.99 while the
        # account read +10.09 at the same instant (2026-09-05).
        s_funding = sum(_f(t.pnl) for t in srows if (getattr(t, "trade_type", "") or "") == "FUNDING")
        stat_rows = sc
        stat_pnls = pnls
        gp = sum(p for p in pnls if p > 0)
        gl = -sum(p for p in pnls if p < 0)
        spos = [p for p in positions if str(p.get("strategy") or "") == s]
        s_unreal = sum(_f(p.get("unrealized_pnl")) for p in spos)
        curve = []
        acc = 0.0
        for t in sc_all:                      # the curve is money: trims included
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
        # A strategy that IS the whole book (the only one with fills or positions) has the book's
        # marked drawdown; the realised curve's 0.19 % beside the account's 2.7 % was a contradiction
        # on one product (2026-09-08). With several strategies the split is not observable, so each
        # keeps its realised figure and says so.
        s_dd_mtm = bool(has_hist and len(strategies) == 1)
        s_dd = (equity_history.max_drawdown(live_equity=_f(equity)) if s_dd_mtm
                else (_max_dd([0.0] + [c[1] for c in curve], initial) if curve else 0.0))
        by_strategy.append({
            "strategy": s, "trades": len(sc), "open_positions": len(spos),
            # the same cash rule the account chains (entries pay their fee at the fill, exits credit
            # the entry share, funding settles): 8.28 vs the account's 8.16 was the ADA/ZEC entry fees
            "realized": round(sum(cash_effect(t) for t in srows), 4), "unrealized": round(s_unreal, 4),
            "pnl": round(sum(cash_effect(t) for t in srows) + s_unreal, 4), "funding": round(s_funding, 6),
            "trims": len(sc_all) - len(sc),
            "volume": round(sum(_f(t.price) * _f(t.quantity) for t in srows), 2),
            "fees": round(sum(_f(getattr(t, "fee", 0.0)) for t in srows), 4),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
            "profit_factor": round(gp / gl, 3) if gl > 0 else (float("inf") if gp > 0 else 0.0),
            "sharpe": (round(s_sharpe, 3) if s_sharpe is not None else None),
            "max_drawdown": round(s_dd, 6),
            "max_drawdown_mtm": s_dd_mtm,
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
        # what the history covers, so the page can say which part of the chart is an estimate
        "equity_history": {
            "real_since": (equity_history.first_real_ts() if equity_history is not None else None),
            "estimated_until": (equity_history.last_estimated_ts() if equity_history is not None else None),
            "samples": hist_n,
        },
    }
