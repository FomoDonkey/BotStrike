"""Daily-candle sources for the trend engine (roadmap P1: multi-asset).

The trend engine needs long daily history; the venue does not have it (Strike's own klines start
2026-03). So SIGNALS come from an independent daily source per market, and EXECUTION always happens
at the venue's price:

    BTCUSDT, ETHUSDT, ...   -> Binance spot REST (free, since 2017)
    XAU-USD, SP500-USD, ... -> Yahoo Finance daily (free, 10+ years)

`fetch_daily_any` dispatches on the symbol shape and returns exactly what the engine's cache
expects: a DataFrame indexed by UTC midnight with open/high/low/close/volume/quote_volume, the last
row possibly being today's forming candle.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional

import pandas as pd

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) botstrike/1.0"

# Strike market -> Yahoo ticker (same map as scripts/download_daily.py; kept here so the runtime
# has no dependency on the research scripts).
YAHOO_MAP: Dict[str, str] = {
    "XAU-USD": "GC=F", "XAG-USD": "SI=F", "WTI-USD": "CL=F",
    "SP500-USD": "^GSPC", "NAS100-USD": "^NDX",
    "NVDA-USD": "NVDA", "TSLA-USD": "TSLA", "GOOGL-USD": "GOOGL", "COIN-USD": "COIN",
    "MU-USD": "MU", "SNDK-USD": "SNDK", "CRCL-USD": "CRCL", "AAOI-USD": "AAOI",
    "SKHYNIX-USD": "000660.KS",
    # crypto fallbacks (only used if a symbol is written in Strike form)
    "BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD", "SOL-USD": "SOL-USD", "ADA-USD": "ADA-USD",
    "XRP-USD": "XRP-USD", "BNB-USD": "BNB-USD", "ZEC-USD": "ZEC-USD", "NEAR-USD": "NEAR-USD",
    "HYPE-USD": "HYPE32196-USD",
}


def is_yahoo_symbol(symbol: str) -> bool:
    """True for the Strike-style markets whose history must come from Yahoo."""
    return symbol.upper() in YAHOO_MAP and not symbol.upper().endswith("USDT")


def fetch_daily_yahoo(symbol: str, start_ms: int = 0, timeout: float = 30.0,
                      attempts: int = 3) -> Optional[pd.DataFrame]:
    """Daily candles for a Strike market from Yahoo. `start_ms` only trims the result: Yahoo is
    asked for the full 10-year range so a cold cache is filled in one call."""
    ticker = YAHOO_MAP.get(symbol.upper())
    if not ticker:
        return None
    url = f"{YAHOO_URL}{urllib.parse.quote(ticker)}?range=10y&interval=1d"
    last_err: Optional[Exception] = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read())
            res = payload["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            # Bars are dated by the EXCHANGE's calendar day (Yahoo stamps a futures day at 00:00
            # New York), and the bar of the CURRENT exchange day is never trusted: while the day's
            # session runs it is forming, and from the evening Globex open (18:00 ET) until
            # midnight ET Yahoo shows the NEXT session's live prints under the CURRENT date. The
            # CT cached silver's "3 Sep" as o 67.63 h 67.69 l 67.51 c 67.59 — one hour of the
            # 4 Sep session — and doubled the position on a breakout that never printed
            # (2026-09-05). The settled bar for a day is only stable after midnight ET.
            tz = str((res.get("meta") or {}).get("exchangeTimezoneName") or "America/New_York")
            stamps = pd.to_datetime([int(t) for t in ts], unit="s", utc=True).tz_convert(tz).normalize()
            today_local = pd.Timestamp.now(tz=tz).normalize()
            df = pd.DataFrame({
                "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                "volume": [v or 0.0 for v in (q.get("volume") or [0] * len(ts))],
            }, index=stamps.tz_localize(None))
            df = df[stamps < today_local]
            df = df.dropna(subset=["close"])
            for c in ("open", "high", "low"):
                df[c] = df[c].fillna(df["close"])
            # the engine ranks the universe by dollar volume; indices report index volume, which is
            # not comparable, so quote_volume is close*volume and only used for ordering
            df["quote_volume"] = df["close"] * df["volume"]
            df = df[~df.index.duplicated(keep="first")].sort_index()   # the settled bar, never a live repeat
            if start_ms:
                df = df[df.index >= pd.Timestamp(start_ms, unit="ms").normalize()]
            return df if len(df) else None
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"yahoo fetch failed for {symbol} ({ticker}): {type(last_err).__name__}: {last_err}")


def make_fetcher(binance_fetcher):
    """Return a fetcher(symbol, start_ms) that routes each symbol to its source."""

    def fetch_daily_any(symbol: str, start_ms: int = 0, **kw):
        if is_yahoo_symbol(symbol):
            return fetch_daily_yahoo(symbol, start_ms)
        return binance_fetcher(symbol, start_ms, **kw)

    return fetch_daily_any
