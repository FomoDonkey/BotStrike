"""
Microprice — Estimador de precio justo superior al mid-price.

El mid-price clasico (best_bid + best_ask) / 2 ignora la informacion contenida
en las cantidades de cada lado del book. El microprice corrige esto ponderando
por las cantidades inversas: si hay mas cantidad en el bid, el precio justo
se mueve hacia el ask (porque es mas probable que el proximo trade sea un buy).

Modelos implementados:
    1. Microprice Level-1: Solo top-of-book (mas rapido, baseline institucional)
    2. Microprice Multi-Level: Pondera N niveles con decay
    3. Microprice Ajustado: Incorpora trade intensity y OBI momentum

Paper de referencia: Stoikov (2018) "The Micro-Price"
Formula base: μ = ask * (bid_qty / (bid_qty + ask_qty)) + bid * (ask_qty / (bid_qty + ask_qty))

Intuicion:
    Si bid_qty >> ask_qty → hay mucho soporte → precio justo se mueve hacia ask
    Si ask_qty >> bid_qty → hay mucha presion venta → precio justo se mueve hacia bid
    Si bid_qty == ask_qty → microprice == mid_price (caso clasico)

Uso:
    mp = MicropriceCalculator()
    result = mp.compute(orderbook)
    # result.microprice: precio justo ajustado por imbalance
    # result.adjustment_bps: diferencia vs mid_price en bps
"""
from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


@dataclass
class MicropriceResult:
    """Resultado del calculo de microprice."""
    microprice: float = 0.0           # Precio justo ajustado
    mid_price: float = 0.0            # Mid-price clasico (referencia)
    adjustment_bps: float = 0.0       # Diferencia microprice vs mid en bps
    imbalance_ratio: float = 0.5      # bid_qty / (bid_qty + ask_qty) en level 1
    multi_level_microprice: float = 0.0  # Microprice con N niveles
    adjusted_microprice: float = 0.0   # Microprice con trade intensity + OBI
    spread_bps: float = 0.0           # Spread actual en bps
    bid_pressure: float = 0.0         # Presion compradora normalizada (0-1)
    ask_pressure: float = 0.0         # Presion vendedora normalizada (0-1)
    timestamp: float = 0.0

    @property
    def is_valid(self) -> bool:
        return self.microprice > 0 and self.mid_price > 0

    @property
    def direction_bias(self) -> str:
        """Sesgo de direccion: 'bullish', 'bearish', 'neutral'."""
        if self.adjustment_bps > 1.0:
            return "bullish"
        elif self.adjustment_bps < -1.0:
            return "bearish"
        return "neutral"


class MicropriceCalculator:
    """
    Calcula microprice a partir del libro de ordenes.

    Tres niveles de sofisticacion:
        1. Level-1: Solo best bid/ask quantities
        2. Multi-Level: Pondera N niveles con decay exponencial
        3. Adjusted: Incorpora trade intensity y OBI delta

    El microprice ajustado es el que deberia usarse como "fair value"
    en vez de mid_price para:
        - Reservation price en Avellaneda-Stoikov
        - Referencia de entrada en Mean Reversion
        - Trigger de breakout en Trend Following
        - Baseline de slippage calculation
    """

    def __init__(
        self,
        levels: int = 5,
        decay: float = 0.6,
        intensity_weight: float = 0.1,
        ema_alpha: float = 0.3,
    ) -> None:
        """
        Args:
            levels: Niveles del book para microprice multi-level
            decay: Factor de decay exponencial por nivel
            intensity_weight: Peso del ajuste por trade intensity (0-1)
            ema_alpha: Alpha del EMA para suavizar microprice
        """
        self.levels = levels
        self.decay = decay
        self.intensity_weight = intensity_weight
        self.ema_alpha = ema_alpha

        # Pesos pre-calculados
        self._weights = np.array([decay ** i for i in range(levels)])
        self._weights /= self._weights.sum()

        # EMA del microprice para suavizado
        self._ema_microprice: float = 0.0
        self._history: deque = deque(maxlen=500)
        self._result = MicropriceResult()

    def compute(
        self,
        orderbook,
        trade_intensity_buy: float = 0.0,
        trade_intensity_sell: float = 0.0,
        obi_delta: float = 0.0,
    ) -> MicropriceResult:
        """Calcula microprice desde un OrderBook.

        Args:
            orderbook: OrderBook con bids y asks
            trade_intensity_buy: Intensidad reciente de trades de compra (eventos/s)
            trade_intensity_sell: Intensidad reciente de trades de venta (eventos/s)
            obi_delta: Cambio reciente del OBI (momentum de presion)
        """
        if orderbook is None:
            return self._result

        bids = orderbook.bids
        asks = orderbook.asks

        if not bids or not asks:
            return self._result

        # Ordenar
        bids_sorted = sorted(bids, key=lambda x: x.price, reverse=True)
        asks_sorted = sorted(asks, key=lambda x: x.price)

        best_bid = bids_sorted[0].price
        best_ask = asks_sorted[0].price
        best_bid_qty = bids_sorted[0].quantity
        best_ask_qty = asks_sorted[0].quantity

        if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
            return self._result

        mid_price = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        spread_bps = spread / mid_price * 10_000

        # ══════════════════════════════════════════════════════════
        # 1. MICROPRICE LEVEL-1 (Stoikov formula)
        # ══════════════════════════════════════════════════════════
        total_qty_l1 = best_bid_qty + best_ask_qty
        if total_qty_l1 > 0:
            imbalance_ratio = best_bid_qty / total_qty_l1
            # Microprice: ponderar ask por bid_qty y bid por ask_qty
            microprice_l1 = (
                best_ask * (best_bid_qty / total_qty_l1)
                + best_bid * (best_ask_qty / total_qty_l1)
            )
        else:
            imbalance_ratio = 0.5
            microprice_l1 = mid_price

        # ══════════════════════════════════════════════════════════
        # 2. MICROPRICE MULTI-LEVEL
        # ══════════════════════════════════════════════════════════
        n_levels = min(len(bids_sorted), len(asks_sorted), self.levels)
        if n_levels >= 2:
            weights = self._weights[:n_levels]
            weights = weights / weights.sum()

            # Imbalance ponderado por nivel
            weighted_bid_qty = sum(
                bids_sorted[i].quantity * weights[i] for i in range(n_levels)
            )
            weighted_ask_qty = sum(
                asks_sorted[i].quantity * weights[i] for i in range(n_levels)
            )
            total_weighted = weighted_bid_qty + weighted_ask_qty

            if total_weighted > 0:
                # Microprice multi-level: usar precio medio ponderado de cada nivel
                bid_price_weighted = sum(
                    bids_sorted[i].price * bids_sorted[i].quantity * weights[i]
                    for i in range(n_levels)
                )
                ask_price_weighted = sum(
                    asks_sorted[i].price * asks_sorted[i].quantity * weights[i]
                    for i in range(n_levels)
                )

                bid_qty_total = sum(bids_sorted[i].quantity * weights[i] for i in range(n_levels))
                ask_qty_total = sum(asks_sorted[i].quantity * weights[i] for i in range(n_levels))

                if bid_qty_total > 0 and ask_qty_total > 0:
                    avg_bid = bid_price_weighted / bid_qty_total
                    avg_ask = ask_price_weighted / ask_qty_total
                    ml_imbalance = weighted_bid_qty / total_weighted
                    microprice_ml = avg_ask * ml_imbalance + avg_bid * (1 - ml_imbalance)
                else:
                    microprice_ml = microprice_l1
            else:
                microprice_ml = microprice_l1
        else:
            microprice_ml = microprice_l1

        # ══════════════════════════════════════════════════════════
        # 3. MICROPRICE AJUSTADO (intensity + OBI momentum)
        # ══════════════════════════════════════════════════════════
        # El trade intensity sesga: si buy_intensity > sell → precio sube
        intensity_adjustment = 0.0
        total_intensity = trade_intensity_buy + trade_intensity_sell
        if total_intensity > 0 and self.intensity_weight > 0:
            intensity_imbalance = (trade_intensity_buy - trade_intensity_sell) / total_intensity
            # Ajuste proporcional al spread (no puede exceder half-spread)
            intensity_adjustment = intensity_imbalance * (spread / 2) * self.intensity_weight

        # OBI delta como momentum predictor
        obi_adjustment = 0.0
        if abs(obi_delta) > 0.05:
            # OBI delta positivo = presion compradora creciente → microprice sube
            obi_adjustment = obi_delta * (spread / 2) * 0.15

        microprice_adjusted = microprice_ml + intensity_adjustment + obi_adjustment

        # Clamp: no puede salir del bid-ask spread
        microprice_adjusted = max(best_bid, min(best_ask, microprice_adjusted))
        microprice_l1 = max(best_bid, min(best_ask, microprice_l1))
        microprice_ml = max(best_bid, min(best_ask, microprice_ml))

        # EMA smoothing
        if self._ema_microprice > 0:
            self._ema_microprice = (
                self.ema_alpha * microprice_adjusted
                + (1 - self.ema_alpha) * self._ema_microprice
            )
        else:
            self._ema_microprice = microprice_adjusted

        # Adjustment vs mid en bps (clamped to avoid extreme values)
        if mid_price > 0:
            adjustment_bps = (microprice_adjusted - mid_price) / mid_price * 10_000
            adjustment_bps = max(-500.0, min(500.0, adjustment_bps))
        else:
            adjustment_bps = 0.0

        # Pressure metrics
        bid_pressure = imbalance_ratio
        ask_pressure = 1.0 - imbalance_ratio

        self._result = MicropriceResult(
            microprice=microprice_l1,
            mid_price=mid_price,
            adjustment_bps=adjustment_bps,
            imbalance_ratio=imbalance_ratio,
            multi_level_microprice=microprice_ml,
            adjusted_microprice=microprice_adjusted,
            spread_bps=spread_bps,
            bid_pressure=bid_pressure,
            ask_pressure=ask_pressure,
            timestamp=time.time(),
        )

        self._history.append(microprice_adjusted)
        return self._result

    @property
    def current(self) -> MicropriceResult:
        return self._result

    @property
    def ema_price(self) -> float:
        """Microprice suavizado con EMA."""
        return self._ema_microprice

    @property
    def history(self) -> List[float]:
        return list(self._history)

    def reset(self) -> None:
        self._ema_microprice = 0.0
        self._history.clear()
        self._result = MicropriceResult()
