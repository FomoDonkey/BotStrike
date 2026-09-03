import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Wifi, WifiOff, Loader2, Play, Settings2 } from "lucide-react";
import { useSystemStore } from "@/stores/systemStore";
import { useExchangeStore } from "@/stores/exchangeStore";
import { ExchangeSelector } from "./ExchangeSelector";
import { startWebSockets, restartWebSockets } from "@/hooks/useWebSocket";
import { useBridgeConfig, getBridgeUrl } from "@/lib/config";
import { probeBridge } from "@/lib/api";
import { OVERLAY_CONNECTED_MS, OVERLAY_CONNECT_TIMEOUT_MS } from "@/lib/constants";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";

type Phase = "probing" | "setup" | "connecting" | "unreachable" | "dismissed";

export function ConnectionOverlay() {
  const bridgeConnected = useSystemStore((s) => s.bridgeConnected);
  const exchange = useExchangeStore((s) => s.exchange);
  const { url: bridgeUrl, mode } = useBridgeConfig();
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>("probing");

  // On mount: if the bridge already answers healthy, connect silently — no setup dialog on
  // every page load. Probe failure falls back to the classic setup flow.
  useEffect(() => {
    let cancelled = false;
    probeBridge(getBridgeUrl())
      .then((h) => {
        if (cancelled) return;
        if (h.exchange === "binance" || h.exchange === "hyperliquid") {
          useExchangeStore.getState().setExchange(h.exchange);
        }
        startWebSockets();
        setPhase("dismissed");
      })
      .catch(() => {
        if (!cancelled) setPhase("setup");
      });
    return () => { cancelled = true; };
  }, []);

  const waiting = phase === "connecting" || phase === "unreachable";
  const showConnected = bridgeConnected && waiting;

  useEffect(() => {
    if (!bridgeConnected || !waiting) return;
    const t = setTimeout(() => setPhase("dismissed"), OVERLAY_CONNECTED_MS);
    return () => clearTimeout(t);
  }, [bridgeConnected, waiting]);

  useEffect(() => {
    if (phase !== "connecting") return;
    const t = setTimeout(() => setPhase((p) => (p === "connecting" ? "unreachable" : p)), OVERLAY_CONNECT_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [phase]);

  if (phase === "dismissed" || phase === "probing") return null;

  const hostLabel = bridgeUrl.replace(/^https?:\/\//, "");
  const modeBadge = <Chip tone={mode === "remote" ? "blue" : "neutral"} size="xs" className="ml-2 align-middle">{mode}</Chip>;
  const goSettings = () => {
    setPhase("dismissed");
    navigate("/settings", { state: { tab: "connection" } });
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 px-3">
      <div className="rounded-[10px] bg-panel border border-hairline-strong p-6 sm:p-8 max-w-lg w-full text-center">
        {showConnected && (
          <>
            <div className="w-14 h-14 mx-auto mb-4 rounded-[10px] bg-mint-soft flex items-center justify-center">
              <Wifi className="w-7 h-7 text-mint" />
            </div>
            <h2 className="text-[18px] font-semibold text-text mb-1">Connected</h2>
            <p className="text-[13px] font-medium text-text-2">{hostLabel}{modeBadge}</p>
          </>
        )}

        {phase === "setup" && (
          <>
            <h2 className="text-[24px] font-bold text-text mb-1">BotStrike</h2>
            <p className="text-[13px] font-medium text-text-2 mb-2">Select your exchange to get started</p>
            <p className="text-[12.5px] font-medium text-text-2 mb-6">
              Bridge <span className="num text-text">{hostLabel}</span>{modeBadge}
              <button type="button" onClick={goSettings} className="ml-2 text-mint underline">change</button>
            </p>
            <ExchangeSelector />
            <div className="flex gap-3 mt-6">
              <Button variant="primary" className="flex-1 h-10" icon={<Play className="w-4 h-4" />} onClick={() => { startWebSockets(); setPhase("connecting"); }}>Connect</Button>
              <Button variant="secondary" className="h-10" onClick={() => { startWebSockets(); setPhase("dismissed"); }}>Skip</Button>
            </div>
          </>
        )}

        {phase === "connecting" && !showConnected && (
          <>
            <div className="w-14 h-14 mx-auto mb-4 rounded-[10px] bg-amber-soft flex items-center justify-center">
              <Loader2 className="w-7 h-7 text-amber animate-spin" />
            </div>
            <h2 className="text-[18px] font-semibold text-text mb-2">Connecting to bridge…</h2>
            <p className="text-[13px] font-medium text-text-2 mb-1">Exchange <span className="text-text uppercase">{exchange}</span></p>
            <p className="text-[13px] font-medium text-text-2 mb-4">Waiting for <span className="num text-text">{hostLabel}</span>{modeBadge}</p>
            <div className="text-[12.5px] font-medium text-text-2 bg-panel-2 rounded-lg p-3 text-left">
              {mode === "local" ? "Starting bundled engine… (or run: python -m server.bridge)" : "Remote bridge — make sure you are on the LAN / Tailscale and port 9420 is open."}
            </div>
            <button type="button" onClick={() => setPhase("dismissed")} className="mt-4 text-[12.5px] font-medium text-text underline">Dismiss — browse without data</button>
          </>
        )}

        {phase === "unreachable" && !showConnected && (
          <>
            <div className="w-14 h-14 mx-auto mb-4 rounded-[10px] bg-rose-soft flex items-center justify-center">
              <WifiOff className="w-7 h-7 text-rose" />
            </div>
            <h2 className="text-[18px] font-semibold text-text mb-2">Bridge unreachable</h2>
            <p className="text-[13px] font-medium text-text-2 mb-4">
              No response from <span className="num text-rose">{hostLabel}</span>{modeBadge}
              <span className="block text-[12.5px] mt-1">
                {mode === "remote" ? "Check the server, the firewall (ufw :9420) and that you are on the LAN / Tailscale." : "The bundled engine did not start. Run manually: python -m server.bridge"}
              </span>
            </p>
            <div className="flex flex-wrap gap-2 justify-center">
              <Button variant="primary" onClick={() => { restartWebSockets(); setPhase("connecting"); }}>Retry</Button>
              <Button variant="secondary" icon={<Settings2 className="w-4 h-4" />} onClick={goSettings}>Connection settings</Button>
              <Button variant="ghost" onClick={() => setPhase("dismissed")}>Dismiss</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
