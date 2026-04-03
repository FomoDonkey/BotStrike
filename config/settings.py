"""
Configuración central del sistema de trading BotStrike.
Define todos los parámetros ajustables: API, estrategias, riesgo, símbolos.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class SymbolConfig:
    """Configuración específica por símbolo/activo."""
    symbol: str
    leverage: int = 10
    max_position_usd: float = 200.0  # Safe default for small accounts (was 10k)
    # Mean Reversion
    mr_zscore_entry: float = 2.0
    mr_zscore_exit: float = 0.5
    mr_lookback: int = 100
    mr_atr_mult_sl: float = 2.0
    mr_atr_mult_tp: float = 3.0
    # Trend Following
    tf_ema_fast: int = 12
    tf_ema_slow: int = 26
    tf_atr_mult_trail: float = 2.0
    tf_momentum_threshold: float = 0.02
    tf_volume_filter: float = 0.8  # ratio sobre media de volumen
    # Market Making
    mm_base_spread_bps: float = 7.0   # basis points
    mm_order_levels: int = 3
    mm_order_size_usd: float = 15.0
    mm_inventory_limit: float = 0.7  # fracción del max position
    mm_gamma: float = 0.1  # aversión al riesgo Avellaneda-Stoikov
    mm_kappa: float = 1.5  # intensidad de llegada de órdenes
    mm_max_spread_bps: float = 100.0  # spread máximo defensivo
    # Microestructura — VPIN
    vpin_enabled: bool = True
    vpin_bucket_size: float = 50_000.0  # USD por bucket (ajustar por activo)
    vpin_n_buckets: int = 50            # buckets para cálculo
    vpin_toxic_threshold: float = 0.8   # umbral de flujo tóxico
    # Microestructura — Hawkes
    hawkes_enabled: bool = True
    hawkes_mu: float = 1.0             # intensidad base (eventos/seg)
    hawkes_alpha: float = 0.5          # factor de excitación
    hawkes_beta: float = 2.0           # tasa de decaimiento
    hawkes_spike_mult: float = 4.0     # multiplicador para declarar spike
    # Régimen
    regime_vol_lookback: int = 50
    regime_momentum_lookback: int = 20
    regime_vol_threshold_low: float = 0.4
    regime_vol_threshold_high: float = 0.7
    # Order Book Imbalance
    obi_levels: int = 5              # niveles del book a considerar
    obi_decay: float = 0.5           # decay exponencial por nivel
    obi_delta_window: int = 10       # ventana para delta de imbalance
    # Kyle Lambda — market impact estimation
    kyle_lambda_window: int = 500    # trades para rolling regression
    kyle_lambda_ema_span: int = 100  # span del EMA smoothing
    adverse_selection_horizon_sec: float = 300.0  # horizonte mark-to-market


@dataclass
class TradingConfig:
    """Configuración global de trading."""
    # Capital
    initial_capital: float = 300.0
    # Riesgo global — calibrado para $300 micro account
    max_drawdown_pct: float = 0.10      # $30 max loss before circuit break (was 0.15)
    max_leverage: int = 5               # Safer for micro account (was 20)
    max_total_exposure_pct: float = 0.6  # 60% max exposure (was 0.8)
    risk_per_trade_pct: float = 0.015   # 1.5% = $4.50 risk budget (was 1%)
    # Asignación por estrategia (se ajusta dinámicamente)
    allocation_mean_reversion: float = 0.40
    allocation_trend_following: float = 0.00
    allocation_market_making: float = 0.00
    allocation_order_flow_momentum: float = 0.60
    # Fees (Strike Finance defaults)
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    # Slippage — realistic for micro orders on Binance
    slippage_bps: float = 2.0           # 2 bps — realistic for Binance micro-orders ($250 notional)
    # Funding rate thresholds
    funding_rate_warn: float = 0.0001   # 1 bps/8h — reduce sizing 30%
    funding_rate_block: float = 0.0005  # 5 bps/8h — bloquear entradas contra funding
    # Stale data protection
    data_stale_warn_sec: float = 60.0    # warn si datos > 60s stale (15m bars)
    data_stale_block_sec: float = 300.0  # no operar si datos > 5min stale
    # Intervalos
    data_interval_sec: float = 1.0
    strategy_interval_sec: float = 5.0   # evaluar cada 5s (OFM signals decay fast)
    mm_interval_sec: float = 0.5       # Market Making quote refresh (mas rapido)
    risk_check_interval_sec: float = 2.0
    # Volatility Targeting
    vol_target_annual: float = 0.15    # Vol anualizada objetivo del portfolio
    vol_target_min_scalar: float = 0.5
    vol_target_max_scalar: float = 1.2    # Cap vol scaling (was 2.0 — too aggressive for $300)
    vol_target_lookback_days: int = 20
    # Kelly Criterion
    kelly_min_trades: int = 50         # Trades minimos para activar Kelly
    kelly_floor_pct: float = 0.005     # Minimo 0.5% riesgo por trade
    kelly_ceiling_pct: float = 0.03    # Maximo 3% riesgo por trade
    # Risk of Ruin
    ror_throttle_threshold: float = 0.03  # Reducir sizing si RoR > 3%
    ror_pause_threshold: float = 0.10     # Pausar trading si RoR > 10%
    # Correlation Regime
    corr_stress_threshold: float = 0.85   # Correlacion para activar stress mode
    corr_lookback_periods: int = 30
    # Impact Stress (Kyle Lambda)
    impact_stress_threshold: float = 0.8  # Block if permanent_impact_bps > this * edge


@dataclass
class Settings:
    """Configuración raíz del sistema."""
    # API Strike Finance
    api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "STRIKE_API_URL", "https://api.strikefinance.org"
        )
    )
    api_price_url: str = field(
        default_factory=lambda: os.getenv(
            "STRIKE_PRICE_URL", "https://api.strikefinance.org/price"
        )
    )
    ws_market_url: str = field(
        default_factory=lambda: os.getenv(
            "STRIKE_WS_MARKET", "wss://api.strikefinance.org/ws/price"
        )
    )
    ws_user_url: str = field(
        default_factory=lambda: os.getenv(
            "STRIKE_WS_USER", "wss://api.strikefinance.org/ws/user-api"
        )
    )
    api_public_key: str = field(
        default_factory=lambda: os.getenv("STRIKE_PUBLIC_KEY", "")
    )
    api_private_key: str = field(
        default_factory=lambda: os.getenv("STRIKE_PRIVATE_KEY", "")
    )

    # Usar testnet por defecto para desarrollo
    use_testnet: bool = True

    # Símbolos a operar
    symbols: List[SymbolConfig] = field(default_factory=lambda: [
        SymbolConfig(symbol="BTC-USD", leverage=2, max_position_usd=200,
                     vpin_bucket_size=50_000.0),
    ])

    trading: TradingConfig = field(default_factory=TradingConfig)

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/botstrike.log"
    metrics_file: str = "logs/metrics.jsonl"

    # Telegram notifications (optional — disabled if token/chat_id not set)
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    telegram_chat_id: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "")
    )

    def get_symbol_config(self, symbol: str) -> SymbolConfig:
        """Obtiene configuración de un símbolo específico."""
        for s in self.symbols:
            if s.symbol == symbol:
                return s
        raise ValueError(f"Symbol {symbol} not configured")

    @property
    def symbol_names(self) -> List[str]:
        return [s.symbol for s in self.symbols]

    def get_microstructure_config(self) -> Dict[str, Dict]:
        """Genera config de microestructura por símbolo para MicrostructureEngine."""
        cfg: Dict[str, Dict] = {}
        for s in self.symbols:
            cfg[s.symbol] = {
                "vpin_bucket_size": s.vpin_bucket_size,
                "vpin_n_buckets": s.vpin_n_buckets,
                "vpin_toxic_threshold": s.vpin_toxic_threshold,
                "hawkes_mu": s.hawkes_mu,
                "hawkes_alpha": s.hawkes_alpha,
                "hawkes_beta": s.hawkes_beta,
                "hawkes_spike_mult": s.hawkes_spike_mult,
                "mm_gamma": s.mm_gamma,
                "mm_kappa": s.mm_kappa,
                "mm_min_spread_bps": s.mm_base_spread_bps,
                "mm_max_spread_bps": s.mm_max_spread_bps,
                "fee_bps": self.trading.maker_fee * 10_000,  # MM uses maker fee
                "kyle_lambda_window": s.kyle_lambda_window,
                "kyle_lambda_ema_span": s.kyle_lambda_ema_span,
                "adverse_selection_horizon_sec": s.adverse_selection_horizon_sec,
            }
        return cfg

    def apply_testnet(self) -> None:
        """Cambia URLs a testnet."""
        if self.use_testnet:
            self.api_base_url = "https://api-v2-testnet.strikefinance.org"
            self.api_price_url = "https://api-v2-testnet.strikefinance.org/price"
            self.ws_market_url = "wss://api-v2-testnet.strikefinance.org/ws/price"
            self.ws_user_url = "wss://api-v2-testnet.strikefinance.org/ws/user-api"
