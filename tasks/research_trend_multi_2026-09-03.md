# Research — Multi-asset trend on the Strike universe (2026-09-03)

**Question.** Does running the already-validated daily trend model over everything Strike lists
(crypto + gold, silver, S&P 500, Nasdaq 100, WTI) beat running it on crypto alone, after costs and
after funding?

**Answer. YES — 11/11 GO.** Sharpe 1.81 net vs 1.37 crypto-only, drawdown 8.8 % vs 11.9 %, CAGR
10.9 % vs 13.0 %. Same model, same code path, no parameter tuning: the improvement comes from
diversification, which is exactly what 40 years of managed-futures evidence predicts.

Script: `scripts/trend_multi_research.py` · data: `scripts/download_daily.py` (Yahoo daily, 10 years,
23 markets) · funding: measured on Binance (166 days) and applied as a cost.

## 1. Setup

| Item | Value |
|---|---|
| Model | Donchian ensemble [5,10,20,30,60,90], never-falling trailing stop, 20 % vol target on 90 d, leverage cap 2 |
| Execution | signal at close t → **open of t+1** (shift 2 in returns), 8 bps/side |
| Funding | 3 %/yr crypto, 4 %/yr TradFi on long exposure (crypto figure measured on Binance) |
| Universe | 14 markets with ≥ 365 d of history: 9 crypto + XAU, XAG, SP500, NAS100, WTI |
| Selection | monthly, one market per asset class first, then longest history; correlation cap 0.85 over 120 d; **never ranked by past returns** |
| N held | 6 |
| Span | 2016-09-02 → 2026-09-03 (3 654 days) |
| Signal source | Yahoo daily (Strike's own klines start 2026-03 and are far too short) |
| Execution source | Strike marks (paper), never Yahoo |

**Single stocks are excluded by default.** Strike lists today's winners (NVDA, MU, COIN, SNDK…), so
including them imports hindsight selection. Measured both ways: with stocks Sharpe 1.76 / DD 6.9 %,
without stocks Sharpe 1.81 / DD 8.8 %. **The result does not depend on them**, so the shipped
configuration is the one without.

## 2. Headline

| Configuration | Sharpe | CAGR | vol | maxDD | skew | trades |
|---|---|---|---|---|---|---|
| **Multi-asset (shipped)** | **1.81** | **10.9 %** | 5.8 % | **8.8 %** | +0.35 | 874 |
| Crypto only, N=3 (current) | 1.37 | 13.0 % | 9.2 % | 11.9 % | +1.29 | 425 |
| Multi-asset incl. single stocks | 1.76 | 9.6 % | 5.3 % | 6.9 % | +0.67 | 948 |

Funding cost over the sample: 11.8 points of equity (≈ 1.1 %/yr). Turnover 10.8×/yr.

Markets actually held: BTC and XAG and NAS100 and WTI (3 260 days each), XAU (2 683), ADA (2 533),
with BNB, SP500 and ZEC entering briefly.

## 3. Robustness (every line is a recorded trial)

| Stress | Sharpe |
|---|---|
| 8 bps/side (base) | 1.81 |
| 15 bps/side | 1.65 |
| 25 bps/side | 1.42 |
| 50 bps/side | 0.85 |
| funding off | 1.99 |
| funding ×2 | 1.63 |
| funding ×3 | 1.46 |
| target vol 0.10 / 0.30 | 1.81 / 1.80 |
| vol window 45 / 135 | 1.90 / 1.84 |
| lookbacks ×0.5 / ×1.5 | 1.95 / 1.71 |
| N = 3 / 8 / 10 | 1.52 / 2.05 / 2.09 |
| correlation cap 0.6 / off | 1.84 / 1.80 |
| long/short | 1.44 |
| 2022+ / 2024+ | 1.71 / 2.20 |
| first half / second half | 1.88 / 1.67 |

Look-ahead audit: shift 1 (forbidden, uses the signal too early) 5.49 → shift 2 (spec) 1.93 → shift
3 (one extra day) 1.68. Stability under an EXTRA delay is the signature of a real edge; a timing
artefact collapses.

Contribution by class (gross points over the sample): crypto 35.1, metals 44.9, indices 24.5,
energy 14.4. No single class carries the result.

## 4. GO/NO-GO — 11/11

n = 3 654 days · 21 trials recorded · deflated Sharpe probability 1.00 (same implementation as the
validated crypto study, so the two numbers are comparable).

| Check | Result |
|---|---|
| Sharpe net ≥ 0.8 | PASS (1.81) |
| CAGR > 0 | PASS (10.9 %) |
| maxDD < 15 % | PASS (8.8 %) |
| maxDD lower than crypto-only | PASS (8.8 % vs 11.9 %) |
| Sharpe ≥ crypto-only | PASS (1.81 vs 1.37) |
| skew > −0.5 | PASS (+0.35) |
| DSR ≥ 0.95 | PASS (1.00) |
| survives 25 bps/side | PASS (1.42) |
| survives funding ×3 | PASS (1.46) |
| no look-ahead artefact | PASS |
| 2022+ Sharpe ≥ 0.5 | PASS (1.71) |

## 5. Honest limitations

1. **Signal prices are not Strike prices.** Yahoo daily closes for gold/indices are the underlying;
   Strike's perp can deviate. The paper stage must track model↔Strike divergence per market.
2. **TradFi calendars.** Weekends are held with zero return in the model; a Strike perp keeps
   trading. This is conservative for the signal but not for the risk: a weekend gap is real.
3. **Strike liquidity is thin in TradFi.** Measured 24 h quote volume: BTC 1.09 M $, ADA 658 k,
   ETH 388 k, SOL 360 k, XAU 199 k, WTI 151 k, SP500 80 k, and single stocks as low as 94 $ (GOOGL).
   The live universe must carry a hard liquidity floor and cap position size against depth.
4. **Selection rule is deterministic, not liquidity-ranked.** It never uses past returns (no
   look-ahead) but it does fix the basket early; the live version should rank by Strike liquidity.
5. **10 years is one regime sample** for TradFi (a bull decade for indices) and two cycles for crypto.

## 6. What ships

- Configuration: crypto + metals + indices + energy, N = 6, correlation cap 0.85, funding on.
- Next step is PAPER, not live: run it alongside the current crypto-only trend for 90 days and
  compare realized vs modelled, per market and in aggregate.
- Live requires the P2 gates (Strike client canary, custody, legal) already in the roadmap.
