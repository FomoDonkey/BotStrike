import { useMemo, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PortfolioDay } from "@/lib/api";
import { TabBar } from "@/components/ui/TabBar";
import { RangePills } from "@/components/ui/SegmentedControl";
import { CalendarHeatmap } from "@/components/ui/CalendarHeatmap";
import { EmptyState } from "@/components/ui/Panel";
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP_ITEM, CHART_TOOLTIP_LABEL, CHART_TOOLTIP_STYLE, COLOR_DOWN, COLOR_UP } from "@/lib/constants";
import { formatMoney, formatSignedMoney } from "@/lib/utils";

type Tab = "value" | "pnl" | "volume" | "calendar";
type Range = "7d" | "30d" | "all";
const TABS = [{ id: "value" as const, label: "Account Value" }, { id: "pnl" as const, label: "PNL" }, { id: "volume" as const, label: "Volume" }, { id: "calendar" as const, label: "Calendar" }];
const RANGES = [{ id: "7d" as const, label: "7D" }, { id: "30d" as const, label: "30D" }, { id: "all" as const, label: "ALL" }];

function dayLabel(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  return d.toLocaleDateString([], { month: "short", day: "numeric", timeZone: "UTC" });
}

/** Portfolio centre card (spec §3.2): Account Value · PNL · Volume · Calendar with 7D / 30D / ALL. */
export function PortfolioChart({ days, missing, todayIso }: { days: PortfolioDay[]; missing: boolean; todayIso: string }) {
  const [tab, setTab] = useState<Tab>("value");
  const [range, setRange] = useState<Range>("7d");

  const data = useMemo(() => {
    const n = range === "7d" ? 7 : range === "30d" ? 30 : days.length;
    return days.slice(Math.max(0, days.length - n)).map((d) => ({ ...d, label: dayLabel(d.date) }));
  }, [days, range]);
  const last = data.length ? data[data.length - 1] : null;

  const body = () => {
    if (missing) return <EmptyState sub="GET /api/portfolio needs bridge ≥ 2.16">Daily portfolio history not available on this bridge</EmptyState>;
    if (tab === "calendar") return <CalendarHeatmap days={days} todayIso={todayIso} />;
    if (data.length === 0) return <EmptyState sub="The bridge records one row per UTC day from the first run">No daily data yet</EmptyState>;
    if (tab === "value") {
      const values = data.map((d) => d.equity);
      const min = Math.min(...values);
      const max = Math.max(...values);
      const pad = Math.max(1, (max - min) * 0.2);
      const dec = max - min < 20 ? 2 : 0;
      return (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 12, right: 12, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="pfValue" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLOR_UP} stopOpacity={0.25} />
                <stop offset="100%" stopColor={COLOR_UP} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={CHART_GRID} vertical={false} />
            <XAxis dataKey="label" tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={{ stroke: "rgba(255,255,255,0.18)" }} tickLine={false} minTickGap={32} />
            <YAxis domain={[min - pad, max + pad]} tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={false} tickLine={false} width={72} tickFormatter={(v) => formatMoney(Number(v), dec)} />
            <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={CHART_TOOLTIP_LABEL} itemStyle={CHART_TOOLTIP_ITEM} formatter={(v: unknown) => [formatMoney(Number(v)), "Account value"]} />
            {last && <ReferenceLine x={last.label} stroke="rgba(255,255,255,0.5)" strokeDasharray="3 3" label={{ value: "now", position: "top", fill: "#FFFFFF", fontSize: 11 }} />}
            <Area type="monotone" dataKey="equity" stroke={COLOR_UP} strokeWidth={2} fill="url(#pfValue)" dot={false} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      );
    }
    const key = tab === "pnl" ? "pnl" : "volume";
    return (
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 12, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={CHART_GRID} vertical={false} />
          <XAxis dataKey="label" tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={{ stroke: "rgba(255,255,255,0.18)" }} tickLine={false} minTickGap={32} />
          <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={false} tickLine={false} width={72} tickFormatter={(v) => (tab === "pnl" ? formatSignedMoney(Number(v), 0) : formatMoney(Number(v), 0))} />
          <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={CHART_TOOLTIP_LABEL} itemStyle={CHART_TOOLTIP_ITEM} cursor={{ fill: "rgba(255,255,255,0.06)" }} formatter={(v: unknown) => [tab === "pnl" ? formatSignedMoney(Number(v)) : formatMoney(Number(v)), tab === "pnl" ? "Daily PnL" : "Volume"]} />
          {tab === "pnl" && <ReferenceLine y={0} stroke="rgba(255,255,255,0.3)" />}
          <Bar dataKey={key} isAnimationActive={false} maxBarSize={28} radius={[3, 3, 0, 0]}>
            {data.map((d) => <Cell key={d.date} fill={tab === "pnl" ? (d.pnl >= 0 ? COLOR_UP : COLOR_DOWN) : COLOR_UP} fillOpacity={tab === "pnl" ? 0.85 : 0.6} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    );
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TabBar size="sm" tabs={TABS} value={tab} onChange={setTab} right={tab !== "calendar" ? <RangePills options={RANGES} value={range} onChange={setRange} /> : undefined} />
      <div className="relative flex-1 min-h-[260px]">
        <div className="absolute inset-0 flex flex-col p-1">{body()}</div>
      </div>
    </div>
  );
}
