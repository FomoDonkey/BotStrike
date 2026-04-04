"""
Dashboard Data Layer — Puente entre el sistema de trading y los dashboards Streamlit.
Lee métricas del JSONL, estado del portfolio/risk, y provee datos listos para graficar.
"""
from __future__ import annotations
import json
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Importar desde el sistema existente
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings, SymbolConfig, TradingConfig
from core.types import StrategyType, MarketRegime, Side
from backtesting.backtester import Backtester, BacktestResult, RealisticBacktester, RealisticBacktestResult
from core.historical_data import HistoricalDataLoader


class DashboardState:
    """Estado compartido del dashboard, lee datos del sistema de trading."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or Settings()
        self._metrics_cache: List[Dict] = []
        self._cache_mtime: float = 0.0

    # ── Lectura de métricas JSONL ──────────────────────────────────

    def load_metrics(self, force: bool = False) -> List[Dict]:
        """Carga métricas del archivo JSONL producido por TradingLogger."""
        path = self.settings.metrics_file
        if not os.path.exists(path):
            return []

        mtime = os.path.getmtime(path)
        if not force and mtime == self._cache_mtime and self._metrics_cache:
            return self._metrics_cache

        records = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        self._metrics_cache = records
        self._cache_mtime = mtime
        return records

    def get_trades(self) -> pd.DataFrame:
        """Extrae trades ejecutados del log de métricas."""
        records = self.load_metrics()
        trades = [r for r in records if r.get("type") == "trade"]
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        if "timestamp" in df.columns:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        return df

    def get_signals(self) -> pd.DataFrame:
        """Extrae señales generadas del log."""
        records = self.load_metrics()
        signals = [r for r in records if r.get("type") == "signal"]
        if not signals:
            return pd.DataFrame()
        df = pd.DataFrame(signals)
        if "timestamp" in df.columns:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        return df

    def get_portfolio_snapshots(self) -> pd.DataFrame:
        """Extrae snapshots del portfolio del log."""
        records = self.load_metrics()
        snaps = [r for r in records if r.get("type") == "portfolio_snapshot"]
        if not snaps:
            return pd.DataFrame()
        return pd.DataFrame(snaps)

    def get_regime_changes(self) -> pd.DataFrame:
        """Extrae cambios de régimen del log."""
        records = self.load_metrics()
        changes = [r for r in records if r.get("type") == "regime_change"]
        if not changes:
            return pd.DataFrame()
        df = pd.DataFrame(changes)
        if "timestamp" in df.columns:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        return df

    def get_microstructure(self) -> pd.DataFrame:
        """Extrae métricas de microestructura del log (VPIN, Hawkes, A-S)."""
        records = self.load_metrics()
        micro = [r for r in records if r.get("type") == "microstructure"]
        if not micro:
            return pd.DataFrame()
        df = pd.DataFrame(micro)
        if "timestamp" in df.columns:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        return df

    def get_risk_events(self) -> pd.DataFrame:
        """Extrae eventos de riesgo del log."""
        records = self.load_metrics()
        events = [r for r in records if r.get("type") == "risk_event"]
        if not events:
            return pd.DataFrame()
        return pd.DataFrame(events)

    # ── Backtesting ────────────────────────────────────────────────

    def run_backtest(
        self,
        symbol: str,
        strategies: Optional[List[str]] = None,
        bars: int = 5000,
        custom_settings: Optional[Dict] = None,
        csv_path: Optional[str] = None,
    ) -> BacktestResult:
        """Ejecuta un backtest y retorna resultados.

        Args:
            symbol: Símbolo a testear
            strategies: Lista de estrategias (None = todas)
            bars: Número de barras para datos sintéticos
            custom_settings: Override de parámetros del Settings
            csv_path: Ruta a CSV con datos históricos reales
        """
        settings = self._apply_custom_settings(custom_settings)
        backtester = Backtester(settings)

        if csv_path and os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
        else:
            start_prices = {"BTC-USD": 50000, "ETH-USD": 3000, "ADA-USD": 0.5}
            df = Backtester.generate_sample_data(
                symbol=symbol,
                bars=bars,
                start_price=start_prices.get(symbol, 1000),
            )

        return backtester.run(df, symbol, strategies=strategies)

    def run_realistic_backtest(
        self,
        symbol: str,
        strategies: Optional[List[str]] = None,
        custom_settings: Optional[Dict] = None,
        csv_path: Optional[str] = None,
        data_type: str = "auto",
        hours: float = 24.0,
    ) -> RealisticBacktestResult:
        """Ejecuta backtest realista con tick-by-tick microestructura.

        Args:
            symbol: Símbolo a testear
            strategies: Lista de estrategias (None = todas)
            custom_settings: Override de parámetros
            csv_path: Ruta a CSV/Parquet de trades o OHLCV históricos
            data_type: "trades", "ohlcv", "auto"
            hours: Horas de datos sintéticos si no hay csv_path
        """
        settings = self._apply_custom_settings(custom_settings)
        loader = HistoricalDataLoader()

        if csv_path and os.path.exists(csv_path):
            loaded_symbol = loader.load(csv_path, data_type=data_type, symbol=symbol)
        else:
            # Generar trades sintéticos realistas
            start_prices = {"BTC-USD": 50000, "ETH-USD": 3000, "ADA-USD": 0.5}
            trades_df = HistoricalDataLoader.generate_realistic_trades(
                symbol=symbol,
                hours=hours,
                start_price=start_prices.get(symbol, 1000),
            )
            loader._trades[symbol] = trades_df
            loaded_symbol = symbol

        bars_with_trades = loader.get_bars_with_trades(loaded_symbol, interval="1min")
        if not bars_with_trades:
            # Fallback a OHLCV
            ohlcv = loader.get_ohlcv(loaded_symbol, interval="1min")
            bt = RealisticBacktester(settings)
            return bt.run(loaded_symbol, df=ohlcv, strategies=strategies)

        bt = RealisticBacktester(settings)
        return bt.run(
            loaded_symbol,
            bars_with_trades=bars_with_trades,
            strategies=strategies,
        )

    @staticmethod
    def load_jsonl_results(jsonl_path: str) -> Dict[str, pd.DataFrame]:
        """Carga resultados de un backtest realista desde JSONL.

        Retorna dict con DataFrames: trades, signals, microstructure,
        regime_changes, portfolio_snapshots, allocations.
        """
        if not os.path.exists(jsonl_path):
            return {}

        records: Dict[str, List] = {
            "trade": [], "signal": [], "microstructure": [],
            "regime_change": [], "portfolio_snapshot": [], "allocation": [],
        }

        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rtype = rec.get("type", "")
                    if rtype in records:
                        records[rtype].append(rec)
                except json.JSONDecodeError:
                    continue

        result = {}
        for rtype, recs in records.items():
            if recs:
                df = pd.DataFrame(recs)
                if "timestamp" in df.columns:
                    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
                result[rtype] = df
            else:
                result[rtype] = pd.DataFrame()

        return result

    def _apply_custom_settings(self, overrides: Optional[Dict]) -> Settings:
        """Crea Settings con overrides de parámetros personalizados."""
        if not overrides:
            return self.settings

        import copy
        settings = copy.deepcopy(self.settings)

        # Overrides de TradingConfig
        tc = overrides.get("trading", {})
        for key, val in tc.items():
            if hasattr(settings.trading, key):
                setattr(settings.trading, key, val)

        # Overrides por símbolo
        for sym_override in overrides.get("symbols", []):
            sym_name = sym_override.get("symbol")
            if sym_name:
                try:
                    sym_cfg = settings.get_symbol_config(sym_name)
                    for key, val in sym_override.items():
                        if key != "symbol" and hasattr(sym_cfg, key):
                            setattr(sym_cfg, key, val)
                except ValueError:
                    pass

        return settings

    # ── Utilidades para gráficos ───────────────────────────────────

    @staticmethod
    def equity_to_drawdown(equity_curve: List[float]) -> List[float]:
        """Convierte curva de equity a curva de drawdown porcentual."""
        if not equity_curve:
            return []
        drawdowns = []
        peak = equity_curve[0]
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            drawdowns.append(dd)
        return drawdowns

    @staticmethod
    def trades_to_cumulative_pnl(trades: List[Dict]) -> pd.DataFrame:
        """Convierte lista de trades a PnL acumulado por estrategia."""
        if not trades:
            return pd.DataFrame()

        df = pd.DataFrame(trades)
        df["cumulative_pnl"] = df["pnl"].cumsum()

        # Por estrategia
        result = pd.DataFrame({"total": df["cumulative_pnl"].values})
        for strat in df["strategy"].unique():
            mask = df["strategy"] == strat
            strat_pnl = df.loc[mask, "pnl"].cumsum()
            # Reindexar para alinearse con el total, forward-fill gaps
            aligned = pd.Series(np.nan, index=df.index)
            aligned.loc[mask] = strat_pnl.values
            result[strat] = aligned.ffill().fillna(0)

        return result

    @staticmethod
    def compute_rolling_sharpe(pnls: List[float], window: int = 50) -> List[float]:
        """Calcula Sharpe ratio rolling sobre una ventana de trades."""
        if len(pnls) < window:
            return [0.0] * len(pnls)
        series = pd.Series(pnls)
        rolling_mean = series.rolling(window).mean()
        rolling_std = series.rolling(window).std()
        sharpe = (rolling_mean / rolling_std.replace(0, np.nan)) * (252 ** 0.5)
        return sharpe.fillna(0).tolist()
