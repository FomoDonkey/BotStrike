"""
ExitOptimizer — Shadow exit strategy comparison engine.

Takes completed trades with price paths and simulates alternative exit strategies
to determine which approach maximizes edge.

Shadow Strategies:
  A. Fixed R:R (1:1, 1.5:1, 2:1, 3:1)
  B. Trailing stop (activate at MFE threshold, trail by ATR/bps)
  C. Time-based exit (close after N seconds if MFE not reached)
  D. Partial TP (50% at first target, 50% trailing)

All simulations use the actual price path recorded during the trade,
so results are deterministic and directly comparable.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

import structlog

logger = structlog.get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────────────────────

@dataclass
class ShadowResult:
    """Result of a single shadow exit simulation on one trade."""
    strategy_name: str = ""
    pnl_bps: float = 0.0
    exit_time_sec: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""     # "TP", "SL", "trailing", "time", "partial_tp+trail", "end_of_path"


@dataclass
class ExitStrategyStats:
    """Aggregated stats for one shadow exit strategy across all trades."""
    name: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl_bps: float = 0.0
    avg_pnl_bps: float = 0.0
    profit_factor: float = 0.0
    expectancy_bps: float = 0.0
    avg_hold_sec: float = 0.0
    # vs current
    improvement_bps: float = 0.0  # avg pnl improvement vs current exit


@dataclass
class MAEMFEAnalysis:
    """Distribution analysis of MAE and MFE."""
    # MFE percentiles
    mfe_p50: float = 0.0
    mfe_p75: float = 0.0
    mfe_p90: float = 0.0
    mfe_mean: float = 0.0
    # MAE percentiles
    mae_p50: float = 0.0
    mae_p75: float = 0.0
    mae_p90: float = 0.0
    mae_mean: float = 0.0
    # Capture efficiency: actual PnL / MFE (1.0 = captured all MFE)
    avg_capture_ratio: float = 0.0
    # What % of trades reached various MFE levels before hitting SL
    pct_reached_1r: float = 0.0   # MFE >= 1x risk
    pct_reached_2r: float = 0.0   # MFE >= 2x risk
    pct_reached_3r: float = 0.0   # MFE >= 3x risk
    # Unused MFE: how much we leave on the table
    avg_unused_mfe_bps: float = 0.0


@dataclass
class ValidationResult:
    """Out-of-sample validation result for one exit strategy."""
    strategy_name: str = ""
    # In-sample
    is_pf: float = 0.0
    is_expectancy_bps: float = 0.0
    is_win_rate: float = 0.0
    is_trades: int = 0
    # Out-of-sample
    oos_pf: float = 0.0
    oos_expectancy_bps: float = 0.0
    oos_win_rate: float = 0.0
    oos_trades: int = 0
    # Verdict
    is_overfit: bool = False
    is_valid: bool = False
    verdict: str = ""   # "VALID", "OVERFIT", "INSUFFICIENT_DATA", "NEGATIVE_EDGE"
    # Degradation
    pf_degradation: float = 0.0      # oos_pf / is_pf
    expect_degradation: float = 0.0  # oos_expect / is_expect


@dataclass
class StabilityCheck:
    """Stability of strategy rankings across IS/OOS."""
    top3_is: List[str] = field(default_factory=list)
    top3_oos: List[str] = field(default_factory=list)
    rank_correlation: float = 0.0   # Spearman-like: 1.0=identical, 0=random, -1=inverted
    is_stable: bool = False
    verdict: str = ""


@dataclass
class ExitReport:
    """Complete exit optimization report."""
    total_trades_analyzed: int = 0
    mae_mfe: MAEMFEAnalysis = field(default_factory=MAEMFEAnalysis)
    current_stats: ExitStrategyStats = field(default_factory=ExitStrategyStats)
    shadow_stats: Dict[str, ExitStrategyStats] = field(default_factory=dict)
    best_strategy: str = ""
    best_improvement_bps: float = 0.0
    # OOS validation (populated by validate())
    validation: Dict[str, ValidationResult] = field(default_factory=dict)
    stability: Optional[StabilityCheck] = None
    validated_best: str = ""           # Best strategy that PASSED OOS validation
    validated_improvement_bps: float = 0.0


# ──────────────────────────────────────────────────────────────
# Shadow Exit Strategies
# ──────────────────────────────────────────────────────────────

def _simulate_fixed_rr(
    path: List[Tuple[float, float]],
    entry_price: float,
    side: str,
    sl_bps: float,
    rr_ratio: float,
    fee_bps: float,
) -> ShadowResult:
    """Fixed R:R exit: TP at rr_ratio * SL distance."""
    tp_bps = sl_bps * rr_ratio
    name = f"fixed_{rr_ratio:.1f}R"

    for elapsed, price in path:
        if entry_price <= 0:
            break
        if side == "BUY":
            pnl_bps = (price - entry_price) / entry_price * 10_000
        else:
            pnl_bps = (entry_price - price) / entry_price * 10_000

        # Check SL
        if pnl_bps <= -sl_bps:
            net = -sl_bps - fee_bps
            return ShadowResult(name, net, elapsed, price, "SL")
        # Check TP
        if pnl_bps >= tp_bps:
            net = tp_bps - fee_bps
            return ShadowResult(name, net, elapsed, price, "TP")

    # End of path — close at last price
    if path and entry_price > 0:
        last_price = path[-1][1]
        if side == "BUY":
            final_bps = (last_price - entry_price) / entry_price * 10_000
        else:
            final_bps = (entry_price - last_price) / entry_price * 10_000
        return ShadowResult(name, final_bps - fee_bps, path[-1][0], last_price, "end_of_path")
    return ShadowResult(name, -fee_bps, 0, entry_price, "no_path")


def _simulate_trailing(
    path: List[Tuple[float, float]],
    entry_price: float,
    side: str,
    sl_bps: float,
    activation_bps: float,
    trail_bps: float,
    fee_bps: float,
) -> ShadowResult:
    """Trailing stop: activate when MFE exceeds activation_bps, then trail."""
    name = f"trail_{activation_bps:.0f}act_{trail_bps:.0f}tr"
    trailing_active = False
    best_pnl_bps = 0.0

    for elapsed, price in path:
        if entry_price <= 0:
            break
        if side == "BUY":
            pnl_bps = (price - entry_price) / entry_price * 10_000
        else:
            pnl_bps = (entry_price - price) / entry_price * 10_000

        # Initial SL
        if not trailing_active and pnl_bps <= -sl_bps:
            return ShadowResult(name, -sl_bps - fee_bps, elapsed, price, "SL")

        # Activate trailing
        if pnl_bps >= activation_bps:
            trailing_active = True

        if trailing_active:
            best_pnl_bps = max(best_pnl_bps, pnl_bps)
            # Trail: exit if price retraces trail_bps from best
            if pnl_bps <= best_pnl_bps - trail_bps:
                net = pnl_bps - fee_bps
                return ShadowResult(name, net, elapsed, price, "trailing")

    # End of path
    if path and entry_price > 0:
        last_price = path[-1][1]
        if side == "BUY":
            final = (last_price - entry_price) / entry_price * 10_000
        else:
            final = (entry_price - last_price) / entry_price * 10_000
        return ShadowResult(name, final - fee_bps, path[-1][0], last_price, "end_of_path")
    return ShadowResult(name, -fee_bps, 0, entry_price, "no_path")


def _simulate_time_exit(
    path: List[Tuple[float, float]],
    entry_price: float,
    side: str,
    sl_bps: float,
    max_hold_sec: float,
    min_mfe_bps: float,
    fee_bps: float,
) -> ShadowResult:
    """Time-based: close after max_hold_sec, OR earlier if MFE not reached by mid-point."""
    name = f"time_{max_hold_sec:.0f}s_mfe{min_mfe_bps:.0f}"
    best_mfe = 0.0

    for elapsed, price in path:
        if entry_price <= 0:
            break
        if side == "BUY":
            pnl_bps = (price - entry_price) / entry_price * 10_000
        else:
            pnl_bps = (entry_price - price) / entry_price * 10_000

        best_mfe = max(best_mfe, pnl_bps)

        # SL always active
        if pnl_bps <= -sl_bps:
            return ShadowResult(name, -sl_bps - fee_bps, elapsed, price, "SL")

        # Early exit: at half time, if MFE hasn't reached threshold, close
        if elapsed >= max_hold_sec * 0.5 and best_mfe < min_mfe_bps:
            net = pnl_bps - fee_bps
            return ShadowResult(name, net, elapsed, price, "time_early")

        # Max hold
        if elapsed >= max_hold_sec:
            net = pnl_bps - fee_bps
            return ShadowResult(name, net, elapsed, price, "time_max")

    # End of path
    if path and entry_price > 0:
        last_price = path[-1][1]
        if side == "BUY":
            final = (last_price - entry_price) / entry_price * 10_000
        else:
            final = (entry_price - last_price) / entry_price * 10_000
        return ShadowResult(name, final - fee_bps, path[-1][0], last_price, "end_of_path")
    return ShadowResult(name, -fee_bps, 0, entry_price, "no_path")


def _simulate_partial_tp(
    path: List[Tuple[float, float]],
    entry_price: float,
    side: str,
    sl_bps: float,
    first_tp_bps: float,
    trail_bps: float,
    fee_bps: float,
) -> ShadowResult:
    """Partial TP: close 50% at first_tp, trail remaining 50%."""
    name = f"partial_{first_tp_bps:.0f}tp_{trail_bps:.0f}tr"
    first_closed = False
    first_pnl = 0.0
    best_pnl_bps = 0.0

    for elapsed, price in path:
        if entry_price <= 0:
            break
        if side == "BUY":
            pnl_bps = (price - entry_price) / entry_price * 10_000
        else:
            pnl_bps = (entry_price - price) / entry_price * 10_000

        # SL on full position (or remaining half)
        if pnl_bps <= -sl_bps:
            if first_closed:
                # SL on remaining 50%
                net = (first_pnl * 0.5 + (-sl_bps) * 0.5) - fee_bps
            else:
                net = -sl_bps - fee_bps
            return ShadowResult(name, net, elapsed, price, "SL")

        # First TP at threshold (50%)
        if not first_closed and pnl_bps >= first_tp_bps:
            first_closed = True
            first_pnl = pnl_bps
            best_pnl_bps = pnl_bps

        # Trail remaining 50%
        if first_closed:
            best_pnl_bps = max(best_pnl_bps, pnl_bps)
            if pnl_bps <= best_pnl_bps - trail_bps:
                net = (first_pnl * 0.5 + pnl_bps * 0.5) - fee_bps
                return ShadowResult(name, net, elapsed, price, "partial_tp+trail")

    # End of path
    if path and entry_price > 0:
        last_price = path[-1][1]
        if side == "BUY":
            final = (last_price - entry_price) / entry_price * 10_000
        else:
            final = (entry_price - last_price) / entry_price * 10_000
        if first_closed:
            net = (first_pnl * 0.5 + final * 0.5) - fee_bps
        else:
            net = final - fee_bps
        return ShadowResult(name, net, path[-1][0], last_price, "end_of_path")
    return ShadowResult(name, -fee_bps, 0, entry_price, "no_path")


# ──────────────────────────────────────────────────────────────
# Exit Optimizer Engine
# ──────────────────────────────────────────────────────────────

class ExitOptimizer:
    """Compares exit strategies on completed trades using recorded price paths."""

    # Default fee: round-trip (entry + exit) in bps
    DEFAULT_FEE_BPS = 14.0  # 0.05% taker × 2 = 10bps, + 2bps slippage × 2 = 14bps total

    def __init__(self, fee_bps: float = DEFAULT_FEE_BPS) -> None:
        self.fee_bps = fee_bps

    # OOS validation thresholds
    MIN_TRADES_FOR_VALIDATION = 50
    IS_RATIO = 0.70                    # 70% in-sample, 30% out-of-sample
    PF_DEGRADATION_THRESHOLD = 0.80    # OOS PF must be >= 80% of IS PF
    STABILITY_THRESHOLD = 0.50         # Rank correlation threshold for stability

    def analyze(self, trades: List[dict]) -> ExitReport:
        """Run full exit analysis on completed trades.

        Args:
            trades: List of signal_features dicts from closed trades.
                    Each must contain: entry_price, exit_price, price_path, side,
                    mae_bps, mfe_bps, pnl_bps, stop_loss, take_profit, hold_time_sec
        """
        valid = [t for t in trades if t.get("price_path") and len(t["price_path"]) >= 2]
        if not valid:
            return ExitReport()

        report = ExitReport(total_trades_analyzed=len(valid))

        # 1. MAE/MFE distribution analysis
        report.mae_mfe = self._analyze_mae_mfe(valid)

        # 2. Current exit stats
        report.current_stats = self._current_stats(valid)

        # 3. Shadow strategies
        shadow_configs = self._get_shadow_configs(valid)
        for name, sim_fn in shadow_configs.items():
            results = [sim_fn(t) for t in valid]
            report.shadow_stats[name] = self._aggregate_results(
                name, results, report.current_stats.avg_pnl_bps)

        # 4. Find best (in-sample, all data)
        if report.shadow_stats:
            best_name = max(report.shadow_stats, key=lambda k: report.shadow_stats[k].expectancy_bps)
            best = report.shadow_stats[best_name]
            report.best_strategy = best_name
            report.best_improvement_bps = best.improvement_bps

        # 5. OOS validation (if enough trades)
        if len(valid) >= self.MIN_TRADES_FOR_VALIDATION:
            self._validate_oos(valid, report)
        elif len(valid) >= 20:
            logger.info("exit_oos_insufficient_data",
                        trades=len(valid),
                        required=self.MIN_TRADES_FOR_VALIDATION)

        return report

    def _validate_oos(self, trades: List[dict], report: ExitReport) -> None:
        """Split trades into IS/OOS, evaluate all strategies on both, detect overfit."""
        n = len(trades)
        split_idx = int(n * self.IS_RATIO)
        is_trades = trades[:split_idx]
        oos_trades = trades[split_idx:]

        if len(is_trades) < 15 or len(oos_trades) < 10:
            return

        # Get shadow configs from IS data (parameters derived from IS only)
        is_configs = self._get_shadow_configs(is_trades)
        is_current = self._current_stats(is_trades)
        oos_current = self._current_stats(oos_trades)

        is_results: Dict[str, ExitStrategyStats] = {}
        oos_results: Dict[str, ExitStrategyStats] = {}

        for name, sim_fn in is_configs.items():
            # Evaluate on IS
            is_sims = [sim_fn(t) for t in is_trades]
            is_results[name] = self._aggregate_results(name, is_sims, is_current.avg_pnl_bps)
            # Evaluate SAME strategy on OOS (no re-optimization)
            oos_sims = [sim_fn(t) for t in oos_trades]
            oos_results[name] = self._aggregate_results(name, oos_sims, oos_current.avg_pnl_bps)

        # Validate each strategy
        for name in is_results:
            is_s = is_results[name]
            oos_s = oos_results[name]

            vr = ValidationResult(
                strategy_name=name,
                is_pf=is_s.profit_factor,
                is_expectancy_bps=is_s.expectancy_bps,
                is_win_rate=is_s.win_rate,
                is_trades=is_s.total_trades,
                oos_pf=oos_s.profit_factor,
                oos_expectancy_bps=oos_s.expectancy_bps,
                oos_win_rate=oos_s.win_rate,
                oos_trades=oos_s.total_trades,
            )

            # Degradation ratios
            if is_s.profit_factor > 0 and is_s.profit_factor < 9999:
                vr.pf_degradation = oos_s.profit_factor / is_s.profit_factor
            if is_s.expectancy_bps != 0:
                vr.expect_degradation = oos_s.expectancy_bps / is_s.expectancy_bps if is_s.expectancy_bps != 0 else 0

            # Verdict
            if oos_s.expectancy_bps <= 0:
                vr.is_overfit = is_s.expectancy_bps > 0  # Positive IS but negative OOS = overfit
                vr.verdict = "OVERFIT" if vr.is_overfit else "NEGATIVE_EDGE"
            elif vr.pf_degradation < self.PF_DEGRADATION_THRESHOLD and is_s.profit_factor > 1.0:
                vr.is_overfit = True
                vr.verdict = "OVERFIT"
            else:
                vr.is_valid = True
                vr.verdict = "VALID"

            report.validation[name] = vr

        # Stability check: do top-3 rankings agree between IS and OOS?
        is_ranked = sorted(is_results, key=lambda k: is_results[k].expectancy_bps, reverse=True)
        oos_ranked = sorted(oos_results, key=lambda k: oos_results[k].expectancy_bps, reverse=True)

        top3_is = is_ranked[:3]
        top3_oos = oos_ranked[:3]

        # Simple rank correlation: count how many of IS top-3 appear in OOS top-3
        overlap = len(set(top3_is) & set(top3_oos))
        # Weighted by position similarity
        rank_corr = self._rank_correlation(is_ranked, oos_ranked)

        report.stability = StabilityCheck(
            top3_is=top3_is,
            top3_oos=top3_oos,
            rank_correlation=rank_corr,
            is_stable=rank_corr >= self.STABILITY_THRESHOLD,
            verdict="STABLE" if rank_corr >= self.STABILITY_THRESHOLD else "UNSTABLE (rankings shift between samples)",
        )

        # Find best VALIDATED strategy
        valid_strategies = [
            name for name, vr in report.validation.items()
            if vr.is_valid and vr.oos_expectancy_bps > 0
        ]
        if valid_strategies:
            best_valid = max(valid_strategies, key=lambda k: report.validation[k].oos_expectancy_bps)
            report.validated_best = best_valid
            report.validated_improvement_bps = report.validation[best_valid].oos_expectancy_bps - oos_current.avg_pnl_bps
        else:
            report.validated_best = ""
            report.validated_improvement_bps = 0.0

    @staticmethod
    def _rank_correlation(rank_a: List[str], rank_b: List[str]) -> float:
        """Compute simplified rank correlation between two orderings.

        Returns value in [-1, 1]: 1=identical, 0=unrelated, -1=inverted.
        Uses Spearman's formula on shared items.
        """
        if not rank_a or not rank_b:
            return 0.0
        # Build rank maps
        rank_map_a = {name: i for i, name in enumerate(rank_a)}
        rank_map_b = {name: i for i, name in enumerate(rank_b)}
        shared = set(rank_a) & set(rank_b)
        if len(shared) < 3:
            return 0.0
        n = len(shared)
        d_sq_sum = sum((rank_map_a[s] - rank_map_b[s]) ** 2 for s in shared)
        # Spearman: 1 - 6*sum(d^2) / (n*(n^2-1))
        denom = n * (n * n - 1)
        if denom == 0:
            return 0.0
        return 1.0 - 6.0 * d_sq_sum / denom

    def _analyze_mae_mfe(self, trades: List[dict]) -> MAEMFEAnalysis:
        """Compute MAE/MFE distribution statistics."""
        maes = [abs(t.get("mae_bps", 0)) for t in trades]
        mfes = [t.get("mfe_bps", 0) for t in trades]
        pnls = [t.get("pnl_bps", 0) for t in trades]

        # Capture ratio: how much of MFE we actually captured
        capture_ratios = []
        for mfe, pnl in zip(mfes, pnls):
            if mfe > 0:
                capture_ratios.append(pnl / mfe)

        # SL distance (from entry to stop_loss in bps)
        risks = []
        for t in trades:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            if entry > 0 and sl > 0:
                risks.append(abs(entry - sl) / entry * 10_000)

        avg_risk = float(np.mean(risks)) if risks else 0

        # % reaching various R-multiples
        pct_1r = sum(1 for m in mfes if avg_risk > 0 and m >= avg_risk) / len(mfes) if mfes and avg_risk > 0 else 0
        pct_2r = sum(1 for m in mfes if avg_risk > 0 and m >= 2 * avg_risk) / len(mfes) if mfes and avg_risk > 0 else 0
        pct_3r = sum(1 for m in mfes if avg_risk > 0 and m >= 3 * avg_risk) / len(mfes) if mfes and avg_risk > 0 else 0

        unused = [mfe - pnl for mfe, pnl in zip(mfes, pnls) if mfe > 0]

        return MAEMFEAnalysis(
            mfe_p50=float(np.percentile(mfes, 50)) if mfes else 0,
            mfe_p75=float(np.percentile(mfes, 75)) if mfes else 0,
            mfe_p90=float(np.percentile(mfes, 90)) if mfes else 0,
            mfe_mean=float(np.mean(mfes)) if mfes else 0,
            mae_p50=float(np.percentile(maes, 50)) if maes else 0,
            mae_p75=float(np.percentile(maes, 75)) if maes else 0,
            mae_p90=float(np.percentile(maes, 90)) if maes else 0,
            mae_mean=float(np.mean(maes)) if maes else 0,
            avg_capture_ratio=float(np.mean(capture_ratios)) if capture_ratios else 0,
            pct_reached_1r=pct_1r,
            pct_reached_2r=pct_2r,
            pct_reached_3r=pct_3r,
            avg_unused_mfe_bps=float(np.mean(unused)) if unused else 0,
        )

    def _current_stats(self, trades: List[dict]) -> ExitStrategyStats:
        """Compute stats for the actual exit that was taken."""
        pnls = [t.get("pnl_bps", 0) - self.fee_bps for t in trades]  # After fees
        return self._compute_stats("current", pnls,
                                   [t.get("hold_time_sec", 0) for t in trades], 0)

    def _get_shadow_configs(self, trades: List[dict]) -> Dict[str, callable]:
        """Define shadow exit strategies to test."""
        # Estimate median SL distance from actual trades
        sl_distances = []
        for t in trades:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            if entry > 0 and sl > 0:
                sl_distances.append(abs(entry - sl) / entry * 10_000)
        median_sl = float(np.median(sl_distances)) if sl_distances else 30.0

        configs = {}

        # A. Fixed R:R variations
        for rr in [1.0, 1.5, 2.0, 3.0]:
            rr_val = rr
            configs[f"fixed_{rr:.1f}R"] = lambda t, _rr=rr_val: _simulate_fixed_rr(
                t["price_path"], t["entry_price"],
                "BUY" if t.get("side", "BUY") == "BUY" or t.get("action", "").endswith("sell") else "SELL",
                median_sl, _rr, self.fee_bps)

        # B. Trailing stop variations
        for act_mult, trail_mult in [(1.0, 0.5), (1.5, 0.7), (2.0, 1.0)]:
            act_bps = median_sl * act_mult
            trail_bps = median_sl * trail_mult
            configs[f"trail_{act_bps:.0f}act_{trail_bps:.0f}tr"] = lambda t, _a=act_bps, _tr=trail_bps: _simulate_trailing(
                t["price_path"], t["entry_price"],
                "BUY" if t.get("side", "BUY") == "BUY" or t.get("action", "").endswith("sell") else "SELL",
                median_sl, _a, _tr, self.fee_bps)

        # C. Time-based
        for max_sec, min_mfe in [(120, 10), (180, 15), (300, 20)]:
            configs[f"time_{max_sec}s_mfe{min_mfe}"] = lambda t, _s=max_sec, _m=min_mfe: _simulate_time_exit(
                t["price_path"], t["entry_price"],
                "BUY" if t.get("side", "BUY") == "BUY" or t.get("action", "").endswith("sell") else "SELL",
                median_sl, _s, _m, self.fee_bps)

        # D. Partial TP
        for tp_mult, trail_mult in [(1.0, 0.5), (1.5, 0.7)]:
            tp_bps = median_sl * tp_mult
            trail_bps = median_sl * trail_mult
            configs[f"partial_{tp_bps:.0f}tp_{trail_bps:.0f}tr"] = lambda t, _tp=tp_bps, _tr=trail_bps: _simulate_partial_tp(
                t["price_path"], t["entry_price"],
                "BUY" if t.get("side", "BUY") == "BUY" or t.get("action", "").endswith("sell") else "SELL",
                median_sl, _tp, _tr, self.fee_bps)

        return configs

    def _aggregate_results(self, name: str, results: List[ShadowResult],
                           current_avg: float) -> ExitStrategyStats:
        """Aggregate shadow results into stats."""
        pnls = [r.pnl_bps for r in results]
        holds = [r.exit_time_sec for r in results]
        return self._compute_stats(name, pnls, holds, current_avg)

    def _compute_stats(self, name: str, pnls: List[float],
                       holds: List[float], current_avg: float) -> ExitStrategyStats:
        """Compute aggregated stats from PnL list."""
        if not pnls:
            return ExitStrategyStats(name=name)

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        avg = float(np.mean(pnls))

        return ExitStrategyStats(
            name=name,
            total_trades=len(pnls),
            wins=len(wins),
            losses=len(losses),
            win_rate=len(wins) / len(pnls) if pnls else 0,
            total_pnl_bps=sum(pnls),
            avg_pnl_bps=avg,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else 9999.99,
            expectancy_bps=avg,
            avg_hold_sec=float(np.mean(holds)) if holds else 0,
            improvement_bps=avg - current_avg,
        )

    def format_report(self, report: ExitReport) -> str:
        """Format exit report as human-readable text."""
        lines = []
        lines.append("=" * 70)
        lines.append("  EXIT OPTIMIZATION REPORT")
        lines.append(f"  Trades analyzed: {report.total_trades_analyzed}")
        lines.append("=" * 70)

        # MAE/MFE
        m = report.mae_mfe
        lines.append("")
        lines.append("  MAE/MFE DISTRIBUTION:")
        lines.append(f"    MFE: p50={m.mfe_p50:.1f}  p75={m.mfe_p75:.1f}  "
                     f"p90={m.mfe_p90:.1f}  mean={m.mfe_mean:.1f} bps")
        lines.append(f"    MAE: p50={m.mae_p50:.1f}  p75={m.mae_p75:.1f}  "
                     f"p90={m.mae_p90:.1f}  mean={m.mae_mean:.1f} bps")
        lines.append(f"    Capture ratio: {m.avg_capture_ratio:.1%} of MFE captured")
        lines.append(f"    Unused MFE: {m.avg_unused_mfe_bps:.1f} bps left on table")
        lines.append(f"    Reached 1R: {m.pct_reached_1r:.0%}  "
                     f"2R: {m.pct_reached_2r:.0%}  3R: {m.pct_reached_3r:.0%}")

        # Current vs shadow comparison table
        lines.append("")
        lines.append("  EXIT STRATEGY COMPARISON:")
        lines.append(f"  {'Strategy':<30} {'WR':>6} {'PF':>6} {'Expect':>8} "
                     f"{'AvgHold':>8} {'vs Current':>10}")
        lines.append("  " + "-" * 68)

        # Current first
        c = report.current_stats
        lines.append(f"  {'>> CURRENT <<':<30} {c.win_rate:>5.0%} {c.profit_factor:>6.2f} "
                     f"{c.expectancy_bps:>+7.1f}bp {c.avg_hold_sec:>7.0f}s {'baseline':>10}")

        # Shadow sorted by expectancy
        sorted_shadows = sorted(report.shadow_stats.values(),
                                key=lambda s: s.expectancy_bps, reverse=True)
        for s in sorted_shadows:
            marker = " ***" if s.name == report.best_strategy else ""
            lines.append(
                f"  {s.name:<30} {s.win_rate:>5.0%} {s.profit_factor:>6.2f} "
                f"{s.expectancy_bps:>+7.1f}bp {s.avg_hold_sec:>7.0f}s "
                f"{s.improvement_bps:>+9.1f}bp{marker}")

        # ── OOS Validation ───────────────────────────────────────
        if report.validation:
            lines.append("")
            lines.append("  OUT-OF-SAMPLE VALIDATION (70/30 split):")
            lines.append(f"  {'Strategy':<30} {'IS PF':>6} {'IS Exp':>8} "
                         f"{'OOS PF':>7} {'OOS Exp':>8} {'Degrad':>7} {'Verdict':>10}")
            lines.append("  " + "-" * 76)

            for name in sorted(report.validation,
                               key=lambda k: report.validation[k].oos_expectancy_bps,
                               reverse=True):
                v = report.validation[name]
                degrad_str = f"{v.pf_degradation:.0%}" if v.pf_degradation > 0 else "n/a"
                marker = ""
                if name == report.validated_best:
                    marker = " <<<"
                lines.append(
                    f"  {name:<30} {v.is_pf:>5.2f} {v.is_expectancy_bps:>+7.1f}bp "
                    f"{v.oos_pf:>6.2f} {v.oos_expectancy_bps:>+7.1f}bp "
                    f"{degrad_str:>7} {v.verdict:>10}{marker}")

            # Stability check
            if report.stability:
                s = report.stability
                lines.append("")
                lines.append(f"  STABILITY: {s.verdict}")
                lines.append(f"    Rank correlation: {s.rank_correlation:.2f}")
                lines.append(f"    IS top-3:  {', '.join(s.top3_is)}")
                lines.append(f"    OOS top-3: {', '.join(s.top3_oos)}")

        # ── Final Recommendation ────────────────────────────────
        lines.append("")
        if report.validated_best:
            lines.append(f"  VALIDATED RECOMMENDATION: '{report.validated_best}'")
            lines.append(f"  OOS improvement: {report.validated_improvement_bps:+.1f} bps/trade")
            v = report.validation[report.validated_best]
            lines.append(f"  OOS PF: {v.oos_pf:.2f}  OOS WR: {v.oos_win_rate:.0%}  "
                         f"OOS Expectancy: {v.oos_expectancy_bps:+.1f} bps")
        elif report.validation:
            overfit_count = sum(1 for v in report.validation.values() if v.is_overfit)
            lines.append(f"  NO VALID EXIT IMPROVEMENT FOUND")
            if overfit_count > 0:
                lines.append(f"  {overfit_count}/{len(report.validation)} strategies flagged as OVERFIT")
            lines.append("  Current exit is the best available (or all alternatives are noise)")
        elif report.best_improvement_bps > 0:
            lines.append(f"  UNVALIDATED suggestion: '{report.best_strategy}' "
                         f"(+{report.best_improvement_bps:.1f} bps)")
            lines.append(f"  WARNING: Need >= {self.MIN_TRADES_FOR_VALIDATION} trades for OOS validation "
                         f"(have {report.total_trades_analyzed})")
        else:
            lines.append("  Current exit is optimal (no shadow beats it)")

        lines.append("=" * 70)
        return "\n".join(lines)
