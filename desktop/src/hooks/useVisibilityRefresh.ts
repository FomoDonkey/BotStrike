import { useEffect } from "react";
import { api } from "@/lib/api";
import { pingAll } from "@/lib/ws";
import { useTradingStore } from "@/stores/tradingStore";
import { useRiskStore } from "@/stores/riskStore";

/** GET /api/performance → tradingStore.metrics (same fields the WS `metrics` broadcast carries). */
export async function refreshPerformanceIntoStore(): Promise<void> {
  try {
    const p = await api.performance();
    useTradingStore.getState().onMetrics({
      equity: p.equity,
      pnl: p.pnl,
      total_trades: p.total_trades,
      win_rate: p.win_rate,
      sharpe_ratio: p.sharpe_ratio,
      max_drawdown: p.max_drawdown,
      total_fees: p.total_fees,
    });
  } catch {
    /* bridge unreachable — the WS reconnect will refresh the store */
  }
}

/** GET /api/risk → riskStore (peak / daily / weekly / killed / compounding). */
export async function refreshRiskIntoStore(): Promise<void> {
  try {
    useRiskStore.getState().onRiskSnapshot(await api.risk());
  } catch {
    /* older bridge without /api/risk, or unreachable */
  }
}

/**
 * Background tabs get their timers throttled to ~1/min: after 30 min hidden the tickers show
 * "---" and the regime UNKNOWN because the sockets died silently. When the tab becomes
 * visible again: drop stale sockets (immediate reconnect) and refetch the REST views into
 * the stores. Page-level polls (usePolling) re-run themselves on the same event.
 */
export function useVisibilityRefresh() {
  useEffect(() => {
    const onChange = () => {
      if (document.visibilityState !== "visible") return;
      pingAll();
      void refreshPerformanceIntoStore();
      void refreshRiskIntoStore();
    };
    document.addEventListener("visibilitychange", onChange);
    window.addEventListener("online", onChange);
    return () => {
      document.removeEventListener("visibilitychange", onChange);
      window.removeEventListener("online", onChange);
    };
  }, []);
}
