import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { Brain } from "lucide-react";
import { api, ApiError, type ConfigField, type ConfigSchemaResponse, type StrategyInfo } from "@/lib/api";
import { STRATEGY_LABELS } from "@/lib/constants";
import { usePolling } from "@/hooks/usePolling";
import { useAlertStore } from "@/stores/alertStore";
import { allocationPath } from "@/components/settings/schemaUtils";
import { StrategyCard } from "./StrategyCard";
import { TrendDailyPanel } from "./TrendDailyPanel";
import { rememberAllocation } from "./allocationMemory";

export function StrategiesPage() {
  const navigate = useNavigate();
  const addAlert = useAlertStore((s) => s.addAlert);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [schema, setSchema] = useState<ConfigSchemaResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>("TREND_DAILY");

  const load = useCallback(async () => {
    try {
      const r = await api.strategies();
      setStrategies(r.strategies ?? []);
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, []);
  usePolling(load, 30_000);

  useEffect(() => {
    let cancelled = false;
    api.configSchema().then((s) => { if (!cancelled) setSchema(s); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const fieldByPath = useMemo(() => {
    const m = new Map<string, ConfigField>();
    for (const g of schema?.groups ?? []) for (const f of g.fields) m.set(f.path, f);
    return m;
  }, [schema]);

  /** Settings tab holding this strategy's params (matched by field name), with a sane fallback. */
  const paramGroupFor = useCallback((s: StrategyInfo): string => {
    if (s.settings_group) return s.settings_group; // bridge ≥ 2.15 says which Settings tab
    const keys = new Set(Object.keys(s.params ?? {}));
    for (const g of schema?.groups ?? []) {
      if (g.per_symbol) continue;
      if (g.fields.some((f) => keys.has(f.path.split(".").pop() ?? ""))) return g.id;
    }
    return s.type === "TREND_DAILY" ? "trend_daily" : "strategies";
  }, [schema]);

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
      await load();
    } catch (e) {
      addAlert({
        level: "critical",
        title: "Allocation update failed",
        message: e instanceof ApiError ? e.message : String(e),
        sound: "alert",
      });
    } finally {
      setBusy(null);
    }
  }, [addAlert, load]);

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
          <Brain className="w-5 h-5 text-accent" /> Strategy Manager
        </h1>
        <span className="text-[11px] text-text-muted">
          {strategies.filter((s) => s.active).length} active · {strategies.filter((s) => s.enabled ?? s.allocation > 0).length} enabled · {strategies.length} total
        </span>
      </div>

      {!loaded ? (
        <GlassPanel className="p-8 text-center"><p className="text-text-muted text-sm">Loading strategies…</p></GlassPanel>
      ) : strategies.length === 0 ? (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted text-sm">No strategies reported by the bridge.</p>
          {loadError && <p className="text-xs font-mono text-loss mt-2 break-all">{loadError}</p>}
        </GlassPanel>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {strategies.map((s) => {
            const isTrend = s.type === "TREND_DAILY";
            return (
              <Fragment key={s.type}>
                <StrategyCard
                  s={s}
                  allocField={fieldByPath.get(allocationPath(s.type))}
                  busy={busy === s.type}
                  expandable={isTrend}
                  expanded={isTrend && expanded === s.type}
                  onToggleExpand={() => setExpanded((cur) => (cur === s.type ? null : s.type))}
                  onAllocation={setAllocation}
                  onEditParams={() => navigate("/settings", { state: { tab: paramGroupFor(s) } })}
                />
                {isTrend && expanded === s.type && (
                  <div className="lg:col-span-2 min-w-0">
                    <TrendDailyPanel />
                  </div>
                )}
              </Fragment>
            );
          })}
        </div>
      )}
      {loaded && loadError && strategies.length > 0 && (
        <p className="text-[11px] font-mono text-warning">Last refresh failed: {loadError}</p>
      )}
    </motion.div>
  );
}
