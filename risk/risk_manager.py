"""
Risk Manager — Control de riesgo del sistema.
Gestiona: leverage, tamaño de posición, drawdown, stop loss dinámico,
exposición máxima por activo, ajuste de inventario, Risk of Ruin,
volatility targeting, y correlation stress.
"""
from __future__ import annotations
import asyncio
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

# Risk-of-ruin pause hygiene (audit R2 risk_sizing-01). A pause held longer than the
# probation window is re-measured instead of being terminal; the state change is logged
# at most once per window so "the bot stopped trading" is visible, not buried.
ROR_PROBATION_SEC = 6 * 3600.0
ROR_LOG_EVERY_SEC = 900.0


class RiskManager:
    """Controlador central de riesgo para todo el sistema."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config = settings.trading

        # Lock para proteger estado mutable contra coroutines concurrentes.
        # asyncio es single-threaded pero interleaves en cada await.
        self._state_lock = asyncio.Lock()

        # Estado de riesgo
        self._equity_peak: float = self.config.initial_capital
        self._current_equity: float = self.config.initial_capital   # realised ledger
        # Open PnL, marked by the engine every few seconds (update_unrealized). Every limit,
        # the peak and the drawdown read realised + open: the backtester that validated the
        # profiles fed this manager mark-to-market equity on every bar, while the live engine
        # fed it fills only — so a book 20 % under water would not have tripped the 36 %
        # drawdown halt until it closed (audit 2026-09-05).
        self._unrealized: float = 0.0
        # Open PnL at the start of the day / the week: the period limits are measured
        # mark-to-market (realised since the reset + how the open book moved since then).
        self._day_start_unrealized: float = 0.0
        self._week_start_unrealized: float = 0.0
        self._unrealized_seeded: bool = False
        self._positions: Dict[str, Position] = {}
        self._daily_pnl: float = 0.0
        self._weekly_pnl: float = 0.0
        self._last_weekly_reset_key: tuple = ()   # (iso_year, iso_week) of the last reset
        self._total_pnl: float = 0.0

        # Contadores de riesgo
        self._consecutive_losses: int = 0
        self._max_consecutive_losses: int = 0
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_until: float = 0.0
        self._last_daily_reset_date: str = ""  # ISO date of last daily reset (e.g. "2026-04-03")
        self._consecutive_loss_pause: bool = False
        self._consecutive_loss_pause_until: float = 0.0
        self._drawdown_halted: bool = False  # Set by risk monitor when max drawdown exceeded

        # ── Modelos cuantitativos avanzados ──────────────────────────
        # Risk of Ruin: probabilidad de alcanzar max drawdown.
        # `risk_of_ruin` stays as the PORTFOLIO-level model (metrics/reporting);
        # gating uses one model PER STRATEGY (audit R2 risk_sizing-01) so a single
        # negative-edge strategy cannot pause the whole bot.
        self.risk_of_ruin = RiskOfRuin(
            max_drawdown_pct=self.config.max_drawdown_pct,
            throttle_threshold=self.config.ror_throttle_threshold,
            pause_threshold=self.config.ror_pause_threshold,
        )
        self._ror_by_strategy: Dict[StrategyType, RiskOfRuin] = {}
        # A pause older than this is re-measured instead of held forever (no deadlock).
        self._ror_paused_since: Dict[StrategyType, float] = {}
        self._ror_last_logged: Dict[StrategyType, float] = {}

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
        if signal.metadata:
            signal.metadata = signal.metadata.copy()

        # Exit signals ALWAYS pass — closing a position must never be blocked
        is_exit = (
            signal.metadata.get("action", "").startswith("exit")
            or signal.metadata.get("exit_reason")
        )
        if is_exit:
            return signal

        # Block new entries when max drawdown halted
        if self._drawdown_halted:
            logger.warning("signal_blocked_drawdown_halt", symbol=signal.symbol)
            return None

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

        # ── Consecutive loss circuit breaker ──────────────────────
        if self._consecutive_loss_pause:
            if time.time() < self._consecutive_loss_pause_until:
                logger.warning("consecutive_loss_pause_active",
                               symbol=signal.symbol,
                               consecutive=self._consecutive_losses,
                               remaining_sec=round(self._consecutive_loss_pause_until - time.time(), 1))
                return None
            else:
                self._consecutive_loss_pause = False
                logger.info("consecutive_loss_pause_lifted",
                            consecutive=self._consecutive_losses)

        # ── Risk of Ruin auto-throttle ────────────────────────────
        # PER STRATEGY, not global (audit R2 risk_sizing-01, P0). A strategy with a
        # negative edge yields ror=1.0 by construction, which used to pause EVERY
        # entry of EVERY strategy, permanently and silently: paused → no new fills →
        # the sample never changes → deadlock, while /api/health kept reporting OK.
        # Pausing a negative-edge strategy is correct; killing the whole bot without
        # telling anyone is not.
        ror_model = self._ror_for(signal.strategy)
        ror = ror_model.current
        if ror.should_pause and ror.sample_size >= ror_model.min_trades:
            if self._ror_probation_expired(signal.strategy):
                # Probation: drop the stale window so the model can re-measure instead
                # of staying paused forever on a sample it can no longer update.
                ror_model.reset()
                self._notify_ror(signal.strategy, "ror_probation_reset", ror)
            else:
                self._notify_ror(signal.strategy, "ror_pause_active", ror)
                return None
        elif ror.should_throttle and ror.sample_size >= ror_model.min_trades:
            signal.size_usd *= 0.5
            logger.info("ror_throttle", ror=round(ror.ror_analytical, 4),
                        strategy=signal.strategy.value, symbol=signal.symbol)

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
        # Recovery requires BOTH: cooldown elapsed AND drawdown recovered below 50% of max
        if self._circuit_breaker_active:
            cooldown_elapsed = time.time() >= self._circuit_breaker_until
            drawdown_recovered = self.current_drawdown_pct < self.config.max_drawdown_pct * 0.5
            if not cooldown_elapsed:
                logger.warning("circuit_breaker_active",
                               symbol=signal.symbol,
                               drawdown_pct=round(self.current_drawdown_pct, 4),
                               cooldown_remaining_sec=round(self._circuit_breaker_until - time.time(), 1))
                return None
            if not drawdown_recovered:
                logger.warning("circuit_breaker_drawdown_still_high",
                               symbol=signal.symbol,
                               drawdown_pct=round(self.current_drawdown_pct, 4),
                               recovery_threshold=round(self.config.max_drawdown_pct * 0.5, 4))
                return None
            # Both conditions met — deactivate
            self._circuit_breaker_active = False
            logger.info("circuit_breaker_deactivated",
                        drawdown_pct=round(self.current_drawdown_pct, 4))

        # 1a. Verificar daily loss limit (escalera de drawdown, nivel 1)
        max_daily_loss = self._mtm() * self.config.max_daily_loss_pct
        if self.daily_pnl_mtm < 0 and abs(self.daily_pnl_mtm) >= max_daily_loss:
            logger.warning("daily_loss_limit_reached",
                           daily_pnl=round(self.daily_pnl_mtm, 2), realised=round(self._daily_pnl, 2),
                           limit=round(-max_daily_loss, 2))
            return None

        # 1a'. Verificar weekly loss limit (nivel 2; restaurado desde la DB al arrancar)
        max_weekly_loss = self._mtm() * getattr(self.config, "max_weekly_loss_pct", 1.0)
        if self.weekly_pnl_mtm < 0 and abs(self.weekly_pnl_mtm) >= max_weekly_loss:
            logger.warning("weekly_loss_limit_reached",
                           weekly_pnl=round(self.weekly_pnl_mtm, 2), realised=round(self._weekly_pnl, 2),
                           limit=round(-max_weekly_loss, 2))
            return None

        # 1b. Verificar drawdown máximo
        if self._check_max_drawdown():
            logger.warning("max_drawdown_reached",
                           drawdown=self.current_drawdown_pct)
            return None

        # 1c. Verificar max concurrent positions (flash crash protection)
        max_pos = getattr(self.config, 'max_open_positions', 0)
        if max_pos > 0:
            open_count = sum(1 for p in self._positions.values() if p is not None)
            if open_count >= max_pos:
                logger.info("max_open_positions_reached",
                            symbol=signal.symbol, open=open_count, limit=max_pos)
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
        if required_margin > self._mtm() * 0.5:
            # Reducir tamaño para cumplir con margen (sin exceder límites previos)
            adjusted_size = min(adjusted_size, self._mtm() * 0.5 * max_lev)
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

        # Log total sizing pipeline result for debugging silent over-reduction
        logger.info("sizing_final",
                    symbol=signal.symbol,
                    strategy=signal.strategy.value,
                    final_size_usd=round(adjusted_size, 2),
                    dd_pct=round(self.current_drawdown_pct, 4),
                    consec_losses=self._consecutive_losses)
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
        max_exposure = self._mtm() * self.config.max_total_exposure_pct * self.config.max_leverage
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
            logger.info("position_size_rejected_symbol_limit",
                        symbol=signal.symbol,
                        current_exposure=round(current_exposure, 2),
                        max_position_usd=max_for_symbol)
            return 0.0

        size = min(size, remaining)

        # Límite por riesgo por trade (use Kelly if enough data, else default)
        kelly_pct = self.get_kelly_risk_pct(signal.strategy)
        max_risk = self._mtm() * kelly_pct
        risk_per_unit = abs(signal.entry_price - signal.stop_loss)
        # Guard entry ≈ stop: RELATIVE threshold (audit R2 risk_sizing-01). The old
        # absolute `< 0.001` was in PRICE units — on ADA at $0.20 it demanded a
        # 50 bps stop distance, which MR's 2×ATR SL (~39 bps) never cleared, so
        # the entire symbol was silently inoperable (0 ADA trades in the paper DB).
        # 1e-5 = 0.1 bps of entry price — only rejects truly degenerate signals.
        if signal.entry_price <= 0 or risk_per_unit / signal.entry_price < 1e-5:
            logger.warning("risk_bypass_rejected",
                           symbol=signal.symbol,
                           strategy=signal.strategy.value,
                           entry_price=signal.entry_price,
                           stop_loss=signal.stop_loss,
                           reason="risk_per_unit_zero_or_negligible")
            return 0.0
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

    def update_equity(self, equity: float, timestamp: float = 0.0,
                      unrealized: Optional[float] = None) -> None:
        """Actualiza equity actual y peak.

        Thread-safe via _state_lock cuando se llama desde async context.
        Sync callers (backtester) pueden llamar directamente — single-threaded.
        """
        self._current_equity = equity
        # A fill moves open PnL onto the ledger: the caller hands over the open PnL that remains,
        # or the peak would be read off the ledger PLUS the PnL just realised (5 s of double count).
        if unrealized is not None and unrealized == unrealized:
            self._unrealized = float(unrealized)
        if self._mtm() > self._equity_peak:
            self._equity_peak = self._mtm()

        # Alimentar volatility targeting
        self.vol_targeting.on_equity_update(self._mtm(), timestamp or time.time())
        self._arm_circuit_breaker_if_severe()

    def update_unrealized(self, unrealized: float) -> None:
        """Mark the open positions: the peak, the drawdown, the circuit breaker and every
        limit see realised + open from here on, as they do in the backtester."""
        try:
            u = float(unrealized)
        except (TypeError, ValueError):
            return
        if u != u:                                   # NaN
            return
        self._unrealized = u
        if not self._unrealized_seeded:
            self._unrealized_seeded = True
            self._day_start_unrealized = u
            self._week_start_unrealized = u
        if self._mtm() > self._equity_peak:
            self._equity_peak = self._mtm()
        self._arm_circuit_breaker_if_severe()

    def raise_peak(self, peak: float) -> None:
        """A mark-to-market peak remembered across restarts (data/risk_peak.json): the trade chain
        only knows the realised peak, so a restart used to measure the drawdown from a lower high."""
        try:
            v = float(peak)
        except (TypeError, ValueError):
            return
        if v == v and v > self._equity_peak:
            self._equity_peak = v

    async def update_unrealized_safe(self, unrealized: float) -> None:
        async with self._state_lock:
            self.update_unrealized(unrealized)

    def _arm_circuit_breaker_if_severe(self) -> None:
        # Activar circuit breaker si drawdown es severo (>80% of max)
        if self.current_drawdown_pct > self.config.max_drawdown_pct * 0.8:
            if not self._circuit_breaker_active:
                # Only set timer on FIRST activation — don't keep resetting it
                logger.warning("circuit_breaker_triggered",
                               drawdown_pct=round(self.current_drawdown_pct, 4),
                               trigger_threshold=round(self.config.max_drawdown_pct * 0.8, 4),
                               recovery_threshold=round(self.config.max_drawdown_pct * 0.5, 4),
                               equity=round(self._current_equity, 2),
                               equity_peak=round(self._equity_peak, 2),
                               cooldown_sec=300)
                self._circuit_breaker_active = True
                self._circuit_breaker_until = time.time() + 300  # 5 min cooldown

    async def update_equity_safe(self, equity: float, timestamp: float = 0.0,
                                 unrealized: Optional[float] = None) -> None:
        """Async-safe version of update_equity — acquires lock."""
        async with self._state_lock:
            self.update_equity(equity, timestamp, unrealized=unrealized)

    def update_position(self, symbol: str, position: Optional[Position]) -> None:
        """Actualiza posición registrada."""
        if position is None or position.size == 0:
            self._positions.pop(symbol, None)
        else:
            self._positions[symbol] = position

    async def update_position_safe(self, symbol: str, position: Optional[Position]) -> None:
        """Async-safe version of update_position — acquires lock."""
        async with self._state_lock:
            self.update_position(symbol, position)

    def record_trade_result(self, pnl: float, strategy: Optional[StrategyType] = None) -> None:
        """Registra resultado de trade para tracking de riesgo."""
        self._daily_pnl += pnl
        self._weekly_pnl += pnl
        self._total_pnl += pnl
        if pnl < 0:
            self._consecutive_losses += 1
            self._max_consecutive_losses = max(
                self._max_consecutive_losses, self._consecutive_losses
            )
            # Consecutive loss circuit breaker: pause after 4+ losses
            if self._consecutive_losses >= 4 and not self._consecutive_loss_pause:
                self._consecutive_loss_pause = True
                # Escalating cooldown: 5min at 4 losses, 15min at 5, 30min at 6+
                cooldown = min(300 * (2 ** (self._consecutive_losses - 4)), 1800)
                self._consecutive_loss_pause_until = time.time() + cooldown
                logger.warning("consecutive_loss_pause",
                               consecutive=self._consecutive_losses,
                               cooldown_sec=cooldown)
        elif pnl > 0:
            self._consecutive_losses = 0
            self._consecutive_loss_pause = False
        # pnl == 0 (break-even / entry fills): no afecta el contador

        # Alimentar modelos cuantitativos
        self.risk_of_ruin.record_trade(pnl)
        if strategy and strategy in self.kelly:
            self.kelly[strategy].record_trade(pnl)
        # Recalcular Risk of Ruin (portfolio + la estrategia que cerró el trade)
        self.risk_of_ruin.compute(self._mtm())
        if strategy is not None:
            model = self._ror_for(strategy)
            model.record_trade(pnl)
            model.compute(self._mtm())

    def record_cash_flow(self, amount: float) -> None:
        """A settlement that is not a trade: funding, a rebate, a transfer.

        It moves the day, the week and the total like a closed trade does, and nothing else — the
        hourly funding of six long positions used to be fed through record_trade_result, so four
        negative settlements in a row read as four losing TRADES and armed the consecutive-loss
        pause (Activity feed, 2026-09-04 17:00Z), while the risk-of-ruin model counted them as trades.
        """
        self._daily_pnl += amount
        self._weekly_pnl += amount
        self._total_pnl += amount

    async def record_cash_flow_safe(self, amount: float) -> None:
        async with self._state_lock:
            self.record_cash_flow(amount)

    async def record_trade_result_safe(self, pnl: float, strategy: Optional[StrategyType] = None) -> None:
        """Async-safe version of record_trade_result — acquires lock."""
        async with self._state_lock:
            self.record_trade_result(pnl, strategy)

    # ── Risk of Ruin, per strategy (audit R2 risk_sizing-01) ─────────
    def _ror_for(self, strategy: Optional[StrategyType]) -> RiskOfRuin:
        """One model per strategy, created on first use with the same config."""
        key = strategy if strategy is not None else StrategyType.MEAN_REVERSION
        model = self._ror_by_strategy.get(key)
        if model is None:
            model = RiskOfRuin(
                max_drawdown_pct=self.config.max_drawdown_pct,
                throttle_threshold=self.config.ror_throttle_threshold,
                pause_threshold=self.config.ror_pause_threshold,
            )
            self._ror_by_strategy[key] = model
        return model

    def _ror_probation_expired(self, strategy: Optional[StrategyType]) -> bool:
        """True once a pause has lasted longer than the probation window.

        Without this the pause is terminal: no entries → no closed trades → the
        pnl window never changes → `should_pause` stays True forever.
        """
        key = strategy if strategy is not None else StrategyType.MEAN_REVERSION
        now = time.time()
        started = self._ror_paused_since.get(key)
        if started is None:
            self._ror_paused_since[key] = now
            return False
        return (now - started) >= ROR_PROBATION_SEC

    def _notify_ror(self, strategy: Optional[StrategyType], event: str, ror) -> None:
        """Log a RoR state change ONCE per throttle window, not once per rejected
        signal (the old code logged on every signal, which reads as noise and hid
        the fact that the bot had stopped trading entirely)."""
        key = strategy if strategy is not None else StrategyType.MEAN_REVERSION
        now = time.time()
        if event == "ror_probation_reset":
            self._ror_paused_since.pop(key, None)
            self._ror_last_logged.pop(key, None)
            logger.warning(event, strategy=key.value,
                           ror=round(ror.ror_analytical, 4), edge=round(ror.edge, 4),
                           hint="pause exceeded probation; window reset to re-measure")
            return
        last = self._ror_last_logged.get(key, 0.0)
        if now - last >= ROR_LOG_EVERY_SEC:
            self._ror_last_logged[key] = now
            logger.error(event, strategy=key.value,
                         ror=round(ror.ror_analytical, 4), edge=round(ror.edge, 4),
                         sample=ror.sample_size,
                         hint="strategy paused by risk-of-ruin: NO entries are being taken")

    def reset_daily(self) -> None:
        """Reset de métricas diarias."""
        self._daily_pnl = 0.0

    def check_daily_reset(self) -> None:
        """Auto-reset daily PnL at UTC midnight and weekly PnL on Monday 00:00 UTC.
        Robust: uses date comparison, not exact timing.

        Protected by _state_lock when called via check_daily_reset_safe().
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        today_utc = now.strftime("%Y-%m-%d")
        if self._last_daily_reset_date != today_utc:
            old_pnl = self._daily_pnl
            self.reset_daily()
            self._day_start_unrealized = self._unrealized
            self._last_daily_reset_date = today_utc
            logger.info("daily_pnl_auto_reset",
                        previous_daily_pnl=round(old_pnl, 2),
                        new_date=today_utc)
        week_key = tuple(now.isocalendar()[:2])
        if self._last_weekly_reset_key and self._last_weekly_reset_key != week_key:
            old_w = self._weekly_pnl
            self._weekly_pnl = 0.0
            self._week_start_unrealized = self._unrealized
            logger.info("weekly_pnl_auto_reset", previous_weekly_pnl=round(old_w, 2),
                        new_week=f"{week_key[0]}-W{week_key[1]:02d}")
        self._last_weekly_reset_key = week_key

    def restore_history(self, equity: float, peak: float, daily_pnl: float,
                        weekly_pnl: float) -> None:
        """Seed the persisted risk state at startup (risk/persistence.py). The daily
        and weekly reset keys are set to NOW so the restored amounts are not wiped by
        the first check_daily_reset() of the session."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        self._current_equity = float(equity)
        self._equity_peak = max(float(peak), float(equity))
        self._daily_pnl = float(daily_pnl)
        self._weekly_pnl = float(weekly_pnl)
        self._last_daily_reset_date = now.strftime("%Y-%m-%d")
        self._last_weekly_reset_key = tuple(now.isocalendar()[:2])
        self.vol_targeting.on_equity_update(self._current_equity, time.time())

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def weekly_pnl(self) -> float:
        return self._weekly_pnl

    @property
    def daily_pnl_mtm(self) -> float:
        """Today's PnL as a venue shows it: realised since 00:00 UTC plus how the open book moved."""
        return self._daily_pnl + (self._unrealized - self._day_start_unrealized)

    @property
    def weekly_pnl_mtm(self) -> float:
        return self._weekly_pnl + (self._unrealized - self._week_start_unrealized)

    @property
    def equity_peak(self) -> float:
        return self._equity_peak

    async def check_daily_reset_safe(self) -> None:
        """Async-safe version of check_daily_reset — acquires lock."""
        async with self._state_lock:
            self.check_daily_reset()

    @property
    def current_drawdown_pct(self) -> float:
        if self._equity_peak == 0:
            return 0.0
        return (self._equity_peak - self._mtm()) / self._equity_peak

    @property
    def current_equity(self) -> float:
        """Mark-to-market: realised ledger + open PnL (what every limit is measured against)."""
        return self._mtm()

    @property
    def realized_equity(self) -> float:
        """The realised ledger alone — what a fill adds its PnL to, and what the trade DB chains."""
        return self._current_equity

    def _mtm(self) -> float:
        return self._current_equity + self._unrealized

    @property
    def is_circuit_breaker_active(self) -> bool:
        """Public API for checking circuit breaker state.

        Circuit breaker deactivates only when BOTH:
        1. Cooldown period has elapsed
        2. Drawdown has recovered below 50% of max_drawdown_pct
        """
        if self._circuit_breaker_active:
            cooldown_elapsed = time.time() >= self._circuit_breaker_until
            drawdown_recovered = self.current_drawdown_pct < self.config.max_drawdown_pct * 0.5
            if cooldown_elapsed and drawdown_recovered:
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
            "equity": self._mtm(),
            "equity_peak": self._equity_peak,
            "drawdown_pct": round(self.current_drawdown_pct, 4),
            "total_exposure": self.total_exposure,
            "daily_pnl": self.daily_pnl_mtm,
            "daily_pnl_realised": self._daily_pnl,
            "max_daily_loss": round(self._mtm() * self.config.max_daily_loss_pct, 2),
            "weekly_pnl": self.weekly_pnl_mtm,
            "weekly_pnl_realised": self._weekly_pnl,
            "max_weekly_loss": round(
                self._mtm() * getattr(self.config, "max_weekly_loss_pct", 1.0), 2),
            "drawdown_halted": self._drawdown_halted,
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
