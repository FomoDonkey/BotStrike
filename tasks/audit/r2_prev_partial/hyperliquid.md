# Auditoría R2 — Integración Hyperliquid en profundidad

**Fecha:** 2026-08-30 · **Área:** `hyperliquid` · **Estado:** EN CURSO (archivo incremental)
**Alcance:** `exchange/hyperliquid_client.py`, `exchange/hyperliquid_ws.py`, `tasks/hyperliquid_api_research.md`, uso desde `main.py` / `server/bridge.py` / `execution/order_engine.py`.
**Método:** lectura completa del código; SDK oficial instalado localmente (`hyperliquid-python-sdk 0.22.0` en `py -3.12`, `requirements.lock` pide 0.24.0) → reproducción de comportamientos con snippets `py -3.12`; contraste con doc oficial (gitbook) vía WebFetch; tests `py -3.12 -m pytest tests/ -q -p no:cacheprovider`.
**Referencia ronda 1:** `tasks/audit/02_exchange_execution.md` P1-13 (HL incompleto) — aquí se verifica ítem por ítem y se amplía.

---

## Hallazgos

