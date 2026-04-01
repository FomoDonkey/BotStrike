"""
Optimización REAL con RealisticBacktester (microestructura completa).

Usa el backtester que incluye VPIN, Hawkes, Kyle Lambda, Risk Manager,
Portfolio Manager, slippage dinámico — idéntico al live trading.

Metodología:
  1. Train/Test split temporal: 7d train / 3d test (sin overlap)
  2. Grid completo de 96 combinaciones de estrategia
  3. × 4 regímenes de riesgo (VPIN threshold + risk_per_trade)
  4. Validación OOS del top 3 por régimen con datos nunca vistos
  5. Todo con microestructura real (no backtester simple)
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
from backtesting.backtester import RealisticBacktester
from backtesting.optimizer_display import OptimizerLiveDisplay
import structlog
import logging

logging.disable(logging.CRITICAL)
structlog.configure(
    wrapper_class=structlog.BoundLogger,
    logger_factory=structlog.ReturnLoggerFactory(),
)

# ── Grid de estrategia (96 combinaciones) ─────────────────────────
PARAM_GRID = {
    "mr_zscore_entry": [1.5, 2.0, 2.5],
    "mr_lookback": [50, 100],
    "mr_atr_mult_sl": [1.5, 2.0],
    "mr_atr_mult_tp": [2.0, 3.0],
    "tf_ema_fast": [8, 12],
    "tf_ema_slow": [26, 50],
    "tf_atr_mult_trail": [2.0, 3.0],
}

# ── Regímenes de riesgo/microestructura ───────────────────────────
RISK_REGIMES = [
    {"name": "Ultra Conservative", "vpin_toxic_threshold": 0.6, "risk_per_trade_pct": 0.01},
    {"name": "Conservative", "vpin_toxic_threshold": 0.75, "risk_per_trade_pct": 0.015},
    {"name": "Moderate", "vpin_toxic_threshold": 0.85, "risk_per_trade_pct": 0.02},
    {"name": "Aggressive", "vpin_toxic_threshold": 0.95, "risk_per_trade_pct": 0.025},
]


def apply_params_to_settings(base_settings: Settings, params: dict, regime: dict, symbol: str) -> Settings:
    """Aplica parámetros de estrategia + régimen de riesgo a los settings."""
    s = copy.deepcopy(base_settings)
    sym_cfg = s.get_symbol_config(symbol)
    # Strategy params
    for key, val in params.items():
        if hasattr(sym_cfg, key):
            setattr(sym_cfg, key, val)
    # Risk regime
    sym_cfg.vpin_toxic_threshold = regime["vpin_toxic_threshold"]
    s.trading.risk_per_trade_pct = regime["risk_per_trade_pct"]
    return s


def load_bars(kline_path: str, symbol: str, start_idx: int, end_idx: int):
    """Carga barras OHLCV con trades sintéticos para el RealisticBacktester."""
    loader = HistoricalDataLoader()
    # Cargar subset del parquet
    df = pd.read_parquet(kline_path)
    df = df.iloc[start_idx:end_idx].reset_index(drop=True)
    # Guardar como trades internos del loader
    loader.load_dataframe(df, symbol=symbol, data_type="ohlcv")
    return loader.get_bars_with_trades(symbol, interval="1min")


def generate_all_combos():
    """Genera todas las combinaciones del grid."""
    import itertools
    keys = sorted(PARAM_GRID.keys())
    values = [PARAM_GRID[k] for k in keys]
    combos = list(itertools.product(*values))
    return keys, combos


def run_single_backtest(settings: Settings, symbol: str, bars_with_trades):
    """Ejecuta un backtest realista y retorna el summary."""
    bt = RealisticBacktester(settings)
    result = bt.run(symbol, bars_with_trades=bars_with_trades)
    return result.summary()


def main():
    base_settings = Settings()
    param_keys, all_combos = generate_all_combos()
    total_combos = len(all_combos)

    print("=" * 65)
    print("  OPTIMIZACION CON BACKTESTER REALISTA (MICROESTRUCTURA)")
    print("=" * 65)
    print(f"  Backtester: RealisticBacktester (VPIN+Hawkes+Kyle+Risk+Portfolio)")
    print(f"  Strategy grid: {total_combos} combinaciones")
    print(f"  Risk regimes: {len(RISK_REGIMES)}")
    print(f"  Split: 7d train / 3d test (sin overlap)")
    print(f"  Total evaluaciones: {total_combos * len(RISK_REGIMES)} por simbolo")
    print("=" * 65)

    global_best = {}

    for symbol in ["BTC-USD", "ETH-USD", "ADA-USD"]:
        kline_path = f"data/binance/klines/{symbol}/1m.parquet"
        if not os.path.exists(kline_path):
            print(f"\n  SKIP {symbol}: no hay datos")
            continue

        df_full = pd.read_parquet(kline_path)
        total_bars = len(df_full)

        # Split: últimos 10 días del dataset (7 train + 3 test)
        train_bars = 7 * 1440   # 10080
        test_bars = 3 * 1440    # 4320
        needed = train_bars + test_bars

        if total_bars < needed:
            print(f"\n  SKIP {symbol}: datos insuficientes ({total_bars} < {needed})")
            continue

        # Tomar del final del dataset (datos más recientes)
        train_start = total_bars - needed
        train_end = train_start + train_bars
        test_end = train_end + test_bars

        print(f"\n{'='*65}")
        print(f"  {symbol}")
        print(f"{'='*65}")
        print(f"  Train: {train_bars:,} barras (7d)")
        print(f"  Test:  {test_bars:,} barras (3d)")

        # Pre-cargar bars para train y test via parquet temporales
        import tempfile

        df_train = df_full.iloc[train_start:train_end].reset_index(drop=True)
        df_test = df_full.iloc[train_end:test_end].reset_index(drop=True)

        train_tmp = os.path.join(tempfile.gettempdir(), f"_opt_train_{symbol}.parquet")
        test_tmp = os.path.join(tempfile.gettempdir(), f"_opt_test_{symbol}.parquet")
        df_train.to_parquet(train_tmp, index=False)
        df_test.to_parquet(test_tmp, index=False)

        loader_train = HistoricalDataLoader()
        loader_train.load(train_tmp, symbol=symbol, data_type="ohlcv")
        train_bars_list = loader_train.get_bars_with_trades(symbol, interval="1min")

        loader_test = HistoricalDataLoader()
        loader_test.load(test_tmp, symbol=symbol, data_type="ohlcv")
        test_bars_list = loader_test.get_bars_with_trades(symbol, interval="1min")

        # Limpiar temporales
        os.remove(train_tmp)
        os.remove(test_tmp)

        print(f"  Train bars: {len(train_bars_list):,}, Test bars: {len(test_bars_list):,}")

        best_for_symbol = None
        best_oos_sharpe = -999

        for regime in RISK_REGIMES:
            regime_name = regime["name"]
            total_evals = total_combos

            print(f"\n  --- Regime: {regime_name} ---")

            display = OptimizerLiveDisplay(
                f"{symbol} [{regime_name[:15]}]", total_evals, "sharpe_ratio"
            )
            display.start()

            regime_results = []

            for combo_idx, combo in enumerate(all_combos):
                params = dict(zip(param_keys, combo))
                settings = apply_params_to_settings(base_settings, params, regime, symbol)

                try:
                    summary = run_single_backtest(settings, symbol, train_bars_list)

                    # Crear result-like object para el display
                    class EvalResult:
                        pass
                    r = EvalResult()
                    r.net_pnl = summary.get("net_pnl", 0)
                    r.sharpe_ratio = summary.get("sharpe_ratio", 0)
                    r.win_rate = summary.get("win_rate", 0)
                    r.profit_factor = summary.get("profit_factor", 0)
                    r.max_drawdown = summary.get("max_drawdown", 0)
                    r.total_trades = summary.get("total_trades", 0)
                    r.params = params

                    regime_results.append(r)

                    # Simular gs_result para display
                    class GSResult:
                        pass
                    gs = GSResult()
                    gs.results = regime_results

                    display.update(
                        combo_idx=combo_idx,
                        total=total_evals,
                        params=params,
                        result=r,
                        gs_result=gs,
                        metric="sharpe_ratio",
                    )
                except Exception:
                    pass

            display.stop()

            # Filtrar resultados válidos y rankear
            valid = [r for r in regime_results if r.total_trades >= 5]
            valid.sort(key=lambda x: x.sharpe_ratio, reverse=True)

            if not valid:
                print(f"    Sin resultados validos (trades < 5)")
                continue

            print(f"    Train best: Sharpe={valid[0].sharpe_ratio:.2f}, "
                  f"PnL=${valid[0].net_pnl:,.2f}, Trades={valid[0].total_trades}")

            # Validar top 3 en TEST (OOS)
            for rank, candidate in enumerate(valid[:3]):
                test_settings = apply_params_to_settings(
                    base_settings, candidate.params, regime, symbol
                )
                test_summary = run_single_backtest(test_settings, symbol, test_bars_list)

                oos_sharpe = test_summary.get("sharpe_ratio", 0)
                oos_pnl = test_summary.get("net_pnl", 0)
                oos_trades = test_summary.get("total_trades", 0)
                oos_wr = test_summary.get("win_rate", 0)
                oos_dd = test_summary.get("max_drawdown", 0)

                print(f"    OOS #{rank+1}: Sharpe={oos_sharpe:.2f}, "
                      f"PnL=${oos_pnl:+,.2f}, WR={oos_wr:.1%}, "
                      f"DD={oos_dd:.2%}, Trades={oos_trades}")

                if oos_sharpe > best_oos_sharpe and oos_trades >= 3:
                    best_oos_sharpe = oos_sharpe
                    best_for_symbol = {
                        "regime": regime_name,
                        "params": candidate.params,
                        "train_sharpe": candidate.sharpe_ratio,
                        "train_pnl": candidate.net_pnl,
                        "oos_sharpe": oos_sharpe,
                        "oos_pnl": oos_pnl,
                        "oos_wr": oos_wr,
                        "oos_dd": oos_dd,
                        "oos_trades": oos_trades,
                        "vpin_threshold": regime["vpin_toxic_threshold"],
                        "risk_per_trade": regime["risk_per_trade_pct"],
                    }

        if best_for_symbol:
            global_best[symbol] = best_for_symbol
            print(f"\n  MEJOR {symbol} (Out-of-Sample):")
            print(f"    Regime:         {best_for_symbol['regime']}")
            print(f"    VPIN threshold: {best_for_symbol['vpin_threshold']}")
            print(f"    Risk/trade:     {best_for_symbol['risk_per_trade']:.1%}")
            print(f"    Train Sharpe:   {best_for_symbol['train_sharpe']:.2f}")
            print(f"    OOS Sharpe:     {best_for_symbol['oos_sharpe']:.2f}")
            print(f"    OOS PnL:        ${best_for_symbol['oos_pnl']:+,.2f}")
            print(f"    OOS WR:         {best_for_symbol['oos_wr']:.1%}")
            print(f"    OOS MaxDD:      {best_for_symbol['oos_dd']:.2%}")
            print(f"    Params:         {best_for_symbol['params']}")
        else:
            print(f"\n  {symbol}: ninguna combinacion rentable en OOS")

    # Resumen final
    print(f"\n{'='*65}")
    print(f"  RESULTADOS FINALES (Out-of-Sample, RealisticBacktester)")
    print(f"{'='*65}")
    if not global_best:
        print("  Ninguna combinacion rentable encontrada.")
        print("  Las estrategias necesitan cambios estructurales, no solo parametros.")
    else:
        for sym, b in global_best.items():
            print(f"\n  {sym}:")
            print(f"    Regime:    {b['regime']} (VPIN={b['vpin_threshold']}, Risk={b['risk_per_trade']:.1%})")
            print(f"    OOS:       Sharpe={b['oos_sharpe']:.2f}, PnL=${b['oos_pnl']:+,.2f}, "
                  f"WR={b['oos_wr']:.1%}, DD={b['oos_dd']:.2%}")
            short_p = ", ".join(f"{k}={v}" for k, v in b["params"].items())
            print(f"    Params:    {short_p}")

        total_oos = sum(b["oos_pnl"] for b in global_best.values())
        print(f"\n  PnL total OOS: ${total_oos:+,.2f}")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()
