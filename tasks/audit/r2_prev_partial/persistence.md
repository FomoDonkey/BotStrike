# Auditoría R2 — persistence: Persistencia, contabilidad y notificaciones

**Fecha:** 2026-08-30 · **Ámbito:** `trade_database/`, `analytics/performance.py`, `data_lifecycle/`, `logging_metrics/logger.py`, `notifications/telegram.py`, `server/serializers.py` + puntos de integración en `main.py` / `server/bridge.py` / `execution/paper_simulator.py`.
**Método:** lectura del código real, snippets ejecutados con `py -3.12`, inspección de `data/trade_database.db` y `logs/metrics.jsonl` reales, contraste con documentación oficial (Binance / Telegram) cuando se afirma un comportamiento externo.
**Ronda 1 (contexto):** hallazgos previos en este ámbito: 03-P1-7 (estado paper en memoria), 03-P2-14 (logs), 03-P2-17 (SQLite backup/checkpoint), 03-P3-19 (shutdown duplicado), 03-P3-27 (Telegram), 04-P2 (Sharpe 252 vs 365), 02-P2-17 (paper sin funding). No se repiten salvo que sigan abiertos o el fix sea incompleto.

Baseline: `py -3.12 -m pytest tests/ -q -p no:cacheprovider` → (ver sección Verificación).

## Hallazgos

(se añaden incrementalmente a medida que se confirman)

