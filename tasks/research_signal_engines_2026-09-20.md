# Research — is there a better trend engine than the Donchian ensemble? (2026-09-20)

Edgar asked for "another strategy with moving averages or liquidity indicators mixed with others, for
perfect entries and exits with the trend". This note is the answer, built the only way a quant desk
accepts: the candidates were run on the SAME harness as the book (same markets, universe rule,
sizing, band and costs) and judged in and out of sample. Scripts: `/tmp/bs/strat_research.py`,
`/tmp/bs/strat_equalrisk.py` (Edgar's PC, Git Bash /tmp); data `data/binance_daily/*.parquet`.

## What the best in the world actually do (and say)

- **Trend signals are one family.** Levine & Pedersen (AQR, FAJ 2016, *Which Trend Is Your Friend?*)
  prove that moving-average crossovers and time-series momentum are equivalent representations of the
  same linear filter — the choice of "indicator" is a choice of weights over past returns, not a new
  source of alpha. Hurst, Ooi & Pedersen (*A Century of Evidence on Trend-Following*, 1880-2016, 67
  markets): the return comes from being in the trend with **volatility-scaled** positions across many
  markets; per-market Sharpe ≈ 0.4, the portfolio is what makes it ~1.
- **Ensembles of speeds, continuous forecasts, capped, vol-targeted** — Carver's EWMAC 8/32 … 64/256
  with forecasts scaled to |10| and capped at 20 — is the CTA production standard. The book already
  runs an ensemble of five Donchian speeds with vol targeting: same architecture, breakout flavour.
- **Turning points are the Achilles' heel** (Garg, Goulding, Harvey & Mazzoleni, *Momentum Turning
  Points*, JFE 2023): slow signals react late, fast ones give false alarms; blending speeds beats both.
  The book blends 10-90 d already.
- **Volume / liquidity as a trend signal**: no robust evidence in futures or crypto daily trend; the
  literature that improves TSMOM does it with sizing (vol scaling), speed blending or, at best, a
  short-horizon mean-reversion timing of entries — not with volume confirmation.

## Candidates (per-market position fraction in [0,1]; everything else identical)

| engine | definition |
|---|---|
| LIVE | Donchian ensemble 10/20/30/60/90, binary legs, never-falling mid stop (what runs) |
| EWMAC | Carver: (EMA_f − EMA_s) / price-vol for 8/32, 16/64, 32/128, 64/256; scaled to |f|≈10, cap 20, long-only f⁺/20, averaged |
| TSMOM | share of {21, 63, 126, 252}-day returns > 0 |
| TURN | Harvey cycles: slow 252 d / fast 21 d → Bull 1 · Correction 0.5 · Rebound 0.5 · Bear 0 |
| MAX | EMA 20/100 crossover, binary |
| MAXVOL | the same crossover, entry only if 20 d volume > 100 d median ("liquidity confirmation") |
| DAMPED | LIVE × TURN multiplier (dynamic-speed overlay) |
| COMBO | (LIVE + EWMAC) / 2 (signal diversification) |

Common: 9 markets that clear Strike's floor, 2018-06 → 2026-09-19, monthly universe (n 8, class
diversity, corr cap 0.85), vol target 0.45 / σ90 cap 3, 1/N, 20 % band, open-to-open, 9.9 bps per
unit of turnover; IS = to 2023-12, OOS = 2024-01 →.

## Results

| engine | CAGR | vol | Sharpe | MaxDD | turn/y | IS Sh | OOS Sh | OOS DD | Sh @25 bps | corr LIVE |
|---|---|---|---|---|---|---|---|---|---|---|
| **LIVE** | 21.0 % | 13.7 % | **1.46** | −19.7 % | 12.8 | **1.16** | **2.09** | −5.9 % | 1.32 | 1.00 |
| EWMAC | 12.4 % | 9.5 % | 1.28 | **−12.8 %** | **3.5** | 0.96 | 1.90 | −6.1 % | 1.23 | 0.87 |
| TSMOM | 22.3 % | 17.6 % | 1.23 | −28.0 % | 15.8 | 0.95 | 1.80 | −9.8 % | 1.10 | 0.89 |
| TURN | 21.2 % | 17.3 % | 1.20 | −29.2 % | 19.8 | 0.89 | 1.78 | −9.4 % | 1.02 | 0.89 |
| MAX | 24.7 % | 19.5 % | 1.23 | −20.4 % | 4.7 | 0.99 | 1.72 | −14.7 % | 1.19 | 0.83 |
| MAXVOL | 19.9 % | 17.6 % | **1.12** | −23.6 % | 2.8 | 0.95 | **1.46** | −15.4 % | 1.09 | 0.78 |
| DAMPED | 18.0 % | 12.1 % | 1.42 | −17.9 % | 13.7 | 1.09 | 2.08 | −6.4 % | 1.25 | 0.97 |
| COMBO | 16.5 % | 11.1 % | 1.43 | −15.7 % | 7.2 | 1.12 | 2.06 | −5.9 % | **1.33** | 0.97 |

COMBO at the SAME realised risk as LIVE (target vol 0.555): CAGR 20.0 % · vol 13.4 % · Sharpe 1.43 ·
MaxDD −19.0 % · turnover 8.5/y · Sh @25 bps 1.33. Year by year COMBO wins 3/9; the daily difference has
t = −0.81 (indistinguishable).

## Verdict

1. **No candidate beats the engine that runs.** The Donchian ensemble has the best Sharpe in sample
   (1.16) and out of sample (2.09). This is not luck: it is the CTA architecture (speed ensemble +
   vol targeting + diversification) in breakout form, validated 11/11.
2. **"Liquidity / volume confirmation" is the worst engine tested** (Sharpe 1.12, OOS 1.46): a volume
   filter delays entries past the start of the move — exactly where a trend strategy earns its
   payoff — and the literature agrees. Order-flow / liquidity alpha lives intraday, a different horizon
   and a different book (retired here for that reason).
3. **The only defensible improvement is diversification of the signal, not a new one**: COMBO gives the
   same Sharpe and drawdown at equal risk with **a third less turnover** (8.5 vs 12.8/y). Statistically
   indistinguishable in return; cheaper in fees and whipsaw. Keep it as the option for when turnover
   becomes the binding constraint (larger account, live slippage). Not switched today: the book is one
   day into a validated profile change and a switch needs its own 11 gates.
4. "Perfect entries and exits" is not a thing the evidence supports anywhere: the payoff of trend comes
   from the right tail (5 % of trades = 110 % of profit, measured 2026-09-20), and every attempt to
   make entries or exits "sharper" (take-profit, chandelier, volume filter) reduced it.

## Sources

- Levine & Pedersen, *Which Trend Is Your Friend?*, Financial Analysts Journal 72(3), 2016 —
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2603731
- Hurst, Ooi & Pedersen, *A Century of Evidence on Trend-Following Investing*, 2017 —
  https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing
- Garg, Goulding, Harvey & Mazzoleni, *Momentum Turning Points*, J. Financial Economics 149(3), 2023 —
  https://people.duke.edu/~charvey/Research/Published_Papers/P158_Momentum_turning_points.pdf
- Carver, EWMAC forecast scaling and rule combination — https://qoppac.blogspot.com/2025/06/quickies-1-overfitting-and-ewmac.html
- Baltas & Kosowski, *Time series momentum and volatility scaling* — https://www.researchgate.net/publication/303846490_Time_series_momentum_and_volatility_scaling

---

# Part 2 — a SECOND book next to the trend: is any of them feasible? (2026-09-20, later)

Edgar: "so it is not feasible — which one would be, apart from the one running?" A second strategy
only earns its place if it is (a) positive on its own, (b) nearly uncorrelated with the trend book,
(c) stable across trivially different specifications, (d) tradeable on the venue. Crypto perps only
(Strike's TradFi funding history is five months); Binance USDT-M funding every 8 h 2019/20 → today
for 7 markets; the trend book's sizing; open-to-open; 9.9 bps/turnover; and the FUNDING CASH FLOW of
every position (a long pays, a short receives). Scripts `/tmp/bs/carry_research.py`, `xs_variants.py`,
`near_test.py`.

| book | CAGR | vol | Sharpe | MaxDD | turn/y | IS Sh | OOS Sh | corr trend |
|---|---|---|---|---|---|---|---|---|
| TREND (crypto only, with funding) | 27.2 % | 18.1 % | 1.42 | −22.5 % | 9.0 | 1.43 | 1.40 | 1.00 |
| CARRY long/short (funding carry) | −14.6 % | 18.3 % | **−0.77** | −67.1 % | 8.7 | −0.90 | −0.56 | **−0.81** |
| CARRY long-only | 4.1 % | 4.6 % | 0.90 | −5.5 % | 2.9 | 1.18 | 0.39 | 0.21 |
| XS momentum L/S (90 d, daily) | 12.1 % | 11.9 % | 1.02 | −14.7 % | 22.3 | 1.30 | 0.69 | **0.10** |
| XS momentum long-only tilt | 24.3 % | 18.3 % | 1.28 | −25.8 % | 10.6 | 1.43 | 1.07 | 0.72 |
| TREND + XS L/S (50/50 risk) | 23.6 % | 13.4 % | 1.65 | −14.5 % | | 1.83 | 1.40 | |

Cross-sectional momentum looked like the candidate (corr 0.10, blend Sharpe 1.65, MaxDD −14.5 %) —
until the specification was moved by one notch:

| XS variant | Sharpe | IS | OOS | blend OOS Sh | blend OOS DD |
|---|---|---|---|---|---|
| 90 d, daily rebalance | 1.02 | 1.30 | 0.69 | 1.40 | −10.9 % |
| 90 d, monthly | 0.17 | 0.53 | −0.28 | 0.85 | −11.6 % |
| 60 d, monthly | −0.17 | 0.00 | −0.38 | 0.75 | −12.3 % |
| 120 d, monthly | 0.47 | 0.66 | 0.21 | 1.24 | −10.0 % |
| 180 d, monthly | 0.07 | 0.08 | 0.07 | 1.09 | −9.1 % |
| 120 d, monthly, skip 5 d | 0.39 | 0.92 | −0.31 | 0.82 | −12.6 % |
| 120 d, monthly, top/bottom 2 | 0.50 | 0.58 | 0.40 | 1.41 | −7.2 % |

TREND alone OOS: Sharpe 1.40, MaxDD −15.5 %. No blend beats it out of sample in Sharpe; the drawdown
relief comes from diluting with a leg of ~zero mean (the same relief cash would give).

Breadth: adding NEAR (Strike's second most liquid crypto, 6 years of history) to the trend pool —
Sharpe 1.42 → 1.39 (n 8) / 1.43 (n 9): nothing. The diversification the book earned came from asset
CLASSES (metal, energy, index), and those are the venue's illiquid markets.

## Verdict

1. **Funding carry is not a book in crypto: it is anti-trend.** High positive funding coincides with
   strong uptrends, so "short when the longs pay" shorts the bull market (Sharpe −0.77, corr −0.81).
   Long-only carry is cash with a hobby (gross exposure 4 %).
2. **Cross-sectional momentum on 7 markets is not robust**: its Sharpe swings from 1.0 to −0.2 by
   changing the rebalance day or the lookback by a month. That is the signature of noise on too few
   names; the literature runs it on dozens to hundreds. Revisit only when the venue offers ≥ 15-20
   liquid crypto with ≥ 2 years of history — today it offers ~8.
3. **Breadth with more of the same asset class adds nothing** (NEAR). Breadth by asset class would —
   and that is a venue question (liquid TradFi perps), not a strategy question.
4. **Therefore: no second strategy is defensible on this venue today.** The book's return will come
   from (a) the one validated engine, sized to survive (Balanced), (b) execution quality (basis
   guard, venue-price ladder, an exchange-side stop before real money), (c) breadth by asset class
   whenever the venue's liquidity allows it. Everything else tested today would have subtracted.

Sources for part 2: Koijen, Moskowitz, Pedersen & Vrugt, *Carry* (JFE 2018); Liu, Tsyvinski & Wu,
*Common Risk Factors in Cryptocurrency* (JF 2022) — momentum and size factors in crypto; the funding
data is Binance USDT-M `fapi/v1/fundingRate`.

---

# Part 3 — overlays, portfolio construction, and what a normal year looks like (2026-09-20, later)

Edgar: "keep testing almost indefinitely until you find a very powerful strategy; it cannot be that
only one works". The families a CTA desk layers OVER a trend engine, each with prior evidence, on the
same harness. Acceptance rule: Sharpe +0.10 and lower MaxDD, in IS and OOS, stable to neighbours.
Script `/tmp/bs/overlay_study.py`, `portfolio_study.py`, `mc_dd.py`.

## Overlays on the live engine

| overlay | CAGR | Sharpe | MaxDD | turn/y | IS Sh | OOS Sh | OOS DD |
|---|---|---|---|---|---|---|---|
| **BASE (live engine)** | 21.0 % | **1.46** | −19.7 % | 12.8 | 1.16 | **2.09** | −5.9 % |
| Vol-regime attenuation p80 ×0.5 / p90 ×0.5 / p80 ×0.7 | 17.5-18.8 % | 1.42-1.45 | −20.7…−21.5 % | 13 | 1.12-1.18 | 2.03-2.07 | −6.1…−6.7 % |
| Breakout only from vol compression k1.0 / k1.2 | 16.4 / 19.5 % | 1.41 / 1.44 | −21 % | 10-12 | 1.12 / 1.24 | 2.06 / 1.90 | −6.3 / −7.8 % |
| Pullback entry, max 3 / 5 / 10 d | 18.4 / 16.3 / 13.6 % | 1.38 / 1.31 / 1.22 | −17.9 / −17.1 / −17.9 % | 10-11 | 1.18 / 1.13 / 1.07 | 1.80 / 1.69 / 1.53 | −7.5…−7.8 % |
| BTC-200d regime gate for alts ×0.5 / ×0 | 20.7 / 20.4 % | 1.46 / 1.44 | −19.1 / −18.4 % | 12.5 | 1.17 / 1.18 | 2.05 / 1.99 | −6.8 / −7.7 % |
| Carry forecast blended in (Carver) w 0.15 / 0.30 | 15.1 / 11.0 % | 1.37 / 1.25 | −16.3 / −13.0 % | 11 | 0.99 / 0.82 | 2.09 / 2.01 | −5.8 / −5.9 % |
| Weekly Donchian 2/4/6/12/18 wk | 20.1 % | 1.28 | −23.8 % | 11.6 | 0.99 | 1.93 | −8.7 % |

None passes the acceptance rule. The closest (BTC gate ×0.5) is a wash: same Sharpe, 0.6 pp less
drawdown, slightly worse OOS. Pullback entries buy a lower drawdown with a lower Sharpe and a worse
OOS: the trend's payoff is at the START of the move, and waiting for a dip misses part of it.

## Portfolio construction (same engine, at EQUAL's realised vol)

| risk split | Sharpe | MaxDD | turn/y | IS | OOS |
|---|---|---|---|---|---|
| **1/N (live)** | **1.48** | −19.7 % | 13 | 1.18 | 2.09 |
| equal risk per asset class | 0.92 | −25.0 % | 15 | 0.67 | 1.36 |
| inverse volatility | 1.20 | −23.3 % | 19 | 0.82 | 1.92 |
| correlation-adjusted (Baltas-Kosowski CF) | 1.47 | −19.2 % | 22 | 1.17 | 2.06 |

## Is the live engine's edge a product of today's search?

Deflated Sharpe probability (Bailey & López de Prado) of the live engine — Sharpe 1.48, 2,954 days,
skew +0.46, kurtosis 13.5 — with 20 / 40 / 80 trials: **0.99 / 0.98 / 0.96**. It is not.

## What a normal year looks like (block bootstrap, 5,000 one-year paths, Balanced size)

- 1-year return: median **+19 %**, p10 −2 %, p90 +50 %; P(losing year) 13 %.
- Worst drawdown within a year: median −8.7 %, p90 −14 %; P(DD > 15 %) 7 %; **P(DD > 23 % = halt) 0.3 %**
  per year, 10 % over an 8-year path (median 8-year max DD −16.3 %).
- Worst single day within a year: median −3.6 %; the 8 % daily limit is never reached.

## Verdict of the day (≈ 50 configurations, five families)

Trend engines, exit rules, second books, portfolio construction and overlays: **nothing beats the
validated Donchian ensemble robustly**, and its deflated Sharpe says the edge is real. "Spectacular"
in a backtest is what a desk learns to distrust; what is spectacular here is a validated ~1.4-1.9
Sharpe engine whose normal year is +19 % with a 9 % drawdown. The levers that remain are not signals:
breadth by asset class (a venue question), execution quality, and time.

---

# Part 4 — high-frequency trading: is it possible for this bot? (2026-09-20, 17:10Z)

Edgar: "I want it to be a high-frequency strategy — there must be incredible studies proving it."
There are. They prove the opposite for anyone without microsecond infrastructure.

## What the studies say
- Aquilina, Budish & O'Neill (QJE 2022): latency-arbitrage races happen ~once a minute per stock,
  the modal race lasts **5-10 microseconds**, the top 6 firms win > 80 % of them; the prize is ~0.5 bps
  of volume, $2-8 bn/yr globally — a winner-take-all race for speed.
- Baron, Brogaard, Hagströmer & Kirilenko (JFQA 2019): HFT profits are concentrated in the fastest
  firms; a rank improvement in latency (colocation) improves performance; the slow lose.
- Makarov & Schoar (JFE 2020): crypto cross-exchange arbitrage was large in 2017 and is bounded by
  capital controls and venue frictions; the common component of signed volume drives 80 % of BTC
  returns — what looks like a price gap is usually a friction, not a free lunch.
- Tiniç & Sensoy; Rajendran & Singaravelu (crypto microstructure): adverse selection is the primary
  cost of market making in crypto; it is worst exactly during one-sided moves, when the maker keeps
  getting filled on the wrong side.

## What this bot measures (CT 104 → Strike, 2026-09-20 17:10Z)
- REST round trip **140-195 ms** (connect ~20 ms); WS tick age 0.22 s. The races above are won in
  5-10 µs: we are ~20,000× slower and always will be from a home LXC over Tailscale.
- BTC-USD book at Strike: spread 0.25 bps, top of book 0.018 BTC (~$1,500) each side, top-5 depth
  0.76 BTC (~$61k). A $300 order is fine; a market-making book is not.
- Costs: taker 4.5 bps + half spread 0.125 → **9.25 bps per round trip**; maker rebate −0.5 bps only
  if BOTH legs fill passively without adverse selection (the case that does not exist).

## The arithmetic on Edgar's own 1-minute data (160,785 BTC bars, 31-May → 20-Sep-2026)

| horizon | mean |move| | autocorr(1) | oracle net per trade (taker) |
|---|---|---|---|
| 1 min | 3.46 bps | 0.005 | **−5.8 bps** |
| 5 min | 7.89 bps | −0.034 | **−1.4 bps** |
| 15 min | 13.8 bps | −0.006 | +4.5 bps |
| 60 min | 27.5 bps | 0.018 | +18.3 bps |

A trader who knew the NEXT minute's direction with certainty loses 5.8 bps per trade after costs.
Realistic 1-15 min signals (sign of the last k bars, long/short, walk-forward): the gross edge flips
sign between IS and OOS for every k (momentum k=3: −39 IS / +43 OOS bps/day), i.e. there is no
signal; net as taker −800 to −3,700 bps/day. The "ideal maker" column (+100 to +500 bps/day) is the
mirage every retail HFT plan is built on: it assumes the rebate on both legs and zero adverse
selection — the money the 5-microsecond firms take.

## What this bot already found the hard way (its own retired intraday engines)
- Mean reversion: no gross edge over 2,284 trades (−0.90/−0.63/−2.05/+0.45 bps, SE 1.2-2.6); random
  entries perform the same and inverting every signal does not help.
- Fibonacci retracement: t = −2.6, PSR(0) = 0.005, bootstrap PnL entirely negative.
- Divergence (RSI + structure): widened to 30 markets, PF 1.11 → 1.01, +4.6 bps, t 0.95.

## Verdict
High-frequency trading is not a strategy family this bot can enter: by physics (150 ms vs 10 µs), by
economics (9.25 bps cost vs 3.5 bps of movement per minute), and by its own experiments. The one
"high-frequency" gain that is real and free: **execute the daily rebalance as a MAKER** (passive
orders, re-pegged, market fallback — `trend_live_*` already implements it for live) — 5-9 bps per side
× ~13 units of turnover a year ≈ +0.6 to +1.1 % of equity per year, with no new risk.

Sources: Aquilina, Budish & O'Neill — https://academic.oup.com/qje/article/137/1/493/6368348 ·
Budish, Cramton & Shim — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2388265 ·
Baron, Brogaard, Hagströmer & Kirilenko — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2433118 ·
Makarov & Schoar — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3171204 ·
Tiniç & Sensoy, *Adverse selection in cryptocurrency markets* — https://nottingham-repository.worktribe.com/OutputFile/40584797

---

# Part 5 — medium frequency (15 m – 4 h): the one thing that survived (2026-09-20, 22:00Z)

Edgar: "a strategy at 15 m / 30 m / 1 h / 4 h, or a market maker". Binance USDT-M perp 1 h bars
2020-01 → 2026-09 for the 7 crypto markets the venue trades (6 years, 52-59k bars each), taker
costs as the venue charges (4.5 bps + half spread per side, ~13 bps per round trip), 8 h funding cash
flows on every position, IS to 2023-12 / OOS 2024-01 →. Scripts `/tmp/bs/mf_study.py`,
`h4_blend.py`, `clock_test.py`, `clock_curve.py`, `clock_shift.py`.

**A first run of this study printed Sharpe 7-12 for intraday trend. That was a look-ahead bug in the
harness (the position was paid for the bar it had just observed). It is recorded here because the
lesson matters: any medium-frequency result that looks spectacular is a bug until proven otherwise.**

## Cost hurdle (mean |move| per bar, 7-market average, vs ~13 bps round trip)
15 m 13.8 bps (BTC) · 30 m 19.4 · 1 h 62 · 4 h 124 · 1 d 322. Below one hour the move is the cost.

## What was tested at 1 h – 4 h (corrected)

| book | Sharpe | MaxDD | turn/y | IS | OOS |
|---|---|---|---|---|---|
| Trend Donchian 4 h, long-only (lb 1-20 d) | 1.35 | −24.9 % | 80 | 1.30 | 1.41 |
| Trend Donchian 1 h, long-only | 1.32 | −21.1 % | 80 | 1.28 | 1.39 |
| Trend 4 h long/short | 0.67 | −24.0 % | 148 | 0.62 | 0.73 |
| EWMAC 4 h long/short | 0.78 | −24.0 % | 24 | 0.89 | 0.62 |
| Mean reversion 1 h (z 24/48 h) | **−1.3 … −1.5** | −90 % | 200-360 | loses | loses |
| Funding-settlement effect (1 h before settlement) | 0.19 gross / **−4.7** net | — | 650 | — | — |
| Market maker on BTC 1 m, 12 variants (spread 0.5-2 bps, adverse 0-2 bps) | **−25 … −43** | — | 550-1,560 fills/day | — | — |

Mean reversion, the settlement effect and market making are dead on arrival at this venue's costs
and this bot's latency (quotes go stale in the 150 ms it takes to reach Strike; the market moves
3.5 bps a minute while a 0.5 bps quote sits there).

## The finding: the SAME daily rule on a faster clock

The 4 h trend book was not a new strategy (corr 0.80-0.98 with the daily book) — so the clean
question was asked: the daily rule exactly (Donchian 10/20/30/60/90 **days**, long-only, same
sizing, same band, same costs, same funding) evaluated every 12 / 8 / 4 / 1 h instead of once a day.

| clock | Sharpe | MaxDD | turn/y | OOS Sh | corr daily |
|---|---|---|---|---|---|
| **24 h (the engine)** | 1.42 | −22.5 % | 9 | 1.40 | 1.00 |
| 12 h | **1.73** | −18.2 % | 9 | **1.61** | — |
| 8 h | 1.68 | −18.3 % | 9 | 1.52 | — |
| 4 h | 1.61 | −18.3 % | 9 | 1.50 | 0.98 |
| 1 h | 1.63 | −17.9 % | 9 | 1.52 | 0.97 |
| 4 h, cost ×2 | 1.57 | −19.2 % | 9 | 1.46 | 0.98 |
| 4 h, neighbouring lookback sets (×3) | 1.56-1.63 | −17.3…−18.1 % | 14-27 | 1.37-1.58 | 0.91-0.96 |

Year by year (4 h vs daily): better in 5/7 years (2021 +0.71, 2026 +0.22, 2024 +0.11, 2023 +0.06,
2025 +0.02; 2020 −0.09, 2022 −0.18); daily difference +2.8 %/y, **t = +1.75**.

**Mechanism.** The rule's stop is "close under the trailing mid" and its entry "close at the N-day
high". Evaluated once a day, a break that happens at 06:00 is acted on at 04:05 the next morning —
up to 28 h of exposure after the trend has ended; a breakout confirmed at noon waits until the
next day. On a sub-daily clock the same events are acted on within hours. The number of entries and
exits per year does not change (turnover 9/y in every row), they just happen earlier. The gain is
mostly in the first step (24 h → 12 h) and flat after — exactly what a delay effect looks like, and
the reverse of the project's own delay audit (each day of extra delay cost Sharpe in
scripts/validate_profile.py). This is NOT a faster signal (those died, Kurth-Eisler-Rej-Bouchaud
2026); it is the validated signal with less lag.

## Acceptance rule check (set before the tests)
Sharpe +0.10 ✔ (+0.19 … +0.31) · lower MaxDD ✔ (−22.5 → −18 %) · IS ✔ and OOS ✔ · stable across
neighbours ✔ and costs ×2 ✔ · mechanism explained ✔ · significance moderate (t = 1.75, 6 years).

## What it would take
Crypto legs only (24/7 bars; Yahoo has no sub-daily bars for gold/oil — those legs stay daily): a
bar-clock abstraction in `strategies/trend_daily.py` (today-keys → bar-keys), a sub-daily kline
store (same fetcher with interval `12h`/`4h`, lookbacks × bars-per-day, annualisation × bars-per-day),
runs at each bar close + 5 min, universe pick unchanged (daily/monthly), tracking per bar, the
basis guard and venue floors unchanged. Then: the 11 gates on the research panel's crypto legs at
the new clock, 60 days of paper beside the daily book, and only then a switch. **Not implemented
tonight**: the book is hours from its first run at the Balanced size.
