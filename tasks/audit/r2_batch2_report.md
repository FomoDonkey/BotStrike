# Auditoría Ronda 2 — TANDA 2 (la maquinaria: ejecución y contabilidad)

**Fecha:** 2026-08-31 · **Áreas:** `fix_core`, `fix_exchange`, `persistence` · **Método:** cada hallazgo confirmado por dos lentes independientes (lectura del código real + reproducción ejecutando las clases del repo con `py -3.12`, sin modificar ningún archivo del proyecto). Suite completa: **132/132 tests pasan**.

**Contexto del dueño:** capital $1.000 · residente en España · Binance en modo **solo-reducir** desde jul-2026 · **hoy el bot NO opera a propósito**: todas las estrategias quedaron congeladas tras la Tanda 1, que midió **edge bruto nulo** en Mean Reversion.

**Pregunta que responde este informe — y no es "por qué pierde dinero":**

> Cuando el proyecto tenga una estrategia con edge demostrado (trend diario spot, `tasks/research_r2_trend_evidence.md`), **¿la MAQUINARIA de ejecución y contabilidad será digna de confianza para operarla?** ¿Ejecutaría bien las órdenes, cerraría las posiciones cuando toca, y los números que reporta (PnL, equity, fees, sesiones) serían ciertos?

---

## 1. Resumen ejecutivo (5 puntos, con números)

1. **El P0 de la Ronda 1 —"posición desnuda a 5x"— NO está cerrado; está abierto por DOS vías independientes, ambas reproducidas con el código real.** (a) `main.py:871` llama a `execution_engine.cancel_all()` **fuera de todo condicional**, así que cuando `close_all_positions()` lanza (timeout/5xx/ban) o devuelve `remaining` no vacío (-2022, -1111, qty < minQty), el bot **borra el SL/TP de una posición viva y se apaga inmediatamente después**. Agravante encontrado en la verificación y no descrito en el hallazgo original: `order_engine.py:561-563` **se traga la excepción del cliente** y devuelve `{"remaining": [], "errors":[...]}`, de modo que la rama `except` casi nunca se alcanza en producción, `remaining` es falsy, **no se emite ni el `logger.critical` ni la alerta de Telegram** — el fallo más probable en real es **totalmente silencioso**. (b) `server/bridge.py:387` —el **único camino de parada que se usa en producción** (`/api/bot/stop`, lifespan y watchdog; el CLI no se usa)— cancela los protectivos **ANTES** de cerrar nada y **nunca** invoca `_flatten_all()`. Es exactamente el orden que el propio docstring del fix declara prohibido: el fix F01 se aplicó al camino que nadie ejecuta.
2. **El apagado de producción puede además morir a mitad del cierre.** `bridge.py:394` envuelve el shutdown en `asyncio.wait_for(..., timeout=10)`. El flatten real son ~15 llamadas REST (4 símbolos × 3 intentos) + 1,8 s de sleeps fijos + `websocket.stop()` + `client.close()` + 2 POST a Telegram. PoC con el `server.bridge` real: con un flatten de 12 s la traza es `[CANCEL_ALL, engine cancelled, shutdown:BEGIN, close_all_positions:BEGIN]` y **"flatten completed? False"** → posición abierta, sin SL/TP, sin WS cerrado y sin cliente cerrado.
3. **`close_all_positions()` aplana TODA la cuenta de Binance Futures, no solo los símbolos del bot.** Reproducido: con `settings.symbols = [BTC, ETH, SOL, ADA]` y una posición manual ajena `DOGEUSDT 5000` en la misma cuenta, el motor emite **dos** `POST /fapi/v1/order`: `BTCUSDT SELL MARKET 0.01` **y** `DOGEUSDT SELL MARKET 5000 reduceOnly=true`. Disparadores por defecto: `close_positions_on_shutdown=True` (`settings.py:93`) + `systemctl restart botstrike-bridge` en **cada** despliegue (`deploy/update.sh:39`) + `_flatten_all("max_drawdown")` (`main.py:773`). Es decir: **un `git push` cierra a mercado, con taker fee, las posiciones manuales del dueño**; y un halt medido sobre el equity *del bot* liquida posiciones que el bot no gestiona.
4. **En LIVE la contabilidad que ve el dueño es la de PAPER, y ninguna operación real aparece jamás.** Las **dos únicas** rutas de la UI (`bridge.py:863` y `bridge.py:1427`) llaman `get_trades(source="paper")` **hardcodeado**, mientras `main.py:115` escribe las filas live con `source="live"`; y la ruta live (`main.py:359`) ni siquiera pasa `trade_type`, que queda en `""` por defecto. Reproducido con el repositorio real y 3 trades live de +250/−80/+400 (**PnL real 570,0**): `rows in DB: 3 | sources: {'live'} | trade_type: ['','','']` → `/api/trades` muestra **0 filas** y `/api/performance` **0 trades, 0 PnL, equity plana**. Y el segundo defecto es independiente: **corregir solo el `source` no arregla nada** (`perf closes con source='live' CORRECTO: 0`), porque el filtro `t.trade_type and t.trade_type != "ENTRY"` descarta la cadena vacía. Es un **apagón contable total en el único modo que mueve dinero**, sin error ni aviso.
5. **Los números que hoy se publican en paper también están inflados.** El Sharpe/Sortino se multiplica por **√(365 / días_operados)** porque `analytics/performance.py:610` omite los días sin trades del calendario — **medido ×2,76**. El **funding no existe** fuera del backtester (~11 %/año de nocional ignorado en perps), en live `net_pnl = total_pnl` es falso porque el `rp` de Binance viene **bruto de comisión**, y `/api/trades?limit=N` devuelve los N trades **MÁS ANTIGUOS** etiquetados como "los más recientes". La tabla `sessions` es inservible: sin heartbeat, nadie lee `end_time`, y la DB de producción ya tiene **3 sesiones huérfanas + 1 fila fantasma con `session_id=''`** (reproducida). Balance de la tanda: **58 hallazgos** (2 P0 y 2 P1 confirmados por doble lente, 16 P0/P1 sin verificar por tope, 38 P2/P3).

---

## 2. Hallazgos confirmados (ordenados por severidad)

| ID | Severidad final | Área | Archivo:línea | Título | Fix en 1 línea |
|---|---|---|---|---|---|
| `fix_core-02` | **P0** | fix_core | `server/bridge.py:387` | El camino de parada de **PRODUCCIÓN** (`stop_engine`) cancela los SL/TP **antes** de cerrar nada y nunca llama a `_flatten_all()`; además `wait_for(timeout=10)` aborta el cierre a mitad | Sustituir el bloque `cancel_all()` por `await engine.shutdown()` (ya idempotente) antes de cancelar la task; subir el timeout a ≥60 s; **nunca** cancelar órdenes desde el bridge |
| `fix_core-01` | **P0** | fix_core | `main.py:871` | `_flatten_all()` cancela los SL/TP **igualmente** cuando el cierre falla o deja posiciones abiertas: la posición desnuda que F01 decía arreglar sigue viva en la rama de error, y en silencio | Guardar antes de cancelar: `if remaining or not isinstance(result, dict) or result.get("errors"): logger.critical(...); await notifier.notify_error(...); return` — y tratar el `except` como `remaining` desconocido |
| `fix_core-03` | **P1** | fix_core | `exchange/binance_client.py:750` | `close_all_positions()` aplana **toda la cuenta** de futuros, no solo los símbolos del bot: un deploy o un halt cierra a mercado posiciones que el bot nunca abrió | Filtrar por `{c.symbol for c in settings.symbols}` y pasarlas como parámetro explícito `close_all_positions(symbols=...)`; "toda la cuenta" solo como opción deliberada de emergencia (ídem `order_engine.py:580-597`) |
| `persistence-01` | **P1** ⚠ | persistence | `server/bridge.py:863` | En LIVE la contabilidad mostrada es la de PAPER: `source="paper"` hardcodeado y `trade_type` nunca se rellena en la ruta live → 0 trades, 0 PnL, equity plana | `src = getattr(engine.trade_db, 'source', 'paper')` en `:863` y `:1427`, y en `main.py:359` pasar `trade_type="EXIT" if trade.pnl != 0 else "ENTRY"` + `entry_price`; mejor: un helper único de `TradeRecord` para las dos rutas |

**⚠ Nota sobre la severidad de `persistence-01`:** los dos verificadores confirmaron el defecto (confianza 0,90 y 0,93) pero **discrepan en la severidad**. Uno lo baja a P1 *solo* porque la ruta live está gateada por `BOTSTRIKE_ALLOW_LIVE` (`bridge.py:1329-1332`, 403 sin la variable) y por el bloqueo regulatorio; el otro **mantiene P0** por ser un apagón contable silencioso sobre dinero real que se activa con **una sola variable de entorno**. Se registra como **P1 latente con reclasificación automática a P0 el día que se active live**: es un bloqueante de go-live, no un defecto de higiene.

**Refutados en esta tanda:** ninguno (§7.1).

---

## 3. Una lectura por área

### 3.1 `fix_core` — veredicto: **los fixes de la Ronda 1 son correctos en el camino feliz y frágiles exactamente donde debían ser robustos.**

El patrón es sistemático y conviene nombrarlo, porque explica los cuatro hallazgos: **cada fix se testeó con el mock que devuelve éxito**. `tests/test_p0_round2.py:774-776` fija el resultado de `close_all_positions` a `{'closed':[...], 'remaining': [], 'errors': []}` de forma **incondicional**; ningún test ejerce el fallo, y por eso ninguno de los dos agujeros P0 se vio. El P0 original (posición apalancada sin protectivos) sigue vivo por dos vías verificadas con PoC sobre las clases reales: `_flatten_all` cancela sin guarda —y por la absorción de excepciones de `order_engine.py:561-563` lo hace **sin log crítico ni alerta**, que es el peor modo de fallo posible en un sistema desatendido— y `bridge.stop_engine()`, que es **el camino real de producción**, reintroduce literalmente el orden prohibido; su docstring apunta a `main.py:1080-1104`, líneas que hoy son el *data collector*: el bridge quedó **congelado en la versión pre-fix**. Lo que sí está bien y hay que decirlo: la **congelación de estrategias es hermética** (100/100 combinaciones a 0,0, `generate_signals` ni se llama), el camino entradas→salidas sigue íntegro, `is_exit_signal` arregla de verdad `exit_fibonacci` y se comparte con el backtester. Pero su docstring "single source of truth" es falso —hay **3 copias divergentes**, y la del risk manager bloquea `trailing_stop_hit` y `mm_unwind` durante un halt—, la otra mitad de 01-F02 nunca se aplicó (`mean_reversion.py:393` hace `_states.pop()` **antes** de que la salida se llene: un exit rechazado deja la posición huérfana y nunca se reintenta), la puerta de rendimiento pasó de "mata con −$0,03" a **inalcanzable** (exige −0,7324 R de media ≈ 22 % de drawdown, cuando el circuit-breaker salta al 10 %), y en live esa puerta mide el **PnL BRUTO** de Binance mientras en paper/backtest mide el neto: es **ciega a la sangría por fricción** que la Tanda 1 documentó. De las tres palancas de congelación, solo dos son reales: `allocation_* = 0.00` no interviene en el sizing — y es justo la variable que el dashboard muestra al usuario.

### 3.2 `fix_exchange` — veredicto: **la mecánica se arregló bien; ninguno de los tres P0 está cerrado del todo.**

Lo que la Ronda 1 (`b3dbf75`) hizo bien es real y verificado contra la API viva: redondeo con `Decimal`, `newClientOrderId` conforme al regex oficial `^[\.A-Z\:/a-z0-9_-]{1,36}$`, elección correcta de `-2013` para `GET /order`, `newOrderRespType=RESULT`, y el parseo `b`/`a` del depth —incluido el acierto de **no** gestionar `U/u/pu`, porque `@depth20@100ms` es un snapshot completo y no un diff—. Los 12 valores de tick/step/minQty de `DEFAULT_SYMBOL_FILTERS` coinciden con el `exchangeInfo` de hoy, y la ruta de datos públicos (la única que corre) está sana. Pero los tres P0 quedaron a medias: el fix de **precisión** solo cachea los 4 símbolos de `SYMBOL_MAP` (`:409`), de modo que `close_all_positions()` emite cantidades inválidas (**-1111**) para cualquier otro símbolo — que es exactamente el escenario que abre `fix_core-03`; el fix de **idempotencia** manda **4 POST** donde su propio docstring promete 1, y `clientOrderId` solo es único *"entre órdenes ABIERTAS"* según la doc, así que un MARKET **ya llenado no bloquea el duplicado**; y el fix de **"no dejar posiciones desnudas"** ignora el **modo hedge**, donde la doc prohíbe `reduceOnly` y exige `positionSide`. A eso se suman dos riesgos operativos: el `recover_fn` se dispara también en **429/418** y **añade peticiones durante un ban de IP** (que escala de 2 min a 3 días, rompiendo de paso la ruta de datos públicos), y `place_order` **no tiene deadline global** — con `recover_fn` anidado puede tardar minutos antes de fallar o de mandar el MARKET. Y la única barrera contra un arranque live accidental vive en el **bridge**, no en el cliente: **`py main.py` sin flags opera en vivo con las claves del `.env`**. 21 hallazgos: 0 P0, 6 P1, 9 P2, 6 P3 — y **nada es P0 hoy por el contexto** (cuenta cerrada, estrategias congeladas, paper), **no por el código**.

### 3.3 `persistence` — veredicto: **funciona en paper, y SOLO en paper. En live la contabilidad no existe.**

El corazón del problema es una divergencia de dos rutas que nunca se unificaron: paper escribe por `_process_paper_fill` (`main.py:660-681`, que **sí** pasa `trade_type` y `entry_price`) y live escribe por `ORDER_TRADE_UPDATE` (`main.py:359-366`, que **no** pasa ninguno de los dos), mientras las dos lecturas de la UI filtran `source="paper"` con una constante. Resultado reproducido: en live, **cero operaciones visibles, cero PnL, equity plana**, sin ningún error. Lo que está bien —y es de lo mejor del repo— es la contabilidad de **fees en paper**: round-trip con la tasa de entrada guardada, correcta. Pero el **funding no existe** fuera del backtester (`paper_simulator.py:99`), lo que sobrevalora sistemáticamente los perps y **rompe la paridad backtest↔paper** que la Tanda 1 ya había declarado inexistente; y `analytics/performance.py:187` iguala `net_pnl = total_pnl`, falso en live porque `rp` es bruto y `n` viene aparte. La tabla `sessions` no sirve para nada hoy: no se actualiza durante la sesión, nadie lee `end_time = 0`, y la DB real ya arrastra **3 sesiones huérfanas** y **1 fila fantasma con `session_id=''`** creada por un `end_session()` sin sesión activa (`adapter.py:114`). El **Sharpe publicado está inflado ×2,76** (medido) porque `_aggregate_daily_returns` omite los días sin trades — sesgo que el backtester **no** tiene, con lo que ni siquiera los dos números del propio repo son comparables entre sí. La ventana de trades de la UI muestra **los más antiguos** (`repository.py:295`). Y el canal por el que el dueño se entera de todo, Telegram, es el eslabón más frágil: descarta mensajes con `<`, `>` o `&` (HTML sin escapar, afecta a `notify_error` — justo las alertas), pierde los que reciben 429, y **filtra el token del bot a journald** vía `str(ContentTypeError)` (reproducido). Lo sano: SQLite pasa `integrity_check`, usa WAL, tiene índices razonables, no hay contención real backtest↔engine, y el `use_equity_after=False` del 2026-08-31 es conceptualmente correcto.

---

## 4. La pregunta de la confianza en la maquinaria

> **Cuando exista una estrategia con edge demostrado, ¿es esta maquinaria digna de confianza para operarla?**

# **NO.**

No es un "no" de matiz ni de higiene pendiente. Es un no con tres cláusulas, cada una suficiente por sí sola:

- **No cierra las posiciones cuando toca.** El camino de parada que **realmente se ejecuta en producción** borra el stop-loss de una posición apalancada **antes** de intentar cerrarla, y puede abortar el cierre a los 10 s. El fix de la Ronda 1 se aplicó a un camino (el CLI) que nadie usa.
- **Ejecuta órdenes que nadie pidió.** Un `git push` rutinario emite `MARKET reduceOnly` contra **cualquier** posición abierta en la cuenta, incluidas las manuales del dueño con su propio SL.
- **Los números que reporta en el único modo que importa son literalmente cero.** No "aproximados": cero trades, cero PnL, equity plana, y ninguna señal de que algo falla.

**Consecuencia práctica, en tres frases.** Primero: **el go-live está bloqueado por la maquinaria, no solo por el edge y no solo por el regulador** — aunque mañana apareciera una estrategia validada y un venue legal en España, conectar dinero real a este motor sería imprudente. Segundo: la buena noticia es que **esto es reparable en horas, no en meses** — los dos P0 son un `return` con guarda y sustituir seis líneas del bridge por `await engine.shutdown()`; a diferencia del "no hay edge" de la Tanda 1, aquí el trabajo es acotado y verificable con tests. Tercero, y es la lección que ordena todo el plan: **la Ronda 1 arregló el código y no arregló el camino que se ejecuta**; ningún fix de ejecución debe darse por cerrado hasta que exista un test que ejercite **la rama de fallo** y el **camino de producción**, no el mock que devuelve éxito.

---

## 5. Veredicto sobre la contabilidad: ¿son ciertos los números que ve el dueño?

**En paper: parcialmente ciertos, con un sesgo conocido y medible. En live: falsos — y no por error de cálculo, sino por ausencia total de datos.**

Dónde mienten exactamente, ordenado por cuánto engañan:

| Número que ve el dueño | ¿Cierto? | Dónde miente exactamente | Dirección del error |
|---|---|---|---|
| **PnL / equity / trades en LIVE** (UI y snapshot) | **NO — cero** | `bridge.py:863` y `:1427` filtran `source="paper"`; `main.py:359` no pasa `trade_type` (queda `""`) y el filtro `t.trade_type and != "ENTRY"` lo descarta | Muestra **0 trades y 0 PnL** con dinero real en juego, sin aviso |
| **Sharpe / Sortino** (UI, Telegram, informes) | **NO** | `analytics/performance.py:610`: `_aggregate_daily_returns` omite los días sin trades → anualiza por √(365/días **operados**) | **Inflado ×2,76** (medido). El backtester no tiene este sesgo → los dos números del repo no son comparables |
| **PnL neto en LIVE** | **NO** | `performance.py:187` `net_pnl = total_pnl`, pero el `rp` de Binance es **bruto** de comisión (`n` llega aparte) | **Optimista** en exactamente el importe de las comisiones |
| **PnL en paper sobre perps** | **NO** | `paper_simulator.py:99`: el funding no se aplica nunca (el backtester **sí** lo aplica) | **Optimista** en ~11 %/año de nocional |
| **Lista de operaciones recientes** | **NO** | `repository.py:295`: con `limit` se ordena ASC → devuelve los **N más antiguos** etiquetados como "most recent" | Muestra el pasado remoto como si fuera hoy |
| **`equity` del endpoint** | **Mezcla convenciones** | `bridge.py:938`: realizado **neto** de fees + no realizado **bruto**; y `:921` usa dos definiciones distintas de equity en el mismo endpoint (acumulado multi-sesión vs equity de sesión del risk manager) | Sesgo variable, imposible de auditar |
| **Historial de sesiones** | **NO** | `adapter.py:102/114`: sin heartbeat, `end_time=0` sin lector, y `end_session()` sin sesión activa crea una fila con `session_id=''` | DB real: **3 sesiones huérfanas + 1 fantasma** |
| **Alertas de error por Telegram** | **NO llegan** | `telegram.py:402`: `parse_mode=HTML` sin escapar `<`, `>`, `&` → la API rechaza y el mensaje se descarta; un 429 también lo descarta (`:533`) | **Silencio** justo cuando algo falla |
| **Fees en paper (round-trip)** | **SÍ** | Tasa de entrada guardada y aplicada correctamente | — |
| **Integridad de la base de datos** | **SÍ** | `integrity_check ok`, WAL, índices razonables, sin contención real | — |

**La frase que resume el veredicto contable:** hoy el dueño ve **números de paper, inflados en Sharpe y optimistas en fees/funding**; el día que active live verá **ceros**. En ninguno de los dos casos ve la verdad, y en ninguno de los dos casos el sistema le avisa. Un motor de trading que se equivoca al medirse **no puede validar ni invalidar ninguna estrategia futura** — con lo cual la contabilidad no es un problema de "reporting": es, junto con la paridad de la Tanda 1, el segundo instrumento de medida roto del proyecto.

---

## 6. Plan de acción priorizado y secuenciado

La lógica del orden: **primero que no se pierda dinero por un fallo mecánico, luego que los números sean ciertos, y solo después pulir el cliente de exchange.** Optimizar el rate limiter antes de arreglar el shutdown es pulir el motor de un coche sin frenos.

### P0 — HOY (total ≈ 3-4 h)

| # | Acción | Hallazgo | Esfuerzo | Criterio de aceptación |
|---|---|---|---|---|
| 1 | **Guarda en `_flatten_all`.** Antes de `cancel_all()`: `if remaining or not isinstance(result, dict) or result.get("errors"): logger.critical(...); await notifier.notify_error(...); return`. Tratar el `except` como `remaining` desconocido → mismo `return` | `fix_core-01` | **45 min** | Tests nuevos para las **3** ramas de fallo (excepción, `remaining` no vacío, `errors` no vacío): en ninguna se llama a `cancel_all()`, en las tres se notifica |
| 2 | **`bridge.stop_engine` usa el shutdown del engine.** Sustituir el bloque `cancel_all()` por `await engine.shutdown()` **antes** de cancelar la task; subir `wait_for` a ≥60 s (configurable). Borrar el docstring obsoleto que apunta a `main.py:1080-1104` | `fix_core-02` | **1 h** | PoC de la tanda como test: la traza de `/api/bot/stop` en live nunca contiene `CANCEL_ALL` antes de `close_all_positions`; con flatten de 12 s el cierre **completa** |
| 3 | **No absorber el error del cliente.** `order_engine.py:561-563` debe propagar o marcar `remaining` como desconocido cuando el cliente lanza, en vez de devolver `remaining: []` con `errors` lleno | `fix_core-01` (causa raíz) | **30 min** | Test: excepción del cliente → `remaining` no vacío/`None`, alerta emitida |
| 4 | **Acotar `close_all_positions` a los símbolos gestionados.** Parámetro explícito `symbols=` alimentado por `{c.symbol for c in settings.symbols}`; ídem en el fallback `order_engine.py:580-597` | `fix_core-03` | **1 h** | Test con una posición ajena en el mock de `positionRisk`: **0** órdenes emitidas sobre ella |
| 5 | **Kill-switch en profundidad.** `BinanceClient.place_order/batch_orders/close_all_positions` lanzan `RuntimeError` si `BOTSTRIKE_ALLOW_LIVE != '1'`; el modo por defecto del CLI pasa a **paper** | `fix_exchange-05` (sin verificar, pero es una red de seguridad barata que protege 1-4) | **30 min** | `py main.py` sin flags **no** firma ninguna petición |

> **Después del P0, apagar el bot deja de ser una operación peligrosa y un deploy deja de tocar posiciones ajenas.** Eso es todo lo que debe conseguirse hoy.

### P1 — ESTA SEMANA (total ≈ 3-4 días)

| # | Acción | Hallazgos | Esfuerzo |
|---|---|---|---|
| 6 | **Un único constructor de `TradeRecord`** compartido por la ruta paper y la live (`trade_type`, `entry_price`, `fee`), y `source` real del adapter en las dos lecturas del bridge (`:863`, `:1427`). Test de invariante paper↔live: mismo trade sintético → mismo registro | `persistence-01` | 0,5 día |
| 7 | **PnL neto de verdad.** Normalizar en el punto de entrada live (`order_engine.py:503` → `pnl = realized_pnl - commission`, con conversión si la comisión no es USDT), dejando `rp`/`n` separados para analítica; documentar la convención en **un** sitio | `persistence-07`, `fix_core-05` | 0,5 día |
| 8 | **Calendario completo en `_aggregate_daily_returns`** (rellenar días sin trades, ordenar por día) y actualizar el docstring que aún dice √252; propagar el 365 a `dashboard/state.py`. **Recalcular y republicar todo Sharpe/Sortino histórico** | `persistence-05`, `persistence-15` | 0,5 día |
| 9 | **Sesiones fiables:** heartbeat 1×/min desde `_metrics_loop` (UPDATE de `total_trades/total_pnl/final_equity/max_drawdown`), guard contra `end_session()` sin sesión activa, y al arrancar detectar/cerrar/notificar `end_time = 0`. Limpiar la fila fantasma existente | `persistence-03`, `persistence-04` | 0,5 día |
| 10 | **Telegram fiable:** `html.escape()` en toda interpolación externa, reintento **una** vez sin `parse_mode` ante 400 *"can't parse entities"*, reencolar el mensaje ante 429, y **borrar el token de los logs** (`str(ContentTypeError)` incluye la URL) | `persistence-08`, `persistence-09`, `persistence-17` | 0,5 día |
| 11 | **`/api/trades` devuelve los recientes:** ORDER BY DESC + `LIMIT` + `reversed()` en Python cuando hay `limit` | `persistence-02` | 1 h |
| 12 | **Cliente de exchange, los 4 P1 de seguridad operativa:** cachear **todos** los símbolos de `exchangeInfo`; separar 429/418 ("no ejecutado", sin `recover_fn`, back-off por IP con `Retry-After` y `X-MBX-USED-WEIGHT-1m`) de timeout/5xx ("estado desconocido"); deadline absoluto por operación (8 s entradas, 20 s protectivas) devolviendo estado `UNKNOWN`; reconciliar entradas por **delta de `positionAmt`**, no por existencia de orden | `fix_exchange-01/02/03/04` | 1,5 días |
| 13 | **Modo hedge:** al arrancar en live, `GET /fapi/v1/positionSide/dual`; si `dualSidePosition == true`, forzar one-way o **abortar con CRITICAL** | `fix_exchange-06` | 2 h |
| 14 | **Reintento de salida correcto:** que `_check_exit` no mute `_states`; marcar "exit pendiente" y llamar a `notify_external_exit` solo con el fill confirmado; calcular la cantidad desde `position.size`, no desde `notional/price` | `fix_core-06` | 0,5 día |

### P2 — ESTE MES (total ≈ 2-3 semanas)

| # | Acción | Por qué ahora y no antes | Esfuerzo |
|---|---|---|---|
| 15 | **Funding en paper y en la DB:** tick cada 8 h con `market_data.get_funding_rate()`, aplicado al equity y persistido (`trade_type='FUNDING'` o columna propia); poblar el funding real vía `GET /fapi/v1/premiumIndex` en vez del `0.0` hardcodeado | Sin esto, ningún soak de paper sobre perps es evidencia admisible — pero es inútil antes de que la contabilidad live exista | 2 días |
| 16 | **Puerta de rendimiento con estadística real:** sustituir la media en R por significación (`t = mean/(std/√n)` con n≥30 o Sharpe rodante por trade) y expresarla en **bps por trade**, comparables con la fricción | Hoy es inalcanzable (−0,73 R ≈ 22 % DD vs halt al 10 %); requiere primero que el PnL sea neto (#7) | 2 días |
| 17 | **`is_exit_signal` de verdad única** (3 copias; la del risk manager bloquea `trailing_stop_hit`/`mm_unwind` durante un halt) y `shutdown()` idempotente completo (BD, Telegram, WS se ejecutan dos veces por Ctrl-C) | Higiene estructural que evita la próxima divergencia | 1 día |
| 18 | **Reconciliación tras reinicio duro:** al arrancar, leer `positionRisk` y adoptar/cerrar las posiciones vivas. Hoy, tras `os._exit(3)` del watchdog o un SIGKILL, una posición live queda **sin gestión software para siempre** | Requiere el P0 hecho para no reintroducir el flatten ciego | 2 días |
| 19 | **Corregir `allocation_* = 0.00` como palanca real** (hoy no participa en el sizing pero es lo que `/api/strategies` reporta al usuario) y la clasificación `is_exit = pnl != 0 or fee > 0` que cuenta las **entradas** live como cierres (doble conteo, win-rate hundido) | Ambos falsean lo que el dueño ve; ninguno mueve dinero hoy | 1 día |
| 20 | **Resto de P2 de exchange y persistencia:** `minNotional` BTC real (50, no 100), lado del precio límite en STOP_LIMIT, `MARKET_LOT_SIZE`/`PERCENT_PRICE`, `_RateLimiter` por **peso** (2400/min) y no por peticiones, `batch_orders` que no se trague el error, `place_bracket_order`, `ws_connected` fantasma, depth duplicado, `DATA_ROOT`/ruta de DB absoluta, FK activadas, buffer de métricas con flush periódico | Higiene; ninguno es bloqueante para el go-live una vez hechos P0 y P1 | 1 semana |

### La respuesta corta a "¿en qué estado está la maquinaria?"

1. **Se apaga mal** — el camino de producción borra los protectivos antes de cerrar, y puede abortar el cierre a los 10 s.
2. **Actúa fuera de su perímetro** — un deploy aplana toda la cuenta, no solo lo que el bot gestiona.
3. **No sabe contar en live** — cero trades, cero PnL, cero aviso.
4. **Y lo que cuenta en paper está sesgado al alza** — Sharpe ×2,76, sin funding, con fees fuera del neto en live.

Nada de esto es irreparable, y esa es la diferencia importante con la Tanda 1: **allí faltaba una hipótesis verdadera; aquí solo falta ingeniería, y está acotada en ~4 días de trabajo.** Pero hasta que esos 4 días se hagan, la respuesta a "¿podemos confiar en la máquina?" es no, y ninguna estrategia con edge debería conectarse a ella.

---

## 7. Anexo

### 7.1 Refutados

**Ninguno.** Los 4 hallazgos presentados a verificación sobrevivieron a dos intentos independientes de refutación (confianza 0,90–0,95), tres de ellos con reproducción ejecutando el código real del repo. Se registran las salvedades honestas de los verificadores, que **matizan pero no refutan**:

- `fix_core-01`: el caso más grave encontrado **no** es el descrito originalmente sino el tercero (`errors` no vacío con `remaining: []`), que **agrava** el hallazgo: el fallo es silencioso y la rama `except` casi nunca se alcanza en producción. El fix debe cubrir `errors`, no solo `remaining`.
- `fix_core-02`: el despliegue actual es **paper**, donde el bloque `cancel_all()` se salta (`if not dry_run and not paper`). El defecto materializa **solo en live** — por lo que es bloqueante de go-live, no un incendio de hoy.
- `fix_core-03`: mitigado hoy por (a) el `return` temprano en paper/dry_run (`main.py:845-856`) y (b) Binance cerrado para residentes ES. Ningún documento ni script de deploy exige una **subcuenta dedicada** (grep sin resultados), así que la mitigación es circunstancial, no de diseño.
- `persistence-01`: la tabla `trades` de la DB local tiene **0 filas** (5 sesiones paper), así que hoy no mostraría un PnL paper inflado sino **ceros**; lo verificado y sólido es que ninguna operación live aparecerá jamás. Discrepancia P0/P1 documentada en §2.

### 7.2 P0/P1 identificados pero **sin verificar** por tope de tanda (16)

Requieren una segunda pasada de verificación antes de tratarse como hechos. Ordenados por área.

**`fix_core` (3)**
- `fix_core-04` — `portfolio/portfolio_manager.py:250`: la puerta de rendimiento (F03) es **inalcanzable** — exige −0,73 R de media (≈22 % DD) pero el halt de drawdown salta 5× antes (10 %). Fix: significación estadística (`t < −2`, n≥30) y gate en **bps por trade**, no en R sobre un presupuesto de riesgo que el sizing real nunca usa (01-F13: riesgo real 0,04-0,06 %, no 1,5 %).
- `fix_core-05` — `main.py:346`: en live la ventana de rendimiento se alimenta del PnL **BRUTO** de Binance (`rp`), en paper/backtest del **neto** → la puerta es ciega a la sangría por comisiones. Fix: `pnl_net = realized_pnl − commission` + test de invariante paper↔live.
- `fix_core-06` — `strategies/mean_reversion.py:393`: 01-F02 solo se arregló a medias — `_states.pop()` **antes** de que la salida se llene → un exit rechazado nunca se reintenta y la posición queda huérfana.

**`fix_exchange` (6)**
- `fix_exchange-01` — `binance_client.py:409`: `load_exchange_info()` cachea **solo** los 4 símbolos de `SYMBOL_MAP` → `close_all_positions()` no puede cerrar nada fuera de ellos (**-1111**). Combina de forma directa con `fix_core-03`.
- `fix_exchange-02` — `:275`: el `recover_fn` del fix P0-02 se dispara también en **429/418** y amplifica el rate-limit/ban de IP (rompe la ruta de datos públicos, la única que corre hoy).
- `fix_exchange-03` — `:268`: `place_order` sin **deadline global**; con `recover_fn` anidado puede tardar minutos antes de fallar o de mandar el MARKET.
- `fix_exchange-04` — `:279`: el P0-02 no está cerrado — reenvío posible (**4 POST**, no 1) y `newClientOrderId` solo es único *"entre órdenes ABIERTAS"*. Reconciliar por **estado de posición**, no por existencia de orden.
- `fix_exchange-05` — `main.py:1621`: **no hay kill-switch** en el cliente ni en el CLI: `py main.py` sin flags opera **en vivo** con las claves del `.env`. (Promovido al P0 del plan por ser una red de seguridad de 30 min que protege todos los demás fixes.)
- `fix_exchange-06` — `binance_client.py:707`: el **modo hedge** sigue sin contemplarse y rompe explícitamente el fix P0-03 (02-06 abierto): la doc prohíbe `reduceOnly` y exige `positionSide`.

**`persistence` (7)**
- `persistence-02` — `repository.py:295`: `/api/trades?limit=N` devuelve los N trades **MÁS ANTIGUOS** etiquetados como *«most recent»*.
- `persistence-03` — `adapter.py:114`: `end_session()` sin sesión activa crea/pisa una fila fantasma con `session_id=''` (**ya existe en la DB de producción**).
- `persistence-04` — `adapter.py:102`: sesiones huérfanas — la fila `sessions` no se actualiza durante la sesión y nadie detecta `end_time = 0` (**3 huérfanas en la DB real**).
- `persistence-05` — `analytics/performance.py:610`: Sharpe y Sortino inflados por √(365/días_operados) — **medido ×2,76**; el backtester no tiene el sesgo.
- `persistence-06` — `execution/paper_simulator.py:99`: el **funding nunca se contabiliza** en paper ni en la trade DB (sí en el backtester) → paper sobrevalora los perps (~11 %/año de nocional) y rompe la paridad backtest↔paper.
- `persistence-07` — `analytics/performance.py:187`: `net_pnl = total_pnl` es **falso en live** (el `rp` de Binance es bruto de comisión, `n` llega aparte).
- `persistence-08` — `notifications/telegram.py:402`: `parse_mode=HTML` sin escapar `<`, `>`, `&` → la API rechaza el mensaje y **se pierde** (afecta a `notify_error`, es decir, a las alertas).

### 7.3 P2/P3 (38) — higiene; ninguno bloquea el go-live una vez hechos P0 y P1

**`fix_core` (P2):** `is_exit_signal` documentado como *single source of truth* con **3 copias divergentes** — la del risk manager bloquea `trailing_stop_hit` y `mm_unwind` durante un halt (`risk_manager.py:130`) · `allocation_mean_reversion = 0.00` **no congela nada** (no participa en el sizing) pero es lo que `/api/strategies` reporta (`settings.py:104`) · `shutdown()` solo es idempotente para el flatten: BD, Telegram y WS se ejecutan **dos veces** en cada Ctrl-C (`main.py:891`) · tras un reinicio duro (`os._exit(3)`/SIGKILL) una posición live queda **sin gestión software para siempre** (`main.py:528`) · el fallback de `close_all_positions` reporta `remaining` obsoleto y puede **duplicar cierres** (`order_engine.py:587`).
**`fix_core` (P3):** `update_strategy_pnl` sin el flag `is_exit` → una salida a breakeven exacto cuenta como entrada (`main.py:631`) · dos puertas de régimen con defaults distintos para la misma pregunta, 0,33 vs 0,0 (`portfolio_manager.py:189`) · el flatten de paper usa **el doble** de slippage que una salida por señal (`paper_simulator.py:356`).

**`fix_exchange` (P2):** `DEFAULT_SYMBOL_FILTERS['BTCUSDT']['minNotional'] = 100` es **falso** (el real es 50) y el comentario lo llama "valor real observado" (`:61`) · `_price_rounding_mode` coloca el precio límite al **lado equivocado** del trigger en STOP_LIMIT/TAKE_PROFIT_LIMIT (`:442`) · cerrar antes de cancelar expone el flatten a **-2022** y `cancel_all()` corre igual (`:729`) · `parse_symbol_filters` ignora `MARKET_LOT_SIZE`, `PERCENT_PRICE`, `maxQty`, `minPrice/maxPrice` (`:115`) · paper + `--testnet` mezcla **REST de testnet con WS de mainnet** (`main.py:63`) · `BinanceWebSocket.stop()` no resetea `_connected` → `/api/health` dice `ws_connected: true` con el WS parado (`binance_ws.py:297`) · `batch_orders` se traga el error y devuelve `{'orders': []}`: hasta **5 órdenes vivas invisibles** (`:952`) · `place_bracket_order` quedó **fuera** del fix (acepta status NEW, `cid` fijo, no espera el fill) (`:791`) · `_RateLimiter` cuenta **peticiones** (1200/min) cuando el límite real es de **peso** (2400/min) y `positionRisk`/`account` pesan 10 (`:148`).
**`fix_exchange` (P3):** endpoints `/fapi/v2/{account,balance,positionRisk}` **deprecados** desde 2024-07-24 (`:615`) · `get_order` devuelve `None` también cuando la respuesta no es un dict — y eso significa "reenvía" (`:665`) · `settings.binance_testnet` es bandera muerta con un comentario que afirma una protección inexistente (`settings.py:323`) · cada mensaje de depth se emite **dos veces** y `main.py` registra el mismo handler en ambos eventos → todo se procesa por duplicado (`binance_ws.py:155`) · el stream `@kline_1m` se suscribe y se parsea **para nadie** (`:76`) · el comentario del `for/else` de `close_all_positions` describe una condición que no es la del lenguaje (`:777`).

**`persistence` (P2):** un **429 de Telegram descarta el mensaje** (se saca de la cola antes de enviarlo y no vuelve a entrar) (`telegram.py:533`) · `is_exit = pnl != 0 or fee > 0` clasifica las **entradas live como cierres** → doble conteo y win-rate hundido (`logger.py:164`) · `_cumulative_performance` materializa **toda** la tabla paper en el event loop **cada 5 s** (`bridge.py:863`) · el `equity` mostrado mezcla realizado **neto** de fees con no realizado **bruto** (`bridge.py:938`) · la FK `trades.session_id → sessions.session_id` es decorativa: `PRAGMA foreign_keys` nunca se activa (`repository.py:84`) · **dos definiciones de `equity` en el mismo endpoint** (acumulado multi-sesión en la UI, equity de sesión en el risk manager) (`bridge.py:921`) · el cambio de 252 a 365 no se propagó: `dashboard/state.py` sigue en √252, y el PDF de documentación también (`state.py:314`) · la ruta de la DB es **relativa al CWD** → ejecutar un script desde otro directorio crea una base vacía paralela (ya ha pasado en este repo) (`repository.py:111`) · el **token de Telegram acaba en journald** (va en la URL y `str(ContentTypeError)` la incluye) (`telegram.py:686`) · si falla la escritura de `metrics.jsonl` el buffer **nunca se vacía**: crece sin límite y reescribe duplicados (`logger.py:128`).
**`persistence` (P3):** rotación duplicada de `metrics.jsonl` — el comentario de logrotate es falso, el `.old` interno queda fuera del patrón y `log_file` no se escribe nunca (`deploy/logrotate-botstrike:2`) · `stop()` drena la cola de Telegram **saltándose el rate limiter** (`telegram.py:127`) · detalles de fórmulas de `PerformanceAnalyzer`: drawdown desalineado, VaR sin interpolación, `profit_factor` centinela 9999.99 (`performance.py:653`) · SQL por f-string en el `LIMIT` y la DB **nunca se compacta** (`vacuum()` sin llamantes, `auto_vacuum = 0`) (`repository.py:297`) · las métricas se escriben cada 10 registros sin flush periódico: **cada `os._exit` pierde hasta 9** (`logger.py:31`).

---

*Informe de la Tanda 2, Ronda 2. Áreas restantes (bridge/deploy/seguridad, desktop, tests/calidad, microestructura, Hyperliquid) pendientes de tandas posteriores. Ningún hallazgo de este informe fue aceptado sin un segundo verificador independiente; ningún archivo del proyecto fue modificado durante la auditoría.*
