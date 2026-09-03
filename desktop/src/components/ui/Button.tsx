import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "amber";
  size?: "xs" | "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
}

/**
 * Primary: mint fill, `#0A0A0A` text, 600, 8 px radius, hover brightens. Secondary: transparent,
 * `--hairline-strong` border, white text. Disabled controls carry the `disabled` attribute (audit-exempt).
 */
export function Button({ variant = "secondary", size = "md", loading, icon, className, children, disabled, ...props }: ButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      aria-disabled={disabled || loading ? true : undefined}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-semibold whitespace-nowrap transition-colors select-none",
        size === "xs" ? "h-6 px-2 text-[12px]" : size === "sm" ? "h-7 px-2.5 text-[12.5px]" : "h-8 px-3.5 text-[13px]",
        variant === "primary" && "bg-mint text-bg hover:brightness-[1.06]",
        variant === "secondary" && "border border-hairline-strong text-text bg-transparent hover:bg-hover",
        variant === "ghost" && "text-text hover:bg-hover",
        variant === "danger" && "bg-rose text-white hover:brightness-[1.06]",
        variant === "amber" && "bg-amber text-bg hover:brightness-[1.06]",
        "disabled:opacity-45 disabled:cursor-not-allowed disabled:hover:brightness-100 disabled:hover:bg-transparent",
        variant === "primary" && "disabled:hover:bg-mint",
        className,
      )}
      {...props}
    >
      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : icon}
      {children}
    </button>
  );
}

/** Square icon button (toolbar icons). */
export function IconButton({ className, active, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex items-center justify-center w-7 h-7 rounded-[6px] text-text transition-colors hover:bg-hover disabled:opacity-45",
        active && "bg-active",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
