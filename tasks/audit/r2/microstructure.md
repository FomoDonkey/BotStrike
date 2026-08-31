# Auditoria R2 — Microestructura y datos

**Fecha:** 2026-08-31 · **Area:** `microstructure`
**Alcance:** `core/microstructure.py` (VPIN, Hawkes, Avellaneda-Stoikov, Kyle Lambda), `core/microprice.py`,
`core/orderbook_alpha.py`, `core/market_data.py`, `exchange/binance_ws.py` (parseo trade/depth/kline/markPrice)
y su consumo real en `main.py` / `risk/risk_manager.py` / `backtesting/backtester.py`.

**Metodo:** lectura del codigo real + snippets ejecutados con `py -3.12` (salidas pegadas literales) +
datos reales de Binance USDT-M (REST `fapi/v1/aggTrades`, `fapi/v1/ticker/24hr`, `fapi/v1/klines`) +
trades reales de `data/trade_database.db`.

**Referencia ronda 1:** `tasks/audit/01_core_strategy_risk.md` (F16, F18, F20, F22, F23, F28),
`tasks/audit/02_exchange_execution.md` (P1-04).

> (en construccion — se rellena incrementalmente)
