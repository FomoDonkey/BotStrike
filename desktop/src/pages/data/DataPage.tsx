import { useEffect, useState } from "react";
import { useShallow } from "zustand/shallow";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { useSystemStore } from "@/stores/systemStore";
import { useMarketStore } from "@/stores/marketStore";
import { PulsingDot } from "@/components/shared/PulsingDot";
import { api } from "@/lib/api";
import { Database, Wifi, HardDrive, Calendar, BarChart3 } from "lucide-react";
import { cn } from "@/lib/utils";

interface DatasetInfo {
  symbol: string;
  type: string;
  records: number;
  size_mb: number;
  date_range: string;
}

export function DataPage() {
  const system = useSystemStore();
  const prices = useMarketStore(useShallow((s) => s.prices));
  const [catalog, setCatalog] = useState<DatasetInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.dataCatalog().then((data) => {
      if (data?.datasets) setCatalog(data.datasets);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const tickCounts = Object.entries(prices).map(([sym, price]) => ({
    symbol: sym,
    count: 0,
    tps: 0,
    lastPrice: price,
  }));

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Database className="w-5 h-5 text-accent" /> Market Data
      </h1>

      {/* Live Feeds */}
      <GlassPanel className="p-5">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
          <Wifi className="w-3 h-3" /> Live Data Feeds
        </h3>
        <div className="grid grid-cols-3 gap-4">
          {tickCounts.length > 0 ? tickCounts.map((t) => (
            <div key={t.symbol} className="p-3 rounded-lg bg-white/[0.02] flex items-center justify-between">
              <div>
                <p className="font-mono text-sm font-semibold text-text-primary">{t.symbol}</p>
                <p className="text-[10px] text-text-muted">{t.count} ticks buffered</p>
              </div>
              <div className="text-right">
                <p className="font-mono text-sm text-accent">${t.lastPrice.toLocaleString("en-US", { minimumFractionDigits: 2 })}</p>
                <p className="text-[10px] text-text-muted">{t.tps} tps</p>
              </div>
            </div>
          )) : (
            <div className="col-span-3 text-center py-4 text-text-muted text-sm">
              <PulsingDot active={system.wsConnected} className="mx-auto mb-2" />
              {system.wsConnected ? "Receiving data..." : "No data feeds — bridge not connected"}
            </div>
          )}
        </div>
      </GlassPanel>

      {/* Connection Status */}
      <div className="grid grid-cols-2 gap-4">
        <GlassPanel className="p-5">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
            <BarChart3 className="w-3 h-3" /> Stream Status
          </h3>
          <div className="space-y-2">
            {["market", "trading", "micro", "risk", "system"].map((ch) => (
              <div key={ch} className="flex items-center justify-between text-xs">
                <span className="text-text-secondary font-mono">ws/{ch}</span>
                <span className={cn(
                  "px-2 py-0.5 rounded text-[10px] font-mono",
                  system.bridgeConnected ? "bg-profit/10 text-profit" : "bg-white/5 text-text-muted"
                )}>
                  {system.bridgeConnected ? "SUBSCRIBED" : "PENDING"}
                </span>
              </div>
            ))}
          </div>
        </GlassPanel>

        {/* Data Catalog */}
        <GlassPanel className="p-5">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
            <HardDrive className="w-3 h-3" /> Local Data Catalog
          </h3>
          {loading ? (
            <p className="text-text-muted text-xs">Loading catalog...</p>
          ) : catalog.length > 0 ? (
            <div className="space-y-2">
              {catalog.slice(0, 10).map((d, i) => (
                <div key={i} className="flex items-center justify-between text-xs">
                  <div>
                    <span className="font-mono text-text-primary">{d.symbol}</span>
                    <span className="text-text-muted ml-2">{d.type}</span>
                  </div>
                  <span className="text-text-muted font-mono">{d.records?.toLocaleString()} rows</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-4">
              <Calendar className="w-8 h-8 text-text-muted/30 mx-auto mb-2" />
              <p className="text-text-muted text-xs">
                No catalog data. Run the data collector to build local datasets.
              </p>
            </div>
          )}
        </GlassPanel>
      </div>
    </motion.div>
  );
}
