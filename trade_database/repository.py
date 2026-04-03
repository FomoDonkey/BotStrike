"""
TradeRepository — Capa de acceso a datos sobre SQLite.

Proporciona una interfaz limpia para:
  - Insertar trades y sesiones
  - Consultar trades por estrategia, símbolo, régimen, fecha
  - Reconstruir equity curve
  - Obtener estadísticas agregadas

SQLite se usa porque:
  - No requiere servidor externo
  - Archivo único, fácil de respaldar
  - Buen rendimiento para este volumen de datos (~miles de trades)
  - Soporte nativo de consultas SQL para análisis ad-hoc
"""
from __future__ import annotations
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

from trade_database.models import TradeRecord, SessionRecord
import structlog

logger = structlog.get_logger(__name__)

# Schema version para migraciones futuras
SCHEMA_VERSION = 1

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'live',
    symbol TEXT NOT NULL DEFAULT '',
    start_time REAL NOT NULL,
    end_time REAL DEFAULT 0,
    initial_equity REAL DEFAULT 0,
    final_equity REAL DEFAULT 0,
    total_trades INTEGER DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    strategies_used TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'live',
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    fee REAL DEFAULT 0,
    fee_asset TEXT DEFAULT 'USD',
    pnl REAL DEFAULT 0,
    order_id TEXT DEFAULT '',
    strategy TEXT DEFAULT '',
    regime TEXT DEFAULT '',
    trade_type TEXT DEFAULT '',
    equity_before REAL DEFAULT 0,
    equity_after REAL DEFAULT 0,
    entry_price REAL DEFAULT 0,
    exit_price REAL DEFAULT 0,
    duration_sec REAL DEFAULT 0,
    micro_vpin REAL DEFAULT 0,
    micro_risk_score REAL DEFAULT 0,
    timestamp REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_regime ON trades(regime);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_source ON trades(source);
"""


class TradeRepository:
    """Repositorio de trades sobre SQLite.

    Thread-safe para lecturas concurrentes. Escrituras se serializan
    automáticamente por SQLite.

    Uso:
        repo = TradeRepository("data/trades.db")
        repo.insert_trade(trade_record)
        trades = repo.get_trades(strategy="MEAN_REVERSION", regime="RANGING")
        equity = repo.get_equity_curve(session_id="abc123")
    """

    def __init__(self, db_path: str = "data/trade_database.db") -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Crea tablas si no existen."""
        with self._connect() as conn:
            conn.executescript(CREATE_TABLES_SQL)
            # Check/set schema version
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            conn.commit()

    @contextmanager
    def _connect(self):
        """Context manager para conexiones SQLite."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # mejor concurrencia
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    # ── Escritura ────────────────────────────────────────────────────

    def insert_trade(self, trade: TradeRecord) -> None:
        """Inserta un trade en la base de datos."""
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trades (
                    trade_id, session_id, source, symbol, side, price, quantity,
                    fee, fee_asset, pnl, order_id, strategy, regime, trade_type,
                    equity_before, equity_after, entry_price, exit_price,
                    duration_sec, micro_vpin, micro_risk_score, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id, trade.session_id, trade.source,
                trade.symbol, trade.side, trade.price, trade.quantity,
                trade.fee, trade.fee_asset, trade.pnl, trade.order_id,
                trade.strategy, trade.regime, trade.trade_type,
                trade.equity_before, trade.equity_after,
                trade.entry_price, trade.exit_price,
                trade.duration_sec, trade.micro_vpin, trade.micro_risk_score,
                trade.timestamp,
            ))
            conn.commit()

    def insert_trades_batch(self, trades: List[TradeRecord]) -> None:
        """Inserta múltiples trades en una transacción."""
        if not trades:
            return
        with self._connect() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO trades (
                    trade_id, session_id, source, symbol, side, price, quantity,
                    fee, fee_asset, pnl, order_id, strategy, regime, trade_type,
                    equity_before, equity_after, entry_price, exit_price,
                    duration_sec, micro_vpin, micro_risk_score, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    t.trade_id, t.session_id, t.source,
                    t.symbol, t.side, t.price, t.quantity,
                    t.fee, t.fee_asset, t.pnl, t.order_id,
                    t.strategy, t.regime, t.trade_type,
                    t.equity_before, t.equity_after,
                    t.entry_price, t.exit_price,
                    t.duration_sec, t.micro_vpin, t.micro_risk_score,
                    t.timestamp,
                )
                for t in trades
            ])
            conn.commit()

    def insert_session(self, session: SessionRecord) -> None:
        """Inserta o actualiza una sesión."""
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sessions (
                    session_id, source, symbol, start_time, end_time,
                    initial_equity, final_equity, total_trades, total_pnl,
                    max_drawdown, strategies_used, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session.session_id, session.source, session.symbol,
                session.start_time, session.end_time,
                session.initial_equity, session.final_equity,
                session.total_trades, session.total_pnl,
                session.max_drawdown, session.strategies_used, session.notes,
            ))
            conn.commit()

    # ── Consultas de trades ──────────────────────────────────────────

    def get_trades(
        self,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        regime: Optional[str] = None,
        trade_type: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 0,
    ) -> List[TradeRecord]:
        """Consulta trades con filtros opcionales.

        Todos los filtros son AND. Retorna ordenado por timestamp ASC.
        """
        conditions = []
        params = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if strategy:
            conditions.append("strategy = ?")
            params.append(strategy)
        if regime:
            conditions.append("regime = ?")
            params.append(regime)
        if trade_type:
            conditions.append("trade_type = ?")
            params.append(trade_type)
        if start_time is not None:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM trades WHERE {where} ORDER BY timestamp ASC"
        if limit > 0:
            sql += f" LIMIT {limit}"

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_trade(r) for r in rows]

    def get_trades_dataframe(self, **kwargs) -> "pd.DataFrame":
        """Retorna trades como DataFrame de pandas (import lazy)."""
        import pandas as pd
        trades = self.get_trades(**kwargs)
        if not trades:
            return pd.DataFrame()
        return pd.DataFrame([t.to_dict() for t in trades])

    # ── Equity curve ─────────────────────────────────────────────────

    def get_equity_curve(
        self,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Tuple[float, float]]:
        """Reconstruye equity curve: lista de (timestamp, equity_after).

        Si no se especifica filtro, retorna toda la historia.
        """
        conditions = ["equity_after IS NOT NULL"]
        params = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if source:
            conditions.append("source = ?")
            params.append(source)

        where = " AND ".join(conditions)
        sql = f"""
            SELECT timestamp, equity_after
            FROM trades WHERE {where}
            ORDER BY timestamp ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [(float(r["timestamp"]), float(r["equity_after"])) for r in rows]

    # ── Estadísticas agregadas ───────────────────────────────────────

    def get_pnl_by_strategy(
        self,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Dict]:
        """PnL agregado por estrategia."""
        return self._aggregate_by("strategy", session_id, source)

    def get_pnl_by_symbol(
        self,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Dict]:
        """PnL agregado por símbolo."""
        return self._aggregate_by("symbol", session_id, source)

    def get_pnl_by_regime(
        self,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Dict]:
        """PnL agregado por régimen de mercado."""
        return self._aggregate_by("regime", session_id, source)

    _VALID_GROUP_COLS = {"strategy", "symbol", "regime", "trade_type", "source"}

    def _aggregate_by(
        self,
        group_col: str,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Dict]:
        """Agrega métricas por una columna dada."""
        if group_col not in self._VALID_GROUP_COLS:
            raise ValueError(f"Invalid group column: {group_col}")
        conditions = [f"{group_col} != ''"]
        params = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if source:
            conditions.append("source = ?")
            params.append(source)

        where = " AND ".join(conditions)
        sql = f"""
            SELECT
                {group_col} as group_key,
                COUNT(*) as total_trades,
                SUM(pnl) as total_pnl,
                AVG(pnl) as avg_pnl,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) as gross_loss,
                SUM(fee) as total_fees,
                MAX(pnl) as best_trade,
                MIN(pnl) as worst_trade
            FROM trades
            WHERE {where}
            GROUP BY {group_col}
            ORDER BY total_pnl DESC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = {}
            for r in rows:
                key = r["group_key"]
                total = r["total_trades"]
                wins = r["wins"]
                gross_loss = r["gross_loss"]
                result[key] = {
                    "total_trades": total,
                    "total_pnl": round(r["total_pnl"], 2),
                    "avg_pnl": round(r["avg_pnl"], 4),
                    "win_rate": round(wins / total, 4) if total > 0 else 0,
                    "wins": wins,
                    "losses": r["losses"],
                    "gross_profit": round(r["gross_profit"], 2),
                    "gross_loss": round(gross_loss, 2),
                    "profit_factor": round(
                        abs(r["gross_profit"] / gross_loss), 2
                    ) if gross_loss != 0 else 0.0,
                    "total_fees": round(r["total_fees"], 2),
                    "best_trade": round(r["best_trade"], 2),
                    "worst_trade": round(r["worst_trade"], 2),
                }
            return result

    # ── Sesiones ─────────────────────────────────────────────────────

    def get_sessions(
        self,
        source: Optional[str] = None,
        limit: int = 50,
    ) -> List[SessionRecord]:
        """Lista sesiones de trading."""
        conditions = []
        params = []
        if source:
            conditions.append("source = ?")
            params.append(source)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""
            SELECT * FROM sessions WHERE {where}
            ORDER BY start_time DESC LIMIT ?
        """
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                SessionRecord(
                    session_id=r["session_id"],
                    source=r["source"],
                    symbol=r["symbol"],
                    start_time=r["start_time"],
                    end_time=r["end_time"],
                    initial_equity=r["initial_equity"],
                    final_equity=r["final_equity"],
                    total_trades=r["total_trades"],
                    total_pnl=r["total_pnl"],
                    max_drawdown=r["max_drawdown"],
                    strategies_used=r["strategies_used"],
                    notes=r["notes"],
                )
                for r in rows
            ]

    def get_trade_count(self, **kwargs) -> int:
        """Cuenta trades que cumplen los filtros."""
        conditions = []
        params = []
        for key in ("session_id", "source", "symbol", "strategy", "regime"):
            val = kwargs.get(key)
            if val:
                conditions.append(f"{key} = ?")
                params.append(val)

        where = " AND ".join(conditions) if conditions else "1=1"
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM trades WHERE {where}", params
            ).fetchone()
            return row["cnt"]

    # ── Utilidades ───────────────────────────────────────────────────

    def delete_session(self, session_id: str) -> int:
        """Elimina una sesión y todos sus trades. Retorna trades eliminados."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM trades WHERE session_id = ?", (session_id,)
            )
            count = cur.rowcount
            conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            conn.commit()
            return count

    def vacuum(self) -> None:
        """Compacta la base de datos."""
        with self._connect() as conn:
            conn.execute("VACUUM")

    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> TradeRecord:
        """Convierte fila SQLite a TradeRecord."""
        return TradeRecord(
            trade_id=row["trade_id"],
            session_id=row["session_id"],
            source=row["source"],
            symbol=row["symbol"],
            side=row["side"],
            price=row["price"],
            quantity=row["quantity"],
            fee=row["fee"],
            fee_asset=row["fee_asset"],
            pnl=row["pnl"],
            order_id=row["order_id"],
            strategy=row["strategy"],
            regime=row["regime"],
            trade_type=row["trade_type"],
            equity_before=row["equity_before"],
            equity_after=row["equity_after"],
            entry_price=row["entry_price"],
            exit_price=row["exit_price"],
            duration_sec=row["duration_sec"],
            micro_vpin=row["micro_vpin"],
            micro_risk_score=row["micro_risk_score"],
            timestamp=row["timestamp"],
        )
