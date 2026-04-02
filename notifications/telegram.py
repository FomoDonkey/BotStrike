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
        mode_desc = {
            "paper": "Paper Trading (simulado, sin dinero real)",
            "live": "LIVE (operando con dinero real)",
            "dry_run": "Dry Run (solo observa, no opera)",
        }.get(mode, mode)
        text = (
            "🟢 <b>Bot encendido</b>\n\n"
            f"Modo: <b>{mode_desc}</b>\n"
            f"Monedas: {', '.join(symbols)}\n"
            f"Capital inicial: ${capital:,.0f}\n"
            f"Red: {'Testnet' if testnet else 'Mainnet (real)'}"
        )
        self._enqueue(text)

    async def notify_shutdown(self, metrics: Optional[Dict] = None) -> None:
        """Bot deteniendose."""
        m = metrics or {}
        total = m.get("total_trades", 0)
        pnl = m.get("total_pnl", 0)
        wr = m.get("win_rate", 0)
        text = (
            "🔴 <b>Bot apagado</b>\n\n"
            f"Operaciones realizadas: {total}\n"
            f"Ganancia/Perdida total: ${pnl:+,.2f}\n"
            f"Tasa de acierto: {wr:.1%}"
        )
        self._enqueue(text)

    async def notify_trade(self, trade: Any) -> None:
        """Trade ejecutado (live o paper). Envio inmediato."""
        side = getattr(trade, "side", "?")
        side_str = side.value if hasattr(side, "value") else str(side)
        symbol = getattr(trade, "symbol", "?")
        price = getattr(trade, "price", 0)
        qty = getattr(trade, "quantity", 0)
        fee = getattr(trade, "fee", 0)
        pnl = getattr(trade, "pnl", 0)
        strategy = getattr(trade, "strategy", "")
        strat_str = strategy.value if hasattr(strategy, "value") else str(strategy)

        nocional = price * qty if price and qty else 0
        accion = "Compra" if side_str == "BUY" else "Venta"
        emoji = "🟢" if side_str == "BUY" else "🔴"

        strat_nombre = {
            "MEAN_REVERSION": "Mean Reversion",
            "TREND_FOLLOWING": "Trend Following",
            "MARKET_MAKING": "Market Making",
        }.get(strat_str, strat_str)

        pnl_emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")

        text = (
            f"{emoji} <b>{accion} de {symbol}</b>\n\n"
            f"Precio: ${price:,.4f}\n"
            f"Cantidad invertida: ${nocional:,.2f}\n"
            f"Estrategia: {strat_nombre}\n"
            f"Comision: ${fee:,.4f}\n"
            f"{pnl_emoji} Resultado: <b>${pnl:+,.4f}</b>"
        )
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
            "TRENDING_UP": ("📈", "Tendencia alcista", "el precio esta subiendo con fuerza"),
            "TRENDING_DOWN": ("📉", "Tendencia bajista", "el precio esta bajando con fuerza"),
            "RANGING": ("↔️", "Mercado lateral", "el precio se mueve en un rango sin direccion clara"),
            "BREAKOUT": ("💥", "Ruptura", "el precio rompio un nivel importante"),
            "UNKNOWN": ("❓", "Indefinido", "no hay suficientes datos para determinar la tendencia"),
        }
        emoji, nombre, explicacion = desc_map.get(new_str, ("🔄", new_str, ""))

        text = (
            f"{emoji} <b>Cambio de mercado en {symbol}</b>\n\n"
            f"Nuevo estado: <b>{nombre}</b>\n"
            f"({explicacion})"
        )
        self._enqueue(text)

    async def notify_risk_event(self, event: str, details: Optional[Dict] = None) -> None:
        """Evento de riesgo critico. Envio inmediato."""
        d = details or {}

        event_desc = {
            "max_drawdown": "Perdida maxima alcanzada",
            "circuit_breaker": "Freno de emergencia activado",
            "impact_stress": "Mercado demasiado peligroso para operar",
        }.get(event, event)

        detail_lines = ""
        for k, v in d.items():
            label = {
                "drawdown_pct": "Perdida acumulada",
                "threshold": "Limite configurado",
            }.get(k, k)
            detail_lines += f"{label}: {v}\n"

        text = (
            f"🚨 <b>ALERTA DE RIESGO</b>\n\n"
            f"<b>{event_desc}</b>\n"
            f"{detail_lines}\n"
            f"Se han cancelado todas las ordenes abiertas por seguridad."
        )
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
        """Snapshot de portfolio. Envia cada 5 llamadas (5 min)."""
        self._portfolio_counter += 1
        if self._portfolio_counter < PORTFOLIO_SUMMARY_EVERY:
            return
        self._portfolio_counter = 0

        equity = summary.get("equity", summary.get("total_equity", 0))
        risk = summary.get("risk", {})
        pnl = risk.get("total_pnl", summary.get("total_pnl", 0))
        dd = risk.get("drawdown_pct", summary.get("max_drawdown_pct", 0))

        pnl_emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
        dd_emoji = "🟢" if dd < 0.05 else ("🟡" if dd < 0.10 else "🔴")

        text = (
            f"📊 <b>Resumen del portfolio</b> (cada 5 min)\n\n"
            f"Capital actual: <b>${equity:,.2f}</b>\n"
            f"{pnl_emoji} Ganancia/Perdida: ${pnl:+,.2f}\n"
            f"{dd_emoji} Peor caida: {dd:.2%}"
        )
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
        }

        lines = []
        for s in signals:
            accion = "Comprar" if s.side == "BUY" else "Vender"
            emoji = "🟢" if s.side == "BUY" else "🔴"
            confianza = "alta" if s.strength > 0.7 else ("media" if s.strength > 0.4 else "baja")
            lines.append(
                f"{emoji} {accion} <b>{s.symbol}</b> a ${s.entry_price:,.2f}\n"
                f"    Estrategia: {strat_nombre.get(s.strategy, s.strategy)}\n"
                f"    Confianza: {confianza} ({s.strength:.0%}) | Monto: ${s.size_usd:,.0f}"
            )

        text = (
            f"📡 <b>Senales detectadas</b> ({len(signals)})\n\n"
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
