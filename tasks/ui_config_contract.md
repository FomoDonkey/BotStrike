# BotStrike v2.14 — Contrato API bridge ↔ UI (configuración en caliente, trend diario, edge monitor)

Fecha: 2026-09-02. Este documento es la referencia compartida entre backend (`server/bridge.py`)
y frontend (`desktop/src`). Todo lo que la UI muestre o edite pasa por aquí.

## 1. Configuración editable

### `GET /api/config`
```json
{
  "use_testnet": false, "has_api_key": true, "has_telegram": true,
  "symbols": [ { "symbol": "BTC-USD", "leverage": 2, "max_position_usd": 500, "...": "todos los campos de SymbolConfig" } ],
  "trading": { "initial_capital": 1000, "...": "todos los campos de TradingConfig" },
  "overrides": { "trading": {...}, "symbols": {"BTC-USD": {...}} },   // lo que el usuario ha cambiado (persistido en data/config_overrides.json)
  "restart_required": false                                             // true si hay overrides que sólo aplican tras reiniciar el engine
}
```

### `GET /api/config/schema`
Describe TODOS los campos editables. La UI renderiza formularios genéricos a partir de esto.
```json
{
  "groups": [
    { "id": "capital", "label": "Capital & Risk", "fields": [
        { "path": "trading.initial_capital", "label": "Initial capital", "type": "number", "min": 50, "max": 10000000, "step": 10, "unit": "$", "help": "...", "restart_required": true },
        { "path": "trading.compounding_enabled", "label": "Compound gains", "type": "bool", "help": "Size positions on all-time equity (initial capital + realized PnL) instead of the fixed initial capital" },
        { "path": "trading.max_drawdown_pct", "type": "percent", "min": 0.01, "max": 0.5, "step": 0.005 }
    ]},
    { "id": "strategies", "label": "Strategies", "fields": [ { "path": "trading.allocation_mean_reversion", "type": "percent", ... } ] },
    { "id": "trend_daily", "label": "Trend daily", "fields": [ ... ] },
    { "id": "edge", "label": "Edge monitor", "fields": [ ... ] },
    { "id": "execution", "label": "Execution", "fields": [ ... ] },
    { "id": "notifications", "label": "Notifications", "fields": [ ... ] },
    { "id": "symbols", "label": "Symbols", "per_symbol": true, "fields": [ { "path": "symbols.{symbol}.leverage", ... } ] }
  ]
}
```
Tipos: `number` (float), `int`, `percent` (float 0–1, la UI muestra %), `bool`, `string`, `select` (con `options: [{value,label}]`), `list` (string separado por comas).
`path` usa notación punto; para símbolos `symbols.<SYMBOL>.<campo>`.

### `PUT /api/config`  (token requerido cuando el bridge no es loopback — cabecera `X-BotStrike-Token`)
Body: `{ "trading": { "max_drawdown_pct": 0.08 }, "symbols": { "BTC-USD": { "leverage": 1 } } }` (sólo lo que cambia).
Respuesta 200:
```json
{ "status": "ok", "applied": ["trading.max_drawdown_pct", "symbols.BTC-USD.leverage"], "restart_required": false, "config": { ...igual que GET /api/config... } }
```
Errores: 400 `{"detail": "trading.max_drawdown_pct: must be between 0.01 and 0.5"}`; 401/403 sin token.
Los cambios se aplican EN CALIENTE al engine cuando el campo lo permite; si algún campo lleva `restart_required`, la respuesta lo indica y la UI ofrece "Restart engine".

### `POST /api/config/reset` (token) → borra overrides, responde como GET. `restart_required: true`.

### `POST /api/bot/restart` (token) → `{"status": "restarting", "mode": "paper"}` (stop + start con el mismo modo/exchange).

## 2. Estrategias y edge

### `GET /api/strategies`
```json
{ "strategies": [
  { "type": "MEAN_REVERSION", "name": "Mean Reversion", "enabled": false, "active": false, "allocation": 0.0,
    "killed": false, "kill_reason": "", "description": "Z-score 1m · entry |z|>2.0 · exit 0.5 · SL 1.5×ATR · TP 4×ATR",
    "params": { "mr_zscore_entry": 2.0, "...": 0 }, "symbols": ["ETH-USD","SOL-USD","ADA-USD"],
    "edge": { ...bloque de /api/edge para esta estrategia... } },
  { "type": "TREND_DAILY", "name": "Trend daily (Donchian ensemble)", "enabled": true, "active": true, "allocation": 1.0, "description": "...", "params": {...}, "edge": {...} }
] }
```
`enabled` = asignación > 0 (config); `active` = enabled y no killed; `killed` viene del edge monitor.

### `GET /api/edge`
```json
{ "window": 200, "min_trades": 100, "t_stat_kill": -2.0, "fee_share_kill": 0.5, "computed_at": 1756800000.0,
  "strategies": {
    "MEAN_REVERSION": { "n": 19, "wins": 6, "win_rate": 0.316, "net_pnl": -8.85, "gross_pnl": -6.26, "fees": 2.59,
      "mean_gross_bps": -15.3, "se_bps": 9.1, "t_stat": -1.68, "profit_factor": 0.20, "fee_share": 0.81,
      "expectancy_usd": -0.47, "avg_hold_min": 28.1, "verdict": "insufficient", "reason": "19 < 100 trades" }
  } }
```
`verdict`: `insufficient` | `ok` | `warn` | `kill`. `fee_share` = fees / beneficio bruto de las ganadoras (1.0 si no hay ganadoras).

### `GET /api/trend`
```json
{ "enabled": true, "allocation": 1.0, "mode": "paper",
  "next_run_utc": "2026-09-03T00:05:00Z", "last_run_utc": "2026-09-02T00:05:12Z", "last_run_status": "ok", "last_error": "",
  "universe": ["BTCUSDT","ETHUSDT","SOLUSDT"], "candidates": 20,
  "targets": { "BTCUSDT": 0.31, "ETHUSDT": 0.0, "SOLUSDT": 0.27 },
  "positions": [ { "symbol": "BTCUSDT", "size": 0.0041, "entry_price": 77100.0, "mark_price": 77400.0, "notional": 317.3, "unrealized_pnl": 1.23, "weight": 0.31, "opened": "2026-08-30" } ],
  "equity_basis": 989.04, "exposure": 0.58,
  "tracking": { "days": 3, "model_return": 0.0041, "paper_return": 0.0038, "tracking_error_ann": 0.012,
                "records": [ { "date": "2026-09-01", "model_ret": 0.001, "paper_ret": 0.0009, "slippage_bps": 1.4 } ] },
  "params": { "lookbacks": "5,10,20,30,60,90", "target_vol": 0.2, "vol_window": 90, "n_assets": 3, "leverage_cap": 2.0, "rebalance_threshold": 0.2, "execution_hour_utc": 0, "execution_delay_min": 5 } }
```

## 3. Rendimiento y riesgo

### `GET /api/performance` (añadidos)
`current_drawdown` (all-time, incluye unrealized), `peak_equity`, `sample_days`, `sharpe_valid` (false si < 30 días o < 30 trades → la UI muestra "n/a"), `first_trade_ts`.

### `GET /api/risk` (nuevo)
```json
{ "equity": 989.04, "peak_equity": 1000.0, "drawdown_pct": 0.011, "max_drawdown_pct": 0.10,
  "daily_pnl": 0.0, "daily_limit": 19.78, "max_daily_loss_pct": 0.02,
  "weekly_pnl": -3.2, "weekly_limit": 49.45, "max_weekly_loss_pct": 0.05,
  "circuit_breaker": false, "drawdown_halted": false, "killed_strategies": {}, "compounding_enabled": true, "equity_basis": 989.04 }
```
El broadcast WS `risk_update` añade los mismos campos (`peak_equity`, `daily_pnl`, `daily_limit`, `weekly_pnl`, `weekly_limit`).

### WS `trading` → `positions`
Las posiciones del trend diario también se emiten (`strategy: "TREND_DAILY"`, `symbol` en formato `BTC-USD`).

### `GET /api/data/catalog`
`records` y `date_range` reales (leídos del parquet), cache 5 min.

### `GET /api/health` (añadidos)
`telegram_failures`, `microstructure_enabled`, `trend_daily_enabled`.

## 4. Rutas
Cualquier `GET /<ruta-sin-extension>` que no sea `/api` ni `/ws` redirige a `/#/<ruta>`.
