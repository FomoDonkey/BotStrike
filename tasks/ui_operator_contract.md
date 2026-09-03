# UI contract v2.17 — operator visibility: exits, funding, manual close, risk profile

Edgar's questions on 2026-09-03, in his words: *"cuando se cierran las operaciones? y cuando se
abren que busca hasta donde? no habia ni tp ni sl ni boton de cierre manual... tampoco veo el
funding por operacion ni el total del funding"* and *"siempre solo usa x1 leverage... si algún
usuario quiere aumentar el riesgo para ganar más en cuentas pequeñas"*.

All four backends are LIVE on the CT (v2.16.0+, verified 2026-09-03 17:2xZ). This document is the
UI contract. Same rules as before: only `desktop/src`, no commits, never mutate the CT, contrast
audit must stay at 0 offenders, tsc/lint/build clean, Playwright at 1440 and 390.

---

## 1. Exit ladder — "when does this position close?"

`GET /api/positions` rows now carry `exit_ladder` (null for intraday strategies, which use a real
stop/target):

```json
"exit_ladder": {
  "price": 80845.2, "active": 4, "total": 6,
  "levels": [
    {"lookback": 20, "stop": 71574.79, "distance_pct": -0.0754, "share_exiting": 0.25, "weight_after": 0.75},
    {"lookback": 30, "stop": 71574.79, "distance_pct": -0.0754, "share_exiting": 0.25, "weight_after": 0.50},
    {"lookback": 60, "stop": 71269.79, "distance_pct": -0.0785, "share_exiting": 0.25, "weight_after": 0.25},
    {"lookback": 90, "stop": 69437.14, "distance_pct": -0.1022, "share_exiting": 0.25, "weight_after": 0.0}
  ],
  "first_exit": 71574.79, "full_exit": 69437.14, "worst_case_pct": -0.1022
}
```

**What it means, and the copy must say it plainly.** The trend book has no single stop-loss and no
take-profit *on purpose*: the position is the average of six Donchian sub-strategies, each with its
own never-falling trailing stop, so the position leaves the market in steps as price falls.
Cutting profits with a fixed target would destroy the edge, because the entire return comes from a
few long trends. What the operator needs is not a TP, it is to SEE this ladder.

Requirements:
- **Positions table**: replace the `---` in the SL/TP columns for trend rows with a compact
  `Exit 71,575 → 69,437` cell plus a small 4-segment bar showing how much weight leaves at each
  level. The hover card shows every level: price, distance %, share exiting, weight after.
- **A "Exits" column** showing `4/6 legs` (active/total sub-strategies) — that is the honest
  measure of how committed the position still is.
- **Chart overlay** on the Trade page for the selected symbol: draw each ladder level as a dashed
  horizontal line labelled `exit 25 %` … `full exit`, in rose, plus the entry line. This is the
  single most useful visual: the user sees exactly where the position dies.
- **Empty state** for intraday strategies: show the real `stop_loss` / `take_profit` as today.
- Copy for the hover/help: "This position exits in steps. Each Donchian lookback has its own
  trailing stop that never falls; when price closes below one, that share leaves. There is no take
  profit: trend returns come from letting winners run."

## 2. Manual close — the operator brake

`POST /api/positions/close` body `{"symbol": "BTC-USD"}`, token-gated, paper-only for now (409 in
live with a clear message).

- A **Close** button on every position row (and in the position card of the Trade page).
- Confirmation dialog naming the symbol, size, notional and current unrealized PnL, with the text:
  "The bot would normally exit through its ladder. Closing now overrides that. If the signal is
  still on, tomorrow's run may re-enter."
- Disabled without a token, with the tooltip we already use for token-gated actions.
- On success: toast, refresh positions, the activity feed will already show the row.

## 3. Funding — per position and total

- `GET /api/positions` rows carry `funding_paid` (negative = paid).
- `GET /api/account` carries `funding_paid` (total).
- `GET /api/funding` carries `total_paid`, `by_symbol`, `interval_hours`, `next_settlement_utc`,
  `recent` (last settlements) and `rates` `{symbol: {rate, annualized_pct}}`.

Requirements:
- **Positions table**: a `Funding` column next to Fees, coloured (rose when paid, mint when
  received).
- **Portfolio left column**: a `Funding paid` row under `Fees paid`.
- **Trade page → Account tab**: a Funding block with the total, the next settlement countdown, and
  the live annualized rate per market (e.g. `BTC 7.8 %/yr`, `ADA 11.0 %/yr`) — the rate is what
  tells the user whether holding is expensive right now.
- **New card in Portfolio or Risk**: "Funding cost", total since inception plus a small bar per
  market. Copy: "Perpetuals charge funding every 8 h. A long position pays when the rate is
  positive. Measured on Binance over 166 days, longs paid 1.2–3.2 %/yr of notional."

## 4. Risk profile — the honest answer to "more leverage"

`GET /api/risk/profiles` returns:

```json
{"current": "balanced", "equity": 1009.64, "validated_target_vol_range": [0.10, 0.30],
 "profiles": [{"profile": "conservative", "validated": true, "target_vol": 0.10,
               "expected_cagr": 0.051, "expected_vol": 0.028, "expected_max_dd": 0.042,
               "sharpe": 1.78, "expected_year_usd": 51.49, "expected_worst_drawdown_usd": 42.40,
               "limits": {"max_drawdown_pct": 0.06, "max_daily_loss_pct": 0.01, "max_weekly_loss_pct": 0.03},
               "note": "..."}, ...],
 "current_values": {...}}
```

`POST /api/risk/profile` body `{"profile": "aggressive"}`, token-gated.

Requirements:
- **Risk page, top**: a three-card selector (Conservative / Balanced / Aggressive) showing, for the
  CURRENT account size: expected €/year, expected worst drawdown in €, target volatility, and the
  three loss limits. The selected one is highlighted; `custom` shows as a fourth read-only state.
- The Sharpe of all three is ~1.77: show it once, with the line **"Same strategy, same edge. More
  risk does not mean better, it means bigger in both directions."**
- Applying a profile asks for confirmation and states that the new target volatility takes effect
  at the next daily run (00:05 UTC).
- Above 0.30 target volatility is NOT validated: if `current` is `custom` and target vol is outside
  `validated_target_vol_range`, show an amber warning "outside the validated range".
- Do NOT present this as leverage. The word to use is **risk level** or **target volatility**.

## 5. Acceptance

Same as v2.16: `npx tsc -b`, `npm run lint`, `npm run build:web` clean;
`py -3.12 scripts/ui_contrast_audit.py <base> --width 1440` and `--width 390 --height 844` both
`TOTAL offenders: 0`; Playwright screenshots of `/trading`, `/portfolio`, `/risk` at both sizes with
0 overflow and 0 console errors; and the numbers on screen compared against the API (at least the
ladder of one position, one funding figure and the three profile cards).
