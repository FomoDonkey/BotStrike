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
import { cn } from "@/lib/utils";

type Phase = "probing" | "setup" | "connecting" | "unreachable" | "dismissed";

export function ConnectionOverlay() {
  const bridgeConnected = useSystemStore((s) => s.bridgeConnected);
  const exchange = useExchangeStore((s) => s.exchange);
  const { url: bridgeUrl, mode } = useBridgeConfig();
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>("probing");

  // On mount: if the bridge already answers healthy (server deployment with
  // autostart, or an engine left running), connect silently — no setup dialog
  // on every page load. Probe failure falls back to the classic setup flow.
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

  // "Connected" is derived, not stored: bridge reachable while we were waiting for it.
  // (v2.11.0 stored it as a phase and the effect below cancelled its own timer — see audit 05.)
  const waiting = phase === "connecting" || phase === "unreachable";
  const showConnected = bridgeConnected && waiting;

  // Bridge became reachable → show "Connected" for a moment, then auto-dismiss.
  // `waiting` stays true across connecting → unreachable, so the timer is only cancelled by a
  // real bridge flap (which simply re-arms it on the next transition) or by a user action.
  useEffect(() => {
    if (!bridgeConnected || !waiting) return;
    const t = setTimeout(() => setPhase("dismissed"), OVERLAY_CONNECTED_MS);
    return () => clearTimeout(t);
  }, [bridgeConnected, waiting]);

  // Nothing after N seconds → tell the user which URL failed instead of spinning forever.
  useEffect(() => {
    if (phase !== "connecting") return;
    const t = setTimeout(() => setPhase((p) => (p === "connecting" ? "unreachable" : p)), OVERLAY_CONNECT_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [phase]);

  if (phase === "dismissed" || phase === "probing") return null;

  const hostLabel = bridgeUrl.replace(/^https?:\/\//, "");
  const modeBadge = (
    <span
      className={cn(
        "ml-2 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase align-middle",
        mode === "remote" ? "bg-info/10 text-info" : "bg-white/5 text-text-muted",
      )}
    >
      {mode}
    </span>
  );
  const goSettings = () => {
    setPhase("dismissed");
    navigate("/settings", { state: { tab: "connection" } });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-base/90 backdrop-blur-md">
      <div className="rounded-2xl bg-bg-surface border border-white/10 p-8 max-w-lg w-full text-center shadow-2xl">

        {/* Phase 3: Connected (derived) */}
        {showConnected && (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-profit/10 flex items-center justify-center">
              <Wifi className="w-8 h-8 text-profit" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">Connected</h2>
            <p className="text-sm text-text-secondary">{hostLabel}{modeBadge}</p>
          </>
        )}

        {/* Phase 1: Setup — choose exchange */}
        {phase === "setup" && (
          <>
            <h2 className="text-2xl font-bold text-text-primary mb-1">BotStrike</h2>
            <p className="text-sm text-text-secondary mb-2">Select your exchange to get started</p>
            <p className="text-xs text-text-muted mb-6">
              Bridge: <span className="font-mono text-accent">{hostLabel}</span>{modeBadge}
              <button onClick={goSettings} className="ml-2 underline hover:text-text-secondary">change</button>
            </p>

            <ExchangeSelector />

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => { startWebSockets(); setPhase("connecting"); }}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-accent text-bg-base font-semibold text-sm hover:bg-accent/90 transition-all"
              >
                <Play className="w-4 h-4" /> Connect
              </button>
              <button
                onClick={() => { startWebSockets(); setPhase("dismissed"); }}
                className="px-4 py-3 rounded-xl border border-white/10 text-text-muted text-sm hover:border-white/20 transition-all"
              >
                Skip
              </button>
            </div>
          </>
        )}

        {/* Phase 2: Connecting */}
        {phase === "connecting" && !showConnected && (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-warning/10 flex items-center justify-center">
              <Loader2 className="w-8 h-8 text-warning animate-spin" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">Connecting to Bridge...</h2>
            <p className="text-sm text-text-secondary mb-1">
              Exchange: <span className="font-mono text-accent uppercase">{exchange}</span>
            </p>
            <p className="text-sm text-text-secondary mb-4">
              Waiting for <span className="font-mono text-accent">{hostLabel}</span>{modeBadge}
            </p>
            {mode === "local" ? (
              <div className="text-xs text-text-muted bg-bg-base/50 rounded-lg p-3 font-mono text-left">
                Starting bundled engine… (or run: python -m server.bridge)
              </div>
            ) : (
              <div className="text-xs text-text-muted bg-bg-base/50 rounded-lg p-3 font-mono text-left">
                Remote bridge — make sure you are on the LAN / Tailscale and port 9420 is open.
              </div>
            )}
            <button
              onClick={() => setPhase("dismissed")}
              className="mt-4 text-xs text-text-muted hover:text-text-secondary transition-colors"
            >
              Dismiss — browse without data
            </button>
          </>
        )}

        {/* Phase 2b: Unreachable */}
        {phase === "unreachable" && !showConnected && (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-loss/10 flex items-center justify-center">
              <WifiOff className="w-8 h-8 text-loss" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">Bridge unreachable</h2>
            <p className="text-sm text-text-secondary mb-4">
              No response from <span className="font-mono text-loss">{hostLabel}</span>{modeBadge}
              {mode === "remote" ? (
                <span className="block text-xs text-text-muted mt-1">
                  Check the server, the firewall (ufw :9420) and that you are on the LAN / Tailscale.
                </span>
              ) : (
                <span className="block text-xs text-text-muted mt-1">
                  The bundled engine did not start. Run manually: python -m server.bridge
                </span>
              )}
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => { restartWebSockets(); setPhase("connecting"); }}
                className="flex-1 px-4 py-2.5 rounded-xl bg-accent text-bg-base font-semibold text-sm hover:bg-accent/90 transition-all"
              >
                Retry
              </button>
              <button
                onClick={goSettings}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-white/10 text-text-secondary text-sm hover:border-white/20 transition-all"
              >
                <Settings2 className="w-4 h-4" /> Connection settings
              </button>
              <button onClick={() => setPhase("dismissed")} className="px-3 text-xs text-text-muted hover:text-text-secondary">
                Dismiss
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
