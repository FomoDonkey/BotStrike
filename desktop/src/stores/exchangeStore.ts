import { create } from "zustand";

export type ExchangeId = "strike" | "binance" | "hyperliquid";

interface ExchangeState {
  exchange: ExchangeId;
  setExchange: (exchange: ExchangeId) => void;
  /** What the ENGINE says it is connected to. The label used to be a browser preference that
   *  nothing ever synced, so the screen could claim Binance while the bot ran on Strike — which is
   *  exactly what it did after the venue switch on 2026-09-04. */
  syncFromEngine: (venue: string | undefined | null) => void;
}

function safeGetItem(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function safeSetItem(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

export const useExchangeStore = create<ExchangeState>((set, get) => ({
  // the stored value is only a first paint; the engine's health message overrides it in seconds
  exchange: (safeGetItem("botstrike-exchange") as ExchangeId) || "strike",

  setExchange: (exchange) => {
    safeSetItem("botstrike-exchange", exchange);
    set({ exchange });
  },

  syncFromEngine: (venue) => {
    const v = String(venue || "").toLowerCase();
    if (v !== "strike" && v !== "binance" && v !== "hyperliquid") return;
    if (get().exchange === v) return;
    safeSetItem("botstrike-exchange", v);
    set({ exchange: v as ExchangeId });
  },
}));
