import { useEffect } from "react";
import { X } from "lucide-react";
import { useUiStore } from "@/stores/uiStore";
import { ActivityFeed } from "@/components/ui/ActivityFeed";

/** Right-hand activity drawer opened from the footer "Activity" button. */
export function ActivityDrawer() {
  const open = useUiStore((s) => s.activityOpen);
  const setOpen = useUiStore((s) => s.setActivityOpen);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  if (!open) return null;
  return (
    <aside className="fixed right-0 top-14 bottom-14 lg:bottom-8 z-[55] w-[360px] max-w-[92vw] bg-panel border-l border-hairline flex flex-col" role="dialog" aria-label="Activity">
      <div className="flex items-center h-10 px-3 border-b border-hairline shrink-0">
        <span className="text-[13px] font-semibold text-text">Recent activity</span>
        <button type="button" onClick={() => setOpen(false)} aria-label="Close activity" className="ml-auto w-7 h-7 inline-flex items-center justify-center rounded-[6px] text-text hover:bg-hover">
          <X className="w-4 h-4" />
        </button>
      </div>
      <ActivityFeed limit={100} />
    </aside>
  );
}
