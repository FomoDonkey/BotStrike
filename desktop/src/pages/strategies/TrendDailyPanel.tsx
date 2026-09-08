import { useMemo } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import { useEndpoint } from "@/hooks/useEndpoint";
import { useNow } from "@/hooks/useNow";
import { Panel, PanelHeader, EmptyState } from "@/components/ui/Panel";
import { StatusChip, Chip } from "@/components/ui/Chip";
import { ListRow, Signed } from "@/components/ui/ListRow";
import { ProgressBar } from "@/components/ui/KpiCard";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { marketName } from "@/lib/market";
import { Hint } from "@/components/shared/Hint";
import { Freshness } from "@/components/shared/Freshness";
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP_ITEM, CHART_TOOLTIP_LABEL, CHART_TOOLTIP_STYLE, COLOR_BLUE, COLOR_UP } from "@/lib/constants";
import { cn, formatCompactUSD, formatLocalDateTime, formatMoney, formatPct, formatPrice, formatRelative, formatSignedMoney, formatSignedPct, formatSize } from "@/lib/utils";
import { trimNumber } from "@/components/settings/schemaUtils";
import type { TrendPosition } from "@/lib/api";

/** Full status of the daily trend engine: schedule, universe, targets, positions and tracking. */
export function TrendDailyPanel() {
  const ep = useEndpoint(() => api.trend(), 30_000);
  const trend = ep.data;
  const now = useNow();

  const chartData = useMemo(() => {
    const recs = [...(trend?.tracking?.records ?? [])].sort((a, b) => a.date.localeCompare(b.date));
    const out: { date: string; model: number; paper: number; slippage: number }[] = [];
    let m = 1;
    let p = 1;
    for (const r of recs) {
      m *= 1 + (Number.isFinite(r.model_ret) ? r.model_ret : 0);
      p *= 1 + (Number.isFinite(r.paper_ret) ? r.paper_ret : 0);
      out.push({ date: r.date, model: (m - 1) * 100, paper: (p - 1) * 100, slippage: r.slippage_bps ?? 0 });
    }
    return out;
  }, [trend]);

  if (!trend) {
    return (
      <Panel className="p-6">
        <EmptyState sub={ep.error ?? undefined}>{ep.error ? "Trend daily status unavailable" : ep.missing ? "This bridge has no trend daily engine" : "Loading trend daily status…"}</EmptyState>
      </Panel>
    );
  }

  const nextMs = trend.next_run_utc ? Date.parse(trend.next_run_utc) : Number.NaN;
  const targets = Object.entries(trend.targets ?? {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const barScale = Math.max(1e-9, ...targets.map(([, w]) => Math.abs(w)));
  const tracking = trend.tracking;
  const statusKind = /^(ok|success|done)$/i.test(trend.last_run_status) ? "ok" : /(error|fail)/i.test(trend.last_run_status) ? "error" : "disabled";
  // The venue liquidity floors (round 9, 2026-09-05): a member the venue no longer trades enough
  // leaves the same day, and a pool that cannot be measured is never re-picked. Both were
  // invisible on this page until 2026-09-08 — the panel showed the universe as if it were a pure
  // signal decision.
  const liq = trend.liquidity;
  const liqRows = Object.entries(liq?.markets ?? {}).sort((a, b) => Number(b[1].member) - Number(a[1].member) || (b[1].venue_24h ?? -1) - (a[1].venue_24h ?? -1));
  const basis = Object.entries(trend.basis ?? {});
  const basisWarn = trend.basis_warn ?? 0.025;
  const basisFlagged = basis.filter(([, b]) => Math.abs(b) >= basisWarn);

  const posColumns: Column<TrendPosition>[] = [
    { id: "symbol", label: "Symbol", align: "l", render: (p) => <span className="font-semibold">{p.ui_symbol ?? marketName(p.symbol)}</span> },
    { id: "size", label: "Size", render: (p) => <span className="num">{formatSize(p.size)}</span> },
    { id: "entry", label: "Entry", render: (p) => <span className="num">{formatPrice(p.entry_price)}</span> },
    { id: "mark", label: "Mark", render: (p) => <span className="num">{formatPrice(p.mark_price)}</span> },
    { id: "pnl", label: "uPnL", render: (p) => <Signed value={p.unrealized_pnl} format={formatSignedMoney} /> },
    { id: "weight", label: "Weight", render: (p) => <span className="num">{formatPct(p.weight, 1)}</span> },
    { id: "opened", label: "Opened", align: "l", render: (p) => p.opened },
  ];

  return (
    <Panel className="flex flex-col min-w-0">
      <PanelHeader
        title="Trend daily · Donchian ensemble"
        right={
          <>
            <Freshness at={ep.at} error={ep.error} every={30_000} className="hidden md:inline" />
            <StatusChip status={trend.enabled ? "enabled" : "disabled"} size="xs" />
            <StatusChip status={trend.mode} size="xs" />
            <span className="hidden sm:inline text-[12px] font-medium text-text-2">alloc <span className="text-text font-semibold">{formatPct(trend.allocation, 0)}</span> · exposure <span className="text-text font-semibold">{formatPct(trend.exposure ?? 0, 0)}</span> · <Hint title="The equity the last run sized on (initial capital + realised + open PnL at 04:05 UTC). The account's live equity moves with the marks until the next run.">sized on</Hint> <span className="text-text font-semibold">{formatMoney(trend.equity_basis ?? 0)}</span></span>
          </>
        }
      />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-x-6 px-4 py-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 h-7 flex items-center">Schedule</p>
          <ListRow label="Next run">{formatLocalDateTime(trend.next_run_utc)}{Number.isFinite(nextMs) && <span className="text-text-2 font-medium"> · {formatRelative(nextMs, now)}</span>}</ListRow>
          <ListRow label="Last run"><span className="inline-flex items-center gap-2">{formatLocalDateTime(trend.last_run_utc)} <StatusChip status={statusKind} label={trend.last_run_status || "never"} size="xs" />{trend.last_run_late && <Chip tone="amber" size="xs" title="The run happened after its slot and filled at the current price, not at the 04:05 open">late</Chip>}</span></ListRow>
          {trend.last_error && <p className="text-[12.5px] font-medium text-rose break-words mt-1">{trend.last_error}</p>}
          {trend.last_adds_blocked && <p className="text-[12.5px] font-medium text-amber break-words mt-1" title="The risk limits held the book's adds at the last run; exits always execute">Adds held by risk: {trend.last_adds_blocked}</p>}
          {trend.killed && <p className="text-[12.5px] font-medium text-rose mt-1">Killed by the edge monitor — the book is closed and no run opens positions.</p>}
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 h-7 flex items-center mt-2">
            Universe <span className="ml-1 normal-case tracking-normal font-medium">({trend.universe?.length ?? 0} of {trend.candidates} candidates)</span>
          </p>
          <div className="flex flex-wrap gap-1.5">
            {(trend.universe ?? []).map((sym) => {
              const w = trend.targets?.[sym] ?? 0;
              return <Chip key={sym} tone={w > 0 ? "mint" : "outline"} size="xs" uppercase={false}>{marketName(sym)}</Chip>;
            })}
            {(trend.universe?.length ?? 0) === 0 && <span className="text-[12.5px] font-medium text-text">empty</span>}
          </div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 h-7 flex items-center mt-2">Params</p>
          <div className="grid grid-cols-2 gap-1">
            {Object.entries(trend.params ?? {}).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2 text-[12px] rounded-[6px] bg-panel-2 px-2 py-1 min-w-0">
                <span className="font-medium text-text-2 truncate" title={k}>{k.replace(/_/g, " ")}</span>
                <span className="num font-semibold text-text truncate">{typeof v === "number" ? trimNumber(v) : Array.isArray(v) ? v.join(",") : String(v ?? "---")}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 h-7 flex items-center">Target weights</p>
          {targets.length === 0 ? (
            <p className="text-[12.5px] font-medium text-text">No targets (flat)</p>
          ) : (
            <div className="space-y-2">
              {targets.map(([sym, w]) => (
                <div key={sym} className="text-[12.5px]">
                  <div className="flex justify-between mb-1">
                    <span className="font-semibold text-text">{marketName(sym)}</span>
                    <span className={cn("num font-semibold", w > 0 ? "text-mint" : w < 0 ? "text-rose" : "text-text")}>{formatPct(w, 1)}</span>
                  </div>
                  <ProgressBar ratio={Math.abs(w) / barScale} tone={w >= 0 ? "blue" : "rose"} />
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="min-w-0 flex flex-col">
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 h-7 flex items-center">Positions ({trend.positions?.length ?? 0})</p>
          <div className="flex flex-col min-h-[120px] rounded-[6px] border border-hairline overflow-hidden">
            <DataTable columns={posColumns} rows={trend.positions ?? []} rowKey={(p) => p.symbol} minWidth="520px" emptyText="No open positions" />
          </div>
        </div>
      </div>

      {liq && (
        <div className="px-4 py-3 border-t border-hairline">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-2 text-[12.5px] font-medium">
            <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2">
              <Hint title="Every pool market against the venue's own 24 h volume. A market must trade at least the ENTER floor to be picked and at least the EXIT floor to stay; a member below the exit floor leaves at the next run. Without venue volumes the pick fails closed and the universe is kept.">Venue liquidity</Hint>
            </span>
            <span className="text-text-2">enter ≥ <span className="text-text font-semibold num">{formatCompactUSD(liq.enter_floor)}</span></span>
            <span className="text-text-2">exit ≥ <span className="text-text font-semibold num">{formatCompactUSD(liq.exit_floor)}</span></span>
            <span className="text-text-2">per 24 h on the venue</span>
            {!liq.available && <Chip tone="amber" size="xs" title="The venue's volumes could not be fetched: the universe is kept as it is until they can">volumes unavailable · pick held</Chip>}
            {trend.liquidity_note && <span className="text-amber">{trend.liquidity_note}</span>}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {liqRows.map(([sym, r]) => {
              const bad = r.member ? !r.ok_exit : !r.ok_enter;
              const title = r.venue_24h === null
                ? `${sym}: the venue publishes no 24 h volume — cannot be entered or exited by the floor`
                : r.member
                  ? `${sym} is in the universe · ${formatCompactUSD(r.venue_24h)} / 24 h on the venue · ${r.ok_exit ? "above the exit floor" : "BELOW the exit floor: leaves at the next run"}`
                  : `${sym} is a candidate · ${formatCompactUSD(r.venue_24h)} / 24 h on the venue · ${r.ok_enter ? "above the enter floor" : "below the enter floor: cannot be picked today"}`;
              return (
                <span key={sym} title={title}
                      className={cn("inline-flex items-baseline gap-1 rounded-[6px] px-2 py-1 bg-panel-2 text-[12px]", r.member && "ring-1 ring-hairline-strong")}>
                  <span className="font-semibold text-text">{marketName(sym)}</span>
                  <span className={cn("num font-semibold", bad ? "text-rose" : "text-text-2")}>{r.venue_24h === null ? "no volume" : formatCompactUSD(r.venue_24h)}</span>
                  {r.member && <span className="text-[11px] text-mint">held</span>}
                  {bad && <span className="text-[11px] text-rose">{r.member ? "exits" : "too thin"}</span>}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {basis.length > 0 && (
        <div className="px-4 py-3 border-t border-hairline">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-2 text-[12.5px] font-medium">
            <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2">
              <Hint title="Strike's mark against the last settled close of the daily data source the signal is computed from (Binance spot for crypto, Yahoo futures for metals, energy and indices). The intraday move since that close is expected; a gap that persists means the two prices have parted.">Basis vs signal source</Hint>
            </span>
            <span className="text-text-2">warn at <span className="text-text font-semibold num">{formatPct(basisWarn, 1)}</span></span>
            {basisFlagged.length > 0 && <Chip tone="amber" size="xs">{basisFlagged.length} flagged</Chip>}
            {typeof trend.basis_ts === "number" && trend.basis_ts > 0 && <span className="text-text-2">measured {formatRelative(trend.basis_ts * 1000, now)}</span>}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {basis.map(([sym, b]) => (
              <span key={sym} className="inline-flex items-baseline gap-1 rounded-[6px] px-2 py-1 bg-panel-2 text-[12px]" title={`${sym}: Strike mark / last settled source close − 1`}>
                <span className="font-semibold text-text">{marketName(sym)}</span>
                <span className={cn("num font-semibold", Math.abs(b) >= basisWarn ? "text-amber" : "text-text-2")}>{formatSignedPct(b, 2)}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="px-4 py-3 border-t border-hairline">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-2 text-[12.5px] font-medium">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2">
            <Hint title="Model = the strategy's own return from the daily source bars (close to close). Paper = what the paper book made over the same day at the venue's marks. The gap is execution: 04:05 fills instead of the close, Strike marks instead of Binance/Yahoo, the rebalance dead-band, fees and funding. TE = annualised tracking error of the daily differences.">Tracking</Hint>
          </span>
          <span className="text-text-2"><span className="text-text font-semibold">{tracking?.days ?? 0}</span> days</span>
          <span className="text-text-2">model <span className="font-semibold" style={{ color: COLOR_BLUE }}>{formatPct(tracking?.model_return ?? 0)}</span></span>
          <span className="text-text-2">paper <span className="font-semibold" style={{ color: COLOR_UP }}>{formatPct(tracking?.paper_return ?? 0)}</span></span>
          <span className="text-text-2">TE (ann.) <span className={cn("font-semibold", (tracking?.tracking_error_ann ?? 0) > 0.05 ? "text-amber" : "text-text")}>{formatPct(tracking?.tracking_error_ann ?? 0)}</span></span>
        </div>
        {chartData.length >= 2 ? (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={CHART_GRID} vertical={false} />
              <XAxis dataKey="date" tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={40} />
              <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={false} tickLine={false} width={48} tickFormatter={(v) => `${Number(v).toFixed(1)}%`} />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={CHART_TOOLTIP_LABEL} itemStyle={CHART_TOOLTIP_ITEM} formatter={(v: unknown, name: unknown) => [`${Number(v).toFixed(2)}%`, String(name)]} />
              <Legend wrapperStyle={{ fontSize: 12, color: "#FFFFFF" }} />
              <Line type="monotone" dataKey="model" name="Model" stroke={COLOR_BLUE} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="paper" name="Paper" stroke={COLOR_UP} strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-[12.5px] font-medium text-text">Not enough daily records yet — the chart needs 2+ days.</p>
        )}
      </div>
    </Panel>
  );
}
