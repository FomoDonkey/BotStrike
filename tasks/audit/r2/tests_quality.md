# Auditoría R2 — AREA: tests_quality

**Fecha:** 2026-08-31
**Alcance:** `tests/*.py` (14 ficheros, 138 tests), `test_functional.py` (raíz), `tests/conftest.py`.
**Objetivo:** (a) verificar que los tests que respaldan los fixes realmente fallarían si el bug volviera; (b) medir cobertura real de los caminos que pierden dinero; (c) diagnosticar por qué la suite no detectó los dos fixes "a un solo lado"; (d) listar los tests que faltan.

**Método:** lectura de los 15 ficheros de test; medición de cobertura real con `pytest-cov` (JSON, recorte por
rangos de línea de cada `def`); **mutation testing manual**: 25 reintroducciones de bugs sobre una COPIA del
repo (`git archive HEAD` → scratchpad), con `pytest tests/ -q` tras cada una. El repositorio no se modificó
(sólo este informe). Baseline y estado final verificados: `138 passed`.

> Estado: COMPLETO.

## Hallazgos

### [P0] tests_quality-01 — Los tests que "blindan" los fixes de paridad son `grep` sobre el fuente: el bug de `exit_fibonacci` puede volver con 138/138 en verde

**Archivo:** `tests/test_audit_r2_batch1_fixes.py:61`

**Evidencia (test real):**
```python
def test_backtester_uses_the_shared_exit_helper():
    """Guards against the hardcoded action tuples coming back."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "backtesting" / "backtester.py").read_text(encoding="utf-8")
    assert 'OrderExecutionEngine.is_exit_signal' in src
    assert '"exit_mean_reversion", "trailing_stop_hit"' not in src
```

**Por qué:** el fuente tiene TRES llamadas a `is_exit_signal` (`backtesting/backtester.py:522, 1107, 1119`). El
test sólo comprueba que la cadena `OrderExecutionEngine.is_exit_signal` aparezca **una vez** en el fichero y que
un literal concreto NO aparezca. Revertir **uno** de los tres sitios a la lista hardcodeada (con un salto de
línea dentro de la tupla, que es como lo escribiría cualquiera con un formateador) deja el fichero con la cadena
buena en los otros dos sitios y con el literal roto en dos trozos: la suite pasa entera.

**Verificado como** (copia en scratchpad, repo intacto):
```
# backtesting/backtester.py:1119 revertido a:
#   is_exit = signal.metadata.get("action") in ("exit_mean_reversion",
#                                               "trailing_stop_hit", "mm_unwind")
py -3.12 -m pytest tests/ -q  ->  138 passed
```
(Con la tupla en UNA sola línea sí falla 1 test — o sea, el guard detecta el formato, no el comportamiento.)
Ese es exactamente el bug `backtest_parity-01` que la ronda 2 encontró: arreglado sólo en live. El "test de
regresión" que se escribió para cerrarlo no puede detectarlo.

**Fix:** sustituir el grep por un test de comportamiento: correr el backtester sobre un DataFrame sintético con
una señal `exit_fibonacci` y aserción sobre el resultado (`la posición se cierra`, `total_trades == 1`), y un
test de paridad `live vs backtest` parametrizado sobre TODAS las acciones de salida que producen las estrategias
vivas. Ver `tests_quality-20` (lista de tests que faltan).

---

### [P0] tests_quality-02 — El P0 de la posición desnuda en producción (`bridge.stop_engine`) se puede revertir comentando el código y la suite sigue en 138/138

**Archivo:** `tests/test_audit_r2_batch2_fixes.py:26`

**Evidencia (los DOS únicos tests que cubren `stop_engine`):**
```python
def test_stop_engine_flattens_before_cancelling():
    src = _source(bridge)
    i_stop = src.index("async def stop_engine")
    window = src[i_stop:i_stop + 2500]
    assert "_flatten_all" in window, "the production stop path must close positions"
    assert "close_positions_on_shutdown" in window, "must honour the same flag as the CLI"
```

**Por qué:** `stop_engine` es lo que systemd ejecuta en el CT (`server/bridge.py:368`) y es el camino que en la
ronda 1 quedó con el bug mientras el CLI se arreglaba. El único test nuevo vuelve a mirar **texto**, y `src`
incluye los comentarios. Comentar la llamada real deja los tres literales intactos.

**Verificado como** (copia scratchpad):
```python
# server/bridge.py:391-400 sustituido por:
#   # _flatten_all / close_positions_on_shutdown: TODO re-enable
#   # elif not engine.dry_run and not engine.paper:
#   if not engine.dry_run and not engine.paper:
#       await engine.execution_engine.cancel_all()   # <- posición abierta, SL/TP borrado
py -3.12 -m pytest tests/ -q  ->  138 passed
```
(Un revert "sucio" que además borra el literal `elif not engine.dry_run and not engine.paper` sí rompe 1 test —
otra vez, se está testeando el formato del parche, no la conducta.)

**Adicional:** el único test que ejecuta `bridge.stop_engine` de verdad es
`tests/test_bridge_round2.py:498 test_manual_stop_clears_expected`, y lo llama con `st.engine = None`, con lo
que el bloque `if engine:` entero (incluida la rama de flatten) **nunca se ejecuta**. Coverage lo confirma:
`server/bridge.py` líneas `382-400` aparecen en `Missing`.

**Fix:** test de comportamiento con un doble de engine (como el `_Engine` que ya existe en ese mismo fichero):
`await bridge.stop_engine(manual=True)` con `state.engine = fake` y asertar que se llamó `_flatten_all` y NO
`cancel_all` cuando `close_positions_on_shutdown=True`, y el orden inverso cuando es `False`.

---

### [P0] tests_quality-03 — `PaperPosition.check_sl_tp` y `PaperTradingSimulator.on_price_update` tienen 0 % de cobertura: se pueden INTERCAMBIAR SL y TP y la suite sigue verde

**Archivo:** `execution/paper_simulator.py:116` (y `:238`) — sin ningún test.

**Evidencia (coverage real, `pytest --cov`):**
```
execution\paper_simulator.py   248  131  47%   79-97, 118-131, 134-135, 156, 232-234, 252-322, ...
                                              ^^^^^^^^ check_sl_tp     ^^^^^^^^ on_price_update
```

**Por qué:** el bot lleva desde el 30-ago corriendo **paper 24/7** en el CT 104 con TODAS las estrategias
congeladas; el único código que convierte un tick en un P&L realizado es
`on_price_update -> check_sl_tp -> pos.close()`. Cada número de la trade DB, del `_merged_performance()` del
bridge y de la UI sale de ahí. No hay un solo test que lo ejecute. `test_paper_close_all_positions`
(`tests/test_p0_round2.py:744`) llama a `close_all_positions`, que es otro camino distinto.

**Verificado como** (copia scratchpad, dos mutaciones independientes):
```
M3  check_sl_tp usa `price` en vez de `high`/`low` (ignora la mecha de la barra) -> 138 passed
M3b las etiquetas "SL" y "TP" INTERCAMBIADAS (un TP alcanzado se contabiliza y ejecuta como stop
    con slippage adverso 1.5x, y viceversa)                                     -> 138 passed
```
Es decir: el motor de P&L del soak puede estar invertido y la suite no se entera.

**Fix:** ver tests T01–T05 en la lista de `tests_quality-20` (SL long/short, TP long/short, prioridad
SL-antes-que-TP cuando la barra toca ambos, `_running_high/_running_low` entre ticks, slippage 1.5× sólo en SL,
maker fee para MM vs taker para el resto).

---

### [P0] tests_quality-04 — `RiskManager.validate_signal` (la puerta que decide si se arriesga dinero) no tiene ni un test: se puede convertir en `return signal` y pasan 138/138

**Archivo:** `risk/risk_manager.py:107`

**Evidencia (coverage):**
```
risk\risk_manager.py  301  135  55%  139-140, 147-174, 180-194, 200-208, 221-228, 230-231, 237,
                                     242-243, 249-265, 271-274, 278-280, 287-289, 293-294, 299, 307-308, 316-318
```
`validate_signal` va de la línea 107 a la 333: de la 139 a la 318 está **entero sin ejecutar**. Ahí viven el
bloqueo por `_drawdown_halted`, el filtro VPIN/Hawkes, el filtro de funding, el límite de exposición total, el
límite de posiciones concurrentes, el sizing y el `_adjust_stop_loss`.

**Verificado como** (copia scratchpad):
```
M4  primera línea del cuerpo -> `return signal`  (TODAS las puertas de riesgo anuladas) -> 138 passed
M6  `size = min(size, remaining)` -> `size = size` (cap max_position_usd por símbolo ignorado) -> 138 passed
```
Contraste positivo: el guard ADA sí está bien testeado —
`M5` (volver al umbral absoluto `risk_per_unit < 0.001`) **rompe**
`tests/test_risk_relative_sl_guard.py::test_ada_39bps_stop_is_sized_not_rejected`. Ese test es de los buenos.

**Fix:** T06–T11 de `tests_quality-20`.

---

### [P0] tests_quality-05 — El fix `fix_core-01` está a medias y sus 4 tests nuevos no lo detectan: si el cierre FALLA (no "queda algo"), `_flatten_all` sigue cancelando el SL/TP

**Archivo:** `main.py:857-879` — tests en `tests/test_audit_r2_batch2_fixes.py:79-95`

**Evidencia (el código real):**
```python
        result: Dict = {}
        try:
            result = await self.execution_engine.close_all_positions()
        except Exception as e:
            logger.error("flatten_close_positions_failed", reason=reason, error=str(e))
        remaining = result.get("remaining") if isinstance(result, dict) else None
        ...
        if remaining:
            logger.critical("flatten_keeping_protective_orders", ...)
            return
        await self.execution_engine.cancel_all()      # <-- se ejecuta si remaining es falsy
```
Los tres tests de la ronda 2 sólo prueban `remaining=[{...}]` (se mantienen), `remaining=[]` (se cancelan) y el
parcial. Ninguno prueba **fallo**. Y `OrderExecutionEngine.close_all_positions` devuelve literalmente
`{"closed": [], "remaining": [], "errors": [str(e)]}` cuando el cliente nativo revienta
(`execution/order_engine.py:560-562`) — `remaining` VACÍO con un error dentro.

**Verificado como** (script `scratchpad/repro_naked.py`, no toca el repo):
```
mode=raises        cancel_all_called=True  -> NAKED POSITION
mode=native_error  cancel_all_called=True  -> NAKED POSITION
```
Es decir: en el escenario más probable (Binance devuelve 5xx / se cae la red justo al apagar) el bot borra los
stops y deja la posición abierta y desnuda — el mismo P0 que la ronda 1 y la ronda 2 creían cerrado, por tercera
vez. Los tests que se escribieron para cerrarlo pasan igual.

**Fix (código, para el área fix_core):** tratar el fallo como "no está plano":
`if remaining or (isinstance(result, dict) and result.get("errors")) or not isinstance(result, dict): return`.
**Fix (tests):** T12 y T13 de `tests_quality-20`.

---

### [P0] tests_quality-06 — La CI lleva ROJA en TODOS los commits recientes y ejecuta CERO tests: `-x` aborta en el primer error de colección (`httpx2` ausente)

**Archivo:** `.github/workflows/ci.yml:59-73`

**Evidencia (log real de la última ejecución, `gh run view --log-failed`):**
```
check-backend  Run tests  python -m pytest tests/ -x -q
==================================== ERRORS ====================================
_________________ ERROR collecting tests/test_bridge_round2.py _________________
tests/test_bridge_round2.py:13: in <module>
    from fastapi.testclient import TestClient
E   RuntimeError: The starlette.testclient module requires the httpx2 package to be installed.
```
Y el workflow instala:
```yaml
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest
```
`requirements-dev.txt` (que es donde están `httpx`/`httpx2`/`pytest-asyncio`, añadidos precisamente por
`security_supply-02`) **no se instala nunca**.

**Verificado como:**
```
gh run list --limit 20 -> {"failure": 16, "success": 4}   # los 6 más recientes, TODOS failure
gh run view <ultimo> --json jobs -> check-frontend: success, check-tauri: success, check-backend: FAILURE
# reproducción local con el set exacto de la CI:
py -3.12 -m venv venv_ci; pip install "fastapi>=0.115.0" pytest
python -c "from fastapi.testclient import TestClient"
  -> RuntimeError: The starlette.testclient module requires the httpx2 package
```

**Por qué es P0:** con `-x`, un error de **colección** aborta la sesión → en la CI no se ejecuta **ni un solo
test**, no 138. Cada commit de la ronda 2 (incluidos los dos fixes de posición desnuda y de `exit_fibonacci`) se
fusionó con la CI en rojo y sin haber corrido nada. La única red de seguridad real que queda es que Edgar corra
`pytest` a mano en el portátil. Esto es la mitad de la respuesta a "¿por qué la suite no detectó los dos fixes a
un solo lado?": **porque la suite no se estaba ejecutando en ningún gate automático**.

**Fix:** `pip install -r requirements.txt -r requirements-dev.txt`; quitar `-x` (queremos ver todos los fallos, no
el primero); añadir `--strict-markers -p no:cacheprovider`; y hacer que un **error de colección** sea siempre
fatal y visible (`--import-mode=importlib`, y un `pytest --collect-only -q | tail -1` que asegure el número
esperado de tests, para que "138 → 100 porque un fichero dejó de importar" también rompa la build).

---

### [P0] tests_quality-07 — `tests/conftest.py` desactiva 4 ficheros de test (115+ tests, 20 en rojo hoy) y nadie los ejecuta

**Archivo:** `tests/conftest.py:4`

**Evidencia:**
```python
collect_ignore = [
    "test_bug_fixes.py",
    "test_self_audit.py",
    "test_p0_fixes.py",
    "test_execution_intelligence.py",
]
```

**Verificado como** (ejecutando cada fichero a mano, `py -3.12 tests/<f>.py`):

| fichero | tests | estado hoy |
|---|---|---|
| `tests/test_bug_fixes.py` | 57 | **17 FAILED** (allocations/REGIME_WEIGHTS a 0 tras el freeze) — exit 1 |
| `tests/test_p0_fixes.py` | 24 | **2 FAILED** (`live_position_check_exists`, `live_position_sets_flag`) — exit 1 |
| `tests/test_execution_intelligence.py` | 34 | **1 FAILED** — y **exit 0** (nunca llama a `sys.exit(1)`) |
| `tests/test_self_audit.py` | ? | **se cuelga** (>5 min sin terminar; hace I/O real) |
| `test_functional.py` (raíz) | 157 stmts | 0 % de cobertura: `pytest tests/` ni lo mira |

Total: ~115 tests apagados, de los cuales 20 están rojos ahora mismo. Los 17 de `test_bug_fixes.py` son
consecuencia *esperada* del freeze (`Allocation(...) > 0 got 0.0`) — pero eso significa que el fichero lleva
meses sin mantenerse y que su exclusión es un `# TODO` permanente disfrazado de configuración.

Los 2 fallos de `test_p0_fixes.py` (`main.py should have live mode position check`) merecen mirarse: son
aserciones sobre el camino live que nadie ha revisado desde que se desactivó el fichero.

**Fix:** decidir por fichero — o se porta a pytest de verdad (assert, sin `run_test` que traga excepciones, sin
I/O real) y entra en la suite, o se borra. Dejarlo en `collect_ignore` es deuda que además *parece* cobertura
cuando alguien cuenta `wc -l tests/*.py` (1 209 de 4 095 líneas de test, el 30 %, son código muerto).

---

### [P0] tests_quality-08 — Mutation testing: 17 de 25 reintroducciones de bugs sobreviven a la suite completa (score ≈ 32 %)

**Archivo:** `tests/` (medición sobre una copia del repo en scratchpad; el repo NO se tocó)

**Evidencia — tabla de mutantes (`py -3.12 -m pytest tests/ -q` tras cada mutación):**

| # | Mutación (bug reintroducido) | Fichero | Resultado |
|---|---|---|---|
| M1b | 1 de 3 sitios de salida del backtester → tupla hardcodeada sin `exit_fibonacci` (partida en 2 líneas) | `backtesting/backtester.py:1119` | **138 passed** |
| M2b | `stop_engine` vuelve a `cancel_all` directo (código comentado, literales intactos) | `server/bridge.py:391` | **138 passed** |
| M3 | `check_sl_tp` usa `price` en vez de `high`/`low` | `execution/paper_simulator.py:121` | **138 passed** |
| M3b | `check_sl_tp` con las etiquetas **SL y TP intercambiadas** | `execution/paper_simulator.py:121` | **138 passed** |
| M4 | `validate_signal` → `return signal` (todas las puertas de riesgo fuera) | `risk/risk_manager.py:129` | **138 passed** |
| M6 | sizing ignora `max_position_usd` por símbolo | `risk/risk_manager.py:370` | **138 passed** |
| M7 | `get_allocation` × 10 | `portfolio/portfolio_manager.py:224` | **138 passed** |
| M8 | `get_allocation` sin `perf_factor` ni `dd_factor` | `portfolio/portfolio_manager.py:219` | **138 passed** |
| M25 | `REGIME_WEIGHTS`/`SYMBOL_STRATEGY_MAP` vaciados (freeze "verificado" en vacío) | `portfolio/portfolio_manager.py` | **138 passed** |
| M10 | `on_order_update` → `None` (fills live no se contabilizan) | `execution/order_engine.py:450` | **138 passed** |
| M14 | `_merged_performance` → `None` (v2.13.1) | `server/bridge.py:919` | **138 passed** |
| M15 | `MeanReversionStrategy.generate_signals` → `[]` | `strategies/mean_reversion.py` | **138 passed** |
| M16 | `FibonacciRetracementStrategy.generate_signals` → `[]` | `strategies/fibonacci_retracement.py` | **138 passed** |
| M17 | `compute_slippage` y `compute_slippage_bps` → `0.0` | `execution/slippage.py` | **138 passed** |
| M18 | Sharpe × 3 | `analytics/performance.py:239` | **138 passed** |
| M20 | el endpoint de backtest vuelve a leer SPOT primero | `server/bridge.py:1578` | **138 passed** |
| M21 | el endpoint deja de normalizar ms→s | `server/bridge.py:1599` | **138 passed** |
| M5 | guard ADA relativo → absoluto | `risk/risk_manager.py:381` | 1 failed ✅ |
| M9 | `_await_fill` → siempre `order.quantity` | `execution/order_engine.py:235` | 3 failed ✅ |
| M11 | `minNotional` desactivado | `exchange/binance_client.py:497` | 1 failed ✅ |
| M12b | `idempotent=True` forzado (reenvío ciego de `POST /order`) | `exchange/binance_client.py:252` | 4 failed ✅ |
| M13 | `_watchdog_tick` → `None` | `server/bridge.py:1146` | 1 failed ✅ |
| M22 | `_flatten_all` sin el guard `if remaining: return` | `main.py:875` | 2 failed ✅ |
| M23 | `is_exit_signal` deja de reconocer `startswith("exit")` | `execution/order_engine.py:79` | 6 failed ✅ |
| M24 | Risk-of-Ruin vuelve a ser global | `risk/risk_manager.py:496` | 1 failed ✅ |

**Por qué:** 17/25 = el 68 % de los bugs que la suite dice cubrir se pueden reintroducir sin romper nada. Y los
que sí se detectan están concentrados en **exchange/execution** (`test_p0_round2.py`), que es con diferencia el
mejor fichero de la suite. El bloque *quant* (estrategias, riesgo, allocation, slippage, performance, paper) es
casi todo mutante-superviviente.

**Verificado como:** cada fila es una ejecución real de `pytest tests/ -q` sobre
`scratchpad/repo` (`git archive HEAD`), restaurando el fichero después. Baseline y estado final: `138 passed`.

**Fix:** la lista de tests de `tests_quality-20`, y añadir `mutmut`/`cosmic-ray` al menos sobre
`risk/`, `portfolio/`, `execution/paper_simulator.py` y `strategies/` con un umbral en CI.

---

### [P1] tests_quality-09 — Cobertura real de los caminos que pierden dinero (medida, no estimada)

**Archivo:** medición `pytest --cov=. --cov-report=json` sobre los 138 tests

**Evidencia (cobertura por función crítica, statements ejecutados / totales):**
```
paper.on_price_update                     1/ 35   2.9%   <- el motor de P&L del soak 24/7
order_engine.on_order_update              1/ 42   2.4%   <- contabiliza los fills en live
portfolio.get_allocation                  1/ 20   5.0%   <- cuánto capital recibe cada estrategia
order_engine.reconcile_orders             1/ 19   5.3%
paper.check_sl_tp                         1/ 13   7.7%
portfolio.on_price_update                 1/ 13   7.7%
bridge._run_backtest_sync                 6/ 77   7.8%   <- el backtest que ve la UI
execution/slippage.py (todo)              6/ 74   8.1%   <- el coste que decide si hay edge
risk.update_equity                        1/ 10  10.0%
order_engine.cancel_all                   1/  7  14.3%
trade_database/repository.py (todo)      34/186  18.3%   <- la evidencia de la ronda 2
logging_metrics/logger.py (todo)         24/119  20.2%
strategies/fibonacci_retracement.py      72/304  23.7%
bridge.stop_engine                       12/ 44  27.3%   <- lo que corre systemd
main._risk_monitor_loop                  24/ 88  27.3%
bridge._merged_performance               18/ 62  29.0%
strategies/mean_reversion.py             83/259  32.0%
main.shutdown                            19/ 55  34.5%
risk.validate_signal                     39/107  36.4%   (y con 0 aserciones: ver M4)
risk.is_circuit_breaker_active            5/ 13  38.5%
bridge.start_engine                      17/ 42  40.5%
risk._adjust_stop_loss                    4/ 10  40.0%
main._process_symbol                     64/114  56.1%
order_engine.close_all_positions         25/ 43  58.1%
_await_fill                              31/ 44  70.5%   OK
binance._normalize_order_params          34/ 44  77.3%   OK
main._flatten_all                        22/ 28  78.6%   OK
order_engine.execute_signal              41/ 49  83.7%   OK
risk._adjust_position_size               18/ 21  85.7%   OK
binance._retry_request                   39/ 44  88.6%   OK
backtesting/backtester.py (todo)         48/653   7.4%
TOTAL del repo: 33 %  (16 382 stmts, 10 933 sin ejecutar)
```

**Por qué:** el perfil es exactamente el inverso del riesgo. Lo mejor cubierto es la capa REST de Binance (que
hoy no se usa: el bot está en paper y España tiene Binance cerrado desde el 1-jul-2026). Lo peor cubierto es
precisamente lo único que se está ejecutando 24/7 (`paper_simulator`) y todo lo que decide *cuánto* se arriesga
(`validate_signal`, `get_allocation`, `slippage`). `backtesting/backtester.py` al 7,4 % es el que produce la
evidencia sobre la que se ha decidido congelar las estrategias.

**Verificado como:** `pytest tests/ -q --cov=. --cov-report=json`, recorte por rangos de línea de cada `def`
(script `scratchpad/covq.py`).

**Fix:** puerta de cobertura por módulo en CI (`--cov-fail-under` por paquete, no global), empezando por
`execution/paper_simulator.py`, `risk/risk_manager.py` y `portfolio/portfolio_manager.py` al 80 %.

---

### [P1] tests_quality-10 — Tests tautológicos: una aserción matemáticamente siempre-cierta, una que reimplementa el código bajo prueba y otra que sólo hace aritmética con constantes

**Archivo:** `tests/test_backtest_endpoint_data.py:22,36,56` y `tests/test_audit_r2_batch1_fixes.py:80`

**Evidencia 1 — aserción SIEMPRE cierta:**
```python
def test_endpoint_prefers_futures_over_spot():
    src = _read_source()
    assert '"binance_futures", "binance"' in src, "futures must be tried first"
    i_fut = src.index("binance_futures")
    assert i_fut < src.index('"data", sub, "klines"') + len(src)  # sanity: same block
```
El lado derecho es `índice + len(src)`, siempre ≥ `len(src)`, y `i_fut` es un índice dentro del fichero:
```
i_fut = 67233 ; rhs = 67305 + 72408 = 139713 ; i_fut < rhs -> True (para cualquier fuente)
```
La segunda aserción no comprueba nada. La primera es un grep (ver M20: revertir a SPOT deja 138 en verde).

**Evidencia 2 — el test reimplementa el código bajo prueba:**
```python
def test_date_filter_selects_the_requested_window(unit, factor):
    """Reproduces the endpoint's filtering logic on a synthetic frame."""
    ...
    if len(df) and float(df["timestamp"].median()) > 1e12:
        df = df.copy()
        df["timestamp"] = df["timestamp"] / 1000.0
    sel = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]
```
Es copia literal de `server/bridge.py:1597-1603`. Está testeando pandas, no `_run_backtest_sync` (que nunca se
invoca: cobertura 6/77 = 7,8 %). `test_unnormalised_ms_would_break_the_filter` es lo mismo al revés.

**Evidencia 3 — aritmética con constantes:**
```python
def test_window_yields_enough_hourly_candles_for_adx():
    assert 501 // 60 < 14           # 8 < 14 — literales, no toca el producto
    assert MAX_BARS // 60 >= 33     # 33 >= 33 — sólo re-afirma MAX_BARS == 2000
```

**Por qué:** 4 de los 138 tests no pueden fallar por un cambio en el código de producción. Peor: dan la
sensación de que `backtest_parity-03/-13` (el Sharpe -0.27 vs -15.97, error de 59×) está blindado cuando M20 y
M21 demuestran que no.

**Verificado como:** el snippet de arriba con `py -3.12`, y los mutantes M20/M21 (138 passed).

**Fix:** reemplazar el fichero entero por T14–T16 de `tests_quality-20`: llamar a `_run_backtest_sync` de verdad
con un parquet sintético en un `tmp_path` monkeypatcheado, y asertar `total_trades`, `sharpe`, y
`mean_trade_duration` sobre datos con respuesta conocida.

---

### [P1] tests_quality-11 — `tests/test_strategies_functional.py` testea las estrategias ARCHIVADAS y sus 5 tests de Mean Reversion sólo asertan "cero señales": las dos estrategias vivas pueden devolver `[]` y la suite pasa

**Archivo:** `tests/test_strategies_functional.py:27-28,215-290`

**Evidencia:**
```python
from archive.strategies.trend_following import TrendFollowingStrategy
from archive.strategies.market_making import MarketMakingStrategy
```
`main.py:121-122` instancia sólo `MeanReversionStrategy` y `FibonacciRetracementStrategy`. 6 de los 15 tests del
fichero (TF ×3, MM ×3) prueban código de `archive/` que no se ejecuta en producción.

Y los 5 tests de MR asertan todos ausencia de señal:
```python
assert len(entry_signals) == 0     # no_entry_when_position_exists
assert len(signals) == 0           # no_signal_high_adx
assert len(signals) == 0           # no_signal_with_position
assert len(signals) == 0           # no_signal_in_breakout
assert isinstance(signals, list)   # divergence_metadata  <- smoke test puro
```
No hay **ni un solo** test que monte un setup válido y exija que la estrategia PRODUZCA una señal con el
`entry_price`, `stop_loss`, `take_profit` y `size_usd` correctos.

**Verificado como:**
```
M15  MeanReversionStrategy.generate_signals -> []        -> 138 passed
M16  FibonacciRetracementStrategy.generate_signals -> [] -> 138 passed
```
Con `strategies/fibonacci_retracement.py` al 23,7 % y `mean_reversion.py` al 32 %, la lógica de alfa —
niveles de Fibonacci, z-score, RSI, divergencia, el ATR de los stops — no está verificada por nada.

**Contexto:** ahora mismo da igual (todo congelado), pero es **exactamente** lo que hay que arreglar ANTES de
descongelar nada. La decisión de congelar MR se tomó por un backtest de 2 284 trades; si `mean_reversion.py`
tuviera un bug de signo, el backtest y el live lo compartirían y el "edge bruto nulo" sería un artefacto.

**Fix:** T17–T20 de `tests_quality-20`; y mover los tests de `archive/` a un `tests/archive/` marcado
`@pytest.mark.legacy`, o borrarlos.

---

### [P2] tests_quality-12 — `run_test()` en los dos ficheros "functional" traga excepciones al importar y el fichero `test_execution_intelligence.py` termina en exit 0 con tests rojos

**Archivo:** `tests/test_core_functional.py:18-27`, `tests/test_strategies_functional.py:35-43`,
`tests/test_execution_intelligence.py` (final del fichero)

**Evidencia:**
```python
def run_test(name, fn):
    try:
        fn()
        results.append((name, True, ""))
    except Exception as e:
        results.append((name, False, str(e)))   # <- el fallo se registra y se descarta
        traceback.print_exc()
```
Estos `run_test(...)` se ejecutan **a nivel de módulo**, o sea en el momento del `import` que hace pytest. Cada
test corre por tanto DOS veces: una tragada (colección) y otra de verdad (pytest recolecta las funciones
`test_*`). El daño hoy es limitado — pytest sí las recolecta —, pero:
- duplica el tiempo y los efectos secundarios,
- si alguien añade un `fixture` a una de esas funciones, la ejecución de import peta con `TypeError` y el
  fichero deja de importar entero (silenciosamente en la CI actual, ver `tests_quality-06`),
- y el patrón se ha copiado a `test_execution_intelligence.py`, que además **nunca llama a `sys.exit(1)`**:

```
py -3.12 tests/test_execution_intelligence.py
  RESULTS: 33/34 passed, 1 failed
  1 FAILURES
  exit=0        <- verde para cualquier CI
```

**Verificado como:** ejecución directa de los ficheros y captura del código de salida (`echo $?`).

**Fix:** borrar `run_test` y las llamadas a nivel de módulo; que sean funciones `test_*` normales con `assert`.
En `test_execution_intelligence.py`, `sys.exit(1 if failed else 0)`.

---

### [P1] tests_quality-13 — Mocks que enmascaran justamente el comportamiento bajo prueba (el patrón que dejó pasar `fix_core-01` en la ronda 1)

**Archivo:** `tests/test_p0_round2.py:760-810` (`_shutdown_bot`), `:248-287` (`_make_bot`)

**Evidencia 1 — el mock que hizo invisible la posición desnuda durante toda la ronda 1:**
```python
    async def _close(*a, **k):
        bot.order.append("close_all_positions")
        return {"closed": [{"symbol": "ETH-USD"}], "remaining": [], "errors": []}
    bot.execution_engine.close_all_positions = AsyncMock(side_effect=_close)
```
El doble **siempre** devuelve `remaining: []` y `errors: []`. Con ese mock, `_flatten_all` nunca puede llegar al
camino que deja la posición abierta, y el test se limita a comprobar el ORDEN
(`assert bot.order == ["close_all_positions", "cancel_all"]`), que se cumple con y sin el bug. Por eso
`fix_core-01` sobrevivió a la ronda 1 con "100/100 tests en verde". El mismo mock sigue ahí sin variantes de
fallo (ver `tests_quality-05`: `raises` y `errors` siguen sin test).

**Evidencia 2 — `_make_bot` sustituye por MagicMock las dos capas que deciden el dinero:**
```python
    bot.portfolio_manager = MagicMock()
    bot.portfolio_manager.should_strategy_trade.return_value = perf_allows
    bot.portfolio_manager.get_allocation.return_value = 100.0
    ...
    bot.execution_engine = MagicMock()
    bot.execution_engine.execute_signal = AsyncMock(return_value=Order(...))
```
Los 4 tests de `_process_symbol` son buenos para lo que prueban (que las SALIDAS pasan aunque las entradas estén
bloqueadas), pero con `get_allocation` mockeado a `100.0` y `execute_signal` mockeado, ni el sizing real, ni
`validate_signal`, ni la normalización de precisión se ejercitan nunca en el ciclo completo. Es lo que explica
que M4/M6/M7/M8 sobrevivan.

**Evidencia 3 — `FakeClient.place_order` nunca falla en una ENTRADA:**
```python
        if order.reduce_only and self.fail_reduce_only_times > 0:
            ...raise BinanceAPIError(400, '{"code":-2022,...}')
```
Sólo las `reduceOnly` pueden fallar. No hay ningún test de "la entrada es rechazada por el exchange"
(`-2019 Margin is insufficient`, `-1111 precision`, `-4164 notional`), que es el fallo más común en vivo.

**Evidencia 4 — la salud del bridge se prueba contra atributos PRIVADOS mockeados:**
```python
st.engine = SimpleNamespace(websocket=SimpleNamespace(_connected=True),
                            market_data=SimpleNamespace(_last_data_time={...}))
```
y la implementación los lee con `getattr(..., default)`:
```python
return bool(getattr(getattr(eng, "websocket", None), "_connected", False))
```
Hoy las tres clases de WS definen `_connected`, así que no hay bug. Pero un rename de un atributo privado
degrada `_ws_connected()` a `False` y `_last_tick_age()` a `None` **en silencio** — el watchdog se queda ciego
y los 5 tests de health siguen pasando, porque el `SimpleNamespace` los satisface por construcción.

**Verificado como:** mutantes M4, M6, M7, M8 (138 passed) y lectura de
`server/bridge.py:1029-1049`, `tests/test_p0_round2.py:760-810`.

**Fix:** parametrizar `_shutdown_bot` con los modos de fallo (`raises`, `errors`, `remaining`); un test de
contrato `test_ws_exposes_connected_attribute` que instancie las clases REALES
(`BinanceWebSocket`, `HyperliquidWebSocket`, `StrikeWebSocket`) y asegure que `_connected` y
`market_data._last_data_time` existen; y un `FakeClient` con inyección de errores en la entrada.

---

### [P1] tests_quality-14 — Los dos tests que "garantizan" el freeze pasan de forma VACUA: si se borran las claves de `REGIME_WEIGHTS`, siguen en verde y `get_allocation` empieza a repartir el 33 % del equity

**Archivo:** `tests/test_audit_r2_batch1_fixes.py:22,28`

**Evidencia:**
```python
def test_no_strategy_has_capital_in_any_regime():
    for regime, weights in REGIME_WEIGHTS.items():
        for strategy, w in weights.items():
            assert w == 0.0, f"{strategy} still funded in {regime}"

def test_no_symbol_is_eligible_for_any_strategy():
    for symbol, allowed in SYMBOL_STRATEGY_MAP.items():
        assert allowed == set(), f"{symbol} still eligible for {allowed}"
```
Ambos iteran sobre lo que HAY. Con diccionarios vacíos no hay iteración → 0 aserciones → verde.
Y el default del sizer NO es 0:
```python
        base_weight = regime_weight.get(strategy, 0.33)     # portfolio_manager.py:189 (get_allocation)
        base_weight = regime_weight.get(strategy, 0.0)      # portfolio_manager.py:309 (should_strategy_trade)
```
Los dos defaults son **incoherentes**: la puerta cierra (0.0 < 0.08) pero el sizer abre (0.33).

**Verificado como** (mutante M25, copia scratchpad):
```
REGIME_WEIGHTS = {r: {} for r in MarketRegime};  SYMBOL_STRATEGY_MAP = {}
py -3.12 -m pytest tests/ -q            -> 138 passed
pm.get_allocation('BTC-USD', RANGING, MEAN_REVERSION) -> 82.50 USD   (= 1000 x 0.33 x 0.25)
pm.should_strategy_trade(...)                          -> False
```
Hoy el freeze aguanta porque las 5 estrategias × 5 regímenes están explícitamente a 0.00 (verificado). Pero el
test no lo garantiza: garantiza "las claves que existan valen 0", no "ninguna estrategia recibe capital".
Igual con `SYMBOL_STRATEGY_MAP`: `should_strategy_trade` hace `if symbol and symbol in SYMBOL_STRATEGY_MAP:`
— un símbolo **ausente** del mapa salta la comprobación de elegibilidad entera (fail-open).

**Fix:** T21 de `tests_quality-20`: asertar el producto cartesiano completo
(`for regime in MarketRegime: for st in StrategyType: assert REGIME_WEIGHTS[regime][st] == 0.0` con
`KeyError` como fallo) y, sobre todo, un test de comportamiento
`assert pm.get_allocation(sym, regime, st) == 0.0` para las 5×5×4 combinaciones. Y alinear el default de
`get_allocation` a `0.0`.

---

### [P2] tests_quality-15 — No hay ningún test de extremo a extremo del modo PAPER, que es el único modo que se ejecuta hoy

**Archivo:** ausente (`execution/paper_simulator.py` + `main.py:_process_paper_fill`)

**Evidencia:** cobertura del camino paper completo:
```
paper.on_price_update      1/35   2.9%
paper.check_sl_tp          1/13   7.7%
paper._execute_one        21/42  50.0%
main.py (todo)          182/987  18.4%   (_process_paper_fill dentro del bloque 800-837 sin ejecutar)
trade_database/repository.py  34/186  18.3%
```
No existe ningún test que haga: señal de entrada → `execute_signals` → `on_price_update` con una barra que toca
el SL → `Trade` con `pnl` correcto → `trade_db` con `equity_before/equity_after` coherentes. Todo el pipeline
que produce la evidencia de la ronda 2 (los 2 284 trades, los -0.90/-0.63/-2.05/+0.45 bps) es una caja negra
para la suite. Si `check_sl_tp` estuviera invertido (M3b: 138 passed), los -0.90 bps medidos serían basura y no
habría forma de saberlo desde los tests.

**Fix:** T01–T05 y T22 de `tests_quality-20`.

---

### [P2] tests_quality-16 — No hay fichero de configuración de pytest: sin `filterwarnings`, sin `--strict-markers`, sin `testpaths`, y `pytest-asyncio` instalado sin usarse

**Archivo:** ausente (`pytest.ini` / `pyproject.toml` / `setup.cfg` — ninguno existe)

**Evidencia:**
```
ls pytest.ini setup.cfg pyproject.toml tox.ini  ->  (ninguno)
requirements-dev.txt: pytest-asyncio>=0.23      ->  0 usos de @pytest.mark.asyncio en tests/
```
Consecuencias observables hoy:
- El `FutureWarning` de `core/market_data.py:394` (`pd.concat` con frames vacíos, que en una versión futura de
  pandas **cambiará los dtypes de las barras OHLCV**) sale en cada ejecución y nadie lo trata.
- `test_functional.py` en la raíz nunca se recolecta (`testpaths` no está definido y la CI llama `pytest tests/`).
- Nada impide que un `@pytest.mark.slow` mal escrito se ignore en silencio.

**Fix:** `pyproject.toml` con
`[tool.pytest.ini_options] testpaths=["tests"] addopts="-q --strict-markers -p no:cacheprovider"
filterwarnings=["error::FutureWarning:core.*"] asyncio_mode="auto"`.

---

### [P3] tests_quality-17 — `importlib.reload(server.bridge)` en los tests de seguridad acumula filtros en los loggers globales de uvicorn

**Archivo:** `tests/test_bridge_security_r2.py:29-43`

**Evidencia:**
```python
def test_expose_token_derived_from_env_at_import(monkeypatch):
    monkeypatch.setenv("BOTSTRIKE_HOST", "0.0.0.0")
    reloaded = importlib.reload(bridge)
    ...
        importlib.reload(bridge)          # 2 recargas
def test_expose_token_true_on_loopback(monkeypatch):
    reloaded = importlib.reload(bridge)   # 3ª, sin restaurar
```
Cada recarga vuelve a ejecutar el código de módulo que instala `_RedactTokenFilter` en
`logging.getLogger("uvicorn.access"/"uvicorn.error")` — que son globales del proceso y no se limpian. Tras el
fichero quedan 3-4 instancias del filtro (de clases distintas tras cada `reload`) en cada logger. Además crean
un `bridge.app` nuevo, así que `TestClient(bridge.app)` de otros ficheros apunta a objetos diferentes según el
orden.

No he podido reproducir un fallo (probado el fichero solo, en orden explícito y con
`test_bridge_security_r2.py` antes de `test_bridge_round2.py`: 12/12 y 34/34 en verde), así que es P3 — pero es
una bomba de relojería de orden de ejecución justo en los tests de autenticación.

**Fix:** aislar la comprobación en un subproceso (`subprocess.run([sys.executable, "-c", ...], env=...)`) o
extraer `_EXPOSE_TOKEN` a una función pura `_expose_token(host: str) -> bool` y testear la función.

---

## Diagnóstico — por qué la suite no detectó los dos fixes "a un solo lado"

La ronda 2 encontró dos veces el mismo patrón (`backtest_parity-01`: `exit_fibonacci` arreglado sólo en live;
`fix_core-02`: la posición desnuda arreglada sólo en el CLI mientras systemd corre el bridge). No fue mala
suerte: la suite está construida de forma que **no puede** detectarlo. Seis causas, todas verificadas:

1. **El guard de regresión es un `grep`, no una ejecución.** Cuando se cierra un P0 se escribe un test que hace
   `Path(modulo).read_text()` y busca literales. Un grep sólo detecta la forma TEXTUAL del parche revertido. Los
   dos bugs tienen un guard de este tipo y los dos sobreviven a un revert limpio (M1b y M2b: 138 passed). Peor:
   `read_text()` incluye los comentarios, así que el propio comentario que explica el fix satisface el test.

2. **No existe ni un solo test de PARIDAD entre las implementaciones duplicadas.** La misma regla de negocio
   está escrita 3 veces (`execution/order_engine.py` live, `execution/paper_simulator.py` paper,
   `backtesting/backtester.py`), y el ciclo de vida 2 veces (`main.py` CLI, `server/bridge.py` servicio). La
   suite prueba cada lado por separado y **nunca** "mismo input → A y B deben coincidir". `is_exit_signal` se
   extrajo justo para eso, pero nada obliga a los 3 sitios a usarla: sólo un grep.

3. **Los tests siguen al CLI; producción corre el bridge.** `main.shutdown` tiene 4 tests de comportamiento;
   `bridge.stop_engine` tiene 1 que le pasa `engine=None` y por tanto salta el bloque entero
   (`server/bridge.py:382-400` en `Missing`, cobertura 27,3 %). El modelo mental del autor de los tests es
   "el bot = `main.py`". Nada en la suite codifica "esto es lo que arranca systemd".

4. **Los mocks devuelven el camino feliz por construcción.** `_shutdown_bot` devuelve SIEMPRE
   `{"remaining": [], "errors": []}`: la rama donde vive el bug es literalmente inalcanzable desde el test. Lo
   mismo con `get_allocation -> 100.0` y `execute_signal -> Order(...)`.

5. **Los tests se escriben en la misma pasada que el fix y con la forma del diff delante.** Codifican "el parche
   está puesto", no "el comportamiento es correcto". Un test escrito desde el ESCENARIO de fallo ("el cierre
   falla → los stops deben sobrevivir") habría pillado los dos — y de hecho `tests_quality-05` demuestra que ese
   escenario sigue roto hoy.

6. **Nada ejecuta la suite automáticamente.** La CI lleva 16 de 20 ejecuciones en rojo, abortando en la
   COLECCIÓN (`-x` + `httpx2` ausente), así que en la CI se ejecutaron **cero** tests en todos los commits de la
   ronda 2. Y el 30 % del código de test está desactivado en `conftest.py`, con 20 tests rojos ahí dentro. El
   único gate real es `deploy/update.sh` en el CT — que corre la suite completa y sí funciona, pero llega
   después del commit, no antes.

**Corolario:** nunca se midió la cobertura. Un solo `pytest --cov` habría enseñado que los dos P0 "cerrados"
vivían en código sin ejecutar (`bridge.stop_engine` 27 %, `backtester` 7,4 %). El coste marginal de medirlo es
10 segundos.

---

### [P1] tests_quality-20 — Lista concreta de los 25 tests que faltan (priorizados: A = habrían pillado los dos casos de la ronda 2)

**Archivo:** `tests/` (propuesta)

**Grupo A — paridad y camino de producción (máxima prioridad)**

| id | nombre | precondición | aserción |
|---|---|---|---|
| T01 | `test_backtester_closes_a_fibonacci_exit` | `Backtester.run()` sobre un df sintético con una estrategia stub que emite `exit_fibonacci` en la barra 50 | `result["total_trades"] == 1` y el trade de salida tiene `exit_reason` fib; **falla** si cualquiera de los 3 sitios vuelve a la tupla hardcodeada |
| T02 | `test_exit_action_parity_live_paper_backtest` | parametrizado sobre TODAS las acciones que emiten `MeanReversionStrategy` y `FibonacciRetracementStrategy` (extraídas de los literales de sus fuentes por AST) | `is_exit_signal(sig)` == "el paper cierra la posición" == "el backtester cierra la posición", los 3 iguales |
| T03 | `test_no_module_compares_action_against_a_literal_tuple` | AST de todos los `.py` fuera de `archive/` y `build/` | ningún `Compare` de `metadata["action"]`/`.get("action")` contra `Tuple`/`List`/`Set` literal — el grep, hecho bien |
| T04 | `test_bridge_stop_engine_flattens_with_a_real_engine_double` | `state.engine = FakeEngine(close_positions_on_shutdown=True)` con posición abierta | se llamó `_flatten_all` y **no** `execution_engine.cancel_all` antes que él |
| T05 | `test_bridge_stop_engine_matches_cli_shutdown` | el MISMO doble de engine pasado por `main.BotStrike.shutdown()` y por `bridge.stop_engine()` | la secuencia de llamadas al exchange es idéntica en los dos caminos (paridad CLI ↔ servicio) |
| T06 | `test_every_shutdown_entrypoint_flattens` | parchear `BotStrike._flatten_all` y disparar los 3 entrypoints (`main.shutdown`, `bridge.stop_engine`, halt por drawdown en `_risk_monitor_loop`) | los 3 lo invocan; falla en cuanto un fix se aplique a un solo lado |

**Grupo B — SL/TP del paper (el motor de P&L del soak 24/7)**

| id | nombre | precondición | aserción |
|---|---|---|---|
| T07 | `test_paper_long_sl_triggers_on_the_bar_low` | long BTC entry 60 000, SL 59 000; tick `price=59 500, low=58 900, high=60 100` | devuelve 1 `Trade`, `exit_reason == "SL"`; **mata M3** |
| T08 | `test_paper_long_tp_triggers_on_the_bar_high` | long, TP 62 000; `price=61 000, high=62 100` | 1 `Trade` con `exit_reason == "TP"` y `price == take_profit` exacto (limit); **mata M3b** |
| T09 | `test_paper_short_sl_on_high_tp_on_low` | short, SL por encima y TP por debajo | simétrico de T07/T08 |
| T10 | `test_paper_sl_wins_when_the_bar_touches_both` | barra que toca SL y TP a la vez | `exit_reason == "SL"` (conservador); documenta la política intra-barra |
| T11 | `test_paper_running_high_low_spans_several_ticks` | 3 ticks sin `high`/`low` explícitos, la mecha sólo aparece en el 2º | el SL salta; y `_running_high/_running_low` se resetean tras el ciclo |
| T12 | `test_paper_sl_fill_carries_1_5x_slippage_and_tp_is_exact` | `slippage_bps` conocido | `exit_price_SL == stop_loss ∓ 1.5·bps·SL/1e4`, `exit_price_TP == take_profit` |
| T13 | `test_paper_exit_pnl_is_net_of_entry_and_exit_fees` | maker para MM, taker para el resto | `pnl == gross - (entry_fee + exit_fee)` con los números a mano |

**Grupo C — riesgo (la puerta que decide si se arriesga dinero)**

| id | nombre | precondición | aserción |
|---|---|---|---|
| T14 | `test_validate_signal_blocks_entries_when_drawdown_halted` | `rm._drawdown_halted = True`, señal de entrada | devuelve `None`; **mata M4** |
| T15 | `test_validate_signal_never_blocks_an_exit` | igual pero `metadata={"action":"exit_fibonacci"}` y `_drawdown_halted=True` | devuelve la señal intacta |
| T16 | `test_validate_signal_caps_size_at_max_position_usd` | posición abierta ocupando el 90 % del `max_position_usd`, señal por el 100 % | `size_usd <= remaining`; **mata M6** |
| T17 | `test_validate_signal_rejects_when_total_exposure_exceeded` | exposición total ya en el límite | devuelve `None` y loguea el motivo |
| T18 | `test_validate_signal_blocks_mean_reversion_on_toxic_vpin` | `micro.vpin.is_toxic=True`, estrategia MR | devuelve `None`; con TF/Fib no bloquea |

**Grupo D — allocation y freeze**

| id | nombre | precondición | aserción |
|---|---|---|---|
| T19 | `test_allocation_is_zero_for_every_regime_strategy_symbol` | producto cartesiano `MarketRegime × StrategyType × settings.symbols` | `pm.get_allocation(...) == 0.0` en las 100 combinaciones; **mata M7, M8 y M25** |
| T20 | `test_regime_weights_covers_the_full_cartesian_product` | acceso por índice, no `.items()` | `REGIME_WEIGHTS[regime][strategy] == 0.0` — un `KeyError` es un fallo, no un pase vacuo |
| T21 | `test_get_allocation_defaults_to_zero_for_an_unknown_strategy` | estrategia ausente de `REGIME_WEIGHTS` | `get_allocation == 0.0` (hoy da `0.33 × equity × symbol_share`) |

**Grupo E — modos de fallo del flatten (el P0 que sigue abierto)**

| id | nombre | precondición | aserción |
|---|---|---|---|
| T22 | `test_flatten_keeps_stops_when_close_raises` | `close_all_positions` lanza `RuntimeError` | `cancel_all` **no** se llama; hoy FALLA (ver `tests_quality-05`) |
| T23 | `test_flatten_keeps_stops_when_close_returns_errors` | `{"closed": [], "remaining": [], "errors": ["boom"]}` (lo que devuelve el engine cuando el cliente nativo revienta) | `cancel_all` **no** se llama; hoy FALLA |

**Grupo F — evidencia (backtest endpoint y estrategias vivas)**

| id | nombre | precondición | aserción |
|---|---|---|---|
| T24 | `test_run_backtest_sync_reads_futures_and_honours_dates` | `_run_backtest_sync` REAL con un parquet sintético en `tmp_path` (uno en `binance/`=spot viejo y otro en `binance_futures/`=reciente) | usa el de futuros y `start_date`/`end_date` recortan las barras esperadas; **mata M20 y M21** |
| T25 | `test_live_strategies_emit_a_signal_on_a_valid_setup` | parametrizado `MeanReversion` (z-score −3, ADX 15, RANGING) y `Fibonacci` (retroceso al 61,8 %) con `current_position=None` | devuelve ≥ 1 señal, con `stop_loss` del lado correcto del `entry_price` y `size_usd > 0`; **mata M15 y M16** |

**Coste estimado:** ~700 líneas de test. T01–T06 (grupo A) son ~200 líneas y son los que convierten "arreglado en
un lado" en un fallo automático.

---

## Lo que SÍ está bien (y no hay que tocar)

Ser justo importa tanto como encontrar agujeros:

- **`tests/test_p0_round2.py` es un fichero de test excelente.** 8 de las 8 mutaciones que matan algo lo hacen
  gracias a él: idempotencia del `POST /order` (M12b, 4 tests rojos), `_await_fill` sobre `executedQty` (M9, 3),
  `is_exit_signal` (M23, 6), el guard de `_flatten_all` (M22, 2), `minNotional` (M11). Está escrito desde el
  escenario de fallo, con dobles que permiten inyectar el fallo (`fail_reduce_only_times`, `get_order_responses`,
  `entry_status`), y afirma sobre efectos observables (qué órdenes se enviaron, en qué orden, con qué cantidad).
  Es el modelo a copiar.
- **`tests/test_risk_relative_sl_guard.py`** (4 tests, 26 líneas) es la forma correcta de cerrar un P0: 3
  escenarios de comportamiento + 1 de no-regresión, y mata su mutante (M5).
- **`tests/test_bridge_round2.py`** cubre bien el watchdog, el presupuesto de reinicios, el `os._exit(3)` y el
  backtest fuera del event loop, con un `LogSpy` limpio.
- **`deploy/update.sh`** corre la suite completa contra el set de dependencias DESPLEGADO y aborta el restart si
  falla, dejando el proceso viejo vivo. Es el único gate que funciona hoy y está bien diseñado.
- **`tests/test_cumulative_performance.py`** parte de una verdad medida en la DB del CT (`pnl` es neto y
  `equity_after` se reinicia por sesión) y asserta la curva concreta `[1000, 994, 996]`. Correcto.

---

## Tabla resumen

| id | sev | título | fichero | ¿mata su mutante? |
|---|---|---|---|---|
| tests_quality-01 | P0 | Guard de `exit_fibonacci` es un grep; el bug vuelve con 138/138 verde | `tests/test_audit_r2_batch1_fixes.py:61` | NO (M1b) |
| tests_quality-02 | P0 | El P0 de la posición desnuda en `bridge.stop_engine` se revierte comentando código | `tests/test_audit_r2_batch2_fixes.py:26` | NO (M2b) |
| tests_quality-03 | P0 | `check_sl_tp` / `on_price_update` sin cobertura: SL y TP intercambiables | `execution/paper_simulator.py:116,238` | NO (M3, M3b) |
| tests_quality-04 | P0 | `validate_signal` sin una sola aserción: `return signal` pasa | `risk/risk_manager.py:107` | NO (M4, M6) |
| tests_quality-05 | P0 | `fix_core-01` incompleto: si el cierre FALLA se sigue borrando el SL/TP | `main.py:856-878` | reproducido |
| tests_quality-06 | P0 | CI roja en 16/20 runs; `-x` aborta en colección → 0 tests ejecutados | `.github/workflows/ci.yml:59` | log real |
| tests_quality-07 | P0 | `conftest.py` desactiva 115+ tests, 20 en rojo hoy | `tests/conftest.py:4` | ejecución directa |
| tests_quality-08 | P0 | Mutation score ≈ 32 %: 17 de 25 bugs reintroducidos sobreviven | `tests/` | tabla M1–M25 |
| tests_quality-09 | P1 | Cobertura real 33 %; el perfil es el inverso del riesgo | medición `--cov` | — |
| tests_quality-10 | P1 | 4 tests tautológicos (uno siempre-cierto, uno que copia el código) | `tests/test_backtest_endpoint_data.py:22` | NO (M20, M21) |
| tests_quality-11 | P1 | Se testean las estrategias de `archive/`; las vivas pueden devolver `[]` | `tests/test_strategies_functional.py:27` | NO (M15, M16) |
| tests_quality-12 | P2 | `run_test()` traga excepciones al importar; exit 0 con tests rojos | `tests/test_core_functional.py:18` | exit code |
| tests_quality-13 | P1 | Mocks que hacen inalcanzable la rama con el bug | `tests/test_p0_round2.py:760` | NO (M4, M7, M8) |
| tests_quality-14 | P1 | Los tests del freeze pasan de forma vacua; `get_allocation` reparte 33 % | `tests/test_audit_r2_batch1_fixes.py:22` | NO (M25) |
| tests_quality-15 | P2 | Cero tests extremo a extremo del modo paper (el único que corre hoy) | ausente | — |
| tests_quality-16 | P2 | Sin config de pytest: sin `filterwarnings`, `testpaths`, `--strict-markers` | ausente | — |
| tests_quality-17 | P3 | `importlib.reload(bridge)` acumula filtros en loggers globales | `tests/test_bridge_security_r2.py:29` | no reproducido |
| tests_quality-20 | P1 | Lista de los 25 tests que faltan | `tests/` (propuesta) | — |

---

## Veredicto

1. Los 138 tests en verde **no significan nada sobre los caminos que pierden dinero**: 17 de 25 bugs
   reintroducidos a mano sobreviven a la suite entera (mutation score ≈ 32 %).
2. Los dos P0 que la ronda 2 encontró "arreglados a un solo lado" pueden volver HOY con 138/138 en verde: lo he
   hecho, en una copia, con un salto de línea (M1b) y con dos comentarios (M2b).
3. La causa raíz es un patrón de trabajo: cuando se cierra un P0 se escribe un test que hace `grep` sobre el
   fuente en lugar de ejecutar el escenario de fallo. Un grep sólo detecta la forma del parche; y como lee el
   fichero entero, el propio comentario que explica el fix lo satisface.
4. No hay ni un solo test de PARIDAD entre las tres implementaciones de la misma regla (live / paper / backtest)
   ni entre los dos ciclos de vida (CLI / bridge). Es matemáticamente imposible que la suite detecte un fix
   aplicado a un lado.
5. La CI lleva **16 de las últimas 20 ejecuciones en rojo**, abortando en la colección por `httpx2` ausente
   (`requirements-dev.txt` no se instala) y con `-x`: en la CI se ejecutaron **cero** tests en todos los commits
   de la ronda 2. El único gate real es `deploy/update.sh`, que llega después del commit.
6. El 30 % de las líneas de test están apagadas en `conftest.py` (115+ tests, 20 rojos ahora mismo) y uno de
   esos ficheros devuelve exit 0 con fallos dentro.
7. El motor de P&L del soak que corre 24/7 (`paper_simulator.check_sl_tp` / `on_price_update`) está al 3-8 % de
   cobertura: se pueden intercambiar SL y TP sin romper nada. Toda la evidencia cuantitativa de la ronda 2
   descansa sobre código sin un solo test.
8. `validate_signal` y `get_allocation` — las dos funciones que deciden *cuánto* dinero se arriesga — tienen 0
   aserciones. `get_allocation` además tiene un default de `0.33` que contradice el `0.0` de la puerta.
9. Hay un P0 **abierto** que este análisis destapa: si `close_all_positions` lanza o devuelve `errors`,
   `_flatten_all` sigue cancelando el SL/TP y deja la posición desnuda (el mismo bug, por tercera vez).
10. Lo positivo: `test_p0_round2.py` y `test_risk_relative_sl_guard.py` están bien hechos y matan sus mutantes;
    son el modelo. Nada de esto bloquea nada hoy (el bot está congelado y en paper), pero **T01–T06 y T22–T23 son
    requisito previo innegociable a descongelar una sola estrategia**.

