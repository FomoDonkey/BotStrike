# Auditoría R2 — fix_exchange: revisión adversarial de los fixes de ronda 1 en `exchange/`

**Fecha:** 2026-08-31 · **Commit auditado:** `b3dbf75` (v2.12.1) · **Alcance:** `exchange/binance_client.py`, `exchange/binance_ws.py`
(+ `execution/order_engine.py` en lo que toca a `_await_fill` / retry `-2022`, porque el fix ronda 1 reparte la lógica entre ambos).
**Método:** `git show b3dbf75 -- exchange/`, lectura completa del código post-fix, `GET /fapi/v1/exchangeInfo` REAL descargado
(2026-08-31, `serverTime=1788134461958`), doc oficial USDT-M Futures, y ejecución de `_normalize_order_params` /
`floor_to_step` / `round_to_tick` con `py -3.12` y sesión falsa.
Registro incremental: cada hallazgo se añade al confirmarlo.

## Datos de referencia (exchangeInfo real, 2026-08-31)

| symbol | tickSize | LOT_SIZE step/min/max | MARKET_LOT_SIZE step/min/max | MIN_NOTIONAL | PERCENT_PRICE up/down |
|---|---|---|---|---|---|
| BTCUSDT | 0.10 | 0.001 / 0.001 / 1000 | 0.001 / 0.001 / **120** | **50** | 1.05 / 0.95 |
| ETHUSDT | 0.01 | 0.001 / 0.001 / 10000 | 0.001 / 0.001 / **2000** | 20 | 1.05 / 0.95 |
| SOLUSDT | 0.0100 | 0.01 / 0.01 / 1000000 | 0.01 / 0.01 / **80000** | 5 | 1.05 / 0.95 |
| ADAUSDT | 0.00010 | 1 / 1 / 30000000 | 1 / 1 / **3000000** | 5 | 1.05 / 0.95 |

`rateLimits` reales: `REQUEST_WEIGHT 2400/min`, `ORDERS 1200/min`, `ORDERS 300/10s`.

## Hallazgos

### [P0] fix_exchange-01 — `close_all_positions()` devuelve `remaining: []` (éxito silencioso) cuando NO consigue leer `positionRisk`; `_flatten_all` continúa y borra los SL/TP → posiciones DESNUDAS (el P0-03 de ronda 1 sigue abierto por el camino de fallo de lectura)

**Archivo:** `exchange/binance_client.py:741-789` · `main.py:857-871`

**Evidencia** (`exchange/binance_client.py:741-789`):
```python
for attempt in range(max_attempts):
    try:
        positions = await self.get_positions()
    except Exception as e:
        errors.append({"stage": "get_positions", "attempt": attempt + 1, "error": str(e)})
        await asyncio.sleep(0.5 * (attempt + 1))
        continue                      # <-- 'remaining' nunca se toca
    ...
else:
    try:
        positions = await self.get_positions()
        remaining = [...]
    except Exception:
        pass                          # <-- 'remaining' se queda en []
```
y el consumidor (`main.py:862-871`):
```python
remaining = result.get("remaining") if isinstance(result, dict) else None
if remaining:                          # [] es FALSY -> ni alerta ni Telegram
    logger.critical("flatten_incomplete_positions_remain", ...)
...
await self.execution_engine.cancel_all()   # borra los SL/TP del exchange
```

**Por qué:** el fix de ronda 1 (02-F03) invirtió el orden `close_all → cancel_all`, pero dejó el camino de error *fail-open*.
Si `/fapi/v2/positionRisk` falla en los 3 intentos (503, -1003 rate ban, corte de red — exactamente el escenario en que se dispara
un corte por drawdown o un shutdown) la función devuelve `{"closed": [], "remaining": [], "errors": [3 errores]}`.
`errors` **no lo lee nadie**: ni `main._flatten_all` ni `OrderExecutionEngine.close_all_positions`. Resultado:
no hay alerta crítica, no hay Telegram, y acto seguido `cancel_all()` elimina los STOP_MARKET/TAKE_PROFIT_MARKET del exchange
dejando la posición abierta a 2-5x sin ninguna protección y con el bot ya parado. Es el mismo daño que P0-03 original.

**Fix:** hacer *fail-closed*. `close_all_positions` debe devolver un marcador de "estado desconocido"
(p. ej. `remaining_unknown: True` o `remaining=[{"symbol": "?", "unknown": True}]`) cuando ninguna lectura de posiciones
tuvo éxito, y `main._flatten_all` debe tratar `errors` no vacío o `remaining_unknown` como fallo crítico:
alerta + Telegram + **NO llamar a `cancel_all()`** (dejar los SL/TP vivos es estrictamente mejor que quedarse desnudo).
Aplicar lo mismo al fallback de `execution/order_engine.py:578-608`, que tiene idéntico agujero.

**Verificado como:** snippet `py -3.12` con `get_positions` que siempre lanza `BinanceAPIError(503)`:
```
T1 close_all_positions con positionRisk SIEMPRE caido:
   get_positions llamadas: 4
   result: {"closed": [], "remaining": [], "errors": [3 x -1001]}
   >>> remaining == []  -> main._flatten_all ve 'remaining' FALSY -> no alerta -> cancel_all()
```
Caso de control (T8): cuando la 1ª lectura sí ve `BTC 0.005`, `remaining` se rellena y sí salta
`POSITIONS_STILL_OPEN_AFTER_CLOSE_ALL` → el agujero es específicamente el fallo total de lectura.

---

### [P1] fix_exchange-02 — REGRESIÓN introducida por el fix: `batch_orders` descarta órdenes inválidas con `continue` y el resultado deja de estar alineado por índice con la lista del llamante → `orderId` asignado a la Order EQUIVOCADA

**Archivo:** `exchange/binance_client.py:923-927` (+ `958-967`) · consumidor `execution/order_engine.py:436-442`

**Evidencia** (`exchange/binance_client.py:923-927`, añadido en `b3dbf75`):
```python
try:
    normalized = self._normalize_order_params(o, bsym)
except ValueError as ve:
    logger.error("binance_batch_order_invalid", symbol=bsym, error=str(ve))
    continue                      # <-- la orden desaparece del batch, pero NO del list del llamante
```
y el llamante mapea **por índice** (`execution/order_engine.py:436-442`):
```python
for i, resp in enumerate(order_results):
    if isinstance(resp, dict) and i < len(orders):
        oid = resp.get("orderId", resp.get("order_id", ""))
        if oid:
            orders[i].order_id = oid
            self._active_orders[oid] = orders[i]   # <-- Order equivocada
```

**Por qué:** antes de `b3dbf75` (`git show b3dbf75^:exchange/binance_client.py`) **no había `continue`**: cada Order producía
siempre una entrada del batch, así que los índices casaban. El fix de precisión (02-F01) añadió el descarte y rompió la
correspondencia. Lo mismo ocurre cuando un chunk entero falla (`except: logger.error(...)`, línea 955): no se añade ningún
resultado para ese chunk y **todos los índices posteriores se desplazan**.
Consecuencias en live con Market Making: (a) el `orderId` real de una cotización queda registrado bajo el símbolo/lado de otra,
por lo que el cancel-por-símbolo del MM manda `cancel_order` con el símbolo equivocado (-2011) y **la cotización real se queda
viva en el libro sin que nadie la cancele**; (b) el fill que llegue por WS para ese `orderId` se casa con la Order equivocada
→ `Trade` con símbolo/precio/lado erróneos → PnL y métricas corruptos.

**Fix:** devolver el mapeo por `clientOrderId`, que ya viene en la respuesta (`{"orders": [...], "by_cid": {...}}`), y que
`order_engine` haga `orders_by_cid[resp["clientOrderId"]].order_id = oid` en vez de indexar. Alternativa mínima: devolver una
entrada placeholder `{"orderId": "", "status": "REJECTED_LOCAL", "clientOrderId": o.client_order_id}` por cada orden descartada
para preservar el alineamiento.

**Verificado como:** snippet con 3 órdenes MM (BTC BUY / ADA SELL de 0.4 uds — por debajo de `minQty=1` → descartada / SOL BUY)
y `_auth_post` falso:
```
enviadas al exchange: ['bs_mm_A', 'bs_mm_C']
result[0] orderId=1000 (cid real bs_mm_A) -> orders[0] = BTC-USD BUY  cid=bs_mm_A   OK
result[1] orderId=1001 (cid real bs_mm_C) -> orders[1] = ADA-USD SELL cid=bs_mm_B   <-- MAL
```

---

### [P1] fix_exchange-03 — `load_exchange_info()` sólo carga filtros para los 4 símbolos de `SYMBOL_MAP`; cualquier otro símbolo usa `GENERIC_SYMBOL_FILTER`, que es incorrecto para el **96 %** de los pares USDT-M → `-1111 BAD_PRECISION`

**Archivo:** `exchange/binance_client.py:409-410` (+ `70-73`, `425-432`)

**Evidencia:**
```python
parsed = parse_symbol_filters(info)
wanted = set(SYMBOL_MAP.values())          # {'BTCUSDT','ETHUSDT','ADAUSDT','SOLUSDT'}
loaded = {k: v for k, v in parsed.items() if k in wanted}
```
```python
GENERIC_SYMBOL_FILTER: Dict[str, Decimal] = {
    "tickSize": Decimal("0.01"), "stepSize": Decimal("0.001"),
    "minQty": Decimal("0.001"), "minNotional": Decimal("5"),
}
```

**Por qué:** `exchangeInfo` ya se ha descargado entero (~1 MB, 883 símbolos); tirar 879 de ellos no ahorra ninguna llamada y
convierte el fix 02-F01 en un parche de 4 símbolos en vez de una solución general. Dos caminos reales lo alcanzan:
1. **Flatten de emergencia** (`close_all_positions`) de una posición en un símbolo no configurado — abierta a mano, heredada
   de otra sesión, o de un símbolo que se quitó del config. El `place_order` reduceOnly se construye con `GENERIC` y sale con
   una precisión inválida → `-1111` → el cierre falla → se acumula en `errors`, **que nadie lee** (ver hallazgo 01).
2. **Añadir un 5.º símbolo al config** (`Settings.symbols` es una lista editable, y el bridge acepta settings del cliente):
   `SYMBOL_MAP` no se actualiza, `load_exchange_info` no carga sus filtros, y todas sus órdenes salen con `stepSize=0.001`.

**Fix:** cargar TODOS los símbolos de `parsed` en la caché (o al menos `{self._to_binance_symbol(s.symbol) for s in
settings.symbols} | set(SYMBOL_MAP.values())`), y hacer que `get_symbol_filters` registre un `logger.error` cuando cae en
`GENERIC_SYMBOL_FILTER` (hoy es un fallback silencioso). Idealmente `place_order` debería **rechazar** la orden si no hay
filtros vivos para el símbolo en vez de adivinar.

**Verificado como:** `py -3.12` sobre el `exchangeInfo` real (883 símbolos):
```
stepSize != 0.001 (GENERIC): 852 (96%)
tickSize != 0.01  (GENERIC): 685 (78%)
XRPUSDT: qty 123.456 con GENERIC step 0.001 -> 123.456 (step real 0.1) => -1111 BAD_PRECISION
         con step real -> 123.4 (valido)
```

---

### [P1] fix_exchange-04 — El retry no-idempotente puede re-enviar el POST hasta 3 veces y su seguridad depende de un comportamiento NO documentado (`-4015` por `clientOrderId` duplicado); en la carrera "la orden entró pero el GET devuelve -2013" queda una posición VIVA sin SL/TP y sin tracking

**Archivo:** `exchange/binance_client.py:268-298`, `300-324`, `723-727`

**Evidencia:**
```python
if not idempotent:
    recovered = await self._recover_or_raise(e, path, recover_fn)
    if recovered is not None:
        return recovered
    # recover_fn confirmed "does not exist" -> fall through to resend
delay = self._RETRY_BASE_SEC * (2 ** attempt)
await asyncio.sleep(delay)          # <-- y vuelve a ejecutar request_fn()
```

**Por qué:** el bucle no lleva contador de "ya reenvié una vez": mientras `recover_fn` devuelva `None`, cada vuelta hace un
POST nuevo. Medido: **4 POST y 3 GET de recuperación** con un timeout persistente. Que eso no acabe en 4 posiciones depende
por completo de que Binance rechace un `newClientOrderId` repetido — comportamiento que **no aparece en la doc oficial**
(`-4015` está documentado sólo como *"Client order id length should not be more than 36 chars"*; el uso como error de
duplicado sólo está confirmado por reportes de la comunidad en dev.binance.vision). Un sistema que mueve dinero no debe
apoyarse en eso sin verificarlo en testnet.
Y aun asumiendo que `-4015` protege, el camino resultante es malo: el POST **sí entró**, el GET inmediato devolvió `-2013`
porque la orden aún no era consultable (el timeout de cliente es `sock_read=10s`, la orden puede seguir en proceso), se
reenvía, Binance responde `400 -4015`, `is_retryable` es False → excepción → `order_engine._place_order` la captura en
`except Exception` (línea 228-230), registra `order_failed` y devuelve `None`. Resultado: **posición abierta en el exchange,
sin SL/TP (nunca se llega a `_await_fill`/`_place_protective_orders`), fuera de `_active_orders` y fuera de `self._positions`**.
El risk manager no la ve; sólo la encontrará el próximo `close_all_positions`.

**Fix:** (a) limitar a **un solo** reenvío por orden (flag `resent = True`); (b) antes de dar por perdida una orden tras
`-4015`/error final, hacer una última consulta `GET /fapi/v1/order?origClientOrderId=...` **y también**
`GET /fapi/v2/positionRisk` del símbolo, y si aparece posición, colocar las protectivas y registrarla; (c) subir
`recvWindow` y añadir un `reconcile_open_positions()` al arranque que adopte cualquier posición sin protectivas.

**Verificado como:**
```
T2 timeout persistente con recover_fn->None: POST enviados: 4  recover (GET /order) llamados: 3
T3 recover devuelve la orden existente     -> POST enviados: 1  (CORRECTO)
T4 el GET de recuperacion tambien cae      -> POST enviados: 1  (CORRECTO, sin doble envio)
T5 batchOrders sin recover_fn              -> POST enviados: 1  (CORRECTO, sin doble envio)
```
Doc: `newClientOrderId` pattern oficial `^[\.A-Z\:/a-z0-9_-]{1,36}$`; `-2013 NO_SUCH_ORDER "Order does not exist."`,
`-2011 CANCEL_REJECTED "Unknown order sent."`, `-4015 INVALID_CL_ORD_ID_LEN`.

---

### [P1] fix_exchange-05 — `_await_fill` asume "LLENA" cuando no puede confirmarlo → protectivas `reduceOnly` sobre una posición inexistente; y la entrada LIMIT IOC no pide `newOrderRespType=RESULT` aunque la doc dice que lo soporta

**Archivo:** `execution/order_engine.py:264-292` · `exchange/binance_client.py:714-716`

**Evidencia** (`execution/order_engine.py:264-268`, `290-292`):
```python
get_order = getattr(self.client, "get_order", None)
if get_order is None or not order.client_order_id:
    logger.warning("fill_status_unknown_assuming_filled", ...)
    return order.quantity
...
logger.warning("fill_poll_timeout_assuming_filled", symbol=order.symbol, status=status, executed=executed)
return executed if executed > 0 else order.quantity
```
```python
# MARKET: get the final execution state in the ACK (P1-05)
if binance_type == "MARKET":
    params["newOrderRespType"] = "RESULT"      # <-- sólo MARKET
```

**Por qué:** la doc oficial de `POST /fapi/v1/order` dice explícitamente que con `RESULT` *"MARKET orders return final FILLED
status; **LIMIT orders with special timeInForce return final status (FILLED or EXPIRED)**"*. La rama de entrada LIMIT del
smart router usa `TimeInForce.IOC` (`order_engine.py:186`), así que pedir `RESULT` daría el estado final en la propia ACK.
Al no pedirlo, la ACK es `NEW` y **toda entrada LIMIT paga 5 polls × 0,2 s = 1 s** antes de colocar las protectivas (1 s de
exposición sin SL en una entrada que ya está llena).
Peor: si los polls fallan (503, ban de IP, corte) o la orden sigue `NEW`, `_await_fill` devuelve `order.quantity` y se colocan
SL+TP `reduceOnly` sobre una posición que **no existe** → `-2022` ×3 en el SL, `-2022` ×3 en el TP, luego el cierre de
emergencia MARKET reduceOnly (también rechazado) y un `EMERGENCY_CLOSE_ALSO_FAILED` crítico. Son 7 órdenes rechazadas
(cuenta contra el límite de ORDERS 300/10s) y una alerta crítica falsa por cada entrada no llena.

**Fix:** enviar `newOrderRespType=RESULT` también en LIMIT (todos los `timeInForce`); distinguir los tres casos en
`_await_fill` — `FILLED` (colocar), `terminal sin fill` (no colocar), y **`DESCONOCIDO`** (no colocar protectivas a ciegas:
consultar `positionRisk` del símbolo y dimensionar sobre `positionAmt` real, o dejar la orden en una cola de reconciliación).
Nunca `return order.quantity` como "por si acaso".

**Verificado como:**
```
T-B _await_fill con get_order caido: devuelve 0.05 (order.quantity=0.05) en 1.01s -> se asume LLENA
T-C _await_fill con orden que sigue NEW (executedQty=0) tras 5 polls: devuelve 0.05 -> protectivas sobre posicion INEXISTENTE
T-D PARTIALLY_FILLED 0.02/0.05 permanente: devuelve 0.02 (correcto) pero el resto 0.03 sigue VIVO y sin protectiva
T-F LIMIT IOC params enviados: {... 'timeInForce': 'IOC'}   -> newOrderRespType AUSENTE
```

---

### [P1] fix_exchange-06 — `connect_market()` sólo captura `ConnectionClosed`/`OSError`; un `InvalidStatus` en el handshake (HTTP 429/451) mata la tarea de datos de mercado para siempre (02-F09 sigue abierto, con evidencia nueva)

**Archivo:** `exchange/binance_ws.py:113-122` (+ `102-111`)

**Evidencia:**
```python
async for raw_msg in ws:
    ...
    try:
        msg = json.loads(raw_msg)
        ...
        await self._process_message(stream, data)
    except json.JSONDecodeError:
        continue
except (websockets.exceptions.ConnectionClosed, OSError) as e:
    self._connected = False
    ...
```

**Por qué:** con `websockets==14.1` (verificado con `issubclass`), `InvalidStatus`, `InvalidHandshake`, `InvalidURI`,
`ProtocolError`, `PayloadTooBig` y `SecurityError` **NO** son subclases de `OSError` — sólo `TimeoutError` lo es. Binance
devuelve HTTP **429** en el handshake WS cuando se supera el ritmo de conexiones y **451** en jurisdicciones bloqueadas
(exactamente el bloqueo regulatorio de España que ya consta en el estado del proyecto): `websockets` lo convierte en
`InvalidStatus`, la excepción escapa del `while self._running`, `connect_market()` termina y **no hay reconexión**.
Además, cualquier excepción lanzada dentro de `_process_message` (no sólo `JSONDecodeError`) escapa igual y deja
`self._connected = True` — el flag que el bridge publica como `ws_connected` — o sea, salud mintiendo.

**Fix:** capturar `Exception` en el bucle exterior (con `except asyncio.CancelledError: raise` delante), poner
`self._connected = False` en un `finally`, y envolver `_process_message` en su propio `try/except Exception` para que un
mensaje malformado no tire la conexión. Tratar `InvalidStatus` con código 451 como fatal-explícito (log crítico + alerta),
no como un reintento infinito silencioso.

**Verificado como:**
```
websockets version: 14.1
  InvalidStatus     OSError? False  mro=['InvalidStatus','InvalidHandshake','WebSocketException','Exception']
  ProtocolError     OSError? False
  ConnectionClosed  OSError? False  (se captura explicitamente, OK)
asyncio.TimeoutError is OSError subclass? True
```

---

### [P1] fix_exchange-07 — `batch_orders` traga el timeout: las órdenes pueden haber entrado y el MM las da por fallidas y vuelve a cotizar (sin reconciliación por `clientOrderId`)

**Archivo:** `exchange/binance_client.py:948-956` · `execution/order_engine.py:429-446`

**Evidencia:**
```python
try:
    # Non-idempotent POST: no blind retry (P0-02). A batch cannot be
    # reconciled atomically, so a timeout/5xx surfaces as an error.
    result = await self._auth_post("/fapi/v1/batchOrders", params)
    if isinstance(result, list):
        all_results.extend(result)
except Exception as e:
    logger.error("binance_batch_failed", error=str(e))     # <-- se traga
```

**Por qué:** no re-enviar es correcto, pero tragarse el error no lo es. `batch_orders` devuelve `{"orders": []}` sin señal de
"estado desconocido", y `_manage_mm_orders` simplemente registra `mm_batch_failed` y en el siguiente ciclo **vuelve a cotizar**.
Si el batch sí llegó, quedan 2..5 órdenes GTX vivas en el libro que el bot no conoce (no están en `_active_orders`) y por tanto
nunca cancela → cotizaciones fantasma que pueden llenarse y abrir posición sin que el motor se entere. El comentario dice que
"un batch no se puede reconciliar atómicamente", lo cual es cierto, pero **sí se puede reconciliar por `clientOrderId`**:
cada entrada del batch ya lleva un `newClientOrderId` único (`bs_mm_...`).

**Fix:** tras un fallo de batch, consultar `GET /fapi/v1/openOrders?symbol=` (peso 1 con símbolo) y adoptar/cancelar las
órdenes cuyo `clientOrderId` empiece por el prefijo del batch; propagar la incertidumbre en el retorno
(`{"orders": [...], "unknown": true}`) y que el MM no vuelva a cotizar ese símbolo hasta reconciliar.

**Verificado como:** lectura del código + `T5` del snippet (`batchOrders` sin `recover_fn` → 1 POST, excepción propagada hasta
el `except` que la registra y sigue). El único `newClientOrderId` por entrada se genera en `binance_client.py:921-922`,
así que la reconciliación es factible.

---

### [P2] fix_exchange-08 — `parse_symbol_filters` ignora `MARKET_LOT_SIZE`, los `maxQty` y `PERCENT_PRICE`: el fix de precisión cubre 4 de los 7 filtros que Binance aplica

**Archivo:** `exchange/binance_client.py:107-129`

**Evidencia:**
```python
if ft == "PRICE_FILTER":   f["tickSize"] = ...
elif ft == "LOT_SIZE":     f["stepSize"] = ...; f["minQty"] = ...
elif ft == "MIN_NOTIONAL": f["minNotional"] = ...
```
Filtros reales de BTCUSDT: `['PRICE_FILTER','LOT_SIZE','MARKET_LOT_SIZE','MAX_NUM_ORDERS','MIN_NOTIONAL','PERCENT_PRICE','POSITION_RISK_CONTROL']`.

**Por qué:** respondiendo a la pregunta del encargo — **sí, `MARKET_LOT_SIZE` es un filtro distinto de `LOT_SIZE`** y hoy los
4 símbolos configurados comparten `stepSize`/`minQty`, pero **no el `maxQty`**: BTC 1000 (LOT_SIZE) vs **120** (MARKET_LOT_SIZE),
ETH 10000 vs **2000**, SOL 1e6 vs **80000**, ADA 3e7 vs **3e6**. Como el código no guarda ningún `maxQty`, una orden MARKET
por encima del límite sale y la rechaza el exchange con `-4005`. Con `max_position_usd ≤ 500` eso es inalcanzable hoy, pero es
un límite silencioso que aparecería en cuanto crezca la cuenta o se opere un símbolo con `MARKET_LOT_SIZE` estrecho.
`PERCENT_PRICE` (±5 % sobre el mark) tampoco se comprueba: un TP o un límite fuera de banda se rechaza con `-4131`.

**Fix:** parsear también `MARKET_LOT_SIZE` (usarlo cuando `type == MARKET`), `LOT_SIZE.maxQty`, `PRICE_FILTER.minPrice/maxPrice`
y `PERCENT_PRICE`, y validarlos en `_normalize_order_params` con un `ValueError` explicativo en vez de dejar que el exchange
responda con un código numérico.

**Verificado como:** `H. BTC MARKET 500 BTC (MARKET_LOT_SIZE.maxQty=120): {'quantity': '500'}` — se acepta localmente.
`B. keys parseadas BTCUSDT: ['minNotional','minQty','stepSize','tickSize']`.

---

### [P2] fix_exchange-09 — El reintento de `-2022` dura 0,4 s reales (el `0.9` de la tupla nunca se usa) y trata como transitorio lo que la doc describe como un CONFLICTO permanente con otra orden reduceOnly

**Archivo:** `execution/order_engine.py:294-316`

**Evidencia:**
```python
_PROTECTIVE_RETRIES = 3
_PROTECTIVE_BACKOFF_SEC = (0.1, 0.3, 0.9)
...
for attempt in range(self._PROTECTIVE_RETRIES):
    ...
    if not retryable or attempt == self._PROTECTIVE_RETRIES - 1:
        return False
    await asyncio.sleep(self._PROTECTIVE_BACKOFF_SEC[min(attempt, 2)])
```

**Por qué:** con 3 intentos sólo se duerme tras el 1.º y el 2.º → **0,1 + 0,3 = 0,4 s**, no los 1,3 s que sugiere la tupla
(medido: `tiempo_total=0.419s`). Y la doc oficial define `-2022 REDUCE_ONLY_REJECT` como *"the new reduce-only order conflicts
with existing open orders; cancel the existing order and resubmit"* — es decir, la causa documentada **no** es "la posición
todavía no es visible" (que es lo que dice el comentario del código) sino un conflicto con otra reduceOnly ya viva. Reintentar
0,4 s no resuelve un conflicto: en netting one-way con dos estrategias sobre el mismo símbolo (02-F18) el SL de la segunda
entrada choca con el SL de la primera, agota los 3 intentos, el TP también, y se dispara el cierre de emergencia.

**Fix:** subir a 4-5 intentos con backoff real hasta ~2 s **y** distinguir causas antes de reintentar: consultar
`GET /fapi/v2/positionRisk` del símbolo — si `positionAmt == 0` no hay nada que proteger (abortar sin emergencia); si hay
posición, listar `openOrders` y cancelar la reduceOnly en conflicto antes de reintentar. Corregir la tupla o el rango
(`range(len(_PROTECTIVE_BACKOFF_SEC) + 1)`) para que el 0,9 se use.

**Verificado como:** `T-A retry -2022: intentos=3 ok=False tiempo_total=0.419s`.

---

### [P2] fix_exchange-10 — `DEFAULT_SYMBOL_FILTERS["BTCUSDT"]["minNotional"] = 100`; el valor real es **50**, y el comentario afirma que son "the real USDT-M filters observed"

**Archivo:** `exchange/binance_client.py:56-61`

**Evidencia:**
```python
# Safe fallback when GET /fapi/v1/exchangeInfo cannot be loaded. Values are
# the real USDT-M filters observed on 2026-08-29 (see tasks/audit/02).
DEFAULT_SYMBOL_FILTERS: Dict[str, Dict[str, Decimal]] = {
    "BTCUSDT": {..., "minNotional": Decimal("100")},
```
`GET /fapi/v1/exchangeInfo` (2026-08-31): `{'notional': '50', 'filterType': 'MIN_NOTIONAL'}` para BTCUSDT.
ETH (20), SOL (5), ADA (5), y los cuatro `tickSize`/`stepSize`/`minQty` **coinciden exactamente** con los reales.

**Por qué:** el error es conservador (bloquea de más, no de menos) y hoy no muerde porque `minQty = 0.001 BTC` ya vale más de
100 USDT. Pero muerde en cuanto BTC baje de 100 000 USDT **y** `exchangeInfo` no cargue: el lote mínimo de BTC pasaría a
rechazarse localmente con "notional below minNotional" y el bot dejaría de operar BTC sin razón real. Lo relevante es que el
comentario declara un valor verificado que no lo está — eso invita a confiar en la tabla sin volver a comprobarla.

**Fix:** poner `Decimal("50")` y añadir un test que compare `DEFAULT_SYMBOL_FILTERS` contra `exchangeInfo` real (marcado como
test de red/opcional) para que la deriva se detecte sola.

**Verificado como:**
```
A. DEFAULT_SYMBOL_FILTERS vs live -> MISMATCH BTCUSDT.minNotional: default=100  live=50   (unico mismatch de 16 valores)
```

---

### [P2] fix_exchange-11 — `get_order()` sólo interpreta `-2013` como "no existe"; cualquier otro error (incluido `-2011`) rompe la recuperación, y un `recvWindow` corto lo hace más probable

**Archivo:** `exchange/binance_client.py:659-668` (+ `74-76`, `217-227`)

**Evidencia:**
```python
except BinanceAPIError as e:
    if BINANCE_ERR_ORDER_NOT_EXIST in (e.body or ""):   # "-2013"
        return None
    raise
```

**Por qué:** la elección de `-2013` es **correcta** según la doc (`-2013 NO_SUCH_ORDER "Order does not exist."` es el de query;
`-2011 CANCEL_REJECTED "Unknown order sent."` es el de cancelación), así que esto no es un bug de doc. El problema es la
fragilidad: se compara una **subcadena** contra los primeros 200 caracteres del cuerpo, y cualquier otro fallo del GET
(`-1021` por deriva de reloj, `-1003` por rate ban, 5xx agotando sus 3 reintentos) hace que `recover_fn` lance → 
`_recover_or_raise` propaga el error original → la orden se da por perdida aunque exista. Además `_sign()` no envía
`recvWindow`, así que rige el default de 5000 ms; en el instante en que se recupera de un timeout, con la red degradada,
es justo cuando más fácil es pasarse (02-F14 sigue abierto: `b3dbf75` no tocó `_sign`).

**Fix:** parsear el JSON y comparar `body["code"] == -2013` (y aceptar también `-2011` como "no existe" por robustez);
enviar `recvWindow=5000..10000` explícito y sincronizar con `GET /fapi/v1/time` al arrancar (deriva medida en esta máquina:
**-848 ms**, dentro de rango, pero sin ninguna comprobación en el código).

**Verificado como:** doc oficial de códigos de error (fetch) + medición real
`local_before 1788136894764 / serverTime 1788136895790 → drift_ms -848`.

---

### [P2] fix_exchange-12 — `cancel_order` se reintenta como idempotente: un cancel que SÍ funcionó pero cuya respuesta se perdió vuelve como excepción `-2011`

**Archivo:** `exchange/binance_client.py:368-376`, `855-859`

**Evidencia:**
```python
async def _auth_delete(self, path, params=None):
    ...
    return await self._retry_request(_do, path)        # idempotent=True por defecto
```

**Por qué:** medido, un 503 en `DELETE /fapi/v1/order` produce **4 intentos**. El 2.º intento sobre una orden ya cancelada
devuelve `400 -2011` → `is_retryable` False → excepción. `_manage_mm_orders` la captura y **borra la orden del tracking**
(`self._active_orders.pop(oid, None)`, línea 403) asumiendo "ya llena o expirada", lo cual en este caso es correcto por
casualidad. Donde sí duele es en `replace_order` (`binance_client.py:884-901`): `cancel_ok` queda en `False` aunque el cancel
funcionase, y se coloca la orden nueva igual → posible orden duplicada (02-F19, sigue abierto).

**Fix:** tratar `-2011`/`-2013` en el DELETE como éxito idempotente (`return {"status": "CANCELED", "note": "already_gone"}`)
en vez de excepción.

**Verificado como:** `T6 DELETE /fapi/v1/order (idempotent=True por defecto) reintentos: 4`.

---

### [P2] fix_exchange-13 — Sin cambios en `b3dbf75`: modo de posición (Hedge), `workingType`, `priceProtect` y limitador por *weight* (02-F06 / 02-F07 / 02-F15 siguen abiertos, y ahora con la doc que lo confirma)

**Archivo:** `exchange/binance_client.py:690-716` (params de la orden), `147-169` (`_RateLimiter`), `971-988`

**Evidencia** — params reales enviados para un STOP_MARKET (capturados con `_auth_post` falso):
```
{'symbol': 'BTCUSDT', 'side': 'SELL', 'type': 'STOP_MARKET', 'quantity': '0.005',
 'newClientOrderId': 'bs_...', 'stopPrice': '105000', 'reduceOnly': 'true'}
```
```python
self._rate_limiter = _RateLimiter(max_requests=1200, window_sec=60.0)   # cuenta PETICIONES
```

**Por qué:** la doc de `POST /fapi/v1/order` dice literalmente que `reduceOnly` **"cannot be sent in Hedge Mode"** y que en
Hedge hay que mandar `positionSide=LONG|SHORT`. El cliente nunca consulta `GET /fapi/v1/positionSide/dual` ni fija el modo, así
que en una cuenta en Hedge **todas** las órdenes fallan con `-4061`/`-1106` — incluido el flatten de emergencia. Ningún test
lo cubre. Sobre el rate limit: `exchangeInfo` real declara `REQUEST_WEIGHT 2400/min`, `ORDERS 1200/min` y `ORDERS 300/10s`;
el limitador cuenta peticiones contra 1200, pero `/fapi/v2/positionRisk` y `/fapi/v2/account` pesan 5 cada una y
`/fapi/v1/depth?limit=20` pesa 2 — 1200 peticiones pueden ser >4000 de weight → `429`/`418`. Y en `418` el código reintenta
con backoff de 1-4 s ignorando `Retry-After`, que es la forma más rápida de alargar un ban de IP.
Falta también `workingType=MARK_PRICE` y `priceProtect=true` en los condicionales: con `CONTRACT_PRICE` (default) un pico de
last price dispara el SL sin que el mark lo respalde.

**Fix:** al arrancar, leer `positionSide/dual` y (a) abortar con error claro si está en Hedge, o (b) enviar `positionSide` y
omitir `reduceOnly`. Añadir `workingType=MARK_PRICE` + `priceProtect=true` a STOP_MARKET/TAKE_PROFIT_MARKET. Cambiar
`_RateLimiter` a un bucket de *weight* alimentado por la cabecera `X-MBX-USED-WEIGHT-1M` de cada respuesta, con bucket
separado para ORDERS, y honrar `Retry-After` en 418/429.

**Verificado como:** `T-G` (params capturados), `rateLimits` reales de `exchangeInfo`, y doc de `POST /fapi/v1/order`
("reduceOnly: Cannot be sent in Hedge Mode"; "positionSide: Default BOTH for One-way Mode; must send LONG or SHORT in Hedge Mode").

---

### [P3] fix_exchange-14 — `STOP_LIMIT`/`TAKE_PROFIT_LIMIT`: `price` y `stopPrice` se redondean en direcciones OPUESTAS → el límite queda al lado equivocado del trigger

**Archivo:** `exchange/binance_client.py:442-453`, `470-486`

**Evidencia:**
```
I.  BUY  STOP_LIMIT price=110000.04 stop=110000.04 -> {'price': '110000',   'stopPrice': '110000.1'}
I2. SELL STOP_LIMIT price=109999.96 stop=109999.96 -> {'price': '110000',   'stopPrice': '109999.9'}
```

**Por qué:** `_price_rounding_mode(order, is_trigger=False)` da BUY→floor y `(…, True)` da BUY→ceil, así que en un
`STOP_LIMIT` de compra el límite (110000) queda **por debajo** del disparador (110000.1): al dispararse, la orden límite no
puede llenarse. No es explotable hoy (`OrderType.STOP_LIMIT` y `TAKE_PROFIT_LIMIT` no se usan en ninguna estrategia — sólo
existen en `core/types.py`), pero es una trampa para el primero que los active.
El resto del redondeo **es correcto y conservador**, y así lo verifiqué: SL de un largo 109876.53→109876.5 (floor, stop más
ancho), TP de un largo 115432.17→115432.1 (dispara antes), SL de un corto 110123.47→110123.5 (ceil, más ancho).

**Fix:** para `STOP_LIMIT`/`TAKE_PROFIT_LIMIT`, redondear el `price` en la MISMA dirección que el `stopPrice` (o directamente
`price = stopPrice ± N ticks` con el offset explícito).

**Verificado como:** snippet `I`/`I2` arriba con filtros reales de BTCUSDT.

---

### [P3] fix_exchange-15 — Cada mensaje de `depth` y `markPrice` se procesa DOS veces: el WS emite dos eventos y `main.py` registra el mismo handler en ambos

**Archivo:** `exchange/binance_ws.py:155-156`, `192-193` · `main.py:333-334`, `408-409`

**Evidencia:**
```python
await self._emit("depth", depth_data)
await self._emit("depthUpdate", depth_data)
```
```python
self.websocket.on("depth", on_depth_update)
self.websocket.on("depthUpdate", on_depth_update)
```

**Por qué:** `@depth20@100ms` × 4 símbolos = 40 msg/s → 80 reconstrucciones de `OrderBook` por segundo, la mitad tiradas.
Ambos handlers son idempotentes (escriben el mismo snapshot), así que no corrompen datos; es CPU y GC desperdiciados en el
hilo del event loop, justo el que no debe bloquearse. Lo mismo con `markPrice`/`markPriceUpdate`.

**Fix:** emitir un solo evento (o registrar un solo handler). El alias existe por compatibilidad con `StrikeWebSocket`;
basta con que `_emit` no dispare el alias si ya hay callbacks en el nombre canónico.

**Verificado como:** `grep -n "websocket.on(" main.py` → `depth` y `depthUpdate` con la misma función; `markPrice` y
`markPriceUpdate` con la misma función.

---

### [P3] fix_exchange-16 — `place_bracket_order()` es código muerto que quedó DIVERGENTE del camino corregido: coloca protectivas con `status == "NEW"`, sin `_await_fill` y sin reintento de `-2022`

**Archivo:** `exchange/binance_client.py:791-853`

**Evidencia:**
```python
status = result.get("status", "")
if status not in ("FILLED", "PARTIALLY_FILLED", "NEW"):
    return result
filled_qty = float(result.get("executedQty", 0))
qty = filled_qty if filled_qty > 0 else order.quantity
```

**Por qué:** es exactamente el bug 02-F05 que la ronda 1 arregló en `order_engine._await_fill`, intacto aquí: con `NEW` y
`executedQty=0` cae en `qty = order.quantity` y manda SL/TP sobre una posición que puede no existir. Su reintento genérico
(2 intentos, `except Exception`) tampoco distingue `-2022`. Hoy no lo llama nadie (`grep place_bracket_order` sólo lo
encuentra en su definición), pero es una trampa para el siguiente que busque "cómo se colocan brackets aquí".

**Fix:** borrarlo, o hacer que delegue en el camino bueno.

**Verificado como:** `grep -rn "place_bracket_order" --include=*.py execution/ strategies/ main.py core/` → sin resultados.

---

### [P3] fix_exchange-17 — `MIN_NOTIONAL` no se comprueba en MARKET sin `_expected_price`, y ningún `maxQty` se comprueba nunca

**Archivo:** `exchange/binance_client.py:487-502`

**Evidencia:**
```
G. ADA MARKET 2 uds SIN _expected_price (notional ~1.1 USDT, min 5): {'quantity': '2'}
H. BTC MARKET 500 BTC (MARKET_LOT_SIZE.maxQty=120): {'quantity': '500'}
```

**Por qué:** la comprobación depende de un atributo dinámico opcional (`order._expected_price`) que sólo pone
`order_engine.py:171` en la rama MARKET. Cualquier otro constructor de órdenes MARKET de apertura (tests, un
`place_bracket_order` reactivado, integración futura) se salta el filtro y recibe `-4164` del exchange. El comportamiento
para `reduce_only` **sí es correcto**: la doc confirma que la exención existe (`-4164 "Order's notional must be no smaller
than %s (unless you choose reduce only)"`).

**Fix:** exigir un precio de referencia para toda orden de apertura (usar `get_mark_price` o el último `MarketSnapshot` si
no hay `_expected_price`) y convertir `_expected_price` en un campo real del dataclass `Order` en vez de un atributo pegado.

**Verificado como:** snippets `G`/`H` arriba, y doc del código `-4164`.

---

