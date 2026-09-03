// ── Market configuration from GET /v2/markets ──

export interface MarginTier {
  max_notional: string;
  max_leverage: number;
  maintenance_margin_rate: string;
  maintenance_amount: string;
}

export interface Market {
  symbol: string; // e.g. "BTC-USD"
  name: string;
  base_asset: string; // e.g. "BTC"
  status: string; // "trading", "halt"
  base_prec: number; // decimal places for sizes
  quote_prec: number; // decimal places for prices
  default_leverage: number;

  // Price constraints
  order_tick_price: string; // min price increment
  order_min_price: string;
  order_max_price: string;
  order_limit_price_bound: string;
  order_market_price_bound: string;

  // Size constraints
  order_limit_step_size: string;
  order_limit_min_size: string;
  order_limit_max_size: string;
  order_market_step_size: string;
  order_market_min_size: string;
  order_market_max_size: string;
  order_min_notional: string; // min order value in USD

  // Margin tiers (sorted by max_notional ascending)
  margin_tiers: MarginTier[];

  reduce_only: boolean;
  liquidation_fee_rate: string;
  trigger_protect: string;

  // Snapshot prices (from initial fetch)
  mark_price: string;
  index_price: string;
  last_price: string;
  bid1_price: string;
  bid1_size: string;
  ask1_price: string;
  ask1_size: string;
  funding_rate: string;
  next_funding_time: number;
}

export interface MarketsResponse {
  markets: Record<string, Market>;
}

export interface OrderBookDepthResponse {
  lastUpdateId: number | string;
  E?: number;
  T?: number;
  symbol?: string;
  bids: string[][]; // [[price, qty], ...] highest first
  asks: string[][]; // [[price, qty], ...] lowest first
}
