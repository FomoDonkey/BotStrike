# Auditoría R2 — fix_exchange: revisión adversarial de los fixes de ronda 1 en `exchange/`

**Fecha:** 2026-08-30 · **Commit auditado:** `b3dbf75` (v2.12.1) · **Alcance:** `exchange/binance_client.py`, `exchange/binance_ws.py`
**Método:** lectura completa del código post-fix, `git show b3dbf75 -- exchange/`, contraste con la doc oficial de Binance USDT-M Futures
(WebFetch/curl), `GET /fapi/v1/exchangeInfo` real, ejecución de `_normalize_order_params` con sesión falsa (`py -3.12`), suite de tests.
Registro incremental: cada hallazgo se añade al confirmarlo.

## Hallazgos

