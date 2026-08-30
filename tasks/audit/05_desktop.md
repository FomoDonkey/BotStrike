# Auditoría 05 — Desktop (Tauri v2 + React 19 + Vite) (BotStrike)

**Fecha:** 2026-08-29 · **Ámbito:** `desktop/src/**`, `desktop/src-tauri/{src,capabilities,tauri.conf.json,Cargo.toml}`, `desktop/{package.json,vite.config.ts,eslint.config.js,tsconfig*.json,index.html,README.md}`; referencias cruzadas a `server/bridge.py` (CORS/auth/WS) y `deploy/*` sólo donde afectan al desktop.
**Objetivo añadido:** el bridge pasa de `localhost:9420` a un Linux remoto (`192.168.1.204:9420` LAN / IP Tailscale, CT 104). La app tiene que poder apuntar a él.
**Método:** lectura del código real (Read/Grep), `git log/show`, `gh release` (para verificar updater), y ejecución real de `npm run build` y `npm run lint` en `desktop/` (salida al final). NO se ha ejecutado `tauri build` ni `tauri dev`; todo lo que dependa de eso se marca "no verificado en runtime". Archivo escrito de forma incremental.

**Resumen ejecutivo**
- `npm run build` (`tsc -b && vite build`): **PASA** (exit 0, 33 s, warning de chunk 843 kB).
- `npm run lint` (`eslint .`): **FALLA** (exit 1, 64 errores: 19 por parsear `src-tauri/target/**` que no está ignorado + 45 reales en `src/`).
- Hoy la app **no puede** conectar a un bridge remoto: host/puerto hardcodeados en 3 sitios TS + 1 Rust + 2 textos UI. `deploy/README.md:29` documenta un "Settings → Connection → Bridge URL" que **no existe** en el código.
- Fix v2.11.1 (overlay "Connected" pegado): **verificado correcto por lectura** — ya no hay bucle de cancelación; queda un `eslint-disable` que tapa una lectura de closure obsoleta (P3, propongo versión sin disable).
- CSP = `null` en producción, capabilities Tauri sobredimensionadas (`shell:default` incluye `shell:allow-open`, `process:default`, `shell:allow-kill`, sidecar declarado pero inexistente), versiones desincronizadas (Cargo.toml 2.7.1 / Cargo.lock 2.1.2 / SystemPage hardcoded / README 1.5.0).

---

## A. Inventario: dónde asume la app `localhost` / `127.0.0.1` / `9420`

Grep real sobre `desktop/src`, `desktop/src-tauri/src`, `tauri.conf.json`, `capabilities`, `vite.config.ts`, `index.html`, `package.json`, `README.md` (patrón `localhost|127\.0\.0\.1|9420|0\.0\.0\.0|ws://|http://`):

| # | Archivo:línea | Qué | Impacto en modo remoto |
|---|---|---|---|
| 1 | `desktop/src/lib/constants.ts:1` | `export const BRIDGE_URL = "http://127.0.0.1:9420";` | Base REST de TODA la API (`api.ts:7`) |
| 2 | `desktop/src/lib/constants.ts:2` | `export const BRIDGE_WS_URL = "ws://127.0.0.1:9420";` | Base de los 5 canales WS (`ws.ts:17`) |
| 3 | `desktop/src/lib/api.ts:1,7` | `import { BRIDGE_URL }` + `fetch(\`${BRIDGE_URL}${path}\`)` | Único punto de fetch REST (bien centralizado; sólo hay que cambiar la fuente) |
| 4 | `desktop/src/lib/ws.ts:1,17` | `this.url = \`${BRIDGE_WS_URL}/ws/${channel}\`` fijado **en el constructor** del singleton | Aunque la URL fuera configurable, los singletons ya creados no la cogerían; hay que resolverla en `connect()` |
| 5 | `desktop/src/pages/backtest/BacktestPage.tsx:5,52` | `fetch(\`${BRIDGE_URL}/api/backtest/run\`)` fuera de `api.ts` | Segundo punto de fetch; texto de error `python -m server.bridge` (línea 64) |
| 6 | `desktop/src/components/shared/ConnectionOverlay.tsx:69-73` | Texto fijo `Waiting for localhost:9420` + hint `python -m server.bridge` | UI miente en remoto |
| 7 | `desktop/src/pages/system/SystemPage.tsx:167` | `detail: "localhost:9420"` en el panel Connections | UI miente en remoto |
| 8 | `desktop/src-tauri/src/lib.rs:66-69` | `TcpStream::connect("127.0.0.1:9420")` → si no hay nada escuchando, lanza `botstrike-engine.exe` local (195 MB PyInstaller) | En remoto el PC arranca un engine local inútil en cada inicio y lo deja huérfano al cerrar |
| 9 | `desktop/src-tauri/src/lib.rs:47-49` | `std::thread::spawn(launch_engine)` incondicional en `setup` | No hay forma de decirle "este bridge es remoto" |
| 10 | `desktop/src-tauri/tauri.conf.json:8` | `"devUrl": "http://localhost:1420"` | Sólo dev server Vite (correcto, no tocar) |
| 11 | `desktop/src-tauri/tauri.conf.json:26` | `"csp": null` | Hoy "funciona" por ausencia de CSP; al añadir CSP hay que permitir `http://*:9420`/`ws://*:9420` |
| 12 | `desktop/src-tauri/capabilities/default.json:14-17` | `shell:allow-spawn` sidecar `binaries/botstrike-engine` | El frontend **no** importa `@tauri-apps/plugin-shell` (grep vacío) y `tauri.conf.json` no declara `bundle.externalBin` → entrada muerta |
| 13 | `server/bridge.py` (CORS, ~L740-750 al leerlo) | `allow_origin_regex=r"^https?://(localhost\|127\.0\.0\.1\|tauri\.localhost)(:\d+)?$\|^tauri://localhost$"` | **OK para remoto**: el origen del WebView en Windows es `http://tauri.localhost` independientemente de dónde viva el bridge. No requiere cambio |
| 14 | `deploy/README.md:29` | "En la app: Settings → Connection → Bridge URL = `http://192.168.1.204:9420`" | Documenta una pantalla que NO existe (`SettingsPage.tsx:45-51` tiene 5 tabs: capital/symbols/execution/notifications/appearance) |
| 15 | `README.md:43` (raíz) | "FastAPI on localhost:9420" | Doc desactualizada |

Sin hallazgos en: `vite.config.ts` (solo puerto 1420 del dev server), `index.html`, `package.json`, `main.tsx`.

---

## Hallazgos

### [P0] La app no puede conectar al bridge remoto ya desplegado (URL hardcodeada, sin UI de configuración)
**Archivo:** `desktop/src/lib/constants.ts:1-2`, `desktop/src/lib/ws.ts:17`, `desktop/src/pages/backtest/BacktestPage.tsx:52`, `desktop/src-tauri/src/lib.rs:66`, `deploy/README.md:29`
**Evidencia:** `BRIDGE_URL = "http://127.0.0.1:9420"` / `BRIDGE_WS_URL = "ws://127.0.0.1:9420"` son constantes de módulo; no hay `localStorage`, store ni tauri-store que las alimente (grep `localStorage` en `src/` → sólo `botstrike-exchange`, `botstrike-theme`, `bs_last_metrics`, equity). `SettingsPage.tsx:45-51` no tiene tab "Connection". `deploy/README.md:29` dice al usuario que configure "Settings → Connection → Bridge URL". CT 104 ya está en producción con `BOTSTRIKE_HOST=0.0.0.0` (`deploy/botstrike-bridge.service:16`).
**Por qué:** bloqueante para el objetivo de la sesión: el desktop instalado (v2.11.1, release pública en GitHub) sólo hablará con un bridge en el mismo PC. La documentación de despliegue promete una funcionalidad inexistente.
**Fix:** ver sección "Diseño Bridge URL configurable" (módulo `src/lib/config.ts` + tab Connection + resolución de URL en `connect()` + comando Rust condicional). Lista de archivos al final.
**Verificado cómo:** Grep/Read de los archivos citados; `git log -- SettingsPage.tsx` no muestra ningún commit con "connection"; `ls deploy/` + `cat deploy/README.md`.

### [P1] `csp: null` — el WebView de producción corre sin Content-Security-Policy
**Archivo:** `desktop/src-tauri/tauri.conf.json:25-27`
**Evidencia:** `"security": { "csp": null }`. Con `null` Tauri no inyecta ninguna CSP. El frontend renderiza texto que viene del bridge (logs `SystemPage.tsx:234`, mensajes de alerta `useWebSocket.ts:115`, `msg.error`) — React escapa, pero cualquier futura `dangerouslySetInnerHTML`/lib de charts con HTML pasa sin red de seguridad. Además el bridge pasa a estar en LAN (superficie mayor).
**Por qué:** con CSP nula + capabilities `shell:default` (ver P2 abajo, incluye `shell:allow-open`), una XSS en el WebView escala a abrir URLs/ejecutables en el PC. Tauri recomienda CSP explícita en prod.
**Fix:** CSP explícita (diff en la sección de diseño) con `connect-src` que permita `http://*:9420 ws://*:9420` (CSP3 admite `*` como host con puerto fijo) más loopback con cualquier puerto, `style-src`/`font-src` para Google Fonts (`index.css:1` importa `fonts.googleapis.com`) y `devCsp` relajada para HMR/react-refresh. **No verificado en runtime** (requiere `tauri dev`/`tauri build`); hay que comprobar en DevTools que no aparecen errores CSP al cargar fuentes, lightweight-charts y framer-motion.
**Verificado cómo:** Read de `tauri.conf.json` e `index.css:1`.

### [P1] Arrancar el bot en LIVE desde el desktop falla en silencio (no se envía `token`, y la respuesta `{error}` se ignora)
**Archivo:** `desktop/src/lib/api.ts:23-25`, `desktop/src/pages/system/SystemPage.tsx:38-39`, `server/bridge.py` (`bot_start`: `if mode == "live" and token != _AUTH_TOKEN: return {"error": ...}`)
**Evidencia:** `botStart: (mode, exchange) => request(`/api/bot/start?mode=${mode}&exchange=${exchange}`)` — sin `token`. El bridge devuelve **HTTP 200** con `{"error": "Invalid or missing auth token for live mode"}`. `request()` sólo lanza si `!res.ok` (`api.ts:12`), así que el `{error}` se devuelve como éxito; `SystemPage.tsx:38` hace `.catch(() => null)` y no mira el body. grep `token` en `desktop/src` → 0 resultados.
**Por qué:** el usuario pulsa Start en modo LIVE y no pasa nada, sin mensaje. Para paper/dry_run funciona porque el bridge no exige token. Mismo patrón en `botStop` en modo live (el bridge exige token para parar live → **no se puede parar un live desde la UI**).
**Fix:** (1) `request()` debe tratar `{error: string}` como fallo (diff en diseño); (2) `SystemPage.handleStart/Stop` deben mostrar el error vía `useAlertStore.addAlert({level:"critical",...})`; (3) añadir campo "Auth token" en Settings → Connection (persistido) y pasarlo en `botStart/botStop`. Nota cruzada con auditoría 03: el bridge expone `auth_token` en `GET /api/bot/status` — al abrir el puerto a la LAN ese token deja de proteger nada; el desktop NO debe leerlo de ahí.
**Verificado cómo:** Read `api.ts`, `SystemPage.tsx`, grep de `_AUTH_TOKEN` en `server/bridge.py`.

### [P1] En modo remoto el desktop seguiría lanzando el engine local (195 MB) en cada arranque y lo deja huérfano al cerrar
**Archivo:** `desktop/src-tauri/src/lib.rs:47-49, 64-99`
**Evidencia:** `setup` hace `std::thread::spawn(move || launch_engine(&engine_paths))` incondicional; `launch_engine` sólo se abstiene si `TcpStream::connect("127.0.0.1:9420")` responde. `Command::new(path)...spawn()` → `Ok(child)` registra el pid y el `Child` se **descarta** (líneas 87-90); no hay `RunEvent::Exit`/`on_window_event` que lo mate. `du -sh src-tauri/binaries/engine` = 195 MB.
**Por qué:** con bridge remoto, cada arranque del desktop lanza un uvicorn+engine local que nadie usa, consume RAM/CPU, abre el puerto 9420 local (que además hace que un usuario que vuelva a "local" crea que está conectado al servidor), y al cerrar la app el proceso sigue vivo invisible (sin tray, sin indicador). Hoy en local ya ocurre el huérfano.
**Fix:** sacar el lanzamiento de `setup`; exponer `#[tauri::command] ensure_local_engine(port)` que el frontend invoca **sólo** si la URL configurada es loopback; guardar el `Child` en `app.manage(...)` y matarlo en `RunEvent::Exit` (decisión de producto: si se quiere que el engine local sobreviva al cierre, dejarlo pero mostrar un tray icon). Diff en diseño.
**Verificado cómo:** Read `lib.rs` completo; grep `RunEvent|on_window_event|kill` en `src-tauri/src` → 0.

### [P1] WS sin detección de conexión medio-abierta: sobre LAN/Tailscale una caída no dispara `onclose` y no hay reconexión hasta que el SO expira el TCP
**Archivo:** `desktop/src/lib/ws.ts:82-96` (ping), `desktop/src/lib/ws.ts:45-50` (onclose)
**Evidencia:** el cliente envía `{"type":"ping"}` cada 30 s y el bridge responde `pong` (`bridge.py` `websocket_endpoint`), pero **nadie mide** si llega el pong ni ningún mensaje (`onmessage` sólo hace `JSON.parse` y despacha). La reconexión sólo se programa desde `onclose`. Hay un watchdog de salud en `systemStore.ts:28-36` (10 s sin `health` → `bridgeConnected=false`) que pinta la UI como desconectada, pero **no** cierra ni reabre los sockets.
**Por qué:** en loopback `onclose` es inmediato. Con el bridge en `192.168.1.204` o por Tailscale (portátil que duerme, Wi-Fi que se cae, CT que se reinicia sin FIN), el socket queda "OPEN" del lado del cliente durante minutos (TCP keepalive del SO); la UI dirá OFFLINE por el watchdog pero el cliente no reconectará. El backoff existente (3 s→30 s + jitter, `ws.ts:98-108`) está bien diseñado pero no se dispara.
**Fix:** en `ws.ts` guardar `lastMessageAt` (cualquier mensaje incl. `pong`), ping cada 10 s y si `now - lastMessageAt > 25 s` forzar cierre + `scheduleReconnect()` (diff en diseño). Además reflejar apertura/cierre de sockets en `systemStore` para que la UI no espere 10-15 s.
**Verificado cómo:** Read `ws.ts`, `systemStore.ts`, `useWebSocket.ts`; sección `@app.websocket` de `bridge.py`.

### [P2] `npm run lint` falla: 64 errores (19 por no ignorar `src-tauri/target`, 45 reales)
**Archivo:** `desktop/eslint.config.js:10` (`globalIgnores(['dist'])`), y los listados en la salida real (sección final)
**Evidencia:** exit code 1. 19 errores "Parsing error: Unexpected character" en `src-tauri/target/release/build/**/tauri-codegen-assets/*.js` (assets binarios generados por Tauri, parseados como JS). 45 en `src/`: 27× `@typescript-eslint/no-explicit-any` (api.ts, ws.ts, CandlestickChart, stores), 12× `no-unused-vars` (imports muertos en Dashboard/Performance/System/Trading, `_`/`__` en useWebSocket.ts:61,91), 3× `no-empty`, 2× `react-hooks/set-state-in-effect` (TopBar.tsx:37, AnimatedNumber.tsx:31), 1× `react-hooks/preserve-manual-memoization` (PerformancePage.tsx:72).
**Por qué:** lint no es usable como gate; los errores de `react-hooks` v7 (React Compiler rules) son advertencias legítimas de renders en cascada. `tsconfig.app.json:30-31` tiene `noUnusedLocals: false` así que `tsc` tampoco los caza.
**Fix:** `globalIgnores(['dist', 'src-tauri/**'])`; tipar `api.ts` con interfaces de respuesta; para `const { type: _, timestamp: __, ...rest }` usar `varsIgnorePattern: "^_"` en la config; borrar imports muertos; `useMemo` deps `[perfData]`.
**Verificado cómo:** `npm run lint` ejecutado (salida íntegra abajo).

### [P2] Versiones desincronizadas en 5 sitios
**Archivo:** `desktop/package.json:4` (2.11.1), `desktop/src-tauri/tauri.conf.json:4` (2.11.1), `desktop/src-tauri/Cargo.toml:3` (**2.7.1**), `desktop/src-tauri/Cargo.lock:225-226` (**2.1.2**), `desktop/src/pages/system/SystemPage.tsx:195` (string literal `2.11.1`), `README.md:11` raíz (badge **1.5.0**), `server/bridge.py` `FastAPI(... version="1.0.0")`, `desktop/README.md` (boilerplate de la plantilla Vite, no habla de BotStrike).
**Evidencia:** `cat` de cada archivo; `git log -- package.json tauri.conf.json Cargo.toml` muestra que los bumps sólo tocan package.json + tauri.conf.json desde v2.8.0.
**Por qué:** el updater usa `tauri.conf.json.version` (por eso funciona), pero `Cargo.toml` alimenta `env!("CARGO_PKG_VERSION")` y los metadatos del binario; la versión en pantalla se olvida en cada bump (ya pasó: SystemPage la edita a mano el commit de release).
**Fix:** (a) `Cargo.toml version = "2.11.1"` + `cargo update -p botstrike`; (b) en `vite.config.ts` `define: { __APP_VERSION__: JSON.stringify(pkg.version) }` + `declare const __APP_VERSION__: string` en `src/vite-env.d.ts` y usarlo en SystemPage (o `getVersion()` de `@tauri-apps/api/app`); (c) badge README; (d) reescribir `desktop/README.md`.
**Verificado cómo:** `cat`/`grep` de los 8 archivos.

### [P2] Indicador de conexión: `bridgeConnected` tarda 10-15 s en apagarse, no refleja el estado real de los sockets, y el TopBar muestra OFFLINE con bridge OK + engine parado
**Archivo:** `desktop/src/stores/systemStore.ts:25-36`, `desktop/src/hooks/useWebSocket.ts:105-107`, `desktop/src/components/layout/TopBar.tsx:71`, `desktop/src/lib/ws.ts:45-50`
**Evidencia:** `bridgeConnected` sólo pasa a `true` en el primer mensaje `health` (cada 3 s desde el bridge) y a `false` cuando el watchdog (tick 5 s) ve `>10 s` sin health → ventana de 10-15 s. `ws.ts` no notifica a ningún store al cerrar (`WebSocketClient.connected` no lo lee nadie). TopBar: `isConnected = bridgeConnected && (wsConnected || hasPrices)` → con el bridge remoto vivo y el engine parado (estado normal tras reiniciar el CT sin autostart) se pinta WifiOff rojo, indistinguible de "no llego al servidor".
**Por qué:** con un bridge remoto la primera pregunta del usuario es "¿llego al servidor?"; la UI actual mezcla "bridge alcanzable" con "engine tiene feed de Binance".
**Fix:** `ws.ts` emite `onChannelStatus(channel, open)`; `systemStore.openChannels: string[]`; si `openChannels` queda vacío → `bridgeConnected=false` inmediato. TopBar: punto = bridge, color del icono = feed; `title` con URL y modo LOCAL/REMOTE. Diff en diseño.
**Verificado cómo:** Read de los 4 archivos.

### [P2] Overlay "Connecting" no expira nunca y muestra host fijo — v2.11.1 verificado OK, pero con `eslint-disable` que tapa una lectura obsoleta de `phase`
**Archivo:** `desktop/src/components/shared/ConnectionOverlay.tsx:14-21, 55-81`
**Evidencia (verificación del fix v2.11.1):** commit `8141d49` cambió deps `[bridgeConnected, phase]` → `[bridgeConnected]`. Traza: click Connect → `phase="connecting"`; llega health → `bridgeConnected=true` → efecto: `phase==="connecting"` → `setPhase("connected")` + timer 1 s → `dismissed`. El cleanup sólo corre si `bridgeConnected` cambia o el componente se desmonta; `bridgeConnected` sólo puede volver a `false` vía watchdog (≥10 s) → el timer de 1 s ya venció. **Conclusión: ya no se queda pegado.** Riesgo residual: el efecto lee `phase` de la closure de ese render (correcto hoy porque el efecto sólo corre al cambiar `bridgeConnected`), y con la mejora P2 anterior (cierre de sockets → `bridgeConnected=false` inmediato) un flapeo <1 s sí cancelaría el timer y dejaría "Connected" fijo → hay que endurecerlo a la vez.
**Evidencia (resto):** fase `connecting` no tiene timeout ni estado "unreachable"; texto `Waiting for localhost:9420` y hint `python -m server.bridge` (líneas 69-73) incorrectos en remoto; `Skip` deja la app sin ningún indicador de que la URL es errónea.
**Por qué:** con bridge remoto la causa nº1 de "no conecta" será URL/firewall/Tailscale; el usuario necesita ver la URL que se está intentando y poder cambiarla desde el overlay.
**Fix:** `phaseRef` en vez de eslint-disable; fase `unreachable` tras 15 s con botones Retry / Settings; mostrar `getBridgeUrl()` + modo. Diff en diseño.
**Verificado cómo:** `git show 8141d49`, Read del componente, traza manual de estados; `systemStore.ts:32` (umbral 10 s).

### [P2] Capabilities Tauri excesivas y un sidecar declarado que no existe
**Archivo:** `desktop/src-tauri/capabilities/default.json:6-19`, `desktop/src-tauri/tauri.conf.json:29-48`, `desktop/src-tauri/src/lib.rs:12`, `desktop/package.json:18`
**Evidencia:** permisos concedidos al WebView: `core:default`, `updater:default`, `dialog:default`, `process:default`, `shell:default`, `shell:allow-spawn` (sidecar `binaries/botstrike-engine`), `shell:allow-kill`. grep `@tauri-apps` en `desktop/src` → **0 imports**: el frontend no usa IPC de ningún plugin. El engine se lanza desde Rust con `std::process::Command` (`lib.rs:81`), no como sidecar; `tauri.conf.json` no tiene `bundle.externalBin` (el engine va como `resources: binaries/engine/**/*`). `shell:default` incluye `shell:allow-open` (abrir URLs/rutas con el programa por defecto desde JS); `process:default` incluye `allow-exit`/`allow-restart`.
**Por qué:** principio de mínimo privilegio; con CSP nula (P1) cualquier inyección en el WebView tendría `shell:open` + `process:exit` a mano. El sidecar declarado y sin `externalBin` es configuración muerta que confunde.
**Fix:** `permissions: ["core:default"]` (Rust sigue usando dialog/updater/process directamente; las capabilities sólo gobiernan IPC del WebView). Quitar `tauri_plugin_shell` de `Cargo.toml`/`lib.rs` y `@tauri-apps/plugin-shell` de `package.json` (no se usan). Diff en diseño.
**Verificado cómo:** grep `@tauri-apps` y `plugin-shell` en `src/`; Read `capabilities/default.json`, `lib.rs`.

### [P2] Manejo de errores de fetch: todo se traga en silencio; 200+`{error}` cuenta como éxito; timeout único de 30 s
**Archivo:** `desktop/src/lib/api.ts:3-17`; callers en `DashboardPage.tsx:40-52`, `StrategiesPage.tsx:38-45`, `DataPage.tsx:27-31`, `PerformancePage.tsx:54-58`, `SettingsPage.tsx:74-77`, `SystemPage.tsx:22,38-39`, `BacktestPage.tsx:52-65`
**Evidencia:** `request()` lanza en `!res.ok` y en abort (30 s), pero no inspecciona `{error}`; 9 de 9 callers hacen `.catch(() => null | {} | setLoading(false))`. `BacktestPage` hace su propio `fetch` sin timeout ni `res.ok`. Ningún sitio muestra "no se puede llegar a X".
**Por qué:** en remoto un error de red/CORS/firewall aparece como "pantalla vacía" o "Loading configuration..." (SettingsPage muestra "Start the bridge server to view configuration" aunque el bridge esté vivo y sea otro el problema).
**Fix:** `ApiError` con `status`; `{error: string}` → throw; `timeoutMs` por llamada (health 4 s); helper `probeBridge(url)`; un `addAlert` en las acciones de usuario (Start/Stop/Backtest). Diff en diseño.
**Verificado cómo:** Read de `api.ts` y grep `api\.` / `fetch(` en `src`.

### [P2] Timers de escalonado no rastreados en `connectAll()` + `_reconnectAttempts` no se resetea en `disconnect()`
**Archivo:** `desktop/src/lib/ws.ts:121-131`, `desktop/src/lib/ws.ts:62-75`
**Evidencia:** `connectAll()` programa 5 `setTimeout(i*500)` sin guardarlos; `disconnectAll()` sólo llama `disconnect()` en los clientes existentes. Si se hace `disconnectAll()` dentro de los 2 s siguientes a `connectAll()` (exactamente lo que hará "cambiar URL y reconectar"), los timers pendientes reabren sockets contra la URL vieja (hoy) o dejan conexiones duplicadas.
**Por qué:** bug latente que se vuelve real al añadir "Save & Reconnect".
**Fix:** guardar los timers y limpiarlos en `disconnectAll()`; `wantOpen` por cliente para que un `onclose` posterior a `disconnect()` no reprograme; resetear `_reconnectAttempts`. Diff en diseño.
**Verificado cómo:** Read `ws.ts`.

### [P2] Números mágicos de red/tiempo repartidos por 8 archivos
**Archivo:** `api.ts:5` (30_000), `ws.ts:88` (30000 ping), `ws.ts:102` (3000/2^n/30000), `ws.ts:125` (500), `systemStore.ts:32,35` (10000/5000), `ConnectionOverlay.tsx:18` (1000), `SystemPage.tsx:24` (5000), `PerformancePage.tsx:68` (30000), `systemStore.ts:63,76` (199), `alertStore.ts:57,61` (99, 10000/30000), `lib.rs:54` (10 s updater)
**Evidencia:** valores literales sin nombre; el ping (30 s) es mayor que el umbral del watchdog (10 s) sin que nada lo relacione.
**Por qué:** al pasar a remoto hay que retocar 4-5 de ellos de forma coherente (ping < stale < watchdog); hoy es fácil dejarlos inconsistentes.
**Fix:** bloque de constantes nombradas en `constants.ts` (diff en diseño).
**Verificado cómo:** grep `setInterval|setTimeout` en `src`.

### [P2] Engine local huérfano al cerrar la app (sin tray ni indicación) → tras un auto-update corre UI nueva + engine viejo
**Archivo:** `desktop/src-tauri/src/lib.rs:81-90, 135`
**Evidencia:** ver P1 "engine local". Se separa porque afecta al modo local actual: cerrar BotStrike deja `botstrike-engine.exe` corriendo; la siguiente apertura detecta el puerto y se "reconecta" al proceso viejo. `check_for_updates` hace `app.restart()` (línea 135) sin tocar el engine.
**Por qué:** tras un auto-update el usuario ejecuta UI nueva + engine viejo hasta reiniciar Windows.
**Fix:** guardar `Child` y matarlo en `RunEvent::Exit` (diff en diseño) y antes de `app.restart()`.
**Verificado cómo:** Read `lib.rs`.

### [P3] Updater: configuración correcta; `blocking_show()` dentro de tarea async y resultado ignorado
**Archivo:** `desktop/src-tauri/tauri.conf.json:37-47`, `desktop/src-tauri/src/lib.rs:51-56, 101-137`
**Evidencia:** `pubkey` minisign presente; endpoint `https://github.com/FomoDonkey/BotStrike/releases/latest/download/latest.json`; `createUpdaterArtifacts: true`; `installMode: passive`. Verificado con `gh release view v2.11.1`: assets `BotStrike_2.11.1_x64-setup.exe(.sig)`, `BotStrike_2.11.1_x64_en-US.msi(.sig)`, `latest.json` con `version: 2.11.1`, `platforms.windows-x86_64.signature` presente. Código: `let _ = check_for_updates(handle).await` (error silenciado); `app.dialog().message(...).blocking_show()` (líneas 118, 133) bloquea un worker de tokio mientras el diálogo está abierto; log de progreso `if p%25==0` se imprime en cada chunk cuyo % caiga en múltiplo de 25 (spam).
**Por qué:** funcional hoy; deuda menor.
**Fix:** `tauri::async_runtime::spawn_blocking` para los diálogos o `.show(callback)`; loguear el `Err`; progreso con "último % impreso".
**Verificado cómo:** `gh release list/view/download latest.json`; Read `lib.rs`.

### [P3] Fuentes desde Google Fonts en el arranque (dependencia de red externa + CSP)
**Archivo:** `desktop/src/index.css:1`
**Evidencia:** `@import url("https://fonts.googleapis.com/css2?family=Inter...&family=JetBrains+Mono...")`.
**Por qué:** una app de escritorio de trading que sin Internet arranca con fuentes fallback tras el timeout del import; al añadir CSP hay que permitir `fonts.googleapis.com`/`fonts.gstatic.com`. Empaquetar las fuentes (woff2 en `public/fonts`) elimina ambas cosas.
**Fix:** self-host de Inter + JetBrains Mono; quitar el dominio de la CSP.
**Verificado cómo:** grep `https?://` en `index.css`.

### [P3] Dependencias muertas: `@tanstack/react-query`, `@tauri-apps/plugin-shell` (npm), `tauri-plugin-shell` (Cargo)
**Archivo:** `desktop/package.json:16-18`, `desktop/src-tauri/Cargo.toml:29`, `desktop/src-tauri/src/lib.rs:12`
**Evidencia:** grep `tanstack|QueryClient|useQuery` en `src` → 0; grep `@tauri-apps` en `src` → 0 (nota: `@tauri-apps/api` sí hará falta para el diseño propuesto — `invoke`). El enunciado menciona react-query, pero la app no lo usa: todo es `useEffect + useState + setInterval`.
**Fix:** desinstalar; o adoptar react-query de verdad (polling de `/api/bot/status`, `/api/performance`) — decisión aparte.
**Verificado cómo:** grep citados.

### [P3] `TopBar` lee `useExchangeStore.getState().exchange` durante el render → el badge HL/BIN no se actualiza al cambiar de exchange
**Archivo:** `desktop/src/components/layout/TopBar.tsx:116`
**Evidencia:** `getState()` fuera de hook; el componente no está suscrito al store de exchange.
**Fix:** `const exchange = useExchangeStore((s) => s.exchange);`
**Verificado cómo:** Read `TopBar.tsx`.

### [P3] `SystemPage`/`DataPage` se suscriben al store entero (`useSystemStore()`) → re-render en cada línea de log y cada health (3 s)
**Archivo:** `desktop/src/pages/system/SystemPage.tsx:14`, `desktop/src/pages/data/DataPage.tsx:21`
**Evidencia:** `const system = useSystemStore();` sin selector; `onLog` crea un array nuevo (`logs.slice(-199)`) por mensaje.
**Fix:** selectores (`useShallow`) para los campos usados; ya se hace bien en `TopBar`.
**Verificado cómo:** Read.

### [P3] Leaks/timers revisados — sin fugas reales, dos matices
**Archivo:** `desktop/src/stores/alertStore.ts:60-62`, `desktop/src/stores/systemStore.ts:26-36`, `desktop/src/stores/marketStore.ts:60-80`, `desktop/src/components/charts/CandlestickChart.tsx:133-142`, `Sidebar.tsx:51-52`, `TopBar.tsx:19-20`, `SystemPage.tsx:24-25`, `PerformancePage.tsx:68-69`
**Evidencia:** todos los `setInterval`/`addEventListener` en componentes tienen cleanup; `CandlestickChart` desconecta `ResizeObserver` y hace `chart.remove()`; `marketStore` auto-para su flush timer tras 40 ticks vacíos; `useWebSocketBridge` desuscribe los 5 handlers y `disconnectAll()`. Matices: (1) `alertStore.addAlert` programa `setTimeout` de 10/30 s sin handle (inofensivo: sólo filtra por id); (2) `systemStore` arranca el watchdog global en la creación del store y nunca lo para (singleton de módulo: aceptable).
**Verificado cómo:** grep `setInterval|addEventListener|removeEventListener` + Read de cada cleanup.

### [P3] Build: chunk principal 843 kB (recharts + framer-motion + react-router en un solo bundle)
**Archivo:** `desktop/vite.config.ts:16-18`
**Evidencia:** salida real de `vite build`: `index-B0Ko-n9Q.js 843.13 kB │ gzip: 252.01 kB` + warning `Some chunks are larger than 500 kB`. `lightweight-charts` sí va en chunk aparte (import dinámico).
**Fix:** `build.rolldownOptions.output.codeSplitting` / `manualChunks` para `recharts` y `framer-motion`, o import dinámico de las páginas.
**Verificado cómo:** `npm run build`.

### [P3] Hygiene de repo en `desktop/`
**Archivo:** `desktop/data/trade_database.db*` (untracked en `git status`), `desktop/logs/`, `desktop/.pytest_cache`, `desktop/README.md`
**Evidencia:** `.gitignore` de `desktop/` no cubre `data/` ni `.pytest_cache/`; README es la plantilla de Vite.
**Fix:** añadir a `.gitignore`; reescribir README.
**Verificado cómo:** `git status`, `ls desktop/data`, `cat desktop/.gitignore`.

---

## Diseño: "Bridge URL configurable" (NO implementado — diffs listos para aplicar)

### Principios
1. **Una sola fuente de verdad**: `src/lib/config.ts` guarda la URL (origin `http(s)://host:port`), la persiste en `localStorage["botstrike-bridge-url"]` (mismo patrón `safeGet/SetItem` que ya usan `exchangeStore`/`themeStore`; no hace falta `tauri-plugin-store`) y deriva `getBridgeUrl()` / `getBridgeWsUrl()` / `getBridgeMode()` (`local` si el host es loopback, `remote` en otro caso). Hook `useBridgeUrl()` vía `useSyncExternalStore` para la UI.
2. **La URL se resuelve en el momento de usarla**, no en constructores ni constantes: `api.request()` y `WebSocketClient.connect()` llaman `getBridgeUrl()`/`getBridgeWsUrl()` cada vez → cambiar la URL sólo requiere `disconnectAll(); connectAll()`.
3. **Engine local sólo si la URL es loopback**: Rust deja de lanzar el engine en `setup`; expone `ensure_local_engine(port)`; el frontend lo invoca en `startWebSockets()` cuando `isLocalBridge()`. En remoto no se toca ningún proceso local y la UI muestra `REMOTE`.
4. **Estado de conexión honesto**: `ws.ts` notifica apertura/cierre por canal → `systemStore.openChannels`; `bridgeConnected` cae a `false` en cuanto no queda ningún socket abierto (sin esperar 10 s), y se detectan sockets medio-abiertos por ausencia de `pong`.
5. **CSP explícita** que permita `http://*:9420` / `ws://*:9420` (+ loopback en cualquier puerto para desarrollo). El bridge **no** necesita cambios de CORS (origen `http://tauri.localhost` ya está en la regex).
6. Entrada de URL tolerante: acepta `192.168.1.204`, `192.168.1.204:9420`, `http://100.x.y.z:9420/`, `https://bridge.tailnet.ts.net` (si algún día va detrás de TLS); se normaliza a origin y si no hay puerto se añade `9420`.

7. **Auth token para LIVE** (alineado con el bridge actual): el bridge lee `BOTSTRIKE_AUTH_TOKEN` del `.env` del servidor y `GET /api/bot/status` **ya no** devuelve `auth_token` cuando escucha en `0.0.0.0`. Por tanto el desktop NO puede descubrir el token: lo guarda el usuario en Settings → Connection (`localStorage["botstrike-bridge-token"]`, `config.ts`) y `api.ts` lo añade como `&token=` en `POST /api/bot/start?mode=live` y en `POST /api/bot/stop` (Diff 3, `withToken`). Sin token guardado, start/stop en live fallan con alerta visible (Diff 11), nunca en silencio.

### Flujo de usuario
- Primera vez: overlay "setup" → muestra la URL actual (`127.0.0.1:9420 · LOCAL`) con enlace "Change" que lleva a Settings → Connection. Connect → `connecting` (muestra URL real) → `connected` → dismiss. Si en 15 s no hay health → `unreachable` con Retry / Settings / Dismiss.
- Settings → Connection: input URL, botón **Test** (GET `/api/health` con timeout 4 s contra la URL del input, sin guardar; muestra `ok · engine running/stopped · mode · N ms` o el error), botón **Save & Reconnect** (normaliza, persiste, `restartWebSockets()`), badge LOCAL/REMOTE, estado de canales abiertos (`4/5`), y **Auth token** (opcional, persistido; se envía en `botStart/botStop` — cierra el P1 del live start).
- TopBar: badge `LOCAL`/`REMOTE` con `title=url`; punto verde = bridge alcanzable; icono Wifi verde = además hay feed de mercado, ámbar = bridge OK sin feed, rojo = sin bridge.

---

### Diff 1 — `desktop/src/lib/config.ts` (NUEVO)
```ts
// Single source of truth for the bridge endpoint (REST + WS).
// Persisted in localStorage; resolved lazily by api.ts / ws.ts on every call.
import { useSyncExternalStore } from "react";

export const DEFAULT_BRIDGE_PORT = 9420;
export const DEFAULT_BRIDGE_URL = `http://127.0.0.1:${DEFAULT_BRIDGE_PORT}`;
const URL_KEY = "botstrike-bridge-url";
const TOKEN_KEY = "botstrike-bridge-token";
const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "0.0.0.0"]);

export type BridgeMode = "local" | "remote";

/** "192.168.1.204" | "host:9420" | "http://host:9420/x" → "http://host:9420" (origin). null if invalid. */
export function normalizeBridgeUrl(raw: string): string | null {
  let s = raw.trim();
  if (!s) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = `http://${s}`;
  let u: URL;
  try { u = new URL(s); } catch { return null; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  if (!u.hostname) return null;
  if (!u.port) u.port = String(DEFAULT_BRIDGE_PORT);
  return u.origin;
}

function safeGet(key: string): string | null { try { return localStorage.getItem(key); } catch { return null; } }
function safeSet(key: string, v: string) { try { localStorage.setItem(key, v); } catch { /* ignore */ } }

let currentUrl: string = (() => { const n = normalizeBridgeUrl(safeGet(URL_KEY) ?? ""); return n ?? DEFAULT_BRIDGE_URL; })();
let currentToken: string = safeGet(TOKEN_KEY) ?? "";
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

export function getBridgeUrl(): string { return currentUrl; }
export function getBridgeWsUrl(url: string = currentUrl): string { return url.replace(/^http/i, "ws"); }
export function getBridgeMode(url: string = currentUrl): BridgeMode {
  try { return LOCAL_HOSTS.has(new URL(url).hostname) ? "local" : "remote"; } catch { return "remote"; }
}
export function isLocalBridge(url: string = currentUrl): boolean { return getBridgeMode(url) === "local"; }
export function getBridgePort(url: string = currentUrl): number {
  try { return Number(new URL(url).port) || DEFAULT_BRIDGE_PORT; } catch { return DEFAULT_BRIDGE_PORT; }
}
export function getBridgeToken(): string { return currentToken; }

/** Persist + notify. Returns normalized URL, or null (state unchanged) if invalid. */
export function setBridgeUrl(raw: string): string | null {
  const n = normalizeBridgeUrl(raw);
  if (!n) return null;
  if (n !== currentUrl) { currentUrl = n; safeSet(URL_KEY, n); notify(); }
  return n;
}
export function setBridgeToken(token: string) {
  const t = token.trim();
  if (t !== currentToken) { currentToken = t; safeSet(TOKEN_KEY, t); notify(); }
}

export function subscribeBridgeConfig(l: () => void): () => void { listeners.add(l); return () => { listeners.delete(l); }; }
export function useBridgeUrl(): string { return useSyncExternalStore(subscribeBridgeConfig, getBridgeUrl, getBridgeUrl); }
export function useBridgeToken(): string { return useSyncExternalStore(subscribeBridgeConfig, getBridgeToken, getBridgeToken); }
```

### Diff 2 — `desktop/src/lib/constants.ts`
```diff
-export const BRIDGE_URL = "http://127.0.0.1:9420";
-export const BRIDGE_WS_URL = "ws://127.0.0.1:9420";
+// Bridge endpoint lives in ./config (configurable). Timings below are the only magic numbers allowed.
+export const API_TIMEOUT_MS = 30_000;          // generic REST call
+export const HEALTH_TIMEOUT_MS = 4_000;        // /api/health probe (Settings → Test, overlay)
+export const WS_PING_MS = 10_000;              // client → bridge {"type":"ping"}
+export const WS_STALE_MS = 25_000;             // no message/pong for this long → socket is half-open → force reconnect
+export const WS_RECONNECT_BASE_MS = 3_000;
+export const WS_RECONNECT_MAX_MS = 30_000;
+export const WS_STAGGER_MS = 500;              // delay between the 5 channel connects
+export const HEALTH_WATCHDOG_TICK_MS = 5_000;
+export const HEALTH_STALE_MS = 10_000;         // bridge health arrives every 3 s; >10 s → not connected
+export const OVERLAY_CONNECTED_MS = 1_000;     // "Connected" splash before auto-dismiss
+export const OVERLAY_CONNECT_TIMEOUT_MS = 15_000; // "connecting" → "unreachable"

 export const WS_CHANNELS = {
   MARKET: "market",
   TRADING: "trading",
   MICRO: "micro",
   RISK: "risk",
   SYSTEM: "system",
 } as const;
+export const WS_CHANNEL_LIST: readonly string[] = Object.values(WS_CHANNELS);
```

### Diff 3 — `desktop/src/lib/api.ts` (reemplazo completo)
```ts
import { getBridgeUrl, getBridgeToken } from "./config";
import { API_TIMEOUT_MS, HEALTH_TIMEOUT_MS } from "./constants";

export class ApiError extends Error {
  constructor(message: string, public readonly status?: number) { super(message); this.name = "ApiError"; }
}

export interface HealthResponse {
  status: string; engine_running: boolean; mode: string; uptime_sec: number; clients: number;
}

type RequestOpts = RequestInit & { timeoutMs?: number; baseUrl?: string };

async function request<T = unknown>(path: string, opts: RequestOpts = {}): Promise<T> {
  const { timeoutMs = API_TIMEOUT_MS, baseUrl = getBridgeUrl(), headers, ...init } = opts;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${baseUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(headers as Record<string, string> | undefined) },
    });
    if (!res.ok) throw new ApiError(`HTTP ${res.status} ${path}`, res.status);
    const body: unknown = await res.json();
    // The bridge answers 200 + {"error": "..."} for auth/validation failures — treat as failure.
    if (body && typeof body === "object" && typeof (body as { error?: unknown }).error === "string") {
      throw new ApiError((body as { error: string }).error, res.status);
    }
    return body as T;
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw new ApiError(`Timeout after ${timeoutMs} ms: ${baseUrl}${path}`);
    if (e instanceof TypeError) throw new ApiError(`Cannot reach ${baseUrl} (${e.message})`); // network/CORS/CSP
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/** Reachability probe against an explicit URL (used by Settings → Test before saving). */
export function probeBridge(baseUrl: string): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", { baseUrl, timeoutMs: HEALTH_TIMEOUT_MS });
}

const withToken = (qs: string) => { const t = getBridgeToken(); return t ? `${qs}&token=${encodeURIComponent(t)}` : qs; };

export const api = {
  health: () => request<HealthResponse>("/api/health", { timeoutMs: HEALTH_TIMEOUT_MS }),
  config: () => request("/api/config"),
  botStatus: () => request("/api/bot/status"),
  botStart: (mode = "paper", exchange = "binance") =>
    request(withToken(`/api/bot/start?mode=${mode}&exchange=${exchange}`), { method: "POST" }),
  botStop: () => request(withToken("/api/bot/stop?_=1"), { method: "POST" }),
  performance: () => request("/api/performance"),
  strategies: () => request("/api/strategies"),
  trades: (limit = 100) => request(`/api/trades?limit=${limit}`),
  dataCatalog: () => request("/api/data/catalog"),
  backtestRun: (body: { symbol: string; strategy: string; exchange: string }) =>
    request("/api/backtest/run", { method: "POST", body: JSON.stringify(body), timeoutMs: 10 * 60_000 }),
};
```
Nota: los tipos de retorno pasan de `any` a `unknown` → los callers que hacen `data.trading` necesitarán `as ConfigData` o interfaces (esto también arregla 9 errores de lint). Si se prefiere no tocar callers en este paso, dejar `request<any>` temporalmente.

### Diff 4 — `desktop/src/lib/ws.ts` (reemplazo completo)
```ts
import { getBridgeWsUrl } from "./config";
import {
  WS_CHANNEL_LIST, WS_PING_MS, WS_STALE_MS, WS_RECONNECT_BASE_MS, WS_RECONNECT_MAX_MS, WS_STAGGER_MS,
} from "./constants";

type MessageHandler = (data: unknown) => void;
type StatusListener = (channel: string, open: boolean) => void;

const statusListeners = new Set<StatusListener>();
/** Subscribe to per-channel open/close transitions (used by systemStore). */
export function onChannelStatus(l: StatusListener): () => void {
  statusListeners.add(l);
  return () => { statusListeners.delete(l); };
}

class WebSocketClient {
  private ws: WebSocket | null = null;
  private readonly channel: string;
  private handlers = new Set<MessageHandler>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private _connected = false;
  private _reconnectAttempts = 0;
  private wantOpen = false; // user intent — a close after disconnect() must not reconnect

  constructor(channel: string) { this.channel = channel; }
  get connected() { return this._connected; }

  connect() {
    this.wantOpen = true;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    // Resolved on every connect → Settings changes take effect on the next (re)connect.
    const url = `${getBridgeWsUrl()}/ws/${this.channel}`;
    let ws: WebSocket;
    try { ws = new WebSocket(url); } catch { this.setClosed(); this.scheduleReconnect(); return; }
    this.ws = ws;

    ws.onopen = () => {
      if (this.ws !== ws) return;
      this._connected = true;
      this._reconnectAttempts = 0;
      this.lastMessageAt = Date.now();
      this.startPing();
      this.emit(true);
    };
    ws.onmessage = (ev) => {
      this.lastMessageAt = Date.now();
      let data: unknown;
      try { data = JSON.parse(ev.data); } catch { return; }
      if ((data as { type?: string } | null)?.type === "pong") return;
      this.handlers.forEach((h) => h(data));
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.setClosed();
      this.scheduleReconnect();
    };
    ws.onerror = () => { /* always followed by onclose */ };
  }

  disconnect() {
    this.wantOpen = false;
    this._reconnectAttempts = 0;
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    this.dropSocket();
    this.setClosed();
  }

  subscribe(handler: MessageHandler) {
    this.handlers.add(handler);
    return () => { this.handlers.delete(handler); };
  }

  /** Detach handlers + close without waiting for the close handshake (may take 60 s on a dead peer). */
  private dropSocket() {
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
    try { ws.close(); } catch { /* already closed */ }
  }

  private setClosed() {
    this.stopPing();
    if (this._connected) { this._connected = false; this.emit(false); }
  }

  private emit(open: boolean) { statusListeners.forEach((l) => l(this.channel, open)); }

  private startPing() {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      if (Date.now() - this.lastMessageAt > WS_STALE_MS) {
        // Half-open TCP (LAN/Tailscale): no pong and no broadcast → treat as dead and reconnect now.
        this.dropSocket();
        this.setClosed();
        this.scheduleReconnect();
        return;
      }
      this.ws.send(JSON.stringify({ type: "ping" }));
    }, WS_PING_MS);
  }

  private stopPing() {
    if (this.pingTimer) { clearInterval(this.pingTimer); this.pingTimer = null; }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer || !this.wantOpen) return;
    this._reconnectAttempts++;
    const base = Math.min(WS_RECONNECT_BASE_MS * 2 ** Math.min(this._reconnectAttempts - 1, 4), WS_RECONNECT_MAX_MS);
    const delay = base * (0.5 + Math.random()); // 50-150 % jitter
    this.reconnectTimer = setTimeout(() => { this.reconnectTimer = null; this.connect(); }, delay);
  }
}

const channels = new Map<string, WebSocketClient>();
let staggerTimers: ReturnType<typeof setTimeout>[] = [];
let started = false;

export function getChannel(channel: string): WebSocketClient {
  let c = channels.get(channel);
  if (!c) { c = new WebSocketClient(channel); channels.set(channel, c); }
  return c;
}

export function connectAll() {
  clearStagger();
  started = true;
  WS_CHANNEL_LIST.forEach((ch, i) => {
    staggerTimers.push(setTimeout(() => getChannel(ch).connect(), i * WS_STAGGER_MS));
  });
}

export function disconnectAll() {
  clearStagger();
  started = false;
  channels.forEach((c) => c.disconnect());
}

export function isStarted() { return started; }

function clearStagger() {
  staggerTimers.forEach(clearTimeout);
  staggerTimers = [];
}
```

### Diff 5 — `desktop/src/lib/engine.ts` (NUEVO — puente hacia el comando Rust)
```ts
import { invoke } from "@tauri-apps/api/core";
import { getBridgePort, isLocalBridge } from "./config";

let inFlight: Promise<void> | null = null;

/** Ask the Rust side to start the bundled engine if nothing listens on the local port.
 *  No-op when the configured bridge is remote or when not running inside Tauri (plain `vite`). */
export function ensureLocalEngine(): Promise<void> {
  if (!isLocalBridge()) return Promise.resolve();
  if (inFlight) return inFlight;
  inFlight = invoke<string>("ensure_local_engine", { port: getBridgePort() })
    .then((msg) => { console.info("[engine]", msg); })
    .catch((e) => { console.warn("[engine] ensure_local_engine:", e); })
    .finally(() => { inFlight = null; });
  return inFlight;
}
```

### Diff 6 — `desktop/src/stores/systemStore.ts`
```diff
 import { create } from "zustand";
+import { HEALTH_STALE_MS, HEALTH_WATCHDOG_TICK_MS } from "@/lib/constants";
@@ interface SystemState {
   bridgeConnected: boolean;
+  openChannels: string[];        // WS channels currently open to the bridge
   _lastHealthAt: number;
   logs: LogEntry[];
@@
   setBridgeConnected: (v: boolean) => void;
+  onChannelStatus: (channel: string, open: boolean) => void;
+  resetConnection: () => void;
 }
@@ function startHealthWatchdog() {
-    if (state.bridgeConnected && Date.now() - state._lastHealthAt > 10000) {
+    if (state.bridgeConnected && Date.now() - state._lastHealthAt > HEALTH_STALE_MS) {
       useSystemStore.setState({ bridgeConnected: false, engineRunning: false });
     }
-  }, 5000);
+  }, HEALTH_WATCHDOG_TICK_MS);
 }
@@
     bridgeConnected: false,
+    openChannels: [],
     _lastHealthAt: 0,
@@
     onHealth: (data) =>
       set({
         engineRunning: data.engine_running ?? false,
@@
         clientsConnected: data.clients_connected ?? 0,
         _lastHealthAt: Date.now(),
+        bridgeConnected: true,
       }),
@@
     setBridgeConnected: (v) => set({ bridgeConnected: v }),
+
+    onChannelStatus: (channel, open) =>
+      set((s) => {
+        const next = new Set(s.openChannels);
+        if (open) next.add(channel); else next.delete(channel);
+        const openChannels = [...next];
+        // Lost the last socket → bridge unreachable right now; don't wait for the 10 s watchdog.
+        return openChannels.length === 0 && s.bridgeConnected
+          ? { openChannels, bridgeConnected: false, engineRunning: false }
+          : { openChannels };
+      }),
+
+    resetConnection: () =>
+      set({ bridgeConnected: false, engineRunning: false, openChannels: [], _lastHealthAt: 0, uptimeSec: 0 }),
   };
 });
```

### Diff 7 — `desktop/src/hooks/useWebSocket.ts`
```diff
 import { useEffect } from "react";
-import { connectAll, disconnectAll, getChannel } from "@/lib/ws";
+import { connectAll, disconnectAll, getChannel, onChannelStatus } from "@/lib/ws";
+import { ensureLocalEngine } from "@/lib/engine";
@@
 export function startWebSockets() {
+  void ensureLocalEngine();   // no-op unless the configured bridge is loopback
   connectAll();
 }
+
+/** Used by Settings → "Save & Reconnect": tear everything down and reconnect against the current URL. */
+export function restartWebSockets() {
+  disconnectAll();
+  useSystemStore.getState().resetConnection();
+  startWebSockets();
+}
@@ export function useWebSocketBridge() {
   useEffect(() => {
+    const unsubStatus = onChannelStatus((ch, open) => useSystemStore.getState().onChannelStatus(ch, open));
@@
     const unsubSystem = getChannel("system").subscribe((msg) => {
       try {
         if (msg.type === "health") {
           useSystemStore.getState().onHealth(msg);
-          useSystemStore.getState().setBridgeConnected(true);
         } else if (msg.type === "log") {
@@
     return () => {
+      unsubStatus();
       unsubMarket();
```
(Los `msg.type` pasan a `unknown` con el nuevo `MessageHandler`; añadir `const m = msg as { type?: string; [k: string]: any }` al inicio de cada handler o un tipo `BridgeMessage` — mismo cambio que exige el lint.)

### Diff 8 — `desktop/src/components/shared/ConnectionOverlay.tsx` (reemplazo completo)
```tsx
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Wifi, WifiOff, Loader2, Play, Settings2 } from "lucide-react";
import { useSystemStore } from "@/stores/systemStore";
import { useExchangeStore } from "@/stores/exchangeStore";
import { ExchangeSelector } from "./ExchangeSelector";
import { startWebSockets, restartWebSockets } from "@/hooks/useWebSocket";
import { useBridgeUrl, getBridgeMode } from "@/lib/config";
import { OVERLAY_CONNECTED_MS, OVERLAY_CONNECT_TIMEOUT_MS } from "@/lib/constants";

type Phase = "setup" | "connecting" | "unreachable" | "connected" | "dismissed";

export function ConnectionOverlay() {
  const bridgeConnected = useSystemStore((s) => s.bridgeConnected);
  const exchange = useExchangeStore((s) => s.exchange);
  const bridgeUrl = useBridgeUrl();
  const mode = getBridgeMode(bridgeUrl);
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>("setup");
  const phaseRef = useRef<Phase>(phase);
  phaseRef.current = phase;

  // Bridge became reachable while we were waiting → flash "Connected", then auto-dismiss.
  // phaseRef avoids the stale-closure/eslint-disable of v2.11.1 and survives a <1 s flap.
  useEffect(() => {
    if (!bridgeConnected) return;
    const p = phaseRef.current;
    if (p !== "connecting" && p !== "unreachable") return;
    setPhase("connected");
    const t = setTimeout(() => setPhase("dismissed"), OVERLAY_CONNECTED_MS);
    return () => clearTimeout(t);
  }, [bridgeConnected]);

  // Nothing after N seconds → tell the user which URL failed instead of spinning forever.
  useEffect(() => {
    if (phase !== "connecting") return;
    const t = setTimeout(() => setPhase((p) => (p === "connecting" ? "unreachable" : p)), OVERLAY_CONNECT_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [phase]);

  if (phase === "dismissed") return null;

  const hostLabel = bridgeUrl.replace(/^https?:\/\//, "");
  const ModeBadge = () => (
    <span className={`ml-2 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${mode === "remote" ? "bg-info/10 text-info" : "bg-white/5 text-text-muted"}`}>
      {mode}
    </span>
  );
  const goSettings = () => { setPhase("dismissed"); navigate("/settings", { state: { tab: "connection" } }); };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-base/90 backdrop-blur-md">
      <div className="rounded-2xl bg-bg-surface border border-white/10 p-8 max-w-lg w-full text-center shadow-2xl">

        {phase === "setup" && (
          <>
            <h2 className="text-2xl font-bold text-text-primary mb-1">BotStrike</h2>
            <p className="text-sm text-text-secondary mb-2">Select your exchange to get started</p>
            <p className="text-xs text-text-muted mb-6">
              Bridge: <span className="font-mono text-accent">{hostLabel}</span><ModeBadge />
              <button onClick={goSettings} className="ml-2 underline hover:text-text-secondary">change</button>
            </p>
            <ExchangeSelector />
            <div className="flex gap-3 mt-6">
              <button onClick={() => { startWebSockets(); setPhase("connecting"); }}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-accent text-bg-base font-semibold text-sm hover:bg-accent/90 transition-all">
                <Play className="w-4 h-4" /> Connect
              </button>
              <button onClick={() => { startWebSockets(); setPhase("dismissed"); }}
                className="px-4 py-3 rounded-xl border border-white/10 text-text-muted text-sm hover:border-white/20 transition-all">
                Skip
              </button>
            </div>
          </>
        )}

        {phase === "connecting" && (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-warning/10 flex items-center justify-center">
              <Loader2 className="w-8 h-8 text-warning animate-spin" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">Connecting to Bridge...</h2>
            <p className="text-sm text-text-secondary mb-1">
              Exchange: <span className="font-mono text-accent uppercase">{exchange}</span>
            </p>
            <p className="text-sm text-text-secondary mb-4">
              Waiting for <span className="font-mono text-accent">{hostLabel}</span><ModeBadge />
            </p>
            {mode === "local" && (
              <div className="text-xs text-text-muted bg-bg-base/50 rounded-lg p-3 font-mono text-left">
                Starting bundled engine… (or run: python -m server.bridge)
              </div>
            )}
            <button onClick={() => setPhase("dismissed")} className="mt-4 text-xs text-text-muted hover:text-text-secondary transition-colors">
              Dismiss — browse without data
            </button>
          </>
        )}

        {phase === "unreachable" && (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-loss/10 flex items-center justify-center">
              <WifiOff className="w-8 h-8 text-loss" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">Bridge unreachable</h2>
            <p className="text-sm text-text-secondary mb-4">
              No response from <span className="font-mono text-loss">{hostLabel}</span><ModeBadge />
              {mode === "remote" && <span className="block text-xs text-text-muted mt-1">Check the server, firewall (ufw :9420) and that you are on the LAN / Tailscale.</span>}
            </p>
            <div className="flex gap-3">
              <button onClick={() => { restartWebSockets(); setPhase("connecting"); }}
                className="flex-1 px-4 py-2.5 rounded-xl bg-accent text-bg-base font-semibold text-sm">Retry</button>
              <button onClick={goSettings}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-white/10 text-text-secondary text-sm">
                <Settings2 className="w-4 h-4" /> Connection settings
              </button>
              <button onClick={() => setPhase("dismissed")} className="px-3 text-xs text-text-muted">Dismiss</button>
            </div>
          </>
        )}

        {phase === "connected" && (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-profit/10 flex items-center justify-center">
              <Wifi className="w-8 h-8 text-profit" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">Connected</h2>
            <p className="text-sm text-text-secondary">{hostLabel}<ModeBadge /></p>
          </>
        )}
      </div>
    </div>
  );
}
```

### Diff 9 — `desktop/src/pages/settings/SettingsPage.tsx` (tab nueva)
```diff
-import { useEffect, useState } from "react";
+import { useEffect, useState } from "react";
+import { useLocation } from "react-router-dom";
 import { motion } from "framer-motion";
 import { GlassPanel } from "@/components/shared/GlassPanel";
-import { api } from "@/lib/api";
-import { Settings, DollarSign, Shield, Zap, Bell, Server, Palette, Volume2, VolumeX } from "lucide-react";
+import { api, probeBridge, ApiError } from "@/lib/api";
+import { Settings, DollarSign, Shield, Zap, Bell, Server, Palette, Volume2, VolumeX, Plug } from "lucide-react";
 import { cn } from "@/lib/utils";
 import { useThemeStore, type ThemeVariant } from "@/stores/themeStore";
 import { useAlertStore } from "@/stores/alertStore";
+import { useSystemStore } from "@/stores/systemStore";
+import { useBridgeUrl, useBridgeToken, setBridgeUrl, setBridgeToken, normalizeBridgeUrl, getBridgeMode } from "@/lib/config";
+import { restartWebSockets } from "@/hooks/useWebSocket";
+import { WS_CHANNEL_LIST } from "@/lib/constants";
@@ const TABS = [
+  { id: "connection", label: "Connection", icon: Plug },
   { id: "capital", label: "Capital & Risk", icon: DollarSign },
@@
+type TestState = { state: "idle" | "testing" | "ok" | "fail"; detail?: string };
+
+function ConnectionSettings() {
+  const currentUrl = useBridgeUrl();
+  const currentToken = useBridgeToken();
+  const [draftUrl, setDraftUrl] = useState(currentUrl);
+  const [draftToken, setDraftToken] = useState(currentToken);
+  const [test, setTest] = useState<TestState>({ state: "idle" });
+  const openChannels = useSystemStore((s) => s.openChannels.length);
+  const bridgeConnected = useSystemStore((s) => s.bridgeConnected);
+
+  const normalized = normalizeBridgeUrl(draftUrl);
+  const dirty = normalized !== currentUrl || draftToken.trim() !== currentToken;
+  const mode = getBridgeMode(normalized ?? currentUrl);
+
+  const runTest = async () => {
+    if (!normalized) { setTest({ state: "fail", detail: "Invalid URL" }); return; }
+    setTest({ state: "testing" });
+    const t0 = Date.now();
+    try {
+      const h = await probeBridge(normalized);
+      setTest({ state: "ok", detail: `${h.status} · engine ${h.engine_running ? "running" : "stopped"} · ${h.mode} · ${Date.now() - t0} ms` });
+    } catch (e) {
+      setTest({ state: "fail", detail: e instanceof ApiError ? e.message : String(e) });
+    }
+  };
+
+  const save = () => {
+    const n = setBridgeUrl(draftUrl);
+    if (!n) { setTest({ state: "fail", detail: "Invalid URL" }); return; }
+    setBridgeToken(draftToken);
+    setDraftUrl(n);
+    restartWebSockets();
+    setTest({ state: "idle" });
+  };
+
+  return (
+    <div className="grid grid-cols-2 gap-4">
+      <GlassPanel className="p-5 col-span-2">
+        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
+          <Plug className="w-3 h-3" /> Bridge Server
+          <span className={cn("ml-auto px-2 py-0.5 rounded text-[10px] font-bold uppercase", mode === "remote" ? "bg-info/10 text-info" : "bg-white/5 text-text-muted")}>{mode}</span>
+        </h3>
+        <label className="text-xs text-text-muted block mb-1">Bridge URL (host[:port], http:// or https://)</label>
+        <input
+          value={draftUrl}
+          onChange={(e) => { setDraftUrl(e.target.value); setTest({ state: "idle" }); }}
+          spellCheck={false}
+          placeholder="http://192.168.1.204:9420"
+          className="w-full bg-bg-base border border-white/10 rounded-lg px-3 py-2 text-sm text-text-primary font-mono focus:outline-none focus:border-accent/50"
+        />
+        <p className="text-[10px] text-text-muted mt-1">
+          Local: <code>127.0.0.1:9420</code> (bundled engine is started automatically) · LAN: <code>192.168.1.204:9420</code> · Tailscale: <code>100.x.y.z:9420</code>
+        </p>
+        <label className="text-xs text-text-muted block mt-4 mb-1">Auth token (required to start/stop LIVE — from the server's startup log)</label>
+        <input
+          value={draftToken}
+          onChange={(e) => setDraftToken(e.target.value)}
+          type="password" spellCheck={false} autoComplete="off"
+          className="w-full bg-bg-base border border-white/10 rounded-lg px-3 py-2 text-sm text-text-primary font-mono focus:outline-none focus:border-accent/50"
+        />
+        <div className="flex items-center gap-3 mt-4">
+          <button onClick={runTest} disabled={test.state === "testing" || !normalized}
+            className="px-4 py-2 rounded-lg border border-white/10 text-sm text-text-secondary hover:border-white/20 disabled:opacity-50">
+            {test.state === "testing" ? "Testing…" : "Test"}
+          </button>
+          <button onClick={save} disabled={!dirty || !normalized}
+            className="px-4 py-2 rounded-lg bg-accent text-bg-base text-sm font-semibold disabled:opacity-40">
+            Save & Reconnect
+          </button>
+          {test.state === "ok" && <span className="text-xs font-mono text-profit">{test.detail}</span>}
+          {test.state === "fail" && <span className="text-xs font-mono text-loss">{test.detail}</span>}
+        </div>
+        <div className="mt-4 pt-4 border-t border-white/5 grid grid-cols-3 text-xs">
+          <div><span className="text-text-muted">Active URL</span><p className="font-mono text-text-secondary">{currentUrl}</p></div>
+          <div><span className="text-text-muted">Bridge</span><p className={cn("font-mono", bridgeConnected ? "text-profit" : "text-loss")}>{bridgeConnected ? "ONLINE" : "OFFLINE"}</p></div>
+          <div><span className="text-text-muted">WS channels</span><p className="font-mono text-text-secondary">{openChannels}/{WS_CHANNEL_LIST.length}</p></div>
+        </div>
+      </GlassPanel>
+    </div>
+  );
+}
+
 export function SettingsPage() {
+  const location = useLocation();
+  const initialTab = (location.state as { tab?: string } | null)?.tab ?? "capital";
   const [config, setConfig] = useState<ConfigData | null>(null);
   const [loading, setLoading] = useState(true);
-  const [tab, setTab] = useState("capital");
+  const [tab, setTab] = useState(initialTab);
@@
-      {loading ? (
+      {tab === "connection" ? (
+        <ConnectionSettings />
+      ) : loading ? (
         <GlassPanel className="p-8 text-center">
```

### Diff 10 — `desktop/src/components/layout/TopBar.tsx`
```diff
 import { SYMBOLS, SYMBOL_LABELS } from "@/lib/constants";
 import { useExchangeStore } from "@/stores/exchangeStore";
+import { useBridgeUrl, getBridgeMode } from "@/lib/config";
@@ export function TopBar() {
   const hasPrices = useMarketStore((s) => Object.keys(s.prices).length > 0);
-  const isConnected = bridgeConnected && (wsConnected || hasPrices);
+  const hasFeed = bridgeConnected && (wsConnected || hasPrices);
   const regime = useRiskStore((s) => s.regime);
+  const exchange = useExchangeStore((s) => s.exchange);
+  const bridgeUrl = useBridgeUrl();
+  const bridgeMode = getBridgeMode(bridgeUrl);
@@
-        <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-white/5 text-text-muted">
-          {useExchangeStore.getState().exchange === "hyperliquid" ? "HL" : "BIN"}
-        </span>
+        <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-white/5 text-text-muted">
+          {exchange === "hyperliquid" ? "HL" : "BIN"}
+        </span>
+        <span title={bridgeUrl}
+          className={cn("px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider",
+            bridgeMode === "remote" ? "bg-info/10 text-info" : "bg-white/5 text-text-muted")}>
+          {bridgeMode}
+        </span>
@@
-        <div className="flex items-center gap-1.5">
-          <PulsingDot active={isConnected} />
-          {isConnected ? <Wifi className="w-3 h-3 text-accent" /> : <WifiOff className="w-3 h-3 text-loss" />}
-        </div>
+        <div className="flex items-center gap-1.5" title={bridgeConnected ? (hasFeed ? "Bridge online · market feed live" : "Bridge online · engine stopped / no feed") : `Bridge unreachable: ${bridgeUrl}`}>
+          <PulsingDot active={bridgeConnected} />
+          {!bridgeConnected ? <WifiOff className="w-3 h-3 text-loss" />
+            : <Wifi className={cn("w-3 h-3", hasFeed ? "text-accent" : "text-warning")} />}
+        </div>
```

### Diff 11 — `desktop/src/pages/system/SystemPage.tsx`
```diff
+import { useBridgeUrl, getBridgeMode } from "@/lib/config";
+import { useAlertStore } from "@/stores/alertStore";
+import { ApiError } from "@/lib/api";
@@ export function SystemPage() {
+  const bridgeUrl = useBridgeUrl();
@@
-  const handleStart = () => api.botStart(startMode, exchange).catch(() => null);
-  const handleStop = () => api.botStop().catch(() => null);
+  const report = (title: string) => (e: unknown) =>
+    useAlertStore.getState().addAlert({ level: "critical", title, message: e instanceof ApiError ? e.message : String(e), sound: "alert" });
+  const handleStart = () => api.botStart(startMode, exchange).catch(report("Start failed"));
+  const handleStop = () => api.botStop().catch(report("Stop failed"));
@@
-              { name: "Bridge Server", connected: system.bridgeConnected, detail: "localhost:9420" },
+              { name: `Bridge Server (${getBridgeMode(bridgeUrl)})`, connected: system.bridgeConnected, detail: bridgeUrl.replace(/^https?:\/\//, "") },
@@
-                <span className="font-mono text-text-secondary">2.11.1</span>
+                <span className="font-mono text-text-secondary">{__APP_VERSION__}</span>
```
(+ en `vite.config.ts`: `import pkg from "./package.json"` y `define: { __APP_VERSION__: JSON.stringify(pkg.version) }`; en `src/vite-env.d.ts`: `declare const __APP_VERSION__: string;`; `tsconfig.node.json` necesita `"resolveJsonModule": true`.)

### Diff 12 — `desktop/src/pages/backtest/BacktestPage.tsx`
```diff
-import { BRIDGE_URL, STRATEGY_LABELS } from "@/lib/constants";
+import { STRATEGY_LABELS } from "@/lib/constants";
+import { api, ApiError } from "@/lib/api";
+import { getBridgeUrl } from "@/lib/config";
@@
-      const res = await fetch(`${BRIDGE_URL}/api/backtest/run`, {
-        method: "POST",
-        headers: { "Content-Type": "application/json" },
-        body: JSON.stringify({ symbol, strategy, exchange: useExchangeStore.getState().exchange }),
-      });
-      const data = await res.json();
-      if (data.error) {
-        setError(data.error);
-      } else {
-        setResult(data);
-      }
-    } catch {
-      setError("Bridge server not running. Start with: python -m server.bridge");
+      const data = await api.backtestRun({ symbol, strategy, exchange: useExchangeStore.getState().exchange });
+      setResult(data as BacktestResult);
+    } catch (e) {
+      setError(e instanceof ApiError ? e.message : `Bridge unreachable at ${getBridgeUrl()}`);
     }
```

### Diff 13 — `desktop/src-tauri/src/lib.rs`
```diff
 use tauri::Manager;
 use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
 use tauri_plugin_updater::UpdaterExt;
-use std::process::Command;
+use std::net::{SocketAddr, TcpStream};
+use std::path::PathBuf;
+use std::process::{Child, Command};
+use std::sync::Mutex;
+use std::time::Duration;
+
+/// Engine process we spawned ourselves (None if a pre-existing bridge was found or bridge is remote).
+struct EngineProc(Mutex<Option<Child>>);

 #[cfg_attr(mobile, tauri::mobile_entry_point)]
 pub fn run() {
     tauri::Builder::default()
         .plugin(tauri_plugin_updater::Builder::new().build())
         .plugin(tauri_plugin_dialog::init())
         .plugin(tauri_plugin_process::init())
-        .plugin(tauri_plugin_shell::init())
+        .manage(EngineProc(Mutex::new(None)))
+        .invoke_handler(tauri::generate_handler![ensure_local_engine])
         .setup(|app| {
@@
-            // Collect possible engine paths (try all known locations)
-            let mut engine_paths: Vec<std::path::PathBuf> = Vec::new();
-
-            // Resource dir (bundled with app)
-            if let Ok(resource) = app.path().resource_dir() {
-                engine_paths.push(resource.join("binaries").join("engine").join("botstrike-engine.exe"));
-            }
-            // Next to main exe (NSIS install: AppData/Local/BotStrike/)
-            if let Ok(exe) = std::env::current_exe() {
-                if let Some(dir) = exe.parent() {
-                    // Most common: binaries/engine/ next to the app exe
-                    engine_paths.push(dir.join("binaries").join("engine").join("botstrike-engine.exe"));
-                    engine_paths.push(dir.join("engine").join("botstrike-engine.exe"));
-                    engine_paths.push(dir.join("botstrike-engine.exe"));
-                }
-            }
-
-            std::thread::spawn(move || {
-                launch_engine(&engine_paths);
-            });
+            // Engine is NOT launched here any more: the frontend calls `ensure_local_engine`
+            // only when the configured bridge URL is loopback (Settings → Connection).

             // Auto-update
@@
-        .run(tauri::generate_context!())
-        .expect("error while running tauri application");
+        .build(tauri::generate_context!())
+        .expect("error while building tauri application")
+        .run(|app, event| {
+            if let tauri::RunEvent::Exit = event {
+                kill_engine(app);
+            }
+        });
 }

-fn launch_engine(paths: &[std::path::PathBuf]) {
-    // Check if bridge already running
-    if std::net::TcpStream::connect("127.0.0.1:9420").is_ok() {
-        log::info!("Bridge already running on :9420, skipping engine launch");
-        return;
-    }
+fn engine_candidate_paths(app: &tauri::AppHandle) -> Vec<PathBuf> {
+    let mut paths = Vec::new();
+    if let Ok(resource) = app.path().resource_dir() {
+        paths.push(resource.join("binaries").join("engine").join("botstrike-engine.exe"));
+    }
+    if let Ok(exe) = std::env::current_exe() {
+        if let Some(dir) = exe.parent() {
+            paths.push(dir.join("binaries").join("engine").join("botstrike-engine.exe"));
+            paths.push(dir.join("engine").join("botstrike-engine.exe"));
+            paths.push(dir.join("botstrike-engine.exe"));
+        }
+    }
+    paths
+}
+
+fn port_open(port: u16) -> bool {
+    let addr = SocketAddr::from(([127, 0, 0, 1], port));
+    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
+}
+
+/// Called by the frontend (src/lib/engine.ts) only when the bridge URL is loopback.
+/// Idempotent: returns early if something already listens on the port or if we already spawned.
+#[tauri::command]
+async fn ensure_local_engine(app: tauri::AppHandle, port: u16) -> Result<String, String> {
+    if port_open(port) {
+        return Ok(format!("bridge already listening on 127.0.0.1:{port}"));
+    }
+    let state = app.state::<EngineProc>();
+    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
+    if let Some(child) = guard.as_mut() {
+        if matches!(child.try_wait(), Ok(None)) {
+            return Ok(format!("engine already spawned (pid {})", child.id()));
+        }
+    }
+    let paths = engine_candidate_paths(&app);
+    let child = launch_engine(&paths, port)?;
+    let pid = child.id();
+    *guard = Some(child);
+    Ok(format!("engine started (pid {pid}) on 127.0.0.1:{port}"))
+}

+fn launch_engine(paths: &[PathBuf], port: u16) -> Result<Child, String> {
     for (i, path) in paths.iter().enumerate() {
         log::info!("Engine path [{}]: {} (exists: {})", i, path.display(), path.exists());
     }
     for path in paths {
-        if path.exists() {
-            log::info!("Launching engine: {}", path.display());
-
-            // Set working dir to engine folder (so _internal/ is found)
-            let work_dir = path.parent().unwrap_or(path);
-
-            match Command::new(path)
-                .current_dir(work_dir)
-                .stdout(std::process::Stdio::null())
-                .stderr(std::process::Stdio::null())
-                .spawn()
-            {
-                Ok(child) => {
-                    log::info!("Engine started (pid: {})", child.id());
-                    return;
-                }
-                Err(e) => {
-                    log::error!("Failed: {} — {}", path.display(), e);
-                }
-            }
-        }
+        if !path.exists() { continue; }
+        log::info!("Launching engine: {}", path.display());
+        let work_dir = path.parent().unwrap_or(path); // so PyInstaller's _internal/ is found
+        match Command::new(path)
+            .current_dir(work_dir)
+            .arg("--host").arg("127.0.0.1")
+            .arg("--port").arg(port.to_string())
+            .stdout(std::process::Stdio::null())
+            .stderr(std::process::Stdio::null())
+            .spawn()
+        {
+            Ok(child) => { log::info!("Engine started (pid: {})", child.id()); return Ok(child); }
+            Err(e) => log::error!("Failed: {} — {}", path.display(), e),
+        }
     }
-
-    log::warn!("Engine not found. Run manually: python -m server.bridge");
+    Err("Engine binary not found. Run manually: python -m server.bridge".into())
 }
+
+/// Kill the engine we spawned (never touches a pre-existing bridge). Called on Exit and before restart.
+fn kill_engine(app: &tauri::AppHandle) {
+    if let Ok(mut guard) = app.state::<EngineProc>().0.lock() {
+        if let Some(mut child) = guard.take() {
+            log::info!("Stopping engine (pid {})", child.id());
+            let _ = child.kill();
+            let _ = child.wait();
+        }
+    }
+}
@@ async fn check_for_updates
-    app.restart();
+    kill_engine(&app);
+    app.restart();
```
`Cargo.toml`: quitar `tauri-plugin-shell = "2"`. El comando es `async` para no bloquear el hilo principal durante `connect_timeout`+`spawn` (Tauri v2 ejecuta los comandos síncronos en el hilo principal). Requiere `@tauri-apps/api` en el frontend (ya está en `package.json`).

### Diff 14 — `desktop/src-tauri/tauri.conf.json`
```diff
     "security": {
-      "csp": null
+      "csp": {
+        "default-src": "'self'",
+        "script-src": "'self'",
+        "style-src": "'self' 'unsafe-inline' https://fonts.googleapis.com",
+        "font-src": "'self' data: https://fonts.gstatic.com",
+        "img-src": "'self' data: blob:",
+        "connect-src": "'self' ipc: http://ipc.localhost http://127.0.0.1:* ws://127.0.0.1:* http://localhost:* ws://localhost:* http://*:9420 ws://*:9420 https://*:9420 wss://*:9420 https://*.ts.net wss://*.ts.net",
+        "object-src": "'none'",
+        "base-uri": "'self'"
+      },
+      "devCsp": {
+        "default-src": "'self'",
+        "script-src": "'self' 'unsafe-inline' 'unsafe-eval'",
+        "style-src": "'self' 'unsafe-inline' https://fonts.googleapis.com",
+        "font-src": "'self' data: https://fonts.gstatic.com",
+        "img-src": "'self' data: blob:",
+        "connect-src": "'self' ipc: http://ipc.localhost http://127.0.0.1:* ws://127.0.0.1:* http://localhost:* ws://localhost:* http://*:9420 ws://*:9420 https://*:9420 wss://*:9420 https://*.ts.net wss://*.ts.net"
+      }
     }
```
Notas: (1) `http://*:9420` es sintaxis CSP3 válida (host `*` + puerto explícito) y es la única forma de expresar "cualquier IP de la LAN/tailnet en 9420" — CSP no admite `192.168.1.*`; si se prefiere cerrar más, sustituir por las IPs concretas (`http://192.168.1.204:9420 ws://192.168.1.204:9420 http://100.x.y.z:9420 ...`). (2) `ipc: http://ipc.localhost` es lo que Tauri v2 documenta para `connect-src` en Windows (necesario para `invoke`). (3) `'unsafe-inline'` en `style-src` es necesario por framer-motion/recharts/lightweight-charts (estilos inline); Tauri añade nonces/hashes a los `<style>` del `index.html` automáticamente. (4) **No verificado en runtime**: tras aplicar, abrir DevTools en `tauri dev` y buscar "Refused to" en consola. (5) Si se empaquetan las fuentes (P3), quitar los dominios de Google.

### Diff 15 — `desktop/src-tauri/capabilities/default.json`
```diff
   "permissions": [
-    "core:default",
-    "updater:default",
-    "dialog:default",
-    "process:default",
-    "shell:default",
-    {
-      "identifier": "shell:allow-spawn",
-      "allow": [{ "name": "binaries/botstrike-engine", "sidecar": true, "args": true }]
-    },
-    "shell:allow-kill"
+    "core:default"
   ]
```
Los comandos propios (`ensure_local_engine`) no necesitan permiso de capability en Tauri v2 (sólo los de plugins/core). `dialog`/`updater`/`process` se siguen usando desde Rust sin IPC.

### Diff 16 — `desktop/eslint.config.js` (para que lint sea usable)
```diff
-  globalIgnores(['dist']),
+  globalIgnores(['dist', 'src-tauri/**', 'node_modules']),
   {
     files: ['**/*.{ts,tsx}'],
@@
     languageOptions: {
       ecmaVersion: 2020,
       globals: globals.browser,
     },
+    rules: {
+      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', destructuredArrayIgnorePattern: '^_' }],
+    },
   },
```

### Diff 17 — docs
- `deploy/README.md:29`: ya describe el flujo correcto; añadir "Test → Save & Reconnect" y dónde sacar el auth token (`journalctl -u botstrike-bridge | grep auth_token` — o mejor, que el bridge lo lea de `.env`, ver auditoría 03).
- `README.md:43`: "FastAPI on localhost:9420" → "FastAPI on :9420 (local bundled engine or remote LXC — configurable in Settings → Connection)".

### Orden de aplicación sugerido (cada paso deja la app funcionando)
1. `constants.ts` + `config.ts` + `api.ts` + `ws.ts` + `BacktestPage.tsx` (URL configurable por localStorage, sin UI aún; `npm run build` debe pasar).
2. `systemStore.ts` + `useWebSocket.ts` + `TopBar.tsx` + `SystemPage.tsx` (indicador honesto).
3. `SettingsPage.tsx` + `ConnectionOverlay.tsx` (UI).
4. `engine.ts` + `lib.rs` + `Cargo.toml` + `capabilities/default.json` (engine condicional; requiere `cargo build`).
5. `tauri.conf.json` CSP (verificar en `tauri dev` con DevTools).
6. `eslint.config.js` + limpieza de lint.

### Verificación que hay que hacer tras aplicar (ninguna hecha aún)
- `npm run build` y `npm run lint` en verde.
- `tauri dev` con bridge local: overlay → Connect → Connected → dismiss; TopBar `LOCAL`; `botstrike-engine.exe` aparece en Task Manager y **desaparece al cerrar la app**.
- Settings → Connection → `192.168.1.204` → Test → `ok · engine running · paper · N ms` → Save & Reconnect → TopBar `REMOTE`, punto verde, sin proceso engine local. Consola DevTools sin "Refused to connect" (CSP).
- Apagar la Wi-Fi 30 s con bridge remoto: en ≤25 s el punto pasa a rojo; al volver la red, reconecta solo (backoff 3-30 s).
- URL inválida (`http://10.0.0.99:9420`): overlay pasa a "Bridge unreachable" a los 15 s con la URL correcta en pantalla.
- Mode LIVE sin token → alerta roja "Start failed: Invalid or missing auth token…" (ya no silencioso).

---

## Salida real de build/lint (ejecutado 2026-08-29 en `desktop/`, Windows, node v20.19.3)

### `npm run build` → **PASA** (exit 0)
```text
> desktop@2.11.1 build
> tsc -b && vite build
vite v8.0.3 building client environment for production...

transforming...✓ 2736 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                          0.60 kB │ gzip:   0.37 kB
dist/assets/index-Cl6OjAun.css                          32.68 kB │ gzip:   6.38 kB
dist/assets/themeStore-k3_yjrtM.js                       0.06 kB │ gzip:   0.08 kB
dist/assets/themeStore-BOnXq2aA.js                       9.89 kB │ gzip:   3.80 kB
dist/assets/lightweight-charts.production-BHjJCDB4.js  157.80 kB │ gzip:  50.51 kB
dist/assets/index-B0Ko-n9Q.js                          843.13 kB │ gzip: 252.01 kB
[PLUGIN_TIMINGS] Warning: Your build spent significant time in plugins. Here is a breakdown:
  - @tailwindcss/vite:generate:build (70%)
  - rolldown:vite-resolve (20%)
  - vite:css (6%)
See https://rolldown.rs/options/checks#plugintimings for more details.
[plugin builtin:vite-reporter] 
(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rolldownOptions.output.codeSplitting to improve chunking: https://rolldown.rs/reference/OutputOptions.codeSplitting
- Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.
✓ built in 33.29s
BUILD_EXIT=0
```

### `npm run lint` → **FALLA** (exit 1, 64 errores). Los 19 primeros son `src-tauri/target/**/tauri-codegen-assets/*.js` (assets binarios; `globalIgnores` sólo excluye `dist`) — se muestran 2 de ejemplo; el resto íntegro:
```text
> desktop@2.11.1 lint
> eslint .
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src-tauri\target\release\build\app-1a3525bd47f8cabc\out\tauri-codegen-assets\d7dedf5839c670292b1f3a3ea134f756d42e3cc6a3e1acdf823573e465df1d1b.js
  1:2  error  Parsing error: Unexpected character '�'
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src-tauri\target\release\build\botstrike-02a2edb93892d6b0\out\tauri-codegen-assets\ffbb24722af88dc4a4881484e9c087d6c31062ba6b9ea2a714a4d2438f16a64e.js
  1:2  error  Parsing error: Unexpected character ''
… (18 archivos más de src-tauri	arget\**	auri-codegen-assets\*.js con el mismo 'Parsing error: Unexpected character' — omitidos)
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\components\charts\CandlestickChart.tsx
   45:27  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
   46:28  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
   47:34  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  127:19  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  167:30  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  175:30  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  183:29  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  192:29  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  231:57  error  Empty block statement  no-empty
  242:22  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\components\layout\TopBar.tsx
  37:5  error  Error: Calling setState synchronously within an effect can trigger cascading renders
Effects are intended to synchronize state between React and external systems such as manually updating the DOM, state management libraries, or other platform APIs. In general, the body of an effect should do one or both of the following:
* Update external systems with the latest state from React.
* Subscribe for updates from some external system, calling setState in a callback function when external state changes.
Calling setState synchronously within an effect body causes cascading renders that can hurt performance, and is not recommended. (https://react.dev/learn/you-might-not-need-an-effect).
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\components\layout\TopBar.tsx:37:5
  35 |   useEffect(() => {
  36 |     if (price === 0 || price === lastPrice.current) return;
> 37 |     setFlash(price > lastPrice.current ? "up" : "down");
     |     ^^^^^^^^ Avoid calling setState() directly within an effect
  38 |     lastPrice.current = price;
  39 |     const t = setTimeout(() => setFlash(null), 400);
  40 |     return () => clearTimeout(t);  react-hooks/set-state-in-effect
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\components\shared\AnimatedNumber.tsx
  31:7  error  Error: Calling setState synchronously within an effect can trigger cascading renders
Effects are intended to synchronize state between React and external systems such as manually updating the DOM, state management libraries, or other platform APIs. In general, the body of an effect should do one or both of the following:
* Update external systems with the latest state from React.
* Subscribe for updates from some external system, calling setState in a callback function when external state changes.
Calling setState synchronously within an effect body causes cascading renders that can hurt performance, and is not recommended. (https://react.dev/learn/you-might-not-need-an-effect).
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\components\shared\AnimatedNumber.tsx:31:7
  29 |
  30 |     if (Math.abs(diff) < 1e-10) {
> 31 |  setDisplay(to);
     |  ^^^^^^^^^^ Avoid calling setState() directly within an effect
  32 |  return;
  33 |     }
  34 |  react-hooks/set-state-in-effect
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\hooks\useWebSocket.ts
  61:25  error  '_' is assigned a value but never used   @typescript-eslint/no-unused-vars
  61:39  error  '__' is assigned a value but never used  @typescript-eslint/no-unused-vars
  91:25  error  '_' is assigned a value but never used   @typescript-eslint/no-unused-vars
  91:39  error  '__' is assigned a value but never used  @typescript-eslint/no-unused-vars
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\lib\api.ts
  20:25  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  21:25  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  22:28  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  24:13  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  25:26  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  26:30  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  27:29  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  28:36  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  29:30  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\lib\ws.ts
  3:30  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\pages\backtest\BacktestPage.tsx
  235:38  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\pages\dashboard\DashboardPage.tsx
  16:15  error  'TrendingUp' is defined but never used  @typescript-eslint/no-unused-vars
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\pages\performance\PerformancePage.tsx
   11:3   error  'BarChart' is defined but never used  @typescript-eslint/no-unused-vars
   11:13  error  'Bar' is defined but never used  @typescript-eslint/no-unused-vars
   11:18  error  'Cell' is defined but never used  @typescript-eslint/no-unused-vars
   72:35  error  Compilation Skipped: Existing memoization could not be preserved
React Compiler has skipped optimizing this component because the existing manual memoization could not be preserved. The inferred dependencies did not match the manually specified dependencies, which could cause the value to change more or less frequently than expected. The inferred dependency was `perfData.equity_curve`, but the source dependencies were [perfData?.equity_curve]. Inferred different dependency than source.
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\pages\performance\PerformancePage.tsx:72:35
  70 |   }, []);
  71 |
> 72 |   const equityCurveData = useMemo(() => {
     |  ^^^^^^^
> 73 |     if (!perfData?.equity_curve?.length) return [];
     | ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
> 74 |     return perfData.equity_curve.map((v, i) => ({ idx: i, equity: typeof v === "number" ? v : 1000 }));
     | ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
> 75 |   }, [perfData?.equity_curve]);
     | ^^^^ Could not preserve existing manual memoization
  76 |
  77 |   const p = perfData || metrics;
  78 |  react-hooks/preserve-manual-memoization
  141:32  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\pages\system\SystemPage.tsx
   9:24  error  'Wifi' is defined but never used  @typescript-eslint/no-unused-vars
   9:30  error  'WifiOff' is defined but never used  @typescript-eslint/no-unused-vars
  17:46  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\pages\trading\TradingPage.tsx
   5:10  error  'AnimatedNumber' is defined but never used  @typescript-eslint/no-unused-vars
  13:10  error  'SYMBOL_LABELS' is defined but never used   @typescript-eslint/no-unused-vars
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\stores\marketStore.ts
   56:22  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  133:20  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\stores\systemStore.ts
  19:20  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  20:17  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
  21:25  error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any
C:\Users\edgar\Desktop\proyectos\BotStrike\desktop\src\stores\tradingStore.ts
   91:11  error  Empty block statement  no-empty
  111:85  error  Empty block statement  no-empty
✖ 64 problems (64 errors, 0 warnings)
LINT_EXIT=1
```

Desglose de los 45 errores en `src/` (contados sobre la salida anterior): `no-explicit-any` ×27 · `no-unused-vars` ×12 · `no-empty` ×3 · `react-hooks/set-state-in-effect` ×2 · `react-hooks/preserve-manual-memoization` ×1 = 45; 45 + 19 = 64. Logs completos en el scratchpad de la sesión (`build.log`, `lint.log`).
