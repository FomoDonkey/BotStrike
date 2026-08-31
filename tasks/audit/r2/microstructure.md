# Auditoria R2 — Microestructura y datos

**Fecha:** 2026-08-31 · **Area:** `microstructure`
**Alcance:** `core/microstructure.py` (VPIN, Hawkes, Avellaneda-Stoikov, Kyle Lambda), `core/microprice.py`,
`core/orderbook_alpha.py`, `core/market_data.py`, `exchange/binance_ws.py` (parseo trade/depth/kline/markPrice)
y su consumo real en `main.py`, `risk/risk_manager.py`, `backtesting/backtester.py`, `server/serializers.py`.

**Metodo (todo medido, nada supuesto):**
- Lectura del codigo real.
- **Captura en vivo del stream combinado EXACTO** que construye `binance_ws._build_streams()`
  (`btcusdt/ethusdt/solusdt/adausdt` × `@trade` + `@depth20@100ms` + `@kline_1m` + `@markPrice@1s`),
  178 s, **40.542 mensajes**, guardada y reproducida contra las clases reales del repo.
- **Klines reales de futuros**: `data/binance_futures/klines/<SYM>/1m.parquet`,
  **216.592 barras × 4 simbolos = 150 dias**, con `taker_buy_quote` (lado agresor REAL) como ground truth.
- REST `fapi/v1/ticker/24hr` y `fapi/v1/klines` para ADV y para la vela en formacion.
- `data/trade_database.db` esta **VACIA (0 trades)** en el repo local (y `desktop/data/` tambien),
  asi que la discriminacion ganadores/perdedores se mide sobre klines reales, no sobre trades del bot.

**Referencia ronda 1:** `tasks/audit/01_core_strategy_risk.md` (F16, F18, F20, F22, F23, F28),
`tasks/audit/02_exchange_execution.md` (P1-04).

---

## Datos de mercado medidos hoy (base de todo lo que sigue)

Captura de 178,1 s del stream combinado del bot (`cap.jsonl`, 40.542 mensajes, **227,6 msg/s**):

```
  adausdt@depth20@100ms        1458    8.18/s     adausdt@trade    910    5.11/s
  btcusdt@depth20@100ms        1725    9.68/s     btcusdt@trade   9203   51.66/s
  ethusdt@depth20@100ms        1734    9.73/s     ethusdt@trade  18758  105.30/s
  solusdt@depth20@100ms        1664    9.34/s     solusdt@trade   5090   28.57/s
  btcusdt@kline_1m                0    0.00/s     btcusdt@markPrice@1s   0  0.00/s   <-- CERO
  (idem ethusdt/solusdt/adausdt kline_1m y markPrice@1s: CERO mensajes en 178 s)
--- notional de trades ---
  btcusdt   9203 trades $17.692.995 -> $ 99.324/s   trade medio $1.923
  ethusdt  18758 trades $21.191.660 -> $118.964/s   trade medio $1.130
  solusdt   5090 trades $ 3.826.357 -> $ 21.480/s   trade medio $  752
  adausdt    910 trades $   290.482 -> $  1.631/s   trade medio $  319
```

**190,6 trades/s + 37 depth/s** llegan a un unico event loop asyncio. Ese es el presupuesto real.

ADV (REST `ticker/24hr`, hoy): BTC $11,23e9 · ETH $11,43e9 · SOL $2,57e9 · ADA $0,169e9.

---

## Estado de los hallazgos de ronda 1 en esta area

| ID r1 | Estado | Evidencia medida hoy |
|---|---|---|
| 01-F16 (seed incluye la vela en formacion) | **SIGUE ABIERTO** | `MICRO-11`: la ultima fila sembrada cubria 11 s del minuto (vol 6,891 vs ~100) |
| 01-F18 (`compute_all` en el callback WS) | **SIGUE ABIERTO** | `MICRO-09`: 45,9 ms/simbolo aqui → 184 ms de loop bloqueado por minuto |
| 01-F20 (clamp del microprice compuesto) | **SIGUE ABIERTO** literal | `MICRO-14`, `core/microprice.py:231` sin cambios |
| 01-F22 (bucket VPIN 50k USD) | **SIGUE ABIERTO**, ahora cuantificado | `MICRO-06`: bucket BTC = **0,503 s**; ventana 50 buckets = **25,1 s**; canonico ADV/50 = $224,6M (4.492× mas) |
| 01-F23 (`refresh_all` reemplaza el snapshot) | **SIGUE ABIERTO** | `core/market_data.py:314` `self._snapshots[symbol] = snapshot` |
| 01-F28 (sin seed, barras 1m desalineadas) | **SIGUE ABIERTO** | `MICRO-12`: `ts % 60 = 16,969` en las 3 barras generadas |
| 02-P1-04 (depth usa claves `b`/`a`) | **ARREGLADO Y CORRECTO** | payload real `{"e":"depthUpdate",...,"b":[["78014.20","5.687"],...],"a":[...]}`; `binance_ws.py:150-153` lee exactamente esas claves |

**Regresiones nuevas introducidas en esta area: ninguna.** El fix de `b`/`a` es correcto y esta verificado
contra el payload real. Los congelamientos de `fb073a1`/`1309927` no tocan este modulo.

---

## Hallazgos

### [P1] MICRO-01 — Hawkes descarta la auto-excitacion en el 78-88 % de los trades reales: `if dt <= 0: return` se dispara justo en los clusters que el proceso existe para detectar
**Archivo:** `core/microstructure.py:325`
**Evidencia (codigo):**
```python
dt = timestamp - self._cached_excitation_time
if dt <= 0:
    return self._result          # <-- sale ANTES de actualizar la excitacion
if dt > 0:                       # <-- rama muerta: dt>0 siempre aqui
    self._cached_excitation = self._cached_excitation * math.exp(-self.beta * dt) + self.alpha
else:
    self._cached_excitation += self.alpha   # <-- inalcanzable
```
**Evidencia (medida sobre los 33.961 trades reales capturados):**
```
adausdt@trade  dt<=0 (early-return) = 78.2%
btcusdt@trade  dt<=0 (early-return) = 80.1%
ethusdt@trade  dt<=0 (early-return) = 88.0%
solusdt@trade  dt<=0 (early-return) = 86.1%
```
**Por que es un problema:** Binance sella los trades con milisegundos y una sola orden taker que barre
varios makers genera N trades con el **mismo** `T`. Esos son exactamente los eventos que un proceso de
Hawkes debe contar como cluster. El codigo los cuenta para `_event_times` (baseline sube) pero **no** para
la excitacion (numerador no sube) → el estimador esta sesgado *a la baja* precisamente durante los picos.
Ademas devuelve `self._result` **stale** (con el timestamp del evento anterior), que es lo que se copia a
`snap.hawkes` en `MicrostructureEngine.on_trade:980`.
**Fix:** usar `dt = max(timestamp - self._cached_excitation_time, 0.0)` y aplicar siempre
`excitation = excitation*exp(-beta*dt) + alpha` (con `dt=0` el decay es 1, que es lo correcto); eliminar la
rama muerta. Alternativamente desempatar los ms con un contador de secuencia.
**Verificado como:** leido + ejecutado (`an1.py` seccion D sobre `trades.pkl` real).

---

### [P1] MICRO-02 — En la ruta de BACKTEST el `spike_ratio` de Hawkes es la constante **1,500** en las 216.592 barras de los 4 simbolos: `is_spike` = 0 % y `should_filter_mr` = 0 % — el filtro de microestructura de MR nunca se ha ejercitado en ningun backtest
**Archivo:** `core/microstructure.py:998` (`on_bar` → `self._hawkes[symbol].on_event(timestamp, "bar")`), `core/microstructure.py:335-350`
**Evidencia (ejecutado, ruta exacta del backtester):**
```
BTC-USD  n=216592  spike_ratio medio=1.500 max=1.500  is_spike= 0.00%  should_filter_mr=0.000%
ETH-USD  n=216590  spike_ratio medio=1.500 max=1.500  is_spike= 0.00%  should_filter_mr=0.000%
SOL-USD  n=216589  spike_ratio medio=1.500 max=1.500  is_spike= 0.00%  should_filter_mr=0.000%
ADA-USD  n=216588  spike_ratio medio=1.500 max=1.500  is_spike= 0.00%  should_filter_mr=0.000%
```
**Por que sale 1,500 exacto (aritmetica, no casualidad):** con un evento por barra, `dt = 60 s`, luego
`excitation = old·e^(-2·60) + 0,5 = 0,5` (e^-120 ≈ 4e-53). En 300 s de ventana caben 5 eventos, y
`if events_in_window > 10` es **False** → `_adaptive_mu` se queda en `mu = 1,0`.
`baseline = max(0,2 · 1,0 ; 1,0) = 1,0` → `intensity = 1,5` → `spike_ratio = 1,5` **siempre**.
**Por que es un problema:** (a) `MicrostructureSnapshot.should_filter_mr` exige `spike_ratio >= 4,0`, asi que
el filtro que `risk_manager.validate_signal:148` aplica a Mean Reversion **es identicamente False en todo
backtest**; nunca se ha validado. (b) `main.py:1046-1049` y `main.py:1188-1189` imprimen
`"Hawkes medio: 1.50x / Hawkes max: 1.50x"` como si fuese una medicion — es una constante del codigo.
(c) La correlacion de `spike_ratio` con cualquier cosa es indefinida (scipy: `ConstantInputWarning`).
**Fix:** en `on_bar`, registrar N eventos sinteticos proporcionales a los trades de la barra (las klines de
Binance traen la columna `trades`) repartidos en el minuto, o marcar Hawkes como no disponible en backtest
en vez de emitir un valor falso.
**Verificado como:** ejecutado (`an2.py` seccion B, 216.592 barras × 4) + aritmetica sobre el codigo.

---

### [P1] MICRO-03 — VPIN tira a la basura el lado del agresor que ya tiene: `VPINCalculator.on_trade` usa la tick rule sobre un flujo donde el 62-93 % de los trades no mueve el precio → VPIN vivo = 0,05-0,16 y `is_toxic` **nunca** se dispara (0,00 % en 33.961 trades reales)
**Archivo:** `core/microstructure.py:119-131` y `core/microstructure.py:958-975`
**Evidencia (codigo — el `is_buy` real llega y solo se pasa a Kyle):**
```python
def on_trade(self, symbol, price, quantity, timestamp, is_buy=None) -> None:
    vpin_result = self._vpin[symbol].on_trade(price, quantity, timestamp)      # <-- sin is_buy
    hawkes_result = self._hawkes[symbol].on_event(timestamp, "trade")
    kyle_result = self._kyle_lambda[symbol].on_trade(price, quantity, timestamp, is_buy=is_buy)
```
**Evidencia (medida sobre los trades reales; `is_buy = not m` es el lado agresor exacto de Binance):**
```
                 dp>0    dp<0    dp==0    tick-rule acierta (de los NO-flat)
adausdt@trade    4.0%    4.6%    91.0%          97.4%
btcusdt@trade   19.3%   18.5%    61.7%          98.2%
ethusdt@trade   10.1%   11.2%    78.3%          98.4%
solusdt@trade    2.5%    3.6%    93.3%          95.5%

VPIN resultante (bucket real de settings.py, toxic_threshold=0.8):
btcusdt   tick-rule  medio=0.1619 [0.117,0.235]  toxic= 0.00%
          agresor    medio=0.7965 [0.628,0.911]  toxic=45.40%
ethusdt   tick-rule  medio=0.0751 [0.025,0.138]  toxic= 0.00%
          agresor    medio=0.8335 [0.699,0.923]  toxic=85.04%
solusdt   tick-rule  medio=0.0570 [0.011,0.106]  toxic= 0.00%
          agresor    medio=0.8240 [0.739,0.919]  toxic=67.14%
adausdt   tick-rule  medio=0.0503 [0.012,0.096]  toxic= 0.00%
          agresor    medio=0.7200 [0.618,0.784]  toxic= 0.00%
```
**Por que es un problema:** la tick rule *acierta* cuando se puede aplicar (95-98 %), pero solo se puede
aplicar al 7-38 % de los trades; el resto se reparte 50/50, lo que **cancela artificialmente** el
desequilibrio y comprime VPIN un orden de magnitud. Con el umbral de `config/settings.py:52`
(`vpin_toxic_threshold = 0.8`) el flag `is_toxic` es **estructuralmente inalcanzable en produccion**.
Y si se arregla el clasificador sin recalibrar el bucket (MICRO-06), pasa al extremo opuesto: 45-85 % toxico.
Las dos configuraciones estan mal; el estimador no discrimina en ninguna.
**Fix:** pasar `is_buy` a `VPINCalculator.on_trade` (una linea) **y** recalibrar `bucket_size` a ADV/50
simultaneamente; sin lo segundo el fix empeora las cosas.
**Verificado como:** leido + ejecutado (`an1.py` secciones A y C sobre 33.961 trades reales con el flag `m`).

---

### [P1] MICRO-04 — Backtest y live ven microestructuras OPUESTAS: `risk_score > 0.5` (el recorte de tamano del risk manager) se dispara en el **95-99,7 %** de las barras de backtest y solo en el **3,8-9,9 %** de los trades en vivo
**Archivo:** `core/microstructure.py:897-901`, `risk/risk_manager.py:154-158`, `core/microstructure.py:145-169` (`on_bar`) vs `:106-143` (`on_trade`)
**Evidencia (codigo del gate):**
```python
# risk/risk_manager.py:154-158
if micro.risk_score > 0.5:
    size_factor = 1.0 - micro.risk_score * 0.3
    signal.size_usd *= max(size_factor, 0.4)
```
**Evidencia (medida, mismo codigo, dos rutas):**
```
RUTA BACKTEST (on_bar, 216.592 barras/simbolo)   RUTA LIVE (on_trade, trades reales)
BTC  risk_score>0.5 = 99.72%  is_toxic=19.60%    BTC  risk_score>0.5 = 7.69%  is_toxic=0.00%
ETH  risk_score>0.5 = 98.36%  is_toxic= 7.54%    ETH  risk_score>0.5 = 3.76%  is_toxic=0.00%
SOL  risk_score>0.5 = 97.26%  is_toxic= 0.79%    SOL  risk_score>0.5 = 8.62%  is_toxic=0.00%
ADA  risk_score>0.5 = 95.14%  is_toxic= 2.33%    ADA  risk_score>0.5 = 9.89%  is_toxic=0.00%
```
**Por que es un problema:** el mismo `MicrostructureSnapshot.risk_score` alimenta el mismo `if` en los dos
motores. En backtest recorta el tamano **casi siempre** (factor medio ≈ 0,79-0,81 → ~20 % menos notional);
en vivo **casi nunca**. Cualquier calibracion de sizing hecha sobre un backtest se ejecutara en produccion
con posiciones ~25 % mayores que las testeadas, en silencio. Es una rotura de paridad backtest↔live
adicional a las de `backtest_parity`, y **con signo desfavorable** (live arriesga mas).
Causa raiz: `on_bar` clasifica con `(close-low)/(high-low)` y `on_trade` con la tick rule; son estimadores
distintos con distribuciones distintas (medias 0,61-0,71 vs 0,05-0,16).
**Fix:** un unico clasificador para las dos rutas (lado agresor real: en vivo el flag `m`, en backtest
`taker_buy_quote/quote_volume`, que ya esta en los parquet de `data/binance_futures/`).
**Verificado como:** ejecutado en las dos rutas con la config real (`Settings().get_microstructure_config()`).

---

### [P1] MICRO-05 — Kyle Lambda esta ~6-7 ordenes de magnitud por debajo de los umbrales que lo consumen: `impact_stress` medido ≤ 4,2e-4 contra umbrales de 0,5 (reduce tamano) y 2,0 (bloquea) → el bloqueo por impacto, el recorte por impacto y el multiplicador de gamma A-S son codigo muerto
**Archivo:** `core/microstructure.py:636-641` (`impact_stress`, linea 641), `risk/risk_manager.py:160-173`, `core/microstructure.py:530-533`, `config/settings.py:143`
**Evidencia (docstring vs realidad):**
```python
# core/microstructure.py:640  -> "Normalizar: lambda_ema de 0.5 bps/$ es normal, >2 bps/$ es stress"
return min(self.kyle_lambda_ema / 2.0, 2.0)
```
```
Medido sobre los trades reales (mismo estimador, misma config de settings.py):
adausdt  lambda_ema medio=1.788e-04 bps/USD  max=8.371e-04   impact_stress medio=8.94e-05 max=4.19e-04
btcusdt  lambda_ema medio=1.633e-07 bps/USD  max=1.780e-06   impact_stress medio=8.17e-08 max=8.90e-07
ethusdt  lambda_ema medio=4.182e-07 bps/USD  max=6.965e-06   impact_stress medio=2.09e-07 max=3.48e-06
solusdt  lambda_ema medio=1.392e-05 bps/USD  max=1.016e-04   impact_stress medio=6.96e-06 max=5.08e-05
Sobre 216.592 klines (ruta backtest): BTC 3.19e-07, ETH 5.39e-07, SOL 3.09e-06, ADA 3.67e-05 bps/USD.
estimate_impact(500 USD) en BTC = 2.2e-09 bps.
```
**Por que es un problema:** "0,5 bps por dolar" significaria que **1 USD** de notional mueve BTC 0,5 bps
(≈ $3,9). El valor economico correcto es ~1,6e-7 bps/USD (un millon de dolares ≈ 0,16 bps), y el codigo
esta calibrado como si fuese 3 millones de veces mayor. Consecuencias verificadas:
`risk_manager.py:165` (`impact_stress >= 2.0` → bloquear) y `:171` (`> 0.5` → recortar) **nunca** se
cumplen; `microstructure.py:531-533` (`lambda_mult = 1 + min(impact_stress,1)*0.5`) multiplica gamma por
1,0000001; `estimate_impact()` devuelve ~0 para cualquier tamano y su resultado se inyecta en
`sig.metadata["kyle_lambda_bps"]` (`main.py:606`) para el smart router y el paper simulator.
Ademas `estimate_impact` mezcla unidades: usa `lambda_ema` (bps/USD) como si fuese un coeficiente en bps.
**Fix:** normalizar por un notional de referencia (`impact_stress = lambda_ema * REF_NOTIONAL / 2`, con
`REF_NOTIONAL` = tamano tipico de orden del bot, p.ej. 500 USD) y volver a fijar los umbrales sobre esa
escala; o retirar los gates hasta tener una calibracion.
**Verificado como:** ejecutado en las dos rutas + lectura de los tres consumidores.

---

### [P1] MICRO-06 — 01-F22 sigue abierto y ahora esta cuantificado: el bucket VPIN dura **0,25-3,0 s** y la ventana completa de 50 buckets **12,6-151 s**; el bucket canonico (ADV/50) es 676-7.617 veces mayor
**Archivo:** `config/settings.py:50-52,201,206,212,218`, `core/microstructure.py:137,164`
**Evidencia (medida con el flujo real de hoy):**
```
                bucket_size   flujo real      1 bucket   ventana 50 buckets   ADV/50 canonico   ratio
adausdt@trade    5.000 USD     1.650 USD/s     3,030 s        151,50 s          $3,38M          676x
btcusdt@trade   50.000 USD    99.464 USD/s     0,503 s         25,13 s        $224,55M        4.491x
ethusdt@trade   30.000 USD   118.977 USD/s     0,252 s         12,61 s        $228,53M        7.618x
solusdt@trade   15.000 USD    21.846 USD/s     0,687 s         34,33 s         $51,39M        3.426x
```
**Por que es un problema:** VPIN (Easley, Lopez de Prado & O'Hara 2012) se define sobre buckets de ~1/50 del
volumen **diario**; su lectura es "desequilibrio persistente del flujo informado en horas". Con 0,25-0,7 s
por bucket, un bucket contiene ~13-26 trades y muy a menudo **una sola orden taker barriendo el libro**, de
modo que `|buy-sell|/total ≈ 1` por construccion. Eso es exactamente lo que muestra la medicion con el lado
agresor real (VPIN 0,72-0,83 de media): no es toxicidad, es granularidad. El estimador no mide lo que su
nombre dice a esta escala.
**Fix:** `bucket_size = quoteVolume_24h / 50` leido en el arranque desde `fapi/v1/ticker/24hr` y refrescado
a diario, o eliminar VPIN (ver `MICRO-08` y el Veredicto).
**Verificado como:** ejecutado (REST ticker/24hr + captura WS de 178 s) + aritmetica.

---

### [P1] MICRO-07 — `MicrostructureEngine.on_trade` consume **16,5 % de un core de forma permanente** (979 us/trade × 190,6 trades/s) dentro del unico event loop, para producir senales que estan muertas; el 67 % de ese coste es `KyleLambdaEstimator` y un tercio de este es un `sorted()` de 500 elementos por trade
**Archivo:** `core/microstructure.py:769-775`, `:754-762`, `main.py:288-311`
**Evidencia (replay de los 4 simbolos con la config real, mediana de 3 repeticiones):**
```
replay: 33961 trades de 4 simbolos en 178.1 s de reloj (190.6 trades/s)
CPU consumido por MicrostructureEngine.on_trade: 22.16 s para 134.2 s de mercado
  ==> DUTY CYCLE = 16.5% de un core, permanentemente, solo microestructura
  ==> 979 us por trade de media

MicrostructureEngine.on_trade (VPIN+Hawkes+Kyle)   896.0 us   17.02% de un core a 190 trades/s
  VPINCalculator.on_trade                          198.3 us    3.77%
  HawkesEstimator.on_event                          88.4 us    1.68%
  KyleLambdaEstimator.on_trade                     659.0 us   12.52%

cProfile de KyleLambdaEstimator.on_trade (3000 trades, 2.219 s totales):
   ncalls  tottime  funcion
     2982    0.655   {built-in method builtins.sorted}      <-- 30% del coste
    14910    0.293   {built-in method numpy.array}
     2982    0.278   numpy ... cov()
     2982    0.190   numpy ... _var()
```
**Evidencia (codigo del hotspot):**
```python
# core/microstructure.py:769-775 — se ejecuta en CADA trade
self._lambda_history.append(lambda_bps)
if len(self._lambda_history) > 50:
    sorted_hist = sorted(self._lambda_history)      # O(n log n) sobre 500 elementos, 190 veces/s
    p99 = sorted_hist[int(len(sorted_hist) * 0.99)]
    p01 = sorted_hist[int(len(sorted_hist) * 0.01)]
```
**Por que es un problema:** ese loop tambien atiende, por cada tick, `market_data.on_trade`,
`paper_sim.on_price_update` (SL/TP de todas las posiciones abiertas) y el cierre de barra con
`Indicators.compute_all` (MICRO-09). 16,5 % es la medida en este escritorio; el hardware de despliegue es
mas lento (la ronda 1 midio `compute_all` en 169 ms donde aqui son 45,9 ms, ≈3,7×), lo que situaria el duty
cycle en el entorno del 60 % de un core **solo** para microestructura. Y las salidas de ese gasto son:
`is_toxic` 0 %, `is_spike` inalcanzable, `should_filter_mr` 0 %, `impact_stress` ~1e-7, A-S nunca calculado.
**Fix inmediato barato:** winsorizar con `numpy.partition` o mantener percentiles incrementales
(quita ~30 % de Kyle); recalcular lambda cada N trades en vez de cada trade; reutilizar arrays numpy.
**Fix correcto:** ver Veredicto (archivar el modulo).
**Verificado como:** ejecutado (`perf2.py`/`perf3.py`, mediana de 3 repeticiones, gc desactivado, con los
trades reales capturados y `Settings().get_microstructure_config()`).

---

### [P1] MICRO-08 — RESPUESTA A LA PREGUNTA CENTRAL: la microestructura no discrimina nada. IC direccional ≤ 0,012 en 4 horizontes; y el VPIN de barra es un proxy **inverso** de la volatilidad (rho = −0,60 en BTC), o sea que marca "toxico" cuando el mercado esta tranquilo
**Archivo:** `core/microstructure.py:145-169` (`on_bar`), `:897-901` (`risk_score`), `:886-894` (`should_filter_mr`)
**Evidencia (216.592 barras × 4 simbolos = 150 dias, Spearman):**
```
Poder DIRECCIONAL sobre el retorno futuro (bps), horizontes 1/5/30/60 min:
  BTC  vpin   IC(ret) = -0.0003 / +0.0026 / +0.0028 / +0.0076
  ETH  vpin   IC(ret) = -0.0017 / -0.0005 / -0.0109 / -0.0117
  SOL  vpin   IC(ret) = -0.0014 / +0.0009 / -0.0022 / -0.0029
  ADA  vpin   IC(ret) = -0.0018 / -0.0037 / -0.0055 / +0.0040
  (rscore da EXACTAMENTE lo mismo: risk_score = vpin cuando hawkes es constante 1.5)
  (ratio: IC indefinido — la serie es constante, ver MICRO-02)

Relacion con la VOLATILIDAD futura |ret| — con el SIGNO EQUIVOCADO:
  BTC  IC(|ret|) = -0.3814 / -0.3555 / -0.3483 / -0.3331   (p = 0.0)
  ETH  IC(|ret|) = -0.3259 / -0.3138 / -0.2961 / -0.2909
  SOL  IC(|ret|) = -0.1972 / -0.1985 / -0.1941 / -0.1808
  ADA  IC(|ret|) = -0.1484 / -0.1693 / -0.1689 / -0.1626

Causa mecanica (Spearman VPIN_t vs rango de la vela en bps):
  BTC -0.5969   ETH -0.5412   SOL -0.3457   ADA -0.3137     (contemporaneo)
  BTC -0.5934   ETH -0.5392   SOL -0.3489   ADA -0.2988     (vela siguiente)
```
**Por que es un problema:** `(close-low)/(high-low)` es la *close location value*, no BVC. En una vela de
rango pequeno el cierre cae casi siempre en un extremo → `buy_pct → 0 o 1` → imbalance ≈ 1 → **VPIN alto**;
en una vela de rango grande el cierre queda mas al centro → VPIN bajo. El indicador que el risk manager usa
para "reducir tamano por riesgo de microestructura" es, medido, un indicador de **calma**. Actua al reves de
como esta documentado, con rho ≈ −0,6.
Calidad del clasificador de barra contra el lado agresor REAL (`taker_buy_quote/quote_volume`):
```
BTC  corr(proxy, real)=+0.5327  MAE=0.2771  acierto de signo=70.7%   real sd=0.2007  proxy sd=0.3898
ETH  corr=+0.4834  MAE=0.2683  signo 68.3%    SOL corr=+0.4858  MAE=0.2493  signo 68.8%
ADA  corr=+0.3999  MAE=0.2889  signo 65.9%
```
El proxy tiene casi el doble de dispersion que la magnitud real y un error absoluto medio de 0,27 sobre una
variable acotada en [0,1] — y el dato correcto (`taker_buy_quote`) **ya esta en los parquet que se usan**.
**Fix:** ver Veredicto. Si se conserva algo, usar `taker_buy_quote/quote_volume` en backtest y el flag `m`
en vivo, y volver a medir el IC antes de conectar nada al sizing.
**Verificado como:** ejecutado (`an2.py` secciones A/B/C, 866.359 observaciones en total, scipy.spearmanr).

---

### [P2] MICRO-09 — 01-F18 sigue abierto: `Indicators.compute_all` sobre 2000 filas cuesta 45,9 ms aqui y corre dentro del callback WS; los 4 simbolos cierran barra en el mismo segundo → 184 ms de event loop bloqueado cada minuto
**Archivo:** `core/market_data.py:400-406`, `core/indicators.py`, `main.py:296`
**Evidencia (ejecutado, mediana de 7):**
```
compute_all(2000 filas): mediana 45.9 ms  min 42.2  max 48.4
  x4 simbolos en el mismo segundo de cierre de minuto = 184 ms de event loop bloqueado
```
**Por que es un problema:** durante esos 184 ms (≈680 ms extrapolando al hardware de despliegue, ver
MICRO-07) no se procesan los ~190 trades/s ni los 37 depth/s; en paper eso retrasa los checks de SL/TP por
tick, en live retrasa fills y depth. El fix propuesto en r1 (vectorizar el percentil, calcular solo la
ultima fila, o `run_in_executor`) sigue sin aplicarse.
**Verificado como:** ejecutado.

---

### [P2] MICRO-10 — 8 de los 16 streams suscritos (`@kline_1m` y `@markPrice@1s` × 4) no entregan **ni un solo mensaje** en 178 s, y ademas `@kline_1m` no tiene ningun consumidor registrado en `main.py`
**Archivo:** `exchange/binance_ws.py:69-78`, `:158-179`, `main.py:314,333-334,408-409`
**Evidencia (medida, tres pruebas independientes):**
```
1) Captura 178 s de los 16 streams del bot: 40.542 mensajes, 0 de kline_1m, 0 de markPrice@1s.
2) Conexion aislada /stream?streams=btcusdt@markPrice@1s          -> 0 mensajes en 20 s
   Conexion aislada /stream?streams=btcusdt@kline_1m              -> 0 mensajes en 20 s
   Conexion aislada /stream?streams=btcusdt@trade/btcusdt@markPrice@1s -> 73 trades, 0 markPrice
3) /ws + SUBSCRIBE + LIST_SUBSCRIPTIONS:
   {"result":null,"id":1}
   {"result":["btcusdt@markPrice@1s","btcusdt@kline_1m","btcusdt@trade"],"id":2}
   -> el servidor ACEPTA la suscripcion y aun asi no emite nada por esos dos canales.
```
Y en `main.py` los unicos callbacks registrados son:
```
main.py:314  self.websocket.on("trade", on_market_trade)
main.py:333  self.websocket.on("depth", on_depth_update)
main.py:334  self.websocket.on("depthUpdate", on_depth_update)
main.py:408  self.websocket.on("markPrice", on_markprice_update)
main.py:409  self.websocket.on("markPriceUpdate", on_markprice_update)
```
**no hay `on("kline", ...)` ni `on("kline_1m", ...)`** — `binance_ws._process_message:158-179` construye y
emite el evento a un conjunto vacio de callbacks.
**Por que es un problema:** (a) `@kline_1m` es ancho de banda y parseo gastados por definicion, tenga o no
entrega. (b) `markPrice` SI tiene consumidor (`funding_rate` y `mark_price` del snapshot), y al no llegar,
el filtro de funding de `risk_manager.py:180-197` depende exclusivamente del REST cada 30 s de
`_data_refresh_loop`, que ademas **reemplaza el objeto snapshot** (01-F23) y pisa lo que hubiera escrito el
handler. (c) No se loguea nunca que un stream suscrito no emita: el bot no puede distinguir "sin funding
extremo" de "sin datos de funding".
**Nota de honestidad:** no he podido determinar la causa raiz de la no-entrega (la suscripcion se acepta y
`@trade`/`@depth20` funcionan en la misma conexion); puede ser especifico de esta IP/region. El hallazgo
accionable es doble e independiente de la causa: **quitar `@kline_1m`** (no tiene consumidor) y
**anadir un watchdog por stream** que avise si un canal suscrito lleva N segundos sin mensajes.
**Verificado como:** ejecutado (3 capturas WS reales + `LIST_SUBSCRIPTIONS`) + grep de callbacks.

---

### [P2] MICRO-11 — 01-F16 sigue abierto y comprobado: `seed_from_binance` guarda la **vela en formacion** como barra cerrada, y a partir de ahi las barras live quedan etiquetadas 60 s por delante de las sembradas (el mismo minuto aparece dos veces)
**Archivo:** `core/market_data.py:129,142-150,161-162`, `:359-411`
**Evidencia (ejecutado contra el REST real, 16:02:11 UTC):**
```
REST /fapi/v1/klines?limit=5:
  open_t=15:59:00 close_t=15:59:59  cerrada=True   vol=76.950
  open_t=16:00:00 close_t=16:00:59  cerrada=False  vol=14.174   <-- vela EN FORMACION

seed_from_binance(hours=1) -> 60 barras. Ultimas 3:
   timestamp    open    high     low   close  volume
1788192000.0 78543.1 78568.0 78509.5 78534.2 126.251
1788192060.0 78534.2 78580.0 78490.9 78580.0  99.015
1788192120.0 78580.0 78596.7 78579.9 78596.6   6.891   <-- solo 11 s de mercado (vol 6.9 vs ~100)

  _last_bar_time = 1788192120 (16:02:00)   ahora = 1788192131 (16:02:11)
Tras inyectar ticks WS, la primera barra live sale con ts=1788192180 (16:03:00) y contiene los ticks
del minuto que ABRE a las 16:02 -> el minuto 16:02 esta partido en dos filas (16:02:00 parcial + 16:03:00).
```
**Por que es un problema:** la fila parcial entra en `Indicators.compute_all` como si fuese una vela real
(ATR/BB/RSI/EMA contaminados con un rango truncado) y se queda ahi hasta salir por `MAX_BARS`. Ademas
`_close_bar` etiqueta con `last_bar + interval` (hora de cierre) mientras el seed etiqueta con `k[0]`
(hora de apertura): dentro del **mismo DataFrame** conviven las dos convenciones separadas por un bar.
**Fix:** descartar la ultima kline si `close_time >= now*1000` (`k[6]`), y unificar la convencion de
etiquetado (usar siempre la hora de apertura: `bar_open_ts = last_bar`).
**Verificado como:** ejecutado (REST real + `MarketDataCollector` real con ticks sinteticos).

---

### [P2] MICRO-12 — 01-F28 sigue abierto: si el seed REST falla, las barras 1m quedan desalineadas del reloj **para siempre**
**Archivo:** `core/market_data.py:347-349`
**Evidencia (codigo):**
```python
# Si es el primer tick y no hay last_bar, inicializar
if last_bar == 0:
    self._last_bar_time[symbol] = ts        # ts = timestamp del trade, no alineado
```
**Evidencia (ejecutado, primer tick en t=…016.969):**
```
timestamps generados: [1788192076.969, 1788192136.969, 1788192196.969]
ts % 60 =            [16.969, 16.969, 16.969]     <- deberia ser 0
```
**Por que es un problema:** las "barras 1m" no corresponden a ningun minuto de reloj; el remuestreo
posicional a 5m/15m de las estrategias (01-F17) hereda el desfase, y ninguna comparacion con klines de
Binance (backtest, grafico, validacion) es valida. El seed falla en cualquier corte de red al arrancar.
**Fix:** `self._last_bar_time[symbol] = ts - (ts % self.bar_interval)`.
**Verificado como:** ejecutado con el `MarketDataCollector` real.

---

### [P2] MICRO-13 — El Avellaneda-Stoikov es codigo muerto en produccion y el bridge publica sus ceros como si fuesen medidas; ademas `sigma` se valida y se guarda pero **no entra en ninguna formula**
**Archivo:** `core/microstructure.py:482-613`, `:1019-1055`, `main.py:512-513`, `server/serializers.py:152-158`, `backtesting/backtester.py:985-986`
**Evidencia:** `compute_as_spread` no tiene **ningun** llamador fuera de `archive/strategies/market_making.py`
(grep sobre todo el repo excluyendo `build/`, `target/`, `binaries/`, `archive/`: 0 resultados en `main.py`
y 0 en `backtesting/backtester.py`). Por tanto `snap.avellaneda_stoikov` es siempre el `ASResult()` por
defecto y estos tres sitios emiten ceros como dato:
```python
# main.py:512-513          "as_spread_bps": micro.avellaneda_stoikov.spread_bps,   -> 0.0 siempre
# backtester.py:985-986    idem, en microstructure_history
# server/serializers.py:152-158
a_s = getattr(micro, "avellaneda_stoikov", None)
if a_s:                                   # un dataclass SIEMPRE es truthy
    result["as_spread"] = {"bid_spread_bps": ..., "reservation_price": ...}   # 0.0 / 0.0
```
Y dentro de `compute()`, `sigma` aparece exactamente tres veces: en la firma (`:487`), en el guard
(`:508 if mid_price <= 0 or sigma <= 0`) y en el resultado (`:608 sigma=sigma`). Ni el precio de reserva
(`:551`, usa `atr`) ni el spread (`:555-561`, usa `atr_bps`) lo usan. Es un A-S sin volatilidad.
**Por que es un problema:** el UI/desktop muestra `reservation_price = 0` y `spread = 0 bps` como si fuese
el estado del motor; y si algun dia se reactiva MM, la "volatilidad" que el usuario configure no hara nada.
**Fix:** borrar el A-S del snapshot y del serializador mientras MM este archivado (o emitir `null`), y si se
recupera, o se usa `sigma` en el termino `0.5·gamma·sigma²·(T−t)` o se quita el parametro.
**Verificado como:** leido + grep exhaustivo de llamadores.

---

### [P2] MICRO-14 — 01-F20 sigue abierto literal: el microprice compuesto se sigue clampeando a `[bid, ask]`, contra la leccion registrada (Audit #24)
**Archivo:** `core/microprice.py:228-233`
**Evidencia:**
```python
microprice_adjusted = microprice_ml + intensity_adjustment + obi_adjustment
# Clamp: no puede salir del bid-ask spread
microprice_adjusted = max(best_bid, min(best_ask, microprice_adjusted))
microprice_l1 = max(best_bid, min(best_ask, microprice_l1))
microprice_ml = max(best_bid, min(best_ask, microprice_ml))
```
**Por que es un problema:** el clamp de L1 y ML es correcto por construccion (son medias convexas de bid y
ask, ya estan dentro). El unico que puede salir del spread es `adjusted`, que es justamente el que lleva la
informacion de `intensity` y `obi_delta`: clamparlo **elimina exactamente la senal que se le anade**.
La leccion de la auditoria #24 dice lo contrario y no se ha aplicado. Impacto hoy limitado (solo alimenta
`sig.metadata["microprice"]` para el smart router), pero es una regresion contra una decision registrada.
**Fix:** clampear L1/ML (redundante pero inocuo) y dejar `adjusted` libre con un limite amplio, p.ej.
`mid ± 2·spread`.
**Verificado como:** leido (codigo + `tasks/lessons.md`).

---

### [P2] MICRO-15 — El OBI se calcula una vez por ciclo de estrategia, no por actualizacion de libro: `delta` y `delta_5` no son momentum de libro sino diferencias entre ciclos, y ~97 % de los 37 depth/s parseados se tira
**Archivo:** `main.py:475`, `core/orderbook_alpha.py:150-160`, `main.py:317-331`
**Evidencia:** `self.obi[symbol].compute(...)` se invoca solo dentro de `_evaluate_symbol`; el callback
`on_depth_update` solo hace `market_data.on_orderbook(symbol, ob)` (sobrescribe el libro) y **no** llama a
`compute`. El historial de OBI (`_imbalance_history`) por tanto avanza a la cadencia del ciclo de
estrategia, no a los 100 ms del stream.
```python
# core/orderbook_alpha.py:150-160
self._imbalance_history.append(weighted_imbalance)
delta   = weighted_imbalance - history[-2]    # "-2" = ciclo anterior de estrategia
delta_5 = weighted_imbalance - history[-6]    # "-6" = 5 ciclos atras
```
**Por que es un problema:** `delta` se documenta como "cambio de imbalance (momentum de presion)" y se usa
como confirmacion de senal y como entrada de `microprice.compute(obi_delta=...)`. Medido sobre el flujo
real, llegan 9,3-9,7 depth/s por simbolo: entre dos calculos de OBI se descartan decenas de libros. Ademas
`on_depth_update` paga 39,4 us de parseo por mensaje (10 niveles × 2 lados) para un dato que casi siempre se
sobrescribe antes de leerse.
**Fix:** o calcular el OBI dentro de `on_depth_update` (y entonces `delta` sí es momentum de 100 ms), o
reducir la suscripcion a `@depth5@100ms`/`@bookTicker` y dejar claro en el nombre que es un nivel, no un delta.
**Verificado como:** leido + medido (9,68 depth/s en BTC; 39,4 us por parseo de `OrderBook`).

---

### [P2] MICRO-16 — `adverse_selection_bps` mide el **markout favorable**, no la seleccion adversa (signo invertido), y en paper nunca se alimenta porque `register_fill` solo existe en la ruta live
**Archivo:** `core/microstructure.py:813-828`, `main.py:349-353`, `main.py:645-680`
**Evidencia (signo):**
```python
# AS: positive = price moved against us (adverse selection cost)
# Buy (sign=+1): price went up -> we got picked off -> AS positive
as_bps = (current_price - fill_price) / fill_price * 10_000 * sign
...
adverse_selection_bps=max(0.0, as_bps),
```
Para una compra (`sign=+1`) que sube de precio, `as_bps > 0`. Pero comprar y que suba es el resultado
**favorable**: la seleccion adversa es comprar justo antes de que caiga. La formula calcula el markout
`(P_T − P_fill)·side` y lo etiqueta como coste; el `max(0.0, ...)` remata el error quedandose solo con los
fills **buenos** y descartando los malos.
**Evidencia (paper):** `register_fill` tiene un unico llamador, `main.py:350`, dentro de
`on_order_update` (evento `ORDER_TRADE_UPDATE` de Binance). `_process_paper_fill` (`main.py:645+`) **no** lo
llama → en paper `_pending_fills` esta siempre vacio y `adverse_selection_bps` es 0 constante, y eso es lo
que `server/serializers.py:166` publica al UI.
**Fix:** `as_bps = -(current_price - fill_price)/fill_price*1e4*sign` (o renombrar el campo a `markout_bps`
y quitar el `max(0,·)`), y llamar a `register_fill` tambien desde `_process_paper_fill`.
**Verificado como:** leido + grep de llamadores.

---

### [P3] MICRO-17 — `_event_times` (maxlen 10.000) satura con una ventana de 300 s: en ETH el baseline se queda clavado en 33,33 ev/s frente a los 105,3 ev/s reales, en silencio
**Archivo:** `core/microstructure.py:305,335-341`
**Evidencia (medida):**
```
ethusdt@trade  |event_times|=10000 (maxlen=10000)  adaptive_mu=33.33 ev/s   (tasa real 105.30/s)
btcusdt@trade  |event_times|= 9203 (maxlen=10000)  adaptive_mu=30.67 ev/s   (tasa real  51.66/s, muestra<300s)
```
Con `window_sec=300` haria falta `maxlen >= rate*300` = 31.590 para ETH; el deque descarta por la izquierda
antes de que lo haga el `while ... popleft()` de la ventana, asi que `events_in_window` se satura en 10.000
y `_adaptive_mu` en `10000/300 = 33,33` exactamente.
**Por que es un problema:** el baseline de Hawkes queda topado 3,2× por debajo del real en ETH; en un
mercado mas rapido el sesgo crece. Es un limite silencioso que no aparece en ningun log.
**Fix:** contar eventos con un histograma por segundo (300 contadores) en vez de guardar timestamps, o
dimensionar `maxlen` en funcion de la tasa observada.
**Verificado como:** ejecutado sobre los trades reales.

---

### [P3] MICRO-18 — El docstring dice "Bulk Volume Classification" pero ninguna de las dos rutas implementa BVC
**Archivo:** `core/microstructure.py:64,70-74,119-131,148-158`
**Evidencia:** `on_trade` implementa la **tick rule** de Lee-Ready (`price > last_price` → compra);
`on_bar` implementa la *close location value* `(close-low)/(high-low)`. BVC (Easley/Lopez de Prado/O'Hara)
es `V_buy = V · Z((P_t − P_{t−1}) / sigma_dP)` con Z la CDF normal estandarizada por la volatilidad de los
cambios de precio del bucket — no aparece en el codigo.
**Por que es un problema:** el nombre justifica la eleccion de parametros y el umbral 0,8; quien lea el
codigo asume una literatura que no se esta aplicando. Ademas explica por que las dos rutas divergen
(MICRO-04): son dos estimadores distintos, no dos implementaciones del mismo.
**Fix:** implementar BVC de verdad, o renombrar honestamente (`_classify_tick_rule` / `_classify_clv`).
**Verificado como:** leido + contrastado con la definicion del paper.

---

### [P3] MICRO-19 — `HawkesEstimator.get_intensity_at` usa `self.mu` (config) en vez del baseline adaptativo que usa `on_event`; el unico llamador es un script roto
**Archivo:** `core/microstructure.py:374-381`, `scripts/quant_audit.py:151`
**Evidencia:**
```python
def get_intensity_at(self, timestamp: float) -> float:
    ...
    return self.mu + excitation          # on_event usa max(self.mu*0.2, self._adaptive_mu)
```
**Por que es un problema:** dos definiciones distintas de "intensidad" en la misma clase; cualquier
comparacion entre ambas es incoherente. Impacto real ~0 (solo lo usa `scripts/quant_audit.py`, que la
ronda 1 ya reporto como runner roto).
**Fix:** devolver `max(self.mu*0.2, self._adaptive_mu) + excitation`.
**Verificado como:** leido + grep.

---

### [P3] MICRO-20 — `save_snapshot` / `get_history` y `MicrostructureEngine._history` son codigo muerto (0 llamadores); el `deepcopy` que contienen nunca se ejecuta
**Archivo:** `core/microstructure.py:922,1094-1105`
**Evidencia:** grep de `save_snapshot|get_history` en todo el repo excluyendo `build/`, `target/`,
`binaries/`, `archive/`: solo las definiciones. `self._history` se inicializa por simbolo y nunca crece.
**Por que es un problema:** menor — pero incluye un `copy.deepcopy(snap)` por llamada y un truncado a 50.000
que se presenta como proteccion de memoria de un camino que no existe.
**Nota positiva:** por esto mismo, **no hay fuga de memoria en este modulo**. Todas las estructuras vivas
son `deque` con `maxlen` (VPIN: 150 buckets + 500 historial; Hawkes: 10.000 + 500; Kyle: window + 200 + 500
+ 500; microprice 500; OBI 50). Extrapolado a 24 h con 190 trades/s el consumo es **constante**, no creciente.
**Fix:** borrar los tres.
**Verificado como:** leido + grep.

---

### [P3] MICRO-21 — `binance_ws` copia el campo `T` de `markPriceUpdate` como si fuese el tiempo del evento, cuando en el payload de Binance `T` es `nextFundingTime`
**Archivo:** `exchange/binance_ws.py:185-191`
**Evidencia:**
```python
mark_data = {
    "s": symbol,
    "p": data.get("p", "0"),        # mark price
    "r": data.get("r", "0"),         # funding rate
    "T": data.get("T", int(time.time() * 1000)),   # <-- en markPriceUpdate T = nextFundingTime
    "e": "markPriceUpdate",
}
```
El payload de `markPriceUpdate` de USDT-M es `{"e","E","s","p","i","P","r","T"}` donde `E` es el event time
y `T` es el **next funding time** (horas en el futuro). El evento normalizado que emite `binance_ws` pierde
`E` y expone `T` en la posicion donde el resto de handlers (`trade`, `depth`) ponen el tiempo del evento.
**Por que es un problema:** hoy inocuo (`on_markprice_update` en `main.py:386-406` solo lee `s`, `r` y `p`),
pero es una trampa: cualquier consumidor futuro que haga `ts = data["T"]/1000` obtendra un timestamp en el
futuro y, por ejemplo, romperia el guard `dt <= 0` de Hawkes o el `data_age`.
**Fix:** `"E": data.get("E", ...)` para el tiempo de evento y renombrar `T` a `next_funding_time`.
**Verificado como:** leido + contrastado con el esquema del payload (no he podido capturar un
`markPriceUpdate` en vivo, ver MICRO-10; por eso queda en P3).

---

## Lo que esta BIEN (y no hay que tocar)

| Punto | Verificado |
|---|---|
| Estabilidad de Hawkes: `alpha=0.5 < beta=2.0`, branching ratio `alpha/beta = 0,25 < 1` (subcritico) y el `ValueError` del constructor lo garantiza | leido + aritmetica |
| El kernel exponencial recursivo `exc = exc·e^(−β·dt) + α` es la forma analitica correcta y O(1) | leido |
| Sin fugas de memoria: todas las estructuras del modulo son `deque(maxlen=…)`; consumo constante a 24 h | leido + inventario completo (MICRO-20) |
| Fix `b`/`a` del depth (02-P1-04): correcto, verificado contra el payload real `{"b":[["78014.20","5.687"],…]}` | ejecutado (captura WS) |
| Stale-tick guard con override tras 5 rechazos consecutivos y `_last_data_time` actualizado **solo** tras aceptar el tick | leido (`market_data.py:252-275,327-332`) |
| Microprice L1 = formula de Stoikov correcta (`ask·I + bid·(1−I)`, `I = bid_qty/(bid_qty+ask_qty)`) | leido |
| `bar_interval = 60` documentado y coherente con el remuestreo interno de las estrategias | leido |
| Sin bloqueos por locks: no hay `Lock`/`Semaphore` en el modulo; todo el estado es de un solo hilo | grep |

---

## Tabla resumen

| ID | Sev | Archivo:linea | Titulo |
|---|---|---|---|
| MICRO-01 | P1 | core/microstructure.py:325 | Hawkes descarta la excitacion en el 78-88 % de los trades (`dt<=0` early-return) |
| MICRO-02 | P1 | core/microstructure.py:998 | En backtest `spike_ratio` es la constante 1,500 → `should_filter_mr` nunca probado |
| MICRO-03 | P1 | core/microstructure.py:119,967 | VPIN ignora el lado agresor real → `is_toxic` 0,00 % en vivo |
| MICRO-04 | P1 | core/microstructure.py:897 / risk_manager.py:155 | `risk_score>0.5`: 95-99,7 % en backtest vs 3,8-9,9 % en vivo (sizing ~25 % mayor en live) |
| MICRO-05 | P1 | core/microstructure.py:641 / risk_manager.py:160-173 | Kyle Lambda 6-7 ordenes por debajo de sus umbrales → gates muertos |
| MICRO-06 | P1 | config/settings.py:50,201-219 | Bucket VPIN = 0,25-3,0 s (canonico ADV/50 es 676-7.618× mayor) — 01-F22 |
| MICRO-07 | P1 | core/microstructure.py:772 | 16,5 % de un core permanente; `sorted()` de 500 por trade |
| MICRO-08 | P1 | core/microstructure.py:145,897 | Sin poder predictivo (IC≤0,012) y con el signo invertido en volatilidad (rho −0,60) |
| MICRO-09 | P2 | core/market_data.py:400 | `compute_all` 45,9 ms en el callback WS → 184 ms/min bloqueados — 01-F18 |
| MICRO-10 | P2 | exchange/binance_ws.py:69-78,158 | `@kline_1m` sin consumidor; `kline`/`markPrice` sin entrega y sin watchdog |
| MICRO-11 | P2 | core/market_data.py:142-162 | Seed guarda la vela en formacion; convencion de timestamp incoherente — 01-F16 |
| MICRO-12 | P2 | core/market_data.py:347 | Sin seed, barras 1m desalineadas del reloj para siempre — 01-F28 |
| MICRO-13 | P2 | core/microstructure.py:482 / serializers.py:152 | A-S muerto en produccion, ceros publicados al UI, `sigma` sin usar |
| MICRO-14 | P2 | core/microprice.py:228-233 | Clamp del microprice compuesto — 01-F20 (regresion abierta) |
| MICRO-15 | P2 | main.py:475 / orderbook_alpha.py:150 | OBI a cadencia de estrategia: `delta` no es momentum; 97 % del depth se tira |
| MICRO-16 | P2 | core/microstructure.py:813 | `adverse_selection_bps` con signo invertido y nunca alimentado en paper |
| MICRO-17 | P3 | core/microstructure.py:305 | `_event_times` satura: baseline clavado en 33,33 ev/s en ETH |
| MICRO-18 | P3 | core/microstructure.py:64 | El docstring dice BVC; el codigo hace tick-rule y close-location-value |
| MICRO-19 | P3 | core/microstructure.py:374 | `get_intensity_at` usa `mu` en vez del baseline adaptativo |
| MICRO-20 | P3 | core/microstructure.py:1094 | `save_snapshot`/`get_history`/`_history`: codigo muerto |
| MICRO-21 | P3 | exchange/binance_ws.py:189 | `T` de `markPriceUpdate` es `nextFundingTime`, no el tiempo de evento |

---

## Veredicto (10 lineas)

1. **La microestructura no aporta nada medible y hay que archivarla.** Sobre 216.592 barras × 4 simbolos
   (150 dias reales), el IC direccional de VPIN y `risk_score` es ≤ 0,012 en horizontes de 1 a 60 minutos.
2. Peor que "cero": el VPIN de barra tiene rho = **−0,60** con el rango de la vela, o sea que marca
   "toxico" cuando el mercado esta **tranquilo**. Es un indicador de calma disfrazado de indicador de riesgo.
3. Los tres gates que consumen el modulo estan muertos, medidos: `should_filter_mr` = **0,000 %** en las dos
   rutas, `is_toxic` = **0,00 %** en vivo, `impact_stress` ≤ 4e-4 contra umbrales de 0,5 y 2,0.
4. Y el unico gate que sí actua lo hace al reves de como se cree: `risk_score>0.5` recorta el tamano en el
   **95-99,7 %** de las barras de backtest y en el **3,8-9,9 %** en vivo — un sesgo de ~25 % de sizing
   *desfavorable en produccion*, silencioso, que contamina cualquier calibracion hecha sobre backtests.
5. El precio de todo esto es **16,5 % de un core, permanentemente** (979 us × 190,6 trades/s), medido con el
   flujo real, dentro del mismo event loop que atiende los SL/TP por tick.
6. Hawkes esta roto en las dos rutas por razones distintas: en vivo pierde el 78-88 % de la excitacion por el
   `dt<=0`; en backtest es la constante aritmetica 1,500 en las 216.592 barras. Nunca ha medido nada.
7. La conclusion es la misma que la tanda 1 saco de Mean Reversion, y por la misma via: no es que falte
   calibrar, es que **los filtros no discriminan** y encima cuestan CPU y crean divergencia backtest↔live.
8. **Recomendacion:** congelar `core/microstructure.py` como esta congelado el resto (dejar de llamar a
   `on_trade` desde el callback WS y a `on_bar` desde el backtester), quitar los tres gates de
   `risk_manager.validate_signal` y `@kline_1m` de la suscripcion. Eso libera un sexto de core y elimina la
   divergencia de sizing sin perder ninguna senal, porque no hay ninguna senal que perder.
9. Lo que sí merece arreglarse aunque se archive el modulo, porque afecta a los **datos**: MICRO-11 (vela en
   formacion sembrada como cerrada), MICRO-12 (barras desalineadas sin seed) y MICRO-09 (184 ms/min de loop
   bloqueado). Son bugs de calidad de datos y de latencia que sobreviven a la microestructura.
10. Si algun dia se recupera la idea, el orden correcto es: (a) un unico clasificador con el lado agresor
    real (`m` en vivo, `taker_buy_quote` en backtest), (b) `bucket_size = ADV/50`, (c) medir el IC **antes**
    de conectar nada al sizing. Hoy no se cumple ninguno de los tres.
