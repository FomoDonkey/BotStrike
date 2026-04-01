"""
Logging & Metrics — Registro de decisiones, trades, PnL y métricas.
Guarda todo en archivos estructurados para análisis y backtesting.
"""
from __future__ import annotations
import json
import os
import time
from typing import Any, Dict, List, Optional

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
        """Escribe una línea JSON al archivo de métricas."""
        try:
            with open(self.metrics_file, "a") as f:
                f.write(json.dumps(data, default=str) + "\n")
        except Exception as e:
            logger.error("metric_write_error", error=str(e))


class MetricsCollector:
    """Recopila y calcula métricas de rendimiento."""

    def __init__(self) -> None:
        self._trades: List[Trade] = []
        self._equity_curve: List[float] = []
        self._start_time: float = time.time()
        # Running totals que sobreviven a truncation del historial
        self._cumulative_pnl: float = 0.0
        self._cumulative_fees: float = 0.0
        self._cumulative_trade_count: int = 0
        self._cumulative_win_count: int = 0
        self._cumulative_loss_count: int = 0
        self._cumulative_win_pnl: float = 0.0
        self._cumulative_loss_pnl: float = 0.0

    def add_trade(self, trade: Trade) -> None:
        self._trades.append(trade)
        # Actualizar running totals ANTES de truncar
        self._cumulative_pnl += trade.pnl
        self._cumulative_fees += trade.fee
        self._cumulative_trade_count += 1
        if trade.pnl > 0:
            self._cumulative_win_count += 1
            self._cumulative_win_pnl += trade.pnl
        elif trade.pnl < 0:
            self._cumulative_loss_count += 1
            self._cumulative_loss_pnl += trade.pnl
        # Limitar historial en memoria
        if len(self._trades) > 5000:
            self._trades = self._trades[-2500:]

    def update_equity(self, equity: float) -> None:
        self._equity_curve.append(equity)
        # Limitar historial en memoria
        if len(self._equity_curve) > 50000:
            self._equity_curve = self._equity_curve[-25000:]

    def get_metrics(self) -> Dict[str, Any]:
        """Calcula métricas completas de rendimiento."""
        if not self._trades:
            return {"total_trades": 0}

        pnls = [t.pnl for t in self._trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        fees = sum(t.fee for t in self._trades)

        total_pnl = sum(pnls)
        win_rate = len(wins) / len(pnls) if pnls else 0
        # Usar acumuladores para avg_win/avg_loss (consistente con contadores cumulativos)
        avg_win = self._cumulative_win_pnl / self._cumulative_win_count if self._cumulative_win_count > 0 else 0
        avg_loss = self._cumulative_loss_pnl / self._cumulative_loss_count if self._cumulative_loss_count > 0 else 0
        profit_factor = abs(self._cumulative_win_pnl / self._cumulative_loss_pnl) if self._cumulative_loss_pnl != 0 else 9999.99

        # Sharpe ratio — agregar a retornos diarios para annualizacion correcta
        import numpy as np
        from collections import defaultdict
        sharpe = 0
        if len(self._trades) > 1:
            daily_pnl: dict = defaultdict(float)
            for t in self._trades:
                day = int(t.timestamp // 86400)
                daily_pnl[day] += t.pnl
            if len(daily_pnl) > 1:
                daily_arr = np.array(list(daily_pnl.values()))
                if np.std(daily_arr) > 0:
                    sharpe = float(np.mean(daily_arr) / np.std(daily_arr) * (252 ** 0.5))

        # Max drawdown de equity curve
        max_dd = 0.0
        if self._equity_curve:
            peak = self._equity_curve[0]
            for eq in self._equity_curve:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

        # Métricas por estrategia
        by_strategy: Dict[str, Dict] = {}
        for st in StrategyType:
            st_trades = [t for t in self._trades if t.strategy == st]
            if st_trades:
                st_pnls = [t.pnl for t in st_trades]
                st_wins = [p for p in st_pnls if p > 0]
                by_strategy[st.value] = {
                    "trades": len(st_trades),
                    "pnl": sum(st_pnls),
                    "win_rate": len(st_wins) / len(st_pnls),
                    "avg_pnl": sum(st_pnls) / len(st_pnls),
                }

        return {
            "total_trades": self._cumulative_trade_count,
            "total_pnl": round(self._cumulative_pnl, 2),
            "total_fees": round(self._cumulative_fees, 2),
            "net_pnl": round(self._cumulative_pnl, 2),  # pnl ya incluye fees
            "win_rate": round(self._cumulative_win_count / self._cumulative_trade_count if self._cumulative_trade_count > 0 else 0, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 4),
            "by_strategy": by_strategy,
            "runtime_hours": round((time.time() - self._start_time) / 3600, 2),
        }
