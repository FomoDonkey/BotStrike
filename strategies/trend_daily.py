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
import math
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
    TrendParams, apply_rebalance_threshold, exit_ladder, model_daily_return, select_universe, venue_floors, asset_class,
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
HEAL_DAYS = 5               # cached bars re-read on every refresh so late revisions land
# Venue mark vs the last settled reference close: warn when the perp sits further from its
# reference than this (thin book, stale reference, wrong mapping). A monitor, never a gate.
BASIS_WARN = 0.025
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
            # Re-read the last days every time so a bar cached before its source settled it is
            # replaced by the settled one (fresh rows win in the concat below): Yahoo revises a
            # futures close at settlement, and once a bar is cached it was never looked at again.
            start_ms = int((cached.index[-1] - pd.Timedelta(days=HEAL_DAYS)).timestamp() * 1000)
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
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            # Atomic: to_parquet truncates first, so a restart landing mid-write leaves a 0-byte file
            # and the next run must refetch ten years of daily bars for that market — which, if the
            # fetch then fails, drops it from the rebalance. Seen on WTI-USD during the 02:36Z deploy
            # (2026-09-04). Write beside the target, then rename, which is atomic on the same volume.
            complete.to_parquet(tmp)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning("trend_cache_write_failed", symbol=sym, error=str(e))
            try:
                os.unlink(tmp)
            except Exception:  # noqa: BLE001 - nothing to clean up
                pass
        return df


# ── Book / state ───────────────────────────────────────────────────────────────
_VENUE_COSTS: Dict[str, Any] = {"mtime": 0.0, "half_spread": {}}


def _venue_half_spread_bps(ui_symbol: str) -> Optional[float]:
    """Half the median spread this market showed on the venue, or None if it was never measured."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "strike_costs.json")
    try:
        mtime = os.path.getmtime(path)
        if mtime != _VENUE_COSTS["mtime"]:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            _VENUE_COSTS["half_spread"] = {
                k: float(v["spread"]["half_spread_bps"])
                for k, v in (raw.get("markets") or {}).items()
                if isinstance(v, dict) and (v.get("spread") or {}).get("half_spread_bps") is not None}
            _VENUE_COSTS["mtime"] = mtime
    except Exception:  # noqa: BLE001 — a missing snapshot just means "use the configured value"
        return _VENUE_COSTS["half_spread"].get(ui_symbol)
    return _VENUE_COSTS["half_spread"].get(ui_symbol)


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
    # Entry fee actually debited at the entry fill(s), still outstanding on the open quantity. A
    # position opened before 2026-09-05 paid nothing at entry (the state file has no field → 0),
    # so its close reports nothing as already paid and charges the entry leg then, as it always did.
    entry_fee_paid: float = 0.0

    @property
    def is_short(self) -> bool:
        """`size` is SIGNED: positive is long, negative is short. Every formula below is written so
        that the sign carries the direction — that is what keeps the long path bit-identical while
        the short path comes out right (2026-09-04)."""
        return self.size < 0

    @property
    def side(self) -> str:
        return "SELL" if self.is_short else "BUY"

    @property
    def notional(self) -> float:
        return abs(self.size) * (self.mark_price or self.entry_price)

    @property
    def unrealized_pnl(self) -> float:
        # signed size does the work: a short with mark < entry gives (negative)x(negative) > 0
        return (self.mark_price - self.entry_price) * self.size if self.mark_price else 0.0


@dataclass
class TrendState:
    positions: Dict[str, BookPosition] = field(default_factory=dict)
    weights: Dict[str, float] = field(default_factory=dict)      # executed weights
    universe: List[str] = field(default_factory=list)
    universe_month: str = ""                                     # YYYY-MM of the last universe pick
    universe_key: str = ""                                       # pool+n_assets the pick was made with
    targets: Dict[str, float] = field(default_factory=dict)
    last_run_date: str = ""
    last_run_ts: float = 0.0
    last_run_status: str = ""
    last_error: str = ""
    equity_basis: float = 0.0
    last_run_late: bool = False
    opens_prev: Dict[str, float] = field(default_factory=dict)   # opens used at the last execution
    tracking: List[Dict[str, Any]] = field(default_factory=list)
    last_adds_blocked: str = ""          # why the last run held its adds (risk gate), else ""
    liquidity_note: str = ""             # why the universe was not (re)picked this run, else ""
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
        for k in ("weights", "universe", "universe_month", "universe_key", "targets", "last_run_date", "last_run_ts",
                  "last_run_status", "last_error", "equity_basis", "opens_prev", "tracking", "candidates",
                  "last_run_late", "last_adds_blocked", "liquidity_note"):
            if k in d:
                setattr(st, k, d[k])
        # One tracking row per day. Files written before 2026-09-05 hold a row per RUN — a restart
        # or a manual run on a day already recorded added a second row for it (six rows for three
        # days on the CT). The last row of a day is the one cut by the run that completed it.
        seen: Dict[str, Dict[str, Any]] = {}
        for rec in st.tracking or []:
            if isinstance(rec, dict) and rec.get("date"):
                seen[str(rec["date"])] = rec
        st.tracking = list(seen.values())
        return st


# ── Engine ─────────────────────────────────────────────────────────────────────
class TrendDailyEngine:
    def __init__(self, settings, on_fill: Callable[[Trade], Awaitable[None]],
                 equity_provider: Callable[[], float],
                 data_store: Optional[DailyDataStore] = None,
                 state_path: str = DEFAULT_STATE_PATH,
                 clock: Callable[[], float] = time.time,
                 risk_gate: Optional[Callable[[], Any]] = None,
                 fill_fn: Optional[Callable[..., Awaitable[Any]]] = None) -> None:
        self.settings = settings
        self.config = settings.trading
        self._on_fill = on_fill
        # Live execution seam: fill_fn(ui_symbol, side, qty, ref_price, reduce_only) -> FillResult
        # (strategies/trend_live_executor.py). None = paper fill at the mark ± measured half-spread.
        self._fill_fn = fill_fn
        self._equity_provider = equity_provider
        # () -> (may_add: bool, reason: str). The risk manager's loss limits, circuit breaker and
        # drawdown halt hold this book's ADDS; exits and reductions always go through (2026-09-05).
        self._risk_gate = risk_gate
        self.store = data_store or DailyDataStore()
        self.state_path = state_path
        self._clock = clock
        self.state = self._load_state()
        self._running = False
        self._run_lock = asyncio.Lock()
        self.last_marks: Dict[str, float] = {}
        # symbol -> the venue's mark, pushed in by the engine on each premiumIndex refresh; the book
        # is valued from this, not from the daily source (see mark_positions)
        self.venue_marks: Dict[str, float] = {}
        # symbol -> the venue's own order rules (tick, step, minimum notional, market cap). Pushed in
        # by the engine; see _venue_size, which every order goes through.
        self.venue_filters: Dict[str, Dict[str, float]] = {}
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

    def _basis_snapshot(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        """Venue mark against the last settled reference close, per market.

        The signal comes from the reference series (Yahoo for TradFi, Binance for crypto) - the
        only history long and clean enough to validate on: Strike's own daily bars begin in
        April 2026 (the S&P in August), a third of gold's days have no trade, and weekends print
        near-flat bars. The fill and the valuation come from Strike's mark. The two must agree to
        within the day's move; a wider gap means the run is trading a breakout the venue never
        printed. Logged on every run and hourly, warned above BASIS_WARN, shown in /api/trend."""
        out: Dict[str, float] = {}
        today = self._today()
        for sym, df in data.items():
            try:
                settled = df[df.index < today] if isinstance(df.index, pd.DatetimeIndex) else df
                close = float(settled["close"].dropna().iloc[-1])
                mark = self._venue_mark_of(sym)
            except Exception:  # noqa: BLE001 - one bad frame must not hide the others
                continue
            if mark and close > 0:
                out[sym] = round(mark / close - 1.0, 5)
        if out:
            self.last_basis = out
            self.last_basis_ts = float(self._clock())
            logger.info("trend_basis", **{to_ui_symbol(k): v for k, v in out.items()})
            wide = {to_ui_symbol(k): v for k, v in out.items() if abs(v) > BASIS_WARN}
            if wide:
                logger.warning("trend_basis_wide", limit=BASIS_WARN, **wide)
        return out

    def _liquidity_view(self, data: Dict[str, pd.DataFrame], venue_vol: Dict[str, float],
                        enter_floor: float, exit_floor: float, universe: List[str]) -> Dict[str, Any]:
        """What the liquidity floors see, per pool market, for /api/trend and the journal."""
        markets: Dict[str, Dict[str, Any]] = {}
        for sym in data:
            v = venue_vol.get(sym) if venue_vol else None
            markets[to_ui_symbol(sym)] = {
                "venue_24h": round(float(v), 2) if v is not None else None,
                "member": sym in universe,
                "ok_enter": bool(v is not None and float(v) >= enter_floor),
                "ok_exit": bool(v is not None and float(v) >= exit_floor),
            }
        return {"enter_floor": round(enter_floor, 2), "exit_floor": round(exit_floor, 2),
                "available": bool(venue_vol), "markets": markets}

    async def _refresh_basis(self) -> None:
        try:
            params = TrendParams.from_config(self.config)
            data = await asyncio.to_thread(self.store.load, self.pool(), self._today(), False,
                                           params.min_history_days)
            self._basis_snapshot(data)
        except Exception as e:  # noqa: BLE001
            logger.debug("trend_basis_refresh_failed", error=str(e)[:120])

    async def warm_cache(self) -> int:
        """Fetch the daily bars of the whole pool once, so every reader of cached frames (exit
        ladders, MAE/MFE, the daily-open fallback) has them from the first request — a fresh CT or
        a deleted cache file left every TradFi row of the positions table at "---" (2026-09-05)."""
        try:
            params = TrendParams.from_config(self.config)
            data = await asyncio.to_thread(self.store.load, self.pool(), self._today(), True,
                                           params.min_history_days)
            logger.info("trend_cache_warmed", markets=len(data))
            self._basis_snapshot(data)
            try:
                if len({asset_class(x) for x in data}) > 1:
                    eq_now = float(self._equity_provider() or 0.0)
                    params.position_notional = eq_now * float(params.leverage_cap) / max(int(params.n_assets), 1)
                    enter_floor, exit_floor = venue_floors(params)
                    venue_vol = await asyncio.to_thread(self._venue_volumes, list(data))
                    self.last_liquidity = self._liquidity_view(data, venue_vol, enter_floor, exit_floor,
                                                               self.state.universe)
            except Exception as e:  # noqa: BLE001 - the daily run computes it again
                logger.debug("trend_liquidity_warm_failed", error=str(e)[:120])
            return len(data)
        except Exception as e:  # noqa: BLE001 - the daily run refreshes again in any case
            logger.warning("trend_cache_warm_failed", error=str(e)[:160])
            return 0

    async def run_loop(self, poll_sec: float = 60.0) -> None:
        self._running = True
        logger.info("trend_daily_engine_started", enabled=self.enabled,
                    next_run=datetime.fromtimestamp(self.next_run_ts(), timezone.utc).isoformat())
        await self.warm_cache()
        while self._running:
            try:
                if self.enabled and self.is_due():
                    await self.run_once()
                elif self.state.positions:
                    await self.mark_positions()
                if self._clock() - float(getattr(self, "last_basis_ts", 0.0)) >= 3600.0:
                    await self._refresh_basis()
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
            self._basis_snapshot(data)
            decision = today - pd.Timedelta(days=1)
            month_key = today.strftime("%Y-%m")
            # The universe is re-picked monthly, but ALSO whenever the pool or the number of
            # assets changes: a config change must take effect at the next daily run, not four
            # weeks later (found on the CT 2026-09-03 switching to the multi-asset pool).
            universe_key = f"{','.join(self.pool())}|{params.n_assets}"
            # what one position would be worth, so the liquidity floors scale with the account
            try:
                eq_now = float(self._equity_provider() or 0.0)
            except Exception:  # noqa: BLE001
                eq_now = 0.0
            params.position_notional = (eq_now * float(params.leverage_cap)
                                        / max(int(params.n_assets), 1))
            mixed = len({asset_class(x) for x in data}) > 1
            venue_vol = self._venue_volumes(list(data)) if mixed else {}
            enter_floor, exit_floor = venue_floors(params)
            repick = st.universe_month != month_key or not st.universe or st.universe_key != universe_key
            # A member that stopped trading at the venue leaves the SAME day, not at the month's
            # re-pick: the S&P perp printed 788 $ of volume in 24 h while the book held 416 $ of
            # it - 53 % of the venue's day - because the pick had run without volumes (2026-09-05).
            if venue_vol and st.universe:
                illiquid = [x for x in st.universe if float(venue_vol.get(x, 0.0)) < exit_floor]
                if illiquid:
                    logger.warning("trend_universe_illiquid", dropped=[to_ui_symbol(x) for x in illiquid],
                                   exit_floor=round(exit_floor),
                                   volumes={to_ui_symbol(x): round(float(venue_vol.get(x, 0.0))) for x in illiquid})
                    st.universe = [x for x in st.universe if x not in illiquid]
                    repick = True
            if repick:
                if venue_vol or not mixed:
                    st.universe = select_universe(data, decision, params, current=st.universe,
                                                  venue_volume=venue_vol or None)
                    st.universe_month = month_key
                    st.universe_key = universe_key
                    st.liquidity_note = ""
                    logger.info("trend_universe_selected", month=month_key,
                                universe=[to_ui_symbol(x) for x in st.universe],
                                enter_floor=round(enter_floor), exit_floor=round(exit_floor))
                else:
                    # Fail closed. The first pick on 2026-09-03 ran with no venue volumes and let in
                    # two markets the floor would have refused. Keep what is held, say so, retry
                    # tomorrow: a day without new members costs less than a market we cannot exit.
                    st.liquidity_note = "venue volumes unavailable: universe unchanged this run"
                    logger.warning("trend_universe_pick_skipped", reason="venue_volume_unavailable",
                                   universe=[to_ui_symbol(x) for x in st.universe])
            self.last_liquidity = self._liquidity_view(data, venue_vol, enter_floor, exit_floor, st.universe)
            raw_targets = target_weights(data, st.universe, decision, params)
            alloc = 0.0 if self.killed else float(self.config.allocation_trend_daily)
            targets = {s: w * alloc for s, w in raw_targets.items()}
            # positions outside the universe (dropped this month) must be closed
            for sym in list(st.positions):
                targets.setdefault(sym, 0.0)
            exec_w = apply_rebalance_threshold(targets, st.weights, params.rebalance_threshold)
            # No NEW risk while a loss limit, the breaker or the drawdown halt is in force: every
            # add is held at what is already held; exits and reductions still execute.
            may_add, why = True, ""
            if self._risk_gate is not None:
                try:
                    may_add, why = self._risk_gate()
                except Exception as e:  # noqa: BLE001 - a broken gate must not stop the exits
                    logger.warning("trend_risk_gate_error", error=str(e)[:120])
            if not may_add:
                blocked = []
                for sym, w in list(exec_w.items()):
                    held = float(st.weights.get(sym, 0.0))
                    if abs(w) > abs(held) + 1e-12 and w * held >= 0:
                        exec_w[sym] = held
                        blocked.append(sym)
                logger.warning("trend_adds_blocked_by_risk", reason=why, blocked=blocked)
            st.last_adds_blocked = why if not may_add else ""
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
            # The fill is what the VENUE would fill, not the daily source's open. Gold's Yahoo open
            # (the CME session, hours earlier) put a paper fill at 4,477 when Strike's book was at
            # 4,435; the position opened 1 % under water on paper alone (2026-09-05). The model's
            # signal still reads the daily bars; only the execution price moves to the venue.
            for sym in list(fills_at):
                vm = self._venue_mark_of(sym)
                if vm:
                    fills_at[sym] = vm
            if late:
                logger.warning("trend_late_run_fills_at_current_price", lag_hours=round((now - sched) / 3600, 1))
            st.last_run_late = late
            equity = float(self._equity_provider())
            prev_basis = float(st.equity_basis or 0.0)
            st.equity_basis = equity
            turnover = 0.0
            for sym, w in sorted(exec_w.items()):
                price = fills_at.get(sym)
                if not price:
                    logger.warning("trend_no_price_skipped", symbol=sym)
                    continue
                prev_w = float(st.weights.get(sym, 0.0))
                turnover += abs(w - prev_w)
                await self._execute_symbol(sym, w, price, equity, today_key, now, allow_add=may_add,
                                           weight_changed=abs(w - prev_w) > 1e-12)
            st.targets = {s: round(w, 6) for s, w in targets.items() if s in st.universe or w > 0}
            self._record_tracking(today_key, opens, turnover, equity, prev_basis)
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

    def _slippage_bps(self, sym: str) -> float:
        """Half the market's own measured spread, not one number for every market.

        `slippage_bps` defaults to 1.5 and its comment says "Binance Futures has deep book" — it was
        calibrated for a different venue and applied to gold, silver and ADA alike. Strike's measured
        spreads (data/strike_costs.json, scripts/strike_market_stats.py) run from 0.23 bps on BTC to
        8.0 on XAU, so a flat 1.5 understates the cost of the illiquid half of the book by ~2.6x and
        overstates BTC's by 13x. Crossing the book costs half the spread, so that is what we charge,
        with the configured value as the floor and the fallback (2026-09-04).
        """
        base = float(self.config.slippage_bps)
        half = _venue_half_spread_bps(to_ui_symbol(sym))
        return max(base, half) if half is not None else base

    async def _execute_symbol(self, sym: str, target_w: float, price: float, equity: float,
                              today_key: str, now: float, allow_add: bool = True,
                              weight_changed: bool = True) -> None:
        """Move one market from what we hold to what the model wants, in SIGNED notional.

        Everything here is expressed as signed exposure (positive long, negative short) so one path
        serves both sides. Three things can happen and they are not the same trade:

          * the exposure GROWS in its current direction -> an entry, averaged into the position;
          * the exposure SHRINKS toward zero            -> a close, realising PnL on the part closed;
          * the sign FLIPS                              -> close everything, then open the other way.

        The flip is the case that only exists with shorts enabled, and folding it into a single delta
        would corrupt both the average entry price and the realised PnL, so it is executed as two
        legs. With `allow_shorts` off the target can never be negative and this behaves exactly as the
        long-only version did (2026-09-04).
        """
        st = self.state
        pos = st.positions.get(sym)
        p = TrendParams.from_config(self.config)
        current = pos.size * price if pos else 0.0                 # signed exposure held
        target = (target_w if p.allow_shorts else max(0.0, target_w)) * equity   # signed exposure wanted
        min_order = float(self.config.trend_min_order_usd)

        # A sign flip is two trades: flatten, then open the other way.
        if pos and target * current < 0:
            await self._close_part(sym, pos, abs(pos.size), price, now, target_w=0.0, reason="flip")
            pos = st.positions.get(sym)
            current = 0.0

        delta = target - current
        closing = pos is not None and abs(target) < abs(current) - 1e-12
        # A weight the model KEPT is re-aligned to equity only when the drift is worth a trade: the
        # same band the rebalance threshold applies to weights, applied here to the dollar drift.
        # The research charges turnover on weight changes only; realigning every ≥ 10 $ of drift
        # was fees the validation never saw, growing with the account (2026-09-05).
        band = min_order
        if pos is not None and not weight_changed and target != 0.0:
            band = max(min_order, float(p.rebalance_threshold) * abs(current))
        if abs(delta) < band and not (pos and target == 0.0):
            st.weights[sym] = float(st.weights.get(sym, 0.0)) if pos else 0.0
            return

        if closing:
            fill_price = price * (1.0 - self._slippage_bps(sym) / 10_000.0 * (1 if pos.size > 0 else -1))
            qty = abs(pos.size) if target == 0.0 else min(abs(pos.size), abs(delta) / fill_price)
            await self._close_part(sym, pos, qty, price, now, target_w=target_w, reason="exit")
            return

        # entry or add, in the direction of `delta`
        if not allow_add:
            # a loss limit / breaker / drawdown halt is in force: no new risk, not even the
            # equity-driven re-alignment of a weight that did not change
            logger.info("trend_add_held_by_risk", symbol=sym, wanted_usd=round(abs(delta), 2))
            return
        slip = self._slippage_bps(sym) / 10_000.0
        buying = delta > 0
        fill = self._venue_price(sym, price * (1.0 + slip) if buying else price * (1.0 - slip))
        taker = float(self.config.taker_fee)
        # what the VENUE would accept, not what the maths asked for
        qty = self._venue_size(sym, abs(delta) / fill, fill, closing=False)
        if qty <= 0:
            logger.info("trend_entry_below_venue_minimum", symbol=sym,
                        wanted_usd=round(abs(delta), 2), rules=self._venue_rules(sym))
            st.weights[sym] = float(st.weights.get(sym, 0.0)) if pos else 0.0
            return
        fee_rate = taker
        fill_fn = getattr(self, "_fill_fn", None)      # tests build the engine without __init__
        if fill_fn is not None:
            # the venue fills it: the price, the quantity and the fee are whatever it reports
            res = await fill_fn(to_ui_symbol(sym), Side.BUY if buying else Side.SELL, qty, price, False)
            got = float(getattr(res, "qty", 0.0) or 0.0)
            if got <= 0:
                logger.warning("trend_live_entry_unfilled", symbol=sym, wanted=round(qty, 8),
                               note=str(getattr(res, "note", ""))[:160])
                st.weights[sym] = float(st.weights.get(sym, 0.0)) if pos else 0.0
                return
            qty, fill = got, float(res.price)
            fee_rate = float(res.fee) / (fill * qty) if fill * qty > 0 else taker
        size = qty * (1 if buying else -1)                          # signed
        adds = pos is not None
        if pos:
            total = pos.size + size
            pos.entry_price = (pos.entry_price * abs(pos.size) + fill * abs(size)) / abs(total)
            pos.entry_fee_rate = (pos.entry_fee_rate * abs(pos.size) + fee_rate * abs(size)) / abs(total)
            pos.size = total
        else:
            pos = BookPosition(symbol=sym, size=size, entry_price=fill, entry_fee_rate=fee_rate,
                               weight=target_w, opened=today_key, opened_ts=now, mark_price=fill)
            st.positions[sym] = pos
        pos.weight = target_w
        pos.mark_price = fill
        st.weights[sym] = target_w
        # The venue debits the taker fee when the order fills. It used to be charged at the close
        # together with the exit leg, which left the balance ~0.05 % of notional too high for as
        # long as the position lived (audit 2026-09-05). `entry_fee_rate` on the position is what
        # the close later reports as already paid (`entry_fee_charged`).
        entry_fee = fill * abs(size) * fee_rate
        pos.entry_fee_paid = float(getattr(pos, "entry_fee_paid", 0.0) or 0.0) + entry_fee
        trade = Trade(
            symbol=to_ui_symbol(sym), side=Side.BUY if buying else Side.SELL, price=fill,
            quantity=abs(size), fee=entry_fee,
            order_id=f"trend_entry_{uuid.uuid4().hex[:8]}", strategy=StrategyType.TREND_DAILY,
            timestamp=now, pnl=0.0, expected_price=price,
            actual_slippage_bps=abs(fill - price) / price * 1e4,
            signal_features={"action": "entry_trend", "target_weight": target_w,
                             "execution": "venue" if getattr(self, "_fill_fn", None) is not None else "paper",
                             "equity_basis": equity, "open_price": price, "pool_symbol": sym,
                             "direction": "short" if size < 0 else "long",
                             "adds_to_position": adds, "position_size_after": abs(pos.size)},
        )
        await self._on_fill(trade)
        logger.info("trend_entry_fill", symbol=sym, size=round(size, 6), price=round(fill, 4),
                    weight=round(target_w, 4), direction="short" if size < 0 else "long")

    async def _close_part(self, sym: str, pos: "BookPosition", qty: float, price: float, now: float,
                          target_w: float, reason: str) -> None:
        """Close `qty` units of a position, whichever way it points, realising the PnL of that part."""
        st = self.state
        slip = self._slippage_bps(sym) / 10_000.0
        taker = float(self.config.taker_fee)
        long_pos = pos.size > 0
        # closing a long SELLS (fills lower), closing a short BUYS (fills higher)
        fill = self._venue_price(sym, price * (1.0 - slip) if long_pos else price * (1.0 + slip))
        qty = min(abs(qty), abs(pos.size))
        if qty <= 0:
            return
        rounded = self._venue_size(sym, qty, fill, closing=True)
        if rounded <= 0:
            rounded = abs(pos.size)                 # too small to round: flatten rather than strand
        # never leave dust the venue would refuse to close on its own
        leftover = abs(pos.size) - rounded
        min_notional = float(self._venue_rules(sym).get("min_notional") or 0.0)
        if leftover > 0 and min_notional > 0 and leftover * fill < min_notional:
            rounded = abs(pos.size)
        qty = min(rounded, abs(pos.size))
        exit_fee_rate = taker
        fill_fn = getattr(self, "_fill_fn", None)
        if fill_fn is not None:
            res = await fill_fn(to_ui_symbol(sym), Side.SELL if long_pos else Side.BUY, qty, price, True)
            got = float(getattr(res, "qty", 0.0) or 0.0)
            if got <= 0:
                logger.warning("trend_live_exit_unfilled", symbol=sym, wanted=round(qty, 8),
                               note=str(getattr(res, "note", ""))[:160])
                return
            qty, fill = min(got, abs(pos.size)), float(res.price)
            exit_fee_rate = float(res.fee) / (fill * qty) if fill * qty > 0 else taker
        signed_qty = qty if long_pos else -qty
        gross = (fill - pos.entry_price) * signed_qty          # sign carries the direction
        entry_share = pos.entry_price * qty * pos.entry_fee_rate          # the entry leg of this quantity
        fees = entry_share + fill * qty * exit_fee_rate
        pnl = gross - fees                                     # round-trip net: what the statistics read
        # what of that entry leg was ALREADY debited at the entry fill (pro rata on a partial close):
        # the close credits it back to the balance so it is not paid twice
        paid = float(getattr(pos, "entry_fee_paid", 0.0) or 0.0)
        entry_fee_charged = min(entry_share, paid * (qty / abs(pos.size))) if abs(pos.size) > 0 else 0.0
        hold = max(0.0, now - pos.opened_ts)
        full_exit = qty >= abs(pos.size) - 1e-12
        trade = Trade(
            symbol=to_ui_symbol(sym), side=Side.SELL if long_pos else Side.BUY, price=fill,
            quantity=qty, fee=fees,
            order_id=f"trend_{'exit' if full_exit else 'rebalance'}_{uuid.uuid4().hex[:8]}",
            strategy=StrategyType.TREND_DAILY, timestamp=now, pnl=pnl, expected_price=pos.entry_price,
            actual_slippage_bps=abs(fill - price) / price * 1e4,
            signal_features={"action": "exit_trend" if full_exit else "exit_trend_rebalance",
                             "execution": "venue" if getattr(self, "_fill_fn", None) is not None else "paper",
                             "exit_reason": ("TREND_FLIP" if reason == "flip" else
                                             "TREND_EXIT" if full_exit else "REBALANCE"),
                             "entry_price": pos.entry_price, "exit_price": fill,
                             "hold_time_sec": hold, "target_weight": target_w,
                             "pnl_bps": ((fill / pos.entry_price - 1.0) * (1 if long_pos else -1) * 1e4
                                         if pos.entry_price else 0.0),
                             "open_price": price, "pool_symbol": sym,
                             "direction": "long" if long_pos else "short",
                             "position_size_after": 0.0 if full_exit else abs(pos.size - signed_qty),
                             "entry_fee_charged": entry_fee_charged},
        )
        if full_exit:
            st.positions.pop(sym, None)
            st.weights[sym] = 0.0
        else:
            pos.size = pos.size - signed_qty
            pos.entry_fee_paid = max(0.0, paid - entry_fee_charged)
            pos.weight = target_w
            pos.mark_price = fill
            st.weights[sym] = target_w
        await self._on_fill(trade)
        logger.info("trend_exit_fill", symbol=sym, size=round(qty, 6), price=round(fill, 4),
                    pnl=round(pnl, 4), full=full_exit, direction="long" if long_pos else "short")

    def _record_tracking(self, today_key: str, opens: Dict[str, float], turnover: float,
                         equity: float, prev_equity: float = 0.0) -> None:
        """One record per trading day: the model's open-to-open return against the book's.

        Two faults lived here (found 2026-09-05 on the CT: six records for three days, every
        `paper_ret` exactly 0.0, and a 7.4 % "tracking error" built on them). The caller had
        already overwritten `equity_basis` with today's equity before this ran, so the paper
        return compared today with itself; and every run appended, so a restart or a manual
        `/api/trend/run` on a day already recorded added a second row for it — with the model
        return of a zero-length day. The previous basis is now handed in, and a day is recorded once.
        """
        st = self.state
        if not st.opens_prev or not st.last_run_date:
            return
        if st.last_run_date == today_key:
            return                                   # a re-run of a day already on the record
        cost_bps = float(self.config.taker_fee) * 1e4 + float(self.config.slippage_bps)
        weights_prev = {s: w for s, w in st.weights.items()}  # weights held since the previous run
        model_ret = model_daily_return(weights_prev, st.opens_prev, opens, turnover, cost_bps)
        prev_eq = float(prev_equity) if prev_equity and prev_equity > 0 else 0.0
        paper_ret = (equity / prev_eq - 1.0) if prev_eq > 0 else 0.0
        rec = {"date": today_key, "model_ret": round(model_ret, 6), "paper_ret": round(paper_ret, 6),
               "turnover": round(turnover, 4)}
        st.tracking = ([r for r in st.tracking if r.get("date") != today_key] + [rec])[-400:]

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

    async def close_symbol(self, ui_or_pool_symbol: str, reason: str = "manual") -> Dict[str, Any]:
        """Close ONE book position now, at the latest known price (operator override).

        The daily model would normally exit through its trailing ladder; this is the manual brake
        for when the operator wants out. The market is also removed from today's targets so the
        next daily run does not immediately re-enter it (it can re-enter tomorrow if the signal is
        still on, which is the honest behaviour: this is an override, not a permanent ban).
        """
        async with self._run_lock:
            st = self.state
            sym = next((k for k in st.positions
                        if k == ui_or_pool_symbol or to_ui_symbol(k) == ui_or_pool_symbol), None)
            if sym is None:
                return {"closed": False, "reason": "position not found", "symbol": ui_or_pool_symbol}
            await self.mark_positions()
            pos = st.positions[sym]
            price = pos.mark_price or pos.entry_price
            now = self._clock()
            equity = float(self._equity_provider())
            await self._execute_symbol(sym, 0.0, price, equity, self._today(now).strftime("%Y-%m-%d"), now)
            st.targets.pop(sym, None)
            st.weights.pop(sym, None)
            self.save_state()
            logger.warning("trend_position_closed_manually", symbol=sym, price=price, reason=reason)
            return {"closed": True, "symbol": to_ui_symbol(sym), "price": price, "reason": reason}

    def _venue_rules(self, sym: str) -> Dict[str, float]:
        # getattr, not self.venue_filters: an engine restored from an older pickled state, or a stub
        # in a test, has no such attribute and an order must not blow up over a missing rule list
        f = getattr(self, "venue_filters", None) or {}
        return f.get(to_ui_symbol(sym).upper()) or f.get(str(sym).upper()) or {}

    def _venue_size(self, sym: str, qty: float, price: float, *, closing: bool) -> float:
        """Round a quantity to something the VENUE would actually accept. Returns 0 to skip.

        The panel showed the venue's rules and nothing enforced them, so the paper book could hold a
        position Strike would have rejected outright — the wrong lot size, or a notional under its
        $10 minimum (audit 2026-09-04). Rounding is always DOWN, so obeying the rules never enlarges
        an order beyond what the strategy asked for.

        A close is treated differently on purpose: the minimum notional does not apply to it (a venue
        always lets you flatten), and if the leftover would be dust below that minimum the whole
        position goes instead of stranding a piece that could never be closed on its own.
        """
        qty = abs(float(qty))
        rules = self._venue_rules(sym)
        if not rules or qty <= 0 or price <= 0:
            return qty
        step = float(rules.get("step_size") or 0.0)
        if step > 0:
            qty = math.floor(qty / step + 1e-9) * step
            qty = round(qty, max(0, int(round(-math.log10(step))) if step < 1 else 0))
        cap = float(rules.get("market_max_qty") or rules.get("max_qty") or 0.0)
        if cap > 0 and qty > cap:
            logger.warning("trend_order_capped_by_venue", symbol=sym, wanted=round(qty, 8), cap=cap)
            qty = cap
        min_qty = float(rules.get("min_qty") or 0.0)
        min_notional = float(rules.get("min_notional") or 0.0)
        if qty <= 0 or (min_qty > 0 and qty < min_qty):
            return 0.0
        if not closing and min_notional > 0 and qty * price < min_notional:
            return 0.0
        return qty

    def _venue_price(self, sym: str, price: float) -> float:
        """Round a price to the venue's tick. Half-up: this is a mark for the paper fill, not a
        resting order, so the nearest tick is the honest one."""
        rules = self._venue_rules(sym)
        tick = float(rules.get("tick_size") or 0.0)
        if tick <= 0 or price <= 0:
            return price
        return round(round(price / tick) * tick, 10)

    # ── marking / views ──
    async def mark_positions(self, data: Optional[Dict[str, pd.DataFrame]] = None) -> None:
        """Value every open position at the VENUE's mark, falling back to the last daily close.

        A position is worth what the venue says it is worth. Marked from the daily source instead,
        silver and gold sat 1.15 % above Strike's own mark: the gold position read -$0.004 when
        against the venue it was -$0.64, and the book's unrealised PnL was overstated by $0.78 on
        $419 (audit 2026-09-04). The daily bars keep their job — the SIGNAL is computed from closing
        bars and must be — but the valuation is the venue's.
        """
        st = self.state
        if not st.positions:
            return
        venue = {k: v for k, v in (getattr(self, "venue_marks", None) or {}).items() if v}

        def _venue_mark(sym: str) -> Optional[float]:
            return venue.get(to_ui_symbol(sym).upper()) or venue.get(str(sym).upper())

        missing = [s for s in st.positions if _venue_mark(s) is None]
        # only fetch daily bars for what the venue does not quote: on a book the venue covers in
        # full this drops a network round trip from every pass of the loop
        if data is None and missing:
            try:
                data = await asyncio.to_thread(self.store.load, missing, self._today(), True, 1)
            except Exception as e:
                logger.debug("trend_mark_failed", error=str(e))
                data = None
        for sym, pos in st.positions.items():
            mark = _venue_mark(sym)
            if mark is None:
                df = (data or {}).get(sym)
                if df is None or not len(df):
                    continue
                mark = float(df["close"].iloc[-1])
            pos.mark_price = float(mark)
            self.last_marks[sym] = pos.mark_price

    def _venue_mark_of(self, sym: str) -> Optional[float]:
        """The venue's latest mark for a pool or UI symbol, or None when it does not quote it."""
        venue = getattr(self, "venue_marks", None) or {}
        v = venue.get(to_ui_symbol(sym).upper()) or venue.get(str(sym).upper())
        return float(v) if v and v > 0 else None

    def set_venue_mark(self, ui_symbol: str, mark: float) -> None:
        """A fresh venue mark for one market, straight from the feed's premiumIndex poll (5 s).

        `run_loop` re-marks the book once a minute, and the venue marks it reads were themselves
        refreshed on a 15 s clock — so an open position's PnL on screen moved once a minute while
        the header's mark moved every few seconds (Edgar, 2026-09-05). The valuation is the venue's
        either way; this only makes it land when the venue publishes it. Signals still come from
        closing bars, untouched.
        """
        try:
            m = float(mark)
        except (TypeError, ValueError):
            return
        if not m > 0 or not ui_symbol:
            return
        key = str(ui_symbol).upper()
        self.venue_marks[key] = m
        for sym, pos in self.state.positions.items():
            if str(sym).upper() == key or to_ui_symbol(sym) == key:
                pos.mark_price = m
                self.last_marks[sym] = m

    def exit_ladders(self) -> Dict[str, Dict[str, Any]]:
        """Exit ladder per held market: the price levels at which each Donchian sub-strategy drops
        out and how much of the position leaves with it. A trend book has no single stop; this is
        what the operator needs to see instead (strategies/trend_daily_model.exit_ladder).

        Uses the cached daily frames — never a network call, so it is safe on every API request.
        """
        out: Dict[str, Dict[str, Any]] = {}
        if not self.state.positions:
            return out
        params = TrendParams.from_config(self.config)
        try:
            today = self._today()
            data = self.store.load(list(self.state.positions), today, refresh=False,
                                   min_days=params.min_history_days)
        except Exception as e:  # noqa: BLE001 — visibility must never break the API
            logger.warning("trend_exit_ladder_unavailable", error=str(e)[:160])
            return out
        for sym, df in data.items():
            try:
                pos = self.state.positions.get(sym)
                out[sym] = exit_ladder(df["close"], params, short=bool(pos and pos.is_short))
            except Exception as e:  # noqa: BLE001
                logger.warning("trend_exit_ladder_failed", symbol=sym, error=str(e)[:120])
        return out

    def excursions(self) -> Dict[str, Dict[str, float]]:
        """MAE / MFE per held market, in basis points against the entry price.

        MAE (maximum adverse excursion) is the deepest the market went AGAINST the position while it
        was open; MFE (maximum favourable excursion) the furthest it went in favour. Together they
        say how much heat a winner took and how much of a winner was given back — the numbers that
        tell you whether the exits are too tight or too slow. The column was empty on every trend
        position until 2026-09-03; the daily bars hold the answer, so nothing needs to be tracked.

        Daily resolution, which is the resolution this book trades at. Cached frames only.
        """
        out: Dict[str, Dict[str, float]] = {}
        if not self.state.positions:
            return out
        params = TrendParams.from_config(self.config)
        try:
            data = self.store.load(list(self.state.positions), self._today(), refresh=False,
                                   min_days=params.min_history_days)
        except Exception as e:  # noqa: BLE001 — visibility must never break the API
            logger.warning("trend_excursion_unavailable", error=str(e)[:160])
            return out
        for sym, pos in self.state.positions.items():
            df = data.get(sym)
            entry = float(pos.entry_price or 0.0)
            if df is None or not len(df) or entry <= 0:
                continue
            try:
                since = df[df.index >= pd.Timestamp(pos.opened)]
                if not len(since):
                    since = df.tail(1)
                low = float(since["low"].min()) if "low" in since else float(since["close"].min())
                high = float(since["high"].max()) if "high" in since else float(since["close"].max())
                # For a SHORT the roles swap: the high is what hurts, the low is what pays.
                if pos.is_short:
                    low, high = high, low
                # Fold in the LIVE mark: a position opened today has a daily bar that does not yet
                # contain today's move, so MFE read 0.0 beside a position showing +0.5 % on screen.
                mark = float(pos.mark_price or 0.0)
                if mark > 0:
                    low, high = min(low, mark), max(high, mark)
                # adverse is always the move against the position, favourable the one in its favour,
                # and both are reported as a signed excursion of the ENTRY price
                sign = -1.0 if pos.is_short else 1.0
                adverse = (min(low, entry) / entry - 1.0) if not pos.is_short else (max(low, entry) / entry - 1.0)
                favour = (max(high, entry) / entry - 1.0) if not pos.is_short else (min(high, entry) / entry - 1.0)
                out[sym] = {"mae_bps": round(adverse * sign * 10_000, 1),
                            "mfe_bps": round(favour * sign * 10_000, 1),
                            "days": int(len(since))}
            except Exception as e:  # noqa: BLE001
                logger.warning("trend_excursion_failed", symbol=sym, error=str(e)[:120])
        return out

    def positions_as_positions(self) -> List[Position]:
        out = []
        for sym, p in self.state.positions.items():
            mark = p.mark_price or p.entry_price
            out.append(Position(symbol=to_ui_symbol(sym), side=Side.SELL if p.is_short else Side.BUY,
                                size=abs(p.size),
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
            notional = abs(p.size) * mark          # exposure is a magnitude; the side carries direction
            exposure += notional
            positions.append({
                "symbol": sym, "ui_symbol": to_ui_symbol(sym), "size": round(p.size, 8),
                "side": p.side, "short": p.is_short,
                "entry_price": round(p.entry_price, 6), "mark_price": round(mark, 6),
                "notional": round(notional, 4), "unrealized_pnl": round((mark - p.entry_price) * p.size, 4),
                "weight": round(p.weight, 4), "opened": p.opened,
            })
        tc = self.config
        return {
            "enabled": self.enabled, "killed": self.killed,
            "last_adds_blocked": getattr(self.state, "last_adds_blocked", ""),
            "liquidity": dict(getattr(self, "last_liquidity", {}) or {}),
            "liquidity_note": getattr(st, "liquidity_note", ""),
            "basis": {to_ui_symbol(k): v for k, v in dict(getattr(self, "last_basis", {}) or {}).items()},
            "basis_ts": float(getattr(self, "last_basis_ts", 0.0) or 0.0),
            "basis_warn": BASIS_WARN,
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
