import { useAlertStore } from "@/stores/alertStore";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, AlertCircle, Info, X } from "lucide-react";
import { cn } from "@/lib/utils";

const icons = {
  info: Info,
  warning: AlertTriangle,
  critical: AlertCircle,
};

const colors = {
  info: { bg: "bg-info/10", border: "border-info/20", text: "text-info", glow: "" },
  warning: { bg: "bg-warning/10", border: "border-warning/20", text: "text-warning", glow: "" },
  critical: { bg: "bg-loss/10", border: "border-loss/20", text: "text-loss", glow: "shadow-[0_0_20px_rgba(255,71,87,0.15)]" },
};

export function AlertToast() {
  const alerts = useAlertStore((s) => s.alerts.filter((a) => !a.dismissed));
  const dismiss = useAlertStore((s) => s.dismissAlert);

  return (
    <div className="fixed top-14 right-4 z-40 flex flex-col gap-2 w-80">
      <AnimatePresence>
        {alerts.slice(-5).map((alert) => {
          const Icon = icons[alert.level];
          const color = colors[alert.level];
          return (
            <motion.div
              key={alert.id}
              initial={{ opacity: 0, x: 100, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 50, scale: 0.95 }}
              transition={{ duration: 0.25, ease: "easeOut" as const }}
              className={cn(
                "rounded-xl border p-3 backdrop-blur-xl",
                color.bg, color.border, color.glow
              )}
            >
              <div className="flex items-start gap-2">
                <Icon className={cn("w-4 h-4 mt-0.5 shrink-0", color.text)} />
                <div className="flex-1 min-w-0">
                  <p className={cn("text-xs font-semibold", color.text)}>{alert.title}</p>
                  <p className="text-[11px] text-text-secondary mt-0.5 leading-relaxed">{alert.message}</p>
                </div>
                <button
                  onClick={() => dismiss(alert.id)}
                  className="text-text-muted hover:text-text-secondary shrink-0"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
