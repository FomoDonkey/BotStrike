"""Risk state that must survive restarts, reconstructed from the trade DB.

Audit 2026-09-02: RiskManager initialised its equity peak with initial_capital on
every start, so the 10% circuit breaker and the daily-loss limit were per SESSION —
a bot losing 9% a day and restarting nightly would never trip. The trade DB already
holds every closed trade, so the peak, today's PnL and this ISO week's PnL are
recomputed from it at startup (no second state file to keep in sync).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class HistoricalRiskState:
    equity: float          # initial_capital + all realized pnl (fees included)
    peak: float            # highest chained equity ever (>= initial_capital)
    daily_pnl: float       # realized pnl closed today (UTC)
    weekly_pnl: float      # realized pnl closed this ISO week (UTC)
    closes: int
    first_ts: float = 0.0
    last_ts: float = 0.0


def compute_historical_risk_state(trade_repo, initial_capital: float, source: str = "paper",
                                  now: Optional[float] = None) -> HistoricalRiskState:
    """Chain closed-trade pnl (NET of fees) from the DB. Never raises: on any error the
    caller gets the plain initial-capital state and a warning in the journal."""
    initial = float(initial_capital)
    now = float(now or time.time())
    try:
        trades = trade_repo.get_trades(source=source)
    except Exception as e:
        logger.warning("risk_history_unavailable", error=str(e), error_type=type(e).__name__)
        return HistoricalRiskState(initial, initial, 0.0, 0.0, 0)
    # FUNDING rows are cash flows: they count towards equity/day/week PnL exactly like a closed trade
    closes = [t for t in trades if t.trade_type and t.trade_type != "ENTRY"]
    closes.sort(key=lambda t: float(t.timestamp or 0.0))
    today = datetime.fromtimestamp(now, timezone.utc)
    day_key = today.strftime("%Y-%m-%d")
    week_key = today.isocalendar()[:2]
    equity = initial
    peak = initial
    daily = 0.0
    weekly = 0.0
    for t in closes:
        pnl = float(t.pnl or 0.0)
        equity += pnl
        peak = max(peak, equity)
        ts = datetime.fromtimestamp(float(t.timestamp or 0.0), timezone.utc)
        if ts.strftime("%Y-%m-%d") == day_key:
            daily += pnl
        if ts.isocalendar()[:2] == week_key:
            weekly += pnl
    return HistoricalRiskState(
        equity=round(equity, 6), peak=round(peak, 6), daily_pnl=round(daily, 6),
        weekly_pnl=round(weekly, 6), closes=len(closes),
        first_ts=float(closes[0].timestamp) if closes else 0.0,
        last_ts=float(closes[-1].timestamp) if closes else 0.0,
    )


def restore_risk_state(risk_manager, state: HistoricalRiskState, compounding: bool) -> None:
    """Seed the RiskManager with the historical peak / daily / weekly PnL.

    With compounding the current equity is the historical one (so sizing reinvests
    gains); without it the equity stays at initial_capital but the peak/limits still
    come from history so the drawdown ladder cannot be reset by a restart."""
    risk_manager.restore_history(
        equity=state.equity if compounding else risk_manager.current_equity,
        peak=max(state.peak, state.equity if compounding else risk_manager.current_equity),
        daily_pnl=state.daily_pnl,
        weekly_pnl=state.weekly_pnl,
    )
    logger.info("risk_state_restored", equity=round(risk_manager.current_equity, 2),
                peak=round(state.peak, 2), daily_pnl=round(state.daily_pnl, 2),
                weekly_pnl=round(state.weekly_pnl, 2), closes=state.closes,
                compounding=compounding)
