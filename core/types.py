"""
Tipos de datos fundamentales del sistema de trading.
Enums, dataclasses y estructuras compartidas entre módulos.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List
import time


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"


class TimeInForce(Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class MarketRegime(Enum):
    RANGING = "RANGING"          # Lateral / mean-reverting
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    BREAKOUT = "BREAKOUT"        # Alta volatilidad + momentum fuerte
    UNKNOWN = "UNKNOWN"


class StrategyType(Enum):
    MEAN_REVERSION = "MEAN_REVERSION"
    TREND_FOLLOWING = "TREND_FOLLOWING"
    MARKET_MAKING = "MARKET_MAKING"
    ORDER_FLOW_MOMENTUM = "ORDER_FLOW_MOMENTUM"


@dataclass
class OHLCV:
    """Candlestick con volumen."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class OrderBookLevel:
    price: float
    quantity: float


@dataclass
class OrderBook:
    """Snapshot del libro de órdenes."""
    symbol: str
    timestamp: float
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)

    @property
    def best_bid(self) -> Optional[float]:
        return max(l.price for l in self.bids) if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return min(l.price for l in self.asks) if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def spread_bps(self) -> float:
        """Spread en basis points."""
        if self.mid_price and self.spread:
            return self.spread / self.mid_price * 10_000
        return 0.0

    @property
    def microprice(self) -> Optional[float]:
        """Microprice Level-1: pondera por cantidades en top-of-book.

        microprice = ask * (bid_qty / total) + bid * (ask_qty / total)
        Si bid_qty >> ask_qty → microprice se acerca al ask (presion compradora)
        """
        if not self.bids or not self.asks:
            return self.mid_price
        best_bid_level = max(self.bids, key=lambda x: x.price)
        best_ask_level = min(self.asks, key=lambda x: x.price)
        total_qty = best_bid_level.quantity + best_ask_level.quantity
        if total_qty <= 0:
            return self.mid_price
        return (
            best_ask_level.price * (best_bid_level.quantity / total_qty)
            + best_bid_level.price * (best_ask_level.quantity / total_qty)
        )

    @property
    def top_bid_depth_usd(self) -> float:
        """Profundidad USD de los top 5 bids."""
        return sum(l.price * l.quantity for l in sorted(
            self.bids, key=lambda x: x.price, reverse=True
        )[:5])

    @property
    def top_ask_depth_usd(self) -> float:
        """Profundidad USD de los top 5 asks."""
        return sum(l.price * l.quantity for l in sorted(
            self.asks, key=lambda x: x.price
        )[:5])


@dataclass
class Signal:
    """Señal generada por una estrategia."""
    strategy: StrategyType
    symbol: str
    side: Side
    strength: float          # 0.0 a 1.0
    entry_price: float
    stop_loss: float
    take_profit: float
    size_usd: float          # tamaño sugerido en USD
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class Order:
    """Orden enviada o recibida del exchange."""
    symbol: str
    side: Side
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = False
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    order_id: Optional[str] = None
    status: str = "NEW"
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    timestamp: float = field(default_factory=time.time)
    strategy: Optional[StrategyType] = None


@dataclass
class Position:
    """Posición abierta en un activo."""
    symbol: str
    side: Side
    size: float              # en unidades del activo
    entry_price: float
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    leverage: int = 1
    liquidation_price: float = 0.0
    strategy: Optional[StrategyType] = None
    timestamp: float = field(default_factory=time.time)

    @property
    def notional(self) -> float:
        return abs(self.size * self.mark_price)

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.side == Side.BUY:
            return (self.mark_price - self.entry_price) / self.entry_price
        return (self.entry_price - self.mark_price) / self.entry_price


@dataclass
class Trade:
    """Trade ejecutado (fill)."""
    symbol: str
    side: Side
    price: float
    quantity: float
    fee: float
    fee_asset: str = "USD"
    order_id: str = ""
    strategy: Optional[StrategyType] = None
    timestamp: float = field(default_factory=time.time)
    pnl: float = 0.0
    # Slippage tracking — medicion real vs esperado
    expected_price: float = 0.0        # Precio de la senal original
    actual_slippage_bps: float = 0.0   # Slippage medido en bps
    latency_ms: float = 0.0            # Latencia envio→fill en ms
    # Feature attribution — que features triggerearon la senal
    signal_features: dict = field(default_factory=dict)


@dataclass
class MarketSnapshot:
    """Snapshot completo del estado del mercado para un símbolo."""
    symbol: str
    timestamp: float
    price: float
    mark_price: float
    index_price: float
    funding_rate: float
    volume_24h: float
    open_interest: float
    orderbook: Optional[OrderBook] = None
    regime: MarketRegime = MarketRegime.UNKNOWN
