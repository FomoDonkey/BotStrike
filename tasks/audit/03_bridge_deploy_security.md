# Auditoría 03 — Bridge / Deploy / Seguridad / Persistencia (BotStrike)

**Fecha:** 2026-08-29 · **Ámbito:** `server/`, `deploy/`, `config/settings.py`, `.env*`, `.gitignore`, `requirements.txt`, `notifications/telegram.py`, `logging_metrics/`, `trade_database/`, `scripts/live_monitor.py`, `automation/`.
**Target de despliegue:** LXC Debian 13 (CT 104), usuario `botstrike`, `/opt/botstrike/app`, systemd `botstrike-bridge.service`, `BOTSTRIKE_AUTOSTART=paper`.
**Método:** lectura del código real + ejecución local (`py -3.12`) donde ha sido posible. Cada hallazgo indica cómo se verificó. Se escribe de forma incremental.

---

## Hallazgos

### [P0] `BOTSTRIKE_AUTOSTART=paper` no existe en el código: el engine NUNCA arranca solo en el servidor
> **ESTADO: CORREGIDO en `c18bb32`** (v2.12.0): `lifespan` lee `BOTSTRIKE_AUTOSTART=paper|dry_run` y arranca el engine; `live` se rechaza. Desplegado en CT 104 con `verify.sh` PASS. Se conserva el hallazgo original como registro; el fix propuesto abajo queda como referencia (el watchdog interno sigue pendiente → ver P1 health).

**Archivo:** `deploy/botstrike-bridge.service:19-20`, `server/bridge.py:716-741` (lifespan), `server/bridge.py:1058-1081` (main)
**Evidencia:**
```ini
# deploy/botstrike-bridge.service
Environment=BOTSTRIKE_AUTOSTART=paper
Environment=BOTSTRIKE_AUTOSTART_EXCHANGE=binance
```
```python
# server/bridge.py:716-731
async def lifespan(app: FastAPI):
    """CRITICAL: Do NOT start the engine here. The user selects exchange and mode
    from the desktop UI, then clicks Start which calls POST /api/bot/start."""
    loops = [ ...broadcast loops... ]
    logger.info("bridge_ready", port=9420)
    yield
```
`grep -rn "AUTOSTART|BOTSTRIKE_HOST|BOTSTRIKE_PORT"` (excluyendo desktop/archive/build) → **solo aparece en `deploy/*.service` y `deploy/README.md`**. Ningún `.py` lee `os.getenv("BOTSTRIKE_AUTOSTART")`. `main()` solo parsea `--host/--port/--live/--dry-run/--dev` y fija `state.mode` (una etiqueta), sin arrancar nada.
**Por qué es un problema:** El servicio arranca, abre el puerto, `/api/health` devuelve `200 {"status":"ok","engine_running":false}` y **no se opera nunca** hasta que alguien pulse Start en el desktop. Tras cualquier reinicio (`Restart=always`, `update.sh`, reboot del CT, crash) el bot vuelve a quedarse parado en silencio. El objetivo "paper 24/7 desatendido" es imposible con el código actual. `deploy/verify.sh:33` lo detecta (`engine NOT running (autostart failed?)`) → el despliegue actual **no puede pasar su propia verificación**.
**Fix propuesto:**
```diff
--- a/server/bridge.py
+++ b/server/bridge.py
@@ async def lifespan(app: FastAPI):
     loops = [ ... ]
-    logger.info("bridge_ready", port=9420)
+    logger.info("bridge_ready", host=os.getenv("BOTSTRIKE_HOST","127.0.0.1"), port=os.getenv("BOTSTRIKE_PORT","9420"))
+    autostart = os.getenv("BOTSTRIKE_AUTOSTART", "").strip().lower()
+    if autostart:
+        if autostart == "live":
+            logger.critical("autostart_live_refused"); raise SystemExit(3)   # nunca live desatendido
+        if autostart not in VALID_MODES:
+            logger.critical("autostart_invalid_mode", mode=autostart); raise SystemExit(3)
+        state.exchange = os.getenv("BOTSTRIKE_AUTOSTART_EXCHANGE", "binance")
+        async def _autostart():
+            await asyncio.sleep(2)           # dejar que uvicorn termine el bind
+            try:
+                await start_engine(autostart)
+                logger.info("engine_autostarted", mode=autostart, exchange=state.exchange)
+            except Exception as e:
+                logger.critical("engine_autostart_failed", error=str(e))
+                os._exit(4)                 # que systemd reinicie el proceso (Restart=always)
+        loops.append(asyncio.create_task(_autostart()))
     yield
```
Y añadir un **watchdog interno** (ver P1 "health hueco") que, si `BOTSTRIKE_AUTOSTART` está definido y `state.running` pasa a `False` (crash del engine), salga del proceso con código ≠0 para que systemd lo levante.
**Verificado cómo:** leído (`grep` repo-wide) + ejecutado: `py -3.12 -m server.bridge --port 9477` → `GET /api/health` → `{"status":"ok","engine_running":false,...}`; ningún log de arranque de engine.

### [P0] El "auth token" de live se regala en `/api/bot/status` sin autenticación; `start/stop` de paper y `backtest` no piden nada
> **ESTADO: CORREGIDO en `c18bb32`** (v2.12.0): `BOTSTRIKE_AUTH_TOKEN` desde `.env`; `/api/bot/status` solo expone el token cuando el bind es loopback; en `0.0.0.0` sin token configurado se avisa. Desplegado en CT 104. **Residual (P1):** `POST /api/bot/start?mode=paper|dry_run`, `POST /api/bot/stop` en paper, `/api/backtest/run` y `/ws/*` siguen sin exigir token, y `/docs` sigue público → cualquiera de la tailnet/LAN puede parar el bot paper o lanzar backtests (ver P1 backtest y P2 WS).

**Archivo:** `server/bridge.py:40`, `server/bridge.py:799-832`, `server/bridge.py:835-845`, `deploy/botstrike-bridge.service:16`, `deploy/install.sh:46-47`
**Evidencia:**
```python
_AUTH_TOKEN = secrets.token_hex(16)                       # bridge.py:40
...
@app.post("/api/bot/start")
async def bot_start(mode: str = "paper", exchange: str = "binance", token: str = ""):
    if mode == "live" and token != _AUTH_TOKEN:           # bridge.py:802
        return {"error": "Invalid or missing auth token for live mode"}
...
@app.get("/api/bot/status")
async def bot_status():
    return { ..., "auth_token": _AUTH_TOKEN, ... }        # bridge.py:843  ← lo publica
```
Ejecutado contra el bridge en :9477:
```
$ curl -s http://127.0.0.1:9477/api/bot/status
{"running":false,"mode":"paper","uptime_sec":0,"equity":300.0,"pnl":0.0,"auth_token":"7373e2cc09f56ce24b73a3a095ab958e","exchange":"binance"}
harvested token length: 32
```
La unit fija `BOTSTRIKE_HOST=0.0.0.0` y `install.sh` abre `9420/tcp` a **toda** la LAN `192.168.1.0/24` y a la tailnet `100.64.0.0/10`. El desktop (`desktop/src/lib/api.ts:24-25`) ni siquiera envía token.
**Por qué es un problema:** Cualquier dispositivo de la LAN (IoT, invitado wifi, portátil comprometido) o cualquier nodo de la tailnet puede: (1) leer el token y arrancar **LIVE** con dinero real (`POST /api/bot/start?mode=live&token=<cosechado>`), (2) parar el bot paper (`POST /api/bot/stop` sin token), (3) arrancar paper/dry_run, (4) lanzar backtests que congelan el engine (ver P1). CORS solo protege contra navegadores; `curl` y cualquier proceso lo saltan (verificado: preflight desde `http://evil.example` → 400, pero la petición directa sin `Origin` → 200). La "autenticación" es puramente decorativa.
**Fix propuesto:**
```diff
--- a/server/bridge.py
+++ b/server/bridge.py
-_AUTH_TOKEN = secrets.token_hex(16)
+# Token estático de operador (min 32 chars) — obligatorio en TODA mutación. Se define en .env / unit.
+_AUTH_TOKEN = os.getenv("BOTSTRIKE_API_TOKEN", "")
+if len(_AUTH_TOKEN) < 32:
+    raise SystemExit("BOTSTRIKE_API_TOKEN missing/too short (>=32 chars). Generate: python -c 'import secrets;print(secrets.token_hex(32))'")
+
+from fastapi import Depends, Header, HTTPException
+def require_token(x_botstrike_token: str = Header(default="")):
+    if not secrets.compare_digest(x_botstrike_token, _AUTH_TOKEN):
+        raise HTTPException(status_code=401, detail="invalid token")
+
+_LIVE_ENABLED = os.getenv("BOTSTRIKE_ALLOW_LIVE", "0") == "1"   # kill-switch de despliegue
...
-@app.post("/api/bot/start")
-async def bot_start(mode: str = "paper", exchange: str = "binance", token: str = ""):
-    if mode == "live" and token != _AUTH_TOKEN:
-        return {"error": "Invalid or missing auth token for live mode"}
+@app.post("/api/bot/start", dependencies=[Depends(require_token)])
+async def bot_start(mode: str = "paper", exchange: str = "binance"):
+    if mode == "live" and not _LIVE_ENABLED:
+        raise HTTPException(403, "live disabled on this host (BOTSTRIKE_ALLOW_LIVE!=1)")
...
-@app.post("/api/bot/stop")
-async def bot_stop(token: str = ""):
+@app.post("/api/bot/stop", dependencies=[Depends(require_token)])
+async def bot_stop():
...
-@app.post("/api/backtest/run")
+@app.post("/api/backtest/run", dependencies=[Depends(require_token)])
...
 @app.get("/api/bot/status")
 async def bot_status():
-    return {..., "auth_token": _AUTH_TOKEN, ...}
+    return {..., "live_enabled": _LIVE_ENABLED, ...}      # nunca el token
```
Complementos: (a) `.env.example` + unit: `BOTSTRIKE_API_TOKEN=` y `BOTSTRIKE_ALLOW_LIVE=0`; (b) desktop: campo "Bridge token" en Settings → cabecera `X-BotStrike-Token` (y también para `/ws/*` vía query `?token=` o primer mensaje `auth`); (c) en el CT bindear solo a la IP Tailscale (`BOTSTRIKE_HOST=$(tailscale ip -4)`) y quitar la regla ufw de `192.168.1.0/24`, o al menos restringirla a la IP del PC de Edgar; (d) `docs_url=None, redoc_url=None, openapi_url=None` en `FastAPI(...)` (verificado `/docs` y `/openapi.json` → 200).
**Verificado cómo:** ejecutado (curl arriba) + leído.

### [P1] `/api/health` es un 200 hueco: no refleja engine vivo, WS de Binance ni frescura de datos; nada reinicia el engine si muere dentro del proceso
**Archivo:** `server/bridge.py:781-789`, `server/bridge.py:221-234`, `server/bridge.py:677-694`
**Evidencia:**
```python
@app.get("/api/health")
async def health():
    return {"status": "ok", "engine_running": state.running, "mode": state.mode,
            "uptime_sec": ..., "clients": ...}          # siempre "ok", siempre HTTP 200
```
```python
async def _run_engine():
    try:   await state.engine.start()
    except Exception as e:  await state.channels.broadcast("system", {"type":"engine_error",...})
    finally: state.running = False                     # nadie reacciona a esto
```
`ws_connected` (leído de `engine.websocket._connected`, que sí existe en `exchange/binance_ws.py:47`) solo se emite por WS `system`, no en `/api/health`. No hay `last_tick_age`. `deploy/update.sh:18` y `deploy/verify.sh:30` usan `curl -sf /api/health` como prueba de vida.
**Por qué es un problema:** Con `Restart=always` systemd solo reinicia si el **proceso** muere. Si muere el engine (task asyncio) el proceso sigue vivo, health devuelve 200 "ok" y el bot está parado. Si Binance WS no conecta (DNS, ban de IP, red del CT), `state.running=True` y health "ok" indefinidamente. Monitorización externa (Uptime Kuma, cron) no puede distinguir "vivo" de "operando".
**Fix propuesto:**
```diff
 @app.get("/api/health")
-async def health():
-    return {"status": "ok", ...}
+async def health(response: Response):
+    eng = state.engine
+    ws_ok = bool(eng and getattr(eng.websocket, "_connected", False))
+    task_alive = bool(state.engine_task and not state.engine_task.done())
+    last_tick = max((t.get("timestamp", 0) for t in state._last_tick_ts.values()), default=0)  # rellenar en on_trade_hook
+    tick_age = time.time() - last_tick if last_tick else None
+    expected = bool(os.getenv("BOTSTRIKE_AUTOSTART"))
+    healthy = (not expected) or (state.running and task_alive and ws_ok and tick_age is not None and tick_age < 60)
+    response.status_code = 200 if healthy else 503
+    return {"status": "ok" if healthy else "degraded", "engine_running": state.running,
+            "engine_task_alive": task_alive, "ws_connected": ws_ok, "last_tick_age_sec": tick_age,
+            "mode": state.mode, "exchange": state.exchange, "uptime_sec": ..., "clients": ...}
```
Más un **watchdog interno** en `system_broadcast_loop`: si `BOTSTRIKE_AUTOSTART` está definido y (`not state.running` o `tick_age > 300`) durante N ciclos → `logger.critical(...)`; `os._exit(5)` → systemd reinicia. Alternativa más limpia: `Type=notify` + `WatchdogSec=90` con `sdnotify` (`sd_notify("WATCHDOG=1")` solo cuando healthy).
**Verificado cómo:** leído + ejecutado (`GET /api/health` → 200 con `engine_running:false`).

### [P1] Un crash del engine es invisible en journald: `_run_engine` no hace `logger.error`, solo broadcast por WS
**Archivo:** `server/bridge.py:221-234`
**Evidencia:**
```python
    except Exception as e:
        await state.channels.broadcast("system", {"type": "engine_error", "error": str(e), "timestamp": time.time()})
    finally:
        state.running = False
```
`ChannelManager.broadcast` retorna sin hacer nada si no hay clientes (`bridge.py:74-75`). En el servidor 24/7 normalmente **no hay desktop conectado**.
**Por qué es un problema:** El motivo del crash (p.ej. `ValueError` de config, `aiohttp.ClientConnectorError`, `KeyError` en un símbolo) se pierde. El operador ve "unit active" y `engine_running:false` sin traza. Sin traceback no hay post-mortem.
**Fix propuesto:**
```diff
     except Exception as e:
+        logger.exception("engine_crashed", error=str(e), error_type=type(e).__name__)
+        try: await state.engine.notifier.notify_error("engine", f"{type(e).__name__}: {e}")
+        except Exception: pass
         await state.channels.broadcast("system", {...})
```
**Verificado cómo:** leído.

### [P1] `/api/backtest/run` ejecuta un backtest CPU-bound **síncrono dentro del event loop** del engine → congela trading, risk monitor y heartbeats WS; sin auth → DoS remoto
**Archivo:** `server/bridge.py:977-1054`, `backtesting/backtester.py:653` (`def run(` — síncrono)
**Evidencia:**
```python
@app.post("/api/backtest/run")
async def run_backtest(body: dict = {}):
    ...
    df = pd.read_parquet(parquet_path)         # I/O síncrono
    result = bt.run(df, symbol=symbol, ...)    # CPU-bound, sin await, sin run_in_executor
```
`grep -n "def run" backtesting/backtester.py` → `653:    def run(` (no `async`). El mismo proceso/loop corre `_strategy_loop`, `_risk_monitor_loop`, el WS de Binance (`ping_interval=20`, `binance_ws.py:89`) y los loops de broadcast.
**Por qué es un problema:** Un backtest de 90 días de 1m (~130k barras) bloquea el loop decenas de segundos: no se evalúan SL/TP en paper, el risk monitor no corre, Binance cierra el WS por falta de pong (reconexión + gap de datos), `/api/health` no responde (systemd/monitor lo interpreta como caído). Es invocable por cualquiera de la LAN (ver P0). Verificado que acepta body basura sin auth: `POST /api/backtest/run {"symbol":"../../etc/passwd","bars":"x"}` → 200 (`No data for ../../etc/passwd`). `symbol` no se valida (path traversal a cualquier `*/1m.parquet`). Lo mismo, a menor escala, en `/api/trades` (SQLite síncrono en el loop) y `update_market_data` (parquet síncrono).
**Fix propuesto:**
```diff
+import functools
+_BT_LOCK = asyncio.Lock()
 @app.post("/api/backtest/run", dependencies=[Depends(require_token)])
 async def run_backtest(body: dict = {}):
+    if state.running and os.getenv("BOTSTRIKE_AUTOSTART"):
+        raise HTTPException(409, "backtests disabled while the engine is trading on this host")
+    symbol = body.get("symbol", "BTC-USD")
+    if symbol not in Settings().symbol_names:
+        raise HTTPException(400, f"unknown symbol {symbol!r}")
+    if _BT_LOCK.locked():
+        raise HTTPException(429, "a backtest is already running")
+    async with _BT_LOCK:
+        loop = asyncio.get_running_loop()
+        return await loop.run_in_executor(None, functools.partial(_run_backtest_sync, body))  # mejor ProcessPoolExecutor(1)
```
(mover el cuerpo actual a `_run_backtest_sync`). Preferible `ProcessPoolExecutor` para no competir por el GIL con el engine.
**Verificado cómo:** leído + ejecutado (POST sin auth → 200).

### [P1] `bot_start` construye `settings` (exchange, fees, slippage) y **los descarta**: `start_engine` crea otro `Settings()` y fuerza `use_binance=True`
**Archivo:** `server/bridge.py:811-821`, `server/bridge.py:184-204`
**Evidencia:**
```python
    settings = Settings()
    settings.trading.exchange_venue = exchange
    if exchange == "hyperliquid": settings.trading.maker_fee = 0.00015; ...
    state.exchange = exchange
    await start_engine(mode)                     # <- `settings` no se pasa
...
async def start_engine(mode: str = "paper"):
    settings = Settings()                        # <- nuevo, venue=binance por defecto
    state.engine = BotStrike(settings=settings, dry_run=..., paper=..., use_binance=True)
```
**Por qué es un problema:** `exchange=hyperliquid|strike` es un no-op: el engine siempre opera Binance con fees de Binance, pero `state.exchange`/`/api/bot/status` dicen "hyperliquid". El desktop muestra un venue falso; cualquier validación paper "en Hyperliquid" es en realidad Binance. En live sería operar en el exchange equivocado respecto a lo que el operador cree.
**Fix propuesto:**
```diff
-async def start_engine(mode: str = "paper"):
+async def start_engine(mode: str = "paper", settings: Optional[Settings] = None, exchange: str = "binance"):
-    settings = Settings()
+    settings = settings or Settings()
-    state.engine = BotStrike(settings=settings, dry_run=is_dry_run, paper=is_paper, use_binance=True)
+    state.engine = BotStrike(settings=settings, dry_run=is_dry_run, paper=is_paper,
+                             use_binance=(exchange == "binance"))
...
-    await start_engine(mode)
+    await start_engine(mode, settings=settings, exchange=exchange)
```
Nota para el auditor de exchange: además `Settings().use_testnet=True` por defecto y el bridge no lo toca en `live` → `BinanceWebSocket(use_testnet=True)` (`main.py:74`) y `BinanceClient` base URL testnet (`binance_client.py:100-103`). **"live" desde el bridge = Binance TESTNET** siempre. Coherente con la política "nunca live desde el servidor", pero el nombre engaña.
**Verificado cómo:** leído.

### [P1] El estado paper vive solo en memoria: cada reinicio (systemd, `update.sh`, crash) resetea equity a 1.000 $ y pierde posiciones abiertas sin registrar EXIT
**Archivo:** `execution/paper_simulator.py:197-350` (sin save/load), `main.py:821-846` (`shutdown` no cierra posiciones paper), `server/bridge.py:237-283`, `logging_metrics/logger.py:133-158` (`MetricsCollector` en memoria)
**Evidencia:** `grep -n "pickle\|json.dump\|save\|load\|persist" execution/paper_simulator.py` → 0 resultados. `shutdown()` solo hace `cancel_all()` en live; en paper no liquida ni serializa `paper_sim`. `MetricsCollector.__init__` parte de cero (`_cumulative_pnl=0`, `_equity_curve` vacía). `TradeDBAdapter.on_trade` inserta directamente (`adapter.py:196` → `repo.insert_trade`) ⇒ la DB conserva ENTRY sin su EXIT.
**Por qué es un problema:** El "protocolo de validación paper" (README de deploy) exige continuidad: N días, drawdown, Sharpe. Con `Restart=always`, cada `update.sh` o crash rompe la serie (equity vuelve a 1.000, posiciones abiertas "desaparecen" sin PnL, `sessions` fragmentadas) → métricas de validación inválidas y sesgadas al alza (las posiciones perdedoras abiertas nunca se cierran). En live no aplica (el exchange es la fuente de verdad), pero en live el análogo es la reconciliación al arranque (fuera de este ámbito).
**Fix propuesto:** (1) Al `shutdown()` en paper: cerrar todas las posiciones al último mark price (registra EXIT real con `exit_reason="shutdown"`) **o** serializar `paper_sim` + `risk_manager.current_equity` + acumuladores de `MetricsCollector` a `data/paper_state.json` (atomic write: tmp + `os.replace`) y rehidratar en `start()` si `source=paper` y el snapshot tiene < 24 h. (2) En `start()`, recomponer equity inicial desde la última `sessions.final_equity` de la DB (`repo.get_sessions(source="paper", limit=1)`) en vez de `initial_capital`. (3) `deploy/update.sh`: no reiniciar si hay posiciones paper abiertas (`GET /api/bot/status` → `open_positions>0`) salvo `FORCE=1`.
**Verificado cómo:** leído.

### [P1] `_supervise_tasks`: una task crítica (strategy / risk_monitor / ws_market) que muere 1-3 veces **ni se reinicia ni para el engine** → engine "running" con el loop de trading muerto
**Archivo:** `main.py:222-278` (dominio engine, pero afecta directamente a lo que el bridge reporta como sano)
**Evidencia:**
```python
        restartable_methods = {"metrics": ..., "data_refresh": ...}      # strategy/risk/ws NO están
        ...
                if name in restartable_methods and crash_counts[name] <= max_restarts:
                    ...reinicia
                elif crash_counts[name] > max_restarts:                   # solo a partir del 4º crash
                    self._running = False; ...; return
                # else: nada — la task queda muerta y el while continúa con las restantes
```
`_strategy_loop` (`main.py:419-429`) captura `Exception` internamente, así que es raro; pero `websocket.connect_market()` de `BinanceWebSocket` y `_risk_monitor_loop` pueden terminar por excepciones no cubiertas. Un `return` normal (exc None) también se ignora: si `connect_market` termina, no hay más datos y nadie se entera.
**Por qué es un problema:** `state.running` sigue `True`, health "ok", no hay Telegram (solo `notify_error`), y el bot deja de evaluar riesgo o de recibir ticks. Silencioso durante horas.
**Fix propuesto:** tratar strategy/risk_monitor/ws_market como críticas: al primer crash **o finalización inesperada** (`exc is None` mientras `self._running`) → `logger.critical`, `notify_error`, `self._running=False`, cancelar todas y `raise` para que `_run_engine` lo registre y el watchdog reinicie el proceso. (Cross-ref: auditorías 01/02.)
**Verificado cómo:** leído.

### [P1] `requirements.txt` sin pinning ni lockfile, y `update.sh` reinstala en cada deploy → producción puede romperse en cualquier `git push`; `streamlit`/`plotly` sobran en el servidor y `pyarrow` (imprescindible para parquet) no está declarado
**Archivo:** `requirements.txt:1-14`, `deploy/update.sh:11`, `deploy/install.sh:27`
**Evidencia:**
```
numpy>=1.24.0 / pandas>=2.0.0 / aiohttp>=3.9.0 / websockets>=12.0 / ... / streamlit>=1.30.0 / plotly>=5.18.0 / fastapi>=0.115.0 / uvicorn>=0.34.0 / hyperliquid-python-sdk>=0.22.0 / eth_account>=0.13.0
```
- `py -3.12 -c "import pandas.io.parquet as p; print(type(p.get_engine('auto')).__name__)"` → `PyArrowImpl`; `pip show streamlit` → `Requires: ..., pyarrow, ...`. **`pyarrow` solo llega como dependencia transitiva de streamlit.** Si alguien quita streamlit, `pd.read_parquet` (bridge:1015, downloader:246) revienta.
- `py -3.12 -W always -c "import uvicorn.protocols.websockets.websockets_impl"` → `DeprecationWarning: websockets.legacy is deprecated` (uvicorn 0.34 + websockets 14.1). Con `websockets>=12.0` sin tope, `websockets` 15/16 elimina `legacy` → WS del bridge cae en un futuro `update.sh`.
- `grep` de importadores: `streamlit`/`plotly` solo en `dashboard/**` (no usado por el bridge); `rich` es import opcional de `structlog.dev` (verificado: el bridge importa con `sys.modules['rich']=None`).
- Versiones locales que sí funcionan (3.12): numpy 2.4.3, pandas 2.3.3, aiohttp 3.11.11, websockets 14.1, fastapi 0.115.6, starlette 0.41.3, uvicorn 0.34.0, structlog 25.5.0, pyarrow 23.0.1, PyNaCl 1.6.2, python-dotenv 1.0.1, ta 0.11.0, eth-account 0.13.7.
**Por qué es un problema:** No reproducible: el CT puede quedar con un set de versiones distinto al PC donde se validó. El coste de un `numpy`/`pandas`/`websockets` major nuevo un domingo a las 3 AM es un bot parado (con el P0-1, además, sin que nadie lo note).
**Fix propuesto:** `requirements-server.txt` **pinneado** (o `uv pip compile` → `requirements.lock`), sin streamlit/plotly, con `pyarrow` explícito:
```
numpy==2.4.3
pandas==2.3.3
pyarrow==23.0.1
aiohttp==3.11.11
websockets==14.1
fastapi==0.115.6
starlette==0.41.3
uvicorn==0.34.0
structlog==25.5.0
python-dotenv==1.0.1
ta==0.11.0
pynacl==1.6.2
hyperliquid-python-sdk==<versión local>
eth-account==0.13.7
```
`install.sh`/`update.sh`: `uv pip sync --python .venv/bin/python requirements.lock` (sync elimina lo que sobre). CI: job que instale `requirements.lock` en ubuntu + 3.12 y ejecute `pytest` (ya existe `check-backend` pero con el `requirements.txt` flotante).
**Verificado cómo:** ejecutado (`pip show`, `get_engine`, `-W always`) + leído.

### [P2] `.env.example` no tiene `BINANCE_API_KEY/SECRET`, `HYPERLIQUID_*` ni `BOTSTRIKE_*`, y sus placeholders de Strike son "truthy"
**Archivo:** `.env.example:1-13`, `deploy/install.sh:32-35`, `config/settings.py:153-174`, `main.py:193-198`
**Evidencia:** `.env.example` solo contiene `STRIKE_PUBLIC_KEY=your_ed25519_public_key_hex_64chars`, `STRIKE_PRIVATE_KEY=your_ed25519_private_key_hex_64chars`, URLs Strike comentadas y Telegram comentado. `install.sh` hace `cp .env.example .env` y dice "Edit ... with your API keys (Binance read-only...)". El `.env` real del PC de desarrollo sí tiene `BINANCE_*`, `GEMINI_API_KEY` (ningún módulo activo la usa: `grep -rl "gemini|google.generativeai"` solo en `archive/`) y `TELEGRAM_*`.
```python
# main.py:193-198
has_api_key = (self.settings.api_private_key or os.getenv("BINANCE_API_KEY", ""))
if has_api_key and not self.paper: tasks.append(asyncio.create_task(self.websocket.connect_user()))
```
**Por qué es un problema:** El operador no tiene plantilla de las variables que realmente se usan en el servidor; el placeholder `your_ed25519_private_key...` es una cadena no vacía → en `dry_run`/`live` se intentaría abrir el user-WS con una clave basura. Sin `TELEGRAM_*` el servidor 24/7 no avisa de nada.
**Fix propuesto:** reescribir `.env.example` con **todas** las variables, vacías por defecto y comentadas:
```
# --- Bridge (obligatorio en servidor) ---
BOTSTRIKE_API_TOKEN=            # >=32 chars: python -c "import secrets;print(secrets.token_hex(32))"
BOTSTRIKE_ALLOW_LIVE=0
# --- Binance Futures (paper: vacío o read-only) ---
BINANCE_API_KEY=
BINANCE_API_SECRET=
# --- Hyperliquid (opcional) ---
HYPERLIQUID_PRIVATE_KEY=
HYPERLIQUID_WALLET_ADDRESS=
# --- Strike (legacy, opcional) ---
STRIKE_PUBLIC_KEY=
STRIKE_PRIVATE_KEY=
# --- Telegram (muy recomendado en 24/7) ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```
y en `settings.py` validar formato (hex de 64 chars) antes de considerar "configurada" una clave. Eliminar `GEMINI_API_KEY` del `.env` local (secreto sin uso).
**Verificado cómo:** leído (`.env` local inspeccionado con valores enmascarados).

### [P2] `install.sh` no es robusto ni realmente idempotente: clone SSH sin `known_hosts`, `uv python install || true`, `ufw --force reset` + `apt-get update` en cada deploy, `chrony` inútil dentro de un LXC
**Archivo:** `deploy/install.sh:11-12,18,24,43-50`, `deploy/host_deploy.sh:11-13`
**Evidencia:**
- `install.sh:24` → `git clone -q git@github.com:FomoDonkey/BotStrike.git` como `botstrike` sin `ssh-keyscan github.com >> ~/.ssh/known_hosts` ni `-o StrictHostKeyChecking=accept-new`: en un CT nuevo falla con "Host key verification failed" (no hay TTY para aceptar) → `set -e` aborta en [4/6] con el usuario creado pero sin código ni servicio.
- `install.sh:18` → `uv python install 3.12 ... || true` oculta el fallo; el siguiente `uv venv --python 3.12` muere con un error críptico.
- `host_deploy.sh:11-13` ejecuta `update.sh` **y después** `install.sh` en cada deploy → `apt-get update` en cada deploy y `ufw --force reset` (borra todas las reglas, deshabilita, vuelve a habilitar) con el servicio ya reiniciado: ventana sin firewall y flap de reglas en cada push.
- `install.sh:11-12` instala y habilita `chrony` **dentro** del contenedor: un LXC comparte el reloj del kernel del host y (unprivileged) no tiene `CAP_SYS_TIME` → chronyd no puede ajustar nada (`verify.sh:17` ya lo admite: "LXC usually inherits host clock"). El reloj correcto depende del **host Proxmox**. `exchange/binance_client.py` no compensa `serverTime` ni envía `recvWindow` (grep → 0 resultados) → en live, drift > 1 s = `-1021 Timestamp ... outside of the recvWindow`.
- No fija `TZ`; no crea `~/.ssh` del usuario `botstrike` ni la deploy key; asume `192.168.1.0/24` hardcodeado.
**Por qué es un problema:** Un despliegue desde cero no termina; los re-despliegues tocan firewall/apt innecesariamente; el tiempo (crítico para firmar peticiones y para timestamps de trades) no está garantizado donde se cree.
**Fix propuesto:**
```diff
--- a/deploy/install.sh
+++ b/deploy/install.sh
-apt-get install -y -qq curl ca-certificates git chrony ufw sqlite3 >/dev/null
-systemctl enable --now chrony >/dev/null 2>&1 || true
+apt-get install -y -qq curl ca-certificates git ufw sqlite3 >/dev/null
+timedatectl set-timezone UTC || true
+echo "NOTE: sync the clock on the PROXMOX HOST (chrony/timesyncd); LXC inherits it. Check: timedatectl"
 ...
-su - botstrike -c 'command -v ~/.local/bin/uv >/dev/null || (curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1); ~/.local/bin/uv python install 3.12 >/dev/null 2>&1 || true'
+su - botstrike -c 'command -v ~/.local/bin/uv >/dev/null || (curl -LsSf https://astral.sh/uv/install.sh | sh); ~/.local/bin/uv python install 3.12'
+install -d -m 700 -o botstrike -g botstrike /opt/botstrike/.ssh
+su - botstrike -c 'grep -q github.com ~/.ssh/known_hosts 2>/dev/null || ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts'
+[ -f /opt/botstrike/.ssh/id_ed25519 ] || { echo "!! deploy key missing at /opt/botstrike/.ssh/id_ed25519"; exit 2; }
 ...
-ufw --force reset >/dev/null
-ufw default deny incoming ...
+if ! ufw status | grep -q "^Status: active"; then   # solo la primera vez
+  ufw default deny incoming; ufw default allow outgoing
+  ufw allow from 100.64.0.0/10 to any port 9420 proto tcp   # tailnet only (LAN opcional y explícita: LAN_CIDR=...)
+  ufw allow from 100.64.0.0/10 to any port 22 proto tcp
+  ufw --force enable
+fi
```
y en `host_deploy.sh` no invocar `install.sh` en cada deploy (solo `update.sh` + `verify.sh`); `apt-get update` solo si `INSTALL=1`.
**Verificado cómo:** leído.

### [P2] Unit systemd: `ProtectSystem=full` deja `/opt` escribible (el `ReadWritePaths` es decorativo), `EnvironmentFile` duplica `load_dotenv()` y falla duro si falta `.env`, bind `0.0.0.0`, sin `TZ`, sin `UMask`, sin watchdog
**Archivo:** `deploy/botstrike-bridge.service:13-33`
**Evidencia:**
```ini
EnvironmentFile=/opt/botstrike/app/.env     # sin "-": si falta, la unit no arranca; python-dotenv ya lo carga (settings.py:12)
Environment=BOTSTRIKE_HOST=0.0.0.0
ProtectSystem=full                          # = /usr, /boot, /efi, /etc RO. /opt sigue RW
ProtectHome=read-only                       # HOME de botstrike es /opt/botstrike → no le afecta
ReadWritePaths=/opt/botstrike/app/data /opt/botstrike/app/logs   # sin efecto con "full"
```
**Por qué es un problema:** La app puede escribir en todo `/opt/botstrike/app` (código, `.env`, `.venv`): un bug o un endpoint abusado puede alterar el código o el venv. `EnvironmentFile` mete secretos en el entorno del proceso de systemd (visible con `systemctl show -p Environment` no, pero sí heredado a cualquier subproceso, p.ej. `launch_dashboard()` → `subprocess`). Sin `TZ=UTC` los `datetime.fromtimestamp` (`bridge.py:911-915`) dependen del TZ del CT. `UMask` por defecto 0022 → `data/trade_database.db` y `logs/metrics.jsonl` legibles por cualquier usuario del CT.
**Fix propuesto:**
```diff
 [Unit]
 Description=BotStrike trading bridge (FastAPI :9420) + engine autostart
-After=network-online.target chrony.service
+After=network-online.target
 Wants=network-online.target
+StartLimitIntervalSec=0
 [Service]
-Type=simple
+Type=simple                      # → notify + WatchdogSec=90 cuando el bridge implemente sd_notify
 User=botstrike
 Group=botstrike
 WorkingDirectory=/opt/botstrike/app
-EnvironmentFile=/opt/botstrike/app/.env
+# .env lo carga python-dotenv; no duplicar en el entorno de systemd
 Environment=PYTHONUNBUFFERED=1
-Environment=PYTHONIOENCODING=utf-8
+Environment=PYTHONUTF8=1
+Environment=PYTHONDONTWRITEBYTECODE=1
+Environment=TZ=UTC
-Environment=BOTSTRIKE_HOST=0.0.0.0
+Environment=BOTSTRIKE_HOST=0.0.0.0        # ver P0-2: preferible la IP Tailscale del CT
 Environment=BOTSTRIKE_PORT=9420
 Environment=BOTSTRIKE_AUTOSTART=paper
 Environment=BOTSTRIKE_AUTOSTART_EXCHANGE=binance
+Environment=BOTSTRIKE_ALLOW_LIVE=0
 ExecStart=/opt/botstrike/app/.venv/bin/python -m server.bridge --host ${BOTSTRIKE_HOST} --port ${BOTSTRIKE_PORT}
 Restart=always
 RestartSec=10
 TimeoutStopSec=30
 KillSignal=SIGTERM
+UMask=0077
 NoNewPrivileges=true
 PrivateTmp=true
-ProtectSystem=full
-ProtectHome=read-only
+ProtectSystem=strict
+ProtectHome=true
+PrivateDevices=true
+ProtectKernelTunables=true
+ProtectKernelModules=true
+ProtectControlGroups=true
+RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
+RestrictNamespaces=true
+LockPersonality=true
+MemoryDenyWriteExecute=false     # numpy/pyarrow necesitan W+X en algunas builds
+SystemCallArchitectures=native
+MemoryMax=2G
 ReadWritePaths=/opt/botstrike/app/data /opt/botstrike/app/logs
 LimitNOFILE=65536
```
Con `strict` hay que garantizar que la app **solo** escribe en `data/` y `logs/` (ver lista de rutas al final: es así, salvo `__pycache__` → resuelto con `PYTHONDONTWRITEBYTECODE=1`). Nota: `ProtectHome=true` oculta `/home`, `/root`; `/opt/botstrike` no es afectado. Validar en el CT con `systemd-analyze verify botstrike-bridge.service` y `systemd-analyze security botstrike-bridge`.
**Verificado cómo:** leído (semántica de `ProtectSystem=` según `systemd.exec(5)`).

### [P2] `.gitignore` con huecos: `*.db-wal/-shm` no se ignoran, `events.jsonl` (5 MB, log de hooks) y `desktop/data/` (con una SQLite) están sin trackear en el repo
**Archivo:** `.gitignore:24`, `git status`
**Evidencia:**
```
$ touch data/x.db-wal data/x.db-shm; git check-ignore -v data/x.db-wal data/x.db-shm data/trade_database.db
.gitignore:24:data/*.db   data/trade_database.db        # solo el .db; -wal/-shm NO ignorados
$ git status --porcelain --untracked-files=all
?? BotStrike_Documentacion.pdf  ?? BotStrike_Guia_Simple.pdf  ?? desktop/data/ (trade_database.db, -shm, -wal)  ?? events.jsonl (5.2 MB)
```
**Por qué es un problema:** Un `git add -A` descuidado sube historial de trades (WAL contiene páginas de la DB), un log de 5 MB de tooling y binarios PDF; y en el CT, `git reset --hard` de `update.sh` no los toca pero `git clean` sí borraría datos.
**Fix propuesto:**
```diff
 data/*.db
+data/*.db-wal
+data/*.db-shm
+data/*.db-journal
+data/paper_state.json
+desktop/data/
+events.jsonl
+*.pdf
```
**Verificado cómo:** ejecutado (`git check-ignore`, `git status`).

### [P2] Logs: `Settings.log_file` nunca se escribe, structlog solo se configura al arrancar el engine, ANSI colors hacia journald, access-log por cada poll de `/api/health`
**Archivo:** `logging_metrics/logger.py:22-44`, `config/settings.py:211`, `server/bridge.py:1075-1081`
**Evidencia:** `TradingLogger.__init__` guarda `log_file` pero **solo** escribe `metrics_file` (`_flush_metrics`). `structlog.configure(...ConsoleRenderer()...)` se ejecuta al crear `TradingLogger` (dentro de `BotStrike.__init__`), no al arrancar el bridge → antes del engine, structlog usa su config por defecto (stdout, otro formato). Salida real capturada del bridge:
```
[2m2026-08-29 23:33:43[0m [[32m[1minfo     [0m] [1mbridge_ready ... [36mport[0m=[35m9420[0m
```
(ANSI aunque `stderr.isatty()==False`; `_has_colors` de structlog no depende del TTY). uvicorn `log_level="info"` registra cada request (`GET /api/health` de update.sh/verify.sh/monitor). `deploy/README.md:10` promete logs en `/opt/botstrike/app/logs/` — solo habrá `metrics.jsonl`.
**Por qué es un problema:** `journalctl -u botstrike-bridge` lleno de escapes ANSI (grep/`-p err` no funcionan bien: `verify.sh:39` busca `\[error` en texto con colores), formato inconsistente antes/después del arranque del engine, y ruido de access-log. Sin config de journald puede crecer sin límite si el CT no tiene `SystemMaxUse`.
**Fix propuesto:** en `server/bridge.py` (al importar) configurar structlog **una vez** y no reconfigurar en `TradingLogger`:
```python
import sys, structlog
_json = not sys.stderr.isatty() or os.getenv("BOTSTRIKE_LOG_JSON") == "1"
structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.add_log_level,
    structlog.processors.StackInfoRenderer(), structlog.processors.format_exc_info,
    structlog.processors.JSONRenderer() if _json else structlog.dev.ConsoleRenderer()],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
```
`uvicorn.run(..., access_log=False)` (o filtro que silencie `/api/health`). En el CT: `/etc/systemd/journald.conf.d/botstrike.conf` → `SystemMaxUse=500M`, `MaxRetentionSec=30day`. Quitar `log_file` de `Settings` o implementarlo (RotatingFileHandler 20 MB × 5).
**Verificado cómo:** ejecutado (captura del log del bridge con escapes ANSI) + leído.

### [P2] Todas las respuestas de error son HTTP 200 (`{"error": ...}`) → `curl -f`, monitores y los propios scripts de deploy no detectan fallos
**Archivo:** `server/bridge.py:796,803,805,807,851,874,1012,1024,1054`, `deploy/update.sh:18`, `deploy/verify.sh:30-31`
**Evidencia:** ejecutado: `POST /api/bot/start?mode=live` (sin token) → `HTTP 200 {"error":"Invalid or missing auth token..."}`; `?mode=evil` → 200; `?exchange=evil` → 200; `/api/backtest/run` con basura → 200. `verify.sh` usa `curl -sf`, que solo falla con ≥400.
**Por qué es un problema:** Imposible alertar por código de estado; el desktop tiene que parsear `error` en cada respuesta; los scripts de deploy dan "PASS" ante errores.
**Fix propuesto:** `raise HTTPException(400|401|403|404|409|503, detail=...)` en cada rama de error; `/api/health` → 503 cuando degradado (ver P1).
**Verificado cómo:** ejecutado.

### [P2] WebSocket: sin auth, sin límite de clientes, broadcast secuencial sin timeout → un cliente lento congela todos los broadcasts y acumula tasks
**Archivo:** `server/bridge.py:70-84`, `server/bridge.py:331-335`, `server/bridge.py:538-647`, `server/bridge.py:756-777`
**Evidencia:**
```python
    async def broadcast(self, channel, data):
        for ws in clients:
            try: await ws.send_text(message)     # secuencial; sin wait_for; un peer con TCP window llena bloquea aquí
```
```python
    async def patched_process(symbol, sym_config):
        await original_process(symbol, sym_config)
        asyncio.ensure_future(_broadcast_symbol_state(engine, symbol))   # fire-and-forget sin límite ni excepciones gestionadas
```
`candle_broadcast_loop` envía hasta 500 velas × 4 símbolos cada segundo a cada cliente (la vela en formación cambia cada tick → el dedup casi nunca salta). `/ws/{channel}` acepta a cualquiera (verificado: conexión a `/ws/system` sin credenciales → `pong`).
**Por qué es un problema:** En LAN/tailnet cualquiera puede abrir N sockets y dejar de leer → `send_text` bloquea → `market_broadcast_loop`, `candle_broadcast_loop`, `_broadcast_symbol_state` se atascan; las tasks `ensure_future` crecen sin límite (memoria). El engine no se bloquea directamente (los broadcasts están fuera del loop de trading), pero el proceso sí degrada.
**Fix propuesto:** (1) token en el handshake WS (`?token=` o primer mensaje `{"type":"auth"}` con timeout 5 s); (2) `await asyncio.wait_for(ws.send_text(msg), timeout=1.0)` y desconectar al que falle; (3) `asyncio.gather(*[...], return_exceptions=True)` en vez de secuencial; (4) cap `MAX_WS_CLIENTS=8`; (5) enviar velas incrementales (solo última cerrada + formación) en vez de 500; (6) guardar referencia de las tasks de `ensure_future` en un `set` con `add_done_callback(discard)` y loguear excepciones.
**Verificado cómo:** leído + ejecutado (WS sin auth).

### [P2] SQLite: sin backup, sin checkpoint explícito al parar, WAL/SHM deben copiarse juntos; conexión nueva + `PRAGMA journal_mode=WAL` en cada operación
**Archivo:** `trade_database/repository.py:155-165`, `deploy/*` (no hay backup)
**Evidencia:**
```python
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA synchronous=NORMAL")
```
No existe ningún `wal_checkpoint`, ningún `.backup`, ningún cron. `deploy/README.md` no menciona backup. `main.py:114` abre `data/trade_database.db` relativo al CWD (OK con `WorkingDirectory`).
**Por qué es un problema:** La DB es la única fuente de verdad del historial (el estado paper no persiste, ver P1). Un `rm`/corrupción/fallo de disco del CT = pérdida total. Copiar solo `.db` con WAL activo puede dar una copia inconsistente. `journal_mode=WAL` en cada conexión es una escritura al header en cada llamada (barato, pero innecesario).
**Fix propuesto:** (1) cron diario en el CT como `botstrike`: `sqlite3 /opt/botstrike/app/data/trade_database.db ".backup '/opt/botstrike/backups/trade_database-$(date +%F).db'"` + rotar 30 días + copiar al host/PBS (Proxmox Backup) o `rclone`; (2) en `stop_engine`/`shutdown`: `conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")`; (3) `PRAGMA journal_mode=WAL` solo en `_init_db`; (4) incluir el CT 104 en el backup de Proxmox (vzdump) con horario.
**Verificado cómo:** leído.

### [P3] Shutdown duplicado: `engine.start()`→`finally: shutdown()` y `stop_engine()` repiten `end_session`, `_flush_metrics`, `notify_shutdown`, `notifier.stop()`; `wait_for(timeout=10)` puede cortar el shutdown a medias
**Archivo:** `main.py:216-220`, `main.py:821-846`, `server/bridge.py:250-279`
**Evidencia:** `start()`: `try: await self._supervise_tasks(tasks) except CancelledError: pass finally: await self.shutdown()`. `stop_engine()`: `engine_task.cancel(); await wait_for(engine_task, timeout=10)` y luego **otra vez** `trade_db.end_session(...)`, `trading_logger._flush_metrics()`, `notifier.notify_shutdown(metrics)`, `notifier.stop()`.
**Por qué es un problema:** Dos mensajes de Telegram de shutdown (el segundo se descarta porque la sesión aiohttp ya está cerrada, `telegram.py:684-685`), `insert_session` dos veces (idempotente por `INSERT OR REPLACE`), y si `shutdown()` tarda >10 s (`websocket.stop()`, `client.close()`, drenaje Telegram 5 s) el `wait_for` lo cancela a medias → "Unclosed client session" y sockets abiertos hasta que systemd mata. La secuencia SIGTERM en sí es correcta: uvicorn captura SIGINT/SIGTERM (`uvicorn/server.py: HANDLED_SIGNALS`), cierra conexiones, ejecuta el `lifespan` shutdown → `stop_engine()`; `TimeoutStopSec=30` cubre 10 s + drenajes.
**Fix propuesto:** en `stop_engine` dejar solo `engine._running=False; engine_task.cancel(); await wait_for(engine_task, timeout=25)` y confiar en `engine.shutdown()` (que ya lo hace todo); subir `TimeoutStopSec=40`. Eliminar el bloque duplicado (`bridge.py:258-279`).
**Verificado cómo:** leído (uvicorn signals verificado por `inspect.getsource`).

### [P3] `/docs`, `/redoc`, `/openapi.json` públicos; token de live viaja como query string (queda en access-log/journald)
**Archivo:** `server/bridge.py:743`, `server/bridge.py:800,825`
**Evidencia:** `curl -o /dev/null -w "%{http_code}" http://127.0.0.1:9477/docs` → 200; `/openapi.json` → 200. Access-log capturado: `"POST /api/bot/start?mode=live HTTP/1.1"` (con `&token=...` quedaría registrado).
**Fix propuesto:** `FastAPI(..., docs_url=None, redoc_url=None, openapi_url=None)` cuando `BOTSTRIKE_AUTOSTART`/prod; token en cabecera (ver P0-2).
**Verificado cómo:** ejecutado.

### [P3] `asyncio.create_task(update_market_data())` sin referencia → puede ser recolectado a medias; descarga 90 días en cada `start_engine`
**Archivo:** `server/bridge.py:187`
**Evidencia:** `asyncio.create_task(update_market_data())` sin guardar el `Task` (advertencia explícita en la doc de asyncio). Descarga incremental (`binance_downloader.py:158-171` reanuda desde el último timestamp), así que el coste es bajo tras la primera vez.
**Fix propuesto:** `state.bg_tasks.add(t := asyncio.create_task(...)); t.add_done_callback(state.bg_tasks.discard)`.
**Verificado cómo:** leído.

### [P3] `state.equity = 300.0` hardcodeado (capital real 1.000) y `bridge_ready port=9420` aunque se pase `--port` distinto
**Archivo:** `server/bridge.py:122`, `server/bridge.py:731`
**Evidencia:** `/api/bot/status` antes de arrancar → `"equity":300.0`; log del bridge en :9477 → `bridge_ready port=9420`.
**Fix propuesto:** `self.equity = Settings().trading.initial_capital`; loguear `args.port`.
**Verificado cómo:** ejecutado.

### [P3] `automation/` y `scripts/install_collector_service.py` / `run_collector.bat` son 100 % Windows (Task Scheduler, `.ps1`, `.bat`, `%APPDATA%`) y apuntan a `archive.data.collector`
**Archivo:** `automation/collector_supervisor.ps1`, `automation/install_task.ps1`, `automation/*.bat`, `scripts/install_collector_service.py:20-30`, `main.py:996-1044`
**Evidencia:** supervisor con reinicio cada 30 s, fichero `STOP_COLLECTOR`, `Register-ScheduledTask`; `main.py --collect-data` importa `archive.data.collector.StrikeDataCollector` (Strike, no Binance).
**Por qué es un problema:** No rompe el servidor (no se invocan), pero confunden al operador: no hay equivalente Linux (systemd timer) ni se documenta que están obsoletos; en el CT el "watchdog" es solo `Restart=always` (ver P1 health).
**Fix propuesto:** mover `automation/` y `scripts/install_collector_service.py` a `archive/windows_automation/`; documentar en `deploy/README.md` que en Linux el supervisor es systemd + watchdog del bridge.
**Verificado cómo:** leído.

### [P3] `/api/backtest/run` acepta `symbol` arbitrario → path traversal limitado a `<symbol>/1m.parquet`
**Archivo:** `server/bridge.py:1004-1010`
**Evidencia:** `parquet_path = os.path.join(data_dir, symbol, "1m.parquet")`; ejecutado con `symbol="../../etc/passwd"` → intenta `data/binance/klines/../../etc/passwd/1m.parquet` (no existe → 200 con error). Solo lee ficheros llamados `1m.parquet` parseables por pyarrow → impacto bajo.
**Fix propuesto:** validar `symbol in Settings().symbol_names` (incluido en el diff del P1 backtest).
**Verificado cómo:** ejecutado.

### [P3] Placeholder/valores de `.env` parseados por dos gramáticas (systemd `EnvironmentFile` y python-dotenv) y `Settings.__post_init__` lanza `ValueError` → bucle de reinicio cada 10 s sin alerta
**Archivo:** `deploy/botstrike-bridge.service:13`, `config/settings.py:12,222-242`
**Evidencia:** `load_dotenv()` no sobreescribe variables ya presentes; si systemd interpretó una comilla/`#`/`\` distinto, gana la versión de systemd. `Settings()` se instancia al importar `server.bridge` (`bridge.py:36` + `Settings()` en `update_market_data`/`start_engine`), y `__post_init__` lanza si `max_position_usd` es incoherente → el proceso muere en el arranque → `Restart=always` cada 10 s indefinidamente (sin `StartLimit` que lo frene, y sin Telegram porque el notifier vive dentro del engine).
**Fix propuesto:** quitar `EnvironmentFile` (P2 unit); en `main()` del bridge envolver el arranque en `try/except` que loguee `critical` y salga con código 3, y un `OnFailure=botstrike-alert@%n.service` (unit que envía Telegram con `curl`) para enterarse de crash-loops.
**Verificado cómo:** leído.

### [P3] `update.sh` no comprueba estado real tras el reinicio ni hace rollback; `host_deploy.sh` espera 25 s fijos
**Archivo:** `deploy/update.sh:16-18`, `deploy/host_deploy.sh:14-16`
**Evidencia:** `systemctl restart ...; sleep 6; curl -sf localhost:9420/api/health && ... status`. Con health hueco (P1) siempre "pasa". No guarda el commit anterior para volver atrás.
**Fix propuesto:** `PREV=$(git rev-parse HEAD)` antes del reset; tras reiniciar, `bash deploy/verify.sh || { git reset --hard $PREV; uv pip sync ...; systemctl restart ...; exit 1; }`; polling de health (hasta 60 s) en vez de `sleep` fijo.
**Verificado cómo:** leído.

### [P3] CI no valida los artefactos de deploy (`shellcheck`, `systemd-analyze verify`) ni el requirements pinneado
**Archivo:** `.github/workflows/ci.yml` (`check-backend`)
**Evidencia:** el job instala `requirements.txt` flotante, importa `server.bridge` y corre `pytest tests/`; `deploy/**` no está en `paths:`.
**Fix propuesto:** añadir `deploy/**` a `paths`, step `shellcheck deploy/*.sh`, `systemd-analyze verify deploy/botstrike-bridge.service` (ubuntu tiene systemd), y usar `requirements.lock`.
**Verificado cómo:** leído.

### [P3] Telegram: OK en lo esencial (token no aparece en logs; cola con descarte logueado; rate-limit), pero sin alerta de "crash-loop"/"engine parado" fuera del engine
**Archivo:** `notifications/telegram.py:94-123, 682-712`
**Evidencia:** `logger.error("telegram_send_error", error=str(e))` — `str()` de `ClientConnectorError`/`TimeoutError` no incluye la URL con el token; no se usa `raise_for_status` (que sí incluiría `url`). `notify_error` solo se invoca desde `_supervise_tasks`.
**Fix propuesto:** que el **bridge** (no solo el engine) tenga un notifier propio para `engine_crashed`, `autostart_failed`, `health degraded > 5 min`; y `OnFailure=` en systemd (ver arriba).
**Verificado cómo:** leído.

---

## (a) Tabla resumen

| # | Sev | Hallazgo | Archivo principal | Verificado |
|---|-----|----------|-------------------|------------|
| 1 | **P0** | **CORREGIDO c18bb32** — `BOTSTRIKE_AUTOSTART` no implementado → engine nunca arrancaba solo | `server/bridge.py:716-741`, `deploy/botstrike-bridge.service:19` | ejecutado |
| 2 | **P0** | **CORREGIDO c18bb32** (token desde .env, solo expuesto en loopback) — residual: start/stop paper, backtest y WS sin auth | `server/bridge.py:40,799-845` | ejecutado |
| 3 | P1 | `/api/health` 200 hueco; nada reinicia el engine si muere dentro del proceso; sin watchdog | `server/bridge.py:781-789,221-234` | ejecutado |
| 4 | P1 | Crash del engine no se loguea (solo broadcast WS) | `server/bridge.py:227-232` | leído |
| 5 | P1 | `/api/backtest/run` síncrono en el event loop → congela trading; DoS sin auth; `symbol` sin validar | `server/bridge.py:977-1054` | ejecutado |
| 6 | P1 | `bot_start` descarta `settings` (exchange/fees); `use_binance=True` fijo; "live" = testnet | `server/bridge.py:811-821,184-204` | leído |
| 7 | P1 | Estado paper solo en memoria → cada reinicio resetea equity y pierde posiciones abiertas | `execution/paper_simulator.py`, `main.py:821-846` | leído |
| 8 | P1 | `_supervise_tasks` deja tasks críticas muertas sin reiniciar ni parar | `main.py:222-278` | leído |
| 9 | P1 | `requirements.txt` sin pin; streamlit/plotly en servidor; `pyarrow` implícito; `websockets.legacy` deprecado | `requirements.txt`, `deploy/update.sh:11` | ejecutado |
| 10 | P2 | `.env.example` incompleto (sin BINANCE/BOTSTRIKE_*), placeholders truthy, `GEMINI_API_KEY` sin uso | `.env.example`, `deploy/install.sh:32-35` | leído |
| 11 | P2 | `install.sh`: sin known_hosts, `uv … \|\| true`, `ufw reset`+apt en cada deploy, chrony inútil en LXC, sin TZ | `deploy/install.sh`, `deploy/host_deploy.sh` | leído |
| 12 | P2 | Unit: `ProtectSystem=full` deja /opt RW; `EnvironmentFile` duplicado; sin `TZ`/`UMask`/watchdog | `deploy/botstrike-bridge.service` | leído |
| 13 | P2 | `.gitignore`: `-wal/-shm`, `events.jsonl`, `desktop/data/`, PDFs | `.gitignore:24` | ejecutado |
| 14 | P2 | Logs: `log_file` nunca escrito, ANSI a journald, structlog configurado tarde, access-log ruidoso | `logging_metrics/logger.py:22-44` | ejecutado |
| 15 | P2 | Errores siempre HTTP 200 | `server/bridge.py` (varias) | ejecutado |
| 16 | P2 | WS sin auth/límite; broadcast secuencial sin timeout; tasks fire-and-forget sin cap | `server/bridge.py:70-84,331-335` | ejecutado |
| 17 | P2 | SQLite sin backup ni checkpoint; WAL en cada conexión | `trade_database/repository.py:155-165` | leído |
| 18 | P3 | Shutdown duplicado engine+bridge; `wait_for(10s)` corta a medias | `server/bridge.py:250-279` | leído |
| 19 | P3 | `/docs` públicos; token por query string | `server/bridge.py:743,800` | ejecutado |
| 20 | P3 | `create_task` sin referencia | `server/bridge.py:187` | leído |
| 21 | P3 | `equity=300` hardcodeado; log `port=9420` fijo | `server/bridge.py:122,731` | ejecutado |
| 22 | P3 | `automation/` y collector scripts 100 % Windows / obsoletos | `automation/*`, `scripts/install_collector_service.py` | leído |
| 23 | P3 | Path traversal limitado en backtest `symbol` | `server/bridge.py:1004` | ejecutado |
| 24 | P3 | Doble gramática `.env`; `Settings()` lanza al importar → crash-loop sin alerta | `config/settings.py:12,222` | leído |
| 25 | P3 | `update.sh` sin rollback ni verificación real | `deploy/update.sh` | leído |
| 26 | P3 | CI no valida `deploy/` | `.github/workflows/ci.yml` | leído |
| 27 | P3 | Telegram OK, pero sin alertas desde el bridge/systemd | `notifications/telegram.py` | leído |

**Totales:** P0 = 2 (ambos **corregidos en c18bb32**, con residual P1 en el #2) · P1 = 7 · P2 = 8 · P3 = 10 · **27 hallazgos**.

**Lo que está bien (verificado):** SQLite en WAL + `synchronous=NORMAL` + inserts directos (no se pierden trades en crash); `TradeRepository` crea `data/` si falta; `stop()`/SIGTERM de uvicorn llega al `lifespan` y a `stop_engine`; CORS con regex estricta para navegadores; canales WS desconocidos rechazados (403); `ping`/`pong` funciona; `check_daily_reset_safe` usa UTC; Telegram no filtra el token en logs; `BinanceWebSocket` reconecta con backoff exponencial (1→30 s) y expone `_connected`; `.env` y `logs/` sí están en `.gitignore`; `.gitattributes` fuerza LF en `*.sh`/`*.service`; `binance_downloader` reanuda incrementalmente; ningún path `C:\`/`.exe`/`py -3` en el camino del bridge (todo `os.path.join` relativo a `__file__`/CWD); `import server.bridge` en Python 3.12 OK en 1,3 s; CI ya prueba en ubuntu + 3.12.

---

## (b) Checklist de despliegue Linux (operador) — orden recomendado

**Pre-requisitos (una vez, en el PC / host Proxmox)**
1. [x] P0-1 y P0-2 corregidos en `c18bb32`. [ ] Pendiente: token obligatorio también en start/stop paper, backtest y WS (residual P0-2), `BOTSTRIKE_ALLOW_LIVE=0`, y health con 503 + watchdog (P1-3). Sin esto NO considerar el 24/7 "desatendido".
2. [ ] Crear `requirements.lock` pinneado (P1-9) y hacer que `install.sh`/`update.sh` usen `uv pip sync`.
3. [ ] Regenerar `.env.example` completo (P2-10). Generar el token: `python -c "import secrets;print(secrets.token_hex(32))"`.
4. [ ] Push a `origin/main` (CI verde).
5. [ ] **Host Proxmox:** `timedatectl status` → NTP synchronized = yes (chrony/timesyncd en el HOST, no en el CT). `ssh root@100.68.139.93 'timedatectl'`.
6. [ ] Host: incluir CT 104 en vzdump/PBS (diario) o al menos snapshot antes de cada deploy: `pct snapshot 104 pre-deploy-$(date +%F)`.

**Dentro del CT 104 (`pct exec 104 -- bash`, verificar `hostname` = botstrike)**
7. [ ] `timedatectl set-timezone UTC`; comprobar `date -u` vs `curl -s https://fapi.binance.com/fapi/v1/time` (drift < 500 ms).
8. [ ] Usuario + deploy key: `id botstrike`; `/opt/botstrike/.ssh/id_ed25519` (600) + `known_hosts` con `ssh-keyscan -t ed25519 github.com`. Probar: `su - botstrike -c 'ssh -T git@github.com'` → "successfully authenticated".
9. [ ] `bash /opt/botstrike/app/deploy/install.sh` (versión corregida, solo primera vez). Verificar cada paso imprime OK y que termina (`echo $?` = 0).
10. [ ] `/opt/botstrike/app/.env`: rellenar `BOTSTRIKE_API_TOKEN`, `BINANCE_API_KEY/SECRET` (read-only para paper), `TELEGRAM_BOT_TOKEN/CHAT_ID`. `chmod 600`, `chown botstrike:botstrike`. `BOTSTRIKE_ALLOW_LIVE=0`.
11. [ ] `systemd-analyze verify /etc/systemd/system/botstrike-bridge.service` → sin errores; `systemd-analyze security botstrike-bridge` → revisar exposición.
12. [ ] `mkdir -p /etc/systemd/journald.conf.d && printf '[Journal]\nSystemMaxUse=500M\nMaxRetentionSec=30day\n' > /etc/systemd/journald.conf.d/botstrike.conf && systemctl restart systemd-journald`.
13. [ ] `systemctl daemon-reload && systemctl enable --now botstrike-bridge && sleep 20 && systemctl status botstrike-bridge --no-pager`.
14. [ ] Health real: `curl -s -w '\n%{http_code}\n' localhost:9420/api/health` → **200** con `engine_running:true`, `ws_connected:true`, `last_tick_age_sec < 10`. Si 503 → `journalctl -u botstrike-bridge -n 200 --no-pager`.
15. [ ] `journalctl -u botstrike-bridge --since -2min | grep -E 'binance_ws_connected|engine_autostarted|klines_'` → deben aparecer.
16. [ ] Auth: `curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:9420/api/bot/stop` → **401**; con `-H "X-BotStrike-Token: $TOKEN"` → 200. `curl -s localhost:9420/api/bot/status | grep -c auth_token` → **0**. `curl -s -o /dev/null -w '%{http_code}\n' localhost:9420/docs` → 404.
17. [ ] Firewall: `ufw status numbered` → solo 9420/22 desde `100.64.0.0/10` (y LAN solo si se decide explícitamente). Desde el PC: `curl -s http://<ip-tailscale-ct>:9420/api/health`.
18. [ ] Datos: `ls -la /opt/botstrike/app/data/binance/klines/*/1m.parquet` (4 símbolos), `ls -la /opt/botstrike/app/data/trade_database.db*`, `ls -la /opt/botstrike/app/logs/metrics.jsonl`. Todo propiedad de `botstrike`, permisos 600 (UMask).
19. [ ] Backup DB: cron `0 3 * * * botstrike sqlite3 /opt/botstrike/app/data/trade_database.db ".backup /opt/botstrike/backups/tdb-$(date +\%F).db"` y prueba manual de restore (`sqlite3 copia.db "select count(*) from trades"`).
20. [ ] Reinicio limpio: `systemctl restart botstrike-bridge` → en journal: `botstrike_shutting_down`, `trade_db_session_ended`, `telegram_notifier_stopped`, sin "Unclosed client session"; tras 30 s health 200 otra vez; Telegram recibe startup/shutdown.
21. [ ] Prueba de crash: `kill -9 $(systemctl show -p MainPID --value botstrike-bridge)` → `NRestarts` +1 y health 200 en < 60 s (`systemctl show -p NRestarts --value botstrike-bridge`).
22. [ ] Prueba de engine muerto (tras fix watchdog): `curl -X POST -H "X-BotStrike-Token: $TOKEN" localhost:9420/api/bot/stop` → con `BOTSTRIKE_AUTOSTART=paper` el watchdog debe reiniciar el proceso y el engine vuelve solo (o documentar que stop manual desactiva el autostart hasta el próximo restart).
23. [ ] Monitor externo (Uptime Kuma en otro CT, o cron en el host): `GET http://<ct>:9420/api/health` cada 60 s, alerta si ≠ 200 durante 3 min. Adicional: `OnFailure=` unit que manda Telegram.
24. [ ] Deploy posterior: `bash deploy/remote_deploy.sh` (desde Git Bash, nunca PowerShell) → `verify.sh` PASS; snapshot previo del CT; no desplegar con posiciones paper abiertas salvo `FORCE=1`.
25. [ ] Documentar en `deploy/README.md`: qué hace el token, cómo rotarlo (`.env` + `systemctl restart`), política "live nunca desde el CT" (`BOTSTRIKE_ALLOW_LIVE=0`), y dónde están backups.

---

## (c) Rutas que la app escribe en runtime (base `/opt/botstrike/app`, `WorkingDirectory`) — para `ReadWritePaths`

| Ruta | Quién | Cuándo | Notas |
|------|-------|--------|-------|
| `data/trade_database.db`, `data/trade_database.db-wal`, `data/trade_database.db-shm` | `trade_database/repository.py:109-165` (vía `main.py:114`) | al crear `BotStrike` y en cada trade/sesión | relativo al CWD; WAL |
| `data/binance/klines/<SYMBOL>/1m.parquet` (4 símbolos) | `data/binance_downloader.py:154-246` vía `server/bridge.py:132-176` | en cada `start_engine` (incremental) | ruta absoluta derivada de `__file__` |
| `data/binance/trades/<SYMBOL>/*.parquet` | `data/binance_downloader.py:288-412` | solo `main.py --download-binance` (no en el bridge) | opcional |
| `data/` (mkdir) | `repository.py:111`, `binance_downloader.py:155` | arranque | `install.sh` ya lo crea |
| `logs/metrics.jsonl` y `logs/metrics.jsonl.old` | `logging_metrics/logger.py:112-128` | cada 10 métricas; rota a 50 MB | único log en disco |
| `logs/` (mkdir) | `logging_metrics/logger.py:27` | al crear `TradingLogger` | |
| `logs/backtest_<SYMBOL>_<ts>.jsonl` | `backtesting/backtester.py:717-738` (`RealisticBacktester`) | solo CLI de backtest realista (no `/api/backtest/run`, que usa `Backtester`) | opcional |
| `**/__pycache__/*.pyc` | CPython | al importar | evitar con `PYTHONDONTWRITEBYTECODE=1` o precompilar en `update.sh` |
| `/tmp` (aiohttp/pyarrow temporales) | libs | esporádico | cubierto por `PrivateTmp=true` |
| `data/paper_state.json` (propuesto P1-7) | futuro | shutdown | atomic write |
| `/opt/botstrike/backups/*.db` (propuesto P2-17) | cron | diario | fuera del árbol del repo |

**No escribe** en: `.env` (solo lectura por dotenv), `data/catalog.json` / `data/metadata.json` (solo lectura en `bridge.py:940-953`; los escritores están en `archive/`), `logs/botstrike.log` (declarado en `Settings.log_file` pero nunca usado), ni fuera de `/opt/botstrike/app` (salvo `/tmp`).

→ `ReadWritePaths=/opt/botstrike/app/data /opt/botstrike/app/logs` es **correcto y suficiente** con `ProtectSystem=strict` + `PYTHONDONTWRITEBYTECODE=1` (+ `/opt/botstrike/backups` si el backup corre bajo la misma unit).

---

## Notas de método
- Ejecutado en Windows con `py -3.12` (3.12.10): `import server.bridge` OK; bridge arrancado en `127.0.0.1:9477` durante la auditoría y matado después (verificado `connection refused` tras la prueba). El engine **no** se arrancó (habría escrito DB/logs del proyecto).
- No se ha modificado ningún archivo del proyecto salvo este informe. Durante la sesión aparecieron `deploy/host_deploy.sh`, `deploy/remote_deploy.sh`, `deploy/verify.sh` sin trackear (creados por otro proceso); se han auditado tal cual.
- Pendiente de verificar **en el CT real** (no reproducible en Windows): `systemd-analyze verify/security`, comportamiento de `ufw` en LXC unprivileged, `chronyc tracking`, SIGTERM end-to-end con journald.
