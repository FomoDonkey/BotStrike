"""Download 1h spot klines (Binance, no key) for the divergence research. Cache: data/binance_1h/<SYM>.parquet"""
import json, os, sys, time, urllib.request
import pandas as pd
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "binance_1h")
os.makedirs(OUT, exist_ok=True)
SYMS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "BNBUSDT", "XRPUSDT"]
START_MS = int(pd.Timestamp("2022-01-01").timestamp() * 1000)
for sym in SYMS:
    path = os.path.join(OUT, f"{sym}.parquet")
    start = START_MS
    if os.path.exists(path):
        old = pd.read_parquet(path); start = int(old.index[-1].timestamp() * 1000) + 3_600_000
    else:
        old = None
    rows = []
    while True:
        url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&limit=1000&startTime={start}"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    chunk = json.load(r)
                break
            except Exception as e:
                chunk = None; time.sleep(2 * (attempt + 1))
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        start = chunk[-1][0] + 3_600_000
        time.sleep(0.12)
    if rows:
        df = pd.DataFrame(rows, columns=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","tb_base","tb_quote","ignore"])
        df = df[["open_time","open","high","low","close","volume","quote_volume"]].copy()
        for c in ("open","high","low","close","volume","quote_volume"):
            df[c] = df[c].astype(float)
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None)
        df = df.drop(columns=["open_time"]).set_index("ts").sort_index()
        if old is not None:
            df = pd.concat([old, df]); df = df[~df.index.duplicated(keep="last")].sort_index()
        df.to_parquet(path)
        print(sym, len(df), df.index[0], "->", df.index[-1])
    else:
        print(sym, "no new rows")
