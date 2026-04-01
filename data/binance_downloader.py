"""
Descarga datos históricos de Binance para backtesting.

Usa la API pública de Binance (sin API key) para obtener:
  - Klines 1m continuas (meses/años de velas con volumen real)
  - Trades agregados (aggTrades) tick-by-tick

Los datos se guardan en el mismo formato Parquet que usa el backtester
de BotStrike, así que son directamente compatibles.

Mapeo de símbolos:
  BTC-USD  → BTCUSDT
  ETH-USD  → ETHUSDT
  ADA-USD  → ADAUSDT

Uso:
    downloader = BinanceDownloader(data_dir="data/binance")
    await downloader.download_klines("BTC-USD", days=90)
    await downloader.download_trades("BTC-USD", days=7)

    # O desde CLI:
    python main.py --download-binance --days 90
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import aiohttp
import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────

BINANCE_BASE_URL = "https://api.binance.com/api/v3"
BINANCE_KLINES_URL = f"{BINANCE_BASE_URL}/klines"
BINANCE_AGG_TRADES_URL = f"{BINANCE_BASE_URL}/aggTrades"

# Binance devuelve max 1000 registros por request
KLINE_BATCH_SIZE = 1000   # 1000 velas de 1m = ~16.6 horas
TRADE_BATCH_SIZE = 1000   # 1000 aggTrades por request

# Rate limiting (Binance permite 1200 req/min en peso, klines/trades pesan 1-5)
REQUEST_DELAY = 0.08  # 80ms entre requests (~750 req/min, bien dentro del límite)

# Mapeo de símbolos BotStrike → Binance
SYMBOL_MAP: Dict[str, str] = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "ADA-USD": "ADAUSDT",
    "SOL-USD": "SOLUSDT",
    "DOT-USD": "DOTUSDT",
    "AVAX-USD": "AVAXUSDT",
    "LINK-USD": "LINKUSDT",
    "MATIC-USD": "MATICUSDT",
}

# Inverso para resolución
SYMBOL_MAP_REVERSE = {v: k for k, v in SYMBOL_MAP.items()}


def _to_binance_symbol(symbol: str) -> str:
    """Convierte símbolo BotStrike (BTC-USD) a Binance (BTCUSDT)."""
    if symbol in SYMBOL_MAP:
        return SYMBOL_MAP[symbol]
    # Fallback: quitar guión y añadir T
    return symbol.replace("-", "") + "T"


class BinanceDownloader:
    """Descarga datos históricos de Binance (API pública, sin key)."""

    def __init__(
        self,
        data_dir: str = "data/binance",
        symbols: Optional[List[str]] = None,
    ) -> None:
        self.data_dir = data_dir
        self.symbols = symbols or ["BTC-USD", "ETH-USD", "ADA-USD"]
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_count = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, url: str, params: dict) -> list:
        """Request con rate limiting y reintentos."""
        session = await self._get_session()
        for attempt in range(3):
            try:
                await asyncio.sleep(REQUEST_DELAY)
                async with session.get(url, params=params) as resp:
                    if resp.status == 429:
                        # Rate limited — esperar y reintentar
                        wait = int(resp.headers.get("Retry-After", "10"))
                        logger.warning("binance_rate_limited", wait=wait)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status == 418:
                        # IP ban temporal
                        logger.error("binance_ip_banned", status=418)
                        await asyncio.sleep(60)
                        continue
                    resp.raise_for_status()
                    self._request_count += 1
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < 2:
                    logger.warning("binance_request_retry", attempt=attempt, error=str(e))
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
        return []

    # ── Descarga de Klines ────────────────────────────────────────

    async def download_klines(
        self,
        symbol: str,
        days: int = 90,
        interval: str = "1m",
        end_time: Optional[int] = None,
    ) -> str:
        """Descarga klines históricas de Binance.

        Args:
            symbol: Símbolo BotStrike (ej: "BTC-USD")
            days: Días hacia atrás desde ahora
            interval: Intervalo de velas ("1m", "5m", "15m", "1h", "1d")
            end_time: Timestamp ms final (default: ahora)

        Returns:
            Ruta al archivo Parquet generado
        """
        binance_sym = _to_binance_symbol(symbol)
        now_ms = end_time or int(time.time() * 1000)
        start_ms = now_ms - (days * 24 * 60 * 60 * 1000)

        # Directorio de salida
        out_dir = os.path.join(self.data_dir, "klines", symbol)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{interval}.parquet")

        # Si ya existe, cargar y continuar desde donde quedó
        existing_df = None
        if os.path.exists(out_path):
            try:
                existing_df = pd.read_parquet(out_path)
                last_ts = int(existing_df["timestamp"].max())
                if last_ts > start_ms:
                    start_ms = last_ts + 60000  # siguiente minuto
                    logger.info(
                        "klines_resuming",
                        symbol=symbol,
                        from_date=datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
                    )
            except Exception:
                existing_df = None

        start_date = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end_date = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)

        logger.info(
            "klines_download_start",
            symbol=symbol,
            binance_symbol=binance_sym,
            interval=interval,
            from_date=start_date.strftime("%Y-%m-%d %H:%M"),
            to_date=end_date.strftime("%Y-%m-%d %H:%M"),
            days=days,
        )

        all_klines = []
        cursor_ms = start_ms
        batch_num = 0
        total_expected = (now_ms - start_ms) / 60000  # velas esperadas para 1m

        while cursor_ms < now_ms:
            data = await self._request(BINANCE_KLINES_URL, {
                "symbol": binance_sym,
                "interval": interval,
                "startTime": cursor_ms,
                "endTime": now_ms,
                "limit": KLINE_BATCH_SIZE,
            })

            if not data:
                break

            for k in data:
                all_klines.append({
                    "timestamp": int(k[0]),     # open time ms
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": int(k[6]),
                    "quote_volume": float(k[7]),
                    "num_trades": int(k[8]),
                    "taker_buy_volume": float(k[9]),
                    "taker_buy_quote_volume": float(k[10]),
                })

            # Avanzar cursor al siguiente lote
            last_open_time = int(data[-1][0])
            if last_open_time <= cursor_ms:
                break  # no avanzamos, evitar loop infinito
            cursor_ms = last_open_time + 60000  # siguiente minuto

            batch_num += 1
            downloaded = len(all_klines)
            pct = min(downloaded / total_expected * 100, 100) if total_expected > 0 else 0
            if batch_num % 20 == 0:
                print(
                    f"  [{symbol}] Klines: {downloaded:,} velas descargadas ({pct:.1f}%)",
                    flush=True,
                )

        if not all_klines:
            logger.warning("klines_download_empty", symbol=symbol)
            return out_path

        df = pd.DataFrame(all_klines)

        # Combinar con datos existentes si hay
        if existing_df is not None and not existing_df.empty:
            df = pd.concat([existing_df, df], ignore_index=True)

        df = df.drop_duplicates(subset=["timestamp"], keep="last")
        df = df.sort_values("timestamp").reset_index(drop=True)
        df.to_parquet(out_path, index=False)

        start_real = datetime.fromtimestamp(df["timestamp"].min() / 1000, tz=timezone.utc)
        end_real = datetime.fromtimestamp(df["timestamp"].max() / 1000, tz=timezone.utc)

        logger.info(
            "klines_download_complete",
            symbol=symbol,
            total_candles=len(df),
            from_date=start_real.strftime("%Y-%m-%d"),
            to_date=end_real.strftime("%Y-%m-%d"),
            file=out_path,
        )
        print(
            f"  [{symbol}] Klines completas: {len(df):,} velas "
            f"({start_real.strftime('%Y-%m-%d')} -> {end_real.strftime('%Y-%m-%d')})",
            flush=True,
        )
        return out_path

    # ── Descarga de Trades ────────────────────────────────────────

    async def download_trades(
        self,
        symbol: str,
        days: int = 7,
        end_time: Optional[int] = None,
    ) -> str:
        """Descarga trades agregados históricos de Binance.

        Args:
            symbol: Símbolo BotStrike (ej: "BTC-USD")
            days: Días hacia atrás
            end_time: Timestamp ms final (default: ahora)

        Returns:
            Ruta al directorio con archivos Parquet diarios
        """
        binance_sym = _to_binance_symbol(symbol)
        now_ms = end_time or int(time.time() * 1000)
        start_ms = int(now_ms - (days * 24 * 60 * 60 * 1000))

        out_dir = os.path.join(self.data_dir, "trades", symbol)
        os.makedirs(out_dir, exist_ok=True)

        start_date = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end_date = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)

        logger.info(
            "trades_download_start",
            symbol=symbol,
            binance_symbol=binance_sym,
            from_date=start_date.strftime("%Y-%m-%d"),
            to_date=end_date.strftime("%Y-%m-%d"),
            days=days,
        )

        all_trades: List[dict] = []
        cursor_ms = start_ms
        batch_num = 0
        current_day = ""

        # Buscar el primer aggTrade ID para poder paginar por ID (más rápido)
        seed = await self._request(BINANCE_AGG_TRADES_URL, {
            "symbol": binance_sym,
            "startTime": int(cursor_ms),
            "limit": 1,
        })
        from_id = int(seed[0]["a"]) if seed else None

        while cursor_ms < now_ms:
            # Paginar por fromId si disponible (más rápido que por tiempo)
            params = {"symbol": binance_sym, "limit": TRADE_BATCH_SIZE}
            if from_id is not None:
                params["fromId"] = from_id
            else:
                params["startTime"] = int(cursor_ms)

            data = await self._request(BINANCE_AGG_TRADES_URL, params)

            if not data:
                break

            for t in data:
                trade_time = int(t["T"])
                if trade_time > now_ms:
                    break
                all_trades.append({
                    "timestamp": trade_time,
                    "price": float(t["p"]),
                    "quantity": float(t["q"]),
                    "side": "SELL" if t.get("m", False) else "BUY",
                    "trade_id": int(t["a"]),
                })

            # Avanzar por ID (mucho más eficiente)
            last_id = int(data[-1]["a"])
            last_time = int(data[-1]["T"])
            if last_time > now_ms:
                break
            from_id = last_id + 1
            cursor_ms = last_time

            batch_num += 1

            # Flush diario: cuando cambia el día, escribir lo acumulado
            day_str = datetime.fromtimestamp(last_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            if current_day and day_str != current_day and all_trades:
                self._save_trades_day(all_trades, symbol, out_dir)
                all_trades = []

            current_day = day_str

            if batch_num % 100 == 0:
                progress_date = datetime.fromtimestamp(cursor_ms / 1000, tz=timezone.utc)
                total_span = now_ms - start_ms
                done_span = cursor_ms - start_ms
                pct = done_span / total_span * 100 if total_span > 0 else 0
                print(
                    f"  [{symbol}] Trades: procesando {progress_date.strftime('%Y-%m-%d %H:%M')} "
                    f"({pct:.1f}%) - {batch_num} batches",
                    flush=True,
                )

        # Flush final
        if all_trades:
            self._save_trades_day(all_trades, symbol, out_dir)

        # Contar total
        total = 0
        files = sorted(f for f in os.listdir(out_dir) if f.endswith(".parquet"))
        for f in files:
            df = pd.read_parquet(os.path.join(out_dir, f))
            total += len(df)

        logger.info(
            "trades_download_complete",
            symbol=symbol,
            total_trades=total,
            files=len(files),
            dir=out_dir,
        )
        print(
            f"  [{symbol}] Trades completos: {total:,} trades en {len(files)} archivos",
            flush=True,
        )
        return out_dir

    def _save_trades_day(self, trades: List[dict], symbol: str, out_dir: str) -> None:
        """Guarda trades particionados por día UTC."""
        df = pd.DataFrame(trades)
        df["_date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")

        for day, group in df.groupby("_date"):
            group = group.drop(columns=["_date"])
            path = os.path.join(out_dir, f"{day}.parquet")

            if os.path.exists(path):
                try:
                    existing = pd.read_parquet(path)
                    group = pd.concat([existing, group], ignore_index=True)
                except Exception:
                    pass

            group = group.drop_duplicates(subset=["trade_id"], keep="last")
            group = group.sort_values("timestamp").reset_index(drop=True)
            group.to_parquet(path, index=False)

    # ── Descarga completa ─────────────────────────────────────────

    async def download_all(
        self,
        kline_days: int = 90,
        trade_days: int = 7,
        kline_interval: str = "1m",
    ) -> Dict[str, dict]:
        """Descarga klines y trades para todos los símbolos configurados.

        Args:
            kline_days: Días de klines a descargar (default 90)
            trade_days: Días de trades a descargar (default 7)
            kline_interval: Intervalo de velas (default "1m")

        Returns:
            Dict con resumen por símbolo
        """
        print("=" * 60)
        print("  Binance Historical Data Downloader")
        print("=" * 60)
        print(f"  Symbols:     {', '.join(self.symbols)}")
        print(f"  Klines:      {kline_days} days ({kline_interval})")
        print(f"  Trades:      {trade_days} days")
        print(f"  Output dir:  {os.path.abspath(self.data_dir)}")
        print("=" * 60)
        print()

        results = {}
        start_time = time.time()

        for symbol in self.symbols:
            print(f"\n--- {symbol} ---")

            # Klines
            kline_path = await self.download_klines(
                symbol, days=kline_days, interval=kline_interval
            )

            # Trades
            trade_dir = await self.download_trades(symbol, days=trade_days)

            # Resumen
            kline_df = pd.read_parquet(kline_path) if os.path.exists(kline_path) else pd.DataFrame()
            trade_files = sorted(
                f for f in os.listdir(trade_dir) if f.endswith(".parquet")
            ) if os.path.exists(trade_dir) else []
            trade_count = sum(
                len(pd.read_parquet(os.path.join(trade_dir, f))) for f in trade_files
            )

            results[symbol] = {
                "klines": len(kline_df),
                "kline_path": kline_path,
                "trades": trade_count,
                "trade_files": len(trade_files),
                "trade_dir": trade_dir,
            }

        elapsed = time.time() - start_time
        await self.close()

        print(f"\n{'=' * 60}")
        print(f"  Descarga completada en {elapsed:.1f}s")
        print(f"  Requests totales: {self._request_count}")
        print(f"{'=' * 60}")
        for sym, info in results.items():
            print(f"  {sym}: {info['klines']:,} klines, {info['trades']:,} trades")
        print(f"{'=' * 60}\n")

        return results
