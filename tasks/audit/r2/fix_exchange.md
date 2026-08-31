# Auditoría R2 — AREA: fix_exchange

**Alcance**: revisión adversarial de los fixes de la ronda 1 (`b3dbf75`) en
`exchange/binance_client.py` y `exchange/binance_ws.py`, contrastados contra la
documentación oficial de Binance USDⓈ-M Futures y contra los valores reales de
`GET /fapi/v1/exchangeInfo` descargados hoy (2026-08-31, `serverTime=1788156524390`).

**Contexto operativo**: Binance está CERRADO para el dueño (residente ES) desde
2026-07-01 en modo solo-reducir. Hoy el cliente se usa SOLO para datos públicos
en paper. Prioridad: (a) ruta de datos públicos correcta, (b) ruta de ORDENES que
no se pueda disparar por accidente, (c) el resto como deuda documentada.

**Estado**: EN PROGRESO (informe incremental).

---

## Lo que la ronda 1 hizo BIEN (verificado, no inventar hallazgos aquí)

- **Redondeo**: `floor_to_step` usa `Decimal` + `ROUND_DOWN` sobre `Decimal(str(x))`,
  nunca `float`. Es correcto: `qty` siempre hacia abajo (nunca sobredimensiona).
- **`format_decimal`**: `format(d.normalize(), "f")` evita notación científica.
  Verificado con `0.001`, `1E+2`, `Decimal('0E-8')` → `"0.001"`, `"100"`, `"0"`.
- **`newClientOrderId`**: cumple el regex oficial `^[\.A-Z\:/a-z0-9_-]{1,36}$` y
  la longitud máxima. Verificado por snippet (27/30/33 chars, `regex_ok=True` en
  los tres prefijos `bs`, `bs_mm`, `bs_close`).
- **`-2013` en `get_order`**: es el código correcto. La doc oficial de códigos de
  error confirma "-2013 NO_SUCH_ORDER — Order does not exist … querying/retrieving";
  `-2011 CANCEL_REJECTED` es el de DELETE. El fix eligió bien.
- **Mapeo de tipos**: `STOP→STOP_MARKET`, `STOP_LIMIT→STOP`,
  `TAKE_PROFIT→TAKE_PROFIT_MARKET`, `TAKE_PROFIT_LIMIT→TAKE_PROFIT` es correcto
  contra `orderTypes` de exchangeInfo.
- **`newOrderRespType=RESULT`** en MARKET: soportado y correcto (doc: "When set to
  RESULT, MARKET orders return final FILLED status").
- **Depth `b`/`a`**: `@depth20@100ms` es *partial book depth*, un **snapshot
  completo** cada 100 ms con claves cortas `b`/`a`; NO necesita el ciclo
  snapshot+diff con `U/u/pu` (eso es sólo para `@depth@100ms` diferencial). El fix
  es correcto y además NO hace falta gestionar secuencia. Bien resuelto.
- **`tickSize`/`stepSize`/`minQty`** de los 4 símbolos coinciden EXACTAMENTE con
  los valores vivos de hoy (sólo falla `minNotional` de BTC, ver `fix_exchange-06`).

---

## Hallazgos

### [P1] fix_exchange-01 — `load_exchange_info()` cachea SOLO los 4 símbolos mapeados: `close_all_positions()` no puede cerrar nada fuera de `SYMBOL_MAP`

**Archivo**: `exchange/binance_client.py:409-413` (+ `70-73`, `425-432`)

**Evidencia**:
```python
info = await self.get_exchange_info()
parsed = parse_symbol_filters(info)
wanted = set(SYMBOL_MAP.values())          # {'BTCUSDT','ETHUSDT','ADAUSDT','SOLUSDT'}
loaded = {k: v for k, v in parsed.items() if k in wanted}
...
GENERIC_SYMBOL_FILTER = {"tickSize": Decimal("0.01"), "stepSize": Decimal("0.001"),
                         "minQty": Decimal("0.001"), "minNotional": Decimal("5")}
```

**Por qué**: `close_all_positions()` itera sobre TODO `/fapi/v2/positionRisk`, no
sólo los 4 símbolos configurados. Para cualquier otro símbolo (posición abierta a
mano, restos de una versión anterior, un símbolo que se añada a `settings.symbols`
sin tocar `SYMBOL_MAP`) los filtros caen al `GENERIC_SYMBOL_FILTER`, cuyo
`stepSize=0.001` es falso para la mayoría de perps. Verificado con XRPUSDT (real
`stepSize=0.1`, `minQty=0.1`): el cliente emite `quantity=123.456`, que Binance
rechaza con `-1111 BAD_PRECISION`. El fix P0-03 ("nunca dejar posiciones
desnudas") falla exactamente en el caso para el que se escribió, y además el
cierre se hace ANTES de `cancel_all()`, así que después se borran las SL/TP y la
posición queda desnuda de verdad.

**Fix**: no filtrar por `wanted` — cachear todos los símbolos de exchangeInfo
(1091 KB de JSON, ~500 símbolos, se parsea una vez), o al menos hacer
`load_exchange_info(force=True)` con el símbolo concreto antes de cerrar y
**abortar el cierre con log CRITICAL si no hay filtros reales** en lugar de
mandar una cantidad que se sabe inválida.

**Verificado como**:
```
$ py -3.12 scratchpad/v_ex3.py
  cached symbols: ['ADAUSDT', 'BTCUSDT', 'ETHUSDT', 'SOLUSDT']
  get_symbol_filters('XRPUSDT') -> {'tickSize': Decimal('0.01'), 'stepSize': Decimal('0.001'), ...}
  XRPUSDT REAL stepSize=0.1 minQty=0.1  -> client believes step=0.001 minQty=0.001
  emergency close of 123.456 XRP -> {'quantity': '123.456'}   (-1111)
```

---
