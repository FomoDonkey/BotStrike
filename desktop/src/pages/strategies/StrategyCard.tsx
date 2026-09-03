import { useState } from "react";
import { ChevronDown, ChevronUp, SlidersHorizontal } from "lucide-react";
import type { ConfigField, ConfigScalar, EdgeStats, StrategyInfo, StrategyPortfolio, StrategyResearch } from "@/lib/api";
import { STRATEGY_COLORS, STRATEGY_DESCRIPTIONS, STRATEGY_LABELS } from "@/lib/constants";
import { cn, formatDurationShort, formatMoney, formatPct, formatSignedMoney } from "@/lib/utils";
import { trimNumber } from "@/components/settings/schemaUtils";
import { Panel } from "@/components/ui/Panel";
import { Chip, StatusChip } from "@/components/ui/Chip";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { Sparkline } from "@/components/ui/Sparkline";
import { ListRow, Signed } from "@/components/ui/ListRow";
import { defaultAllocation, recallAllocation, rememberAllocation } from "./allocationMemory";

interface StrategyCardProps {
  s: StrategyInfo;
  /** /api/portfolio.by_strategy row (bridge ≥ 2.16) */
  pf?: StrategyPortfolio;
  edge?: EdgeStats;
  /** Schema field of `trading.allocation_<type>` (bounds for the slider) */
  allocField?: ConfigField;
  busy: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
  onAllocation: (type: string, value: number) => void;
  onEditParams: () => void;
  /** Remote bridge without token → controls disabled */
  canEdit: boolean;
  nowMs: number;
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

function strategyStatus(s: StrategyInfo): string {
  if (s.killed) return "killed";
  if (s.active) return "active";
  if (s.enabled ?? s.allocation > 0) return "enabled";
  return "disabled";
}

/** Vault-style strategy card (spec §3.3). */
export function StrategyCard({ s, pf, edge, allocField, busy, expanded, onToggleExpand, onAllocation, onEditParams, canEdit, nowMs }: StrategyCardProps) {
  const enabled = s.enabled ?? s.allocation > 0;
  const color = STRATEGY_COLORS[s.type] ?? "#FFFFFF";
  const label = STRATEGY_LABELS[s.type] ?? s.name ?? s.type;
  const max = allocField?.max ?? 1;
  const step = allocField?.step ?? 0.05;
  const params = Object.entries(s.params ?? {});
  const curve = pf?.equity_curve?.map((p) => p[1]) ?? [];
  const alltime = pf ? pf.pnl : edge ? edge.net_pnl : null;
  const ageSec = pf?.first_trade_ts ? Math.max(0, nowMs / 1000 - pf.first_trade_ts) : null;

  const toggle = () => {
    if (busy || !canEdit) return;
    if (enabled) {
      rememberAllocation(s.type, s.allocation);
      onAllocation(s.type, 0);
    } else {
      const last = recallAllocation(s.type) ?? defaultAllocation(s.type);
      onAllocation(s.type, Math.min(Math.max(last, allocField?.min ?? 0), max));
    }
  };

  return (
    <Panel className="flex flex-col min-w-0" accent={s.active}>
      <div className="flex items-start gap-3 px-4 pt-4">
        <span className="w-3 h-3 mt-1 rounded-full shrink-0" style={{ backgroundColor: color }} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[15px] font-semibold text-text">{label}</span>
            <StatusChip status={strategyStatus(s)} size="xs" />
            {s.research && <ResearchChip r={s.research} />}
          </div>
          <p className="text-[12.5px] font-medium text-text-2 mt-1 leading-snug">{s.description || STRATEGY_DESCRIPTIONS[s.type] || s.name}</p>
          {s.symbols && s.symbols.length > 0 && <p className="text-[12px] font-medium text-text-2 mt-1">{s.symbols.join(" · ")}</p>}
        </div>
        <Switch checked={enabled} onChange={toggle} busy={busy} disabled={!canEdit} label={enabled ? "Disable strategy" : "Enable strategy"} />
      </div>

      <div className="flex items-end justify-between gap-3 px-4 pt-4">
        <div className="min-w-0">
          <p className="text-[12.5px] font-medium text-text-2">All-time PNL</p>
          <p className="num text-[24px] font-bold leading-tight"><Signed value={alltime} format={formatSignedMoney} /></p>
        </div>
        <Sparkline values={curve} width={140} height={40} />
      </div>

      <div className="grid grid-cols-2 gap-x-6 px-4 pt-3">
        <ListRow label="All-time PNL"><Signed value={alltime} format={formatSignedMoney} /></ListRow>
        <ListRow label="30D return"><Signed value={pf ? pf.return_30d : null} format={(v) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`} /></ListRow>
        <ListRow label="Trades">{pf ? pf.trades : edge ? edge.n : "---"}</ListRow>
        <ListRow label="Win rate">{pf ? formatPct(pf.win_rate, 0) : edge ? formatPct(edge.win_rate, 0) : "---"}</ListRow>
        <ListRow label="PF" hint="Profit factor = gross wins / gross losses">{pf ? num(pf.profit_factor) : edge ? num(edge.profit_factor) : "---"}</ListRow>
        <ListRow label="Sharpe">{pf && typeof pf.sharpe === "number" ? num(pf.sharpe) : "n/a"}</ListRow>
        <ListRow label="Max DD"><span className={cn(pf && pf.max_drawdown > 0 && "text-rose")}>{pf ? formatPct(pf.max_drawdown) : "---"}</span></ListRow>
        <ListRow label="Age" hint="Since the first trade">{ageSec !== null ? formatDurationShort(ageSec) : "---"}</ListRow>
        <ListRow label="Allocation">{formatPct(s.allocation, 0)}</ListRow>
        <ListRow label="Open positions">{pf ? pf.open_positions : "---"}</ListRow>
      </div>

      <div className="px-4 pt-3">
        <div className="flex items-center justify-between text-[12.5px] mb-1">
          <span className="font-medium text-text-2">Allocation</span>
          {allocField?.restart_required && <Chip tone="amber" size="xs">restart</Chip>}
        </div>
        <AllocationSlider key={`${s.type}-${s.allocation}`} value={s.allocation} max={max} step={step} disabled={busy || !canEdit} onCommit={(v) => onAllocation(s.type, v)} />
      </div>

      <div className="flex items-center gap-2 px-4 py-3">
        <Button variant="primary" className="flex-1" onClick={onToggleExpand} icon={expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}>
          {expanded ? "Hide details" : "View details"}
        </Button>
        <Button variant="secondary" onClick={onEditParams} icon={<SlidersHorizontal className="w-3.5 h-3.5" />}>Parameters</Button>
      </div>

      {expanded && (
        <div className="border-t border-hairline px-4 py-3 space-y-3">
          {s.research && <ResearchDetails r={s.research} />}
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 mb-1.5">Edge monitor</p>
            {edge ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-6">
                <ListRow label="n">{edge.n}</ListRow>
                <ListRow label="Win rate">{formatPct(edge.win_rate, 0)}</ListRow>
                <ListRow label="t-stat"><span className={cn(edge.t_stat <= -2 && "text-rose", edge.t_stat >= 2 && "text-mint")}>{num(edge.t_stat)}</span></ListRow>
                <ListRow label="PF"><span className={cn(edge.profit_factor >= 1 ? "text-mint" : "text-rose")}>{num(edge.profit_factor)}</span></ListRow>
                <ListRow label="Fee share"><span className={cn(edge.fee_share >= 0.5 && "text-amber")}>{formatPct(edge.fee_share, 0)}</span></ListRow>
                <ListRow label="Net PnL"><Signed value={edge.net_pnl} format={formatSignedMoney} /></ListRow>
                <ListRow label="Mean gross">{num(edge.mean_gross_bps, 1)} ± {num(edge.se_bps, 1)} bps</ListRow>
                <ListRow label="Expectancy">{formatMoney(edge.expectancy_usd)}</ListRow>
                <ListRow label="Avg hold">{num(edge.avg_hold_min, 0)} min</ListRow>
                <ListRow label="Verdict"><StatusChip status={edge.verdict === "ok" ? "ok" : edge.verdict === "kill" ? "killed" : edge.verdict === "warn" ? "warning" : "disabled"} label={edge.verdict} size="xs" title={edge.reason} /></ListRow>
              </div>
            ) : (
              <p className="text-[12.5px] font-medium text-text">No edge data yet — the monitor needs closed trades.</p>
            )}
            {s.killed && s.kill_reason && <p className="text-[12.5px] font-medium text-rose mt-1 break-words">Killed: {s.kill_reason}</p>}
          </div>
          {params.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 mb-1.5">Parameters</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
                {params.map(([k, v]) => (
                  <div key={k} className="rounded-[6px] bg-panel-2 px-2 py-1 min-w-0">
                    <p className="text-[11px] font-medium text-text-2 truncate" title={k}>{k.replace(/_/g, " ")}</p>
                    <p className="num text-[12.5px] font-semibold text-text truncate" title={paramText(v)}>{paramText(v)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

function researchTone(verdict: string): "mint" | "rose" | "amber" | "neutral" {
  const v = verdict.toUpperCase();
  if (v === "GO" || v === "PASS") return "mint";
  if (v.includes("NO") || v === "FAIL" || v === "KILL") return "amber";
  if (v) return "amber";
  return "neutral";
}

function researchChecks(r: StrategyResearch): string {
  if (Array.isArray(r.checks)) return `${r.checks.length}`;
  if (typeof r.checks === "string" && r.checks) return r.checks;
  return "";
}

/** `RESEARCH GO 11/11` (mint) / `NO-GO 2/7` (amber) with the checklist as hover card. */
function ResearchChip({ r }: { r: StrategyResearch }) {
  const checks = researchChecks(r);
  const title = [r.summary, r.note, Array.isArray(r.checks) ? r.checks.map(String).join("\n") : ""].filter(Boolean).join("\n") || "Offline research verdict";
  return <Chip tone={researchTone(r.verdict ?? "")} size="xs" title={title}>Research {r.verdict || "n/a"}{checks ? ` ${checks}` : ""}</Chip>;
}

function ResearchDetails({ r }: { r: StrategyResearch }) {
  const parts: string[] = [];
  if (typeof r.trades === "number") parts.push(`${r.trades} trades`);
  if (typeof r.profit_factor === "number") parts.push(`PF ${r.profit_factor.toFixed(2)}`);
  if (typeof r.t_stat === "number") parts.push(`t ${r.t_stat.toFixed(2)}`);
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 mb-1.5">Research · {r.verdict || "n/a"}</p>
      {(parts.length > 0 || r.summary || r.note) && (
        <p className="text-[12.5px] font-medium text-text leading-snug break-words">{parts.join(" · ")}{parts.length && (r.summary || r.note) ? " · " : ""}{r.summary || r.note}</p>
      )}
      {Array.isArray(r.checks) && r.checks.length > 0 && (
        <ul className="mt-1 grid grid-cols-1 sm:grid-cols-2 gap-x-4">
          {r.checks.map((c, i) => <li key={i} className="text-[12px] font-medium text-text-2 leading-snug">• {String(c)}</li>)}
        </ul>
      )}
    </div>
  );
}

const COMMIT_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);

function AllocationSlider({ value, max, step, disabled, onCommit }: { value: number; max: number; step: number; disabled: boolean; onCommit: (v: number) => void }) {
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
        aria-label="Allocation"
      />
      <span className="num text-[13px] font-semibold w-12 text-right text-text">{pct}%</span>
    </div>
  );
}
