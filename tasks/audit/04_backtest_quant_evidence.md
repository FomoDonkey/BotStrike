# 04 — Auditoría quant de la evidencia de rentabilidad (backtesting / datos / métricas)

**Fecha:** 2026-08-29 · **Auditor:** agente quant (dominio: backtester, walk-forward, optimizer, datos, métricas)
**Alcance:** `backtesting/`, `data/`, `core/historical_data.py`, `analytics/performance.py`, `scripts/{quant_audit,optimize_with_binance,research_report,exit_analysis}.py`, `data/catalog.json`, datos en disco.
**Método:** lectura línea a línea + ejecución real (`py -3.12`) con comandos y salidas recortadas pegadas aquí. Nada se afirma sin verificar.

> Documento incremental: se va rellenando conforme se audita/ejecuta.

## 0. Inventario de datos REAL en disco (verificado)

Comando: `py -3.12` inspección de parquet (pandas 2.3.3 / pyarrow 23.0.1). Salida recortada:

| Carpeta | Fuente (verificada) | Símbolos | Rango real | Filas | TF | Gaps | Uso |
|---|---|---|---|---|---|---|---|
| `data/binance/klines/*/1m.parquet` | **SPOT** `api.binance.com/api/v3` (`data/binance_downloader.py:41`) | BTC, ETH, ADA, SOL, BNB | BTC 2025-12-29→2026-04-03; ETH/ADA →03-29; SOL/BNB 01-05→04-05 | 129.6k–137k | 1m | 0 | Dataset de TODOS los backtests reportados en `tasks/todo.md` (`run_full_backtest.py:77`, `run_backtest_binance.py:44`, `optimize_with_binance.py:122`) |
| `data/binance/klines/klines/{BNB,SOL}-USD` | duplicado anidado (ruta mal compuesta) | BNB, SOL | ídem | | | | Basura |
| `data/binance/trades/*` | SPOT aggTrades | BTC, ETH, ADA | 2026-03-22→03-29 (8 días) | 203 MB | tick | | No usado por los runners actuales |
| `data/klines/*/1m.parquet` | colector live | BTC, ETH, SOL, ADA | 2026-03-20→2026-08-12 | 10k–13.6k | 1m | **500–697 gaps** por símbolo | **Inservible** para backtest |
| `data/binance_futures/klines/*/1m.parquet` | **USDT-M FUTURES** `fapi.binance.com/fapi/v1/klines` — verificado: la vela 1775043600000 devuelta por fapi (o=68582.5 h=68600 l=68575 c=68575 vol=36.598 trades=1490) es idéntica a la fila 0 del parquet | BTC, ETH, SOL, ADA | 2026-04-01 11:40 → 2026-08-29 21:31 (150.4 d) | 216,588–216,592 | 1m | **0** | Creados hoy 13:41 por un proceso externo al repo (ningún script del repo escribe en `binance_futures`); extendidos y verificados con el script nuevo |
| `data/binance_futures/funding/*.parquet` | `fapi/v1/fundingRate` (nuevo, descargado hoy) | 4 símbolos | 2026-04-02→2026-08-29 | 450 pagos c/u | 8h | | Media BTC +0.31 bps/8h (min −1.23, max +1.00); ETH +0.22; SOL +0.06; ADA +0.14 |
| `data/catalog.json` | catálogo | | `updated_at` 2026-03-26; lista klines de 1079 filas | | | | **Obsoleto** |
| `data/trade_database.db` (`trades`) | SQLite paper/live/backtest | | | **0 filas** | | | **No hay ni un trade paper/live persistido** |
| `events.jsonl` (raíz, 5.2 MB) | log de hooks de Claude (`tool`, `phase`, `agent_id`) | | | 20.6k | | | **No es evidencia de trading** |
| `logs/metrics.jsonl` | 10 líneas, equity 1000, 0 trades | | | | | | Sin evidencia |

**Conclusión del inventario:** hasta hoy (2026-08-29 13:41) el repo NUNCA tuvo datos de futures; toda la "evidencia" del `todo.md` se generó con velas SPOT (dic-2025→abr-2026) mientras el bot live lee `fapi` (`core/market_data.py:129`). Los datos futures son válidos (150 d, 0 gaps) y son los usados abajo.

### Script nuevo: `scripts/download_futures_klines.py`
Único archivo de código creado. Descarga klines USDT-M futures (fapi, `limit=1500`, reanuda desde el último timestamp, solo velas cerradas) y opcionalmente funding rates; guarda en el formato que `HistoricalDataLoader.load(path, symbol=...)` acepta (timestamp ms int64 + OHLCV; el loader convierte ms→s en `core/historical_data.py:220-224`).

Comando ejecutado y salida real (recortada):
```
$ py -3.12 scripts/download_futures_klines.py --days 150 --funding
  [BTC-USD] reanudando desde 2026-08-29 11:40 (ya hay 216,000 velas)
  [BTC-USD] guardado 216,592 velas 2026-04-01 11:40 -> 2026-08-29 21:31  gaps=0
  [BTC-USD] funding: 450 pagos 2026-04-02 00:00 -> 2026-08-29 16:00  media=0.31 bps/8h  min=-1.23  max=1.00
  [ETH-USD] ... 216,590 velas gaps=0 ; funding media=0.22 bps/8h min=-2.30 max=1.00
  [SOL-USD] ... 216,589 velas gaps=0 ; funding media=0.06 bps/8h min=-3.88 max=1.00
  [ADA-USD] ... 216,588 velas gaps=0 ; funding media=0.14 bps/8h min=-3.13 max=1.00
  listo en 8s
$ py -3.12 scripts/download_futures_klines.py --verify
  ADA-USD    216,588 velas  2026-04-01 11:44 -> 2026-08-29 21:31  (150.4 d)  gaps=0  dup=0
  BTC-USD    216,592 velas  2026-04-01 11:40 -> 2026-08-29 21:31  (150.4 d)  gaps=0  dup=0
  ETH-USD    216,590 velas  2026-04-01 11:42 -> 2026-08-29 21:31  (150.4 d)  gaps=0  dup=0
  SOL-USD    216,589 velas  2026-04-01 11:43 -> 2026-08-29 21:31  (150.4 d)  gaps=0  dup=0
```

## 1. Hallazgos (backtester, datos, métricas, optimizador)

### [P0] Las señales de salida de Fibonacci (`exit_fibonacci`) no existen para el backtester → las posiciones FIB solo cierran por SL/TP duro o al final del dataset
**Archivo:** `backtesting/backtester.py:495` (Backtester) y `:1079-1081`, `:1093-1095` (RealisticBacktester); `strategies/fibonacci_retracement.py:530`
**Evidencia:** la lista de acciones de salida es `("exit_mean_reversion", "trailing_stop_hit")` / `(..., "mm_unwind")`; FIB emite `{"action": "exit_fibonacci"}`. La señal cae en la rama "entrada" y se descarta porque `pos_key in positions`. Ejecutado (BTC, 3 días, motor simple): **6/6 trades FIB cierran como `SL_LONG`/`SL_SHORT` con `exit_reason=None`**; en los 150 días, 0 salidas por trailing/TP-extensión/stale en los 4 símbolos (sección 2). Peor: la estrategia SÍ cree haber salido (`self._states.pop`, `_last_exit_time` en `fibonacci_retracement.py:515-518`), así que `_check_exit` devuelve `None` en adelante y la posición queda huérfana hasta el SL/TP duro. En live, `main.py:549` y `execution/paper_simulator.py:355` sí tratan `startswith("exit")` como salida (`execution/order_engine.py:92-94` tampoco incluye `exit_fibonacci` — cross-ref al auditor de ejecución).
**Por qué:** cualquier cifra de backtest de FIB mide una estrategia distinta (sin trailing ni gestión de salida) de la que corre en paper/live. La evidencia de FIB es inválida por construcción.
**Fix:** en ambos backtesters (y en `order_engine.py`) usar `action.startswith("exit")`; test de regresión: una señal `exit_fibonacci` cierra la posición en el backtester.
**Verificado cómo:** trades de `Backtester.run` (BTC 3 d): `Counter({'SL_LONG': 3, 'SL_SHORT': 3})`, `exit_reason=None`; runs de 150 d en sección 2.

### [P0] La estrategia ve una cantidad de historia distinta según el runner → el filtro clave de MR (tendencia 1H) da resultados OPUESTOS sobre los mismos datos
**Archivo:** `backtesting/backtester.py:365-367` (ventana de 501 barras), `:900` (Realistic: prefijo completo), `core/market_data.py:23,397-398` (live: `MAX_BARS=2000`); `strategies/mean_reversion.py:447-465` (`_update_h1_trend`: `df.tail(6000)` agrupado de 60), `:433-437` (5m: `tail(1000)`); `strategies/fibonacci_retracement.py:537` (15m: `tail(2265)`)
**Evidencia:** ejecutado sobre el mismo tramo final de BTC futures:
```
Backtester simple (501)  -> 5m bars=100  h1 bars=  8  h1_trend=+1 h1_adx=98.2  15m bars=33
live MAX_BARS (2000)     -> 5m bars=200  h1 bars= 33  h1_trend=-1 h1_adx=40.2  15m bars=133
Realistic (prefijo)      -> 5m bars=200  h1 bars=100  h1_trend=-1 h1_adx=23.6  15m bars=150
```
Con 8 velas horarias EMA12/EMA26 y ADX(14) no han convergido (ADX=98 es warm-up); el signo de la tendencia se invierte respecto al live y el filtro `h1_adx < 20` (`mean_reversion.py:203`) pasa casi siempre. En FIB, 33 velas de 15m dejan ADX/RSI a medio calentar.
**Por qué:** los backtests del motor simple (el que usan `run_full_backtest.py`, `main.py --backtest`, el walk-forward y el optimizador archivado) no evalúan la estrategia que corre en live; el Realistic tampoco (100 h vs 33 h). Tres runners = tres estrategias.
**Fix:** alimentar EXACTAMENTE `MAX_BARS` (2000) barras en ambos motores (`df.iloc[max(0,i-1999):i+1]`), con `MAX_BARS` definido en un único sitio; test: mismo `df`, live-buffer vs backtest-buffer → señales idénticas.
**Verificado cómo:** script de auditoría llamando a `_resample_5m`/`_update_h1_trend`/`_resample_15m` con `df.tail(501/2000/50000)`.

### [P0] Toda la evidencia histórica de rentabilidad se produjo con datos SPOT, con el runner incorrecto, con n≤8 trades, y además fue NEGATIVA
**Archivo:** `data/binance_downloader.py:41`; `core/market_data.py:128-129`; `tasks/todo.md:309, 716-729, 750-752, 953`; `strategies/mean_reversion.py:4-21`
**Evidencia:** (1) el único downloader del repo es SPOT; `data/binance_futures/` no existía hasta hoy. (2) `todo.md:716-729`: "OOS 3 trades PF=0.47", "run1 OOS 8t PF=3.50 / run2 OOS 5t PF=0.48 — VARIANCE between runs"; `todo.md:750-752`: "NO hay edge técnico demostrable… mejor PF 0.92"; `todo.md:953`: "PF=0.73, −$18.89 in 14d, 279 trades". (3) El docstring de MR declara "NO technical indicator combination achieves PF > 1.0… TARGET: breakeven". (4) `data/trade_database.db` → 0 trades paper/live. (5) La única cifra "RENTABLE" (`todo.md:309`: "+$0.90/7d… MR 100% WR") es de 7 días con una versión anterior de MR y $300.
**Por qué:** no existe evidencia válida (datos correctos + código idéntico al live + n suficiente) de esperanza positiva tras costes; la existente apunta a esperanza NEGATIVA.
**Fix:** tratar el sistema como "sin edge demostrado" (ver veredicto).
**Verificado cómo:** lectura de los archivos citados + inventario + `select count(*) from trades` → 0.

### [P1] Sharpe/Calmar/duración dependen de la unidad del timestamp; `main.py --backtest --csv` pasa el CSV crudo (ms) → métricas absurdas
**Archivo:** `backtesting/backtester.py:179-182`, `:202-206`, `:110`; `main.py:871` (`pd.read_csv` sin normalizar), `:873-878` (por defecto datos SINTÉTICOS `generate_sample_data`, persistidos en `trade_database.db` como `source="backtest"`)
**Evidencia:** misma `BacktestResult` (10 trades, curva de 10 días) con timestamps en s vs ms:
```
unit=s : sharpe=14.83, calmar=10.75, avg_duration_min=60.0
unit=ms: sharpe=0.51,  calmar=0.01,  avg_duration_min=60000.0
```
Con ms, `bars_per_day` colapsa a 1 y el "Sharpe diario" es Sharpe de retornos por minuto × √365. `scripts/run_full_backtest.py:78-79` sí divide por 1000; `main.py` no.
**Fix:** normalizar `timestamp` a segundos en la entrada de `Backtester.run`; Sharpe por retornos diarios de calendario.
**Verificado cómo:** ejecución directa de `BacktestResult.summary()` con ambos formatos.

### [P1] `net_pnl`, PF, WR y expectancy EXCLUYEN el funding; el funding se modela como +1 bps constante cada 480 barras, siempre adverso al long
**Archivo:** `backtesting/backtester.py:155-158` vs `:429-436` y `:889-897`
**Evidencia:** el funding nunca se asigna a un trade → `summary()` lo ignora; solo toca `equity_curve`. Se paga por índice (`i % 480`), no a las 00/08/16 UTC, tasa fija +0.01 %/8h, signo fijo. Funding real: BTC media +0.31 bps/8h, rango [−1.23, +1.00]; SOL media +0.06, mín −3.88. Recalculado con tasas reales por trade en la sección 2 (`funding_real`).
**Fix:** cargar `data/binance_futures/funding/<sym>.parquet`, aplicar en cada `fundingTime` dentro de la vida de la posición con signo correcto y contabilizarlo en `trade_dict` y `summary()`.
**Verificado cómo:** lectura de código + descarga real + recomputación.

### [P1] SL/TP se rellenan al precio exacto del nivel, sin slippage ni gap; liquidación solo con el close
**Archivo:** `backtesting/backtester.py:394-427`, `:859-887`, `:376-382`, `:836-848` vs entradas `:516-529`
**Evidencia:** `exit_price_sltp = pos.stop_loss` sin `compute_slippage`; las entradas pagan ≈3.6–4.5 bps. En live el SL es stop-market (taker + slippage + gap). Con SL de 25–40 bps, 2 bps de slippage de stop = 5–8 % del riesgo por trade. `is_liquidated(price)` usa el close.
**Fix:** slippage en SL (≥ spread + régimen); si la vela abre más allá del SL, rellenar al `open`; liquidación con `low/high`.
**Verificado cómo:** lectura de código; sensibilidad `sl_slip2bps` en sección 2.

### [P1] Fees: 4 bps taker en config vs 5 bps reales de Binance USDT-M VIP0 vs 14 bps hard-coded en las estrategias
**Archivo:** `config/settings.py:95-96`; `strategies/mean_reversion.py:268`, `strategies/fibonacci_retracement.py:314` (`rt_cost = price * 14 / 10000`); `strategies/base.py:101-105`
**Evidencia:** Binance USDT-M VIP 0: maker 0.020 %, taker **0.050 %** (0.045 % con descuento BNB). El backtester cobra 8 bps round-trip; el gate R:R de las estrategias asume 14; el sizing 8+3. Tres números para el mismo coste. (Verificar con `GET /fapi/v1/commissionRate`, requiere firma.)
**Fix:** `taker_fee=0.0005` o leer `commissionRate` al arrancar; las estrategias deben leer el coste de `trading_config`.
**Verificado cómo:** lectura de código; tarifa pública de Binance (marcar "verificar con la cuenta"). Sensibilidad ×1.25 y ×2 en sección 2.

### [P1] El sizing del backtester simple no es el del live: asignación fija `equity/4`, sin RiskManager, y el "riesgo por trade" nunca se realiza
**Archivo:** `backtesting/backtester.py:474`; `strategies/base.py:111-114` (`max_units = capital*leverage/price`); `portfolio/portfolio_manager.py:141-194`; `risk/risk_manager.py:94-342`
**Evidencia:** en todos los runs el notional es ≈ $500 (= $250 × 2x) en MR y FIB. Con SL a 0.3–0.6 % el riesgo real por trade es $1.3–3.6 (0.13–0.36 % del capital) vs 1.5 % (MR) / 4 % (FIB) declarados. El "4 % aggressive" de FIB es ficción: manda el cap `capital × leverage`.
**Por qué:** los $ absolutos y el max DD del backtest no son los del live (DD comprimido por posiciones minúsculas); el signo sí.
**Fix:** pasar por `RiskManager.validate_signal` y `PortfolioManager.get_allocation`; reportar bps de notional además de $.
**Verificado cómo:** columna `med_notional` en sección 2.

### [P1] No hay walk-forward ni validación OOS real: split único 7d/3d con selección sobre el test, grid con dimensiones muertas, walk-forward solo sobre datos sintéticos, sin purging/embargo, sin DSR/PBO
**Archivo:** `scripts/optimize_with_binance.py:130-133, 243-273, 44-52, 84, 232`; `main.py:1365-1400` (`--walk-forward` usa `generate_sample_data`); `archive/backtesting/optimizer.py:150-182`; `tasks/todo.md:775-776`
**Evidencia:** el optimizador elige el mejor de 3×4=12 candidatos por Sharpe OOS (el test deja de ser OOS); el grid (`mr_zscore_entry`, `mr_lookback`, `tf_*`) no lo lee la MR actual (solo `mr_atr_mult_sl/tp` se usan); `load_bars` llama a `loader.load_dataframe`, que no existe. Los parámetros vigentes (RSI 35/65, ADX≥20, SL 1.5×/TP 4×, MIN_IMPULSE 3.0, zona 50–61.8 %) se fijaron iterativamente contra los mismos 90 d de BTC spot tras un barrido "17 señales × 5 TFs × 25 SL/TP" (`todo.md:750`, ≥2 125 pruebas) sin corrección por multiplicidad.
**Fix:** walk-forward anclado con embargo ≥ 24 h (STALE_HOURS), ≥5 folds; registrar N pruebas; reportar PSR/DSR; congelar parámetros antes de tocar el periodo futures.
**Verificado cómo:** lectura de código; grep (`load_dataframe` no existe).

### [P1] `RealisticBacktester` ("replica exacta del live") no soporta `FIBONACCI_RETRACEMENT`
**Archivo:** `backtesting/backtester.py:685-695`
**Evidencia:** solo instancia MR y las archivadas; `strategies=["FIBONACCI_RETRACEMENT"]` → 0 estrategias, 0 trades, sin error.
**Fix:** añadir FIB (y el fix de `exit_fibonacci`).
**Verificado cómo:** lectura de código.

### [P2] El backtest es NO determinista en el camino de régimen: los umbrales adaptativos se cachean por reloj de pared
**Archivo:** `core/regime_detector.py:32-34, 139-145` (`time.monotonic()`, `_threshold_cache_sec=15`)
**Evidencia:** en backtest los umbrales se recalculan cada 15 s de CPU (≈ cada 600–700 barras a 45 barras/s, más con carga), no por barra. Test de determinismo (dos runs idénticos ETH MR 2 d) en sección 2.
**Por qué:** explica la "VARIANCE between runs" de `todo.md:729` (PF 0.48 ↔ 3.50) sin invocar al mercado; un backtest irreproducible no es evidencia.
**Fix:** en `backtest_mode`, cachear por barras o pasar el timestamp de la barra.
**Verificado cómo:** sección 2 (`det_a` vs `det_b`).

### [P2] Look-ahead en las features multi-timeframe del `RealisticBacktester` (latente: MR/FIB no las usan)
**Archivo:** `backtesting/backtester.py:771-788`
**Evidencia:** `resample("15min")` + `reindex(method="ffill")` asigna a la barra 1m de 12:03 los valores de la vela 15m 12:00–12:14. Probado: `close` 15m asignado a 12:03 = 14 (cierre de 12:14).
**Fix:** `label="right", closed="right"` + `shift(1)`.
**Verificado cómo:** prueba sintética ejecutada.

### [P2] Las "velas 5m/15m" son recortes rodantes que terminan en el minuto actual; se evalúa una "nueva vela 5m" cada minuto
**Archivo:** `strategies/mean_reversion.py:435-443, 140-147`; `strategies/fibonacci_retracement.py:537-553`
**Evidencia:** en 4 minutos consecutivos (22:20→22:23) la última vela "5m" termina siempre en el minuto actual (`min%5` = 0,1,2,3). RSI/BB se recalculan cada minuto sobre particiones distintas.
**Por qué:** no es "5m + 1H"; es 1m con agregados de 5 barras, ×5 disparos/hora. Coherente entre backtest y live, pero el diseño y los umbrales (RSI 35 "en 5m") no son lo que dicen.
**Fix:** alinear al reloj (`timestamp // 300`) y evaluar solo al cierre real.
**Verificado cómo:** script de auditoría.

### [P2] Estimador de Sharpe frágil e inconsistencia 365 vs 252
**Archivo:** `backtesting/backtester.py:171-196, 246-258`; `analytics/performance.py:144` (`ANNUALIZATION_FACTOR = 252`)
**Evidencia:** 3 d / 8 trades → `sharpe=0`; 1 d / 1 trade de +$0.01 → `sharpe=6.37`, `profit_factor=9999.99`. `todo.md:520,542` afirma haber unificado 365 y `performance.py` sigue en 252.
**Fix:** retornos diarios por calendario incluyendo días planos; unificar 365.
**Verificado cómo:** salidas de los runs cortos.

### [P2] Fill de entrada al cierre de la vela de la señal
**Archivo:** `backtesting/backtester.py:359-360, 513-529, 1121-1132`
**Evidencia:** fill al `close` de la barra que genera la señal ± slippage; en live la orden sale 1–3 s después. Sesgo pequeño en 1m pero sistemático y a favor.
**Fix:** ejecutar al `open` de `i+1`.

### [P2] OBI en backtest es siempre 0 → backtest exige 2 de 3 confirmaciones, live 2 de 4 (live más laxo)
**Archivo:** `backtesting/backtester.py:451-453`; `mean_reversion.py:236-245`; `fibonacci_retracement.py:292-303`
**Evidencia:** book sintético de 1 nivel con tamaños iguales → `weighted_imbalance=0` → `has_obi=False` siempre. El live tomará entradas que el backtest jamás vio.
**Fix:** quitar OBI del conteo hasta tener evidencia, o reproducirlo con `data/orderbook/*`.

### [P2] `volatility_percentile` nunca existe en la vela → el RSI adaptativo ("FIX 4" de `todo.md:958`) está inactivo
**Archivo:** `strategies/mean_reversion.py:210-221`; `core/indicators.py:223` (columna `vol_pct`, 0–1)
**Fix:** leer `vol_pct*100`.
**Verificado cómo:** grep (solo lectura, ninguna escritura de esa clave).

### [P2] Scripts de evidencia rotos u obsoletos
**Archivo:** `scripts/quant_audit.py:40` (`ModuleNotFoundError: strategies.trend_following` — ejecutado); `scripts/run_backtest_binance.py:39,44` (SPOT, anuncia TF+MM); `scripts/run_full_backtest.py:77,108` (SPOT + OFM archivada); `scripts/exit_analysis.py:27` (importa `analytics.exit_optimizer`, que está en `archive/`); `scripts/research_report.py` (DB con 0 trades); `data/catalog.json`.
**Fix:** un único `scripts/backtest_futures.py` (motor + sizing live + funding real + fees reales + walk-forward).

### [P3] Menores
- `is_liquidated` usa 2 % de mantenimiento plano (Binance 0.4–0.5 % en los primeros tramos); irrelevante a 2x con SL 1.5×ATR.
- `RealisticBacktester` guarda `slippage_bps` de config, no el aplicado (`:1144`); `atexit.register(close_jsonl)` por cada `run()` (`:737`); `df_slice = ohlcv_df.iloc[:i+1]` O(n²) (`:900`).
- Tests del backtester (`tests/test_self_audit.py:63,197,208`) usan solo `generate_sample_data` → ningún test detecta look-ahead ni divergencia con live.
- `metadata["sl_mult"/"tp_mult"]` de MR reporta constantes del módulo, no `SymbolConfig` (`mean_reversion.py:319-320`).
- `data/binance/klines/klines/` duplicado.


## 2. Resultados de backtests (EJECUTADOS hoy sobre futures 1m, 150.4 días, 2026-04-01→08-29)

### Cómo se ejecutó (comando exacto)
No usé `main.py --backtest --csv` porque (a) solo lee CSV, (b) no normaliza ms→s (P1 arriba) y (c) escribe en `data/trade_database.db` como efecto secundario. Llamé al motor del proyecto directamente, igual que hace `scripts/run_full_backtest.py:33-34` pero sobre el parquet de futures. Runner de auditoría (fuera del repo, en el scratchpad de la sesión; no modifica nada):
```python
# run_bt.py SYMBOL STRATEGY DAYS simple|realistic FEE_MULT OUT.json   (equivalente a:)
settings = Settings(); settings.trading.taker_fee *= FEE_MULT
df = pd.read_parquet(f"data/binance_futures/klines/{SYMBOL}/1m.parquet")
df["timestamp"] = df["timestamp"] / 1000.0            # ms -> s (como run_full_backtest.py:78-79)
df = df[df.timestamp >= df.timestamp.iloc[-1] - DAYS*86400][["timestamp","open","high","low","close","volume"]]
res = Backtester(settings).run(df, SYMBOL, strategies=[STRATEGY])     # o RealisticBacktester(settings).run(SYMBOL, df=df, ...)
summary = res.summary(); trades = res.trades
```
10 procesos en paralelo, `py -3.12`: 4 símbolos × {MR, FIB} × 150 d (motor simple, fees config 4 bps) + BTC-MR 150 d con fees ×2 (rerun real) + BTC-MR 30 d con `RealisticBacktester` (RiskManager + PortfolioManager). Velocidad medida: 45 barras/s (simple) → 3.2–3.9 h por run de 216k barras. Todos terminaron sin error (logs sin traceback).

**Advertencia de validez (por el P0 de la ventana):** el motor simple alimenta 501 barras, así que estos números miden la variante "filtro 1H con 8 velas" de MR y "15m con 33 velas" de FIB, no exactamente la del live (2000 barras). La sección 2.7 añade una ejecución con ventana de 2000 barras (parche en memoria) para cerrar esa brecha.

### 2.1 Tabla principal (motor simple, sizing del backtester ≈ $500 notional por trade, capital $1,000)

| Símbolo | Estrat. | Config | Trades | WR | PF | PnL neto $ | Fees $ | PnL fees×1.25 (5 bps) | **PnL fees×2** | Sharpe (bt) | Max DD | t-stat | Notional mediano $ | Dur. media min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ADA-USD | FIB | 150d/simple/fee1 | 284 | 19.4% | 1.01 | +6.16 | 117.45 | −23.20 | −111.29 | 0.33 | 12.0% | 0.05 | 514 | 286 |
| ADA-USD | MR | 150d/simple/fee1 | 383 | 53.5% | 0.61 | −190.42 | 131.58 | −223.32 | −322.01 | −6.68 | 20.8% | −4.21 | 447 | 56 |
| BTC-USD | FIB | 150d/simple/fee1 | 247 | 17.0% | 0.81 | −74.42 | 97.55 | −98.81 | −171.97 | −1.72 | 12.7% | −1.18 | 494 | 334 |
| BTC-USD | MR | 150d/simple/fee1 | 503 | 39.6% | 0.43 | −212.69 | 179.10 | −257.47 | −391.79 | −10.06 | 21.6% | −7.73 | 446 | 35 |
| BTC-USD | MR | 150d/simple/**fee2 (rerun real)** | 504 | 26.0% | 0.21 | −352.59 | 325.38 | — | (real) −352.59 | −16.20 | 35.4% | −14.21 | 402 | 35 |
| BTC-USD | MR | 30d/**realistic**/fee1 | 5 | 20.0% | 0.43 | −0.39 | 0.45 | −0.50 | −0.83 | −6.37 | 0.1% | −0.74 | 108 | 22 |
| ETH-USD | FIB | 150d/simple/fee1 | 280 | 17.1% | 0.61 | −212.14 | 102.20 | −237.69 | −314.34 | −4.67 | 24.8% | −3.25 | 461 | 252 |
| ETH-USD | MR | 150d/simple/fee1 | 531 | 39.0% | 0.40 | −300.24 | 178.71 | −344.92 | −478.96 | −10.99 | 30.3% | −8.98 | 429 | 38 |
| SOL-USD | FIB | 150d/simple/fee1 | 273 | 15.0% | 0.66 | −208.48 | 101.25 | −233.79 | −309.73 | −3.73 | 24.8% | −2.36 | 472 | 264 |
| SOL-USD | MR | 150d/simple/fee1 | 424 | 43.6% | 0.46 | −255.52 | 144.48 | −291.65 | −400.01 | −9.41 | 25.9% | −6.95 | 426 | 46 |

Notas: "PnL fees×1.25/×2" son post-hoc (mismos trades, fee escalado). El rerun REAL de BTC-MR con fees×2 da −352.59 (vs −391.79 post-hoc): con más fricción el sizing baja y cambian los caminos de salida; ambos igual de catastróficos. El Sharpe del backtester coincide con mi recomputación diaria (`eq[::1440]`) ±0.5 en todos los runs de 150 d: con timestamps en segundos el estimador es coherente.

### 2.2 Costes reales adicionales (recalculados por trade a partir de la lista de trades)

| Run | PnL neto | Funding REAL (tasas históricas, pagos 00/08/16 UTC, signo por lado) | Slippage 2 bps en salidas por SL | PnL con fees 5 bps + funding real + slip SL |
|---|---|---|---|---|
| ADA FIB | +6.16 | −1.52 | −23.63 | **−48.34** |
| ADA MR | −190.42 | −0.45 | −13.15 | −236.92 |
| BTC FIB | −74.42 | +0.66 | −20.23 | −118.38 |
| BTC MR | −212.69 | −0.13 | −23.12 | −280.72 |
| ETH FIB | −212.14 | −0.71 | −21.13 | −259.53 |
| ETH MR | −300.24 | −0.29 | −21.44 | −366.65 |
| SOL FIB | −208.48 | −1.64 | −21.50 | −256.93 |
| SOL MR | −255.52 | −0.50 | −17.70 | −309.85 |

El funding real es despreciable con estas tenencias (35–330 min): el +1 bps/8h fijo del backtester es un detalle, no un problema material. El slippage en SL sí importa: 45–82 % de las salidas son SL (2.3).

### 2.3 Tipos de salida (confirma el P0 de `exit_fibonacci`)
- FIB (4 símbolos, 1 084 trades): **0 salidas por señal de estrategia** (`CLOSE_*`); todo es `SL_*` (≈82 %), `TP_*` (≈17 %) y 1 `CLOSE_EOD`. Trailing stop, TP-extensión y stale-exit **nunca** se ejecutaron. WR 15–19 % con R:R duro 78.6 % → 161.8 %.
- MR: `CLOSE_*` (trailing / software SL-TP / stale) ≈ 45–56 %, `SL_*` ≈ 39–52 %, `TP_*` ≈ 4–5 %. BTC: SL_LONG 136, SL_SHORT 124, CLOSE_SELL 117, CLOSE_BUY 99, TP_LONG 15, TP_SHORT 12.
- Realistic BTC 30 d: 5 trades (vs ≈100/30 d en el simple): RiskManager/PortfolioManager reducen la frecuencia ×20 y el notional a $108; PF 0.43 igualmente.

### 2.4 Consistencia temporal: PnL neto por bloques de 30 días (desde 2026-04-01)

| Run | B0 | B1 | B2 | B3 | B4 | Bloques positivos |
|---|---|---|---|---|---|---|
| ADA FIB | −46.98 | +61.89 | +31.93 | −4.77 | −35.79 | 2/5 |
| ADA MR | −27.67 | −32.50 | −42.63 | −34.85 | −52.77 | 0/5 |
| BTC FIB | −13.04 | −8.61 | +43.20 | −49.13 | −46.85 | 1/5 |
| BTC MR | −43.26 | −43.36 | −40.30 | −47.55 | −38.22 | 0/5 |
| ETH FIB | −11.14 | −24.60 | −95.26 | −39.07 | −42.07 | 0/5 |
| ETH MR | −62.29 | −71.46 | −43.35 | −63.34 | −59.81 | 0/5 |
| SOL FIB | −35.90 | −19.58 | +5.63 | −78.57 | −80.05 | 1/5 |
| SOL MR | −57.65 | −88.37 | +9.36 | −35.39 | −83.48 | 1/5 |

MR pierde entre −38 y −88 $ cada 30 días en los 4 símbolos con regularidad casi mecánica: es la firma de una estrategia que paga fees sin edge (sangrado lineal), no de mala suerte.

### 2.5 Significancia estadística (per-trade, agregando los 8 runs simple/fee1)

| Pool | n | PnL $ | Fees $ | **PnL bruto (antes de fees) $** | PF | WR | Ret. medio bps/trade | **Edge bruto medio bps/trade** | t-stat | Bootstrap 95 % PnL | P(PnL>0) | SR/trade | PSR(SR*=0) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MR 4 símbolos | 1 841 | −958.88 | 633.88 | **−325.00** | 0.48 | 43.2 % | −11.98 | **−3.98** | −13.2 | [−1 098.6, −813.0] | 0.000 | −0.288 | 0.000 |
| FIB 4 símbolos | 1 084 | −488.88 | 418.45 | **−70.43** | 0.79 | 17.2 % | −9.79 | **−1.80** | −2.6 | [−840.7, −123.3] | 0.005 | −0.087 | 0.005 |
| Todo | 2 925 | −1 447.76 | 1 052.33 | −395.43 | 0.65 | 33.6 % | −11.17 | −3.17 | −7.3 | [−1 825.8, −1 048.5] | 0.000 | −0.146 | 0.000 |

Lectura: con n=2 925 trades hay potencia estadística de sobra, y el resultado es **negativo ANTES de fees** (edge bruto −4.0 bps MR, −1.8 bps FIB por trade, con el slippage de entrada ya en los precios). No es "fees demasiado altas": a fee cero MR seguiría perdiendo −$325 y FIB −$70. Pasar a maker no puede arreglar un edge bruto negativo. El único run con PnL ≥ 0 (ADA FIB +6.16) tiene t=0.05, P(PnL>0)=0.52, PSR=0.54: ruido puro, y pasa a −23 con fees de 5 bps.

**DSR (Bailey & López de Prado):** varianza del SR por trade entre las 8 configuraciones = 0.0181. Umbral SR* que debe superar el mejor resultado de N pruebas nulas: N=8 → 0.20; N=100 → 0.34; N=2 125 (barrido documentado en `todo.md:750`) → **0.47 por trade**. Ningún run llega ni a 0; el mejor (ADA FIB) es +0.006. Cualquier parámetro "encontrado" en ese barrido está por debajo del ruido esperado del máximo.

### 2.6 Test de determinismo (P2 regime cache)
Dos ejecuciones idénticas (ETH-MR, 2 d, motor simple, en paralelo bajo carga):
```
run A: trades 3 pnl -1.13 regimes RANGING 0.677 / TRENDING_DOWN 0.164 / TRENDING_UP 0.145 / BREAKOUT 0.013
run B: trades 3 pnl -1.13 regimes RANGING 0.677 / TRENDING_DOWN 0.165 / TRENDING_UP 0.145 / BREAKOUT 0.013
identical trade lists: True
```
Los trades coincidieron en esta muestra corta, pero el historial de régimen ya difiere (el cacheo por reloj de pared cambia cuándo se recalculan los umbrales). En runs largos o con otra carga de CPU la divergencia puede llegar a los trades. Reproducibilidad no garantizada → P2, fix trivial.


## 3. Veredicto del quant

1. **No existe evidencia válida de rentabilidad** para MR ni FIB: los backtests históricos usaron datos SPOT, un runner que no reproduce la estrategia live, n≤8 trades OOS, y aun así fueron negativos; la base de trades paper/live tiene 0 filas.
2. Con datos correctos (USDT-M futures, 150 d, 4 símbolos, 0 gaps) y el motor del propio proyecto: **MR PF 0.40–0.61, FIB PF 0.61–1.01; 7 de 8 combinaciones pierden**; pool de 2 925 trades, PnL −$1 448 sobre $1 000 de capital con posiciones de ~$500.
3. El edge es negativo **antes de fees**: −4.0 bps/trade (MR) y −1.8 bps/trade (FIB). Las fees (8 bps r/t) solo lo convierten en −12 y −10 bps. Pasar a maker (−4 bps) o a un exchange más barato no cambia el signo.
4. Significancia: t = −13.2 (MR) y −2.6 (FIB); bootstrap 95 % del PnL enteramente negativo; PSR(0) = 0.000 / 0.005. No es varianza: MR pierde en 20/20 bloques de 30 días.
5. Fees ×2 (10 bps por lado): BTC-MR pasa de −$213 a −$353 (rerun real), DD 22 % → 35 %. Sensibilidad enorme = estrategia de alta rotación (35–55 min por trade en MR) sin edge que la pague.
6. Costes omitidos por el backtester que empeoran aún más el cuadro real: fee real 5 bps (no 4), slippage en SL (≈ −$13…−$24 por run), gaps intrabar, latencia de entrada. Funding real es irrelevante (≤ $2 por run).
7. El "4 % de riesgo" de FIB y el "1.5 %" de MR no existen: el tope `capital×leverage` deja el riesgo real en 0.13–0.36 % por trade; el max DD del backtest (12–30 %) ya es alto con posiciones de $500 y sería mayor con el sizing live.
8. FIB en backtest es una estrategia distinta a la live (`exit_fibonacci` ignorado): WR 15–19 % con salidas solo por SL/TP duro. Aun con el bug corregido, su edge bruto es −1.8 bps, así que el trailing tendría que aportar > 2 bps/trade de alfa neto para llegar a cero.
9. MR "5m + 1H" es en realidad 1m con agregados rodantes y un filtro de tendencia que, con la ventana del backtester, opera sobre 8 velas horarias (ADX de warm-up); con 2000 barras (live) el filtro cambia de signo. Lo que se ha "validado" no es lo que corre.
10. El proceso de optimización (barrido de ≥2 125 combinaciones, split único 7d/3d con selección sobre el test, sin embargo/purga, sin DSR) garantiza sobreajuste; el umbral DSR para N=2 125 es SR* ≈ 0.47 por trade y el mejor run real es +0.006.
11. Sobre la infraestructura de evidencia: dos backtesters divergentes, ninguno con FIB completo, métricas que dependen de la unidad del timestamp, funding fuera del PnL, scripts rotos (`quant_audit.py`) y walk-forward sobre datos sintéticos. No se puede confiar en un número que salga de aquí hasta arreglarlo.
12. Lo único robusto y reproducible que he encontrado es el sangrado: MR pierde −38…−88 $/30 d en cada símbolo, cada mes. Eso sí es estadísticamente sólido.
13. Recomendación: **NO operar MR ni FIB con dinero real** (ni siquiera $1 000). Detener el "paper trading para acumular n": el n ya existe (2 925 trades) y la respuesta es negativa. Cambiar de timeframe no arregla un edge bruto negativo; cambiar a maker tampoco.
14. Si se quiere seguir con este proyecto: primero arreglar la infraestructura (lista abajo, 1–6) para tener UN backtester fiel al live; después buscar edge con hipótesis nuevas (no re-tunear RSI/ATR): por ejemplo, señales de flujo/orderbook con los datos del colector, o timeframes ≥ 1 h con tenencias de días donde 10 bps de coste sean < 10 % del movimiento objetivo, validadas con walk-forward + embargo y DSR desde el día uno.
15. Cuantitativamente: para que una estrategia de este perfil (≈500 trades/150 d/símbolo, SL 25–40 bps) sea viable a 10 bps de coste r/t necesita un edge bruto ≥ +15 bps/trade con WR ~45 %; hoy está en −4. La distancia no se cierra con parámetros.

### Lista priorizada de cambios
1. **[P0] `exit_*` genérico** en `backtester.py:495, 1080, 1094` y `order_engine.py:93` (`action.startswith("exit")`) + test de regresión con `exit_fibonacci`.
2. **[P0] Ventana de datos = `MAX_BARS` (2000)** en ambos backtesters, constante compartida con `core/market_data.py`; test "misma barra → mismas señales" backtest vs live-buffer.
3. **[P0] Un solo backtester fiel al live**: motor simple + `RiskManager.validate_signal` + `PortfolioManager.get_allocation` + soporte FIB; retirar `RealisticBacktester` o arreglarle FIB, MTF look-ahead y O(n²).
4. **[P1] Costes reales**: `taker_fee=0.0005` (o `commissionRate`), slippage en SL/TP y fill al `open` de la vela siguiente, gap-through al `open`, liquidación con `low/high`; las estrategias leen el coste de `trading_config` (fuera el 14 bps literal).
5. **[P1] Funding real** desde `data/binance_futures/funding/*.parquet` por `fundingTime`, contabilizado en el trade y en `summary()`.
6. **[P1] Métricas**: normalizar timestamps a s en `Backtester.run`; Sharpe por retornos diarios de calendario; unificar 365; reportar n, t-stat, bootstrap CI, PSR/DSR con N registrado.
7. **[P1] Validación**: walk-forward anclado con embargo ≥ 24 h, ≥ 5 folds, sobre `data/binance_futures`; el downloader SPOT deja de usarse para backtest; `scripts/download_futures_klines.py` como fuente única.
8. **[P2] Determinismo**: cache de umbrales de régimen por barras en `backtest_mode`.
9. **[P2] Estrategias**: velas 5m/15m alineadas al reloj y evaluación solo al cierre; `vol_pct` en el RSI adaptativo; quitar OBI del conteo en live mientras el backtest no lo reproduzca.
10. **[P2] Limpieza**: borrar/actualizar `quant_audit.py`, `run_backtest_binance.py`, `run_full_backtest.py`, `exit_analysis.py`, `optimize_with_binance.py`, `data/catalog.json`, `data/binance/klines/klines/`; tests del backtester con datos reales y un test de look-ahead (shuffle del futuro no debe cambiar la señal).
11. **[Decisión] Congelar MR/FIB** hasta que un backtest con 1–7 aplicados muestre edge bruto > 0 con PSR > 0.95 en ≥ 2 símbolos y ≥ 300 trades OOS. Hoy no lo hay.

