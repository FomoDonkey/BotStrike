<!-- source: https://docs.strikefinance.org/perpetuals/margin-tiers.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/perpetuals/margin-tiers.md).

# Margin Tiers

Perpetual exchanges use margin to ensure traders can cover potential losses on their open positions. As position sizes grow, the risk to the exchange and other traders increases — a large position that gets liquidated can cause significant market impact. Margin tiers address this by requiring proportionally more margin as your position size increases, while still allowing high leverage on smaller positions.

### How Margin Tiers Work

Each market has a set of margin tiers defined by position notional value thresholds. Your position's notional value — calculated as Mark Price × Position Size — determines which tier applies to you. Smaller positions fall into lower tiers with higher maximum leverage and lower maintenance requirements. Larger positions fall into higher tiers with lower maximum leverage and higher maintenance requirements.

Your tier is re-evaluated continuously as the mark price changes. If a price move causes your position's notional value to cross a tier boundary, your margin requirements adjust accordingly — no action is required on your part.

### Tier Structure

Each tier defines three parameters:

* **Max Notional** — The maximum position notional value for this tier. Once your notional exceeds this value, you move to the next tier.
* **Max Leverage** — The maximum leverage you can use while in this tier. Higher tiers have lower maximum leverage.
* **Maintenance Margin Rate (MMR)** — The percentage of your notional value that must be maintained as margin to avoid liquidation. Higher tiers have higher maintenance rates.

Tiers are ordered from smallest to largest. The first tier always offers the highest leverage and the lowest maintenance rate.

### Example Tier Table

| Tier | Max Notional | Max Leverage | MMR |
| ---- | ------------ | ------------ | ----- |
| 1 | $50,000 | 100x | 0.50% |
| 2 | $250,000 | 50x | 1.00% |
| 3 | $1,000,000 | 20x | 2.50% |
| 4 | $5,000,000 | 10x | 5.00% |

A trader with a $30,000 notional position is in Tier 1 — they can use up to 100x leverage and need only 0.50% of their notional as maintenance margin. A trader with a $300,000 notional position is in Tier 3 — they are limited to 20x leverage and must maintain 2.50% of their notional as margin.

### Smooth Transitions Between Tiers

Crossing a tier boundary could cause a sudden jump in your margin requirement — for example, going from 1% MMR to 2% MMR on a $100,000 position would instantly double your maintenance margin. To prevent this, each tier includes a **Maintenance Amount (MA)** deduction that smooths the transition. At the exact boundary between two tiers, both tiers produce the same maintenance margin, so your requirements grow gradually rather than jumping at boundaries.

### Impact on Leverage

Your chosen leverage cannot exceed the Max Leverage of the tier your position falls into. If adding to a position would push your notional into a higher tier, the leverage limit of that higher tier applies to the entire position.

Reducing leverage always succeeds. Increasing leverage may fail if the requested leverage exceeds your current tier's maximum.

### Impact on Liquidation

Margin tiers directly affect your liquidation price. Higher tiers — with higher MMR — push your liquidation price closer to your entry price, meaning you have less room before liquidation. The Maintenance Amount partially offsets this, giving you slightly more breathing room than the raw MMR alone would suggest.

In practice, if your position grows large enough to move into a higher tier (either from adding size or from price appreciation), your maintenance requirement increases. For price-driven tier changes, the unrealized profit from the price move typically more than compensates for the higher requirement.

### Cross vs Isolated Margin

Margin tiers apply the same way regardless of margin mode. Each position's tier is resolved independently based on its own notional value. In cross margin, positions share the same margin pool but each has its own tier. In isolated margin, each position has dedicated margin and its tier only affects that position.

### Key Takeaways

* Margin tiers protect the exchange and traders by requiring proportionally more margin for larger positions.
* Your tier is determined by your position's notional value and re-evaluates continuously as the mark price changes.
* Higher tiers mean lower maximum leverage, higher maintenance margin rates, and liquidation prices closer to entry.
* Transitions between tiers are smooth — there are no sudden jumps in margin requirements at boundaries.
* Each market has its own independent set of margin tiers, so the same notional value may place you in different tiers depending on the asset you are trading.