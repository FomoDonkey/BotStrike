import { cn } from "@/lib/utils";
import { motion, type HTMLMotionProps } from "framer-motion";

interface GlassPanelProps extends HTMLMotionProps<"div"> {
  /** Highlight the panel with the accent hairline (active/alert state). */
  glow?: boolean;
  noBorder?: boolean;
}

/**
 * Hairline panel (v2.15 visual system): solid surface, 1 px border, no blur, no shadow.
 * The name is kept so the rest of the app did not need to change.
 */
export function GlassPanel({ className, glow, noBorder, children, ...props }: GlassPanelProps) {
  return (
    <motion.div
      className={cn(
        "rounded-lg bg-bg-surface",
        !noBorder && (glow ? "border border-accent/50" : "border border-hairline"),
        className
      )}
      {...props}
    >
      {children}
    </motion.div>
  );
}
