import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { MoreHorizontal, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { MOBILE_MORE, MOBILE_PRIMARY } from "./navItems";

/** Fixed bottom tab bar (< lg): Trade · Portfolio · Strategies · Risk · More. */
export function MobileTabBar({ className }: { className?: string }) {
  const location = useLocation();
  // The sheet is "open for this path": navigating anywhere closes it without an effect.
  const [openPath, setOpenPath] = useState<string | null>(null);
  const more = openPath === location.pathname;
  const setMore = (v: boolean | ((prev: boolean) => boolean)) => {
    const next = typeof v === "function" ? v(more) : v;
    setOpenPath(next ? location.pathname : null);
  };
  const moreActive = MOBILE_MORE.some((m) => location.pathname.startsWith(m.path));

  return (
    <>
      {more && (
        <div className="fixed inset-0 z-[60] lg:hidden" role="dialog" aria-label="More">
          <div className="absolute inset-0 bg-black/70" onClick={() => setMore(false)} aria-hidden />
          <div className="absolute left-0 right-0 bottom-14 bg-panel border-t border-hairline rounded-t-[10px]">
            <div className="flex items-center h-11 px-4 border-b border-hairline">
              <span className="text-[14px] font-semibold text-text">More</span>
              <button type="button" onClick={() => setMore(false)} aria-label="Close" className="ml-auto w-8 h-8 inline-flex items-center justify-center rounded-[6px] text-text hover:bg-hover"><X className="w-4 h-4" /></button>
            </div>
            <div className="grid grid-cols-2 gap-1 p-2">
              {MOBILE_MORE.map((item) => (
                <NavLink key={item.path} to={item.path} className={({ isActive }) => cn("flex items-center gap-3 h-11 px-3 rounded-lg text-[14px] font-medium", isActive ? "bg-active text-mint" : "text-text hover:bg-hover")}>
                  <item.icon className="w-4.5 h-4.5" /> {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        </div>
      )}
      <nav className={cn("fixed left-0 right-0 bottom-0 z-[65] h-14 bg-bg border-t border-hairline grid grid-cols-5 select-none", className)} aria-label="Primary">
        {MOBILE_PRIMARY.map((item) => (
          <NavLink key={item.path} to={item.path} className={({ isActive }) => cn("flex flex-col items-center justify-center gap-0.5 text-[11px] font-medium", isActive ? "text-mint" : "text-text")}>
            <item.icon className="w-5 h-5" />
            {item.label}
          </NavLink>
        ))}
        <button type="button" onClick={() => setMore((m) => !m)} className={cn("flex flex-col items-center justify-center gap-0.5 text-[11px] font-medium", more || moreActive ? "text-mint" : "text-text")}>
          <MoreHorizontal className="w-5 h-5" />
          More
        </button>
      </nav>
    </>
  );
}
