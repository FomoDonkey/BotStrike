# R2 — Paridad backtest <-> live (backtest_parity)

**Fecha:** 2026-08-31 · **Auditor:** agente quant r2 (dominio: backtester vs loop live, buffers, datos)
**Alcance:** `backtesting/backtester.py`, `main.py` (`_process_symbol`, WS callbacks, `run_backtest*`), `core/market_data.py`, `core/historical_data.py`, `execution/paper_simulator.py`, `strategies/{mean_reversion,fibonacci_retracement}.py`, `portfolio/portfolio_manager.py`, `scripts/download_futures_klines.py`, `scripts/*`.
**Método:** lectura línea a línea + experimento de paridad ejecutado con `py -3.12` sobre 3 días reales de BTC futures 1m (`data/binance_futures/klines/BTC-USD/1m.parquet`, 216 592 velas, 0 gaps, 0 dups — verificado). Nada se afirma sin verificar. Referencias a la ronda 1: `04-*` = `tasks/audit/04_backtest_quant_evidence.md`, `01-*` = `tasks/audit/01_core_strategy_risk.md`.

> Documento incremental: se rellena conforme se confirma cada hallazgo.

## 0. Estado de los fixes de ronda 1 que tocan esta área

(pendiente)

## 1. Tabla de paridad (ciclo live vs Backtester vs RealisticBacktester)

(pendiente)

## 2. Experimento de paridad (ejecutado)

(pendiente)

## 3. Hallazgos

(pendiente)
