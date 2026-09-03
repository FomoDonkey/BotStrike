"""TREND_DAILY engine — daily-cadence execution of the validated Donchian ensemble.

Why a separate engine: the intraday loop is tick/3-second driven and its paper
simulator lives only for the session. This strategy decides ONCE a day (at the
close of day t, executed at the open of t+1 — research §11.2) and holds for days or
weeks, so it needs its own book that survives restarts and daily klines fetched by
REST (Binance SPOT, no API key) — independent of the intraday WebSocket universe.

Flow (every day at `trend_execution_hour_utc:00` + `trend_execution_delay_min`):
  1. load/refresh daily klines for the candidate pool (cache: data/binance_daily/*.parquet)
  2. decision date = yesterday (last complete UTC day); universe = top-N by 30d median
     dollar volume, recomputed on the first run of each month (point-in-time)
  3. target weights = Donchian ensemble × vol scalar × 1/N × allocation_trend_daily,
     then the rebalance threshold vs the weights currently held
  4. execute at TODAY's open (the forming daily candle's open) with taker fee +
     slippage, as Trade objects that go through the normal paper pipeline
     (`BotStrike._process_paper_fill`: trade DB, equity, Telegram, UI)
  5. persist the book (data/trend_daily_state.json) and a tracking record
     (model open-to-open return vs the paper book's return) for the 90-day paper gate

Sizing uses the equity provided by the engine (all-time equity when compounding is
enabled), so gains are reinvested automatically.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import numpy as np
import pandas as pd
import structlog

from core.types import Position, Side, StrategyType, Trade
from strategies.trend_daily_model import (
    TrendParams, apply_rebalance_threshold, model_daily_return, select_universe,
    target_weights,
)

logger = structlog.get_logger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.getenv("BOTSTRIKE_TREND_DATA_DIR", os.path.join(PROJECT_ROOT, "data", "binance_daily"))
DEFAULT_STATE_PATH = os.getenv("BOTSTRIKE_TREND_STATE", os.path.join(PROJECT_ROOT, "data", "trend_daily_state.json"))
START_MS = 1_502_928_000_000  # 2017-08-17 — first Binance daily candle
# A run more than this late after the scheduled open (restart, first deploy) must fill at the
# CURRENT price, never at the stale daily open — the model assumes execution AT the open.
LATE_FILL_SEC = 3600.0
FETCH_ATTEMPTS = 3          # daily kline download retries per symbol
SPOT_KLINES_URL = "https://api.binance.com/api/v3/klines"


def to_ui_symbol(pool_symbol: str) -> str:
    """BTCUSDT → BTC-USD. Strike-style markets (XAU-USD, SP500-USD) are already in UI form."""
    s = pool_symbol.upper()
    if "-" in s:
        return s
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}-USD"
    return s


# ── Data ───────────────────────────────────────────────────────────────────────
def fetch_daily_klines(symbol: str, start_ms: int = START_MS, timeout: float = 60.0) -> Optional[pd.DataFrame]:
    """All daily candles from `start_ms` (inclusive) — the LAST row may be the
    forming (incomplete) candle of today. None when the pair does not exist."""
    rows: List[list] = []
    start = int(start_ms)
    while True:
        url = f"{SPOT_KLINES_URL}?symbol={symbol}&interval=1d&limit=1000&startTime={start}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                chunk = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None
            raise
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        start = int(chunk[-1][0]) + 86_400_000
        time.sleep(0.1)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "tb_base", "tb_quote", "ignore"])
    df = df[["open_time", "open", "high", "low", "close", "volume", "quote_volume"]].copy()
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = df[c].astype(float)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None)
    return df.drop(columns=["open_time"]).drop_duplicates("date").set_index("date").sort_index()


class DailyDataStore:
    """Parquet cache of complete daily candles + the forming candle of today."""

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR, fetcher: Optional[Callable] = None) -> None:
        if fetcher is None:
            # Route each market to its daily source: Binance spot for the USDT pairs, Yahoo for the
            # Strike TradFi markets (gold, silver, indices, oil, stocks) — see strategies/daily_sources.py
            from strategies.daily_sources import make_fetcher
            fetcher = make_fetcher(fetch_daily_klines)
        self.data_dir = data_dir
        self._fetch = fetcher
        os.makedirs(self.data_dir, exist_ok=True)

    def _path(self, sym: str) -> str:
        return os.path.join(self.data_dir, f"{sym}.parquet")

    def load(self, symbols: List[str], today: pd.Timestamp, refresh: bool = True,
             min_days: int = 30) -> Dict[str, pd.DataFrame]:
        """Returns {symbol: frame} where the frame holds complete days (< today)
        plus, when available, today's forming candle (index == today)."""
        out: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            df = self._load_one(sym, today, refresh)
            if df is not None and len(df[df.index < today]) >= min_days:
                out[sym] = df
        return out

    def _load_one(self, sym: str, today: pd.Timestamp, refresh: bool) -> Optional[pd.DataFrame]:
        path = self._path(sym)
        cached: Optional[pd.DataFrame] = None
        if os.path.exists(path):
            try:
                cached = pd.read_parquet(path)
            except Exception as e:
                logger.warning("trend_cache_unreadable", symbol=sym, error=str(e))
                cached = None
        if cached is not None:
            cached = cached[cached.index < today]          # never trust a cached forming candle
        if not refresh:
            return cached
        start_ms = START_MS
        if cached is not None and len(cached):
            start_ms = int((cached.index[-1] + pd.Timedelta(days=1)).timestamp() * 1000)
        fresh = None
        last_err: Optional[Exception] = None
        for attempt in range(FETCH_ATTEMPTS):
            try:
                fresh = self._fetch(sym, start_ms)
                last_err = None
                break
            except Exception as e:  # Binance REST read timeouts happen (CT, 2026-09-02: BNB, ZEC)
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        if last_err is not None:
            logger.warning("trend_fetch_failed", symbol=sym, error=str(last_err),
                           error_type=type(last_err).__name__, attempts=FETCH_ATTEMPTS)
            return cached
        if fresh is None:
            return cached
        df = pd.concat([cached, fresh]) if cached is not None else fresh
        df = df[~df.index.duplicated(keep="last")].sort_index()
        complete = df[df.index < today]
        try:
            complete.to_parquet(path)
        except Exception as e:
            logger.warning("trend_cache_write_failed", symbol=sym, error=str(e))
        return df


# ── Book / state ───────────────────────────────────────────────────────────────
@dataclass
class BookPosition:
    symbol: str                 # pool symbol (BTCUSDT)
    size: float
    entry_price: float          # weighted average
    entry_fee_rate: float
    weight: float               # executed weight (fraction of equity at execution)
    opened: str                 # YYYY-MM-DD
    opened_ts: float
    mark_price: float = 0.0

    @property
    def notional(self) -> float:
        return self.size * (self.mark_price or self.entry_price)

    @property
    def unrealized_pnl(self) -> float:
        return (self.mark_price - self.entry_price) * self.size if self.mark_price else 0.0


@dataclass
class TrendState:
    positions: Dict[str, BookPosition] = field(default_factory=dict)
    weights: Dict[str, float] = field(default_factory=dict)      # executed weights
    universe: List[str] = field(default_factory=list)
    universe_month: str = ""                                     # YYYY-MM of the last universe pick
    targets: Dict[str, float] = field(default_factory=dict)
    last_run_date: str = ""
    last_run_ts: float = 0.0
    last_run_status: str = ""
    last_error: str = ""
    equity_basis: float = 0.0
    last_run_late: bool = False
    opens_prev: Dict[str, float] = field(default_factory=dict)   # opens used at the last execution
    tracking: List[Dict[str, Any]] = field(default_factory=list)
    candidates: int = 0

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["positions"] = {k: asdict(v) for k, v in self.positions.items()}
        return d

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "TrendState":
        st = cls()
        for k, v in (d.get("positions") or {}).items():
            st.positions[k] = BookPosition(**{f: v.get(f, 0.0) for f in BookPosition.__dataclass_fields__})
        for k in ("weights", "universe", "universe_month", "targets", "last_run_date", "last_run_ts",
                  "last_run_status", "last_error", "equity_basis", "opens_prev", "tracking", "candidates",
                  "last_run_late"):
            if k in d:
                setattr(st, k, d[k])
        return st


# ── Engine ─────────────────────────────────────────────────────────────────────
class TrendDailyEngine:
    def __init__(self, settings, on_fill: Callable[[Trade], Awaitable[None]],
                 equity_provider: Callable[[], float],
                 data_store: Optional[DailyDataStore] = None,
                 state_path: str = DEFAULT_STATE_PATH,
                 clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self.config = settings.trading
        self._on_fill = on_fill
        self._equity_provider = equity_provider
        self.store = data_store or DailyDataStore()
        self.state_path = state_path
        self._clock = clock
        self.state = self._load_state()
        self._running = False
        self._run_lock = asyncio.Lock()
        self.last_marks: Dict[str, float] = {}
        self._venue_vol: Dict[str, float] = {}
        self._venue_vol_day: str = ""
        # Edge-monitor kill: no entries; every target is 0 so the book is closed at the
        # next daily run (a killed strategy must not keep capital at risk).
        self.killed: bool = False

    # ── persistence ──
    def _load_state(self) -> TrendState:
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as f:
                    st = TrendState.from_json(json.load(f))
                logger.info("trend_state_loaded", positions=len(st.positions),
                            last_run=st.last_run_date or "never")
                return st
        except Exception as e:
            logger.error("trend_state_unreadable", error=str(e), error_type=type(e).__name__)
        return TrendState()

    def save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state.to_json(), f, indent=1)
            os.replace(tmp, self.state_path)
        except Exception as e:
            logger.error("trend_state_write_failed", error=str(e))

    # ── scheduling ──
    @property
    def enabled(self) -> bool:
        return float(self.config.allocation_trend_daily) > 0

    def _today(self, now: Optional[float] = None) -> pd.Timestamp:
        ts = datetime.fromtimestamp(now or self._clock(), timezone.utc)
        return pd.Timestamp(ts.date())

    def next_run_ts(self, now: Optional[float] = None) -> float:
        now = now or self._clock()
        dt = datetime.fromtimestamp(now, timezone.utc)
        run = dt.replace(hour=int(self.config.trend_execution_hour_utc), minute=0, second=0,
                         microsecond=0) + timedelta(minutes=int(self.config.trend_execution_delay_min))
        today_key = dt.strftime("%Y-%m-%d")
        if self.state.last_run_date == today_key or run.timestamp() <= now:
            if self.state.last_run_date == today_key or run.timestamp() <= now - 1:
                run = run + timedelta(days=1)
        return run.timestamp()

    def is_due(self, now: Optional[float] = None) -> bool:
        now = now or self._clock()
        dt = datetime.fromtimestamp(now, timezone.utc)
        today_key = dt.strftime("%Y-%m-%d")
        if self.state.last_run_date == today_key:
            return False
        run = dt.replace(hour=int(self.config.trend_execution_hour_utc), minute=0, second=0,
                         microsecond=0) + timedelta(minutes=int(self.config.trend_execution_delay_min))
        return now >= run.timestamp()

    async def run_loop(self, poll_sec: float = 60.0) -> None:
        self._running = True
        logger.info("trend_daily_engine_started", enabled=self.enabled,
                    next_run=datetime.fromtimestamp(self.next_run_ts(), timezone.utc).isoformat())
        while self._running:
            try:
                if self.enabled and self.is_due():
                    await self.run_once()
                elif self.state.positions:
                    await self.mark_positions()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("trend_daily_loop_error", error=str(e), error_type=type(e).__name__)
            await asyncio.sleep(poll_sec)

    def stop(self) -> None:
        self._running = False

    # ── the daily run ──
    def pool(self) -> List[str]:
        return [s.strip().upper() for s in str(self.config.trend_pool).split(",") if s.strip()]

    def _venue_volumes(self, symbols: List[str]) -> Dict[str, float]:
        """24 h quote volume per market AT THE VENUE, used as the liquidity floor for mixed pools.

        Yahoo/Binance dollar volume cannot be compared across asset classes (an index reports the
        summed share volume of its constituents), and what actually limits us is how much can be
        traded on Strike: measured 2026-09-03, BTC 1.09 M$/24 h but GOOGL 94 $/24 h. Cached for the
        day; on any error the floor is simply not applied (returns {}), never blocking a run.
        """
        today_key = self._today().strftime("%Y-%m-%d")
        if self._venue_vol_day == today_key and self._venue_vol:
            return self._venue_vol
        out: Dict[str, float] = {}
        try:
            import urllib.request
            url = os.getenv("BOTSTRIKE_VENUE_TICKER_URL",
                            "https://api.strikefinance.org/price/v2/ticker/24hr")
            req = urllib.request.Request(url, headers={"User-Agent": "botstrike/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                rows = json.loads(r.read())
            by_ui = {str(x.get("symbol", "")).upper(): float(x.get("quoteVolume") or 0.0) for x in rows}
            for sym in symbols:
                v = by_ui.get(to_ui_symbol(sym))
                if v is not None:
                    out[sym] = v
        except Exception as e:  # noqa: BLE001 — liquidity data is an extra filter, never a blocker
            logger.warning("trend_venue_volume_unavailable", error=str(e)[:160])
            return {}
        self._venue_vol, self._venue_vol_day = out, today_key
        logger.info("trend_venue_volume_loaded", markets=len(out))
        return out

    async def run_once(self, now: Optional[float] = None) -> Dict[str, Any]:
        async with self._run_lock:
            return await self._run_once_locked(now)

    async def _run_once_locked(self, now: Optional[float]) -> Dict[str, Any]:
        now = now or self._clock()
        today = self._today(now)
        today_key = today.strftime("%Y-%m-%d")
        params = TrendParams.from_config(self.config)
        st = self.state
        try:
            data = await asyncio.to_thread(self.store.load, self.pool(), today, True,
                                           params.min_history_days)
            st.candidates = len(data)
            decision = today - pd.Timedelta(days=1)
            month_key = today.strftime("%Y-%m")
            if st.universe_month != month_key or not st.universe:
                # what one position would be worth, so the liquidity floor scales with the account
                try:
                    eq_now = float(self._equity_provider() or 0.0)
                except Exception:  # noqa: BLE001
                    eq_now = 0.0
                params.position_notional = (eq_now * float(params.leverage_cap)
                                            / max(int(params.n_assets), 1))
                st.universe = select_universe(data, decision, params, current=st.universe,
                                              venue_volume=self._venue_volumes(list(data)))
                st.universe_month = month_key
                logger.info("trend_universe_selected", month=month_key, universe=st.universe)
            raw_targets = target_weights(data, st.universe, decision, params)
            alloc = 0.0 if self.killed else float(self.config.allocation_trend_daily)
            targets = {s: w * alloc for s, w in raw_targets.items()}
            # positions outside the universe (dropped this month) must be closed
            for sym in list(st.positions):
                targets.setdefault(sym, 0.0)
            exec_w = apply_rebalance_threshold(targets, st.weights, params.rebalance_threshold)
            # Scheduled execution time of today; a late run (restart/first deploy hours after
            # the open) cannot honestly claim the open price → fill at the forming candle's close
            sched = today.timestamp() + int(self.config.trend_execution_hour_utc) * 3600                 + int(self.config.trend_execution_delay_min) * 60
            late = (now - sched) > LATE_FILL_SEC
            opens: Dict[str, float] = {}
            fills_at: Dict[str, float] = {}
            for sym in exec_w:
                df = data.get(sym)
                if df is None:
                    continue
                if today in df.index and float(df.loc[today, "open"]) > 0:
                    opens[sym] = float(df.loc[today, "open"])
                    cur = float(df.loc[today, "close"])
                    fills_at[sym] = cur if (late and cur > 0) else opens[sym]
                else:  # forming candle not returned (rare): fall back to yesterday's close
                    closes = df.loc[df.index < today, "close"]
                    if len(closes):
                        opens[sym] = float(closes.iloc[-1])
                        fills_at[sym] = opens[sym]
            if late:
                logger.warning("trend_late_run_fills_at_current_price", lag_hours=round((now - sched) / 3600, 1))
            st.last_run_late = late
            equity = float(self._equity_provider())
            st.equity_basis = equity
            turnover = 0.0
            for sym, w in sorted(exec_w.items()):
                price = fills_at.get(sym)
                if not price:
                    logger.warning("trend_no_price_skipped", symbol=sym)
                    continue
                turnover += abs(w - float(st.weights.get(sym, 0.0)))
                await self._execute_symbol(sym, w, price, equity, today_key, now)
            st.targets = {s: round(w, 6) for s, w in targets.items() if s in st.universe or w > 0}
            self._record_tracking(today_key, opens, turnover, equity)
            st.opens_prev = opens
            st.last_run_date = today_key
            st.last_run_ts = now
            st.last_run_status = "ok"
            st.last_error = ""
            self.save_state()
            logger.info("trend_daily_run_ok", date=today_key, universe=st.universe,
                        targets=st.targets, positions=len(st.positions),
                        equity_basis=round(equity, 2))
            await self.mark_positions(data)
            return {"status": "ok", "date": today_key, "targets": st.targets}
        except Exception as e:
            st.last_run_status = "error"
            st.last_error = f"{type(e).__name__}: {e}"[:300]
            st.last_run_ts = now
            self.save_state()
            logger.error("trend_daily_run_failed", error=str(e), error_type=type(e).__name__)
            return {"status": "error", "error": st.last_error}

    async def _execute_symbol(self, sym: str, target_w: float, price: float, equity: float,
                              today_key: str, now: float) -> None:
        st = self.state
        pos = st.positions.get(sym)
        current_notional = pos.size * price if pos else 0.0
        target_notional = max(0.0, target_w) * equity
        delta = target_notional - current_notional
        min_order = float(self.config.trend_min_order_usd)
        if abs(delta) < min_order and not (pos and target_w <= 0):
            st.weights[sym] = float(st.weights.get(sym, 0.0)) if pos else 0.0
            return
        slip = float(self.config.slippage_bps) / 10_000.0
        taker = float(self.config.taker_fee)
        if delta > 0:
            fill = price * (1.0 + slip)
            size = delta / fill
            if pos:
                total = pos.size + size
                pos.entry_price = (pos.entry_price * pos.size + fill * size) / total
                pos.entry_fee_rate = (pos.entry_fee_rate * pos.size + taker * size) / total
                pos.size = total
            else:
                pos = BookPosition(symbol=sym, size=size, entry_price=fill, entry_fee_rate=taker,
                                   weight=target_w, opened=today_key, opened_ts=now, mark_price=fill)
                st.positions[sym] = pos
            pos.weight = target_w
            pos.mark_price = fill
            st.weights[sym] = target_w
            trade = Trade(
                symbol=to_ui_symbol(sym), side=Side.BUY, price=fill, quantity=size, fee=0.0,
                order_id=f"trend_entry_{uuid.uuid4().hex[:8]}", strategy=StrategyType.TREND_DAILY,
                timestamp=now, pnl=0.0, expected_price=price,
                actual_slippage_bps=abs(fill - price) / price * 1e4,
                signal_features={"action": "entry_trend", "target_weight": target_w,
                                 "equity_basis": equity, "open_price": price, "pool_symbol": sym},
            )
            await self._on_fill(trade)
            logger.info("trend_entry_fill", symbol=sym, size=round(size, 6), price=round(fill, 4),
                        weight=round(target_w, 4))
        else:
            if not pos:
                return
            fill = price * (1.0 - slip)
            size = min(pos.size, -delta / fill) if target_w > 0 else pos.size
            gross = (fill - pos.entry_price) * size
            fees = pos.entry_price * size * pos.entry_fee_rate + fill * size * taker
            pnl = gross - fees
            hold = max(0.0, now - pos.opened_ts)
            full_exit = size >= pos.size - 1e-12 or target_w <= 0
            trade = Trade(
                symbol=to_ui_symbol(sym), side=Side.SELL, price=fill, quantity=size, fee=fees,
                order_id=f"trend_{'exit' if full_exit else 'rebalance'}_{uuid.uuid4().hex[:8]}",
                strategy=StrategyType.TREND_DAILY, timestamp=now, pnl=pnl, expected_price=pos.entry_price,
                actual_slippage_bps=abs(fill - price) / price * 1e4,
                signal_features={"action": "exit_trend" if full_exit else "exit_trend_rebalance",
                                 "exit_reason": "TREND_EXIT" if full_exit else "REBALANCE",
                                 "entry_price": pos.entry_price, "exit_price": fill,
                                 "hold_time_sec": hold, "target_weight": target_w,
                                 "pnl_bps": (fill / pos.entry_price - 1.0) * 1e4 if pos.entry_price else 0.0,
                                 "open_price": price, "pool_symbol": sym},
            )
            if full_exit:
                st.positions.pop(sym, None)
                st.weights[sym] = 0.0
            else:
                pos.size -= size
                pos.weight = target_w
                pos.mark_price = fill
                st.weights[sym] = target_w
            await self._on_fill(trade)
            logger.info("trend_exit_fill", symbol=sym, size=round(size, 6), price=round(fill, 4),
                        pnl=round(pnl, 4), full=full_exit)

    def _record_tracking(self, today_key: str, opens: Dict[str, float], turnover: float,
                         equity: float) -> None:
        st = self.state
        if not st.opens_prev or not st.last_run_date:
            return
        cost_bps = float(self.config.taker_fee) * 1e4 + float(self.config.slippage_bps)
        weights_prev = {s: w for s, w in st.weights.items()}  # weights held since the previous run
        model_ret = model_daily_return(weights_prev, st.opens_prev, opens, turnover, cost_bps)
        prev_eq = float(st.equity_basis) if st.equity_basis else equity
        paper_ret = (equity / prev_eq - 1.0) if prev_eq > 0 else 0.0
        rec = {"date": today_key, "model_ret": round(model_ret, 6), "paper_ret": round(paper_ret, 6),
               "turnover": round(turnover, 4)}
        st.tracking = (st.tracking + [rec])[-400:]

    async def close_all(self, reason: str = "risk_halt") -> int:
        """Close every book position at the latest known price (risk halt only —
        never on shutdown). Returns the number of positions closed."""
        async with self._run_lock:
            st = self.state
            if not st.positions:
                return 0
            await self.mark_positions()
            now = self._clock()
            today_key = self._today(now).strftime("%Y-%m-%d")
            equity = float(self._equity_provider())
            closed = 0
            for sym, pos in list(st.positions.items()):
                price = pos.mark_price or pos.entry_price
                await self._execute_symbol(sym, 0.0, price, equity, today_key, now)
                closed += 1
            st.targets = {}
            st.last_run_status = f"flattened:{reason}"
            self.save_state()
            logger.warning("trend_book_flattened", reason=reason, closed=closed)
            return closed

    # ── marking / views ──
    async def mark_positions(self, data: Optional[Dict[str, pd.DataFrame]] = None) -> None:
        st = self.state
        if not st.positions:
            return
        if data is None:
            try:
                data = await asyncio.to_thread(self.store.load, list(st.positions), self._today(), True, 1)
            except Exception as e:
                logger.debug("trend_mark_failed", error=str(e))
                return
        for sym, pos in st.positions.items():
            df = data.get(sym)
            if df is None or not len(df):
                continue
            pos.mark_price = float(df["close"].iloc[-1])
            self.last_marks[sym] = pos.mark_price

    def positions_as_positions(self) -> List[Position]:
        out = []
        for sym, p in self.state.positions.items():
            mark = p.mark_price or p.entry_price
            out.append(Position(symbol=to_ui_symbol(sym), side=Side.BUY, size=p.size,
                                entry_price=p.entry_price, mark_price=mark,
                                unrealized_pnl=(mark - p.entry_price) * p.size,
                                strategy=StrategyType.TREND_DAILY, timestamp=p.opened_ts))
        return out

    def unrealized_pnl(self) -> float:
        return float(sum(p.unrealized_pnl for p in self.state.positions.values()))

    def tracking_summary(self) -> Dict[str, Any]:
        recs = self.state.tracking
        if not recs:
            return {"days": 0, "model_return": 0.0, "paper_return": 0.0, "tracking_error_ann": 0.0,
                    "records": []}
        m = np.array([r["model_ret"] for r in recs], dtype=float)
        p = np.array([r["paper_ret"] for r in recs], dtype=float)
        diff = p - m
        te = float(diff.std(ddof=1) * np.sqrt(365)) if len(diff) > 1 else 0.0
        return {"days": len(recs),
                "model_return": float(np.prod(1 + m) - 1),
                "paper_return": float(np.prod(1 + p) - 1),
                "tracking_error_ann": round(te, 6),
                "records": recs[-120:]}

    def status(self) -> Dict[str, Any]:
        st = self.state
        equity = st.equity_basis or float(self._equity_provider() or 0.0)
        positions = []
        exposure = 0.0
        for sym, p in st.positions.items():
            mark = p.mark_price or p.entry_price
            notional = p.size * mark
            exposure += notional
            positions.append({
                "symbol": sym, "ui_symbol": to_ui_symbol(sym), "size": round(p.size, 8),
                "entry_price": round(p.entry_price, 6), "mark_price": round(mark, 6),
                "notional": round(notional, 4), "unrealized_pnl": round((mark - p.entry_price) * p.size, 4),
                "weight": round(p.weight, 4), "opened": p.opened,
            })
        tc = self.config
        return {
            "enabled": self.enabled, "killed": self.killed,
            "allocation": float(tc.allocation_trend_daily),
            "next_run_utc": datetime.fromtimestamp(self.next_run_ts(), timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_run_utc": (datetime.fromtimestamp(st.last_run_ts, timezone.utc).isoformat().replace("+00:00", "Z")
                             if st.last_run_ts else ""),
            "last_run_status": st.last_run_status, "last_error": st.last_error,
            "last_run_late": bool(getattr(st, "last_run_late", False)),
            "universe": list(st.universe), "candidates": st.candidates,
            "targets": dict(st.targets), "weights": {k: round(v, 6) for k, v in st.weights.items() if v},
            "positions": positions, "equity_basis": round(equity, 4),
            "exposure": round(exposure / equity, 4) if equity > 0 else 0.0,
            "tracking": self.tracking_summary(),
            "params": {
                "lookbacks": tc.trend_lookbacks, "target_vol": tc.trend_target_vol,
                "vol_window": tc.trend_vol_window, "n_assets": tc.trend_n_assets,
                "leverage_cap": tc.trend_leverage_cap, "rebalance_threshold": tc.trend_rebalance_threshold,
                "execution_hour_utc": tc.trend_execution_hour_utc,
                "execution_delay_min": tc.trend_execution_delay_min, "min_order_usd": tc.trend_min_order_usd,
            },
        }
