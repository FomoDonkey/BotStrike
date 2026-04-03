"""
PerformanceAnalyzer — Módulo de análisis de rendimiento de estrategias.

Evalúa rendimiento a través de múltiples dimensiones:
  - Por estrategia (MR, TF, MM)
  - Por símbolo (BTC-USD, ETH-USD, ADA-USD)
  - Por régimen de mercado (RANGING, TRENDING_UP, etc.)
  - Por periodo temporal (diario, semanal, mensual)
  - Portfolio completo

Trabaja con:
  - TradeRepository (datos persistentes)
  - BacktestResult (datos en memoria)
  - Lista de TradeRecord directamente

Diseño: stateless — recibe datos, retorna PerformanceReport.
No mantiene estado interno, puede usarse como servicio compartido.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import time

import numpy as np

from trade_database.models import TradeRecord
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class PerformanceReport:
    """Resultado estructurado de análisis de rendimiento.

    Contiene todas las métricas calculadas para un conjunto de trades.
    Puede representar un segmento (por estrategia, régimen, etc.) o el total.
    """
    # Identificación del segmento
    label: str = ""           # e.g., "MEAN_REVERSION", "BTC-USD", "RANGING"
    dimension: str = ""       # e.g., "strategy", "symbol", "regime", "total"

    # Conteos
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0

    # PnL
    total_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    median_pnl: float = 0.0

    # Ratios
    win_rate: float = 0.0
    profit_factor: float = 0.0
    payoff_ratio: float = 0.0     # avg_win / abs(avg_loss)
    expectancy: float = 0.0       # win_rate * avg_win + (1-win_rate) * avg_loss

    # Riesgo
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: float = 0.0  # en segundos
    volatility: float = 0.0       # std dev de PnL
    var_95: float = 0.0           # Value at Risk 95%
    cvar_95: float = 0.0          # Conditional VaR 95%

    # Equity
    initial_equity: float = 0.0
    final_equity: float = 0.0
    return_pct: float = 0.0
    equity_curve: List[float] = field(default_factory=list)

    # Temporal
    avg_trade_duration_sec: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    trades_per_day: float = 0.0

    # Exposición
    avg_exposure: float = 0.0     # promedio de notional

    # Distribuciones (listas para histogramas — excluidas de to_dict por compacidad)
    drawdown_events: List[float] = field(default_factory=list)       # cada drawdown peak-to-trough
    duration_distribution: List[float] = field(default_factory=list) # duration_sec por trade
    slippage_distribution: List[float] = field(default_factory=list) # slippage_bps por trade
    fee_distribution: List[float] = field(default_factory=list)      # fee por trade

    _LIST_FIELDS = {"equity_curve", "drawdown_events", "duration_distribution",
                     "slippage_distribution", "fee_distribution"}

    def to_dict(self) -> dict:
        """Convierte a diccionario (excluye listas grandes para compacidad)."""
        d = {}
        for k, v in self.__dict__.items():
            if k in self._LIST_FIELDS:
                d[f"{k}_length"] = len(v)
            elif isinstance(v, float):
                d[k] = round(v, 4)
            else:
                d[k] = v
        return d

    def summary_str(self) -> str:
        """Resumen legible de una línea."""
        return (
            f"{self.label}: {self.total_trades} trades, "
            f"PnL=${self.net_pnl:,.2f}, WR={self.win_rate:.1%}, "
            f"PF={self.profit_factor:.2f}, Sharpe={self.sharpe_ratio:.2f}, "
            f"MaxDD={self.max_drawdown:.2%}"
        )


class PerformanceAnalyzer:
    """Analizador de rendimiento multi-dimensional.

    Uso directo:
        analyzer = PerformanceAnalyzer()
        report = analyzer.analyze(trades, initial_equity=100000)

    Uso con TradeRepository:
        analyzer = PerformanceAnalyzer()
        trades = repo.get_trades(session_id="abc123")
        report = analyzer.analyze(trades, initial_equity=100000)

    Análisis por dimensión:
        by_strategy = analyzer.analyze_by_strategy(trades, initial_equity=100000)
        by_regime = analyzer.analyze_by_regime(trades, initial_equity=100000)

    Uso con BacktestResult:
        report = analyzer.from_backtest_result(result)
    """

    ANNUALIZATION_FACTOR = 252  # trading days per year

    def analyze(
        self,
        trades: List[TradeRecord],
        initial_equity: float = 100_000.0,
        label: str = "total",
        dimension: str = "total",
    ) -> PerformanceReport:
        """Analiza una lista de trades y genera reporte completo.

        Args:
            trades: Lista de TradeRecord ordenados por timestamp
            initial_equity: Equity inicial para cálculos de retorno
            label: Etiqueta del segmento
            dimension: Tipo de segmento (strategy, symbol, regime, total)

        Returns:
            PerformanceReport con todas las métricas
        """
        report = PerformanceReport(label=label, dimension=dimension)

        if not trades:
            return report

        pnls = [t.pnl for t in trades]
        fees = [t.fee for t in trades]
        pnl_arr = np.array(pnls)

        # ── Conteos ──────────────────────────────────────────────────
        report.total_trades = len(trades)
        report.wins = sum(1 for p in pnls if p > 0)
        report.losses = sum(1 for p in pnls if p < 0)
        report.breakeven = report.total_trades - report.wins - report.losses

        # ── PnL ──────────────────────────────────────────────────────
        report.total_pnl = float(np.sum(pnl_arr))
        report.total_fees = sum(fees)
        report.net_pnl = report.total_pnl  # pnl already includes fees in backtester
        report.gross_profit = float(np.sum(pnl_arr[pnl_arr > 0])) if report.wins > 0 else 0.0
        report.gross_loss = float(np.sum(pnl_arr[pnl_arr < 0])) if report.losses > 0 else 0.0
        report.avg_pnl = float(np.mean(pnl_arr))
        report.median_pnl = float(np.median(pnl_arr))
        report.best_trade = float(np.max(pnl_arr))
        report.worst_trade = float(np.min(pnl_arr))

        win_pnls = pnl_arr[pnl_arr > 0]
        loss_pnls = pnl_arr[pnl_arr < 0]
        report.avg_win = float(np.mean(win_pnls)) if len(win_pnls) > 0 else 0.0
        report.avg_loss = float(np.mean(loss_pnls)) if len(loss_pnls) > 0 else 0.0

        # ── Ratios ───────────────────────────────────────────────────
        report.win_rate = report.wins / report.total_trades if report.total_trades > 0 else 0.0
        report.profit_factor = (
            abs(report.gross_profit / report.gross_loss)
            if report.gross_loss != 0 else 9999.99
        )
        report.payoff_ratio = (
            abs(report.avg_win / report.avg_loss)
            if report.avg_loss != 0 else 9999.99
        )
        report.expectancy = (
            report.win_rate * report.avg_win
            + (1 - report.win_rate) * report.avg_loss
        )

        # ── Equity curve y drawdown ──────────────────────────────────
        report.initial_equity = initial_equity
        equity_curve = self._build_equity_curve(trades, initial_equity)
        report.equity_curve = equity_curve
        report.final_equity = equity_curve[-1] if equity_curve else initial_equity

        if initial_equity > 0:
            report.return_pct = (report.final_equity - initial_equity) / initial_equity * 100

        max_dd, max_dd_duration = self._compute_drawdown(equity_curve, trades)
        report.max_drawdown = max_dd
        report.max_drawdown_duration = max_dd_duration

        # ── Métricas de riesgo ───────────────────────────────────────
        report.volatility = float(np.std(pnl_arr)) if len(pnl_arr) > 1 else 0.0

        # Sharpe y Sortino: agregar PnL a retornos DIARIOS para annualizacion correcta
        # (per-trade Sharpe infla el ratio para estrategias con multiples trades/dia)
        daily_returns = self._aggregate_daily_returns(trades, initial_equity)
        if len(daily_returns) > 1:
            daily_arr = np.array(daily_returns)
            daily_mean = np.mean(daily_arr)
            daily_std = np.std(daily_arr)
            if daily_std > 0:
                report.sharpe_ratio = float(
                    daily_mean / daily_std * np.sqrt(self.ANNUALIZATION_FACTOR)
                )

            # Sortino: solo penaliza downside volatility
            downside = daily_arr[daily_arr < 0]
            if len(downside) > 1:
                downside_std = float(np.std(downside))
                if downside_std > 0:
                    report.sortino_ratio = float(
                        daily_mean / downside_std * np.sqrt(self.ANNUALIZATION_FACTOR)
                    )
        else:
            # Fallback a per-trade si solo hay 1 dia de datos
            if report.volatility > 0 and len(pnl_arr) > 1:
                report.sharpe_ratio = float(
                    np.mean(pnl_arr) / np.std(pnl_arr) * np.sqrt(self.ANNUALIZATION_FACTOR)
                )

        # Calmar (annualized return / max drawdown)
        if max_dd > 0 and initial_equity > 0:
            total_return = report.total_pnl / initial_equity
            # Annualize: estimate trading days from timestamps
            if len(trades) >= 2:
                span_days = max((trades[-1].timestamp - trades[0].timestamp) / 86400.0, 1.0)
                annual_return = total_return * (365.0 / span_days)
            else:
                annual_return = total_return
            report.calmar_ratio = annual_return / max_dd

        # VaR y CVaR (95%)
        if len(pnl_arr) >= 20:
            sorted_pnls = np.sort(pnl_arr)
            var_idx = int(len(sorted_pnls) * 0.05)
            report.var_95 = float(sorted_pnls[var_idx])
            report.cvar_95 = float(np.mean(sorted_pnls[:var_idx + 1]))

        # ── Temporal ─────────────────────────────────────────────────
        durations = [t.duration_sec for t in trades if t.duration_sec > 0]
        report.avg_trade_duration_sec = float(np.mean(durations)) if durations else 0.0

        report.max_consecutive_wins = self._max_consecutive(pnls, positive=True)
        report.max_consecutive_losses = self._max_consecutive(pnls, positive=False)

        if len(trades) >= 2:
            time_span = trades[-1].timestamp - trades[0].timestamp
            if time_span > 0:
                report.trades_per_day = report.total_trades / (time_span / 86400)

        # ── Exposición ───────────────────────────────────────────────
        notionals = [t.notional for t in trades if t.notional > 0]
        report.avg_exposure = float(np.mean(notionals)) if notionals else 0.0

        # ── Distribuciones ──────────────────────────────────────────
        # Drawdown events: cada drawdown individual (peak-to-trough)
        report.drawdown_events = self._compute_drawdown_events(equity_curve)

        # Trade durations
        report.duration_distribution = [t.duration_sec for t in trades if t.duration_sec > 0]

        # Slippage per trade (from trade records that have it)
        slippages = [getattr(t, "slippage_bps", 0) for t in trades]
        report.slippage_distribution = [s for s in slippages if s > 0]

        # Fee per trade
        report.fee_distribution = [t.fee for t in trades if t.fee > 0]

        return report

    # ── Análisis por dimensión ───────────────────────────────────────

    def analyze_by_strategy(
        self,
        trades: List[TradeRecord],
        initial_equity: float = 100_000.0,
    ) -> Dict[str, PerformanceReport]:
        """Analiza rendimiento agrupado por estrategia."""
        return self._analyze_by_field(trades, "strategy", initial_equity)

    def analyze_by_symbol(
        self,
        trades: List[TradeRecord],
        initial_equity: float = 100_000.0,
    ) -> Dict[str, PerformanceReport]:
        """Analiza rendimiento agrupado por símbolo."""
        return self._analyze_by_field(trades, "symbol", initial_equity)

    def analyze_by_regime(
        self,
        trades: List[TradeRecord],
        initial_equity: float = 100_000.0,
    ) -> Dict[str, PerformanceReport]:
        """Analiza rendimiento agrupado por régimen de mercado."""
        return self._analyze_by_field(trades, "regime", initial_equity)

    def analyze_by_vpin_bucket(
        self,
        trades: List[TradeRecord],
        initial_equity: float = 100_000.0,
        n_buckets: int = 5,
    ) -> Dict[str, PerformanceReport]:
        """Analiza rendimiento agrupado por nivel de VPIN al momento del trade.

        Usa el campo micro_vpin del TradeRecord para agrupar trades en buckets.
        Permite ver si las estrategias rinden mejor o peor en flujo toxico.

        Args:
            n_buckets: Numero de buckets (default 5 → 0-0.2, 0.2-0.4, ...)
        """
        step = 1.0 / n_buckets
        groups: Dict[str, List[TradeRecord]] = {}

        for t in trades:
            vpin = getattr(t, "micro_vpin", 0.0) or 0.0
            bucket_idx = min(int(vpin / step), n_buckets - 1)
            low = round(bucket_idx * step, 2)
            high = round((bucket_idx + 1) * step, 2)
            label = f"VPIN {low:.1f}-{high:.1f}"
            groups.setdefault(label, []).append(t)

        result = {}
        for label, group_trades in sorted(groups.items()):
            result[label] = self.analyze(
                group_trades, initial_equity, label=label, dimension="vpin_bucket"
            )
        return result

    def analyze_by_period(
        self,
        trades: List[TradeRecord],
        period: str = "daily",
        initial_equity: float = 100_000.0,
    ) -> Dict[str, PerformanceReport]:
        """Analiza rendimiento por periodo temporal.

        Args:
            period: 'daily', 'weekly', 'monthly'
        """
        from datetime import datetime
        groups: Dict[str, List[TradeRecord]] = {}

        for t in trades:
            dt = datetime.fromtimestamp(t.timestamp)
            if period == "daily":
                key = dt.strftime("%Y-%m-%d")
            elif period == "weekly":
                key = f"{dt.year}-W{dt.isocalendar()[1]:02d}"
            elif period == "monthly":
                key = dt.strftime("%Y-%m")
            else:
                key = dt.strftime("%Y-%m-%d")
            groups.setdefault(key, []).append(t)

        result = {}
        for key, group_trades in sorted(groups.items()):
            result[key] = self.analyze(
                group_trades, initial_equity, label=key, dimension=period
            )
        return result

    def analyze_cross_strategy_regime(
        self,
        trades: List[TradeRecord],
        initial_equity: float = 100_000.0,
    ) -> Dict[str, Dict[str, PerformanceReport]]:
        """Análisis cruzado: rendimiento de cada estrategia en cada régimen.

        Returns:
            Dict[strategy][regime] = PerformanceReport
        """
        # Agrupar por (strategy, regime)
        groups: Dict[str, Dict[str, List[TradeRecord]]] = {}
        for t in trades:
            strat = t.strategy or "UNKNOWN"
            regime = t.regime or "UNKNOWN"
            groups.setdefault(strat, {}).setdefault(regime, []).append(t)

        result = {}
        for strat, regime_trades in groups.items():
            result[strat] = {}
            for regime, rt in regime_trades.items():
                result[strat][regime] = self.analyze(
                    rt, initial_equity,
                    label=f"{strat}/{regime}",
                    dimension="strategy_regime",
                )
        return result

    def compute_strategy_correlation(
        self,
        trades: List[TradeRecord],
    ) -> Dict[str, Dict[str, float]]:
        """Calcula correlación de PnL entre estrategias.

        Returns:
            Dict[strategy_a][strategy_b] = correlation coefficient
        """
        from datetime import datetime

        # Agrupar PnL diario por estrategia
        daily_pnl: Dict[str, Dict[str, float]] = {}
        for t in trades:
            strat = t.strategy or "UNKNOWN"
            day = datetime.fromtimestamp(t.timestamp).strftime("%Y-%m-%d")
            daily_pnl.setdefault(strat, {})
            daily_pnl[strat][day] = daily_pnl[strat].get(day, 0) + t.pnl

        strategies = sorted(daily_pnl.keys())
        if len(strategies) < 2:
            return {}

        # Alinear fechas
        all_days = sorted(set().union(*(d.keys() for d in daily_pnl.values())))

        # Construir matriz
        matrix = {}
        for s in strategies:
            matrix[s] = [daily_pnl[s].get(d, 0.0) for d in all_days]

        # Calcular correlaciones
        result = {}
        for i, s1 in enumerate(strategies):
            result[s1] = {}
            for j, s2 in enumerate(strategies):
                arr1 = np.array(matrix[s1])
                arr2 = np.array(matrix[s2])
                if np.std(arr1) > 0 and np.std(arr2) > 0:
                    corr = float(np.corrcoef(arr1, arr2)[0, 1])
                else:
                    corr = 0.0
                result[s1][s2] = round(corr, 4)

        return result

    def portfolio_analysis(
        self,
        trades: List[TradeRecord],
        initial_equity: float = 100_000.0,
    ) -> Dict:
        """Análisis completo a nivel portfolio.

        Returns:
            Dict con:
              - total: PerformanceReport del portfolio completo
              - by_strategy: Dict[str, PerformanceReport]
              - by_symbol: Dict[str, PerformanceReport]
              - by_regime: Dict[str, PerformanceReport]
              - correlations: Dict[str, Dict[str, float]]
              - cross_strategy_regime: Dict[str, Dict[str, PerformanceReport]]
        """
        return {
            "total": self.analyze(trades, initial_equity),
            "by_strategy": self.analyze_by_strategy(trades, initial_equity),
            "by_symbol": self.analyze_by_symbol(trades, initial_equity),
            "by_regime": self.analyze_by_regime(trades, initial_equity),
            "by_vpin": self.analyze_by_vpin_bucket(trades, initial_equity),
            "correlations": self.compute_strategy_correlation(trades),
            "cross_strategy_regime": self.analyze_cross_strategy_regime(
                trades, initial_equity
            ),
        }

    # ── Desde BacktestResult ─────────────────────────────────────────

    def from_backtest_result(
        self,
        result,  # BacktestResult
        initial_equity: float = 100_000.0,
        symbol: str = "",
    ) -> PerformanceReport:
        """Crea PerformanceReport directamente desde un BacktestResult.

        No requiere Trade Database — convierte trades dicts a TradeRecords en memoria.
        """
        regime_history = getattr(result, "regime_history", [])
        micro_history = getattr(result, "microstructure_history", [])

        # regime_history starts at start_idx (not bar 0), compute offset
        regime_offset = 0
        if result.trades and regime_history:
            max_bar = max(td.get("bar", 0) for td in result.trades)
            regime_offset = max(0, max_bar - len(regime_history) + 1)

        records = []
        equity = initial_equity
        for i, td in enumerate(result.trades):
            bar_idx = td.get("bar", i)
            adjusted_idx = bar_idx - regime_offset
            regime = regime_history[adjusted_idx] if 0 <= adjusted_idx < len(regime_history) else ""

            pnl = td.get("pnl", 0)
            eq_before = equity
            equity += pnl

            records.append(TradeRecord(
                symbol=td.get("symbol", symbol),
                side=td.get("side", ""),
                price=td.get("exit", td.get("entry", 0)),
                quantity=td.get("size", 0),
                pnl=pnl,
                strategy=td.get("strategy", ""),
                regime=regime,
                entry_price=td.get("entry", 0),
                exit_price=td.get("exit", 0),
                equity_before=eq_before,
                equity_after=equity,
                timestamp=td.get("timestamp", i * 60),
                source="backtest",
            ))

        report = self.analyze(records, initial_equity, label=symbol or "backtest")

        # Si el result tiene equity curve, usarla directamente (más precisa)
        if result.equity_curve:
            report.equity_curve = list(result.equity_curve)

        return report

    # ── Helpers internos ─────────────────────────────────────────────

    def _analyze_by_field(
        self,
        trades: List[TradeRecord],
        field_name: str,
        initial_equity: float,
    ) -> Dict[str, PerformanceReport]:
        """Agrupa trades por campo y analiza cada grupo."""
        groups: Dict[str, List[TradeRecord]] = {}
        for t in trades:
            key = getattr(t, field_name, "") or "UNKNOWN"
            groups.setdefault(key, []).append(t)

        result = {}
        for key, group_trades in groups.items():
            result[key] = self.analyze(
                group_trades, initial_equity, label=key, dimension=field_name
            )
        return result

    @staticmethod
    def _build_equity_curve(
        trades: List[TradeRecord],
        initial_equity: float,
    ) -> List[float]:
        """Reconstruye equity curve desde trades."""
        curve = [initial_equity]
        equity = initial_equity
        for t in trades:
            if t.equity_after > 0:
                equity = t.equity_after
            else:
                equity += t.pnl
            curve.append(equity)
        return curve

    @staticmethod
    def _aggregate_daily_returns(
        trades: List[TradeRecord], initial_equity: float
    ) -> List[float]:
        """Agrega PnL de trades a retornos diarios normalizados por equity.

        Agrupa trades por día (86400s) y calcula retorno diario = sum(pnl) / equity.
        Esto permite annualizar correctamente con sqrt(252).
        """
        if not trades:
            return []
        from collections import defaultdict
        daily_pnl: dict = defaultdict(float)
        for t in trades:
            day = int(t.timestamp // 86400)
            daily_pnl[day] += t.pnl

        if not daily_pnl:
            return []

        # Normalizar by rolling equity (not static initial — avoids bias on equity changes)
        equity = initial_equity if initial_equity > 0 else 100_000.0
        daily_returns = []
        for pnl in daily_pnl.values():
            daily_returns.append(pnl / equity if equity > 0 else 0.0)
            equity += pnl  # Rolling equity for next day's normalization
        return daily_returns

    @staticmethod
    def _compute_drawdown(
        equity_curve: List[float],
        trades: List[TradeRecord],
    ) -> Tuple[float, float]:
        """Calcula max drawdown y su duración.

        Returns:
            (max_drawdown_pct, max_dd_duration_seconds)
        """
        if not equity_curve:
            return 0.0, 0.0

        peak = equity_curve[0]
        max_dd = 0.0
        dd_start_idx = 0
        max_dd_duration = 0.0

        for i, eq in enumerate(equity_curve):
            if eq > peak:
                peak = eq
                dd_start_idx = i
            dd = (peak - eq) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                # Estimar duración si tenemos timestamps
                if trades and i > 0 and dd_start_idx < len(trades) and i - 1 < len(trades):
                    t_start = trades[min(dd_start_idx, len(trades) - 1)].timestamp
                    t_end = trades[min(i - 1, len(trades) - 1)].timestamp
                    max_dd_duration = t_end - t_start

        return max_dd, max_dd_duration

    @staticmethod
    def _compute_drawdown_events(equity_curve: List[float]) -> List[float]:
        """Extrae todos los drawdown events individuales (peak-to-trough pct)."""
        if len(equity_curve) < 3:
            return []
        events = []
        peak = equity_curve[0]
        current_dd = 0.0
        in_drawdown = False
        for eq in equity_curve:
            if eq >= peak:
                if in_drawdown and current_dd > 0.001:
                    events.append(current_dd)
                peak = eq
                current_dd = 0.0
                in_drawdown = False
            else:
                dd = (peak - eq) / peak if peak > 0 else 0
                if dd > current_dd:
                    current_dd = dd
                    in_drawdown = True
        if in_drawdown and current_dd > 0.001:
            events.append(current_dd)
        return events

    @staticmethod
    def _max_consecutive(pnls: List[float], positive: bool = True) -> int:
        """Máximo de resultados consecutivos (ganadores o perdedores)."""
        max_streak = 0
        current = 0
        for p in pnls:
            if (positive and p > 0) or (not positive and p < 0):
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak
