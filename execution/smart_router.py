"""
Smart Order Router — Motor de ejecucion inteligente nivel institucional.

Reemplaza la logica hardcodeada de order type selection con un modelo
de costos que optimiza: LIMIT vs MARKET, precio de colocacion, sizing,
y timing de ejecucion.

Contiene:
    1. FillProbabilityModel  — P(fill | distance, vol, depth, intensity)
    2. QueuePositionModel    — Estimacion de posicion en la cola
    3. SmartOrderRouter      — Decision limit vs market basada en costos
    4. SpreadPredictor       — Predice spread futuro para timing optimo
    5. TradeIntensityModel   — Hawkes bidireccional (compras vs ventas separadas)
    6. VWAPEngine            — Execution algo para ordenes grandes
    7. ExecutionAnalytics    — Post-trade analysis (implementation shortfall)

Filosofia:
    En trading institucional, la decision limit vs market NO es binaria.
    Es un problema de optimizacion: minimizar el costo total de ejecucion
    (slippage + market impact + opportunity cost de no fill).

    costo_market = slippage_inmediato + market_impact
    costo_limit  = (1 - P(fill)) * opportunity_cost + P(fill) * price_improvement
    decision = argmin(costo_market, costo_limit)
"""
from __future__ import annotations
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ======================================================================
# 1. FILL PROBABILITY MODEL
# ======================================================================

@dataclass
class FillProbResult:
    """Resultado del modelo de probabilidad de fill."""
    fill_prob: float = 0.0          # P(fill) en el horizonte dado
    expected_wait_sec: float = 0.0  # Tiempo esperado hasta fill
    distance_to_mid_bps: float = 0.0  # Distancia al mid en bps
    queue_ahead_usd: float = 0.0    # Capital estimado delante en cola
    confidence: float = 0.0         # Confianza del estimado (0-1)


class FillProbabilityModel:
    """
    Modelo empirico de probabilidad de fill para ordenes limit.

    P(fill) depende de:
        1. Distancia al mid price (en bps) — mas lejos = menor prob
        2. Volatilidad (ATR) — mayor vol = mas prob de que precio llegue
        3. Profundidad del book — mas depth en tu nivel = menor prob
        4. Trade intensity — mas actividad = mas prob de fill
        5. Horizonte temporal — mas tiempo = mas prob

    El modelo usa una funcion logistica calibrada:
        P(fill) = sigmoid(a*distance + b*vol + c*depth + d*intensity + e*time)

    Los coeficientes se calibran con datos empiricos.
    Sin datos, usa un modelo conservador basado en principios fisicos.
    """

    def __init__(self) -> None:
        # Historial de fills para calibracion empirica
        self._fill_records: deque = deque(maxlen=5000)
        self._cancel_records: deque = deque(maxlen=5000)

    def estimate(
        self,
        distance_bps: float,
        atr_bps: float,
        book_depth_at_level_usd: float,
        trade_intensity: float,
        horizon_sec: float = 5.0,
        spread_bps: float = 0.0,
    ) -> FillProbResult:
        """Estima probabilidad de fill para una orden limit.

        Args:
            distance_bps: Distancia del precio limit al mid (en bps, positivo = mejor precio)
            atr_bps: ATR en bps (volatilidad)
            book_depth_at_level_usd: Profundidad USD en el nivel de precio
            trade_intensity: Trades por segundo recientes
            horizon_sec: Horizonte temporal en segundos
            spread_bps: Spread actual del book en bps
        """
        if atr_bps <= 0:
            atr_bps = 1.0  # Prevent division by zero

        # ── Componente 1: Distancia ──────────────────────────────────
        # Dentro del spread = alta prob; fuera del spread = baja
        if spread_bps > 0:
            normalized_distance = distance_bps / spread_bps
        else:
            normalized_distance = distance_bps / max(atr_bps * 0.1, 1.0)

        # Sigmoid: prob decrece con la distancia
        # A 0 bps del mid = ~70%, a 1 spread = ~50%, a 2 spreads = ~25%
        distance_factor = 1.0 / (1.0 + math.exp(1.5 * normalized_distance - 0.5))

        # ── Componente 2: Volatilidad favorece fills ─────────────────
        # Mayor vol = precio se mueve mas = mas prob de tocar nuestro nivel
        vol_factor = min(atr_bps / 20.0, 1.5)  # Normalizar: 20bps ATR = factor 1.0

        # ── Componente 3: Queue depth reduce probabilidad ────────────
        # Mas gente delante = menos prob de fill
        depth_penalty = 1.0
        if book_depth_at_level_usd > 0:
            # Penalizar logaritmicamente: $10k depth = factor 0.7, $100k = 0.4
            depth_penalty = 1.0 / (1.0 + math.log1p(book_depth_at_level_usd / 5000.0) * 0.3)

        # ── Componente 4: Trade intensity favorece fills ─────────────
        # Mas trades/sec = mas prob de que alguno llegue a nuestro nivel
        intensity_factor = min(1.0 + trade_intensity * 0.1, 2.0)

        # ── Componente 5: Horizonte temporal ─────────────────────────
        # Mas tiempo = mas prob (raiz cuadrada: sqrt scaling como brownian motion)
        time_factor = min(math.sqrt(horizon_sec / 5.0), 3.0)

        # ── Combinar ────────────────────────────────────────────────
        raw_prob = distance_factor * vol_factor * depth_penalty * intensity_factor * time_factor

        # Clamp a [0, 0.95] — nunca 100% seguro
        fill_prob = max(0.01, min(0.95, raw_prob))

        # Tiempo esperado de fill (inverso de probabilidad escalado)
        if fill_prob > 0.05:
            expected_wait = horizon_sec * (1.0 / fill_prob - 1.0)
        else:
            expected_wait = float("inf")

        # Confianza basada en datos disponibles
        n_records = len(self._fill_records) + len(self._cancel_records)
        confidence = min(n_records / 100.0, 1.0)

        return FillProbResult(
            fill_prob=fill_prob,
            expected_wait_sec=min(expected_wait, 300.0),
            distance_to_mid_bps=distance_bps,
            queue_ahead_usd=book_depth_at_level_usd,
            confidence=confidence,
        )

    def record_fill(self, distance_bps: float, wait_sec: float, filled: bool) -> None:
        """Registra outcome de una orden para calibracion futura."""
        record = {
            "distance_bps": distance_bps,
            "wait_sec": wait_sec,
            "timestamp": time.time(),
        }
        if filled:
            self._fill_records.append(record)
        else:
            self._cancel_records.append(record)

    def get_empirical_fill_rate(self, distance_bps_range: Tuple[float, float] = (0, 5)) -> float:
        """Retorna fill rate empirico para un rango de distancia."""
        fills = sum(1 for r in self._fill_records
                    if distance_bps_range[0] <= r["distance_bps"] <= distance_bps_range[1])
        cancels = sum(1 for r in self._cancel_records
                      if distance_bps_range[0] <= r["distance_bps"] <= distance_bps_range[1])
        total = fills + cancels
        return fills / total if total > 0 else 0.5


# ======================================================================
# 2. QUEUE POSITION MODEL
# ======================================================================

@dataclass
class QueueResult:
    """Resultado del modelo de cola."""
    estimated_position: int = 0       # Posicion estimada en la cola
    queue_depth_usd: float = 0.0     # USD total delante
    time_to_front_sec: float = 0.0   # Tiempo estimado para llegar al frente
    queue_utilization: float = 0.0   # Que tan llena esta la cola (0-1)


class QueuePositionModel:
    """
    Estima la posicion en la cola para ordenes limit.

    En un CLOB (Central Limit Order Book), las ordenes se ejecutan
    en orden price-time priority. Si colocas una orden a un precio
    donde ya hay $50k, necesitas que se ejecuten esos $50k antes
    de que te toque.

    El modelo estima:
        1. Cuanto capital hay delante de ti en la cola
        2. A que velocidad se consume la cola (trades/sec * avg_size)
        3. Cuanto tiempo esperarias para fill

    Esto es critico para decidir: ¿vale la pena esperar en la cola
    o es mejor pagar el spread y usar market order?
    """

    def __init__(self) -> None:
        # Tracking de velocidad de consumo de cola
        self._consume_rates: Dict[str, deque] = {}  # symbol -> deque of (timestamp, consumed_usd)

    def estimate(
        self,
        price_level_depth_usd: float,
        trade_rate_per_sec: float,
        avg_trade_size_usd: float,
        our_order_usd: float,
    ) -> QueueResult:
        """Estima posicion en cola y tiempo de fill.

        Args:
            price_level_depth_usd: USD total en el nivel de precio
            trade_rate_per_sec: Trades por segundo en el mercado
            avg_trade_size_usd: Tamano promedio de trade en USD
            our_order_usd: Tamano de nuestra orden en USD
        """
        # Asumimos que entramos al final de la cola
        queue_ahead = price_level_depth_usd

        # Tasa de consumo: trades/sec * avg_size = USD/sec que se ejecutan
        consume_rate = trade_rate_per_sec * avg_trade_size_usd
        if consume_rate <= 0:
            consume_rate = 1.0  # Minimo 1 USD/sec

        # Tiempo estimado para que la cola delante se agote
        # Solo ~50% de trades van al lado que nos interesa
        effective_rate = consume_rate * 0.5
        time_to_front = queue_ahead / effective_rate if effective_rate > 0 else 300.0

        # Posicion estimada (en numero de ordenes, asumiendo avg_size)
        avg_order = avg_trade_size_usd if avg_trade_size_usd > 0 else 100.0
        estimated_position = int(queue_ahead / avg_order)

        # Queue utilization: que tan llena esta vs capacidad tipica
        # Un nivel con $100k+ es "lleno", $1k es "vacio"
        queue_utilization = min(price_level_depth_usd / 50_000.0, 1.0)

        return QueueResult(
            estimated_position=estimated_position,
            queue_depth_usd=queue_ahead,
            time_to_front_sec=min(time_to_front, 600.0),
            queue_utilization=queue_utilization,
        )

    def record_consume(self, symbol: str, consumed_usd: float) -> None:
        """Registra consumo de cola para calibracion."""
        if symbol not in self._consume_rates:
            self._consume_rates[symbol] = deque(maxlen=1000)
        self._consume_rates[symbol].append((time.time(), consumed_usd))


# ======================================================================
# 3. SMART ORDER ROUTER — Decision limit vs market
# ======================================================================

@dataclass
class RoutingDecision:
    """Resultado de la decision de routing."""
    order_type: str = "LIMIT"         # "LIMIT" o "MARKET"
    limit_price: float = 0.0         # Precio limit optimo (si LIMIT)
    time_in_force: str = "IOC"       # GTC, IOC, FOK
    expected_cost_bps: float = 0.0   # Costo esperado total en bps
    market_cost_bps: float = 0.0     # Costo de market order
    limit_cost_bps: float = 0.0      # Costo esperado de limit order
    fill_probability: float = 0.0    # P(fill) si limit
    reason: str = ""                 # Explicacion de la decision
    use_twap: bool = False           # True si conviene split via TWAP
    twap_slices: int = 1             # Numero de slices si TWAP


class SmartOrderRouter:
    """
    Motor de decision de routing basado en minimizacion de costos.

    Para cada senal, compara:
        costo_market = spread/2 + market_impact + slippage
        costo_limit  = (1-P(fill)) * opportunity_cost + spread_improvement
        decision = argmin(costo_market, costo_limit)

    Factores adicionales:
        - Urgencia (exits siempre market)
        - Size vs depth (ordenes grandes → TWAP)
        - Spread actual (spread < 2 bps → market; spread > 10 bps → limit)
        - Signal strength (strength > 0.9 → urgente → market)
    """

    def __init__(
        self,
        fill_model: Optional[FillProbabilityModel] = None,
        queue_model: Optional[QueuePositionModel] = None,
        opportunity_cost_bps: float = 5.0,
        twap_threshold_usd: float = 10_000.0,
    ) -> None:
        self.fill_model = fill_model or FillProbabilityModel()
        self.queue_model = queue_model or QueuePositionModel()
        self.opportunity_cost_bps = opportunity_cost_bps
        self.twap_threshold_usd = twap_threshold_usd

    def route(
        self,
        side: str,
        price: float,
        size_usd: float,
        spread_bps: float,
        atr_bps: float,
        book_depth_usd: float,
        trade_intensity: float,
        signal_strength: float = 0.5,
        is_exit: bool = False,
        is_mm: bool = False,
        maker_fee_bps: float = 2.0,
        taker_fee_bps: float = 5.0,
        microprice: float = 0.0,
        mid_price: float = 0.0,
        horizon_sec: float = 5.0,
        kyle_lambda_bps: float = 0.0,
    ) -> RoutingDecision:
        """Decide como ejecutar una orden.

        Args:
            side: "BUY" o "SELL"
            price: Precio de referencia (mid o microprice)
            size_usd: Tamano de la orden en USD
            spread_bps: Spread actual del book en bps
            atr_bps: Volatilidad ATR en bps
            book_depth_usd: Profundidad del book en el lado relevante
            trade_intensity: Trades por segundo
            signal_strength: Fuerza de la senal (0-1)
            is_exit: True si es salida de posicion (urgente)
            is_mm: True si es market making (siempre limit post_only)
            maker_fee_bps: Fee de maker en bps
            taker_fee_bps: Fee de taker en bps
            microprice: Microprice calculado (si disponible)
            mid_price: Mid price del book
            horizon_sec: Horizonte para fill probability
        """
        ref_price = microprice if microprice > 0 else (mid_price if mid_price > 0 else price)

        # ── Casos especiales (no necesitan modelo de costos) ─────────
        # Market Making: siempre LIMIT + post_only (capturar maker fee)
        if is_mm:
            return RoutingDecision(
                order_type="LIMIT",
                limit_price=price,
                time_in_force="GTC",
                expected_cost_bps=-maker_fee_bps,  # Ganamos maker rebate
                fill_probability=0.5,
                reason="mm_always_limit_postonly",
            )

        # Exits urgentes: siempre MARKET (prioridad = velocidad)
        if is_exit:
            market_cost = spread_bps / 2 + taker_fee_bps
            return RoutingDecision(
                order_type="MARKET",
                expected_cost_bps=market_cost,
                market_cost_bps=market_cost,
                fill_probability=1.0,
                reason="exit_urgent_market",
            )

        # ── Costo de MARKET order ────────────────────────────────────
        # Slippage base = half spread + size impact + taker fee
        half_spread = spread_bps / 2
        # Size impact: proporcional al ratio size/depth
        size_impact = 0.0
        if book_depth_usd > 0:
            size_ratio = min(size_usd / book_depth_usd, 2.0)
            size_impact = half_spread * size_ratio * 0.5

        market_cost = half_spread + size_impact + taker_fee_bps

        # Kyle Lambda penalty: market orders pay permanent impact
        if kyle_lambda_bps > 0 and book_depth_usd > 0 and size_usd > 0:
            size_ratio = size_usd / book_depth_usd
            lambda_impact = kyle_lambda_bps * math.sqrt(min(size_ratio, 4.0))
            market_cost += lambda_impact

        # ── Costo de LIMIT order ─────────────────────────────────────
        # Fill probability para limit dentro del spread
        # Colocar limit a ~1/3 del spread dentro → price improvement
        limit_distance_bps = max(spread_bps * 0.3, 0.5)

        fill_result = self.fill_model.estimate(
            distance_bps=limit_distance_bps,
            atr_bps=atr_bps,
            book_depth_at_level_usd=book_depth_usd * 0.1,  # Solo nuestro nivel
            trade_intensity=trade_intensity,
            horizon_sec=horizon_sec,
            spread_bps=spread_bps,
        )

        # Costo esperado de limit:
        #   Si fill: ganamos price_improvement + pagamos maker_fee (menor)
        #   Si no fill: pagamos opportunity_cost (precio se mueve sin nosotros)
        price_improvement = limit_distance_bps
        limit_cost_if_fill = maker_fee_bps - price_improvement
        limit_cost_if_no_fill = self.opportunity_cost_bps

        expected_limit_cost = (
            fill_result.fill_prob * limit_cost_if_fill
            + (1 - fill_result.fill_prob) * limit_cost_if_no_fill
        )

        # ── Signal strength adjustment ───────────────────────────────
        # Senales muy fuertes (>0.9): opportunity cost es ALTO (no queremos perder la senal)
        if signal_strength > 0.85:
            expected_limit_cost += (signal_strength - 0.85) * 20  # Penalidad fuerte

        # ── Decision ────────────────────────────────────────────────
        use_market = market_cost <= expected_limit_cost

        # ── TWAP check ──────────────────────────────────────────────
        use_twap = False
        twap_slices = 1
        if size_usd > self.twap_threshold_usd and not is_exit:
            # Ordenes grandes se benefician de split temporal
            use_twap = True
            twap_slices = min(max(int(size_usd / self.twap_threshold_usd) + 1, 2), 10)

        # ── Calcular limit price optimo ─────────────────────────────
        limit_price = 0.0
        if not use_market:
            if side == "BUY":
                # Comprar: colocar por debajo del mid para price improvement
                limit_price = ref_price - limit_distance_bps * ref_price / 10_000
            else:
                # Vender: colocar por encima del mid
                limit_price = ref_price + limit_distance_bps * ref_price / 10_000

        # Override: spread muy tight (<3 bps) → market es casi gratis
        if spread_bps < 3.0 and not use_market:
            use_market = True
            reason = "spread_tight_market"
        else:
            reason = "cost_model_market" if use_market else "cost_model_limit"

        return RoutingDecision(
            order_type="MARKET" if use_market else "LIMIT",
            limit_price=limit_price,
            time_in_force="IOC" if use_market else "GTC",
            expected_cost_bps=market_cost if use_market else expected_limit_cost,
            market_cost_bps=market_cost,
            limit_cost_bps=expected_limit_cost,
            fill_probability=1.0 if use_market else fill_result.fill_prob,
            reason=reason,
            use_twap=use_twap,
            twap_slices=twap_slices,
        )


# ======================================================================
# 4. SPREAD PREDICTOR — Predice spread futuro
# ======================================================================

@dataclass
class SpreadPrediction:
    """Prediccion de spread futuro."""
    predicted_spread_bps: float = 0.0  # Spread predicho
    current_spread_bps: float = 0.0   # Spread actual
    direction: str = "stable"          # "widening", "tightening", "stable"
    confidence: float = 0.0


class SpreadPredictor:
    """
    Predice el spread futuro basado en features del mercado.

    Features:
        1. Spread historico (EMA)
        2. Volatilidad (ATR) — alta vol → spreads anchos
        3. Trade intensity — alta actividad → spreads pueden tighten o widen
        4. VPIN — flujo toxico → spreads se ensanchan
        5. Order book imbalance — imbalance alto → spreads se ensanchan
        6. Hora del dia (si disponible) — spreads tipicamente menores en horas pico

    Modelo: regresion lineal simple con features normalizadas.
    Sin ML pesado — los features son lo suficientemente predictivos.
    """

    def __init__(self, lookback: int = 100) -> None:
        self.lookback = lookback
        self._spread_history: deque = deque(maxlen=lookback * 3)
        self._ema_spread: float = 0.0
        self._ema_alpha: float = 0.1

    def on_spread(self, spread_bps: float) -> None:
        """Registra spread observado."""
        self._spread_history.append(spread_bps)
        if self._ema_spread > 0:
            self._ema_spread = self._ema_alpha * spread_bps + (1 - self._ema_alpha) * self._ema_spread
        else:
            self._ema_spread = spread_bps

    def predict(
        self,
        current_spread_bps: float,
        atr_bps: float = 0.0,
        vpin: float = 0.0,
        hawkes_ratio: float = 1.0,
        obi_abs: float = 0.0,
    ) -> SpreadPrediction:
        """Predice spread en el proximo intervalo.

        Args:
            current_spread_bps: Spread actual
            atr_bps: Volatilidad en bps
            vpin: VPIN actual (0-1)
            hawkes_ratio: Ratio de Hawkes (>1 = actividad elevada)
            obi_abs: Valor absoluto del OBI (0-1)
        """
        if len(self._spread_history) < 5:
            return SpreadPrediction(
                predicted_spread_bps=current_spread_bps,
                current_spread_bps=current_spread_bps,
            )

        # Baseline: EMA del spread
        baseline = self._ema_spread if self._ema_spread > 0 else current_spread_bps

        # Factores de ajuste
        # Volatilidad: alta vol → spread se ensancha
        vol_factor = 1.0
        if atr_bps > 0:
            # Normalizar ATR: 20bps = normal (1.0), 40bps = wide (1.5)
            vol_factor = 0.5 + min(atr_bps / 20.0, 2.0) * 0.5

        # VPIN: flujo toxico → spread sube
        vpin_factor = 1.0 + vpin * 0.5  # VPIN=0.8 → 1.4x spread

        # Hawkes: actividad anomala → spread puede irse a ambos lados
        hawkes_factor = 1.0
        if hawkes_ratio > 1.5:
            hawkes_factor = 1.0 + (hawkes_ratio - 1.0) * 0.15

        # OBI: imbalance alto → spread se ensancha (mas riesgo para MM)
        obi_factor = 1.0 + obi_abs * 0.3

        # Prediccion
        predicted = baseline * vol_factor * vpin_factor * hawkes_factor * obi_factor

        # Mean reversion del spread: si actual >> predicho, el spread tiende a bajar
        # Si actual << predicho, tiende a subir
        mean_reversion = 0.3  # Velocidad de reversion
        predicted = predicted * (1 - mean_reversion) + current_spread_bps * mean_reversion

        # Direccion
        if predicted > current_spread_bps * 1.1:
            direction = "widening"
        elif predicted < current_spread_bps * 0.9:
            direction = "tightening"
        else:
            direction = "stable"

        confidence = min(len(self._spread_history) / 50.0, 1.0)

        return SpreadPrediction(
            predicted_spread_bps=predicted,
            current_spread_bps=current_spread_bps,
            direction=direction,
            confidence=confidence,
        )


# ======================================================================
# 5. TRADE INTENSITY MODEL — Hawkes bidireccional
# ======================================================================

@dataclass
class IntensityResult:
    """Resultado del modelo de intensidad bidireccional."""
    buy_intensity: float = 0.0       # Intensidad de compras (eventos/sec)
    sell_intensity: float = 0.0      # Intensidad de ventas (eventos/sec)
    net_intensity: float = 0.0       # buy - sell (positivo = mas compras)
    total_intensity: float = 0.0     # buy + sell
    buy_ratio: float = 0.5          # buy / total
    is_buy_dominant: bool = False
    is_sell_dominant: bool = False
    dominance_ratio: float = 1.0     # max/min de las dos intensidades


class TradeIntensityModel:
    """
    Modelo de intensidad de trades bidireccional.

    A diferencia del Hawkes unidireccional (que suma todos los trades),
    este modelo separa buy vs sell trades para entender la presion
    direccional del flujo de ordenes.

    Usa EMA exponencial para suavizar (mas rapido que Hawkes completo).

    Esto alimenta:
        - Microprice ajustado (intensity_buy vs sell)
        - Fill probability (mas trades = mas fills)
        - Spread predictor (imbalance de intensity → spread change)
    """

    def __init__(
        self,
        ema_half_life_sec: float = 30.0,
        window_sec: float = 60.0,
    ) -> None:
        self.decay = math.log(2) / max(ema_half_life_sec, 1.0)
        self.window_sec = window_sec

        self._buy_events: deque = deque(maxlen=5000)
        self._sell_events: deque = deque(maxlen=5000)
        self._last_update: float = 0.0
        self._buy_ema: float = 0.0
        self._sell_ema: float = 0.0
        self._result = IntensityResult()

    def on_trade(self, timestamp: float, is_buy: bool, size_usd: float = 0.0) -> IntensityResult:
        """Registra un trade y actualiza intensidades."""
        if is_buy:
            self._buy_events.append((timestamp, size_usd))
        else:
            self._sell_events.append((timestamp, size_usd))

        # Calcular intensidad en ventana
        cutoff = timestamp - self.window_sec
        buy_count = sum(1 for t, _ in self._buy_events if t >= cutoff)
        sell_count = sum(1 for t, _ in self._sell_events if t >= cutoff)

        buy_intensity = buy_count / self.window_sec if self.window_sec > 0 else 0
        sell_intensity = sell_count / self.window_sec if self.window_sec > 0 else 0

        # EMA smoothing
        if self._last_update > 0:
            dt = timestamp - self._last_update
            alpha = 1.0 - math.exp(-self.decay * dt)
            self._buy_ema = alpha * buy_intensity + (1 - alpha) * self._buy_ema
            self._sell_ema = alpha * sell_intensity + (1 - alpha) * self._sell_ema
        else:
            self._buy_ema = buy_intensity
            self._sell_ema = sell_intensity

        self._last_update = timestamp

        total = self._buy_ema + self._sell_ema
        buy_ratio = self._buy_ema / total if total > 0 else 0.5

        # Dominance: cuando un lado tiene >60% del flujo
        dominance_ratio = max(self._buy_ema, self._sell_ema) / max(min(self._buy_ema, self._sell_ema), 0.001)

        self._result = IntensityResult(
            buy_intensity=self._buy_ema,
            sell_intensity=self._sell_ema,
            net_intensity=self._buy_ema - self._sell_ema,
            total_intensity=total,
            buy_ratio=buy_ratio,
            is_buy_dominant=buy_ratio > 0.6,
            is_sell_dominant=buy_ratio < 0.4,
            dominance_ratio=dominance_ratio,
        )
        return self._result

    @property
    def current(self) -> IntensityResult:
        return self._result


# ======================================================================
# 6. VWAP ENGINE — Volume Weighted Average Price execution
# ======================================================================

@dataclass
class VWAPSlice:
    """Un slice de una ejecucion VWAP/TWAP."""
    slice_index: int = 0
    target_size_usd: float = 0.0     # USD a ejecutar en este slice
    target_time: float = 0.0         # Timestamp objetivo
    executed: bool = False
    fill_price: float = 0.0
    fill_size_usd: float = 0.0


@dataclass
class VWAPPlan:
    """Plan completo de ejecucion VWAP."""
    slices: List[VWAPSlice] = field(default_factory=list)
    total_size_usd: float = 0.0
    interval_sec: float = 0.0
    algo_type: str = "TWAP"          # "TWAP" (uniform) o "VWAP" (volume-weighted)
    executed_size_usd: float = 0.0
    avg_fill_price: float = 0.0

    @property
    def is_complete(self) -> bool:
        return all(s.executed for s in self.slices)

    @property
    def next_slice(self) -> Optional[VWAPSlice]:
        for s in self.slices:
            if not s.executed:
                return s
        return None

    @property
    def completion_pct(self) -> float:
        if not self.slices:
            return 0.0
        return sum(1 for s in self.slices if s.executed) / len(self.slices)


class VWAPEngine:
    """
    Execution algorithm para ordenes grandes.

    TWAP (Time Weighted): divide la orden en N slices uniformes
    separados por intervalos de tiempo iguales.

    Esto minimiza market impact al no ejecutar todo de una vez.
    Para ordenes > $10k (configurable), es preferible a single fill.

    El engine genera un plan de ejecucion con slices y timestamps.
    El caller (order_engine) ejecuta cada slice cuando llega su tiempo.
    """

    def __init__(
        self,
        min_slice_usd: float = 500.0,
        max_slices: int = 20,
        default_interval_sec: float = 30.0,
    ) -> None:
        self.min_slice_usd = min_slice_usd
        self.max_slices = max_slices
        self.default_interval_sec = default_interval_sec
        self._active_plans: Dict[str, VWAPPlan] = {}

    def create_plan(
        self,
        order_key: str,
        total_size_usd: float,
        n_slices: int = 0,
        interval_sec: float = 0.0,
        start_time: float = 0.0,
    ) -> VWAPPlan:
        """Crea un plan de ejecucion TWAP.

        Args:
            order_key: Identificador unico de la orden
            total_size_usd: Tamano total en USD
            n_slices: Numero de slices (0 = auto-calcular)
            interval_sec: Intervalo entre slices (0 = default)
            start_time: Timestamp de inicio (0 = ahora)
        """
        if n_slices <= 0:
            n_slices = max(2, min(int(total_size_usd / self.min_slice_usd), self.max_slices))

        if interval_sec <= 0:
            interval_sec = self.default_interval_sec

        if start_time <= 0:
            start_time = time.time()

        slice_size = total_size_usd / n_slices

        slices = []
        for i in range(n_slices):
            slices.append(VWAPSlice(
                slice_index=i,
                target_size_usd=slice_size,
                target_time=start_time + i * interval_sec,
            ))

        plan = VWAPPlan(
            slices=slices,
            total_size_usd=total_size_usd,
            interval_sec=interval_sec,
            algo_type="TWAP",
        )

        self._active_plans[order_key] = plan
        return plan

    def mark_slice_executed(
        self, order_key: str, slice_index: int, fill_price: float, fill_size_usd: float
    ) -> None:
        """Marca un slice como ejecutado."""
        plan = self._active_plans.get(order_key)
        if plan and slice_index < len(plan.slices):
            s = plan.slices[slice_index]
            s.executed = True
            s.fill_price = fill_price
            s.fill_size_usd = fill_size_usd
            plan.executed_size_usd += fill_size_usd

            # Recalcular avg fill price
            total_value = sum(
                sl.fill_price * sl.fill_size_usd
                for sl in plan.slices if sl.executed and sl.fill_size_usd > 0
            )
            if plan.executed_size_usd > 0:
                plan.avg_fill_price = total_value / plan.executed_size_usd

    def get_due_slices(self, order_key: str, current_time: float = 0.0) -> List[VWAPSlice]:
        """Retorna slices que ya deberian ejecutarse."""
        if current_time <= 0:
            current_time = time.time()

        plan = self._active_plans.get(order_key)
        if not plan:
            return []

        return [
            s for s in plan.slices
            if not s.executed and s.target_time <= current_time
        ]

    def get_plan(self, order_key: str) -> Optional[VWAPPlan]:
        return self._active_plans.get(order_key)

    def remove_plan(self, order_key: str) -> None:
        self._active_plans.pop(order_key, None)


# ======================================================================
# 7. EXECUTION ANALYTICS — Post-trade analysis
# ======================================================================

@dataclass
class ExecutionReport:
    """Reporte de calidad de ejecucion."""
    total_trades: int = 0
    avg_slippage_bps: float = 0.0
    avg_latency_ms: float = 0.0
    market_order_pct: float = 0.0    # % de trades que fueron market orders
    limit_fill_rate: float = 0.0     # % de limit orders que se ejecutaron
    avg_price_improvement_bps: float = 0.0  # Mejora de precio vs mid
    implementation_shortfall_bps: float = 0.0  # Costo total vs decision price
    timing_cost_bps: float = 0.0     # Costo de esperar (precio movio contra)
    impact_cost_bps: float = 0.0     # Costo de impacto de mercado
    by_strategy: Dict[str, Dict] = field(default_factory=dict)
    by_order_type: Dict[str, Dict] = field(default_factory=dict)


class ExecutionAnalytics:
    """
    Analisis post-trade de calidad de ejecucion.

    Mide:
        1. Implementation Shortfall: diferencia entre precio de decision
           (cuando se genero la senal) y precio de fill real
        2. Timing cost: cuanto movio el precio entre decision y fill
        3. Impact cost: cuanto movimos el precio con nuestra orden
        4. Fill rate: que % de limit orders se ejecutaron
        5. Price improvement: cuanto mejor fue limit vs market would-have-been

    Esto permite:
        - Calibrar el fill probability model con datos reales
        - Ajustar el smart router basado en resultados
        - Detectar si el sistema esta ejecutando peor de lo esperado
    """

    def __init__(self) -> None:
        self._trades: deque = deque(maxlen=10000)

    def record_execution(
        self,
        decision_price: float,
        fill_price: float,
        mid_at_fill: float,
        order_type: str,
        strategy: str,
        size_usd: float,
        latency_ms: float = 0.0,
        was_filled: bool = True,
    ) -> None:
        """Registra una ejecucion para analisis."""
        if decision_price <= 0:
            return

        self._trades.append({
            "decision_price": decision_price,
            "fill_price": fill_price,
            "mid_at_fill": mid_at_fill,
            "order_type": order_type,
            "strategy": strategy,
            "size_usd": size_usd,
            "latency_ms": latency_ms,
            "was_filled": was_filled,
            "timestamp": time.time(),
            # Computed metrics
            "slippage_bps": abs(fill_price - decision_price) / decision_price * 10_000 if was_filled else 0,
            "shortfall_bps": (fill_price - decision_price) / decision_price * 10_000 if was_filled else 0,
        })

    def get_report(self, lookback_trades: int = 0) -> ExecutionReport:
        """Genera reporte de calidad de ejecucion."""
        trades = list(self._trades)
        if lookback_trades > 0:
            trades = trades[-lookback_trades:]

        if not trades:
            return ExecutionReport()

        filled = [t for t in trades if t["was_filled"]]
        n_filled = len(filled)

        if not filled:
            return ExecutionReport(total_trades=len(trades))

        slippages = [t["slippage_bps"] for t in filled]
        latencies = [t["latency_ms"] for t in filled if t["latency_ms"] > 0]
        shortfalls = [t["shortfall_bps"] for t in filled]

        # Market vs limit breakdown
        market_trades = [t for t in filled if t["order_type"] == "MARKET"]
        limit_trades = [t for t in trades if t["order_type"] == "LIMIT"]
        limit_filled = [t for t in limit_trades if t["was_filled"]]

        # By strategy
        by_strategy: Dict[str, Dict] = {}
        for t in filled:
            strat = t["strategy"]
            if strat not in by_strategy:
                by_strategy[strat] = {"count": 0, "slippage_sum": 0.0}
            by_strategy[strat]["count"] += 1
            by_strategy[strat]["slippage_sum"] += t["slippage_bps"]

        for k, v in by_strategy.items():
            v["avg_slippage_bps"] = v["slippage_sum"] / v["count"] if v["count"] > 0 else 0

        # By order type
        by_type: Dict[str, Dict] = {}
        for t in filled:
            ot = t["order_type"]
            if ot not in by_type:
                by_type[ot] = {"count": 0, "slippage_sum": 0.0}
            by_type[ot]["count"] += 1
            by_type[ot]["slippage_sum"] += t["slippage_bps"]

        for k, v in by_type.items():
            v["avg_slippage_bps"] = v["slippage_sum"] / v["count"] if v["count"] > 0 else 0

        return ExecutionReport(
            total_trades=len(trades),
            avg_slippage_bps=float(np.mean(slippages)) if slippages else 0.0,
            avg_latency_ms=float(np.mean(latencies)) if latencies else 0.0,
            market_order_pct=len(market_trades) / n_filled if n_filled > 0 else 0.0,
            limit_fill_rate=len(limit_filled) / len(limit_trades) if limit_trades else 0.0,
            implementation_shortfall_bps=float(np.mean(shortfalls)) if shortfalls else 0.0,
            by_strategy=by_strategy,
            by_order_type=by_type,
        )
