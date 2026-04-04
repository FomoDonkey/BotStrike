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

        # MAE/MFE tracking — updated on every price tick
        self.mae_price: float = entry_price  # worst price seen (lowest for BUY, highest for SELL)
        self.mfe_price: float = entry_price  # best price seen (highest for BUY, lowest for SELL)

        # Execution metadata (set by paper_sim after routing)
        self.order_type: str = ""           # LIMIT or MARKET
        self.expected_cost_bps: float = 0.0
        self.fill_probability: float = 0.0
        self.routing_reason: str = ""
        self.spread_at_entry_bps: float = 0.0
        self.atr_at_entry: float = 0.0
        self.regime_at_entry: str = ""

        # Price path for shadow exit simulation — (relative_time_sec, price)
        self._price_path: List[tuple] = []
        self._last_snapshot_time: float = 0.0

    def update_pnl(self, current_price: float) -> float:
        if self.side == Side.BUY:
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
            if current_price < self.mae_price:
                self.mae_price = current_price
            if current_price > self.mfe_price:
                self.mfe_price = current_price
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size
            if current_price > self.mae_price:
                self.mae_price = current_price
            if current_price < self.mfe_price:
                self.mfe_price = current_price
        # Sample price path every ~3s for shadow exit simulation (keeps memory bounded)
        now = time.time()
        if now - self._last_snapshot_time >= 3.0:
            elapsed = now - self.open_time
            self._price_path.append((round(elapsed, 1), current_price))
            self._last_snapshot_time = now
        return self.unrealized_pnl

    def close(self, exit_price: float, fee_rate: float) -> tuple:
        """Cierra posicion. Retorna (pnl_neto, fee_total).

        Cobra round-trip fees: entry + exit.
        """
        if self.side == Side.BUY:
            gross = (exit_price - self.entry_price) * self.size
        else:
            gross = (self.entry_price - exit_price) * self.size
        # Round-trip fee: entry fee + exit fee
        entry_fee = self.entry_price * self.size * fee_rate
        exit_fee = exit_price * self.size * fee_rate
        total_fee = entry_fee + exit_fee
        return gross - total_fee, total_fee

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


def _build_exit_features(pos: PaperPosition, exit_price: float,
                         hold_time: float, action: str, exit_reason: str) -> dict:
    """Build comprehensive signal_features dict for exit trades with MAE/MFE and execution context."""
    entry = pos.entry_price
    # MAE/MFE in bps relative to entry
    if entry > 0:
        if pos.side == Side.BUY:
            mae_bps = (entry - pos.mae_price) / entry * 10_000
            mfe_bps = (pos.mfe_price - entry) / entry * 10_000
        else:
            mae_bps = (pos.mae_price - entry) / entry * 10_000
            mfe_bps = (entry - pos.mfe_price) / entry * 10_000
        pnl_bps = (exit_price - entry) / entry * 10_000 if pos.side == Side.BUY else (entry - exit_price) / entry * 10_000
    else:
        mae_bps = mfe_bps = pnl_bps = 0.0

    return {
        # Core
        "entry_price": entry,
        "exit_price": exit_price,
        "hold_time_sec": round(hold_time, 1),
        "action": action,
        "exit_reason": exit_reason,
        # MAE/MFE
        "mae_bps": round(mae_bps, 2),
        "mfe_bps": round(mfe_bps, 2),
        "mae_price": pos.mae_price,
        "mfe_price": pos.mfe_price,
        "pnl_bps": round(pnl_bps, 2),
        # Execution metadata (captured at entry)
        "order_type": pos.order_type,
        "expected_cost_bps": round(pos.expected_cost_bps, 2),
        "fill_probability": round(pos.fill_probability, 3),
        "routing_reason": pos.routing_reason,
        # Market context at entry
        "spread_at_entry_bps": round(pos.spread_at_entry_bps, 2),
        "atr_at_entry": pos.atr_at_entry,
        "regime_at_entry": pos.regime_at_entry,
        # Price path for shadow exit simulation (list of (elapsed_sec, price))
        "price_path": pos._price_path,
        # SL/TP levels for shadow comparison
        "stop_loss": pos.stop_loss,
        "take_profit": pos.take_profit,
    }


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
        # Running high/low per symbol since last SL/TP check — ensures wicks
        # that cross SL/TP between ticks are not missed (paper realism fix)
        self._running_high: Dict[str, float] = {}
        self._running_low: Dict[str, float] = {}

        # SmartOrderRouter for execution parity with live mode
        from execution.smart_router import SmartOrderRouter
        self._router = SmartOrderRouter()

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
        # Maintain running high/low from tick stream so wicks between
        # individual trade ticks are captured for SL/TP checks
        prev_high = self._running_high.get(symbol, price)
        prev_low = self._running_low.get(symbol, price)
        effective_high = max(price, prev_high) if high <= 0 else max(high, prev_high)
        effective_low = min(price, prev_low) if low <= 0 else min(low, prev_low)

        self._last_prices[symbol] = price
        trades: List[Trade] = []
        keys_to_close = []

        for key, pos in self._positions.items():
            if pos.symbol != symbol:
                continue

            trigger = pos.check_sl_tp(price, effective_high, effective_low)
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
            hold_time = time.time() - pos.open_time if pos.open_time > 0 else 0

            trade = Trade(
                symbol=symbol,
                side=close_side,
                price=exit_price,
                quantity=pos.size,
                fee=fee,
                order_id=f"paper_{trigger.lower()}_{uuid.uuid4().hex[:8]}",
                strategy=pos.strategy,
                pnl=pnl,
                expected_price=pos.entry_price,
                signal_features=_build_exit_features(pos, exit_price, hold_time,
                                                       f"exit_{trigger.lower()}", trigger),
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

        # Reset running high/low after SL/TP evaluation cycle
        self._running_high[symbol] = price
        self._running_low[symbol] = price

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
        action = signal.metadata.get("action", "")
        is_exit = (
            action.startswith("exit")  # exit_ofm, exit_mean_reversion, etc.
            or action in ("trailing_stop_hit", "mm_unwind")
            or signal.metadata.get("exit_reason") is not None
        )

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
            import time as _time
            hold_time = _time.time() - pos.open_time if pos.open_time > 0 else 0
            trade = Trade(
                symbol=signal.symbol,
                side=close_side,
                price=exit_price,
                quantity=pos.size,
                fee=fee,
                order_id=f"paper_exit_{uuid.uuid4().hex[:8]}",
                strategy=signal.strategy,
                pnl=pnl,
                expected_price=pos.entry_price,  # Original entry price for tracking
                signal_features=_build_exit_features(pos, exit_price, hold_time,
                                                     signal.metadata.get("action", "exit"),
                                                     signal.metadata.get("exit_reason", "")),
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

        # ── SmartOrderRouter: same routing logic as live mode ────
        # Extract market context from signal metadata
        spread_bps = signal.metadata.get("spread_bps", self.config.slippage_bps * 2)
        atr_bps = 0.0
        atr_val = signal.metadata.get("atr", 0)
        if atr_val and price > 0:
            atr_bps = atr_val / price * 10_000
        book_depth_usd = signal.metadata.get("book_depth_usd", 0)
        trade_intensity = signal.metadata.get("trade_intensity", 0)
        kyle_lambda_bps = signal.metadata.get("kyle_lambda_bps", 0)
        microprice = signal.metadata.get("microprice", 0)

        is_mm = signal.strategy == StrategyType.MARKET_MAKING
        routing = self._router.route(
            side=signal.side.value,
            price=price,
            size_usd=signal.size_usd,
            spread_bps=spread_bps,
            atr_bps=atr_bps,
            book_depth_usd=book_depth_usd,
            trade_intensity=trade_intensity,
            signal_strength=signal.strength,
            is_exit=False,
            is_mm=is_mm,
            maker_fee_bps=self.config.maker_fee * 10_000,
            taker_fee_bps=self.config.taker_fee * 10_000,
            microprice=microprice,
            mid_price=price,
            kyle_lambda_bps=kyle_lambda_bps,
        )

        # Apply slippage based on routing decision (same model as live)
        from execution.slippage import compute_slippage
        import math as _math

        if routing.order_type == "LIMIT" and routing.limit_price > 0:
            # LIMIT order: fill at router's optimized price + small slippage
            base_price = routing.limit_price
            # Simulate fill probability — use router's estimate
            import random
            if random.random() > routing.fill_probability:
                # Order not filled — no trade (matches live behavior)
                logger.debug("paper_limit_no_fill",
                             symbol=signal.symbol,
                             fill_prob=round(routing.fill_probability, 3),
                             reason=routing.reason)
                return None
            # Filled: minimal slippage on limit orders
            slippage = compute_slippage(
                base_bps=self.config.slippage_bps * 0.3,  # Limit orders have less slippage
                price=base_price,
                size_usd=signal.size_usd,
                regime=signal.metadata.get("regime", ""),
                book_depth_usd=book_depth_usd,
                hawkes_ratio=signal.metadata.get("hawkes_ratio", 0),
                atr=signal.metadata.get("atr", 0),
            )
        else:
            # MARKET order: full slippage model (same as before)
            base_price = price
            slippage = compute_slippage(
                base_bps=self.config.slippage_bps, price=base_price,
                size_usd=signal.size_usd,
                regime=signal.metadata.get("regime", ""),
                book_depth_usd=book_depth_usd,
                hawkes_ratio=signal.metadata.get("hawkes_ratio", 0),
                atr=signal.metadata.get("atr", 0),
            )

        # Add Kyle Lambda permanent impact component (both order types)
        depth = book_depth_usd
        if kyle_lambda_bps > 0 and depth > 0 and signal.size_usd > 0:
            slippage += kyle_lambda_bps * _math.sqrt(
                min(signal.size_usd / depth, 4.0)
            ) * base_price / 10_000

        if signal.side == Side.BUY:
            fill_price = base_price + slippage
        else:
            fill_price = base_price - slippage

        # Crear posicion paper (round-trip fee se cobra al cerrar: entry + exit)
        pos = PaperPosition(
            symbol=signal.symbol,
            side=signal.side,
            size=size,
            entry_price=fill_price,
            strategy=signal.strategy,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            order_id=f"paper_entry_{uuid.uuid4().hex[:8]}",
        )
        # Store execution metadata for later analysis
        pos.order_type = routing.order_type
        pos.expected_cost_bps = routing.expected_cost_bps
        pos.fill_probability = routing.fill_probability
        pos.routing_reason = routing.reason
        pos.spread_at_entry_bps = spread_bps
        pos.atr_at_entry = signal.metadata.get("atr", 0)
        pos.regime_at_entry = signal.metadata.get("regime", "")
        self._positions[pos_key] = pos
        self._trade_count += 1

        # Slippage tracking: medir diferencia entre precio de senal y fill
        actual_slippage_bps = abs(fill_price - price) / price * 10_000 if price > 0 else 0.0

        # Retornar Trade de entrada (pnl=0, fee=0 — round-trip fee se cobra al cerrar)
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
            order_type=routing.order_type,
            expected_cost_bps=round(routing.expected_cost_bps, 2),
            fill_prob=round(routing.fill_probability, 3),
            routing_reason=routing.reason,
        )
        return trade
