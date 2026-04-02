"""
BotStrike Live Dashboard — Terminal profesional con rich.

Conecta directamente al WebSocket de Binance para datos en tiempo real.
Lee el log del paper trading para trades, equity y microestructura.

Uso: python scripts/live_monitor.py
"""
import sys
import os
import time
import asyncio
import json
import re
from datetime import datetime
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich import box

import websockets

console = Console()

WS_TRADE = "wss://stream.binance.com:9443/ws/btcusdt@trade"
WS_TICKER = "wss://stream.binance.com:9443/ws/btcusdt@miniTicker"
LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "logs", "paper_binance.log")

BLOCKS = " ▁▂▃▄▅▆▇█"
CANDLE_UP = "│"
CANDLE_DN = "│"


class LiveDashboard:
    def __init__(self):
        # Price
        self.price = 0.0
        self.prev_price = 0.0
        self.open_price = 0.0
        self.high_24h = 0.0
        self.low_24h = 999999.0
        self.change_24h = 0.0

        # Flow
        self.trade_count = 0
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self.tps = 0
        self._tps_count = 0
        self._tps_reset = time.time()
        self.big_trades = deque(maxlen=8)
        self.whale_buy_count = 0
        self.whale_sell_count = 0
        self.whale_buy_vol = 0.0
        self.whale_sell_vol = 0.0

        # Charts
        self.price_1s = deque(maxlen=120)  # 2 min
        self.price_5s = deque(maxlen=60)   # 5 min
        self._5s_buffer = []
        self._5s_last = time.time()

        # Candles (15m simulated)
        self.candles = deque(maxlen=20)
        self._candle_open = 0
        self._candle_high = 0
        self._candle_low = 999999
        self._candle_close = 0
        self._candle_start = time.time()

        # Bot status from log
        self.regime = "..."
        self.adx = 0.0
        self.momentum = 0.0
        self.vol_pct = 0.0
        self.equity = 300.0
        self.pnl = 0.0
        self.total_trades = 0
        self.win_rate = 0.0
        self.runtime = 0.0
        self.fees = 0.0
        self.sharpe = 0.0
        self.max_dd = 0.0
        self.trades = deque(maxlen=15)
        self.signals_gen = 0
        self.signals_val = 0
        self.signals_blocked = 0
        # Read entire log on init to catch all trades
        self._log_pos = 0
        self.ws_ok = False
        self.start_time = time.time()
        self.bars_count = 0
        # Trend from Binance klines (4H + 1D)
        self._trend_label = "..."
        self._trend_color = "dim"
        self._trend_last_fetch = 0
        self._strat_panel = self._build_strat_panel()
        # Cache strategy config for live display
        from config.settings import Settings as _S
        from strategies.order_flow_momentum import COOLDOWN_SEC as _ofm_cd_val
        self._tc = _S().trading
        _s0 = _S().symbols[0] if _S().symbols else None
        self._lev = _s0.leverage if _s0 else 1
        self._mr_alloc = self._tc.allocation_mean_reversion * 100
        self._ofm_alloc = self._tc.allocation_order_flow_momentum * 100
        self._ofm_cd = _ofm_cd_val

    def _fetch_trend(self):
        """Fetch real trend from Binance klines (sync, blocking but fast)."""
        import urllib.request, json
        base = "https://api.binance.com/api/v3/klines"

        def _get(url):
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())

        def _ema(vals, span):
            alpha = 2 / (span + 1)
            r = vals[0]
            for v in vals[1:]:
                r = alpha * v + (1 - alpha) * r
            return r

        data_4h = _get(f"{base}?symbol=BTCUSDT&interval=4h&limit=60")
        data_1d = _get(f"{base}?symbol=BTCUSDT&interval=1d&limit=30")

        closes_4h = [float(c[4]) for c in data_4h]
        closes_1d = [float(c[4]) for c in data_1d]

        t4h = 1 if _ema(closes_4h, 20) > _ema(closes_4h, 50) else -1
        t1d = 1 if _ema(closes_1d, 7) > _ema(closes_1d, 21) else -1

        if t4h == 1 and t1d == 1:
            self._trend_label = "BULLISH"
            self._trend_color = "green"
        elif t4h == -1 and t1d == -1:
            self._trend_label = "BEARISH"
            self._trend_color = "red"
        else:
            labels = {(1, -1): "4H:BULL 1D:BEAR", (-1, 1): "4H:BEAR 1D:BULL"}
            self._trend_label = labels.get((t4h, t1d), "MIXED")
            self._trend_color = "yellow"

    @staticmethod
    def _build_strat_panel():
        from config.settings import Settings
        from strategies.mean_reversion import TF_CONFIGS
        from strategies.order_flow_momentum import OrderFlowMomentumStrategy

        settings = Settings()
        tc = settings.trading
        sym0 = settings.symbols[0] if settings.symbols else None

        # ── Global Parameters ──
        t = Table(show_header=True, box=box.SIMPLE_HEAVY, expand=True, padding=(0, 1),
                  header_style="bold cyan")
        t.add_column("Parameter", width=16)
        t.add_column("Value", width=18)
        t.add_column("Parameter", width=16)
        t.add_column("Value", width=18)
        t.add_column("Parameter", width=16)
        t.add_column("Value", width=18)

        syms = ", ".join(s.symbol for s in settings.symbols)
        lev = sym0.leverage if sym0 else 1
        max_pos = f"${sym0.max_position_usd:,.0f}" if sym0 else "N/A"

        t.add_row(
            "Capital", f"[bold]${tc.initial_capital:.0f}[/]",
            "Risk/trade", f"[bold]{tc.risk_per_trade_pct*100:.1f}%[/] (${tc.initial_capital * tc.risk_per_trade_pct:.2f})",
            "Max DD", f"[bold red]{tc.max_drawdown_pct*100:.0f}%[/] (${tc.initial_capital * tc.max_drawdown_pct:.2f})",
        )
        t.add_row(
            "Symbols", f"[bold]{syms}[/]",
            "Leverage", f"[bold]{lev}x[/]",
            "Max position", f"[bold]{max_pos}[/]",
        )
        t.add_row(
            "Vol target", f"{tc.vol_target_annual*100:.0f}% annual",
            "Slippage", f"{tc.slippage_bps:.0f} bps",
            "Fees", f"M:{tc.maker_fee*10000:.1f} T:{tc.taker_fee*10000:.1f} bps",
        )
        t.add_row(
            "Kelly", f"[{tc.kelly_floor_pct*100:.1f}%-{tc.kelly_ceiling_pct*100:.1f}%] (min {tc.kelly_min_trades} trades)",
            "Max exposure", f"{tc.max_total_exposure_pct*100:.0f}%",
            "Eval interval", f"{tc.strategy_interval_sec:.0f}s",
        )

        # ── MR: Multi-TF Divergence ──
        mr = Table(show_header=True, box=box.SIMPLE, expand=True, padding=(0, 1),
                   header_style="bold green")
        mr.add_column("TF", width=4)
        mr.add_column("RSI OS/OB", width=10)
        mr.add_column("Recovery", width=9)
        mr.add_column("Lookback", width=9)
        mr.add_column("ADX max", width=8)
        mr.add_column("SL (ATR)", width=9)
        mr.add_column("TP (ATR)", width=9)
        mr.add_column("R:R", width=5)
        mr.add_column("Risk %", width=7)
        mr.add_column("Cache", width=6)

        for tf_key, cfg in TF_CONFIGS.items():
            rr = cfg.tp_mult / cfg.sl_mult if cfg.sl_mult > 0 else 0
            mr.add_row(
                f"[bold]{cfg.name}[/]",
                f"{cfg.rsi_oversold:.0f}/{cfg.rsi_overbought:.0f}",
                f">{cfg.rsi_recovery:.0f}",
                f"{cfg.lookback} bars",
                f"<{cfg.adx_max:.0f}",
                f"{cfg.sl_mult}x",
                f"{cfg.tp_mult}x",
                f"1:{rr:.1f}",
                f"[bold]{cfg.risk_pct*100:.0f}%[/]",
                f"{cfg.cache_ttl}s",
            )

        mr_alloc = tc.allocation_mean_reversion * 100
        mr_panel = Panel(mr, title=f"[bold green]Mean Reversion {mr_alloc:.0f}% -- Multi-TF RSI Divergence[/]",
                         border_style="green", box=box.ROUNDED)

        # ── OFM: Order Flow Momentum ──
        ofm_inst = OrderFlowMomentumStrategy(tc)
        ofm = Table(show_header=True, box=box.SIMPLE, expand=True, padding=(0, 1),
                    header_style="bold magenta")
        ofm.add_column("Component", width=14)
        ofm.add_column("Detail", width=50)
        ofm.add_column("Weight", width=8)

        ofm.add_row("OBI", "Imbalance >0.10 + delta >0.01 = directional pressure", "[bold]40%[/]")
        ofm.add_row("Microprice", "Fair value divergence from mid (ATR-scaled threshold)", "[bold]30%[/]")
        ofm.add_row("Hawkes", f"Spike ratio >2.5 (low VPIN) / >3.5 (high VPIN) + OBI confirm", "[bold]20%[/]")
        ofm.add_row("Depth ratio", "Bid/ask depth >2.0 (long) / <0.5 (short)", "[bold]10%[/]")
        ofm.add_row("[dim]---[/]", "[dim]---[/]", "[dim]---[/]")
        ofm.add_row("Min score", "[bold]0.55[/] (OBI + 1 other signal minimum)", "")
        ofm.add_row("Trend scalar", "With: x1.1 | Against: x0.3 | Neutral: x1.0 (4H+1D Binance)", "")
        ofm.add_row("SL / TP", "[bold]1.5x / 3.0x ATR[/] (R:R 1:2.0)", "")
        ofm.add_row("Hold time", "30s-180s (momentum scalping)", "")
        from strategies.order_flow_momentum import COOLDOWN_SEC as _ofm_cd
        ofm.add_row("Cooldown", f"{_ofm_cd}s between trades", "")
        ofm.add_row("Filters", "VPIN <0.75 | Spread <15bps | Hawkes count >=3 | Kyle impact <1.5", "")

        ofm_alloc = tc.allocation_order_flow_momentum * 100
        ofm_panel = Panel(ofm, title=f"[bold magenta]Order Flow Momentum {ofm_alloc:.0f}% -- Microstructure Scalping[/]",
                          border_style="magenta", box=box.ROUNDED)

        # ── Disabled ──
        disabled = []
        if tc.allocation_trend_following == 0:
            disabled.append("Trend Following (0% -- breakout 0% WR)")
        if tc.allocation_market_making == 0:
            disabled.append("Market Making (0% -- not profitable with $300)")

        disabled_text = Text()
        for d in disabled:
            disabled_text.append(f"  [dim]{d}[/]\n")

        return Panel(
            Group(t, mr_panel, ofm_panel, disabled_text),
            title="[bold white]Strategy Configuration[/]",
            border_style="white", box=box.HEAVY,
        )

    def on_trade(self, data):
        self.prev_price = self.price
        self.price = float(data["p"])
        qty = float(data["q"])
        is_sell = data.get("m", False)
        notional = self.price * qty

        self.trade_count += 1
        if is_sell:
            self.sell_vol += notional
        else:
            self.buy_vol += notional

        if self.price > self.high_24h:
            self.high_24h = self.price
        if self.price < self.low_24h:
            self.low_24h = self.price

        # TPS
        self._tps_count += 1
        now = time.time()
        if now - self._tps_reset >= 1.0:
            self.tps = self._tps_count
            self._tps_count = 0
            self._tps_reset = now
            self.price_1s.append(self.price)

        # 5s aggregation
        self._5s_buffer.append(self.price)
        if now - self._5s_last >= 5.0:
            if self._5s_buffer:
                self.price_5s.append(sum(self._5s_buffer) / len(self._5s_buffer))
            self._5s_buffer = []
            self._5s_last = now

        # Mini candle (60s for visual)
        if self._candle_open == 0:
            self._candle_open = self.price
            self._candle_high = self.price
            self._candle_low = self.price
        self._candle_high = max(self._candle_high, self.price)
        self._candle_low = min(self._candle_low, self.price)
        self._candle_close = self.price
        if now - self._candle_start >= 60:
            self.candles.append({
                "o": self._candle_open, "h": self._candle_high,
                "l": self._candle_low, "c": self._candle_close,
            })
            self._candle_open = self.price
            self._candle_high = self.price
            self._candle_low = self.price
            self._candle_start = now

        # Big trades (>$50K)
        if notional > 250000:
            side = "SELL" if is_sell else "BUY"
            self.big_trades.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "side": side,
                "qty": qty,
                "notional": notional,
            })
            if side == "BUY":
                self.whale_buy_count += 1
                self.whale_buy_vol += notional
            else:
                self.whale_sell_count += 1
                self.whale_sell_vol += notional

    def on_ticker(self, data):
        self.open_price = float(data.get("o", 0))
        self.high_24h = float(data.get("h", self.high_24h))
        self.low_24h = float(data.get("l", self.low_24h))
        if self.open_price > 0:
            self.change_24h = (self.price - self.open_price) / self.open_price * 100

    def parse_log(self):
        if not os.path.exists(LOG_FILE):
            return
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(self._log_pos)
                lines = f.readlines()
                self._log_pos = f.tell()
        except Exception:
            return

        for line in lines:
            if "binance_ws_connected" in line:
                self.ws_ok = True
            if "regime_detected" in line:
                m = re.search(r'regime=(\w+)', line)
                if m: self.regime = m.group(1)
                m = re.search(r'adx=np\.float64\(([\d.]+)\)', line)
                if m: self.adx = float(m.group(1))
                m = re.search(r'momentum=np\.float64\(([\d.e+-]+)\)', line)
                if m: self.momentum = float(m.group(1))
                m = re.search(r'vol_pct=np\.float64\(([\d.]+)\)', line)
                if m: self.vol_pct = float(m.group(1))
                # bars_count from runtime (not each log line)
            if "runtime_hours" in line:
                m = re.search(r'runtime_hours=([\d.]+)', line)
                if m:
                    hours = float(m.group(1))
                    self.bars_count = int(hours * 60 / 15)  # 15m bars
            if "performance_update" in line:
                for key, attr in [("net_pnl", "pnl"), ("total_trades", "total_trades"),
                                   ("win_rate", "win_rate"), ("runtime_hours", "runtime"),
                                   ("total_fees", "fees"), ("sharpe_ratio", "sharpe"),
                                   ("max_drawdown", "max_dd")]:
                    m = re.search(rf'{key}=(?:np\.float64\()?([\d.e+-]+)\)?', line)
                    if m:
                        val = float(m.group(1))
                        setattr(self, attr, int(val) if attr == "total_trades" else val)
                self.equity = 300.0 + self.pnl
            if "signal_exit" in line:
                self._last_was_exit = True
            elif "signal_generated" in line:
                self.signals_gen += 1
                self._last_was_exit = False
            if "signal_validated" in line:
                if not getattr(self, '_last_was_exit', False):
                    self.signals_val += 1
            if "signals_blocked" in line:
                m = re.search(r'count=(\d+)', line)
                if m: self.signals_blocked += int(m.group(1))

            if "paper_entry_fill" in line:
                m_p = re.search(r'price=np\.float64\(([\d.]+)\)', line)
                m_s = re.search(r'side=(\w+)', line)
                m_st = re.search(r'strategy=(\w+)', line)
                m_sl = re.search(r'sl=np\.float64\(([\d.]+)\)', line)
                m_tp = re.search(r'tp=np\.float64\(([\d.]+)\)', line)
                m_sz = re.search(r'size=np\.float64\(([\d.]+)\)', line)
                ts = re.match(r'\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})', line)
                self.trades.append({
                    "time": ts.group(1) if ts else "",
                    "type": "ENTRY", "side": m_s.group(1) if m_s else "?",
                    "price": float(m_p.group(1)) if m_p else 0,
                    "sl": float(m_sl.group(1)) if m_sl else 0,
                    "tp": float(m_tp.group(1)) if m_tp else 0,
                    "size": float(m_sz.group(1)) if m_sz else 0,
                    "pnl": None, "strat": m_st.group(1) if m_st else "",
                    "status": "OPEN",
                })
            if "paper_exit_fill" in line:
                m_pnl = re.search(r'pnl=np\.float64\(([\d.e+-]+)\)', line)
                m_p = re.search(r'price=([\d.]+)', line)
                m_st = re.search(r'strategy=(\w+)', line)
                ts = re.match(r'\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})', line)
                pnl_val = float(m_pnl.group(1)) if m_pnl else 0
                self.trades.append({
                    "time": ts.group(1) if ts else "",
                    "type": "EXIT", "side": "",
                    "price": float(m_p.group(1)) if m_p else 0,
                    "sl": 0, "tp": 0,
                    "pnl": pnl_val, "strat": m_st.group(1) if m_st else "",
                    "status": "WIN" if pnl_val > 0 else "LOSS",
                })
                for t in reversed(list(self.trades)):
                    if t["type"] == "ENTRY" and t["status"] == "OPEN":
                        t["status"] = "CLOSED"; break

            for tp_key in ["paper_tp_triggered", "paper_sl_triggered"]:
                if tp_key in line:
                    m_pnl = re.search(r'pnl=np\.float64\(([\d.e+-]+)\)', line)
                    m_p = re.search(r'exit_price=np\.float64\(([\d.]+)\)', line)
                    ts = re.match(r'\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})', line)
                    pnl_val = float(m_pnl.group(1)) if m_pnl else 0
                    self.trades.append({
                        "time": ts.group(1) if ts else "",
                        "type": "TP" if "tp" in tp_key else "SL", "side": "",
                        "price": float(m_p.group(1)) if m_p else 0,
                        "sl": 0, "tp": 0,
                        "pnl": pnl_val, "strat": "",
                        "status": "WIN" if pnl_val > 0 else "LOSS",
                    })

    # ── Rendering ─────────────────────────────────────────────

    def _sparkline(self, data, width=50, height=8):
        """Sparkline chart that fills the panel width using block characters."""
        if len(data) < 3:
            lines = ["  Collecting data..."] + [""] * (height - 1)
            return Text("\n".join(lines), style="dim")

        vals = list(data)
        # Resample to exactly `width` points to fill panel
        if len(vals) > width:
            step = len(vals) / width
            vals = [vals[int(i * step)] for i in range(width)]
        elif len(vals) < width:
            # Stretch: repeat values to fill width
            step = len(vals) / width
            vals = [vals[min(int(i * step), len(vals) - 1)] for i in range(width)]

        mn, mx = min(vals), max(vals)
        rng = mx - mn if mx != mn else 1

        # Build grid with connected line and area fill below
        grid = [[" "] * len(vals) for _ in range(height)]
        for col, v in enumerate(vals):
            row = height - 1 - int((v - mn) / rng * (height - 1))
            row = max(0, min(height - 1, row))
            grid[row][col] = "█"
            # Fill below the line with dim blocks for area effect
            for r in range(row + 1, height):
                grid[r][col] = "░"
            # Connect vertical gaps between adjacent points
            if col > 0:
                prev_row = height - 1 - int((vals[col - 1] - mn) / rng * (height - 1))
                prev_row = max(0, min(height - 1, prev_row))
                lo, hi = min(row, prev_row), max(row, prev_row)
                for r in range(lo, hi + 1):
                    if grid[r][col] == " ":
                        grid[r][col] = "▌"

        t = Text()
        for r_idx, row in enumerate(grid):
            # Price label (left side)
            price_at_row = mx - (r_idx / max(height - 1, 1)) * rng
            if r_idx == 0:
                t.append(f"${mx:>8,.0f} ", style="dim")
            elif r_idx == height - 1:
                t.append(f"${mn:>8,.0f} ", style="dim")
            elif r_idx == height // 2:
                mid = (mx + mn) / 2
                t.append(f"${mid:>8,.0f} ", style="dim")
            else:
                t.append("          ", style="dim")

            for col, ch in enumerate(row):
                is_up = col == 0 or vals[col] >= vals[col - 1]
                color = "green" if is_up else "red"
                if ch == "█":
                    t.append(ch, style=f"bold {color}")
                elif ch == "▌":
                    t.append(ch, style=color)
                elif ch == "░":
                    t.append(ch, style=f"dim {color}")
                else:
                    t.append(ch)
            t.append("\n")

        last = vals[-1]
        first = vals[0]
        chg = last - first
        chg_c = "green" if chg >= 0 else "red"
        t.append(f"          Last: ${last:,.2f}  ", style="dim")
        t.append(f"Chg: ${chg:+,.2f}", style=chg_c)
        return t

    def _candle_chart(self, width=20, height=8):
        """Candle chart with 2-char wide candles to fill panel."""
        if len(self.candles) < 2:
            lines = ["  Building candles..."] + [""] * (height - 1)
            return Text("\n".join(lines), style="dim")

        # Use 2 chars per candle + 1 space = 3 chars per candle
        max_candles = max(width // 3, 5)
        candles = list(self.candles)[-max_candles:]
        all_prices = [c["h"] for c in candles] + [c["l"] for c in candles]
        mn, mx = min(all_prices), max(all_prices)
        rng = mx - mn if mx != mn else 1

        def to_row(price):
            return max(0, min(height - 1, height - 1 - int((price - mn) / rng * (height - 1))))

        # Build grid: 3 chars per candle (body body space)
        chart_width = len(candles) * 3
        grid = [[" "] * chart_width for _ in range(height)]

        for i, c in enumerate(candles):
            col = i * 3  # Start column for this candle
            h_row = to_row(c["h"])
            l_row = to_row(c["l"])
            o_row = to_row(c["o"])
            c_row = to_row(c["c"])
            is_green = c["c"] >= c["o"]

            # Wick (thin)
            for r in range(min(h_row, l_row), max(h_row, l_row) + 1):
                if 0 <= r < height:
                    grid[r][col] = "│"
                    grid[r][col + 1] = " "

            # Body (thick, 2 chars wide)
            body_top = min(o_row, c_row)
            body_bot = max(o_row, c_row)
            if body_top == body_bot:
                body_bot = body_top  # Doji: single line
            for r in range(body_top, body_bot + 1):
                if 0 <= r < height:
                    grid[r][col] = "█" if is_green else "▓"
                    grid[r][col + 1] = "█" if is_green else "▓"

        t = Text()
        for r_idx, row in enumerate(grid):
            # Price labels
            if r_idx == 0:
                t.append(f"${mx:>8,.0f} ", style="dim")
            elif r_idx == height - 1:
                t.append(f"${mn:>8,.0f} ", style="dim")
            else:
                t.append("          ", style="dim")

            for col, ch in enumerate(row):
                candle_idx = col // 3
                if candle_idx < len(candles):
                    is_green = candles[candle_idx]["c"] >= candles[candle_idx]["o"]
                else:
                    is_green = True
                if ch == "█":
                    t.append(ch, style="bold green" if is_green else "bold red")
                elif ch == "▓":
                    t.append(ch, style="red")
                elif ch == "│":
                    t.append(ch, style="green dim" if is_green else "red dim")
                else:
                    t.append(ch)
            t.append("\n")

        # Summary line
        last_c = candles[-1]
        chg = last_c["c"] - last_c["o"]
        chg_c = "green" if chg >= 0 else "red"
        t.append(f"          O:${last_c['o']:,.0f} ", style="dim")
        t.append(f"H:${last_c['h']:,.0f} ", style="dim green")
        t.append(f"L:${last_c['l']:,.0f} ", style="dim red")
        t.append(f"C:", style="dim")
        t.append(f"${last_c['c']:,.0f}", style=chg_c)
        return t

    def _flow_bar(self, width=40):
        total = self.buy_vol + self.sell_vol
        if total == 0:
            return Text("  No volume yet", style="dim")
        buy_pct = self.buy_vol / total
        buy_w = int(buy_pct * width)
        t = Text()
        t.append(f" BUY {buy_pct*100:.0f}% ", style="bold green")
        t.append("█" * buy_w, style="green")
        t.append("█" * (width - buy_w), style="red")
        t.append(f" {(1-buy_pct)*100:.0f}% SELL ", style="bold red")
        return t

    def build(self):
        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        elapsed = time.time() - self.start_time

        # Price with direction
        if self.price > self.prev_price:
            p_str = f"[bold green]${self.price:,.2f} ▲[/]"
        elif self.price < self.prev_price:
            p_str = f"[bold red]${self.price:,.2f} ▼[/]"
        else:
            p_str = f"[bold]${self.price:,.2f}[/]"

        chg_c = "green" if self.change_24h >= 0 else "red"
        # Calculate floating PnL from open positions
        floating = 0.0
        for t in self.trades:
            if t.get("status") == "OPEN" and t.get("price", 0) > 0:
                if t.get("side") == "BUY":
                    floating += (self.price - t["price"]) * t.get("size", 0.001)
                elif t.get("side") == "SELL":
                    floating += (t["price"] - self.price) * t.get("size", 0.001)
        total_pnl = self.pnl + floating
        pnl_c = "green" if total_pnl >= 0 else "red"
        rc = {"RANGING": "yellow", "TRENDING_UP": "green", "TRENDING_DOWN": "red",
              "BREAKOUT": "bold red"}.get(self.regime, "dim")

        # Daily trend from Binance 4H+1D klines (fetch every 5 min)
        daily_trend = self._trend_label
        dt_color = self._trend_color
        now_t = time.time()
        if now_t - self._trend_last_fetch > 300:  # every 5 min
            self._trend_last_fetch = now_t
            try:
                self._fetch_trend()
                daily_trend = self._trend_label
                dt_color = self._trend_color
            except Exception:
                pass

        # ── TOP: Price + Status ──
        top = Table.grid(expand=True, padding=(0, 1))
        top.add_column(ratio=2)
        top.add_column(ratio=1)
        top.add_column(ratio=1)
        top.add_row(
            f" {p_str}  [{chg_c}]{self.change_24h:+.2f}%[/]  H:${self.high_24h:,.0f} L:${self.low_24h:,.0f}",
            f"[{rc}]{self.regime}[/] ADX:{self.adx:.0f}  Trend:[{dt_color}]{daily_trend}[/]",
            f"[{pnl_c}]${300+total_pnl:,.2f}[/] PnL:[{pnl_c}]${total_pnl:+,.2f}[/] Float:[{('green' if floating>=0 else 'red')}]${floating:+,.2f}[/]",
        )
        top.add_row(
            f" TPS: {self.tps}  Trades: {self.trade_count:,}  Vol: ${(self.buy_vol+self.sell_vol)/1e6:.1f}M",
            f"Bars: {self.bars_count}x15m  Run: {self.runtime:.1f}h",
            f"Trades:{self.total_trades} WR:{self.win_rate:.0%} Sig:{self.signals_gen}/{self.signals_val}/{self.signals_blocked}" if self.total_trades > 0 else f"Sig:{self.signals_gen}gen/{self.signals_val}exec/{self.signals_blocked}blk" if self.signals_gen > 0 else "Bot: waiting...",
        )
        top_panel = Panel(top, title=f"[bold cyan]BotStrike[/] [dim]{now}[/]",
                          border_style="cyan", box=box.HEAVY)

        # ── Flow bar ──
        flow = self._flow_bar()

        # ── Charts side by side ──
        # Terminal is typically ~120 chars. Each panel gets ~55 chars inner width.
        # Panel border + padding = ~5 chars, so inner = ~50 usable chars.
        chart_w = 45
        spark = self._sparkline(self.price_1s, width=chart_w, height=8)
        candles = self._candle_chart(width=chart_w, height=8)

        chart_left = Panel(spark, border_style="blue",
                           title="[bold]Price (2min)[/]", padding=(0, 1))
        chart_right = Panel(candles, border_style="blue",
                            title="[bold]Candles (1min)[/]", padding=(0, 1))

        charts = Table.grid(expand=True)
        charts.add_column(ratio=1)
        charts.add_column(ratio=1)
        charts.add_row(chart_left, chart_right)

        # ── Signals summary ──
        sig_text = Text()
        sig_text.append(f" Signals: ", style="bold")
        sig_text.append(f"{self.signals_gen} gen ", style="cyan")
        sig_text.append(f"{self.signals_val} exec ", style="green")
        sig_text.append(f"{self.signals_blocked} blocked", style="red")

        # ── Trades ──
        trades_tbl = Table(show_header=True, box=box.SIMPLE, expand=True,
                           header_style="bold cyan")
        trades_tbl.add_column("Hora", width=9)
        trades_tbl.add_column("Tipo", width=6, justify="center")
        trades_tbl.add_column("Lado", width=5, justify="center")
        trades_tbl.add_column("Entry", width=10, justify="right")
        trades_tbl.add_column("SL", width=10, justify="right")
        trades_tbl.add_column("TP", width=10, justify="right")
        trades_tbl.add_column("PnL", width=9, justify="right")
        trades_tbl.add_column("Strat", width=8)
        trades_tbl.add_column("", width=6)

        trade_list = list(reversed(list(self.trades)))[:8]
        if not trade_list:
            trades_tbl.add_row("", "", "", "", "", "", "[dim]Waiting...[/]", "", "")
            for _ in range(5):
                trades_tbl.add_row("[dim]---[/]", "", "", "", "", "", "", "", "")
        else:
            for t in trade_list:
                sc = {"BUY": "green", "SELL": "red"}.get(t.get("side", ""), "")
                status = t.get("status", "")
                if t["type"] == "ENTRY" and status == "OPEN":
                    tc = "yellow"
                elif t["type"] == "ENTRY":
                    tc = "dim"
                elif t["type"] == "EXIT" or t["type"] == "TP":
                    tc = "green" if (t.get("pnl") or 0) > 0 else "red"
                elif t["type"] == "SL":
                    tc = "red"
                else:
                    tc = "white"
                pnl_s = ""
                if t.get("status") == "OPEN" and t.get("price", 0) > 0 and self.price > 0:
                    # Floating PnL in real-time
                    sz = t.get("size", 0)
                    if t.get("side") == "BUY":
                        fpnl = (self.price - t["price"]) * sz
                    else:
                        fpnl = (t["price"] - self.price) * sz
                    pc = "green" if fpnl > 0 else "red"
                    pnl_s = f"[{pc}]${fpnl:+,.2f}[/]"
                elif t.get("pnl") is not None:
                    pc = "green" if t["pnl"] > 0 else "red"
                    pnl_s = f"[{pc}]${t['pnl']:+,.2f}[/]"
                sl_s = f"[red]${t['sl']:,.0f}[/]" if t.get("sl", 0) > 0 else ""
                tp_s = f"[green]${t['tp']:,.0f}[/]" if t.get("tp", 0) > 0 else ""
                entry_s = f"${t['price']:,.0f}" if t.get("price", 0) > 0 else ""
                status_s = f"[{tc}]{status}[/]" if status else ""
                trades_tbl.add_row(
                    t.get("time", ""), f"[{tc}]{t['type']}[/]",
                    f"[{sc}]{t.get('side','')}[/]" if t.get("side") else "",
                    entry_s, sl_s, tp_s, pnl_s,
                    t.get("strat", "")[:8], status_s,
                )
            for _ in range(8 - len(trade_list)):
                trades_tbl.add_row("[dim]---[/]", "", "", "", "", "", "", "", "")

        whale_tbl = Table(show_header=True, box=box.SIMPLE, expand=True,
                          header_style="bold yellow", min_width=40)
        whale_tbl.add_column("Hora", width=9)
        whale_tbl.add_column("Lado", width=5, justify="center")
        whale_tbl.add_column("BTC", width=8, justify="right")
        whale_tbl.add_column("USD", width=12, justify="right")

        # Always show exactly 6 rows (stable height = no flicker)
        whale_list = list(self.big_trades)
        whale_display = list(reversed(whale_list))[:6]
        for bt in whale_display:
            sc = "green" if bt["side"] == "BUY" else "red"
            whale_tbl.add_row(
                bt["time"], f"[{sc}]{bt['side']}[/]",
                f"{bt['qty']:.3f}", f"${bt['notional']:,.0f}",
            )
        # Pad empty rows to keep height stable
        for _ in range(6 - len(whale_display)):
            whale_tbl.add_row("[dim]---[/]", "", "", "")

        # Totals always visible
        wt = self.whale_buy_count + self.whale_sell_count
        whale_tbl.add_row(
            "[bold]TOTAL[/]",
            f"[green]{self.whale_buy_count}B[/]/[red]{self.whale_sell_count}S[/]",
            f"{wt}",
            f"[green]${self.whale_buy_vol/1e6:.1f}M[/]/[red]${self.whale_sell_vol/1e6:.1f}M[/]",
        )

        bottom = Table.grid(expand=True)
        bottom.add_column(ratio=3)
        bottom.add_column(ratio=2)
        bottom.add_row(
            Panel(Group(sig_text, trades_tbl), title="[bold green]Bot Trades & Signals[/]", border_style="green"),
            Panel(whale_tbl, title="[bold yellow]Whale Trades >$250K[/]", border_style="yellow"),
        )

        # Strategy panel — compact but complete, always visible
        from strategies.mean_reversion import TF_CONFIGS as _tfc

        strat = Table(show_header=True, box=box.SIMPLE, expand=True, padding=(0, 0),
                      header_style="bold")
        strat.add_column("Strategy", width=12)
        strat.add_column("Timeframes", width=14)
        strat.add_column("Entry", width=30)
        strat.add_column("SL/TP", width=20)
        strat.add_column("Risk", width=8)
        strat.add_column("Filters", width=24)

        # MR row with all TFs
        tf_detail = " | ".join(f"{c.name}:{c.rsi_oversold:.0f}/{c.rsi_overbought:.0f}" for c in _tfc.values())
        sl_tp_detail = " | ".join(f"{c.name}:{c.sl_mult}/{c.tp_mult}x" for c in _tfc.values())
        risk_detail = " | ".join(f"{c.name}:{c.risk_pct*100:.0f}%" for c in _tfc.values())
        strat.add_row(
            f"[green]MR {self._mr_alloc:.0f}%[/]",
            tf_detail,
            "RSI divergence + OBV + OBI confirm",
            sl_tp_detail,
            risk_detail,
            f"ADX<{list(_tfc.values())[0].adx_max:.0f} | Dip proximity",
        )

        # OFM row
        strat.add_row(
            f"[magenta]OFM {self._ofm_alloc:.0f}%[/]",
            "Tick (5s eval)",
            "OBI:40% Micro:30% Hawkes:20% Depth:10%",
            "SL 1.5x / TP 3.0x ATR",
            f"{self._tc.risk_per_trade_pct*100:.1f}%",
            f"VPIN<.75 Spread<15bps CD:{self._ofm_cd}s",
        )

        strat_panel = Panel(strat, title=f"[bold white]Strategies[/] | ${self._tc.initial_capital:.0f} {self._lev}x | DD [red]{self._tc.max_drawdown_pct*100:.0f}%[/] | Fees M:{self._tc.maker_fee*10000:.1f}/T:{self._tc.taker_fee*10000:.1f}bps | Slip {self._tc.slippage_bps:.0f}bps",
                            border_style="white", box=box.ROUNDED)

        return Group(top_panel, flow, charts, bottom, strat_panel)


async def main():
    dash = LiveDashboard()
    # Strategy panel is now always visible in the live display

    async def trade_ws():
        while True:
            try:
                async with websockets.connect(WS_TRADE, ping_interval=20) as ws:
                    dash.ws_ok = True
                    async for msg in ws:
                        dash.on_trade(json.loads(msg))
            except Exception:
                dash.ws_ok = False
                await asyncio.sleep(1)

    async def ticker_ws():
        url = "wss://stream.binance.com:9443/ws/btcusdt@miniTicker"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    async for msg in ws:
                        dash.on_ticker(json.loads(msg))
            except Exception:
                await asyncio.sleep(1)

    async def log_reader():
        while True:
            dash.parse_log()
            await asyncio.sleep(0.3)

    async def renderer():
        with Live(dash.build(), console=console, refresh_per_second=2, screen=True) as live:
            while True:
                live.update(dash.build())
                await asyncio.sleep(0.5)

    await asyncio.gather(trade_ws(), ticker_ws(), log_reader(), renderer())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/]")
