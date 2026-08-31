# Auditoría R2 — risk_sizing (Riesgo y sizing numérico)

**Fecha:** 2026-08-30/31 · **Alcance:** `risk/risk_manager.py`, `portfolio/portfolio_manager.py`, `core/quant_models.py`, `strategies/base.py` (+ call sites en `strategies/mean_reversion.py`, `main.py`, `backtesting/backtester.py`, `exchange/binance_client.py`).
**Método:** lectura línea a línea + reproducción numérica con `py -3.12` instanciando `RiskManager`/`PortfolioManager`/`MeanReversionStrategy` con `Settings()` real y señales sintéticas, usando **precio y ATR14(5m) REALES de Binance Futures del 2026-08-31**. Referencias a la ronda 1: `01-Fxx` = `tasks/audit/01_core_strategy_risk.md`.

**Datos de mercado usados (Binance USDT-M, 2026-08-31 ~02:30 UTC):**

| símbolo | precio | ATR14 5m | ATR bps | minNotional real (exchangeInfo) |
|---|---|---|---|---|
| BTCUSDT | 78 069.50 | 249.83 | 32.0 | 50 |
| ETHUSDT | 2 435.83 | 12.004 | 49.3 | 20 |
| SOLUSDT | 102.44 | 0.5671 | 55.4 | 5 |
| ADAUSDT | 0.1945 | 0.001386 | 71.3 | 5 |

---

## Tabla señal → tamaño (verificada, clases reales, equity $1 000, régimen RANGING, MR, sin posiciones abiertas)

| símbolo | alloc$ (`get_allocation`) | SL dist (bps) | `base.py` size_usd | risk $ @ SL | risk % equity | **final tras `validate_signal`** | leverage efectivo s/ alloc | leverage s/ equity |
|---|---|---|---|---|---|---|---|---|
| BTC-USD¹ | 162.50 | 48.0 | 325.00 | 1.560 | **0.156 %** | **$325.00** | 2.00× | 0.33× |
| ETH-USD | 162.50 | 73.9 | 280.67 | 2.075 | **0.207 %** | **$280.67** | 1.73× | 0.28× |
| SOL-USD | 162.50 | 99.7 | 217.60 | 2.168 | **0.217 %** | **$217.60** | 1.34× | 0.22× |
| ADA-USD | 162.50 | 142.5 | 157.83 | 2.138 | **0.214 %** | **$150.00**² | 0.92× | 0.15× |

¹ BTC ya no opera: `SYMBOL_STRATEGY_MAP["BTC-USD"] = set()` desde `fb073a1`. La fila es el sizing que saldría si se reactivara.
² Recortado por `sym_config.max_position_usd = 150` en `_adjust_position_size`.

- **Exposición total 4 posiciones = $973.27 (97.3 % del equity)**; cap `_check_total_exposure` = 1000 × 0.6 × 5 = **$3 000 (300 % del equity)** → **no muerde nunca** (01-F14 confirmado, sigue abierto).
- **Riesgo simultáneo si las 4 pegan SL = $7.94 = 0.79 % del equity**, frente a `max_daily_loss` $50 y `max_drawdown` $100. Los límites de pérdida diaria y de drawdown son **inalcanzables por trading normal**: harían falta ~24 pérdidas completas en un día para tocar el 5 % diario.
- **Riesgo configurado por trade: 1.5 % = $15. Riesgo real medido: $1.56–$2.17 (0.16–0.22 %).** Factor 7–10×. 01-F13 sigue abierto y es la raíz de otros dos hallazgos de esta ronda (risk_sizing-04 y -06).
- Margen total requerido con 4 posiciones a 2× = $486.63 (48.7 % del equity).

---

## Hallazgos

### [P1] risk_sizing-01 — CERRADO en ronda 1.5 (`fb073a1`) — verificado
El guard `risk_per_unit < 0.001` en unidades de PRECIO bloqueaba el 100 % de las señales de ADA (a $0.20, 0.001 = 50 bps; el SL de MR 2×ATR daba 39 bps). Estaba reportado en la versión parcial de este informe y **ya está corregido**:

```python
# risk/risk_manager.py:356
if signal.entry_price <= 0 or risk_per_unit / signal.entry_price < 1e-5:
```
**Verificado cómo:** `git show fb073a1` (incluye `tests/test_risk_relative_sl_guard.py`, 4 tests); reproducido hoy con ADA a $0.1945 y SL 142.5 bps → `final $150.00`, no rechazado. **El fix es correcto y no introduce regresión**: el umbral relativo 1e-5 (0.1 bps) sigue rechazando `entry == stop` y ahora también cubre `entry_price <= 0` (antes `risk_per_unit/entry` habría dividido por cero). Sin hallazgo abierto.

### [P0] risk_sizing-02 — Risk of Ruin PAUSA TODAS las entradas de forma PERMANENTE y silenciosa a partir del trade cerrado nº30; con el `avg_loss` real ($2.07) hace falta un `edge` muestral > 0.0238 para no pausar, y un sistema con edge VERDADERO +0.125 pausa el 36 % de las veces. Una vez pausado nunca se recalcula (deadlock)
**Archivo:** `core/quant_models.py:323-375` (`RiskOfRuin.compute`), `risk/risk_manager.py:198-207` (`validate_signal`), `risk/risk_manager.py:454-459` (único caller de `compute`)
**Evidencia:**
```python
# quant_models.compute
edge = win_rate * (avg_win / avg_loss) - (1.0 - win_rate)
if edge <= 0: ror = 1.0                      # escalón
else: capital_units = current_equity*max_dd/avg_loss ; ror = ((1-edge)/(1+edge)) ** capital_units
# risk_manager.validate_signal:200
if ror.should_pause and ror.sample_size >= self.risk_of_ruin.min_trades: return None   # TODAS las entradas
# risk_manager.record_trade_result:459  (UNICO sitio que llama compute)
self.risk_of_ruin.compute(self._current_equity)
```
Ejecutado (clase real, 200 trades, **avg_loss = $2.07 = el riesgo real medido hoy**, equity 1000, max_dd 10 % → `capital_units = 48.3`):
```
WR=0.39 payoff=1.5 : edge=-0.0250 ror=1.0000 throttle=True PAUSE=True
WR=0.39 payoff=1.6 : edge=+0.0140 ror=0.2585 throttle=True PAUSE=True   <- edge POSITIVO y pausa igual
WR=0.45 payoff=1.25: edge=+0.0125 ror=0.2989 throttle=True PAUSE=True
WR=0.50 payoff=1.01: edge=+0.0050 ror=0.6169 throttle=True PAUSE=True
WR=0.50 payoff=3.0 : edge=+1.0000 ror=0.0000 throttle=False PAUSE=False  <- rama edge>=1
umbrales exactos con capital_units=48.3: edge > 0.0238 para NO pausar, > 0.0363 para NO throttle
E2E RiskManager real: 30 trades (24 x -2.07 / 6 x +2.0) -> ror=1.0 pause=True -> validate_signal() -> None
```
Monte Carlo (4 000 muestras de 30 trades, clase real) — P(pausa en el trade 30) con **edge VERDADERO positivo**:
`WR45/payoff1.5 (+0.125) → 36.1 %` · `WR50/1.3 (+0.150) → 30.9 %` · `WR40/2.0 (+0.200) → 27.6 %` · `WR55/1.2 (+0.210) → 13.1 %`.
**Por qué es P0:** (1) La fórmula está **mal aplicada**: la ruina del jugador `((1−e)/(1+e))^u` exige que `e` sea la ventaja en PROBABILIDAD (p−q) de una apuesta binaria de tamaño fijo; aquí `e` es la esperanza en múltiplos de R, que puede valer >1 (rama `edge>=1 → ror=0`, sin sentido). (2) Con la evidencia del propio repo (MR PF 0.85/0.86 → edge bruto ≤ 0) el disparo es **seguro**, no probable: `edge ≤ 0 ⇒ ror = 1.0 ⇒ pause`. (3) El gate es **GLOBAL**: pausa todas las estrategias y todos los símbolos. (4) Una vez pausado no hay fills → `record_trade_result` no se llama → `compute` no se recalcula → **pausa permanente hasta reiniciar el proceso**; es el mismo deadlock que 01-F03 arregló para el performance factor y que aquí quedó sin tocar. (5) **No hay alerta**: `ror_pause_active` es un `logger.warning`, no un `notify_risk_event` → el bot parece vivo (health OK, WS conectado, señales generadas) y no opera. Las salidas sí pasan (`validate_signal` corta antes en la línea 121), así que no deja posiciones desnudas — pero el motor queda convertido en un no-op silencioso.
**Fix:** (a) sustituir el escalón por `compute_empirical` (bootstrap, ya escrito y **sin ningún caller**: `grep -rn compute_empirical` → 0 usos fuera de su definición) exigiendo que el límite superior del IC supere el umbral; (b) cooldown/probation como en F03 (`ror_blocked_since` + reevaluación con ventana limpia); (c) RoR por estrategia y `min_trades` mucho mayor (30 trades no estiman una WR); (d) `notify_risk_event("ror_pause")` obligatorio. Test: 30 pérdidas → pausa; +3600 s → entrada permitida en probation.
**Verificado cómo:** ejecutado (clase real + `RiskManager` E2E + Monte Carlo con `numpy.default_rng(7)`), umbrales de `edge` resueltos analíticamente y comprobados; `grep -rn "compute_empirical\|risk_of_ruin.compute"` → único caller `record_trade_result`.

### [P1] risk_sizing-03 — DEADLOCK por rachas: con ≥7 pérdidas seguidas el tamaño cae por debajo del `minNotional` de Binance y TODA orden se rechaza localmente; `_consecutive_losses` solo se resetea con un PnL>0 que ya no puede ocurrir
**Archivo:** `risk/risk_manager.py:289-295` (reducción `0.5**(n-3)`), `risk/risk_manager.py:435-451` (`record_trade_result`: reset solo con `pnl > 0`), `exchange/binance_client.py:495-501` (rechazo local por `minNotional`)
**Evidencia:**
```python
# risk_manager.py:290
if self._consecutive_losses >= 4:
    reduction = 0.5 ** (self._consecutive_losses - 3)
    adjusted_size *= reduction
# risk_manager.py:449
elif pnl > 0:
    self._consecutive_losses = 0     # UNICA salida del contador
# binance_client.py:497
if notional < f["minNotional"]:
    raise ValueError(f"notional ... below minNotional ...")
```
Ejecutado (clases reales, `_consecutive_loss_pause=False` para simular cooldown expirado):
```
 N losses |                ETH-USD |                SOL-USD |                ADA-USD
        4 | $   140.33          OK | $   108.80          OK | $    75.00          OK
        6 | $    35.08          OK | $    27.20          OK | $    18.75          OK
        7 | $    17.54  REJ<minNot | $    13.60          OK | $     9.38          OK
        9 | $     4.39  REJ<minNot | $     3.40  REJ<minNot | $     2.34  REJ<minNot
       10 | $     2.19  REJ<minNot | $     1.70  REJ<minNot | $     1.17  REJ<minNot
```
**Por qué:** A partir de 9 pérdidas consecutivas ninguna entrada supera el `minNotional` de ninguno de los 3 símbolos operables → `place_order` lanza `ValueError` antes de tocar la red → no hay fill → no hay PnL>0 → `_consecutive_losses` nunca baja → **el bot queda muerto de forma silenciosa e irreversible hasta un reinicio del proceso**. Con la evidencia del backtest (MR PF 0.85/0.86, WR ~40 %), P(9 pérdidas seguidas) ≈ 0.6^9 ≈ 1 % por ventana de 9 trades — es cuestión de días, no de años. No hay ninguna alerta: el `ValueError` se registra como error de orden. Además, entre n=4 y n=8 el bot opera con $9–$140 de notional donde la comisión round-trip (11 bps) es un porcentaje creciente del edge — se sigue perdiendo por fricción mientras se "protege".
**Fix:** (a) resetear/decaer `_consecutive_losses` también por tiempo (p. ej. tras `consecutive_loss_pause` expirado, decrementar 1) o por número de barras sin operar; (b) suelo de tamaño: si `adjusted_size < min_notional_del_símbolo`, **no reducir: bloquear la entrada explícitamente y avisar** (`notify_risk_event`), en vez de emitir una orden imposible; (c) cap del exponente (`min(self._consecutive_losses - 3, 3)` → suelo 0.125).
**Verificado cómo:** ejecutado con `RiskManager`/`PortfolioManager`/`MeanReversionStrategy` reales; `minNotional` leído de `GET /fapi/v1/exchangeInfo` en vivo (BTC 50, ETH 20, SOL 5, ADA 5) y del fallback `DEFAULT_SYMBOL_FILTERS`.

### [P1] risk_sizing-04 — El fix F03 de la ronda 1 dejó el gate de performance MATEMÁTICAMENTE INALCANZABLE: normaliza por el presupuesto de riesgo de CONFIG ($15) cuando el riesgo real por trade es ~$2.1; perder el 100 % de los trades al SL da factor 0.898 (bloqueo a <0.60)
**Archivo:** `portfolio/portfolio_manager.py:219-256` (`_risk_budget_per_trade`, `_performance_factor`), `portfolio/portfolio_manager.py:79-81` (`PERF_FLOOR=0.5`, `PERF_BLOCK_THRESHOLD=0.6`), `portfolio/portfolio_manager.py:312`
**Evidencia:**
```python
def _risk_budget_per_trade(self) -> float:
    return max(equity * self.config.risk_per_trade_pct, 1e-6)   # 1000*0.015 = $15.00
...
avg_r = (sum(closed) / n) / self._risk_budget_per_trade()
factor = 1.0 + 0.5 * math.tanh(1.5 * avg_r)
factor = max(PERF_FLOOR, min(PERF_CEIL, factor))
```
Ejecutado:
```
  _risk_budget_per_trade() = $15.00     riesgo REAL medido: ETH $2.07 / SOL $2.17 / ADA $2.14
  avg pnl/trade = $  -2.07 -> avg_r=-0.138 -> factor=0.898  no bloquea
  avg pnl/trade = $ -10.98 -> avg_r=-0.732 -> factor=0.600  no bloquea (limite)
  avg pnl/trade = $ -15.00 -> avg_r=-1.000 -> factor=0.547  BLOQUEA
  -> factor<0.6 exige avg pnl/trade <= $-10.99
  E2E: 60 trades cerrados de -$2.07 -> factor=0.8980  should_strategy_trade=True
```
**Por qué:** la pérdida máxima por trade está acotada por el SL ($1.56–$2.17 hoy), así que `avg_r` no puede bajar de ≈−0.14 → **`factor` nunca baja de ≈0.90, `PERF_BLOCK_THRESHOLD=0.6` y `PERF_FLOOR=0.5` son decorativos y `should_strategy_trade` no puede bloquear jamás por rendimiento**. La ronda 1 cambió un bug de "desactiva para siempre tras 5 fills" por otro de "no desactiva nunca": el mismo desajuste de unidades de 01-F13 (riesgo configurado 1.5 % vs riesgo entregado 0.2 %) se propagó al normalizador. Además la reducción de allocation también queda anulada: el mejor caso realista es ×0.90, irrelevante frente a los otros multiplicadores.
**Fix:** normalizar por el riesgo REAL por trade, no por el de config: `avg_r = avg_pnl / (media histórica de |entry−SL|×size)` — el `trade_db` ya guarda `entry_price`/`stop_loss`/`size`. Alternativa mínima e inmediata: usar como normalizador la desviación típica de los PnL cerrados (`avg_pnl / std(closed)`), que es invariante a escala. Añadir test: 30 trades a −1R real ⇒ `should_strategy_trade == False`.
**Verificado cómo:** ejecutado con `PortfolioManager` real (`update_strategy_pnl` ×60 y `_performance_factor`), riesgo real medido en la tabla señal→tamaño de este mismo informe.

### [P2] risk_sizing-05 — Cadena de 7 multiplicadores independientes sobre `size_usd` sin ningún suelo: el tamaño puede llegar a $2.75 en ETH (minNotional $20); paper NO comprueba `minNotional` y live SÍ → divergencia paper/live
**Archivo:** `risk/risk_manager.py:142-144, 158-161, 177-183, 204-205, 209-212, 214-219, 289-295`; `execution/paper_simulator.py:451` (sin filtro); `exchange/binance_client.py:495-501` (con filtro)
**Evidencia:** ejecutado sobre ETH partiendo del tamaño real $280.67:
```
  base.py size_usd            = $280.67
  x 0.7  (micro risk_score=1.0    ) -> $  196.47
  x 0.7  (kyle impact moderado    ) -> $  137.53
  x 0.4  (funding 3bps (warn)     ) -> $   55.01
  x 0.5  (RoR throttle            ) -> $   27.51
  x 0.5  (vol scalar min          ) -> $   13.75
  x 0.4  (corr stress max         ) -> $    5.50
  ...y ademas consecutive_losses=4 -> $2.75   (minNotional ETH = $20)
  grep -c min_notional risk/risk_manager.py -> 0
```
`execution/paper_simulator.py:451` hace `size = signal.size_usd / price` sin ninguna comprobación de `minQty`/`minNotional`/`stepSize`.
**Por qué:** (a) los 7 recortes son multiplicativos y ninguno conoce a los demás, así que el resultado no tiene interpretación de riesgo; (b) no hay suelo: por debajo del `minNotional` la orden se rechaza en `binance_client` (live) pero **se rellena en paper** → el soak paper de CT 104 está midiendo un sistema que en live no habría operado, y las estadísticas de WR/PF que alimentan Kelly/RoR/performance factor no son transferibles; (c) por debajo de ~$40 de notional la comisión round-trip (11 bps ≈ $0.04) más el tick de ADA/SOL hacen el trade inviable aunque se acepte.
**Fix:** un único `size_multiplier` acumulado con clamp explícito (p. ej. `max(0.25, prod(mults))`), y un `min_notional` por símbolo (leído de `exchangeInfo`, ya cacheado en `binance_client._symbol_filters`) inyectado en el `RiskManager`: por debajo → rechazar la señal con log/alerta, no encoger. Replicar `minQty`/`stepSize`/`minNotional` en `paper_simulator` para tener paridad.
**Verificado cómo:** ejecutado (multiplicadores tomados de las ramas reales de `validate_signal`), `grep -c min_notional risk/risk_manager.py` → 0, lectura de `paper_simulator._execute_one`.

### [P2] risk_sizing-06 — `risk_per_trade_pct` tiene DOS significados incompatibles: `strategies/base.py` lo aplica sobre `allocated_capital` ($162.50) y `risk_manager._adjust_position_size` sobre el equity ($1 000); el cap del risk manager es 6.2× más laxo y no muerde nunca
**Archivo:** `strategies/base.py:90-99`, `risk/risk_manager.py:347-365`, `main.py:549` (`kelly_pct` inyectado a la estrategia), `config/settings.py:87`
**Evidencia:**
```python
# strategies/base.py:90-98  (riesgo sobre el capital ASIGNADO)
risk_pct = kelly_risk_pct if ... else self.trading_config.risk_per_trade_pct
risk_amount = capital * risk_pct                 # 162.50 * 0.015 = $2.4375
# risk/risk_manager.py:348-364  (riesgo sobre el EQUITY)
kelly_pct = self.get_kelly_risk_pct(signal.strategy)
max_risk = self._current_equity * kelly_pct      # 1000 * 0.015 = $15.00
max_size_by_risk = (max_risk / risk_per_unit) * signal.entry_price
```
Medido: `max_size_by_risk` = **$2 029 (ETH) / $1 505 (SOL) / $1 052 (ADA)** vs tamaños entregados $280.67 / $217.60 / $150 → el cap del risk manager está 7.2×/6.9×/7.0× por encima y **nunca es el binding constraint**; el binding real es `max_units = capital*leverage/price` de `base.py` (BTC) o `max_position_usd` (ADA).
**Por qué:** el mismo parámetro se interpreta como "1.5 % del capital asignado a este bucket" en un sitio y "1.5 % del equity" en otro. El resultado es que el sistema promete 1.5 % y entrega 0.2 % (01-F13, sigue abierto), que el único límite de riesgo del `RiskManager` es inerte, y que 01-F13 contamina el performance factor (risk_sizing-04) y el sizing de Kelly. Con BTC congelado desde `fb073a1`, además `symbol_share = 1/len(settings.symbols) = 1/4` reparte el 25 % de la asignación de MR a un símbolo que ya no opera: MR sólo puede desplegar 0.65 × 0.75 = **48.75 %** del equity en vez del 65 % que dice `REGIME_WEIGHTS`.
**Fix:** definir una única fuente de verdad: `risk_amount = equity × risk_per_trade_pct` en `base.py` (con `allocated_capital` degradado a cap de notional), y que `symbol_share` use el nº de símbolos ELEGIBLES para esa estrategia (`SYMBOL_STRATEGY_MAP`), no `len(settings.symbols)`.
**Verificado cómo:** ejecutado (tabla señal→tamaño arriba) + lectura de los dos call sites.

### [P2] risk_sizing-07 — Kelly: se activa exactamente en el trade nº100 y salta de 1.5 % a 3 % (el techo) de golpe; con n=100 el IC 95 % del edge incluye el cero, y sin persistencia (01-F19) nunca llegará a 100
**Archivo:** `core/quant_models.py:195-268` (`KellyCriterion.compute`), `config/settings.py:125-127`, `risk/risk_manager.py:73-81`
**Evidencia:** ejecutado con la clase real:
```
  n=99  WR=0.50 payoff=1.5 -> capped=0.0150 valid=False   (default)
  n=100 WR=0.45 payoff=1.5 -> full=0.083 half=0.042 capped=0.0300 valid=True   <- x2 de riesgo
  n=100 WR=0.40 payoff=1.2 -> full=-0.100                 capped=0.0050 valid=True   <- /3 de riesgo
  n=100 WR=0.55 payoff=2.0 -> full=0.325 half=0.163       capped=0.0300 valid=True
  DB de paper (data/trade_database.db): sessions=4, trades=0 filas
```
Con n=100 y WR=0.45, SE(WR)=0.0497 → IC 95 % de WR = [0.353, 0.547]; con b=1.5 el Kelly completo va de **−0.078 a +0.245**: el 0.083 estimado es estadísticamente indistinguible de cero, y aun así el sistema **duplica** el riesgo por trade.
**Por qué:** (a) `capped = max(floor, min(ceiling, half_kelly))` con `floor=0.005` y `ceiling=0.03` convierte Kelly en otra función escalón (edge>0 ⇒ 3 %, edge≤0 ⇒ 0.5 %), no en un sizing continuo; (b) la discontinuidad en n=100 duplica el riesgo por una sola observación; (c) los PnL que alimentan Kelly son **USD crudos mezclando símbolos con notionals distintos** ($280 ETH vs $150 ADA), así que `payoff_ratio = avg_win/avg_loss` mide tamaño además de edge — debería normalizarse a R-múltiplos; (d) `self.kelly` se indexa sólo por `StrategyType`, no por símbolo, y `backtesting/backtester.py:842,881,1109,1188` llama a `record_trade_result(pnl)` **sin `strategy`** → en backtest Kelly nunca se alimenta: si algún día se activa en live, backtest y live divergen en sizing; (e) sin persistencia (01-F19 abierto) y con 0 trades cerrados en la DB tras 4 sesiones, `min_trades=100` no se alcanzará jamás.
**Fix:** rampa continua `w = min(1, (n − min_trades)/min_trades)` entre el default y el half-Kelly; usar R-múltiplos; pasar `strategy=` en el backtester; persistir el historial en `data/`.
**Verificado cómo:** ejecutado (clase real) + aritmética del IC binomial + `sqlite3 data/trade_database.db` + grep de los call sites del backtester.
