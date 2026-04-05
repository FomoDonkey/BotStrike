"""
Order Execution Engine — Motor de ejecución de órdenes.
Convierte señales en órdenes, maneja cancel/replace dinámico,
minimiza slippage, gestiona órdenes pendientes.
"""
from __future__ import annotations
import asyncio
import time
import uuid
from collections import deque
from typing import Dict, List, Optional

from config.settings import Settings, SymbolConfig
from core.types import (
    Signal, Order, OrderType, Side, TimeInForce, StrategyType, Position, Trade,
)
from typing import Union
from exchange.strike_client import StrikeClient
from exchange.binance_client import BinanceClient, SYMBOL_MAP_REVERSE as BINANCE_SYMBOL_REVERSE
from risk.risk_manager import RiskManager

# Exchange client type — both implement the same interface (place_order, cancel_order, etc.)
ExchangeClient = Union[StrikeClient, BinanceClient]
from execution.smart_router import (
    SmartOrderRouter, FillProbabilityModel, QueuePositionModel,
    VWAPEngine, ExecutionAnalytics, TradeIntensityModel,
    SpreadPredictor,
)
import structlog

logger = structlog.get_logger(__name__)


class OrderExecutionEngine:
    """Motor de ejecución que convierte señales validadas en órdenes.

    Integra SmartOrderRouter para decision inteligente de limit vs market,
    fill probability model, y execution analytics.
    """

    def __init__(
        self,
        settings: Settings,
        client: ExchangeClient,
        risk_manager: RiskManager,
    ) -> None:
        self.settings = settings
        self.client = client
        self.risk_manager = risk_manager

        # Tracking de órdenes activas
        self._active_orders: Dict[str, Order] = {}  # order_id -> Order
        # Mapping de señal a orden para cancel/replace
        self._signal_orders: Dict[str, str] = {}  # signal_key -> order_id
        # Fills recientes (bounded deque — no manual trimming needed)
        self._recent_trades: deque = deque(maxlen=500)

        # ── Smart execution components ───────────────────────────────
        self.fill_model = FillProbabilityModel()
        self.queue_model = QueuePositionModel()
        self.smart_router = SmartOrderRouter(
            fill_model=self.fill_model,
            queue_model=self.queue_model,
            opportunity_cost_bps=5.0,
        )
        self.vwap_engine = VWAPEngine()
        self.exec_analytics = ExecutionAnalytics()
        self.trade_intensity: Dict[str, TradeIntensityModel] = {}
        self.spread_predictor: Dict[str, SpreadPredictor] = {}

        # Inicializar per-symbol
        for sym in settings.symbols:
            self.trade_intensity[sym.symbol] = TradeIntensityModel()
            self.spread_predictor[sym.symbol] = SpreadPredictor()

    # ── Ejecución de señales ───────────────────────────────────────

    async def execute_signal(self, signal: Signal, sym_config: SymbolConfig) -> Optional[Order]:
        """Ejecuta una señal de trading como orden en el exchange.

        Usa SmartOrderRouter para decision inteligente de tipo de orden.
        """
        price = signal.entry_price
        size_units = signal.size_usd / price if price > 0 else 0
        if size_units <= 0:
            return None

        # Generar client_order_id único
        client_id = f"bs_{signal.strategy.value[:2]}_{uuid.uuid4().hex[:8]}"

        # ── Smart Routing Decision ───────────────────────────────────
        is_exit = signal.metadata.get("action") in (
            "exit_mean_reversion", "trailing_stop_hit", "mm_unwind"
        )
        is_mm = signal.strategy == StrategyType.MARKET_MAKING

        # Extraer features del mercado desde metadata
        spread_bps = signal.metadata.get("spread_bps", self.settings.trading.slippage_bps * 2)
        atr_bps = 0.0
        atr_val = signal.metadata.get("atr", 0)
        if atr_val is not None and atr_val > 0 and price > 0:
            atr_bps = atr_val / price * 10_000

        book_depth = signal.metadata.get("book_depth_usd", 0)
        microprice = signal.metadata.get("microprice", 0)
        mid_price = signal.metadata.get("mid_price", price)

        # Trade intensity
        intensity = self.trade_intensity.get(signal.symbol)
        trade_rate = intensity.current.total_intensity if intensity else 0.0

        kyle_lambda_bps = signal.metadata.get("kyle_lambda_bps", 0.0)

        routing = self.smart_router.route(
            side=signal.side.value,
            price=price,
            size_usd=signal.size_usd,
            spread_bps=spread_bps,
            atr_bps=atr_bps,
            book_depth_usd=book_depth,
            trade_intensity=trade_rate,
            signal_strength=signal.strength,
            is_exit=is_exit,
            is_mm=is_mm,
            maker_fee_bps=self.settings.trading.maker_fee * 10_000,
            taker_fee_bps=self.settings.trading.taker_fee * 10_000,
            microprice=microprice,
            mid_price=mid_price,
            kyle_lambda_bps=kyle_lambda_bps,
        )

        # ── Build order from routing decision ────────────────────────
        if is_mm:
            # MM: siempre limit + post_only (override routing for safety)
            order = Order(
                symbol=signal.symbol,
                side=signal.side,
                order_type=OrderType.LIMIT,
                quantity=size_units,
                price=signal.entry_price,
                time_in_force=TimeInForce.GTC,
                post_only=True,
                client_order_id=client_id,
                strategy=signal.strategy,
            )
        elif routing.order_type == "MARKET" or is_exit:
            order = Order(
                symbol=signal.symbol,
                side=signal.side,
                order_type=OrderType.MARKET,
                quantity=size_units,
                reduce_only=is_exit,
                client_order_id=client_id,
                strategy=signal.strategy,
            )
            # Stash expected price for slippage tracking (market orders have price=None)
            order._expected_price = signal.entry_price
        else:
            # Limit order con precio optimizado del router
            limit_price = routing.limit_price
            if limit_price <= 0:
                # Fallback: precio de senal + slippage minimo
                slippage = self.settings.trading.slippage_bps * price / 10_000
                limit_price = price + slippage if signal.side == Side.BUY else price - slippage

            order = Order(
                symbol=signal.symbol,
                side=signal.side,
                order_type=OrderType.LIMIT,
                quantity=size_units,
                price=limit_price,
                time_in_force=TimeInForce.IOC,
                client_order_id=client_id,
                strategy=signal.strategy,
            )

        logger.debug("smart_routing", symbol=signal.symbol,
                      decision=routing.order_type, reason=routing.reason,
                      cost_bps=round(routing.expected_cost_bps, 2),
                      fill_prob=round(routing.fill_probability, 2))

        # Enviar orden
        try:
            result = await self.client.place_order(order)
            order.order_id = result.get("orderId", result.get("order_id", ""))
            order.status = result.get("status", "NEW")
            if order.order_id:
                self._active_orders[order.order_id] = order
            logger.info(
                "order_placed", symbol=order.symbol, side=order.side.value,
                type=order.order_type.value, qty=round(order.quantity, 6),
                price=order.price, order_id=order.order_id,
            )

            # Place protective orders (SL/TP) for non-MM strategies.
            # CRITICAL FIX: Don't gate on status — the order may fill on the
            # exchange before the REST response arrives (race condition).
            # For MARKET orders the fill is near-instant; for LIMIT IOC the
            # fill-or-cancel also resolves before we'd poll.  Always place
            # protectives so the position is never unprotected.  If the
            # parent order ends up unfilled/cancelled, the reduce_only
            # protectives will be no-ops on the exchange.
            if (signal.stop_loss != signal.entry_price
                    and signal.take_profit != signal.entry_price
                    and signal.strategy != StrategyType.MARKET_MAKING):
                await self._place_protective_orders(signal, size_units, sym_config)

            return order

        except Exception as e:
            logger.error("order_failed", symbol=signal.symbol, error=str(e))
            return None

    async def _place_protective_orders(
        self, signal: Signal, size: float, sym_config: SymbolConfig
    ) -> None:
        """Coloca órdenes de stop loss y take profit.

        CRITICAL: If both SL and TP fail, emergency-close the position
        via market order to prevent unprotected exposure.
        """
        sl_ok = False
        tp_ok = False
        sl_side = Side.SELL if signal.side == Side.BUY else Side.BUY

        # Stop Loss
        sl_order = Order(
            symbol=signal.symbol,
            side=sl_side,
            order_type=OrderType.STOP,
            quantity=size,
            stop_price=signal.stop_loss,
            reduce_only=True,
            client_order_id=f"bs_sl_{uuid.uuid4().hex[:8]}",
            strategy=signal.strategy,
        )
        try:
            result = await self.client.place_order(sl_order)
            sl_order.order_id = result.get("orderId", result.get("order_id", ""))
            if sl_order.order_id:
                self._active_orders[sl_order.order_id] = sl_order
                sl_ok = True
        except Exception as e:
            logger.error("sl_order_failed", symbol=signal.symbol, error=str(e))

        # Take Profit
        tp_order = Order(
            symbol=signal.symbol,
            side=sl_side,
            order_type=OrderType.TAKE_PROFIT,
            quantity=size,
            stop_price=signal.take_profit,
            reduce_only=True,
            client_order_id=f"bs_tp_{uuid.uuid4().hex[:8]}",
            strategy=signal.strategy,
        )
        try:
            result = await self.client.place_order(tp_order)
            tp_order.order_id = result.get("orderId", result.get("order_id", ""))
            if tp_order.order_id:
                self._active_orders[tp_order.order_id] = tp_order
                tp_ok = True
        except Exception as e:
            logger.error("tp_order_failed", symbol=signal.symbol, error=str(e))

        # EMERGENCY: If BOTH protective orders failed, close the position immediately
        if not sl_ok and not tp_ok:
            logger.critical("BOTH_PROTECTIVES_FAILED_emergency_close",
                            symbol=signal.symbol, size=size)
            emergency = Order(
                symbol=signal.symbol,
                side=sl_side,
                order_type=OrderType.MARKET,
                quantity=size,
                reduce_only=True,
                client_order_id=f"bs_emg_{uuid.uuid4().hex[:8]}",
                strategy=signal.strategy,
            )
            try:
                await self.client.place_order(emergency)
                logger.warning("emergency_close_sent", symbol=signal.symbol)
            except Exception as e2:
                logger.critical("EMERGENCY_CLOSE_ALSO_FAILED", symbol=signal.symbol,
                                error=str(e2))
        elif not sl_ok:
            logger.critical("SL_FAILED_position_has_TP_only", symbol=signal.symbol)

    # ── Market Making: cancel/replace ──────────────────────────────

    async def refresh_mm_orders(
        self, symbol: str, signals: List[Signal]
    ) -> None:
        """Refresca órdenes de market making: cancela las viejas y coloca nuevas."""
        # Cancelar solo órdenes MM activas para este símbolo (no tocar SL/TP de otras estrategias)
        to_cancel = [
            oid for oid, o in self._active_orders.items()
            if o.symbol == symbol and o.strategy == StrategyType.MARKET_MAKING
        ]

        for oid in to_cancel:
            try:
                result = await self.client.cancel_order(symbol, oid)
                # Only remove from tracking if cancel was confirmed by exchange
                if isinstance(result, dict) and result.get("status") in ("CANCELED", "CANCELLED", None):
                    self._active_orders.pop(oid, None)
                else:
                    # Cancel returned but status unclear — mark for re-check
                    self._active_orders.pop(oid, None)
            except Exception as e:
                # Order may already be filled/expired — remove from tracking
                logger.warning("mm_cancel_single_failed", order_id=oid, error=str(e))
                self._active_orders.pop(oid, None)
        if to_cancel:
            logger.debug("mm_orders_cancelled", symbol=symbol, count=len(to_cancel))

        # Colocar nuevas órdenes MM en batch
        mm_signals = [s for s in signals if s.strategy == StrategyType.MARKET_MAKING]
        if not mm_signals:
            return

        orders = []
        for sig in mm_signals:
            price = sig.entry_price
            size = sig.size_usd / price if price > 0 else 0
            if size <= 0:
                continue
            orders.append(Order(
                symbol=sig.symbol,
                side=sig.side,
                order_type=OrderType.LIMIT,
                quantity=size,
                price=price,
                post_only=True,
                client_order_id=f"bs_mm_{uuid.uuid4().hex[:8]}",
                strategy=StrategyType.MARKET_MAKING,
            ))

        if orders:
            try:
                result = await self.client.batch_orders(orders)
                # Track order IDs from batch response for future cancellation
                if isinstance(result, dict):
                    order_results = result.get("orders", result.get("data", []))
                    if isinstance(order_results, list):
                        for i, resp in enumerate(order_results):
                            if isinstance(resp, dict) and i < len(orders):
                                oid = resp.get("orderId", resp.get("order_id", ""))
                                if oid:
                                    orders[i].order_id = oid
                                    orders[i].status = resp.get("status", "NEW")
                                    self._active_orders[oid] = orders[i]
                logger.info("mm_orders_placed", symbol=symbol, count=len(orders),
                            tracked=sum(1 for o in orders if o.order_id))
            except Exception as e:
                logger.error("mm_batch_failed", symbol=symbol, error=str(e))

    # ── Procesamiento de fills (desde WebSocket) ──────────────────

    def on_order_update(self, data: Dict) -> Optional[Trade]:
        """Procesa actualización de orden desde WebSocket."""
        order_id = str(data.get("i", data.get("orderId", "")))
        exec_type = data.get("x", data.get("executionType", ""))
        status = data.get("X", data.get("orderStatus", ""))

        order = self._active_orders.get(order_id)

        if exec_type == "TRADE":
            # Fill parcial o total
            fill_price = float(data.get("L", data.get("lastFilledPrice", 0)))
            fill_qty = float(data.get("l", data.get("lastFilledQuantity", 0)))
            # Guard: skip if fill data is invalid (double WS callback or stale event)
            if fill_price <= 0 or fill_qty <= 0:
                logger.warning("invalid_fill_data", order_id=order_id,
                               fill_price=fill_price, fill_qty=fill_qty)
                return None
            commission = float(data.get("n", data.get("commission", 0)))
            realized_pnl = float(data.get("rp", data.get("realizedProfit", 0)))

            side = Side(data.get("S", data.get("side", "BUY")))
            raw_symbol = data.get("s", data.get("symbol", ""))
            # Normalize Binance symbols (BTCUSDT → BTC-USD) if using Binance client
            symbol = BINANCE_SYMBOL_REVERSE.get(raw_symbol, raw_symbol)

            # Slippage tracking: comparar precio esperado vs fill real
            expected_price = 0.0
            actual_slippage_bps = 0.0
            latency_ms = 0.0
            signal_features = {}

            if order:
                # For market orders price=None; use _expected_price stashed at creation
                expected_price = order.price or getattr(order, '_expected_price', 0.0)
                if expected_price > 0 and fill_price > 0:
                    # Signed slippage: positive = adverse (paid more for BUY / received less for SELL)
                    if side == Side.BUY:
                        actual_slippage_bps = (fill_price - expected_price) / expected_price * 10_000
                    else:
                        actual_slippage_bps = (expected_price - fill_price) / expected_price * 10_000
                    # Registrar en slippage tracker del risk manager
                    self.risk_manager.slippage_tracker.record_fill(
                        expected_price=expected_price,
                        fill_price=fill_price,
                        symbol=symbol,
                        size_usd=fill_price * fill_qty,
                    )
                # Latencia: timestamp de fill - timestamp de orden
                # fill_ts is in milliseconds (exchange), order.timestamp is in seconds (Python time.time())
                fill_ts = float(data.get("T", data.get("transactTime", 0)))
                if fill_ts > 0 and order.timestamp > 0:
                    latency_ms = fill_ts - order.timestamp * 1000  # both in ms now

            trade = Trade(
                symbol=symbol,
                side=side,
                price=fill_price,
                quantity=fill_qty,
                fee=commission,
                order_id=order_id,
                strategy=order.strategy if order else None,
                pnl=realized_pnl,
                expected_price=expected_price,
                actual_slippage_bps=actual_slippage_bps,
                latency_ms=latency_ms,
            )
            self._recent_trades.append(trade)

            # Actualizar risk manager con PnL (use async-safe to prevent
            # race condition with _risk_monitor_loop / _process_paper_fill)
            if realized_pnl != 0:
                import asyncio as _asyncio
                _asyncio.ensure_future(
                    self.risk_manager.record_trade_result_safe(realized_pnl, strategy=trade.strategy)
                )

            logger.info(
                "trade_fill", symbol=symbol, side=side.value,
                price=fill_price, qty=fill_qty, pnl=realized_pnl,
            )

            # Limpiar orden completamente filled del tracking
            if status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                self._active_orders.pop(order_id, None)

            return trade

        if status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
            self._active_orders.pop(order_id, None)

        return None

    # ── Emergencia ─────────────────────────────────────────────────

    async def cancel_all(self) -> None:
        """Cancela todas las órdenes activas (emergencia)."""
        try:
            await self.client.cancel_all_orders()
            self._active_orders.clear()
            logger.warning("all_orders_cancelled_emergency")
        except Exception as e:
            logger.error("emergency_cancel_failed", error=str(e))

    def cleanup_stale_orders(self, max_age_sec: float = 300.0) -> int:
        """Remove orders older than max_age_sec from tracking.

        Called periodically to prevent _active_orders from growing after WS disconnects
        where FILLED/CANCELED events were missed.
        """
        now = time.time()
        stale = [oid for oid, order in self._active_orders.items()
                 if (now - order.timestamp) > max_age_sec]
        for oid in stale:
            self._active_orders.pop(oid, None)
        if stale:
            logger.warning("stale_orders_cleaned", count=len(stale), max_age_sec=max_age_sec)
        return len(stale)

    @property
    def active_order_count(self) -> int:
        return len(self._active_orders)

    @property
    def recent_trades(self) -> List[Trade]:
        return list(self._recent_trades)[-100:]  # últimos 100 trades

    async def reconcile_orders_with_exchange(self) -> int:
        """Query open orders from exchange and reconcile with local tracking.

        Removes locally tracked orders that no longer exist on exchange
        (filled/cancelled during WS disconnect). Returns count of reconciled orders.
        """
        try:
            if not hasattr(self.client, 'get_open_orders'):
                return 0
            exchange_orders = await self.client.get_open_orders()
            if not isinstance(exchange_orders, list):
                return 0

            exchange_ids = {
                str(o.get("orderId", o.get("order_id", "")))
                for o in exchange_orders
            }
            stale = [
                oid for oid in self._active_orders
                if oid and oid not in exchange_ids
            ]
            for oid in stale:
                order = self._active_orders.pop(oid, None)
                if order:
                    logger.warning("reconciled_stale_order",
                                   order_id=oid, symbol=order.symbol,
                                   order_type=order.order_type.value)
            if stale:
                logger.info("order_reconciliation_complete",
                            removed=len(stale), remaining=len(self._active_orders))
            return len(stale)
        except Exception as e:
            logger.error("order_reconciliation_failed", error=str(e))
            return 0
