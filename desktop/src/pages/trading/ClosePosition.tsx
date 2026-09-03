import { useState } from "react";
import { Lock, XCircle } from "lucide-react";
import type { PositionData } from "@/lib/api";
import { useClosePosition } from "@/hooks/useClosePosition";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Modal";
import { SideChip } from "@/components/ui/Chip";
import { cn, formatMoney, formatPrice, formatSignedMoney, formatSize } from "@/lib/utils";
import { positionNotional } from "@/lib/market";

/** The warning the operator must read before overriding the engine's own exit (contract §2). */
export const MANUAL_CLOSE_COPY =
  "The bot would normally exit through its ladder. Closing now overrides that. If the signal is still on, " +
  "tomorrow's run may re-enter.";

interface ClosePositionButtonProps {
  position: PositionData;
  size?: "xs" | "sm" | "md";
  className?: string;
  /** Full-width secondary button (Bot column) instead of the compact table one */
  block?: boolean;
}

/** Close button + confirmation dialog naming the symbol, size, notional and unrealized PnL. */
export function ClosePositionButton({ position, size = "xs", className, block }: ClosePositionButtonProps) {
  const { canClose, disabledReason, busy, close } = useClosePosition();
  const [open, setOpen] = useState(false);
  const p = position;
  const running = busy === p.symbol;

  return (
    <>
      <Button
        variant={block ? "secondary" : "ghost"}
        size={size}
        className={cn(!block && "text-rose hover:bg-rose-soft", className)}
        disabled={!canClose}
        title={disabledReason ?? `Close ${p.symbol} now at the current price (paper only)`}
        loading={running}
        icon={block ? <XCircle className="w-3.5 h-3.5" /> : !canClose ? <Lock className="w-3 h-3" /> : undefined}
        onClick={() => setOpen(true)}
      >
        {/* A disabled button whose reason lives only in a tooltip is a button that looks broken:
            Edgar asked why it had stopped working (2026-09-04). Say it on the face of the control. */}
        {block ? (canClose ? "Close position (paper)" : "Close — needs the auth token") : canClose ? "Close" : "Locked"}
      </Button>
      <ConfirmDialog
        open={open}
        onClose={() => setOpen(false)}
        onConfirm={() => {
          setOpen(false);
          void close(p.symbol);
        }}
        title={`Close ${p.symbol} now?`}
        danger
        confirmLabel="Close position"
        busy={running}
        body={
          <div className="flex flex-col gap-3">
            <div className="rounded-[8px] bg-panel-2 px-3 py-2.5">
              <div className="flex items-center gap-2 mb-1.5">
                <SideChip side={p.side} size="xs" />
                <span className="text-[13px] font-semibold text-text">{p.symbol}</span>
                <span className="num ml-auto text-[12.5px] font-semibold text-text">@ {formatPrice(p.mark_price > 0 ? p.mark_price : p.entry_price)}</span>
              </div>
              <dl className="kv">
                <dt>Size</dt>
                <dd>{formatSize(p.size)}</dd>
                <dt>Notional</dt>
                <dd>{formatMoney(positionNotional(p))}</dd>
                <dt>Unrealized PnL</dt>
                <dd className={(p.unrealized_pnl ?? 0) > 0 ? "text-mint" : (p.unrealized_pnl ?? 0) < 0 ? "text-rose" : undefined}>
                  {formatSignedMoney(p.unrealized_pnl ?? 0)}
                </dd>
              </dl>
            </div>
            <p className="text-[13px] font-medium text-text leading-relaxed">{MANUAL_CLOSE_COPY}</p>
          </div>
        }
      />
    </>
  );
}
