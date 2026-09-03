<!-- source: https://docs.strikefinance.org/api/platform-stats/coin-history.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/platform-stats/coin-history.md).

# Coin History

Time-series history data per symbol (cached)

## Open Interest History

> Returns open interest and volume history for a symbol, aggregated\
> by the specified interval. Cached server-side (default 3 minutes).\
> \
> Time range = max\_points x interval\_duration.<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Perpetuals - Statistics API","version":"1.0.0"},"tags":[{"name":"Coin History","description":"Time-series history data per symbol (cached)"}],"servers":[{"url":"https://api.strikefinance.org/stat","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org/stat","description":"Testnet"}],"paths":{"/v1/stats/coin/history/open-interest":{"get":{"tags":["Coin History"],"summary":"Open Interest History","description":"Returns open interest and volume history for a symbol, aggregated\nby the specified interval. Cached server-side (default 3 minutes).\n\nTime range = max_points x interval_duration.\n","parameters":[{"name":"symbol","in":"query","required":true,"schema":{"type":"string"},"description":"Symbol to query."},{"name":"interval","in":"query","required":false,"schema":{"type":"string","enum":["10m","15m","30m","1h","12h","1d"],"default":"10m"},"description":"Aggregation interval."}],"responses":{"200":{"description":"Open interest history","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HistoryResponse"}}}},"400":{"description":"Missing symbol","content":{"application/json":{}}}}}}},"components":{"schemas":{"HistoryResponse":{"type":"object","description":"Time-series response. `columns` describes the fields in each row of `data`.\nEach row in `data` is an array matching the column order.\n","properties":{"symbol":{"type":"string"},"interval":{"type":"string"},"days":{"type":"integer"},"columns":{"type":"array","items":{"type":"string"}},"data":{"type":"array","items":{"type":"array"}}}}}}}
```

## Funding Rate History

> Returns historical funding rates at 8-hour intervals for a symbol.\
> Cached server-side (default 3 minutes).<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Perpetuals - Statistics API","version":"1.0.0"},"tags":[{"name":"Coin History","description":"Time-series history data per symbol (cached)"}],"servers":[{"url":"https://api.strikefinance.org/stat","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org/stat","description":"Testnet"}],"paths":{"/v1/stats/coin/history/funding":{"get":{"tags":["Coin History"],"summary":"Funding Rate History","description":"Returns historical funding rates at 8-hour intervals for a symbol.\nCached server-side (default 3 minutes).\n","parameters":[{"name":"symbol","in":"query","required":true,"schema":{"type":"string"},"description":"Symbol to query."},{"name":"days","in":"query","required":false,"schema":{"type":"integer","minimum":1,"maximum":90,"default":30},"description":"Number of days of history."}],"responses":{"200":{"description":"Funding rate history","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HistoryResponse"}}}},"400":{"description":"Missing symbol"}}}}},"components":{"schemas":{"HistoryResponse":{"type":"object","description":"Time-series response. `columns` describes the fields in each row of `data`.\nEach row in `data` is an array matching the column order.\n","properties":{"symbol":{"type":"string"},"interval":{"type":"string"},"days":{"type":"integer"},"columns":{"type":"array","items":{"type":"string"}},"data":{"type":"array","items":{"type":"array"}}}}}}}
```

## Basis Rate History

> Returns mark price, index price, and basis (mark - index) history\
> for a symbol. Cached server-side (default 3 minutes).<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Perpetuals - Statistics API","version":"1.0.0"},"tags":[{"name":"Coin History","description":"Time-series history data per symbol (cached)"}],"servers":[{"url":"https://api.strikefinance.org/stat","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org/stat","description":"Testnet"}],"paths":{"/v1/stats/coin/history/basis":{"get":{"tags":["Coin History"],"summary":"Basis Rate History","description":"Returns mark price, index price, and basis (mark - index) history\nfor a symbol. Cached server-side (default 3 minutes).\n","parameters":[{"name":"symbol","in":"query","required":true,"schema":{"type":"string"},"description":"Symbol to query."},{"name":"interval","in":"query","required":false,"schema":{"type":"string","enum":["10m","15m","30m","1h","12h","1d"],"default":"10m"},"description":"Aggregation interval."}],"responses":{"200":{"description":"Basis rate history","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HistoryResponse"}}}},"400":{"description":"Missing symbol"}}}}},"components":{"schemas":{"HistoryResponse":{"type":"object","description":"Time-series response. `columns` describes the fields in each row of `data`.\nEach row in `data` is an array matching the column order.\n","properties":{"symbol":{"type":"string"},"interval":{"type":"string"},"days":{"type":"integer"},"columns":{"type":"array","items":{"type":"string"}},"data":{"type":"array","items":{"type":"array"}}}}}}}
```

## Bid-Ask Spread History

> Returns bid-ask spread and ratio history for a symbol.\
> \
> \- \`spread\` = best\_ask - best\_bid\
> \- \`ratio\` = (spread / mid\_price) x 100\
> \
> Cached server-side (default 3 minutes).<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Perpetuals - Statistics API","version":"1.0.0"},"tags":[{"name":"Coin History","description":"Time-series history data per symbol (cached)"}],"servers":[{"url":"https://api.strikefinance.org/stat","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org/stat","description":"Testnet"}],"paths":{"/v1/stats/coin/history/spread":{"get":{"tags":["Coin History"],"summary":"Bid-Ask Spread History","description":"Returns bid-ask spread and ratio history for a symbol.\n\n- `spread` = best_ask - best_bid\n- `ratio` = (spread / mid_price) x 100\n\nCached server-side (default 3 minutes).\n","parameters":[{"name":"symbol","in":"query","required":true,"schema":{"type":"string"},"description":"Symbol to query."},{"name":"interval","in":"query","required":false,"schema":{"type":"string","enum":["10m","15m","30m","1h","12h","1d"],"default":"10m"},"description":"Aggregation interval."}],"responses":{"200":{"description":"Bid-ask spread history","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HistoryResponse"}}}},"400":{"description":"Missing symbol"}}}}},"components":{"schemas":{"HistoryResponse":{"type":"object","description":"Time-series response. `columns` describes the fields in each row of `data`.\nEach row in `data` is an array matching the column order.\n","properties":{"symbol":{"type":"string"},"interval":{"type":"string"},"days":{"type":"integer"},"columns":{"type":"array","items":{"type":"string"}},"data":{"type":"array","items":{"type":"array"}}}}}}}
```

## OI / Market Cap Ratio History

> Returns open interest, market cap, and price history for a symbol.\
> Market cap is calculated as \`circulating\_supply x mark\_price\`.\
> Cached server-side (default 3 minutes).<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Perpetuals - Statistics API","version":"1.0.0"},"tags":[{"name":"Coin History","description":"Time-series history data per symbol (cached)"}],"servers":[{"url":"https://api.strikefinance.org/stat","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org/stat","description":"Testnet"}],"paths":{"/v1/stats/coin/history/oi-marketcap-ratio":{"get":{"tags":["Coin History"],"summary":"OI / Market Cap Ratio History","description":"Returns open interest, market cap, and price history for a symbol.\nMarket cap is calculated as `circulating_supply x mark_price`.\nCached server-side (default 3 minutes).\n","parameters":[{"name":"symbol","in":"query","required":true,"schema":{"type":"string"},"description":"Symbol to query."},{"name":"interval","in":"query","required":false,"schema":{"type":"string","enum":["10m","15m","30m","1h","12h","1d"],"default":"10m"},"description":"Aggregation interval."}],"responses":{"200":{"description":"OI / Market Cap ratio history","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HistoryResponse"}}}},"400":{"description":"Missing symbol"}}}}},"components":{"schemas":{"HistoryResponse":{"type":"object","description":"Time-series response. `columns` describes the fields in each row of `data`.\nEach row in `data` is an array matching the column order.\n","properties":{"symbol":{"type":"string"},"interval":{"type":"string"},"days":{"type":"integer"},"columns":{"type":"array","items":{"type":"string"}},"data":{"type":"array","items":{"type":"array"}}}}}}}
```

## Long / Short Ratio History

> Returns long vs short position ratio history by value and by account count.\
> Cached server-side (default 3 minutes).<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Perpetuals - Statistics API","version":"1.0.0"},"tags":[{"name":"Coin History","description":"Time-series history data per symbol (cached)"}],"servers":[{"url":"https://api.strikefinance.org/stat","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org/stat","description":"Testnet"}],"paths":{"/v1/stats/coin/history/long-short-ratio":{"get":{"tags":["Coin History"],"summary":"Long / Short Ratio History","description":"Returns long vs short position ratio history by value and by account count.\nCached server-side (default 3 minutes).\n","parameters":[{"name":"symbol","in":"query","required":true,"schema":{"type":"string"},"description":"Symbol to query."},{"name":"interval","in":"query","required":false,"schema":{"type":"string","enum":["10m","15m","30m","1h","12h","1d"],"default":"10m"},"description":"Aggregation interval."}],"responses":{"200":{"description":"Long / short ratio history","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HistoryResponse"}}}},"400":{"description":"Missing symbol"}}}}},"components":{"schemas":{"HistoryResponse":{"type":"object","description":"Time-series response. `columns` describes the fields in each row of `data`.\nEach row in `data` is an array matching the column order.\n","properties":{"symbol":{"type":"string"},"interval":{"type":"string"},"days":{"type":"integer"},"columns":{"type":"array","items":{"type":"string"}},"data":{"type":"array","items":{"type":"array"}}}}}}}
```

## Top Trader Long / Short Ratio History

> Same as long/short ratio but filtered to the top 20% of traders by position size.\
> Cached server-side (default 3 minutes).<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Perpetuals - Statistics API","version":"1.0.0"},"tags":[{"name":"Coin History","description":"Time-series history data per symbol (cached)"}],"servers":[{"url":"https://api.strikefinance.org/stat","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org/stat","description":"Testnet"}],"paths":{"/v1/stats/coin/history/top-trader-long-short-ratio":{"get":{"tags":["Coin History"],"summary":"Top Trader Long / Short Ratio History","description":"Same as long/short ratio but filtered to the top 20% of traders by position size.\nCached server-side (default 3 minutes).\n","parameters":[{"name":"symbol","in":"query","required":true,"schema":{"type":"string"},"description":"Symbol to query."},{"name":"interval","in":"query","required":false,"schema":{"type":"string","enum":["10m","15m","30m","1h","12h","1d"],"default":"10m"},"description":"Aggregation interval."}],"responses":{"200":{"description":"Top trader long / short ratio history","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HistoryResponse"}}}},"400":{"description":"Missing symbol"}}}}},"components":{"schemas":{"HistoryResponse":{"type":"object","description":"Time-series response. `columns` describes the fields in each row of `data`.\nEach row in `data` is an array matching the column order.\n","properties":{"symbol":{"type":"string"},"interval":{"type":"string"},"days":{"type":"integer"},"columns":{"type":"array","items":{"type":"string"}},"data":{"type":"array","items":{"type":"array"}}}}}}}
```