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


def compute_alltime_performance(trade_repo, initial_capital: float,
                                source: str = "paper") -> Optional[Dict]:
    """All-time realized performance for `source` trades. Returns None on error."""
    try:
        initial = float(initial_capital)
        trades = trade_repo.get_trades(source=source)
        closes = [t for t in trades if t.trade_type and t.trade_type != "ENTRY"]
        if not closes:
            return {
                "initial_capital": initial, "total_trades": 0, "pnl": 0.0,
                "win_rate": 0.0, "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
                "max_drawdown": 0.0, "total_fees": 0.0, "avg_win": 0.0,
                "avg_loss": 0.0, "profit_factor": 0.0, "expectancy": 0.0,
                "equity_curve_ts": [],
            }
        from analytics.performance import PerformanceAnalyzer
        rep = PerformanceAnalyzer().analyze(
            closes, initial_equity=initial, use_equity_after=False)
        # (timestamp, equity) pairs; first point = capital before first close
        pts = [[float(closes[0].timestamp), initial]] + [
            [float(t.timestamp), float(v)]
            for t, v in zip(closes, rep.equity_curve[1:])
        ]
        if len(pts) > 500:  # downsample for the chart, always keep the last point
            step = len(pts) // 500 + 1
            pts = pts[::step] + [pts[-1]]
        return {
            "initial_capital": initial,
            "total_trades": rep.total_trades,
            "pnl": round(rep.total_pnl, 4),
            "win_rate": round(rep.win_rate, 4),
            "sharpe_ratio": round(rep.sharpe_ratio, 2),
            "sortino_ratio": round(rep.sortino_ratio, 2),
            "max_drawdown": round(rep.max_drawdown, 4),
            "total_fees": round(rep.total_fees, 4),
            "avg_win": round(rep.avg_win, 4),
            "avg_loss": round(rep.avg_loss, 4),
            "profit_factor": round(rep.profit_factor, 2),
            "expectancy": round(rep.expectancy, 4),
            "equity_curve_ts": pts,
        }
    except Exception as e:
        logger.debug("alltime_perf_error", error=str(e), error_type=type(e).__name__)
        return None
