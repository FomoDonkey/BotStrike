# Estado del Arte (SOTA) en trading algorítmico cripto — Agosto 2026

> Investigación para BotStrike. Fecha: 2026-08-29. Autor: Claude (agente de investigación).
> Convención: **[EVIDENCIA]** = dato con fuente verificable (URL/fecha). **[OPINIÓN]** = juicio del autor basado en la evidencia. **[NO VERIFICADO]** = dato de memoria no contrastado hoy.
> Estado del documento: COMPLETO (secciones 1-9). Búsquedas web realizadas: 27; fetches de fuentes primarias: 20.

## Índice
1. Estado del mercado cripto perp 2026
2. Estrategias con edge realista para retail ($1k) — y cuáles NO
3. ML/IA para trading: qué funciona vs hype
4. Validación SOTA (walk-forward, CPCV, DSR, PBO, costes)
5. Riesgo y ejecución para cuentas pequeñas
6. Arquitectura/infra 24/7 y frameworks de referencia
7. Regulación 2026 (España/UE, MiCA, Binance por jurisdicción)
8. Síntesis final: diagnóstico de BotStrike, 10 recomendaciones, verdad incómoda
9. Bibliografía (todas las URLs)

---

## 1. Estado del mercado cripto perp 2026

### 1.1 Volúmenes y estructura de mercado
- **[EVIDENCIA]** Los perpetuos son el instrumento dominante: ~77% de los ~$79T de volumen cripto de los últimos 12 meses son perps (Datawallet, "Crypto Perpetual Futures Statistics & Trends in 2026", https://www.datawallet.com/crypto/crypto-perpetual-futures-statistics). Binance + OKX siguen siendo las anclas de liquidez (≈$275B en futuros de BTC solo en las 2 primeras semanas de 2026, misma fuente).
- **[EVIDENCIA]** Hyperliquid procesó ~$633B en Q1-2026; volumen acumulado >$4.7T a junio 2026; cuota del volumen perp on-chain 36% (ene-26) → 44% (mitad 2026); los DEX perp ya son ~16.5% del volumen perp total (CryptoBriefing https://cryptobriefing.com/hyperliquid-decentralized-perpetual-trading-volume/ ; Yellow Research https://yellow.com/research/hyperliquid-perp-volume-dominance-how-2026).
- **[EVIDENCIA]** Binance está empujando fuerte "TradFi perps" (perpetuos sobre acciones/índices/RWA): ~76% del volumen de equity-perps en julio 2026; volumen semanal x79 desde enero (Cryptonomist 2026-08-19, https://en.cryptonomist.ch/2026/08/19/binance-tradfi-perpetuals/). Esto se refleja en el changelog de la API (sesiones de trading, `tradingSchedule`, funding "Special" por dividendos).
- **[OPINIÓN]** Implicación para BotStrike: BTC/ETH/SOL en Binance USDT-M son los libros más competidos del planeta. Cada bp de ineficiencia a 1-5 min lo disputan MMs con colocation en Tokio. El edge, si existe, no vendrá de velocidad ni de microestructura pura.

### 1.2 Fees Binance USDT-M Futures (VIP0) — agosto 2026
- **[EVIDENCIA]** VIP0: **maker 0.02% (2 bps), taker 0.05% (5 bps)**; con pago en BNB −10% → taker 0.045%, maker 0.018%. VIP9: 0% maker / 0.017% taker (BitDegree 2026 https://www.bitdegree.org/crypto/tutorials/binance-fees ; TradersUnion https://tradersunion.com/brokers/crypto/view/binance/futures-fees/ ; DappGrid https://dappgrid.com/binance-futures-fees-explained/). La página oficial https://www.binance.com/en/fee/futureFee requiere login para mostrar la tabla (verificado 2026-08-29: "No records found" sin sesión).
- **[EVIDENCIA]** Aritmética que manda: un round-trip taker/taker cuesta **10 bps** (9 bps con BNB); maker/maker **4 bps**; maker-entrada + taker-salida (lo habitual para un mean-reversion con SL) **7 bps**. En BTC a ~1-5 min el rango medio de una barra es del orden de 5-15 bps → el coste de transacción es del mismo orden de magnitud que el movimiento que se intenta capturar. (Ver sección 2.)
- **[EVIDENCIA]** Binance introdujo órdenes **RPI (Retail Price Improvement)** en nov-2025: órdenes maker que sólo pueden ser ejecutadas por flujo "retail" (no-API), con libro `rpiDepth` separado y stream `<symbol>@rpiDepth@500ms`; el campo `nq` en `aggTrade` (2025-12-31) excluye los trades RPI. Changelog oficial https://developers.binance.com/docs/derivatives/change-log (entradas 2025-11-18, 2025-11-26, 2025-12-31). **[OPINIÓN]** Es un mecanismo de segmentación de flujo al estilo TradFi (PFOF-lite): el flujo retail "tóxico-para-nadie" se lo quedan los MMs con RPI; un bot por API queda por definición en el lado *no* retail, compitiendo contra flujo más informado. Mala noticia para market making retail vía API.

### 1.3 Funding
- **[EVIDENCIA]** El funding en BTC/ETH es cíclico y bimodal; en 2025 el rendimiento medio anualizado de estrategias de funding-arb reportado por vendedores fue 14-19% (dato de industria, no auditado: Medium/ArbitrageGhost 2026 https://medium.com/@arbitrageghost/funding-rate-arbitrage-in-2026-the-complete-guide-with-real-calculations-40e6cf341e52 ; ArbitrageScanner https://arbitragescanner.io/blog/crypto-funding-rate-arbitrage-guide dice 8-20% APY). Un paper 2026 (MDPI Mathematics 14(2):346 "The Two-Tiered Structure of Cryptocurrency Funding Rate Markets", https://www.mdpi.com/2227-7390/14/2/346) documenta que **sólo ~40% de las mejores oportunidades son rentables tras costes y reversión del spread**, aunque el 17% de las observaciones muestran spreads ≥20 bps.
- **[EVIDENCIA]** Nuevo en 2026: `GET /fapi/v1/fundingRate` devuelve `rateType` = "Regular" | "Special" (funding especial por dividendos en TradFi-perps, changelog 2026-07-23). Un bot que use funding como feature debe filtrar `rateType`.
- **[OPINIÓN]** Para una cuenta de $1k, funding-arb puro (spot long + perp short) rinde ~$100-150/año bruto en el mejor caso, menos fees de entrada/salida (≥4 round-trips al año × ~10 bps × 2 patas). Es "positivo pero irrelevante" a esa escala; sirve más como **filtro de régimen** (no ir largo cuando funding >0.05%/8h, etc.) que como estrategia.

### 1.4 Cambios de API Binance Futures 2025-2026 (relevantes para el bot)
Fuente: changelog oficial https://developers.binance.com/docs/derivatives/change-log (leído 2026-08-29). Cronología:
- **2025-01-13**: `GET /fapi/v1/historicalTrades` `limit` máx 1000→500, default 500→100.
- **2025-02-25**: **WebSocket API** de trading disponible en `wss://ws-fapi.binance.com/ws-fapi/v1` (equivalente funcional al REST: enviar/cancelar órdenes por WS → menor latencia y sin overhead HTTP).
- **2025-04-23**: nuevo `GET /fapi/v1/insuranceBalance`.
- **2025-07-02**: **máx. streams por conexión WS 200→1024**.
- **2025-07-25**: error `-4109` = cuenta inactiva; hay que transferir activos a la cuenta USDM para activarla.
- **2025-10-23**: `priceMatch` pierde `OPPONENT_10`/`OPPONENT_20`; campo `er` (expire reason) en `ORDER_TRADE_UPDATE`.
- **2025-11-18/26**: órdenes **RPI** (nuevo `timeInForce=RPI`), `rpiDepth`, `commissionRate` incluye RPI; **2025-11-19** `GET /fapi/v1/symbolAdlRisk` (rating de riesgo ADL por símbolo).
- **2025-12-10**: `tradingSchedule`, `tradingSession` (TradFi perps), `POST /fapi/v1/stock/contract`.
- **2025-12-15**: **`CONDITIONAL_ORDER_TRIGGER_REJECT` DEPRECADO** → los rechazos de órdenes condicionales llegan en `ALGO_UPDATE`. **Acción**: si el bot escucha ese evento para SL/TP condicionales, hay que migrar a `ALGO_UPDATE`.
- **2026-04-23**: **URL legacy de WebSocket decomisionada** (verificar que el bot usa `wss://fstream.binance.com/ws` / `/stream` y no hosts antiguos).
- **2026-05-13**: error `-4531` al cambiar `positionSide/dual` si requiere sync con cuenta CM.
- **2026-06-20**: `POST /fapi/v1/algoOrder` ahora cuenta contra el **order rate limit** (1 en 10s y 1min).
- **2026-06-29**: countdown (auto-cancel) COIN-M suspendido por migración CM (no afecta a USDT-M).
- **2026-07-21**: `modifyId` opcional en `PUT /fapi/v1/order`, `batchOrders`, WS `order.modify`; campo `M` en `ORDER_TRADE_UPDATE` (x="AMENDMENT").
- **2026-08-07**: `ACCOUNT_UPDATE` añade `S` (símbolo) cuando la razón es `FUNDING_FEE`; `ALGO_UPDATE` añade `ia` (activación de trailing stop, plenamente activo desde 2026-08-21).
- **2026-08-11**: `aggTrades` lookback 24h→48h.
- **2026-08-25**: `GET /fapi/v1/allOrders` `symbol` pasa a opcional.
- **2026-08-26**: `GET /fapi/v1/userTrades` **sólo devuelve últimos 3 meses** (antes 6). **Acción**: la reconciliación de trades/PnL debe persistir localmente; no confiar en el exchange como fuente histórica.
- **[EVIDENCIA]** Límites WS market streams: 10 mensajes entrantes/s por conexión; conexiones repetidamente desconectadas → ban de IP (docs https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams). Límites REST estándar (no cambiados): 2400 weight/min por IP, 300 órdenes/10s y 1200/min por cuenta **[NO VERIFICADO hoy, valores de docs 2024-2025]**.

### 1.5 Hyperliquid (estado agosto 2026)
- **[EVIDENCIA]** Fees perps tier base (a 2026-08-02): **maker 0.015%, taker 0.045%**; spot 0.04%/0.07%. Tiers por volumen rodante de 14 días: taker 0.040% desde $5M, 0.035% desde $25M… hasta 0.024% en $5B+. Descuentos apilables (referral, staking HYPE, VIP, rebates de quote-asset) (Eco.com 2026 https://eco.com/support/en/articles/15191998-hyperliquid-fees-explained-maker-taker-funding-and-withdrawal-in-2026 ; hyperliquidguide.com https://hyperliquidguide.com/guides/fees/fees-explained). Sin gas, sin KYC on-chain.
- **[EVIDENCIA]** Rate limits API (docs oficiales https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits): REST 1200 weight/min por IP (`l2Book`, `allMids`, `clearinghouseState` pesan 2; la mayoría de `info` 20; `userRole` 60); exchange requests peso `1 + floor(batch/40)`. WS: máx 10 conexiones, 30 nuevas/min, 1000 subscripciones, 2000 msgs/min, 100 posts en vuelo. **Límite por dirección: 1 request por cada 1 USDC de volumen acumulado, buffer inicial 10.000 requests; al agotarlo 1 req/10s**; cancelaciones `min(limit+100k, limit×2)`; 1000 órdenes abiertas por defecto.
- **[OPINIÓN]** Para $1k, el límite "1 request por USDC operado" es la restricción real: con 10k requests de buffer y un bot que hace polling agresivo, se agota en horas si no se genera volumen. Diseñar la integración con WS push (no polling) y batch de órdenes. Ventaja real vs Binance: fee taker 4.5 bps vs 5 bps (marginal), maker 1.5 vs 2 bps, y **no hay riesgo jurisdiccional MiCA** (ver §7), pero sí riesgo de smart-contract/bridge y de un validador set pequeño.

---

## 2. Estrategias con edge realista para retail ($1k) — y cuáles NO

### 2.0 La aritmética que nadie quiere hacer (scalping/mean-reversion intradía en BTC)
**[EVIDENCIA + cálculo propio]** Supuestos: VIP0, entrada maker (2 bps) + salida taker (5 bps) = **7 bps** por round-trip (6.3 con BNB); taker/taker = **10 bps**. Volatilidad realizada BTC ~45% anualizada → σ por barra ≈ 45%/√525.600 ≈ **6 bps a 1 min, ~14 bps a 5 min, ~22 bps a 15 min** (cálculo: σ_1min = 0.45/725 = 0.062%).
- Con objetivo/stop simétricos de ±X bps y coste c, la **tasa de acierto de equilibrio es p* = 0.5 + c/(2X)**:
  - X = 15 bps (≈1σ a 5 min), c = 7 → **p* = 73.3%**; con taker/taker (c = 10) → **83.3%**.
  - X = 30 bps, c = 7 → p* = 61.7%; X = 60 bps, c = 7 → p* = 55.8%.
- Para comparar: la predictibilidad intradía documentada en Binance USDT-M perps es del orden de **0.5 bps por evento** (ver abajo), es decir, **~1/10 de una comisión taker y ~1/20 de un round-trip**.
- **[OPINIÓN]** Conclusión matemática: una estrategia que rota cada 1-5 min con SL/TP del orden de 1σ necesita una tasa de acierto de 70-85% sostenida para simplemente **no perder**. Nadie retail sostiene eso en BTC contra MMs colocados en Tokio. Para que la esperanza sea positiva con c = 7 bps hay que (a) operar horizontes donde el movimiento esperado sea ≥ 5-10× el coste (≥ 40-70 bps → horizontes de 30 min-4 h en BTC, o más), o (b) ser maker en ambas patas (4 bps) aceptando riesgo de no-ejecución y adverse selection, o (c) operar activos con más volatilidad por unidad de fee (SOL/ADA: σ ~1.5-2.5× BTC, mismo fee).

### 2.1 Mean reversion intradía (Z-score/RSI/Bollinger/OBI a 1-5 min) — **edge negativo tras costes en BTC/ETH; marginal en alts**
- **[EVIDENCIA]** "The Quarter-Hour Effect" (arXiv 2607.09426v2, jul-2026, https://arxiv.org/html/2607.09426v2): Binance USDT-M perps BTC/ETH/XRP/SOL/DOGE/ADA, barras de 10 s, 2021-01→2024-10. Encuentran reversión de corto plazo (autocorrelación negativa, revierte en <30 min) y momentum a 4-12 h por order-flow imbalance. R² OOS 1.2-5.8% en la apertura de cuarto de hora, pero el componente predecible es **≈0.5 bps por evento**: "not implementable as a standalone strategy", sólo útil para *timing* de ejecución.
- **[EVIDENCIA]** "Microstructure alpha: hierarchical learning and cross-asset transfer in cryptocurrency markets" (Frontiers in Blockchain, 2026, https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full): Binance spot+perps BTC/ETH/SOL/AVAX/LINK/DOT, barras de 1 min ago-2025→feb-2026 (~3.4M obs), objetivo retorno a 5 min. Features: Corwin-Schultz, RV, intensidad de trades/volumen, **VPIN, Kyle λ, Amihud, depth imbalance, OFI**. Resultados: OLS mejora R² OOS 1.23% vs random walk (DM 1.28, **no significativo**); LightGBM **sobreajusta catastróficamente (R² −10.94%)**; **Kyle λ y Amihud no son individualmente significativos**; simulación de trading con rebalanceo a 5 min y fees VIP0 → **Sharpe neto −18 (futuros) a −52 (spot)** con turnover 124-204× notional/día. Cita: "genuine but weak information content… **not exploitable at standard retail fee levels**".
- **[EVIDENCIA]** Bitcoin "wild moves" y toxicidad de flujo (Research in International Business and Finance 2025, https://www.sciencedirect.com/science/article/pii/S0275531925004192) y Easley-López de Prado-O'Hara sobre microestructura cripto (SSRN 4814346, https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf): VPIN y medidas de liquidez **explican** dinámica y saltos; eso no es lo mismo que **rentabilidad neta** a horizonte de minutos.
- **[OPINIÓN]** Para BotStrike: los indicadores del bot (Z-score/RSI/BB/OBI/VPIN/Hawkes/Kyle λ) tienen respaldo académico como *descriptores*, pero la literatura 2025-2026 más directa (mismo exchange, mismos símbolos, mismas barras) dice que a 1-5 min **no cubren el fee**. El sitio donde la mean-reversion cripto sí ha mostrado algo es (i) horizontes de horas/días tras movimientos extremos (reversal post-shock), (ii) spreads entre pares cointegrados (ver 2.4), (iii) en alts con σ alta y con ejecución maker.

### 2.2 Momentum / trend following — **edge positivo documentado a horizonte diario-semanal, débil intradía**
- **[EVIDENCIA]** Zarattini, Pagani & Barbon, "Catching Crypto Trends" (SSRN 5209907, 2025, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907): modelo rotacional sobre las 20 monedas más líquidas; **Sharpe > 1.5 neto de fees, alfa anualizada 10.8% vs BTC**.
- **[EVIDENCIA]** Fieberg et al., "A Trend Factor for the Cross-Section of Cryptocurrency Returns" (JFQA, https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178): factor de tendencia (medias móviles multi-horizonte) con prima significativa en la sección cruzada.
- **[EVIDENCIA, contra]** Grobys & Shahzad, "Cryptocurrency momentum: Is it an illusion?" (SSRN 4633099, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4633099) y Han, Kang & Ryu, "Time-Series and Cross-Sectional Momentum… under Realistic Assumptions" (SSRN 4675565, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565): al incluir costes y fluctuaciones diarias, **muchas carteras de momentum pierden significación**; sólo sobreviven variantes con baja rotación y universo líquido.
- **[EVIDENCIA]** arXiv 2602.11708 "Systematic Trend-Following with Adaptive Portfolio Construction… in Cryptocurrency Markets" (feb-2026, https://arxiv.org/pdf/2602.11708): trend following con construcción adaptativa mejora el ratio riesgo/retorno; horizonte de días.
- **[OPINIÓN]** El edge robusto en cripto está en **time-series momentum a 1-4 semanas con baja rotación** (2-8 operaciones/mes por activo), no en el intradía. Es aburrido, tiene drawdowns largos (2022) y correlación alta con BTC, pero es lo único con réplica académica múltiple neta de costes. BotStrike lo tiene **archivado**: es exactamente al revés de lo que dice la evidencia.

### 2.3 Funding-rate / basis arbitrage — **edge positivo pero pequeño e insuficiente para $1k**
- **[EVIDENCIA]** MDPI Mathematics 2026 (https://www.mdpi.com/2227-7390/14/2/346): 17% de observaciones con spread ≥20 bps, pero **sólo ~40% de las mejores oportunidades son rentables tras costes**. Industria: 8-20% APY (ArbitrageScanner), media 2025 19% (ArbitrageGhost, no auditado). Un estudio 2025 en *Blockchain: Research and Applications* reporta hasta 115.9%/6 meses con MDD 1.92% en 60 escenarios BTC/ETH/XRP/BNB/SOL **[NO VERIFICADO: no he leído el paper; probable cherry-picking de ventanas]**.
- **[OPINIÓN]** Con $1k: $80-200/año bruto en el mejor caso, dos patas (spot + perp o dos exchanges), riesgo de contraparte y de inversión de funding. **No es una estrategia para esta cuenta; sí es un filtro de régimen** barato de implementar (funding extremo → sesgo contrarian a 8-24 h, documentado ampliamente).

### 2.4 Stat-arb / pares cointegrados — **edge positivo pero "órdenes de magnitud menor" tras costes**
- **[EVIDENCIA]** "Copula-based trading of cointegrated cryptocurrency pairs" (Financial Innovation 2024/25, https://link.springer.com/article/10.1186/s40854-024-00702-7) y estudio 2019-2024 con 10 criptos: rendimientos ajustados a riesgo positivos con baja exposición a mercado; **con costes realistas los excesos "remain statistically significant but are orders of magnitude smaller"**. Survey WNE UW 19/2025 (https://www.wne.uw.edu.pl/download_file/6095/0).
- **[EVIDENCIA]** DRL para stat-arb cripto: +79-113% OOS **sin costes** (ScienceDirect 2024, https://www.sciencedirect.com/science/article/abs/pii/S1568494624000292) — el paper mismo subraya que el resultado depende de asumir coste cero.
- **[OPINIÓN]** Con 4 símbolos (BTC/ETH/SOL/ADA) hay 6 pares; la cointegración entre ellos es inestable (beta a BTC domina). Posible a horizonte de horas-días con ejecución maker; **no** a minutos.

### 2.5 Market making retail (Avellaneda-Stoikov) — **NO en BTC/ETH top-tier; quizá en alts ilíquidas con riesgo de inventario**
- **[EVIDENCIA]** Binance segmenta el flujo retail vía órdenes **RPI** (nov-2025; §1.2): el MM por API queda fuera del flujo menos informado.
- **[EVIDENCIA]** Adverse selection drena cuentas de MM simples "at astonishingly fast speed" en backtests (Crypto Chassis, https://medium.com/open-crypto-market-data-initiative/defensive-market-making-against-market-manipulators-3ceabb5d1b71); "The Extremity Premium" (arXiv 2602.07018v2, feb-2026, https://arxiv.org/html/2602.07018v2): spreads y adverse selection suben en regímenes de sentimiento extremo (Glosten-Milgrom); no reporta PnL de MM retail. Latencia Europa→Tokio ~270 ms RTT (ver §6) vs 20-25 ms colocado.
- **[OPINIÓN]** Archivarlo fue correcto. Mantenerlo archivado.

### 2.6 Grid trading — **sin evidencia académica; vende bien, drawdowns brutales**
- **[EVIDENCIA]** Sólo literatura de vendedores (Gainium, 3Commas, Altrady). Ejemplo publicado de MDD −26.67% en un grid "de rango" cuando BTC tendió (Intralogic, https://intralogic.eu/backtest/en/blog/grid-bot-crypto-trading-advantages-risks-analysis). Rendimiento real 30-50% por debajo del backtest por slippage/latencia (TV-Hub 2026, https://www.tv-hub.org/guide/is-automated-trading-profitable — dato de industria). **[OPINIÓN]** Un grid es short-gamma sin cobertura: cobra pequeño y pierde grande. No.

### 2.7 Fibonacci retracement — **sin evidencia**
- **[EVIDENCIA]** No hay estudio revisado por pares 2024-2026 que muestre edge de niveles Fibonacci en cripto neto de costes; cuando se testan reglas técnicas contra pasivo (Palazzi 2025, *J. Futures Markets*, https://onlinelibrary.wiley.com/doi/full/10.1002/fut.70018) las que sobreviven son de **tendencia**, no de retroceso geométrico. **[OPINIÓN]** Como *feature* de nivel (S/R) es ruido correlacionado con swing highs/lows; como estrategia standalone, retirar.

### 2.8 Resumen sección 2 (tabla)
| Estrategia | Evidencia 2024-26 neta de costes | Horizonte con edge | Veredicto $1k |
|---|---|---|---|
| Mean reversion 1-5 min BTC/ETH | Negativa (Sharpe neto −18/−52; señal 0.5 bps) | — | **NO** |
| Mean reversion post-shock (horas-días) | Positiva débil | 4 h-3 d | Posible, pocas operaciones |
| TS momentum / trend | Positiva, replicada (SR>1.5 neto, alfa 10.8%) | 1-4 semanas | **SÍ (mejor candidato)** |
| Funding/basis arb | Positiva pequeña (40% rentable tras costes) | días-semanas | Filtro, no estrategia |
| Pares cointegrados | Positiva, "órdenes de magnitud menor" | horas-días | Marginal |
| Market making AS | Negativa para retail vía API | — | **NO** |
| Grid | Sin evidencia; MDD grandes | — | **NO** |
| Fibonacci | Sin evidencia | — | **NO** |

---

## 3. ML/IA para trading: qué funciona vs hype

### 3.1 Agentes LLM (FinAgent, FinMem, TradingAgents, FinCon, QuantAgent…) — **hype con evidencia de fracaso en replicación**
- **[EVIDENCIA]** "The Alpha Illusion: Reported Alpha from LLM Trading Agents Should Not Be Treated as Deployment Evidence" (arXiv 2605.16895, mayo-2026, https://arxiv.org/html/2605.16895v1). Replicación de 6 sistemas end-to-end + FinBen. Ventana 2025-01→2026-01, cartera de 5 tickers, con comisión + coste de tokens + spread + impacto: **TradingAgents Sharpe bruto 0.43 → neto 0.22; QuantAgent −0.96 → −1.15**; ambos por debajo de buy-and-hold. FinCon reportaba Sharpe 3.27 de cartera en su paper. **35 de 40 celdas sistema-componente no modelaban costes** en los papers originales. Contaminación: **FinMem −71.85% de retorno al cruzar el knowledge cutoff; QuantAgent −51.48% de Sharpe post-cutoff**. Conclusión textual: "the alpha reported by end-to-end LLM trading systems should not be interpreted as evidence of deployable trading capability"; recomiendan usar LLMs como **interfaz de información upstream, no como decisor final**.
- **[EVIDENCIA]** "Toward Reliable Evaluation of LLM-Based Financial Multi-Agent Systems" (arXiv 2603.27539, https://arxiv.org/html/2603.27539v1): FinAgent/FinCon/HedgeAgents/QuantAgents cumplen 2/5 criterios de calidad de evaluación; **FinMem 0/5, y su +23% en MSFT se convierte en −22% al re-evaluar con control**. StockBench (DJIA mar-jul 2025, post-cutoff): **la mayoría de agentes LLM no baten buy-and-hold**.
- **[EVIDENCIA]** "Beyond Agent Architecture: Execution Assumptions and Reproducibility in LLM-Based Trading Systems" (arXiv 2606.08285, https://arxiv.org/html/2606.08285) y "Agentic Trading: When LLM Agents Meet Financial Markets" (arXiv 2605.19337): los resultados dependen más de supuestos de ejecución (fill al close, sin spread) que de la arquitectura del agente.
- **[OPINIÓN]** Para BotStrike: **cero LLMs en el loop de decisión**. Uso legítimo y barato: parseo de noticias/anuncios de exchange (delistings, mantenimientos, cambios de tiers), generación de tests, revisión de código.

### 3.2 Reinforcement learning — **funciona en papers, no replica en producción; sensible a costes y recompensa**
- **[EVIDENCIA]** Bandarupalli 2025, "Risk-Aware DRL for Crypto and Equity Trading Under Transaction Costs" (SSRN 5662930, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5662930): PPO con penalización de turnover, OOS 2024: **Sharpe 1.23 vs 1.46 buy-and-hold**, NAV 1.916 vs 2.213. Honesto: RL pierde contra pasivo.
- **[EVIDENCIA, sospechoso]** DQN selector de estrategias técnicas en BTC 2022-2025 reporta **×120 NAV** (Cogent Economics & Finance 2025, https://www.tandfonline.com/doi/full/10.1080/23322039.2025.2594873). **[OPINIÓN]** Resultado incompatible con cualquier capacidad de mercado y sin CPCV/DSR: overfitting de libro.
- **[EVIDENCIA]** RL pair-trading (arXiv 2407.16103) muestra que el resultado cambia de signo entre tiers de coste 0.05% / 0.01% / 0%. MDPI Mathematics 2026 (14(5):794) sobre funciones de recompensa: las recompensas de beneficio puro "perform well during favorable market conditions but suffer catastrophic losses during downturns".
- **[OPINIÓN]** RL para *ejecución* (dónde colocar una orden límite, cuándo cruzar el spread) tiene sentido en firmas con datos L3 y colocation. Para señal direccional a 1-5 min con $1k: no.

### 3.3 Transformers / deep learning sobre precios y LOB — **predicen algo, no pagan el fee**
- **[EVIDENCIA]** LTSF-Linear (Zeng et al.) sigue vigente: un modelo lineal de una capa bate a transformers en 9 datasets de forecasting; "Why Do Transformers Fail to Forecast Time Series In-Context?" (arXiv 2510.09776, https://arxiv.org/pdf/2510.09776) prueba que la self-attention lineal **no puede batir al predictor lineal óptimo** en procesos AR. ICML 2025 "A closer look at transformers for time series forecasting" (https://dl.acm.org/doi/10.5555/3780338.3780628).
- **[EVIDENCIA]** LOB: CNN temporal logra 71% de acierto walk-forward a 2 s en Coinbase BTC (arXiv 2010.01241); pero "Deep limit order book forecasting: a microstructural guide" (LSE/Ideas 2025, https://ideas.repec.org/p/ehl/lserod/128950.html): "high forecasting power… does not necessarily correspond to actionable trading signals" y las métricas ML clásicas no miden calidad de señal. "Better Inputs Matter More Than Stacking Another Hidden Layer" (arXiv 2506.05764, https://arxiv.org/pdf/2506.05764): la ganancia viene del preprocesado, no de la profundidad. LiT (Frontiers AI 2025, https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1616485/full).
- **[EVIDENCIA]** El estudio Frontiers 2026 de §2.1: **LightGBM sobre features de microestructura a 1 min → R² OOS −10.94%** (sobreajuste) frente a OLS +1.23%.
- **[OPINIÓN]** Si se usa ML: modelos **lineales regularizados o gradient boosting muy restringido** (pocas features, monotonic constraints, early stopping con purged CV), objetivo a horizonte ≥ 1-4 h, evaluado con DSR/PBO. Los transformers no aportan nada a esta escala de datos.

### 3.4 Qué sí funciona en producción (evidencia + práctica de industria)
- **[EVIDENCIA]** Freqtrade integra **FreqAI** (regresores/clasificadores clásicos con reentrenamiento rodante, versión 2026.7) y ninguno de los frameworks serios (NautilusTrader, Hummingbot) mete LLMs/RL en el camino crítico de órdenes (docs oficiales, ver §6).
- **[OPINIÓN]** Jerarquía realista para BotStrike: (1) reglas simples con pocos parámetros (trend/carry/reversal a horas-días), (2) filtros de régimen (volatilidad, funding, hora del día — el Quarter-Hour Effect muestra que el *timing* de ejecución sí es explotable), (3) ML sólo para *meta-labeling* (López de Prado): decidir el tamaño/ir-no-ir de una señal primaria, no generar la señal.

---

## 4. Validación SOTA (walk-forward, CPCV, DSR, PBO, costes, mínimo de trades)

### 4.1 Herramientas y qué dice la evidencia
- **[EVIDENCIA]** Bailey & López de Prado, "The Deflated Sharpe Ratio" (JPM 2014; SSRN 2460551, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551; PDF https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf): el DSR corrige el Sharpe por (a) **número de pruebas** (selección múltiple), (b) longitud de muestra, (c) asimetría y curtosis. Requiere **registrar cuántas configuraciones se probaron** (N) y la varianza de los Sharpe entre pruebas. Fórmula: PSR(SR*) = Z[ (SR − SR*)·√(T−1) / √(1 − γ₃·SR + (γ₄−1)/4·SR²) ], con SR* = E[max SR de N pruebas] ≈ √V[SR]·((1−γ)Z⁻¹(1−1/N) + γZ⁻¹(1−1/(N·e))), γ≈0.5772.
- **[EVIDENCIA]** PBO / CSCV (Bailey, Borwein, López de Prado, Zhu, "The Probability of Backtest Overfitting", https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf): PBO = probabilidad de que la configuración elegida in-sample esté por debajo de la mediana out-of-sample. Se calcula con particiones combinatorias simétricas (S=16 típico → 12.870 combinaciones).
- **[EVIDENCIA]** Arian, Norouzi & Seco, "Backtest Overfitting in the Machine Learning Era" (Knowledge-Based Systems 2024; SSRN 4686376, https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID4686376_code4361537.pdf?abstractid=4686376&mirid=1): en entorno sintético controlado, **CPCV domina a K-fold, purged K-fold y walk-forward** en PBO y DSR. Walk-forward "tests only a single scenario and easily overfits"; sigue siendo el estándar de industria por realismo secuencial → usar **ambos**.
- **[EVIDENCIA]** Purging + embargo (López de Prado, *AFML* cap. 7; Wikipedia https://en.wikipedia.org/wiki/Purged_cross-validation; implementación sklearn-compatible https://github.com/eslazarev/purged-cross-validation con CPCV y DSR). Purgar = eliminar del train las muestras cuyas etiquetas se solapan temporalmente con el test; embargo = franja adicional tras el test (p. ej. 1-2% de la muestra o ≥ horizonte de la etiqueta).
- **[EVIDENCIA]** "Interpretable Hypothesis-Driven Trading: A Rigorous Walk-Forward Validation Framework for Market Microstructure Signals" (arXiv 2512.12924, https://arxiv.org/pdf/2512.12924) y "Implementation Risk in Portfolio Backtesting" (arXiv 2603.20319, 2026, https://arxiv.org/pdf/2603.20319): diferencias de implementación del *mismo* backtest producen errores no cuantificados antes → hay que versionar y testear el backtester como software crítico.
- **[EVIDENCIA]** Minimum Track Record Length (Bailey & LdP 2012, "The Sharpe Ratio Efficient Frontier"; herramienta https://github.com/tschm/jsharpe ; explicación https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/): MinTRL = 1 + [1 − γ₃·SR + (γ₄−1)/4·SR²]·(Z_α/(SR − SR*))². Ejemplo: con SR por observación de 0.1 (≈ Sharpe anual 1.6 con datos diarios), γ₃=0, γ₄=3, α=95% → MinTRL ≈ 1 + (1.645/0.1)² ≈ **271 observaciones**. Regla práctica de industria: **≥100 trades** para que un Sharpe 1.0 sea significativo al 95% (Medium/Trading Dude, https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05) — con retornos no normales y colas cripto, multiplicar por 2-3.

### 4.2 Look-ahead y otros sesgos que matan bots de 1-5 min
- **[OPINIÓN, práctica estándar]** (i) Barras resampleadas: la señal debe calcularse con la barra **cerrada** y ejecutarse en la **siguiente** (o en el primer tick posterior); (ii) indicadores con `center=True`, `bfill`, normalizaciones con estadísticas de toda la serie (z-score con σ global) = look-ahead; (iii) **funding**: aplicarlo en el timestamp exacto (00/08/16 UTC) sólo a posiciones abiertas; (iv) **mark price vs last price** para SL/liquidación; (v) fills: un `limit` no se llena por "tocar" el precio — exigir que el precio lo **atraviese** o modelar cola; (vi) survivorship en universos de alts; (vii) latencia: añadir 300-500 ms (Europa→Tokio, §6) entre señal y fill en el backtest.
- **[EVIDENCIA]** El estudio Frontiers 2026 (§2.1) usa exactamente **purged + embargoed walk-forward** y un modelo de slippage Corwin-Schultz; con eso, la microestructura a 5 min pasa de "significativa" a Sharpe neto negativo. Ese es el estándar mínimo.

### 4.3 Coste de transacción realista (modelo mínimo)
- Fee explícito por lado según tipo de orden (maker 2 / taker 5 bps; BNB −10%).
- **Medio spread** en cada cruce taker (BTCUSDT perp ≈ 0.1-1 bps en calma; 5-20 bps en eventos) **[NO VERIFICADO hoy; orden de magnitud habitual]**.
- **Impacto**: irrelevante con $5k notional en BTC; relevante en ADA/SOL en horas ilíquidas → usar profundidad L2 del backtest o un modelo √(notional/ADV).
- **Funding** en posiciones que cruzan 00/08/16 UTC; **slippage de stop-market** en gaps (asumir 2-3× el spread normal).
- **Fallo de fill de límites**: modelar probabilidad de no-ejecución (p. ej. sólo cuenta si el precio atraviesa el límite ≥ 1 tick).

### 4.4 Umbrales propuestos para aprobar una estrategia a live (BotStrike, $1k) — **[OPINIÓN fundada en lo anterior]**
| Criterio | Umbral | Por qué |
|---|---|---|
| Nº de trades OOS (CPCV + walk-forward) | **≥ 300 en total y ≥ 75 por símbolo** | MinTRL con SR/obs≈0.1 ⇒ ~270; colas cripto |
| Nº de trials registrado | **Obligatorio** (log de cada configuración probada) | Sin N no hay DSR |
| DSR | **> 0.95** (PSR con SR* = E[max SR de N trials]) | Bailey-LdP |
| PBO (CSCV, S=16) | **< 0.20** | Arian et al. 2024 |
| Sharpe anual neto OOS | **≥ 1.0** y **≥ 50% del IS** | degradación IS→OOS típica 50% |
| Ratio PnL neto / costes totales | **≥ 1.0** (los fees no pueden ser > 50% del bruto) | scalping muere aquí |
| Max drawdown OOS (al sizing objetivo) | **< 15%** | circuit breaker a 10% |
| Sensibilidad a costes | Sharpe sigue > 0.5 con **fees ×1.5 y slippage ×2** | robustez |
| Sensibilidad a parámetros | Meseta: vecinos ±20% mantienen ≥ 70% del Sharpe | anti-sobreajuste |
| Paper trading live | **≥ 6 semanas y ≥ 100 trades**, tracking error PnL paper vs backtest < 30% | valida ejecución/latencia |
| Live con capital mínimo | 4 semanas al 25% del sizing objetivo antes de escalar | valida infra |

---

## 5. Riesgo y ejecución para cuentas pequeñas

### 5.1 Sizing: Kelly fraccional + vol targeting
- **[EVIDENCIA]** Half-Kelly retiene ~75% de la tasa de crecimiento de full-Kelly con mucha menos varianza (propiedad matemática, no regla empírica); **full-Kelly tiene ~1/3 de probabilidad de un drawdown del 50%** en horizontes largos; la práctica profesional es **¼-½ Kelly** (Deriv Insights https://experts.deriv.com/insights/kelly-criterion-position-sizing ; Altrady https://www.altrady.com/blog/risk-management/kelly-criterion-crypto-position-sizing). Busseti, Ryu & Boyd, "Risk-Constrained Kelly Gambling" (arXiv 1603.06183, https://arxiv.org/pdf/1603.06183): Kelly con restricción explícita de probabilidad de drawdown como problema convexo.
- **[OPINIÓN]** Kelly presupone que conoces p y b; con una estrategia nueva, el error de estimación del edge (que es ~0) domina. Para BotStrike: **riesgo por trade fijo 0.5-1% del equity** ($5-10), vol-targeting a 15-25% anual de la cartera, y Kelly sólo como **techo** (nunca > ¼ Kelly estimado con el OOS). Con SL a 1% del precio, $10 de riesgo ⇒ notional $1.000 (1× apalancamiento efectivo); con SL a 0.3% ⇒ $3.300 (3.3×). El 5× máximo del bot casi nunca debería tocarse.

### 5.2 Liquidación y margen en Binance USDT-M
- **[EVIDENCIA]** Liquidación por **mark price** (índice de spot + funding), no por last price; se liquida cuando colateral (inicial + PnL realizado + no realizado) < margen de mantenimiento; Binance recomienda margin ratio < 80%. "Smart Liquidation": una orden IOC grande; si no basta → posición "bankrupt" → **Insurance Fund**; si el fondo no cubre → **ADL** contra posiciones contrarias rentables y apalancadas (ranking por PnL% × apalancamiento). Se cobra **Liquidation Clearance Fee** sobre el notional (Binance FAQ https://www.binance.com/en/support/faq/binance-futures-liquidation-protocols-360033525271). API: `GET /fapi/v1/symbolAdlRisk` (nov-2025) y `GET /fapi/v1/insuranceBalance` (abr-2025) permiten monitorizar ADL/fondo.
- **[EVIDENCIA, secundaria]** MMR del tier 1 de BTCUSDT: **0.40%** (Trade Reclaim https://trade-reclaim.com/en/blog/binance-leverage; la tabla oficial https://www.binance.com/en/futures/trading-rules/perpetual/leverage-margin no carga sin JS — **[NO VERIFICADO en fuente oficial hoy]**). Binance reajustó tiers de apalancamiento USDT-M el **2026-01-30** (anuncio referenciado por MEXC News; **[NO VERIFICADO detalle]**).
- **[Cálculo]** Aislado, 5×, BTC long: precio de liquidación ≈ entrada × (1 − 1/5 + MMR) ≈ **−19.6%**. Con un SL a −1%…−2% la liquidación sólo ocurre si el SL **no se ejecuta** (bot caído + sin stop en exchange, o gap extremo). Cross margin con 4 símbolos: una posición puede comerse el margen de las otras → **usar ISOLATED por símbolo** en una cuenta pequeña.

### 5.3 Stop-loss: en el exchange, siempre
- **[EVIDENCIA]** `STOP_MARKET` con `closePosition=true` y `workingType=MARK_PRICE` se ejecuta en el servidor aunque el bot esté muerto (docs New Algo Order https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order). Desde **2026-06-20** los `algoOrder` cuentan contra el **rate limit de órdenes**; desde **2025-12-15** los rechazos de trigger llegan por **`ALGO_UPDATE`** (no `CONDITIONAL_ORDER_TRIGGER_REJECT`); desde **2026-08-21** `ALGO_UPDATE.ia` indica activación de trailing stops (changelog §1.4). La UI migró TP/SL a "Conditional Orders" el 2026-03-25 (MEXC News, **[NO VERIFICADO oficial]**).
- **[EVIDENCIA]** Freqtrade (docs https://www.freqtrade.io/en/stable/stoploss/): `stoploss_on_exchange=true`, re-verifica cada `stoploss_on_exchange_interval` (60 s) y **re-coloca el stop si desaparece**; `emergency_exit=market` si falla la colocación; soporta Binance, Bybit, OKX, Hyperliquid… en futuros.
- **[OPINIÓN]** Patrón correcto: (1) abrir posición; (2) **inmediatamente** colocar `STOP_MARKET closePosition` en mark price; (3) reconciliar cada 30-60 s que existe (si no → recolocar; si falla 2 veces → cerrar a mercado); (4) el SL del bot es sólo una capa adicional más ajustada. Nunca al revés.

### 5.4 Desconexión de WebSocket y reconciliación
- **[EVIDENCIA]** User Data Stream: `listenKey` válido 60 min (renovar con PUT cada ≤30 min); la conexión se corta a las **24 h**; eventos ordenados por `T` (matching engine) y `E`; tras reconectar, **resincronizar posiciones/órdenes por REST** (docs https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams ; keepalive https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream). Market streams: 10 msgs/s entrantes; reconexiones repetidas → ban de IP (§1.4).
- **[EVIDENCIA]** Timestamp: error `-1021` si `timestamp` está fuera de `serverTime ± recvWindow` (default 5000 ms, máx 60000). Solución: NTP/chrony + offset dinámico contra `/fapi/v1/time` (python-binance FAQ https://python-binance.readthedocs.io/en/latest/faqs.html ; dev.binance.vision FAQ https://dev.binance.vision/t/api-frequently-asked-questions/37).
- **[OPINIÓN]** Estado de la verdad = **el exchange**, no la DB del bot. Máquina de estados: `DISCONNECTED → RECONNECTING → RESYNC (REST: positionRisk, openOrders, openAlgoOrders) → LIVE`. Durante `RESYNC` no se envían órdenes nuevas. Si la desconexión dura > N min, **flatten** (cerrar todo) o al menos verificar que cada posición tiene su stop en exchange. Como recordatorio: Hyperliquid tuvo un corte de API de 27 min el 2025-07-29 (The Block, https://www.theblock.co/post/364798/hyperliquid-outage-api-traffic-spike-not-hack-vulnerability-exploit) donde **nadie** pudo cerrar posiciones: el stop en exchange tampoco salva de eso.

### 5.5 Drawdown limits / circuit breakers (práctica)
- **[OPINIÓN, alineada con Freqtrade Protections]** Tres niveles: **diario** (−2% del equity → parar hasta 00:00 UTC), **semanal** (−5% → parar 7 días y revisar), **desde máximo** (−10% → modo paper obligatorio hasta re-aprobación con los umbrales de §4.4). `StoplossGuard`: ≥3 stops en 24 h en el mismo símbolo → cooldown 12-24 h. `CooldownPeriod`: no reentrar en un símbolo hasta pasar ≥ 2 barras tras salir.

### 5.6 API keys
- **[EVIDENCIA]** Binance: **IP whitelist obligatoria** para habilitar retiros (hard block); recomienda whitelist en toda key; keys sin whitelist e inactivas 30 días se borran; rotación sugerida cada 90 días (Binance blog https://www.binance.com/en/blog/security/how-to-use-an-api-key-securely-5-tips-from-binance-8638066848800196896). Phishing es el vector nº1.
- **[OPINIÓN]** Para el bot: key **sin permiso de retiro**, con "Enable Futures" únicamente, IP whitelist a la IP fija del LXC (Tailscale/egress), secretos en `EnvironmentFile=` con `chmod 600` o en `systemd-creds`, nunca en repo (el repo tiene `events.jsonl`/`desktop/data/` sin trackear — revisar que no contengan claves), y una key distinta para paper/testnet.

### 5.7 Particularidades de $1k
- **[NO VERIFICADO hoy]** Binance USDT-M exige notional mínimo **100 USDT** por orden en la mayoría de pares (BTCUSDT incluido) → con 1% de riesgo y SL de 0.5% el notional es $2.000: cabe, pero el **tamaño mínimo obliga a un riesgo por trade ≥ 0.05-0.1% del equity por cada 1% de SL**, y en ADA/SOL el step size redondea. Regla: si `qty_calculada < minQty` o `notional < 100` → **no operar**, no inflar el tamaño.

---

## 6. Arquitectura/infra 24/7 y frameworks de referencia

### 6.1 Latencia y ubicación
- **[EVIDENCIA]** El matching engine de Binance corre en **AWS Tokio (ap-northeast-1)**; desde Europa el RTT es **~270 ms** (>10× que ~20-25 ms desde Tokio) (Zenlayer https://cloud.zenlayer.com/blog/crypto-trading-latency-tokyo ; Arbitron https://arbitron.app/learn/crypto-exchange-server-locations); enlaces dedicados Tokio-Londres bajan de 132 ms RTT (AWS/Avelacom https://aws.amazon.com/blogs/industries/ultra-low-latency-cross-region-crypto-trading-with-avelacom-and-aws/).
- **[OPINIÓN]** A 1-5 min, 270 ms no es el problema; el problema es que **cualquier señal de microestructura** (OBI, VPIN a nivel de tick) llega 250 ms tarde respecto a los MMs que la explotan. Para un bot de horas/días, el LXC en Proxmox en España es perfectamente válido. Usar la **WebSocket API de trading** (`wss://ws-fapi.binance.com/ws-fapi/v1`, desde 2025-02-25) para enviar órdenes ahorra el handshake HTTP y decenas de ms por orden.

### 6.2 Proceso y supervisión (Linux/systemd) — lo que ya tiene el proyecto (unit + scripts para CT 104) y lo que falta
- **[EVIDENCIA]** systemd: `Type=notify` + `WatchdogSec=` + `Restart=on-failure`/`on-watchdog` + `RestartSec=`; el servicio manda `WATCHDOG=1` vía `sd_notify` (pip `sdnotify`) más a menudo que `WatchdogSec`; recomendación: WatchdogSec = 2-4× la latencia peor del loop (OneUptime 2026 https://oneuptime.com/blog/post/2026-03-02-how-to-configure-systemd-watchdog-for-service-health-checks-on-ubuntu/view ; ejemplo Python https://blog.stigok.com/2020/01/26/sd-notify-systemd-watchdog-python-3.html). Añadir `StartLimitIntervalSec/StartLimitBurst` para evitar crash-loops que quemen el rate limit de Binance.
- **[EVIDENCIA]** Reloj: chrony/NTP + comprobación de offset contra `serverTime` en arranque y cada N min; `-1021` = alarma (§5.4).
- **[EVIDENCIA]** Backups de estado: **Litestream** replica el WAL de SQLite a S3/MinIO con lag < 1 s y 1-3% CPU (https://litestream.io/how-it-works/ ; https://github.com/benbjohnson/litestream). No es durabilidad síncrona, pero para un bot que **reconcilia contra el exchange** es suficiente. Alternativa mínima: `sqlite3 .backup` por cron cada hora + `pct snapshot`/`vzdump` del LXC.
- **[EVIDENCIA]** Observabilidad: Prometheus + Grafana + Alertmanager con `telegram_configs` (Grafana docs https://grafana.com/docs/grafana-cloud/alerting-and-irm/alerting/configure-notifications/manage-contact-points/integrations/configure-telegram/ ; stack docker-compose de referencia https://github.com/maxim-avramenko/monitoring). Métricas mínimas del bot: heartbeat, WS connected, edad del último tick por símbolo, órdenes abiertas vs esperadas, **¿tiene cada posición su stop en exchange?**, equity/DD, rate-limit usage (`X-MBX-USED-WEIGHT-1M`, `X-MBX-ORDER-COUNT-*`), reloj offset. Alertas Telegram para: bot caído > 60 s, posición sin stop > 30 s, DD diario > 1.5%, -1021, -1003/-1015 (rate limit), ADL rating alto.
- **[OPINIÓN]** Docker no es necesario dentro de un LXC ya dedicado (un proceso por producto, systemd lo supervisa). Un **"kill switch" externo** (script que cancela todo y cierra posiciones usando otra key/máquina, p. ej. desde el PC por Tailscale) es obligatorio antes de ir live.

### 6.3 Frameworks open-source 2026: qué copiar
| Framework | Estado 2026 | Qué han hecho bien (copiable) | Fuente |
|---|---|---|---|
| **NautilusTrader** | Núcleo Rust + PyO3; "production-grade" | **Bus de mensajes en un solo hilo** ⇒ determinismo y **paridad backtest/live** con el mismo `NautilusKernel`; `RiskEngine` pre-trade (límites de notional/posición/rate); `Cache` con persistencia; **crash-only design** (reiniciar es el camino de recuperación normal); "data integrity over availability" | https://nautilustrader.io/docs/latest/concepts/architecture/ |
| **Freqtrade** | v2026.7, 53k★, FreqAI | `stoploss_on_exchange` con re-colocación; **Protections** (`StoplossGuard`, `MaxDrawdown` modo equity, `LowProfitPairs`, `CooldownPeriod`) backtesteables con `--enable-protections`; pairlist filters (`SpreadFilter` 0.5%, `VolatilityFilter`, `AgeFilter`); dry-run como ciudadano de primera | https://www.freqtrade.io/en/stable/plugins/ ; https://www.freqtrade.io/en/stable/stoploss/ |
| **Hummingbot** | v2.13 (mar-2026), 50+ conectores | Conectores CEX/DEX desacoplados; foco en MM; su implementación AS es la referencia si algún día se retoma | https://gainium.io/compare/freqtrade-vs-hummingbot |
| Backtesting (python.financial 2026) | — | "Escalar investigación en vectorizado, **validar ejecución en Nautilus**" | https://python.financial/ |

- **[OPINIÓN]** Para BotStrike, lo más rentable no es migrar a Nautilus, sino **adoptar tres ideas**: (1) un único camino de código para paper y live (mismo engine, sólo cambia el adapter), (2) `RiskEngine` como capa independiente que rechaza órdenes (notional, apalancamiento, símbolo sin stop, DD), (3) crash-only: el bot debe poder morir en cualquier línea y reiniciarse **leyendo el estado del exchange**, no de su DB.

---

## 7. Regulación 2026 (España/UE, MiCA, MiFID/ESMA, Binance por jurisdicción)

### 7.1 Binance ha salido de la UE (hecho más importante de todo el informe para BotStrike)
- **[EVIDENCIA]** El **24-jun-2026** Binance retiró su solicitud MiCA ante la HCMC griega (tras objeciones públicas de la presidenta del BCE) y comunicó a los usuarios de **Francia, Italia, España, Polonia** y otros Estados miembros que dejaba de prestar servicios desde el **1-jul-2026**: sin nuevas órdenes spot, sin depósitos, sin altas, sin Earn/staking; **retiros abiertos** (CoinDesk 2026-06-26, https://www.coindesk.com/policy/2026/06/26/binance-tells-eu-users-it-will-no-longer-provide-services-after-failing-to-secure-mica-license ; Zyphe 2026-07-01, https://www.zyphe.com/resources/news/binance-mica-licence-eu-lockout-july-2026 ; AML Intelligence jun-2026, https://www.amlintelligence.com/2026/06/news-binance-prepares-to-suspend-services-across-the-eu/).
- **[EVIDENCIA]** Cryptonomist 2026-07-06 (https://en.cryptonomist.ch/2026/07/06/binance-mica-license-failure/): "From July 1 onward, French users on Binance lost access to **spot trading, margin trading, and futures contracts**"; ~2M usuarios franceses; España entre los países suspendidos. Cuentas existentes: **sólo cerrar posiciones y retirar** (Dexly/ELLIPAL, https://dexly.trade/learn/is-binance-banned-in-europe ; https://www.ellipal.com/blogs/news/binance-eu-suspension-mica-self-custody).
- **[EVIDENCIA]** Binance dice que es temporal y que solicitará autorización en **Francia** "en los próximos meses"; sin fecha. Sólo ~210 de >3.000 firmas obtuvieron autorización MiCA (~7%); con licencia: Coinbase, Kraken, OKX, Crypto.com, Bybit, Bitvavo, Bitpanda, Bit2Me (CoinDesk; Finantres https://finantres.com/exchanges-licencia-mica/). En España, CNMV ha autorizado como CASP a BBVA, Cecabank, Openbank y Bit2Me (Bit2Me News 2026-06-18, https://news.bit2me.com/en/Binance-compliant-with-Mica-exit-EU-July-2026/). Los registros previos de Binance en el Banco de España **no equivalían** a licencia MiCA.
- **[NOTA de fiabilidad]** Un artículo de CriptoNoticias del 2026-06-10 aún afirmaba que Binance operaba por "reconocimiento mutuo" con licencia de grupo (https://www.criptonoticias.com/regulacion/cambia-1-julio-espana-criptomonedas-mica/); quedó desmentido dos semanas después. Ilustra la velocidad del cambio: **cualquier plan de venue debe revisarse mensualmente**.
- **[OPINIÓN]** Para un residente fiscal en España, **Binance USDT-M Futures no es un venue viable en agosto 2026**. Todo el trabajo de integración/changelog de Binance (§1.4) queda en *stand-by* hasta que exista licencia francesa y Binance reabra derivados a retail UE — lo cual, por el punto siguiente, puede no ocurrir en las condiciones actuales (5×).

### 7.2 Los perpetuos son CFDs para ESMA/CNMV → apalancamiento retail 2:1 en cripto
- **[EVIDENCIA]** ESMA, declaración pública **2026-02-24** (ESMA35-243228190-8024, https://www.esma.europa.eu/sites/default/files/2026-02/ESMA35-243228190-8024_-_Public_statement_on_derivatives_in_scope_of_the_CFD_product_intervention_measures.pdf): los "perpetual futures", "spot-quoted futures" y rolling spot ofrecidos a retail entran en las **medidas de intervención de CFDs** (MiFID II) independientemente del nombre comercial → **apalancamiento máximo 2:1 en criptoactivos**, cierre automático al 50% de margen, protección de saldo negativo, advertencia de riesgo, prohibición de incentivos (Finance Magnates https://www.financemagnates.com/forex/regulation/esma-tells-firms-perpetual-futures-fall-under-eu-cfd-rules/ ; PwC Legal https://legal.pwc.de/en/news/articles/esma-reminds-firms-of-cfd-product-intervention-obligations).
- **[EVIDENCIA]** La **CNMV** notificó formalmente a CySEC que los spot-quoted y perpetual futures ofrecidos a retail español se tratan como CFDs (Zitadelle AG, https://www.zitadelleag.com/news/spain-cnmv-spot-perpetual-futures-cfd-classification-belgium-ban-eu-cif-brokers).
- **[EVIDENCIA]** Los DEX de perps (Hyperliquid 50×, Aster 200×) siguen accesibles para europeos y **no están autorizados** ni bajo MiCA ni bajo MiFID (CoinDesk opinión 2026-07-01, https://www.coindesk.com/opinion/2026/07/01/europe-is-closing-the-door-on-offshore-crypto-but-it-s-leaving-the-riskiest-window-open). MiCA/MiFID regulan al **proveedor**, no penalizan al usuario; pero el usuario pierde toda protección (sin fondo de garantía, sin arbitraje, riesgo de smart-contract).
- **[OPINIÓN]** Mapa de opciones realistas para BotStrike hoy: (a) **Hyperliquid** (ya en integración): el único venue donde el diseño actual (perps, hasta 5×) es ejecutable; (b) **CEX con MiCA** (Kraken/OKX/Bybit) para perps a retail UE: previsiblemente **2:1** y KYC completo; comprobar por exchange si ofrecen perps a residentes españoles **[NO VERIFICADO por exchange]**; (c) esperar a Binance-Francia (sin fecha; y llegaría con el mismo 2:1). El límite ESMA 2:1 coincide, casualmente, con el apalancamiento efectivo que la gestión de riesgo de §5.1 recomienda de todos modos.

### 7.3 Fiscalidad (España, ejercicio 2026)
- **[EVIDENCIA]** Ganancias de cripto y de derivados (futuros/CFDs) tributan como **ganancia patrimonial en la base del ahorro**: 19% (≤6k€), 21% (6-50k), 23% (50-200k), 27% (200-300k), 30% (>300k); las de futuros se declaran igual que acciones/ETFs y **se compensan** entre sí (Rankia 2026 https://www.rankia.com/blog/irpf-declaracion-renta/3761495-fiscalidad-criptomonedas-tributacion-bitcoin ; Blockpit https://www.blockpit.io/tax-guides/impuestos-criptomonedas-espana). **Modelo 721**: saldos en plataformas extranjeras > 50.000 € a 31-dic (no aplica a $1k). **DAC8** en vigor 2026: los exchanges con clientes UE reportan automáticamente saldos y operaciones a Hacienda.
- **[OPINIÓN]** Con cientos de operaciones al año, la carga es el **ledger**: exportar cada fill, funding y fee con timestamp y FIFO. Dado que `userTrades` de Binance ya sólo devuelve 3 meses (§1.4) y Hyperliquid limita `userFills` por peso, el bot debe ser la **fuente de verdad fiscal** (DB append-only + backup, §6.2). Hyperliquid sin KYC no exime de declarar.

---

## 8. Síntesis final

### 8.1 Diagnóstico: dónde está BotStrike frente al SOTA (según la descripción del proyecto, no auditado)
| Dimensión | BotStrike (descrito) | SOTA 2026 (evidencia §1-7) | Veredicto |
|---|---|---|---|
| **Venue** | Binance USDT-M como principal; Hyperliquid "en curso" | Binance cerrado a residentes UE desde 2026-07-01; perps = CFD 2:1 en venues regulados; Hyperliquid accesible (maker 1.5 / taker 4.5 bps) | **Bloqueo crítico**: el venue principal no existe para el dueño |
| **Estrategias activas** | Mean reversion 1-5 min (Z/RSI/BB/OBI) + Fibonacci | MR a 1-5 min en Binance: Sharpe neto −18/−52; señal 0.5 bps vs fee 5 bps; Fibonacci sin evidencia | **Contra la evidencia** |
| **Estrategias archivadas** | Trend following, MM Avellaneda-Stoikov, order-flow momentum | Trend/TS-momentum a 1-4 semanas: única familia con réplica neta de costes (SR>1.5, alfa 10.8%); MM retail: no; order-flow a minutos: no | **Archivaron lo que funciona y activaron lo que no** |
| **Microestructura** | VPIN, Hawkes, Kyle λ | Kyle λ/Amihud no significativos a 1 min; VPIN significativo como descriptor, no rentable neto a 5 min | Degradar a filtros de régimen/ejecución |
| **Riesgo** | Kelly, vol targeting, circuit breaker DD | ¼-½ Kelly, 0.5-1%/trade, DD escalonado, stop **en exchange** + reconciliación, kill switch | Bien encaminado; verificar que el stop vive en el exchange |
| **Backtester** | Fees/slippage/funding | + purged/embargoed CPCV, DSR, PBO, registro de trials, stress de costes, latencia 300 ms, no-fill de límites | Falta la capa estadística |
| **Infra** | Python, systemd en LXC Proxmox (CT 104), paper/live, desktop | Watchdog sd_notify, NTP, Litestream/backup, Prometheus+Telegram, crash-only, un solo camino paper/live (Nautilus) | Base sólida; faltan watchdog/observabilidad/backup verificados |
| **Capital** | $1.000, 5× | Fee anual a 2 trades/día × $2k × 7 bps ≈ **$1.020/año = 100% del capital** | La rotación decide el resultado, no la señal |

### 8.2 Diez recomendaciones priorizadas
**P0 — antes de cualquier euro real**
1. **Resolver el venue legalmente viable.** Congelar el objetivo "live en Binance" (cerrado a España desde 2026-07-01, §7.1) y terminar el adapter de **Hyperliquid** como venue primario; en paralelo, verificar por escrito si Kraken/OKX/Bybit ofrecen perps a retail español y a qué apalancamiento (previsible 2:1, §7.2). *Cuantitativo*: probabilidad de que Binance reabra derivados a retail UE a 5× en 12 meses ≈ baja (opinión); el coste de desarrollar contra un venue inaccesible es 100% desperdicio.
2. **Presupuesto de fees como límite duro en el RiskEngine.** Regla: gasto anual en fees+slippage ≤ **30% del capital** ($300). A 7 bps por round-trip y $2.000 de notional medio ⇒ **≤ ~215 round-trips/año ≈ 4/semana en total** para los 4 símbolos. Cualquier estrategia que necesite más rotación queda automáticamente descartada. (Con la rotación actual de una MR a 1-5 min — decenas de trades/día — el fee anual supera **5×** el capital: $14/día ≈ $5.100/año.)
3. **Stop en el exchange + reconciliación + kill switch** (§5.3-5.4): `STOP_MARKET closePosition` (o equivalente TP/SL en Hyperliquid) colocado en el mismo ciclo que la entrada; loop cada 30-60 s que verifica "posición ⇒ stop existe"; máquina de estados `RESYNC` tras reconexión; script externo de *flatten* desde otra máquina. *Cuantitativo*: a 5× la liquidación está a −19.6%; un gap del 5% sin stop = −25% del equity en una posición.
4. **Puerta de validación estadística obligatoria** (§4.4) aplicada **retroactivamente** a MR y Fibonacci: registro de todos los trials, CPCV (S=16) + walk-forward purgado/embargado, **DSR > 0.95, PBO < 0.20, ≥ 300 trades OOS**, Sharpe neto OOS ≥ 1.0 y ≥ 50% del IS, stress fees ×1.5 / slippage ×2, 6 semanas de paper con ≥ 100 trades. Si MR/Fib no pasan (predicción: no pasan), se archivan.

**P1 — el rediseño con edge**
5. **Desarchivar trend following y reconstruirlo como TS-momentum de baja rotación** (señal a 1-4 semanas: medias multi-horizonte/breakouts, BTC/ETH/SOL(+ADA), 2-8 trades/mes/activo, entradas maker, vol-targeting 20%, filtro de funding extremo). *Cuantitativo*: única familia con SR > 1.5 neto y alfa ~10.8% replicados (Zarattini et al. 2025; Fieberg et al. JFQA); rotación compatible con la regla 2 (≈100-200 round-trips/año).
6. **Mover mean reversion a horizonte ≥ 4 h "post-shock"** (retorno > 2.5σ en 1-4 h, funding/vol como filtros, salida por tiempo) **o retirarla**; **retirar Fibonacci** como estrategia (sin evidencia, §2.7). *Cuantitativo*: a 4 h en BTC σ ≈ 70 bps ⇒ ratio movimiento/coste ≈ 10×, frente a ≈ 2× a 5 min.
7. **Degradar la microestructura a filtros y timing**: VPIN alto ⇒ reducir tamaño/ampliar stop; evitar cruzar el spread en los bursts de :00/:15/:30/:45 (Quarter-Hour Effect, §2.1); **retirar Kyle λ y Amihud como señales** (no significativos a 1 min, Frontiers 2026). *Cuantitativo*: la señal microestructural vale ~0.5 bps; usada como timing de ejecución ahorra parte del spread en vez de intentar "ganar" 0.5 bps pagando 7.
8. **Sizing y drawdown**: riesgo 0.5-1% del equity por trade, tope **¼ Kelly** estimado OOS, **margen aislado por símbolo**, apalancamiento efectivo ≤ 2× (coincide con ESMA), escalera de DD **−2% día / −5% semana / −10% desde máximo ⇒ paper obligatorio**; `StoplossGuard` (3 stops/24 h/símbolo ⇒ cooldown). *Cuantitativo*: full-Kelly ⇒ ~33% de probabilidad de DD 50%; ½-Kelly conserva 75% del crecimiento.

**P2 — infraestructura y mantenimiento**
9. **Checklist de API 2025-2026** (§1.4-1.5): migrar `CONDITIONAL_ORDER_TRIGGER_REJECT` → `ALGO_UPDATE`; confirmar hosts WS no legacy (decomiso 2026-04-23); ledger local de fills (Binance `userTrades` = 3 meses); contabilizar `algoOrder` en el rate limit de órdenes; usar WS trading API; en Hyperliquid, diseño **push por WS y órdenes en batch** por el límite "1 request por USDC operado" (buffer 10.000).
10. **Observabilidad y resiliencia verificadas** (§6.2): `Type=notify` + `WatchdogSec` + `sdnotify`; chrony y alarma en `-1021`; Litestream o `.backup` horario + `vzdump` del CT 104; Prometheus/Grafana + Alertmanager→Telegram con alertas de *posición sin stop*, *tick stale*, *DD*, *rate limit*; adoptar de NautilusTrader el **camino único paper/live** y el **crash-only** (reiniciar leyendo el estado del exchange). *Cuantitativo*: el corte de 27 min de Hyperliquid (2025-07-29) y el ban por reconexiones de Binance son fallos reales; el coste de un watchdog es ~20 líneas.

### 8.3 La verdad incómoda
- **[Cálculo]** Con $1.000, incluso ejecutando *perfectamente* la mejor estrategia con evidencia (trend, Sharpe neto ~1-1.5 a 20% de vol objetivo), la **esperanza es ≈ +$150-250/año con 1σ ≈ ±$200** y drawdowns de −20-30% en años como 2022. Es menos que el coste de la electricidad del servidor y del tiempo invertido. Ninguna estrategia a 1-5 min en BTC/ETH con 5 bps taker tiene esperanza positiva para retail: necesita 73-83% de aciertos sólo para empatar (§2.0) y la literatura más reciente sobre **exactamente este exchange y estos símbolos** mide Sharpe neto negativo de dos dígitos (§2.1).
- **[OPINIÓN]** El riesgo que más daño ha hecho al proyecto en 2026 no fue de mercado sino **regulatorio**: el venue principal desapareció de un día para otro. Un bot con cuenta pequeña debe ser **multi-venue por diseño** o no ser.
- **[OPINIÓN]** El valor real de BotStrike a $1k no es el PnL: es (a) un **track record auditado** (paper + live pequeño con DSR/PBO documentados) y (b) una **infraestructura que sobrevive a fallos**. Eso sólo se convierte en dinero si el capital escala a $20-50k (donde +15%/año ya son $3-7k) o si el conocimiento se aplica a otra cosa. Con las expectativas ajustadas a "no perder dinero contra los fees y aprender a validar", el proyecto tiene sentido; con la expectativa de "vivir de esto con $1k", no lo tiene, y ninguna cantidad de IA cambia esa aritmética.

---

## 9. Bibliografía (URLs citadas, agrupadas; fecha de consulta 2026-08-29)

**Mercado, fees, API**
- Binance changelog USDS-M: https://developers.binance.com/docs/derivatives/change-log
- Binance WS market streams: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams
- Binance User Data Streams / keepalive: https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams ; https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream
- Binance New Algo Order: https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order
- Binance liquidation protocols: https://www.binance.com/en/support/faq/binance-futures-liquidation-protocols-360033525271
- Binance API key security: https://www.binance.com/en/blog/security/how-to-use-an-api-key-securely-5-tips-from-binance-8638066848800196896
- Fees (secundarias): https://www.bitdegree.org/crypto/tutorials/binance-fees ; https://tradersunion.com/brokers/crypto/view/binance/futures-fees/ ; https://dappgrid.com/binance-futures-fees-explained/ ; https://trade-reclaim.com/en/blog/binance-leverage
- Hyperliquid rate limits: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits
- Hyperliquid fees: https://eco.com/support/en/articles/15191998-hyperliquid-fees-explained-maker-taker-funding-and-withdrawal-in-2026 ; https://hyperliquidguide.com/guides/fees/fees-explained
- Hyperliquid outage 2025-07-29: https://www.theblock.co/post/364798/hyperliquid-outage-api-traffic-spike-not-hack-vulnerability-exploit
- Volúmenes: https://www.datawallet.com/crypto/crypto-perpetual-futures-statistics ; https://cryptobriefing.com/hyperliquid-decentralized-perpetual-trading-volume/ ; https://yellow.com/research/hyperliquid-perp-volume-dominance-how-2026 ; https://en.cryptonomist.ch/2026/08/19/binance-tradfi-perpetuals/
- Latencia: https://cloud.zenlayer.com/blog/crypto-trading-latency-tokyo ; https://arbitron.app/learn/crypto-exchange-server-locations ; https://aws.amazon.com/blogs/industries/ultra-low-latency-cross-region-crypto-trading-with-avelacom-and-aws/
- -1021/recvWindow: https://python-binance.readthedocs.io/en/latest/faqs.html ; https://dev.binance.vision/t/api-frequently-asked-questions/37

**Estrategias (papers)**
- Quarter-Hour Effect (2026): https://arxiv.org/html/2607.09426v2
- Microstructure alpha (Frontiers 2026): https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full
- Easley/LdP/O'Hara crypto microstructure: https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf
- Bitcoin wild moves / VPIN (2025): https://www.sciencedirect.com/science/article/pii/S0275531925004192
- Funding two-tiered (MDPI 2026): https://www.mdpi.com/2227-7390/14/2/346 ; industria: https://arbitragescanner.io/blog/crypto-funding-rate-arbitrage-guide ; https://medium.com/@arbitrageghost/funding-rate-arbitrage-in-2026-the-complete-guide-with-real-calculations-40e6cf341e52
- Catching Crypto Trends (SSRN 5209907): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907
- Trend factor JFQA: https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178
- Momentum illusion (SSRN 4633099): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4633099 ; realistic assumptions (SSRN 4675565): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565
- Trend following adaptativo (arXiv 2602.11708): https://arxiv.org/pdf/2602.11708
- Pairs copula (Financial Innovation): https://link.springer.com/article/10.1186/s40854-024-00702-7 ; survey WNE 19/2025: https://www.wne.uw.edu.pl/download_file/6095/0 ; DRL stat-arb: https://www.sciencedirect.com/science/article/abs/pii/S1568494624000292
- Trading Games (Palazzi 2025): https://onlinelibrary.wiley.com/doi/full/10.1002/fut.70018
- Extremity Premium / adverse selection (arXiv 2602.07018): https://arxiv.org/html/2602.07018v2 ; Crypto Chassis defensive MM: https://medium.com/open-crypto-market-data-initiative/defensive-market-making-against-market-manipulators-3ceabb5d1b71
- Grid (industria): https://intralogic.eu/backtest/en/blog/grid-bot-crypto-trading-advantages-risks-analysis ; https://www.tv-hub.org/guide/is-automated-trading-profitable

**ML/IA**
- Alpha Illusion (arXiv 2605.16895): https://arxiv.org/html/2605.16895v1
- Reliable evaluation of LLM financial MAS (arXiv 2603.27539): https://arxiv.org/html/2603.27539v1
- Execution assumptions & reproducibility (arXiv 2606.08285): https://arxiv.org/html/2606.08285 ; Agentic Trading (arXiv 2605.19337): https://arxiv.org/html/2605.19337v1
- Risk-aware DRL under costs (SSRN 5662930): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5662930 ; DQN BTC ×120 (Cogent 2025): https://www.tandfonline.com/doi/full/10.1080/23322039.2025.2594873 ; RL pair trading: https://arxiv.org/pdf/2407.16103 ; reward functions (MDPI 2026): https://www.mdpi.com/2227-7390/14/5/794
- Transformers: https://arxiv.org/pdf/2510.09776 ; https://dl.acm.org/doi/10.5555/3780338.3780628
- LOB: https://ideas.repec.org/p/ehl/lserod/128950.html ; https://arxiv.org/pdf/2506.05764 ; https://arxiv.org/pdf/2010.01241 ; https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1616485/full

**Validación**
- Deflated Sharpe: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 ; https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
- PBO/CSCV: https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf
- Arian/Norouzi/Seco 2024: https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID4686376_code4361537.pdf?abstractid=4686376&mirid=1
- Purged CV / CPCV lib: https://en.wikipedia.org/wiki/Purged_cross-validation ; https://github.com/eslazarev/purged-cross-validation ; https://github.com/tschm/jsharpe ; https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/
- WF microestructura (arXiv 2512.12924): https://arxiv.org/pdf/2512.12924 ; Implementation risk (arXiv 2603.20319): https://arxiv.org/pdf/2603.20319
- Trades mínimos (industria): https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05

**Riesgo / infra / frameworks**
- Kelly: https://arxiv.org/pdf/1603.06183 ; https://experts.deriv.com/insights/kelly-criterion-position-sizing ; https://www.altrady.com/blog/risk-management/kelly-criterion-crypto-position-sizing
- NautilusTrader arquitectura: https://nautilustrader.io/docs/latest/concepts/architecture/
- Freqtrade: https://www.freqtrade.io/en/stable/plugins/ ; https://www.freqtrade.io/en/stable/stoploss/ ; comparativa: https://gainium.io/compare/freqtrade-vs-hummingbot ; https://python.financial/
- systemd watchdog: https://oneuptime.com/blog/post/2026-03-02-how-to-configure-systemd-watchdog-for-service-health-checks-on-ubuntu/view ; https://blog.stigok.com/2020/01/26/sd-notify-systemd-watchdog-python-3.html
- Litestream: https://litestream.io/how-it-works/ ; https://github.com/benbjohnson/litestream
- Alertas Telegram: https://grafana.com/docs/grafana-cloud/alerting-and-irm/alerting/configure-notifications/manage-contact-points/integrations/configure-telegram/ ; https://github.com/maxim-avramenko/monitoring

**Regulación / fiscalidad**
- CoinDesk 2026-06-26: https://www.coindesk.com/policy/2026/06/26/binance-tells-eu-users-it-will-no-longer-provide-services-after-failing-to-secure-mica-license
- Zyphe 2026-07-01: https://www.zyphe.com/resources/news/binance-mica-licence-eu-lockout-july-2026 ; Cryptonomist 2026-07-06: https://en.cryptonomist.ch/2026/07/06/binance-mica-license-failure/ ; AML Intelligence: https://www.amlintelligence.com/2026/06/news-binance-prepares-to-suspend-services-across-the-eu/ ; Dexly: https://dexly.trade/learn/is-binance-banned-in-europe ; ELLIPAL: https://www.ellipal.com/blogs/news/binance-eu-suspension-mica-self-custody
- Bit2Me News 2026-06-18: https://news.bit2me.com/en/Binance-compliant-with-Mica-exit-EU-July-2026/ ; Finantres: https://finantres.com/exchanges-licencia-mica/ ; CriptoNoticias 2026-06-10 (desactualizado): https://www.criptonoticias.com/regulacion/cambia-1-julio-espana-criptomonedas-mica/ ; CNMV MiCA: https://www.cnmv.es/portal/mica/regulacion-criptoactivos?lang=en
- ESMA 2026-02-24: https://www.esma.europa.eu/sites/default/files/2026-02/ESMA35-243228190-8024_-_Public_statement_on_derivatives_in_scope_of_the_CFD_product_intervention_measures.pdf ; Finance Magnates: https://www.financemagnates.com/forex/regulation/esma-tells-firms-perpetual-futures-fall-under-eu-cfd-rules/ ; PwC Legal: https://legal.pwc.de/en/news/articles/esma-reminds-firms-of-cfd-product-intervention-obligations ; CNMV→CySEC: https://www.zitadelleag.com/news/spain-cnmv-spot-perpetual-futures-cfd-classification-belgium-ban-eu-cif-brokers ; CoinDesk opinión 2026-07-01: https://www.coindesk.com/opinion/2026/07/01/europe-is-closing-the-door-on-offshore-crypto-but-it-s-leaving-the-riskiest-window-open
- Fiscalidad: https://www.rankia.com/blog/irpf-declaracion-renta/3761495-fiscalidad-criptomonedas-tributacion-bitcoin ; https://www.blockpit.io/tax-guides/impuestos-criptomonedas-espana
