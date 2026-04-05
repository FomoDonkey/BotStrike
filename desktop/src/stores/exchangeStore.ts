import { create } from "zustand";

export type ExchangeId = "binance" | "hyperliquid";

interface ExchangeState {
  exchange: ExchangeId;
  setExchange: (exchange: ExchangeId) => void;
}

function safeGetItem(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function safeSetItem(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

export const useExchangeStore = create<ExchangeState>((set) => ({
  exchange: (safeGetItem("botstrike-exchange") as ExchangeId) || "binance",

  setExchange: (exchange) => {
    safeSetItem("botstrike-exchange", exchange);
    set({ exchange });
  },
}));
