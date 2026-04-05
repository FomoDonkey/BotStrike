"""
Backtesting Module — Simulación de estrategias con datos históricos.
Incluye fees, slippage, funding rate y liquidaciones.
Permite backtest individual y combinado de estrategias.

Contiene:
  - Backtester: backtester original simplificado (bar-a-bar)
  - RealisticBacktester: backtester que replica el live trading exactamente,
    con tick-by-tick microestructura, PortfolioManager, RiskManager y JSONL logging.
"""
from __future__ import annotations
import copy
import json
import os
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config.settings import Settings, SymbolConfig, TradingConfig
from core.microstructure import MicrostructureEngine, MicrostructureSnapshot
from core.types import (
    Signal, Side, StrategyType, MarketRegime, MarketSnapshot, Position, OrderBook, Trade,
    OrderBookLevel,
)
from core.indicators import Indicators
from core.regime_detector import RegimeDetector
from strategies.base import BaseStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.fibonacci_retracement import FibonacciRetracementStrategy

# Archived strategies — lazy import only if explicitly requested in backtest
def _get_strategy_class(name: str):
    """Lazy-load archived strategies for backtest-only use."""
    if name == "TREND_FOLLOWING":
        from archive.strategies.trend_following import TrendFollowingStrategy
        return TrendFollowingStrategy
    elif name == "MARKET_MAKING":
        from archive.strategies.market_making import MarketMakingStrategy
        return MarketMakingStrategy
    elif name == "ORDER_FLOW_MOMENTUM":
        from archive.strategies.order_flow_momentum import OrderFlowMomentumStrategy
        return OrderFlowMomentumStrategy
    return None
from risk.risk_manager import RiskManager
from portfolio.portfolio_manager import PortfolioManager
from execution.slippage import compute_slippage, compute_slippage_bps
from core.orderbook_alpha import OrderBookImbalance
import structlog

logger = structlog.get_logger(__name__)


class BacktestPosition:
    """Posición simulada durante backtest."""

    def __init__(self, symbol: str, side: Side, size: float, entry_price: float,
                 strategy: StrategyType, leverage: int = 1,
                 entry_timestamp: float = 0.0, slippage_bps: float = 0.0,
                 entry_metadata: Optional[Dict] = None,
                 stop_loss: float = 0.0, take_profit: float = 0.0) -> None:
        self.symbol = symbol
        self.side = side
        self.size = size
        self.entry_price = entry_price
        self.strategy = strategy
        self.leverage = leverage
        self.entry_timestamp = entry_timestamp
        self.slippage_bps = slippage_bps
        self.entry_metadata = entry_metadata or {}
        self.unrealized_pnl = 0.0
        self.stop_loss = stop_loss
        self.take_profit = take_profit

    def update_pnl(self, current_price: float) -> float:
        if self.side == Side.BUY:
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size
        return self.unrealized_pnl

    def close(self, exit_price: float, fee_rate: float) -> float:
        """Cierra posición y retorna PnL neto."""
        if self.side == Side.BUY:
            gross_pnl = (exit_price - self.entry_price) * self.size
        else:
            gross_pnl = (self.entry_price - exit_price) * self.size

        # Fees: entry + exit (each side charged separately)
        fee = (self.entry_price * self.size + exit_price * self.size) * fee_rate
        self._last_fee = fee
        return gross_pnl - fee

    @property
    def last_fee(self) -> float:
        """Fee del ultimo close()."""
        return getattr(self, "_last_fee", 0.0)

    def trade_dict(self, bar: int, symbol: str, side: str, exit_price: float,
                   pnl: float, exit_timestamp: float = 0.0) -> dict:
        """Construye dict de trade con todos los campos de tracking."""
        result = {
            "bar": bar, "symbol": symbol, "side": side,
            "entry": self.entry_price, "exit": exit_price,
            "size": self.size, "pnl": round(pnl, 4),
            "strategy": self.strategy.value,
            "fee": round(self.last_fee, 4),
            "slippage_bps": round(self.slippage_bps, 2),
            "duration_sec": round(exit_timestamp - self.entry_timestamp, 2) if self.entry_timestamp > 0 and exit_timestamp > 0 else 0,
            "timestamp": exit_timestamp,
            "metadata": self.entry_metadata,
        }
        return result

    def is_liquidated(self, current_price: float, maintenance_pct: float = 0.02) -> bool:
        """Verifica si la posición sería liquidada.

        Liquidation: loss >= initial_margin * (1 - maintenance_pct).
        With 2x leverage: margin = 50% notional, liquidate when ~49% price move against.
        """
        if self.leverage <= 1:
            return False
        initial_margin = (self.entry_price * self.size) / self.leverage
        pnl = self.update_pnl(current_price)
        if pnl >= 0:
            return False
        return abs(pnl) >= initial_margin * (1.0 - maintenance_pct)

    def to_position(self, mark_price: float) -> Position:
        self.update_pnl(mark_price)
        return Position(
            symbol=self.symbol, side=self.side, size=self.size,
            entry_price=self.entry_price, mark_price=mark_price,
            unrealized_pnl=self.unrealized_pnl, leverage=self.leverage,
            strategy=self.strategy,
        )


class BacktestResult:
    """Resultados de un backtest."""

    def __init__(self) -> None:
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []
        self.drawdown_curve: List[float] = []
        self.regime_history: List[str] = []
        self.signals_generated: int = 0
        self.signals_executed: int = 0

    def summary(self) -> Dict:
        if not self.trades:
            return {"total_trades": 0, "net_pnl": 0}

        pnls = [t["pnl"] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total_pnl = sum(pnls)

        peak = self.equity_curve[0] if self.equity_curve else 0
        max_dd = 0.0
        for eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        win_rate = len(wins) / len(pnls) if pnls else 0
        pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 9999.99

        # Sharpe — from equity curve daily returns (proper method)
        sharpe = 0
        if len(self.equity_curve) > 2:
            eq = np.array(self.equity_curve)
            # Sample equity at daily boundaries (every ~1440 bars for 1m data)
            # Use simple approach: take every Nth point where N = bars per day
            n_bars = len(eq)
            if self.trades:
                first_ts = self.trades[0].get("timestamp", 0)
                last_ts = self.trades[-1].get("timestamp", 0)
                span_sec = max(last_ts - first_ts, 1)
                bars_per_day = max(1, int(n_bars / (span_sec / 86400)))
            else:
                bars_per_day = 1440  # assume 1m bars

            # Sample equity at daily intervals
            daily_eq = eq[::bars_per_day]
            if len(daily_eq) < 2:
                daily_eq = eq[::max(1, len(eq) // 10)]  # at least 10 samples

            if len(daily_eq) > 2:
                daily_eq = np.where(daily_eq == 0, 1e-10, daily_eq)
                daily_returns = np.diff(daily_eq) / daily_eq[:-1]
                daily_returns = daily_returns[np.isfinite(daily_returns)]
                if len(daily_returns) > 1 and np.std(daily_returns) > 0:
                    sharpe = float(np.mean(daily_returns) / np.std(daily_returns) * (365 ** 0.5))  # Crypto: 365 days

        # Calmar ratio (annualized)
        calmar = 0.0
        if max_dd > 0 and self.equity_curve and self.trades:
            total_return = total_pnl / self.equity_curve[0]
            first_ts = self.trades[0].get("timestamp", 0)
            last_ts = self.trades[-1].get("timestamp", 0)
            span_days = max((last_ts - first_ts) / 86400.0, 1.0) if last_ts > first_ts else 1.0
            annual_return = total_return * (365.0 / span_days)
            calmar = annual_return / max_dd

        # By strategy
        by_strategy = {}
        for st in StrategyType:
            st_trades = [t for t in self.trades if t.get("strategy") == st.value]
            if st_trades:
                st_pnls = [t["pnl"] for t in st_trades]
                st_wins = [p for p in st_pnls if p > 0]
                by_strategy[st.value] = {
                    "trades": len(st_trades),
                    "pnl": round(sum(st_pnls), 2),
                    "win_rate": round(len(st_wins) / len(st_pnls), 4),
                }

        # Additional metrics
        avg_win = round(np.mean(wins), 2) if wins else 0
        avg_loss = round(np.mean(losses), 2) if losses else 0
        # Expectancy = (WR * avg_win) + ((1-WR) * avg_loss)  [avg_loss is negative]
        expectancy = round(win_rate * avg_win + (1 - win_rate) * avg_loss, 2) if pnls else 0

        # Total fees
        total_fees = sum(
            t.get("fee", 0) for t in self.trades
        )

        # Max consecutive losses
        max_consec_losses = 0
        cur_consec = 0
        for p in pnls:
            if p < 0:
                cur_consec += 1
                max_consec_losses = max(max_consec_losses, cur_consec)
            else:
                cur_consec = 0

        # Average trade duration (seconds)
        durations = [t.get("duration_sec", 0) for t in self.trades if t.get("duration_sec", 0) > 0]
        avg_duration_min = round(np.mean(durations) / 60, 1) if durations else 0

        # Sortino ratio (downside deviation only)
        sortino = 0
        if len(self.equity_curve) > 2:
            eq = np.array(self.equity_curve)
            bars_per_day_s = bars_per_day if 'bars_per_day' in dir() else 1440
            daily_eq_s = eq[::max(1, bars_per_day_s)]
            if len(daily_eq_s) > 2:
                daily_eq_s = np.where(daily_eq_s == 0, 1e-10, daily_eq_s)
                dr = np.diff(daily_eq_s) / daily_eq_s[:-1]
                dr = dr[np.isfinite(dr)]
                downside = dr[dr < 0]
                if len(downside) > 0 and np.std(downside) > 0:
                    sortino = float(np.mean(dr) / np.std(downside) * (365 ** 0.5))  # Crypto: 365 days

        return {
            "total_trades": len(self.trades),
            "net_pnl": round(total_pnl, 2),
            "return_pct": round(total_pnl / self.equity_curve[0] * 100, 2) if self.equity_curve else 0,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(pf, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "calmar_ratio": round(calmar, 2),
            "max_drawdown": round(max_dd, 4),
            "avg_trade_pnl": round(total_pnl / len(pnls), 2),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "total_fees": round(total_fees, 2),
            "max_consecutive_losses": max_consec_losses,
            "avg_duration_min": avg_duration_min,
            "signals_generated": self.signals_generated,
            "signals_executed": self.signals_executed,
            "by_strategy": by_strategy,
        }


class Backtester:
    """Motor de backtesting para estrategias individuales y combinadas."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self,
        df: pd.DataFrame,
        symbol: str,
        strategies: Optional[List[str]] = None,
        funding_rate: float = 0.0001,
        funding_interval_bars: int = 480,  # cada 8h si barras de 1min
    ) -> BacktestResult:
        """Ejecuta backtest sobre datos históricos OHLCV.

        Args:
            df: DataFrame con columnas open, high, low, close, volume
            symbol: Símbolo a simular
            strategies: Lista de estrategias a usar (None = todas)
            funding_rate: Funding rate por intervalo
            funding_interval_bars: Barras entre pagos de funding

        Returns:
            BacktestResult con todos los resultados
        """
        sym_config = self.settings.get_symbol_config(symbol)
        trading_config = self.settings.trading
        result = BacktestResult()

        # Inicializar estrategias
        active_strategies: List[BaseStrategy] = []
        strat_names = strategies or ["MEAN_REVERSION"]
        if "MEAN_REVERSION" in strat_names:
            _mr = MeanReversionStrategy(trading_config)
            _mr.backtest_mode = True
            active_strategies.append(_mr)
        if "FIBONACCI_RETRACEMENT" in strat_names:
            _fib = FibonacciRetracementStrategy(trading_config)
            _fib.backtest_mode = True
            active_strategies.append(_fib)
        # Archived strategies (lazy-loaded if explicitly requested)
        for archived_name in ["TREND_FOLLOWING", "MARKET_MAKING", "ORDER_FLOW_MOMENTUM"]:
            if archived_name in strat_names:
                cls = _get_strategy_class(archived_name)
                if cls:
                    active_strategies.append(cls(trading_config))

        regime_detector = RegimeDetector()

        # Microestructura para backtest
        micro_engine = MicrostructureEngine(
            symbols=[symbol],
            config=self.settings.get_microstructure_config(),
        )

        # OBI calculator
        obi_calculator = OrderBookImbalance(levels=5, decay=0.5)

        # Estado del backtest
        equity = trading_config.initial_capital
        positions: Dict[str, BacktestPosition] = {}
        result.equity_curve.append(equity)

        # Calcular indicadores
        df = df.copy()
        df = Indicators.compute_all(df, {
            "ema_fast": sym_config.tf_ema_fast,
            "ema_slow": sym_config.tf_ema_slow,
            "zscore_lookback": sym_config.mr_lookback,
        })

        # Iterar sobre barras (desde suficiente lookback)
        start_idx = max(sym_config.mr_lookback, sym_config.tf_ema_slow * 3, 50)

        for i in range(start_idx, len(df)):
            bar = df.iloc[i]
            price = float(bar["close"])
            open_ = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])

            # Windowed slice: last 500 bars (avoids O(n^2) full-prefix copy)
            window_start = max(0, i - 500)
            df_slice = df.iloc[window_start:i + 1]

            # Detectar régimen
            regime = regime_detector.detect(df_slice, symbol, sym_config)
            result.regime_history.append(regime.value)

            ts = float(bar.get("timestamp", i * 60))

            # Verificar liquidaciones
            for key in list(positions.keys()):
                pos = positions[key]
                if pos.is_liquidated(price):
                    liq_pnl = -(pos.entry_price * pos.size) / pos.leverage
                    equity += liq_pnl
                    result.trades.append(pos.trade_dict(i, symbol, "LIQUIDATION", price, liq_pnl, ts))
                    del positions[key]

            # Check SL/TP on existing positions
            # When both SL and TP could trigger on the same bar,
            # use distance from open to determine which hit first (reduces bias)
            for key in list(positions.keys()):
                pos = positions[key]
                if pos.stop_loss <= 0 and pos.take_profit <= 0:
                    continue
                hit = False
                exit_price_sltp = 0.0
                exit_side_sltp = ""
                if pos.side == Side.BUY:
                    sl_hit = pos.stop_loss > 0 and low <= pos.stop_loss
                    tp_hit = pos.take_profit > 0 and high >= pos.take_profit
                    if sl_hit and tp_hit:
                        # Both hit — closer to open wins
                        sl_dist = abs(open_ - pos.stop_loss)
                        tp_dist = abs(open_ - pos.take_profit)
                        if sl_dist <= tp_dist:
                            exit_price_sltp, exit_side_sltp, hit = pos.stop_loss, "SL_LONG", True
                        else:
                            exit_price_sltp, exit_side_sltp, hit = pos.take_profit, "TP_LONG", True
                    elif sl_hit:
                        exit_price_sltp, exit_side_sltp, hit = pos.stop_loss, "SL_LONG", True
                    elif tp_hit:
                        exit_price_sltp, exit_side_sltp, hit = pos.take_profit, "TP_LONG", True
                else:
                    sl_hit = pos.stop_loss > 0 and high >= pos.stop_loss
                    tp_hit = pos.take_profit > 0 and low <= pos.take_profit
                    if sl_hit and tp_hit:
                        sl_dist = abs(open_ - pos.stop_loss)
                        tp_dist = abs(open_ - pos.take_profit)
                        if sl_dist <= tp_dist:
                            exit_price_sltp, exit_side_sltp, hit = pos.stop_loss, "SL_SHORT", True
                        else:
                            exit_price_sltp, exit_side_sltp, hit = pos.take_profit, "TP_SHORT", True
                    elif sl_hit:
                        exit_price_sltp, exit_side_sltp, hit = pos.stop_loss, "SL_SHORT", True
                    elif tp_hit:
                        exit_price_sltp, exit_side_sltp, hit = pos.take_profit, "TP_SHORT", True
                if hit:
                    pnl = pos.close(exit_price_sltp, trading_config.taker_fee)
                    equity += pnl
                    result.trades.append(pos.trade_dict(i, symbol, exit_side_sltp, exit_price_sltp, pnl, ts))
                    del positions[key]

            # Funding rate
            if i % funding_interval_bars == 0:
                for pos in positions.values():
                    funding_cost = pos.size * price * funding_rate
                    if pos.side == Side.BUY:
                        equity -= funding_cost
                    else:
                        equity += funding_cost

            # Crear snapshot simulado
            snapshot = MarketSnapshot(
                symbol=symbol, timestamp=float(bar.get("timestamp", i)),
                price=price, mark_price=price, index_price=price,
                funding_rate=funding_rate, volume_24h=0, open_interest=0,
                orderbook=OrderBook(
                    symbol=symbol, timestamp=float(bar.get("timestamp", i)),
                    bids=[],  # no hay orderbook en backtest
                    asks=[],
                ),
            )
            # Simular mid_price para market making
            atr = float(bar.get("atr", price * 0.001)) if not pd.isna(bar.get("atr")) else price * 0.001
            from core.types import OrderBookLevel
            snapshot.orderbook.bids = [OrderBookLevel(price - atr * 0.01, 1.0)]
            snapshot.orderbook.asks = [OrderBookLevel(price + atr * 0.01, 1.0)]

            # Actualizar microestructura con datos de barra
            micro_engine.on_bar(
                symbol, float(bar["open"]), high, low, price,
                float(bar.get("volume", 0)),
                float(bar.get("timestamp", i)),
            )
            micro_snap = micro_engine.get_snapshot(symbol)

            # Generar señales de cada estrategia activa
            # MR only evaluates every 15 bars (15m at 1m resolution) — no point running divergence
            # detection on every 1m bar since 15m is the minimum timeframe
            all_signals: List[Signal] = []
            for strat in active_strategies:
                if not strat.should_activate(regime):
                    continue

                # MR now operates on 1m bars directly — evaluate every bar

                # Capital asignado (simplificado)
                alloc = equity / len(active_strategies) / len(self.settings.symbols)

                # Posición actual para esta estrategia
                pos_key = f"{symbol}_{strat.strategy_type.value}"
                current_pos = None
                if pos_key in positions:
                    current_pos = positions[pos_key].to_position(price)

                obi_result_simple = obi_calculator.compute(snapshot.orderbook)
                signals = strat.generate_signals(
                    symbol, df_slice, snapshot, regime, sym_config, alloc, current_pos,
                    micro=micro_snap, obi=obi_result_simple,
                )
                all_signals.extend(signals)
                result.signals_generated += len(signals)

            # Procesar señales
            for signal in all_signals:
                pos_key = f"{symbol}_{signal.strategy.value}"

                # Señal de salida
                if signal.metadata.get("action") in ("exit_mean_reversion", "trailing_stop_hit"):
                    if pos_key in positions:
                        pos = positions[pos_key]
                        # Apply slippage to exit price (adverse direction)
                        exit_slip = compute_slippage(
                            base_bps=trading_config.slippage_bps, price=price,
                            size_usd=pos.size * price, regime=regime.value,
                        )
                        exit_price = price - exit_slip if pos.side == Side.BUY else price + exit_slip
                        pnl = pos.close(exit_price, trading_config.taker_fee)
                        equity += pnl
                        result.trades.append(pos.trade_dict(i, symbol, "CLOSE_" + pos.side.value, exit_price, pnl, ts))
                        del positions[pos_key]
                        result.signals_executed += 1
                    continue

                # Señal de entrada (no abrir si ya hay posición)
                if pos_key not in positions:
                    size = signal.size_usd / price if price > 0 else 0
                    if size > 0:
                        # Aplicar slippage dinamico
                        slippage_applied = compute_slippage(
                            base_bps=trading_config.slippage_bps, price=price,
                            size_usd=signal.size_usd, regime=regime.value,
                            atr=float(atr) if not pd.isna(atr) else 0,
                        )
                        actual_slip_bps = compute_slippage_bps(
                            base_bps=trading_config.slippage_bps, price=price,
                            size_usd=signal.size_usd, regime=regime.value,
                            atr=float(atr) if not pd.isna(atr) else 0,
                        )
                        if signal.side == Side.BUY:
                            fill_price = price + slippage_applied
                        else:
                            fill_price = price - slippage_applied

                        positions[pos_key] = BacktestPosition(
                            symbol=symbol, side=signal.side, size=size,
                            entry_price=fill_price, strategy=signal.strategy,
                            leverage=sym_config.leverage,
                            entry_timestamp=ts,
                            slippage_bps=actual_slip_bps,
                            entry_metadata=signal.metadata.copy(),
                            stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit,
                        )
                        result.signals_executed += 1
                        # SL/TP checked on NEXT bar (no intra-bar look-ahead)

            # Actualizar equity con unrealized PnL
            unrealized = sum(p.update_pnl(price) for p in positions.values())
            result.equity_curve.append(equity + unrealized)

        # Cerrar posiciones abiertas al final
        final_price = float(df.iloc[-1]["close"])
        final_ts = float(df.iloc[-1].get("timestamp", len(df) * 60))
        for pos_key, pos in positions.items():
            pnl = pos.close(final_price, trading_config.taker_fee)
            equity += pnl
            result.trades.append(pos.trade_dict(len(df) - 1, symbol, "CLOSE_EOD", final_price, pnl, final_ts))

        return result

    @staticmethod
    def generate_sample_data(
        symbol: str = "BTC-USD", bars: int = 5000, start_price: float = 50000.0,
        volatility: float = 0.02, trend: float = 0.0001,
    ) -> pd.DataFrame:
        """Genera datos OHLCV sintéticos para pruebas.

        Incluye regímenes alternantes: trending, ranging, breakout.
        """
        rng = np.random.default_rng(42)
        prices = [start_price]
        volumes = []

        for i in range(1, bars):
            # Alternar regímenes cada ~500 barras
            regime_phase = (i // 500) % 3
            if regime_phase == 0:  # trending
                drift = trend * 3
                vol = volatility * 0.8
            elif regime_phase == 1:  # ranging
                drift = 0
                vol = volatility * 0.5
            else:  # breakout
                drift = trend * 5
                vol = volatility * 2.0

            ret = drift + vol * rng.standard_normal()
            prices.append(prices[-1] * (1 + ret))
            volumes.append(abs(rng.standard_normal()) * 100 + 50)

        volumes.insert(0, 100)

        df = pd.DataFrame({
            "timestamp": np.arange(bars) * 60,
            "close": prices,
            "volume": volumes,
        })
        # Generar OHLC a partir de close
        noise = rng.uniform(0.001, 0.005, bars)
        df["open"] = df["close"].shift(1).fillna(df["close"])
        df["high"] = df[["open", "close"]].max(axis=1) * (1 + noise)
        df["low"] = df[["open", "close"]].min(axis=1) * (1 - noise)

        return df


# ══════════════════════════════════════════════════════════════════
# RealisticBacktester — Replica exacta del live trading
# ══════════════════════════════════════════════════════════════════

class RealisticBacktestResult(BacktestResult):
    """Resultado extendido con historial de microestructura y métricas JSONL."""

    def __init__(self) -> None:
        super().__init__()
        self.microstructure_history: List[Dict] = []
        self.portfolio_history: List[Dict] = []
        self.allocation_history: List[Dict] = []
        self.jsonl_path: Optional[str] = None


class RealisticBacktester:
    """Backtester que replica el loop de live trading EXACTAMENTE.

    A diferencia del Backtester básico, este usa:
      - PortfolioManager para asignación dinámica de capital por régimen
      - RiskManager con todos los filtros (drawdown, exposure, consecutivas, micro)
      - MicrostructureEngine tick-by-tick (no solo bar-by-bar)
      - JSONL logging idéntico al de producción
      - MarketSnapshots con orderbook simulado realista

    Acepta dos formatos de entrada:
      1. bars_with_trades: lista de (bar_dict, trades_list) del HistoricalDataLoader
         → alimenta microestructura tick-by-tick, luego procesa la barra
      2. DataFrame OHLCV clásico → fallback al modo bar-by-bar (compatible)

    El flujo por barra replica _process_symbol() de main.py:
      1. Procesar trades tick-by-tick → microestructura
      2. Cerrar barra → recalcular indicadores
      3. Detectar régimen
      4. Obtener snapshot de microestructura
      5. Para cada estrategia:
         a. should_activate(regime)?
         b. should_strategy_trade()?
         c. get_allocation()
         d. generate_signals(micro=snap)
      6. validate_signal(micro=snap) por RiskManager
      7. Ejecutar señal simulada (slippage, fees, SL/TP intra-bar)
      8. Logging JSONL
    """

    def __init__(self, settings: Settings, output_dir: str = "logs") -> None:
        self.settings = settings
        self.output_dir = output_dir

    def run(
        self,
        symbol: str,
        bars_with_trades: Optional[List[Tuple[dict, List[dict]]]] = None,
        df: Optional[pd.DataFrame] = None,
        orderbook_df: Optional[pd.DataFrame] = None,
        strategies: Optional[List[str]] = None,
        funding_rate: float = 0.0001,
        funding_interval_bars: int = 480,
        on_bar_callback: Optional[callable] = None,
        ml_filter: Optional[Any] = None,
    ) -> RealisticBacktestResult:
        """Ejecuta backtest realista.

        Args:
            symbol: Simbolo a simular
            bars_with_trades: Lista de (bar_dict, trades_list) para tick-by-tick.
                              Si None, usa df en modo bar-by-bar.
            df: DataFrame OHLCV (fallback si no hay bars_with_trades)
            orderbook_df: Snapshots reales de orderbook (del collector).
                          Columnas: timestamp, best_bid, best_ask, spread, bid_depth, ask_depth.
                          Si None, simula con ATR.
            strategies: Lista de estrategias (None = todas)
            funding_rate: Funding rate por intervalo
            funding_interval_bars: Barras entre pagos de funding
        """
        sym_config = self.settings.get_symbol_config(symbol)
        trading_config = self.settings.trading
        result = RealisticBacktestResult()

        # ── Inicializar componentes (idéntico a BotStrike.__init__) ──

        active_strategies: List[BaseStrategy] = []
        strat_names = strategies or ["MEAN_REVERSION"]
        if "MEAN_REVERSION" in strat_names:
            _mr = MeanReversionStrategy(trading_config)
            _mr.backtest_mode = True
            active_strategies.append(_mr)
        for archived_name in ["TREND_FOLLOWING", "MARKET_MAKING", "ORDER_FLOW_MOMENTUM"]:
            if archived_name in strat_names:
                cls = _get_strategy_class(archived_name)
                if cls:
                    active_strategies.append(cls(trading_config))

        regime_detector = RegimeDetector()
        risk_manager = RiskManager(self.settings)
        portfolio_manager = PortfolioManager(self.settings, risk_manager)
        micro_engine = MicrostructureEngine(
            symbols=[symbol],
            config=self.settings.get_microstructure_config(),
        )

        # OBI calculator para pasar a estrategias
        obi_calculator = OrderBookImbalance(levels=5, decay=0.5)

        # Estado
        equity = trading_config.initial_capital
        risk_manager.update_equity(equity)
        positions: Dict[str, BacktestPosition] = {}
        result.equity_curve.append(equity)
        last_regime = MarketRegime.UNKNOWN

        # JSONL output
        os.makedirs(self.output_dir, exist_ok=True)
        jsonl_path = os.path.join(
            self.output_dir, f"backtest_{symbol}_{int(time.time())}.jsonl"
        )
        result.jsonl_path = jsonl_path
        jsonl_file = open(jsonl_path, "w")
        _jsonl_open = True

        def log_jsonl(record: dict) -> None:
            try:
                if _jsonl_open:
                    jsonl_file.write(json.dumps(record, default=str) + "\n")
            except Exception:
                pass  # No perder el backtest por error de log

        def close_jsonl() -> None:
            nonlocal _jsonl_open
            if _jsonl_open:
                _jsonl_open = False
                jsonl_file.close()

        import atexit
        atexit.register(close_jsonl)

        # ── Construir DataFrame OHLCV ────────────────────────────

        if bars_with_trades is not None:
            # Extraer OHLCV de las barras
            bar_records = [b for b, _ in bars_with_trades]
            ohlcv_df = pd.DataFrame(bar_records)
        elif df is not None:
            ohlcv_df = df.copy()
            # Generar bars_with_trades vacío (modo bar-by-bar)
            bars_with_trades = [
                (row.to_dict(), [])
                for _, row in ohlcv_df.iterrows()
            ]
        else:
            raise ValueError("Debe proporcionar bars_with_trades o df")

        # Calcular indicadores sobre OHLCV completo
        ohlcv_df = Indicators.compute_all(ohlcv_df, {
            "ema_fast": sym_config.tf_ema_fast,
            "ema_slow": sym_config.tf_ema_slow,
            "zscore_lookback": sym_config.mr_lookback,
        })

        # Multi-timeframe: breakout levels en timeframe superior para Trend Following
        if "timestamp" in ohlcv_df.columns and len(ohlcv_df) > 100:
            try:
                ts_unit = "s" if ohlcv_df["timestamp"].max() < 1e12 else "ms"
                ohlcv_df_ts = pd.to_datetime(ohlcv_df["timestamp"], unit=ts_unit)
                ohlcv_indexed = ohlcv_df.set_index(ohlcv_df_ts)

                # Probar 15m y 1h
                for tf_label, tf_rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h")]:
                    resampled = ohlcv_indexed.resample(tf_rule).agg({
                        "open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum",
                    }).dropna()
                    resampled[f"high_20_{tf_label}"] = resampled["high"].rolling(20, min_periods=1).max()
                    resampled[f"low_20_{tf_label}"] = resampled["low"].rolling(20, min_periods=1).min()
                    resampled[f"adx_{tf_label}"] = Indicators.adx(
                        resampled["high"], resampled["low"], resampled["close"], 14
                    )
                    resampled[f"rsi_{tf_label}"] = Indicators.rsi(resampled["close"], 14)
                    bb_u, bb_m, bb_l = Indicators.bollinger_bands(resampled["close"])
                    resampled[f"bb_upper_{tf_label}"] = bb_u
                    resampled[f"bb_lower_{tf_label}"] = bb_l
                    for col in [f"high_20_{tf_label}", f"low_20_{tf_label}", f"adx_{tf_label}",
                                f"rsi_{tf_label}", f"bb_upper_{tf_label}", f"bb_lower_{tf_label}"]:
                        mapped = resampled[col].reindex(ohlcv_df_ts, method="ffill")
                        ohlcv_df[col] = mapped.values
            except Exception:
                for tf in ["5m", "15m", "1h"]:
                    ohlcv_df[f"high_20_{tf}"] = ohlcv_df["high_20"]
                    ohlcv_df[f"low_20_{tf}"] = ohlcv_df["low_20"]
                    ohlcv_df[f"adx_{tf}"] = ohlcv_df["adx"]
        else:
            for tf in ["5m", "15m", "1h"]:
                ohlcv_df[f"high_20_{tf}"] = ohlcv_df["high_20"]
                ohlcv_df[f"low_20_{tf}"] = ohlcv_df["low_20"]
                ohlcv_df[f"adx_{tf}"] = ohlcv_df["adx"]

        start_idx = max(sym_config.mr_lookback, sym_config.tf_ema_slow * 3, 100)

        # ── Preparar orderbook real si disponible ──────────────────
        ob_timestamps = None
        ob_data = None
        if orderbook_df is not None and not orderbook_df.empty:
            ob_data = orderbook_df.sort_values("timestamp")
            ob_timestamps = ob_data["timestamp"].values

        # ── Loop principal (replica _process_symbol) ──────────────

        for i in range(start_idx, len(ohlcv_df)):
            bar_dict, bar_trades = bars_with_trades[i]
            bar = ohlcv_df.iloc[i]
            price = float(bar["close"])
            high = float(bar["high"])
            low = float(bar["low"])
            ts = float(bar.get("timestamp", i * 60))

            # ── 0. Alimentar microestructura tick-by-tick ─────────
            if bar_trades:
                for tick in bar_trades:
                    micro_engine.on_trade(
                        symbol,
                        float(tick["price"]),
                        float(tick["quantity"]),
                        float(tick["timestamp"]),
                    )
            else:
                # Fallback bar-by-bar
                micro_engine.on_bar(
                    symbol, float(bar["open"]), high, low, price,
                    float(bar.get("volume", 0)), ts,
                )

            # ── 1. Verificar liquidaciones ────────────────────────
            for key in list(positions.keys()):
                pos = positions[key]
                if pos.is_liquidated(price):
                    liq_pnl = -(pos.entry_price * pos.size) / pos.leverage
                    equity += liq_pnl
                    risk_manager.update_equity(equity)
                    risk_manager.record_trade_result(liq_pnl)
                    portfolio_manager.update_strategy_pnl(pos.strategy, liq_pnl)
                    trade_rec = pos.trade_dict(i, symbol, "LIQUIDATION", price, liq_pnl, ts)
                    result.trades.append(trade_rec)
                    log_jsonl({"type": "trade", **trade_rec})
                    risk_manager.update_position(symbol, None)
                    del positions[key]

            # ── 1b. Check SL/TP on existing positions ─────────────
            for key in list(positions.keys()):
                pos = positions[key]
                if pos.stop_loss <= 0 and pos.take_profit <= 0:
                    continue
                fee = trading_config.taker_fee
                hit = False
                exit_price_sltp = 0.0
                exit_side_sltp = ""
                if pos.side == Side.BUY:
                    if pos.stop_loss > 0 and low <= pos.stop_loss:
                        exit_price_sltp = pos.stop_loss
                        exit_side_sltp = "SL_LONG"
                        hit = True
                    elif pos.take_profit > 0 and high >= pos.take_profit:
                        exit_price_sltp = pos.take_profit
                        exit_side_sltp = "TP_LONG"
                        hit = True
                else:
                    if pos.stop_loss > 0 and high >= pos.stop_loss:
                        exit_price_sltp = pos.stop_loss
                        exit_side_sltp = "SL_SHORT"
                        hit = True
                    elif pos.take_profit > 0 and low <= pos.take_profit:
                        exit_price_sltp = pos.take_profit
                        exit_side_sltp = "TP_SHORT"
                        hit = True
                if hit:
                    pnl = pos.close(exit_price_sltp, fee)
                    equity += pnl
                    risk_manager.update_equity(equity)
                    risk_manager.record_trade_result(pnl)
                    risk_manager.update_position(symbol, None)
                    portfolio_manager.update_strategy_pnl(pos.strategy, pnl)
                    trade_rec = pos.trade_dict(i, symbol, exit_side_sltp, exit_price_sltp, pnl, ts)
                    result.trades.append(trade_rec)
                    log_jsonl({"type": "trade", **trade_rec})
                    del positions[key]

            # ── 2. Funding rate ───────────────────────────────────
            if i % funding_interval_bars == 0:
                for pos in positions.values():
                    cost = pos.size * price * funding_rate
                    if pos.side == Side.BUY:
                        equity -= cost
                    else:
                        equity += cost
                    risk_manager.update_equity(equity)

            # ── 3. Slice de indicadores + detectar régimen ────────
            df_slice = ohlcv_df.iloc[:i + 1]
            regime = regime_detector.detect(df_slice, symbol, sym_config)
            result.regime_history.append(regime.value)

            if regime != last_regime:
                log_jsonl({
                    "type": "regime_change", "timestamp": ts,
                    "symbol": symbol, "old_regime": last_regime.value,
                    "new_regime": regime.value,
                })
                last_regime = regime

            # ── 4. MarketSnapshot + orderbook (real o simulado) ───
            atr_val = float(bar.get("atr", price * 0.001))
            if pd.isna(atr_val) or atr_val <= 0:
                atr_val = price * 0.001

            # Usar orderbook real si disponible (lookup por timestamp mas cercano)
            ob_bid = price - atr_val * 0.01
            ob_ask = price + atr_val * 0.01
            ob_bid_depth = 1.0
            ob_ask_depth = 1.0
            if ob_timestamps is not None:
                idx = np.searchsorted(ob_timestamps, ts, side="right") - 1
                if 0 <= idx < len(ob_data):
                    ob_row = ob_data.iloc[idx]
                    ob_bid = float(ob_row["best_bid"])
                    ob_ask = float(ob_row["best_ask"])
                    if ob_bid > 0 and ob_ask > 0:
                        ob_bid_depth = float(ob_row.get("bid_depth", 1.0))
                        ob_ask_depth = float(ob_row.get("ask_depth", 1.0))
                    else:
                        ob_bid = price - atr_val * 0.01
                        ob_ask = price + atr_val * 0.01

            snapshot = MarketSnapshot(
                symbol=symbol, timestamp=ts, price=price,
                mark_price=price, index_price=price,
                funding_rate=funding_rate, volume_24h=0, open_interest=0,
                orderbook=OrderBook(
                    symbol=symbol, timestamp=ts,
                    bids=[OrderBookLevel(ob_bid, ob_bid_depth)],
                    asks=[OrderBookLevel(ob_ask, ob_ask_depth)],
                ),
                regime=regime,
            )

            # ── 5. Snapshot de microestructura ────────────────────
            micro_snap = micro_engine.get_snapshot(symbol)

            micro_record = {
                "type": "microstructure", "timestamp": ts, "symbol": symbol,
                "vpin": micro_snap.vpin.vpin,
                "vpin_toxic": micro_snap.vpin.is_toxic,
                "hawkes_intensity": micro_snap.hawkes.intensity,
                "hawkes_spike": micro_snap.hawkes.is_spike,
                "hawkes_ratio": micro_snap.hawkes.spike_ratio,
                "as_spread_bps": micro_snap.avellaneda_stoikov.spread_bps,
                "as_gamma_eff": micro_snap.avellaneda_stoikov.effective_gamma,
                "risk_score": micro_snap.risk_score,
            }
            result.microstructure_history.append(micro_record)
            log_jsonl(micro_record)

            # ── 6. Generar señales (replica _process_symbol) ──────
            all_signals: List[Signal] = []
            mm_signals: List[Signal] = []

            for strategy in active_strategies:
                if not strategy.should_activate(regime):
                    continue
                if not portfolio_manager.should_strategy_trade(
                    strategy.strategy_type, regime
                ):
                    continue

                allocated = portfolio_manager.get_allocation(
                    symbol, regime, strategy.strategy_type
                )

                pos_key = f"{symbol}_{strategy.strategy_type.value}"
                current_pos = None
                if pos_key in positions:
                    current_pos = positions[pos_key].to_position(price)
                    risk_manager.update_position(symbol, current_pos)

                obi_result = obi_calculator.compute(snapshot.orderbook)
                # Multi-timeframe data for strategies
                mtf_data = {}
                for tf in ["5m", "15m", "1h"]:
                    rsi_col = f"rsi_{tf}"
                    bbu_col = f"bb_upper_{tf}"
                    bbl_col = f"bb_lower_{tf}"
                    adx_col = f"adx_{tf}"
                    if rsi_col in bar.index:
                        mtf_data[tf] = {
                            "rsi": float(bar.get(rsi_col, 50)),
                            "bb_upper": float(bar.get(bbu_col, 0)),
                            "bb_lower": float(bar.get(bbl_col, 0)),
                            "adx": float(bar.get(adx_col, 0)),
                            "atr": float(bar.get("atr", 0)),
                        }
                signals = strategy.generate_signals(
                    symbol, df_slice, snapshot, regime, sym_config,
                    allocated, current_pos, micro=micro_snap, obi=obi_result,
                    mtf=mtf_data,
                )

                for sig in signals:
                    # Enriquecer metadata con features de micro/indicadores para ML
                    sig.metadata.setdefault("vpin", micro_snap.vpin.vpin if micro_snap else 0)
                    sig.metadata.setdefault("hawkes_ratio", micro_snap.hawkes.spike_ratio if micro_snap else 1)
                    sig.metadata.setdefault("risk_score", micro_snap.risk_score if micro_snap else 0)
                    sig.metadata.setdefault("obi_imbalance", obi_result.weighted_imbalance if obi_result else 0)
                    sig.metadata.setdefault("obi_delta", obi_result.delta if obi_result else 0)
                    sig.metadata.setdefault("rsi", float(bar.get("rsi", 50)))
                    sig.metadata.setdefault("zscore", float(bar.get("zscore", 0)))
                    sig.metadata.setdefault("atr_pct", float(atr_val / price * 100) if price > 0 else 0)
                    sig.metadata.setdefault("momentum", float(bar.get("momentum_20", 0)))
                    sig.metadata.setdefault("vol_ratio", float(bar.get("vol_ratio", 1)))
                    sig.metadata.setdefault("adx", float(bar.get("adx", 0)))
                    sig.metadata.setdefault("regime_num", {"RANGING": 0, "TRENDING_UP": 1, "TRENDING_DOWN": -1, "BREAKOUT": 2, "UNKNOWN": 0}.get(regime.value, 0))
                    sig.metadata.setdefault("strength", sig.strength)
                    bb_u = float(bar.get("bb_upper", 0))
                    bb_l = float(bar.get("bb_lower", 0))
                    if atr_val > 0 and bb_u > 0:
                        sig.metadata.setdefault("bb_distance", min((bb_u - price) / atr_val, (price - bb_l) / atr_val))
                    else:
                        sig.metadata.setdefault("bb_distance", 0)

                    result.signals_generated += 1
                    log_jsonl({
                        "type": "signal", "timestamp": ts, "symbol": symbol,
                        "strategy": sig.strategy.value, "side": sig.side.value,
                        "strength": round(sig.strength, 4),
                        "entry_price": round(sig.entry_price, 4),
                        "stop_loss": round(sig.stop_loss, 4),
                        "take_profit": round(sig.take_profit, 4),
                        "size_usd": round(sig.size_usd, 2),
                        "metadata": sig.metadata,
                    })

                    if sig.strategy == StrategyType.MARKET_MAKING:
                        mm_signals.append(sig)
                    else:
                        all_signals.append(sig)

            # Guardar allocation snapshot
            alloc_rec = {
                "type": "allocation", "timestamp": ts, "symbol": symbol,
                "regime": regime.value, "equity": round(equity, 2),
            }
            for st in StrategyType:
                alloc_rec[st.value] = round(
                    portfolio_manager.get_allocation(symbol, regime, st), 2
                )
            result.allocation_history.append(alloc_rec)
            if i % 50 == 0:  # loguear allocation cada 50 barras
                log_jsonl(alloc_rec)

            # ── 7. Validar con RiskManager ────────────────────────
            validated: List[Signal] = []
            for sig in all_signals:
                valid = risk_manager.validate_signal(
                    sig, sym_config, regime, micro=micro_snap,
                    funding_rate=funding_rate,
                )
                if valid:
                    validated.append(valid)

            # MM signals: no pasan por risk_manager individual
            # (el motor A-S ya incorpora los filtros de micro)
            validated.extend(mm_signals)

            # ── 7b. Filtro ML (si disponible) ────────────────────
            if ml_filter is not None and ml_filter.is_trained:
                ml_validated = []
                for sig in validated:
                    # No filtrar exits ni MM
                    is_exit = sig.metadata.get("action") in (
                        "exit_mean_reversion", "trailing_stop_hit", "mm_unwind"
                    )
                    if is_exit or sig.strategy == StrategyType.MARKET_MAKING:
                        ml_validated.append(sig)
                    elif ml_filter.should_pass(sig.metadata):
                        ml_validated.append(sig)
                validated = ml_validated

            # ── 8. Ejecutar señales simuladas ─────────────────────
            for signal in validated:
                pos_key = f"{symbol}_{signal.strategy.value}"

                # Señal de salida
                is_exit = signal.metadata.get("action") in (
                    "exit_mean_reversion", "trailing_stop_hit", "mm_unwind"
                )
                if is_exit:
                    if pos_key in positions:
                        pos = positions[pos_key]
                        fee = trading_config.taker_fee
                        # Apply slippage to exit price (adverse direction)
                        exit_slip = compute_slippage(
                            base_bps=trading_config.slippage_bps, price=price,
                            size_usd=pos.size * price, regime=regime.value,
                        )
                        exit_price = price - exit_slip if pos.side == Side.BUY else price + exit_slip
                        pnl = pos.close(exit_price, fee)
                        equity += pnl
                        risk_manager.update_equity(equity)
                        risk_manager.record_trade_result(pnl)
                        risk_manager.update_position(symbol, None)
                        portfolio_manager.update_strategy_pnl(signal.strategy, pnl)
                        trade_rec = pos.trade_dict(i, symbol, "CLOSE_" + pos.side.value, exit_price, pnl, ts)
                        result.trades.append(trade_rec)
                        log_jsonl({"type": "trade", **trade_rec})
                        result.signals_executed += 1
                        del positions[pos_key]
                    continue

                # Señal de entrada
                if pos_key not in positions:
                    size = signal.size_usd / price if price > 0 else 0
                    if size <= 0:
                        continue

                    # Slippage dinamico
                    slippage_applied = compute_slippage(
                        base_bps=trading_config.slippage_bps, price=price,
                        size_usd=signal.size_usd, regime=regime.value,
                        hawkes_ratio=micro_snap.hawkes.spike_ratio if micro_snap else 1.0,
                        atr=float(bar.get("atr", 0)) if not pd.isna(bar.get("atr", 0)) else 0,
                    )
                    fill_price = (price + slippage_applied) if signal.side == Side.BUY else (price - slippage_applied)

                    # Determinar fee (MM = maker, rest = taker)
                    fee = (trading_config.maker_fee
                           if signal.strategy == StrategyType.MARKET_MAKING
                           else trading_config.taker_fee)

                    positions[pos_key] = BacktestPosition(
                        symbol=symbol, side=signal.side, size=size,
                        entry_price=fill_price, strategy=signal.strategy,
                        leverage=sym_config.leverage,
                        entry_timestamp=ts,
                        slippage_bps=trading_config.slippage_bps,
                        entry_metadata=signal.metadata.copy(),
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                    )
                    result.signals_executed += 1
                    # SL/TP checked on NEXT bar (no intra-bar look-ahead bias)

            # ── 9. Actualizar equity y portfolio snapshot ──────────
            unrealized = sum(p.update_pnl(price) for p in positions.values())
            current_equity = equity + unrealized
            result.equity_curve.append(current_equity)
            risk_manager.update_equity(current_equity)

            if i % 50 == 0:
                port_snap = portfolio_manager.get_portfolio_summary()
                result.portfolio_history.append(port_snap)
                log_jsonl({"type": "portfolio_snapshot", "timestamp": ts, **port_snap})

            # ── Callback visual ──────────────────────────────────
            if on_bar_callback is not None:
                on_bar_callback(
                    bar_index=i,
                    total_bars=len(ohlcv_df),
                    timestamp=ts,
                    price=price,
                    equity=current_equity,
                    initial_capital=trading_config.initial_capital,
                    positions=positions,
                    result=result,
                    regime=regime,
                    micro_snap=micro_snap,
                )

        # ── Cerrar posiciones al final ────────────────────────────
        if ohlcv_df.empty:
            close_jsonl()
            return result

        final_price = float(ohlcv_df.iloc[-1]["close"])
        final_ts = float(ohlcv_df.iloc[-1].get("timestamp", 0))
        for pos_key, pos in list(positions.items()):
            pnl = pos.close(final_price, trading_config.taker_fee)
            equity += pnl
            risk_manager.record_trade_result(pnl)
            portfolio_manager.update_strategy_pnl(pos.strategy, pnl)
            trade_rec = pos.trade_dict(len(ohlcv_df) - 1, symbol, "CLOSE_EOD", final_price, pnl, final_ts)
            result.trades.append(trade_rec)
            log_jsonl({"type": "trade", **trade_rec})

        # Final portfolio snapshot
        log_jsonl({
            "type": "portfolio_snapshot", "timestamp": final_ts,
            **portfolio_manager.get_portfolio_summary(),
        })

        close_jsonl()
        return result
