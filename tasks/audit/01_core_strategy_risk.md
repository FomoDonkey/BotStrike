# Auditoría 01 — Core / Strategy / Risk (BotStrike)

**Fecha:** 2026-08-29 · **Alcance:** `main.py`, `strategies/*`, `core/market_data.py`, `core/regime_detector.py`, `core/indicators.py`, `core/quant_models.py`, `core/microstructure.py`, `core/microprice.py`, `risk/risk_manager.py`, `portfolio/portfolio_manager.py`, `config/settings.py`.
**Método:** lectura línea a línea + ejecución de snippets con `py -3.12` sobre el código real. Nada afirmado sin evidencia. Archivo escrito de forma incremental.

---

## Hallazgos

### [P0] `cancel_all()` cancela los SL/TP de exchange y deja posiciones abiertas DESNUDAS (shutdown y halt por drawdown)
**Archivo:** `main.py:821-826` (shutdown), `main.py:750-757` (risk monitor, halt por DD), `execution/order_engine.py:453-460`, `exchange/binance_client.py:491-504`
**Evidencia:**
```python
# main.py shutdown()
if not self.dry_run and not self.paper:
    await self.execution_engine.cancel_all()      # DELETE /fapi/v1/allOpenOrders por símbolo
await self.websocket.stop()                       # ...y no se cierra ninguna posición
# main.py _risk_monitor_loop (cada 2s mientras dd >= max_drawdown_pct)
self.risk_manager._drawdown_halted = True
if not self.dry_run and not self.paper:
    await self.execution_engine.cancel_all()      # mata STOP_MARKET/TAKE_PROFIT de posiciones vivas
```
`grep close_position|close_all main.py execution/order_engine.py` → 0 resultados: no existe cierre de posiciones en shutdown ni en halt.
**Por qué es un problema:** `allOpenOrders` borra también los `STOP_MARKET`/`TAKE_PROFIT_MARKET` reduce-only. (a) Cada `systemctl restart`/deploy (commit 3895da6) deja las posiciones abiertas sin stop mientras el bot está caído; al arrancar no se re-colocan protecciones ni se reconstruye `_states` (ver P1 F08) → posición sin SL de exchange ni SL software. (b) Peor: al tocar el max DD (10%) el loop cancela protecciones cada 2s justo cuando más se está perdiendo, y no cierra nada. Con 2x y un gap del 10% → -20% de la cuenta sin freno.
**Fix propuesto:** En shutdown: `await execution_engine.close_all_positions()` (MARKET reduce-only) **o** política explícita "keep positions" que NO cancele órdenes reduce-only (cancelar solo entradas: `for o in _active_orders if not o.reduce_only`). En el halt por DD: cerrar posiciones a mercado y después cancelar; ejecutar una sola vez (`if not _drawdown_halted:`), no cada 2s.
**Verificado cómo:** leído (main.py, order_engine.py, binance_client.py) + grep negativo de cierre de posiciones.

### [P0] Salidas de Fibonacci (`action="exit_fibonacci"`) NO se reconocen como exit en el motor de ejecución live → orden no reduce-only, puede ir como LIMIT IOC y no llenarse, con el estado de la estrategia ya borrado
**Archivo:** `execution/order_engine.py:92-94,146-152`, `strategies/fibonacci_retracement.py:530`, `execution/paper_simulator.py:354-356`
**Evidencia:**
```python
# order_engine.execute_signal
is_exit = signal.metadata.get("action") in ("exit_mean_reversion", "trailing_stop_hit", "mm_unwind")
...
elif routing.order_type == "MARKET" or is_exit:
    order = Order(..., order_type=OrderType.MARKET, reduce_only=is_exit, ...)
else:  # LIMIT IOC del smart router
# fibonacci_retracement._check_exit
metadata={"action": "exit_fibonacci", "exit_reason": exit_reason}
# paper_simulator (sí lo trata como exit):
is_exit = (action.startswith("exit") or action in ("trailing_stop_hit", "mm_unwind"))
```
**Por qué es un problema:** En live, un trailing stop / software SL / stale-exit de Fib se enruta como entrada normal: `reduce_only=False`, y si el router elige LIMIT IOC (no urgente porque `is_exit=False`) puede quedar sin llenar o parcial. Mientras tanto `_check_exit` ya hizo `self._states.pop(symbol)` y fijó cooldown → la posición sigue abierta sin trailing ni SL software (`_check_exit` devuelve None sin estado). Es además una divergencia paper≠live: en paper la salida funciona. También `risk_manager.validate_signal` sí la deja pasar (usa `startswith("exit")`), así que nada la bloquea antes.
**Fix propuesto:** `is_exit = str(signal.metadata.get("action","")).startswith("exit") or signal.metadata.get("exit_reason") is not None` en `execute_signal` (misma regla que paper y risk manager). Y en la estrategia, no hacer `_states.pop` hasta confirmar el fill (o rehacer el estado si la posición sigue existiendo en el siguiente eval).
**Verificado cómo:** leído + grep de `exit_fibonacci` en `execution/` (solo aparece en la estrategia).

### [P0] `_performance_factor` está mal escalado: tras ≥5 fills con PnL medio ≤ -$0.03 la estrategia queda desactivada PARA SIEMPRE y en silencio — y además se salta la gestión de salidas de posiciones abiertas
**Archivo:** `portfolio/portfolio_manager.py:196-211,249-251`, `main.py:521-526,342,617`
**Evidencia:**
```python
# portfolio_manager._performance_factor
avg_pnl = pnl / trades
exp_val = max(-500, min(500, -avg_pnl * 100))     # ×100 sobre USD absolutos
factor = 1.0 + 0.5 * (2.0 / (1.0 + math.e ** exp_val) - 1.0)
# should_strategy_trade:  if perf < 0.6: return False   (sin log)
# main._process_symbol — ANTES de generate_signals (que contiene _check_exit):
if not strategy.should_activate(regime): continue
if not self.portfolio_manager.should_strategy_trade(strategy.strategy_type, regime, symbol=symbol): continue
# main.py:617 / :342 — update_strategy_pnl se llama en TODOS los fills, incl. entradas con pnl=0 (trades cuenta 2× por round-trip)
```
Ejecutado (`py -3.12`): avg_pnl=-0.02 → factor 0.619 (opera); **-0.03 → 0.547 → bloqueada**; -0.10 → 0.500. Con posiciones de ~$250 (ver F13) una pérdida típica es $0.4-1 → 3 pérdidas y 2 ganancias en los primeros 5 trades bloquean la estrategia.
**Por qué es un problema:** (1) Deadlock: sin trades nuevos `avg_pnl` nunca se recupera → bot zombi hasta reinicio (estado en memoria) y sin ningún log que lo diga. (2) Al hacer `continue` antes de `generate_signals`, la posición abierta de esa estrategia deja de recibir trailing stop / software SL / stale exit (queda solo el SL/TP del exchange, que en shutdown se cancela — F01). (3) Contar entradas (pnl=0) como trades duplica `trades` y distorsiona `avg_pnl`.
**Fix propuesto:** Normalizar por equity y por trade: `avg_ret = (pnl/trades)/equity`, sigmoide sobre `avg_ret*1000` (≈-1% medio → 0.5), y `trades` solo con fills de salida (`pnl != 0` o `action startswith exit`). Añadir `logger.warning("strategy_disabled_by_performance", ...)`. Y en `_process_symbol`: evaluar SIEMPRE `generate_signals` cuando `current_pos is not None` (o exponer `check_exit()` separado) y aplicar `should_activate/should_strategy_trade` solo a ENTRADAS.
**Verificado cómo:** leído + ejecutado (réplica exacta de la fórmula).

### [P1] F04 — `bars_held`, stale-exit (24h), trailing "tight" y expiración de impulsos Fib se derivan de `len(resampled)`, que SATURA (200 barras MR / 133 Fib) → todos quedan muertos en live y en backtest
**Archivo:** `strategies/mean_reversion.py:43,288,342-343,371,381-385`, `strategies/fibonacci_retracement.py:45,249-253,348,470-471,503-507,560`, `core/market_data.py:23`
**Evidencia:**
```python
# mean_reversion: entry_bar_idx=len(self._resampled[symbol]); bars_held = current_bar_count - state.entry_bar_idx
# _resample_5m: tail = df.tail(RESAMPLE_MINUTES * RESAMPLE_BUFFER)  # 1000 filas -> len(m5) == 200 constante
# fibonacci: self._resampled[symbol] = m15.tail(RESAMPLE_BUFFER); impulse_age = len(m15) - active_impulse.bar_idx_end
# market_data: MAX_BARS = 2000  -> len(m15) = 2000//15 = 133 constante
```
Ejecutado: con `df` de 2000 filas `len(m5)=200`, `len(m15)=133`; tras alimentar **600 barras 1m más (10h)**: `bars_held = 0` (esperado ~120). En backtest la ventana deslizante es de 501 filas (`backtesting/backtester.py:366-367`) → `len(m5)=100` constante → mismo resultado desde la primera barra.
**Por qué es un problema:** `stale_position_24h` nunca dispara, `TRAIL_TIGHT_AFTER_BARS` nunca se activa, `MAX_IMPULSE_AGE` nunca expira (`impulse_age = 20 - max_idx <= 20 < 30`). Es la misma clase de bug "contador ≠ barras" que lessons.md (Audit #26) ya documentó: se sustituyó `eval_counter` por `len()` y volvió a fallar por otro camino. Backtest y live coinciden… en tener ese código muerto.
**Fix propuesto:** Guardar en el estado el **timestamp** de la barra de entrada (`m5["timestamp"].iloc[-1]`; Fib ya lo tiene por grupo) y calcular `bars_held = (ts_now - ts_entry) / (RESAMPLE_MINUTES*60)`; expirar impulsos por `ts_now - ts_swing_end > MAX_IMPULSE_AGE*15*60`. En backtest usar el mismo timestamp de barra (como ya se hace para el cooldown).
**Verificado cómo:** ejecutado (simulación con las clases reales) + leído.

### [P1] F05 — El "filtro de tendencia 1H" es estadísticamente vacío: ADX(1H) sesgado a ~100 en warmup y >=20 el 100% del tiempo en un random walk; solo 33 barras horarias disponibles para siempre (MAX_BARS=2000)
**Archivo:** `strategies/mean_reversion.py:47,203,447-480`, `core/indicators.py:115-134`, `core/market_data.py:23`, `main.py:171` (seed 6h)
**Evidencia:**
```python
# indicators.adx: dx.ewm(span=2*period-1, adjust=False).mean()   <- arranca en el primer DX (~100: solo un DM != 0)
# mean_reversion._update_h1_trend: if len(df) < 60*6: trend=0 ... tail(60*100) -> pero df tiene max 2000 -> 33 barras 1H
if h1_trend == 0 or h1_adx < ADX_MIN_TREND: return signals   # "the key filter that turns losing MR into breakeven+"
```
Ejecutado sobre random walks (sin tendencia, código real de `Indicators`): **6 barras 1H → ADX medio 91 (p10=83), >=20 en el 100%**; **33 barras 1H (estado estacionario) → ADX medio 39.6, >=20 en el 100%**; con 200 barras → 25.2, >=20 en el 63%. Con `adjust=True`+`min_periods` a 33 barras: 29.9 / 76%. EMA26 con `adjust=False` sobre 33 barras aún lleva ~8% de peso del primer close.
**Por qué es un problema:** El filtro que la docstring llama "clave" no filtra nada: la dirección la decide EMA12>EMA26 sobre <=33 barras (momentum de 1-2 días con sesgo de arranque) y el ADX pasa siempre. ADX>=20 es ~60% del tiempo en ruido puro incluso bien calentado. Además `NaN < 20` es False → un ADX NaN también PASA el filtro.
**Fix propuesto:** (1) DataFrame 1H separado sembrado con `GET /fapi/v1/klines?interval=1h&limit=300` y actualizado por cierre de barra (no re-cortar 1m). (2) `Indicators.adx/atr/rsi` con `adjust=True, min_periods=period` (o seeding Wilder clásico), devolver NaN en warmup y tratar NaN como "sin tendencia" en MR. (3) Reconsiderar ADX_MIN_TREND (>=25) con validación OOS.
**Verificado cómo:** ejecutado (300 paths) + leído.

### [P1] F06 — MR no tiene puerta de R:R NETO: con ATR real de hoy, ETH 5m da net R:R 1.01 (breakeven WR 49.7%) y BTC 0.87; las comisiones equivalen al 100% de la distancia al SL. La docstring promete "gross R:R 2.67:1"
**Archivo:** `strategies/mean_reversion.py:48-49,267-271`, `config/settings.py:95-98,185-203`
**Evidencia:**
```python
rt_cost = price * 14 / 10000          # 14 bps hardcoded (config real: 4+4 taker + 1.5+1.5 slip = 11 bps)
net_profit = tp_mult * atr - rt_cost
if net_profit <= 0: return signals    # solo exige TP > coste; no compara con la perdida neta
```
Ejecutado con klines reales de Binance Futures (500x5m, mediana ATR14 últimas 300 barras, coste 11 bps):

| sym | TF | ATR bps | SL bps | TP bps | net win | net loss | net R:R | BE WR |
|---|---|---|---|---|---|---|---|---|
| ETH | 5m | 8.9 | 13.4 | 35.7 | 24.7 | 24.4 | **1.01** | **49.7%** |
| SOL | 5m | 18.1 | 32.7 | 72.6 | 61.6 | 43.7 | 1.41 | 41.5% |
| ADA | 5m | 20.1 | 40.3 | 80.5 | 69.5 | 51.3 | 1.36 | 42.4% |
| BTC | 5m | 7.6 | 11.5 | 30.6 | 19.6 | 22.5 | 0.87 | 53.5% |
| BTC | 15m | 26.2 | 39.2 | 104.6 | 93.6 | 50.2 | 1.86 | 34.9% |

**Por qué es un problema:** Un pullback RSI<35 con 2 confirmaciones no tiene WR>=50% demostrado (lessons: 45-50% en literatura; OOS previo PF 0.47-0.85). En ETH la estrategia opera con edge cero antes de la primera pérdida. Fib sí tiene `net_reward/net_risk >= 1.5`.
**Fix propuesto:** `cost_bps` desde `trading_config`; `net_rr = (tp - cost)/(sl + cost); if net_rr < 1.5: return`; gate `atr_bps >= 2*cost_bps` (regla ya escrita en lessons). Loggear `net_rr` en metadata.
**Verificado cómo:** ejecutado con datos reales + leído.

### [P1] F07 — Live: equity y peak nunca se inicializan desde el exchange; `_current_equity=1000` fantasma hasta el primer ACCOUNT_UPDATE; el peak se resetea a `initial_capital` en cada reinicio → DD floor fijo respecto a una constante de config
**Archivo:** `risk/risk_manager.py:40-41`, `main.py:369-380`, `main.py:135-215` (start: sin `get_balances`), `exchange/binance_client.py:342-345`
**Evidencia:**
```python
# risk_manager.__init__
self._equity_peak = self.config.initial_capital; self._current_equity = self.config.initial_capital
# main.on_account_update — unica fuente de equity en live:
equity = float(b.get("wb", 0))         # wallet balance: realizado, SIN unrealized PnL
await self.risk_manager.update_equity_safe(equity)
```
`grep get_balances|get_account main.py` → 0 usos (existen en `binance_client.py:342-345`).
**Por qué es un problema:** (a) Hasta que llegue un ACCOUNT_UPDATE (solo con fill/funding/transfer) sizing y límites usan $1000 aunque la cuenta tenga $700 o $1500. (b) Tras una pérdida real del 10%, cada reinicio arranca con peak=1000 y equity≈900 → `max_drawdown_reached` inmediato y permanente: bot halted para siempre sin cambiar config (y con F01 cancelando protecciones cada 2s). (c) Tras ganancias el peak se pierde y el 10% ya no protege el capital ganado. (d) `wb` excluye unrealized → el circuit breaker no ve pérdidas flotantes de 4 posiciones correlacionadas (lección "flash crash").
**Fix propuesto:** En `start()` live: `bal = await client.get_balances()` → `update_equity(totalMarginBalance)`; persistir `equity_peak`/`daily_pnl`/fecha en `data/risk_state.json` y recargar; usar margin balance (con unrealized) vía REST en cada ciclo del risk loop.
**Verificado cómo:** leído + grep.

### [P1] F08 — Live: los fills de SL/TP del exchange no notifican a la estrategia (solo paper llama a `notify_external_exit`) → sin cooldown tras stop y `_states` obsoleto; tras reinicio, posiciones existentes no tienen estado → `_check_exit` devuelve None (sin trailing ni software SL)
**Archivo:** `main.py:619-624` (solo `_process_paper_fill`), `main.py:330-360` (`on_order_update` live no notifica), `strategies/mean_reversion.py:330-332`, `strategies/fibonacci_retracement.py:458-460`, `main.py:733-748`
**Evidencia:**
```python
# _process_paper_fill (paper ONLY)
is_sl_tp = trade.signal_features.get("exit_reason") in ("SL", "TP")
if is_sl_tp: ... strategy.notify_external_exit(trade.symbol, time.time())
# _check_exit (ambas estrategias)
state = self._states.get(symbol)
if not state: return None
```
**Por qué es un problema:** (1) Live: tras un SL de exchange `_last_exit_time` no se fija → re-entrada en la siguiente barra 1m si RSI sigue <35 y la tendencia 1H no cambió ("revenge re-entry" en el mismo pullback fallido); paper sí aplica 180s → divergencia paper≠live en la frecuencia de trades tras pérdida. (2) Reinicio con posición abierta: `_states` vacío → la posición vive solo del SL/TP del exchange (que F01 cancela en shutdown) → sin ninguna gestión.
**Fix propuesto:** En `on_order_update` live: si `trade.pnl != 0` → `strategy.notify_external_exit(symbol, ts)` para `order.strategy`. En `generate_signals`, si hay `current_position` y no hay estado → reconstruir `MRState` desde la posición (entry_price, timestamp, sl/tp de sym_config) y loggear `state_rebuilt`; Fib: estado mínimo con SL/TP por ATR.
**Verificado cómo:** leído + grep (`notify_external_exit` solo en main.py:623-624).

### [P1] F09 — La gestión de salidas se salta cuando cambia el régimen: `should_activate`/`should_strategy_trade` se evalúan ANTES de `generate_signals` → MR en BREAKOUT y Fib en UNKNOWN dejan posiciones abiertas sin trailing ni software SL
**Archivo:** `main.py:520-547`, `strategies/mean_reversion.py:107-110,161-165`, `strategies/fibonacci_retracement.py:158-162,202-206`
**Evidencia:**
```python
for strategy in self.strategies:
    if not strategy.should_activate(regime): continue            # MR: regime != BREAKOUT ; Fib: regime != UNKNOWN
    if not self.portfolio_manager.should_strategy_trade(...): continue
    ...
    signals = strategy.generate_signals(...)   # <- aqui dentro esta _check_exit
```
**Por qué es un problema:** BREAKOUT es justo el régimen en que una posición MR contraria necesita salir rápido; en ese momento el trailing deja de evaluarse. El régimen se calcula sobre 1m con percentiles adaptativos (`regime_detector.py:147-180`: vol>p75 y |mom|>1.5·p65) → BREAKOUT es frecuente. Misma causa raíz que el `continue` de F03.
**Fix propuesto:** Separar `check_exit(...)` en `BaseStrategy` y llamarlo incondicionalmente cuando hay posición; aplicar gates solo a entradas.
**Verificado cómo:** leído.

### [P1] F10 — Fib: impulsos obsoletos nunca caducan (F04) y `_detect_impulse`→None no limpia el activo; tras una salida el mismo impulso se re-detecta y se re-entra a los 5 min
**Archivo:** `strategies/fibonacci_retracement.py:240-253,352-353,389-450`
**Evidencia:**
```python
impulse = self._detect_impulse(symbol, m15, atr)
if impulse: self._impulses[symbol] = impulse            # None -> el viejo sigue activo
if impulse_age > MAX_IMPULSE_AGE: ...                     # nunca (len(m15) constante, F04)
self._impulses[symbol] = None   # "Consume the impulse" — la siguiente barra lo vuelve a detectar (mismo max/min en la ventana)
```
**Por qué es un problema:** (1) Se operan niveles Fib de un impulso de 20+ horas si el precio vuelve a la zona 50-61.8%. (2) Tras un stop-out en 78.6% y rebote a la zona, se reentra en el mismo impulso fallido (cooldown 300s), contra el diseño declarado.
**Fix propuesto:** Identificar impulso por `(ts_swing_low, ts_swing_high)`; `consumed_impulses[symbol]`; expirar por timestamp.
**Verificado cómo:** leído + ejecutado (saturación de `len(m15)`).

### [P2] F11 — Umbrales RSI adaptativos por volatilidad son código muerto: leen `volatility_percentile` pero la columna se llama `vol_pct` (y va 0-1, no 0-100)
**Archivo:** `strategies/mean_reversion.py:210-221`, `core/indicators.py:223`
**Evidencia:**
```python
vol_pctile = float(bar.get("volatility_percentile", 50))   # siempre 50
if vol_pctile > 70: ... elif vol_pctile < 30: ...
# indicators.compute_all: df["vol_pct"] = Indicators.volatility_percentile(close)   # rango 0..1
```
Ejecutado: `'volatility_percentile' in m5.columns → False`, `vol_pct` rango 0.0-0.98.
**Por qué es un problema:** SOL/ADA nunca reciben el pullback más profundo que el comentario describe; la lógica documentada no existe en producción. **Fix:** `bar.get("vol_pct", 0.5)*100`.
**Verificado cómo:** ejecutado + leído.

### [P2] F12 — REGRESIÓN (lessons Audit #24): `_calc_position_size` sigue restando la fricción del `risk_amount` en vez de sumarla a `risk_per_unit`
**Archivo:** `strategies/base.py:97-109`
**Evidencia:**
```python
friction_cost = raw_notional * friction_bps / 10_000
adjusted_risk = max(risk_amount - friction_cost, risk_amount * 0.5)
size_units = adjusted_risk / risk_per_unit
```
Lección escrita: "Fórmula correcta: `size = risk_amount / (risk_per_unit + friction_per_unit)`". Hoy es irrelevante en la práctica porque manda el cap de apalancamiento (F13), pero en cuanto se corrija F13 vuelve a infra-dimensionar 20-30%.
**Fix:** `size = risk_amount / (risk_per_unit + price*friction_bps/1e4)`.
**Verificado cómo:** leído (código y lessons.md).

### [P2] F13 — El sizing lo domina el cap `allocated_capital × leverage` ($125×2=$250): el riesgo real por trade es 0.04-0.06% del equity (config: 1.5% MR / 4% Fib). Kelly, RoR-throttle, vol-targeting, `max_position_usd` y `risk_per_trade_pct` no influyen; el 50% del equity nunca es asignable
**Archivo:** `strategies/base.py:110-114`, `portfolio/portfolio_manager.py:62-67,168-170,189`, `config/settings.py:87,181-205`, `strategies/fibonacci_retracement.py:72`
**Evidencia (ejecutado con las clases reales):** `allocated = 1000×0.5×0.25 = $125` por bucket. MR-ETH (ATR 10 bps, SL 1.5 ATR): `size_usd=250.0`, riesgo al SL **$0.375 = 0.037% equity** (previsto $1.875). Fib-BTC (SL 24 bps): `size_usd=250.0`, riesgo **$0.60 = 0.06%** (previsto $5 = "4%"). `symbol_share = 1/4` aunque `SYMBOL_STRATEGY_MAP` permite una sola estrategia por símbolo → los buckets MR-BTC y Fib-ETH/SOL/ADA (50% del equity) no existen. `max_position_usd` 500/400 inalcanzables (cap $250).
**Por qué es un problema:** No pierde dinero, pero todo el "risk framework" es ficción: Kelly (100 trades), RoR (30), vol-targeting, `size_reduced_losses`… nunca cambian el tamaño porque el cap de apalancamiento es 5-10× más restrictivo. Con $250 notional las comisiones ($0.28/round-trip) son ~30-50% del movimiento medio esperado → la cuenta muere por fricción antes de aprender nada. Además `base.py` exige `min_notional=20` pero Binance USDT-M exige **100 USDT en BTCUSDT** (20 ETH, 5 SOL/ADA): tras cualquier reducción (funding 0.8×, micro 0.4×, RoR 0.5×) una orden BTC de <$100 se rechaza (-4164).
**Fix:** Decidir el modelo: o (a) `allocated_capital` = equity × peso de estrategia / nº de símbolos ELEGIBLES para esa estrategia, o (b) sizing por riesgo sobre equity total (`risk_amount = equity×risk_pct`) con cap por `max_position_usd` (ya lo hace el risk manager). Tabla `min_notional` por símbolo desde `exchangeInfo`. Eliminar del hot path los modelos que no pueden actuar (lessons: "si no tiene consumidor, no generarlo").
**Verificado cómo:** ejecutado + leído.

### [P2] F14 — `max_total_exposure_pct=0.6` significa en realidad 300% del equity: `_check_total_exposure` multiplica por `max_leverage`
**Archivo:** `risk/risk_manager.py:296-306`, `config/settings.py:85,224-232`
**Evidencia:**
```python
max_exposure = self._current_equity * self.config.max_total_exposure_pct * self.config.max_leverage  # 1000×0.6×5 = $3000
```
El `__post_init__` de Settings valida `max_position_usd <= capital×0.6 = 600` (sin leverage) → dos semánticas distintas del mismo parámetro. Hoy no muerde (suma de caps $1300), pero si se corrige F13 el límite "60%" permitirá 3× el equity.
**Fix:** Definir exposición como notional/equity y usar `max_total_exposure_pct × max_leverage` explícitamente en config con nombre `max_gross_leverage`.
**Verificado cómo:** leído.

### [P2] F15 — Filtro de funding mal calibrado para holds intradía: `funding_rate_warn=0.0001` es el funding BASE de Binance (0.01%/8h) → todo long en mercado neutro se recorta 20%; `block=0.0005` bloquea todos los longs en mercados alcistas normales
**Archivo:** `risk/risk_manager.py:165-186`, `config/settings.py:100-101`
**Evidencia:** `penalty = 1 - min(abs_rate/0.0005, 0.7)` → con 0.0001: 0.8; con 0.0003: 0.4; >=0.0005: `return None`.
**Por qué es un problema:** Un trade de 1-5h paga como máximo un funding de 1-5 bps, frente a 11-14 bps de comisiones ya asumidas. El filtro sesga sistemáticamente contra la dirección de la tendencia (justo la que MR "trend pullback" quiere operar).
**Fix:** `warn=0.0005`, `block=0.0015` (o comparar `funding_bps × holds_esperados/8h` contra `net_profit_bps`).
**Verificado cómo:** leído + aritmética.

### [P2] F16 — El seed de Binance incluye la vela 1m EN FORMACIÓN como si estuviera cerrada; timestamps del seed = open time, de las barras live = close time; el primer minuto queda duplicado/parcial
**Archivo:** `core/market_data.py:117-179,336-349,359-409`
**Evidencia:** REST real (`limit=2`) devuelto en la auditoría: último kline `open_time=…9000, close_time=…9059, now=…9013 → closed=False`. `seed_from_binance` guarda `timestamp=k[0]` (open) y `_last_bar_time = df.timestamp.iloc[-1]`; `_close_bar` etiqueta `timestamp=bar_close_ts` (close).
**Por qué es un problema:** Indicadores calculados con una barra parcial persistente; la primera barra live cubre solo los segundos desde la conexión y duplica el minuto; el eje temporal mezcla dos convenciones (afecta al chart y a cualquier lógica futura basada en timestamps, incluida la propuesta en F04).
**Fix:** Descartar el último kline si `close_time > now`; `_last_bar_time = close_time_del_último_cerrado`; unificar la convención (recomendado: open time) en `_close_bar`.
**Verificado cómo:** ejecutado (REST real) + leído.

### [P2] F17 — El "resampleo 5m/15m" es posicional y se RE-CORTA en cada barra 1m: no son barras 5m de reloj; la señal 5m se evalúa 5 veces por período sobre cortes distintos y los indicadores saltan cada minuto
**Archivo:** `strategies/mean_reversion.py:428-445`, `strategies/fibonacci_retracement.py:535-560`
**Evidencia:** `groups = np.arange(len(trim)) // RESAMPLE_MINUTES` sobre `tail(n)` que termina siempre en la última 1m. Ejecutado: tras añadir UNA barra 1m, las 3 últimas barras "5m" cambian por completo (`[56294,56376],[56376,56477],[56477,56400]` → `[56336,56426],[56426,56435],[56435,56406]`).
**Por qué es un problema:** RSI/ATR/BB "5m" no son series estacionarias sino 5 series entrelazadas; el BB-touch y la mecha de rechazo de la "última vela 5m" dependen del minuto en que se mire. Es coherente con el backtest (mismo código), pero cualquier análisis externo "5m" (los PF 0.85/0.86 citados en `SYMBOL_STRATEGY_MAP`) no describe lo que corre. Además multiplica ×5 las oportunidades de entrada frente a un 5m real.
**Fix:** Agrupar por `timestamp // (RESAMPLE_MINUTES*60)` y usar solo grupos completos; evaluar entradas solo al cerrar un grupo 5m/15m.
**Verificado cómo:** ejecutado + leído.

### [P2] F18 — `Indicators.compute_all` sobre 2000 filas tarda ~169 ms (rolling.apply en Python en `volatility_percentile`) y corre DENTRO del callback WS `on_trade` → `_close_bar` bloquea el event loop ~0.7 s para 4 símbolos en cada límite de minuto
**Archivo:** `core/market_data.py:400-406`, `core/indicators.py:96-112`, `main.py:290-300`
**Evidencia:** ejecutado `compute_all(2000 rows) = 169 ms`. Las 4 barras cierran en el mismo segundo (alineadas a minuto). En paper, los checks de SL/TP por tick (`paper_sim.on_price_update`) y en live el procesamiento de fills/depth se retrasan ese tiempo.
**Fix:** vectorizar el percentil (`rolling.rank(pct=True)`), calcular solo la última fila incrementalmente, o `loop.run_in_executor`.
**Verificado cómo:** ejecutado + leído.

### [P2] F19 — Los modelos cuant (VolTargeting: 5 returns diarios; CorrelationRegime: 10 días; Kelly: 100 trades; RoR: 30 trades) viven solo en memoria → con reinicios semanales nunca se activan; y si se activan, el vol-scalar 1.5× se aplica DESPUÉS del cap de apalancamiento de la estrategia
**Archivo:** `core/quant_models.py:69-93,178-186,264-276`, `risk/risk_manager.py:199-214`, `portfolio/portfolio_manager.py:114-135`
**Evidencia:** ningún `load/save` de estado (`grep -rn "risk_state\|json.dump" risk/ core/quant_models.py` → 0). `signal.size_usd *= vol_scalar` tras `_calc_position_size` (cap `capital*leverage`).
**Por qué es un problema:** Complejidad sin efecto (lessons "Estrategias desactivadas corriendo en runtime"), y cuando por fin actúa, empuja el notional por encima del cap que la estrategia creía respetar (luego lo frena `max_position_usd`, ver F13).
**Fix:** Persistir en `data/` y precargar desde `trade_database` al arrancar, o sacar del hot path hasta tener datos.
**Verificado cómo:** leído + grep.

### [P2] F20 — REGRESIÓN (lessons Audit #24 "Microprice Clamping Destruye Alpha"): `microprice_adjusted` vuelve a estar clampeado a [bid, ask]
**Archivo:** `core/microprice.py:228-233`
**Evidencia:**
```python
microprice_adjusted = microprice_ml + intensity_adjustment + obi_adjustment
# Clamp: no puede salir del bid-ask spread
microprice_adjusted = max(best_bid, min(best_ask, microprice_adjusted))
```
**Por qué es un problema:** La lección documentada dice exactamente lo contrario para el estimador compuesto. Impacto hoy limitado (solo alimenta el smart router vía metadata), pero es una regresión contra una decisión registrada.
**Fix:** clamp solo L1/ML; dejar `adjusted` libre con límite amplio (p.ej. ±2 spreads).
**Verificado cómo:** leído (código + lessons.md).

### [P2] F21 — Live: `record_trade_result` recibe `rp` (realizedProfit de Binance, BRUTO de comisión) y una vez por fill parcial; paper registra neto por round-trip → rachas de pérdidas, Kelly y RoR ven series distintas en paper y live
**Archivo:** `execution/order_engine.py:377,420,427-433`, `main.py:626-631`
**Evidencia:** `realized_pnl = float(data.get("rp", …))`; `Trade(pnl=realized_pnl, fee=commission)`; `ensure_future(record_trade_result_safe(realized_pnl, …))` (sí se programa: no es el bug de coroutine sin await; ese sí persiste en las copias congeladas `desktop/src-tauri/target/{debug,release}/…/order_engine.py:374`, que son builds viejas).
**Fix:** `record_trade_result(rp - commission)` y agregar por `orderId` hasta `status == FILLED`.
**Verificado cómo:** leído + grep.

### [P2] F22 — VPIN con `bucket_size=50k USD` en BTC-USDT-PERP (volumen diario del orden de $20-50B en Binance) → ~50 buckets = ventana de segundos; `should_filter_mr` (VPIN>=0.85 y Hawkes>=4) es ruido sin calibrar (lessons Audit #22)
**Archivo:** `config/settings.py:50-52,184`, `core/microstructure.py:890-895`, `risk/risk_manager.py:126-133`
**Evidencia:** aritmética: 50k × 50 = $2.5M de volumen por ventana VPIN ≈ 5-10 s de flujo en BTC. VPIN (Easley et al.) se define con buckets de 1/50 del volumen diario.
**Por qué es un problema:** Bloqueos/reducciones de tamaño aleatorios en entradas MR sin evidencia; lección ya registrada sin aplicar. **Fix:** `bucket_size = ADV/50` por símbolo calculado en arranque desde `ticker/24hr`, o desactivar el filtro hasta calibrar.
**Verificado cómo:** leído + aritmética (no medido con datos en vivo).

### [P3] F23 — `_data_refresh_loop` sustituye el objeto `MarketSnapshot` cada 30 s → se pierden `orderbook` y `regime` hasta el siguiente depth update; un eval concurrente ve `obi=None` (una confirmación menos)
**Archivo:** `core/market_data.py:310-318`, `main.py:768-776`, `main.py:495-499`
**Fix:** actualizar campos in-place (`snap.funding_rate = …`) en vez de reemplazar el objeto.
**Verificado cómo:** leído.

### [P3] F24 — Metadata de señal MR registra `SL_ATR_MULT/TP_ATR_MULT` (constantes) en vez de `sl_mult/tp_mult` per-symbol
**Archivo:** `strategies/mean_reversion.py:319-320`. **Fix:** `"sl_mult": sl_mult, "tp_mult": tp_mult`. **Verificado cómo:** leído.

### [P3] F25 — `is_circuit_breaker_active` (property) muta estado; `_adjust_stop_loss` estrecha el SL de exchange en DD>5% mientras el SL software (`MRState.sl_mult`) y el R:R de entrada siguen con el multiplicador original
**Archivo:** `risk/risk_manager.py:337-352,501-513`. Estrechar stops dentro de la banda de ruido durante un drawdown aumenta la frecuencia de pérdidas (anti-patrón). **Fix:** eliminar el ajuste o propagarlo a metadata para que la estrategia lo respete. **Verificado cómo:** leído.

### [P3] F26 — Incoherencia config↔runtime: `SYMBOL_STRATEGY_MAP` hardcoded en portfolio_manager (BTC solo Fib; ETH/SOL/ADA solo MR) vs `allocation_* = 0.50/0.50` y `REGIME_WEIGHTS`; `should_strategy_trade` devuelve False en silencio
**Archivo:** `portfolio/portfolio_manager.py:62-67,231-253`, `config/settings.py:88-93`. **Fix:** mover el mapa a `SymbolConfig.strategies` y loggear el motivo de exclusión una vez por cambio. **Verificado cómo:** leído.

### [P3] F27 — Live: `_positions` solo se alimenta por REST cada 2 s; si `get_positions()` falla >60 s (429/418), la estrategia puede generar una segunda entrada en la siguiente barra 1m sobre una posición ya abierta
**Archivo:** `main.py:535,727-748`. Mitigado hoy por `new_bar_arrived` (1/min) y `max_open_positions=4`. **Fix:** marcar `pending_entry[symbol]` en `execute_signal` hasta reconciliar. **Verificado cómo:** leído.

### [P3] F28 — Si el seed REST falla, `_last_bar_time` se fija con el timestamp del primer tick → barras 1m desalineadas del reloj para siempre
**Archivo:** `core/market_data.py:347-349`. **Fix:** `self._last_bar_time[symbol] = ts - (ts % self.bar_interval)`. **Verificado cómo:** leído.

---

## Regresiones comprobadas contra `tasks/lessons.md`

| Lección | Estado |
|---|---|
| Circuit breaker no se re-arma en cada check (Audit #26) | ✅ sigue arreglado (`risk_manager.py:363-378`, `if not self._circuit_breaker_active`) |
| Stale tick guard con override tras 5 rechazos; `_last_data_time` solo tras aceptar | ✅ (`market_data.py:252-275,327-332`) |
| `bar_interval=60` documentado | ✅ (`market_data.py:62-64`) |
| `eval_counter` no se usa como contador de barras | ⚠️ sustituido por `len(resampled)` → **nuevo bug equivalente (F04)** |
| Warmup 1H ≤ seed (6h) | ⚠️ cumple en horas, pero el filtro resultante es estadísticamente nulo (F05) |
| Handler de funding en WS markPrice | ✅ (`main.py:383-405`) |
| `Position.notional` fallback a entry_price | ✅ (`core/types.py:185-190`) |
| Symbol map único (adapter) | ✅ (`binance_ws.py:35-36` deriva de `binance_client.SYMBOL_MAP`) |
| Protecciones fallidas → cierre de emergencia | ✅ (`order_engine.py:266-285`) |
| Friction en sizing amplía `risk_per_unit` (Audit #24) | ❌ **REGRESIÓN (F12)** |
| Microprice sin clamp para el compuesto (Audit #24) | ❌ **REGRESIÓN (F20)** |
| Race conditions: `asyncio.Lock` en RiskManager | ✅ existe; `validate_signal` sigue sin lock pero es sync sin `await` (OK) |
| Tests | ✅ `py -3.12 -m pytest tests -q` → **36 passed** (ninguno cubre F01-F10) |

---

## Tabla resumen

| ID | Sev | Archivo | Título |
|---|---|---|---|
| F01 | P0 | main.py:821-826, 750-757 | `cancel_all()` en shutdown y en halt por DD cancela SL/TP y deja posiciones desnudas |
| F02 | P0 | execution/order_engine.py:92-94 | `exit_fibonacci` no reconocido como exit en live → orden no reduce-only / LIMIT IOC, estado ya borrado |
| F03 | P0 | portfolio/portfolio_manager.py:196-211 + main.py:521-526 | `_performance_factor` mal escalado → estrategia desactivada para siempre tras 5 fills con avg ≤ -$0.03, sin log y sin gestión de salidas |
| F04 | P1 | strategies/mean_reversion.py:288,342 / fibonacci_retracement.py:249,470 | `bars_held`/`impulse_age` desde `len(resampled)` saturado → stale-exit, trail tight y expiración de impulsos muertos |
| F05 | P1 | strategies/mean_reversion.py:203,447-480 / core/indicators.py:115-134 | Filtro de tendencia 1H nulo: ADX sesgado (≥20 el 100% en random walk), 33 barras 1H máximo |
| F06 | P1 | strategies/mean_reversion.py:267-271 | Sin puerta de R:R neto: ETH 5m net R:R 1.01 (BE WR 49.7%), coste hardcoded 14 bps |
| F07 | P1 | risk/risk_manager.py:40-41 / main.py:369-380 | Equity/peak no se inicializan desde el exchange ni se persisten; `wb` sin unrealized |
| F08 | P1 | main.py:619-624 / strategies/*:_check_exit | SL/TP de exchange no notifican a la estrategia en live; sin estado tras reinicio → sin trailing/software SL |
| F09 | P1 | main.py:520-547 | Gates de régimen/performance antes de `generate_signals` → sin gestión de salidas en BREAKOUT/UNKNOWN |
| F10 | P1 | strategies/fibonacci_retracement.py:240-253,352 | Impulsos no caducan ni se consumen: re-entrada en el mismo impulso fallido |
| F11 | P2 | strategies/mean_reversion.py:210-221 | `volatility_percentile` no existe (es `vol_pct` 0-1) → umbrales adaptativos muertos |
| F12 | P2 | strategies/base.py:97-109 | Regresión: fricción restada del risk_amount (lessons #24) |
| F13 | P2 | strategies/base.py:110-114 / portfolio_manager.py:168-189 | Sizing dominado por cap `alloc×leverage` ($250): riesgo real 0.04-0.06% vs 1.5-4% configurado; 50% equity inasignable; min notional 20 vs 100 Binance BTC |
| F14 | P2 | risk/risk_manager.py:296-306 | `max_total_exposure_pct=0.6` = 300% del equity |
| F15 | P2 | risk/risk_manager.py:165-186 / settings.py:100-101 | Filtro de funding recorta todo long al funding base (0.01%) |
| F16 | P2 | core/market_data.py:117-179 | Seed incluye la vela en formación; timestamps open vs close mezclados |
| F17 | P2 | strategies/*/_resample_* | Resampleo posicional re-cortado cada minuto: no son barras 5m/15m de reloj |
| F18 | P2 | core/market_data.py:400-406 / indicators.py:96-112 | `compute_all` 169 ms en callback WS → event loop bloqueado ~0.7 s/min |
| F19 | P2 | core/quant_models.py / risk_manager.py:199-214 | Modelos cuant inertes (sin persistencia); vol-scalar aplicado tras el cap de leverage |
| F20 | P2 | core/microprice.py:228-233 | Regresión: microprice compuesto clampeado a [bid, ask] (lessons #24) |
| F21 | P2 | execution/order_engine.py:377,427-433 | Live registra `rp` bruto por fill parcial; paper neto por round-trip |
| F22 | P2 | config/settings.py:50-52 / microstructure.py:890-895 | VPIN bucket 50k USD en BTC ≈ ventana de segundos; filtro MR sin calibrar |
| F23 | P3 | core/market_data.py:310-318 | `refresh_all` reemplaza el snapshot → pierde orderbook/regime 30 s |
| F24 | P3 | strategies/mean_reversion.py:319-320 | Metadata registra constantes en vez de sl/tp per-symbol |
| F25 | P3 | risk/risk_manager.py:337-352,501-513 | Property con side-effect; SL estrechado en DD incoherente con la estrategia |
| F26 | P3 | portfolio_manager.py:62-67,231-253 | `SYMBOL_STRATEGY_MAP` hardcoded vs allocations config; exclusión silenciosa |
| F27 | P3 | main.py:535,727-748 | `_positions` live solo por REST/2 s: ventana de doble entrada si REST falla >60 s |
| F28 | P3 | core/market_data.py:347-349 | Sin seed, barras 1m desalineadas del reloj |

**Totales:** P0 = 3 · P1 = 7 · P2 = 12 · P3 = 6 · **28 hallazgos**.

## Veredicto del quant

1. La matemática de la señal MR es coherente en la forma (pullback RSI en tendencia + 2 confirmaciones semi-ortogonales, SL/TP en ATR) pero **no en el fondo**: con ATR real de hoy el R:R neto en ETH es 1.0:1 y el "filtro de tendencia 1H" que sostiene toda la tesis no filtra nada (ADX pasa el 100% del tiempo). Lo que corre en producción es "momentum EMA12/26 de ≤33 horas + RSI<35 en un corte 1m re-agrupado de 5". Nadie ha validado eso.
2. Fib tiene mejor higiene (puerta de R:R neto ≥1.5, SL estructural), pero su expiración y consumo de impulsos están muertos, así que opera niveles rancios y reentra en impulsos fallidos.
3. El riesgo real por trade es 0.04-0.06% del equity: la cuenta no puede reventar por una mala racha, pero **sí por fricción**: $0.28 de coste por $250 de notional sobre movimientos esperados de $0.4-1. Con ese tamaño no se acumulan ni evidencia ni capital; la lección "ATR/fee ≥ 2×" está escrita y no aplicada.
4. Todo el "risk framework" (Kelly, RoR, vol-targeting, correlación, consecutive-loss sizing) es decorativo: nunca alcanza a modificar el tamaño, no persiste entre reinicios y su estado se pierde cada deploy.
5. Los tres P0 son de **ingeniería, no de alpha**: cancelar protecciones sin cerrar posiciones (shutdown y halt por DD), salidas de Fib mal enrutadas en live, y un factor de performance que apaga la estrategia para siempre tras 5 trades mediocres y además desactiva las salidas. Cualquiera de ellos puede costar más que todo el edge teórico del año.
6. Paper ≠ live en cuatro puntos medibles (F02, F08, F21, F07): la validación en paper no reproduce lo que hará live tras el primer SL de exchange o el primer reinicio.
7. Orden de ataque: (i) F01+F02 hoy mismo (una tarde: `close_all_positions` en shutdown/halt, `startswith("exit")` en el engine); (ii) F03+F09 (separar `check_exit` de los gates; normalizar `_performance_factor`); (iii) F07+F08 (equity real al arrancar, persistencia de peak, `notify_external_exit` en live, reconstrucción de estado); (iv) F04+F10 (timestamps en vez de `len`); (v) F05+F06 (klines 1H reales, ADX bien calentado, puerta net R:R ≥1.5 y ATR ≥ 2× coste — esto dejará a ETH fuera la mayor parte del tiempo, y eso es lo correcto).
8. Después de (v), re-backtestear con barras 5m/15m alineadas a reloj (F17): los PF 0.85/1.11 que justifican `SYMBOL_STRATEGY_MAP` se obtuvieron con un resampleo que no corresponde a ningún gráfico real.
9. Hasta entonces, mi recomendación como quant: **no pasar a live**; paper con F01-F03 corregidos y logging de `net_rr`, `atr_bps`, `h1_adx`, `bars_held` reales en cada señal para poder auditar con datos.
10. Lo que sí está bien y hay que conservar: indicadores RSI/ATR Wilder correctos en régimen estacionario, guards de ticks, circuit breaker sin re-armado, cierre de emergencia si fallan ambas protecciones, mapa de símbolos único, `asyncio.Lock` en el risk manager, y una suite de tests que pasa (36/36) aunque no cubra nada de lo anterior.
