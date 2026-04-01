"""
DataCatalog — Metadatos sobre datasets disponibles.

Mantiene un índice JSON con información sobre todos los datos
disponibles: símbolos, rangos de fechas, conteo de filas, tamaños.

Esto permite:
  - Saber qué datos hay disponibles sin escanear archivos
  - Validar rápidamente la integridad de los datos
  - Facilitar la selección de datos para backtesting
  - Mostrar información en el dashboard

El catálogo se regenera bajo demanda (no es un proceso continuo).
"""
from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class DatasetInfo:
    """Información sobre un dataset disponible."""
    symbol: str = ""
    data_type: str = ""       # trades, klines, orderbook, aggregated
    timeframe: str = ""       # 1m, 5m, 1h, etc. (para klines/aggregated)
    file_path: str = ""
    file_count: int = 0
    total_rows: int = 0
    total_bytes: int = 0
    date_start: str = ""
    date_end: str = ""
    last_updated: float = 0.0
    compression: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "data_type": self.data_type,
            "timeframe": self.timeframe,
            "file_path": self.file_path,
            "file_count": self.file_count,
            "total_rows": self.total_rows,
            "total_bytes": self.total_bytes,
            "size_mb": round(self.total_bytes / (1024 * 1024), 2),
            "date_start": self.date_start,
            "date_end": self.date_end,
            "last_updated": self.last_updated,
            "compression": self.compression,
        }


class DataCatalog:
    """Catálogo de datasets disponibles.

    Uso:
        catalog = DataCatalog("data")
        catalog.refresh()                    # escanea y actualiza catálogo
        datasets = catalog.list_datasets()   # lista todos
        info = catalog.get_dataset("BTC-USD", "trades")  # info específica
        catalog.save()                       # persiste a catalog.json
    """

    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir = Path(data_dir)
        self.catalog_path = self.data_dir / "catalog.json"
        self._datasets: Dict[str, DatasetInfo] = {}
        self._load()

    def _load(self) -> None:
        """Carga catálogo desde disco si existe."""
        if self.catalog_path.exists():
            try:
                with open(self.catalog_path) as f:
                    data = json.load(f)
                for key, info in data.get("datasets", {}).items():
                    # Filtrar campos desconocidos para evitar TypeError
                    valid_fields = {k: v for k, v in info.items()
                                    if k in DatasetInfo.__dataclass_fields__}
                    self._datasets[key] = DatasetInfo(**valid_fields)
            except Exception as e:
                logger.warning("catalog_load_error", error=str(e))

    def save(self) -> None:
        """Persiste catálogo a disco."""
        data = {
            "updated_at": time.time(),
            "updated_at_str": datetime.now().isoformat(),
            "total_datasets": len(self._datasets),
            "datasets": {k: v.to_dict() for k, v in self._datasets.items()},
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.catalog_path, "w") as f:
            json.dump(data, f, indent=2)

    def refresh(self) -> int:
        """Escanea el directorio de datos y actualiza el catálogo.

        Returns:
            Número de datasets encontrados
        """
        self._datasets.clear()

        # Escanear trades diarios
        self._scan_daily_files("trades")

        # Escanear orderbook diarios
        self._scan_daily_files("orderbook")

        # Escanear klines
        self._scan_klines()

        # Escanear compactados
        self._scan_compacted()

        # Escanear agregados
        self._scan_aggregated()

        self.save()
        logger.info("catalog_refreshed", datasets=len(self._datasets))
        return len(self._datasets)

    def list_datasets(
        self,
        symbol: Optional[str] = None,
        data_type: Optional[str] = None,
    ) -> List[DatasetInfo]:
        """Lista datasets con filtros opcionales."""
        results = []
        for info in self._datasets.values():
            if symbol and info.symbol != symbol:
                continue
            if data_type and info.data_type != data_type:
                continue
            results.append(info)
        return sorted(results, key=lambda x: (x.symbol, x.data_type, x.timeframe))

    def get_dataset(
        self,
        symbol: str,
        data_type: str,
        timeframe: str = "",
    ) -> Optional[DatasetInfo]:
        """Obtiene info de un dataset específico."""
        key = f"{symbol}/{data_type}"
        if timeframe:
            key += f"/{timeframe}"
        return self._datasets.get(key)

    def get_available_symbols(self) -> List[str]:
        """Retorna símbolos con datos disponibles."""
        return sorted(set(d.symbol for d in self._datasets.values()))

    def get_date_range(self, symbol: str) -> Optional[Dict[str, str]]:
        """Retorna rango de fechas disponible para un símbolo."""
        dates = []
        for d in self._datasets.values():
            if d.symbol == symbol and d.date_start:
                dates.append(d.date_start)
            if d.symbol == symbol and d.date_end:
                dates.append(d.date_end)
        if not dates:
            return None
        return {"start": min(dates), "end": max(dates)}

    def summary(self) -> Dict:
        """Resumen del catálogo."""
        symbols = self.get_available_symbols()
        total_rows = sum(d.total_rows for d in self._datasets.values())
        total_bytes = sum(d.total_bytes for d in self._datasets.values())

        return {
            "total_datasets": len(self._datasets),
            "symbols": symbols,
            "total_rows": total_rows,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "by_type": {
                dt: sum(
                    1 for d in self._datasets.values() if d.data_type == dt
                )
                for dt in set(d.data_type for d in self._datasets.values())
            },
        }

    # ── Escaneo interno ──────────────────────────────────────────────

    def _scan_daily_files(self, data_type: str) -> None:
        """Escanea archivos diarios (trades, orderbook)."""
        base_dir = self.data_dir / data_type
        if not base_dir.exists():
            return

        for symbol_dir in sorted(base_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue

            symbol = symbol_dir.name
            files = sorted(symbol_dir.glob("*.parquet"))
            if not files:
                continue

            total_rows = 0
            total_bytes = 0
            dates = []

            for f in files:
                total_bytes += f.stat().st_size
                try:
                    date_str = f.stem
                    dates.append(date_str)
                    # Contar filas sin cargar todo en memoria
                    pf = pd.read_parquet(f, columns=[])
                    total_rows += len(pf)
                except Exception:
                    pass

            key = f"{symbol}/{data_type}"
            self._datasets[key] = DatasetInfo(
                symbol=symbol,
                data_type=data_type,
                file_path=str(symbol_dir),
                file_count=len(files),
                total_rows=total_rows,
                total_bytes=total_bytes,
                date_start=min(dates) if dates else "",
                date_end=max(dates) if dates else "",
                last_updated=time.time(),
            )

    def _scan_klines(self) -> None:
        """Escanea klines incrementales."""
        klines_dir = self.data_dir / "klines"
        if not klines_dir.exists():
            return

        for symbol_dir in sorted(klines_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue

            symbol = symbol_dir.name
            for f in symbol_dir.glob("*.parquet"):
                tf = f.stem  # e.g., "1m"
                try:
                    df = pd.read_parquet(f, columns=["timestamp"])
                    rows = len(df)
                    if rows > 0 and "timestamp" in df.columns:
                        ts_min = df["timestamp"].min()
                        ts_max = df["timestamp"].max()
                        # Convertir a fecha
                        if ts_min > 1e12:
                            ts_min /= 1000
                            ts_max /= 1000
                        date_start = datetime.fromtimestamp(ts_min).strftime("%Y-%m-%d")
                        date_end = datetime.fromtimestamp(ts_max).strftime("%Y-%m-%d")
                    else:
                        rows = 0
                        date_start = date_end = ""
                except Exception:
                    rows = 0
                    date_start = date_end = ""

                key = f"{symbol}/klines/{tf}"
                self._datasets[key] = DatasetInfo(
                    symbol=symbol,
                    data_type="klines",
                    timeframe=tf,
                    file_path=str(f),
                    file_count=1,
                    total_rows=rows,
                    total_bytes=f.stat().st_size,
                    date_start=date_start,
                    date_end=date_end,
                    last_updated=time.time(),
                )

    def _scan_compacted(self) -> None:
        """Escanea archivos compactados."""
        compacted_dir = self.data_dir / "compacted"
        if not compacted_dir.exists():
            return

        for data_type_dir in sorted(compacted_dir.iterdir()):
            if not data_type_dir.is_dir():
                continue

            data_type = data_type_dir.name
            for symbol_dir in sorted(data_type_dir.iterdir()):
                if not symbol_dir.is_dir():
                    continue

                symbol = symbol_dir.name
                files = sorted(symbol_dir.glob("*.parquet"))
                if not files:
                    continue

                total_rows = 0
                total_bytes = 0
                for f in files:
                    total_bytes += f.stat().st_size
                    try:
                        pf = pd.read_parquet(f, columns=[])
                        total_rows += len(pf)
                    except Exception:
                        pass

                key = f"{symbol}/compacted_{data_type}"
                self._datasets[key] = DatasetInfo(
                    symbol=symbol,
                    data_type=f"compacted_{data_type}",
                    file_path=str(symbol_dir),
                    file_count=len(files),
                    total_rows=total_rows,
                    total_bytes=total_bytes,
                    date_start=files[0].stem if files else "",
                    date_end=files[-1].stem if files else "",
                    last_updated=time.time(),
                )

    def _scan_aggregated(self) -> None:
        """Escanea klines agregadas."""
        agg_dir = self.data_dir / "aggregated"
        if not agg_dir.exists():
            return

        for symbol_dir in sorted(agg_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue

            symbol = symbol_dir.name
            for f in symbol_dir.glob("*.parquet"):
                tf = f.stem
                try:
                    df = pd.read_parquet(f, columns=[])
                    rows = len(df)
                except Exception:
                    rows = 0

                key = f"{symbol}/aggregated/{tf}"
                self._datasets[key] = DatasetInfo(
                    symbol=symbol,
                    data_type="aggregated",
                    timeframe=tf,
                    file_path=str(f),
                    file_count=1,
                    total_rows=rows,
                    total_bytes=f.stat().st_size,
                    last_updated=time.time(),
                )
