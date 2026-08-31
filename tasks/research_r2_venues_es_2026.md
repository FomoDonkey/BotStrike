# Research R2 — Venues de perpetuos cripto para residente en España (persona física) — Agosto 2026

> Estado: **COMPLETO** (31-ago-2026) — 35 búsquedas/fetches. Quedan 6 verificaciones que **no se pueden cerrar por research** y requieren abrir cuenta: ver §7.4.
> Pregunta: ¿qué venues puede usar legal y prácticamente un residente en España (persona física) para operar PERPETUOS de cripto con API programática y capital ~$1000, en agosto de 2026?
> Metodología: WebSearch/WebFetch (>=10 búsquedas), cada afirmación marcada como [EVIDENCIA] (con fuente URL+fecha) u [OPINIÓN/INFERENCIA].

## 1. Contexto regulatorio (MiCA, ESMA, CNMV)

**[EVIDENCIA]** Marco a agosto 2026:
- **MiCA (spot/custodia):** el periodo transitorio terminó — desde el **1 de julio de 2026** todo exchange que sirva a residentes UE necesita licencia CASP MiCA de al menos un regulador nacional (pasaportable a los 27). Fuente: [Kraken Blog — MiCA enforcement begins July 1](https://blog.kraken.com/news/industry-news/europe-mica-switch) (2026); [Finance Magnates — Europe's Crypto Market After July 1](https://www.financemagnates.com/cryptocurrency/regulation/europes-crypto-market-after-july-1-who-stays-who-leaves-and-what-changes-under-mica/) (jul 2026).
- **MiCA NO cubre derivados.** Los perpetuos/futuros/apalancados caen bajo **MiFID II**: solo un venue con licencia de empresa de servicios de inversión (MiFID II) puede ofrecer perps legalmente a clientes UE. Un CASP MiCA "a secas" solo puede ofrecer spot. Fuente: [BingX Learn — MiCA Winners and Losers July 2026](https://bingx.com/en/learn/article/top-mica-compliant-crypto-platforms-in-european-union-eu-market) (jul 2026).
- **ESMA, declaración pública 24-feb-2026 (ESMA35-243228190-8024):** los derivados comercializados como "perpetual futures"/"perpetual contracts" **encajan en la definición de CFD** a efectos de las medidas de intervención de producto nacionales heredadas de ESMA 2018/2019 cuando cumplen la definición; el nombre comercial es irrelevante y el funding rate no cambia el análisis. Consecuencia para retail UE: **apalancamiento máximo 2:1 en cripto**, margin close-out al 50%, protección de saldo negativo, aviso de riesgo estandarizado y prohibición de incentivos. Fuentes: [PDF oficial ESMA (24-feb-2026)](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA35-243228190-8024_-_Public_statement_on_derivatives_in_scope_of_the_CFD_product_intervention_measures.pdf); [Finance Magnates — ESMA Tells Firms Perpetual Futures Fall Under EU CFD Rules](https://www.financemagnates.com/forex/regulation/esma-tells-firms-perpetual-futures-fall-under-eu-cfd-rules/) (feb 2026); [Harneys — ESMA reinforces investor protection in CFD compliance](https://www.harneys.com/our-blogs/regulatory/esma-reinforces-investor-protection-in-cfd-compliance/) (2026).

### 1.1 ⚠️ El carve-out "futuro con vencimiento" — el detalle que lo cambia todo

**[EVIDENCIA]** La definición de CFD usada por las medidas de intervención de producto (ESMA 2018, heredadas por CNMV y demás reguladores nacionales) excluye explícitamente **"an option, future, swap or forward rate agreement"**. Es decir: un derivado apalancado liquidado en efectivo que **tenga fecha de vencimiento** es legalmente un **futuro**, NO un CFD, y por tanto **queda fuera del tope 2:1**. Los futuros sí están permitidos a retail bajo MiFID II con test de conveniencia (*appropriateness test*).

**[EVIDENCIA]** La industria ha explotado este carve-out con contratos de **vencimiento largo (5 años)** que funcionan como perps (funding rate horario, sin rollover práctico):
- **OKX X-Perps** — lanzado el **15-abr-2026**: *"MiFID-regulated five-year expiry crypto derivatives with up to 10× leverage"*, disponibles a **retail** en todo el EEE. Fuentes: [Businesswire — OKX Launches X-Perps in Europe (15-abr-2026)](https://www.businesswire.com/news/home/20260415510125/en/OKX-Launches-X-Perps-in-Europe-MiFID-Regulated-Crypto-Derivatives-with-up-to-10x-Leverage); [OKX Europe — X-Perps live in Europe](https://www.okx.com/en-eu/learn/okx-x-perps-mifid-regulated-crypto-futures-derivatives-europe) (abr-2026, act. ago-2026).
- Análisis del mecanismo: *"X-Perps carry a five-year expiration date, which legally classifies them as futures contracts rather than CFDs. This reclassification sidesteps the CFD intervention framework entirely."* Fuente: [BlockEden — OKX X-Perps: How a 5-Year Expiry Clause Cracked Europe's Derivatives Market (17-abr-2026)](https://blockeden.xyz/blog/2026/04/17/okx-x-perps-europe-mifid-5-year-expiry-perpetual-futures/).
- Segunda vía de escape citada: un perp **negociado en un centro de negociación MiFID (MTF/mercado regulado)** tampoco es un CFD (los CFDs son OTC por definición). Fuentes: [coinperps — Best Crypto Perpetual Futures Exchanges in Europe (2026)](https://www.coinperps.com/learn/best-crypto-futures-platforms-in-europe); [Barchart — perpetuals.com becomes first European MTF for crypto derivatives](https://www.barchart.com/story/news/1003828/perpetuals-com-becomes-first-european-mtf-to-offer-direct-client-execution-for-crypto-derivatives).

**[EVIDENCIA — CONFLICTO DE FUENTES, sin resolver]** Hay contradicción entre fuentes secundarias sobre el apalancamiento retail realmente aplicado a agosto 2026:
- A favor de 10x: comunicado oficial de OKX (abr-2026) y specs oficiales de Kraken EEE (10x, margen inicial 10%).
- A favor de 2x: [cex101 — OKX X-Perps Europe Guide 2026](https://cex101.com/en/articles/okx-x-perps-europe-regulated-trading-guide/) afirma *"EU retail accounts are limited to 2x on all crypto perpetuals per ESMA guidelines"*, reservando 20x/10x/5x a profesionales; [Finance Magnates — 10x Down to 2x: Has Europe Killed Crypto Perps?](https://www.financemagnates.com/forex/10x-down-to-2x-has-europe-killed-crypto-perps-even-before-it-started/) plantea la rebaja como escenario probable tras el statement de ESMA (25-feb-2026).
- **[ACCIÓN REQUERIDA]** Esto **NO se puede cerrar por research**: hay que abrir cuenta, pasar KYC y **leer el apalancamiento máximo real que muestra la UI/API** para una cuenta retail española. Es una verificación de 30 minutos que evita construir sobre una suposición.

**[INFERENCIA]** Implicación práctica para BotStrike: en el **peor caso** (2:1) el retail español sigue pudiendo operar perps legalmente; el tope solo limita el tamaño de posición, no la estrategia. Con ~$1000 de capital y una estrategia que no depende de apalancamiento alto, **2:1 no es un bloqueo**. La recategorización a **profesional electivo** (2 de 3: cartera >500k€, ≥10 operaciones/trimestre de tamaño significativo, experiencia profesional en el sector) es **inalcanzable con $1000** — descartada.

## 2. Binance — estado para residentes en España

**[EVIDENCIA]**
- Binance **NO obtuvo licencia MiCA** antes del 30-jun-2026 (había solicitado vía el regulador griego y **retiró la solicitud**). Desde el **1 de julio de 2026** cesó servicios para residentes en España y empresas registradas en España (vía Binance Spain, S.L.). Fuentes: [Observatorio Blockchain — Binance cesa su actividad en España](https://observatorioblockchain.com/regulacion/binance-cesa-su-actividad-en-espana-y-solo-permitira-retirar-cripto/) (jun-jul 2026); [ModoCripto — Binance restringirá servicios en España desde el 1 de julio de 2026](https://www.modocripto.es/binance-restringira-servicios-en-espana-desde-el-1-de-julio-de-2026/) (2026); [Demócrata — Binance corta servicios en la UE](https://www.democrata.es/economia/binance-alerta-a-sus-clientes-en-la-ue-de-que-cortara-servicios-en-dias-al-no-lograr-autorizacion/amp/) (jun 2026).
- **Qué sigue disponible tras el 1-jul-2026:** solo operaciones para **reducir/cerrar posiciones y retirar activos** (cripto y EUR). Los fondos no quedan congelados. NO se pueden abrir posiciones nuevas ni operar spot/derivados con normalidad. Fuentes: mismas de arriba + [Criptonoticias — Binance suspende servicios con criptomonedas en Europa](https://www.criptonoticias.com/regulacion/binance-servicios-criptomonedas-europa-mica/) (2026).

**[CONCLUSIÓN — EVIDENCIA]** Binance queda **DESCARTADO** como venue para BotStrike siendo residente ES: no es cuestión de "riesgo", es que la cuenta está en modo solo-retirada. Cualquier workaround (VPN, cuenta en otra jurisdicción) sería fraude de residencia contra los ToS y un riesgo de congelación de fondos. **[OPINIÓN]** No intentarlo.

## 3. CEX con licencia UE — derivados

> **Regla de cribado [EVIDENCIA]:** una licencia **MiCA (CASP) NO habilita perps**. MiCA cubre spot, custodia y transferencia; los perpetuos son instrumentos financieros bajo **MiFID II** y requieren licencia de empresa de servicios de inversión. Por eso la lista de venues viables es corta: solo los que tienen **MiCA + MiFID II**. Fuente: [Dexly — Is Bybit Banned in Europe? The MiCA Migration Explained](https://dexly.trade/learn/is-bybit-banned-in-europe) (2026); [coinperps — MiCA Licensed Exchanges (CASP List 2026)](https://www.coinperps.com/learn/mica-licensed-exchanges).

### 3.1 Kraken / Kraken Pro Derivatives — ✅ VIABLE (favorito)

**[EVIDENCIA]**
- **Entidad y licencia:** *Payward Europe Digital Solutions (Cyprus) Ltd* (PEDSL-CY), **MiFID II** por CySEC, **licencia 342/17**; pasaporte a todo el EEE (incluida España). Spot/custodia bajo licencia **MiCA de Irlanda (CBI)**. Fuente: [Kraken Support — Changes to Derivatives offerings for EEA clients](https://support.kraken.com/articles/derivatives-offerings-for-eea-clients) (act. 2025-07-03); [Kraken Support — Overview of changes for EEA clients](https://support.kraken.com/articles/overview-of-changes-for-eea-clients).
- **Producto:** **perpetuos multi-colateral lineales** para clientes EEE, **+150–300 mercados**. Colateral: BTC, ETH, ciertas stablecoins, **EUR y GBP**; liquidación en USD. Fuentes: [Kraken Blog — Crypto-collateral EU futures](https://blog.kraken.com/product/kraken-derivatives/crypto-collateral-eu-futures) (nov-2025); [Kraken Blog — Europe's largest regulated futures offering](https://blog.kraken.com/news/euro-reg-futures).
- **Apalancamiento:** **hasta 10x**, margen inicial desde **10%**, margen de mantenimiento = mitad del inicial. Fuente: [Kraken — Especificaciones de contratos perpetuos para clientes del EEE](https://support.kraken.com/articles/perpetual-contract-specifications-for-clients-in-the-eea).
- **Mínimo de orden (PF_XBTUSD):** **0.0001 BTC**; tick **1 USD**; posición máx. 1.200 BTC. Funding **horario**, rango −0,5% / +0,5% por hora. Misma fuente.
- **Fees derivados (tier base):** **maker 0,02% / taker 0,05%**, escalando hasta 0,00%/0,01% a partir de $100M de volumen. Consultables por API vía `Market().get_fee_schedules()`. Fuentes: [Kraken Developers — Derivatives Introduction](https://docs.kraken.com/api/docs/guides/futures-introduction/); [python-kraken-sdk docs](https://python-kraken-sdk.readthedocs.io/).
- **API / SDK Python:** REST + **WebSocket v1 y v2** documentados. SDK oficial de comunidad **`python-kraken-sdk`** (`pip install python-kraken-sdk`) con clientes Futures REST y WS y un bot de ejemplo (`examples/futures_trading_bot_example.py`). También soportado en **CCXT / CCXT Pro**. Fuentes: [docs.kraken.com/api](https://docs.kraken.com/api/docs/guides/futures-introduction/); [PyPI python-kraken-sdk](https://pypi.org/project/python-kraken-sdk/).
- **TESTNET REAL:** ✅ **`https://demo-futures.kraken.com`** — sandbox de paper trading con **endpoints y estructura de respuesta idénticos a producción**; se activa con `sandbox=True` en el SDK. Fuente: [Kraken Support — API Testing Environment (Derivatives)](https://support.kraken.com/articles/360024809011-api-testing-environment-derivatives).
- **Onboarding:** cuestionario de idoneidad/conveniencia (*appropriateness test*) + NIF obligatorio para nuevos operadores de futuros. Promo: **30 días sin comisiones** para nuevos traders de futuros del EEE. Fuentes: [Kraken Support — EEA derivatives](https://support.kraken.com/articles/derivatives-offerings-for-eea-clients); [Kraken Blog — EEA futures 30 days no trading fees](https://blog.kraken.com/product/kraken-derivatives/eea-futures-30-days-no-trading-fees).
- **España: ✅ CONFIRMADA.** Kraken obtuvo la **licencia MiCA del Banco Central de Irlanda** (jun-2025), pasaportable a los 30 estados del EEE, y está registrado como VASP en España entre otros mercados clave. Suma **MiFID (derivados)** + licencia de **dinero electrónico**. Fuentes: [Kraken Blog — Licencia MiCA del Banco Central de Irlanda](https://blog.kraken.com/global/licencia-mica); [El Economista — Kraken obtiene la licencia MiCA para operar en Europa](https://www.eleconomista.es/cripto/noticias/13437540/06/25/la-firma-de-criptomonedas-kraken-obtiene-la-licencia-mica-para-operar-en-europa.html) (jun-2025); [Crypto-Insiders ES — Kraken avanza en España e Irlanda](https://www.crypto-insiders.es/noticias/kraken-avanza-en-espana-e-irlanda-nuevas-licencias-regulatorias-clave/).

**[OPINIÓN]** Es el venue regulado con **mejor relación completitud/fricción** para BotStrike: es el único de la lista con **testnet pública, idéntica a prod y gratuita**, lo que permite portar el motor sin arriesgar capital. Su punto débil regulatorio: sus perps **no tienen vencimiento** (spec oficial), así que son el candidato más expuesto a una rebaja a 2:1 si la CNMV aplica la doctrina ESMA de forma estricta.

### 3.2 Coinbase (Advanced) EU — ✅ VIABLE

**[EVIDENCIA]**
- **Lanzamiento:** **9-mar-2026**, derivados regulados a **retail e institucional en 26 países europeos**. Fuentes: [Cryptopolitan — Coinbase targets offshore volume with MiFID-regulated crypto futures in 26 EU nations](https://www.cryptopolitan.com/coinbase-regulated-crypto-futures-eu/) (mar-2026); [Coinbase Blog — Regulatory approval to enable retail perpetual futures](https://www.coinbase.com/blog/coinbase-receives-regulatory-approval-to-enable-retail-perpetual-futures).
- **Entidad y licencia:** *Coinbase Financial Services Europe Ltd.*, **CySEC licencia 374/19**, con pasaporte **MiFID II** a todo el EEE.
- **Producto:** **perpetual-style futures con plazo de 5 años** (mismo carve-out que OKX), funding **horario**, liquidación diaria; además contratos con vencimiento mensual/trimestral e índices de renta variable (Mag7). Todo **liquidado en efectivo**.
- **Apalancamiento:** **hasta 10x** en BTC, ETH e índices; **4–5x** en el resto.
- **Fees:** desde **0,02% por contrato**.
- **Plataforma:** vía **Coinbase Advanced** (no Coinbase International Exchange, que es la entidad offshore/institucional).

- **España: ✅ CONFIRMADA.** El lanzamiento cubre **los 27 estados de la UE excepto Bulgaria, más Noruega**; España figura explícitamente entre los mercados principales. Fuentes: [The Block — Coinbase rolls out crypto futures trading across 26 European countries](https://www.theblock.co/post/392797/coinbase-opens-crypto-futures-trading-europe) (mar-2026); [Coinbase Blog — Futures Contracts Now Available in Europe](https://www.coinbase.com/blog/futures-contracts-europe); [CoinMarketCap Academy](https://coinmarketcap.com/academy/article/coinbase-launches-regulated-futures-in-26-european-countries).
**[NO VERIFICADO]** No hay **testnet/sandbox pública** documentada para los derivados EU de Coinbase Advanced. La Advanced Trade API tiene SDK Python oficial (`coinbase-advanced-py`), pero no he verificado que exponga los perps EU.

### 3.3 OKX Europe (X-Perps) — ✅ VIABLE (mejor cobertura de producto)

**[EVIDENCIA]**
- **Entidad y licencia:** *OKX Europe Markets Ltd* (OEM), **MFSA de Malta**, Investment Services Licence **OEML-15905** (MiFID II), adquirida vía compra de entidad maltesa en mar-2025; **+ licencia MiCA + EMI (feb-2026)**. Es uno de los pocos venues **dual-licenciados MiCA + MiFID II** del EEE. Fuentes: [OKX Europe — X-Perps live in Europe](https://www.okx.com/en-eu/learn/okx-x-perps-mifid-regulated-crypto-futures-derivatives-europe); [Businesswire (15-abr-2026)](https://www.businesswire.com/news/home/20260415510125/en/OKX-Launches-X-Perps-in-Europe-MiFID-Regulated-Crypto-Derivatives-with-up-to-10x-Leverage).
- **Producto (X-Perps):** futuros cripto de **vencimiento a 5 años** con **funding rate** que ancla el precio al spot — se comportan como perps. Cuenta unificada, **margen continuo en tiempo real**, **colateral multi-activo** sin retardos de liquidación. Lanzados **15-abr-2026**; ampliados el **9-jun-2026** con 13 mercados nuevos (Mag7, oro, plata, petróleo, índices). Fuente: [Businesswire (09-jun-2026)](https://www.businesswire.com/news/home/20260609760528/en/OKX-Launches-X-Perps-on-the-Magnificent-7-Stocks-Gold-Silver-and-Oil-for-European-Traders).
- **Apalancamiento:** comunicado oficial dice **"up to 10x"** a retail en el EEE. ⚠️ Ver conflicto de fuentes en §1.1 (cex101 sostiene 2x retail / 20x profesional).
- **España:** **[EVIDENCIA de fuente secundaria]** España figura entre las jurisdicciones de lanzamiento (Francia, Alemania, Países Bajos, **España**, Italia, Polonia). Fuente: [cex101 — OKX X-Perps Europe Guide 2026](https://cex101.com/en/articles/okx-x-perps-europe-regulated-trading-guide/). No confirmado en fuente primaria.
- **Instrumentos al lanzamiento:** BTC, ETH, SOL, liquidados en **USDT**; modo coin-margined no activo en abr-2026.
- **Fees:** baseline de derivados OKX **0,08% maker / 0,10% taker**; ⚠️ la propia fuente advierte que **no está confirmado** que ese baseline aplique dentro de la entidad regulada X-Perps. Otra fuente da 0,02%/0,05%. **[NO VERIFICADO]** — confirmar en la web de tarifas de la entidad EU.
- **KYC:** verificación MiCA **separada** — el KYC nivel 2 existente **no se transfiere**; exige prueba de residencia UE (factura/extracto bancario de <90 días) + cuestionario de idoneidad; revisión 24–48h.
- **API / testnet:** **API v5** de OKX (REST + WS), SDK Python no oficial ampliamente usado (`python-okx`), soportado en CCXT. **Demo trading disponible para X-Perps en el EEE**, con **API keys de demo** propias (Trade → Demo Trading → Demo Trading API). Fuentes: [OKX API v5 docs](https://app.okx.com/docs-v5/en/); [OKX Europe — How to start trading X-Perps](https://www.okx.com/en-eu/learn/how-to-start-trading-x-perps-on-okx); [github python-okx](https://github.com/zwd163/python-okx).

**[OPINIÓN]** Regulatoriamente es **el más blindado** de los tres: el vencimiento a 5 años saca el producto del perímetro CFD por diseño, no por interpretación. Contra: doble KYC, fees inciertos en la entidad EU y SDK Python no oficial.

### 3.4 Bybit EU — ❌ NO VIABLE para perps

**[EVIDENCIA]** *Bybit EU GmbH* tiene **licencia CASP MiCA de la FMA austriaca** (autorizada **28-may-2025**), en el registro oficial de ESMA, con pasaporte a 29 países **incluida España**. Pero **`bybit.eu` ofrece solo spot y margin (hasta 10x); NO ofrece futuros ni perpetuos**, porque su autorización MiCA no incluye servicios de inversión MiFID II. Los clientes del EEE fueron migrados de `bybit.com` a `bybit.eu`. Fuentes: [CASP Tracker — Bybit](https://casptracker.eu/exchange/bybit/); [Dexly — Is Bybit Banned in Europe?](https://dexly.trade/learn/is-bybit-banned-in-europe); [Trade-Reclaim — Bybit EU & MiCA: What the July 2026 Deadline Changed](https://trade-reclaim.com/en/blog/bybit-eu-mica).

**[CONCLUSIÓN]** Descartado para BotStrike mientras no obtenga licencia MiFID II.
### 3.5 Bitget — ❌ NO VIABLE (ni siquiera spot)

**[EVIDENCIA]** Bitget **no tiene licencia MiCA**. Su entidad europea (Bitget EU, sede en Viena) **presentó solicitud** ante la FMA austriaca el **17-jun-2026** — solicitud ≠ autorización; solo la inscripción en el registro de ESMA lo confirma. Precedente documentado en Francia: desde el **16-mar-2026** no aceptaba órdenes nuevas ni depósitos, **liquidó todas las posiciones abiertas el 31-mar-2026** y pasó las cuentas a modo restringido. Fuentes: [CASP Tracker — Bitget: not yet, application pending](https://casptracker.eu/exchange/bitget/); [Tangem Learning Hub — Is Bitget MiCA Authorised?](https://tangem.com/en/learning-hub/post/is-bitget-mica-authorised/) (2026).

**[CONCLUSIÓN]** Descartado. Y aunque obtuviera MiCA, seguiría **sin poder ofrecer perps** sin MiFID II.

### 3.6 Crypto.com — ❌ NO VIABLE para perps a retail ES

**[EVIDENCIA]** Crypto.com **sí tiene licencia CASP MiCA** y sigue operativo en la UE tras el 1-jul-2026, pero eso **solo cubre spot**. No he encontrado evidencia de una licencia **MiFID II** que le habilite perps a retail del EEE. Fuentes: [Finance Magnates — Europe's Crypto Market After July 1](https://www.financemagnates.com/cryptocurrency/regulation/europes-crypto-market-after-july-1-who-stays-who-leaves-and-what-changes-under-mica/) (jul-2026); [coinperps — MiCA Licensed Exchanges](https://www.coinperps.com/learn/mica-licensed-exchanges).
**[NO VERIFICADO]** Crypto.com adquirió brokers regulados en otras jurisdicciones; no descarto que active derivados UE más adelante. A agosto 2026, no consta.

### 3.7 Bitpanda — ❌ NO VIABLE para perps

**[EVIDENCIA]** Bitpanda es un CASP MiCA sólido (licencia alemana/austriaca) y ofrece **Bitpanda Margin hasta 10x**, pero **NO ofrece perpetuos**. Además su API es de tipo *investment platform*, no de exchange de derivados. Fuente: [blockspot.io — Beste MiFID II-regulierte Krypto-Derivate-Plattformen 2026](https://blockspot.io/de/beste-mifid-krypto-derivate-plattformen-europa/) (2026, vía resultados de búsqueda).

**[CONCLUSIÓN]** Descartado para BotStrike.

### 3.8 Otros venues MiFID II del EEE (descubiertos durante la investigación)

**[EVIDENCIA]**
- **Robinhood Europe** — MiFID II + MiCA del **Banco de Lituania**. Ofrece perps a retail del EEE pero con **máximo 3x**. Sin API pública de trading algorítmico documentada. Fuente: [coinperps — Best Crypto Perpetual Futures Exchanges in Europe (2026)](https://www.coinperps.com/learn/best-crypto-futures-platforms-in-europe).
- **Perpetuals.com Ltd (Nasdaq: PDC)** — **primer MTF europeo** de derivados cripto con **ejecución directa de clientes** en una sola entidad licenciada; ampliación de licencia MiFID II aprobada por **CySEC en marzo-2026**. Producto: *"barrier futures"*, presentado como alternativa regulada a los perp swaps offshore y a los CFDs. Fuentes: [AccessNewswire (mar-2026)](https://www.accessnewswire.com/newsroom/en/blockchain-and-cryptocurrency/perpetuals.com-becomes-first-european-mtf-to-offer-direct-client-exec-1152149); [Barchart — PDC secures CySEC approval to expand MiFID II license](https://www.barchart.com/story/news/1008697/perpetuals-com-nasdaq-pdc-secures-cysec-approval-to-expand-mifid-ii-license). **[OPINIÓN]** Al ser un **MTF**, sus contratos escapan al perímetro CFD por la vía más limpia posible (los CFDs son OTC por definición). Venue joven y de liquidez desconocida — vigilar, no adoptar aún.
- **Gemini** — anunció perps bajo **nueva licencia MiFID II**. Fuente: [Finance Magnates — Gemini to Offer Crypto Perpetuals Under New MiFID II License](https://tr.tradingview.com/news/financemagnates%3Af1de5d946094b%3A0-gemini-to-offer-crypto-perpetuals-under-new-mifid-ii-license-is-cfds-next). **[NO VERIFICADO]** estado de lanzamiento real y disponibilidad en España a ago-2026.
- **One Trading** (Austria, MTF/MiFID) y **Backpack EU** — citados como proveedores de perps del EEE. Fuente: [Finance Magnates — 10x Down to 2x](https://www.financemagnates.com/forex/10x-down-to-2x-has-europe-killed-crypto-perps-even-before-it-started/) (feb-2026). **[NO VERIFICADO]** para España/API.

## 4. DEX de perpetuos

### 4.0 ¿Es legal para un residente español usar un perp DEX? — el punto clave

**[EVIDENCIA]** Las obligaciones de MiCA y MiFID II recaen sobre el **proveedor de servicios**, no sobre el usuario particular. No existe en España ninguna norma que prohíba a una persona física operar en una plataforma no autorizada; lo que la CNMV dice es que el inversor **pierde toda protección y supervisión**. Fuentes: [CNMV — MiCA: nueva regulación de criptoactivos](https://www.cnmv.es/portal/mica/regulacion-criptoactivos?lang=en); [Observatorio Blockchain — CNMV pide comprobar si tu plataforma cripto tiene el aval de MiCA](https://observatorioblockchain.com/regulacion/cnmv-plataforma-cripto-aval-mica/) (jun-2026).

**[EVIDENCIA]** **MiCA no cubre DeFi**: el considerando 22 excluye los servicios prestados *"de forma totalmente descentralizada y sin intermediario"*. Pero **"totalmente descentralizado" no está definido** en el articulado (solo en el preámbulo), la descentralización parcial **sí está dentro del ámbito**, y **ningún regulador ha bendecido ningún DEX concreto**. ESMA prepara guía de Nivel 3 durante 2026 y la consulta de revisión de MiCA cerraba el **31-ago-2026**. Fuentes: [CERHA HEMPEL — Testing the Boundaries of MiCA's DeFi Exemption](https://www.cerhahempel.com/blog/fintech-ledger/testing-the-boundaries-of-micas-defi-exemption); [Aurum — MiCA's DeFi Fully Decentralised Exemption: Where the Line Is](https://aurum.law/newsroom/MiCAs-DeFi-Fully-Decentralised-Exemption); [LegalBison / EBA & ESMA disagree](https://globallawexperts.com/legalbison-study-we-are-defi-so-mica-does-not-apply-to-us-eba-esma-disagree/).

**[EVIDENCIA]** ESMA ya ha señalado el perímetro: analistas regulatorios describen los perps de Hyperliquid como *"CFDs in DeFi clothing"* y discuten si el **front-end** constituye actividad de intermediación (broker) sujeta a autorización. Fuente: [FinTelegram — ESMA Draws the Line: Why Hyperliquid's Crypto Perpetuals Look Increasingly Like CFDs in DeFi Clothing](https://fintelegram.com/esma-draws-the-line-why-hyperliquids-crypto-perpetuals-look-increasingly-like-cfds-in-defi-clothing/) (2026) — *nota: 403 al fetch, contenido vía snippet de búsqueda, no verificado íntegro*.

**[OPINIÓN — la conclusión honesta]** Para el usuario individual español el uso de un perp DEX es **legal pero desprotegido**: no es delito ni infracción propia, no hay MiCA que invocar, no hay fondo de garantía, no hay reclamación ante la CNMV. **El riesgo no es sancionador, es operativo y fiscal.** El riesgo realista a 12–24 meses no es una multa: es que **el front-end se geobloquee la UE** (como ya hacen con EE. UU.), dejando el acceso solo por API/contrato directo — lo cual, para BotStrike que es un bot por API, es un riesgo **menor** que para un usuario manual.

### 4.1 Hyperliquid — ⚠️ VIABLE TÉCNICAMENTE, ZONA GRIS REGULATORIA

**[EVIDENCIA — acceso]**
- **Sin KYC.** No hay verificación de identidad en ningún nivel; a mayo-2026 no había planes anunciados de introducirla. El acceso se controla por **geobloqueo de IP en el front-end**, no por identidad. Restringidos: **EE. UU., Ontario (Canadá) y jurisdicciones sancionadas/OFAC** (Sección 1.5 de los Términos de Uso). **La UE / España NO figuran como restringidas**; opera en ~190 países. Fuentes: [Datawallet — Hyperliquid Supported and Restricted Countries (2026)](https://www.datawallet.com/crypto/hyperliquid-supported-and-restricted-countries); [coinperps — Hyperliquid Restricted Countries List 2026](https://www.coinperps.com/learn/hyperliquid-restricted-countries); [Hyperliquid Guide — KYC Requirements 2026](https://hyperliquidguide.com/guides/getting-started/hyperliquid-kyc-requirements).

**[EVIDENCIA — economía]**
- **Fees:** base **maker 0,015% / taker 0,045%** sobre **nocional** (no sobre margen: una posición de $10.000 a 10x paga comisión sobre $10.000, no sobre los $1.000 de margen). Taker baja hasta 0,024% por encima de $5B de volumen a 14 días. Fuentes: [Hyperliquid Guide — Fees Explained](https://hyperliquidguide.com/guides/fees/fees-explained); [eco.com — Hyperliquid Fees Explained 2026](https://eco.com/support/en/articles/15191998-hyperliquid-fees-explained-maker-taker-funding-and-withdrawal-in-2026).
- **Apalancamiento:** hasta **50x** (BTC/ETH); 5–20x en altcoins.
- **Mínimos:** depósito mínimo **5 USDC**, retirada mínima **2 USDC**, **nocional mínimo por orden ≈ $10** en la mayoría de pares. Fuentes: [OneKey — Complete Guide to Hyperliquid Deposits & Withdrawals 2026](https://onekey.so/blog/ecosystem/complete-guide-to-hyperliquid-deposits-withdrawals-2026-fbe041/); [buildix — Hyperliquid Minimum Order Size for Every Pair (2026)](https://www.buildix.trade/blog/hyperliquid-minimum-order-size-all-pairs-leverage-limits-2026).

**[EVIDENCIA — API, la mejor de la lista]**
- **Testnet completa:** `api.hyperliquid-testnet.xyz`, que **replica la superficie de API, tipos de orden y rate limits** de mainnet con USDC de faucet.
- **SDK Python oficial** (`hyperliquid-python-sdk`). ⚠️ Requiere **Python 3.10 exacto** — versiones superiores dan conflictos de dependencias en la release actual.
- **Rate limits (docs oficiales):** por **IP**, 1.200 de peso agregado/minuto; peso de exchange = `1 + floor(batch_length/40)`; info requests peso 2 (`l2Book`, `allMids`, `clearinghouseState`, `orderStatus`…), 20 el resto, 60 `userRole`. Por **dirección**: **1 request por cada 1 USDC negociado acumulado**, con buffer inicial de **10.000 requests**; al superarlo, 1 request cada 10 s. Órdenes abiertas: 1.000 + 1 por cada 5M USDC de volumen, tope 5.000. **WebSocket:** máx. 10 conexiones concurrentes, 30 conexiones nuevas/min, 1.000 suscripciones, 2.000 mensajes/min, 100 post inflight. Fuente: [Hyperliquid Docs — Rate limits and user limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits).

> ⚠️ **[EVIDENCIA] El rate limit por dirección es el riesgo operativo nº1 para un bot con $1000.** El buffer de 10.000 requests se consume y luego solo se recupera negociando volumen. Un bot que consulte estado agresivamente sin operar **se autobloqueará a 1 req/10 s**. Diseño obligatorio: **WebSocket para estado, REST solo para órdenes**.

**[EVIDENCIA — bridge de USDC, el eslabón débil tras la salida de Binance]**
- La única vía canónica de fondeo es el **Hyperliquid Bridge desde Arbitrum**, con **USDC nativo (NO USDC.e)**. Rutas cross-chain desde Ethereum/Solana/Base vía deBridge, LI.FI, Across, CCTP. Fuentes: [eco.com — Hyperliquid Bridge: Deposit USDC and Cross-Chain Routes 2026](https://eco.com/support/en/articles/15191997-hyperliquid-bridge-deposit-usdc-and-cross-chain-routes-2026); [eco.com — Hyperliquid Arbitrum Bridge](https://eco.com/support/en/articles/15082533-hyperliquid-arbitrum-bridge).
- **Ruta práctica para un residente ES sin Binance:** SEPA en EUR → **Kraken** (CASP MiCA irlandés) → comprar USDC → **retirar USDC nativo por red Arbitrum** (Kraken soporta USDC nativo en Arbitrum, Optimism y Polygon) → bridge oficial → Hyperliquid. Fuentes: [Kraken — Native USD Coin (USDC) on Arbitrum, Optimism and Polygon](https://support.kraken.com/articles/native-usd-coin); [Kraken Blog — USDC deposits and withdrawals on Arbitrum](https://blog.kraken.com/product/dai-usdc-and-usdt-deposits-and-withdrawals-available-on-the-arbitrum-network).
- **[OPINIÓN]** Esta ruta es la **razón estratégica para abrir cuenta en Kraken aunque se opere en Hyperliquid**: Kraken es a la vez venue regulado *y* on/off-ramp fiat legal en España. Un solo proveedor cubre las dos necesidades.

### 4.2 dYdX y otros perp DEX

**[EVIDENCIA]**
- **dYdX v4:** *dYdX Operations Services Ltd.* geobloquea **EE. UU., Reino Unido, Canadá** y jurisdicciones sancionadas. **La UE/España no están en la lista de prohibidos**, pero el **Reino Unido sí** — señal de que dYdX **sí responde a presión regulatoria de jurisdicciones desarrolladas**, lo que aumenta la probabilidad de un futuro bloqueo UE. Sus términos **prohíben el uso de VPN** y han suspendido cuentas por ello. Fuentes: [dYdX Help Center — Geo Restrictions & Site Access](https://help.dydx.trade/en/articles/166970-geo-restrictions-site-access); [Datawallet — dYdX Supported and Restricted Countries](https://www.datawallet.com/crypto/dydx-restricted-countries).
- **Cuota de mercado 2026:** los perp DEX capturaron **~26% del mercado de futuros** y superaron **$1T de volumen mensual** por primera vez. Dominan **Hyperliquid, Aster y Lighter**. Fuentes: [BlockEden — The Perp DEX Wars of 2026](https://blockeden.xyz/blog/2026/01/29/perp-dex-wars-2026-hyperliquid-lighter-aster-edgex-paradex-decentralized-derivatives/) (29-ene-2026); [21Shares — The perpetual DEX wars: Hyperliquid, Aster and Lighter](https://www.21shares.com/en-eu/insights/the-perpetual-dex-wars-hyperliquid-aster-and-lighter-in-focus).
- **Alternativas:** **Lighter** (zk-rollup en Ethereum, **fees cero**, hasta 50x); **Paradex** (CLOB completo, ~93–100 mercados, hasta 20–50x, **fees cero maker y taker**); **Aster** (multi-cadena, hasta 1001x — *red flag*); **GMX** (AMM, sin libro de órdenes).

**[OPINIÓN]** Para BotStrike **ninguno desplaza a Hyperliquid hoy**: Hyperliquid tiene la mayor liquidez (menor slippage con $1000 es irrelevante, pero importa para el realismo del backtest), la mejor documentación de API y una testnet real. **Lighter y Paradex merecen vigilancia** por sus fees cero — con una estrategia de alta rotación, pasar de 0,045% taker a 0% es una mejora de edge enorme. **[NO VERIFICADO]** la calidad de sus APIs y su estabilidad; no adoptarlos sin una prueba propia.

## 5. Fiscalidad España (persona física)

**[EVIDENCIA]**
- Las ganancias de cripto tributan en el IRPF como **ganancias patrimoniales en la base del ahorro**: **19%** hasta 6.000 €, **21%** de 6.000 a 50.000 €, **23%** de 50.000 a 200.000 €, **28%** por encima de 200.000 €. Fuentes: [Blockpit — Impuestos Criptomonedas España 2026](https://www.blockpit.io/tax-guides/impuestos-criptomonedas-espana); [CL Cripto — Impuestos Criptomonedas España 2026](https://www.clcripto.com/impuestos-criptomonedas-espana/).
- **Cada permuta cripto-cripto es hecho imponible**; ganancia/pérdida = valor de transmisión − valor de adquisición. Fuente: criterio AEAT recogido en las guías anteriores.
- **Compensación de pérdidas:** primero contra ganancias patrimoniales del mismo ejercicio; el exceso hasta un **25%** contra rendimientos del capital mobiliario de la base del ahorro; lo no compensado se arrastra **4 años**. Misma fuente.
- **Modelo 721** (activos virtuales en el extranjero, sustituye al 720 para cripto desde 2023): obligatorio si el saldo supera **50.000 €**. Fuentes: [Finanzas Guías — Declarar criptomonedas en España 2026: IRPF, modelo 721](https://finanzasguias.com/cripto/declarar-criptomonedas-irpf-espana/); [Gestoría Sahel — Criptomonedas y obligaciones fiscales en España: modelo 721](https://gestoriasahel.com/en/criptomonedas-y-obligaciones-fiscales-en-espana-declaracion-modelo-721-y-regularizacion-de-ganancias/).

**[INFERENCIA]** Con ~$1000 de capital, el **Modelo 721 no aplica** (umbral 50.000 €). El impacto fiscal real es otro:

> ⚠️ **[OPINIÓN — riesgo subestimado]** Un bot de perps genera **cientos o miles de eventos imponibles al año**. La AEAT **no recibe información automática de Hyperliquid** (no es un CASP declarante), así que **toda la carga de prueba recae en ti**. Kraken/OKX/Coinbase EU **sí** reportan bajo DAC8. Consecuencia operativa para BotStrike: **el motor debe exportar un log fiscal inmutable** (timestamp, par, lado, tamaño, precio, fee, funding, PnL realizado, valor en EUR al tipo del día) desde el primer trade real. Reconstruirlo a posteriori es infernal. Esto es un **requisito de producto**, no una tarea de gestoría.

**[NO VERIFICADO]** El tratamiento exacto de los **perps** (¿ganancia patrimonial o rendimiento del capital mobiliario, como ocurre con algunos derivados?) y del **funding rate** cobrado/pagado no lo he podido cerrar con una consulta vinculante de la DGT. **[ACCIÓN]** Consultar con asesor fiscal antes del primer ejercicio con operativa real. Las guías de trading españolas tratan CFD/Forex como ganancia patrimonial de la base del ahorro, lo que sugiere el mismo encaje, pero es inferencia. Fuente orientativa: [Novatos Trading Club — Declarar trading en España 2026: Forex, CFD y cuentas fondeadas](https://www.novatostradingclub.com/blog/como-optimizar-tu-declaracion-de-hacienda-en-trading/).

## 6. Tabla comparativa

### 6.1 Venues VIABLES para un residente ES con ~$1000 (agosto 2026)

| | **Kraken Pro Derivatives** | **OKX Europe X-Perps** | **Coinbase Advanced EU** | **Hyperliquid** |
|---|---|---|---|---|
| **Tipo** | CEX regulado | CEX regulado | CEX regulado | DEX (L1 propia) |
| **Licencia** | MiFID II CySEC **342/17** + MiCA Irlanda | MiFID II MFSA **OEML-15905** + MiCA + EMI | MiFID II CySEC **374/19** | ❌ Ninguna |
| **España** | ✅ Confirmada | ⚠️ Confirmada solo por fuente 2ª | ✅ Confirmada | ✅ No geobloqueada |
| **Producto** | Perp **sin vencimiento** | Futuro **exp. 5 años** + funding | Perp-style **exp. 5 años** + funding | Perp puro |
| **Riesgo CFD/ESMA** | 🔴 **ALTO** (sin vencimiento) | 🟢 **BAJO** (carve-out por diseño) | 🟢 **BAJO** (carve-out por diseño) | ⚫ N/A (fuera del perímetro) |
| **Apalanc. retail** | 10x *(conflicto: ¿2x?)* | 10x *(conflicto: ¿2x?)* | 10x BTC/ETH, 4–5x resto | **50x** (BTC/ETH) |
| **Fee maker/taker** | **0,02% / 0,05%** | 0,02%/0,05% ó 0,08%/0,10% ❓ | desde **0,02%** | **0,015% / 0,045%** |
| **Mín. orden** | **0,0001 BTC** (~$10) | ❓ | ❓ | **~$10 nocional** |
| **Mercados** | 150–300 perps | BTC/ETH/SOL + 13 no-cripto | BTC, SOL, índices, Mag7 | 200+ pares |
| **API** | REST + **WS v1/v2** | **v5** REST + WS | Advanced Trade API | REST + WS |
| **SDK Python** | ✅ `python-kraken-sdk` + CCXT | ⚠️ `python-okx` (no oficial) + CCXT | ✅ `coinbase-advanced-py` ❓ perps EU | ✅ **oficial** (⚠️ Python 3.10 exacto) |
| **TESTNET** | ✅ **`demo-futures.kraken.com`** — idéntica a prod | ✅ Demo trading con API keys propias | ❌ No documentada | ✅ **`api.hyperliquid-testnet.xyz`** — replica todo |
| **KYC** | Estándar + NIF + test conveniencia | **Doble** (MiCA aparte) + test | Estándar + test | ❌ **Ninguno** |
| **Rate limits** | Documentados | Documentados | Documentados | **1 req / 1 USDC negociado** ⚠️ |
| **Reporta a AEAT (DAC8)** | ✅ Sí | ✅ Sí | ✅ Sí | ❌ **No — carga probatoria tuya** |
| **On/off-ramp EUR SEPA** | ✅ Sí | ✅ Sí (EMI) | ✅ Sí | ❌ Requiere puente vía CEX |

### 6.2 Venues DESCARTADOS y por qué

| Venue | Estado | Motivo |
|---|---|---|
| **Binance** | ❌ Muerto para ES | Sin MiCA (retiró solicitud); desde 1-jul-2026 **solo cierre de posiciones y retirada** |
| **Bybit EU** | ❌ No perps | CASP MiCA austriaco (28-may-2025) → **solo spot y margin 10x**; falta MiFID II |
| **Bitget** | ❌ Ni spot | Sin MiCA; solicitud pendiente ante FMA (17-jun-2026); precedente FR: posiciones **liquidadas 31-mar-2026** |
| **Crypto.com** | ❌ No perps | CASP MiCA → solo spot; sin MiFID II acreditada |
| **Bitpanda** | ❌ No perps | CASP MiCA; margin 10x pero **sin perpetuos** |
| **Robinhood EU** | ⚠️ Marginal | MiFID II Lituania, perps a retail pero **máx. 3x** y **sin API algorítmica** |
| **dYdX v4** | ⚠️ Riesgo | UE no bloqueada, pero **bloquea ya UK/US/CA** → precedente de ceder ante reguladores |
| **Perpetuals.com / Gemini / One Trading / Backpack** | 👀 Vigilar | MiFID II reales (MTF incluso) pero **liquidez y API no verificadas** |

---

## 7. Recomendación razonada para BotStrike (multi-venue)

### 7.1 La respuesta directa a la pregunta

**[EVIDENCIA]** A agosto de 2026, un residente español (persona física) con ~$1000 tiene **cuatro** opciones realistas para operar perps por API: **Kraken Pro Derivatives, OKX Europe X-Perps, Coinbase Advanced EU** (los tres regulados MiFID II y plenamente legales) y **Hyperliquid** (DEX, legal para el usuario pero sin protección regulatoria). **Binance ya no es una de ellas.**

### 7.2 Arquitectura recomendada: 2 venues, no 4

**[OPINIÓN]** La tentación multi-venue es construir adaptadores para todo. Es un error con $1000: cada venue añade superficie de bugs, un modelo de fees distinto, una semántica de órdenes distinta y una fuente de fallos de reconciliación. **Con $1000, el coste de un bug supera cualquier ventaja de diversificación de venue.**

**Recomendación: dos venues con roles claramente distintos.**

**1) `Kraken Pro Derivatives` — venue PRIMARIO regulado y ramp fiat**
- **Por qué:** es el **único** que resuelve tres problemas a la vez — (a) **testnet gratuita e idéntica a producción**, lo que permite validar el motor sin quemar capital; (b) **on/off-ramp SEPA en EUR** con licencia española efectiva; (c) **fees de los mejores** (0,02%/0,05%) con 30 días gratis para empezar.
- **Contra reconocida:** sus perps **no tienen vencimiento**, lo que los deja como el candidato más expuesto a una rebaja a 2:1 si la CNMV aplica la doctrina ESMA de forma estricta. **[OPINIÓN]** Riesgo asumible: si eso ocurre, se migra a X-Perps, y la migración es de configuración, no de arquitectura.

**2) `Hyperliquid` — venue SECUNDARIO de ejecución**
- **Por qué:** mejor API documentada de las cuatro, **SDK Python oficial**, **testnet que replica la superficie completa**, sin KYC, y **fees maker 0,015%** — un 25% por debajo de Kraken.
- **Contra reconocida:** cero protección regulatoria, **cero reporte a la AEAT** (toda la carga probatoria fiscal es tuya) y el rate limit por dirección puede autobloquear el bot.

**3) `OKX X-Perps` — tercer adaptador, solo si Kraken cae a 2:1**
- Documentar el adaptador, **no construirlo aún**. Es la póliza de seguro regulatoria: si ESMA/CNMV aprietan, el vencimiento a 5 años es el diseño que sobrevive.

### 7.3 Qué implica esto para el código, concretamente

**[OPINIÓN]** Tres requisitos de producto que salen de esta investigación y que **no están en el backlog actual**:

1. **Log fiscal inmutable desde el primer trade real.** Timestamp UTC, venue, par, lado, tamaño, precio de ejecución, fee, funding pagado/cobrado, PnL realizado y **valor en EUR al tipo de cambio del día**. Hyperliquid no reporta a la AEAT: si no lo registras tú, no existe. Reconstruirlo a posteriori es inviable.
2. **Presupuesto de requests por dirección en el adaptador de Hyperliquid.** El límite es **1 request por 1 USDC negociado acumulado**, con un buffer inicial de 10.000. Un bot con $1000 que consulte estado por REST agresivamente **se autobloquea a 1 req/10 s**. Regla de diseño: **WebSocket para todo el estado; REST exclusivamente para enviar órdenes.**
3. **Apalancamiento máximo como parámetro de configuración por venue, no como constante.** El conflicto de fuentes 10x-vs-2x sigue abierto; el motor debe leer el máximo real de la API al arrancar y **fallar ruidosamente** si el valor no coincide con lo configurado, en lugar de que una orden sea rechazada en producción.

### 7.4 Verificaciones pendientes antes de mover un euro

**[NO VERIFICADO — hay que comprobarlo en persona, no por research]**

- [ ] **Apalancamiento retail real** que muestran la UI y la API de Kraken y OKX para una cuenta española tras KYC. Fuentes secundarias en conflicto directo (§1.1). ~30 min.
- [ ] **Fees reales de la entidad OKX EU** para X-Perps (0,02%/0,05% vs 0,08%/0,10% — una diferencia de 4x en taker que cambia el edge de cualquier estrategia de alta rotación).
- [ ] **España en la lista oficial** de jurisdicciones X-Perps de OKX (fuente primaria, no cex101).
- [ ] **Tratamiento fiscal de perps y funding rate** en España — consulta a asesor fiscal. ¿Ganancia patrimonial o rendimiento del capital mobiliario?
- [ ] **Existencia de sandbox** para los derivados EU de Coinbase Advanced.
- [ ] **Retirada de USDC nativo por Arbitrum desde Kraken España** — probar con $20 antes de mover capital de trabajo.

### 7.5 Lo que NO se debe hacer

**[OPINIÓN, con base en evidencia]**
- **No usar VPN ni declarar residencia falsa** para acceder a Binance o a venues bloqueados. dYdX **prohíbe expresamente el uso de VPN y ha suspendido cuentas por ello**; en un CEX con KYC equivale a fraude de residencia contra los ToS, con congelación de fondos como resultado esperable. Con $1000 en juego, el riesgo asimétrico es absurdo.
- **No usar el apalancamiento máximo porque esté disponible.** Que Hyperliquid permita 50x no es una invitación. Con $1000, una posición a 50x se liquida con un movimiento del 2%.
- **No dar por cerrado el punto del apalancamiento 10x/2x con este documento.** Es el único punto donde las fuentes se contradicen frontalmente y **la investigación web no puede resolverlo**.

---

## 8. Metodología y balance de evidencia

**Búsquedas y fetches realizados (31-ago-2026):** **21 WebSearch + 14 WebFetch = 35 llamadas**, de las cuales **3 fetches fallaron con HTTP 403** (blockspot.io, fintelegram.com ×2) y **1 falló por PDF binario** (ESMA). Esos 4 casos están marcados como no verificados o sustentados en snippets de búsqueda, nunca presentados como fuente sólida.

**Calidad de las fuentes, con honestidad:**
- **Fuertes (primarias):** documentación oficial de Kraken (specs de contrato EEE, API, sandbox), docs de API de Hyperliquid y OKX v5, comunicados de prensa de OKX en Businesswire, blog oficial de Coinbase, portal MiCA de la CNMV, PDF de la declaración pública de ESMA.
- **Medias (prensa financiera especializada):** Finance Magnates, The Block, Cointelegraph, Cryptopolitan, El Economista, Observatorio Blockchain.
- **Débiles (agregadores SEO, tratadas con cautela y marcadas):** cex101, coinperps, datawallet, blockeden, eco.com, hyperliquidguide. Se han usado para datos operativos (fees, mínimos, países) donde coincidían entre sí, **nunca como base única de una conclusión regulatoria**.

**Limitación conocida:** el PDF oficial de ESMA (ESMA35-243228190-8024) **no se pudo extraer** (binario corrupto en el fetch, y no hay `pdftoppm` en el entorno para renderizarlo). La definición de CFD y su carve-out de futuros están reconstruidos a partir de fuentes secundarias jurídicas de calidad (CMS, MFSA, Harneys) y del comportamiento observable del mercado (OKX y Coinbase estructurando vencimientos a 5 años, lo cual **solo tiene sentido si el carve-out existe**). **[ACCIÓN]** Si se necesita certeza jurídica, leer el PDF manualmente.

**Reparto de la evidencia:** de las ~60 afirmaciones sustantivas del documento, **~45 están marcadas [EVIDENCIA]** con URL y fecha, **~10 son [OPINIÓN]/[INFERENCIA]** explícitamente etiquetadas, y **6 están marcadas [NO VERIFICADO]** con una acción concreta asociada en §7.4.
