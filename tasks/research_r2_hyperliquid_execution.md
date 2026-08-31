# Research R2 — Hyperliquid: Ejecución para bot paper/live ($1000)

> Fecha: 2026-08-31 · Fuentes: documentación OFICIAL (hyperliquid.gitbook.io/hyperliquid-docs), SDK oficial Python (github.com/hyperliquid-dex/hyperliquid-python-sdk) y fuentes 2025-2026. Todas las cifras citadas provienen de la doc oficial salvo que se indique lo contrario.
> Estado: **COMPLETO** (secciones 1-16). Verificado contra doc oficial, PyPI e issues del SDK el 2026-08-31.
> ⚠️ Lo NO verificable por doc oficial (jurisdicción España, incidentes de prensa) va marcado explícitamente como fuente secundaria.

---

## 1. Endpoints REST: `info` y `exchange`

Fuente: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint y .../info-endpoint

- **Mainnet**: `https://api.hyperliquid.xyz/info` y `https://api.hyperliquid.xyz/exchange` (POST, `Content-Type: application/json`).
- **Testnet**: `https://api.hyperliquid-testnet.xyz/info` y `.../exchange`.
- `info` = solo lectura (sin firma). `exchange` = acciones firmadas.

### 1.1 Estructura de request a `/exchange`
```json
{
  "action": { ... },            // la acción (order, cancel, modify, ...)
  "nonce": 1693526400000,        // timestamp ms actual
  "signature": {"r": "...", "s": "...", "v": 27},
  "vaultAddress": "0x...",       // OPCIONAL: operar un vault/subaccount
  "expiresAfter": 1693526460000  // OPCIONAL: caducidad de la acción en ms
}
```
Respuestas: `{"status":"ok","response":{"type":"order","data":{"statuses":[...]}}}`. Un error de firma/validación devuelve `"status":"err"` con string descriptivo. **Importante**: incluso con `status:ok`, cada orden del batch tiene su propio status (`resting`, `filled`, `error`) dentro de `statuses[]` — hay que comprobar cada uno.

Ejemplos oficiales de respuesta:
- Resting: `{"status":"ok","response":{"type":"order","data":{"statuses":[{"resting":{"oid":77738308}}]}}}`
- Filled: `{"statuses":[{"filled":{"totalSz":"0.02","avgPx":"1891.4","oid":77747314}}]}`

### 1.2 Requests clave de `/info` para un bot
| type | Uso | Peso rate-limit |
|---|---|---|
| `allMids` | precios mid de todo | 2 |
| `l2Book` (coin, nSigFigs 2-5, mantissa) | libro, máx 20 niveles/lado | 2 |
| `clearinghouseState` (user) | posiciones perp, margen, liquidación | 2 |
| `openOrders` / `frontendOpenOrders` (user) | órdenes abiertas | 20 |
| `userFills` (máx 2000 recientes) / `userFillsByTime` | fills: `coin, px, sz, side, time, fee, feeToken, tid, closedPnl` | 20 + extra por cada 20 items |
| `orderStatus` (user, oid o cloid) | estado de orden | 2 |
| `historicalOrders` (máx 2000) | histórico | 20 + extra/20 items |
| `userRateLimit` | `cumVlm, nRequestsUsed, nRequestsCap, nRequestsSurplus` | 20 |
| `candleSnapshot` (máx 5000 velas; 1m…1M) | OHLCV: `t,T,o,h,l,c,v,n` | 20 + extra/60 items |
| `meta` / `metaAndAssetCtxs` | universo perp: `szDecimals`, `maxLeverage`, funding, OI, oraclePx | 20 |
| `userFees` | tier de fees efectivo del usuario | 20 |
| `subAccounts`, `vaultDetails`, `userRole` | estructura de cuentas | 20 (userRole = 60) |

- **Paginación**: respuestas limitadas a 500 elementos; usar el último timestamp como `startTime` siguiente.
- **Pitfall oficial**: consultar `info` con la dirección del **agent wallet** devuelve vacío — hay que usar SIEMPRE la dirección de la cuenta maestra.

### 1.3 Estados de orden posibles (orderStatus)
`open, filled, canceled, triggered, rejected, marginCanceled, vaultWithdrawalCanceled, openInterestCapCanceled, selfTradeCanceled, reduceOnlyCanceled, siblingFilledCanceled, delistedCanceled, liquidatedCanceled, scheduledCancel, tickRejected, minTradeNtlRejected, perpMarginRejected, reduceOnlyRejected, badAloPxRejected, iocCancelRejected, badTriggerPxRejected, marketOrderNoLiquidityRejected, positionIncreaseAtOpenInterestCapRejected, positionFlipAtOpenInterestCapRejected, tooAggressiveAtOpenInterestCapRejected, openInterestIncreaseRejected, insufficientSpotBalanceRejected, oracleRejected, perpMaxPositionRejected`

(El bot debe mapear al menos: `tickRejected`, `minTradeNtlRejected`, `perpMarginRejected`, `badAloPxRejected`, `iocCancelRejected`, `badTriggerPxRejected`, `marketOrderNoLiquidityRejected`.)

### 1.4 Asset IDs
Fuente: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids
- Perps estándar: `a` = índice del coin en `meta.universe` ("BTC = 0 on mainnet").
- Spot: `a = 10000 + spotInfo["index"]` (PURR/USDC → 10000).
- Perps HIP-3 (builder-deployed): `a = 100000 + perp_dex_index * 10000 + index_in_meta`; nombre `{dex}:{coin}`.

---

## 2. Firma: EIP-712, agent/API wallets, nonce

Fuentes: .../api/signing y .../api/nonces-and-api-wallets

### 2.1 Dos esquemas de firma (error nº1 de integración)
1. **L1 actions** (`sign_l1_action` en el SDK): order, cancel, modify, updateLeverage… La acción se serializa con **msgpack** (¡el ORDEN de los campos importa!), se le añade nonce y vaultAddress, se hashea → "phantom agent" → firma EIP-712 con dominio `{name: "Exchange", version: "1", chainId: 1337, verifyingContract: 0x0}` y tipo `Agent {source, connectionId}`. `source` = `"a"` mainnet / `"b"` testnet.
2. **User-signed actions** (`sign_user_signed_action`): usdSend, withdraw3, usdClassTransfer, approveAgent, approveBuilderFee… EIP-712 "legible" con dominio `HyperliquidSignTransaction` y `signatureChainId` real de la wallet (p.ej. `0xa4b1` Arbitrum). Estas acciones **debe firmarlas la wallet maestra**, no el agent (p.ej. `ApproveBuilderFee`: "This action must be signed by the user's main wallet, not an agent/API wallet").

Errores comunes citados por la doc oficial:
- "not realizing that there are two signing schemes".
- "The order of fields matter for msgpack".
- Trailing zeroes en números al serializar ("If implementing signing, trailing zeroes should be removed").
- "It is recommended to lowercase any address before signing and sending".
- Una firma incorrecta NO dice por qué falla; se manifiesta como dirección recuperada distinta → errores tipo `"L1 error: User or API Wallet 0x0123... does not exist."` o `"Must deposit before performing actions. User: 0x123..."`.
- Recomendación oficial: **usar un SDK existente en vez de firmar a mano**.

### 2.2 Agent / API wallets
- "A master account can approve API wallets to sign on behalf of the master account or any of the sub-accounts" (acción `approveAgent`).
- Expiración máxima **180 días** (renovar antes; poner recordatorio).
- El agent **solo firma**; los datos se consultan con la dirección maestra.
- **Riesgo de replay**: "Once an agent is deregistered, its used nonce state may be pruned... previously signed actions can be replayed once the nonce set is pruned" → generar SIEMPRE agent wallets nuevas (clave privada fresca), nunca reutilizar direcciones.

### 2.3 Nonces
Reglas exactas (doc oficial):
- "The 100 highest nonces are stored per address. Every new transaction must have nonce larger than the smallest nonce in this set and also never have been used before."
- "Nonces must be within `(T - 2 days, T + 1 day)`" donde T = timestamp ms del bloque.
- El nonce se trackea **por firmante** (agent wallet ≠ master).
- Best practices oficiales para concurrencia: 1 API wallet por proceso de trading; batchear órdenes/cancels cada ~0.1 s con contador atómico (timestamp ms + contador); separar IOC/GTC de ALO (los validadores priorizan cancels/ALO); tolerancia a desorden ~2 s. Con subcuentas: un API wallet distinto por subcuenta para evitar colisiones de nonce.
- Acción `noop` sirve para invalidar un nonce pendiente.

---

## 3. Tipos de orden

Fuente: .../api/exchange-endpoint

### 3.1 Wire format de una orden
```json
{
  "a": 0,               // asset id
  "b": true,             // isBuy
  "p": "29000",         // precio (string)
  "s": "0.01",          // tamaño en unidades del coin (string)
  "r": false,            // reduceOnly
  "t": {"limit": {"tif": "Gtc"}},
  "c": "0x1234...cafe"  // cloid opcional, hex de 128 bits (16 bytes)
}
```

### 3.2 TIF (órdenes limit)
- `Alo` (Add Liquidity Only) = **post-only**: se cancela si cruzaría el libro (status `badAloPxRejected` si el precio es inválido). Fee de maker.
- `Ioc` (Immediate or Cancel): lo no ejecutado se cancela. Así se hace una "market order": el SDK envía IOC con precio agresivo (slippage por defecto 5% en `market_open`).
- `Gtc`: orden en libro estándar.
- **No hay tipo "market" nativo**: market = limit IOC con precio protegido.

### 3.3 Trigger orders (TP/SL)
```json
"t": {"trigger": {"isMarket": true, "triggerPx": "27000", "tpsl": "sl"}}
```
- `tpsl`: `"tp"` o `"sl"`; `triggerPx` se compara contra el **mark price**.
- `isMarket: true` → al disparar ejecuta como market (IOC agresiva); `false` → coloca limit al precio `p`.
- Para SL/TP de protección usar `r: true` (reduceOnly) para no abrir posición contraria.

### 3.4 `grouping`
- `"na"`: órdenes independientes (default).
- `"normalTpsl"`: entrada + TP + SL enviados juntos; TP/SL ligados a la ORDEN (se activan/cancelan según el fill de la entrada; tamaño fijo).
- `"positionTpsl"`: TP/SL ligados a la POSICIÓN (siguen el tamaño de la posición). Cuando un sibling se ejecuta, el otro se cancela (status `siblingFilledCanceled`).

### 3.5 Otras acciones útiles
- `cancel` (por `{a, o}` oid) y `cancelByCloid` (`{asset, cloid}`).
- `modify` / `batchModify` (por oid o cloid).
- `updateLeverage {asset, isCross, leverage}` — cambiar apalancamiento (solo posible sin reducir por debajo del margen usado).
- `updateIsolatedMargin {asset, isBuy, ntli}` (ntli en 1e-6 USD: 1000000 = 1 USD).
- `scheduleCancel {time}` = **dead-man's switch**: cancela TODAS las órdenes en `time` (mín. 5 s en el futuro; máx. 10 usos por día UTC; sin `time` lo desarma). IMPRESCINDIBLE para un bot live.
- `twapOrder` / `twapCancel` (TWAP nativo, slices de 30 s, `m` minutos, `t` randomize).
- `usdClassTransfer` (spot↔perp), `usdSend`, `withdraw3` (fee $1, ~5 min), `vaultTransfer`, `approveAgent`, `approveBuilderFee`, `reserveRequestWeight`, `noop`.

---

## 4. Formato de precios y tamaños (tick/lot)

Fuente: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size

- **Precio (perps)**: "Prices can have up to 5 significant figures, but no more than `MAX_DECIMALS - szDecimals` decimal places where `MAX_DECIMALS` is 6" (spot: MAX_DECIMALS = 8).
- "Integer prices are always allowed, regardless of the number of significant figures" (123456 es válido).
- Ejemplos oficiales (perp): válidos `1234.5`, `0.001234`; inválidos `1234.56` (6 cifras significativas), `0.0012345` (>6 decimales). Con szDecimals=1: `0.01234` válido, `0.012345` no.
- **Tamaño**: redondeado a `szDecimals` del asset (de `meta`). "if `szDecimals = 3` then `1.001` is valid but `1.0001` is not".
- **Firma**: quitar trailing zeroes ("If implementing signing, trailing zeroes should be removed") — `"29000.0"` es firma inválida, debe ser `"29000"`.

### 4.1 `float_to_wire` (SDK Python, hyperliquid/utils/signing.py)
```python
def float_to_wire(x: float) -> str:
    rounded = f"{x:.8f}"
    if abs(float(rounded) - x) >= 1e-12:
        raise ValueError("float_to_wire causes rounding", x)
    if rounded == "-0": rounded = "0"
    normalized = Decimal(rounded).normalize()
    return f"{normalized:f}"
```
El SDK valida que el float no pierda precisión al pasarlo a string de 8 decimales y normaliza (sin trailing zeros). En el bot: redondear precio con `round(float(f"{px:.5g}"), 6 - szDecimals)` (patrón del ejemplo oficial `basic_order.py` / `rounding.py`) y tamaño con `round(sz, szDecimals)` ANTES de enviar.

### 4.2 Mínimo de orden
- **Valor mínimo de orden: $10 USDC de notional** (perps y spot). Rechazo con status `minTradeNtlRejected`. Con $1000 de cuenta esto limita el tamaño mínimo de cada slice/entrada: toda orden (incluidos TP/SL parciales) debe valer ≥ $10.
- Máximos: "Maximum market order value: $30,000,000 for max leverage >= 25, $5,000,000 for max leverage in [20, 25), $2,000,000 for max leverage in [10, 20), otherwise $500,000"; límite de orden límite = 10× ese valor (irrelevante para $1000).

---

## 5. Fees (2026)

Fuente: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees

### 5.1 Tiers base perps (volumen ponderado 14 días; subcuentas suman al master, vaults aparte)
| Tier | Volumen 14d | Taker | Maker |
|---|---|---|---|
| 0 | — | 0.045% | 0.015% |
| 1 | >$5M | 0.040% | 0.012% |
| 2 | >$25M | 0.035% | 0.008% |
| 3 | >$100M | 0.030% | 0.004% |
| 4 | >$500M | 0.028% | 0.000% |
| 5 | >$2B | 0.026% | 0.000% |
| 6 | >$7B | 0.024% | 0.000% |

Con $1000 el bot estará en Tier 0: **taker 4.5 bps / maker 1.5 bps**. Spot tier 0: 0.070% taker / 0.040% maker.

### 5.2 Descuentos y extras
- **Staking HYPE**: Wood >10 HYPE = 5%, Bronze >100 = 10%, Silver >1k = 15%, Gold >10k = 20%, Platinum >100k = 30%, Diamond >500k = 40% de descuento.
- **Referral**: descuento (4%) aplicable a los primeros **$25M** de volumen del referido; recompensas al referidor durante el primer $1B.
- **Maker rebates**: solo por cuota de volumen maker global (>0.5% / >1.5% / >3.0% → -0.001% / -0.002% / -0.003%). Inalcanzable con $1000.
- **Builder fee**: máx **0.1% en perps, 1% en spot**; requiere `approveBuilderFee` firmado por la wallet maestra; unidad = décimas de basis point (`f:10` = 1 bp); el builder necesita ≥100 USDC en perps. Un bot propio NO debe adjuntar builder fee (es coste extra); máx 10 approvals activos por usuario.
- Destino de fees: "Fees are entirely directed to the community (HLP, the assistance fund, and deployers)".

### 5.3 Coste realista para el bot ($1000, tier 0)
- Round-trip taker-taker: 9 bps + funding. Con posición de $1000: ~$0.90 por round-trip.
- Estrategia maker (Alo) entrada + taker salida: 6 bps ≈ $0.60. El edge de la estrategia debe superar esto + slippage.

---

## 6. Funding (cada hora)

Fuente: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding

- Pago **cada hora**: se usa la tasa de 8h dividida en pagos horarios (1/8 por hora).
- **Interés**: "predetermined at 0.01% every 8 hours, which is 0.00125% every hour, or 11.6% APR paid to short".
- **Fórmula oficial**: `Funding Rate (F) = Average Premium Index (P) + clamp(interest rate − Premium Index (P), −0.0005, 0.0005)`.
- Premium: `premium = impact_price_difference / oracle_price`, con `impact_price_difference = max(impact_bid_px − oracle_px, 0) − max(oracle_px − impact_ask_px, 0)`. (HIP-3: `premium = (0.5*(impact_bid+impact_ask)/oracle_px) − 1`.)
- **Cap**: "Funding on Hyperliquid is capped at 4%/hour" (extremo pero posible en squeezes: $1000 de posición podría pagar $40/h — vigilar funding antes de mantener posición).
- **Pago**: `position_size * oracle_price * funding_rate` — usa el **oracle price** (spot), no el mark.
- El funding previsto está en `metaAndAssetCtxs` (`funding` del asset ctx) y el histórico en `fundingHistory` / `userFunding`.

---

## 7. Margen (cross/isolated) y liquidación

Fuentes: .../trading/margining, .../trading/liquidations, .../trading/margin-tiers

### 7.1 Margen
- Modos: **cross** (default, colateral compartido), **isolated** (colateral confinado al asset), "strict isolated" (no se puede retirar margen; se libera proporcionalmente al cerrar).
- Margen inicial = `position_size * mark_price / leverage`. "The initial margin is used by the position and cannot be withdrawn for cross margin positions".
- "**Leverage is only checked upon opening a position.** Afterwards, the user is responsible for monitoring the leverage usage to avoid liquidation."
- PnL no realizado: en cross queda disponible como margen inicial para nuevas posiciones; retirable solo si el margen restante ≥ 10% del notional total abierto.

### 7.2 Liquidación
- Ocurre cuando el equity < maintenance margin (mark price). "The maintenance margin is currently set to half of the initial margin at max leverage" → entre 1.25% (40x) y 16.7% (3x).
- Proceso: 1º intento de cierre con **órdenes de mercado al libro**; posiciones >100k USDC se liquidan al 20% con cooldown de 30 s. Si equity < **2/3 del maintenance margin** → **backstop liquidation** vía liquidator vault (parte de HLP): en cross se transfieren TODAS las posiciones cross y el margen; "During backstop liquidation, the maintenance margin is not returned to the user".
- **Fórmula oficial de precio de liquidación**:
  `liq_price = price − side * margin_available / position_size / (1 − l * side)` con `l = 1 / MAINTENANCE_LEVERAGE`, `side = 1 long / −1 short`; `margin_available` según cross/isolated.
- Mitigación recomendada por la doc: stop-loss por debajo/encima del precio de liquidación.

### 7.3 Apalancamiento máximo por activo (margin tiers, mainnet, 2026)
| Activo | Notional | Max lev |
|---|---|---|
| BTC | 0–150M / >150M | 40x / 20x |
| ETH | 0–100M / >100M | 25x / 15x |
| SOL | 0–70M / >70M | 20x / 10x |
| XRP | 0–40M / >40M | 20x / 10x |
| AAVE, ADA, APT, AVAX, BCH, CRV, DOGE, ENA, HYPE, kBONK, kPEPE, LINK, LTC, NEAR, SUI, TRUMP, UNI, WLD, ZEC… | 0–20M / >20M | 10x / 5x |
| ARB, BNB, DOT, JUP, kSHIB, MKR, ONDO, PAXG, TON, TRX… | 0–3M / >3M | 10x / 5x |
- Maintenance margin rate = (tasa de margen inicial a max leverage del tier)/2, con `maintenance_margin = notional * mm_rate − maintenance_deduction`.
- Rango global de max leverage: 3x–40x según activo. Testnet tiene tiers más bajos.
- Consultar por API: `meta.universe[i].maxLeverage` y (si aplica) `margin-tiers`.

### 7.4 Mark price y oracle price (robust price indices)
Fuente: .../trading/robust-price-indices
- **Oracle price** (para funding y margen): weighted median de precios spot de CEXs publicada por validadores, "approximately once every three seconds"; no depende del propio mercado de Hyperliquid.
- **Mark price** = mediana de: (1) oracle + EMA 150s del premium del mid de HL; (2) mediana de best bid, best ask y last trade en HL; (3) mediana de mid de perps en Binance, OKX, Bybit, Gate, MEXC con pesos 3,2,2,1,1. Si solo existen 2 de 3 inputs, se añade EMA 30s del (2).
- Los triggers TP/SL y las liquidaciones se evalúan contra el **mark price** — el bot debe usar mark, no last, para calcular distancias de stop.

---

## 8. Rate limits

Fuente: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits

### 8.1 Por IP (REST)
- Presupuesto agregado: **1200 weight/minuto por IP**.
- `/exchange`: weight = `1 + floor(batch_length / 40)` (1 orden = 1; batch de 79 = 2).
- `/info`: weight 2 (`l2Book, allMids, clearinghouseState, orderStatus, spotClearinghouseState, exchangeStatus`), weight 60 (`userRole`), weight 20 (el resto). Requests con listas largas (userFills, historicalOrders, fundingHistory, candleSnapshot…) cargan peso adicional por cada 20 items (60 en candles).
- Explorer API: weight 40.

### 8.2 Por dirección (address-based, según volumen)
- Allowance: "**1 request per 1 USDC traded cumulatively since address inception**" + buffer inicial de **10,000 requests**.
- Al agotarlo: **1 request cada 10 segundos** (cooldown). ⇒ Con $1000 y poco volumen, el bot tiene ~10k acciones iniciales; cada $1 de volumen añade 1 acción. Un bot de $1000 con churn moderado genera volumen suficiente, pero un bot que spamea órdenes sin fills PUEDE agotar el buffer → diseñar para pocas órdenes/minuto y monitorizar `userRateLimit`.
- Cancels: límite extra `min(limit + 100000, limit * 2)` (los cancels casi nunca se bloquean antes que las órdenes).
- Batch: cuenta como 1 para IP pero como n para address-based.
- Órdenes abiertas: cap de **1000** (+1 por cada 5M USDC de volumen, máx 5000); con ≥1000 abiertas se rechazan nuevas reduce-only/trigger.
- `reserveRequestWeight`: permite comprar peso adicional (paga en USDC) — último recurso.

### 8.3 WebSocket
- Máx **10 conexiones WS**, **30 conexiones nuevas/min**, **1000 suscripciones**, **10 usuarios únicos** en suscripciones user-specific, **2000 mensajes/min enviados**, **100 post messages en vuelo**.

---

## 9. WebSocket

Fuentes: .../api/websocket/subscriptions, .../websocket/timeouts-and-heartbeats, .../websocket/post-requests

- URL: `wss://api.hyperliquid.xyz/ws` (testnet: `wss://api.hyperliquid-testnet.xyz/ws`).
- Suscripción: `{"method": "subscribe", "subscription": {"type": "l2Book", "coin": "BTC"}}`; unsubscribe análogo.
- **Heartbeat**: "The server will close any connection if it hasn't sent a message to it in the last 60 seconds." → enviar `{"method": "ping"}` (respuesta `{"channel": "pong"}`) cada <60 s (el SDK usa 50 s).
- Suscripciones relevantes para el bot:
  - Mercado: `allMids`, `bbo` (best bid/offer), `l2Book`, `trades`, `candle`, `activeAssetCtx` (funding/OI/mark del asset).
  - Usuario: `orderUpdates` (cambios de estado de órdenes: `{order, status, statusTimestamp}`), `userFills` (con `isSnapshot: true` en el primer mensaje), `userEvents` (unión: `fills | funding | liquidation | nonUserCancel`), `userFundings`, `notification`, `activeAssetData` (leverage y margen disponibles por asset).
- Streaming con snapshot inicial: "the first message has `isSnapshot: true` and the following streaming updates have `isSnapshot: false`" (userFills, userFundings, userEvents…). El bot debe deduplicar fills del snapshot contra su estado.
- **Reconexión**: al reconectar hay que re-suscribir todo; los eventos ocurridos durante la desconexión NO se reenvían (salvo el snapshot inicial de userFills) → reconciliar SIEMPRE vía REST (`openOrders` + `clearinghouseState` + `userFillsByTime` desde el último fill conocido).
- WS también acepta **post requests** (info y exchange firmado) vía `{"method":"post","id":...,"request":{...}}` — útil para latencia, pero límite de 100 en vuelo.

---

## 10. Testnet

Fuentes: .../for-developers/api (base URLs) · .../onboarding/testnet-faucet

| Recurso | URL |
|---|---|
| App | `https://app.hyperliquid-testnet.xyz` |
| Faucet | `https://app.hyperliquid-testnet.xyz/drip` |
| REST info | `https://api.hyperliquid-testnet.xyz/info` |
| REST exchange | `https://api.hyperliquid-testnet.xyz/exchange` |
| WebSocket | `wss://api.hyperliquid-testnet.xyz/ws` |
| Gestión API wallets | `https://app.hyperliquid-testnet.xyz/API` |

- Cita oficial: *"All example API calls use the mainnet url (https://api.hyperliquid.xyz), but you can make the same requests against testnet using the corresponding url (https://api.hyperliquid-testnet.xyz)"*.
- **Faucet**: da **1.000 mock USDC**. **Requisito bloqueante**: hay que **haber depositado antes en mainnet con la misma dirección**. Sin depósito previo en mainnet no hay faucet → para BotStrike esto implica que testnet NO es gratis del todo: exige una cuenta mainnet financiada (aunque sea con el mínimo).
- **Trampa con login por email**: *"If you use email-based login, the mainnet and testnet wallets differ"* → hay que exportar la wallet de email de mainnet e importarla en Rabby/MetaMask para que la dirección de testnet coincida.
- En el SDK: `from hyperliquid.utils import constants; Info(constants.TESTNET_API_URL, skip_ws=True)`.
- **Diferencias testnet vs mainnet que rompen backtests/paper**: `szDecimals` y **margin tiers más bajos** (menos apalancamiento máximo), universo de activos distinto, liquidez del libro casi nula (el slippage medido en testnet NO es representativo), y el bug abierto del SDK (issue #275) *"Testnet API returns invalid token indices"* → `IndexError` al construir `Info`. **Conclusión: testnet sirve para validar firma/plumbing, NO para validar edge ni costes.**

---

## 11. SDK oficial Python (`hyperliquid-python-sdk`)

Fuente: https://github.com/hyperliquid-dex/hyperliquid-python-sdk · https://pypi.org/project/hyperliquid-python-sdk/

### 11.1 Versión y dependencias (verificado en PyPI, 2026-08-31)
- **Última versión: `0.24.0` (4 jun 2026)**. Instalación: `pip install hyperliquid-python-sdk`.
- `requires_python`: **>=3.9, <4.0**. Pero el README avisa: *"Development requires Python 3.10 exactly"* (problemas de dependencias con versiones nuevas y de typing con las viejas). **Recomendación para BotStrike: pinear 3.11 o 3.12 y correr el test suite propio; si algo falla, bajar a 3.10.**
- Dependencias: `eth-utils >=2.1.0,<6.0.0`, `eth-account >=0.10.0,<0.14.0`, `websocket-client >=1.5.1,<2.0.0`, `requests >=2.31.0,<3.0.0`, `msgpack >=1.0.5,<2.0.0`.
- ⚠️ **`eth-account` es el punto frágil**: el rango `<0.14.0` es amplio y la firma EIP-712 cambió de API entre versiones. El release `0.14.1` (8 may 2025) fue precisamente *"Switch from sign_message to sign_typed_data"*. **Pinear `hyperliquid-python-sdk==0.24.0` Y `eth-account` a una versión exacta en el lockfile**; no dejar rangos abiertos en producción.

### 11.2 Historial de releases relevante (para saber qué versión mínima necesitas)
| Versión | Fecha | Cambio relevante para un bot |
|---|---|---|
| 0.24.0 | 2026-06-04 | Acciones de user abstraction con multi-sig |
| 0.23.0 | 2026-04-14 | **Priority fees**; fix de construcción de `Info` |
| 0.22.0 | 2026-02-04 | Acciones generales de user abstraction (modos de cuenta) |
| 0.21.0 | 2025-11-18 | **`grouping` en órdenes** (normalTpsl/positionTpsl) ← mínimo para TP/SL agrupado |
| 0.20.1 | 2025-11-04 | Fix market orders en DEXs HIP-3 |
| 0.19.0 | 2025-09-11 | Más endpoints de `info` |
| 0.18.0 | 2025-08-12 | Acción `noop`; **timeouts de red configurables**; keystore cifrado en ejemplos |
| 0.16.0 | 2025-07-02 | Suscripciones WS `activeAssetData` / `activeAssetCtx` |
| 0.15.0 | 2025-05-14 | Suscripción WS `bbo` |
| 0.14.1 | 2025-05-08 | `sign_message` → `sign_typed_data` (rotura con eth-account) |
| 0.12.0 | 2025-04-22 | `expiresAfter` para L1 actions |

**Mínimo recomendado para BotStrike: ≥0.21.0** (grouping) y preferentemente **0.23.0+** (fix de `Info` + timeouts).

### 11.3 API del SDK que importa (verificado en `hyperliquid/exchange.py`)
```python
class Exchange:
    DEFAULT_SLIPPAGE = 0.05          # 5 % — ¡demasiado para $1000!
    def __init__(self, wallet: LocalAccount, base_url=None, meta=None,
                 vault_address=None, account_address=None,
                 spot_meta=None, perp_dexs=None, timeout=None): ...
    def order(self, name, is_buy, sz, limit_px, order_type, reduce_only=False,
              cloid=None, builder=None): ...
    def bulk_orders(self, order_requests, builder=None, grouping="na"): ...
    def market_open(self, ..., slippage=DEFAULT_SLIPPAGE, cloid=None, builder=None): ...
    def market_close(self, ...): ...
    def update_leverage(self, leverage: int, name: str, is_cross: bool = True): ...
    def schedule_cancel(self, time: Optional[int]): ...
    def set_expires_after(self, expires_after: Optional[int]) -> None: ...
```
- **`DEFAULT_SLIPPAGE = 0.05` es un peligro real**: `market_open()` sin `slippage` explícito envía una IOC a ±5% del mid. En un libro fino eso puede comerse 500 bps de una cuenta de $1000. **BotStrike DEBE pasar `slippage` explícito (p.ej. 0.001–0.003) y validar contra `l2Book` antes de enviar.**
- `set_expires_after(ms)` NO aplica a user-signed actions (solo L1). Útil como red de seguridad: si el request se queda encolado, caduca en vez de ejecutar tarde.
- `market_close()` prioriza `account_address` sobre `wallet.address` para buscar la posición → **si usas agent wallet y no pasas `account_address`, `market_close()` no encuentra la posición y no cierra nada** (falla silenciosa, no excepción).
- `vault_address` se inyecta en `_post_action()` salvo para `usdClassTransfer` y `sendAsset`.

### 11.4 Errores comunes del SDK y sus causas (documentados + issues reales)
| Síntoma | Causa real | Fix |
|---|---|---|
| `L1 error: User or API Wallet 0x... does not exist.` | Firma incorrecta → se recupera OTRA dirección. La dirección del error **cambia** con el input, parece aleatoria. | Comparar campo a campo con `sign_l1_action` del SDK; verificar `chainId=1337` para L1, `source` `"a"`/`"b"` correcto, nonce ms fresco. |
| `Must deposit before performing actions. User: 0x...` | Mismo problema de firma, o cuenta realmente sin fondos. | Ídem + confirmar depósito. |
| `info` devuelve vacío / sin posiciones | Se consultó con la dirección del **agent wallet**. | Usar SIEMPRE la dirección de la cuenta maestra en `info`. Cita oficial: *"A common pitfall is to use the agent wallet which leads to an empty result."* |
| Firma "válida" pero rechazada | Casi siempre chain-id o nonce, no la clave. | L1 → chainId 1337 + dominio `Exchange`. User-signed → `signatureChainId` real (0xa4b1) + dominio `HyperliquidSignTransaction`. |
| `IndexError` al construir `Info` en testnet | Issue **#275** abierto: *"Testnet API returns invalid token indices"*. | Try/except o pinear meta manualmente. |
| Cuelgue / conexión muerta tras inactividad | Issue **#293** abierto: la sesión REST expira tras **~3 min** de inactividad; el SDK no trae `is_alive()`/`reconnect()`. | Usar `requests.Session` propia con retry, o heartbeat REST periódico. **Crítico para un bot que opera pocas veces al día.** |
| El programa no termina | WS thread no cerrado (arreglado en PR #73). | Llamar al shutdown del WS explícitamente. |
| `float_to_wire causes rounding` | Se pasó un float con >8 decimales. | Redondear ANTES (ver §4.1). |

### 11.5 Ejemplos oficiales útiles (`examples/`)
`basic_order.py`, `basic_market_order.py`, `basic_tpsl.py`, `basic_agent.py`, `basic_schedule_cancel.py`, `basic_order_with_cloid.py`, `basic_leverage_adjustment.py`, `cancel_open_orders.py`, `rounding.py`, `basic_ws.py`, `basic_vault.py`, `basic_sub_account.py`, `basic_withdraw.py`, `priority_fees.py`, `user_abstraction.py`, `example_utils.py`.
→ **`rounding.py` y `basic_tpsl.py` son los dos a copiar literalmente**; `example_utils.py` muestra el patrón `config.json` (`account_address` = master, `secret_key` = clave del agent).

---

## 12. Vaults y subcuentas

### 12.1 Vaults (legacy HyperCore)
Fuente: .../hypercore/vaults/for-vault-leaders-legacy
- Leader: **depósito mínimo 100 USDC**; debe *"maintain ≥5% of the vault at all times"* (no puede retirar por debajo de ese 5%).
- **Profit share del leader: 10%**.
- Cierre: *"All positions must be closed before the vault can close."*
- API: se opera pasando `vaultAddress` en el request a `/exchange` (o `vault_address` en el constructor de `Exchange`). Las fees del vault **no** se agregan al tier del master.
- Los "vaults" nuevos (2026) soportan spot y HIP-3; los legacy (2023) no.
- **Para BotStrike con $1000: NO usar vault.** Añade complejidad, 10% de profit share si hay terceros, y el capital queda sujeto a reglas de retirada. Sin beneficio a esta escala.

### 12.2 Subcuentas
Fuente: .../trading/sub-accounts
- Cupo: **empieza en 10 subcuentas tras alcanzar $100.000 de volumen**; +1 por cada $100M adicionales, **máx 50**.
- **API wallets: 3 por master account, +2 por cada subcuenta.**
- Las subcuentas **heredan el fee tier del master**, pero **los descuentos de referral NO se extienden a ellas**.
- Nonces: *"A single API wallet signing for a user, vault, or subaccount all share the same nonce set"* → con subcuentas, **un API wallet distinto por subcuenta** (regla oficial) para no colisionar nonces.
- **Para BotStrike con $1000: 1 cuenta, 1 API wallet, 1 proceso.** Las subcuentas solo tienen sentido para aislar estrategias con capital real separado.

---

## 13. Modos de cuenta 2026 (account abstraction) — ⚠️ impacto en rate limits

Fuente: .../trading/account-abstraction-modes

Tres modos activos:
1. **Unified Account** (default recomendado): *"single balance for each asset"* — el mismo USDC colateraliza perps cross y spot.
2. **Portfolio Margin**: agrupa *"all eligible assets, which are currently HYPE, BTC, USDC, USDT"* como colateral unificado.
3. **Manual/Standard** (market makers): balances perp y spot separados, cross-margin por DEX.

**Dos consecuencias operativas para el bot:**
- **Cap de acciones**: *"portfolio margin and unified account are capped at 50,000 daily user actions"*; **standard NO tiene ese límite**. Un bot de baja frecuencia con $1000 no llega a 50k/día, pero conviene contarlo y alertar al 70%.
- **Lectura de balances**: en unified/portfolio margin *"all balances and holds [show] in the spot clearinghousestate"* en lugar de en el user state de cada perp DEX → **si el bot lee solo `clearinghouseState` (perps) puede ver el colateral mal**. Verificar el modo activo y leer también `spotClearinghouseState`.

**Recomendación BotStrike**: dejar **Unified Account** (default) y **codificar la lectura de equity leyendo AMBOS clearinghouse states**, no asumir el modo.

---

## 14. Priority fees (2026) — opcional, no usar al principio

Fuente: .../for-developers/api/priority-fees

- Dos mecanismos independientes: **gossip (lectura)** y **order (escritura)**.
- Gossip: 2 subastas holandesas sincronizadas a un **ciclo de 3 minutos**; precio mínimo **0,1 HYPE**, cada subasta *"resets to 10 times the previous winning price"*; efecto *"approximately 25 ms reduction in latency per auction slot"*. Las fees se **queman**.
- Order priority (IOC): `{"p": 12345}`, con `p/100000000` = tasa; rango *"0-8bps, i.e. p = 80000"*, efecto *"approximately 45 ms reduction in end-to-end latency per 1 bp"*.
- ALO priority: ordenado en escalas de 400 ms; ⚠️ *"ALO priority fees are deducted at time of placing the order regardless of whether the order fills"* → **pagas aunque no ejecutes**.
- **Veredicto para $1000**: **NO usar priority fees.** 1 bp de fee por 45 ms de latencia no compra nada en una estrategia que no es HFT, y las ALO priority fees se cobran sin fill (sangría garantizada). Latencia se optimiza gratis colocando el bot cerca (Tokyo/AWS ap-northeast-1 según práctica común) y usando WS post requests.

---

## 15. Riesgos operativos

### 15.1 Downtime del API / exchange
- **Incidente real 2025-07-29 (14:20–14:47 UTC, >30 min)**: caída del API por *"spike in traffic"* (no hack). Usuarios *"completely locked out"*, sin poder cerrar posiciones. Hyperliquid emitió **reembolsos automáticos**. Fuente: The Block, CoinDesk.
- **Implicación de diseño (no negociable)**: el bot **no puede depender de poder cerrar en el momento crítico**. Mitigación: **TP/SL nativos on-chain (`trigger` + `reduceOnly`) SIEMPRE colocados junto con la entrada** — se ejecutan en el matching engine aunque el bot esté caído o el API no responda. Un stop "en memoria del bot" es un stop que no existe.
- Arquitectura: *"API servers listen to updates from a node and maintain the blockchain state locally"* → el API server es un intermediario que puede fallar aunque la cadena esté viva. Correr un nodo no validador es la mitigación seria (fuera de alcance para $1000).

### 15.2 Riesgo de protocolo: JELLY (2025-03-26)
- Un atacante abrió longs y shorts cruzados sobre JELLY explotando el mecanismo de liquidación; **HLP llegó a estar -$13,5M**.
- Los validadores votaron **delistar JELLY en ~2 minutos** y **liquidaron todas las posiciones a $0,0095** (el oráculo marcaba ~$0,50). Los usuarios no marcados fueron compensados por la Hyper Foundation.
- **Lección**: en un altcoin ilíquido, tu posición puede ser **liquidada a un precio administrativo decidido por votación**, no por el mercado. Después del incidente se añadió voto de delisting totalmente on-chain (stake-based), pero el poder sigue existiendo.
- **Regla para BotStrike: operar SOLO BTC, ETH y como mucho SOL.** Nada de memecoins ni activos con OI bajo. El riesgo de cola en altcoins de Hyperliquid no es de mercado, es de gobernanza.

### 15.3 ADL (auto-deleveraging)
Fuente: .../trading/auto-deleveraging
- Se activa cuando *"a user's account value or isolated position value becomes negative"*.
- Ranking de contrapartes: `(mark_price / entry_price) * (notional_position / account_value)` → **primero los más rentables y más apalancados**.
- Las posiciones se *"closed at the previous mark price against the now underwater user"*.
- **Implicación**: aunque el bot vaya ganando, su posición puede ser **cerrada forzosamente** si otro usuario queda underwater. Cuanto más apalancado esté el bot, más arriba en la cola de ADL. **Otra razón para leverage bajo.**
- Invariante protectora: *"a user who has no open positions will not socialize any losses of the platform."*

### 15.4 Delisting y OI caps
- Estados de rechazo reales: `openInterestCapCanceled`, `positionIncreaseAtOpenInterestCapRejected`, `tooAggressiveAtOpenInterestCapRejected` (*"Order rejected due to price more aggressive than oracle while at open interest cap"*), `delistedCanceled`.
- El bot debe **tratar el rechazo por OI cap como condición de mercado normal**, no como bug: no reintentar en bucle (quema rate limit por dirección).

### 15.5 Geoblocking y jurisdicción (España)
- Restringidos por los Terms of Use §1.5 "Restricted Persons": **EE. UU., Ontario (Canadá), Cuba, Irán, Corea del Norte, Siria, Crimea, Donetsk, Luhansk**. El frontend `app.hyperliquid.xyz` hace comprobación de IP.
- **España / UE no están en la lista de restringidos** — acceso técnico disponible (fuentes secundarias 2026: datawallet, coinperps, hyperliquidguide; **no es documentación oficial de Hyperliquid**).
- ⚠️ **Esto NO es asesoramiento fiscal ni legal.** Hyperliquid es un DEX sin licencia MiCA en España; el acceso técnico ≠ cobertura regulatoria. Contrasta con el bloqueo de Binance perps para residentes en España registrado en `research_r2_venues_es_2026.md`. **Antes de operar en live: verificar los Terms of Use vigentes y las obligaciones fiscales (AEAT, modelo 721 / ganancias patrimoniales).**

### 15.6 Bridge y custodia
- **USDC es ahora nativo en HyperCore**: *"natively minted on the Hyperliquid L1"*, con los contratos de Circle en HyperEVM. El **bridge legacy de Arbitrum** *"holds less than 10% of the USDC supply on HyperCore"* → el riesgo de bridge se ha reducido mucho respecto a 2024.
- Auditoría: el bridge legacy y su lógica con el staking L1 fueron *"audited by Zellic"*.
- Depósitos: USDC en Arbitrum/Ethereum/Base/Polygon a la dirección de depósito, o **CCTP** de Circle desde otras cadenas.
- Retiradas: acción `withdraw3`, **fee $1**, ~5 min.
- **Fee de activación**: *"New HyperCore accounts require 1 quote token (e.g., 1 USDC, 1 USDT) of fees for the first transaction which has the new account as destination address."* → **presupuestar $1 al crear la cuenta y $1 por cada retirada.** Sobre $1000 es 0,1% por viaje: no hacer micro-retiradas.
- **Riesgo residual**: el capital vive en una L1 con ~16 validadores y un conjunto de gobernanza pequeño. No es custodia bancaria. **No poner más de lo que se puede perder entero.**

### 15.7 Otros comportamientos que sorprenden
- **Self-trade prevention**: *"Trades between the same address cancel the resting order instead of causing a fill"*, *"No fees are deducted, nor does the cancel show up in the trade feed"* → **tu orden en reposo desaparece en silencio** si mandas una agresiva del otro lado. Si el bot hace maker en ambos lados, debe reconciliar `openOrders` y no fiarse de su estado interno.
- **Leverage solo se comprueba al abrir**: *"Leverage is only checked upon opening a position. Afterwards, the user is responsible for monitoring the leverage usage to avoid liquidation."*
- **Funding cap 4%/hora**: en un squeeze, $1000 de notional puede pagar $40/h.
- **Cap de 1000 órdenes abiertas**; con ≥1000 se rechazan nuevas reduce-only/trigger → un bot que deja TP/SL huérfanos puede autobloquearse.

---

## 16. Checklist de implementación segura — bot paper/live con $1000

Marcar TODO antes de pasar a live. Ninguno es opcional.

### A. Credenciales y firma
- [ ] **Agent/API wallet nueva y exclusiva** (clave privada fresca, generada en `app.hyperliquid.xyz/API`). **Nunca reutilizar una dirección de agent** (riesgo de replay tras pruning de nonces).
- [ ] `config.json`: `account_address` = **dirección MAESTRA**, `secret_key` = clave del **agent**. Fuera del repo, permisos 600, en `.gitignore`.
- [ ] Recordatorio en calendario a **170 días** para renovar el agent (expira a los 180).
- [ ] Verificado que `info` se consulta con la dirección MAESTRA (no la del agent) — si devuelve vacío, es este bug.
- [ ] `hyperliquid-python-sdk==0.24.0` y `eth-account` **pineados a versión exacta** en el lockfile. Test de firma en CI que rompa si cambia.
- [ ] Las acciones user-signed (`approveBuilderFee`, `withdraw3`, `usdSend`) NO las firma el agent → excluidas del bot o firmadas manualmente.

### B. Nonces y concurrencia
- [ ] **Un solo proceso** de trading con **un solo API wallet**. Cero paralelismo de firma.
- [ ] Nonce = `timestamp_ms` con **contador atómico** monótono (nunca dos iguales, nunca decreciente).
- [ ] Nonce siempre dentro de `(T − 2 días, T + 1 día)` → **NTP sincronizado en el CT** (verificar `timedatectl`; un reloj desfasado rompe TODAS las órdenes).
- [ ] `expiresAfter` (~5–10 s) en las L1 actions para que un request encolado caduque en vez de ejecutar tarde.

### C. Precios y tamaños (pre-flight de cada orden)
- [ ] `szDecimals` y `maxLeverage` **cacheados de `meta`** y refrescados al arrancar y cada N horas.
- [ ] Precio: `round(float(f"{px:.5g}"), 6 - szDecimals)` — ≤5 cifras significativas Y ≤`6 - szDecimals` decimales.
- [ ] Tamaño: `round(sz, szDecimals)`.
- [ ] **Sin trailing zeros** en los strings firmados (`"29000"`, no `"29000.0"`).
- [ ] **Notional ≥ $10** en TODA orden, incluidos TP/SL parciales → con $1000, planificar un máximo de ~3-4 slices por posición.
- [ ] Assert en código: si el pre-flight falla, **no enviar** (no dejar que lo rechace el exchange y quemar rate limit).

### D. Órdenes y protección
- [ ] **`slippage` explícito** en `market_open` (0.001–0.003). **Nunca usar el `DEFAULT_SLIPPAGE = 0.05` del SDK.**
- [ ] Validar profundidad en `l2Book` antes de enviar una IOC: si el notional consume más de X bps, abortar.
- [ ] **TP y SL nativos on-chain enviados junto con la entrada**, `grouping="positionTpsl"`, `isMarket: true`, **`reduceOnly: true`**. Un stop en memoria del bot NO es un stop.
- [ ] SL calculado contra el **mark price** (no last), y **por dentro del precio de liquidación** con margen.
- [ ] `cloid` (hex 128 bits) en toda orden → idempotencia y reconciliación tras timeout.
- [ ] Comprobar `statuses[]` orden por orden: `status:"ok"` global **no** significa que la orden entrase.
- [ ] Mapeados y manejados sin reintento ciego: `minTradeNtlRejected`, `tickRejected`, `perpMarginRejected`, `badAloPxRejected`, `iocCancelRejected`, `badTriggerPxRejected`, `marketOrderNoLiquidityRejected`, `oracleRejected`, `*OpenInterestCap*`, `reduceOnlyRejected`.

### E. Dead-man's switch (obligatorio en live)
- [ ] `scheduleCancel` armado y **re-armado periódicamente** (p.ej. cada 60 s a T+120 s). Si el bot muere, las órdenes se cancelan solas.
- [ ] Respetar el límite: **máx 10 usos por día UTC**, mínimo 5 s en el futuro → re-armar con `modify`, no consumir usos.
- [ ] ⚠️ Ojo: `scheduleCancel` cancela **todas** las órdenes, **incluidos los TP/SL**. Diseñar la secuencia para que el kill-switch cierre la posición ANTES de cancelar las protecciones, o aceptar quedar plano.

### F. Riesgo y sizing ($1000)
- [ ] **Leverage ≤ 3x** (menos cola de ADL, más distancia a liquidación). `updateLeverage` explícito al arrancar; no confiar en el default de la cuenta.
- [ ] **Isolated margin** por posición → una liquidación no se lleva la cuenta entera.
- [ ] **Solo BTC / ETH / SOL.** Sin memecoins ni activos con OI bajo (riesgo JELLY).
- [ ] Riesgo por operación ≤ 1% ($10) → con SL a 1,5%, tamaño ≈ $650 de notional. Comprobar que cada slice sigue ≥ $10.
- [ ] Budget de costes explícito: **round-trip taker-taker = 9 bps ≈ $0,90** sobre $1000. El edge esperado debe superar 9 bps + slippage + funding, o la estrategia pierde por construcción.
- [ ] Vigilar `funding` en `metaAndAssetCtxs` antes de mantener posición overnight (**cap 4%/hora**); regla de salida si funding supera un umbral.
- [ ] Máx drawdown diario ($ y %) que dispara halt + `scheduleCancel` inmediato.

### G. Datos, reconexión y reconciliación
- [ ] WS con **ping cada ~50 s** (el servidor corta a los 60 s de silencio).
- [ ] Backoff exponencial en reconexión + **re-suscripción completa** (nada se reenvía tras la desconexión).
- [ ] **Reconciliación REST obligatoria tras cada reconexión**: `openOrders` + `clearinghouseState` + `spotClearinghouseState` + `userFillsByTime` desde el último fill conocido. **La verdad es el exchange, no el estado en memoria.**
- [ ] Deduplicar fills por `tid` (el primer mensaje de `userFills` trae `isSnapshot: true`).
- [ ] Suscrito a `orderUpdates`, `userFills`, `userEvents` (captura `liquidation` y `nonUserCancel` — así se entera de un self-trade cancel o una liquidación).
- [ ] Heartbeat REST periódico (la sesión REST expira a los ~3 min de inactividad, issue #293) o `requests.Session` con retry propio.
- [ ] Reloj: los `time` del exchange en ms UTC; no mezclar con hora local.

### H. Rate limits
- [ ] Contador de weight por IP: **≤1200/min**. `l2Book`/`clearinghouseState` = 2, la mayoría de `info` = 20, `userRole` = 60.
- [ ] Monitorizar `userRateLimit` (`nRequestsUsed` vs `nRequestsCap`). El presupuesto por dirección es **1 request por 1 USDC de volumen acumulado + 10.000 iniciales**; agotarlo baja a **1 request cada 10 s** (bot inutilizado).
- [ ] Alerta al 70% del presupuesto por dirección. **Prohibido reintentar en bucle** un rechazo.
- [ ] Contar acciones diarias si la cuenta está en Unified/Portfolio Margin (**cap 50.000/día**).
- [ ] No dejar órdenes huérfanas (**cap 1000 abiertas**); barrido de limpieza al arrancar (`cancel_open_orders.py`).

### I. Paper → live
- [ ] Fase 1 **testnet**: valida firma, formato, TP/SL, reconexión, dead-man's switch. **NO valida edge ni slippage** (libro vacío).
- [ ] Fase 2 **paper con datos de mainnet reales** (WS mainnet, ejecución simulada con el `l2Book` real y coste 4,5 bps taker + funding real). Aquí es donde se mide el edge.
- [ ] Fase 3 **live con tamaño mínimo** ($10-20 por orden) durante ≥2 semanas: compara fills reales vs paper. **Si el slippage real supera al modelado, volver a fase 2.**
- [ ] Fase 4: escalar a $1000 solo si el PnL live ≈ PnL paper.
- [ ] Presupuestar **$1 de activación** + **$1 por retirada**; no hacer micro-retiradas.
- [ ] **NO adjuntar builder fee** (es coste extra para el propio bot). Sí considerar **código de referral** (4% de descuento hasta $25M de volumen) — gratis.
- [ ] Logs JSONL de toda acción firmada (action, nonce, cloid, respuesta) con rotación — imprescindible para el post-mortem del primer fallo.

### J. Antes de pulsar "live"
- [ ] Verificado en el CT: `hostname`, `timedatectl` (NTP ok), versión del SDK pineada, conectividad a `api.hyperliquid.xyz`.
- [ ] Simulacro de kill: matar el proceso y **comprobar en la UI** que `scheduleCancel` limpió las órdenes y que los TP/SL nativos siguen protegiendo (o que quedó plano).
- [ ] Simulacro de desconexión: cortar la red 2 min y verificar que reconecta y reconcilia sin duplicar posición.
- [ ] Revisados los Terms of Use vigentes y la situación fiscal en España.
- [ ] Escrito el runbook de "qué hago si el API está caído 30 minutos con posición abierta".

---

## Fuentes

**Oficiales (Hyperliquid Docs — gitbook.io/hyperliquid-docs)**
- API: `/for-developers/api`, `/api/info-endpoint`, `/api/exchange-endpoint`, `/api/signing`, `/api/nonces-and-api-wallets`, `/api/tick-and-lot-size`, `/api/asset-ids`, `/api/notation`, `/api/error-responses`, `/api/rate-limits-and-user-limits`, `/api/websocket/{subscriptions,post-requests,timeouts-and-heartbeats}`, `/api/priority-fees`, `/api/optimizing-latency`, `/api/activation-gas-fee`
- Trading: `/trading/fees`, `/funding`, `/margining`, `/margin-tiers`, `/liquidations`, `/auto-deleveraging`, `/robust-price-indices`, `/self-trade-prevention`, `/order-types`, `/take-profit-and-stop-loss-orders-tp-sl`, `/sub-accounts`, `/account-abstraction-modes`, `/builder-codes`
- HyperCore: `/hypercore/usdc`, `/hypercore/api-servers`, `/hypercore/vaults`, `/hypercore/vaults/for-vault-leaders-legacy`
- Onboarding: `/onboarding/how-to-start-trading`, `/onboarding/testnet-faucet`

**SDK oficial**
- https://github.com/hyperliquid-dex/hyperliquid-python-sdk (README, `hyperliquid/exchange.py`, `hyperliquid/utils/signing.py`, `examples/`, issues #275, #293, #54)
- https://pypi.org/project/hyperliquid-python-sdk/ (v0.24.0, 2026-06-04)

**Secundarias (2025-2026, marcadas como NO oficiales en el texto)**
- The Block — "Hyperliquid says API outage was caused by 'spike in traffic'" (2025-07-29) y "Hyperliquid plans automated refunds"
- CoinDesk — "HyperLiquid Delists JELLY After Vault Squeezed in $13M Tussle" (2025-03-26)
- OAK Research — análisis del ataque JELLY
- Chainstack — "Hyperliquid Agent Wallets and Nonce State Machine" / "Debugging signature errors"
- datawallet.com, coinperps.com, hyperliquidguide.com — países soportados/restringidos 2026

---

*Documento completo. Última actualización: 2026-08-31.*
