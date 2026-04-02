"""
TelegramNotifier — Notificaciones en tiempo real via Telegram Bot API.

Envia notificaciones de todos los eventos del bot:
  - Startup / Shutdown
  - Trades ejecutados (live y paper)
  - Señales generadas
  - Cambios de régimen
  - Eventos de riesgo (drawdown, circuit breaker)
  - Estado del collector (resumen periodico)
  - Errores y crashes de tasks
  - Metricas de portfolio (resumen periodico)

Diseño:
  - Cola interna asyncio para desacoplar callers del envio HTTP
  - Rate limiting con token bucket (respeta limites de Telegram)
  - Mensajes de alta frecuencia se agrupan en resumenes periodicos
  - Fire-and-forget: notify() nunca bloquea ni lanza excepciones
  - Si Telegram esta caido, los mensajes se dropean silenciosamente

Uso:
    notifier = TelegramNotifier(bot_token, chat_id)
    await notifier.start()
    await notifier.notify_trade(trade)
    await notifier.stop()
"""
from __future__ import annotations
import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

# ── Rate limiting ────────────────────────────────────────────────
RATE_LIMIT_PER_SEC = 20       # Telegram permite ~30, usamos 20 con margen
MAX_QUEUE_SIZE = 500           # Mensajes en cola antes de dropear
SUMMARY_INTERVAL_SEC = 300    # Resumen cada 5 minutos
PORTFOLIO_SUMMARY_EVERY = 5   # Cada N llamadas a notify_portfolio (1 call/min → cada 5min)
ERROR_DEDUP_WINDOW_SEC = 300  # Mismo error suprimido por 5 minutos
SIGNAL_BATCH_SEC = 30         # Agrupar señales en ventanas de 30s

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


@dataclass
class _PendingSignal:
    """Señal pendiente de agrupar en batch."""
    strategy: str
    symbol: str
    side: str
    strength: float
    entry_price: float
    size_usd: float
    timestamp: float


class TelegramNotifier:
    """Notificador de Telegram con cola async y rate limiting."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._tasks: List[asyncio.Task] = []

        # Rate limiting: token bucket
        self._tokens = float(RATE_LIMIT_PER_SEC)
        self._max_tokens = float(RATE_LIMIT_PER_SEC)
        self._last_refill = time.monotonic()

        # Batching de señales
        self._signal_buffer: List[_PendingSignal] = []
        self._last_signal_flush = time.monotonic()

        # Collector status acumulado
        self._collector_status: Dict[str, Dict] = {}
        self._last_collector_summary = time.monotonic()

        # Portfolio snapshot counter (envia cada N)
        self._portfolio_counter = 0

        # Error dedup
        self._recent_errors: Dict[str, float] = {}

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Inicia el sender loop y el summary loop."""
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
        self._tasks = [
            asyncio.create_task(self._sender_loop(), name="tg_sender"),
            asyncio.create_task(self._summary_loop(), name="tg_summary"),
        ]
        logger.info("telegram_notifier_started", chat_id=self._chat_id)

    async def stop(self) -> None:
        """Drena cola pendiente y cierra."""
        self._running = False
        # Flush señales pendientes
        await self._flush_signals()
        await self._flush_collector_summary()
        # Esperar a que la cola se vacie (max 5s)
        try:
            await asyncio.wait_for(self._drain_queue(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        for t in self._tasks:
            t.cancel()
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("telegram_notifier_stopped")

    async def _drain_queue(self) -> None:
        """Envia todos los mensajes pendientes en la cola."""
        while not self._queue.empty():
            msg = self._queue.get_nowait()
            await self._send(msg)

    # ── Public API (fire-and-forget) ─────────────────────────────

    async def notify(self, text: str, priority: str = "NORMAL") -> None:
        """Notificacion generica. No bloquea, no lanza excepciones."""
        self._enqueue(text)

    async def notify_startup(self, mode: str, symbols: List[str],
                             config: Optional[Dict] = None) -> None:
        """Bot arrancando."""
        cfg = config or {}
        capital = cfg.get("initial_capital", "N/A")
        testnet = cfg.get("testnet", False)
        strategies = cfg.get("strategies", [])
        risk_per_trade = cfg.get("risk_per_trade_pct", 0)
        max_drawdown = cfg.get("max_drawdown_pct", 0)
        mode_desc = {
            "paper": "Paper Trading (simulado, sin dinero real)",
            "live": "🔥 LIVE (operando con dinero real)",
            "dry_run": "Dry Run (solo observa, no opera)",
        }.get(mode, mode)

        strat_nombres = {
            "MEAN_REVERSION": "Mean Reversion",
            "TREND_FOLLOWING": "Trend Following",
            "MARKET_MAKING": "Market Making",
            "ORDER_FLOW_MOMENTUM": "Order Flow Momentum",
        }
        strat_list = ", ".join(strat_nombres.get(s, s) for s in strategies) if strategies else "Todas"

        text = (
            "🟢 <b>Bot encendido</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 Modo: <b>{mode_desc}</b>\n"
            f"🌐 Red: {'Testnet' if testnet else 'Mainnet (real)'}\n\n"
            f"💰 Capital inicial: <b>${capital:,.0f}</b>\n"
            f"📊 Monedas: {', '.join(symbols)}\n"
            f"🧠 Estrategias: {strat_list}\n"
        )
        if risk_per_trade:
            text += f"⚖️ Riesgo por trade: {risk_per_trade:.1%}\n"
        if max_drawdown:
            text += f"🛡️ Max drawdown permitido: {max_drawdown:.0%}\n"

        self._enqueue(text)

    async def notify_shutdown(self, metrics: Optional[Dict] = None) -> None:
        """Bot deteniendose con resumen completo de sesion."""
        m = metrics or {}
        total = m.get("total_trades", 0)
        pnl = m.get("total_pnl", 0)
        net_pnl = m.get("net_pnl", pnl)
        fees = m.get("total_fees", 0)
        wr = m.get("win_rate", 0)
        avg_win = m.get("avg_win", 0)
        avg_loss = m.get("avg_loss", 0)
        pf = m.get("profit_factor", 0)
        sharpe = m.get("sharpe_ratio", 0)
        max_dd = m.get("max_drawdown", 0)
        runtime = m.get("runtime_hours", 0)
        by_strat = m.get("by_strategy", {})

        pnl_emoji = "✅" if net_pnl > 0 else ("❌" if net_pnl < 0 else "➖")

        text = (
            "🔴 <b>Bot apagado</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏱️ Tiempo activo: <b>{runtime:.1f}h</b>\n"
            f"📈 Operaciones: <b>{total}</b>\n\n"
        )

        if total > 0:
            text += (
                f"<b>💰 Resultado de sesion</b>\n"
                f"{pnl_emoji} PnL neto: <b>${net_pnl:+,.2f}</b>\n"
                f"💸 Comisiones pagadas: ${fees:,.2f}\n\n"
                f"<b>📊 Metricas clave</b>\n"
                f"🎯 Win rate: <b>{wr:.1%}</b>\n"
                f"✅ Ganancia promedio: ${avg_win:+,.4f}\n"
                f"❌ Perdida promedio: ${avg_loss:+,.4f}\n"
                f"📐 Profit factor: <b>{pf:.2f}</b>\n"
                f"📉 Max drawdown: {max_dd:.2%}\n"
            )
            if sharpe != 0:
                sharpe_emoji = "🟢" if sharpe > 1 else ("🟡" if sharpe > 0 else "🔴")
                text += f"{sharpe_emoji} Sharpe ratio: <b>{sharpe:.2f}</b>\n"

            # Desglose por estrategia
            if by_strat:
                strat_nombres = {
                    "MEAN_REVERSION": "MR", "TREND_FOLLOWING": "TF",
                    "MARKET_MAKING": "MM", "ORDER_FLOW_MOMENTUM": "OFM",
                }
                text += "\n<b>🧠 Por estrategia</b>\n"
                for st, data in sorted(by_strat.items()):
                    nombre = strat_nombres.get(st, st)
                    st_pnl = data.get("pnl", 0)
                    st_trades = data.get("trades", 0)
                    st_wr = data.get("win_rate", 0)
                    st_emoji = "✅" if st_pnl > 0 else ("❌" if st_pnl < 0 else "➖")
                    text += f"  {st_emoji} {nombre}: ${st_pnl:+,.2f} ({st_trades} ops, {st_wr:.0%} wr)\n"
        else:
            text += "Sin operaciones en esta sesion.\n"

        self._enqueue(text)

    async def notify_trade(self, trade: Any) -> None:
        """Trade ejecutado (live o paper). Envio inmediato con detalle completo."""
        side = getattr(trade, "side", "?")
        side_str = side.value if hasattr(side, "value") else str(side)
        symbol = getattr(trade, "symbol", "?")
        price = getattr(trade, "price", 0)
        qty = getattr(trade, "quantity", 0)
        fee = getattr(trade, "fee", 0)
        pnl = getattr(trade, "pnl", 0)
        strategy = getattr(trade, "strategy", "")
        strat_str = strategy.value if hasattr(strategy, "value") else str(strategy)
        slippage_bps = getattr(trade, "actual_slippage_bps", 0)
        expected_price = getattr(trade, "expected_price", 0)
        latency_ms = getattr(trade, "latency_ms", 0)

        nocional = price * qty if price and qty else 0
        accion = "Compra" if side_str == "BUY" else "Venta"
        emoji = "🟢" if side_str == "BUY" else "🔴"

        strat_nombre = {
            "MEAN_REVERSION": "Mean Reversion",
            "TREND_FOLLOWING": "Trend Following",
            "MARKET_MAKING": "Market Making",
            "ORDER_FLOW_MOMENTUM": "Order Flow Momentum",
        }.get(strat_str, strat_str)

        pnl_emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")

        text = (
            f"{emoji} <b>{accion} — {symbol}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💲 Precio: <b>${price:,.4f}</b>\n"
            f"💰 Nocional: ${nocional:,.2f}\n"
            f"🧠 Estrategia: {strat_nombre}\n"
            f"💸 Comision: ${fee:,.4f}\n"
            f"{pnl_emoji} Resultado: <b>${pnl:+,.4f}</b>\n"
        )

        # Slippage y ejecucion
        if expected_price > 0:
            text += f"\n<b>⚡ Ejecucion</b>\n"
            text += f"🎯 Precio esperado: ${expected_price:,.4f}\n"
            slip_emoji = "🟢" if abs(slippage_bps) < 5 else ("🟡" if abs(slippage_bps) < 15 else "🔴")
            text += f"{slip_emoji} Slippage: {slippage_bps:+.1f} bps\n"
            if latency_ms > 0:
                text += f"⏱️ Latencia: {latency_ms:.0f}ms\n"

        self._enqueue(text)

    async def notify_signal(self, signal: Any) -> None:
        """Senal generada. Se agrupa en batches de 30s."""
        side = getattr(signal, "side", "?")
        strategy = getattr(signal, "strategy", "?")
        self._signal_buffer.append(_PendingSignal(
            strategy=strategy.value if hasattr(strategy, "value") else str(strategy),
            symbol=getattr(signal, "symbol", "?"),
            side=side.value if hasattr(side, "value") else str(side),
            strength=getattr(signal, "strength", 0),
            entry_price=getattr(signal, "entry_price", 0),
            size_usd=getattr(signal, "size_usd", 0),
            timestamp=time.time(),
        ))

    async def notify_regime_change(self, symbol: str, old_regime: Any,
                                   new_regime: Any) -> None:
        """Cambio de regimen de mercado. Envio inmediato."""
        old_str = old_regime.value if hasattr(old_regime, "value") else str(old_regime)
        new_str = new_regime.value if hasattr(new_regime, "value") else str(new_regime)

        desc_map = {
            "TRENDING_UP": ("📈", "Tendencia alcista", "el precio sube con fuerza — Trend Following favorecido"),
            "TRENDING_DOWN": ("📉", "Tendencia bajista", "el precio baja con fuerza — Trend Following favorecido"),
            "RANGING": ("↔️", "Mercado lateral", "sin direccion clara — Mean Reversion y Market Making favorecidos"),
            "BREAKOUT": ("💥", "Ruptura", "rotura de nivel importante — volatilidad esperada alta"),
            "UNKNOWN": ("❓", "Indefinido", "datos insuficientes — operando con cautela"),
        }
        old_emoji, old_nombre, _ = desc_map.get(old_str, ("🔄", old_str, ""))
        emoji, nombre, explicacion = desc_map.get(new_str, ("🔄", new_str, ""))

        text = (
            f"{emoji} <b>Cambio de regimen — {symbol}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Antes: {old_emoji} {old_nombre}\n"
            f"Ahora: {emoji} <b>{nombre}</b>\n\n"
            f"💡 {explicacion}"
        )
        self._enqueue(text)

    async def notify_risk_event(self, event: str, details: Optional[Dict] = None) -> None:
        """Evento de riesgo critico. Envio inmediato con contexto completo."""
        d = details or {}

        event_desc = {
            "max_drawdown": "Perdida maxima alcanzada",
            "circuit_breaker": "Freno de emergencia activado",
            "impact_stress": "Mercado demasiado peligroso para operar",
            "ror_throttle": "Risk of Ruin elevado — reduciendo posiciones",
            "correlation_stress": "Correlacion entre activos anormalmente alta",
        }.get(event, event)

        event_action = {
            "max_drawdown": "Se cancelan todas las ordenes abiertas. El bot seguira monitoreando pero no abrira nuevas posiciones hasta que el drawdown se recupere.",
            "circuit_breaker": "TODAS las operaciones pausadas. Se requiere intervencion manual o esperar el cooldown automatico.",
            "impact_stress": "Se reducen los tamanos de posicion y se evitan nuevas entradas hasta que el impacto de mercado se normalice.",
            "ror_throttle": "Se reduce el tamano de nuevas posiciones para proteger el capital.",
            "correlation_stress": "Se reducen posiciones para limitar riesgo de movimiento correlacionado.",
        }.get(event, "Se han tomado medidas de proteccion automaticas.")

        label_map = {
            "drawdown_pct": "📉 Drawdown actual",
            "threshold": "🛡️ Limite configurado",
            "equity": "💰 Equity actual",
            "equity_peak": "🏔️ Peak de equity",
            "consecutive_losses": "📊 Perdidas consecutivas",
            "total_exposure": "📏 Exposicion total",
            "risk_of_ruin": "☠️ Risk of Ruin",
            "avg_correlation": "🔗 Correlacion media",
        }

        detail_lines = ""
        for k, v in d.items():
            label = label_map.get(k, k)
            if isinstance(v, float):
                if k.endswith("_pct") or k in ("risk_of_ruin", "avg_correlation"):
                    detail_lines += f"  {label}: {v:.2%}\n"
                else:
                    detail_lines += f"  {label}: ${v:,.2f}\n"
            else:
                detail_lines += f"  {label}: {v}\n"

        text = (
            "🚨 <b>ALERTA DE RIESGO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚠️ <b>{event_desc}</b>\n\n"
        )
        if detail_lines:
            text += f"{detail_lines}\n"
        text += f"🛡️ <i>{event_action}</i>"

        self._enqueue(text)

    async def notify_error(self, task_name: str, error: str) -> None:
        """Error/crash de un task. Dedup por 5 minutos."""
        key = f"{task_name}:{error[:100]}"
        now = time.time()
        last = self._recent_errors.get(key, 0)
        if now - last < ERROR_DEDUP_WINDOW_SEC:
            return  # Suprimir duplicado
        self._recent_errors[key] = now
        self._recent_errors = {
            k: v for k, v in self._recent_errors.items()
            if now - v < ERROR_DEDUP_WINDOW_SEC
        }

        task_desc = {
            "ws_market": "Conexion al exchange",
            "strategy": "Motor de estrategias",
            "mm": "Market Making",
            "risk_monitor": "Monitor de riesgo",
            "data_refresh": "Actualizacion de datos",
            "metrics": "Metricas",
        }.get(task_name, task_name)

        text = (
            f"❌ <b>Error en el bot</b>\n\n"
            f"Componente: {task_desc}\n"
            f"Detalle: <code>{error[:300]}</code>\n\n"
            f"El sistema intentara reiniciar este componente automaticamente."
        )
        self._enqueue(text)

    async def notify_collector_status(self, stats: Dict) -> None:
        """Status del collector. Se acumula y envia cada 5min."""
        symbol = stats.get("symbol", "?")
        self._collector_status[symbol] = stats

    async def notify_collector_flush(self, data_type: str, symbol: str,
                                     count: int) -> None:
        """Flush a disco del collector. Se acumula en el resumen."""
        key = f"{symbol}_flush_{data_type}"
        existing = self._collector_status.get(symbol, {})
        existing[f"flush_{data_type}"] = existing.get(f"flush_{data_type}", 0) + count
        self._collector_status[symbol] = existing

    async def notify_portfolio_snapshot(self, summary: Dict) -> None:
        """Snapshot de portfolio completo. Envia cada 5 llamadas (5 min)."""
        self._portfolio_counter += 1
        if self._portfolio_counter < PORTFOLIO_SUMMARY_EVERY:
            return
        self._portfolio_counter = 0

        equity = summary.get("equity", summary.get("total_equity", 0))
        risk = summary.get("risk", {})
        pnl = risk.get("total_pnl", summary.get("total_pnl", 0))
        daily_pnl = risk.get("daily_pnl", 0)
        dd = risk.get("drawdown_pct", summary.get("max_drawdown_pct", 0))
        equity_peak = risk.get("equity_peak", equity)
        consec_losses = risk.get("consecutive_losses", 0)
        circuit_breaker = risk.get("circuit_breaker", False)
        total_exposure = risk.get("total_exposure", 0)
        positions = risk.get("positions", {})

        # Quant models
        ror = risk.get("risk_of_ruin", 0)
        vol_scalar = risk.get("vol_target_scalar", 1.0)
        vol_realized = risk.get("vol_realized", 0)
        corr_stress = risk.get("correlation_stress", False)
        avg_corr = risk.get("avg_correlation", 0)
        slippage_bps = risk.get("slippage_avg_bps", 0)
        slippage_n = risk.get("slippage_samples", 0)
        kelly = risk.get("kelly_fractions", {})

        # Strategy data
        strat_pnl = summary.get("strategy_pnl", {})
        strat_trades = summary.get("strategy_trades", {})
        weights = summary.get("weights", {})

        pnl_emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
        daily_emoji = "✅" if daily_pnl > 0 else ("❌" if daily_pnl < 0 else "➖")
        dd_emoji = "🟢" if dd < 0.05 else ("🟡" if dd < 0.10 else "🔴")

        text = (
            "📊 <b>Portfolio</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Equity: <b>${equity:,.2f}</b>\n"
            f"🏔️ Peak: ${equity_peak:,.2f}\n"
            f"{pnl_emoji} PnL total: <b>${pnl:+,.2f}</b>\n"
            f"{daily_emoji} PnL hoy: ${daily_pnl:+,.2f}\n"
            f"{dd_emoji} Drawdown: {dd:.2%}\n"
        )

        # Posiciones abiertas
        active_pos = {s: n for s, n in positions.items() if abs(n) > 0.01}
        if active_pos:
            text += f"\n<b>📍 Posiciones abiertas</b>\n"
            for sym, notional in sorted(active_pos.items()):
                text += f"  {'🟢' if notional > 0 else '🔴'} {sym}: ${notional:+,.2f}\n"
            text += f"📏 Exposicion total: ${total_exposure:,.2f}\n"
        else:
            text += f"\n📍 Sin posiciones abiertas\n"

        # Estrategias
        total_trades = sum(strat_trades.values())
        if total_trades > 0:
            strat_nombres = {
                "MEAN_REVERSION": "MR", "TREND_FOLLOWING": "TF",
                "MARKET_MAKING": "MM", "ORDER_FLOW_MOMENTUM": "OFM",
            }
            text += f"\n<b>🧠 Estrategias</b> ({total_trades} ops)\n"
            for st in sorted(strat_pnl.keys()):
                nombre = strat_nombres.get(st, st)
                sp = strat_pnl.get(st, 0)
                st_n = strat_trades.get(st, 0)
                w = weights.get(st, 0)
                k = kelly.get(st, 0)
                if st_n > 0:
                    st_emoji = "✅" if sp > 0 else ("❌" if sp < 0 else "➖")
                    text += f"  {st_emoji} {nombre}: ${sp:+,.2f} | {st_n} ops | peso {w:.0%} | kelly {k:.1%}\n"

        # Modelos cuantitativos
        text += f"\n<b>🔬 Risk Engine</b>\n"
        ror_emoji = "🟢" if ror < 0.05 else ("🟡" if ror < 0.15 else "🔴")
        text += f"{ror_emoji} Risk of ruin: {ror:.1%}\n"

        vol_emoji = "🟢" if 0.8 <= vol_scalar <= 1.2 else "🟡"
        text += f"{vol_emoji} Vol target: x{vol_scalar:.2f} (vol real: {vol_realized:.2%})\n"

        if slippage_n > 0:
            slip_emoji = "🟢" if slippage_bps < 5 else ("🟡" if slippage_bps < 15 else "🔴")
            text += f"{slip_emoji} Slippage prom: {slippage_bps:.1f} bps ({slippage_n} fills)\n"

        if corr_stress:
            text += f"⚠️ Correlacion elevada: {avg_corr:.2f}\n"

        if consec_losses >= 3:
            text += f"⚠️ Rachas perdedoras: {consec_losses} seguidas\n"

        if circuit_breaker:
            text += "🚨 <b>CIRCUIT BREAKER ACTIVO</b>\n"

        self._enqueue(text)

    # ── Internal: enqueue ────────────────────────────────────────

    def _enqueue(self, text: str) -> None:
        """Añade mensaje a la cola. No bloquea; dropea si esta llena."""
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            logger.warning("telegram_queue_full_dropping_message")

    # ── Internal: sender loop ────────────────────────────────────

    async def _sender_loop(self) -> None:
        """Drena la cola y envia mensajes respetando rate limit."""
        while self._running:
            try:
                text = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._acquire_token()
                await self._send(text)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("telegram_sender_error", error=str(e))
                await asyncio.sleep(1)

    async def _summary_loop(self) -> None:
        """Loop periodico: flush señales batched y collector summary."""
        while self._running:
            try:
                await asyncio.sleep(SIGNAL_BATCH_SEC)

                # Flush señales agrupadas
                await self._flush_signals()

                # Flush collector summary cada 5 min
                now = time.monotonic()
                if now - self._last_collector_summary >= SUMMARY_INTERVAL_SEC:
                    await self._flush_collector_summary()
                    self._last_collector_summary = now

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("telegram_summary_error", error=str(e))
                await asyncio.sleep(5)

    async def _flush_signals(self) -> None:
        """Agrupa senales pendientes en un solo mensaje."""
        if not self._signal_buffer:
            return
        signals = self._signal_buffer[:]
        self._signal_buffer.clear()

        strat_nombre = {
            "MEAN_REVERSION": "Mean Reversion",
            "TREND_FOLLOWING": "Trend Following",
            "MARKET_MAKING": "Market Making",
            "ORDER_FLOW_MOMENTUM": "Order Flow",
        }

        buys = sum(1 for s in signals if s.side == "BUY")
        sells = len(signals) - buys

        lines = []
        for s in signals:
            accion = "Comprar" if s.side == "BUY" else "Vender"
            emoji = "🟢" if s.side == "BUY" else "🔴"
            if s.strength > 0.7:
                confianza = "🔥 alta"
            elif s.strength > 0.4:
                confianza = "🟡 media"
            else:
                confianza = "⚪ baja"
            lines.append(
                f"{emoji} {accion} <b>{s.symbol}</b> a ${s.entry_price:,.4f}\n"
                f"    🧠 {strat_nombre.get(s.strategy, s.strategy)}\n"
                f"    📊 Confianza: {confianza} ({s.strength:.0%})\n"
                f"    💰 Tamano: ${s.size_usd:,.2f}"
            )

        text = (
            f"📡 <b>Senales detectadas</b> ({len(signals)})\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Compras: {buys} | 🔴 Ventas: {sells}\n\n"
            + "\n\n".join(lines)
        )
        self._enqueue(text)

    async def _flush_collector_summary(self) -> None:
        """Envia resumen acumulado del collector."""
        if not self._collector_status:
            return
        status = dict(self._collector_status)
        self._collector_status.clear()

        lines = []
        all_ok = True
        for symbol, stats in sorted(status.items()):
            trades_total = stats.get("trades_today", stats.get("total_trades_today", 0))
            klines = stats.get("kline_bars", 0)
            ob = stats.get("ob_rows", 0)
            ws_t = stats.get("ws_trades", 0)
            rest_t = stats.get("rest_trades", 0)
            nuevos = ws_t + rest_t
            last_price = stats.get("last_price", 0)

            price_str = ""
            if isinstance(last_price, (int, float)) and last_price > 0:
                if last_price < 1:
                    price_str = f"${last_price:.4f}"
                else:
                    price_str = f"${last_price:,.2f}"

            line = f"<b>{symbol}</b>"
            if price_str:
                line += f" — {price_str}"

            if nuevos > 0:
                line += f"\n  Trades nuevos esta sesion: {nuevos}"
            else:
                line += f"\n  Trades nuevos: ninguno (sin actividad)"
                all_ok = False

            line += f"\n  Velas guardadas: {klines:,}"
            line += f"\n  Capturas orderbook hoy: {ob:,}"

            if ws_t > 0:
                line += f"\n  Conexion: activa y recibiendo"
            else:
                line += f"\n  Conexion: activa, esperando trades"

            lines.append(line)

        estado = "Todo funcionando correctamente." if all_ok else "Conectado al exchange. Algunos pares sin trades nuevos (normal en horas de baja actividad)."

        text = (
            f"📦 <b>Recoleccion de datos</b>\n"
            f"{estado}\n\n"
            + "\n\n".join(lines)
        )
        self._enqueue(text)

    # ── Rate limiting (token bucket) ─────────────────────────────

    async def _acquire_token(self) -> None:
        """Espera hasta que haya un token disponible."""
        while True:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._max_tokens,
                self._tokens + elapsed * RATE_LIMIT_PER_SEC,
            )
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # Esperar hasta que se regenere 1 token
            await asyncio.sleep(1.0 / RATE_LIMIT_PER_SEC)

    # ── HTTP send ────────────────────────────────────────────────

    async def _send(self, text: str) -> bool:
        """Envia un mensaje via Telegram Bot API. Retorna True si OK."""
        if not self._session or self._session.closed:
            return False
        url = TELEGRAM_API.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return True
                elif resp.status == 429:
                    # Rate limited by Telegram
                    data = await resp.json()
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.warning("telegram_rate_limited", retry_after=retry_after)
                    await asyncio.sleep(retry_after)
                    return False
                else:
                    body = await resp.text()
                    logger.warning("telegram_send_failed",
                                   status=resp.status, body=body[:200])
                    return False
        except Exception as e:
            logger.error("telegram_send_error", error=str(e))
            return False


class NullNotifier(TelegramNotifier):
    """No-op: todas las notificaciones se ignoran silenciosamente.

    Se usa cuando no hay token de Telegram configurado.
    Hereda la interfaz para que los callers no necesiten if-checks.
    """

    def __init__(self) -> None:
        # No llamar a super().__init__ — no necesitamos nada
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def notify(self, text: str, priority: str = "NORMAL") -> None:
        pass

    async def notify_startup(self, mode: str, symbols: List[str],
                             config: Optional[Dict] = None) -> None:
        pass

    async def notify_shutdown(self, metrics: Optional[Dict] = None) -> None:
        pass

    async def notify_trade(self, trade: Any) -> None:
        pass

    async def notify_signal(self, signal: Any) -> None:
        pass

    async def notify_regime_change(self, symbol: str, old_regime: Any,
                                   new_regime: Any) -> None:
        pass

    async def notify_risk_event(self, event: str, details: Optional[Dict] = None) -> None:
        pass

    async def notify_error(self, task_name: str, error: str) -> None:
        pass

    async def notify_collector_status(self, stats: Dict) -> None:
        pass

    async def notify_collector_flush(self, data_type: str, symbol: str,
                                     count: int) -> None:
        pass

    async def notify_portfolio_snapshot(self, summary: Dict) -> None:
        pass
