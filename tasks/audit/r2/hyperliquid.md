# Auditoría R2 — Área: Hyperliquid (integración en profundidad)

Fecha: 2026-08-31 · Auditor: agente R2 `hyperliquid`
Alcance: `exchange/hyperliquid_client.py`, `exchange/hyperliquid_ws.py`, uso desde `main.py` y `server/bridge.py`, contraste con el SDK **realmente instalado** y con `tasks/research_r2_hyperliquid_execution.md` + doc oficial.

> **Estado: EN PROGRESO** — hallazgos añadidos incrementalmente conforme se confirman.

## Entorno verificado (medido, no supuesto)

```
py -3.12 -c "import importlib.metadata as md; print(md.version('hyperliquid-python-sdk'), md.version('eth-account'))"
0.22.0  0.13.7
```
- SDK instalado en `C:\Users\edgar\AppData\Local\Programs\Python\Python312\Lib\site-packages\hyperliquid`
- `requirements.txt:14` → `hyperliquid-python-sdk>=0.22.0` (flotante) · `requirements.lock:33` → `==0.24.0`
- `git log --oneline -- exchange/hyperliquid_client.py exchange/hyperliquid_ws.py` → **un solo commit**: `2e9b9ce feat: v2.10.0`.
  ⇒ **Ni un solo fix de la ronda 1 (02-P1-13) se ha aplicado a Hyperliquid.** Todo lo que se listó en la auditoría 2026-08-29 sigue literalmente igual.

---

## Hallazgos

_(en construcción)_
