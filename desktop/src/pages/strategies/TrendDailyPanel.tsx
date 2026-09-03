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
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP_ITEM, CHART_TOOLTIP_LABEL, CHART_TOOLTIP_STYLE, COLOR_BLUE, COLOR_UP } from "@/lib/constants";
import { cn, formatLocalDateTime, formatMoney, formatPct, formatPrice, formatRelative, formatSignedMoney, formatSize } from "@/lib/utils";
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
      out.push({ date: r.date, model: (m - 1) * 100, paper: (p - 1) * 100, slippage: r.slippage_bps });
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
            <StatusChip status={trend.enabled ? "enabled" : "disabled"} size="xs" />
            <StatusChip status={trend.mode} size="xs" />
            <span className="hidden sm:inline text-[12px] font-medium text-text-2">alloc <span className="text-text font-semibold">{formatPct(trend.allocation, 0)}</span> · exposure <span className="text-text font-semibold">{formatPct(trend.exposure ?? 0, 0)}</span> · basis <span className="text-text font-semibold">{formatMoney(trend.equity_basis ?? 0)}</span></span>
          </>
        }
      />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-x-6 px-4 py-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 h-7 flex items-center">Schedule</p>
          <ListRow label="Next run">{formatLocalDateTime(trend.next_run_utc)}{Number.isFinite(nextMs) && <span className="text-text-2 font-medium"> · {formatRelative(nextMs, now)}</span>}</ListRow>
          <ListRow label="Last run"><span className="inline-flex items-center gap-2">{formatLocalDateTime(trend.last_run_utc)} <StatusChip status={statusKind} label={trend.last_run_status || "never"} size="xs" /></span></ListRow>
          {trend.last_error && <p className="text-[12.5px] font-medium text-rose break-words mt-1">{trend.last_error}</p>}
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

      <div className="px-4 py-3 border-t border-hairline">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-2 text-[12.5px] font-medium">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2">Tracking</span>
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
