import { useMemo } from "react";
import { COLOR_DOWN, COLOR_UP } from "@/lib/constants";
import { cn } from "@/lib/utils";

interface SparklineProps {
  values: readonly number[];
  width?: number;
  height?: number;
  className?: string;
  /** Force a colour; default = sign of the last value vs the first */
  color?: string;
  fill?: boolean;
  strokeWidth?: number;
}

/** Inline SVG sparkline (mint when the series ends above where it started, rose otherwise). */
export function Sparkline({ values, width = 120, height = 36, className, color, fill = true, strokeWidth = 1.5 }: SparklineProps) {
  const { path, area, stroke } = useMemo(() => {
    const pts = values.filter((v) => Number.isFinite(v));
    if (pts.length < 2) return { path: "", area: "", stroke: color ?? COLOR_UP };
    const min = Math.min(...pts);
    const max = Math.max(...pts);
    const span = max - min || 1;
    const stepX = width / (pts.length - 1);
    const coords: string[] = [];
    for (let i = 0; i < pts.length; i++) {
      const x = i * stepX;
      const y = height - 2 - ((pts[i] - min) / span) * (height - 4);
      coords.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    const p = `M${coords.join(" L")}`;
    const a = `${p} L${width},${height} L0,${height} Z`;
    const s = color ?? (pts[pts.length - 1] >= pts[0] ? COLOR_UP : COLOR_DOWN);
    return { path: p, area: a, stroke: s };
  }, [values, width, height, color]);

  if (!path) {
    return <svg width={width} height={height} className={cn("shrink-0", className)} aria-hidden><line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="rgba(255,255,255,0.25)" strokeDasharray="2 3" /></svg>;
  }
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={cn("shrink-0", className)} aria-hidden>
      {fill && <path d={area} fill={stroke} fillOpacity={0.14} />}
      <path d={path} fill="none" stroke={stroke} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
