<!-- source: https://docs.strikefinance.org/perpetuals/prices.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/perpetuals/prices.md).

# Prices

| Price | What it means | Common use |
| ----------- | --------------------------------------------------------------------------------- | ------------------------------------- |
| Index Price | Fair external spot price, aggregated from multiple markets | Anchor for fair value |
| Mark Price | Fair risk price for perpetuals | Unrealized PnL, liquidations, funding |
| Mid Price | Midpoint between bid and ask | Quick view of current order book |
| Entry Price | User's average open position price | PnL reference |
| Close Price | Execution price used when a position is reduced/closed, or candle close in charts | Realized PnL or market data |

### Why We Use Multiple Prices

Perpetual futures trade on Strike's order book, but their fair value should stay close to the underlying spot market. A single traded price can be noisy, stale, or temporarily manipulated, especially in fast markets.

For that reason, Strike separates prices by purpose:

* The Index Price answers: "What is the asset worth across external spot markets?"
* The Mark Price answers: "What fair price should we use for PnL, funding, and liquidation risk?"
* The Mid Price answers: "Where is the center of the local order book right now?"
* The Entry Price answers: "At what average price did this user open their current position?"
* The Close Price answers: "At what price was this trade, candle, or closing action completed?"

### Index Price

The Index Price is Strike's estimate of the fair USD spot price of an asset. It is built from multiple external exchange price feeds rather than a single venue.

For example, the BTC Index Price may combine BTC/USDT, BTC/USD, and similar markets from several exchanges after converting all quotes into USD.

#### High-Level Calculation

1. Collect prices from configured external sources.
2. Convert non-USD quotes into USD, such as BTC/USDT multiplied by the current USDT/USD rate.
3. Ignore stale sources.
4. Compute the median of the remaining source prices.
5. Exclude source prices that are too far from the median.
6. Calculate a weighted average from the remaining sources.

Conceptually:

```
USD Price_i = Raw Price_i x Quote-to-USD Rate_i

Median Price = median(USD Price_1, USD Price_2, ..., USD Price_n)

Valid Sources = sources where:
Median Price x (1 - Deviation Limit) <= USD Price_i <= Median Price x (1 + Deviation Limit)

Index Price = sum(USD Price_i x Weight_i) / sum(Weight_i)
```

Exchange weights allow more reliable or liquid sources to have more influence on the final Index Price.

#### Stablecoin Conversion

When an asset is quoted in a stablecoin, Strike first calculates a USD rate for that stablecoin. For example:

```
BTC/USDT = 60,000
USDT/USD = 0.9998

BTC/USD source price = 60,000 x 0.9998 = 59,988
```

Stablecoin feeds use tighter outlier checks because stablecoins are expected to remain close to their peg under normal market conditions.

### Mid Price

The Mid Price is the center of the order book.

```
Mid Price = (Best Bid + Best Ask) / 2
```

Example:

```
Best Bid = 99.90
Best Ask = 100.10

Mid Price = (99.90 + 100.10) / 2 = 100.00
```

The Mid Price is useful as a quick market reference, but it is not necessarily the price a user can execute at. A buy order usually executes against asks, and a sell order usually executes against bids.

Strike also uses an impact-price version of the order book midpoint for some risk calculations. Impact prices look deeper into the book and estimate the average executable price for a configured notional size, rather than only using the very best bid and ask.

### Mark Price

The Mark Price is the fair risk price of a perpetual market. It is designed to be harder to manipulate than the last traded price.

Strike uses the Mark Price for:

* Unrealized PnL
* Liquidation checks
* Funding payments
* Risk monitoring

The Mark Price is not simply copied from another exchange, and it is not always equal to the latest trade price on Strike.

#### High-Level Calculation

Strike calculates three price components, then takes the median.

```
Mark Price = median(Price 1, Price 2, Price 3)
```

#### Price 1: Funding-Adjusted Index Price

Price 1 starts from the Index Price and adjusts it by the latest funding rate over the remaining time in the funding window.

```
Price 1 = Index Price x (1 + Last Funding Rate x Time Until Next Funding / Funding Period)
```

This helps the Mark Price smoothly account for funding effects as the next funding time approaches.

#### Price 2: Basis-Adjusted Index Price

Price 2 starts from the Index Price and adjusts it by the recent local order book basis.

```
Price 2 = Index Price + Moving Average(Recent Basis)

Recent Basis = Impact Mid Price - Index Price
Impact Mid Price = (Impact Bid + Impact Ask) / 2
```

This lets the Mark Price reflect persistent differences between Strike's perpetual market and the external spot index, while still being anchored to the Index Price.

#### Price 3: Contract and Order Book Component

Price 3 uses local market information from the order book and latest trade.

```
Price 3 = median(Best Bid, Best Ask, Last Trade Price)
```

Taking the median helps prevent a single unusual trade, bid, or ask from dominating the final Mark Price.

#### Final Mark Price Example

```
Price 1 = 60,010
Price 2 = 59,995
Price 3 = 60,200

Mark Price = median(60,010, 59,995, 60,200) = 60,010
```

### Premium Index and Funding

The Premium Index measures whether the perpetual market is trading above or below the Index Price. It is an important input into funding.

```
Premium Index =
[max(0, Impact Bid - Index Price) - max(0, Index Price - Impact Ask)] / Index Price
```

Interpretation:

| Premium Index | Meaning |
| ------------- | ------------------------------------------------ |
| Positive | Perpetual market is trading above the spot index |
| Negative | Perpetual market is trading below the spot index |
| Near zero | Perpetual market is close to the spot index |

Funding uses the average Premium Index plus an interest-rate component, with caps and floors.

```
Funding Rate =
Average Premium + clamp(Interest Rate - Average Premium, Damper Min, Damper Max)

Final Funding Rate =
clamp(Funding Rate, Funding Floor, Funding Cap)
```

Depending on the market configuration, the final rate may be scaled from a standard funding basis into the active payment interval.

Funding payments use Mark Price:

```
Funding Payment = -Position Size x Mark Price x Funding Rate
```

When the funding rate is positive, longs pay shorts. When the funding rate is negative, shorts pay longs.

### Entry Price

The Entry Price is the average price of a user's current open position.

If a user opens a position from zero, the Entry Price is the fill price. If the user increases a position in the same direction, the Entry Price becomes a weighted average.

```
New Entry Price =
(Old Entry Price x |Old Position Size| + Fill Price x Fill Size)
/ (|Old Position Size| + Fill Size)
```

Example:

```
Existing long: 1 BTC at 60,000
New buy fill: 1 BTC at 62,000

New Entry Price = (60,000 x 1 + 62,000 x 1) / 2 = 61,000
```

If a user partially closes a position, the Entry Price of the remaining position does not change. The closing fill realizes profit or loss against the existing Entry Price.

If a user fully closes a position, there is no remaining Entry Price. If a user reverses direction, the closed portion realizes PnL and the remaining new position starts with a new Entry Price based on the reversal fill.

Unrealized PnL uses Mark Price and Entry Price:

```
Unrealized PnL = (Mark Price - Entry Price) x Position Size
```

Position Size is positive for longs and negative for shorts.

### Close Price

"Close Price" can mean two related but different things depending on context.

#### Position Close Price

For a user's position, the Close Price is the actual fill price when the position is reduced or closed. If a close happens across multiple fills, the average close price can be understood as:

```
Average Close Price =
sum(Fill Price_i x Closed Size_i) / sum(Closed Size_i)
```

Realized PnL is calculated from actual fill prices, not from the Mark Price or Index Price.

For a long:

```
Realized PnL = (Close Price - Entry Price) x Closed Size
```

For a short:

```
Realized PnL = (Entry Price - Close Price) x Closed Size
```

#### Candle Close Price

For charts and klines, the Close Price is the last price recorded in that candle interval.

For example, in a 1-minute candle, the close is the final price at the end of that minute. Depending on the selected chart type, this may be based on last traded price, mark price, or index price.

### Outlier Detection and Price Protection

Strike applies multiple safeguards so one bad source or unusual trade is less likely to affect users unfairly.

#### External Source Protection

For Index Price calculation:

* Stale exchange feeds are ignored.
* A median price is calculated from valid sources.
* Sources outside a configured median band are excluded.
* The Index Price is calculated from the weighted average of sources that remain.

Conceptually:

```
Lower Bound = Median Price x (1 - Deviation Limit)
Upper Bound = Median Price x (1 + Deviation Limit)

Exclude source if:
Source Price < Lower Bound
or
Source Price > Upper Bound
```

Spot markets currently use a wider deviation band than stablecoin conversion feeds.

#### Order Book Impact Protection

Impact bid and impact ask prices are checked against the Index Price. If an impact price is missing, invalid, or far outside the expected range, Strike falls back to the Index Price for that component.

This helps protect funding and mark calculations from thin or temporarily distorted order books.

#### Stale Trade Protection

If the latest trade price moves far away from the previous Mark Price and there has not been a fresh trade within the configured threshold, Strike can replace the last-trade component with the previous Mark Price.

This reduces the chance that an old or isolated trade affects liquidation-sensitive calculations.

#### Final Mark Price Guardrail

After the Mark Price is calculated, Strike checks whether it is too far from the Index Price. If it is outside the configured deviation band, Strike falls back to the basis-adjusted component.

Conceptually:

```
If abs(Mark Price - Index Price) / Index Price > Deviation Limit:
Mark Price = Price 2
```

This keeps the Mark Price anchored to the broader external market.

### Common Questions

#### Why is my PnL based on Mark Price instead of Last Price?

The Last Price can move sharply because of a single trade. Mark Price is designed to be more stable and harder to manipulate, so it is used for unrealized PnL and liquidation checks.

#### Why can Mark Price be different from Index Price?

The Index Price tracks external spot markets. The Mark Price starts from the Index Price but also considers funding, recent basis, and local order book conditions. This lets it remain fair while still reflecting the perpetual market.

#### Why can my execution price differ from Mid Price?

The Mid Price is only the midpoint between bid and ask. Actual execution depends on order side, available liquidity, order type, and how much size is available at each price level.

#### Does Entry Price change when I partially close?

No. A partial close realizes PnL on the closed size, while the Entry Price for the remaining position stays the same.

#### Which price matters for liquidation?

Liquidation checks use Mark Price, not Last Price. This protects users from liquidations caused by a single unusual trade.