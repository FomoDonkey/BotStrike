# Auditoría R2 — persistence: Persistencia, contabilidad y notificaciones

**Fecha:** 2026-08-31 · **Ámbito:** `trade_database/`, `analytics/performance.py`, `data_lifecycle/`, `logging_metrics/logger.py`, `notifications/telegram.py`, `server/serializers.py` + puntos de integración en `main.py` / `server/bridge.py` / `execution/paper_simulator.py`.
**Método:** lectura del código real, snippets ejecutados con `py -3.12`, inspección de `data/trade_database.db` y `logs/metrics.jsonl` reales, contraste con documentación oficial cuando se afirma un comportamiento externo.
**Ronda 1 (contexto):** hallazgos previos en este ámbito: 03-P1-7 (estado paper en memoria), 03-P2-14 (logs), 03-P2-17 (SQLite backup/checkpoint), 03-P3-19 (shutdown duplicado), 03-P3-27 (Telegram), 04-P2 (Sharpe), 02-P2-17 (paper sin funding). No se repiten salvo que sigan abiertos o el fix sea incompleto.

## Hallazgos

### [P0] persistence-01 — En LIVE cada fill de ENTRADA se contabiliza como trade cerrado: `total_trades` x2, `win_rate` /2, y el PnL reportado es BRUTO de comisiones

**Archivo:** `logging_metrics/logger.py:164` (`MetricsCollector.add_trade`), `analytics/performance.py:187`

**Evidencia:**
```python
# logging_metrics/logger.py:162-174
# Running totals — only count EXITS (round-trip completed)
# Entry trades have pnl=0 and fee=0; they're not "completed trades"
is_exit = trade.pnl != 0 or trade.fee > 0
...
# analytics/performance.py:186-187
report.total_fees = sum(fees)
report.net_pnl = report.total_pnl  # pnl already includes fees in backtester
```
El comentario («entry trades have pnl=0 **and fee=0**») solo es cierto en **paper**
(`execution/paper_simulator.py:570` `fee=0.0`, `:573` `pnl=0.0`, el round-trip se cobra al cerrar).
En **LIVE** el Trade lo construye `execution/order_engine.py:502-513` desde el fill del WS:
`fee=commission` (campo `n`, presente en TODO fill, también en las aperturas) y
`pnl=realized_pnl` (campo `rp` de Binance USDT-M, que es realized profit **bruto de comisión**).
Ejecutado con el código real (`py -3.12`), un único round-trip ganador live:
```
LIVE 1 round-trip ganador -> {'total_trades': 2, 'win_rate': 0.5, 'total_pnl': 3.0,
                              'total_fees': 0.48, 'net_pnl': 3.0}
  esperado: total_trades=1, win_rate=1.0, net_pnl=3.0-0.4812=2.5188
```

**Por qué:** es el número que se enseña en Telegram (`notifications/telegram.py:181,204`
`net_pnl = m.get("net_pnl", pnl)` → «💰 PnL neto») y en `/api/performance` cuando el DB no
responde (`server/bridge.py:876`). En live el bot reportaría **+3.00 $ de «PnL neto»** cuando
realmente ganó 2.52 $, y un win rate del 50 % cuando fue del 100 %. Con 200 round-trips/día y
fee taker 0.04 % sobre 60 k$ de nocional son ~48 $/día de comisiones invisibles: el sistema
puede estar perdiendo dinero y mostrando PnL positivo. Además `profit_factor`, `avg_win`,
`avg_loss` y `sharpe` se calculan sobre esa misma base contaminada por trades fantasma con pnl=0.

**Fix:** no inferir el tipo de trade por el fee. Propagar un flag explícito desde el productor
del Trade (`order_engine.on_order_update` conoce `reduceOnly`/`ps`/posición previa; el paper ya
sabe si es entrada o salida) y usarlo en `MetricsCollector.add_trade`, `serialize_trade` y
`trade_db.on_trade`. Y separar de verdad bruto/neto: `net_pnl = total_pnl - total_fees` cuando
la fuente es live (Binance `rp` es bruto), `net_pnl = total_pnl` cuando es paper/backtest
(ahí el fee ya está descontado en `PaperPosition.close`, `execution/paper_simulator.py:114`).

**Verificado como:** snippet ejecutado con `py -3.12` importando `MetricsCollector` real y
construyendo el `Trade` exactamente como lo hace `order_engine.on_order_update` (salida arriba).

---

### [P1] persistence-02 — `serialize_trade` invierte el lado de TODAS las entradas live: una compra se muestra como venta en el dashboard

**Archivo:** `server/serializers.py:85-91`

**Evidencia:**
```python
is_exit = t.pnl != 0 or t.fee > 0 or t.order_id.startswith("paper_exit") or ...
if is_exit:
    display_side = "SELL" if _enum_val(t.side) == "BUY" else "BUY"
else:
    display_side = _enum_val(t.side)
```
Mismo heurístico roto que persistence-01. Ejecutado:
```
serialize_trade(ENTRADA LIVE BUY) -> side= SELL  trade_type= EXIT
  esperado: side=BUY, trade_type=ENTRY
```

**Por qué:** el operador que mire el panel de trades en live verá cada apertura de LARGO
etiquetada como «SELL / EXIT». Es exactamente la información que se usa para decidir si
intervenir manualmente cuando algo va mal; enseñar el lado invertido en el peor momento es
peor que no enseñar nada. En paper no se dispara (entradas con `fee=0`, `pnl=0` y
`order_id="paper_entry_..."`), por eso no se ha visto todavía.

**Fix:** el mismo flag explícito de persistence-01. Como parche mínimo, exigir además que el
`order_id` no sea de entrada y que `pnl != 0` para considerar salida — pero lo correcto es que
`Trade` lleve el campo (`is_reduce_only` / `trade_type`).

**Verificado como:** snippet con `serialize_trade` real sobre el `Trade` que produce
`order_engine.on_order_update` en una apertura.

---

### [P1] persistence-03 — Sortino usa `np.std(solo los días negativos)` en vez de la downside deviation: reporta 0.00 con el mejor perfil de pérdidas y lo infla un 60 % en el resto

**Archivo:** `analytics/performance.py:243-250`

**Evidencia:**
```python
# Sortino: solo penaliza downside volatility
downside = daily_arr[daily_arr < 0]
if len(downside) > 1:
    downside_std = float(np.std(downside))
    if downside_std > 0:
        report.sortino_ratio = float(daily_mean / downside_std * np.sqrt(self.ANNUALIZATION_FACTOR))
```
La downside deviation de Sortino & Price es `sqrt(mean_sobre_TODAS_las_obs(min(r-target,0)^2))`,
no la desviación típica **de** los negativos respecto a **su propia media**. Ejecutado:
```
retornos: [0.02, 0.03, -0.01, 0.04, -0.01, 0.05, -0.01, 0.02]
np.std(solo negativos) = 0.0 -> Sortino implementado = 0.0
downside deviation correcta = 0.006123 -> Sortino correcto = 50.7

caso 2: [0.02, 0.03, -0.01, 0.04, -0.02, 0.05, -0.03, 0.02]
 Sortino implementado = 29.25
 Sortino correcto     = 18.05
```

**Por qué:** el caso 1 no es artificial — es el perfil de una estrategia con stop-loss fijo:
todas las pérdidas del mismo tamaño. Ahí `np.std(negativos)=0` y el guard `if downside_std > 0`
deja el Sortino en **0.00**, o sea la estrategia con el mejor perfil de riesgo posible se
reporta como la peor. En el caso general el denominador ignora la magnitud de las pérdidas
(solo mide su dispersión) → Sortino inflado un 62 % aquí. Se publica en `/api/performance`
(`server/bridge.py:845`) y es una de las tres cifras con las que se decide si el sistema pasa
a live.

**Fix:**
```python
downside_dev = float(np.sqrt(np.mean(np.minimum(daily_arr, 0.0) ** 2)))
if downside_dev > 0:
    report.sortino_ratio = float(daily_mean / downside_dev * np.sqrt(self.ANNUALIZATION_FACTOR))
```
(sin el `len(downside) > 1`, que además descarta el caso de una única pérdida).

**Verificado como:** snippet numérico con numpy comparando ambas fórmulas sobre dos series
(salida arriba).

---

### [P1] persistence-04 — Sharpe/Sortino se anualizan con √365 sobre una serie que solo contiene los días CON trades: 3,3x de inflación en la muestra de prueba

**Archivo:** `analytics/performance.py:599-625` (`_aggregate_daily_returns`), usado en `:233-250`

**Evidencia:**
```python
daily_pnl: dict = defaultdict(float)
for t in trades:
    day = int(t.timestamp // 86400)
    daily_pnl[day] += t.pnl
...
for pnl in daily_pnl.values():
    daily_returns.append(pnl / equity if equity > 0 else 0.0)
```
El dict solo tiene clave para los días en los que hubo al menos un trade. Los días sin
operar (fin de semana de baja vol, halt por drawdown, bot parado, régimen filtrado) **no**
entran como 0.0, así que ni bajan la media ni suben la desviación, pero el factor √365 sigue
asumiendo 365 observaciones/año. Ejecutado con `PerformanceAnalyzer` real, 10 días con trade
repartidos en 28 días de calendario:
```
Sharpe (10 dias con trade repartidos en 30 dias calendario): 40.83
Sharpe con calendario completo ( 28 dias): 12.36
```

**Por qué:** el mismo defecto está en `logging_metrics/logger.py:210-219`
(`daily_values = list(self._daily_pnl.values())`, `* (365 ** 0.5)`). Un bot que solo opera
1 día de cada 3 verá su Sharpe multiplicado por √3. Es precisamente la métrica que decide el
paso a live, y el sesgo va siempre en la dirección favorable.

**Fix:** reindexar sobre el calendario completo del periodo antes de anualizar:
```python
lo, hi = min(daily_pnl), max(daily_pnl)
serie = [daily_pnl.get(d, 0.0) for d in range(lo, hi + 1)]
```
y normalizar por la equity rodante como ya se hace. Alternativa (más honesta si el bot está
parado a ratos): anualizar por el número real de días de calendario transcurridos,
`sqrt(len(serie_completa) / span_dias * 365)`.

**Verificado como:** snippet ejecutado con `analytics.performance.PerformanceAnalyzer.analyze`
real vs. cálculo manual con calendario relleno (salida arriba).

---

### [P1] persistence-05 — `/api/performance` recarga y reanaliza TODO el histórico de trades en el event loop cada 5 s: 1,1 s de bloqueo con 25 k trades, 4,7 s con 100 k

**Archivo:** `server/bridge.py:817` (`_cumulative_performance`), llamado desde `metrics_broadcast_loop` (`:910`, cada 2 s) y `/api/performance` (`:1318`)

**Evidencia:**
```python
_PERF_CACHE_TTL_SEC = 5.0
...
trades = engine.trade_repo.get_trades(source="paper")          # SELECT * sin LIMIT
closes = [t for t in trades if t.trade_type and t.trade_type != "ENTRY"]
...
rep = PerformanceAnalyzer().analyze(closes, initial_equity=initial, use_equity_after=False)
```
`metrics_broadcast_loop` (`while True: ... await asyncio.sleep(2)`) es una corrutina del MISMO
event loop que el engine, y ni `get_trades` ni `analyze` son `await` — bloquean. Benchmark
ejecutado con el `TradeRepository` y el `PerformanceAnalyzer` reales sobre una DB sintética:
```
N=   5000  get_trades=   181.3 ms   analyze=   18.5 ms   total=  199.8 ms   db= 1.2 MB
N=  25000  get_trades=   948.9 ms   analyze=  123.7 ms   total= 1072.5 ms   db= 5.6 MB
N= 100000  get_trades=  4213.3 ms   analyze=  466.0 ms   total= 4679.3 ms   db=22.9 MB
```

**Por qué:** con 25 k trades el loop queda bloqueado 1,07 s cada 5 s (**21 % del tiempo**); con
100 k, 4,7 s cada 5 s (**94 %**). Durante ese bloqueo no corre nada: ni `_strategy_loop`
(que es donde el paper simulator evalúa SL/TP, `main.py:425`), ni `_risk_monitor_loop`, ni el
pong del WS de Binance. Un SL que debía dispararse se evalúa varios segundos tarde, y el
tamaño del bloqueo **crece sin límite** con el histórico: es una bomba de relojería que no se
manifiesta hoy (la DB tiene 0 trades) pero sí a los meses de paper continuo. El CT 104 es más
lento que la máquina donde se midió esto.

**Fix:** (1) sacar el cálculo del loop: `await asyncio.to_thread(_cumulative_performance)`;
(2) no traer filas completas — agregar en SQL (`SELECT COUNT(*), SUM(pnl), SUM(fee) ... WHERE
source=? AND trade_type<>'ENTRY'`) y guardar solo lo que necesita el gráfico; (3) mantener
acumuladores incrementales persistidos (una fila de resumen por sesión, ya existe la tabla
`sessions`) en vez de reanalizar el histórico entero cada 5 s; (4) subir el TTL de caché y/o
invalidar por evento (nuevo trade) en lugar de por tiempo.

**Verificado como:** benchmark ejecutado con `py -3.12` creando una DB temporal con el
`TradeRepository` real y midiendo `get_trades` + `PerformanceAnalyzer().analyze` (salida arriba).

---

### [P1] persistence-06 — En modo LIVE `/api/performance` reporta siempre 0 trades y 0 PnL: la consulta está fijada a `source="paper"` y los trades live se guardan sin `trade_type`

**Archivo:** `server/bridge.py:817-818`, `main.py:359-366`

**Evidencia:**
```python
# server/bridge.py:817-818
trades = engine.trade_repo.get_trades(source="paper")
closes = [t for t in trades if t.trade_type and t.trade_type != "ENTRY"]
```
```python
# main.py:115-117  — la fuente depende del modo
self.trade_db = TradeDBAdapter(self.trade_repo, source="paper" if self.paper else "live")
# main.py:359-366  — el camino LIVE NO pasa trade_type (queda "")
self.trade_db.on_trade(
    trade, regime=regime,
    equity_before=..., equity_after=...,
    micro_vpin=..., micro_risk_score=...,
)   # <- sin trade_type=, sin slippage_bps=, sin entry_price=, sin pnl_pct=
```
Doble filtro que elimina el 100 % de los trades live: por `source` (se guardan como `'live'`,
se consulta `'paper'`) y por `trade_type` (`""` es falsy → `if t.trade_type` los descarta).

**Por qué:** el día que se pase a live, el panel principal y el WS `metrics` mostrarán
equity = capital inicial y PnL = 0.00 permanentemente, mientras la cuenta real se mueve.
`_merged_performance` (`:890-897`) construye `equity` como `initial_capital + cum["pnl"] +
unrealized`, y `unrealized` solo mira `paper_sim` (`:797`), que en live es `None` → 0. Es decir,
en live el dashboard queda **congelado en el capital inicial**. Además `slippage_bps`,
`mae/mfe`, `order_type` y `pnl_pct` quedan a 0 en la DB para todos los trades live, así que el
análisis post-mortem del periodo live no tendrá calidad de ejecución.

**Fix:** derivar la fuente del engine (`source = "paper" if engine.paper else "live"`) en
`_cumulative_performance`, y en `main.py:359` pasar `trade_type` (ENTRY/EXIT, derivado del flag
explícito de persistence-01) junto con `slippage_bps=trade.actual_slippage_bps`,
`order_type`, `entry_price` y `pnl_pct`, igual que ya hace `_process_paper_fill` (`main.py:660-681`).

**Verificado como:** lectura del código real de ambos caminos; el filtro `source="paper"` es
literal y no hay ninguna rama que lo cambie (`grep -n "_cumulative_performance\|source=" server/bridge.py`).

---

### [P1] persistence-07 — 03-P1-7 SIGUE ABIERTO y tiene un efecto contable que la ronda 1 no registró: tras un kill -9 la sesión queda huérfana en la DB con PnL 0 y la posición abierta desaparece del track record

**Archivo:** `trade_database/adapter.py:89-100` / `:102-131`, `execution/paper_simulator.py` (sin persistencia), `main.py:178`

**Evidencia — DB real del repo:**
```
$ py -3.12 ... select session_id,end_time,total_trades,total_pnl,final_equity from sessions
{'session_id': 'f9cede9fd637', 'end_time': 0.0, 'total_trades': 0, 'total_pnl': 0.0, 'final_equity': 0.0}
{'session_id': '2fd712bb762c', 'end_time': 1788057555.1, 'total_trades': 0, 'total_pnl': 0.0, 'final_equity': 1000.0}
{'session_id': '9e8df68e3d52', 'end_time': 0.0, 'total_trades': 0, 'total_pnl': 0.0, 'final_equity': 0.0}
{'session_id': '199bdcd0d0dc', 'end_time': 0.0, 'total_trades': 0, 'total_pnl': 0.0, 'final_equity': 0.0}
```
3 de 4 sesiones ya están huérfanas. Reproducido con un hijo real que hace `os._exit(9)`
(equivalente a kill -9) tras 3 salidas de −2 $:
```
--- sessions tras kill ---  {'end_time': 0.0, 'total_trades': 0, 'total_pnl': 0.0, 'final_equity': 0.0}
--- trades tras kill ---    (3, -6.0)
```
Los trades sí sobreviven (`insert_trade` commitea uno a uno), pero el resumen de sesión NO, y
**ningún arranque posterior lo repara**: `start_session` solo crea una fila nueva
(`adapter.py:89-97`) y no existe ninguna consulta ni reconciliación de `end_time = 0`
(`grep end_time` sobre `trade_database/`, `main.py`, `server/bridge.py`: solo definiciones).
`execution/paper_simulator.py` no tiene **ninguna** persistencia (`grep -n "json\|pickle\|save\|load\|persist"` → 0 coincidencias) y `risk_manager` reinicia `_current_equity = initial_capital`.

**Por qué:** dos daños distintos.
(a) *Contabilidad*: la tabla `sessions` — el único resumen por sesión — miente (0 trades, 0 PnL,
final_equity 0) para cualquier sesión que no acabe con un shutdown limpio, que es el caso
mayoritario en el CT (systemd `Restart=always`, watchdog `_hard_exit(3)` en `server/bridge.py:1036`).
(b) *Integridad del track record (lo grave)*: una posición paper abierta en el momento del
crash desaparece. Su ENTRY está en la DB pero nunca habrá EXIT, y `_cumulative_performance`
filtra los ENTRY (`bridge.py:818`) → la posición se evapora sin realizar su pérdida. Como los
crashes/reinicios correlacionan con caídas de WS y volatilidad, las posiciones que se pierden
son sistemáticamente las que iban perdiendo: **sesgo de supervivencia a favor del bot** en la
única evidencia con la que se decidirá pasar a dinero real.

**Fix:** (1) al arrancar, reconciliar: `UPDATE sessions SET end_time=?, total_trades=(SELECT
COUNT(*)...), total_pnl=(SELECT SUM(pnl)...), notes=notes||' [crash-recovered]' WHERE end_time=0`
y avisar por log/Telegram de cuántas se recuperaron; (2) persistir el estado del paper
simulator (posiciones abiertas + equity) en la propia SQLite en cada fill y restaurarlo al
arrancar — o, como mínimo, marcar en la DB las posiciones abiertas al arrancar como
`trade_type='ABANDONED'` con su unrealized a último precio para que no desaparezcan del análisis.

**Verificado como:** inspección de `data/trade_database.db` real + hijo `os._exit(9)` ejecutado
con el `TradeDBAdapter`/`TradeRepository` reales (salidas arriba) + `grep` de persistencia en
`paper_simulator.py`.

---

### [P1] persistence-08 — El token de autenticación del bridge viaja en el query string y uvicorn lo escribe en claro en el access log (journald)

**Archivo:** `server/bridge.py:1217` + `:1595`, `desktop/src/lib/api.ts:234`

**Evidencia:**
```python
# server/bridge.py:1217-1224
async def require_token_when_remote(token: str = "", x_botstrike_token: str = Header(default="")):
    """... (query `token=` o header `X-BotStrike-Token`)."""
# server/bridge.py:1590-1595
uvicorn.run(..., log_level="info", ...)
```
```ts
// desktop/src/lib/api.ts:234  — el cliente usa la variante de query string
return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
```
uvicorn construye la línea de acceso con el query string completo (verificado en el uvicorn
instalado):
```python
def get_path_with_query_string(scope):
    path_with_query_string = urllib.parse.quote(scope["path"])
    if scope["query_string"]:
        path_with_query_string = "{}?{}".format(path_with_query_string, scope["query_string"].decode("ascii"))
    return path_with_query_string
```
→ `journalctl -u botstrike-bridge` contiene literalmente
`"POST /api/bot/start?mode=live&token=<BOTSTRIKE_AUTH_TOKEN> HTTP/1.1" 200`.

**Por qué:** `BOTSTRIKE_AUTH_TOKEN` es lo único que separa a un nodo cualquiera de la tailnet/LAN
de `POST /api/bot/start?mode=live` (arrancar el bot con dinero real). Queda en texto plano en
el journal, que se rota/archiva/copia y que cualquiera con lectura de logs (o cualquier
recolector de logs futuro) puede leer. Es la razón por la que OWASP y RFC 6750 §2.3 prohíben
credenciales en la URL. La ronda 1 (03-P0 token) arregló *quién* puede leer el token por la API,
pero no que el propio token se autoregistre en los logs en cada uso.

**Fix:** (1) aceptar el token SOLO por cabecera `X-BotStrike-Token` (quitar el parámetro `token`
de `require_token_when_remote`) y cambiar `desktop/src/lib/api.ts:234` para que ponga la
cabecera; para el WS, usar el primer mensaje `{"type":"auth"}` en vez de `?token=`;
(2) mientras exista el parámetro, `uvicorn.run(..., access_log=False)` o un
`logging.Filter` sobre `uvicorn.access` que redacte `token=[^&]*`.

**Verificado como:** `inspect.getsource(uvicorn.protocols.utils.get_path_with_query_string)`
ejecutado con `py -3.12` sobre el uvicorn instalado (salida arriba) + lectura de
`server/bridge.py:1595` y `desktop/src/lib/api.ts:234`.

---

### [P2] persistence-09 — Si falla la escritura de `metrics.jsonl` el buffer NUNCA se vacía: fuga de memoria sin límite (~1,4 GB/día) y reintento O(n) en cada flush

**Archivo:** `logging_metrics/logger.py:112-130`

**Evidencia:**
```python
def _flush_metrics(self) -> None:
    if not self._metric_buffer:
        return
    try:
        ...
        with open(self.metrics_file, "a") as f:
            f.write("\n".join(self._metric_buffer) + "\n")
        self._metric_buffer.clear()        # <- dentro del try, DESPUÉS del write
    except Exception as e:
        logger.error("metric_write_error", error=str(e))
```
Ejecutado con `TradingLogger` real y `open()` parcheado para lanzar `OSError(28)`:
```
entradas retenidas en RAM tras 1000 metricas con disco lleno: 1000
bytes retenidos: 35890
errores logueados: 100
```

**Por qué:** el sistema escribe ~49 MB/día en este archivo (medido, ver persistence-10). Con el
disco lleno, permisos rotos tras un `chown` de logrotate, o el filesystem en solo-lectura, el
buffer crece a ~1,4 GB/día en RAM **y** cada flush reintenta serializar y escribir el buffer
completo (`"\n".join` de n elementos cada 10 métricas → O(n²) acumulado). En un LXC con
memoria limitada esto convierte un disco lleno (recuperable) en un OOM-kill del engine con
posiciones abiertas. Y como el `except` solo loguea, se emiten 4 líneas de error por segundo a
stderr → journald, agravando el disco lleno.

**Fix:** mover `self._metric_buffer.clear()` fuera del `try` (o a un `finally`), y acotar el
buffer: `deque(maxlen=10_000)` con contador de descartes, más un backoff en el log de error
(loguear 1 de cada N).

**Verificado como:** snippet con `logging_metrics.logger.TradingLogger` real y `builtins.open`
parcheado (salida arriba).

---

### [P2] persistence-10 — La rotación interna a 50 MB y el logrotate nuevo se pisan: `rotate 14`/`compress` no retienen nada, `.old` queda fuera del glob, y el comentario que justifica `copytruncate` es falso

**Archivo:** `logging_metrics/logger.py:117-124`, `deploy/logrotate-botstrike:1-13`

**Evidencia:**
```python
# logging_metrics/logger.py:117-124
if os.path.exists(self.metrics_file):
    size_mb = os.path.getsize(self.metrics_file) / (1024 * 1024)
    if size_mb > 50:
        rotated = self.metrics_file + ".old"
        if os.path.exists(rotated):
            os.remove(rotated)
        os.rename(self.metrics_file, rotated)
```
```
# deploy/logrotate-botstrike:2
# copytruncate: the engine keeps the file handle open; truncating in place is safe for append-only JSONL.
/opt/botstrike/app/logs/*.jsonl /opt/botstrike/app/logs/*.log {
    daily / rotate 14 / maxsize 200M / compress / delaycompress / copytruncate
}
```
Volumen real medido (413 B/línea de `microstructure` medidos sobre `logs/metrics.jsonl`,
`strategy_interval_sec=3.0`, 4 símbolos → `main.py:503` escribe una línea por símbolo y ciclo):
```
microstructure:    115,200 lineas/dia x 413 B = 47.6 MB/dia
portfolio_snapshot:  1,440 lineas/dia x 1171 B =  1.7 MB/dia
TOTAL aprox: 49.3 MB/dia   ->  dias hasta la rotacion interna de 50 MB: 1.01
```
Tres problemas concretos:
1. **El comentario es falso.** `_flush_metrics` abre y cierra el archivo en cada flush
   (`with open(self.metrics_file, "a")`, cada 10 métricas ≈ cada 2,5 s); el engine NO mantiene
   el handle abierto. La premisa que justifica elegir `copytruncate` (el modo **con** ventana
   de pérdida de datos entre el copy y el truncate) no se cumple: el modo `create` por defecto
   habría sido estrictamente mejor y sin pérdida, porque el escritor reabre por ruta.
2. **`rotate 14` y `compress` no retienen nada.** La rotación interna dispara cada ~1,01 días
   —antes o a la vez que logrotate— y `metrics.jsonl.old` **no** casa con `*.jsonl` ni con
   `*.log`, así que logrotate no lo ve: nunca se comprime, nunca se borra, y el histórico real
   queda acotado a 1 archivo (~50 MB) en lugar de los 14 días prometidos.
3. **Carrera real.** Si `os.rename()` cae entre el `copy` y el `truncate` de logrotate, se
   trunca/recrea el archivo equivocado y se pierde el tramo copiado.

**Por qué:** el JSONL es la única evidencia en disco de microestructura y snapshots de portfolio
(audit 03-P2-14: no hay ningún otro log en disco). Creer que hay 14 días comprimidos cuando en
realidad hay ~1 día y medio invalida cualquier post-mortem a más de 48 h.

**Fix:** quitar la rotación interna de `_flush_metrics` (dejar la rotación al sistema) o, si se
quiere mantener autónoma, renombrar a `metrics-YYYYMMDD-HHMMSS.jsonl` (que sí casa con el glob)
en vez de `.old`. Corregir el comentario de `deploy/logrotate-botstrike` y cambiar
`copytruncate` por el modo por defecto (`create 0640 botstrike botstrike`), que con este
escritor es lossless. Y reducir el volumen en origen: `microstructure` cada ciclo de 3 s por
símbolo es un muestreo absurdo para lo que se usa — 1 línea/minuto/símbolo baja el archivo de
47,6 MB/día a 2,4 MB/día.

**Verificado como:** medición de bytes/línea por tipo sobre `logs/metrics.jsonl` real
(`portfolio_snapshot` 1171 B, `microstructure` 413 B) + `Settings()` real para símbolos e
intervalo + lectura de `logging_metrics/logger.py:126` (`open(..., "a")`, sin handle persistente).
