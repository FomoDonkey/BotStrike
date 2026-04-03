"""
Modelos de datos para el Trade Database.

TradeRecord extiende el concepto de Trade existente (core/types.py) con
contexto adicional necesario para análisis posterior: régimen de mercado,
equity después del trade, sesión de trading, y métricas de microestructura.

No modifica ni reemplaza core.types.Trade — es una representación de
almacenamiento separada.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time
import uuid


@dataclass
class TradeRecord:
    """Registro persistente de un trade ejecutado.

    Contiene toda la información necesaria para análisis posterior:
    PnL por estrategia, por régimen, equity curve, fees, slippage.

    Campos adicionales vs core.types.Trade:
      - regime: régimen de mercado al momento del trade
      - equity_before / equity_after: para reconstruir equity curve
      - session_id: agrupa trades de una sesión de live/backtest
      - source: 'live' o 'backtest'
      - entry_price / exit_price: para trades de cierre
      - trade_type: 'ENTRY', 'EXIT', 'SL', 'TP', 'LIQUIDATION'
      - duration_sec: duración de la posición (si es cierre)
      - micro_vpin / micro_risk_score: contexto de microestructura
    """
    # Identificación
    trade_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    session_id: str = ""
    source: str = "live"  # 'live' | 'backtest'

    # Trade core
    symbol: str = ""
    side: str = ""          # BUY, SELL, CLOSE_BUY, CLOSE_SELL, SL_LONG, etc.
    price: float = 0.0
    quantity: float = 0.0
    fee: float = 0.0
    fee_asset: str = "USD"
    pnl: float = 0.0
    order_id: str = ""

    # Contexto de estrategia
    strategy: str = ""       # MEAN_REVERSION, TREND_FOLLOWING, MARKET_MAKING
    regime: str = ""         # RANGING, TRENDING_UP, etc.
    trade_type: str = ""     # ENTRY, EXIT, SL, TP, LIQUIDATION, CLOSE_EOD

    # Equity tracking
    equity_before: float = 0.0
    equity_after: float = 0.0

    # Posición (para trades de cierre)
    entry_price: float = 0.0   # precio de entrada de la posición
    exit_price: float = 0.0    # precio de salida
    duration_sec: float = 0.0  # duración de la posición en segundos

    # Microestructura al momento del trade
    micro_vpin: float = 0.0
    micro_risk_score: float = 0.0

    # Execution quality (captured from paper_sim / live fills)
    slippage_bps: float = 0.0
    expected_cost_bps: float = 0.0
    fill_probability: float = 0.0
    order_type: str = ""            # MARKET, LIMIT

    # MAE/MFE (from paper_sim price path tracking)
    mae_bps: float = 0.0           # Max Adverse Excursion in basis points
    mfe_bps: float = 0.0           # Max Favorable Excursion in basis points

    # Market context at entry
    signal_strength: float = 0.0
    spread_bps: float = 0.0
    atr: float = 0.0               # ATR at entry (absolute, not bps)

    # Derived
    pnl_pct: float = 0.0           # PnL as % of equity_before

    # Timestamp
    timestamp: float = field(default_factory=time.time)

    @property
    def notional(self) -> float:
        """Valor nocional del trade."""
        return abs(self.price * self.quantity)

    @property
    def is_winner(self) -> bool:
        """True si el trade fue ganador."""
        return self.pnl > 0

    @property
    def return_pct(self) -> float:
        """Retorno porcentual sobre equity antes del trade."""
        if self.equity_before <= 0:
            return 0.0
        return self.pnl / self.equity_before * 100

    def to_dict(self) -> dict:
        """Convierte a diccionario para serialización."""
        return {
            "trade_id": self.trade_id,
            "session_id": self.session_id,
            "source": self.source,
            "symbol": self.symbol,
            "side": self.side,
            "price": self.price,
            "quantity": self.quantity,
            "fee": self.fee,
            "fee_asset": self.fee_asset,
            "pnl": self.pnl,
            "order_id": self.order_id,
            "strategy": self.strategy,
            "regime": self.regime,
            "trade_type": self.trade_type,
            "equity_before": self.equity_before,
            "equity_after": self.equity_after,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "duration_sec": self.duration_sec,
            "micro_vpin": self.micro_vpin,
            "micro_risk_score": self.micro_risk_score,
            "slippage_bps": self.slippage_bps,
            "expected_cost_bps": self.expected_cost_bps,
            "fill_probability": self.fill_probability,
            "order_type": self.order_type,
            "mae_bps": self.mae_bps,
            "mfe_bps": self.mfe_bps,
            "signal_strength": self.signal_strength,
            "spread_bps": self.spread_bps,
            "atr": self.atr,
            "pnl_pct": self.pnl_pct,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TradeRecord:
        """Crea TradeRecord desde diccionario."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SessionRecord:
    """Registro de una sesión de trading (live o backtest).

    Agrupa trades y permite comparar sesiones entre sí.
    """
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: str = "live"        # 'live' | 'backtest'
    symbol: str = ""            # símbolo principal o 'MULTI'
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    initial_equity: float = 0.0
    final_equity: float = 0.0
    total_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    strategies_used: str = ""   # comma-separated
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "source": self.source,
            "symbol": self.symbol,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "initial_equity": self.initial_equity,
            "final_equity": self.final_equity,
            "total_trades": self.total_trades,
            "total_pnl": self.total_pnl,
            "max_drawdown": self.max_drawdown,
            "strategies_used": self.strategies_used,
            "notes": self.notes,
        }
