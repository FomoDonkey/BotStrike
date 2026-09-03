import { useShallow } from "zustand/shallow";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { SwitchRow } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { useThemeStore, type ThemeVariant } from "@/stores/themeStore";
import { useAlertStore } from "@/stores/alertStore";
import { useUiStore } from "@/stores/uiStore";

const THEMES: { id: ThemeVariant; name: string; desc: string; bg: string }[] = [
  { id: "dark", name: "Dark", desc: "Neutral near-black (default)", bg: "#0A0A0A" },
  { id: "darker", name: "Darker", desc: "Deeper panels", bg: "#050505" },
  { id: "oled", name: "OLED", desc: "Pure black", bg: "#000000" },
];

export function AppearanceSettings() {
  const themeVariant = useThemeStore((s) => s.variant);
  const setTheme = useThemeStore((s) => s.setVariant);
  const soundEnabled = useAlertStore((s) => s.soundEnabled);
  const toggleSound = useAlertStore((s) => s.toggleSound);
  const { layout, display, setLayout, setDisplay, resetAll } = useUiStore(useShallow((s) => ({ layout: s.layout, display: s.display, setLayout: s.setLayout, setDisplay: s.setDisplay, resetAll: s.resetAll })));

  return (
    <div className="space-y-3">
      <Panel>
        <PanelHeader title="Theme" />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 p-4">
          {THEMES.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-pressed={themeVariant === t.id}
              onClick={() => setTheme(t.id)}
              className={cn("p-3 rounded-lg border text-left transition-colors", themeVariant === t.id ? "border-mint bg-mint-soft" : "border-hairline hover:bg-hover")}
            >
              <div className="w-full h-8 rounded-[6px] mb-2 border border-hairline-strong" style={{ backgroundColor: t.bg }} />
              <p className="text-[13px] font-semibold text-text">{t.name}</p>
              <p className="text-[12px] font-medium text-text-2">{t.desc}</p>
            </button>
          ))}
        </div>
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Panel>
          <PanelHeader title="Layout · Trade page" />
          <div className="px-4 py-2">
            <SwitchRow label="Account overview" help="Bot column · Account tab" checked={layout.accountOverview} onChange={(v) => setLayout("accountOverview", v)} />
            <SwitchRow label="Chart" checked={layout.chart} onChange={(v) => setLayout("chart", v)} />
            <SwitchRow label="Favorites" help="Symbol strip under the nav" checked={layout.favorites} onChange={(v) => setLayout("favorites", v)} />
            <SwitchRow label="Order book" help="Order book · Trades column" checked={layout.orderBook} onChange={(v) => setLayout("orderBook", v)} />
            <SwitchRow label="Tables" help="Positions · Orders · History panel" checked={layout.tables} onChange={(v) => setLayout("tables", v)} />
            <SwitchRow label="Activity feed" checked={layout.activityFeed} onChange={(v) => setLayout("activityFeed", v)} />
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="Trading · Display" right={<Button variant="ghost" size="xs" onClick={resetAll}>Reset all</Button>} />
          <div className="px-4 py-2">
            <SwitchRow label="Trade notifications" help="Toast on every live fill" checked={display.tradeToasts} onChange={(v) => setDisplay("tradeToasts", v)} />
            <SwitchRow label="Sound" help="Tones for fills, PnL and alerts" checked={soundEnabled} onChange={() => toggleSound()} />
            <SwitchRow label="Colour blind mode" help="Mint / rose → blue / orange" checked={display.colorBlind} onChange={(v) => setDisplay("colorBlind", v)} />
            <SwitchRow label="Compact rows" help="26 px table rows" checked={display.compactRows} onChange={(v) => setDisplay("compactRows", v)} />
          </div>
        </Panel>
      </div>
    </div>
  );
}
