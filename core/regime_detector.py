"""
Detector de régimen de mercado.
Clasifica el mercado en: RANGING, TRENDING_UP, TRENDING_DOWN, BREAKOUT.
Usa volatilidad relativa, momentum y ADX con thresholds adaptativos.
"""
from __future__ import annotations
import math
from collections import deque
from typing import Dict

import numpy as np
import pandas as pd

from config.settings import SymbolConfig
from core.types import MarketRegime
from core.indicators import Indicators
import structlog

logger = structlog.get_logger(__name__)


class RegimeDetector:
    """Detecta el régimen de mercado usando múltiples señales."""

    def __init__(self) -> None:
        # Historial de regímenes por símbolo para suavizado
        self._regime_history: Dict[str, list] = {}
        # Régimen suavizado confirmado por símbolo
        self._current_regime: Dict[str, MarketRegime] = {}
        # Thresholds adaptativos por símbolo (cached)
        self._adaptive_thresholds: Dict[str, Dict[str, float]] = {}
        # Cache timer: recalcular thresholds solo cada 60s (no en cada detect)
        self._threshold_last_update: Dict[str, float] = {}
        self._threshold_cache_sec: float = 60.0

    def detect(
        self, df: pd.DataFrame, symbol: str, config: SymbolConfig
    ) -> MarketRegime:
        """Detecta el régimen actual del mercado para un símbolo.

        Args:
            df: DataFrame con indicadores ya calculados (close, atr, adx, momentum, vol_pct, etc.)
            symbol: Nombre del símbolo
            config: Configuración del símbolo

        Returns:
            MarketRegime detectado
        """
        if df.empty or len(df) < 5:
            return MarketRegime.UNKNOWN

        if len(df) < config.regime_vol_lookback:
            return MarketRegime.UNKNOWN

        # Obtener métricas actuales
        current = df.iloc[-1]
        vol_pct = current.get("vol_pct", 0.5)
        adx = current.get("adx", 25)
        momentum = current.get("momentum_20", 0)
        ema_cross = current.get("ema_cross", 0)

        # Guard against NaN values propagating through comparisons
        if not isinstance(vol_pct, (int, float)) or math.isnan(vol_pct):
            vol_pct = 0.5
        if not isinstance(adx, (int, float)) or math.isnan(adx):
            adx = 25.0
        if not isinstance(momentum, (int, float)) or math.isnan(momentum):
            momentum = 0.0
        if not isinstance(ema_cross, (int, float)) or math.isnan(ema_cross):
            ema_cross = 0.0

        # Actualizar thresholds adaptativos
        thresholds = self._update_adaptive_thresholds(df, symbol, config)

        # Lógica de clasificación multi-señal
        regime = self._classify(
            vol_pct=vol_pct,
            adx=adx,
            momentum=momentum,
            ema_cross=ema_cross,
            thresholds=thresholds,
        )

        # Suavizado: evitar cambios bruscos de régimen (requiere 2 señales consecutivas)
        regime = self._smooth_regime(symbol, regime)
        self._current_regime[symbol] = regime

        logger.debug(
            "regime_detected",
            symbol=symbol,
            regime=regime.value,
            vol_pct=round(vol_pct, 3),
            adx=round(adx, 2),
            momentum=round(momentum, 5),
        )
        return regime

    def _classify(
        self,
        vol_pct: float,
        adx: float,
        momentum: float,
        ema_cross: float,
        thresholds: Dict[str, float],
    ) -> MarketRegime:
        """Clasifica régimen basándose en métricas y thresholds."""
        vol_low = thresholds["vol_low"]
        vol_high = thresholds["vol_high"]
        adx_trend = thresholds["adx_trend"]
        mom_threshold = thresholds["mom_threshold"]

        # BREAKOUT: alta volatilidad + momentum fuerte (cualquier direccion)
        if vol_pct > vol_high and abs(momentum) > mom_threshold * 1.5:
            return MarketRegime.BREAKOUT

        # TRENDING: ADX alto + dirección clara
        if adx > adx_trend and abs(momentum) > mom_threshold * 0.5:
            if momentum > 0 and ema_cross > 0:
                return MarketRegime.TRENDING_UP
            elif momentum < 0 and ema_cross < 0:
                return MarketRegime.TRENDING_DOWN

        # RANGING: baja volatilidad, bajo ADX
        if vol_pct < vol_low and adx < adx_trend * 0.8:
            return MarketRegime.RANGING

        # Default: si volatilidad media y no hay tendencia clara → ranging
        if adx < adx_trend:
            return MarketRegime.RANGING

        # Tendencia moderada
        if momentum > 0:
            return MarketRegime.TRENDING_UP
        return MarketRegime.TRENDING_DOWN

    def _update_adaptive_thresholds(
        self, df: pd.DataFrame, symbol: str, config: SymbolConfig
    ) -> Dict[str, float]:
        """Actualiza thresholds adaptativos. Cached por 60s para evitar recalcular."""
        import time as _time
        now = _time.monotonic()
        last = self._threshold_last_update.get(symbol, 0)
        cached = self._adaptive_thresholds.get(symbol)
        if cached and (now - last) < self._threshold_cache_sec:
            return cached

        lookback = min(len(df), 500)
        recent = df.iloc[-lookback:]  # iloc es más eficiente que tail()

        # Calcular distribución de volatilidad para este activo
        vol_pct_series = recent.get("vol_pct", pd.Series(dtype=float)).dropna()
        if len(vol_pct_series) > 10:
            # Un solo np.percentile con múltiples cuantiles
            vol_pcts = np.percentile(vol_pct_series.values, [30, 75])
            vol_low = float(vol_pcts[0])
            vol_high = float(vol_pcts[1])
        else:
            vol_low = config.regime_vol_threshold_low
            vol_high = config.regime_vol_threshold_high

        # ADX adaptativo
        adx_series = recent.get("adx", pd.Series(dtype=float)).dropna()
        if len(adx_series) > 10:
            adx_trend = float(np.percentile(adx_series.values, 60))
        else:
            adx_trend = 25.0

        # Momentum adaptativo basado en volatilidad del activo
        mom_series = recent.get("momentum_20", pd.Series(dtype=float)).dropna().abs()
        if len(mom_series) > 10:
            mom_threshold = float(np.percentile(mom_series.values, 65))
        else:
            mom_threshold = 0.02

        thresholds = {
            "vol_low": max(vol_low, 0.2),
            "vol_high": min(vol_high, 0.9),
            "adx_trend": max(adx_trend, 20.0),
            "mom_threshold": max(mom_threshold, 0.005),
        }
        self._adaptive_thresholds[symbol] = thresholds
        self._threshold_last_update[symbol] = now
        return thresholds

    def _smooth_regime(self, symbol: str, regime: MarketRegime) -> MarketRegime:
        """Suaviza transiciones de régimen para evitar whipsaws.
        Requiere 2 detecciones consecutivas del mismo régimen para cambiar."""
        if symbol not in self._regime_history:
            self._regime_history[symbol] = deque(maxlen=5)

        history = self._regime_history[symbol]
        history.append(regime)  # deque auto-evicts oldest

        if len(history) < 2:
            return regime

        # Si las últimas 2 coinciden, confirmar cambio
        if history[-1] == history[-2]:
            return regime

        # Si no, mantener el régimen anterior estable
        # Buscar el último régimen que se mantuvo 2+ veces
        for i in range(len(history) - 2, -1, -1):
            if i > 0 and history[i] == history[i - 1]:
                return history[i]

        return history[-2]  # mantener el previo

    def get_regime_confidence(self, symbol: str) -> float:
        """Retorna confianza en el régimen actual (0-1)."""
        history = self._regime_history.get(symbol, [])
        if len(history) < 3:
            return 0.5
        last_3 = history[-3:]
        agreement = sum(1 for r in last_3 if r == last_3[-1]) / 3.0
        return agreement

    def get_current_regime(self, symbol: str) -> MarketRegime:
        """Obtiene el último régimen suavizado/confirmado para un símbolo."""
        return self._current_regime.get(symbol, MarketRegime.UNKNOWN)
