# Auditoría R2 — fix_core (revisión adversarial de los fixes ronda 1 en core)

**Fecha:** 2026-08-31 · **Auditor:** subagente fix_core
**Base:** commit `b3dbf75` (fixes ronda 1) verificado sobre HEAD `7b9da43` (incluye Fase 0 quant).
**Alcance:** `main.py`, `execution/order_engine.py`, `execution/paper_simulator.py`,
`portfolio/portfolio_manager.py`, `config/settings.py`.
**Método:** lectura del código real + snippets ejecutados con `py -3.12` (sin red) +
lectura de `tests/test_p0_round2.py` para detectar mocks que enmascaran.

Informe INCREMENTAL — cada hallazgo se añade en cuanto se confirma.

## Hallazgos

### [P0] fix_core-01 — `_flatten_all()` llama a `cancel_all()` INCONDICIONALMENTE, también cuando el cierre falló → vuelve a dejar posiciones desnudas (F01/P0-03 solo arreglado en el camino feliz)
**Archivo:** `main.py:857-871` (+ `execution/order_engine.py:561-563,578-608`, `exchange/binance_client.py:740-787`)
**Evidencia:**
```python
# main.py:857-871
result: Dict = {}
try:
    result = await self.execution_engine.close_all_positions()
except Exception as e:
    logger.error("flatten_close_positions_failed", reason=reason, error=str(e))
remaining = result.get("remaining") if isinstance(result, dict) else None
if remaining:
    logger.critical("flatten_incomplete_positions_remain", ...)
    asyncio.ensure_future(self.notifier.notify_error(...))
for item in (result.get("closed") or []) if isinstance(result, dict) else []:
    self._positions.pop(item.get("symbol"), None)
    self._notify_strategies_flat(item.get("symbol"), None)
await self.execution_engine.cancel_all()          # <-- SIEMPRE, pase lo que pase
```
```python
# execution/order_engine.py:561-563 — el error se traga y devuelve remaining=[]
except Exception as e:
    logger.error("close_all_positions_failed", error=str(e))
    return {"closed": [], "remaining": [], "errors": [str(e)]}
```
Snippet ejecutado (`_flatten_all` REAL de `main.BotStrike`, engine falso, `py -3.12`):
```
remaining non-empty (close failed on exchange)       -> ['close_all_positions', 'cancel_all']
close_all_positions raises (positionRisk 503)        -> ['close_all_positions', 'cancel_all']
engine swallowed error -> remaining=[] + errors      -> ['close_all_positions', 'cancel_all']
clean close                                          -> ['close_all_positions', 'cancel_all']
```
**Por qué:** El objetivo declarado de F01/P0-03 es «nunca cancelar las SL/TP del exchange con
la posición abierta». El fix solo cumple eso cuando `close_all_positions()` tiene éxito. En los
escenarios reales de fallo (positionRisk 5xx/timeout, `-1001`/`-1021` en la MARKET, IP baneada
por rate-limit, cliente sin `close_all_positions` ni `get_positions`) el código continúa y
`cancel_all()` borra las protectivas de las posiciones que NO pudo cerrar — exactamente el
escenario a 5x del hallazgo original, y ahora además con `_dd_flattened=True` /
`_shutdown_flatten_done=True`, así que **no hay reintento posterior**. Peor: `remaining=[]` con
`errors` no vacío es indistinguible de «todo cerrado», así que ni siquiera se emite el CRITICAL.
En `order_engine.close_all_positions` el fallback devuelve `remaining=[]` si los 3
`get_positions()` fallan (nunca se llega a `remaining = open_pos`), y en
`binance_client.close_all_positions` la relectura final del `for/else` traga la excepción con
`pass` dejando `remaining=[]`.
**Fix:** (1) `ok = isinstance(result, dict) and not result.get("remaining") and not result.get("errors")`;
solo si `ok` → `cancel_all()`. Si no → CRITICAL + Telegram y **no cancelar** (las SL/TP del
exchange son la única protección que queda). (2) Devolver `remaining=None` / `"unknown": True`
cuando no se pudo leer el estado, en vez de `[]`. (3) En el halt por DD dejar `_dd_flattened=False`
si el flatten no fue completo, para reintentar con backoff. (4) Test: `close_all_positions` que
lanza o devuelve `remaining` no vacío ⇒ `cancel_all` NO se llama.
**Verificado como:** lectura de `main.py:839-871`, `order_engine.py:544-621`,
`binance_client.py:728-787` + snippet ejecutado con `py -3.12` (salida arriba).

### [P0] fix_core-02 — REGRESIÓN NUEVA: `shutdown()` se ejecuta DOS VECES en paralelo; la segunda cierra la sesión HTTP mientras el flatten de la primera está en vuelo
**Archivo:** `main.py:886-899` (+ `main.py:244-282` `_supervise_tasks`, `main.py:219-224`, `main.py:1646-1660`)
**Evidencia:**
```python
# main.py:891-899  — el guard se pone ANTES del await largo
if not self._shutdown_flatten_done:
    self._shutdown_flatten_done = True
    if self.settings.trading.close_positions_on_shutdown:
        await self._flatten_all(reason="shutdown")     # segundos: 3 intentos + N MARKET
    ...
await self.websocket.stop()
await self.client.close()                              # <- la 2ª invocación llega aquí YA
```
```python
# main.py:1646-1652 (SIGINT)          |  # main.py:219-224 (start)
def handle_signal(sig, frame):        |  try:
    loop.call_soon_threadsafe(        |      await self._supervise_tasks(tasks)
      lambda: asyncio.ensure_future(  |  finally:
          bot.shutdown()))            |      await self.shutdown()      # 2ª llamada
# main.py:244  while self._running:   -> shutdown() pone _running=False y _supervise_tasks
#                                       RETORNA en cuanto un loop despierta (risk loop: 2 s)
```
Snippet ejecutado (`BotStrike.shutdown` REAL, `close_all_positions` de 1 s, `py -3.12`):
```
t= 0.00s  close_all_positions START (MARKET reduceOnly in flight)
t= 0.06s  websocket.stop
t= 0.06s  client.close  <-- aiohttp session destroyed
t= 0.06s  trade_db.end_session
t= 1.02s  close_all_positions END
t= 1.02s  cancel_all
t= 1.02s  client.close  (otra vez)
```
**Por qué:** El guard `_shutdown_flatten_done` protege de un flatten DUPLICADO, pero no de un
shutdown CONCURRENTE: la segunda llamada salta el flatten y corre directamente a
`self.client.close()`, destruyendo la `aiohttp.ClientSession` mientras las MARKET reduceOnly del
flatten siguen en vuelo. `binance_client.close_all_positions` duerme por sí sola 0.3+0.6+0.9 = 1.8 s
entre intentos, más 1-4 órdenes y hasta 3 lecturas de `positionRisk`; el `risk_monitor_loop`
despierta cada `risk_check_interval_sec = 2.0`, así que `_supervise_tasks` retorna (su `while
self._running` ya es falso) y dispara el segundo `shutdown()` en pleno flatten. Resultado en un
`systemctl restart` / Ctrl+C con posición abierta: el cierre se aborta a mitad, `cancel_all()` se
ejecuta después sobre una sesión muerta, `trade_db.end_session()` y `notify_shutdown` se ejecutan
dos veces, y `_shutdown_flatten_done=True` impide cualquier reintento. Es decir, el fix P0-03
falla justo en el momento para el que se escribió (deploy/reinicio). Antes de b3dbf75 el shutdown
solo hacía `cancel_all()` (una llamada), por lo que la ventana de carrera era ~0.
**Fix:** Serializar el shutdown con un `asyncio.Lock` + flag `_shutdown_done` (no solo el del
flatten): `async with self._shutdown_lock: if self._shutdown_done: return; ...`. Alternativa
mínima: en `main()` NO llamar a `bot.shutdown()` desde el handler de señal — poner solo
`bot._running = False` (+ `loop.call_soon_threadsafe`) y dejar que el `finally` de `start()` haga
el único shutdown. Test: dos `shutdown()` concurrentes ⇒ `client.close` se llama una sola vez y
siempre DESPUÉS de que `close_all_positions` haya retornado.
**Verificado como:** lectura de `main.py:219-224,244-282,886-899,1642-1662`,
`config/settings.py:116-118` (`risk_check_interval_sec=2.0`) + snippet ejecutado con `py -3.12`
(salida arriba, `shutdown()` real).

### [P1] fix_core-03 — El "R-multiple" del performance factor se normaliza con `equity × risk_per_trade_pct` ($15) cuando el riesgo REAL por trade es $0.34–$2.21 → el gate de bloqueo (0.6) es físicamente inalcanzable: F03 pasó de "mata la estrategia con −$0.03" a "no reacciona nunca"
**Archivo:** `portfolio/portfolio_manager.py:219-224` y `:243-245` (+ `strategies/base.py:90-91`, `main.py:543-547`)
**Evidencia:**
```python
# portfolio_manager.py:219-224
def _risk_budget_per_trade(self) -> float:
    equity = self.risk_manager.current_equity
    if equity <= 0:
        equity = self.config.initial_capital
    return max(equity * self.config.risk_per_trade_pct, 1e-6)   # 1000 * 0.015 = $15
# :243-245
avg_r = (sum(closed) / n) / self._risk_budget_per_trade()
factor = 1.0 + 0.5 * math.tanh(1.5 * avg_r)
```
```python
# strategies/base.py:90-91 — lo que se arriesga DE VERDAD por trade
risk_pct = kelly_risk_pct if kelly_risk_pct is not None else self.trading_config.risk_per_trade_pct
risk_amount = capital * risk_pct     # capital = allocated (equity * peso * dd * 1/n_symbols)
```
Snippet ejecutado (`Settings()`, `PortfolioManager.get_allocation` y
`MeanReversionStrategy._calc_position_size` REALES, SL 1.2 %, `py -3.12`):
```
normalizer _risk_budget_per_trade = 15.0
RANGING      alloc=$162.50 notional=$184.51 real_risk=$2.21 avg_r=-0.1476 factor=0.8911 blocks?=False
TRENDING_UP  alloc=$ 75.00 notional=$ 85.16 real_risk=$1.02 avg_r=-0.0681 factor=0.9491 blocks?=False
BREAKOUT     alloc=$ 25.00 notional=$ 28.39 real_risk=$0.34 avg_r=-0.0227 factor=0.9830 blocks?=False
UNKNOWN      alloc=$125.00 notional=$141.93 real_risk=$1.70 avg_r=-0.1135 factor=0.9157 blocks?=False
avg_r needed to block: -0.7324 => avg loss per trade needed: $10.99
after 20 full SLs: factor = 0.8911  should_strategy_trade = True
after 50 full SLs: factor = 0.8911  should_strategy_trade = True
```
**Por qué:** El peor caso físicamente posible (perder el 100 % del riesgo en CADA trade, 50 stops
seguidos) deja el factor en **0.891** y la estrategia **habilitada**. Para bloquearse necesitaría
perder $10.99 de media por trade cuando su pérdida máxima es ~$2.21 (5x más de lo posible). El
gate `PERF_BLOCK_THRESHOLD=0.6` y toda la maquinaria de probation/cooldown son código muerto en
producción; el único efecto real del factor es un recorte del 11 % de asignación. Un performance
factor existe para cortar una estrategia rota (datos mal calentados, régimen mal clasificado)
antes de que la fricción se coma la cuenta — hoy no corta nada. Afecta igual a paper y a live.
Los tests pasan porque usan pérdidas de **−$30 y −$500 por trade** (`tests/test_p0_round2.py:185,198`),
imposibles con el sizing real: son mocks numéricos que enmascaran el bug.
**Fix:** normalizar con el riesgo REALMENTE asumido: guardar el `risk_usd` de la entrada
(`size × |entry − stop_loss|`, ya disponible en `PaperPosition`/`signal.stop_loss`) en el Trade de
salida y usar `avg_r = mean(pnl_i / risk_i)`. Alternativa mínima de una línea: normalizar con
`self._current_weights[strategy] * equity * risk_per_trade_pct / n_symbols` (la asignación real).
Y reescribir los tests con pérdidas realistas: 20 trades a −1R deben bloquear, 20 a −0.3R solo
reducir.
**Verificado como:** lectura de `portfolio_manager.py:219-256`, `strategies/base.py:81-121`,
`main.py:534-556` + snippet ejecutado con `py -3.12` (salida arriba, código real).

### [P1] fix_core-04 — El fix F02 (`is_exit_signal`) se aplicó a live/paper pero NO al backtester: `exit_fibonacci` se DESCARTA silenciosamente ⇒ todo backtest de Fibonacci es inválido (y es la evidencia con la que se decide congelar/descongelar la estrategia)
**Archivo:** `backtesting/backtester.py:1093-1117` (y `:495` en la ruta rápida)
**Evidencia:**
```python
# backtesting/backtester.py:1093-1117 — tupla hardcodeada, sin exit_fibonacci
is_exit = signal.metadata.get("action") in (
    "exit_mean_reversion", "trailing_stop_hit", "mm_unwind"
)
if is_exit:
    ...
    continue
# Señal de entrada
if pos_key not in positions:      # <- la posición Fib SÍ está -> la señal se descarta
```
```python
# strategies/fibonacci_retracement.py:530
metadata={"action": "exit_fibonacci", "exit_reason": exit_reason},
```
Snippet ejecutado (`py -3.12`, `Signal` real con `action="exit_fibonacci"`):
```
live engine  is_exit_signal : True
paper sim    criterion      : True
risk_manager criterion      : True
BACKTESTER   criterion      : False
```
**Por qué:** En el backtester la señal de salida de Fibonacci no cierra la posición y tampoco abre
nada (`pos_key` ya existe) — se pierde. La posición queda abierta hasta el SL/TP duro o hasta el
`CLOSE_EOD`, mientras la estrategia ya hizo `self._states.pop(symbol)` y por tanto no volverá a
emitir salida nunca. Consecuencia directa: **el trailing stop, el software-SL y el `stale_position`
de Fibonacci no existen en backtest**; los PF publicados para Fib (BTC PF=1.11, ADA PF=0.14,
`portfolio_manager.py:69-72`) miden un sistema que no es el que corre en producción, y son el
criterio con el que se congeló/se descongelará la estrategia. Además `risk_manager.validate_signal`
(`risk/risk_manager.py:117-120`) tampoco reconoce `trailing_stop_hit`/`mm_unwind`, así que la
afirmación de `fixes_round1.md` («mismo criterio que … `risk_manager.validate_signal`») es falsa:
son 4 criterios distintos en 4 ficheros.
**Fix:** sustituir las 3 tuplas del backtester y el criterio del `risk_manager` por
`OrderExecutionEngine.is_exit_signal(sig)` (o mover el helper a `core/types.py` para no importar
`execution` desde `risk`), y añadir un test de paridad que recorra todas las acciones que emiten
las estrategias vivas y compruebe que los 4 consumidores coinciden. Re-correr los backtests de Fib
antes de usar sus PF para cualquier decisión.
**Verificado como:** lectura de `backtesting/backtester.py:490-500,1078-1120`,
`strategies/fibonacci_retracement.py:509-531`, `risk/risk_manager.py:116-121` + snippet ejecutado
con `py -3.12` (salida arriba).

### [P1] fix_core-05 — El nuevo flatten por drawdown está conectado a una equity que EXCLUYE el PnL no realizado (`01-F07` / `02-10` siguen abiertos): en live no puede dispararse mientras la posición sangra, que es justo el caso para el que se escribió
**Archivo:** `main.py:373-381` + `main.py:757-773` (+ `risk/risk_manager.py:491-494`)
**Evidencia:**
```python
# main.py:376-381 — única fuente de equity en live
balances = data.get("a", {}).get("B", [])
for b in balances:
    if b.get("a") in ("USDT", "USD"):
        equity = float(b.get("wb", 0))       # wallet balance: SIN unrealized PnL
        await self.risk_manager.update_equity_safe(equity)
```
```python
# risk/risk_manager.py:491-494
return (self._equity_peak - self._current_equity) / self._equity_peak
# main.py:758-773
if self.risk_manager.current_drawdown_pct >= self.settings.trading.max_drawdown_pct:
    ...
    if not self._dd_flattened:
        self._dd_flattened = True
        await self._flatten_all(reason="max_drawdown")
```
**Por qué:** El flatten por DD es la ÚNICA protección de cartera añadida en la ronda 1, y su
disparador (`current_drawdown_pct`) solo se mueve con PnL **realizado**. Cuatro posiciones
correlacionadas cayendo un 15 % a la vez no mueven `wb` ni un centavo: el halt no salta, no se
aplana nada, y cuando por fin salta (porque los SL ya cerraron) aplanar ya no sirve de nada. El
`_risk_monitor_loop` YA lee `positionRisk` cada 2 s (`main.py:725-742`) con `unrealizedProfit` por
posición, así que el dato está a mano y no se usa. `01-F07` y `02-10` describen la causa raíz y
**siguen abiertos** — los cito porque el fix de la ronda 1 construyó encima una función de
seguridad que hereda el defecto.
**Fix:** en el `_risk_monitor_loop` live, tras leer `positionRisk`, calcular
`margin_balance = wallet + Σ unrealizedProfit` y llamar a `update_equity_safe(margin_balance)`;
inicializar equity/peak al arrancar con `GET /fapi/v2/account.totalMarginBalance` y persistir
`equity_peak`. En `ACCOUNT_UPDATE` usar `cw` + Σ`up` del array `a.P`.
**Verificado como:** lectura de `main.py:372-383,690-782`, `risk/risk_manager.py:387-398,491-498` +
contraste con `tasks/audit/01_core_strategy_risk.md:115-127` y
`tasks/audit/02_exchange_execution.md:227-239` (ambos abiertos: `grep` sobre `main.py` confirma
que `wb` sigue siendo la única fuente).

### [P2] fix_core-06 — La ventana de performance (20 trades cerrados) vive SOLO en memoria y no se persiste: con los reinicios reales del bot nunca llega a 20 → el factor es 1.0 permanente y toda la maquinaria F03 es inerte
**Archivo:** `portfolio/portfolio_manager.py:109-116` (+ ausencia de persistencia)
**Evidencia:**
```python
# portfolio_manager.py:109-116 — todo en RAM, sin load/save
self._strategy_closed_pnl: Dict[StrategyType, Deque[float]] = {
    st: deque(maxlen=PERF_WINDOW) for st in StrategyType
}
self._perf_blocked_since: Dict[StrategyType, float] = {}
```
```
$ grep -rn "_strategy_closed_pnl" --include=*.py .    # (excluyendo build/ y binarios)
portfolio/portfolio_manager.py:111,238,272,318,322     <- solo la propia clase
$ grep -rn "json.dump|pickle|to_json" portfolio/ risk/  -> sin resultados
```
**Por qué:** `PERF_MIN_TRADES = 20` cuenta trades CERRADOS por estrategia dentro de un proceso.
El bot se reinicia en cada deploy (`deploy/update.sh`), en cada reinicio del watchdog y en cada
`os._exit(3)` del bridge; y el ritmo real de cierres documentado es de unidades (`portfolio_manager.py:23-26`:
«20 % WR / −$2.11 sobre 5 cierres en paper»). El contador se reinicia a 0 cada vez, así que
`_performance_factor` devuelve 1.0 de forma permanente y ni el recorte de asignación ni el
`PERF_BLOCK_THRESHOLD` llegan nunca a evaluarse. Es el mismo patrón de `01-F19` (modelos cuant que
solo viven en memoria) aplicado al mecanismo nuevo de la ronda 1.
**Fix:** al arrancar, rellenar la ventana desde `TradeRepository` (`data/trade_database.db` ya
guarda cada EXIT con su PnL y su estrategia): `SELECT pnl FROM trades WHERE trade_type='EXIT' AND
strategy=? ORDER BY ts DESC LIMIT 50`. Persistir también `_perf_blocked_since` junto a
`equity_peak` en un `data/risk_state.json`.
**Verificado como:** `grep` (salida arriba), lectura de `portfolio_manager.py:97-136`, ausencia de
cualquier `load`/`save` en `portfolio/` y `risk/`.

### [P2] fix_core-07 — El nuevo gate `entries_allowed` hace que una estrategia INELEGIBLE/congelada ejecute `generate_signals()` sobre la posición de OTRA estrategia (en live `_positions` es por símbolo, no por estrategia) — y diverge de paper
**Archivo:** `main.py:524-541` (+ `strategies/fibonacci_retracement.py:201-206`, `strategies/mean_reversion.py:160-165`)
**Evidencia:**
```python
# main.py:525-541
if self.paper_sim:
    current_pos = self.paper_sim.get_position(symbol, strategy.strategy_type)  # POR ESTRATEGIA
else:
    current_pos = self._positions.get(symbol)                                  # POR SÍMBOLO
entries_allowed = (strategy.should_activate(regime)
                   and self.portfolio_manager.should_strategy_trade(...))
if current_pos is None and not entries_allowed:
    continue                    # <- antes del fix se salía SIEMPRE que el gate cerraba
```
```python
# strategies/fibonacci_retracement.py:202-206 (idéntico en mean_reversion.py:161-165)
if current_position is not None:
    exit_sig = self._check_exit(symbol, m15, current_position, snapshot)  # posición ajena
    if exit_sig:
        signals.append(exit_sig)
    return signals
```
**Por qué:** Antes del fix, una estrategia con el gate cerrado hacía `continue` y nunca veía nada.
Ahora, en live, basta con que HAYA una posición en el símbolo (aunque sea de otra estrategia) para
que la estrategia congelada corra su `_check_exit` sobre ella. La única barrera es que
`_check_exit` exige `self._states.get(symbol)` — pero ese estado queda obsoleto precisamente en
live, porque `notify_external_exit` solo se llama desde `_process_paper_fill` (paper) y desde
`_notify_strategies_flat` (`01-F08` sigue abierto): tras un SL/TP del exchange el estado de la
estrategia NO se limpia. Con dos estrategias elegibles en un mismo símbolo, la señal
`exit_*` de la estrategia A cerraría (MARKET reduceOnly) la posición de B. Hoy está latente
(Fibonacci congelada y `SYMBOL_STRATEGY_MAP` deja una sola estrategia por símbolo), pero es un
riesgo armado que no existía antes del fix. Además introduce una divergencia paper/live real:
en paper la estrategia congelada se salta (`continue`), en live se evalúa en cada ciclo.
**Fix:** llevar `_positions` a la misma clave que paper (`f"{symbol}_{strategy.value}"`, poblada
desde el `strategy` de la orden de entrada), o como mínimo `if current_pos is not None and
getattr(current_pos, "strategy", None) not in (None, strategy.strategy_type): current_pos = None`.
Y cerrar `01-F08` llamando a `notify_external_exit` también desde `on_order_update` en live.
**Verificado como:** lectura de `main.py:520-558,625-645,873-884`,
`strategies/fibonacci_retracement.py:168-210,509-531`, `strategies/mean_reversion.py:116-166`;
`grep -rn "notify_external_exit" main.py` → solo `:637` (paper) y `:880` (flatten).

### [P3] fix_core-08 — El clamp `[PERF_FLOOR, PERF_CEIL]` es matemáticamente inalcanzable (código muerto) y el test que lo "verifica" pasa solo por saturación de `tanh` en coma flotante
**Archivo:** `portfolio/portfolio_manager.py:244-245` (+ `tests/test_p0_round2.py:180-189`)
**Evidencia:**
```python
factor = 1.0 + 0.5 * math.tanh(1.5 * avg_r)      # imagen = (0.5, 1.5) abierto
factor = max(PERF_FLOOR, min(PERF_CEIL, factor)) # nunca actúa
```
Snippet ejecutado (`py -3.12`):
```
clamp binds ever? False
tanh(-1e9) = -1.0  factor = 0.5      # el test pasa por saturación float, no por el clamp
avg_r=-1.0000 -> factor=0.5474       # con el normalizador CORRECTO, -1R sí bloquearía (<0.6)
```
**Por qué:** No hace daño, pero da una falsa sensación de que hay una barrera de seguridad y el
test `test_performance_factor_floor_and_warning` (`assert f == PERF_FLOOR`) parece validar el
clamp cuando en realidad valida `math.tanh(-50) == -1.0`. Nota positiva: el snippet confirma que
la FORMA de la función es correcta — con el normalizador arreglado (fix_core-03), `avg_r = -1R`
da 0.547 < 0.6 y sí bloquea.
**Fix:** borrar el clamp (o dejarlo con un comentario «defensivo, inalcanzable») y reescribir el
test para que compruebe el umbral de bloqueo con un `avg_r` realista.
**Verificado como:** snippet ejecutado con `py -3.12` (salida arriba) + lectura de
`portfolio_manager.py:226-256`.

### [P2] fix_core-09 — Los tests de la ronda 1 solo cubren el camino feliz: ninguno ejercita el fallo del flatten, la concurrencia del shutdown ni magnitudes de PnL alcanzables
**Archivo:** `tests/test_p0_round2.py:688-845` y `:162-211`
**Evidencia:**
```python
# :761-763 close_all_positions SIEMPRE devuelve remaining=[]  -> el camino de fallo no se prueba
async def _close(*a, **k):
    bot.order.append("close_all_positions")
    return {"closed": [{"symbol": "ETH-USD"}], "remaining": [], "errors": []}
# :806-808 el "segundo shutdown" es SECUENCIAL, no concurrente
run(bot.shutdown()); assert bot.order == ["close_all_positions", "cancel_all"]
# :697-698 el fallback "muta" el exchange a plano a mitad -> no prueba get_positions() caído
await asyncio.sleep(0.05); client.positions = []
# :185,198 PnL imposibles con el sizing real ($2.21 máx por trade)
pm.update_strategy_pnl(mr, -500.0)  /  pm.update_strategy_pnl(mr, -30.0)
```
Ejecutado: `py -3.12 -m pytest tests/test_p0_round2.py -q -p no:cacheprovider` → **34 passed**;
`py -3.12 -m pytest tests/ -q -p no:cacheprovider` → **100 passed** (el README dice 92; hay 8 más).
**Por qué:** Los 34 tests verifican que el orden `close → cancel` es correcto cuando todo va bien,
que es el 5 % del riesgo. Los tres agujeros que encontró esta auditoría (fix_core-01, -02, -03)
son exactamente los que la suite no puede ver: no hay ningún test en el que `close_all_positions`
lance o devuelva `remaining`, ninguno con dos `shutdown()` concurrentes, y los del performance
factor usan pérdidas 13-225× mayores que la pérdida máxima física de un trade. Una suite verde
aquí no es evidencia de que los P0 estén cerrados.
**Fix:** añadir (1) `close_all_positions` que lanza / devuelve `remaining` ⇒ `cancel_all` NO se
llama; (2) `asyncio.gather(bot.shutdown(), bot.shutdown())` ⇒ `client.close` una sola vez y después
del flatten; (3) `get_positions` que falla en los 3 intentos ⇒ el resultado debe marcarse como
`unknown`, no como `remaining=[]`; (4) performance factor con pérdidas de −1R reales.
**Verificado como:** lectura completa de `tests/test_p0_round2.py` + ejecución de la suite
(salidas arriba).

## Tabla resumen

(pendiente)

## Veredicto

(pendiente)
