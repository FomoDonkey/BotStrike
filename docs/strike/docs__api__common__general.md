<!-- source: https://docs.strikefinance.org/api/common/general.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/common/general.md).

# General

Connectivity test and server time

## Test Connectivity

> Test connectivity to the API server. Returns an empty JSON object.\
> \
> Use this endpoint to verify that your client can reach the server\
> and that the API is operational.<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Common API","version":"2.0.0"},"tags":[{"name":"General","description":"Connectivity test and server time"}],"servers":[{"url":"https://api.strikefinance.org","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org","description":"Testnet"}],"paths":{"/v2/ping":{"get":{"tags":["General"],"summary":"Test Connectivity","description":"Test connectivity to the API server. Returns an empty JSON object.\n\nUse this endpoint to verify that your client can reach the server\nand that the API is operational.\n","responses":{"200":{"description":"API is reachable","content":{"application/json":{"schema":{"$ref":"#/components/schemas/PingResponse"}}}}}}}},"components":{"schemas":{"PingResponse":{"type":"object","description":"Empty JSON object. Confirms the API is reachable."}}}}
```

## Server Time

> Returns the current server time as a Unix timestamp in milliseconds.\
> \
> Use this endpoint to synchronise your local clock with the server.\
> This is useful for time-sensitive operations such as signing requests.<br>

```json
{"openapi":"3.0.3","info":{"title":"Strike Common API","version":"2.0.0"},"tags":[{"name":"General","description":"Connectivity test and server time"}],"servers":[{"url":"https://api.strikefinance.org","description":"Mainnet"},{"url":"https://api-v2-testnet.strikefinance.org","description":"Testnet"}],"paths":{"/v2/time":{"get":{"tags":["General"],"summary":"Server Time","description":"Returns the current server time as a Unix timestamp in milliseconds.\n\nUse this endpoint to synchronise your local clock with the server.\nThis is useful for time-sensitive operations such as signing requests.\n","responses":{"200":{"description":"Current server time","content":{"application/json":{"schema":{"$ref":"#/components/schemas/TimeResponse"}}}}}}}},"components":{"schemas":{"TimeResponse":{"type":"object","properties":{"serverTime":{"type":"integer","format":"int64","description":"Current server time (Unix milliseconds)."}}}}}}
```