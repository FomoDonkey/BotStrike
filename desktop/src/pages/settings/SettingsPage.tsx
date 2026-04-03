import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { api } from "@/lib/api";
import { Settings, DollarSign, Shield, Zap, Bell, Server, Palette, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";
import { useThemeStore, type ThemeVariant } from "@/stores/themeStore";
import { useAlertStore } from "@/stores/alertStore";

interface ConfigData {
  use_testnet: boolean;
  has_api_key: boolean;
  has_telegram: boolean;
  symbols: Array<{
    symbol: string;
    leverage: number;
    max_position_usd: number;
    vpin_bucket_size: number;
    vpin_toxic_threshold: number;
    hawkes_spike_mult: number;
    mm_gamma: number;
    obi_levels: number;
  }>;
  trading: {
    initial_capital: number;
    max_drawdown_pct: number;
    max_leverage: number;
    max_total_exposure_pct: number;
    risk_per_trade_pct: number;
    allocation_mean_reversion: number;
    allocation_order_flow_momentum: number;
    allocation_trend_following: number;
    allocation_market_making: number;
    maker_fee: number;
    taker_fee: number;
    slippage_bps: number;
    vol_target_annual: number;
    kelly_min_trades: number;
    kelly_floor_pct: number;
    kelly_ceiling_pct: number;
  };
}

const TABS = [
  { id: "capital", label: "Capital & Risk", icon: DollarSign },
  { id: "symbols", label: "Symbols", icon: Zap },
  { id: "execution", label: "Execution", icon: Server },
  { id: "notifications", label: "Notifications", icon: Bell },
  { id: "appearance", label: "Appearance", icon: Palette },
];

function Field({ label, value, unit }: { label: string; value: string | number; unit?: string }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-white/[0.03]">
      <span className="text-sm text-text-secondary">{label}</span>
      <span className="font-mono text-sm text-text-primary">
        {value}{unit && <span className="text-text-muted ml-1">{unit}</span>}
      </span>
    </div>
  );
}

export function SettingsPage() {
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("capital");
  const themeVariant = useThemeStore((s) => s.variant);
  const setTheme = useThemeStore((s) => s.setVariant);
  const soundEnabled = useAlertStore((s) => s.soundEnabled);
  const toggleSound = useAlertStore((s) => s.toggleSound);

  useEffect(() => {
    api.config().then((data) => {
      if (data && !data.error) setConfig(data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Settings className="w-5 h-5 text-accent" /> Settings & Configuration
      </h1>

      {/* Tabs */}
      <div className="flex gap-1 p-1 rounded-xl bg-bg-surface/50">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "flex items-center gap-2 px-4 py-2 rounded-lg text-sm transition-all",
              tab === t.id
                ? "bg-accent/10 text-accent"
                : "text-text-muted hover:text-text-secondary"
            )}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted">Loading configuration...</p>
        </GlassPanel>
      ) : !config ? (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted">Start the bridge server to view configuration</p>
        </GlassPanel>
      ) : (
        <>
          {tab === "capital" && (
            <div className="grid grid-cols-2 gap-4">
              <GlassPanel className="p-5">
                <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
                  <DollarSign className="w-3 h-3" /> Capital
                </h3>
                <Field label="Initial Capital" value={`$${config.trading.initial_capital}`} />
                <Field label="Max Leverage" value={`${config.trading.max_leverage}x`} />
                <Field label="Max Exposure" value={`${(config.trading.max_total_exposure_pct * 100).toFixed(0)}%`} />
              </GlassPanel>
              <GlassPanel className="p-5">
                <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
                  <Shield className="w-3 h-3" /> Risk Parameters
                </h3>
                <Field label="Max Drawdown" value={`${(config.trading.max_drawdown_pct * 100).toFixed(0)}%`} />
                <Field label="Risk per Trade" value={`${(config.trading.risk_per_trade_pct * 100).toFixed(1)}%`} />
                <Field label="Vol Target (Annual)" value={`${(config.trading.vol_target_annual * 100).toFixed(0)}%`} />
                <Field label="Kelly Floor" value={`${(config.trading.kelly_floor_pct * 100).toFixed(1)}%`} />
                <Field label="Kelly Ceiling" value={`${(config.trading.kelly_ceiling_pct * 100).toFixed(1)}%`} />
                <Field label="Kelly Min Trades" value={config.trading.kelly_min_trades} />
              </GlassPanel>
              <GlassPanel className="col-span-2 p-5">
                <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Strategy Allocation</h3>
                <div className="grid grid-cols-4 gap-4">
                  {[
                    { name: "Mean Reversion", val: config.trading.allocation_mean_reversion, color: "#6C5CE7" },
                    { name: "Order Flow Momentum", val: config.trading.allocation_order_flow_momentum, color: "#00CEC9" },
                    { name: "Trend Following", val: config.trading.allocation_trend_following, color: "#00B894" },
                    { name: "Market Making", val: config.trading.allocation_market_making, color: "#FDCB6E" },
                  ].map((s) => (
                    <div key={s.name} className="text-center">
                      <div className="text-2xl font-mono font-bold" style={{ color: s.color }}>
                        {(s.val * 100).toFixed(0)}%
                      </div>
                      <p className="text-xs text-text-muted mt-1">{s.name}</p>
                    </div>
                  ))}
                </div>
              </GlassPanel>
            </div>
          )}

          {tab === "symbols" && (
            <div className="space-y-3">
              {config.symbols.map((sym) => (
                <GlassPanel key={sym.symbol} className="p-5">
                  <h3 className="text-sm font-mono font-bold text-text-primary mb-3">{sym.symbol}</h3>
                  <div className="grid grid-cols-3 gap-x-8">
                    <Field label="Leverage" value={`${sym.leverage}x`} />
                    <Field label="Max Position" value={`$${sym.max_position_usd}`} />
                    <Field label="OBI Levels" value={sym.obi_levels} />
                    <Field label="VPIN Bucket" value={`$${sym.vpin_bucket_size.toLocaleString()}`} />
                    <Field label="VPIN Toxic" value={sym.vpin_toxic_threshold} />
                    <Field label="Hawkes Spike" value={`${sym.hawkes_spike_mult}x`} />
                  </div>
                </GlassPanel>
              ))}
            </div>
          )}

          {tab === "execution" && (
            <GlassPanel className="p-5">
              <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Execution Parameters</h3>
              <div className="grid grid-cols-2 gap-x-12">
                <Field label="Maker Fee" value={`${(config.trading.maker_fee * 10000).toFixed(1)}`} unit="bps" />
                <Field label="Taker Fee" value={`${(config.trading.taker_fee * 10000).toFixed(1)}`} unit="bps" />
                <Field label="Slippage Model" value={config.trading.slippage_bps} unit="bps" />
              </div>
            </GlassPanel>
          )}

          {tab === "notifications" && (
            <GlassPanel className="p-5">
              <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Notifications</h3>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-text-secondary">API Key</span>
                  <span className={cn(
                    "text-xs font-mono px-2 py-0.5 rounded",
                    config.has_api_key ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
                  )}>
                    {config.has_api_key ? "CONFIGURED" : "NOT SET"}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-text-secondary">Telegram</span>
                  <span className={cn(
                    "text-xs font-mono px-2 py-0.5 rounded",
                    config.has_telegram ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
                  )}>
                    {config.has_telegram ? "CONFIGURED" : "NOT SET"}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-text-secondary">Testnet Mode</span>
                  <span className={cn(
                    "text-xs font-mono px-2 py-0.5 rounded",
                    config.use_testnet ? "bg-warning/10 text-warning" : "bg-profit/10 text-profit"
                  )}>
                    {config.use_testnet ? "TESTNET" : "MAINNET"}
                  </span>
                </div>
              </div>
            </GlassPanel>
          )}

          {tab === "appearance" && (
            <div className="space-y-4">
              <GlassPanel className="p-5">
                <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
                  <Palette className="w-3 h-3" /> Theme
                </h3>
                <div className="grid grid-cols-3 gap-3">
                  {([
                    { id: "dark" as ThemeVariant, name: "Dark", desc: "Default cyberpunk", bg: "#050810" },
                    { id: "darker" as ThemeVariant, name: "Darker", desc: "Deep space", bg: "#020408" },
                    { id: "oled" as ThemeVariant, name: "OLED", desc: "Pure black", bg: "#000000" },
                  ]).map((t) => (
                    <button
                      key={t.id}
                      onClick={() => setTheme(t.id)}
                      className={cn(
                        "p-4 rounded-xl border text-left transition-all",
                        themeVariant === t.id
                          ? "border-accent/50 shadow-[0_0_12px_rgba(0,212,170,0.1)]"
                          : "border-white/5 hover:border-white/10"
                      )}
                    >
                      <div
                        className="w-full h-8 rounded-lg mb-3 border border-white/10"
                        style={{ backgroundColor: t.bg }}
                      />
                      <p className="text-sm font-medium text-text-primary">{t.name}</p>
                      <p className="text-[10px] text-text-muted">{t.desc}</p>
                    </button>
                  ))}
                </div>
              </GlassPanel>

              <GlassPanel className="p-5">
                <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Sound</h3>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {soundEnabled ? <Volume2 className="w-4 h-4 text-accent" /> : <VolumeX className="w-4 h-4 text-text-muted" />}
                    <span className="text-sm text-text-secondary">Notification Sounds</span>
                  </div>
                  <button
                    onClick={toggleSound}
                    className={cn(
                      "w-10 h-5 rounded-full transition-all relative",
                      soundEnabled ? "bg-accent" : "bg-white/10"
                    )}
                  >
                    <span className={cn(
                      "absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all",
                      soundEnabled ? "left-[22px]" : "left-0.5"
                    )} />
                  </button>
                </div>
                <p className="text-[10px] text-text-muted mt-2">
                  Plays tones for trade fills, profit/loss, and alert triggers
                </p>
              </GlassPanel>
            </div>
          )}
        </>
      )}
    </motion.div>
  );
}
