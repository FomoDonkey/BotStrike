"""All-time realized performance from the trade DB — ONE builder for every surface.

The UI (REST /api/performance + WS metrics broadcast via server/bridge.py) and
the Telegram notifications (portfolio snapshot, startup) must show the same
numbers, so they all call this function. Ground truth (CT DB audit 2026-08-31):
TradeRecord.pnl is NET of fees and each session's equity_after restarts at
initial_capital, so multi-session curves chain pnl (use_equity_after=False) —
never equity_after, which produces a sawtooth on every service restart.
"""
from __future__ import annotations

from typing import Dict, Optional

import structlog

logger = structlog.get_logger(__name__)

# Minimum sample before an annualized Sharpe is shown at all (research §4.4 asks
# for far more before trusting one; this is only the "not pure noise" floor).
SHARPE_MIN_DAYS = 30.0
SHARPE_MIN_TRADES = 30


def compute_alltime_performance(trade_repo, initial_capital: float,
                                source: str = "paper") -> Optional[Dict]:
    """All-time realized performance for `source` trades. Returns None on error."""
    try:
        initial = float(initial_capital)
        from trade_database.models import cash_effect, fee_paid
        trades = trade_repo.get_trades(source=source)
        closes = [t for t in trades if t.trade_type and t.trade_type not in ("ENTRY", "FUNDING")]
        # Funding is a realized cash flow, not a trade: it moves equity but must not pollute
        # win rate / PF / Sharpe (roadmap P0.1).
        funding_total = sum(float(t.pnl or 0.0) for t in trades if t.trade_type == "FUNDING")
        # The balance: every row's cash effect — an entry's fee leaves at the fill, an exit brings
        # its round-trip net plus the entry share already paid, funding settles (2026-09-05).
        rows = sorted(trades, key=lambda t: float(t.timestamp or 0.0))
        realized_cash = sum(cash_effect(t) for t in rows)
        fees_cash = sum(fee_paid(t) for t in rows)
        if not closes:
            return {
                "initial_capital": initial, "total_trades": 0, "pnl": round(realized_cash, 4),
                "funding_paid": funding_total, "trade_pnl": 0.0,
                "win_rate": 0.0, "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
                "max_drawdown": 0.0, "total_fees": round(fees_cash, 4), "avg_win": 0.0,
                "avg_loss": 0.0, "profit_factor": 0.0, "expectancy": 0.0,
                "equity_curve_ts": [], "peak_equity": initial, "current_drawdown": 0.0,
                "sample_days": 0.0, "sharpe_valid": False, "first_trade_ts": 0.0,
            }
        from analytics.performance import PerformanceAnalyzer
        closes = sorted(closes, key=lambda t: float(t.timestamp or 0.0))
        rep = PerformanceAnalyzer().analyze(
            closes, initial_equity=initial, use_equity_after=False)
        # (timestamp, equity) pairs: the balance after every row, in order — entries (their fee),
        # exits, settlements — so the chart, the peak and the drawdown are the account's own path
        pts = [[float(rows[0].timestamp or 0.0), initial]]
        eq = initial
        for t in rows:
            eq += cash_effect(t)
            pts.append([float(t.timestamp or 0.0), round(eq, 6)])
        peak = max(v for _, v in pts)
        last = pts[-1][1]
        current_dd = (peak - last) / peak if peak > 0 else 0.0
        # the worst peak-to-trough of that same path (rep.max_drawdown chains closes only)
        max_dd_cash, run_peak = 0.0, pts[0][1]
        for _, v in pts:
            run_peak = max(run_peak, v)
            if run_peak > 0:
                max_dd_cash = max(max_dd_cash, (run_peak - v) / run_peak)
        first_ts = float(closes[0].timestamp)
        sample_days = max(0.0, (float(closes[-1].timestamp) - first_ts) / 86400.0)
        # A Sharpe annualized from a handful of daily returns is noise (audit
        # 2026-09-02: -29.51 from two days). The UI shows "n/a" until the sample
        # covers at least SHARPE_MIN_DAYS days AND SHARPE_MIN_TRADES closes.
        sharpe_valid = sample_days >= SHARPE_MIN_DAYS and rep.total_trades >= SHARPE_MIN_TRADES
        if len(pts) > 500:  # downsample for the chart, always keep the last point
            step = len(pts) // 500 + 1
            pts = pts[::step] + [pts[-1]]
        return {
            "initial_capital": initial,
            "total_trades": rep.total_trades,
            # Funding is a realized cash flow: it belongs in the all-time PnL and in the equity
            # curve, but never in the trade statistics. Reporting `pnl` without it made
            # /api/performance disagree with /api/portfolio and with the account (2026-09-03).
            "pnl": round(realized_cash, 4),
            "trade_pnl": round(rep.total_pnl, 4),
            "funding_paid": round(funding_total, 6),
            "win_rate": round(rep.win_rate, 4),
            "sharpe_ratio": round(rep.sharpe_ratio, 2),
            "sortino_ratio": round(rep.sortino_ratio, 2),
            "max_drawdown": round(max_dd_cash, 4),
            "total_fees": round(fees_cash, 4),
            "avg_win": round(rep.avg_win, 4),
            "avg_loss": round(rep.avg_loss, 4),
            "profit_factor": round(rep.profit_factor, 2),
            "expectancy": round(rep.expectancy, 4),
            "equity_curve_ts": pts,
            "peak_equity": round(peak, 4),
            "current_drawdown": round(current_dd, 4),
            "sample_days": round(sample_days, 2),
            "sharpe_valid": bool(sharpe_valid),
            "first_trade_ts": first_ts,
        }
    except Exception as e:
        logger.debug("alltime_perf_error", error=str(e), error_type=type(e).__name__)
        return None
