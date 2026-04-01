"""
Trade Database — Capa de almacenamiento persistente para trades ejecutados.

Proporciona:
  - TradeRecord: registro estructurado de cada trade con contexto (régimen, equity, sesión)
  - TradeRepository: interfaz de consulta sobre SQLite (por estrategia, símbolo, régimen, fecha)
  - TradeDBAdapter: adaptador que conecta con el sistema existente sin modificar interfaces
"""
from trade_database.models import TradeRecord, SessionRecord
from trade_database.repository import TradeRepository
from trade_database.adapter import TradeDBAdapter

__all__ = ["TradeRecord", "SessionRecord", "TradeRepository", "TradeDBAdapter"]
