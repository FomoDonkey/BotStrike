import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { Settings, RefreshCw, RotateCcw, AlertTriangle } from "lucide-react";
import { Panel, EmptyState } from "@/components/ui/Panel";
import { TabBar } from "@/components/ui/TabBar";
import { Button } from "@/components/ui/Button";
import { SchemaForm } from "@/components/settings/SchemaForm";
import { api, ApiError, type ConfigResponse, type ConfigSchemaResponse } from "@/lib/api";
import { useBridgeConfig } from "@/lib/config";
import { useAlertStore } from "@/stores/alertStore";
import { ConnectionSettings } from "./ConnectionSettings";
import { AppearanceSettings } from "./AppearanceSettings";

export function SettingsPage() {
  const location = useLocation();
  // Remount on every navigation so `state.tab` (Strategies → "Parameters") is honoured.
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
  const { url: bridgeUrl, isLocal, token } = useBridgeConfig();
  const canEdit = isLocal || token.length > 0;
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

  const tabs = [
    { id: "connection", label: "Connection" },
    ...(schema?.groups ?? []).map((g) => ({ id: g.id, label: g.label })),
    { id: "appearance", label: "Appearance" },
  ];
  const activeGroup = schema?.groups.find((g) => g.id === tab) ?? null;
  const isConfigTab = tab !== "connection" && tab !== "appearance";

  const onSaved = (cfg: ConfigResponse, needsRestart: boolean, applied: string[]) => {
    setConfig(cfg);
    setRestartRequired(needsRestart || !!cfg.restart_required);
    addAlert({ level: "info", title: "Configuration saved", message: applied.length ? `Applied: ${applied.join(", ")}` : "No field changed" });
  };

  const restartEngine = async () => {
    setRestarting(true);
    try {
      const r = await api.botRestart();
      addAlert({ level: "info", title: "Engine restarting", message: `${r.status}${r.mode ? ` · ${r.mode}` : ""}` });
      setRestartRequired(false);
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
    <div className="flex flex-col gap-3 p-3 sm:p-4 min-w-0">
      <h1 className="text-[18px] font-semibold text-text flex items-center gap-2"><Settings className="w-5 h-5 text-mint" /> Settings</h1>

      <Panel className="overflow-hidden">
        <TabBar tabs={tabs} value={tab} onChange={setTab} flush />
      </Panel>

      {restartRequired && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber/60 bg-amber-soft px-4 py-2.5">
          <AlertTriangle className="w-4 h-4 text-amber shrink-0" />
          <span className="text-[13px] font-medium text-text flex-1 min-w-[12rem]">Some changes only apply after an engine restart.</span>
          <Button variant="amber" size="sm" icon={<RefreshCw className="w-3.5 h-3.5" />} onClick={restartEngine} loading={restarting} disabled={!canEdit} title={canEdit ? undefined : "Remote bridge — set the auth token in Connection"}>
            Restart engine
          </Button>
        </div>
      )}

      {tab === "connection" ? (
        <ConnectionSettings />
      ) : tab === "appearance" ? (
        <AppearanceSettings />
      ) : loading ? (
        <Panel className="p-8"><EmptyState>Loading configuration…</EmptyState></Panel>
      ) : !config ? (
        <Panel className="p-8"><EmptyState sub={loadError ?? "Start the engine (Bot → Start) or check Settings → Connection."}>Configuration unavailable from {bridgeUrl.replace(/^https?:\/\//, "")}</EmptyState></Panel>
      ) : !schema ? (
        <Panel className="p-8"><EmptyState sub={schemaError ?? "Upgrade the bridge to v2.14+ (GET /api/config/schema) to edit parameters from here."}>This bridge does not expose the configuration schema</EmptyState></Panel>
      ) : activeGroup ? (
        <SchemaForm key={activeGroup.id} group={activeGroup} config={config} onSaved={onSaved} readOnly={!canEdit} />
      ) : (
        <Panel className="p-8"><EmptyState>Unknown settings group “{tab}”</EmptyState></Panel>
      )}

      {isConfigTab && config && schema && (
        <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
          {resetStep === "idle" && (
            <Button variant="ghost" size="sm" icon={<RotateCcw className="w-3.5 h-3.5" />} onClick={() => setResetStep("confirm")} disabled={!canEdit}>Reset all to defaults</Button>
          )}
          {resetStep !== "idle" && (
            <>
              <span className="text-[12.5px] font-medium text-rose">Delete every override and go back to the server defaults?</span>
              <Button variant="danger" size="sm" onClick={resetDefaults} loading={resetStep === "busy"}>Yes, reset</Button>
              <Button variant="secondary" size="sm" onClick={() => setResetStep("idle")} disabled={resetStep === "busy"}>Cancel</Button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
