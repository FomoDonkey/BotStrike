"""
Portfolio Manager — Balanceo de capital entre estrategias.
Ajusta asignación dinámicamente según régimen, drawdown, rendimiento,
covarianza (Risk Parity), volatility targeting, y correlation regime.
"""
from __future__ import annotations
import math
import time
from collections import deque
from typing import Deque, Dict, Optional

from config.settings import Settings, TradingConfig
from core.types import MarketRegime, StrategyType
from core.quant_models import CovarianceTracker
from risk.risk_manager import RiskManager
import structlog

logger = structlog.get_logger(__name__)

# Mapeo de régimen → pesos ideales para cada estrategia
# Fibonacci thrives in trending (impulse→retrace pattern).
# MR thrives in ranging (pullback→reversion pattern).
# FIBONACCI_RETRACEMENT frozen at 0.00 everywhere (2026-08-31): no published
# evidence for the family (research_sota_2026 §2.7) and 20% WR / -$2.11 over
# 5 paper closes. MR weights deliberately NOT renormalized upward — MR is also
# under evidence freeze (§2.1: negative edge after costs at 1-5 min).
REGIME_WEIGHTS: Dict[MarketRegime, Dict[StrategyType, float]] = {
    MarketRegime.RANGING: {
        StrategyType.MEAN_REVERSION: 0.65,
        StrategyType.FIBONACCI_RETRACEMENT: 0.00,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.00,
    },
    MarketRegime.TRENDING_UP: {
        StrategyType.MEAN_REVERSION: 0.30,
        StrategyType.FIBONACCI_RETRACEMENT: 0.00,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.00,
    },
    MarketRegime.TRENDING_DOWN: {
        StrategyType.MEAN_REVERSION: 0.30,
        StrategyType.FIBONACCI_RETRACEMENT: 0.00,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.00,
    },
    MarketRegime.BREAKOUT: {
        StrategyType.MEAN_REVERSION: 0.10,
        StrategyType.FIBONACCI_RETRACEMENT: 0.00,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.00,
    },
    MarketRegime.UNKNOWN: {
        StrategyType.MEAN_REVERSION: 0.50,
        StrategyType.FIBONACCI_RETRACEMENT: 0.00,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.00,
    },
}

# Per-symbol strategy eligibility — based on backtest PF results.
# Only allow profitable or near-breakeven combinations.
# Key: symbol → set of allowed StrategyTypes
SYMBOL_STRATEGY_MAP: Dict[str, set] = {
    "BTC-USD": set(),                                          # FIB frozen 2026-08-31 (was PF=1.11 on 14d backtest; 20% WR live) — BTC data-only
    "ETH-USD": {StrategyType.MEAN_REVERSION},                  # MR PF=0.85 ⚠️ (Fib PF=0.58 ❌)
    "ADA-USD": {StrategyType.MEAN_REVERSION},                  # MR PF=0.86 ⚠️ (Fib PF=0.14 ❌)
    "SOL-USD": {StrategyType.MEAN_REVERSION},                   # MR only per user preference
}

# Performance factor (audit F03) — evaluated on CLOSED trades only, normalized
# in R-multiples (avg PnL / risk budget per trade), never permanent.
PERF_MIN_TRADES = 20            # closed trades required before the factor can move
PERF_WINDOW = 50                # rolling window of closed-trade PnLs
PERF_FLOOR = 0.5
PERF_CEIL = 1.5
PERF_BLOCK_THRESHOLD = 0.6      # below this should_strategy_trade() blocks entries
PERF_BLOCK_COOLDOWN_SEC = 3600  # after this the window is reset (probation) — no deadlock


class PortfolioManager:
    """Gestiona la asignación de capital entre estrategias y activos.

    Combina:
    1. Pesos base por régimen de mercado
    2. Factor de performance (sigmoid)
    3. Factor de drawdown
    4. Risk Parity por covarianza (inverse volatility weighting)
    5. Vol Targeting global (del risk manager)
    6. Correlation Stress factor (del risk manager)
    """

    def __init__(self, settings: Settings, risk_manager: RiskManager) -> None:
        self.settings = settings
        self.config = settings.trading
        self.risk_manager = risk_manager

        # Tracking de performance por estrategia
        self._strategy_pnl: Dict[StrategyType, float] = {
            st: 0.0 for st in StrategyType
        }
        self._strategy_trades: Dict[StrategyType, int] = {
            st: 0 for st in StrategyType
        }
        # Rolling window of CLOSED-trade PnLs per strategy (audit F03: entries
        # with pnl=0 must not dilute the average; performance is never permanent).
        self._strategy_closed_pnl: Dict[StrategyType, Deque[float]] = {
            st: deque(maxlen=PERF_WINDOW) for st in StrategyType
        }
        self._perf_blocked_since: Dict[StrategyType, float] = {}
        self._perf_last_logged: Dict[StrategyType, float] = {}
        self._now = time.time  # injectable clock (tests)

        # Pesos actuales (se ajustan dinámicamente)
        self._current_weights: Dict[StrategyType, float] = {
            StrategyType.MEAN_REVERSION: self.config.allocation_mean_reversion,
            StrategyType.FIBONACCI_RETRACEMENT: self.config.allocation_fibonacci_retracement,
            StrategyType.TREND_FOLLOWING: self.config.allocation_trend_following,
            StrategyType.MARKET_MAKING: self.config.allocation_market_making,
            StrategyType.ORDER_FLOW_MOMENTUM: self.config.allocation_order_flow_momentum,
        }

        # Covariance Tracker para Risk Parity
        self._cov_tracker = CovarianceTracker(
            lookback=60,
            min_periods=10,
            blend_factor=0.3,
        )

        # Track daily returns por symbol para correlacion
        self._last_prices: Dict[str, float] = {}

    def on_price_update(self, symbol: str, price: float) -> None:
        """Registra precio para calcular returns diarios (para correlacion).

        Only feeds correlation regime on day boundaries (UTC) to avoid
        inflating correlations with micro-returns from 3s strategy ticks.
        """
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        day_key = f"{symbol}_{today}"

        # Store first price of the day as anchor for daily return
        anchor_key = f"_anchor_{symbol}"
        if anchor_key not in self._last_prices or self._last_prices.get(f"_day_{symbol}") != today:
            # New day: compute yesterday's daily return from anchor → current price
            old_anchor = self._last_prices.get(anchor_key, 0)
            if old_anchor > 0 and price > 0:
                daily_ret = (price - old_anchor) / old_anchor
                self.risk_manager.correlation_regime.on_return(symbol, daily_ret)
            self._last_prices[anchor_key] = price
            self._last_prices[f"_day_{symbol}"] = today

        self._last_prices[symbol] = price

    def on_strategy_return(self, key: str, daily_return: float) -> None:
        """Registra return diario de un bucket strategy×symbol para Risk Parity."""
        self._cov_tracker.on_return(key, daily_return)

    def get_allocation(
        self,
        symbol: str,
        regime: MarketRegime,
        strategy: StrategyType,
    ) -> float:
        """Calcula capital asignado a una estrategia para un símbolo.

        Combina: pesos de régimen, performance, drawdown, Risk Parity,
        vol targeting, y correlation stress.

        Returns:
            Capital en USD asignado
        """
        equity = self.risk_manager.current_equity

        # 1. Peso base por régimen de mercado
        regime_weight = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS[MarketRegime.UNKNOWN])
        base_weight = regime_weight.get(strategy, 0.33)

        # 2. Ajuste por performance de la estrategia
        perf_factor = self._performance_factor(strategy)

        # 3. Ajuste por drawdown (reducir exposición general)
        dd = self.risk_manager.current_drawdown_pct
        dd_factor = max(0.3, 1.0 - dd * 2.0)  # reduce hasta 30% en drawdown alto

        # 4. Distribuir entre símbolos (equitativo por ahora)
        num_symbols = len(self.settings.symbols)
        symbol_share = 1.0 / num_symbols if num_symbols > 0 else 1.0

        # 5. Risk Parity blend: ajustar peso base con inverse-vol weighting
        rp_key = f"{symbol}_{strategy.value}"
        rp = self._cov_tracker.compute_risk_parity()
        if rp.weights and rp_key in rp.weights:
            # Risk Parity weights estan normalizados (sum=1 sobre todos los buckets).
            # Para comparar con base_weight (que es por estrategia, no por bucket),
            # necesitamos escalar el RP weight al mismo espacio.
            n_buckets = len(rp.weights)
            # Un weight "neutro" en RP es 1/n_buckets. Lo comparamos con base_weight.
            rp_raw = rp.weights[rp_key]
            neutral_rp = 1.0 / n_buckets if n_buckets > 0 else base_weight
            # Si RP dice que este bucket debe tener mas peso que neutral, escalar arriba
            rp_ratio = rp_raw / neutral_rp if neutral_rp > 0 else 1.0
            # Blend: 70% regime + 30% RP-adjusted
            base_weight = 0.7 * base_weight + 0.3 * base_weight * min(rp_ratio, 2.0)

        # Calcular asignación final
        allocation = equity * base_weight * perf_factor * dd_factor * symbol_share

        # Guardar peso actual
        self._current_weights[strategy] = base_weight * perf_factor * dd_factor

        return max(allocation, 0.0)

    def _risk_budget_per_trade(self) -> float:
        """USD risked per trade at current equity (normalizer for the factor)."""
        equity = self.risk_manager.current_equity
        if equity <= 0:
            equity = self.config.initial_capital
        return max(equity * self.config.risk_per_trade_pct, 1e-6)

    def _performance_factor(self, strategy: StrategyType) -> float:
        """Factor de ajuste basado en rendimiento de la estrategia.
        Rango: PERF_FLOOR (0.5, peor) a PERF_CEIL (1.5, mejor).

        Audit F03: the old formula used absolute USD (x100) so 5 fills with an
        average of -$0.03 disabled a strategy forever. Now:
        - only CLOSED trades count (rolling window of PERF_WINDOW);
        - neutral (1.0) until PERF_MIN_TRADES closed trades exist;
        - avg PnL is expressed in R-multiples of the per-trade risk budget
          (avg_r = -1 => lost a full risk budget per trade => ~0.55);
        - a WARNING is logged whenever the factor reduces allocation.
        """
        closed = self._strategy_closed_pnl.get(strategy)
        n = len(closed) if closed else 0
        if n < PERF_MIN_TRADES:
            return 1.0  # no hay suficiente historial

        avg_r = (sum(closed) / n) / self._risk_budget_per_trade()
        factor = 1.0 + 0.5 * math.tanh(1.5 * avg_r)
        factor = max(PERF_FLOOR, min(PERF_CEIL, factor))

        if factor < 1.0:
            last = self._perf_last_logged.get(strategy)
            if last is None or abs(last - factor) >= 0.05:
                logger.warning("strategy_allocation_reduced_by_performance",
                               strategy=strategy.value, factor=round(factor, 3),
                               avg_r=round(avg_r, 3), closed_trades=n)
                self._perf_last_logged[strategy] = factor
        else:
            self._perf_last_logged.pop(strategy, None)
        return factor

    def update_strategy_pnl(
        self, strategy: StrategyType, pnl: float, is_exit: Optional[bool] = None,
    ) -> None:
        """Registra PnL de un fill para ajuste de asignación.

        `is_exit` marks a closing fill; when None it is inferred from pnl != 0
        (entries are recorded with pnl=0 by both paper and live pipelines).
        Only closing fills feed the performance window.
        """
        self._strategy_pnl[strategy] = self._strategy_pnl.get(strategy, 0) + pnl
        self._strategy_trades[strategy] = self._strategy_trades.get(strategy, 0) + 1
        if is_exit is None:
            is_exit = pnl != 0
        if is_exit:
            self._strategy_closed_pnl.setdefault(
                strategy, deque(maxlen=PERF_WINDOW)
            ).append(pnl)

    def get_portfolio_summary(self) -> Dict:
        """Resumen del estado del portfolio."""
        rp = self._cov_tracker.compute_risk_parity()
        return {
            "equity": self.risk_manager.current_equity,
            "weights": {st.value: round(w, 4) for st, w in self._current_weights.items()},
            "strategy_pnl": {st.value: round(pnl, 2) for st, pnl in self._strategy_pnl.items()},
            "strategy_trades": {st.value: n for st, n in self._strategy_trades.items()},
            "risk_parity_weights": {k: round(v, 4) for k, v in rp.weights.items()},
            "risk_parity_vols": {k: round(v, 6) for k, v in rp.volatilities.items()},
            "risk": self.risk_manager.get_risk_summary(),
        }

    def should_strategy_trade(
        self,
        strategy: StrategyType,
        regime: MarketRegime,
        symbol: str = "",
    ) -> bool:
        """Determina si una estrategia debería operar dado el régimen, símbolo y performance."""
        # Per-symbol eligibility check — only allow proven combinations
        if symbol and symbol in SYMBOL_STRATEGY_MAP:
            if strategy not in SYMBOL_STRATEGY_MAP[symbol]:
                return False

        regime_weight = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS[MarketRegime.UNKNOWN])
        base_weight = regime_weight.get(strategy, 0.0)

        if base_weight < 0.08:
            return False

        # Performance gate — NEVER permanent (audit F03). A blocked strategy is
        # re-enabled on probation after PERF_BLOCK_COOLDOWN_SEC with a fresh
        # window, and every transition is logged.
        perf = self._performance_factor(strategy)
        now = self._now()
        if perf < PERF_BLOCK_THRESHOLD:
            since = self._perf_blocked_since.get(strategy)
            if since is None:
                self._perf_blocked_since[strategy] = now
                logger.warning("strategy_disabled_by_performance",
                               strategy=strategy.value, factor=round(perf, 3),
                               closed_trades=len(self._strategy_closed_pnl.get(strategy, ())),
                               cooldown_sec=PERF_BLOCK_COOLDOWN_SEC)
                return False
            if now - since >= PERF_BLOCK_COOLDOWN_SEC:
                self._strategy_closed_pnl[strategy].clear()
                self._perf_blocked_since.pop(strategy, None)
                self._perf_last_logged.pop(strategy, None)
                logger.warning("strategy_reenabled_after_cooldown",
                               strategy=strategy.value, blocked_sec=round(now - since))
                return True
            return False

        if strategy in self._perf_blocked_since:
            self._perf_blocked_since.pop(strategy, None)
            logger.info("strategy_performance_recovered",
                        strategy=strategy.value, factor=round(perf, 3))
        return True
