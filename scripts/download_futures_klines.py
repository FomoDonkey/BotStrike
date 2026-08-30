#!/usr/bin/env python
"""
Descarga klines (y opcionalmente funding rates) de Binance USDT-M FUTURES.

Motivo: `data/binance_downloader.py` usa la API SPOT (`api.binance.com/api/v3`),
pero el bot opera en USDT-M Futures. Este script usa `fapi.binance.com/fapi/v1`
para que el backtest use la MISMA serie de precios que el live
(`core/market_data.py` tambien usa fapi).

Formato de salida (compatible con `HistoricalDataLoader.load(path, symbol=...)`,
que solo necesita timestamp/open/high/low/close/volume y auto-detecta ms):
    data/binance_futures/klines/<SYMBOL>/<interval>.parquet
    columnas: timestamp(ms, int64), open, high, low, close, volume, close_time,
              quote_volume, trades, taker_buy_base, taker_buy_quote
Funding (opcional, --funding):
    data/binance_futures/funding/<SYMBOL>.parquet
    columnas: timestamp(ms), funding_rate, mark_price

Reanuda desde el ultimo timestamp existente (no re-descarga lo que ya hay).
No requiere API key. Rate limit: fapi permite 2400 weight/min; klines limit=1500
pesa 10 -> dormimos 0.25s entre requests (~240 req/min, muy por debajo).

Uso:
    py -3.12 scripts/download_futures_klines.py --days 150
    py -3.12 scripts/download_futures_klines.py --symbols BTC-USD ETH-USD --days 90 --interval 5m
    py -3.12 scripts/download_futures_klines.py --funding --days 150
    py -3.12 scripts/download_futures_klines.py --verify          # solo inspecciona lo que hay
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests

FAPI_BASE = "https://fapi.binance.com/fapi/v1"
KLINES_URL = f"{FAPI_BASE}/klines"
FUNDING_URL = f"{FAPI_BASE}/fundingRate"

KLINE_LIMIT = 1500          # max por request en fapi
FUNDING_LIMIT = 1000
REQUEST_DELAY = 0.25        # segundos entre requests
MAX_RETRIES = 5

SYMBOL_MAP: Dict[str, str] = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
    "ADA-USD": "ADAUSDT",
    "BNB-USD": "BNBUSDT",
    "XRP-USD": "XRPUSDT",
    "DOGE-USD": "DOGEUSDT",
}

INTERVAL_MS: Dict[str, int] = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

KLINE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
]


def to_binance_symbol(symbol: str) -> str:
    if symbol in SYMBOL_MAP:
        return SYMBOL_MAP[symbol]
    if symbol.endswith("USDT"):
        return symbol
    return symbol.replace("-", "").replace("USD", "") + "USDT"


def _get(session: requests.Session, url: str, params: dict) -> list:
    """GET con reintentos y respeto de 429/418."""
    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(REQUEST_DELAY)
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "10"))
                print(f"    rate-limited (429), esperando {wait}s", flush=True)
                time.sleep(wait)
                continue
            if resp.status_code == 418:
                print("    IP ban temporal (418), esperando 120s", flush=True)
                time.sleep(120)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == MAX_RETRIES - 1:
                raise
            back = 2 ** attempt
            print(f"    error {e!r}, reintento en {back}s", flush=True)
            time.sleep(back)
    return []


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def download_klines(
    session: requests.Session,
    symbol: str,
    days: int,
    interval: str,
    data_dir: str,
    end_ms: Optional[int] = None,
) -> str:
    bsym = to_binance_symbol(symbol)
    step = INTERVAL_MS[interval]
    now_ms = end_ms or int(time.time() * 1000)
    # Solo velas CERRADAS: recortamos al inicio de la vela actual
    now_ms = (now_ms // step) * step
    start_ms = now_ms - days * 86_400_000

    out_dir = os.path.join(data_dir, "klines", symbol)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{interval}.parquet")

    existing: Optional[pd.DataFrame] = None
    if os.path.exists(out_path):
        try:
            existing = pd.read_parquet(out_path)
            last_ts = int(existing["timestamp"].max())
            if last_ts >= start_ms:
                start_ms = last_ts + step
                print(f"  [{symbol}] reanudando desde {_fmt(start_ms)} "
                      f"(ya hay {len(existing):,} velas)", flush=True)
        except Exception as e:  # archivo corrupto -> re-descargar
            print(f"  [{symbol}] no se pudo leer existente ({e!r}), re-descargando", flush=True)
            existing = None

    if start_ms >= now_ms:
        print(f"  [{symbol}] al dia, nada que descargar", flush=True)
        return out_path

    print(f"  [{symbol}] {bsym} {interval}: {_fmt(start_ms)} -> {_fmt(now_ms)}", flush=True)

    rows: List[dict] = []
    cursor = start_ms
    expected = (now_ms - start_ms) // step
    n_req = 0
    while cursor < now_ms:
        data = _get(session, KLINES_URL, {
            "symbol": bsym, "interval": interval,
            "startTime": cursor, "endTime": now_ms - 1, "limit": KLINE_LIMIT,
        })
        n_req += 1
        if not data:
            break
        for k in data:
            open_time = int(k[0])
            if open_time >= now_ms:
                break
            rows.append({
                "timestamp": open_time,
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
                "taker_buy_base": float(k[9]),
                "taker_buy_quote": float(k[10]),
            })
        last_open = int(data[-1][0])
        if last_open < cursor:
            break
        cursor = last_open + step
        if n_req % 10 == 0:
            pct = min(len(rows) / expected * 100, 100) if expected else 100
            print(f"    {len(rows):,}/{expected:,} velas ({pct:.0f}%)", flush=True)

    if not rows:
        print(f"  [{symbol}] sin datos nuevos", flush=True)
        return out_path

    df = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    if existing is not None and not existing.empty:
        # Alinear columnas si el archivo previo tenia otro esquema
        for col in KLINE_COLUMNS:
            if col not in existing.columns:
                existing[col] = pd.NA
        df = pd.concat([existing[KLINE_COLUMNS], df], ignore_index=True)
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = df["timestamp"].astype("int64")
    df.to_parquet(out_path, index=False)

    gaps = int((df["timestamp"].diff().dropna() != step).sum())
    print(f"  [{symbol}] guardado {len(df):,} velas {_fmt(int(df['timestamp'].min()))} -> "
          f"{_fmt(int(df['timestamp'].max()))}  gaps={gaps}  -> {out_path}", flush=True)
    return out_path


def download_funding(
    session: requests.Session, symbol: str, days: int, data_dir: str,
    end_ms: Optional[int] = None,
) -> str:
    bsym = to_binance_symbol(symbol)
    now_ms = end_ms or int(time.time() * 1000)
    start_ms = now_ms - days * 86_400_000
    out_dir = os.path.join(data_dir, "funding")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{symbol}.parquet")

    rows: List[dict] = []
    cursor = start_ms
    while cursor < now_ms:
        data = _get(session, FUNDING_URL, {
            "symbol": bsym, "startTime": cursor, "endTime": now_ms, "limit": FUNDING_LIMIT,
        })
        if not data:
            break
        for f in data:
            rows.append({
                "timestamp": int(f["fundingTime"]),
                "funding_rate": float(f["fundingRate"]),
                "mark_price": float(f.get("markPrice") or 0.0),
            })
        last = int(data[-1]["fundingTime"])
        if last <= cursor:
            break
        cursor = last + 1
        if len(data) < FUNDING_LIMIT:
            break

    if not rows:
        print(f"  [{symbol}] funding: sin datos", flush=True)
        return out_path
    df = pd.DataFrame(rows).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(out_path, index=False)
    fr = df["funding_rate"]
    print(f"  [{symbol}] funding: {len(df)} pagos {_fmt(int(df['timestamp'].min()))} -> "
          f"{_fmt(int(df['timestamp'].max()))}  media={fr.mean()*1e4:.2f} bps/8h  "
          f"min={fr.min()*1e4:.2f}  max={fr.max()*1e4:.2f}  -> {out_path}", flush=True)
    return out_path


def verify(data_dir: str, interval: str) -> None:
    root = os.path.join(data_dir, "klines")
    if not os.path.isdir(root):
        print(f"  no existe {root}")
        return
    for sym in sorted(os.listdir(root)):
        path = os.path.join(root, sym, f"{interval}.parquet")
        if not os.path.exists(path):
            continue
        df = pd.read_parquet(path)
        step = INTERVAL_MS[interval]
        ts = df["timestamp"].astype("int64")
        gaps = int((ts.diff().dropna() != step).sum())
        days = (ts.max() - ts.min()) / 86_400_000
        print(f"  {sym:<9} {len(df):>8,} velas  {_fmt(int(ts.min()))} -> {_fmt(int(ts.max()))}  "
              f"({days:.1f} d)  gaps={gaps}  dup={int(ts.duplicated().sum())}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD"])
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--interval", default="1m", choices=sorted(INTERVAL_MS))
    ap.add_argument("--data-dir", default="data/binance_futures")
    ap.add_argument("--funding", action="store_true", help="descargar tambien funding rates")
    ap.add_argument("--no-klines", action="store_true", help="no descargar klines (solo funding/verify)")
    ap.add_argument("--verify", action="store_true", help="solo inspeccionar lo existente")
    ap.add_argument("--end", type=str, default=None, help="fecha fin UTC (YYYY-MM-DD); default ahora")
    args = ap.parse_args()

    end_ms = None
    if args.end:
        end_ms = int(datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    print("=" * 66)
    print("  Binance USDT-M FUTURES downloader (fapi.binance.com/fapi/v1)")
    print("=" * 66)
    if args.verify:
        verify(args.data_dir, args.interval)
        return 0

    t0 = time.time()
    with requests.Session() as session:
        session.headers["User-Agent"] = "BotStrike-futures-downloader/1.0"
        for sym in args.symbols:
            if not args.no_klines:
                download_klines(session, sym, args.days, args.interval, args.data_dir, end_ms)
            if args.funding:
                download_funding(session, sym, args.days, args.data_dir, end_ms)
    print(f"  listo en {time.time() - t0:.0f}s")
    print("-" * 66)
    verify(args.data_dir, args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
