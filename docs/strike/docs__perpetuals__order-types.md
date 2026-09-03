<!-- source: https://docs.strikefinance.org/perpetuals/order-types.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/perpetuals/order-types.md).

# Order Types

Strike supports a range of order types for perpetual futures trading. Orders are matched on a price-time priority basis — at the same price level, earlier orders are filled first.

### Basic Order Types

#### Market Order

A market order executes immediately at the best available price in the order book. Use market orders when you want guaranteed execution and are willing to accept the current market price.

* Fills against resting limit orders on the opposite side of the book.
* If the order is larger than the available liquidity at the best price, it fills across multiple price levels (price impact).
* A price bound is automatically applied based on the current mark price to protect against extreme slippage. The order will not fill beyond this bound.

#### Limit Order

A limit order rests in the order book at a specified price and waits to be filled. Buy limit orders execute at or below the limit price. Sell limit orders execute at or above the limit price.

* If the limit price crosses the current best price, the order fills immediately (like a market order) up to the available liquidity, and any remaining size rests in the book.
* Limit orders provide price certainty — you will never get a worse price than your limit.
* Limit prices are validated against the current mark price — orders too far from the mark price are rejected to prevent erroneous submissions.

### Conditional Orders

Conditional orders sit dormant until a trigger price is reached, at which point they activate and become regular market or limit orders. These are used for automated risk management and entry strategies.

#### Trigger Price Source

Conditional orders can be configured to trigger on one of two price sources:

* **Mark Price** (default) — Triggers based on the oracle-derived mark price. More resistant to short-term price manipulation and wicks.
* **Last Price** — Triggers based on the last traded price on the order book. Reacts faster to actual trades but is more susceptible to wicks.

#### Stop (Stop Market)

Triggers a market order when the price reaches the stop price. Used primarily for stop-loss protection.

* **Sell stop**: Triggers when price falls to or below the stop price. Protects long positions.
* **Buy stop**: Triggers when price rises to or above the stop price. Protects short positions.

#### Stop Limit

Triggers a limit order when the price reaches the stop price. Combines the trigger mechanism of a stop with the price certainty of a limit order.

* After triggering, the order rests in the book at the specified limit price.
* Risk: if the market moves past your limit price before filling, the order may not execute.

#### Take Profit (Take Profit Market)

Triggers a market order when the price reaches the take-profit level. Used to lock in gains automatically.

* **Sell take-profit**: Triggers when price rises to or above the target. Closes a long at profit.
* **Buy take-profit**: Triggers when price falls to or below the target. Closes a short at profit.

#### Take Profit Limit

Triggers a limit order at the take-profit level. Same as take-profit but with a limit price for execution.

### Strategy Orders (Bracket / OTOCO)

Strategy orders allow you to attach take-profit and stop-loss orders to a primary order, creating a bracket around your position. The system uses an OTOCO (One-Triggers-One-Cancels-Other) model:

1. **Primary order** — Your entry order (market or limit). When this fills, the attached secondary orders become active.
2. **Secondary orders** — Take-profit and/or stop-loss orders attached to the primary. These remain dormant until the primary fills.
3. **OCO behavior** — When one secondary order triggers and fills, all other secondary orders in the same strategy are automatically cancelled.

Secondary orders are always reduce-only and sized to close the position opened by the primary order. If the primary order is cancelled or rejected before filling, all attached secondary orders are also cancelled.

### Time in Force

Time in force determines how long an order stays active:

* **GTC (Good Till Cancelled)** — The order remains in the book until fully filled or manually cancelled. This is the default.
* **IOC (Immediate or Cancel)** — The order fills as much as possible immediately, then any unfilled portion is cancelled. Use when you want partial fills but don't want to leave a resting order.
* **FOK (Fill or Kill)** — The order must fill entirely in a single match or it is cancelled completely. Use when you need the full size or nothing.

### Order Options

#### Post-Only

A post-only order is guaranteed to enter the order book as a maker (resting) order. If the order would immediately match against an existing order (i.e., cross the spread), it is cancelled instead of filling. This ensures you always pay the maker fee, never the taker fee.

#### Reduce-Only

A reduce-only order can only decrease an existing position — it cannot open a new position or increase the current one. If you have no open position, a reduce-only order is rejected. If the order size exceeds your position size, it is clamped to the position size.

Use reduce-only orders for stop-losses and take-profits to ensure they only close your position and never accidentally open a position in the opposite direction.

#### Close Position

A close-position order automatically sizes itself to close your entire open position. It is always reduce-only. Typically used with conditional orders — for example, a stop-loss that closes your entire position when triggered.

### Self-Match Prevention

If an incoming order would match against another order from the same account, the match is prevented — no trade is created and the resting order's quantity is consumed. This prevents wash trading.

### Batch Orders

You can submit multiple orders in a single request using batch orders. Each order in the batch is processed independently — some may succeed while others fail. Batch orders reduce latency for strategies that need to place or update multiple orders simultaneously.

### Replace Orders

A replace order atomically cancels an existing order and places a new one. This is useful for updating the price or size of a resting order without the risk of the position being unhedged between a cancel and a new placement. If the original order has already been partially filled, the replacement reflects the remaining size.

You can also replace orders in batch, updating multiple resting orders in a single request.

### Cancel Orders

* **Cancel** — Cancels a single order by order ID.
* **Cancel All** — Cancels all open orders, optionally filtered by symbol. Useful for quickly exiting all resting orders during volatile conditions.

### Order Sizing

Each market defines its own sizing constraints:

* **Step size** — Orders must be in increments of the step size (e.g., 0.001 BTC).
* **Minimum size** — The smallest order allowed (does not apply to reduce-only or close-position orders).
* **Maximum size** — The largest single order allowed.
* **Minimum notional** — The minimum USD value of an order (does not apply to reduce-only or close-position orders).

### Partial Fills

When an order cannot be fully filled in a single match, it receives a partial fill. The filled portion is executed immediately, and the remaining size stays in the order book (for GTC orders) or is cancelled (for IOC orders). FOK orders do not allow partial fills.