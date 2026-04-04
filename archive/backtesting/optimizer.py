"""
Walk-Forward Backtesting y Parameter Optimization.

WalkForwardBacktester:
  Divide datos en N folds de train+test. Para cada fold:
    1. Optimiza parametros en la ventana de training
    2. Evalua con los mejores parametros en la ventana de test (out-of-sample)
  Retorna metricas agregadas out-of-sample — la medida mas honesta de rendimiento.

ParameterOptimizer:
  Grid search sobre rangos configurables de parametros.
  Ejecuta backtests para cada combinacion.
  Retorna resultados rankeados por metrica objetivo (Sharpe, PnL, etc).

Uso:
    # Walk-forward
    wf = WalkForwardBacktester(settings)
    results = wf.run(df, "BTC-USD", n_folds=5, train_pct=0.7)

    # Grid search
    opt = ParameterOptimizer(settings)
    results = opt.optimize(df, "BTC-USD", param_grid={...}, metric="sharpe_ratio")
"""
from __future__ import annotations
import copy
import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config.settings import Settings
from backtesting.backtester import Backtester, BacktestResult
import structlog

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════
# WALK-FORWARD BACKTESTER
# ══════════════════════════════════════════════════════════════════

@dataclass
class WalkForwardFold:
    """Resultado de un fold individual del walk-forward."""
    fold_idx: int = 0
    train_bars: int = 0
    test_bars: int = 0
    train_start: int = 0
    train_end: int = 0
    test_start: int = 0
    test_end: int = 0
    # Metricas out-of-sample (test set)
    total_trades: int = 0
    net_pnl: float = 0.0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    # Parámetros optimizados en training
    best_params: Dict[str, Any] = field(default_factory=dict)
    # Resultado completo
    result: Optional[BacktestResult] = None


@dataclass
class WalkForwardResult:
    """Resultado agregado del walk-forward."""
    n_folds: int = 0
    train_pct: float = 0.0
    total_bars: int = 0
    folds: List[WalkForwardFold] = field(default_factory=list)
    # Metricas agregadas out-of-sample
    total_trades: int = 0
    total_pnl: float = 0.0
    avg_pnl_per_fold: float = 0.0
    avg_sharpe: float = 0.0
    avg_win_rate: float = 0.0
    avg_max_drawdown: float = 0.0
    consistency_ratio: float = 0.0  # % de folds rentables
    combined_equity_curve: List[float] = field(default_factory=list)

    def summary(self) -> Dict:
        return {
            "n_folds": self.n_folds,
            "total_trades": self.total_trades,
            "total_pnl": round(self.total_pnl, 2),
            "avg_pnl_per_fold": round(self.avg_pnl_per_fold, 2),
            "avg_sharpe": round(self.avg_sharpe, 2),
            "avg_win_rate": round(self.avg_win_rate, 4),
            "avg_max_drawdown": round(self.avg_max_drawdown, 4),
            "consistency_ratio": round(self.consistency_ratio, 2),
        }


class WalkForwardBacktester:
    """Walk-forward: entrena en ventana pasada, evalua en ventana futura.

    Divide el dataset en N folds secuenciales (no shuffled — respeta el tiempo).
    Cada fold tiene un segmento de training (in-sample) y test (out-of-sample).
    Las ventanas avanzan en el tiempo, nunca miran al futuro.

    Uso:
        wf = WalkForwardBacktester(settings)
        result = wf.run(df, "BTC-USD", n_folds=5, train_pct=0.7)
        print(result.summary())
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self,
        df: pd.DataFrame,
        symbol: str,
        n_folds: int = 5,
        train_pct: float = 0.7,
        strategies: Optional[List[str]] = None,
    ) -> WalkForwardResult:
        """Ejecuta walk-forward backtest.

        Args:
            df: DataFrame OHLCV completo
            symbol: Simbolo a testear
            n_folds: Numero de folds (default 5)
            train_pct: Fraccion de cada fold para training (default 70%)
            strategies: Lista de estrategias (None = todas)

        Returns:
            WalkForwardResult con metricas agregadas out-of-sample
        """
        total_bars = len(df)
        fold_size = total_bars // n_folds
        wf_result = WalkForwardResult(
            n_folds=n_folds, train_pct=train_pct, total_bars=total_bars
        )

        if fold_size < 200:
            logger.warning("walk_forward_too_few_bars",
                           total=total_bars, fold_size=fold_size)

        backtester = Backtester(self.settings)

        for fold_idx in range(n_folds):
            fold_start = fold_idx * fold_size
            fold_end = min(fold_start + fold_size, total_bars)
            if fold_idx == n_folds - 1:
                fold_end = total_bars

            train_size = int((fold_end - fold_start) * train_pct)
            train_end = fold_start + train_size
            test_start = train_end
            test_end = fold_end

            if test_end - test_start < 50:
                continue

            train_df = df.iloc[fold_start:train_end].reset_index(drop=True)
            test_df = df.iloc[test_start:test_end].reset_index(drop=True)
            if len(test_df) < 100 or len(train_df) < 100:
                continue

            # 1. Optimizar parámetros en training data (in-sample)
            optimizer = ParameterOptimizer(self.settings)
            # Grid reducido para velocidad en walk-forward
            wf_grid = {
                "mr_zscore_entry": [1.5, 2.0, 2.5],
                "mr_atr_mult_sl": [1.0, 1.5, 2.0],
                "tf_ema_fast": [8, 12],
                "tf_ema_slow": [21, 26],
                "tf_atr_mult_trail": [1.5, 2.0],
            }
            opt_result = optimizer.optimize(
                train_df, symbol, param_grid=wf_grid,
                strategies=strategies, metric="sharpe_ratio",
                max_combinations=100,
            )

            # 2. Aplicar mejores parámetros al test set (out-of-sample)
            if opt_result.best and opt_result.best.total_trades > 0:
                best_settings = optimizer._apply_params(opt_result.best.params, symbol)
                test_backtester = Backtester(best_settings)
            else:
                # Fallback a parámetros default si no hay resultado válido
                test_backtester = backtester

            result = test_backtester.run(test_df, symbol, strategies=strategies)
            summary = result.summary()

            fold = WalkForwardFold(
                fold_idx=fold_idx,
                train_bars=train_size,
                test_bars=test_end - test_start,
                train_start=fold_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                total_trades=summary.get("total_trades", 0),
                net_pnl=summary.get("net_pnl", 0),
                win_rate=summary.get("win_rate", 0),
                sharpe_ratio=summary.get("sharpe_ratio", 0),
                max_drawdown=summary.get("max_drawdown", 0),
                profit_factor=summary.get("profit_factor", 0),
                best_params=opt_result.best.params if opt_result.best else {},
                result=result,
            )
            wf_result.folds.append(fold)

        # Agregar metricas
        if wf_result.folds:
            folds = wf_result.folds
            wf_result.total_trades = sum(f.total_trades for f in folds)
            wf_result.total_pnl = sum(f.net_pnl for f in folds)
            wf_result.avg_pnl_per_fold = wf_result.total_pnl / len(folds)
            wf_result.avg_sharpe = np.mean([f.sharpe_ratio for f in folds])
            wf_result.avg_win_rate = np.mean([f.win_rate for f in folds])
            wf_result.avg_max_drawdown = np.mean([f.max_drawdown for f in folds])
            profitable = sum(1 for f in folds if f.net_pnl > 0)
            wf_result.consistency_ratio = profitable / len(folds)

            # Concatenar equity curves
            for f in folds:
                if f.result and f.result.equity_curve:
                    wf_result.combined_equity_curve.extend(f.result.equity_curve)

        return wf_result


# ══════════════════════════════════════════════════════════════════
# PARAMETER OPTIMIZER (GRID SEARCH)
# ══════════════════════════════════════════════════════════════════

@dataclass
class OptimizationResult:
    """Resultado de una combinacion de parametros."""
    params: Dict[str, Any] = field(default_factory=dict)
    total_trades: int = 0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0


@dataclass
class GridSearchResult:
    """Resultado completo de la optimizacion."""
    param_grid: Dict[str, List] = field(default_factory=dict)
    total_combinations: int = 0
    completed: int = 0
    duration_sec: float = 0.0
    results: List[OptimizationResult] = field(default_factory=list)
    best: Optional[OptimizationResult] = None
    metric_used: str = "sharpe_ratio"

    def summary(self) -> Dict:
        return {
            "total_combinations": self.total_combinations,
            "completed": self.completed,
            "duration_sec": round(self.duration_sec, 1),
            "metric": self.metric_used,
            "best_params": self.best.params if self.best else {},
            "best_sharpe": round(self.best.sharpe_ratio, 2) if self.best else 0,
            "best_pnl": round(self.best.net_pnl, 2) if self.best else 0,
            "best_win_rate": round(self.best.win_rate, 4) if self.best else 0,
        }

    def top_n(self, n: int = 10) -> List[OptimizationResult]:
        """Retorna los N mejores resultados."""
        return self.results[:n]


# Parametros predefinidos con rangos razonables
DEFAULT_PARAM_GRID = {
    "mr_zscore_entry": [1.5, 2.0, 2.5, 3.0],
    "mr_lookback": [50, 100, 150],
    "mr_atr_mult_sl": [1.0, 1.5, 2.0],
    "mr_atr_mult_tp": [2.0, 2.5, 3.0],
    "tf_ema_fast": [8, 12, 16],
    "tf_ema_slow": [21, 26, 34],
    "tf_atr_mult_trail": [1.5, 2.0, 2.5],
}


class ParameterOptimizer:
    """Grid search sobre parametros de estrategia.

    Ejecuta backtests para cada combinacion de parametros y retorna
    resultados rankeados por la metrica objetivo.

    Uso:
        opt = ParameterOptimizer(settings)
        result = opt.optimize(df, "BTC-USD",
                             param_grid={"mr_zscore_entry": [1.5, 2.0, 2.5]},
                             metric="sharpe_ratio")
        print(result.summary())
        for r in result.top_n(5):
            print(f"  {r.params} -> Sharpe={r.sharpe_ratio:.2f} PnL=${r.net_pnl:.2f}")
    """

    def __init__(self, settings: Settings) -> None:
        self.base_settings = settings

    def optimize(
        self,
        df: pd.DataFrame,
        symbol: str,
        param_grid: Optional[Dict[str, List]] = None,
        strategies: Optional[List[str]] = None,
        metric: str = "sharpe_ratio",
        max_combinations: int = 500,
        on_eval_callback: Optional[callable] = None,
    ) -> GridSearchResult:
        """Ejecuta grid search.

        Args:
            df: DataFrame OHLCV
            symbol: Simbolo a optimizar
            param_grid: Dict de parametro → lista de valores a probar.
                        Usa DEFAULT_PARAM_GRID si None.
            strategies: Estrategias a usar (None = todas)
            metric: Metrica para rankear ('sharpe_ratio', 'net_pnl', 'win_rate',
                    'profit_factor', 'calmar_ratio')
            max_combinations: Limite de combinaciones (para evitar explosion combinatoria)

        Returns:
            GridSearchResult con todos los resultados rankeados
        """
        grid = param_grid or DEFAULT_PARAM_GRID
        start_time = time.time()

        # Generar todas las combinaciones
        param_names = sorted(grid.keys())
        param_values = [grid[k] for k in param_names]
        all_combos = list(itertools.product(*param_values))

        if len(all_combos) > max_combinations:
            # Sample aleatorio si hay demasiadas
            rng = np.random.default_rng(42)
            indices = rng.choice(len(all_combos), max_combinations, replace=False)
            all_combos = [all_combos[i] for i in sorted(indices)]

        gs_result = GridSearchResult(
            param_grid=grid,
            total_combinations=len(all_combos),
            metric_used=metric,
        )

        for combo_idx, combo in enumerate(all_combos):
            params = dict(zip(param_names, combo))

            # Aplicar parametros
            settings = self._apply_params(params, symbol)
            backtester = Backtester(settings)

            try:
                result = backtester.run(df, symbol, strategies=strategies)
                summary = result.summary()

                opt_result = OptimizationResult(
                    params=params,
                    total_trades=summary.get("total_trades", 0),
                    net_pnl=summary.get("net_pnl", 0),
                    return_pct=summary.get("return_pct", 0),
                    win_rate=summary.get("win_rate", 0),
                    sharpe_ratio=summary.get("sharpe_ratio", 0),
                    profit_factor=summary.get("profit_factor", 0),
                    max_drawdown=summary.get("max_drawdown", 0),
                    calmar_ratio=summary.get("calmar_ratio", 0),
                )
                gs_result.results.append(opt_result)
                gs_result.completed += 1

                if on_eval_callback is not None:
                    on_eval_callback(
                        combo_idx=combo_idx,
                        total=len(all_combos),
                        params=params,
                        result=opt_result,
                        gs_result=gs_result,
                        metric=metric,
                    )

            except Exception as e:
                logger.warning("optimizer_combo_failed", params=params, error=str(e))

        # Rankear por metrica
        gs_result.results.sort(
            key=lambda r: getattr(r, metric, 0), reverse=True
        )
        if gs_result.results:
            gs_result.best = gs_result.results[0]

        gs_result.duration_sec = time.time() - start_time
        return gs_result

    def _apply_params(self, params: Dict[str, Any], symbol: str) -> Settings:
        """Crea Settings con parametros personalizados."""
        settings = copy.deepcopy(self.base_settings)
        try:
            sym_config = settings.get_symbol_config(symbol)
            for key, val in params.items():
                if hasattr(sym_config, key):
                    setattr(sym_config, key, val)
                elif hasattr(settings.trading, key):
                    setattr(settings.trading, key, val)
        except ValueError:
            pass
        return settings
