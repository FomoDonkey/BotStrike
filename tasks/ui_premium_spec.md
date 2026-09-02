# BotStrike UI/UX v2.16 — "Strike-grade" specification (2026-09-02)

Reference studied tab by tab in Chrome: https://app.strikefinance.org (Trade, Portfolio, Leaderboard,
Vaults, Staking, Tools menu, settings popover, bottom tabs, chart tabs, market picker, order book,
trades tape, footer ticker). Edgar's brief: **premium, elegant, professional, "bien desglosado",
bright white text everywhere, nothing dim/dark/greyed, striking colours, accessible, not chaotic.**

This document is the contract for the frontend agent. Backend endpoints in §5 are implemented in
`server/bridge.py` (v2.16) — treat the shapes as exact.

---

## 0. Acceptance criteria (non-negotiable, verified by the coordinator)

1. `py -3.12 scripts/ui_contrast_audit.py <base> --routes trading,portfolio,strategies,risk,backtest,data,settings,system`
   → **0 offenders at 1440 px and at 390 px** on every route. Rule: any visible text has effective
   alpha ≥ 0.70 and WCAG contrast ≥ 4.5:1 (≥ 3:1 for ≥ 18 px). Disabled controls are exempt only when
   they carry `disabled`/`aria-disabled`.
2. `cd desktop && npx tsc -b && npm run lint && npm run build:web` all exit 0.
3. Playwright at 1440×900 and 390×844: no horizontal overflow (`scrollWidth == clientWidth`), 0 page
   errors, 0 console errors on every route; screenshots of every route in both sizes.
4. Numbers on screen equal the API (spot-check equity, available, margin ratio, position rows, trade
   counts, strategy status) — report the actual values compared.
5. Never PUT config, start/stop/restart against the CT (http://192.168.1.204:9420 is read-only). Local
   bridge on 9421 with `TELEGRAM_BOT_TOKEN= TELEGRAM_CHAT_ID=` and isolated env (see README v2.15).
6. Only `desktop/src`; no commits. Report what was verified with real numbers and what was not.

---

## 1. Design tokens (measured on Strike, adapted to Edgar's brightness rule)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0A0A0A` | page canvas (body background, explicit) |
| `--panel` | `#0F0F0F` | cards, tables, side panels |
| `--panel-2` | `#141414` | nested surfaces, segmented control track, inputs |
| `--hover` | `#1A1A1A` | row hover, button hover |
| `--active` | `#232323` | active segment / selected tab pill |
| `--hairline` | `rgba(255,255,255,0.10)` | all borders (1 px) |
| `--hairline-strong` | `rgba(255,255,255,0.18)` | outlined buttons, focused inputs |
| `--text` | `#FFFFFF` | **every value, number, title, nav item, table cell** |
| `--text-2` | `rgba(255,255,255,0.80)` | labels, column headers, helper text (Strike uses 0.60 — we go brighter) |
| `--text-3` | `rgba(255,255,255,0.72)` | timestamps, footnotes — the DIMMEST allowed for visible text |
| `--disabled` | `rgba(255,255,255,0.45)` | only with `disabled`/`aria-disabled` |
| `--mint` | `#4EFAB0` | accent: active nav, links, primary CTA fill, positive values, switches ON |
| `--mint-soft` | `rgba(78,250,176,0.16)` | LONG chip bg, positive row tint, mint focus ring |
| `--rose` | `#F43F5E` | negative values, SHORT chip text, sell side |
| `--rose-soft` | `rgba(244,63,94,0.16)` | SHORT chip bg, negative tint |
| `--amber` | `#F5B942` | warnings, paper mode chip, research NO-GO |
| `--blue` | `#5AA9FF` | info, MACD line, index price |
| `--up` / `--down` | `#4EFAB0` / `#F43F5E` | candles, order book sides, depth chart |
| `--grid` | `rgba(255,255,255,0.06)` | chart grid |
| font | IBM Plex Sans (Google Fonts, already loaded), fallback system-ui | everything; `font-variant-numeric: tabular-nums` on every number |
| sizes | 12.5 px tables/body · 13 px chips · 14 px nav/buttons · 16 px values/headers · 20 px price · 24–32 px hero numbers | |
| weights | 400 body · 500 labels/table cells · 600 values/CTAs/titles · 700 hero | |
| radius | 6 px chips/inputs · 8 px buttons/cards · 10 px modals | |
| spacing | 4 / 8 / 12 / 16 / 24 | |

**Buttons.** Primary: mint fill, `#0A0A0A` text, 600, 8 px radius, hover brightens 6 %. Secondary:
transparent, `--hairline-strong` border, white text. Segmented control (Cross / 100x / One-Way
style): track `--panel-2`, active `--active` with white text, inactive `--text-2`. Tabs: text
`--text-2`, active white with 2 px mint underline. Switches: mint when ON, `#3A3A3A` track when OFF
with a white knob. Chips: LONG/SHORT as above; status chips (ACTIVE mint, PAPER amber, KILLED rose,
DISABLED `--panel-2` white text). Labels with a tooltip get `text-decoration: underline dotted`
(`Hint` component) exactly like Strike.

**Charts.** lightweight-charts: background transparent over `--panel`, grid `--grid`, axis text
`--text-2`, candles up/down tokens, volume 40 % alpha, MACD line `--blue`, signal `--amber`,
histogram up/down, RSI mint with 30/70 dotted lines. recharts: same palette, area fills at 20 % alpha,
tooltip on `--panel-2` with white text.

**Absolutely no**: grey text under 0.72 alpha, blurred/backdrop-dimmed text, glass gradients that
reduce contrast, decorative emojis, three different greys on one screen.

---

## 2. App shell (replaces the left sidebar)

**Top navigation bar (56 px, `--bg`, hairline bottom)** — mirrors Strike:
- Left: BotStrike logo mark + wordmark (white, "STRIKE"-style weight 700).
- Items: **Trade · Portfolio · Strategies · Risk · Backtest · Data · System** (14 px, 500, `--text-2`; active
  white + mint 2 px underline). "Portfolio" replaces Dashboard + Performance (keep `/dashboard` and
  `/performance` redirecting to `/portfolio`). Settings lives behind the gear.
- Right cluster: connection dot + "Paper · Binance feed" (mint dot when `ws_connected`), regime chip
  (from `/api/regime`), equity chip (`Equity $1,003.42`, white, tabular), gear (opens the Settings
  popover, §3.7), primary CTA **"Bot · Running"** (mint) with a dropdown Start/Stop/Restart — actions
  token-gated exactly as today, disabled without a token; on the CT never trigger them.
- Mobile (< 1024 px): logo + regime chip + equity + hamburger; a **bottom tab bar** with Trade,
  Portfolio, Strategies, Risk, More (More opens the remaining routes).

**Footer status bar (32 px, `--bg`, hairline top, on every page)** — mirrors Strike's:
`● Paper` (mint dot) · `Binance feed 0.1 s` · ticker marquee of the 4 symbols (price + 24 h % coloured)
· `24H Vol $8.1B` (BTC) · right: `Activity` (opens the feed), `Docs`, `System`.

**Favorites strip (Trade page only, under the nav)**: the 4 symbols with icon, price, 24 h %.

---

## 3. Pages (Strike mapping)

### 3.1 Trade (`/trading`) — the Strike trade page, one to one

Grid at ≥ 1280 px: `[chart column 1fr] [order book 290 px] [bot column 300 px]`; bottom panel full
width; favorites strip on top. Exactly Strike's proportions in the screenshots.

- **Market header**: symbol picker (icon, name, chevron → dropdown with search, tabs Favorites/All,
  columns Symbol · Last Price · 24h Change · 24h Volume · Open Interest · 8H Funding; keyboard hints
  row `Ctrl K Open · ↑↓ Navigate · Enter Select · Esc Close` — implement Ctrl+K), then Mark Price,
  Index Price, Funding / Countdown (`+0.0018% / 00:44:10`, dotted labels), 24h Change (coloured), 24h
  High, 24h Low, 24h Vol (base + USD), Open Interest, Spread, Regime chip. Labels `--text-2`, values
  white 600. Horizontal scroll with a chevron when narrow (Strike does this).
- **Chart tabs**: `Chart · Funding · Depth · Signals · Details`.
  - Chart: toolbar `1m 5m 15m 1h 4h · More ▾ · Mark Price ▾ · Indicators ▾ (RSI/MACD/none) · Chart
    Elements` + right icons (screenshot, reset, fullscreen); OHLC legend line `BTC-USD · 5m O H L C
    Δ` like Strike; overlays (entry line, SL/TP, divergence pivots) as today.
  - Funding: line chart of `/api/market/{sym}/funding_history` with 24H / 1W / 1M pills, positive
    mint / negative rose fills and a cumulative line — as Strike's Funding tab.
  - Depth: existing DepthChart restyled (mint/rose areas, mid label).
  - Signals: existing feed restyled.
  - Details: "About <symbol>", Order Size Rules (paper: min notional, max position, leverage from
    config), Funding & Fees (current rate, next payment countdown, maintenance margin 0.5 %, taker/maker
    paper fees), Price Protection (paper slippage model), Regime parameters (15 min bars, 30 min dwell).
- **Order book column**: tabs `Order Book · Trades`; layout toggles (both / bids / asks icons),
  precision selector; rows with depth bars (rose left for asks, mint for bids), mid price large
  (20 px, mint/rose with arrow) + `Spread: 0.10 / 0.013 bps`; footer ratio bar `B 50.01% ▮▮▮ 49.99% S`.
  Trades tab: Price · Size · Time with coloured price.
- **Bot column** (Strike's order form becomes the bot's control/account panel):
  - Segmented header `Paper · 1x · Long-only` (mode, leverage of symbol, direction policy).
  - Tabs `Bot · Account`.
  - Bot: the strategies acting on this symbol (name, status chip, allocation, next action: e.g. "Trend
    daily · next run in 5h 37m · target 11.8 %"), the open position card for this symbol (side chip,
    size, entry, mark, PnL ROE, SL/TP, liq, hold) and a secondary button **Close position (paper)**
    (token-gated, confirm dialog; disabled without token). Below: a details list with dotted labels —
    Est. entry (current mark), Slippage model, Est. liquidation, Margin, Order size (next sizing from
    risk manager: `risk_per_trade_pct × equity`), Fees — like Strike's "Est. Entry / Slippage / Est.
    Liquidation Price / Margin / Order Size / Fees".
  - Account: **Account Overview** exactly as Strike's list: Account Value, Available Balance,
    Withdrawable Balance (= available), Position Value, Unrealized PNL, Margin Ratio, Maintenance
    Margin; plus Daily PnL / Weekly PnL with limits, Peak equity, Drawdown. Buttons row **Restart
    engine** (secondary, token-gated) in place of Deposit/Withdraw.
- **Bottom panel**: tabs with counts `Positions 3 · Orders 0 · Order History · Trade History 24 ·
  Signals · Activity`; right side: view toggles, filters `Markets ▾ · Strategy ▾ · Type ▾`, **Export
  (CSV)** → `/api/trades/export.csv`. Positions columns as v2.15 (Symbol, Side, Size, Notional, Entry,
  Mark, Liq. Price, Margin, Lev, PNL (ROE %), SL, TP, MAE/MFE, Hold, Strategy, Trigger, Regime, Fees).
  Order History = ENTRY/EXIT rows with order type, slippage, spread, regime. Activity = `/api/activity`.

### 3.2 Portfolio (`/portfolio`) — Strike's Portfolio page

Three columns at ≥ 1280 px: `[left 290 px] [center 1fr] [right 330 px]`.

- **Left column** (list rows, label `--text-2` left, value white right, dotted labels where a tooltip
  helps):
  - Account info: mode chip PAPER, feed Binance, `Initial capital $1,000.00`, `Since 2026-09-02`.
  - **Account value** hero (28 px 700) = equity.
  - Account equity: Paper balance (cash = equity − margin used), Trend book (trend positions
    notional), Unrealized PNL.
  - Overview: Unrealized PNL, Account leverage, Margin usage %, All Time PNL, All Time Volume, Fees
    paid.
  - 30 Day Volume + link "See trade history".
  - Fees (Taker/Maker) paper `0.04% / 0.02%`.
  - Analysis: Longest win streak (days), Trading style, Avg trade duration, Median trade duration.
  - Performance 30D: Drawdown, Win rate, Sharpe (n/a when `sharpe_valid=false`, with the reason).
- **Top KPI cards** (4): Performance (`+$3.42 PNL`, 18 win/loss-day dots mint/rose/grey, `1 win days ·
  1 days`), Leverage (`0.27x`, bar), Margin usage (`26.7 %`, bar, `$735 free`), Direction bias
  (Long/Short bar with `100 % · Long $268 · $0 Short · 0 %`).
- **Center chart card**: tabs `Account Value · PNL · Volume · Calendar`, range `7D ▾ (7D/30D/ALL)`.
  Account Value: mint area line with dotted "now" marker; PNL: bars per day mint/rose; Volume: bars;
  Calendar: month grid with daily PnL cells (mint/rose intensity, value on hover) — from
  `/api/portfolio.daily`.
- **Below**: tabs `Positions · Trend book · Open Orders · Order History · Trade History` (same tables as
  Trade).
- **Right column — Recent activity** (`/api/activity`): rows exactly like Strike's: `[LONG] BTC-USD
  Opened · Sep 2, 11:06 · Size 0.00152 BTC ($117.78)`, `Closed · +$1.12 (+0.96 %)`; run rows `Trend
  daily run OK · 3 positions`; regime rows `BTC-USD → RANGING`; kill/halt rows in rose; restart rows.

### 3.3 Strategies (`/strategies`) — Strike's Vaults + Leaderboard

- **Cards row** (one per strategy, Vault-card style): name + verified/status chip, one-line
  description, `All-time PNL` + sparkline (equity of that strategy from `/api/portfolio.by_strategy`),
  KPIs list: All-time PNL, 30D return, Trades, Win rate, PF, Sharpe, Max DD, Age (since first trade),
  Allocation; research chip (`RESEARCH GO 11/11` mint / `NO-GO 2/7` amber) with a hover card showing
  the checklist; the switch + allocation slider; primary button **View details** → expands the
  existing parameters/edge panel; TrendDailyPanel restyled inside.
- **Strategy leaderboard table** below (Rank · Strategy · Status · Equity share · All-time PnL ·
  Realized · Volume · Trades · Fees · Win rate · Sharpe · Max DD · t-stat) from
  `/api/portfolio.by_strategy` + `/api/edge`.

### 3.4 Risk (`/risk`) — ladder (daily/weekly/max DD) as three progress rows with white numbers, the
kill list, exposure by symbol bars, compounding basis; same list style as Portfolio left column.

### 3.5 Backtest, 3.6 Data — restyle only (tokens, tables, buttons, tabs); no logic changes.

### 3.7 Settings — Strike's settings popover (gear) + full page

- Gear popover (any page): **Layout** switches (Account Overview, Chart, Favorites, Order book, Tables,
  Activity feed — persist in localStorage, apply to the Trade page) and **Trading/Display** switches
  (Trade notifications toast, Sound, Color blind mode = swap mint/rose for blue/orange, Compact rows),
  `Reset all`.
- Full page `/settings`: the schema-driven form (unchanged logic) restyled: section headers, rows
  `label · help · control`, mint switches, white inputs on `--panel-2`, Save/Restart banner in amber.

### 3.8 System (`/system`) — health card, **Ops monitor** card from `/api/ops` (last check time, alerts,
facts, next timer), feed status, version/commit, uptime, Telegram status, recent log lines.

---

## 4. Components to build/restyle (desktop/src/components)

`TopNav`, `FooterBar`, `FavoritesStrip`, `MarketPicker` (Ctrl+K), `SegmentedControl`, `Switch`,
`Chip` (side/status), `KpiCard`, `ListRow` (label/value with Hint), `DataTable` (sticky header,
tabular, hover, sortable, empty state "No open positions found" white), `TabBar` (counts), `RangePills`,
`ActivityFeed`, `WinDayDots`, `BiasBar`, `CalendarHeatmap`, `FundingChart`, `SparkLine`.
Delete `GlassPanel` glass effects (keep a plain `Panel`).

---

## 5. Endpoint contracts (v2.16 — implemented in server/bridge.py)

Existing: `/api/health`, `/api/account`, `/api/positions`, `/api/orders`, `/api/market/{sym}`,
`/api/trades?limit&symbol&strategy`, `/api/strategies`, `/api/trend`, `/api/risk`, `/api/regime`,
`/api/edge`, `/api/performance`, `/api/config/schema`, `PUT /api/config`, WS channels.

### 5.1 `GET /api/portfolio`
```json
{
  "engine": true, "mode": "paper", "initial_capital": 1000.0, "since_ts": 1788347165.7,
  "equity": 1003.42, "cash": 735.35, "margin_used": 268.07, "unrealized_pnl": 3.42, "realized_pnl": 0.0,
  "alltime_pnl": 3.42, "alltime_volume": 268.07, "fees_paid": 0.0, "leverage": 0.27, "margin_usage": 0.267,
  "trend_book_notional": 268.07,
  "volume_30d": 268.07, "fees_taker": 0.0004, "fees_maker": 0.0002,
  "analysis": {"longest_win_streak_days": 0, "trading_style": "Swing", "avg_hold_sec": 0, "median_hold_sec": 0},
  "perf_30d": {"drawdown": 0.0, "win_rate": 0.0, "sharpe": null, "sharpe_valid": false, "sharpe_reason": "needs 30 trades and 30 days", "trades": 0},
  "win_days": [{"date": "2026-08-16", "pnl": 0.0, "trades": 0, "result": "flat"}, "... 18 entries, oldest first, result = win|loss|flat"],
  "bias": {"long_notional": 268.07, "short_notional": 0.0, "long_pct": 1.0},
  "daily": [{"date": "2026-09-02", "equity": 1003.42, "pnl": 0.0, "volume": 268.07, "trades": 0, "fees": 0.0}],
  "by_strategy": [{"strategy": "TREND_DAILY", "trades": 0, "open_positions": 3, "pnl": 0.0, "realized": 0.0, "unrealized": 3.42,
                   "volume": 268.07, "fees": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "sharpe": null, "max_drawdown": 0.0,
                   "t_stat": 0.0, "first_trade_ts": 1788347165.7, "equity_curve": [[1788347165.7, 0.0]], "return_30d": 0.0}]
}
```
`daily` covers every calendar day from `since_ts` to today (UTC), for the Calendar/PNL/Volume tabs.
`equity_curve` per strategy = cumulative realized PnL of that strategy over time (for the sparkline).

### 5.2 `GET /api/activity?limit=100`
```json
{"events": [{"ts": 1788374822.1, "kind": "fill", "level": "info", "symbol": "BTC-USD", "side": "BUY",
             "title": "Opened LONG BTC-USD", "detail": "0.001523 BTC ($117.78) · Trend daily", "pnl": null, "roe_pct": null},
            {"ts": ..., "kind": "fill", "side": "SELL", "title": "Closed LONG ETH-USD", "detail": "...", "pnl": 1.12, "roe_pct": 0.0096},
            {"ts": ..., "kind": "run", "title": "Trend daily run OK", "detail": "3 positions · exposure 27 %"},
            {"ts": ..., "kind": "regime", "symbol": "BTC-USD", "title": "Regime RANGING", "detail": "was BREAKOUT"},
            {"ts": ..., "kind": "risk", "level": "warning", "title": "Circuit breaker", "detail": "..."},
            {"ts": ..., "kind": "system", "title": "Bridge started", "detail": "v2.16.0"},
            {"ts": ..., "kind": "config", "title": "Config changed", "detail": "trading.allocation_mean_reversion → 0.5"}]}
```
Newest first. Kinds: `fill | run | regime | risk | kill | system | config | signal`.

### 5.3 `GET /api/market/{sym}/funding_history?limit=200`
```json
{"symbol": "BTC-USD", "points": [{"ts": 1788336000.0, "rate": 0.0000175, "mark_price": 77120.5}], "cumulative": [{"ts": ..., "value": 0.00012}], "source": "binance_fapi", "cached_at": 1788375000.0}
```
Every 8 h (Binance funding), ≈ 33 days for limit 100. Positive = longs pay.

### 5.4 `GET /api/ops`
```json
{"available": true, "last_check": "2026-09-02T19:48:00+00:00", "alerts": [{"key": "...", "text": "..."}], "sent": [], "summary_sent": false,
 "facts": {"bridge": "ok", "trend_status": "ok", "trend_last_run": "..."}, "journal_15": {"errors": 0, "regime_changed": 0, "telegram_sent": 0, "restarts": 0},
 "state": {"last_summary_date": "2026-09-02", "last_alerts": {}}}
```
`available:false` when the monitor has not run yet (desktop/local).

### 5.5 `GET /api/trades/export.csv` — CSV of all trades (same columns as `/api/trades`), UTF-8, header row.

### 5.6 Symbol details for the Details tab: use `/api/config/schema` + `/api/market/{sym}` + settings
values already exposed by `/api/strategies` (leverage per symbol is in `SymbolConfig`; the bridge adds
`symbol_config` to `/api/market/{sym}`: `{"leverage": 1, "max_position_usd": 500, "min_notional_usd": 20,
"strategies": ["TREND_DAILY"], "taker_fee": 0.0004, "maker_fee": 0.0002, "maintenance_margin": 0.005}`).

---

## 6. Mobile (390 px) — everything stacks: favorites strip scrolls, market header scrolls
horizontally, chart tabs, chart 260 px + indicator pane 96 px (absolute inset), order book + bot
column become tabs (`Book · Trades · Bot · Account`), bottom panel tabs scroll, tables scroll inside
their container, bottom tab bar fixed. Portfolio: KPI cards 2×2, left column collapses into an
"Account" accordion, activity feed below the chart.

## 7. Verification protocol for the agent
1. Build; start the isolated local bridge on 9421; run the contrast audit at 1440 and 390 → fix until 0.
2. Screenshots of every route at both sizes; open them and look (labels clipped? overlaps? dim text?).
3. Against the CT (GET only): same audit + screenshots; compare 8 numbers with the API and list them.
4. Report: verified (with numbers), not verified, files touched.
