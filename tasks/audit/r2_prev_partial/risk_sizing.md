# Auditoría R2 — risk_sizing (Riesgo y sizing numérico)

**Fecha:** 2026-08-30 · **Alcance:** `risk/risk_manager.py`, `portfolio/portfolio_manager.py`, `core/quant_models.py`, `strategies/base.py` (+ call sites en `strategies/*`, `main.py`, `backtesting/backtester.py`).
**Método:** lectura línea a línea + reproducción numérica con `py -3.12` instanciando `RiskManager`/`PortfolioManager`/estrategias con `Settings()` real y señales sintéticas. Archivo escrito de forma incremental. Referencias a la ronda 1: `01-Fxx` = `tasks/audit/01_core_strategy_risk.md`.

---

## Hallazgos

### [P1] risk_sizing-01 — ADA está BLOQUEADO al 100% en el risk manager: umbral absoluto `risk_per_unit < 0.001` en unidades de precio equivale a 50 bps a $0.20; el SL de MR (2×ATR ≈ 39 bps) nunca lo supera
**Archivo:** `risk/risk_manager.py:350-359` (`_adjust_position_size`), `config/settings.py:205-210` (ADA `mr_atr_mult_sl=2.0`)
**Evidencia:**
```python
risk_per_unit = abs(signal.entry_price - signal.stop_loss)
if risk_per_unit < 0.001:
    # entry_price ≈ stop_loss → undefined risk per unit → reject
    logger.warning("risk_bypass_rejected", ..., reason="risk_per_unit_zero_or_negligible")
    return 0.0
```
Ejecutado (`py -3.12`, clases reales, ADAUSDT perp = $0.2007, ATR14 5m mediana = 0.00039 → SL 2.0×ATR = **0.00078 < 0.001**):
```
ADA-USD MEA RANGING     alloc 162.5  SL 38.9 bps  base.py $325  final $0.0  REJ
ADA-USD MEA TRENDING_UP alloc  75.0  SL 38.9 bps  base.py $150  final $0.0  REJ
ADA-USD MEA UNKNOWN     alloc 125.0  SL 38.9 bps  base.py $250  final $0.0  REJ
umbral 0.001 abs = 49.8 bps en ADA ; 0.095 bps en SOL ; 0.004 bps en ETH ; 0.0001 bps en BTC
```
Hace falta `sl_mult ≥ 3.0` (58 bps) para pasar; con 2.5 (48.6 bps) sigue rechazado.
**Por qué:** El guard "entry ≈ stop" está escrito en USD absolutos y se aplica a un activo que cotiza a $0.20. ADA solo tiene MR permitido (`SYMBOL_STRATEGY_MAP`), así que **el símbolo entero es inoperable** mientras ADA < ~$0.26 (o ATR < 25 bps). La señal se genera (`mr_entry`, `_states[symbol]` fijado) y se descarta en silencio en `validate_signal` → se repite en cada barra 5m: log spam de `risk_bypass_rejected` y cero trades. Ningún test cubre precios < $1. Es una mezcla de unidades (precio absoluto vs distancia relativa), justo lo que pedía el foco de esta auditoría.
**Fix:** `if risk_per_unit / signal.entry_price < 1e-5` (0.1 bps) o directamente `if risk_per_unit <= 0`. Añadir test con `price=0.2, sl=0.1992`.
**Verificado cómo:** ejecutado con `RiskManager(Settings())` + ATR real de Binance Futures (2026-08-30); confirmación en el soak paper del CT 104 (ver abajo si hay registros).

### [P1] risk_sizing-02 — Risk of Ruin es una función escalón `sign(edge)` que PAUSA TODAS las estrategias de forma permanente (deadlock): con `edge ≤ 0` → RoR=1.0 → `ror_pause_active` → sin trades nuevos nunca se recalcula. Falsa pausa en 13-35 % de los sistemas con edge positivo real
**Archivo:** `core/quant_models.py:344-374` (`RiskOfRuin.compute`), `risk/risk_manager.py:198-207` (`validate_signal`), `risk/risk_manager.py:451-456` (único caller de `compute`)
**Evidencia:**
```python
# quant_models.compute
edge = win_rate * (avg_win / avg_loss) - (1.0 - win_rate)
if edge <= 0: ror = 1.0                      # escalón
else: capital_units = current_equity*max_dd/avg_loss ; ror = ((1-edge)/(1+edge)) ** capital_units
# risk_manager.validate_signal
if ror.should_pause and ror.sample_size >= self.risk_of_ruin.min_trades: return None   # TODAS las entradas
# risk_manager.record_trade_result  (unico sitio que llama compute)
self.risk_of_ruin.compute(self._current_equity)
```
Ejecutado (200 trades, pérdida media $0.60, equity 1000, max_dd 10 %):
```
WR=0.39 payoff=1.5 : edge=-0.025 ror=1.0000 pause=True
WR=0.39 payoff=1.6 : edge=+0.014 ror=0.0094 pause=False   <- 0.1 de payoff separa "pausa permanente" de "sin throttle"
WR=0.45 payoff=1.2 : edge=-0.010 ror=1.0000 pause=True
WR=0.45 payoff=1.25: edge=+0.013 ror=0.0155 pause=False
WR=0.50 payoff=1.0 : edge=+0.000 ror=1.0000 pause=True
```
Simulación (4.000 muestras de 30 trades, clase real): P(pausa en el trade 30) con edge REAL positivo: WR45/payoff1.5 (+0.125) → **35.4 %**; WR50/1.3 → 29.2 %; WR40/2.0 → 29.7 %; WR55/1.2 → 12.7 %.
**Por qué:** (1) Con `capital_units = 1000×0.10/0.60 ≈ 167`, cualquier edge > 0 da RoR ≈ 0 y cualquier edge ≤ 0 da 1.0: los umbrales `ror_throttle 3 %` / `ror_pause 10 %` son inalcanzables en la región intermedia; el "modelo" es un interruptor sobre el signo de la expectativa muestral de 30 trades. (2) Una vez pausado, `validate_signal` devuelve `None` para todas las entradas (RoR es GLOBAL, no por estrategia: MR-ETH perdedora pausa FIB-BTC), no hay fills, `record_trade_result` no se llama, `compute` no se recalcula → **pausa permanente hasta reinicio**. Es exactamente el deadlock de 01-F03 que la ronda 1 arregló para el performance factor, pero no aquí. (3) Con la evidencia del backtest (MR PF 0.40-0.61, edge bruto negativo), en paper/live esto dispara con seguridad antes de 30 trades cerrados (~2-4 días) y desde entonces el bot solo gestiona salidas. La ronda 1 dejó el CT en "soak paper" — muy probablemente está pausado por esto sin que nadie lo sepa (sin alerta Telegram: `ror_pause_active` es `logger.warning`).
**Fix:** (a) sustituir el escalón por la fórmula continua de RoR para pagos desiguales o por `compute_empirical` (bootstrap, ya escrito y sin usar) con IC; (b) cooldown/probation como en F03 (`ror_blocked_since` + reevaluación con ventana limpia o `min_trades` escalonado); (c) RoR por estrategia (`self.risk_of_ruin[st]`) o al menos no pausar estrategias con `n < min_trades` propias; (d) `notify_risk_event("ror_pause")`. Test: 30 pérdidas → pausa; luego 3600 s → permite entrada en probation.
**Verificado cómo:** ejecutado (clase real + simulación Monte Carlo) + grep de callers de `compute` (solo `record_trade_result`).

