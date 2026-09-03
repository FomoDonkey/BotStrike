import { useMemo } from "react";
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useMarketStore } from "@/stores/marketStore";
import { CHART_TEXT, CHART_TOOLTIP_ITEM, CHART_TOOLTIP_LABEL, CHART_TOOLTIP_STYLE, COLOR_DOWN, COLOR_UP, SYMBOL_LABELS } from "@/lib/constants";
import { formatPrice, formatSize } from "@/lib/utils";
import { EmptyState } from "@/components/ui/Panel";

interface Point {
  price: number;
  bid?: number;
  ask?: number;
}

/** Cumulative depth of the top-of-book levels the bridge broadcasts (10 per side), mint / rose areas, mid label. */
export function DepthChart({ symbol }: { symbol: string }) {
  const ob = useMarketStore((s) => s.orderbooks[symbol]);
  const base = SYMBOL_LABELS[symbol] ?? "";

  const data = useMemo<Point[]>(() => {
    if (!ob) return [];
    const bids: Point[] = [];
    let cum = 0;
    for (const l of ob.bids ?? []) { cum += l.quantity || 0; bids.push({ price: l.price, bid: cum }); }
    const asks: Point[] = [];
    cum = 0;
    for (const l of ob.asks ?? []) { cum += l.quantity || 0; asks.push({ price: l.price, ask: cum }); }
    return [...bids.reverse(), ...asks];
  }, [ob]);

  if (!data.length) return <EmptyState>Waiting for order book…</EmptyState>;
  const mid = ob?.mid_price ?? null;

  return (
    <div className="relative flex-1 min-h-0">
      <div className="absolute inset-0 p-2">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 16, right: 8, bottom: 0, left: 0 }}>
            <XAxis dataKey="price" type="number" domain={["dataMin", "dataMax"]} tick={{ fill: CHART_TEXT, fontSize: 11 }} tickFormatter={(v) => formatPrice(Number(v))} axisLine={{ stroke: "rgba(255,255,255,0.18)" }} tickLine={false} minTickGap={48} />
            <YAxis orientation="right" width={64} tick={{ fill: CHART_TEXT, fontSize: 11 }} tickFormatter={(v) => formatSize(Number(v))} axisLine={false} tickLine={false} />
            <Tooltip
              contentStyle={CHART_TOOLTIP_STYLE}
              labelStyle={CHART_TOOLTIP_LABEL}
              itemStyle={CHART_TOOLTIP_ITEM}
              labelFormatter={(v) => `Price ${formatPrice(Number(v))}`}
              formatter={(v: unknown, name: unknown) => [`${formatSize(Number(v))} ${base}`, name === "bid" ? "Bid depth" : "Ask depth"]}
            />
            {mid !== null && <ReferenceLine x={mid} stroke="rgba(255,255,255,0.6)" strokeDasharray="3 3" label={{ value: `Mid ${formatPrice(mid)}`, position: "top", fill: "#FFFFFF", fontSize: 11, fontWeight: 600 }} />}
            <Area type="stepAfter" dataKey="bid" stroke={COLOR_UP} fill={COLOR_UP} fillOpacity={0.2} strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} />
            <Area type="stepBefore" dataKey="ask" stroke={COLOR_DOWN} fill={COLOR_DOWN} fillOpacity={0.2} strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
