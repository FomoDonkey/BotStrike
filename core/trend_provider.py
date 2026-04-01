"""
Trend Provider — Obtiene tendencia real de Binance klines (4H, 1D).

Cachea resultados por 15 minutos para no saturar la API.
No necesita API key (endpoint público).
"""
from __future__ import annotations
import time
import asyncio
from dataclasses import dataclass
from typing import Dict, Optional

import structlog

logger = structlog.get_logger(__name__)

# Binance symbol mapping
_SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "ADA-USD": "ADAUSDT",
}

CACHE_TTL_SEC = 900  # 15 minutes


@dataclass
class TrendInfo:
    """Trend information across timeframes."""
    trend_4h: int = 0      # -1=bearish, 0=neutral, 1=bullish
    trend_1d: int = 0      # -1=bearish, 0=neutral, 1=bullish
    ema20_4h: float = 0
    ema50_4h: float = 0
    ema7_1d: float = 0
    ema21_1d: float = 0
    price: float = 0
    timestamp: float = 0

    @property
    def macro_trend(self) -> int:
        """Combined macro trend: only bullish if BOTH 4h and 1d agree."""
        if self.trend_4h == 1 and self.trend_1d == 1:
            return 1
        if self.trend_4h == -1 and self.trend_1d == -1:
            return -1
        # Mixed or neutral
        return 0


class TrendProvider:
    """Provides real trend data from Binance klines API."""

    def __init__(self) -> None:
        self._cache: Dict[str, TrendInfo] = {}
        self._last_fetch: Dict[str, float] = {}

    def get_trend(self, symbol: str) -> Optional[TrendInfo]:
        """Get cached trend. Returns None if never fetched."""
        return self._cache.get(symbol)

    async def update(self, symbol: str) -> TrendInfo:
        """Fetch fresh trend data from Binance (cached for 15 min)."""
        now = time.time()
        last = self._last_fetch.get(symbol, 0)

        if now - last < CACHE_TTL_SEC and symbol in self._cache:
            return self._cache[symbol]

        try:
            info = await self._fetch_trend(symbol)
            self._cache[symbol] = info
            self._last_fetch[symbol] = now
            logger.debug("trend_updated",
                         symbol=symbol,
                         trend_4h=info.trend_4h,
                         trend_1d=info.trend_1d,
                         macro=info.macro_trend)
            return info
        except Exception as e:
            logger.warning("trend_fetch_failed", symbol=symbol, error=str(e))
            # Return cached if available, else neutral
            return self._cache.get(symbol, TrendInfo())

    async def _fetch_trend(self, symbol: str) -> TrendInfo:
        """Fetch klines from Binance and compute EMAs."""
        import aiohttp

        binance_sym = _SYMBOL_MAP.get(symbol, symbol.replace("-", ""))
        base = "https://api.binance.com/api/v3/klines"

        async with aiohttp.ClientSession() as session:
            # 4H klines (last 60 candles = 10 days)
            url_4h = f"{base}?symbol={binance_sym}&interval=4h&limit=60"
            async with session.get(url_4h, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data_4h = await resp.json()

            # 1D klines (last 30 candles = 30 days)
            url_1d = f"{base}?symbol={binance_sym}&interval=1d&limit=30"
            async with session.get(url_1d, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data_1d = await resp.json()

        closes_4h = [float(c[4]) for c in data_4h]
        closes_1d = [float(c[4]) for c in data_1d]

        if not closes_4h or not closes_1d:
            return TrendInfo()

        # 4H trend: EMA20 vs EMA50
        ema20_4h = self._ema(closes_4h, 20)
        ema50_4h = self._ema(closes_4h, 50)
        trend_4h = 1 if ema20_4h > ema50_4h else -1

        # 1D trend: EMA7 vs EMA21
        ema7_1d = self._ema(closes_1d, 7)
        ema21_1d = self._ema(closes_1d, 21)
        trend_1d = 1 if ema7_1d > ema21_1d else -1

        return TrendInfo(
            trend_4h=trend_4h,
            trend_1d=trend_1d,
            ema20_4h=ema20_4h,
            ema50_4h=ema50_4h,
            ema7_1d=ema7_1d,
            ema21_1d=ema21_1d,
            price=closes_1d[-1],
            timestamp=time.time(),
        )

    @staticmethod
    def _ema(values: list, span: int) -> float:
        """Compute EMA of a list, return last value."""
        if not values:
            return 0.0
        alpha = 2 / (span + 1)
        result = values[0]
        for v in values[1:]:
            result = alpha * v + (1 - alpha) * result
        return result
