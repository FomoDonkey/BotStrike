# Verificación independiente de los hallazgos R2 (área `security_supply`)

**Fecha:** 2026-08-31 · **Verificador:** Claude (sesión principal, NO el subagente que los encontró)
**Por qué existe este archivo:** el workflow de la ronda 2 cayó dos veces (límite de sesión y créditos).
El área `security_supply` sí completó, pero **las 3 lentes de verificación adversarial fallaron**, así que
sus hallazgos llegaron SIN verificar. Regla nº1 (CLAUDE.md): nada se da por bueno de palabra.
Aquí verifico cada uno con ejecución real y salida a la vista, y corrijo la severidad donde toca.

---

## Resumen de veredictos

| ID | Severidad reportada | **Mi veredicto** | Cambio |
|----|--------------------|------------------|--------|
| security_supply-01 | P1 | **CONFIRMADO** (mecanismo reproducido) — pero **0 fugas reales hasta hoy** en el CT | matiz a la baja |
| security_supply-02 | P1 | **PARCIALMENTE REFUTADO** → **P2**: el set desplegado pasa 100/100; solo falta `httpx2` en el CT | ⬇ baja |
| security_supply-03 | P2 | **CONFIRMADO** (no hay `ALLOW_LIVE` en la unit) | = |
| security_supply-05 | P2 | **CONFIRMADO Y AGRAVADO** → **P1**: bypass TOTAL de auth, no solo "silencioso" | ⬆ sube |
| security_supply-07 | P3 | **CONFIRMADO** (secreto muerto presente en el `.env` del CT) | = |
| security_supply-02b (streamlit) | parte de 02 | **CONFIRMADO** | = |

---

## security_supply-01 — token en query string → access log

**CONFIRMADO.** Reproducido por mí (bridge local en `0.0.0.0:9493`, token falso):
```
INFO:  127.0.0.1:53273 - "GET /api/bot/status?token=FAKE_SECRET_TOKEN_ABC123 HTTP/1.1" 200 OK
INFO:  127.0.0.1:53274 - "POST /api/bot/stop?token=FAKE_SECRET_TOKEN_ABC123 HTTP/1.1" 401 Unauthorized
```
En el CT ese stderr va a journald. El 401 confirma además que la comprobación de token funciona.

**MATIZ IMPORTANTE (no estaba en el informe): no ha habido fuga real todavía.**
```
journalctl -u botstrike-bridge --since "-7 days" | grep -c "token="   →  0
```
Motivo: la UI solo manda `?token=` en start/stop/backtest, y Edgar aún no ha usado esos botones desde
la web. La vulnerabilidad es real y hay que cerrarla **antes** de que pulse Start; no hay incidente que
gestionar. Sigue siendo P1 (P0 el día que existan claves Binance con permiso de trading en el host).

## security_supply-02 — `requirements.lock` con majors no probados

**CONFIRMADO Y AGRAVADO.** El informe decía "versiones nunca validadas por la suite". `tasks/todo.md`
afirmaba lo contrario ("92/92 dentro del CT, pandas 3.0.5"), así que **ejecuté la suite en el CT**:
```
versions: pandas 3.0.5  starlette 1.6.0  fastapi 0.141.1  numpy 2.5.2  pytest 9.1.1
ERROR tests/test_bridge_round2.py - RuntimeError: The starlette.testclient module requires ...
E       $ pip install httpx2
!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!
1 error in 0.40s
```
**El entorno desplegado no puede ni recolectar los tests** (starlette 1.6 movió TestClient a `httpx2`,
que el lock no incluye). O sea: 0 de 100 tests cubren hoy lo que corre en producción; el "92/92 en el CT"
del todo fue cierto con un lock anterior y hoy es falso.

### ⚠️ Corrección de mi propio veredicto (esto es lo que pasa cuando se verifica de verdad)

Al leer eso escalé el hallazgo a "P0 de mantenibilidad" asumiendo que los majors no probados
(pandas 3.0, starlette 1.6) **podían romper el código**. **Me equivoqué, y lo demuestro:** repliqué el set
desplegado EXACTO en un venv desechable (`uv pip install -r requirements.lock`) y corrí la suite completa:
```
replica: pandas 3.0.5  starlette 1.6.0  fastapi 0.141.1  numpy 2.5.2
100 passed in 8.95s
```
**El set desplegado pasa 100/100.** Lo único que faltaba era `httpx2`, dependencia SOLO de test que
starlette 1.6 exige para su TestClient. Ni pandas 3.0 ni starlette 1.6 rompen nada del bot.

Veredicto corregido → **P2**, y se descompone en dos cosas pequeñas y reales:
1. **Falta `httpx2` en el CT** → allí la suite no arranca → no hay puerta de calidad en el despliegue.
2. **Paquetes solo-dashboard en el servidor** (confirmado): `altair  git(gitpython)  plotly  pydeck  streamlit`
   → superficie de ataque a cambio de cero función.

La alarma "el CT corre versiones que podrían reventar" queda **refutada con datos**. Buena noticia:
el CT está sobre un set que ahora sí está validado.

## security_supply-03 — sin kill-switch de host para live

**CONFIRMADO.** `grep -c ALLOW_LIVE /etc/systemd/system/botstrike-bridge.service` → **0**.
La única barrera para `POST /api/bot/start?mode=live` es el token. Dado el bloqueo regulatorio
(Binance no opera para residentes en España desde jul-2026) y la nota "do NOT go live", el kill-switch
de despliegue es defensa en profundidad barata y alineada con la política ya decidida.

## security_supply-05 — `--dev` desactiva la auth

**CONFIRMADO POR EJECUCIÓN Y AGRAVADO a P1.** El informe lo dedujo leyendo; yo lo ejecuté
(`--host 0.0.0.0 --port 9494 --dev`):
```
/api/bot/status   → auth_token_exposed: True | auth_token: LEAKED
/docs             → 200   (debería ser 404 en bind no-loopback)
POST /api/bot/stop SIN token → 200   (debería ser 401)
```
No es "un control que se relaja": es **bypass completo** — el token se regala por HTTP y las mutaciones
se aceptan sin credencial. Causa: `_EXPOSE_TOKEN` es un default de módulo (`True`) que solo se corrige
dentro de `main()`; con `reload=True` el worker importa `server.bridge:app` sin pasar por `main()`.
Solo afecta a `--dev` (producción usa el arranque normal), pero el fix es de 3 líneas.

## security_supply-07 — secreto muerto en `.env`

**CONFIRMADO.** `grep -c "^GEMINI_API_KEY=." .env` → **1** en el CT, y los únicos consumidores viven en
`archive/` y en el dashboard Streamlit archivado. Secreto sin uso = superficie gratis.

---

## Plan de corrección (por orden de valor)

1. **SEC-05 (P1, bypass total):** derivar `_EXPOSE_TOKEN` de entorno a nivel de módulo (o prohibir
   `--dev` fuera de loopback). Es el único agujero que hoy regala el token y acepta mutaciones sin auth.
2. **SEC-01 (P1):** token SOLO por cabecera `X-BotStrike-Token` desde la UI + redacción de `token=` en el
   access log del bridge (mantener el access log, que es útil) + aceptar cabecera en el check de live.
3. **SEC-03 (P2):** `BOTSTRIKE_ALLOW_LIVE` (default 0) + fijarlo a 0 en la unit del CT.
4. **SEC-02 (P2):** `httpx2` en `requirements-dev.txt` (para que la suite corra en el CT) + puerta de
   calidad en `update.sh` (no reiniciar si los tests fallan) + `requirements-server.txt` sin
   streamlit/plotly/altair/pydeck/gitpython.
5. **SEC-07 (P3):** quitar `GEMINI_API_KEY` del `.env` del servidor.
6. Pendientes de R1 que este área reconfirma abiertos: 03-P2-11, 03-P2-12, 03-P2-16.

## security_supply-04 — GET sin auth en bind no-loopback

**CONFIRMADO** — y ya estaba probado sin querer durante esta misma sesión: desde mi PC (un dispositivo
cualquiera de la LAN, **sin token**) contra el CT:
```
curl http://192.168.1.204:9420/api/performance  → equity, PnL, WR, Sharpe, drawdown, curva completa
curl http://192.168.1.204:9420/api/strategies   → estrategias, allocations, estado
curl http://192.168.1.204:9420/api/trades       → historial completo de operaciones
```
Todo servido sin credencial a cualquiera en `192.168.1.0/24`. Confirmado.

**PERO su "fix" NO es un parche ciego, es una decisión de producto:** exigir token en los GET rompe el
flujo "abro el navegador y veo el bot" que Edgar usa a diario (y que acabamos de construir). Propuesta:
`BOTSTRIKE_REQUIRE_AUTH_READS` (default 0 = comportamiento actual), de modo que quien quiera cerrarlo
lo cierre sin romper a quien no. Decisión de Edgar, no mía.

**Pendiente de verificar:** SEC-06 (scope de la API key de Binance) — el grep del agente es plausible
pero no lo he ejecutado yo.
