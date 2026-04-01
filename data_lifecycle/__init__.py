"""
Data Lifecycle — Gestión del ciclo de vida de datos de mercado.

Proporciona:
  - StorageManager: compactación, agregación y optimización de archivos Parquet
  - DataCatalog: metadatos sobre datasets disponibles
  - RetentionPolicy: políticas configurables de retención y limpieza
"""
from data_lifecycle.storage_manager import StorageManager
from data_lifecycle.catalog import DataCatalog

__all__ = ["StorageManager", "DataCatalog"]
