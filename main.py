"""
BotStrike — Sistema de Trading Algorítmico para Strike Finance.
Punto de entrada principal. Orquesta todos los módulos.

Uso:
    python main.py                  # Modo live trading
    python main.py --backtest       # Modo backtesting con datos sintéticos
    python main.py --backtest --csv data.csv  # Backtest con CSV
    python main.py --dry-run        # Live sin enviar órdenes reales
    python main.py --dashboard      # Lanza dashboard Streamlit
"""
from __future__ import annotations
import argparse
import asyncio
import os
import signal
import sys
import time
from typing import Dict, List, Optional

from config.settings import Settings, SymbolConfig, ExchangeVenue
from core.types import MarketRegime, StrategyType, Signal, Position, Side
from core.market_data import MarketDataCollector
from core.regime_detector import RegimeDetector
from exchange.strike_client import StrikeClient
from exchange.binance_client import BinanceClient
from exchange.websocket_client import StrikeWebSocket
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.market_making import MarketMakingStrategy
from strategies.base import BaseStrategy
from risk.risk_manager import RiskManager
from portfolio.portfolio_manager import PortfolioManager
from execution.order_engine import OrderExecutionEngine
from logging_metrics.logger import TradingLogger, MetricsCollector
from core.microstructure import MicrostructureEngine
from core.orderbook_alpha import OrderBookImbalance
from core.microprice import MicropriceCalculator
from core.quant_models import MonteCarloBootstrap
from backtesting.backtester import Backtester
from trade_database.repository import TradeRepository
from trade_database.adapter import TradeDBAdapter
from execution.paper_simulator import PaperTradingSimulator
from notifications import get_notifier
import structlog

logger = structlog.get_logger(__name__)


class BotStrike:
    """Orquestador principal del sistema de trading."""

    def __init__(self, settings: Settings, dry_run: bool = False, paper: bool = False,
                 use_binance: bool = False) -> None:
        self.settings = settings
        self.dry_run = dry_run
        self.paper = paper
        self.use_binance = use_binance
        self._running = False

        # Resolve exchange venue: --binance flag overrides config; config default is "binance"
        self._venue = ExchangeVenue.BINANCE if use_binance else settings.exchange_venue_enum

        # Paper trading with Strike: forzar URLs de MAINNET para datos reales
        if self.paper and self._venue == ExchangeVenue.STRIKE:
            settings.api_price_url = "https://api.strikefinance.org/price"
            settings.api_base_url = "https://api.strikefinance.org"
            settings.ws_market_url = "wss://api.strikefinance.org/ws/price"

        # Inicializar exchange client según venue configurado
        if self._venue == ExchangeVenue.BINANCE:
            self.client = BinanceClient(settings)
            from exchange.binance_ws import BinanceWebSocket
            self.websocket = BinanceWebSocket(symbols=settings.symbol_names)
            self.use_binance = True  # Force Binance WS for data
        else:
            self.client = StrikeClient(settings)
            if use_binance:
                from exchange.binance_ws import BinanceWebSocket
                self.websocket = BinanceWebSocket(symbols=settings.symbol_names)
            else:
                self.websocket = StrikeWebSocket(settings)

        self.regime_detector = RegimeDetector()
        self.market_data = MarketDataCollector(settings, self.client, self.regime_detector)
        self.risk_manager = RiskManager(settings)
        self.portfolio_manager = PortfolioManager(settings, self.risk_manager)
        self.execution_engine = OrderExecutionEngine(settings, self.client, self.risk_manager)
        self.trading_logger = TradingLogger(settings.log_file, settings.metrics_file)
        self.metrics = MetricsCollector()

        # Paper Trading Simulator (solo en modo paper)
        self.paper_sim: Optional[PaperTradingSimulator] = None
        if self.paper:
            self.paper_sim = PaperTradingSimulator(settings)

        # Microestructura (VPIN, Hawkes, A-S mejorado)
        self.microstructure = MicrostructureEngine(
            symbols=settings.symbol_names,
            config=settings.get_microstructure_config(),
        )

        # Trade Database — almacenamiento persistente de trades
        self.trade_repo = TradeRepository("data/trade_database.db")
        self.trade_db = TradeDBAdapter(
            self.trade_repo, source="paper" if self.paper else "live"
        )

        # Trend Provider — tendencia real de Binance 4H/1D klines
        from core.trend_provider import TrendProvider
        self.trend_provider = TrendProvider()

        # Estrategias
        from strategies.order_flow_momentum import OrderFlowMomentumStrategy
        self.strategies: List[BaseStrategy] = [
            MeanReversionStrategy(settings.trading),
            TrendFollowingStrategy(settings.trading),
            MarketMakingStrategy(settings.trading),
            OrderFlowMomentumStrategy(settings.trading),
        ]

        # Microprice calculator por símbolo
        self.microprice: Dict[str, MicropriceCalculator] = {}
        for sym in settings.symbols:
            self.microprice[sym.symbol] = MicropriceCalculator(levels=5)

        # Order Book Imbalance por símbolo
        self.obi: Dict[str, OrderBookImbalance] = {}
        for sym in settings.symbols:
            self.obi[sym.symbol] = OrderBookImbalance(
                levels=sym.obi_levels,
                decay=sym.obi_decay,
                delta_window=sym.obi_delta_window,
            )

        # Monte Carlo para análisis periódico
        self.monte_carlo = MonteCarloBootstrap(
            max_drawdown_pct=settings.trading.max_drawdown_pct,
        )

        # Research Engine — quantitative validation of strategies
        from analytics.research_engine import ResearchEngine
        self.research = ResearchEngine(settings)

        # Telegram notifications
        self.notifier = get_notifier(settings)

        # Estado: último régimen por símbolo
        self._last_regime: Dict[str, MarketRegime] = {}
        # Posiciones internas por símbolo+estrategia
        self._positions: Dict[str, Position] = {}

    async def start(self) -> None:
        """Inicia el sistema de trading."""
        mode = "paper" if self.paper else ("dry_run" if self.dry_run else "live")
        logger.info("botstrike_starting", symbols=self.settings.symbol_names, mode=mode)

        # Aplicar testnet si corresponde (paper ya forzo MAINNET en __init__)
        if not self.paper:
            self.settings.apply_testnet()

        # Configurar leverage en exchange (no en paper/dry-run)
        if not self.dry_run and not self.paper:
            for sym in self.settings.symbols:
                try:
                    await self.client.set_leverage(sym.symbol, sym.leverage)
                    logger.info("leverage_set", symbol=sym.symbol, leverage=sym.leverage)
                except Exception as e:
                    logger.warning("leverage_set_failed", symbol=sym.symbol, error=str(e))

        # Inicializar datos de mercado
        await self.market_data.initialize()

        # Seed with Binance klines for immediate chart data (6h of 1m candles)
        if self.use_binance:
            for sym_config in self.settings.symbols:
                await self.market_data.seed_from_binance(sym_config.symbol, sym_config, hours=6)

        # Iniciar sesión de trade database
        self.trade_db.start_session(
            initial_equity=self.settings.trading.initial_capital,
            symbol="MULTI",
            notes=f"{mode} trading",
        )

        # Registrar callbacks de WebSocket
        self._setup_ws_callbacks()

        # Arrancar loops
        self._running = True
        tasks = [
            asyncio.create_task(self.websocket.connect_market()),
            asyncio.create_task(self._strategy_loop()),
            asyncio.create_task(self._mm_loop()),
            asyncio.create_task(self._risk_monitor_loop()),
            asyncio.create_task(self._data_refresh_loop()),
            asyncio.create_task(self._metrics_loop()),
            asyncio.create_task(self._daily_analysis_loop()),
        ]

        # WebSocket de usuario solo si hay API key Y no estamos en paper mode
        has_api_key = (
            self.settings.api_private_key  # Strike key
            or os.getenv("BINANCE_API_KEY", "")  # Binance key
        )
        if has_api_key and not self.paper:
            tasks.append(asyncio.create_task(self.websocket.connect_user()))

        logger.info("botstrike_running")

        # Notificaciones Telegram
        await self.notifier.start()
        await self.notifier.notify_startup(
            mode=mode, symbols=self.settings.symbol_names,
            config={
                "initial_capital": self.settings.trading.initial_capital,
                "testnet": self.settings.use_testnet,
                "risk_per_trade_pct": self.settings.trading.risk_per_trade_pct,
                "max_drawdown_pct": self.settings.trading.max_drawdown_pct,
            },
        )

        try:
            await self._supervise_tasks(tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def _supervise_tasks(self, tasks: list) -> None:
        """Supervisa tasks: detecta crashes y reinicia tasks críticos.

        Si un task no-crítico (metrics, data_refresh) muere, se reinicia.
        Si un task crítico (strategy, mm, risk, websocket) muere 3 veces, se hace shutdown.
        """
        task_names = ["ws_market", "strategy", "mm", "risk_monitor", "data_refresh", "metrics"]
        if len(tasks) > len(task_names):
            task_names.append("ws_user")

        # Tasks que pueden reiniciarse (no-críticos)
        restartable_methods = {
            "metrics": self._metrics_loop,
            "data_refresh": self._data_refresh_loop,
        }
        crash_counts: Dict[str, int] = {name: 0 for name in task_names}
        max_restarts = 3

        while self._running:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for completed_task in done:
                idx = None
                for i, t in enumerate(tasks):
                    if t is completed_task:
                        idx = i
                        break
                if idx is None:
                    continue

                name = task_names[idx] if idx < len(task_names) else f"task_{idx}"
                exc = completed_task.exception()

                if exc is None or isinstance(exc, asyncio.CancelledError):
                    # Task finished normally or was cancelled
                    continue

                crash_counts[name] = crash_counts.get(name, 0) + 1
                logger.error("task_crashed", task=name, error=str(exc),
                             crash_count=crash_counts[name])
                asyncio.ensure_future(self.notifier.notify_error(name, str(exc)))

                # Try to restart if restartable and under limit
                if name in restartable_methods and crash_counts[name] <= max_restarts:
                    logger.warning("task_restarting", task=name)
                    new_task = asyncio.create_task(restartable_methods[name]())
                    tasks[idx] = new_task
                elif crash_counts[name] > max_restarts:
                    logger.critical("task_max_restarts_exceeded", task=name)
                    self._running = False
                    for t in tasks:
                        t.cancel()
                    return

            # If all tasks finished, exit
            if not any(not t.done() for t in tasks):
                break

    def _setup_ws_callbacks(self) -> None:
        """Configura callbacks para eventos de WebSocket."""
        # Registrar callback de reconexión para tick quality warmup
        self.websocket._on_market_connect_cb = self.market_data.on_ws_connected

        # Trades de mercado → actualizar datos
        async def on_market_trade(data: Dict):
            try:
                symbol = data.get("s", "")
                price = float(data.get("p", 0))
                qty = float(data.get("q", 0))
                ts = float(data.get("T", time.time() * 1000)) / 1000.0
                if symbol and price > 0:
                    self.market_data.on_trade(symbol, price, qty, ts)
                    # Clasificar dirección: m=True → buyer is maker → sell aggressor
                    is_buy = not data.get("m", False)
                    # Alimentar indicadores de microestructura tick-a-tick (+ Kyle Lambda)
                    self.microstructure.on_trade(symbol, price, qty, ts, is_buy=is_buy)
                    # Alimentar trade intensity model (bidireccional)
                    intensity_model = self.execution_engine.trade_intensity.get(symbol)
                    if intensity_model:
                        intensity_model.on_trade(ts, is_buy, price * qty)
                    # Paper trading: verificar SL/TP en cada tick de precio
                    if self.paper_sim:
                        sl_tp_trades = self.paper_sim.on_price_update(symbol, price)
                        for trade in sl_tp_trades:
                            self._process_paper_fill(trade)
            except Exception as e:
                logger.error("on_market_trade_error", error=str(e))

        self.websocket.on("trade", on_market_trade)

        # Depth updates → actualizar orderbook en snapshot
        async def on_depth_update(data: Dict):
            symbol = data.get("s", "")
            if not symbol or symbol not in [s.symbol for s in self.settings.symbols]:
                return
            bids = data.get("b", data.get("bids", []))
            asks = data.get("a", data.get("asks", []))
            if bids and asks:
                from core.types import OrderBook, OrderBookLevel
                ob = OrderBook(
                    symbol=symbol,
                    timestamp=time.time(),
                    bids=[OrderBookLevel(float(b[0]), float(b[1])) for b in bids[:10]],
                    asks=[OrderBookLevel(float(a[0]), float(a[1])) for a in asks[:10]],
                )
                self.market_data.on_orderbook(symbol, ob)

        self.websocket.on("depth", on_depth_update)
        self.websocket.on("depthUpdate", on_depth_update)

        # Order updates (fills) → procesar
        async def on_order_update(data: Dict):
            try:
                if data.get("e") == "ORDER_TRADE_UPDATE":
                    order_data = data.get("o", data)
                    trade = self.execution_engine.on_order_update(order_data)
                    if trade:
                        self.trading_logger.log_trade(trade)
                        self.metrics.add_trade(trade)
                        if trade.strategy:
                            self.portfolio_manager.update_strategy_pnl(
                                trade.strategy, trade.pnl
                            )
                        # Register fill for Kyle Lambda adverse selection measurement
                        self.microstructure.register_fill(
                            trade.symbol, trade.price, time.time(),
                            is_buy=(trade.side == Side.BUY),
                        )
                        # Notificar trade por Telegram
                        asyncio.ensure_future(self.notifier.notify_trade(trade))
                        # Persistir en trade database
                        regime = self._last_regime.get(trade.symbol, MarketRegime.UNKNOWN)
                        micro = self.microstructure.get_snapshot(trade.symbol)
                        self.trade_db.on_trade(
                            trade,
                            regime=regime,
                            equity_before=self.risk_manager.current_equity,
                            equity_after=self.risk_manager.current_equity + trade.pnl,
                            micro_vpin=micro.vpin.vpin if micro and micro.vpin else 0,
                            micro_risk_score=micro.risk_score if micro else 0,
                        )
            except Exception as e:
                logger.error("on_order_update_error", error=str(e))

        self.websocket.on("ORDER_TRADE_UPDATE", on_order_update)

        # Account updates → equity
        async def on_account_update(data: Dict):
            if data.get("e") == "ACCOUNT_UPDATE":
                # Actualizar equity
                balances = data.get("a", {}).get("B", [])
                for b in balances:
                    if b.get("a") == "USD":
                        equity = float(b.get("wb", 0))
                        self.risk_manager.update_equity(equity)
                        self.metrics.update_equity(equity)

        self.websocket.on("ACCOUNT_UPDATE", on_account_update)

        # Suscribir a canales de mercado para cada símbolo
        for sym in self.settings.symbols:
            asyncio.ensure_future(self.websocket.subscribe("trade", sym.symbol))
            asyncio.ensure_future(self.websocket.subscribe("depth", sym.symbol))
            asyncio.ensure_future(self.websocket.subscribe("markprice", sym.symbol))

    # ── Loop principal de estrategia ──────────────────────────────

    async def _strategy_loop(self) -> None:
        """Loop principal: detecta régimen, genera señales, ejecuta."""
        await asyncio.sleep(5)  # esperar inicialización

        while self._running:
            try:
                for sym_config in self.settings.symbols:
                    symbol = sym_config.symbol
                    await self._process_symbol(symbol, sym_config)

                await asyncio.sleep(self.settings.trading.strategy_interval_sec)

            except Exception as e:
                logger.error("strategy_loop_error", error=str(e))
                await asyncio.sleep(5)

    async def _mm_loop(self) -> None:
        """Loop dedicado para Market Making — refresha quotes cada mm_interval_sec.

        Mas rapido que el strategy_loop (500ms vs 5s) porque MM necesita
        quotes frescas para capturar spread. NO recalcula indicadores ni regimen
        — usa los valores cacheados del ultimo ciclo de _strategy_loop.
        """
        await asyncio.sleep(8)  # esperar que _strategy_loop haya corrido al menos 1 vez

        mm_strategy = None
        for s in self.strategies:
            if s.strategy_type == StrategyType.MARKET_MAKING:
                mm_strategy = s
                break
        if mm_strategy is None:
            return

        while self._running:
            try:
                for sym_config in self.settings.symbols:
                    symbol = sym_config.symbol

                    # No hacer MM con datos stale
                    if self.market_data.is_data_stale(
                        symbol, self.settings.trading.data_stale_warn_sec
                    ):
                        continue

                    regime = self._last_regime.get(symbol, MarketRegime.UNKNOWN)

                    # Verificar si MM esta activa en este regimen
                    mm_active = (
                        mm_strategy.should_activate(regime)
                        and self.portfolio_manager.should_strategy_trade(
                            StrategyType.MARKET_MAKING, regime
                        )
                    )

                    # Si MM no esta activa pero hay inventory, cerrar posicion
                    if not mm_active:
                        await self._unwind_mm_inventory(symbol, sym_config)
                        continue

                    # Obtener datos frescos (snapshot se actualiza tick-a-tick via WS)
                    df = self.market_data.get_dataframe(symbol)
                    snapshot = self.market_data.get_snapshot(symbol)
                    if df.empty or snapshot is None:
                        continue

                    snapshot.regime = regime
                    micro = self.microstructure.get_snapshot(symbol)

                    allocated = self.portfolio_manager.get_allocation(
                        symbol, regime, StrategyType.MARKET_MAKING
                    )

                    if self.paper_sim:
                        current_pos = self.paper_sim.get_position(symbol, StrategyType.MARKET_MAKING)
                    else:
                        current_pos = self._positions.get(symbol)

                    # OBI para MM spread skew
                    obi_result = None
                    if symbol in self.obi and snapshot.orderbook:
                        obi_result = self.obi[symbol].compute(snapshot.orderbook)

                    mm_signals = mm_strategy.generate_signals(
                        symbol, df, snapshot, regime, sym_config, allocated, current_pos,
                        micro=micro,
                        obi=obi_result,
                    )

                    if not mm_signals:
                        continue

                    # MM safety: skip si circuit breaker o max drawdown excedido
                    if self.risk_manager.is_circuit_breaker_active:
                        continue
                    if self.risk_manager.current_drawdown_pct >= self.settings.trading.max_drawdown_pct:
                        continue

                    # Inyectar régimen en metadata para slippage por régimen
                    for sig in mm_signals:
                        sig.metadata["regime"] = regime.value

                    # Ejecutar
                    if self.paper and self.paper_sim:
                        fills = self.paper_sim.execute_signals([], mm_signals, sym_config)
                        for trade in fills:
                            self._process_paper_fill(trade)
                    elif not self.dry_run:
                        await self.execution_engine.refresh_mm_orders(symbol, mm_signals)

                await asyncio.sleep(self.settings.trading.mm_interval_sec)

            except Exception as e:
                logger.error("mm_loop_error", error=str(e))
                await asyncio.sleep(2)

    async def _update_trend_background(self, symbol: str) -> None:
        """Fetch trend data in background (non-blocking)."""
        try:
            await self.trend_provider.update(symbol)
        except Exception as e:
            logger.debug("trend_bg_update_failed", symbol=symbol, error=str(e))

    async def _process_symbol(self, symbol: str, sym_config: SymbolConfig) -> None:
        """Procesa un símbolo: régimen → señales → ejecución."""
        # Protección de datos stale: no operar si no hay datos frescos
        data_age = self.market_data.get_data_age(symbol)
        if data_age > self.settings.trading.data_stale_block_sec:
            logger.warning("data_stale_skip", symbol=symbol,
                           age_sec=round(data_age, 1),
                           threshold=self.settings.trading.data_stale_block_sec)
            return
        if data_age > self.settings.trading.data_stale_warn_sec:
            logger.info("data_stale_warn", symbol=symbol, age_sec=round(data_age, 1))

        df = self.market_data.get_dataframe(symbol)
        snapshot = self.market_data.get_snapshot(symbol)

        if df.empty or snapshot is None:
            return

        # Trend from Binance 4H/1D (use cached, update in background to avoid blocking)
        trend_info = self.trend_provider.get_trend(symbol)
        if trend_info is None or (time.time() - trend_info.timestamp > 900):
            asyncio.ensure_future(self._update_trend_background(symbol))

        # Multi-timeframe: generar DataFrames de 5m, 15m, 1h para estrategias
        mtf_signals = {}
        if len(df) > 30 and "timestamp" in df.columns:
            try:
                from core.indicators import Indicators
                ts_unit = "s" if df["timestamp"].max() < 1e12 else "ms"
                df_indexed = df.set_index(pd.to_datetime(df["timestamp"], unit=ts_unit))
                for tf_label, tf_rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h")]:
                    resampled = df_indexed.resample(tf_rule).agg({
                        "open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum",
                    }).dropna()
                    if len(resampled) > 20:
                        resampled = Indicators.compute_all(resampled.reset_index(drop=True))
                        last = resampled.iloc[-1]
                        mtf_signals[tf_label] = {
                            "rsi": float(last.get("rsi", 50)),
                            "bb_upper": float(last.get("bb_upper", 0)),
                            "bb_lower": float(last.get("bb_lower", 0)),
                            "adx": float(last.get("adx", 0)),
                            "atr": float(last.get("atr", 0)),
                        }
            except Exception:
                pass

        # 1. Detectar régimen
        regime = self.regime_detector.detect(df, symbol, sym_config)

        # Log cambio de régimen
        old_regime = self._last_regime.get(symbol, MarketRegime.UNKNOWN)
        if regime != old_regime:
            self.trading_logger.log_regime_change(symbol, old_regime, regime)
            logger.info("regime_changed", symbol=symbol,
                        old=old_regime.value, new=regime.value)
            asyncio.ensure_future(self.notifier.notify_regime_change(
                symbol, old_regime, regime))
            self._last_regime[symbol] = regime

        # Actualizar régimen en snapshot
        snapshot.regime = regime

        # 1b. Obtener snapshot de microestructura (VPIN, Hawkes, A-S)
        micro = self.microstructure.get_snapshot(symbol)

        # 1c. Calcular Order Book Imbalance
        obi_result = None
        if symbol in self.obi and snapshot.orderbook:
            obi_result = self.obi[symbol].compute(snapshot.orderbook)

        # 1d. Calcular Microprice (fair value superior al mid_price)
        microprice_result = None
        if symbol in self.microprice and snapshot.orderbook:
            # Trade intensity para microprice ajustado
            intensity = self.execution_engine.trade_intensity.get(symbol)
            buy_int = intensity.current.buy_intensity if intensity else 0.0
            sell_int = intensity.current.sell_intensity if intensity else 0.0
            obi_d = obi_result.delta if obi_result else 0.0

            microprice_result = self.microprice[symbol].compute(
                snapshot.orderbook,
                trade_intensity_buy=buy_int,
                trade_intensity_sell=sell_int,
                obi_delta=obi_d,
            )

            # Feed spread predictor
            spread_pred = self.execution_engine.spread_predictor.get(symbol)
            if spread_pred and snapshot.orderbook.spread_bps > 0:
                spread_pred.on_spread(snapshot.orderbook.spread_bps)

        # 1e. Alimentar correlation regime con precios
        self.portfolio_manager.on_price_update(symbol, snapshot.price)

        # Log microestructura periódicamente (cada ciclo de estrategia)
        if micro:
            self.trading_logger._append_metric({
                "type": "microstructure",
                "timestamp": time.time(),
                "symbol": symbol,
                "vpin": micro.vpin.vpin if micro.vpin else 0,
                "vpin_toxic": micro.vpin.is_toxic,
                "hawkes_intensity": micro.hawkes.intensity,
                "hawkes_spike": micro.hawkes.is_spike,
                "hawkes_ratio": micro.hawkes.spike_ratio,
                "as_spread_bps": micro.avellaneda_stoikov.spread_bps,
                "as_gamma_eff": micro.avellaneda_stoikov.effective_gamma,
                "risk_score": micro.risk_score,
                "kyle_lambda": micro.kyle_lambda.kyle_lambda_ema,
                "kyle_lambda_valid": micro.kyle_lambda.is_valid,
                "impact_stress": micro.kyle_lambda.impact_stress,
                "adverse_selection_bps": micro.kyle_lambda.adverse_selection_bps,
            })

        # 2. Generar señales de cada estrategia (MR + TF — MM en _mm_loop)
        all_signals: List[Signal] = []

        # Check if ANY strategy already has a position on this symbol
        # to prevent multiple strategies from trading the same symbol simultaneously
        symbol_has_position = False
        if self.paper_sim:
            for strat_type in [StrategyType.MEAN_REVERSION, StrategyType.ORDER_FLOW_MOMENTUM,
                               StrategyType.TREND_FOLLOWING]:
                if self.paper_sim.get_position(symbol, strat_type) is not None:
                    symbol_has_position = True
                    break
        elif self._positions.get(symbol) is not None:
            # Live mode: exchange has one aggregate position per symbol
            symbol_has_position = True

        for strategy in self.strategies:
            # MM se maneja en _mm_loop (refresh 500ms, no aqui cada 5s)
            if strategy.strategy_type == StrategyType.MARKET_MAKING:
                continue
            # Kill switch: check if research engine disabled this strategy
            is_active, kill_reason = self.research.get_strategy_status(strategy.strategy_type)
            if not is_active:
                logger.info("strategy_killed_by_research",
                            strategy=strategy.strategy_type.value,
                            symbol=symbol, reason=kill_reason)
                continue
            # Verificar si la estrategia debe operar
            if not strategy.should_activate(regime):
                logger.debug("strategy_regime_skip",
                             strategy=strategy.strategy_type.value,
                             regime=regime.value, symbol=symbol)
                continue
            if not self.portfolio_manager.should_strategy_trade(
                strategy.strategy_type, regime
            ):
                logger.debug("strategy_portfolio_skip",
                             strategy=strategy.strategy_type.value,
                             regime=regime.value, symbol=symbol)
                continue

            # Obtener capital asignado
            allocated = self.portfolio_manager.get_allocation(
                symbol, regime, strategy.strategy_type
            )

            # Posición actual para esta estrategia
            if self.paper_sim:
                current_pos = self.paper_sim.get_position(symbol, strategy.strategy_type)
            else:
                # Live: exchange has one aggregate position per symbol
                current_pos = self._positions.get(symbol)

            # Block new entries if another strategy already has a position on this symbol
            # (exits are always allowed — must be able to close existing positions)
            if symbol_has_position and current_pos is None:
                logger.info("position_blocked_symbol_locked",
                            strategy=strategy.strategy_type.value,
                            symbol=symbol,
                            mode="paper" if self.paper_sim else "live",
                            reason="another_strategy_has_position")
                continue

            # Kelly risk fraction para esta estrategia
            kelly_pct = self.risk_manager.get_kelly_risk_pct(strategy.strategy_type)

            # Generar señales (pasar micro, OBI, Kelly, trend vía kwargs)
            signals = strategy.generate_signals(
                symbol, df, snapshot, regime, sym_config, allocated, current_pos,
                micro=micro,
                obi=obi_result,
                kelly_risk_pct=kelly_pct,
                mtf=mtf_signals,
                trend_info=trend_info,
            )

            for sig in signals:
                self.trading_logger.log_signal(sig)
                asyncio.ensure_future(self.notifier.notify_signal(sig))
                all_signals.append(sig)
                # Only log as "generated" if it's an entry signal, not exit
                is_exit = sig.metadata.get("action", "").startswith("exit") or sig.metadata.get("exit_reason")
                log_type = "signal_exit" if is_exit else "signal_generated"
                logger.info(log_type,
                            symbol=sig.symbol, strategy=sig.strategy.value,
                            side=sig.side.value, strength=round(sig.strength, 3),
                            price=round(sig.entry_price, 2),
                            trigger=sig.metadata.get("trigger", sig.metadata.get("exit_reason", "")))

        # 3. Validar señales con risk manager (incluye filtro de microestructura + funding)
        funding_rate = self.market_data.get_funding_rate(symbol)
        validated: List[Signal] = []
        blocked_count = 0
        for sig in all_signals:
            valid = self.risk_manager.validate_signal(
                sig, sym_config, regime, micro=micro, funding_rate=funding_rate
            )
            if valid:
                validated.append(valid)
                logger.info("signal_validated",
                            symbol=sig.symbol, strategy=sig.strategy.value,
                            side=sig.side.value, size_usd=round(sig.size_usd, 2))
            else:
                blocked_count += 1
        if blocked_count > 0:
            logger.info("signals_blocked", count=blocked_count, total=len(all_signals))

        # 4. Ejecutar (MR + TF solamente — MM se maneja en _mm_loop)
        # Inyectar datos de mercado en metadata para smart router y paper_sim
        for sig in validated:
            sig.metadata["regime"] = regime.value
            if microprice_result and microprice_result.is_valid:
                sig.metadata["microprice"] = microprice_result.adjusted_microprice
                sig.metadata["mid_price"] = microprice_result.mid_price
                sig.metadata["spread_bps"] = microprice_result.spread_bps
            if snapshot.orderbook:
                sig.metadata["book_depth_usd"] = (
                    snapshot.orderbook.top_bid_depth_usd
                    if sig.side == Side.BUY
                    else snapshot.orderbook.top_ask_depth_usd
                )
            # Kyle Lambda for smart router and paper simulator
            if micro and micro.kyle_lambda.is_valid:
                sig.metadata["kyle_lambda_bps"] = micro.kyle_lambda.kyle_lambda_ema

        if self.paper and self.paper_sim:
            fills = self.paper_sim.execute_signals(validated, [], sym_config)
            for trade in fills:
                self._process_paper_fill(trade)
        elif not self.dry_run:
            for sig in validated:
                order = await self.execution_engine.execute_signal(sig, sym_config)
                if order:
                    logger.info("signal_executed", symbol=symbol,
                                strategy=sig.strategy.value, side=sig.side.value)
            # MM execution handled by _mm_loop (faster refresh rate)
        else:
            for sig in validated:
                logger.info("dry_run_signal", symbol=symbol,
                            strategy=sig.strategy.value, side=sig.side.value,
                            price=sig.entry_price, size=sig.size_usd)

    def _process_paper_fill(self, trade: "Trade") -> None:
        """Procesa un fill simulado por el paper trading, identico al pipeline live."""
        self.trading_logger.log_trade(trade)
        self.metrics.add_trade(trade)
        asyncio.ensure_future(self.notifier.notify_trade(trade))
        if trade.strategy:
            self.portfolio_manager.update_strategy_pnl(trade.strategy, trade.pnl)
        # Notify strategies about SL/TP exits so they can update cooldowns
        # (strategy-generated exits already track this internally)
        is_sl_tp = trade.signal_features.get("exit_reason") in ("SL", "TP")
        if is_sl_tp:
            for strategy in self.strategies:
                if strategy.strategy_type == trade.strategy and hasattr(strategy, "notify_external_exit"):
                    strategy.notify_external_exit(trade.symbol, time.time())
        # Capturar equity ANTES de actualizar
        equity_before = self.risk_manager.current_equity
        new_equity = equity_before + trade.pnl
        self.risk_manager.update_equity(new_equity)
        self.metrics.update_equity(new_equity)
        if trade.pnl != 0:
            self.risk_manager.record_trade_result(trade.pnl, strategy=trade.strategy)
        # Slippage tracking para paper fills
        if trade.expected_price > 0:
            self.risk_manager.slippage_tracker.record_fill(
                expected_price=trade.expected_price,
                fill_price=trade.price,
                symbol=trade.symbol,
                regime=self._last_regime.get(trade.symbol, MarketRegime.UNKNOWN).value,
                size_usd=trade.price * trade.quantity,
            )
        # Feed to Research Engine — only closed trades (pnl != 0)
        if trade.pnl != 0:
            research_report = self.research.on_trade(trade)
            if research_report:
                # Auto-report triggered — log and notify
                report_text = self.research.format_report(research_report)
                logger.info("research_report_generated",
                            report_number=research_report.report_number,
                            trades=research_report.total_trades)
                asyncio.ensure_future(
                    self.notifier.notify_risk_event("research_report", {
                        "report": report_text[:2000],  # Truncate for Telegram
                    })
                )

        # Persistir en trade database — extract execution quality from signal_features
        regime = self._last_regime.get(trade.symbol, MarketRegime.UNKNOWN)
        micro = self.microstructure.get_snapshot(trade.symbol)
        sf = trade.signal_features or {}
        is_exit = trade.pnl != 0 or sf.get("action", "").startswith("exit")
        self.trade_db.on_trade(
            trade,
            regime=regime,
            equity_before=equity_before,
            equity_after=new_equity,
            micro_vpin=micro.vpin.vpin if micro and micro.vpin else 0,
            micro_risk_score=micro.risk_score if micro else 0,
            trade_type="EXIT" if is_exit else "ENTRY",
            entry_price=trade.expected_price if not is_exit else sf.get("entry_price", 0),
            duration_sec=sf.get("hold_time_sec", 0),
            # Execution quality (new fields)
            slippage_bps=trade.actual_slippage_bps,
            expected_cost_bps=sf.get("expected_cost_bps", 0),
            fill_probability=sf.get("fill_probability", 0),
            order_type=sf.get("order_type", ""),
            mae_bps=sf.get("mae_bps", 0),
            mfe_bps=sf.get("mfe_bps", 0),
            signal_strength=sf.get("strength", sf.get("signal_strength", 0)),
            spread_bps=sf.get("spread_at_entry_bps", sf.get("spread_bps", 0)),
            atr=sf.get("atr_at_entry", sf.get("atr", 0)),
            pnl_pct=(trade.pnl / equity_before * 100) if equity_before > 0 else 0,
        )

    async def _unwind_mm_inventory(self, symbol: str, sym_config: SymbolConfig) -> None:
        """Cierra inventory de Market Making cuando la estrategia se desactiva.

        Genera una señal de cierre market order para devolver inventory a cero.
        Se llama cuando el régimen cambia y MM deja de estar activa.
        """
        if self.paper_sim:
            current_pos = self.paper_sim.get_position(symbol, StrategyType.MARKET_MAKING)
        else:
            current_pos = self._positions.get(symbol)

        if current_pos is None or current_pos.size == 0:
            return

        price = self.market_data.get_current_price(symbol)
        if price <= 0:
            return

        close_side = Side.SELL if current_pos.side == Side.BUY else Side.BUY
        exit_size = current_pos.notional if current_pos.notional > 0 else current_pos.size * price

        unwind_signal = Signal(
            strategy=StrategyType.MARKET_MAKING,
            symbol=symbol,
            side=close_side,
            strength=1.0,
            entry_price=price,
            stop_loss=price,
            take_profit=price,
            size_usd=exit_size,
            metadata={"action": "mm_unwind", "reason": "regime_change"},
        )

        logger.info("mm_inventory_unwind", symbol=symbol, side=close_side.value,
                     size_usd=round(exit_size, 2))

        if self.paper and self.paper_sim:
            fills = self.paper_sim.execute_signals([unwind_signal], [], sym_config)
            for trade in fills:
                self._process_paper_fill(trade)
        elif not self.dry_run:
            await self.execution_engine.execute_signal(unwind_signal, sym_config)

    # ── Loops auxiliares ──────────────────────────────────────────

    async def _risk_monitor_loop(self) -> None:
        """Monitorea riesgo continuamente."""
        while self._running:
            try:
                # Auto-reset daily PnL at UTC midnight (robust date-based check)
                self.risk_manager.check_daily_reset()

                if self.paper and self.paper_sim:
                    # Paper mode: sync posiciones desde el simulador
                    # Aggregate by symbol (paper_sim keys by symbol_STRATEGY,
                    # risk_manager keys by symbol — must sum notional to avoid
                    # one strategy overwriting another)
                    symbol_positions: Dict[str, list] = {}
                    for key, pos in self.paper_sim.get_all_positions().items():
                        symbol_positions.setdefault(pos.symbol, []).append(pos)

                    paper_symbols = set()
                    for sym, pos_list in symbol_positions.items():
                        paper_symbols.add(sym)
                        # Use first position as base, aggregate notional/size
                        base = pos_list[0]
                        if len(pos_list) > 1:
                            total_size = sum(p.size for p in pos_list)
                            total_notional = sum(p.notional for p in pos_list)
                            total_unrealized = sum(p.unrealized_pnl for p in pos_list)
                            base = Position(
                                symbol=sym, side=base.side, size=total_size,
                                entry_price=base.entry_price, mark_price=base.mark_price,
                                unrealized_pnl=total_unrealized, strategy=base.strategy,
                            )
                        self.risk_manager.update_position(sym, base)

                    # Clear closed positions from risk_manager
                    for sym in list(self.risk_manager._positions.keys()):
                        if sym not in paper_symbols:
                            self.risk_manager.update_position(sym, None)
                elif not self.dry_run and (self.settings.api_private_key or self.settings.binance_api_secret):
                    # Live: actualizar posiciones desde exchange
                    positions_data = await self.client.get_positions()
                    if isinstance(positions_data, list):
                        for p in positions_data:
                            symbol = p.get("symbol", "")
                            size = float(p.get("positionAmt", p.get("size", 0)))
                            if size != 0:
                                side = Side.BUY if size > 0 else Side.SELL
                                pos = Position(
                                    symbol=symbol, side=side, size=abs(size),
                                    entry_price=float(p.get("entryPrice", 0)),
                                    mark_price=float(p.get("markPrice", 0)),
                                    unrealized_pnl=float(p.get("unrealizedProfit", 0)),
                                )
                                self.risk_manager.update_position(symbol, pos)
                                # Sync aggregate position for strategy decision-making.
                                # In live mode we can't distinguish per-strategy, so store
                                # under a generic key. Strategies check this for the symbol.
                                self._positions[symbol] = pos
                            else:
                                self.risk_manager.update_position(symbol, None)
                                self._positions.pop(symbol, None)

                # Verificar drawdown crítico (>= consistente con _check_max_drawdown)
                if self.risk_manager.current_drawdown_pct >= self.settings.trading.max_drawdown_pct:
                    logger.warning("MAX_DRAWDOWN_EXCEEDED — cancelling all orders")
                    self.trading_logger.log_risk_event("max_drawdown", {
                        "drawdown": self.risk_manager.current_drawdown_pct,
                    })
                    await self.notifier.notify_risk_event("max_drawdown", {
                        "drawdown_pct": f"{self.risk_manager.current_drawdown_pct:.2%}",
                        "threshold": f"{self.settings.trading.max_drawdown_pct:.2%}",
                    })
                    if not self.dry_run and not self.paper:
                        await self.execution_engine.cancel_all()

                await asyncio.sleep(self.settings.trading.risk_check_interval_sec)

            except Exception as e:
                logger.error("risk_loop_error", error=str(e))
                await asyncio.sleep(5)

    async def _data_refresh_loop(self) -> None:
        """Refresca datos de mercado periódicamente via REST (backup de WS)."""
        while self._running:
            try:
                await self.market_data.refresh_all()
                await asyncio.sleep(30)  # cada 30 segundos
            except Exception as e:
                logger.error("data_refresh_error", error=str(e))
                await asyncio.sleep(10)

    async def _metrics_loop(self) -> None:
        """Loguea métricas periódicamente."""
        while self._running:
            try:
                summary = self.portfolio_manager.get_portfolio_summary()
                self.trading_logger.log_portfolio_snapshot(summary)

                # Tick quality stats
                tq = self.market_data.get_tick_quality_stats()
                if tq["total_ticks"] > 0:
                    logger.info("tick_quality", **{k: v for k, v in tq.items() if k != "jitter_ema"})

                metrics = self.metrics.get_metrics()
                if metrics.get("total_trades", 0) > 0:
                    logger.info("performance_update", **{
                        k: v for k, v in metrics.items()
                        if k != "by_strategy"
                    })

                # Log quant model status
                risk = self.risk_manager.get_risk_summary()
                if risk.get("risk_of_ruin", 0) > 0:
                    logger.info("quant_models",
                                ror=risk["risk_of_ruin"],
                                vol_scalar=risk["vol_target_scalar"],
                                corr_stress=risk["correlation_stress"],
                                slippage_bps=risk["slippage_avg_bps"],
                                kelly_mr=risk["kelly_fractions"].get("MEAN_REVERSION", 0.02),
                                kelly_tf=risk["kelly_fractions"].get("TREND_FOLLOWING", 0.02))

                # Correlation regime update
                self.risk_manager.correlation_regime.compute(time.time())

                # Telegram portfolio snapshot (cada 5 min internamente)
                await self.notifier.notify_portfolio_snapshot(summary)

                await asyncio.sleep(60)  # cada minuto
            except Exception as e:
                logger.error("metrics_error", error=str(e))
                await asyncio.sleep(30)

    async def _daily_analysis_loop(self) -> None:
        """Ejecuta análisis IA cada 24h y envía reporte a Telegram."""
        from core.ai_analyst import AIAnalyst

        analyst = AIAnalyst()
        # Esperar 1 hora antes del primer análisis (acumular datos)
        await asyncio.sleep(3600)

        while self._running:
            try:
                # Recopilar datos from trade database (metrics.get_metrics() has no recent_trades key)
                trades = []
                if hasattr(self, 'trade_repo') and self.trade_repo:
                    try:
                        db_trades = self.trade_repo.get_trades(limit=50)
                        trades = [{"pnl": t.pnl, "strategy": t.strategy, "symbol": t.symbol,
                                   "side": t.side, "entry_price": t.entry_price,
                                   "exit_price": t.exit_price, "duration_sec": t.duration_sec}
                                  for t in db_trades if t.pnl is not None]
                    except Exception:
                        pass

                sym = self.settings.symbols[0]
                df = self.market_data.get_dataframe(sym.symbol)
                last = df.iloc[-1] if not df.empty else {}

                market_state = {
                    "regime": self._last_regime.get(sym.symbol, MarketRegime.UNKNOWN).value,
                    "adx": float(last.get("adx", 0)) if not df.empty else 0,
                    "momentum": float(last.get("momentum_20", 0)) if not df.empty else 0,
                    "vol_pct": float(last.get("vol_pct", 0.5)) if not df.empty else 0.5,
                    "price": self.market_data.get_snapshot(sym.symbol).price if self.market_data.get_snapshot(sym.symbol) else 0,
                    "rsi": float(last.get("rsi", 50)) if not df.empty else 50,
                }

                current_config = {
                    "leverage": sym.leverage,
                    "sl_mult": sym.mr_atr_mult_sl,
                    "tp_mult": sym.mr_atr_mult_tp,
                    "rsi_oversold": 40,
                    "rsi_overbought": 60,
                    "adx_max": 30,
                    "risk_pct": self.settings.trading.risk_per_trade_pct * 100,
                }

                result = analyst.analyze(
                    trades=trades,
                    equity=self.risk_manager.current_equity,
                    initial_capital=self.settings.trading.initial_capital,
                    market_state=market_state,
                    current_config=current_config,
                )

                # Enviar a Telegram
                telegram_text = analyst.format_telegram(result)
                await self.notifier.notify(telegram_text)
                logger.info("daily_analysis_sent", source=result.get("source", "?"))

                # Esperar 24h
                await asyncio.sleep(86400)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("daily_analysis_error", error=str(e))
                await asyncio.sleep(3600)  # Retry en 1h

    async def shutdown(self) -> None:
        """Apaga el sistema de forma limpia."""
        logger.info("botstrike_shutting_down")
        self._running = False
        if not self.dry_run and not self.paper:
            await self.execution_engine.cancel_all()
        await self.websocket.stop()
        await self.client.close()

        # Cerrar sesión de trade database
        self.trade_db.end_session(
            final_equity=self.risk_manager.current_equity,
            max_drawdown=self.risk_manager.current_drawdown_pct,
        )

        # Imprimir métricas finales
        metrics = self.metrics.get_metrics()
        logger.info("final_metrics", **metrics)

        # Flush pending metrics to disk
        self.trading_logger._flush_metrics()

        # Notificar shutdown por Telegram
        await self.notifier.notify_shutdown(metrics)
        await self.notifier.stop()


# ── Modo Backtest ──────────────────────────────────────────────────

def run_backtest(settings: Settings, csv_path: Optional[str] = None) -> None:
    """Ejecuta backtest y muestra resultados."""
    import logging
    import pandas as pd

    # Silenciar logs durante backtest para output limpio
    logging.disable(logging.CRITICAL)
    structlog.configure(
        wrapper_class=structlog.BoundLogger,
        logger_factory=structlog.ReturnLoggerFactory(),
    )

    backtester = Backtester(settings)

    for sym_config in settings.symbols:
        symbol = sym_config.symbol
        print(f"\n{'='*60}")
        print(f"  BACKTEST: {symbol}")
        print(f"{'='*60}")

        if csv_path:
            df = pd.read_csv(csv_path)
        else:
            # Generar datos sintéticos
            start_prices = {"BTC-USD": 50000, "ETH-USD": 3000, "ADA-USD": 0.5}
            start_price = start_prices.get(symbol, 1000)
            df = Backtester.generate_sample_data(
                symbol=symbol, bars=5000, start_price=start_price
            )
            print(f"  (Usando datos sintéticos: {len(df)} barras)")

        # Backtest combinado
        print(f"\n  --- Todas las estrategias combinadas ---")
        result = backtester.run(df, symbol)
        summary = result.summary()
        _print_summary(summary)

        # Persistir en trade database
        trade_repo = TradeRepository("data/trade_database.db")
        trade_adapter = TradeDBAdapter(trade_repo, source="backtest")
        trade_adapter.import_backtest_result(
            result, symbol=symbol,
            initial_equity=settings.trading.initial_capital,
            notes=f"bar-by-bar backtest {symbol}",
        )

        # Backtest individual por estrategia
        for strat_name in ["MEAN_REVERSION", "TREND_FOLLOWING", "MARKET_MAKING"]:
            print(f"\n  --- Solo {strat_name} ---")
            result = backtester.run(df, symbol, strategies=[strat_name])
            summary = result.summary()
            _print_summary(summary)

    logging.disable(logging.NOTSET)


def run_realistic_backtest(
    settings: Settings, csv_path: Optional[str] = None, hours: float = 24.0
) -> None:
    """Ejecuta backtest realista con tick-by-tick y visualización en vivo."""
    import logging
    from core.historical_data import HistoricalDataLoader
    from backtesting.backtester import RealisticBacktester
    from backtesting.live_display import BacktestLiveDisplay

    logging.disable(logging.CRITICAL)
    structlog.configure(
        wrapper_class=structlog.BoundLogger,
        logger_factory=structlog.ReturnLoggerFactory(),
    )

    for sym_config in settings.symbols:
        symbol = sym_config.symbol

        loader = HistoricalDataLoader()

        if csv_path:
            loaded = loader.load(csv_path, symbol=symbol)
        else:
            trades_df = HistoricalDataLoader.generate_realistic_trades(
                symbol=symbol, hours=hours,
                start_price={"BTC-USD": 50000, "ETH-USD": 3000, "ADA-USD": 0.5}.get(symbol, 1000),
            )
            loader._trades[symbol] = trades_df

        bars_with_trades = loader.get_bars_with_trades(symbol, interval="1min")

        # Visualización en vivo
        display = BacktestLiveDisplay(symbol, len(bars_with_trades))
        display.start()

        bt = RealisticBacktester(settings)
        result = bt.run(
            symbol,
            bars_with_trades=bars_with_trades,
            on_bar_callback=display.update,
        )

        display.stop()
        summary = result.summary()

        print(f"\n  --- Resultado {symbol} ---")
        _print_summary(summary)

        # Persistir en trade database
        trade_repo = TradeRepository("data/trade_database.db")
        trade_adapter = TradeDBAdapter(trade_repo, source="backtest")
        trade_adapter.import_backtest_result(
            result, symbol=symbol,
            initial_equity=settings.trading.initial_capital,
            notes=f"realistic backtest {symbol}",
        )

        if result.microstructure_history:
            import numpy as np
            vpins = [m.get("vpin", 0) for m in result.microstructure_history]
            hawkes = [m.get("hawkes_ratio", 0) for m in result.microstructure_history]
            print(f"\n  --- Microestructura ---")
            print(f"    VPIN medio:     {np.mean(vpins):.4f}")
            print(f"    VPIN máx:       {np.max(vpins):.4f}")
            print(f"    Hawkes medio:   {np.mean(hawkes):.2f}x")
            print(f"    Hawkes máx:     {np.max(hawkes):.2f}x")

        if result.jsonl_path:
            print(f"\n  JSONL: {result.jsonl_path}")

    logging.disable(logging.NOTSET)


def _print_summary(s: Dict) -> None:
    """Imprime resumen de backtest formateado."""
    print(f"    Trades:         {s.get('total_trades', 0)}")
    print(f"    PnL neto:       ${s.get('net_pnl', 0):,.2f}")
    print(f"    Retorno:        {s.get('return_pct', 0):.2f}%")
    print(f"    Win rate:       {s.get('win_rate', 0):.2%}")
    print(f"    Profit factor:  {s.get('profit_factor', 0):.2f}")
    print(f"    Sharpe:         {s.get('sharpe_ratio', 0):.2f}")
    print(f"    Calmar:         {s.get('calmar_ratio', 0):.2f}")
    print(f"    Max drawdown:   {s.get('max_drawdown', 0):.2%}")
    if s.get("by_strategy"):
        for st, data in s["by_strategy"].items():
            print(f"      [{st}] trades={data['trades']}, "
                  f"pnl=${data['pnl']:,.2f}, wr={data['win_rate']:.2%}")


# ── Modo Recolección de Datos ──────────────────────────────────────

def run_data_collector(settings: Settings) -> None:
    """Arranca el servicio de recoleccion continua de datos de Strike.

    SIEMPRE recolecta de MAINNET — los datos para backtesting/simulacion
    deben reflejar el mercado real, no testnet.
    """
    from data.collector import StrikeDataCollector

    # NO aplicar testnet — el collector siempre usa mainnet
    notifier = get_notifier(settings)
    collector = StrikeDataCollector(settings, notifier=notifier)

    print(f"{'='*60}")
    print(f"  BotStrike Data Collector")
    print(f"{'='*60}")
    print(f"  Source:  MAINNET (datos reales)")
    print(f"  Symbols: {settings.symbol_names}")
    print(f"  Data dir: {collector.data_dir}")
    print(f"")
    print(f"  Recolectando:")
    print(f"    - Trades tick-by-tick (WS + REST/15s) -> Parquet diario")
    print(f"    - Klines 1m (WS + REST/60s) -> Parquet incremental")
    print(f"    - Orderbook depth (WS + REST/10s) -> Parquet diario")
    print(f"    - Flush a disco cada 30s (trades/ob) / 60s (klines)")
    print(f"")
    print(f"  Ctrl+C para detener")
    print(f"{'='*60}\n")

    loop = asyncio.new_event_loop()
    _stopping = False

    def handle_sig(sig, frame):
        nonlocal _stopping
        if _stopping:
            return
        _stopping = True
        print("\nDeteniendo recoleccion...")
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(collector.stop()))

    signal.signal(signal.SIGINT, handle_sig)

    try:
        loop.run_until_complete(collector.start())
    except KeyboardInterrupt:
        if not _stopping:
            loop.run_until_complete(collector.stop())
    finally:
        loop.close()


def run_backtest_with_real_data(settings: Settings, days: int = 7) -> None:
    """Ejecuta backtest realista usando datos reales recolectados."""
    import logging
    from core.historical_data import HistoricalDataLoader
    from backtesting.backtester import RealisticBacktester
    from backtesting.live_display import BacktestLiveDisplay

    logging.disable(logging.CRITICAL)
    structlog.configure(
        wrapper_class=structlog.BoundLogger,
        logger_factory=structlog.ReturnLoggerFactory(),
    )

    loader = HistoricalDataLoader()
    loaded = loader.load_from_collector(data_dir="data", days=days)

    if not loaded:
        print("ERROR: No hay datos recolectados en data/trades/")
        print("  Ejecuta primero: python main.py --collect-data")
        print("  O proporciona un CSV: python main.py --backtest-realistic --csv archivo.csv")
        return

    for symbol in loaded:
        # Cargar orderbook real si disponible
        ob_df = loader.get_orderbook(symbol)

        bars_with_trades = loader.get_bars_with_trades(symbol, interval="1min")
        if len(bars_with_trades) < 100:
            print(f"  {symbol}: SKIP (solo {len(bars_with_trades)} barras, minimo 100)")
            continue

        # Visualización en vivo
        display = BacktestLiveDisplay(symbol, len(bars_with_trades))
        display.start()

        bt = RealisticBacktester(settings)
        result = bt.run(
            symbol,
            bars_with_trades=bars_with_trades,
            orderbook_df=ob_df if not ob_df.empty else None,
            on_bar_callback=display.update,
        )

        display.stop()
        summary = result.summary()

        print(f"\n  --- Resultado {symbol} ---")
        _print_summary(summary)

        # Persistir en trade database
        trade_repo = TradeRepository("data/trade_database.db")
        trade_adapter = TradeDBAdapter(trade_repo, source="backtest")
        trade_adapter.import_backtest_result(
            result, symbol=symbol,
            initial_equity=settings.trading.initial_capital,
            notes=f"real data backtest {symbol} {days}d",
        )

        if result.microstructure_history:
            import numpy as np
            vpins = [m.get("vpin", 0) for m in result.microstructure_history]
            hawkes = [m.get("hawkes_ratio", 0) for m in result.microstructure_history]
            print(f"\n  --- Microestructura ---")
            print(f"    VPIN medio:     {np.mean(vpins):.4f}")
            print(f"    Hawkes medio:   {np.mean(hawkes):.2f}x")

        if result.jsonl_path:
            print(f"    JSONL: {result.jsonl_path}")

    logging.disable(logging.NOTSET)


# ── Entry Point ────────────────────────────────────────────────────

def run_storage_optimize(settings: Settings) -> None:
    """Ejecuta optimizacion de almacenamiento: compactacion + agregacion + limpieza."""
    from data_lifecycle.storage_manager import StorageManager
    from data_lifecycle.catalog import DataCatalog

    print(f"\n{'='*60}")
    print(f"  BotStrike Storage Optimization")
    print(f"{'='*60}\n")

    manager = StorageManager("data")

    # Estadisticas antes
    stats_before = manager.get_storage_stats()
    total_mb = stats_before["total_bytes"] / (1024 * 1024)
    print(f"  Almacenamiento actual: {total_mb:.1f} MB")

    symbols = manager._detect_symbols()
    if not symbols:
        print("  No hay datos para optimizar.")
        return

    print(f"  Simbolos: {symbols}\n")

    # Ejecutar optimizacion
    results = manager.optimize_all(symbols)

    # Mostrar resultados
    for symbol, info in results.get("compact_trades", {}).items():
        print(f"  Compactado trades {symbol}: {info['files']} archivos "
              f"-> {info['created']} compactados, ahorro {info.get('savings_pct', 0)}%")

    for symbol, info in results.get("compact_orderbook", {}).items():
        print(f"  Compactado orderbook {symbol}: {info['files']} archivos "
              f"-> {info['created']} compactados")

    for symbol, tfs in results.get("aggregate_klines", {}).items():
        print(f"  Klines agregadas {symbol}: {tfs}")

    retention = results.get("retention", {})
    if any(v > 0 for v in retention.values()):
        print(f"  Retencion: {retention}")

    # Actualizar catalogo
    catalog = DataCatalog("data")
    n = catalog.refresh()
    print(f"\n  Catalogo actualizado: {n} datasets")

    summary = catalog.summary()
    print(f"  Total: {summary['total_rows']:,} filas, {summary['total_size_mb']:.1f} MB")
    print(f"  Simbolos: {summary['symbols']}")


def run_analytics_report(settings: Settings, session_id: Optional[str] = None) -> None:
    """Genera reporte de rendimiento desde el trade database."""
    from trade_database.repository import TradeRepository
    from analytics.performance import PerformanceAnalyzer

    repo = TradeRepository("data/trade_database.db")
    analyzer = PerformanceAnalyzer()

    print(f"\n{'='*60}")
    print(f"  BotStrike Performance Report")
    print(f"{'='*60}\n")

    # Mostrar sesiones disponibles
    sessions = repo.get_sessions(limit=10)
    if not sessions:
        print("  No hay sesiones registradas en el trade database.")
        print("  Ejecuta un backtest o trading session primero.")
        return

    print(f"  Sesiones recientes ({len(sessions)}):")
    for s in sessions:
        from datetime import datetime
        dt = datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d %H:%M")
        print(f"    [{s.session_id}] {s.source} {s.symbol} "
              f"{dt} | {s.total_trades} trades | PnL ${s.total_pnl:,.2f}")

    # Analizar sesion especifica o la mas reciente
    target = session_id or sessions[0].session_id
    trades = repo.get_trades(session_id=target)
    if not trades:
        print(f"\n  Sesion {target}: sin trades.")
        return

    session = next((s for s in sessions if s.session_id == target), None)
    initial_eq = session.initial_equity if session else settings.trading.initial_capital

    print(f"\n  Analizando sesion: {target} ({len(trades)} trades)\n")

    # Analisis total
    report = analyzer.analyze(trades, initial_equity=initial_eq)
    _print_performance_report(report, "TOTAL")

    # Por estrategia
    by_strat = analyzer.analyze_by_strategy(trades, initial_equity=initial_eq)
    for name, r in by_strat.items():
        _print_performance_report(r, name)

    # Por regimen
    by_regime = analyzer.analyze_by_regime(trades, initial_equity=initial_eq)
    if by_regime:
        print(f"\n  --- Por regimen ---")
        for name, r in by_regime.items():
            if r.total_trades > 0:
                print(f"    {name:18s} | {r.total_trades:4d} trades | "
                      f"PnL ${r.net_pnl:>10,.2f} | WR {r.win_rate:6.1%} | "
                      f"PF {r.profit_factor:6.2f}")

    # Por simbolo
    by_sym = analyzer.analyze_by_symbol(trades, initial_equity=initial_eq)
    if by_sym:
        print(f"\n  --- Por simbolo ---")
        for name, r in by_sym.items():
            if r.total_trades > 0:
                print(f"    {name:12s} | {r.total_trades:4d} trades | "
                      f"PnL ${r.net_pnl:>10,.2f} | WR {r.win_rate:6.1%} | "
                      f"Sharpe {r.sharpe_ratio:6.2f}")

    # Correlaciones
    corr = analyzer.compute_strategy_correlation(trades)
    if corr:
        print(f"\n  --- Correlacion entre estrategias ---")
        strats = sorted(corr.keys())
        header = "    " + " ".join(f"{s:>18s}" for s in strats)
        print(header)
        for s1 in strats:
            row = f"    {s1:18s}" + " ".join(
                f"{corr[s1].get(s2, 0):>18.3f}" for s2 in strats
            )
            print(row)

    # Estrategia x Regimen (cruzado)
    cross = analyzer.analyze_cross_strategy_regime(trades, initial_equity=initial_eq)
    if cross:
        print(f"\n  --- Estrategia x Regimen ---")
        for strat, regimes in cross.items():
            for regime, r in regimes.items():
                if r.total_trades > 0:
                    print(f"    {strat:18s} x {regime:18s} | {r.total_trades:3d} trades | "
                          f"PnL ${r.net_pnl:>8,.2f} | WR {r.win_rate:5.1%}")

    # PnL por nivel de VPIN
    by_vpin = analyzer.analyze_by_vpin_bucket(trades, initial_equity=initial_eq)
    if by_vpin:
        print(f"\n  --- PnL por VPIN bucket ---")
        for name, r in sorted(by_vpin.items()):
            if r.total_trades > 0:
                print(f"    {name:18s} | {r.total_trades:4d} trades | "
                      f"PnL ${r.net_pnl:>10,.2f} | WR {r.win_rate:6.1%} | "
                      f"Sharpe {r.sharpe_ratio:6.2f}")

    # Distribuciones
    if report.drawdown_events:
        import numpy as np
        dd_arr = np.array(report.drawdown_events) * 100
        print(f"\n  --- Drawdown distribution ({len(dd_arr)} events) ---")
        print(f"    Mean: {np.mean(dd_arr):.2f}%  Median: {np.median(dd_arr):.2f}%  "
              f"Max: {np.max(dd_arr):.2f}%  p90: {np.percentile(dd_arr, 90):.2f}%")

    if report.duration_distribution:
        import numpy as np
        dur = np.array(report.duration_distribution)
        print(f"\n  --- Trade duration ({len(dur)} trades with duration) ---")
        print(f"    Mean: {np.mean(dur):.0f}s  Median: {np.median(dur):.0f}s  "
              f"Max: {np.max(dur):.0f}s  Min: {np.min(dur):.0f}s")

    if report.fee_distribution:
        import numpy as np
        fees = np.array(report.fee_distribution)
        print(f"\n  --- Fees ({len(fees)} trades) ---")
        print(f"    Total: ${np.sum(fees):,.2f}  Mean: ${np.mean(fees):.4f}  Max: ${np.max(fees):.4f}")


def _print_performance_report(r, label: str) -> None:
    """Imprime un PerformanceReport formateado."""
    print(f"\n  --- {label} ---")
    print(f"    Trades:         {r.total_trades}")
    print(f"    PnL neto:       ${r.net_pnl:,.2f}")
    print(f"    Retorno:        {r.return_pct:.2f}%")
    print(f"    Win rate:       {r.win_rate:.2%}")
    print(f"    Profit factor:  {r.profit_factor:.2f}")
    print(f"    Sharpe:         {r.sharpe_ratio:.2f}")
    print(f"    Sortino:        {r.sortino_ratio:.2f}")
    print(f"    Calmar:         {r.calmar_ratio:.2f}")
    print(f"    Max drawdown:   {r.max_drawdown:.2%}")
    print(f"    Expectancy:     ${r.expectancy:,.2f}")
    if r.var_95 != 0:
        print(f"    VaR 95%:        ${r.var_95:,.2f}")
        print(f"    CVaR 95%:       ${r.cvar_95:,.2f}")
    print(f"    Max consec W/L: {r.max_consecutive_wins}/{r.max_consecutive_losses}")


def run_stress_test(settings: Settings, symbol: str = "BTC-USD") -> None:
    """Ejecuta backtest con datos estresados (eventos extremos)."""
    import logging
    logging.disable(logging.CRITICAL)
    structlog.configure(wrapper_class=structlog.BoundLogger, logger_factory=structlog.ReturnLoggerFactory())

    from backtesting.stress_test import StressTestGenerator

    print(f"\n{'='*60}")
    print(f"  STRESS TEST: {symbol}")
    print(f"{'='*60}")

    # Generar datos base
    start_prices = {"BTC-USD": 50000, "ETH-USD": 3000, "ADA-USD": 0.5}
    df = Backtester.generate_sample_data(
        symbol=symbol, bars=5000,
        start_price=start_prices.get(symbol, 1000),
    )
    print(f"  Datos base: {len(df)} barras")

    # Inyectar eventos extremos
    gen = StressTestGenerator()
    stressed_df = gen.inject_all(df, n_crashes=3, n_gaps=5, n_low_liq=2, n_cascades=1)
    print(gen.get_events_summary())

    # Backtest normal (referencia)
    backtester = Backtester(settings)
    print(f"\n  --- Referencia (sin stress) ---")
    result_normal = backtester.run(df, symbol)
    sn = result_normal.summary()
    _print_summary(sn)

    # Backtest con stress
    print(f"\n  --- Con stress events ---")
    result_stress = backtester.run(stressed_df, symbol)
    ss = result_stress.summary()
    _print_summary(ss)

    # Comparacion
    print(f"\n  --- Comparacion ---")
    pnl_diff = ss.get("net_pnl", 0) - sn.get("net_pnl", 0)
    dd_diff = ss.get("max_drawdown", 0) - sn.get("max_drawdown", 0)
    wr_diff = ss.get("win_rate", 0) - sn.get("win_rate", 0)
    print(f"    PnL impact:      ${pnl_diff:+,.2f}")
    print(f"    MaxDD impact:    {dd_diff:+.2%}")
    print(f"    WinRate impact:  {wr_diff:+.2%}")

    robustness = "ROBUSTO" if ss.get("max_drawdown", 1) < 0.25 else "FRAGIL"
    print(f"\n    Resultado: {robustness}")
    logging.disable(logging.NOTSET)


def run_walk_forward(settings: Settings, symbol: str = "BTC-USD", n_folds: int = 5) -> None:
    """Ejecuta walk-forward backtest."""
    import logging
    logging.disable(logging.CRITICAL)
    structlog.configure(wrapper_class=structlog.BoundLogger, logger_factory=structlog.ReturnLoggerFactory())

    from backtesting.optimizer import WalkForwardBacktester
    from backtesting.backtester import Backtester

    print(f"\n{'='*60}")
    print(f"  Walk-Forward Backtest: {symbol} ({n_folds} folds)")
    print(f"{'='*60}")

    df = Backtester.generate_sample_data(
        symbol=symbol, bars=5000,
        start_price={"BTC-USD": 50000, "ETH-USD": 3000, "ADA-USD": 0.5}.get(symbol, 1000),
    )
    print(f"  Datos: {len(df)} barras sinteticas")

    wf = WalkForwardBacktester(settings)
    result = wf.run(df, symbol, n_folds=n_folds, train_pct=0.7)
    s = result.summary()

    print(f"\n  Resultados out-of-sample:")
    print(f"    Folds:             {s['n_folds']}")
    print(f"    Total trades:      {s['total_trades']}")
    print(f"    Total PnL:         ${s['total_pnl']:,.2f}")
    print(f"    Avg PnL/fold:      ${s['avg_pnl_per_fold']:,.2f}")
    print(f"    Avg Sharpe:        {s['avg_sharpe']:.2f}")
    print(f"    Avg Win Rate:      {s['avg_win_rate']:.2%}")
    print(f"    Avg Max Drawdown:  {s['avg_max_drawdown']:.2%}")
    print(f"    Consistency:       {s['consistency_ratio']:.0%} folds rentables")

    for f in result.folds:
        print(f"    Fold {f.fold_idx}: bars={f.test_bars}, trades={f.total_trades}, "
              f"PnL=${f.net_pnl:,.2f}, Sharpe={f.sharpe_ratio:.2f}")

    logging.disable(logging.NOTSET)


def run_optimizer(settings: Settings, symbol: str = "BTC-USD", metric: str = "sharpe_ratio") -> None:
    """Ejecuta parameter optimization via grid search."""
    import logging
    logging.disable(logging.CRITICAL)
    structlog.configure(wrapper_class=structlog.BoundLogger, logger_factory=structlog.ReturnLoggerFactory())

    from backtesting.optimizer import ParameterOptimizer
    from backtesting.backtester import Backtester

    print(f"\n{'='*60}")
    print(f"  Parameter Optimization: {symbol} (metric={metric})")
    print(f"{'='*60}")

    df = Backtester.generate_sample_data(
        symbol=symbol, bars=3000,
        start_price={"BTC-USD": 50000, "ETH-USD": 3000, "ADA-USD": 0.5}.get(symbol, 1000),
    )
    print(f"  Datos: {len(df)} barras")

    opt = ParameterOptimizer(settings)
    result = opt.optimize(df, symbol, metric=metric)
    s = result.summary()

    print(f"\n  Grid search: {s['completed']}/{s['total_combinations']} combinaciones en {s['duration_sec']}s")
    print(f"\n  Top 10 resultados (por {metric}):\n")
    print(f"  {'#':>3s} {'Sharpe':>8s} {'PnL':>10s} {'WR':>8s} {'PF':>6s} {'MaxDD':>8s} | Params")

    for i, r in enumerate(result.top_n(10)):
        params_str = ", ".join(f"{k}={v}" for k, v in r.params.items())
        print(f"  {i+1:3d} {r.sharpe_ratio:8.2f} ${r.net_pnl:>9,.2f} {r.win_rate:8.2%} "
              f"{r.profit_factor:6.2f} {r.max_drawdown:8.2%} | {params_str}")

    if result.best:
        print(f"\n  MEJOR: {result.best.params}")
        print(f"    Sharpe={result.best.sharpe_ratio:.2f}, PnL=${result.best.net_pnl:,.2f}, "
              f"WR={result.best.win_rate:.2%}")

    logging.disable(logging.NOTSET)


def launch_dashboard() -> None:
    """Lanza el dashboard Streamlit como subproceso."""
    import subprocess
    app_path = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")
    print(f"Launching BotStrike Dashboard...")
    print(f"  streamlit run {app_path}")
    print(f"  Abre http://localhost:8501 en tu navegador\n")
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", app_path,
        "--server.headless=true",
        "--theme.base=dark",
        "--theme.primaryColor=#6C5CE7",
        "--theme.backgroundColor=#0E1117",
        "--theme.secondaryBackgroundColor=#1E2130",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="BotStrike Trading System")
    parser.add_argument("--backtest", action="store_true", help="Run backtest mode (bar-by-bar)")
    parser.add_argument("--backtest-realistic", action="store_true", help="Run realistic tick-by-tick backtest")
    parser.add_argument("--backtest-real", action="store_true", help="Backtest with collected real Strike data")
    parser.add_argument("--collect-data", action="store_true", help="Start data collection service")
    parser.add_argument("--csv", type=str, help="CSV/Parquet file for backtest data")
    parser.add_argument("--hours", type=float, default=24.0, help="Hours of synthetic data for realistic backtest")
    parser.add_argument("--days", type=int, default=7, help="Days of collected data for real backtest")
    parser.add_argument("--dry-run", action="store_true", help="Live mode without real orders (legacy)")
    parser.add_argument("--paper", action="store_true", help="Paper trading: real market data, simulated fills, full PnL tracking")
    parser.add_argument("--binance", action="store_true", help="Use Binance WebSocket for market data (more liquid than Strike)")
    parser.add_argument("--testnet", action="store_true", default=False, help="Use testnet (default: off for mainnet)")
    parser.add_argument("--no-testnet", action="store_true", help="Force mainnet (overrides --testnet)")
    parser.add_argument("--dashboard", action="store_true", help="Launch Streamlit dashboard")
    parser.add_argument("--optimize-storage", action="store_true", help="Compact and optimize data storage")
    parser.add_argument("--analytics", action="store_true", help="Show performance analytics report")
    parser.add_argument("--session-id", type=str, help="Session ID for analytics report")
    parser.add_argument("--catalog", action="store_true", help="Show data catalog")
    parser.add_argument("--backtest-stress", action="store_true", help="Backtest with injected extreme events (stress test)")
    parser.add_argument("--walk-forward", action="store_true", help="Run walk-forward backtest")
    parser.add_argument("--optimize", action="store_true", help="Run parameter optimization (grid search)")
    parser.add_argument("--download-binance", action="store_true", help="Download historical data from Binance for backtesting")
    parser.add_argument("--kline-days", type=int, default=90, help="Days of klines to download from Binance (default: 90)")
    parser.add_argument("--trade-days", type=int, default=7, help="Days of trades to download from Binance (default: 7)")
    parser.add_argument("--symbol", type=str, default="BTC-USD", help="Symbol for walk-forward/optimize")
    parser.add_argument("--folds", type=int, default=5, help="Number of folds for walk-forward")
    parser.add_argument("--metric", type=str, default="sharpe_ratio", help="Metric for optimization")
    args = parser.parse_args()

    use_testnet = args.testnet and not args.no_testnet
    settings = Settings(use_testnet=use_testnet)

    if args.catalog:
        from data_lifecycle.catalog import DataCatalog
        catalog = DataCatalog("data")
        catalog.refresh()
        summary = catalog.summary()
        print(f"\nData Catalog: {summary['total_datasets']} datasets, "
              f"{summary['total_rows']:,} rows, {summary['total_size_mb']:.1f} MB")
        for d in catalog.list_datasets():
            print(f"  {d.symbol:10s} {d.data_type:20s} {d.timeframe:5s} "
                  f"{d.total_rows:>10,} rows  {d.total_bytes / 1024:>8,.0f} KB  "
                  f"{d.date_start} - {d.date_end}")
        return
    elif args.optimize_storage:
        run_storage_optimize(settings)
        return
    elif args.analytics:
        run_analytics_report(settings, args.session_id)
        return
    elif args.backtest_stress:
        run_stress_test(settings, args.symbol)
        return
    elif args.walk_forward:
        run_walk_forward(settings, args.symbol, args.folds)
        return
    elif args.optimize:
        run_optimizer(settings, args.symbol, args.metric)
        return
    elif args.download_binance:
        from data.binance_downloader import BinanceDownloader
        downloader = BinanceDownloader(
            data_dir="data/binance",
            symbols=settings.symbol_names,
        )
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                downloader.download_all(
                    kline_days=args.kline_days,
                    trade_days=args.trade_days,
                )
            )
        finally:
            loop.run_until_complete(downloader.close())
            loop.close()
        return
    elif args.collect_data:
        run_data_collector(settings)
    elif args.dashboard:
        launch_dashboard()
    elif args.backtest_real:
        run_backtest_with_real_data(settings, args.days)
    elif args.backtest_realistic:
        run_realistic_backtest(settings, args.csv, args.hours)
    elif args.backtest:
        run_backtest(settings, args.csv)
    else:
        bot = BotStrike(settings, dry_run=args.dry_run, paper=args.paper,
                        use_binance=args.binance)

        if args.paper:
            venue = settings.trading.exchange_venue.upper()
            source = "BINANCE" if (args.binance or venue == "BINANCE") else "STRIKE MAINNET"
            print(f"\n{'='*60}")
            print(f"  BotStrike PAPER TRADING")
            print(f"{'='*60}")
            print(f"  Exchange:         {venue}")
            print(f"  Datos de mercado: {source} (precios reales)")
            print(f"  Ordenes:          SIMULADAS (sin dinero real)")
            print(f"  PnL tracking:     COMPLETO (trade DB + analytics)")
            print(f"  Simbolos:         {settings.symbol_names}")
            print(f"  Capital virtual:  ${settings.trading.initial_capital:,.0f}")
            print(f"  Alloc MR:         {settings.trading.allocation_mean_reversion*100:.0f}%")
            print(f"  Alloc OFM:        {settings.trading.allocation_order_flow_momentum*100:.0f}%")
            print(f"  Ctrl+C para detener")
            print(f"{'='*60}\n")

        # Manejar Ctrl+C
        loop = asyncio.new_event_loop()
        _stopping = False

        def handle_signal(sig, frame):
            nonlocal _stopping
            if _stopping:
                return
            _stopping = True
            print("\nShutting down...")
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(bot.shutdown()))

        signal.signal(signal.SIGINT, handle_signal)

        try:
            loop.run_until_complete(bot.start())
        except KeyboardInterrupt:
            if not _stopping:
                loop.run_until_complete(bot.shutdown())
        finally:
            loop.close()


if __name__ == "__main__":
    main()
