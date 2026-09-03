import { useMemo } from "react";
import { useShallow } from "zustand/shallow";
import { api, type AccountResponse, type PositionData } from "@/lib/api";
import { useRiskStore } from "@/stores/riskStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useSystemStore } from "@/stores/systemStore";
import { positionMargin, positionNotional } from "@/lib/market";
import { useEndpoint } from "./useEndpoint";

const POLL_MS = 5_000;

/** Account overview: WS risk_update (≥ 2.15) → GET /api/account (5 s) → derived from the open positions. */
export function useAccount(positions: PositionData[]): { acct: AccountResponse; derived: boolean; missing: boolean } {
  const ep = useEndpoint(() => api.account(), POLL_MS);
  const rest = ep.data && ep.data.engine !== false && typeof ep.data.equity === "number" ? ep.data : null;
  const wsAccount = useRiskStore((s) => s.account);
  const risk = useRiskStore(useShallow((s) => ({ equity: s.equity, peak: s.peak_equity, dd: s.drawdown_pct, daily: s.daily_pnl, weekly: s.weekly_pnl })));
  const metrics = useTradingStore(useShallow((s) => ({ equity: s.metrics.equity, pnl: s.metrics.pnl })));
  const mode = useSystemStore((s) => s.mode);

  const acct = useMemo<AccountResponse>(() => {
    if (wsAccount) return wsAccount;
    if (rest) return rest;
    const equity = metrics.equity > 0 ? metrics.equity : risk.equity;
    const unreal = positions.reduce((a, p) => a + (p.unrealized_pnl || 0), 0);
    const posValue = positions.reduce((a, p) => a + positionNotional(p), 0);
    const marginUsed = positions.reduce((a, p) => a + positionMargin(p), 0);
    return {
      mode,
      equity,
      initial_capital: 0,
      realized_pnl: metrics.pnl - unreal,
      unrealized_pnl: unreal,
      position_value: posValue,
      margin_used: marginUsed,
      available: Math.max(0, equity - marginUsed),
      margin_ratio: equity > 0 ? marginUsed / equity : 0,
      exposure_pct: equity > 0 ? posValue / equity : 0,
      leverage_effective: equity > 0 ? posValue / equity : 0,
      open_positions: positions.length,
      fees_today: NaN,
      daily_pnl: risk.daily,
      weekly_pnl: risk.weekly,
      peak_equity: risk.peak,
      drawdown_pct: risk.dd,
    };
  }, [wsAccount, rest, metrics, risk, positions, mode]);

  return { acct, derived: !wsAccount && !rest, missing: ep.missing };
}
