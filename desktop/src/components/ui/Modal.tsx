import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  width?: string;
  className?: string;
  /** No title bar (market picker draws its own) */
  bare?: boolean;
}

/** Centered modal on a dimmed (not blurred) backdrop. Escape closes. Surface `--panel`, 10 px radius. */
export function Modal({ open, onClose, title, children, width = "max-w-lg", className, bare }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[80] flex items-start justify-center px-3 pt-[8vh] pb-6 bg-black/70" onMouseDown={onClose} role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
        className={cn("w-full rounded-[10px] border border-hairline-strong bg-panel flex flex-col min-h-0 max-h-[84vh] overflow-hidden", width, className)}
      >
        {!bare && (
          <div className="flex items-center h-11 px-4 border-b border-hairline shrink-0">
            <span className="text-[14px] font-semibold text-text truncate">{title}</span>
            <button type="button" onClick={onClose} aria-label="Close" className="ml-auto w-7 h-7 inline-flex items-center justify-center rounded-[6px] text-text hover:bg-hover">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

/** Confirm dialog with a primary / danger action. */
export function ConfirmDialog({ open, onClose, onConfirm, title, body, confirmLabel = "Confirm", danger, busy }: { open: boolean; onClose: () => void; onConfirm: () => void; title: string; body: ReactNode; confirmLabel?: string; danger?: boolean; busy?: boolean }) {
  return (
    <Modal open={open} onClose={onClose} title={title} width="max-w-md">
      <div className="px-4 py-4 text-[13px] text-text-2 leading-relaxed">{body}</div>
      <div className="flex justify-end gap-2 px-4 py-3 border-t border-hairline">
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} loading={busy}>{confirmLabel}</Button>
      </div>
    </Modal>
  );
}
