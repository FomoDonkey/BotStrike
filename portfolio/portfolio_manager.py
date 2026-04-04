"""
Portfolio Manager — Balanceo de capital entre estrategias.
Ajusta asignación dinámicamente según régimen, drawdown, rendimiento,
covarianza (Risk Parity), volatility targeting, y correlation regime.
"""
from __future__ import annotations
import math
from typing import Dict, Optional

from config.settings import Settings, TradingConfig
from core.types import MarketRegime, StrategyType
from core.quant_models import CovarianceTracker
from risk.risk_manager import RiskManager
import structlog

logger = structlog.get_logger(__name__)

# Mapeo de régimen → pesos ideales para cada estrategia
REGIME_WEIGHTS: Dict[MarketRegime, Dict[StrategyType, float]] = {
    MarketRegime.RANGING: {
        StrategyType.MEAN_REVERSION: 0.50,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.50,
    },
    MarketRegime.TRENDING_UP: {
        StrategyType.MEAN_REVERSION: 0.20,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.80,
    },
    MarketRegime.TRENDING_DOWN: {
        StrategyType.MEAN_REVERSION: 0.20,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.80,
    },
    MarketRegime.BREAKOUT: {
        StrategyType.MEAN_REVERSION: 0.10,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.90,
    },
    MarketRegime.UNKNOWN: {
        StrategyType.MEAN_REVERSION: 0.40,
        StrategyType.TREND_FOLLOWING: 0.00,
        StrategyType.MARKET_MAKING: 0.00,
        StrategyType.ORDER_FLOW_MOMENTUM: 0.60,
    },
}


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

        # Pesos actuales (se ajustan dinámicamente)
        self._current_weights: Dict[StrategyType, float] = {
            StrategyType.MEAN_REVERSION: self.config.allocation_mean_reversion,
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

    def _performance_factor(self, strategy: StrategyType) -> float:
        """Factor de ajuste basado en rendimiento de la estrategia.
        Rango: 0.5 (peor) a 1.5 (mejor).
        """
        pnl = self._strategy_pnl.get(strategy, 0)
        trades = self._strategy_trades.get(strategy, 0)

        if trades < 5:
            return 1.0  # no hay suficiente historial

        avg_pnl = pnl / trades
        # Normalizar: si avg_pnl es positivo, aumentar; si negativo, reducir
        # Usamos una función sigmoide suave
        exp_val = max(-500, min(500, -avg_pnl * 100))
        factor = 1.0 + 0.5 * (2.0 / (1.0 + math.e ** exp_val) - 1.0)
        return max(0.5, min(1.5, factor))

    def update_strategy_pnl(self, strategy: StrategyType, pnl: float) -> None:
        """Registra PnL de un trade para ajuste de asignación."""
        self._strategy_pnl[strategy] = self._strategy_pnl.get(strategy, 0) + pnl
        self._strategy_trades[strategy] = self._strategy_trades.get(strategy, 0) + 1

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
    ) -> bool:
        """Determina si una estrategia debería operar dado el régimen y su performance."""
        regime_weight = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS[MarketRegime.UNKNOWN])
        base_weight = regime_weight.get(strategy, 0.33)

        # Si el peso es muy bajo, no operar
        if base_weight < 0.08:
            return False

        # Si la estrategia está perdiendo mucho, reducir actividad
        perf = self._performance_factor(strategy)
        if perf < 0.6:
            return False

        return True
