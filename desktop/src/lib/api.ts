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

// ── Response shapes (bridge.py) ──────────────────────────────────

export interface HealthResponse {
  status: string;
  engine_running: boolean;
  mode: string;
  uptime_sec: number;
  clients: number;
  version?: string;
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

export interface SymbolConfig {
  symbol: string;
  leverage: number;
  max_position_usd: number;
  vpin_bucket_size: number;
  vpin_toxic_threshold: number;
  hawkes_spike_mult: number;
  mm_gamma: number;
  obi_levels: number;
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
}

export interface ConfigResponse {
  use_testnet: boolean;
  has_api_key: boolean;
  has_telegram: boolean;
  symbols: SymbolConfig[];
  trading: TradingConfig;
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
}

export interface StrategyInfo {
  type: string;
  active: boolean;
  allocation: number;
  name: string;
  killed?: boolean;
  kill_reason?: string;
}

export interface StrategiesResponse {
  strategies?: StrategyInfo[];
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
  return null;
}

function withToken(path: string, token: string): string {
  if (!token) return path;
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
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
    return await request<T>(withToken(path, token), opts);
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
  botStatus: () => request<BotStatusResponse>("/api/bot/status"),
  botStart: (mode = "paper", exchange = "binance") =>
    authed<BotActionResponse>(
      `/api/bot/start?mode=${encodeURIComponent(mode)}&exchange=${encodeURIComponent(exchange)}`,
      { method: "POST" },
    ),
  botStop: () => authed<BotActionResponse>("/api/bot/stop", { method: "POST" }),
  performance: () => request<PerformanceResponse>("/api/performance"),
  strategies: () => request<StrategiesResponse>("/api/strategies"),
  trades: (limit = 100) => request<TradesResponse>(`/api/trades?limit=${limit}`),
  dataCatalog: () => request<DataCatalogResponse>("/api/data/catalog"),
  backtestRun: (body: BacktestRequest) =>
    authed<BacktestResult>("/api/backtest/run", {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs: BACKTEST_TIMEOUT_MS,
    }),
};
