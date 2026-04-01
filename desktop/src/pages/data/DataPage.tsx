import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { useSystemStore } from "@/stores/systemStore";
import { PulsingDot } from "@/components/shared/PulsingDot";
import { Database, Wifi } from "lucide-react";

export function DataPage() {
  const system = useSystemStore();

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Database className="w-5 h-5 text-accent" /> Market Data
      </h1>

      <GlassPanel className="p-5">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Connection Status</h3>
        <div className="flex items-center gap-4">
          <PulsingDot active={system.wsConnected} />
          <Wifi className={system.wsConnected ? "w-4 h-4 text-accent" : "w-4 h-4 text-loss"} />
          <span className="text-sm text-text-primary">
            {system.wsConnected ? "Connected to Binance WebSocket" : "Disconnected"}
          </span>
        </div>
      </GlassPanel>

      <GlassPanel className="p-8 text-center">
        <Database className="w-12 h-12 text-accent/30 mx-auto mb-4" />
        <p className="text-text-secondary text-sm">
          Data collection status, coverage calendar, and Parquet catalog
          will be available in Phase 2.
        </p>
      </GlassPanel>
    </motion.div>
  );
}
