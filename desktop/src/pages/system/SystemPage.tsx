import { useEffect, useState, useRef } from "react";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { PulsingDot } from "@/components/shared/PulsingDot";
import { useSystemStore } from "@/stores/systemStore";
import { formatDuration, cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useMarketStore } from "@/stores/marketStore";
import { Monitor, Cpu, Wifi, WifiOff, Clock, Users, Activity, Play, Square, RefreshCw } from "lucide-react";
import { ExchangeSelector } from "@/components/shared/ExchangeSelector";
import { useExchangeStore } from "@/stores/exchangeStore";

export function SystemPage() {
  const system = useSystemStore();
  const logs = useSystemStore((s) => s.logs);
  const hasPriceData = useMarketStore((s) => Object.keys(s.prices).length > 0);
  const [botStatus, setBotStatus] = useState<any>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  // Fetch bot status periodically
  useEffect(() => {
    const fetch = () => api.botStatus().then(setBotStatus).catch(() => null);
    fetch();
    const i = setInterval(fetch, 5000);
    return () => clearInterval(i);
  }, []);

  // Auto-scroll when new logs arrive
  useEffect(() => {
    const timer = setTimeout(() => {
      logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 100);
    return () => clearTimeout(timer);
  }, [logs.length]);

  const exchange = useExchangeStore((s) => s.exchange);
  const [startMode, setStartMode] = useState<"paper" | "dry_run" | "live">("paper");
  const handleStart = () => api.botStart(startMode, exchange).catch(() => null);
  const handleStop = () => api.botStop().catch(() => null);

  return (
    <motion.div
      className="space-y-4 h-full flex flex-col"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Monitor className="w-5 h-5 text-accent" /> System Monitor
      </h1>

      <div className="grid grid-cols-2 gap-4">
        {/* Engine Status */}
        <GlassPanel className="p-5" glow={system.engineRunning}>
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Trading Engine</h3>
          <div className="flex items-center gap-3 mb-4">
            <PulsingDot active={system.engineRunning} />
            <span className={cn(
              "text-lg font-semibold",
              system.engineRunning ? "text-profit" : "text-loss"
            )}>
              {system.engineRunning ? "Running" : "Stopped"}
            </span>
          </div>
          <div className="space-y-2 text-xs mb-4">
            <div className="flex justify-between">
              <span className="text-text-muted flex items-center gap-1"><Cpu className="w-3 h-3" /> Mode</span>
              <span className={cn(
                "font-mono uppercase font-semibold",
                system.mode === "live" ? "text-loss" : system.mode === "paper" ? "text-warning" : "text-info"
              )}>
                {system.mode}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted flex items-center gap-1"><Clock className="w-3 h-3" /> Uptime</span>
              <span className="font-mono">{formatDuration(system.uptimeSec)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted flex items-center gap-1"><Users className="w-3 h-3" /> WS Clients</span>
              <span className="font-mono">{system.clientsConnected}</span>
            </div>
            {botStatus && (
              <>
                <div className="flex justify-between">
                  <span className="text-text-muted">Equity</span>
                  <span className="font-mono text-text-primary">${(botStatus.equity ?? 300).toFixed(2)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-muted">PnL</span>
                  <span className={cn("font-mono", (botStatus.pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                    ${(botStatus.pnl ?? 0).toFixed(2)}
                  </span>
                </div>
              </>
            )}
          </div>
          {/* Mode + Exchange Selector (only when engine is stopped) */}
          {!system.engineRunning && (
            <div className="space-y-4 mb-4">
              <div>
                <label className="text-xs text-text-muted block mb-2">Mode</label>
                <div className="flex gap-2">
                  {(["paper", "dry_run", "live"] as const).map((m) => (
                    <button
                      key={m}
                      onClick={() => setStartMode(m)}
                      className={cn(
                        "flex-1 px-3 py-2 rounded-lg text-xs font-semibold uppercase transition-all border",
                        startMode === m
                          ? m === "live" ? "border-loss bg-loss/10 text-loss"
                            : m === "paper" ? "border-warning bg-warning/10 text-warning"
                            : "border-info bg-info/10 text-info"
                          : "border-white/5 text-text-muted hover:border-white/10",
                      )}
                    >
                      {m.replace("_", " ")}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-xs text-text-muted block mb-2">Exchange</label>
                <ExchangeSelector />
              </div>
            </div>
          )}
          {system.engineRunning && (
            <div className="flex justify-between text-xs mb-4">
              <span className="text-text-muted">Exchange</span>
              <span className="font-mono text-text-primary uppercase">{botStatus?.exchange || exchange}</span>
            </div>
          )}
          {/* Controls */}
          <div className="flex gap-2">
            <button
              onClick={handleStart}
              disabled={system.engineRunning}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold transition-all",
                system.engineRunning
                  ? "bg-white/5 text-text-muted cursor-not-allowed"
                  : "bg-profit/10 text-profit hover:bg-profit/20"
              )}
            >
              <Play className="w-3 h-3" /> Start
            </button>
            <button
              onClick={handleStop}
              disabled={!system.engineRunning}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold transition-all",
                !system.engineRunning
                  ? "bg-white/5 text-text-muted cursor-not-allowed"
                  : "bg-loss/10 text-loss hover:bg-loss/20"
              )}
            >
              <Square className="w-3 h-3" /> Stop
            </button>
          </div>
        </GlassPanel>

        {/* WebSocket Status */}
        <GlassPanel className="p-5" glow={system.wsConnected}>
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Connections</h3>
          <div className="space-y-3">
            {[
              { name: "Bridge Server", connected: system.bridgeConnected, detail: "localhost:9420" },
              { name: `Market Data (${exchange === "hyperliquid" ? "Hyperliquid" : "Binance"})`,
                connected: system.wsConnected || hasPriceData,
                detail: exchange === "hyperliquid" ? "api.hyperliquid.xyz" : "fstream.binance.com" },
            ].map((c) => (
              <div key={c.name} className="flex items-center justify-between p-3 rounded-lg bg-white/[0.02]">
                <div className="flex items-center gap-3">
                  <PulsingDot active={c.connected} />
                  <div>
                    <p className="text-sm text-text-primary">{c.name}</p>
                    <p className="text-[10px] text-text-muted font-mono">{c.detail}</p>
                  </div>
                </div>
                <span className={cn(
                  "text-[10px] font-mono px-2 py-0.5 rounded font-semibold",
                  c.connected ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
                )}>
                  {c.connected ? "ONLINE" : "OFFLINE"}
                </span>
              </div>
            ))}
          </div>

          <div className="mt-4 pt-4 border-t border-white/5">
            <h4 className="text-xs text-text-muted mb-2">App Info</h4>
            <div className="space-y-1 text-xs">
              <div className="flex justify-between">
                <span className="text-text-muted">Version</span>
                <span className="font-mono text-text-secondary">2.10.2</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">Framework</span>
                <span className="font-mono text-text-secondary">Tauri v2 + React</span>
              </div>
            </div>
          </div>
        </GlassPanel>
      </div>

      {/* Live Logs */}
      <GlassPanel className="flex-1 p-4 flex flex-col min-h-0">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider flex items-center gap-2">
            <Activity className="w-3 h-3" /> Live Logs
          </h3>
          <button
            onClick={() => useSystemStore.setState({ logs: [] })}
            className="text-[10px] text-text-muted hover:text-text-secondary flex items-center gap-1"
          >
            <RefreshCw className="w-2.5 h-2.5" /> Clear
          </button>
        </div>
        <div className="flex-1 overflow-auto rounded-lg bg-bg-base/50 p-3 font-mono text-[11px] leading-5">
          {logs.length === 0 ? (
            <p className="text-text-muted">Waiting for log output...</p>
          ) : (
            logs.map((log, i) => (
              <div key={i} className="flex gap-2">
                <span className="text-text-muted shrink-0">
                  {new Date(log.timestamp * 1000).toLocaleTimeString("en-US", { hour12: false })}
                </span>
                <span className={cn(
                  "shrink-0 w-12",
                  log.level === "error" ? "text-loss" : log.level === "warn" ? "text-warning" : "text-text-muted"
                )}>
                  [{log.level}]
                </span>
                <span className="text-text-secondary break-all">{log.message}</span>
              </div>
            ))
          )}
          <div ref={logsEndRef} />
        </div>
      </GlassPanel>
    </motion.div>
  );
}
