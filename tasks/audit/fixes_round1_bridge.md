# Fixes round 1 — bridge/deploy (auditoría 03, P1) — progreso

Fecha: 2026-08-30 · Ámbito tocado: `server/bridge.py`, `deploy/botstrike-bridge.service`, `deploy/update.sh`,
`requirements.txt`, `tests/test_bridge_round2.py` (nuevo). Sin commit.
Baseline antes de tocar nada: `py -3.12 -m pytest tests/ -q -p no:cacheprovider` → 36 passed.

## Estado
- [x] 1 Health real: `/api/health` devuelve `status/degraded/reasons/version/engine_running/engine_expected/
      engine_task_alive/ws_connected/last_tick_age_sec/autostart/mode/exchange/uptime_sec/clients`.
      HTTP 503 (mismo JSON, `status=degraded`) si `engine_expected and not engine_running`, o engine running con
      `last_tick_age_sec > 120` (o sin ningún tick pasados 120 s desde el arranque del engine).
      `ws_connected` = `engine.websocket._connected` (existe en `exchange/binance_ws.py:47` y en `hyperliquid_ws.py:38`).
      `last_tick_age_sec` = min(edad del último tick raw visto por el hook del bridge, edad de `market_data._last_data_time`).
      `version` = `BRIDGE_VERSION = "2.12.1"` (único sitio; también `FastAPI(version=...)`).
- [x] 2 Watchdog crash-only (solo con `BOTSTRIKE_AUTOSTART`): `_restart_engine_after_failure` → backoff
      10/30/60/60/60 s, máx 5 intentos por ventana de 600 s; agotado → `os._exit(3)` (systemd `Restart=always`).
      `_engine_watchdog_loop` cada 30 s: engine no running → restart inmediato; ticks > 300 s en 3 comprobaciones
      seguidas → restart. `engine_autostart_failed` también entra en el mismo camino.
      Decisión: `POST /api/bot/stop` (operador) pone `engine_expected=False` → health 200 con `engine_running=false`
      y el watchdog NO resucita el engine hasta el próximo start o reinicio del servicio (log `engine_stopped_by_operator`).
      El watchdog solo reinicia en el modo de autostart (paper/dry_run), nunca live.
- [x] 3 Backtest fuera del event loop: `/api/backtest/run` → `await asyncio.to_thread(_run_backtest_sync, body)`;
      `symbol` validado contra `Settings().symbol_names` (400); 1 backtest concurrente (409); `bars` saneado.
- [x] 4 Auth residual: dependencia `require_token_when_remote` en `/api/bot/start`, `/api/bot/stop`, `/api/backtest/run`:
      con bind no-loopback (`_EXPOSE_TOKEN=False`) exige `token` (query) o `X-BotStrike-Token` (header) en todos los
      modos → 401. En loopback sin cambios (paper sin token). Live sigue exigiendo token en cualquier bind (ahora 401,
      antes 200 `{error}`); mode/exchange inválidos → 400. `/docs`, `/redoc`, `/openapi.json` → 404 si no es loopback.
- [x] 5 Crash visible: `_run_engine` hace `logger.exception("engine_crashed", ...)` (traceback a stderr/journald) +
      broadcast; un return "normal" de `engine.start()` mientras el engine sigue esperado → `engine_exited_unexpectedly`
      + restart. Hook sobre `engine._supervise_tasks` (sin tocar main.py): done-callbacks en ws_market/strategy/
      risk_monitor → si mueren o terminan con el engine `_running` → `logger.critical` + `engine._running=False`
      → `start()` termina → `_run_engine` lo reporta → watchdog reinicia.
- [x] 6 `bot_start` pasa `settings` (`_build_settings(exchange)`) a `start_engine(mode, settings=None)`;
      `use_binance=True` intacto.
- [x] 7 `requirements.txt`: `pyarrow>=17.0.0` (local: pyarrow 23.0.1 / pandas 2.3.3; compatible con pandas 3.x;
      último en PyPI 25.0.1). `deploy/update.sh`: `uv pip sync requirements.lock` si existe, si no `uv pip install -r`.
      Lock NO generado (se hará en el CT).
- [x] 8 Unit systemd: `StartLimitIntervalSec=600` + `StartLimitBurst=10` en [Unit]; `PYTHONDONTWRITEBYTECODE=1`;
      `ReadWritePaths` revisado contra la lista de la auditoría (§c): data/ y logs/ son suficientes → sin cambios,
      comentario añadido. LF conservado.
- [x] Tests: `tests/test_bridge_round2.py` 22 tests (health 503/200, auth 401/200, docs 404, backtest to_thread/400/409,
      watchdog tick/backoff/os._exit(3)/éxito, crash logging, critical task callback, stop manual). Suite: 92 passed.
- [x] `py -3.12 -c "import server.bridge"` OK.

## Verificación real (2026-08-30)
Bridge arrancado con `BOTSTRIKE_AUTOSTART=paper --host 127.0.0.1 --port 9432`, `curl /api/health` a t+27 s:
```
{"status":"ok","degraded":false,"reasons":[],"version":"2.12.1","engine_running":true,"engine_expected":true,
 "engine_task_alive":true,"ws_connected":true,"last_tick_age_sec":0.017,"autostart":"paper","mode":"paper",
 "exchange":"binance","uptime_sec":25.6,"clients":0}   HTTP 200
```
Log: `bridge_ready version=2.12.1` → `engine_autostarted mode=paper` → `binance_ws_connected streams=16`.
Segunda instancia `--host 0.0.0.0 --port 9433` (sin autostart, `BOTSTRIKE_AUTH_TOKEN` fijado):
health 200 (`engine_expected:false`), `/docs` 404, `/openapi.json` 404, `POST start?mode=paper` 401,
`POST stop` 401, `POST backtest` 401, backtest con token y `symbol="../../etc/passwd"` → 400,
`/api/bot/status` → `auth_token:null`. Ambos procesos matados después (puertos cerrados verificados).

## Pendiente / fuera de alcance de esta ronda
- `requirements.lock` se genera en el CT (`uv pip compile requirements.txt -o requirements.lock`).
- Desktop (`desktop/src/lib/api.ts`) no envía token: con el bridge en loopback no cambia nada; contra el CT
  (bind 0.0.0.0) necesitará cabecera `X-BotStrike-Token` para start/stop/backtest (fuera de mi ámbito).
- Prueba end-to-end del watchdog (matar el WS de Binance / agotar 5 intentos) solo en el CT: cubierto por unit tests.
- P1 restantes de la auditoría no incluidos aquí: estado paper persistente (#7), `_supervise_tasks` en main.py (#8,
  mitigado desde el bridge), pinning completo (#9, parcial).
