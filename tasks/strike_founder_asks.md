# What we would ask Strike Finance — prioritized, with the reason and the evidence

Context for whoever reads this: BotStrike is a systematic daily trend-following bot (managed-futures
style) that trades a diversified basket of Strike perpetuals. It is in paper today, with a validated
strategy (10 years, 11/11 GO gates) and a planned 30-day canary before any real size. Everything
below comes from actually integrating against the API on 2026-09-03, not from reading the docs.

**Already found, no need to ask.** Funding history, spread history, open interest, long/short ratio
and klines all exist at `/stat/v1/stats/coin/history/...` and `/price/v2/...`. Ed25519 API wallets,
sub-account and vault scoping, order types, TWAP and the bracket endpoint are all documented and
work. The integration is done and the numbers reconcile with the UI.

---

## Tier 1 — these change what we can safely do

### 1. Longer kline history, or the index series behind each market
Measured today: BTC-USD daily klines start **2026-03-20** (167 bars), XAU and WTI **2026-04-23**,
NVDA **2026-06-04**, SP500 **2026-08-16** (18 bars). Our model uses 90-day lookbacks and a 90-day
volatility window, so it needs years, not months.

Consequence: we compute signals from an independent daily source (Yahoo) and execute on Strike.
Signal and execution then sit on different price series, which is a real risk we have to monitor
instead of eliminate.

**Ask:** can klines be backfilled from the index/oracle source per market, or can the index history
itself be exposed? Even daily closes since each market's index existed would let a systematic
integrator run signal and execution on the same series.

### 2. Funding history beyond 90 days
`/stat/v1/stats/coin/history/funding` caps `days` at 90. We measured, in that window, longs paying
BTC +8.6 %/yr, ETH +7.8 %, SOL +7.1 %, ADA +10.9 %, XAG +15.1 %, SP500 +8.4 %, XAU +0.9 %, while
NAS100 (−3.7 %) and WTI (−15.7 %) *pay* the longs. That dispersion is a first-order input for a
long-only book — with the real numbers our funding cost over ten simulated years drops from 10.6 to
1.5 points of equity — but 90 days is far too short to know whether it is a regime or a snapshot.

**Ask:** raise the cap to 1–2 years, or publish a downloadable history. This is cheap and it is
exactly what makes a venue attractive to systematic flow.

### 3. What happens to TradFi perps when the underlying market is closed
This is our biggest **unmodelled** risk. A trend book holds XAU, SP500, NAS100 and WTI over
weekends and holidays. We do not know, and could not find documented:

- Does the index freeze at the last underlying print, or does it track a proxy?
- Does the order book keep trading against a frozen index, and can the mark then diverge?
- Are liquidations and ADL evaluated against a frozen index outside session hours?
- Are there wider bands, halts or different `marketTakeBound` around the session open?

**Ask:** a short written description of the closed-session behaviour per TradFi market. We will size
weekend exposure differently depending on the answer.

### 4. Testnet accounts with a faucet
We can read mainnet with an API wallet, but we will not send a first order against real money to
find out that our reduce-only, TP/SL or partial-fill handling is wrong. `api-v2-testnet` is
documented but we have no funded account there.

**Ask:** testnet funds (or a faucet) so we can exercise the full lifecycle — place, partial fill,
replace, reduce-only close, TP/SL trigger, liquidation — and keep it in CI. This is the single
change that most shortens our path to trading real size on Strike.

### 5. Regulatory position for EU / Spanish residents
The operator is resident in Spain. Binance became unusable for him under MiCA, and ESMA treats
perpetuals as CFDs. This is the actual blocker between a validated strategy and live trading.

**Ask:** what is Strike's position on serving EU residents, and is there anything shareable
(legal opinion, terms, geo policy) that we can rely on?

---

## Tier 2 — these improve the economics measurably

### 6. Rates and FX markets — the missing legs of a managed-futures book
Measured daily-return correlation between the classes Strike lists today (2022+):

| | crypto | energy | equity | index | metal |
|---|---|---|---|---|---|
| **crypto** | 1.00 | 0.03 | 0.40 | 0.40 | 0.13 |
| **energy** | 0.03 | 1.00 | 0.01 | 0.00 | 0.08 |
| **equity** | 0.40 | 0.01 | 1.00 | 0.78 | 0.20 |
| **index** | 0.40 | 0.00 | 0.78 | 1.00 | 0.18 |
| **metal** | 0.13 | 0.08 | 0.20 | 0.18 | 1.00 |

Average off-diagonal correlation 0.22. Equity and index are 0.78 correlated, so they are really one
leg. The classic managed-futures book has **six** legs: equities, rates, FX, energy, metals and
crypto. Strike is missing rates and FX entirely.

With the same per-leg edge, going from 4 effective legs to 5 lowers portfolio volatility by 4.8 %
and raises Sharpe by ~5 %; to 6 legs, ~9 %. Our own tests already show the effect within the
existing universe: N=6 Sharpe 1.76, N=8 1.81, N=10 1.83.

**Ask:** a rates perp (10Y or an equivalent) and two or three FX majors would be, for a
trend-following flow, worth more than another ten single stocks. Rates in particular is the leg that
historically carries managed futures through equity bear markets.

### 7. Maker treatment for a daily rebalancing flow
Our rebalance is once a day, not latency-sensitive: it is naturally patient, maker-side flow.
Measured median spreads: BTC 0.23 bps, SP500 3.0, NAS100 3.0, ETH/SOL 4.0, WTI 6.0, XAG 7.7,
XAU 8.0, ADA 7.8. With the 4.5 bps taker fee that is 4.6–8.5 bps per side crossing.

**Ask:** is there a maker programme or fee tier for systematic flow, and what is the realistic fill
probability for post-only orders at the touch in the thin TradFi books? At our turnover (9.6× a
year) moving from taker to maker is worth roughly 0.5–1 point of annual return, which for a small
account is the difference between "worth running" and "not".

### 8. Depth and impact guidance in the thin markets
Measured 24 h quote volume: BTC 1.45 M$, ETH 261 k, ZEC 217 k, XAU 197 k, WTI 65 k, SP500 40 k,
XAG 19.5 k, NAS100 4.0 k, GOOGL 0. We already refuse to trade a market unless it shows 50× our
position notional in 24 h, so several TradFi markets are excluded at a 1 000 $ account and more
would be excluded as the account grows.

**Ask:** how is `marketTakeBound` computed and enforced, and is there guidance (or an endpoint) for
the maximum size that can be taken without moving the mark more than X bps? Also: are there market
makers committed to the TradFi books, or is the depth opportunistic? The answer decides whether this
strategy can ever run at size on Strike.

### 9. Sub-account creation through the API
`GET /v2/sub-accounts` returns an empty list for us and we found no documented way to create one.
We want the canary capital in its own sub-account, and if the bot is shared with a couple of
friends, one sub-account each with its own API wallet is the clean structure.

**Ask:** how are sub-accounts created, can they be created by API, and can an API wallet be scoped
to a single sub-account so a key cannot touch the rest of the balance?

### 10. Auto-deleveraging policy
ADL is documented as a mechanism but not as a probability. For a systematic book, being reduced
without our action is an uncontrollable risk we would have to model.

**Ask:** under what conditions has ADL actually fired, and is there any historical record of it per
market?

---

## Tier 3 — useful, not blocking

11. **Roadmap of new markets**, so the universe filter can anticipate rather than react.
12. **WebSocket order placement**, if it exists, to remove an HTTP round trip (not critical for a
    daily book, relevant if we ever add an intraday strategy).
13. **Vault requirements**: minimum track record, fee structures, lockups, and whether a vault can be
    traded by an API wallet with `vault_id` (the docs suggest yes). If the bot ever manages a
    friend's capital, a vault is the clean structure and we would rather build for it from the start.

---

## What we can offer back

We are integrating carefully and we keep everything we measure. If it is useful, we can share the
funding, spread and liquidity analysis per market, and report any API behaviour that does not match
the documentation — we have already found that the stats endpoints are documented under a base path
(`/stat`) that is easy to miss, and that `/v1/stats/...` without it returns 404.
