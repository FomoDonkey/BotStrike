"""
TradeDBAdapter — Conecta el Trade Database con el sistema existente.

Este adaptador NO modifica interfaces existentes. Se inyecta como un
observador adicional en los puntos de integración:

  - Live trading: se registra en los callbacks de on_order_update
  - Backtesting: se pasa como parámetro opcional al backtester
  - Importación: puede importar trades desde BacktestResult o JSONL

Patrón: Observer/Adapter — el sistema existente sigue funcionando
exactamente igual, el adapter solo escucha y persiste.
"""
from __future__ import annotations
import time
import uuid
from typing import Dict, List, Optional

from core.types import Trade, MarketRegime, StrategyType
from trade_database.models import TradeRecord, SessionRecord
from trade_database.repository import TradeRepository
import structlog

logger = structlog.get_logger(__name__)


class TradeDBAdapter:
    """Adaptador que conecta el sistema de trading con el Trade Database.

    Uso en live trading:
        adapter = TradeDBAdapter(repo, source="live")
        adapter.start_session(initial_equity=100000)
        # En cada trade ejecutado:
        adapter.on_trade(trade, regime, equity, micro_snap)
        # Al terminar:
        adapter.end_session(final_equity=105000)

    Uso en backtesting:
        adapter = TradeDBAdapter(repo, source="backtest")
        adapter.start_session(initial_equity=100000, symbol="BTC-USD")
        # El backtester llama on_backtest_trade() por cada trade
        adapter.end_session(final_equity=102000)
    """

    def __init__(
        self,
        repository: TradeRepository,
        source: str = "live",
    ) -> None:
        self.repo = repository
        self.source = source
        self._session_id: str = ""
        self._session_symbol: str = ""
        self._session_start: float = 0.0
        self._trade_count: int = 0
        self._total_pnl: float = 0.0
        self._initial_equity: float = 0.0
        self._equity_peak: float = 0.0
        self._max_drawdown: float = 0.0
        self._current_equity: float = 0.0
        self._strategies_used: set = set()
        # Buffer para batch inserts (eficiente en backtesting)
        self._buffer: List[TradeRecord] = []
        self._buffer_size: int = 100  # flush cada 100 trades

    def start_session(
        self,
        initial_equity: float = 0.0,
        symbol: str = "",
        notes: str = "",
    ) -> str:
        """Inicia una nueva sesión de trading.

        Returns:
            session_id generado
        """
        self._session_id = uuid.uuid4().hex[:12]
        self._session_symbol = symbol
        self._session_start = time.time()
        self._trade_count = 0
        self._total_pnl = 0.0
        self._initial_equity = initial_equity
        self._equity_peak = initial_equity
        self._max_drawdown = 0.0
        self._current_equity = initial_equity
        self._strategies_used = set()
        self._buffer = []

        session = SessionRecord(
            session_id=self._session_id,
            source=self.source,
            symbol=symbol,
            start_time=self._session_start,
            initial_equity=initial_equity,
            notes=notes,
        )
        self.repo.insert_session(session)
        logger.info("trade_db_session_started",
                     session_id=self._session_id, source=self.source)
        return self._session_id

    def end_session(
        self,
        final_equity: float = 0.0,
        max_drawdown: float = 0.0,
    ) -> None:
        """Cierra la sesión actual y actualiza estadísticas."""
        # Flush buffer pendiente
        self._flush_buffer()

        if max_drawdown > 0:
            self._max_drawdown = max(self._max_drawdown, max_drawdown)

        session = SessionRecord(
            session_id=self._session_id,
            source=self.source,
            symbol=self._session_symbol,
            start_time=self._session_start,
            end_time=time.time(),
            initial_equity=self._initial_equity,
            final_equity=final_equity or self._current_equity,
            total_trades=self._trade_count,
            total_pnl=round(self._total_pnl, 2),
            max_drawdown=round(self._max_drawdown, 4),
            strategies_used=",".join(sorted(self._strategies_used)),
        )
        self.repo.insert_session(session)
        logger.info("trade_db_session_ended",
                     session_id=self._session_id,
                     trades=self._trade_count,
                     pnl=round(self._total_pnl, 2))

    # ── Registrar trades desde live trading ──────────────────────────

    def on_trade(
        self,
        trade: Trade,
        regime: Optional[MarketRegime] = None,
        equity_before: float = 0.0,
        equity_after: float = 0.0,
        micro_vpin: float = 0.0,
        micro_risk_score: float = 0.0,
        trade_type: str = "",
        entry_price: float = 0.0,
        duration_sec: float = 0.0,
    ) -> None:
        """Registra un trade desde el sistema live.

        Convierte core.types.Trade a TradeRecord y lo persiste.
        """
        record = TradeRecord(
            session_id=self._session_id,
            source=self.source,
            symbol=trade.symbol,
            side=trade.side.value if hasattr(trade.side, "value") else str(trade.side),
            price=trade.price,
            quantity=trade.quantity,
            fee=trade.fee,
            fee_asset=trade.fee_asset,
            pnl=trade.pnl,
            order_id=trade.order_id,
            strategy=trade.strategy.value if trade.strategy else "",
            regime=regime.value if regime else "",
            trade_type=trade_type,
            equity_before=equity_before,
            equity_after=equity_after,
            entry_price=entry_price,
            exit_price=trade.price,
            duration_sec=duration_sec,
            micro_vpin=micro_vpin,
            micro_risk_score=micro_risk_score,
            timestamp=trade.timestamp,
        )
        self._track(record)
        self.repo.insert_trade(record)

    # ── Registrar trades desde backtesting ───────────────────────────

    def on_backtest_trade(
        self,
        trade_dict: dict,
        equity_before: float = 0.0,
        equity_after: float = 0.0,
        regime: str = "",
        micro_vpin: float = 0.0,
        micro_risk_score: float = 0.0,
    ) -> None:
        """Registra un trade desde el backtester.

        El backtester usa dicts (no core.types.Trade), así que se convierte.
        """
        side = trade_dict.get("side", "")
        # Determinar trade_type del side
        trade_type = "ENTRY"
        if side.startswith("CLOSE_"):
            trade_type = "EXIT"
        elif side.startswith("SL_"):
            trade_type = "SL"
        elif side.startswith("TP_"):
            trade_type = "TP"
        elif side == "LIQUIDATION":
            trade_type = "LIQUIDATION"
        elif side == "CLOSE_EOD":
            trade_type = "CLOSE_EOD"

        record = TradeRecord(
            session_id=self._session_id,
            source=self.source,
            symbol=trade_dict.get("symbol", ""),
            side=side,
            price=trade_dict.get("exit", trade_dict.get("entry", 0)),
            quantity=trade_dict.get("size", 0),
            fee=trade_dict.get("fee", 0),
            pnl=trade_dict.get("pnl", 0),
            strategy=trade_dict.get("strategy", ""),
            regime=regime,
            trade_type=trade_type,
            equity_before=equity_before,
            equity_after=equity_after,
            entry_price=trade_dict.get("entry", 0),
            exit_price=trade_dict.get("exit", 0),
            duration_sec=trade_dict.get("duration_sec", 0),
            micro_vpin=micro_vpin,
            micro_risk_score=micro_risk_score,
            timestamp=trade_dict.get("timestamp", time.time()),
        )
        self._track(record)
        self._buffer.append(record)

        if len(self._buffer) >= self._buffer_size:
            self._flush_buffer()

    # ── Importar desde BacktestResult ────────────────────────────────

    def import_backtest_result(
        self,
        result,  # BacktestResult o RealisticBacktestResult
        symbol: str = "",
        initial_equity: float = 100_000.0,
        notes: str = "",
    ) -> str:
        """Importa todos los trades de un BacktestResult al DB.

        Returns:
            session_id de la sesión creada
        """
        self.start_session(
            initial_equity=initial_equity,
            symbol=symbol,
            notes=notes or f"Backtest import {symbol}",
        )

        equity = initial_equity
        regime_history = getattr(result, "regime_history", [])
        micro_history = getattr(result, "microstructure_history", [])

        # regime_history starts at start_idx (not bar 0), so compute offset
        # from max bar index in trades: offset = max_bar - len(history) + 1
        regime_offset = 0
        micro_offset = 0
        if result.trades:
            max_bar = max(t.get("bar", 0) for t in result.trades)
            if regime_history:
                regime_offset = max(0, max_bar - len(regime_history) + 1)
            if micro_history:
                micro_offset = max(0, max_bar - len(micro_history) + 1)

        for i, trade_dict in enumerate(result.trades):
            equity_before = equity
            pnl = trade_dict.get("pnl", 0)
            equity += pnl

            # Obtener régimen del historial (ajustar por offset)
            bar_idx = trade_dict.get("bar", i)
            regime = ""
            adjusted_idx = bar_idx - regime_offset
            if regime_history and 0 <= adjusted_idx < len(regime_history):
                regime = regime_history[adjusted_idx]

            # Obtener micro del historial (ajustar por offset)
            vpin = 0.0
            risk_score = 0.0
            adjusted_micro = bar_idx - micro_offset
            if micro_history and 0 <= adjusted_micro < len(micro_history):
                micro = micro_history[adjusted_micro]
                vpin = micro.get("vpin", 0)
                risk_score = micro.get("risk_score", 0)

            self.on_backtest_trade(
                trade_dict,
                equity_before=equity_before,
                equity_after=equity,
                regime=regime,
                micro_vpin=vpin,
                micro_risk_score=risk_score,
            )

        # Calcular max drawdown desde equity curve
        max_dd = 0.0
        if result.equity_curve:
            peak = result.equity_curve[0]
            for eq in result.equity_curve:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

        self.end_session(final_equity=equity, max_drawdown=max_dd)
        return self._session_id

    # ── Internos ─────────────────────────────────────────────────────

    def _track(self, record: TradeRecord) -> None:
        """Actualiza estadísticas internas de la sesión."""
        self._trade_count += 1
        self._total_pnl += record.pnl
        if record.strategy:
            self._strategies_used.add(record.strategy)
        if record.equity_after > 0:
            self._current_equity = record.equity_after
            if record.equity_after > self._equity_peak:
                self._equity_peak = record.equity_after
            if self._equity_peak > 0:
                dd = (self._equity_peak - record.equity_after) / self._equity_peak
                self._max_drawdown = max(self._max_drawdown, dd)

    def _flush_buffer(self) -> None:
        """Escribe buffer de trades a DB."""
        if self._buffer:
            self.repo.insert_trades_batch(self._buffer)
            self._buffer = []

    @property
    def session_id(self) -> str:
        return self._session_id
