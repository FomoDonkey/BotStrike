import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { Button } from "@/components/ui/Button";
import { Chip, StatusChip } from "@/components/ui/Chip";
import { ListRow } from "@/components/ui/ListRow";
import { INPUT_CLS } from "@/components/settings/FieldInput";
import { probeBridge, ApiError, type HealthResponse } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useSystemStore } from "@/stores/systemStore";
import { useBridgeConfig, setBridgeUrl, setBridgeToken, normalizeBridgeUrl, validateBridgeUrl, getBridgeMode, DEFAULT_BRIDGE_URL } from "@/lib/config";
import { restartWebSockets } from "@/hooks/useWebSocket";
import { WS_CHANNEL_LIST } from "@/lib/constants";

type TestState =
  | { state: "idle" }
  | { state: "testing" }
  | { state: "ok"; health: HealthResponse; ms: number; url: string }
  | { state: "fail"; detail: string };

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
    <Panel>
      <PanelHeader title="Bridge server" right={<Chip tone={draftMode === "remote" ? "blue" : "neutral"} size="xs">{draftMode}</Chip>} />
      <div className="px-4 py-3 space-y-4">
        <label className="block">
          <span className="text-[12.5px] font-medium text-text-2 block mb-1">Bridge URL (host[:port], http:// or https://)</span>
          <input
            value={draftUrl}
            onChange={(e) => { setDraftUrl(e.target.value); setTest({ state: "idle" }); }}
            onKeyDown={(e) => { if (e.key === "Enter") void runTest(); }}
            spellCheck={false}
            autoComplete="off"
            placeholder="http://192.168.1.204:9420"
            className={cn(INPUT_CLS, urlError && draftUrl.trim() && "border-rose")}
          />
          <p className="text-[12px] font-medium text-text-2 mt-1 break-words">
            {urlError && draftUrl.trim() ? (
              <span className="text-rose">{urlError}</span>
            ) : (
              <>
                Local <code className="text-text">{DEFAULT_BRIDGE_URL.replace(/^https?:\/\//, "")}</code> (bundled engine starts automatically)
                {" · "}LAN <code className="text-text">192.168.1.204:9420</code>{" · "}Tailscale <code className="text-text">100.x.y.z:9420</code>
                {normalized && normalized !== draftUrl.trim() && <> · saved as <code className="text-text">{normalized}</code></>}
              </>
            )}
          </p>
        </label>

        <label className="block">
          <span className="text-[12.5px] font-medium text-text-2 block mb-1">Auth token — required on a remote bridge to edit the configuration, control the bot and run backtests (server <code className="text-text">.env</code>)</span>
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
            <button type="button" onClick={() => setShowToken((v) => !v)} title={showToken ? "Hide token" : "Show token"} className="absolute right-2 top-1/2 -translate-y-1/2 text-text hover:bg-hover rounded-[6px] p-1">
              {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          {/* Where the value actually lives. Without this the operator has to ask someone, which is
              what happened on 2026-09-04 when every Close button showed as locked. */}
          {!currentToken && (
            <p className="mt-1.5 text-[12px] font-medium text-text-2 leading-snug">
              The value is <code className="text-text">BOTSTRIKE_AUTH_TOKEN</code> in the bridge&apos;s <code className="text-text">.env</code>.
              It is stored per browser and per address, so a new address needs it again. To copy it without
              displaying it, in Git Bash:{" "}
              <code className="text-text break-all">ssh root@HOST &apos;pct exec CT -- grep BOTSTRIKE_AUTH_TOKEN /opt/botstrike/app/.env&apos; | cut -d= -f2 | tr -d &apos;
&apos; | clip</code>
            </p>
          )}
        </label>

        <div className="flex items-center gap-2 flex-wrap">
          <Button variant="secondary" onClick={runTest} disabled={!normalized} loading={test.state === "testing"}>{test.state === "testing" ? "Testing…" : "Test connection"}</Button>
          <Button variant="primary" onClick={save} disabled={!dirty || !normalized}>Save &amp; reconnect</Button>
          {test.state === "ok" && (
            <span className="text-[12.5px] font-medium text-mint">
              {test.health.status} · engine {test.health.engine_running ? "running" : "stopped"} · {test.health.mode}{test.health.version ? ` · v${test.health.version}` : ""} · {test.ms} ms
            </span>
          )}
          {test.state === "fail" && <span className="text-[12.5px] font-medium text-rose break-all">{test.detail}</span>}
          {test.state === "idle" && savedAt && !dirty && <span className="text-[12.5px] font-medium text-text">Saved · reconnecting…</span>}
        </div>

        <div className="pt-3 border-t border-hairline grid grid-cols-1 sm:grid-cols-2 gap-x-6">
          <ListRow label="Active URL"><span className="break-all whitespace-normal text-right">{currentUrl}</span></ListRow>
          <ListRow label="Bridge"><StatusChip status={bridgeConnected ? "online" : "offline"} size="xs" /></ListRow>
          <ListRow label="Engine"><StatusChip status={engineRunning ? "running" : "stopped"} size="xs" /></ListRow>
          <ListRow label="WS channels">{openChannels}/{WS_CHANNEL_LIST.length}</ListRow>
        </div>
      </div>
    </Panel>
  );
}
