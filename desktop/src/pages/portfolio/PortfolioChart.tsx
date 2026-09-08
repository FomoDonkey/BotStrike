import { useMemo, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EquityHistoryMeta, PortfolioDay } from "@/lib/api";
import { TabBar } from "@/components/ui/TabBar";
import { RangePills } from "@/components/ui/SegmentedControl";
import { CalendarHeatmap } from "@/components/ui/CalendarHeatmap";
import { EmptyState } from "@/components/ui/Panel";
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP_ITEM, CHART_TOOLTIP_LABEL, CHART_TOOLTIP_STYLE, COLOR_DOWN, COLOR_UP } from "@/lib/constants";
import { formatDate, formatDateTime, formatMoney, formatSignedMoney } from "@/lib/utils";

type Tab = "value" | "pnl" | "volume" | "calendar";
type Range = "7d" | "30d" | "all";
const TABS = [{ id: "value" as const, label: "Account Value" }, { id: "pnl" as const, label: "PNL" }, { id: "volume" as const, label: "Volume" }, { id: "calendar" as const, label: "Calendar" }];
const RANGES = [{ id: "7d" as const, label: "7D" }, { id: "30d" as const, label: "30D" }, { id: "all" as const, label: "ALL" }];
const DAY = 86400;

function dayLabel(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  return d.toLocaleDateString([], { month: "short", day: "numeric", timeZone: "UTC" });
}

interface Props {
  days: PortfolioDay[];
  missing: boolean;
  todayIso: string;
  /** the MARK-TO-MARKET equity path: [epoch s, equity] — minute samples, estimated day-ends before `history.real_since` */
  curve?: [number, number][] | null;
  history?: EquityHistoryMeta | null;
  /** the all-time mark-to-market peak, drawn as a reference line */
  peak?: number | null;
  nowSec: number;
}

/**
 * Portfolio centre card (spec §3.2): Account Value · PNL · Volume · Calendar with 7D / 30D / ALL.
 *
 * "Account Value" draws the MARKED equity (analytics/equity_history.py), not the cash chain: until
 * 2026-09-08 the chart plotted the realised balance per day with the open PnL folded into the last
 * point only, so its maximum was always "now" while the account had been at 1,038 and back. The
 * days before the first real sample are estimates (fills priced at the daily source close) and are
 * drawn dashed and said so. The PNL bars are the day's marked move; the realised cash rides in the tooltip.
 */
export function PortfolioChart({ days, missing, todayIso, curve, history, peak, nowSec }: Props) {
  const [tab, setTab] = useState<Tab>("value");
  const [range, setRange] = useState<Range>("7d");

  const dayData = useMemo(() => {
    const n = range === "7d" ? 7 : range === "30d" ? 30 : days.length;
    return days.slice(Math.max(0, days.length - n)).map((d) => ({ ...d, label: dayLabel(d.date), pnlShown: d.pnl_mtm ?? d.pnl }));
  }, [days, range]);

  const realSince = history?.real_since ?? null;
  const estUntil = history?.estimated_until ?? null;
  const valueData = useMemo(() => {
    const pts = curve ?? [];
    if (!pts.length) return [];
    const from = range === "all" ? -Infinity : nowSec - (range === "7d" ? 7 : 30) * DAY;
    // two series on one axis: the estimated prefix (dashed) and the sampled path; they share the
    // boundary point so the line is continuous
    const out: { ts: number; est: number | null; real: number | null; isEst: boolean }[] = [];
    for (const [ts, eq] of pts) {
      if (ts < from) continue;
      const isEst = realSince === null ? true : ts < realSince;
      out.push({ ts, est: isEst ? eq : null, real: isEst ? null : eq, isEst });
    }
    const firstReal = out.findIndex((p) => !p.isEst);
    if (firstReal > 0) out[firstReal - 1].real = out[firstReal - 1].est;
    return out;
  }, [curve, range, nowSec, realSince]);
  const hasEst = valueData.some((p) => p.isEst);
  const calendarDays = useMemo(() => days.map((d) => ({ ...d, pnl: d.pnl_mtm ?? d.pnl })), [days]);

  const body = () => {
    if (missing) return <EmptyState sub="GET /api/portfolio needs bridge ≥ 2.16">Daily portfolio history not available on this bridge</EmptyState>;
    if (tab === "calendar") return <CalendarHeatmap days={calendarDays} todayIso={todayIso} />;
    if (tab === "value") {
      if (valueData.length < 2) return <EmptyState sub="The bridge samples the marked equity once a minute from the moment it starts">Not enough equity history yet</EmptyState>;
      const values = valueData.map((p) => (p.real ?? p.est) as number);
      const min = Math.min(...values, peak && peak > 0 ? peak : Infinity);
      const max = Math.max(...values, peak && peak > 0 ? peak : -Infinity);
      const pad = Math.max(1, (max - min) * 0.15);
      const dec = max - min < 20 ? 2 : 0;
      const spanDays = (valueData[valueData.length - 1].ts - valueData[0].ts) / DAY;
      const tick = (t: number) => new Date(t * 1000).toLocaleString([], spanDays > 3 ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      return (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={valueData} margin={{ top: 14, right: 12, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="pfValue" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLOR_UP} stopOpacity={0.25} />
                <stop offset="100%" stopColor={COLOR_UP} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={CHART_GRID} vertical={false} />
            <XAxis dataKey="ts" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={tick} tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={{ stroke: "rgba(255,255,255,0.18)" }} tickLine={false} minTickGap={48} />
            <YAxis domain={[min - pad, max + pad]} tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={false} tickLine={false} width={72} tickFormatter={(v) => formatMoney(Number(v), dec)} />
            <Tooltip
              contentStyle={CHART_TOOLTIP_STYLE} labelStyle={CHART_TOOLTIP_LABEL} itemStyle={CHART_TOOLTIP_ITEM}
              labelFormatter={(v) => formatDateTime(Number(v))}
              formatter={(v: unknown, name: unknown) => [formatMoney(Number(v)), name === "est" ? "Account value (estimated: daily source close)" : "Account value (marked)"]}
            />
            {peak && peak > 0 && <ReferenceLine y={peak} stroke="rgba(255,255,255,0.45)" strokeDasharray="3 3" label={{ value: `peak ${formatMoney(peak)}`, position: "insideTopRight", fill: "#FFFFFF", fontSize: 11 }} />}
            <Line type="monotone" dataKey="est" stroke={COLOR_UP} strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} connectNulls={false} />
            <Area type="monotone" dataKey="real" stroke={COLOR_UP} strokeWidth={2} fill="url(#pfValue)" dot={false} isAnimationActive={false} connectNulls={false} />
          </AreaChart>
        </ResponsiveContainer>
      );
    }
    if (dayData.length === 0) return <EmptyState sub="The bridge records one row per UTC day from the first run">No daily data yet</EmptyState>;
    const key = tab === "pnl" ? "pnlShown" : "volume";
    return (
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={dayData} margin={{ top: 12, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={CHART_GRID} vertical={false} />
          <XAxis dataKey="label" tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={{ stroke: "rgba(255,255,255,0.18)" }} tickLine={false} minTickGap={32} />
          <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={false} tickLine={false} width={72} tickFormatter={(v) => (tab === "pnl" ? formatSignedMoney(Number(v), 0) : formatMoney(Number(v), 0))} />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE} labelStyle={CHART_TOOLTIP_LABEL} itemStyle={CHART_TOOLTIP_ITEM} cursor={{ fill: "rgba(255,255,255,0.06)" }}
            formatter={(v: unknown, _name: unknown, item: { payload?: PortfolioDay & { pnlShown: number } }) => {
              if (tab !== "pnl") return [formatMoney(Number(v)), "Volume"];
              const d = item?.payload;
              const realised = d ? ` · realised cash ${formatSignedMoney(d.pnl)}${d.equity_est ? " · estimated day" : ""}` : "";
              return [`${formatSignedMoney(Number(v))}${realised}`, d?.pnl_mtm !== undefined ? "Daily PnL (marked)" : "Daily PnL (realised)"];
            }}
          />
          {tab === "pnl" && <ReferenceLine y={0} stroke="rgba(255,255,255,0.3)" />}
          <Bar dataKey={key} isAnimationActive={false} maxBarSize={28} radius={[3, 3, 0, 0]}>
            {dayData.map((d) => <Cell key={d.date} fill={tab === "pnl" ? (d.pnlShown >= 0 ? COLOR_UP : COLOR_DOWN) : COLOR_UP} fillOpacity={tab === "pnl" ? (d.equity_est ? 0.5 : 0.85) : 0.6} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    );
  };

  const note = tab === "value" && hasEst
    ? `Dashed = estimated${estUntil ? ` up to ${formatDate(estUntil)}` : ""}: fills valued at each day's source close (Binance / Yahoo), not the venue's mark. Solid = the account sampled every minute${realSince ? ` since ${formatDateTime(realSince)}` : ""}.`
    : tab === "value" && valueData.length >= 2 && history?.samples
      ? "Marked equity, one sample a minute (analytics/equity_history)."
      : tab === "pnl" && dayData.some((d) => d.pnl_mtm !== undefined)
        ? "Bars: the day's marked move (open positions included). Hover for the realised cash of the day."
        : null;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TabBar size="sm" tabs={TABS} value={tab} onChange={setTab} right={tab !== "calendar" ? <RangePills options={RANGES} value={range} onChange={setRange} /> : undefined} />
      <div className="relative flex-1 min-h-[200px]">
        <div className="absolute inset-0 flex flex-col p-1">{body()}</div>
      </div>
      {note && <p className="shrink-0 px-3 py-1.5 border-t border-hairline text-[11.5px] font-medium text-text-2 leading-snug">{note}</p>}
    </div>
  );
}
