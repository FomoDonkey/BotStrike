import { motion } from "framer-motion";
import { useShallow } from "zustand/shallow";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { MetricCard } from "@/components/shared/MetricCard";
import { useRiskStore, killReason } from "@/stores/riskStore";
import { useTradingStore } from "@/stores/tradingStore";
import { usePolling } from "@/hooks/usePolling";
import { refreshRiskIntoStore } from "@/hooks/useVisibilityRefresh";
import { formatUSD, formatPct, cn } from "@/lib/utils";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { Shield, AlertTriangle, CircleOff, Gauge, Zap, TrendingDown, Layers, Skull } from "lucide-react";

const RISK_POLL_MS = 5_000;

interface LadderRow {
  label: string;
  /** How much of the budget is consumed, 0–1 (null → the bridge did not send this limit) */
  used: number | null;
  current: string;
  limit: string;
  note?: string;
}

function usedColor(ratio: number): string {
  if (ratio >= 0.7) return "linear-gradient(90deg, #FFA502, #F43F5E)";
  if (ratio >= 0.3) return "linear-gradient(90deg, #00D4AA, #FFA502)";
  return "linear-gradient(90deg, #00D4AA, #00D4AAaa)";
}

export function RiskPage() {
  const risk = useRiskStore(useShallow((s) => ({
    equity: s.equity,
    drawdown_pct: s.drawdown_pct,
    max_drawdown_pct: s.max_drawdown_pct,
    circuit_breaker_active: s.circuit_breaker_active,
    regime: s.regime,
    peak_equity: s.peak_equity,
    daily_pnl: s.daily_pnl,
    daily_limit: s.daily_limit,
    max_daily_loss_pct: s.max_daily_loss_pct,
    weekly_pnl: s.weekly_pnl,
    weekly_limit: s.weekly_limit,
    max_weekly_loss_pct: s.max_weekly_loss_pct,
    drawdown_halted: s.drawdown_halted,
    killed_strategies: s.killed_strategies,
    compounding_enabled: s.compounding_enabled,
    equity_basis: s.equity_basis,
    restLoadedAt: s.restLoadedAt,
  })));
  const metrics = useTradingStore((s) => s.metrics);

  // /api/risk every 5 s; the WS risk_update fills the same fields in between.
  usePolling(refreshRiskIntoStore, RISK_POLL_MS);

  const ddPct = risk.drawdown_pct * 100;
  const maxDdPct = risk.max_drawdown_pct * 100;
  const hasRest = risk.restLoadedAt > 0;
  const halted = risk.circuit_breaker_active || risk.drawdown_halted;

  const ladder: LadderRow[] = [
    {
      label: "Daily loss",
      used: risk.daily_limit > 0 ? Math.min(1, Math.max(0, -risk.daily_pnl) / risk.daily_limit) : null,
      current: `${risk.daily_pnl >= 0 ? "+" : ""}${formatUSD(risk.daily_pnl)}`,
      limit: risk.daily_limit > 0 ? `-${formatUSD(risk.daily_limit)} (${formatPct(risk.max_daily_loss_pct, 1)})` : "n/a",
      note: "resets at 00:00 UTC",
    },
    {
      label: "Weekly loss",
      used: risk.weekly_limit > 0 ? Math.min(1, Math.max(0, -risk.weekly_pnl) / risk.weekly_limit) : null,
      current: `${risk.weekly_pnl >= 0 ? "+" : ""}${formatUSD(risk.weekly_pnl)}`,
      limit: risk.weekly_limit > 0 ? `-${formatUSD(risk.weekly_limit)} (${formatPct(risk.max_weekly_loss_pct, 1)})` : "n/a",
      note: "ISO week · resets Monday 00:00 UTC",
    },
    {
      label: "Drawdown from peak",
      used: risk.max_drawdown_pct > 0 ? Math.min(1, risk.drawdown_pct / risk.max_drawdown_pct) : null,
      current: formatPct(risk.drawdown_pct),
      limit: `${formatPct(risk.max_drawdown_pct, 1)} → circuit breaker`,
      note: risk.peak_equity > 0 ? `peak ${formatUSD(risk.peak_equity)}` : undefined,
    },
  ];

  const killed = Object.entries(risk.killed_strategies ?? {});

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
          <Shield className="w-5 h-5 text-accent" /> Risk Monitor
        </h1>
        {risk.compounding_enabled !== null && (
          <span
            title={risk.compounding_enabled
              ? `Sizing on all-time equity (basis ${formatUSD(risk.equity_basis)})`
              : "Sizing on the fixed initial capital"}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider",
              risk.compounding_enabled ? "bg-accent/10 text-accent" : "bg-white/5 text-text-muted",
            )}
          >
            <Layers className="w-3 h-3" /> Compounding {risk.compounding_enabled ? "ON" : "OFF"}
          </span>
        )}
      </div>

      {/* Circuit Breaker */}
      <GlassPanel className="p-4 sm:p-6" glow={halted}>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className={cn(
              "w-14 h-14 sm:w-16 sm:h-16 rounded-2xl flex items-center justify-center shrink-0",
              halted ? "bg-loss/10" : "bg-profit/10"
            )}>
              {halted ? (
                <CircleOff className="w-8 h-8 text-loss" />
              ) : (
                <Shield className="w-8 h-8 text-profit" />
              )}
            </div>
            <div>
              <p className="text-sm text-text-secondary">Circuit Breaker</p>
              <p className={cn("text-2xl font-bold", halted ? "text-loss" : "text-profit")}>
                {risk.circuit_breaker_active ? "TRIPPED" : risk.drawdown_halted ? "HALTED (DRAWDOWN)" : "NORMAL"}
              </p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4 sm:text-right">
            <div>
              <p className="text-xs text-text-muted">Peak equity</p>
              <p className="text-lg font-mono font-semibold text-text-primary">
                {risk.peak_equity > 0 ? formatUSD(risk.peak_equity) : "---"}
              </p>
            </div>
            <div>
              <p className="text-xs text-text-muted">Max drawdown allowed</p>
              <p className="text-lg font-mono font-semibold text-text-primary">{maxDdPct.toFixed(1)}%</p>
            </div>
          </div>
        </div>
      </GlassPanel>

      {/* Drawdown ladder */}
      <GlassPanel className="p-4 sm:p-5">
        <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
          <span className="text-xs text-text-secondary uppercase tracking-wider flex items-center gap-2">
            <TrendingDown className="w-3 h-3" /> Loss limits
          </span>
          {!hasRest && (
            <span className="text-[10px] text-text-muted">daily / weekly limits need bridge ≥ 2.14 (/api/risk)</span>
          )}
        </div>
        <div className="space-y-4">
          {ladder.map((row) => (
            <div key={row.label}>
              <div className="flex items-baseline justify-between gap-2 text-xs mb-1 flex-wrap">
                <span className="text-text-secondary">
                  {row.label}
                  {row.note && <span className="text-text-muted"> · {row.note}</span>}
                </span>
                <span className="font-mono">
                  <span className={cn(
                    "font-semibold",
                    row.used === null ? "text-text-muted" : row.used >= 0.7 ? "text-loss" : row.used >= 0.3 ? "text-warning" : "text-profit",
                  )}>
                    {row.current}
                  </span>
                  <span className="text-text-muted"> / {row.limit}</span>
                </span>
              </div>
              <div className="w-full h-3 rounded-full bg-white/5 overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{ width: `${(row.used ?? 0) * 100}%`, background: usedColor(row.used ?? 0) }}
                />
              </div>
              <div className="flex justify-between mt-0.5 text-[10px] text-text-muted font-mono">
                <span>{row.used === null ? "no limit reported" : `${(row.used * 100).toFixed(0)}% used`}</span>
                <span>100%</span>
              </div>
            </div>
          ))}
        </div>
      </GlassPanel>

      {/* Risk Metrics Grid */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {/* metrics.equity = merged all-time view (same as TopBar/Dashboard);
            risk.equity is the risk engine's SESSION equity and resets on restart */}
        <MetricCard label="Equity" value={metrics.equity} format={formatUSD} icon={<Gauge className="w-3 h-3" />} />
        <MetricCard
          label="Session DD"
          value={risk.drawdown_pct}
          format={formatPct}
          subtext={`all-time max ${formatPct(metrics.max_drawdown)}`}
          icon={<TrendingDown className="w-3 h-3" />}
        />
        <MetricCard label="Regime" value={0} format={() => risk.regime} icon={<Zap className="w-3 h-3" />} />
        <MetricCard label="Total Trades" value={metrics.total_trades} format={(v) => v.toFixed(0)} icon={<AlertTriangle className="w-3 h-3" />} />
      </div>

      {/* Killed strategies */}
      <GlassPanel className="p-4 sm:p-5">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
          <Skull className="w-3 h-3" /> Killed by edge monitor
          <span className={cn("ml-auto px-2 py-0.5 rounded text-[10px] font-bold", killed.length ? "bg-loss/10 text-loss" : "bg-profit/10 text-profit")}>
            {killed.length}
          </span>
        </h3>
        {killed.length === 0 ? (
          <p className="text-xs text-text-muted">No strategy is killed{ddPct > 0 ? "" : " — all clear"}.</p>
        ) : (
          <ul className="space-y-2">
            {killed.map(([type, v]) => (
              <li key={type} className="flex items-start gap-2 text-xs">
                <span className="w-2 h-2 mt-1 rounded-full shrink-0" style={{ backgroundColor: STRATEGY_COLORS[type] || "#6B7280" }} />
                <span className="text-text-primary font-medium">{STRATEGY_LABELS[type] || type}</span>
                <span className="text-text-muted font-mono break-words min-w-0">{killReason(v)}</span>
              </li>
            ))}
          </ul>
        )}
      </GlassPanel>
    </motion.div>
  );
}
