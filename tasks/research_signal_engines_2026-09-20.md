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
