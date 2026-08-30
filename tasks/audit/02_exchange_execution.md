# Auditoría 02 — Exchange & Execution (Binance USDT-M Futures / Hyperliquid / Order Engine / Paper Simulator)

**Fecha:** 2026-08-29
**Alcance:** `exchange/binance_client.py`, `exchange/binance_ws.py`, `exchange/hyperliquid_client.py`, `exchange/hyperliquid_ws.py`, `exchange/strike_client.py`, `exchange/websocket_client.py`, `execution/order_engine.py`, `execution/paper_simulator.py`, `execution/smart_router.py`, `execution/slippage.py`, y el uso de estas capas en `main.py` (arranque, fills WS, reconciliación, cierre de emergencia, shutdown).
**Método:** lectura completa del código (sin suponer), grep cruzado, contraste con documentación oficial de Binance USDT-M Futures y Hyperliquid (WebFetch), **verificación empírica** (conexión real al WS de futuros, `GET /fapi/v1/exchangeInfo` real, simulación del formateo de órdenes, código fuente oficial del SDK de Hyperliquid, `curl` a testnet/demo), y ejecución de la suite de tests (`36 passed`).
**Estado:** COMPLETADA.

Notas de verificación:
- La doc de developers.binance.com es una SPA; varias páginas devolvieron la home a WebFetch/curl. Donde no se pudo leer la doc, se verificó **contra el exchange real** (WS/REST públicos) y se indica explícitamente.
- Tests: `uv run --python 3.11 --with pytest ... python -m pytest tests -q` → **36 passed in 3.15s**. Ningún test cubre precisión/formateo de órdenes ni el parser del WS (`grep stepSize|tickSize|place_order tests/` → 0).
- Secretos: `.env` NO está versionado (`git ls-files .env` vacío; `.gitignore` lo excluye). Ningún log imprime API key/secret/private key (HL loguea `wallet[:10]`). Sin hallazgo.

---

## Verificación de lecciones históricas (`tasks/lessons.md`)

| Lección | Estado hoy | Evidencia |
|---|---|---|
| WS spot (`stream.binance.com:9443`) vs futures | **Arreglado** | `exchange/binance_ws.py:25-26` usa `wss://fstream.binance.com/{ws,stream}`; REST `fapi.binance.com` (`binance_client.py:32`) |
| 3 `SYMBOL_MAP` distintos, SOL ausente en WS | **Arreglado** | `binance_ws.py:35-36` importa el mapa de `binance_client.py:36-42` (único origen; SOL incluido) |
| listenKey: POST + PUT cada 30 min | **Parcial** | keepalive cada 1800 s (`binance_ws.py:224`) OK, pero URL mainnet fija en testnet y `listenKeyExpired` sin manejar (ver P1-08) |
| Protectivas fire-and-forget → cierre de emergencia | **Presente pero defectuoso** | `order_engine.py:266-284` — ver P1-05 (carrera -2022) |
| "SL/TP solo en FILLED/PARTIALLY_FILLED" (lección 2026-03) | **REVERTIDO** | `order_engine.py:195-206` coloca protectivas sin mirar el status ("CRITICAL FIX: Don't gate on status") |
| `cancel_order` con `orderId` camelCase | **Arreglado** | `binance_client.py:487-489` |
| Timeout aiohttp | **Arreglado** | `binance_client.py:116` `ClientTimeout(total=15, connect=5, sock_read=10)` |
| Rate limiter con re-check tras sleep | **Arreglado** | `binance_client.py:81-92` (while loop) |
| Reconciliación de posiciones por REST | **Parcial** | `main.py:709-731` sincroniza posiciones cada 2 s; NO verifica que tengan SL/TP (ver P0-03) |
| Funding rate desde WS `markPrice` | **Arreglado** | `binance_ws.py:177-189` + `main.py:382-405` |

---

## Hallazgos

### [P0] 01 — Cantidades y precios se envían sin respetar `stepSize`/`tickSize` de exchangeInfo → órdenes rechazadas (-1111 / -4014) en BTC, SOL y ADA
**Archivo:** `exchange/binance_client.py:374`, `:379`, `:387`, `:392`; `:554`, `:557`
**Evidencia:**
```python
"quantity": f"{order.quantity:.6f}".rstrip("0").rstrip("."),
...
params["price"] = f"{order.price:.2f}"
...
params["stopPrice"] = f"{order.stop_price:.2f}"
```
`get_exchange_info()` existe (`:241-242`) pero **nadie lo llama** (`grep -rn get_exchange_info` → solo la definición). No hay redondeo hacia abajo a `stepSize` ni a `tickSize`, ni comprobación de `MIN_NOTIONAL`.

Filtros reales (`GET https://fapi.binance.com/fapi/v1/exchangeInfo`, 2026-08-29):

| Símbolo | tickSize | stepSize | minQty | minNotional |
|---|---|---|---|---|
| BTCUSDT | 0.10 | 0.001 | 0.001 | 50 |
| ETHUSDT | 0.01 | 0.001 | 0.001 | 20 |
| SOLUSDT | 0.0100 | 0.01 | 0.01 | 5 |
| ADAUSDT | 0.00010 | **1** (entero) | 1 | 5 |

Simulación del formateo del cliente con tamaños reales de la config (`max_position_usd`):
```
BTCUSDT: qty=0.007692 (step 0.001) INVÁLIDO | price=65432.15 (tick 0.10) INVÁLIDO
ETHUSDT: qty=0.125                  VÁLIDO  | price=3210.12  VÁLIDO
SOLUSDT: qty=1.666667 (step 0.01)  INVÁLIDO | price OK
ADAUSDT: qty=428.571429 (step 1)   INVÁLIDO | SL 0.3487 → "0.35" = desviación de 37.3 bps del nivel de stop
```
**Por qué es un problema:** En LIVE, la orden de entrada de BTC/SOL/ADA es rechazada con `-1111 Precision is over the maximum defined for this asset` (o `-4014 Price not increased by tick size` para límites/stops de BTC con céntimos impares). `execute_signal` captura la excepción, loguea `order_failed` y devuelve `None` → el bot **no opera 3 de 4 símbolos**, y en ETH opera con SL/TP cuyo `stopPrice` se redondea arbitrariamente. Peor: las órdenes de **salida** (`reduce_only` MARKET, `order_engine.py:146-157`) y las de **emergencia** (`:270-280`) sufren el mismo rechazo → una posición abierta (manual, de una versión previa o parcialmente llenada) **no puede cerrarse desde el bot**. En ADA el stop se desplaza 37 bps: con SL de ~50-100 bps es 40-70 % del riesgo definido.
**Fix propuesto:**
1. Al arrancar, `GET /fapi/v1/exchangeInfo` y cachear por símbolo `tickSize`, `stepSize`, `minQty`, `minNotional`, `pricePrecision`, `quantityPrecision`.
2. En `place_order`/`batch_orders`: `qty = floor(qty/stepSize)*stepSize` con `Decimal`; `price` redondeado a `tickSize` **hacia el lado conservador** para SL/TP (SL de un long hacia abajo, SL de un short hacia arriba); formatear con `f"{Decimal:f}"` (sin notación científica).
3. Rechazar localmente si `qty < minQty` o `qty*price < minNotional` (salvo `reduceOnly`) con ERROR + notificación.
4. Test unitario con los 4 filtros reales de arriba.
**Verificado cómo:** leído; **ejecutado** (`curl` exchangeInfo real + script de simulación del formateo); doc oficial de códigos de error https://developers.binance.com/docs/derivatives/usds-margined-futures/error-code (-1111, -4014, -4164).

---

### [P0] 02 — Reintentos automáticos de `POST /fapi/v1/order` tras timeout/5xx → ORDEN DUPLICADA (posición doble o cuádruple)
**Archivo:** `exchange/binance_client.py:157-185` (`_retry_request`), `:212-220` (`_auth_post`), `:408` (`place_order`)
**Evidencia:**
```python
async def _retry_request(self, request_fn, path: str) -> Any:
    for attempt in range(self._MAX_RETRIES + 1):          # hasta 4 envíos
        try:
            return await request_fn()
        except BinanceAPIError as e:
            if not e.is_retryable or attempt == self._MAX_RETRIES:   # 429/418/5xx → reintenta
                raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:    # timeout → reintenta
            ...
```
`_auth_post` envuelve **todos** los POST, incluido `/fapi/v1/order`. Se reenvía el mismo `newClientOrderId`, pero Binance solo garantiza unicidad "among **open** orders".
**Por qué es un problema:** La doc oficial dice que ante 503 "*Request accepted but no response before timeout; execution may have succeeded*" y que los 5xx tienen estado de ejecución **desconocido**. Un `sock_read` timeout (10 s) o un 5xx tras una MARKET ya ejecutada provoca un segundo MARKET → **posición al doble (hasta ×4 con 3 reintentos)** con SL/TP dimensionados para el tamaño original. Como la MARKET ya no está "open", el `newClientOrderId` repetido **no** bloquea el duplicado. Con 5x y $1.000, duplicar una posición de $500 rompe todos los límites de exposición y el circuit breaker no lo ve (P1-10).
**Fix propuesto:**
- Para endpoints con efecto secundario (`/fapi/v1/order`, `/fapi/v1/batchOrders`, DELETE, `/leverage`): **0 reintentos ciegos**. Ante timeout/5xx: `GET /fapi/v1/order?symbol=&origClientOrderId=<cid>`; si existe → usar ese resultado; si `-2013 Order does not exist` → reenviar con el **mismo** `cid`.
- Mantener reintentos solo para GET idempotentes.
- Generar `clientOrderId` siempre (hoy es opcional: `if order.client_order_id`).
**Verificado cómo:** leído; doc oficial https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info (5XX / 503 "execution may have succeeded") y New-Order (`newClientOrderId`: "A unique id among open orders").

---

### [P0] 03 — `cancel_all()` en el corte por drawdown y en `shutdown()` elimina SL/TP dejando posiciones DESNUDAS; al reiniciar nadie las re-protege
**Archivo:** `main.py:755-756`, `main.py:825-826`; `execution/order_engine.py:453-460`; `exchange/binance_client.py:491-504`; `main.py:709-731`
**Evidencia:**
```python
# main.py:743-756 — corte por max drawdown (se repite cada 2 s mientras dure)
if self.risk_manager.current_drawdown_pct >= self.settings.trading.max_drawdown_pct:
    ...
    if not self.dry_run and not self.paper:
        await self.execution_engine.cancel_all()        # DELETE /fapi/v1/allOpenOrders por símbolo
# main.py:821-828 — shutdown
self._running = False
if not self.dry_run and not self.paper:
    await self.execution_engine.cancel_all()            # cancela SL y TP, NO cierra posiciones
```
No existe `close_all_positions()` ni uso de `closePosition` (`grep -rn "close_all\|closePosition\|close_position" exchange/ execution/ main.py` → 0). En el arranque (`main.py:709-731`) las posiciones existentes se cargan en `_positions`/risk manager pero **no se comprueba si tienen SL/TP** ni se colocan.
**Por qué es un problema:** Justo cuando el sistema decide "parar" (drawdown ≥10 %) o el operador reinicia/actualiza (`systemctl restart`, deploy, crash+restart), las posiciones abiertas quedan **sin stop en el exchange**. Mientras el drawdown siga alto, `cancel_all()` se re-ejecuta cada 2 s: ni un SL puesto a mano sobrevive. Tras reiniciar, el bucle de riesgo ve la posición pero nadie le pone protección. Con 5x, un gap del 10 % = 50 % de la cuenta.
**Fix propuesto:**
1. `BinanceClient.close_all_positions()`: `GET /fapi/v2/positionRisk` → por cada `positionAmt != 0`, MARKET `reduceOnly` con `quantity=abs(positionAmt)` redondeada a `stepSize`. Usarlo en el corte por drawdown **antes** de cancelar órdenes.
2. `shutdown()`: no cancelar SL/TP (dejar la protección viva) o cerrar posiciones si la política es "flat al apagar". Nunca cancelar sin cerrar.
3. Arranque: reconciliación de protección — para cada posición sin `STOP_MARKET reduceOnly` en `openOrders`, colocar SL/TP (parámetros de la estrategia) o cerrar y notificar.
4. Comprobación periódica "toda posición tiene SL" en `_risk_monitor_loop` (ya tiene posiciones y `openOrders` vía `reconcile_orders_with_exchange`).
**Verificado cómo:** leído; grep.

---

### [P1] 04 — El stream `@depth20@100ms` de USDT-M usa las claves `b`/`a`; el parser lee `bids`/`asks` → el order book vía WebSocket está SIEMPRE vacío
**Archivo:** `exchange/binance_ws.py:141-152`; consumidor `main.py:313-327`
**Evidencia:**
```python
elif "@depth" in stream:
    depth_data = {
        "s": symbol,
        "b": data.get("bids", []),   # el payload real trae "b"
        "a": data.get("asks", []),   # el payload real trae "a"
```
Verificación empírica (conexión real a `wss://fstream.binance.com/stream?streams=btcusdt@depth20@100ms`, 2026-08-29):
```
STREAM btcusdt@depth20@100ms KEYS ['e','E','T','s','ps','U','u','pu','b','a','st']
  has 'bids': False | has 'b': True | len(b)= 20
```
`main.py:319` hace `if bids and asks:` → nunca se cumple → `market_data.on_orderbook()` **jamás** se invoca desde el WS.
**Por qué es un problema:** OBI, microprice, `spread_bps`, `book_depth_usd` (smart router y modelo de slippage), `top_bid_depth_usd` (`main.py:584-589`) trabajan con el snapshot REST de `_data_refresh_loop` (cada **30 s**) o con ceros. Para un bot que evalúa cada 3 s y coloca límites a "1/3 del spread", un book de 30 s produce precios límite erróneos y `book_depth_usd=0` desactiva el impacto por tamaño. Mismo patrón silencioso que la lección "spot vs futures" del Audit #26. Borderline P0.
**Fix propuesto:** `data.get("b", [])` / `data.get("a", [])`; propagar `data["E"]`/`data["T"]`; test con el payload real; contador `depth_msgs_parsed` en el health del bridge y alerta si es 0 tras 10 s conectado.
**Verificado cómo:** **ejecutado** (WS real, salida arriba); leído.

---

### [P1] 05 — Protectivas colocadas inmediatamente tras una MARKET con respuesta `ACK` (status NEW) → carrera `-2022 ReduceOnly Order is rejected` → posición sin SL/TP y sin reintento
**Archivo:** `execution/order_engine.py:195-206`, `:214-286`; `exchange/binance_client.py:365-419`
**Evidencia:**
```python
# order_engine.py:195-206
# CRITICAL FIX: Don't gate on status — the order may fill on the exchange before the REST response arrives
if (signal.stop_loss != signal.entry_price ...):
    await self._place_protective_orders(signal, size_units, sym_config)
# order_engine.py:266-284
if not sl_ok and not tp_ok:
    emergency = Order(... order_type=OrderType.MARKET, quantity=size, reduce_only=True ...)
    await self.client.place_order(emergency)   # también reduceOnly → también -2022 si aún no hay posición
```
`place_order` no envía `newOrderRespType=RESULT` (grep → 0) → la respuesta de una MARKET en USDT-M es `ACK` (`status: NEW`, `executedQty: 0`).
**Por qué es un problema:** Si el SL/TP `reduceOnly` llega antes de que la posición exista (ventana de ms, mayor bajo carga), Binance responde `-2022`. No hay reintento (el `retry` de `place_bracket_order` existe pero **no se usa**: `grep place_bracket_order` → 0 usos fuera de `exchange/`), se dispara el "cierre de emergencia" que **también** es `reduceOnly` y **también** falla, y después la MARKET se ejecuta → **posición abierta sin ninguna protección**, solo un `logger.critical`, sin Telegram, sin reconciliación posterior (P0-03.3). El tamaño de las protectivas es `size_units` teórico, no `executedQty`. Contradice la lección registrada "SL/TP solo en FILLED".
**Fix propuesto:** (1) `newOrderRespType=RESULT` en MARKET → `status=FILLED` + `executedQty` + `avgPrice`. (2) Protectivas con `quantity=executedQty` redondeada; si 0, esperar `ORDER_TRADE_UPDATE` o consultar `positionRisk`. (3) Reintentar `-2022` con backoff (100/300/900 ms); si persiste, cerrar por `positionRisk` y **notificar**. (4) Para el SL usar `STOP_MARKET closePosition=true` (cierra toda la posición sin qty; no combinable con `reduceOnly`).
**Verificado cómo:** leído; doc oficial New-Order (`newOrderRespType`; `closePosition` "Cannot be sent with reduceOnly"); códigos de error (-2022).

---

### [P1] 06 — No se verifica/fija el modo de posición (Hedge vs One-way) ni el `marginType` al arrancar; `set_leverage` fallido no es fatal
**Archivo:** `main.py:156-163`; `exchange/binance_client.py:365-419`, `:592-603`
**Evidencia:**
```python
# main.py:157-163 — solo leverage, y un fallo es solo warning
await self.client.set_leverage(sym.symbol, sym.leverage)
except Exception as e:
    logger.warning("leverage_set_failed", ...)
```
`grep -rn "positionSide" exchange/ execution/ main.py` → 0. `set_margin_mode` existe pero `grep set_margin main.py` → 0 llamadas.
**Por qué es un problema:** En **Hedge Mode** (ajuste persistente de cuenta, fácil de activar por error desde la app) **todas** las órdenes sin `positionSide` fallan con `-4061`, incluidas cierre y emergencia. El margen queda en lo que estuviera (CROSSED por defecto): 4 posiciones en cross comparten el colateral y una liquidación arrastra la cuenta entera, mientras la config asume aislamiento por `max_position_usd`. Un `set_leverage` fallido deja el apalancamiento previo (p.ej. 20x de una prueba) y el bot sigue.
**Fix propuesto:** Arranque live: `GET /fapi/v1/positionSide/dual`; si `true` → `POST` a `false` (solo sin posiciones; si falla, abortar). `set_margin_mode(sym, "isolated")` explícito (decisión documentada). `set_leverage` fallido → abortar arranque en live.
**Verificado cómo:** leído; doc oficial New-Order (`positionSide` "must be sent in Hedge Mode"); error -4061.

---

### [P1] 07 — 418/429: reintenta un baneo de IP con backoff de 1-4 s (agrava el ban), ignora `Retry-After`; el limitador cuenta peticiones y no *weight*
**Archivo:** `exchange/binance_client.py:64-67`, `:70-92`, `:154-185`
**Evidencia:**
```python
def is_retryable(self) -> bool:
    return self.status in (429, 418) or self.status >= 500
_MAX_RETRIES = 3; _RETRY_BASE_SEC = 1.0  # 1s → 2s → 4s
self._rate_limiter = _RateLimiter(max_requests=1200, window_sec=60.0)
```
**Por qué es un problema:** Doc oficial: 418 = "IP auto-banned for continuing to send requests after receiving 429"; bans "from 2 minutes to 3 days" y escalan para reincidentes. Reintentar un 418 a los 1-4 s es exactamente "continuing to send requests" → ban más largo, durante el cual el bot **no puede cancelar ni cerrar nada**. Límites reales hoy (exchangeInfo): `REQUEST_WEIGHT 2400/min`, `ORDERS 300/10s`, `ORDERS 1200/min`. El limitador ignora pesos (`depth` 2-20, `positionRisk` 5) y no lee `X-MBX-USED-WEIGHT-1M` / `X-MBX-ORDER-COUNT-10S`.
**Fix propuesto:** 418 → **no reintentar**; pausar todo REST según `Retry-After` (≥120 s) y notificar. 429 → respetar `Retry-After`. Leer/loguear `X-MBX-USED-WEIGHT-1M`, frenar al 70 %. Limitador por weight.
**Verificado cómo:** leído; doc oficial general-info; exchangeInfo real (`rateLimits`).

---

### [P1] 08 — User data stream: URLs de mainnet fijas (ignora testnet), `listenKeyExpired` sin manejar, keepalive sin reintento
**Archivo:** `exchange/binance_ws.py:27-28`, `:211`, `:221-225`, `:236-240`, `:263`, `:283`
**Evidencia:**
```python
BINANCE_FAPI_BASE = "https://fapi.binance.com"      # sin variante testnet
BINANCE_FAPI_WS = "wss://fstream.binance.com/ws"    # sin variante testnet
url = f"{BINANCE_FAPI_WS}/{listen_key}"             # connect_user ignora self._use_testnet
if event_type == "ORDER_TRADE_UPDATE": ...
elif event_type == "ACCOUNT_UPDATE": ...            # listenKeyExpired → ignorado
```
**Por qué es un problema:** (a) Con claves de testnet, `POST https://fapi.binance.com/fapi/v1/listenKey` → 401 → `connect_user` retorna en silencio → en testnet **nunca** se procesan fills (`on_order_update` no corre) → imposible validar el pipeline live en testnet, el paso previo a dinero real. (b) Si un PUT de keepalive falla no se reintenta hasta 30 min después; la clave caduca a los 60 min, Binance emite `listenKeyExpired` y deja de enviar eventos; el código no lo detecta → fills, `rp` y equity dejan de actualizarse sin alerta. (c) `_running` compartido entre `connect_market` y `connect_user`.
**Fix propuesto:** Parametrizar REST/WS de user stream por `use_testnet` (`testnet.binancefuture.com` / `stream.binancefuture.com`; la doc actual también lista `demo-fapi.binance.com` / `demo-fstream.binance.com` — ambos responden HTTP 200 hoy). Manejar `e == "listenKeyExpired"` → reconectar con clave nueva. Reintentar keepalive (3×, 10 s) y forzar reconexión si persiste. Watchdog: sin frames de usuario en N min con órdenes activas → `GET /fapi/v1/openOrders`.
**Verificado cómo:** leído; doc oficial Start-User-Data-Stream ("The stream will close after 60 minutes unless a keepalive is sent"); `curl` a testnet/demo (200).

---

### [P1] 09 — La tarea del WS de mercado muere ante cualquier excepción no prevista; el supervisor nunca reinicia tareas críticas; `_connected` queda en `True`
**Archivo:** `exchange/binance_ws.py:87-122`; `main.py:222-278`
**Evidencia:**
```python
# binance_ws.py:113 — solo estas dos
except (websockets.exceptions.ConnectionClosed, OSError) as e:
    self._connected = False
# main.py:264-274
if name in restartable_methods and crash_counts[name] <= max_restarts:   # solo metrics/data_refresh
elif crash_counts[name] > max_restarts:                                   # 1 crash nunca supera 3
```
**Por qué es un problema:** `InvalidStatus`/`InvalidHandshake` (429/403 en el handshake durante una tormenta de reconexiones), `InvalidMessage`, o un error dentro de `_process_message` (fuera del `try` que solo captura `JSONDecodeError`) terminan la corrutina. `_supervise_tasks` registra `task_crashed` con `crash_count=1`, no la reinicia y no apaga → el bot sigue vivo **sin datos de mercado** con `_connected=True` (bridge/desktop muestran "Connected"). El guard de datos stale bloquea nuevas entradas, pero las posiciones abiertas dependen solo de los SL del exchange (que P0-03/P1-05 pueden haber eliminado).
**Fix propuesto:** Capturar `Exception` en el bucle de reconexión (log, backoff, continuar); `_connected=False` en `finally`. En `_supervise_tasks`, `ws_market`/`ws_user`/`strategy`/`risk_monitor` reiniciables con límite; al superarlo, apagado seguro (cerrar posiciones o dejar SL vivos).
**Verificado cómo:** leído.

---

### [P1] 10 — En live la equity nunca se inicializa desde el exchange; solo cambia con `ACCOUNT_UPDATE.wb` (wallet balance, sin PnL no realizado)
**Archivo:** `main.py:368-379`; `risk/risk_manager.py:40-41`; `main.py` (`grep get_balances|get_account` → 0 llamadas)
**Evidencia:**
```python
# risk_manager.py:40-41
self._equity_peak: float = self.config.initial_capital     # 1000.0 hardcoded
self._current_equity: float = self.config.initial_capital
# main.py:372-376
if b.get("a") in ("USDT", "USD"):
    equity = float(b.get("wb", 0))     # wallet balance: NO incluye unrealized PnL
```
**Por qué es un problema:** Sizing (`risk_per_trade_pct`), drawdown y circuit breaker operan sobre $1.000 ficticios hasta el primer `ACCOUNT_UPDATE` (solo tras fill/funding/transfer). Con $700 (o $3.000) reales, el riesgo por trade y la exposición son incorrectos. `wb` excluye PnL no realizado → 4 posiciones perdiendo 8 % cada una = drawdown "medido" 0 % hasta cerrarlas; el corte del 10 % no salta.
**Fix propuesto:** Arranque live: `GET /fapi/v2/balance` (`balance`, `crossUnPnl`) o `/fapi/v2/account` (`totalMarginBalance`) → `update_equity_safe()`. En el bucle de riesgo (ya llama a `positionRisk` cada 2 s) usar margin balance = wallet + Σ`unrealizedProfit`. En `ACCOUNT_UPDATE` usar `cw` + Σ`up` de `a.P`.
**Verificado cómo:** leído.

---

### [P1] 11 — Rama LIMIT del smart router: precio por debajo del mid + `IOC` → en live casi nunca llena; paper la llena con probabilidad aleatoria → divergencia paper/live y protectivas sobre posición inexistente
**Archivo:** `execution/order_engine.py:158-175`; `execution/smart_router.py:386`, `:426-433`, `:445`; `execution/paper_simulator.py:450-461`
**Evidencia:**
```python
# smart_router.py:428-433 — BUY por debajo del mid; SELL por encima
limit_price = ref_price - limit_distance_bps * ref_price / 10_000
# order_engine.py:166-175
order = Order(..., order_type=OrderType.LIMIT, price=limit_price, time_in_force=TimeInForce.IOC, ...)
# paper_simulator.py:455
if random.random() > routing.fill_probability: return None   # paper: fill probabilístico (sin semilla)
```
**Por qué es un problema:** Una LIMIT `IOC` de compra por debajo del mejor ask se cancela al instante → `EXPIRED`, `executedQty=0`. En live es "no entrar", pero el motor **igualmente** coloca SL/TP (`:203-206`) → `-2022` ×2 → "emergency close" → `-2022`: tres CRITICAL por señal sin posición. En paper la misma señal se llena con `fill_probability` → paper **sobreestima** entradas y price improvement respecto a live. El override `spread < 3 bps → MARKET` (`:436`) lo enmascara en BTC/ETH pero no en SOL/ADA.
**Fix propuesto:** Decidir semántica: (a) LIMIT pasiva `GTC`/`GTX` con timeout y cancel/replace, o (b) LIMIT agresiva `IOC` que **cruce** el spread (marketable limit = protección de slippage). Gatear protectivas en `executedQty>0`. Paper debe aplicar la misma regla (una IOC no marketable no llena).
**Verificado cómo:** leído.

---

### [P1] 12 — Sin OCO explícito: SL y TP nunca se cancelan mutuamente; un SL/TP huérfano puede cerrar la SIGUIENTE posición del mismo símbolo; `cleanup_stale_orders` borra los SL/TP del tracking
**Archivo:** `execution/order_engine.py:359-449`, `:462-475`, `:214-286`
**Evidencia:** En `on_order_update`, al llegar `X == "FILLED"` de un SL solo se hace `_active_orders.pop(order_id)`; `grep -n cancel_order execution/order_engine.py` → solo en `refresh_mm_orders` (MM archivado). `cleanup_stale_orders(300s)` elimina cualquier orden de >5 min sin filtrar por tipo.
**Por qué es un problema:** Binance no tiene OCO en futuros; el código confía en que el exchange expire las `reduceOnly` restantes al quedar la posición a 0 (comportamiento de plataforma no verificable hoy en la doc de la API vía WebFetch; no aplica si la posición queda parcial). Escenario: MR sale por señal (MARKET reduceOnly); el SL/TP antiguo sigue vivo o la siguiente entrada llega dentro de la ventana de reconciliación (10 s) → la nueva posición queda con un SL/TP **del trade anterior** (niveles/tamaño equivocados) además del nuevo → cierre prematuro o doble. Además, tras 5 min los SL/TP salen de `_active_orders` → el fill del SL llega con `order=None` → `strategy=None`, sin slippage/latencia ni atribución por estrategia para Kelly.
**Fix propuesto:** Vincular SL y TP (`parent_cid`); en el fill de uno, `DELETE` del otro (tolerar `-2011`). En cada exit por señal: cancelar protectivas del símbolo **antes** de la MARKET de cierre. Excluir `STOP`/`TAKE_PROFIT` de `cleanup_stale_orders` (reconciliar con `openOrders`, no por edad). Verificar en testnet el auto-cancel del exchange y anotarlo en `lessons.md`.
**Verificado cómo:** leído.

---

### [P1] 13 — Integración Hyperliquid INCOMPLETA e insegura (P0 si se activa): SL/TP crashean, sin redondeo, wallet agente mal usada, fills WS nunca parseados, testnet ignorado, sin heartbeat
**Archivo:** `exchange/hyperliquid_client.py:72-79`, `:94-97`, `:249-289`, `:201-245`; `exchange/hyperliquid_ws.py:69`, `:122-154`, `:206-245`
**Evidencia:**
```python
# hyperliquid_client.py:272-282 — triggerPx como str
{"trigger": {"triggerPx": str(order.stop_price), "isMarket": True, "tpsl": "sl"}}
# SDK oficial (hyperliquid/utils/signing.py, master):
def float_to_wire(x: float) -> str:
    rounded = f"{x:.8f}"          # con str → ValueError: Unknown format code 'f' for object of type 'str'
# hyperliquid_client.py:72-79 — siempre mainnet; wallet del agente sobrescribe la master
base_url = constants.MAINNET_API_URL
account = Account.from_key(self._private_key)
self._wallet = account.address                   # ignora settings.hyperliquid_wallet_address
self._exchange = Exchange(account, base_url)     # sin account_address=
# hyperliquid_ws.py:211-212 — userFills es un objeto {isSnapshot,user,fills:[...]}, no una lista
for fill in data if isinstance(data, list) else [data]:
    coin = fill.get("coin", "")                  # data no tiene "coin" → símbolo "-USD", px "0"
# hyperliquid_ws.py:230 — userEvents es un dict {"fills": [...]}
if isinstance(data, list):                       # nunca True → nada
```
**Por qué es un problema:** (1) Cualquier `OrderType.STOP`/`TAKE_PROFIT` lanza `ValueError` antes de firmar → las protectivas **siempre** fallan → "emergency close" (que sí funciona) → el bot abre y cierra al instante cada trade, pagando fees/slippage. (2) `sz=order.quantity` y `limit_px` sin redondear a `szDecimals`/5 cifras significativas → `Invalid size/price` (doc oficial tick-and-lot-size). (3) Con una **API/agent wallet** (lo recomendado por seguridad), `user_state(agent_address)` devuelve vacío: la doc dice "you must pass in the actual address of that account" → posiciones y balance = 0 → risk manager ciego, entradas duplicadas. (4) `userFills`/`userEvents` mal parseados → ningún fill llega a `on_order_update` (el guard `fill_price<=0` descarta el evento basura). (5) `use_testnet` ignorado por cliente y WS → "testnet" opera con dinero real. (6) Sin `{"method":"ping"}` cada <60 s (doc: "The server will close any connection if it hasn't sent a message to it in the last 60 seconds"); `ping_interval=20` envía frames de protocolo, no el mensaje JSON exigido → posibles reconexiones cada minuto. (7) `_run_sync` usa `asyncio.get_event_loop()` (deprecado) y el SDK síncrono bloquea un hilo por llamada; `get_market_snapshot` descarga `meta_and_asset_ctxs` completo por símbolo y ciclo. (8) `markPrice` = `positionValue` (notional, no precio); `available` = `totalRawUsd`; `get_klines` close time = open time.
**Fix propuesto:** No activar HL en live hasta: `triggerPx=float(...)`; redondeo con `szDecimals` de `meta()` y regla de 5 s.f.; `Exchange(agent, url, account_address=settings.hyperliquid_wallet_address)` y consultas con la master; parsear `data["fills"]` en ambos canales; `TESTNET_API_URL` cuando `use_testnet`; tarea de heartbeat `{"method":"ping"}` cada 50 s; tests unitarios con payloads reales de la doc; `markPx` desde `meta_and_asset_ctxs`.
**Verificado cómo:** leído; código fuente oficial del SDK (`raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/hyperliquid/utils/signing.py`, `exchange.py`); doc oficial HL (websocket/subscriptions, timeouts-and-heartbeats, nonces-and-api-wallets, tick-and-lot-size). El SDK no está instalado en el entorno local (`ModuleNotFoundError: hyperliquid`) → la rama HL nunca ha corrido aquí.

---

### [P2] 14 — Sin sincronización de reloj ni `recvWindow` explícito → ante deriva de reloj todas las llamadas firmadas fallan (-1021) sin auto-corrección
**Archivo:** `exchange/binance_client.py:134-144`
**Evidencia:** `params["timestamp"] = int(time.time() * 1000)`; `grep -rn "serverTime\|/fapi/v1/time\|recvWindow" exchange/` → 0.
**Por qué es un problema:** Regla oficial: `timestamp < serverTime + 1000 && serverTime - timestamp <= recvWindow(5000)`. Un reloj del LXC 1 s adelantado (NTP caído, VM pausada) → **todas** las órdenes, cancelaciones y cierres fallan con `-1021` hasta reiniciar; `_retry_request` lo trata como no reintentable y no re-sincroniza.
**Fix propuesto:** `GET /fapi/v1/time` al arrancar y cada 10 min → `_time_offset_ms`; al recibir `-1021`, re-sincronizar y reintentar una vez (es idempotente: la orden no llegó a crearse). Enviar `recvWindow=5000` explícito.
**Verificado cómo:** leído; doc oficial general-info (Timing security).

---

### [P2] 15 — `workingType` por defecto `CONTRACT_PRICE` (last price) y sin `priceProtect` en SL/TP
**Archivo:** `exchange/binance_client.py:383-388`
**Evidencia:** `params["stopPrice"] = ...` sin `workingType` ni `priceProtect` (grep → 0).
**Por qué es un problema:** El SL se dispara por último precio negociado: una mecha/impresión aislada en un símbolo delgado (ADA/SOL de madrugada) activa el stop aunque el mark price no haya llegado; con `MARK_PRICE` el disparo sigue el índice y es el criterio de liquidación, coherente con el margen. Sin `priceProtect`, un stop puede ejecutar durante una desviación mark/last extrema.
**Fix propuesto:** `workingType=MARK_PRICE` + `priceProtect=true` para SL; para TP valorar `CONTRACT_PRICE` (se ejecuta por precio real). Documentar la decisión y reflejarla en el paper simulator (usar mark price para SL).
**Verificado cómo:** leído; doc oficial New-Order (`workingType` "Default: CONTRACT_PRICE"; `priceProtect`).

---

### [P2] 16 — Tamaño de las órdenes de salida/emergencia derivado de `signal.size_usd/price`, no de la posición real; `MIN_NOTIONAL` sin comprobar
**Archivo:** `execution/order_engine.py:83-84`, `:146-155`, `:270-278`
**Evidencia:** `size_units = signal.size_usd / price`; la salida es `MARKET reduce_only` con ese tamaño; la emergencia usa `size` de la señal.
**Por qué es un problema:** Si la posición real difiere (fill parcial, duplicado por P0-02, posición previa), la salida cierra de menos o (según cómo trate Binance un reduceOnly mayor que la posición) es rechazada. Órdenes por debajo de `minNotional` (BTC 50, ETH 20; posible con vol-targeting reduciendo el size) fallan con `-4164` salvo `reduceOnly`.
**Fix propuesto:** Para exits usar `abs(positionAmt)` de `positionRisk` (o `closePosition=true`). Validar `minNotional` antes de enviar entradas (P0-01.3).
**Verificado cómo:** leído; error -4164 doc oficial.

---

### [P2] 17 — Paper simulator optimista respecto a live en TP, funding y fills parciales; no determinista
**Archivo:** `execution/paper_simulator.py:280-281`, `:455`, `:496-537`
**Evidencia:**
```python
exit_price = pos.take_profit  # TP as limit order — exact fill      (live: TAKE_PROFIT_MARKET → taker + slippage)
if random.random() > routing.fill_probability: return None         # sin semilla
```
Sin modelo de funding (la config tiene umbrales de funding pero el paper nunca lo cobra), sin fills parciales, sin liquidación, fee de entrada diferido al cierre (equity intermedia desviada).
**Por qué es un problema:** Cada TP en paper ahorra ~spread/2 + slippage + (taker−maker) respecto a live → con cientos de trades la esperanza de paper queda inflada varios bps por trade, que es del orden del edge de un scalper. Resultados no reproducibles entre corridas.
**Fix propuesto:** TP con slippage adverso 0.5-1.0× `slippage_bps` y fee taker (coherente con `TAKE_PROFIT_MARKET`), o cambiar live a `TAKE_PROFIT` límite `GTX` y mantener el paper exacto. Cobrar funding cada 8 h usando `snap.funding_rate`. `random.Random(seed)` inyectable. Modelar fill parcial para LIMIT.
**Verificado cómo:** leído.

---

### [P2] 18 — Netting en one-way mode entre MR y Fibonacci sobre el mismo símbolo; el motor no serializa órdenes por símbolo
**Archivo:** `main.py:120-122` (dos estrategias activas), `main.py:535` (`_positions.get(symbol)` por símbolo), `execution/order_engine.py:78`
**Evidencia:** `self.strategies = [MeanReversionStrategy, FibonacciRetracementStrategy]`; en live `current_pos = self._positions.get(symbol)` (una por símbolo) mientras `paper_sim` indexa `symbol_STRATEGY`.
**Por qué es un problema:** En one-way mode el exchange netea: un short de Fib cierra (o reduce) el long de MR; un exit `reduceOnly` de una estrategia cierra la posición de la otra; los SL/TP de ambas coexisten sobre una única posición neta. Paper y live divergen (paper permite dos posiciones por símbolo). Nada impide que dos señales del mismo ciclo lancen dos entradas en paralelo.
**Fix propuesto:** Regla explícita "una posición por símbolo" en live (lock por símbolo en `execute_signal`, consulta a `_positions` antes de enviar), o hedge mode con `positionSide` por estrategia (más complejo). Alinear el paper con la regla elegida.
**Verificado cómo:** leído.

---

### [P2] 19 — `replace_order` reintenta `place_order` tras cancelar → mismo riesgo de duplicado que P0-02 (hoy sin uso)
**Archivo:** `exchange/binance_client.py:506-531`
**Evidencia:** `except Exception: ... await asyncio.sleep(0.3); return await self.place_order(new_order)` — reenvío ciego. `grep replace_order` fuera de `exchange/` → 0 usos.
**Fix propuesto:** Misma política que P0-02 (consultar por `origClientOrderId` antes de reenviar). Marcar como no usado o eliminar.
**Verificado cómo:** leído.

---

### [P3] 20 — Fallback de normalización de símbolos genera símbolos COIN-M
**Archivo:** `exchange/binance_client.py:124-126`
**Evidencia:** `SYMBOL_MAP.get(symbol, symbol.replace("-", ""))` → `"BNB-USD"` → `"BNBUSD"` (formato COIN-M, inexistente en `/fapi`).
**Fix propuesto:** Sin fallback: lanzar `ValueError` si el símbolo no está en `SYMBOL_MAP` (falla rápido en arranque).
**Verificado cómo:** leído.

### [P3] 21 — Dependencia muerta de Strike (pynacl) importada en el arranque
**Archivo:** `exchange/__init__.py:1-2`; `execution/order_engine.py:18-23`
**Evidencia:** `from .strike_client import StrikeClient` en `__init__` y `ExchangeClient = Union[StrikeClient, BinanceClient]` → `nacl` se importa siempre aunque `exchange_venue="binance"`. `HyperliquidClient` no está en el `Union`.
**Fix propuesto:** Definir un `Protocol` `ExchangeClient` en `core/types.py`; importar Strike de forma perezosa o archivarlo.
**Verificado cómo:** leído; grep de usos.

### [P3] 22 — Endpoints/URLs con deriva respecto a la doc actual
**Archivo:** `exchange/binance_client.py:33`, `:355`; `exchange/binance_ws.py:30-31`
**Evidencia:** `GET /fapi/v2/positionRisk` (existe `v3`); testnet `testnet.binancefuture.com` / `stream.binancefuture.com` (la doc actual lista `demo-fapi.binance.com` / `demo-fstream.binance.com`; ambos hosts responden HTTP 200 hoy).
**Fix propuesto:** Centralizar URLs en `settings` y anotar la versión de doc contra la que se validó.
**Verificado cómo:** `curl` (200 en ambos); doc oficial general-info.

### [P3] 23 — Detalles menores
- `binance_client.py:195,233`: acepta 201 (Binance solo devuelve 200); inocuo.
- `binance_ws.py:98-100`: el log `subscribed` lista `trade/depth/kline_1m` pero también se suscribe `markPrice`.
- `binance_ws.py:149`: el timestamp de depth es `time.time()` local en lugar de `data["E"]`/`data["T"]` (impide medir latencia de book).
- `hyperliquid_client.py:134`: `index_price=mark_price`.
- `paper_simulator.py:377`: `import time as _time` redundante.

---

## Tabla resumen

| ID | Sev | Título | Archivo |
|---|---|---|---|
| 01 | **P0** | Sin redondeo a stepSize/tickSize → órdenes rechazadas en BTC/SOL/ADA; SL de ADA desplazado 37 bps | `binance_client.py:374-392` |
| 02 | **P0** | Reintentos ciegos de `POST /order` tras timeout/5xx → orden duplicada | `binance_client.py:157-220` |
| 03 | **P0** | `cancel_all()` en drawdown/shutdown deja posiciones sin SL/TP; sin re-protección al arrancar | `main.py:755,826`; `order_engine.py:453` |
| 04 | P1 | Depth WS lee `bids/asks` (real: `b/a`) → book WS siempre vacío | `binance_ws.py:145-148` |
| 05 | P1 | Protectivas tras MARKET `ACK` → carrera -2022 → posición desnuda | `order_engine.py:195-286` |
| 06 | P1 | Sin check de hedge mode / marginType; `set_leverage` fallido no fatal | `main.py:156-163` |
| 07 | P1 | Reintenta 418 (ban) en 1-4 s; ignora Retry-After; limitador sin weight | `binance_client.py:64-92,154-185` |
| 08 | P1 | User stream: mainnet fijo (testnet sin fills), `listenKeyExpired` ignorado | `binance_ws.py:27-28,211-283` |
| 09 | P1 | WS task muere por excepciones no previstas; supervisor no reinicia; `_connected` falso | `binance_ws.py:113`; `main.py:264-274` |
| 10 | P1 | Equity live no inicializada desde exchange; `wb` sin uPnL | `main.py:372-376`; `risk_manager.py:40` |
| 11 | P1 | LIMIT IOC por debajo del mid nunca llena en live; paper sí (aleatorio) | `order_engine.py:158-175`; `smart_router.py:426-445` |
| 12 | P1 | Sin OCO explícito; `cleanup_stale_orders` borra SL/TP del tracking | `order_engine.py:359-475` |
| 13 | P1 | Hyperliquid incompleto: SL/TP crashean, sin redondeo, wallet agente, fills no parseados, sin testnet, sin heartbeat | `hyperliquid_client.py`; `hyperliquid_ws.py` |
| 14 | P2 | Sin sync de reloj / recvWindow (-1021 sin recuperación) | `binance_client.py:134-144` |
| 15 | P2 | `workingType` CONTRACT_PRICE y sin `priceProtect` | `binance_client.py:383-388` |
| 16 | P2 | Exits/emergencia con tamaño de señal, no de posición; sin MIN_NOTIONAL | `order_engine.py:83-155,270-278` |
| 17 | P2 | Paper optimista: TP exacto, sin funding, sin parciales, aleatorio sin semilla | `paper_simulator.py:280,455` |
| 18 | P2 | Netting one-way entre MR y Fib; sin lock por símbolo | `main.py:120-122,535` |
| 19 | P2 | `replace_order` reenvía ciego (sin uso) | `binance_client.py:506-531` |
| 20 | P3 | Fallback de símbolo genera formato COIN-M | `binance_client.py:124-126` |
| 21 | P3 | Dependencia muerta Strike/pynacl; `Union` sin HL | `exchange/__init__.py`; `order_engine.py:18-23` |
| 22 | P3 | positionRisk v2 / URLs testnet con deriva de doc | `binance_client.py:33,355` |
| 23 | P3 | Detalles menores (timestamps, logs, imports) | varios |

**Totales:** P0 = 3 · P1 = 10 · P2 = 6 · P3 = 4 (23 hallazgos).

---

## Veredicto (¿es seguro pasar a LIVE con dinero real tal como está?)

**NO.** Tal como está, el bot no es operable en live con Binance USDT-M, y menos con dinero real:

1. Con los filtros reales del exchange, las entradas de BTC/SOL/ADA se rechazan por precisión (P0-01); solo ETH operaría, con stops redondeados. No es "seguro por inoperante": las salidas y cierres de emergencia sufren el mismo rechazo, así que cualquier posición existente no puede cerrarse desde el bot.
2. Los reintentos ciegos de `POST /order` (P0-02) pueden duplicar o cuadruplicar una posición con SL/TP dimensionados para el tamaño original.
3. El corte por drawdown y el `shutdown` cancelan los SL/TP sin cerrar posiciones (P0-03): el mecanismo de "parar" convierte una pérdida limitada en exposición desnuda con 5x, y un reinicio no la re-protege.
4. El order book vía WS está vacío (P1-04) y las protectivas tienen una carrera real con `-2022` sin reintento (P1-05); el modo hedge/margen no se verifica (P1-06); en testnet nunca llegan fills (P1-08), por lo que **ni siquiera es posible validar el pipeline de ejecución en testnet** antes de arriesgar dinero.
5. La integración Hyperliquid (P1-13) no debe activarse bajo ningún concepto: sus SL/TP crashean y opera en mainnet aunque se pida testnet.

**Obligatorio antes de live (orden sugerido):** P0-01 (exchangeInfo + redondeo + tests) → P0-02 (sin reintentos en órdenes; reconciliar por `origClientOrderId`) → P0-03 (`close_all_positions`, no cancelar SL en shutdown, re-protección al arrancar) → P1-05 (`newOrderRespType=RESULT`, protectivas por `executedQty`, `closePosition=true`, reintento -2022, alerta) → P1-04 (`b`/`a`) → P1-06 (one-way + margen explícito) → P1-08 (user stream testnet + `listenKeyExpired`) → P1-10 (equity real) → P1-07 (418) → P1-12 (OCO explícito). Después: ≥2 semanas en **testnet con fills reales** verificando fill → SL/TP → cierre → cancel del hermano, y arrancar en mainnet con `max_position_usd` reducido al 20 %.
