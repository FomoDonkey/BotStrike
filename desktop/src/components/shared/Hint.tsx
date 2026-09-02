import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface HintProps {
  /** Tooltip text explaining how the value is derived. */
  title: string;
  children: ReactNode;
  className?: string;
}

/** Label with the dotted "has a tooltip" underline of the reference UI (Mark, Liq, ROE, Funding…). */
export function Hint({ title, children, className }: HintProps) {
  return (
    <span className={cn("hint", className)} title={title}>
      {children}
    </span>
  );
}
