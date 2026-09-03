import { Link } from "react-router-dom";
import { Settings, RotateCcw } from "lucide-react";
import { useShallow } from "zustand/shallow";
import { useUiStore } from "@/stores/uiStore";
import { useAlertStore } from "@/stores/alertStore";
import { Popover, MenuLabel, MenuDivider } from "@/components/ui/Popover";
import { SwitchRow } from "@/components/ui/Switch";
import { IconButton } from "@/components/ui/Button";

/** Gear popover (spec §3.7): Layout switches (Trade page) + Trading / Display switches, Reset all. */
export function SettingsPopover() {
  const { layout, display, setLayout, setDisplay, resetAll } = useUiStore(useShallow((s) => ({
    layout: s.layout, display: s.display, setLayout: s.setLayout, setDisplay: s.setDisplay, resetAll: s.resetAll,
  })));
  const soundEnabled = useAlertStore((s) => s.soundEnabled);
  const toggleSound = useAlertStore((s) => s.toggleSound);

  return (
    <Popover
      align="right"
      width="w-[300px]"
      trigger={(open) => (
        <IconButton active={open} aria-label="Settings" title="Settings">
          <Settings className="w-4 h-4" />
        </IconButton>
      )}
    >
      {(close) => (
        <div className="px-3 pb-2">
          <MenuLabel>Layout</MenuLabel>
          <SwitchRow label="Account overview" help="Bot column · Account tab" checked={layout.accountOverview} onChange={(v) => setLayout("accountOverview", v)} />
          <SwitchRow label="Chart" checked={layout.chart} onChange={(v) => setLayout("chart", v)} />
          <SwitchRow label="Favorites" help="Symbol strip under the nav" checked={layout.favorites} onChange={(v) => setLayout("favorites", v)} />
          <SwitchRow label="Order book" help="Order book · Trades column" checked={layout.orderBook} onChange={(v) => setLayout("orderBook", v)} />
          <SwitchRow label="Tables" help="Positions · Orders · History panel" checked={layout.tables} onChange={(v) => setLayout("tables", v)} />
          <SwitchRow label="Activity feed" help="Activity tab and drawer" checked={layout.activityFeed} onChange={(v) => setLayout("activityFeed", v)} />
          <MenuDivider />
          <MenuLabel>Trading · Display</MenuLabel>
          <SwitchRow label="Trade notifications" help="Toast on every live fill" checked={display.tradeToasts} onChange={(v) => setDisplay("tradeToasts", v)} />
          <SwitchRow label="Sound" help="Tones for fills, PnL and alerts" checked={soundEnabled} onChange={() => toggleSound()} />
          <SwitchRow label="Colour blind mode" help="Mint / rose → blue / orange" checked={display.colorBlind} onChange={(v) => setDisplay("colorBlind", v)} />
          <SwitchRow label="Compact rows" help="26 px table rows" checked={display.compactRows} onChange={(v) => setDisplay("compactRows", v)} />
          <MenuDivider />
          <div className="flex items-center justify-between pt-1">
            <button type="button" onClick={resetAll} className="inline-flex items-center gap-1.5 h-7 px-2 rounded-[6px] text-[12.5px] font-medium text-text hover:bg-hover">
              <RotateCcw className="w-3.5 h-3.5" /> Reset all
            </button>
            <Link to="/settings" onClick={close} className="text-[12.5px] font-semibold text-mint hover:underline">All settings →</Link>
          </div>
        </div>
      )}
    </Popover>
  );
}
