# Research — RSI divergence strategy (2026-09-02)

**Question.** Does a classic RSI-divergence setup, with an objective verifier and a
precise entry trigger, carry an edge on our universe after costs?

**Answer. NO on 1h (2/7 GO/NO-GO), NEUTRAL on 4h (PF 1.00, t +0.44).** The strategy
ships in v2.15 fully wired (engine + UI + config) but **disabled** (`allocation_divergence = 0`).
Enabling it is a research decision, not a click; the UI shows this verdict next to the switch.

Script: `scripts/divergence_research.py` (`--extra` for the with-trend / 4h variants).
Data: `scripts/download_1h.py` → Binance spot 1h klines, 6 symbols × 40,932 bars each
(2022-01 → 2026-09, 4.7 years): BTC, ETH, SOL, ADA, BNB, XRP.

## 1. Definition (what "a divergence" means in the code)

A signal needs three independent parts, and each is objective:

| Part | Rule | Why |
|---|---|---|
| **Pivot** | Bar low/high that is strictly the extreme of `k=3` bars on each side. A pivot is only *known* `k` bars later (no repainting). | Divergence indicators on charts repaint; ours cannot. |
| **Divergence (verifier)** | Two consecutive pivot lows 5–60 bars apart. Regular bull: price L2 < L1 **and** RSI14(L2) > RSI14(L1) by ≥ 3 points, with RSI(L1) < 35 (extreme zone). Mirror for bearish (RSI > 65). Hidden = the opposite price/RSI order, only with the EMA200 trend. | The RSI gap and the extreme-zone filter reject "divergences" that are just noise. |
| **Trigger (entry point)** | Within 6 bars of confirmation, a bar must **close beyond the high (bull) / low (bear) of the L2 pivot bar** (structure break) and the MACD histogram must be rising (bull) / falling (bear). Entry at the **next bar open**. | A divergence is a condition, not a signal; the break is what says the pullback is over. |
| Stop / target | Stop = pivot ∓ 0.5 × ATR14; target = 2R; time stop 48 bars (1h) / 24 bars (4h). | Objective, and the same rule the simulator uses live. |

Costs: 8 bps per side (taker + slippage, our paper assumption), plus stress runs at 15 and 25 bps.

## 2. Results — 1h base (regular divergences, structure break + MACD, 2R)

| Set | n | per yr | WR | avg R | PF | exp bps | t | Sharpe | maxDD | stops |
|---|---|---|---|---|---|---|---|---|---|---|
| **POOLED** | 1102 | 237 | 38.2% | −0.124 | **0.77** | −41.4 | **−2.15** | −1.38 | 78.3% | 52% |
| BTC | 194 | 42 | 37.6% | −0.130 | 0.64 | −45.9 | −1.64 | −0.73 | 33.2% | 48% |
| ETH | 180 | 39 | 37.2% | −0.117 | 0.84 | −26.5 | −0.36 | −0.61 | 22.9% | 54% |
| SOL | 206 | 44 | 35.0% | −0.181 | 0.70 | −68.3 | −1.62 | −1.00 | 34.7% | 59% |
| ADA | 191 | 41 | 35.6% | −0.207 | 0.80 | −39.7 | −0.71 | −1.21 | 33.7% | 54% |
| BNB | 178 | 38 | 40.4% | −0.063 | 0.81 | −25.5 | −0.41 | −0.34 | 23.2% | 48% |
| XRP | 153 | 33 | 45.1% | −0.015 | 0.80 | −37.4 | −0.63 | −0.08 | 13.6% | 45% |

Gross expectancy (before costs) is already negative: −25 bps per trade pooled. This is not a
cost problem; the setup itself loses. Every symbol loses. Every year loses (PF 0.59 / 0.93 /
0.77 / 0.84 / 0.94 for 2022–2026). Longs and shorts lose equally (PF 0.77 / 0.76).

### Look-ahead audit
Filling at the signal-bar close (forbidden) gives PF 0.77, t −2.07; filling at the next open
(what we ship) gives PF 0.77, t −2.15. No artefact: the result does not depend on peeking.

## 3. Variants (14 recorded trials; none rescues it on 1h)

| Variant | n | PF | exp bps | t | Sharpe | maxDD |
|---|---|---|---|---|---|---|
| no MACD confirmation | 1105 | 0.77 | −40.8 | −2.10 | −1.34 | 78% |
| RSI 21 | 569 | 0.69 | −59.6 | −2.53 | −1.33 | 62% |
| pivot k=5 | 1078 | 0.88 | −20.4 | −0.36 | −0.85 | 66% |
| rr 1.5 | 1109 | 0.76 | −41.3 | −2.27 | −1.48 | 79% |
| rr 3 | 1091 | 0.78 | −39.9 | −1.95 | −1.11 | 76% |
| extreme zone 30/70 | 743 | 0.70 | −56.1 | −2.75 | −1.26 | 68% |
| no extreme-zone filter | 1648 | 0.78 | −37.9 | −2.40 | −1.53 | 87% |
| min RSI gap 6 | 671 | 0.70 | −54.7 | −2.56 | −1.38 | 65% |
| trigger window 12 | 1192 | 0.77 | −40.6 | −2.18 | −1.40 | 81% |
| max hold 96 | 1078 | 0.83 | −33.4 | −1.25 | −1.31 | 79% |
| HIDDEN (continuation, EMA200) | 1346 | 0.91 | −13.1 | +0.29 | −0.17 | 48% |
| costs 15 bps/side | 1102 | 0.70 | −55.4 | −2.15 | −2.01 | 88% |
| costs 25 bps/side | 1102 | 0.62 | −75.4 | −2.15 | −2.87 | 95% |

### Higher timeframe / with-trend (`--extra`)

| Variant | n | per yr | WR | PF | exp bps | t | Sharpe | maxDD |
|---|---|---|---|---|---|---|---|---|
| 1h, with-trend (EMA200 direction) | 74 | 16 | 37.8% | 0.97 | −5.8 | +0.21 | 0.03 | 14.5% |
| **4h, base** | 308 | 66 | 46.1% | **1.00** | −0.9 | +0.44 | 0.08 | 14.5% |
| 4h, with-trend | 13 | 4 | 69.2% | 3.28 | +261 | +1.81 | 0.66 | 2.5% |
| 4h, pivot k=5 | 282 | 61 | 45.7% | 0.97 | −7.4 | +0.23 | −0.09 | 27% |

The 4h/with-trend line looks great and means nothing: 13 trades in 4.7 years. With 14 trials
recorded, a t of 1.8 on n=13 is exactly what data mining produces. It is **not** evidence.

## 4. GO/NO-GO (research §4.4 checklist, adapted)

| Check | 1h base | 4h base |
|---|---|---|
| n ≥ 300 pooled | PASS (1102) | PASS (308) |
| PF net ≥ 1.2 | FAIL (0.77) | FAIL (1.00) |
| t ≥ 2 | FAIL (−2.15) | FAIL (+0.44) |
| Sharpe ≥ 0.8 | FAIL (−1.38) | FAIL (0.08) |
| maxDD < 15% | FAIL (78%) | PASS (14.5%) |
| no look-ahead artefact | PASS | PASS |
| PF > 1 at 15 bps/side | FAIL | FAIL |
| **Verdict** | **NO-GO 2/7** | **NO-GO 3/7** |

## 5. What ships and why

- `strategies/divergence.py` implements exactly the rules above on live data: seeded from
  Binance 4h klines (499 closed bars), then aggregated from the 1-minute stream; pivots are
  confirmed `k` bars late (no repainting); candidate → trigger → next evaluation → signal;
  SL/TP/time stop as in research. Every signal carries `pivots`, `rsi_gap`, `trigger_level`,
  `macd_hist`, `bars_to_trigger` so the UI can show *why* it fired.
- Defaults are the least-bad configuration (4h, regular, 24-bar time stop). `div_with_trend`
  exists as a switch but is off: 13 trades is not evidence.
- Allocation 0 by default. The Strategies page shows this verdict and the checklist next to
  the switch; the engine skips seeding for disabled strategies.
- Kill rule if someone enables it in paper: the edge monitor (t-stat / fee-share) applies to
  DIVERGENCE like any other strategy.

## 6. What would change the verdict

1. A verifier that is *not* RSI-shaped: the hidden-divergence line (PF 0.91, t +0.3) is the
   only one in the family with non-negative gross expectancy. Worth one more trial with
   volume-at-pivot and a 4h EMA200 filter, on a fresh out-of-sample window, before touching it.
2. ≥ 100 4h with-trend trades. At 4 per year that means adding symbols, not waiting.
3. Anything below t = 2 after that stays disabled. The bot is not short of untested ideas;
   it is short of proven ones.
