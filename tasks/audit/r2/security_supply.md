# Auditoría R2 — Seguridad, secretos y cadena de suministro

**Fecha:** 2026-08-31 · **Auditor:** Claude (subagente `security_supply`, pase 2 — completo)
**Ámbito:** `config/settings.py`, manejo de `.env`, `.gitignore`, `requirements*.txt`/`requirements.lock`, `deploy/*`, `server/bridge.py` (auth/CORS/WS/webui), `notifications/telegram.py`, `desktop/src-tauri/*` (updater/CSP/capabilities), `.github/workflows/*`, repositorio GitHub, historial git.
**Método:** lectura del código real + **ejecución** (`py -3.12`, `curl`, cliente WS, `gh` API, OSV batch API, `npm audit`) + doc oficial (`man7.org/systemd.exec`). Cada hallazgo dice cómo se verificó. NO se ha modificado ningún archivo del proyecto salvo este informe.
**Regla nº1 (CLAUDE.md):** nada "de palabra". Lo comprobable está ejecutado con salida a la vista.

> **Nota sobre este archivo.** Los hallazgos 01–08 vienen del primer pase de esta área y fueron
> **verificados de forma independiente** por Edgar/sesión principal en `tasks/audit/r2_verification_claude.md`
> (commits `83a0f21`, `01b2cb0`). Aquí se recogen **con la severidad ya corregida por esa verificación**
> (02 baja a P2 — parcialmente refutado; 05 sube a P1 — bypass total). Los hallazgos **09–20 son nuevos
> de este pase** y nadie los había visto: CSRF/DNS-rebinding contra el bridge de loopback, la UI web
> servida sin auth, el repositorio **público**, la clave de firma del updater expuesta a todo el build,
> escalada a root vía `/opt`, y **CI en rojo en los últimos 10 push** mientras `update.sh` despliega igual.

---

## Modelo de amenazas (breve)

| # | Actor | Capacidad hoy (verificada) | Activo en riesgo |
|---|-------|----------------------------|------------------|
| A | Cualquier dispositivo de la LAN `192.168.1.0/24` (IoT, invitado wifi, portátil comprometido) | `ufw` abre 9420/tcp a toda la LAN (`install.sh:47`). **Comprobado desde mi PC sin credenciales:** `GET /` → terminal completa 200, `GET /api/trades` → 14 KB de historial, `GET /api/health` → 200 | Historial de trades, PnL, posiciones, config, consola de operador |
| B | Cualquier nodo de la tailnet `100.64.0.0/10` | Igual que A, pero cifrado en tránsito (WireGuard) | Igual que A |
| C | Cualquiera que lea journald del CT (root, grupo `adm`/`systemd-journal`, o un `journalctl` pegado en un chat/issue) | uvicorn escribe la query string entera en el access log (**reproducido**) | `BOTSTRIKE_AUTH_TOKEN` → arranque de **live** |
| D | **Cualquier web que el operador visite** con la app de escritorio abierta | `POST /api/bot/stop` cross-origin sin token → **200** (**reproducido**); DNS-rebinding → lectura de `/api/bot/status` → token → `mode=live` | Parada del bot con posición abierta; **live trading** |
| E | Cadena de suministro (PyPI, npm, acciones de GitHub, `astral.sh`) | La clave privada del updater está en el `env` de **todos** los pasos del release; acciones sin pinear; `pip install` sin pinear; `curl \| sh` sin verificar | Firma de updates → **RCE en el PC de trading** de todos los instalados |
| F | Internet (pasivo) | El repo es **PÚBLICO** y publica IP Tailscale del host, IP LAN del CT, puerto, reglas ufw y la lista viva de vulnerabilidades **abiertas** | Mapa de ataque completo |

**Activo crítico:** el token de operador (`BOTSTRIKE_AUTH_TOKEN`) — es la **única** barrera para
`POST /api/bot/start?mode=live` (no hay kill-switch de despliegue, SEC-03). **Segundo activo crítico,
nuevo en este pase:** `TAURI_SIGNING_PRIVATE_KEY`, que firma updates que se instalan solos
(`installMode: "passive"`) en la máquina donde viven las claves de Binance.

**Lo que está bien (verificado, no inventado):**
`.env` real **nunca** commiteado (`git log --all -- .env` → vacío; el único fichero `*env*` añadido en toda
la historia es `.env.example`) · `serialize_settings` no filtra secretos · Telegram no filtra el token del bot
(solo lo mete en la URL de `api.telegram.org` sobre TLS; los logs de error solo llevan `str(e)`) ·
la deploy key del CT es **read-only y verificada** (`gh api .../keys` → `"read_only": true`) ·
el sidecar PyInstaller **no** empaqueta el `.env` (`find build desktop/src-tauri/binaries -name ".env*"` → 0) ·
capabilities de Tauri mínimas (`core:default` y nada más) · CSP de producción restrictiva
(`script-src 'self'`, `object-src 'none'`, `form-action 'none'`) · updater con clave minisign sobre HTTPS ·
`package-lock.json` commiteado y `npm ci` en CI/release · **0 CVEs conocidos** en las 86 versiones de
`requirements.lock` (OSV batch API, ejecutado).

---

## Hallazgos

### [P1] security_supply-01 — El token de live viaja en query string y uvicorn lo escribe en claro en journald (y en la red, sin TLS)
**Archivo:** `server/bridge.py:1590-1596`; `server/bridge.py:1270,1290,1456`; `desktop/src/lib/api.ts:232-234`; `deploy/README.md:26,40`.
**Evidencia (ejecutado por mí, misma forma de llamada que el bridge):**
```python
# server/bridge.py:1590-1596 — log_level="info" y SIN access_log=False
uvicorn.run("server.bridge:app" if args.dev else app, host=args.host, port=args.port,
            reload=args.dev, log_level="info")
```
```
INFO:     127.0.0.1:59934 - "GET /api/bot/status?token=SUPER_SECRET_LIVE_TOKEN_123 HTTP/1.1" 200 OK
INFO:     127.0.0.1:59935 - "GET /api/bot/status?mode=live&token=SUPER_SECRET_LIVE_TOKEN_123 HTTP/1.1" 200 OK
```
```typescript
// desktop/src/lib/api.ts:232
function withToken(path: string, token: string): string {
  if (!token) return path;
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}
```
**Por qué es un problema:** credencial estática, no rotada, que autoriza **live trading**, escrita en claro
en journald en cada petición y —sin TLS— visible para cualquier sniffer de la LAN. El servidor **ya acepta**
`X-BotStrike-Token` (`bridge.py:1217`), pero la UI eligió la query string. `logrotate-botstrike` solo cubre
`logs/*.jsonl`, no journald.
**Matiz verificado por Edgar:** `journalctl -u botstrike-bridge --since -7d | grep -c "token=" → 0`. **No ha
habido fuga real todavía** porque aún no se han pulsado Start/Stop desde la web. Hay que cerrarlo *antes*.
**Fix:** (1) UI solo por cabecera; (2) `access_log=False` o filtro que redacte `token=`; (3) retirar
`token: str` de las firmas; (4) TLS o bind solo a la IP Tailscale.
**Verificado cómo:** ejecutado (repro del access log) + leído.

### [P2] security_supply-02 — El gate de calidad del despliegue no existe: falta `httpx2` en el CT y el lock arrastra streamlit/plotly/gitpython al servidor *(parcialmente refutado, bajado de P1)*
**Archivo:** `requirements.lock:44,46,49,53,72,73,82,85`, `deploy/update.sh:13-17`, `requirements-dev.txt`.
**Evidencia (ejecutado):** el set desplegado (pandas 3.0.5 / starlette 1.6.0 / fastapi 0.141.1 / numpy 2.5.2)
**no rompe el bot** — réplica en venv desechable: `100 passed in 8.95s`. Lo que sí es cierto:
```
# En el CT: starlette 1.6 movió TestClient a httpx2, que el lock NO incluye
ERROR tests/test_bridge_round2.py - RuntimeError: The starlette.testclient module requires the httpx2 package
!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!
```
```
# requirements.lock — instalado por `uv pip sync` en el servidor, sin uso alguno allí:
streamlit==1.59.1  plotly==7.0.0  altair==6.2.2  gitpython==3.1.61  pydeck==0.9.3  pillow==12.3.0
```
**Por qué es un problema:** en el CT la suite **ni siquiera colecciona** → 0 tests cubren lo que corre en
producción, y `update.sh` reinicia el servicio sin ninguna puerta. Y el lock formaliza en el servidor
5 paquetes que solo usa el dashboard **archivado** → superficie a cambio de cero función (residual de
**03-P1-9**, que pedía justo lo contrario). **Corrección honesta:** la alarma "pandas 3.0 / starlette 1.6
pueden reventar el bot" queda **refutada con datos**; por eso esto es P2 y no P1.
**Fix:** `httpx2` en `requirements-dev.txt`; `requirements-server.txt` sin streamlit/plotly/altair/pydeck/gitpython; puerta en `update.sh` (no reiniciar si `pytest` falla).
**Verificado cómo:** ejecutado (suite en réplica del set del lock: 100/100; suite en el CT: error de colección) + OSV (0 CVEs).

### [P2] security_supply-03 — No hay kill-switch de despliegue para live: `mode=live` es alcanzable por API solo con el token
**Archivo:** `server/bridge.py:1269-1273`; ausencia de `BOTSTRIKE_ALLOW_LIVE` en `deploy/botstrike-bridge.service`.
**Evidencia (ejecutado):**
```
$ grep -rn "ALLOW_LIVE" --include=*.py --include=*.service --include=*.sh .   → 0 resultados
$ grep -c ALLOW_LIVE /etc/systemd/system/botstrike-bridge.service (en el CT)  → 0
```
```python
# bridge.py:1272 — la ÚNICA barrera para live
if mode == "live" and not _token_ok(token):
    raise HTTPException(status_code=401, detail="Invalid or missing auth token for live mode")
```
`lifespan` sí rechaza `BOTSTRIKE_AUTOSTART=live` (`bridge.py:1174-1176`), pero eso solo cubre el autostart.
**Por qué es un problema:** R1 (**03-P0-2**) recomendó explícitamente `BOTSTRIKE_ALLOW_LIVE=0` y no se
implementó. Con el bloqueo regulatorio (Binance/España) y la política "do NOT go live", es defensa en
profundidad barata y ya decidida por política.
**Fix:** `_LIVE_ENABLED = os.getenv("BOTSTRIKE_ALLOW_LIVE","0")=="1"`; 403 si `mode=="live"` y no está;
`Environment=BOTSTRIKE_ALLOW_LIVE=0` en la unit.
**Verificado cómo:** ejecutado (grep local y en el CT) + leído.

### [P2] security_supply-04 — Todos los GET y los `/ws/*` sin auth en bind no-loopback → la LAN/tailnet lee trades, PnL, posiciones y config
**Archivo:** `server/bridge.py:1262` (`/api/config`), `:1300` (`/api/bot/status`), `:1314` (`/api/performance`), `:1327` (`/api/strategies`), `:1362` (`/api/trades`), `:1415` (`/api/data/catalog`), `:1228` (`/ws/{channel}`).
**Evidencia (ejecutado desde mi PC, LAN, SIN token):**
```
$ curl -s -m 6 -o /dev/null -w "%{http_code} %{size_download}b\n" http://192.168.1.204:9420/api/trades
200 14055b
$ curl http://192.168.1.204:9420/api/performance   → equity, PnL, WR, Sharpe, drawdown, curva completa
$ curl http://192.168.1.204:9420/api/strategies    → estrategias, allocations, estado
```
Ninguno declara `dependencies=[Depends(require_token_when_remote)]` (solo start/stop/backtest lo hacen).
`/api/data/catalog` sirve `data/catalog.json` verbatim, con rutas internas (`"file_path": "data\\trades\\ADA-USD"`).
**Por qué es un problema:** inteligencia operativa en tiempo real (posiciones, capital, parámetros) para
cualquier dispositivo de la LAN. `serialize_settings` sí excluye secretos (bien), pero los datos de negocio no.
Residual de **03-P0-2** y **03-P2-16**, ambos abiertos.
**Fix (decisión de producto, no parche ciego):** `BOTSTRIKE_REQUIRE_AUTH_READS` (default 0 = comportamiento
actual) para no romper el flujo "abro el navegador y veo el bot"; autenticar `/ws/*`; no publicar rutas de fichero.
**Verificado cómo:** ejecutado (curl real contra el CT en producción).

### [P1] security_supply-05 — `--dev` es un **bypass total** de la auth en bind no-loopback (no solo "silencioso")
**Archivo:** `server/bridge.py:53`, `:1575-1576`, `:1591-1594`.
**Evidencia (ejecutado con `--host 0.0.0.0 --port 9494 --dev`):**
```
/api/bot/status    → auth_token_exposed: True | auth_token: <REGALADO POR HTTP>
/docs              → 200   (debería ser 404 en bind no-loopback)
POST /api/bot/stop SIN token → 200   (debería ser 401)
```
```python
_EXPOSE_TOKEN = True                                        # bridge.py:53 — default de módulo
def main():
    global _EXPOSE_TOKEN
    _EXPOSE_TOKEN = args.host in ("127.0.0.1","localhost","::1")   # solo en el proceso padre
    uvicorn.run("server.bridge:app" if args.dev else app, reload=args.dev, ...)
```
Con `reload=True` el worker **re-importa** `server.bridge:app` y no pasa por `main()` (está bajo `if __name__=="__main__"`).
**Por qué es un problema:** no es un control que se relaja, es el control entero desactivado: el token se
regala por HTTP y las mutaciones se aceptan sin credencial. Solo afecta a `--dev`, pero el fix es de 3 líneas.
**Fix:** derivar `_EXPOSE_TOKEN` de `BOTSTRIKE_HOST` a nivel de módulo, o rehusar `--dev` con host no-loopback.
**Verificado cómo:** ejecutado (verificación independiente de Edgar, `r2_verification_claude.md:83-95`).

### [P2] security_supply-06 — No se verifica el alcance de la API key de Binance ("read-only para paper" es solo un comentario)
**Archivo:** `deploy/install.sh:34`, `deploy/verify.sh:27`, ausencia de comprobación en `exchange/`.
**Evidencia (ejecutado):**
```
$ grep -rniE "apiRestrictions|canTrade|enableWithdraw|read.?only|permissions" exchange/binance_client.py main.py config/settings.py
(0 resultados)
```
`install.sh:34` dice `>> Edit $APP_DIR/.env with your API keys (Binance read-only keys are enough for paper).`
y `verify.sh:27` solo comprueba que `BINANCE_API_KEY` **no esté vacía**, nunca qué permisos tiene.
**Por qué es un problema:** si el operador pega por comodidad una key con Futures/trading (o retiro), nada lo
detecta; sumado a SEC-01/03/09 eso es dinero real en un host expuesto a la LAN. **Honestidad:** no he llamado a
la API de Binance con la key real (no toco la cuenta del usuario); lo verificado es que **el código no comprueba nada**.
**Fix:** al arrancar, si hay `BINANCE_API_KEY`, `GET /sapi/v1/account/apiRestrictions`; en paper, si
`enableSpotAndMarginTrading`/`enableFutures`/`enableWithdrawals` → `logger.critical` + Telegram (y rehusar arrancar).
**Verificado cómo:** ejecutado (grep) + leído. Alcance de la key viva: **no verificado** (deliberado).

### [P3] security_supply-07 — `GEMINI_API_KEY` es un secreto muerto que sigue en el `.env` (y en el entorno del servicio)
**Archivo:** `.env` (línea `GEMINI_API_KEY=`), consumidores solo en `archive/core/ai_analyst.py` y `dashboard/pages/4_*.py` (Streamlit archivado).
**Evidencia (ejecutado):** `grep -c "^GEMINI_API_KEY=." .env` → **1** (también en el CT). `config/settings.py` no lo lee en ninguna línea; ningún módulo de la ruta viva lo importa.
**Por qué es un problema:** secreto sin uso = superficie gratis; con `EnvironmentFile` acaba además en
`/proc/<pid>/environ` y lo heredan los subprocesos.
**Fix:** borrarlo del `.env` del servidor y documentarlo como legacy en `.env.example`.
**Verificado cómo:** ejecutado (grep + git grep de consumidores).

### [P3] security_supply-08 — `EnvironmentFile=.env` duplica todos los secretos en el entorno del proceso (redundante: `load_dotenv()` ya los carga)
**Archivo:** `deploy/botstrike-bridge.service:17`, `config/settings.py` (`load_dotenv()`).
**Evidencia:** la unit hace `EnvironmentFile=/opt/botstrike/app/.env` y la app ya llama `load_dotenv()` al
importar `config.settings`. Resultado: `BOTSTRIKE_AUTH_TOKEN`, `BINANCE_API_SECRET`, `TELEGRAM_BOT_TOKEN` y
`STRIKE_PRIVATE_KEY` quedan en `/proc/<pid>/environ` y se heredan a cualquier subproceso. Sin prefijo `-`,
además la unit **no arranca** si falta `.env`.
**Por qué es un problema:** duplica la exposición sin ganar nada. Ángulo de secretos de **03-P2-12** (abierto).
**Fix:** quitar `EnvironmentFile`; dejar solo `Environment=` para lo no secreto.
**Verificado cómo:** leído.

---

### [P1] security_supply-09 — **NUEVO** · Cualquier web que el operador visite puede parar el bot (CSRF), y con DNS-rebinding robar el token y arrancar **live**
**Archivo:** `server/bridge.py:1194-1200` (CORS), `:1217-1224` (`require_token_when_remote`), `:1228-1234` (WS sin `Origin`), `:1308` (`auth_token` en claro cuando el bind es loopback).
**Evidencia (ejecutado — bridge real en `127.0.0.1:9495`, exactamente la configuración de la app de escritorio):**
```
===== A) POST cross-origin, SIN token, "simple request" (sin preflight) =====
$ curl -X POST -H "Origin: https://evil.example" -H "Content-Type: text/plain" \
       http://127.0.0.1:9495/api/bot/start?mode=paper
{"status":"starting","mode":"paper","exchange":"binance"}    HTTP 200
$ curl -X POST -H "Origin: https://evil.example" -H "Content-Type: text/plain" \
       http://127.0.0.1:9495/api/bot/stop
{"status":"stopped"}                                         HTTP 200
===== B) live sin token (la barrera que SÍ aguanta) =====
POST /api/bot/start?mode=live  → 401 {"detail":"Invalid or missing auth token for live mode"}
===== C) ¿valida el Host? (DNS-rebinding) =====
$ curl -H "Host: evil.example" http://127.0.0.1:9495/api/bot/status
{"running":false,...,"auth_token":"b2becefc62e36bceccb5f66e7d9adf4a","auth_token_exposed":true,...}  HTTP 200
===== D) el preflight sí se rechaza (pero eso NO impide el efecto de A) =====
OPTIONS /api/bot/stop (Origin: https://evil.example) → 400 "Disallowed CORS origin"
```
```
===== WebSocket cross-origin (los WS NO están sujetos a CORS) =====
  /ws/trading: ACCEPTED from foreign origin -> {"type": "pong"}
  /ws/risk:    ACCEPTED from foreign origin -> {"type": "pong"}
  /ws/system:  ACCEPTED from foreign origin -> {"type": "pong"}
```
**Por qué es un problema:** R1 vio que "CORS solo protege contra navegadores; `curl` lo salta". Lo que
**nadie vio** es el reverso: **el navegador también lo salta para el efecto secundario**. `POST` con
`Content-Type: text/plain` y sin cabeceras custom es una *simple request*: el navegador la **envía** sin
preflight; CORS solo impide **leer la respuesta**. Cadena de ataque, toda con partes ya reproducidas:
1. El operador tiene la app de escritorio abierta (bridge en `127.0.0.1:9420`, `_EXPOSE_TOKEN=True` → sin token) y visita una web cualquiera.
2. Esa web hace `fetch("http://127.0.0.1:9420/api/bot/stop",{method:"POST",mode:"no-cors"})` → **el bot se para** (posición abierta sin gestionar), o spamea `/api/backtest/run` (DoS de CPU), o `start?mode=paper`.
3. Escalada: el atacante sirve la página desde un dominio con TTL 0 y **rebinding a 127.0.0.1**. Tras el rebind el origen es *el mismo* (`http://evil.example:9420`), CORS deja de aplicar, la página **lee** `/api/bot/status` → obtiene `auth_token` en claro (evidencia C: no hay validación de `Host`) → `POST /api/bot/start?mode=live&token=<robado>` → **trading real con las claves del operador**.
4. En paralelo, `ws://evil.example:9420/ws/trading` da streaming de trades/riesgo cross-origin (los WS ignoran CORS y `websocket_endpoint` no mira `Origin`).
**Es P1 hoy y P0 el día que existan claves de Binance con permiso de trading en el PC** (que es donde vive el `.env` con `BINANCE_API_SECRET`).
**Fix:** (1) exigir una cabecera custom (`X-BotStrike-Token`) en **toda** mutación, también en loopback — una cabecera custom fuerza preflight y mata el CSRF; (2) allowlist de `Host` (`127.0.0.1`, `localhost`, IP de bind) → 421 en cualquier otro, que es el antídoto estándar del rebinding; (3) validar `Origin` en el handshake de `/ws/{channel}`; (4) dejar de publicar `auth_token` en `/api/bot/status` (que el desktop lo lea de un fichero local o de un canal IPC de Tauri).
**Verificado cómo:** ejecutado (bridge real, curl con `Origin`/`Host` forjados, cliente WS con `Origin` forjado).

### [P1] security_supply-10 — **NUEVO** · El bridge sirve la consola de operador completa, sin auth y en HTTP plano, a toda la LAN/tailnet
**Archivo:** `server/bridge.py:1560-1562`; bundle commiteado en `server/webui/` (`index-DBh0rRsX.js`, 870 KB).
**Evidencia (ejecutado contra el CT en producción, desde mi PC, sin ninguna credencial):**
```
$ curl -s -m 6 -o /dev/null -w "%{http_code} %{content_type} %{size_download}b\n" http://192.168.1.204:9420/
200 text/html; charset=utf-8 634b
$ curl -s http://192.168.1.204:9420/ | head -8
<!doctype html> ... <title>BotStrike Trading Terminal</title>
```
```python
# server/bridge.py:1560-1562 — sin auth, sin TLS, montado en "/"
_WEBUI_DIR = Path(__file__).resolve().parent / "webui"
if _WEBUI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_WEBUI_DIR), html=True), name="webui")
```
```
$ grep -o "botstrike.authToken\|token=" server/webui/assets/index-DBh0rRsX.js | sort | uniq -c
      1 botstrike.authToken          ← el token de LIVE se guarda en localStorage de un origen http://
      2 token=
```
**Por qué es un problema:** esto es **nuevo en 2.13.1** (R1 auditó 2.12 y no existía) y va más allá de SEC-04:
no es que se filtren unos JSON, es que **cualquiera en la LAN abre `http://192.168.1.204:9420` y tiene la
terminal de trading entera** — equity, posiciones, estrategias, historial, y los botones Start/Stop/Backtest.
Las mutaciones piden token (bien), pero: (a) el flujo diario del operador consiste en **teclear el token de
live en Settings → Connection de una página servida por HTTP plano en la LAN**, con lo que SEC-01 deja de ser
teórico; (b) el token queda en `localStorage` de un origen `http://` (sin `Secure`, compartido con cualquier
contenido que se sirva desde ese origen); (c) es una superficie de phishing perfecta (un vecino de LAN sirve
una copia de esa UI y recolecta tokens). `_hide_docs_when_remote` (`bridge.py:1205-1210`) tapa `/docs` pero
**no** la UI, que es infinitamente más informativa.
**Fix:** servir la UI solo con TLS (Caddy/nginx delante, o Tailscale Serve con certificado) **o** solo cuando el
bind es loopback; exigir el token también para `GET /` cuando `_EXPOSE_TOKEN=False`; nunca pedir la credencial
de live en una página no-HTTPS.
**Verificado cómo:** ejecutado (curl contra el CT real) + leído (`bridge.py`, bundle).

### [P1] security_supply-11 — **NUEVO** · El repositorio es **PÚBLICO** y publica las direcciones de la infra y la lista viva de vulnerabilidades **abiertas**
**Archivo:** `deploy/remote_deploy.sh:7`, `deploy/README.md:3,32,48`, `deploy/install.sh:47-50`, `tasks/audit/*.md`, `tasks/audit_2026-08-29.md`, `tasks/audit/r2/security_supply.md`, `tasks/audit/r2_verification_claude.md`.
**Evidencia (ejecutado):**
```
$ gh repo view FomoDonkey/BotStrike --json isPrivate,visibility
{ "isPrivate": false, "visibility": "PUBLIC" }
$ git rev-list --left-right --count origin/main...HEAD   →  0  0     (todo está publicado)
$ gh api repos/FomoDonkey/BotStrike/contents/deploy/remote_deploy.sh | base64 -d | sed -n 7p
HOST=${HOST:-root@100.68.139.93}
```
```
deploy/README.md:3   CT 104 `botstrike` en `proxmox-mizu` — Debian 13, IP LAN `192.168.1.204`, Tailscale.
deploy/README.md:48  El puerto 9420 sólo está abierto (ufw) para la LAN 192.168.1.0/24 y la tailnet 100.64.0.0/10.
tasks/audit/r2_verification_claude.md:122  curl http://192.168.1.204:9420/api/performance → equity, PnL, WR, Sharpe...
```
`gh api .../branches/main/protection` → `"Branch not protected"`.
**Por qué es un problema:** no es el código lo que preocupa (publicarlo es legítimo), es el **paquete completo**:
la IP Tailscale del host Proxmox con `root@`, la IP LAN del CT, el puerto, las reglas de firewall exactas, y
—lo más grave— **un informe de pentest actualizado a hoy que dice qué vulnerabilidades siguen abiertas**
(03-P2-11/12/16, SEC-01…SEC-08), con `curl` de ejemplo contra la IP real y la frase "la única barrera para live
es el token". Cualquiera que consiga un pie en la LAN (o un nodo de la tailnet: un móvil perdido, un dispositivo
compartido) no necesita reconocimiento: tiene el manual. También expone el email de commits y el hecho de que
la deploy key vive en el CT 104.
**Fix (10 minutos, altísimo retorno):** poner el repo en **privado** (o, si se quiere mantener público el código,
mover `deploy/` y `tasks/audit*` a un repo privado y purgar las IPs del historial); sustituir la IP por
`HOST=${HOST:?set HOST}`; proteger `main`. Los informes de auditoría de un sistema vivo que maneja dinero
**no se publican mientras los hallazgos estén abiertos**.
**Verificado cómo:** ejecutado (`gh repo view`, `gh api contents`, `git rev-list`, `gh api .../protection`).

### [P1] security_supply-12 — **NUEVO** · La clave privada de firma del updater está en el `env` de **todos** los pasos del release, junto a `pip install` sin pinear y 3 acciones sin pinear
**Archivo:** `.github/workflows/release.yml:16-18` (env a nivel de workflow), `:38,41,62` (acciones con tag móvil), `:47` (`pip install -r requirements.txt`), `:55` (`npm ci`), `:51` (`python scripts/build_engine.py`); `desktop/src-tauri/tauri.conf.json` (`installMode: "passive"`).
**Evidencia:**
```yaml
# release.yml:16-18 — nivel WORKFLOW ⇒ visible en TODOS los jobs y TODOS los pasos
env:
  TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
  TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
...
      - uses: dtolnay/rust-toolchain@stable     # :38  tag MÓVIL
      - uses: swatinem/rust-cache@v2            # :41  tag móvil
      - run: pip install -r requirements.txt    # :47  requirements.txt es TODO ">=" (sin pin)
      - run: npm ci                             # :55  scripts postinstall del árbol npm
      - uses: tauri-apps/tauri-action@v0        # :62  tag MÓVIL
```
```
$ gh api repos/FomoDonkey/BotStrike/actions/secrets
TAURI_SIGNING_PRIVATE_KEY            updated=2026-04-01T09:37:08Z
TAURI_SIGNING_PRIVATE_KEY_PASSWORD   updated=2026-04-01T09:41:19Z
```
**Por qué es un problema:** es el hallazgo con **mayor radio de explosión** de todo el proyecto. Esa clave firma
los updates que el cliente instala **solo** (`"installMode": "passive"`, endpoint fijado a GitHub Releases, pubkey
embebida). Cualquiera de estos puede leerla del entorno y exfiltrarla: un paquete de PyPI comprometido (versiones
**flotantes** `>=`, así que el contenido cambia sin que nadie lo apruebe), un `postinstall` de npm, o cualquiera de
las tres acciones referenciadas por **tag móvil** (`@stable`, `@v0`, `@v2` se pueden reapuntar). Con la clave, el
atacante publica un `latest.json` firmado y **ejecuta código en el PC donde están las claves de Binance**. Regla
básica: los secretos de firma se dan **solo al paso que firma**.
**Fix:** mover el bloque `env:` al `- uses: tauri-apps/tauri-action@v0` únicamente; pinear todas las acciones por
SHA completo (`uses: org/action@<sha40>`); instalar con `requirements.lock` (o `--require-hashes`) en el release;
considerar un job de firma aislado; rotar la clave si alguna vez se sospecha de una dependencia.
**Verificado cómo:** leído (workflow) + ejecutado (`gh api actions/secrets` confirma que los secretos existen y el flujo está armado).

### [P1] security_supply-13 — **NUEVO** · root ejecuta scripts de deploy desde un directorio que el usuario del servicio puede escribir → escalada a root (y el comentario de *hardening* de la unit es falso)
**Archivo:** `deploy/host_deploy.sh:11,13`, `deploy/botstrike-bridge.service:14,16,20,36,40`, `deploy/update.sh:9-10`.
**Evidencia:**
```bash
# host_deploy.sh:11,13 — root del CT ejecuta scripts que viven en /opt/botstrike/app (dueño: botstrike)
pct exec "$CT" -- bash "$APP/deploy/update.sh"
pct exec "$CT" -- bash "$APP/deploy/install.sh"
```
```ini
# botstrike-bridge.service
User=botstrike                                   # :14
WorkingDirectory=/opt/botstrike/app              # :16
# No __pycache__ writes: the app must only write under data/ and logs/ (see ReadWritePaths)   # :20  ← FALSO
ProtectSystem=full                               # :36
ReadWritePaths=/opt/botstrike/app/data /opt/botstrike/app/logs   # :40
```
Doc oficial (man7 `systemd.exec(5)`, consultada): `ProtectSystem=full` monta en solo-lectura **"/usr/ and the
boot loader directories (/boot and /efi)"** más **"/etc/ ... read-only, too"**. `/opt` **no** entra. Y
`ReadWritePaths=` solo sirve para *"exclude specific directories from being made read-only"*: no convierte en
solo-lectura lo que no se ha protegido. Por tanto el proceso del bot **puede escribir todo su propio código**
en `/opt/botstrike/app` (`server/`, `strategies/`, `risk/`, **y `deploy/`**).
**Por qué es un problema:** dos cosas, y la segunda es la seria.
1. El comentario de la línea 20 afirma una garantía que la configuración **no da**. Quien lea la unit creerá que el código es inmutable en runtime. No lo es.
2. **Cadena de escalada:** cualquier escritura arbitraria como `botstrike` (por ejemplo vía una RCE en el bridge, o `git reset --hard origin/main` desde un repo **público** cuyo `main` no está protegido — ver SEC-11) coloca contenido en `deploy/update.sh`; en el siguiente deploy **root lo ejecuta** (`host_deploy.sh:11`). Compromiso de la app → compromiso de root del CT. Además el bot puede modificar sus propias estrategias/riesgo en caliente sin dejar rastro en git.
Ref: **03-P2-12** ya señaló "`ProtectSystem=full` deja /opt RW", pero **no** conectó la escalada ni el comentario falso; sigue abierto.
**Fix:** `ProtectSystem=strict` + `ReadWritePaths=` solo `data/` y `logs/` (entonces sí es cierto lo que dice el comentario); root debe ejecutar los scripts desde una copia bajo `/root` o `/usr/local/sbin` con dueño `root:root` y modo `0755`, no desde el árbol del servicio; corregir/borrar el comentario de la línea 20.
**Verificado cómo:** leído (unit + scripts) + doc oficial systemd (man7.org, citada literalmente).

### [P1] security_supply-14 — **NUEVO** · CI lleva **10 push en rojo** (la suite ni colecciona) y `update.sh` despliega `origin/main` a producción sin ninguna puerta
**Archivo:** `.github/workflows/ci.yml:59-72`, `deploy/update.sh:5-24`, `requirements.txt:1-15`, `requirements-dev.txt`.
**Evidencia (ejecutado):**
```
$ gh run list -R FomoDonkey/BotStrike -w CI -L 30
failure 2026-08-30T23:26:09Z   failure 2026-08-30T23:14:55Z   failure 2026-08-30T23:04:54Z
failure 2026-08-30T23:02:44Z   failure 2026-08-30T23:00:54Z   failure 2026-08-30T22:57:57Z
failure 2026-08-30T22:55:41Z   failure 2026-08-30T22:17:21Z   failure 2026-08-30T02:41:46Z
failure 2026-08-30T02:39:50Z   success 2026-08-29T21:41:08Z   ← último verde
$ gh run view 33341809403 --log-failed | tail -6
ERROR tests/test_bridge_round2.py - RuntimeError: The starlette.testclient module requires the httpx2 package
!!!!!!!!!! stopping after 1 failures !!!!!!!!!!
##[error]Process completed with exit code 1.
```
```yaml
# ci.yml:59-62 — instala el requirements.txt FLOTANTE y solo pytest (nunca requirements-dev.txt)
- run: |
    pip install -r requirements.txt
    pip install pytest
```
```bash
# update.sh:9-22 — despliega y reinicia pase lo que pase; no consulta CI ni corre tests
git reset -q --hard origin/main
uv pip sync -q --python .venv/bin/python requirements.lock
systemctl restart botstrike-bridge
```
**Por qué es un problema:** el único control automático del proyecto lleva **~2 días y 10 commits caído**, y en
esa ventana se ha desplegado al CT de producción. Causa raíz: `requirements.txt` sin pines (`starlette` sube a
1.6, que exige `httpx2` para `TestClient`) → el hallazgo de cadena de suministro se ha materializado ya, no es
teórico. La consecuencia práctica es que "92/92" o "100/100" **no lo está comprobando nadie de forma automática**
en ninguna de las tres superficies (CI, CT, release), y `release.yml` **ni siquiera depende de CI**: un
`git tag v*` construye y publica un instalador firmado sin haber pasado un solo test.
**Fix:** (1) `pip install -r requirements-dev.txt` en CI y añadir `httpx2` allí; (2) que CI instale
`requirements.lock` (lo que corre en el CT), no el flotante; (3) `needs: [check-backend]` en el job de release;
(4) puerta en `update.sh`: correr `pytest` antes de `systemctl restart` y abortar si falla; (5) proteger `main`
exigiendo CI verde.
**Verificado cómo:** ejecutado (`gh run list`, `gh run view --log-failed`) + leído.

### [P2] security_supply-15 — **NUEVO** · Cero escaneo de vulnerabilidades de dependencias; 2 avisos *high* vivos en dependencias de **producción** del desktop
**Archivo:** `.github/workflows/ci.yml` (sin `pip-audit`/`npm audit`/`cargo audit`), ausencia de `.github/dependabot.yml`, `desktop/package.json:24`.
**Evidencia (ejecutado):**
```
$ ls .github/dependabot.yml                    → No such file or directory
$ grep -rn "pip-audit\|npm audit\|cargo audit\|codeql" .github/   → 0 resultados

$ cd desktop && npm audit --omit=dev
{'info':0,'low':0,'moderate':0,'high':2,'critical':0,'total':2}
react-router      high   6.0.0 - 7.18.1      (instalado: 7.13.2)
react-router-dom  high   7.0.0-pre.0 - 7.14.1 (instalado: 7.13.2)

$ npm audit          # incluyendo dev (cadena de build del instalador firmado)
high: 7  low: 1     → brace-expansion, js-yaml, nanoid, postcss, vite, react-router(-dom), @babel/core

$ OSV batch API sobre requirements.lock (86 paquetes)   → 0 vulnerables   ✔
$ OSV batch API sobre el intérprete py-3.12 con el que se construye el sidecar local (328 paquetes)
  → 36 vulnerables, entre ellos dependencias reales del proyecto:
    starlette==0.41.3 (14 avisos)  urllib3==2.6.0 (6)  requests==2.32.5 (2)  python-dotenv==1.0.1 (2)  pillow==11.3.0 (37)
```
**Por qué es un problema:** no hay ningún mecanismo —ni automático ni manual— que avise de un CVE en una
dependencia. Hoy el `requirements.lock` está limpio (buena noticia, verificada), pero eso es suerte, no proceso:
en la superficie que **sí se envía a usuarios** (el bundle del desktop) hay dos avisos *high* vivos, y el
intérprete con el que se compila el sidecar local acumula 36 paquetes vulnerables. Sin Dependabot ni auditoría
en CI, el día que salga un CVE explotable en `fastapi`/`starlette`/`aiohttp` nadie se entera.
**Fix:** `.github/dependabot.yml` (pip + npm + cargo + github-actions); job de CI con `pip-audit -r requirements.lock`,
`npm audit --omit=dev --audit-level=high` y `cargo audit`; subir `react-router-dom` ≥ 7.14.2; construir el sidecar
en un venv dedicado del proyecto, no contra el intérprete global.
**Verificado cómo:** ejecutado (`npm audit --json`, OSV `querybatch` sobre lock y sobre `pip freeze`, `ls`, grep).

### [P2] security_supply-16 — **NUEVO** · `install.sh` instala el toolchain con `curl | sh` sin verificar y luego instala el `requirements.txt` **flotante** (no el lock), en cada deploy
**Archivo:** `deploy/install.sh:18,27`, `deploy/host_deploy.sh:13`.
**Evidencia:**
```bash
# install.sh:18 — sin checksum, sin firma, sin versión fijada; y el fallo se traga con || true
su - botstrike -c 'command -v ~/.local/bin/uv >/dev/null || (curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1); ~/.local/bin/uv python install 3.12 >/dev/null 2>&1 || true'
# install.sh:27 — el arranque del CT NO usa el lock, usa el flotante
uv pip install -q --python .venv/bin/python -r requirements.txt
# host_deploy.sh:13 — install.sh se re-ejecuta ENTERO en cada deploy (incluido ufw --force reset)
pct exec "$CT" -- bash "$APP/deploy/install.sh"
```
**Por qué es un problema:** dos agujeros de cadena de suministro distintos. (a) `curl | sh` de `astral.sh` sin
pinear versión ni verificar hash: un compromiso de ese dominio o un MITM de TLS ejecuta código arbitrario como
`botstrike`, y desde ahí SEC-13 lleva a root. (b) La primera instalación del CT resuelve `numpy>=1.24`,
`pandas>=2.0`, `websockets>=12` a "lo último de ese día": el entorno del servidor depende de la fecha en que se
ejecutó el script — que es exactamente cómo se ha llegado a starlette 1.6 y a CI en rojo (SEC-14). Además
`host_deploy.sh` corre `install.sh` en **cada** deploy → `ufw --force reset` deja el CT unos milisegundos sin
firewall en cada push, con el servicio ya escuchando. Extiende **03-P2-11**, que sigue abierto (sin `known_hosts`,
`|| true`, `chrony` inútil en LXC).
**Fix:** pinear el instalador de `uv` a una versión + verificar SHA256 (o instalarlo por `apt`/binario descargado y
comprobado); usar `requirements.lock` también en `install.sh`; separar `install.sh` (una vez) de `update.sh` (cada
deploy) para que `host_deploy.sh` no resetee el firewall en cada push.
**Verificado cómo:** leído (scripts) + ejecutado (`grep -n`).

### [P2] security_supply-17 — **NUEVO** · Se commitea un bundle JS ya construido (870 KB) que se despliega por git y **nadie verifica que corresponda al código fuente**
**Archivo:** `server/webui/index.html`, `server/webui/assets/index-DBh0rRsX.js` (869.794 B), `.../index-yZMUyf0H.css`, `.../lightweight-charts.production-BsJi6uPl.js`; `server/bridge.py:1560-1562`; `.github/workflows/ci.yml:46-48`.
**Evidencia (ejecutado):**
```
$ git ls-files server/ | grep webui
server/webui/assets/index-DBh0rRsX.js
server/webui/assets/index-yZMUyf0H.css
server/webui/assets/lightweight-charts.production-BsJi6uPl.js
server/webui/favicon.svg   server/webui/icons.svg   server/webui/index.html
$ git check-ignore -v server/webui        → (no ignorado: se commitea a propósito)
```
CI hace `npx vite build` (`ci.yml:48`) pero **descarta el resultado**: nunca lo compara con el bundle commiteado.
**Por qué es un problema:** ese fichero es el que ejecuta el navegador del operador, el que pide el token de live
y el que lo guarda en `localStorage` — y llega a producción por `git reset --hard origin/main` desde un repo
**público** con `main` **sin protección de rama** (SEC-11). Un cambio en ese `.js` (accidental, o de cualquiera con
push) sirve código distinto del TypeScript que se audita y revisa, sin que ninguna herramienta lo note. Es la
definición de un hueco de procedencia en la cadena de suministro. (Hoy está sincronizado: fuente y bundle se
tocaron en el mismo commit `7e3e42b` — buena higiene, pero por disciplina, no por control.)
**Fix:** o no commitear el bundle (construirlo en el deploy / servirlo desde un artefacto de release firmado), o
añadir a CI un paso que reconstruya y falle si `git diff --exit-code server/webui` no está limpio.
**Verificado cómo:** ejecutado (`git ls-files`, `git check-ignore`, `git log -1 -- server/webui` vs `-- desktop/src`).

### [P2] security_supply-18 — **NUEVO** · Deriva de versiones en el manifiesto de release: un release cortado hoy publicaría código 2.13.1 etiquetado **v2.12.0**, y el updater no lo ofrecería
**Archivo:** `desktop/src-tauri/tauri.conf.json:4` (`2.12.0`), `desktop/src-tauri/Cargo.toml:3` (`2.12.0`), `desktop/package.json:4` (`2.13.1`), `server/bridge.py:44` (`BRIDGE_VERSION = "2.13.1"`), `.github/workflows/release.yml:67` (`tagName: v__VERSION__`).
**Evidencia (ejecutado):**
```
desktop/package.json:4              "version": "2.13.1"
desktop/src-tauri/tauri.conf.json:4 "version": "2.12.0"     ← el que usa el bundler y el updater
desktop/src-tauri/Cargo.toml:3      version = "2.12.0"
server/bridge.py:44                 BRIDGE_VERSION = "2.13.1"
$ git tag --sort=-v:refname | head -3     → v2.11.1  v2.11.0  v2.10.3
```
**Por qué es un problema:** el updater de Tauri compara la versión **del bundle** (`tauri.conf.json`/`Cargo.toml`)
con la del `latest.json`. Con la deriva actual, `release.yml` etiquetaría `v2.12.0` un binario que contiene el
código 2.13.1 (incluida la UI web y los cambios de bridge). Consecuencias: los usuarios ven una versión que no es
la que ejecutan (imposible correlacionar un incidente con un commit), y cualquier cliente que ya se creyera
≥2.12.0 **no recibiría la actualización**, lo que en la práctica anula el canal por el que se distribuirían
precisamente los parches de SEC-09/10.
**Fix:** una sola fuente de verdad para la versión (script que sincronice `package.json`/`tauri.conf.json`/`Cargo.toml`/`BRIDGE_VERSION`) y un check en CI que falle si divergen.
**Verificado cómo:** ejecutado (grep de las cuatro fuentes + `git tag`).

### [P3] security_supply-19 — `UMask` sin fijar (0022 por defecto ⇒ base de datos de trades y logs en 0644) y el resto del endurecimiento de systemd sigue sin aplicarse
**Archivo:** `deploy/botstrike-bridge.service:33-41`.
**Evidencia:** la unit trae `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`, `ProtectHome=read-only`,
`ReadWritePaths`, `LimitNOFILE`. **No** trae `UMask=`, `PrivateDevices=`, `RestrictAddressFamilies=`,
`CapabilityBoundingSet=`, `SystemCallFilter=`, `RestrictSUIDSGID=`, `LockPersonality=`,
`ProtectKernelTunables=`, `ProtectControlGroups=`, `RestrictNamespaces=`, ni `TimeZone`/`TZ`.
Doc oficial (man7 `systemd.exec(5)`): `UMask=` *"Defaults to 0022 for system units"* → todo lo que crea el
servicio bajo `data/` y `logs/` (la sqlite con el historial completo de operaciones, `metrics.jsonl`) nace
**legible por cualquier usuario del contenedor**.
**Por qué es un problema:** hoy el impacto es bajo (en el CT solo existen `root` y `botstrike`), pero es
higiene gratis y es exactamente lo que pedía **03-P2-12**, que **sigue abierto**.
**Fix:** `UMask=0077` + el bloque de endurecimiento anterior; `ProtectSystem=strict` (ver SEC-13).
**Verificado cómo:** leído (unit) + doc oficial systemd consultada.

### [P3] security_supply-20 — `.env.example` sigue sin documentar ninguna variable relevante para la seguridad (**03-P2 abierto**), y hay artefactos sin `.gitignore` en un repo público
**Archivo:** `.env.example:1-13`, `deploy/install.sh:33-35`, `.gitignore`.
**Evidencia (ejecutado):**
```
$ sed -E 's/=.*/=<REDACTED>/' .env          # lo que el .env REAL necesita
STRIKE_PUBLIC_KEY  STRIKE_PRIVATE_KEY  BINANCE_API_KEY  BINANCE_API_SECRET
GEMINI_API_KEY  TELEGRAM_BOT_TOKEN  TELEGRAM_CHAT_ID
$ cat .env.example                          # lo que install.sh copia a .env
STRIKE_PUBLIC_KEY / STRIKE_PRIVATE_KEY / URLs comentadas / TELEGRAM_* comentado
# FALTAN: BOTSTRIKE_AUTH_TOKEN, BINANCE_API_KEY/SECRET, HYPERLIQUID_PRIVATE_KEY, HYPERLIQUID_WALLET_ADDRESS
$ for f in smoke_live.json BotStrike_Documentacion.pdf; do git check-ignore -q $f || echo "NOT IGNORED: $f"; done
NOT IGNORED: smoke_live.json            (1,5 MB, salida de una corrida en modo "live")
NOT IGNORED: BotStrike_Documentacion.pdf
```
**Por qué es un problema:** `install.sh:33` hace `cp .env.example .env` y luego dice *"Edit .env with your API
keys (Binance read-only keys are enough for paper)"* — pero en ese fichero **no hay ninguna línea de Binance que
editar**, ni se menciona `BOTSTRIKE_AUTH_TOKEN`, que es **la** variable de la que depende toda la autenticación en
`0.0.0.0`. Una instalación limpia queda con un token aleatorio por proceso que nadie conoce (falla cerrado, bien)
y con el operador buscando a ciegas — que es justo la situación que empuja a usar `--dev` (SEC-05) o a abrir
cosas. `HYPERLIQUID_PRIVATE_KEY` (una clave de wallet real) tampoco está documentada. Aparte, en un repo
**público** con `git add -A` habitual, dos ficheros grandes sin ignorar son un accidente esperando a ocurrir.
Es **03-P2 (`.env.example`) sigue abierto** — R1 ya lo señaló y no se ha tocado.
**Fix:** completar `.env.example` con **todas** las variables (secretos con placeholder vacío) y un comentario que
explique que `BOTSTRIKE_AUTH_TOKEN` es obligatorio en bind no-loopback; añadir `smoke_live.json`, `*.pdf` y
`tasks/audit/r2/` (o lo que no deba publicarse) al `.gitignore`.
**Verificado cómo:** ejecutado (`sed` sobre `.env`, `cat .env.example`, `git check-ignore`).

---

## Verificación de los fixes de la Ronda 1

| Fix R1 | Estado | Nota (verificado) |
|--------|--------|-------------------|
| 03-P0-1 autostart `BOTSTRIKE_AUTOSTART` | **Correcto** | `lifespan` arranca paper/dry_run y **rechaza `live`** (`bridge.py:1168-1178`). Sin regresión. |
| 03-P0-2 token desde `.env`, no expuesto en `0.0.0.0` | **Correcto pero incompleto** | `auth_token:null` en no-loopback ✔ y `POST .../start?mode=live` sin token → **401** (reproducido). Pero: query string → logs (SEC-01), `--dev` lo anula del todo (SEC-05), GET/WS siguen abiertos (SEC-04), y ahora la UI entera también (SEC-10). |
| 03-P1 health real + watchdog | **Correcto** | `/api/health` 503 real; watchdog con backoff y `os._exit(3)`. Sin regresión. |
| 03-P1 backtest fuera del loop + validación de símbolo | **Correcto** | `asyncio.to_thread`, símbolo validado (400), 1 concurrente (409). |
| 03-P1-9 pinning / quitar streamlit del server | **Incompleto** | Hay lock (0 CVEs ✔) pero `install.sh:27` sigue instalando el `requirements.txt` flotante, CI también, y el lock mantiene streamlit/plotly/altair/pydeck/gitpython en el servidor (SEC-02, SEC-16). |
| 03-P2-11 `install.sh` (known_hosts, ufw reset, chrony) | **Abierto** | Sin cambios: `chrony`, `ufw --force reset` en cada deploy, `\|\| true`, sin `known_hosts` (+ `curl \| sh`, SEC-16). |
| 03-P2-12 endurecer systemd | **Abierto y peor de lo que decía** | `ProtectSystem=full` deja `/opt` escribible → escalada a root vía `deploy/*.sh` (SEC-13); sin `UMask` (SEC-19); `EnvironmentFile` sigue (SEC-08). El comentario de la línea 20 afirma lo contrario de lo que hace. |
| 03-P2-16 `/ws/*` sin auth | **Abierto y ampliado** | Sigue sin token **y** sin validación de `Origin` → lectura cross-origin desde cualquier web (SEC-09). |
| 03-P2 `.env.example` incompleto | **Abierto** | Sin cambios (SEC-20). |
| 03-P3-19 `/docs` públicos + token en query | **Mitad hecho** | `/docs` ya devuelve 404 en no-loopback ✔ (salvo con `--dev`, SEC-05). El token por query string sigue y ha escalado a P1 (SEC-01). |

**Regresiones detectadas: ninguna en el comportamiento arreglado.** Pero sí hay **dos superficies nuevas
introducidas después de R1**: la UI web montada en `/` (SEC-10) y CI en rojo permanente (SEC-14).

---

## Tabla resumen

| ID | Sev | Título | Archivo:línea |
|----|-----|--------|---------------|
| security_supply-01 | **P1** | Token de live en query string → journald en claro + LAN sin TLS | `server/bridge.py:1590`; `desktop/src/lib/api.ts:232` |
| security_supply-05 | **P1** | `--dev` = bypass **total** de la auth (token regalado + mutaciones sin credencial) | `server/bridge.py:53,1576,1591` |
| security_supply-09 | **P1** | **NUEVO** CSRF drive-by + DNS-rebinding → robo del token → `mode=live`; WS cross-origin | `server/bridge.py:1194,1217,1228,1308` |
| security_supply-10 | **P1** | **NUEVO** Consola de operador completa servida sin auth en HTTP plano a la LAN | `server/bridge.py:1560` |
| security_supply-11 | **P1** | **NUEVO** Repo **público** con IPs de la infra y la lista de vulnerabilidades abiertas | `deploy/remote_deploy.sh:7`; `tasks/audit/**` |
| security_supply-12 | **P1** | **NUEVO** Clave de firma del updater en el `env` de todo el release; acciones y pip sin pinear | `.github/workflows/release.yml:16-18,38,41,47,62` |
| security_supply-13 | **P1** | **NUEVO** root ejecuta scripts desde `/opt` escribible por el servicio → escalada; comentario falso | `deploy/host_deploy.sh:11`; `botstrike-bridge.service:20,36,40` |
| security_supply-14 | **P1** | **NUEVO** CI en rojo 10 push seguidos; `update.sh` despliega igual; release no depende de CI | `.github/workflows/ci.yml:59`; `deploy/update.sh:9` |
| security_supply-02 | P2 | Sin puerta de calidad en el CT (falta `httpx2`) + streamlit/plotly/gitpython en el servidor | `requirements.lock`; `deploy/update.sh:13` |
| security_supply-03 | P2 | Sin kill-switch de despliegue para live | `server/bridge.py:1269` |
| security_supply-04 | P2 | GET y `/ws/*` sin auth en `0.0.0.0` → trades/PnL/config a la LAN | `server/bridge.py:1362,1314,1228` |
| security_supply-06 | P2 | No se verifica el alcance de la API key de Binance | `deploy/install.sh:34`; `exchange/` |
| security_supply-15 | P2 | **NUEVO** Cero escaneo de dependencias; 2 *high* en deps de producción del desktop | `.github/workflows/ci.yml`; `desktop/package.json:24` |
| security_supply-16 | P2 | **NUEVO** `curl \| sh` sin verificar + `requirements.txt` flotante en el arranque del CT | `deploy/install.sh:18,27` |
| security_supply-17 | P2 | **NUEVO** Bundle JS commiteado y desplegado por git sin verificar procedencia | `server/webui/assets/*.js` |
| security_supply-18 | P2 | **NUEVO** Deriva de versiones: release hoy = código 2.13.1 etiquetado v2.12.0 | `tauri.conf.json:4` vs `package.json:4` |
| security_supply-07 | P3 | `GEMINI_API_KEY` secreto muerto en `.env` | `.env`; `archive/core/ai_analyst.py` |
| security_supply-08 | P3 | `EnvironmentFile=.env` duplica los secretos en el entorno del proceso | `botstrike-bridge.service:17` |
| security_supply-19 | P3 | `UMask` sin fijar (trade DB y logs 0644) + resto del endurecimiento sin aplicar | `botstrike-bridge.service:33-41` |
| security_supply-20 | P3 | `.env.example` sin `BOTSTRIKE_AUTH_TOKEN`/`BINANCE_*`; artefactos sin `.gitignore` | `.env.example`; `.gitignore` |

**Hallazgos de R1 reconfirmados como abiertos:** 03-P2-11, 03-P2-12, 03-P2-16, 03-P2 (`.env.example`), 03-P1-9 (residual), 03-P3-19 (mitad).

**Orden de ataque sugerido (coste/beneficio):**
1. Repo a **privado** (SEC-11) — 2 minutos, quita el manual de ataque de internet.
2. Mover el `env:` de la clave de firma al paso de `tauri-action` + pinear acciones por SHA (SEC-12) — 10 minutos, quita el peor radio de explosión.
3. Cabecera custom obligatoria en toda mutación + allowlist de `Host` + `Origin` en WS (SEC-09) — mata CSRF y rebinding de golpe, y de paso SEC-01.
4. `_EXPOSE_TOKEN` a nivel de módulo (SEC-05) y `access_log=False`/redacción (SEC-01).
5. Arreglar CI (`httpx2` + `requirements-dev.txt`) y poner puerta en `update.sh` (SEC-14) — sin esto, ningún fix está verificado de forma automática.
6. `ProtectSystem=strict` + scripts de deploy fuera de `/opt` (SEC-13); `BOTSTRIKE_ALLOW_LIVE=0` (SEC-03).
7. TLS o solo-tailnet para el 9420, y no pedir el token de live en una página HTTP (SEC-10).

---

## Veredicto (10 líneas)

1. **Los P0 de la Ronda 1 están bien cerrados y sin regresión** (autostart, token oculto en `0.0.0.0`, health/watchdog real, backtest fuera del loop): lo comprobé ejecutando el bridge, no leyéndolo.
2. **No hay ni un secreto en el historial de git**: `.env` jamás se commiteó, el sidecar PyInstaller no lo empaqueta, `serialize_settings` no filtra y Telegram no expone su token. Esto está genuinamente bien hecho.
3. Pero R1 auditó el perímetro equivocado: **la superficie que más ha crecido desde entonces no estaba mirada**. Hoy `http://192.168.1.204:9420/` sirve **la terminal de trading entera, sin credencial, en HTTP plano** a toda la LAN — verificado desde mi PC, sin token (SEC-10).
4. El hallazgo nuevo más serio a nivel técnico es **SEC-09**: reproducido, un `POST` desde `Origin: https://evil.example` **sin token para el bot** devolvió 200 y lo paró; y como el bridge **no valida `Host`**, un DNS-rebinding convierte eso en robo del token y `mode=live`. Cualquier web que el operador visite.
5. El de mayor radio de explosión es **SEC-12**: `TAURI_SIGNING_PRIVATE_KEY` está en el `env` de **todos** los pasos del release, junto a un `pip install` sin pinear y tres acciones con tag móvil. Quien la robe firma un update que se auto-instala en la máquina donde viven las claves de Binance.
6. **SEC-11** convierte todo lo anterior en un manual público: el repo es **PUBLIC** y publica la IP Tailscale del host con `root@`, la IP LAN del CT, las reglas de ufw y —peor— estos mismos informes con la lista de lo que sigue **abierto**. Poner el repo en privado es el mejor retorno por minuto de toda la lista.
7. **SEC-14 es el que explica por qué nada de esto se detectó:** CI lleva **10 push en rojo** (la suite ni colecciona, por `httpx2`), `update.sh` despliega a producción igualmente y `release.yml` ni siquiera depende de CI. No hay puerta de calidad en ninguna de las tres superficies.
8. En cadena de suministro la foto es mixta y honesta: **0 CVEs conocidos** en las 86 versiones del `requirements.lock` (OSV, ejecutado) y la alarma de "pandas 3.0 rompe el bot" quedó **refutada con datos** (100/100). Lo real es la ausencia total de escaneo (sin Dependabot, sin `pip-audit`, sin `npm audit`) y 2 avisos *high* vivos en dependencias de producción del desktop.
9. Endurecimiento del host: `ProtectSystem=full` **no** protege `/opt`, así que el servicio puede reescribir su propio código y sus `deploy/*.sh` — que root ejecuta en el siguiente despliegue (**SEC-13**). El comentario de la unit que promete lo contrario es falso y hay que corregirlo.
10. **Conclusión:** ninguno de estos hallazgos ha causado una pérdida todavía (journald: **0** apariciones de `token=` en 7 días, verificado) y el sistema está en paper. Pero con SEC-09/10/11/12/13/14 abiertos, **este sistema no está listo para 24/7 desatendido ni para tocar dinero real**; los seis primeros puntos del orden de ataque son unas pocas horas de trabajo y cambian el veredicto por completo.
