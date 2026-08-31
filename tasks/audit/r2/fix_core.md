# Auditoría R2 — AREA `fix_core`
## Revisión adversarial de los fixes de ronda 1 en core

**Alcance:** `main.py`, `execution/order_engine.py`, `execution/paper_simulator.py`,
`portfolio/portfolio_manager.py`, `config/settings.py` — commit base `b3dbf75`
(+ `fb073a1`, `1309927`, `6d528d9`, v2.13.1 posteriores).

**Método:** `git show b3dbf75`, lectura del código actual, PoC ejecutados con `py -3.12`,
suite completa (`tests/test_p0_round2.py` 34/34 verde) y lectura de lo que asertan de verdad.

**Estado:** COMPLETO — 14 hallazgos (2 P0, 4 P1, 5 P2, 3 P3) + 1 verificación OK.
Suite completa `py -3.12 -m pytest tests/ -q -p no:cacheprovider` → **132 passed**.

---

## Hallazgos

### [P0] fix_core-01 — `_flatten_all()` cancela los SL/TP igualmente cuando el cierre FALLA: la posición desnuda que F01 decía arreglar sigue viva en la rama de error

**Archivo:** `main.py:857-871`

**Evidencia:**
```python
result: Dict = {}
try:
    result = await self.execution_engine.close_all_positions()
except Exception as e:
    logger.error("flatten_close_positions_failed", reason=reason, error=str(e))
remaining = result.get("remaining") if isinstance(result, dict) else None
if remaining:
    logger.critical("flatten_incomplete_positions_remain", reason=reason,
                    symbols=[p.get("symbol") for p in remaining])
    asyncio.ensure_future(self.notifier.notify_error(...))
for item in (result.get("closed") or []) if isinstance(result, dict) else []:
    self._positions.pop(item.get("symbol"), None)
    self._notify_strategies_flat(item.get("symbol"), None)
await self.execution_engine.cancel_all()      # <-- INCONDICIONAL
```

**Por qué:** el commit `b3dbf75` documenta textualmente *"cancel_all removes the exchange
SL/TP, so it must never run while positions are still open"*. Pero `cancel_all()` está fuera
de todo condicional. Los dos caminos de fallo reales —`close_all_positions()` lanza
(timeout/5xx/ban de Binance, que es justo el momento en que se apaga el bot) o devuelve
`remaining` no vacío (`-2022`, `-1111`, posición por debajo de `minQty`)— acaban
**cancelando el stop-loss de una posición abierta a 5x** y dejando el bot apagado
inmediatamente después. El resultado es peor que antes del fix: antes quedaba desnuda; ahora
queda desnuda *y* con un `logger.critical` que nadie lee a las 4 AM. Además, en la rama de
excepción `remaining` es `None`, así que ni siquiera se emite la alerta.

**Fix:**
```python
if remaining or not isinstance(result, dict) or result.get("errors"):
    logger.critical("flatten_incomplete_protectives_kept_alive", reason=reason, ...)
    await self.notifier.notify_error("flatten_incomplete", ...)
    return          # NUNCA cancelar SL/TP sobre posición viva
await self.execution_engine.cancel_all()
```
(y en la rama `except`, tratar el fallo como `remaining` desconocido → mismo `return`).

**Verificado como:** PoC ejecutado con el `BotStrike._flatten_all` real
(`scratchpad/repro_flatten.py`, `py -3.12`), dos escenarios:
```
close_all_positions RAISES:            close_all_positions -> cancel_all  <-- SL/TP REMOVED
close_all_positions leaves REMAINING:  close_all_positions -> cancel_all  <-- SL/TP REMOVED
engine._positions still: ['ETH-USD']
```
Ningún test cubre esto: `tests/test_p0_round2.py:774-776` fija el mock a
`{"closed":[...], "remaining": [], "errors": []}` — sólo se testea el camino feliz.

---

### [P0] fix_core-02 — el camino de parada de PRODUCCIÓN (`bridge.stop_engine`) llama a `cancel_all()` ANTES de cerrar nada y nunca usa `_flatten_all()`

**Archivo:** `server/bridge.py:384-396`

**Evidencia:**
```python
async def stop_engine(manual: bool = False):
    """Gracefully stop the engine — mirrors CLI shutdown sequence (main.py:1080-1104)."""
    ...
        # Cancel live orders if in live mode (match CLI: main.py:1084-1085)
        if not engine.dry_run and not engine.paper:
            try:
                await engine.execution_engine.cancel_all()   # <-- SIN cerrar posiciones
            except Exception as e:
                logger.warning("shutdown_cancel_all_failed", error=str(e))

    if state.engine_task and not state.engine_task.done():
        state.engine_task.cancel()
        try:
            await asyncio.wait_for(state.engine_task, timeout=10)
```

**Por qué:** el bot en producción (CT 104, systemd `botstrike-bridge`) NO se para por el CLI:
se para por `/api/bot/stop` y por el `lifespan` del bridge, ambos vía `stop_engine()`. Ese
camino conserva **exactamente el orden que F01 declaró prohibido**: cancela primero los SL/TP
del exchange y sólo después cancela la task, con lo que `BotStrike.start()` llega a su
`finally: await self.shutdown()` → `_flatten_all()` cuando los protectivos ya no existen. La
referencia del docstring (`main.py:1080-1104`) apunta a líneas que hoy no son el shutdown
(está en `main.py:886`): el bridge quedó congelado en la versión pre-fix.
Agravante: `asyncio.wait_for(..., timeout=10)` aborta el `shutdown()` a mitad si el flatten
tarda más de 10 s — y `close_all_positions(max_attempts=3)` con 4 símbolos son ~15 llamadas
REST más 1.8 s de `sleep` fijos, más `websocket.stop()`, `client.close()` y dos llamadas HTTP
a Telegram. Es perfectamente alcanzable.

**Fix:** en `stop_engine`, sustituir el bloque `cancel_all()` por `await engine.shutdown()`
(que ya es idempotente vía `_shutdown_flatten_done`) antes de cancelar la task, y subir el
`wait_for` a ≥60 s o hacerlo configurable; nunca cancelar órdenes desde el bridge.

**Verificado como:** lectura de `server/bridge.py:300-424` (el engine se lanza como
`_run_engine()` → `state.engine.start()`, cuyo `finally` llama a `shutdown()`), y PoC de
cancelación en `py -3.12` (`scratchpad/repro_cancel.py`):
```
fast shutdown (0.1s), timeout 10s: ['caught_cancel', 'shutdown_begin', 'shutdown_flatten_done']
slow shutdown (2s),  timeout 0.5s: ['caught_cancel', 'shutdown_begin', 'run_engine_cancelled']
```
→ con un flatten lento, el `wait_for` mata el `shutdown()` **después** de haber empezado a
cerrar y **después** del `cancel_all()` del bridge.

---

### [P1] fix_core-03 — `close_all_positions()` aplana TODA la cuenta de futuros, no sólo los símbolos del bot: un deploy cierra a mercado posiciones que el bot nunca abrió

**Archivo:** `exchange/binance_client.py:743-765` (y el fallback `execution/order_engine.py:580-597`)

**Evidencia:**
```python
positions = await self.get_positions()        # GET /fapi/v2/positionRisk SIN symbol -> toda la cuenta
open_pos = [p for p in (positions or [])
            if float(p.get("positionAmt", p.get("size", 0)) or 0) != 0]
...
for p in open_pos:                            # <-- ningun filtro por settings.symbols
    order = Order(symbol=symbol, side=side, order_type=OrderType.MARKET,
                  quantity=abs(amt), reduce_only=True, ...)
    res = await self.place_order(order)
```
Y el round-trip de símbolos desconocidos funciona sin problemas, así que la orden se envía:
`_from_binance_symbol("DOGEUSDT") -> "DOGEUSDT"` (`binance_client.py:211-213`, `SYMBOL_MAP_REVERSE.get(s, s)`),
`_to_binance_symbol("DOGEUSDT") -> "DOGEUSDT"`.

**Por qué:** `close_positions_on_shutdown` es `True` por defecto y `deploy/update.sh` reinicia el
servicio en cada despliegue. Cualquier posición manual del usuario en la misma cuenta de
Binance Futures —una que el bot no gestiona, que no está en `settings.symbols`, que tiene su
propio SL puesto a mano— se **cierra a mercado con taker fee** en el siguiente deploy o en el
siguiente halt por drawdown. La función se llama "close_all_positions" pero el sitio que la
llama pretende "aplanar lo del bot". El halt por drawdown es aún peor: el drawdown lo mide el
bot sobre SU equity, y la reacción liquida posiciones ajenas.

**Fix:** filtrar por los símbolos gestionados —`{c.symbol for c in settings.symbols}`— y
pasar ese conjunto como parámetro explícito (`close_all_positions(symbols=...)`), dejando el
"toda la cuenta" sólo como opción deliberada de emergencia. Alternativamente, aplanar sólo los
símbolos con orden propia viva en `_active_orders` / `_positions`.

**Verificado como:** lectura de `/fapi/v2/positionRisk` sin `symbol` (devuelve la cuenta
entera, `binance_client.py:611-621`) y del round-trip de símbolos no mapeados
(`SYMBOL_MAP.get(symbol, symbol.replace("-", ""))`). Mitigación real: hoy Binance está cerrado
para residentes ES (`tasks/research_r2_venues_es_2026.md`), así que el escenario es futuro,
no actual.

---

### [P1] fix_core-04 — la puerta de rendimiento (F03) es INALCANZABLE: exige −0.73 R de media, pero el halt de drawdown salta 5× antes

**Archivo:** `portfolio/portfolio_manager.py:88, 233-263, 296-335`

**Evidencia:**
```python
PERF_MIN_TRADES = 20
PERF_BLOCK_THRESHOLD = 0.6
...
avg_r = (sum(closed) / n) / self._risk_budget_per_trade()
factor = 1.0 + 0.5 * math.tanh(1.5 * avg_r)
```
Resolviendo `1 + 0.5·tanh(1.5·avg_r) = 0.6`:

| magnitud | valor |
|---|---|
| `avg_r` necesario para bloquear | **−0.7324 R** por trade cerrado |
| presupuesto de riesgo ($1000 × 1.5%) | $15.00 |
| pérdida media exigida | **$10.99/trade** |
| pérdida acumulada en los 20 trades mínimos | **$219.8 = 22% del equity** |
| `max_drawdown_pct` | **10% = $100** → salta tras **9.1** de esos trades |

**Por qué:** la ronda 1 corrigió una fórmula que mataba una estrategia con −$0.03 de media…
sobrecorrigiendo hasta dejarla muerta en el otro sentido. La puerta no puede dispararse nunca
en producción: el circuit-breaker de drawdown (que aplana y para todo) llega antes. Peor aún
para el modo de fallo REAL medido en este proyecto: MR pierde **−10.5/−13.1 bps por trade**
sobre ~$150 de notional ≈ −$0.20/trade → `avg_r = −0.013` → `factor = 0.99`. La estrategia que
la tanda 1 demostró que sangra produce un factor **0.99**: completamente invisible. `PERF_FLOOR
= 0.5` sólo se alcanza a −2 R. La única protección efectiva que queda es "pierde el 10% primero".

**Fix:** el estadístico correcto no es la media en R sino la **significación**: bloquear cuando
`t = mean/(SE)` con `SE = std/√n` cae por debajo de −2 con `n ≥ 30`, o usar un Sharpe rodante
por trade. Y expresar el gate en unidades comparables con el coste de fricción (bps por trade),
no en R sobre un presupuesto de riesgo que el sizing real nunca llega a usar (ver `01-F13`:
riesgo real por trade 0.04–0.06%, no 1.5%) — con ese sizing, `avg_r` está por construcción
2 órdenes de magnitud por debajo del umbral.

**Verificado como:** `py -3.12 scratchpad/perf_gate.py`:
```
avg_r needed to BLOCK (factor<0.6): -0.7324 R per closed trade
  equity=$1000 risk_budget=$15.00 -> avg loss/trade $10.99; over 50 trades = $549.31 = 54.9% of equity
  ... but max_drawdown_pct=10% fires at $100 (after 9.1 such trades)
factor at avg_r=-0.2R: 0.8543   factor at avg_r=-1R: 0.5474
```
Los tests que "validan" F03 usan −$500/trade (`test_p0_round2.py:198`) y −$30/trade
(`:211`) sobre una cuenta de $1000: prueban el mecanismo, jamás la calibración.

---

### [P1] fix_core-05 — en LIVE la ventana de rendimiento se alimenta con el PnL BRUTO de Binance (`rp`), en paper/backtest con el NETO: la puerta es ciega a la sangría por comisiones

**Archivo:** `main.py:345-348` + `execution/order_engine.py:467-511` vs `execution/paper_simulator.py:99-114`

**Evidencia (live):**
```python
commission   = float(data.get("n", data.get("commission", 0)))
realized_pnl = float(data.get("rp", data.get("realizedProfit", 0)))
trade = Trade(..., fee=commission, pnl=realized_pnl, ...)
```
```python
# main.py:345
if trade.strategy:
    self.portfolio_manager.update_strategy_pnl(trade.strategy, trade.pnl)   # pnl = rp = BRUTO
```
**Evidencia (paper/backtest):**
```python
# paper_simulator.py:105-114  -> Position.close()
total_fee = entry_fee + exit_fee
return gross - total_fee, total_fee        # pnl NETO de comisiones round-trip
```

**Por qué:** en el stream de usuario de Binance USDⓈ-M, `rp` es *Realized Profit of the trade*
y `n` es la *Commission*, campos separados; el propio moderador de Binance ilustra
`rp 0.01830000 = (507.80 − 507.19) * 0.03`, es decir precio×cantidad **sin restar comisión**.
Consecuencia: (a) `_strategy_closed_pnl` mide cosas distintas en paper y en live, así que la
calibración hecha en paper no transfiere; (b) en live la puerta mide exactamente la magnitud
que la tanda 1 demostró que es **cero** (edge bruto −0.90/−0.63/−2.05/+0.45 bps, SE 1.2–2.6),
y es ciega a los 11 bps de fricción que son la única razón por la que la estrategia pierde.
Una estrategia que sangre indefinidamente por comisiones nunca reducirá su asignación.
(El mismo `realized_pnl` bruto alimenta `risk_manager.record_trade_result` y por tanto Kelly y
Risk-of-Ruin — impacto fuera de esta área, pero mismo origen.)

**Fix:** en `on_order_update`, componer `pnl_net = realized_pnl − commission` (convirtiendo
la comisión a USDT si `N != "USDT"`) y usar el neto para `update_strategy_pnl` /
`record_trade_result`, dejando `rp` y `n` como campos separados en `Trade` para la analítica.
Añadir un test de invariante paper↔live: mismo trade sintético → mismo `pnl` en ambos caminos.

**Verificado como:** lectura del código de ambos caminos + doc/foro oficial de Binance
([Event Order Update](https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams/Event-Order-Update),
[hilo del moderador con la aritmética de `rp`](https://dev.binance.vision/t/why-is-realized-profit-negative-on-futures-api/4550)).
`backtesting/backtester.py:1116` también usa el neto (`pos.close(exit_price, fee)`).

---

### [P1] fix_core-06 — `01-F02` sólo se arregló a medias: la estrategia hace `_states.pop()` ANTES de que la salida se llene, así que un exit rechazado nunca se reintenta

**Archivo:** `strategies/mean_reversion.py:393` y `strategies/fibonacci_retracement.py:515`
(consumidor: `main.py:612-617`)

**Evidencia:**
```python
# mean_reversion.py:390-408 (idéntico en fibonacci_retracement.py:512-530)
close_side = Side.SELL if position.side == Side.BUY else Side.BUY
size_usd = position.notional if position.notional > 0 else position.size * price
self._states.pop(symbol, None)          # <-- estado destruido al EMITIR, no al llenar
self._last_exit_time[symbol] = _time.time()
return Signal(..., size_usd=size_usd, metadata={"action": "exit_...", ...})
```
```python
# main.py:613-617 — fire and forget: si execute_signal devuelve None, nadie se entera
order = await self.execution_engine.execute_signal(sig, sym_config)
if order:
    logger.info("signal_executed", ...)
```

**Por qué:** el hallazgo `01-F02` proponía DOS cosas: (1) unificar el criterio `is_exit`
—hecho— y (2) *"no hacer `_states.pop` hasta confirmar el fill (o rehacer el estado si la
posición sigue existiendo en el siguiente eval)"* — **no hecho**. Hoy, si la orden de salida se
rechaza (`ValueError` de `_normalize_order_params` por `minQty`, `-1111`, `-4164`, timeout,
5xx), `execute_signal` devuelve `None` y la estrategia ya ha olvidado la posición: en el
siguiente tick `_check_exit` hace `state = self._states.get(symbol)` → `None` → `return None`.
**La salida no se reintenta jamás**; la posición queda viva sin trailing, sin SL software y sin
salida por stale, sólo con el SL/TP del exchange (que fix_core-01 puede haber cancelado).
Camino de rechazo concreto y alcanzable: `size_usd = position.notional = size × mark_price`
mientras `execute_signal` calcula `size_units = size_usd / signal.entry_price` con
`entry_price = snapshot.price` (último trade). `mark_price` (de `positionRisk`, hasta 2 s de
retraso) ≠ `snapshot.price` → la cantidad de cierre se desvía; si el último precio es mayor que
el mark, se **infra-cierra** y queda un residuo que ya nadie gestiona.

**Fix:** que `_check_exit` NO mute su estado; que main marque el estado como "exit pendiente"
y sólo llame a `notify_external_exit(symbol, ts)` cuando el fill esté confirmado
(`order.status == "FILLED"` o el `ORDER_TRADE_UPDATE` correspondiente). Y calcular la cantidad
de salida a partir de `position.size` directamente, no de `notional/price`.

**Verificado como:** lectura de los tres ficheros; `01-F02` (`tasks/audit/01_core_strategy_risk.md:43-44`)
documenta el mismo `_states.pop` como parte del fix propuesto y `git show b3dbf75 -- strategies/`
no toca ningún fichero de `strategies/`.

---

### [P2] fix_core-07 — `is_exit_signal` se documenta como "single source of truth" pero hay 3 copias divergentes: la del risk manager bloquea `trailing_stop_hit` y `mm_unwind` durante un halt

**Archivo:** `execution/order_engine.py:78-89` vs `risk/risk_manager.py:130-133` vs `execution/paper_simulator.py:393-398`

**Evidencia:**
```python
# order_engine.py:78-89 — "Single source of truth ... Mirrors paper_simulator and risk_manager"
action.startswith("exit") or action in ("trailing_stop_hit", "mm_unwind") \
    or signal.metadata.get("exit_reason") is not None
```
```python
# risk/risk_manager.py:130-133 — NO tiene la tupla
is_exit = (signal.metadata.get("action", "").startswith("exit")
           or signal.metadata.get("exit_reason"))
```
```python
# execution/paper_simulator.py:393-398 — copia literal, no llama a is_exit_signal
```

**Por qué:** el docstring afirma que refleja a los otros dos y no es verdad. Una señal
`trailing_stop_hit`/`mm_unwind` **sin** `exit_reason` es salida para el motor y para paper, pero
para el risk manager es una ENTRADA: pasa por el gauntlet completo y, con `_drawdown_halted`
activo, se **bloquea** (`risk_manager.py:137-140`) — exactamente la salida que más falta hace.
Hoy es latente (sólo existen MR y Fib, que emiten `exit_*` + `exit_reason`), pero es una mina
para cualquiera que reactive Market Making o añada un trailing hardware. Además `main.py:564` y
`server/bridge.py:547` tienen una CUARTA y QUINTA variante para clasificar logs.

**Fix:** `risk_manager` y `paper_simulator` deben importar y llamar
`OrderExecutionEngine.is_exit_signal` (o moverla a `core/types.py` para romper el ciclo de
imports), y `main.py:564` / `bridge.py:547` usarla también.

**Verificado como:** `py -3.12 scratchpad/exit_divergence.py` con el `RiskManager` real y
`_drawdown_halted = True`:
```
action                order_engine  paper_sim   risk_mgr  validate_signal(halt)
exit_mean_reversion   True          True        True      PASA
exit_fibonacci        True          True        True      PASA
trailing_stop_hit     True          True        False     BLOQUEADA
mm_unwind             True          True        False     BLOQUEADA
mr_entry              False         False       False     BLOQUEADA
```

---

### [P2] fix_core-08 — `allocation_mean_reversion = 0.00` NO congela nada: no participa en el sizing, y `/api/strategies` reporta `active`/`allocation` desde esa variable muerta

**Archivo:** `config/settings.py:104-110`, `portfolio/portfolio_manager.py:126-132, 185-224`, `server/bridge.py:1392-1417`

**Evidencia:**
```python
# portfolio_manager.py:126-132 — unico uso de allocation_*: sembrar un dict de REPORTING
self._current_weights = {StrategyType.MEAN_REVERSION: self.config.allocation_mean_reversion, ...}
# portfolio_manager.py:187-189 — el sizing real NO lo mira
regime_weight = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS[MarketRegime.UNKNOWN])
base_weight  = regime_weight.get(strategy, 0.33)
allocation   = equity * base_weight * perf_factor * dd_factor * symbol_share
```
```python
# bridge.py:1400-1414
alloc_active = alloc_map.get(s.strategy_type, 0) > 0     # alloc_map = settings.allocation_*
"active": alloc_active and research_active,
```
`grep` confirma que `allocation_mean_reversion` sólo aparece en: `settings.py`, el dict de
reporting, `main.py:1637` (print), `bridge.py:1393`, `serializers.py:206`, `live_monitor.py`.

**Por qué:** el commit de congelación pone tres palancas (`allocation_* = 0.00`,
`REGIME_WEIGHTS = 0.00`, `SYMBOL_STRATEGY_MAP = set()`) pero **sólo dos son reales**. Quien
descongele mañana tocando sólo `REGIME_WEIGHTS` creerá que `allocation_* = 0` sigue frenando y
el bot operará; y al revés, el dashboard seguirá diciendo `active: false, allocation: 0` mientras
el motor abre posiciones. Una UI que miente sobre si el bot opera es peor que no tener UI.

**Fix:** o eliminar `allocation_*` de `TradingConfig` y derivar los pesos de `REGIME_WEIGHTS`, o
—mejor— hacer que `get_allocation` multiplique por `self._current_weights[strategy]` para que
la palanca sea real; y que `/api/strategies` reporte
`portfolio_manager.should_strategy_trade(...)` en vez de `allocation_* > 0`.

**Verificado como:** `py -3.12 scratchpad/freeze_check.py` (ver fix_core-09) y `grep -rn
allocation_mean_reversion` sobre el repo excluyendo `build/`, `desktop/`, `archive/`.

---

### [OK] fix_core-09 — VERIFICADO OK: la congelación es hermética y el camino entries→exits sigue íntegro (no es un hallazgo; es la comprobación pedida)

**Archivo:** `portfolio/portfolio_manager.py:34-80`, `main.py:534-558`

**Evidencia:** ejecutado sobre los objetos reales, 4 símbolos × 5 estrategias × 5 regímenes = 100 combinaciones:
```
symbols: ['BTC-USD', 'ETH-USD', 'SOL-USD', 'ADA-USD']
should_strategy_trade True anywhere: []
max get_allocation over all combos: 0.0
unknown symbol DOGE-USD -> should_strategy_trade: False
empty symbol '' -> should_strategy_trade: False
```
Y en `main._process_symbol` el orden es correcto:
```python
entries_allowed = strategy.should_activate(regime) and self.portfolio_manager.should_strategy_trade(...)
if current_pos is None and not entries_allowed:
    continue                       # <-- generate_signals NI SE LLAMA
```
No existe ninguna vía alternativa de apertura: `execute_signal` sólo se llama en `main.py:614` y
`paper_sim.execute_signals` sólo en `main.py:609`, ambos sobre `validated`; el bridge no expone
ningún endpoint de orden manual (`grep @app.post` → `/api/bot/start`, `/api/bot/stop`,
`/api/backtest/run`).

**Y el camino entries→exits sigue íntegro para el día que se descongele:** MR
(`mean_reversion.py:161-165`) y Fib (`fibonacci_retracement.py:202-206`) comprueban la salida
**antes** de mirar `allocated_capital`, así que con `allocated = 0.0` y posición abierta la
señal de salida se genera igual; `risk_manager.validate_signal` la devuelve intacta antes de
cualquier filtro (`risk_manager.py:129-135`). Confirmado por
`test_exit_executed_when_regime_gate_blocks_entries` y
`test_exit_executed_when_performance_gate_blocks_entries` (34/34 verde).

**Salvedad:** los tests de ese camino usan un `FakeStrategy` que devuelve un exit siempre que
`current_position is not None`, lo cual enmascara el requisito real de las estrategias (tener
`_states[symbol]` vivo) — ver fix_core-06 y fix_core-11.

---

### [P2] fix_core-10 — `shutdown()` sólo es idempotente para el flatten: cierre de sesión de BD, Telegram y WS se ejecutan DOS veces en cada parada por Ctrl-C/SIGINT

**Archivo:** `main.py:886-916`, `main.py:1642-1662`

**Evidencia:**
```python
if not self._shutdown_flatten_done:     # <-- el guard SOLO cubre el flatten
    self._shutdown_flatten_done = True
    ...
await self.websocket.stop()             # <-- fuera del guard
await self.client.close()
self.trade_db.end_session(final_equity=..., max_drawdown=...)
...
await self.notifier.notify_shutdown(metrics)
await self.notifier.stop()
```
Y hay dos invocadores concurrentes: el handler de señal (`main.py:1652`,
`asyncio.ensure_future(bot.shutdown())`) y el `finally: await self.shutdown()` de `start()`
(`main.py:224`).

**Por qué:** `trade_db.end_session()` escribe la fila de sesión dos veces (equity final y
drawdown duplicados en la tabla de la que sale la analítica) y el usuario recibe dos mensajes
de Telegram de apagado. `client.close()` es inocuo (aiohttp comprueba `closed`), pero el
patrón "guard parcial" invita a que el próximo bloque que se añada tampoco esté protegido.

**Fix:** un único guard `if self._shutdown_done: return` al principio del método.

**Verificado como:** `py -3.12 scratchpad/repro_double_shutdown.py` con el `shutdown()` real:
```
close_all_positions calls : 1     cancel_all calls          : 1
trade_db.end_session calls: 2     websocket.stop calls      : 2
client.close calls        : 2     notifier.notify_shutdown  : 2
```

---

### [P2] fix_core-11 — tras un reinicio duro (watchdog `os._exit(3)` / SIGKILL) una posición live queda sin gestión software para siempre

**Archivo:** `main.py:524-558` + `strategies/mean_reversion.py:330-332`, `strategies/fibonacci_retracement.py:472-476`

**Evidencia:**
```python
# main.py:527-528 (live) — la posicion se reconstruye desde positionRisk...
current_pos = self._positions.get(symbol)
# mean_reversion.py:330-332 — ...pero el estado interno de la estrategia NO
state = self._states.get(symbol)
if not state:
    return None
```

**Por qué:** el comentario del fix promete *"An open position must ALWAYS be managed (trailing
stop, software SL, stale exit)"*. Se cumple dentro de una misma vida del proceso, pero el
bridge tiene un watchdog que hace `os._exit(3)` tras 5 fallos y systemd puede mandar SIGKILL:
en esos casos NO se ejecuta `shutdown()` (correcto: los SL/TP del exchange sobreviven), pero al
arrancar de nuevo `_states` está vacío y `_check_exit` devuelve `None` en cada tick, para
siempre. La posición sólo tiene ya el SL/TP duro: sin trailing, sin salida por stale a 24 h,
sin SL software. Además, como `self._positions` está indexado **por símbolo** y no por
símbolo+estrategia (a diferencia de paper, `paper_sim.get_position(symbol, strategy_type)`),
las DOS estrategias ven la misma posición y ambas ejecutan `generate_signals` sobre ella.

**Fix:** persistir `_states`/`_last_exit_time` (ya hay SQLite en `data/trade_database.db`) o
reconstruir un estado mínimo al arrancar cuando `positionRisk` devuelve posición y la estrategia
no tiene estado (entry_price, entry_bar_idx = 0, `best_pnl_atr` desde el PnL actual). Y pasar
`_positions` a clave `symbol+strategy` para igualar el modelo de paper.

**Verificado como:** lectura de los tres ficheros y de `server/bridge.py` (`os._exit(3)` tras
5 fallos del watchdog); ningún fichero de `strategies/` persiste estado
(`grep -n "json\|pickle\|sqlite" strategies/*.py` → sin resultados).

---

### [P2] fix_core-12 — el fallback de `OrderExecutionEngine.close_all_positions` reporta `remaining` obsoleto y puede duplicar cierres

**Archivo:** `execution/order_engine.py:555-608`

**Evidencia:**
```python
try:
    result = await native(max_attempts=max_attempts)
except TypeError:                      # <-- tambien captura TypeError DE DENTRO del cierre
    result = await native()            #     -> re-ejecuta el flatten entero
...
for attempt in range(max_attempts):
    ...
    remaining = open_pos               # <-- se fija ANTES de cerrar
    if not open_pos: break
    for p in open_pos: ... place_order ...
    await asyncio.sleep(0.3 * (attempt + 1))
# fin del for: NO hay relectura -> remaining son las posiciones YA cerradas del ultimo intento
```
Compárese con `exchange/binance_client.py:777-784`, que sí tiene la cláusula `for…else` con
relectura final.

**Por qué:** (a) el `except TypeError` está pensado para una firma sin `max_attempts`, pero
`float(None)` dentro del cierre (un `positionAmt: null` en `positionRisk`) también lanza
`TypeError` → se relanza el flatten completo y se reenvían MARKET reduceOnly ya enviadas.
(b) En Strike/Hyperliquid (que usan el fallback) el último intento exitoso reporta
`remaining` no vacío → `CRITICAL` + Telegram falsos en cada parada; y con el fix propuesto en
fix_core-01 (no cancelar si queda algo) se bloquearía `cancel_all` **para siempre** en esos
venues.

**Fix:** `except TypeError` sólo alrededor de la construcción de la llamada
(`inspect.signature`), y añadir la relectura final `for…else` idéntica a la de `BinanceClient`.

**Verificado como:** lectura comparada de las dos implementaciones. Ningún test cubre el
fallback con `remaining` no vacío (`test_engine_close_all_positions_fallback_uses_get_positions`
vacía `client.positions` a mitad para que salga limpio).

---

### [P3] fix_core-13 — `update_strategy_pnl` se llama sin el flag `is_exit` que la ronda 1 le añadió: una salida a breakeven exacto cuenta como entrada

**Archivo:** `main.py:630-631` (y `main.py:345-348`)

**Evidencia:**
```python
if trade.strategy:
    self.portfolio_manager.update_strategy_pnl(trade.strategy, trade.pnl)   # sin is_exit
...
# 28 lineas mas abajo, main.py:659 — el mismo metodo YA calcula el dato correcto:
is_exit = trade.pnl != 0 or sf.get("action", "").startswith("exit")
```
```python
# portfolio_manager.py:276-277
if is_exit is None:
    is_exit = pnl != 0
```

**Por qué:** el parámetro se añadió precisamente para no depender de `pnl != 0`, y nadie lo
usa. Un cierre exacto a breakeven (posible tras el redondeo a `stepSize`, o un `mm_unwind`)
queda fuera de la ventana de rendimiento. Impacto pequeño (sesga la ventana hacia abajo, no
hacia arriba) pero es código muerto con nombre de garantía.

**Fix:** `self.portfolio_manager.update_strategy_pnl(trade.strategy, trade.pnl, is_exit=is_exit)`,
subiendo el cálculo de `is_exit` por encima de la llamada.

**Verificado como:** lectura de `main.py:625-681` y `portfolio_manager.py:265-281`;
`grep -rn "update_strategy_pnl" --include=*.py` → ninguna llamada pasa `is_exit` (main:346,
main:631, backtester:852/892/1116/1194, scripts/test_paper.py:34).

---

### [P3] fix_core-14 — dos puertas de régimen con defaults distintos para la misma pregunta

**Archivo:** `portfolio/portfolio_manager.py:189` vs `:309`

**Evidencia:**
```python
# get_allocation:189
base_weight = regime_weight.get(strategy, 0.33)   # default 0.33
# should_strategy_trade:309
base_weight = regime_weight.get(strategy, 0.0)    # default 0.0
```

**Por qué:** una `StrategyType` nueva que se olvide en `REGIME_WEIGHTS` obtiene "no operar"
en un sitio y "33% del equity" en el otro. Hoy inalcanzable (las 5 estrategias están en los 5
regímenes y `get_allocation` sólo se llama si `should_strategy_trade` dijo que sí), pero es
justo el tipo de default silencioso que convierte un olvido en una posición.

**Fix:** `0.0` en ambos, o una constante compartida.

**Verificado como:** ejecutado `scratchpad/freeze_check.py`: `REGIME_WEIGHTS[RANGING]` contiene
las 5 estrategias, así que el default no se alcanza hoy.

---

### [P3] fix_core-15 — el flatten de paper usa el doble de slippage que una salida normal

**Archivo:** `execution/paper_simulator.py:353-357` vs `:406-412`

**Evidencia:**
```python
# close_all_positions (flatten)
slip = self.config.slippage_bps * price / 10_000            # 1.0x
# _execute_one (salida por señal)
exit_slip_bps = self.config.slippage_bps * 0.5              # 0.5x
exit_slip = exit_slip_bps * signal.entry_price / 10_000
```

**Por qué:** el PnL de un cierre por drawdown/shutdown no es comparable con el de una salida
normal en la misma serie de paper. Es conservador (peor precio), así que no infla resultados,
pero mete un sesgo no documentado en la trade DB de la que salen las métricas del soak.
Los SL usan `1.5x` (`:274`), lo que sugiere que el `1.0x` del flatten es un descuido, no una
decisión.

**Fix:** unificar en un único helper `_adverse_fill_price(side, price, kind)` con los
multiplicadores documentados (`entry`/`exit`/`sl`/`flatten`).

**Verificado como:** lectura comparada de las tres rutas de fill del simulador.

---

## Tabla resumen

| id | Sev | Archivo:línea | Título | ¿Fix R1 correcto? |
|----|-----|---------------|--------|-------------------|
| fix_core-01 | **P0** | `main.py:857-871` | `_flatten_all` cancela SL/TP igual cuando el cierre falla o deja `remaining` | **NO — incompleto en la rama que importa** |
| fix_core-02 | **P0** | `server/bridge.py:384-396` | El stop de producción llama `cancel_all()` antes de cerrar y nunca usa `_flatten_all` | **NO — el fix se puentea en prod** |
| fix_core-03 | P1 | `exchange/binance_client.py:743-765` | El flatten aplana toda la cuenta, no sólo los símbolos del bot | Fix nuevo, con efecto colateral |
| fix_core-04 | P1 | `portfolio/portfolio_manager.py:88,250-252` | La puerta de rendimiento exige −0.73 R: inalcanzable antes del halt de DD | **Sobrecorrección: gate muerto** |
| fix_core-05 | P1 | `main.py:345-348` | Live alimenta la ventana con `rp` BRUTO; paper/backtest con el NETO | **NO — ciego a la fricción** |
| fix_core-06 | P1 | `strategies/mean_reversion.py:393` | `01-F02` a medias: `_states.pop()` antes del fill → exit rechazado nunca se reintenta | **Parcial (mitad del fix propuesto)** |
| fix_core-07 | P2 | `risk/risk_manager.py:130-133` | "Single source of truth" con 3 copias; el risk manager bloquea `trailing_stop_hit`/`mm_unwind` | Parcial |
| fix_core-08 | P2 | `config/settings.py:104` | `allocation_* = 0.00` no congela nada; `/api/strategies` reporta desde esa variable muerta | Palanca cosmética |
| fix_core-09 | OK | `portfolio/portfolio_manager.py:34-80` | Congelación hermética (100/100 combinaciones) y exits íntegros | **Correcto** |
| fix_core-10 | P2 | `main.py:886-916` | `shutdown()` idempotente sólo para el flatten: BD y Telegram se duplican | Parcial |
| fix_core-11 | P2 | `main.py:527-528` | Tras `os._exit(3)`/SIGKILL la posición queda sin gestión software para siempre | Incompleto |
| fix_core-12 | P2 | `execution/order_engine.py:555-608` | Fallback con `remaining` obsoleto y `except TypeError` que puede duplicar cierres | Incompleto |
| fix_core-13 | P3 | `main.py:630-631` | El flag `is_exit` que la R1 añadió no lo usa nadie | Código muerto |
| fix_core-14 | P3 | `portfolio/portfolio_manager.py:189` | Defaults divergentes 0.33 vs 0.0 para la misma pregunta | Preexistente |
| fix_core-15 | P3 | `execution/paper_simulator.py:353-357` | El flatten de paper usa 2× el slippage de una salida normal | Fix nuevo, sesgo no documentado |

**Suite:** `py -3.12 -m pytest tests/ -q -p no:cacheprovider` → **132 passed** (los 34 de
`tests/test_p0_round2.py` incluidos). Verde, pero ninguno de los 34 cubre las ramas de fallo
de fix_core-01, -10 ni -12: `_shutdown_bot` fija el mock de `close_all_positions` a
`{"closed": [...], "remaining": [], "errors": []}` de forma incondicional
(`tests/test_p0_round2.py:774-776`), y los tests de F03/F09 sustituyen el `PortfolioManager`
real por un `MagicMock` (`:260-262`), así que la calibración del gate nunca se ejerce.

---

## Veredicto

1. Los fixes de la ronda 1 son **buenos en el camino feliz y frágiles justo donde tenían que
   ser robustos**: cada uno se testeó con el mock que devuelve éxito.
2. El P0 original (posición desnuda a 5x) **sigue vivo por dos vías independientes**:
   `_flatten_all` cancela los SL/TP aunque el cierre haya lanzado o dejado posiciones abiertas,
   y el `stop_engine` del bridge —el camino real de producción— cancela **antes** de cerrar y
   nunca invoca `_flatten_all`. El comentario del código dice literalmente lo contrario.
3. `is_exit_signal` sí arregla el enrutado de `exit_fibonacci` y se comparte con el backtester,
   pero el docstring "single source of truth" es falso: hay tres copias y la del risk manager
   bloquea dos acciones de salida durante un halt.
4. La otra mitad de `01-F02` —no destruir el estado de la estrategia antes de confirmar el
   fill— **no se aplicó**, así que una salida rechazada sigue dejando la posición huérfana.
5. La puerta de rendimiento pasó de "mata con −$0.03" a "no se dispara nunca": exige −0.73 R de
   media, ~22% de drawdown, cuando el circuit-breaker salta al 10%. Es una sobrecorrección
   verificada numéricamente, no una opinión.
6. Y aunque se disparase, en live mide el PnL **bruto** de Binance (`rp`, sin comisión) mientras
   paper y backtest miden el neto: es ciega justo al modo de fallo que la tanda 1 documentó
   (edge bruto ≈ 0, pérdida por 11 bps de fricción).
7. `close_all_positions` aplana **toda la cuenta de futuros**, incluidos símbolos que el bot no
   gestiona. Con `close_positions_on_shutdown=True` por defecto, cada deploy los cierra a mercado.
8. La congelación de estrategias **sí es hermética** (100/100 combinaciones a 0.0, `generate_signals`
   ni se llama sin posición) y el camino entries→exits sigue íntegro para descongelar. Pero de
   las tres palancas de congelación sólo dos son reales: `allocation_* = 0.00` no interviene en
   el sizing y sin embargo es lo que el dashboard usa para decir "inactiva".
9. Prioridad de arreglo: **fix_core-02 y -01 antes de que el bot toque dinero real** (son la
   diferencia entre un stop ordenado y una posición apalancada sin stop), luego -06 y -05.
10. Nada de esto pierde dinero hoy porque el bot no abre posiciones y Binance está cerrado para
    residentes ES. Son bombas armadas para el día del descongelado, no incendios activos.
