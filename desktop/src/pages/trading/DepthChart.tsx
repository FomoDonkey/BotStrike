import { useMemo } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useMarketStore } from "@/stores/marketStore";
import { COLOR_DOWN, COLOR_UP, SYMBOL_LABELS } from "@/lib/constants";
import { formatPrice, formatSize } from "@/lib/utils";

interface Point {
  price: number;
  bid?: number;
  ask?: number;
}

/** Cumulative depth of the top-of-book levels the bridge broadcasts (10 per side). */
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

  if (!data.length) {
    return <div className="flex-1 flex items-center justify-center text-text-faint text-xs">Waiting for order book…</div>;
  }

  // absolute box inside a flex child: recharts measures a real size on the first paint
  return (
    <div className="relative flex-1 min-h-0">
      <div className="absolute inset-0 p-2">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="price" type="number" domain={["dataMin", "dataMax"]} tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 10, fontFamily: "JetBrains Mono" }} tickFormatter={(v) => formatPrice(Number(v))} axisLine={{ stroke: "rgba(255,255,255,0.12)" }} tickLine={false} minTickGap={48} />
          <YAxis orientation="right" width={64} tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 10, fontFamily: "JetBrains Mono" }} tickFormatter={(v) => formatSize(Number(v))} axisLine={false} tickLine={false} />
          <Tooltip
            contentStyle={{ background: "#171717", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 6, fontSize: 11, fontFamily: "JetBrains Mono" }}
            labelStyle={{ color: "rgba(255,255,255,0.6)" }}
            labelFormatter={(v) => `Price ${formatPrice(Number(v))}`}
            formatter={(v: unknown, name: unknown) => [`${formatSize(Number(v))} ${base}`, name === "bid" ? "Bid depth" : "Ask depth"]}
          />
          <Area type="stepAfter" dataKey="bid" stroke={COLOR_UP} fill={COLOR_UP} fillOpacity={0.15} strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} />
          <Area type="stepBefore" dataKey="ask" stroke={COLOR_DOWN} fill={COLOR_DOWN} fillOpacity={0.15} strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} />
        </AreaChart>
      </ResponsiveContainer>
      </div>
    </div>
  );
}
