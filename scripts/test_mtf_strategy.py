"""Test rápido de estrategia MTF microstructure en 5m bars."""
import sys, warnings, os
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

import numpy as np
np.seterr(all="ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from core.indicators import Indicators

df = pd.read_parquet("data/binance/klines/BTC-USD/1m.parquet")
df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
ts_unit = "ms" if df["timestamp"].max() > 1e12 else "s"
df_idx = df.set_index(pd.to_datetime(df["timestamp"], unit=ts_unit))

# Build timeframes
df_5m = df_idx.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
df_5m = Indicators.compute_all(df_5m.reset_index(drop=True))

df_15m = df_idx.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
df_15m = Indicators.compute_all(df_15m.reset_index(drop=True))

df_1h = df_idx.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
df_1h = Indicators.compute_all(df_1h.reset_index(drop=True))

# Forward fill higher TF RSI to 5m
ts_5m = pd.to_datetime(df_idx.resample("5min").first().dropna().index)
ts_15m = pd.to_datetime(df_idx.resample("15min").first().dropna().index)
ts_1h = pd.to_datetime(df_idx.resample("1h").first().dropna().index)

for col in ["rsi"]:
    s15 = pd.Series(df_15m[col].values, index=ts_15m[:len(df_15m)])
    df_5m[f"{col}_15m"] = s15.reindex(ts_5m[:len(df_5m)], method="ffill").values
    s1h = pd.Series(df_1h[col].values, index=ts_1h[:len(df_1h)])
    df_5m[f"{col}_1h"] = s1h.reindex(ts_5m[:len(df_5m)], method="ffill").values

# Backtest
capital = 300.0
leverage = 3
trades = []
position = None
trailing_sl = None

for i in range(100, len(df_5m)):
    row = df_5m.iloc[i]
    price = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    atr = float(row["atr"]) if not pd.isna(row["atr"]) else 0
    rsi_5m = float(row["rsi"]) if not pd.isna(row["rsi"]) else 50
    rsi_15m = float(row.get("rsi_15m", 50)) if not pd.isna(row.get("rsi_15m", 50)) else 50
    rsi_1h = float(row.get("rsi_1h", 50)) if not pd.isna(row.get("rsi_1h", 50)) else 50
    if atr <= 0 or capital <= 50:
        continue

    sl_mult = 2.0
    tp_mult = 3.0

    # Manage position
    if position:
        if position["side"] == "LONG":
            profit = (price - position["entry"]) * position["size"]
            if profit > position["atr_e"] * position["size"]:
                trailing_sl = max(trailing_sl or position["sl"], position["entry"] + 0.01)
            eff_sl = trailing_sl or position["sl"]
            if low <= eff_sl:
                pnl = (eff_sl - position["entry"]) * position["size"]
                fee = (position["entry"] + abs(eff_sl)) * position["size"] * 0.0005
                capital += pnl - fee
                trades.append({"pnl": pnl - fee, "side": "LONG", "exit": "SL"})
                position = None; trailing_sl = None; continue
            if high >= position["tp"]:
                pnl = (position["tp"] - position["entry"]) * position["size"]
                fee = (position["entry"] + position["tp"]) * position["size"] * 0.0005
                capital += pnl - fee
                trades.append({"pnl": pnl - fee, "side": "LONG", "exit": "TP"})
                position = None; trailing_sl = None; continue
        else:
            profit = (position["entry"] - price) * position["size"]
            if profit > position["atr_e"] * position["size"]:
                trailing_sl = min(trailing_sl or position["sl"], position["entry"] - 0.01)
            eff_sl = trailing_sl or position["sl"]
            if high >= eff_sl:
                pnl = (position["entry"] - eff_sl) * position["size"]
                fee = (position["entry"] + abs(eff_sl)) * position["size"] * 0.0005
                capital += pnl - fee
                trades.append({"pnl": pnl - fee, "side": "SHORT", "exit": "SL"})
                position = None; trailing_sl = None; continue
            if low <= position["tp"]:
                pnl = (position["entry"] - position["tp"]) * position["size"]
                fee = (position["entry"] + position["tp"]) * position["size"] * 0.0005
                capital += pnl - fee
                trades.append({"pnl": pnl - fee, "side": "SHORT", "exit": "TP"})
                position = None; trailing_sl = None; continue

    if position is not None:
        continue

    # ENTRY: MTF RSI alignment
    go_long = rsi_1h < 40 and rsi_15m < 35 and rsi_5m < 35
    go_short = rsi_1h > 60 and rsi_15m > 65 and rsi_5m > 65

    if go_long:
        sl = price - sl_mult * atr
        tp = price + tp_mult * atr
        rpu = price - sl
        size = min(capital * 0.01 / rpu, (capital * leverage) / price) if rpu > 0 else 0
        if size > 0:
            position = {"entry": price, "size": size, "sl": sl, "tp": tp, "side": "LONG", "atr_e": atr}
            trailing_sl = None
    elif go_short:
        sl = price + sl_mult * atr
        tp = price - tp_mult * atr
        rpu = sl - price
        size = min(capital * 0.01 / rpu, (capital * leverage) / price) if rpu > 0 else 0
        if size > 0:
            position = {"entry": price, "size": size, "sl": sl, "tp": tp, "side": "SHORT", "atr_e": atr}
            trailing_sl = None

# Close EOD
if position:
    if position["side"] == "LONG":
        pnl = (price - position["entry"]) * position["size"]
    else:
        pnl = (position["entry"] - price) * position["size"]
    capital += pnl - abs(pnl) * 0.001
    trades.append({"pnl": pnl, "side": position["side"], "exit": "EOD"})

wins = [t for t in trades if t["pnl"] > 0]
losses = [t for t in trades if t["pnl"] <= 0]
longs = [t for t in trades if t["side"] == "LONG"]
shorts = [t for t in trades if t["side"] == "SHORT"]
total_pnl = sum(t["pnl"] for t in trades)
wr = len(wins) / len(trades) * 100 if trades else 0

print("MTF MICROSTRUCTURE STRATEGY - 90d BTC (5m bars)")
print("=" * 55)
print(f"Trades: {len(trades)} (L:{len(longs)} S:{len(shorts)})")
print(f"WR: {wr:.1f}%")
print(f"PnL: ${total_pnl:+,.2f} ({total_pnl/300*100:+.1f}%)")
print(f"Equity: $300 -> ${capital:,.2f}")
if wins:
    print(f"Avg win: ${np.mean([t['pnl'] for t in wins]):+,.2f}")
if losses:
    print(f"Avg loss: ${np.mean([t['pnl'] for t in losses]):+,.2f}")
print(f"Long PnL: ${sum(t['pnl'] for t in longs):+,.2f}")
print(f"Short PnL: ${sum(t['pnl'] for t in shorts):+,.2f}")
print()
for t in trades[:20]:
    print(f"  {'W' if t['pnl']>0 else 'L'} ${t['pnl']:+.2f} {t['side']:5s} {t.get('exit','?')}")
if len(trades) > 20:
    print(f"  ... +{len(trades)-20} more")
