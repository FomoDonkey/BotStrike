"""Ejecuta backtest realista con datos de Binance con visualización en vivo."""
import sys
import os
import warnings

sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

import numpy as np
np.seterr(all="ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings
from core.historical_data import HistoricalDataLoader
from backtesting.backtester import RealisticBacktester
from backtesting.live_display import BacktestLiveDisplay
import structlog
import logging
import time as _time

logging.disable(logging.CRITICAL)
structlog.configure(
    wrapper_class=structlog.BoundLogger,
    logger_factory=structlog.ReturnLoggerFactory(),
)

settings = Settings()
results_all = {}
symbols = ["BTC-USD", "ETH-USD", "ADA-USD"]

print()
print("=" * 65)
print("  BACKTEST REALISTA - DATOS BINANCE (90 DIAS)")
print("=" * 65)
print(f"  Simbolos:    {', '.join(symbols)}")
print(f"  Capital:     ${settings.trading.initial_capital:,.0f}")
print(f"  Estrategias: Mean Reversion + Trend Following + Market Making")
print("=" * 65)
print()

for symbol in symbols:
    kline_path = f"data/binance/klines/{symbol}/1m.parquet"
    if not os.path.exists(kline_path):
        print(f"  SKIP: {kline_path} no existe")
        continue

    loader = HistoricalDataLoader()
    loaded = loader.load(kline_path, symbol=symbol)
    info = loader.get_info(loaded)

    bars_with_trades = loader.get_bars_with_trades(symbol, interval="1min")
    total_bars = len(bars_with_trades)

    # Crear display visual en vivo
    display = BacktestLiveDisplay(symbol, total_bars)
    display.start()

    t0 = _time.time()
    bt = RealisticBacktester(settings)
    result = bt.run(
        symbol,
        bars_with_trades=bars_with_trades,
        on_bar_callback=display.update,
    )
    elapsed = _time.time() - t0

    display.stop()

    summary = result.summary()
    results_all[symbol] = summary

    # Resumen post-backtest
    print(f"\n  {symbol} completado en {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"    Trades: {summary.get('total_trades', 0)}")
    print(f"    PnL: ${summary.get('net_pnl', 0):,.2f} ({summary.get('return_pct', 0):+.2f}%)")
    print(f"    Sharpe: {summary.get('sharpe_ratio', 0):.2f}")
    print(f"    Max DD: {summary.get('max_drawdown', 0):.2%}")

    if summary.get("by_strategy"):
        for st, data in summary["by_strategy"].items():
            print(f"      [{st}] trades={data['trades']}, pnl=${data['pnl']:,.2f}, wr={data['win_rate']:.2%}")
    print()

# Resumen final
print(f"\n{'='*65}")
print(f"  RESUMEN GLOBAL")
print(f"{'='*65}")
total_pnl = sum(r.get("net_pnl", 0) for r in results_all.values())
total_trades = sum(r.get("total_trades", 0) for r in results_all.values())
print(f"  Total trades: {total_trades}")
print(f"  PnL total:    ${total_pnl:,.2f}")
print(f"  Retorno:      {total_pnl / settings.trading.initial_capital * 100:+.2f}%")
print()
print(f"  {'Simbolo':<12} {'PnL':>12} {'Retorno':>10} {'Sharpe':>8} {'MaxDD':>10} {'WinRate':>10} {'Trades':>8}")
print(f"  {'-'*12} {'-'*12} {'-'*10} {'-'*8} {'-'*10} {'-'*10} {'-'*8}")
for sym, r in results_all.items():
    pnl = r.get("net_pnl", 0)
    ret = r.get("return_pct", 0)
    sharpe = r.get("sharpe_ratio", 0)
    dd = r.get("max_drawdown", 0)
    wr = r.get("win_rate", 0)
    trades = r.get("total_trades", 0)
    print(f"  {sym:<12} ${pnl:>10,.2f} {ret:>+9.2f}% {sharpe:>7.2f} {dd:>9.2%} {wr:>9.2%} {trades:>7}")
print(f"{'='*65}")
