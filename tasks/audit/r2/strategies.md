# Auditoría R2 — Estrategias y señal

**Área:** `strategies/mean_reversion.py`, `strategies/fibonacci_retracement.py`, `strategies/base.py`, `core/indicators.py`, `core/regime_detector.py`, `core/market_data.py`
**Fecha:** 2026-08-31 · **Auditor:** agente r2/strategies
**Método:** lectura línea a línea + réplica vectorizada **validada contra las clases reales** y ejecutada sobre `data/binance_futures/klines/<SYM>/1m.parquet` (216.590 barras 1m = **149,7 días** por símbolo, Binance Futures).

### Validación de la herramienta de medida (antes de creerme ningún número)

| Componente | Validación | Resultado |
|---|---|---|
| `h1fast.h1_series` vs `MeanReversionStrategy._update_h1_trend` (ventana live 2000 barras) | 250 índices aleatorios reales | **250/250 exacto** (`trend` idéntico, `adx` rtol 1e-9) |
| `m5fast.build_phase_frames` vs `_resample_5m` (última barra) | 120 índices aleatorios reales | máx. dif. relativa **3e-5** (ADX), 1e-6 RSI/ATR, 0 en OHLC |
| Réplica completa de entradas vs `MeanReversionStrategy.generate_signals` real | 6.000 barras 1m reales de ETH (índices 100.000-106.000), ventana live 2000 | **48 señales vs 48 señales, conjunto de índices IDÉNTICO** |

Todo lo cuantitativo de este informe sale de esa réplica o de llamadas directas a las clases reales.

**Alcance de verificación de ronda 1:** F04, F05, F06, F10, F11, F16, F17 (ninguno figura en `tasks/audit/fixes_round1.md` → se comprueba uno a uno si siguen abiertos).

---

## Hallazgos

### [P0] strategies-01 — Mean Reversion (la ÚNICA estrategia con capital) no tiene edge bruto: 150 días de datos reales dan PF 0.40-0.60 y −10,5 a −13,1 bps netos por trade con t-stat −5 a −8,7

**Archivo:** `strategies/mean_reversion.py:1-22` (docstring), `:200-247` (lógica de entrada), `config/settings.py:96` (`allocation_mean_reversion = 0.50`)

**Evidencia** (réplica exacta validada, 149,7 días, coste 11 bps round-trip = config real `taker 4×2 + slippage 1.5×2`; salidas = SL/TP de exchange intrabar + trailing software tal cual corre en producción):

| símbolo | trades | trades/día | WR | **avg BRUTO** | **avg NETO** | PF | t-stat | mix de salidas |
|---|---|---|---|---|---|---|---|---|
| ETH-USD | 637 | 4,25 | 43,2% | **−0,90 bps** | **−11,90 bps** | 0,454 | −8,40 | SL 314 / TRAIL 282 / TP 21 |
| SOL-USD | 554 | 3,70 | 48,6% | **−0,63 bps** | **−11,63 bps** | 0,540 | −6,01 | TRAIL 266 / SL 256 / TP 18 |
| ADA-USD | 531 | 3,55 | 53,3% | **−2,05 bps** | **−13,05 bps** | 0,597 | −4,97 | TRAIL 269 / SL 229 / TP 16 |
| BTC-USD | 562 | 3,75 | 37,4% | **+0,45 bps** | **−10,55 bps** | 0,402 | −8,72 | SL 283 / TRAIL 238 / TP 27 |

```python
# strategies/mean_reversion.py:21  (docstring)
TARGET: Breakeven to slightly positive while accumulating live data for OFM validation.
```

**Por qué es un problema:** el retorno **bruto** medio por trade es estadísticamente indistinguible de cero (|avg gross| ≤ 2 bps con SE 1,2-2,6 bps). Es decir: la señal (tendencia 1H + RSI 5m + 2 confirmaciones) **no aporta nada**; lo único que queda es la asimetría SL/TP y el trailing, que en neto son ruido. El coste de 11 bps es entonces un impuesto puro. Con los 3 símbolos activos (ETH+SOL+ADA) son **11,5 trades/día × ~12 bps**; con el notional real que permite el cap de apalancamiento (~$325 en RANGING, ver 01-F13) eso es **≈ $4/día ≈ $120/mes sobre una cuenta de $1.000 (12%/mes)**. El docstring promete "breakeven to slightly positive": los datos dicen PF 0,40-0,60. Es exactamente el mismo veredicto que `tasks/research_r2_trend_evidence.md` ("MR intradía sin edge tras costes"), pero MR sigue con el 50% de la asignación mientras Fibonacci (que en BTC tenía PF 1,11) fue congelado.

**Fix:** congelar MR igual que Fibonacci (`allocation_mean_reversion = 0.00`, `REGIME_WEIGHTS[*][MEAN_REVERSION] = 0.00`, `SYMBOL_STRATEGY_MAP` → `set()`), o al menos exigir antes de descongelar: (a) edge BRUTO > 3× SE en OOS, (b) puerta `atr_bps >= 2 × cost_bps` y `net_rr >= 1.5`, (c) reparar F04/F05/F11/F17 y re-medir. Ningún ajuste de SL/TP arregla un edge bruto nulo.

**Verificado cómo:** ejecutado — réplica validada 48/48 contra la clase real; 2.284 trades simulados sobre 149,7 días de klines reales de Binance Futures.

---

### [P1] strategies-02 — 01-F05 SIGUE ABIERTO y es peor de lo estimado: el "filtro clave de tendencia 1H" deja pasar el **99,1-99,4%** de las barras sobre datos reales

**Archivo:** `strategies/mean_reversion.py:47,153-158,200-204,447-480`, `core/indicators.py:115-134`, `core/market_data.py:23`

**Evidencia** (llamadas reales a `_update_h1_trend`, 840 muestras por símbolo repartidas por todas las fases, ventana live 2000 barras = 33 barras 1H):

```
[ETH-USD] win= 2000 (33 barras 1H) | ADX media=41.58 p10=26.54 p50=39.68 | pct(ADX>=20)= 99.5% | pct(trend==0)=0.0%
[SOL-USD] win= 2000 (33 barras 1H) | ADX media=40.86 p10=26.50 p50=38.77 | pct(ADX>=20)= 99.6% | pct(trend==0)=0.0%
[ADA-USD] win= 2000 (33 barras 1H) | ADX media=41.08 p10=26.64 p50=38.72 | pct(ADX>=20)= 99.8% | pct(trend==0)=0.0%
[BTC-USD] win= 2000 (33 barras 1H) | ADX media=41.27 p10=26.80 p50=39.38 | pct(ADX>=20)= 99.9% | pct(trend==0)=0.0%
```

Sobre el conjunto completo (216.590 barras) la puerta `h1_trend != 0 and not (h1_adx < 20)` pasa el **99,1% (ETH) / 99,4% (SOL) / 99,3% (ADA) / 99,2% (BTC)**.

Causa: `Indicators.adx` arranca el EWM (`span=2*period-1`, `adjust=False`) en la primera barra, donde `DX≈100` (solo un DM ≠ 0). Con 33 barras el peso residual del arranque es `(13/14)^32 ≈ 0,091` → +9 puntos de sesgo permanente. Con 100 barras 1H la mediana baja a **24,95** y el pass-rate a **69,9%**.

```python
# core/indicators.py:132-134
dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
return dx.ewm(span=2 * period - 1, adjust=False).mean()   # arranca en DX[1] ~ 100
```

**Por qué es un problema:** el comentario del código dice literalmente *"This is the key filter that turns losing MR into breakeven+"*. Con un pass-rate del 99,3% el ADX no filtra nada; lo único que hace el bloque 1H es fijar la **dirección** con `EMA12>EMA26` sobre 33 barras reiniciadas (donde EMA26 aún carga ~8% del primer close). Y `pd.NA < 20` es `False`: un ADX NaN también pasa (mi réplica lo reproduce con `~(h1adx < 20)`, y coincide 48/48 con la clase real, luego el comportamiento NaN→pasa está confirmado).

**Fix:** (1) mantener un DataFrame 1H propio sembrado con `GET /fapi/v1/klines?interval=1h&limit=300` (no re-cortar 1m con `MAX_BARS=2000`); (2) `Indicators.adx/atr/rsi` con seeding de Wilder o `min_periods=3*period` devolviendo NaN en warmup, y tratar NaN como "sin tendencia"; (3) recalibrar `ADX_MIN_TREND` contra la distribución real (mediana 25 con 100 barras → un umbral de 20 sigue dejando pasar el 70%).

**Verificado cómo:** ejecutado (`_update_h1_trend` real, 4 símbolos × 840 muestras × 3 tamaños de ventana) + lectura.

---

### [P1] strategies-03 — NUEVO: ruptura de paridad backtest↔live en el filtro 1H: el backtest usa **8 barras horarias** (ADX medio 86, pasa el 100%) y su dirección **discrepa de live el 35% del tiempo**

**Archivo:** `backtesting/backtester.py:366-367`, `strategies/mean_reversion.py:449-465`, `core/market_data.py:23`

**Evidencia:**

```python
# backtesting/backtester.py:366-367  (run_backtest)
window_start = max(0, i - 500)
df_slice = df.iloc[window_start:i + 1]        # 501 barras 1m
# mean_reversion._update_h1_trend:  tail(60*100) -> n = 501//60*60 = 480 -> 8 barras 1H
```

Mismo instante, mismo símbolo (ETH-USD, última barra del parquet), cambiando solo el tamaño de la ventana `df`:

```
df=  501 -> 8 barras 1H  : h1_trend=+1  h1_adx=97.30
df= 2000 -> 33 barras 1H : h1_trend=-1  h1_adx=47.25     <-- LIVE
df= 6000 -> 100 barras 1H: h1_trend=-1  h1_adx=26.31     <-- run_full_backtest (df.iloc[:i+1])
```

Sobre 840 muestras por símbolo:

| símbolo | ADX medio (501 / 2000 / 6000) | pct(ADX≥20) | **acuerdo de dirección 501 vs 2000** |
|---|---|---|---|
| ETH | 85,95 / 41,58 / 27,49 | 100% / 99,5% / 69,9% | **65,0%** |
| SOL | 85,75 / 40,86 / 26,73 | 100% / 99,6% / 68,3% | **65,9%** |
| ADA | 85,75 / 41,08 / 27,21 | 100% / 99,8% / 68,2% | **64,8%** |
| BTC | 85,83 / 41,27 / 27,49 | 100% / 99,9% / 75,4% | **64,2%** |

**Por qué es un problema:** los PF que justifican la asignación de capital están escritos en el código —`SYMBOL_STRATEGY_MAP`: `"ETH-USD": {MEAN_REVERSION}  # MR PF=0.85`, `"ADA-USD": ... # MR PF=0.86`— y salen de `run_backtest`, que corre MR con un filtro direccional que **acierta el signo de producción solo 2 de cada 3 veces** y con un ADX que nunca filtra. Hay además una tercera configuración (`run_full_backtest` usa `ohlcv_df.iloc[:i+1]`, prefijo completo → 100 barras 1H). Tres motores, tres estrategias distintas. Cualquier número de backtest sobre MR es inservible como evidencia mientras la ventana no sea la de producción.

**Fix:** que el backtester alimente exactamente `MAX_BARS=2000` (constante compartida importada de `core.market_data`), y añadir un test de paridad que compare `_update_h1_trend` con la ventana del backtester y con la de live sobre el mismo bar.

**Verificado cómo:** ejecutado (mismas clases reales, 3 tamaños de ventana, 4 símbolos) + lectura de los dos motores de backtest.

---

### [P1] strategies-04 — 01-F04 SIGUE ABIERTO: `bars_held` es **0 para siempre**; el stale-exit de 24 h y el trailing "tight" son código muerto verificado

**Archivo:** `strategies/mean_reversion.py:288,342-343,371-385`, `strategies/fibonacci_retracement.py:249-253,470-471,503-507`

**Evidencia** (clase real, ventana live 2000 barras, ETH-USD real):

```
len(m5) at entry = 200  -> MRState.entry_bar_idx = 200
  +  60 barras 1m ( 1.0 h) -> len(m5)=200  bars_held = 0   (esperado 12)
  + 300 barras 1m ( 5.0 h) -> len(m5)=200  bars_held = 0   (esperado 60)
  + 600 barras 1m (10.0 h) -> len(m5)=200  bars_held = 0   (esperado 120)
  +1500 barras 1m (25.0 h) -> len(m5)=200  bars_held = 0   (esperado 300)
stale_bars threshold = 288  -> stale_position_24h NUNCA puede dispararse
```

```python
# mean_reversion.py:288
entry_bar_idx=len(self._resampled.get(symbol, pd.DataFrame())),   # siempre 200
# mean_reversion.py:342-343
current_bar_count = len(self._resampled.get(symbol, pd.DataFrame()))  # siempre 200
bars_held = current_bar_count - state.entry_bar_idx                   # siempre 0
```

**Por qué es un problema:** las tres cosas que dependen de `bars_held` están muertas: `TRAIL_TIGHT_AFTER_BARS=20` (nunca se estrecha el trailing), `stale_position_24h` (nunca libera capital) y, en Fibonacci, `MAX_IMPULSE_AGE=30` (`impulse_age = 133 − bar_idx_end ∈ [1,20] < 30` por aritmética directa: `bar_idx_end = (len−20)+idx` con `idx ∈ [0,19]`). Resultado real medido: hay trades abiertos **127-137 barras 5m** (>10 h) sin que ninguna gestión temporal actúe; solo salen por SL/TP/trailing. Es la misma clase de bug que `lessons.md` (Audit #26) ya documentó ("contador ≠ barras") y que se "arregló" cambiando `eval_counter` por `len()`.

**Fix:** guardar el timestamp de la barra de entrada. `_resample_5m` ni siquiera propaga `timestamp` al frame 5m (`groupby(...).agg({...})` solo lleva OHLCV) → hay que añadirlo (`"timestamp": "first"`) y calcular `bars_held = (ts_now − ts_entry) / (RESAMPLE_MINUTES*60)`.

**Verificado cómo:** ejecutado (clase real `_resample_5m` alimentada con +60/+300/+600/+1500 barras 1m reales) + aritmética sobre `RESAMPLE_BUFFER`.

---

### [P1] strategies-05 — 01-F06 SIGUE ABIERTO: la única "puerta de coste" mata el **0,33%** de las señales; el **6,6%** de las que pasan tienen R:R neto < 1 (mínimo 0,23)

**Archivo:** `strategies/mean_reversion.py:267-271`, `config/settings.py:104-107`

**Evidencia** (réplica validada, ETH-USD, 149,7 días):

```python
# mean_reversion.py:267-271
rt_cost = price * 14 / 10000          # 14 bps hardcoded
net_profit = tp_mult * atr - rt_cost
if net_profit <= 0:
    return signals                    # solo exige TP > coste
```

```
config real: taker_fee=0.0004  slippage_bps=1.5  -> round trip = 11.0 bps  (el codigo usa 14)
senales que llegan a la puerta: 1815 | eliminadas por la puerta: 6 (0.33%)
R:R NETO de las supervivientes: min=0.23  p10=1.14  mediana=1.67
supervivientes con R:R neto < 1.0 (la perdida es mayor que la ganancia): 6.6%
```

**Por qué es un problema:** `tp_mult*atr` es 4×20 bps = 80 bps frente a un coste de 11-14 bps, así que la puerta nunca muerde. Lo que falta es la comparación con la **pérdida** neta: `net_rr = (tp·atr − c)/(sl·atr + c)`. El 6,6% de las entradas se abren con la pérdida esperada mayor que la ganancia esperada, y el peor caso es 0,23:1 (necesitaría 81% de acierto). Fibonacci sí tiene esa puerta (`net_reward/net_risk < 1.5 → return`); MR no. Además el coste está hardcodeado a 14 bps en dos estrategias en vez de leerse de `TradingConfig` (11 bps con la config actual), lo que hace que ninguna simulación de coste sea consistente con la ejecución.

**Fix:** `cost_bps = trading_config.taker_fee*2*1e4 + trading_config.slippage_bps*2`; `net_rr = (tp_mult*atr − price*cost_bps/1e4) / (sl_mult*atr + price*cost_bps/1e4)`; `if net_rr < 1.5: return`; añadir la puerta `atr_bps >= 2*cost_bps` (regla ya escrita en `lessons.md`); loggear `net_rr` en metadata. **Nota honesta:** medí el efecto de ambas puertas (ver strategies-01) y **no rescatan la estrategia** — el neto sigue siendo −11,5 a −12,7 bps. Son correcciones de higiene, no un arreglo del edge.

**Verificado cómo:** ejecutado (réplica validada + contra-factuales con las puertas activadas) + lectura de config.

---

### [P1] strategies-06 — Tras CADA reinicio, MR opera **27,3 horas** con un filtro 1H calculado sobre 6→33 barras horarias (ADX medio 90 → 41, pasa el 100%)

**Archivo:** `main.py:175` (`seed_from_binance(..., hours=6)`), `core/market_data.py:23` (`MAX_BARS = 2000`), `strategies/mean_reversion.py:449`

**Evidencia** (clase real `_update_h1_trend`, 30 muestras por tamaño, ETH real):

```
df= 360 ( 6 barras 1H) ADX media= 89.89  pct(ADX>=20)=100.0%   <-- justo tras arrancar
df= 600 (10 barras 1H) ADX media= 77.93  pct(ADX>=20)=100.0%
df= 900 (15 barras 1H) ADX media= 66.54  pct(ADX>=20)=100.0%
df=1200 (20 barras 1H) ADX media= 57.80  pct(ADX>=20)=100.0%
df=2000 (33 barras 1H) ADX media= 40.89  pct(ADX>=20)=100.0%   <-- regimen estacionario
minutos hasta llenar MAX_BARS=2000 desde un seed de 6h: 1640 min = 27.3 h
```

```python
# mean_reversion.py:449
if len(df) < 60 * 6:  # Need 6 hours minimum (matches Binance seed)
    self._h1_trend[symbol] = 0
```

**Por qué es un problema:** el mínimo (`60*6`) está puesto para coincidir con el seed, o sea que MR empieza a operar **exactamente** en el punto de máximo sesgo: 6 barras 1H, `EMA26` que es prácticamente el primer close, y ADX ≈ 90. Y el `dd`/`deploy` reinicia el motor en cada actualización (`deploy/update.sh`), así que ese régimen degradado se repite en cada despliegue. Durante 27 horas la "dirección de la tendencia" es esencialmente el signo del movimiento de las últimas 3-6 horas.

**Fix:** no operar hasta tener N barras 1H reales (mín. 3×`period` = 42) obtenidas de `interval=1h` por REST en el arranque; y separar el buffer 1H del buffer 1m (`MAX_BARS`).

**Verificado cómo:** ejecutado (clase real con df de 360…2000 barras, 30 ventanas por tamaño) + lectura de `main.py`/`market_data.py`.

---

### [P2] strategies-07 — 01-F11 SIGUE ABIERTO: los umbrales RSI adaptativos por volatilidad son código muerto (columna inexistente **y** escala equivocada)

**Archivo:** `strategies/mean_reversion.py:206-221`, `core/indicators.py:96-112,223`

**Evidencia:**

```python
# mean_reversion.py:210
vol_pctile = float(bar.get("volatility_percentile", 50))   # la columna NO existe -> 50 SIEMPRE
if vol_pctile > 70:   ...   elif vol_pctile < 30:   ...
# indicators.py:223
df["vol_pct"] = Indicators.volatility_percentile(close)    # otro nombre, y rango 0..1
```

Ejecutado sobre el frame 5m real que produce `_resample_5m`:
`'volatility_percentile' in m5.columns → False` · `vol_pct` rango medido **0.0 – 1.0**.

**Por qué es un problema:** doble bug (nombre + escala 0-1 vs 0-100). Aunque se renombrara la columna, `vol_pct ∈ [0,1]` nunca sería `> 70` ni `< 30` → la rama "low vol" se activaría el 100% del tiempo. El comentario del código promete umbrales por símbolo ("BTC 35/65, ETH 33/67, SOL/ADA 30/70") que **no existen en ninguna parte**: los tres símbolos usan 35/65. Lo que corre no es lo que documenta.

**Fix:** `vol_pctile = float(bar.get("vol_pct", 0.5)) * 100` y test que falle si la columna no existe (`assert "vol_pct" in bar.index`). Si de verdad se quieren umbrales por símbolo, deben vivir en `SymbolConfig`, no en un comentario.

**Verificado cómo:** ejecutado (`'volatility_percentile' in m5.columns`, rango de `vol_pct`) + lectura.

---

### [P2] strategies-08 — La gestión de salidas está denominada en el **ATR ACTUAL**, no en el de entrada: el SL software se ejecuta de media al **78%** de la distancia prometida (p10 = 63%)

**Archivo:** `strategies/mean_reversion.py:338,346-377`, `strategies/fibonacci_retracement.py:466,474-500`

**Evidencia:**

```python
# mean_reversion.py:338, 348-356  (_check_exit, cada evaluacion)
atr = float(m5.iloc[-1].get("atr", 0))                     # ATR de AHORA
pnl_atr = (price - position.entry_price) / atr             # denominador movil
if pnl_atr < -(state.sl_mult + 0.2): exit_reason = "software_sl_safety"
if pnl_atr >= state.tp_mult:          exit_reason = "software_tp_safety"
if pnl_atr > state.best_pnl_atr: state.best_pnl_atr = pnl_atr   # se compara con un best de otro ATR
```

Medido sobre los trades reales (ETH, 637 trades, 149,7 días):

```
SW_SL: n=18 | distancia de stop PREVISTA=52.1 bps | REALIZADA=39.3 bps | ratio medio=0.78
       ratio p10=0.63  p50=0.77  p90=0.95        (1.0 = el stop cae donde se prometio)
ATR(salida)/ATR(entrada): p10=0.84  p50=1.02  p90=1.17
TRAIL: give-back real medido en ATR de ENTRADA: p10=0.42  p50=0.60  (disenado 0.50)
       disparos con retroceso real <0.4 ATR de entrada (puro drift de ATR): 18/282 = 6.4% (ETH),
       20/266 = 7.5% (SOL), 16/269 = 5.9% (ADA); ATR(salida)/ATR(entrada) mediana 0.85 en esos
```

**Por qué es un problema:** `best_pnl_atr` acumula máximos calculados con denominadores distintos, así que no es una magnitud consistente. Si el ATR se contrae un 20% mientras la posición vive, el SL software se dispara a 0,8× la distancia prometida (medido: 78% de media, 63% en el decil malo) → se corta la posición antes de que el SL del exchange (precio fijo) actúe, con una pérdida que nadie dimensionó. En el otro sentido, una expansión de ATR >12% dispara un trailing "fantasma" en el 6-7% de las salidas por trailing. `state.sl_mult`/`tp_mult` ya se guardan en `MRState`: falta guardar también `entry_atr` y usarlo.

**Fix:** `MRState.entry_atr = atr` en la entrada y usar SIEMPRE `entry_atr` en `pnl_atr`, `best_pnl_atr`, trailing, SL/TP software y stale. Es la única forma de que el trailing signifique lo que dice el comentario ("Trail 0.5 ATR behind peak").

**Verificado cómo:** ejecutado (bucle de salidas de producción sobre 1.437 trades reales de 3 símbolos, comparando el retroceso en ATR de entrada vs. el de la barra de salida).

---

### [P2] strategies-09 — 01-F17 SIGUE ABIERTO: el "resampleo 5m/15m" es posicional y se re-corta en cada barra 1m; el 25-32% de las señales se repite en la barra siguiente

**Archivo:** `strategies/mean_reversion.py:428-445`, `strategies/fibonacci_retracement.py:535-560`

**Evidencia:**

```python
# mean_reversion.py:436-441
n = len(tail) // RESAMPLE_MINUTES * RESAMPLE_MINUTES
trim = tail.tail(n).copy().reset_index(drop=True)
groups = np.arange(len(trim)) // RESAMPLE_MINUTES      # rejilla anclada al ULTIMO 1m, no al reloj
```

Medido sobre las señales reales: `consecutive-bar signal runs: n=1246 media=1.45 max=8 pct(len>1)=31.5%` (ETH), 30.2% (SOL), 25.3% (ADA).

**Por qué es un problema:** no son barras 5m de reloj sino 5 series entrelazadas; el `bb_touch`, la mecha de rechazo y el RSI de "la última vela 5m" dependen del minuto en que se mire. La consecuencia medible es que casi un tercio de los setups persiste 2-8 barras 1m seguidas: el precio de entrada real es un punto arbitrario dentro de una ventana de 5-8 minutos, y el mismo evento se cuenta como varias "señales" en las métricas. **No es duplicidad de órdenes** (la entrada solo se evalúa cuando `new_bar_arrived`, es decir cada 60 s, y para entonces la posición ya es visible), así que no lo marco como riesgo de doble orden — pero sí invalida cualquier comparación con un backtest de 5m de reloj.

**Fix:** agrupar por `timestamp // (RESAMPLE_MINUTES*60)` y evaluar entradas solo al cerrar un grupo completo.

**Verificado cómo:** ejecutado (distribución de rachas de barras consecutivas con señal, 3 símbolos, 149,7 días) + lectura.

---

### [P2] strategies-10 — 01-F16 SIGUE ABIERTO (re-verificado hoy contra la API real): el seed de Binance guarda la vela EN FORMACIÓN como cerrada y mezcla dos convenciones de timestamp

**Archivo:** `core/market_data.py:141-162,383-390`

**Evidencia** (llamada REST real a `fapi.binance.com` hecha durante esta auditoría, 2026-08-31):

```
open_time=1788153840000 close_time=1788153899999 now=1788153986295 closed=True   close=2435.73
open_time=1788153900000 close_time=1788153959999 now=1788153986295 closed=True   close=2437.68
open_time=1788153960000 close_time=1788154019999 now=1788153986295 closed=False  close=2436.79   <-- EN FORMACION
seed guarda timestamp = open_time/1000 = 1788153960.0
```

```python
# market_data.py:144   seed:  timestamp = OPEN time
"timestamp": int(k[0]) / 1000,
# market_data.py:162
self._last_bar_time[symbol] = float(df["timestamp"].iloc[-1])   # = open time de la vela EN FORMACION
# market_data.py:384  live:  timestamp = CLOSE time
new_bar = {"timestamp": bar_close_ts, ...}       # bar_close_ts = last_bar + 60
```

**Por qué es un problema:** (1) la última fila del seed es una vela parcial tratada como cerrada → todos los indicadores (ATR/RSI/BB/ADX de 1m, 5m y 1H) arrancan contaminados; (2) el primer `_close_bar` etiqueta ese MISMO minuto con `open_time+60` → el minuto aparece dos veces con dos convenciones distintas; (3) cualquier arreglo de F04 basado en timestamps heredará el desfase de 60 s.

**Fix:** descartar el último kline si `close_time > now*1000`; fijar `_last_bar_time` al `close_time` del último cerrado; unificar la convención (recomendado: open time) también en `_close_bar` y `get_forming_bar`.

**Verificado cómo:** ejecutado (GET real a `fapi/v1/klines?limit=3` con comparación contra `time.time()`) + lectura.

---

### [P2] strategies-11 — NUEVO: los thresholds adaptativos del `RegimeDetector` se cachean por **reloj de pared** (15 s) → en backtest solo se recalculan **2 veces en 43.200 barras**

**Archivo:** `core/regime_detector.py:33-34,136-183`

**Evidencia:**

```python
# regime_detector.py:140-145
import time as _time
now = _time.monotonic()                       # reloj de PARED, no tiempo de barra
last = self._threshold_last_update.get(symbol, 0)
if cached and (now - last) < self._threshold_cache_sec:   # 15 s
    return cached
```

Ejecutado: bucle de 43.200 barras 1m reales de ETH con la ventana live (2000) →
`recálculos de threshold en TODO el bucle: 2` (el bucle tardó 23,1 s de reloj).
Mezcla de regímenes resultante: RANGING 58,4% · TRENDING_DOWN 20,0% · TRENDING_UP 19,9% · **BREAKOUT 1,7%**.

**Por qué es un problema:** (1) en cualquier backtest los percentiles adaptativos (`vol_low`, `vol_high`, `adx_trend`, `mom_threshold`) quedan **congelados** en los valores calculados en la primera barra → la serie de regímenes del backtest no es la que produciría live, y `REGIME_WEIGHTS` (que decide el capital) se alimenta de ella; (2) el resultado del backtest **depende de la velocidad de la máquina** (un equipo 2× más rápido recalcula la mitad de veces) → no es reproducible; (3) de paso, el dato útil: BREAKOUT (el único régimen que bloquea MR) es solo el **1,7%** del tiempo, así que `should_activate` no filtra prácticamente nada.

**Fix:** cachear por número de barras (`if bars_since_update < K`) o por el timestamp de la última barra, nunca por `monotonic()`. Es un patrón que se repite: MR/Fib ya tuvieron que añadir `backtest_mode` para el cooldown por la misma razón.

**Verificado cómo:** ejecutado (`RegimeDetector` real sobre 43.200 barras reales, contando identidades del dict de thresholds).

---

### [P2] strategies-12 — NUEVO: `has_obi` es **estructuralmente imposible** en backtest → el backtest exige 2 de 3 confirmaciones y live tiene 4 candidatas

**Archivo:** `backtesting/backtester.py:452-453,935-941`, `strategies/mean_reversion.py:236-244`, `strategies/fibonacci_retracement.py:291-300`

**Evidencia:**

```python
# backtester.py:452-453  (y equivalente en :915-916 cuando no hay parquet de orderbook)
snapshot.orderbook.bids = [OrderBookLevel(price - atr * 0.01, 1.0)]
snapshot.orderbook.asks = [OrderBookLevel(price + atr * 0.01, 1.0)]
```

Ejecutado con `OrderBookImbalance(levels=5, decay=0.5).compute(...)` sobre ese libro:
`weighted_imbalance = -0.0001` → `>0.05` False y `<-0.05` False → **`has_obi` es False en el 100% de los backtests**.

**Por qué es un problema:** en live hay 4 confirmaciones candidatas (BB touch, volumen seco, OBI, mecha) y en backtest solo 3, con `MIN_CONFIRMATIONS = 2` en ambos. Live es por construcción más permisivo que el backtest que lo validó: la tasa de señales live es estrictamente mayor. Sumado a strategies-03 (ventana 1H distinta), ningún PF de backtest describe el sistema que corre.

**Fix:** o inyectar un OBI sintético con distribución realista, o desactivar explícitamente la confirmación OBI en backtest y exigir el mismo número efectivo de confirmaciones en ambos caminos.

**Verificado cómo:** ejecutado (`OrderBookImbalance.compute` real sobre el libro sintético del backtester) + lectura de los dos motores.

---

### [P2] strategies-13 — NUEVO: la tabla de ATR del docstring de MR está **etiquetada como BTC pero son los números de ETH**, y sobre esa premisa se asignó Fibonacci a BTC

**Archivo:** `strategies/mean_reversion.py:4-11`, `strategies/fibonacci_retracement.py:23`

**Evidencia** (`Indicators.atr` real, 150 días, mediana de ATR14 en bps, ignorando warmup):

| símbolo | 1m | 5m | 15m | 1h |
|---|---|---|---|---|
| **BTC-USD** | **4,8** | **13,0** | **24,8** | 53,0 |
| ETH-USD | 6,6 | 17,5 | 33,4 | 70,7 |
| SOL-USD | 8,3 | 20,7 | 37,7 | 79,9 |
| ADA-USD | 12,5 | 27,5 | 49,2 | 100,1 |

```python
# mean_reversion.py:8-10
- BTC 1m ATR = 6bps (0.5x fees) — IMPOSSIBLE to profit
- BTC 5m ATR = 17bps (1.2x fees) — marginal
- BTC 15m ATR = 34bps (2.4x fees) — first viable timeframe
# fibonacci_retracement.py:23
TIMEFRAME: 15m (ATR ~34bps = 2.4x fees, first viable TF for BTC)
```

6/17/34 son exactamente ETH (6,6/17,5/33,4). BTC real es 4,8/13,0/**24,8**.

**Por qué es un problema:** la conclusión "15m es el primer timeframe viable para BTC" se apoya en un ATR un 37% mayor que el real. Con 24,8 bps y 11 bps de coste, BTC 15m está a 2,25× coste, no a 2,4× de un 34 bps inexistente; y con los 14 bps que el código usa, a 1,77×. Esa premisa es la que puso Fibonacci en BTC (`SYMBOL_STRATEGY_MAP`, hoy congelado). Una tabla mal etiquetada dirigió la asignación de capital.

**Fix:** regenerar la tabla con el script de medida y fijarla en un test (`test_atr_bps_by_timeframe`) para que no vuelva a divergir.

**Verificado cómo:** ejecutado (ATR14 real por TF sobre 216.590 barras 1m × 4 símbolos).

---

### [P2] strategies-14 — 01-F10 SIGUE ABIERTO (Fibonacci, hoy congelado): los impulsos nunca caducan y se re-detectan tras "consumirse"

**Archivo:** `strategies/fibonacci_retracement.py:240-253,352-353,389-450`

**Evidencia:**

```python
impulse = self._detect_impulse(symbol, m15, atr)
if impulse: self._impulses[symbol] = impulse        # None NO limpia el impulso viejo
current_bar_idx = len(m15)                          # constante 133 en live (F04)
impulse_age = current_bar_idx - active_impulse.bar_idx_end
if impulse_age > MAX_IMPULSE_AGE: ...               # bar_idx_end = (len-20)+idx, idx in [0,19]
                                                    # -> impulse_age in [1,20] < 30 SIEMPRE
self._impulses[symbol] = None                       # "Consume the impulse"
```

`len(m15)` medido con la clase real: 133 con la ventana live de 2000 (y 33 con la del backtester).

**Por qué es un problema:** por aritmética, `impulse_age` está acotado en [1,20] y `MAX_IMPULSE_AGE=30` es inalcanzable. Y como el impulso se identifica por su máximo/mínimo dentro de una ventana de 20 barras, tras "consumirlo" la siguiente barra vuelve a detectar el mismo swing → re-entrada en el mismo impulso fallido pasados 300 s. Severidad P2 (no P1) **solo** porque Fibonacci está congelado en las 3 puertas; si se descongela, vuelve a ser P1.

**Fix:** identificar el impulso por `(ts_swing_start, ts_swing_end)`, mantener `consumed_impulses[symbol]` y expirar por timestamp.

**Verificado cómo:** aritmética sobre `bar_idx_end` + ejecutado (`len(m15)` = 133/33 con la clase real).

---

### [P2] strategies-15 — NUEVO: el README describe un sistema que no existe (estrategia, timeframe, asignación, SL/TP y umbrales, todos incorrectos)

**Archivo:** `README.md:25,156-165`, `config/settings.py:95-99`, `strategies/mean_reversion.py:42-51`

**Evidencia:**

| README | Realidad en el código |
|---|---|
| "2 Active Strategies: Mean Reversion & **Order Flow Momentum**" | OFM está archivada (`allocation_order_flow_momentum = 0.00`) y **no existe en `strategies/`**; la segunda estrategia es Fibonacci (hoy congelada) |
| "Mean Reversion (**40%** allocation)" | `allocation_mean_reversion = 0.50` (y el peso real lo fija `REGIME_WEIGHTS`: 0.65/0.30/0.10/0.50) |
| "Timeframe: **15-minute** bars" | `RESAMPLE_MINUTES = 5` |
| "SL/TP: 1.5x / **3.0x** ATR" | `TP_ATR_MULT = 4.0`; SL por símbolo 1,5 / 1,8 / 2,0 |
| "RSI extremes (**<30/>70**)" | `RSI_OVERSOLD = 35`, `RSI_OVERBOUGHT = 65` |
| — (no se menciona) | El filtro de tendencia 1H, que es la puerta dominante |
| "**153** Tests Passing" | 112 hoy |

**Por qué es un problema:** es la documentación de cara al usuario y a cualquiera que retome el proyecto; describe una estrategia distinta en timeframe, umbrales, TP y asignación. Cualquier decisión tomada leyendo el README será errónea.

**Fix:** reescribir la sección "Strategies" desde `config/settings.py` + las constantes de módulo, y añadir un test que compare los números del README con los del código (o generar esa sección).

**Verificado cómo:** lectura contrastada README ↔ `settings.py` ↔ constantes de `mean_reversion.py` + conteo de tests.

---

### [P3] strategies-16 — `RegimeDetector.get_regime_confidence` lanza `TypeError` (slicing sobre `deque`); hoy es código muerto

**Archivo:** `core/regime_detector.py:189,209-216`

**Evidencia:**

```python
self._regime_history[symbol] = deque(maxlen=5)   # :189
...
history = self._regime_history.get(symbol, [])
last_3 = history[-3:]                            # :214  deque NO soporta slicing
```

Ejecutado: `RAISES: TypeError sequence index must be integer, not 'slice'`.
`grep get_regime_confidence` → 0 llamadas en código de producción (solo la definición y sus copias en `build/`).

**Por qué es un problema:** no rompe nada hoy porque nadie lo llama, pero es una mina: el primer consumidor (un panel de "confianza de régimen" en el desktop, por ejemplo) rompe el ciclo de estrategia. Además revela que el método nunca se probó.

**Fix:** `last_3 = list(history)[-3:]` y un test. O borrarlo si no se va a usar.

**Verificado cómo:** ejecutado (llamada real tras 4 `_smooth_regime`) + grep.

---

### [P3] strategies-17 — 01-F24 SIGUE ABIERTO: la metadata de la señal MR reporta los multiplicadores de módulo, no los del símbolo; y no lleva `net_rr`

**Archivo:** `strategies/mean_reversion.py:252-254,319-320`

**Evidencia** (señal real generada por la clase real, ETH-USD):

```python
sl_mult = sym_config.mr_atr_mult_sl   # :253  se USA esto para el SL
...
"sl_mult": SL_ATR_MULT,               # :319  pero se REPORTA la constante 1.5
"tp_mult": TP_ATR_MULT,               # :320  constante 4.0
```

```
metadata keys: ['adx_5m','atr','atr_bps','confirmations','h1_adx','h1_trend','has_bb_touch',
                'has_obi','has_rejection','has_vol_dry','obi','rsi_5m','sl_mult','tp_mult','trigger','zscore']
"action" in metadata: False | "net_rr" in metadata: False
```

**Por qué es un problema:** para SOL (`mr_atr_mult_sl = 1.8`) y ADA (`2.0`) la metadata miente: dice 1,5. Esa metadata alimenta `signal_features` en la trade DB y el análisis posterior, así que cualquier estudio "PnL vs distancia de stop" sobre SOL/ADA está mal etiquetado en origen. Falta `net_rr` (la métrica que decide si el trade tiene sentido) y no hay `action` en entradas — esto último es correcto (`is_exit_signal` devuelve False con `action` ausente, verificado en `execution/order_engine.py:79-89`), pero conviene fijarlo con un test.

**Fix:** `"sl_mult": sl_mult, "tp_mult": tp_mult, "net_rr": round(net_rr, 2), "cost_bps": cost_bps`.

**Verificado cómo:** ejecutado (señal real, inspección de `metadata`) + lectura.

---

### [P3] strategies-18 — Las señales de salida se dimensionan en USD con el `mark_price` y luego se re-dividen por el precio del snapshot → riesgo de resto de posición

**Archivo:** `strategies/mean_reversion.py:391`, `strategies/fibonacci_retracement.py:513`, `core/types.py:185-190`, `execution/order_engine.py:97`

**Evidencia:**

```python
# mean_reversion.py:391
size_usd = position.notional if position.notional > 0 else position.size * price
# core/types.py:189-190   notional usa MARK price
price = self.mark_price if self.mark_price > 0 else self.entry_price
return abs(self.size * price)
# order_engine.py:97      y aqui se divide por el precio del SNAPSHOT
size_units = signal.size_usd / price
```

**Por qué es un problema:** `size_units = size × mark_price / snapshot_price`. En Binance Futures el `markPrice` difiere del mid por basis/funding (típicamente 1-5 bps), y si `get_positions()` devolviera `markPrice=0` el fallback es `entry_price` → el error pasa a ser el movimiento completo del precio (hasta 80 bps con TP a 4 ATR). Con `reduceOnly` un exceso se recorta, pero un defecto deja un resto de posición **sin SL/TP** (las protectivas se cancelan/consumen). Es un rodeo innecesario: la posición ya tiene `size` en unidades.

**Fix:** añadir `metadata["close_units"] = position.size` y que `order_engine` use unidades directas cuando `is_exit_signal(signal)`.

**Verificado cómo:** lectura del camino completo señal → `execute_signal` (no reproducido en vivo: requiere un `markPrice` divergente real).

---

### [P3] strategies-19 — Código muerto y comentarios que contradicen al código en MR

**Archivo:** `strategies/mean_reversion.py:63-76,84-87,132-134,229-231,286-291`

**Evidencia:**

```python
self._eval_counter[symbol] = self._eval_counter.get(symbol, 0) + 1   # :133
eval_count = self._eval_counter[symbol]                              # :134  NUNCA se usa
# ── CONFIRMATION (at least 1 INDEPENDENT signal) ────────           # :229
if confirmations < MIN_CONFIRMATIONS:                                # :245  MIN_CONFIRMATIONS = 2
TF_CONFIGS: Dict[str, TFConfig] = {...}                              # :73  "Legacy compatibility"
entry_time=now,                                                      # :287 MRState.entry_time nunca se lee
```

**Por qué es un problema:** ruido que despista al leer y que ya causó un bug (`eval_counter` fue el contador que se sustituyó por `len()` en el fix de Audit #26 y que generó F04). El comentario dice "al menos 1 confirmación independiente" y el código exige 2.

**Fix:** borrar `_eval_counter`, `TF_CONFIGS`/`TFConfig` (o marcarlos deprecados con `grep` previo), `MRState.entry_time` (o usarlo para `bars_held`), y corregir el comentario.

**Verificado cómo:** lectura + grep.

---

### [P3] strategies-20 — `TradingConfig.allocation_*` NO gobierna el capital (solo `REGIME_WEIGHTS` + `SYMBOL_STRATEGY_MAP`), pero es lo que se muestra al usuario

**Archivo:** `config/settings.py:94-102`, `portfolio/portfolio_manager.py:118-126,181-214,289-305`, `server/bridge.py:1393-1397`, `server/serializers.py:206-210`

**Evidencia:** `grep allocation_mean_reversion` → solo `_current_weights` (inicialización que `get_allocation:214` sobrescribe), el `print` de `main.py:1637`, `scripts/live_monitor.py` y los serializadores del bridge/desktop. El capital real sale de `REGIME_WEIGHTS[regime][strategy] × perf × dd × 1/num_symbols`.

**Por qué es un problema (y crítica al cambio reciente fb073a1):** el congelado de Fibonacci **sí funciona** — lo verifiqué: `SYMBOL_STRATEGY_MAP` no incluye FIB en ningún símbolo y `REGIME_WEIGHTS[*][FIBONACCI] = 0.00 < 0.08` → `should_strategy_trade` devuelve False por dos vías independientes, y con posición abierta `main.py:534-541` sigue dejando pasar solo salidas. Correcto. Pero poner además `allocation_fibonacci_retracement = 0.00` es decorativo en cuanto a capital, y su gemelo `allocation_mean_reversion = 0.50` es **lo que el desktop enseña como "asignación de MR"** aunque el peso real sea 0,65/0,30/0,10 × ¼ (=$162,50/$75/$25 con $1.000). Dos fuentes de verdad para el mismo concepto.

**Fix:** derivar `allocation_*` de `REGIME_WEIGHTS` (o al revés) y que el bridge publique el peso efectivo que devuelve `get_allocation`, no la constante.

**Verificado cómo:** grep exhaustivo + lectura de `get_allocation`/`should_strategy_trade`.

---

## Lo que está BIEN (comprobado, no lo toquéis)

- **No hay look-ahead.** Revisé el camino completo: `_close_bar` solo añade barras cerradas; el grupo 5m/1H siempre termina en la barra actual y usa exclusivamente datos pasados; en el backtester `Indicators.compute_all` se calcula una vez sobre el frame completo pero todos los indicadores son causales (`rolling`/`ewm`/`shift(1)`), y `df_slice` no adelanta nada. **Confirmado con la réplica: 48/48 señales idénticas usando solo datos hasta la barra t.**
- **No hay estado compartido entre símbolos.** Todos los dicts de MR/Fib/RegimeDetector están cacheados por `symbol`; `Indicators` es stateless.
- **NaN y división por cero bien guardados en las rutas calientes:** `pd.isna(atr) or atr <= 0`, `pd.isna(bb_lower) or bb_lower == 0`, `candle_range <= 0`, `vol_avg > 0`, `std.where(std > 1e-12, np.nan)` en `zscore`, `.replace(0, np.nan)` en `adx`/`volume_ratio`. Único hueco real: `NaN < 20` es `False` en la puerta 1H (strategies-02).
- **`Indicators.adx` implementa el ratio de Wilder correctamente** (`alpha = 2/(2·14−1+1) = 1/14`, DM con la regla del mayor movimiento, DI como cociente DM/ATR suavizados). El problema es exclusivamente el warmup sin seeding.
- **El congelado de Fibonacci (fb073a1) es efectivo y está doblemente cerrado** (`SYMBOL_STRATEGY_MAP` + `REGIME_WEIGHTS`), y las salidas de posiciones existentes siguen funcionando.
- **`is_exit_signal` cubre bien las salidas de MR y Fib** (`action.startswith("exit")`), y `validate_signal` deja pasar siempre los exits: el fix F02 de la ronda 1 está correcto.

### Corrección a la ronda 1

- **01-F18 exagera la magnitud.** Medido en esta máquina: `Indicators.compute_all(2000 filas)` = **29,2 ms** (no 169 ms) → **117 ms para 4 símbolos**, no 0,7 s. El hallazgo (bloqueo del event loop dentro del callback WS) sigue siendo válido, pero es ~6× menor de lo reportado. Desglose: `vol_pct` 2,9 ms, `adx` 1,8 ms, `directional_indicators` 1,6 ms, `rsi` 0,9 ms — `directional_indicators` recalcula ATR y los DM que `adx` ya calculó (duplicación gratuita del 40%).

---

## Tabla resumen

| id | sev | título | archivo:línea | estado ronda 1 |
|---|---|---|---|---|
| strategies-01 | **P0** | MR sin edge bruto: PF 0,40-0,60, −10,5/−13,1 bps netos, t −5/−8,7 | `strategies/mean_reversion.py:1-22,200-247` | nuevo (cuantifica 01-F06) |
| strategies-02 | P1 | 01-F05: el filtro 1H deja pasar el 99,1-99,4% | `strategies/mean_reversion.py:200-204,447-480` | **sigue abierto** |
| strategies-03 | P1 | Paridad rota: backtest usa 8 barras 1H, dirección discrepa 35% | `backtesting/backtester.py:366-367` | nuevo |
| strategies-04 | P1 | 01-F04: `bars_held` = 0 siempre; stale y trail-tight muertos | `strategies/mean_reversion.py:288,342-343` | **sigue abierto** |
| strategies-05 | P1 | 01-F06: la puerta de coste mata el 0,33%; 6,6% con R:R neto <1 | `strategies/mean_reversion.py:267-271` | **sigue abierto** |
| strategies-06 | P1 | 27,3 h tras cada reinicio con 6→33 barras 1H (ADX 90→41) | `main.py:175`, `core/market_data.py:23` | nuevo |
| strategies-07 | P2 | 01-F11: umbrales RSI adaptativos son código muerto | `strategies/mean_reversion.py:210-221` | **sigue abierto** |
| strategies-08 | P2 | Salidas en ATR ACTUAL: SL software al 78% de lo prometido | `strategies/mean_reversion.py:338,346-377` | nuevo |
| strategies-09 | P2 | 01-F17: resampleo posicional; 25-32% de señales repetidas | `strategies/mean_reversion.py:428-445` | **sigue abierto** |
| strategies-10 | P2 | 01-F16: seed con vela en formación (re-verificado hoy vía REST) | `core/market_data.py:141-162` | **sigue abierto** |
| strategies-11 | P2 | Thresholds de régimen cacheados por reloj de pared | `core/regime_detector.py:140-145` | nuevo |
| strategies-12 | P2 | `has_obi` imposible en backtest → confirmaciones asimétricas | `backtesting/backtester.py:452-453` | nuevo |
| strategies-13 | P2 | Tabla ATR "BTC" del docstring son los números de ETH | `strategies/mean_reversion.py:4-11` | nuevo |
| strategies-14 | P2 | 01-F10: impulsos Fib nunca caducan ni se consumen de verdad | `strategies/fibonacci_retracement.py:240-253` | **sigue abierto** |
| strategies-15 | P2 | README describe otro sistema (TF, TP, RSI, alloc, estrategias) | `README.md:25,156-165` | nuevo |
| strategies-16 | P3 | `get_regime_confidence` lanza TypeError (deque slicing) | `core/regime_detector.py:214` | nuevo |
| strategies-17 | P3 | 01-F24: metadata con multiplicadores de módulo, sin `net_rr` | `strategies/mean_reversion.py:319-320` | **sigue abierto** |
| strategies-18 | P3 | Salidas dimensionadas en USD vía mark_price → resto de posición | `strategies/mean_reversion.py:391` | nuevo |
| strategies-19 | P3 | Código muerto y comentarios que contradicen al código | `strategies/mean_reversion.py:133-134,229-231` | nuevo |
| strategies-20 | P3 | `allocation_*` no gobierna el capital pero es lo que se muestra | `config/settings.py:94-102` | nuevo |

**Verificación de la ronda 1:** F04 ✗ abierto · F05 ✗ abierto (peor de lo estimado) · F06 ✗ abierto · F10 ✗ abierto · F11 ✗ abierto · F16 ✗ abierto · F17 ✗ abierto. **Ninguno de los 7 P1/P2 de estrategias de la ronda 1 se ha corregido.** F18 sigue abierto pero su magnitud estaba inflada ~6×.

---

## Veredicto

1. **Mean Reversion no tiene edge. Ni neto, ni bruto.** Sobre 149,7 días de klines reales de Binance y 2.284 trades simulados con el código exacto de producción, el retorno **bruto** medio por trade es −0,90 / −0,63 / −2,05 / +0,45 bps (ETH/SOL/ADA/BTC) con errores estándar de 1,2-2,6 bps: cero estadístico.
2. **Lo demuestran los controles, no mi opinión:** entradas **aleatorias** con la misma frecuencia y el mismo mix long/short dan exactamente lo mismo (ETH +1,26 / −0,84 / +0,77 bps), y **invertir el lado** de todas las señales tampoco cambia nada (−0,29 vs −0,90). Una señal con información direccional no sobrevive a esas dos pruebas.
3. **En neto pierde con t-stat −5 a −8,7 y PF 0,40-0,60.** Con 3 símbolos y ~3,8 trades/día cada uno, el sangrado es **$2-4,5/día = 6-13%/mes sobre $1.000**, solo en fricción.
4. **Ninguna puerta lo rescata.** Lo medí: `ATR ≥ 2×coste` → −11,5/−15,1 bps. `net_rr ≥ 1,5` → −11,5/−13,0 bps. Ambas → igual. `confirmaciones ≥ 3` deja 4-27 trades en 150 días: sin poder estadístico.
5. **El código no hace lo que dice su docstring.** "Uses 1H trend as directional filter… the key filter that turns losing MR into breakeven+": ese filtro deja pasar el **99,3%** de las barras porque el ADX arranca en ~100 y con 33 barras conserva 9 puntos de sesgo. Lo único que aporta es un signo de EMA sobre 33 barras reiniciadas.
6. **Los 7 hallazgos P1/P2 de estrategias de la ronda 1 siguen todos abiertos**, y verifiqué uno a uno que no son teóricos: `bars_held = 0` medido, `volatility_percentile` inexistente medido, seed con vela en formación re-confirmado hoy contra la API real de Binance.
7. **El backtest que justifica la asignación de capital no simula el sistema que corre.** Ventana de 501 barras → 8 barras 1H → ADX medio 86 (pasa el 100%) y una **dirección que discrepa de producción el 35% del tiempo**; además OBI estructuralmente nulo y thresholds de régimen congelados. Los "PF=0.85 / PF=0.86" escritos en `SYMBOL_STRATEGY_MAP` no son evidencia de nada.
8. **Lo que está bien conviene decirlo:** no hay look-ahead, no hay estado compartido entre símbolos, los guards de NaN/división por cero de la ruta caliente son correctos, la fórmula de Wilder del ADX es correcta (falla solo el warmup) y el congelado de Fibonacci está bien hecho y doblemente cerrado.
9. **Recomendación directa:** congelar MR con el mismo criterio con el que se congeló Fibonacci. Fibonacci se congeló por "20% WR en 5 cierres"; MR tiene 2.284 trades diciendo PF 0,45. Mantener el 50% de asignación en MR mientras Fibonacci está a 0 es incoherente con la propia regla del proyecto.
10. **Orden de trabajo si se quiere reintentar:** (a) arreglar la medida antes que la estrategia — paridad backtest/live (strategies-03), ADX con seeding (02), `bars_held` por timestamp (04), resampleo por reloj (09), seed sin vela parcial (10); (b) recién entonces buscar un edge **bruto** > 3×SE fuera de muestra. Sin (a), cualquier número nuevo será tan inservible como los actuales.

