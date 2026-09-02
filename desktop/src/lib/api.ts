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
}

export interface StrategiesResponse {
  strategies?: StrategyInfo[];
}

export interface TrendPosition {
  symbol: string;
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
}

export interface TradesResponse {
  trades?: TradeRecord[];
}

export interface DatasetInfo {
  symbol: string;
  type: string;
  records: number;
  size_mb: number;
  date_range: string;
}

export interface DataCatalogResponse {
  datasets?: DatasetInfo[];
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

/** Reachability probe against an explicit URL (Settings → "Test connection", before saving). */
export function probeBridge(baseUrl: string): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", { baseUrl, timeoutMs: HEALTH_TIMEOUT_MS });
}

// ── Public API ───────────────────────────────────────────────────

export const api = {
  health: () => request<HealthResponse>("/api/health", { timeoutMs: HEALTH_TIMEOUT_MS }),
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
  dataCatalog: () => request<DataCatalogResponse>("/api/data/catalog"),
  backtestRun: (body: BacktestRequest) =>
    authed<BacktestResult>("/api/backtest/run", {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs: BACKTEST_TIMEOUT_MS,
    }),
};
