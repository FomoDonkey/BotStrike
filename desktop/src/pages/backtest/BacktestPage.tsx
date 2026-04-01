import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { FlaskConical } from "lucide-react";

export function BacktestPage() {
  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <FlaskConical className="w-5 h-5 text-accent" /> Backtesting Lab
      </h1>
      <GlassPanel className="p-8 text-center">
        <FlaskConical className="w-12 h-12 text-accent/30 mx-auto mb-4" />
        <p className="text-text-secondary text-sm">
          Configure and run backtests against historical data.
        </p>
        <p className="text-text-muted text-xs mt-2">
          Strategy selection, date range, parameter overrides, and results comparison
          will be available in Phase 2.
        </p>
      </GlassPanel>
    </motion.div>
  );
}
