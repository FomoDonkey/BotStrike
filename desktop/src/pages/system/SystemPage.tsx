import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { useSystemStore } from "@/stores/systemStore";
import { PulsingDot } from "@/components/shared/PulsingDot";
import { formatDuration, cn } from "@/lib/utils";
import { Monitor, Cpu, Wifi, WifiOff, Clock, Users } from "lucide-react";

export function SystemPage() {
  const system = useSystemStore();

  return (
    <motion.div
      className="space-y-4"
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
          <div className="flex items-center gap-3 mb-3">
            <PulsingDot active={system.engineRunning} />
            <span className={cn(
              "text-lg font-semibold",
              system.engineRunning ? "text-profit" : "text-loss"
            )}>
              {system.engineRunning ? "Running" : "Stopped"}
            </span>
          </div>
          <div className="space-y-2 text-xs">
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
          </div>
        </GlassPanel>

        {/* WebSocket Status */}
        <GlassPanel className="p-5" glow={system.wsConnected}>
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">WebSocket Connections</h3>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {system.wsConnected ? (
                  <Wifi className="w-4 h-4 text-accent" />
                ) : (
                  <WifiOff className="w-4 h-4 text-loss" />
                )}
                <span className="text-sm text-text-primary">Binance Market Data</span>
              </div>
              <span className={cn(
                "text-xs font-mono px-2 py-0.5 rounded",
                system.wsConnected ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
              )}>
                {system.wsConnected ? "CONNECTED" : "DISCONNECTED"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <PulsingDot active={system.bridgeConnected} />
                <span className="text-sm text-text-primary">Bridge Server</span>
              </div>
              <span className={cn(
                "text-xs font-mono px-2 py-0.5 rounded",
                system.bridgeConnected ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
              )}>
                {system.bridgeConnected ? "CONNECTED" : "DISCONNECTED"}
              </span>
            </div>
          </div>
        </GlassPanel>
      </div>
    </motion.div>
  );
}
