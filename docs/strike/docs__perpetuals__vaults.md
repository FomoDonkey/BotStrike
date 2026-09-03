<!-- source: https://docs.strikefinance.org/perpetuals/vaults.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/perpetuals/vaults.md).

# Vaults

Vaults are managed trading accounts on Strike where a leader trades on behalf of depositors. Depositors allocate capital to a vault, and the vault leader executes trades using the pooled funds. Profits and losses are shared proportionally based on each depositor's share of the vault.

### Why Vaults

#### Earn Passive Yield as a Depositor

Vaults let you earn yield on your capital without actively trading. Browse verified vaults run by professional market makers and experienced traders, deposit USDC, and earn returns based on the vault's performance. You can track every trade the vault makes in real time, review historical performance metrics like PnL and APR, and request withdrawals at any time (subject to a configurable lockup period). It's a hands-off way to put your capital to work on Strike.

#### Scale Your Strategy as a Vault Leader

If you're a market maker, run a trading bot, or have a consistent strategy, vaults let you raise capital from depositors and trade with a larger pool. You earn a performance fee on the profits you generate for depositors. The more capital in your vault and the better you perform, the more you earn. Verified vaults get additional visibility on the platform, making it easier to attract depositors.

### How Vaults Work

A vault is a trading account controlled by a single leader. The leader makes all trading decisions — placing orders, managing positions, adjusting leverage — using the combined capital of all depositors. Depositors earn returns based on the vault's performance without needing to trade themselves.

Each vault has its own isolated trading account. The leader's personal account and the vault account are completely separate — the leader's personal positions and the vault's positions do not affect each other.

### Vault Types

Strike supports two types of vaults:

* **User Vaults** — Created by any trader. Subject to a 100 USDC creation fee, a default 10% leader commission on profits, and a requirement that the leader maintains at least 5% of total vault shares. Users can only have one active user vault at a time.
* **Protocol Vaults** — Created by Strike administrators. No creation fee, no leader commission (forced to 0%), automatically verified. Admins can create multiple protocol vaults. Used for official protocol strategies.

### Creating a Vault

To create a vault, the leader specifies:

* **Name and description** — Identifies the vault to potential depositors.
* **Initial deposit** — The leader must deposit their own capital to start the vault. This initial deposit sets the baseline share price at $1.00 per share.
* **Leader commission** — The percentage of depositor profits the leader earns as a performance fee, set in basis points (0–5000 bps, i.e., 0–50%). Defaults to 10% for user vaults.
* **Minimum deposit** — The minimum amount a depositor must contribute. Defaults to $100.
* **Lockup period** — The number of days a depositor must wait after requesting a withdrawal before funds are released. Defaults to 1 day.

The leader's initial deposit is deducted from their personal account along with the creation fee (for user vaults). The creation fee goes to the protocol commission account.

### Depositing to a Vault

Any user can deposit USD into an active vault. When depositing:

1. The deposit amount is deducted from the depositor's personal account balance (only withdrawable balance is used — margin for open positions is protected).
2. New shares are minted based on the current share price. If the vault has grown in value since creation, new depositors receive fewer shares per dollar, reflecting the vault's appreciation.
3. The depositor's average entry price is tracked for profit calculations on withdrawal.

The share price is calculated as: **vault account equity / total shares outstanding**, where equity is the vault's cash balance plus unrealized PnL from all open positions. As the leader generates profits, the share price increases. As the leader incurs losses, it decreases.

### Withdrawing from a Vault

Withdrawals follow a request-then-execute model with a lockup period:

1. **Request** — The depositor requests to withdraw a specific number of shares. Those shares are locked immediately and cannot be used for further withdrawal requests. The estimated USD value is calculated at the current share price, but the actual payout is determined at execution time.
2. **Lockup** — The withdrawal enters a waiting period (configured per vault, default 1 day). This gives the leader time to manage liquidity — closing positions or canceling orders if needed to free up cash.
3. **Execution** — After the lockup period expires, the withdrawal is executed at the current share price. Shares are burned, the leader's commission is deducted from any profits, and the depositor receives the net USD back to their personal account. If the vault does not have sufficient available balance (e.g., funds are tied up in open positions), the withdrawal remains pending and retries automatically until the leader frees up liquidity.
4. **Cancellation** — Depositors can cancel a pending withdrawal at any time while it has not yet been executed. Locked shares are returned to their available balance.

#### Leader Commission on Profits

When a depositor withdraws at a profit (current share price > their average entry price), the leader earns a commission on the profit portion:

* **Commission** = (current price - entry price) x shares x commission rate
* The commission is deducted from the withdrawal amount and stays in the vault, effectively increasing the share value for remaining depositors.
* If the depositor is withdrawing at a loss, no commission is charged.

#### Leader Minimum Stake

The vault leader must maintain at least 5% of the vault's total shares at all times. This requirement is enforced on leader withdrawals — the leader cannot withdraw shares that would drop their ownership below this threshold. This ensures the leader has meaningful skin in the game.

### Vault States

* **Active** — The vault is open for deposits and the leader can trade normally.
* **Paused** — The leader (or an admin) has paused the vault. No new deposits are accepted, but the leader can still trade to close positions. Withdrawal requests can still be submitted, and pending withdrawals continue to be processed.

### Public Vault Data

The following vault information is publicly visible to all users:

* Vault name, description, and type
* Leader identity and commission rate
* Total value, share price, and number of depositors
* Historical deposits and withdrawals
* Portfolio metrics (PnL, TVL, APR)
* All open positions and trade history (via the vault's account ID)

This transparency allows depositors to evaluate vault performance before committing capital.