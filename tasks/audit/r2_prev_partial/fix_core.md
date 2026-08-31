# Audit R2 — fix_core: revisión adversarial de los fixes ronda 1 en core

Fecha: 2026-08-30 · Commit revisado: `b3dbf75` (`main.py`, `execution/order_engine.py`,
`execution/paper_simulator.py`, `portfolio/portfolio_manager.py`, `config/settings.py`) ·
Baseline: `py -3.12 -m pytest tests/ -q -p no:cacheprovider` (se ejecuta al final).

Registro incremental: cada hallazgo se añade en cuanto se confirma con código real / snippet ejecutado.

## Hallazgos

### [P0] fix_core-01 - `_flatten_all()` ejecuta `cancel_all()` aunque el cierre haya FALLADO o queden posiciones abiertas -> vuelve a dejar posiciones desnudas (F01 solo arreglado en el camino feliz)
**Archivo:** `main.py:857-871` (+ `execution/order_engine.py:561-563`, `exchange/binance_client.py:777-784`)
**Evidencia:**
```python
# main.py:857-871
result: Dict = {}
try:
    result = await self.execution_engine.close_all_positions()
except Exception as e:
    logger.error("flatten_close_positions_failed", reason=reason, error=str(e))
remaining = result.get("remaining") if isinstance(result, dict) else None
if remaining:
    logger.critical("flatten_incomplete_positions_remain", ...)
    asyncio.ensure_future(self.notifier.notify_error(...))
...
await self.execution_engine.cancel_all()          # <- se ejecuta SIEMPRE
```
```python
# order_engine.py:561-563 — el error del cliente se traga y devuelve remaining=[]
except Exception as e:
    logger.error("close_all_positions_failed", error=str(e))
    return {"closed": [], "remaining": [], "errors": [str(e)]}
```
Snippet ejecutado (`scratchpad/flatten_probe.py`, `_flatten_all` real con engine mockeado):
```
remaining!=[]                 -> ['close_all_positions', 'cancel_all']
close raised (positionRisk 503)-> ['close_all_positions', 'cancel_all']
engine on native error        -> {'closed': [], 'remaining': [], 'errors': ['timeout']}   -> cancel_all
engine fallback all reads fail-> remaining=[] errors=[get_positions x2]                      -> cancel_all
engine fallback order fails   -> remaining=[ETH-USD]                                         -> cancel_all
```
**Por que:** El objetivo de F01/P0-03 era "nunca cancelar SL/TP con la posicion abierta". El fix solo lo cumple si `close_all_positions` tiene exito. En los casos reales de fallo (positionRisk 5xx/timeout, -1001/-1021 en la MARKET, IP baneada por rate limit, o cliente sin `close_all_positions` ni `get_positions`) el codigo sigue hasta `cancel_all()` y elimina las protectivas de las posiciones que NO pudo cerrar: exactamente el escenario a 5x del hallazgo original, y ademas con `_dd_flattened=True` (halt) o `_shutdown_flatten_done=True` (shutdown) no hay ningun reintento posterior. `remaining=[]` con `errors` no vacio es indistinguible de "todo cerrado".
**Fix:** (1) En `_flatten_all`: `ok = isinstance(result, dict) and not result.get("remaining") and not any(e.get("stage")=="get_positions" ... for e in result.get("errors", []))`; solo si `ok` -> `cancel_all()`; si no -> CRITICAL + Telegram y NO cancelar (las SL/TP del exchange son la unica proteccion que queda). (2) En `OrderExecutionEngine.close_all_positions` y `BinanceClient.close_all_positions`, cuando no se ha podido leer el estado devolver `remaining=None`/`"unknown": True` en vez de `[]`. (3) En el halt por DD, si el flatten no fue completo, dejar `_dd_flattened=False` para reintentar en el siguiente ciclo (con backoff). (4) Test: `close_all` que devuelve `remaining` no vacio o lanza -> `cancel_all` NO llamado.
**Verificado como:** lectura de `main.py:857-871`, `order_engine.py:555-608`, `binance_client.py:729-789` + snippet ejecutado con `py -3.12` (salida arriba).

### [P1] fix_core-03 - Performance factor: el normalizador "R" usa `equity x risk_per_trade_pct` ($15) pero el riesgo REAL por trade es `allocated x risk_pct` ($0.34-$3.07) -> el factor queda en [0.85, 1.0] y el gate de bloqueo (0.6) es inalcanzable: la "probation" nunca se ejecuta en produccion
**Archivo:** `portfolio/portfolio_manager.py:215-241` (+ `strategies/base.py:90-91`, `portfolio_manager.py:208`)
**Evidencia:**
```python
# portfolio_manager.py:215-220
def _risk_budget_per_trade(self) -> float:
    equity = self.risk_manager.current_equity
    ...
    return max(equity * self.config.risk_per_trade_pct, 1e-6)      # $1000 x 1.5% = $15
# :239-241
avg_r = (sum(closed) / n) / self._risk_budget_per_trade()
factor = 1.0 + 0.5 * math.tanh(1.5 * avg_r)
```
```python
# strategies/base.py:90-91 — lo que de verdad se arriesga por trade
risk_pct = kelly_risk_pct if kelly_risk_pct is not None else self.trading_config.risk_per_trade_pct
risk_amount = capital * risk_pct        # capital = allocated_capital = equity*peso*share (main.py:545)
```
Snippet ejecutado (`scratchpad/perf_probe.py`, `Settings()` real, `get_allocation` + `_calc_position_size` reales, SL 1.2%):
```
normalizer _risk_budget_per_trade = 15.0
RANGING     MEAN_REVERSION   alloc=$162.50 risk_usd=$2.21 avg_r(1R real loss)=-0.148 factor=0.891 blocks?=False
TRENDING_UP FIBONACCI        alloc=$175.00 risk_usd=$2.38 avg_r(1R real loss)=-0.159 factor=0.883 blocks?=False
BREAKOUT    MEAN_REVERSION   alloc=$ 25.00 risk_usd=$0.34 avg_r(1R real loss)=-0.023 factor=0.983 blocks?=False
BREAKOUT    FIBONACCI        alloc=$225.00 risk_usd=$3.07 avg_r(1R real loss)=-0.204 factor=0.851 blocks?=False
avg_r needed to block: -0.732 => avg loss per trade needed: $10.99
```
**Por que:** Una estrategia que pierde el 100% de su riesgo en CADA trade (peor caso fisico: SL a 1R + slippage) obtiene factor 0.85-0.98, no 0.5; para bloquearse necesitaria perder ~$11 de media por trade cuando su perdida maxima posible es ~$2-3. Es decir: F03 paso de "desactiva para siempre con -$0.03" a "no reacciona nunca". Los tests (`-30`/`-500` por trade) usan perdidas imposibles con el sizing real y por eso pasan. El valor de un performance factor es cortar una estrategia que esta rota (p.ej. datos 1H mal calentados, F05/F06) antes de que la friccion se coma la cuenta; hoy es decorativo. Afecta igual a paper y live.
**Fix:** Normalizar con el riesgo que la estrategia realmente asumio: guardar en cada Trade de salida el `risk_usd` de la entrada (`size x |entry - sl|`, ya disponible en `PaperPosition`/`signal.stop_loss`) y hacer `avg_r = mean(pnl_i / risk_i)`; fallback `allocated x kelly_pct` si no hay SL. Alternativa minima: `_risk_budget_per_trade()` = `self._last_allocation[strategy] x risk_pct`. Ajustar tests con perdidas realistas (-1R = -$2.2) y comprobar que 20 trades a -1R bloquean y 20 a -0.3R solo reducen.
**Verificado como:** lectura de `portfolio_manager.py:215-252`, `strategies/base.py:81-121`, `main.py:543-556` + snippet ejecutado con `py -3.12` (salida arriba).

