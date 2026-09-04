"""
Configuración central del sistema de trading BotStrike.
Define todos los parámetros ajustables: API, estrategias, riesgo, símbolos.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Dict, List
import os
from dotenv import load_dotenv

load_dotenv()


class ExchangeVenue(Enum):
    """Exchange de ejecución."""
    BINANCE = "binance"
    HYPERLIQUID = "hyperliquid"
    STRIKE = "strike"


@dataclass
class SymbolConfig:
    """Configuración específica por símbolo/activo."""
    symbol: str
    leverage: int = 2   # Safe default (was 10 — exceeded max_leverage=5)
    max_position_usd: float = 200.0  # Safe default for small accounts (was 10k)
    # Intraday strategies allowed on this symbol (comma-separated StrategyType values).
    # Replaces the hardcoded SYMBOL_STRATEGY_MAP so it can be edited from the UI.
    # Whether a strategy actually trades is still gated by its allocation (> 0) and
    # by the edge monitor; this list only says "eligible here".
    strategies: str = "MEAN_REVERSION,FIBONACCI_RETRACEMENT"
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
    kyle_lambda_window: int = 200    # trades para rolling regression (500 = 6-10h, too stale; 200 = 2-3h)
    kyle_lambda_ema_span: int = 50   # span del EMA smoothing (faster adaptation)
    adverse_selection_horizon_sec: float = 60.0  # horizonte mark-to-market (300s too long for 3s bot cycle)


@dataclass
class TradingConfig:
    """Configuración global de trading."""
    # Exchange venue — Binance for liquidity, Strike when ready
    exchange_venue: str = "binance"      # "binance" or "strike"
    # Capital
    initial_capital: float = 1000.0
    # Compounding (2026-09-02): when True the engine sizes every position on the
    # ALL-TIME equity (initial_capital + realized PnL from the trade DB) instead of
    # the fixed initial_capital, so gains are reinvested across restarts. In paper
    # mode this also makes the risk manager start from the historical equity/peak.
    compounding_enabled: bool = True
    # Perpetual funding: charge open positions every `funding_interval_hours` at the venue rate.
    # STRIKE SETTLES EVERY HOUR, not every 8 h (verified 2026-09-03: /stat/v1/stats/coin/history/funding
    # returns 167 rows for days=7, and premiumIndex.nextFundingTime is always the top of the next hour,
    # and its fundingRate is the HOURLY rate). Charging an hourly rate on an 8-hour clock undercharged
    # the carry by ~8x, which flatters exactly the cost the multi-asset thesis depends on.
    # Measured on Strike over 90 d: longs paid a median 8.1 %/yr of notional (XAG +15.1 %, WTI -15.7 %).
    funding_enabled: bool = True
    funding_interval_hours: int = 1
    # Riesgo global — escalera de drawdown (research_sota_2026 §8.2, item 8):
    # -2% día / -5% semana / -10% desde el máximo histórico. Los tres se miden sobre
    # el histórico persistido en la trade DB, no sobre la sesión (audit 2026-09-02:
    # el pico de equity se reiniciaba en cada restart y el circuit breaker del 10%
    # nunca acumulaba entre reinicios).
    max_drawdown_pct: float = 0.10      # 10% from the all-time peak → halt + flatten
    max_daily_loss_pct: float = 0.02    # 2% of equity per UTC day → no new entries
    max_weekly_loss_pct: float = 0.05   # 5% of equity per ISO week → no new entries
    max_leverage: int = 5               # Safer for micro account (was 20)
    max_total_exposure_pct: float = 0.6  # 60% max exposure (was 0.8)
    max_open_positions: int = 4          # Max concurrent positions (one per symbol)
    risk_per_trade_pct: float = 0.015   # 1.5% = $4.50 risk budget (was 1%)
    # Shutdown policy (audit F01 / P0-03). True = flatten every open position
    # (MARKET reduceOnly) BEFORE cancelling orders on a normal shutdown, so a
    # restart/deploy never leaves a naked position. False = keep positions open
    # AND keep their exchange SL/TP alive (cancel_all is skipped). The drawdown
    # halt ALWAYS flattens regardless of this flag.
    close_positions_on_shutdown: bool = True
    # Asignación por estrategia — MR (conservative) + Fib (growth)
    # FROZEN 2026-08-31 (audit R2 batch 1, strategies-01 — P0 confirmed by two
    # independent verifiers): Mean Reversion has NO GROSS edge. Over 149.7 days of
    # real Binance Futures klines and 2,284 simulated trades with production code,
    # mean GROSS return per trade is -0.90/-0.63/-2.05/+0.45 bps (ETH/SOL/ADA/BTC)
    # with SE 1.2-2.6 bps — statistically zero. Net of 11 bps friction: PF 0.40-0.60,
    # -10.5/-13.1 bps per trade, t-stat -5 to -8.7. The decisive control: INVERTING
    # every signal does not improve the result, so there is no directional information
    # to exploit — no SL/TP tuning can fix a null gross edge. See
    # tasks/audit/r2_batch1_report.md §3.1 for the unfreeze conditions.
    allocation_mean_reversion: float = 0.00
    # FROZEN 2026-08-31: no published evidence for Fibonacci retracement
    # (research_sota_2026 §2.7) and 20% WR / -$2.11 over 5 paper closes.
    allocation_fibonacci_retracement: float = 0.00
    allocation_trend_following: float = 0.00   # archived
    allocation_market_making: float = 0.00     # archived
    allocation_order_flow_momentum: float = 0.00  # archived
    # TREND_DAILY (2026-09-02): the only strategy that passed the §11.3 GO/NO-GO
    # checklist (Sharpe 1.21 net, maxDD 12.6%, look-ahead audit stable). 1.0 = the
    # vol-targeted weights are applied at 100%; 0 = disabled. It runs in its own
    # daily engine and does NOT use the intraday allocation machinery.
    allocation_trend_daily: float = 1.00
    # DIVERGENCE (2026-09-02): research NO-GO 2/7 (scripts/divergence_research.py: 1,102
    # trades on 1h: WR 38%, PF 0.77, gross -25 bps/trade, t -2.15, negative every year and
    # symbol). Implemented in full so it can be studied in paper; 0 = disabled.
    allocation_divergence: float = 0.00
    div_timeframe_min: int = 240
    div_rsi_period: int = 14
    div_pivot_k: int = 3
    div_rsi_os: float = 35.0
    div_rsi_ob: float = 65.0
    div_min_gap_bars: int = 5
    div_max_gap_bars: int = 60
    div_min_rsi_gap: float = 3.0
    div_trigger_window: int = 6
    div_require_macd: bool = True
    div_require_volume: bool = False
    div_atr_buffer: float = 0.5
    div_rr: float = 2.0
    div_max_hold: int = 24
    div_hidden: bool = False
    div_with_trend: bool = False    # only in the EMA200 direction (research: too few trades to judge)
    div_cooldown_min: int = 60
    # ── Regime detection horizon (2026-09-02) ──
    # Audit: on 1-minute bars the detector flipped 885 times in 48 h (median regime
    # 5 min, 320 A→B→A round-trips under 5 min) and every flip went to Telegram.
    # The intraday strategies hold ~30 min, so the regime is measured on 15-minute
    # bars (ADX14 = 3.5 h, momentum20 = 5 h) and a new regime must persist
    # `regime_min_dwell_min` minutes before it is confirmed.
    regime_timeframe_min: int = 15
    regime_min_dwell_min: int = 30
    telegram_regime_min_interval_min: int = 60   # at most one regime message per symbol per hour
    # ── Trend daily parameters (research_r2_trend_evidence §11.2 — validated set) ──
    trend_lookbacks: str = "5,10,20,30,60,90"   # Donchian ensemble lookbacks (days)
    trend_target_vol: float = 0.20               # annualized vol target per asset
    trend_vol_window: int = 90                   # days for realized vol
    trend_n_assets: int = 3                      # top-N by 30d median dollar volume
    trend_leverage_cap: float = 2.0              # cap on the vol scalar (spec: 2.0)
    trend_rebalance_threshold: float = 0.20      # only re-trade vol-induced size changes > 20%
    trend_execution_hour_utc: int = 0            # execute at the daily open (00:00 UTC)...
    trend_execution_delay_min: int = 5           # ...plus this delay (candle must exist)
    trend_min_order_usd: float = 10.0            # skip rebalances smaller than this notional
    trend_min_listing_days: int = 365
    trend_liq_enter_usd: float = 2_000_000.0     # 30d median dollar volume to enter the universe
    trend_liq_exit_usd: float = 1_000_000.0      # ... and to stay
    # Mixed (multi-asset) pools: dollar volume is NOT comparable across asset classes, so the
    # universe is picked by class diversity + history with a correlation cap, and liquidity is
    # enforced with the VENUE's own 24 h volume (Strike: BTC 1.1M$, XAU 199k$, SP500 80k$/24h).
    trend_corr_window: int = 120
    trend_corr_cap: float = 0.85
    trend_liq_venue_usd: float = 5_000.0        # hard minimum 24 h volume at the venue
    trend_liq_venue_multiple: float = 50.0      # and >= 50x the notional of one position
    # Short side of the daily book. OFF by default and that is a measured decision, not caution:
    # tasks/research_shorts_and_speed_2026-09-04.md — at half size it holds the Sharpe (1.92) and cuts
    # the drawdown in all ten stress scenarios (7.6 % -> 5.6 %), and it is the only natural hedge the
    # book has against expensive funding (a short RECEIVES it). But it SUBTRACTED return in the last
    # four years (2022+: 1.73 vs 1.94): a hedge with a premium, not an edge. Symmetric shorts (full
    # size) measured 1.57 and are the version that was rejected earlier.
    # NOTE: enabling this needs the EXECUTION path reviewed — the exit ladder, the funding sign and
    # the paper fill logic are all written for a long book.
    trend_allow_shorts: bool = False
    trend_short_size: float = 0.5
    trend_pool: str = ("BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,LTCUSDT,TRXUSDT,"
                       "ETCUSDT,EOSUSDT,XLMUSDT,NEOUSDT,IOTAUSDT,ZECUSDT,DASHUSDT,SOLUSDT,"
                       "AVAXUSDT,DOTUSDT,LINKUSDT,ATOMUSDT")
    # Multi-asset pool validated 11/11 on 2026-09-03 (tasks/research_trend_multi_2026-09-03.md):
    # Sharpe 1.81 net vs 1.37 crypto-only, maxDD 8.8 % vs 11.9 %, funding charged. Strike-style
    # markets take their daily history from Yahoo (strategies/daily_sources.py) and execute on the
    # venue. Set trend_pool to this string and trend_n_assets to 6 to switch the book over.
    TREND_POOL_MULTI: ClassVar[str] = ("BTCUSDT,ETHUSDT,ADAUSDT,SOLUSDT,XRPUSDT,BNBUSDT,ZECUSDT,"
                                       "XAU-USD,XAG-USD,SP500-USD,NAS100-USD,WTI-USD")
    # ── Edge monitor (research §4.4 / audit 2026-09-02) ──
    # Per-strategy statistics over the last `edge_window` closed trades. A strategy is
    # killed (no new entries, Telegram alert) when n >= edge_kill_min_trades and either
    # the t-stat of the gross return per trade is <= edge_kill_t_stat or fees eat more
    # than edge_kill_fee_share of the gross profit of the winners.
    edge_monitor_enabled: bool = True
    edge_window: int = 200
    edge_kill_min_trades: int = 100
    edge_kill_t_stat: float = -2.0
    edge_kill_fee_share: float = 0.50
    edge_check_interval_sec: int = 600
    # ── Microstructure (VPIN / Hawkes / Kyle λ / OBI / microprice) ──
    # Audit R2: zero measured predictive power and 16.5% CPU. Off by default; the
    # intraday strategies still run (regime detection is cheap) and the Order Flow
    # page shows "disabled" while this is False.
    microstructure_enabled: bool = False
    # ── Telegram notification switches (the notifier reads these live) ──
    telegram_enabled: bool = True
    telegram_notify_trades: bool = True
    telegram_notify_signals: bool = True
    telegram_notify_regime: bool = False
    telegram_notify_portfolio: bool = True
    # 60 min meant 24 "nothing changed" messages a day for a bot that trades once a day; the real
    # alerts then drown in noise (Edgar 2026-09-02: "me llegan muchisimas notificaciones").
    # Twice a day keeps the "still alive" signal. Editable from Settings.
    telegram_portfolio_every_min: int = 720
    telegram_notify_daily_digest: bool = True
    telegram_digest_hour_utc: int = 7
    # Fees — Binance Futures defaults (VIP 0)
    maker_fee: float = 0.0002           # 2 bps — Binance Futures maker
    taker_fee: float = 0.0004           # 4 bps — Binance Futures taker (was 5 bps Strike)
    # Slippage — calibrado para Binance Futures micro orders
    slippage_bps: float = 1.5           # 1.5 bps — Binance Futures has deep book (was 2.0 bps)
    # Funding rate thresholds
    funding_rate_warn: float = 0.0001   # 1 bps/8h — reduce sizing 30%
    funding_rate_block: float = 0.0005  # 5 bps/8h — bloquear entradas contra funding
    # Stale data protection — tight for scalping (alpha decays <10s)
    data_stale_warn_sec: float = 15.0    # warn si datos > 15s stale (was 60s — too permissive)
    data_stale_block_sec: float = 30.0   # no operar si datos > 30s stale (was 300s — absurd for scalping)
    # Intervalos
    data_interval_sec: float = 1.0
    strategy_interval_sec: float = 3.0   # evaluar cada 3s — OFM alpha decays <10s, 5s was too slow
    mm_interval_sec: float = 0.5       # Market Making quote refresh (mas rapido)
    risk_check_interval_sec: float = 2.0
    # Volatility Targeting
    vol_target_annual: float = 0.15    # Vol anualizada objetivo del portfolio
    vol_target_min_scalar: float = 0.5
    vol_target_max_scalar: float = 1.5    # Cap vol scaling (was 1.2 for $300, more room with $1,000)
    vol_target_lookback_days: int = 20
    # Kelly Criterion
    kelly_min_trades: int = 100        # Trades minimos para activar Kelly (50 had ±15% WR variance at 95% CI)
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

    # Binance Futures API (when exchange_venue="binance")
    binance_api_key: str = field(
        default_factory=lambda: os.getenv("BINANCE_API_KEY", "")
    )
    binance_api_secret: str = field(
        default_factory=lambda: os.getenv("BINANCE_API_SECRET", "")
    )

    # Hyperliquid API (when exchange_venue="hyperliquid")
    hyperliquid_private_key: str = field(
        default_factory=lambda: os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    )
    hyperliquid_wallet_address: str = field(
        default_factory=lambda: os.getenv("HYPERLIQUID_WALLET_ADDRESS", "")
    )

    # Usar testnet por defecto para desarrollo
    use_testnet: bool = True

    # Símbolos a operar (4 assets, $1,000 account, max 4 concurrent positions)
    # SL/TP calibrated per-symbol from backtest analysis
    # Per-symbol eligibility mirrors the pre-freeze research: Fibonacci on BTC only,
    # Mean Reversion on ETH/SOL/ADA only (editable from the UI).
    symbols: List[SymbolConfig] = field(default_factory=lambda: [
        SymbolConfig(
            symbol="BTC-USD", leverage=2, max_position_usd=500,
            vpin_bucket_size=50_000.0,
            mr_atr_mult_sl=1.5, mr_atr_mult_tp=4.0,
            strategies="FIBONACCI_RETRACEMENT,DIVERGENCE",
        ),
        SymbolConfig(
            symbol="ETH-USD", leverage=2, max_position_usd=400,
            vpin_bucket_size=30_000.0,
            kyle_lambda_window=150, kyle_lambda_ema_span=40,
            mr_atr_mult_sl=1.5, mr_atr_mult_tp=4.0,
            strategies="MEAN_REVERSION,DIVERGENCE",
        ),
        SymbolConfig(
            symbol="SOL-USD", leverage=2, max_position_usd=250,
            vpin_bucket_size=15_000.0,
            kyle_lambda_window=150, kyle_lambda_ema_span=40,
            mr_atr_mult_sl=1.8, mr_atr_mult_tp=4.0,
            strategies="MEAN_REVERSION,DIVERGENCE",
        ),
        SymbolConfig(
            symbol="ADA-USD", leverage=2, max_position_usd=150,
            vpin_bucket_size=5_000.0,
            kyle_lambda_window=100, kyle_lambda_ema_span=30,
            mr_atr_mult_sl=2.0, mr_atr_mult_tp=4.0,
            strategies="MEAN_REVERSION,DIVERGENCE",
        ),
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

    def __post_init__(self) -> None:
        """Validate configuration coherence at startup, then apply the user's
        runtime overrides (data/config_overrides.json — edited from the UI) and
        validate again so an invalid override can never boot the engine."""
        self.validate()
        try:
            from config.overrides import apply_saved_overrides
            apply_saved_overrides(self)
        except Exception as e:  # pragma: no cover — defensive: never block startup
            import structlog
            structlog.get_logger(__name__).warning("config_overrides_skipped", error=str(e),
                                                   error_type=type(e).__name__)

    def validate(self) -> None:
        """Coherence checks shared by startup and by PUT /api/config."""
        max_exposure_usd = self.trading.initial_capital * self.trading.max_total_exposure_pct
        for sym in self.symbols:
            if sym.max_position_usd > max_exposure_usd:
                raise ValueError(
                    f"Config incoherence: {sym.symbol} max_position_usd={sym.max_position_usd} "
                    f"exceeds max_total_exposure={max_exposure_usd:.0f} "
                    f"(capital={self.trading.initial_capital} × exposure_pct={self.trading.max_total_exposure_pct}). "
                    f"Reduce max_position_usd to <= {max_exposure_usd:.0f}"
                )
            # Validate leveraged notional doesn't exceed capital
            leveraged_notional = sym.max_position_usd * sym.leverage
            if leveraged_notional > self.trading.initial_capital * self.trading.max_leverage:
                raise ValueError(
                    f"Config incoherence: {sym.symbol} leveraged notional "
                    f"({sym.max_position_usd} × {sym.leverage}x = ${leveraged_notional}) "
                    f"exceeds max allowed (${self.trading.initial_capital} × {self.trading.max_leverage}x "
                    f"= ${self.trading.initial_capital * self.trading.max_leverage:.0f}). "
                    f"Reduce max_position_usd or leverage."
                )
        t = self.trading
        if not (0 < t.max_daily_loss_pct <= t.max_weekly_loss_pct <= t.max_drawdown_pct <= 0.9):
            raise ValueError(
                "Config incoherence: the drawdown ladder must satisfy "
                f"0 < daily ({t.max_daily_loss_pct}) <= weekly ({t.max_weekly_loss_pct}) "
                f"<= max drawdown ({t.max_drawdown_pct}) <= 0.9")
        for name in ("allocation_mean_reversion", "allocation_fibonacci_retracement",
                     "allocation_trend_daily", "allocation_divergence", "allocation_trend_following",
                     "allocation_market_making", "allocation_order_flow_momentum"):
            v = getattr(t, name)
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"Config incoherence: {name}={v} must be within [0, 1]")
        # A retired strategy has no gross edge; funding it is not a preference, it is a mistake.
        from core.types import RETIRED_STRATEGIES
        for key, reason in RETIRED_STRATEGIES.items():
            attr = f"allocation_{key.lower()}"
            if float(getattr(t, attr, 0.0) or 0.0) > 0:
                raise ValueError(f"{key} was retired by the research and cannot be allocated capital. {reason}")
        try:
            lbs = [int(x) for x in str(t.trend_lookbacks).split(",") if x.strip()]
        except ValueError:
            raise ValueError("Config incoherence: trend_lookbacks must be comma-separated integers")
        if not lbs or min(lbs) < 2 or max(lbs) > 400:
            raise ValueError("Config incoherence: trend_lookbacks must contain 1+ integers in [2, 400]")

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

    @property
    def exchange_venue_enum(self) -> ExchangeVenue:
        """Returns the configured exchange venue as enum."""
        return ExchangeVenue(self.trading.exchange_venue)

    @property
    def is_binance(self) -> bool:
        return self.trading.exchange_venue == "binance"

    @property
    def is_hyperliquid(self) -> bool:
        return self.trading.exchange_venue == "hyperliquid"

    @property
    def is_strike(self) -> bool:
        return self.trading.exchange_venue == "strike"

    def apply_testnet(self) -> None:
        """Cambia URLs a testnet (both Strike and Binance)."""
        if self.use_testnet:
            if self.is_strike:
                self.api_base_url = "https://api-v2-testnet.strikefinance.org"
                self.api_price_url = "https://api-v2-testnet.strikefinance.org/price"
                self.ws_market_url = "wss://api-v2-testnet.strikefinance.org/ws/price"
                self.ws_user_url = "wss://api-v2-testnet.strikefinance.org/ws/user-api"
            elif self.is_binance:
                # Binance Futures testnet — important: prevents trading mainnet
                # when use_testnet=True. BinanceClient reads these in __init__.
                self.binance_testnet = True  # Flag for BinanceClient to use testnet URLs
