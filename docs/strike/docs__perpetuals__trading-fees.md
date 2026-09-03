<!-- source: https://docs.strikefinance.org/perpetuals/trading-fees.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/perpetuals/trading-fees.md).

# Trading Fees

Strike uses a tiered fee schedule based on your rolling 30-day trading activity. Higher-volume traders may qualify for lower taker fees, and high maker participation may qualify for maker rebates.

Fee tiers are recalculated every day at **00:05 UTC**.

### Fee Tiers

Your base fee tier is determined by your 30-day trading volume.

| Tier | 30D Volume | Taker Fee | Maker Fee |
| ------ | ------------: | --------: | --------: |
| Tier 0 | $0 - $100K | 0.050% | -0.005% |
| Tier 1 | $100K - $500K | 0.045% | -0.005% |
| Tier 2 | $500K - $2M | 0.040% | -0.005% |
| Tier 3 | $2M - $10M | 0.035% | -0.005% |
| Tier 4 | $10M - $50M | 0.032% | -0.005% |
| Tier 5 | $50M - $200M | 0.030% | -0.005% |
| Tier 6 | >= $200M | 0.028% | -0.005% |

### Maker Rebate Tiers

Maker rebate tiers are based on your share of total 30-day maker volume. If you qualify, your maker fee can become negative, which means you receive a rebate instead of paying a fee.

| Tier | 30D Maker Volume Share | Maker Fee |
| ---- | ---------------------: | --------: |
| 1 | > 5.00% | -0.008% |
| 2 | > 15.00% | -0.010% |
| 3 | > 30.00% | -0.012% |

Negative fees indicate a rebate. For example, a maker fee of `-0.005%` means you receive a maker rebate on eligible maker fills.

### Staked $STRIKE Fee Discounts

Eligible staked $STRIKE balances may unlock additional trading fee discounts.

| $STRIKE Amount | Fee Discount |
| --------------: | -----------: |
| 5,000 $STRIKE | 5% |
| 20,000 $STRIKE | 10% |
| 50,000 $STRIKE | 15% |
| 100,000 $STRIKE | 20% |
| 150,000 $STRIKE | 30% |
| 250,000 $STRIKE | 40% |

Discounts apply to positive trading fees. Maker rebates are already negative fees, so discounts do not reduce or increase the rebate amount.

### How Fees Are Applied

Fees are calculated on filled order notional:

```
Trading Fee = Fill Price x Fill Size x Fee Rate
```

For taker fills, the taker fee rate from your current fee tier is used.

For maker fills, the maker fee rate from your base tier is used unless you qualify for a maker rebate tier. If the maker fee is negative, the amount is credited as a rebate.

### Examples

#### Taker Fee

```
Fill notional = $10,000
Taker fee = 0.050%

Fee = 10,000 x 0.0005 = $5.00
```

#### Maker Rebate

```
Fill notional = $10,000
Maker fee = -0.005%

Rebate = 10,000 x 0.00005 = $0.50
```

#### STRIKE Discount

```
Base taker fee = 0.050%
STRIKE discount = 10%

Effective taker fee = 0.050% x (1 - 10%) = 0.045%
```