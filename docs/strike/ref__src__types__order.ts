// ── Order types ──

export type OrderSide = "buy" | "sell";
export type OrderType =
  | "market"
  | "limit"
  | "stop"
  | "stop_limit"
  | "take_profit"
  | "take_profit_limit";
export type OrderStatus =
  | "pending"
  | "open"
  | "filled"
  | "canceled"
  | "rejected"
  | "expired"
  | "untriggered";
export type TimeInForce = "GTC" | "IOC" | "FOK";
export type WorkingType = "mark_price" | "contract_price";

export interface CreateOrderRequest {
  symbol: string;
  side: OrderSide;
  type: OrderType;
  size: string;
  price?: string; // for limit / stop_limit / take_profit_limit
  stop_price?: string; // for stop / stop_limit / take_profit / take_profit_limit
  time_in_force?: TimeInForce;
  post_only?: boolean;
  reduce_only?: boolean;
  working_type?: WorkingType;
  vault_id?: string;
}

export interface CreateStrategyOrderRequest extends CreateOrderRequest {
  strategy_id: string;
  client_order_id: string;
  take_profit_orders?: {
    type: "take_profit" | "take_profit_limit";
    size: string;
    stop_price: string;
    client_order_id: string;
    working_type: WorkingType;
  }[];
  stop_loss_orders?: {
    type: "stop" | "stop_limit";
    size: string;
    stop_price: string;
    client_order_id: string;
    working_type: WorkingType;
  }[];
}

export interface OrderHistory {
  id: number;
  client_order_id: string;
  account_id: string;
  symbol: string;
  strategy_id: string | null;
  side: string;
  status: string;
  type: string;
  origin_type: string;
  auto_close_type: string;
  time_in_force: string;
  working_type: string;
  size: string;
  filled: string;
  price: string;
  stop_price: string;
  cost: string;
  fee: string;
  post_only: boolean;
  reduce_only: boolean;
  close_position: boolean;
  close_reason: string | null;
  create_timestamp: number;
  event_timestamp: number;
}

export interface TradeHistory {
  id: number;
  trade_id: number;
  order_id: number;
  account_id: string;
  symbol: string;
  side: string;
  role: string; // "maker" | "taker"
  price: string;
  size: string;
  realized_pnl: string;
  fee: string;
  fee_type: string;
  auto_close_type: string;
  timestamp: number;
}
