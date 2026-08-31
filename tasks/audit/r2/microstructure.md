# Auditoria R2 — Microestructura y datos

**Fecha:** 2026-08-31 · **Alcance:** `core/microstructure.py`, `core/microprice.py`, `core/orderbook_alpha.py`, `core/market_data.py`, `core/indicators.py` (coste en el hot path), `exchange/binance_ws.py` (parseo trade/depth/kline/markPrice) y su consumo en `main.py` / `risk/risk_manager.py`.
**Metodo:** lectura del codigo real + snippets ejecutados con `py -3.12` (salidas pegadas) + **WS real de Binance USDT-M** (`wss://fstream.binance.com`, sin claves, 52 s + 25 s + 4 min de captura) + `SUBSCRIBE`/`LIST_SUBSCRIPTIONS` contra el endpoint oficial.
**Referencia ronda 1:** `tasks/audit/01_core_strategy_risk.md` (F16, F18, F20, F22, F23, F28), `tasks/audit/02_exchange_execution.md` (P1-04), `tasks/audit/fixes_round1.md`.

---

## Datos de mercado reales usados como base (medidos, no supuestos)

Captura del stream combinado exacto que construye `_build_streams()`
(`btcusdt@trade/btcusdt@depth20@100ms/btcusdt@kline_1m/btcusdt@markPrice@1s` × 4 simbolos), 52.4 s:

```
elapsed=52.4s total_msgs=9534 rate=182.1/s
adausdt@depth20@100ms   374    7.14/s        adausdt@trade    214    4.09/s
btcusdt@depth20@100ms   425    8.12/s        btcusdt@trade   2466   47.09/s
ethusdt@depth20@100ms   428    8.17/s        ethusdt@trade   3791   72.40/s
solusdt@depth20@100ms   414    7.91/s        solusdt@trade   1422   27.16/s
--- trade notional ---
btcusdt: 2466 trades, $5,807,596 en 52.4s -> $110,912/s, trade medio $2,355
ethusdt: 3791 trades, $5,051,625 en 52.4s -> $96,474/s,  trade medio $1,333
solusdt: 1422 trades, $1,214,514 en 52.4s -> $23,194/s,  trade medio $854
adausdt:  214 trades,    $61,530 en 52.4s -> $1,175/s,   trade medio $288
```

**Rate real de mensajes = 182/s** en el unico event loop del proceso. Este es el presupuesto contra el
que hay que medir cualquier trabajo en `on_market_trade` / `on_depth_update`.

---

## Estado de los hallazgos de ronda 1 en esta area

| ID r1 | Estado hoy | Evidencia |
|---|---|---|
| 01-F16 (seed incluye vela en formacion, timestamps open vs close) | **SIGUE ABIERTO** y peor de lo descrito | ver `MICRO-03` |
| 01-F18 (`compute_all` 169 ms en el callback WS) | **SIGUE ABIERTO** (45 ms/simbolo en esta maquina, 181 ms por minuto con 4 simbolos) | ver `MICRO-05` |
| 01-F20 (clamp del microprice compuesto) | **SIGUE ABIERTO** literal, `core/microprice.py:231` | ver `MICRO-08` |
| 01-F22 (bucket VPIN 50k USD) | **SIGUE ABIERTO**, medido: bucket = 0.45 s en BTC | ver `MICRO-06` |
| 01-F23 (`refresh_all` reemplaza el snapshot) | **SIGUE ABIERTO** (`core/market_data.py:314`) | ver `MICRO-12` |
| 01-F28 (sin seed, barras 1m desalineadas) | **SIGUE ABIERTO** (`core/market_data.py:348-349`) | ver `MICRO-03` |
| 02-P1-04 (depth `b`/`a`) | **ARREGLADO Y CORRECTO** | payload real: `{"e":"depthUpdate",...,"b":[["78014.20","5.687"],...],"a":[...]}` — el fix de `binance_ws.py:150-153` lee exactamente esas claves |

Regresiones de ronda 1: **ninguna nueva introducida en esta area**. El fix de `b`/`a` es correcto y
esta verificado contra el payload real.

---

## Hallazgos nuevos

(se rellena incrementalmente)
