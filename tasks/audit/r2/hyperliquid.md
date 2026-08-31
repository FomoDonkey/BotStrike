# Auditoría R2 — Área: Hyperliquid (integración en profundidad)

Fecha: 2026-08-31 · Auditor: agente R2 `hyperliquid`
Alcance: `exchange/hyperliquid_client.py`, `exchange/hyperliquid_ws.py`, uso desde `main.py` y `server/bridge.py`, contraste con el SDK **realmente instalado**, con `tasks/research_r2_hyperliquid_execution.md` y con la doc oficial (WebFetch).

## Entorno verificado (medido, no supuesto)

```
py -3.12 -c "import importlib.metadata as md; print(md.version('hyperliquid-python-sdk'), md.version('eth-account'))"
0.22.0  0.13.7
```
- SDK en `C:\Users\edgar\AppData\Local\Programs\Python\Python312\Lib\site-packages\hyperliquid`
- `requirements.txt:14` → `hyperliquid-python-sdk>=0.22.0` (flotante) · `requirements.lock:33` → `==0.24.0`
- `git log --oneline -- exchange/hyperliquid_client.py exchange/hyperliquid_ws.py` → **un único commit**: `2e9b9ce feat: v2.10.0`.
  ⇒ **Ni un solo fix de la ronda 1 (`02-P1-13`) se ha aplicado.** Todo lo que se listó el 2026-08-29 sigue literalmente igual, byte a byte.
- Todas las cifras de mercado de este informe se midieron hoy contra `POST https://api.hyperliquid.xyz/info` (mainnet, 200 OK).

## Veredicto sobre las dos "trampas" que documenta la investigación

| Trampa (research §11.3) | Veredicto | Detalle |
|---|---|---|
| (a) `DEFAULT_SLIPPAGE = 0.05` → IOC a ±5% del mid | **REFUTADA en el repo** | `hyperliquid_client.py:261-263` pasa `0.01` explícito por posición. El bot **no** cae en el 5%. Riesgo residual real: 100 bps sigue siendo 11× el round-trip taker (9 bps) y no hay validación de profundidad contra `l2Book` → ver **HL-19**. |
| (b) `market_close()` prioriza `account_address` sobre `wallet.address` → con agent wallet no cierra nada, en silencio | **CONFIRMADA la causa raíz; el vector concreto no es alcanzable hoy** | El repo **nunca llama a `market_close()`** (`grep market_close exchange/` → 0). Pero la causa raíz —consultar `info` con la dirección del agent— sí está en el repo y afecta a **4 rutas**, verificado en vivo: ver **HL-05**. Y si alguien "arregla" HL-03 usando `market_close()` (como sugería el informe previo), la trampa (b) se activa tal cual. |
| `02-P1-13` (SL/TP crashean por `float_to_wire(str)`; opera en mainnet aunque se pida testnet) | **SIGUE ABIERTO, sin tocar** | Reproducido con el SDK instalado: ver **HL-04** y **HL-06**. |

---

## Hallazgos

### [P0] hyperliquid-01 — La UI ofrece "Hyperliquid" pero el bridge arranca SIEMPRE el motor en Binance (`use_binance=True` hardcodeado)
**Archivo:** `server/bridge.py:314`
**Evidencia:**
```python
# server/bridge.py:291-315
async def start_engine(mode: str = "paper", settings: Optional[Settings] = None):
    """... Binance stays the data/execution backend (use_binance=True)."""
    state.engine = BotStrike(settings=settings, dry_run=is_dry_run, paper=is_paper,
                             use_binance=True)          # <-- CONSTANTE
# main.py:60
self._venue = ExchangeVenue.BINANCE if use_binance else settings.exchange_venue_enum
```
`_build_settings("hyperliquid")` (`bridge.py:280-288`) solo cambia fees y `exchange_venue`; el venue efectivo se descarta una línea después. La UI sí expone el venue (`desktop/src/components/shared/ExchangeSelector.tsx:13-14`, `exchangeStore.ts:3`). Y el único test que lo toca comprueba el campo, no el motor:
```python
# tests/test_bridge_round2.py:162
assert starts[0]["settings"].trading.exchange_venue == "hyperliquid"
```
**Por qué:** el dueño reside en España y Binance está cerrado para residentes ES desde 1-jul-2026 (`tasks/research_r2_venues_es_2026.md`). Hyperliquid es **el único venue potencialmente ejecutable**, y desde el producto es **inalcanzable**: pulsar "Hyperliquid" arranca `BinanceClient` + `BinanceWebSocket` contra `fapi.binance.com`, mostrando "Hyperliquid" en la barra superior. `HyperliquidClient` y `HyperliquidWebSocket` **nunca se instancian por la ruta de producto** — de ahí que ninguno de los 12 P0 de este informe se haya manifestado nunca en runtime. Consecuencia colateral: las fees de HL (1.5/4.5 bps) se aplican a una ejecución que corre en Binance, así que el modelo de costes también queda mal.
**Fix:** `use_binance=(exchange == "binance")`; y el test debe afirmar `isinstance(state.engine.client, HyperliquidClient)`, no un campo de dataclass.
**Verificado cómo:** leídos `bridge.py`, `main.py`, el test y los dos ficheros del desktop; es una asignación constante sin ramas.

---

### [P0] hyperliquid-02 — El 100% de las órdenes revienta con `ValueError` ANTES de salir a la red: `sz = size_usd/price` sin redondear a `szDecimals`
**Archivo:** `exchange/hyperliquid_client.py:256`, `:258-289`; `execution/order_engine.py:97`
**Evidencia:** `grep -n "szDecimals\|round\|Decimal\|sig" exchange/hyperliquid_client.py` → **0 resultados**. El tamaño llega crudo del motor:
```python
# execution/order_engine.py:97
size_units = signal.size_usd / price if price > 0 else 0
# exchange/hyperliquid_client.py:256
sz = order.quantity                      # sin tocar
```
El SDK valida el float antes de firmar (`hyperliquid/utils/signing.py:474-481`). Ejecutado con `py -3.12` y el SDK instalado, sobre tamaños calculados con **precios reales de hoy** y $200 de notional:
```
0.002547082826039337 (BTC) -> CRASH ValueError ('float_to_wire causes rounding', ...)
0.08093889113719142  (ETH) -> CRASH ValueError
1.940052381414298    (SOL) -> CRASH ValueError
1021.3982942648485   (ADA) -> CRASH ValueError
0.00255 / 1021.0            -> OK ('0.00255' / '1021')
```
`szDecimals` reales medidos hoy (`metaAndAssetCtxs`): `BTC 5 · ETH 4 · SOL 2 · ADA 0 · BNB 3 · DOGE 0 · XRP 0`.
Y los precios, aunque pasan `float_to_wire`, violan la regla de tick: `77343.382` son **8 cifras significativas** y BTC solo admite `6 - szDecimals = 1` decimal → `tickRejected`. El válido sería `77343.0`.
**Por qué:** no es "a veces el exchange rechaza"; es un `ValueError` **determinista y client-side en todas y cada una de las órdenes** (entrada, SL, TP, cierre por señal). La probabilidad de que `size_usd/price` caiga en ≤8 decimales es despreciable. Comparativa demoledora: la ronda 1 construyó para Binance `parse_symbol_filters` / `round_quantity` / `round_price` / `_normalize_order_params` + validación de `minNotional` (`binance_client.py:434-505`); en HL **no existe nada de eso**, ni tampoco el mínimo oficial de **$10 de notional** (`minTradeNtlRejected`), ni la validación de `maxLeverage`.
**Fix:** cachear `meta()` al arrancar → `{coin: (szDecimals, maxLeverage)}`; `sz = round(qty, szDecimals)` con *floor* (nunca subir el tamaño); `px = round(float(f"{px:.5g}"), 6 - szDecimals)` (misma fórmula que `Exchange._slippage_price`, que ya la aplica bien y por eso el precio de las MARKET sí sale válido); rechazar `sz*px < 10` con error explícito; y `assert` de pre-flight que impida enviar algo inválido (no quemar presupuesto de acciones).
**Verificado cómo:** `py -3.12` contra el SDK 0.22.0 instalado (traza arriba); `szDecimals` y precios descargados en vivo de `api.hyperliquid.xyz/info`; regla de tick contrastada con la doc oficial (`for-developers/api/tick-and-lot-size`).

---

### [P0] hyperliquid-03 — Toda orden MARKET pierde `reduce_only`: el flatten de shutdown puede ABRIR una posición contraria desnuda
**Archivo:** `exchange/hyperliquid_client.py:259-263`
**Evidencia:**
```python
# exchange/hyperliquid_client.py:259-263
if order.order_type == OrderType.MARKET:
    result = self._exchange.market_open(
        coin, is_buy, sz, None, 0.01     # <-- order.reduce_only NUNCA se pasa
    )
```
SDK instalado (`site-packages/hyperliquid/exchange.py:225-240`):
```python
def market_open(self, name, is_buy, sz, px=None, slippage=DEFAULT_SLIPPAGE, cloid=None, builder=None):
    px = self._slippage_price(name, is_buy, slippage, px)
    return self.order(name, is_buy, sz, px, order_type={"limit": {"tif": "Ioc"}},
                      reduce_only=False, ...)          # <-- HARDCODED False
```
Los cierres del motor son MARKET + `reduce_only=True`: `order_engine.py:344` (exit), `:365` (emergency close), `:595` (flatten en shutdown/drawdown).
**Por qué:** este es el escenario que el commit `b3dbf75` ("naked positions") eliminó en Binance y que en HL sigue vivo. **Y es alcanzable pese a HL-02**: `_flatten_all` (`order_engine.py:590-596`) toma `quantity=abs(amt)` de `positionAmt` **devuelto por el exchange**, que ya viene redondeado a `szDecimals` → `float_to_wire` lo acepta → la orden sale de verdad, sin `reduceOnly`. Si la posición ya está cerrada (SL disparado, cierre parcial, doble evento, lag de reconciliación), esa MARKET **abre una posición nueva del lado contrario, con el tamaño completo y sin SL ni TP**. `close_positions_on_shutdown = True` por defecto (`config/settings.py:93`) ⇒ se dispara en **cada deploy y cada reinicio**.
**Fix:** no usar `market_open`. Construir a mano: `Exchange.order(coin, is_buy, sz, self._exchange._slippage_price(coin, is_buy, slip), {"limit": {"tif": "Ioc"}}, reduce_only=order.reduce_only, cloid=...)`. **No sustituirlo por `market_close()`**: con agent wallet devuelve `None` en silencio (ver HL-05).
**Verificado cómo:** fuente del SDK instalado 0.22.0 leído; `grep` de los cuatro `reduce_only=True` del motor; ruta del flatten leída línea a línea.

---

### [P0] hyperliquid-04 — `02-P1-13` sigue abierto: `triggerPx` como `str` revienta con `ValueError` en TODOS los SL/TP
**Archivo:** `exchange/hyperliquid_client.py:274`, `:280`
**Evidencia:**
```python
# :274 y :280 — sin cambios desde 2e9b9ce
{"trigger": {"triggerPx": str(order.stop_price), "isMarket": True, "tpsl": "sl"}}
```
Reproducido con el SDK realmente instalado:
```
>>> order_type_to_wire({'trigger': {'triggerPx': str(64000.5), 'isMarket': True, 'tpsl': 'sl'}})
CRASH ValueError Unknown format code 'f' for object of type 'str'
>>> order_type_to_wire({'trigger': {'triggerPx': str(None), ...}})
CRASH ValueError Unknown format code 'f' for object of type 'str'
>>> order_type_to_wire({'trigger': {'triggerPx': 64000.5, ...}})     # float
{'trigger': {'isMarket': True, 'triggerPx': '64000.5', 'tpsl': 'sl'}}
```
(el SDK ya hace `float_to_wire(order_type["trigger"]["triggerPx"])`, `signing.py:162`).
**Por qué:** la ronda 1 lo clasificó P1 "porque HL no está activo"; es P0 el día que se active, y HL es el único venue legal para el dueño. Encadenado con HL-03 produce el peor camino posible: entrada llena → SL falla → TP falla → `BOTH_PROTECTIVES_FAILED_emergency_close` (`order_engine.py:357`) → MARKET **sin** `reduceOnly` → si el mercado ya movió la posición, se abre la contraria. Además `str(None)` = `"None"` cuando `stop_price` es `None`, así que ni siquiera falla de forma legible.
**Fix:** `triggerPx: float(order.stop_price)` redondeado con la regla de tick, y validar `stop_price is not None` antes de construir la orden.
**Verificado cómo:** ejecutado `py -3.12` contra el SDK 0.22.0 (trazas arriba).

---

### [P0] hyperliquid-05 — Agent wallet: `info` se consulta con la dirección del agent → posiciones vacías y balance 0 **con HTTP 200, sin ningún error**
**Archivo:** `exchange/hyperliquid_client.py:76-79`, `:207`, `:237`, `:332`, `:354`
**Evidencia:**
```python
# :75-79
account = Account.from_key(self._private_key)
self._wallet = account.address                    # PISA settings.hyperliquid_wallet_address
self._exchange = Exchange(account, base_url)      # sin account_address=
```
Doc oficial (`api/nonces-and-api-wallets`, WebFetch): *"A common pitfall is to use the agent wallet which leads to an empty result."*
Verificado **en vivo** contra mainnet con una dirección que nunca ha depositado (equivalente a una agent wallet recién creada):
```
agent addr: 0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A
clearinghouseState -> assetPositions: []   marginSummary: {'accountValue': '0.0', 'totalRawUsd': '0.0', ...}
openOrders         -> []
=> get_positions()       devuelve []
=> get_account_balance() devuelve {'balance': 0.0, 'available': 0.0}
=> market_close()        el bucle no encuentra la posición -> return None
```
**Por qué:** es un fallo **silencioso** en 4 rutas a la vez, no una excepción: `get_positions()` (reconciliación de riesgo, `_flatten_all`, `close_all_positions`) ve el portfolio vacío; `get_account_balance()` devuelve equity 0; `get_open_orders()` vacío ⇒ `reconcile_orders_with_exchange` borra del tracking **todas** las órdenes vivas por "stale"; `cancel_all_orders()` itera sobre una lista vacía y reporta `{"cancelled": 0}` como éxito. Con dinero real y SL/TP colocados, el bot cree estar plano mientras la posición existe. La configuración correcta (`HYPERLIQUID_WALLET_ADDRESS` = master) **existe en settings** (`config/settings.py:189-191`) y el código la sobreescribe en la línea 77.
**Fix:** `Exchange(account, base_url, account_address=settings.hyperliquid_wallet_address or account.address)`; guardar `self._wallet = settings.hyperliquid_wallet_address or account.address` (nunca pisar la master); y validar al arrancar que `clearinghouseState(self._wallet)` devuelve `accountValue > 0` — si es 0 con clave configurada, **abortar** con log CRITICAL en vez de operar a ciegas.
**Verificado cómo:** doc oficial vía WebFetch; consulta real a `api.hyperliquid.xyz/info` con `clearinghouseState`/`openOrders` (200 OK, cuerpos transcritos arriba); lectura del bucle de `market_close` en el SDK instalado.

---

### [P0] hyperliquid-06 — `use_testnet=True` (valor por DEFECTO) firma y envía órdenes a **MAINNET** con dinero real
**Archivo:** `exchange/hyperliquid_client.py:72`, `exchange/hyperliquid_ws.py:19`, `:32`, `config/settings.py:194`, `:312-330`, `main.py:81-84`
**Evidencia:**
```python
# hyperliquid_client.py:72 — siempre mainnet
base_url = constants.MAINNET_API_URL           # el SDK trae TESTNET_API_URL
# hyperliquid_ws.py:19 — constante de módulo
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
# hyperliquid_ws.py:32 — el parámetro existe y NO se usa en ninguna línea del fichero
def __init__(self, symbols=None, wallet_address="", use_testnet: bool = False):
# main.py:81-84 — main ni siquiera lo pasa
self.websocket = HyperliquidWebSocket(symbols=..., wallet_address=...)
# config/settings.py:194
use_testnet: bool = True                        # <-- DEFECTO
# config/settings.py:312-330 — apply_testnet() solo tiene ramas strike y binance
if self.is_strike: ...  elif self.is_binance: ...
```
**Por qué:** la configuración por defecto del proyecto es testnet. Un usuario que arranque HL en live "seguro" estará firmando contra mainnet con fondos reales, sin un solo aviso. Además la URL determina el dominio de firma (`sign_l1_action(..., self.base_url == MAINNET_API_URL)`, `source` `"a"`/`"b"`): mezclar URLs no produce órdenes "inofensivas", produce firmas inválidas. La doc oficial confirma que el único cambio necesario es la URL base (`https://api.hyperliquid-testnet.xyz`, `wss://api.hyperliquid-testnet.xyz/ws`).
**Fix:** `base_url = constants.TESTNET_API_URL if settings.use_testnet else constants.MAINNET_API_URL`; `self._ws_url` por instancia en el WS y pasar `use_testnet` desde `main.py`; rama `elif self.is_hyperliquid:` en `apply_testnet()`; y loguear en **CRITICAL** la URL efectiva al arrancar. Nota operativa (research §10): el faucet de testnet exige haber depositado antes en mainnet con la misma dirección, y el libro de testnet no sirve para medir slippage — testnet solo valida firma y plumbing.
**Verificado cómo:** leídos los cinco ficheros; `TESTNET_API_URL` confirmado en `site-packages/hyperliquid/utils/constants.py`.

---

### [P0] hyperliquid-07 — Sin timeout HTTP: una conexión colgada congela el hilo para siempre (el fix de la ronda 1 para Binance no llegó a HL)
**Archivo:** `exchange/hyperliquid_client.py:73`, `:79`
**Evidencia:**
```python
# hyperliquid_client.py:73,79 — ningún timeout=
self._info = Info(base_url, skip_ws=True)
self._exchange = Exchange(account, base_url)
```
SDK instalado (`site-packages/hyperliquid/api.py:12-23`):
```python
def __init__(self, base_url=None, timeout=None):
    self.session = requests.Session()
    self.timeout = timeout                      # None
def post(self, url_path, payload=None):
    response = self.session.post(url, json=payload, timeout=self.timeout)   # timeout=None => infinito
```
`Exchange.__init__` construye a su vez su propio `Info(base_url, True, meta, spot_meta, perp_dexs, timeout)` — también sin timeout.
**Por qué:** `requests` con `timeout=None` **bloquea indefinidamente**. Cada llamada del cliente corre en `loop.run_in_executor(None, ...)`, es decir en el `ThreadPoolExecutor` por defecto (`min(32, cpu+4)` hilos, compartido con todo el proceso). Un puñado de POSTs colgados a `api.hyperliquid.xyz` agota el pool y **todas** las corutinas que hagan `await self._run_sync(...)` quedan esperando para siempre: el bot deja de reconciliar, de cancelar y de cerrar, con posiciones abiertas y sin ningún log de error. La ronda 1 arregló exactamente esto para Binance (`binance_client.py:116`, `ClientTimeout(total=15, connect=5, sock_read=10)`, marcado "Arreglado" en `02_exchange_execution.md`); HL se quedó fuera. El SDK soporta `timeout=` desde 0.18.0, está a un argumento.
**Fix:** `Info(base_url, skip_ws=True, timeout=10)` y `Exchange(account, base_url, account_address=..., timeout=10)`; `ThreadPoolExecutor` propio y acotado en vez del executor por defecto; y `asyncio.wait_for` alrededor de las llamadas críticas.
**Verificado cómo:** leído el fuente del SDK instalado (`api.py`); comportamiento de `requests` con `timeout=None` es documentado y determinista.

---

### [P0] hyperliquid-08 — En live con Hyperliquid ni arranca el WS de usuario ni se reconcilian posiciones por REST: el risk manager queda ciego
**Archivo:** `main.py:197-203`, `main.py:723`
**Evidencia:**
```python
# main.py:197-203
has_api_key = (
    self.settings.api_private_key          # Strike
    or os.getenv("BINANCE_API_KEY", "")    # Binance
)
if has_api_key and not self.paper:
    tasks.append(asyncio.create_task(self.websocket.connect_user()))
# main.py:723
elif not self.dry_run and (self.settings.api_private_key or self.settings.binance_api_secret):
    positions_data = await self.client.get_positions()
```
`grep -n "hyperliquid_private_key\|HYPERLIQUID_PRIVATE_KEY" main.py` → **0 resultados**.
**Por qué:** con `exchange_venue="hyperliquid"` el usuario configura `HYPERLIQUID_PRIVATE_KEY`; ni `api_private_key` (Strike) ni `BINANCE_API_KEY` estarán puestas ⇒ ambas condiciones son falsas ⇒ **`connect_user()` no se lanza jamás** y **`get_positions()` no se llama jamás**. Resultado: `risk_manager._positions` permanece vacío para siempre mientras hay dinero real en riesgo → drawdown, exposición total, `max_open_positions` y el halt por drawdown operan sobre un portfolio vacío. Es la condición perfecta para acumular posiciones sin ningún freno. Sumado a HL-05 y HL-09, no hay **ninguna** vía por la que el estado real del exchange llegue al bot.
**Fix:** una sola propiedad `client.is_authenticated` (o `settings` según venue activo) que sustituya las tres condiciones ad-hoc (`main.py:198`, `:723` y la de `_setup_ws_callbacks`).
**Verificado cómo:** leído + `grep`; condiciones booleanas deterministas.

---

### [P0] hyperliquid-09 — Los fills se descartan por partida doble: `userFills`/`userEvents` mal parseados **y** el evento emitido sin la clave `"e"`
**Archivo:** `exchange/hyperliquid_ws.py:211-226`, `:228-245`; `main.py:337-339`
**Evidencia:**
```python
# hyperliquid_ws.py:211-213 — userFills es {isSnapshot, user, fills:[...]}, NO una lista
if channel == "userFills":
    for fill in data if isinstance(data, list) else [data]:
        coin = fill.get("coin", "")        # el dict raíz no tiene "coin" -> "" -> símbolo "-USD", px "0"
# hyperliquid_ws.py:230 — userEvents es un dict {"fills":[...]}, nunca una lista
if isinstance(data, list):                 # nunca True -> no se procesa nada
# hyperliquid_ws.py:215 — el payload emitido NO lleva "e"
await self._emit("ORDER_TRADE_UPDATE", {"s": ..., "S": ..., "i": ..., "x": "TRADE", ...})
# main.py:337-339 — el handler exige data["e"]
if data.get("e") == "ORDER_TRADE_UPDATE":     # None != "..." -> return silencioso
# exchange/binance_ws.py:238-242 — Binance reenvía el payload CRUDO, que sí trae "e"
```
**Por qué:** son **dos** filtros independientes que matan el 100% de los fills. Arreglar solo el parseo (que es lo que pedía `02-P1-13`) no sirve: el `if` de `main.py:339` los seguiría descartando. Sin fills no hay `Trade`, ni PnL realizado, ni fee real, ni slippage medido, ni `trade_db`, ni atribución por estrategia (Kelly), ni notificación Telegram, ni limpieza de `_active_orders`. Y no hay ningún log de "evento ignorado" que lo delate.
**Fix:** parsear `data.get("fills", [])` en ambos canales; añadir `"e": "ORDER_TRADE_UPDATE"` al dict emitido (el resto encaja: `main.py` hace `data.get("o", data)` y `order_engine.on_order_update` lee `i/x/X/L/l/n/rp/S/s/T`); y descartar el primer mensaje con `isSnapshot: true` para no recontabilizar fills antiguos como nuevos en cada reconexión.
**Verificado cómo:** leídos los tres ficheros y comparados con la ruta Binance equivalente; formato de `userFills`/`userEvents` según doc oficial (`api/websocket/subscriptions`).

---

### [P0] hyperliquid-10 — El order book por WebSocket nunca llega: `KeyError(0)` en cada mensaje de profundidad
**Archivo:** `exchange/hyperliquid_ws.py:179-186`, `main.py:321-330`
**Evidencia:**
```python
# hyperliquid_ws.py:179-180 — emite listas de DICTS
bids = [{"p": l["px"], "q": l["sz"]} for l in levels[0]] if len(levels) > 0 else []
await self._emit("depth", {"s": bs_symbol, "bids": bids, "asks": asks, ...})
# main.py:321-329 — el consumidor indexa por POSICIÓN (formato Binance: [["px","sz"], ...])
bids = data.get("b", data.get("bids", []))
ob = OrderBook(..., bids=[OrderBookLevel(float(b[0]), float(b[1])) for b in bids[:10]], ...)
```
Reproducido:
```
>>> b = {'p': '78500.0', 'q': '0.5'}; float(b[0])
CRASH KeyError KeyError(0)
```
**Por qué:** la excepción la traga `_emit` (`hyperliquid_ws.py:56-57`, `logger.error("hl_ws_callback_error")`), así que no rompe nada visible: simplemente **`market_data.on_orderbook` nunca se llama**. Todo lo que depende del libro queda muerto en HL: microprice, Order Book Imbalance, `spread_bps`, `book_depth_usd` (que alimenta al `smart_router` y por tanto la decisión MARKET vs LIMIT), y la validación de profundidad previa a una IOC. Efecto secundario: un `logger.error` por **cada** actualización de libro de cada símbolo — cientos por minuto de ruido que además engordan el JSONL que el commit `2a67ec2` acaba de poner bajo logrotate. Es el gemelo exacto del P1-04 de la ronda 1 en Binance (`bids/asks` vs `b/a`), que allí sí se arregló.
**Fix:** emitir el formato canónico de Binance (`"b": [[px, sz], ...]`, `"a": [...]`) desde `hyperliquid_ws.py`, o hacer `OrderBookLevel` tolerante a ambos (`b["p"] if isinstance(b, dict) else b[0]`). Además, subir el `logger.error` de `_emit` a un contador con muestreo para que un bug de formato no inunde el log.
**Verificado cómo:** `py -3.12` (traza arriba); leídos los dos ficheros y contrastado con `binance_ws.py:148-155`.

---

### [P0] hyperliquid-11 — El histórico inicial se construye con precio 0.0 y solo 10 trades: los indicadores arrancan degenerados
**Archivo:** `exchange/hyperliquid_client.py:189-197`; `core/market_data.py:100`, `:184-192`
**Evidencia:** el cliente devuelve los dicts **crudos** de HL y el consumidor solo entiende el formato Binance:
```python
# hyperliquid_client.py:196-197
data = await self._run_sync(_fetch)     # self._info.recent_trades(coin)
return data[:limit]                     # claves: coin, hash, px, side, sz, tid, time, users
# core/market_data.py:187-192
"price":    float(t.get("price", t.get("p", 0))),   # HL usa "px" -> 0.0
"quantity": float(t.get("qty",   t.get("q", 0))),   # HL usa "sz" -> 0.0
```
Y el endpoint devuelve como máximo **10** trades, no 1000 (`market_data.py:100` pide `limit=1000`). Medido hoy en vivo:
```
BTC n=10 span=0.735s | ETH n=10 span=0.867s | SOL n=10 span=16.1s | ADA n=10 span=17.6s
keys = ['coin','hash','px','side','sz','tid','time','users']
```
**Por qué:** doble fallo. (1) Todas las barras OHLC del DataFrame base valen **0.0** → EMA=0, ATR=0, z-score con std=0 → NaN/inf. La MR opera sobre z-score y el sizing usa ATR para el SL: con ATR=0 el stop sale al precio de entrada. No hay ningún guard que rechace un DataFrame de ceros. (2) Aun arreglando el parseo, 10 trades cubren **0,7-18 segundos** → 1 barra de 1m; las estrategias necesitan 20-50 barras. El único parche que rellenaría el histórico (`seed_from_binance`) está detrás de `if self.use_binance:` (`main.py:173`) y en la rama HL `use_binance = False` (`main.py:85`) → nunca corre. En Binance el mismo `limit=1000` sí devuelve 1000 aggTrades, así que el bug es específico del venue y hoy está enmascarado.
**Fix:** en la rama HL sembrar con `get_klines(symbol, "1m", limit=360)` (`candleSnapshot` admite hasta 5000 velas) en vez de con trades; generalizar `seed_from_binance` a `seed_history(venue)`; y añadir un guard en `_init_symbol`: si `df["close"].max() <= 0` → error y no arrancar.
**Verificado cómo:** 4 llamadas reales a `POST /info {"type":"recentTrades"}` (mainnet) con conteo y span impresos; lectura cruzada de los dos ficheros.

---

### [P0] hyperliquid-12 — El SDK **instalado** (0.22.0) revienta al construir `Info()`: la rama HL no arranca hoy en la máquina del dueño
**Archivo:** `exchange/hyperliquid_client.py:72-73`, `:84-86`; `requirements.txt:14`
**Evidencia:** ejecutado con el entorno real (`py -3.12`), 2/2 veces:
```
>>> Info(constants.MAINNET_API_URL, skip_ws=True)
CRASH IndexError list index out of range      (site-packages/hyperliquid/info.py:48, base_info = spot_meta["tokens"][base])
```
```
requirements.txt:14 -> hyperliquid-python-sdk>=0.22.0     # permite la versión rota
requirements.lock:33 -> hyperliquid-python-sdk==0.24.0    # el lock sí está bien
```
**Por qué:** (a) `Exchange.__init__` construye su propio `Info`, así que el crash afecta también a la parte de trading; (b) `_ensure_sdk` solo captura `ImportError` (`:84`), este `IndexError` sube sin control y **desde dentro del event loop** (ver HL-20); (c) `deploy/install.sh` instala con `requirements.txt` (flotante), así que la restricción `>=0.22.0` no protege de nada; (d) que el entorno local tenga 0.22.0 y nadie lo haya notado es la prueba definitiva de que **la rama HL nunca se ha ejecutado**. Es además el issue #275 del SDK, sensible al estado del mercado spot de HL, así que puede reaparecer.
**Fix:** `hyperliquid-python-sdk>=0.24.0` en `requirements.txt` (research §11.2: ≥0.21.0 para `grouping`, ≥0.23.0 por el fix de `Info` y los timeouts), `pip install -U` en local, y ampliar el `except ImportError` a `except Exception` con log CRITICAL + estado "cliente HL no disponible" que impida arrancar en silencio. Pinear también `eth-account` a versión exacta (research §11.1: la firma EIP-712 cambió de API en 0.14.1).
**Verificado cómo:** ejecución real 2/2 con el SDK instalado; `requirements.txt` / `requirements.lock` leídos.

---

### [P0] hyperliquid-13 — `markPrice` recibe el **notional en USD**, no un precio: la exposición se calcula con un error de factor `size`
**Archivo:** `exchange/hyperliquid_client.py:220`; `main.py:738`; `core/types.py:186-190`; `risk/risk_manager.py:320`
**Evidencia:**
```python
# hyperliquid_client.py:216-223
"markPrice": p.get("positionValue", "0"),      # <-- notional USD, NO un precio
# main.py:738
mark_price=float(p.get("markPrice", 0))
# core/types.py:186-190
price = self.mark_price if self.mark_price > 0 else self.entry_price
return abs(self.size * price)                  # notional = size * "mark_price"
```
**Por qué:** `notional` pasa a ser `size × positionValue` = `size² × precio`. Con precios y `szDecimals` reales de hoy:
- BTC `0.00255 @ 78 510` → `positionValue` ≈ 200 → notional calculado **0,51 USD** en vez de 200 → **~390× infraestimado** ⇒ `_check_total_exposure` y `max_position_usd` no bloquean nada.
- ADA `1021 @ 0,196` → `positionValue` ≈ 200 → notional calculado **204 200 USD** en vez de 200 → **~1000× sobreestimado** ⇒ toda entrada en ADA bloqueada para siempre.
Además `pnl_pct = (mark − entry)/entry` sale absurdo para cualquier posición. La ronda 1 lo mencionó como un guion suelto ("(8) markPrice = positionValue") dentro de un P1; cuantificado, es P0.
**Fix:** derivarlo exacto: `mark = abs(float(positionValue)) / abs(size)`; o cachear `markPx` de `metaAndAssetCtxs` / del canal WS `activeAssetCtx`. Añadir test de invariante `abs(notional/positionValue − 1) < 0.01`.
**Verificado cómo:** leídos los cuatro ficheros; cifras calculadas con `szDecimals` y `markPx` medidos hoy en mainnet.

---

### [P1] hyperliquid-14 — Sin `cloid` y sin `get_order`: cero idempotencia y protectivas dimensionadas "a ojo"
**Archivo:** `exchange/hyperliquid_client.py:249-313`; `execution/order_engine.py:264-267`
**Evidencia:** `grep -n "cloid\|client_order_id\|get_order" exchange/hyperliquid_client.py` → **0 resultados**. El motor genera y pasa un `client_order_id` en todas las órdenes (`order_engine.py:101`, `:339`, `:352`, `:367`, `:596`) que el cliente HL **tira a la basura**.
```python
# order_engine.py:264-267
get_order = getattr(self.client, "get_order", None)
if get_order is None or not order.client_order_id:
    logger.warning("fill_status_unknown_assuming_filled", symbol=order.symbol, status=status)
    return order.quantity          # asume llenado TOTAL
```
Compárese con Binance, donde la ronda 1 (P0-02) implementó exactamente lo contrario (`binance_client.py:676-724`: `newClientOrderId` siempre + recuperación por `origClientOrderId` tras timeout).
**Por qué:** dos consecuencias. (1) Un `ReadTimeout` (que con HL-07 puede ser eterno) tras el cual la orden SÍ entró deja al bot sin forma de descubrirla: no hay cloid con el que preguntar, y `place_order` no reintenta ni recupera. Posición huérfana sin SL/TP. (2) `_await_entry_fill` siempre cae en `fill_status_unknown_assuming_filled` ⇒ el SL y el TP se dimensionan al tamaño **pedido**, no al ejecutado; con una IOC parcialmente llenada, HL responderá `reduceOnlyRejected` a unas protectivas sobredimensionadas y el motor irá al emergency close (que a su vez pierde `reduceOnly`, HL-03).
**Fix:** generar `Cloid.from_str(...)` de 128 bits a partir de `order.client_order_id` y pasarlo en cada `order()`; implementar `get_order(symbol, client_order_id=...)` sobre `POST /info {"type":"orderStatus","user":master,"oid":cloid}`; leer `totalSz` del status `filled` para dimensionar las protectivas.
**Verificado cómo:** `grep` en el cliente; lectura de `order_engine._await_entry_fill` y del path Binance equivalente.

---

### [P1] hyperliquid-15 — Una respuesta de error del exchange revienta con `AttributeError` en vez de propagar el motivo
**Archivo:** `exchange/hyperliquid_client.py:294`
**Evidencia:**
```python
status_data = result.get("response", {}).get("data", {})
```
HL devuelve, ante un error de acción, `{"status": "err", "response": "<mensaje>"}` — `response` es un **string**. Reproducido:
```
>>> {'status':'err','response':'Insufficient margin to place order.'}.get('response', {}).get('data', {})
CRASH AttributeError 'str' object has no attribute 'get'
```
**Por qué:** por esta vía llegan los errores más comunes (`Insufficient margin`, `Order must have minimum value of $10`, `Price must be divisible by tick size`, rate limit). En vez de un `RuntimeError` legible, el motor recibe un `AttributeError` opaco y el operador nunca ve la causa. Y ni siquiera es el único filtro: con `status:"ok"` global cada orden del batch trae su propio status en `statuses[]` (`resting`/`filled`/`error`), que el código sí mira (`:298-311`) pero sin mapear ninguno de los rechazos documentados (`minTradeNtlRejected`, `tickRejected`, `perpMarginRejected`, `badAloPxRejected`, `iocCancelRejected`, `badTriggerPxRejected`, `marketOrderNoLiquidityRejected`, `*OpenInterestCap*`, `reduceOnlyRejected`).
**Fix:** `if result.get("status") != "ok": raise RuntimeError(f"HL rejected: {result.get('response')}")` **antes** de tocar `["data"]`; y clasificar permanente vs transitorio para no reintentar en bucle un rechazo permanente (quema presupuesto de acciones, ver HL-22).
**Verificado cómo:** ejecutado en `py -3.12`; forma de la respuesta según doc oficial (`api/exchange-endpoint`, `api/error-responses`).

---

### [P1] hyperliquid-16 — El filtro de funding es 8× demasiado permisivo: HL cobra funding **cada hora**, los umbrales están calibrados a 8 h
**Archivo:** `exchange/hyperliquid_client.py:117`; `config/settings.py` (`funding_rate_warn` / `funding_rate_block`); `risk/risk_manager.py:166-182`
**Evidencia:**
```python
# config/settings.py — comentarios explícitos "por 8h"
funding_rate_warn:  float = 0.0001   # 1 bps/8h — reduce sizing 30%
funding_rate_block: float = 0.0005   # 5 bps/8h — bloquea entradas contra funding
# hyperliquid_client.py:117 — se mete el valor HORARIO de HL en ese mismo campo
"funding_rate": float(ctx.get("funding", 0)),
# risk_manager.py:173-177
if abs_rate >= self.config.funding_rate_block: ...    # compara horario contra umbral de 8h
```
Doc oficial (`trading/funding`, WebFetch): *"The funding rate on Hyperliquid is paid every hour"*; interés *"0.01% every 8 hours, which is 0.00125% every hour"*; cap *"4%/hour"*. Medido hoy en mainnet: `funding = 0.0000125` **idéntico** en BTC, ETH, SOL, ADA, BNB, DOGE y XRP — exactamente 0,00125%/h ⇒ el campo `funding` de `metaAndAssetCtxs` **es la tasa horaria** (la doc no lo etiqueta explícitamente; la coincidencia al dígito con el interés horario documentado no deja alternativa razonable).
**Por qué:** el funding basal de HL queda 8× por debajo del `warn`, así que el filtro **nunca** se activa en condiciones normales; solo saltaría con 0,0005/h = **4,4%/día**, un extremo brutal. En HL el bot pagaría funding adverso durante horas sin recortar sizing ni bloquear la entrada — justo el escenario que este filtro existe para evitar. Y la comparación entre venues queda rota: el mismo número significa cosas distintas según el venue.
**Fix:** normalizar en el cliente a una unidad común (p.ej. `funding_rate_8h = hourly * 8` en HL, `lastFundingRate` tal cual en Binance) y documentarlo en `core/types.py`; o guardar `funding_rate_annualized` y recalibrar los umbrales sobre eso. Añadir una regla de salida por funding extremo (cap 4%/h ⇒ $1000 de notional puede pagar $40/h).
**Verificado cómo:** doc oficial vía WebFetch (citas literales arriba); valor `0.0000125` medido en vivo en 7 símbolos.

---

### [P1] hyperliquid-17 — Sin heartbeat `{"method":"ping"}`: HL cierra el socket de usuario cada 60 s de silencio
**Archivo:** `exchange/hyperliquid_ws.py:69`, `:129`
**Evidencia:** `grep -n "ping\|pong" exchange/hyperliquid_ws.py` → solo `websockets.connect(HL_WS_URL, ping_interval=20)`, que envía **frames de protocolo WebSocket**, no un mensaje de aplicación. Doc oficial (`api/websocket/timeouts-and-heartbeats`): *"The server will close any connection if it hasn't sent a message to it in the last 60 seconds"*, y el cliente debe enviar `{"method": "ping"}` (respuesta `{"channel": "pong"}`).
**Por qué:** el canal de mercado recibe datos constantemente y sobrevive, pero **`connect_user` puede pasar minutos sin actividad** (sin fills) ⇒ HL corta cada 60 s ⇒ ciclo perpetuo de reconexión y resuscripción. En cada reconexión `userFills` reenvía un snapshot (`isSnapshot: true`) que, en cuanto se arregle HL-09, se reprocesaría como fills nuevos → **doble contabilidad de PnL**. Es un bug latente que se activa justo al arreglar el otro.
**Fix:** tarea `asyncio` que envíe `{"method":"ping"}` cada ~50 s en ambas conexiones; ignorar `channel == "pong"`; descartar el mensaje con `isSnapshot: true` salvo la primera vez y deduplicar fills por `tid`.
**Verificado cómo:** doc oficial vía WebFetch; `grep` del código.

---

### [P1] hyperliquid-18 — No se suscribe a `orderUpdates`: cancelaciones, rechazos y triggers nunca llegan al motor
**Archivo:** `exchange/hyperliquid_ws.py:131-138`
**Evidencia:**
```python
await ws.send(json.dumps({"method":"subscribe","subscription":{"type":"userEvents","user": self._wallet}}))
await ws.send(json.dumps({"method":"subscribe","subscription":{"type":"userFills","user": self._wallet}}))
```
No hay `orderUpdates`. Tampoco hay reconciliación REST tras reconectar (`connect_user` solo re-suscribe; los eventos ocurridos durante la desconexión **no** se reenvían).
**Por qué:** `orderUpdates` es el equivalente HL del `ORDER_TRADE_UPDATE` no-fill de Binance: entrega `{order, status, statusTimestamp}` con `canceled`, `rejected`, `triggered`, `marginCanceled`, `reduceOnlyCanceled`, `siblingFilledCanceled`… Sin él, el motor no se entera de que un SL fue rechazado, ni de que una orden fue cancelada por el propio exchange, ni del **self-trade prevention** de HL (doc oficial: *"Trades between the same address cancel the resting order instead of causing a fill"*, sin fee y sin aparecer en el feed) — la orden en reposo desaparece en silencio. `_active_orders` solo se limpia por antigüedad (`cleanup_stale_orders`, 300 s) o por `reconcile_orders_with_exchange`, que con HL-05 ve una lista vacía y borra **todo**.
**Fix:** suscribirse a `orderUpdates` y mapear los estados de rechazo a acciones (no reintentar los permanentes); y tras **cada** reconexión hacer reconciliación REST obligatoria: `openOrders` + `clearinghouseState` + `userFillsByTime` desde el último `tid` conocido. La verdad es el exchange, no el estado en memoria.
**Verificado cómo:** leído el código; catálogo de suscripciones y de estados de orden según doc oficial (`api/websocket/subscriptions`, `api/info-endpoint`).

---

### [P1] hyperliquid-19 — La IOC de mercado va a 100 bps sin ninguna validación de profundidad
**Archivo:** `exchange/hyperliquid_client.py:261-263`
**Evidencia:**
```python
result = self._exchange.market_open(coin, is_buy, sz, None, 0.01)   # slippage explícito 1%
```
SDK: `_slippage_price` calcula `px = mid * (1 ± slippage)` y **redondea correctamente** a 5 s.f. / `6 − szDecimals`. `grep -n "l2_snapshot\|get_orderbook" exchange/hyperliquid_client.py` → solo la definición de `get_orderbook`, nunca invocada desde `place_order`.
**Por qué:** el repo **no** cae en el `DEFAULT_SLIPPAGE = 0.05` del SDK (trampa (a) refutada), pero 0.01 = **100 bps** sigue siendo 11× el round-trip taker completo (9 bps, tier 0) y ~$10 sobre una cuenta de $1000 en una sola orden. En los símbolos configurados el libro es muy desigual: `dayNtlVlm` medido hoy = BTC $2 754 M, ETH $1 891 M, SOL $589 M, XRP $88 M, **DOGE $11,4 M, BNB $7,5 M, ADA $6,5 M**. En ADA/BNB/DOGE una IOC de $200 puede barrer varios niveles y llenarse cerca del límite del 1% sin que nadie lo impida. Efecto secundario: `_slippage_price` hace una llamada REST extra (`allMids`) **por cada orden**, bloqueante, dentro del executor y sin timeout (HL-07).
**Fix:** slippage 0,001-0,003 y, sobre todo, **pre-flight contra `l2Book`**: calcular el precio medio de ejecución del notional pedido y abortar si supera X bps. Reutilizar el mid ya conocido (`get_market_snapshot`) pasándolo como `px=` para evitar el `allMids` extra. Y, cuando el edge de la estrategia lo permita, entrar con `Alo` (maker, 1.5 bps) en vez de IOC.
**Verificado cómo:** leído el SDK instalado (`_slippage_price`); `dayNtlVlm` por símbolo medido en vivo contra `api.hyperliquid.xyz/info`; fees tier 0 según doc oficial (`trading/fees`).

---

### [P1] hyperliquid-20 — `_ensure_sdk()` bloquea el event loop ~1,7 s, y en cada llamada mientras falle
**Archivo:** `exchange/hyperliquid_client.py:62-86`, invocado desde `:102`, `:138`, `:162`, `:190`, `:202`, `:232`, `:250`
**Evidencia:**
```python
async def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
    self._ensure_sdk()                      # <-- SÍNCRONO, fuera del executor
    ...
    data = await self._run_sync(_fetch)     # solo el fetch va al executor
```
`Info.__init__` hace `spot_meta()` + `meta()` por HTTP antes de devolver. Medido hoy: `metaAndAssetCtxs` = 71 738 bytes en **1 675 ms**. Y si falla (HL-12) `self._info` sigue a `None` ⇒ **cada** llamada posterior reintenta y vuelve a bloquear.
```python
# :94-97 — además, API deprecada
loop = asyncio.get_event_loop()             # 3.12: usar asyncio.get_running_loop()
return await loop.run_in_executor(None, func, *args)   # executor por defecto, compartido
```
**Por qué:** ~1,7 s con el loop congelado significa no procesar ticks del WS, no ejecutar `_risk_monitor_loop` y no atender timeouts. Con 4 símbolos y un fallo persistente, el bot queda permanentemente atascado sin un log que lo explique.
**Fix:** un `async def _init()` llamado **una vez** desde `BotStrike.start()` (o mover `_ensure_sdk()` dentro de `_run_sync`), cachear el fallo con backoff, `asyncio.get_running_loop()` y un `ThreadPoolExecutor` propio y acotado.
**Verificado cómo:** medido con `time.time()` alrededor de los POST reales a mainnet; leído el código.

---

### [P1] hyperliquid-21 — SL y TP como órdenes independientes: HL ofrece `grouping="positionTpsl"` y no se usa
**Archivo:** `exchange/hyperliquid_client.py:270-282`; `execution/order_engine.py:331-355`
**Evidencia:** el motor coloca SL y TP en **dos** llamadas separadas (`_place_with_retry(sl_order)` y luego `_place_with_retry(tp_order)`), y el cliente HL usa `Exchange.order(...)`, que llama a `bulk_orders([order], builder)` con `grouping="na"` por defecto. El SDK instalado **sí** soporta lo necesario:
```python
def bulk_orders(self, order_requests, builder=None, grouping: Grouping = "na") -> Any:
```
**Por qué:** con `grouping="na"` los dos triggers son ajenos entre sí: cuando el SL se ejecuta, el TP **sigue vivo** como orden reduce-only huérfana. Con `positionTpsl` es el matching engine quien cancela el hermano (`siblingFilledCanceled`) y quien ajusta el tamaño al de la posición. Sin eso: (a) un TP huérfano puede cerrar la **siguiente** posición del mismo símbolo (mismo problema que `02-P1-12` en Binance, pero aquí con solución nativa disponible); (b) las huérfanas se acumulan contra el cap de **1000 órdenes abiertas**, y al llegar al cap HL rechaza nuevas reduce-only y trigger — es decir, el bot se autobloquea la capacidad de protegerse. Además, enviar entrada+SL+TP en un solo `bulk_orders` con `normalTpsl`/`positionTpsl` elimina de raíz la carrera "entrada llena → protectivas aún no puestas" y consume 1 acción en vez de 3.
**Fix:** implementar `place_bracket(entry, sl, tp)` en el cliente HL usando `bulk_orders([...], grouping="positionTpsl")` con `isMarket: true` y `reduceOnly: true` en los triggers. Riesgo operativo asociado (research §15.1): los TP/SL nativos on-chain se ejecutan aunque el bot esté caído — **un stop en memoria del bot no es un stop**.
**Verificado cómo:** leída la firma de `bulk_orders` en el SDK 0.22.0 instalado; leído `order_engine._place_protective_orders`; semántica de `grouping` según doc oficial (`api/exchange-endpoint`).

---

### [P1] hyperliquid-22 — `cancel_all_orders` firma N acciones en vez de una: gasta el presupuesto **por dirección**, que sí aplica a acciones
**Archivo:** `exchange/hyperliquid_client.py:331-346`
**Evidencia:**
```python
for o in open_orders:
    r = self._exchange.cancel(coin, oid)      # 1 acción firmada por orden
```
Doc oficial (`api/rate-limits-and-user-limits`, WebFetch): allowance de *"1 request per 1 USDC traded cumulatively since address inception"* con *"an initial buffer of 10000 requests"*; agotado, *"an address is allowed one request every 10 seconds"*. Y explícitamente: *"this rate limit only applies to actions, not info requests"*. Un batch cuenta **1 para el límite por IP** pero **n para el límite por dirección**.
**Por qué:** con $1000 y poco volumen, el presupuesto útil es prácticamente el buffer inicial de 10 000 acciones. Cancelar de una en una (más los reintentos ciegos de HL-15) lo consume mucho más rápido de lo necesario, y agotarlo deja al bot a **1 acción cada 10 segundos**: incapaz de cerrar una posición en un movimiento adverso. Nada en el código monitoriza `userRateLimit` (`grep userRateLimit` → 0).
**Fix:** usar `bulk_cancel` / `cancel_by_cloid` en una sola llamada; monitorizar `userRateLimit` (`nRequestsUsed` vs `nRequestsCap`) y alertar al 70%; prohibir el reintento en bucle de rechazos permanentes.
**Verificado cómo:** doc oficial vía WebFetch (citas literales); leído el bucle en el cliente.

---

### [P1] hyperliquid-23 — `midPx` viene `null` en 56 de 233 activos: `float(None)` revienta `get_market_snapshot`
**Archivo:** `exchange/hyperliquid_client.py:116`
**Evidencia:**
```python
"mid_price": float(ctx.get("midPx", 0)),   # .get devuelve None si la CLAVE existe con valor null
```
Medido hoy en mainnet: **56 de 233** activos tienen `midPx: null`; `markPx` nunca lo es.
```
>>> float(None)  ->  TypeError: float() argument must be a string or a real number, not 'NoneType'
```
**Por qué:** `midPx` es null cuando el libro está vacío o el activo está en halt/delisting. Si le ocurre a un símbolo operado, `get_market_snapshot` lanza `TypeError`, `update_snapshot` lo captura y **devuelve el snapshot anterior** (`core/market_data.py:317`) ⇒ el bot sigue operando con un precio congelado mientras el activo está parado, sin que `is_data_stale` se entere. En `_init_symbol` el símbolo queda con DataFrame vacío en silencio. Es exactamente el escenario JELLY (research §15.2): el activo se congela/deslista y tú sigues calculando señales sobre un precio muerto.
**Fix:** `mid = ctx.get("midPx") or ctx.get("markPx") or 0` con `float()` defensivo; si tampoco hay `markPx`, propagar un error explícito para que el guard de datos rancios bloquee el símbolo.
**Verificado cómo:** conteo real contra `api.hyperliquid.xyz/info` (56/233) y `float(None)` reproducido en `py -3.12`.

---

### [P2] hyperliquid-24 — Las fees correctas de HL solo existen por la ruta del bridge; el arranque por CLI usa las de Binance
**Archivo:** `server/bridge.py:280-288`; `main.py:1629-1631`
**Evidencia:**
```python
# bridge.py:284-287 — solo aquí
if exchange == "hyperliquid":
    settings.trading.maker_fee = 0.00015; settings.trading.taker_fee = 0.00045; settings.trading.slippage_bps = 2.0
# main.py:1629-1631 — el CLI usa el Settings por defecto, sin override por venue
bot = BotStrike(settings, dry_run=args.dry_run, paper=args.paper, use_binance=args.binance)
# config/settings.py — defaults calibrados a Binance
maker_fee: float = 0.0002    # 2 bps
taker_fee: float = 0.0004    # 4 bps
slippage_bps: float = 1.5    # "Binance Futures has deep book"
```
Doc oficial (`trading/fees`): perps tier 0 → taker **0,045%** (4,5 bps), maker **0,015%** (1,5 bps).
**Por qué:** con `EXCHANGE_VENUE=hyperliquid` por CLI el bot modela taker 4 bps donde el real es 4,5 (**+12,5%**) y maker 2 bps donde el real es 1,5 (**−25%**). En una estrategia cuyo edge se mide en bps, un round-trip taker-taker real de 9 bps modelado como 8 sobreestima el edge sistemáticamente. Y `slippage_bps=1.5` está justificado por la profundidad de Binance; para ADA/BNB/DOGE en HL ($6-11 M/día) es optimista por varios múltiplos.
**Fix:** mover el override de fees a `Settings` en función de `exchange_venue` (no al bridge), poner HL a 1.5/4.5 bps, y medir el slippage real por símbolo con `slippage_tracker` antes de subir tamaño. Considerar el referral (−4% hasta $25M de volumen) solo cuando esté confirmado en `userFees`; **no** adjuntar builder fee (es coste extra para el propio bot).
**Verificado cómo:** doc oficial vía WebFetch; leídos `bridge.py`, `main.py` y `settings.py`.

---

### [P2] hyperliquid-25 — `refresh_all` descarga el universo entero (233 activos, 70 KB) una vez POR SÍMBOLO cada 30 s
**Archivo:** `exchange/hyperliquid_client.py:105-121`; `core/market_data.py:485-488`; `main.py:788-793`
**Evidencia:**
```python
# market_data.py:487 — una tarea por símbolo
tasks = [self.update_snapshot(s.symbol) for s in self.settings.symbols]
# hyperliquid_client.py:106 — cada una baja TODO el universo y filtra un elemento
meta = self._info.meta_and_asset_ctxs()
```
Medido: `metaAndAssetCtxs` = **71 738 bytes**, **1 675 ms**, 233 activos. Con 4 símbolos cada 30 s ⇒ 8 llamadas/min × weight 20 = **160 weight/min** (de 1200 por IP) y ~**570 KB/min** de payload, para extraer 4 valores.
**Por qué:** no es un P0 (no revienta el presupuesto de 1200 weight/min por IP), pero es 4× más peso, 4× más latencia y 4× más ocupación del `ThreadPoolExecutor` de lo necesario, justo en el componente que ya tiene el problema del bloqueo (HL-20) y del timeout infinito (HL-07). El peso total estimado del bot en HL —snapshots 160 + `clearinghouseState` cada 2 s (60) + `openOrders` cada 10 s (120)— ronda **340/min**, cómodo; pero cualquier añadido futuro lo estrecha sin motivo.
**Fix:** cachear `meta_and_asset_ctxs()` con TTL (5-15 s) y servir los 4 símbolos de la misma respuesta; para el precio en caliente usar `allMids` (weight 2) o el canal WS `activeAssetCtx`.
**Verificado cómo:** tamaños y latencias medidos contra mainnet; pesos por endpoint según doc oficial (`api/rate-limits-and-user-limits`).

---

### [P2] hyperliquid-26 — Sin dead-man's switch (`scheduleCancel`) ni `expiresAfter`; leverage cross hardcodeado y sin validar contra `maxLeverage`
**Archivo:** `exchange/hyperliquid_client.py:372-381`; `grep scheduleCancel|schedule_cancel|set_expires_after exchange/` → 0
**Evidencia:**
```python
def _set():
    return self._exchange.update_leverage(leverage, coin, is_cross=True)   # cross fijo, sin validar
```
`config/settings.py:26` → `leverage: int = 2` (razonable). `maxLeverage` medido hoy: BTC 40, ETH 25, SOL 20, ADA 10, BNB 10, DOGE 10, XRP 20 — 2x es válido en todos, pero nada lo comprueba.
**Por qué:** (a) sin `scheduleCancel`, si el proceso muere (crash, OOM, deploy, corte de red) las órdenes en reposo se quedan en el libro indefinidamente. La doc lo define como *dead-man's switch* (mín. 5 s en el futuro, **máx 10 usos por día UTC**); en un bot live no es opcional. ⚠️ Cancela **todas** las órdenes, TP/SL incluidos, así que la secuencia de kill debe cerrar la posición antes o aceptar quedar plano. (b) Sin `expiresAfter` (~5-10 s), un request encolado puede ejecutarse tarde a un precio ya obsoleto. (c) `is_cross=True` comparte colateral: una liquidación se lleva toda la cuenta; con $1000 y varios símbolos, **isolated** limita el daño a la posición (research §7.1, checklist F). Y la doc avisa: *"Leverage is only checked upon opening a position"* — después es responsabilidad del bot vigilarlo.
**Fix:** `schedule_cancel(now + 120_000)` re-armado cada ~60 s (contando los 10 usos/día), `set_expires_after(now + 8_000)` antes de cada acción, `is_cross=False` (isolated) y `assert leverage <= maxLeverage[coin]` antes de `update_leverage`.
**Verificado cómo:** `grep` en `exchange/`; `maxLeverage` medido en vivo; semántica de `scheduleCancel`/`expiresAfter` y la cita sobre leverage según doc oficial (`api/exchange-endpoint`, `trading/margining`).

---

### [P2] hyperliquid-27 — `close()` es un `pass`: la `requests.Session` y el hilo del WS quedan colgando
**Archivo:** `exchange/hyperliquid_client.py:383-385`; `exchange/hyperliquid_ws.py:247-251`
**Evidencia:**
```python
async def close(self) -> None:
    """Cleanup — SDK doesn't need explicit close."""
    pass
```
El SDK **sí** abre recursos: `API.__init__` crea `self.session = requests.Session()` (para `Info` y otra para `Exchange`), y el `Info` con WS lanza un hilo (issue #54 del SDK: el programa no termina si no se cierra).
**Por qué:** en un proceso que se reinicia con `deploy/update.sh` no es fatal, pero deja sockets TCP a medio cerrar y, sumado a la sesión REST que caduca a ~3 min de inactividad (issue #293 del SDK), es la receta para que la primera orden después de un rato de calma falle por conexión muerta — precisamente en un bot de baja frecuencia. El comentario del docstring es además falso.
**Fix:** `self._info.session.close()` y `self._exchange.session.close()` (defensivos con `getattr`), y un heartbeat REST ligero (`allMids`, weight 2) cada ~2 min para mantener viva la sesión.
**Verificado cómo:** leído `site-packages/hyperliquid/api.py:12-16`; issues del SDK citados en `tasks/research_r2_hyperliquid_execution.md` §11.4.

---

### [P3] hyperliquid-28 — Detalles menores confirmados
- `exchange/hyperliquid_client.py:134` — `index_price = mark_price`: el oracle price (que es lo que HL usa para funding y margen, y que sí está disponible como `oraclePx` en `metaAndAssetCtxs`) se ignora.
- `exchange/hyperliquid_ws.py:88-92` y `:188-204` — se suscribe al canal `candle` y emite eventos `"kline"` que **nadie consume**: `grep -n 'websocket.on(' main.py` lista `trade`, `depth`, `depthUpdate`, `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, `markPrice`, `markPriceUpdate` — no hay `kline`. Además el handler pone `"x": True` (barra cerrada) en **todas** las actualizaciones, incluida la vela en curso; si algún día se conecta un consumidor, contabilizará barras parciales como cerradas.
- `exchange/hyperliquid_ws.py` no emite nunca `ACCOUNT_UPDATE` ni `markPrice` ⇒ `on_account_update` (equity) y `on_markprice_update` (funding) de `main.py:372-408` están muertos en HL.
- `exchange/hyperliquid_client.py:119` — `volume_24h` recibe `dayNtlVlm`, que es **notional en USD**, mientras que en Binance el mismo campo lleva volumen en unidades base. Cualquier umbral cruzado sobre `volume_24h` compara peras con manzanas.
- `main.py:1633-1636` — el banner de paper imprime `Datos de mercado: STRIKE MAINNET` cuando el venue es HYPERLIQUID (la condición solo contempla Binance y Strike).
- `exchange/__init__.py` — `ExchangeClient = Union[StrikeClient, BinanceClient]` sigue sin incluir `HyperliquidClient` (`02-P3-21` abierto).

---

## Tabla resumen

| ID | Sev | Título | Archivo:línea |
|---|---|---|---|
| hyperliquid-01 | **P0** | El bridge/UI ofrece HL pero arranca Binance (`use_binance=True` fijo) | `server/bridge.py:314` |
| hyperliquid-02 | **P0** | 100% de órdenes revientan: `sz` sin `szDecimals` → `float_to_wire` ValueError; sin 5 s.f. ni mín. $10 | `exchange/hyperliquid_client.py:256` |
| hyperliquid-03 | **P0** | MARKET pierde `reduce_only` → el flatten de shutdown puede abrir posición contraria desnuda | `exchange/hyperliquid_client.py:259` |
| hyperliquid-04 | **P0** | `02-P1-13` abierto: `triggerPx=str(...)` → ValueError en todos los SL/TP | `exchange/hyperliquid_client.py:274` |
| hyperliquid-05 | **P0** | Agent wallet en `info` → posiciones vacías y balance 0 con HTTP 200, sin error | `exchange/hyperliquid_client.py:77` |
| hyperliquid-06 | **P0** | `use_testnet=True` (defecto) opera en MAINNET real | `exchange/hyperliquid_client.py:72` |
| hyperliquid-07 | **P0** | Sin timeout HTTP (`timeout=None`) → cuelgue infinito del executor | `exchange/hyperliquid_client.py:73` |
| hyperliquid-08 | **P0** | En live HL ni WS de usuario ni reconciliación REST → risk manager ciego | `main.py:198`, `main.py:723` |
| hyperliquid-09 | **P0** | Fills descartados dos veces: parseo de `userFills` + falta la clave `"e"` | `exchange/hyperliquid_ws.py:212`, `main.py:339` |
| hyperliquid-10 | **P0** | Order book WS nunca llega: `KeyError(0)` en cada mensaje de profundidad | `exchange/hyperliquid_ws.py:179`, `main.py:328` |
| hyperliquid-11 | **P0** | Histórico inicial con precio 0.0 y solo 10 trades (`recentTrades` cap 10) | `exchange/hyperliquid_client.py:197` |
| hyperliquid-12 | **P0** | SDK instalado 0.22.0 revienta al construir `Info()`; `requirements.txt` lo permite | `requirements.txt:14` |
| hyperliquid-13 | **P0** | `markPrice` = `positionValue` (notional) → exposición con error de factor `size` | `exchange/hyperliquid_client.py:220` |
| hyperliquid-14 | P1 | Sin `cloid` ni `get_order`: cero idempotencia; protectivas dimensionadas a ojo | `exchange/hyperliquid_client.py:249` |
| hyperliquid-15 | P1 | Error del exchange (`response` string) → `AttributeError` opaco | `exchange/hyperliquid_client.py:294` |
| hyperliquid-16 | P1 | Funding horario contra umbrales de 8 h → filtro 8× permisivo, nunca dispara | `exchange/hyperliquid_client.py:117` |
| hyperliquid-17 | P1 | Sin heartbeat `{"method":"ping"}` (corte a 60 s) ni dedup de `isSnapshot` | `exchange/hyperliquid_ws.py:129` |
| hyperliquid-18 | P1 | Sin `orderUpdates` ni reconciliación REST tras reconectar | `exchange/hyperliquid_ws.py:131` |
| hyperliquid-19 | P1 | IOC a 100 bps sin validación de profundidad (`l2Book` nunca se consulta) | `exchange/hyperliquid_client.py:261` |
| hyperliquid-20 | P1 | `_ensure_sdk()` bloquea el event loop 1,7 s y reintenta en cada llamada | `exchange/hyperliquid_client.py:62` |
| hyperliquid-21 | P1 | SL/TP sin `grouping="positionTpsl"` → huérfanas, sin cancelación mutua nativa | `exchange/hyperliquid_client.py:270` |
| hyperliquid-22 | P1 | `cancel_all_orders` firma N acciones en vez de `bulk_cancel` | `exchange/hyperliquid_client.py:340` |
| hyperliquid-23 | P1 | `midPx: null` en 56/233 activos → `TypeError`, snapshot congelado | `exchange/hyperliquid_client.py:116` |
| hyperliquid-24 | P2 | Fees HL solo por la ruta bridge; el CLI usa las de Binance | `server/bridge.py:284` |
| hyperliquid-25 | P2 | `metaAndAssetCtxs` (70 KB) descargado una vez por símbolo cada 30 s | `exchange/hyperliquid_client.py:106` |
| hyperliquid-26 | P2 | Sin `scheduleCancel` ni `expiresAfter`; cross fijo, sin validar `maxLeverage` | `exchange/hyperliquid_client.py:379` |
| hyperliquid-27 | P2 | `close()` es un `pass`: sesiones REST y hilo WS sin cerrar | `exchange/hyperliquid_client.py:383` |
| hyperliquid-28 | P3 | Detalles: `index_price`, canal `candle` sin consumidor, `volume_24h`, banner, `Union` | varios |

**Totales:** P0 = 13 · P1 = 10 · P2 = 4 · P3 = 1 (**28 hallazgos**).

---

## Plan de cambios EXACTO y ORDENADO para que paper y live en HL sean seguros

Cada fase es un gate: no pasar a la siguiente sin verificar la anterior. Estimaciones para un desarrollador que ya conoce el repo.

### Fase 0 — Que la rama HL exista de verdad (2,5 h)
| # | Cambio | Hallazgo | h |
|---|---|---|---|
| 0.1 | `requirements.txt` → `hyperliquid-python-sdk>=0.24.0`; `eth-account` pineado exacto; `pip install -U` local | HL-12 | 0,5 |
| 0.2 | `bridge.py:314` → `use_binance=(exchange == "binance")`; test que afirme `isinstance(engine.client, HyperliquidClient)` | HL-01 | 0,5 |
| 0.3 | `_ensure_sdk`: `except Exception` + log CRITICAL + estado "cliente no disponible"; `Info/Exchange` con `timeout=10`; init asíncrono una sola vez; `get_running_loop()` + executor propio | HL-07, HL-12, HL-20 | 1,0 |
| 0.4 | `apply_testnet()` rama HL + `base_url`/`HL_WS_URL` por `use_testnet`; log CRITICAL de la URL efectiva al arrancar | HL-06 | 0,5 |

### Fase 1 — Que las órdenes salgan válidas (5 h) — **sin esto no hay nada más**
| # | Cambio | Hallazgo | h |
|---|---|---|---|
| 1.1 | `SymbolMeta` cacheado de `meta()`: `szDecimals`, `maxLeverage`; refresco al arrancar y cada 6 h | HL-02 | 1,0 |
| 1.2 | `_round_sz` (floor a `szDecimals`) y `_round_px` (`round(float(f"{px:.5g}"), 6-szDecimals)`); aplicarlos en las 4 ramas de `place_order` | HL-02 | 1,0 |
| 1.3 | Pre-flight: `sz*px >= 10` USD y `sz > 0`, si no → `ValueError` sin enviar | HL-02 | 0,5 |
| 1.4 | `triggerPx` a `float` redondeado; validar `stop_price is not None` | HL-04 | 0,5 |
| 1.5 | Sustituir `market_open` por `Exchange.order(..., {"limit":{"tif":"Ioc"}}, reduce_only=order.reduce_only)` con `px` de `_slippage_price` y `slippage=0.002` | HL-03, HL-19 | 1,0 |
| 1.6 | Manejo de respuesta: `status != "ok"` → `RuntimeError` con el string; mapear los rechazos documentados a permanente/transitorio | HL-15 | 1,0 |

### Fase 2 — Que el bot vea la realidad (5,5 h)
| # | Cambio | Hallazgo | h |
|---|---|---|---|
| 2.1 | `Exchange(..., account_address=master)` y `self._wallet = master`; abortar si `accountValue == 0` con clave configurada | HL-05 | 1,0 |
| 2.2 | `markPrice = abs(positionValue)/abs(size)`; test de invariante | HL-13 | 0,5 |
| 2.3 | `client.is_authenticated` y sustituir las 3 condiciones de credenciales en `main.py` | HL-08 | 0,5 |
| 2.4 | WS usuario: parsear `data["fills"]` en `userFills` y `userEvents`, añadir `"e"`, dedup por `tid`, descartar `isSnapshot` posterior | HL-09, HL-17 | 1,0 |
| 2.5 | WS mercado: emitir `"b"`/`"a"` en formato `[[px, sz], ...]` | HL-10 | 0,5 |
| 2.6 | Heartbeat `{"method":"ping"}` cada 50 s en ambas conexiones; ignorar `pong` | HL-17 | 0,5 |
| 2.7 | Suscribir `orderUpdates`; reconciliación REST obligatoria tras cada reconexión (`openOrders` + `clearinghouseState` + `userFillsByTime`) | HL-18 | 1,0 |
| 2.8 | `midPx or markPx or error`; `funding` normalizado a 8 h (o anualizado) y umbrales recalibrados | HL-23, HL-16 | 0,5 |

### Fase 3 — Que se pueda operar sin datos degenerados (2 h)
| # | Cambio | Hallazgo | h |
|---|---|---|---|
| 3.1 | `seed_history(venue)`: en HL sembrar con `get_klines("1m", 360)`; guard `df["close"].max() > 0` o no arrancar | HL-11 | 1,5 |
| 3.2 | Normalizar `get_recent_trades` al formato Binance (`p`/`q`/`T`/`m`) como red de seguridad | HL-11 | 0,5 |

### Fase 4 — Seguridad de ejecución para live (6 h)
| # | Cambio | Hallazgo | h |
|---|---|---|---|
| 4.1 | `cloid` de 128 bits derivado de `client_order_id` en toda orden + `get_order` vía `orderStatus` | HL-14 | 1,5 |
| 4.2 | `place_bracket()` con `bulk_orders(..., grouping="positionTpsl")`, `isMarket:true`, `reduceOnly:true` | HL-21 | 1,5 |
| 4.3 | Pre-flight de profundidad contra `l2Book`: abortar si el coste estimado supera X bps | HL-19 | 1,0 |
| 4.4 | `scheduleCancel` re-armado cada 60 s a T+120 s (máx 10 usos/día) + `set_expires_after(+8 s)` | HL-26 | 1,0 |
| 4.5 | `bulk_cancel` en `cancel_all_orders`; monitor de `userRateLimit` con alerta al 70% | HL-22 | 0,5 |
| 4.6 | `is_cross=False` (isolated) + validación contra `maxLeverage`; fees por venue en `Settings` | HL-26, HL-24 | 0,5 |

### Fase 5 — Higiene y validación (4,5 h)
| # | Cambio | Hallazgo | h |
|---|---|---|---|
| 5.1 | Cache TTL de `meta_and_asset_ctxs` compartido entre símbolos | HL-25 | 0,5 |
| 5.2 | `close()` real (cerrar sesiones) + heartbeat REST cada 2 min | HL-27 | 0,5 |
| 5.3 | Detalles P3 (`index_price`=`oraclePx`, `volume_24h`, canal `candle`, banner, `Union`) | HL-28 | 0,5 |
| 5.4 | **Tests con payloads reales de HL**: `place_order` (los 4 tipos) con `meta` mock, parser del WS, respuesta de error, redondeo por símbolo. Hoy: 0 tests tocan `HyperliquidClient` | todos | 3,0 |

**Total: ~25,5 h de desarrollo** + validación operativa (no es código):
- Testnet (firma, formato, TP/SL agrupado, reconexión, dead-man's switch) — 1 día. **Requisito bloqueante**: el faucet exige un depósito previo en mainnet con la misma dirección.
- Paper contra datos de mainnet reales con coste 4,5 bps taker + funding real — ≥2 semanas.
- Live con tamaño mínimo ($10-20/orden) ≥2 semanas comparando fills reales vs paper antes de escalar.
- Agent wallet **nueva** (nunca reutilizar dirección), `account_address` = master, recordatorio a 170 días (expira a 180), NTP verificado en el CT.

---

## Veredicto

1. **La integración Hyperliquid nunca se ha ejecutado.** Un solo commit la creó y nada la ha tocado desde entonces; el SDK del entorno local (0.22.0) revienta en la primera llamada y nadie lo había notado. Todo lo de abajo es consecuencia de eso.
2. **Y no puede ejecutarse desde el producto**: el bridge fuerza `use_binance=True`, así que pulsar "Hyperliquid" en la UI arranca Binance. El único venue legal para un residente en España es, hoy, inalcanzable con un clic.
3. **Si se activara tal cual, no colocaría ni una orden**: `sz = size_usd/price` sin redondear hace que `float_to_wire` lance `ValueError` en el 100% de los casos, medido con precios reales de hoy en BTC, ETH, SOL y ADA.
4. **El camino que sí es alcanzable es el peligroso**: el flatten de shutdown usa tamaños que vienen del exchange (ya wire-válidos) y `market_open` descarta `reduce_only` → puede **abrir** una posición contraria desnuda. Con `close_positions_on_shutdown=True`, en cada deploy.
5. **Las dos trampas de la investigación**: (a) `DEFAULT_SLIPPAGE=0.05` **refutada** — el repo pasa `0.01` explícito (aunque 100 bps sin validar profundidad sigue siendo demasiado); (b) `market_close` **confirmada en su causa raíz**: verificado en vivo que `clearinghouseState` con una dirección de agent devuelve `200 OK` con posiciones vacías y balance `0.0`, sin error alguno.
6. **`02-P1-13` sigue abierto entero**, no parcialmente: SL/TP crashean, no hay redondeo, la wallet del agent pisa la master, los fills no se parsean, testnet se ignora y no hay heartbeat.
7. **El bot sería ciego en live**: ni WS de usuario, ni reconciliación REST, ni fills, ni order book. El risk manager operaría sobre un portfolio vacío con dinero real en riesgo.
8. **Lo que está bien**: el mapeo de símbolos es correcto, `market_open` recibe slippage explícito (no cae en el 5%), el leverage por defecto (2x) es prudente, el bridge sí conoce las fees reales de HL, y la interfaz async del cliente es la correcta para no romper el resto del sistema.
9. **Coste de arreglarlo**: ~25,5 h de desarrollo repartidas en 6 fases, más un mes de validación escalonada (testnet → paper mainnet → live mínimo).
10. **Recomendación**: **NO activar Hyperliquid**, ni en paper por la ruta de producto, hasta completar las Fases 0-3. Con las estrategias congeladas (`fb073a1`) no hay urgencia de ejecución: el orden correcto es arreglar la ejecución **primero** y buscar edge **después**, no al revés.
