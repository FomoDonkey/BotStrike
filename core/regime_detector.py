"""
Detector de régimen de mercado.
Clasifica el mercado en: RANGING, TRENDING_UP, TRENDING_DOWN, BREAKOUT.
Usa volatilidad relativa, momentum y ADX con thresholds adaptativos.

2026-09-02 — horizonte y permanencia (auditoría del ruido de régimen):
  En el CT el detector cambió de régimen 885 veces en 48 h (4,6/h por símbolo, mediana
  de 5 min por régimen, 320 idas y vueltas A→B→A en menos de 5 min) porque todo se
  calculaba sobre velas de 1 minuto (ADX14 = 14 min, momentum20 = 20 min) con umbrales
  que son percentiles móviles de 8 h y un "suavizado" de 2 detecciones a 3 s (6 s).
  Ahora:
    - `regime_timeframe_min` (por defecto 15): las velas de 1 min se agregan a velas de
      N minutos COMPLETAS antes de calcular los indicadores (ADX14 = 3,5 h, momentum20 = 5 h).
    - `regime_min_dwell_min` (por defecto 30): un régimen candidato debe mantenerse ese
      tiempo antes de sustituir al confirmado (histéresis temporal).
  Ambos se leen en vivo de Settings.trading (editables desde la UI). Sin settings el
  detector conserva el comportamiento antiguo (1 min, 2 detecciones) para las pruebas.
"""
from __future__ import annotations
import math
import time
from collections import deque
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from config.settings import SymbolConfig
from core.types import MarketRegime
from core.indicators import Indicators
import structlog

logger = structlog.get_logger(__name__)

# Wilder's ADX (EWM span 27) carries ~10 % of its seed after 42 bars; the thresholds never look at
# the bars before that. And whatever the window, they stay inside textbook bands.
ADX_WARMUP_BARS = 42
MIN_THRESHOLD_BARS = 30
ADX_TREND_BAND = (20.0, 30.0)
MOM_THRESHOLD_BAND = (0.005, 0.03)


class RegimeDetector:
    """Detecta el régimen de mercado usando múltiples señales."""

    def __init__(self, settings: Any = None, timeframe_min: int = 1, min_dwell_min: int = 0) -> None:
        self._settings = settings
        self._timeframe_min = int(timeframe_min)
        self._min_dwell_min = int(min_dwell_min)
        # Historial de regímenes por símbolo para suavizado (modo legacy, dwell = 0)
        self._regime_history: Dict[str, list] = {}
        # Régimen suavizado/confirmado por símbolo
        self._current_regime: Dict[str, MarketRegime] = {}
        # Histéresis temporal (dwell > 0)
        self._candidate: Dict[str, MarketRegime] = {}
        self._candidate_since: Dict[str, float] = {}
        self._confirmed_since: Dict[str, float] = {}
        self._last_raw: Dict[str, MarketRegime] = {}
        self._last_inputs: Dict[str, Dict[str, float]] = {}
        # Cache del remuestreo por símbolo: (último timestamp de 1 min, frame agregado)
        self._resample_cache: Dict[str, tuple] = {}
        # Thresholds adaptativos por símbolo (cached)
        self._adaptive_thresholds: Dict[str, Dict[str, float]] = {}
        # Cache timer: recalcular thresholds cada 15s (60s was too stale for 1m bar regime transitions)
        self._threshold_last_update: Dict[str, float] = {}
        self._threshold_cache_sec: float = 15.0

    # ── parámetros en vivo ─────────────────────────────────────────
    def params(self) -> tuple:
        tc = getattr(self._settings, "trading", None)
        if tc is None:
            return max(1, self._timeframe_min), max(0, self._min_dwell_min)
        return (max(1, int(getattr(tc, "regime_timeframe_min", self._timeframe_min))),
                max(0, int(getattr(tc, "regime_min_dwell_min", self._min_dwell_min))))

    def detect(
        self, df: pd.DataFrame, symbol: str, config: SymbolConfig
    ) -> MarketRegime:
        """Detecta el régimen actual del mercado para un símbolo.

        Args:
            df: DataFrame de velas de 1 min con indicadores (close, atr, adx, momentum, vol_pct…)
            symbol: Nombre del símbolo
            config: Configuración del símbolo

        Returns:
            MarketRegime detectado (confirmado)
        """
        if df.empty or len(df) < 5:
            return MarketRegime.UNKNOWN

        tf, dwell = self.params()
        frame = self._resample(df, symbol, config, tf) if tf > 1 else df
        if frame is None or frame.empty or len(frame) < config.regime_vol_lookback:
            return MarketRegime.UNKNOWN

        # Obtener métricas actuales
        current = frame.iloc[-1]
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
        thresholds = self._update_adaptive_thresholds(frame, symbol, config)

        # Lógica de clasificación multi-señal
        raw = self._classify(
            vol_pct=vol_pct,
            adx=adx,
            momentum=momentum,
            ema_cross=ema_cross,
            thresholds=thresholds,
        )
        self._last_raw[symbol] = raw
        self._last_inputs[symbol] = {
            "vol_pct": round(float(vol_pct), 4), "adx": round(float(adx), 2),
            "momentum": round(float(momentum), 5), "ema_cross": round(float(ema_cross), 5),
            **{f"thr_{k}": round(float(v), 4) for k, v in thresholds.items()},
        }

        # Confirmación: histéresis temporal (dwell) o suavizado legacy (2 detecciones)
        bar_ts = self._frame_time(frame)
        if dwell > 0:
            regime = self._confirm(symbol, raw, bar_ts, dwell * 60.0)
        else:
            regime = self._smooth_regime(symbol, raw)
            if self._current_regime.get(symbol) != regime:
                self._confirmed_since[symbol] = bar_ts
        self._current_regime[symbol] = regime

        logger.debug(
            "regime_detected",
            symbol=symbol,
            regime=regime.value,
            raw=raw.value,
            vol_pct=round(vol_pct, 3),
            adx=round(adx, 2),
            momentum=round(momentum, 5),
        )
        return regime

    # ── remuestreo a velas de N minutos ────────────────────────────
    @staticmethod
    def _frame_time(frame: pd.DataFrame) -> float:
        if "timestamp" in frame.columns:
            try:
                return float(frame["timestamp"].iloc[-1])
            except Exception:
                pass
        return time.time()

    def _resample(self, df: pd.DataFrame, symbol: str, config: SymbolConfig,
                  tf: int) -> Optional[pd.DataFrame]:
        """Agrega velas de 1 min (timestamp = cierre de vela) a velas COMPLETAS de `tf`
        minutos y recalcula los indicadores sobre ellas. Cacheado hasta que llega una
        vela de 1 min nueva (el loop de estrategia corre cada 3 s)."""
        if "timestamp" not in df.columns or not {"open", "high", "low", "close"}.issubset(df.columns):
            return df
        last_ts = float(df["timestamp"].iloc[-1])
        cached = self._resample_cache.get(symbol)
        if cached and cached[0] == last_ts and cached[1] == tf:
            return cached[2]
        from core.bars import aggregate_1m
        agg = aggregate_1m(df, tf)
        if agg is None or agg.empty:
            out = agg if agg is not None else df
        else:
            try:
                out = Indicators.compute_all(
                    agg, {"ema_fast": config.tf_ema_fast, "ema_slow": config.tf_ema_slow})
            except Exception as e:
                logger.warning("regime_resample_indicators_failed", symbol=symbol, error=str(e))
                out = agg
        self._resample_cache[symbol] = (last_ts, tf, out)
        return out

    # ── histéresis temporal ────────────────────────────────────────
    def _confirm(self, symbol: str, raw: MarketRegime, now_ts: float, dwell_sec: float) -> MarketRegime:
        """Un régimen candidato sustituye al confirmado solo tras `dwell_sec` segundos
        (tiempo de vela) de detecciones consecutivas. El primer régimen se acepta directo."""
        confirmed = self._current_regime.get(symbol)
        if confirmed is None or confirmed == MarketRegime.UNKNOWN:
            self._candidate.pop(symbol, None)
            self._confirmed_since[symbol] = now_ts
            return raw
        if raw == confirmed:
            self._candidate.pop(symbol, None)
            return confirmed
        if self._candidate.get(symbol) != raw:
            self._candidate[symbol] = raw
            self._candidate_since[symbol] = now_ts
            return confirmed
        if now_ts - self._candidate_since.get(symbol, now_ts) >= dwell_sec:
            self._candidate.pop(symbol, None)
            self._confirmed_since[symbol] = now_ts
            return raw
        return confirmed

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
        """Actualiza thresholds adaptativos. Cached 15 s de tiempo de VELA, no de reloj.

        This cache used to be keyed on `time.monotonic()`, which made the whole regime path
        depend on how fast the process happened to run. Measured 2026-09-04: the same 2,000 bars
        of BTC, same code, same data, run twice in one process with nothing between them but a
        4 ms sleep per bar, classified 256 of 1,900 bars into a DIFFERENT regime — because a
        slower run crosses the 15-wall-second boundary more often and so refreshes the
        percentile thresholds more often. A backtest whose result moves with CPU load is not
        evidence of anything, and the same code runs live.

        Bar time fixes it and is also what the cache was always trying to express: the frame is
        1m bars, so the thresholds cannot change more than once a minute no matter how often the
        3 s poll loop asks. Live behaviour is unchanged in substance — it now refreshes once per
        new bar instead of up to four times for the same bar.
        """
        now = self._frame_time(df)   # bar timestamp (epoch s); falls back to wall clock only
        last = self._threshold_last_update.get(symbol, 0)
        cached = self._adaptive_thresholds.get(symbol)
        if cached and (now - last) < self._threshold_cache_sec:
            return cached

        # Skip the indicator warm-up at the head of the frame. Wilder's ADX starts from its first
        # bars and reads 60+ for the first two or three periods; with the 15 h seed of a restart the
        # 60th percentile of that came out at 61, so nothing could ever be TRENDING and every
        # symbol read RANGING for a day after each restart - five restarts on 2026-09-05 made it
        # "siempre RANGING". The percentiles are then clamped to textbook bands (ADX 20-30,
        # momentum 0.5-3 %), so a short or odd window can bias them but never break them.
        valid = df.iloc[ADX_WARMUP_BARS:] if len(df) >= ADX_WARMUP_BARS + MIN_THRESHOLD_BARS else df.iloc[0:0]
        recent = valid.iloc[-500:]
        vol_low = float(config.regime_vol_threshold_low)
        vol_high = float(config.regime_vol_threshold_high)
        adx_trend, mom_threshold = 25.0, 0.005
        if len(recent) >= MIN_THRESHOLD_BARS:
            vol_pct_series = recent.get("vol_pct", pd.Series(dtype=float)).dropna()
            if len(vol_pct_series) > 10:
                lo, hi = np.percentile(vol_pct_series.values, [30, 75])
                if lo < hi:
                    vol_low, vol_high = float(lo), float(hi)
            adx_series = recent.get("adx", pd.Series(dtype=float)).dropna()
            if len(adx_series) > 10:
                adx_trend = float(np.percentile(adx_series.values, 60))
            mom_series = recent.get("momentum_20", pd.Series(dtype=float)).dropna().abs()
            if len(mom_series) > 10:
                mom_threshold = float(np.percentile(mom_series.values, 65))

        thresholds = {
            "vol_low": min(max(vol_low, 0.2), 0.5),
            "vol_high": max(min(vol_high, 0.9), 0.6),
            "adx_trend": min(max(adx_trend, ADX_TREND_BAND[0]), ADX_TREND_BAND[1]),
            "mom_threshold": min(max(mom_threshold, MOM_THRESHOLD_BAND[0]), MOM_THRESHOLD_BAND[1]),
        }
        self._adaptive_thresholds[symbol] = thresholds
        self._threshold_last_update[symbol] = now
        return thresholds

    def _smooth_regime(self, symbol: str, regime: MarketRegime) -> MarketRegime:
        """Suaviza transiciones de régimen para evitar whipsaws (modo legacy, dwell = 0).
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

    def status(self, symbol: str) -> Dict[str, Any]:
        """Estado observable para la API/UI: confirmado, candidato, desde cuándo, inputs."""
        tf, dwell = self.params()
        cand = self._candidate.get(symbol)
        return {
            "regime": self._current_regime.get(symbol, MarketRegime.UNKNOWN).value,
            "raw": self._last_raw.get(symbol, MarketRegime.UNKNOWN).value,
            "candidate": cand.value if cand else "",
            "candidate_since": self._candidate_since.get(symbol, 0.0) if cand else 0.0,
            "confirmed_since": self._confirmed_since.get(symbol, 0.0),
            "timeframe_min": tf, "min_dwell_min": dwell,
            "inputs": self._last_inputs.get(symbol, {}),
        }
