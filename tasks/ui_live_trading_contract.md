# BotStrike v2.15 — Live Trading "terminal" + estrategia DIVERGENCE — contrato API ↔ UI

Fecha: 2026-09-02. Referencia visual: https://app.strikefinance.org/trade/BTC-USD (capturas en la sesión).
Objetivo de Edgar: que Live Trading muestre MUCHA más información de los trades, al nivel de un
exchange profesional, y añadir una estrategia de divergencias con verificación de entrada.

## 0. Lenguaje visual de la referencia (lo que se copia y lo que no)

- Fondo casi negro (`#0A0A0A`), paneles separados por bordes de 1 px `rgba(255,255,255,.12)`, sin
  sombras ni glassmorphism. Texto blanco al 100 % para valores, 60 % para etiquetas, gris `#B8B8B8`
  para secundarios. Verde menta `#4EFAB0` (largos, compras, positivo), rosa `#F43F5E` (cortos, ventas,
  negativo). Un solo acento: el menta. Tipografía IBM Plex Sans 12.5 px (tablas/etiquetas), 16 px
  (valores), 18 px (precio principal); números siempre tabulares.
- Etiquetas con subrayado punteado = tienen tooltip explicativo (Mark Price, Funding, Liq. Price…).
- Densidad alta pero ordenada: cada panel tiene título en pestañas (Chart | Funding | Depth …),
  las tablas usan cabeceras en mayúsculas pequeñas y filas de 32 px.
- NO copiar: el naranja del logo, los botones de depósito/retirada, copy trading, referrals, vaults.
  BotStrike sigue con su acento `#00D4AA`; se adopta la estructura, la densidad y la jerarquía.

## 1. Layout de Live Trading (≥ 1280 px)

```
┌ Market header ───────────────────────────────────────────────────────────────────┐
│ [BTC-USD ▾] Mark 76,681 · Index 76,716 · Funding −0.0037 % / 00:56:14 · 24h −1.71 % · │
│ 24h High 78,330 · 24h Low 76,238 · 24h Vol 1.1 M $ · Regime RANGING (15m, 2h 10m) │
├ Chart (tabs: Chart | Signals | Depth | Details) ─────┬ Order book / Trades ──────┤
│ candles 1m/5m/15m/1h + volumen + panel RSI/MACD      │ Price · Size · Total       │
│ marcadores: entradas/salidas, SL/TP vivos, pivotes   │ barras de profundidad      │
│ y líneas de divergencia (cuando la estrategia está   │ mid + spread + ratio B/S   │
│ activa), botones "Long"/"Short" DESHABILITADOS       │ Trades: hora, precio,      │
│ (paper no acepta órdenes manuales… ver §4)           │ tamaño, lado (tape vivo)   │
├ Positions | Orders (SL/TP) | Trade History | Signals | Account ──────────────────┤
│ tabla densa (columnas §2)                                                        │
└──────────────────────────────────────────────────────────────────────────────────┘
Columna derecha inferior: Account overview (§3).  Móvil: pestañas apiladas.
```

## 2. Datos por trade / posición (backend → UI)

### WS `trading` → `positions` (por símbolo) — campos añadidos por posición
```json
{ "symbol": "BTC-USD", "side": "BUY", "size": 0.00152, "entry_price": 76571.65, "mark_price": 76560.2,
  "notional": 116.4, "unrealized_pnl": -0.02, "pnl_pct": -0.00015, "roe_pct": -0.0003,
  "leverage": 2, "margin": 58.2, "liquidation_price": 38285.8,
  "stop_loss": 0.0, "take_profit": 0.0, "sl_distance_pct": null, "tp_distance_pct": null,
  "strategy": "TREND_DAILY", "opened_ts": 1788347201.0, "hold_sec": 5400,
  "mae_bps": -12.3, "mfe_bps": 8.1, "entry_fee_rate": 0.0004, "fees_paid": 0.0,
  "funding_paid": 0.0, "order_id": "trend_entry_ab12cd34", "trigger": "donchian_ensemble" }
```
- `liquidation_price` en paper = precio al que el margen (notional/leverage) se agota:
  long `entry × (1 − 1/leverage + mm)`, short `entry × (1 + 1/leverage − mm)` con `mm` = 0.5 %.
- `roe_pct` = unrealized / margin. `mae_bps`/`mfe_bps` vivos desde PaperPosition.
- Las posiciones del trend diario llevan `leverage` = 1 y `liquidation_price` = 0 (spot-like).

### `GET /api/trades?limit=N` — campos añadidos por fila (cierres)
`fee`, `pnl_bps`, `mae_bps`, `mfe_bps`, `slippage_bps`, `order_type`, `exit_reason` (SL/TP/signal/
time/rebalance/trend_exit/close), `hold_sec`, `entry_ts`, `exit_ts`, `equity_after`, `trigger`,
`strategy`, `regime`, `roe_pct` (pnl / margen), `leverage`.

### `GET /api/orders` (nuevo) — órdenes protectoras vivas del paper (SL/TP por posición)
```json
{ "orders": [ { "symbol": "ETH-USD", "type": "STOP", "side": "SELL", "price": 2340.1, "size": 0.03,
                "strategy": "MEAN_REVERSION", "position_id": "paper_entry_…", "distance_pct": -0.012 } ] }
```

### WS `trading` → `signal` — metadata completa de la señal (ya se envía `metadata`); la UI la
muestra en el feed: trigger, confirmaciones, RSI/ADX/z-score, ATR bps, SL/TP, tamaño, y para
DIVERGENCE: tipo (regular/hidden), pivotes (ts, precio, RSI), gap de RSI, nivel de disparo, MACD.

## 3. Account overview — `GET /api/account` (nuevo) + WS `risk_update` (mismos campos)
```json
{ "mode": "paper", "equity": 989.0, "initial_capital": 1000.0, "realized_pnl": -10.96, "unrealized_pnl": -0.04,
  "position_value": 263.3, "margin_used": 131.6, "available": 857.4, "margin_ratio": 0.133,
  "exposure_pct": 0.266, "leverage_effective": 0.27, "open_positions": 3, "fees_today": 0.0,
  "daily_pnl": 0.0, "weekly_pnl": -5.05, "peak_equity": 1000.0, "drawdown_pct": 0.011 }
```

## 4. Market header — `GET /api/market/{symbol}` (nuevo) + WS `snapshot`
`price`, `mark_price`, `index_price`, `funding_rate`, `funding_countdown_sec` (próximo múltiplo de 8 h
UTC), `change_24h_pct`, `high_24h`, `low_24h`, `volume_24h_usd`, `open_interest`, `spread_bps`,
`regime`, `regime_since`, `regime_timeframe_min`. 24h high/low/change se calculan desde las velas 1m
en memoria (`market_data.get_dataframe`) — no hace falta REST.

Botones Long/Short: en paper NO hay órdenes manuales (el bot decide). Se muestran deshabilitados
con tooltip "Las órdenes las decide el motor; activa una estrategia en Strategies". No es un
exchange: no se implementa formulario de orden.

## 5. Estrategia DIVERGENCE — `StrategyType.DIVERGENCE`
- Marco: velas de 1 h agregadas de las de 1 min en memoria (el motor arranca con 16 h de 1 min;
  la estrategia pide al menos `regime_vol_lookback` velas de 1 h → hasta tener historia usa el
  seed de 1 h por REST, `seed_from_binance(interval="1h", hours=400)`).
- Detección: pivotes de precio confirmados con `pivot_k` velas a cada lado; divergencia regular
  alcista (precio LL, RSI HL con RSI del primer pivote < `rsi_os`) y bajista (HH / LH, RSI > `rsi_ob`);
  opcional oculta (continuación) con filtro EMA200.
- Verificador (lo que Edgar pidió): la divergencia es candidata; la entrada exige (1) separación
  5–60 velas y gap de RSI ≥ `min_rsi_gap`, (2) DISPARO por ruptura de estructura: cierre por encima
  del máximo de la vela del segundo pivote (alcista) / por debajo del mínimo (bajista) dentro de
  `trigger_window` velas, (3) histograma MACD confirmando en la vela del disparo, (4) opcional volumen
  ≥ media. Entrada a mercado tras el cierre del disparo; SL bajo el pivote − `atr_buffer`×ATR;
  TP = `rr`×riesgo; salida por tiempo a `max_hold` velas. Tamaño por riesgo (como MR).
- Parámetros en Settings (grupo "Divergence"): rsi_period, pivot_k, rsi_os, rsi_ob, min_gap_bars,
  max_gap_bars, min_rsi_gap, trigger_window, require_macd, require_volume, atr_buffer, rr, max_hold,
  hidden, timeframe_min. Asignación `allocation_divergence` (0 por defecto salvo GO de la
  investigación `scripts/divergence_research.py`).
- Régimen: multiplicador 1.0 en RANGING y TRENDING (las divergencias regulares son reversión al
  final de tendencia), 0 en BREAKOUT.
- La UI muestra en el feed de señales y en el chart: los dos pivotes, la línea de divergencia
  (precio y RSI), el nivel de disparo y el SL/TP resultante.
