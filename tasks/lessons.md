# BotStrike — Lessons Learned

## Data Collection & Storage
- Los archivos Parquet se corrompen si el PC se apaga durante una escritura. Solución: escritura atómica (tempfile + os.replace, que es atómico en NTFS/ext4).
- Al cargar trades históricos via REST (initial load), NO meter todos en el archivo de "hoy". Particionar por la fecha real del timestamp del trade.
- Depth updates parciales (bids sin asks, o viceversa) generan filas con best_ask=0 que corrompen análisis. Filtrar ambos lados presentes.
- Strike Finance tiene liquidez extremadamente baja (~556 trades/día BTC vs ~480K en Binance). Los datos de Strike no sirven para backtesting serio — usar Binance para entrenar y Strike para ejecutar.
- Binance aggTrades: paginar por fromId es mucho más rápido que por startTime+endTime.

## Strategy Design (1m Crypto)
- Z-score mean reversion NO funciona en BTC 1m — el precio no es estacionario en ese timeframe.
- EMA crossover (12/26) en 1m es demasiado lento — el movimiento ya pasó cuando cruzan.
- RSI extremos (<25/>75) + Bollinger Band + OBI SÍ funciona para mean reversion en 1m crypto — produce 100% WR en backtest.
- Breakout de 20 barras en 1m genera 100% falsos positivos en crypto — necesita ML o timeframe superior.
- VPIN toxic threshold 0.6 es demasiado bajo para datos reales de Binance — 0.8 es más apropiado.
- should_filter_mr con lógica OR bloquea 99.9% de señales. Cambiar a AND con thresholds más altos.
- Con $300 de capital, position sizes son minúsculos. Leverage alto (10-20x) es necesario para que los trades tengan impacto.

## Strike Finance API
- Market data usa base `/price`, endpoints autenticados usan root `/`.
- WebSocket puede enviar múltiples JSON en un solo frame separados por `\n`.
- Auth usa Ed25519 (nacl), NO SSH keys. El body hash es SHA-256 incluso para GETs (hash de string vacío).
- Timestamp del auth debe estar dentro de ±3 minutos del servidor.
- Rate limit details en `/v2/exchangeInfo`, no en página separada.

## Defensive Coding
- Bare `except: pass` in async loops silently swallows real bugs. Always at least `logger.debug()` so issues surface in dev logs.
- Chained attribute access like `micro.vpin.vpin` needs null checks at each level — `micro` can be non-None while `micro.vpin` is None.
- Hawkes timestamps can arrive non-monotonically (WS reconnect, clock skew). Guard `dt <= 0` to avoid negative exponentials.
- Division by zero in equity curves happens when equity drops to exactly 0 (e.g., liquidation in backtest). `np.where(eq == 0, 1e-10, eq)` is safer than checking afterwards.
- `position.pnl_pct` can be NaN from division by zero in position tracking — always guard with `pd.isna()` before using in comparisons.

## Arquitectura
- Separar completamente market data de estrategias permite cambiar exchange sin tocar lógica.
- El régimen detector con suavizado (requiere 2 detecciones consecutivas) evita whipsaws costosos.
- Thresholds adaptativos por activo son esenciales: BTC, ETH y ADA tienen distribuciones de volatilidad muy diferentes.

## Estrategias
- Mean Reversion: el Z-score threshold debe ajustarse por volatilidad relativa del activo.
- Market Making Avellaneda-Stoikov: el parámetro gamma (aversión al riesgo) domina el comportamiento — valores bajos = más agresivo, valores altos = spreads amplios.
- Trend Following: filtrar por volumen (vol_ratio > 1.2) reduce significativamente falsas señales.
- El RSI como filtro secundario en Mean Reversion (RSI < 35 para long, > 65 para short) mejora win rate.

## Dashboard
- Streamlit multi-page: usar carpeta `pages/` con prefijos numéricos para controlar el orden de navegación.
- structlog imprime a stdout por defecto, lo que contamina el output del backtester — redirigir a stderr o silenciar con ReturnLoggerFactory durante backtests.
- Para backtests interactivos en dashboard, silenciar logging es obligatorio: `logging.disable(CRITICAL)` + `structlog.configure(logger_factory=ReturnLoggerFactory())`.
- DashboardState como capa de datos compartida permite que los 3 dashboards lean métricas sin duplicar lógica.
- Plotly con `paper_bgcolor/plot_bgcolor="rgba(0,0,0,0)"` se integra limpiamente con el tema oscuro de Streamlit.

## Microestructura
- VPIN con Bulk Volume Classification funciona mejor por barra (backtesting) usando (close-low)/(high-low) como buy_pct.
- El bucket_size de VPIN debe ajustarse por activo: BTC necesita ~$50k/bucket, ADA ~$500.
- Hawkes: alpha debe ser < beta para estabilidad del proceso (alpha/beta < 1 → subcrítico).
- Hawkes adaptativo: mu se recalcula como events_in_window/window_sec para adaptarse al nivel de actividad del activo.
- A-S mejorado: usar tanh(inventory_ratio*2) para el skew produce un ajuste suave no-lineal que evita saltos bruscos.
- La clave de integración sin romper nada: pasar `micro` como **kwarg a generate_signals, y como Optional param a validate_signal. Así el backtester viejo sigue funcionando sin micro.
- Market Making win rate mejoró de 0.3% a ~29% al reemplazar el A-S básico por el motor mejorado con spread bounds realistas.

## Backtester Realista
- La clave de fidelidad: alimentar microestructura tick-by-tick ANTES de procesar la barra — simula el flujo live donde trades llegan continuamente.
- HistoricalDataLoader.get_bars_with_trades() es el puente: devuelve (barra, ticks_de_esa_barra) para que el backtester procese ticks → close_bar → indicadores → estrategias.
- Para datos OHLCV sin ticks, se generan trades sintéticos interpolados (O→H→L→C) para alimentar VPIN/Hawkes.
- El RealisticBacktester usa PortfolioManager real, no capital simplificado — esto produce asignaciones dinámicas por régimen que cambian durante el backtest.
- Market Making con fees de maker (0.02%) en backtest realista vs taker (0.05%) en el básico mejora significativamente los resultados de MM.
- JSONL de backtest realista contiene los mismos tipos que producción: signal, trade, microstructure, regime_change, portfolio_snapshot, allocation — el dashboard los lee identico.
- **BUG CRÍTICO ARREGLADO**: El backtester NO monitoreaba SL/TP en barras posteriores a la entrada. Las posiciones se mantenían abiertas hasta EOD. Arreglado añadiendo check de SL/TP en cada barra del loop principal.
- **BUG CRÍTICO ARREGLADO**: Intra-bar SL/TP check causaba look-ahead bias (abrir y cerrar en la misma barra usando high/low de esa barra). Removido — SL/TP solo se checkea a partir de la barra siguiente.
- **BUG ARREGLADO**: Slippage model retornaba slippage en USD basado en precio ($10+ en trade de $5). Añadido cap de 1% del notional del trade.
- **BUG ARREGLADO**: ATR/ADX usaban EWM(span=period) en vez de Wilder's smoothing (span=2*period-1). ATR era 15-30% diferente del standard.
- **LECCIÓN**: MR strategy recibe 1m bars del backtester pero detecta divergencias en 15m. El ATR de 1m es ~4x menor que 15m ATR. Debe escalar ATR por sqrt(bar_ratio) para SL/TP correctos.
- **LECCIÓN**: OFM no produce señales en backtest porque el orderbook simulado (1 bid/1 ask) no genera OBI/depth data significativa. OFM solo funciona en live/paper con datos reales.
- **LECCIÓN**: Con solo 5 trades MR en 90 días, el resultado no es estadísticamente significativo. Necesita más señales o datos más largos para validar.
- Para datos de Strike: exportar trades con timestamp en ms, columnas time/price/qty. El loader auto-detecta formato (ms/s/us) por magnitud del timestamp.

## Data Pipeline (Strike Finance)
- **SIEMPRE recolectar de MAINNET**: testnet no tiene actividad real, los datos no sirven para backtesting.
  - Testnet: WS no produce datos, stats endpoints 404, ETH-USD vacio.
  - Mainnet: 26-33 depth updates/s, klines reales, todos los simbolos activos.
  - El collector fuerza URLs de mainnet independientemente de --testnet.
- Strike SI tiene endpoint REST de klines: /v2/klines?symbol=X&interval=1m&limit=N.
- /v2/trades solo retorna los ultimos 1000. Para historial completo: WS trade channel + REST polling + flush a Parquet diario.
- /v1/stats/coin/history/* endpoints 404 tanto en testnet como mainnet — no implementados en la API.
- **Arquitectura dual WS+REST**: WS es fuente primaria tick-by-tick, REST cada 10-15s captura gaps.
  - WS depth: ~30 updates/s por simbolo (riqueza de microestructura).
  - WS trades: solo cuando hay matching real (intermitente en exchange nuevo).
  - REST trades/15s + REST orderbook/10s: red de seguridad, dedup por trade_id.
- Flush a disco frecuente (30s trades/ob, 60s klines) minimiza perdida de datos si crashea.
- Parquet por dia (trades, orderbook) + incremental (klines) mantiene archivos manejables.
- Dedup por trade_id (preferido) o timestamp+price+qty al hacer append a Parquet.
- load_from_collector() normaliza timestamps de ms a s automaticamente.
- Unicode arrows causan UnicodeEncodeError en terminal Windows cp1252 — usar ASCII en prints de CLI.
- El campo "isBuyerMaker" en REST trades (no "m") determina el side: isBuyerMaker=true -> SELL, false -> BUY.
- El host mainnet WS es wss://api.strikefinance.org/ws/price (no api-v2 que no resuelve DNS).

## Audit de Bugs (sesion 2026-03-25)
- VPIN CDF: `[1.0 for v in hist if v <= vpin]` siempre retorna lista de 1.0s. Fix: contar proporcion correctamente.
- A-S spread: el log term `(2/gamma)*ln(1+gamma/kappa)` domina con valores tipicos, produciendo spreads de ~12900 bps. Fix: escalar kappa por mid_price.
- `mid_price` en OrderBook usaba truthiness (`if self.best_bid and self.best_ask`) que falla si bid/ask es 0.0. Fix: `is not None`.
- `refresh_mm_orders` usaba `cancel_all_orders(symbol)` que mataba SL/TP de otras estrategias. Fix: cancelar individualmente solo ordenes MM.
- Buffer race condition en collector: entre `DataFrame(buf)` y `buf.clear()`, trades nuevos se perdian. Fix: swap atomico `self._buffers[sym] = []` antes de procesar.
- Signal handler en main.py usaba `loop.create_task()` que no es thread-safe desde signal handler. Fix: `loop.call_soon_threadsafe()`.
- `_trailing_stops` en TrendFollowing nunca se limpiaba cuando posicion se cerraba externamente, causando exits espurios en posiciones nuevas.
- Market Making generaba senales con price=0 cuando sigma=0 (returns constantes). Fix: guard sigma=0 y validar A-S result.
- `wmic` deprecado en Windows 11 para deteccion de procesos. Fix: usar PowerShell `Get-CimInstance`.
- `.bat` del collector redirigía todo output a log (`>> log 2>&1`), dejando la ventana negra. Fix: output directo + `python -u` unbuffered.
- Listas sin limite (`_recent_trades`, `_equity_curve`, `_history`) causan memory leak en ejecucion prolongada. Fix: limitar con trim periodico.

## Trade Database & Analytics (sesion 2026-03-26)
- SQLite con WAL mode + NORMAL synchronous es el balance optimo para este volumen: escrituras rapidas sin corromper datos.
- El patron Adapter/Observer permite inyectar persistencia sin modificar interfaces existentes: el adapter escucha callbacks, no los reemplaza.
- Para backtesting, batch inserts (100 trades por transaccion) son ~50x mas rapidos que inserts individuales.
- INSERT OR REPLACE evita errores de duplicados al re-importar backtests sin complicar la logica.
- PerformanceAnalyzer debe ser stateless: recibe trades, retorna report. Permite reutilizarlo desde CLI, dashboard, y backtester sin conflictos de estado.
- El analisis cruzado estrategia/regimen (cross_strategy_regime) es la metrica mas valiosa: muestra que MR funciona en RANGING pero pierde en TRENDING.
- Sortino > Sharpe para trading: penalizar solo volatilidad negativa es mas representativo.
- Compactacion de Parquet con zstd reduce ~40-60% vs snappy default, sin costo de lectura significativo.
- Nunca borrar archivos originales sin verificar que el compactado existe y tiene el mismo row count.
- DataCatalog.refresh() escanea disco bajo demanda (no daemon). Para datasets < 10GB el escaneo toma < 1s.
- La clave de integracion sin romper nada: imports nuevos en main.py + parametros opcionales con default=None en backtester.

## Audit profundo (sesion 2026-03-26 #2)
- **close_jsonl recursion infinita**: `close_jsonl()` se llamaba a si misma en vez de `jsonl_file.close()`. No crasheaba porque `_jsonl_open=False` antes de la recursion, pero el archivo NUNCA se cerraba -> file handle leak en cada backtest. Fix: llamar `jsonl_file.close()`.
- **self.base_mu no existe en HawkesEstimator**: Las lineas de fallback cuando `_adaptive_mu == 0` referenciaban `self.base_mu` (no existe), deberia ser `self.mu`. Crashea en edge case al inicio si no hay eventos. Fix: reemplazar por `self.mu`.
- **end_session guardaba initial_equity equivocado**: `self._current_equity` se usaba como `initial_equity` en el SessionRecord final, pero ya habia sido actualizado durante la sesion. Fix: almacenar `_initial_equity` al inicio y usarlo en end_session.
- **Hawkes nunca se actualizaba en MicrostructureEngine.on_bar()**: En backtests bar-by-bar sin ticks, solo VPIN se actualizaba. Hawkes quedaba en intensidad=0 todo el backtest, haciendo que los filtros de microestructura fueran parcialmente inoperantes. Fix: registrar un evento Hawkes por barra en on_bar().
- **Variable muerta `_micro_adjusted_size`**: Asignada en risk_manager.py pero nunca leida. Eliminada.
- Patron de bugs: funciones auxiliares internas (close_jsonl, fallback paths) son las mas propensas a bugs porque se prueban menos. Revisar especialmente codigo de limpieza y edge cases.

## Audit profundo #3 (sesion 2026-03-26 #3)
- **ISO week vs strftime %W**: `strftime("%Y-W%W")` usa semanas lunes-base (W00 posible), `isocalendar()[1]` usa ISO 8601 (W01-W53). En enero 1 divergen: W00 vs W01. Fix: usar isocalendar() para ambos.
- **regime_history offset**: El backtester empieza regime_history en start_idx (~100), pero trades guardan bar index absoluto. `regime_history[bar_idx]` daba el regimen de la barra bar_idx+start_idx, no bar_idx. Resultado: 100% de trades tenian el regimen equivocado o vacio. Fix: calcular offset = max_bar - len(history) + 1 y ajustar.
- **cumulative PnL ffill**: `aligned.ffill() if mask.all()` solo aplicaba forward-fill cuando TODAS las trades eran de una estrategia (nunca en multi-strategy). El grafico del dashboard mostraba gaps a 0 entre trades de cada estrategia. Fix: siempre aplicar ffill con NaN como sentinel.
- Patron: bugs de indexacion/offset son invisibles en tests unitarios que solo verifican "no crash". Se necesitan tests de integridad de datos: "el regimen asignado es correcto", "la curva es continua".

## Audit profundo #4 (sesion 2026-03-26 #4)
- **close_jsonl vs jsonl_file.close()**: El path normal del backtester (linea 956) llamaba `jsonl_file.close()` directamente sin pasar por `close_jsonl()`, dejando `_jsonl_open=True`. El atexit handler intentaba re-cerrar el archivo. En CPython es no-op pero es inconsistente. Fix: usar `close_jsonl()` en todos los exit paths.
- **refresh_rate slider ignorado**: El slider de intervalo de auto-refresh en live_operations.py generaba un valor que nunca se usaba — `time.sleep(5)` estaba hardcodeado. El usuario movia el slider sin efecto. Fix: `time.sleep(refresh_rate)`.
- Patron: variables de UI (sliders, inputs) que se capturan pero nunca se consumen son bugs silenciosos. Buscar variables asignadas pero no referenciadas despues.

## Paper Trading (sesion 2026-03-26)
- La clave del paper trading es que los fills simulados fluyen por el MISMO pipeline que los fills reales: logger, metrics, portfolio_manager, risk_manager, trade_db. Asi no hay divergencia de comportamiento.
- PaperPosition trackea SL/TP internamente y los verifica en cada tick de precio via on_price_update(). Esto captura SL/TP que el dry-run anterior ignoraba completamente.
- Paper mode fuerza URLs de MAINNET (como el collector) porque testnet de Strike no tiene datos reales.
- El simulador no necesita WebSocket de usuario (no hay ordenes reales). Solo market WS para precios.
- Entry fills tienen pnl=0 (es una apertura). Solo exits/SL/TP generan PnL real. Esto es identico al exchange real.
- La session en trade_db se marca como source="paper" para distinguir de "live" y "backtest" en analytics.

## Audit Paper Trading (sesion 2026-03-26)
- **Positions invisibles para estrategias**: `self._positions` (dict del orquestador) nunca se actualiza en paper mode. Las estrategias veian `current_position=None` siempre, asi que MR nunca generaba exit signals y TF trailing stop nunca se activaba. Fix: leer de `paper_sim.get_position()` en vez de `self._positions`.
- **Fee double-counted**: Entry Trade.fee cobraba 1x fee + close() cobraba 2x fee = 3x total. En live el exchange cobra 1x por entry + 1x por exit = 2x. Fix: entry.fee=0, close() cobra ambos lados (identico al backtester).
- **cancel_all leak**: Cuando max_drawdown se excedia, el risk loop llamaba `execution_engine.cancel_all()` incluso en paper mode, enviando un DELETE real al exchange. Fix: skip si `self.paper`.
- Patron: cuando un componente nuevo (paper_sim) mantiene estado paralelo a uno existente (self._positions), HAY que verificar que todos los consumidores lean del correcto. El bug era invisible porque el paper_sim rechazaba duplicados silenciosamente.

## Priorities Implementation (sesion 2026-03-26)
- **MM loop dedicado**: MM no necesita recalcular indicadores/regimen en cada ciclo. Solo necesita snapshot fresco (mid_price del WS) + micro + ATR cacheado. Separar en _mm_loop a 500ms reduce latencia de quotes de 5s a 0.5s sin duplicar logica.
- **Slippage dinamico**: Slippage fijo de 2 bps era irreal. Modelo: `base * regime_mult + size_impact + hawkes_impact`. BREAKOUT=2x base, RANGING=0.8x. Size 50% del book depth = +100% slippage. Hawkes spike 4x = +150% slippage. Test confirma 9 valores unicos de slippage en 190 trades.
- **VPIN bucket analysis**: Simple extension del _analyze_by_field pattern. Agrupa trades por micro_vpin en 5 buckets de 0.2. Permite ver si el bot pierde mas dinero cuando VPIN es alto (deberia, si los filtros no son suficientes).
- **Stress test**: No necesita modulo nuevo complejo. Toma datos existentes + inyecta eventos in-place. Flash crash = caida acelerada + recovery parcial. Cascade = caida con volumen exponencial. El backtest existente procesa los datos estresados sin cambios.

## Audit profundo #7 — 40 fixes (sesion 2026-03-26)
- **CRITICAL: micro=None crash** — `microstructure.get_snapshot()` retorna None antes de tener datos, pero el log de microestructura accedia `.vpin.vpin` sin guard. Crasheaba el strategy loop en los primeros 5 segundos. Fix: wrap en `if micro:`.
- **CRITICAL: --testnet always True** — `argparse default=True` + `store_true` = nunca se puede usar mainnet via CLI. Fix: default=False + --no-testnet flag.
- **CRITICAL: equity double-count** — `_process_paper_fill` calculaba `equity_before` DESPUES de actualizar equity, usando `current_equity - pnl`. Con multiples fills en el mismo tick (SL/TP cascade), los pares equity_before/after se solapaban. Fix: capturar equity_before ANTES de update.
- **HIGH: MR doubling** — Mean Reversion generaba entry signals incluso con posicion abierta, duplicando el tamaño. Fix: guard `if current_position is None`.
- **HIGH: negative MM strength** — Cuando inventory_ratio > 1 (posicion excede max), `0.5 * (1 - ratio)` daba negativo. Fix: `max(0.01, ...)`.
- **HIGH: Hawkes self-defeating** — adaptive_mu absorbia spikes, subiendo el threshold justo cuando debia detectar anomalias. Fix: usar mu original (fijo) para spike threshold.
- **HIGH: ADX bearish bias** — minus_dm se comparaba contra plus_dm YA filtrado en vez del raw. Fix: usar variables _raw para comparaciones.
- **HIGH: paper PnL always zero** — `get_position()` usaba `entry_price` como mark_price, haciendo unrealized_pnl=0 siempre. Risk checks de exposure eran inoperantes. Fix: trackear `_last_prices` y usarlos.
- **HIGH: orphaned SL/TP** — Ordenes protectivas se colocaban sin verificar que la orden principal fue accepted. Fix: verificar status antes.
- **HIGH: filled orders leak** — on_order_update retornaba Trade antes de limpiar _active_orders, causando memory leak + cancel de ordenes ya filled. Fix: cleanup antes del return.
- **MEDIUM: WS reconnect race** — Ambas conexiones WS compartian `_reconnect_delay`. Una reconexion exitosa reseteaba el backoff de la otra. Fix: delays independientes.
- **MEDIUM: collector mutates shared Settings** — `_apply_mainnet_urls()` modificaba el Settings original. Si el collector corria junto al bot, este recibia URLs de mainnet. Fix: `copy.deepcopy(settings)`.
- **MEDIUM: ISO week year** — `date.year` vs `date.isocalendar()[0]` diverge en enero/diciembre. Fix: usar isocalendar para ambos.
- **MEDIUM: catalog load crash** — `DatasetInfo(**info)` fallaba si el JSON tenia campos extra de `to_dict()`. Fix: filtrar campos validos.
- Patron: los bugs mas peligrosos son los que funcionan "casi siempre" — overflow de inventory, edge cases de timing, NaN propagation. Tests de humo no los detectan.
- **Bar boundary tick leakage**: El tick que dispara `_close_bar` ya estaba en el buffer, contaminando la barra actual con datos del siguiente periodo. Fix: verificar ANTES de añadir, separar ticks por timestamp, guardar sobrantes para la nueva barra.
- **Walk-forward fake**: La docstring decia "optimiza en training" pero el codigo solo hacia backtest en test con params default. Sin optimizacion real, el walk-forward era solo un rolling-window backtest. Fix: correr ParameterOptimizer en train_df, aplicar best_params al test_df.
- **Sharpe per-trade inflado**: `mean(pnl)/std(pnl)*sqrt(252)` con 10 trades/dia infla Sharpe ~3x vs la realidad. Fix: agregar PnL a retornos diarios ANTES de annualizar. Afectaba backtester, performance analyzer, y MetricsCollector.
- **MM inventory sin exit**: Cuando el regimen cambia de RANGING a TRENDING, MM deja de generar quotes pero la posicion existente queda expuesta a riesgo direccional sin limite. Fix: `_unwind_mm_inventory()` genera market order de cierre cuando `should_activate()` retorna False.

## Recalibracion Microestructura (sesion 2026-03-26)
- **VPIN bucket_size era $5k para todos** — BTC llena un bucket en ~10 trades, random runs de 6-7 ticks crean falso imbalance. Normal market daba VPIN=0.67 (TOXICO). Fix: BTC=$50k (100+ trades/bucket), ETH=$10k, ADA=$500.
- **A-S spread siempre stuck en 7 bps (min floor)** — `scaled_kappa = kappa * mid_price = 75,000` hacia que `ln(1 + gamma/75000) ≈ 0`. La formula A-S clasica con kappa escalado produce spreads de 3 bps, siempre clamped al fee floor. Gamma via VPIN/Hawkes tenia ZERO efecto en quotes.
- **Solucion A-S**: Reemplazar con formula ATR-based: `spread = ATR_bps * gamma * kappa_factor`. ATR captura volatilidad real, gamma escala con micro, kappa comprime por liquidez. Rango: 7 bps (calm) a 100 bps (crisis).
- **Reservation price era $0.0000005 de ajuste** — `inv * gamma * sigma^2 * T` con sigma=0.01 produce micro-centavos. Fix: usar ATR en lugar de sigma^2: `mid - inv * ATR * gamma * T` produce $2.50-$7.50 de offset real.
- **Principio**: Siempre hacer calibration test numerico ANTES de confiar en una formula matematica. Las formulas academicas (A-S paper) asumen parametros en unidades especificas que no coinciden con como se alimentan en produccion.
- VPIN post-fix: Normal=0.33, Sweep=1.0 (separacion perfecta). A-S post-fix: 10bps(safe) → 30bps(VPIN) → 60bps(crisis).

## Functional Bug Fix Tests (sesion 2026-03-26)
- Circuit breaker uses strict `>` (not `>=`) for the 80% threshold. At exactly 80% of max_drawdown it does NOT trigger. The test must use a value slightly above to verify activation.
- When testing size reduction with `min()`, if capital is very low the signal may be rejected entirely by exposure checks before reaching the margin reduction logic. Test design must account for upstream rejection.
- `_track()` in TradeDBAdapter is safe against equity_peak=0 because the `if self._equity_peak > 0:` guard prevents the division. The equity_peak updates first if equity_after is positive.
- `end_session` uses `max(self._max_drawdown, max_drawdown)` only when `max_drawdown > 0`, so providing 0 keeps the internal value. This is intentional — 0 means "no external measurement".

## Audit profundo #8 (sesion 2026-03-26)
- **Paper simulator regime slippage siempre 1.0x**: `signal.metadata.get("regime", "")` nunca encontraba "regime" porque ninguna estrategia lo incluye en metadata. El paper trading aplicaba slippage uniforme sin importar si era BREAKOUT (debería ser 2x) o RANGING (0.8x). Fix: inyectar `regime.value` en `sig.metadata["regime"]` en `_process_symbol` y `_mm_loop` antes de pasar señales al paper_sim.
- **Multi-bar gap en MarketDataCollector**: `on_trade` solo cerraba UNA barra por llamada con un `if`. Si un trade llegaba después de 3+ intervalos de silencio, las barras intermedias se perdían y el DataFrame tenía gaps. Fix: cambiar `if` por `while` loop que cierra todas las barras pendientes.
- **MM signals sin risk checks en _mm_loop**: Market Making generaba y ejecutaba órdenes sin pasar por `risk_manager.validate_signal`. Esto significaba que MM ignoraba circuit breaker, max drawdown, y exposure limits. Fix: agregar check de `_circuit_breaker_active` y `current_drawdown_pct >= max_drawdown_pct` antes de ejecutar MM signals.
- **RealisticBacktester no reconocía mm_unwind**: La lista de exit actions solo incluía "exit_mean_reversion" y "trailing_stop_hit". Si el backtest generara un mm_unwind, no se cerraría la posición. Fix: agregar "mm_unwind" a la tupla de exit actions.
- **RealisticBacktester no pasaba funding_rate a validate_signal**: En live, el risk manager filtra entradas contra funding extremo. En backtest realista, este filtro estaba deshabilitado porque `funding_rate=` no se pasaba. Fix: agregar `funding_rate=funding_rate` al call.
- **MetricsCollector avg_win/avg_loss inconsistente**: Después de truncación del historial (>5000 trades), `avg_win` y `avg_loss` se calculaban de la ventana truncada pero `total_trades`, `total_pnl`, `win_rate` usaban contadores cumulativos. Resultado: estadísticas inconsistentes en ejecución prolongada. Fix: agregar `_cumulative_win_pnl`, `_cumulative_loss_pnl`, `_cumulative_loss_count` y usarlos para avg_win/avg_loss/profit_factor.
- **BacktestResult.summary() usaba float("inf")**: Para profit_factor sin losses, usaba `float("inf")` que rompe `json.dumps()`. PerformanceAnalyzer correctamente usaba 9999.99. Fix: alinear a 9999.99.
- **Imports muertos**: `import math` en market_making.py (no se usa, A-S está en microstructure.py), `Set` en order_engine.py, `Tuple` en regime_detector.py, `Iterator` en historical_data.py, `List+Tuple` en portfolio_manager.py. Eliminados.
- **Euler hardcodeado**: `2.718` en portfolio_manager.py en vez de `math.e`. Imprecisión mínima pero innecesaria.
- Patron: los bugs de "pipeline inconsistency" (paper vs live, backtest vs live) son los más difíciles de detectar porque cada componente funciona correctamente en aislamiento. La divergencia solo aparece cuando se traza el flujo completo end-to-end.

## Quant Review (sesion 2026-03-26)
- **RSI NaN cuando avg_loss=0**: Cuando todos los cambios de precio son positivos, avg_loss=0, rs=NaN, RSI=NaN en vez de 100. Fix: `rsi.fillna(100.0)`. Simétrico: avg_gain=0 ya producía RSI=0 correctamente.
- **Hawkes sin validacion de estabilidad**: No habia check de alpha < beta. En proceso de Hawkes, si alpha >= beta (branching ratio >= 1), el proceso es supercritico y la intensidad diverge a infinito. Config actual (0.5/2.0=0.25) es subcritico, pero si alguien modifica config podria romper el sistema silenciosamente. Fix: `ValueError` en `__init__` si alpha >= beta.
- **Maintenance margin 0.5% en liquidacion**: El default de 0.005 significa liquidacion cuando pierdes 99.5% del margen. En crypto perpetuals, maintenance margin tipico es 2-5%. Resultado: backtests subestimaban liquidaciones, posiciones sobrevivian movimientos extremos. Fix: cambiar a 0.02 (2%).
- **Gamma A-S sin cap**: Con VPIN=0.8 (3x) y Hawkes spike 5x (2.5x), gamma efectivo llegaba a 0.1 * 7.5 = 0.75. Esto producía spreads de 50-75 bps incluso en mercados normales-estresados. En A-S clasico, gamma > 0.3 es extremadamente defensivo. Fix: cap en 5x base (0.5 max), suficiente para crisis pero no absurdo.
- **Inventory skew del A-S es CORRECTO**: Analisis numerico confirma que `-inventory_skew` en ambos bid y ask desplaza toda la quote en la direccion correcta. LONG inventory → precios bajan → incentiva ventas. SHORT inventory → precios suben → incentiva compras. Patron estandar A-S.
- **MR threshold scaling es CORRECTO**: `threshold = base * (0.8 + 0.4 * vol_pct)`. Baja volatilidad = threshold bajo = mas senales. Alta volatilidad = threshold alto = filtra ruido. Esto es correcto para mean reversion: en mercados tranquilos, desviaciones menores son significativas.
- **Sharpe sparse calendar (limitacion conocida)**: El calculo de Sharpe solo incluye dias con trades, omitiendo dias sin actividad. Esto sobreestima Sharpe en escenarios donde el bot no opera todos los dias. No corregido: requiere tracking de fechas start/end que complica la interfaz. Documentado como limitacion.
- **Fee calculation en backtester es correcta**: `fee = (entry_notional + exit_notional) * fee_rate` cobra fees sobre notional real, no sobre margen. Esto coincide con como Strike Finance cobra (sobre valor nocional).
- Patron quant: siempre verificar formulas con numeros concretos antes de declararlas incorrectas. El agente automatizado declaro 3 formulas como "backwards" que en realidad eran correctas (inventory skew, MR threshold, fee calc). Los numeros no mienten.

## Tick Quality Guards (sesion 2026-03-26, inspirado en articulo HFT Polymarket)
- **Warmup period**: Los primeros 5s post-conexion WS producen ticks stale (snapshots cacheados del exchange). Ahora descartamos ticks durante WARMUP_SEC post-on_ws_connected(). Solo aplica en modo live/paper, no en backtest.
- **First tick skip**: Cada reconexion WS produce un primer tick por simbolo que es un orderbook snapshot cacheado, no un trade real. Se descarta automaticamente.
- **Stale tick guard**: Ticks con delta > 5% vs ultimo precio aceptado se rechazan (STALE_TICK_MAX_PCT=0.05). Protege contra gaps de datos que producen precios incorrectos.
- **Jitter EMA tracking**: Se mide el intervalo promedio entre ticks con EMA(alpha=0.1) para monitorear calidad de conexion. No es un filtro activo, solo diagnostico.
- **Diseño clave**: Los guards solo se activan DESPUES de on_ws_connected(). Cuando ws_connect_time=0 (backtest, test, REST init), todos los ticks se aceptan sin filtrar. Esto evita romper tests existentes y el backtester.
- **Metricas**: get_tick_quality_stats() retorna accepted/rejected counts para monitoreo. Se loguea cada 60s en _metrics_loop.
- No implementamos multi-WS spawn (Layer 2 del articulo) porque Strike Finance tiene un solo endpoint y spawning 300 conexiones a un exchange pequeno causaria ban inmediato. El multi-WS es para CLOB de alta liquidez como Polymarket/Binance.
- No implementamos timing offset (Layer 5) ni anti-jitter reaper (Layer 6) porque solo tenemos 1 conexion WS. Esas capas son para gestionar POOLS de conexiones redundantes.

## Quant Models Avanzados (2026-03-26)
- **Volatility Targeting**: Escalar posiciones inversamente a la vol realizada es la tecnica mas efectiva para estabilizar Sharpe. Implementado como scalar global que se aplica ANTES del risk manager en validate_signal. Lookback de 20 dias, clamped 0.5-2.0x.
- **Kelly Criterion**: Half-Kelly con floor/ceiling es el estandar de la industria. El Kelly completo es demasiado agresivo y asume estimaciones perfectas de win rate y payoff ratio. Implementado por estrategia — cada StrategyType tiene su propio historial de trades.
- **Risk of Ruin**: Debe calcularse ANTES de poner dinero real. Formula analitica ((1-edge)/(1+edge))^N es rapida pero optimista. Bootstrap empirico es mas conservador. Implementamos ambos: analitico cada trade, empirico bajo demanda.
- **Order Book Imbalance**: El DELTA de imbalance es mas predictivo que el nivel absoluto. Implementamos weighted imbalance con exponential decay (niveles cercanos al mid pesan mas). Integrado como confirmacion (no trigger primario) en MR/TF, y como spread skew en MM.
- **Risk Parity**: Con solo 3 activos, Markowitz es inestable. Inverse-vol weighting es mas robusto. Implementamos blend 70% regimen + 30% risk parity para no perder la logica de regimen que ya funciona.
- **Correlation Regime**: En crypto, las correlaciones saltan a ~1.0 durante crashes. Si no reduces exposicion, la "diversificacion" de 3 activos es una ilusion. Implementamos stress_factor que reduce automaticamente.
- **Slippage Tracking**: La debilidad mas peligrosa del sistema era no medir slippage real. Sin esa medicion, el backtester puede mentir sistematicamente. Ahora cada Trade guarda expected_price y actual_slippage_bps.
- **Inventory Half-Life**: El modelo A-S original no penaliza inventario viejo. Agregamos time-weighted penalty que escala el skew con la edad del inventario. Esto fuerza rebalanceo incluso cuando el mercado no se mueve.
- **Backward compatibility**: Todas las mejoras son aditivas — nuevos campos tienen defaults, nuevos kwargs son opcionales. Los tests existentes (52+21+15) siguen pasando sin modificacion.

## Execution Intelligence Layer (2026-03-26)
- **Microprice es fundamental**: Reemplazar mid_price con microprice cambia la calidad de TODAS las decisiones downstream: reservation price A-S, entry triggers, slippage estimation. Formula de Stoikov (2018): microprice = ask * bid_qty/(bid+ask) + bid * ask_qty/(bid+ask). Con bid_qty >> ask_qty, el microprice se mueve hacia el ask (precio justo sube).
- **Limit vs Market no es binario**: Es un problema de optimizacion de costos. costo_market = half_spread + impact + taker_fee. costo_limit = (1-P(fill)) * opportunity_cost - P(fill) * price_improvement + maker_fee. Con spread < 3bps siempre conviene market. Con spread > 10bps casi siempre limit. El rango intermedio es donde el modelo agrega valor.
- **Fill probability**: El factor dominante es la distancia al mid normalizada por el spread. Segundo factor: profundidad en el nivel (queue ahead). Tercer factor: volatilidad (ATR alto = mas prob de tocar). El modelo usa logistic function con estos 5 inputs.
- **Queue model**: Critico para Market Making. Si hay $50k delante en tu nivel de precio, y el mercado consume ~$500/sec en tu lado, tardas ~100 segundos en llegar al frente. Esto informa si vale la pena esperar o mejorar precio.
- **TWAP para ordenes grandes**: Cualquier orden > $10k deberia dividirse temporalmente. Market impact es concavo (sqrt), asi que 4 ordenes de $2.5k tienen menos impacto total que 1 de $10k.
- **Slippage avanzado con sqrt impact**: El modelo lineal sobreestima impact de ordenes pequenas y subestima impact de grandes. Almgren-Chriss muestra que impact ~ sqrt(size/ADV). Cambiamos a sqrt model + 6 componentes adicionales.
- **Trade intensity bidireccional**: El Hawkes original suma todos los trades. Separar buy vs sell intensidad es mucho mas informativo: te dice si el flujo tiene sesgo direccional, lo cual alimenta microprice y spread prediction.
- **Dependencias entre modulos**: Microprice -> todo. Fill prob -> smart router. Trade intensity -> microprice ajustado + fill prob. OBI -> microprice + strategies. Spread predictor -> smart router + MM timing. No se pueden implementar en cualquier orden.

## Deep Audit #9 (2026-03-26 anterior)
- **BUG CRITICO en risk_manager**: `min(adjusted_size, signal.size_usd)` en la linea final de validate_signal REVERTIA todas las reducciones de riesgo (RoR, vol targeting, corr stress, consecutive losses) cuando micro.risk_score > 0.3. El signal.size_usd ya habia sido modificado por los filtros previos, pero la intencion original era "no exceder el tamano micro-reducido". En la practica, si _adjust_position_size retornaba un tamano menor que signal.size_usd (que siempre ocurre), el min() era un no-op. Pero si retornaba algo mayor (que no ocurre en el codigo actual), seria correcto. Eliminado por ser redundante y confuso.
- **BUG en portfolio_manager**: Risk Parity weight scaling multiplicaba por len(rp.weights) para "desnormalizar", pero esto producía pesos incorrectos. Los RP weights ya estan normalizados (sum=1). Fix: calcular ratio vs weight neutro (1/N) y usar como multiplicador del base_weight.
- **BUG en order_engine**: ATR falsiness — `if atr_val and price > 0` trataba ATR=0.0001 como falsy (False en Python). Fix: `if atr_val is not None and atr_val > 0`.
- **BUG en market_making**: Division by zero cuando price=0 en max_inventory calculation. Fix: guard `if price <= 0: return signals` antes del calculo.
- **NaN en Monte Carlo**: drawdown calculation `(peak - equity) / peak` podia producir NaN cuando equity negativa. Fix: np.where con guard + np.nanmax.
- **Sigmoid fill probability verificada correcta**: El audit sospechaba inversion pero `1/(1+exp(1.5x-0.5))` SI decrece con la distancia: x=0→0.62, x=1→0.27, x=2→0.08. Falso positivo del audit.
- **Trade intensity `is` vs `==`**: `data.get("m", False) is False` usa identity check, no equality. Funciona en CPython (False is singleton), pero es fragil. Fix: `not data.get("m", False)`.
- **Unused imports limpiados**: VPINResult, HawkesResult, OrderBook, np, RoutingDecision, RiskParityResult, List, RiskOfRuinResult, VolTargetResult, KellyResult, SlippageStats, CorrelationRegimeResult, TradingConfig.
- **Regime detector dead code**: BREAKOUT retornaba lo mismo en ambas ramas de if/else. Simplificado a un solo return.

## Deep Audit #10 — El más exhaustivo (2026-03-26)
- **CRITICO: `_positions` nunca poblado en live mode**: `self._positions` era un dict vacío que nadie escribía en modo live. Estrategias siempre veían `current_pos=None`. MR no podía generar exits, TF no podía hacer trailing stops, MM no podía unwind. El bot abriría posiciones infinitamente sin cerrar ninguna. Fix: sync desde `get_positions()` en `_risk_monitor_loop`.
- **CRITICO: `asyncio.gather` destructor**: Un crash en cualquier task (ej. `_metrics_loop`) terminaba TODAS las tasks incluyendo trading. Fix: `return_exceptions=True` + log de crashes sin terminar.
- **CRITICO: Sin timeout en aiohttp**: Default de 300s. Si Strike Finance no responde, el bot entero se congela 5 minutos con posiciones abiertas. Fix: `ClientTimeout(total=15, connect=5, sock_read=10)`.
- **CRITICO: Z-score near-zero std**: `std.replace(0, np.nan)` solo protege contra exactamente 0. Un std de 1e-300 produce z-scores de 1e+300. Fix: `std.where(std > 1e-12, np.nan)`.
- **CRITICO: Vol targeting ddof=0**: Subestimaba volatilidad sistemáticamente con muestras pequeñas (5-20 valores). `vol_scalar` era muy alto → over-leverage en cada trade. Fix: `ddof=1`.
- **CRITICO: Monte Carlo ruin check unidades mixtas**: `max_dd * initial_equity >= max_loss` donde `max_dd` ya es porcentaje. `prob_ruin` era una métrica sin sentido. Fix: `max_dd >= self.max_drawdown_pct`.
- **CRITICO: Hawkes O(n²)**: Iteraba 10,000 eventos en cada call O(n) + segundo scan de conteo. A cientos de eventos/s, cuello de botella severo. Fix: kernel analítico `excitation = old * exp(-beta*dt) + alpha` que es O(1).
- **CRITICO: Hawkes adaptive_mu inflaba intensity**: `intensity = adaptive_mu + excitation` subía la baseline durante spikes, produciendo false positives en spike detection. Fix: usar `mu` original (fijo) para intensity y threshold.
- **HIGH: Rate limiter concurrencia**: Después de sleep, múltiples coroutines despertaban simultáneamente y todas appended timestamps sin re-check. Fix: while loop que re-verifica tras sleep.
- **HIGH: `get_market_snapshot` sin return_exceptions**: Un API fail de los 4 mataba todo el snapshot. Fix: `return_exceptions=True` + fallback a defaults.
- **HIGH: cancel_order field name snake_case**: `"order_id"` vs API que espera `"orderId"`. Potencialmente todas las cancelaciones fallaban silenciosamente. Fix: usar camelCase.
- **HIGH: SL/TP en status NEW**: Órdenes protectivas se colocaban antes de que la principal se llenara. Si era IOC y expiraba, quedaban SL/TP huérfanos. Fix: solo en `FILLED`/`PARTIALLY_FILLED`.
- **HIGH: Slippage sin signo**: `abs()` impedía distinguir slippage favorable de adverso. Calibración corrupta. Fix: signed slippage por lado.
- **HIGH: Backtester sin slippage en exits**: Solo entries tenían slippage → PnL sobreestimado. Fix: aplicar slippage adverso en exits.
- **HIGH: Calmar ratio no anualizado**: Return total / max_dd no es Calmar ratio. Fix: anualizar return basado en span temporal de trades.
- **HIGH: `logging.disable` permanente**: 6 funciones de backtest deshabilitaban logging para todo el proceso y nunca lo rehabilitaban. Fix: `logging.disable(logging.NOTSET)` al final de cada función.
- **MEDIUM: ISO year vs Gregorian year**: `now.year` vs `isocalendar()[0]` diverge en enero/diciembre. Semana actual calculada incorrectamente → compactación prematura o skip. Fix: usar isocalendar() para ambos (2 ocurrencias).
- **MEDIUM: NaN en regime_detector**: Si `vol_pct` era NaN, todas las comparaciones retornaban False, y el régimen se basaba solo en `momentum > 0`. Fix: guard con `math.isnan()` y defaults explícitos.
- **MEDIUM: Global random seed**: `np.random.seed(42)` en historical_data y optimizer contaminaba toda la randomness del proceso. Fix: `np.random.default_rng(42)` local.
- **Patron general**: Los bugs más peligrosos en un trading bot son los de estado compartido (`_positions`, `_equity`, async state). No crashean — producen comportamiento silenciosamente incorrecto que solo se descubre en producción cuando ya perdiste dinero.

## Desktop App Architecture (sesion 2026-04-01)
- **Tauri v2 > Electron** para trading apps: ~15MB installer vs 100MB+, uses OS WebView2 (preinstalled Win11), near-zero RAM overhead. Sidecar support manages Python process natively.
- **Bridge server pattern**: FastAPI wraps existing BotStrike class without modifying it. WebSocket channels for real-time streaming, REST for request/response. Zero changes to battle-tested Python backend.
- **TradingView Lightweight Charts**: Imperative API (`series.update()`) avoids React re-renders for high-frequency price data. Use refs, not state, for chart data.
- **Zustand over Redux**: Perfect for WebSocket-driven updates — direct store mutations from WS message handlers, no action creators/reducers boilerplate.
- **Tailwind v4 + @theme**: Custom properties via `@theme {}` block instead of `tailwind.config.ts`. Colors defined once, used everywhere with `text-accent`, `bg-bg-surface`, etc.
- **Glassmorphism recipe**: `bg-bg-surface/70 backdrop-blur-xl border border-white/5` + subtle box-shadow. The `/70` opacity suffix is key for the glass effect.
- **Font loading**: `@import url()` must come BEFORE `@import "tailwindcss"` in CSS or the Tailwind layer rules override it.
- **TypeScript strict + path aliases**: `@/*` mapped to `./src/*` via tsconfig `paths` + vite `resolve.alias`. Both must be configured for it to work.
- **verbatimModuleSyntax**: Disable in tsconfig when using barrel re-exports or type imports without explicit `type` keyword.

## Signal Generation Calibration (2026-04-03)
- OFM thresholds were calibrated for Binance/CME liquidity. Strike Finance has much less activity → Hawkes never spiked above 2.5x, OBI never exceeded 0.10, scores never reached 0.55. Solution: recalibrate all thresholds for the actual exchange.
- MR relied ONLY on RSI divergence (rare pattern, can be absent for days). Added z-score mode as frequent fallback for RANGING regime.
- Hawkes event count filter of 3/min was too strict for less liquid venues. Lowered to 1.
- CRITICAL: when strategies evaluate but generate zero signals, there was NO logging. Impossible to diagnose. Added debug logs at every filter/score computation point.
- With $300 capital, need more trade frequency for statistical feedback. Rare high-conviction signals don't give enough data to calibrate Kelly/RoR.

## OBI Absolute Level vs Delta (2026-04-03) — CRITICAL QUANT LESSON
- BTC orderbook on Binance has STRUCTURAL ask-heavy bias (more sell-side depth). This is normal market microstructure — market makers place more asks.
- Using OBI absolute level as directional signal → 99% SELL signals. This is NOT a bug in OBI calculation — the measurement is correct, the usage was wrong.
- OBI DELTA (change in imbalance) IS predictive of short-term price movement. When buying pressure INCREASES (delta > 0), price tends to move up. The absolute level tells you nothing about direction.
- Same lesson applies to depth_ratio: absolute ratio has structural bias. Use deviation from baseline (1.0) instead.
- General principle: in market microstructure, CHANGES are predictive. LEVELS have structural bias from market maker inventory management.
