# Auditoría R2 — AREA: tests_quality

**Fecha:** 2026-08-31
**Alcance:** `tests/*.py` (14 ficheros, 138 tests), `test_functional.py` (raíz), `tests/conftest.py`.
**Objetivo:** (a) verificar que los tests que respaldan los fixes realmente fallarían si el bug volviera; (b) medir cobertura real de los caminos que pierden dinero; (c) diagnosticar por qué la suite no detectó los dos fixes "a un solo lado"; (d) listar los tests que faltan.

> Estado: EN PROGRESO (informe incremental).

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
