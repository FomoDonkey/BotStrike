import { create } from "zustand";

export type AlertLevel = "info" | "warning" | "critical";

export interface Alert {
  id: string;
  level: AlertLevel;
  title: string;
  message: string;
  timestamp: number;
  dismissed: boolean;
  sound?: "trade" | "profit" | "loss" | "alert" | "circuitBreaker";
}

export interface AlertRule {
  id: string;
  enabled: boolean;
  name: string;
  type: "price_above" | "price_below" | "vpin_above" | "drawdown_above" | "hawkes_spike";
  threshold: number;
  symbol?: string;
  level: AlertLevel;
  cooldownSec: number;
  lastTriggered: number;
}

// The drawdown rules are SHARES OF THE CONFIGURED LIMIT, not fixed percentages: at 5 % / 8 % of
// equity they would have fired every minute through an ordinary drawdown of the aggressive
// profile, whose limit is 39 % and whose measured worst drawdown is 30 % (2026-09-08).
// checkAndTrigger scales them by `max_drawdown_pct` when the risk update carries it.
const DEFAULT_RULES: AlertRule[] = [
  { id: "dd_warn", enabled: true, name: "Drawdown at half the limit", type: "drawdown_above", threshold: 0.5, level: "warning", cooldownSec: 3600, lastTriggered: 0 },
  { id: "dd_crit", enabled: true, name: "Drawdown near the halt", type: "drawdown_above", threshold: 0.8, level: "critical", cooldownSec: 1800, lastTriggered: 0 },
  { id: "vpin_toxic", enabled: true, name: "VPIN Toxic", type: "vpin_above", threshold: 0.8, level: "warning", cooldownSec: 120, lastTriggered: 0 },
  { id: "hawkes", enabled: true, name: "Hawkes Spike", type: "hawkes_spike", threshold: 4.0, level: "info", cooldownSec: 60, lastTriggered: 0 },
];

interface AlertState {
  alerts: Alert[];
  rules: AlertRule[];
  soundEnabled: boolean;

  addAlert: (alert: Omit<Alert, "id" | "timestamp" | "dismissed">) => void;
  dismissAlert: (id: string) => void;
  clearAll: () => void;
  toggleSound: () => void;
  updateRule: (id: string, updates: Partial<AlertRule>) => void;
  checkAndTrigger: (data: { drawdown_pct?: number; max_drawdown_pct?: number; vpin?: number; hawkes_mult?: number; price?: number; symbol?: string }) => void;
}

let _alertCounter = 0;

export const useAlertStore = create<AlertState>((set, get) => ({
  alerts: [],
  rules: DEFAULT_RULES,
  soundEnabled: true,

  addAlert: (alert) => {
    const id = `alert_${++_alertCounter}`;
    const newAlert: Alert = { ...alert, id, timestamp: Date.now() / 1000, dismissed: false };
    set((s) => ({ alerts: [...s.alerts.slice(-99), newAlert] }));

    // Auto-dismiss after 10s for info, 30s for warning
    if (alert.level !== "critical") {
      setTimeout(() => get().dismissAlert(id), alert.level === "info" ? 10000 : 30000);
    }
  },

  dismissAlert: (id) =>
    set((s) => ({ alerts: s.alerts.filter((a) => a.id !== id) })),

  clearAll: () => set({ alerts: [] }),

  toggleSound: () => set((s) => ({ soundEnabled: !s.soundEnabled })),

  updateRule: (id, updates) =>
    set((s) => ({
      rules: s.rules.map((r) => r.id === id ? { ...r, ...updates } : r),
    })),

  checkAndTrigger: (data) => {
    const { rules, addAlert } = get();
    const now = Date.now() / 1000;

    // Collect triggered rule IDs and their alerts, then batch-update cooldowns
    const triggered: { ruleId: string; level: AlertLevel; title: string; message: string }[] = [];

    for (const rule of rules) {
      if (!rule.enabled) continue;
      if (now - rule.lastTriggered < rule.cooldownSec) continue;

      let match = false;
      let message = "";

      switch (rule.type) {
        case "drawdown_above": {
          // threshold = share of the configured max-drawdown limit; without a limit on the wire
          // there is nothing to measure against, so the rule stays quiet
          const limit = data.max_drawdown_pct;
          if (data.drawdown_pct !== undefined && typeof limit === "number" && limit > 0 && data.drawdown_pct >= rule.threshold * limit) {
            match = true;
            message = `Drawdown ${(data.drawdown_pct * 100).toFixed(1)}% of equity — ${(data.drawdown_pct / limit * 100).toFixed(0)}% of the ${(limit * 100).toFixed(0)}% limit that halts the bot`;
          }
          break;
        }
        case "vpin_above":
          if (data.vpin !== undefined && data.vpin >= rule.threshold) {
            match = true;
            message = `VPIN at ${(data.vpin * 100).toFixed(0)}% — toxic flow detected`;
          }
          break;
        case "hawkes_spike":
          if (data.hawkes_mult !== undefined && data.hawkes_mult >= rule.threshold) {
            match = true;
            message = `Hawkes intensity spike: ${data.hawkes_mult.toFixed(1)}x baseline`;
          }
          break;
        case "price_above":
          if (data.price !== undefined && data.price >= rule.threshold) {
            match = true;
            message = `${data.symbol || "BTC"} price above $${rule.threshold.toLocaleString()}`;
          }
          break;
        case "price_below":
          if (data.price !== undefined && data.price <= rule.threshold) {
            match = true;
            message = `${data.symbol || "BTC"} price below $${rule.threshold.toLocaleString()}`;
          }
          break;
      }

      if (match) {
        triggered.push({ ruleId: rule.id, level: rule.level, title: rule.name, message });
      }
    }

    // Batch update all triggered rule cooldowns in a single set() call
    if (triggered.length > 0) {
      const triggeredIds = new Set(triggered.map((t) => t.ruleId));
      set((s) => ({
        rules: s.rules.map((r) => triggeredIds.has(r.id) ? { ...r, lastTriggered: now } : r),
      }));
      for (const t of triggered) {
        addAlert({
          level: t.level,
          title: t.title,
          message: t.message,
          sound: t.level === "critical" ? "alert" : undefined,
        });
      }
    }
  },
}));
