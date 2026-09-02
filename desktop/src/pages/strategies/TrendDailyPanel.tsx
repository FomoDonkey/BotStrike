import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { api, ApiError, type TrendResponse } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { useNow } from "@/hooks/useNow";
import { cn, formatLocalDateTime, formatPct, formatPrice, formatRelative, formatUSD } from "@/lib/utils";
import { trimNumber } from "@/components/settings/schemaUtils";

const MODEL_COLOR = "#38BDF8";
const PAPER_COLOR = "#00D4AA";

function StatusChip({ status }: { status: string }) {
  const ok = /^(ok|success|done)$/i.test(status);
  const err = /(error|fail)/i.test(status);
  return (
    <span className={cn(
      "px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider",
      ok ? "bg-profit/10 text-profit" : err ? "bg-loss/10 text-loss" : "bg-white/5 text-text-muted",
    )}>
      {status || "never"}
    </span>
  );
}

/** Full status of the daily trend engine: schedule, universe, targets, positions and tracking. */
export function TrendDailyPanel() {
  const [trend, setTrend] = useState<TrendResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const now = useNow();

  usePolling(
    () => api.trend()
      .then((t) => { setTrend(t); setError(null); })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e))),
    30_000,
  );

  // Cumulative model vs paper return, compounded day by day.
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
      <GlassPanel className="p-5">
        {error
          ? <p className="text-xs font-mono text-loss break-all">Trend daily status unavailable: {error}</p>
          : <p className="text-xs text-text-muted">Loading trend daily status…</p>}
      </GlassPanel>
    );
  }

  const nextMs = trend.next_run_utc ? Date.parse(trend.next_run_utc) : Number.NaN;
  const targets = Object.entries(trend.targets ?? {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const barScale = Math.max(1, ...targets.map(([, w]) => Math.abs(w)));
  const tracking = trend.tracking;

  return (
    <GlassPanel className="p-4 sm:p-5 space-y-4 min-w-0">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <h3 className="text-sm font-semibold text-text-primary">Trend daily · Donchian ensemble</h3>
        <span className={cn("px-2 py-0.5 rounded text-[10px] font-bold uppercase", trend.enabled ? "bg-profit/10 text-profit" : "bg-white/5 text-text-muted")}>
          {trend.enabled ? "enabled" : "disabled"}
        </span>
        <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-warning/10 text-warning">{trend.mode}</span>
        <span className="text-[11px] font-mono text-text-muted ml-auto">
          alloc {formatPct(trend.allocation, 0)} · exposure {formatPct(trend.exposure ?? 0, 0)} · basis {formatUSD(trend.equity_basis ?? 0)}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Schedule + universe + params */}
        <div className="space-y-4 min-w-0">
          <div className="space-y-1.5 text-xs">
            <p className="text-[10px] uppercase tracking-wider text-text-muted">Schedule</p>
            <div className="flex justify-between gap-2">
              <span className="text-text-secondary">Next run</span>
              <span className="font-mono text-text-primary text-right">
                {formatLocalDateTime(trend.next_run_utc)}
                {Number.isFinite(nextMs) && <span className="text-text-muted"> · {formatRelative(nextMs, now)}</span>}
              </span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-text-secondary">Last run</span>
              <span className="font-mono text-text-primary text-right flex items-center gap-2 justify-end">
                {formatLocalDateTime(trend.last_run_utc)} <StatusChip status={trend.last_run_status} />
              </span>
            </div>
            {trend.last_error && (
              <p className="text-[11px] font-mono text-loss break-words">{trend.last_error}</p>
            )}
          </div>

          <div>
            <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1.5">
              Universe <span className="text-text-muted/70">({trend.universe?.length ?? 0} of {trend.candidates} candidates)</span>
            </p>
            <div className="flex flex-wrap gap-1.5">
              {(trend.universe ?? []).map((sym) => {
                const w = trend.targets?.[sym] ?? 0;
                return (
                  <span
                    key={sym}
                    className={cn(
                      "px-2 py-0.5 rounded-md text-[10px] font-mono border",
                      w > 0 ? "border-accent/40 text-accent bg-accent/5" : "border-white/10 text-text-muted",
                    )}
                  >
                    {sym}
                  </span>
                );
              })}
              {(trend.universe?.length ?? 0) === 0 && <span className="text-[11px] text-text-muted">empty</span>}
            </div>
          </div>

          <div>
            <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1.5">Params</p>
            <div className="grid grid-cols-2 gap-1">
              {Object.entries(trend.params ?? {}).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-2 text-[11px] rounded bg-white/[0.02] px-2 py-0.5 min-w-0">
                  <span className="text-text-muted truncate" title={k}>{k.replace(/_/g, " ")}</span>
                  <span className="font-mono text-text-secondary truncate">
                    {typeof v === "number" ? trimNumber(v) : Array.isArray(v) ? v.join(",") : String(v ?? "---")}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Targets */}
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1.5">Target weights</p>
          {targets.length === 0 ? (
            <p className="text-[11px] text-text-muted">No targets (flat)</p>
          ) : (
            <div className="space-y-1.5">
              {targets.map(([sym, w]) => (
                <div key={sym} className="text-[11px]">
                  <div className="flex justify-between mb-0.5">
                    <span className="font-mono text-text-secondary">{sym}</span>
                    <span className={cn("font-mono", w > 0 ? "text-accent" : w < 0 ? "text-loss" : "text-text-muted")}>{formatPct(w, 1)}</span>
                  </div>
                  <div className="w-full h-1.5 rounded-full bg-white/5 overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${Math.min(100, (Math.abs(w) / barScale) * 100)}%`, backgroundColor: w >= 0 ? MODEL_COLOR : "#F43F5E" }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Positions */}
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1.5">Positions ({trend.positions?.length ?? 0})</p>
          {(trend.positions?.length ?? 0) === 0 ? (
            <p className="text-[11px] text-text-muted">No open positions</p>
          ) : (
            <div className="overflow-x-auto -mx-1 px-1">
              <table className="w-full min-w-[360px] text-[11px]">
                <thead>
                  <tr className="text-text-muted border-b border-white/5">
                    <th className="text-left py-1 font-normal">Symbol</th>
                    <th className="text-right font-normal">Size</th>
                    <th className="text-right font-normal">Entry</th>
                    <th className="text-right font-normal">Mark</th>
                    <th className="text-right font-normal">uPnL</th>
                    <th className="text-right font-normal">Weight</th>
                    <th className="text-right font-normal">Opened</th>
                  </tr>
                </thead>
                <tbody>
                  {trend.positions.map((p) => (
                    <tr key={p.symbol} className="border-b border-white/[0.02]">
                      <td className="py-1 font-mono text-text-primary">{p.symbol}</td>
                      <td className="text-right font-mono">{trimNumber(p.size)}</td>
                      <td className="text-right font-mono">{formatPrice(p.entry_price)}</td>
                      <td className="text-right font-mono">{formatPrice(p.mark_price)}</td>
                      <td className={cn("text-right font-mono", p.unrealized_pnl >= 0 ? "text-profit" : "text-loss")}>{formatUSD(p.unrealized_pnl)}</td>
                      <td className="text-right font-mono">{formatPct(p.weight, 1)}</td>
                      <td className="text-right font-mono text-text-muted">{p.opened}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Tracking: model vs paper */}
      <div className="pt-3 border-t border-white/5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-2 text-[11px] font-mono">
          <span className="text-[10px] uppercase tracking-wider text-text-muted font-sans">Tracking</span>
          <span className="text-text-muted">{tracking?.days ?? 0} days</span>
          <span>model <span style={{ color: MODEL_COLOR }}>{formatPct(tracking?.model_return ?? 0)}</span></span>
          <span>paper <span style={{ color: PAPER_COLOR }}>{formatPct(tracking?.paper_return ?? 0)}</span></span>
          <span>TE (ann.) <span className={cn((tracking?.tracking_error_ann ?? 0) > 0.05 ? "text-warning" : "text-text-secondary")}>{formatPct(tracking?.tracking_error_ann ?? 0)}</span></span>
        </div>
        {chartData.length >= 2 ? (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey="date" tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 10, fontFamily: "JetBrains Mono" }} axisLine={false} tickLine={false} minTickGap={40} />
              <YAxis tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 10, fontFamily: "JetBrains Mono" }} axisLine={false} tickLine={false} width={48} tickFormatter={(v) => `${Number(v).toFixed(1)}%`} />
              <Tooltip
                contentStyle={{ background: "#171717", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 6, fontSize: 12, fontFamily: "JetBrains Mono" }}
                labelStyle={{ color: "rgba(255,255,255,0.6)" }}
                formatter={(v: unknown, name: unknown) => [`${Number(v).toFixed(2)}%`, String(name)]}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line type="monotone" dataKey="model" name="Model" stroke={MODEL_COLOR} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="paper" name="Paper" stroke={PAPER_COLOR} strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-[11px] text-text-muted">Not enough daily records yet — the chart needs 2+ days.</p>
        )}
      </div>
    </GlassPanel>
  );
}
