"""
Módulo de carga y procesamiento de datos históricos de Strike Finance.

Soporta:
  - CSV/Parquet de trades tick-by-tick exportados de la API de Strike
  - CSV OHLCV estándar (columnas: timestamp,open,high,low,close,volume)
  - Generación de datos sintéticos realistas con micro-regímenes

Flujo:
  1. HistoricalDataLoader.load() → carga y normaliza desde cualquier formato
  2. .get_trades() → trades crudos tick-by-tick (para microestructura)
  3. .get_ohlcv()  → barras OHLCV agregadas (para indicadores y estrategias)
  4. .get_bars_with_trades() → iterador que emite (barra, trades_de_esa_barra)
     para alimentar el backtester realista tick-a-tick
"""
from __future__ import annotations
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ── Formatos soportados de columnas ────────────────────────────────

# Strike Finance trade export (REST /v2/trades o WebSocket)
STRIKE_TRADE_COLUMNS = {
    "time": "timestamp_ms",     # milisegundos unix
    "T": "timestamp_ms",
    "price": "price",
    "p": "price",
    "qty": "quantity",
    "q": "quantity",
    "symbol": "symbol",
    "s": "symbol",
    "side": "side",             # BUY/SELL (opcional)
    "m": "is_maker",            # booleano maker (opcional)
}

# OHLCV estándar
OHLCV_REQUIRED = {"open", "high", "low", "close", "volume"}


class HistoricalDataLoader:
    """Carga y procesa datos históricos de Strike Finance para backtesting.

    Uso típico:
        loader = HistoricalDataLoader()
        loader.load("data/btc_trades.csv", data_type="trades", symbol="BTC-USD")
        ohlcv = loader.get_ohlcv("BTC-USD", interval="1min")
        for bar, ticks in loader.get_bars_with_trades("BTC-USD", interval="1min"):
            # bar = dict con open,high,low,close,volume,timestamp
            # ticks = list de dicts con price,quantity,timestamp
            ...
    """

    def __init__(self) -> None:
        # Almacena trades crudos por símbolo
        self._trades: Dict[str, pd.DataFrame] = {}
        # Cache de OHLCV por símbolo+intervalo
        self._ohlcv_cache: Dict[str, pd.DataFrame] = {}
        # Orderbook snapshots por símbolo
        self._orderbook: Dict[str, pd.DataFrame] = {}

    # ── Carga de datos ─────────────────────────────────────────────

    def load(
        self,
        path: str,
        data_type: str = "auto",
        symbol: Optional[str] = None,
        timestamp_unit: str = "auto",
    ) -> str:
        """Carga datos desde CSV o Parquet.

        Args:
            path: Ruta al archivo CSV o Parquet
            data_type: "trades", "ohlcv", o "auto" (detecta automáticamente)
            symbol: Símbolo a asignar si no está en los datos
            timestamp_unit: "ms", "s", "us" o "auto"

        Returns:
            Símbolo cargado (str)
        """
        # Leer archivo
        if path.endswith(".parquet") or path.endswith(".pq"):
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path)

        if df.empty:
            raise ValueError(f"Archivo vacío: {path}")

        # Auto-detectar tipo
        if data_type == "auto":
            cols_lower = {c.lower() for c in df.columns}
            if OHLCV_REQUIRED.issubset(cols_lower):
                data_type = "ohlcv"
            else:
                data_type = "trades"

        # Normalizar columnas
        df.columns = [c.strip() for c in df.columns]

        if data_type == "trades":
            df = self._normalize_trades(df, symbol, timestamp_unit)
            sym = df["symbol"].iloc[0] if "symbol" in df.columns else (symbol or "UNKNOWN")
            self._trades[sym] = df
            self._ohlcv_cache.pop(sym, None)  # invalidar cache
            return sym

        elif data_type == "ohlcv":
            df = self._normalize_ohlcv(df, symbol, timestamp_unit)
            sym = symbol or "UNKNOWN"
            # Convertir OHLCV a trades sintéticos para microestructura
            trades = self._ohlcv_to_synthetic_trades(df)
            trades["symbol"] = sym
            self._trades[sym] = trades
            self._ohlcv_cache[sym] = df
            return sym

        raise ValueError(f"data_type debe ser 'trades', 'ohlcv' o 'auto', no '{data_type}'")

    def _normalize_trades(
        self, df: pd.DataFrame, symbol: Optional[str], ts_unit: str
    ) -> pd.DataFrame:
        """Normaliza trades a formato interno: timestamp(s), price, quantity, symbol."""
        # Renombrar columnas conocidas
        rename = {}
        for col in df.columns:
            key = col.strip().lower()
            if key in ("time", "t", "timestamp", "timestamp_ms", "ts"):
                rename[col] = "timestamp_raw"
            elif key in ("price", "p"):
                rename[col] = "price"
            elif key in ("qty", "q", "quantity", "amount", "size", "vol"):
                rename[col] = "quantity"
            elif key in ("symbol", "s", "pair", "market"):
                rename[col] = "symbol"
            elif key in ("side",):
                rename[col] = "side"

        df = df.rename(columns=rename)

        # Validar columnas requeridas
        if "price" not in df.columns:
            raise ValueError("Falta columna de precio (price, p)")
        if "quantity" not in df.columns:
            raise ValueError("Falta columna de cantidad (qty, q, quantity, amount)")

        # Timestamp
        if "timestamp_raw" in df.columns:
            ts = pd.to_numeric(df["timestamp_raw"], errors="coerce")
            if ts_unit == "auto":
                # Si los valores son > 1e12, son milisegundos
                median_ts = ts.median()
                if median_ts > 1e15:
                    ts = ts / 1e6  # microsegundos
                elif median_ts > 1e12:
                    ts = ts / 1e3  # milisegundos
                # Si < 1e12, ya son segundos
            elif ts_unit == "ms":
                ts = ts / 1e3
            elif ts_unit == "us":
                ts = ts / 1e6
            df["timestamp"] = ts
        else:
            # Generar timestamps incrementales
            df["timestamp"] = np.arange(len(df), dtype=float)

        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").abs()

        if symbol and "symbol" not in df.columns:
            df["symbol"] = symbol

        # Limpiar NaN
        df = df.dropna(subset=["price", "quantity", "timestamp"])
        df = df[df["price"] > 0]
        df = df[df["quantity"] > 0]
        df = df.sort_values("timestamp").reset_index(drop=True)

        return df[["timestamp", "price", "quantity"] +
                  ([c for c in ["symbol", "side"] if c in df.columns])]

    def _normalize_ohlcv(
        self, df: pd.DataFrame, symbol: Optional[str], ts_unit: str
    ) -> pd.DataFrame:
        """Normaliza OHLCV a formato estándar."""
        rename = {}
        for col in df.columns:
            key = col.strip().lower()
            if key in ("time", "t", "timestamp", "date", "datetime", "ts"):
                rename[col] = "timestamp_raw"
            elif key == "open":
                rename[col] = "open"
            elif key == "high":
                rename[col] = "high"
            elif key == "low":
                rename[col] = "low"
            elif key == "close":
                rename[col] = "close"
            elif key in ("volume", "vol"):
                rename[col] = "volume"

        df = df.rename(columns=rename)

        for required in ["open", "high", "low", "close"]:
            if required not in df.columns:
                raise ValueError(f"Falta columna requerida: {required}")

        if "volume" not in df.columns:
            df["volume"] = 0.0

        # Timestamp
        if "timestamp_raw" in df.columns:
            raw = df["timestamp_raw"]
            if pd.api.types.is_numeric_dtype(raw):
                ts = pd.to_numeric(raw, errors="coerce")
                if ts_unit == "auto":
                    median_ts = ts.median()
                    if median_ts > 1e12:
                        ts = ts / 1e3
                df["timestamp"] = ts
            else:
                df["timestamp"] = pd.to_datetime(raw).astype(np.int64) // 10**9
        else:
            df["timestamp"] = np.arange(len(df), dtype=float) * 60

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        return df[["timestamp", "open", "high", "low", "close", "volume"]]

    def _ohlcv_to_synthetic_trades(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Genera trades sintéticos a partir de OHLCV para alimentar microestructura.

        Para cada barra, genera ~10 trades interpolados entre OHLC.
        """
        records = []
        for bar in ohlcv.itertuples():
            ts = float(bar.timestamp)
            o, h, l, c = float(bar.open), float(bar.high), float(bar.low), float(bar.close)
            vol = float(bar.volume) if bar.volume > 0 else 1.0

            # Secuencia: open → high → low → close (simplificada)
            path = [o, (o + h) / 2, h, (h + l) / 2, l, (l + c) / 2, c]
            n = len(path)
            qty_each = vol / n
            for i, price in enumerate(path):
                records.append({
                    "timestamp": ts + i * (60.0 / n),
                    "price": price,
                    "quantity": qty_each,
                })

        return pd.DataFrame(records)

    # ── Acceso a datos ─────────────────────────────────────────────

    def get_trades(self, symbol: str) -> pd.DataFrame:
        """Retorna trades crudos para un símbolo. Columnas: timestamp, price, quantity."""
        return self._trades.get(symbol, pd.DataFrame())

    def get_ohlcv(self, symbol: str, interval: str = "1min") -> pd.DataFrame:
        """Retorna barras OHLCV para un símbolo.

        Args:
            symbol: Símbolo a consultar
            interval: Intervalo de barras ("1min", "5min", "15min", "1h")
        """
        cache_key = f"{symbol}_{interval}"
        if cache_key in self._ohlcv_cache:
            return self._ohlcv_cache[cache_key].copy()

        trades = self._trades.get(symbol)
        if trades is None or trades.empty:
            return pd.DataFrame()

        df = self._aggregate_trades_to_ohlcv(trades, interval)
        self._ohlcv_cache[cache_key] = df
        return df.copy()

    def _aggregate_trades_to_ohlcv(
        self, trades: pd.DataFrame, interval: str
    ) -> pd.DataFrame:
        """Agrega trades en barras OHLCV."""
        df = trades[["timestamp", "price", "quantity"]].copy()
        df["dt"] = pd.to_datetime(df["timestamp"], unit="s")
        df = df.set_index("dt")

        # Single resample pass con agg dict (evita doble agrupación)
        ohlcv = df.resample(interval).agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("quantity", "sum"),
        )
        ohlcv["timestamp"] = ohlcv.index.astype(np.int64) // 10**9
        ohlcv = ohlcv.dropna(subset=["open"]).reset_index(drop=True)
        return ohlcv

    def get_bars_with_trades(
        self, symbol: str, interval: str = "1min"
    ) -> List[Tuple[dict, List[dict]]]:
        """Retorna lista de (barra_dict, trades_de_esa_barra).

        Esto permite al backtester realista:
        1. Procesar trades tick-a-tick para microestructura
        2. Luego usar la barra OHLCV para indicadores y estrategias

        Cada barra_dict tiene: timestamp, open, high, low, close, volume
        Cada trade_dict tiene: timestamp, price, quantity
        """
        trades = self._trades.get(symbol)
        if trades is None or trades.empty:
            return []

        ohlcv = self.get_ohlcv(symbol, interval)
        if ohlcv.empty:
            return []

        # Mapear intervalo a segundos
        interval_map = {"1min": 60, "5min": 300, "15min": 900, "1h": 3600}
        interval_sec = interval_map.get(interval, 60)

        result = []
        trades_arr = trades.to_dict("records")
        trade_idx = 0

        for bar in ohlcv.itertuples():
            bar_start = float(bar.timestamp)
            bar_end = bar_start + interval_sec

            bar_dict = {
                "timestamp": bar_start,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            }

            # Recoger trades que caen en esta barra
            bar_trades = []
            while trade_idx < len(trades_arr):
                t = trades_arr[trade_idx]
                if t["timestamp"] >= bar_end:
                    break
                if t["timestamp"] >= bar_start:
                    bar_trades.append(t)
                trade_idx += 1

            result.append((bar_dict, bar_trades))

        return result

    # ── Información ────────────────────────────────────────────────

    def get_symbols(self) -> List[str]:
        return list(self._trades.keys())

    def get_info(self, symbol: str) -> Dict:
        """Info resumida del dataset cargado."""
        trades = self._trades.get(symbol)
        if trades is None or trades.empty:
            return {"symbol": symbol, "trades": 0}

        return {
            "symbol": symbol,
            "trades": len(trades),
            "start": pd.to_datetime(trades["timestamp"].min(), unit="s").isoformat(),
            "end": pd.to_datetime(trades["timestamp"].max(), unit="s").isoformat(),
            "duration_hours": (trades["timestamp"].max() - trades["timestamp"].min()) / 3600,
            "price_min": float(trades["price"].min()),
            "price_max": float(trades["price"].max()),
            "price_last": float(trades["price"].iloc[-1]),
            "volume_total": float(trades["quantity"].sum()),
        }

    # ── Generación de datos sintéticos mejorados ───────────────────

    @staticmethod
    def generate_realistic_trades(
        symbol: str = "BTC-USD",
        hours: float = 24.0,
        start_price: float = 50000.0,
        avg_trades_per_min: int = 30,
        volatility: float = 0.0003,
    ) -> pd.DataFrame:
        """Genera trades tick-by-tick sintéticos realistas.

        Más realista que generate_sample_data: simula llegada de trades
        con tiempos inter-evento exponenciales y clustering de volatilidad.

        Args:
            symbol: Símbolo
            hours: Duración en horas
            start_price: Precio inicial
            avg_trades_per_min: Trades promedio por minuto
            volatility: Volatilidad por trade (std de retornos)
        """
        rng = np.random.default_rng(42)
        total_trades = int(hours * 60 * avg_trades_per_min)
        total_seconds = hours * 3600

        # Tiempos inter-evento exponenciales (proceso de Poisson)
        avg_interval = total_seconds / total_trades
        intervals = rng.exponential(avg_interval, total_trades)

        # Clustering: en algunos períodos hay más actividad
        cluster_factor = np.sin(np.linspace(0, 20 * np.pi, total_trades)) * 0.5 + 1.0
        intervals = intervals / cluster_factor

        timestamps = np.cumsum(intervals)
        timestamps = timestamps * (total_seconds / timestamps[-1])  # normalizar

        # Precios con GARCH-like volatility clustering
        prices = [start_price]
        current_vol = volatility
        for i in range(1, total_trades):
            # Volatilidad cambia lentamente
            current_vol = 0.99 * current_vol + 0.01 * volatility * (1 + rng.exponential(0.5))
            current_vol = np.clip(current_vol, volatility * 0.3, volatility * 5.0)

            # Micro-drift basado en régimen (cambia cada ~2 horas)
            phase = (timestamps[i] / 3600) % 6
            if phase < 2:
                drift = 0.00001   # trending up
            elif phase < 4:
                drift = 0         # ranging
            else:
                drift = -0.00001  # trending down

            ret = drift + current_vol * rng.standard_normal()
            prices.append(prices[-1] * (1 + ret))

        # Cantidades: log-normal distribution (muchos trades pequeños, pocos grandes)
        quantities = rng.lognormal(mean=-4, sigma=1.5, size=total_trades)
        quantities = np.clip(quantities, 0.0001, 10.0)

        df = pd.DataFrame({
            "timestamp": timestamps,
            "price": prices,
            "quantity": quantities,
            "symbol": symbol,
        })

        return df

    # ── Carga desde StrikeDataCollector ────────────────────────────

    def load_from_collector(
        self, data_dir: str = "data", symbol: Optional[str] = None, days: int = 7
    ) -> List[str]:
        """Carga todos los datos recolectados por StrikeDataCollector.

        Lee trades, orderbook y klines almacenados en Parquet.
        Retorna lista de simbolos cargados.

        Args:
            data_dir: Directorio raiz de datos (default: "data")
            symbol: Simbolo especifico, o None para todos los disponibles
            days: Dias de datos a cargar
        """
        trades_root = os.path.join(data_dir, "trades")
        if not os.path.exists(trades_root):
            return []

        if symbol:
            symbols_to_load = [symbol]
        else:
            symbols_to_load = [
                d for d in os.listdir(trades_root)
                if os.path.isdir(os.path.join(trades_root, d))
            ]

        loaded = []
        for sym in symbols_to_load:
            # 1. Cargar trades
            sym_dir = os.path.join(trades_root, sym)
            if not os.path.isdir(sym_dir):
                continue

            files = sorted(
                [f for f in os.listdir(sym_dir) if f.endswith(".parquet")],
                reverse=True,
            )[:days]

            if not files:
                continue

            dfs = []
            for f in files:
                try:
                    dfs.append(pd.read_parquet(os.path.join(sym_dir, f)))
                except Exception:
                    continue

            if not dfs:
                continue

            trades_df = pd.concat(dfs, ignore_index=True)

            # Normalizar timestamps (collector guarda en ms)
            if trades_df["timestamp"].median() > 1e12:
                trades_df["timestamp"] = trades_df["timestamp"] / 1000.0

            trades_df = trades_df.sort_values("timestamp").reset_index(drop=True)

            if "price" not in trades_df.columns or "quantity" not in trades_df.columns:
                continue

            trades_df["symbol"] = sym
            self._trades[sym] = trades_df
            self._ohlcv_cache.pop(sym, None)

            # 2. Cargar orderbook snapshots
            ob_dir = os.path.join(data_dir, "orderbook", sym)
            if os.path.isdir(ob_dir):
                ob_files = sorted(
                    [f for f in os.listdir(ob_dir) if f.endswith(".parquet")],
                    reverse=True,
                )[:days]
                ob_dfs = []
                for f in ob_files:
                    try:
                        ob_dfs.append(pd.read_parquet(os.path.join(ob_dir, f)))
                    except Exception:
                        continue
                if ob_dfs:
                    ob_df = pd.concat(ob_dfs, ignore_index=True)
                    if ob_df["timestamp"].median() > 1e12:
                        ob_df["timestamp"] = ob_df["timestamp"] / 1000.0
                    ob_df = ob_df.sort_values("timestamp").reset_index(drop=True)
                    self._orderbook[sym] = ob_df

            # 3. Cargar klines (si no hay trades suficientes, las klines sirven de fallback)
            kline_path = os.path.join(data_dir, "klines", sym, "1m.parquet")
            if os.path.exists(kline_path):
                try:
                    kdf = pd.read_parquet(kline_path)
                    if kdf["timestamp"].median() > 1e12:
                        kdf["timestamp"] = kdf["timestamp"] / 1000.0
                    kdf = kdf.sort_values("timestamp").reset_index(drop=True)
                    # Cachear como OHLCV directamente
                    cache_key = f"{sym}_1min"
                    self._ohlcv_cache[cache_key] = kdf
                except Exception:
                    pass

            loaded.append(sym)

        return loaded

    def get_orderbook(self, symbol: str) -> pd.DataFrame:
        """Retorna snapshots de orderbook para un simbolo."""
        return self._orderbook.get(symbol, pd.DataFrame())
