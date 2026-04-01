"""
StrikeDataCollector — Servicio de recoleccion continua de datos de Strike Finance.

SIEMPRE recolecta de MAINNET (datos reales para backtesting/simulacion).
El flag --testnet solo afecta la ejecucion de ordenes, no la recoleccion.

Recopila y almacena automaticamente:
  1. Trades tick-by-tick (WebSocket + REST) -> Parquet diario
  2. Klines/velas (WebSocket + REST) -> Parquet por intervalo
  3. Orderbook depth updates (WebSocket) -> Parquet diario
  4. Snapshots de orderbook (REST periodico) -> Parquet diario

Estructura de almacenamiento:
  data/
  +-- trades/
  |   +-- BTC-USD/
  |   |   +-- 2026-03-25.parquet
  |   |   +-- 2026-03-26.parquet
  |   +-- ETH-USD/
  +-- klines/
  |   +-- BTC-USD/
  |   |   +-- 1m.parquet         (append incremental)
  |   +-- ...
  +-- orderbook/
  |   +-- BTC-USD/
  |       +-- 2026-03-25.parquet
  +-- metadata.json              (estado de la recoleccion)

Uso:
    collector = StrikeDataCollector(settings)
    await collector.start()     # arranca recoleccion continua
    await collector.stop()      # detiene limpiamente

    # O desde CLI:
    python main.py --collect-data
"""
from __future__ import annotations
import asyncio
import copy
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import tempfile

import numpy as np
import pandas as pd

from config.settings import Settings
from exchange.strike_client import StrikeClient
from exchange.websocket_client import StrikeWebSocket
import structlog

logger = structlog.get_logger(__name__)

# ── Intervalos de recoleccion ──────────────────────────────────────
# Flush: cada cuanto se escribe el buffer a disco (protege contra crash)
TRADE_FLUSH_INTERVAL = 30       # 30s - trades son lo mas valioso
KLINE_FLUSH_INTERVAL = 60       # 60s - klines cambian cada minuto
ORDERBOOK_FLUSH_INTERVAL = 30   # 30s - orderbook genera mucho volumen

# REST polling: backup cuando WS no produce trades (complementa, no reemplaza)
REST_TRADE_POLL_INTERVAL = 15   # 15s - captura trades que el WS pudo perder
REST_KLINE_POLL_INTERVAL = 60   # 60s - sync klines historicas
REST_OB_SNAPSHOT_INTERVAL = 10  # 10s - snapshots completos del libro

# Status
STATUS_PRINT_INTERVAL = 60      # 60s - log de estado

# URLs de mainnet (SIEMPRE usamos mainnet para datos)
MAINNET_PRICE_URL = "https://api.strikefinance.org/price"
MAINNET_BASE_URL = "https://api.strikefinance.org"
MAINNET_WS_URL = "wss://api.strikefinance.org/ws/price"


class StrikeDataCollector:
    """Servicio de recoleccion continua de datos de Strike Finance.

    Siempre recolecta de MAINNET independientemente de la config de testnet,
    porque los datos para backtesting deben reflejar el mercado real.
    """

    def __init__(
        self,
        settings: Settings,
        data_dir: str = "data",
        symbols: Optional[List[str]] = None,
        notifier: Optional[Any] = None,
    ) -> None:
        self.settings = copy.deepcopy(settings)
        self.data_dir = os.path.abspath(data_dir)
        self.symbols = symbols or settings.symbol_names

        # Forzar URLs de mainnet para recoleccion
        self._apply_mainnet_urls()

        self.client = StrikeClient(settings)
        self.websocket = StrikeWebSocket(settings)
        self._notifier = notifier
        self._running = False

        # Buffers en memoria — el WS llena estos, los flush loops los escriben
        self._trade_buffers: Dict[str, List[Dict]] = defaultdict(list)
        self._kline_buffers: Dict[str, List[Dict]] = defaultdict(list)
        self._orderbook_buffers: Dict[str, List[Dict]] = defaultdict(list)

        # Contadores para monitoreo
        self._ws_trade_count: Dict[str, int] = defaultdict(int)
        self._ws_depth_count: Dict[str, int] = defaultdict(int)
        self._rest_trade_count: Dict[str, int] = defaultdict(int)

        # Metadata de recoleccion (cargar ANTES de leer last_trade_id)
        self._metadata_path = os.path.join(self.data_dir, "metadata.json")
        self._metadata: Dict = self._load_metadata()

        # Recuperar last_trade_id del metadata (sobrevive reinicios)
        self._last_trade_id: Dict[str, int] = {}
        for sym in self.symbols:
            saved_id = self._metadata.get(sym, {}).get("last_trade_id", 0)
            if saved_id:
                self._last_trade_id[sym] = int(saved_id)

        # Crear directorios y limpiar temporales huérfanos de crashes previos
        for subdir in ["trades", "klines", "orderbook"]:
            for symbol in self.symbols:
                dirpath = os.path.join(self.data_dir, subdir, symbol)
                os.makedirs(dirpath, exist_ok=True)
                for f in os.listdir(dirpath):
                    if f.startswith(".parquet_") and f.endswith(".tmp"):
                        try:
                            os.remove(os.path.join(dirpath, f))
                            logger.info("orphan_tmp_cleaned", file=f, dir=dirpath)
                        except OSError:
                            pass

    def _apply_mainnet_urls(self) -> None:
        """Fuerza URLs de mainnet en settings para recoleccion de datos."""
        self.settings.api_price_url = MAINNET_PRICE_URL
        self.settings.api_base_url = MAINNET_BASE_URL
        self.settings.ws_market_url = MAINNET_WS_URL

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Arranca la recoleccion continua de datos."""
        self._running = True
        logger.info(
            "data_collector_starting",
            symbols=self.symbols,
            source="MAINNET",
            ws_url=MAINNET_WS_URL,
            rest_url=MAINNET_PRICE_URL,
        )

        # Registrar callbacks WebSocket
        self._setup_callbacks()

        # Carga inicial: trades + klines historicas via REST
        await self._fetch_initial_data()

        # Lanzar todos los loops concurrentes
        tasks = [
            # WebSocket: fuente primaria de datos en tiempo real
            asyncio.create_task(self.websocket.connect_market(), name="ws_market"),
            # Flush loops: escriben buffers a disco periodicamente
            asyncio.create_task(self._flush_trades_loop(), name="flush_trades"),
            asyncio.create_task(self._flush_klines_loop(), name="flush_klines"),
            asyncio.create_task(self._flush_orderbook_loop(), name="flush_ob"),
            # REST polling: complementa WS, captura gaps
            asyncio.create_task(self._rest_trade_poll_loop(), name="rest_trades"),
            asyncio.create_task(self._rest_kline_poll_loop(), name="rest_klines"),
            asyncio.create_task(self._rest_orderbook_poll_loop(), name="rest_ob"),
            # Status
            asyncio.create_task(self._print_status_loop(), name="status"),
        ]

        # Esperar conexion WS y suscribir canales
        await asyncio.sleep(2)
        for symbol in self.symbols:
            await self.websocket.subscribe("trade", symbol)
            await self.websocket.subscribe("depth", symbol)
            await self.websocket.subscribe("kline_1m", symbol)
            print(f"  [WS] Subscribed: {symbol} (trade, depth, kline_1m)")

        print(f"\n  Collector ACTIVE - showing status every {STATUS_PRINT_INTERVAL}s")
        print(f"  Flush: trades/{TRADE_FLUSH_INTERVAL}s, klines/{KLINE_FLUSH_INTERVAL}s, ob/{ORDERBOOK_FLUSH_INTERVAL}s")
        print(f"  {'='*55}\n", flush=True)

        # Telegram notification
        if self._notifier:
            await self._notifier.start()
            await self._notifier.notify(
                "📦 <b>Recolector de datos encendido</b>\n\n"
                f"Monedas: {', '.join(self.symbols)}\n"
                f"Fuente: Mainnet (datos reales)\n"
                f"Guardando en: {self.data_dir}\n\n"
                f"Recibiras un resumen cada 5 minutos.",
            )

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Detiene la recoleccion y hace flush final de todos los buffers."""
        self._running = False
        logger.info("data_collector_stopping")
        # Flush final — no perder nada que este en memoria
        for symbol in self.symbols:
            for flush_fn, args in [
                (self._flush_trades, (symbol,)),
                (self._flush_klines, (symbol, True)),
                (self._flush_orderbook, (symbol,)),
            ]:
                try:
                    flush_fn(*args)
                except Exception as e:
                    logger.error("flush_on_stop_error", symbol=symbol, fn=flush_fn.__name__, error=str(e))
        self._save_metadata()
        await self.websocket.stop()
        await self.client.close()
        logger.info("data_collector_stopped")

        # Telegram notification
        if self._notifier:
            await self._notifier.notify(
                "📦 <b>Recolector de datos apagado</b>\n\n"
                f"Monedas: {', '.join(self.symbols)}\n"
                f"Todos los datos se han guardado correctamente.",
            )
            await self._notifier.stop()

    # ── WebSocket Callbacks (fuente primaria) ─────────────────────

    def _setup_callbacks(self) -> None:
        """Registra callbacks para cada tick en tiempo real."""

        async def on_trade(data: Dict) -> None:
            """Cada trade individual del matching engine."""
            symbol = data.get("s", "")
            if not symbol or symbol not in self.symbols:
                return
            trade_id = data.get("t", data.get("a", 0))
            self._trade_buffers[symbol].append({
                "timestamp": float(data.get("T", time.time() * 1000)),
                "price": float(data.get("p", 0)),
                "quantity": float(data.get("q", 0)),
                "side": "BUY" if not data.get("m", False) else "SELL",
                "trade_id": int(trade_id) if trade_id else 0,
            })
            self._ws_trade_count[symbol] += 1
            # Trackear ultimo trade ID para dedup con REST
            if trade_id:
                self._last_trade_id[symbol] = max(
                    self._last_trade_id.get(symbol, 0), int(trade_id)
                )

        async def on_depth(data: Dict) -> None:
            """Cada update del orderbook — captura microestructura."""
            symbol = data.get("s", "")
            if not symbol or symbol not in self.symbols:
                return
            ts = float(data.get("E", time.time() * 1000))
            bids = data.get("b", data.get("bids", []))
            asks = data.get("a", data.get("asks", []))
            if not bids or not asks:
                return  # Ambos lados necesarios para un snapshot válido
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
                return  # Descartar datos inválidos (crossed book, zeros)
            mid = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            self._orderbook_buffers[symbol].append({
                "timestamp": ts,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid_price": mid,
                "spread": spread,
                "bid_depth": sum(float(b[1]) for b in bids[:10]) if bids else 0,
                "ask_depth": sum(float(a[1]) for a in asks[:10]) if asks else 0,
                "bid_levels": len(bids),
                "ask_levels": len(asks),
            })
            self._ws_depth_count[symbol] += 1

        async def on_kline(data: Dict) -> None:
            """Cada update de vela (abierta y cerrada)."""
            k = data.get("k", data)
            symbol = k.get("s", data.get("s", ""))
            if not symbol or symbol not in self.symbols:
                return
            self._kline_buffers[symbol].append({
                "timestamp": float(k.get("t", 0)),
                "open": float(k.get("o", 0)),
                "high": float(k.get("h", 0)),
                "low": float(k.get("l", 0)),
                "close": float(k.get("c", 0)),
                "volume": float(k.get("v", 0)),
                "closed": k.get("x", False),
            })

        self.websocket.on("trade", on_trade)
        self.websocket.on("depth", on_depth)
        self.websocket.on("depthUpdate", on_depth)
        self.websocket.on("kline", on_kline)
        self.websocket.on("kline_1m", on_kline)
        self.websocket.on("*", self._handle_kline_wildcard)

    async def _handle_kline_wildcard(self, data: Dict) -> None:
        """Captura eventos kline que vienen con channel=kline_*."""
        channel = data.get("channel", data.get("e", ""))
        if isinstance(channel, str) and channel.startswith("kline"):
            k = data.get("k", data)
            symbol = k.get("s", data.get("s", ""))
            if symbol and symbol in self.symbols:
                self._kline_buffers[symbol].append({
                    "timestamp": float(k.get("t", 0)),
                    "open": float(k.get("o", 0)),
                    "high": float(k.get("h", 0)),
                    "low": float(k.get("l", 0)),
                    "close": float(k.get("c", 0)),
                    "volume": float(k.get("v", 0)),
                    "closed": k.get("x", False),
                })

    # ── Escritura atómica a disco ────────────────────────────────────

    @staticmethod
    def _atomic_write_parquet(df: pd.DataFrame, path: str) -> None:
        """Escribe un DataFrame a Parquet de forma atómica.

        Escribe primero a un archivo temporal en el mismo directorio y luego
        hace os.replace() que es atómico en NTFS/ext4. Si el PC se apaga a
        medio-write, el archivo original queda intacto.
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp", prefix=".parquet_", dir=os.path.dirname(path)
        )
        try:
            os.close(fd)
            df.to_parquet(tmp_path, index=False)
            os.replace(tmp_path, path)  # atómico en NTFS y ext4
        except BaseException:
            # Si falla la escritura, limpiar el temporal
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    # ── Flush a disco ──────────────────────────────────────────────

    def _flush_trades(self, symbol: str) -> None:
        """Escribe trades del buffer a Parquet, archivo diario por fecha del trade."""
        buf = self._trade_buffers.get(symbol, [])
        if not buf:
            return

        # Swap atómico: capturar buffer actual y reemplazar con lista vacía
        self._trade_buffers[symbol] = []
        df = pd.DataFrame(buf)

        # Determinar la fecha UTC de cada trade a partir de su timestamp
        ts_col = df["timestamp"]
        if ts_col.max() > 1e12:  # milisegundos
            df["_date"] = pd.to_datetime(ts_col, unit="ms", utc=True).dt.strftime("%Y-%m-%d")
        else:  # segundos
            df["_date"] = pd.to_datetime(ts_col, unit="s", utc=True).dt.strftime("%Y-%m-%d")

        # Escribir cada grupo de trades a su archivo diario correspondiente
        for day, group in df.groupby("_date"):
            group = group.drop(columns=["_date"])
            path = os.path.join(self.data_dir, "trades", symbol, f"{day}.parquet")

            if os.path.exists(path):
                try:
                    existing = pd.read_parquet(path)
                    group = pd.concat([existing, group], ignore_index=True)
                except Exception as e:
                    logger.warning("corrupt_parquet_replaced", path=path, error=str(e))
                    os.remove(path)

            # Dedup: por trade_id si existe, sino por timestamp+price+qty
            if "trade_id" in group.columns and (group["trade_id"] != 0).any():
                group = group.drop_duplicates(subset=["trade_id"], keep="last")
            else:
                group = group.drop_duplicates(subset=["timestamp", "price", "quantity"])

            group = group.sort_values("timestamp")
            self._atomic_write_parquet(group, path)

        # Actualizar metadata y trackear ultimo trade_id para dedup cross-restart
        self._metadata.setdefault(symbol, {})
        self._metadata[symbol]["last_trade_flush"] = time.time()
        self._metadata[symbol]["total_trades_today"] = len(df)
        if "trade_id" in df.columns:
            max_id = int(df["trade_id"].max())
            if max_id > 0:
                self._metadata[symbol]["last_trade_id"] = max_id

    def _flush_klines(self, symbol: str, force: bool = False) -> None:
        """Escribe klines a Parquet. Solo barras cerradas (salvo force=True en shutdown)."""
        buf = self._kline_buffers.get(symbol, [])
        if not buf:
            return

        if force:
            # En shutdown, guardar todo
            closed = buf
        else:
            closed = [k for k in buf if k.get("closed", False)]
            if not closed:
                return

        df = pd.DataFrame(closed)
        df = df.drop(columns=["closed"], errors="ignore")
        path = os.path.join(self.data_dir, "klines", symbol, "1m.parquet")

        if os.path.exists(path):
            try:
                existing = pd.read_parquet(path)
                df = pd.concat([existing, df], ignore_index=True)
            except Exception as e:
                logger.warning("corrupt_parquet_replaced", path=path, error=str(e))
                os.remove(path)

        df = df.drop_duplicates(subset=["timestamp"])
        df = df.sort_values("timestamp")
        self._atomic_write_parquet(df, path)

        # Limpiar cerradas, mantener barra abierta actual
        if force:
            self._kline_buffers[symbol].clear()
        else:
            self._kline_buffers[symbol] = [k for k in buf if not k.get("closed", False)]

    def _flush_orderbook(self, symbol: str) -> None:
        """Escribe snapshots de orderbook a Parquet diario. Deduplica por timestamp."""
        buf = self._orderbook_buffers.get(symbol, [])
        if not buf:
            return

        # Swap atómico para evitar perder datos durante flush
        self._orderbook_buffers[symbol] = []
        df = pd.DataFrame(buf)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(self.data_dir, "orderbook", symbol, f"{today}.parquet")

        if os.path.exists(path):
            try:
                existing = pd.read_parquet(path)
                df = pd.concat([existing, df], ignore_index=True)
            except Exception as e:
                logger.warning("corrupt_parquet_replaced", path=path, error=str(e))
                os.remove(path)

        # Dedup: redondear a ms y eliminar duplicados del mismo instante
        df["_ts_ms"] = (df["timestamp"] * 1).astype(np.int64)
        df = df.drop_duplicates(subset=["_ts_ms"], keep="last")
        df = df.drop(columns=["_ts_ms"])
        df = df.sort_values("timestamp")
        self._atomic_write_parquet(df, path)

    # ── Carga inicial ──────────────────────────────────────────────

    async def _fetch_initial_data(self) -> None:
        """Carga datos historicos al arrancar para no empezar de cero."""
        # 1. Trades recientes (ultimos 1000 por simbolo)
        for symbol in self.symbols:
            try:
                trades = await self.client.get_recent_trades(symbol, limit=1000)
                if trades:
                    for t in trades:
                        trade_id = t.get("id", t.get("a", 0))
                        self._trade_buffers[symbol].append({
                            "timestamp": float(t.get("time", t.get("T", 0))),
                            "price": float(t.get("price", t.get("p", 0))),
                            "quantity": float(t.get("qty", t.get("q", 0))),
                            "side": "BUY" if not t.get("m", t.get("isBuyerMaker", False)) else "SELL",
                            "trade_id": int(trade_id) if trade_id else 0,
                        })
                        if trade_id:
                            self._last_trade_id[symbol] = max(
                                self._last_trade_id.get(symbol, 0), int(trade_id)
                            )
                    self._flush_trades(symbol)
                    logger.info("initial_trades_loaded", symbol=symbol, count=len(trades))
                else:
                    logger.warning("initial_trades_empty", symbol=symbol)
            except Exception as e:
                logger.warning("initial_trades_error", symbol=symbol, error=str(e))

        # 2. Klines historicas (ultimas 1000 barras de 1m)
        for symbol in self.symbols:
            try:
                raw_klines = await self.client.get_klines(symbol, interval="1m", limit=1000)
                if raw_klines:
                    for k in raw_klines:
                        if len(k) >= 6:
                            self._kline_buffers[symbol].append({
                                "timestamp": float(k[0]),
                                "open": float(k[1]),
                                "high": float(k[2]),
                                "low": float(k[3]),
                                "close": float(k[4]),
                                "volume": float(k[5]),
                                "closed": True,
                            })
                    self._flush_klines(symbol)
                    logger.info("initial_klines_loaded", symbol=symbol, count=len(raw_klines))
            except Exception as e:
                logger.warning("initial_klines_error", symbol=symbol, error=str(e))

        # 3. Snapshot inicial de orderbook
        for symbol in self.symbols:
            try:
                ob = await self.client.get_orderbook(symbol, limit=20)
                if ob and ob.bids and ob.asks:
                    best_bid = ob.bids[0].price
                    best_ask = ob.asks[0].price
                    self._orderbook_buffers[symbol].append({
                        "timestamp": time.time() * 1000,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "mid_price": (best_bid + best_ask) / 2,
                        "spread": best_ask - best_bid,
                        "bid_depth": sum(l.quantity for l in ob.bids[:10]),
                        "ask_depth": sum(l.quantity for l in ob.asks[:10]),
                        "bid_levels": len(ob.bids),
                        "ask_levels": len(ob.asks),
                    })
                    self._flush_orderbook(symbol)
                    logger.info("initial_orderbook_loaded", symbol=symbol)
            except Exception as e:
                logger.warning("initial_orderbook_error", symbol=symbol, error=str(e))

    # ── Flush loops (escriben buffers a disco) ────────────────────

    async def _flush_trades_loop(self) -> None:
        while self._running:
            await asyncio.sleep(TRADE_FLUSH_INTERVAL)
            flushed = []
            for symbol in self.symbols:
                n = len(self._trade_buffers.get(symbol, []))
                self._flush_trades(symbol)
                if n > 0:
                    flushed.append(f"{symbol}: {n} operaciones")
            if flushed:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"  [{ts}] Guardado trades en disco -> {', '.join(flushed)}", flush=True)

    async def _flush_klines_loop(self) -> None:
        while self._running:
            await asyncio.sleep(KLINE_FLUSH_INTERVAL)
            flushed = []
            for symbol in self.symbols:
                n = len([k for k in self._kline_buffers.get(symbol, []) if k.get("closed")])
                self._flush_klines(symbol)
                if n > 0:
                    flushed.append(f"{symbol}: {n} velas")
            if flushed:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"  [{ts}] Guardado velas en disco  -> {', '.join(flushed)}", flush=True)

    async def _flush_orderbook_loop(self) -> None:
        while self._running:
            await asyncio.sleep(ORDERBOOK_FLUSH_INTERVAL)
            flushed = []
            for symbol in self.symbols:
                n = len(self._orderbook_buffers.get(symbol, []))
                self._flush_orderbook(symbol)
                if n > 0:
                    flushed.append(f"{symbol}: {n} capturas")
            if flushed:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"  [{ts}] Guardado orderbook       -> {', '.join(flushed)}", flush=True)

    # ── REST polling (complementa WS, captura gaps) ───────────────

    async def _rest_trade_poll_loop(self) -> None:
        """Polling REST de trades cada 15s. Captura trades que WS pudo perder."""
        await asyncio.sleep(REST_TRADE_POLL_INTERVAL)
        while self._running:
            for symbol in self.symbols:
                try:
                    trades = await self.client.get_recent_trades(symbol, limit=1000)
                    if trades:
                        new_count = 0
                        for t in trades:
                            trade_id = t.get("id", t.get("a", 0))
                            # Solo agregar si es nuevo (no duplicar lo del WS)
                            if trade_id and self._last_trade_id.get(symbol, 0) >= int(trade_id):
                                continue
                            self._trade_buffers[symbol].append({
                                "timestamp": float(t.get("time", t.get("T", 0))),
                                "price": float(t.get("price", t.get("p", 0))),
                                "quantity": float(t.get("qty", t.get("q", 0))),
                                "side": "BUY" if not t.get("m", t.get("isBuyerMaker", False)) else "SELL",
                                "trade_id": int(trade_id) if trade_id else 0,
                            })
                            new_count += 1
                            if trade_id:
                                self._last_trade_id[symbol] = max(
                                    self._last_trade_id.get(symbol, 0), int(trade_id)
                                )
                        if new_count > 0:
                            self._rest_trade_count[symbol] += new_count
                            logger.debug("rest_new_trades", symbol=symbol, new=new_count)
                except Exception as e:
                    logger.warning("rest_trade_poll_error", symbol=symbol, error=str(e))
            await asyncio.sleep(REST_TRADE_POLL_INTERVAL)

    async def _rest_kline_poll_loop(self) -> None:
        """Polling REST de klines cada 60s. Sincroniza barras cerradas."""
        await asyncio.sleep(REST_KLINE_POLL_INTERVAL)
        while self._running:
            for symbol in self.symbols:
                try:
                    raw = await self.client.get_klines(symbol, interval="1m", limit=10)
                    if raw:
                        for k in raw:
                            if len(k) >= 7:
                                close_time = float(k[6])
                                # Solo agregar si la barra ya cerro
                                if close_time < time.time() * 1000:
                                    self._kline_buffers[symbol].append({
                                        "timestamp": float(k[0]),
                                        "open": float(k[1]),
                                        "high": float(k[2]),
                                        "low": float(k[3]),
                                        "close": float(k[4]),
                                        "volume": float(k[5]),
                                        "closed": True,
                                    })
                except Exception as e:
                    logger.warning("rest_kline_poll_error", symbol=symbol, error=str(e))
            await asyncio.sleep(REST_KLINE_POLL_INTERVAL)

    async def _rest_orderbook_poll_loop(self) -> None:
        """Snapshot completo del orderbook cada 10s via REST."""
        await asyncio.sleep(REST_OB_SNAPSHOT_INTERVAL)
        while self._running:
            for symbol in self.symbols:
                try:
                    ob = await self.client.get_orderbook(symbol, limit=20)
                    if ob and ob.bids and ob.asks:
                        best_bid = ob.bids[0].price
                        best_ask = ob.asks[0].price
                        self._orderbook_buffers[symbol].append({
                            "timestamp": time.time() * 1000,
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "mid_price": (best_bid + best_ask) / 2,
                            "spread": best_ask - best_bid,
                            "bid_depth": sum(l.quantity for l in ob.bids[:10]),
                            "ask_depth": sum(l.quantity for l in ob.asks[:10]),
                            "bid_levels": len(ob.bids),
                            "ask_levels": len(ob.asks),
                        })
                except Exception as e:
                    logger.warning("rest_ob_poll_error", symbol=symbol, error=str(e))
            await asyncio.sleep(REST_OB_SNAPSHOT_INTERVAL)

    # ── Status ─────────────────────────────────────────────────────

    async def _print_status_loop(self) -> None:
        """Imprime estado visible en consola cada minuto."""
        start_time = time.time()
        while self._running:
            await asyncio.sleep(STATUS_PRINT_INTERVAL)
            now_str = datetime.now().strftime("%H:%M:%S")
            uptime_min = (time.time() - start_time) / 60

            print(f"\n  [{now_str}] ══════ Estado del recolector (activo {uptime_min:.0f} min) ══════")

            for symbol in self.symbols:
                buf_trades = len(self._trade_buffers.get(symbol, []))
                sym_meta = self._metadata.get(symbol, {})

                # Datos en disco
                total_trades = sym_meta.get("total_trades_today", 0)
                kline_path = os.path.join(self.data_dir, "klines", symbol, "1m.parquet")
                kline_bars = 0
                if os.path.exists(kline_path):
                    try:
                        kline_bars = len(pd.read_parquet(kline_path))
                    except Exception:
                        pass
                ob_path = os.path.join(
                    self.data_dir, "orderbook", symbol,
                    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.parquet",
                )
                ob_rows = 0
                if os.path.exists(ob_path):
                    try:
                        ob_rows = len(pd.read_parquet(ob_path))
                    except Exception:
                        pass

                # Ultimo precio
                last_price = ""
                if buf_trades > 0:
                    last_t = self._trade_buffers[symbol][-1]
                    last_price = f"${last_t['price']:,.4f}" if last_t['price'] < 1 else f"${last_t['price']:,.2f}"

                ws_t = self._ws_trade_count[symbol]
                rest_t = self._rest_trade_count[symbol]
                nuevos = ws_t + rest_t

                print(f"\n  {symbol}")
                if last_price:
                    print(f"    Precio actual:                 {last_price}")

                # Trades: separar nuevos de historicos
                if nuevos > 0:
                    print(f"    Trades nuevos (esta sesion):   {nuevos}  (en disco hoy: {total_trades:,} total)")
                else:
                    print(f"    Trades nuevos (esta sesion):   ninguno (en disco hoy: {total_trades:,} total)")
                    print(f"      (normal si no hay actividad en el exchange a esta hora)")

                print(f"    Velas de 1 min guardadas:      {kline_bars:,}")
                print(f"    Capturas de orderbook (hoy):   {ob_rows:,}")

                # Estado conexion
                estado = "Conectado y recibiendo" if ws_t > 0 else "Conectado (esperando actividad)"
                print(f"    Conexion WebSocket:            {estado}")

                # Acumular para Telegram
                if self._notifier:
                    last_p = 0
                    if buf_trades > 0:
                        last_p = self._trade_buffers[symbol][-1].get("price", 0)
                    asyncio.ensure_future(self._notifier.notify_collector_status({
                        "symbol": symbol,
                        "total_trades_today": total_trades,
                        "kline_bars": kline_bars,
                        "ob_rows": ob_rows,
                        "ws_trades": ws_t,
                        "rest_trades": rest_t,
                        "last_price": last_p,
                    }))

            print(f"\n  [{now_str}] ═══════════════════════════════════════════════════", flush=True)

            self._save_metadata()

    # ── Metadata ───────────────────────────────────────────────────

    def _load_metadata(self) -> Dict:
        if os.path.exists(self._metadata_path):
            with open(self._metadata_path) as f:
                return json.load(f)
        return {}

    def _save_metadata(self) -> None:
        self._metadata["last_updated"] = time.time()
        self._metadata["symbols"] = self.symbols
        self._metadata["source"] = "mainnet"
        with open(self._metadata_path, "w") as f:
            json.dump(self._metadata, f, indent=2, default=str)

    # ── Acceso a datos almacenados ─────────────────────────────────

    def get_stored_trades(
        self, symbol: str, days: int = 7
    ) -> pd.DataFrame:
        """Lee trades almacenados de los ultimos N dias."""
        trades_dir = os.path.join(self.data_dir, "trades", symbol)
        if not os.path.exists(trades_dir):
            return pd.DataFrame()

        files = sorted(
            [f for f in os.listdir(trades_dir) if f.endswith(".parquet")],
            reverse=True,
        )[:days]

        if not files:
            return pd.DataFrame()

        dfs = []
        for f in files:
            try:
                dfs.append(pd.read_parquet(os.path.join(trades_dir, f)))
            except Exception:
                continue

        if not dfs:
            return pd.DataFrame()

        return pd.concat(dfs, ignore_index=True).sort_values("timestamp")

    def get_stored_klines(self, symbol: str) -> pd.DataFrame:
        """Lee klines almacenadas."""
        path = os.path.join(self.data_dir, "klines", symbol, "1m.parquet")
        if os.path.exists(path):
            return pd.read_parquet(path)
        return pd.DataFrame()

    def get_collection_info(self) -> Dict:
        """Resumen del estado de la recoleccion."""
        info = {"data_dir": self.data_dir, "source": "mainnet", "symbols": {}}
        for symbol in self.symbols:
            sym_info = {}
            # Trades
            trades_dir = os.path.join(self.data_dir, "trades", symbol)
            if os.path.exists(trades_dir):
                files = [f for f in os.listdir(trades_dir) if f.endswith(".parquet")]
                sym_info["trade_files"] = len(files)
                if files:
                    total_rows = sum(
                        len(pd.read_parquet(os.path.join(trades_dir, f)))
                        for f in files
                    )
                    sym_info["total_trades"] = total_rows
                    sym_info["date_range"] = f"{sorted(files)[0]} to {sorted(files)[-1]}"

            # Klines
            kline_path = os.path.join(self.data_dir, "klines", symbol, "1m.parquet")
            if os.path.exists(kline_path):
                kdf = pd.read_parquet(kline_path)
                sym_info["kline_bars"] = len(kdf)

            # Orderbook
            ob_dir = os.path.join(self.data_dir, "orderbook", symbol)
            if os.path.exists(ob_dir):
                ob_files = [f for f in os.listdir(ob_dir) if f.endswith(".parquet")]
                if ob_files:
                    total_ob = sum(
                        len(pd.read_parquet(os.path.join(ob_dir, f)))
                        for f in ob_files
                    )
                    sym_info["ob_snapshots"] = total_ob

            info["symbols"][symbol] = sym_info

        info["metadata"] = self._metadata
        return info
