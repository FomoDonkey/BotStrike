import { useMemo, useState } from "react";
import { useShallow } from "zustand/shallow";
import { api, ApiError, type AccountResponse, type PositionData } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { useRiskStore } from "@/stores/riskStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useSystemStore } from "@/stores/systemStore";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { cn, formatPct, formatSignedUSD, formatUSD } from "@/lib/utils";
import { positionMargin, positionNotional } from "@/lib/market";

const POLL_MS = 5_000;

interface AccountPanelProps {
  positions: PositionData[];
  /** compact = single column (right rail); full = bottom tab grid */
  variant: "compact" | "full";
  className?: string;
}

/** Account overview from GET /api/account (5 s); on a 2.14 bridge the same fields are derived. */
export function AccountPanel({ positions, variant, className }: AccountPanelProps) {
  const [rest, setRest] = useState<AccountResponse | null>(null);
  const [missing, setMissing] = useState(false);
  usePolling(async () => {
    try {
      const r = await api.account();
      // engine stopped → only mode / initial_capital are present: fall back to the derived view
      setRest(r.engine === false || typeof r.equity !== "number" ? null : r);
      setMissing(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
    }
  }, POLL_MS);

  const wsAccount = useRiskStore((s) => s.account);
  const risk = useRiskStore(useShallow((s) => ({
    equity: s.equity, peak: s.peak_equity, dd: s.drawdown_pct, daily: s.daily_pnl, weekly: s.weekly_pnl, basis: s.equity_basis,
  })));
  const metrics = useTradingStore(useShallow((s) => ({ equity: s.metrics.equity, pnl: s.metrics.pnl, fees: s.metrics.total_fees })));
  const mode = useSystemStore((s) => s.mode);

  const acct = useMemo<AccountResponse>(() => {
    // WS risk_update (≥ 2.15) is fresher than the 5 s REST poll; REST is the fallback source.
    if (wsAccount) return wsAccount;
    if (rest) return rest;
    // Derived view (bridge 2.14): equity from the merged metrics, risk figures from the WS
    // risk_update, exposure from the live positions.
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

  const derived = !wsAccount && !rest;
  const mr = acct.margin_ratio;
  const mrTone = mr >= 0.8 ? "text-loss" : mr >= 0.5 ? "text-warning" : "text-text-primary";

  const groups: { title: string; rows: { k: string; v: React.ReactNode; hint?: string }[] }[] = [
    {
      title: "Balance",
      rows: [
        { k: "Account value", v: formatUSD(acct.equity), hint: "Equity = initial capital + realised PnL + unrealised PnL" },
        { k: "Available", v: formatUSD(acct.available), hint: HINTS.available },
        { k: "Margin used", v: formatUSD(acct.margin_used), hint: HINTS.margin },
        { k: "Position value", v: formatUSD(acct.position_value), hint: HINTS.notional },
        { k: "Unrealized PnL", v: <span className={acct.unrealized_pnl > 0 ? "text-profit" : acct.unrealized_pnl < 0 ? "text-loss" : ""}>{formatSignedUSD(acct.unrealized_pnl)}</span>, hint: HINTS.pnl },
        { k: "Realized PnL", v: <span className={acct.realized_pnl > 0 ? "text-profit" : acct.realized_pnl < 0 ? "text-loss" : ""}>{formatSignedUSD(acct.realized_pnl)}</span>, hint: "Closed-trade PnL net of fees (all-time)" },
      ],
    },
    {
      title: "Risk",
      rows: [
        { k: "Margin ratio", v: <span className={mrTone}>{formatPct(acct.margin_ratio, 1)}</span>, hint: HINTS.marginRatio },
        { k: "Exposure", v: formatPct(acct.exposure_pct, 1), hint: HINTS.exposure },
        { k: "Effective leverage", v: `${acct.leverage_effective.toFixed(2)}x`, hint: HINTS.levEff },
        { k: "Open positions", v: String(acct.open_positions) },
        { k: "Peak equity", v: acct.peak_equity > 0 ? formatUSD(acct.peak_equity) : "---" },
        { k: "Drawdown", v: <span className={acct.drawdown_pct > 0 ? "text-loss" : ""}>{formatPct(acct.drawdown_pct)}</span>, hint: HINTS.drawdown },
      ],
    },
    {
      title: "Period",
      rows: [
        { k: "Daily PnL", v: <span className={acct.daily_pnl > 0 ? "text-profit" : acct.daily_pnl < 0 ? "text-loss" : ""}>{formatSignedUSD(acct.daily_pnl)}</span>, hint: HINTS.dailyPnl },
        { k: "Weekly PnL", v: <span className={acct.weekly_pnl > 0 ? "text-profit" : acct.weekly_pnl < 0 ? "text-loss" : ""}>{formatSignedUSD(acct.weekly_pnl)}</span>, hint: HINTS.weeklyPnl },
        { k: "Fees today", v: Number.isFinite(acct.fees_today) ? formatUSD(acct.fees_today) : <span title={`All-time fees ${formatUSD(metrics.fees)} — today's split needs bridge ≥ 2.15`} className="text-text-faint">---</span>, hint: HINTS.feesToday },
        { k: "Initial capital", v: acct.initial_capital > 0 ? formatUSD(acct.initial_capital) : "---" },
        { k: "Mode", v: <span className={cn("uppercase text-[10px] font-bold tracking-wider px-1.5 py-0.5 rounded", acct.mode === "live" ? "bg-loss/10 text-loss" : "bg-warning/10 text-warning")}>{acct.mode || mode}</span> },
      ],
    },
  ];

  // Right-rail overview: five rows so the order book above keeps room for its levels at 900 px
  const compactRows = variant === "compact"
    ? [groups[0].rows[0], groups[0].rows[1], groups[0].rows[3], groups[0].rows[4], groups[1].rows[0]]
    : null;

  return (
    <div className={cn("flex flex-col min-h-0", className)}>
      {compactRows ? (
        <dl className="kv px-3 py-1">
          {compactRows.map((r) => (
            <Row key={r.k} k={r.k} hint={r.hint}>{r.v}</Row>
          ))}
        </dl>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-x-8 px-3 py-2 overflow-auto">
          {groups.map((g) => (
            <div key={g.title} className="min-w-0">
              <p className="text-[10.5px] uppercase tracking-[0.06em] text-text-muted h-7 flex items-center border-b border-hairline-soft">{g.title}</p>
              <dl className="kv">
                {g.rows.map((r) => <Row key={r.k} k={r.k} hint={r.hint}>{r.v}</Row>)}
              </dl>
            </div>
          ))}
        </div>
      )}
      {derived && (
        <p className="px-3 py-1 text-[10px] text-text-faint border-t border-hairline-soft shrink-0" title={missing ? "GET /api/account returned 404 — figures are derived from the metrics / risk_update broadcasts and the open positions." : "No account overview from the bridge yet (engine stopped?) — figures are derived from the metrics / risk_update broadcasts and the open positions."}>
          derived · {missing ? "GET /api/account needs bridge ≥ 2.15" : "waiting for the bridge account overview"}
        </p>
      )}
    </div>
  );
}

function Row({ k, hint, children }: { k: string; hint?: string; children: React.ReactNode }) {
  return (
    <>
      <dt>{hint ? <Hint title={hint}>{k}</Hint> : k}</dt>
      <dd>{children}</dd>
    </>
  );
}
