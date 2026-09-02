import { useState } from "react";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { probeBridge, ApiError, type HealthResponse } from "@/lib/api";
import { Plug, Eye, EyeOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSystemStore } from "@/stores/systemStore";
import {
  useBridgeConfig,
  setBridgeUrl,
  setBridgeToken,
  normalizeBridgeUrl,
  validateBridgeUrl,
  getBridgeMode,
  DEFAULT_BRIDGE_URL,
} from "@/lib/config";
import { restartWebSockets } from "@/hooks/useWebSocket";
import { WS_CHANNEL_LIST } from "@/lib/constants";

type TestState =
  | { state: "idle" }
  | { state: "testing" }
  | { state: "ok"; health: HealthResponse; ms: number; url: string }
  | { state: "fail"; detail: string };

const INPUT_CLS =
  "w-full bg-bg-base border border-white/10 rounded-lg px-3 py-2 text-sm text-text-primary font-mono focus:outline-none focus:border-accent/50";

export function ConnectionSettings() {
  const { url: currentUrl, token: currentToken } = useBridgeConfig();
  const [draftUrl, setDraftUrl] = useState(currentUrl);
  const [draftToken, setDraftToken] = useState(currentToken);
  const [showToken, setShowToken] = useState(false);
  const [test, setTest] = useState<TestState>({ state: "idle" });
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const openChannels = useSystemStore((s) => s.openChannels.length);
  const bridgeConnected = useSystemStore((s) => s.bridgeConnected);
  const engineRunning = useSystemStore((s) => s.engineRunning);

  const urlError = validateBridgeUrl(draftUrl);
  const normalized = urlError ? null : normalizeBridgeUrl(draftUrl);
  const dirty = normalized !== currentUrl || draftToken.trim() !== currentToken;
  const draftMode = getBridgeMode(normalized ?? currentUrl);

  const runTest = async () => {
    if (!normalized) {
      setTest({ state: "fail", detail: urlError ?? "Invalid URL" });
      return;
    }
    setTest({ state: "testing" });
    const t0 = Date.now();
    try {
      const health = await probeBridge(normalized);
      setTest({ state: "ok", health, ms: Date.now() - t0, url: normalized });
    } catch (e) {
      setTest({ state: "fail", detail: e instanceof ApiError ? e.message : String(e) });
    }
  };

  const save = () => {
    const n = setBridgeUrl(draftUrl);
    if (!n) {
      setTest({ state: "fail", detail: urlError ?? "Invalid URL" });
      return;
    }
    setBridgeToken(draftToken);
    setDraftUrl(n);
    setDraftToken(draftToken.trim());
    restartWebSockets();
    setTest({ state: "idle" });
    setSavedAt(Date.now());
  };

  return (
    <GlassPanel className="p-4 sm:p-5">
      <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
        <Plug className="w-3 h-3" /> Bridge Server
        <span
          className={cn(
            "ml-auto px-2 py-0.5 rounded text-[10px] font-bold uppercase",
            draftMode === "remote" ? "bg-info/10 text-info" : "bg-white/5 text-text-muted",
          )}
        >
          {draftMode}
        </span>
      </h3>

      <label className="text-xs text-text-muted block mb-1">Bridge URL (host[:port], http:// or https://)</label>
      <input
        value={draftUrl}
        onChange={(e) => { setDraftUrl(e.target.value); setTest({ state: "idle" }); }}
        onKeyDown={(e) => { if (e.key === "Enter") void runTest(); }}
        spellCheck={false}
        autoComplete="off"
        placeholder="http://192.168.1.204:9420"
        className={cn(INPUT_CLS, urlError && draftUrl.trim() && "border-loss/50")}
      />
      <p className="text-[10px] text-text-muted mt-1 break-words">
        {urlError && draftUrl.trim() ? (
          <span className="text-loss">{urlError}</span>
        ) : (
          <>
            Local: <code>{DEFAULT_BRIDGE_URL.replace(/^https?:\/\//, "")}</code> (bundled engine is started automatically)
            {" · "}LAN: <code>192.168.1.204:9420</code>{" · "}Tailscale: <code>100.x.y.z:9420</code>
            {normalized && normalized !== draftUrl.trim() && (
              <> · will be saved as <code className="text-text-secondary">{normalized}</code></>
            )}
          </>
        )}
      </p>

      <label className="text-xs text-text-muted block mt-4 mb-1">
        Auth token (required for a remote bridge, to edit the configuration and to start/stop LIVE — from the server&apos;s <code>.env</code>)
      </label>
      <div className="relative">
        <input
          value={draftToken}
          onChange={(e) => setDraftToken(e.target.value)}
          type={showToken ? "text" : "password"}
          spellCheck={false}
          autoComplete="off"
          placeholder="BOTSTRIKE_AUTH_TOKEN"
          className={cn(INPUT_CLS, "pr-9")}
        />
        <button
          type="button"
          onClick={() => setShowToken((v) => !v)}
          title={showToken ? "Hide token" : "Show token"}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
        >
          {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>

      <div className="flex items-center gap-3 mt-4 flex-wrap">
        <button
          onClick={runTest}
          disabled={test.state === "testing" || !normalized}
          className="px-4 py-2 rounded-lg border border-white/10 text-sm text-text-secondary hover:border-white/20 disabled:opacity-50 transition-all"
        >
          {test.state === "testing" ? "Testing…" : "Test connection"}
        </button>
        <button
          onClick={save}
          disabled={!dirty || !normalized}
          className="px-4 py-2 rounded-lg bg-accent text-bg-base text-sm font-semibold disabled:opacity-40 hover:bg-accent/90 transition-all"
        >
          Save &amp; reconnect
        </button>
        {test.state === "ok" && (
          <span className="text-xs font-mono text-profit">
            {test.health.status} · engine {test.health.engine_running ? "running" : "stopped"} · {test.health.mode}
            {test.health.version ? ` · v${test.health.version}` : ""} · {test.ms} ms
          </span>
        )}
        {test.state === "fail" && <span className="text-xs font-mono text-loss break-all">{test.detail}</span>}
        {test.state === "idle" && savedAt && !dirty && (
          <span className="text-xs font-mono text-text-muted">Saved · reconnecting…</span>
        )}
      </div>

      <div className="mt-4 pt-4 border-t border-white/5 grid grid-cols-2 sm:grid-cols-4 text-xs gap-2">
        <div className="min-w-0">
          <span className="text-text-muted">Active URL</span>
          <p className="font-mono text-text-secondary break-all">{currentUrl}</p>
        </div>
        <div>
          <span className="text-text-muted">Bridge</span>
          <p className={cn("font-mono", bridgeConnected ? "text-profit" : "text-loss")}>
            {bridgeConnected ? "ONLINE" : "OFFLINE"}
          </p>
        </div>
        <div>
          <span className="text-text-muted">Engine</span>
          <p className={cn("font-mono", engineRunning ? "text-profit" : "text-text-muted")}>
            {engineRunning ? "RUNNING" : "STOPPED"}
          </p>
        </div>
        <div>
          <span className="text-text-muted">WS channels</span>
          <p className="font-mono text-text-secondary">{openChannels}/{WS_CHANNEL_LIST.length}</p>
        </div>
      </div>
    </GlassPanel>
  );
}
