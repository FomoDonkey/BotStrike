import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useShallow } from "zustand/shallow";
import { Zap, Menu, X, Play, Square, RefreshCw, ChevronDown, ExternalLink } from "lucide-react";
import { cn, capitalize, formatMoney } from "@/lib/utils";
import { EXCHANGE_LABELS, DOCS_URL } from "@/lib/constants";
import { useSystemStore } from "@/stores/systemStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useRiskStore } from "@/stores/riskStore";
import { useMarketStore } from "@/stores/marketStore";
import { useExchangeStore } from "@/stores/exchangeStore";
import { useBridgeConfig } from "@/lib/config";
import { RegimeChip } from "@/components/ui/Chip";
import { Popover, MenuItem, MenuDivider, MenuLabel } from "@/components/ui/Popover";
import { ConfirmDialog } from "@/components/ui/Modal";
import { AnimatedNumber } from "@/components/shared/AnimatedNumber";
import { NAV_ITEMS, SETTINGS_ITEM } from "./navItems";
import { SettingsPopover } from "./SettingsPopover";
import { useBotControl, type BotAction } from "./useBotControl";

function Logo() {
  return (
    <NavLink to="/trading" className="flex items-center gap-2 shrink-0" aria-label="BotStrike">
      <span className="inline-flex items-center justify-center w-7 h-7 rounded-[8px] bg-mint text-bg">
        <Zap className="w-4 h-4" strokeWidth={2.5} />
      </span>
      <span className="text-[15px] font-bold tracking-[0.02em] text-text">BOTSTRIKE</span>
    </NavLink>
  );
}

/** Connection dot: mint = bridge + market feed · amber = bridge only · rose = unreachable. */
function ConnectionStatus() {
  const { wsConnected, bridgeConnected, mode } = useSystemStore(useShallow((s) => ({ wsConnected: s.wsConnected, bridgeConnected: s.bridgeConnected, mode: s.mode })));
  const hasPrices = useMarketStore((s) => s.lastTickAt > 0);
  const exchange = useExchangeStore((s) => s.exchange);
  const { url } = useBridgeConfig();
  const feed = bridgeConnected && (wsConnected || hasPrices);
  const title = !bridgeConnected ? `Bridge unreachable: ${url}` : feed ? `Bridge online (${url}) · market feed live` : `Bridge online (${url}) · engine stopped / no market feed`;
  return (
    <span className="hidden xl:inline-flex items-center gap-2 text-[13px] font-medium text-text whitespace-nowrap" title={title}>
      <span className={cn("w-2 h-2 rounded-full", !bridgeConnected ? "bg-rose" : feed ? "bg-mint" : "bg-amber")} />
      {capitalize(mode.replace("_", " "))} · {EXCHANGE_LABELS[exchange] ?? exchange} feed
    </span>
  );
}

function EquityChip({ className }: { className?: string }) {
  const equity = useTradingStore((s) => s.metrics.equity);
  return (
    <span className={cn("inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg bg-panel-2 text-[13px] whitespace-nowrap", className)} title="Account equity (all-time)">
      <span className="font-medium text-text-2">Equity</span>
      <AnimatedNumber value={equity} format={(v) => formatMoney(v)} className="num font-semibold text-text" />
    </span>
  );
}

/** Primary CTA "Bot · Running" with the Start / Stop / Restart menu (token-gated). */
function BotMenu() {
  const { engineRunning, bridgeConnected } = useSystemStore(useShallow((s) => ({ engineRunning: s.engineRunning, bridgeConnected: s.bridgeConnected })));
  const { canControl, disabledReason, busy, run } = useBotControl();
  const [confirm, setConfirm] = useState<BotAction | null>(null);
  const label = !bridgeConnected ? "Bot · Offline" : engineRunning ? "Bot · Running" : "Bot · Stopped";
  const tone = !bridgeConnected ? "rose" : engineRunning ? "mint" : "amber";
  return (
    <>
      <Popover
        align="right"
        width="w-56"
        trigger={(open) => (
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={open}
            className={cn(
              "inline-flex items-center gap-1.5 h-8 pl-2.5 pr-2 rounded-lg text-[13px] font-semibold whitespace-nowrap transition-colors",
              tone === "mint" ? "bg-mint text-bg hover:brightness-[1.06]" : "border border-hairline-strong text-text hover:bg-hover",
            )}
          >
            <span className={cn("w-2 h-2 rounded-full", tone === "mint" ? "bg-bg" : tone === "amber" ? "bg-amber" : "bg-rose")} />
            {label}
            <ChevronDown className={cn("w-3.5 h-3.5 transition-transform", open && "rotate-180")} />
          </button>
        )}
      >
        {(close) => (
          <>
            <MenuLabel>Engine</MenuLabel>
            <MenuItem disabled={!canControl || engineRunning || busy !== null} title={disabledReason} onClick={() => { close(); void run("start"); }}>
              <Play className="w-3.5 h-3.5 text-mint" /> Start · paper
            </MenuItem>
            <MenuItem disabled={!canControl || engineRunning || busy !== null} title={disabledReason} onClick={() => { close(); void run("start_dry"); }}>
              <Play className="w-3.5 h-3.5 text-blue" /> Start · dry run
            </MenuItem>
            <MenuItem disabled={!canControl || !engineRunning || busy !== null} title={disabledReason} onClick={() => { close(); setConfirm("stop"); }}>
              <Square className="w-3.5 h-3.5 text-rose" /> Stop
            </MenuItem>
            <MenuItem disabled={!canControl || busy !== null} title={disabledReason} onClick={() => { close(); setConfirm("restart"); }}>
              <RefreshCw className="w-3.5 h-3.5" /> Restart
            </MenuItem>
            {!canControl && (
              <>
                <MenuDivider />
                <p className="px-3 py-1.5 text-[12px] font-medium text-text-2 whitespace-normal leading-snug">{disabledReason}</p>
              </>
            )}
          </>
        )}
      </Popover>
      <ConfirmDialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        onConfirm={() => { const a = confirm; setConfirm(null); if (a) void run(a); }}
        title={confirm === "stop" ? "Stop the bot?" : "Restart the engine?"}
        body={confirm === "stop"
          ? "The engine stops trading. Open paper positions stay in the book until the engine runs again."
          : "The engine stops and starts again with the same mode and exchange. Positions are kept."}
        confirmLabel={confirm === "stop" ? "Stop bot" : "Restart"}
        danger={confirm === "stop"}
        busy={busy !== null}
      />
    </>
  );
}

function MobileMenu({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[70] lg:hidden" role="dialog" aria-label="Navigation">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} aria-hidden />
      <div className="absolute inset-y-0 right-0 w-72 max-w-[85vw] bg-panel border-l border-hairline flex flex-col">
        <div className="flex items-center h-14 px-4 border-b border-hairline">
          <Logo />
          <button type="button" onClick={onClose} aria-label="Close navigation" className="ml-auto w-8 h-8 inline-flex items-center justify-center rounded-[6px] text-text hover:bg-hover">
            <X className="w-4 h-4" />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto py-2">
          {[...NAV_ITEMS, SETTINGS_ITEM].map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              onClick={onClose}
              className={({ isActive }) => cn("flex items-center gap-3 h-11 px-4 text-[14px] font-medium", isActive ? "text-mint bg-active" : "text-text hover:bg-hover")}
            >
              <item.icon className="w-4.5 h-4.5" /> {item.label}
            </NavLink>
          ))}
          <a href={DOCS_URL} target="_blank" rel="noreferrer" className="flex items-center gap-3 h-11 px-4 text-[14px] font-medium text-text hover:bg-hover">
            <ExternalLink className="w-4.5 h-4.5" /> Docs
          </a>
        </nav>
        <div className="p-4 border-t border-hairline flex items-center gap-2">
          <BotMenu />
          <SettingsPopover />
        </div>
      </div>
    </div>
  );
}

/** 56 px top navigation bar (spec §2). */
export function TopNav() {
  const navigate = useNavigate();
  const regime = useRiskStore((s) => s.regime);
  const [menuOpen, setMenuOpen] = useState(false);

  // Alt+1..7 navigate, Alt+9 settings (unchanged from the sidebar)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!e.altKey) return;
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) return;
      const item = [...NAV_ITEMS, SETTINGS_ITEM].find((n) => n.shortcut === e.key);
      if (item) {
        e.preventDefault();
        navigate(item.path);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  return (
    <header className="flex items-center gap-3 h-14 px-3 sm:px-4 bg-bg border-b border-hairline shrink-0 select-none">
      <Logo />
      <nav className="hidden lg:flex items-stretch h-full ml-4" aria-label="Primary">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              cn("relative flex items-center px-3 text-[14px] font-medium transition-colors", isActive ? "text-text" : "text-text-2 hover:text-text")
            }
          >
            {({ isActive }) => (
              <>
                {item.label}
                {isActive && <span className="absolute left-3 right-3 bottom-0 h-[2px] rounded-full bg-mint" />}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-2 sm:gap-3 min-w-0">
        <ConnectionStatus />
        <RegimeChip regime={regime} />
        <EquityChip />
        <span className="hidden lg:inline-flex"><SettingsPopover /></span>
        <span className="hidden lg:inline-flex"><BotMenu /></span>
        <button
          type="button"
          onClick={() => setMenuOpen(true)}
          className="lg:hidden inline-flex items-center justify-center w-8 h-8 rounded-[6px] text-text hover:bg-hover"
          aria-label="Open navigation"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>
      <MobileMenu open={menuOpen} onClose={() => setMenuOpen(false)} />
    </header>
  );
}
