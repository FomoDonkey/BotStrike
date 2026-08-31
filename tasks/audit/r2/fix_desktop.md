# Auditoría R2 — AREA `fix_desktop`

Revisión adversarial de los cambios del desktop 2.12.0 (commit `ffacf4a`) tal como
están **hoy en HEAD** (`7b9da43`, la app ya va por 2.13.x y `config.ts`/`api.ts`/
`ConnectionOverlay.tsx` recibieron cambios posteriores en `6d0256d` — se auditan en su
estado actual, señalando cuándo el defecto nació en `ffacf4a` y cuándo lo introdujo el
webui posterior).

Alcance: `desktop/src/lib/{config,api,ws,engine,constants}.ts`,
`desktop/src/hooks/useWebSocket.ts`, `desktop/src/stores/systemStore.ts`,
`desktop/src/components/shared/ConnectionOverlay.tsx`,
`desktop/src/pages/settings/SettingsPage.tsx`,
`desktop/src-tauri/src/lib.rs`, `desktop/src-tauri/tauri.conf.json`,
`desktop/src-tauri/capabilities/default.json`.

_En construcción — los hallazgos se añaden según se confirman._

## Verificación base (ejecutada hoy, 2026-08-31)

```
$ cd desktop && npm run lint      →  eslint . (sin salida)      exit 0
$ cd desktop && npm run build     →  tsc -b && vite build
                                     ✓ built in 11.26s          exit 0
                                     dist/assets/index-DBh0rRsX.js  869.79 kB (warning de tamaño, ya conocido)
```
La ronda 1 dejó lint y build limpios y **siguen limpios**. Confirmado.

## Hallazgos

### [P1] fix_desktop-01 — `normalizeBridgeUrl` reescribe silenciosamente `:443`/`:80` a `:9420`: un bridge detrás de HTTPS es inalcanzable y el usuario no ve por qué
**Archivo:** `desktop/src/lib/config.ts:56`

**Evidencia:**
```ts
if (u.protocol !== "http:" && u.protocol !== "https:") return null;
if (!u.hostname) return null;
if (u.username || u.password) return null;
if (!u.port) u.port = String(DEFAULT_BRIDGE_PORT);
return u.origin;
```
Ejecutado con node (`new URL` es el mismo WHATWG URL del WebView):
```
"https://bridge.example.com:443" | port tras new URL = ""  -> https://bridge.example.com:9420
"http://host:80"                 | port tras new URL = ""  -> http://host:9420
"https://bridge.tailnet.ts.net"  | port tras new URL = ""  -> https://bridge.tailnet.ts.net:9420
```

**Por qué:** `new URL()` **elimina el puerto por defecto del esquema** (443 para https, 80 para http),
así que `u.port` queda `""` y la línea 56 no distingue "el usuario no puso puerto" de "el usuario puso
el puerto por defecto explícitamente". Resultado: el usuario teclea `https://botstrike.midominio.com:443`
(o `https://host` a secas) y la app guarda `…:9420`, muestra en el preview "will be saved as https://…:9420"
en letra de 10 px y luego falla con "Cannot reach https://…:9420". **No hay ninguna forma de configurar un
bridge detrás de un reverse proxy / Cloudflare Tunnel / nginx TLS en 443**, que es exactamente el despliegue
que `validateBridgeUrl` anuncia al aceptar `https://`. `deploy/README.md:32-33` documenta `http(s)://host:puerto`
como entrada válida, así que la forma está publicada y rota.

**Fix:** distinguir "sin puerto" del puerto por defecto antes de que `new URL` lo normalice:
```ts
const hadExplicitPort = /:\d+(?:\/|$|\?|#)/.test(s.replace(/^[a-z][a-z0-9+.-]*:\/\//i, ""));
if (!u.port && !hadExplicitPort && u.protocol === "http:") u.port = String(DEFAULT_BRIDGE_PORT);
```
(o simplemente: aplicar el default 9420 **sólo** cuando el input no traía esquema, que es el caso
`192.168.1.204` / `host:9420` para el que se diseñó el atajo; nunca para un `https://` explícito).

**Verificado cómo:** leído `config.ts:43-58` + reproducido el comportamiento de `new URL` en node con 8 casos
(salida arriba). No es teoría: la reescritura ocurre.

---

### [P1] fix_desktop-02 — La CSP fija el puerto 9420 en `connect-src`: cualquier bridge en otro puerto queda bloqueado por el navegador y el error se disfraza de "red caída"
**Archivo:** `desktop/src-tauri/tauri.conf.json:31` (y `:42` para devCsp)

**Evidencia:**
```json
"connect-src": "'self' ipc: http://ipc.localhost http://127.0.0.1:9420 ws://127.0.0.1:9420 http://localhost:9420 ws://localhost:9420 http://*:9420 ws://*:9420 https://*:9420 wss://*:9420",
```
frente a `desktop/src/lib/config.ts:40-41` (docstring de la función que valida la entrada):
```
"192.168.1.204" | "host:9420" | "http://host:9420/x" | "https://bridge.tailnet.ts.net"
```
y `SettingsPage.tsx:114`: `Bridge URL (host[:port], http:// o https://)`.

**Por qué:** `http://*:9420` **sí es sintaxis CSP válida** (CSP3 §`host-source = [ scheme-part "://" ] host-part [ ":" port-part ] [ path-part ]`,
con `host-part = "*" / …` y `port-part = 1*DIGIT / "*"`) — el comodín de host no es el problema. El problema es
que `port-part` con dígitos **sólo casa ese puerto exacto**: `https://*:9420` no casa `https://host` (443).
Como la UI permite y normaliza puertos arbitrarios (`host:8080` → `http://host:8080`, verificado en node),
Settings deja configurar un endpoint que el WebView bloqueará. Y el fallo es indistinguible de un problema
de red: el `fetch` bloqueado por CSP lanza `TypeError`, que `api.ts:214-217` convierte en
`Cannot reach http://host:8080 (Failed to fetch)`, y la overlay dice "Check the server, the firewall (ufw :9420)".
El usuario perseguirá un firewall que está bien. Nota adicional: el comodín de host hace que la CSP tampoco
esté comprando seguridad real (permite hablar con *cualquier* host en 9420), sólo está estorbando.

**Fix:** o (a) abrir el puerto — `http://*:* https://*:* ws://*:* wss://*:*` en `connect-src` (con
`default-src 'self'` + `script-src 'self'` + `object-src 'none'` intactos el riesgo residual es exfiltración,
no ejecución); o (b) si se quiere mantener el bloqueo duro, hacer que `normalizeBridgeUrl` **rechace**
cualquier puerto ≠ 9420 con un mensaje explícito ("only port 9420 is supported by this build") en vez de
aceptarlo y morir después. Lo que no puede quedarse es la combinación actual: aceptar y luego bloquear en silencio.

**Verificado cómo:** leído `tauri.conf.json` + contrastada la gramática `host-source`/`port-part` contra
la spec oficial **W3C CSP Level 3** (WebFetch a https://www.w3.org/TR/CSP3/, ABNF citada arriba: puerto
explícito ⇒ coincidencia exacta) + comprobado en node que `normalizeBridgeUrl("http://192.168.1.5:8080")`
devuelve `http://192.168.1.5:8080`. No ejecutado en runtime (requiere `tauri dev`).

---

### [P1] fix_desktop-03 — `probeBridge` trata el HTTP 503 de `/api/health` como "bridge inalcanzable": con el engine caído la app muestra el diálogo de setup y "Test connection" falla en rojo, aunque el bridge responde
**Archivo:** `desktop/src/lib/api.ts:203-205,272-274`; `desktop/src/components/shared/ConnectionOverlay.tsx:27-38`; `desktop/src/pages/settings/SettingsPage.tsx:77-82`

**Evidencia:** `api.ts` (introducido en `ffacf4a`):
```ts
if (!res.ok) {
  throw new ApiError(errMsg ?? `HTTP ${res.status} ${res.statusText || ""}`.trim() + ` (${path})`, res.status);
}
```
`server/bridge.py:1253-1259` (el "real health" que introdujo la propia ronda 1, `b3dbf75`):
```python
@app.get("/api/health")
async def health(response: Response):
    snap = _health_snapshot()
    response.status_code = 503 if snap["degraded"] else 200
    return snap
```
`server/bridge.py:1003-1010`: `degraded` ⇐ `engine_expected and not engine_running`, o `stale_ticks`,
o `no_ticks` (`HEALTH_STALE_TICK_SEC = 120.0`).
`ConnectionOverlay.tsx:36-38`: `.catch(() => { if (!cancelled) setPhase("setup"); })`.

**Por qué:** el 503 significa **"el bridge está vivo y te está diciendo que el engine no lo está"** — es
justo la señal de valor que añadió la ronda 1. El desktop la interpreta como "no hay bridge":
(1) al arrancar, la overlay salta a `setup` ("Select your exchange to get started") y **no llama a
`startWebSockets()`**, así que ni siquiera abre los WS que le mostrarían los logs del engine muerto;
(2) Settings → **Test connection** pinta en rojo `HTTP 503 (/api/health)` junto a una URL correcta, con lo
que el usuario va a toquetear URL/token/firewall en vez de mirar el engine. Es una regresión de
interacción entre dos fixes de la ronda 1 (bridge: 503 real; desktop: no-2xx ⇒ throw) que nadie cruzó.

**Fix:** en `probeBridge` (y sólo ahí) aceptar el 503 de `/api/health` como respuesta válida y devolver
el snapshot — el propio payload trae `status:"degraded"` y `reasons[]`:
```ts
export async function probeBridge(baseUrl: string): Promise<HealthResponse> {
  try { return await request<HealthResponse>("/api/health", { baseUrl, timeoutMs: HEALTH_TIMEOUT_MS }); }
  catch (e) { if (e instanceof ApiError && e.status === 503) return e.body as HealthResponse; throw e; }
}
```
(requiere adjuntar el body al `ApiError`, hoy se descarta). Y en la overlay/Settings pintar
`degraded → ámbar + reasons`, no rojo.

**Verificado cómo:** leído `api.ts:185-222,272-274`, `bridge.py:995-1026,1253-1259`, `ConnectionOverlay.tsx:25-40`,
`SettingsPage.tsx:70-83`. La cadena 503 → `!res.ok` → `throw` → `.catch` → `setPhase("setup")` es directa
en el código; no ejecutado en runtime.

---

### [P1] fix_desktop-04 — El token de auth viaja como query param `?token=` y el bridge lo escribe literal en su access log (uvicorn `log_level="info"`), pese a que el bridge ya acepta la cabecera `X-BotStrike-Token`
**Archivo:** `desktop/src/lib/api.ts:232-235`

**Evidencia:**
```ts
function withToken(path: string, token: string): string {
  if (!token) return path;
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}
```
El bridge ya ofrece la alternativa segura — `server/bridge.py:1217-1224`:
```python
async def require_token_when_remote(token: str = "", x_botstrike_token: str = Header(default="")):
    """... (query `token=` o header `X-BotStrike-Token`)"""
    if _EXPOSE_TOKEN: return
    if not _token_ok(token or x_botstrike_token):
        raise HTTPException(status_code=401, detail="auth token required (BOTSTRIKE_AUTH_TOKEN)")
```
y `server/bridge.py:1590-1596` arranca uvicorn con `log_level="info"` y sin `access_log=False`, lo que
imprime la línea de acceso con query string completa.

**Por qué:** el token es la única credencial que arranca y para **LIVE** con dinero real. Con `?token=`
acaba en: el journal de systemd del CT 104 (`journalctl -u botstrike-bridge`, legible por cualquiera con
acceso al CT), cualquier proxy intermedio, el historial de `/api/backtest/run`, y el `Referer` si la web UI
(v2.13.0, servida por el propio bridge) llegara a navegar. La ronda 1 diseñó `withToken` así (`05_desktop.md:204`)
teniendo la cabecera disponible en el mismo commit del bridge. **Solapa con `tasks/audit/r2/security_supply.md`
(mitad servidor: `access_log=False`); esta es la mitad cliente y se arregla en 3 líneas.**

**Fix:**
```ts
async function authed<T>(path: string, opts: RequestOpts): Promise<T> {
  const token = await resolveToken();
  const headers = { ...(opts.headers as Record<string,string> | undefined),
                    ...(token ? { "X-BotStrike-Token": token } : {}) };
  ...request<T>(path, { ...opts, headers })
```
`bot_start` mantiene el chequeo `mode == "live" and not _token_ok(token)` leyendo sólo el query param
(`bridge.py:1270-1273`), así que hay que ampliarlo también a la cabecera — si no, LIVE dejaría de arrancar.
Ese detalle es la razón por la que el fix debe hacerse en ambos lados a la vez.

**Verificado cómo:** leído `api.ts:232-235,261-269`, `server/bridge.py:1213-1224,1269-1296,1455,1590-1596`.
La cabecera existe y está soportada: confirmado en el código del bridge, no supuesto.

---

### [P1] fix_desktop-05 — `ensure_local_engine` lanza un binario del **5 de abril de 2026**: el modo local del desktop ejecuta código anterior a TODOS los P0 de la ronda 1
**Archivo:** `desktop/src-tauri/src/lib.rs:65-80,109-137`; `desktop/src-tauri/tauri.conf.json:59-61`

**Evidencia:**
```
$ ls -la desktop/src-tauri/binaries/engine/
-rwxr-xr-x  22619713  Apr  5 20:00  botstrike-engine.exe
drwxr-xr-x            Apr  5 17:35  _internal
$ git log -1 --format=%ci b3dbf75   →  2026-08-30 04:39:45 +0200   (fixes P0 core/exchange/bridge)
$ git check-ignore -v desktop/src-tauri/binaries/engine
.gitignore:37:desktop/src-tauri/binaries/	src-tauri/binaries/engine
```
```rust
paths.push(resource.join("binaries").join("engine").join("botstrike-engine.exe"));
...
let child = launch_engine(&paths, port)?;
```
```json
"resources": [ "binaries/engine/**/*" ]
```

**Por qué:** el `.exe` empaquetado tiene **~5 meses** y es anterior a `b3dbf75` (posiciones desnudas,
precisión de órdenes, reintentos idempotentes, health real) y a `2a67ec2`. `bundle.resources` lo mete en
el instalador MSI/NSIS, y `ensure_local_engine` lo arranca en cuanto la Bridge URL es loopback — que es
**el valor por defecto** (`DEFAULT_BRIDGE_URL = http://127.0.0.1:9420`). Cualquier instalación nueva del
desktop opera, por defecto, con el engine pre-auditoría. Además el directorio está en `.gitignore`, así que
no hay nada en el repo ni en CI que lo regenere, lo versione o compare su versión con la del bridge: la
deriva es invisible y permanente. No lo marco P0 sólo porque el despliegue real de hoy es CT 104 en remoto
(paper) y no usa este binario; para cualquiera que instale el desktop y pulse "Connect", es P0 de facto.

**Fix:** (1) regenerar el sidecar en el mismo paso que se etiqueta la release (script de build que falle si
`git log -1 --format=%ct HEAD -- core/ exchange/ server/` > mtime del exe); (2) que `/api/health` del engine
local se compare con `__APP_VERSION__` al conectar y se avise en la UI si no coinciden; (3) documentar en
`deploy/README.md` que el modo local requiere rebuild del sidecar.

**Verificado cómo:** `ls -la` + `Get-Item` (LastWriteTime 05/04/2026 20:00:52) + `git log -1 --format=%ci b3dbf75`
+ `git check-ignore -v`. Salidas reales arriba.

---

### [P1] fix_desktop-06 — Los WebSockets siguen sin autenticación (03 "WebSocket: sin auth…" **sigue abierto**): `ws.ts` no envía token y `/ws/{channel}` no lo pide
**Archivo:** `desktop/src/lib/ws.ts:52-66`; `server/bridge.py:1228-1250`

**Evidencia:**
```ts
const url = `${getBridgeWsUrl()}/ws/${this.channel}`;
let ws: WebSocket;
try { ws = new WebSocket(url); } catch { ... }
```
```python
@app.websocket("/ws/{channel}")
async def websocket_endpoint(ws: WebSocket, channel: str):
    if channel not in ChannelManager.VALID_CHANNELS:
        await ws.close(code=4000, reason=f"Unknown channel: {channel}")
        return
    await state.channels.connect(channel, ws)   # ← sin ninguna comprobación de token
```

**Por qué:** la ronda 1 blindó el REST (`require_token_when_remote`) y `05_desktop.md:204` planificó
explícitamente el token WS ("y también para `/ws/*` vía query `?token=` o primer mensaje `auth`"), pero
**no se implementó ni en el cliente ni en el servidor**. Con el bridge en 0.0.0.0 detrás de ufw LAN+tailnet,
cualquier host de la LAN/tailnet abre `ws://192.168.1.204:9420/ws/trading` y recibe en directo posiciones,
equity, PnL, señales y logs. El REST está cerrado y la puerta de al lado sigue abierta de par en par;
la exposición de información es equivalente y en tiempo real.

**Fix:** cliente — `new WebSocket(url + (token ? \`?token=\${encodeURIComponent(token)}\` : ""))` (o mejor,
primer frame `{"type":"auth","token":…}` para no meterlo en el log de acceso, coherente con
fix_desktop-04); servidor — rechazar con `close(code=4401)` si `_EXPOSE_TOKEN` es False y el token no valida,
con un timeout de 5 s para el frame `auth`.

**Verificado cómo:** leído `ws.ts:52-66` (no hay ninguna referencia a `getBridgeToken` en todo el fichero)
y `server/bridge.py:1228-1250` completo. Referencia: `tasks/audit/03_bridge_deploy_security.md`
(§ "WebSocket: sin auth, sin límite de clientes…", línea 503 del fix propuesto) — sigue abierto tras `b3dbf75`.

---

### [P2] fix_desktop-07 — `kill_engine` es un `TerminateProcess` en seco: se salta el `stop_engine()` del lifespan del bridge, que es donde el engine cierra posiciones y tareas
**Archivo:** `desktop/src-tauri/src/lib.rs:58-62,140-148`

**Evidencia:**
```rust
.run(|app, event| {
    if let tauri::RunEvent::Exit = event { kill_engine(app); }
});
...
fn kill_engine(app: &tauri::AppHandle) {
    if let Ok(mut guard) = app.state::<EngineProc>().0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
```
lo que se pierde — `server/bridge.py:1179-1188`:
```python
    yield
    state.shutting_down = True
    await stop_engine()
    for t in list(loops) + list(state.bg_tasks):
        t.cancel()
```

**Por qué:** en Windows `Child::kill()` es `TerminateProcess`, que **no puede interceptarse**: ni signal
handler, ni `atexit`, ni el `finally` de uvicorn, ni el `yield`/teardown del `lifespan`. Cerrar la ventana
del desktop mata el engine a mitad de ciclo sin ejecutar `stop_engine()`. La ronda 1 arregló el problema
real (antes el proceso quedaba huérfano), pero eligió la variante bruta. En paper el coste es estado
inconsistente y trades sin flush; **en live sería salir con posiciones abiertas sin ninguna secuencia de
cierre**, exactamente el escenario "naked positions" que `b3dbf75` cerró en el core.

**Fix:** en `kill_engine`, intentar primero el apagado ordenado y sólo escalar si no muere:
`POST http://127.0.0.1:{port}/api/bot/stop` (o `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT)` con el hijo
lanzado en su propio grupo vía `CREATE_NEW_PROCESS_GROUP`), esperar hasta ~5 s con `try_wait()` en bucle,
y sólo entonces `child.kill()`. Manejar también `RunEvent::ExitRequested` para tener margen antes de que
la ventana desaparezca.

**Verificado cómo:** leído `lib.rs:58-62,140-148` y `server/bridge.py:1145-1188`. La semántica de
`std::process::Child::kill` en Windows (TerminateProcess, no capturable) es de la stdlib, no una suposición
sobre este código.

---

### [P2] fix_desktop-08 — "Test connection" nunca valida el token: da verde con el token vacío o equivocado, y el fallo aparece después al arrancar/parar LIVE
**Archivo:** `desktop/src/pages/settings/SettingsPage.tsx:70-83,138-140`; `desktop/src/lib/api.ts:272-274`

**Evidencia:**
```ts
const runTest = async () => {
  ...
  const health = await probeBridge(normalized);      // ← nunca pasa `draftToken`
  setTest({ state: "ok", health, ms: Date.now() - t0, url: normalized });
```
```ts
export function probeBridge(baseUrl: string): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", { baseUrl, timeoutMs: HEALTH_TIMEOUT_MS });
}
```
`server/bridge.py:1253`: `@app.get("/api/health")` — **sin** `dependencies=[Depends(require_token_when_remote)]`,
es decir, endpoint abierto.

**Por qué:** el label del propio campo dice "Auth token (required for a remote bridge and to start/stop LIVE)"
(`SettingsPage.tsx:138-139`), pero el único botón de verificación de la pantalla ignora ese campo. El usuario
pega un token con un salto de línea, un token del `.env` viejo, o ninguno, ve `ok · engine running · paper · v2.13.1 · 12 ms`
en verde, guarda, y descubre el problema cuando pulsa Start LIVE y recibe un 401. Es el campo con más
probabilidad de estar mal y es el único que el test no cubre.

**Fix:** que `runTest` haga dos llamadas: `/api/health` (alcanzabilidad) y, si hay token en el draft,
una verificación de credencial. Como no hay endpoint idempotente autenticado, la más limpia es añadir
`GET /api/auth/check` al bridge (200/401 con el token, sin efectos) y pintar dos chips:
`reachable ✓` y `token ✓ / token ✗`.

**Verificado cómo:** leído `SettingsPage.tsx:70-97`, `api.ts:271-274`, `server/bridge.py:1253-1259`
(ausencia de la dependencia de auth verificada leyendo el decorador completo).

---

### [P2] fix_desktop-09 — Tras reiniciar el engine local, el primer Start/Stop **siempre** falla con 401: la caché del token se invalida pero no se reintenta
**Archivo:** `desktop/src/lib/api.ts:241-269`

**Evidencia:**
```ts
let discoveredToken: { url: string; token: string } | null = null;
...
async function authed<T>(path: string, opts: RequestOpts): Promise<T> {
  const token = await resolveToken();
  try {
    return await request<T>(withToken(path, token), opts);
  } catch (e) {
    if (e instanceof ApiError && e.isAuth) discoveredToken = null; // stale local token → rediscover next time
    throw e;
  }
}
```
`server/bridge.py:49`:
```python
_AUTH_TOKEN = os.getenv("BOTSTRIKE_AUTH_TOKEN", "").strip() or secrets.token_hex(16)
```

**Por qué:** en modo local el bridge genera **un token aleatorio por proceso** (no hay `.env` en el PC del
usuario), y `api.ts` lo descubre una vez vía `/api/bot/status` y lo cachea. Cuando el engine local se
reinicia (crash + relanzamiento, `ensure_local_engine` tras cerrar y reabrir la app, watchdog), el token
cambia. La caché sigue con el viejo → 401 → el comentario dice "rediscover next time", y efectivamente lo
hace… **en la siguiente llamada**. La actual muere. El usuario ve un error rojo de auth al pulsar Start,
vuelve a pulsar, y funciona. En un terminal de trading, "pulsa dos veces y ya" es un bug, no una peculiaridad.

**Fix:** un reintento único, explícito y acotado:
```ts
async function authed<T>(path: string, opts: RequestOpts, retried = false): Promise<T> {
  const token = await resolveToken();
  try { return await request<T>(withToken(path, token), opts); }
  catch (e) {
    if (e instanceof ApiError && e.isAuth && !retried && !getBridgeToken() && isLocalBridge()) {
      discoveredToken = null;
      return authed<T>(path, opts, true);   // sólo con token descubierto, nunca con token configurado
    }
    if (e instanceof ApiError && e.isAuth) discoveredToken = null;
    throw e;
  }
}
```
Importante: **no** reintentar cuando el token es el configurado por el usuario (ahí el 401 es la respuesta
correcta y reintentar sólo duplica peticiones de mutación).

**Verificado cómo:** leído `api.ts:241-269` y `server/bridge.py:46-52`. El camino
"caché invalidada → `throw e` sin reintento" está en la línea inmediatamente siguiente a la invalidación.

---

### [P2] fix_desktop-10 — Deriva de versiones: la UI dice 2.13.1, el binario/instalador/updater dicen 2.12.0. La sincronización que hizo la ronda 1 se rompió dos releases después
**Archivo:** `desktop/package.json:3` vs `desktop/src-tauri/tauri.conf.json:4` y `desktop/src-tauri/Cargo.toml:3`

**Evidencia:**
```
desktop/package.json          "version": "2.13.1"
desktop/src-tauri/tauri.conf.json  "version": "2.12.0"
desktop/src-tauri/Cargo.toml       version = "2.12.0"

$ git log --oneline -3 -- desktop/package.json
e58912b feat(perf): v2.13.1 …
6d0256d feat(webui): v2.13.0 …
ffacf4a feat(desktop): v2.12.0 …
$ git log --oneline -3 -- desktop/src-tauri/tauri.conf.json
ffacf4a feat(desktop): v2.12.0 …        ← última vez que se tocó
```
`vite.config.ts:8-13` toma `__APP_VERSION__` de `package.json` y `SystemPage.tsx:215` lo pinta.

**Por qué:** `ffacf4a` sincronizó los tres a propósito ("versiones sincronizadas a 2.12.0", paso 9 de
`fixes_round1_desktop.md`) y las dos releases siguientes sólo bumpearon `package.json`. Consecuencias
reales: (a) System → App Info muestra `2.13.1` mientras el `.msi/.exe` instalado, el `latest.json` que
genera `createUpdaterArtifacts` y el `current_version` que compara el updater dicen `2.12.0` — imposible
saber qué está instalado; (b) publicar una release "2.13.1" desde este árbol produciría artefactos marcados
`2.12.0`, con lo que **el updater no vería ninguna actualización**; (c) la línea 2.13.x nunca ha existido
como release de escritorio.

**Fix:** un único origen. Lo más barato aquí: script `npm version` + un `prebuild` que reescriba
`tauri.conf.json` y `Cargo.toml` desde `package.json`, y un test/CI que falle si los tres no coinciden.

**Verificado cómo:** leídos los tres ficheros + `git log` por fichero (salida arriba).

---

### [P2] fix_desktop-11 — `ensure_local_engine` devuelve antes de que el engine escuche y su error no llega a la UI: en arranque local en frío la app enseña "Bridge unreachable" mientras el engine está arrancando bien
**Archivo:** `desktop/src-tauri/src/lib.rs:90-107`; `desktop/src/lib/engine.ts:21-31`; `desktop/src/hooks/useWebSocket.ts:27-30`

**Evidencia:**
```rust
let child = launch_engine(&paths, port)?;
let pid = child.id();
*guard = Some(child);
Ok(format!("engine started (pid {pid}) on 127.0.0.1:{port}"))   // ← no espera a que el puerto abra
```
```ts
export function startWebSockets() {
  void ensureLocalEngine();   // no await
  connectAll();
}
```
```ts
    .catch((e: unknown) => { console.warn("[engine] ensure_local_engine:", e); });
```

**Por qué:** un engine PyInstaller *onedir* con pandas/pyarrow/numpy tarda del orden de 5-15 s en importar
y abrir el puerto. `connectAll()` dispara los 5 canales de inmediato: todos fallan y entran en backoff
`3 → 6 → 12 → 24 → 30 s` con jitter (`ws.ts:176-186`). Mientras tanto la overlay arma su temporizador de
15 s (`OVERLAY_CONNECT_TIMEOUT_MS`) y pinta **"Bridge unreachable — The bundled engine did not start"**
sobre un engine que sí arrancó. Peor: el único caso en que ese mensaje es cierto —
`Err("Engine binary not found. Run manually: python -m server.bridge")` — se queda en `console.warn`,
un sitio donde ningún usuario mira, porque `engine.ts` traga el resultado.

**Fix:** hacer `ensure_local_engine` esperar la disponibilidad real antes de responder (bucle
`port_open(port)` cada 250 ms hasta ~20 s, con `tokio::time::sleep`, devolviendo `Err` si expira), y en
`startWebSockets()` `await ensureLocalEngine()` antes de `connectAll()`; propagar el `Err` a un estado
visible de la overlay en vez de a la consola.

**Verificado cómo:** leído `lib.rs:82-107`, `engine.ts:10-32`, `useWebSocket.ts:27-30`, `ws.ts:176-186`,
`constants.ts:8-14`. Los tiempos de arranque de PyInstaller son estimación mía; el resto (no espera,
no await, error sólo en consola, 15 s de timeout) está en el código.

---

### [P3] fix_desktop-12 — El `try/catch` alrededor de `invoke()` en `engine.ts` es código muerto: `invoke` es `async`, nunca lanza de forma síncrona
**Archivo:** `desktop/src/lib/engine.ts:13-20`

**Evidencia:**
```ts
let p: Promise<string>;
try {
  p = invoke<string>("ensure_local_engine", { port: getBridgePort() });
} catch (e) {
  // window.__TAURI_INTERNALS__ missing (browser dev) → nothing to do
  console.info("[engine] not running inside Tauri:", e);
  return Promise.resolve();
}
```
`desktop/node_modules/@tauri-apps/api/core.js:201`:
```js
async function invoke(cmd, args = {}, options) {
```

**Por qué:** al ser `async function`, el `TypeError` de `window.__TAURI_INTERNALS__ is undefined` se
convierte en una promesa rechazada, no en una excepción síncrona. El `catch` nunca se ejecuta; en su lugar
salta el `.catch()` de abajo y se loguea `console.warn("[engine] ensure_local_engine: …")`. Funcionalmente
inocuo (en navegador el efecto neto es el mismo: no-op), pero el mensaje pensado para el caso "no estás en
Tauri" nunca aparece y el ruido de warning confunde al depurar. Relevante ahora que la web UI (2.13.0)
corre en navegador de verdad.

**Fix:** quitar el try/catch y discriminar en el `.catch()` (`if (String(e).includes("__TAURI_INTERNALS__")) console.info(...)`),
o comprobar `"__TAURI_INTERNALS__" in window` antes de invocar.

**Verificado cómo:** leído `engine.ts:10-32` y la definición real de `invoke` en
`node_modules/@tauri-apps/api/core.js:201` (`grep -n "async function invoke"`).

---

### [P3] fix_desktop-13 — `request()` pone `Content-Type: application/json` también en los GET: preflight OPTIONS en cada llamada REST a un bridge remoto
**Archivo:** `desktop/src/lib/api.ts:191-196`

**Evidencia:**
```ts
const res = await fetch(url, {
  ...init,
  signal: controller.signal,
  headers: { "Content-Type": "application/json", ...(headers as Record<string, string> | undefined) },
});
```

**Por qué:** `Content-Type: application/json` no es un header CORS-safelisted, así que **todas** las
peticiones desde `http://tauri.localhost` (o desde la web UI a otro origen) dejan de ser "simples" y
disparan un `OPTIONS` previo. Con un bridge por Tailscale eso duplica el RTT de `/api/health`,
`/api/performance`, `/api/trades`… La overlay tiene un presupuesto de 4 s (`HEALTH_TIMEOUT_MS`) que se come
dos viajes en vez de uno. Es una regresión menor de latencia introducida al centralizar `request()` en `ffacf4a`.

**Fix:** poner la cabecera sólo cuando hay cuerpo:
```ts
headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...headers },
```

**Verificado cómo:** leído `api.ts:185-200` y `server/bridge.py:1194-1200`
(`CORSMiddleware` con `allow_origin_regex` que sí cubre `tauri.localhost`, así que el preflight pasa —
el coste es latencia, no un fallo).

---

### [P3] fix_desktop-14 — Arranque: hasta 4 s de pantalla sin overlay y luego el diálogo de setup aparece encima de la app ya pintada
**Archivo:** `desktop/src/components/shared/ConnectionOverlay.tsx:20,25-40,63`

**Evidencia:**
```ts
const [phase, setPhase] = useState<Phase>("probing");
...
probeBridge(getBridgeUrl()).then(...).catch(() => { if (!cancelled) setPhase("setup"); });
...
if (phase === "dismissed" || phase === "probing") return null;
```
con `HEALTH_TIMEOUT_MS = 4_000` (`constants.ts:4`).

**Por qué:** con el bridge caído (o simplemente lento), la fase `probing` renderiza `null` durante hasta
4 s: el usuario ve el dashboard vacío, empieza a hacer clic, y entonces le cae encima el modal
"Select your exchange to get started". Introducido por `6d0256d` sobre la overlay de `ffacf4a`.

**Fix:** renderizar un estado de "Checking bridge…" durante `probing` (mismo card, spinner), en vez de `null`.

**Verificado cómo:** leído `ConnectionOverlay.tsx:20-63` y `constants.ts:4`.

---

### [P3] fix_desktop-15 — `config.ts` no escucha el evento `storage`: dos pestañas de la web UI (2.13.0) divergen en URL/token sin avisar
**Archivo:** `desktop/src/lib/config.ts:69-84,165-179`

**Evidencia:**
```ts
export function subscribeBridgeConfig(l: () => void): () => void {
  listeners.add(l);
  return () => { listeners.delete(l); };
}
```
(el `Set` de listeners es puramente in-process; no hay `window.addEventListener("storage", …)`).

**Por qué:** irrelevante en Tauri (una sola ventana), pero desde `6d0256d` la misma build se sirve por HTTP
desde el bridge y se abre en varias pestañas. Cambiar la URL en una pestaña no se refleja en la otra, que
sigue hablando con el endpoint viejo hasta un F5. Mismo comentario para `SERVED_FROM_BRIDGE`
(`config.ts:14-21`): sólo siembra el valor **inicial**, así que si la IP del CT cambia, un `localStorage`
previo sigue apuntando a la vieja aunque la página se haya cargado desde la nueva.

**Fix:** `window.addEventListener("storage", e => { if (e.key === URL_KEY || e.key === TOKEN_KEY) { recargar de localStorage; notify(); } })`,
dentro de un `try` (el acceso puede lanzar en modos restringidos).

**Verificado cómo:** leído `config.ts` completo; `grep "addEventListener"` en `src/lib/` → sin resultados en `config.ts`.

---

### [P3] fix_desktop-16 — `onChannelStatus` devuelve un array nuevo en cada transición aunque no cambie nada
**Archivo:** `desktop/src/stores/systemStore.ts:113-123`

**Evidencia:**
```ts
onChannelStatus: (channel, open) =>
  set((s) => {
    const next = new Set(s.openChannels);
    if (open) next.add(channel); else next.delete(channel);
    const openChannels = [...next];
    return openChannels.length === 0 && s.bridgeConnected
      ? { openChannels, bridgeConnected: false, engineRunning: false }
      : { openChannels };
  }),
```

**Por qué:** si el canal ya estaba en el estado pedido (p. ej. un `close` de un socket que nunca llegó a
`open`), el `Set` no cambia pero `[...next]` es una referencia nueva → todo suscriptor de `openChannels`
(SettingsPage, SystemPage) re-renderiza sin motivo. Coste bajo, pero es gratis evitarlo.

**Fix:** `if (next.size === s.openChannels.length) return s;` antes de materializar el array.

**Verificado cómo:** leído `systemStore.ts:113-123`.

---

## Lo que la ronda 1 hizo BIEN (verificado, sin hallazgo)

| Punto del foco | Veredicto |
|---|---|
| `ws.ts` — doble conexión tras Save & reconnect | **Correcto.** `disconnect()` limpia `reconnectTimer`, `dropSocket()` anula los 4 handlers y pone `this.ws = null`; `connectAll()` limpia `staggerTimers` antes de re-armar. `connect()` es idempotente por el guard `OPEN \|\| CONNECTING`. No encontré ningún camino que abra dos sockets del mismo canal. |
| `ws.ts` — timers huérfanos | **Correcto.** `pingTimer` (clearInterval desde dentro de su propio callback: legal), `reconnectTimer` y `staggerTimers` están todos rastreados y limpiados. `wantOpen` impide la reconexión tras un `disconnect()` deliberado. |
| `ws.ts` — detección de half-open | **Correcto y comprobado contra el servidor:** `server/bridge.py:1240-1241` responde `{"type":"pong"}` a `{"type":"ping"}`, y `lastMessageAt` se actualiza en la primera línea de `onmessage` (antes del `return` del pong). Con ping 10 s / stale 25 s no hay falsos positivos en canales silenciosos. |
| `useSyncExternalStore` con localStorage bloqueado | **Correcto.** `safeGet`/`safeSet` envuelven en try/catch y `getSnapshot` devuelve el objeto cacheado `snapshot` (referencia estable), no uno nuevo — que es el error clásico que provoca "getSnapshot should be cached" / bucle infinito. `getServerSnapshot` está provisto. |
| Guardar token vacío borra el anterior | **Comportamiento correcto e intencional**, no un bug: `safeSet` hace `removeItem` con `""` y `dirty` detecta el cambio, así que "vaciar y guardar" es la forma de borrar el token. Documentable, no arreglable. |
| `capabilities: ["core:default"]` basta para `invoke` | **Sí.** Los comandos registrados por la propia app en `invoke_handler` están permitidos por defecto (docs oficiales Tauri v2, *Capabilities*: "By default, all commands that you registered in your app … are allowed to be used by all the windows and webviews"). El ACL sólo cierra comandos de plugins/core. La app además no abre URLs externas (`grep window.open\|target="_blank"\|openUrl\|shell` en `src/` → 0 resultados), así que quitar `plugin-shell` no rompió nada. |
| `manage(EngineProc)` antes del `setup` | **Correcto.** `.manage()` va antes de `.setup()` y de `.invoke_handler()` en el builder, así que `app.state::<EngineProc>()` nunca puede panicar por estado no registrado. |
| `--port` sólo si ≠ 9420 sobre el exe empaquetado | **Supuesto correcto:** `add_argument("--port")` existe en `server/bridge.py` desde `67921ef` (2026-04-01), anterior al binario del 05/04. Verificado con `git log -S`. |
| `bundle` / `updater` intactos | **Sí.** El diff de `ffacf4a` sobre `tauri.conf.json` toca únicamente `version` y el bloque `security`; `bundle.targets`, `icon`, `createUpdaterArtifacts`, `resources` y `plugins.updater` (pubkey + endpoint + installMode) quedan byte a byte iguales. |
| CSP y fuentes de Google | **La CSP es coherente con el bundle.** `dist/assets/index-*.css` conserva `@import "https://fonts.googleapis.com/css2?…"` (verificado sobre el `dist/` recién construido) y la CSP permite `style-src …fonts.googleapis.com` + `font-src …fonts.gstatic.com`. No hay mixed content: la app corre en `http://tauri.localhost`, no en `https:`, así que `ws://` no se bloquea. |
| `eslint` / `build` | **Siguen en verde** (salidas arriba). |

