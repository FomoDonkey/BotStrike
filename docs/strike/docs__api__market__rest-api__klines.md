<!-- source: https://docs.strikefinance.org/api/market/rest-api/klines.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/market/rest-api/klines.md).

# Klines

Candlestick / kline chart data

## Kline / Candlestick Data

> Returns candlestick (OHLCV) bars for a symbol.\
> \
> Data is assembled from a cold store (PostgreSQL) and a hot store (Redis).\
> Missing candles between data points are gap-filled with the previous close price and zero volume.\
> \
> \### Price types\
> \
> \| \`priceType\` | Description |\
> \|-------------|-------------|\
> \| \`last\` | Last traded price (default). |\
> \| \`mark\` | Mark price — volume is overlaid from \`last\` klines. |\
> \| \`index\` | Index price — volume is overlaid from \`last\` klines. |\
> \
> \### Response format\
> \
> Each element is a JSON array (Binance-compatible):\
> \
> \| Index | Field | Type |\
> \|-------|-----------------|--------|\
> \| 0 | openTime | int64 |\
> \| 1 | open | string |\
> \| 2 | high | string |\
> \| 3 | low | string |\
> \| 4 | close | string |\
> \| 5 | volume | string |\
> \| 6 | closeTime | int64 |\
> \| 7 | quoteVolume | string |\
> \| 8 | trades | int64 |\
> \| 9 | takerBuyBase | string |\
> \| 10 | takerBuyQuote | string |\
> \| 11 | (unused) | string |\
> \
> \### Caching\
> \
> Responses are cached server-side for \*\*5 seconds\*\* (short-TTL).\
> The \`X-Cache\` response header indicates \`HIT\` or \`MISS\`.<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Market Data API","version":"2.0.0"},"tags":[{"name":"Klines","description":"Candlestick / kline chart data"}],"servers":[{"url":"https://api.strikefinance.org/price","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org/price","description":"Testnet"}],"paths":{"/v2/klines":{"get":{"tags":["Klines"],"summary":"Kline / Candlestick Data","description":"Returns candlestick (OHLCV) bars for a symbol.\n\nData is assembled from a cold store (PostgreSQL) and a hot store (Redis).\nMissing candles between data points are gap-filled with the previous close price and zero volume.\n\n### Price types\n\n| `priceType` | Description |\n|-------------|-------------|\n| `last` | Last traded price (default). |\n| `mark` | Mark price — volume is overlaid from `last` klines. |\n| `index` | Index price — volume is overlaid from `last` klines. |\n\n### Response format\n\nEach element is a JSON array (Binance-compatible):\n\n| Index | Field | Type |\n|-------|-----------------|--------|\n| 0 | openTime | int64 |\n| 1 | open | string |\n| 2 | high | string |\n| 3 | low | string |\n| 4 | close | string |\n| 5 | volume | string |\n| 6 | closeTime | int64 |\n| 7 | quoteVolume | string |\n| 8 | trades | int64 |\n| 9 | takerBuyBase | string |\n| 10 | takerBuyQuote | string |\n| 11 | (unused) | string |\n\n### Caching\n\nResponses are cached server-side for **5 seconds** (short-TTL).\nThe `X-Cache` response header indicates `HIT` or `MISS`.\n","parameters":[{"name":"symbol","in":"query","required":true,"schema":{"type":"string"},"description":"Trading pair symbol."},{"name":"interval","in":"query","required":true,"schema":{"type":"string","enum":["1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"]},"description":"Kline interval."},{"name":"startTime","in":"query","required":false,"schema":{"type":"integer","format":"int64"},"description":"Start time in Unix milliseconds (inclusive). Omit to let the server choose."},{"name":"endTime","in":"query","required":false,"schema":{"type":"integer","format":"int64"},"description":"End time in Unix milliseconds (inclusive). Omit to return up to the latest candle."},{"name":"limit","in":"query","required":false,"schema":{"type":"integer","minimum":1,"maximum":1500,"default":500},"description":"Maximum number of candles to return. Clamped to 1500."},{"name":"priceType","in":"query","required":false,"schema":{"type":"string","enum":["last","mark","index"],"default":"last"},"description":"Price series to use.\n`mark` and `index` klines overlay volume from `last` klines.\n"}],"responses":{"200":{"description":"Array of kline bars","content":{"application/json":{"schema":{"type":"array","items":{"type":"array","items":{},"minItems":12,"maxItems":12},"description":"Each element is a 12-element array: [openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, takerBuyBase, takerBuyQuote, \"0\"]."}}}},"400":{"description":"Missing or invalid parameter","content":{"application/json":{"schema":{"type":"object","properties":{"code":{"type":"string"},"msg":{"type":"string"}}}}}}}}}}}
```