import { useSystemStore } from "@/stores/systemStore";
import { useEffect, useState } from "react";
import { Wifi, Loader2, Play } from "lucide-react";
import { ExchangeSelector } from "./ExchangeSelector";
import { useExchangeStore } from "@/stores/exchangeStore";
import { startWebSockets } from "@/hooks/useWebSocket";

type Phase = "setup" | "connecting" | "connected" | "dismissed";

export function ConnectionOverlay() {
  const bridgeConnected = useSystemStore((s) => s.bridgeConnected);
  const [phase, setPhase] = useState<Phase>("setup");

  // Once bridge connects, show "connected" briefly then auto-dismiss
  useEffect(() => {
    if (bridgeConnected && (phase === "connecting" || phase === "connected")) {
      setPhase("connected");
      const t = setTimeout(() => setPhase("dismissed"), 1000);
      return () => clearTimeout(t);
    }
  }, [bridgeConnected]); // eslint-disable-line -- intentionally exclude phase to avoid cancel loop

  if (phase === "dismissed") return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-base/90 backdrop-blur-md">
      <div className="rounded-2xl bg-bg-surface border border-white/10 p-8 max-w-lg w-full text-center shadow-2xl">

        {/* Phase 1: Setup — choose exchange */}
        {phase === "setup" && (
          <>
            <h2 className="text-2xl font-bold text-text-primary mb-1">BotStrike</h2>
            <p className="text-sm text-text-secondary mb-6">Select your exchange to get started</p>

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
        {phase === "connecting" && (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-warning/10 flex items-center justify-center">
              <Loader2 className="w-8 h-8 text-warning animate-spin" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">
              Connecting to Bridge...
            </h2>
            <p className="text-sm text-text-secondary mb-1">
              Exchange: <span className="font-mono text-accent uppercase">
                {useExchangeStore.getState().exchange}
              </span>
            </p>
            <p className="text-sm text-text-secondary mb-4">
              Waiting for <span className="font-mono text-accent">localhost:9420</span>
            </p>
            <div className="text-xs text-text-muted bg-bg-base/50 rounded-lg p-3 font-mono text-left">
              python -m server.bridge
            </div>
            <button
              onClick={() => setPhase("dismissed")}
              className="mt-4 text-xs text-text-muted hover:text-text-secondary transition-colors"
            >
              Dismiss — browse without data
            </button>
          </>
        )}

        {/* Phase 3: Connected */}
        {phase === "connected" && (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-profit/10 flex items-center justify-center">
              <Wifi className="w-8 h-8 text-profit" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">Connected</h2>
            <p className="text-sm text-text-secondary">Bridge server is running</p>
          </>
        )}
      </div>
    </div>
  );
}
