import { motion } from "framer-motion";
import { useShallow } from "zustand/shallow";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { useMicroStore } from "@/stores/microStore";
import { cn } from "@/lib/utils";
import { Waves, Activity, TrendingUp, Shield } from "lucide-react";

export function OrderFlowPage() {
  const snapshots = useMicroStore(useShallow((s) => s.snapshots));
  const history = useMicroStore(useShallow((s) => s.history));
  const entries = Object.entries(snapshots);

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
    >
      <h1 className="text-lg font-semibold text-text-primary">Order Flow Analysis</h1>

      {entries.length === 0 ? (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted">Waiting for microstructure data from bridge...</p>
        </GlassPanel>
      ) : (
        entries.map(([sym, data]) => (
          <div key={sym} className="space-y-3">
            <h2 className="text-sm font-mono text-text-secondary">{sym}</h2>
            <div className="grid grid-cols-4 gap-3">
              {/* VPIN */}
              <GlassPanel className="p-4" glow={data.vpin?.is_toxic}>
                <div className="flex items-center gap-2 text-xs text-text-secondary uppercase mb-3">
                  <Waves className="w-3 h-3 text-[#E84393]" /> VPIN
                </div>
                <div className="text-3xl font-mono font-bold text-text-primary">
                  {data.vpin ? `${(data.vpin.vpin * 100).toFixed(0)}%` : "---"}
                </div>
                <div className="mt-2 w-full h-2 rounded-full bg-white/5 overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{
                      width: `${(data.vpin?.vpin || 0) * 100}%`,
                      background: data.vpin?.is_toxic
                        ? "linear-gradient(90deg, #E84393, #FF4757)"
                        : "linear-gradient(90deg, #E84393, #E84393aa)",
                    }}
                  />
                </div>
                <p className="text-[10px] text-text-muted mt-1">
                  CDF: {data.vpin?.cdf?.toFixed(2) ?? "---"}
                  {data.vpin?.is_toxic && <span className="text-loss ml-2">TOXIC FLOW</span>}
                </p>
              </GlassPanel>

              {/* Hawkes */}
              <GlassPanel className="p-4" glow={data.hawkes?.is_spike}>
                <div className="flex items-center gap-2 text-xs text-text-secondary uppercase mb-3">
                  <Activity className="w-3 h-3 text-[#FF7675]" /> Hawkes Intensity
                </div>
                <div className={cn(
                  "text-3xl font-mono font-bold",
                  data.hawkes?.is_spike ? "text-loss" : "text-text-primary"
                )}>
                  {data.hawkes ? `${data.hawkes.multiplier.toFixed(1)}x` : "---"}
                </div>
                <p className="text-[10px] text-text-muted mt-2">
                  Intensity: {data.hawkes?.intensity?.toFixed(2) ?? "---"} events/s
                  {data.hawkes?.is_spike && <span className="text-loss ml-2">SPIKE</span>}
                </p>
              </GlassPanel>

              {/* Kyle Lambda */}
              <GlassPanel className="p-4">
                <div className="flex items-center gap-2 text-xs text-text-secondary uppercase mb-3">
                  <TrendingUp className="w-3 h-3 text-info" /> Kyle Lambda
                </div>
                <div className="text-3xl font-mono font-bold text-text-primary">
                  {data.kyle_lambda ? `${data.kyle_lambda.lambda_bps.toFixed(1)}` : "---"}
                  <span className="text-sm text-text-muted ml-1">bps</span>
                </div>
                <p className="text-[10px] text-text-muted mt-2">
                  Impact Stress: {data.kyle_lambda?.impact_stress?.toFixed(2) ?? "---"}
                </p>
                <p className="text-[10px] text-text-muted">
                  Adverse Sel: {data.kyle_lambda?.adverse_selection_bps?.toFixed(1) ?? "---"} bps
                </p>
              </GlassPanel>

              {/* A-S Spread */}
              <GlassPanel className="p-4">
                <div className="flex items-center gap-2 text-xs text-text-secondary uppercase mb-3">
                  <Shield className="w-3 h-3 text-[#00CEC9]" /> A-S Spread
                </div>
                <div className="text-3xl font-mono font-bold text-text-primary">
                  {data.as_spread ? `${data.as_spread.bid_spread_bps.toFixed(1)}` : "---"}
                  <span className="text-sm text-text-muted ml-1">bps</span>
                </div>
                <p className="text-[10px] text-text-muted mt-2">
                  Bid: {data.as_spread?.bid_spread_bps?.toFixed(1) ?? "---"} / Ask: {data.as_spread?.ask_spread_bps?.toFixed(1) ?? "---"}
                </p>
                <p className="text-[10px] text-text-muted">
                  Reservation: ${data.as_spread?.reservation_price?.toFixed(2) ?? "---"}
                </p>
              </GlassPanel>
            </div>

            {/* Risk Score */}
            <GlassPanel className="p-4">
              <div className="flex items-center justify-between">
                <span className="text-xs text-text-secondary uppercase">Composite Risk Score</span>
                <span className={cn(
                  "text-xl font-mono font-bold",
                  data.risk_score > 0.7 ? "text-loss" : data.risk_score > 0.4 ? "text-warning" : "text-profit"
                )}>
                  {data.risk_score?.toFixed(3) ?? "---"}
                </span>
              </div>
              <div className="mt-2 w-full h-2 rounded-full bg-white/5 overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${(data.risk_score || 0) * 100}%`,
                    background: data.risk_score > 0.7
                      ? "linear-gradient(90deg, #FFA502, #FF4757)"
                      : data.risk_score > 0.4
                        ? "linear-gradient(90deg, #00D4AA, #FFA502)"
                        : "linear-gradient(90deg, #00D4AA, #00D4AAaa)",
                  }}
                />
              </div>
            </GlassPanel>
          </div>
        ))
      )}
    </motion.div>
  );
}
