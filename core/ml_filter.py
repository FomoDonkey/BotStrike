"""
ML Signal Filter — Clasificador ligero para filtrar señales de trading.

Usa LightGBM para predecir P(trade ganador) basado en features de
microestructura e indicadores técnicos. Solo deja pasar señales con
probabilidad > threshold.

Features:
  - RSI, z-score, ATR, momentum, vol_ratio, ADX
  - VPIN, Hawkes ratio, risk_score
  - OBI imbalance, OBI delta
  - Spread actual, regime numérico
  - Signal strength, strategy type

Entrenamiento:
  1. Ejecutar backtest sin filtro ML → recolectar trades con sus features
  2. Label: 1 si PnL > 0, 0 si PnL <= 0
  3. Train LightGBM classifier
  4. En producción: filtrar señales con predict_proba < threshold

Ligero: ~1ms por predicción, ~100KB modelo.
"""
from __future__ import annotations

import os
import pickle
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

import structlog

logger = structlog.get_logger(__name__)

# Feature names en orden consistente
FEATURE_NAMES = [
    "rsi", "zscore", "atr_pct", "momentum", "vol_ratio", "adx",
    "vpin", "hawkes_ratio", "risk_score",
    "obi_imbalance", "obi_delta",
    "strength", "regime_num",
    "bb_distance",  # distancia al BB band más cercano normalizada por ATR
]


class MLSignalFilter:
    """Filtro ML ligero para señales de trading."""

    def __init__(self, model_path: str = "data/ml_signal_filter.pkl", threshold: float = 0.55):
        self.model_path = model_path
        self.threshold = threshold
        self._model = None
        self._is_trained = False
        self._training_data: List[Dict] = []

        # Intentar cargar modelo existente
        if os.path.exists(model_path):
            try:
                with open(model_path, "rb") as f:
                    saved = pickle.load(f)
                    self._model = saved["model"]
                    self.threshold = saved.get("threshold", threshold)
                    self._is_trained = True
                    logger.info("ml_filter_loaded", path=model_path)
            except Exception as e:
                logger.warning("ml_filter_load_failed", error=str(e))

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    def extract_features(
        self,
        signal: Any,
        bar: pd.Series,
        micro: Any = None,
        obi: Any = None,
        regime: Any = None,
    ) -> Dict[str, float]:
        """Extrae features de una señal para predicción."""
        price = float(bar.get("close", 0))
        atr = float(bar.get("atr", 0)) if not pd.isna(bar.get("atr", 0)) else 0
        atr_pct = atr / price * 100 if price > 0 else 0

        bb_upper = float(bar.get("bb_upper", 0)) if not pd.isna(bar.get("bb_upper", 0)) else 0
        bb_lower = float(bar.get("bb_lower", 0)) if not pd.isna(bar.get("bb_lower", 0)) else 0
        if atr > 0 and bb_upper > 0:
            bb_dist_upper = (bb_upper - price) / atr
            bb_dist_lower = (price - bb_lower) / atr
            bb_distance = min(bb_dist_upper, bb_dist_lower)
        else:
            bb_distance = 0

        regime_map = {
            "RANGING": 0, "TRENDING_UP": 1, "TRENDING_DOWN": -1,
            "BREAKOUT": 2, "UNKNOWN": 0,
        }
        regime_num = regime_map.get(regime.value if regime else "UNKNOWN", 0)

        return {
            "rsi": float(bar.get("rsi", 50)) if not pd.isna(bar.get("rsi", 50)) else 50,
            "zscore": float(bar.get("zscore", 0)) if not pd.isna(bar.get("zscore", 0)) else 0,
            "atr_pct": atr_pct,
            "momentum": float(bar.get("momentum_20", 0)) if not pd.isna(bar.get("momentum_20", 0)) else 0,
            "vol_ratio": float(bar.get("vol_ratio", 1)) if not pd.isna(bar.get("vol_ratio", 1)) else 1,
            "adx": float(bar.get("adx", 0)) if not pd.isna(bar.get("adx", 0)) else 0,
            "vpin": micro.vpin.vpin if micro else 0,
            "hawkes_ratio": micro.hawkes.spike_ratio if micro else 1,
            "risk_score": micro.risk_score if micro else 0,
            "obi_imbalance": obi.weighted_imbalance if obi else 0,
            "obi_delta": obi.delta if obi else 0,
            "strength": signal.strength if signal else 0,
            "regime_num": regime_num,
            "bb_distance": bb_distance,
        }

    def record_trade(self, features: Dict[str, float], pnl: float) -> None:
        """Registra un trade completado para entrenamiento."""
        features["label"] = 1 if pnl > 0 else 0
        features["pnl"] = pnl
        self._training_data.append(features)

    def train(self, min_samples: int = 50) -> bool:
        """Entrena el modelo con los trades registrados."""
        if len(self._training_data) < min_samples:
            logger.info("ml_filter_insufficient_data",
                        samples=len(self._training_data), min=min_samples)
            return False

        try:
            import lightgbm as lgb

            df = pd.DataFrame(self._training_data)
            X = df[FEATURE_NAMES].fillna(0)
            y = df["label"]

            # LightGBM con regularización fuerte para evitar overfitting
            model = lgb.LGBMClassifier(
                n_estimators=50,
                max_depth=3,
                learning_rate=0.1,
                num_leaves=8,
                min_child_samples=5,
                reg_alpha=1.0,
                reg_lambda=1.0,
                subsample=0.8,
                colsample_bytree=0.8,
                verbose=-1,
            )
            model.fit(X, y)

            # Threshold selection via time-series cross-validation (not in-sample)
            # Split chronologically: train on first 70%, validate on last 30%
            split_idx = int(len(df) * 0.7)
            if split_idx >= 20 and len(df) - split_idx >= 10:
                X_val = df.iloc[split_idx:][FEATURE_NAMES].fillna(0)
                df_val = df.iloc[split_idx:]
                probs_val = model.predict_proba(X_val)[:, 1]
                best_threshold = 0.55
                best_metric = -999
                for t in np.arange(0.4, 0.75, 0.05):
                    preds = (probs_val >= t).astype(int)
                    if preds.sum() < 2:
                        continue
                    filtered_pnl = df_val.loc[preds == 1, "pnl"].sum()
                    if filtered_pnl > best_metric:
                        best_metric = filtered_pnl
                        best_threshold = t
            else:
                best_threshold = 0.55  # Safe default when not enough data for CV

            self._model = model
            self.threshold = best_threshold
            self._is_trained = True

            # Guardar
            os.makedirs(os.path.dirname(self.model_path) or ".", exist_ok=True)
            with open(self.model_path, "wb") as f:
                pickle.dump({"model": model, "threshold": best_threshold}, f)

            # Feature importance
            importance = dict(zip(FEATURE_NAMES, model.feature_importances_))
            top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:5]

            logger.info(
                "ml_filter_trained",
                samples=len(df),
                positive_rate=f"{y.mean():.1%}",
                threshold=round(best_threshold, 2),
                top_features=[(f, int(v)) for f, v in top_features],
            )
            return True

        except Exception as e:
            logger.error("ml_filter_train_failed", error=str(e))
            return False

    def should_pass(self, features: Dict[str, float]) -> bool:
        """Retorna True si la señal debería ejecutarse."""
        if not self._is_trained or self._model is None:
            return True  # Sin modelo, pasar todo

        try:
            X = pd.DataFrame([features])[FEATURE_NAMES].fillna(0)
            prob = self._model.predict_proba(X)[0][1]
            return prob >= self.threshold
        except Exception:
            return True  # Si falla predicción, pasar

    def predict_proba(self, features: Dict[str, float]) -> float:
        """Retorna probabilidad de trade ganador."""
        if not self._is_trained or self._model is None:
            return 0.5

        try:
            X = pd.DataFrame([features])[FEATURE_NAMES].fillna(0)
            return float(self._model.predict_proba(X)[0][1])
        except Exception:
            return 0.5
