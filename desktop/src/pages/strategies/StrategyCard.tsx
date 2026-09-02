import { useState } from "react";
import { ChevronDown, ChevronUp, Loader2, SlidersHorizontal } from "lucide-react";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { VerdictChip } from "@/components/shared/VerdictChip";
import type { ConfigField, ConfigScalar, StrategyInfo } from "@/lib/api";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { cn, formatPct, formatUSD } from "@/lib/utils";
import { trimNumber } from "@/components/settings/schemaUtils";
import { defaultAllocation, recallAllocation, rememberAllocation } from "./allocationMemory";

interface StrategyCardProps {
  s: StrategyInfo;
  /** Schema field of `trading.allocation_<type>` (bounds for the slider), when the bridge exposes it */
  allocField?: ConfigField;
  busy: boolean;
  expandable?: boolean;
  expanded?: boolean;
  onToggleExpand?: () => void;
  onAllocation: (type: string, value: number) => void;
  onEditParams: () => void;
}

function num(v: unknown, digits = 2): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "---";
}

function paramText(v: ConfigScalar | undefined): string {
  if (v === null || v === undefined) return "---";
  if (typeof v === "boolean") return v ? "ON" : "OFF";
  if (typeof v === "number") return trimNumber(v);
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

export function StrategyCard({ s, allocField, busy, expandable, expanded, onToggleExpand, onAllocation, onEditParams }: StrategyCardProps) {
  const enabled = s.enabled ?? s.allocation > 0;
  const color = STRATEGY_COLORS[s.type] ?? "#4A5568";
  const label = STRATEGY_LABELS[s.type] ?? s.name ?? s.type;
  const max = allocField?.max ?? 1;
  const step = allocField?.step ?? 0.05;
  const params = Object.entries(s.params ?? {}).slice(0, 6);
  const edge = s.edge;

  const toggle = () => {
    if (busy) return;
    if (enabled) {
      rememberAllocation(s.type, s.allocation);
      onAllocation(s.type, 0);
    } else {
      const last = recallAllocation(s.type) ?? defaultAllocation(s.type);
      onAllocation(s.type, Math.min(Math.max(last, allocField?.min ?? 0), max));
    }
  };

  return (
    <GlassPanel className="p-4 sm:p-5 flex flex-col gap-4 min-w-0" glow={s.active}>
      {/* Header: dot + name + status + switch */}
      <div className="flex items-start gap-3">
        <div className="w-3 h-3 mt-1 rounded-full shrink-0" style={{ backgroundColor: color }} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-text-primary">{label}</span>
            <span className={cn(
              "px-2 py-0.5 rounded text-[10px] font-semibold uppercase",
              s.killed ? "bg-loss/10 text-loss" :
              s.active ? "bg-profit/10 text-profit" :
              enabled ? "bg-warning/10 text-warning" : "bg-white/5 text-text-muted"
            )}>
              {s.killed ? "KILLED" : s.active ? "ACTIVE" : enabled ? "ENABLED" : "DISABLED"}
            </span>
            {s.symbols && s.symbols.length > 0 && (
              <span className="text-[10px] font-mono text-text-muted truncate">{s.symbols.join(" · ")}</span>
            )}
          </div>
          <p className="text-xs text-text-secondary mt-1 leading-snug">{s.description || s.name}</p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label={enabled ? "Disable strategy" : "Enable strategy"}
          disabled={busy}
          onClick={toggle}
          className={cn("w-10 h-5 rounded-full transition-all relative shrink-0 disabled:opacity-50", enabled ? "bg-accent" : "bg-white/10")}
        >
          {busy ? (
            <Loader2 className="w-3 h-3 animate-spin absolute top-1 left-3.5 text-bg-base" />
          ) : (
            <span className={cn("absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all", enabled ? "left-[22px]" : "left-0.5")} />
          )}
        </button>
      </div>

      {/* Allocation slider — PUT on release */}
      <div>
        <div className="flex items-center justify-between text-xs mb-1">
          <span className="text-text-muted">Allocation</span>
          {allocField?.restart_required && (
            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-warning/10 text-warning">restart</span>
          )}
        </div>
        <AllocationSlider
          key={`${s.type}-${s.allocation}`}
          value={s.allocation}
          max={max}
          step={step}
          color={color}
          disabled={busy}
          onCommit={(v) => onAllocation(s.type, v)}
        />
      </div>

      {/* Params */}
      {params.length > 0 && (
        <div>
          <div className="flex items-center justify-between text-xs mb-1.5">
            <span className="text-text-muted">Parameters</span>
            <button
              type="button"
              onClick={onEditParams}
              className="flex items-center gap-1 text-[11px] text-accent hover:underline"
            >
              <SlidersHorizontal className="w-3 h-3" /> Edit in Settings
            </button>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
            {params.map(([k, v]) => (
              <div key={k} className="rounded-lg bg-white/[0.02] px-2 py-1 min-w-0">
                <p className="text-[9px] uppercase tracking-wider text-text-muted truncate" title={k}>{k.replace(/_/g, " ")}</p>
                <p className="font-mono text-xs text-text-secondary truncate" title={paramText(v)}>{paramText(v)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Edge */}
      <div>
        <div className="flex items-center justify-between text-xs mb-1.5">
          <span className="text-text-muted">Edge</span>
          <VerdictChip verdict={edge?.verdict} title={edge?.reason} />
        </div>
        {edge ? (
          <>
            <div className="grid grid-cols-3 sm:grid-cols-5 gap-1.5 text-[11px]">
              <Stat label="n" value={String(edge.n ?? 0)} />
              <Stat label="Win rate" value={typeof edge.win_rate === "number" ? formatPct(edge.win_rate, 0) : "---"} />
              <Stat
                label="t-stat"
                value={num(edge.t_stat)}
                tone={typeof edge.t_stat === "number" ? (edge.t_stat <= -2 ? "loss" : edge.t_stat >= 2 ? "profit" : undefined) : undefined}
              />
              <Stat label="PF" value={num(edge.profit_factor)} tone={typeof edge.profit_factor === "number" ? (edge.profit_factor >= 1 ? "profit" : "loss") : undefined} />
              <Stat label="Fee share" value={typeof edge.fee_share === "number" ? formatPct(edge.fee_share, 0) : "---"} tone={typeof edge.fee_share === "number" && edge.fee_share >= 0.5 ? "warning" : undefined} />
            </div>
            <p className="text-[10px] text-text-muted mt-1 font-mono">
              net {formatUSD(edge.net_pnl ?? 0)} · {num(edge.mean_gross_bps, 1)} ± {num(edge.se_bps, 1)} bps · hold {num(edge.avg_hold_min, 0)} min
              {edge.reason ? ` · ${edge.reason}` : ""}
            </p>
          </>
        ) : (
          <p className="text-[11px] text-text-muted">No edge data yet</p>
        )}
        {s.killed && s.kill_reason && (
          <p className="text-[11px] text-loss mt-1 font-mono break-words">Killed: {s.kill_reason}</p>
        )}
      </div>

      {expandable && (
        <button
          type="button"
          onClick={onToggleExpand}
          className="flex items-center justify-center gap-1 -mb-1 pt-2 border-t border-white/5 text-[11px] text-accent hover:text-accent/80"
        >
          {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          {expanded ? "Hide details" : "Show details (targets, positions, tracking)"}
        </button>
      )}
    </GlassPanel>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "profit" | "loss" | "warning" }) {
  return (
    <div className="rounded-lg bg-white/[0.02] px-2 py-1">
      <p className="text-[9px] uppercase tracking-wider text-text-muted">{label}</p>
      <p className={cn("font-mono text-xs", tone === "profit" && "text-profit", tone === "loss" && "text-loss", tone === "warning" && "text-warning", !tone && "text-text-secondary")}>
        {value}
      </p>
    </div>
  );
}

const COMMIT_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);

function AllocationSlider({ value, max, step, color, disabled, onCommit }: {
  value: number; max: number; step: number; color: string; disabled: boolean; onCommit: (v: number) => void;
}) {
  const [pct, setPct] = useState(Math.round(value * 100));
  const commit = () => {
    const next = pct / 100;
    if (Math.abs(next - value) > 1e-9) onCommit(next);
  };
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        className="bs-range flex-1 min-w-0"
        min={0}
        max={Math.max(1, Math.round(max * 100))}
        step={Math.max(1, Math.round(step * 100))}
        value={pct}
        disabled={disabled}
        onChange={(e) => setPct(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={(e) => { if (COMMIT_KEYS.has(e.key)) commit(); }}
        onBlur={commit}
        style={{ accentColor: color }}
        aria-label="Allocation"
      />
      <span className="font-mono text-sm w-12 text-right tabular-nums" style={{ color }}>{pct}%</span>
    </div>
  );
}
