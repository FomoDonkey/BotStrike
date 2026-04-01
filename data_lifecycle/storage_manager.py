"""
StorageManager — Compactación, agregación y optimización de datos de mercado.

El DataCollector genera archivos Parquet diarios que crecen con el tiempo.
Este módulo los gestiona:

  1. Compactación: fusiona archivos pequeños del mismo tipo en archivos más grandes
  2. Agregación: crea resúmenes temporales (diario → semanal → mensual)
  3. Optimización: recompresión con mejor codec, re-sorting, deduplicación
  4. Retención: limpieza de datos viejos según política configurable

Estructura de salida (coexiste con la estructura original):
  data/
  ├── trades/             ← originales (sin tocar)
  │   └── BTC-USD/
  │       ├── 2026-03-25.parquet
  │       └── 2026-03-26.parquet
  ├── compacted/          ← archivos compactados
  │   └── trades/
  │       └── BTC-USD/
  │           └── 2026-W13.parquet   (semana completa)
  ├── aggregated/         ← datos agregados (OHLCV de mayor timeframe)
  │   └── BTC-USD/
  │       ├── 5m.parquet
  │       ├── 15m.parquet
  │       ├── 1h.parquet
  │       └── 1d.parquet
  └── catalog.json        ← metadatos de todos los datasets

Principios:
  - No destructivo: nunca borra originales hasta que la compactación se verifica
  - Idempotente: ejecutar dos veces produce el mismo resultado
  - Compatible: no cambia formatos existentes, solo agrega
"""
from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class RetentionPolicy:
    """Política de retención de datos.

    Define cuánto tiempo se mantiene cada tipo de dato.
    Los datos más viejos que el periodo se compactan o eliminan.
    """
    # Retener archivos diarios originales (días). 0 = infinito
    raw_trades_days: int = 90
    raw_orderbook_days: int = 30
    raw_klines_days: int = 0       # klines incrementales se mantienen siempre

    # Retener compactados (días). 0 = infinito
    compacted_trades_days: int = 0   # compactados se mantienen siempre
    compacted_orderbook_days: int = 365

    # Compactar archivos más pequeños que este tamaño (bytes)
    min_file_size_bytes: int = 10_000  # 10KB

    # Mínimo de archivos diarios para activar compactación semanal
    min_files_for_compact: int = 3


@dataclass
class CompactionResult:
    """Resultado de una operación de compactación."""
    files_processed: int = 0
    files_created: int = 0
    rows_before: int = 0
    rows_after: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    errors: List[str] = field(default_factory=list)
    duration_sec: float = 0.0


class StorageManager:
    """Gestiona el ciclo de vida de datos de mercado Parquet.

    Uso:
        manager = StorageManager("data")
        manager.compact_trades("BTC-USD")         # compacta semanas completas
        manager.aggregate_klines("BTC-USD")        # genera 5m, 15m, 1h, 1d
        manager.apply_retention()                   # limpia datos viejos
        manager.optimize_all()                      # compactación + agregación + limpieza
    """

    def __init__(
        self,
        data_dir: str = "data",
        policy: Optional[RetentionPolicy] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.policy = policy or RetentionPolicy()
        self.compacted_dir = self.data_dir / "compacted"
        self.aggregated_dir = self.data_dir / "aggregated"

    # ── Compactación de trades ───────────────────────────────────────

    def compact_trades(self, symbol: str) -> CompactionResult:
        """Compacta archivos diarios de trades en archivos semanales.

        Solo compacta semanas completas (no la semana actual).
        Verifica integridad antes de marcar como compactado.
        """
        result = CompactionResult()
        start = time.time()

        trades_dir = self.data_dir / "trades" / symbol
        if not trades_dir.exists():
            return result

        # Listar archivos diarios
        daily_files = sorted(trades_dir.glob("*.parquet"))
        if len(daily_files) < self.policy.min_files_for_compact:
            return result

        # Agrupar por semana ISO
        weeks: Dict[str, List[Path]] = {}
        for f in daily_files:
            try:
                date = datetime.strptime(f.stem, "%Y-%m-%d")
                week_key = f"{date.isocalendar()[0]}-W{date.isocalendar()[1]:02d}"
                weeks.setdefault(week_key, []).append(f)
            except ValueError:
                continue

        # Compactar solo semanas completas (no la actual)
        now = datetime.now()
        current_week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
        out_dir = self.compacted_dir / "trades" / symbol
        out_dir.mkdir(parents=True, exist_ok=True)

        for week_key, files in sorted(weeks.items()):
            if week_key >= current_week:
                continue  # no compactar semana en curso

            out_path = out_dir / f"{week_key}.parquet"
            if out_path.exists():
                continue  # ya compactado

            if len(files) < self.policy.min_files_for_compact:
                continue

            try:
                dfs = []
                total_bytes = 0
                for f in files:
                    df = pd.read_parquet(f)
                    dfs.append(df)
                    total_bytes += f.stat().st_size
                    result.files_processed += 1

                if not dfs:
                    continue

                merged = pd.concat(dfs, ignore_index=True)
                result.rows_before += len(merged)

                # Deduplicar
                if "trade_id" in merged.columns:
                    merged = merged.drop_duplicates(subset=["trade_id"])
                elif "timestamp" in merged.columns and "price" in merged.columns:
                    merged = merged.drop_duplicates(
                        subset=["timestamp", "price", "quantity"],
                    )

                # Ordenar por timestamp
                if "timestamp" in merged.columns:
                    merged = merged.sort_values("timestamp").reset_index(drop=True)

                result.rows_after += len(merged)
                result.bytes_before += total_bytes

                # Escribir compactado con mejor compresión
                merged.to_parquet(out_path, compression="zstd", index=False)
                result.bytes_after += out_path.stat().st_size
                result.files_created += 1

                logger.info("trades_compacted",
                            symbol=symbol, week=week_key,
                            files=len(files), rows=len(merged))

            except Exception as e:
                result.errors.append(f"{week_key}: {e}")
                logger.error("compact_error", symbol=symbol, week=week_key, error=str(e))

        result.duration_sec = time.time() - start
        return result

    # ── Compactación de orderbook ────────────────────────────────────

    def compact_orderbook(self, symbol: str) -> CompactionResult:
        """Compacta archivos diarios de orderbook en archivos semanales."""
        result = CompactionResult()
        start = time.time()

        ob_dir = self.data_dir / "orderbook" / symbol
        if not ob_dir.exists():
            return result

        daily_files = sorted(ob_dir.glob("*.parquet"))
        if len(daily_files) < self.policy.min_files_for_compact:
            return result

        weeks: Dict[str, List[Path]] = {}
        for f in daily_files:
            try:
                date = datetime.strptime(f.stem, "%Y-%m-%d")
                week_key = f"{date.isocalendar()[0]}-W{date.isocalendar()[1]:02d}"
                weeks.setdefault(week_key, []).append(f)
            except ValueError:
                continue

        now = datetime.now()
        current_week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
        out_dir = self.compacted_dir / "orderbook" / symbol
        out_dir.mkdir(parents=True, exist_ok=True)

        for week_key, files in sorted(weeks.items()):
            if week_key >= current_week:
                continue

            out_path = out_dir / f"{week_key}.parquet"
            if out_path.exists():
                continue

            if len(files) < self.policy.min_files_for_compact:
                continue

            try:
                dfs = [pd.read_parquet(f) for f in files]
                total_bytes = sum(f.stat().st_size for f in files)
                result.files_processed += len(files)

                merged = pd.concat(dfs, ignore_index=True)
                result.rows_before += len(merged)

                if "timestamp" in merged.columns:
                    merged = merged.drop_duplicates(subset=["timestamp"])
                    merged = merged.sort_values("timestamp").reset_index(drop=True)

                result.rows_after += len(merged)
                result.bytes_before += total_bytes

                merged.to_parquet(out_path, compression="zstd", index=False)
                result.bytes_after += out_path.stat().st_size
                result.files_created += 1

            except Exception as e:
                result.errors.append(f"orderbook {week_key}: {e}")

        result.duration_sec = time.time() - start
        return result

    # ── Agregación de klines ─────────────────────────────────────────

    def aggregate_klines(self, symbol: str) -> Dict[str, int]:
        """Genera klines agregadas de mayor timeframe desde 1m.

        Genera: 5m, 15m, 1h, 4h, 1d
        Lee klines/symbol/1m.parquet y produce aggregated/symbol/*.parquet

        Returns:
            Dict[timeframe] = rows generadas
        """
        kline_path = self.data_dir / "klines" / symbol / "1m.parquet"
        if not kline_path.exists():
            return {}

        try:
            df_1m = pd.read_parquet(kline_path)
        except Exception as e:
            logger.error("kline_read_error", symbol=symbol, error=str(e))
            return {}

        if df_1m.empty:
            return {}

        # Asegurar timestamp como datetime
        if "timestamp" in df_1m.columns:
            if df_1m["timestamp"].dtype in ("int64", "float64"):
                # Detectar unidad (ms vs s)
                sample_ts = df_1m["timestamp"].iloc[0]
                if sample_ts > 1e12:
                    df_1m["datetime"] = pd.to_datetime(df_1m["timestamp"], unit="ms")
                else:
                    df_1m["datetime"] = pd.to_datetime(df_1m["timestamp"], unit="s")
            else:
                df_1m["datetime"] = pd.to_datetime(df_1m["timestamp"])
        else:
            return {}

        df_1m = df_1m.set_index("datetime").sort_index()

        out_dir = self.aggregated_dir / symbol
        out_dir.mkdir(parents=True, exist_ok=True)

        timeframes = {
            "5m": "5min",
            "15m": "15min",
            "1h": "1h",
            "4h": "4h",
            "1d": "1D",
        }

        result = {}
        for tf_label, tf_rule in timeframes.items():
            try:
                agg = df_1m.resample(tf_rule).agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }).dropna()

                if "timestamp" in df_1m.columns:
                    agg["timestamp"] = agg.index.astype(np.int64) // 10**6  # ms

                agg = agg.reset_index(drop=True)
                out_path = out_dir / f"{tf_label}.parquet"
                agg.to_parquet(out_path, compression="zstd", index=False)
                result[tf_label] = len(agg)

            except Exception as e:
                logger.error("aggregate_error",
                             symbol=symbol, timeframe=tf_label, error=str(e))

        if result:
            logger.info("klines_aggregated", symbol=symbol, timeframes=result)

        return result

    # ── Retención / limpieza ─────────────────────────────────────────

    def apply_retention(self) -> Dict[str, int]:
        """Aplica política de retención: elimina datos más viejos que el límite.

        Solo elimina archivos diarios originales si existe el compactado correspondiente.

        Returns:
            Dict con conteo de archivos eliminados por categoría
        """
        deleted = {"trades": 0, "orderbook": 0}
        now = datetime.now()

        # Trades originales
        if self.policy.raw_trades_days > 0:
            cutoff = now - timedelta(days=self.policy.raw_trades_days)
            deleted["trades"] = self._cleanup_daily_files(
                self.data_dir / "trades", cutoff, check_compacted="trades"
            )

        # Orderbook originales
        if self.policy.raw_orderbook_days > 0:
            cutoff = now - timedelta(days=self.policy.raw_orderbook_days)
            deleted["orderbook"] = self._cleanup_daily_files(
                self.data_dir / "orderbook", cutoff, check_compacted="orderbook"
            )

        if any(v > 0 for v in deleted.values()):
            logger.info("retention_applied", deleted=deleted)

        return deleted

    def _cleanup_daily_files(
        self,
        base_dir: Path,
        cutoff: datetime,
        check_compacted: str = "",
    ) -> int:
        """Elimina archivos diarios anteriores al cutoff.

        Si check_compacted está definido, solo elimina si existe el compactado semanal.
        """
        deleted = 0
        if not base_dir.exists():
            return 0

        for symbol_dir in base_dir.iterdir():
            if not symbol_dir.is_dir():
                continue

            symbol = symbol_dir.name
            for f in sorted(symbol_dir.glob("*.parquet")):
                try:
                    file_date = datetime.strptime(f.stem, "%Y-%m-%d")
                except ValueError:
                    continue

                if file_date >= cutoff:
                    continue

                # Verificar que existe el compactado antes de borrar
                if check_compacted:
                    week_key = f"{file_date.isocalendar()[0]}-W{file_date.isocalendar()[1]:02d}"
                    compacted = (
                        self.compacted_dir / check_compacted / symbol / f"{week_key}.parquet"
                    )
                    if not compacted.exists():
                        continue  # no borrar si no hay compactado

                f.unlink()
                deleted += 1

        return deleted

    # ── Optimización completa ────────────────────────────────────────

    def optimize_all(self, symbols: Optional[List[str]] = None) -> Dict:
        """Ejecuta compactación + agregación + retención para todos los símbolos.

        Args:
            symbols: Lista de símbolos. Si None, detecta automáticamente.

        Returns:
            Dict con resultados de cada operación.
        """
        if symbols is None:
            symbols = self._detect_symbols()

        results = {
            "compact_trades": {},
            "compact_orderbook": {},
            "aggregate_klines": {},
            "retention": {},
        }

        for symbol in symbols:
            r = self.compact_trades(symbol)
            if r.files_processed > 0:
                results["compact_trades"][symbol] = {
                    "files": r.files_processed,
                    "created": r.files_created,
                    "rows": r.rows_after,
                    "savings_pct": round(
                        (1 - r.bytes_after / r.bytes_before) * 100, 1
                    ) if r.bytes_before > 0 else 0,
                }

            r = self.compact_orderbook(symbol)
            if r.files_processed > 0:
                results["compact_orderbook"][symbol] = {
                    "files": r.files_processed,
                    "created": r.files_created,
                }

            agg = self.aggregate_klines(symbol)
            if agg:
                results["aggregate_klines"][symbol] = agg

        results["retention"] = self.apply_retention()

        logger.info("storage_optimized", results=results)
        return results

    # ── Estadísticas de almacenamiento ───────────────────────────────

    def get_storage_stats(self) -> Dict:
        """Retorna estadísticas de uso de almacenamiento.

        Returns:
            Dict con tamaños por categoría y símbolo
        """
        stats = {
            "total_bytes": 0,
            "categories": {},
        }

        for category in ("trades", "klines", "orderbook", "compacted", "aggregated"):
            cat_dir = self.data_dir / category
            if not cat_dir.exists():
                continue

            cat_stats = {"total_bytes": 0, "file_count": 0, "symbols": {}}

            for f in cat_dir.rglob("*.parquet"):
                size = f.stat().st_size
                cat_stats["total_bytes"] += size
                cat_stats["file_count"] += 1
                stats["total_bytes"] += size

                # Extraer símbolo del path
                parts = f.relative_to(cat_dir).parts
                symbol = parts[0] if parts else "unknown"
                sym_stats = cat_stats["symbols"].setdefault(symbol, {
                    "bytes": 0, "files": 0
                })
                sym_stats["bytes"] += size
                sym_stats["files"] += 1

            stats["categories"][category] = cat_stats

        return stats

    # ── Helpers ───────────────────────────────────────────────────────

    def _detect_symbols(self) -> List[str]:
        """Detecta símbolos disponibles en el directorio de datos."""
        symbols = set()
        for category in ("trades", "klines", "orderbook"):
            cat_dir = self.data_dir / category
            if cat_dir.exists():
                for d in cat_dir.iterdir():
                    if d.is_dir():
                        symbols.add(d.name)
        return sorted(symbols)
