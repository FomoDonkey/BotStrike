"""DIVERGENCE strategy — RSI divergences with a verifier and a precise entry trigger.

Research first (scripts/divergence_research.py, 2026-09-02, 6 majors, 1h bars, 2022→2026,
1,102 trades): regular RSI divergences confirmed by a structure break + MACD have NO edge —
WR 38%, PF 0.77, gross −25 bps/trade (t = −2.15), negative every year, both sides, every
symbol; hidden divergences ≈ 0. The strategy therefore ships DISABLED (allocation 0) and
fully configurable, so the owner can run it in paper under the edge monitor, which will
kill it again if its own statistics turn negative. Nothing here is a recommendation to
allocate capital.

Mechanics (same as the research, on `div_timeframe_min` bars aggregated from the engine's
1-minute frame, seeded with REST history at start):
  candidate  : price pivot low L2 < L1 while RSI(L2) > RSI(L1) + min_rsi_gap, and RSI(L1)
               below `rsi_os` (bullish); mirror for bearish. Pivots confirmed with `pivot_k`
               bars each side. Optional hidden (continuation) divergences with an EMA200 filter.
  verifier   : gap 5–60 bars, RSI gap, and the TRIGGER: a close beyond the second pivot's
               high (bullish) / low (bearish) within `trigger_window` bars, with the MACD
               histogram agreeing (and volume ≥ average when required).
  entry      : market at the trigger close; SL = pivot ∓ atr_buffer×ATR; TP = rr × risk;
               time stop after `max_hold` bars (exit signal). Sizing by risk (as MR).
  metadata   : both pivots (ts, price, RSI), RSI gap, trigger level, MACD state — the UI
               draws the divergence on the chart and the signals feed shows the reasoning.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import structlog

from config.settings import SymbolConfig, TradingConfig
from core.bars import aggregate_1m
from core.indicators import Indicators
from core.types import MarketRegime, MarketSnapshot, Position, Side, Signal, StrategyType
from strategies.base import BaseStrategy

logger = structlog.get_logger(__name__)


@dataclass
class Pivot:
    ts: float
    price: float
    rsi: float
    index: int


@dataclass
class Candidate:
    side: Side
    kind: str                 # regular | hidden
    p1: Pivot
    p2: Pivot
    trigger_level: float
    stop_ref: float
    atr: float
    born_ts: float            # bar close when the divergence became confirmed
    bars_left: int


@dataclass
class DivState:
    entry_ts: float
    entry_bar_ts: float
    bars_held: int = 0
    candidate: Optional[Candidate] = None


def _pivots(values: np.ndarray, k: int, low: bool) -> np.ndarray:
    n = len(values)
    out = np.zeros(n, dtype=bool)
    for i in range(k, n - k):
        w = values[i - k:i + k + 1]
        if low:
            out[i] = values[i] == w.min() and (w == values[i]).sum() == 1
        else:
            out[i] = values[i] == w.max() and (w == values[i]).sum() == 1
    return out


class DivergenceStrategy(BaseStrategy):
    """RSI divergence + structure-break trigger. Parameters live in TradingConfig
    (div_*) and are read at use time, so Settings edits apply without restart."""

    def __init__(self, trading_config: TradingConfig) -> None:
        super().__init__(StrategyType.DIVERGENCE, trading_config)
        self._history: Dict[str, pd.DataFrame] = {}      # seeded REST bars (closed)
        self._bars: Dict[str, pd.DataFrame] = {}         # merged bars + indicators
        self._bars_key: Dict[str, float] = {}
        self._pending: Dict[str, Candidate] = {}
        self._states: Dict[str, DivState] = {}
        self._last_exit_time: Dict[str, float] = {}
        self._last_p1_low: Dict[str, Optional[Pivot]] = {}
        self._last_p1_high: Dict[str, Optional[Pivot]] = {}
        self._seen_pivot_ts: Dict[str, set] = {}
        self.backtest_mode = False

    # ── parameters (live) ────────────────────────────────────────
    def _p(self, name: str, default):
        return getattr(self.trading_config, f"div_{name}", default)

    def should_activate(self, regime: MarketRegime) -> bool:
        # Regular divergences are end-of-move reversals: allowed in ranging and trending
        # regimes; a BREAKOUT (high vol + strong momentum) is exactly when they fail.
        return regime != MarketRegime.BREAKOUT

    def notify_external_exit(self, symbol: str, ts: float) -> None:
        self._last_exit_time[symbol] = ts
        self._states.pop(symbol, None)

    # ── history seeding (called by the engine at start, async) ───
    async def prime_history(self, symbol: str, binance_symbol: str, limit: int = 500) -> None:
        """Load closed bars of the strategy timeframe from Binance futures REST so the
        strategy is usable minutes after a restart (the 1-minute seed covers 16 h only)."""
        tf = int(self._p("timeframe_min", 60))
        interval = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 120: "2h", 240: "4h"}.get(tf, "1h")
        try:
            import aiohttp
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={binance_symbol}&interval={interval}&limit={limit}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        logger.warning("divergence_seed_failed", symbol=symbol, status=resp.status)
                        return
                    data = await resp.json()
            rows = [{"timestamp": int(k[6]) / 1000 + 0.001, "open": float(k[1]), "high": float(k[2]),
                     "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in data[:-1]]
            self._history[symbol] = pd.DataFrame(rows)
            self._bars_key.pop(symbol, None)
            logger.info("divergence_history_seeded", symbol=symbol, bars=len(rows), interval=interval)
        except Exception as e:
            logger.warning("divergence_seed_error", symbol=symbol, error=str(e), error_type=type(e).__name__)

    # ── bars of the strategy timeframe ───────────────────────────
    def _update_bars(self, symbol: str, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        tf = int(self._p("timeframe_min", 60))
        last_ts = float(df["timestamp"].iloc[-1]) if "timestamp" in df.columns and len(df) else 0.0
        if self._bars_key.get(symbol) == last_ts and symbol in self._bars:
            return self._bars[symbol]
        live = aggregate_1m(df, tf)
        hist = self._history.get(symbol)
        if live is None:
            return None
        frames = [f for f in (hist, live) if f is not None and len(f)]
        if not frames:
            return None
        bars = pd.concat(frames, ignore_index=True)
        # de-duplicate by bucket close time (history uses close_time+1ms; live exact)
        bars["_key"] = np.round(bars["timestamp"].astype(float) / 60.0)
        bars = bars.drop_duplicates("_key", keep="last").sort_values("timestamp").drop(columns=["_key"])
        bars = bars.reset_index(drop=True)
        close = bars["close"]
        bars["rsi"] = Indicators.rsi(close, int(self._p("rsi_period", 14)))
        bars["atr"] = Indicators.atr(bars["high"], bars["low"], close, 14)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        bars["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
        bars["ema200"] = close.ewm(span=200, adjust=False).mean()
        bars["vol_avg"] = bars["volume"].rolling(20, min_periods=5).mean()
        self._bars[symbol] = bars
        self._bars_key[symbol] = last_ts
        return bars

    # ── detection ────────────────────────────────────────────────
    def _scan(self, symbol: str, bars: pd.DataFrame) -> None:
        """Find new confirmed pivots and register a candidate divergence (if any)."""
        k = int(self._p("pivot_k", 3))
        n = len(bars)
        if n < 2 * k + 5:
            return
        lows, highs = bars["low"].to_numpy(), bars["high"].to_numpy()
        rsi = bars["rsi"].to_numpy()
        ts = bars["timestamp"].to_numpy(dtype=float)
        closes = bars["close"].to_numpy()
        ema200 = bars["ema200"].to_numpy()
        atr = bars["atr"].to_numpy()
        seen = self._seen_pivot_ts.setdefault(symbol, set())
        hidden = bool(self._p("hidden", False))
        rsi_os, rsi_ob = float(self._p("rsi_os", 35.0)), float(self._p("rsi_ob", 65.0))
        min_gap, max_gap = int(self._p("min_gap_bars", 5)), int(self._p("max_gap_bars", 60))
        min_rsi_gap = float(self._p("min_rsi_gap", 3.0))
        window = int(self._p("trigger_window", 6))
        # only the pivots that just became confirmed (index n-1-k) are new; on first call
        # replay the whole frame to build the pivot memory
        start = k if not seen else max(k, n - 1 - k)
        pl = _pivots(lows, k, low=True)
        ph = _pivots(highs, k, low=False)
        for j in range(start, n - k):
            key_l, key_h = ("L", ts[j]), ("H", ts[j])
            if pl[j] and key_l not in seen:
                seen.add(key_l)
                p2 = Pivot(ts=ts[j], price=float(lows[j]), rsi=float(rsi[j]), index=j)
                p1 = self._last_p1_low.get(symbol)
                if p1 is not None and not np.isnan(p2.rsi) and not np.isnan(p1.rsi):
                    gap = j - p1.index
                    if min_gap <= gap <= max_gap:
                        if not hidden:
                            ok = p2.price < p1.price and p2.rsi > p1.rsi + min_rsi_gap and p1.rsi < rsi_os
                        else:
                            ok = p2.price > p1.price and p2.rsi < p1.rsi - min_rsi_gap and closes[j] > ema200[j]
                        if ok and bool(self._p("with_trend", False)) and not hidden and closes[j] < ema200[j]:
                            ok = False
                        if ok:
                            self._pending[symbol] = Candidate(
                                side=Side.BUY, kind="hidden" if hidden else "regular", p1=p1, p2=p2,
                                trigger_level=float(highs[j]), stop_ref=p2.price,
                                atr=float(atr[j]) if not np.isnan(atr[j]) else 0.0,
                                born_ts=ts[min(n - 1, j + k)], bars_left=window)
                            logger.info("divergence_candidate", symbol=symbol, side="BUY", kind=self._pending[symbol].kind,
                                        p1=round(p1.price, 4), p2=round(p2.price, 4),
                                        rsi1=round(p1.rsi, 1), rsi2=round(p2.rsi, 1), trigger=round(float(highs[j]), 4))
                self._last_p1_low[symbol] = p2
            if ph[j] and key_h not in seen:
                seen.add(key_h)
                p2 = Pivot(ts=ts[j], price=float(highs[j]), rsi=float(rsi[j]), index=j)
                p1 = self._last_p1_high.get(symbol)
                if p1 is not None and not np.isnan(p2.rsi) and not np.isnan(p1.rsi):
                    gap = j - p1.index
                    if min_gap <= gap <= max_gap:
                        if not hidden:
                            ok = p2.price > p1.price and p2.rsi < p1.rsi - min_rsi_gap and p1.rsi > rsi_ob
                        else:
                            ok = p2.price < p1.price and p2.rsi > p1.rsi + min_rsi_gap and closes[j] < ema200[j]
                        if ok and bool(self._p("with_trend", False)) and not hidden and closes[j] > ema200[j]:
                            ok = False
                        if ok:
                            self._pending[symbol] = Candidate(
                                side=Side.SELL, kind="hidden" if hidden else "regular", p1=p1, p2=p2,
                                trigger_level=float(lows[j]), stop_ref=p2.price,
                                atr=float(atr[j]) if not np.isnan(atr[j]) else 0.0,
                                born_ts=ts[min(n - 1, j + k)], bars_left=window)
                            logger.info("divergence_candidate", symbol=symbol, side="SELL", kind=self._pending[symbol].kind,
                                        p1=round(p1.price, 4), p2=round(p2.price, 4),
                                        rsi1=round(p1.rsi, 1), rsi2=round(p2.rsi, 1), trigger=round(float(lows[j]), 4))
                self._last_p1_high[symbol] = p2

    def candidate_view(self, symbol: str) -> Optional[Dict]:
        c = self._pending.get(symbol)
        if c is None:
            return None
        return {"side": c.side.value, "kind": c.kind, "trigger_level": c.trigger_level, "stop_ref": c.stop_ref,
                "bars_left": c.bars_left, "p1": {"ts": c.p1.ts, "price": c.p1.price, "rsi": c.p1.rsi},
                "p2": {"ts": c.p2.ts, "price": c.p2.price, "rsi": c.p2.rsi}}

    # ── main entry point ─────────────────────────────────────────
    def generate_signals(self, symbol: str, df: pd.DataFrame, snapshot: MarketSnapshot,
                         regime: MarketRegime, sym_config: SymbolConfig, allocated_capital: float,
                         current_position: Optional[Position], **kwargs) -> List[Signal]:
        signals: List[Signal] = []
        if df is None or df.empty or "timestamp" not in df.columns:
            return signals
        bars = self._update_bars(symbol, df)
        if bars is None or len(bars) < 60:
            return signals
        last_bar_ts = float(bars["timestamp"].iloc[-1])
        # ── exit management ──
        if current_position is not None:
            st = self._states.get(symbol)
            if st is not None and last_bar_ts > st.entry_bar_ts:
                bars_held = int(round((last_bar_ts - st.entry_bar_ts) / (int(self._p("timeframe_min", 60)) * 60)))
                if bars_held >= int(self._p("max_hold", 48)):
                    price = snapshot.price if snapshot.price > 0 else float(bars["close"].iloc[-1])
                    exit_side = Side.SELL if current_position.side == Side.BUY else Side.BUY
                    signals.append(Signal(strategy=self.strategy_type, symbol=symbol, side=exit_side, strength=1.0,
                                          entry_price=price, stop_loss=price, take_profit=price,
                                          size_usd=current_position.notional,
                                          metadata={"action": "exit_divergence", "exit_reason": "time_stop",
                                                    "bars_held": bars_held}))
                    self._states.pop(symbol, None)
            return signals
        # ── entries ──
        self._scan(symbol, bars)
        cand = self._pending.get(symbol)
        if cand is None:
            return signals
        now = _time.time() if not self.backtest_mode else last_bar_ts
        last_exit = self._last_exit_time.get(symbol, 0.0)
        cooldown = int(self._p("cooldown_min", 60)) * 60
        if last_exit and now - last_exit < cooldown:
            return signals
        # the trigger is evaluated on each completed bar after the candidate was born
        if last_bar_ts <= cand.born_ts:
            return signals
        bar = bars.iloc[-1]
        prev = bars.iloc[-2]
        close = float(bar["close"])
        # count bars consumed
        consumed = int(round((last_bar_ts - cand.born_ts) / (int(self._p("timeframe_min", 60)) * 60)))
        if consumed > int(self._p("trigger_window", 6)):
            self._pending.pop(symbol, None)
            return signals
        broke = close > cand.trigger_level if cand.side == Side.BUY else close < cand.trigger_level
        hist_now, hist_prev = float(bar["macd_hist"]), float(prev["macd_hist"])
        macd_ok = (hist_now > hist_prev) if cand.side == Side.BUY else (hist_now < hist_prev)
        if bool(self._p("require_macd", True)) and not macd_ok:
            return signals
        vol_ok = True
        if bool(self._p("require_volume", False)):
            va = float(bar.get("vol_avg", 0) or 0)
            vol_ok = va > 0 and float(bar["volume"]) >= va
        if not (broke and vol_ok):
            return signals
        price = snapshot.price if snapshot.price > 0 else close
        atr = cand.atr if cand.atr > 0 else float(bar.get("atr", 0) or 0)
        if atr <= 0:
            return signals
        buf = float(self._p("atr_buffer", 0.5)) * atr
        stop = cand.stop_ref - buf if cand.side == Side.BUY else cand.stop_ref + buf
        risk = abs(price - stop)
        if risk <= 0 or risk / price < 1e-4:
            return signals
        rr = float(self._p("rr", 2.0))
        tp = price + rr * risk if cand.side == Side.BUY else price - rr * risk
        kelly = kwargs.get("kelly_risk_pct")
        risk_pct = kelly if kelly else self.trading_config.risk_per_trade_pct
        size = self._calc_position_size(allocated_capital, price, stop, sym_config.leverage, kelly_risk_pct=risk_pct)
        size_usd = size * price
        if size_usd < 20:
            self._pending.pop(symbol, None)
            return signals
        rsi_gap = abs(cand.p2.rsi - cand.p1.rsi)
        strength = min(1.0, 0.5 + rsi_gap / 40.0 + (0.1 if macd_ok else 0.0))
        self._pending.pop(symbol, None)
        self._states[symbol] = DivState(entry_ts=now, entry_bar_ts=last_bar_ts)
        logger.info("divergence_entry", symbol=symbol, side=cand.side.value, kind=cand.kind,
                    price=round(price, 4), stop=round(stop, 4), tp=round(tp, 4), rsi_gap=round(rsi_gap, 1))
        signals.append(Signal(
            strategy=self.strategy_type, symbol=symbol, side=cand.side, strength=strength,
            entry_price=price, stop_loss=stop, take_profit=tp, size_usd=size_usd,
            metadata={
                "trigger": f"divergence_{cand.kind}_{'bull' if cand.side == Side.BUY else 'bear'}",
                "divergence_kind": cand.kind,
                "pivots": [{"ts": cand.p1.ts, "price": cand.p1.price, "rsi": round(cand.p1.rsi, 2)},
                           {"ts": cand.p2.ts, "price": cand.p2.price, "rsi": round(cand.p2.rsi, 2)}],
                "rsi_gap": round(rsi_gap, 2), "trigger_level": cand.trigger_level,
                "macd_hist": round(hist_now, 6), "macd_confirmed": macd_ok, "volume_confirmed": vol_ok,
                "bars_to_trigger": consumed, "atr": round(atr, 6), "atr_bps": round(atr / price * 1e4, 1),
                "rr": rr, "risk_bps": round(risk / price * 1e4, 1), "timeframe_min": int(self._p("timeframe_min", 60)),
            },
        ))
        return signals
