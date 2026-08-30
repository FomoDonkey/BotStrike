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
