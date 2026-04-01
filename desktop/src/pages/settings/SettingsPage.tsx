import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { Settings } from "lucide-react";

export function SettingsPage() {
  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Settings className="w-5 h-5 text-accent" /> Settings
      </h1>
      <GlassPanel className="p-8 text-center">
        <Settings className="w-12 h-12 text-accent/30 mx-auto mb-4" />
        <p className="text-text-secondary text-sm">
          API keys, capital, risk parameters, execution, and notification settings
          will be available in Phase 2.
        </p>
      </GlassPanel>
    </motion.div>
  );
}
