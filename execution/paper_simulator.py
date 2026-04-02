"""
PaperTradingSimulator — Simulador de fills para paper trading en tiempo real.

Reemplaza al exchange en modo paper: recibe senales validadas, simula fills
con slippage y fees realistas, trackea posiciones internas, y produce objetos
Trade IDENTICOS a los que produciria el exchange real.

Esto permite que todo el pipeline downstream (TradingLogger, MetricsCollector,
PortfolioManager, TradeDBAdapter, RiskManager) funcione exactamente igual
que en live, sin enviar ordenes reales.

Ademas, monitorea precios en tiempo real para activar SL/TP de posiciones
abiertas — algo que el dry-run anterior no hacia.

Uso:
    sim = PaperTradingSimulator(settings)
    # En cada ciclo de estrategia:
    trades = sim.execute_signals(validated_signals, mm_signals, sym_config)
    # En cada tick de precio:
    sl_tp_trades = sim.on_price_update(symbol, price, high, low)
    # Ambos retornan List[Trade] que se procesan igual que fills reales.
"""
from __future__ import annotations
import time
import uuid
from typing import Dict, List, Optional

from config.settings import Settings, SymbolConfig
from core.types import Signal, Trade, Side, StrategyType, Position
import structlog

logger = structlog.get_logger(__name__)


class PaperPosition:
    """Posicion simulada en paper trading."""

    def __init__(
        self,
        symbol: str,
        side: Side,
        size: float,
        entry_price: float,
        strategy: StrategyType,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        order_id: str = "",
    ) -> None:
        self.symbol = symbol
        self.side = side
        self.size = size
        self.entry_price = entry_price
        self.strategy = strategy
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.order_id = order_id
        self.open_time = time.time()
        self.unrealized_pnl = 0.0

    def update_pnl(self, current_price: float) -> float:
        if self.side == Side.BUY:
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size
        return self.unrealized_pnl

    def close(self, exit_price: float, fee_rate: float) -> tuple:
        """Cierra posicion. Retorna (pnl_neto, fee)."""
        if self.side == Side.BUY:
            gross = (exit_price - self.entry_price) * self.size
        else:
            gross = (self.entry_price - exit_price) * self.size
        # Exit fee only — entry fee already deducted at open
        fee = exit_price * self.size * fee_rate
        return gross - fee, fee

    def check_sl_tp(self, price: float, high: float, low: float) -> Optional[str]:
        """Verifica si SL o TP se activaron. Retorna 'SL' o 'TP' o None."""
        if self.stop_loss <= 0 and self.take_profit <= 0:
            return None

        if self.side == Side.BUY:
            if self.stop_loss > 0 and low <= self.stop_loss:
                return "SL"
            if self.take_profit > 0 and high >= self.take_profit:
                return "TP"
        else:
            if self.stop_loss > 0 and high >= self.stop_loss:
                return "SL"
            if self.take_profit > 0 and low <= self.take_profit:
                return "TP"
        return None

    def to_position(self, mark_price: float) -> Position:
        self.update_pnl(mark_price)
        return Position(
            symbol=self.symbol, side=self.side, size=self.size,
            entry_price=self.entry_price, mark_price=mark_price,
            unrealized_pnl=self.unrealized_pnl, strategy=self.strategy,
        )


class PaperTradingSimulator:
    """Simulador de fills para paper trading.

    Interfaz principal:
      execute_signals() → simula fills de senales validadas
      on_price_update() → verifica SL/TP en posiciones abiertas
      get_position()    → posicion actual por symbol+strategy
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config = settings.trading
        self._positions: Dict[str, PaperPosition] = {}  # key: symbol_STRATEGY
        self._last_prices: Dict[str, float] = {}  # symbol -> last known price
        self._trade_count = 0

    def execute_signals(
        self,
        signals: List[Signal],
        mm_signals: List[Signal],
        sym_config: SymbolConfig,
    ) -> List[Trade]:
        """Simula ejecucion de senales validadas. Retorna fills como Trade objects.

        Procesa MR+TF signals y MM signals por separado, igual que live.
        """
        trades: List[Trade] = []

        # MR + TF signals
        for sig in signals:
            fill = self._execute_one(sig, sym_config)
            if fill:
                trades.append(fill)

        # MM signals: reemplaza ordenes MM abiertas (simula refresh)
        for sig in mm_signals:
            fill = self._execute_one(sig, sym_config)
            if fill:
                trades.append(fill)

        return trades

    def on_price_update(
        self,
        symbol: str,
        price: float,
        high: float = 0.0,
        low: float = 0.0,
    ) -> List[Trade]:
        """Verifica SL/TP para posiciones abiertas de este simbolo.

        Llamar en cada tick o cada ciclo de estrategia con el precio actual.
        high/low opcionales para verificacion intra-bar mas precisa.
        """
        if high <= 0:
            high = price
        if low <= 0:
            low = price

        self._last_prices[symbol] = price
        trades: List[Trade] = []
        keys_to_close = []

        for key, pos in self._positions.items():
            if pos.symbol != symbol:
                continue

            trigger = pos.check_sl_tp(price, high, low)
            if trigger is None:
                pos.update_pnl(price)
                continue

            # Determinar precio de exit (with adverse slippage on SL fills)
            if trigger == "SL":
                # SL orders become market orders — add slippage
                sl_slip_bps = self.config.slippage_bps * 0.5  # Half base slippage on SL
                sl_slip = sl_slip_bps * pos.stop_loss / 10_000
                if pos.side == Side.BUY:
                    exit_price = pos.stop_loss - sl_slip  # Worse for longs
                else:
                    exit_price = pos.stop_loss + sl_slip  # Worse for shorts
            else:
                exit_price = pos.take_profit  # TP as limit order — exact fill

            # Determinar fee (MM=maker, others=taker)
            fee_rate = (self.config.maker_fee
                        if pos.strategy == StrategyType.MARKET_MAKING
                        else self.config.taker_fee)

            pnl, fee = pos.close(exit_price, fee_rate)
            close_side = Side.SELL if pos.side == Side.BUY else Side.BUY

            trade = Trade(
                symbol=symbol,
                side=close_side,
                price=exit_price,
                quantity=pos.size,
                fee=fee,
                order_id=f"paper_{trigger.lower()}_{uuid.uuid4().hex[:8]}",
                strategy=pos.strategy,
                pnl=pnl,
            )
            trades.append(trade)
            keys_to_close.append(key)

            logger.info(
                f"paper_{trigger.lower()}_triggered",
                symbol=symbol, strategy=pos.strategy.value,
                side=close_side.value, exit_price=round(exit_price, 2),
                pnl=round(pnl, 2),
            )

        for key in keys_to_close:
            del self._positions[key]

        return trades

    def get_position(self, symbol: str, strategy: StrategyType) -> Optional[Position]:
        """Retorna posicion paper actual, o None."""
        key = f"{symbol}_{strategy.value}"
        pos = self._positions.get(key)
        if pos:
            mark = self._last_prices.get(pos.symbol, pos.entry_price)
            return pos.to_position(mark)
        return None

    def get_all_positions(self) -> Dict[str, Position]:
        """Retorna todas las posiciones paper como Position objects."""
        return {
            key: pos.to_position(self._last_prices.get(pos.symbol, pos.entry_price))
            for key, pos in self._positions.items()
        }

    def get_total_exposure(self) -> float:
        """Exposicion total en USD."""
        return sum(pos.entry_price * pos.size for pos in self._positions.values())

    @property
    def position_count(self) -> int:
        return len(self._positions)

    # ── Ejecucion interna ────────────────────────────────────────

    def _execute_one(self, signal: Signal, sym_config: SymbolConfig) -> Optional[Trade]:
        """Simula ejecucion de una senal individual."""
        pos_key = f"{signal.symbol}_{signal.strategy.value}"
        is_exit = signal.metadata.get("action") in ("exit_mean_reversion", "trailing_stop_hit", "mm_unwind")

        # ── Salida de posicion existente ─────────────────────────
        if is_exit:
            pos = self._positions.get(pos_key)
            if not pos:
                return None

            # Apply exit slippage (adverse direction)
            exit_slip_bps = self.config.slippage_bps * 0.5
            exit_slip = exit_slip_bps * signal.entry_price / 10_000
            if pos.side == Side.BUY:
                exit_price = signal.entry_price - exit_slip  # Selling lower
            else:
                exit_price = signal.entry_price + exit_slip  # Buying higher
            fee_rate = self.config.taker_fee
            pnl, fee = pos.close(exit_price, fee_rate)

            close_side = Side.SELL if pos.side == Side.BUY else Side.BUY
            trade = Trade(
                symbol=signal.symbol,
                side=close_side,
                price=exit_price,
                quantity=pos.size,
                fee=fee,
                order_id=f"paper_exit_{uuid.uuid4().hex[:8]}",
                strategy=signal.strategy,
                pnl=pnl,
            )
            del self._positions[pos_key]
            self._trade_count += 1

            logger.info(
                "paper_exit_fill", symbol=signal.symbol,
                strategy=signal.strategy.value, side=close_side.value,
                price=round(exit_price, 2), pnl=round(pnl, 2),
            )
            return trade

        # ── Entrada nueva (no abrir si ya hay posicion) ──────────
        if pos_key in self._positions:
            return None

        price = signal.entry_price
        if price <= 0:
            return None

        size = signal.size_usd / price
        if size <= 0:
            return None

        # Aplicar slippage dinamico (with available market context + Kyle Lambda)
        from execution.slippage import compute_slippage
        import math as _math
        slippage = compute_slippage(
            base_bps=self.config.slippage_bps, price=price,
            size_usd=signal.size_usd,
            regime=signal.metadata.get("regime", ""),
            book_depth_usd=signal.metadata.get("book_depth_usd", 0),
            hawkes_ratio=signal.metadata.get("hawkes_ratio", 0),
            atr=signal.metadata.get("atr", 0),
        )
        # Add Kyle Lambda permanent impact component
        kyle_lambda_bps = signal.metadata.get("kyle_lambda_bps", 0)
        depth = signal.metadata.get("book_depth_usd", 0)
        if kyle_lambda_bps > 0 and depth > 0 and signal.size_usd > 0:
            slippage += kyle_lambda_bps * _math.sqrt(
                min(signal.size_usd / depth, 4.0)
            ) * price / 10_000
        if signal.side == Side.BUY:
            fill_price = price + slippage
        else:
            fill_price = price - slippage

        # Crear posicion paper (fee se cobra completo al cerrar, igual que backtester)
        self._positions[pos_key] = PaperPosition(
            symbol=signal.symbol,
            side=signal.side,
            size=size,
            entry_price=fill_price,
            strategy=signal.strategy,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            order_id=f"paper_entry_{uuid.uuid4().hex[:8]}",
        )
        self._trade_count += 1

        # Slippage tracking: medir diferencia entre precio de senal y fill
        actual_slippage_bps = abs(fill_price - price) / price * 10_000 if price > 0 else 0.0

        # Retornar Trade de entrada (pnl=0, fee=0 — fee completo se cobra al cerrar)
        trade = Trade(
            symbol=signal.symbol,
            side=signal.side,
            price=fill_price,
            quantity=size,
            fee=0.0,
            order_id=self._positions[pos_key].order_id,
            strategy=signal.strategy,
            pnl=0.0,  # entrada, sin PnL aun
            expected_price=price,
            actual_slippage_bps=actual_slippage_bps,
            signal_features=signal.metadata.copy(),
        )

        logger.info(
            "paper_entry_fill", symbol=signal.symbol,
            strategy=signal.strategy.value, side=signal.side.value,
            price=round(fill_price, 2), size=round(size, 6),
            sl=round(signal.stop_loss, 2), tp=round(signal.take_profit, 2),
        )
        return trade
