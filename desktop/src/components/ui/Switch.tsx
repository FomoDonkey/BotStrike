import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  busy?: boolean;
  label?: string;
  size?: "sm" | "md";
  className?: string;
}

/** Mint when ON, #3A3A3A track when OFF, white knob (spec §1 Buttons). */
export function Switch({ checked, onChange, disabled, busy, label, size = "md", className }: SwitchProps) {
  const w = size === "sm" ? "w-8 h-[18px]" : "w-10 h-[22px]";
  const knob = size === "sm" ? "w-3.5 h-3.5 top-[2px]" : "w-[18px] h-[18px] top-[2px]";
  const on = size === "sm" ? "left-[16px]" : "left-[20px]";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled || busy}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative shrink-0 rounded-full transition-colors duration-150 disabled:opacity-50",
        w,
        checked ? "bg-mint" : "bg-[#3A3A3A]",
        className,
      )}
    >
      {busy ? (
        <Loader2 className={cn("absolute animate-spin text-bg", size === "sm" ? "w-3 h-3 top-[3px] left-[10px]" : "w-3.5 h-3.5 top-[4px] left-[13px]")} />
      ) : (
        <span className={cn("absolute rounded-full bg-white transition-all duration-150 shadow-none", knob, checked ? on : "left-[2px]")} />
      )}
    </button>
  );
}

/** Label · help · switch row used by the settings popover and settings pages. */
export function SwitchRow({ label, help, checked, onChange, disabled, className }: { label: string; help?: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; className?: string }) {
  return (
    <div className={cn("flex items-center justify-between gap-4 h-9", className)}>
      <div className="min-w-0">
        <p className="text-[13px] font-medium text-text truncate">{label}</p>
        {help && <p className="text-[11.5px] text-text-2 truncate">{help}</p>}
      </div>
      <Switch checked={checked} onChange={onChange} disabled={disabled} label={label} size="sm" />
    </div>
  );
}
