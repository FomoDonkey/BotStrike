"""
Módulo de recolección y almacenamiento de datos de mercado.
Mantiene DataFrames OHLCV con indicadores para cada símbolo.
"""
from __future__ import annotations
import asyncio
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config.settings import Settings, SymbolConfig
from core.types import MarketSnapshot, OrderBook, OHLCV
from core.indicators import Indicators
from core.regime_detector import RegimeDetector
import structlog

logger = structlog.get_logger(__name__)

# Tamaño máximo de barras almacenadas por símbolo
MAX_BARS = 2000


class MarketDataCollector:
    """Recolecta, almacena y procesa datos de mercado en tiempo real.

    Incluye tick quality guards inspirados en mejores prácticas de HFT:
      - Warmup period: descarta ticks durante los primeros segundos post-conexión
      - Stale tick guard: rechaza ticks con delta de precio excesivo
      - First tick skip: descarta el primer tick post-reconexión (snapshot cacheado)
      - Jitter tracking: monitorea intervalos inter-tick para detectar degradación
    """

    # ── Tick quality config ──────────────────────────────────────
    WARMUP_SEC = 5.0          # Segundos post-conexión donde los ticks se descartan
    STALE_TICK_MAX_PCT = 0.15 # Max delta % vs último precio para aceptar tick (15% — crypto is volatile)
    JITTER_EMA_ALPHA = 0.1    # Factor de suavizado para EMA de jitter inter-tick

    def __init__(
        self,
        settings: Settings,
        client: Any,  # StrikeClient or BinanceClient — both implement get_klines()
        regime_detector: RegimeDetector,
    ) -> None:
        self.settings = settings
        self.client = client
        self.regime_detector = regime_detector

        # DataFrames OHLCV por símbolo (con indicadores)
        self._dataframes: Dict[str, pd.DataFrame] = {}
        # Snapshots más recientes
        self._snapshots: Dict[str, MarketSnapshot] = {}
        # Ticks crudos para construir barras
        self._tick_buffer: Dict[str, List[dict]] = defaultdict(list)
        # Última barra creada (timestamp)
        self._last_bar_time: Dict[str, float] = {}
        # Último dato recibido por símbolo (para detección de stale data)
        self._last_data_time: Dict[str, float] = {}
        # Intervalo de barras en segundos
        # 60 = 1min — MR strategy resamples 1m bars to 5m internally.
        # Must be 60 for indicator math to be correct (ATR, RSI, BB).
        self.bar_interval = 60

        # ── Tick quality state ───────────────────────────────────
        # Timestamp de última conexión/reconexión WS (para warmup)
        self._ws_connect_time: float = 0.0
        # Primer tick post-conexión ya descartado? (por símbolo)
        self._first_tick_skipped: Dict[str, bool] = {}
        # Último precio aceptado por símbolo (para stale tick guard)
        self._last_accepted_price: Dict[str, float] = {}
        # Jitter EMA: intervalo promedio entre ticks (para quality monitoring)
        self._tick_jitter_ema: Dict[str, float] = {}
        self._last_tick_time: Dict[str, float] = {}
        # Contadores de calidad (para logging)
        self._ticks_accepted: int = 0
        self._ticks_rejected_warmup: int = 0
        self._ticks_rejected_stale: int = 0
        self._ticks_rejected_first: int = 0

    # ── Inicialización ─────────────────────────────────────────────

    async def initialize(self) -> None:
        """Carga datos iniciales para todos los símbolos."""
        tasks = [self._init_symbol(s) for s in self.settings.symbols]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("market_data_initialized", symbols=self.settings.symbol_names)

    async def _init_symbol(self, sym_config: SymbolConfig) -> None:
        """Carga snapshot inicial y construye DataFrame base."""
        symbol = sym_config.symbol
        try:
            snapshot = await self.client.get_market_snapshot(symbol)
            self._snapshots[symbol] = snapshot

            # Construir DataFrame inicial con trades recientes
            trades = await self.client.get_recent_trades(symbol, limit=1000)
            if trades:
                df = self._trades_to_ohlcv(trades, symbol)
                df = Indicators.compute_all(df, {
                    "ema_fast": sym_config.tf_ema_fast,
                    "ema_slow": sym_config.tf_ema_slow,
                    "zscore_lookback": sym_config.mr_lookback,
                })
                self._dataframes[symbol] = df
                logger.info("symbol_initialized", symbol=symbol, bars=len(df))
            else:
                self._dataframes[symbol] = pd.DataFrame()

        except Exception as e:
            logger.error("init_symbol_error", symbol=symbol, error=str(e))
            self._dataframes[symbol] = pd.DataFrame()

    async def seed_from_binance(self, symbol: str, sym_config: SymbolConfig, hours: int = 6) -> None:
        """Load recent klines from Binance REST API to seed the chart on startup.

        Without this, the chart starts empty and builds 1 bar per minute.
        This loads the last N hours of 1m candles so the chart is immediately useful.
        """
        try:
            import aiohttp
            _SYMBOL_MAP = {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "ADA-USD": "ADAUSDT"}
            binance_sym = _SYMBOL_MAP.get(symbol, symbol.replace("-", ""))
            limit = hours * 60  # 1m bars
            url = f"https://api.binance.com/api/v3/klines?symbol={binance_sym}&interval=1m&limit={limit}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.warning("binance_seed_failed", symbol=symbol, status=resp.status)
                        return
                    data = await resp.json()

            if not data:
                return

            rows = []
            for k in data:
                rows.append({
                    "timestamp": int(k[0]) / 1000,  # ms → seconds
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })

            df = pd.DataFrame(rows)
            df = Indicators.compute_all(df, {
                "ema_fast": sym_config.tf_ema_fast,
                "ema_slow": sym_config.tf_ema_slow,
                "zscore_lookback": sym_config.mr_lookback,
            })
            self._dataframes[symbol] = df

            # Set last bar time so new ticks continue from here
            if len(df) > 0:
                self._last_bar_time[symbol] = float(df["timestamp"].iloc[-1])
                # Initialize snapshot price from last candle
                last_price = float(df["close"].iloc[-1])
                if symbol not in self._snapshots:
                    from core.types import MarketSnapshot
                    self._snapshots[symbol] = MarketSnapshot(
                        symbol=symbol, timestamp=self._last_bar_time[symbol],
                        price=last_price, mark_price=last_price, index_price=last_price,
                        funding_rate=0.0, volume_24h=0.0, open_interest=0.0,
                    )
                else:
                    self._snapshots[symbol].price = last_price

            # Initialize _last_data_time so stale data guard doesn't block
            # strategy until first WS tick arrives (prevents seed-then-stale gap)
            self._last_data_time[symbol] = time.time()

            logger.info("binance_seed_loaded", symbol=symbol, bars=len(df), hours=hours)

        except Exception as e:
            logger.warning("binance_seed_error", symbol=symbol, error=str(e))

    def _trades_to_ohlcv(self, trades: List[dict], symbol: str) -> pd.DataFrame:
        """Convierte trades crudos a barras OHLCV de 1 minuto."""
        records = []
        for t in trades:
            records.append({
                "timestamp": float(t.get("time", t.get("T", 0))) / 1000.0,
                "price": float(t.get("price", t.get("p", 0))),
                "quantity": float(t.get("qty", t.get("q", 0))),
            })

        if not records:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(records)
        df["dt"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("dt", inplace=True)

        resample_rule = f"{self.bar_interval // 60}min" if self.bar_interval >= 60 else f"{self.bar_interval}s"
        ohlcv = df["price"].resample(resample_rule).ohlc()
        ohlcv.columns = ["open", "high", "low", "close"]
        ohlcv["volume"] = df["quantity"].resample(resample_rule).sum()
        ohlcv["timestamp"] = ohlcv.index.astype(np.int64) // 10**9
        ohlcv.dropna(subset=["open"], inplace=True)
        ohlcv.reset_index(drop=True, inplace=True)
        return ohlcv

    # ── Tick Quality Guards ─────────────────────────────────────────

    def on_ws_connected(self) -> None:
        """Notifica que el WebSocket se (re)conectó. Inicia warmup period."""
        self._ws_connect_time = time.time()
        self._first_tick_skipped = {}
        logger.info("tick_guard_warmup_started", duration_sec=self.WARMUP_SEC)

    def _should_accept_tick(self, symbol: str, price: float) -> bool:
        """Evalúa si un tick debe aceptarse o rechazarse por calidad.

        Implementa 3 guards del artículo de HFT adaptados a nuestro contexto:
          1. Warmup: primeros N segundos post-conexión = descartar
          2. First tick skip: primer tick por símbolo post-conexión = descartar
          3. Stale tick guard: delta vs último precio > X% = descartar

        Los guards solo se activan DESPUÉS de que on_ws_connected() es llamado
        (es decir, cuando el WS realmente se conecta). Antes de eso, todos los
        ticks se aceptan (modo backtesting/test o inicialización vía REST).
        """
        now = time.time()

        # Si el WS nunca se conectó, no aplicar guards (backtesting, tests, REST init)
        if self._ws_connect_time == 0:
            self._last_accepted_price[symbol] = price
            self._ticks_accepted += 1
            return True

        # Guard 1: Warmup period post-(re)conexión
        elapsed = now - self._ws_connect_time
        if elapsed < self.WARMUP_SEC:
            self._ticks_rejected_warmup += 1
            return False

        # Guard 2: First tick skip (snapshot cacheado del exchange)
        if not self._first_tick_skipped.get(symbol, False):
            self._first_tick_skipped[symbol] = True
            self._ticks_rejected_first += 1
            logger.debug("first_tick_skipped", symbol=symbol, price=price)
            return False

        # Guard 3: Stale tick guard (precio con delta excesivo)
        last_price = self._last_accepted_price.get(symbol, 0)
        if last_price > 0:
            delta_pct = abs(price - last_price) / last_price
            if delta_pct > self.STALE_TICK_MAX_PCT:
                self._ticks_rejected_stale += 1
                logger.warning("stale_tick_rejected", symbol=symbol,
                               price=price, last_price=last_price,
                               delta_pct=round(delta_pct * 100, 2))
                return False

        # Tick aceptado: actualizar tracking
        self._last_accepted_price[symbol] = price
        self._ticks_accepted += 1

        # Jitter tracking: medir intervalo entre ticks (solo con WS activo)
        if self._ws_connect_time > 0:
            last_t = self._last_tick_time.get(symbol, 0)
            if last_t > 0:
                interval = now - last_t
                ema = self._tick_jitter_ema.get(symbol, interval)
                self._tick_jitter_ema[symbol] = (
                    self.JITTER_EMA_ALPHA * interval + (1 - self.JITTER_EMA_ALPHA) * ema
                )
            self._last_tick_time[symbol] = now

        return True

    def get_tick_quality_stats(self) -> Dict:
        """Retorna estadísticas de calidad de ticks para monitoreo."""
        total = (self._ticks_accepted + self._ticks_rejected_warmup
                 + self._ticks_rejected_stale + self._ticks_rejected_first)
        return {
            "total_ticks": total,
            "accepted": self._ticks_accepted,
            "rejected_warmup": self._ticks_rejected_warmup,
            "rejected_stale": self._ticks_rejected_stale,
            "rejected_first": self._ticks_rejected_first,
            "accept_rate": self._ticks_accepted / total if total > 0 else 1.0,
            "jitter_ema": {s: round(v, 4) for s, v in self._tick_jitter_ema.items()},
        }

    # ── Actualización en tiempo real ───────────────────────────────

    async def update_snapshot(self, symbol: str) -> Optional[MarketSnapshot]:
        """Actualiza snapshot de mercado vía REST."""
        try:
            snapshot = await self.client.get_market_snapshot(symbol)
            self._snapshots[symbol] = snapshot
            return snapshot
        except Exception as e:
            logger.error("snapshot_update_error", symbol=symbol, error=str(e))
            return self._snapshots.get(symbol)

    def on_trade(self, symbol: str, price: float, quantity: float, ts: float) -> None:
        """Procesa un trade en tiempo real (desde WebSocket).

        Aplica tick quality guards antes de procesar:
        warmup, first-tick skip, stale-tick rejection.
        """
        self._last_data_time[symbol] = time.time()

        # Tick quality guard: rechazar ticks de baja calidad
        if not self._should_accept_tick(symbol, price):
            return

        tick = {"price": price, "quantity": quantity, "timestamp": ts}

        # Verificar si debemos cerrar barras ANTES de añadir el tick
        # Loop para cerrar múltiples barras si hubo un gap de datos
        last_bar = self._last_bar_time.get(symbol, 0)
        while last_bar > 0 and ts - last_bar >= self.bar_interval:
            bar_close_ts = last_bar + self.bar_interval
            self._close_bar(symbol, bar_close_ts)
            last_bar = self._last_bar_time.get(symbol, 0)

        # Añadir tick al buffer de la NUEVA barra
        self._tick_buffer[symbol].append(tick)

        # Si es el primer tick y no hay last_bar, inicializar
        if last_bar == 0:
            self._last_bar_time[symbol] = ts

    def on_orderbook(self, symbol: str, orderbook: OrderBook) -> None:
        """Actualiza el orderbook en el snapshot."""
        self._last_data_time[symbol] = time.time()
        if symbol in self._snapshots:
            self._snapshots[symbol].orderbook = orderbook
            if orderbook.mid_price:
                self._snapshots[symbol].price = orderbook.mid_price

    def _close_bar(self, symbol: str, bar_close_ts: float) -> None:
        """Cierra la barra actual y agrega al DataFrame.

        Args:
            bar_close_ts: Timestamp de cierre de la barra (last_bar + interval),
                          NO el timestamp del trade que disparó el cierre.
        """
        ticks = self._tick_buffer.get(symbol, [])
        if not ticks:
            self._last_bar_time[symbol] = bar_close_ts
            return

        # Separar ticks de esta barra vs ticks que pertenecen a la siguiente
        bar_ticks = [t for t in ticks if t["timestamp"] < bar_close_ts]
        next_ticks = [t for t in ticks if t["timestamp"] >= bar_close_ts]

        if not bar_ticks:
            # No hay ticks en esta barra, solo actualizar timestamp
            self._last_bar_time[symbol] = bar_close_ts
            return

        prices = [t["price"] for t in bar_ticks]
        volumes = [t["quantity"] for t in bar_ticks]

        new_bar = {
            "timestamp": bar_close_ts,
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "volume": sum(volumes),
        }

        df = self._dataframes.get(symbol, pd.DataFrame())
        new_row = pd.DataFrame([new_bar])
        df = pd.concat([df, new_row], ignore_index=True)

        # Mantener tamaño máximo
        if len(df) > MAX_BARS:
            df = df.tail(MAX_BARS).reset_index(drop=True)

        # Recalcular indicadores
        sym_config = self.settings.get_symbol_config(symbol)
        df = Indicators.compute_all(df, {
            "ema_fast": sym_config.tf_ema_fast,
            "ema_slow": sym_config.tf_ema_slow,
            "zscore_lookback": sym_config.mr_lookback,
        })

        self._dataframes[symbol] = df
        self._last_bar_time[symbol] = bar_close_ts
        # Mantener solo ticks de la siguiente barra
        self._tick_buffer[symbol] = next_ticks

    # ── Acceso a datos ─────────────────────────────────────────────

    def get_dataframe(self, symbol: str) -> pd.DataFrame:
        """Obtiene DataFrame OHLCV con indicadores para un símbolo."""
        return self._dataframes.get(symbol, pd.DataFrame())

    def get_snapshot(self, symbol: str) -> Optional[MarketSnapshot]:
        """Obtiene el último snapshot del mercado."""
        return self._snapshots.get(symbol)

    def get_current_price(self, symbol: str) -> float:
        """Obtiene el último precio conocido."""
        snap = self._snapshots.get(symbol)
        if snap:
            return snap.price
        df = self._dataframes.get(symbol)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
        return 0.0

    def get_data_age(self, symbol: str) -> float:
        """Retorna cuántos segundos han pasado desde el último dato recibido.

        Returns:
            Edad en segundos. float('inf') si nunca se recibió dato.
        """
        last = self._last_data_time.get(symbol)
        if last is None:
            return float("inf")
        return time.time() - last

    def is_data_stale(self, symbol: str, threshold_sec: float = 30.0) -> bool:
        """Verifica si los datos de un símbolo están stale."""
        return self.get_data_age(symbol) > threshold_sec

    def get_current_atr(self, symbol: str) -> float:
        """Obtiene el ATR actual."""
        df = self._dataframes.get(symbol)
        if df is not None and not df.empty and "atr" in df.columns:
            val = df["atr"].iloc[-1]
            return float(val) if not pd.isna(val) else 0.0
        return 0.0

    def get_funding_rate(self, symbol: str) -> float:
        """Obtiene el funding rate actual."""
        snap = self._snapshots.get(symbol)
        return snap.funding_rate if snap else 0.0

    async def refresh_all(self) -> None:
        """Refresca snapshots de todos los símbolos."""
        tasks = [self.update_snapshot(s.symbol) for s in self.settings.symbols]
        await asyncio.gather(*tasks, return_exceptions=True)
