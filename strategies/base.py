"""
Clase base abstracta para todas las estrategias de trading.
Define la interfaz común y utilidades compartidas.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional

import pandas as pd

from config.settings import SymbolConfig, TradingConfig
from core.types import Signal, MarketRegime, MarketSnapshot, StrategyType, Position
import structlog

logger = structlog.get_logger(__name__)


class BaseStrategy(ABC):
    """Interfaz base para estrategias de trading."""

    def __init__(
        self,
        strategy_type: StrategyType,
        trading_config: TradingConfig,
    ) -> None:
        self.strategy_type = strategy_type
        self.trading_config = trading_config
        self.active = True
        # PnL acumulado por estrategia para tracking
        self.total_pnl: float = 0.0
        self.win_count: int = 0
        self.loss_count: int = 0

    @abstractmethod
    def generate_signals(
        self,
        symbol: str,
        df: pd.DataFrame,
        snapshot: MarketSnapshot,
        regime: MarketRegime,
        sym_config: SymbolConfig,
        allocated_capital: float,
        current_position: Optional[Position],
        **kwargs,
    ) -> List[Signal]:
        """Genera señales de trading basándose en datos actuales.

        Args:
            symbol: Símbolo del activo
            df: DataFrame OHLCV con indicadores
            snapshot: Snapshot actual del mercado
            regime: Régimen detectado
            sym_config: Configuración del símbolo
            allocated_capital: Capital asignado a esta estrategia para este símbolo
            current_position: Posición abierta actual (si existe)
            **kwargs: Extensiones (e.g., micro=MicrostructureSnapshot)

        Returns:
            Lista de señales generadas
        """
        ...

    @abstractmethod
    def should_activate(self, regime: MarketRegime) -> bool:
        """Determina si la estrategia debe estar activa dado el régimen."""
        ...

    def update_pnl(self, pnl: float) -> None:
        """Registra un PnL realizado."""
        self.total_pnl += pnl
        if pnl > 0:
            self.win_count += 1
        elif pnl < 0:
            self.loss_count += 1

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / total if total > 0 else 0.0

    def _calc_position_size(
        self, capital: float, price: float, stop_loss: float, leverage: int = 1,
        kelly_risk_pct: Optional[float] = None,
    ) -> float:
        """Position sizing accounting for round-trip fees and slippage.

        Formula: size = (capital * risk_pct - friction_cost) / |price - stop_loss|
        Enforces minimum $20 notional to be economically viable.
        """
        risk_pct = kelly_risk_pct if kelly_risk_pct is not None else self.trading_config.risk_per_trade_pct
        risk_amount = capital * risk_pct

        # Deduct estimated round-trip friction from risk budget
        # Entry slippage + exit slippage + entry fee + exit fee
        friction_bps = (
            self.trading_config.slippage_bps * 2  # entry + exit slippage
            + self.trading_config.taker_fee * 10_000  # entry taker fee in bps
            + self.trading_config.taker_fee * 10_000  # exit taker fee in bps
        )
        # Estimate friction cost on expected position size
        estimated_notional = capital * risk_pct * 10  # rough estimate
        friction_cost = estimated_notional * friction_bps / 10_000
        adjusted_risk = max(risk_amount - friction_cost, risk_amount * 0.5)

        risk_per_unit = abs(price - stop_loss)
        if risk_per_unit == 0 or price <= 0:
            return 0.0
        size_units = adjusted_risk / risk_per_unit
        # Cap by leveraged capital
        max_units = (capital * leverage) / price
        final_size = min(size_units, max_units)

        # Minimum viable position: $20 notional to cover fees
        min_notional = 20.0
        if final_size * price < min_notional:
            return 0.0

        return final_size
