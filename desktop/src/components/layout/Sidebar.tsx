import { NavLink, useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  CandlestickChart,
  TrendingUp,
  Waves,
  Brain,
  Shield,
  FlaskConical,
  Database,
  Settings,
  Monitor,
  ChevronLeft,
  ChevronRight,
  Zap,
} from "lucide-react";
import { useState, useEffect } from "react";

const NAV_ITEMS = [
  { path: "/", icon: LayoutDashboard, label: "Dashboard", shortcut: "1" },
  { path: "/trading", icon: CandlestickChart, label: "Live Trading", shortcut: "2" },
  { path: "/performance", icon: TrendingUp, label: "Performance", shortcut: "3" },
  { path: "/orderflow", icon: Waves, label: "Order Flow", shortcut: "4" },
  { path: "/strategies", icon: Brain, label: "Strategies", shortcut: "5" },
  { path: "/risk", icon: Shield, label: "Risk Monitor", shortcut: "6" },
  { path: "/backtest", icon: FlaskConical, label: "Backtesting", shortcut: "7" },
  { path: "/data", icon: Database, label: "Market Data", shortcut: "8" },
  { path: "/settings", icon: Settings, label: "Settings", shortcut: "9" },
  { path: "/system", icon: Monitor, label: "System", shortcut: "0" },
];

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();

  // Wire keyboard shortcuts (Alt+1..0 to navigate)
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Only with Alt key, and not when typing in an input/textarea
      if (!e.altKey) return;
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) return;

      const item = NAV_ITEMS.find((n) => n.shortcut === e.key);
      if (item) {
        e.preventDefault();
        navigate(item.path);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);

  return (
    <aside
      className={cn(
        "relative flex flex-col h-full bg-bg-surface/50 backdrop-blur-xl border-r border-white/5 transition-all duration-300",
        collapsed ? "w-16" : "w-56"
      )}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 h-14 border-b border-white/5">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-accent/10">
          <Zap className="w-4 h-4 text-accent" />
        </div>
        {!collapsed && (
          <span className="text-sm font-bold tracking-wide text-text-primary">
            BOT<span className="text-accent">STRIKE</span>
          </span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-3 px-2 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              cn(
                "group flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition-all duration-150",
                isActive
                  ? "bg-accent/10 text-accent shadow-[0_0_12px_rgba(0,212,170,0.08)]"
                  : "text-text-secondary hover:text-text-primary hover:bg-white/[0.03]"
              )
            }
          >
            <item.icon
              className={cn(
                "w-4.5 h-4.5 shrink-0 transition-transform duration-150 group-hover:scale-110",
              )}
            />
            {!collapsed && (
              <span className="flex-1 truncate">{item.label}</span>
            )}
            {!collapsed && (
              <kbd className="hidden group-hover:inline text-[10px] text-text-muted bg-white/5 rounded px-1.5 py-0.5 font-mono">
                Alt+{item.shortcut}
              </kbd>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center justify-center h-10 border-t border-white/5 text-text-muted hover:text-text-secondary transition-colors"
      >
        {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
      </button>
    </aside>
  );
}
