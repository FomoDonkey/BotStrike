"""Daily history for the Strike universe (roadmap P1).

Strike's own klines only go back to 2026-03 (BTC) / 2026-08 (SP500), far too short for a trend
model with 90-day lookbacks. Signals therefore come from an independent daily source (Yahoo
Finance, no API key) while EXECUTION prices, funding and risk always come from Strike.

    py -3.12 scripts/download_daily.py                # every mapped market
    py -3.12 scripts/download_daily.py --only XAU-USD,SP500-USD --years 10

Writes data/daily/<STRIKE_SYMBOL>.parquet with columns timestamp (UTC seconds, session close),
open/high/low/close/volume, plus data/daily/_catalog.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "daily")
CATALOG = os.path.join(OUT_DIR, "_catalog.json")

# Strike market → Yahoo ticker. Crypto uses the spot pair; TradFi uses the front future or index;
# single stocks use their listing. Markets with no public history (pre-IPO / synthetic baskets such
# as SPCX, UNITREE, MINIMAX, ZHIPU, CXMT, DRAM, NIGHT, PUMP) are deliberately absent: they can only
# join the universe once Strike itself has enough history.
YAHOO_MAP: Dict[str, str] = {
    # crypto
    "BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD", "SOL-USD": "SOL-USD", "ADA-USD": "ADA-USD",
    "XRP-USD": "XRP-USD", "BNB-USD": "BNB-USD", "ZEC-USD": "ZEC-USD", "NEAR-USD": "NEAR-USD",
    "HYPE-USD": "HYPE32196-USD",
    # metals, energy, indices
    "XAU-USD": "GC=F", "XAG-USD": "SI=F", "WTI-USD": "CL=F",
    "SP500-USD": "^GSPC", "NAS100-USD": "^NDX",
    # single stocks
    "NVDA-USD": "NVDA", "TSLA-USD": "TSLA", "GOOGL-USD": "GOOGL", "COIN-USD": "COIN",
    "MU-USD": "MU", "SNDK-USD": "SNDK", "CRCL-USD": "CRCL", "AAOI-USD": "AAOI",
    "SKHYNIX-USD": "000660.KS",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) botstrike-research/1.0"


def fetch_yahoo(ticker: str, years: int = 10, attempts: int = 3) -> Optional[pd.DataFrame]:
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(ticker)}?range={years}y&interval=1d")
    last_err = ""
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.loads(r.read())
            res = payload["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            df = pd.DataFrame({"timestamp": [float(t) for t in ts], "open": q["open"], "high": q["high"],
                               "low": q["low"], "close": q["close"], "volume": q.get("volume") or [0] * len(ts)})
            df = df.dropna(subset=["close"]).reset_index(drop=True)
            df["volume"] = df["volume"].fillna(0.0)
            for c in ("open", "high", "low"):
                df[c] = df[c].fillna(df["close"])
            return df if len(df) else None
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (i + 1))
    print(f"  ! {ticker}: {last_err}", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--only", default="", help="comma-separated Strike symbols")
    ap.add_argument("--min-bars", type=int, default=250, help="skip series shorter than this")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    wanted = [s.strip() for s in args.only.split(",") if s.strip()] or sorted(YAHOO_MAP)
    catalog: Dict[str, Dict] = {}
    if os.path.exists(CATALOG):
        try:
            catalog = json.load(open(CATALOG, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            catalog = {}
    ok = skipped = 0
    for sym in wanted:
        ticker = YAHOO_MAP.get(sym)
        if not ticker:
            print(f"{sym}: no daily source mapped — skipped")
            skipped += 1
            continue
        df = fetch_yahoo(ticker, args.years)
        if df is None or len(df) < args.min_bars:
            print(f"{sym} ({ticker}): {0 if df is None else len(df)} bars — skipped")
            skipped += 1
            continue
        path = os.path.join(OUT_DIR, f"{sym}.parquet")
        df.to_parquet(path, index=False)
        first = time.strftime("%Y-%m-%d", time.gmtime(df["timestamp"].iloc[0]))
        last = time.strftime("%Y-%m-%d", time.gmtime(df["timestamp"].iloc[-1]))
        catalog[sym] = {"yahoo": ticker, "bars": int(len(df)), "first": first, "last": last,
                        "downloaded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        print(f"{sym:12s} ({ticker:12s}) {len(df):5d} bars  {first} -> {last}")
        ok += 1
    json.dump(catalog, open(CATALOG, "w", encoding="utf-8"), indent=1)
    print(f"\n{ok} markets written to {OUT_DIR}, {skipped} skipped")
    return 0


def load_daily(symbols: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
    """Read the cached daily frames (used by the research scripts and the multi-asset engine)."""
    out: Dict[str, pd.DataFrame] = {}
    if not os.path.isdir(OUT_DIR):
        return out
    for fn in sorted(os.listdir(OUT_DIR)):
        if not fn.endswith(".parquet"):
            continue
        sym = fn[:-len(".parquet")]
        if symbols and sym not in symbols:
            continue
        try:
            out[sym] = pd.read_parquet(os.path.join(OUT_DIR, fn))
        except Exception:  # noqa: BLE001
            continue
    return out


if __name__ == "__main__":
    raise SystemExit(main())
