# Auditoría R2 — AREA: fix_exchange

**Alcance**: revisión adversarial de los fixes de la ronda 1 (`b3dbf75`) en
`exchange/binance_client.py` y `exchange/binance_ws.py`, contrastados contra la
documentación oficial de Binance USDT-M Futures y contra los valores reales de
`GET /fapi/v1/exchangeInfo` de hoy.

**Contexto operativo**: Binance está CERRADO para el dueño (residente ES) desde
2026-07-01 en modo solo-reducir. Hoy el cliente se usa SOLO para datos públicos
en paper. Prioridad: (a) ruta de datos públicos correcta, (b) ruta de órdenes
que no se pueda disparar por accidente, (c) el resto como deuda documentada.

**Estado**: EN PROGRESO (informe incremental).

---

## Hallazgos

<!-- se añaden incrementalmente -->
