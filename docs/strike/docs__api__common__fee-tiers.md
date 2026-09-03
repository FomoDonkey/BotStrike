<!-- source: https://docs.strikefinance.org/api/common/fee-tiers.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/common/fee-tiers.md).

# Fee Tiers

Trading fee schedule based on 30-day volume

## Fee Tiers

> Returns the global fee tier schedule. Each tier specifies a minimum 30-day trailing volume (USD) and the corresponding maker / taker fee rates.\
> \
> \### How fee tiers work\
> \
> Your fee tier is determined by your \*\*trailing 30-day trading volume\*\* (in USD). The highest tier whose \`minVolume\` threshold you meet is applied to all subsequent trades.\
> \
> \| Field | Type | Description |\
> \|-------------|---------|-------------|\
> \| \`tier\` | integer | Tier number (0 = base tier). |\
> \| \`minVolume\` | number | Minimum 30-day volume (USD) to qualify for this tier. |\
> \| \`takerRate\` | number | Taker fee rate (e.g. \`0.0005\` = 0.05 %). |\
> \| \`makerRate\` | number | Maker fee rate (e.g. \`0.0002\` = 0.02 %). |\
> \
> \> \*\*Note:\*\* Fee rates are expressed as decimals, not percentages.\
> \> Multiply by 100 to convert (e.g. \`0.0005\` → 0.05 %).<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Common API","version":"2.0.0"},"tags":[{"name":"Fee Tiers","description":"Trading fee schedule based on 30-day volume"}],"servers":[{"url":"https://api.strikefinance.org","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org","description":"Testnet"}],"paths":{"/v2/fee-tiers":{"get":{"tags":["Fee Tiers"],"summary":"Fee Tiers","description":"Returns the global fee tier schedule. Each tier specifies a minimum 30-day trailing volume (USD) and the corresponding maker / taker fee rates.\n\n### How fee tiers work\n\nYour fee tier is determined by your **trailing 30-day trading volume** (in USD). The highest tier whose `minVolume` threshold you meet is applied to all subsequent trades.\n\n| Field | Type | Description |\n|-------------|---------|-------------|\n| `tier` | integer | Tier number (0 = base tier). |\n| `minVolume` | number | Minimum 30-day volume (USD) to qualify for this tier. |\n| `takerRate` | number | Taker fee rate (e.g. `0.0005` = 0.05 %). |\n| `makerRate` | number | Maker fee rate (e.g. `0.0002` = 0.02 %). |\n\n> **Note:** Fee rates are expressed as decimals, not percentages.\n> Multiply by 100 to convert (e.g. `0.0005` → 0.05 %).\n","responses":{"200":{"description":"Fee tier schedule","content":{"application/json":{"schema":{"$ref":"#/components/schemas/FeeTiersResponse"}}}}}}}},"components":{"schemas":{"FeeTiersResponse":{"type":"object","properties":{"feeTiers":{"type":"array","items":{"$ref":"#/components/schemas/FeeTier"},"description":"List of fee tiers sorted by ascending volume threshold."}}},"FeeTier":{"type":"object","properties":{"tier":{"type":"integer","description":"Tier number (0 = base tier)."},"minVolume":{"type":"number","description":"Minimum 30-day trailing volume (USD) to qualify."},"takerRate":{"type":"number","description":"Taker fee rate as a decimal (e.g. `0.0005` = 0.05 %)."},"makerRate":{"type":"number","description":"Maker fee rate as a decimal (e.g. `0.0002` = 0.02 %)."}}}}}}
```