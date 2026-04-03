"""
Modelos cuantitativos avanzados para BotStrike.

Contiene:
    1. VolatilityTargeting  — Escala posiciones para mantener vol constante
    2. KellyCriterion       — Fraccion optima de apuesta (Half-Kelly capped)
    3. RiskOfRuin           — Probabilidad de alcanzar drawdown maximo
    4. MonteCarloBootstrap  — Simulacion de equity curves por bootstrap
    5. CorrelationRegime    — Detecta regime de correlacion (stress vs normal)
    6. CovarianceTracker    — Matriz de covarianza rolling para risk parity
"""
from __future__ import annotations
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ======================================================================
# 1. VOLATILITY TARGETING — Escalar posiciones para vol constante
# ======================================================================

@dataclass
class VolTargetResult:
    """Resultado del calculo de volatility targeting."""
    scalar: float = 1.0             # Multiplicador de posicion (0.5 a 2.0)
    realized_vol: float = 0.0      # Vol anualizada realizada del portfolio
    target_vol: float = 0.15       # Vol objetivo
    timestamp: float = 0.0


class VolatilityTargeting:
    """
    Volatility Targeting: escala la exposicion total del portfolio para
    mantener una volatilidad anualizada constante.

    Cuando la vol realizada sube, reduce posiciones.
    Cuando la vol realizada baja, aumenta posiciones.

    Usado por CTAs profesionales para estabilizar el Sharpe ratio.
    El scalar se aplica a TODAS las allocations antes del risk manager.

    Formula:
        scalar = target_vol / realized_vol(lookback_days)
        scalar = clamp(scalar, min_scalar, max_scalar)
    """

    def __init__(
        self,
        target_vol: float = 0.15,
        lookback_days: int = 20,
        min_scalar: float = 0.5,
        max_scalar: float = 2.0,
        annualization: float = 365.0,  # Crypto trades 365 days/year (was 252 — equity convention)
    ) -> None:
        self.target_vol = target_vol
        self.lookback_days = lookback_days
        self.min_scalar = min_scalar
        self.max_scalar = max_scalar
        self.annualization = annualization

        # Daily returns del portfolio (equity-based)
        self._daily_returns: deque = deque(maxlen=lookback_days * 2)
        self._last_equity: float = 0.0
        self._last_date: str = ""
        self._result = VolTargetResult(target_vol=target_vol)

    def on_equity_update(self, equity: float, timestamp: float = 0.0) -> VolTargetResult:
        """Actualiza con nuevo equity snapshot.

        Debe llamarse al menos una vez al dia (idealmente cada ciclo).
        Calcula daily returns cuando detecta cambio de dia.
        """
        ts = timestamp or time.time()
        # Detectar cambio de dia (UTC)
        import datetime
        current_date = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")

        if self._last_equity > 0 and current_date != self._last_date and self._last_date:
            # Nuevo dia: calcular return del dia anterior
            daily_ret = (equity - self._last_equity) / self._last_equity
            self._daily_returns.append(daily_ret)
            self._result = self._compute(ts)

        if current_date != self._last_date:
            self._last_equity = equity
            self._last_date = current_date

        return self._result

    def force_update(self, equity: float, timestamp: float = 0.0) -> VolTargetResult:
        """Fuerza update intra-dia: recalcula scalar incluyendo return parcial del dia actual."""
        if self._last_equity > 0 and len(self._daily_returns) >= 5:
            intraday_ret = (equity - self._last_equity) / self._last_equity
            # Temporarily include intraday return for a fresher volatility estimate
            returns = list(self._daily_returns)[-self.lookback_days:]
            returns.append(intraday_ret)
            arr = np.array(returns)
            realized_vol = float(np.std(arr, ddof=1)) * math.sqrt(self.annualization)
            if realized_vol > 0:
                scalar = self.target_vol / realized_vol
                scalar = max(self.min_scalar, min(self.max_scalar, scalar))
                ts = timestamp or time.time()
                self._result = VolTargetResult(
                    scalar=scalar, realized_vol=realized_vol,
                    target_vol=self.target_vol, timestamp=ts,
                )
        return self._result

    def _compute(self, timestamp: float) -> VolTargetResult:
        """Calcula el scalar de vol targeting."""
        if len(self._daily_returns) < 5:
            return VolTargetResult(scalar=1.0, target_vol=self.target_vol, timestamp=timestamp)

        returns = np.array(list(self._daily_returns))[-self.lookback_days:]
        realized_vol = float(np.std(returns, ddof=1)) * math.sqrt(self.annualization)

        if realized_vol <= 0:
            scalar = 1.0
        else:
            scalar = self.target_vol / realized_vol

        scalar = max(self.min_scalar, min(self.max_scalar, scalar))

        return VolTargetResult(
            scalar=scalar,
            realized_vol=realized_vol,
            target_vol=self.target_vol,
            timestamp=timestamp,
        )

    @property
    def current(self) -> VolTargetResult:
        return self._result

    @property
    def scalar(self) -> float:
        return self._result.scalar


# ======================================================================
# 2. KELLY CRITERION — Fraccion optima de apuesta
# ======================================================================

@dataclass
class KellyResult:
    """Resultado del calculo de Kelly."""
    full_kelly: float = 0.0         # f* completo
    half_kelly: float = 0.0         # f*/2 (recomendado)
    capped_kelly: float = 0.02      # Kelly con floor/ceiling aplicado
    win_rate: float = 0.0
    payoff_ratio: float = 0.0
    sample_size: int = 0
    is_valid: bool = False          # True si hay suficiente historial


class KellyCriterion:
    """
    Kelly Criterion para sizing optimo de posiciones.

    Formula: f* = (p * b - q) / b
    Donde:
        p = probabilidad de ganar (win rate)
        q = 1 - p (probabilidad de perder)
        b = ratio de payoff (avg_win / avg_loss)

    Se aplica Half-Kelly (f*/2) con floor y ceiling configurables.
    Solo se activa con suficiente historial (min_trades).
    """

    def __init__(
        self,
        min_trades: int = 50,
        floor_pct: float = 0.005,
        ceiling_pct: float = 0.03,
        lookback: int = 200,
        default_risk_pct: float = 0.02,
    ) -> None:
        self.min_trades = min_trades
        self.floor_pct = floor_pct
        self.ceiling_pct = ceiling_pct
        self.lookback = lookback
        self.default_risk_pct = default_risk_pct

        # Historial de PnL por trade
        self._trade_pnls: deque = deque(maxlen=lookback)

    def record_trade(self, pnl: float) -> None:
        """Registra resultado de un trade."""
        self._trade_pnls.append(pnl)

    def compute(self) -> KellyResult:
        """Calcula la fraccion de Kelly optima."""
        pnls = list(self._trade_pnls)
        n = len(pnls)

        if n < self.min_trades:
            return KellyResult(
                capped_kelly=self.default_risk_pct,
                sample_size=n,
                is_valid=False,
            )

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        if not wins or not losses:
            return KellyResult(
                capped_kelly=self.default_risk_pct,
                sample_size=n,
                is_valid=False,
            )

        win_rate = len(wins) / n
        avg_win = np.mean(wins)
        avg_loss = abs(np.mean(losses))

        if avg_loss <= 0:
            return KellyResult(
                capped_kelly=self.default_risk_pct,
                win_rate=win_rate,
                sample_size=n,
                is_valid=False,
            )

        payoff_ratio = avg_win / avg_loss
        if payoff_ratio <= 0:
            return KellyResult(
                capped_kelly=self.floor_pct, win_rate=win_rate,
                payoff_ratio=0.0, sample_size=n, is_valid=False,
            )
        # Kelly: f* = (p * b - q) / b
        q = 1.0 - win_rate
        full_kelly = (win_rate * payoff_ratio - q) / payoff_ratio

        # Si Kelly es negativo, el sistema no tiene edge → usar minimo
        if full_kelly <= 0:
            return KellyResult(
                full_kelly=full_kelly,
                half_kelly=full_kelly / 2,
                capped_kelly=self.floor_pct,
                win_rate=win_rate,
                payoff_ratio=payoff_ratio,
                sample_size=n,
                is_valid=True,
            )

        half_kelly = full_kelly / 2.0
        capped = max(self.floor_pct, min(self.ceiling_pct, half_kelly))

        return KellyResult(
            full_kelly=full_kelly,
            half_kelly=half_kelly,
            capped_kelly=capped,
            win_rate=win_rate,
            payoff_ratio=payoff_ratio,
            sample_size=n,
            is_valid=True,
        )

    @property
    def risk_fraction(self) -> float:
        """Retorna la fraccion de riesgo a usar (Half-Kelly capped o default)."""
        result = self.compute()
        return result.capped_kelly


# ======================================================================
# 3. RISK OF RUIN — Probabilidad de alcanzar max drawdown
# ======================================================================

@dataclass
class RiskOfRuinResult:
    """Resultado del calculo de Risk of Ruin."""
    ror_analytical: float = 0.0     # Formula analitica
    ror_empirical: float = 0.0      # Via bootstrap (si disponible)
    edge: float = 0.0               # Edge estimado
    should_throttle: bool = False   # True si RoR > throttle_threshold
    should_pause: bool = False      # True si RoR > pause_threshold
    sample_size: int = 0


class RiskOfRuin:
    """
    Calcula la probabilidad de que el sistema alcance el drawdown maximo.

    Formula analitica (simplificada):
        edge = win_rate * (avg_win/avg_loss) - (1 - win_rate)
        RoR = ((1 - edge) / (1 + edge)) ^ capital_units

    Donde capital_units = equity / avg_loss (cuantas perdidas promedio
    puede absorber la cuenta).

    Thresholds:
        - RoR > 3%: throttle (reducir sizing 50%)
        - RoR > 10%: pause (pausar trading)
    """

    def __init__(
        self,
        max_drawdown_pct: float = 0.15,
        throttle_threshold: float = 0.03,
        pause_threshold: float = 0.10,
        lookback: int = 200,
        min_trades: int = 30,
    ) -> None:
        self.max_drawdown_pct = max_drawdown_pct
        self.throttle_threshold = throttle_threshold
        self.pause_threshold = pause_threshold
        self.lookback = lookback
        self.min_trades = min_trades

        self._trade_pnls: deque = deque(maxlen=lookback)
        self._result = RiskOfRuinResult()

    def record_trade(self, pnl: float) -> None:
        """Registra resultado de un trade."""
        self._trade_pnls.append(pnl)

    def compute(self, current_equity: float) -> RiskOfRuinResult:
        """Calcula Risk of Ruin."""
        pnls = list(self._trade_pnls)
        n = len(pnls)

        if n < self.min_trades:
            return RiskOfRuinResult(sample_size=n)

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        if not wins or not losses:
            return RiskOfRuinResult(sample_size=n)

        win_rate = len(wins) / n
        avg_win = np.mean(wins)
        avg_loss = abs(np.mean(losses))

        if avg_loss <= 0:
            return RiskOfRuinResult(sample_size=n)

        # Edge = win_rate * payoff - loss_rate
        edge = win_rate * (avg_win / avg_loss) - (1.0 - win_rate)

        # Risk of Ruin analitica
        if edge <= 0:
            # Sin edge positivo, ruin es practicamente segura
            ror = 1.0
        elif edge >= 1.0:
            ror = 0.0
        else:
            # capital_units = cuantas perdidas promedio puede absorber
            max_loss_equity = current_equity * self.max_drawdown_pct
            capital_units = max_loss_equity / avg_loss if avg_loss > 0 else 100
            capital_units = max(1, capital_units)

            ratio = (1.0 - edge) / (1.0 + edge)
            # Evitar overflow
            if capital_units > 300:
                ror = 0.0
            else:
                ror = ratio ** capital_units

        ror = max(0.0, min(1.0, ror))

        self._result = RiskOfRuinResult(
            ror_analytical=ror,
            edge=edge,
            should_throttle=ror > self.throttle_threshold,
            should_pause=ror > self.pause_threshold,
            sample_size=n,
        )
        return self._result

    def compute_empirical(
        self,
        current_equity: float,
        n_simulations: int = 5000,
    ) -> RiskOfRuinResult:
        """Calcula Risk of Ruin empirico via bootstrap de trades.

        Resamplea el historial de trades y simula N paths de equity.
        Cuenta que porcentaje de paths toca el max drawdown.
        """
        pnls = list(self._trade_pnls)
        n = len(pnls)

        if n < self.min_trades:
            return self._result

        # Primero calcular analitico
        result = self.compute(current_equity)

        pnl_array = np.array(pnls)
        ruin_count = 0
        max_loss = current_equity * self.max_drawdown_pct
        rng = np.random.default_rng(None)  # Non-deterministic for live use

        for _ in range(n_simulations):
            # Bootstrap: resamplear con reemplazo
            path = rng.choice(pnl_array, size=n, replace=True)
            cumulative = np.cumsum(path)
            # Drawdown del path
            running_max = np.maximum.accumulate(cumulative)
            drawdowns = running_max - cumulative
            if np.max(drawdowns) >= max_loss:
                ruin_count += 1

        result.ror_empirical = ruin_count / n_simulations
        # Usar el mas conservador de los dos para decisiones
        max_ror = max(result.ror_analytical, result.ror_empirical)
        result.should_throttle = max_ror > self.throttle_threshold
        result.should_pause = max_ror > self.pause_threshold

        self._result = result
        return result

    @property
    def current(self) -> RiskOfRuinResult:
        return self._result


# ======================================================================
# 4. MONTE CARLO BOOTSTRAP — Simulacion de equity curves
# ======================================================================

@dataclass
class MonteCarloResult:
    """Resultado de simulacion Monte Carlo."""
    median_final_equity: float = 0.0
    p5_final_equity: float = 0.0      # Percentil 5 (peor caso)
    p95_final_equity: float = 0.0     # Percentil 95 (mejor caso)
    median_max_dd: float = 0.0        # Max drawdown mediano
    p95_max_dd: float = 0.0           # Percentil 95 de max drawdown
    prob_profitable: float = 0.0      # % de paths rentables
    prob_ruin: float = 0.0            # % de paths que tocan max DD
    sharpe_distribution: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # p5, median, p95
    n_simulations: int = 0


class MonteCarloBootstrap:
    """
    Simulacion Monte Carlo por bootstrap de trades historicos.

    NO usa GBM (geometric brownian motion) — preserva la estructura
    de dependencia de los trades reales.

    Genera N equity curves resampleando trades con reemplazo,
    y calcula distribuciones de: retorno final, max drawdown, Sharpe.
    """

    def __init__(self, max_drawdown_pct: float = 0.15) -> None:
        self.max_drawdown_pct = max_drawdown_pct

    def simulate(
        self,
        trade_pnls: List[float],
        initial_equity: float,
        n_simulations: int = 10000,
        path_length: Optional[int] = None,
    ) -> MonteCarloResult:
        """Ejecuta simulacion Monte Carlo.

        Args:
            trade_pnls: Lista de PnLs de trades historicos
            initial_equity: Equity inicial
            n_simulations: Numero de simulaciones
            path_length: Largo de cada path (default: len(trade_pnls))
        """
        if len(trade_pnls) < 10:
            return MonteCarloResult()

        pnls = np.array(trade_pnls)
        n = path_length or len(pnls)
        max_loss = initial_equity * self.max_drawdown_pct

        final_equities = []
        max_drawdowns = []
        ruin_count = 0
        rng = np.random.default_rng(None)

        for _ in range(n_simulations):
            path = rng.choice(pnls, size=n, replace=True)
            equity_curve = initial_equity + np.cumsum(path)

            final_eq = equity_curve[-1]
            final_equities.append(final_eq)

            # Max drawdown (safe against zero/negative equity)
            running_peak = np.maximum.accumulate(equity_curve)
            with np.errstate(divide='ignore', invalid='ignore'):
                drawdowns = np.where(running_peak > 0,
                                     (running_peak - equity_curve) / running_peak,
                                     0.0)
            max_dd = float(np.nanmax(drawdowns)) if len(drawdowns) > 0 else 0.0
            max_drawdowns.append(max_dd)

            if max_dd >= self.max_drawdown_pct:
                ruin_count += 1

        final_eq = np.array(final_equities)
        max_dds = np.array(max_drawdowns)

        # Calmar ratio distribution (return / max_drawdown)
        # Not Sharpe (which requires daily returns), but Calmar is meaningful
        # for evaluating drawdown-adjusted return per simulation path
        returns_pct = (final_eq - initial_equity) / initial_equity
        sharpe_proxy = returns_pct / (max_dds + 1e-10)  # Actually Calmar ratio

        return MonteCarloResult(
            median_final_equity=float(np.median(final_eq)),
            p5_final_equity=float(np.percentile(final_eq, 5)),
            p95_final_equity=float(np.percentile(final_eq, 95)),
            median_max_dd=float(np.median(max_dds)),
            p95_max_dd=float(np.percentile(max_dds, 95)),
            prob_profitable=float(np.mean(final_eq > initial_equity)),
            prob_ruin=ruin_count / n_simulations,
            sharpe_distribution=(
                float(np.percentile(sharpe_proxy, 5)),
                float(np.median(sharpe_proxy)),
                float(np.percentile(sharpe_proxy, 95)),
            ),
            n_simulations=n_simulations,
        )


# ======================================================================
# 5. CORRELATION REGIME — Detecta cambio de correlacion entre activos
# ======================================================================

@dataclass
class CorrelationRegimeResult:
    """Resultado del analisis de correlacion."""
    avg_correlation: float = 0.0       # Correlacion media entre activos
    is_stress: bool = False            # True si correlacion > stress_threshold
    stress_factor: float = 1.0         # 1.0 normal, <1.0 en stress (reduce exposure)
    correlation_matrix: Dict = field(default_factory=dict)
    timestamp: float = 0.0


class CorrelationRegime:
    """
    Detecta el regimen de correlacion entre activos.

    En condiciones normales, BTC/ETH/ADA tienen correlacion moderada (0.4-0.7).
    En crashes, todo se correlaciona a ~1.0 → la diversificacion desaparece.

    Cuando la correlacion promedio supera el umbral de stress:
    - Reduce la exposicion total del portfolio
    - Previene la ilusion de diversificacion

    Formula:
        avg_corr = mean(pairwise_correlations)
        stress_factor = max(0.4, 1.0 - (avg_corr - normal_threshold) / (1.0 - normal_threshold))
    """

    def __init__(
        self,
        stress_threshold: float = 0.85,
        lookback_periods: int = 30,
        min_periods: int = 10,
    ) -> None:
        self.stress_threshold = stress_threshold
        self.lookback = lookback_periods
        self.min_periods = min_periods

        # Returns por simbolo: symbol -> deque of daily returns
        self._returns: Dict[str, deque] = {}
        self._result = CorrelationRegimeResult()

    def on_return(self, symbol: str, daily_return: float) -> None:
        """Registra un return diario para un simbolo."""
        if symbol not in self._returns:
            self._returns[symbol] = deque(maxlen=self.lookback * 2)
        self._returns[symbol].append(daily_return)

    def compute(self, timestamp: float = 0.0) -> CorrelationRegimeResult:
        """Calcula el regimen de correlacion actual."""
        symbols = list(self._returns.keys())
        if len(symbols) < 2:
            return CorrelationRegimeResult(timestamp=timestamp)

        # Verificar que haya suficientes datos
        min_len = min(len(self._returns[s]) for s in symbols)
        if min_len < self.min_periods:
            return CorrelationRegimeResult(timestamp=timestamp)

        # Construir matriz de returns alineados
        n = min(min_len, self.lookback)
        matrix = {}
        for s in symbols:
            matrix[s] = list(self._returns[s])[-n:]

        # Calcular correlaciones pairwise
        corr_matrix = {}
        pairwise_corrs = []

        for i, s1 in enumerate(symbols):
            corr_matrix[s1] = {}
            for j, s2 in enumerate(symbols):
                if i == j:
                    corr_matrix[s1][s2] = 1.0
                elif j > i:
                    r1 = np.array(matrix[s1])
                    r2 = np.array(matrix[s2])
                    if np.std(r1) > 1e-8 and np.std(r2) > 1e-8:
                        corr = float(np.corrcoef(r1, r2)[0, 1])
                        if not np.isnan(corr):
                            corr_matrix[s1][s2] = corr
                            corr_matrix.setdefault(s2, {})[s1] = corr
                            pairwise_corrs.append(corr)
                            continue
                    # Flat series: skip from average (don't bias toward 0)
                    corr_matrix[s1][s2] = 0.0
                    corr_matrix.setdefault(s2, {})[s1] = 0.0

        if not pairwise_corrs:
            return CorrelationRegimeResult(timestamp=timestamp)

        avg_corr = float(np.mean(pairwise_corrs))
        is_stress = avg_corr > self.stress_threshold

        # Stress factor: reduce exposure cuando correlacion es alta
        if is_stress:
            # Reducir proporcionalmente: de 1.0 a 0.4 mientras corr va de threshold a 1.0
            range_above = 1.0 - self.stress_threshold
            if range_above <= 1e-10:
                stress_factor = 0.4
            else:
                excess = avg_corr - self.stress_threshold
                stress_factor = max(0.4, 1.0 - (excess / range_above) * 0.6)
        else:
            stress_factor = 1.0

        self._result = CorrelationRegimeResult(
            avg_correlation=avg_corr,
            is_stress=is_stress,
            stress_factor=stress_factor,
            correlation_matrix=corr_matrix,
            timestamp=timestamp,
        )
        return self._result

    @property
    def current(self) -> CorrelationRegimeResult:
        return self._result


# ======================================================================
# 6. COVARIANCE TRACKER — Risk Parity allocation
# ======================================================================

@dataclass
class RiskParityResult:
    """Resultado de risk parity."""
    weights: Dict[str, float] = field(default_factory=dict)  # key -> weight (sum=1)
    volatilities: Dict[str, float] = field(default_factory=dict)
    timestamp: float = 0.0


class CovarianceTracker:
    """
    Mantiene una rolling covariance matrix y calcula pesos de Risk Parity.

    Risk Parity: cada 'bucket' (strategy x symbol) contribuye la misma
    cantidad de riesgo al portfolio. Los buckets mas volatiles reciben
    menos capital.

    Formula:
        weight_i = (1 / vol_i) / sum(1 / vol_j for all j)

    Los pesos se combinan con los pesos de regimen como:
        final_weight = regime_weight * risk_parity_blend + (1-blend) * regime_weight
    """

    def __init__(
        self,
        lookback: int = 60,
        min_periods: int = 10,
        blend_factor: float = 0.3,
    ) -> None:
        """
        Args:
            lookback: Ventana rolling para covarianza
            min_periods: Minimo de periodos antes de activar
            blend_factor: Cuanto pesa risk parity vs pesos de regimen (0=solo regimen, 1=solo RP)
        """
        self.lookback = lookback
        self.min_periods = min_periods
        self.blend_factor = blend_factor

        # Returns por bucket: key -> deque of returns
        self._returns: Dict[str, deque] = {}

    def on_return(self, key: str, daily_return: float) -> None:
        """Registra return diario de un bucket (e.g., 'BTC-USD_MR')."""
        if key not in self._returns:
            self._returns[key] = deque(maxlen=self.lookback * 2)
        self._returns[key].append(daily_return)

    def compute_risk_parity(self) -> RiskParityResult:
        """Calcula pesos de Risk Parity basados en volatilidad inversa."""
        keys = list(self._returns.keys())
        if not keys:
            return RiskParityResult()

        vols = {}
        for key in keys:
            rets = list(self._returns[key])
            if len(rets) < self.min_periods:
                vols[key] = None
                continue
            r = np.array(rets[-self.lookback:])
            vol = float(np.std(r, ddof=1))
            vols[key] = vol if vol > 0 else None

        # Solo usar keys con vol valida
        valid_keys = [k for k in keys if vols[k] is not None and vols[k] > 0]
        if not valid_keys:
            return RiskParityResult()

        # Inverse volatility weighting
        inv_vols = {k: 1.0 / max(vols[k], 1e-6) for k in valid_keys}
        total_inv = sum(inv_vols.values())

        weights = {}
        for k in valid_keys:
            weights[k] = inv_vols[k] / total_inv

        # Keys sin vol valida reciben peso 0 (no apostar en volatilidad desconocida)
        invalid_keys = [k for k in keys if k not in valid_keys]
        for k in invalid_keys:
            weights[k] = 0.0

        return RiskParityResult(
            weights=weights,
            volatilities={k: v for k, v in vols.items() if v is not None},
        )

    def blend_weights(
        self,
        regime_weights: Dict[str, float],
    ) -> Dict[str, float]:
        """Combina pesos de regimen con Risk Parity.

        Args:
            regime_weights: Pesos base por regimen (key -> weight)

        Returns:
            Pesos finales combinados (same keys as regime_weights)
        """
        rp = self.compute_risk_parity()

        if not rp.weights:
            return regime_weights

        blended = {}
        for key, regime_w in regime_weights.items():
            rp_w = rp.weights.get(key, regime_w)
            blended[key] = (
                (1.0 - self.blend_factor) * regime_w
                + self.blend_factor * rp_w
            )

        # Normalizar para que sumen 1
        total = sum(blended.values())
        if total > 0:
            blended = {k: v / total for k, v in blended.items()}

        return blended


# ======================================================================
# 7. SLIPPAGE TRACKER — Medicion real de slippage y latency
# ======================================================================

@dataclass
class SlippageStats:
    """Estadisticas de slippage real medido."""
    avg_slippage_bps: float = 0.0
    median_slippage_bps: float = 0.0
    p95_slippage_bps: float = 0.0
    avg_latency_ms: float = 0.0
    sample_size: int = 0
    by_regime: Dict[str, float] = field(default_factory=dict)
    by_symbol: Dict[str, float] = field(default_factory=dict)


class SlippageTracker:
    """
    Mide slippage real comparando precio esperado vs precio de fill.

    Registra cada fill con su precio esperado (de la senal) y el precio
    real de ejecucion. Calcula estadisticas para recalibrar el modelo
    de slippage del backtester.

    Previene que te enganes con backtests optimistas.
    """

    def __init__(self, max_records: int = 5000) -> None:
        self._records: deque = deque(maxlen=max_records)

    def record_fill(
        self,
        expected_price: float,
        fill_price: float,
        symbol: str = "",
        regime: str = "",
        size_usd: float = 0.0,
        latency_ms: float = 0.0,
    ) -> float:
        """Registra un fill y retorna slippage en bps."""
        if expected_price <= 0:
            return 0.0

        slippage_bps = abs(fill_price - expected_price) / expected_price * 10_000

        self._records.append({
            "expected": expected_price,
            "fill": fill_price,
            "slippage_bps": slippage_bps,
            "symbol": symbol,
            "regime": regime,
            "size_usd": size_usd,
            "latency_ms": latency_ms,
            "timestamp": time.time(),
        })

        return slippage_bps

    def get_stats(self) -> SlippageStats:
        """Calcula estadisticas agregadas de slippage."""
        if not self._records:
            return SlippageStats()

        records = list(self._records)
        slippages = [r["slippage_bps"] for r in records]
        latencies = [r["latency_ms"] for r in records if r["latency_ms"] > 0]

        # Por regimen
        by_regime: Dict[str, List[float]] = {}
        by_symbol: Dict[str, List[float]] = {}
        for r in records:
            if r["regime"]:
                by_regime.setdefault(r["regime"], []).append(r["slippage_bps"])
            if r["symbol"]:
                by_symbol.setdefault(r["symbol"], []).append(r["slippage_bps"])

        return SlippageStats(
            avg_slippage_bps=float(np.mean(slippages)),
            median_slippage_bps=float(np.median(slippages)),
            p95_slippage_bps=float(np.percentile(slippages, 95)),
            avg_latency_ms=float(np.mean(latencies)) if latencies else 0.0,
            sample_size=len(records),
            by_regime={k: float(np.mean(v)) for k, v in by_regime.items()},
            by_symbol={k: float(np.mean(v)) for k, v in by_symbol.items()},
        )

    def get_calibrated_slippage_bps(self, regime: str = "", symbol: str = "") -> float:
        """Retorna slippage calibrado para usar en backtester.

        Busca el promedio real para el regimen/simbolo especifico.
        Si no hay datos suficientes, retorna el promedio global.
        """
        stats = self.get_stats()
        if stats.sample_size < 10:
            return 2.0  # Default

        # Intentar especifico primero
        if regime and regime in stats.by_regime:
            return stats.by_regime[regime]
        if symbol and symbol in stats.by_symbol:
            return stats.by_symbol[symbol]

        return stats.avg_slippage_bps
