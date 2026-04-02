"""
Logging & Metrics — Registro de decisiones, trades, PnL y métricas.
Guarda todo en archivos estructurados para análisis y backtesting.
"""
from __future__ import annotations
import json
import os
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

import numpy as np
import structlog
from core.types import Signal, Trade, MarketRegime, StrategyType

logger = structlog.get_logger(__name__)


class TradingLogger:
    """Logger estructurado para decisiones de trading."""

    def __init__(self, log_file: str, metrics_file: str) -> None:
        self.log_file = log_file
        self.metrics_file = metrics_file

        # Crear directorio de logs
        os.makedirs(os.path.dirname(log_file) or "logs", exist_ok=True)

        # Buffer para escritura en batch (reduce I/O de disco)
        self._metric_buffer: List[str] = []
        self._metric_flush_size = 10  # flush cada 10 métricas

        # Configurar structlog para escribir a stderr (no contaminar stdout)
        import sys
        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.add_log_level,
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.BoundLogger,
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        )

    def log_signal(self, signal: Signal) -> None:
        """Registra una señal de trading generada."""
        self._append_metric({
            "type": "signal",
            "timestamp": signal.timestamp,
            "strategy": signal.strategy.value,
            "symbol": signal.symbol,
            "side": signal.side.value,
            "strength": signal.strength,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "size_usd": signal.size_usd,
            "metadata": signal.metadata,
        })

    def log_trade(self, trade: Trade) -> None:
        """Registra un trade ejecutado."""
        self._append_metric({
            "type": "trade",
            "timestamp": trade.timestamp,
            "symbol": trade.symbol,
            "side": trade.side.value,
            "price": trade.price,
            "quantity": trade.quantity,
            "fee": trade.fee,
            "pnl": trade.pnl,
            "strategy": trade.strategy.value if trade.strategy else None,
            "order_id": trade.order_id,
        })

    def log_regime_change(
        self, symbol: str, old_regime: MarketRegime, new_regime: MarketRegime
    ) -> None:
        """Registra cambio de régimen de mercado."""
        self._append_metric({
            "type": "regime_change",
            "timestamp": time.time(),
            "symbol": symbol,
            "old_regime": old_regime.value,
            "new_regime": new_regime.value,
        })

    def log_risk_event(self, event: str, details: Dict) -> None:
        """Registra evento de riesgo."""
        self._append_metric({
            "type": "risk_event",
            "timestamp": time.time(),
            "event": event,
            **details,
        })

    def log_portfolio_snapshot(self, snapshot: Dict) -> None:
        """Registra estado del portfolio."""
        self._append_metric({
            "type": "portfolio_snapshot",
            "timestamp": time.time(),
            **snapshot,
        })

    def _append_metric(self, data: Dict) -> None:
        """Bufferiza métricas y escribe a disco en batch."""
        self._metric_buffer.append(json.dumps(data, default=str))
        if len(self._metric_buffer) >= self._metric_flush_size:
            self._flush_metrics()

    def _flush_metrics(self) -> None:
        """Escribe buffer de métricas a disco de una sola vez."""
        if not self._metric_buffer:
            return
        try:
            with open(self.metrics_file, "a") as f:
                f.write("\n".join(self._metric_buffer) + "\n")
            self._metric_buffer.clear()
        except Exception as e:
            logger.error("metric_write_error", error=str(e))


class MetricsCollector:
    """Recopila y calcula métricas de rendimiento.

    Optimizado: usa deque (sin reasignación), acumuladores incrementales
    para estrategias/Sharpe/drawdown, y evita recalcular en cada query.
    """

    def __init__(self) -> None:
        self._trades: deque = deque(maxlen=2500)
        self._equity_curve: deque = deque(maxlen=25000)
        self._start_time: float = time.time()
        # Running totals — sobreviven a truncation
        self._cumulative_pnl: float = 0.0
        self._cumulative_fees: float = 0.0
        self._cumulative_trade_count: int = 0
        self._cumulative_win_count: int = 0
        self._cumulative_loss_count: int = 0
        self._cumulative_win_pnl: float = 0.0
        self._cumulative_loss_pnl: float = 0.0
        # Incremental strategy tracking — evita O(n*m) en get_metrics
        self._by_strategy: Dict[str, Dict] = {}
        # Incremental daily PnL for Sharpe — evita recorrer trades
        self._daily_pnl: Dict[int, float] = {}
        # Running max drawdown — evita recorrer equity curve
        self._equity_peak: float = 0.0
        self._max_drawdown: float = 0.0

    def add_trade(self, trade: Trade) -> None:
        self._trades.append(trade)  # deque auto-evicts oldest
        # Running totals
        self._cumulative_pnl += trade.pnl
        self._cumulative_fees += trade.fee
        self._cumulative_trade_count += 1
        if trade.pnl > 0:
            self._cumulative_win_count += 1
            self._cumulative_win_pnl += trade.pnl
        elif trade.pnl < 0:
            self._cumulative_loss_count += 1
            self._cumulative_loss_pnl += trade.pnl
        # Incremental strategy bucket
        st_key = trade.strategy.value if trade.strategy else "UNKNOWN"
        bucket = self._by_strategy.get(st_key)
        if bucket is None:
            bucket = {"trades": 0, "pnl": 0.0, "wins": 0}
            self._by_strategy[st_key] = bucket
        bucket["trades"] += 1
        bucket["pnl"] += trade.pnl
        if trade.pnl > 0:
            bucket["wins"] += 1
        # Incremental daily PnL for Sharpe
        day = int(trade.timestamp // 86400)
        self._daily_pnl[day] = self._daily_pnl.get(day, 0.0) + trade.pnl

    def update_equity(self, equity: float) -> None:
        self._equity_curve.append(equity)  # deque auto-evicts oldest
        # Running max drawdown — O(1) per update
        if equity > self._equity_peak:
            self._equity_peak = equity
        if self._equity_peak > 0:
            dd = (self._equity_peak - equity) / self._equity_peak
            if dd > self._max_drawdown:
                self._max_drawdown = dd

    def get_metrics(self) -> Dict[str, Any]:
        """Retorna métricas usando acumuladores incrementales (O(1) para la mayoría)."""
        tc = self._cumulative_trade_count
        if tc == 0:
            return {"total_trades": 0}

        avg_win = self._cumulative_win_pnl / self._cumulative_win_count if self._cumulative_win_count > 0 else 0
        avg_loss = self._cumulative_loss_pnl / self._cumulative_loss_count if self._cumulative_loss_count > 0 else 0
        profit_factor = abs(self._cumulative_win_pnl / self._cumulative_loss_pnl) if self._cumulative_loss_pnl != 0 else 9999.99

        # Sharpe from incremental daily_pnl dict
        sharpe = 0.0
        if len(self._daily_pnl) > 1:
            daily_arr = np.array(list(self._daily_pnl.values()))
            std = float(np.std(daily_arr))
            if std > 0:
                sharpe = float(np.mean(daily_arr) / std * (252 ** 0.5))

        # Strategy breakdown from incremental buckets
        by_strategy: Dict[str, Dict] = {}
        for st_key, b in self._by_strategy.items():
            if b["trades"] > 0:
                by_strategy[st_key] = {
                    "trades": b["trades"],
                    "pnl": round(b["pnl"], 4),
                    "win_rate": b["wins"] / b["trades"],
                    "avg_pnl": b["pnl"] / b["trades"],
                }

        return {
            "total_trades": tc,
            "total_pnl": round(self._cumulative_pnl, 2),
            "total_fees": round(self._cumulative_fees, 2),
            "net_pnl": round(self._cumulative_pnl, 2),
            "win_rate": round(self._cumulative_win_count / tc, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(self._max_drawdown, 4),
            "by_strategy": by_strategy,
            "runtime_hours": round((time.time() - self._start_time) / 3600, 2),
        }
