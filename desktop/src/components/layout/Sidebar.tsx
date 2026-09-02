import { NavLink, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
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
  X,
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

interface SidebarProps {
  /** Mobile drawer state (ignored ≥ lg where the sidebar is always a column). */
  open: boolean;
  onClose: () => void;
}

function Logo({ collapsed }: { collapsed: boolean }) {
  return (
    <>
      <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-accent/10 shrink-0">
        <Zap className="w-4 h-4 text-accent" />
      </div>
      {!collapsed && (
        <span className="text-sm font-bold tracking-wide text-text-primary">
          BOT<span className="text-accent">STRIKE</span>
        </span>
      )}
    </>
  );
}

function NavList({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  return (
    <nav className="flex-1 py-3 px-2 space-y-0.5 overflow-y-auto">
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "group flex items-center gap-3 px-3 py-2 rounded-md text-[13px] transition-colors duration-150",
              isActive
                ? "bg-accent/10 text-accent"
                : "text-text-secondary hover:text-text-primary hover:bg-white/[0.04]"
            )
          }
        >
          <item.icon className="w-4.5 h-4.5 shrink-0 transition-transform duration-150 group-hover:scale-110" />
          {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
          {!collapsed && (
            <kbd className="hidden group-hover:inline text-[10px] text-text-muted bg-white/5 rounded px-1.5 py-0.5 font-mono">
              Alt+{item.shortcut}
            </kbd>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

export function Sidebar({ open, onClose }: SidebarProps) {
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
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate, onClose]);

  // Escape closes the mobile drawer
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      {/* Desktop column (≥ lg) */}
      <aside
        className={cn(
          "relative hidden lg:flex flex-col h-full shrink-0 bg-bg-surface border-r border-hairline transition-all duration-300",
          collapsed ? "w-16" : "w-56"
        )}
      >
        <div className="flex items-center gap-3 px-4 h-11 border-b border-hairline">
          <Logo collapsed={collapsed} />
        </div>
        <NavList collapsed={collapsed} />
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center justify-center h-9 border-t border-hairline text-text-muted hover:text-text-secondary transition-colors"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
        </button>
      </aside>

      {/* Mobile drawer (< lg) */}
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              key="backdrop"
              className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              onClick={onClose}
              aria-hidden
            />
            <motion.aside
              key="drawer"
              className="fixed inset-y-0 left-0 z-50 flex w-64 max-w-[85vw] flex-col bg-bg-surface border-r border-hairline lg:hidden"
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: "tween", duration: 0.2, ease: "easeOut" }}
              role="dialog"
              aria-label="Navigation"
            >
              <div className="flex items-center gap-3 px-4 h-11 border-b border-hairline">
                <Logo collapsed={false} />
                <button
                  onClick={onClose}
                  className="ml-auto p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-white/5"
                  aria-label="Close navigation"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <NavList collapsed={false} onNavigate={onClose} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
