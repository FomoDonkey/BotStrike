import { useEffect, useState, type ComponentType } from "react";
import { useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { SchemaForm } from "@/components/settings/SchemaForm";
import { api, ApiError, type ConfigResponse, type ConfigSchemaResponse } from "@/lib/api";
import {
  Settings, DollarSign, Zap, Bell, Server, Palette, Plug, Brain, TrendingUp, Activity,
  RefreshCw, RotateCcw, Loader2, AlertTriangle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useBridgeConfig } from "@/lib/config";
import { useAlertStore } from "@/stores/alertStore";
import { ConnectionSettings } from "./ConnectionSettings";
import { AppearanceSettings } from "./AppearanceSettings";

interface Tab {
  id: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
}

const GROUP_ICONS: Record<string, Tab["icon"]> = {
  capital: DollarSign,
  strategies: Brain,
  trend_daily: TrendingUp,
  edge: Activity,
  execution: Server,
  notifications: Bell,
  symbols: Zap,
};

const CONNECTION_TAB: Tab = { id: "connection", label: "Connection", icon: Plug };
const APPEARANCE_TAB: Tab = { id: "appearance", label: "Appearance", icon: Palette };

export function SettingsPage() {
  const location = useLocation();
  // Remount on every navigation so `state.tab` (Strategies → "Edit in Settings") is honoured.
  return <SettingsInner key={location.key} initialTab={(location.state as { tab?: string } | null)?.tab ?? "capital"} />;
}

function SettingsInner({ initialTab }: { initialTab: string }) {
  const [tab, setTab] = useState(initialTab);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [schema, setSchema] = useState<ConfigSchemaResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [restartRequired, setRestartRequired] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [resetStep, setResetStep] = useState<"idle" | "confirm" | "busy">("idle");
  const { url: bridgeUrl } = useBridgeConfig();
  const addAlert = useAlertStore((s) => s.addAlert);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.allSettled([api.config(), api.configSchema()]).then(([cfg, sch]) => {
      if (cancelled) return;
      if (cfg.status === "fulfilled") {
        setConfig(cfg.value);
        setRestartRequired(!!cfg.value.restart_required);
        setLoadError(null);
      } else {
        setLoadError(cfg.reason instanceof ApiError ? cfg.reason.message : String(cfg.reason));
      }
      if (sch.status === "fulfilled") {
        setSchema(sch.value);
        setSchemaError(null);
      } else {
        setSchemaError(sch.reason instanceof ApiError ? sch.reason.message : String(sch.reason));
      }
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [bridgeUrl]);

  const groupTabs: Tab[] = (schema?.groups ?? []).map((g) => ({
    id: g.id,
    label: g.label,
    icon: GROUP_ICONS[g.id] ?? Settings,
  }));
  const tabs: Tab[] = [CONNECTION_TAB, ...groupTabs, APPEARANCE_TAB];
  const activeGroup = schema?.groups.find((g) => g.id === tab) ?? null;
  const isConfigTab = tab !== "connection" && tab !== "appearance";

  const onSaved = (cfg: ConfigResponse, needsRestart: boolean, applied: string[]) => {
    setConfig(cfg);
    setRestartRequired(needsRestart || !!cfg.restart_required);
    addAlert({
      level: "info",
      title: "Configuration saved",
      message: applied.length ? `Applied: ${applied.join(", ")}` : "No field changed",
    });
  };

  const restartEngine = async () => {
    setRestarting(true);
    try {
      const r = await api.botRestart();
      addAlert({ level: "info", title: "Engine restarting", message: `${r.status}${r.mode ? ` · ${r.mode}` : ""}` });
      setRestartRequired(false);
      // Give the engine a moment, then re-read the config (restart_required should be false now).
      setTimeout(() => {
        api.config().then((c) => { setConfig(c); setRestartRequired(!!c.restart_required); }).catch(() => {});
      }, 3000);
    } catch (e) {
      addAlert({ level: "critical", title: "Restart failed", message: e instanceof ApiError ? e.message : String(e), sound: "alert" });
    } finally {
      setRestarting(false);
    }
  };

  const resetDefaults = async () => {
    setResetStep("busy");
    try {
      const cfg = await api.configReset();
      setConfig(cfg);
      setRestartRequired(cfg.restart_required !== false);
      addAlert({ level: "warning", title: "Configuration reset", message: "All overrides deleted — restart the engine to apply." });
    } catch (e) {
      addAlert({ level: "critical", title: "Reset failed", message: e instanceof ApiError ? e.message : String(e), sound: "alert" });
    } finally {
      setResetStep("idle");
    }
  };

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Settings className="w-5 h-5 text-accent" /> Settings & Configuration
      </h1>

      {/* Tabs — scroll horizontally on narrow screens */}
      <div className="flex gap-1 p-1 rounded-xl bg-bg-surface/50 overflow-x-auto scrollbar-none">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "flex items-center gap-2 px-3 sm:px-4 py-2 rounded-lg text-sm transition-all shrink-0 whitespace-nowrap",
              tab === t.id
                ? "bg-accent/10 text-accent"
                : "text-text-muted hover:text-text-secondary"
            )}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </button>
        ))}
      </div>

      {restartRequired && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-warning/30 bg-warning/10 px-4 py-3">
          <AlertTriangle className="w-4 h-4 text-warning shrink-0" />
          <span className="text-sm text-warning flex-1 min-w-[12rem]">
            Some changes only apply after an engine restart.
          </span>
          <button
            onClick={restartEngine}
            disabled={restarting}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-warning text-bg-base text-xs font-semibold hover:bg-warning/90 disabled:opacity-50 transition-all"
          >
            {restarting ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
            Restart engine
          </button>
        </div>
      )}

      {tab === "connection" ? (
        <ConnectionSettings />
      ) : tab === "appearance" ? (
        <AppearanceSettings />
      ) : loading ? (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted">Loading configuration...</p>
        </GlassPanel>
      ) : !config ? (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted">Configuration unavailable from {bridgeUrl.replace(/^https?:\/\//, "")}</p>
          {loadError && <p className="text-xs font-mono text-loss mt-2 break-all">{loadError}</p>}
          <p className="text-xs text-text-muted mt-2">
            Start the engine (System → Start) or check Settings → Connection.
          </p>
        </GlassPanel>
      ) : !schema ? (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted">This bridge does not expose the configuration schema.</p>
          {schemaError && <p className="text-xs font-mono text-loss mt-2 break-all">{schemaError}</p>}
          <p className="text-xs text-text-muted mt-2">
            Upgrade the bridge to v2.14+ (GET /api/config/schema) to edit parameters from here.
          </p>
        </GlassPanel>
      ) : activeGroup ? (
        <SchemaForm key={activeGroup.id} group={activeGroup} config={config} onSaved={onSaved} />
      ) : (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted">Unknown settings group “{tab}”.</p>
        </GlassPanel>
      )}

      {/* Reset to defaults — global (every override), with a confirm step */}
      {isConfigTab && config && schema && (
        <div className="flex flex-wrap items-center justify-end gap-2 pt-2">
          {resetStep === "idle" && (
            <button
              onClick={() => setResetStep("confirm")}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-text-muted hover:text-loss border border-transparent hover:border-loss/30 transition-all"
            >
              <RotateCcw className="w-3 h-3" /> Reset all to defaults
            </button>
          )}
          {resetStep !== "idle" && (
            <>
              <span className="text-xs text-loss">Delete every override and go back to the server defaults?</span>
              <button
                onClick={resetDefaults}
                disabled={resetStep === "busy"}
                className="px-3 py-1.5 rounded-lg bg-loss text-bg-base text-xs font-semibold hover:bg-loss/90 disabled:opacity-50"
              >
                {resetStep === "busy" ? "Resetting…" : "Yes, reset"}
              </button>
              <button
                onClick={() => setResetStep("idle")}
                disabled={resetStep === "busy"}
                className="px-3 py-1.5 rounded-lg border border-white/10 text-xs text-text-secondary hover:border-white/20"
              >
                Cancel
              </button>
            </>
          )}
        </div>
      )}
    </motion.div>
  );
}
