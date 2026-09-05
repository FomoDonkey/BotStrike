import { useMemo } from "react";
import { useShallow } from "zustand/shallow";
import { Shield, CircleOff } from "lucide-react";
import { useRiskStore, killReason } from "@/stores/riskStore";
import { useTradingStore } from "@/stores/tradingStore";
import { usePolling } from "@/hooks/usePolling";
import { refreshRiskIntoStore } from "@/hooks/useVisibilityRefresh";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ListRow, ListSection, Signed } from "@/components/ui/ListRow";
import { KpiCard, ProgressBar } from "@/components/ui/KpiCard";
import { Chip, RegimeChip, StrategyTag } from "@/components/ui/Chip";
import { SYMBOLS } from "@/lib/constants";
import { HINTS } from "@/lib/hints";
import { cn, formatMoney, formatPct, formatSignedMoney } from "@/lib/utils";
import { positionNotional } from "@/lib/market";
import { RiskProfileCards } from "./RiskProfileCards";
import { useEndpoint } from "@/hooks/useEndpoint";
import { api } from "@/lib/api";

const RISK_POLL_MS = 5_000;

interface LadderRow {
  label: string;
  hint: string;
  /** budget consumed 0–1 (null → the bridge did not send this limit) */
  used: number | null;
  current: React.ReactNode;
  limit: string;
  note?: string;
}

function tone(used: number | null): "mint" | "amber" | "rose" {
  if (used === null) return "mint";
  return used >= 0.7 ? "rose" : used >= 0.3 ? "amber" : "mint";
}

/** Risk (spec §3.4): ladder as three progress rows, kill list, exposure by symbol, compounding basis. */
export function RiskPage() {
  const risk = useRiskStore(useShallow((s) => ({
    equity: s.equity, drawdown_pct: s.drawdown_pct, max_drawdown_pct: s.max_drawdown_pct, circuit_breaker_active: s.circuit_breaker_active,
    regime: s.regime, regimes: s.regimes, peak_equity: s.peak_equity, daily_pnl: s.daily_pnl, daily_limit: s.daily_limit, max_daily_loss_pct: s.max_daily_loss_pct,
    weekly_pnl: s.weekly_pnl, weekly_limit: s.weekly_limit, max_weekly_loss_pct: s.max_weekly_loss_pct, drawdown_halted: s.drawdown_halted,
    killed_strategies: s.killed_strategies, compounding_enabled: s.compounding_enabled, equity_basis: s.equity_basis, restLoadedAt: s.restLoadedAt, account: s.account,
  })));
  const metrics = useTradingStore(useShallow((s) => s.metrics));
  const positionsMap = useTradingStore(useShallow((s) => s.positions));
  const positions = useMemo(() => Object.values(positionsMap).flat(), [positionsMap]);

  usePolling(refreshRiskIntoStore, RISK_POLL_MS);

  const hasRest = risk.restLoadedAt > 0;
  const halted = risk.circuit_breaker_active || risk.drawdown_halted;
  const equity = metrics.equity > 0 ? metrics.equity : risk.equity;

  // A day whose PnL is fees only (-0.0032) printed as "-$0.00": a signed zero reads as a losing day
  // that is not one. Show enough digits to see what it actually is (2026-09-04).
  const money = (v: number) => formatSignedMoney(v, Math.abs(v) > 0 && Math.abs(v) < 0.005 ? 4 : 2);

  const ladder: LadderRow[] = [
    {
      label: "Daily loss", hint: HINTS.dailyPnl,
      used: risk.daily_limit > 0 ? Math.min(1, Math.max(0, -risk.daily_pnl) / risk.daily_limit) : null,
      current: <Signed value={risk.daily_pnl} format={money} />,
      limit: risk.daily_limit > 0 ? `-${formatMoney(risk.daily_limit)} (${formatPct(risk.max_daily_loss_pct, 1)})` : "n/a",
      note: "resets 00:00 UTC",
    },
    {
      label: "Weekly loss", hint: HINTS.weeklyPnl,
      used: risk.weekly_limit > 0 ? Math.min(1, Math.max(0, -risk.weekly_pnl) / risk.weekly_limit) : null,
      current: <Signed value={risk.weekly_pnl} format={money} />,
      limit: risk.weekly_limit > 0 ? `-${formatMoney(risk.weekly_limit)} (${formatPct(risk.max_weekly_loss_pct, 1)})` : "n/a",
      note: "ISO week · resets Monday 00:00 UTC",
    },
    {
      label: "Drawdown from peak", hint: HINTS.drawdown,
      used: risk.max_drawdown_pct > 0 ? Math.min(1, risk.drawdown_pct / risk.max_drawdown_pct) : null,
      current: <span className={cn("num", risk.drawdown_pct > 0 && "text-rose")}>{formatPct(risk.drawdown_pct)}</span>,
      limit: `${formatPct(risk.max_drawdown_pct, 1)} → circuit breaker`,
      note: risk.peak_equity > 0 ? `peak equity ${formatMoney(risk.peak_equity)}` : undefined,
    },
  ];

  const exposure = useMemo(() => {
    const by: Record<string, number> = {};
    for (const p of positions) by[p.symbol] = (by[p.symbol] ?? 0) + positionNotional(p);
    const rows: { symbol: string; notional: number; ratio: number }[] = SYMBOLS.map((s) => ({ symbol: s, notional: by[s] ?? 0, ratio: equity > 0 ? (by[s] ?? 0) / equity : 0 }));
    const known = new Set<string>(SYMBOLS);
    for (const [s, n] of Object.entries(by)) if (!known.has(s)) rows.push({ symbol: s, notional: n, ratio: equity > 0 ? n / equity : 0 });
    return rows;
  }, [positions, equity]);
  const totalExposure = exposure.reduce((a, r) => a + r.notional, 0);
  const maxExposurePct = risk.account?.max_total_exposure_pct ?? null;
  // The risk manager's cap is equity x max_total_exposure_pct x max_leverage (risk_manager.py
  // _check_total_exposure). Showing "limit 60 %" next to a dollar total implied the cap was 60 % of
  // equity: at $418.92 that reads as 69 % of the budget used when the real answer is 14 % (2026-09-04).
  const maxLeverage = risk.account?.max_leverage ?? null;
  const exposureCapUsd = maxExposurePct !== null && maxLeverage !== null && equity > 0
    ? equity * maxExposurePct * maxLeverage : null;
  const exposureUsed = exposureCapUsd && exposureCapUsd > 0 ? totalExposure / exposureCapUsd : null;
  const killed = Object.entries(risk.killed_strategies ?? {});
  // /api/regime covers the engine's symbols AND every market the trend book trades or holds
  // (classified from the venue's own bars); the socket only carries the engine's four.
  const regimeEp = useEndpoint(() => api.regime(), 30_000);
  const regimeRows: { symbol: string; regime: string; source: string; tf: number }[] = regimeEp.data
    ? Object.entries(regimeEp.data.symbols).map(([symbol, r]) => ({ symbol, regime: r.regime ?? "UNKNOWN", source: r.source ?? "engine", tf: r.timeframe_min ?? 15 }))
    : Object.entries(risk.regimes).map(([symbol, regime]) => ({ symbol, regime, source: "engine", tf: 15 }));

  return (
    <div className="flex flex-col gap-3 p-3 sm:p-4 min-w-0">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="text-[18px] font-semibold text-text flex items-center gap-2"><Shield className="w-5 h-5 text-mint" /> Risk</h1>
        {risk.compounding_enabled !== null && (
          <Chip tone={risk.compounding_enabled ? "mint" : "neutral"} size="sm" title={risk.compounding_enabled ? `Sizing on all-time equity (basis ${formatMoney(risk.equity_basis)})` : "Sizing on the fixed initial capital"}>
            Compounding {risk.compounding_enabled ? "ON" : "OFF"}
          </Chip>
        )}
      </div>

      <RiskProfileCards />

      <div className="grid grid-cols-2 xl:grid-cols-4 gap-2">
        <KpiCard label="Circuit breaker" hint="Trips on the max-drawdown limit or an engine halt; no new entries while tripped" value={<span className={cn("inline-flex items-center gap-2", halted ? "text-rose" : "text-mint")}>{halted ? <CircleOff className="w-5 h-5" /> : <Shield className="w-5 h-5" />}{risk.circuit_breaker_active ? "TRIPPED" : risk.drawdown_halted ? "HALTED" : "NORMAL"}</span>} sub={risk.drawdown_halted ? "Halted by the drawdown limit" : "All limits inside budget"} />
        {/* The risk manager's peak tracks REALISED equity — the basis the drawdown ladder measures
            against. Printed as plain "Peak" under the live value it read as a peak below the current
            equity, which looks like a bug (audit 2026-09-03). */}
        <KpiCard label="Equity" hint="Live equity including open positions (same as the top bar). The peak below is the realised equity the drawdown ladder measures from."
                 value={formatMoney(equity)}
                 sub={risk.peak_equity > 0 ? `Peak equity ${formatMoney(risk.peak_equity)} (mark-to-market)` : "Peak not reported yet"} />
        <KpiCard label="Session drawdown" hint={HINTS.drawdown} value={<span className={cn(risk.drawdown_pct > 0 && "text-rose")}>{formatPct(risk.drawdown_pct)}</span>} sub={`All-time max ${formatPct(metrics.max_drawdown)} · limit ${formatPct(risk.max_drawdown_pct, 0)}`}>
          <ProgressBar ratio={risk.max_drawdown_pct > 0 ? risk.drawdown_pct / risk.max_drawdown_pct : 0} tone={tone(risk.max_drawdown_pct > 0 ? risk.drawdown_pct / risk.max_drawdown_pct : 0)} />
        </KpiCard>
        <KpiCard label="Regime" hint={HINTS.regime} value={<RegimeChip regime={risk.regime} size="md" />} sub={`${metrics.total_trades} trades all time`} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Panel>
          <PanelHeader title="Loss limits" right={!hasRest ? <span className="text-[12px] font-medium text-text-2">daily / weekly limits need bridge ≥ 2.14</span> : undefined} />
          <div className="px-4 py-3 space-y-4">
            {ladder.map((row) => (
              <div key={row.label}>
                <ListRow label={row.label} hint={row.hint} size="md">
                  <span className="inline-flex items-baseline gap-1.5">{row.current}<span className="text-text-2 font-medium">/ {row.limit}</span></span>
                </ListRow>
                <ProgressBar ratio={row.used ?? 0} tone={tone(row.used)} height="h-2.5" />
                <div className="flex justify-between mt-1 text-[12px] font-medium text-text-2">
                  <span>{row.used === null ? "no limit reported" : `${(row.used * 100).toFixed(0)}% used`}{row.note ? ` · ${row.note}` : ""}</span>
                  {/* A bare "100%" opposite "0% used" reads as a second, contradictory measurement.
                      It is the end of the bar: say so. */}
                  <span>{row.used === null ? "" : `${Math.max(0, 100 - row.used * 100).toFixed(0)}% left`}</span>
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Exposure by symbol" right={
            <span className="text-[12px] font-medium text-text-2">
              total <span className="text-text font-semibold">{formatMoney(totalExposure)}</span>
              {exposureCapUsd !== null
                ? <> of <span className="text-text font-semibold">{formatMoney(exposureCapUsd)}</span> · {formatPct(exposureUsed ?? 0, 1)} used</>
                : maxExposurePct !== null ? ` · limit ${formatPct(maxExposurePct, 0)}` : ""}
            </span>} />
          <div className="px-4 py-3 space-y-3">
            {exposure.map((r) => (
              <div key={r.symbol}>
                <ListRow label={r.symbol}><span className="inline-flex items-baseline gap-1.5">{formatMoney(r.notional)}<span className="text-text-2 font-medium">· {formatPct(r.ratio, 1)} of equity</span></span></ListRow>
                {/* measured against the real cap, not against 60 % of equity */}
                <ProgressBar ratio={exposureCapUsd ? r.notional / exposureCapUsd : r.ratio} tone={r.ratio > 0.5 ? "amber" : "mint"} />
              </div>
            ))}
            {positions.length === 0 && <p className="text-[12.5px] font-medium text-text">No open positions — exposure 0 %</p>}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Panel>
          <PanelHeader title="Killed by edge monitor" right={<Chip tone={killed.length ? "rose" : "mint"} size="xs">{killed.length}</Chip>} />
          <div className="px-4 py-3">
            {killed.length === 0 ? (
              <p className="text-[12.5px] font-medium text-text">No strategy is killed — all clear.</p>
            ) : (
              <ul className="space-y-2">
                {killed.map(([type, v]) => (
                  <li key={type} className="flex items-start gap-2 text-[12.5px]">
                    <StrategyTag strategy={type} />
                    <span className="font-medium text-text-2 break-words min-w-0">{killReason(v)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="Sizing basis" />
          <ListSection first>
            <ListRow label="Compounding" hint="ON: sizing uses all-time equity; OFF: the fixed initial capital">{risk.compounding_enabled === null ? "---" : risk.compounding_enabled ? "ON" : "OFF"}</ListRow>
            <ListRow label="Equity basis" hint="Capital the risk manager sizes against right now">{risk.equity_basis > 0 ? formatMoney(risk.equity_basis) : "---"}</ListRow>
            <ListRow label="Max leverage" hint="The risk manager's cap on any position; the trend book sizes by volatility under its own, lower ceiling">
              {risk.account?.max_leverage ? `${risk.account.max_leverage}x` : "---"}
              {risk.account?.trend_leverage_cap ? <span className="text-text-2 font-medium"> · trend book ≤{risk.account.trend_leverage_cap}x</span> : null}
            </ListRow>
            <ListRow label="Max total exposure" hint="Cap on the sum of open notionals: equity x this share x max leverage">
              {maxExposurePct !== null ? formatPct(maxExposurePct, 0) : "---"}
              {exposureCapUsd !== null && <span className="text-text-2 font-medium"> · {formatMoney(exposureCapUsd)}</span>}
            </ListRow>
            <ListRow label="Open positions">{positions.length}</ListRow>
            <ListRow label="Regimes" hint="Intraday classifier (15-minute bars) for the engine's symbols and for every market the trend book trades or holds, the latter from the venue's own bars. Informational: the trend book does not read it.">
              <span className="inline-flex flex-wrap gap-1 justify-end">{regimeRows.map((r) => <span key={r.symbol} title={`${r.source} · ${r.tf}m bars`}><RegimeChip regime={r.regime} size="xs" suffix={<span className="ml-1 text-text">{r.symbol.split("-")[0]}</span>} /></span>)}</span>
            </ListRow>
          </ListSection>
        </Panel>
      </div>
    </div>
  );
}
