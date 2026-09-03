// ── Account data from GET /v2/account ──

export interface SymbolSettings {
  margin_mode: "cross" | "isolated";
  leverage: number;
  allow_pre_trade: boolean;
}

export interface Account {
  account_id: string;
  blockchain: string;
  blockchain_address: string;
  wallet_balance: string;
  available_balance: string;
  withdrawable_balance: string;
  unrealized_pnl: string;
  margin_balance: string;
  total_margin: string;
  position_initial_margin: string;
  maintenance_margin: string;
  symbol_settings?: Record<string, SymbolSettings> | null;
}

// ── Builder Connect types ──

export interface BuilderConnectResult {
  account_id: string;
  builder_code: string;
  max_fee_bps: number;
  api_wallet_id: number;
  api_wallet_public_key: string;
  api_wallet_created_at: string;
}

// ── Fee tier ──

export interface FeeTierConfig {
  Tier: number;
  MinVolume: number;
  TakerRate: number;
  MakerRate: number;
}
