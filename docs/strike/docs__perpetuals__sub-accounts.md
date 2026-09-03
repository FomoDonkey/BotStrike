<!-- source: https://docs.strikefinance.org/perpetuals/sub-accounts.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/perpetuals/sub-accounts.md).

# Sub-accounts

Sub-accounts let you create separate trading accounts under one master account. Each sub-account can have its own balance, positions, orders, performance, and risk level.

Your master account remains the main account connected to your wallet. Sub-accounts are managed by the master account and do not have their own separate wallet identity.

### Why use sub-accounts?

Sub-accounts help you keep different activities separate without needing to manage multiple wallets.

* Run different trading strategies independently.
* Separate hedging positions from your main trading strategy.
* Give each strategy its own balance and risk budget.
* Track performance and PnL by strategy.
* Keep market-making, arbitrage, and directional trading separate.
* Test new ideas with a dedicated balance.

Each sub-account has its own margin and liquidation risk. Balances and risk are not automatically combined across your master account and sub-accounts.

### Moving funds between accounts

Funds can be moved internally between your master account and its sub-accounts.

You can:

* Move funds from the master account into a sub-account.
* Move funds from a sub-account back to the master account.
* Move funds between sub-accounts belonging to the same master account.

These internal transfers do not involve the blockchain and do not require an external wallet transaction.

Only accounts belonging to the same master account can transfer funds to one another. Transfers between different users are not supported.

### Deposits and withdrawals

External deposits and withdrawals are handled through the master account.

Sub-accounts do not have their own deposit addresses and cannot directly withdraw funds to an external wallet. To move funds out of a sub-account, first transfer the funds back to the master account. The master account can then complete the external withdrawal.

### Vaults

Only the master account can deposit funds into or withdraw funds from a vault.

Sub-accounts cannot directly:

* Deposit into a vault.
* Request a withdrawal from a vault.
* Use a vault position as a substitute for their own trading balance.

The usual flow is:

1. Deposit external funds into the master account.
2. Use the master account to deposit funds into a vault.
3. Receive vault shares in the master account.
4. Request a withdrawal from the master account when you want to exit the vault.
5. After the vault's lock-up period, the withdrawn funds return to the master account.
6. Transfer those funds to a sub-account if they are needed for trading.

Vault withdrawals are based on the number of shares held and may be subject to a lock-up period. A vault withdrawal returns funds to the master account; it is not an immediate external wallet withdrawal.

### Hedging example

A trader wants to run a BTC strategy while reducing its overall market exposure.

They can create two sub-accounts:

* **BTC Directional:** holds the main long BTC position.
* **BTC Hedge:** holds a short BTC position to offset some of the directional exposure.

The master account can allocate funds to both sub-accounts and monitor their performance separately. This makes it easier to see how much profit or loss comes from the main strategy and how much comes from the hedge.

The hedge reduces overall exposure, but margin is still calculated separately for each sub-account. The hedge account must maintain enough collateral for its own position.

### Other examples

#### Market making and directional trading

Use one sub-account for market-making orders and another for directional trades. This keeps inventory, fills, and strategy performance easier to review.

#### Multiple independent strategies

Use separate sub-accounts for trend following, mean reversion, arbitrage, and other strategies. A problem in one strategy does not automatically close positions in another sub-account.

#### Testing and experimentation

Allocate a small balance to a new strategy without mixing its activity with your main trading account.

### Important rules

* The master account owns and manages its sub-accounts.
* Sub-accounts do not have independent wallet identities.
* External deposits and withdrawals are master-account-only.
* Vault deposits and withdrawals are master-account-only.
* Internal transfers are limited to the master account and its own sub-accounts.
* Sub-accounts cannot create additional sub-accounts.
* Margin and liquidation risk are managed separately for each account.