import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useBridgeConfig } from "@/lib/config";
import { useAlertStore } from "@/stores/alertStore";
import { TOKEN_GATED_REASON } from "@/lib/constants";
import { refreshPositionsIntoStore } from "./useVisibilityRefresh";

/**
 * POST /api/positions/close with the same token rule as the other mutating actions: a loopback
 * bridge needs no token, a remote one does — without it the button is disabled and never fires.
 * Paper only: the bridge answers 409 in live and its message is surfaced as-is.
 */
export function useClosePosition() {
  const { isLocal, token } = useBridgeConfig();
  const addAlert = useAlertStore((s) => s.addAlert);
  const [busy, setBusy] = useState<string | null>(null);
  const canClose = isLocal || token.length > 0;
  const disabledReason = canClose ? undefined : TOKEN_GATED_REASON;

  const close = async (symbol: string) => {
    if (!canClose || busy) return;
    setBusy(symbol);
    try {
      const r = await api.closePosition(symbol);
      addAlert({
        level: "warning",
        title: `Closed ${symbol}`,
        message: r.closed ? `Manual close accepted (${r.source ?? "paper"} book)` : "The bridge reported no position to close",
        sound: "trade",
      });
      await refreshPositionsIntoStore();
    } catch (e) {
      addAlert({
        level: "critical",
        title: `Close ${symbol} failed`,
        message: e instanceof ApiError ? e.message : String(e),
        sound: "alert",
      });
    } finally {
      setBusy(null);
    }
  };

  return { canClose, disabledReason, busy, close };
}
