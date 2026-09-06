import { Fragment, useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, type ConfigField, type EdgeStats, type StrategyInfo, type StrategyPortfolio } from "@/lib/api";
import { STRATEGY_LABELS } from "@/lib/constants";
import { useEndpoint } from "@/hooks/useEndpoint";
import { usePortfolio } from "@/hooks/usePortfolio";
import { useNow } from "@/hooks/useNow";
import { useAlertStore } from "@/stores/alertStore";
import { useBridgeConfig } from "@/lib/config";
import { allocationPath } from "@/components/settings/schemaUtils";
import { Panel, PanelHeader, EmptyState } from "@/components/ui/Panel";
import { StatusChip, StrategyTag } from "@/components/ui/Chip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Signed } from "@/components/ui/ListRow";
import { cn, formatMoney, formatPct, formatSignedMoney } from "@/lib/utils";
import { StrategyCard } from "./StrategyCard";
import { TrendDailyPanel } from "./TrendDailyPanel";
import { rememberAllocation } from "./allocationMemory";

interface LeaderRow {
  rank: number;
  type: string;
  status: string;
  share: number;
  pf?: StrategyPortfolio;
  edge?: EdgeStats;
}

function strategyStatus(s: StrategyInfo): string {
  if (s.killed) return "killed";
  if (s.active) return "active";
  if (s.enabled ?? s.allocation > 0) return "enabled";
  return "disabled";
}

/** Strategies (spec §3.3): vault-style cards + leaderboard table. */
export function StrategiesPage() {
  const navigate = useNavigate();
  const now = useNow();
  const addAlert = useAlertStore((s) => s.addAlert);
  const { isLocal, token } = useBridgeConfig();
  const canEdit = isLocal || token.length > 0;
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>("TREND_DAILY");
  const [reloadKey, setReloadKey] = useState(0);

  const strategiesEp = useEndpoint(() => api.strategies(), 30_000, String(reloadKey));
  const schemaEp = useEndpoint(() => api.configSchema(), 300_000);
  const edgeEp = useEndpoint(() => api.edge(), 30_000);
  const pf = usePortfolio(30_000);
  const strategies = useMemo(() => strategiesEp.data?.strategies ?? [], [strategiesEp.data]);

  const fieldByPath = useMemo(() => {
    const m = new Map<string, ConfigField>();
    for (const g of schemaEp.data?.groups ?? []) for (const f of g.fields) m.set(f.path, f);
    return m;
  }, [schemaEp.data]);

  const pfByType = useMemo(() => {
    const m = new Map<string, StrategyPortfolio>();
    for (const r of pf.data?.by_strategy ?? []) m.set(r.strategy, r);
    return m;
  }, [pf.data]);

  const paramGroupFor = useCallback((s: StrategyInfo): string => {
    if (s.settings_group) return s.settings_group;
    const keys = new Set(Object.keys(s.params ?? {}));
    for (const g of schemaEp.data?.groups ?? []) {
      if (g.per_symbol) continue;
      if (g.fields.some((f) => keys.has(f.path.split(".").pop() ?? ""))) return g.id;
    }
    return s.type === "TREND_DAILY" ? "trend_daily" : "strategies";
  }, [schemaEp.data]);

  const setAllocation = useCallback(async (type: string, value: number) => {
    setBusy(type);
    const label = STRATEGY_LABELS[type] ?? type;
    try {
      const key = allocationPath(type).split(".")[1];
      const res = await api.configUpdate({ trading: { [key]: value } });
      if (value > 0) rememberAllocation(type, value);
      addAlert({
        level: res.restart_required ? "warning" : "info",
        title: value > 0 ? `${label} → ${(value * 100).toFixed(0)}%` : `${label} disabled`,
        message: res.restart_required ? "Applies after an engine restart (Settings)" : "Applied to the running engine",
      });
      setReloadKey((k) => k + 1);
    } catch (e) {
      addAlert({ level: "critical", title: "Allocation update failed", message: e instanceof ApiError ? e.message : String(e), sound: "alert" });
    } finally {
      setBusy(null);
    }
  }, [addAlert]);

  const leaderboard = useMemo<LeaderRow[]>(() => {
    const totalAlloc = strategies.reduce((a, s) => a + ((s.enabled ?? s.allocation > 0) ? s.allocation : 0), 0);
    const rows = strategies.map((s) => ({
      rank: 0,
      type: s.type,
      status: strategyStatus(s),
      share: totalAlloc > 0 && (s.enabled ?? s.allocation > 0) ? s.allocation / totalAlloc : 0,
      pf: pfByType.get(s.type),
      edge: edgeEp.data?.strategies?.[s.type],
    }));
    rows.sort((a, b) => (b.pf?.pnl ?? b.edge?.net_pnl ?? -Infinity) - (a.pf?.pnl ?? a.edge?.net_pnl ?? -Infinity));
    return rows.map((r, i) => ({ ...r, rank: i + 1 }));
  }, [strategies, pfByType, edgeEp.data]);

  const columns: Column<LeaderRow>[] = [
    { id: "rank", label: "Rank", align: "l", sortValue: (r) => r.rank, render: (r) => <span className="font-semibold">#{r.rank}</span> },
    { id: "strategy", label: "Strategy", align: "l", sortValue: (r) => r.type, render: (r) => <StrategyTag strategy={r.type} /> },
    { id: "status", label: "Status", align: "l", sortValue: (r) => r.status, render: (r) => <StatusChip status={r.status} size="xs" /> },
    { id: "share", label: "Equity share", hint: "Allocation share among the enabled strategies", sortValue: (r) => r.share, render: (r) => <span className="num">{formatPct(r.share, 0)}</span> },
    { id: "pnl", label: "All-time PnL", sortValue: (r) => r.pf?.pnl ?? r.edge?.net_pnl ?? null, render: (r) => <Signed value={r.pf?.pnl ?? r.edge?.net_pnl ?? null} format={formatSignedMoney} /> },
    { id: "realized", label: "Realized", sortValue: (r) => r.pf?.realized ?? null, render: (r) => <Signed value={r.pf?.realized ?? null} format={formatSignedMoney} /> },
    { id: "volume", label: "Volume", sortValue: (r) => r.pf?.volume ?? null, render: (r) => <span className="num">{r.pf ? formatMoney(r.pf.volume) : "---"}</span> },
    { id: "trades", label: "Trades", sortValue: (r) => r.pf?.trades ?? r.edge?.n ?? null, render: (r) => <span className="num">{r.pf?.trades ?? r.edge?.n ?? "---"}</span> },
    { id: "fees", label: "Fees", sortValue: (r) => r.pf?.fees ?? r.edge?.fees ?? null, render: (r) => <span className="num">{typeof (r.pf?.fees ?? r.edge?.fees) === "number" ? formatMoney((r.pf?.fees ?? r.edge?.fees) as number) : "---"}</span> },
    { id: "wr", label: "Win rate", sortValue: (r) => r.pf?.win_rate ?? r.edge?.win_rate ?? null, render: (r) => <span className="num">{typeof (r.pf?.win_rate ?? r.edge?.win_rate) === "number" ? formatPct((r.pf?.win_rate ?? r.edge?.win_rate) as number, 0) : "---"}</span> },
    { id: "sharpe", label: "Sharpe", sortValue: (r) => r.pf?.sharpe ?? null, render: (r) => <span className="num">{typeof r.pf?.sharpe === "number" ? r.pf.sharpe.toFixed(2) : "n/a"}</span> },
    { id: "dd", label: "Max DD", sortValue: (r) => r.pf?.max_drawdown ?? null, render: (r) => <span className={cn("num", r.pf && r.pf.max_drawdown > 0 && "text-rose")}>{r.pf ? formatPct(r.pf.max_drawdown) : "---"}</span> },
    { id: "t", label: "t-stat", sortValue: (r) => r.pf?.t_stat ?? (r.edge?.verdict === "insufficient" ? null : r.edge?.t_stat) ?? null, render: (r) => { const t = r.pf?.t_stat ?? (r.edge?.verdict === "insufficient" ? null : r.edge?.t_stat); return <span className={cn("num", typeof t === "number" && t <= -2 && "text-rose", typeof t === "number" && t >= 2 && "text-mint")}>{typeof t === "number" ? t.toFixed(2) : "---"}</span>; } },
  ];

  const active = strategies.filter((s) => s.active).length;
  const enabledN = strategies.filter((s) => s.enabled ?? s.allocation > 0).length;

  return (
    <div className="flex flex-col gap-3 p-3 sm:p-4 min-w-0">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h1 className="text-[18px] font-semibold text-text">Strategies</h1>
        <span className="text-[12.5px] font-medium text-text-2">
          <span className="text-text font-semibold">{active}</span> active · <span className="text-text font-semibold">{enabledN}</span> enabled · <span className="text-text font-semibold">{strategies.length}</span> total
          {!canEdit && <span className="text-amber"> · read-only (remote bridge without a token)</span>}
        </span>
      </div>

      {!strategiesEp.loaded ? (
        <Panel className="p-8"><EmptyState>Loading strategies…</EmptyState></Panel>
      ) : strategies.length === 0 ? (
        <Panel className="p-8"><EmptyState sub={strategiesEp.error ?? undefined}>No strategies reported by the bridge</EmptyState></Panel>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-3">
          {strategies.map((s) => (
            <Fragment key={s.type}>
              <StrategyCard
                s={s}
                pf={pfByType.get(s.type)}
                edge={edgeEp.data?.strategies?.[s.type]}
                allocField={fieldByPath.get(allocationPath(s.type))}
                busy={busy === s.type}
                expanded={expanded === s.type}
                onToggleExpand={() => setExpanded((cur) => (cur === s.type ? null : s.type))}
                onAllocation={setAllocation}
                onEditParams={() => navigate("/settings", { state: { tab: paramGroupFor(s) } })}
                canEdit={canEdit}
                nowMs={now}
              />
            </Fragment>
          ))}
        </div>
      )}
      {/* The record, in one line. These used to occupy two greyed-out cards, which read as "not yet"
          rather than "never": they have no gross edge, and that is not a parameter away. */}
      {(strategiesEp.data?.retired?.length ?? 0) > 0 && (
        <div className="text-[12px] font-medium text-text-2 leading-snug">
          <span className="text-text font-semibold">Retired by the research:</span>{" "}
          {strategiesEp.data!.retired!.map((r, i) => (
            <span key={r.type}>
              {i > 0 && " · "}
              <span className="text-text" title={r.reason}>{r.name}</span>
            </span>
          ))}
          . No gross edge, so no parameter brings them back — hover for the evidence.
        </div>
      )}
      {expanded === "TREND_DAILY" && strategies.some((s) => s.type === "TREND_DAILY") && <TrendDailyPanel />}
      {strategiesEp.loaded && strategiesEp.error && strategies.length > 0 && (
        <p className="text-[12.5px] font-medium text-amber">Last refresh failed: {strategiesEp.error}</p>
      )}

      <Panel className="flex flex-col overflow-hidden">
        <PanelHeader title="Strategy leaderboard" right={pf.missing ? <span className="text-[12px] font-medium text-text-2">Volume · Sharpe · Max DD need bridge ≥ 2.16</span> : undefined} />
        <DataTable columns={columns} rows={leaderboard} rowKey={(r) => r.type} minWidth="1180px" defaultSort={{ id: "rank", dir: "asc" }} emptyText="No strategies to rank" />
      </Panel>
    </div>
  );
}
