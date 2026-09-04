import { getBridgeUrl, getBridgeToken, isLocalBridge } from "./config";
import { API_TIMEOUT_MS, BACKTEST_TIMEOUT_MS, HEALTH_TIMEOUT_MS } from "./constants";

export class ApiError extends Error {
  readonly status: number | undefined;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
  get isAuth() {
    return this.status === 401 || this.status === 403 || /token/i.test(this.message);
  }
}

// ── Response shapes (bridge.py — see tasks/ui_config_contract.md) ──

export interface HealthResponse {
  status: string;
  engine_running: boolean;
  mode: string;
  uptime_sec: number;
  clients: number;
  version?: string;
  exchange?: string;
  /** Bridge ≥ 2.14 */
  telegram_failures?: number;
  microstructure_enabled?: boolean;
  trend_daily_enabled?: boolean;
}

export interface BotStatusResponse {
  running: boolean;
  mode: string;
  uptime_sec: number;
  equity: number;
  pnl: number;
  auth_token: string | null;
  auth_token_exposed?: boolean;
  exchange?: string;
}

export interface BotActionResponse {
  status: string;
  mode?: string;
  exchange?: string;
}

/** Any editable scalar the config schema can describe. */
export type ConfigScalar = number | boolean | string | string[] | null;

export interface SymbolConfig {
  symbol: string;
  leverage: number;
  max_position_usd: number;
  vpin_bucket_size: number;
  vpin_toxic_threshold: number;
  hawkes_spike_mult: number;
  mm_gamma: number;
  obi_levels: number;
  /** Every other SymbolConfig field the bridge exposes — read generically by the schema editor. */
  [key: string]: ConfigScalar | undefined;
}

export interface TradingConfig {
  initial_capital: number;
  max_drawdown_pct: number;
  max_leverage: number;
  max_total_exposure_pct: number;
  risk_per_trade_pct: number;
  allocation_mean_reversion: number;
  allocation_fibonacci_retracement: number;
  allocation_order_flow_momentum: number;
  allocation_trend_following: number;
  allocation_market_making: number;
  maker_fee: number;
  taker_fee: number;
  slippage_bps: number;
  vol_target_annual: number;
  kelly_min_trades: number;
  kelly_floor_pct: number;
  kelly_ceiling_pct: number;
  /** Bridge ≥ 2.14 (optional so an older bridge still type-checks) */
  compounding_enabled?: boolean;
  allocation_trend_daily?: number;
  /** Every other TradingConfig field the bridge exposes — read generically by the schema editor. */
  [key: string]: ConfigScalar | undefined;
}

export interface ConfigOverrides {
  trading?: Partial<TradingConfig>;
  symbols?: Record<string, Partial<SymbolConfig>>;
}

export interface ConfigResponse {
  use_testnet: boolean;
  has_api_key: boolean;
  has_telegram: boolean;
  symbols: SymbolConfig[];
  trading: TradingConfig;
  /** Bridge ≥ 2.14 — what the user changed (data/config_overrides.json) */
  overrides?: ConfigOverrides;
  /** Bridge ≥ 2.14 — true if some override only applies after an engine restart */
  restart_required?: boolean;
}

export type ConfigFieldType = "number" | "int" | "percent" | "bool" | "string" | "select" | "list";

export interface ConfigFieldOption {
  value: string | number;
  label: string;
}

export interface ConfigField {
  /** Dot path: `trading.max_drawdown_pct` or `symbols.{symbol}.leverage` in per-symbol groups */
  path: string;
  label?: string;
  type: ConfigFieldType;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  help?: string;
  restart_required?: boolean;
  options?: ConfigFieldOption[];
}

export interface ConfigGroup {
  id: string;
  label: string;
  fields: ConfigField[];
  per_symbol?: boolean;
}

export interface ConfigSchemaResponse {
  groups: ConfigGroup[];
}

/** PUT /api/config body — only what changes. */
export interface ConfigUpdateRequest {
  trading?: Record<string, ConfigScalar>;
  symbols?: Record<string, Record<string, ConfigScalar>>;
  /** Any other root the schema may introduce (`<root>.<field>`) */
  [root: string]: Record<string, ConfigScalar> | Record<string, Record<string, ConfigScalar>> | undefined;
}

export interface ConfigUpdateResponse {
  status: string;
  applied: string[];
  restart_required: boolean;
  config: ConfigResponse;
}

export interface PerformanceResponse {
  equity: number;
  pnl: number;
  total_trades: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  total_fees: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  equity_curve: number[];
  /** Bridge ≥ 2.13.1 — merged all-time (trade DB) + live view */
  initial_capital?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  session_pnl?: number;
  session_trades?: number;
  sortino_ratio?: number;
  expectancy?: number;
  /** [epoch_seconds, equity] pairs for a real time axis */
  equity_curve_ts?: [number, number][];
  /** Bridge ≥ 2.14 */
  current_drawdown?: number; // all-time, includes unrealized
  peak_equity?: number;
  sample_days?: number;
  sharpe_valid?: boolean; // false → < 30 days or < 30 trades → UI shows "n/a"
  first_trade_ts?: number;
}

export type EdgeVerdict = "insufficient" | "ok" | "warn" | "kill";

export interface EdgeStats {
  n: number;
  wins: number;
  win_rate: number;
  net_pnl: number;
  gross_pnl: number;
  fees: number;
  mean_gross_bps: number;
  se_bps: number;
  t_stat: number;
  profit_factor: number;
  fee_share: number;
  expectancy_usd: number;
  avg_hold_min: number;
  verdict: EdgeVerdict;
  reason?: string;
}

export interface EdgeResponse {
  window: number;
  min_trades: number;
  t_stat_kill: number;
  fee_share_kill: number;
  computed_at: number;
  strategies: Record<string, EdgeStats>;
}

export interface StrategyInfo {
  type: string;
  active: boolean;
  allocation: number;
  name: string;
  killed?: boolean;
  kill_reason?: string;
  /** Bridge ≥ 2.14 */
  enabled?: boolean; // allocation > 0
  description?: string;
  params?: Record<string, ConfigScalar>;
  symbols?: string[];
  edge?: EdgeStats;
  /** Bridge ≥ 2.15 — Settings tab holding this strategy's params */
  settings_group?: string;
  /** Bridge ≥ 2.15 — offline research verdict (scripts/*_research.py) */
  research?: StrategyResearch | null;
}

export interface StrategyResearch {
  verdict: string; // GO | NO-GO | …
  /** "2/7" or a list of check names */
  checks?: string | string[];
  trades?: number;
  profit_factor?: number;
  t_stat?: number;
  summary?: string;
  note?: string;
  [key: string]: unknown;
}

export interface StrategiesResponse {
  strategies?: StrategyInfo[];
  /** Retired by the research: no gross edge, so they are no longer offered — only recorded. */
  retired?: { type: string; name: string; reason: string }[];
}

export interface TrendPosition {
  symbol: string;          // the venue key the engine trades (BTCUSDT)
  ui_symbol?: string;      // the market name every table shows (BTC-USD)
  size: number;
  entry_price: number;
  mark_price: number;
  notional: number;
  unrealized_pnl: number;
  weight: number;
  opened: string;
}

export interface TrendTrackingRecord {
  date: string;
  model_ret: number;
  paper_ret: number;
  slippage_bps: number;
}

export interface TrendResponse {
  enabled: boolean;
  allocation: number;
  mode: string;
  next_run_utc: string | null;
  last_run_utc: string | null;
  last_run_status: string;
  last_error: string;
  universe: string[];
  candidates: number;
  targets: Record<string, number>;
  positions: TrendPosition[];
  equity_basis: number;
  exposure: number;
  tracking: {
    days: number;
    model_return: number;
    paper_return: number;
    tracking_error_ann: number;
    records: TrendTrackingRecord[];
  };
  params: Record<string, ConfigScalar>;
}

export interface RiskResponse {
  equity: number;
  peak_equity: number;
  drawdown_pct: number;
  max_drawdown_pct: number;
  daily_pnl: number;
  daily_limit: number;
  max_daily_loss_pct: number;
  weekly_pnl: number;
  weekly_limit: number;
  max_weekly_loss_pct: number;
  circuit_breaker: boolean;
  drawdown_halted: boolean;
  /** strategy → reason (string) or an object carrying a `reason` */
  killed_strategies: Record<string, string | { reason?: string; [k: string]: unknown }>;
  compounding_enabled: boolean;
  equity_basis: number;
}

export interface TradeRecord {
  id: number;
  symbol: string;
  side: string;
  trade_type?: string;
  strategy: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl: number;
  fee: number;
  duration_sec: number;
  entry_time: string;
  exit_time: string;
  /** Epoch seconds (UTC) — timezone-proof chart markers (bridge ≥ 2.13). */
  entry_ts?: number;
  exit_ts?: number;
  regime: string;
  /** Bridge ≥ 2.15 (tasks/ui_live_trading_contract.md §2) — all optional for older bridges */
  pnl_bps?: number;
  mae_bps?: number;
  mfe_bps?: number;
  slippage_bps?: number;
  order_type?: string;
  /** SL / TP / signal / time / rebalance / trend_exit / close */
  exit_reason?: string;
  hold_sec?: number;
  equity_after?: number;
  trigger?: string;
  /** pnl / margin */
  roe_pct?: number;
  leverage?: number;
  trade_id?: string;
  order_id?: string;
  signal_strength?: number;
  /** spread at entry, bps */
  spread_bps?: number;
}

export interface TradesResponse {
  trades?: TradeRecord[];
}

/**
 * One rung of the trend exit ladder (operator contract §1): a Donchian lookback with its own
 * trailing stop that never falls. When price closes below it, `share_exiting` of the position
 * leaves and `weight_after` stays open.
 */
export interface ExitLadderLevel {
  lookback: number;
  stop: number;
  /** Signed distance from the ladder reference price, as a ratio (−0.0754 = 7.54 % below) */
  distance_pct: number;
  /** Fraction of the position that leaves at this level (0.25 = a quarter) */
  share_exiting: number;
  /** Fraction still open once this level has triggered */
  weight_after: number;
}

/**
 * The trend book has no single stop-loss and no take-profit by design: the position is the
 * average of `total` Donchian sub-strategies, so it leaves the market in steps.
 * `null` on intraday strategies, which carry a real `stop_loss` / `take_profit`.
 */
export interface ExitLadder {
  /** Reference price the distances were measured from */
  price: number;
  /** Sub-strategies still holding / in the ensemble */
  active: number;
  total: number;
  levels: ExitLadderLevel[];
  /** Highest stop — where the first share leaves */
  first_exit: number;
  /** Lowest stop — where the position is fully out */
  full_exit: number;
  /** Distance from `price` to `full_exit` as a ratio — always the loss still on the table */
  worst_case_pct: number;
  /** true when the ladder belongs to a SHORT: its stops sit above the price */
  short?: boolean;
}

/**
 * WS `trading` → `positions` row (bridge 2.14 sends the first block; ≥ 2.15 adds the rest —
 * contract §2). Everything past the 2.14 block is optional so an older bridge still renders.
 */
export interface PositionData {
  symbol: string;
  side: string;
  size: number;
  entry_price: number;
  mark_price: number;
  unrealized_pnl: number;
  realized_pnl?: number;
  leverage?: number;
  liquidation_price?: number;
  strategy: string | null;
  notional?: number;
  pnl_pct?: number;
  /** 2.14: position open time (epoch s) */
  timestamp?: number;
  /** ≥ 2.15 */
  roe_pct?: number;
  margin?: number;
  stop_loss?: number;
  take_profit?: number;
  sl_distance_pct?: number | null;
  tp_distance_pct?: number | null;
  opened_ts?: number;
  hold_sec?: number;
  mae_bps?: number;
  mfe_bps?: number;
  entry_fee_rate?: number;
  fees_paid?: number;
  funding_paid?: number;
  order_id?: string;
  trigger?: string;
  regime_at_entry?: string;
  spread_at_entry_bps?: number;
  order_type?: string;
  atr_at_entry?: number;
  expected_cost_bps?: number;
  /** Bridge ≥ 2.16 — trend positions exit in steps instead of on one stop (operator contract §1) */
  exit_ladder?: ExitLadder | null;
}

/** GET /api/positions (bridge ≥ 2.15) — same rich rows as the WS broadcast. */
export interface PositionsResponse {
  positions?: PositionData[];
}

/** GET /api/orders — live protective orders of the paper book (SL/TP per position). */
export interface ProtectiveOrder {
  symbol: string;
  /** STOP | TAKE_PROFIT | LIMIT … */
  type: string;
  side: string;
  price: number;
  size: number;
  strategy?: string | null;
  position_id?: string;
  /** Signed distance from mark, as a ratio (−0.012 = 1.2 % below) */
  distance_pct?: number | null;
}

export interface OrdersResponse {
  orders?: ProtectiveOrder[];
}

/** GET /api/account (contract §3) — same fields ride on the WS `risk_update`. */
export interface AccountResponse {
  mode: string;
  equity: number;
  initial_capital: number;
  realized_pnl: number;
  unrealized_pnl: number;
  position_value: number;
  margin_used: number;
  available: number;
  margin_ratio: number;
  exposure_pct: number;
  leverage_effective: number;
  open_positions: number;
  fees_today: number;
  daily_pnl: number;
  weekly_pnl: number;
  peak_equity: number;
  drawdown_pct: number;
  max_leverage?: number;
  max_total_exposure_pct?: number;
  /** Bridge ≥ 2.16 — cumulative perpetual funding on the book (negative = paid) */
  funding_paid?: number;
  /** false → engine not running: only mode / initial_capital are present */
  engine?: boolean;
}

/** Live per-settlement funding rate of one market and its annualised equivalent (0.0948 = 9.5 %/yr). */
export interface FundingRate {
  rate: number;
  annualized_pct: number;
  held?: boolean;               // the book has an open position in this market
  candidate?: boolean;          // in the daily run's candidate pool: it may be bought tomorrow
  source?: "venue" | "feed" | "none";
  annualized_90d?: number | null;   // measured median on the venue, the reference for today's rate
}

/** One settlement charged to the paper book (negative `amount` = the book paid). */
export interface FundingSettlement {
  symbol: string;
  side: string;
  strategy?: string | null;
  notional: number;
  rate: number;
  amount: number;
  ts: number;
  mark_price?: number;
  periods?: number;
}

/** GET /api/funding (operator contract §3) — cumulative cost, per market, and the live rates. */
export interface FundingResponse {
  enabled: boolean;
  engine?: boolean;
  /** Settlement interval in hours (1 on Strike, 8 on Binance-style perps) */
  interval_hours?: number;
  /** seconds since the venue quote these rates come from was taken */
  quote_age_sec?: number | null;
  last_settled_utc?: string | null;
  next_settlement_utc?: string | null;
  /** Cumulative funding since inception (negative = paid) */
  total_paid?: number;
  by_symbol?: Record<string, number>;
  recent?: FundingSettlement[];
  rates?: Record<string, FundingRate>;
}

export interface RiskProfileLimits {
  max_drawdown_pct: number;
  max_daily_loss_pct: number;
  max_weekly_loss_pct: number;
}

/** One risk level from GET /api/risk/profiles — same strategy, sized harder or softer. */
export interface RiskProfileInfo {
  profile: string;
  /** false → outside the range the research validated */
  validated: boolean;
  target_vol: number;
  /** ceiling on the position scalar for this profile (2x, or 3x on aggressive) */
  leverage_cap?: number;
  leverage_note?: string;
  /** a named profile can still sit outside the range the research covers — aggressive does */
  beyond_validated_range?: boolean;
  worst_day?: number | null;
  worst_week?: number | null;
  longest_underwater_days?: number | null;
  /** how many of the book's own GO/NO-GO gates this level passed, and the deflated Sharpe */
  gates_passed?: number | null;
  gates_total?: number | null;
  dsr?: number | null;
  expected_cagr: number;
  expected_vol: number;
  expected_max_dd: number;
  sharpe: number;
  /** Expected return over a year at the CURRENT equity */
  expected_year_usd: number;
  /** Expected worst peak-to-trough loss at the CURRENT equity */
  expected_worst_drawdown_usd: number;
  limits: RiskProfileLimits;
  note?: string;
}

/** GET /api/risk/profiles (operator contract §4). `current` is `custom` when nothing matches. */
export interface RiskProfilesResponse {
  current: string;
  equity: number;              // the risk manager's realised equity
  equity_basis?: number;       // what the engine sizes on: equity including open positions
  /** [min, max] target volatility the research validated */
  validated_target_vol_range: [number, number] | number[];
  profiles: RiskProfileInfo[];
  current_values: {
    trend_target_vol: number;
    max_drawdown_pct: number;
    max_daily_loss_pct: number;
    max_weekly_loss_pct: number;
  };
  source?: string;
}

export interface RiskProfileApplyResponse {
  status: string;
  profile: string;
  applied?: Record<string, ConfigScalar>;
  restart_required?: boolean;
  describe?: string;
}

/** POST /api/positions/close — paper only (409 in live). */
export interface ClosePositionResponse {
  symbol: string;
  closed: boolean;
  source?: string;
}

/**
 * GET /api/market/{symbol} (contract §4). Numeric fields are null while the engine has no
 * snapshot for the symbol; `engine: false` → nothing but the symbol is present.
 */
export interface MarketInfoResponse {
  symbol?: string;
  engine?: boolean;
  price?: number | null;
  mark_price?: number | null;
  index_price?: number | null;
  funding_rate?: number | null;
  /** seconds to the venue's next funding settlement */
  funding_countdown_sec?: number | null;
  change_24h_pct?: number | null;
  high_24h?: number | null;
  low_24h?: number | null;
  volume_24h_usd?: number | null;
  volume_24h_base?: number | null;
  /** minutes of 1m bars behind the 24h block (< 1440 right after a start) */
  window_min?: number;
  open_interest?: number | null;
  /** the VENUE's live spread (top of Strike's book), not the reference feed's */
  spread_bps?: number | null;
  best_bid?: number | null;
  best_ask?: number | null;
  /** the reference feed (Binance) kept apart, so nothing shows its numbers as the venue's */
  feed_price?: number | null;
  feed_spread_bps?: number | null;
  feed_age_sec?: number | null;
  regime?: string;
  /** epoch seconds */
  regime_since?: number;
  regime_candidate?: string;
  regime_timeframe_min?: number;
  data_age_sec?: number;
  /** Bridge ≥ 2.16 (spec §5.6) */
  symbol_config?: SymbolConfigInfo;
  /** The venue's own rules for an order on this market, straight from its exchangeInfo. */
  venue_filters?: VenueFilters | null;
}

/** `/api/market/{sym}.venue_filters` — what Strike says an order here must look like. */
export interface VenueFilters {
  tick_size?: number | null;
  step_size?: number | null;
  min_qty?: number | null;
  max_qty?: number | null;
  /** cap on a single MARKET order — a real constraint on sizing, not a curiosity */
  market_max_qty?: number | null;
  min_notional?: number | null;
  min_price?: number | null;
  max_price?: number | null;
  liquidation_fee?: number | null;
  price_precision?: number | null;
  qty_precision?: number | null;
  margin_asset?: string | null;
  status?: string | null;
}

/** `/api/market/{sym}.symbol_config` (spec §5.6) */
export interface SymbolConfigInfo {
  leverage: number;
  /** null when the market has no per-symbol cap: the daily run sizes it by volatility */
  max_position_usd: number | null;
  min_notional_usd: number;
  strategies: string[];
  taker_fee: number;
  maker_fee: number;
  maintenance_margin: number;
  /** half this market's own measured spread on the venue, floored at the configured default */
  slippage_bps?: number;
}

// ── v2.16 (spec §5) ──────────────────────────────────────────────

export type WinDayResult = "win" | "loss" | "flat";

export interface WinDay {
  date: string;
  pnl: number;
  trades: number;
  result: WinDayResult;
}

export interface PortfolioDay {
  date: string;
  equity: number;
  pnl: number;
  volume: number;
  trades: number;
  fees: number;
}

export interface StrategyPortfolio {
  strategy: string;
  trades: number;
  open_positions: number;
  pnl: number;
  realized: number;
  unrealized: number;
  volume: number;
  fees: number;
  win_rate: number;
  profit_factor: number;
  sharpe: number | null;
  max_drawdown: number;
  t_stat: number;
  first_trade_ts: number | null;
  /** [epoch_seconds, cumulative realized pnl] */
  equity_curve: [number, number][];
  return_30d: number;
}

/** GET /api/portfolio (spec §5.1) */
export interface PortfolioResponse {
  engine: boolean;
  mode: string;
  initial_capital: number;
  since_ts: number;
  equity: number;
  cash: number;
  margin_used: number;
  unrealized_pnl: number;
  realized_pnl: number;
  alltime_pnl: number;
  alltime_volume: number;
  fees_paid: number;
  leverage: number;
  margin_usage: number;
  trend_book_notional: number;
  volume_30d: number;
  fees_taker: number;
  fees_maker: number;
  analysis: {
    longest_win_streak_days: number;
    trading_style: string;
    avg_hold_sec: number;
    median_hold_sec: number;
  };
  perf_30d: {
    drawdown: number;
    win_rate: number;
    sharpe: number | null;
    sharpe_valid: boolean;
    sharpe_reason?: string;
    trades: number;
  };
  win_days: WinDay[];
  bias: { long_notional: number; short_notional: number; long_pct: number };
  daily: PortfolioDay[];
  by_strategy: StrategyPortfolio[];
}

export type ActivityKind = "fill" | "run" | "regime" | "risk" | "kill" | "system" | "config" | "signal";

/** GET /api/activity (spec §5.2) — newest first */
export interface ActivityEvent {
  ts: number;
  kind: ActivityKind | string;
  level?: "info" | "warning" | "error" | string;
  symbol?: string | null;
  side?: string | null;
  title: string;
  detail?: string | null;
  pnl?: number | null;
  roe_pct?: number | null;
}

export interface ActivityResponse {
  events?: ActivityEvent[];
}

/** GET /api/market/{sym}/funding_history (spec §5.3) */
export interface FundingPoint {
  ts: number;
  rate: number;
  mark_price?: number | null;
}

export interface FundingHistoryResponse {
  symbol: string;
  points?: FundingPoint[];
  cumulative?: { ts: number; value: number }[];
  source?: string;
  cached_at?: number;
}

/** GET /api/ops (spec §5.4) */
export interface OpsResponse {
  available: boolean;
  last_check?: string | null;
  alerts?: { key: string; text: string }[];
  sent?: unknown[];
  summary_sent?: boolean;
  facts?: Record<string, string | number | boolean | null>;
  /** transient faults seen once and not yet confirmed — not alerts */
  pending?: Record<string, number>;
  journal_15?: Record<string, number>;
  state?: { last_summary_date?: string; last_alerts?: Record<string, unknown>; [k: string]: unknown };
  next_timer?: string | null;
  [k: string]: unknown;
}

/** GET /api/regime */
export interface RegimeStatus {
  regime?: string;
  candidate?: string;
  confirmed_since?: number;
  timeframe_min?: number;
  [k: string]: unknown;
}

export interface RegimeResponse {
  symbols: Record<string, RegimeStatus>;
  timeframe_min?: number;
  min_dwell_min?: number;
}

export interface DatasetInfo {
  symbol: string;
  type?: string;
  /** bridge field name */
  data_type?: string;
  records?: number;
  total_rows?: number;
  size_mb?: number;
  date_range?: string;
  date_start?: string;
  date_end?: string;
  file_count?: number;
  timeframe?: string;
}

/** `datasets` is an array on old bridges and an object keyed by "SYMBOL/type" on the current one. */
export interface DataCatalogResponse {
  datasets?: DatasetInfo[] | Record<string, DatasetInfo>;
  total_datasets?: number;
  updated_at?: number;
}

export interface BacktestResult {
  equity_curve: number[];
  total_trades: number;
  win_rate: number;
  pnl: number;
  sharpe_ratio: number;
  max_drawdown: number;
  profit_factor: number;
  avg_trade_pnl: number;
  total_fees: number;
  return_pct: number;
  by_strategy: Record<string, { trades: number; pnl: number; win_rate: number }>;
  bars_tested: number;
}

export interface BacktestRequest {
  symbol: string;
  strategy: string;
  exchange: string;
  start_date?: string;
  end_date?: string;
  bars?: number;
}

// ── Core request ─────────────────────────────────────────────────

type RequestOpts = RequestInit & { timeoutMs?: number; baseUrl?: string };

async function request<T>(path: string, opts: RequestOpts = {}): Promise<T> {
  const { timeoutMs = API_TIMEOUT_MS, baseUrl = getBridgeUrl(), headers, ...init } = opts;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const url = `${baseUrl}${path}`;
  try {
    const res = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(headers as Record<string, string> | undefined) },
    });
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    const errMsg = extractError(body);
    if (!res.ok) {
      throw new ApiError(errMsg ?? `HTTP ${res.status} ${res.statusText || ""}`.trim() + ` (${path})`, res.status);
    }
    // Legacy bridge: HTTP 200 + {"error": "..."} for auth/validation failures — treat as failure.
    if (errMsg !== null) throw new ApiError(errMsg, res.status);
    return body as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(`Timeout after ${Math.round(timeoutMs / 1000)} s: ${url}`);
    }
    if (e instanceof TypeError) {
      // network / DNS / CORS / CSP — the bridge could not be reached at all
      throw new ApiError(`Cannot reach ${baseUrl} (${e.message})`);
    }
    throw new ApiError(e instanceof Error ? e.message : String(e));
  } finally {
    clearTimeout(timer);
  }
}

function extractError(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const b = body as { error?: unknown; detail?: unknown };
  if (typeof b.error === "string") return b.error;
  if (typeof b.detail === "string") return b.detail; // FastAPI HTTPException
  // FastAPI request validation: detail = [{loc, msg, type}]
  if (Array.isArray(b.detail)) {
    const parts = b.detail
      .map((d) => {
        if (!d || typeof d !== "object") return null;
        const { loc, msg } = d as { loc?: unknown; msg?: unknown };
        const where = Array.isArray(loc) ? loc.filter((x) => x !== "body").join(".") : "";
        return typeof msg === "string" ? (where ? `${where}: ${msg}` : msg) : null;
      })
      .filter((x): x is string => !!x);
    if (parts.length) return parts.join("; ");
  }
  return null;
}

/**
 * The token goes in a header, never in the URL: a query string is written verbatim to the
 * bridge's access log (journald on the server), to proxy logs and to browser history
 * (audit R2 security_supply-01 — reproduced). The bridge accepts both, so an older bridge
 * that only reads `?token=` would reject these calls — upgrade the bridge, not this file.
 */
function tokenHeader(token: string): Record<string, string> {
  return token ? { "X-BotStrike-Token": token } : {};
}

// ── Token resolution ─────────────────────────────────────────────
// Configured token (Settings → Connection) wins. When the bridge is local (loopback) and no
// token is configured, the bridge still exposes it on /api/bot/status → discover it once.

let discoveredToken: { url: string; token: string } | null = null;

async function resolveToken(): Promise<string> {
  const configured = getBridgeToken();
  if (configured) return configured;
  if (!isLocalBridge()) return "";
  const url = getBridgeUrl();
  if (discoveredToken?.url === url) return discoveredToken.token;
  try {
    const st = await request<BotStatusResponse>("/api/bot/status", { timeoutMs: HEALTH_TIMEOUT_MS });
    if (typeof st.auth_token === "string" && st.auth_token) {
      discoveredToken = { url, token: st.auth_token };
      return st.auth_token;
    }
  } catch {
    /* status unreachable — the call below will surface the real error */
  }
  return "";
}

async function authed<T>(path: string, opts: RequestOpts): Promise<T> {
  const token = await resolveToken();
  try {
    return await request<T>(path, {
      ...opts,
      headers: { ...(opts.headers as Record<string, string> | undefined), ...tokenHeader(token) },
    });
  } catch (e) {
    if (e instanceof ApiError && e.isAuth) discoveredToken = null; // stale local token → rediscover next time
    throw e;
  }
}

/** Absolute URL of the CSV export (spec §5.5) — a plain link, no auth needed. */
export function tradesExportUrl(): string {
  return `${getBridgeUrl()}/api/trades/export.csv`;
}

/** Reachability probe against an explicit URL (Settings → "Test connection", before saving). */
export function probeBridge(baseUrl: string): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", { baseUrl, timeoutMs: HEALTH_TIMEOUT_MS });
}

// ── Public API ───────────────────────────────────────────────────

/** GET /api/markets — the venue's tradable universe, tagged by what each market offers here. */
/** `/api/market/{sym}/klines` — the venue's candles for any market it lists. */
export interface VenueKlinesResponse {
  symbol: string;
  /** the interval the venue could actually fill — coarser than asked on a thin market */
  interval: string;
  requested_interval?: string;
  source?: string;
  candles: { timestamp: number; open: number; high: number; low: number; close: number; volume: number }[];
}

/** `/api/market/{sym}/book` — the venue's order book for any market it lists. */
export interface VenueBookResponse {
  symbol: string;
  source?: string;
  bids: [number, number][];
  asks: [number, number][];
  spread_bps: number | null;
}

/** `/api/market/{sym}/trades` — the venue's recent prints, oldest first. */
export interface VenueTradesResponse {
  symbol: string;
  source?: string;
  trades: { price: number; quantity: number; timestamp: number; side: "buy" | "sell" }[];
}

export interface VenueMarket {
  symbol: string;
  /** a live intraday stream: chart, order book and tape work */
  feed: boolean;
  /** a candidate the daily trend run may buy */
  pool: boolean;
  held: boolean;
  funding_rate: number | null;
  annualized_pct: number | null;
  annualized_90d?: number | null;
  /** the VENUE's own figures, so no column of the picker is quietly the reference feed's */
  price?: number | null;
  change_24h_pct?: number | null;
  volume_24h_usd?: number | null;
  open_interest?: number | null;
}

export interface MarketsResponse {
  engine: boolean;
  venue?: string;
  interval_hours?: number;
  /** seconds since the venue quote these rates come from was taken */
  quote_age_sec?: number | null;
  markets: VenueMarket[];
}

export const api = {
  health: () => request<HealthResponse>("/api/health", { timeoutMs: HEALTH_TIMEOUT_MS }),
  /** Every market the bot could operate on the venue, not only the four with an intraday feed. */
  markets: () => request<MarketsResponse>("/api/markets"),
  config: () => request<ConfigResponse>("/api/config"),
  configSchema: () => request<ConfigSchemaResponse>("/api/config/schema"),
  /** Partial update — only the changed paths. Token required when the bridge is not loopback. */
  configUpdate: (body: ConfigUpdateRequest) =>
    authed<ConfigUpdateResponse>("/api/config", { method: "PUT", body: JSON.stringify(body) }),
  /** Drop every override → responds like GET /api/config with restart_required: true. */
  configReset: () => authed<ConfigResponse>("/api/config/reset", { method: "POST" }),
  botStatus: () => request<BotStatusResponse>("/api/bot/status"),
  botStart: (mode = "paper", exchange = "binance") =>
    authed<BotActionResponse>(
      `/api/bot/start?mode=${encodeURIComponent(mode)}&exchange=${encodeURIComponent(exchange)}`,
      { method: "POST" },
    ),
  botStop: () => authed<BotActionResponse>("/api/bot/stop", { method: "POST" }),
  /** stop + start with the same mode/exchange → {"status": "restarting", "mode": "paper"} */
  botRestart: () => authed<BotActionResponse>("/api/bot/restart", { method: "POST" }),
  performance: () => request<PerformanceResponse>("/api/performance"),
  strategies: () => request<StrategiesResponse>("/api/strategies"),
  edge: () => request<EdgeResponse>("/api/edge"),
  trend: () => request<TrendResponse>("/api/trend"),
  risk: () => request<RiskResponse>("/api/risk"),
  trades: (limit = 100) => request<TradesResponse>(`/api/trades?limit=${limit}`),
  /** Bridge ≥ 2.15 — rich open positions (404 on older bridges; the WS broadcast carries the same rows). */
  positions: () => request<PositionsResponse>("/api/positions"),
  /** Bridge ≥ 2.15 — live SL/TP orders of the paper book (404 on older bridges). */
  orders: () => request<OrdersResponse>("/api/orders"),
  /** Bridge ≥ 2.15 — account overview (404 on older bridges). */
  account: () => request<AccountResponse>("/api/account"),
  /** Bridge ≥ 2.15 — market header data for one symbol (404 on older bridges). */
  market: (symbol: string) => request<MarketInfoResponse>(`/api/market/${encodeURIComponent(symbol)}`),
  /** Candles, book and prints for ANY market the venue lists — the engine only streams four. */
  marketKlines: (symbol: string, interval = "1m", limit = 500) =>
    request<VenueKlinesResponse>(`/api/market/${encodeURIComponent(symbol)}/klines?interval=${encodeURIComponent(interval)}&limit=${limit}`),
  marketBook: (symbol: string, limit = 20) =>
    request<VenueBookResponse>(`/api/market/${encodeURIComponent(symbol)}/book?limit=${limit}`),
  marketTrades: (symbol: string, limit = 50) =>
    request<VenueTradesResponse>(`/api/market/${encodeURIComponent(symbol)}/trades?limit=${limit}`),
  dataCatalog: () => request<DataCatalogResponse>("/api/data/catalog"),
  /** Bridge ≥ 2.16 — portfolio page data (404 on older bridges). */
  portfolio: () => request<PortfolioResponse>("/api/portfolio"),
  /** Bridge ≥ 2.16 — activity feed, newest first (404 on older bridges). */
  activity: (limit = 100) => request<ActivityResponse>(`/api/activity?limit=${limit}`),
  /** Bridge ≥ 2.16 — 8 h funding history of a symbol (404 on older bridges). */
  fundingHistory: (symbol: string, limit = 200) =>
    request<FundingHistoryResponse>(`/api/market/${encodeURIComponent(symbol)}/funding_history?limit=${limit}`),
  /** Bridge ≥ 2.16 — funding accrued on the book + the live rate per market (operator contract §3). */
  funding: () => request<FundingResponse>("/api/funding"),
  /** Bridge ≥ 2.16 — the three validated risk levels priced for the current equity (§4). */
  riskProfiles: () => request<RiskProfilesResponse>("/api/risk/profiles"),
  /** Token-gated. Moves target volatility AND the loss ladder; live at the next daily run. */
  riskProfileApply: (profile: string) =>
    authed<RiskProfileApplyResponse>("/api/risk/profile", { method: "POST", body: JSON.stringify({ profile }) }),
  /** Token-gated operator brake: close ONE position now at the current price. Paper only (409 in live). */
  closePosition: (symbol: string) =>
    authed<ClosePositionResponse>("/api/positions/close", { method: "POST", body: JSON.stringify({ symbol }) }),
  /** Bridge ≥ 2.16 — ops monitor state (`available:false` until the monitor ran). */
  ops: () => request<OpsResponse>("/api/ops"),
  regime: () => request<RegimeResponse>("/api/regime"),
  backtestRun: (body: BacktestRequest) =>
    authed<BacktestResult>("/api/backtest/run", {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs: BACKTEST_TIMEOUT_MS,
    }),
};
