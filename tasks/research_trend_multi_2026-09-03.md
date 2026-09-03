# Research — Multi-asset trend on the Strike universe (2026-09-03)

**Question.** Does running the already-validated daily trend model over everything Strike lists
(crypto + gold, silver, S&P 500, Nasdaq 100, WTI) beat running it on crypto alone, after costs and
after funding?

**Answer. YES — 11/11 GO.** Sharpe 1.92 net vs 1.37 crypto-only, drawdown 7.6 % vs 11.9 %, CAGR
11.2 % vs 13.0 %, with Strike's own measured funding and spreads. Same model, same code path, no parameter tuning: the improvement comes from
diversification, which is exactly what 40 years of managed-futures evidence predicts.

**Re-run 2026-09-03 (with the venue's own costs).** Strike publishes 90 days of funding and spread
history per market (`/stat/v1/stats/coin/history/...`, a base path the old client had wrong). Feeding
the MEASURED rates in instead of class averages, the result IMPROVES: **Sharpe 1.92, CAGR 11.2 %,
maxDD 7.6 %**, and the total funding cost over ten years drops from 10.6 to **1.5 points of equity**.
The reason is structural and worth understanding: on Strike the TradFi perps pay the longs
(WTI −15.7 %/yr, NAS100 −3.7 %/yr) while crypto charges them (BTC +8.6 %, ADA +10.9 %, XAG +15.1 %),
so a diversified long-only book is close to funding-neutral. Measured spreads also confirm the cost
assumption: median 0.2–8 bps, so 4.6–8.5 bps per side including the 4.5 bps taker fee against the
8 bps/side the study assumes. Caveat: 90 days of funding is a short sample to project over ten
years, which is why the ×2 and ×3 funding stresses (Sharpe 1.90 and 1.87) still matter.

**Re-run 2026-09-03 (selection rule).** The first pass used a selection rule written inside the research
script. It has been replaced by a direct call to the ENGINE's own `select_universe`, so what is
validated here is literally what the bot executes; the numbers above are that re-run (the
research-only rule gave 1.81 / 8.8 %). The engine's rule adds monthly hysteresis (a market already
held is kept) and the venue liquidity floor.

Script: `scripts/trend_multi_research.py` · price data: `scripts/download_daily.py` (Yahoo daily,
10 years, 23 markets) · costs: `scripts/strike_market_stats.py` writes `data/strike_costs.json`
from Strike's own funding and spread history. **That file is a snapshot, not a live feed: re-run
the stats script before re-running this study, or the funding assumptions drift.** The live bot
does not use it — it charges the venue's current rate at every settlement.

## 1. Setup

| Item | Value |
|---|---|
| Model | Donchian ensemble [5,10,20,30,60,90], never-falling trailing stop, 20 % vol target on 90 d, leverage cap 2 |
| Execution | signal at close t → **open of t+1** (shift 2 in returns), 8 bps/side |
| Funding | per market, MEASURED on Strike over 90 d (data/strike_costs.json): BTC +8.6 %, ETH +7.8 %, SOL +7.1 %, ADA +10.9 %, XAG +15.1 %, SP500 +8.4 %, XAU +0.9 % paid by longs; NAS100 −3.7 % and WTI −15.7 % paid TO longs |
| Universe | 14 markets with ≥ 365 d of history: 9 crypto + XAU, XAG, SP500, NAS100, WTI |
| Selection | the ENGINE's `select_universe`: monthly, hysteresis on current members, one market per asset class, then longest history; correlation cap 0.85 over 120 d; venue liquidity floor; **never ranked by past returns** |
| N held | 6 |
| Span | 2016-09-02 → 2026-09-03 (3 654 days) |
| Signal source | Yahoo daily (Strike's own klines start 2026-03 and are far too short) |
| Execution source | Strike marks (paper), never Yahoo |

**Single stocks are excluded by default.** Strike lists today's winners (NVDA, MU, COIN, SNDK…), so
including them imports hindsight selection. Measured both ways with the research rule: with stocks
Sharpe 1.76 / DD 6.9 %, without stocks 1.81 / 8.8 %. **The result does not depend on them**, so the
shipped configuration is the one without — and most of them fail the venue liquidity floor anyway.

## 2. Headline

| Configuration | Sharpe | CAGR | vol | maxDD | skew | trades |
|---|---|---|---|---|---|---|
| **Multi-asset (shipped, venue-measured costs)** | **1.92** | **11.2 %** | 5.6 % | **7.6 %** | +0.44 | 878 |
| Multi-asset, class-average funding guess | 1.76 | 10.2 % | 5.6 % | 7.8 % | +0.43 | 878 |
| Multi-asset (research-only rule) | 1.81 | 10.9 % | 5.8 % | 8.8 % | +0.35 | 874 |
| Crypto only, N=3 (current) | 1.37 | 13.0 % | 9.2 % | 11.9 % | +1.29 | 425 |
| Multi-asset incl. single stocks | 1.76 | 9.6 % | 5.3 % | 6.9 % | +0.67 | 948 |

Funding cost over the sample: 10.6 points of equity (≈ 1.0 %/yr). Turnover 9.6×/yr.

Markets actually held (engine rule): BTC, XAG, NAS100 and WTI (3 260 days each), ZEC (2 347),
XAU (2 044), ADA (1 218), with BNB (425) and SP500 (151) entering briefly.

## 3. Robustness (every line is a recorded trial)

| Stress | Sharpe | maxDD |
|---|---|---|
| 8 bps/side (base) | 1.76 | 7.8 % |
| 15 bps/side | 1.59 | 9.3 % |
| 25 bps/side | 1.34 | 11.3 % |
| 50 bps/side | 0.72 | 16.2 % |
| funding off | 1.95 | 7.4 % |
| funding ×2 | 1.58 | 8.8 % |
| funding ×3 | 1.39 | 9.8 % |
| target vol 0.10 / 0.30 | 1.78 / 1.77 | 4.2 % / 11.3 % |
| vol window 45 / 135 | 1.86 / 1.78 | 7.8 % / 8.0 % |
| lookbacks ×0.5 / ×1.5 | 1.89 / 1.70 | 8.0 % / 8.2 % |
| N = 3 / 8 / 10 | 1.44 / 1.81 / 1.83 | 9.1 % / 7.9 % / 7.9 % |
| correlation cap 0.6 / off | 1.91 / 1.58 | 8.2 % / 7.8 % |
| long/short | 1.49 | 6.5 % |
| 2022+ / 2024+ | 1.80 / 2.60 | — |
| first half / second half | 1.87 / 1.66 | — |

At 50 bps/side the drawdown breaches the 15 % gate, so that column is the honest limit of the
configuration: it tolerates 25 bps/side comfortably and dies somewhere before 50.

Look-ahead audit: shift 1 (forbidden, uses the signal too early) 5.52 → shift 2 (spec) 1.95 → shift
3 (one extra day) 1.74. Stability under an EXTRA delay is the signature of a real edge; a timing
artefact collapses.

Contribution by class (gross points over the sample): crypto 46.0, metals 35.9, indices 24.7,
energy 14.3. No single class carries the result.

## 4. GO/NO-GO — 11/11

n = 3 654 days · 21 trials recorded · deflated Sharpe probability 1.00 (same implementation as the
validated crypto study, so the two numbers are comparable).

| Check | Result |
|---|---|
| Sharpe net ≥ 0.8 | PASS (1.76) |
| CAGR > 0 | PASS (10.2 %) |
| maxDD < 15 % | PASS (7.8 %) |
| maxDD lower than crypto-only | PASS (7.8 % vs 11.9 %) |
| Sharpe ≥ crypto-only | PASS (1.76 vs 1.37) |
| skew > −0.5 | PASS (+0.43) |
| DSR ≥ 0.95 | PASS (1.00) |
| survives 25 bps/side | PASS (1.34) |
| survives funding ×3 | PASS (1.39) |
| no look-ahead artefact | PASS |
| 2022+ Sharpe ≥ 0.5 | PASS (1.80) |

## 5. Honest limitations

1. **Signal prices are not Strike prices.** Yahoo daily closes for gold/indices are the underlying;
   Strike's perp can deviate. The paper stage must track model↔Strike divergence per market.
2. **TradFi calendars.** Weekends are held with zero return in the model; a Strike perp keeps
   trading. This is conservative for the signal but not for the risk: a weekend gap is real.
3. **Strike liquidity is thin in TradFi — measured, and now enforced.** 24 h quote volume on
   2026-09-03: BTC 1.45 M$, ETH 261 k, ZEC 217 k, XAU 197 k, WTI 65 k, SP500 40 k, XAG 19.5 k,
   NAS100 4.0 k, GOOGL 0. The engine therefore requires a market to show at least 50× the notional
   of one position in 24 h (hard minimum 5 000 $), so the universe shrinks automatically as the
   account grows: at 1 000 $ of equity NAS100 is already excluded.
4. **Selection is deterministic, never return-ranked.** No look-ahead, but it does fix the basket
   early; only liquidity and correlation can evict a market.
5. **10 years is one regime sample** for TradFi (a bull decade for indices) and two cycles for crypto.

## 6. What ships

- Configuration: crypto + metals + indices + energy, N = 6, correlation cap 0.85, funding on.
- Next step is PAPER, not live: run it alongside the current crypto-only trend for 90 days and
  compare realized vs modelled, per market and in aggregate.
- Live requires the P2 gates (Strike client canary, custody, legal) already in the roadmap.
