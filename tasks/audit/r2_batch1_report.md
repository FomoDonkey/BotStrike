# Auditoría Ronda 2 — TANDA 1 (áreas críticas para el dinero)

**Fecha:** 2026-08-31 · **Áreas:** `strategies`, `risk_sizing`, `backtest_parity` · **Método:** cada hallazgo verificado por dos lentes independientes (lectura de código + reproducción ejecutando las clases reales del repo con `py -3.12` sobre klines reales de Binance Futures).
**Contexto del dueño:** capital $1.000 · residente en España · Binance en modo solo-retirada desde el 1-jul-2026 (sin licencia MiCA) · hoy **Mean Reversion es la única estrategia con capital** (50 % de asignación); Fibonacci fue congelado en Fase 0 por falta de evidencia.

**Pregunta que responde este informe:** *después de esta tanda, ¿qué impide que este bot gane dinero, y en qué orden se arregla?*

---

## 1. Resumen ejecutivo (5 puntos, con números)

1. **La única estrategia con capital no tiene edge, ni siquiera bruto.** Sobre 149,7 días de klines reales y 2.284 trades simulados con el código exacto de producción, el retorno **BRUTO** medio por trade de Mean Reversion es −0,90 / −0,63 / −2,05 / +0,45 bps (ETH/SOL/ADA/BTC) con errores estándar de 1,2–2,6 bps: **cero estadístico**. En neto (11 bps de fricción: taker 4 bps × 2 + slippage 1,5 bps × 2) queda **PF 0,40–0,60 y −10,5 / −13,1 bps por trade, t-stat −5 a −8,7**. Dos controles lo cierran: entradas aleatorias con la misma frecuencia rinden igual, e **invertir el lado de todas las señales no mejora el resultado** — una señal con información direccional no sobrevive a esa prueba.
2. **Traducido a dinero: la sangría es de 6–13 % mensual sobre la cuenta.** Con ETH+SOL+ADA activos son ~11,5 trades/día × ~12 bps sobre el notional real que permite el cap de apalancamiento (medido con el sizer real: $325 en RANGING, $250 en UNKNOWN, $150 en TRENDING) → **$2–4,5/día = $62–133/mes sobre $1.000**. Todo es fricción pura. Para calibrar: la mejor estrategia con evidencia replicada del research (trend diario, Sharpe neto esperado 0,7) espera **+$60 al AÑO** con este capital. El bot pierde en un mes lo que la mejor alternativa documentada gana en dos años.
3. **No hay ningún instrumento fiable para medir edge en este repo.** Experimento ejecutado (3 días de BTC-USD futures 1m, misma estrategia, mismos indicadores, mismo detector de régimen): el backtester genera 24 señales, la ruta live 36, comunes 18 → **solapamiento Jaccard 42,9 %**. Causa dominante: **una línea** (`backtester.py:366` alimenta 501 barras frente a `MAX_BARS=2000` del live), que deja el filtro 1H de MR con **8 velas horarias en vez de 33** → ADX mediano 85,2 (warm-up) vs 43,9 real y **el SIGNO del filtro se invierte en el 38–41 % de las muestras**. De los 39 pasos del ciclo live tabulados, **26 divergen materialmente**.
4. **Los arreglos de la ronda 1 empeoraron la paridad en vez de mejorarla.** El fix de `exit_fibonacci` (commit `b3dbf75`) tocó **solo el lado live**: `git show b3dbf75 -- backtesting/backtester.py` sale **vacío**. Antes ambos motores ignoraban la señal (eran consistentes); hoy live cierra por señal y el backtest deja la posición huérfana hasta el SL/TP duro (reproducido: 1 señal `exit_fibonacci` emitida → 0 ejecutadas → salida `CLOSE_EOD`). Lo mismo con la gestión de posición con entradas bloqueadas y con `kelly_risk_pct`.
5. **El motor de riesgo no dimensiona por riesgo y además se auto-bloquea en silencio.** Entrega **0,061–0,117 % del equity por trade ($0,61–$1,18) frente al 1,5 % configurado** (12,7×–24,6× por debajo), porque manda `allocated_capital × leverage` y no la distancia al stop → todos los límites de pérdida son decorativos (harían falta 42–82 pérdidas completas para tocar el tope diario de $50). Y a partir del **trade cerrado nº 30**, el modelo de Risk of Ruin **pausa TODAS las entradas de forma permanente y sin alerta** (deadlock: pausado → sin fills → nunca se recalcula). Con el rendimiento documentado del propio repo (PF 0,85, WR 20 % en paper) el disparo no es probable, **es seguro**; y un Monte Carlo con edge VERDADERO positivo pausa falsamente el **35,1 %** de las veces.

---

## 2. Hallazgos confirmados (ordenados por severidad)

| ID | Severidad final | Área | Archivo:línea | Título | Fix en 1 línea |
|---|---|---|---|---|---|
| `strategies-01` | **P0** | strategies | `strategies/mean_reversion.py:21` | Mean Reversion (única estrategia con capital) no tiene edge bruto: PF 0,40–0,60 y −10,5/−13,1 bps netos por trade sobre 150 días reales, t-stat −5 a −8,7 | Congelar MR igual que Fibonacci: `allocation_mean_reversion = 0.00`, `REGIME_WEIGHTS[*][MEAN_REVERSION] = 0.00`, `SYMBOL_STRATEGY_MAP → set()` para ETH/SOL/ADA |
| `backtest_parity-02` | **P0** | backtest_parity | `backtesting/backtester.py:366` | La ventana de 501 barras produce solo 42,9 % de solapamiento de señales con el live y el filtro 1H de MR se invierte de signo el 38–41 % del tiempo | `from core.market_data import MAX_BARS` y `window_start = max(0, i - MAX_BARS + 1)`; test de paridad de señales buffer-live vs buffer-backtest |
| `backtest_parity-01` | **P0** | backtest_parity | `backtesting/backtester.py:495` | `exit_fibonacci` sigue ignorado por los dos backtesters; el fix de ronda 1 solo tocó el lado live y AGRANDÓ la brecha | Extraer `is_exit_signal` a un único sitio e importarlo en `backtester.py:495` y `:1093`; test de regresión que cierre la posición en ambos motores |
| `risk_sizing-01` | **P0** ⬆ (elevado desde P1 por ambos verificadores) | risk_sizing | `core/quant_models.py:348` | Risk of Ruin pausa TODAS las entradas de forma permanente y silenciosa desde el trade nº 30: fórmula mal aplicada, gate global, deadlock y sin alerta | Sustituir el escalón por `compute_empirical` (bootstrap ya escrito, 0 callers) con cota superior del IC, añadir cooldown/probation, RoR por estrategia, `min_trades` configurable y `notify_risk_event('ror_pause')` |

**Nota sobre la elevación de `risk_sizing-01` a P0:** entró en la tanda como P1 y los **dos** verificadores independientes concluyeron que la severidad correcta es P0. El razonamiento es el mismo en ambos: es una **parada total, permanente y no notificada** de la operativa. El bot queda con health OK, WebSocket conectado y señales generándose, y no opera. En un sistema que corre desatendido con `Restart=always`, un fallo silencioso que aparenta salud es peor que una caída.

**Refutados en esta tanda:** ninguno. Los cuatro hallazgos presentados sobrevivieron a dos intentos independientes de refutación, tres de ellos con reproducción ejecutando código real (confianza 0,91–0,95).

---

## 3. Una lectura por área

### 3.1 `strategies` — veredicto: **SIN EDGE. La estrategia financiada debe congelarse hoy.**

Lo esencial no es un bug, es una ausencia. Mean Reversion no falla por estar mal calibrada: falla porque **la lógica no contiene información direccional**. Los dos controles que lo demuestran son los que importan y no admiten interpretación amable: entradas aleatorias con la misma frecuencia rinden igual, y **invertir el lado de todas las señales no cambia el resultado** (ETH −0,27 vs −6,24 bps; SOL +8,66 vs −13,65). Una señal con edge, al invertirla, debe perder claramente más. Los retornos forward firmados son ~0 a 5/15/30 minutos y **negativos** a 60/240 minutos (t = −3,2 a −6,3). Sobre eso, el código tampoco hace lo que promete su propio docstring (`mean_reversion.py:21`, "breakeven to slightly positive"): el "filtro clave de tendencia 1H" **deja pasar el 99,1–99,4 % de las barras** (el ADX de Wilder sin seeding arranca en ~100 y con 33 barras conserva 9 puntos de sesgo), la única puerta de coste (`:268-271`) elimina el **0,33 %** de las señales y está hardcodeada a 14 bps frente a los 11 de la config, `bars_held` vale **0 para siempre** (el stale-exit de 24 h y el trailing "tight" son código muerto verificado), y los umbrales RSI adaptativos leen una columna que no existe y en una escala equivocada (0-1 vs 0-100). Los **7 hallazgos P1/P2 de estrategias de la ronda 1 (F04, F05, F06, F10, F11, F16, F17) siguen TODOS abiertos**. Medí explícitamente si las puertas de higiene rescatan la estrategia (ATR ≥ 2×coste, net_rr ≥ 1,5, ambas): **no** — el neto sigue en −11,5/−15,1 bps. Ningún ajuste de SL/TP arregla un edge bruto nulo. Lo positivo, y conviene decirlo: no hay look-ahead (réplica validada 48/48 señales idénticas contra la clase real), no hay estado compartido entre símbolos, los guards de NaN y división por cero son correctos, la fórmula de Wilder del ADX es correcta (falla solo el warm-up) y la congelación de Fibonacci está bien hecha y doblemente cerrada. **El código es competente; la hipótesis es falsa.**

### 3.2 `risk_sizing` — veredicto: **NO es un módulo de riesgo. Es un módulo de asignación con frenos que muerden en el sitio equivocado.**

Reproducido con clases reales y precio/ATR en vivo: el sizer entrega **0,061–0,117 % del equity por trade** frente al 1,5 % configurado, porque `strategies/base.py:113` manda `allocated_capital × leverage` en vez de `equity × risk_per_trade_pct / distancia_al_stop`. La consecuencia es que **todo el aparato de límites es decorativo**: harían falta 42–82 pérdidas completas para tocar el tope diario de $50. Los tres caps de exposición no cierran ninguno: `_check_total_exposure` es código muerto (cap $3.000 frente a un máximo alcanzable de $1.300), el validador de config no mira la suma de `max_position_usd` ($1.300 = 130 % del equity) y el chequeo de margen del 50 % es **por señal, no agregado** (52,5 % hoy, 65 % en el peor caso). En cambio, los frenos que **sí** muerden son globales y contraproducentes: RoR pausa todo desde el trade 30 (P0, arriba) y el freno por rachas es exponencial sin suelo, dejando el **13–26 % de las entradas por debajo del `minNotional` real — que live rechaza y paper rellena**, lo que por sí solo **invalida el soak de CT 104 como evidencia**. Nada del estado de riesgo se persiste y el servicio corre con `Restart=always`: cualquier reinicio resetea límite diario, circuit breaker, halt por drawdown y contador de rachas. El drawdown se mide sobre un equity sin PnL no realizado (Binance: `marginBalance = walletBalance + unrealizedProfit`), también en paper. Vol targeting está clavado en ×1,5 — **infla el 50 % todas las posiciones en vez de protegerlas** — y se aplica después del cap de apalancamiento. Kelly es inalcanzable (0 trades en la DB tras 5 sesiones). Lo positivo: el guard `entry ≈ stop` introducido en `fb073a1` es **correcto** en tamaño; le falta un suelo económico (0,1 bps frente a 11 bps de fricción) y validar el signo del stop.

### 3.3 `backtest_parity` — veredicto: **NO EXISTE PARIDAD, y no es aproximada. Ningún número de `tasks/todo.md` es válido.**

El experimento está ejecutado, no razonado: 3 días reales de BTC-USD futures 1m, misma estrategia, mismos indicadores, mismo `RegimeDetector`, solo entradas → backtester 24 señales, live 36, comunes 18, **Jaccard 42,9 %**. **La mitad de lo que el bot haría en producción no aparece en ningún backtest.** La causa dominante es una sola línea (`backtester.py:366`, 501 barras vs `MAX_BARS=2000`) y su efecto es direccional, no de ruido: 501 barras → `//60` → **8 velas horarias**, sobre las que ADX(14) y EMA(26) no han convergido, y el signo del filtro 1H —que es la puerta que **elige el lado de la operación** (`mean_reversion.py:203`, `:223-224`)— se invierte en el 38–41 % de las muestras. Control ejecutado que aísla la causa: los indicadores de 1m son **idénticos a 15 decimales** con 501/2000/50.000 barras, así que toda la divergencia viene del multi-timeframe remuestreado. De los 39 pasos del ciclo live tabulados, **26 divergen materialmente**: SL rellenado sin slippage en backtest mientras live paga 1,5×, ruta de orden (live enruta por `SmartOrderRouter` con LIMIT y fill estocástico sin semilla, el backtest siempre llena MARKET al 100 %), `notify_external_exit` nunca llamado (tras un SL la estrategia se sigue creyendo dentro y no aplica cooldown), salidas evaluadas ~20 veces/minuto en live vs 1 vez por barra en backtest, barras etiquetadas con hora de cierre en live y de apertura en las klines (desfase de 60 s), agujeros de serie nunca rellenados y agrupación por posición en vez de por reloj (una vela "5m" puede cubrir 15 minutos). **Los 6 hallazgos de ronda 1 en esta área siguen abiertos**, la congelación Fase 0 de Fibonacci no llega al backtester (abre 10 posiciones FIB en BTC), y el **único backtest que un usuario puede lanzar** (UI/desktop → `POST /api/backtest/run`) usa datos SPOT caducados el 2026-04-03, ignora `start_date`, devuelve 0 barras con `end_date` y reporta **Sharpe −0,27 cuando el real es −15,97 (59× de diferencia, verificado end-to-end)**. Los datos correctos (`data/binance_futures/`, 150 días, 0 gaps) los escribe **un** archivo y los leen **cero**.

---

## 4. La pregunta de la paridad backtest↔live

> **¿Puede un backtest de este repositorio predecir lo que hará el bot en vivo?**

# **NO.**

Y no es un "no" de matiz. Es 42,9 % de solapamiento de señales medido, con el filtro que decide la **dirección** de la operación invertido en 4 de cada 10 muestras. Un backtester que coincide en el 43 % de las entradas y se equivoca de lado en el 40 % de las que sí comparte **no está midiendo una versión ligeramente distinta de la estrategia: está midiendo otra estrategia**.

**Consecuencia práctica, en tres frases:**

1. **Todo PF, Sharpe, WR y drawdown producido por este repo hasta hoy es inadmisible como evidencia** — incluidos los números que justificaron asignar el 50 % del capital a Mean Reversion y los que justificaron congelar Fibonacci (la decisión de congelar Fibonacci resultó correcta, pero **por suerte, no por evidencia**).
2. **Ninguna estrategia puede ser aprobada para capital real hasta que exista un motor con paridad demostrada por test**, porque hoy el resultado del backtest no es una estimación ruidosa del resultado live: es un número no relacionado.
3. **Corolario incómodo pero central:** el hallazgo `strategies-01` (MR sin edge) **no viene del backtester del repo** — viene de ejecutar la clase real de producción con la ventana de producción (`MAX_BARS=2000`). De hecho, el backtester propio del repo produce **CERO trades** para MR sobre 5.000 barras de ETH: la herramienta del proyecto es literalmente incapaz de ver su propio problema más caro. Ese contraste es la mejor prueba de por qué la paridad es P0.

---

## 5. Veredicto sobre Mean Reversion: **se congela, con el mismo criterio que Fibonacci**

**Decisión: `allocation_mean_reversion = 0.00`. Hoy.**

**El argumento estadístico.** Fibonacci se congeló con **5 cierres y 20 % de win rate** — una muestra sin poder estadístico alguno, congelada por prudencia. Mean Reversion conserva el 50 % de la asignación con **2.284 trades y PF 0,45**. Es una incoherencia del proyecto consigo mismo: se aplicó el criterio duro a la evidencia débil y el criterio blando a la evidencia fuerte. Con 2.284 trades y t-stat entre −5 y −8,7 en neto, **la duda ya no existe**: no es que no sepamos si MR gana, es que sabemos con alta confianza que pierde, y sabemos por qué (fricción sobre un edge bruto nulo). El único argumento que quedaría a favor —"el edge bruto es solo ligeramente negativo, bajemos costes"— muere con el test de inversión de lado: si invertir todas las señales no mejora nada, **no hay señal que abaratar**.

**El argumento de la evidencia externa.** `tasks/research_r2_trend_evidence.md` §8 cierra el caso desde fuera, y coincide con la medición interna sin haberla visto: la reversión intradía en cripto **existe estadísticamente** (Wen et al. 2022, NAJEF; momentum intradía BTC con Sharpe reportado 0,53) pero **no es explotable a este tamaño**. El Sharpe bruto documentado (~0,5) es **la mitad** del trend diario (1,14 en la réplica propia sobre Binance) y requiere un orden de magnitud más de operaciones, cada una pagando 8–15 bps. La aritmética del research es la misma que la mía: 4 operaciones/día × 8 bps × 365 = **117 % anual solo en costes**; MR opera **11,5 veces al día**. La literatura independiente lo confirma (*"Due to transaction costs, reversal strategies are not applicable, and profitable strategies do not exist"*, EFMA). Es una estrategia para market-makers con rebates y colocation, no para un bot retail con $1.000.

**Los tres números que cierran la decisión:**

| | Mean Reversion (medido) | Trend diario Donchian (replicado) |
|---|---|---|
| Edge bruto por trade | −0,90 a +0,45 bps (SE 1,2–2,6) = **cero** | positivo y persistente, skew +1,09 |
| Coste anual implícito | ~11,5 trades/día × 11 bps ≈ **460 %/año** | turnover 7,6×/año ≈ **0,76 %/año** a 10 bps |
| Resultado esperado sobre $1.000 | **−$62 a −$133 al MES** | **+$60 al AÑO** (Sharpe 0,7, vol 9 %) |

**Condiciones de descongelación (idénticas para cualquier estrategia, sin excepciones):** (a) edge **BRUTO** fuera de muestra > 3×SE; (b) el test de inversión de lado debe empeorar el resultado de forma clara; (c) las puertas `net_rr ≥ 1,5` y `atr_bps ≥ 2 × cost_bps` activas; (d) medido en un motor con **paridad demostrada por test**, no en el actual.

**Estado del bot tras congelar:** cero estrategias con capital. Eso es lo correcto. Un bot que no opera pierde $0/mes; el bot de hoy pierde $62–133/mes. **Parar es la operación con mayor retorno esperado disponible en este momento.**

---

## 6. Plan de acción priorizado y secuenciado

La lógica del orden es deliberada: **primero se corta la sangría, luego se repara el instrumento de medida, y solo entonces se busca un edge.** Arreglar el sizer o la persistencia antes de detener MR es optimizar la velocidad de una pérdida.

### P0 — HOY (total ≈ 6-8 h)

| # | Acción | Hallazgo | Esfuerzo | Criterio de aceptación |
|---|---|---|---|---|
| 1 | **Congelar Mean Reversion.** `allocation_mean_reversion = 0.00`, `REGIME_WEIGHTS[*][MEAN_REVERSION] = 0.00`, `SYMBOL_STRATEGY_MAP → set()` para ETH/SOL/ADA. Doble cierre, como Fibonacci | `strategies-01` | **30 min** | Test: con la config nueva, 0 señales de entrada ejecutadas en 24 h de replay |
| 2 | **Ventana única de barras.** `from core.market_data import MAX_BARS` en `backtester.py:366`; en `RealisticBacktester` sustituir `ohlcv_df.iloc[:i+1]` (`:900`) por la ventana acotada (arregla de paso el O(n²)) | `backtest_parity-02` | **1-2 h** | Test de paridad: mismo df, buffer-live vs buffer-backtest → listas de señales **idénticas** |
| 3 | **`is_exit_signal` única fuente de verdad.** Extraer a `core/types.py` e importar en `backtester.py:495` y `:1093` | `backtest_parity-01` | **1 h** | Test de regresión: una señal `exit_fibonacci` cierra la posición en los dos motores |
| 4 | **RoR: quitar el freno de mano.** `notify_risk_event('ror_pause')` + cooldown/probation (patrón de 01-F03) + `min_trades` configurable y ≫30 + gate por estrategia; agregar fills parciales a un único trade cerrado | `risk_sizing-01` | **3 h** | Test: 30 pérdidas → pausa **con alerta**; +3.600 s → entrada permitida en probation |
| 5 | **Bridge → datos de futures.** `_run_backtest_sync` apunta a `data/binance_futures/klines/`, normaliza `timestamp` ms→s antes de filtrar; ídem `main.py:942` | `backtest_parity-03` (sin verificar) | **1-2 h** | El backtest del UI devuelve barras > 0 con `end_date` y un Sharpe coherente con el CLI |

> **Después del P0 el bot no pierde dinero y el backtester deja de mentir sobre qué señales genera.** Eso es todo lo que debe conseguirse hoy.

### P1 — ESTA SEMANA (total ≈ 3-4 días)

| # | Acción | Hallazgos | Esfuerzo |
|---|---|---|---|
| 6 | **Reescribir el sizer como sizer de riesgo:** `risk_amount = equity × risk_per_trade_pct`, `allocated_capital` degradado a **cap de notional** (`min(size_usd, alloc × lev)`), `symbol_share` sobre símbolos **elegibles** | `risk_sizing-06` | 1 día |
| 7 | **Suelo de `minNotional` real + replicarlo en `paper_simulator`.** Sin esto ningún número del soak vale. Cap del exponente de rachas (suelo 0,125), decaimiento temporal, contador por símbolo, notificación | `risk_sizing-02` | 1 día |
| 8 | **Persistir el estado de riesgo** (`data/risk_state.json`, escritura atómica) y sembrar `update_equity()` desde `GET /fapi/v2/account` al arrancar. Sin esto, `Restart=always` borra todos los límites | `risk_sizing-03` | 0,5 día |
| 9 | **Cerrar los caps de exposición:** `_check_total_exposure` vivo, validación de `sum(max_position_usd)` en `__post_init__`, chequeo de margen **agregado** | `risk_sizing-04` | 0,5 día |
| 10 | **Paridad de ejecución:** `simulate_exit_fill()` compartida (SL con slippage 1,5×, gap-through al open), `notify_external_exit` en las ramas SL/TP, patrón `entries_allowed` en ambos motores, RNG con semilla | `bp-04`, `bp-07`, `bp-08`, `bp-09` | 1 día |
| 11 | **Higiene de estrategia** (aunque MR esté congelada, se hereda al siguiente motor de señales): buffer 1H propio por REST (`interval=1h&limit=300`), `bars_held` por timestamp, puertas `net_rr ≥ 1,5` y `atr_bps ≥ 2×cost` con coste leído de la config | `strategies-02/04/05/06` | 1 día |
| 12 | **Un solo `DATA_ROOT`** en `config/settings.py` que lean todos los runners y el bridge; marcar `data/binance/` como obsoleto | `bp-13` | 1 h |

### P2 — ESTE MES (total ≈ 3-4 semanas)

| # | Acción | Por qué ahora y no antes | Esfuerzo |
|---|---|---|---|
| 13 | **Motor de backtest ÚNICO.** Fusionar `Backtester` y `RealisticBacktester`; el motor debe pasar por `should_strategy_trade(..., symbol=)` y `get_allocation()`, resolver el desempate SL/TP igual que live, y evaluar salidas sub-barra | Requiere el P1 hecho; es la refactorización que hace admisible cualquier número futuro | 1 semana |
| 14 | **Suite de paridad como gate de CI.** Mismo dataset por los dos caminos → diferencia de PnL < X bps o falla el build. Sin este gate, la paridad se vuelve a romper en la próxima ronda de fixes (ya pasó en la ronda 1) | Es la única defensa estructural contra la regresión que documenta este informe | 3 días |
| 15 | **Implementar y validar el candidato con evidencia: TSMOM diario tipo Donchian** (ensemble 5–30d, vol target, trailing = max(stop, mid), umbral de rebalanceo 20 %), 3 activos, **long-only, spot**, según `research_r2_trend_evidence.md` §2 y §11 | Es la única familia con evidencia replicada y con turnover (7,6×/año) compatible con $1.000. Pero **medirla en el motor actual sería repetir el error** | 1 semana |
| 16 | **Resolver el venue.** Binance está en modo solo-retirada para residentes en España desde el 1-jul-2026: hoy el bot no puede operar en real aunque tuviera edge. Verificar cuenta + apalancamiento retail real (conflicto 10× vs 2× sin resolver en el research) en el venue elegido | Bloqueante para live, pero **no** para P0/P1: no tiene sentido resolver dónde operar antes de tener qué operar | 3-5 días |
| 17 | **P2 restantes:** ATR de entrada vs actual en las salidas, resampleo por reloj, caché de umbrales de régimen por reloj de pared, funding descalibrado 3 órdenes de magnitud, Kelly, drawdown ciego al no realizado, README que describe un sistema que no existe | Higiene; ninguno mueve la aguja del edge | 1 semana |

### La respuesta corta a "¿qué impide que este bot gane dinero?"

Cuatro cosas, en este orden causal exacto:

1. **No tiene edge** — la única estrategia financiada pierde 11-13 bps por trade, 11,5 veces al día.
2. **No puede saber si tiene edge** — el backtester mide otra estrategia (42,9 % de solapamiento).
3. **Aunque lo tuviera, no lo capturaría** — el sizer entrega 1/20 del riesgo configurado y el gate de RoR apaga el bot en silencio en el trade 30.
4. **Aunque lo capturara, no puede ejecutarlo** — el venue está cerrado para un residente español.

Y una quinta, que es de expectativas y conviene fijar por escrito antes de invertir un mes más: con $1.000 y la mejor estrategia documentada, la expectativa honesta es **~+$60/año con un 25-30 % de probabilidad de cerrar el año en pérdidas**. El valor de este proyecto a este tamaño **no es el dinero, es la infraestructura verificada**. Merece la pena decirlo antes, no después.

---

## 7. Anexo

### 7.1 Refutados

**Ninguno.** Los 4 hallazgos presentados a verificación sobrevivieron a dos intentos independientes de refutación (confianza 0,91–0,95). Se registran las salvedades honestas de los verificadores, que **matizan pero no refutan**:

- `strategies-01`: el replay histórico no puede aportar `obi` (1 de 4 confirmaciones) y `risk_manager.validate_signal` (VPIN/Hawkes/Kyle) bloquea parte de las entradas en vivo → el **$/día es una cota superior**, no una estimación puntual.
- `risk_sizing-01`: el valor de RoR **sí aparece como número** en el snapshot periódico de Telegram (`telegram.py:439`), lo que suaviza "silencioso" pero no aporta alerta de pausa. `order_engine.py:519` filtra `realized_pnl != 0`, así que los fills de entrada no alimentan el modelo — pero cada fill parcial de **cierre** sí cuenta como trade independiente.
- `backtest_parity-01`: el `RealisticBacktester` nunca instancia FIB, así que su hueco es **latente**, no ejercitado hoy; solo el motor simple lo ejecuta.
- `backtest_parity-02`: la subcláusula "un ADX inflado abre la puerta casi siempre" **no** se reprodujo como diferenciador (el gate ADX≥20 se abrió al 100 % con **ambas** ventanas). Lo decisivo es la **inversión de signo (38-41 %)**, no la inflación. Debilita una cláusula de apoyo, no el titular ni la severidad.

### 7.2 P0/P1 identificados pero **sin verificar** por tope de tanda (20)

Requieren una segunda tanda de verificación antes de tratarse como hechos. Están ordenados por área.

**P0 (1)**
- `backtest_parity-03` — `server/bridge.py:1559`: el único backtest lanzable por el usuario (UI/desktop → `POST /api/backtest/run`) usa SPOT caducado, ignora `start_date`, devuelve 0 barras con `end_date` y deprime el Sharpe 59×.

**P1 — strategies (5)**
- `strategies-02` — `mean_reversion.py:203`: 01-F05 abierto; el "filtro clave de tendencia 1H" deja pasar el 99,1–99,4 % de las barras.
- `strategies-03` — `backtester.py:366`: paridad rota en el filtro 1H (8 barras horarias, ADX medio 86, dirección discrepante el 35 %).
- `strategies-04` — `mean_reversion.py:342`: 01-F04 abierto; `bars_held` = 0 para siempre → stale-exit de 24 h, trailing "tight" y expiración de impulsos Fib son código muerto.
- `strategies-05` — `mean_reversion.py:267`: 01-F06 abierto; la única puerta de coste elimina el 0,33 % de las señales, el 6,6 % de las que pasan tienen R:R neto < 1 (mínimo 0,23) y el coste está hardcodeado a 14 bps vs 11 de config.
- `strategies-06` — `mean_reversion.py:449`: tras cada reinicio MR opera 27,3 h con el filtro 1H sobre 6→33 barras horarias.

**P1 — risk_sizing (6)**
- `risk_sizing-02` — `risk_manager.py:290`: freno por rachas global y exponencial sin suelo; 13–26 % de entradas bajo `minNotional` (live rechaza, paper rellena); deadlock con ≥9 pérdidas seguidas.
- `risk_sizing-03` — `risk_manager.py:40`: cero persistencia del estado de riesgo con `Restart=always` + watchdog.
- `risk_sizing-04` — `risk_manager.py:321`: el tope de exposición del 60 % es inaplicable (`_check_total_exposure` muerto, validador ciego a la suma, margen por señal).
- `risk_sizing-05` — `quant_models.py:124`: vol targeting clavado en ×1,5, medido sobre un equity que solo se mueve con PnL realizado, y aplicado **después** del cap de apalancamiento.
- `risk_sizing-06` — `base.py:113`: el sizer no es un sizer de riesgo (0,061–0,117 % vs 1,5 % configurado, 12,7×–24,6×).
- `risk_sizing-07` — `portfolio_manager.py:243`: el fix 01-F03 es incorrecto; el gate de rendimiento normaliza por el presupuesto de config ($15) cuando el riesgo real es ~$0,80 → no puede bloquear jamás.

**P1 — backtest_parity (8)**
- `bp-04` `backtester.py:468` (gestión de posición con entradas bloqueadas) · `bp-05` `:474` (la congelación de FIB no llega al backtester) · `bp-06` `market_data.py:384` (hora de cierre vs apertura, minuto duplicado en la costura del seed) · `bp-07` `paper_simulator.py:490` (live enruta por `SmartOrderRouter`, backtest siempre MARKET al 100 %) · `bp-08` `backtester.py:424` (SL sin slippage en backtest vs 1,5× en live) · `bp-09` `:423` (`notify_external_exit` nunca llamado) · `bp-10` `mean_reversion.py:161` (salidas ~20×/min vs 1×/barra) · `bp-11` `market_data.py:375` (agujeros nunca rellenados; agrupación por posición, no por reloj).

### 7.3 P2/P3 (23) — higiene, no mueven la aguja del edge

**strategies (P2):** RSI adaptativo muerto y en escala equivocada (`:210`) · salidas denominadas en ATR **actual** y no en el de entrada → el SL software ejecuta de media al 78 % de la distancia prometida, p10 = 63 % (`:348`) · resampleo posicional re-cortado cada barra → 25–32 % de señales repetidas (`:439`) · el seed guarda la vela **en formación** como cerrada (`market_data.py:144`) · thresholds del `RegimeDetector` cacheados por reloj de pared (`:141`) · `has_obi` estructuralmente imposible en backtest (`backtester.py:452`) · la tabla de ATR del docstring está etiquetada BTC pero son números de ETH — **y sobre esa premisa se asignó Fibonacci a BTC** (`:8`) · impulsos Fib que nunca caducan (`fibonacci_retracement.py:249`) · el README describe un sistema que no existe (`:158`).
**strategies (P3):** `get_regime_confidence` lanza `TypeError` · metadata con multiplicadores de módulo y sin `net_rr` · señales de salida re-divididas por el precio del snapshot · código muerto y comentarios que contradicen al código · `allocation_*` no gobierna el capital pero es lo que se muestra al usuario.
**risk_sizing (P2):** guard `entry≈stop` sin suelo económico ni validación de signo (un LONG con stop por encima pasa a tamaño completo y en live queda sin SL) · filtro de funding descalibrado 3 órdenes de magnitud · Kelly inalcanzable y discontinuo · drawdown/pérdida diaria/circuit breaker ciegos al no realizado también en paper · siete multiplicadores sobre `size_usd` sin suelo (ETH puede caer a $3,46 con `minNotional` $20).
**risk_sizing (P3):** siete parámetros de riesgo muertos o incoherentes con $1.000 · estado mutable compartido en `_current_weights` y `validate_signal` que desactiva el circuit breaker como efecto secundario.
**backtest_parity (P2):** `kelly_risk_pct` solo en live (divergencia latente) · desempate SL/TP distinto entre los dos motores y ninguno igual a live · look-ahead multi-timeframe en `RealisticBacktester` · reproducibilidad (caché por reloj de pared, `random` sin semilla) · reloj de pared para las pausas de riesgo temporizadas · tres runners rotos (`quant_audit`, `optimize_with_binance`, `exit_analysis`) · `download_futures_klines` trunca el funding al reejecutar · ninguna de las tres rutas de backtest usa datos de futures ni reconoce `exit_*`.
**backtest_parity (P3):** menores en `backtester.py` (`Any` sin importar, `atexit` registrado en cada `run()`, `slippage_bps` guardado ≠ aplicado).

---

*Informe de la Tanda 1, Ronda 2. Áreas restantes (exchange/ejecución, bridge/deploy/seguridad, desktop, persistencia, tests) pendientes de tandas posteriores. Ningún hallazgo de este informe fue aceptado sin un segundo verificador independiente.*
