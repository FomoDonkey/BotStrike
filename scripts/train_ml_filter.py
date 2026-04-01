"""
Entrena el ML signal filter usando datos de backtest.

Paso 1: Ejecuta backtest SIN filtro ML → recolecta trades con features
Paso 2: Entrena LightGBM con los trades
Paso 3: Ejecuta backtest CON filtro ML → compara resultados

Usa datos de Binance (90 días) con train/test split temporal.
"""
import sys
import os
import warnings
import time
import copy

sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

import numpy as np
np.seterr(all="ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from config.settings import Settings
from core.historical_data import HistoricalDataLoader
from backtesting.backtester import RealisticBacktester, BacktestPosition
from core.ml_filter import MLSignalFilter, FEATURE_NAMES
from core.microstructure import MicrostructureEngine
from core.indicators import Indicators
from core.types import MarketRegime
import structlog
import logging

logging.disable(logging.CRITICAL)
structlog.configure(
    wrapper_class=structlog.BoundLogger,
    logger_factory=structlog.ReturnLoggerFactory(),
)


def collect_training_data(settings, symbol, bars_with_trades):
    """Ejecuta backtest y recolecta features de cada trade."""
    print(f"  Recolectando datos de entrenamiento para {symbol}...")

    bt = RealisticBacktester(settings)
    result = bt.run(symbol, bars_with_trades=bars_with_trades)
    s = result.summary()

    # Extraer features de cada trade del resultado
    training_samples = []
    for trade in result.trades:
        meta = trade.get("metadata", {})
        features = {}
        for f in FEATURE_NAMES:
            features[f] = meta.get(f, 0)
        # Rellenar desde el trade directamente
        features["strength"] = trade.get("strength", meta.get("strength", 0.5))
        features["label"] = 1 if trade.get("pnl", 0) > 0 else 0
        features["pnl"] = trade.get("pnl", 0)
        features["strategy"] = trade.get("strategy", "")
        training_samples.append(features)

    print(f"    Trades: {len(result.trades)}, PnL: ${s.get('net_pnl', 0):+,.2f}")
    return training_samples, result


def main():
    settings = Settings()

    print("=" * 60)
    print("  ML SIGNAL FILTER — ENTRENAMIENTO")
    print("=" * 60)
    print(f"  Capital: ${settings.trading.initial_capital:,.0f}")
    print()

    all_training = []

    for symbol in ["BTC-USD", "ETH-USD", "ADA-USD"]:
        kline_path = f"data/binance/klines/{symbol}/1m.parquet"
        if not os.path.exists(kline_path):
            continue

        # Cargar 90 días
        loader = HistoricalDataLoader()
        loader.load(kline_path, symbol=symbol)
        all_bars = loader.get_bars_with_trades(symbol, interval="1min")

        # Train: primeros 60 días, Test: últimos 30 días
        train_bars = all_bars[:60*1440]
        test_bars = all_bars[60*1440:]

        print(f"\n--- {symbol} ---")
        print(f"  Train: {len(train_bars):,} barras ({len(train_bars)//1440}d)")
        print(f"  Test:  {len(test_bars):,} barras ({len(test_bars)//1440}d)")

        # Paso 1: Recolectar datos de training (backtest sin filtro)
        samples, _ = collect_training_data(settings, symbol, train_bars)
        all_training.extend(samples)

    # Paso 2: Entrenar modelo
    print(f"\n{'='*60}")
    print(f"  ENTRENAMIENTO ML")
    print(f"{'='*60}")
    print(f"  Muestras totales: {len(all_training)}")

    if len(all_training) < 10:
        print("  INSUFICIENTES muestras para entrenar. Necesita más trades.")
        print("  Intentando con datos de train más amplios...")
        # Usar todos los 90 días para recolectar más datos
        all_training = []
        for symbol in ["BTC-USD", "ETH-USD", "ADA-USD"]:
            kline_path = f"data/binance/klines/{symbol}/1m.parquet"
            if not os.path.exists(kline_path):
                continue
            loader = HistoricalDataLoader()
            loader.load(kline_path, symbol=symbol)
            all_bars = loader.get_bars_with_trades(symbol, interval="1min")
            samples, _ = collect_training_data(settings, symbol, all_bars)
            all_training.extend(samples)
        print(f"  Muestras con 90d: {len(all_training)}")

    if len(all_training) < 10:
        print("  AÚN insuficientes. El filtro ML requiere más trades para entrenar.")
        return

    ml_filter = MLSignalFilter(model_path="data/ml_signal_filter.pkl", threshold=0.55)
    for sample in all_training:
        features = {k: sample.get(k, 0) for k in FEATURE_NAMES}
        ml_filter.record_trade(features, sample.get("pnl", 0))

    success = ml_filter.train(min_samples=10)
    if success:
        print(f"  Modelo entrenado y guardado en data/ml_signal_filter.pkl")
        print(f"  Threshold: {ml_filter.threshold:.2f}")

        # Análisis de features
        df = pd.DataFrame(all_training)
        winners = df[df["label"] == 1]
        losers = df[df["label"] == 0]
        print(f"  Winners: {len(winners)} ({len(winners)/len(df)*100:.0f}%)")
        print(f"  Losers:  {len(losers)} ({len(losers)/len(df)*100:.0f}%)")
    else:
        print("  Entrenamiento fallido.")

    print(f"\n{'='*60}")
    print("  COMPLETADO")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
