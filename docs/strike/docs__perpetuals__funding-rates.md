<!-- source: https://docs.strikefinance.org/perpetuals/funding-rates.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/perpetuals/funding-rates.md).

# Funding Rates

Perpetual futures do not expire. Funding helps keep a perpetual contract's price aligned with its underlying index price by transferring value between traders holding long and short positions.

Funding is a peer-to-peer transfer between traders. Strike does not keep the funding payment.

### At a glance

| Item | Strike funding behavior |
| ----------------- | ----------------------------------------------------- |
| Payment schedule | Every hour, on the hour (UTC) |
| Calculation basis | Market parameters and rate limits use an 8-hour basis |
| Hourly rate | The calculated 8-hour rate divided by 8 |
| Premium sampling | At most one valid sample per fixed 5-second UTC slot |
| Premium averaging | Simple average over the active 1-hour Funding Window |
| Positive funding | Longs pay shorts |
| Negative funding | Shorts pay longs |
| Platform fee | None; funding is transferred between traders |

An 8-hour calculation basis does **not** mean funding is paid only once every eight hours. Strike recalculates funding for each 1-hour window and applies one-eighth of that window's 8-hour-basis rate.

### Who pays whom?

| Hourly funding rate | Long positions | Short positions |
| ------------------- | --------------- | --------------- |
| Positive | Pay funding | Receive funding |
| Negative | Receive funding | Pay funding |
| Zero | No transfer | No transfer |

A positive Premium Index generally indicates that the perpetual is trading above its index; a negative Premium Index indicates that it is trading below its index. The final funding rate can also include a market-specific Interest Rate and Interest Rate Dampener, so its sign is determined by the complete formula below—not by the latest premium sample alone.

### Funding payment calculation

Strike represents a long position with a positive size and a short position with a negative size. The signed balance transfer for one position is:

```
Funding Transfer = -Signed Position Size × Mark Price × Hourly Funding Rate
```

| Term | Meaning |
| -------------------- | --------------------------------------------------- |
| Signed Position Size | Positive for a long; negative for a short |
| Mark Price | The market's Mark Price at the funding event |
| Hourly Funding Rate | The decimal rate calculated for that Funding Window |
| Funding Transfer | Positive means received; negative means paid |

#### Payment example

Assume a trader holds a long position of `2 BTC`, the Mark Price is `$50,000`, and the hourly Funding Rate is `+0.00125%`, or `0.0000125` as a decimal:

```
Funding Transfer = -2 × $50,000 × 0.0000125
= -$1.25
```

The long pays `$1.25`. A `-2 BTC` short receives `$1.25`. If the Funding Rate were `-0.00125%`, the direction would reverse: the long would receive `$1.25` and the short would pay `$1.25`.

### How the funding rate is determined

Each hourly funding calculation has four stages:

1. Calculate Impact Bid and Impact Ask Prices from executable order-book depth.
2. Sample the Premium Index and average valid samples over the active hour.
3. Apply the market's Interest Rate and Interest Rate Dampener rules.
4. Apply the 8-hour cap and floor, then divide the result by 8.

#### 1. Impact Prices and Impact Notional

Impact Prices estimate the average execution price of a market order of a standard quote-currency size. Strike derives that size from the market's tier-0 maximum leverage:

```
Impact Leverage = clamp(Tier-0 Maximum Leverage, 20, 50)
Impact Notional = $50 × Impact Leverage
```

| Tier-0 maximum leverage | Impact leverage | Impact Notional |
| ----------------------: | --------------: | --------------: |
| 10× | 20 | $1,000 |
| 20× | 20 | $1,000 |
| 30× | 30 | $1,500 |
| 50× | 50 | $2,500 |
| 125× | 50 | $2,500 |

The order book is walked until the Impact Notional is filled. The resulting average fill prices become the Impact Bid and Impact Ask Prices.

As a safeguard, an Impact Price outside `80%–120%` of the Index Price is replaced with the Index Price. A side that cannot produce a valid positive Impact Price also falls back to the Index Price.

#### 2. Premium Index

The Premium Index measures executable order-book pressure relative to the Index Price:

```
Premium Index =
[max(0, Impact Bid - Index Price)
- max(0, Index Price - Impact Ask)]
/ Index Price
```

Strike commits at most one valid Premium Index sample per fixed 5-second UTC slot. The average used for funding is the simple mean of valid samples in the active 1-hour Funding Window:

```
P = sum(Premium Index samples) / number of valid samples
```

A full window can contain up to 720 samples. Before the first valid sample in an active window, the real-time estimate uses `P = 0`, so the market's other configured funding components still appear in the estimate. Actual settlement requires premium data: if a completed window has no valid Premium Index samples, Strike does not publish a funding payment for that market and hour.

#### 3. Market funding parameters

All parameters in this table use an 8-hour basis. These are Strike's current standard profiles; a market may use different limits if its risk profile requires them.

| Market profile | Interest Rate (`I`) | Interest Rate Dampener (`D`) | 8-hour cap | 8-hour floor |
| -------------- | ------------------: | ---------------------------: | -------------: | --------------: |
| Crypto | `0.01%` (`0.0001`) | `0%` (`0`) | `+4%` (`0.04`) | `-4%` (`-0.04`) |
| TradFi and RWA | `0%` (`0`) | `0.05%` (`0.0005`) | `+4%` (`0.04`) | `-4%` (`-0.04`) |

**Interest Rate Dampener behavior**

The Interest Rate Dampener controls how the Interest Rate contributes to the funding calculation. It does not clamp the Premium Index itself and it is separate from the final Funding Rate cap and floor.

| Dampener setting | Effective Interest (`E`) | Behavior | Standard profile |
| ---------------- | -------------------------- | ----------------------------------------------------------------------------------------------- | ---------------- |
| `D = 0` | `E = I` | Adds the Interest Rate directly to the Average Premium Index | Crypto |
| `D > 0` | `E = clamp(I - P, -D, +D)` | Keeps the `I - P` adjustment within `-D` and `+D` before adding it to the Average Premium Index | TradFi and RWA |

The following examples show how the two branches behave. All inputs and intermediate rates use an 8-hour basis; the last column is the hourly rate.

| Scenario | `P` | `I` | `D` | Effective Interest (`E`) | `P + E` | Hourly rate |
| --------------------------------- | ---------: | -------: | ------: | -----------------------: | ---------: | ------------: |
| Crypto additive branch | `+0.0429%` | `+0.01%` | `0%` | `+0.01%` | `+0.0529%` | `+0.0066125%` |
| Dampened premium inside the band | `+0.03%` | `0%` | `0.05%` | `-0.03%` | `0%` | `0%` |
| Dampened premium beyond the band | `+0.08%` | `0%` | `0.05%` | `-0.05%` | `+0.03%` | `+0.00375%` |
| Dampened discount beyond the band | `-0.08%` | `0%` | `0.05%` | `+0.05%` | `-0.03%` | `-0.00375%` |

#### 4. Final rate formula

Let:

| Symbol | Definition |
| ---------- | --------------------------------------------------- |
| `P` | Average Premium Index for the active 1-hour window |
| `I` | Market's 8-hour Interest Rate |
| `D` | Market's non-negative 8-hour Interest Rate Dampener |
| `E` | Effective Interest produced by the selected branch |
| `F_8h` | Final rate on an 8-hour basis |
| `F_hourly` | Rate applied at the hourly funding event |

The calculation is:

```
If D = 0:
E = I

If D > 0:
E = clamp(I - P, -D, +D)

F_8h = clamp(P + E, Funding Rate Floor, Funding Rate Cap)
F_hourly = F_8h / 8
```

The standard `+4%` and `-4%` 8-hour boundaries correspond to maximum hourly rates of `+0.5%` and `-0.5%` after division by 8.

### Worked rate examples

#### Crypto: additive Interest Rate

For the standard Crypto profile, `I = 0.01%` and `D = 0`:

```
Average Premium Index (P) = 0.0429%
Interest Rate (I) = 0.01%
Interest Rate Dampener = 0%

E = I = 0.01%
F_8h = clamp(0.0429% + 0.01%, -4%, +4%)
= 0.0529%
F_hourly = 0.0529% / 8
= 0.0066125%
```

With `D = 0`, the configured Interest Rate is additive, so the hourly rate is `0.0066125%`.

#### TradFi/RWA: bounded interest adjustment

For the standard TradFi/RWA profile, `I = 0%` and `D = 0.05%`:

```
Average Premium Index (P) = 0.08%
Interest Rate (I) = 0%
Interest Rate Dampener = 0.05%

I - P = -0.08%
E = clamp(-0.08%, -0.05%, +0.05%) = -0.05%
F_8h = clamp(0.08% - 0.05%, -4%, +4%)
= 0.03%
F_hourly = 0.03% / 8
= 0.00375%
```

### Funding Window timeline

| Time within the hour | What happens |
| -------------------- | --------------------------------------------------------------------------------------- |
| Start of the hour | A new Funding Window begins; its sample count starts at zero |
| During the hour | Valid Premium Index samples are collected and the estimated rate updates |
| `HH:00:00 UTC` | If the completed window has valid samples, its hourly rate is applied to open positions |
| Immediately after | A new window starts and the next funding time moves to the next hour |

You pay or receive funding only if you hold an open position when the funding event is processed. Opening and closing a position entirely between funding events does not create a funding payment.

The real-time rate shown before the boundary is an estimate based on samples collected so far. It can change until the window closes.

### Funding, margin, and liquidation

Funding transfers are applied directly to account equity. Payments received increase equity; payments made decrease it.

| Margin mode | Funding behavior |
| ----------- | ------------------------------------------------------------------------------------ |
| Cross | The transfer affects the shared account balance used by cross-margin positions |
| Isolated | The transfer also adjusts that position's isolated balance by the same signed amount |

A funding payment can move an account or isolated position closer to liquidation. Funding is applied before the liquidation checks in the same market-update cycle, so a sufficiently large payment can trigger liquidation immediately. If an isolated balance becomes unsafe or negative, the normal liquidation and bankruptcy protections apply.

### History and accumulated funding

Every funding payment is recorded in transaction history. Each position also tracks its accumulated funding over its lifetime:

| Accumulated value | Meaning |
| ----------------- | --------------------------------------------------------------- |
| Positive | The position has received more funding than it has paid |
| Negative | The position has paid more funding than it has received |
| Zero | Funding received and paid are equal, or no funding has occurred |