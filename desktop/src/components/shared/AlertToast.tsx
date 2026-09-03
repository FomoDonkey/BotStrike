import { useMemo } from "react";
import { useAlertStore } from "@/stores/alertStore";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, AlertCircle, Info, X } from "lucide-react";
import { cn } from "@/lib/utils";

const icons = { info: Info, warning: AlertTriangle, critical: AlertCircle };

const colors = {
  info: { border: "border-mint/50", text: "text-mint" },
  warning: { border: "border-amber/60", text: "text-amber" },
  critical: { border: "border-rose/70", text: "text-rose" },
};

/** Toasts: solid `--panel-2` surface (no blur), white message, tone-coloured title + hairline. */
export function AlertToast() {
  const allAlerts = useAlertStore((s) => s.alerts);
  const dismiss = useAlertStore((s) => s.dismissAlert);
  const alerts = useMemo(() => allAlerts.filter((a) => !a.dismissed), [allAlerts]);

  return (
    <div className="fixed top-16 right-3 z-[90] flex flex-col gap-2 w-80 max-w-[calc(100vw-24px)]">
      <AnimatePresence>
        {alerts.slice(-5).map((alert) => {
          const Icon = icons[alert.level];
          const color = colors[alert.level];
          return (
            <motion.div
              key={alert.id}
              initial={{ opacity: 0, x: 40 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 40 }}
              transition={{ duration: 0.18, ease: "easeOut" as const }}
              className={cn("rounded-[10px] border bg-panel-2 p-3", color.border)}
            >
              <div className="flex items-start gap-2">
                <Icon className={cn("w-4 h-4 mt-0.5 shrink-0", color.text)} />
                <div className="flex-1 min-w-0">
                  <p className={cn("text-[13px] font-semibold", color.text)}>{alert.title}</p>
                  <p className="text-[12.5px] font-medium text-text mt-0.5 leading-snug break-words">{alert.message}</p>
                </div>
                <button type="button" onClick={() => dismiss(alert.id)} aria-label="Dismiss" className="text-text hover:bg-hover rounded-[6px] p-1 shrink-0">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
