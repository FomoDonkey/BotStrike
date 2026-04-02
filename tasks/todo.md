# BotStrike — Tasks

## Completado
- [x] Fetch y documentar API Strike Finance
- [x] Diseño de arquitectura modular (10 modulos)
- [x] Config & Settings (config/settings.py)
- [x] Core types & enums (core/types.py)
- [x] Strike Finance REST client con auth Ed25519 (exchange/strike_client.py)
- [x] WebSocket client market + user data (exchange/websocket_client.py)
- [x] Indicadores tecnicos: ATR, SMA, EMA, Z-score, RSI, ADX, Bollinger, momentum
- [x] Detector de regimen adaptativo (core/regime_detector.py)
- [x] Market Data Collector con OHLCV en tiempo real (core/market_data.py)
- [x] Estrategia Mean Reversion con Z-score dinamico
- [x] Estrategia Trend Following con trailing stops
- [x] Estrategia Market Making con Avellaneda-Stoikov
- [x] Risk Manager: drawdown, circuit breaker, sizing dinamico
- [x] Portfolio Manager: asignacion dinamica por regimen
- [x] Order Execution Engine: limit/market/bracket, batch MM
- [x] Logging & Metrics: JSONL, performance tracking
- [x] Backtester con fees, slippage, funding, liquidaciones
- [x] Main orchestrator con CLI args
- [x] Dashboard Live Operations, Backtesting, Riesgo & What-If
- [x] VPIN, Hawkes, A-S Engine mejorado, MicrostructureEngine
- [x] Integracion microestructura en MM, RiskManager, main.py, backtester, dashboard
- [x] HistoricalDataLoader: carga CSV/Parquet de trades o OHLCV
- [x] RealisticBacktester: replica exacta del live loop tick-by-tick
- [x] Generador de trades sinteticos realistas con GARCH-like volatility
- [x] Endpoints REST de platform stats: funding, OI, basis, spread, L/S ratio
- [x] StrikeDataCollector: recoleccion continua WS trades/klines/orderbook + REST stats
- [x] Almacenamiento automatico en Parquet diario con dedup y auto-rotacion
- [x] HistoricalDataLoader.load_from_collector() lee datos recolectados
- [x] CLI: --collect-data, --backtest-real, --backtest-realistic, --backtest, --dashboard
- [x] Dashboard: deteccion automatica de datos reales + modo "Datos Reales" en backtesting
- [x] Fix: collector ahora SIEMPRE recolecta de MAINNET (no testnet)
- [x] Fix: arquitectura dual WS+REST (WS primario, REST/10-15s backup, flush/30s)
- [x] Fix: backtester usa orderbook REAL de datos recolectados (no simulado con ATR)
- [x] Fix: load_from_collector() ahora carga trades + orderbook + klines
- [x] Script para instalar collector como servicio de Windows (Task Scheduler)

- [x] Audit profundo de bugs: ~30 fixes aplicados sin romper funcionalidad
- [x] Fix collector: pantalla negra -> output visible + status cada 60s
- [x] Fix collector: buffer race condition (trades perdidos durante flush)
- [x] Fix collector: deteccion de proceso en Windows 11 (wmic -> Get-CimInstance)

- [x] Trade Database: SQLite persistente con TradeRecord, TradeRepository, TradeDBAdapter
- [x] Performance Analytics: PerformanceAnalyzer multi-dimensional (estrategia/simbolo/regimen/periodo)
- [x] Data Lifecycle: StorageManager (compactacion semanal, agregacion klines, retencion)
- [x] Data Catalog: metadatos JSON de todos los datasets disponibles
- [x] Integracion Trade DB en live trading (BotStrike.on_order_update -> TradeDBAdapter)
- [x] Integracion Trade DB en backtesting (import automático de BacktestResult)
- [x] CLI: --optimize-storage, --analytics, --catalog, --session-id
- [x] Analytics report con correlacion entre estrategias y analisis cruzado estrategia/regimen

- [x] Audit profundo #2: 5 bugs encontrados y corregidos sin romper funcionalidad
- [x] Fix CRITICO: close_jsonl() recursion infinita -> file handle leak (backtester.py)
- [x] Fix: self.base_mu -> self.mu en HawkesEstimator (AttributeError en edge case)
- [x] Fix: end_session guardaba initial_equity incorrecto (trade_database/adapter.py)
- [x] Fix: Hawkes no se actualizaba en on_bar() -> microestructura incompleta en backtests
- [x] Cleanup: variable muerta _micro_adjusted_size en risk_manager.py

- [x] Audit profundo #3: 4 bugs adicionales encontrados y corregidos
- [x] Fix: ISO week vs strftime %W mismatch en StorageManager (compactaba semana incorrecta)
- [x] Fix: regime_history offset — trades tenian regimen incorrecto o vacio (100% -> correcto ahora)
- [x] Fix: trades_to_cumulative_pnl no hacia ffill en multi-strategy (grafico con gaps)
- [x] Cleanup: import shutil no usado en storage_manager.py

- [x] Audit profundo #4: 2 bugs adicionales corregidos (11 total acumulado)
- [x] Fix: backtester exit path usaba jsonl_file.close() directo sin marcar _jsonl_open=False
- [x] Fix: dashboard refresh_rate slider ignorado — sleep hardcodeado a 5s en vez de usar valor del slider

- [x] Recalibracion profunda de microestructura (VPIN + A-S + bucket sizes)
- [x] Fix: VPIN bucket sizes por activo (BTC=$50k, ETH=$10k, ADA=$500) — normal market ya no es toxic
- [x] Fix: A-S spread formula — reemplazada con ATR-based que responde a gamma/VPIN/Hawkes (antes stuck 7bps)
- [x] Fix: A-S reservation price usa ATR (antes sigma^2 producia $0.0000005 de ajuste)
- [x] Fix: A-S spread floor dinamico por gamma_mult — VPIN/Hawkes siempre visibles en spread

- [x] Backtester trade dicts: ahora incluyen fee, slippage_bps, duration_sec, timestamp
- [x] PerformanceAnalyzer: drawdown_events, duration_distribution, fee_distribution
- [x] Walk-forward backtesting: WalkForwardBacktester con N folds train/test
- [x] Parameter optimization: ParameterOptimizer grid search con ranking por metrica
- [x] CLI: --walk-forward, --optimize, --symbol, --folds, --metric
- [x] Analytics CLI: ahora muestra PnL por simbolo, estrategia x regimen, distribuciones

- [x] Audit profundo #5: 0 bugs nuevos, 1 unused import limpiado, 51 archivos compilados OK

- [x] Paper Trading: PaperTradingSimulator con fills simulados, SL/TP en tiempo real
- [x] Paper Trading: integrado en BotStrike con pipeline identico a live (logger, metrics, portfolio, DB)
- [x] Paper Trading: datos reales de MAINNET, posiciones virtuales, equity tracking completo
- [x] CLI: --paper flag para activar paper trading

- [x] Audit #6: 3 bugs en paper trading encontrados y corregidos
- [x] Fix: strategies no veian posiciones paper (MR exit y TF trailing stop no funcionaban)
- [x] Fix: entry fee double-counted (3x total en vez de 2x) — ahora entry.fee=0, close cobra ambos lados
- [x] Fix: max_drawdown cancel_all enviaba DELETE real al exchange en paper mode
- [x] Cleanup: import TradingConfig no usado en paper_simulator.py

- [x] P1: MM loop dedicado a 500ms (mm_interval_sec config, _mm_loop separado de _strategy_loop)
- [x] P1: Slippage dinamico: base + size_impact + regime_mult + hawkes_impact (execution/slippage.py)
- [x] P1: Integrado en backtester (ambos), paper_simulator
- [x] P2: analyze_by_vpin_bucket() en PerformanceAnalyzer + wired en CLI --analytics
- [x] P4: StressTestGenerator: flash crashes, gaps, baja liquidez, cascadas de liquidacion
- [x] P4: CLI --backtest-stress con comparacion normal vs stress

- [x] Audit profundo #7: ~40 fixes aplicados sin romper funcionalidad (5 CRITICAL, 15 HIGH, 15 MEDIUM, 5 LOW)
- [x] Fix bar boundary tick leakage: ticks del siguiente bar ya no se incluyen en el actual
- [x] Fix walk-forward: ahora optimiza parametros en training data antes de evaluar en test
- [x] Fix Sharpe ratio: usa retornos diarios agregados en vez de per-trade (3 archivos)
- [x] Fix MM inventory unwind: genera señal de cierre cuando régimen cambia y MM se desactiva

- [x] Tests funcionales de estrategias: 15/15 tests pasados (base, MR, TF, MM)
- [x] Tests funcionales de bug fixes: 52/52 tests pasados (risk, portfolio, paper_sim, order_engine, trade_db)

- [x] P1: Funding rate integrado en decisiones — bloquea entradas contra funding extremo, reduce size con funding moderado
- [x] P2: Rate limiter en StrikeClient — token bucket 50 req/10s, throttlea automaticamente
- [x] P2: Graceful degradation — no opera con datos stale (>30s warn, >120s block)

- [x] Audit #8: Paper simulator regime slippage fix — señales ahora incluyen regime en metadata para slippage dinámico
- [x] Audit #8: Multi-bar gap handling — MarketDataCollector cierra múltiples barras si hubo gap de datos
- [x] Audit #8: MM safety checks en _mm_loop — circuit breaker y max drawdown verificados antes de MM
- [x] Audit #8: RealisticBacktester mm_unwind exit + funding_rate en validate_signal
- [x] Audit #8: MetricsCollector cumulative avg_win/avg_loss/profit_factor consistente con contadores
- [x] Audit #8: BacktestResult profit_factor float("inf") → 9999.99 (JSON serializable)
- [x] Audit #8: Cleanup imports no usados (math en MM, Set en order_engine, Tuple en regime/historical, List+Tuple en portfolio)
- [x] Audit #8: math.e en portfolio_manager en vez de 2.718 hardcodeado
- [x] Audit #8: test_self_audit faltaba _last_data_time en MDC mock

- [x] Quant Review: RSI NaN fix cuando avg_loss=0 (retorna 100 correctamente)
- [x] Quant Review: Hawkes stability validation (alpha < beta enforced en __init__)
- [x] Quant Review: Maintenance margin 0.5% → 2% en liquidation check (realista para crypto)
- [x] Quant Review: Gamma effective cap 5x base en A-S engine (evita spreads absurdos)
- [x] Quant Review: Verificado inventory skew A-S correcto (numéricamente validado)
- [x] Quant Review: Verificado MR threshold scaling correcto (baja vol = mas senales)
- [x] Quant Review: Verificado fee calculation correcta (sobre nocional, no margen)

- [x] Tick Quality Guards: warmup period 5s post-conexion WS (descarta snapshots cacheados)
- [x] Tick Quality Guards: first tick skip por simbolo post-reconexion
- [x] Tick Quality Guards: stale tick guard (delta > 5% rechazado con log)
- [x] Tick Quality Guards: jitter EMA tracking para monitoreo de calidad de conexion
- [x] Tick Quality Guards: on_ws_connected() callback desde websocket_client
- [x] Tick Quality Guards: get_tick_quality_stats() con metricas de accepted/rejected
- [x] Tick Quality Guards: logging periodico en _metrics_loop

- [x] Quant Upgrade: Volatility Targeting global — escala posiciones para mantener vol anualizada constante (15% target)
- [x] Quant Upgrade: Risk of Ruin — calculo analitico + auto-throttle (>3% reduce, >10% pausa)
- [x] Quant Upgrade: Kelly Criterion capped — Half-Kelly por estrategia con floor 0.5% ceiling 3%
- [x] Quant Upgrade: Order Book Imbalance alpha — multi-nivel con decay exponencial, delta tracking
- [x] Quant Upgrade: OBI integrado en MR (confirmacion reversal), TF (boost confianza), MM (spread skew)
- [x] Quant Upgrade: Risk Parity / Covariance — inverse-vol weighting blended 30% con pesos de regimen
- [x] Quant Upgrade: Correlation Regime — detecta stress (corr>0.85) y reduce exposicion automaticamente
- [x] Quant Upgrade: Monte Carlo Bootstrap — simulacion de equity curves por resampleo de trades
- [x] Quant Upgrade: Slippage Real Measurement — tracking expected vs fill price en cada trade
- [x] Quant Upgrade: Feature Attribution — signal_features guardadas en cada Trade para analisis
- [x] Quant Upgrade: Inventory Half-Life en A-S — penaliza inventario viejo con time-weighted factor
- [x] Quant Upgrade: Asymmetric gamma — inventory age escala skew para forzar liquidacion
- [x] Quant Upgrade: Kelly integrado en base strategy _calc_position_size
- [x] Quant Upgrade: Vol Targeting + Correlation Stress + RoR integrados en RiskManager.validate_signal
- [x] Quant Upgrade: Covariance Tracker integrado en PortfolioManager.get_allocation
- [x] Quant Upgrade: SlippageTracker integrado en order_engine y paper_simulator
- [x] Quant Upgrade: Quant models status logged en _metrics_loop
- [x] Tests: 15/15 strategy, 52/52 bug fixes, 21/21 core, nuevo test suite de 15 quant models — all passing

- [x] Execution Intelligence: Microprice Level-1 + Multi-Level + Adjusted (Stoikov 2018)
- [x] Execution Intelligence: Microprice integrado en OrderBook.microprice property + Market Making A-S
- [x] Execution Intelligence: FillProbabilityModel — P(fill | distance, vol, depth, intensity, horizon)
- [x] Execution Intelligence: QueuePositionModel — posicion estimada en cola, tiempo al frente
- [x] Execution Intelligence: SmartOrderRouter — decision limit vs market basada en costos
- [x] Execution Intelligence: SpreadPredictor — predice spread futuro con features de mercado
- [x] Execution Intelligence: TradeIntensityModel — Hawkes bidireccional (buy vs sell separados)
- [x] Execution Intelligence: VWAPEngine — Time-Weighted execution para ordenes grandes
- [x] Execution Intelligence: ExecutionAnalytics — implementation shortfall, fill rate, timing cost
- [x] Execution Intelligence: Advanced Slippage Model — 7 componentes (spread, sqrt-impact, vol, Hawkes, regime, OBI adverse, VPIN toxicity)
- [x] Execution Intelligence: Smart Router integrado en OrderExecutionEngine.execute_signal
- [x] Execution Intelligence: Trade Intensity alimentado tick-by-tick desde WebSocket
- [x] Execution Intelligence: Microprice y book depth inyectados en signal metadata para routing
- [x] Tests: 34/34 execution intelligence + 88/88 regression — all passing

- [x] Audit profundo #9: 107 issues encontrados, ~40 fixes aplicados sin romper (153/153 tests pass)
- [x] Fix CRITICO: `_positions` nunca se poblaba en live mode — estrategias veian None (main.py)
- [x] Fix CRITICO: `asyncio.gather` sin `return_exceptions` — un task crash mataba todo el bot (main.py)
- [x] Fix CRITICO: Sin timeout en aiohttp — bot se congelaba 5min si exchange colgaba (strike_client.py)
- [x] Fix CRITICO: SL/TP se colocaban en status NEW (antes de fill) — huérfanos posibles (order_engine.py)
- [x] Fix CRITICO: Z-score explotaba con std near-zero (1e-300 → z-scores de 1e+300) (indicators.py)
- [x] Fix CRITICO: Vol targeting usaba ddof=0 → over-leverage sistemático (quant_models.py)
- [x] Fix CRITICO: Monte Carlo ruin check mezclaba % con absoluto → prob_ruin incorrecto (quant_models.py)
- [x] Fix CRITICO: Hawkes O(n²) → O(1) kernel analítico + usa mu original (no adaptativo) (microstructure.py)
- [x] Fix HIGH: Rate limiter no re-chequeaba tras sleep → burst posible (strike_client.py)
- [x] Fix HIGH: `get_market_snapshot` sin `return_exceptions` → un API fail mataba todo (strike_client.py)
- [x] Fix HIGH: `cancel_order` usaba `order_id` snake_case → probablemente API rechazaba (strike_client.py)
- [x] Fix HIGH: Slippage `abs()` perdía signo — no distinguía favorable/adverso (order_engine.py)
- [x] Fix HIGH: Backtester no aplicaba slippage en exits → sobreestimaba PnL (backtester.py)
- [x] Fix HIGH: Calmar ratio no anualizado → incorrecto para períodos ≠ 1 año (performance.py)
- [x] Fix HIGH: `np.random.seed(42)` global corrompía randomness de Monte Carlo (historical_data.py)
- [x] Fix HIGH: `logging.disable(CRITICAL)` permanente → nunca se rehabilitaba (main.py 6 funciones)
- [x] Fix HIGH: Circuit breaker accesado via `_private` attr → nueva property pública (risk_manager.py)
- [x] Fix MEDIUM: ISO year vs Gregorian year mismatch cerca de fin de año (storage_manager.py)
- [x] Fix MEDIUM: NaN propagation en regime_detector — todas comparaciones fallaban (regime_detector.py)
- [x] Fix MEDIUM: `fee_bps` usaba average fee → ahora maker fee para MM (config/settings.py)
- [x] Fix MEDIUM: Paper sim slippage sin book_depth/hawkes/atr → unrealistically low (paper_simulator.py)
- [x] Fix MEDIUM: Inventory sign_change_time usaba `or` con timestamp=0.0 falsy (microstructure.py)
- [x] Fix MEDIUM: Drawdown comparación inconsistente `>` vs `>=` (main.py)
- [x] Fix MEDIUM: `utcfromtimestamp` deprecated Python 3.12+ (quant_models.py)
- [x] Fix MEDIUM: Optimizer `np.random.seed(42)` global → local RNG (optimizer.py)
- [x] Cleanup: 6 unused imports removidos (mean_reversion, trend_following, websocket_client, microprice, risk_manager)
- [x] Tests: 153/153 pasaron post-audit (21 core + 15 strategy + 52 bug fixes + 34 execution + 31 self-audit)

- [x] Kyle Lambda: KyleLambdaEstimator — rolling Cov(ΔP,Q)/Var(Q) incremental, EMA smoothing, outlier clipping
- [x] Kyle Lambda: Adverse Selection Measurement — mark-to-market fills después de T+5min
- [x] Kyle Lambda: Integrado en MicrostructureSnapshot + MicrostructureEngine (on_trade con is_buy)
- [x] Kyle Lambda: A-S Engine gamma escala con impact_stress (lambda alto → spreads más anchos)
- [x] Kyle Lambda: Smart Router penaliza market orders con permanent impact (sqrt model)
- [x] Kyle Lambda: Risk Manager impact_stress — bloquea si stress>=1.5, reduce sizing si >0.5
- [x] Kyle Lambda: Slippage model advanced — componente 8: permanent_impact = lambda * sqrt(size/depth)
- [x] Kyle Lambda: Paper simulator aplica permanent impact component
- [x] Kyle Lambda: MM signals incluyen kyle_lambda en metadata
- [x] Kyle Lambda: main.py inyecta is_buy en on_trade + kyle_lambda_bps en signal metadata
- [x] Kyle Lambda: register_fill en live fills para adverse selection tracking
- [x] Kyle Lambda: Config — kyle_lambda_window, ema_span, adverse_selection_horizon, impact_stress_threshold
- [x] Tests: 153/153 regression + 10 unit + 6 integration = ALL PASSED

- [x] Audit profundo #11: 24 issues encontrados, 13 fixes aplicados sin romper (153/153 tests pass)
- [x] Fix CRITICO: generate_sample_data() aún usaba np.random.seed(42) global (backtester.py)
- [x] Fix CRITICO: Monte Carlo (RiskOfRuin + Bootstrap) usaba np.random.choice global (quant_models.py)
- [x] Fix HIGH: Kyle Lambda ddof mismatch — Var(Q) usaba ddof=0 pero Cov usaba ddof=1 (microstructure.py)
- [x] Fix HIGH: Kyle Lambda saltaba trades a mismo precio que son informativos para lambda (microstructure.py)
- [x] Fix HIGH: Hawkes _cached_excitation no inicializado en __init__ — usaba hasattr (microstructure.py)
- [x] Fix HIGH: Hawkes events_1m O(n) scan → O(early-exit) reversed iteration (microstructure.py)
- [x] Fix HIGH: RealisticBacktester exit no aplicaba slippage → PnL sobreestimado (backtester.py)
- [x] Fix HIGH: BacktestResult.summary() Calmar ratio no anualizado (backtester.py)
- [x] Fix HIGH: Market orders (price=None) → slippage tracking fallaba silenciosamente (order_engine.py)
- [x] Fix MEDIUM: impact_stress_threshold config value era dead code → ahora usado en risk_manager
- [x] Fix MEDIUM: Kyle Lambda adverse_selection del deque[i] O(n) → popleft O(1) (microstructure.py)
- [x] Fix MEDIUM: CovarianceTracker ddof=0 → ddof=1 (quant_models.py)
- [x] Fix CRITICO: _positions live mode broadcast a TODOS los strategies → ahora por symbol aggregado
- [x] Tests: 153/153 post-audit = ALL PASSED

- [x] Fix HIGH: asyncio.gather zombie state → task supervisor con auto-restart de tasks no-críticos (main.py)
- [x] Fix MEDIUM: force_update() era no-op → ahora estima vol intra-día con return parcial (quant_models.py)
- [x] Fix MEDIUM: Kyle Lambda no se actualizaba en on_bar → ahora usa BVC direction desde OHLC (microstructure.py)
- [x] Fix MEDIUM: Batch MM orders no tracked → procesa response y guarda order IDs (order_engine.py)
- [x] Fix HIGH: Dashboard logging.disable permanente en admin_panel → re-enable con try/finally (4_admin_panel.py)
- [x] Tests: 153/153 post-fixes = ALL PASSED + supervisor test + on_bar lambda test

- [x] PDF Docs: actualizado generate_docs_pdf.py con Kyle Lambda, Adverse Selection, Impact Stress, Task Supervisor, 8-component slippage

- [x] Generador de PDF simplificado (scripts/generate_simple_pdf.py) - Guia no tecnica con analogias

- [x] Telegram Bot: TelegramNotifier con cola async, rate limiting, batching de señales
- [x] Telegram Bot: NullNotifier no-op cuando no hay token (zero overhead)
- [x] Telegram Bot: Notifica startup/shutdown, trades, señales, régimen, riesgo, errores, portfolio
- [x] Telegram Bot: Integrado en BotStrike (main.py) — todos los eventos del trading loop
- [x] Telegram Bot: Integrado en StrikeDataCollector — status cada 5min, start/stop
- [x] Telegram Bot: Config via env vars TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
- [x] Tests: 157/158 post-integración (1 pre-existente, 0 nuevos fallos)

- [x] Fix collector: archivos parquet corruptos por reinicio forzado de PC (orderbook 2026-03-29)
- [x] Fix collector: escritura atómica a disco (tempfile + os.replace) — previene corrupción por crash
- [x] Fix collector: limpieza de .tmp huérfanos al iniciar
- [x] Fix collector: stop() protegido con try/except por cada flush — no se pierden datos si un flush falla
- [x] Fix collector: trades se particionan por fecha real del trade (no por "hoy") — elimina bleed-over
- [x] Fix collector: orderbook descarta depth updates con asks/bids vacíos o crossed book
- [x] Limpieza de datos existentes: reparticionados trades, eliminados 7270 duplicados, 5840 filas inválidas de OB
- [x] BinanceDownloader: descarga de klines 1m + aggTrades desde API pública (sin key)
- [x] BinanceDownloader: paginación por aggTrade ID (rápido), resume incremental, rate limiting
- [x] BinanceDownloader: mapeo automático de símbolos (BTC-USD → BTCUSDT)
- [x] BinanceDownloader: integrado en CLI (--download-binance --kline-days --trade-days)
- [x] Datos descargados: 90d klines (129K velas/sym, 100% cobertura) + 7d trades (13.5M total)
- [x] BacktestLiveDisplay: visualización en tiempo real con rich (progreso, equity, trades, microestructura)
- [x] Live display integrado en --backtest-realistic y --backtest-real

- [x] Audit profundo #12: 11 fixes aplicados sin romper (52+21+15+31 tests pass)
- [x] Fix CRITICAL: Kelly payoff_ratio div-by-zero guard (quant_models.py)
- [x] Fix CRITICAL: VPIN BVC buy_pct clamped to [0,1] — data gaps ya no rompen VPIN (microstructure.py)
- [x] Fix CRITICAL: Sigmoid overflow en portfolio_manager — exponent clamped [-500,500]
- [x] Fix HIGH: Correlation stress div-by-zero cuando threshold=1.0 (quant_models.py)
- [x] Fix HIGH: Inverse vol weighting floor 1e-6 — evita explosion numérica (quant_models.py)
- [x] Fix HIGH: start_idx usa tf_ema_slow*3 para convergencia real de EMAs (backtester.py)
- [x] Fix HIGH: kyle_lambda null guard en market_making metadata
- [x] Fix MEDIUM: funding_rate_block div-by-zero guard (risk_manager.py)
- [x] Cleanup: slippage import movido al top de backtester.py (4 imports en hot loop eliminados)
- [x] Cleanup: import math/copy movidos al top de regime_detector.py y microstructure.py

- [x] Rediseño Mean Reversion: RSI extremos + Bollinger Band + OBI confirmación (reemplaza z-score)
- [x] Rediseño Trend Following: Breakout N-bar + ADX + DI + Hawkes (reemplaza EMA crossover)
- [x] TF desactivada temporalmente: breakout 1m genera 100% falsos positivos
- [x] Relajar should_filter_mr: OR→AND, VPIN 0.6→0.85, Hawkes 3.0→4.0
- [x] Relajar risk_score threshold: 0.3→0.5, factor 0.5→0.3
- [x] Relajar MM pausa: solo cuando VPIN≥0.9 AND Hawkes≥4.0x
- [x] OBI pass-through en backtester (ambos backtesters)
- [x] Nuevos indicadores: DI+/DI-, high_20/low_20 breakout levels
- [x] Capital ajustado a $300 real (posiciones max: BTC=$1K, ETH=$750, ADA=$250)
- [x] Allocación: MR=60%, TF=0%, MM=40%
- [x] Resultado: sistema RENTABLE (+$0.90/7d = +0.3% semanal con $300, MR 100% WR)
- [x] Tests: 52+21+15 = 88/88 pasando

- [x] ML Signal Filter: LightGBM classifier entrenado con 32 trades y 14 features de micro/indicadores
- [x] Features enriquecidos en backtester: VPIN, Hawkes, risk_score, OBI, RSI, zscore, ATR etc. en metadata de trades
- [x] entry_metadata propagado desde Signal → BacktestPosition → trade_dict
- [x] MR OBI relajado: obi_delta >= -0.05 (permite OBI neutral)
- [x] Capital ajustado: $300 con posiciones BTC=$1K, ETH=$750, ADA=$250, MM order=$15

- [x] ML Signal Filter integrado en backtester (filtra señales con LightGBM cuando 50+ trades)
- [x] Multi-timeframe: barras 5m/15m/1h generadas en backtester para TF
- [x] TF probada en 5m, 15m, 1h — breakout pierde en todos los timeframes (0% WR)
- [x] TF desactivada definitivamente — requiere cambio fundamental de estrategia
- [x] 81/81 archivos compilan, 88/88 tests pasan
- [x] Backtest 90d final: -$5.07 (-1.7%) sobre $300, drawdown max 1.5%, 32 trades

- [x] Order Flow Momentum strategy creada (order_flow_momentum.py) — scalp basado en OBI+Hawkes+microprice
- [x] StrategyType.ORDER_FLOW_MOMENTUM añadido al enum
- [x] OFM registrada en ambos backtesters + REGIME_WEIGHTS actualizado
- [x] MM desactivada (no rentable con $300)
- [x] TF desactivada (breakout pierde en todos los timeframes)
- [x] OFM no opera en backtest (necesita orderbook real, no simulado) — se activa en live/paper
- [x] 82 archivos compilan, 93 tests pasan (57+21+15)

## Desktop App — Phase 1: Foundation
- [x] Python Bridge Server: FastAPI + WebSocket bridge (server/bridge.py, server/serializers.py)
- [x] Tauri v2 + React + TypeScript project init (desktop/)
- [x] Tailwind CSS v4 + custom cyberpunk design system (colors, glass, neon glow)
- [x] Root layout: collapsible sidebar (10 nav items) + top status bar (price, equity, PnL, regime, mode)
- [x] Zustand stores: market, trading, micro, risk, system
- [x] WebSocket client with auto-reconnect + channel routing to stores
- [x] REST API client (api.ts)
- [x] Shared components: GlassPanel, AnimatedNumber, MetricCard, PulsingDot
- [x] Dashboard page: portfolio value, key metrics, positions, signals, microstructure
- [x] Live Trading page: TradingView Lightweight Charts, orderbook, positions, signal feed
- [x] Performance page: metrics grid, equity curve placeholder, trade history table
- [x] Order Flow page: VPIN gauge, Hawkes intensity, Kyle Lambda, A-S spread, risk score
- [x] Strategy Manager page: strategy cards with allocation, status, descriptions
- [x] Risk Monitor page: circuit breaker, drawdown gauge, risk metrics
- [x] Backtesting Lab, Market Data, Settings, System Monitor — placeholder pages
- [x] TypeScript compiles with zero errors, Vite build successful
- [ ] Tauri native build (.msi installer)
- [ ] E2E: bridge server + Tauri app live data verification

## Desktop App — Phase 2: Core Screens (pending)
- [ ] All 5 WS channels fully wired with throttled broadcast
- [ ] All REST endpoints with real data
- [ ] Equity curve chart (TradingView area series)
- [ ] Backtest execution via REST
- [ ] Settings read/write via REST
- [ ] Strategy parameter editing UI

## Desktop App — Phase 3: Visual Polish (pending)
- [ ] Framer Motion page transitions + card mount animations
- [ ] Custom D3 viz: VPIN radial gauge, Hawkes sparkline, depth chart
- [ ] Price tick flash animations
- [ ] Resizable panel layout (Live Trading page)

## Desktop App — Phase 4: Packaging (pending)
- [ ] PyInstaller bundle of Python backend
- [ ] Tauri sidecar configuration
- [ ] MSI installer + auto-update

- [x] Defensive fixes batch: 11 surgical guards applied across 9 files (no logic/parameter changes)
- [x] Fix: try/except in WS callbacks on_market_trade + on_order_update (main.py)
- [x] Fix: null safety for micro.vpin — guard micro.vpin before accessing .vpin (main.py, 3 locations)
- [x] Fix: bare except:pass → logger.debug in 4 bridge broadcast loops (server/bridge.py)
- [x] Fix: print() → logger.warning + added structlog import (server/bridge.py)
- [x] Cleanup: removed unused import traceback (notifications/telegram.py)
- [x] Fix: guard empty bids_sorted/asks_sorted after sort (core/microprice.py)
- [x] Fix: division by zero guard atr_notional <= 0 (execution/slippage.py)
- [x] Fix: daily_eq zero guard before division in Sharpe/Sortino calc (backtesting/backtester.py, 2 locations)
- [x] Fix: guard empty/tiny DataFrame at start of detect() (core/regime_detector.py)
- [x] Fix: NaN guard in pnl_pct using pd.isna() (strategies/order_flow_momentum.py)
- [x] Fix: guard non-monotonic timestamps (dt<=0) in HawkesEstimator.on_event (core/microstructure.py)

## Pendiente / Mejoras futuras
- [x] ~~Alertas por Telegram/Discord~~ (Telegram implementado)
- [x] ~~Multi-exchange support~~ (Binance data downloader implementado, trading pendiente)
- [ ] Binance trading client (BinanceClient para ejecución de órdenes en Binance)
- [ ] Bayesian optimization (reemplazar grid search)
- [ ] Calibrar slippage model con datos reales de Strike (cuando haya 30+ dias recolectados)
- [ ] Sharpe ratio: incluir dias sin trades como retorno 0 (sparse calendar fix)
- [ ] HMM regime transition model (probabilidades de cambio de regimen)
- [ ] Execution analytics cross-venue (comparar fills con Binance/Bybit via API publica)
- [ ] Warm-start backtester con posiciones abiertas persistentes
