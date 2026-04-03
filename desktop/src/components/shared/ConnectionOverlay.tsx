import { useSystemStore } from "@/stores/systemStore";
import { useEffect, useState } from "react";
import { Wifi, WifiOff, Loader2 } from "lucide-react";

export function ConnectionOverlay() {
  const bridgeConnected = useSystemStore((s) => s.bridgeConnected);
  const engineRunning = useSystemStore((s) => s.engineRunning);
  const [show, setShow] = useState(true);
  const [dismissed, setDismissed] = useState(false);

  // Hide overlay once bridge connects
  useEffect(() => {
    if (bridgeConnected) {
      const t = setTimeout(() => setShow(false), 800);
      return () => clearTimeout(t);
    }
    if (!dismissed) setShow(true);
  }, [bridgeConnected, dismissed]);

  if (!show) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-base/80 backdrop-blur-sm">
      <div className="rounded-2xl bg-bg-surface border border-white/10 p-8 max-w-md text-center shadow-2xl">
        {bridgeConnected ? (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-profit/10 flex items-center justify-center">
              <Wifi className="w-8 h-8 text-profit" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">Connected</h2>
            <p className="text-sm text-text-secondary">Bridge server is running</p>
          </>
        ) : (
          <>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-warning/10 flex items-center justify-center">
              <Loader2 className="w-8 h-8 text-warning animate-spin" />
            </div>
            <h2 className="text-lg font-semibold text-text-primary mb-2">Connecting to Bridge...</h2>
            <p className="text-sm text-text-secondary mb-4">
              Waiting for the Python bridge server on <span className="font-mono text-accent">localhost:9420</span>
            </p>
            <div className="text-xs text-text-muted bg-bg-base/50 rounded-lg p-3 font-mono text-left">
              python -m server.bridge
            </div>
            <button
              onClick={() => { setDismissed(true); setShow(false); }}
              className="mt-4 text-xs text-text-muted hover:text-text-secondary transition-colors"
            >
              Dismiss — browse without data
            </button>
          </>
        )}
      </div>
    </div>
  );
}
