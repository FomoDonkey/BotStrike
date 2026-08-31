# Auditoría R2 — AREA: fix_exchange

**Alcance**: revisión adversarial de los fixes de la ronda 1 (`b3dbf75`) en
`exchange/binance_client.py` y `exchange/binance_ws.py`, contrastados contra la
documentación oficial de Binance USDⓈ-M Futures y contra los valores reales de
`GET /fapi/v1/exchangeInfo` descargados hoy (2026-08-31, `serverTime=1788156524390`).

**Contexto operativo**: Binance está CERRADO para el dueño (residente ES) desde
2026-07-01 en modo solo-reducir. Hoy el cliente se usa SOLO para datos públicos
en paper. Prioridad aplicada: (a) ruta de datos públicos correcta, (b) ruta de
ORDENES que no se pueda disparar por accidente, (c) el resto como deuda documentada.

**Baseline**: `py -3.12 -m pytest tests/ -q -p no:cacheprovider` → **132 passed** en 2,27 s.

---

## Lo que la ronda 1 hizo BIEN (verificado — no invento hallazgos aquí)

- **Redondeo**: `floor_to_step` usa `Decimal(str(x))` + `ROUND_DOWN`, nunca `float`.
  La cantidad siempre va hacia abajo; nunca sobredimensiona. Correcto.
- **`format_decimal`**: `format(d.normalize(), "f")` evita notación científica.
  Verificado con `0.001`, `1E+2`, `0E-8` → `"0.001"`, `"100"`, `"0"`.
- **`newClientOrderId`**: cumple el regex oficial `^[\.A-Z\:/a-z0-9_-]{1,36}$` y la
  longitud máxima de 36. Verificado por snippet: 27 / 30 / 33 chars, `regex_ok=True`
  para los prefijos `bs`, `bs_mm`, `bs_close`.
- **`-2013` en `get_order`**: código correcto. La doc oficial confirma
  `-2013 NO_SUCH_ORDER "Order does not exist"` para endpoints de consulta, y
  `-2011 CANCEL_REJECTED` sólo para el DELETE. El fix eligió el código adecuado.
- **Mapeo de tipos**: `STOP→STOP_MARKET`, `STOP_LIMIT→STOP`,
  `TAKE_PROFIT→TAKE_PROFIT_MARKET`, `TAKE_PROFIT_LIMIT→TAKE_PROFIT` coincide con
  el array `orderTypes` real de exchangeInfo.
- **`newOrderRespType=RESULT`** en MARKET: soportado y correcto (doc: *"When set to
  RESULT, MARKET orders return final FILLED status"*).
- **Depth `b`/`a` (P1-04)**: verificado CONTRA EL STREAM VIVO. `@depth20@100ms`
  entrega 20 niveles en `b`/`a` y NO trae `bids`/`asks`. Además, al ser un
  *partial book depth* (snapshot completo cada 100 ms), **no hace falta** el ciclo
  snapshot+diff con `U`/`u`/`pu` que sí exigiría `@depth@100ms`. El fix es correcto
  y la ausencia de gestión de secuencia también lo es.
- **`tickSize`/`stepSize`/`minQty`** de los 4 símbolos coinciden EXACTAMENTE con
  los valores vivos de hoy. Sólo falla `minNotional` de BTC (ver `fix_exchange-07`).
- **Gating de paper**: `main.py:608` y `_flatten_all` (`main.py:846`) cortan antes
  de tocar `BinanceClient.place_order` en paper/dry-run. En el modo en que corre
  hoy el bot, la ruta de órdenes **no** se alcanza. Correcto.
- **`batch_orders`**: no hay reenvío ciego (sin `recover_fn` → `_recover_or_raise`
  lanza inmediatamente). El "doble envío en batchOrders" que buscaba el foco de
  esta auditoría NO existe. El problema es otro (ver `fix_exchange-13`).

---

## Hallazgos

### [P1] fix_exchange-01 — `load_exchange_info()` cachea SOLO los 4 símbolos mapeados: `close_all_positions()` no puede cerrar nada fuera de `SYMBOL_MAP`

**Archivo**: `exchange/binance_client.py:409`

**Evidencia**:
```python
info = await self.get_exchange_info()
parsed = parse_symbol_filters(info)
wanted = set(SYMBOL_MAP.values())          # {'BTCUSDT','ETHUSDT','ADAUSDT','SOLUSDT'}
loaded = {k: v for k, v in parsed.items() if k in wanted}
...
GENERIC_SYMBOL_FILTER = {"tickSize": Decimal("0.01"), "stepSize": Decimal("0.001"),
                         "minQty": Decimal("0.001"), "minNotional": Decimal("5")}
```

**Por qué**: `close_all_positions()` itera sobre TODO `/fapi/v2/positionRisk`, no
sólo sobre los 4 símbolos configurados. Para cualquier otro símbolo (posición
abierta a mano, restos de una versión anterior, un símbolo añadido a
`settings.symbols` sin tocar `SYMBOL_MAP`) los filtros caen al
`GENERIC_SYMBOL_FILTER`, cuyo `stepSize=0.001` es falso para la mayoría de perps.
Con XRPUSDT (real `stepSize=0.1`, `minQty=0.1`) el cliente emite
`quantity=123.456`, que Binance rechaza con `-1111 BAD_PRECISION`. El fix P0-03
("nunca dejar posiciones desnudas") falla exactamente en el caso para el que se
escribió, y como el cierre corre ANTES de `cancel_all()`, después se borran las
SL/TP y la posición queda desnuda de verdad. Es un fallo **introducido** por el
fix (antes no había filtro por símbolo porque no había filtros).

**Fix**: no filtrar por `wanted` — cachear todos los símbolos de exchangeInfo (se
parsea una vez; el JSON son ~1,1 MB y ~500 símbolos). Y en `close_all_positions`,
**abortar con log CRITICAL + notificación si el símbolo no tiene filtros reales**,
en lugar de enviar una cantidad que se sabe inválida.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex3.py
  cached symbols: ['ADAUSDT', 'BTCUSDT', 'ETHUSDT', 'SOLUSDT']
  get_symbol_filters('XRPUSDT') -> {'tickSize': Decimal('0.01'), 'stepSize': Decimal('0.001'), ...}
  XRPUSDT REAL stepSize=0.1 minQty=0.1
  emergency close of 123.456 XRP -> {'quantity': '123.456'}   -> -1111
```

---

### [P1] fix_exchange-02 — El `recover_fn` del fix P0-02 se dispara también en 429/418 y AMPLIFICA el rate-limit/ban de IP (rompe la ruta de datos públicos)

**Archivo**: `exchange/binance_client.py:275`

**Evidencia**:
```python
except BinanceAPIError as e:
    last_error = e
    if not e.is_retryable or attempt == self._MAX_RETRIES:
        raise
    if not idempotent:
        recovered = await self._recover_or_raise(e, path, recover_fn)   # <-- GET /fapi/v1/order
```
`is_retryable` incluye 429 y 418 (`binance_client.py:144`).

**Por qué**: un 429/418 significa que la petición **NO se ejecutó** — el estado no
es desconocido, y el `recover_fn` sobra. Peor: `recover_fn` es un
`GET /fapi/v1/order` que pasa por su propio `_retry_request`, así que cada intento
de orden añade peticiones **mientras ya estás limitado**. La doc oficial es
explícita: *"When a 429 is received, it's your obligation as an API to back off and
not spam the API"*, *"Repeatedly violating rate limits and/or failing to back off
after receiving 429s will result in an automated IP ban (HTTP status 418)"* y
*"IP bans are tracked and scale in duration for repeat offenders, from 2 minutes to
3 days"*. El cliente además **reintenta el propio 418** con 1 s/2 s/4 s. Esto es
prioridad (a): un ban de IP tumba el feed público de klines/depth REST del bot en
paper, y el propio retry lo convierte de 2 minutos en horas o días.
**`02-07` de la ronda 1 sigue abierto y el fix P0-02 lo ha empeorado.**

**Fix**: (1) `is_retryable` debe separar "no ejecutado" (429/418) de "estado
desconocido" (timeout/5xx) y saltarse `recover_fn` en el primer caso; (2) tratar
418 como NO reintentable a corto plazo — abrir un circuit breaker por IP con
back-off de minutos; (3) leer `Retry-After` cuando venga y `X-MBX-USED-WEIGHT-1m`
siempre, y frenar proactivamente al acercarse al límite.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex3.py    (### F, POST que devuelve 429)
  POSTs=4  extra GET /order recovery calls during the rate limit=3
```

---

### [P1] fix_exchange-03 — `place_order` no tiene deadline global: con `recover_fn` anidado puede tardar minutos antes de fallar o de mandar el MARKET

**Archivo**: `exchange/binance_client.py:268` (bucle) y `:199` (timeouts)

**Evidencia**:
```python
timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
...
_MAX_RETRIES = 3
_RETRY_BASE_SEC = 1.0
for attempt in range(self._MAX_RETRIES + 1):   # 4 intentos
    ...
    recovered = await self._recover_or_raise(e, path, recover_fn)  # GET con SU PROPIO _retry_request
    delay = self._RETRY_BASE_SEC * (2 ** attempt)                  # 1 + 2 + 4 s
```

**Por qué**: `recover_fn` → `get_order` → `_auth_get` → `_retry_request` con 4
intentos propios de hasta 15 s + 7 s de backoff ≈ 67 s **por cada** intento del
POST. En el peor caso `place_order` tarda del orden de 4-5 minutos antes de
devolver. Para un MARKET de entrada eso no es "robustez": es enviar una orden a
mercado con la señal caducada, o dejar la posición sin SL/TP durante minutos
mientras el engine espera (`_place_protective_orders` es secuencial). En una
estrategia intradía con SL de ~1 ATR, 5 minutos de latencia es la diferencia entre
un stop y una liquidación.

**Fix**: `deadline` absoluto por operación (p. ej. 8 s para entradas MARKET, 20 s
para protectivas), propagado al `recover_fn`; abortar el bucle en cuanto se supere
y devolver un estado explícito `UNKNOWN` que el engine trate como "posición
posiblemente abierta → reconciliar por `positionRisk`, no reintentar".

**Verificado como**: lectura del código + el snippet `### E` demuestra 4 llamadas
al POST y 3 al `recover_fn`; con los timeouts reales (15 s cada uno) el producto
son los minutos descritos.

---

### [P1] fix_exchange-04 — El P0-02 de la ronda 1 NO está cerrado: el reenvío sigue siendo posible y `newClientOrderId` sólo es único "entre órdenes ABIERTAS"

**Archivo**: `exchange/binance_client.py:279` y `:723`

**Evidencia**:
```python
                if not idempotent:
                    recovered = await self._recover_or_raise(e, path, recover_fn)
                    if recovered is not None:
                        return recovered
                    # recover_fn confirmed "does not exist" -> fall through to resend
```
y el docstring del método afirma: *"or None when the exchange confirms the request
never landed (-> **ONE** re-send is allowed)"*.

**Por qué**: dos problemas.
1. **El docstring miente**: no es un reenvío, son hasta **3** (4 POST en total).
   Verificado.
2. **La carrera sigue viva**: tras un timeout del POST la orden puede estar *en
   vuelo* en Binance. El `GET /order?origClientOrderId` se ejecuta antes de que la
   orden exista → `-2013` → `recovered is None` → se reenvía. La doc oficial dice
   que `newClientOrderId` es *"A unique id among **open** orders"*: un MARKET que ya
   se llenó **deja de ser una orden abierta**, así que el reenvío con el mismo
   `clientOrderId` NO es rechazado y se abre una segunda posición. Es exactamente
   el escenario que describía `02-02` de la ronda 1 (posición al doble con SL/TP
   dimensionados para el tamaño original). El fix estrecha la ventana pero no la
   cierra, y el docstring vende una garantía que el código no da.

**Fix**: para órdenes de entrada, la reconciliación fiable no es por
`clientOrderId` sino por **estado de posición**: tras un timeout, leer
`/fapi/v3/positionRisk` (o `userTrades` con `startTime`) y decidir con el delta de
`positionAmt`, no con la existencia de una orden. Como mínimo: reenviar **una sola
vez** (respetar el docstring), y esperar ≥1 s entre el POST fallido y el GET de
recuperación para que la orden en vuelo tenga tiempo de materializarse.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex3.py    (### E)
  POST attempts actually sent = 4   recover_fn calls = 3
  docstring claims: 'ONE re-send is allowed'
```
Doc oficial `POST /fapi/v1/order`: `newClientOrderId` = *"A unique id among open orders."*

---

### [P1] fix_exchange-05 — No hay kill-switch en el cliente ni en el CLI: `py main.py` sin flags opera EN VIVO con las claves del `.env` (prioridad (b))

**Archivo**: `main.py:1621` y `exchange/binance_client.py:670`

**Evidencia**:
```python
    else:
        bot = BotStrike(settings, dry_run=args.dry_run, paper=args.paper,
                        use_binance=args.binance)
```
con `exchange_venue: str = "binance"` (`config/settings.py:78`) y
```python
        self._api_key = os.getenv("BINANCE_API_KEY", "")
        self._api_secret = os.getenv("BINANCE_API_SECRET", "")
```

**Por qué**: `BOTSTRIKE_ALLOW_LIVE` (`server/bridge.py:1329`) sólo protege el
arranque **por el bridge**. Un `py main.py` a secas (sin `--paper` ni `--dry-run`)
construye el motor en modo live, con venue `binance` por defecto y las claves que
haya en `.env` — sin confirmación, sin variable de entorno, sin log de aviso. El
propio `deploy/verify.sh:27` comprueba que `BINANCE_API_KEY` esté puesta en el
`.env` de producción, así que las claves están donde puede leerlas. La barrera
está en el sitio equivocado de la pila: el objeto que envía dinero
(`BinanceClient.place_order`) no tiene ninguna. Dado que hoy Binance está cerrado
para el dueño en modo solo-reducir, el daño real es limitado, pero la petición
explícita de esta auditoría era "que la ruta de ORDENES no pueda dispararse por
accidente", y hoy sí puede.

**Fix**: guard de defensa en profundidad dentro de `BinanceClient.place_order` /
`batch_orders` / `close_all_positions`: si `os.getenv("BOTSTRIKE_ALLOW_LIVE") != "1"`
lanzar `RuntimeError("live orders disabled")` antes de firmar nada. Añadir el
mismo check en `main.py` antes de instanciar `BotStrike` sin `--paper`, y que el
modo por defecto del CLI sea `paper` en vez de live.

**Verificado como**: lectura de `main.py:1538-1622` (`--paper` es `store_true`,
no hay `--live`, no existe rama por defecto que exija confirmación) +
`grep -rn "ALLOW_LIVE"` → sólo aparece en `server/bridge.py` y en el `.service`.

---

### [P1] fix_exchange-06 — Modo Hedge sigue sin contemplarse y ahora rompe explícitamente el fix P0-03 (`02-06` sigue abierto)

**Archivo**: `exchange/binance_client.py:707` y `:761`

**Evidencia**:
```python
        if order.reduce_only:
            params["reduceOnly"] = "true"
```
```python
                order = Order(
                    symbol=symbol, side=side, order_type=OrderType.MARKET,
                    quantity=abs(amt), reduce_only=True,
                    client_order_id=self.new_client_order_id("bs_close"),
                )
```
No existe ninguna llamada a `/fapi/v1/positionSide/dual` en el repo (sólo aparece
como string en `_IDEMPOTENT_POST_PATHS:245`), ni se envía `positionSide` en ningún sitio.

**Por qué**: la doc oficial de `POST /fapi/v1/order` dice literalmente
`reduceOnly` → *"Cannot be sent in Hedge Mode"* y `positionSide` → *"Default BOTH
for One-way Mode; LONG or SHORT for Hedge Mode. **It must be sent in Hedge Mode**"*.
Si la cuenta está en Hedge (se activa con dos clics en la web de Binance, y es un
estado persistente de la cuenta que el bot ni consulta ni fuerza) entonces **todas**
las órdenes fallan con `-4061 POSITION_SIDE_NOT_MATCH`: entradas, SL, TP, cierre de
emergencia y `close_all_positions`. El fix P0-03 se vuelve un no-op silencioso
justo en el escenario que debía cubrir. Además `/fapi/v2/positionRisk` devuelve
**dos filas por símbolo** en Hedge (LONG y SHORT), y el filtro
`float(positionAmt) != 0` las procesaría como dos posiciones independientes con
`reduceOnly` prohibido.

**Fix**: en el arranque live, `GET /fapi/v1/positionSide/dual`; si
`dualSidePosition == true`, o bien forzar one-way con el POST correspondiente (sólo
posible sin posiciones ni órdenes abiertas) o **abortar el arranque** con log
CRITICAL. Alternativamente soportar hedge de verdad: enviar `positionSide` y omitir
`reduceOnly`.

**Verificado como**: doc oficial `POST /fapi/v1/order` (parámetros `reduceOnly` y
`positionSide`) + `grep -rn "positionSide\|dualSidePosition"` en `exchange/ execution/ main.py`
→ 1 sola coincidencia, la del set de paths idempotentes.

---

### [P2] fix_exchange-07 — `DEFAULT_SYMBOL_FILTERS["BTCUSDT"]["minNotional"] = 100` es FALSO (el real es 50) y el comentario afirma que son "los filtros reales observados"

**Archivo**: `exchange/binance_client.py:61`

**Evidencia**:
```python
# Safe fallback when GET /fapi/v1/exchangeInfo cannot be loaded. Values are
# the real USDT-M filters observed on 2026-08-29 (see tasks/audit/02). They are
DEFAULT_SYMBOL_FILTERS: Dict[str, Dict[str, Decimal]] = {
    "BTCUSDT": {"tickSize": Decimal("0.1"), "stepSize": Decimal("0.001"),
                "minQty": Decimal("0.001"), "minNotional": Decimal("100")},
```

**Por qué**: el `MIN_NOTIONAL` real de BTCUSDT hoy es `50`. El propio informe de
la ronda 1 (`tasks/audit/02_exchange_execution.md:317`) ya decía "BTC 50, ETH 20",
así que el valor se metió mal al escribir el fix. Con `$1.000` de capital y
sizing por volatilidad, un notional de BTC entre 50 y 100 USDT es un rango de
tamaños perfectamente normal: `_normalize_order_params` lanza `ValueError` y el
engine registra `order_failed` — la señal se pierde **en silencio y por un motivo
inventado**. Es "conservador" sólo en apariencia: destruye señales válidas. Y el
comentario "*the real USDT-M filters observed on 2026-08-29*" convierte un dato
erróneo en una afirmación verificada, que es peor que no tener comentario.

**Fix**: `Decimal("50")`, y añadir un test que compare `DEFAULT_SYMBOL_FILTERS`
contra un `exchangeInfo` fijado en `tests/fixtures/` para que la divergencia salte
sola. Idealmente cachear el último `exchangeInfo` bueno en disco y usar eso como
fallback en vez de constantes escritas a mano.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex1.py
  BTCUSDT  minNotional  default=100        live=50         MISMATCH
  (ETH 20, SOL 5, ADA 5 y los 12 valores de tick/step/minQty: OK)
```

---

### [P2] fix_exchange-08 — `_price_rounding_mode` coloca el precio límite al lado EQUIVOCADO del trigger en STOP_LIMIT / TAKE_PROFIT_LIMIT

**Archivo**: `exchange/binance_client.py:442`

**Evidencia**:
```python
    @staticmethod
    def _price_rounding_mode(order: Order, is_trigger: bool) -> str:
        if is_trigger:
            return "floor" if order.side == Side.SELL else "ceil"
        return "floor" if order.side == Side.BUY else "ceil"
```

**Por qué**: para una misma orden, `price` y `stopPrice` se redondean con criterios
OPUESTOS. En un stop-limit de venta (SL de un largo) con `price == stopPrice`, el
trigger baja al tick inferior y el límite sube al superior → el límite queda **por
encima** del trigger. Cuando el precio cae y dispara el stop, la orden se convierte
en un LIMIT de venta por encima del mercado: no se ejecuta hasta que el precio
suba. En una caída rápida no sube nunca → **el stop no protege**. Simétrico en
compra. Hoy es latente porque el engine sólo construye `OrderType.STOP` /
`TAKE_PROFIT` (STOP_MARKET / TAKE_PROFIT_MARKET), pero la lógica está escrita para
un tipo de orden que el propio `ORDER_TYPE_MAP` soporta y que cualquiera puede
activar.

**Fix**: el modo de redondeo debe depender del ROL del precio, no sólo del lado:
en un stop-limit, `price` debe redondearse en la MISMA dirección que `stopPrice`
(o mejor, aplicar un offset explícito de N ticks en el sentido de ejecución) y
después validar `price <= stopPrice` para SELL y `price >= stopPrice` para BUY.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex2.py    (### B, filtros vivos de BTCUSDT, tick=0.10)
  SELL STOP_LIMIT intended price=stop=77850.05 -> {'price': '77850.1', 'stopPrice': '77850'}
  limit ABOVE trigger? True
  BUY  STOP_LIMIT intended price=stop=77850.05 -> {'price': '77850', 'stopPrice': '77850.1'}
  limit BELOW trigger? True
```

---

### [P2] fix_exchange-09 — Cerrar ANTES de cancelar expone el flatten a `-2022` ("conflicts with existing open orders")

**Archivo**: `exchange/binance_client.py:729`; `main.py:875`

**Evidencia**:
```python
    async def close_all_positions(self, max_attempts: int = 3) -> Dict[str, Any]:
        """Flatten every open position with MARKET reduceOnly orders (audit P0-03).
```
```python
        result = await self.execution_engine.close_all_positions()
        ...
        await self.execution_engine.cancel_all()
```

**Por qué**: el orden (cerrar → cancelar) es correcto en su intención — evita la
ventana desnuda que denunciaba `02-03`. Pero la doc oficial define
`-2022 REDUCE_ONLY_REJECT` como *"the new reduce-only order conflicts with existing
open orders; **cancel the existing order**"*. Es decir: mandar un MARKET reduceOnly
mientras las SL/TP reduceOnly siguen vivas es precisamente el caso que el exchange
documenta como rechazable. Si ocurre, `close_all_positions` acumula el error, sale
con `remaining` no vacío, y `_flatten_all` ejecuta igualmente `cancel_all()` a
continuación (`main.py:875` no está condicionado a `remaining`): posición abierta +
SL/TP borrados = exactamente la exposición desnuda que el P0-03 quería eliminar.

No he podido comprobarlo empíricamente (cuenta cerrada, y el testnet no reproduce
el estado de reduceOnly de forma fiable), así que lo doy como **riesgo documentado,
no como fallo observado**.

**Fix**: por símbolo, `DELETE /fapi/v1/allOpenOrders` → inmediatamente
`MARKET reduceOnly` → verificar `positionRisk`. Y en `_flatten_all`, **no ejecutar
`cancel_all()` si `remaining` no está vacío**: es preferible dejar las protectivas
vivas y alertar que quedarse sin ninguna. Alternativa más limpia: `closePosition=true`
sobre STOP_MARKET, que Binance gestiona como cierre total sin `quantity`.

**Verificado como**: doc oficial de códigos de error (`-2022`) + lectura de
`main.py:856-875` (la llamada a `cancel_all()` es incondicional).

---

### [P2] fix_exchange-10 — `parse_symbol_filters` ignora `MARKET_LOT_SIZE`, `PERCENT_PRICE`, `maxQty` y `minPrice/maxPrice`

**Archivo**: `exchange/binance_client.py:115`

**Evidencia**:
```python
        for flt in s.get("filters", []) or []:
            ft = flt.get("filterType")
            try:
                if ft == "PRICE_FILTER":
                    f["tickSize"] = Decimal(str(flt.get("tickSize")))
                elif ft == "LOT_SIZE":
                    f["stepSize"] = Decimal(str(flt.get("stepSize")))
                    f["minQty"] = Decimal(str(flt.get("minQty")))
                elif ft == "MIN_NOTIONAL":
                    f["minNotional"] = Decimal(str(flt.get("notional")))
```

**Por qué**: BTCUSDT declara hoy 7 filtros y el parser lee 3. Los ignorados son
`MARKET_LOT_SIZE` (para BTC `maxQty=120` frente a los `1000` de `LOT_SIZE`: las
MARKET tienen su propio límite), `PERCENT_PRICE`
(`multiplierUp=1.05 / multiplierDown=0.95` sobre el mark price — un SL o un TP
colocado a más de un 5 % del mark se rechaza), `MAX_NUM_ORDERS` (200) y los
`minPrice/maxPrice` del `PRICE_FILTER`. Con `$1.000` de capital el `maxQty` es
irrelevante, pero `PERCENT_PRICE` **sí** es alcanzable con un TP amplio o un SL de
varias ATR en un régimen volátil, y el rechazo llega como excepción genérica
(`order_failed`) sin diagnóstico. La promesa del fix ("las órdenes ya no se
rechazan por filtros") es por tanto parcial.

**Fix**: parsear los 4 filtros restantes y validarlos localmente con un mensaje
explícito por filtro. `PERCENT_PRICE` exige el mark price, que ya llega por el
stream `@markPrice@1s`.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex1.py
  present: ['PRICE_FILTER','LOT_SIZE','MARKET_LOT_SIZE','MAX_NUM_ORDERS','MIN_NOTIONAL','PERCENT_PRICE','POSITION_RISK_CONTROL']
  IGNORED: ['MARKET_LOT_SIZE', 'MAX_NUM_ORDERS', 'PERCENT_PRICE', 'POSITION_RISK_CONTROL']
$ py -3.12 scratchpad/v_ex2.py   (### D)
  BTC MARKET qty=500 (MARKET_LOT_SIZE.maxQty=120) -> {'quantity': '500'}   # pasa el check local
```

---

### [P2] fix_exchange-11 — `paper` + `--testnet` mezcla REST de testnet con WebSocket de mainnet; el comentario que dice lo contrario sólo aplica a Strike

**Archivo**: `main.py:63` y `main.py:74`

**Evidencia**:
```python
        # Paper trading with Strike: forzar URLs de MAINNET para datos reales
        if self.paper and self._venue == ExchangeVenue.STRIKE:
            settings.api_price_url = "https://api.strikefinance.org/price"
            ...
        if self._venue == ExchangeVenue.BINANCE:
            self.client = BinanceClient(settings)          # <-- usa settings.use_testnet
            self.websocket = BinanceWebSocket(
                symbols=settings.symbol_names,
                use_testnet=settings.use_testnet and not self.paper,   # <-- mainnet en paper
            )
```
y `main.py:156`: `# Aplicar testnet si corresponde (paper ya forzo MAINNET en __init__)`.

**Por qué**: el forzado a mainnet está condicionado a `ExchangeVenue.STRIKE`; para
Binance no ocurre. Con `py main.py --paper --binance --testnet`, el cliente REST
apunta a `testnet.binancefuture.com` (klines de seed, `depth`, `openInterest`,
`ticker/24hr`) mientras el WebSocket apunta a `fstream.binance.com` (trades y book
reales). Los precios de testnet están cerca pero el **volumen es ficticio**:
verificado en la misma vela de 1 m, mainnet `54.044 BTC / 2.714 trades` frente a
testnet `8.3712 BTC / 101 trades`. Cualquier métrica de liquidez, VPIN, Kyle lambda
u OI queda envenenada, y las barras del seed no casan con las barras construidas
desde el WS. El bridge sí fuerza `use_testnet = False` en paper
(`server/bridge.py:308`), así que **el despliegue actual de CT 104 no está
afectado**; el agujero es del CLI, y el comentario de `main.py:156` afirma una
protección que no existe.

**Fix**: mover el forzado de mainnet fuera del `if venue == STRIKE`: en paper,
mainnet siempre, para todos los venues. Y corregir el comentario.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex4.py   (### I)
  BinanceClient(settings)._base_url  = https://testnet.binancefuture.com
  BinanceWebSocket url base          = wss://fstream.binance.com/stream
  -> MISMATCH: True
$ curl .../fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=1
  MAINNET: ...,"77855.90","54.044",...,2714,...
  TESTNET: ...,"77872.30","8.3712",...,101,...
```

---

### [P2] fix_exchange-12 — `BinanceWebSocket.stop()` no resetea `_connected`: `/api/health` sigue diciendo `ws_connected: true` con el WS parado

**Archivo**: `exchange/binance_ws.py:297`

**Evidencia**:
```python
    async def stop(self) -> None:
        """Detiene la conexión."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("binance_ws_stopped")
```
`_connected` sólo pasa a `False` en el `except` de reconexión (`:114`).

**Por qué**: el bridge lee ese atributo tal cual
(`server/bridge.py:1038: bool(getattr(..., "_connected", False))`) para
`/api/health` y para el watchdog (`:1153`). Tras un `stop()` limpio el flag se
queda en `True`, así que la salud reporta un feed vivo que no existe. Esto
contradice directamente el fix de la ronda 1 en el bridge ("health real"): el
health es tan real como el atributo que lee, y este miente. Es la misma clase de
problema que `02-09` ("`_connected` falso"), que sigue abierto por el otro lado.

**Fix**: `self._connected = False` como primera línea de `stop()`, y en un `finally`
al salir del `async with websockets.connect(...)` para cubrir también las salidas
no excepcionales.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex4.py   (### H)
  despues de stop():  _running = False  _connected = True
  bridge.py:1038 -> ws_connected = True
```

---

### [P2] fix_exchange-13 — `batch_orders` no reenvía (correcto) pero se traga el error y devuelve `{"orders": []}`: hasta 5 órdenes vivas invisibles para el engine

**Archivo**: `exchange/binance_client.py:952`

**Evidencia**:
```python
            try:
                # Non-idempotent POST: no blind retry (P0-02). A batch cannot be
                # reconciled atomically, so a timeout/5xx surfaces as an error.
                result = await self._auth_post("/fapi/v1/batchOrders", params)
                if isinstance(result, list):
                    all_results.extend(result)
            except Exception as e:
                logger.error("binance_batch_failed", error=str(e))
```

**Por qué**: la mitad buena está bien: sin `recover_fn`, `_recover_or_raise` lanza
al primer error y **no hay doble envío** — el riesgo que buscaba el foco de esta
auditoría no existe. El problema es el `except` de arriba: tras un timeout, el
lote puede haber llegado y hasta 5 LIMIT post-only pueden estar vivas en el libro,
pero la función devuelve `{"orders": []}` y el llamante concluye "no se colocó
nada". Nadie las cancela ni las trackea; quedan como órdenes zombi que pueden
llenarse y abrir posición sin SL/TP. El `logger.error` es la única traza. Además
la lista `all_results` sólo se rellena si el resultado es una lista, y el batch de
Binance devuelve elementos de error `{"code":..., "msg":...}` mezclados con
órdenes buenas: `_normalize` (`:960-966`) los convierte en `{"orderId": "",
"status": "NEW"}`, es decir, **errores presentados como órdenes aceptadas**.

**Fix**: propagar el error (o devolver un estado `unknown` explícito) para que el
llamante dispare una reconciliación con `GET /fapi/v1/openOrders` por símbolo; y
en la normalización, distinguir los elementos con `code` negativo y reportarlos
como fallos en vez de como `NEW`.

**Verificado como**: lectura del código; el test existente
`tests/test_p0_round2.py:592 test_batch_orders_not_resent_on_timeout` confirma el
no-reenvío pero no comprueba qué se devuelve al llamante.

---

### [P2] fix_exchange-14 — `place_bracket_order` quedó FUERA del fix: no usa `new_client_order_id`, no espera el fill y coloca `reduceOnly` sobre una entrada con estado `NEW`

**Archivo**: `exchange/binance_client.py:791`

**Evidencia**:
```python
        status = result.get("status", "")
        if status not in ("FILLED", "PARTIALLY_FILLED", "NEW"):
            return result
        filled_qty = float(result.get("executedQty", 0))
        qty = filled_qty if filled_qty > 0 else order.quantity
        ...
            client_order_id=f"bs_sl_{uuid.uuid4().hex[:8]}",
```

**Por qué**: el engine (`execution/order_engine.py:218`) sí recibió el fix
completo (`_await_fill` + `_place_with_retry` + reintento de `-2022`), pero este
camino paralelo del cliente conservó la lógica vieja: (1) acepta `NEW` y coloca
SL/TP `reduceOnly` cuando **no hay posición** → `-2022` garantizado en los dos, y
el `logger.critical("BRACKET_BOTH_PROTECTIVES_FAILED")` sin cierre de emergencia;
(2) cuando `executedQty` es 0 dimensiona las protectivas con `order.quantity`, la
cantidad SIN redondear; (3) reintenta con el MISMO `client_order_id`, así que si el
primer intento sí colocó la orden pero se perdió la respuesta, el segundo choca con
el id duplicado y se registra un fallo falso; (4) no usa `new_client_order_id()`,
que es la función que la ronda 1 introdujo justo para esto. Hoy no lo llama nadie
en `execution/`, pero es código público del cliente que aparenta estar arreglado.

**Fix**: o se borra, o se reescribe delegando en el mismo `_await_fill` /
`_place_with_retry` del engine. Un camino de colocación de protectivas es
suficiente; dos que divergen es una trampa.

**Verificado como**: lectura de `binance_client.py:791-853` frente a
`execution/order_engine.py:209-316`; `grep -rn "place_bracket_order"` en
`execution/ main.py server/` → 0 llamadas.

---

### [P2] fix_exchange-15 — `_RateLimiter` cuenta PETICIONES (1200/min) cuando el límite real es de PESO (2400/min); `positionRisk`/`account` pesan 10

**Archivo**: `exchange/binance_client.py:148` y `:182`

**Evidencia**:
```python
class _RateLimiter:
    """Token bucket rate limiter — 1200 req/min para Binance Futures."""
...
        self._rate_limiter = _RateLimiter(max_requests=1200, window_sec=60.0)
```
`rateLimits` real de `exchangeInfo` de hoy:
```
[{'rateLimitType': 'REQUEST_WEIGHT', 'interval': 'MINUTE', 'limit': 2400},
 {'rateLimitType': 'ORDERS', 'interval': 'MINUTE', 'limit': 1200},
 {'rateLimitType': 'ORDERS', 'interval': 'SECOND', 'intervalNum': 10, 'limit': 300}]
```

**Por qué**: el 1200 del código es el límite de **ÓRDENES**, no el de peso; el
comentario lo etiqueta mal. El limitador no distingue endpoints: 1200 llamadas a
`/fapi/v2/positionRisk` (peso 10 desde 2024-09-03) son 12.000 de peso, 5× por
encima del límite de 2400 → 429 → y de ahí al ciclo de amplificación del
`fix_exchange-02`. Tampoco lee `X-MBX-USED-WEIGHT-1m`, que es el único dato
autoritativo. `02-07` ("limitador sin weight") sigue abierto tal cual.

**Fix**: tabla de pesos por path, contabilizar peso en vez de peticiones con
límite 2400/min, un contador aparte para ÓRDENES (1200/min y 300/10 s), y ajustar
el contador con el header `X-MBX-USED-WEIGHT-1m` de cada respuesta.

**Verificado como**: `rateLimits` extraídos del `exchangeInfo` vivo (arriba) +
changelog oficial 2024-08-07: *"the following endpoints IP weight limit will be
adjusted from 2024-09-03 … GET /fapi/v2/positionRisk (5→10)"*.

---

### [P3] fix_exchange-16 — El cliente usa `/fapi/v2/{account,balance,positionRisk}`, deprecados desde 2024-07-24 (`02-22` sigue abierto)

**Archivo**: `exchange/binance_client.py:603`, `:606`, `:615`

**Evidencia**:
```python
    async def get_account(self) -> Dict:
        return await self._auth_get("/fapi/v2/account")
...
        data = await self._auth_get("/fapi/v2/positionRisk", params)
```

**Por qué**: changelog oficial (2024-07-24): *"The following endpoints will be
deprecated in the coming months (exact date to be announced later)"* →
`GET /fapi/v2/balance`, `/fapi/v2/account`, `/fapi/v2/positionRisk`, con `v3` ya
disponible. No hay fecha de apagado anunciada, así que hoy no rompe nada; pero
`close_all_positions` — el mecanismo de última línea contra posiciones desnudas —
cuelga de un endpoint deprecado. Deuda a saldar antes de cualquier live.

**Fix**: migrar a `/fapi/v3/positionRisk`, `/fapi/v3/account`, `/fapi/v3/balance`
verificando el mapeo de campos (`v3` cambia algunos nombres).

**Verificado como**: `WebFetch` del changelog oficial USDⓈ-M.

---

### [P3] fix_exchange-17 — `get_order` devuelve `None` también cuando la respuesta no es un dict, y eso significa "reenvía"

**Archivo**: `exchange/binance_client.py:665`

**Evidencia**:
```python
        if not isinstance(result, dict):
            return None
        result["symbol"] = symbol
        return result
```

**Por qué**: en el contrato de `_recover_or_raise`, `None` significa exactamente
una cosa: *"el exchange confirma que la petición NO llegó → se puede reenviar"*.
Confundir "Binance respondió `-2013`" con "la respuesta no tenía la forma esperada"
convierte un fallo de parseo en una autorización a mandar otra orden a mercado.

**Fix**: `get_order` debe distinguir tres estados (`FOUND` / `NOT_FOUND` /
`UNKNOWN`) y `_recover_or_raise` sólo debe permitir el reenvío con `NOT_FOUND`.

**Verificado como**: lectura de `binance_client.py:659-668` y `:318-324`.

---

### [P3] fix_exchange-18 — `settings.binance_testnet` es una bandera muerta y su comentario afirma una protección inexistente

**Archivo**: `config/settings.py:323`

**Evidencia**:
```python
            elif self.is_binance:
                # Binance Futures testnet — important: prevents trading mainnet
                # when use_testnet=True. BinanceClient reads these in __init__.
                self.binance_testnet = True  # Flag for BinanceClient to use testnet URLs
```

**Por qué**: `BinanceClient` no lee `binance_testnet` en ningún sitio (usa
`settings.use_testnet`), y además `apply_testnet()` se invoca en `start()`
(`main.py:158`), **después** de que el cliente se haya construido en `__init__`.
La bandera no hace nada y el comentario describe un mecanismo que no existe.

**Fix**: borrar el atributo y el comentario, o hacer que `BinanceClient` resuelva
la URL base de forma perezosa en `_get_session()`.

**Verificado como**: `grep -rn "binance_testnet"` → 1 escritura, 0 lecturas.

---

### [P3] fix_exchange-19 — Cada mensaje de depth se emite dos veces (`depth` y `depthUpdate`) y `main.py` registra el mismo handler en ambos: todo se procesa por duplicado

**Archivo**: `exchange/binance_ws.py:155`; `main.py:333`

**Evidencia**:
```python
            await self._emit("depth", depth_data)
            await self._emit("depthUpdate", depth_data)
```
```python
        self.websocket.on("depth", on_depth_update)
        self.websocket.on("depthUpdate", on_depth_update)
```

**Por qué**: con 4 símbolos a `@depth20@100ms` son 40 mensajes/s que producen 80
construcciones de `OrderBook` (20 `OrderBookLevel` cada una) por segundo, la mitad
tiradas a la basura. Lo mismo con `markPrice`/`markPriceUpdate` (`main.py:408-409`).
No corrompe nada — `market_data.on_orderbook` sólo asigna, es idempotente — así que
el impacto es CPU desperdiciada en un LXC, no un error de datos. Lo señalo porque
es una trampa: en cuanto un consumidor **acumule** (OFI, contador de updates,
Hawkes sobre eventos de libro) el doble conteo pasa a ser silencioso y sistemático.

**Fix**: emitir un único nombre de evento y registrar un único handler.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex4.py   (### G)
  1 mensaje de depth -> el handler de main.py se ejecuta 2 veces
```

---

### [P3] fix_exchange-20 — El stream `@kline_1m` se suscribe y se parsea para nadie

**Archivo**: `exchange/binance_ws.py:76` y `:178`

**Evidencia**:
```python
            streams.append(f"{binance_sym}@kline_1m")
...
            await self._emit("kline", kline_data)
            await self._emit("kline_1m", kline_data)
```

**Por qué**: `grep -rn '\.on("kline'` en `main.py server/ core/` no devuelve nada.
Se recibe, se convierte a dict y se descarta. Son 4 streams de más y trabajo por
mensaje inútil; más relevante, es la señal de que el bot construye sus barras
agregando ticks del stream `@trade` en vez de usar las velas oficiales del
exchange — lo que explica el `bp-06` de la tanda 1 (minuto duplicado en la costura
del seed). Consumir `kline_1m` con `x == true` sería más barato y eliminaría por
completo esa clase de desajuste.

**Fix**: o quitar la suscripción, o usarla como fuente de barras cerradas.

**Verificado como**: `grep -rn '\.on("kline\|"kline_1m"' main.py server/ core/` → 0 resultados.

---

### [P3] fix_exchange-21 — El comentario del `for/else` de `close_all_positions` describe una condición que no es la del lenguaje

**Archivo**: `exchange/binance_client.py:777`

**Evidencia**:
```python
        else:
            # Loop exhausted without a clean read -> report whatever is still open
```

**Por qué**: el `else` de un `for` se ejecuta siempre que el bucle termina sin
`break`, es decir, siempre que **quedaron posiciones abiertas tras `max_attempts`**
— tanto si las lecturas fueron limpias como si no. El comportamiento del código es
correcto (re-lee y reporta); el comentario induce a error a quien lo mantenga.

**Fix**: "Se agotaron los intentos y aún quedaban posiciones -> re-leer y reportar".

**Verificado como**: lectura y semántica de `for/else` en Python.

---

## Tabla resumen

| ID | Sev | Título | Archivo:línea | Verificación |
|---|---|---|---|---|
| fix_exchange-01 | **P1** | `load_exchange_info` sólo cachea 4 símbolos → `close_all_positions` rechazado con `-1111` fuera de `SYMBOL_MAP` | `exchange/binance_client.py:409` | snippet |
| fix_exchange-02 | **P1** | `recover_fn` corre también en 429/418 → amplifica el ban de IP (rompe el feed público) | `exchange/binance_client.py:275` | snippet + doc |
| fix_exchange-03 | **P1** | Sin deadline global: `place_order` puede tardar minutos con `recover_fn` anidado | `exchange/binance_client.py:268` | snippet + lectura |
| fix_exchange-04 | **P1** | P0-02 no cerrado: 4 POST (no 1) y `clientOrderId` sólo único entre órdenes ABIERTAS | `exchange/binance_client.py:279` | snippet + doc |
| fix_exchange-05 | **P1** | Sin kill-switch en cliente/CLI: `py main.py` sin flags opera en vivo | `main.py:1621` | lectura + grep |
| fix_exchange-06 | **P1** | Hedge mode ignorado; `reduceOnly` prohibido y `positionSide` obligatorio en hedge (`02-06` abierto) | `exchange/binance_client.py:707` | doc + grep |
| fix_exchange-07 | P2 | `minNotional` de BTC = 100, el real es 50; el comentario lo llama "valor real observado" | `exchange/binance_client.py:61` | exchangeInfo vivo |
| fix_exchange-08 | P2 | STOP_LIMIT: `price` redondeado al lado contrario de `stopPrice` → el stop no llena | `exchange/binance_client.py:442` | snippet |
| fix_exchange-09 | P2 | Cerrar antes de cancelar expone el flatten a `-2022`; `cancel_all()` corre igual | `exchange/binance_client.py:729` | doc + lectura |
| fix_exchange-10 | P2 | `MARKET_LOT_SIZE`, `PERCENT_PRICE`, `maxQty`, `minPrice` sin parsear | `exchange/binance_client.py:115` | snippet |
| fix_exchange-11 | P2 | `paper --testnet`: REST testnet + WS mainnet (volumen ficticio) | `main.py:63` | snippet + curl |
| fix_exchange-12 | P2 | `stop()` no resetea `_connected` → `/api/health` miente | `exchange/binance_ws.py:297` | snippet |
| fix_exchange-13 | P2 | `batch_orders` se traga el error → hasta 5 órdenes zombi; errores del lote como `NEW` | `exchange/binance_client.py:952` | lectura |
| fix_exchange-14 | P2 | `place_bracket_order` quedó sin el fix (acepta `NEW`, cid fijo, sin `_await_fill`) | `exchange/binance_client.py:791` | lectura + grep |
| fix_exchange-15 | P2 | Rate limiter por peticiones (1200) y no por peso (2400 real); ignora `X-MBX-USED-WEIGHT` | `exchange/binance_client.py:148` | exchangeInfo + changelog |
| fix_exchange-16 | P3 | Endpoints `/fapi/v2/*` deprecados desde 2024-07-24 (`02-22` abierto) | `exchange/binance_client.py:615` | changelog |
| fix_exchange-17 | P3 | `get_order` devuelve `None` por respuesta malformada = autorización a reenviar | `exchange/binance_client.py:665` | lectura |
| fix_exchange-18 | P3 | `binance_testnet`: bandera muerta con comentario falso | `config/settings.py:323` | grep |
| fix_exchange-19 | P3 | Doble emisión `depth`/`depthUpdate` + doble registro → todo procesado 2× | `exchange/binance_ws.py:155` | snippet |
| fix_exchange-20 | P3 | `@kline_1m` suscrito y parseado sin ningún consumidor | `exchange/binance_ws.py:76` | grep |
| fix_exchange-21 | P3 | Comentario incorrecto del `for/else` en `close_all_positions` | `exchange/binance_client.py:777` | lectura |

**Totales**: P0 = 0 · P1 = 6 · P2 = 9 · P3 = 6 → **21 hallazgos**.

**Hallazgos de la ronda 1 que siguen abiertos**: `02-06` (hedge mode), `02-07`
(418/Retry-After/weight — y empeorado), `02-14` (sin `recvWindow` ni sync de reloj:
`grep` de `recvWindow` en `exchange/` → 0 resultados), `02-15` (`workingType` /
`priceProtect` ausentes: 0 resultados), `02-20` (fallback de símbolo genera
`XRPUSD`, formato COIN-M), `02-22` (`positionRisk` v2). `02-02` está **parcialmente**
cerrado (ver `fix_exchange-04`); `02-19` (`replace_order`) queda mitigado de rebote
porque el reintento reutiliza el mismo `clientOrderId`.

---

## Veredicto

1. La ronda 1 arregló de verdad lo que dijo arreglar en la parte mecánica:
   redondeo con `Decimal`, `clientOrderId` conforme al regex oficial, `-2013` bien
   elegido, `newOrderRespType=RESULT` y el parseo `b`/`a` del depth — este último
   verificado contra el stream vivo, y correcto también en no gestionar `U/u/pu`
   porque `@depth20@100ms` es un snapshot completo. Es trabajo competente.
2. La **ruta de datos públicos** (prioridad (a)), que es la única que corre hoy,
   está esencialmente sana: WS a mainnet, depth correcto, book poblado. Sus dos
   defectos reales son el manejo de 429/418 (`-02`, `-15`), que puede convertir un
   throttle en un ban de días y dejar al bot ciego, y la mezcla testnet/mainnet del
   CLI (`-11`), que no afecta al despliegue de CT 104 porque el bridge fuerza mainnet.
3. La **ruta de órdenes** (prioridad (b)) está bien cortada en paper: `main.py:608`
   y `_flatten_all` no llegan a `place_order`. Pero la única barrera contra un
   arranque live accidental (`BOTSTRIKE_ALLOW_LIVE`) vive en el bridge, no en el
   cliente: `py main.py` a secas opera en vivo con las claves del `.env` (`-05`).
4. Los tres fixes P0 tienen cada uno un agujero medible, no teórico: el de
   precisión deja fuera cualquier símbolo no mapeado y por tanto el cierre de
   emergencia falla justo donde importa (`-01`); el de idempotencia manda 4 POST
   donde su propio docstring promete 1, y `clientOrderId` sólo es único entre
   órdenes abiertas, así que un MARKET ya llenado no bloquea el duplicado (`-04`);
   el de "no dejar posiciones desnudas" ignora el modo hedge, donde `reduceOnly`
   está prohibido por la propia doc (`-06`).
5. Un dato mal copiado con comentario de autoridad (`minNotional` de BTC = 100
   frente a 50, presentado como "valor real observado", `-07`) es el tipo de error
   que sobrevive a las auditorías porque parece verificado. Descarta señales de BTC
   válidas en silencio con una cuenta de $1.000.
6. Hay dos caminos divergentes para colocar protectivas: el del engine, arreglado,
   y `place_bracket_order`, que se quedó en la versión vieja (`-14`). Dos caminos
   que difieren en seguridad son peor que uno malo, porque uno de ellos aparenta
   estar bien.
7. Nada de lo encontrado es P0 **hoy**, y eso es una consecuencia del contexto, no
   del código: la cuenta está cerrada, las estrategias congeladas y el modo es
   paper. Con las órdenes habilitadas, `-01`, `-04` y `-06` serían P0 sin discusión.
8. Recomendación de orden de trabajo si algún día se reabre una ruta live (Binance
   o cualquier otra): `-05` (kill-switch en el cliente) → `-06` (hedge) → `-01`
   (filtros de todos los símbolos) → `-04` (reconciliar por posición, no por
   orden) → `-02`/`-15` (peso y back-off) → `-09` (cancelar antes de cerrar).
9. Correcciones baratas y de valor inmediato para el estado actual (paper):
   `-07` (un carácter), `-12` (una línea), `-11` (mover un `if`), `-19` y `-20`
   (menos CPU en el LXC). Ninguna toca la ruta de órdenes.
10. Veredicto global del área: **los fixes van en la dirección correcta y el código
    es de buena calidad, pero ninguno de los tres P0 está cerrado del todo, y la
    protección contra un disparo accidental de órdenes está en la capa equivocada.**
    Para datos públicos en paper, apto con las reservas de `-02` y `-15`; para live,
    no.
