"""
Backend de datos en tiempo real para el dashboard.
Lee WebSocket de Binance + logs del paper trading.
"""
import json
import os
import re
import time
import threading
from collections import deque
from datetime import datetime, timezone

import pandas as pd
import numpy as np

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "logs", "paper_binance.log")

# Singleton data store
_store = {
    "price": 0.0,
    "prices_1s": deque(maxlen=300),
    "candles_1m": deque(maxlen=100),
    "volume_buy": 0.0,
    "volume_sell": 0.0,
    "tps": 0,
    "whale_trades": deque(maxlen=50),
    "whale_buy_vol": 0.0,
    "whale_sell_vol": 0.0,
    "whale_buy_count": 0,
    "whale_sell_count": 0,
    "regime": "...",
    "adx": 0.0,
    "rsi": 50.0,
    "momentum": 0.0,
    "vol_pct": 0.5,
    "equity": 300.0,
    "pnl": 0.0,
    "pnl_history": deque(maxlen=500),  # (timestamp, pnl) every 30s
    "equity_history": deque(maxlen=500),
    "total_trades": 0,
    "win_rate": 0.0,
    "runtime": 0.0,
    "sharpe": 0.0,
    "max_dd": 0.0,
    "fees": 0.0,
    "bot_trades": deque(maxlen=50),
    "open_positions": [],  # [{side, entry, sl, tp, size, strategy, time}]
    "floating_pnl": 0.0,
    "signals_generated": deque(maxlen=30),
    "signals_exits": 0,
    "signals_blocked": 0,
    "signals_validated": 0,
    "ws_connected": False,
    "bars_15m": 0,
    "daily_trend": "...",
    "_ema_prices": [],
    "high_24h": 0.0,
    "low_24h": 999999.0,
    "change_24h": 0.0,
    "_candle": {"o": 0, "h": 0, "l": 999999, "c": 0, "v": 0, "t": 0},
    "_tps_count": 0,
    "_tps_reset": time.time(),
    "_log_pos": 0,  # Read entire log on init to catch all trades/signals
    "_ws_thread": None,
}


def get_store():
    return _store


def start_ws_feed():
    """Inicia WebSocket de Binance en background thread."""
    if _store["_ws_thread"] and _store["_ws_thread"].is_alive():
        return

    def _run():
        import asyncio
        import websockets

        async def _connect():
            url = "wss://stream.binance.com:9443/stream?streams=btcusdt@trade/btcusdt@miniTicker"
            while True:
                try:
                    async with websockets.connect(url, ping_interval=20) as ws:
                        _store["ws_connected"] = True
                        async for msg in ws:
                            data = json.loads(msg).get("data", {})
                            e = data.get("e", "")
                            if e == "trade":
                                _on_trade(data)
                            elif e == "24hrMiniTicker":
                                _on_ticker(data)
                except Exception:
                    _store["ws_connected"] = False
                    await asyncio.sleep(2)

        asyncio.run(_connect())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    _store["_ws_thread"] = t


def _on_trade(data):
    price = float(data.get("p", 0))
    qty = float(data.get("q", 0))
    is_sell = data.get("m", False)
    notional = price * qty

    _store["price"] = price
    if is_sell:
        _store["volume_sell"] += notional
    else:
        _store["volume_buy"] += notional

    # Daily trend from Binance 4H+1D klines (fetch every 5 min)
    import time as _time
    if _time.time() - _store.get("_trend_last_fetch", 0) > 300:
        _store["_trend_last_fetch"] = _time.time()
        try:
            import urllib.request, json as _json
            base = "https://api.binance.com/api/v3/klines"

            def _ema_calc(vals, span):
                alpha = 2 / (span + 1)
                r = vals[0]
                for v in vals[1:]:
                    r = alpha * v + (1 - alpha) * r
                return r

            def _get_klines(url):
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return _json.loads(resp.read())

            data_4h = _get_klines(f"{base}?symbol=BTCUSDT&interval=4h&limit=60")
            data_1d = _get_klines(f"{base}?symbol=BTCUSDT&interval=1d&limit=30")
            closes_4h = [float(c[4]) for c in data_4h]
            closes_1d = [float(c[4]) for c in data_1d]

            t4h = 1 if _ema_calc(closes_4h, 20) > _ema_calc(closes_4h, 50) else -1
            t1d = 1 if _ema_calc(closes_1d, 7) > _ema_calc(closes_1d, 21) else -1

            if t4h == 1 and t1d == 1:
                _store["daily_trend"] = "BULLISH"
            elif t4h == -1 and t1d == -1:
                _store["daily_trend"] = "BEARISH"
            else:
                _store["daily_trend"] = f"MIXED"
        except Exception:
            pass  # Keep previous value

    # Floating PnL for open positions
    floating = 0.0
    for pos in _store["open_positions"]:
        if pos["side"] == "BUY":
            floating += (price - pos["price"]) * pos["size"]
        else:
            floating += (pos["price"] - price) * pos["size"]
    _store["floating_pnl"] = floating

    # Update PnL/equity history every second (not just on performance_update)
    _store["_tps_count"] += 1
    now = time.time()
    if now - _store.get("_last_hist_update", 0) >= 1.0:
        _store["_last_hist_update"] = now
        total = _store["pnl"] + floating
        _store["pnl_history"].append({"t": now, "v": total})
        _store["equity_history"].append({"t": now, "v": 300.0 + total})

    # TPS

    now = time.time()
    if now - _store["_tps_reset"] >= 1.0:
        _store["tps"] = _store["_tps_count"]
        _store["_tps_count"] = 0
        _store["_tps_reset"] = now
        _store["prices_1s"].append({"t": now, "p": price})

    # 1m candle
    c = _store["_candle"]
    if c["o"] == 0:
        c["o"] = price
        c["t"] = now
    c["h"] = max(c["h"], price)
    c["l"] = min(c["l"], price)
    c["c"] = price
    c["v"] += notional
    if now - c["t"] >= 60:
        _store["candles_1m"].append({
            "time": datetime.fromtimestamp(c["t"]),
            "open": c["o"], "high": c["h"], "low": c["l"],
            "close": c["c"], "volume": c["v"],
        })
        _store["_candle"] = {"o": price, "h": price, "l": price, "c": price, "v": 0, "t": now}

    # Whale
    if notional > 250000:
        side = "SELL" if is_sell else "BUY"
        _store["whale_trades"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "side": side, "qty": qty, "usd": notional,
        })
        if side == "BUY":
            _store["whale_buy_count"] += 1
            _store["whale_buy_vol"] += notional
        else:
            _store["whale_sell_count"] += 1
            _store["whale_sell_vol"] += notional


def _on_ticker(data):
    _store["high_24h"] = float(data.get("h", _store["high_24h"]))
    _store["low_24h"] = float(data.get("l", _store["low_24h"]))
    o = float(data.get("o", 0))
    if o > 0:
        _store["change_24h"] = (_store["price"] - o) / o * 100


def parse_log():
    """Lee últimas líneas del log del paper trading."""
    if not os.path.exists(LOG_FILE):
        return
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(_store["_log_pos"])
            lines = f.readlines()
            _store["_log_pos"] = f.tell()
    except Exception:
        return

    for line in lines:
        # Calculate runtime from any log timestamp
        ts_match = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', line)
        if ts_match and "_start_ts" not in _store:
            _store["_start_ts"] = ts_match.group(1)
        if ts_match:
            try:
                from datetime import datetime as _dt, timezone as _tz
                current = _dt.fromisoformat(ts_match.group(1)).replace(tzinfo=_tz.utc)
                if "_start_ts" in _store:
                    start = _dt.fromisoformat(_store["_start_ts"]).replace(tzinfo=_tz.utc)
                    _store["runtime"] = (current - start).total_seconds() / 3600
            except Exception:
                pass

        if "regime_detected" in line:
            m = re.search(r'regime=(\w+)', line)
            if m: _store["regime"] = m.group(1)
            m = re.search(r'adx=np\.float64\(([\d.]+)\)', line)
            if m: _store["adx"] = float(m.group(1))
            m = re.search(r'momentum=np\.float64\(([\d.e+-]+)\)', line)
            if m: _store["momentum"] = float(m.group(1))
            m = re.search(r'vol_pct=np\.float64\(([\d.]+)\)', line)
            if m: _store["vol_pct"] = float(m.group(1))

        # Calculate bars from runtime
        if _store["runtime"] > 0:
            _store["bars_15m"] = int(_store["runtime"] * 60 / 15)

        if "performance_update" in line:
            for key, attr in [("net_pnl", "pnl"), ("total_trades", "total_trades"),
                               ("win_rate", "win_rate"), ("runtime_hours", "runtime"),
                               ("total_fees", "fees"), ("sharpe_ratio", "sharpe"),
                               ("max_drawdown", "max_dd")]:
                m = re.search(rf'{key}=(?:np\.float64\()?([\d.e+-]+)\)?', line)
                if m:
                    val = float(m.group(1))
                    _store[attr] = int(val) if attr == "total_trades" else val
            _store["equity"] = 300.0 + _store["pnl"]

        if "signal_exit" in line:
            _store["signals_exits"] += 1
            _store["_last_was_exit"] = True

        elif "signal_generated" in line:
            _store["_last_was_exit"] = False
            m_sym = re.search(r'symbol=([\w-]+)', line)
            m_strat = re.search(r'strategy=(\w+)', line)
            m_side = re.search(r'side=(\w+)', line)
            m_str = re.search(r'strength=([\d.]+)', line)
            m_price = re.search(r'price=([\d.]+)', line)
            m_trigger = re.search(r'trigger=(\w+)', line)
            ts_match = re.match(r'(\d{2}:\d{2}:\d{2})', line[11:19])
            _store["signals_generated"].append({
                "time": line[11:19] if len(line) > 19 else "",
                "strategy": m_strat.group(1) if m_strat else "?",
                "side": m_side.group(1) if m_side else "?",
                "strength": float(m_str.group(1)) if m_str else 0,
                "price": float(m_price.group(1)) if m_price else 0,
                "trigger": m_trigger.group(1) if m_trigger else "?",
                "status": "pending",
            })

        if "signal_validated" in line:
            # Only count entry validations, not exit validations
            if not _store.get("_last_was_exit", False):
                _store["signals_validated"] += 1
                for sig in reversed(list(_store["signals_generated"])):
                    if sig["status"] == "pending":
                        sig["status"] = "validated"
                        break

        if "signals_blocked" in line:
            m = re.search(r'count=(\d+)', line)
            if m:
                _store["signals_blocked"] += int(m.group(1))
                # Mark pending signals as blocked
                for sig in reversed(list(_store["signals_generated"])):
                    if sig["status"] == "pending":
                        sig["status"] = "blocked"

        if "paper_entry_fill" in line:
            m_p = re.search(r'price=np\.float64\(([\d.]+)\)', line)
            m_s = re.search(r'side=(\w+)', line)
            m_st = re.search(r'strategy=(\w+)', line)
            m_sl = re.search(r'sl=np\.float64\(([\d.]+)\)', line)
            m_tp = re.search(r'tp=np\.float64\(([\d.]+)\)', line)
            m_sz = re.search(r'size=np\.float64\(([\d.]+)\)', line)
            ts_match = re.match(r'\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})', line)
            entry = {
                "time": ts_match.group(1) if ts_match else "",
                "type": "ENTRY",
                "side": m_s.group(1) if m_s else "?",
                "price": float(m_p.group(1)) if m_p else 0,
                "sl": float(m_sl.group(1)) if m_sl else 0,
                "tp": float(m_tp.group(1)) if m_tp else 0,
                "size": float(m_sz.group(1)) if m_sz else 0,
                "pnl": None,
                "strategy": m_st.group(1) if m_st else "",
                "status": "OPEN",
            }
            _store["bot_trades"].append(entry)
            _store["open_positions"].append(entry.copy())

        if "paper_exit_fill" in line:
            m_pnl = re.search(r'pnl=np\.float64\(([\d.e+-]+)\)', line)
            m_s = re.search(r'side=(\w+)', line)
            m_st = re.search(r'strategy=(\w+)', line)
            m_p = re.search(r'price=([\d.]+)', line)
            ts_match = re.match(r'\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})', line)
            pnl_val = float(m_pnl.group(1)) if m_pnl else 0
            _store["bot_trades"].append({
                "time": ts_match.group(1) if ts_match else "",
                "type": "EXIT",
                "side": m_s.group(1) if m_s else "",
                "price": float(m_p.group(1)) if m_p else 0,
                "sl": 0, "tp": 0, "size": 0,
                "pnl": pnl_val,
                "strategy": m_st.group(1) if m_st else "",
                "status": "WIN" if pnl_val > 0 else "LOSS",
            })
            # Mark matching entry as closed and remove from open positions
            strat_name = m_st.group(1) if m_st else ""
            for t in reversed(list(_store["bot_trades"])):
                if t["type"] == "ENTRY" and t["status"] == "OPEN" and t.get("strategy") == strat_name:
                    t["status"] = "CLOSED"
                    break
            _store["open_positions"] = [p for p in _store["open_positions"] if p.get("strategy") != strat_name]

        for tp in ["paper_tp_triggered", "paper_sl_triggered"]:
            if tp in line:
                m_pnl = re.search(r'pnl=np\.float64\(([\d.e+-]+)\)', line)
                m_p = re.search(r'exit_price=np\.float64\(([\d.]+)\)', line)
                ts_match = re.match(r'\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})', line)
                pnl_val = float(m_pnl.group(1)) if m_pnl else 0
                exit_type = "TP" if "tp" in tp else "SL"
                _store["bot_trades"].append({
                    "time": ts_match.group(1) if ts_match else "",
                    "type": exit_type,
                    "side": "", "price": float(m_p.group(1)) if m_p else 0,
                    "sl": 0, "tp": 0, "size": 0,
                    "pnl": pnl_val,
                    "strategy": "",
                    "status": "WIN" if pnl_val > 0 else "LOSS",
                })
