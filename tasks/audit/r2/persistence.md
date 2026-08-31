# Auditoría R2 — AREA: persistence

**Alcance:** `trade_database/`, `analytics/performance.py`, `data_lifecycle/`, `logging_metrics/logger.py`,
`notifications/telegram.py`, `server/serializers.py`, y los puntos de contabilidad de
`server/bridge.py` (`_cumulative_performance` / `_merged_performance`) y `main.py`
(`_process_paper_fill`, `on_order_update`, `shutdown`).

**Fecha:** 2026-08-31 · **Auditor:** agente `persistence` (ronda 2)
**Base de datos analizada:** `data/trade_database.db` (local) — 5 sesiones, 0 trades, integridad `ok`, journal `wal`.

> Escritura incremental: cada hallazgo se añade en cuanto queda **verificado con código real
> o con una ejecución**. Nada de suposiciones.

---

## Hallazgos

### [P0] persistence-01 — En LIVE la contabilidad es la de PAPER: `/api/performance` y `/api/trades` filtran `source="paper"` y la ruta live nunca rellena `trade_type`

**Archivo:** `server/bridge.py:863-864`, `server/bridge.py:1426-1428`, `main.py:359-366`

**Evidencia:**
```python
# server/bridge.py:862-864  (_cumulative_performance)
        initial = float(engine.settings.trading.initial_capital)
        trades = engine.trade_repo.get_trades(source="paper")
        closes = [t for t in trades if t.trade_type and t.trade_type != "ENTRY"]
```
```python
# server/bridge.py:1426-1428  (/api/trades)
        records = state.engine.trade_repo.get_trades(
            source="paper", limit=limit,
        )
```
```python
# main.py:359-366  — ruta LIVE (on_order_update). NO pasa trade_type ni entry_price
                        self.trade_db.on_trade(
                            trade,
                            regime=regime,
                            equity_before=self.risk_manager.current_equity,
                            equity_after=self.risk_manager.current_equity + trade.pnl,
                            micro_vpin=micro.vpin.vpin if micro and micro.vpin else 0,
                            micro_risk_score=micro.risk_score if micro else 0,
                        )
```
```python
# main.py:114-117 — el source depende del modo
        self.trade_repo = TradeRepository("data/trade_database.db")
        self.trade_db = TradeDBAdapter(
            self.trade_repo, source="paper" if self.paper else "live"
        )
```

**Por qué:** en modo live los trades se persisten con `source="live"` y con `trade_type=""`
(el default del dataclass, porque el kwarg no se pasa). Las **dos** rutas que alimentan la UI
filtran `source="paper"`, así que:
1. `/api/performance` devuelve el histórico de **paper** — PnL, equity, Sharpe, win-rate — y lo
   presenta como el rendimiento del bot mientras opera con dinero real.
2. `/api/trades` devuelve la lista de operaciones de paper; **ninguna operación live aparece jamás**.
3. Aunque se corrigiera el `source`, el filtro `t.trade_type and t.trade_type != "ENTRY"` descarta
   todo lo que tenga `trade_type == ""` → seguiría dando 0 trades y 0 PnL.

Es decir: el día que se active `BOTSTRIKE_ALLOW_LIVE`, el operador ve una pantalla de dinero
**que no corresponde a su cuenta** y no tiene ningún aviso. Hoy es latente (live devuelve 403 sin
`BOTSTRIKE_ALLOW_LIVE` y Binance está cerrado para residentes ES), pero es un fallo de contabilidad
total en el único modo que importa.

**Fix:**
```python
# bridge.py — usar el source del adapter, no una constante
src = getattr(engine.trade_db, "source", "paper")
trades = engine.trade_repo.get_trades(source=src)
closes = [t for t in trades if (t.trade_type or "") not in ("", "ENTRY")] \
         or [t for t in trades if t.pnl != 0]   # fallback mientras trade_type esté vacío
```
y en `main.py:359` pasar `trade_type="EXIT" if trade.pnl != 0 else "ENTRY"` + `entry_price`,
igual que hace `_process_paper_fill` (`main.py:667`). Mejor aún: extraer la construcción del
`TradeRecord` a un único helper compartido por las dos rutas.

**Verificado como:** lectura del código (las tres localizaciones) + inspección de
`data/trade_database.db` (todas las filas de `sessions` tienen `source='paper'`; no hay ninguna
fila `live`, así que la ruta live nunca se ha ejercitado ni testeado contra la DB).

---

### [P1] persistence-02 — `/api/trades?limit=N` devuelve los N trades MÁS ANTIGUOS y los etiqueta como «most recent»

**Archivo:** `trade_database/repository.py:294-301`, consumido en `server/bridge.py:1426`

**Evidencia:**
```python
# trade_database/repository.py:294-301
        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM trades WHERE {where} ORDER BY timestamp ASC"
        if limit > 0:
            sql += f" LIMIT {limit}"
```
```python
# server/bridge.py:1466-1468
        # Return most recent first
        trades.reverse()
        return {"trades": trades[:limit]}
```

**Por qué:** `ORDER BY timestamp ASC … LIMIT 100` toma los **primeros** 100 por timestamp, no los
últimos. El `reverse()` posterior sólo invierte esos 100 antiguos. En cuanto la tabla supere el
límite, la lista de operaciones de la UI (desktop y web) queda **congelada en las primeras 100
operaciones de la historia** y nunca vuelve a mostrar una operación nueva: el usuario cree que el
bot no opera. Con la DB actual (0 trades) no se nota; con 2.284 trades, como los de la tanda 1,
se ve el 4 % más viejo.

**Fix:** en el repositorio, cuando hay `limit`, ordenar DESC y reinvertir:
```python
order = "DESC" if limit > 0 else "ASC"
sql = f"SELECT * FROM trades WHERE {where} ORDER BY timestamp {order}"
if limit > 0:
    sql += " LIMIT ?"; params.append(int(limit))
rows = conn.execute(sql, params).fetchall()
out = [self._row_to_trade(r) for r in rows]
return list(reversed(out)) if limit > 0 else out
```

**Verificado como:** ejecutado contra una DB temporal con 500 trades
(`PYTHONPATH=. py -3.12 …/limit_test.py`):
```
n = 100
primer trade_id devuelto : t0000  ts= 1700000000.0
ultimo trade_id devuelto : t0099  ts= 1700005940.0
el mas reciente en la DB : t0499  ts= 1700029940
=> /api/trades muestra los 100 MAS ANTIGUOS: True
```

---

### [P1] persistence-03 — `end_session()` sin sesión activa crea/pisa una fila fantasma con `session_id=''` (ya está en la DB de producción)

**Archivo:** `trade_database/adapter.py:102-131`

**Evidencia:**
```python
# trade_database/adapter.py:102-127
    def end_session(self, final_equity: float = 0.0, max_drawdown: float = 0.0) -> None:
        """Cierra la sesión actual y actualiza estadísticas."""
        self._flush_buffer()
        ...
        session = SessionRecord(
            session_id=self._session_id,     # ← "" si start_session() nunca corrió
            source=self.source,
            symbol=self._session_symbol,
            start_time=self._session_start,  # ← 0.0
            end_time=time.time(),
            ...
        )
        self.repo.insert_session(session)    # INSERT OR REPLACE sobre la PK ''
```
Fila real en `data/trade_database.db`:
```
{'session_id': '', 'source': 'paper', 'symbol': '', 'start_time': 0.0,
 'end_time': 1788137120.014248, 'initial_equity': 0.0, 'final_equity': 1000.0,
 'total_trades': 0, 'total_pnl': 0.0, 'max_drawdown': 0.0}
```

**Por qué:** `Engine.shutdown()` (`main.py:900-905`) llama a `end_session()` sin comprobar que
`start()` llegó a crear la sesión. Si `start()` aborta antes de la línea 178 (`market_data.initialize()`,
`seed_from_binance`, `set_leverage`… todos pueden lanzar), se escribe una sesión con PK `''`.
Como es `INSERT OR REPLACE`, **cada arranque fallido pisa el registro del anterior**: se pierde la
evidencia de arranques fallidos previos. Peor: si en ese estado se llegase a registrar un trade,
`on_trade` lo insertaría con `session_id=''`, y la FK `trades → sessions` no lo impide (ver
persistence-11).

**Fix:**
```python
def end_session(self, final_equity=0.0, max_drawdown=0.0) -> None:
    if not self._session_id:
        logger.warning("trade_db_end_session_without_start", source=self.source)
        return
    ...
```
y simétricamente rechazar `on_trade`/`on_backtest_trade` con `_session_id == ""`.

**Verificado como:** reproducido con `py -3.12`, dos adapters sin `start_session()`:
```
{'session_id': '', 'start_time': 0.0, 'end_time': 1788184343.6, 'final_equity': 777.0}
rows: 1        ← el segundo end_session() PISÓ al primero
```
La fila resultante es idéntica en forma a la que ya existe en `data/trade_database.db`.

---

### [P1] persistence-04 — Sesiones huérfanas: la fila `sessions` no se actualiza nunca durante la sesión y nadie detecta `end_time = 0`

**Archivo:** `trade_database/adapter.py:66-131`, `server/bridge.py:1075-1082`

**Evidencia:** estado real de `data/trade_database.db` (3 de 5 sesiones huérfanas):
```
{'session_id': '199bdcd0d0dc', 'start_time': 1788039136.28, 'end_time': 0.0,
 'initial_equity': 1000.0, 'final_equity': 0.0, 'total_trades': 0, 'total_pnl': 0.0}
{'session_id': '9e8df68e3d52', 'start_time': 1788057351.29, 'end_time': 0.0, ...}
{'session_id': 'f9cede9fd637', 'start_time': 1788130499.74, 'end_time': 0.0, ...}
```
```python
# server/bridge.py:1075-1082 — el watchdog mata el proceso sin pasar por shutdown()
def _hard_exit(code: int) -> None:
    """Leave the process so systemd (Restart=always) brings a fresh one up."""
    try:
        sys.stdout.flush(); sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)
```
Y **ninguna** consulta del proyecto lee `sessions.end_time`:
```
$ grep -rn "end_time" --include=*.py .   # (excluyendo build/, desktop/, archive/)
→ sólo exchange/*.py y data/binance_downloader.py (parámetros de klines). Cero usos de sessions.end_time.
```

**Por qué:** `start_session()` escribe la fila y `end_session()` la reescribe al final. Entre medias
no hay ningún *heartbeat*. Tras un `os._exit` del watchdog, un `kill -9`, un OOM o un corte de luz,
la sesión queda para siempre con `end_time=0`, `total_trades=0`, `total_pnl=0`,
`final_equity=0`, `strategies_used=''` — es decir, **la tabla `sessions` es inservible como
contabilidad**: dice que ninguna sesión operó nunca. Los trades sí sobreviven (se insertan uno a
uno con commit), pero el resumen por sesión no. Y en el siguiente arranque nadie avisa de que la
anterior murió sucia.

**Fix:**
1. Persistir el progreso: en `_track()`, cada N trades (o desde `_metrics_loop`, 1×/min) hacer
   `UPDATE sessions SET total_trades=?, total_pnl=?, final_equity=?, max_drawdown=? WHERE session_id=?`.
2. Al arrancar, `SELECT session_id, start_time FROM sessions WHERE end_time = 0 AND source = ?`
   y (a) loguear `orphan_session_detected`, (b) notificar por Telegram, (c) cerrarlas con
   `end_time = último timestamp de sus trades` para que las métricas históricas cuadren.

**Verificado como:** consulta directa a `data/trade_database.db` (3/5 sesiones con `end_time=0`) +
`grep` exhaustivo confirmando que ningún módulo consulta ese campo.

---

### [P1] persistence-05 — Sharpe y Sortino inflados por √(365/días_operados): `_aggregate_daily_returns` omite los días sin trades

**Archivo:** `analytics/performance.py:599-625` (usado en `:233-250`)

**Evidencia:**
```python
# analytics/performance.py:610-625
        from collections import defaultdict
        daily_pnl: dict = defaultdict(float)
        for t in trades:
            day = int(t.timestamp // 86400)
            daily_pnl[day] += t.pnl          # ← sólo días CON trades
        ...
        daily_returns = []
        for pnl in daily_pnl.values():        # ← len == nº de días operados, no días de calendario
            daily_returns.append(pnl / equity if equity > 0 else 0.0)
            equity += pnl
        return daily_returns
```
```python
# analytics/performance.py:238-241
            if daily_std > 0:
                report.sharpe_ratio = float(
                    daily_mean / daily_std * np.sqrt(self.ANNUALIZATION_FACTOR)   # 365
                )
```

**Por qué:** anualizar con √365 exige una serie de **365 observaciones/año**, incluyendo los días
planos (retorno 0). Al comprimir la serie a sólo los días con operaciones, la media diaria sube y
la varianza no incorpora los ceros → el Sharpe se multiplica por √(365/días_operados). Con las
estrategias reales del proyecto (MR intradía sólo entra en RANGING, Fibonacci sólo en
retrocesos) los días sin operación son la mayoría. Este es exactamente el número que se mira
para decidir si una estrategia merece capital: `/api/performance.sharpe_ratio` sale de aquí
(`bridge.py:890`).

Nota: el `Backtester` **no** tiene este sesgo — muestrea la equity curve por barras
(`backtester.py:189-198`, `daily_eq = eq[::bars_per_day]`), que sí incluye los días planos. Por eso
el Sharpe de un backtest y el Sharpe de `/api/performance` sobre los mismos trades **no coinciden**.

**Fix:** rellenar el calendario:
```python
days = sorted(daily_pnl)
full = range(days[0], days[-1] + 1)
daily_returns = []
for d in full:
    pnl = daily_pnl.get(d, 0.0)
    daily_returns.append(pnl / equity if equity > 0 else 0.0)
    equity += pnl
```
(y de paso ordenar por día: hoy se depende del orden de inserción del dict).

**Verificado como:** ejecutado (`sharpe_gap.py`) con una estrategia que opera 1 día de cada 7
durante un año (53 trades, `np.random.normal(0.5, 5.0)`, seed 7):
```
sharpe reportado por el modulo:           1.0
sharpe con serie diaria completa (0 los dias sin trade): 0.363
factor de inflacion: 2.76        (sqrt(365/52) = 2.65)
```

---

### [P1] persistence-06 — El funding NUNCA se contabiliza en paper ni en la trade DB (sí en el backtester) → paper sobrevalora los perps y rompe la paridad backtest↔paper

**Archivo:** `execution/paper_simulator.py` (0 apariciones de «funding»), `trade_database/models.py:40-48`,
frente a `backtesting/backtester.py:437-443`

**Evidencia:**
```python
# backtesting/backtester.py:437-443 — el BACKTEST sí cobra funding
            if i % funding_interval_bars == 0:
                for pos in list(positions.values()):
                    funding_cost = pos.size * price * funding_rate
                    if pos.side == Side.BUY:
                        equity -= funding_cost
                    else:
                        equity += funding_cost
```
```
$ grep -c funding execution/paper_simulator.py
0
```
`TradeRecord` (`trade_database/models.py:40-48`) tiene `fee`, `fee_asset`, `pnl`, `slippage_bps`,
`expected_cost_bps`… pero **ninguna columna de funding**, y el esquema SQL
(`repository.py:51-85`) tampoco. `Position.close()` (`paper_simulator.py:99-114`) sólo resta
`entry_fee + exit_fee`.

**Por qué:** el motor opera sobre datos de **futuros perpetuos** (`core/market_data.py:128-129`,
`https://fapi.binance.com/fapi/v1/klines`). En perps, mantener posición cuesta funding cada 8 h.
El backtester lo cobra a 0.0001/8h (`backtester.py:296`) — 0.03 %/día ≈ **11 %/año sobre el
nocional** — y el simulador paper lo ignora por completo. Consecuencias:
1. El PnL de paper (y por tanto todo `/api/performance` y la DB) está sesgado al alza en
   posiciones largas mantenidas horas.
2. Backtest y paper **no son comparables**: el mismo trade da un PnL distinto en cada motor, lo
   que invalida cualquier validación «el paper confirma el backtest».
3. `MarketSnapshot.funding_rate` se rellena con `0.0` fijo en el seed
   (`core/market_data.py:170`), así que los guards `funding_rate_warn` / `funding_rate_block`
   (`config/settings.py:117-118`) tampoco pueden dispararse nunca.

**Fix:** añadir a `PaperTradingSimulator` un tick de funding cada 8 h (usar
`market_data.get_funding_rate(symbol)`, ya existe en `core/market_data.py:480`), aplicarlo al
equity y persistirlo como fila `trade_type='FUNDING'` (o columna `funding` en `trades`) para que
`total_pnl` y la equity curve reconstruida lo incluyan. Y poblar `funding_rate` de verdad
(`GET /fapi/v1/premiumIndex`) en vez del `0.0` hardcodeado.

**Verificado como:** `grep -c funding execution/paper_simulator.py` → 0; lectura de
`repository.py:51-85` (sin columna) y de `backtester.py:437-443` (sí lo cobra); lectura de
`core/market_data.py:170` (`funding_rate=0.0` literal).

---

### [P1] persistence-07 — `net_pnl = total_pnl` es falso en LIVE: Binance devuelve `rp` bruto de comisión y `n` aparte

**Archivo:** `analytics/performance.py:186-187`, `logging_metrics/logger.py:232-236`,
origen del dato en `execution/order_engine.py:467-468, 503-512`

**Evidencia:**
```python
# execution/order_engine.py:467-468 — dos campos SEPARADOS del WS de Binance
            commission = float(data.get("n", data.get("commission", 0)))
            realized_pnl = float(data.get("rp", data.get("realizedProfit", 0)))
...
            trade = Trade(..., fee=commission, ..., pnl=realized_pnl, ...)
```
```python
# analytics/performance.py:185-187
        report.total_pnl = float(np.sum(pnl_arr))
        report.total_fees = sum(fees)
        report.net_pnl = report.total_pnl  # pnl already includes fees in backtester
```
```python
# logging_metrics/logger.py:234-236
            "total_pnl": round(self._cumulative_pnl, 2),
            "total_fees": round(self._cumulative_fees, 2),
            "net_pnl": round(self._cumulative_pnl, 2),
```

**Por qué:** el comentario («pnl already includes fees») es cierto **sólo en paper y backtest**:
`Position.close()` devuelve `gross - total_fee` (`paper_simulator.py:99-114`) y el backtester resta
fees. En live, `Trade.pnl` es el `rp` de Binance, que según la documentación oficial es el PnL
realizado **sin** comisiones (Binance calcula el ROI como
`realizedPNL + commissionFeeTotal + fundingFee + insuranceClearFee`, es decir suma las comisiones
aparte). Por tanto en live:
- `net_pnl` sobreestima el resultado en exactamente `total_fees`;
- lo mismo le llega a Telegram (`notify_shutdown` usa `m["net_pnl"]`, `telegram.py:181`);
- y `risk_manager.record_trade_result_safe(realized_pnl)` (`order_engine.py:520-522`) alimenta
  Kelly / Risk-of-Ruin / racha de pérdidas con PnL **bruto** → sizing optimista.

Con `taker_fee = 4 bps` ida y vuelta (`config/settings.py:113`), 8 bps por round-trip sobre un
edge medido en la tanda 1 de **−0.9 a +0.45 bps**, la diferencia entre bruto y neto no es un
matiz: es el signo del resultado.

**Fix:** en el pipeline live, normalizar a neto en el punto de entrada
(`order_engine.py:503`, `pnl=realized_pnl - commission`, dejando `fee` para el desglose) o, si se
prefiere no tocar el `Trade`, calcular `report.net_pnl = total_pnl - total_fees` cuando
`source == "live"` y documentar la convención en un solo sitio.

**Verificado como:** lectura del parseo real (`order_engine.py:467-468`) + documentación oficial de
Binance («How to Calculate Profit and Loss for Futures Contracts»), que presenta el PnL realizado
separado de las comisiones y define
`ROI = (realizedPNL + commissionFeeTotal + fundingFee + insuranceClearFee) / …`.

---

### [P1] persistence-08 — Telegram: el texto se envía con `parse_mode=HTML` sin escapar `<`, `>`, `&` → los mensajes que los contengan los rechaza la API y se pierden (afecta justo a `notify_error`)

**Archivo:** `notifications/telegram.py:399-405`, `:682-711`

**Evidencia:**
```python
# notifications/telegram.py:399-405
        text = (
            f"❌ <b>Error en el bot</b>\n\n"
            f"Componente: {task_desc}\n"
            f"Detalle: <code>{error[:300]}</code>\n\n"     # ← error SIN escapar
            f"El sistema intentara reiniciar este componente automaticamente."
        )
        self._enqueue(text)
```
```python
# notifications/telegram.py:687-708
        payload = {
            "chat_id": self._chat_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }
        ...
                else:
                    body = await resp.text()
                    logger.warning("telegram_send_failed", status=resp.status, body=body[:200])
                    return False          # ← mensaje descartado, sin reintento ni fallback
```

**Por qué:** la Bot API exige que «all `<`, `>` and `&` symbols that are not a part of a tag or an
HTML entity must be replaced with the corresponding HTML entities». Si no, `sendMessage` responde
400 *can't parse entities* y el mensaje **no se entrega**. El código lo registra como warning y lo
tira: no hay reintento en texto plano. Los textos que más probablemente contienen `<` son
precisamente los de error (`str(e)` de un `TypeError` típico: `'<' not supported between instances
of 'NoneType' and 'int'`), y los que contienen `&` son los símbolos/URLs. Resultado: **la alerta
crítica se pierde exactamente cuando el bot falla**. Lo mismo aplica a `notify_trade`
(símbolo/estrategia interpolados) y a `notify_risk_event` (claves de `details` arbitrarias).

**Fix:**
```python
import html
...
f"Detalle: <code>{html.escape(error[:300])}</code>\n\n"
```
y, como red de seguridad, en `_send`: si la respuesta es 400 con `can't parse entities`, reintentar
una vez **sin** `parse_mode` (texto plano) antes de descartar.

**Verificado como:** lectura del código + documentación oficial de la Telegram Bot API, sección
*HTML style*: «All `<`, `>` and `&` symbols that are not a part of a tag or an HTML entity must be
replaced with the corresponding HTML entities (`<` with `&lt;`, `>` with `&gt;` and `&` with
`&amp;`)». No he podido lanzar una petición real contra la API (no hay token en el entorno de
auditoría), así que la consecuencia «400 → mensaje perdido» está apoyada en la documentación y en
el código (`return False` sin reintento), no en una ejecución.

---

### [P2] persistence-09 — Un 429 de Telegram descarta el mensaje: se saca de la cola antes de enviarlo y no vuelve a entrar

**Archivo:** `notifications/telegram.py:529-542`, `:694-703`

**Evidencia:**
```python
# notifications/telegram.py:531-535
            while self._running:
            try:
                text = await asyncio.wait_for(self._queue.get(), timeout=1.0)  # ← ya fuera de la cola
                await self._acquire_token()
                await self._send(text)                                          # ← si falla, se pierde
```
```python
# notifications/telegram.py:697-703
                elif resp.status == 429:
                    data = await resp.json()
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.warning("telegram_rate_limited", retry_after=retry_after)
                    await asyncio.sleep(retry_after)
                    return False     # ← duerme el retry_after… y descarta el mensaje igualmente
```

**Por qué:** el token bucket local (20 msg/s) no protege del límite **por chat** de Telegram
(~1 msg/s sostenido, ~20/min por grupo). Cuando salta el 429, `_send` paga el `retry_after`
(bloqueando el sender loop, que es lo correcto) pero devuelve `False` y el mensaje ya no está en
la cola: se pierde. Cualquier ráfaga (un flatten con varios símbolos, un batch de señales, varias
alertas de riesgo) tira mensajes de forma silenciosa. Es el mismo agujero que persistence-08:
`_send` sólo distingue éxito/fracaso, nunca reintenta.

**Fix:** que `_sender_loop` reencole ante `False` con un contador de intentos
(`for attempt in range(3): if await self._send(text): break; await asyncio.sleep(backoff)`), o que
`_send` reintente internamente el caso 429 (que es el único con un `retry_after` explícito).

**Verificado como:** lectura del código; el fallo es estructural (el `get()` precede al `_send()`
y no hay `put_nowait` de vuelta en ninguna rama).

---

### [P2] persistence-10 — `is_exit = pnl != 0 or fee > 0` clasifica las ENTRADAS live como operaciones cerradas → doble conteo y win-rate hundido

**Archivo:** `logging_metrics/logger.py:160-185`, misma heurística en `server/serializers.py:85`

**Evidencia:**
```python
# logging_metrics/logger.py:162-174
        # Running totals — only count EXITS (round-trip completed)
        # Entry trades have pnl=0 and fee=0; they're not "completed trades"
        is_exit = trade.pnl != 0 or trade.fee > 0
        ...
        if is_exit:
            self._cumulative_trade_count += 1
            if trade.pnl > 0:   self._cumulative_win_count += 1; ...
            elif trade.pnl < 0: self._cumulative_loss_count += 1; ...
```
```python
# server/serializers.py:85
    is_exit = t.pnl != 0 or t.fee > 0 or t.order_id.startswith("paper_exit") or ...
```

**Por qué:** el comentario («Entry trades have pnl=0 and fee=0») describe **paper**
(`paper_simulator.py:564-573`: la entrada devuelve `fee=0.0, pnl=0.0`). En **live**, la entrada es
un fill real y Binance manda `n` (comisión) > 0 con `rp` = 0 → `is_exit` es `True`. Efectos en
modo live:
- `total_trades` se **duplica** (cuenta entrada + salida como dos round-trips);
- la entrada tiene `pnl == 0` → no es win ni loss, pero sí suma al denominador
  → `win_rate = wins / total_trades` queda dividido por ~2;
- `by_strategy[...]["trades"]` y `avg_pnl` heredan el mismo error;
- `serialize_trade` además invierte el `side` mostrado de cada entrada
  (`display_side = "SELL" if side == "BUY"`), así que la UI enseña la dirección contraria.

Estos números son los que van a Telegram en `notify_shutdown` (`main.py:911-916` →
`telegram.py:176-234`) y a `session_pnl`/`session_trades` de `/api/performance`.

**Fix:** dejar de inferir. El `Trade` ya sabe qué es (`signal_features["action"]`,
`order_id.startswith("paper_exit")`, o el `trade_type` que `main.py:667` calcula). Pasar
`trade_type` explícito a `MetricsCollector.add_trade()` y a `serialize_trade()` y borrar la
heurística de las dos.

**Verificado como:** lectura de `paper_simulator.py:564-573` (entrada paper: `fee=0.0, pnl=0.0`)
frente a `order_engine.py:467-468, 503-512` (entrada live: `fee=commission` del campo `n`,
`pnl=rp`). En la DB actual no hay ninguna fila `source='live'`, así que el camino nunca se ha
ejercitado.

---

### [P2] persistence-11 — `_cumulative_performance` materializa TODA la tabla paper en el event loop cada 5 s

**Archivo:** `server/bridge.py:853-905`

**Evidencia:**
```python
# server/bridge.py:862-876
        initial = float(engine.settings.trading.initial_capital)
        trades = engine.trade_repo.get_trades(source="paper")   # sin límite ni ventana temporal
        closes = [t for t in trades if t.trade_type and t.trade_type != "ENTRY"]
        ...
            rep = PerformanceAnalyzer().analyze(
                closes, initial_equity=initial, use_equity_after=False)
```
Se invoca desde `metrics_broadcast_loop` (cada 2 s, caché de 5 s → recálculo cada 5 s) y desde
`/api/performance`. Todo **síncrono dentro del event loop** del bridge.

**Por qué:** el coste crece linealmente y sin techo: cada recálculo abre una conexión SQLite,
lee todas las filas `source='paper'`, construye un `TradeRecord` por fila y corre el analizador
completo (equity curve, drawdown events, correlaciones no, pero sí VaR/CVaR y distribuciones).
Mientras dura, el loop del bridge no atiende WS, ni health, ni ticks. Hoy la DB tiene 0 trades, así
que no duele; a escala de la tanda 1 (2.284 trades) son ~40 ms cada 5 s (tolerable) y a 50k trades
es casi 1 s cada 5 s (20 % del loop parado).

**Fix:** (a) calcular incrementalmente — guardar el último `timestamp` procesado y agregar sólo lo
nuevo; o (b) como mínimo, mover la consulta+análisis a `asyncio.to_thread` (el patrón ya existe en
`bridge.py:1511`) y subir la TTL de caché a 15-30 s.

**Verificado como:** medido con `py -3.12` sobre una DB temporal (Windows/NTFS, misma máquina):
```
insert 50000: 0.334 s
get_trades 50000: 0.731 s
analyze: 0.133 s  sharpe=-3.56 trades=50000
db size MB 11.03
```
(`insert_trade` individual, que sí corre en el loop en cada fill: p50 3.5 ms, p95 6.1 ms,
max 14.8 ms — pequeño, no lo cuento como hallazgo aparte.)

---

### [P2] persistence-12 — El `equity` que ve el usuario mezcla dos convenciones: realizado NETO de fees + no realizado BRUTO

**Archivo:** `server/bridge.py:840-850`, `:935-944`, frente a `execution/paper_simulator.py:78-114`

**Evidencia:**
```python
# execution/paper_simulator.py:78-86 — unrealized SIN fees
    def update_pnl(self, current_price: float) -> float:
        if self.side == Side.BUY:
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size
```
```python
# execution/paper_simulator.py:105-114 — realizado CON round-trip fee
            gross = (exit_price - self.entry_price) * self.size
            ...
            total_fee = entry_fee + exit_fee
            return gross - total_fee, total_fee
```
```python
# server/bridge.py:938-941
        "equity": round(cum["initial_capital"] + cum["pnl"] + unrealized, 4),
        "pnl": round(cum["pnl"] + unrealized, 4),
        "realized_pnl": cum["pnl"],
        "unrealized_pnl": round(unrealized, 4),
```

**Por qué:** `cum["pnl"]` viene de la DB (neto de fees) y `unrealized` del simulador (bruto). El
`equity` mostrado sobreestima sistemáticamente en el round-trip fee de todas las posiciones
abiertas: con `maker 2 bps` / `taker 4 bps` (`config/settings.py:112-113`) son 4-8 bps del nocional
abierto. No es doble conteo (lo he comprobado: el realizado sale sólo de filas con
`trade_type != 'ENTRY'` y el no realizado sólo de posiciones vivas del simulador, conjuntos
disjuntos), pero sí es un sesgo optimista permanente y una incoherencia de unidades. Con un edge
medido en ±1 bps, 8 bps de sesgo en el número que más se mira es relevante.

**Fix:** que `Position.update_pnl` reste el fee estimado de cierre (o exponer
`unrealized_pnl_net = unrealized_pnl - (entry_fee + est_exit_fee)`) y que `_paper_unrealized_pnl`
use la versión neta, que es la que casa con el realizado.

**Verificado como:** lectura de las tres funciones; comprobado que los conjuntos (filas cerradas
en DB vs posiciones vivas en `paper_sim`) son disjuntos y por tanto no hay doble conteo.

---

### [P2] persistence-13 — La FK `trades.session_id → sessions.session_id` es decorativa: `PRAGMA foreign_keys` nunca se activa

**Archivo:** `trade_database/repository.py:84`, `:155-165`

**Evidencia:**
```sql
-- trade_database/repository.py:84
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
```
```python
# trade_database/repository.py:155-165 — los únicos PRAGMA que se ejecutan
    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
```
Comprobado sobre la DB real:
```
$ py -3.12 -c "...pragma foreign_keys..." → foreign_keys pragma = 0
```

**Por qué:** SQLite trae las FK **desactivadas por defecto**; hay que pedir
`PRAGMA foreign_keys=ON` en **cada** conexión. Como no se hace, nada impide insertar trades con
un `session_id` inexistente o vacío — exactamente el escenario de persistence-03. No es crítico
(el `delete_session` borra los trades a mano, `repository.py:492-503`), pero la garantía que el
esquema aparenta dar no existe.

**Fix:** añadir `conn.execute("PRAGMA foreign_keys=ON")` en `_connect()` y arreglar antes las
filas huérfanas (`UPDATE trades SET session_id='orphan' WHERE session_id NOT IN (SELECT session_id FROM sessions)`).

**Verificado como:** `PRAGMA foreign_keys` = 0 ejecutado contra `data/trade_database.db`.

---

### [P2] persistence-14 — Dos «equity» distintos en la misma pantalla: la UI muestra el acumulado multi-sesión, el risk manager decide con el de la sesión

**Archivo:** `server/bridge.py:918-934` (rama sin DB) vs `:935-944` (rama con DB)

**Evidencia:**
```python
# server/bridge.py:919-922 — SIN trade DB: equity = el del risk manager (sesión, arranca en 1000)
        return {
            "initial_capital": float(engine.settings.trading.initial_capital),
            "equity": float(engine.risk_manager.current_equity),
            "pnl": session_pnl, "realized_pnl": session_pnl, "unrealized_pnl": 0.0,
```
```python
# server/bridge.py:938 — CON trade DB: equity = capital inicial + PnL de TODAS las sesiones
        "equity": round(cum["initial_capital"] + cum["pnl"] + unrealized, 4),
```

**Por qué:** las dos ramas del mismo endpoint devuelven cosas distintas bajo el mismo nombre. La
rama principal reconstruye una curva «como si nunca se hubiera reiniciado» (encadenando `pnl`,
`use_equity_after=False`), que **no es** el equity del simulador: cada sesión paper vuelve a
arrancar en `initial_capital = 1000` (`config/settings.py:80`) y así se refleja en `equity_after`.
Consecuencias:
- si la historia acumulada es −300, la UI enseña 700 mientras el simulador y el risk manager
  trabajan con 1000 → el drawdown-halt, el sizing y el `max_drawdown_pct` se calculan sobre una
  base distinta de la que ve el usuario;
- `state.equity`, que alimenta `/api/bot/status`, hereda el valor acumulado
  (`bridge.py:972-973`).

La elección de encadenar es defendible para medir edge, pero entonces debe llamarse
`cumulative_equity` y publicarse **junto** al equity real de la sesión, no en su lugar.

**Fix:** exponer ambos campos con nombres distintos (`equity` = `risk_manager.current_equity`,
`cumulative_equity` = la curva encadenada) y que el desktop etiquete cuál está mirando.

**Verificado como:** lectura de las dos ramas + confirmación en la DB de que `initial_equity`
es 1000 en todas las sesiones (`data/trade_database.db`), es decir que el equity se reinicia por
sesión tal como dice el comentario de `bridge.py:829-835`.

---

### [P2] persistence-15 — El cambio de 252 a 365 no se propagó: `dashboard/state.py` sigue en √252 y el PDF de documentación también

**Archivo:** `dashboard/state.py:314`, `scripts/generate_docs_pdf.py:946`

**Evidencia:**
```python
# dashboard/state.py:314
        sharpe = (rolling_mean / rolling_std.replace(0, np.nan)) * (252 ** 0.5)
```
```python
# scripts/generate_docs_pdf.py:946
            ["Sharpe Ratio", "mean(daily_ret) / std(daily_ret) * sqrt(252)", "Riesgo-ajustado"],
```
Barrido completo del repo (excluyendo `build/`, `desktop/`, `archive/`):
```
analytics/performance.py:144   ANNUALIZATION_FACTOR = 365     ✓
analytics/performance.py:264   annual_return = total_return * (365.0 / span_days)   ✓
backtesting/backtester.py:207  365.0 / span_days              ✓
backtesting/backtester.py:198  * (365 ** 0.5)                 ✓
logging_metrics/logger.py:219  * (365 ** 0.5)                 ✓
core/quant_models.py:57        annualization: float = 365.0   ✓
dashboard/state.py:314         * (252 ** 0.5)                 ✗
scripts/generate_docs_pdf.py:946  "sqrt(252)"                 ✗ (documentación al usuario)
```

**Por qué:** el Sharpe del dashboard Streamlit queda un 17 % por debajo del de `/api/performance`
sobre los mismos datos (√(365/252) = 1.20), y el PDF que lee el usuario documenta una fórmula que
el código ya no usa. Además `analytics/performance.py:606` conserva el docstring viejo
(«permite annualizar correctamente con sqrt(252)») justo en la función que anualiza con 365.

**Fix:** importar la constante en vez de repetir el literal:
`from analytics.performance import PerformanceAnalyzer as _PA; ANN = _PA.ANNUALIZATION_FACTOR`,
y actualizar docstring y PDF.

**Verificado como:** `grep -rn "252\|365\|ANNUALIZATION"` sobre todo el repo (salida arriba).
El test `tests/test_cumulative_performance.py:29-32` sólo comprueba `analytics`, por eso los otros
dos sitios pasaron desapercibidos.

---

### [P2] persistence-16 — La ruta de la DB es relativa al CWD: ejecutar cualquier script desde otro directorio crea una base vacía paralela (ya ha pasado en este repo)

**Archivo:** `main.py:114`, `:959`, `:1025`, `:1167`, `:1248`

**Evidencia:**
```python
# main.py:114
        self.trade_repo = TradeRepository("data/trade_database.db")
```
```python
# trade_database/repository.py:109-112
    def __init__(self, db_path: str = "data/trade_database.db") -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)   # ← crea el árbol donde sea
        self._init_db()
```
Prueba de que ocurre: en este propio repo hay una base vacía creada por un proceso que corrió
desde otro directorio —
```
$ ls -la tasks/audit/r2/
-rw-r--r-- 1 edgar 197609 49152 Aug 31 02:45 data/trade_database.db
```

**Por qué:** `os.makedirs(...)` + `sqlite3.connect` crean silenciosamente una DB nueva en vez de
fallar. En el CT no da problemas porque el service fija `WorkingDirectory=/opt/botstrike/app`
(`deploy/botstrike-bridge.service:16`), pero cualquier `py -3.12 scripts/xxx.py` lanzado desde otra
carpeta escribe/lee una base distinta y el usuario cree que ha perdido los trades. Y el árbol
`data/` se replica por todo el disco.

**Fix:** resolver la ruta contra la raíz del proyecto, no contra el CWD:
```python
_ROOT = os.path.dirname(os.path.abspath(__file__))          # en trade_database/
DEFAULT_DB = os.path.join(os.path.dirname(_ROOT), "data", "trade_database.db")
```
o exponerla por env (`BOTSTRIKE_DB_PATH`) con un único punto de verdad.

**Verificado como:** existencia del fichero `tasks/audit/r2/data/trade_database.db` (49.152 bytes,
esquema completo, 0 filas) creado por un proceso de auditoría anterior con otro CWD.

---

### [P2] persistence-17 — El token del bot de Telegram acaba en journald: va en la URL y `str(aiohttp.ContentTypeError)` la incluye

**Archivo:** `notifications/telegram.py:47`, `:686`, `:698-711`; el filtro de redacción
(`server/bridge.py:78-101`) no lo cubre

**Evidencia:**
```python
# notifications/telegram.py:47, 686 — el token va en el PATH de la URL
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
        url = TELEGRAM_API.format(token=self._token)
```
```python
# notifications/telegram.py:697-711
                elif resp.status == 429:
                    data = await resp.json()        # ← lanza ContentTypeError si el cuerpo no es JSON
                    ...
        except Exception as e:
            logger.error("telegram_send_error", error=str(e))   # ← str(e) lleva la URL completa
```
```python
# server/bridge.py:78 — la redacción existente sólo cubre `token=` en la QUERY, no el path del bot
_TOKEN_IN_URL_RE = re.compile(r"(token=)[^&\s\"']+", re.IGNORECASE)
```

**Por qué:** basta con que un proxy, Cloudflare o el propio Telegram devuelvan un 429 (o cualquier
error) con cuerpo HTML en vez de JSON para que `resp.json()` lance `ContentTypeError`, cuya
representación incluye `url='https://api.telegram.org/bot<TOKEN>/sendMessage'`. structlog escribe a
stderr (`logging_metrics/logger.py:43`), que en el CT es journald → el token del bot queda en disco
y en cualquier `journalctl` que se comparta al depurar. Con ese token se puede leer y escribir en
el chat del bot.

**Fix:** no meter el token en el mensaje de error. Sanear antes de loguear:
```python
except Exception as e:
    logger.error("telegram_send_error", error=str(e).replace(self._token, "***"))
```
y extender `_RedactTokenFilter` con `r"/bot\d+:[A-Za-z0-9_-]+"` → `/bot***`.

**Verificado como:** reproducido (`tg_leak2.py`) levantando un servidor local que responde 429 con
`text/html` en la ruta `/bot<TOKEN>/sendMessage`, ejecutando el mismo bloque `try` de `_send`:
```
type      = ContentTypeError
token en str(e)? -> True
log line  = telegram_send_error error=429, message='Attempt to decode JSON with unexpected mimetype:
            text/html; charset=utf-8', url='http://127.0.0.1:8899/bot123456789:AAFAKE_BOT_TOKEN_SECRET_xyz/sendMessage'
```
(aiohttp 3.11.11, el mismo del `requirements.lock`.)

---

### [P2] persistence-18 — Si falla la escritura de `metrics.jsonl`, el buffer nunca se vacía: crece sin límite y reescribe duplicados

**Archivo:** `logging_metrics/logger.py:112-130`

**Evidencia:**
```python
# logging_metrics/logger.py:112-130
    def _flush_metrics(self) -> None:
        if not self._metric_buffer:
            return
        try:
            if os.path.exists(self.metrics_file):
                size_mb = os.path.getsize(self.metrics_file) / (1024 * 1024)
                if size_mb > 50:
                    rotated = self.metrics_file + ".old"
                    if os.path.exists(rotated):
                        os.remove(rotated)
                    os.rename(self.metrics_file, rotated)

            with open(self.metrics_file, "a") as f:
                f.write("\n".join(self._metric_buffer) + "\n")
            self._metric_buffer.clear()          # ← sólo si TODO fue bien
        except Exception as e:
            logger.error("metric_write_error", error=str(e))
```

**Por qué:** con el disco lleno, permisos rotos (el service corre con
`ReadWritePaths=/opt/botstrike/app/data /opt/botstrike/app/logs`, así que cualquier cambio de ruta
lo rompe) o un `os.rename` fallido, la excepción salta y `clear()` no se ejecuta. A partir de ahí:
1. el buffer crece 10 registros cada vez y **nunca** se libera → fuga de memoria en un proceso que
   debe correr semanas;
2. cada flush hace `"\n".join(buffer)` completo → coste O(n²) en CPU y en bytes intentados;
3. si la escritura falló **a medias** (write parcial antes del ENOSPC), el siguiente flush reescribe
   todo el buffer → líneas duplicadas en el JSONL, que es la fuente de las métricas.

**Fix:** limpiar el buffer pase lo que pase, con tope y contador de descartes:
```python
except Exception as e:
    logger.error("metric_write_error", error=str(e), dropped=len(self._metric_buffer))
finally:
    if len(self._metric_buffer) > 10_000:
        self._metric_buffer.clear()
```
(o mejor: escribir línea a línea con `f.write(line + "\n")` y vaciar lo ya escrito).

**Verificado como:** lectura del flujo de control — `clear()` está dentro del `try`, después del
`write`, y no hay `finally`.

---

### [P3] persistence-19 — Rotación duplicada de `metrics.jsonl`: el comentario de logrotate es falso y el `.old` interno queda fuera del patrón

**Archivo:** `deploy/logrotate-botstrike:2-3`, `logging_metrics/logger.py:117-124`, `config/settings.py:228`

**Evidencia:**
```
# deploy/logrotate-botstrike
# copytruncate: the engine keeps the file handle open; truncating in place is safe for append-only JSONL.
/opt/botstrike/app/logs/*.jsonl /opt/botstrike/app/logs/*.log { ... copytruncate ... }
```
```python
# logging_metrics/logger.py:126 — NO mantiene el handle abierto: abre y cierra en cada flush
            with open(self.metrics_file, "a") as f:
```

**Por qué:** tres cosas menores, ninguna peligrosa pero todas confusas:
1. La justificación de `copytruncate` es incorrecta (no hay handle persistente). `copytruncate`
   sigue siendo **seguro** aquí, y de hecho lo es *más* que si lo hubiera, porque cada `open(...,"a")`
   reposiciona al final. Conclusión: la config está bien, el comentario no.
2. La rotación interna a 50 MB produce `metrics.jsonl.old`, que **no** casa con `*.jsonl` ni con
   `*.log` → logrotate nunca lo comprime ni lo caduca. Añadir `*.jsonl.old` al patrón.
3. `settings.log_file = "logs/botstrike.log"` (`config/settings.py:228`) se pasa a
   `TradingLogger.__init__` pero **nunca se escribe** (structlog va a stderr, `logger.py:33-44`).
   El `*.log` del patrón de logrotate no cubre nada.

**Fix:** corregir el comentario, añadir `*.jsonl.old`, y o bien usar `log_file` o bien borrarlo de
`Settings`.

**Verificado como:** lectura de `logger.py:126` (apertura por flush) y del patrón de logrotate;
`grep` confirma que `self.log_file` sólo se usa para `os.makedirs` (`logger.py:27`).

---

### [P3] persistence-20 — `stop()` drena la cola de Telegram saltándose el rate limiter

**Archivo:** `notifications/telegram.py:125-129`

**Evidencia:**
```python
# notifications/telegram.py:125-129
    async def _drain_queue(self) -> None:
        """Envia todos los mensajes pendientes en la cola."""
        while not self._queue.empty():
            msg = self._queue.get_nowait()
            await self._send(msg)          # ← sin await self._acquire_token()
```

**Por qué:** en el apagado se puede intentar enviar hasta 500 mensajes (`MAX_QUEUE_SIZE`) tan rápido
como aguante la red, ignorando el token bucket. Telegram responde 429 y, por persistence-09, esos
mensajes se pierden — justo el resumen final de sesión que más interesa. El `wait_for(..., 5.0)` de
`stop()` además corta el drenaje a los 5 s.

**Fix:** `await self._acquire_token()` también en `_drain_queue`, y priorizar: enviar primero el
resumen de `notify_shutdown` y descartar el resto si no da tiempo.

**Verificado como:** lectura del código; `_acquire_token` sólo se llama en `_sender_loop:534`.

---

### [P3] persistence-21 — Detalles de las fórmulas de `PerformanceAnalyzer`: drawdown desalineado, VaR sin interpolación, `profit_factor` centinela 9999.99

**Archivo:** `analytics/performance.py:627-658`, `:269-274`, `:202-209`

**Evidencia:**
```python
# analytics/performance.py:589-597 — la curva tiene len(trades)+1 puntos
        curve = [initial_equity]
        for t in trades: ... curve.append(equity)
```
```python
# analytics/performance.py:653-656 — pero se indexa contra trades[] con un off-by-one manual
                if trades and i > 0 and dd_start_idx < len(trades) and i - 1 < len(trades):
                    t_start = trades[min(dd_start_idx, len(trades) - 1)].timestamp
                    t_end = trades[min(i - 1, len(trades) - 1)].timestamp
```
```python
# analytics/performance.py:270-274 — VaR/CVaR por índice truncado
        if len(pnl_arr) >= 20:
            var_idx = int(len(sorted_pnls) * 0.05)
            report.var_95 = float(sorted_pnls[var_idx])
            report.cvar_95 = float(np.mean(sorted_pnls[:var_idx + 1]))
```
```python
# analytics/performance.py:202-209
        report.profit_factor = (... if report.gross_loss != 0 else 9999.99)
        report.payoff_ratio  = (... if report.avg_loss  != 0 else 9999.99)
```

**Por qué:**
- `max_drawdown_duration` mezcla índices de la curva (base `initial_equity` en la posición 0) con
  índices de trades; el `min(...)` evita el `IndexError` pero devuelve duraciones desplazadas un
  trade. Es un número que se muestra, no que se usa para decidir → P3.
- `var_95` con `int(n*0.05)`: con exactamente 20 trades `var_idx = 1`, es decir el **segundo** peor,
  y `cvar_95` la media de los dos peores. Sin interpolación el VaR es optimista en muestras
  pequeñas. `np.percentile(pnl_arr, 5)` resuelve.
- `profit_factor = 9999.99` cuando no hay pérdidas: si algún consumidor promedia profit factors
  entre estrategias/regímenes (`analyze_by_regime` los devuelve por grupo), un solo grupo sin
  pérdidas domina la media. Mejor `float("inf")` o `None`.

**Fix:** usar `np.percentile` para VaR, alinear el drawdown con `curve[1:]` ↔ `trades`, y devolver
`None` en vez de la centinela.

**Verificado como:** lectura del código y de las longitudes (`_build_equity_curve` arranca con un
punto extra); aritmética de índices comprobada a mano con n = 20.

---

### [P3] persistence-22 — SQL por f-string en `LIMIT`, y la DB nunca se compacta (`VACUUM` sin llamantes, `auto_vacuum = 0`)

**Archivo:** `trade_database/repository.py:296-297`, `:505-508`

**Evidencia:**
```python
# trade_database/repository.py:296-297
        if limit > 0:
            sql += f" LIMIT {limit}"      # interpolación directa, no parámetro
```
```python
# trade_database/repository.py:505-508
    def vacuum(self) -> None:
        """Compacta la base de datos."""
        with self._connect() as conn:
            conn.execute("VACUUM")
```
```
$ grep -rn "\.vacuum()" --include=*.py .   → 0 llamantes
$ py -3.12 -c "...pragma auto_vacuum..."   → auto_vacuum = 0
```

**Por qué:** el `LIMIT` interpolado **hoy no es explotable** porque el único llamante externo es
`/api/trades` con `limit: int = 100`, que FastAPI valida y coerciona antes de llegar aquí; lo
reporto como deuda, no como vulnerabilidad. Y con `auto_vacuum = 0` y `vacuum()` sin llamantes, el
fichero nunca devuelve espacio tras un `delete_session` — irrelevante ahora (48 KB) pero es la
única palanca de mantenimiento del esquema y está muerta.

**Fix:** `sql += " LIMIT ?"; params.append(int(limit))`, y llamar a `vacuum()` desde un
mantenimiento periódico (o `PRAGMA auto_vacuum=INCREMENTAL` en la creación).

**Verificado como:** `grep` sin llamantes de `vacuum()`; `PRAGMA auto_vacuum` = 0 ejecutado contra
`data/trade_database.db`; `limit: int = 100` en `bridge.py:1422` (validado por FastAPI).

---

### [P3] persistence-23 — Las métricas se escriben cada 10 registros y no hay flush periódico: cada `os._exit` pierde hasta 9

**Archivo:** `logging_metrics/logger.py:30-31`, `:106-110`; `server/bridge.py:1075-1082`

**Evidencia:**
```python
# logging_metrics/logger.py:30-31
        self._metric_buffer: List[str] = []
        self._metric_flush_size = 10  # flush cada 10 métricas
```
```python
# logging_metrics/logger.py:106-110
    def _append_metric(self, data: Dict) -> None:
        self._metric_buffer.append(json.dumps(data, default=str))
        if len(self._metric_buffer) >= self._metric_flush_size:
            self._flush_metrics()
```
El único flush explícito está en `Engine.shutdown()` (`main.py:915`), que el watchdog **no**
ejecuta: `_hard_exit()` llama a `os._exit(code)` tras vaciar sólo stdout/stderr.

**Por qué:** los últimos segundos antes de un reinicio del watchdog son justo los que hacen falta
para el post-mortem (señales, eventos de riesgo, el trade que disparó el fallo) y son los que se
pierden. Con las estrategias congeladas el volumen es bajo, así que 9 registros pueden ser horas de
contexto.

**Fix:** flush también por tiempo (`if now - self._last_flush > 5: self._flush_metrics()`) y llamar
a `trading_logger._flush_metrics()` desde `_hard_exit()` antes del `os._exit`.

**Verificado como:** lectura de `_append_metric` (sin criterio temporal) y de `_hard_exit`
(`bridge.py:1075-1082`, sin flush del logger de métricas).

---

## Tabla resumen

| id | Sev | Título | Archivo:línea | Estado |
|----|-----|--------|---------------|--------|
| persistence-01 | **P0** | En live la contabilidad es la de paper (`source="paper"` + `trade_type` vacío) | `server/bridge.py:863`, `:1427`, `main.py:359` | nuevo |
| persistence-02 | P1 | `/api/trades` devuelve los N trades **más antiguos** | `trade_database/repository.py:295`, `server/bridge.py:1426` | nuevo |
| persistence-03 | P1 | `end_session()` sin sesión escribe/pisa la fila `session_id=''` | `trade_database/adapter.py:114` | nuevo (fila ya en la DB) |
| persistence-04 | P1 | Sesiones huérfanas: sin heartbeat y sin detección de `end_time=0` | `trade_database/adapter.py:102`, `server/bridge.py:1075` | nuevo (3/5 en la DB) |
| persistence-05 | P1 | Sharpe/Sortino inflados ×√(365/días_operados) | `analytics/performance.py:610` | nuevo (medido ×2.76) |
| persistence-06 | P1 | Funding nunca contabilizado en paper ni en la DB | `execution/paper_simulator.py`, `trade_database/repository.py:51` | nuevo |
| persistence-07 | P1 | `net_pnl = total_pnl` es falso en live (`rp` es bruto de comisión) | `analytics/performance.py:187`, `logging_metrics/logger.py:236` | nuevo |
| persistence-08 | P1 | Telegram HTML sin escapar → mensajes de error descartados | `notifications/telegram.py:402` | nuevo |
| persistence-09 | P2 | 429 de Telegram descarta el mensaje (sin reintento) | `notifications/telegram.py:533`, `:703` | nuevo |
| persistence-10 | P2 | `is_exit = pnl!=0 or fee>0` cuenta las entradas live como cierres | `logging_metrics/logger.py:164`, `server/serializers.py:85` | nuevo |
| persistence-11 | P2 | `_cumulative_performance` lee toda la tabla en el event loop cada 5 s | `server/bridge.py:863` | nuevo |
| persistence-12 | P2 | Realizado NETO + no realizado BRUTO en el mismo `equity` | `server/bridge.py:938`, `execution/paper_simulator.py:80` | nuevo |
| persistence-13 | P2 | `PRAGMA foreign_keys` nunca activado: la FK es decorativa | `trade_database/repository.py:84`, `:158` | nuevo |
| persistence-14 | P2 | Dos definiciones de `equity` en el mismo endpoint | `server/bridge.py:921` vs `:938` | nuevo |
| persistence-15 | P2 | √252 residual en `dashboard/state.py` y en el PDF de docs | `dashboard/state.py:314`, `scripts/generate_docs_pdf.py:946` | crítica al fix de 2026-08-31 |
| persistence-16 | P2 | Ruta de DB relativa al CWD → bases vacías paralelas | `main.py:114`, `trade_database/repository.py:111` | nuevo (evidencia en el repo) |
| persistence-17 | P2 | El token de Telegram acaba en journald vía `str(ContentTypeError)` | `notifications/telegram.py:686`, `:710` | nuevo (reproducido) |
| persistence-18 | P2 | Buffer de `metrics.jsonl` nunca se vacía si falla la escritura | `logging_metrics/logger.py:128` | nuevo |
| persistence-19 | P3 | Rotación duplicada: comentario falso + `.old` fuera del patrón + `log_file` muerto | `deploy/logrotate-botstrike:2`, `logging_metrics/logger.py:126` | crítica al fix 2a67ec2 |
| persistence-20 | P3 | `_drain_queue` salta el rate limiter en el apagado | `notifications/telegram.py:127` | nuevo |
| persistence-21 | P3 | Drawdown desalineado, VaR sin interpolación, PF centinela 9999.99 | `analytics/performance.py:653`, `:272`, `:204` | nuevo |
| persistence-22 | P3 | `LIMIT` por f-string (no explotable hoy) y `VACUUM` sin llamantes | `trade_database/repository.py:297`, `:505` | nuevo |
| persistence-23 | P3 | Flush de métricas sólo cada 10 registros, sin flush en `_hard_exit` | `logging_metrics/logger.py:31`, `server/bridge.py:1081` | nuevo |

**Reparto:** 1 P0 · 7 P1 · 10 P2 · 5 P3 = **23 hallazgos**.

---

## Lo que está BIEN (verificado, no lo toquéis)

- **Integridad de la DB:** `PRAGMA integrity_check` = `ok`, `journal_mode` = `wal` persistido en el
  fichero. WAL + `synchronous=NORMAL` es la elección correcta para este caso.
- **Fees en paper: correctos y en ambos lados.** `Position.close()` cobra
  `entry_fee (tasa guardada al entrar) + exit_fee (tasa de salida)`, y guarda `entry_fee_rate`
  según fuera MARKET (taker) o LIMIT (maker) — modelo honesto, mejor que el habitual «2× taker».
  La entrada se persiste con `fee=0, pnl=0` y el round-trip completo va en la salida: no hay doble
  cobro. Lo verifiqué línea a línea (`paper_simulator.py:99-114, 536-573`).
- **`use_equity_after=False` en `_cumulative_performance` es la decisión correcta**, y el comentario
  de `bridge.py:829-835` documenta bien por qué (cada sesión reinicia `equity_after` en
  `initial_capital`). Confirmado en la DB: las 5 sesiones tienen `initial_equity = 1000`.
- **No hay doble conteo entre realizado y no realizado**: el realizado sale sólo de filas con
  `trade_type != 'ENTRY'` y el no realizado sólo de posiciones vivas del simulador. Conjuntos
  disjuntos. (El problema es de *unidades* — persistence-12 — no de doble conteo.)
- **`copytruncate` es seguro** con este logger: abre y cierra el fichero en cada flush, así que no
  hay descriptor con offset viejo. La razón del comentario es falsa, la config es correcta.
- **`data_lifecycle/` está archivado** (`__init__.py` de una línea) y **no hay ni una ruta Windows
  hardcodeada** en el Python activo (`grep` de `C:\`, `D:\`, `%USERPROFILE%`, `Program Files`,
  `schtasks` → 0 en `*.py` fuera de `automation/*.ps1|bat`, que es el colector local y no se
  despliega). El foco «rutas Windows / tareas que no existen en Linux» no tiene hallazgo.
- **Contención SQLite entre el backtest y el motor: no existe.** `/api/backtest/run` corre en
  `asyncio.to_thread` (`bridge.py:1531`) pero `_run_backtest_sync` **no escribe en la trade DB**
  (sólo lee parquet y devuelve el resumen). El único escritor concurrente posible es un
  `main.py --backtest` lanzado a mano en otro proceso, y con WAL + `timeout=10` eso no bloquea a
  los lectores.
- **`insert_trade` en el loop cuesta p50 3.5 ms / p95 6.1 ms** (medido en NTFS, peor caso). No es
  un problema y no lo he inflado a hallazgo.
- **El backtester NO tiene el sesgo de Sharpe** de persistence-05: muestrea la equity curve por
  barras e incluye los días planos (`backtester.py:189-198`).

---

## Veredicto (10 líneas)

1. La capa de persistencia **funciona en paper y sólo en paper**: todo lo que la UI llama
   «rendimiento» está cableado a `source="paper"`, y la ruta live ni siquiera rellena `trade_type`.
2. Eso convierte el único P0: el día que se active live, `/api/performance` y `/api/trades`
   enseñarán el histórico de paper y ocultarán cada operación real, sin un solo aviso.
3. La contabilidad de fees en paper es **correcta y de las mejores piezas del repo** (round-trip
   con tasa de entrada guardada); en live está rota por convención (`rp` de Binance es bruto).
4. **El funding no existe fuera del backtester.** Se opera sobre datos de perps y se ignora un
   coste de ~11 %/año sobre el nocional: backtest y paper no son comparables, y el paper miente al alza.
5. La tabla `sessions` es hoy **inservible**: se escribe al empezar y al terminar, nada la actualiza,
   nadie detecta `end_time=0`, y ya hay 3 sesiones huérfanas y 1 fila fantasma con `session_id=''`.
6. El Sharpe publicado está **inflado por √(365/días_operados)** — medido ×2.76 con una estrategia
   que opera 1 día de cada 7. Es el número sobre el que se decide asignar capital.
7. `/api/trades` muestra los trades **más antiguos** etiquetados como los más recientes: en cuanto
   haya más de 100, la lista de operaciones queda congelada para siempre.
8. Telegram es frágil donde más duele: los mensajes con `<`, `>` o `&` los rechaza la API y se
   descartan (afecta a `notify_error`), un 429 pierde el mensaje, y el token del bot se filtra a journald.
9. SQLite en sí está sano —integridad ok, WAL, índices razonables, sin contención real entre hilos—
   y el fix de `use_equity_after=False` del 2026-08-31 es conceptualmente el correcto.
10. Prioridad de arreglo: **01 → 04 → 02 → 06 → 05 → 07/08**. Con las estrategias congeladas nada de
    esto sangra dinero hoy; el 01, el 04 y el 06 tienen que estar cerrados **antes** de descongelar nada.

