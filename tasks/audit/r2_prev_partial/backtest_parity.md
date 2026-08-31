# R2 — Paridad backtest <-> live (backtest_parity)

**Fecha:** 2026-08-30 · **Auditor:** agente quant r2 (dominio: backtester vs loop live, buffers, datos)
**Alcance:** `backtesting/backtester.py`, `main.py` (`_process_symbol`, WS callbacks, `run_backtest*`), `core/market_data.py`, `core/historical_data.py`, `execution/paper_simulator.py`, `strategies/{mean_reversion,fibonacci_retracement}.py`, `scripts/download_futures_klines.py`, `scripts/*`.
**Método:** lectura línea a línea + experimento de paridad ejecutado con `py -3.12` sobre 3 días reales de BTC futures 1m. Nada se afirma sin verificar. Referencias a la ronda 1: `04-Pxx` = `tasks/audit/04_backtest_quant_evidence.md`.

> Documento incremental: se rellena conforme se confirma cada hallazgo.

## 0. Estado de los fixes de ronda 1 que tocan esta área

(pendiente de rellenar)

## 1. Tabla de paridad (ciclo live vs Backtester vs RealisticBacktester)

(pendiente de rellenar)

## 2. Experimento de paridad (ejecutado)

(pendiente de rellenar)

## 3. Hallazgos

