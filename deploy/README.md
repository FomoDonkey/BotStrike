# Despliegue BotStrike en servidor Linux (Proxmox LXC)

Producción actual: **CT 104 `botstrike`** en `proxmox-mizu` — Debian 13, IP LAN `192.168.1.204`, Tailscale.

| Qué | Dónde |
|---|---|
| Código | `/opt/botstrike/app` (clon de `main` con deploy key read-only) |
| Python | `/opt/botstrike/app/.venv` (Python 3.12 vía `uv`) |
| Servicio | `systemctl status botstrike-bridge` (bridge FastAPI :9420 + engine paper autostart) |
| Logs | `journalctl -u botstrike-bridge -f` y `/opt/botstrike/app/logs/` |
| Datos | `/opt/botstrike/app/data/` (parquet klines, `trade_database.db`) |
| Secretos | `/opt/botstrike/app/.env` (600, usuario botstrike) |

## Operación
```bash
# desde el host Proxmox
pct exec 104 -- systemctl status botstrike-bridge
pct exec 104 -- journalctl -u botstrike-bridge -n 100 --no-pager
pct exec 104 -- bash /opt/botstrike/app/deploy/update.sh     # desplegar último main
pct exec 104 -- curl -s localhost:9420/api/health
```

## Modo
`BOTSTRIKE_AUTOSTART=paper` en la unit: al arrancar el servicio el engine entra en paper automáticamente
(datos reales Binance Futures, fills simulados). Para **live** NO se usa autostart: se arranca desde el
desktop con token (`/api/bot/start?mode=live&token=...`) y sólo tras el protocolo de validación paper.

## Acceso desde el desktop (BotStrike ≥ 2.12.0)
En la app: **Settings → Connection** (también accesible desde el overlay inicial con "change" o desde
"Bridge unreachable → Connection settings"):

1. **Bridge URL** = `192.168.1.204:9420` (LAN) o la IP Tailscale del CT (`100.x.y.z:9420`). Se acepta
   `host`, `host:puerto` o `http(s)://host:puerto`; sin puerto se asume 9420. La app muestra el badge
   **LOCAL** (loopback: arranca el engine empaquetado) o **REMOTE** (no toca ningún proceso local).
2. **Auth token**: con el bridge escuchando en `0.0.0.0` el token NO se expone en `/api/bot/status`,
   así que hay que pegarlo a mano. Obtenerlo desde el host Proxmox:
   ```bash
   pct exec 104 -- grep AUTH_TOKEN /opt/botstrike/app/.env
   ```
   El desktop lo envía como `?token=` en `/api/bot/start`, `/api/bot/stop` y `/api/backtest/run`
   (obligatorio en remoto; en local sólo para LIVE). Sin token válido el bridge responde 401 y la app
   muestra una alerta roja ("Start failed: …") en vez de fallar en silencio.
3. **Test connection** → hace `GET /api/health` contra la URL del campo (sin guardar) y muestra
   `ok · engine running/stopped · paper · N ms` o el error (timeout 4 s, CORS, 401…).
4. **Save & reconnect** → persiste URL + token (localStorage) y reabre los 5 canales WebSocket contra la
   nueva URL. El TopBar pasa a `REMOTE` con el punto verde si el bridge responde.

El puerto 9420 sólo está abierto (ufw) para la LAN `192.168.1.0/24` y la tailnet `100.64.0.0/10`.
Si la app dice "Bridge unreachable": comprobar `pct exec 104 -- curl -s localhost:9420/api/health`,
el ufw y que el PC esté en la LAN o en la tailnet.

## v2.14 — configuración en caliente, trend diario, interés compuesto

- **Todo se edita desde la UI** (Settings / Strategies). La UI habla con `GET /api/config/schema`
  (campos editables con tipo/límites/ayuda) y `PUT /api/config` (parche parcial, token
  `X-BotStrike-Token` obligatorio fuera de loopback). Los cambios se aplican EN CALIENTE al engine y
  se guardan en **`data/config_overrides.json`** (no versionado; sobrevive a `git reset --hard` del
  deploy). Los campos marcados `restart_required` (capital inicial, vol targeting, Kelly, venue…)
  piden "Restart engine" (`POST /api/bot/restart`). `POST /api/config/reset` borra el fichero.
- **TREND_DAILY** (`strategies/trend_daily.py`): motor de cadencia diaria, REST a Binance SPOT
  (`api.binance.com`, sin API key), cache en **`data/binance_daily/*.parquet`**, libro persistente en
  **`data/trend_daily_state.json`**. Ejecuta a las 00:05 UTC (configurable); al arrancar después de
  esa hora ejecuta el día en curso. `POST /api/trend/run` fuerza la decisión del día; `GET /api/trend`
  muestra universo, pesos, posiciones y tracking. **Un restart/deploy NO cierra el libro** (solo el halt
  por drawdown máximo lo aplana).
- **Interés compuesto** (`trading.compounding_enabled`, por defecto ON): el sizing usa el equity
  histórico (capital inicial + PnL realizado de la DB + PnL abierto). El pico de equity y las pérdidas
  del día/semana se reconstruyen desde la DB al arrancar (`risk/persistence.py`): la escalera
  −2 % día / −5 % semana / −10 % desde máximo ya no se reinicia con cada deploy.
- **Edge monitor** (`analytics/edge.py`, `GET /api/edge`): estadísticas por estrategia sobre los
  últimos N cierres; kill automático (sin nuevas entradas + aviso Telegram) si t-stat ≤ −2 con ≥ 100
  trades o si las comisiones se comen ≥ 50 % del bruto de las ganadoras. Se levanta solo si mejora.
- **Microestructura** apagada por defecto (`trading.microstructure_enabled`); Telegram con
  interruptores por tipo de mensaje, reintento con backoff y digest diario.
- Comprobación rápida tras un deploy:
  ```
  pct exec 104 -- curl -s localhost:9420/api/health      # version 2.14.0, trend_daily_enabled
  pct exec 104 -- curl -s localhost:9420/api/trend       # last_run_status ok, positions
  pct exec 104 -- curl -s localhost:9420/api/risk        # peak_equity, daily/weekly limits
  pct exec 104 -- ls -la /opt/botstrike/app/data/        # config_overrides.json, trend_daily_state.json
  ```

## v2.15 — terminal de trading (datos completos por trade) + estrategia DIVERGENCE (desactivada)

- **Endpoints nuevos** (todos GET, sin token): `/api/account` (Account Value, Available, Position
  Value, Unrealized PNL, Margin Ratio, Maintenance Margin, fees hoy, PnL día/semana, escalera de
  riesgo), `/api/positions` (por posición: margen, **liquidación estimada** = entrada × (1 − 1/lev +
  0,5 %), ROE %, distancias a SL/TP, MAE/MFE en bps, tiempo abierto, comisiones, trigger, régimen y
  spread de entrada), `/api/orders` (SL/TP paper como órdenes protectoras con distancia al mark),
  `/api/market/{símbolo}` (mark/index/funding + **cuenta atrás del funding**, spread, bid/ask, cambio/
  máx/mín/volumen 24 h, régimen). `/api/trades` añade `trade_id, pnl_bps, roe_pct, leverage, mae_bps,
  mfe_bps, slippage_bps, order_type, exit_reason, hold_sec, equity_after, signal_strength, spread_bps`.
  El broadcast `positions` del WebSocket lleva las mismas filas ricas; `risk_update` incluye `account`.
- **DIVERGENCE** (`strategies/divergence.py`): divergencias regulares/ocultas de RSI14 entre pivotes
  confirmados (k=3, sin repintar), verificadas por zona extrema (35/65) y separación mínima de RSI,
  con entrada SOLO al cierre que rompe el máximo/mínimo de la barra del segundo pivote (ventana de
  6 barras) + histograma MACD a favor; stop = pivote ∓ 0,5 ATR, objetivo 2R, time stop 24 barras.
  Se siembra desde klines 4h de Binance al arrancar (solo si tiene asignación > 0).
  **Research (`tasks/research_divergence_2026-09-02.md`): NO-GO 2/7 en 1h (PF 0,77, t −2,15, 1.102
  trades, 14 variantes) y neutra en 4h (PF 1,00). Se despliega con `allocation_divergence = 0`**;
  la pestaña Strategies muestra el veredicto y la lista GO/NO-GO junto al interruptor.
- Config: grupo `divergence` en `/api/config/schema` (timeframe requiere restart; el resto en caliente).
- Comprobación rápida tras un deploy:
  ```
  pct exec 104 -- curl -s localhost:9420/api/health              # version 2.15.0
  pct exec 104 -- curl -s localhost:9420/api/account              # equity, available, margin_ratio
  pct exec 104 -- curl -s localhost:9420/api/market/BTC-USD       # funding_countdown_sec, high_24h
  pct exec 104 -- curl -s localhost:9420/api/strategies           # DIVERGENCE enabled=false, research NO-GO
  ```

## v2.16 — vigilancia automática, reset de histórico y API para la UI premium

- **Ops monitor dentro del CT** (`scripts/ops_monitor.py`, `botstrike-monitor.timer` cada 15 min, usuario
  `botstrike` en el grupo `systemd-journal`): comprueba bridge/engine/feed/edad del tick, el run diario del
  trend (debe estar OK antes de las 00:20 UTC), halts de riesgo y estrategias killed, errores/tracebacks
  del journal, bucles de reinicio (≥ 3 en 15 min) y avalanchas de régimen (> 8/h, sin contar arranques).
  Avisa por Telegram (mismo bot), con deduplicación de 6 h y aviso de "Resuelto"; a las 00:33 UTC envía el
  **resumen diario**. Estado en `data/ops_monitor_state.json`, última evaluación en
  `data/ops_monitor_last.json` (→ `GET /api/ops`). Comprobar: `systemctl list-timers botstrike-monitor.timer`,
  `journalctl -u botstrike-monitor --since -1h`.
- **Reset de histórico (2026-09-02 19:14Z, a petición de Edgar):** se borraron las 48 filas de los 24 trades
  cerrados de agosto y 25 sesiones antiguas; quedan solo las 3 entradas abiertas de hoy (trend). Copia previa
  en `/opt/botstrike/backups/2026-09-02_pre_reset/` (CT), `/root/botstrike_trade_database_2026-09-02_pre_reset.db`
  (host) y `data/ct104_trade_database_2026-09-02_pre_reset.db` (PC). Equity pasa a 1.000 + PnL abierto; pico,
  día y semana se reconstruyen desde la DB vacía.
- **API nueva para la UI premium** (`tasks/ui_premium_spec.md` §5): `GET /api/portfolio` (analítica
  estilo Strike: rachas, estilo de trading, duración media/mediana, 30D DD/WR/Sharpe, puntos de días
  ganadores, sesgo long/short, serie diaria para calendario, desglose por estrategia con curva),
  `GET /api/activity` (timeline: fills, runs del trend, cambios de régimen, kills, riesgo, config, sistema;
  persistida en `data/activity.json`), `GET /api/market/{sym}/funding_history` (Binance fapi, caché 5 min),
  `GET /api/ops`, `GET /api/trades/export.csv`, `symbol_config` en `/api/market/{sym}`.
- **UI v2.16 (Strike-grade)**: barra superior + footer con tickers (sin sidebar), Trade 1:1 con Strike (panel Bot en
  lugar del formulario de orden), Portfolio sustituye a Dashboard y Performance (`/dashboard` y `/performance`
  redirigen; `/orderflow` → `/trading`), Strategies tipo vault + ranking, engranaje con interruptores de layout,
  Ctrl+K para cambiar de mercado, cajón de actividad. Criterio de aceptación: `py -3.12 scripts/ui_contrast_audit.py
  http://192.168.1.204:9420 --width 1440` (y `--width 390 --height 844`) → `TOTAL offenders: 0`.
