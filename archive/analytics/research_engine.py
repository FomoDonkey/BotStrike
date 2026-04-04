"""
ResearchEngine — Quantitative validation engine for paper trading.

Answers: "Does this strategy have real edge after costs?"

Features:
  - Rolling performance metrics (last N trades, last 24h)
  - Per-strategy breakdown with after-fees calculations
  - MAE/MFE analysis (trade quality assessment)
  - Execution quality analysis (LIMIT vs MARKET)
  - Auto-report generation (every N trades or on demand)
  - Kill switch: auto-disables strategies with negative expectancy

Usage:
    engine = ResearchEngine(settings)
    engine.on_trade(trade)          # Feed every closed trade
    engine.check_report()           # Auto-generates report if threshold reached
    report = engine.generate_report()  # On-demand full report
"""
from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from config.settings import Settings
from core.types import Trade, StrategyType
import structlog

logger = structlog.get_logger(__name__)


# ────────────────���─────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────────────────────

@dataclass
class TradeAnalysis:
    """Complete analysis record for a single closed trade."""
    # Identity
    trade_id: str = ""
    timestamp: float = 0.0
    symbol: str = ""
    strategy: str = ""
    side: str = ""

    # Price
    entry_price: float = 0.0
    exit_price: float = 0.0

    # Size
    size_units: float = 0.0
    size_usd: float = 0.0

    # Performance (after fees)
    pnl_usd: float = 0.0
    pnl_bps: float = 0.0
    fee_usd: float = 0.0
    holding_time_sec: float = 0.0

    # MAE/MFE
    mae_bps: float = 0.0   # Maximum Adverse Excursion in bps
    mfe_bps: float = 0.0   # Maximum Favorable Excursion in bps
    mae_price: float = 0.0
    mfe_price: float = 0.0

    # Execution quality
    slippage_bps: float = 0.0
    expected_cost_bps: float = 0.0
    fill_probability: float = 0.0
    order_type: str = ""
    routing_reason: str = ""

    # Market context at entry
    regime: str = ""
    spread_bps: float = 0.0
    atr: float = 0.0
    signal_strength: float = 0.0
    exit_reason: str = ""

    @property
    def is_winner(self) -> bool:
        return self.pnl_usd > 0

    @property
    def r_multiple(self) -> float:
        """PnL as multiple of initial risk (MAE). R > 1 means captured more than risked."""
        if self.mae_bps > 0:
            return self.pnl_bps / self.mae_bps
        return 0.0


@dataclass
class StrategyReport:
    """Performance report for a single strategy."""
    strategy: str = ""
    # Counts
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    # PnL (after fees)
    total_pnl: float = 0.0
    total_fees: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    # Ratios
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0    # Expected PnL per trade (after fees)
    payoff_ratio: float = 0.0  # avg_win / |avg_loss|
    # Risk
    sharpe: float = 0.0
    max_drawdown_usd: float = 0.0
    max_consecutive_losses: int = 0
    # MAE/MFE
    avg_mae_bps: float = 0.0
    avg_mfe_bps: float = 0.0
    mae_mfe_ratio: float = 0.0  # avg_mae / avg_mfe — lower is better
    # Execution
    avg_slippage_bps: float = 0.0
    pct_market_orders: float = 0.0
    avg_hold_time_sec: float = 0.0
    # Regime breakdown
    regime_counts: Dict[str, int] = field(default_factory=dict)
    regime_pnl: Dict[str, float] = field(default_factory=dict)
    # Status
    is_active: bool = True
    kill_reason: str = ""


@dataclass
class ResearchReport:
    """Complete research report across all strategies."""
    timestamp: float = 0.0
    report_number: int = 0
    total_trades: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    equity: float = 0.0
    max_drawdown_usd: float = 0.0
    strategies: Dict[str, StrategyReport] = field(default_factory=dict)
    alerts: List[str] = field(default_factory=list)
    kill_switches_triggered: List[str] = field(default_factory=list)
    exit_report_text: str = ""  # Formatted exit optimization report


# ──────────────────────────────────────────────────────────────
# Research Engine
# ──────────────────────────────────────────────────────────────

class ResearchEngine:
    """Quantitative research engine for paper trading validation."""

    # Kill switch thresholds
    KILL_MIN_TRADES = 30              # Minimum trades before kill switch activates
    KILL_PROFIT_FACTOR = 1.0          # Disable if PF < 1.0 over last 50 trades
    KILL_WIN_RATE_FLOOR = 0.20        # Disable if WR < 20% (clearly broken)
    KILL_MAX_CONSECUTIVE_LOSSES = 10  # Disable after 10 consecutive losses

    # Report thresholds
    REPORT_EVERY_N_TRADES = 20
    REPORT_EVERY_SEC = 86400  # 24 hours

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config = settings.trading
        self._trades: deque[TradeAnalysis] = deque(maxlen=5000)
        self._trade_features: deque[dict] = deque(maxlen=5000)  # raw signal_features for exit optimizer
        self._trades_since_report: int = 0
        self._last_report_time: float = time.time()
        self._report_count: int = 0
        self._equity: float = self.config.initial_capital
        self._equity_peak: float = self.config.initial_capital
        self._max_drawdown: float = 0.0

        # Exit optimizer
        from analytics.exit_optimizer import ExitOptimizer
        fee_bps = (self.config.taker_fee * 10_000) * 2 + self.config.slippage_bps * 2
        self.exit_optimizer = ExitOptimizer(fee_bps=fee_bps)

        # Per-strategy state for kill switch
        self._consecutive_losses: Dict[str, int] = {}
        self._disabled_strategies: Dict[str, str] = {}  # strategy -> reason

    # ── Trade ingestion ─────────────────────────────────────

    def on_trade(self, trade: Trade) -> Optional[ResearchReport]:
        """Process a closed trade. Returns report if threshold reached.

        Only call this for EXIT trades (pnl != 0). Entries are ignored.
        """
        if trade.pnl == 0:
            return None

        analysis = self._convert_trade(trade)
        self._trades.append(analysis)
        self._trades_since_report += 1

        # Store raw signal_features for exit optimizer (needs price_path)
        if trade.signal_features:
            sf = trade.signal_features.copy()
            sf["side"] = trade.side.value if trade.side else "BUY"
            self._trade_features.append(sf)

        # Update equity
        self._equity += trade.pnl
        if self._equity > self._equity_peak:
            self._equity_peak = self._equity
        dd = self._equity_peak - self._equity
        if dd > self._max_drawdown:
            self._max_drawdown = dd

        # Track consecutive losses
        strat = analysis.strategy
        if analysis.is_winner:
            self._consecutive_losses[strat] = 0
        else:
            self._consecutive_losses[strat] = self._consecutive_losses.get(strat, 0) + 1

        # Check kill switch
        self._check_kill_switch(strat)

        # Check if report threshold reached
        return self.check_report()

    def check_report(self) -> Optional[ResearchReport]:
        """Generate report if trade count or time threshold reached."""
        trades_trigger = self._trades_since_report >= self.REPORT_EVERY_N_TRADES
        time_trigger = (time.time() - self._last_report_time) >= self.REPORT_EVERY_SEC

        if trades_trigger or time_trigger:
            report = self.generate_report()
            self._trades_since_report = 0
            self._last_report_time = time.time()
            self._log_report(report)
            return report
        return None

    # ── Report generation ───────────────────────────────────

    def generate_report(self) -> ResearchReport:
        """Generate comprehensive research report from all recorded trades."""
        self._report_count += 1
        trades = list(self._trades)

        report = ResearchReport(
            timestamp=time.time(),
            report_number=self._report_count,
            total_trades=len(trades),
            total_pnl=sum(t.pnl_usd for t in trades),
            total_fees=sum(t.fee_usd for t in trades),
            equity=self._equity,
            max_drawdown_usd=self._max_drawdown,
        )

        # Per-strategy analysis
        by_strategy: Dict[str, List[TradeAnalysis]] = {}
        for t in trades:
            by_strategy.setdefault(t.strategy, []).append(t)

        for strat, strat_trades in by_strategy.items():
            sr = self._analyze_strategy(strat, strat_trades)
            sr.is_active = strat not in self._disabled_strategies
            if strat in self._disabled_strategies:
                sr.kill_reason = self._disabled_strategies[strat]
            report.strategies[strat] = sr

        # Generate alerts
        report.alerts = self._generate_alerts(report)
        report.kill_switches_triggered = list(self._disabled_strategies.keys())

        # Exit optimization report (if enough trades with price paths)
        features = list(self._trade_features)
        if len(features) >= 10:
            try:
                exit_rpt = self.exit_optimizer.analyze(features)
                report.exit_report_text = self.exit_optimizer.format_report(exit_rpt)
            except Exception as e:
                logger.warning("exit_optimizer_error", error=str(e))

        return report

    def get_strategy_status(self, strategy: StrategyType) -> Tuple[bool, str]:
        """Check if a strategy should be active. Returns (is_active, reason)."""
        strat_name = strategy.value
        if strat_name in self._disabled_strategies:
            return False, self._disabled_strategies[strat_name]
        return True, ""

    def force_enable_strategy(self, strategy: str) -> None:
        """Manually re-enable a killed strategy."""
        self._disabled_strategies.pop(strategy, None)
        self._consecutive_losses[strategy] = 0
        logger.info("strategy_force_enabled", strategy=strategy)

    # ── Internal analysis ───────────────────────────────────

    def _convert_trade(self, trade: Trade) -> TradeAnalysis:
        """Convert core Trade to TradeAnalysis with full context."""
        sf = trade.signal_features or {}
        entry = sf.get("entry_price", trade.expected_price)
        exit_p = sf.get("exit_price", trade.price)

        return TradeAnalysis(
            trade_id=trade.order_id,
            timestamp=trade.timestamp,
            symbol=trade.symbol,
            strategy=trade.strategy.value if trade.strategy else "",
            side=trade.side.value if trade.side else "",
            entry_price=entry,
            exit_price=exit_p,
            size_units=trade.quantity,
            size_usd=trade.price * trade.quantity if trade.price > 0 else 0,
            pnl_usd=trade.pnl,
            pnl_bps=sf.get("pnl_bps", 0),
            fee_usd=trade.fee,
            holding_time_sec=sf.get("hold_time_sec", 0),
            # MAE/MFE
            mae_bps=sf.get("mae_bps", 0),
            mfe_bps=sf.get("mfe_bps", 0),
            mae_price=sf.get("mae_price", 0),
            mfe_price=sf.get("mfe_price", 0),
            # Execution
            slippage_bps=trade.actual_slippage_bps,
            expected_cost_bps=sf.get("expected_cost_bps", 0),
            fill_probability=sf.get("fill_probability", 0),
            order_type=sf.get("order_type", ""),
            routing_reason=sf.get("routing_reason", ""),
            # Market context
            regime=sf.get("regime_at_entry", ""),
            spread_bps=sf.get("spread_at_entry_bps", 0),
            atr=sf.get("atr_at_entry", 0),
            signal_strength=sf.get("signal_strength", 0),
            exit_reason=sf.get("exit_reason", ""),
        )

    def _analyze_strategy(self, strategy: str, trades: List[TradeAnalysis]) -> StrategyReport:
        """Compute full strategy report from trade list."""
        if not trades:
            return StrategyReport(strategy=strategy)

        pnls = [t.pnl_usd for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0

        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0

        # Consecutive losses
        max_consec = 0
        current_consec = 0
        for p in pnls:
            if p <= 0:
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0

        # Drawdown
        equity_curve = np.cumsum(pnls)
        peak = np.maximum.accumulate(equity_curve)
        drawdowns = peak - equity_curve
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

        # Sharpe (annualized, using trade PnLs as returns)
        if len(pnls) > 1:
            mean_pnl = np.mean(pnls)
            std_pnl = np.std(pnls, ddof=1)
            if std_pnl > 0:
                # Estimate trades per day, annualize
                time_span = trades[-1].timestamp - trades[0].timestamp
                days = max(time_span / 86400, 1)
                trades_per_day = len(trades) / days
                sharpe = (mean_pnl / std_pnl) * np.sqrt(trades_per_day * 365)
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # MAE/MFE
        maes = [t.mae_bps for t in trades if t.mae_bps != 0]
        mfes = [t.mfe_bps for t in trades if t.mfe_bps != 0]
        avg_mae = float(np.mean(maes)) if maes else 0.0
        avg_mfe = float(np.mean(mfes)) if mfes else 0.0

        # Execution
        slippages = [t.slippage_bps for t in trades if t.slippage_bps > 0]
        market_count = sum(1 for t in trades if t.order_type == "MARKET")
        hold_times = [t.holding_time_sec for t in trades if t.holding_time_sec > 0]

        # Regime breakdown
        regime_counts: Dict[str, int] = {}
        regime_pnl: Dict[str, float] = {}
        for t in trades:
            r = t.regime or "UNKNOWN"
            regime_counts[r] = regime_counts.get(r, 0) + 1
            regime_pnl[r] = regime_pnl.get(r, 0) + t.pnl_usd

        wr = len(wins) / len(trades) if trades else 0
        pf = gross_profit / gross_loss if gross_loss > 0 else 9999.99
        expectancy = float(np.mean(pnls)) if pnls else 0.0

        return StrategyReport(
            strategy=strategy,
            total_trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            total_pnl=sum(pnls),
            total_fees=sum(t.fee_usd for t in trades),
            avg_pnl=expectancy,
            avg_win=float(avg_win),
            avg_loss=float(avg_loss),
            best_trade=max(pnls) if pnls else 0,
            worst_trade=min(pnls) if pnls else 0,
            win_rate=wr,
            profit_factor=pf,
            expectancy=expectancy,
            payoff_ratio=abs(float(avg_win) / float(avg_loss)) if avg_loss != 0 else 9999.99,
            sharpe=float(sharpe),
            max_drawdown_usd=max_dd,
            max_consecutive_losses=max_consec,
            avg_mae_bps=avg_mae,
            avg_mfe_bps=avg_mfe,
            mae_mfe_ratio=avg_mae / avg_mfe if avg_mfe > 0 else 0,
            avg_slippage_bps=float(np.mean(slippages)) if slippages else 0,
            pct_market_orders=market_count / len(trades) if trades else 0,
            avg_hold_time_sec=float(np.mean(hold_times)) if hold_times else 0,
            regime_counts=regime_counts,
            regime_pnl=regime_pnl,
        )

    # ── Kill switch ─────────────────────────────────────────

    def _check_kill_switch(self, strategy: str) -> None:
        """Evaluate kill conditions for a strategy. Disables if triggered."""
        if strategy in self._disabled_strategies:
            return

        strat_trades = [t for t in self._trades if t.strategy == strategy]
        n = len(strat_trades)

        # Not enough data yet
        if n < self.KILL_MIN_TRADES:
            return

        # Check consecutive losses
        consec = self._consecutive_losses.get(strategy, 0)
        if consec >= self.KILL_MAX_CONSECUTIVE_LOSSES:
            reason = f"consecutive_losses={consec}"
            self._disable_strategy(strategy, reason)
            return

        # Use last 50 trades for rolling evaluation
        window = strat_trades[-50:]
        pnls = [t.pnl_usd for t in window]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0

        # Profit factor check
        if gross_loss > 0:
            pf = gross_profit / gross_loss
            if pf < self.KILL_PROFIT_FACTOR:
                reason = f"profit_factor={pf:.2f}<{self.KILL_PROFIT_FACTOR} (last {len(window)} trades)"
                self._disable_strategy(strategy, reason)
                return

        # Win rate floor
        wr = len(wins) / len(window) if window else 0
        if wr < self.KILL_WIN_RATE_FLOOR:
            reason = f"win_rate={wr:.1%}<{self.KILL_WIN_RATE_FLOOR:.0%} (last {len(window)} trades)"
            self._disable_strategy(strategy, reason)
            return

    def _disable_strategy(self, strategy: str, reason: str) -> None:
        """Kill switch: disable a strategy."""
        self._disabled_strategies[strategy] = reason
        logger.warning("kill_switch_triggered",
                       strategy=strategy,
                       reason=reason,
                       total_trades=len([t for t in self._trades if t.strategy == strategy]))

    # ── Alerts ──────────────────────────────────────────────

    def _generate_alerts(self, report: ResearchReport) -> List[str]:
        """Generate actionable alerts from report data."""
        alerts = []

        for strat, sr in report.strategies.items():
            if sr.total_trades < 10:
                continue  # Not enough data

            if sr.profit_factor < 1.0 and sr.total_trades >= 20:
                alerts.append(
                    f"[CRITICAL] {strat}: profit_factor={sr.profit_factor:.2f} < 1.0 "
                    f"({sr.total_trades} trades, PnL=${sr.total_pnl:.2f})"
                )
            elif sr.profit_factor < 1.2 and sr.total_trades >= 20:
                alerts.append(
                    f"[WARNING] {strat}: profit_factor={sr.profit_factor:.2f} — marginal edge "
                    f"({sr.total_trades} trades)"
                )

            if sr.win_rate < 0.25 and sr.total_trades >= 15:
                alerts.append(
                    f"[CRITICAL] {strat}: win_rate={sr.win_rate:.1%} — below viable threshold"
                )

            if sr.max_drawdown_usd > report.equity * 0.05:
                alerts.append(
                    f"[WARNING] {strat}: max_drawdown=${sr.max_drawdown_usd:.2f} "
                    f"({sr.max_drawdown_usd/report.equity:.1%} of equity)"
                )

            if sr.mae_mfe_ratio > 1.5 and sr.total_trades >= 10:
                alerts.append(
                    f"[WARNING] {strat}: MAE/MFE ratio={sr.mae_mfe_ratio:.2f} — "
                    f"trades go against you more than for you"
                )

            if sr.avg_slippage_bps > 5.0:
                alerts.append(
                    f"[INFO] {strat}: avg_slippage={sr.avg_slippage_bps:.1f}bps — "
                    f"execution quality degrading"
                )

        if report.max_drawdown_usd > report.equity * 0.08:
            alerts.append(
                f"[CRITICAL] Portfolio drawdown=${report.max_drawdown_usd:.2f} "
                f"({report.max_drawdown_usd/report.equity:.1%}) approaching circuit breaker"
            )

        return alerts

    # ── Logging ─────────────────────────────────────────────

    def _log_report(self, report: ResearchReport) -> None:
        """Log report summary to structured logger."""
        logger.info("research_report",
                    report_number=report.report_number,
                    total_trades=report.total_trades,
                    total_pnl=round(report.total_pnl, 2),
                    total_fees=round(report.total_fees, 2),
                    equity=round(report.equity, 2),
                    max_drawdown=round(report.max_drawdown_usd, 2),
                    num_alerts=len(report.alerts),
                    num_kills=len(report.kill_switches_triggered))

        for strat, sr in report.strategies.items():
            logger.info("strategy_report",
                        strategy=strat,
                        trades=sr.total_trades,
                        win_rate=round(sr.win_rate, 3),
                        profit_factor=round(sr.profit_factor, 2),
                        expectancy=round(sr.expectancy, 4),
                        sharpe=round(sr.sharpe, 2),
                        avg_mae_bps=round(sr.avg_mae_bps, 1),
                        avg_mfe_bps=round(sr.avg_mfe_bps, 1),
                        avg_slippage_bps=round(sr.avg_slippage_bps, 1),
                        pnl=round(sr.total_pnl, 2),
                        active=sr.is_active)

        for alert in report.alerts:
            logger.warning("research_alert", alert=alert)

    def format_report(self, report: ResearchReport) -> str:
        """Format report as human-readable text."""
        lines = []
        lines.append("=" * 70)
        lines.append(f"  RESEARCH REPORT #{report.report_number}")
        lines.append(f"  {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(report.timestamp))}")
        lines.append("=" * 70)
        lines.append(f"  Trades: {report.total_trades}  |  PnL: ${report.total_pnl:+.2f}  |  "
                     f"Fees: ${report.total_fees:.2f}  |  Net: ${report.total_pnl:+.2f}")
        lines.append(f"  Equity: ${report.equity:.2f}  |  Max DD: ${report.max_drawdown_usd:.2f} "
                     f"({report.max_drawdown_usd/max(report.equity,1):.1%})")
        lines.append("")

        for strat, sr in report.strategies.items():
            status = "ACTIVE" if sr.is_active else f"KILLED: {sr.kill_reason}"
            lines.append(f"  --- {strat} [{status}] ---")
            lines.append(f"  Trades: {sr.total_trades}  |  W/L: {sr.wins}/{sr.losses}  |  "
                         f"WR: {sr.win_rate:.1%}")
            lines.append(f"  PnL: ${sr.total_pnl:+.2f}  |  Fees: ${sr.total_fees:.2f}  |  "
                         f"Expectancy: ${sr.expectancy:+.4f}/trade")
            lines.append(f"  PF: {sr.profit_factor:.2f}  |  Payoff: {sr.payoff_ratio:.2f}  |  "
                         f"Sharpe: {sr.sharpe:.2f}")
            lines.append(f"  Avg Win: ${sr.avg_win:+.4f}  |  Avg Loss: ${sr.avg_loss:+.4f}  |  "
                         f"Best: ${sr.best_trade:+.4f}")
            lines.append(f"  MAE: {sr.avg_mae_bps:.1f}bps  |  MFE: {sr.avg_mfe_bps:.1f}bps  |  "
                         f"MAE/MFE: {sr.mae_mfe_ratio:.2f}")
            lines.append(f"  Slippage: {sr.avg_slippage_bps:.1f}bps  |  "
                         f"Market%: {sr.pct_market_orders:.0%}  |  "
                         f"Hold: {sr.avg_hold_time_sec:.0f}s")
            lines.append(f"  Max DD: ${sr.max_drawdown_usd:.2f}  |  "
                         f"Max Consec Losses: {sr.max_consecutive_losses}")
            if sr.regime_pnl:
                regime_str = "  |  ".join(
                    f"{r}: ${p:+.2f} ({sr.regime_counts.get(r,0)}t)"
                    for r, p in sorted(sr.regime_pnl.items())
                )
                lines.append(f"  Regime PnL: {regime_str}")
            lines.append("")

        if report.alerts:
            lines.append("  ALERTS:")
            for alert in report.alerts:
                lines.append(f"    {alert}")
            lines.append("")

        if report.kill_switches_triggered:
            lines.append("  KILL SWITCHES ACTIVE:")
            for strat in report.kill_switches_triggered:
                lines.append(f"    {strat}: {self._disabled_strategies.get(strat, '')}")
            lines.append("")

        # Append exit optimization report if available
        if report.exit_report_text:
            lines.append("")
            lines.append(report.exit_report_text)

        lines.append("=" * 70)
        return "\n".join(lines)
