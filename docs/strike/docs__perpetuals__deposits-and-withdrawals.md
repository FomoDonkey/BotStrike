<!-- source: https://docs.strikefinance.org/perpetuals/deposits-and-withdrawals.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/perpetuals/deposits-and-withdrawals.md).

# Deposits & Withdrawals

Strike uses a validator-based system for all asset transfers between on-chain wallets and Strike accounts. Every deposit and withdrawal is quoted, signed, and verified by the Strike validator network before any balance is updated.

### How It Works

All transfers go through a quote-validate-settle lifecycle. This ensures that every movement of funds is cryptographically verified and settled on-chain.

#### Deposits

1. **Request a Quote** — A user requests a deposit quote from the validator network, specifying the asset and amount. The validators return a signed quote containing the deposit address, required confirmations, and a quote expiration window.
2. **Submit On-Chain Transaction** — The user sends assets to the protocol's locker contract on the respective blockchain (Cardano, Ethereum, etc.). The funds are locked in the smart contract.
3. **Validator Verification** — Strike validators independently monitor the locker contract for incoming deposit transactions. Once detected, validators verify:
* The funds were locked in the correct locker contract
* The amount matches the signed quote
* The quote has not expired
* The required number of block confirmations has been reached
4. **Balance Credit** — After validator consensus, the corresponding USD value is credited to the user's Strike account.

#### Withdrawals

1. **Request a Quote** — The user requests a withdrawal quote specifying the amount, destination blockchain, recipient address, and asset. Validators verify the protocol has sufficient liquidity and return a signed message for the user to authorize.
2. **Sign with Wallet** — The user signs the withdrawal message using their connected wallet. This proves ownership and authorizes the transfer — no assets move without the user's cryptographic signature.
3. **Validator Verification** — Validators verify the wallet signature, confirm the quote is still valid, and check that the withdrawal will not put the account below margin requirements. The withdrawal amount is deducted from the user's balance.
4. **On-Chain Settlement** — Validators construct and submit the withdrawal transaction on-chain, sending the assets to the user's specified recipient address. The transaction is finalized once confirmed on the respective blockchain.

### Multi-Chain Support

Strike validators support deposits and withdrawals across multiple blockchains. Each chain has its own dedicated listener node that monitors for relevant transactions in real time. Currently supported chains include Cardano and Ethereum, with more chains planned.

### Security Model

* **Quote Signing** — Every quote is cryptographically signed by the validator network. Unsigned or tampered quotes are rejected.
* **Wallet Signatures** — Withdrawals require a wallet signature from the account owner. No withdrawal can be initiated without the user explicitly signing the authorization message.
* **Confirmation Thresholds** — Deposits require a configurable number of block confirmations before being credited, protecting against chain reorganizations.
* **Replay Protection** — Each quote is single-use. Once a quote is consumed or expires, it cannot be reused.
* **Margin Safety** — Withdrawal amounts are validated against the user's margin requirements to prevent liquidation risk.

### Validator Network

Strike is launching with Strike-operated validator nodes handling all quote signing, transaction monitoring, and on-chain settlement. This allows us to ship a performant and secure system while maintaining strict control over the verification process during the early stages of the protocol.

Over time, the validator set will be progressively opened to third-party node operators. The goal is a fully decentralized validator network where multiple independent parties participate in verifying deposits, signing quotes, and settling withdrawals — removing any single point of trust from the transfer lifecycle.