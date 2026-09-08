# Auditoría de métricas, datos y estadísticas de la UI — 2026-09-08 (ronda 18)

Edgar: "el account value estuvo en +25 y en el gráfico de Portfolio el máximo es el actual"; "el panel lateral
tiene métricas que no se actualizan solas". Ambas ciertas. Esta tabla es la definición de CADA métrica que
muestra la UI, de dónde sale, cada cuánto se refresca y qué se corrigió.

## Causa raíz de lo que se veía mal
El bot no guardaba ningún histórico **mark-to-market**. Todo lo histórico (gráfico de Account Value, drawdown 30D,
días ganados/perdidos, racha, sparkline y Max DD de la estrategia) se calculaba desde la **cadena de caja** (capital
+ efecto de cada fill), que en un libro de tendencia con 6 posiciones abiertas está plana (~1.008) mientras la
cuenta marcada fue 1.000 → 1.038,87 → 1.010. Solo el punto de "hoy" llevaba el PnL abierto, por eso el máximo del
gráfico era siempre "ahora".

**Arreglo:** `analytics/equity_history.py` — una muestra por minuto de la equity marcada (equity, realizado,
abierto) persistida en `data/equity_history.json` (120 días), y para los días anteriores al primer muestreo una
reconstrucción "estimada" desde los fills valorados al cierre diario de la fuente (Binance spot / Yahoo). Todo lo
histórico lee ahora esa serie; lo estimado se dibuja discontinuo y se dice.

## Convenciones (una definición por concepto)
- **Trade / round trip**: posición abierta y aplanada. Un **trim** (recorte de rebalanceo) realiza dinero pero no es
  un trade: cuenta en balance, volumen, comisiones y días; no en win rate, PF, t-stat ni "trades".
- **Realizado (caja)**: Σ `cash_effect` de las filas (ENTRY −fee; EXIT pnl neto ida y vuelta + fee de entrada ya
  cobrada; FUNDING). Equity = capital + realizado + PnL abierto a marca del venue.
- **Marcado (MTM)**: incluye el PnL abierto. Toda cifra de "cuenta", drawdown, pico, días, racha y Sharpe es MTM.
- **Precio de entrada**: siempre el **fill** (con slippage). La fila ENTRY de la DB guarda la referencia de la señal
  en otra columna; `/api/trades` la sirve como `expected_price`.

## Tabla de métricas

| Página · panel | Métrica | Fuente · cadencia | Definición | Estado |
|---|---|---|---|---|
| Barra superior | Equity | `risk_update.account.equity` WS 5 s (antes frame `metrics` 2 s) | capital + realizado + abierto | ✅ una sola fuente con Account y Portfolio (ronda 17) |
| Barra superior | Regime chip | WS `risk_update` | régimen intradía 15 m de BTC, informativo | ✅ etiquetado |
| Trade · Account | Account Value / Available / Position Value / Margin / Unrealized | `risk_update.account` 5 s | como la cuenta | ✅ |
| Trade · Account | Daily / Weekly PnL | `account.daily_pnl` (MTM desde 00:00Z / lunes) | MTM, baselines persistidos | ✅ |
| Trade · Account | Realized PnL | `account.realized_pnl` | cadena de caja | ✅ hint corregido (ronda 16) |
| Trade · Account | Peak / Drawdown | `account.peak_equity` MTM persistido | 1 − equity/pico | ✅ |
| Trade · Positions | Entry / Mark / PnL / ROE / Funding / Fees / Exits | `/api/positions` 5 s + WS | media de fills, marca venue, MTM | ✅ reconciliado fill a fill |
| Trade · Order History | Fill price | `/api/trades` 15 s | **fill** (antes referencia) | ✅ ronda 17 |
| Trade · Trade History | PnL / bps / Fee / Hold | filas EXIT | neto ida y vuelta; fee = ambas patas | ✅; cuenta N = round trips |
| Journal | Net / Open / Round trips / Win rate / PF / Fees | episodios reconstruidos desde fills | net = realizado + abierto + funding − fee entrada ya cobrada = cuenta | ✅; fee de entrada no duplicada (ronda 16) |
| Portfolio · cabecera | Account value / all-time / Paper balance / Unrealized | `account` 5 s | MTM | ✅ (ronda 17) |
| Portfolio · cabecera | **Realised equity** | nuevo | capital + realizado (caja) | ✅ nuevo, para ver ambas |
| Portfolio · KPI Performance | dots 18 días | `win_days` (MTM día a día) | mint = la cuenta marcada subió ese día | ✅ **antes: caja realizada** |
| Portfolio · KPI Leverage / Margin / Bias | `account` 5 s / `/api/portfolio` 10 s | notional/equity, margen/equity | ✅ |
| Portfolio · gráfico Account Value | serie | `/api/performance.equity_curve_ts` 30 s = **historia MTM** (1/min + estimados) + línea del pico | ✅ **antes: caja diaria, máximo = hoy** |
| Portfolio · gráfico PNL | barras | `daily[].pnl_mtm` (realizado en tooltip) | movimiento marcado del día | ✅ **antes: caja** |
| Portfolio · gráfico Volume / Calendar | `daily[].volume`, `pnl_mtm` | Σ notional de fills; calendario coloreado por MTM | ✅ |
| Portfolio · lateral | All Time Volume / Fees paid / Funding paid / 30D volume / Taker / Maker | `/api/portfolio` 10 s, funding 30 s | Σ fills; fees cobradas; Σ funding | ✅ reconciliado |
| Portfolio · lateral | Longest win streak | MTM días consecutivos al alza | ✅ **antes: días de caja** |
| Portfolio · lateral | Trading style / Avg / Median duration | round trips cerrados | ✅ |
| Portfolio · lateral | Performance 30D · Max drawdown | peor pico-valle **MTM** en 30 d (+ live) | ✅ **antes: caja con suelo live (podía bajar)** |
| Portfolio · lateral | Performance 30D · Sharpe | retornos diarios MTM, necesita 30 días | n/a hasta tener 30 días (dice cuántos hay) |
| Portfolio · lateral | Performance 30D · Win rate / Round trips | round trips en 30 d | ✅ |
| Portfolio · lateral | frescura | `Freshness` (updated Xs ago / refresh failed) | nuevo | ✅ nuevo |
| Strategies · tarjeta | All-time PNL / Trades / Win rate / PF / Age / Open | `by_strategy` 30 s | cadena de caja + abierto; stats sobre round trips | ✅ |
| Strategies · tarjeta | 30D realised | Σ pnl round trips 30 d / capital | ✅ etiquetado (ronda 16) |
| Strategies · tarjeta | **Max DD** / sparkline | MTM del libro cuando la estrategia es todo el libro (`max_drawdown_mtm`) | ✅ **antes: curva realizada (0,19 % vs 2,7 % en Risk)** |
| Strategies · leaderboard | Max DD | idem, con sufijo "mtm" | ✅ |
| Strategies · Trend panel | targets/weights/positions/liquidez/basis/tracking | `/api/trend` 30 s + `Freshness` | ✅ |
| Risk · KPI | Equity / Peak / Drawdown / All-time max | `risk_update` + `metrics.max_drawdown` (ahora MTM del histórico) | ✅ |
| Risk · Loss limits | Daily / Weekly / DD | MTM vs límites del perfil | ✅ |
| Risk · Exposure | por mercado tenido | posiciones | ✅ (ronda 16) |
| Risk · perfiles | expected/year, worst DD, ladder | `/api/risk/profiles` 30 s + `Freshness` | ✅ |
| System | Health / Ops / Connections | 5 s / 30 s + `Freshness` | ✅ (plazo del run corregido, ronda 16) |
| Data | feeds / catálogo | WS + `/api/data/catalog` 60 s | ✅ |

## Lo que sigue siendo aproximado, y se dice en pantalla
- Los días anteriores al primer muestreo MTM (antes del despliegue del 8 sep) son **estimados**: posiciones al
  cierre diario de la fuente, no a la marca del venue (basis típico 0,2–1,3 %), y sin el pico intradía. El pico
  real persistido (1.038,87) se dibuja como línea de referencia.
- Sharpe 30D: n/a hasta 30 días de histórico marcado.

## Cómo comprobarlo
- `py -3.12 scripts/reconcile_accounting.py` → 60 identidades desde las filas crudas; cualquier `MISMATCH` nombra la
  superficie que discrepa.
- `/api/performance.equity_history` → `real_since`, `estimated_until`, `samples`.
