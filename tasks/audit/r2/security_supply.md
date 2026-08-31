# Auditoría R2 — Seguridad, secretos y cadena de suministro

**Fecha:** 2026-08-31 · **Auditor:** Claude (subagente `security_supply`)
**Ámbito:** `config/settings.py`, manejo de `.env`, `.gitignore`, `requirements*.txt`/`requirements.lock`, `deploy/*`, `server/bridge.py` (auth/CORS/WS), `notifications/telegram.py`, `desktop/src-tauri/*` (updater/CSP/capabilities), historial git.
**Método:** lectura del código real + ejecución (`py -3.12`, `curl` contra el bridge arrancado en loopback) + OSV API para CVEs de dependencias. Cada hallazgo indica cómo se verificó. Se escribe de forma incremental. NO se modifica ningún archivo del proyecto salvo este informe.
**Regla nº1 (CLAUDE.md):** nada se afirma "de palabra"; lo comprobable está ejecutado con salida a la vista.

---

## Modelo de amenazas (breve)

- **Superficie de red:** el bridge FastAPI escucha en `0.0.0.0:9420` (`deploy/botstrike-bridge.service`), **HTTP plano sin TLS**. `deploy/install.sh:47-50` abre `9420/tcp` a **toda la LAN `192.168.1.0/24`** y a la tailnet `100.64.0.0/10`. La tailnet va cifrada (WireGuard); la LAN **no**.
- **Actores:** (A) cualquier dispositivo de la LAN (IoT, invitado wifi, portátil comprometido); (B) cualquier nodo de la tailnet; (C) cualquiera con acceso de lectura a journald del CT (root, grupo `systemd-journal`/`adm`, o un volcado/pega de `journalctl`); (D) cadena de suministro (PyPI, GitHub releases del updater).
- **Activo crítico:** el **token de operador** (`BOTSTRIKE_AUTH_TOKEN`) — con él se puede `POST /api/bot/start?mode=live` = **trading real con dinero**. No hay kill-switch de despliegue para live (ver SEC-03).
- **Lo que está bien:** `.env` real nunca commiteado; `serialize_settings` excluye secretos; Telegram no filtra el token; updater Tauri firmado (minisign) sobre HTTPS; capabilities Tauri mínimas; CSP restrictiva; 0 CVEs conocidos en las versiones pinneadas (OSV).

---

## Hallazgos

### [P1] security_supply-01 — El token de live viaja en query string y uvicorn lo escribe en claro en journald (y en la red, sin TLS)
**Archivo:** `server/bridge.py:1590-1596` (uvicorn `log_level="info"`, sin `access_log=False`); `server/bridge.py:1270,1290,1456` (`token: str = ""` como query param); `desktop/src/lib/api.ts:232-234` (`withToken` → `?token=`); `deploy/README.md:40` y `:26` (`/api/bot/start?mode=live&token=...`).
**Evidencia (ejecutado, salida real):**
```
$ py -3.12 -m server.bridge --host 127.0.0.1 --port 9492
$ curl "http://127.0.0.1:9492/api/bot/status?token=SECRET_TOKEN_ABC123"
$ curl -X POST "http://127.0.0.1:9492/api/bot/stop?token=SECRET_TOKEN_ABC123"
# stderr del bridge → journald:
INFO:  127.0.0.1:61470 - "GET /api/bot/status?token=SECRET_TOKEN_ABC123 HTTP/1.1" 200 OK
INFO:  127.0.0.1:61471 - "POST /api/bot/stop?token=SECRET_TOKEN_ABC123 HTTP/1.1" 200 OK
```
```typescript
// desktop/src/lib/api.ts:232
function withToken(path: string, token: string): string {
  if (!token) return path;
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}
```
**Por qué es un problema:** El token es una credencial **estática, no rotada** que autoriza **live trading**. El desktop lo envía como `?token=` en TODAS las llamadas autenticadas (start/stop/backtest), y la documentación de deploy lo confirma para live (`?mode=live&token=...`). uvicorn con `log_level="info"` deja el access-log ACTIVO (verificado arriba) → el token queda escrito en claro en journald en CADA petición. Vectores de fuga: (C) cualquiera que lea el journal (o un `journalctl` pegado en un chat/issue), y (A) al no haber TLS, cualquier sniffer de la LAN captura el token en la URL en texto plano. Una vez filtrado, `POST /api/bot/start?mode=live&token=<filtrado>` desde la LAN/tailnet arranca live con dinero real (no hay más barrera que el token — ver SEC-03). El servidor **ya soporta** la cabecera `X-BotStrike-Token` (`bridge.py:1217`), pero el desktop eligió query string y logrotate solo cubre `logs/*.jsonl`, no el journald donde cae el token. **Es un P0 en cuanto existan claves Binance con permiso de trading en el host** (hoy el CT es paper).
**Fix:**
1. Desktop: enviar el token SOLO por cabecera `X-BotStrike-Token`, nunca en la URL (quitar `withToken`/`?token=`).
2. Bridge: `uvicorn.run(..., access_log=False)` o un filtro que redacte `token=` en la línea de acceso; retirar `token: str` de la firma de los endpoints (usar únicamente la dependencia por cabecera).
3. Poner TLS delante (o restringir el bind a la IP Tailscale y quitar la regla ufw de `192.168.1.0/24`); rotar el token periódicamente.
**Verificado cómo:** ejecutado (bridge en :9492, access-log capturado con el token en claro) + leído (`api.ts`, `deploy/README.md`, `uvicorn.run`).

### [P1] security_supply-02 — `requirements.lock` fija versiones NUNCA validadas por la suite (pandas 3.0, starlette 1.6, fastapi 0.141, uvicorn 0.52, websockets 17) y sigue arrastrando streamlit/plotly/gitpython al servidor
**Archivo:** `requirements.lock:26,44,46,49,53,72,73,82,85`, `deploy/update.sh:13-17`.
**Evidencia (ejecutado):** versiones instaladas localmente (contra las que corren los 92/92 tests) **vs** el lock que `update.sh` instala en el CT con `uv pip sync`:
```
              LOCAL (probado)     requirements.lock (desplegado)
pandas        2.3.3               3.0.5     ← MAJOR (CoW por defecto, APIs eliminadas)
starlette     0.41.3             1.6.0      ← MAJOR
fastapi       0.115.6            0.141.1
uvicorn       0.34.0             0.52.4
websockets    14.1               17.1       ← 15+ elimina websockets.legacy
numpy         2.4.3              2.5.2
pydantic      2.10.3             2.13.5
structlog     25.5.0             26.1.0
```
```
# requirements.lock incluye, y uv pip sync INSTALA en el servidor:
streamlit==1.59.1  plotly==7.0.0  altair==6.2.2  gitpython==3.1.61  pydeck==0.9.3
pillow==12.3.0  protobuf==7.36.0  watchdog==6.0.0  jinja2==3.1.6  requests==2.34.2
```
```bash
# deploy/update.sh:13
if [ -f requirements.lock ]; then
  uv pip sync -q --python .venv/bin/python requirements.lock   # instala TODO el lock
```
**Por qué es un problema:** El objetivo de un lock es reproducibilidad **de lo probado**. Aquí el lock se generó con `uv pip compile` a "lo último" → fija majors (pandas 3.0, starlette 1.6) que la suite NUNCA ha ejecutado. "92/92 hoy" es contra el set LOCAL; el CT corre otro set. pandas 3.0 y starlette 1.6 traen cambios rompedores → riesgo real de que el bridge/engine reviente en el próximo `update.sh` (un domingo a las 3 AM, con el bot "parado" en silencio). Además el lock formaliza streamlit/plotly/gitpython/altair/pydeck/pillow/protobuf en el servidor —dependencias solo del dashboard **archivado**, que el bridge no importa— ampliando la superficie de ataque a cambio de cero función (residual de 03-P1-9, que pedía justo lo contrario).
**Fix:** (1) Generar el lock a partir del set REALMENTE probado (`uv pip compile` con `--upgrade-package` acotado, o pinnear a las versiones locales verificadas) y correr los 92 tests con ESE lock en CI antes de desplegar; (2) `requirements-server.txt` (o extras) SIN streamlit/plotly/gitpython/altair/pydeck; (3) CI: job que instale `requirements.lock` en ubuntu+3.12 y ejecute `pytest`.
**Verificado cómo:** ejecutado (`pip show` local vs lectura del lock) + OSV (0 CVEs en esas versiones, así que el riesgo es de compatibilidad/superficie, no de CVE conocido).

### [P2] security_supply-03 — No hay kill-switch de despliegue para live: `mode=live` es alcanzable por API en `0.0.0.0` solo con el token
**Archivo:** `server/bridge.py:1269-1286` (`bot_start` acepta `mode="live"` con token válido en cualquier bind); ausencia de `BOTSTRIKE_ALLOW_LIVE`.
**Evidencia (ejecutado):**
```
$ grep -rn "ALLOW_LIVE|allow_live" --include=*.py --include=*.service --include=*.sh .
NONE (no deploy-level live kill-switch)
```
```python
# bridge.py:1272 — la ÚNICA barrera para live es el token
if mode == "live" and not _token_ok(token):
    raise HTTPException(401, "Invalid or missing auth token for live mode")
```
`lifespan` sí rechaza `BOTSTRIKE_AUTOSTART=live` (bridge.py:1174-1176), pero eso solo cubre el autostart; una llamada explícita `POST /api/bot/start?mode=live&token=...` arranca live en el CT.
**Por qué es un problema:** La auditoría R1 (03-P0-2) recomendó explícitamente `BOTSTRIKE_ALLOW_LIVE=0` como kill-switch de host; **no se implementó**. Combinado con SEC-01 (token filtrable) y la política "NUNCA live desde el servidor" (MEMORY: blocker regulatorio Binance/España, "do NOT go live"), el host de paper es capaz de operar live si el token se filtra y hay claves con trading. Defensa en profundidad ausente.
**Fix:** añadir `_LIVE_ENABLED = os.getenv("BOTSTRIKE_ALLOW_LIVE","0")=="1"`; en `bot_start`, si `mode=="live" and not _LIVE_ENABLED` → `HTTPException(403)`. Fijar `BOTSTRIKE_ALLOW_LIVE=0` en la unit del CT.
**Verificado cómo:** ejecutado (grep) + leído.

### [P2] security_supply-04 — Todos los GET (y `/ws/*`) sin auth en bind no-loopback → la LAN/tailnet lee trades, PnL, posiciones y config en vivo
**Archivo:** `server/bridge.py:1262` (`/api/config`), `:1300` (`/api/bot/status`), `:1314` (`/api/performance`), `:1327` (`/api/strategies`), `:1362` (`/api/trades`), `:1415` (`/api/data/catalog`), `:1228` (`/ws/{channel}` sin token).
**Evidencia:** ninguno de esos endpoints declara `dependencies=[Depends(require_token_when_remote)]` (solo lo hacen start/stop/backtest). `/api/trades` devuelve historial completo (símbolo, side, cantidad, `pnl`, `fee`, timestamps); `/api/performance` devuelve equity curve, Sharpe, drawdown. `/api/data/catalog` sirve `data/catalog.json` verbatim, que incluye rutas internas:
```json
"file_path": "data\\trades\\ADA-USD"
```
`/ws/system` acepta conexiones sin credenciales (R1 03-P2-16, residual).
**Por qué es un problema:** En `0.0.0.0` con ufw abriendo LAN+tailnet, cualquier dispositivo lee la actividad de trading en tiempo real (posiciones, PnL, parámetros de estrategia, capital) — inteligencia útil para front-running o para perfilar la cuenta. `serialize_settings` sí excluye secretos (bien), pero los datos operativos y las rutas internas se exponen sin autenticación.
**Fix:** exigir el token también en los GET sensibles cuando `_EXPOSE_TOKEN=False` (dejar `/api/health` abierto para el monitor); autenticar `/ws/*` con `?token=`/primer mensaje `auth`; no servir rutas de fichero en `/api/data/catalog`.
**Verificado cómo:** leído (firmas de endpoints) + ejecutado (contenido de `catalog.json`).

### [P2] security_supply-05 — `--dev` (reload) desactiva la auth en silencio en bind no-loopback
**Archivo:** `server/bridge.py:53` (`_EXPOSE_TOKEN = True` por defecto a nivel módulo), `:1575-1576` (se ajusta SOLO en `main()`), `:1591-1594` (`uvicorn.run("server.bridge:app", reload=args.dev)`).
**Evidencia:**
```python
_EXPOSE_TOKEN = True            # bridge.py:53 (default de módulo)
...
def main():
    global _EXPOSE_TOKEN
    _EXPOSE_TOKEN = args.host in ("127.0.0.1","localhost","::1")   # solo en el proceso padre
    uvicorn.run("server.bridge:app" if args.dev else app, reload=args.dev, ...)
```
Con `--dev`, uvicorn usa `reload=True`: el proceso worker que sirve las peticiones **re-importa** `server.bridge:app` y **no ejecuta `main()`** (está bajo `if __name__=="__main__"`), así que `_EXPOSE_TOKEN` queda en su default `True`. `require_token_when_remote` retorna sin comprobar nada cuando `_EXPOSE_TOKEN` es True → con `--dev --host 0.0.0.0` las mutaciones NO piden token, `/api/bot/status` expone el token y `/docs` se sirve.
**Por qué es un problema:** Es un bypass silencioso de un control de seguridad. Solo afecta a `--dev` (no es el arranque de producción), pero un operador que levante dev en `0.0.0.0` para depurar deja el bridge abierto sin saberlo.
**Fix:** derivar `_EXPOSE_TOKEN` de una variable de entorno leída a nivel módulo (p.ej. `BOTSTRIKE_HOST`) en vez de mutarla solo en `main()`, para que el worker de reload la calcule igual; o prohibir `--dev` con host no-loopback.
**Verificado cómo:** leído (semántica del reloader de uvicorn: el worker importa el string de app sin `__main__`).

### [P2] security_supply-06 — No se verifica el alcance de la API key de Binance ("read-only para paper" es solo un consejo)
**Archivo:** `deploy/install.sh:34`, `deploy/verify.sh:27`, `deploy/README.md`; ausencia de comprobación en `exchange/`.
**Evidencia (ejecutado):**
```
$ grep -rniE "apiRestrictions|canTrade|enableFutures|read.?only" exchange/ main.py config/
# (nada relevante: solo hyperliquid_no_private_key_read_only; NINGUNA verificación Binance)
```
`install.sh` sugiere "Binance read-only keys are enough for paper" pero nada llama a `GET /sapi/v1/account/apiRestrictions` ni comprueba permisos.
**Por qué es un problema:** Si el operador pega una key con permiso de Futures/trading/retiro en el `.env` del host de paper (por comodidad o error), nada lo detecta. Sumado a SEC-01 + SEC-03 (live alcanzable con token filtrado), esa key con trading en un host expuesto a la LAN es un riesgo de dinero real y, si tiene retiro habilitado, de fondos.
**Fix:** al arrancar, si hay `BINANCE_API_KEY`, llamar a `apiRestrictions`; en paper, si `enableFutures`/`enableSpotAndMarginTrading`/`enableWithdrawals` están activos → `logger.critical` + Telegram (y opcionalmente rehusar arrancar). Documentar la lista exacta de permisos a desmarcar.
**Verificado cómo:** ejecutado (grep) + leído.

### [P3] security_supply-07 — `GEMINI_API_KEY` es un secreto muerto: nadie en la ruta viva lo usa, pero vive en `.env` (y en el entorno del servicio)
**Archivo:** `config/settings.py` (no lo lee), consumidores solo en `archive/core/ai_analyst.py`.
**Evidencia (ejecutado):**
```
$ git grep -l "ai_analyst|AIAnalyst" -- '*.py' | grep -v build
archive/core/ai_analyst.py
archive/dashboard/.../4_Strategy.py
dashboard/pages/4_Strategy.py     # dashboard NO usado por el bridge
```
Las únicas referencias a `GEMINI_API_KEY` fuera de `archive/` están en artefactos de build no trackeados (`desktop/src-tauri/target/...`, en `.gitignore`).
**Por qué es un problema:** Un secreto en `.env` sin uso amplía la superficie sin beneficio; con `EnvironmentFile=.env` (unit) acaba en el entorno del proceso del servicio (`/proc/<pid>/environ`, heredado por subprocesos). Menos secretos = menos que filtrar.
**Fix:** eliminar `GEMINI_API_KEY` del `.env` del servidor; documentar en `.env.example` que es legacy/archivado.
**Verificado cómo:** ejecutado (git grep).

### [P3] security_supply-08 — `EnvironmentFile=.env` mete TODOS los secretos (token de live + secret de Binance) en el entorno del servicio, innecesariamente (python-dotenv ya los carga)
**Archivo:** `deploy/botstrike-bridge.service:18` (`EnvironmentFile=/opt/botstrike/app/.env`), `config/settings.py:12` (`load_dotenv()`).
**Evidencia:** la app ya llama `load_dotenv()` al importar `config.settings`; el `EnvironmentFile` es redundante y, sin prefijo `-`, además hace que la unit **no arranque** si falta `.env`. Todos los valores (`BOTSTRIKE_AUTH_TOKEN`, `BINANCE_API_SECRET`, `TELEGRAM_BOT_TOKEN`) quedan en el entorno del proceso, legibles por root vía `/proc/<pid>/environ` y heredados por cualquier subproceso.
**Por qué es un problema:** duplica la exposición de secretos sin necesidad. Ref: **03-P2-12 sigue abierto** (systemd sin endurecer: `ProtectSystem=full` deja `/opt` escribible, sin `UMask=0077`, sin `TZ`, sin `ProtectSystem=strict`); este hallazgo es el ángulo específico de secretos-en-entorno.
**Fix:** quitar `EnvironmentFile` (dotenv ya carga `.env`); dejar solo variables no-secretas como `Environment=`. Aplicar el endurecimiento de 03-P2-12 (`ProtectSystem=strict`, `UMask=0077`, `ProtectHome=true`).
**Verificado cómo:** leído.

---

## Verificación de fixes de la Ronda 1 (correctos / incompletos)

| Fix R1 | Estado | Nota |
|--------|--------|------|
| 03-P0-1 autostart `BOTSTRIKE_AUTOSTART` | **Correcto** | `lifespan` arranca paper/dry_run, rechaza `live`; verificado por lectura (`bridge.py:1168-1178`). |
| 03-P0-2 token desde `.env`, no expuesto en `0.0.0.0` | **Correcto pero incompleto** | `/api/bot/status` da `auth_token:null` en no-loopback ✔. PERO el token sigue yendo por query string y a los logs (SEC-01), y los GET/WS siguen sin auth (SEC-04). |
| 03-P1 health real + watchdog | **Correcto** | `_health_snapshot` 503, watchdog con backoff y `os._exit(3)`; buena implementación. |
| 03-P1 backtest fuera del loop + validación symbol | **Correcto** | `asyncio.to_thread`, `symbol in symbol_names` (400), 1 concurrente (409). |
| 03-P1-9 pinning / quitar streamlit | **Incompleto/regresión** | Hay lock, pero fija majors no probados y sigue instalando streamlit/plotly/gitpython en el server (SEC-02). |
| 03-P2-11 install.sh (known_hosts, ufw reset, chrony) | **Abierto** | `install.sh` sigue con `chrony`, `ufw --force reset`, `uv ... \|\| true`, sin `known_hosts`. |
| 03-P2-12 endurecer systemd | **Abierto** | Sigue `ProtectSystem=full` (no strict), sin `UMask`/`TZ`, `EnvironmentFile` presente (SEC-08). |
| 03-P2-16 WS sin auth | **Abierto** | `/ws/{channel}` sigue sin token (SEC-04). |
| 03-P3-19 token en query string | **Abierto → escalado** | Ahora es el vector principal de fuga del token (SEC-01). |

**Sin regresiones en las áreas verificadas por ejecución** (bridge importa y arranca en 1,3 s; health/auth/backtest se comportan como documenta `fixes_round1_bridge.md`).

---

## Tabla resumen

| ID | Sev | Título | Archivo |
|----|-----|--------|---------|
| security_supply-01 | **P1** | Token de live en query string → journald en claro + en la red sin TLS | server/bridge.py:1590; desktop/src/lib/api.ts:232 |
| security_supply-02 | **P1** | `requirements.lock` fija majors no probados (pandas 3.0, starlette 1.6…) + streamlit/plotly en server | requirements.lock; deploy/update.sh:13 |
| security_supply-03 | P2 | Sin kill-switch de host para live: `mode=live` por API solo con token | server/bridge.py:1269 |
| security_supply-04 | P2 | GET y `/ws/*` sin auth en `0.0.0.0` → LAN lee trades/PnL/posiciones/config | server/bridge.py:1362,1314,1228 |
| security_supply-05 | P2 | `--dev` desactiva la auth en silencio en bind no-loopback | server/bridge.py:53,1576,1591 |
| security_supply-06 | P2 | No se verifica el alcance de la API key de Binance | deploy/install.sh:34; exchange/ |
| security_supply-07 | P3 | `GEMINI_API_KEY` secreto muerto en `.env` | config/settings.py; archive/core/ai_analyst.py |
| security_supply-08 | P3 | `EnvironmentFile=.env` mete secretos en el entorno del servicio (redundante) | deploy/botstrike-bridge.service:18 |

Referencias a hallazgos R1 aún abiertos: **03-P2-11**, **03-P2-12**, **03-P2-16**, **03-P1-9** (residual).

---

## Veredicto (10 líneas)

1. No hay secretos en el historial git ni hardcodeados: `.env` real nunca commiteado, solo `.env.example` con placeholders — correcto.
2. Los P0 de la Ronda 1 (autostart, token no expuesto en `0.0.0.0`, health/watchdog, backtest off-loop) están **bien corregidos y sin regresión** (verificado ejecutando el bridge).
3. El agujero de seguridad vivo más serio es **SEC-01**: la credencial que autoriza live viaja en query string y uvicorn la escribe **en claro en journald** (probado con salida real) y, sin TLS, en la LAN.
4. Ese token es estático, no rotado, y la única barrera para live: sin kill-switch de host (**SEC-03**), un token filtrado = trading real desde la LAN/tailnet.
5. Hoy el CT es paper sin claves de trading, así que SEC-01 es P1; **pasa a P0 en cuanto haya claves Binance con permiso de trading** en el host.
6. La cadena de suministro tiene un problema real (**SEC-02**): el `requirements.lock` desplegado fija **majors nunca probados** (pandas 3.0, starlette 1.6, fastapi 0.141) → "92/92 tests" no cubre lo que corre el CT.
7. OSV no reporta CVEs conocidos en las versiones pinneadas, pero el lock arrastra streamlit/plotly/gitpython al servidor sin uso → superficie innecesaria.
8. Info-disclosure real (**SEC-04**): todos los GET y los WS quedan abiertos a la LAN en `0.0.0.0`; `serialize_settings` sí protege los secretos (bien).
9. Bien hechos: updater Tauri firmado (minisign) sobre HTTPS, capabilities mínimas, CSP restrictiva, Telegram sin fuga de token, endpoints de config sin secretos.
10. **Prioridad:** cabecera en vez de `?token=` + `access_log=False` + TLS/tailnet-only (SEC-01); regenerar el lock desde lo probado y sacar streamlit/plotly (SEC-02); `BOTSTRIKE_ALLOW_LIVE=0` (SEC-03); cerrar 03-P2-11/12/16 aún abiertos. Con SEC-01/02 abiertos, el 24/7 desatendido **no** es seguro.
