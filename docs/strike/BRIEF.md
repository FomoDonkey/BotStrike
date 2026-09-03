# Strike Finance V2 — ficha técnica para BotStrike (2026-09-03)

Fuentes: `docs.strikefinance.org` (Markdown oficial, índice en `llms.txt`, copiado en `docs/strike/docs__*.md`),
repo `strike-finance/strike-finance-skills` (OpenAPI 3.0: `skills__openapi__{trade,user,market}-api.yaml`, skills
`skills__skills__*`), repo `strike-finance/strike-builder-reference` (`ref__*`), y llamadas reales a la API pública
desde el PC de Edgar el 2026-09-03 (~01:30Z).

## 1. Arquitectura (V2, desde 2026)
- **CLOB off-chain** ("Strike Node", determinista, auditable por replay) + custodia/liquidación on-chain en
  contratos "locker" por cadena (Cardano, Ethereum, Solana). Depósitos: quote → tx on-chain → confirmación por
  validadores; los activos volátiles (ADA, ETH) se convierten a stablecoin al entrar, saldos 1:1 USD.
- Órdenes: market, limit, stop, stop_limit, take_profit, take_profit_limit, trailing_stop_market; TIF GTC/IOC/FOK;
  post_only, reduce_only, close_position, price_protect; bracket TP/SL (OCO) en un solo envío; batch; replace
  atómico; TWAP y Grid (algo). Margen cross/isolated por símbolo. Liquidación graduada + fondo de seguro + ADL.
- Precio-tiempo (FIFO). Los market orders llevan un límite automático de slippage respecto al mark.

## 2. Autenticación y custodia (resuelve el diseño de custodia del bot)
- **API Wallet = par Ed25519** (32 bytes). Se genera en https://app.strikefinance.org/api-keys o localmente y se
  registra la clave pública en esa página (mainnet). Cabeceras en cada petición autenticada:
  `X-API-Wallet-Public-Key` (64 hex), `X-API-Wallet-Signature` (128 hex), `X-API-Wallet-Timestamp` (s),
  `X-API-Wallet-Nonce` (UUID v4). Mensaje firmado: `{METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{SHA256(body)}`
  (body `""` en GET → sha256 de cadena vacía).
- **La API wallet puede operar pero NO puede retirar fondos** (documentado). Retiradas exigen firma de la wallet
  on-chain. → El CT solo guarda la clave Ed25519; la wallet de Cardano de Edgar nunca toca el servidor.
- Subcuentas: cabecera/parámetro de subcuenta (ver `docs__api__trade__subaccounts.md`); permiten aislar el
  capital del bot (canary) del resto. Vaults: mismos endpoints con `vault_id` (líder del vault).
- El cliente actual `exchange/strike_client.py` implementa ESTA firma correctamente (`_sign_request`).

## 3. URLs
| Uso | Mainnet | Testnet |
|---|---|---|
| Trade/User REST | `https://api.strikefinance.org` | `https://api-v2-testnet.strikefinance.org` |
| Market REST | `https://api.strikefinance.org/price` | `https://api-v2-testnet.strikefinance.org/price` |
| WS público | `wss://api.strikefinance.org/ws/price` | (testnet análogo) |
| WS usuario | `wss://api.strikefinance.org/ws/user-api` | |
Rate limits (exchangeInfo): 2 400 "request weight"/min, 1 200 órdenes/min.
Los endpoints `/v1/stats/...` que usa el cliente actual devuelven **404** → obsoletos; las estadísticas de
plataforma están en `docs__api__platform-stats*.md` (paths distintos).

## 4. Endpoints (OpenAPI)
- Trade: `POST /v2/order`, `GET /v2/order`, `DELETE /v2/order/cancel`, `DELETE /v2/order/cancel-all`,
  `POST /v2/order/strategy` (bracket), `POST /v2/orders/batch`, `POST /v2/order/replace`, `/replace-batch`,
  `GET /v2/openOrders`, `POST/GET/DELETE /v2/algo/twap`, `GET /v2/history/order|fill`, `POST /v2/leverage`
  (1–125, solo nuevas posiciones), `POST /v2/marginMode` (no con posición abierta), `POST /v2/isoMargin`.
- User: `GET /v2/account` (wallet_balance, available_balance, unrealized_pnl, margin_balance, total_margin,
  maintenance_margin, symbol_settings{margin_mode, leverage}), `GET /v2/balances`, `GET /v2/portfolio`,
  `GET /v2/positions`, `GET /v2/closedPositions`, `GET /v2/history/order|fill|funding|transaction`.
- Market (sin auth): `GET /v2/exchangeInfo`, `/v2/klines`, `/v2/premiumIndex` (mark, index, fundingRate,
  nextFundingTime, interestRate), `/v2/markPrice`, `/v2/indexPrice`, `/v2/ticker/24hr`, `/v2/ticker/price`,
  `/v2/ticker/bookTicker`, `/v2/depth`, `/v2/trades`, `/v2/openInterest`.
- **CreateOrderRequest** (campos EXACTOS, snake_case): `symbol`, `side` ∈ {buy, sell}, `type` ∈ {limit, market,
  stop, stop_limit, take_profit, take_profit_limit, trailing_stop_market}, `size` (string, base asset), `price`,
  `stop_price`, `time_in_force`, `working_type` ∈ {mark_price, contract_price}, `post_only`, `reduce_only`,
  `close_position`, `price_protect`, `vault_id`, `callback_rate`, `activation_price`, `slippage`,
  `client_order_id`. Respuesta 201: `client_order_id, account_id, symbol, sequence_id, message_id` (la orden se
  confirma por WS/GET, no en la respuesta).
- **El cliente actual envía `quantity`, `stopPrice`, `timeInForce`, `postOnly`, `reduceOnly`, `clientOrderId` y
  side/type en MAYÚSCULAS → incompatible con la API real. Hay que reescribir `place_order`, `place_bracket_order`,
  `replace_order`, `batch_orders` contra el esquema.**

## 5. Mercados (exchangeInfo, 2026-09-03: 31 símbolos, todos `PERPETUAL`, status `trading`)
Cripto: BTC, ETH, SOL, ADA, XRP, BNB, HYPE, ZEC, NEAR, PUMP, NIGHT. TradFi: **XAU (oro), XAG (plata), SP500,
NAS100, WTI (petróleo)**, acciones NVDA, TSLA, GOOGL, COIN, MU, SNDK, CRCL, AAOI, DRAM, SKHYNIX, SPCX, UNITREE,
MINIMAX, ZHIPU, CXMT. Cada símbolo trae `filters` (PRICE_FILTER tick, LOT_SIZE min/step/max, MARKET_LOT_SIZE…),
`liquidationFee`, `marketTakeBound`/`limitTakeBound`, `triggerProtect`, `settlePlan`. Ejemplos de mínimos:
BTC 0.00001, ETH 0.001, XAU 0.001 oz (≈ 4,7 $), SP500 0.001 (≈ 7,8 $), NAS100 0.0001, WTI 0.01, NVDA 0.01.
→ **Con 300 $ ya son operables 8–10 mercados**; el nocional mínimo no es el cuello de botella (a diferencia de
Binance con 100 USDT).
- El universo debe leerse de `exchangeInfo` en cada arranque y filtrarse por `status == trading`, volumen 24 h
  (`ticker/24hr.quoteVolume`) y antigüedad de datos; los mercados nuevos entran solos cuando cumplen el filtro
  (petición de Edgar: "todo lo que ofrezca Strike y vayan incorporando").

## 6. Datos históricos en Strike (klines diarias medidas)
BTC desde 2026-03-20 (167 velas), XAU y WTI desde 2026-04-23 (133), NVDA desde 2026-06-04 (91), SP500 desde
2026-08-16 (18). Volumen bajo en TradFi (SP500: 0,02 contratos/día en la primera vela).
→ **Research y señales de lookback largo NO pueden depender de las velas de Strike.** Diseño: fuente de señal
(Binance spot diario para cripto; Stooq/Yahoo diario para XAUUSD, XAGUSD, ^SPX, ^NDX, CL.F) separada del venue
de ejecución (Strike marks para fills, funding y riesgo). Tracking modelo↔paper con el precio de Strike.

## 7. Costes
- Fees (UI y `docs__perpetuals__trading-fees.md`): taker 0,045 %, maker −0,005 % (rebate) en el tier base;
  Edgar: 0,04275 %/−0,005 %. Liquidación 1,25 % (BTC). Mantenimiento 0,4–2,5 % por tiers (`docs__perpetuals__margin-tiers.md`).
- Funding cada 8 h (`nextFundingTime`), fórmula con `interestRate` 0,01 %/8 h + premium (`docs__perpetuals__funding-rates.md`).
  Ejemplo 2026-09-03 01:30Z: ADA +0,0000169/8 h (≈ 1,8 %/año), BNB +0,0000126, AAOI 0. Hay que **registrar el funding
  de Strike** (job cada 8 h → `data/strike_funding.parquet`) porque no existe endpoint público de histórico.
- Slippage: market orders con límite automático respecto al mark; `slippage` opcional (p. ej. "0.05" = 5 %).

## 8. WebSocket
- Público (`/ws/price`): markPrice, depth (deltas + snapshot REST), trade, kline; ver `docs__api__market__websocket.md`.
- Usuario (`/ws/user-api`): fills, órdenes, posiciones, balance; autenticado con las mismas cabeceras/firma;
  ver `docs__api__user__websocket.md` (36 KB) y `skills__skills__strike-userstream__SKILL.md`.

## 9. Decisión de operabilidad (gate P0.3)
**Sí: el trend diario (y su versión multi-activo) es operable en Strike con ≥ 300 $.** Condiciones: (a) API wallet
registrada por Edgar en app.strikefinance.org/api-keys y guardada en el CT (solo esa clave; sin capacidad de
retirada); (b) subcuenta o cuenta dedicada al canary; (c) cliente reescrito contra el esquema V2 y probado en
testnet + lectura en mainnet antes de la primera orden; (d) funding de Strike registrado desde el día 1;
(e) universo dinámico con filtro de liquidez; (f) datos de señal externos para TradFi.

## 10. Plan de integración (orden de trabajo)
1. `exchange/strike_client.py`: corregir órdenes al esquema V2 (snake_case, minúsculas, `size`), añadir
   `get_premium_index`, `get_ticker_24h`, `get_exchange_info` tipado, `get_closed_positions`, `get_funding_history`,
   subcuenta/`vault_id`; eliminar `/v1/stats`. Tests unitarios con HTTP simulado que validan los cuerpos contra
   el OpenAPI (campos requeridos y enums).
2. `scripts/strike_smoke.py`: solo lectura (exchangeInfo, premiumIndex, account, positions, balances) con la
   API wallet; luego una orden `limit` post-only lejos del mercado + cancel en la subcuenta canary.
3. Motor: `exchange_venue=strike` → market data por REST/WS de Strike (marks), universo dinámico, funding
   acumulado, paper con precios de Strike; `SYMBOL_MAP` Strike ("XAU-USD" nativo, sin conversión).
4. UI: Settings → Connection: API wallet de Strike (pegar pública/privada, se guarda en `data/secrets.json` 600,
   nunca se devuelve), estado "conectado a Strike: cuenta X, saldo Y", selector de subcuenta.
5. Research P1: datos diarios TradFi (Stooq) + universo multi-activo + funding → GO/NO-GO.
6. Canary en mainnet con 50–100 $ en subcuenta dedicada, 30 días.
