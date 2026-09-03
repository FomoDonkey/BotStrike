import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface PopoverProps {
  /** The trigger; receives open state */
  trigger: (open: boolean) => ReactNode;
  children: ReactNode | ((close: () => void) => ReactNode);
  align?: "left" | "right";
  className?: string;
  /** Panel width class */
  width?: string;
  /** Called with the new state */
  onOpenChange?: (open: boolean) => void;
}

/** Anchored popover / dropdown: closes on outside click and Escape. Surface `--panel-2`, 10 px radius. */
export function Popover({ trigger, children, align = "left", className, width = "w-64", onOpenChange }: PopoverProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        onOpenChange?.(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        onOpenChange?.(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onOpenChange]);

  const close = () => {
    setOpen(false);
    onOpenChange?.(false);
  };

  return (
    <div ref={ref} className={cn("relative", className)}>
      <div
        onClick={() => {
          const next = !open;
          setOpen(next);
          onOpenChange?.(next);
        }}
      >
        {trigger(open)}
      </div>
      {open && (
        <div
          role="menu"
          className={cn(
            "absolute top-full mt-1.5 z-50 rounded-[10px] border border-hairline-strong bg-panel-2 py-1 max-h-[70vh] overflow-y-auto",
            align === "right" ? "right-0" : "left-0",
            width,
          )}
        >
          {typeof children === "function" ? children(close) : children}
        </div>
      )}
    </div>
  );
}

/** Menu row inside a Popover. */
export function MenuItem({ children, onClick, disabled, active, title, className, tone }: { children: ReactNode; onClick?: () => void; disabled?: boolean; active?: boolean; title?: string; className?: string; tone?: "rose" | "mint" }) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      aria-disabled={disabled ? true : undefined}
      title={title}
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-2 px-3 h-8 text-[13px] font-medium text-left whitespace-nowrap transition-colors",
        active ? "bg-active text-text" : "text-text hover:bg-hover",
        tone === "rose" && "text-rose",
        tone === "mint" && "text-mint",
        "disabled:opacity-45 disabled:cursor-not-allowed disabled:hover:bg-transparent",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <div className="px-3 h-7 flex items-center text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2">{children}</div>;
}

export function MenuDivider() {
  return <div className="my-1 border-t border-hairline" />;
}

/** Small "Label ▾" trigger used by filters and chart menus. */
export function DropdownTrigger({ label, open, className, size = "sm" }: { label: ReactNode; open: boolean; className?: string; size?: "xs" | "sm" }) {
  return (
    <button
      type="button"
      aria-haspopup="menu"
      aria-expanded={open}
      className={cn(
        "inline-flex items-center gap-1 rounded-[6px] font-medium text-text whitespace-nowrap transition-colors hover:bg-hover",
        size === "xs" ? "h-6 px-1.5 text-[12px]" : "h-7 px-2 text-[12.5px]",
        open && "bg-active",
        className,
      )}
    >
      {label}
      <ChevronDown className={cn("w-3.5 h-3.5 transition-transform", open && "rotate-180")} />
    </button>
  );
}
