"""
Order Book Imbalance Alpha — Extrae senales predictivas del libro de ordenes.

El Order Book Imbalance (OBI) mide la presion relativa de compra vs venta
en los niveles del orderbook. Es un predictor estadisticamente significativo
de movimiento a corto plazo en crypto.

Contiene:
    1. OrderBookImbalance — Calculo multi-nivel con pesos por proximidad
    2. OBIDelta — Cambio de imbalance en el tiempo (mas predictivo que nivel)

Uso en estrategias:
    - Mean Reversion: confirmar reversal con imbalance favorable
    - Trend Following: imbalance en direccion del trend = mas confianza
    - Market Making: sesgar spreads hacia el lado con mas presion

Uso:
    obi = OrderBookImbalance(levels=5)
    result = obi.compute(orderbook)
    # result.imbalance: -1 (sell pressure) a +1 (buy pressure)
    # result.delta: cambio de imbalance (momentum de presion)
"""
from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


@dataclass
class OBIResult:
    """Resultado del calculo de Order Book Imbalance."""
    imbalance: float = 0.0           # -1 (sell) a +1 (buy) presion
    weighted_imbalance: float = 0.0  # Ponderado por proximidad al mid
    delta: float = 0.0               # Cambio de imbalance (momentum)
    delta_5: float = 0.0             # Cambio sobre 5 snapshots
    bid_depth_usd: float = 0.0      # Profundidad total bids en USD
    ask_depth_usd: float = 0.0      # Profundidad total asks en USD
    depth_ratio: float = 1.0         # bid_depth / ask_depth
    top_bid_qty: float = 0.0        # Cantidad en best bid
    top_ask_qty: float = 0.0        # Cantidad en best ask
    timestamp: float = 0.0

    @property
    def signal_strength(self) -> float:
        """Fuerza de la senal OBI (0 a 1)."""
        return min(abs(self.weighted_imbalance), 1.0)

    @property
    def direction(self) -> str:
        """Direccion de la presion: 'buy', 'sell', o 'neutral'."""
        if self.weighted_imbalance > 0.15:
            return "buy"
        elif self.weighted_imbalance < -0.15:
            return "sell"
        return "neutral"


class OrderBookImbalance:
    """
    Calcula Order Book Imbalance multi-nivel con pesos por proximidad.

    Niveles mas cercanos al mid pesan mas (decay exponencial).
    Trackea historial para calcular delta (cambio de imbalance),
    que es mas predictivo que el nivel absoluto.

    Parametros:
        levels: Numero de niveles a considerar (top N bids/asks)
        decay: Factor de decay exponencial por nivel (0.5 = primer nivel pesa 2x mas)
        delta_window: Ventana para calcular delta de imbalance
    """

    def __init__(
        self,
        levels: int = 5,
        decay: float = 0.5,
        delta_window: int = 10,
    ) -> None:
        self.levels = levels
        self.decay = decay
        self.delta_window = delta_window

        # Pesos pre-calculados (exponential decay)
        self._weights = np.array([decay ** i for i in range(levels)])
        self._weights /= self._weights.sum()  # normalizar

        # Historial de imbalance para delta
        self._imbalance_history: deque = deque(maxlen=delta_window * 5)
        self._result = OBIResult()

    def compute(self, orderbook) -> OBIResult:
        """Calcula OBI desde un OrderBook object.

        Args:
            orderbook: OrderBook con listas de bids y asks (OrderBookLevel)

        Returns:
            OBIResult con imbalance, delta, y metricas de profundidad
        """
        if orderbook is None:
            return self._result

        bids = orderbook.bids
        asks = orderbook.asks

        if not bids or not asks:
            return self._result

        # Ordenar: bids descendente por precio, asks ascendente
        bids_sorted = sorted(bids, key=lambda x: x.price, reverse=True)[:self.levels]
        asks_sorted = sorted(asks, key=lambda x: x.price)[:self.levels]

        mid = orderbook.mid_price
        if mid is None or mid <= 0:
            return self._result

        # Cantidades por nivel
        bid_qtys = np.zeros(self.levels)
        ask_qtys = np.zeros(self.levels)

        for i, b in enumerate(bids_sorted):
            bid_qtys[i] = b.quantity * b.price  # convertir a USD
        for i, a in enumerate(asks_sorted):
            ask_qtys[i] = a.quantity * a.price

        # Simple imbalance (todos los niveles iguales)
        total_bid = bid_qtys.sum()
        total_ask = ask_qtys.sum()

        if total_bid + total_ask == 0:
            return self._result

        simple_imbalance = (total_bid - total_ask) / (total_bid + total_ask)

        # Weighted imbalance (niveles cercanos pesan mas)
        n_actual = min(len(bids_sorted), len(asks_sorted), self.levels)
        weights = self._weights[:n_actual]
        weights = weights / weights.sum()

        weighted_bid = np.sum(bid_qtys[:n_actual] * weights)
        weighted_ask = np.sum(ask_qtys[:n_actual] * weights)

        if weighted_bid + weighted_ask > 0:
            weighted_imbalance = (weighted_bid - weighted_ask) / (weighted_bid + weighted_ask)
        else:
            weighted_imbalance = 0.0

        # Delta: cambio de imbalance respecto a snapshot anterior
        self._imbalance_history.append(weighted_imbalance)
        history = list(self._imbalance_history)

        delta = 0.0
        if len(history) >= 2:
            delta = weighted_imbalance - history[-2]

        delta_5 = 0.0
        if len(history) >= 6:
            delta_5 = weighted_imbalance - history[-6]

        # Depth ratio
        depth_ratio = total_bid / total_ask if total_ask > 0 else 1.0

        self._result = OBIResult(
            imbalance=float(simple_imbalance),
            weighted_imbalance=float(weighted_imbalance),
            delta=float(delta),
            delta_5=float(delta_5),
            bid_depth_usd=float(total_bid),
            ask_depth_usd=float(total_ask),
            depth_ratio=float(depth_ratio),
            top_bid_qty=float(bid_qtys[0]) if len(bid_qtys) > 0 else 0.0,
            top_ask_qty=float(ask_qtys[0]) if len(ask_qtys) > 0 else 0.0,
            timestamp=time.time(),
        )
        return self._result

    @property
    def current(self) -> OBIResult:
        return self._result

    @property
    def history(self) -> List[float]:
        return list(self._imbalance_history)

    def reset(self) -> None:
        self._imbalance_history.clear()
        self._result = OBIResult()
