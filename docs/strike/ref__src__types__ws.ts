// ── WebSocket event schemas ──

// ── Price Stream Events ──

export interface MarkPriceUpdate {
  e: "markPriceUpdate";
  E: number; // event time (ms)
  s: string; // symbol
  p: string; // mark price
  i: string; // index price
  P: string; // estimated settlement price
  r: string; // funding rate (decimal, e.g. "0.0001" = 0.01%)
  T: number; // next funding time (ms)
}

export interface DepthUpdate {
  e: "depthUpdate";
  s: string;
  u: number | string; // update ID
  b: string[][]; // bids [[price, qty], ...]
  a: string[][]; // asks [[price, qty], ...]
}

export interface TradeEvent {
  e: "trade";
  s: string;
  p: string; // price
  q: string; // quantity
  T: number; // trade time (ms)
  t: number; // trade ID
  m: boolean; // isBuyerMaker
}

export interface KlineEvent {
  e: "kline";
  k: {
    s: string;
    i: string; // interval
    t: number; // candle open time (ms)
    o: string;
    h: string;
    l: string;
    c: string;
    v: string;
  };
}

export interface MiniTickerUpdate {
  e: "24hrMiniTicker";
  E: number;
  s: string;
  c: string; // close
  o: string; // open
  h: string; // high
  l: string; // low
  v: string; // base volume
  q: string; // quote volume
}

// ── User Stream Events ──

export interface AccountUpdateBalance {
  a: string; // asset
  wb: string; // wallet balance
  cw: string; // cross wallet balance
  bc: string; // balance change
}

export interface AccountUpdatePosition {
  s: string; // symbol
  pa: string | number; // position amount (size)
  ep: string | number; // entry price
  mt: string; // margin type: "cross" | "isolated"
  ib: string | number; // isolated balance
  ps: string; // position side: "LONG" | "SHORT"
  i?: string; // position ID
}

export interface AccountUpdateEvent {
  e: "ACCOUNT_UPDATE";
  E: number;
  T: number;
  r: string; // reason: DEPOSIT | WITHDRAW | FUNDING | LIQUIDATION | ADL | ORDER
  B: AccountUpdateBalance[];
  P: AccountUpdatePosition[];
}

export interface OrderTradeUpdateEvent {
  e: "ORDER_TRADE_UPDATE";
  i: number; // order ID
  c: string; // client order ID
  s: string; // symbol
  S: string; // side: "BUY" | "SELL"
  o: string; // order type
  ot: string; // original order type
  f: string; // time in force
  q: string; // original quantity
  p: string; // limit price
  sp: string; // stop price
  X: string; // status
  x: string; // execution type
  z: string; // cumulative filled qty
  l: string; // last filled qty
  L: string; // last filled price
  n: string; // commission/fee
  N?: string; // commission asset
  T: number; // trade time
  E: number; // event time
  t: number; // trade ID
  m: boolean; // is maker
  R: boolean; // reduce only
  cp: boolean; // close position
  wt: string; // working type
  rp: string; // realized profit
  cr?: string; // close reason
  act?: string; // auto close type
  C?: string; // cost
}

// Final statuses — remove from open orders list
export const FINAL_ORDER_STATUSES = new Set([
  "FILLED",
  "CANCELED",
  "CANCELLED",
  "REJECTED",
  "EXPIRED",
]);
