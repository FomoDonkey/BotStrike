# Auditoría R2 — risk_sizing (Riesgo y sizing numérico)

**Fecha:** 2026-08-31 · **Auditor:** agente quant R2
**Alcance:** `risk/risk_manager.py`, `portfolio/portfolio_manager.py`, `core/quant_models.py`, `strategies/base.py` (sizing) + call sites reales en `strategies/mean_reversion.py`, `main.py`, `execution/order_engine.py`, `execution/paper_simulator.py`, `exchange/binance_client.py`, `backtesting/backtester.py`, `deploy/botstrike-bridge.service`.
**Método:** lectura línea a línea + **reproducción numérica con `py -3.12` instanciando las clases reales** (`Settings()`, `RiskManager`, `PortfolioManager`, `MeanReversionStrategy`) con datos de mercado **en vivo de Binance USDT-M del 2026-08-31 ~06:55 UTC**, filtros reales de `GET /fapi/v1/exchangeInfo`, histórico real de `GET /fapi/v1/fundingRate` (500 settlements ≈ 166 días) y documentación oficial de Binance para la semántica de `walletBalance`/`marginBalance`.
Suite de tests verde antes de auditar: `112 passed in 2.48s`.
Referencias ronda 1: `01-Fxx` = `tasks/audit/01_core_strategy_risk.md`; `02-NN` = `tasks/audit/02_exchange_execution.md`.

---

## Datos de mercado usados (reales, no supuestos)

| símbolo | precio | ATR14 5m | ATR bps | minNotional | minQty | stepSize | tickSize |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 77 621.10 | 96.99 | 12.49 | **50** | 0.001 | 0.001 | 0.10 |
| ETHUSDT | 2 417.37 | 3.417 | 14.14 | **20** | 0.001 | 0.001 | 0.01 |
| SOLUSDT | 101.50 | 0.2650 | 26.11 | **5** | 0.01 | 0.01 | 0.01 |
| ADAUSDT | 0.1936 | 0.000514 | 26.56 | **5** | 1 | 1 | 0.0001 |

> El `minNotional` real de BTCUSDT en **futuros** es **50 USDT**, no 100 (el enunciado de la tarea asumía 100; el fallback `DEFAULT_SYMBOL_FILTERS["BTCUSDT"]` del repo también dice 100 → más conservador que la realidad, pero desalineado).

---

## Tabla señal → tamaño **verificada** (clases reales, equity $1 000, régimen RANGING, MR, sin posiciones abiertas)

`PortfolioManager.get_allocation` → `BaseStrategy._calc_position_size` → `RiskManager.validate_signal`

| símbolo | alloc $ | SL dist (bps) | size base.py | **final tras `validate_signal`** | riesgo $ @SL | **riesgo % equity** | lev s/ alloc | lev s/ equity | restricción que muerde | ¿pasa minNotional? |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC-USD¹ | 162.50 | 18.74 | $325.00 | **$325.00** | 0.609 | **0.061 %** | 2.00× | 0.33× | `alloc × leverage` | sí ($50) |
| ETH-USD | 162.50 | 21.20 | $325.00 | **$325.00** | 0.689 | **0.069 %** | 2.00× | 0.33× | `alloc × leverage` | sí ($20) |
| SOL-USD | 162.50 | 47.00 | $325.00 | **$250.00** | 1.175 | **0.117 %** | 1.54× | 0.25× | `max_position_usd=250` | sí ($5) |
| ADA-USD | 162.50 | 53.13 | $325.00 | **$150.00** | 0.797 | **0.080 %** | 0.92× | 0.15× | `max_position_usd=150` | sí ($5) |

¹ BTC ya no opera (`SYMBOL_STRATEGY_MAP["BTC-USD"] = set()` desde `fb073a1`); la fila es el sizing que saldría si se reactivara. `allocated=162.5` coincide con el valor real registrado en `smoke_live.json`.

**Agregados verificados:**

- **Exposición total 4 posiciones = $1 050 = 105 % del equity.** Margen inicial a 2× = **$525 = 52,5 % del equity**.
- Cap de `_check_total_exposure` = `1000 × 0.6 × 5` = **$3 000 = 300 % del equity** → **01-F14 confirmado y además el chequeo es CÓDIGO MUERTO** (ver `risk_sizing-04`).
- **Riesgo simultáneo si las 4 pegan SL = $3.27 = 0,33 % del equity.** `max_daily_loss` = $50, `max_drawdown` = $100.
- **Riesgo configurado por trade: 1,5 % = $15,00. Riesgo real entregado: $0,61–$1,18 = 0,061–0,117 %.** Factor **12,7×–24,6×**. 01-F13 sigue abierto y peor que en ronda 1.
- Hacen falta **42–82 pérdidas completas en un día** para tocar el límite diario, y **85–164** para el max drawdown. Ambos son inalcanzables por trading normal.

---

## Hallazgos

### [P0] risk_sizing-01 — Risk of Ruin pausa TODAS las entradas, de forma permanente y sin alerta, a partir del trade cerrado nº30. Con el rendimiento documentado del propio repo el disparo es SEGURO, y una vez pausado nunca se recalcula (deadlock hasta reiniciar el proceso)
**Archivo:** `core/quant_models.py:344-366` (`RiskOfRuin.compute`), `risk/risk_manager.py:198-207` (gate), `risk/risk_manager.py:459` (único caller de `compute`)
**Línea:** `core/quant_models.py:348`
**Evidencia (código real):**
```python
# core/quant_models.py:345-364
edge = win_rate * (avg_win / avg_loss) - (1.0 - win_rate)
if edge <= 0:
    ror = 1.0                      # escalón: sin edge muestral -> ruina "segura"
elif edge >= 1.0:
    ror = 0.0
else:
    capital_units = (current_equity * self.max_drawdown_pct) / avg_loss
    ratio = (1.0 - edge) / (1.0 + edge)
    if capital_units > 300: ror = 0.0
    else: ror = ratio ** capital_units
# risk/risk_manager.py:199-203
ror = self.risk_of_ruin.current
if ror.should_pause and ror.sample_size >= self.risk_of_ruin.min_trades:
    logger.warning("ror_pause_active", ...); return None      # TODAS las entradas, TODOS los símbolos
# risk/risk_manager.py:459  (record_trade_result — ÚNICO sitio que llama a compute)
self.risk_of_ruin.compute(self._current_equity)
```
Ejecutado con las clases reales (`min_trades=30` por defecto — **no configurable**, `max_dd=0.10`, `pause=0.10`):
```
avg_loss=$1.00 (riesgo real medido hoy): capital_units = 1000*0.10/1.00 = 100.0
   edge mínimo para NO pausar:   0.01151
   edge mínimo para NO throttle: 0.01753
avg_loss=$2.07: capital_units = 48.3 -> edge > 0.02383 para no pausar

WR=0.39 payoff=1.50: edge=-0.0250 ror=1.0000 throttle=True PAUSE=True
WR=0.50 payoff=1.01: edge=+0.0050 ror=0.3679 throttle=True PAUSE=True   <- edge POSITIVO y pausa
WR=0.45 payoff=1.25: edge=+0.0125 ror=0.0821 throttle=True PAUSE=False
WR=0.50 payoff=3.00: edge=+1.0000 ror=0.0000 throttle=False PAUSE=False <- rama edge>=1 (sin sentido)

E2E RiskManager real, 30 cierres con la WR observada en paper (20 %):
   ror=1.0  edge=-0.5  pause=True  n=30
   validate_signal(señal MR válida de ETH) -> None
```
Monte Carlo (4 000 muestras de 30 trades, clase real, `default_rng(11)`) — **P(pausa en el trade 30) con edge VERDADERO positivo**:
`WR45/b1.5 (+0.125) → 35.1 %` · `WR50/b1.3 (+0.150) → 28.3 %` · `WR40/b2.0 (+0.200) → 29.8 %` · `WR55/b1.2 (+0.210) → 13.7 %` · `WR50/b2.0 (+0.500) → 4.9 %`.
**Por qué:**
1. **La fórmula está mal aplicada.** La ruina del jugador `((1−e)/(1+e))^u` exige que `e` sea la ventaja en PROBABILIDAD (`p−q`) de una apuesta binaria de tamaño fijo. Aquí `e` es la esperanza en múltiplos de R, que puede valer >1 → existe la rama `edge>=1 → ror=0` que no tiene interpretación probabilística. Y hay un **acantilado adicional**: `capital_units > 300 → ror = 0.0`, es decir, si el riesgo medio por trade baja de $0.333 el modelo declara ruina imposible sea cual sea el edge (con edge>0).
2. **Con la evidencia del propio repo el disparo es seguro, no probable.** MR tiene PF 0.85/0.86 en backtest y 20 % WR / −$2.11 en paper (`portfolio_manager.py:69-72`, `config/settings.py:96-98`) ⇒ `edge ≤ 0` ⇒ `ror = 1.0` ⇒ pausa en el trade 30.
3. **El gate es GLOBAL**: pausa todas las estrategias y todos los símbolos, no la estrategia culpable.
4. **Deadlock.** Pausado ⇒ no hay entradas ⇒ no hay fills ⇒ `record_trade_result` no se llama ⇒ `compute` no se recalcula ⇒ **pausa permanente hasta reiniciar el proceso**. Es exactamente el bug que 01-F03 arregló para el performance factor (con `PERF_BLOCK_COOLDOWN_SEC`) y que aquí quedó sin tocar.
5. **No hay alerta.** `grep -rn notify_risk_event` → el ÚNICO evento notificado es `max_drawdown` (`main.py:764`). `ror_pause_active` es un `logger.warning`. El bot queda vivo (health OK, WS conectado, señales generadas, `/status` verde) y **no opera**, en silencio.
6. En live el problema llega **~3× antes**: `execution/order_engine.py:523` llama a `record_trade_result_safe` **por fill parcial** (01-F21 sigue abierto), así que un cierre en 3 parciales cuenta como 3 "trades" para el `min_trades=30`.
   Las salidas sí pasan (`validate_signal` corta antes, línea 121), así que no deja posiciones desnudas: el motor se convierte en un no-op silencioso.
**Fix:** (a) sustituir el escalón por `compute_empirical` (bootstrap, ya escrito y **sin ningún caller** — `grep -rn compute_empirical` → 0 usos fuera de su definición) exigiendo que el límite superior del IC supere el umbral; (b) cooldown/probation como en 01-F03 (`ror_blocked_since` + ventana limpia al expirar); (c) RoR **por estrategia** y `min_trades` configurable y mucho mayor (30 trades no estiman una WR: SE(WR)≈9 pp); (d) `notify_risk_event("ror_pause", …)` obligatorio; (e) agregar los fills parciales a un único trade cerrado antes de alimentar el modelo. Test: 30 pérdidas ⇒ pausa; +3600 s ⇒ entrada permitida en probation.
**Verificado cómo:** `py -3.12` con `RiskOfRuin` y `RiskManager` reales (E2E `validate_signal → None`), umbrales de `edge` resueltos por bisección y comprobados contra la clase, Monte Carlo con `numpy.default_rng(11)`; `grep -rn "risk_of_ruin.compute\|compute_empirical"` → único caller `record_trade_result:459`; `grep -rn notify_risk_event` → solo `max_drawdown`.

### [P1] risk_sizing-02 — El freno por rachas es GLOBAL y exponencial sin suelo: con la WR observada (20 %) recorta el tamaño un 34 % de media y deja el 13–26 % de las entradas por debajo del `minNotional` de Binance. Paper las rellena y live las rechaza → el soak de CT 104 mide un sistema que en live no habría operado. Con ≥9 pérdidas seguidas el bot queda muerto de forma irreversible
**Archivo:** `risk/risk_manager.py:289-295` (reducción), `risk/risk_manager.py:435-452` (reset solo con `pnl > 0`), `execution/paper_simulator.py:451` (sin filtros), `exchange/binance_client.py:495-501` (con filtros)
**Línea:** `risk/risk_manager.py:290`
**Evidencia (código real):**
```python
# risk/risk_manager.py:290-292
if self._consecutive_losses >= 4:
    reduction = 0.5 ** (self._consecutive_losses - 3)
    adjusted_size *= reduction
# risk/risk_manager.py:449-451  — UNICA salida del contador
elif pnl > 0:
    self._consecutive_losses = 0
# execution/paper_simulator.py:451  — paper NO comprueba nada
size = signal.size_usd / price
# exchange/binance_client.py:497-500 — live SI
if notional < f["minNotional"]:
    raise ValueError(f"notional ... below minNotional ...")
```
Ejecutado (clases reales, `_consecutive_loss_pause=False` para simular cooldown expirado, `minNotional` reales de `exchangeInfo`):
```
  n |  BTC-USD (min $50) |  ETH-USD (min $20) |  SOL-USD (min $5) |  ADA-USD (min $5)
  4 | $162.50         OK | $162.50         OK | $125.00        OK | $ 75.00        OK
  6 | $ 40.62 REJ<minNot | $ 40.62         OK | $ 31.25        OK | $ 18.75        OK
  8 | $ 10.16 REJ<minNot | $ 10.16 REJ<minNot | $  7.81        OK | $  4.69 REJ<mN
  9 | $  5.08 REJ<minNot | $  5.08 REJ<minNot | $  3.91 REJ<mN  | $  2.34 REJ<mN   <- TODO rechazado
 12 | $  0.63            | $  0.63            | $  0.49           | $  0.29
```
Simulación de 200 000 trades (Bernoulli; multiplicador visto en el momento de la entrada):
```
  WR=20%: E[mult]=0.658  P(mult<1)=41.0%  P(mult<=1/16)=21.1%  P(size<minNotional): BTC=26.3% ETH=16.9% SOL=13.5% ADA=16.9%
  WR=30%: E[mult]=0.816  P(mult<1)=23.9%  P(mult<=1/16)= 8.3%  P(size<minNotional): BTC=11.8% ETH= 5.8% SOL= 4.1% ADA= 5.8%
  WR=40%: E[mult]=0.907  P(mult<1)=13.0%  P(mult<=1/16)= 2.8%  P(size<minNotional): BTC= 4.6% ETH= 1.7% SOL= 1.0% ADA= 1.7%
Estado real: tras 12 perdidas -> consecutive_losses=12, reduccion 0.5**9 = 0.00195
  un fill con pnl=0 (ENTRY) NO resetea; check_daily_reset() (medianoche UTC) NO resetea
```
**Por qué:**
(a) **Divergencia paper↔live cuantificada.** Con la WR de 20 % observada en paper, **entre el 13,5 % y el 26,3 % de las entradas se rellenarían en paper y serían rechazadas en live** por `minNotional`. Las estadísticas de WR/PF que salen del soak —y que alimentan Kelly, RoR y el performance factor— **no son transferibles a live**. Esto invalida la evidencia con la que se decidiría ir a real.
(b) **Deadlock a partir de 9 pérdidas seguidas**: ninguna entrada supera el `minNotional` de ninguno de los 3 símbolos operables → `place_order` lanza `ValueError` antes de tocar la red → no hay fill → no hay `pnl>0` → `_consecutive_losses` nunca baja → **el bot queda muerto hasta reiniciar el proceso**. Con WR 20 %, P(racha ≥9) ≈ 13 % por racha iniciada.
(c) El contador es **GLOBAL a todos los símbolos y estrategias**: 4 pérdidas seguidas en ADA reducen a la mitad el tamaño de BTC, ETH y SOL. Con 3 símbolos MR correlacionados en el mismo régimen, las rachas conjuntas son la norma.
(d) Entre n=4 y n=8 el bot opera con $5–$162 de notional donde la comisión round-trip (11 bps) es un porcentaje creciente del edge: se sigue perdiendo por fricción mientras se "protege".
(e) **No hay alerta**: `consecutive_loss_pause` es `logger.warning`; el `ValueError` de `minNotional` se registra como error de orden genérico.
**Fix:** (1) **suelo de tamaño**: inyectar el `min_notional` por símbolo (ya cacheado en `binance_client._symbol_filters`) en el `RiskManager`; si `adjusted_size < min_notional` → **rechazar la entrada explícitamente y notificar**, nunca emitir una orden imposible. (2) Replicar `minQty`/`stepSize`/`minNotional` en `paper_simulator` (sin esto ningún número del soak vale). (3) Cap del exponente: `0.5 ** min(self._consecutive_losses - 3, 3)` → suelo 0.125. (4) Decaer el contador por tiempo (p. ej. −1 al expirar el `consecutive_loss_pause`) y llevarlo por símbolo, no global. (5) `notify_risk_event("consecutive_loss_pause")`.
**Verificado cómo:** `py -3.12` con `RiskManager`/`Settings` reales por símbolo y `n` de 4 a 12; `minNotional`/`minQty`/`stepSize` leídos en vivo de `GET /fapi/v1/exchangeInfo`; simulación Bernoulli de 200 000 trades con `default_rng(42)`; lectura de `paper_simulator._execute_one:451` (sin ninguna comprobación de filtros) y `binance_client:463,497`.

### [P1] risk_sizing-03 — Ningún estado de riesgo se persiste, y el servicio corre con `Restart=always` / `RestartSec=10` + watchdog interno: cada reinicio pone a cero el límite de pérdida diaria, el circuit breaker, el halt por drawdown y el contador de rachas
**Archivo:** `risk/risk_manager.py:40-54`, `deploy/botstrike-bridge.service`, `server/bridge.py:106-108`
**Línea:** `risk/risk_manager.py:40`
**Evidencia (código real):**
```python
# risk/risk_manager.py:40-54
self._equity_peak: float = self.config.initial_capital   # constante de CONFIG, no del exchange
self._current_equity: float = self.config.initial_capital
self._daily_pnl: float = 0.0
self._consecutive_losses: int = 0
self._circuit_breaker_active: bool = False
self._consecutive_loss_pause: bool = False
self._drawdown_halted: bool = False
self._last_daily_reset_date: str = ""
```
```
# server/bridge.py:106-108
# Internal watchdog: ticks older than 300 s on 3 consecutive checks -> restart engine in-process
# with backoff; after 5 attempts inside a 10-min window -> os._exit(3) so systemd (Restart=always) restarts us.
# deploy/botstrike-bridge.service
Restart=always
RestartSec=10
```
`grep -rn "risk_state\|persist\|json.dump" risk/ portfolio/ core/quant_models.py main.py` → **0 resultados**: no existe ninguna serialización del estado de riesgo.
Estado tras reinicio (verificado instanciando `RiskManager(Settings())`): `_equity_peak=1000.0`, `_daily_pnl=0.0`, `_consecutive_losses=0`, `_circuit_breaker_active=False`, `_drawdown_halted=False`, `_last_daily_reset_date=""`, colas de RoR/Kelly/VolTargeting/Correlation vacías.
**Por qué:** todos los frenos del sistema son **volátiles** y el despliegue está diseñado para **reiniciar solo**:
- `max_daily_loss_pct = 0.05` ($50/día) **no es aplicable**: cualquier reinicio (deploy, watchdog por ticks stale, crash) pone `_daily_pnl = 0` y el presupuesto diario vuelve entero. Con `RestartSec=10`, el "límite diario" dura lo que dure el proceso.
- El circuit breaker (`_circuit_breaker_active`, cooldown 300 s) y `_drawdown_halted` se levantan solos al reiniciar: el bot vuelve a operar sin haber recuperado nada.
- `_consecutive_losses` vuelve a 0 → se pierde el freno por rachas.
- `_equity_peak = initial_capital` (constante de config, no del exchange): **01-F07 sigue totalmente abierto** — `get_account()`/`get_balances()` existen en `binance_client.py:602-605` pero `grep` confirma **cero callers**. En live, con una cuenta real de $850, cada reinicio arranca con `peak=1000` y `dd=15 % ≥ 10 %` → **halt inmediato y permanente**; con $1 500 se pierde el peak y el 10 % ya no protege lo ganado.
- Los modelos cuant (01-F19) nunca acumulan historia: `sqlite3 data/trade_database.db` → **`sessions=5, trades=0`**.
**Fix:** persistir en `data/risk_state.json` (escritura atómica en cada `record_trade_result` y cada N s): `equity_peak`, `daily_pnl`, `last_daily_reset_date`, `consecutive_losses`, `circuit_breaker_until`, `drawdown_halted` y las colas de PnL de RoR/Kelly; recargarlo en `RiskManager.__init__`. En live, sembrar `update_equity()` en `start()` desde `GET /fapi/v2/account` (`totalMarginBalance`). Test: matar el proceso con `daily_pnl=-40` y 5 pérdidas seguidas ⇒ al arrancar el estado se conserva.
**Verificado cómo:** lectura del constructor + `grep -rn "risk_state\|persist\|json.dump"` (0 hits); `grep -rn "get_account()\|get_balances()"` (0 callers); `cat deploy/botstrike-bridge.service`; `sqlite3 data/trade_database.db`; instanciación de `RiskManager` y volcado de los 11 atributos de estado.

### [P1] risk_sizing-04 — El tope de exposición del 60 % es inaplicable: `_check_total_exposure` es CÓDIGO MUERTO (cap $3 000 vs máximo alcanzable $1 300), `Settings.__post_init__` valida por símbolo pero no la suma, y el chequeo de margen del 50 % es por señal y no agregado → hasta 130 % de notional y 65 % de margen con el "cap del 60 %" activo
**Archivo:** `risk/risk_manager.py:313-322`, `risk/risk_manager.py:276-284`, `config/settings.py:231-251`, `config/settings.py:85`
**Línea:** `risk/risk_manager.py:321`
**Evidencia (código real):**
```python
# risk/risk_manager.py:320-322
total_exposure = sum(p.notional for p in self._positions.values())
max_exposure = self._current_equity * self.config.max_total_exposure_pct * self.config.max_leverage
return (total_exposure + signal.size_usd) > max_exposure          # 1000 * 0.6 * 5 = $3000
# risk/risk_manager.py:277-282  — POR SENAL, nunca agregado
max_lev = min(sym_config.leverage, self.config.max_leverage)
required_margin = adjusted_size / max_lev
if required_margin > self._current_equity * 0.5:
    adjusted_size = min(adjusted_size, self._current_equity * 0.5 * max_lev)
# config/settings.py:234-241 — valida CADA simbolo, nunca la SUMA
for sym in self.symbols:
    if sym.max_position_usd > max_exposure_usd:   # 1000*0.6 = 600
        raise ValueError(...)
```
Ejecutado:
```
cap _check_total_exposure                          = $3000  (300 % del equity)
suma de max_position_usd (500+400+250+150)         = $1300  <- exposicion maxima ALCANZABLE
size_usd maximo que puede llegar (base.py)         = $487.50 (alloc 243.75 x lev 2)
=> disparar el cap exige total_exposure > $2512 > $1300  => CHEQUEO IMPOSIBLE DE DISPARAR

margen por senal: solo recorta si size > equity*0.5*max_lev = $1000
4 posiciones hoy: notional $1050 -> margen @2x = $525.00 = 52.5 % del equity (> el 50 % que la regla pretende)
peor caso (todas al max_position_usd): notional $1300 -> margen $650 = 65 % del equity

perf_factor con 25 cierres de +$20 = 1.482  ->  suma de allocations = $963.31 = 96.3 % del equity
   (REGIME_WEIGHTS[RANGING][MR] dice 65 %)  ->  notional implicito $1927
```
**Por qué:** hay **tres** capas que deberían limitar la exposición y **ninguna** lo hace.
1. `max_total_exposure_pct = 0.6` está documentado como "60% max exposure" y `Settings.__post_init__` lo usa así (`max_exposure_usd = 600`), pero `_check_total_exposure` lo multiplica por `max_leverage` → 300 %. **Además el cap resultante es inalcanzable**: la función siempre devuelve `False`. 01-F14 no solo sigue abierto — el chequeo es *dead code*, más grave de lo reportado en ronda 1.
2. El validador de coherencia comprueba `max_position_usd ≤ 600` **por símbolo**, pero la **suma** de los 4 caps es $1 300 = **130 % del equity**, 2,2× el tope que él mismo declara.
3. La regla "nunca más del 50 % del equity en margen" se evalúa **por señal**; con `max_open_positions = 4` el margen agregado llega al 52,5 % hoy y al 65 % en el peor caso, sin que nadie lo mire.
4. `PERF_CEIL = 1.5` **multiplica** el presupuesto de régimen en vez de reasignar dentro de él: el 65 % declarado se convierte en el **96,3 % del equity** asignado a MR cuando la estrategia va bien.
**Fix:** (a) `max_exposure = equity * max_total_exposure_pct` (sin `max_leverage`) y documentar si 0.6 es notional o margen; (b) validar en `__post_init__` también `sum(max_position_usd) <= initial_capital * max_total_exposure_pct`; (c) convertir el chequeo de margen en agregado: `(Σ notional + size) / max_lev <= equity * margin_budget`; (d) `PERF_CEIL` debe reasignar dentro del presupuesto, no ampliarlo. Test: 4 posiciones a `max_position_usd` ⇒ la 4ª se rechaza o se recorta.
**Verificado cómo:** `py -3.12` con `Settings`/`RiskManager`/`PortfolioManager` reales; suma de allocations medida con `get_allocation` sobre los 4 símbolos con `perf_factor=1.0` ($650, correcto) y con el techo alcanzado ($963.31); imposibilidad del cap demostrada contra el `size_usd` máximo que `base.py` puede producir.

### [P1] risk_sizing-05 — Volatility Targeting está estructuralmente clavado en `max_scalar = 1.5`: mide la vol de un equity que solo se mueve con PnL REALIZADO de $0.6–$1.2 por trade, así que la vol medida (2–7 % anual) nunca alcanza el objetivo del 15 %. Resultado: **infla el 50 % TODAS las posiciones**, y se aplica DESPUÉS del cap de apalancamiento
**Archivo:** `core/quant_models.py:113-133` (`_compute`), `core/quant_models.py:71-92` (`on_equity_update`), `risk/risk_manager.py:209-212` (aplicación), `main.py:640-643` / `main.py:376-381` (únicas fuentes de equity)
**Línea:** `core/quant_models.py:124`
**Evidencia (código real):**
```python
# core/quant_models.py:118-126
returns = np.array(list(self._daily_returns))[-self.lookback_days:]
realized_vol = float(np.std(returns, ddof=1)) * math.sqrt(self.annualization)   # 365
scalar = self.target_vol / realized_vol            # 0.15 / vol
scalar = max(self.min_scalar, min(self.max_scalar, scalar))   # clamp [0.5, 1.5]
# core/quant_models.py:82-86  — solo registra un return al CAMBIAR el dia UTC
if self._last_equity > 0 and current_date != self._last_date and self._last_date:
    daily_ret = (equity - self._last_equity) / self._last_equity
# risk/risk_manager.py:210-212  — se aplica DESPUES del cap alloc*leverage de base.py
vol_scalar = self.vol_targeting.scalar
if vol_scalar != 1.0: signal.size_usd *= vol_scalar
```
Ejecutado (clase real `VolatilityTargeting(0.15, 20, 0.5, 1.5)`, 21 días):
```
 sigma_d=0.10% -> vol_anual= 2.21% scalar=1.500
 sigma_d=0.20% -> vol_anual= 4.41% scalar=1.500
 sigma_d=0.30% -> vol_anual= 6.62% scalar=1.500
 sigma_d=0.50% -> vol_anual=11.03% scalar=1.360
 sigma_d=0.79% -> vol_anual=17.43% scalar=0.861
=> el scalar solo baja de 1.5 si la vol DIARIA DEL EQUITY supera 0.785 % (= 15 %/sqrt(365)),
   es decir >= $7.85 de PnL neto diario sobre $1000. Con riesgo real de $0.61-$1.18 por trade
   harian falta ~7-13 trades a SL completo EN UN DIA, y sin que se compensen entre si.

Efecto en el sizing (RiskManager real, ETH):
  vol_scalar=0.5 -> final=$162.50  lev sobre alloc = 1.00x
  vol_scalar=1.0 -> final=$325.00  lev sobre alloc = 2.00x
  vol_scalar=1.5 -> final=$400.00  lev sobre alloc = 2.46x   (sym_config.leverage = 2)

Dias saltados (on_equity_update solo se llama en FILLS):
  updates en dias 1, 2 y 10 con -5% acumulado -> _daily_returns = [0.0, -0.05]
  el -5 % de OCHO dias queda registrado como un unico return DIARIO
```
**Por qué:**
1. **Sesgo sistemático hacia el techo.** El equity solo cambia con PnL realizado (`main.py:640-642` en paper; `ACCOUNT_UPDATE.wb` en live) y el PnL por trade es de $0.6–$1.2 sobre $1 000. La σ diaria del equity es de ~0,1–0,3 % ⇒ vol anualizada 2–7 % ⇒ `scalar = 0.15/0.04 ≈ 3.7` ⇒ **clamp a 1.5 siempre**. El módulo cuyo propósito es *reducir* exposición cuando sube la vol acabará *aumentándola* un 50 % de forma permanente en cuanto reúna 5 returns.
2. **Se aplica después del cap de apalancamiento** (01-F19 sigue abierto): `base.py` limita a `alloc × sym_config.leverage` (2×) y luego `validate_signal` multiplica por 1.5 → apalancamiento efectivo 2,46× sobre la asignación (o 3× si `max_position_usd` no muerde). El `leverage=2` declarado por símbolo se incumple silenciosamente.
3. **Los "daily returns" no son diarios.** `on_equity_update` solo se llama en fills y solo registra un return al cambiar el día UTC, así que un salto de N días se anota como un único return diario. Anualizar eso con `sqrt(365)` es incorrecto por construcción y **subestima aún más la vol**, empujando el scalar al techo.
4. `annualization = 365` es correcto para cripto (coherente con `analytics/performance.py:144`), pero anualizar mal-etiquetados no lo arregla.
**Fix:** (a) alimentar `on_equity_update` desde el bucle de riesgo con **mark-to-market** (`wallet + Σ unrealized`), no solo con fills; (b) exigir que el gap entre returns sea de 1 día y, si no, escalar `daily_ret / sqrt(n_días)` o descartarlo; (c) requerir `min_samples` mucho mayor (20 días reales) antes de dejar que el scalar se aleje de 1.0; (d) aplicar el scalar **antes** del cap `alloc × leverage`, o recortar a posteriori con `min(size, alloc*leverage)`. Test: 20 returns de σ=0.2 % ⇒ el scalar debe quedarse en 1.0, no en 1.5.
**Verificado cómo:** `py -3.12` con `VolatilityTargeting` real (6 escenarios de σ), `RiskManager.validate_signal` real con `_result` forzado a scalar 0.5/1.0/1.5, y reproducción del salto de días con `datetime` UTC explícito. Umbral 0.785 % = 0.15/√365 comprobado analítica y numéricamente.

### [P1] risk_sizing-06 — El sizer NO es un sizer de riesgo: la restricción que muerde es `allocated_capital × leverage`, no `risk_per_trade_pct`. Entrega 0,061–0,117 % del equity por trade frente al 1,5 % configurado (12,7×–24,6×), y el cambio de régimen ocurre en SL ≈ 62 bps
**Archivo:** `strategies/base.py:90-121`, `risk/risk_manager.py:347-367`, `main.py:549-556`, `config/settings.py:87`
**Línea:** `strategies/base.py:113`
**Evidencia (código real):**
```python
# strategies/base.py:90-114  — riesgo sobre el capital ASIGNADO, y cap por apalancamiento
risk_pct = kelly_risk_pct if kelly_risk_pct is not None else self.trading_config.risk_per_trade_pct
risk_amount = capital * risk_pct                       # 162.50 * 0.015 = $2.4375
...
size_units = adjusted_risk / risk_per_unit
max_units = (capital * leverage) / price               # 162.50 * 2 / price   <- ESTE es el que manda
final_size = min(size_units, max_units)
# risk/risk_manager.py:348-365 — riesgo sobre el EQUITY (6.2x mas laxo, nunca muerde)
kelly_pct = self.get_kelly_risk_pct(signal.strategy)
max_risk = self._current_equity * kelly_pct            # 1000 * 0.015 = $15.00
max_size_by_risk = (max_risk / risk_per_unit) * signal.entry_price
size = min(size, max_size_by_risk)
```
Medido con datos reales (ver tabla señal→tamaño):
```
sym       SL(bps)  size base.py  restriccion que muerde   riesgo $   riesgo % equity
BTC-USD    18.74      $325.00    alloc x leverage           0.609        0.061 %
ETH-USD    21.20      $325.00    alloc x leverage           0.689        0.069 %
SOL-USD    47.00      $325.00    alloc x leverage           1.175        0.117 %
ADA-USD    53.13      $325.00    alloc x leverage           0.797        0.080 %
risk_per_trade_pct promete $15.00/trade  ->  factor 12.7x - 24.6x
```
Umbral de conmutación (resuelto analíticamente y comprobado): con `risk_pct=0.015`, `leverage=2` y fricción de 11 bps, la rama de riesgo solo manda si `SL > 61.6 bps` (o `< 13.4 bps`, donde el suelo `risk_amount*0.5` toma el control). Hoy los 4 símbolos están **entre 18,7 y 53,1 bps** ⇒ los 4 en la rama "notional constante".
**Por qué:**
- `risk_per_trade_pct` tiene **dos significados incompatibles**: en `base.py` es "1,5 % del capital ASIGNADO" ($2.44) y en `risk_manager` es "1,5 % del EQUITY" ($15). El cap del risk manager está 7×–25× por encima del tamaño entregado y **nunca es el binding constraint** (`max_size_by_risk` medido: ETH $13 636 con SL 11 bps, $3 000 con SL 50 bps, frente a $325 entregados). 01-F13 sigue abierto y con números peores que en ronda 1.
- Consecuencia cuantitativa: **el tamaño es constante ($325) sea cual sea la volatilidad**, así que el riesgo por trade es proporcional al ATR — es decir, **más riesgo justo cuando el mercado está más volátil**, lo contrario de lo que pretende `vol_target`. En mercados quietos (hoy) los 4 símbolos reciben el mismo notional aunque su ATR difiera 2,1× (12,5 vs 26,6 bps).
- El sistema **conmuta de sizer** en SL ≈ 62 bps sin que nadie lo sepa: mismo código, dos comportamientos completamente distintos según la volatilidad del día. Los backtests y el paper mezclan ambos regímenes.
- Con BTC congelado (`SYMBOL_STRATEGY_MAP["BTC-USD"] = set()`), `symbol_share = 1/len(settings.symbols) = 1/4` sigue repartiendo el 25 % de la asignación de MR a un símbolo que no opera: MR solo despliega `0.65 × 0.75 = 48,75 %` del equity en vez del 65 % que dice `REGIME_WEIGHTS`.
**Fix:** una única fuente de verdad: `risk_amount = equity × risk_per_trade_pct` en `base.py`, con `allocated_capital` degradado a **cap de notional** (`min(size_usd, allocated_capital*leverage)`) y no a base del riesgo; y `symbol_share` calculado sobre los símbolos **elegibles** para esa estrategia (`SYMBOL_STRATEGY_MAP`), no sobre `len(settings.symbols)`. Test: con SL de 20 bps y equity $1 000, el riesgo entregado debe ser $15 ± fricción, no $0.69.
**Verificado cómo:** `py -3.12` recorriendo el pipeline real `get_allocation → _calc_position_size → validate_signal` para los 4 símbolos con precio y ATR14(5m) en vivo; `allocated=162.5` contrastado contra el valor registrado en `smoke_live.json`; umbral de 61,6 bps resuelto de la cuadrática `sl² − 0.0075·sl + 8.25e−6 > 0` y verificado numéricamente.

### [P1] risk_sizing-07 — El fix 01-F03 dejó la puerta de rendimiento MATEMÁTICAMENTE INALCANZABLE: normaliza por el presupuesto de riesgo de CONFIG ($15) cuando el riesgo real es ~$0.8. Perder el 100 % de los trades al SL da `factor = 0.960`; para bloquear haría falta perder $10.99 por trade, 7× más que la pérdida máxima posible
**Archivo:** `portfolio/portfolio_manager.py:219-256`, `portfolio/portfolio_manager.py:79-82`, `portfolio/portfolio_manager.py:310-328`
**Línea:** `portfolio/portfolio_manager.py:243`
**Evidencia (código real):**
```python
# portfolio/portfolio_manager.py:219-224
def _risk_budget_per_trade(self) -> float:
    equity = self.risk_manager.current_equity
    return max(equity * self.config.risk_per_trade_pct, 1e-6)     # 1000 * 0.015 = $15.00
# portfolio/portfolio_manager.py:243-245
avg_r = (sum(closed) / n) / self._risk_budget_per_trade()
factor = 1.0 + 0.5 * math.tanh(1.5 * avg_r)
factor = max(PERF_FLOOR, min(PERF_CEIL, factor))                  # [0.5, 1.5]
# portfolio/portfolio_manager.py:312
if perf < PERF_BLOCK_THRESHOLD:      # 0.6
```
Aritmética verificada con el riesgo real medido hoy:
```
_risk_budget_per_trade() = $15.00     riesgo REAL medido: BTC $0.61 / ETH $0.69 / SOL $1.18 / ADA $0.80
perdida maxima por trade = SL + friccion ~= $1.18 + $0.36 = $1.54
avg pnl/trade = $ -0.80 -> avg_r = -0.053 -> factor = 0.960   no bloquea
avg pnl/trade = $ -1.54 -> avg_r = -0.103 -> factor = 0.923   no bloquea
avg pnl/trade = $-10.99 -> avg_r = -0.733 -> factor = 0.600   limite de bloqueo
=> factor < 0.6 exige avg pnl/trade <= -$10.99, 7.1x la perdida maxima posible por trade
=> PERF_BLOCK_THRESHOLD=0.6 y PERF_FLOOR=0.5 son DECORATIVOS: should_strategy_trade
   no puede devolver False por rendimiento, nunca.
```
**Por qué:** la pérdida por trade está acotada por el stop, así que `avg_r` no puede bajar de ≈−0,10 y `factor` no baja de ≈0,92. La ronda 1 cambió un bug de "desactiva para siempre tras 5 fills" por otro de "no desactiva nunca": el desajuste de unidades de 01-F13 (riesgo configurado 1,5 % vs riesgo entregado 0,08 %) se propagó al normalizador. Además la reducción de allocation también queda anulada: el peor caso realista es ×0,92, irrelevante frente al resto de multiplicadores. **El fix de 01-F03 es incorrecto, no incompleto.**
**Fix:** normalizar por el riesgo REAL, no por el de config: `avg_r = avg_pnl / mean(|entry − SL| × size)` (el `trade_db` ya guarda `entry_price`, `stop_loss` y `size`). Alternativa mínima e inmediata: `avg_r = avg_pnl / std(closed)`, invariante a escala. Test: 30 cierres a −1R real ⇒ `should_strategy_trade == False`.
**Verificado cómo:** `py -3.12` con `PortfolioManager` real (`update_strategy_pnl` ×25 y `_performance_factor` → 1.482 con +$20/trade); umbrales de `avg_r` resueltos invirtiendo `1 + 0.5·tanh(1.5·x) = 0.6`; riesgo real por trade tomado de la tabla señal→tamaño de este mismo informe.

### [P2] risk_sizing-08 — El guard `entry ≈ stop` nuevo (relativo 1e-5) es correcto en cuanto a tamaño (NO abre la puerta a tamaños absurdos) pero sigue sin ser un suelo económico ni comprobar el SIGNO del stop: un LONG con el stop POR ENCIMA de la entrada pasa a tamaño completo y en live deja la posición sin stop loss
**Archivo:** `risk/risk_manager.py:350-365`, `risk/risk_manager.py:369-383` (`_adjust_stop_loss`), `execution/order_engine.py:213-220`, `execution/order_engine.py:318-360`
**Línea:** `risk/risk_manager.py:356`
**Evidencia (código real):**
```python
# risk/risk_manager.py:350-365  (fb073a1)
risk_per_unit = abs(signal.entry_price - signal.stop_loss)     # <- abs(): pierde el SIGNO
if signal.entry_price <= 0 or risk_per_unit / signal.entry_price < 1e-5:
    return 0.0
max_size_by_risk = (max_risk / risk_per_unit) * signal.entry_price
size = min(size, max_size_by_risk)                              # <- solo min(): nunca infla
# execution/order_engine.py:213-216  — la unica comprobacion de geometria es != entry
if (not is_exit
        and signal.stop_loss != signal.entry_price
        and signal.take_profit != signal.entry_price
        and signal.strategy != StrategyType.MARKET_MAKING):
```
**A) ¿Puede un stop minúsculo producir un tamaño absurdo? NO. Verificado:**
```
umbral 1e-5 en unidades de tick por simbolo (precio real de hoy):
  BTC 7.8 ticks | ETH 2.4 ticks | SOL 0.1 ticks | ADA 0.0 ticks
  SL real de MR hoy: BTC 18.74 / ETH 21.20 / SOL 47.00 / ADA 53.13 bps  -> ninguno cerca del umbral

ETH, senal de $325 (cap alloc x leverage de base.py):
  SL= 0.05 bps -> RECHAZADA
  SL= 0.10 bps -> RECHAZADA
  SL= 0.50 bps -> ACEPTADA size=$325.00  max_size_by_risk=$300,000  perdida@SL=$0.016  coste RT(11bps)=$0.357
  SL= 1.00 bps -> ACEPTADA size=$325.00  max_size_by_risk=$150,000  perdida@SL=$0.033  coste RT=$0.357
  SL=11.00 bps -> ACEPTADA size=$325.00  max_size_by_risk=$ 13,636  perdida@SL=$0.357  coste RT=$0.357
```
`_adjust_position_size` solo hace `min(...)` y `signal.size_usd` ya viene topado por `base.py` (`alloc × leverage`), así que **el umbral relativo no puede inflar el tamaño en ningún camino del código**. El fix `fb073a1` es correcto y además cubre `entry_price <= 0` (antes habría dividido por cero). Confirmado también que desbloquea ADA (SL 53 bps ≫ 0,1 bps).
**B) Lo que sigue mal:**
1. **No es un suelo económico.** El umbral (0,1 bps) está **110× por debajo del coste round-trip (11 bps)**. Con un SL de 1 bp el sizer cree arriesgar $0.033 y el trade cuesta $0.357 en fricción — **11× más**: la "pérdida al stop" que el modelo de riesgo usa es ficción. Ojo: hoy BTC (18,7 bps) y ETH (21,2 bps) ya están **por debajo de 2× la fricción**, es decir, las comisiones son el 52–59 % de la distancia al stop (refuerza 01-F06, que sigue abierto).
2. **No comprueba el signo.** Verificado con el `RiskManager` real:
```
BUY  entry=2417.37 stop=2422.50 (POR ENCIMA) -> validate_signal = ACEPTADA size=$325.00
SELL entry=2417.37 stop=2412.24 (POR DEBAJO) -> validate_signal = ACEPTADA size=$325.00
dd=6 % -> _adjust_stop_loss NO corrige la inversion: stop 2422.4957 -> 2421.5731 (sigue arriba)
```
En live el efecto es una **posición sin stop loss**: la entrada MARKET llena, el `STOP_MARKET` reduceOnly se rechaza con `-2021` (*Order would immediately trigger*, **no retryable**: `_place_with_retry` solo reintenta `-2022`) y el `TAKE_PROFIT` sí entra ⇒ como **solo uno** de los dos falla, no se dispara el `BOTH_PROTECTIVES_FAILED_emergency_close` (`order_engine.py:356-358`) y la posición queda abierta con TP y sin SL. Es la misma familia que 01-F01 (posiciones desnudas) por otra puerta.
3. **Alcanzabilidad honesta:** MR construye siempre el stop del lado correcto (`mean_reversion.py:254-264`) y Fibonacci está congelado, así que hoy es un agujero **latente** de defensa en profundidad, no un bug activo. Pasa a activo el día que se reactive Fib o se añada una estrategia cuyo stop venga de un swing/nivel.
**Fix:** en `_adjust_position_size`, antes del cálculo: (a) validar geometría — `BUY ⇒ stop < entry < tp`, `SELL ⇒ tp < entry < stop` — y rechazar con `logger.error` + `notify_risk_event` si no se cumple; (b) sustituir el umbral 1e-5 por un **suelo económico**: `risk_per_unit / entry >= k × friction_bps/1e4` con `k ≈ 2` (22 bps con la fricción actual), configurable por símbolo; (c) en `order_engine`, tratar `-2021` en el SL como fallo crítico y disparar el cierre de emergencia aunque el TP haya entrado. Tests: stop invertido ⇒ `validate_signal is None`; SL de 5 bps ⇒ rechazado por suelo económico.
**Verificado cómo:** `py -3.12` con `RiskManager.validate_signal` real barriendo SL de 0,05 a 50 bps y con las dos geometrías invertidas (BUY/SELL); `tickSize` reales de `exchangeInfo`; lectura de `order_engine._place_with_retry:310` (solo `-2022` es retryable) y de la condición de emergencia `not sl_ok and not tp_ok`.

### [P2] risk_sizing-09 — Filtro de funding descalibrado en 3 órdenes de magnitud: la rama de BLOQUEO no se ha disparado NI UNA VEZ en 500 settlements reales (~166 días) en ninguno de los 4 símbolos, y la rama de aviso recorta el tamaño un 20–32 % en el 8–44 % de los settlements para evitar un coste esperado de ~0,06 bps frente a 11 bps de comisiones
**Archivo:** `risk/risk_manager.py:163-183`, `config/settings.py:109-110`
**Línea:** `risk/risk_manager.py:179`
**Evidencia (código real):**
```python
# risk/risk_manager.py:172-183
abs_rate = abs(funding_rate)
if abs_rate >= self.config.funding_rate_block:        # 0.0005 = 5 bps / 8h
    return None
elif abs_rate >= self.config.funding_rate_warn:       # 0.0001 = 1 bp / 8h
    funding_penalty = 1.0 - min(abs_rate / self.config.funding_rate_block, 0.7)
    signal.size_usd *= funding_penalty
```
Datos reales de `GET /fapi/v1/fundingRate?limit=1000` (500 settlements ≈ 166 días, 2026-08-31):
```
BTCUSDT: media=+0.277bps  |r|>=1bp:  8.8%   |r|>=5bp: 0.0%   p50|r|=0.456bps  p95|r|=1.000bps
ETHUSDT: media=+0.193bps  |r|>=1bp:  8.2%   |r|>=5bp: 0.0%   p50|r|=0.370bps  p95|r|=1.000bps
SOLUSDT: media=-0.025bps  |r|>=1bp: 25.0%   |r|>=5bp: 0.0%   p50|r|=0.523bps  p95|r|=1.509bps
ADAUSDT: media=+0.094bps  |r|>=1bp: 43.6%   |r|>=5bp: 0.0%   p50|r|=0.852bps  p95|r|=1.601bps
penalizacion aplicada: a 1.0 bps -> x0.80 (-20 %) ; a 1.6 bps (p95 ADA) -> x0.68 (-32 %)
```
**Por qué:**
1. `funding_rate_block = 5 bps/8h` es **código muerto**: 0 de 500 settlements en los 4 símbolos. La rama de bloqueo nunca se ejecuta.
2. `funding_rate_warn = 1 bp/8h` es exactamente el funding **base** de Binance, así que dispara en el 8,2 % (ETH) y hasta el **43,6 % (ADA)** de los settlements — en ADA casi la mitad de los longs se recortan un 20–32 %.
3. **El coste que evita es despreciable.** MR es intradía (cooldown 180 s, TP 4×ATR ≈ 50–105 bps); el funding solo se paga si la posición cruza un settlement, con probabilidad ≈ `horas_de_hold / 8`. Para un hold de 30 min: `0.0625 × 1 bp ≈ 0.06 bps` esperados, frente a **11 bps** de comisiones+slippage round-trip. El filtro sacrifica un 20–32 % del tamaño por un coste que es el **0,6 % de la fricción** que el sistema ya paga sin rechistar.
4. Además el filtro no prorratea por tiempo de hold ni comprueba si queda algún settlement dentro del horizonte esperado del trade. 01-F15 sigue abierto y ahora está cuantificado con datos reales.
**Fix:** (a) convertir el filtro en económico: `coste_funding_esperado_bps = |rate| × P(cruzar settlement)` y compararlo con el edge esperado del trade, no con umbrales absolutos; (b) recalibrar por percentiles reales por símbolo (p. ej. warn = p90 de `|rate|`, block = p99.9) en vez de constantes globales; (c) si no se implementa (a), subir `warn` a ≥ 3 bps para dejar de castigar el funding neutro. Test: rate = 1 bp y hold esperado de 30 min ⇒ sin recorte.
**Verificado cómo:** descarga en vivo de `GET /fapi/v1/fundingRate?symbol=…&limit=1000` para los 4 símbolos y cálculo de percentiles/frecuencias con numpy; penalizaciones evaluadas con la fórmula literal del código.

### [P2] risk_sizing-10 — Kelly es inalcanzable, discontinuo y se alimenta con USD crudos de símbolos con notional distinto; además el backtester nunca lo alimenta (`record_trade_result(pnl)` sin `strategy`) → si algún día se activa, live y backtest divergen en sizing
**Archivo:** `core/quant_models.py:195-268`, `config/settings.py:125-127`, `risk/risk_manager.py:73-81`, `risk/risk_manager.py:456-457`, `backtesting/backtester.py:842,881,1109,1188`
**Línea:** `core/quant_models.py:252`
**Evidencia (código real):**
```python
# core/quant_models.py:200-205
if n < self.min_trades:                      # 100
    return KellyResult(capped_kelly=self.default_risk_pct, sample_size=n, is_valid=False)
# core/quant_models.py:252
capped = max(self.floor_pct, min(self.ceiling_pct, half_kelly))     # [0.005, 0.03]
# risk/risk_manager.py:456-457 — solo se alimenta si llega `strategy`
if strategy and strategy in self.kelly:
    self.kelly[strategy].record_trade(pnl)
# backtesting/backtester.py:881 — SIN strategy
risk_manager.record_trade_result(pnl)
```
Ejecutado con la clase real (`min_trades=100`, floor 0.005, ceiling 0.03, default 0.015):
```
 n= 99 WR=0.50 b=1.5: full=+0.0000 -> risk_pct=0.0150  (1.00x el default)
 n=100 WR=0.50 b=1.5: full=+0.1667 half=+0.0833 -> risk_pct=0.0300  (2.00x)   <- salto con UNA observacion
 n=100 WR=0.45 b=1.5: full=+0.0833 half=+0.0417 -> risk_pct=0.0300  (2.00x)
 n=100 WR=0.40 b=1.2: full=-0.1000            -> risk_pct=0.0050  (0.33x)
 n=100 WR=0.55 b=2.0: full=+0.3250 half=+0.1625 -> risk_pct=0.0300  (2.00x)
DB de paper: sqlite3 data/trade_database.db -> sessions=5, trades=0
```
**Por qué:**
(a) **Inalcanzable**: `deque(maxlen=200)` en memoria, sin persistencia (risk_sizing-03), reinicios automáticos cada pocas horas y **0 trades cerrados en la DB tras 5 sesiones** ⇒ `min_trades=100` no se alcanzará nunca. Kelly es, hoy, una constante de 0,015.
(b) **Discontinuidad**: en `n=99` el riesgo es 0,015 y en `n=100` salta a 0,030 (**×2**) o cae a 0,005 (**÷3**) por una única observación. Con `n=100` y `WR=0.45`, `SE(WR)=0.0497` ⇒ IC 95 % de WR = [0.353, 0.547] ⇒ el Kelly completo va de −0,078 a +0,245: el 0,083 estimado es **estadísticamente indistinguible de cero** y aun así el sistema duplica el riesgo. `max(floor, min(ceiling, half))` convierte Kelly en otra función escalón (edge>0 ⇒ 3 %, edge≤0 ⇒ 0,5 %), no en un sizing continuo.
(c) **Unidades mezcladas**: los PnL que alimentan Kelly son **USD crudos** de posiciones con notional distinto ($325 ETH vs $150 ADA), así que `payoff_ratio = avg_win/avg_loss` mide **tamaño** además de edge. Debería normalizarse a múltiplos de R.
(d) `self.kelly` se indexa solo por `StrategyType`, no por símbolo: un ADA malo cambia el sizing de ETH y SOL.
(e) El backtester llama a `record_trade_result(pnl)` **sin `strategy`** en sus 4 call sites ⇒ Kelly nunca se alimenta en backtest. Si en live llegara a activarse, backtest y live usarían fracciones de riesgo distintas y ningún backtest validaría el sizing real.
**Fix:** rampa continua `w = min(1, (n − min_trades)/min_trades)` entre el default y el half-Kelly; alimentar con **R-múltiplos** (`pnl / (|entry−SL| × size)`), no USD; pasar `strategy=` en los 4 call sites del backtester; persistir el historial (risk_sizing-03). Test: `n=100` con IC del edge que incluye 0 ⇒ el riesgo no debe subir del default.
**Verificado cómo:** `py -3.12` con `KellyCriterion` real (6 escenarios `n/WR/b`); IC binomial calculado a mano; `sqlite3 data/trade_database.db`; `grep -n "record_trade_result" backtesting/backtester.py` → 4 llamadas sin `strategy`.

### [P2] risk_sizing-11 — Drawdown, pérdida diaria y circuit breaker son CIEGOS al PnL no realizado, también en paper; y v2.13.1 introdujo la asimetría de que el bridge SÍ muestra el unrealized mientras el motor de riesgo lo ignora
**Archivo:** `risk/risk_manager.py:490-494` (`current_drawdown_pct`), `main.py:376-381` (live: `wb`), `main.py:640-643` (paper: solo PnL realizado), `server/bridge.py` (`_merged_performance`, v2.13.1)
**Línea:** `main.py:376`
**Evidencia (código real):**
```python
# main.py:376  (live: unica fuente de equity)
equity = float(b.get("wb", 0))            # wallet balance
await self.risk_manager.update_equity_safe(equity)
# main.py:641-642  (paper: solo realizado)
new_equity = equity_before + trade.pnl
await self.risk_manager.update_equity_safe(new_equity)
# risk/risk_manager.py:492-494
return (self._equity_peak - self._current_equity) / self._equity_peak
```
Documentación oficial de Binance (`GET /fapi/v2/account`) verificada por WebFetch: los campos son `totalWalletBalance`, `totalUnrealizedProfit` y `totalMarginBalance`, con **`totalMarginBalance = totalWalletBalance + totalUnrealizedProfit`** ⇒ `wb` **excluye** el PnL no realizado.
Reproducido con las clases reales:
```
equity realizado = 1000.0   posicion ETH con unrealized = -81.20  (-8.1 % del equity)
current_drawdown_pct = 0.0            <- deberia ser ~8.1 %
_check_max_drawdown() = False
circuit_breaker       = False
```
**Por qué:** 01-F07 y 02-10 (ronda 1) **siguen abiertos** — el código no ha cambiado y `get_account()`/`get_balances()` siguen sin ningún caller. Novedades de esta ronda:
1. **La ceguera también existe en paper**, que es donde corre el soak: `_process_paper_fill` solo actualiza equity en fills con PnL realizado, y el bucle de riesgo sincroniza posiciones pero **no marca a mercado**. Con 4 posiciones abiertas cayendo un 10 % (−$105 sobre $1 050 de notional = −10,5 % del equity), el `drawdown_halt` y el circuit breaker siguen a 0 %.
2. **v2.13.1 introdujo una asimetría**: `server/bridge.py::_merged_performance()` combina el realizado de la trade DB con el **unrealized vivo** para la UI, así que el escritorio muestra un equity que el motor de riesgo no usa para ninguna decisión. El operador ve −8 % mientras el bot dice drawdown 0 % y sigue abriendo.
3. Los límites, además, son **inalcanzables por trading normal**: hacen falta 42–82 pérdidas completas para el $50 diario y 85–164 para el $100 de max drawdown (ver tabla de agregados). Sin marcar a mercado, el único camino realista al circuit breaker es un gap que cierre los stops de golpe — exactamente el escenario "flash crash" que el módulo dice proteger.
**Fix:** en el bucle de riesgo (que ya consulta `positionRisk` cada 2 s en live y tiene todas las posiciones en paper) llamar a `update_equity_safe(wallet + Σ unrealized_pnl)`; sembrar el equity al arrancar desde `GET /fapi/v2/account` (`totalMarginBalance`); usar la MISMA fórmula que `_merged_performance()` para que UI y motor no discrepen. Test: posición con −8 % de unrealized ⇒ `current_drawdown_pct ≈ 0.08` y circuit breaker activo.
**Verificado cómo:** `py -3.12` con `RiskManager`/`Position` reales; documentación oficial de Binance (`/fapi/v2/account`) consultada por WebFetch para confirmar `marginBalance = walletBalance + unrealizedProfit`; `grep -rn "get_account()\|get_balances()"` → 0 callers.

### [P2] risk_sizing-12 — Siete multiplicadores independientes sobre `size_usd` sin ningún suelo ni acumulador: el tamaño puede caer a $3.46 en ETH (minNotional $20) y ninguno de los recortes conoce a los demás
**Archivo:** `risk/risk_manager.py:142-144, 158-161, 177-183, 204-205, 209-212, 214-219, 289-295`
**Línea:** `risk/risk_manager.py:144`
**Evidencia (ejecutado con los factores de las ramas reales, partiendo del tamaño real de ETH $325):**
```
  x0.700  micro risk_score=1.0 -> max(1-0.3*1.0, 0.4)      -> $ 227.50
  x0.760  kyle impact moderado -> 1-min(0.8*0.3, 0.5)      -> $ 172.90
  x0.400  funding 3 bps (warn) -> 1-min(3e-4/5e-4, 0.7)    -> $  69.16
  x0.500  RoR throttle                                     -> $  34.58
  x0.500  vol scalar minimo                                -> $  17.29
  x0.400  corr stress maximo                               -> $   6.92
  y ademas consecutive_losses=4 -> x0.5                    -> $   3.46   (minNotional ETH = $20)
grep -c "min_notional" risk/risk_manager.py -> 0
```
**Por qué:** (a) los siete recortes son multiplicativos e independientes, así que el resultado no tiene ninguna interpretación de riesgo (¿qué significa "0,5 % de equity × 0,7 × 0,76 × 0,4 …"?); (b) **no hay suelo** en el `RiskManager`: `base.py` sí impone `min_notional = 20.0` (`strategies/base.py:117-119`) pero eso ocurre **antes** de toda la cadena, así que el suelo se pierde; (c) por debajo del `minNotional` la orden se rechaza en live y **se rellena en paper** (misma raíz que risk_sizing-02); (d) por debajo de ~$40 de notional la comisión round-trip ($0.04) más el tick de ADA/SOL hacen el trade inviable aunque se acepte.
**Fix:** acumular un único `size_multiplier` a lo largo de `validate_signal`, aplicarlo una sola vez con clamp explícito (`max(0.25, prod(mults))`), y comparar el resultado contra el `min_notional` del símbolo: por debajo, **rechazar con log/alerta**, no encoger. Test: los 7 recortes al máximo ⇒ la señal se rechaza, no se emite a $3.46.
**Verificado cómo:** `py -3.12` evaluando literalmente cada rama de `validate_signal` con sus parámetros de `Settings()`; `grep -c min_notional risk/risk_manager.py` → 0; lectura de `strategies/base.py:116-119` (el suelo existe pero es previo a la cadena).

### [P3] risk_sizing-13 — Siete parámetros de riesgo son código muerto o incoherentes con una cuenta de $1 000
**Archivo:** `portfolio/portfolio_manager.py:79-81,189,244-245`, `risk/risk_manager.py:58-62,321`, `config/settings.py:87,110`, `exchange/binance_client.py:60-62`
**Línea:** `portfolio/portfolio_manager.py:245`
**Evidencia (verificada una por una):**
```python
# 1) PERF_FLOOR / PERF_CEIL son NO-OPs: 1+0.5*tanh(1.5*x) esta acotado en (0.5, 1.5) ESTRICTO
factor = max(PERF_FLOOR, min(PERF_CEIL, 1.0 + 0.5*math.tanh(1.5*avg_r)))   # el clamp nunca actua
   avg_r=-1000 -> 0.500000   avg_r=-1 -> 0.547426   avg_r=+1000 -> 1.500000
# 2) dd_factor: suelo 0.3 INALCANZABLE (halt a dd>=10 %)
dd_factor = max(0.3, 1.0 - dd*2.0)     # dd=0 ->1.00  dd=5% ->0.90  dd=10% ->0.80  (0.3 exigiria dd=35 %)
# 3) funding_rate_block = 0.0005: 0 disparos en 500 settlements reales de los 4 simbolos
# 4) _check_total_exposure: cap $3000 vs exposicion maxima alcanzable $1300 -> nunca True (risk_sizing-04)
# 5) RiskOfRuin se construye SIN min_trades -> hardcoded 30, sin knob en Settings
self.risk_of_ruin = RiskOfRuin(max_drawdown_pct=..., throttle_threshold=..., pause_threshold=...)
#    (kelly_min_trades=100 si es configurable -> incoherencia entre los dos modelos)
# 6) config/settings.py:87 comentario obsoleto de la era de $300
risk_per_trade_pct: float = 0.015   # 1.5% = $4.50 risk budget    <- con $1000 son $15.00
# 7) exchange/binance_client.py:61 fallback desalineado con exchangeInfo real
"BTCUSDT": {..., "minNotional": Decimal("100")}   # el valor real hoy es 50
```
**Por qué:** ninguno de los siete rompe nada por sí solo, pero **todos crean la ilusión de que hay límites donde no los hay**. Al leer el código, un operador cree que la asignación puede caer al 30 % en drawdown (imposible: mínimo 80 %), que una estrategia mala se recorta al 50 % (imposible: mínimo ~92 %, risk_sizing-07), que hay un tope de exposición del 60 % (imposible de disparar) y que el funding bloquea entradas (nunca ha bloqueado ninguna). El comentario de `$4.50` induce a error sobre el presupuesto de riesgo real, que además no se entrega (risk_sizing-06).
**Fix:** borrar o recalibrar cada uno; poner `ror_min_trades` en `TradingConfig`; actualizar el comentario a `$15.00`; sincronizar `DEFAULT_SYMBOL_FILTERS` con `exchangeInfo` o eliminar los valores hardcodeados y fallar cerrado si no hay cache.
**Verificado cómo:** aritmética del `tanh` ejecutada en `py -3.12`; `dd_factor` evaluado en 5 puntos; frecuencias de funding sobre 500 settlements reales; `grep -n "min_trades" config/settings.py risk/risk_manager.py`; `GET /fapi/v1/exchangeInfo` en vivo.

### [P3] risk_sizing-14 — Estado mutable compartido: `_current_weights` se sobrescribe en cada `get_allocation(symbol, …)` y el resumen publica el peso del ÚLTIMO símbolo evaluado; `is_circuit_breaker_active` (property) y `validate_signal` mutan estado como efecto secundario
**Archivo:** `portfolio/portfolio_manager.py:215`, `portfolio/portfolio_manager.py:280-287`, `risk/risk_manager.py:500-513`, `risk/risk_manager.py:238-241`
**Línea:** `portfolio/portfolio_manager.py:215`
**Evidencia (código real + ejecución):**
```python
# portfolio/portfolio_manager.py:215  — clave por ESTRATEGIA, llamada por SIMBOLO
self._current_weights[strategy] = base_weight * perf_factor * dd_factor
# risk/risk_manager.py:508-513 — una property que MUTA
if cooldown_elapsed and drawdown_recovered:
    self._circuit_breaker_active = False
# risk/risk_manager.py:238-241 — validar una senal DESACTIVA el circuit breaker
self._circuit_breaker_active = False
```
```
get_allocation(ETH-USD) = $115.74  -> _current_weights[MR] = 0.4630
get_allocation(ADA-USD) = $209.26  -> _current_weights[MR] = 0.8370
(get_portfolio_summary() / el endpoint de performance publican 0.8370, el peso de ADA,
 como si fuera el peso de la estrategia)
```
**Por qué:** (a) el "peso actual" que ve el operador en el dashboard depende del orden de evaluación de símbolos y del blend de Risk Parity, que es por bucket `símbolo×estrategia`; con RP activo la diferencia medida es de 0,463 vs 0,837 — un factor 1,8× de puro artefacto de estado compartido; (b) una property que muta estado (01-F25, sigue abierta) hace que *leer* el circuit breaker lo desactive, así que el orden de las lecturas cambia el comportamiento; (c) `validate_signal` desactiva el circuit breaker como efecto secundario de validar una señal, lo que acopla la política de riesgo al flujo de señales; (d) a esto se suma que **todos** los contadores de riesgo (`_consecutive_losses`, `risk_of_ruin`, `kelly`) son globales a símbolos y estrategias (risk_sizing-01, -02, -10).
**Fix:** `_current_weights` con clave `(symbol, strategy)`; convertir `is_circuit_breaker_active` en un método puro `check_circuit_breaker()` y hacer la desactivación explícita desde el bucle de riesgo, no desde `validate_signal`. Test: `get_portfolio_summary()` tras evaluar ETH y ADA debe reportar los dos pesos, no uno.
**Verificado cómo:** `py -3.12` con `PortfolioManager` real alimentando `CovarianceTracker` con dos buckets de vol distinta y leyendo `_current_weights` tras cada `get_allocation`.

---

## Tabla resumen

| id | sev | archivo:línea | hallazgo | estado ronda 1 |
|---|---|---|---|---|
| risk_sizing-01 | **P0** | `core/quant_models.py:348` | RoR pausa TODAS las entradas de forma permanente y silenciosa desde el trade 30; fórmula mal aplicada; deadlock; sin alerta | nuevo |
| risk_sizing-02 | P1 | `risk/risk_manager.py:290` | Freno por rachas global sin suelo: 13–26 % de entradas bajo `minNotional` (live rechaza, paper rellena); deadlock a n≥9 | nuevo |
| risk_sizing-03 | P1 | `risk/risk_manager.py:40` | Cero persistencia de estado de riesgo + `Restart=always`: límite diario, CB, halt y rachas se resetean solos | extiende 01-F07 / 01-F19 |
| risk_sizing-04 | P1 | `risk/risk_manager.py:321` | Tope del 60 % inaplicable: `_check_total_exposure` es código muerto; validador no mira la suma; margen no agregado (hasta 65 %) | 01-F14 abierto, agravado |
| risk_sizing-05 | P1 | `core/quant_models.py:124` | Vol targeting clavado en `max_scalar=1.5` (infla el 50 % siempre); returns "diarios" de N días; se aplica tras el cap de leverage | 01-F19 abierto, cuantificado |
| risk_sizing-06 | P1 | `strategies/base.py:113` | El sizer entrega 0,061–0,117 % de riesgo frente al 1,5 % configurado; manda `alloc × leverage`; conmuta de régimen en SL≈62 bps | 01-F13 abierto, peor |
| risk_sizing-07 | P1 | `portfolio/portfolio_manager.py:243` | El fix 01-F03 es incorrecto: el gate de rendimiento no puede bloquear (haría falta −$10.99/trade vs −$1.54 máximo) | fix de 01-F03 mal |
| risk_sizing-08 | P2 | `risk/risk_manager.py:356` | Guard `entry≈stop` nuevo: correcto en tamaño, pero sin suelo económico (0,1 vs 11 bps) y sin comprobar el signo → posible posición sin SL | nuevo (sobre `fb073a1`) |
| risk_sizing-09 | P2 | `risk/risk_manager.py:179` | Funding: bloqueo con 0 disparos en 500 settlements; aviso recorta 20–32 % en el 8–44 % de los casos por ~0,06 bps de coste | 01-F15 abierto, cuantificado |
| risk_sizing-10 | P2 | `core/quant_models.py:252` | Kelly inalcanzable (0 trades en DB), discontinuo (×2 con una observación), USD crudos, nunca alimentado en backtest | 01-F19 abierto, ampliado |
| risk_sizing-11 | P2 | `main.py:376` | Drawdown/pérdida diaria/CB ciegos al unrealized también en paper; v2.13.1 muestra unrealized en la UI que el motor ignora | 01-F07 / 02-10 abiertos |
| risk_sizing-12 | P2 | `risk/risk_manager.py:144` | 7 multiplicadores independientes sin suelo: ETH puede bajar a $3.46 (minNotional $20) | nuevo |
| risk_sizing-13 | P3 | `portfolio/portfolio_manager.py:245` | 7 parámetros muertos o incoherentes con $1 000 (PERF_FLOOR/CEIL no-op, dd_factor, funding block, exposure, ror min_trades, comentario $4.50, minNotional BTC) | nuevo |
| risk_sizing-14 | P3 | `portfolio/portfolio_manager.py:215` | `_current_weights` sobrescrito por símbolo; property que muta; `validate_signal` desactiva el CB como efecto secundario | 01-F25 abierto, ampliado |

### Cerrado y verificado en esta ronda
- **Guard `entry ≈ stop` relativo (`fb073a1`)**: el cambio de `< 0.001` absoluto a `risk_per_unit/entry < 1e-5` es **correcto**. Desbloquea ADA (SL real 53 bps ≫ 0,1 bps), cubre `entry_price <= 0` (antes habría dividido por cero) y **no puede inflar el tamaño** porque `_adjust_position_size` solo aplica `min(...)` sobre un `size_usd` ya topado por `base.py`. Lo que falta es un suelo económico y la validación del signo (risk_sizing-08).
- **`FIBONACCI_RETRACEMENT` congelado en las 3 puertas** (`allocation=0.00`, `REGIME_WEIGHTS=0.00` en los 5 regímenes, `SYMBOL_STRATEGY_MAP["BTC-USD"]=set()`): verificado por lectura; `should_strategy_trade` devuelve `False` por `base_weight < 0.08` incluso si se saltara el mapa de símbolos. Congelación efectiva. Único efecto colateral: `symbol_share` sigue dividiendo entre 4 (risk_sizing-06).
- **`ANNUALIZATION_FACTOR = 365`** en `analytics/performance.py:144` y `annualization=365.0` en `VolatilityTargeting`: coherentes entre sí y correctos para cripto 24/7. (Queda un comentario obsoleto "annualizar correctamente con sqrt(252)" en `analytics/performance.py:606`, y `_aggregate_daily_returns` omite los días sin trades, lo que sesga el Sharpe al alza — **fuera del alcance de este área**, para `backtest_parity`.)
- **Unidades USD vs contratos**: revisadas en toda la cadena (`base.py` devuelve UNIDADES, `mean_reversion` convierte con `size * price`, `risk_manager` trabaja en USD, `Position.notional = |size × price|`). **No hay mezcla de unidades.**

---

## Veredicto (10 líneas)

1. El módulo de riesgo **no dimensiona por riesgo**: entrega 0,061–0,117 % del equity por trade frente al 1,5 % configurado, porque la restricción que manda es `allocated_capital × leverage` y no el stop (risk_sizing-06). Con $1 000, el sistema arriesga entre $0,61 y $1,18 por operación.
2. Como consecuencia directa, **todos los límites de pérdida son decorativos**: harían falta 42–82 pérdidas completas en un día para el tope de $50 y 85–164 para el de $100.
3. Los tres caps de exposición (60 %, margen 50 %, `max_position_usd`) **no cierran**: `_check_total_exposure` es código muerto, el validador de config no mira la suma y el margen agregado llega al 65 % del equity (risk_sizing-04).
4. Los frenos que **sí muerden** son los equivocados y todos son globales: RoR pausa el bot entero de forma permanente y silenciosa desde el trade 30 (P0), y el freno por rachas empuja el tamaño por debajo del `minNotional` en el 13–26 % de las entradas.
5. Esa última divergencia **invalida el soak de paper**: paper rellena órdenes que live rechazaría, así que la WR/PF del soak no son transferibles y no sirven como evidencia para ir a real.
6. Ningún estado de riesgo se persiste y el servicio corre con `Restart=always` + watchdog: **cualquier reinicio pone a cero el límite diario, el circuit breaker, el halt por drawdown y el contador de rachas** (risk_sizing-03).
7. El drawdown se mide sobre equity **sin PnL no realizado** (verificado contra la documentación oficial de Binance), también en paper: 4 posiciones perdiendo un 10 % dan drawdown 0 % y el circuit breaker no salta.
8. Los tres modelos "cuant" son inertes o contraproducentes: Kelly es inalcanzable y discontinuo, vol targeting está clavado en ×1,5 (infla, no protege) y RoR es un escalón mal derivado que pausa sistemas con edge positivo el 28–35 % de las veces.
9. El guard `entry ≈ stop` cambiado hoy **es correcto** y no abre la puerta a tamaños absurdos; lo que falta es un suelo económico (0,1 bps frente a 11 bps de fricción) y validar el signo del stop.
10. **Recomendación:** no tocar live. Antes de cualquier prueba con dinero real hay que (a) arreglar el P0 de RoR, (b) poner suelo de `minNotional` y paridad paper/live, (c) persistir el estado de riesgo, (d) unificar `risk_per_trade_pct` en una sola definición y (e) marcar a mercado el equity. Sin (b) y (e), ningún número que produzca el bot es interpretable.

