import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useBridgeConfig } from "@/lib/config";
import { useAlertStore } from "@/stores/alertStore";
import { useExchangeStore } from "@/stores/exchangeStore";
import { TOKEN_GATED_REASON } from "@/lib/constants";

export type BotAction = "start" | "start_dry" | "stop" | "restart";

/**
 * Start / Stop / Restart with the same token rule as the bridge: a loopback bridge needs no
 * token (it is discovered from /api/bot/status), a remote one needs the configured token —
 * without it the actions are disabled in the UI (never fired blindly against the CT).
 */
export function useBotControl() {
  const { isLocal, token } = useBridgeConfig();
  const exchange = useExchangeStore((s) => s.exchange);
  const addAlert = useAlertStore((s) => s.addAlert);
  const [busy, setBusy] = useState<BotAction | null>(null);
  const canControl = isLocal || token.length > 0;
  const disabledReason = canControl ? undefined : TOKEN_GATED_REASON;

  const run = async (action: BotAction) => {
    if (!canControl || busy) return;
    setBusy(action);
    try {
      if (action === "start") {
        const r = await api.botStart("paper", exchange);
        addAlert({ level: "info", title: "Bot starting", message: `${r.status} · paper · ${r.exchange ?? exchange}` });
      } else if (action === "start_dry") {
        const r = await api.botStart("dry_run", exchange);
        addAlert({ level: "info", title: "Bot starting", message: `${r.status} · dry run · ${r.exchange ?? exchange}` });
      } else if (action === "stop") {
        const r = await api.botStop();
        addAlert({ level: "warning", title: "Bot stopped", message: r.status });
      } else {
        const r = await api.botRestart();
        addAlert({ level: "info", title: "Engine restarting", message: `${r.status}${r.mode ? ` · ${r.mode}` : ""}` });
      }
    } catch (e) {
      addAlert({ level: "critical", title: `${action === "stop" ? "Stop" : action === "restart" ? "Restart" : "Start"} failed`, message: e instanceof ApiError ? e.message : String(e), sound: "alert" });
    } finally {
      setBusy(null);
    }
  };

  return { canControl, disabledReason, busy, run };
}
