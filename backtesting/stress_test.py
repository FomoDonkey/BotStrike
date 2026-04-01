"""
Stress Test Generator — Inyecta eventos extremos en datos para backtesting.

Toma datos OHLCV (reales o sinteticos) e inyecta:
  1. Flash crashes: caida de 5-15% en 1-5 barras
  2. Price gaps: saltos sin barras intermedias
  3. Low liquidity: periodos donde ATR se multiplica y volumen cae
  4. Liquidation cascades: caida sostenida rapida con volumen extremo

Usa el Backtester existente internamente — no duplica logica.

Uso:
    gen = StressTestGenerator()
    stressed_df = gen.inject_all(df, n_crashes=3, n_gaps=5)
    backtester.run(stressed_df, symbol)  # backtest normal sobre datos estresados

    # O via CLI:
    python main.py --backtest-stress --symbol BTC-USD
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class StressEvent:
    """Registro de un evento de stress inyectado."""
    event_type: str        # "flash_crash", "gap", "low_liquidity", "cascade"
    bar_start: int
    bar_end: int
    magnitude_pct: float   # cambio de precio en %
    description: str = ""


class StressTestGenerator:
    """Genera datos con eventos extremos para stress testing.

    No modifica el DataFrame original — retorna una copia con eventos inyectados.
    Registra cada evento inyectado para analisis posterior.
    """

    def __init__(self, seed: int = 42) -> None:
        self.rng = np.random.RandomState(seed)
        self.events: List[StressEvent] = []

    def inject_all(
        self,
        df: pd.DataFrame,
        n_crashes: int = 3,
        n_gaps: int = 5,
        n_low_liq: int = 2,
        n_cascades: int = 1,
    ) -> pd.DataFrame:
        """Inyecta todos los tipos de eventos extremos.

        Args:
            df: DataFrame OHLCV original
            n_crashes: Flash crashes a inyectar
            n_gaps: Price gaps a inyectar
            n_low_liq: Periodos de baja liquidez
            n_cascades: Cascadas de liquidacion

        Returns:
            DataFrame con eventos inyectados (copia del original)
        """
        self.events.clear()
        result = df.copy()

        # Inyectar en orden de menor a mayor impacto
        result = self.inject_low_liquidity(result, n_events=n_low_liq)
        result = self.inject_gaps(result, n_events=n_gaps)
        result = self.inject_flash_crashes(result, n_events=n_crashes)
        result = self.inject_cascades(result, n_events=n_cascades)

        # Enforce OHLC validity: high >= max(open,close), low <= min(open,close), all > 0
        result["high"] = result[["open", "high", "close"]].max(axis=1)
        result["low"] = result[["open", "low", "close"]].min(axis=1)
        result[["open", "high", "low", "close"]] = result[["open", "high", "low", "close"]].clip(lower=0.0001)

        return result

    def inject_flash_crashes(
        self,
        df: pd.DataFrame,
        n_events: int = 3,
        min_drop_pct: float = 5.0,
        max_drop_pct: float = 15.0,
        duration_bars: int = 3,
    ) -> pd.DataFrame:
        """Inyecta flash crashes: caida rapida seguida de recuperacion parcial.

        El precio cae min_drop_pct a max_drop_pct en duration_bars,
        luego recupera ~50% en las siguientes 5 barras.
        """
        result = df.copy()
        n = len(result)
        safe_zone = 50  # no inyectar en las primeras/ultimas barras

        for _ in range(n_events):
            start = self.rng.randint(safe_zone, n - safe_zone - duration_bars - 10)
            drop_pct = self.rng.uniform(min_drop_pct, max_drop_pct) / 100.0

            # Fase de caida
            base_price = float(result.iloc[start]["close"])
            for j in range(duration_bars):
                idx = start + j
                frac = (j + 1) / duration_bars
                crash_mult = 1.0 - drop_pct * frac
                new_close = base_price * crash_mult
                new_low = new_close * 0.998
                new_open = float(result.iloc[idx]["open"]) if j == 0 else float(result.iloc[idx - 1]["close"])
                new_high = max(new_open, new_close) * 1.001

                result.at[result.index[idx], "close"] = new_close
                result.at[result.index[idx], "low"] = new_low
                result.at[result.index[idx], "high"] = new_high
                result.at[result.index[idx], "open"] = new_open
                result.at[result.index[idx], "volume"] = float(result.iloc[idx]["volume"]) * 5.0

            # Fase de recuperacion parcial (~50%)
            bottom = base_price * (1.0 - drop_pct)
            recovery_target = bottom + (base_price - bottom) * 0.5
            for j in range(5):
                idx = start + duration_bars + j
                if idx >= n:
                    break
                frac = (j + 1) / 5
                new_close = bottom + (recovery_target - bottom) * frac
                result.at[result.index[idx], "close"] = new_close
                result.at[result.index[idx], "low"] = min(new_close, float(result.iloc[idx]["low"]))
                result.at[result.index[idx], "high"] = max(new_close, float(result.iloc[idx - 1]["close"]))
                result.at[result.index[idx], "open"] = float(result.iloc[idx - 1]["close"])
                result.at[result.index[idx], "volume"] = float(result.iloc[idx]["volume"]) * 3.0

            self.events.append(StressEvent(
                event_type="flash_crash", bar_start=start,
                bar_end=start + duration_bars + 5,
                magnitude_pct=-drop_pct * 100,
                description=f"Flash crash: -{drop_pct*100:.1f}% over {duration_bars} bars",
            ))

        return result

    def inject_gaps(
        self,
        df: pd.DataFrame,
        n_events: int = 5,
        min_gap_pct: float = 1.0,
        max_gap_pct: float = 5.0,
    ) -> pd.DataFrame:
        """Inyecta price gaps: precio salta sin transicion entre barras.

        Simula overnight gaps o eventos de noticias.
        """
        result = df.copy()
        n = len(result)
        safe_zone = 50

        for _ in range(n_events):
            idx = self.rng.randint(safe_zone, n - safe_zone)
            gap_pct = self.rng.uniform(min_gap_pct, max_gap_pct) / 100.0
            direction = self.rng.choice([-1, 1])
            gap_mult = 1.0 + direction * gap_pct

            prev_close = float(result.iloc[idx - 1]["close"])
            new_open = prev_close * gap_mult

            # Ajustar la barra del gap y las siguientes
            old_close = float(result.iloc[idx]["close"])
            price_shift = new_open - float(result.iloc[idx]["open"])

            result.at[result.index[idx], "open"] = new_open
            result.at[result.index[idx], "high"] = float(result.iloc[idx]["high"]) + price_shift
            result.at[result.index[idx], "low"] = float(result.iloc[idx]["low"]) + price_shift
            result.at[result.index[idx], "close"] = old_close + price_shift

            # Propagar el shift a las barras siguientes (decaying)
            for j in range(1, min(20, n - idx)):
                decay = 0.95 ** j
                shift = price_shift * decay
                result.at[result.index[idx + j], "open"] = float(result.iloc[idx + j]["open"]) + shift
                result.at[result.index[idx + j], "high"] = float(result.iloc[idx + j]["high"]) + shift
                result.at[result.index[idx + j], "low"] = float(result.iloc[idx + j]["low"]) + shift
                result.at[result.index[idx + j], "close"] = float(result.iloc[idx + j]["close"]) + shift

            self.events.append(StressEvent(
                event_type="gap", bar_start=idx, bar_end=idx,
                magnitude_pct=direction * gap_pct * 100,
                description=f"Price gap: {direction*gap_pct*100:+.1f}% at bar {idx}",
            ))

        return result

    def inject_low_liquidity(
        self,
        df: pd.DataFrame,
        n_events: int = 2,
        duration_bars: int = 30,
        volume_mult: float = 0.1,
        spread_mult: float = 5.0,
    ) -> pd.DataFrame:
        """Inyecta periodos de baja liquidez: volumen cae, spread se amplifica.

        Simula holidays, pre-market, o exchange issues.
        """
        result = df.copy()
        n = len(result)
        safe_zone = 100

        for _ in range(n_events):
            start = self.rng.randint(safe_zone, n - safe_zone - duration_bars)

            for j in range(duration_bars):
                idx = start + j
                # Reducir volumen drasticamente
                result.at[result.index[idx], "volume"] = float(result.iloc[idx]["volume"]) * volume_mult
                # Ampliar rango high-low (simula spread amplio)
                mid = (float(result.iloc[idx]["high"]) + float(result.iloc[idx]["low"])) / 2
                half_range = (float(result.iloc[idx]["high"]) - float(result.iloc[idx]["low"])) / 2
                result.at[result.index[idx], "high"] = mid + half_range * spread_mult
                result.at[result.index[idx], "low"] = mid - half_range * spread_mult

            self.events.append(StressEvent(
                event_type="low_liquidity", bar_start=start,
                bar_end=start + duration_bars,
                magnitude_pct=0,
                description=f"Low liquidity: {duration_bars} bars, vol x{volume_mult}, spread x{spread_mult}",
            ))

        return result

    def inject_cascades(
        self,
        df: pd.DataFrame,
        n_events: int = 1,
        duration_bars: int = 15,
        total_drop_pct: float = 20.0,
    ) -> pd.DataFrame:
        """Inyecta cascada de liquidacion: caida sostenida con volumen extremo.

        Simula liquidaciones en cadena como en crypto crashes historicos.
        Cada barra cae mas rapido que la anterior (aceleracion).
        """
        result = df.copy()
        n = len(result)
        safe_zone = 100

        for _ in range(n_events):
            start = self.rng.randint(safe_zone, n - safe_zone - duration_bars)
            base_price = float(result.iloc[start]["close"])
            drop_pct = total_drop_pct / 100.0

            for j in range(duration_bars):
                idx = start + j
                # Caida acelerada: cada barra cae mas que la anterior
                frac = ((j + 1) / duration_bars) ** 1.5  # exponential acceleration
                new_close = base_price * (1.0 - drop_pct * frac)
                new_open = float(result.iloc[idx - 1]["close"]) if j > 0 else base_price
                new_low = new_close * (1.0 - 0.002 * (j + 1))
                new_high = new_open * 1.001

                result.at[result.index[idx], "open"] = new_open
                result.at[result.index[idx], "high"] = new_high
                result.at[result.index[idx], "low"] = new_low
                result.at[result.index[idx], "close"] = new_close
                # Volumen extremo en cascadas
                result.at[result.index[idx], "volume"] = float(result.iloc[idx]["volume"]) * (3 + j * 2)

            self.events.append(StressEvent(
                event_type="cascade", bar_start=start,
                bar_end=start + duration_bars,
                magnitude_pct=-total_drop_pct,
                description=f"Liquidation cascade: -{total_drop_pct}% over {duration_bars} bars",
            ))

        return result

    def get_events_summary(self) -> str:
        """Resumen legible de eventos inyectados."""
        if not self.events:
            return "No stress events injected"
        lines = [f"  {len(self.events)} stress events injected:"]
        for e in self.events:
            lines.append(f"    [{e.event_type:15s}] bars {e.bar_start}-{e.bar_end} | {e.description}")
        return "\n".join(lines)
