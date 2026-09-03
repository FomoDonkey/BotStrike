<!-- source: https://docs.strikefinance.org/api/builder-codes/builder-connect.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/builder-codes/builder-connect.md).

# Builder Connect

Public wallet-signature flow that connects a user to a builder. No JWT or prior Strike account required — new users are created automatically.

## Request Signature Challenge

> Returns a challenge message for the user to sign in their wallet\
> (Phantom, Rabby, Nami). This is the first step of the builder\
> connect flow.\
> \
> The dApp generates an Ed25519 keypair client-side, then sends\
> the public key along with the user's wallet address, chain,\
> builder code, fee approval, and an optional referral code.\
> \
> When \`referral\_code\` is supplied, referral attribution is opt-in.\
> The code must be the canonical or customized referral code owned by\
> the account that owns the builder code. Omit it to connect without\
> accepting a referral.\
> \
> \### Validation\
> \
> \- \`chain\` must be \`ethereum\`, \`solana\`, or \`cardano\`\
> \- \`public\_key\` must be exactly 64 hex characters (Ed25519)\
> \- \`code\` must reference an existing, active builder\
> \- \`fee\_share\_bps\` must be between 0 and 100\
> \- Optional \`referral\_code\` must belong to the builder-code owner\
> \
> The returned \`nonce\` expires after 5 minutes.<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Builder API","version":"2.0.0"},"tags":[{"name":"Builder Connect","description":"Public wallet-signature flow that connects a user to a builder. No JWT or prior Strike account required — new users are created automatically."}],"servers":[{"url":"https://api.strikefinance.org","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org","description":"Testnet"}],"paths":{"/auth/builder/request-signature":{"post":{"tags":["Builder Connect"],"summary":"Request Signature Challenge","description":"Returns a challenge message for the user to sign in their wallet\n(Phantom, Rabby, Nami). This is the first step of the builder\nconnect flow.\n\nThe dApp generates an Ed25519 keypair client-side, then sends\nthe public key along with the user's wallet address, chain,\nbuilder code, fee approval, and an optional referral code.\n\nWhen `referral_code` is supplied, referral attribution is opt-in.\nThe code must be the canonical or customized referral code owned by\nthe account that owns the builder code. Omit it to connect without\naccepting a referral.\n\n### Validation\n\n- `chain` must be `ethereum`, `solana`, or `cardano`\n- `public_key` must be exactly 64 hex characters (Ed25519)\n- `code` must reference an existing, active builder\n- `fee_share_bps` must be between 0 and 100\n- Optional `referral_code` must belong to the builder-code owner\n\nThe returned `nonce` expires after 5 minutes.\n","requestBody":{"required":true,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/RequestSignatureRequest"}}}},"responses":{"200":{"description":"Signature challenge created","content":{"application/json":{"schema":{"$ref":"#/components/schemas/RequestSignatureResponse"}}}},"400":{"description":"Validation error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/ErrorResponse"}}}},"404":{"description":"Builder not found","content":{"application/json":{"schema":{"$ref":"#/components/schemas/ErrorResponse"}}}}}}}},"components":{"schemas":{"RequestSignatureRequest":{"type":"object","required":["address","chain","code","fee_share_bps","public_key"],"properties":{"address":{"type":"string","description":"User's wallet address."},"chain":{"type":"string","enum":["ethereum","solana","cardano"],"description":"Blockchain the user's wallet is on. Must be `ethereum`, `solana`, or `cardano`."},"code":{"type":"string","description":"Builder code to connect to. Must exist and be active."},"fee_share_bps":{"type":"integer","minimum":0,"maximum":100,"description":"Maximum fee rate (in basis points) the user approves the builder to charge on trades. Required. Range 0-100 (0% to 1%)."},"public_key":{"type":"string","pattern":"^[0-9a-fA-F]{64}$","description":"Ed25519 public key for the API wallet, encoded as 64 hex characters. The dApp generates this keypair client-side."},"referral_code":{"type":"string","minLength":1,"maxLength":64,"description":"Optional referral attribution. When supplied, this must match the canonical or customized referral code owned by the account that owns `code`. Omit it to connect without accepting a referral."}}},"RequestSignatureResponse":{"type":"object","properties":{"nonce":{"type":"string","format":"uuid","description":"Single-use nonce identifying this challenge. Expires after 5 minutes. Pass this to the verify-signature endpoint."},"message_to_sign":{"type":"string","description":"Canonical human-readable consent statement to sign exactly as returned. It binds the builder code, approved fee, optional referral attribution, wallet, chain, API public key, nonce, and timestamp."},"message":{"type":"string","description":"Display-oriented alias of `message_to_sign`. The wallet signature must cover `message_to_sign` exactly."},"referral_code":{"type":"string","description":"The validated referral code echoed when referral attribution was requested. Omitted when the request did not include `referral_code`."}}},"ErrorResponse":{"type":"object","properties":{"error":{"type":"string","description":"Human-readable error message."}}}}}}
```

## Verify Signature

> Verifies the user's wallet signature against the challenge message\
> and completes the builder connect flow.\
> \
> On success, the backend:\
> 1\. Creates a new Strike account if the user doesn't have one\
> 2\. Applies the validated referral when one was requested and the user\
> &#x20; does not already have a referrer\
> 3\. Approves the builder for this account (idempotent)\
> 4\. Revokes any existing API wallets for this account + builder code\
> 5\. Creates a new API wallet linked to the builder code\
> \
> The nonce is single-use (\`GETDEL\` in Redis) — replaying the same\
> nonce returns a 400 error.\
> \
> \### Signature verification\
> \
> \- \*\*Ethereum:\*\* EIP-191 personal sign\
> \- \*\*Solana:\*\* Ed25519 signature over raw message bytes\
> \- \*\*Cardano:\*\* CIP-30 data sign<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Builder API","version":"2.0.0"},"tags":[{"name":"Builder Connect","description":"Public wallet-signature flow that connects a user to a builder. No JWT or prior Strike account required — new users are created automatically."}],"servers":[{"url":"https://api.strikefinance.org","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org","description":"Testnet"}],"paths":{"/auth/builder/verify-signature":{"post":{"tags":["Builder Connect"],"summary":"Verify Signature","description":"Verifies the user's wallet signature against the challenge message\nand completes the builder connect flow.\n\nOn success, the backend:\n1. Creates a new Strike account if the user doesn't have one\n2. Applies the validated referral when one was requested and the user\n does not already have a referrer\n3. Approves the builder for this account (idempotent)\n4. Revokes any existing API wallets for this account + builder code\n5. Creates a new API wallet linked to the builder code\n\nThe nonce is single-use (`GETDEL` in Redis) — replaying the same\nnonce returns a 400 error.\n\n### Signature verification\n\n- **Ethereum:** EIP-191 personal sign\n- **Solana:** Ed25519 signature over raw message bytes\n- **Cardano:** CIP-30 data sign\n","requestBody":{"required":true,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/VerifySignatureRequest"}}}},"responses":{"200":{"description":"Builder connected successfully. Returns the account ID, builder code, approved fee cap, referral result, and the newly created API wallet details.","content":{"application/json":{"schema":{"$ref":"#/components/schemas/VerifySignatureResponse"}}}},"400":{"description":"Validation or verification error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/ErrorResponse"}}}},"401":{"description":"Signature verification failed","content":{"application/json":{"schema":{"$ref":"#/components/schemas/ErrorResponse"}}}}}}}},"components":{"schemas":{"VerifySignatureRequest":{"type":"object","required":["nonce","wallet_signature"],"properties":{"nonce":{"type":"string","format":"uuid","description":"Nonce from the request-signature response."},"wallet_signature":{"type":"string","description":"The user's wallet signature over the `message_to_sign`. Format depends on chain: hex-encoded for Ethereum, base58-encoded for Solana, CIP-30 COSE format (`{coseSign1Hex}:{coseKeyHex}`) for Cardano."}}},"VerifySignatureResponse":{"type":"object","properties":{"account_id":{"type":"string","description":"The user's Strike account ID. Created automatically if this is a new user."},"nickname":{"type":"string","description":"The user's profile nickname, if set."},"avatar_url":{"type":"string","description":"The user's profile avatar URL, if set."},"builder_code":{"type":"string","description":"The builder code that was approved."},"fee_share_bps":{"type":"integer","description":"The maximum fee rate the user approved (basis points)."},"api_wallet_id":{"type":"integer","description":"ID of the newly created API wallet. Any prior API wallets for this account + builder code are revoked."},"api_wallet_public_key":{"type":"string","description":"Ed25519 public key of the API wallet (64 hex chars). Matches the `public_key` sent in the request-signature step."},"api_wallet_expired_at":{"type":"string","format":"date-time","description":"Expiration timestamp for the API wallet."},"referral":{"$ref":"#/components/schemas/BuilderConnectReferralResult"}}},"BuilderConnectReferralResult":{"type":"object","required":["status"],"properties":{"status":{"type":"string","enum":["accepted","already_accepted","already_set","skipped_self_referral","skipped_circular_referral","not_requested"],"description":"Outcome of optional referral attribution. `already_set` preserves a different existing referrer; skipped statuses do not block builder onboarding; `not_requested` means `referral_code` was omitted."},"code":{"type":"string","description":"Accepted referral code. Present for `accepted` and `already_accepted`; omitted for other statuses."}}},"ErrorResponse":{"type":"object","properties":{"error":{"type":"string","description":"Human-readable error message."}}}}}}
```