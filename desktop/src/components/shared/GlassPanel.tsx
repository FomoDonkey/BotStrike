import { cn } from "@/lib/utils";
import { motion, type HTMLMotionProps } from "framer-motion";

interface GlassPanelProps extends HTMLMotionProps<"div"> {
  glow?: boolean;
  noBorder?: boolean;
}

export function GlassPanel({ className, glow, noBorder, children, ...props }: GlassPanelProps) {
  return (
    <motion.div
      className={cn(
        "rounded-2xl bg-bg-surface/70 backdrop-blur-xl",
        !noBorder && "border border-white/5",
        glow && "shadow-[0_0_0_1px_rgba(0,212,170,0.05),0_0_20px_rgba(0,212,170,0.05)]",
        !glow && "shadow-[0_4px_24px_rgba(0,0,0,0.4)]",
        className
      )}
      {...props}
    >
      {children}
    </motion.div>
  );
}
