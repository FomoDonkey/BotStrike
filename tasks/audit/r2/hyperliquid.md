# Auditoría R2 — Área: Hyperliquid

Fecha: 2026-08-31
Auditor: agente r2 (área `hyperliquid`)
Alcance: `exchange/hyperliquid_client.py`, `exchange/hyperliquid_ws.py`, `tasks/hyperliquid_api_research.md`, uso desde `main.py` / `server/bridge.py`, contraste con SDK instalado (`hyperliquid-python-sdk==0.22.0`) y doc oficial.

> **Estado: EN PROGRESO** — hallazgos añadidos incrementalmente.

## Entorno verificado

- `hyperliquid-python-sdk` **0.22.0** instalado en `C:\Users\edgar\AppData\Local\Programs\Python\Python312\Lib\site-packages\hyperliquid`
- `eth-account` 0.13.7

---

## Hallazgos

### [P0] hyperliquid-01 — El histórico inicial en Hyperliquid se construye con precio 0.0 y volumen 0.0 (campos `px`/`sz` nunca leídos)
**Archivo:** `core/market_data.py:184-192` (consumidor) + `exchange/hyperliquid_client.py:189-197` (productor)
**Evidencia:**
```python
# exchange/hyperliquid_client.py:189-197 — devuelve los dicts CRUDOS de Hyperliquid
async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
    data = await self._run_sync(_fetch)      # self._info.recent_trades(coin)
    return data[:limit]                      # [{'coin','side','px','sz','time','hash','tid','users'}]

# core/market_data.py:187-192 — solo entiende el formato Binance
"price": float(t.get("price", t.get("p", 0))),   # HL usa "px"  -> 0.0
"quantity": float(t.get("qty", t.get("q", 0))),  # HL usa "sz"  -> 0.0
```
Ejecutado (`py -3.12`) con un payload REAL de `POST /info {"type":"recentTrades","coin":"BTC"}`:
```
[{'timestamp': 1788136773.802, 'price': 0.0, 'quantity': 0.0},
 {'timestamp': 1788136774.0,   'price': 0.0, 'quantity': 0.0}]
```
**Por qué es un problema:** `_init_symbol` construye el DataFrame base con `_trades_to_ohlcv(trades)` → todas las barras OHLC valen **0.0**. `Indicators.compute_all` corre encima: EMA=0, ATR=0, `zscore` con std=0 → NaN/inf. La MR opera sobre z-score; el sizing usa ATR para el SL. Con ATR=0 el stop-loss calculado es el precio de entrada (o distancia 0) → sizing infinito o rechazo, y el z-score sobre precios 0 no significa nada. No hay ningún guard que rechace un DataFrame de ceros. Además `seed_from_binance` (el único parche que rellenaría el histórico) está detrás de `if self.use_binance:` (`main.py:172`) y en la rama HL `self.use_binance = False` (`main.py:85`) → nunca se ejecuta.
**Fix:** normalizar en `HyperliquidClient.get_recent_trades` al formato Binance (`{"p": t["px"], "q": t["sz"], "T": t["time"], "m": t["side"]=="A"}`), o mejor: sembrar el histórico con `get_klines` (candleSnapshot) en vez de con trades para HL. Añadir un guard en `_init_symbol`: si `df["close"].max() <= 0` → error y no arrancar.
**Verificado cómo:** ejecutado con `py -3.12` sobre payload real descargado del endpoint público `https://api.hyperliquid.xyz/info` (200 OK).

---

### [P0] hyperliquid-02 — `recentTrades` de Hyperliquid devuelve como máximo **10** trades (no 1000): el bot arranca sin histórico y nunca hace warm-up
**Archivo:** `exchange/hyperliquid_client.py:189-197`, `core/market_data.py:100`
**Evidencia:** medido en vivo contra mainnet:
```
BTC n=10 span_sec=2.895
ETH n=10 span_sec=5.390
ADA n=10 span_sec=79.822
SOL n=10 span_sec=13.275
```
```python
# core/market_data.py:100
trades = await self.client.get_recent_trades(symbol, limit=1000)   # HL devuelve 10
# exchange/hyperliquid_client.py:197
return data[:limit]        # no hay paginación ni fallback a klines
```
**Por qué es un problema:** aun arreglando `hyperliquid-01`, 10 trades cubren **3-80 segundos** → `_trades_to_ohlcv` produce 1-2 barras de 1m. Las estrategias necesitan `mr_lookback` (20-50) barras y `Indicators` necesita ≥ EMA slow. El bot arrancaría en HL sin datos y tardaría ~1 h en tener barras suficientes, construidas a 1 barra/min desde el WS, sin ningún aviso. En Binance el mismo `limit=1000` sí devuelve 1000 aggTrades, así que el bug es específico del venue y está enmascarado hoy.
**Fix:** en la rama HL, sembrar con `get_klines(symbol, "1m", limit=360)` (el endpoint `candleSnapshot` sí soporta hasta 5000 velas) en lugar de `get_recent_trades`. Generalizar `seed_from_binance` a `seed_history(venue)` y llamarlo para cualquier venue, no solo `use_binance`.
**Verificado cómo:** 4 llamadas reales a `POST /info {"type":"recentTrades"}` (mainnet), conteo y span temporal impresos.

---

### [P0] hyperliquid-03 — En live con Hyperliquid el WebSocket de usuario NUNCA se arranca: cero fills, cero reconciliación
**Archivo:** `main.py:197-203`
**Evidencia:**
```python
# main.py:197-203
has_api_key = (
    self.settings.api_private_key      # Strike key
    or os.getenv("BINANCE_API_KEY", "")  # Binance key
)
if has_api_key and not self.paper:
    tasks.append(asyncio.create_task(self.websocket.connect_user()))
```
`grep -n "HYPERLIQUID_PRIVATE_KEY" main.py` → 0 resultados.
**Por qué es un problema:** con `exchange_venue="hyperliquid"` el usuario configura `HYPERLIQUID_PRIVATE_KEY`; ni `api_private_key` (Strike) ni `BINANCE_API_KEY` estarán puestos → `has_api_key` es falsy → **`connect_user()` no se lanza nunca**. El bot manda órdenes reales pero jamás recibe `userFills`: `on_order_update` no se llama, `_active_orders` no se limpia, no hay precio de fill real, ni slippage, ni PnL realizado, ni atribución por estrategia (Kelly), ni detección de SL disparado. La reconciliación por REST (`get_positions` cada 10 s) es el único hilo que ve la realidad. Es distinto y adicional a 02-P1-13 (que solo señalaba el *parseo* de `userFills`): aquí la tarea ni siquiera existe.
**Fix:** `has_api_key = bool(settings.api_private_key or os.getenv("BINANCE_API_KEY") or settings.hyperliquid_private_key)`. Mejor: preguntar al cliente (`self.client.has_credentials`) en vez de leer env vars de otros venues.
**Verificado cómo:** leído + `grep`; ruta de código determinista y trivialmente comprobable.

---

### [P0] hyperliquid-04 — 02-P1-13 SIGUE ABIERTO Y SIN TOCAR: `triggerPx` como `str` revienta con `ValueError` antes de firmar (todos los SL/TP)
**Archivo:** `exchange/hyperliquid_client.py:270-282`
**Evidencia:** el código no ha cambiado desde la ronda 1 (`git log --oneline -- exchange/hyperliquid_client.py` → último cambio pre-auditoría):
```python
# exchange/hyperliquid_client.py:274 y :280
{"trigger": {"triggerPx": str(order.stop_price), "isMarket": True, "tpsl": "sl"}},
```
Reproducido con el SDK **realmente instalado** (`hyperliquid-python-sdk==0.22.0`):
```
>>> order_type_to_wire({'trigger': {'triggerPx': str(64000.5), 'isMarket': True, 'tpsl': 'sl'}})
CRASH ValueError Unknown format code 'f' for object of type 'str'
>>> order_type_to_wire({'trigger': {'triggerPx': 64000.5, ...}})   # con float
{'trigger': {'isMarket': True, 'triggerPx': '64000.5', 'tpsl': 'sl'}}
```
(el SDK ya hace `float_to_wire(order_type["trigger"]["triggerPx"])` en `hyperliquid/utils/signing.py:162`)
**Por qué es un problema:** ronda 1 lo clasificó P1 "porque HL no está activo". Es P0 el día que se active, y HL es **el único venue legal** para el dueño. Cada SL/TP lanza `ValueError`; el order engine cae al camino de "emergency close" → el bot abre y cierra al instante, pagando taker fee ×2 + slippage en cada ciclo. Además `str(None)` = `"None"` cuando `stop_price` es `None`, así que ni siquiera falla de forma legible.
**Fix:** `triggerPx: float(order.stop_price)` + validar `stop_price is not None` antes de construir la orden.
**Verificado cómo:** ejecutado `py -3.12` contra el SDK instalado (v0.22.0); traza de excepción reproducida arriba.

---

### [P0] hyperliquid-05 — Toda orden MARKET en Hyperliquid pierde `reduce_only`: los cierres pueden ABRIR posición contraria (regresión del fix de "naked positions" de la ronda 1)
**Archivo:** `exchange/hyperliquid_client.py:258-263`
**Evidencia:**
```python
# exchange/hyperliquid_client.py:259-263
if order.order_type == OrderType.MARKET:
    # SDK market_open places IOC limit with slippage
    result = self._exchange.market_open(
        coin, is_buy, sz, None, 0.01  # 1% slippage tolerance
    )                                  # <-- order.reduce_only NUNCA se pasa
```
SDK instalado (`hyperliquid/exchange.py:225-240`):
```python
def market_open(self, name, is_buy, sz, px=None, slippage=DEFAULT_SLIPPAGE, cloid=None, builder=None):
    px = self._slippage_price(name, is_buy, slippage, px)
    return self.order(name, is_buy, sz, px, order_type={"limit": {"tif": "Ioc"}},
                      reduce_only=False, ...)      # <-- HARDCODED False
```
Los cuatro cierres del motor son MARKET + `reduce_only=True` (`order_engine.py:338`, `:351`, `:366` emergency close, `:595` flatten en shutdown).
**Por qué es un problema:** la ronda 1 (commit `b3dbf75`, "naked positions") endureció exactamente esto en Binance. En HL la bandera se descarta silenciosamente: si la posición ya está cerrada (SL disparado, cierre parcial, doble evento de salida, reconciliación con lag de 10 s), la MARKET de cierre **abre una posición nueva del lado contrario, sin SL ni TP**, con el tamaño completo. Es el escenario exacto que la ronda 1 se propuso eliminar. Y `close_positions_on_shutdown=True` lo dispara en cada deploy.
**Fix:** no usar `market_open`. Construir la orden a mano con `Exchange.order(coin, is_buy, sz, px_slippage, {"limit": {"tif": "Ioc"}}, reduce_only=order.reduce_only)`, calculando `px_slippage` como hace `_slippage_price` (o llamando a `self._exchange._slippage_price(coin, is_buy, slip)`). Alternativa para cierres puros: `market_close(coin, sz)` (que sí pone `reduce_only=True`).
**Verificado cómo:** leído el fuente del SDK instalado v0.22.0 (`site-packages/hyperliquid/exchange.py:225-240`) + `grep` de los cuatro `reduce_only=True` del motor.

---

### [P0] hyperliquid-06 — Los fills se descartan aunque el parseo se arregle: el evento emitido no lleva `"e"` y `main.py` filtra por `data["e"]`
**Archivo:** `exchange/hyperliquid_ws.py:215-226` y `:236-245`; `main.py:337-339`
**Evidencia:**
```python
# exchange/hyperliquid_ws.py:215 — payload emitido SIN la clave "e"
await self._emit("ORDER_TRADE_UPDATE", {
    "s": bs_symbol, "S": ..., "i": ..., "x": "TRADE", "X": "FILLED", "L": ..., "l": ..., ...
})
# main.py:337-339 — el handler exige data["e"]
async def on_order_update(data: Dict):
    if data.get("e") == "ORDER_TRADE_UPDATE":     # None != "ORDER_TRADE_UPDATE" -> return
        order_data = data.get("o", data)
# exchange/binance_ws.py:238-242 — Binance reenvía el payload CRUDO, que sí trae "e"
event_type = data.get("e", "")
if event_type == "ORDER_TRADE_UPDATE":
    await self._emit("ORDER_TRADE_UPDATE", data)
```
**Por qué es un problema:** es un **segundo** filtro que mata los fills de HL, independiente del bug de parseo de `userFills` señalado en 02-P1-13. Aunque se arregle `data["fills"]`, el `if` de `main.py:339` seguiría descartándolos: sin `Trade`, sin PnL realizado, sin fee real, sin slippage, sin `trade_db`, sin notificación Telegram, sin Kelly. Nadie lo detectaría porque no hay log de "evento ignorado".
**Fix:** añadir `"e": "ORDER_TRADE_UPDATE"` al dict emitido en ambos sitios de `hyperliquid_ws.py` (el resto encaja: `main.py` hace `data.get("o", data)` y cae al dict plano, y `order_engine.on_order_update` lee `i/x/X/L/l/n/rp/S/s/T`).
**Verificado cómo:** leído los tres ficheros y comparado con la ruta Binance equivalente; el filtro es una comparación de igualdad determinista.

---

### [P0] hyperliquid-07 — Sin redondeo a `szDecimals` ni a 5 cifras significativas: toda orden con precio/tamaño "bonito" es rechazada por el exchange
**Archivo:** `exchange/hyperliquid_client.py:249-289`
**Evidencia:** el cliente pasa `sz` y `limit_px` en crudo; `grep -n "szDecimals\|sig\|round\|Decimal" exchange/hyperliquid_client.py` → **0 resultados**.
```python
sz = order.quantity                                        # :256, sin redondear
result = self._exchange.order(coin, is_buy, sz, order.price or 0, ...)   # :265-269
```
Regla oficial (tick-and-lot-size): *"Prices can have up to 5 significant figures, but no more than `MAX_DECIMALS - szDecimals` decimal places where `MAX_DECIMALS` is 6 for perps"*; *"Sizes are rounded to the `szDecimals` of that asset"*; *"Integer prices are always allowed"*.
`szDecimals`/`maxLeverage` reales medidos hoy en mainnet (`POST /info {"type":"metaAndAssetCtxs"}`):
```
BTC szDec 5 maxLev 40 | ETH szDec 4 maxLev 25 | SOL szDec 2 maxLev 20
ADA szDec 0 maxLev 10 | BNB szDec 3 maxLev 10 | DOGE szDec 0 maxLev 10 | XRP szDec 0 maxLev 20
```
**Por qué es un problema:** `ADA szDecimals = 0` → el tamaño debe ser un **entero de ADA**; el sizing produce `size_usd/price` = p.ej. `77.0345` → rechazo. Para BTC, `MAX_DECIMALS - szDecimals = 6-5 = 1` decimal y 5 s.f. → un `limit_px` de `78023.4567` es doblemente inválido. El comparativo es demoledor: la ronda 1 (P0-01) construyó para Binance `parse_symbol_filters`, `round_quantity`, `round_price`, `_normalize_order_params` y validación de `minNotional` (`binance_client.py:434-505`) — **nada de eso existe en el cliente HL**. Además la doc oficial exige un **valor mínimo de orden de 10 USD** y no hay ninguna comprobación (`minNotional` de HL no está modelado). Resultado: entradas rechazadas de forma intermitente y, peor, un SL rechazado deja la posición desnuda.
**Fix:** cachear `meta()` al arrancar → `{coin: szDecimals, maxLeverage}`; `sz = round(qty, szDecimals)` (floor); `px = round(float(f"{px:.5g}"), 6 - szDecimals)` (misma fórmula que `Exchange._slippage_price`, que ya lo hace bien y por eso el precio de las MARKET sí sale válido); rechazar `sz*px < 10` con `ValueError` como hace el path Binance con `minNotional`; validar `leverage <= maxLeverage` antes de `update_leverage`.
**Verificado cómo:** doc oficial vía WebFetch (tick-and-lot-size, error-responses/mínimo 10 USD); `szDecimals` reales medidos contra `api.hyperliquid.xyz/info`; lectura del fuente del SDK (`_slippage_price` aplica la fórmula correcta, `order()` no).

---

### [P1] hyperliquid-08 — Una respuesta de error del exchange revienta con `AttributeError` en vez de propagar el mensaje real
**Archivo:** `exchange/hyperliquid_client.py:294`
**Evidencia:**
```python
# exchange/hyperliquid_client.py:294
status_data = result.get("response", {}).get("data", {})
```
Hyperliquid devuelve, en error de acción, `{"status": "err", "response": "<mensaje>"}` (string, no dict). Reproducido:
```
>>> {'status':'err','response':'Insufficient margin to place order.'}.get('response', {}).get('data', {})
CRASH AttributeError 'str' object has no attribute 'get'
```
**Por qué es un problema:** los errores más comunes de HL (`Insufficient margin`, `Order must have minimum value of $10`, `Price must be divisible by tick size`, `Order has invalid size`, rate limit) llegan por esta vía. En vez de un `RuntimeError` con el motivo, el motor recibe un `AttributeError` opaco → `_place_with_retry` lo trata como fallo genérico y reintenta N veces contra un error permanente, quemando peso de rate limit, y el operador nunca ve la causa en los logs.
**Fix:** `if result.get("status") != "ok": raise RuntimeError(f"HL order rejected: {result.get('response')}")` antes de tocar `["data"]`. Clasificar permanente vs transitorio para no reintentar los permanentes.
**Verificado cómo:** ejecutado en `py -3.12`; forma de la respuesta según doc oficial (exchange-endpoint / error-responses).

---

### [P1] hyperliquid-09 — El filtro de funding es ~8× demasiado permisivo en Hyperliquid: HL cobra funding **cada hora**, los umbrales están calibrados a 8 h
**Archivo:** `config/settings.py` (`funding_rate_warn`, `funding_rate_block`), `exchange/hyperliquid_client.py:117`, `risk/risk_manager.py:166-182`
**Evidencia:**
```python
# config/settings.py — comentarios explícitos "por 8h"
funding_rate_warn: float = 0.0001   # 1 bps/8h — reduce sizing 30%
funding_rate_block: float = 0.0005  # 5 bps/8h — bloquear entradas contra funding
# exchange/hyperliquid_client.py:117 — se mete el valor HORARIO de HL en ese mismo campo
"funding_rate": float(ctx.get("funding", 0)),
# risk/risk_manager.py:173-177
if abs_rate >= self.config.funding_rate_block: ...   # compara HORARIO contra umbral de 8h
```
Doc oficial (Funding): *"The funding rate on Hyperliquid is paid every hour"*; componente de interés *"0.01% every 8 hours, which is 0.00125% every hour"*; cap *"4%/hour"*. Medido hoy en mainnet: `funding` = **0.0000125** para BTC/ETH/SOL/BNB/DOGE — exactamente 0.00125%/h → confirma que el campo `funding` de `metaAndAssetCtxs` **es la tasa horaria**.
**Por qué es un problema:** el funding basal de HL (0.0000125/h) queda 8× por debajo del `warn` (0.0001) → el filtro **nunca** se activa en condiciones normales, y solo lo haría con un funding de 0.0005/h = **0.4%/8h = 4.4%/día**, un extremo brutal. Es decir: en HL el bot pagaría funding adverso durante horas sin recortar sizing ni bloquear la entrada, justo el escenario que este filtro existe para evitar. La comparación entre venues (Binance = 8h) también queda rota: el mismo número significa cosas distintas según el venue.
**Fix:** normalizar en el cliente a una unidad común (p.ej. tasa por 8 h: `funding_rate = hourly * 8` en HL, `lastFundingRate` tal cual en Binance) y documentarlo en `core/types.py:230`; o mejor, guardar `funding_rate_annualized` y calibrar los umbrales sobre eso. Recalibrar también con `predictedFundings`.
**Verificado cómo:** doc oficial vía WebFetch (trading/funding); valor `funding=0.0000125` medido en vivo, coincide al dígito con la tasa de interés horaria documentada.

---

### [P1] hyperliquid-10 — Modelo de costes calibrado a Binance: en Hyperliquid el taker cuesta 4.5 bps, no 4 bps (y el slippage no está calibrado)
**Archivo:** `config/settings.py` (`maker_fee`, `taker_fee`, `slippage_bps`)
**Evidencia:**
```python
maker_fee: float = 0.0002           # 2 bps — Binance Futures maker
taker_fee: float = 0.0004           # 4 bps — Binance Futures taker (was 5 bps Strike)
slippage_bps: float = 1.5           # 1.5 bps — Binance Futures has deep book
```
Doc oficial (trading/fees): perps tier 0 → **taker 0.045%** (4.5 bps), **maker 0.015%** (1.5 bps).
**Por qué es un problema:** no hay override de fees por venue. En HL el taker real es **+12.5%** sobre el modelado y el maker es **-25%**. La estrategia MR es de scalping con edge en unidades de bps: un backtest/Kelly/breakeven calculado con 4 bps sobre un venue de 4.5 bps sobreestima el edge sistemáticamente en 1 bp por round-trip (2 taker = 9 bps reales vs 8 bps modelados). Además `slippage_bps=1.5` está justificado por la profundidad de Binance; el book de HL para ADA/DOGE es mucho más fino (dayNtlVlm ADA = $6.8 M vs BTC $1.85 B, medido hoy) → el slippage real será varias veces mayor en los símbolos pequeños.
**Fix:** mover fees/slippage a config por venue (`fees[venue][symbol]`), poner HL a maker 1.5 bps / taker 4.5 bps, y medir el slippage real por símbolo con `slippage_tracker` antes de subir tamaño. Considerar el descuento por volumen y el referral (4% off) solo cuando esté confirmado en `userFees`.
**Verificado cómo:** doc oficial vía WebFetch (trading/fees); `dayNtlVlm` por símbolo medido en vivo contra `api.hyperliquid.xyz/info`. **Matiz:** `server/bridge.py:238-242` SÍ pone las fees correctas de HL (1.5/4.5 bps) — pero solo por esa vía; el arranque por CLI (`main.py` con `exchange_venue="hyperliquid"`) usa las de Binance.

---

### [P0] hyperliquid-11 — El bridge acepta `exchange=hyperliquid` pero SIEMPRE arranca el motor en Binance (`use_binance=True` fijo): el venue mostrado es mentira
**Archivo:** `server/bridge.py:265`, `main.py:71`
**Evidencia:**
```python
# server/bridge.py:234-242 — lo único que cambia el "exchange" son las fees
def _build_settings(exchange: str = "binance") -> Settings:
    settings.trading.exchange_venue = exchange
    if exchange == "hyperliquid":
        settings.trading.maker_fee = 0.00015; settings.trading.taker_fee = 0.00045; ...
# server/bridge.py:262-267 — y luego se ignora el venue
state.engine = BotStrike(settings=settings, dry_run=is_dry_run, paper=is_paper,
                         use_binance=True)      # <-- HARDCODED
# main.py:71
self._venue = ExchangeVenue.BINANCE if use_binance else settings.exchange_venue_enum
```
El test que "cubre" esto solo comprueba el campo de settings, no el venue efectivo:
```python
# tests/test_bridge_round2.py:162
assert starts[0]["settings"].trading.exchange_venue == "hyperliquid"
```
**Por qué es un problema:** el dueño reside en España y Binance cerró en jul-2026 (bloqueo regulatorio documentado en `audit_2026-08-29.md`). Pulsar "Hyperliquid" en la UI produce un motor que se conecta a `fapi.binance.com` y firma con claves de Binance: se cree que se opera en el único venue legal y no es así. Además, `HyperliquidClient`/`HyperliquidWebSocket` **nunca se instancian por esta ruta** → toda la integración HL está muerta en producción y ningún test de integración la toca (el único test HL comprueba un campo de un dataclass). El bug se llevó por delante también las fees correctas: se calibran para HL pero se ejecuta en Binance.
**Fix:** `use_binance=(exchange == "binance")` en `start_engine`, y afirmar en el test `isinstance(engine.client, HyperliquidClient)` (o `engine._venue == ExchangeVenue.HYPERLIQUID`), no el campo de settings.
**Verificado cómo:** leído los tres ficheros; la ruta es una asignación constante, sin ramas.

---

### [P0] hyperliquid-12 — El SDK **instalado** (0.22.0) revienta al construir `Info()` contra mainnet: la rama HL no arranca hoy en la máquina del dueño
**Archivo:** `exchange/hyperliquid_client.py:72-73`, `requirements.txt:14`
**Evidencia:** ejecutado con el entorno real (`py -3.12`, `hyperliquid-python-sdk==0.22.0`):
```
>>> Info(constants.MAINNET_API_URL, skip_ws=True)
Traceback (most recent call last):
  File "site-packages/hyperliquid/info.py", line 48, in __init__
    base_info = spot_meta["tokens"][base]
IndexError: list index out of range
```
(reproducido 3/3 veces). Causa medida contra mainnet: `spotMeta` devuelve `universe`=326 y `tokens`=497, pero **21 pares referencian índices de token de hasta 864**; el 0.22.0 indexa por posición (`tokens[base]`) en vez de por `index`. Instalado 0.24.0 en un directorio aparte y verificado que **sí funciona** (`OK ms 1501, assets 558`), porque cambia a `token_by_index = {t["index"]: t for t in spot_meta["tokens"]}`.
```
requirements.txt:14 -> hyperliquid-python-sdk>=0.22.0     # permite la versión rota
requirements.lock:33 -> hyperliquid-python-sdk==0.24.0    # el lock sí está bien
```
**Por qué es un problema:** (a) el entorno de desarrollo local tiene 0.22.0 → cualquier intento de probar HL muere en la primera llamada, lo que confirma que **la rama HL nunca se ha ejecutado**; (b) `_ensure_sdk` solo captura `ImportError` (`:84`), así que este `IndexError` sube sin control; (c) `deploy/install.sh:27` instala con `requirements.txt` (flotante) — la restricción `>=0.22.0` no protege de nada, y CI hace lo mismo; (d) el fallo depende del estado del mercado spot de HL, así que puede reaparecer con cualquier versión que indexe mal.
**Fix:** subir el mínimo a `hyperliquid-python-sdk>=0.24.0` en `requirements.txt`, `pip install -U` en el entorno local, y ampliar el `except ImportError` a `except Exception` con log crítico + estado "cliente no disponible" (no seguir arrancando en silencio).
**Verificado cómo:** ejecución real 3/3 con el SDK instalado; instalación paralela de 0.24.0 en el scratchpad y ejecución OK; `spotMeta` real descargado y contados los 21 índices fuera de rango.

---

### [P0] hyperliquid-13 — `markPrice` recibe el **notional en USD**, no un precio: los límites de exposición se calculan con un error de factor `size` (02-P1-13 punto 8 sigue abierto e infravalorado)
**Archivo:** `exchange/hyperliquid_client.py:220`, `core/types.py:185-190`, `risk/risk_manager.py:320-338`
**Evidencia:**
```python
# exchange/hyperliquid_client.py:216-223
positions.append({
    "symbol": bs_symbol,
    "positionAmt": str(size),
    "entryPrice": p.get("entryPx", "0"),
    "markPrice": p.get("positionValue", "0"),   # <-- notional USD, NO precio
    ...
})
# main.py:739  ->  mark_price=float(p.get("markPrice", 0))
# core/types.py:186-190
price = self.mark_price if self.mark_price > 0 else self.entry_price
return abs(self.size * price)                    # notional = size * "mark_price"
# risk/risk_manager.py:320
total_exposure = sum(p.notional for p in self._positions.values())
```
**Por qué es un problema:** `notional` pasa a ser `size × positionValue` = `size² × precio`. Cuantificado con `szDecimals`/precios reales de hoy:
- BTC 0.01 @ 78 024 → `positionValue` = 780 → notional calculado **7.80 USD** en vez de 780 → **100× infraestimado** → `_check_total_exposure` y `max_position_usd` no bloquean nada; el bot puede apalancarse sin límite.
- ADA 500 @ 0.1947 → `positionValue` = 97 → notional calculado **48 675 USD** en vez de 97 → **500× sobreestimado** → toda entrada en ADA queda bloqueada para siempre.
Además `pnl_pct = (mark-entry)/entry` sale ≈ **-99%** para cualquier long en BTC, con lo que cualquier lógica que mire `pnl_pct` ve una pérdida catastrófica falsa. La ronda 1 lo listó como un guion suelto ("(8) markPrice = positionValue") dentro de un P1; medido, es P0 puro.
**Fix:** `"markPrice"` debe ser un precio. HL no lo da en `assetPositions`; usar `entryPx` como aproximación no vale para el PnL. Correcto: leer `markPx` de `metaAndAssetCtxs` (o del canal WS `activeAssetCtx`) y cachearlo por símbolo; alternativamente derivarlo (`abs(float(positionValue)) / abs(size)`), que es exacto. Añadir un test de invariante: `abs(notional/positionValue - 1) < 0.01`.
**Verificado cómo:** leídos los cuatro ficheros; cifras calculadas con `szDecimals` y precios reales medidos hoy contra `api.hyperliquid.xyz/info`.

---

### [P0] hyperliquid-14 — En live con Hyperliquid tampoco se reconcilian posiciones por REST: el risk manager queda completamente ciego
**Archivo:** `main.py:723`
**Evidencia:**
```python
# main.py:723 — la condición de "live" nunca incluye HL
elif not self.dry_run and (self.settings.api_private_key or self.settings.binance_api_secret):
    positions_data = await self.client.get_positions()
```
`grep -n "hyperliquid_private_key" main.py` → 0 resultados.
**Por qué es un problema:** es el gemelo de `hyperliquid-03` en el `_risk_monitor_loop`. Con `exchange_venue="hyperliquid"` y solo `HYPERLIQUID_PRIVATE_KEY` puesta, la rama no entra → `self.risk_manager._positions` nunca se rellena desde el exchange. Sumado a `hyperliquid-03` (WS de usuario sin arrancar) y `hyperliquid-06` (fills filtrados), el resultado es que **el risk manager no ve ninguna posición jamás**: drawdown, exposición total, `max_open_positions`, equity y el halt por drawdown operan sobre un portfolio vacío mientras hay dinero real en riesgo. Es la condición ideal para acumular posiciones sin ningún freno.
**Fix:** sustituir las tres condiciones ad-hoc de credenciales (`main.py:198`, `:723`, y la de `_setup_ws_callbacks`) por una única propiedad del cliente (`client.is_authenticated`) o por `settings` según el venue activo.
**Verificado cómo:** leído + `grep`; condición booleana determinista.

---

### [P1] hyperliquid-15 — `_ensure_sdk()` hace ~1.5 s de I/O de red **bloqueante dentro del event loop**, en cada llamada mientras falle
**Archivo:** `exchange/hyperliquid_client.py:62-86`, llamado desde `:102`, `:138`, `:162`, `:190`, `:202`, `:232`, `:250`
**Evidencia:**
```python
async def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
    self._ensure_sdk()        # <-- SÍNCRONO, fuera del executor
    ...
    data = await self._run_sync(_fetch)   # solo el fetch va al executor
```
`Info.__init__` (SDK) hace `spot_meta()` + `meta()` por HTTP antes de devolver. Medido:
```
Info() blocking construction ms: 1501
spotMeta 134 814 bytes / 1339 ms | metaAndAssetCtxs 71 421 bytes / 931 ms | meta 17 495 bytes / 745 ms
```
**Por qué es un problema:** ~1.5 s con el event loop congelado: no se procesan ticks del WS, no corre `_risk_monitor_loop`, no se atienden los timeouts. Peor: si falla (ver `hyperliquid-12`) `self._info` sigue a `None` → **cada** llamada posterior reintenta y vuelve a bloquear 1.5 s; con 4 símbolos y `data_interval_sec=1.0` el bot queda permanentemente atascado. Añadido: `_run_sync` usa `asyncio.get_event_loop()` (deprecado en 3.12 fuera de un loop corriendo; debe ser `asyncio.get_running_loop()`) y el executor por defecto tiene solo `min(32, cpu+4)` hilos compartidos con todo lo demás.
**Fix:** mover `_ensure_sdk()` dentro de `_run_sync` (o hacer un `async def _init()` llamado una vez desde `BotStrike.start()`), cachear el fallo con backoff, y usar `asyncio.get_running_loop()` + un `ThreadPoolExecutor` propio y acotado.
**Verificado cómo:** medido con `time.time()` alrededor de `Info(...)` y de cada POST, contra mainnet real.

---

### [P1] hyperliquid-16 — Sin heartbeat `{"method":"ping"}`: HL cierra el socket a los 60 s de silencio (02-P1-13 punto 6, sigue abierto)
**Archivo:** `exchange/hyperliquid_ws.py:69`, `:129`
**Evidencia:** `grep -n "ping\|pong" exchange/hyperliquid_ws.py` → solo `websockets.connect(HL_WS_URL, ping_interval=20)`, que envía **frames de protocolo**, no un mensaje de aplicación.
Doc oficial (websocket/timeouts-and-heartbeats): *"The server will close any connection if it hasn't sent a message to it in the last 60 seconds"*, y el cliente debe mandar `{"method": "ping"}` (respuesta `{"channel": "pong"}`).
**Por qué es un problema:** el canal de mercado recibe datos constantemente, pero **`connect_user` puede pasar minutos sin actividad** (sin fills) → HL corta la conexión cada 60 s; el bucle reconecta y vuelve a suscribirse, y en cada reconexión `userFills` reenvía un snapshot (`isSnapshot: true`) que, si algún día se parsea, reprocesaría fills antiguos como nuevos (doble contabilidad de PnL). Es un bug latente que se activa justo cuando se arregle el parseo.
**Fix:** tarea `asyncio` que envíe `{"method":"ping"}` cada ~50 s en ambas conexiones; ignorar `channel == "pong"`; y descartar el mensaje `userFills` con `isSnapshot: true` tras la primera vez.
**Verificado cómo:** doc oficial vía WebFetch (timeouts-and-heartbeats); `grep` del código.

---

### [P1] hyperliquid-17 — `midPx` puede venir `null` y `float(None)` revienta `get_market_snapshot`
**Archivo:** `exchange/hyperliquid_client.py:116`
**Evidencia:**
```python
"mid_price": float(ctx.get("midPx", 0)),      # .get devuelve None si la CLAVE existe con valor null
```
Medido hoy en mainnet: **56 de 232** activos tienen `midPx: null` (`MATIC`, `RNDR`, `FTM`, `MKR`, `FXS`, ...); `markPx` nunca es null. El SDK lo documenta: `"midPx": Optional(float string)`.
```
>>> float(None)  ->  TypeError: float() argument must be a string or a real number, not 'NoneType'
```
**Por qué es un problema:** `midPx` es null cuando el book está vacío o el activo está en delisting/halt. Si le pasa a un símbolo operado, `get_market_snapshot` lanza `TypeError` → `update_snapshot` lo captura y **devuelve el snapshot anterior** (`market_data.py:317`) → el bot sigue operando con un precio congelado mientras el activo está halted, sin que `is_data_stale` se entere (el timestamp del snapshot viejo no se actualiza, pero tampoco se marca el error). Y en `_init_symbol` el símbolo queda con DataFrame vacío en silencio.
**Fix:** `mid = ctx.get("midPx") or ctx.get("markPx") or 0` con `float()` defensivo; si `markPx` también falta, propagar un error explícito para que el guard de datos rancios bloquee el símbolo.
**Verificado cómo:** contados los nulls contra `api.hyperliquid.xyz/info` (56/232) y reproducido `float(None)` en `py -3.12`.

---

### [P1] hyperliquid-18 — `use_testnet` sigue ignorado por cliente y WS (02-P1-13 punto 5, abierto y sin fix)
**Archivo:** `exchange/hyperliquid_client.py:72`, `exchange/hyperliquid_ws.py:19`, `:28-33`, `config/settings.py:304-316`, `main.py:81-84`
**Evidencia:**
```python
# hyperliquid_client.py:72 — siempre mainnet
base_url = constants.MAINNET_API_URL          # el SDK sí trae TESTNET_API_URL
# hyperliquid_ws.py:19 — constante de módulo
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
# hyperliquid_ws.py:32 — el parámetro existe y NO SE USA en ningún sitio
def __init__(self, symbols=None, wallet_address="", use_testnet: bool = False):
# main.py:81-84 — main ni siquiera lo pasa
self.websocket = HyperliquidWebSocket(symbols=..., wallet_address=...)
# config/settings.py:304-316 — apply_testnet() solo tiene ramas para strike y binance
if self.is_strike: ... elif self.is_binance: ...
```
`use_testnet` vale **True por defecto** (`settings.py:186`).
**Por qué es un problema:** un usuario que ponga `use_testnet=True` para probar HL sin riesgo estará firmando y enviando órdenes a **mainnet con dinero real**. El SDK tiene `TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"` y el WS de testnet es `wss://api.hyperliquid-testnet.xyz/ws`; están a una línea. Además la firma EIP-712 incluye `is_mainnet = (base_url == MAINNET_API_URL)`, así que la URL determina también el dominio de firma: mezclarlas produce firmas inválidas, no órdenes "seguras".
**Fix:** `base_url = constants.TESTNET_API_URL if settings.use_testnet else constants.MAINNET_API_URL`; `self._ws_url` por instancia en el WS; rama `elif self.is_hyperliquid:` en `apply_testnet()`; y loguear en `CRITICAL` la URL efectiva al arrancar.
**Verificado cómo:** leído; `TESTNET_API_URL` confirmado en `site-packages/hyperliquid/utils/constants.py`.

