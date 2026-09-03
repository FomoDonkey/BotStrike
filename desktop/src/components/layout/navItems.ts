import type { ComponentType } from "react";
import { CandlestickChart, PieChart, Brain, Shield, FlaskConical, Database, Monitor, Settings } from "lucide-react";

export interface NavItem {
  path: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  shortcut: string;
}

/** Top navigation (spec §2): Trade · Portfolio · Strategies · Risk · Backtest · Data · System. */
export const NAV_ITEMS: readonly NavItem[] = [
  { path: "/trading", label: "Trade", icon: CandlestickChart, shortcut: "1" },
  { path: "/portfolio", label: "Portfolio", icon: PieChart, shortcut: "2" },
  { path: "/strategies", label: "Strategies", icon: Brain, shortcut: "3" },
  { path: "/risk", label: "Risk", icon: Shield, shortcut: "4" },
  { path: "/backtest", label: "Backtest", icon: FlaskConical, shortcut: "5" },
  { path: "/data", label: "Data", icon: Database, shortcut: "6" },
  { path: "/system", label: "System", icon: Monitor, shortcut: "7" },
];

export const SETTINGS_ITEM: NavItem = { path: "/settings", label: "Settings", icon: Settings, shortcut: "9" };

/** Mobile bottom tab bar: the first four + "More". */
export const MOBILE_PRIMARY = NAV_ITEMS.slice(0, 4);
export const MOBILE_MORE = [...NAV_ITEMS.slice(4), SETTINGS_ITEM];
