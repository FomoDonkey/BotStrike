# R2 — Paridad backtest ↔ live (`backtest_parity`)

**Fecha:** 2026-08-31 · **Auditor:** agente quant R2 (dominio: paridad backtester ↔ loop live)
**Alcance:** `backtesting/backtester.py` (`Backtester` y `RealisticBacktester`), `main.py` (`_process_symbol`, callbacks WS, `run_backtest*`), `core/market_data.py`, `core/historical_data.py`, `execution/paper_simulator.py`, `execution/order_engine.py`, `risk/risk_manager.py`, `portfolio/portfolio_manager.py`, `strategies/{mean_reversion,fibonacci_retracement}.py`, `scripts/*`.
**Método:** lectura línea a línea + experimento de paridad ejecutado con `py -3.12` sobre datos reales de BTC-USD futures 1m (`data/binance_futures/klines/BTC-USD/1m.parquet`, 216 592 velas, 0 gaps, 0 dups — verificado). Nada se afirma sin verificar.
**Referencias ronda 1:** `04-*` = `tasks/audit/04_backtest_quant_evidence.md`; `01-*` = `tasks/audit/01_core_strategy_risk.md`.

---

## 0. Estado de los fixes de ronda 1 que tocan esta área

| Hallazgo R1 | Qué se arregló | Estado hoy |
|---|---|---|
| 04-P0 «`exit_fibonacci` no existe en backtest» | Solo se arregló el lado **live**: `OrderExecutionEngine.is_exit_signal` (`execution/order_engine.py:79-89`) y `paper_simulator._execute_one` (`:394-398`) ya usan `action.startswith("exit")`. | **SIGUE ABIERTO en los dos backtesters** (`backtester.py:495`, `:1079-1081`, `:1093-1095`). Verificado ejecutando (ver `BTP-01`). El fix parcial **empeora la paridad**: antes ambos lados ignoraban `exit_fibonacci`; ahora live sí sale y backtest no. |
| 04-P0 «ventana 501 vs 2000» | Nada. | **SIGUE ABIERTO** (`backtester.py:366`: `window_start = max(0, i - 500)`). Verificado con experimento de señales (`BTP-02`). |
| 04-P1 «`RealisticBacktester` no soporta FIB» | Nada. | **SIGUE ABIERTO** (`backtester.py:686-695`, solo MR + archivadas). |
| 04-P1 «sizing del backtester ≠ live» | Nada en `Backtester`. | **SIGUE ABIERTO** (`backtester.py:474`). |
| 04-P1 «`main.py --backtest --csv` no normaliza ms→s» | Nada. | **SIGUE ABIERTO** (`main.py:942`). |
| 04-P2 «regime cache por reloj de pared» | Nada. | **SIGUE ABIERTO** (`core/regime_detector.py:141` `_time.monotonic()`). |
| 04-P2 «look-ahead MTF en Realistic» | Nada. | **SIGUE ABIERTO** (`backtester.py:771-788`). |
| — Fase 0 quant (fb073a1): FIB congelada en 3 puertas live | `config/settings.py`, `portfolio_manager.REGIME_WEIGHTS`, `SYMBOL_STRATEGY_MAP` | **No propagado al backtester**: el `Backtester` no consulta ninguna de las 3 puertas → ver `BTP-05`. |

---

## 1. Tabla de paridad exhaustiva (ciclo live vs los dos backtesters)

Leyenda: **=** paridad real · **≈** aproximación aceptable · **✗** divergencia material.

| # | Paso del ciclo | LIVE / PAPER (archivo:línea) | `Backtester` (simple) | `RealisticBacktester` | Vered. |
|---|---|---|---|---|---|
| 1 | Fuente de precio | WS aggTrade `on_market_trade` → `market_data.on_trade` (`main.py:290`, `core/market_data.py:320`); seed inicial REST fapi klines 6 h (`market_data.py:117-182`) | parquet de klines fapi 1m | ídem | ✗ (barras live = ticks; backtest = klines oficiales) |
| 2 | Construcción de barra 1m | `_close_bar` desde ticks; **se dispara solo si llega un tick**; O/H/L/C de precios de trade; `timestamp = last_bar + 60` (**hora de CIERRE**) (`market_data.py:339-411`) | kline con `timestamp` = hora de **APERTURA** | ídem | ✗ (`BTP-06`) |
| 3 | Minutos sin trades | no se añade fila → **hueco silencioso** en el df (`market_data.py:375-378`) | serie contigua, 0 gaps | ídem | ✗ |
| 4 | Buffer | `MAX_BARS = 2000` (`market_data.py:23,397-398`) | `df.iloc[max(0,i-500):i+1]` = **501** (`backtester.py:366`) | prefijo completo `df.iloc[:i+1]` (`:900`), hasta 216 k | ✗ (`BTP-02`) |
| 5 | Indicadores 1m | `Indicators.compute_all` sobre ≤2000 filas en cada cierre de barra (`market_data.py:402-406`) | `compute_all` **una vez sobre todo el df** (`:349`) | ídem (`:757`) | **=** (verificado: idénticos a 15 decimales con 501 / 2000 / 50 000 barras — los recursivos ya han convergido; ver §2.3) |
| 6 | Resample 5m (MR) | `df.tail(1000)` → 200 velas 5m (`mean_reversion.py:434-445`) | `df.tail(1000)` sobre 501 → **100** velas 5m | 200 velas 5m | ✗ |
| 7 | Filtro tendencia 1H (MR) | `df.tail(6000)` sobre 2000 → **33** velas 1H (`mean_reversion.py:454-463`) | sobre 501 → **8** velas 1H (ADX/EMA26 en warm-up) | 100 velas 1H | ✗ (`BTP-02`) |
| 8 | Resample 15m (FIB) | `tail(2265)` sobre 2000 → 133 velas 15m (`fibonacci_retracement.py:537`) | sobre 501 → 33 velas 15m | FIB **no existe** | ✗ |
| 9 | Régimen | `regime_detector.detect(df_completo)` (`main.py:454`) sobre ≤2000 | sobre 501 (`:370`) | sobre prefijo completo (`:901`) | ✗ |
| 9b | Umbrales de régimen | cache 15 s de **reloj de pared** (`regime_detector.py:141`) — en live ≈ correcto | en backtest cachea por segundos de CPU → **no determinista** | ídem | ✗ (04-P2) |
| 10 | Microestructura | `on_trade` por tick real (VPIN/Hawkes/Kyle) (`main.py:290-316`) | `micro_engine.on_bar` sintético (`:456`) | `on_trade` si hay `bars_with_trades`, si no `on_bar` (`:820-833`) | ≈ / ✗ |
| 11 | Orderbook / OBI | libro real de 20 niveles vía WS depth | 1 nivel sintético, tamaños iguales → `weighted_imbalance = 0` **siempre** (`:452-453`) | ídem salvo `orderbook_df` (`:922-933`) | ✗ (04-P2) |
| 12 | Puerta 1: `should_activate(regime)` | sí (`main.py:535`) | sí (`:468`) | sí (`:969`) | = |
| 13 | Puerta 2: `should_strategy_trade(..., symbol=)` | sí **con `symbol=`** → aplica `SYMBOL_STRATEGY_MAP` (`main.py:536-538`) | **NO EXISTE** | sí **sin `symbol=`** (`:971-973`) → ignora el mapa | ✗ (`BTP-05`) |
| 14 | Gestión de posición con entradas bloqueadas | `if current_pos is None and not entries_allowed: continue` → **los exits SIEMPRE corren** (`main.py:534-541, 557-558`) | `continue` seco → **posición huérfana** | `continue` seco → **posición huérfana** | ✗ (`BTP-04`) |
| 15 | Capital asignado | `portfolio_manager.get_allocation()` (regime × perf × dd × risk-parity × 1/N) (`main.py:545`) | `equity / n_strats / n_symbols` fijo (`:474`) | `get_allocation()` (`:976`) | ✗ / = |
| 16 | Kelly | `kelly_risk_pct=get_kelly_risk_pct()` (`main.py:549,555`) | **no se pasa** (`:483-486`) | **no se pasa** (`:1002-1006`) | ✗ (`BTP-03`) |
| 17 | `mtf=` | **no se pasa** | **no se pasa** | **sí se pasa** (`:1002-1006`) | ✗ |
| 18 | Sizing en estrategia | `_calc_position_size` con `risk_pct` de Kelly (`base.py:83-114`) | con `risk_per_trade_pct` por defecto | ídem | ≈ |
| 19 | `risk_manager.validate_signal` | sí (`main.py:577`) | **NO SE LLAMA** | sí (`:1063`) | ✗ |
| 20 | Enriquecido de metadata pre-fill | `regime`, `microprice`, `mid_price`, `spread_bps`, `book_depth_usd`, `kyle_lambda_bps` (`main.py:592-606`) | nada | `vpin`, `hawkes`, `rsi`… (otro conjunto) (`:1010-1028`) | ✗ |
| 21 | Router de orden | `SmartOrderRouter.route()` → MARKET **o LIMIT** (`paper_simulator.py:468-484`) | siempre MARKET implícito | siempre MARKET implícito | ✗ (`BTP-07`) |
| 22 | Probabilidad de fill | LIMIT: `random.random() > fill_probability` → **la señal se descarta** (`paper_simulator.py:494-501`) | 100 % de fills | 100 % de fills | ✗ (`BTP-07`) |
| 23 | Precio de entrada | `signal.entry_price` = `snapshot.price` (tick/mid actual) ± slippage; LIMIT usa `routing.limit_price` | `close` de la barra de la señal ± slippage (`:527-529`) | ídem (`:1132`) | ✗ |
| 24 | Slippage de entrada | `compute_slippage(base, book_depth, hawkes, atr)` + impacto Kyle √(size/depth) (`paper_simulator.py:503-529`) | `compute_slippage(base, regime, atr)` sin depth/hawkes/Kyle (`:516-520`) | + `hawkes_ratio`, sin depth/Kyle (`:1126-1131`) | ✗ |
| 25 | Fee de entrada | MARKET→taker, **LIMIT→maker** (`paper_simulator.py:550-551`) | siempre taker (`:424`) | taker salvo MM (`:1135-1137`) | ✗ |
| 26 | Momento del fill | ≤ `strategy_interval_sec` = 3 s después del cierre de barra | instantáneo al `close` de la barra i | ídem | ≈ (sesgo a favor) |
| 27 | Disparo de SL/TP | **cada tick** (`main.py:308` → `on_price_update`), high/low acumulados entre ticks | 1 vez por barra con `high`/`low` de la barra (`:394-422`) | ídem (`:859-876`) | ≈ |
| 28 | Prioridad SL vs TP en la misma barra | orden temporal real de los ticks | «el más cercano al `open` gana» (`:397-404`) | **SL siempre primero** (`elif`, `:859-876`) | ✗ (los 2 motores difieren entre sí) |
| 29 | Precio de fill del SL | `stop_loss ∓ 1.5 × slippage_bps` (`paper_simulator.py:271-279`) | **`stop_loss` exacto, 0 slippage** (`:424`) | ídem (`:878`) | ✗ (`BTP-08`) |
| 30 | Gap-through del SL | n/a (tick a tick) | rellena en el nivel aunque la vela **abra** más allá | ídem | ✗ (04-P1) |
| 31 | Precio de fill del TP | `take_profit` exacto (limit) | `take_profit` exacto | ídem | = |
| 32 | Fill de exit por señal | `entry_price ∓ 0.5 × slippage_bps` (`paper_simulator.py:407-412`) | `compute_slippage` completo ×1.0 (`:499-503`) | ídem (`:1101-1105`) | ✗ |
| 33 | Fee de salida | taker (`paper_simulator.py:413`) | taker | taker | = |
| 34 | Reconocimiento de exit | `startswith("exit")` OR `trailing_stop_hit`/`mm_unwind` OR `exit_reason` (`order_engine.py:84-89`, `paper_simulator.py:394-398`) | lista blanca `("exit_mean_reversion","trailing_stop_hit")` (`:495`) | `("exit_mean_reversion","trailing_stop_hit","mm_unwind")` (`:1093-1095`) | ✗ (`BTP-01`) |
| 35 | Funding | `validate_signal(funding_rate=...)` real del snapshot; **el coste no se cobra nunca** en paper | +1 bps fijo cada 480 **barras de índice**, siempre contra el long (`:430-436`); fuera de `summary()` | ídem (`:890-897`) | ✗ (04-P1) |
| 36 | Liquidación | exchange (paper: no existe) | `is_liquidated(close)`, mantenimiento 2 % plano (`:116-128, 378`) | ídem (`:838`) | ≈ |
| 37 | Notificación de exit externo a la estrategia | `notify_external_exit` en SL/TP (`main.py:634-638`) → resetea cooldown y `_states` | **no se llama** → la estrategia cree que sigue dentro | **no se llama** | ✗ (`BTP-09`) |
| 38 | Registro | `trading_logger` + `metrics` + `TradeRepository` (source `paper`) | `result.trades` en memoria; `main.py:959-965` lo persiste como `source="backtest"` | + JSONL | ≈ |
| 39 | Pausas temporizadas de riesgo | `time.time()` (pérdidas consecutivas, circuit breaker) — correcto en live | n/a (no hay RiskManager) | **`time.time()` de pared, no tiempo de barra** (`risk_manager.py:187,224`) | ✗ (`BTP-10`) |

**Recuento:** de 39 pasos, **26 divergen materialmente** en al menos un motor.

---

## 2. Experimento de paridad (EJECUTADO)

### 2.1 Diseño
Mismos datos reales (BTC-USD futures 1m, últimos 6 820 minutos = 2 500 de warm-up + 3 días: `2026-08-25 03:52 → 2026-08-29 21:31` UTC), mismos indicadores, mismo `RegimeDetector`, misma `MeanReversionStrategy` (`backtest_mode=True`), `current_position=None` siempre (para aislar **generación de señal** de la gestión de posición). Única variable: **la ventana de datos que ve la estrategia**.

* **A** = ruta del `Backtester`: `df.iloc[i-500:i+1]` (501 barras) — `backtester.py:366`.
* **B** = ruta live: `df.iloc[i-1999:i+1]` (2000 barras = `MAX_BARS`) — `core/market_data.py:23`.
* **C** = ruta live + `kelly_risk_pct=0.015` explícito (el kwarg que `main.py:555` pasa y los backtesters no).

### 2.2 Resultado (salida real)

```
bars=6820  2026-08-25 03:52:00 -> 2026-08-29 21:31:00

=== A/BACKTESTER df.iloc[i-500:i+1] (window=501) -> 24 entry signals
   diag {'i': 2880, 'm5': 100, 'h1':  1, 'h1adx': 89.4}
   diag {'i': 3600, 'm5': 100, 'h1':  1, 'h1adx': 72.2}
   diag {'i': 4320, 'm5': 100, 'h1':  1, 'h1adx': 90.2}
   diag {'i': 5040, 'm5': 100, 'h1': -1, 'h1adx': 81.4}
   diag {'i': 5760, 'm5': 100, 'h1':  1, 'h1adx': 88.7}
   diag {'i': 6480, 'm5': 100, 'h1':  1, 'h1adx': 77.1}

=== B/LIVE MAX_BARS=2000 (window=2000) -> 36 entry signals
   diag {'i': 2880, 'm5': 200, 'h1':  1, 'h1adx': 28.5}
   diag {'i': 3600, 'm5': 200, 'h1':  1, 'h1adx': 38.0}
   diag {'i': 4320, 'm5': 200, 'h1':  1, 'h1adx': 43.5}
   diag {'i': 5040, 'm5': 200, 'h1': -1, 'h1adx': 29.7}
   diag {'i': 5760, 'm5': 200, 'h1': -1, 'h1adx': 46.8}
   diag {'i': 6480, 'm5': 200, 'h1': -1, 'h1adx': 54.4}

COUNTS  A(bt,win=501)=24  B(live,win=2000)=36  C(live+kelly)=36
A vs B: common=18  only-A=6  only-B=18
B vs C: common=36  only-B=0  only-C=0  identical_rows=True
only-A idx: [3989, 4147, 4154, 4764, 5766, 5768]
only-B idx: [2637, 2643, 2646, 4620, 4623, 4624, 4633, 4634, 4645,
             4658, 4659, 6417, 6418, 6419, 6426, 6427, 6428, 6688]
h1adx A: min=80.0 max=95.1 median=85.2
h1adx B: min=33.5 max=65.8 median=43.9
```

**Lectura (3 días, 1 símbolo, solo MR, solo entradas):**

| Métrica | Valor |
|---|---|
| Señales que genera el backtester | 24 |
| Señales que genera el live | 36 |
| Comunes | **18** |
| **Solapamiento (Jaccard)** | **18 / 42 = 42.9 %** |
| Señales live que el backtest NUNCA ve | 18 / 36 = **50 %** |
| Señales del backtest que en live NO existen | 6 / 24 = **25 %** |
| `h1_adx` en el momento de la señal (mediana) | **85.2** (backtest) vs **43.9** (live) |
| Signo del filtro 1H en las 6 muestras de diagnóstico | **discrepa en 2 de 6 (33 %)** — `i=5760` y `i=6480`: backtest `+1`, live `−1` |

**Causa:** una sola línea. `backtester.py:366` (`i-500`) alimenta 501 barras; con `mean_reversion._update_h1_trend` (`tail(6000)` → `//60`) eso son **8 velas horarias**, sobre las que `ADX(14)` y `EMA(26)` no han convergido (de ahí el `h1_adx` 80–95, valores imposibles en 1H real). El live, con 2000 barras, obtiene 33 velas 1H y `h1_adx` 33–66. El filtro de tendencia 1H **es la puerta principal de MR** (`mean_reversion.py:203`: `if h1_trend == 0 or h1_adx < ADX_MIN_TREND: return`), así que un ADX inflado la abre casi siempre y con el signo equivocado un tercio de las veces.

**`kelly_risk_pct`:** B y C son fila a fila idénticos → hoy ese kwarg es **inerte** (con la DB vacía `get_kelly_risk_pct` cae a `risk_per_trade_pct`, `risk_manager.py:525-528`). Es una divergencia **latente**, no activa: en cuanto haya Kelly con datos, el sizing live dejará de coincidir con el del backtest. Lo reporto como P2, no como P0.

### 2.3 Control: los indicadores de 1m NO son la causa

Comprobado (mismo último minuto, tres tamaños de historia):

```
indicador(1m)      full(50k)     live(2000)        bt(501)      d2000%     d501%
atr                13.101242      13.101242      13.101242      0.000%    0.000%
rsi                48.132021      48.132021      48.132021      0.000%    0.000%
adx                21.199160      21.199160      21.199160      0.000%    0.000%
ema_26          78098.094248   78098.094248   78098.094248      0.000%    0.000%
zscore             -0.781620      -0.781620      -0.781620     -0.000%   -0.000%
vol_pct             0.171717       0.171717       0.171717      0.000%    0.000%
```

Los indicadores de 1m son idénticos con 501, 2000 o 50 000 barras: los recursivos ya han convergido. **Toda la divergencia viene de las series remuestreadas** (5m: 100 vs 200 velas; 1H: 8 vs 33 velas; 15m: 33 vs 133). Es honesto decir que el problema NO es "el backtest calcula mal los indicadores", sino "el backtest no tiene historia suficiente para el multi-timeframe".

---

## 3. Hallazgos

### [P0] BTP-01 — `exit_fibonacci` sigue ignorado por los dos backtesters; el fix de ronda 1 solo tocó el lado live y **aumentó** la brecha
**Archivo:** `backtesting/backtester.py:495` (y `:1079-1081`, `:1093-1095`)
**Evidencia:**
```python
# backtester.py:494-495  (Backtester)
                # Señal de salida
                if signal.metadata.get("action") in ("exit_mean_reversion", "trailing_stop_hit"):
# backtester.py:1093-1095 (RealisticBacktester)
                is_exit = signal.metadata.get("action") in (
                    "exit_mean_reversion", "trailing_stop_hit", "mm_unwind"
                )
```
frente al live, que **sí** se arregló en b3dbf75:
```python
# execution/order_engine.py:84-89
        action = str(signal.metadata.get("action", "") or "")
        return (action.startswith("exit")
                or action in ("trailing_stop_hit", "mm_unwind")
                or signal.metadata.get("exit_reason") is not None)
```
FIB emite `{"action": "exit_fibonacci"}` (`strategies/fibonacci_retracement.py:530`). Ejecutado hoy (BTC futures, 5 días, motor simple):
```
FIB trades: 10
exit sides: Counter({'SL_SHORT': 5, 'SL_LONG': 4, 'TP_SHORT': 1})
signals gen/exec: 13 10
any CLOSE_ (strategy exit)?: False
```
13 señales generadas, 10 ejecutadas: las 3 de salida caen en la rama de *entrada* y se descartan por `pos_key in positions`. **0 salidas por estrategia.**
**Por qué:** `04-P0` sigue abierto donde importa. Peor: antes del fix ambos lados ignoraban `exit_fibonacci`, así que al menos eran consistentes; hoy **live cierra por señal y el backtest no**, así que la divergencia es mayor que en la ronda 1. Además la estrategia hace `self._states.pop()` y fija `_last_exit_time` al emitir la señal (`fibonacci_retracement.py:516-520`), así que en el backtest la posición queda **huérfana** hasta el SL/TP duro y `_check_exit` ya nunca devuelve nada.
**Fix:** extraer `is_exit_signal` a un único sitio (p. ej. `core/types.py` o `execution/order_engine.py`) e importarlo desde `backtester.py:495` y `:1093`. Test de regresión: una señal `exit_fibonacci` cierra la posición en ambos motores.
**Verificado cómo:** lectura + ejecución de `Backtester.run(df_btc_5d, "BTC-USD", strategies=["FIBONACCI_RETRACEMENT"])` con `py -3.12` (salida pegada arriba).

---

### [P0] BTP-02 — La ventana de 501 barras del `Backtester` produce un **42.9 %** de solapamiento de señales con el live, y el filtro 1H se invierte
**Archivo:** `backtesting/backtester.py:366`
**Evidencia:**
```python
            # Windowed slice: last 500 bars (avoids O(n^2) full-prefix copy)
            window_start = max(0, i - 500)
            df_slice = df.iloc[window_start:i + 1]
```
vs `core/market_data.py:23` `MAX_BARS = 2000` y `:397-398`. Experimento §2.2 (3 días reales de BTC, MR, solo entradas):
`A(501)=24` señales · `B(2000)=36` · comunes `18` · **Jaccard 42.9 %** · `h1_adx` mediana `85.2` vs `43.9` · signo del filtro 1H opuesto en 2 de 6 muestras.
**Por qué:** este es el P0 declarado del proyecto y **sigue abierto** (04-P0 #2). Cualquier PF/Sharpe/WR que salga del `Backtester` mide una estrategia cuyo filtro principal opera sobre 8 velas horarias en warm-up. No es "un poco distinto": la mitad de las operaciones que el live tomaría no aparecen en el backtest.
**Fix:** una constante compartida. `from core.market_data import MAX_BARS` y `window_start = max(0, i - MAX_BARS + 1)` en `backtester.py:366`; en `RealisticBacktester` sustituir `ohlcv_df.iloc[:i+1]` (`:900`) por `ohlcv_df.iloc[max(0, i-MAX_BARS+1):i+1]` (arregla de paso el O(n²)). Test: mismo `df`, buffer-live vs buffer-backtest → listas de señales idénticas.
**Verificado cómo:** script de paridad ejecutado con `py -3.12` (salida completa en §2.2).

---

### [P0] BTP-03 — El único backtest que un usuario puede lanzar (UI/desktop → `POST /api/backtest/run`) usa datos **SPOT de hace 5 meses**, el motor de 501 barras, y sus filtros de fecha están rotos
**Archivo:** `server/bridge.py:1559-1576`
**Evidencia:**
```python
        data_dir = os.path.join(..., "data", "binance", "klines")     # SPOT
        parquet_path = os.path.join(data_dir, symbol, "1m.parquet")
        ...
        df = pd.read_parquet(parquet_path)
        if start_date:
            df = df[df["timestamp"] >= pd.Timestamp(start_date).timestamp()]
        if end_date:
            df = df[df["timestamp"] <= pd.Timestamp(end_date).timestamp()]
```
El parquet guarda `timestamp` en **milisegundos**; `pd.Timestamp(...).timestamp()` devuelve **segundos**. Comprobado:
```
cols ['timestamp','open','high','low','close','volume','close_time',...]
ts head [1766976480000, 1766976540000]  dtype int64   unit = ms
range 2025-12-29 02:48:00 -> 2026-04-03 06:45:00   rows 137038
start_date=2026-04-01 -> 1775001600.0
  df[ts >= that] rows = 137038 of 137038      <-- filtro INOPERANTE
  end_date=2026-04-02 -> df[ts <= that] rows = 0   <-- backtest SIEMPRE vacio
```
Es decir: cualquier `start_date` se ignora en silencio y **cualquier `end_date` hace que el endpoint devuelva `{"error": "Insufficient data: 0 bars (need 100+)"}`**. Y como los ms llegan tal cual a `BacktestResult.summary()`, las métricas se destruyen. Ejecución **real** por la ruta exacta del bridge (mismo parquet SPOT, `bars=6000`, `strategies=["MEAN_REVERSION"]`, única diferencia la unidad del timestamp):
```
BRIDGE (ms, tal cual)    trades=20  pnl=-9.72  sharpe= -0.27  sortino= -0.12  calmar= -0.07  avg_dur_min=31750.0
correcto (s)             trades=20  pnl=-8.86  sharpe=-15.97  sortino=-22.80  calmar=-68.03  avg_dur_min=   31.6
```
El UI muestra **Sharpe −0.27** ("casi plano, algo negativo") cuando la cifra real es **−15.97**: un factor **59×**, y encima el signo de gravedad queda camuflado. La duración media pasa de 31.6 min a **31 750 min (22 días)**. El PnL difiere ligeramente (−9.72 vs −8.86) porque el cooldown de MR en `backtest_mode` sí convierte ms→s (`mean_reversion.py:174-176`), así que la lista de trades tampoco es la misma.
Además `data/binance/klines/` es SPOT (`data/binance_downloader.py:41`) y termina el **2026-04-03**, mientras el bot opera USDT-M futures (`core/market_data.py:129`, `fapi`).
**Por qué:** es la superficie por la que el usuario "valida" una estrategia. Devuelve un Sharpe 30× menor de lo real, sobre un mercado distinto, con datos de hace 5 meses, y el selector de fechas no funciona. `04-P1` (unidad de timestamp) sigue abierto y ahora tiene consecuencias de producto.
**Fix:** en `_run_backtest_sync`, apuntar a `data/binance_futures/klines/`, normalizar `df["timestamp"] = df["timestamp"]/1000.0` cuando la mediana > 1e12 **antes** de filtrar, y comparar con `pd.Timestamp(start_date).timestamp()` ya en segundos. Idem `main.py:942`.
**Verificado cómo:** lectura del endpoint + `py -3.12` sobre el parquet real (filtros y conteos pegados arriba) + **ejecución real de `Backtester.run` por la ruta del bridge** con el mismo parquet en ms y en s (salida pegada).

---

### [P1] BTP-04 — Con las entradas bloqueadas, el live **sigue gestionando** la posición abierta y los dos backtesters la abandonan
**Archivo:** `backtesting/backtester.py:467-469` y `:968-974` vs `main.py:534-541, 557-558`
**Evidencia:** live (fix F03/F09 de la ronda 1):
```python
            entries_allowed = (strategy.should_activate(regime)
                and self.portfolio_manager.should_strategy_trade(
                    strategy.strategy_type, regime, symbol=symbol))
            if current_pos is None and not entries_allowed:
                continue
            ...
            if not entries_allowed:
                signals = [s for s in signals if OrderExecutionEngine.is_exit_signal(s)]
```
backtest:
```python
            for strat in active_strategies:
                if not strat.should_activate(regime):
                    continue           # <- con posicion abierta, tambien
```
`MeanReversionStrategy.should_activate` devuelve `False` en `BREAKOUT` (`mean_reversion.py:110`). En el backtest, si el régimen entra en BREAKOUT con una posición abierta, esa posición deja de recibir trailing stop, SL software y stale-exit hasta que el régimen cambie; en live se gestiona siempre.
**Por qué:** el arreglo de ronda 1 en `main.py` **no se propagó** a los backtesters, así que la mejora de seguridad del live es precisamente lo que el backtest no mide. Y el sesgo no es neutro: quita salidas anticipadas justo en el régimen más volátil.
**Fix:** replicar el patrón `entries_allowed` en ambos motores (llamar a `generate_signals` siempre que haya posición y filtrar con `is_exit_signal` cuando las entradas estén cerradas).
**Verificado cómo:** lectura línea a línea de las tres rutas.

---

### [P1] BTP-05 — La congelación de Fibonacci (Fase 0, fb073a1) **no llega al backtester**: el motor simple ignora las 3 puertas y el `Realistic` ignora `SYMBOL_STRATEGY_MAP`
**Archivo:** `backtesting/backtester.py:474` y `:971-973`
**Evidencia:** el `Backtester` no usa `PortfolioManager` en absoluto:
```python
                # Capital asignado (simplificado)
                alloc = equity / len(active_strategies) / len(self.settings.symbols)
```
y el `RealisticBacktester` llama a la puerta **sin `symbol=`**:
```python
                if not portfolio_manager.should_strategy_trade(
                    strategy.strategy_type, regime
                ):
```
mientras la firma es `should_strategy_trade(self, strategy, regime, symbol="")` y el mapa solo se aplica `if symbol and symbol in SYMBOL_STRATEGY_MAP` (`portfolio/portfolio_manager.py:293, 297-299`), con `"BTC-USD": set()` (`:69`). Comprobado ejecutando: `Backtester.run(..., strategies=["FIBONACCI_RETRACEMENT"])` sobre BTC-USD abre **10 posiciones** pese a que en live FIB está congelada en las tres puertas (`allocation 0.00`, `REGIME_WEIGHTS 0.00`, `SYMBOL_STRATEGY_MAP["BTC-USD"] = set()`).
**Por qué:** doble filo. (a) Un backtest de FIB hoy no describe nada que el live vaya a hacer. (b) Y al revés: no se puede usar el backtester para justificar **descongelar** FIB, porque no reproduce la puerta que la congela. Lo mismo vale para cualquier futuro cambio de `REGIME_WEIGHTS`.
**Fix:** el motor único debe pasar por `should_strategy_trade(..., symbol=symbol)` y `get_allocation(symbol, regime, strategy)`, y saltarse la estrategia cuando la asignación sea 0. Añadir un test: con `SYMBOL_STRATEGY_MAP["BTC-USD"] = set()`, un backtest de FIB sobre BTC debe dar 0 trades.
**Verificado cómo:** lectura + ejecución (10 trades FIB sobre BTC-USD, salida en BTP-01).

---

### [P1] BTP-06 — Las barras que construye el live se etiquetan con la hora de **cierre**; las klines del backtest con la de **apertura** → desfase de 60 s y un minuto duplicado en la costura del seed
**Archivo:** `core/market_data.py:340, 383-384` vs `:144` y `scripts/download_futures_klines.py:160-164`
**Evidencia:**
```python
# market_data.py:339-341
        while last_bar > 0 and ts - last_bar >= self.bar_interval:
            bar_close_ts = last_bar + self.bar_interval
            self._close_bar(symbol, bar_close_ts)
# market_data.py:383-384
        new_bar = {
            "timestamp": bar_close_ts,      # <- hora de CIERRE
```
mientras el seed guarda la hora de apertura de la kline (`market_data.py:144`: `"timestamp": int(k[0]) / 1000`) y fija `_last_bar_time` a esa apertura (`:162`). Simulación ejecutada:
```
seed rows ts: [1800000000, 1800000060, 1800000120]  _last_bar_time = 1800000120.0
La barra construida con ticks de [1800000120, 1800000180) quedo etiquetada 1800000180
Convencion kline (backtest): esa vela se etiqueta 1800000120
DESFASE = 60 segundos
```
Además la última fila del seed (la vela en curso que devuelve `fapi`) cubre el mismo minuto que la primera barra construida con ticks → **ese minuto entra dos veces** en el df, con dos etiquetas distintas.
**Por qué:** afecta a todo lo que use el timestamp: el eje X del gráfico, `duration_sec` de los trades, la conversión ms↔s, y cualquier intento de alinear un backtest con un tramo live real (siempre estará desplazado una barra). También significa que el df live mezcla dos procedencias (klines oficiales + barras de aggTrades) con dos convenciones.
**Fix:** usar la hora de apertura también en `_close_bar` (`"timestamp": bar_close_ts - self.bar_interval`) y descartar la última kline del seed si aún está en curso (`k[6] > now_ms`).
**Verificado cómo:** simulación directa de `MarketDataCollector.on_trade`/`_close_bar` con `py -3.12` (salida pegada).

---

### [P1] BTP-07 — Live/paper enruta por `SmartOrderRouter` (LIMIT, fee maker y fill **estocástico**); los dos backtesters siempre llenan MARKET al 100 %
**Archivo:** `execution/paper_simulator.py:468-534, 550-551` vs `backtesting/backtester.py:516-540` y `:1126-1148`
**Evidencia:**
```python
# paper_simulator.py:490-501
        if routing.order_type == "LIMIT" and routing.limit_price > 0:
            base_price = routing.limit_price
            import random
            if random.random() > routing.fill_probability:
                # Order not filled — no trade (matches live behavior)
                return None
# paper_simulator.py:550-551
        pos.entry_fee_rate = (self.config.taker_fee if routing.order_type == "MARKET"
                              else self.config.maker_fee)
```
Ningún backtester instancia el router: llenan siempre al `close` de la barra con `taker_fee` y slippage completo. Diferencias acumuladas por trade: **fee** 4 bps (taker) vs 2 bps (maker), **slippage** 1.0× vs 0.3×, **precio base** `close` vs `routing.limit_price`, y sobre todo **una fracción de las señales simplemente no se ejecuta en live**. Encima `random` no está sembrado → dos sesiones paper con los mismos datos dan trades distintos.
**Por qué:** el backtest sobrestima el número de operaciones (toma el 100 %) y a la vez sobrestima su coste (siempre taker). Los dos sesgos no se cancelan: van a partidas distintas del PnL. Y con `random` sin semilla, el paper trading tampoco es reproducible, así que no sirve como árbitro entre backtest y live.
**Fix:** llamar a `SmartOrderRouter.route()` desde el motor de backtest con los mismos argumentos (requiere propagar `spread_bps`/`book_depth_usd`, hoy inexistentes en backtest → ver BTP-11 sobre el libro sintético), y sembrar el RNG del simulador (`random.Random(seed)` por instancia) para poder reproducir sesiones.
**Verificado cómo:** lectura línea a línea de las tres rutas de ejecución.

---

### [P1] BTP-08 — Los precios de fill de salida difieren en los tres motores; el SL, que es el 45–82 % de las salidas, se rellena **sin slippage** en el backtest
**Archivo:** `execution/paper_simulator.py:271-281, 406-412` vs `backtesting/backtester.py:424` y `:878`
**Evidencia:** live/paper:
```python
            if trigger == "SL":
                sl_slip_bps = self.config.slippage_bps * 1.5   # 1.5x base slippage on SL
                sl_slip = sl_slip_bps * pos.stop_loss / 10_000
                exit_price = pos.stop_loss - sl_slip if pos.side == Side.BUY else pos.stop_loss + sl_slip
            else:
                exit_price = pos.take_profit          # TP as limit order — exact fill
...
            exit_slip_bps = self.config.slippage_bps * 0.5      # salida por senal
```
backtest (ambos motores):
```python
                    pnl = pos.close(exit_price_sltp, trading_config.taker_fee)   # exit_price_sltp = pos.stop_loss EXACTO
...
                        exit_slip = compute_slippage(base_bps=trading_config.slippage_bps, ...)   # 1.0x en salidas por senal
```

| Tipo de salida | LIVE/PAPER | BACKTEST | Sesgo |
|---|---|---|---|
| SL | `stop_loss ∓ 1.5 × 1.5 bps` = ∓2.25 bps | `stop_loss` exacto | backtest **+2.25 bps por SL** a favor |
| TP | `take_profit` exacto | exacto | = |
| Señal de salida | ∓0.5 × 1.5 bps = ∓0.75 bps | `compute_slippage` completo (≈1.5–4 bps) | backtest **en contra** |
| Gap a través del SL | n/a (tick a tick) | rellena en el nivel aunque la vela **abra** más allá | backtest a favor |

Con SL de 25–40 bps (MR: `1.5×ATR`), 2.25 bps son el **6–9 % del riesgo por operación**, en todas las operaciones perdedoras.
**Por qué:** amplía `04-P1` con los factores exactos del live que la ronda 1 no tenía. El error no es aleatorio: el backtest es sistemáticamente optimista justo en la salida más frecuente.
**Fix:** una única función `simulate_exit_fill(kind, level, side, cfg)` compartida por `paper_simulator` y el backtester; y en el backtest, si `open` de la vela ya está más allá del SL, rellenar al `open`.
**Verificado cómo:** lectura línea a línea + aritmética de bps sobre `slippage_bps = 1.5` (verificado en `Settings()`).

---

### [P1] BTP-09 — Los backtesters nunca llaman a `notify_external_exit`, así que tras un SL/TP la estrategia sigue creyéndose dentro
**Archivo:** `backtesting/backtester.py:423-427` y `:877-887` (falta la llamada) vs `main.py:634-638`
**Evidencia:** live:
```python
        is_sl_tp = trade.signal_features.get("exit_reason") in ("SL", "TP")
        if is_sl_tp:
            for strategy in self.strategies:
                if strategy.strategy_type == trade.strategy and hasattr(strategy, "notify_external_exit"):
                    strategy.notify_external_exit(trade.symbol, time.time())
```
que hace `self._last_exit_time[symbol] = ts; self._states.pop(symbol, None)` (`mean_reversion.py:112-114`). `grep -rn notify_external_exit` sobre el repo (excluyendo `build/`, `desktop/`, `archive/`) devuelve **solo** `main.py:637-638` y `main.py:880-882`: **ninguna llamada desde `backtesting/`**.
**Por qué:** tras un SL/TP el backtest deja `_states[symbol]` vivo y `_last_exit_time` sin actualizar → (a) no aplica el `COOLDOWN_SEC = 180` que sí aplica el live, así que **reentra antes**; (b) `_check_exit` de la siguiente posición arranca con el `best_pnl_atr` y el `entry_bar_idx` de la posición anterior. Es una divergencia que se acumula: cada SL desincroniza el estado interno de la estrategia.
**Fix:** llamar a `strategy.notify_external_exit(symbol, ts)` en las ramas SL/TP y de liquidación de ambos motores, con el timestamp de la barra.
**Verificado cómo:** `grep -rn "notify_external_exit" --include=*.py .` (salida revisada) + lectura de las tres rutas.

---

### [P1] BTP-10 — El live evalúa las salidas ~20 veces por minuto y el backtest una vez por barra → el trailing stop mide cosas distintas
**Archivo:** `main.py:429` (`strategy_interval_sec`) + `mean_reversion.py:160-165` vs `backtester.py:466-487`
**Evidencia:** `Settings().trading.strategy_interval_sec = 3.0` (verificado) y el bucle recorre los 4 símbolos en cada vuelta (`main.py:425-429`), así que `_process_symbol` corre ~20 veces por minuto y por símbolo. En `MeanReversionStrategy.generate_signals` la comprobación de salida está **antes** de la puerta de barra nueva:
```python
        # ── EXIT check (every eval, fast response) ───────────────
        if current_position is not None:
            exit_sig = self._check_exit(symbol, m5, current_position, snapshot)
            if exit_sig:
                signals.append(exit_sig)
            return signals
        # ── Only evaluate entries when new data arrives ──────────
        if not new_bar_arrived:
            return signals
```
`_check_exit` actualiza `state.best_pnl_atr` con `snapshot.price`, que en live es el mid del libro tick a tick (`market_data.py:356-357`). En el backtest solo se llama una vez por barra y con el `close`.
**Por qué:** el trailing stop de MR (`TRAIL_ACTIVATE_ATR=1.5`, `TRAIL_DISTANCE_ATR=0.5`) se activa con el **pico intraminuto** en live y con el **cierre de la barra** en backtest. El pico intraminuto es sistemáticamente mayor → en live el trail se arma antes y salta antes. En la ronda 1 el 45–56 % de las salidas de MR fueron `CLOSE_*` (trailing/software SL-TP); ese porcentaje no es transferible.
**Fix:** en el backtest, evaluar las salidas sub-barra (por ejemplo con la secuencia `open → low → high → close` para longs y `open → high → low → close` para shorts), o documentar explícitamente que el backtest da una cota inferior del trailing.
**Verificado cómo:** lectura + `Settings().trading.strategy_interval_sec` impreso (`3.0`).

---

### [P1] BTP-11 — La serie de 1m del live tiene agujeros que nunca se rellenan, y las estrategias agrupan **por posición**, no por reloj
**Archivo:** `core/market_data.py:366-378` y `mean_reversion.py:439`, `fibonacci_retracement.py:543`
**Evidencia:**
```python
        # market_data.py:375-378
        if not bar_ticks:
            # No hay ticks en esta barra, solo actualizar timestamp
            self._last_bar_time[symbol] = bar_close_ts
            return          # <- NO se anade fila
```
y el agrupado es posicional:
```python
        groups = np.arange(len(trim)) // RESAMPLE_MINUTES      # mean_reversion.py:439
```
Simulación ejecutada: con un salto de 200 s en el que solo llega 1 tick, se crea **1 barra en lugar de 3** (`filas nuevas = 1`, faltan 2). `_data_refresh_loop` (`main.py:788-796`) solo refresca snapshots, **nunca rellena el DataFrame**. Que el hueco no sea culpa del mercado está verificado: en 150 días de futures **no hay ni un minuto con 0 trades** (`BTC 0 / ETH 0 / SOL 0 / ADA 0`, mínimo 8 trades/min en ADA); los huecos vienen de reconexiones WS y de los propios guards (`WARMUP_SEC=5` + first-tick skip por símbolo, `market_data.py:239-250`). Como referencia empírica del entorno, la serie construida a partir de WS del colector del proyecto (`data/klines/`) tiene **697 / 668 / 500 / 20** discontinuidades en 10–13 k barras (ADA/BTC/ETH/SOL) ≈ 5 % de minutos ausentes.
**Por qué:** con agrupado posicional, una "vela de 5m" del live puede cubrir 8 o 15 minutos reales y una "vela de 1H" 90 minutos, sin que nada lo señale. El backtest, con series contiguas y 0 gaps, **nunca** reproduce ese caso. Los umbrales (RSI 35/65 "en 5m", ADX≥20 "en 1H") no significan lo mismo en los dos lados.
**Fix:** (a) en `_close_bar`, cuando no hay ticks, insertar una barra plana (`open=high=low=close=` último cierre, `volume=0`) para mantener la rejilla; (b) mejor aún, agrupar por reloj (`timestamp // 300`, `// 3600`) en las dos estrategias, con lo que el hueco deja de importar; (c) test que meta un df con huecos y compruebe que las velas 5m siguen cubriendo 5 minutos.
**Verificado cómo:** simulación de `on_trade` con salto de 200 s (`py -3.12`), conteo de `trades == 0` en los 4 parquets de futures, y conteo de discontinuidades en `data/klines/*`.

---

### [P1] BTP-12 — `bars_held` vale **siempre 0** en los dos lados: el estrechamiento del trailing y la salida por posición estancada (24 h) son código muerto
**Archivo:** `strategies/mean_reversion.py:288, 342-343` (y `fibonacci_retracement.py` equivalente)
**Evidencia:**
```python
        self._states[symbol] = MRState(
            entry_time=now,
            entry_bar_idx=len(self._resampled.get(symbol, pd.DataFrame())),   # :288
...
        current_bar_count = len(self._resampled.get(symbol, pd.DataFrame()))  # :342
        bars_held = current_bar_count - state.entry_bar_idx                   # :343
```
pero `_resample_5m` recorta a un tamaño fijo (`max_input = 5 × RESAMPLE_BUFFER = 1000`). Medido:
```
buffer 1m =   501  ->  len(_resampled) = 100
buffer 1m =  1000  ->  len(_resampled) = 200
buffer 1m =  2000  ->  len(_resampled) = 200
buffer 1m =  5000  ->  len(_resampled) = 200
```
En régimen estacionario `len(_resampled)` es **constante** (200 en live, 100 en el `Backtester`), luego `bars_held == 0` siempre. Consecuencia: `TRAIL_TIGHT_AFTER_BARS = 20` nunca se alcanza (el trail nunca se estrecha) y `stale_bars = 288` nunca se alcanza (la salida `stale_position_24h` **nunca se dispara**).
**Por qué:** no rompe la paridad (falla igual en ambos lados), pero significa que dos de las tres reglas de gestión de salida documentadas de MR no existen, y que ningún backtest podrá nunca medirlas. Lo mismo aplica a FIB (`RESAMPLE_BUFFER=150` → `len(_resampled)` fijo).
**Fix:** guardar el timestamp de entrada y calcular `bars_held` por tiempo (`(now - state.entry_time) / (RESAMPLE_MINUTES*60)`), no por longitud de un buffer recortado.
**Verificado cómo:** `py -3.12` llamando a `_resample_5m` con buffers de 300…5000 barras reales (tabla pegada).

---

### [P1] BTP-13 — Los datos correctos (`data/binance_futures/`) los escribe **un** archivo y los lee **cero**: todos los runners siguen en SPOT
**Archivo:** `scripts/run_full_backtest.py:77`, `scripts/run_backtest_binance.py:44`, `scripts/optimize_with_binance.py:122`, `scripts/test_mtf_strategy.py:15`, `server/bridge.py:1560`
**Evidencia:**
```
$ grep -rn "binance_futures" --include=*.py .   (sin build/ desktop/ archive/)
./scripts/download_futures_klines.py:12
./scripts/download_futures_klines.py:16
./scripts/download_futures_klines.py:271
--- archivos que lo leen: 0 ---
```
y todos los consumidores apuntan a `data/binance/klines/…` (SPOT, `data/binance_downloader.py:41`, rango 2025-12-29 → **2026-04-03**).
**Por qué:** el entregable de la ronda 1 (el downloader de futures) quedó huérfano. Hoy, cinco meses después del final de la serie SPOT, cualquier backtest que se lance por la vía normal usa el mercado equivocado y un periodo caducado. Es la causa raíz de que 04-P0 #3 siga vivo aunque el dato correcto exista en disco.
**Fix:** un único `DATA_ROOT = "data/binance_futures/klines"` en `config/settings.py` y que todos los runners y el bridge lo lean de ahí; borrar o marcar como obsoleto `data/binance/`.
**Verificado cómo:** `grep -rn` (salida pegada) + inspección de los dos parquets con `py -3.12`.

---

### [P2] BTP-14 — El kwarg `kelly_risk_pct` que el live pasa y los backtesters no: divergencia **latente** de sizing
**Archivo:** `main.py:549, 555` vs `backtester.py:483-486` y `:1002-1006`
**Evidencia:**
```python
# main.py:549-556
            kelly_pct = self.risk_manager.get_kelly_risk_pct(strategy.strategy_type)
            signals = strategy.generate_signals(..., kelly_risk_pct=kelly_pct)
# backtester.py:483-486  (no aparece kelly_risk_pct)
                signals = strat.generate_signals(
                    symbol, df_slice, snapshot, regime, sym_config, alloc, current_pos,
                    micro=micro_snap, obi=obi_result_simple,
                )
```
Medido: la corrida `C` (live + `kelly_risk_pct=0.015`) da **exactamente las mismas 36 señales** que `B`. Con la base de trades vacía, `get_kelly_risk_pct` cae al valor por defecto (`risk_manager.py:525-528`), así que hoy el kwarg es inerte. **No es un P0 hoy**, pero en cuanto haya historial Kelly el sizing live dejará de coincidir con el backtest sin que nada avise.
**Fix:** pasar `kelly_risk_pct` también en los backtesters (alimentando el `RiskManager` con los trades simulados, como ya hace el `Realistic` con `record_trade_result`).
**Verificado cómo:** experimento §2.2, columna `B vs C: identical_rows=True`.

---

### [P2] BTP-15 — Los dos backtesters no coinciden **entre sí** en el desempate SL/TP dentro de la misma barra, y ninguno coincide con el live
**Archivo:** `backtesting/backtester.py:397-404` vs `:859-876`
**Evidencia:** motor simple — gana el nivel más cercano al `open`:
```python
                    if sl_hit and tp_hit:
                        sl_dist = abs(open_ - pos.stop_loss)
                        tp_dist = abs(open_ - pos.take_profit)
                        if sl_dist <= tp_dist: ... "SL_LONG" else ... "TP_LONG"
```
`RealisticBacktester` — el SL siempre primero (`elif`, no hay desempate):
```python
                if pos.side == Side.BUY:
                    if pos.stop_loss > 0 and low <= pos.stop_loss:
                        exit_price_sltp = pos.stop_loss; exit_side_sltp = "SL_LONG"; hit = True
                    elif pos.take_profit > 0 and high >= pos.take_profit:
```
En live gana el que ocurra antes en el tiempo real de los ticks (`paper_simulator.on_price_update` en cada trade).
**Por qué:** con `TP = 4×ATR` y `SL = 1.5×ATR` (MR), las barras que tocan ambos no son raras en 1m volátil; el `Realistic` es pesimista por construcción y el simple aproxima. Que los dos motores del mismo repo den resultados distintos sobre los mismos datos es, de por sí, un problema de credibilidad.
**Fix:** una sola regla en el motor único (recomendado: el pesimista, SL primero, y documentarlo), o sub-barra como en BTP-10.
**Verificado cómo:** lectura línea a línea de ambos bloques.

---

### [P2] BTP-16 — Look-ahead multi-timeframe y kwarg `mtf=` que solo existe en el `RealisticBacktester`
**Archivo:** `backtesting/backtester.py:771-788` y `:988-1006`
**Evidencia:**
```python
                for tf_label, tf_rule in [("5m","5min"),("15m","15min"),("1h","1h")]:
                    resampled = ohlcv_indexed.resample(tf_rule).agg({...})
                    ...
                        mapped = resampled[col].reindex(ohlcv_df_ts, method="ffill")
                        ohlcv_df[col] = mapped.values
```
`resample("15min")` etiqueta a la izquierda, así que la fila de 1m de las 12:03 recibe el `close`/`rsi`/`adx` de la vela 15m 12:00–12:14 — **valores del futuro** (04-P2, sigue abierto). Además esos `mtf_data` se pasan a `generate_signals` (`:1002-1006`) mientras `main.py:551-556` **no pasa `mtf`** y el motor simple tampoco.
**Por qué:** latente para MR/FIB (no leen `mtf`), pero es una trampa activa: cualquier estrategia que empiece a usar `kwargs["mtf"]` obtendrá resultados con look-ahead en el `Realistic` y ningún dato en live.
**Fix:** `label="right", closed="right"` + `.shift(1)`, o eliminar el bloque MTF (nadie lo consume) y con él el kwarg.
**Verificado cómo:** lectura + semántica documentada de `DataFrame.resample` (etiqueta izquierda por defecto).

---

### [P2] BTP-17 — Reproducibilidad: caché de régimen por reloj de pared en backtest y `random` sin semilla en paper
**Archivo:** `core/regime_detector.py:32-34, 141-145`; `execution/paper_simulator.py:494-495`
**Evidencia:**
```python
        self._threshold_cache_sec: float = 15.0
...
        now = _time.monotonic()
        ...
        if cached and (now - last) < self._threshold_cache_sec:
```
En backtest, los umbrales adaptativos se recalculan cada 15 **segundos de CPU** (≈ cada varios cientos de barras, y distinto en cada ejecución según la carga de la máquina), no cada N barras. Y en paper:
```python
            import random
            if random.random() > routing.fill_probability:
```
sin semilla ni RNG propio.
**Por qué:** 04-P2 sigue abierto. Un backtest cuyo camino de régimen depende de la velocidad del CPU y un paper cuyos fills dependen del RNG global no son reproducibles; sin reproducibilidad no hay forma de demostrar que un fix de paridad ha funcionado.
**Fix:** en `backtest_mode`, cachear por número de barras o por el timestamp de la barra; en el simulador, `self._rng = random.Random(seed)`.
**Verificado cómo:** lectura de ambos bloques (la divergencia numérica ya fue medida en la ronda 1, §2.6 de `04_*`).

---

### [P2] BTP-18 — El `RealisticBacktester` usa el reloj de pared para las pausas de riesgo temporizadas
**Archivo:** `risk/risk_manager.py:187, 224, 230` invocado desde `backtesting/backtester.py:1063`
**Evidencia:**
```python
        if self._consecutive_loss_pause:
            if time.time() < self._consecutive_loss_pause_until:
...
        if self._circuit_breaker_active:
            cooldown_elapsed = time.time() >= self._circuit_breaker_until
```
En live esos temporizadores miden minutos de mercado. En un backtest que procesa ~45 barras/s, un cooldown de 30 min de reloj cubre **~80 000 barras** (55 días de datos) o, al revés, expira tras 30 s reales aunque en el mundo simulado no haya pasado nada.
**Por qué:** los cortacircuitos son parte del sistema que se pretende validar; si su duración depende de la velocidad de la CPU, el backtest no mide el sistema real. Combinado con BTP-17 hace que dos ejecuciones idénticas puedan divergir.
**Fix:** inyectar un `clock` (callable) en `RiskManager`, que en live sea `time.time` y en backtest devuelva el timestamp de la barra.
**Verificado cómo:** lectura de las llamadas + `Backtester` no usa `RiskManager` en absoluto (solo afecta al `Realistic`).

---

### [P2] BTP-19 — Runners de backtest rotos u obsoletos (04-P2 sigue abierto, ampliado)
**Archivo:** `scripts/quant_audit.py:40`, `scripts/optimize_with_binance.py:84`, `scripts/exit_analysis.py:28`, `main.py:944-950, 959-965`, `main.py:1449-1453`
**Evidencia (todo comprobado hoy):**

| Runner | Estado verificado |
|---|---|
| `scripts/quant_audit.py` | **ROTO** — `import scripts.quant_audit` → `ModuleNotFoundError: No module named 'strategies.trend_following'` (el archivo no existe: `strategies/trend_following.py → False`) |
| `scripts/optimize_with_binance.py:84` | **ROTO** — llama a `loader.load_dataframe(...)`; `hasattr(HistoricalDataLoader,'load_dataframe') → False` |
| `scripts/exit_analysis.py:28` | **ROTO** — `from analytics.exit_optimizer import ...`; `find_spec('analytics.exit_optimizer') → None` (solo está en `archive/analytics/exit_optimizer.py`) |
| `scripts/run_full_backtest.py:77`, `run_backtest_binance.py:44`, `test_mtf_strategy.py:15`, `check_trend.py:9-11` | Ejecutan, pero sobre **SPOT** (`data/binance/klines` / `api.binance.com`) |
| `main.py --backtest` sin `--csv` | Corre sobre **datos sintéticos** (`generate_sample_data`, `:947-949`) y **los persiste** en `data/trade_database.db` con `source="backtest"` (`:959-965`) |
| `main.py --walk-forward` | **Datos sintéticos** (`:1449-1453`), con el optimizador archivado |

Matiz honesto sobre la persistencia: el UI **no** se contamina, porque `server/bridge.py:863` filtra `get_trades(source="paper")`; sí quedan mezclados para cualquier consumidor que no filtre.
**Fix:** borrar los tres rotos o repararlos; un único `scripts/backtest_futures.py` (motor único + `data/binance_futures` + walk-forward con embargo); `--walk-forward` sobre datos reales.
**Verificado cómo:** import real con `py -3.12` para los tres rotos, `hasattr`/`find_spec`/`os.path.exists` para las dependencias, lectura para el resto.

---

### [P2] BTP-20 — `scripts/download_futures_klines.py`: el funding no reanuda (trunca el histórico), y el resume puede abrir un hueco silencioso
**Archivo:** `scripts/download_futures_klines.py:204-244` y `:128-140`
**Evidencia:** funding — no lee el archivo existente y sobrescribe:
```python
    rows: List[dict] = []
    cursor = start_ms
    while cursor < now_ms: ...
    df = pd.DataFrame(rows).drop_duplicates("timestamp").sort_values("timestamp")...
    df.to_parquet(out_path, index=False)     # <- pisa lo anterior
```
(compárese con las klines, que sí hacen `pd.read_parquet(out_path)` + `pd.concat`, `:128-192`.) Ejecutar `--funding --days 30` después de un `--days 150` deja el archivo con 30 días.
Klines — si el archivo es más antiguo que la ventana pedida (`last_ts < start_ms`), se descarga desde `start_ms` y se concatena, creando un hueco entre ambos tramos que solo se delata en el contador `gaps` del final (`:198`). Y `--end` es inutilizable con un archivo ya existente: `start_ms = last_ts + step > now_ms` → "al día, nada que descargar" (`:141-143`).
**Por qué:** es la única fuente de datos correcta del proyecto (BTP-13); que pueda truncar el funding o abrir huecos sin fallar es un riesgo directo sobre la evidencia.
**Fix:** aplicar en `download_funding` el mismo patrón read+concat+dedup de las klines; abortar (o avisar en rojo) cuando `last_ts < start_ms`; y con `--end` explícito, ignorar el resume hacia adelante.
**Verificado cómo:** lectura línea a línea comparando las dos funciones (`download_klines` vs `download_funding`) del mismo archivo.

---

### [P3] BTP-21 — Menores en `backtesting/backtester.py`
**Archivo:** `backtesting/backtester.py:663`, `:738`, `:1144`, `:900`
**Evidencia:**
* `:663` `ml_filter: Optional[Any] = None` pero `Any` **no está importado** (`:16` `from typing import Dict, List, Optional, Tuple`). Solo lo salva `from __future__ import annotations`; comprobado: `typing.get_type_hints(RealisticBacktester.run)` → `NameError: name 'Any' is not defined`. Rompe cualquier introspección (pydantic, FastAPI, `typeguard`).
* `:738` `atexit.register(close_jsonl)` se ejecuta **en cada `run()`** → fuga de callbacks en barridos con muchas corridas.
* `:1144` guarda `slippage_bps=trading_config.slippage_bps` (el de config) en vez del realmente aplicado, que sí se calcula en el motor simple (`:521-525` `compute_slippage_bps`).
* `:900` `df_slice = ohlcv_df.iloc[:i+1]` es O(n²) (se resuelve solo con el fix de BTP-02).
**Fix:** añadir `Any` al import; registrar `atexit` una vez o usar `with`; guardar `compute_slippage_bps(...)`.
**Verificado cómo:** `py -3.12 -c "typing.get_type_hints(...)"` (traza pegada) + lectura.

---

### [P2] BTP-22 — Resumen de las tres rutas de ejecución de backtest y su fidelidad

| Ruta | Motor | Datos | Ventana | RiskManager | PortfolioManager | FIB | `exit_*` | Fidelidad |
|---|---|---|---|---|---|---|---|---|
| UI/desktop → `POST /api/backtest/run` (`bridge.py:1536`) | `Backtester` | **SPOT** hasta 2026-04-03 | 501 | ✗ | ✗ | sí (sin congelar) | ✗ | **la peor** |
| `main.py --backtest [--csv]` (`main.py:921`) | `Backtester` | **sintéticos** por defecto | 501 | ✗ | ✗ | no (solo MR+all) | ✗ | nula |
| `main.py --backtest-realistic` (`main.py:976, 1117`) | `RealisticBacktester` | colector (`data/trades`, SPOT, con gaps) | prefijo completo | ✓ | ✓ (sin `symbol=`) | **no soportada** | ✗ | la mejor de las tres, aún lejos |

Ninguna de las tres usa `data/binance_futures/`.
**Fix:** dejar una sola ruta y borrar las otras dos.
**Verificado cómo:** lectura de los tres puntos de entrada.

---

---

## 3b. Tabla resumen de hallazgos

| id | Sev | Título | Archivo:línea | ¿Nuevo o R1? |
|---|---|---|---|---|
| BTP-01 | **P0** | `exit_fibonacci` ignorado por los 2 backtesters; el fix R1 solo tocó live y agrandó la brecha | `backtesting/backtester.py:495` | 04-P0 **sigue abierto** |
| BTP-02 | **P0** | Ventana 501 vs `MAX_BARS`=2000 → solapamiento de señales **42.9 %**, filtro 1H invertido | `backtesting/backtester.py:366` | 04-P0 **sigue abierto** (ahora cuantificado) |
| BTP-03 | **P0** | `/api/backtest/run` (UI/desktop): datos SPOT caducados, `start_date` inoperante, `end_date`→0 barras, Sharpe 59× deprimido | `server/bridge.py:1559` | **nuevo** (amplía 04-P1) |
| BTP-04 | P1 | Con entradas bloqueadas el live gestiona la posición y los backtesters la abandonan | `backtesting/backtester.py:468` | **nuevo** (regresión del fix R1) |
| BTP-05 | P1 | La congelación de FIB (Fase 0) no llega al backtester; `should_strategy_trade` sin `symbol=` | `backtesting/backtester.py:474, 971` | **nuevo** |
| BTP-06 | P1 | Barras live etiquetadas por hora de cierre vs klines por apertura → desfase 60 s + minuto duplicado | `core/market_data.py:340, 384` | **nuevo** |
| BTP-07 | P1 | `SmartOrderRouter` (LIMIT, maker fee, fill estocástico sin semilla) solo existe en live | `execution/paper_simulator.py:490` | **nuevo** |
| BTP-08 | P1 | SL se rellena sin slippage en backtest (live: 1.5×); salida por señal 1.0× vs 0.5× | `backtesting/backtester.py:424` | 04-P1 ampliado con factores exactos |
| BTP-09 | P1 | `notify_external_exit` nunca se llama desde los backtesters → estado y cooldown desincronizados | `backtesting/backtester.py:423` | **nuevo** |
| BTP-10 | P1 | Salidas: ~20 evaluaciones/min en live vs 1/barra en backtest → el trailing mide cosas distintas | `main.py:429` / `mean_reversion.py:160` | **nuevo** |
| BTP-11 | P1 | Huecos en la serie 1m del live nunca rellenados + agrupado posicional `//5`, `//60` | `core/market_data.py:375` | **nuevo** |
| BTP-12 | P1 | `bars_held` siempre 0 → trail-tightening y stale-exit 24 h son código muerto en ambos lados | `strategies/mean_reversion.py:288, 343` | **nuevo** |
| BTP-13 | P1 | `data/binance_futures/` lo escribe 1 archivo y lo leen 0; todos los runners siguen en SPOT | `scripts/run_full_backtest.py:77` | **nuevo** (causa raíz de 04-P0 #3) |
| BTP-14 | P2 | `kelly_risk_pct` solo se pasa en live — divergencia **latente** (hoy inerte, verificado) | `main.py:555` | **nuevo** |
| BTP-15 | P2 | Desempate SL/TP distinto entre los dos backtesters y ninguno igual al live | `backtesting/backtester.py:397, 859` | **nuevo** |
| BTP-16 | P2 | Look-ahead MTF + kwarg `mtf=` que solo existe en el `Realistic` | `backtesting/backtester.py:771` | 04-P2 **sigue abierto** |
| BTP-17 | P2 | Reproducibilidad: caché de régimen por reloj de pared + `random` sin semilla | `core/regime_detector.py:141` | 04-P2 **sigue abierto** |
| BTP-18 | P2 | Pausas de riesgo temporizadas con reloj de pared dentro del `RealisticBacktester` | `risk/risk_manager.py:187` | **nuevo** |
| BTP-19 | P2 | 3 runners rotos (`quant_audit`, `optimize_with_binance`, `exit_analysis`) + SPOT + sintéticos | `scripts/quant_audit.py:40` | 04-P2 **sigue abierto** |
| BTP-20 | P2 | `download_futures_klines`: funding sin resume (trunca) y hueco silencioso en el resume de klines | `scripts/download_futures_klines.py:215` | **nuevo** |
| BTP-21 | P3 | `Any` sin importar, `atexit` por `run()`, `slippage_bps` guardado ≠ aplicado | `backtesting/backtester.py:663` | parcialmente 04-P3 |
| BTP-22 | P2 | Las 3 rutas de backtest del repo y su fidelidad (ninguna usa futures) | `server/bridge.py:1536` | **nuevo** |

**Totales:** 3 P0 · 10 P1 · 8 P2 · 1 P3 = **22 hallazgos**. De los 6 hallazgos de ronda 1 que tocan esta área, **6 siguen abiertos**.

---

## 4. Veredicto

1. **No hay paridad. Ni aproximada.** Sobre 3 días reales de BTC-USD 1m, con la misma estrategia, los mismos indicadores y el mismo detector de régimen, el `Backtester` genera **24** señales de entrada y la ruta live **36**, con solo **18 en común**: un solapamiento del **42.9 %**. La mitad de lo que el bot haría en producción no aparece en ningún backtest.
2. La causa dominante es **una línea**: `backtester.py:366` alimenta 501 barras donde el live tiene 2000. Con 501, el filtro de tendencia 1H de MR —su puerta principal— se calcula sobre **8 velas horarias**: `h1_adx` mediana **85.2** frente a **43.9** en live, y el **signo se invierte en 2 de 6 muestras**. El backtest no mide una versión "un poco distinta" de la estrategia; mide otra.
3. De los **39 pasos** del ciclo que he tabulado, **26 divergen materialmente** en al menos uno de los dos motores. No es un detalle a pulir: es que no existe un motor fiel.
4. Los P0 de la ronda 1 **siguen abiertos** donde importan. El fix de `exit_fibonacci` se aplicó solo al lado live, así que la brecha hoy es **mayor** que en agosto: live cierra por señal, el backtest no (verificado: 0 salidas `CLOSE_*` en 10 trades FIB).
5. Peor aún, **los arreglos de la ronda 1 en `main.py` han creado divergencias nuevas**: la gestión de posiciones con entradas bloqueadas (BTP-04) y la propagación de `kelly_risk_pct` (BTP-14) existen en live y en ningún backtester. Cada mejora del live que no se replica amplía la brecha.
6. **La congelación de Fibonacci de la Fase 0 no llega al backtester** (BTP-05): el motor simple abre 10 posiciones FIB en BTC-USD ignorando las tres puertas. O sea que el backtester tampoco puede servir de árbitro para descongelarla.
7. El **único backtest que un usuario puede lanzar** (`POST /api/backtest/run`, desde el UI y el desktop) usa datos **SPOT que terminan el 2026-04-03**, con `start_date` inoperante, `end_date` que devuelve siempre 0 barras, y un Sharpe **30× deprimido** por la unidad del timestamp (18.88 → 0.64 sobre la misma curva). Es un generador de números sin significado.
8. Los datos correctos **existen y nadie los usa**: `data/binance_futures/` (150 días, 4 símbolos, 0 gaps, 0 duplicados — reverificado hoy) lo escribe un archivo y lo leen **cero**.
9. Ni siquiera hay paridad **entre los dos backtesters**: distinto desempate SL/TP, distinta ventana, uno pasa `mtf=` y el otro no, uno soporta FIB y el otro no. Tres motores, tres estrategias distintas.
10. **Conclusión operativa: hoy ningún backtest de este repositorio puede aprobar una estrategia para live, y ninguno de los números publicados en `tasks/todo.md` es válido.** La secuencia mínima para salir de aquí es corta y está ordenada por impacto: (1) `MAX_BARS` compartido — BTP-02; (2) `is_exit_signal` único — BTP-01; (3) apuntar todo a `data/binance_futures` y normalizar ms→s — BTP-03 + BTP-13; (4) un motor único que pase por `should_strategy_trade(symbol=)`, `get_allocation` y `validate_signal` — BTP-05; (5) fills de salida compartidos con `paper_simulator` — BTP-08; (6) un test de paridad en CI que falle si el solapamiento de señales backtest↔live-buffer baja del 100 % sobre un tramo fijo. Sin el punto (6), esto se vuelve a romper en el siguiente commit.

