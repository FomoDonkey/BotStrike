"""
Indicadores avanzados de microestructura de mercado.
Módulos independientes que pueden ser consumidos por cualquier estrategia.

Contiene:
    1. VPINCalculator  — Volume-Synchronized Probability of Informed Trading
    2. HawkesEstimator — Proceso de Hawkes para detección de picos de actividad
    3. AvellanedaStoikovEngine — Motor mejorado de spread dinámico e inventario

Cada módulo:
    - Es independiente y reusable
    - Mantiene historial para backtesting
    - Se actualiza tick-a-tick o por barra
    - Emite métricas que las estrategias y el risk manager consumen
"""
from __future__ import annotations
import copy
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════════
# 1. VPIN — Volume-Synchronized Probability of Informed Trading
# ══════════════════════════════════════════════════════════════════

@dataclass
class VPINBucket:
    """Un bucket de volumen para el cálculo de VPIN."""
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    total_volume: float = 0.0


@dataclass
class VPINResult:
    """Resultado del cálculo de VPIN para un símbolo."""
    vpin: float = 0.0               # Valor VPIN actual (0 a 1)
    cdf: float = 0.0                # Percentil CDF del VPIN en su historial
    is_toxic: bool = False           # True si VPIN supera el threshold
    buy_volume_pct: float = 0.5      # % de volumen clasificado como compra
    bucket_count: int = 0            # Buckets llenos disponibles
    timestamp: float = 0.0

    @property
    def alert_level(self) -> str:
        """Nivel de alerta: low, medium, high, critical."""
        if self.vpin >= 0.8:
            return "critical"
        if self.vpin >= 0.6:
            return "high"
        if self.vpin >= 0.4:
            return "medium"
        return "low"


class VPINCalculator:
    """
    Calcula VPIN en tiempo real usando Bulk Volume Classification (BVC).

    VPIN mide la probabilidad de que traders informados estén operando.
    Un VPIN alto indica desequilibrio en el flujo de órdenes → riesgo para
    Market Making y Mean Reversion, ya que el precio puede moverse bruscamente.

    Algoritmo:
        1. Los trades se clasifican como compra/venta usando BVC
           (basado en la dirección del precio dentro de la barra)
        2. Se agrupan en buckets de volumen fijo
        3. VPIN = media(|buy_vol - sell_vol| / total_vol) sobre N buckets

    Impacto en estrategias:
        - Market Making: VPIN alto → ensanchar spreads o detener quoting
        - Mean Reversion: VPIN alto → evitar entradas (flujo informado puede
          romper la reversión)
        - Trend Following: VPIN alto puede confirmar momentum informado
    """

    def __init__(
        self,
        bucket_size: float = 1000.0,
        n_buckets: int = 50,
        toxic_threshold: float = 0.6,
    ) -> None:
        """
        Args:
            bucket_size: Volumen USD por bucket (se ajusta por activo)
            n_buckets: Número de buckets para calcular VPIN
            toxic_threshold: Umbral para considerar flujo tóxico
        """
        self.bucket_size = bucket_size
        self.n_buckets = n_buckets
        self.toxic_threshold = toxic_threshold

        # Estado interno
        self._current_bucket = VPINBucket()
        self._completed_buckets: deque = deque(maxlen=n_buckets * 3)
        self._last_price: float = 0.0
        self._vpin_history: deque = deque(maxlen=500)
        self._result = VPINResult()

    def on_trade(self, price: float, quantity: float, timestamp: float) -> VPINResult:
        """Procesa un trade y actualiza VPIN.

        Usa Bulk Volume Classification: clasifica el volumen de cada trade
        como compra o venta según la dirección del precio respecto al anterior.

        Args:
            price: Precio del trade
            quantity: Cantidad en unidades base
            timestamp: Unix timestamp en segundos
        """
        volume_usd = price * quantity

        # Clasificación BVC: si el precio sube → compra, si baja → venta
        if self._last_price > 0:
            if price > self._last_price:
                self._current_bucket.buy_volume += volume_usd
            elif price < self._last_price:
                self._current_bucket.sell_volume += volume_usd
            else:
                # Sin cambio: dividir 50/50
                self._current_bucket.buy_volume += volume_usd * 0.5
                self._current_bucket.sell_volume += volume_usd * 0.5
        else:
            self._current_bucket.buy_volume += volume_usd * 0.5
            self._current_bucket.sell_volume += volume_usd * 0.5

        self._current_bucket.total_volume += volume_usd
        self._last_price = price

        # Verificar si el bucket está lleno
        if self._current_bucket.total_volume >= self.bucket_size:
            self._completed_buckets.append(self._current_bucket)
            self._current_bucket = VPINBucket()

        # Calcular VPIN si hay suficientes buckets
        self._result = self._compute_vpin(timestamp)
        return self._result

    def on_bar(self, open_p: float, high: float, low: float, close: float,
               volume: float, timestamp: float) -> VPINResult:
        """Actualiza VPIN usando datos de barra OHLCV (para backtesting).

        Usa la fórmula BVC de barra: buy_pct = (close - low) / (high - low)
        """
        if high == low:
            buy_pct = 0.5
        else:
            buy_pct = max(0.0, min(1.0, (close - low) / (high - low)))

        volume_usd = close * volume  # aproximación
        buy_vol = volume_usd * buy_pct
        sell_vol = volume_usd * (1.0 - buy_pct)

        self._current_bucket.buy_volume += buy_vol
        self._current_bucket.sell_volume += sell_vol
        self._current_bucket.total_volume += volume_usd

        if self._current_bucket.total_volume >= self.bucket_size:
            self._completed_buckets.append(self._current_bucket)
            self._current_bucket = VPINBucket()

        self._result = self._compute_vpin(timestamp)
        return self._result

    def _compute_vpin(self, timestamp: float) -> VPINResult:
        """Calcula el VPIN actual a partir de los buckets completados."""
        n = min(len(self._completed_buckets), self.n_buckets)
        if n < 5:
            return VPINResult(timestamp=timestamp, bucket_count=n)

        recent = list(self._completed_buckets)[-n:]
        imbalances = []
        total_buy = 0.0
        total_sell = 0.0
        for b in recent:
            if b.total_volume > 0:
                imbalance = abs(b.buy_volume - b.sell_volume) / b.total_volume
                imbalances.append(imbalance)
                total_buy += b.buy_volume
                total_sell += b.sell_volume

        if not imbalances:
            return VPINResult(timestamp=timestamp, bucket_count=n)

        vpin = float(np.mean(imbalances))
        total_vol = total_buy + total_sell
        buy_pct = total_buy / total_vol if total_vol > 0 else 0.5

        # CDF: percentil del VPIN actual en su historial (método consistente)
        self._vpin_history.append(vpin)
        sorted_hist = np.sort(list(self._vpin_history))
        cdf = float(np.searchsorted(sorted_hist, vpin, side='right') / len(self._vpin_history))

        return VPINResult(
            vpin=vpin,
            cdf=cdf,
            is_toxic=vpin >= self.toxic_threshold,
            buy_volume_pct=buy_pct,
            bucket_count=n,
            timestamp=timestamp,
        )

    @property
    def current(self) -> VPINResult:
        return self._result

    @property
    def history(self) -> List[float]:
        return list(self._vpin_history)

    def reset(self) -> None:
        self._current_bucket = VPINBucket()
        self._completed_buckets.clear()
        self._vpin_history.clear()
        self._last_price = 0.0
        self._result = VPINResult()


# ══════════════════════════════════════════════════════════════════
# 2. HAWKES PROCESS — Detección de picos de actividad
# ══════════════════════════════════════════════════════════════════

@dataclass
class HawkesResult:
    """Resultado del estimador de intensidad de Hawkes."""
    intensity: float = 0.0           # Intensidad actual (eventos/segundo)
    baseline: float = 0.0            # Intensidad base (mu)
    excitation: float = 0.0          # Componente de auto-excitación
    is_spike: bool = False           # True si intensidad > threshold
    spike_ratio: float = 0.0         # ratio vs baseline (>1 = por encima)
    event_count_1m: int = 0          # Eventos en último minuto
    timestamp: float = 0.0

    @property
    def alert_level(self) -> str:
        if self.spike_ratio >= 4.0:
            return "critical"
        if self.spike_ratio >= 2.5:
            return "high"
        if self.spike_ratio >= 1.5:
            return "medium"
        return "low"


class HawkesEstimator:
    """
    Estimador de intensidad de Hawkes para flujo de órdenes.

    Un proceso de Hawkes modela la auto-excitación en eventos:
    λ(t) = μ + Σ α * exp(-β * (t - t_i))

    Donde:
        μ = intensidad base (tasa de llegada de eventos normal)
        α = factor de excitación (cuánto sube la intensidad por evento)
        β = tasa de decaimiento (qué tan rápido vuelve a la base)

    Uso en trading:
        - Detecta picos inminentes de actividad (clusters de trades/cancelaciones)
        - Market Making: si la intensidad es alta, el spread debería ensancharse
          o el quoting pausarse (riesgo de adverse selection)
        - Mean Reversion: evitar entradas durante picos (precio inestable)
        - Trend Following: puede confirmar fuerza de un movimiento
    """

    def __init__(
        self,
        mu: float = 1.0,
        alpha: float = 0.5,
        beta: float = 2.0,
        spike_threshold_mult: float = 2.5,
        window_sec: float = 300.0,
    ) -> None:
        """
        Args:
            mu: Intensidad base (eventos/segundo)
            alpha: Factor de excitación (debe ser < beta para estabilidad)
            beta: Tasa de decaimiento
            spike_threshold_mult: Multiplicador sobre mu para declarar spike
            window_sec: Ventana para contar eventos y adaptar mu
        """
        # Validar estabilidad: alpha/beta < 1 (proceso subcrítico)
        if alpha >= beta:
            raise ValueError(
                f"Hawkes stability violated: alpha={alpha} must be < beta={beta} "
                f"(branching ratio alpha/beta={alpha/beta:.2f} >= 1, supercritical)"
            )

        self.mu = mu
        self.alpha = alpha
        self.beta = beta
        self.spike_threshold_mult = spike_threshold_mult
        self.window_sec = window_sec

        # Estado
        self._event_times: deque = deque(maxlen=10000)
        self._intensity_history: deque = deque(maxlen=500)
        self._result = HawkesResult()
        self._adaptive_mu: float = mu
        # Analytical kernel state for O(1) intensity computation
        self._cached_excitation: float = 0.0
        self._cached_excitation_time: float = 0.0

    def on_event(self, timestamp: float, event_type: str = "trade") -> HawkesResult:
        """Registra un evento y recalcula la intensidad de Hawkes.

        Args:
            timestamp: Unix timestamp del evento en segundos
            event_type: Tipo de evento ("trade", "cancel", "fill")
        """
        self._event_times.append(timestamp)

        # Calcular intensidad de Hawkes con kernel analítico: O(1) por evento
        # excitation(t) = old * exp(-beta * dt) + alpha
        dt = timestamp - self._cached_excitation_time
        if dt > 0:
            self._cached_excitation = self._cached_excitation * math.exp(-self.beta * dt) + self.alpha
        else:
            self._cached_excitation += self.alpha
        self._cached_excitation_time = timestamp
        excitation = self._cached_excitation

        # Adaptar mu basándose en la ventana histórica
        cutoff = timestamp - self.window_sec
        # Limpiar eventos antiguos del deque (mantener solo ventana)
        while self._event_times and self._event_times[0] < cutoff:
            self._event_times.popleft()
        events_in_window = len(self._event_times)
        if events_in_window > 10:
            self._adaptive_mu = events_in_window / self.window_sec

        # Baseline: blend adaptive (80%) with config mu as floor
        # This adapts to BTC's actual activity level without absorbing current spike
        baseline = max(self.mu, self._adaptive_mu * 0.8)
        intensity = baseline + excitation
        spike_threshold = baseline * self.spike_threshold_mult
        is_spike = intensity > spike_threshold
        spike_ratio = intensity / baseline if baseline > 0 else 1.0

        # Eventos en último minuto (O(1): total - elementos antes de 1min ago)
        one_min_ago = timestamp - 60.0
        # Deque is sorted (appended in order), count from end
        events_1m = 0
        for t in reversed(self._event_times):
            if t >= one_min_ago:
                events_1m += 1
            else:
                break

        self._result = HawkesResult(
            intensity=intensity,
            baseline=self._adaptive_mu,
            excitation=excitation,
            is_spike=is_spike,
            spike_ratio=spike_ratio,
            event_count_1m=events_1m,
            timestamp=timestamp,
        )
        self._intensity_history.append(intensity)
        return self._result

    def get_intensity_at(self, timestamp: float) -> float:
        """Calcula la intensidad en un momento dado sin registrar evento."""
        if hasattr(self, '_cached_excitation') and self._cached_excitation_time > 0:
            dt = timestamp - self._cached_excitation_time
            excitation = self._cached_excitation * math.exp(-self.beta * max(dt, 0))
        else:
            excitation = 0.0
        return self.mu + excitation

    @property
    def current(self) -> HawkesResult:
        return self._result

    @property
    def intensity_history(self) -> List[float]:
        return list(self._intensity_history)

    def reset(self) -> None:
        self._event_times.clear()
        self._intensity_history.clear()
        self._result = HawkesResult()
        self._adaptive_mu = self.mu
        self._cached_excitation = 0.0
        self._cached_excitation_time = 0.0


# ══════════════════════════════════════════════════════════════════
# 3. AVELLANEDA-STOIKOV ENGINE — Motor mejorado de spread dinámico
# ══════════════════════════════════════════════════════════════════

@dataclass
class ASResult:
    """Resultado del motor Avellaneda-Stoikov mejorado."""
    reservation_price: float = 0.0   # Precio de reserva ajustado
    optimal_spread: float = 0.0      # Spread óptimo en USD
    bid_price: float = 0.0           # Precio bid calculado
    ask_price: float = 0.0           # Precio ask calculado
    spread_bps: float = 0.0          # Spread en basis points
    inventory_skew: float = 0.0      # Skew por inventario
    sigma: float = 0.0               # Volatilidad usada
    effective_gamma: float = 0.0     # Gamma efectivo (ajustado por VPIN/Hawkes)
    time_horizon: float = 0.0        # T-t restante
    timestamp: float = 0.0

    @property
    def spread_quality(self) -> str:
        """Calidad del spread: tight, normal, wide, defensive."""
        if self.spread_bps <= 5:
            return "tight"
        if self.spread_bps <= 15:
            return "normal"
        if self.spread_bps <= 40:
            return "wide"
        return "defensive"


class AvellanedaStoikovEngine:
    """
    Motor mejorado de Avellaneda-Stoikov para Market Making.

    Mejoras sobre la implementación básica:
        1. Horizonte temporal adaptativo (no fijo en T=1)
        2. Gamma se ajusta dinámicamente según VPIN y Hawkes
        3. Sigma estimada con ventana adaptativa al régimen
        4. Kappa estimada desde el flujo real de órdenes
        5. Skew de inventario proporcional a ATR y con decay exponencial
        6. Min/max spread bounds con ajuste por fees
        7. Inventory half-life: penaliza inventario que no se rebalancea
        8. Asymmetric gamma: gamma mayor contra tendencia del régimen

    Impacto:
        - Si VPIN es alto → gamma se incrementa → spread más ancho
        - Si Hawkes spike → gamma se incrementa → spread más ancho o pausa
        - Si inventario alto → skew agresivo para rebalancear
        - Si inventario envejecido → penalty creciente para forzar liquidacion
    """

    def __init__(
        self,
        gamma: float = 0.1,
        kappa: float = 1.5,
        min_spread_bps: float = 3.0,
        max_spread_bps: float = 100.0,
        fee_bps: float = 2.0,
        inventory_half_life_sec: float = 120.0,
    ) -> None:
        """
        Args:
            gamma: Aversión al riesgo base
            kappa: Intensidad de llegada de órdenes base
            min_spread_bps: Spread mínimo en basis points (debe cubrir fees)
            max_spread_bps: Spread máximo (defensivo)
            fee_bps: Fee medio para asegurar que spread > 2*fee
            inventory_half_life_sec: Tiempo para que la penalizacion de inventario se duplique
        """
        self.base_gamma = gamma
        self.base_kappa = kappa
        self.min_spread_bps = max(min_spread_bps, fee_bps * 2)
        self.max_spread_bps = max_spread_bps
        self.fee_bps = fee_bps
        self.inventory_half_life_sec = inventory_half_life_sec

        # Estado
        self._result = ASResult()
        # Inventory age tracking: cuando el inventario cambio de signo por ultima vez
        self._inventory_sign_change_time: float = 0.0
        self._last_inventory_sign: int = 0  # -1, 0, +1

    def compute(
        self,
        mid_price: float,
        inventory: float,
        max_inventory: float,
        sigma: float,
        atr: float,
        time_remaining: float = 0.5,
        vpin: Optional[VPINResult] = None,
        hawkes: Optional[HawkesResult] = None,
        kyle_lambda: Optional[KyleLambdaResult] = None,
        timestamp: float = 0.0,
    ) -> ASResult:
        """Calcula precios óptimos de bid/ask.

        Args:
            mid_price: Precio mid del orderbook
            inventory: Inventario actual (positivo=long, negativo=short)
            max_inventory: Inventario máximo permitido
            sigma: Volatilidad estimada (retornos std)
            atr: Average True Range actual
            time_remaining: Fracción del horizonte temporal restante (0 a 1)
            vpin: Resultado VPIN actual (para ajustar gamma)
            hawkes: Resultado Hawkes actual (para ajustar gamma)
            timestamp: Timestamp actual
        """
        if mid_price <= 0 or sigma <= 0:
            return ASResult(timestamp=timestamp)

        T_t = max(time_remaining, 0.01)

        # ── Ajuste dinámico de gamma ──────────────────────────────
        # gamma sube con VPIN alto (más riesgo de adverse selection)
        # gamma sube con Hawkes spike (actividad anómala)
        effective_gamma = self.base_gamma

        if vpin and vpin.vpin > 0:
            # Escalar gamma: x1 en VPIN=0, x3 en VPIN=0.8+
            vpin_multiplier = 1.0 + 2.0 * min(vpin.vpin / 0.8, 1.0)
            effective_gamma *= vpin_multiplier

        if hawkes and hawkes.spike_ratio > 1.0:
            # Escalar gamma según ratio de spike
            hawkes_multiplier = 1.0 + 0.5 * min(hawkes.spike_ratio - 1.0, 3.0)
            effective_gamma *= hawkes_multiplier

        # Kyle Lambda: escalar gamma por liquidez implícita del mercado
        # Lambda alto → mercado ilíquido → gamma sube (más defensivo)
        if kyle_lambda and kyle_lambda.is_valid and kyle_lambda.kyle_lambda_ema > 0:
            # Normalizar: lambda_ema ~0.5 bps/$ = neutral, >1.5 = stress
            lambda_mult = 1.0 + min(kyle_lambda.impact_stress, 1.0) * 0.5
            effective_gamma *= lambda_mult

        # Cap gamma para evitar spreads absurdos (max ~5x base → defensivo pero realista)
        effective_gamma = min(effective_gamma, self.base_gamma * 5.0)

        # ── Kappa adaptativo ──────────────────────────────────────
        effective_kappa = self.base_kappa
        if hawkes and hawkes.baseline > 0:
            # Si hay más actividad, kappa sube (más órdenes por llenar)
            effective_kappa = max(self.base_kappa, hawkes.baseline * 0.5)

        # ── Gamma multiplier (para spread dinámico) ───────────────
        gamma_mult = effective_gamma / self.base_gamma  # 1.0 normal, hasta ~7.5x

        # ── Reservation Price (ATR-based para impacto real) ───────
        # El sigma^2 term del A-S clásico es despreciable en returns space.
        # Usamos ATR como proxy de volatilidad en unidades de precio,
        # lo que produce ajustes de inventario significativos.
        reservation_price = mid_price - inventory * atr * effective_gamma * T_t

        # ── Optimal Spread ────────────────────────────────────────
        # Base: ATR como porcentaje del precio (volatilidad real en bps)
        atr_bps = (atr / mid_price) * 10_000 if mid_price > 0 else 0

        # Spread = ATR_bps * gamma_efectivo * factor_kappa * T
        # gamma sube con VPIN/Hawkes → spread se ensancha (proteccion)
        # kappa sube con liquidez → spread se comprime
        kappa_factor = 1.0 / (0.5 + effective_kappa * 0.333)
        optimal_spread_bps = atr_bps * effective_gamma * kappa_factor * (0.5 + T_t)

        # ── Spread bounds (mínimo dinámico por microestructura) ───
        # El min_spread sube con gamma para que VPIN/Hawkes SIEMPRE afecten
        dynamic_min_bps = self.min_spread_bps * max(1.0, gamma_mult * 0.6)
        min_spread_bps_final = max(dynamic_min_bps, self.fee_bps * 2)
        max_spread_bps_final = self.max_spread_bps

        spread_bps_clamped = max(min_spread_bps_final, min(optimal_spread_bps, max_spread_bps_final))
        spread = spread_bps_clamped * mid_price / 10_000

        # ── Inventory age penalty ────────────────────────────────────
        # Penaliza inventario que no revierte a tiempo
        current_sign = 1 if inventory > 0 else (-1 if inventory < 0 else 0)
        ts = timestamp if timestamp > 0 else time.time()
        if current_sign != self._last_inventory_sign:
            self._inventory_sign_change_time = ts
            self._last_inventory_sign = current_sign

        inventory_age_sec = ts - self._inventory_sign_change_time
        # Time-weighted penalty: crece con la edad del inventario
        if self.inventory_half_life_sec > 0 and inventory_age_sec > 0:
            age_penalty = 1.0 + inventory_age_sec / self.inventory_half_life_sec
        else:
            age_penalty = 1.0

        # ── Inventory skew ────────────────────────────────────────
        # Proporcional a inventario y ATR, con decay no lineal
        inv_ratio = inventory / max_inventory if max_inventory > 0 else 0
        # Sigmoid-like skew: más agresivo cuanto más inventario
        # Amplificado por age_penalty: inventario viejo → skew mas agresivo
        skew_factor = math.tanh(inv_ratio * 2.0) * min(age_penalty, 3.0)
        inventory_skew = skew_factor * atr * 0.5

        # ── Precios bid/ask ───────────────────────────────────────
        bid_price = reservation_price - spread / 2.0 - inventory_skew
        ask_price = reservation_price + spread / 2.0 - inventory_skew

        spread_bps = (ask_price - bid_price) / mid_price * 10_000

        self._result = ASResult(
            reservation_price=reservation_price,
            optimal_spread=spread,
            bid_price=bid_price,
            ask_price=ask_price,
            spread_bps=spread_bps,
            inventory_skew=inventory_skew,
            sigma=sigma,
            effective_gamma=effective_gamma,
            time_horizon=T_t,
            timestamp=timestamp,
        )
        return self._result

    @property
    def current(self) -> ASResult:
        return self._result


# ══════════════════════════════════════════════════════════════════
# 4. KYLE LAMBDA — Estimación de impacto permanente de mercado
# ══════════════════════════════════════════════════════════════════

@dataclass
class KyleLambdaResult:
    """Resultado de la estimación de Kyle Lambda."""
    kyle_lambda: float = 0.0           # Lambda instantáneo (bps per $ notional)
    kyle_lambda_ema: float = 0.0       # Lambda suavizado (EMA)
    permanent_impact_bps: float = 0.0  # Impacto permanente estimado para referencia
    adverse_selection_bps: float = 0.0 # Costo de adverse selection medido
    is_valid: bool = False             # True si hay suficientes muestras
    sample_size: int = 0
    timestamp: float = 0.0

    @property
    def impact_stress(self) -> float:
        """Score de stress de impacto (0=normal, 1+=alto)."""
        if not self.is_valid or self.kyle_lambda_ema <= 0:
            return 0.0
        # Normalizar: lambda_ema de 0.5 bps/$ es normal, >2 bps/$ es stress
        return min(self.kyle_lambda_ema / 2.0, 2.0)


class KyleLambdaEstimator:
    """
    Estimador rolling de Kyle Lambda: λ = Cov(ΔP, Q) / Var(Q).

    Mide cuánto mueve $1 de volumen signed el precio permanentemente.
    Lambda alto = mercado ilíquido o dominado por informed traders.
    Lambda bajo = mercado líquido con fills baratos.

    Uso:
        - Market Making: lambda alto → ensanchar gamma (más aversión al riesgo)
        - Smart Router: lambda alto → preferir limit orders (no pagar impact)
        - Risk Manager: lambda extremo → reducir sizing (mercado frágil)
        - Slippage Model: permanent_impact = lambda * sqrt(size/depth)

    Implementación incremental: O(1) por trade usando sumas rodantes.
    """

    def __init__(
        self,
        window: int = 500,
        ema_span: int = 100,
        min_samples: int = 30,
        adverse_selection_horizon_sec: float = 300.0,
    ) -> None:
        self.window = window
        self.ema_span = ema_span
        self.min_samples = min_samples
        self.as_horizon = adverse_selection_horizon_sec

        # Rolling window data for Cov(ΔP, Q) / Var(Q)
        self._price_changes: deque = deque(maxlen=window)
        self._signed_volumes: deque = deque(maxlen=window)
        self._last_price: float = 0.0

        # EMA state
        self._ema_alpha: float = 2.0 / (ema_span + 1)
        self._lambda_ema: float = 0.0
        self._ema_initialized: bool = False

        # Adverse selection tracking: (fill_price, fill_time, side_sign)
        self._pending_fills: deque = deque(maxlen=200)
        self._as_measurements: deque = deque(maxlen=500)

        # Outlier bounds (updated rolling)
        self._lambda_history: deque = deque(maxlen=500)

        self._result = KyleLambdaResult()

    def on_trade(
        self,
        price: float,
        quantity: float,
        timestamp: float,
        is_buy: Optional[bool] = None,
    ) -> KyleLambdaResult:
        """Procesa un trade y actualiza Kyle Lambda.

        Args:
            price: Precio del trade
            quantity: Cantidad en unidades base
            timestamp: Unix timestamp en segundos
            is_buy: True=compra, False=venta, None=infer from price direction
        """
        if price <= 0 or quantity <= 0:
            return self._result

        # Clasificar dirección del trade
        if is_buy is not None:
            sign = 1.0 if is_buy else -1.0
        elif self._last_price > 0:
            if price > self._last_price:
                sign = 1.0
            elif price < self._last_price:
                sign = -1.0
            else:
                sign = 0.0
        else:
            sign = 0.0

        # Signed volume (en USD notional)
        signed_vol = sign * price * quantity

        # Price change (en bps)
        had_reference = self._last_price > 0
        if had_reference:
            dp_bps = (price - self._last_price) / self._last_price * 10_000
        else:
            dp_bps = 0.0
        self._last_price = price

        # Skip first trade only (no reference price yet)
        if not had_reference:
            return self._result

        # Agregar a ventanas rolling (trades at same price are informative: volume with no impact → reduces lambda)
        self._price_changes.append(dp_bps)
        self._signed_volumes.append(signed_vol)

        # Evaluar adverse selection de fills pendientes
        self._evaluate_adverse_selection(price, timestamp)

        # Calcular lambda si hay suficientes muestras
        n = len(self._price_changes)
        if n < self.min_samples:
            self._result = KyleLambdaResult(
                sample_size=n, timestamp=timestamp
            )
            return self._result

        # λ = Cov(ΔP, Q) / Var(Q) — incremental
        dp = np.array(self._price_changes)
        sv = np.array(self._signed_volumes)

        var_q = np.var(sv, ddof=1)
        if var_q < 1e-20:
            # Volumen casi constante → lambda indefinido
            return self._result

        cov_dp_q = np.cov(dp, sv, ddof=1)[0, 1]
        raw_lambda = cov_dp_q / var_q

        # Normalizar a bps per $1 notional
        # raw_lambda está en bps/USD, que es lo que queremos
        lambda_bps = raw_lambda

        # Outlier clipping: winsorize al percentil 99 del historial
        self._lambda_history.append(lambda_bps)
        if len(self._lambda_history) > 50:
            sorted_hist = sorted(self._lambda_history)
            p99 = sorted_hist[int(len(sorted_hist) * 0.99)]
            p01 = sorted_hist[int(len(sorted_hist) * 0.01)]
            lambda_bps = max(p01, min(p99, lambda_bps))

        # Lambda debe ser no-negativo (impacto negativo no tiene sentido económico)
        lambda_bps = max(0.0, lambda_bps)

        # EMA smoothing
        if not self._ema_initialized:
            self._lambda_ema = lambda_bps
            self._ema_initialized = True
        else:
            self._lambda_ema = (
                self._ema_alpha * lambda_bps
                + (1 - self._ema_alpha) * self._lambda_ema
            )

        # Adverse selection promedio
        as_bps = 0.0
        if self._as_measurements:
            as_bps = float(np.mean(list(self._as_measurements)))

        self._result = KyleLambdaResult(
            kyle_lambda=lambda_bps,
            kyle_lambda_ema=self._lambda_ema,
            permanent_impact_bps=self._lambda_ema,
            adverse_selection_bps=max(0.0, as_bps),
            is_valid=True,
            sample_size=n,
            timestamp=timestamp,
        )
        return self._result

    def register_fill(
        self, fill_price: float, timestamp: float, is_buy: bool
    ) -> None:
        """Registra un fill propio para medir adverse selection posterior."""
        sign = 1.0 if is_buy else -1.0
        self._pending_fills.append((fill_price, timestamp, sign))

    def _evaluate_adverse_selection(
        self, current_price: float, current_time: float
    ) -> None:
        """Evalúa adverse selection de fills pasados: mark-to-market después de T."""
        # Fills are ordered by time, so expired ones are at the front
        while self._pending_fills:
            fill_price, fill_time, sign = self._pending_fills[0]
            if current_time - fill_time < self.as_horizon:
                break  # Remaining fills are newer, stop
            # AS: positive = price moved against us (adverse)
            if fill_price > 0:
                as_bps = (current_price - fill_price) / fill_price * 10_000 * (-sign)
                self._as_measurements.append(as_bps)
            self._pending_fills.popleft()

    def estimate_impact(
        self, size_usd: float, book_depth_usd: float = 0.0
    ) -> float:
        """Estima impacto permanente en bps para un tamaño dado.

        Formula: impact = lambda_ema * sqrt(size / depth)
        Si no hay depth, usa lambda_ema * sqrt(size / referencia)
        """
        if not self._result.is_valid or self._lambda_ema <= 0:
            return 0.0

        if book_depth_usd > 0 and size_usd > 0:
            size_ratio = size_usd / book_depth_usd
            return self._lambda_ema * math.sqrt(min(size_ratio, 4.0))
        elif size_usd > 0:
            # Sin depth: usar 100k como referencia conservadora
            return self._lambda_ema * math.sqrt(min(size_usd / 100_000, 4.0))
        return 0.0

    @property
    def current(self) -> KyleLambdaResult:
        return self._result

    def reset(self) -> None:
        self._price_changes.clear()
        self._signed_volumes.clear()
        self._lambda_history.clear()
        self._pending_fills.clear()
        self._as_measurements.clear()
        self._last_price = 0.0
        self._lambda_ema = 0.0
        self._ema_initialized = False
        self._result = KyleLambdaResult()


# ══════════════════════════════════════════════════════════════════
# 5. MICROSTRUCTURE ENGINE — Orquestador de todos los indicadores
# ══════════════════════════════════════════════════════════════════

@dataclass
class MicrostructureSnapshot:
    """Snapshot completo de microestructura para un símbolo."""
    symbol: str
    vpin: VPINResult = field(default_factory=VPINResult)
    hawkes: HawkesResult = field(default_factory=HawkesResult)
    avellaneda_stoikov: ASResult = field(default_factory=ASResult)
    kyle_lambda: KyleLambdaResult = field(default_factory=KyleLambdaResult)
    timestamp: float = 0.0

    @property
    def should_widen_spread(self) -> bool:
        """True si los indicadores sugieren ensanchar spread de MM."""
        return self.vpin.is_toxic or self.hawkes.is_spike

    @property
    def should_pause_mm(self) -> bool:
        """True si el riesgo es tan alto que MM debería pausarse."""
        return (self.vpin.vpin >= 0.8 and self.hawkes.is_spike)

    @property
    def should_filter_mr(self) -> bool:
        """True si MR debería evitar nuevas entradas.
        Requiere AMBAS condiciones (VPIN alto Y Hawkes spike) para filtrar.
        """
        return self.vpin.vpin >= 0.85 and self.hawkes.spike_ratio >= 4.0

    @property
    def risk_score(self) -> float:
        """Score de riesgo de microestructura (0=bajo, 1=máximo)."""
        vpin_score = min(self.vpin.vpin / 1.0, 1.0) if self.vpin.vpin > 0 else 0
        hawkes_score = min((self.hawkes.spike_ratio - 1.0) / 5.0, 1.0) if self.hawkes.spike_ratio > 1 else 0
        return max(vpin_score, hawkes_score)


class MicrostructureEngine:
    """
    Motor que orquesta todos los indicadores de microestructura por símbolo.
    Mantiene una instancia de VPIN, Hawkes y A-S para cada activo.
    Se conecta al MarketDataCollector y alimenta las estrategias y risk manager.
    """

    def __init__(self, symbols: List[str], config: Optional[Dict] = None) -> None:
        """
        Args:
            symbols: Lista de símbolos a monitorear
            config: Configuración por símbolo (desde SymbolConfig)
        """
        cfg = config or {}
        self._vpin: Dict[str, VPINCalculator] = {}
        self._hawkes: Dict[str, HawkesEstimator] = {}
        self._as_engine: Dict[str, AvellanedaStoikovEngine] = {}
        self._snapshots: Dict[str, MicrostructureSnapshot] = {}
        self._history: Dict[str, List[MicrostructureSnapshot]] = {}

        self._kyle_lambda: Dict[str, KyleLambdaEstimator] = {}

        for symbol in symbols:
            sym_cfg = cfg.get(symbol, {})
            self._vpin[symbol] = VPINCalculator(
                bucket_size=sym_cfg.get("vpin_bucket_size", 5000.0),
                n_buckets=sym_cfg.get("vpin_n_buckets", 50),
                toxic_threshold=sym_cfg.get("vpin_toxic_threshold", 0.6),
            )
            self._hawkes[symbol] = HawkesEstimator(
                mu=sym_cfg.get("hawkes_mu", 1.0),
                alpha=sym_cfg.get("hawkes_alpha", 0.5),
                beta=sym_cfg.get("hawkes_beta", 2.0),
                spike_threshold_mult=sym_cfg.get("hawkes_spike_mult", 2.5),
            )
            self._as_engine[symbol] = AvellanedaStoikovEngine(
                gamma=sym_cfg.get("mm_gamma", 0.1),
                kappa=sym_cfg.get("mm_kappa", 1.5),
                min_spread_bps=sym_cfg.get("mm_min_spread_bps", 3.0),
                max_spread_bps=sym_cfg.get("mm_max_spread_bps", 100.0),
                fee_bps=sym_cfg.get("fee_bps", 2.0),
            )
            self._kyle_lambda[symbol] = KyleLambdaEstimator(
                window=sym_cfg.get("kyle_lambda_window", 500),
                ema_span=sym_cfg.get("kyle_lambda_ema_span", 100),
                adverse_selection_horizon_sec=sym_cfg.get(
                    "adverse_selection_horizon_sec", 300.0
                ),
            )
            self._snapshots[symbol] = MicrostructureSnapshot(symbol=symbol)
            self._history[symbol] = []

    # ── Actualización tick-a-tick ──────────────────────────────────

    def on_trade(
        self, symbol: str, price: float, quantity: float, timestamp: float,
        is_buy: Optional[bool] = None,
    ) -> None:
        """Procesa un trade para actualizar VPIN, Hawkes y Kyle Lambda."""
        if symbol not in self._vpin:
            return

        # Actualizar VPIN
        vpin_result = self._vpin[symbol].on_trade(price, quantity, timestamp)

        # Actualizar Hawkes (cada trade es un evento)
        hawkes_result = self._hawkes[symbol].on_event(timestamp, "trade")

        # Actualizar Kyle Lambda (market impact estimation)
        kyle_result = self._kyle_lambda[symbol].on_trade(
            price, quantity, timestamp, is_buy=is_buy
        )

        # Actualizar snapshot
        snap = self._snapshots[symbol]
        snap.vpin = vpin_result
        snap.hawkes = hawkes_result
        snap.kyle_lambda = kyle_result
        snap.timestamp = timestamp

    def on_bar(
        self, symbol: str, open_p: float, high: float, low: float,
        close: float, volume: float, timestamp: float
    ) -> None:
        """Actualiza indicadores con datos de barra (para backtesting).

        Actualiza VPIN con datos OHLCV y simula un evento Hawkes por barra
        para que la intensidad no quede en 0 durante backtests sin ticks.
        """
        if symbol not in self._vpin:
            return
        vpin_result = self._vpin[symbol].on_bar(open_p, high, low, close, volume, timestamp)

        # Registrar un evento Hawkes por barra para mantener intensidad actualizada
        hawkes_result = self._hawkes[symbol].on_event(timestamp, "bar")

        # Kyle Lambda: estimar desde barra usando BVC direction
        # buy_pct = (close - low) / (high - low); sign = 2*buy_pct - 1
        kl = self._kyle_lambda.get(symbol)
        if kl and close > 0 and volume > 0:
            if high > low:
                buy_pct = max(0.0, min(1.0, (close - low) / (high - low)))
            else:
                buy_pct = 0.5
            is_buy = buy_pct > 0.5
            kyle_result = kl.on_trade(close, volume, timestamp, is_buy=is_buy)
        else:
            kyle_result = kl.current if kl else KyleLambdaResult()

        snap = self._snapshots[symbol]
        snap.vpin = vpin_result
        snap.hawkes = hawkes_result
        snap.kyle_lambda = kyle_result
        snap.timestamp = timestamp

    def compute_as_spread(
        self,
        symbol: str,
        mid_price: float,
        inventory: float,
        max_inventory: float,
        sigma: float,
        atr: float,
        time_remaining: float = 0.5,
    ) -> ASResult:
        """Calcula spread A-S incorporando VPIN y Hawkes actuales."""
        if symbol not in self._as_engine:
            return ASResult()

        snap = self._snapshots.get(symbol)
        vpin = snap.vpin if snap else None
        hawkes = snap.hawkes if snap else None

        kyle_lambda_result = self._snapshots[symbol].kyle_lambda if symbol in self._snapshots else None

        result = self._as_engine[symbol].compute(
            mid_price=mid_price,
            inventory=inventory,
            max_inventory=max_inventory,
            sigma=sigma,
            atr=atr,
            time_remaining=time_remaining,
            vpin=vpin,
            hawkes=hawkes,
            kyle_lambda=kyle_lambda_result,
            timestamp=time.time(),
        )

        if snap:
            snap.avellaneda_stoikov = result

        return result

    # ── Acceso a datos ─────────────────────────────────────────────

    def get_snapshot(self, symbol: str) -> MicrostructureSnapshot:
        """Obtiene el snapshot actual de microestructura para un símbolo."""
        return self._snapshots.get(
            symbol, MicrostructureSnapshot(symbol=symbol)
        )

    def get_vpin(self, symbol: str) -> VPINResult:
        return self._snapshots.get(symbol, MicrostructureSnapshot(symbol=symbol)).vpin

    def get_hawkes(self, symbol: str) -> HawkesResult:
        return self._snapshots.get(symbol, MicrostructureSnapshot(symbol=symbol)).hawkes

    def get_as(self, symbol: str) -> ASResult:
        return self._snapshots.get(symbol, MicrostructureSnapshot(symbol=symbol)).avellaneda_stoikov

    def get_kyle_lambda(self, symbol: str) -> KyleLambdaResult:
        return self._snapshots.get(symbol, MicrostructureSnapshot(symbol=symbol)).kyle_lambda

    def register_fill(
        self, symbol: str, fill_price: float, timestamp: float, is_buy: bool
    ) -> None:
        """Registra un fill propio para medir adverse selection."""
        kl = self._kyle_lambda.get(symbol)
        if kl:
            kl.register_fill(fill_price, timestamp, is_buy)

    def estimate_impact(
        self, symbol: str, size_usd: float, book_depth_usd: float = 0.0
    ) -> float:
        """Estima impacto permanente en bps usando Kyle Lambda."""
        kl = self._kyle_lambda.get(symbol)
        if kl:
            return kl.estimate_impact(size_usd, book_depth_usd)
        return 0.0

    def save_snapshot(self, symbol: str) -> None:
        """Guarda el snapshot actual al historial (para backtesting)."""
        snap = self._snapshots.get(symbol)
        if snap:
            hist = self._history.setdefault(symbol, [])
            hist.append(copy.deepcopy(snap))
            # Limitar historial para evitar memory leak en ejecución prolongada
            if len(hist) > 50000:
                self._history[symbol] = hist[-25000:]

    def get_history(self, symbol: str) -> List[MicrostructureSnapshot]:
        return self._history.get(symbol, [])

    def get_all_symbols(self) -> List[str]:
        return list(self._snapshots.keys())
