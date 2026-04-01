"""
Risk Manager — Control de riesgo del sistema.
Gestiona: leverage, tamaño de posición, drawdown, stop loss dinámico,
exposición máxima por activo, ajuste de inventario, Risk of Ruin,
volatility targeting, y correlation stress.
"""
from __future__ import annotations
import copy
import time
from typing import Dict, Optional

from config.settings import Settings, SymbolConfig
from core.types import Signal, Position, Side, StrategyType, MarketRegime
from core.microstructure import MicrostructureSnapshot
from core.quant_models import (
    RiskOfRuin,
    VolatilityTargeting,
    KellyCriterion,
    SlippageTracker,
    CorrelationRegime,
)
import structlog

logger = structlog.get_logger(__name__)


class RiskManager:
    """Controlador central de riesgo para todo el sistema."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config = settings.trading

        # Estado de riesgo
        self._equity_peak: float = self.config.initial_capital
        self._current_equity: float = self.config.initial_capital
        self._positions: Dict[str, Position] = {}
        self._daily_pnl: float = 0.0
        self._total_pnl: float = 0.0

        # Contadores de riesgo
        self._consecutive_losses: int = 0
        self._max_consecutive_losses: int = 0
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_until: float = 0.0

        # ── Modelos cuantitativos avanzados ──────────────────────────
        # Risk of Ruin: probabilidad de alcanzar max drawdown
        self.risk_of_ruin = RiskOfRuin(
            max_drawdown_pct=self.config.max_drawdown_pct,
            throttle_threshold=self.config.ror_throttle_threshold,
            pause_threshold=self.config.ror_pause_threshold,
        )

        # Volatility Targeting: escalar posiciones para vol constante
        self.vol_targeting = VolatilityTargeting(
            target_vol=self.config.vol_target_annual,
            lookback_days=self.config.vol_target_lookback_days,
            min_scalar=self.config.vol_target_min_scalar,
            max_scalar=self.config.vol_target_max_scalar,
        )

        # Kelly Criterion: sizing optimo por estrategia
        self.kelly: Dict[StrategyType, KellyCriterion] = {
            st: KellyCriterion(
                min_trades=self.config.kelly_min_trades,
                floor_pct=self.config.kelly_floor_pct,
                ceiling_pct=self.config.kelly_ceiling_pct,
                default_risk_pct=self.config.risk_per_trade_pct,
            )
            for st in StrategyType
        }

        # Slippage Tracker: medicion real de slippage
        self.slippage_tracker = SlippageTracker()

        # Correlation Regime: detecta stress de correlacion
        self.correlation_regime = CorrelationRegime(
            stress_threshold=self.config.corr_stress_threshold,
            lookback_periods=self.config.corr_lookback_periods,
        )

    # ── Validación de señales ──────────────────────────────────────

    def validate_signal(
        self,
        signal: Signal,
        sym_config: SymbolConfig,
        regime: MarketRegime,
        micro: Optional[MicrostructureSnapshot] = None,
        funding_rate: float = 0.0,
    ) -> Optional[Signal]:
        """Valida y ajusta una señal antes de ejecutarla.

        Retorna None si la señal es rechazada por riesgo.
        Retorna señal ajustada si pasa los filtros.

        Args:
            micro: Snapshot de microestructura (VPIN, Hawkes). Si disponible,
                   aplica filtros adicionales de flujo tóxico y spikes de actividad.
        """
        # Trabajar sobre copia para no mutar señal original si es rechazada
        signal = copy.copy(signal)

        # ── Filtro de microestructura (VPIN + Hawkes) ─────────────
        # VPIN alto para MR: no entrar (flujo informado puede romper reversión)
        # Hawkes spike para MR/MM: reducir tamaño o bloquear
        if micro is not None:
            # Divergence signals are high-quality (triple confirmation) — skip VPIN/Hawkes block
            is_divergence = signal.metadata.get("trigger", "").endswith("_divergence")
            if signal.strategy == StrategyType.MEAN_REVERSION and micro.should_filter_mr and not is_divergence:
                logger.info("mr_blocked_by_microstructure", symbol=signal.symbol,
                            vpin=round(micro.vpin.vpin, 3),
                            hawkes=round(micro.hawkes.spike_ratio, 2))
                return None

            # Reducir sizing si hay riesgo de microestructura moderado
            if micro.risk_score > 0.5:
                size_factor = 1.0 - micro.risk_score * 0.3
                signal.size_usd *= max(size_factor, 0.4)

            # ── Kyle Lambda impact stress ──────────────────────────
            # Si el impacto permanente estimado supera el edge esperado, reducir o bloquear
            kl = micro.kyle_lambda
            if kl.is_valid and kl.impact_stress > 0:
                stress_threshold = self.config.impact_stress_threshold
                if kl.impact_stress >= stress_threshold * 2.5:  # más permisivo
                    # Impacto extremo → bloquear trade (no MM que tiene su propia lógica)
                    if signal.strategy != StrategyType.MARKET_MAKING:
                        logger.warning("impact_stress_block", symbol=signal.symbol,
                                       lambda_ema=round(kl.kyle_lambda_ema, 4),
                                       stress=round(kl.impact_stress, 2))
                        return None
                elif kl.impact_stress > stress_threshold * 0.625:  # ~0.5 at default 0.8
                    # Impacto moderado → reducir sizing proporcionalmente
                    impact_factor = 1.0 - min(kl.impact_stress * 0.3, 0.5)
                    signal.size_usd *= impact_factor

        # ── Filtro de funding rate ──────────────────────────────
        # Funding positivo = longs pagan shorts. Funding negativo = shorts pagan longs.
        # No entrar en la dirección que paga si funding es extremo.
        if funding_rate != 0 and signal.strategy != StrategyType.MARKET_MAKING:
            funding_against = (
                (signal.side == Side.BUY and funding_rate > 0) or
                (signal.side == Side.SELL and funding_rate < 0)
            )
            if funding_against:
                abs_rate = abs(funding_rate)
                if abs_rate >= self.config.funding_rate_block:
                    logger.info("signal_blocked_funding", symbol=signal.symbol,
                                side=signal.side.value, funding_rate=round(funding_rate, 6))
                    return None
                elif abs_rate >= self.config.funding_rate_warn:
                    # Reducir sizing proporcional al funding
                    funding_penalty = 1.0 - min(abs_rate / self.config.funding_rate_block, 0.7) if self.config.funding_rate_block > 0 else 0.3
                    signal.size_usd *= funding_penalty
                    logger.info("size_reduced_funding", symbol=signal.symbol,
                                funding_rate=round(funding_rate, 6),
                                penalty=round(funding_penalty, 2))

        # ── Risk of Ruin auto-throttle ────────────────────────────
        ror = self.risk_of_ruin.current
        if ror.should_pause and ror.sample_size >= self.risk_of_ruin.min_trades:
            logger.warning("ror_pause_active", ror=round(ror.ror_analytical, 4),
                           symbol=signal.symbol)
            return None
        if ror.should_throttle and ror.sample_size >= self.risk_of_ruin.min_trades:
            signal.size_usd *= 0.5
            logger.info("ror_throttle", ror=round(ror.ror_analytical, 4),
                        symbol=signal.symbol)

        # ── Volatility Targeting scalar ──────────────────────────────
        vol_scalar = self.vol_targeting.scalar
        if vol_scalar != 1.0:
            signal.size_usd *= vol_scalar

        # ── Correlation stress reduction ─────────────────────────────
        corr_result = self.correlation_regime.current
        if corr_result.is_stress:
            signal.size_usd *= corr_result.stress_factor
            logger.info("corr_stress_reduction", avg_corr=round(corr_result.avg_correlation, 3),
                        factor=round(corr_result.stress_factor, 3), symbol=signal.symbol)

        # Circuit breaker: pausa trading tras drawdown severo
        if self._circuit_breaker_active:
            if time.time() < self._circuit_breaker_until:
                logger.warning("circuit_breaker_active", symbol=signal.symbol)
                return None
            self._circuit_breaker_active = False

        # 1. Verificar drawdown máximo
        if self._check_max_drawdown():
            logger.warning("max_drawdown_reached",
                           drawdown=self.current_drawdown_pct)
            return None

        # 2. Verificar exposición total
        if self._check_total_exposure(signal):
            logger.warning("max_exposure_reached", symbol=signal.symbol)
            return None

        # 3. Verificar exposición por activo
        adjusted_size = self._adjust_position_size(signal, sym_config)
        if adjusted_size <= 0:
            return None

        # 4. Verificar leverage
        max_lev = min(sym_config.leverage, self.config.max_leverage)
        position_value = adjusted_size
        required_margin = position_value / max_lev
        if required_margin > self._current_equity * 0.5:
            # Reducir tamaño para cumplir con margen (sin exceder límites previos)
            adjusted_size = min(adjusted_size, self._current_equity * 0.5 * max_lev)
            logger.info("size_reduced_margin", symbol=signal.symbol,
                        new_size=adjusted_size)

        # 5. Ajustar stop loss dinámico según volatilidad y drawdown
        signal = self._adjust_stop_loss(signal, sym_config)

        # 6. Reducir tamaño tras pérdidas consecutivas
        if self._consecutive_losses >= 4:
            reduction = 0.5 ** (self._consecutive_losses - 3)
            adjusted_size *= reduction
            logger.info("size_reduced_losses",
                        consecutive=self._consecutive_losses,
                        reduction=reduction)

        # Aplicar tamaño final (todas las reducciones ya están en adjusted_size)
        signal.size_usd = adjusted_size
        return signal

    def _check_max_drawdown(self) -> bool:
        """Verifica si se alcanzó el drawdown máximo permitido."""
        return self.current_drawdown_pct >= self.config.max_drawdown_pct

    def _check_total_exposure(self, signal: Signal) -> bool:
        """Verifica exposición total del portafolio.

        Uses notional but max_exposure accounts for leverage:
        max_exposure = equity * max_total_exposure_pct * max_leverage
        With $300, 60% exposure, 5x leverage: max = $900 notional.
        """
        total_exposure = sum(p.notional for p in self._positions.values())
        max_exposure = self._current_equity * self.config.max_total_exposure_pct * self.config.max_leverage
        return (total_exposure + signal.size_usd) > max_exposure

    def _adjust_position_size(
        self, signal: Signal, sym_config: SymbolConfig
    ) -> float:
        """Ajusta el tamaño de posición según límites de riesgo."""
        size = signal.size_usd

        # Límite por activo
        current_exposure = 0.0
        pos = self._positions.get(signal.symbol)
        if pos:
            current_exposure = pos.notional

        max_for_symbol = sym_config.max_position_usd
        remaining = max_for_symbol - current_exposure
        if remaining <= 0:
            return 0.0

        size = min(size, remaining)

        # Límite por riesgo por trade
        max_risk = self._current_equity * self.config.risk_per_trade_pct
        risk_per_unit = abs(signal.entry_price - signal.stop_loss)
        if risk_per_unit > 0 and signal.entry_price > 0:
            max_size_by_risk = (max_risk / risk_per_unit) * signal.entry_price
            size = min(size, max_size_by_risk)

        return size

    def _adjust_stop_loss(
        self, signal: Signal, sym_config: SymbolConfig
    ) -> Signal:
        """Ajusta stop loss dinámicamente según condiciones."""
        # Tighten stop loss en drawdown alto
        dd = self.current_drawdown_pct
        if dd > self.config.max_drawdown_pct * 0.5:
            tightening = 1.0 - (dd / self.config.max_drawdown_pct) * 0.3
            if signal.side == Side.BUY:
                risk = signal.entry_price - signal.stop_loss
                signal.stop_loss = signal.entry_price - risk * tightening
            else:
                risk = signal.stop_loss - signal.entry_price
                signal.stop_loss = signal.entry_price + risk * tightening
        return signal

    # ── Estado del portafolio ──────────────────────────────────────

    def update_equity(self, equity: float, timestamp: float = 0.0) -> None:
        """Actualiza equity actual y peak."""
        self._current_equity = equity
        if equity > self._equity_peak:
            self._equity_peak = equity

        # Alimentar volatility targeting
        self.vol_targeting.on_equity_update(equity, timestamp or time.time())

        # Activar circuit breaker si drawdown es severo
        if self.current_drawdown_pct > self.config.max_drawdown_pct * 0.8:
            self._circuit_breaker_active = True
            self._circuit_breaker_until = time.time() + 300  # 5 min pausa
            logger.warning("circuit_breaker_triggered",
                           drawdown=self.current_drawdown_pct)

    def update_position(self, symbol: str, position: Optional[Position]) -> None:
        """Actualiza posición registrada."""
        if position is None or position.size == 0:
            self._positions.pop(symbol, None)
        else:
            self._positions[symbol] = position

    def record_trade_result(self, pnl: float, strategy: Optional[StrategyType] = None) -> None:
        """Registra resultado de trade para tracking de riesgo."""
        self._daily_pnl += pnl
        self._total_pnl += pnl
        if pnl < 0:
            self._consecutive_losses += 1
            self._max_consecutive_losses = max(
                self._max_consecutive_losses, self._consecutive_losses
            )
        elif pnl > 0:
            self._consecutive_losses = 0
        # pnl == 0 (break-even / entry fills): no afecta el contador

        # Alimentar modelos cuantitativos
        self.risk_of_ruin.record_trade(pnl)
        if strategy and strategy in self.kelly:
            self.kelly[strategy].record_trade(pnl)
        # Recalcular Risk of Ruin
        self.risk_of_ruin.compute(self._current_equity)

    def reset_daily(self) -> None:
        """Reset de métricas diarias."""
        self._daily_pnl = 0.0

    @property
    def current_drawdown_pct(self) -> float:
        if self._equity_peak == 0:
            return 0.0
        return (self._equity_peak - self._current_equity) / self._equity_peak

    @property
    def current_equity(self) -> float:
        return self._current_equity

    @property
    def is_circuit_breaker_active(self) -> bool:
        """Public API for checking circuit breaker state."""
        if self._circuit_breaker_active and time.time() >= self._circuit_breaker_until:
            self._circuit_breaker_active = False
        return self._circuit_breaker_active

    @property
    def total_exposure(self) -> float:
        return sum(p.notional for p in self._positions.values())

    @property
    def exposure_by_symbol(self) -> Dict[str, float]:
        return {s: p.notional for s, p in self._positions.items()}

    def get_kelly_risk_pct(self, strategy: StrategyType) -> float:
        """Retorna fraccion de riesgo Kelly para una estrategia (o default)."""
        kelly = self.kelly.get(strategy)
        if kelly:
            return kelly.risk_fraction
        return self.config.risk_per_trade_pct

    def get_risk_summary(self) -> Dict:
        """Resumen del estado de riesgo."""
        ror = self.risk_of_ruin.current
        vol = self.vol_targeting.current
        corr = self.correlation_regime.current
        slip = self.slippage_tracker.get_stats()

        return {
            "equity": self._current_equity,
            "equity_peak": self._equity_peak,
            "drawdown_pct": round(self.current_drawdown_pct, 4),
            "total_exposure": self.total_exposure,
            "daily_pnl": self._daily_pnl,
            "total_pnl": self._total_pnl,
            "consecutive_losses": self._consecutive_losses,
            "circuit_breaker": self._circuit_breaker_active,
            "positions": {s: p.notional for s, p in self._positions.items()},
            # Quant models
            "risk_of_ruin": round(ror.ror_analytical, 4),
            "ror_edge": round(ror.edge, 4),
            "ror_throttle": ror.should_throttle,
            "vol_target_scalar": round(vol.scalar, 3),
            "vol_realized": round(vol.realized_vol, 4),
            "correlation_stress": corr.is_stress,
            "avg_correlation": round(corr.avg_correlation, 3),
            "corr_stress_factor": round(corr.stress_factor, 3),
            "slippage_avg_bps": round(slip.avg_slippage_bps, 2),
            "slippage_samples": slip.sample_size,
            "kelly_fractions": {
                st.value: round(self.get_kelly_risk_pct(st), 4)
                for st in StrategyType
            },
        }
