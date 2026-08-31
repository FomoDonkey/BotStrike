# Research R2 — Evidencia de Time-Series Momentum / Trend Following en cripto (días–semanas, neto de costes)

**Fecha:** 2026-08-31 · **Autor:** agente de investigación (Claude) · **Estado:** EN PROGRESO (escritura incremental)
**Pregunta:** ¿Cuál es la evidencia más sólida y REPLICADA de TSMOM/trend en cripto a horizonte días–semanas, neta de costes, y cómo se implementaría con $1000 (perps 2x o spot) en 2026?

Convención de etiquetas: **[EVIDENCIA]** = resultado empírico publicado/replicado con URL · **[EVIDENCIA DÉBIL]** = un solo paper / muestra corta / sin réplica · **[OPINIÓN]** = juicio del autor o práctica de industria sin test formal.

---

## 0. TL;DR ejecutivo (leer esto primero)

1. **Lo que sobrevive: trend-following time-series, long-only, sobre majors líquidos, con lookbacks mixtos (5–360d), vol targeting y trailing stop.** El resultado mejor documentado y con reglas explícitas y replicables es Zarattini–Pagani–Barbon (2025): Sharpe **1.57 neto de 10 bps** sobre top-20 más líquidos, CAGR 18%, MDD 11%, alfa 10.8%/año vs BTC, 2015–2025. [EVIDENCIA]
2. **Lo que NO sobrevive: momentum cross-sectional (long-short de altcoins).** Grobys & Sapkota (2019) y Grobys & Shahzad (IJFE 2025/2026) muestran que el famoso ~3%/semana de Liu-Tsyvinski-Wu tiene **varianza poblacional no definida** (leyes de potencia) → el Sharpe "no existe" estadísticamente. [EVIDENCIA]
3. **El Sharpe real esperado tras costes, decay y ausencia del régimen 2015–2017 está mucho más cerca de 0.6–1.0 que de 1.5.** Todo lo que reporte Sharpe >2.0 en cripto en muestras de 3–4 años con parámetros re-optimizados mensualmente debe tratarse como sobreajuste hasta prueba contraria. [OPINIÓN fundada en la evidencia de §5]
4. **Con $1000 la restricción vinculante NO es el alfa, es el coste fijo y la granularidad.** Un programa de 20 activos con $1000 = $50/activo: por debajo del notional mínimo de casi todo. La implementación honesta con $1000 son **3–5 activos, spot o perps 1–2x, rebalanceo con umbral, y objetivo de Sharpe ~0.7 neto**, no 1.5.
5. **Mean-reversion intradía: existe evidencia estadística, pero NO sobrevive a costes al tamaño de $1000.** Fibonacci: **no hay evidencia**; lo poco publicado es negativo o no distinguible de azar. Ver §7 y §8 — debate cerrado.

---

## 1. Mapa de la literatura núcleo (2019–2026)

### 1.1 Papers académicos clave localizados

| Paper | Año/Journal | Hallazgo central | Etiqueta |
|---|---|---|---|
| Liu, Tsyvinski & Wu — *Common Risk Factors in Cryptocurrency* | JF 2022 | Mercado, tamaño y momentum (cross-sectional, semanal) explican el cross-section; ~10 características forman long-shorts significativos | [EVIDENCIA] top-journal, pero cross-sectional y **pre-costes** |
| Grobys & Sapkota — *Cryptocurrencies and momentum* | Economics Letters 2019 | **Réplica fallida**: con datos mensuales 2014–2018 NO hay payoffs de momentum significativos | [EVIDENCIA] de no-robustez |
| **Grobys & Shahzad — *Cryptocurrency Momentum: Is It an Illusion?*** | IJFE 2026, 31(2):2180–2193 (SSRN 4633099) | No se puede rechazar la hipótesis de **varianza teórica infinita** → t-stats y Sharpe **no están definidos** para el momentum long-short cripto. "En la vida real podríamos no ser capaces de realizar esas primas" | [EVIDENCIA] — la refutación más dura |
| Han, Kang & Ryu — *Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market* | SSRN 4675565 (dic-2023) | TSMOM **fuerte**, cross-sectional **débil**. Con costes + fluctuación intradía muchas carteras se **liquidan** y muchas con retornos "significativos" ganan cero. El t-test de la media es **insuficiente** para testar rentabilidad | [EVIDENCIA] — el paper conceptualmente más alineado |
| **Zarattini, Pagani & Barbon — *Catching Crypto Trends*** | SSRN 5209907 (abr-2025), Swiss Finance Institute RP 25-80 | Ensemble Donchian + vol targeting 25%: **Sharpe 1.57 neto**, CAGR 18%, MDD 11%, alfa 10.8% vs BTC, 20 activos, 2015–2025 | [EVIDENCIA] — **el ancla de este informe** (reglas explícitas, dataset libre de sesgo de supervivencia) |
| Huang, Sangiorgi & Urquhart — *Cryptocurrency Volume-Weighted TSMOM* | SSRN 4825389 (2024) | TSMOM ponderado por volumen: 0.94%/día, Sharpe 2.17 | [EVIDENCIA DÉBIL] — 0.94%/día es ~1200%/año; casi seguro **bruto** y no ejecutable |
| Le & Ruthbah — *Trend-following Strategies for Crypto Investors* | SSRN 4551518 (2023) | Trend funciona en muestra, pero "el efecto de los costes de transacción es muy sustancial". **Fuente de los niveles 10/25/50 bps** usados por Zarattini | [EVIDENCIA] sobre sensibilidad a costes |
| Bui & Nguyen — *Systematic Trend-Following with Adaptive Portfolio Construction* | arXiv 2602.11708 (2026) | Sharpe 2.41 neto (4 bps), MDD −12.7%, 150+ perps Binance, 2021–2024 | [EVIDENCIA MUY DÉBIL / sospechosa de overfitting] — ver §5.3 |
| *Cryptocurrency anomalies and economic constraints* | IRFA 2024 (S1057521924001509) | ~4000 monedas 2014–2022: retornos anormales **concentrados en bull markets** y **decaen en el tiempo**; momentum sobrevive en monedas grandes pero con costes altos | [EVIDENCIA] clave para decay |
| *Non-standard errors in the cryptocurrency world* | IRFA 2024 (S1057521924000383) | Enorme dispersión de resultados entre equipos con los mismos datos → fragilidad metodológica | [EVIDENCIA] meta |
| *Cryptocurrency momentum has (not) its moments* | FMPM 2025 (Springer) | Momentum cripto no robusto en momentos/submuestras | [EVIDENCIA] |
| Huang, Li, Wang & Zhou — *Time series momentum: Is it there?* | **JFE 2020**, 135:774–794 | En activos tradicionales: regresiones activo-a-activo muestran **poca evidencia** de TSMOM in- y out-of-sample; el t-stat del pooled regression **no supera** los valores críticos bootstrap. La estrategia TSMOM rinde **igual** que una basada en la media histórica | [EVIDENCIA] — el precedente de réplica fallida que define el listón |
| Wen, Bouri, Xu & Zhao — *Intraday return predictability in the cryptocurrency markets: momentum, reversal, or both* | JIFMIM/NAJEF 2022 (S1062940822000833) | Hay **ambos** intradía en BTC/ETH/LTC/XRP 2013–2020; cambia con jumps, FOMC, liquidez, COVID | [EVIDENCIA] de existencia estadística — ver §7 sobre ejecutabilidad |

### 1.2 Síntesis del mapa
- El consenso emergente NO es "el momentum cripto es un hecho". Es **frecuencia-dependiente** (diario/semanal sí, mensual no), **régimen-dependiente** (bull markets), **tipo-dependiente** (time-series sobrevive, cross-sectional no) y **muy sensible a costes**. [EVIDENCIA]
- Existe un patrón histórico claro y aleccionador: en activos tradicionales, **el TSMOM de Moskowitz-Ooi-Pedersen (2012) fue desmontado por Huang et al. en JFE 2020**. Si el paper fundacional del TSMOM clásico no replicó bien, la prior sobre el TSMOM cripto (muestra 10x más corta, datos peores) debe ser **escéptica por defecto**. [OPINIÓN, pero derivada de [EVIDENCIA]]

---

## 2. El ancla: Zarattini, Pagani & Barbon (2025) — reglas exactas

**URL:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907 · PDF: https://concretumgroup.com/wp-content/uploads/2026/02/Catching-Crypto-Trends.pdf

Es el único paper localizado que da **reglas completamente especificadas, dataset libre de sesgo de supervivencia y resultados netos de costes**. Es la plantilla correcta para un backtest honesto.

### 2.1 Datos
- CoinMarketCap, **21,616 criptos**, enero 2010 – marzo 2025, panel diario OHLCV, **libre de sesgo de supervivencia**.
- Se excluyen stablecoins, wrapped tokens y tokens de NFT.
- ⚠️ **Caveat propio:** los precios de CMC son **agregados entre exchanges**. No son precios ejecutables en un venue concreto. Un backtest honesto para BotStrike debe usar OHLCV del **exchange donde se va a ejecutar**. [OPINIÓN]

### 2.2 Señal: Donchian breakout + trailing stop
```
DonchianUp(n,t)   = max(Close_t, ..., Close_{t-n+1})
DonchianDown(n,t) = min(Close_t, ..., Close_{t-n+1})
DonchianMid(n,t)  = 0.5 * (DonchianUp + DonchianDown)

TrailingStop(n,t+1) = max( TrailingStop(n,t), DonchianMid(n,t) )   # nunca baja

Pos(n,t) = 1                if Close_t >= DonchianUp(n,t)      # entrada
         = 0                if Close_t <= TrailingStop(n,t)    # salida
         = Pos(n,t-1)       en otro caso
```
- Stop inicial de una posición nueva = DonchianMid en el momento de la entrada.
- **Long-only.** Los autores lo justifican: los costes de venta en corto en tokens poco líquidos son "altamente inciertos", lo que hace los backtests long-short sensibles a supuestos.

### 2.3 Sizing: volatility targeting al 25% anualizado
```
w(n,t) = min( 0.25 / sigma_t , 200% ) * Pos(n,t)
```
- `sigma_t` = **volatilidad anualizada de 90 días** (3 meses) de los retornos del activo.
- **Cap de apalancamiento: 200%.**
- Los propios autores señalan el defecto: en tendencias fuertes la vol sube y el vol-targeting **recorta exposición justo cuando más conviene**. Trade-off estructural riesgo/participación. [EVIDENCIA, reconocido por los autores]

### 2.4 Ensemble ("Combo"): 9 lookbacks equiponderados
`n ∈ {5, 10, 20, 30, 60, 90, 150, 250, 360} días` → `w_Combo(t) = (1/9) * Σ w(n_i, t)`

Esto es lo que hace la estrategia **robusta a la elección de parámetro**: en lugar de elegir el lookback ganador ex-post, se promedian los 9. Es la defensa anti-overfitting más importante del diseño. [OPINIÓN fundada]

### 2.5 Resultados en Bitcoin solo (2015-01-01 → 2025-03-19, **SIN costes**)

| Modelo | CAGR | Vol | Sharpe | Sortino | MDD | Alfa vs BTC | Beta | Trades |
|---|---|---|---|---|---|---|---|---|
| 5d | 36% | 19% | 1.66 | 1.87 | 25% | 19% | 0.16 | 292 |
| 10d | 32% | 18% | 1.55 | 1.64 | 27% | 18% | 0.15 | 156 |
| 20d | 34% | 18% | 1.60 | 1.60 | 26% | 19% | 0.16 | 78 |
| 30d | 34% | 19% | 1.61 | 1.61 | 24% | 19% | 0.16 | 49 |
| 60d | 28% | 19% | 1.30 | 1.25 | 19% | 13% | 0.17 | 28 |
| 90d | 27% | 20% | 1.20 | 1.15 | 24% | 11% | 0.18 | 20 |
| 150d | 21% | 20% | 0.99 | 0.97 | 29% | 7% | 0.19 | 15 |
| 250d | 25% | 20% | 1.13 | 1.15 | 33% | 9% | 0.20 | ~9 |
| 360d | 29% | 20% | 1.28 | 1.27 | 34% | 12% | 0.18 | ~5 |
| **Combo** | **30%** | **17%** | **1.58** | **2.03** | **19%** | **14%** | **0.17** | — |

**Hechos relevantes:** (a) los lookbacks **cortos (5–30d) ganan** — esto es exactamente el horizonte "días–semanas" de la pregunta; (b) el MDD del trend (19–27%) es **~1/4 del de BTC buy&hold (>80%)**; (c) beta vs BTC de solo ~0.17 → prácticamente todo el retorno es alfa; (d) alfas significativos al 2.5% **excepto** 150d y 250d.

**Win rate: 40–49%** para los lookbacks cortos, con gain:loss de 3.7–6.7. Es una estrategia de **muchas pérdidas pequeñas y pocas ganancias grandes** — psicológicamente dura. El win rate sube (60–80%) solo en lookbacks largos con 5–15 trades en 10 años, muestras estadísticamente inútiles. [EVIDENCIA]

### 2.6 Costes de transacción — el punto crítico
Siguiendo Le et al. (2023), testean **10, 25 y 50 bps** por operación. Nota textual de los autores: estos niveles **exceden** las comisiones típicas de exchanges grandes, donde BTC cuesta "generalmente **por debajo de 5 bps**".

- A **50 bps**, el CAGR del modelo 5d cae de **34% → 18%** (pierde la mitad). Los modelos largos apenas se ven afectados.
- **Mitigación (importante y directamente aplicable a BotStrike):** *rebalance threshold* del **20%**. Solo se rebalancea si |peso actual − peso objetivo| > 20%. **Se aplica SOLO al rebalanceo inducido por cambios de volatilidad, NO a las señales.** Entradas/salidas por breakout o stop se ejecutan **inmediatamente** sin umbral.
- Efecto del umbral bajo escenario 50 bps: recuperación de **~100 bps/año**.
- **Todos los resultados del resto del paper son netos de 10 bps + umbral 20%.**

### 2.7 El programa diversificado (§7 del paper) — la implementación de referencia

Filtros de universo, **al final de cada mes**:
1. Listado ≥ **365 días naturales**.
2. No wrapped, no stablecoin, no NFT.
3. Volumen diario **mediano** ≥ **$2M** en los 30 días previos.
4. Ranking por volumen diario mediano del mes → **top B activos**.

Construcción: capital **equiponderado** (1/B por activo), estrategia Combo aplicada independientemente a cada activo, **rebalanceo mensual**.

Reglas de salida por iliquidez (se elimina el activo si):
- Volumen diario mediano de 30d < **$1M**, o
- Variación de precio diaria mediana de 30d < **0.5%**.

**Resultados netos (10 bps + umbral 20%), 2015-01-01 → 2025-03-19:**

| #Activos | Ret.Tot | CAGR | Vol | Sharpe | Sortino | MDD | MAR | Alfa | Beta |
|---|---|---|---|---|---|---|---|---|---|
| 5 | 452% | 18% | 10% | 1.44 | 1.77 | 14% | 1.28 | 9.7% | 0.10 |
| 10 | 438% | 18% | 9% | 1.50 | 1.87 | 12% | 1.44 | 10.3% | 0.09 |
| **20** | **443%** | **18%** | **9%** | **1.57** | **1.97** | **11%** | **1.61** | **10.8%** | **0.08** |
| 30 | 404% | 17% | 8% | 1.56 | 1.96 | 11% | 1.54 | 10.5% | 0.07 |
| 40 | 366% | 16% | 8% | 1.53 | 1.92 | 11% | 1.46 | 9.9% | 0.07 |
| 50 | 359% | 16% | 8% | 1.54 | 1.93 | 11% | 1.44 | 9.9% | 0.07 |

**Lecturas clave:**
- **El Sharpe es asombrosamente plano (1.44–1.57) entre 5 y 50 activos.** Esto es la mejor señal de robustez del paper: no depende del número de activos. Y **con 5 activos ya se captura el 92% del Sharpe de 20**. → **Directamente relevante para $1000.** [EVIDENCIA]
- **La vol de la cartera es 8–10%, no 25%.** Porque cada activo se dimensiona al 25% pero recibe 1/B del capital y está flat gran parte del tiempo. Con $1000, un CAGR del 18% son **$180/año**. Realismo absoluto.
- **Beta vs BTC ≈ 0.08.** Es un diversificador, no una apuesta direccional apalancada.
- **No hay efecto tamaño:** los deciles D1 (top-10 más líquidos) a D10 (rank 91–100) tienen Sharpes similares (2020–2025). → No hace falta bajar a altcoins ilíquidas.
- Correlación media con el SG Trend Index (CTAs tradicionales): **7.4%** (rolling 6m, oscila entre negativa y +30%).

### 2.8 Debilidades honestas del ancla [OPINIÓN crítica]
1. **La muestra 2015–2025 incluye los dos mayores bull markets de la historia del activo (2017, 2020–21).** El alfa vs BTC de 10.8% es un promedio sobre un régimen irrepetible.
2. **Precios agregados de CoinMarketCap** ≠ precios ejecutables. Los breakouts se ejecutan al cierre diario del agregado; el slippage real en el momento del breakout (justo cuando el libro se vacía) no está modelado.
3. **10 bps** es razonable para BTC/ETH en spot maker/taker, pero optimista para el activo #18 por volumen en un breakout.
4. **Sin test de overfitting formal** (ni DSR, ni PBO, ni walk-forward). El ensemble mitiga, pero no sustituye.
5. Los propios autores incluyen un disclaimer explícito de que la rentabilidad depende de "la persistencia del comportamiento tendencial", "un aspecto que puede evolucionar en el tiempo".

---

## 3. RÉPLICA PROPIA VERIFICADA — no me creo el paper, lo he ejecutado

> **[EVIDENCIA — generada en esta sesión, 2026-08-31]** He reimplementado las reglas exactas de §2 (Donchian ensemble 9 lookbacks, vol target 25% con ventana 90d, cap 200%, trailing stop = max(stop, mid), umbral de rebalanceo 20%, coste 10 bps sobre el turnover) sobre **datos diarios reales de Binance** (`api.binance.com/api/v3/klines`, BTCUSDT, 2017-08-17 → 2026-08-31, 3.302 días). Código en el scratchpad de la sesión (`repl.py`, `repl2.py`, `repl3.py`, `repl4.py`).
>
> Esto **no es** el mismo test que el paper: Binance empieza en agosto de 2017, así que **se pierden 2015–2017** (uno de los dos grandes bull markets de la muestra original) y **se añaden 2025–2026**. Precios de un único exchange, ejecutables, en lugar del agregado CoinMarketCap.

### 3.1 Resultado headline (BTC, neto de 10 bps)

| Coste | CAGR | Vol | **Sharpe** | MDD |
|---|---|---|---|---|
| 0 bps | 18.2% | 15.0% | 1.19 | 18.7% |
| **10 bps** | **17.3%** | **15.0%** | **1.14** | **19.5%** |
| 25 bps | 16.0% | 15.0% | 1.06 | 20.7% |
| 50 bps | 13.8% | 15.0% | 0.94 | 23.7% |
| **BTC buy & hold** | **36.7%** | **61.9%** | **0.82** | **76.6%** |

**Sharpe replicado = 1.14, contra el 1.58 del paper.** La diferencia es casi toda **régimen**: el paper incluye 2015–2017. El signo cualitativo se confirma (Sharpe del trend > Sharpe del B&H; MDD 4x menor), la magnitud **no**. [EVIDENCIA]

⚠️ **El hallazgo más incómodo, y el que hay que interiorizar:** en términos de **retorno absoluto, el trend-following PIERDE contra comprar y aguantar BTC** (17.3% vs 36.7% CAGR). Gana en Sharpe (1.14 vs 0.82) y en MDD (19.5% vs 76.6%). Si se escala el trend a la misma volatilidad que BTC (×4.1), daría ~70% CAGR con ~80% de MDD. **La ventaja del trend es de forma de la distribución, no de retorno.** [EVIDENCIA]

### 3.2 Descomposición temporal — aquí está el decay, verificado

| Periodo | Combo CAGR | Combo Sharpe | Combo MDD | BTC B&H Sharpe |
|---|---|---|---|---|
| 2017–2020 | 28.6% | **1.85** | 11.1% | 1.11 |
| 2021–2022 | 1.3% | **0.16** | 19.5% | −0.02 |
| 2023–2024 | 31.6% | **1.60** | 10.3% | 2.02 |
| **2025–2026** | **−4.2%** | **−0.34** | 16.2% | −0.03 |
| **2022–2026 (≈OOS)** | **8.4%** | **0.65** | 16.2% | 0.47 |

**Esto es lo más importante de todo el informe.** El Sharpe de los últimos ~4,7 años es **0.65**, no 1.57. Y **2025–2026 va en negativo**. Cualquier plan de negocio construido sobre "Sharpe 1.5" está construido sobre 2015–2020. [EVIDENCIA]

### 3.3 Cartera multi-activo (lo relevante para $1000)

Mismas reglas por activo, capital equiponderado, 10 bps:

| Universo | Muestra | CAGR | Vol | **Sharpe** | MDD |
|---|---|---|---|---|---|
| BTC | 2017–2026 | 17.3% | 15.0% | 1.14 | 19.5% |
| BTC+ETH | 2017–2026 | 15.5% | 13.0% | 1.17 | 15.3% |
| **Top 3** (BTC/ETH/BNB) | 2017–2026 | 17.1% | 12.4% | **1.33** | 14.5% |
| Top 5 (+XRP/SOL) | 2021–2026 | 7.8% | 9.3% | 0.85 | 12.0% |
| Top 10 | 2021–2026 | 5.7% | 8.4% | 0.71 | 11.6% |
| *B&H equipond. 5* | 2021–2026 | 9.6% | 61.6% | 0.46 | 79.3% |

Top-5 y Top-10 arrancan en 2021 (SOL/AVAX no existen antes), así que su muestra **coincide con el peor régimen**. Sobre el periodo comparable **2022–2026 la cartera de 5 activos da Sharpe 0.92, CAGR 8.6%, MDD 11.3%** — mejor que BTC solo en el mismo tramo (0.65). **La diversificación sí funciona**; lo que no funciona es el régimen. [EVIDENCIA]

### 3.4 Sensibilidad de parámetros (BTC, 10 bps) — dónde está y dónde no está el sobreajuste

| Bloque | Resultado | Lectura |
|---|---|---|
| **Objetivo de vol** 15/20/25/30/40% | Sharpe **1.14 idéntico** en todos; solo cambia CAGR (10.4%→27.8%) y MDD (12.1%→29.6%) | El vol target es una **palanca de escala, no de alfa**. Elegirlo por tolerancia al drawdown, no por backtest |
| **Ventana de vol** 30/60/90/180d | Sharpe 1.11 / 1.16 / 1.14 / 1.15 | **Robusto.** No optimizar esto |
| **Umbral rebalanceo** 0/10/20/35/50% | A 10 bps: 1.12–1.14 (irrelevante). A 50 bps: 0.90 → 0.94 | El umbral **solo paga si los costes son altos**. Confirma el mecanismo de Zarattini |
| **Cap apalancamiento** 1.0/1.5/2.0/3.0x | **Idéntico** | El cap **nunca se activa** en BTC (su vol nunca baja de 12.5%). Es un parámetro muerto para majors |
| **Composición ensemble** | 9LB=1.14 · cortos(5-30)=1.17 · medios(20-90)=**1.22** · largos(150-360)=0.85 · **único 30d=1.27** | Los lookbacks **largos son los malos**. El mejor único (30d) bate al ensemble — pero solo se sabe *ex post* |
| **Turnover** | **7.6x/año** (suma de \|Δw\|) | A 10 bps ≈ 0.76%/año de coste. A 50 bps ≈ 3.8%/año |

**Exposición nocional media: 0.205x del equity en BTC; 0.139x en la cartera de 5.** Es decir, **el 80-86% del capital está parado**. Es el hecho operativo más importante para dimensionar (§8). [EVIDENCIA]

### 3.5 Test formal de sobreajuste sobre mi propia réplica

Barrí **120 configuraciones** (5 vol targets × 4 ventanas de vol × 6 composiciones de ensemble) y medí la dispersión real de Sharpes:

```
N = 120 configuraciones   media SR = 1.12   sd(SR) = 0.148   max = 1.27   min = 0.68
hurdle E[max SR | H0] = sd × maxZ(120) = 0.38      <<   mejor observado 1.27
```

**Deflated Sharpe Ratio** (usando la dispersión medida sd=0.148, skew y curtosis reales de la serie):

| Muestra | SR anual | skew | kurt | DSR con N=10 | N=120 | N=500 |
|---|---|---|---|---|---|---|
| **2017–2026 completa** | 1.14 | **+1.09** | 16.6 | 0.9957 | **0.9859** | 0.9771 |
| **2022–2026 (≈OOS)** | 0.65 | +0.86 | 19.1 | 0.8196 | **0.7204** | 0.6685 |

**Veredicto honesto:** sobre la muestra completa la estrategia **pasa DSR > 0.95 incluso asumiendo 500 pruebas**. Sobre los últimos 4,7 años **NO pasa** (DSR 0.72). La **asimetría positiva (+1.09)** ayuda mucho al DSR y es una característica estructural real del trend-following (muchas pérdidas pequeñas, pocas ganancias grandes), no un artefacto. [EVIDENCIA]

---

## 4. Réplicas fallidas y evidencia en contra (lo que cierra el caso del cross-sectional)

### 4.1 El precedente: el TSMOM clásico no replicó
**Huang, Li, Wang & Zhou, JFE 2020** (https://www.sciencedirect.com/science/article/abs/pii/S0304405X19301953) desmontaron el TSMOM de Moskowitz-Ooi-Pedersen (2012) en activos tradicionales: las regresiones activo-a-activo muestran **poca evidencia** de TSMOM in- y out-of-sample; el t-stat grande venía de una **pooled regression** cuyo estadístico **no supera los valores críticos bootstrap**; y la estrategia TSMOM rinde **igual que una basada en la media histórica** de los retornos. [EVIDENCIA]

**Implicación para BotStrike:** el paper fundacional de toda esta literatura fue refutado en un top-3 journal 8 años después. La prior correcta sobre un TSMOM cripto con 9 años de datos es **escepticismo**, no entusiasmo. [OPINIÓN derivada]

### 4.2 El momentum cross-sectional cripto: refutado por varianza infinita
**Grobys & Shahzad, IJFE 2026, 31(2):2180–2193** (SSRN https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4633099 · https://onlinelibrary.wiley.com/doi/10.1002/ijfe.70036):
- La literatura reporta ~**3% de exceso semanal** en un long-short de ganadores vs perdedores.
- **No se puede rechazar la hipótesis de varianza teórica infinita** → las varianzas realizadas se rigen por **leyes de potencia** y la media y varianza poblacionales del factor **no están definidas estadísticamente**.
- Consecuencia literal: **"t-statistics o Sharpe ratios no existen para esta estrategia"** y la prima **"no es observable en la realidad"**. [EVIDENCIA]

Esto no dice "el momentum cripto es más débil de lo que crees". Dice **"el estadístico con el que lo mediste no está definido"**. Es una refutación de nivel metodológico, no de magnitud.

Refuerzos concordantes:
- **Grobys & Sapkota, Economics Letters 2019:** con datos **mensuales** 2014–2018, **no hay** payoffs de momentum significativos. [EVIDENCIA]
- ***Cryptocurrency momentum has (not) its moments*, FMPM 2025** (Springer): no robusto entre submuestras. [EVIDENCIA]
- ***Cryptocurrency anomalies and economic constraints*, IRFA 2024** (https://www.sciencedirect.com/science/article/abs/pii/S1057521924001509): ~4.000 monedas 2014–2022; los retornos anormales **se concentran en bull markets** y **decaen en el tiempo**; el momentum sobrevive solo en monedas grandes y con costes altos. [EVIDENCIA]
- ***Non-standard errors in the cryptocurrency world*, IRFA 2024** (S1057521924000383): equipos distintos con los **mismos datos** obtienen resultados muy dispares. La fragilidad no es del dato, es del **grado de libertad metodológico**. [EVIDENCIA]

### 4.3 Han, Kang & Ryu (SSRN 4675565) — el matiz que separa TS de CS
Hallazgos (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565):
- Evidencia de momentum **time-series FUERTE**; **cross-sectional DÉBIL**. ✔ concuerda con §3.3 y con Zarattini.
- Al evaluar correctamente — **con costes de transacción y con la fluctuación de precio intradía** — muchas carteras de momentum **se liquidan** (stop-out por el recorrido intradía que un backtest close-to-close no ve) y muchas con retornos "estadísticamente significativos" **ganan cero**.
- El efecto se concentra en **ganadores**; los **perdedores rebotan** e infligen pérdidas grandes → mata el lado corto.
- **"El t-test de la media es insuficiente para testar rentabilidad."** [EVIDENCIA]

> ⚠️ **Requisito de backtest que sale de aquí y que casi nadie implementa:** simular con **OHLC intradía, no solo cierres**. Un stop que en datos diarios "nunca se toca" sí se toca con el *low* del día. Mi réplica de §3 **no** hace esto (usa cierres) → mi Sharpe 1.14 es, si acaso, **optimista**. [OPINIÓN fundada]

### 4.4 Réplica independiente 2026 con resultado mucho peor
***Momentum Trading in Cryptocurrencies: A Comparative Study of Time-Series and Cross-Sectional Strategies*** — Vilnius University, *Buhalterinės apskaitos teorija ir praktika*, aceptado 30-abr-2026 (https://www.journals.vu.lt/BATP/en/article/download/44540/42590/138419).

- **8 criptos grandes, 1-ene-2020 → 31-oct-2025**, señales EMA multi-horizonte, escaladas por volatilidad.
- **TS momentum: 31.96% anual, media diaria 0.000741, vol diaria 0.026301 → Sharpe anualizado ≈ 0.54 (365d) / 0.45 (252d). MDD 45.5%.**
- **CS momentum: 14.59% anual, Sharpe diario 0.012757 → ≈0.24 anualizado. MDD 55.0%.**
- Los propios autores: *"does not incorporate transaction costs, slippage, funding rates, or liquidity constraints"* → **son cifras BRUTAS**.
- Dinámica por año: en **2022–2023 el TS produce retornos anuales negativos** "as trends collapse"; en **2024–2025 ambas convergen a retornos modestos y similares".
- Explicación estructural del fracaso del CS: **correlaciones de 0.5–0.8 entre majors** → no hay dispersión que explotar. [EVIDENCIA]

**Este es el contraste decisivo:** un estudio independiente, con 8 majors, en 2020–2025, **bruto de todo coste**, obtiene **Sharpe ~0.5 y MDD 45%**. Zarattini obtiene 1.57 neto y MDD 11%. La diferencia **no es el activo, es el diseño**: ensemble de lookbacks + trailing stop Donchian + vol targeting con cap + long-only + rebalanceo con umbral. **El diseño de gestión de riesgo ES el alfa.** [OPINIÓN, respaldada por el contraste de §3.4 donde el ensemble largo solo daba 0.85]

### 4.5 El caso sospechoso: arXiv 2602.11708 — cómo se ve el sobreajuste
*Systematic Trend-Following with Adaptive Portfolio Construction* (Bui & Nguyen, 2026), https://arxiv.org/abs/2602.11708. Reporta Sharpe **2.41**, MDD **−12.7%**, Calmar 3.18, 150+ perps de Binance, velas de 6h, 70/30 long-short, ene-2021→dic-2024.

Impacto de costes que sí reportan (Tabla 4): **0 bps → 2.87 · 4 bps → 2.41 · 8 bps → 2.01 · 12 bps → 1.62**.

**Por qué NO debe usarse como evidencia** [OPINIÓN, con base en los propios datos del paper]:
1. **Los umbrales de entrada se re-optimizan MENSUALMENTE.** Su propio ablation: *"Fixed parameters (no monthly optimization): 1.34 Sharpe"* vs 2.41 con optimización. **Más de la mitad del Sharpe viene del ajuste mensual de parámetros**, no de la señal.
2. Filtros de selección con umbrales tan finos como "Sharpe del mes previo ≥ 1.3 para largos, ≥ 1.7 para cortos" — números que **no salen de ninguna teoría**.
3. Muestra de **36–48 meses**. Con MinBTL (§6.2), una muestra de 4 años solo tolera ~**30 configuraciones independientes** para un umbral de Sharpe 1; aquí se barren α, λ, timeframes, umbrales, filtros → muy por encima.
4. **MDD de −12.7% con 40.5% de retorno anual en cripto 2021–2024** (que incluye el colapso de FTX y −75% en BTC) es una cifra extraordinaria que exigiría evidencia extraordinaria.
5. **Sin DSR, sin PBO, sin walk-forward.** El bootstrap por bloques que aplican testa "¿es distinto del benchmark?", **no** "¿está sobreajustado?".
6. Su propio análisis por régimen: **Sharpe −0.31 en bear markets.** Consistente con toda la literatura y con mi §3.2.

---

## 5. Decay post-publicación — cuánto alfa queda

**[EVIDENCIA]**
- **McLean & Pontiff (base de referencia general):** ~**50% del alfa de una anomalía desaparece tras su publicación**. Documentado en toda la literatura de asset pricing (ver también *Publication Bias in Asset Pricing Research*, https://arxiv.org/pdf/2209.13623).
- **Momentum en acciones:** ~10%/año en los 90 → ~2%/año hoy. El decay se ajusta mejor a un modelo **hiperbólico** (R²=0.65) que exponencial (0.61) o lineal (0.51); el *crowding* se acelera tras 2015 (*Not All Factors Crowd Equally*, https://arxiv.org/pdf/2512.11913). [EVIDENCIA]
- **Cripto específicamente:** *Cryptocurrency anomalies and economic constraints* (IRFA 2024) documenta que los retornos anormales **decaen en el tiempo** y se concentran en bull markets.
- **Mi propia medición (§3.2):** Sharpe 1.85 (2017–2020) → 0.65 (2022–2026) → **−0.34 (2025–2026)**. Un decay del **65%** entre la primera y la segunda mitad de la muestra. [EVIDENCIA]
- **Contexto de la industria:** el **SG Trend Index** (CTAs institucionales, activos tradicionales) sufrió su **segundo mayor drawdown desde 2000: −20.4% de mayo-2024 a mayo-2025**; la categoría *systematic trend* de Morningstar perdió **−2.3% anualizado** en los 3 años hasta agosto-2025. **El trend-following como clase está en un drawdown plurianual, no solo en cripto.** [EVIDENCIA]

> **[OPINIÓN — el número con el que planificar]** Partir de un Sharpe esperado **neto, forward-looking, de 0.5–0.8** para un programa de trend cripto bien construido, con un intervalo de confianza que incluye **años enteros negativos**. Usar 1.5 es planificar sobre un régimen que ya no existe.

---

## 6. Umbrales estadísticos de aceptación — los números exactos

### 6.1 Deflated Sharpe Ratio (Bailey & López de Prado, JPM 2014)
PDF: https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf · SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551

**Umbral bajo hipótesis nula** (Extreme Value Theory sobre N pruebas independientes):
```
E[max SR_N] = E[{SR_n}] + sd({SR_n}) · [ (1-γ)·Z⁻¹(1 - 1/N) + γ·Z⁻¹(1 - 1/(N·e)) ]
γ = 0.5772156649  (Euler-Mascheroni)     cota superior:  E[max SR_N] ≤ √(2·ln N)
```
**Estadístico:**
```
DSR = Z[ (SR̂ - SR₀)·√(T-1) / √(1 - γ₃·SR̂ + ((γ₄-1)/4)·SR̂²) ]
SR₀ = E[max SR_N] ,  todo en unidades POR PERIODO (no anualizadas)
γ₃ = asimetría,  γ₄ = curtosis (no exceso),  T = nº de observaciones
```
Notar que **la asimetría negativa castiga** y la positiva premia — por eso el trend-following (skew positivo) sale bien parado y las estrategias de venta de volatilidad (skew negativo) salen mal.

**Hurdle E[max SR] anualizado, calculado:**

| N pruebas | maxZ(N) | sd(SR)=0.15 | sd(SR)=0.25 | sd(SR)=0.35 | sd(SR)=0.50 |
|---|---|---|---|---|---|
| 10 | 1.575 | 0.24 | 0.39 | 0.55 | 0.79 |
| 20 | 1.901 | 0.29 | 0.48 | 0.67 | 0.95 |
| 50 | 2.276 | 0.34 | 0.57 | 0.80 | 1.14 |
| 100 | 2.531 | 0.38 | 0.63 | 0.89 | 1.27 |
| 200 | 2.766 | 0.42 | 0.69 | 0.97 | 1.38 |
| 1000 | 3.255 | 0.49 | 0.81 | 1.14 | 1.63 |

**Sharpe anualizado mínimo para DSR > 0.95**, con la dispersión **realmente medida** en mi barrido (sd=0.148), skew −0.3, kurt 6:

| Longitud de muestra | N=20 | N=120 | N=500 |
|---|---|---|---|
| **3 años** | **1.24** | **1.35** | **1.42** |
| **5 años** | **1.02** | **1.13** | **1.20** |
| **9 años** | **0.83** | **0.94** | **1.01** |

### 6.2 Minimum Backtest Length (Bailey, Borwein, López de Prado & Zhu, *Notices of the AMS* 61(5), 2014)
PDF: https://www.ams.org/notices/201405/rnoti-p458.pdf
```
MinBTL (años) ≈ [ (1-γ)·Z⁻¹(1-1/N) + γ·Z⁻¹(1-1/(N·e)) ]² / E[max_N]²   <   2·ln(N) / E[max_N]²
```
**Cita literal del paper:** con solo **5 años de datos, no deben probarse más de 45 configuraciones independientes**, o estrategias sin ninguna habilidad serán seleccionadas con un Sharpe IS de 1. Y: **con solo N=10 pruebas se espera encontrar un Sharpe in-sample de 1.57 aunque el Sharpe OOS verdadero de todas sea cero.** [EVIDENCIA]

**Tabla calculada (para mantener E[max SR]=1):**

| N pruebas independientes | MinBTL (años) |
|---|---|
| 5 | 1.4 |
| 10 | 2.5 |
| 20 | 3.6 |
| **45** | **5.0** |
| 100 | 6.4 |
| 200 | 7.6 |
| 1000 | 10.6 |

**Con ~9 años de datos diarios de cripto de calidad, el presupuesto es de ~300–400 configuraciones independientes.** Parece mucho, pero un grid search de 5 parámetros × 5 valores = 3.125 combinaciones lo revienta 10 veces. [EVIDENCIA + cálculo propio]

### 6.3 Probability of Backtest Overfitting (PBO) — Bailey, Borwein, López de Prado & Zhu
PDF: https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf · SSRN 2326253

**Definición:** PBO = probabilidad de que la configuración óptima **in-sample** quede **por debajo de la mediana out-of-sample**. Se estima por **CSCV** (Combinatorially Symmetric Cross-Validation): se parte la serie en S bloques (típico S=16), se toman todas las C(S, S/2) combinaciones como IS y el complemento como OOS, se calcula el rango OOS de la config ganadora IS, y se mide la fracción de casos en que cae bajo la mediana.

**Umbrales — honestidad requerida:** **[OPINIÓN, no [EVIDENCIA]]** — busqué específicamente un umbral canónico y **no existe en los papers originales**. Bailey et al. definen el estadístico pero **no fijan un corte**. Lo que sí es interpretación directa e incuestionable de la definición:
- **PBO ≥ 0.50** ⇒ la selección es peor que tirar una moneda ⇒ **descartar sin discusión**.
- **PBO ∈ [0.20, 0.50)** ⇒ zona roja.
- **PBO < 0.20** ⇒ zona tolerable.
- **PBO < 0.10** ⇒ buena.
Implementaciones: `pypbo` (https://github.com/esvhd/pypbo), paquete R `pbo` (https://cran.r-project.org/web/packages/pbo/).

### 6.4 Número mínimo de trades — potencia estadística
**[OPINIÓN, cálculo propio, no hay estándar académico]** El error estándar de un Sharpe estimado es aproximadamente `SE(SR) ≈ √((1 + SR²/2)/n)`. Para distinguir SR=0.8 de SR=0 al 95% con un solo test hacen falta ~n=400 observaciones. **Pero el número relevante no es "observaciones", son eventos independientes = trades cerrados.**

De mi réplica (§2.5, tabla de Zarattini): el lookback de 30d produce **49 trades en 10 años**; el de 150d, **15**. **Un backtest de trend con menos de ~100 trades cerrados no tiene potencia estadística para nada.** El ensemble de 9 lookbacks ayuda: suma ~650 trades en 10 años sobre BTC solo.

**Umbral propuesto: ≥150 trades cerrados por configuración evaluada, y ≥3 regímenes de mercado distintos cubiertos (bull, bear, lateral).** El segundo criterio es más vinculante que el primero.

---

## 7. Costes de transacción — el modelo honesto para 2026

### 7.1 Comisiones reales (verificadas, agosto 2026)

| Venue | Maker | Taker | Fuente |
|---|---|---|---|
| Kraken Pro Derivatives (EEE) | **0.02%** = 2 bps | **0.05%** = 5 bps | Docs oficiales Kraken (ver `research_r2_venues_es_2026.md`) |
| Hyperliquid | **0.015%** = 1.5 bps | **0.045%** = 4.5 bps | https://hyperliquidguide.com/guides/fees |
| Binance USDT-M | 0.02% | 0.05% | — (❌ no disponible para residentes ES desde 1-jul-2026) |

### 7.2 ⚠️ El coste que casi todos los papers ignoran: FUNDING de perpetuos

**[EVIDENCIA — datos reales descargados de la API pública de Binance Futures en esta sesión: `fapi.binance.com/fapi/v1/fundingRate`, 6.205 pagos de funding por símbolo, 2021-01 → 2026-08]**

Tasa de funding **anualizada** (media de los pagos de 8h × 3 × 365), **pagada por los largos cuando es positiva**:

| Año | BTCUSDT | ETHUSDT | SOLUSDT | % pagos >0 (BTC) |
|---|---|---|---|---|
| 2021 | **+30.61%** | +37.54% | +28.59% | 92.7% |
| 2022 | +4.16% | +0.79% | −35.56% | 77.9% |
| 2023 | +7.87% | +8.26% | +1.30% | 89.9% |
| 2024 | +11.92% | +12.96% | +13.62% | 91.6% |
| 2025 | +5.13% | +4.93% | +0.35% | 87.1% |
| 2026 (a ago) | +2.59% | +1.56% | −1.67% | 71.4% |
| **2021–2026** | **+10.84%** | **+11.57%** | +0.84% | **~87%** |

Verificación cruzada independiente: el **CF Bitcoin Kraken Perpetual Funding Rate Index (KFRI)** marcaba **9.9974% anualizado el 30-ago-2026** (https://www.cfbenchmarks.com/data/indices/KFRI). Coherente. [EVIDENCIA]

> 🔴 **Conclusión de coste:** estar **largo** en un perp de BTC ha costado de media **~11% anual solo en funding**, y el largo paga en **~87% de los periodos**. Esto **no aparece en ninguno de los papers académicos revisados** (todos usan spot o ignoran el funding). Es el mayor sesgo optimista de toda la literatura para una implementación en perps.

**Pero — y esto es clave — el impacto real es menor de lo que parece**, porque la exposición nocional media del Combo es solo **0.139x del equity** (§3.4):
- Sin apalancamiento: **0.139 × 10.84% = 1.51% del equity/año**.
- Con 2x: **3.01% del equity/año**.

Contra un CAGR bruto de ~8–17%, es entre el **10% y el 35% del retorno**. Material, pero no letal. [EVIDENCIA + cálculo propio]

### 7.3 Presupuesto de costes total recomendado para un backtest honesto

| Componente | Valor a usar | Justificación |
|---|---|---|
| Comisión | **5 bps taker por lado** (no 2 bps maker) | Los breakouts se ejecutan a mercado. Suponer maker en una señal de ruptura es hacer trampa |
| Slippage majors (BTC/ETH) | **+3 bps** con $1000 de tamaño | Tamaño irrelevante frente al libro |
| Slippage alt top-20 | **+10 bps**, y **+25 bps en el momento del breakout** | El libro se vacía justo cuando salta el stop/breakout |
| **Coste total por operación** | **8 bps (majors) / 15–35 bps (alts)** | |
| Funding (solo perps, largo) | **+11%/año × exposición nocional media** | Datos §7.2 |
| Turnover esperado | **~7.6x/año** (Σ\|Δw\|) | Medido en §3.4 |
| **Drag total anual estimado** | **~0.6% (spot majors) a ~4.5% (perps 2x alts)** | |

**[OPINIÓN]** Si el backtest no sobrevive a **15 bps por operación + funding**, no sobrevivirá en producción. Testarlo también a **50 bps** como escenario de estrés: Zarattini y mi réplica coinciden en que a 50 bps el Sharpe cae a ~0.94 en BTC (de 1.14). Sigue siendo positivo — es una señal genuinamente robusta a costes, aunque a costa de la mitad del retorno en los lookbacks cortos.

---

## 8. Mean reversion intradía — ¿existe? ¿es explotable con $1000?

### 8.1 La evidencia de que EXISTE
**Wen, Bouri, Xu & Zhao — *Intraday return predictability in the cryptocurrency markets: Momentum, reversal, or both***, North American Journal of Economics & Finance 2022 (https://www.sciencedirect.com/science/article/abs/pii/S1062940822000833):
- Datos de alta frecuencia de **BTC, 3-mar-2013 → 31-may-2020**; extendido a ETH, LTC, XRP.
- **Se documentan AMBOS**: momentum intradía **y** reversión intradía.
- Los patrones **cambian** con: saltos de precio grandes, anuncios del **FOMC**, niveles de liquidez y el brote de **COVID**.
- Las estrategias de *timing* baten a always-long / buy&hold. [EVIDENCIA]

**Bitcoin intraday time-series momentum** (Univ. of Reading, CentAUR 100181): estrategia de momentum intradía semihorario en BTC → **CER de 5.95% anual** y **Sharpe reportado de 0.53**. [EVIDENCIA]

### 8.2 La evidencia de que NO es explotable a este tamaño
- **Sharpe 0.53** para el momentum intradía es **la mitad** del Sharpe del trend diario (1.14 en mi réplica) — y con **~50–100x más operaciones**. [EVIDENCIA]
- ***Another look at trading costs and short-term reversal profits*** (EFMA): *"Due to transaction costs, reversal strategies are not applicable, and profitable strategies do not exist"*; las reversiones requieren **rotación frecuente en los activos de mayor coste**; el ensanchamiento del bid-ask **elimina la mayor parte del beneficio**; el efecto es *"estadísticamente significativo pero no económicamente"*. [EVIDENCIA]
- **ML sobre BTC con walk-forward** (https://arxiv.org/html/2606.00060v1): las estrategias sign-based ingenuas **fracasan en cuanto se imponen 10 bps** de coste; solo se recupera rentabilidad con un filtro explícito de reducción de turnover. [EVIDENCIA]
- **Aritmética elemental:** una estrategia intradía que opere 4 veces al día a 8 bps por operación paga **4 × 8 bps × 365 = 117% anual** en costes. Necesita un edge bruto superior al **117% anual** solo para empatar.

> ### 🔒 VEREDICTO MEAN REVERSION INTRADÍA
> **La reversión intradía en cripto EXISTE estadísticamente. NO es explotable con $1000 en 2026.** El edge bruto documentado (Sharpe ~0.5) es la mitad del edge del trend diario y requiere un orden de magnitud más de operaciones, cada una pagando 8–15 bps. Es una estrategia para market-makers con rebates negativos y colocation, no para un bot retail. **[EVIDENCIA para la existencia; OPINIÓN fundada en aritmética de costes para la no-explotabilidad]**

---

## 9. Fibonacci — cerrando el debate

### 9.1 La evidencia directa
**Estudio decisivo:** ***Automatic identification and evaluation of Fibonacci retracements: Empirical evidence from three equity markets***, *Expert Systems with Applications* (2022), https://www.sciencedirect.com/science/article/abs/pii/S0957417421012495
- Testa retrocesos de Fibonacci **automáticamente** (eliminando la subjetividad del trazado manual, que es el gran agujero metodológico de todo lo anterior) en **Dow Jones, NASDAQ y DAX**.
- **Resultado: la probabilidad de que el precio rebote en un nivel de Fibonacci es estadísticamente indistinguible de la probabilidad de que rebote en un nivel NO-Fibonacci elegido al azar.** Como regla autónoma, los niveles **no tienen ningún poder especial**. [EVIDENCIA]
- El único hallazgo positivo: existe una relación entre la **anchura de la zona** de Fibonacci y la probabilidad de detectar un rebote — lo cual es tautológico (una zona más ancha captura más rebotes) y **los propios autores señalan que no implica una estrategia rentable**.

### 9.2 La evidencia periférica (y por qué no rescata el caso)
- ***Energy crypto currencies and leading U.S. energy stock prices: are Fibonacci retracements profitable?***, *Financial Innovation* 2022 (https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8752186/): la estrategia Fibonacci bate al buy&hold en **varias acciones energéticas**, pero **FRACASA en las criptomonedas energéticas**, donde la volatilidad extrema destruye la señal. **En cripto, negativo.** [EVIDENCIA]
- Estudios positivos que circulan (Bolsa de Pakistán, "74% de efectividad", CNN híbrido + Fibonacci en *Journal of Big Data* 2024): muestras pequeñas, trazado subjetivo de los puntos de swing, sin corrección por multiplicidad, sin comparación contra niveles aleatorios, sin costes. **No cumplen ninguno de los umbrales de §6.**
- El problema estructural: **Fibonacci tiene un grado de libertad no reportado** — la elección del swing high y swing low. Dos analistas trazan niveles distintos sobre el mismo gráfico. Una regla con un parámetro que se elige a ojo *ex post* no es testable, y por tanto no es una hipótesis científica.

> ### 🔒 VEREDICTO FIBONACCI
> **No hay evidencia. El único test riguroso (automatizado, contra control aleatorio, en 3 mercados) no encuentra diferencia frente a niveles al azar, y el único test en cripto es negativo.** Fibonacci no debe aparecer en BotStrike ni como señal, ni como filtro, ni como nivel de stop o take-profit. Si se quiere un nivel de referencia estructural, usar algo con base económica y testable: el **canal de Donchian** (§2.2), un **ATR múltiplo**, o un **percentil de volatilidad realizada**. **[EVIDENCIA]**
>
> Matiz honesto de reflexividad: los niveles redondos y los retrocesos del 50% *pueden* tener un efecto de profecía autocumplida si suficientes órdenes se colocan ahí. Pero (a) el 50% no es un número de Fibonacci, y (b) el test automatizado ya incluiría ese efecto si existiera y lo encontró **nulo**. **[OPINIÓN]**

---

## 10. Implementación con $1000 en 2026 — la respuesta concreta

### 10.1 La restricción vinculante NO es el alfa, es la granularidad
Con **$1000** y el programa de 20 activos de Zarattini: $50 por activo × exposición media 0.139 = **$7 de posición nocional media**. El mínimo de orden en Hyperliquid es **~$10 de nocional** y en Kraken **0.0001 BTC**. **La mitad de las señales serían inejecutables.** [EVIDENCIA de mínimos: `research_r2_venues_es_2026.md`]

**Consecuencia directa: con $1000 el universo máximo viable es 3–5 activos, no 20.** Y afortunadamente §3.3 muestra que **con 3 activos ya se obtiene el Sharpe más alto de mi réplica (1.33)** y con 5 se obtiene 0.92 en el periodo reciente — la diversificación más allá de 5 aporta poco y cuesta ejecutabilidad.

### 10.2 Spot vs perps 2x — la decisión, con números

| | **Spot (3–5 majors)** | **Perps 1x** | **Perps 2x** |
|---|---|---|---|
| Exposición nocional media | 0.139x | 0.139x | 0.278x |
| Coste funding/año | **$0** | ~$15 (1.5%) | ~$30 (3.0%) |
| Comisiones/año (7.6x turnover, 8 bps) | ~$6 | ~$6 | ~$12 |
| CAGR esperado (Sharpe 0.7, vol 9%) | ~6.3% = **$63** | ~4.8% = $48 | ~9.6% = **$96** |
| MDD esperado | ~12% | ~12% | ~24% |
| Puede ir corto | ❌ | ✅ | ✅ |
| Riesgo de liquidación | ❌ ninguno | bajo | real |
| Complejidad operativa | baja | media | media |
| Fiscalidad ES | ganancia patrimonial (claro) | **no verificado** (§5 venues) | idem |

**[OPINIÓN — recomendación]** **Empezar en SPOT sobre 3 activos (BTC, ETH, +1).** Razones:
1. El **funding es el mayor coste identificado** (§7.2) y en spot es exactamente cero.
2. La estrategia es **long-only por diseño** (Zarattini justifica esto: los costes del corto en cripto son "altamente inciertos"; Han et al. muestran que los **perdedores rebotan** y matan el lado corto). Si no vas a ir corto, **el perp solo te aporta apalancamiento y funding**.
3. El apalancamiento **no mejora el Sharpe** — §3.4 lo demuestra: el Sharpe es idéntico (1.14) para objetivos de vol del 15% al 40%. Solo escala retorno **y** drawdown proporcionalmente.
4. Con $1000, un drawdown del 24% (perps 2x) frente al 12% (spot) es la diferencia entre seguir operando y abandonar. **El riesgo de ruina psicológica es el riesgo dominante a este tamaño.**

**Cuándo justificaría el perp 2x:** solo si el objetivo declarado es maximizar retorno absoluto **aceptando explícitamente** un MDD del 25–30%, y solo tras ≥6 meses de operación real en spot con la ejecución validada.

### 10.3 Expectativa honesta con $1000
Con Sharpe neto esperado **0.7** (§5) y vol objetivo de cartera **9%**:
- **Retorno esperado: ~6% = $60/año.** Rango 1σ: **−$30 a +$150**.
- **Drawdown esperado en algún momento de los 2 primeros años: 12–15% = −$150.**
- **Probabilidad de un año natural en pérdidas: ~25–30%** (con Sharpe 0.7, P(retorno anual < 0) = Φ(−0.7) ≈ 24%).

> **[OPINIÓN]** Con $1000, **el valor de este proyecto no es el dinero, es la infraestructura**: validar ejecución, reconciliación, logs fiscales, watchdog y disciplina con capital real pero irrelevante. Si el objetivo real fuera ganar dinero, $60/año no justifica el trabajo. Merece la pena decirlo explícitamente antes de invertir meses.

---

## 11. Parámetros exactos para un backtest honesto — la especificación

### 11.1 Datos
| Parámetro | Valor | Por qué |
|---|---|---|
| Fuente | **OHLCV del venue de ejecución** (Kraken o Hyperliquid), NO CoinMarketCap ni CoinGecko | Los precios agregados no son ejecutables (§2.8) |
| Frecuencia señal | **Diaria (cierre UTC)** | Los lookbacks 5–90d son el sweet spot (§2.5, §3.4). Intradía no sobrevive costes (§8) |
| Frecuencia simulación | **OHLC diario como mínimo; ideal barras de 1h para stops** | Han et al.: los stops se activan con el *low*, no con el cierre (§4.3) |
| Muestra | **≥5 años; objetivo 2017-08 → hoy (~9 años)** | MinBTL: 5 años solo tolera 45 configs (§6.2) |
| Universo | **Point-in-time**, ranking por volumen mediano 30d recalculado mensualmente. **Prohibido** elegir los activos por su historial completo | Zarattini reconoce sesgo de selección en su Tabla 3; su §7 lo evita |
| Delisting / supervivencia | Incluir activos que murieron; salida forzada si volumen mediano 30d < $1M | §2.7 |

### 11.2 Estrategia — punto de partida (NO optimizar estos valores en la primera pasada)
```yaml
señal:
  tipo: donchian_breakout_ensemble
  lookbacks: [5, 10, 20, 30, 60, 90]      # 6, no 9: los largos (150/250/360) dan Sharpe 0.85 (§3.4)
  entrada:  Close_t >= max(Close[t-n+1 .. t])
  salida:   Close_t <= TrailingStop_t
  trailing_stop: max(TrailingStop_{t-1}, 0.5*(DonchianUp_n + DonchianDown_n))   # nunca baja
  stop_inicial:  DonchianMid_n en el momento de entrada
  direccion: LONG_ONLY                     # los cortos: coste incierto + los perdedores rebotan (§4.3)

sizing:
  metodo: volatility_targeting
  target_vol_anual: 0.20                   # 0.25 en el paper; 0.20 por prudencia. NO afecta al Sharpe (§3.4)
  ventana_vol: 90                          # días; robusto entre 30 y 180 (§3.4)
  anualizacion: sqrt(365)                  # cripto opera 365 días, NO 252
  cap_apalancamiento: 2.0                  # nunca se activa en majors; protege en activos de baja vol
  peso_final: w = mean_over_lookbacks( min(target_vol/sigma_t, cap) * Pos_n_t )

cartera:
  n_activos: 3                             # 5 máximo con $1000 (§10.1)
  ponderacion: equiponderada (1/N)
  rebalanceo_universo: mensual (fin de mes)
  filtro_liquidez: volumen mediano 30d >= $2M para entrar, < $1M para salir
  antiguedad_minima: 365 dias listado

ejecucion:
  umbral_rebalanceo: 0.20                  # SOLO sobre cambios inducidos por volatilidad
  señales: SIN umbral — entradas y stops se ejecutan inmediatamente
  latencia: señal en cierre de vela t -> ejecución en apertura de t+1 (NUNCA al cierre de t)

costes:
  comision_por_lado_bps: 5                 # taker; los breakouts van a mercado
  slippage_majors_bps: 3
  slippage_alts_bps: 10
  escenarios_estres_bps: [15, 25, 50]
  funding_perps: serie histórica REAL del venue, NO una constante   # §7.2
```

### 11.3 Umbrales de aceptación — checklist de GO / NO-GO

Un backtest **solo pasa a paper trading** si cumple **TODOS**:

| # | Criterio | Umbral | Fuente |
|---|---|---|---|
| 1 | **Sharpe neto** en la muestra completa | **≥ 0.80** | §5, §6.1 |
| 2 | **Sharpe neto en la submuestra 2022–hoy** | **≥ 0.50** | §3.2 — el régimen reciente es el que importa |
| 3 | **Deflated Sharpe Ratio** | **≥ 0.95**, con N = **nº real de configuraciones probadas** (contarlas y registrarlas) | §6.1 |
| 4 | **PBO** (CSCV, S=16) | **< 0.20** | §6.3 |
| 5 | **Nº de configuraciones probadas** | **≤ 300** con 9 años de datos; **≤ 45** si solo hay 5 años | MinBTL, §6.2 |
| 6 | **Trades cerrados** | **≥ 150** por configuración evaluada | §6.4 |
| 7 | **Cobertura de regímenes** | Rentable (o pérdida < 10%) en **≥ 2 de 3** regímenes: bull, bear, lateral | §3.2, §4.5 |
| 8 | **Robustez a costes** | Sharpe **> 0.5 a 50 bps** por operación | §3.1, §7.3 |
| 9 | **Sensibilidad de parámetros** | Sharpe varía **< 25%** al mover cada parámetro ±50% | §3.4 |
| 10 | **Max drawdown** | **≤ 25%** con el vol target elegido | §10.3 |
| 11 | **Asimetría de retornos** | **> 0** (característica estructural del trend; si es negativa, hay una venta de volatilidad escondida) | §3.5 |
| 12 | **Sin look-ahead** | Auditar: la señal de t usa solo datos ≤ t; la ejecución es en t+1 | — |

**Y un criterio de descarte inmediato:** si el Sharpe reportado supera **2.0**, asumir un bug o look-ahead hasta demostrar lo contrario. Ninguna evidencia creíble en cripto lo sostiene (§4.5).

### 11.4 Umbrales para pasar de paper a dinero real
| Criterio | Umbral |
|---|---|
| Duración del paper trading | **≥ 90 días** con el motor final, sin cambios de código en la estrategia |
| Tracking error vs backtest | Diferencia de retorno diario medio **< 15%** del retorno esperado (si no, hay un problema de ejecución/slippage no modelado) |
| Slippage realizado | **≤ 2x** el asumido en el backtest |
| Órdenes rechazadas / fallidas | **< 1%** |
| Reconciliación de posiciones motor↔exchange | **100%**, diaria, automática |
| Log fiscal | Operativo desde el primer trade (§5 de `research_r2_venues_es_2026.md`) |

---

## 12. Balance de evidencia y metodología

**Trabajo realizado (2026-08-31):** 12 WebSearch + 15 WebFetch, **4 papers extraídos íntegros vía `pdftotext`** (Catching Crypto Trends, Deflated Sharpe Ratio, Pseudo-Mathematics/AMS, VU Momentum, Momentum & Liquidity), **2 descargas de datos reales de API pública** (Binance klines 3.302 días × 10 símbolos; Binance funding 6.205 pagos × 3 símbolos) y **4 scripts de replicación propia** ejecutados.

**Lo que está VERIFICADO por ejecución propia, no por lectura:**
- El Sharpe del Donchian Combo en BTC 2017–2026 neto de 10 bps = **1.14** (no 1.57).
- El decay: **1.85 → 0.65 → −0.34**.
- La invarianza del Sharpe al vol target.
- El turnover (7.6x/año) y la exposición media (0.139x en cartera de 5).
- El funding medio de BTC perp: **+10.84% anualizado 2021–2026**, pagado por el largo el 87% del tiempo.
- El DSR de mi propia réplica: 0.986 en muestra completa, **0.72 en 2022–2026**.

**Limitaciones conocidas y no disimuladas:**
1. Mi réplica usa **cierres diarios**, no OHLC intradía → los stops están evaluados de forma **optimista** (Han et al., §4.3). El Sharpe real sería algo menor.
2. Mi réplica no incluye **slippage variable**, solo un coste fijo en bps.
3. La muestra de Binance empieza en **2017-08**; no puedo testar 2015–2017.
4. Las carteras de 5 y 10 activos tienen muestras **más cortas y sesgadas al peor régimen** (SOL/AVAX listan en 2020–21). No son comparables directamente con la de 3 activos.
5. **No pude leer el PDF de Zarattini vía WebFetch** (403 y binario); lo resolví descargando y extrayendo con `pdftotext`. Todas las cifras de §2 son cita directa del texto extraído.
6. **SSRN devuelve 403** sistemáticamente: los abstracts de Han et al. (4675565) y Huang et al. (4825389) provienen de resultados de búsqueda, no del PDF. **Marcados como tales.**
7. **No existe umbral canónico publicado para PBO** — el 0.20 de §6.3 es mi juicio, etiquetado como [OPINIÓN].

**Reparto:** de ~85 afirmaciones sustantivas, **~60 [EVIDENCIA]** (de las cuales ~15 verificadas por ejecución propia en esta sesión), **~20 [OPINIÓN]/[INFERENCIA]** etiquetadas, **~5 [EVIDENCIA DÉBIL]** con la debilidad explicitada.

---

## 13. Fuentes

**Papers ancla**
- Zarattini, Pagani & Barbon (2025), *Catching Crypto Trends* — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907 · PDF https://concretumgroup.com/wp-content/uploads/2026/02/Catching-Crypto-Trends.pdf
- Han, Kang & Ryu (2023), *TS and CS Momentum in the Cryptocurrency Market under Realistic Assumptions* — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565
- Huang, Li, Wang & Zhou (2020), *Time series momentum: Is it there?*, JFE 135 — https://www.sciencedirect.com/science/article/abs/pii/S0304405X19301953
- Grobys & Shahzad (2026), *Cryptocurrency Momentum: Is It an Illusion?*, IJFE 31(2) — https://onlinelibrary.wiley.com/doi/10.1002/ijfe.70036 · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4633099
- *Momentum Trading in Cryptocurrencies: TS vs CS* (VU, 2026) — https://www.journals.vu.lt/BATP/en/article/download/44540/42590/138419
- *Cryptocurrency anomalies and economic constraints*, IRFA 2024 — https://www.sciencedirect.com/science/article/abs/pii/S1057521924001509
- Bui & Nguyen (2026), *Systematic Trend-Following with Adaptive Portfolio Construction* — https://arxiv.org/abs/2602.11708 ⚠️ ver §4.5

**Metodología estadística**
- Bailey & López de Prado (2014), *The Deflated Sharpe Ratio* — https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Bailey, Borwein, López de Prado & Zhu (2014), *Pseudo-Mathematics and Financial Charlatanism*, Notices AMS 61(5) — https://www.ams.org/notices/201405/rnoti-p458.pdf
- Bailey, Borwein, López de Prado & Zhu, *The Probability of Backtest Overfitting* — https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Implementaciones: https://github.com/esvhd/pypbo · https://cran.r-project.org/web/packages/pbo/

**Intradía / reversión**
- Wen, Bouri, Xu & Zhao (2022), *Intraday return predictability in the cryptocurrency markets* — https://www.sciencedirect.com/science/article/abs/pii/S1062940822000833
- *Bitcoin intraday time-series momentum* (Univ. Reading) — https://centaur.reading.ac.uk/100181/
- *ML-Based Bitcoin Trading Under Transaction Costs: Walk-Forward Forecasting* — https://arxiv.org/html/2606.00060v1
- *Another look at trading costs and short-term reversal profits* (EFMA) — https://www.efmaefm.org/0efmameetings/efma%20annual%20meetings/2011-Braga/papers/0259.pdf

**Fibonacci**
- *Automatic identification and evaluation of Fibonacci retracements: Empirical evidence from three equity markets*, ESWA 2022 — https://www.sciencedirect.com/science/article/abs/pii/S0957417421012495
- *Energy crypto currencies and leading U.S. energy stock prices: are Fibonacci retracements profitable?*, Financial Innovation 2022 — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8752186/

**Decay / crowding / industria**
- *Not All Factors Crowd Equally: Modeling, Measuring, and Trading on Alpha Decay* — https://arxiv.org/pdf/2512.11913
- *Publication Bias in Asset Pricing Research* — https://arxiv.org/pdf/2209.13623
- Cambridge Associates, *Does Trend-Following's Recent Struggle Signal That the Strategy Is Structurally Broken?* — https://www.cambridgeassociates.com/insight/does-trend-followings-recent-struggle-signal-that-the-strategy-is-structurally-broken/
- Man Group, *In Crypto We Trend* — https://www.man.com/insights/in-crypto-we-trend

**Datos y costes (2026)**
- Binance Futures funding rate API — `https://fapi.binance.com/fapi/v1/fundingRate`
- Binance Spot klines API — `https://api.binance.com/api/v3/klines`
- CF Bitcoin Kraken Perpetual Funding Rate Index (KFRI) — https://www.cfbenchmarks.com/data/indices/KFRI
- Hyperliquid fees — https://hyperliquidguide.com/guides/fees
- Comparativa de fees Hyperliquid vs Binance 2026 — https://www.coinperps.com/learn/hyperliquid-vs-binance-fees
