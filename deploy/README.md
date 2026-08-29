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

## Acceso desde el desktop
En la app: Settings → Connection → Bridge URL = `http://192.168.1.204:9420` (LAN) o la IP Tailscale del CT.
El puerto 9420 sólo está abierto (ufw) para la LAN `192.168.1.0/24` y la tailnet `100.64.0.0/10`.
