import { useMemo, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis, Cell } from "recharts";
import { api } from "@/lib/api";
import { useEndpoint } from "@/hooks/useEndpoint";
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP_ITEM, CHART_TOOLTIP_LABEL, CHART_TOOLTIP_STYLE, COLOR_DOWN, COLOR_UP } from "@/lib/constants";
import { RangePills } from "./SegmentedControl";
import { EmptyState } from "./Panel";

type Range = "24h" | "1w" | "1m";
const RANGES = [{ id: "24h" as const, label: "24H" }, { id: "1w" as const, label: "1W" }, { id: "1m" as const, label: "1M" }];
const RANGE_SEC: Record<Range, number> = { "24h": 24 * 3600, "1w": 7 * 86400, "1m": 30 * 86400 };
const POLL_MS = 60_000;

function pct(v: number, d = 4): string {
  return `${(v * 100).toFixed(d)}%`;
}

/**
 * Funding tab: one bar per venue settlement + the cumulative line, 24H / 1W / 1M.
 *
 * ROSE = the rate is positive = the long book PAYS; mint = negative = it is paid. This is the
 * opposite of a price chart and matches the Account panel: the sign of the same quantity cannot
 * mean "good" on one screen and "bad" on another (audit 2026-09-03).
 */
export function FundingChart({ symbol }: { symbol: string }) {
  const [range, setRange] = useState<Range>("1w");
  const fh = useEndpoint(() => api.fundingHistory(symbol, 200), POLL_MS, symbol);

  const { data, cumLast, last, avg } = useMemo(() => {
    const pts = fh.data?.points ?? [];
    const cum = fh.data?.cumulative ?? [];
    const cumByTs = new Map<number, number>();
    for (const c of cum) cumByTs.set(Math.round(c.ts), c.value);
    const nowSec = pts.length ? pts[pts.length - 1].ts : 0;
    const cutoff = nowSec - RANGE_SEC[range];
    const rows: { ts: number; rate: number; cum: number | null; label: string }[] = [];
    let sum = 0;
    for (const p of pts) {
      if (p.ts < cutoff) continue;
      const d = new Date(p.ts * 1000);
      rows.push({
        ts: p.ts,
        rate: p.rate,
        cum: cumByTs.get(Math.round(p.ts)) ?? null,
        label: range === "24h"
          ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
          : d.toLocaleDateString([], { month: "short", day: "numeric" }),
      });
      sum += p.rate;
    }
    return {
      data: rows,
      cumLast: rows.length ? rows[rows.length - 1].cum : null,
      last: rows.length ? rows[rows.length - 1].rate : null,
      avg: rows.length ? sum / rows.length : null,
    };
  }, [fh.data, range]);

  const hasCum = data.some((r) => r.cum !== null);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex items-center gap-4 px-3 h-10 border-b border-hairline shrink-0 overflow-x-auto scrollbar-none">
        <RangePills options={RANGES} value={range} onChange={setRange} />
        <Stat label="Last settlement" value={last === null ? "---" : pct(last)} tone={last === null ? undefined : last > 0 ? "rose" : "mint"} />
        <Stat label={`Avg (${RANGES.find((r) => r.id === range)?.label})`} value={avg === null ? "---" : pct(avg)} tone={avg === null ? undefined : avg > 0 ? "rose" : "mint"} />
        <Stat label="Cumulative" value={cumLast === null ? "---" : pct(cumLast, 3)} tone={cumLast === null ? undefined : cumLast > 0 ? "rose" : "mint"} />
        {fh.data?.source && <span className="ml-auto text-[12px] font-medium text-text-2 whitespace-nowrap">{fh.data.source}</span>}
      </div>
      {fh.missing ? (
        <EmptyState sub="GET /api/market/{symbol}/funding_history needs bridge ≥ 2.16">Funding history not available on this bridge</EmptyState>
      ) : fh.error ? (
        <EmptyState sub={fh.error}>Funding history unavailable</EmptyState>
      ) : !fh.loaded ? (
        <EmptyState>Loading funding history…</EmptyState>
      ) : data.length === 0 ? (
        <EmptyState>No funding points in this range</EmptyState>
      ) : (
        <div className="relative flex-1 min-h-0">
          <div className="absolute inset-0 p-2">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={CHART_GRID} vertical={false} />
                <XAxis dataKey="label" tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={{ stroke: "rgba(255,255,255,0.18)" }} tickLine={false} minTickGap={32} />
                <YAxis yAxisId="rate" tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={false} tickLine={false} width={64} tickFormatter={(v) => pct(Number(v), 3)} />
                {hasCum && <YAxis yAxisId="cum" orientation="right" tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={false} tickLine={false} width={64} tickFormatter={(v) => pct(Number(v), 2)} />}
                <Tooltip
                  contentStyle={CHART_TOOLTIP_STYLE}
                  labelStyle={CHART_TOOLTIP_LABEL}
                  itemStyle={CHART_TOOLTIP_ITEM}
                  cursor={{ fill: "rgba(255,255,255,0.06)" }}
                  formatter={(v: unknown, name: unknown) => [pct(Number(v), name === "cum" ? 3 : 4), name === "cum" ? "Cumulative" : "Funding rate"]}
                />
                <Bar yAxisId="rate" dataKey="rate" isAnimationActive={false} maxBarSize={18} radius={[2, 2, 0, 0]}>
                  {data.map((r) => <Cell key={r.ts} fill={r.rate > 0 ? COLOR_DOWN : COLOR_UP} fillOpacity={0.75} />)}
                </Bar>
                {hasCum && <Line yAxisId="cum" type="monotone" dataKey="cum" stroke="#FFFFFF" strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls />}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "mint" | "rose" }) {
  return (
    <span className="flex items-baseline gap-1.5 whitespace-nowrap text-[12.5px]">
      <span className="font-medium text-text-2">{label}</span>
      <span className={`num font-semibold ${tone === "mint" ? "text-mint" : tone === "rose" ? "text-rose" : "text-text"}`}>{value}</span>
    </span>
  );
}
