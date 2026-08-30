import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { api, probeBridge, ApiError, type ConfigResponse, type HealthResponse } from "@/lib/api";
import { Settings, DollarSign, Shield, Zap, Bell, Server, Palette, Volume2, VolumeX, Plug, Eye, EyeOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { useThemeStore, type ThemeVariant } from "@/stores/themeStore";
import { useAlertStore } from "@/stores/alertStore";
import { useSystemStore } from "@/stores/systemStore";
import {
  useBridgeConfig,
  setBridgeUrl,
  setBridgeToken,
  normalizeBridgeUrl,
  validateBridgeUrl,
  getBridgeMode,
  DEFAULT_BRIDGE_URL,
} from "@/lib/config";
import { restartWebSockets } from "@/hooks/useWebSocket";
import { WS_CHANNEL_LIST } from "@/lib/constants";

const TABS = [
  { id: "connection", label: "Connection", icon: Plug },
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

// ── Connection tab ───────────────────────────────────────────────

type TestState =
  | { state: "idle" }
  | { state: "testing" }
  | { state: "ok"; health: HealthResponse; ms: number; url: string }
  | { state: "fail"; detail: string };

const INPUT_CLS =
  "w-full bg-bg-base border border-white/10 rounded-lg px-3 py-2 text-sm text-text-primary font-mono focus:outline-none focus:border-accent/50";

function ConnectionSettings() {
  const { url: currentUrl, token: currentToken } = useBridgeConfig();
  const [draftUrl, setDraftUrl] = useState(currentUrl);
  const [draftToken, setDraftToken] = useState(currentToken);
  const [showToken, setShowToken] = useState(false);
  const [test, setTest] = useState<TestState>({ state: "idle" });
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const openChannels = useSystemStore((s) => s.openChannels.length);
  const bridgeConnected = useSystemStore((s) => s.bridgeConnected);
  const engineRunning = useSystemStore((s) => s.engineRunning);

  const urlError = validateBridgeUrl(draftUrl);
  const normalized = urlError ? null : normalizeBridgeUrl(draftUrl);
  const dirty = normalized !== currentUrl || draftToken.trim() !== currentToken;
  const draftMode = getBridgeMode(normalized ?? currentUrl);

  const runTest = async () => {
    if (!normalized) {
      setTest({ state: "fail", detail: urlError ?? "Invalid URL" });
      return;
    }
    setTest({ state: "testing" });
    const t0 = Date.now();
    try {
      const health = await probeBridge(normalized);
      setTest({ state: "ok", health, ms: Date.now() - t0, url: normalized });
    } catch (e) {
      setTest({ state: "fail", detail: e instanceof ApiError ? e.message : String(e) });
    }
  };

  const save = () => {
    const n = setBridgeUrl(draftUrl);
    if (!n) {
      setTest({ state: "fail", detail: urlError ?? "Invalid URL" });
      return;
    }
    setBridgeToken(draftToken);
    setDraftUrl(n);
    setDraftToken(draftToken.trim());
    restartWebSockets();
    setTest({ state: "idle" });
    setSavedAt(Date.now());
  };

  return (
    <div className="grid grid-cols-2 gap-4">
      <GlassPanel className="p-5 col-span-2">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
          <Plug className="w-3 h-3" /> Bridge Server
          <span
            className={cn(
              "ml-auto px-2 py-0.5 rounded text-[10px] font-bold uppercase",
              draftMode === "remote" ? "bg-info/10 text-info" : "bg-white/5 text-text-muted",
            )}
          >
            {draftMode}
          </span>
        </h3>

        <label className="text-xs text-text-muted block mb-1">Bridge URL (host[:port], http:// or https://)</label>
        <input
          value={draftUrl}
          onChange={(e) => { setDraftUrl(e.target.value); setTest({ state: "idle" }); }}
          onKeyDown={(e) => { if (e.key === "Enter") void runTest(); }}
          spellCheck={false}
          autoComplete="off"
          placeholder="http://192.168.1.204:9420"
          className={cn(INPUT_CLS, urlError && draftUrl.trim() && "border-loss/50")}
        />
        <p className="text-[10px] text-text-muted mt-1">
          {urlError && draftUrl.trim() ? (
            <span className="text-loss">{urlError}</span>
          ) : (
            <>
              Local: <code>{DEFAULT_BRIDGE_URL.replace(/^https?:\/\//, "")}</code> (bundled engine is started automatically)
              {" · "}LAN: <code>192.168.1.204:9420</code>{" · "}Tailscale: <code>100.x.y.z:9420</code>
              {normalized && normalized !== draftUrl.trim() && (
                <> · will be saved as <code className="text-text-secondary">{normalized}</code></>
              )}
            </>
          )}
        </p>

        <label className="text-xs text-text-muted block mt-4 mb-1">
          Auth token (required for a remote bridge and to start/stop LIVE — from the server&apos;s <code>.env</code>)
        </label>
        <div className="relative">
          <input
            value={draftToken}
            onChange={(e) => setDraftToken(e.target.value)}
            type={showToken ? "text" : "password"}
            spellCheck={false}
            autoComplete="off"
            placeholder="BOTSTRIKE_AUTH_TOKEN"
            className={cn(INPUT_CLS, "pr-9")}
          />
          <button
            type="button"
            onClick={() => setShowToken((v) => !v)}
            title={showToken ? "Hide token" : "Show token"}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
          >
            {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>

        <div className="flex items-center gap-3 mt-4 flex-wrap">
          <button
            onClick={runTest}
            disabled={test.state === "testing" || !normalized}
            className="px-4 py-2 rounded-lg border border-white/10 text-sm text-text-secondary hover:border-white/20 disabled:opacity-50 transition-all"
          >
            {test.state === "testing" ? "Testing…" : "Test connection"}
          </button>
          <button
            onClick={save}
            disabled={!dirty || !normalized}
            className="px-4 py-2 rounded-lg bg-accent text-bg-base text-sm font-semibold disabled:opacity-40 hover:bg-accent/90 transition-all"
          >
            Save &amp; reconnect
          </button>
          {test.state === "ok" && (
            <span className="text-xs font-mono text-profit">
              {test.health.status} · engine {test.health.engine_running ? "running" : "stopped"} · {test.health.mode}
              {test.health.version ? ` · v${test.health.version}` : ""} · {test.ms} ms
            </span>
          )}
          {test.state === "fail" && <span className="text-xs font-mono text-loss">{test.detail}</span>}
          {test.state === "idle" && savedAt && !dirty && (
            <span className="text-xs font-mono text-text-muted">Saved · reconnecting…</span>
          )}
        </div>

        <div className="mt-4 pt-4 border-t border-white/5 grid grid-cols-4 text-xs gap-2">
          <div>
            <span className="text-text-muted">Active URL</span>
            <p className="font-mono text-text-secondary break-all">{currentUrl}</p>
          </div>
          <div>
            <span className="text-text-muted">Bridge</span>
            <p className={cn("font-mono", bridgeConnected ? "text-profit" : "text-loss")}>
              {bridgeConnected ? "ONLINE" : "OFFLINE"}
            </p>
          </div>
          <div>
            <span className="text-text-muted">Engine</span>
            <p className={cn("font-mono", engineRunning ? "text-profit" : "text-text-muted")}>
              {engineRunning ? "RUNNING" : "STOPPED"}
            </p>
          </div>
          <div>
            <span className="text-text-muted">WS channels</span>
            <p className="font-mono text-text-secondary">{openChannels}/{WS_CHANNEL_LIST.length}</p>
          </div>
        </div>
      </GlassPanel>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────

export function SettingsPage() {
  const location = useLocation();
  const initialTab = (location.state as { tab?: string } | null)?.tab ?? "capital";
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState(initialTab);
  const { url: bridgeUrl } = useBridgeConfig();
  const themeVariant = useThemeStore((s) => s.variant);
  const setTheme = useThemeStore((s) => s.setVariant);
  const soundEnabled = useAlertStore((s) => s.soundEnabled);
  const toggleSound = useAlertStore((s) => s.toggleSound);

  useEffect(() => {
    let cancelled = false;
    api.config()
      .then((data) => {
        if (cancelled) return;
        setConfig(data);
        setConfigError(null);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setConfigError(e instanceof ApiError ? e.message : String(e));
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [bridgeUrl]);

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

      {tab === "connection" ? (
        <ConnectionSettings />
      ) : loading ? (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted">Loading configuration...</p>
        </GlassPanel>
      ) : !config ? (
        <GlassPanel className="p-8 text-center">
          <p className="text-text-muted">Configuration unavailable from {bridgeUrl.replace(/^https?:\/\//, "")}</p>
          {configError && <p className="text-xs font-mono text-loss mt-2">{configError}</p>}
          <p className="text-xs text-text-muted mt-2">
            Start the engine (System → Start) or check Settings → Connection.
          </p>
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
                <div className="grid grid-cols-5 gap-4">
                  {[
                    { name: "Mean Reversion", val: config.trading.allocation_mean_reversion, color: "#6C5CE7" },
                    { name: "Fibonacci", val: config.trading.allocation_fibonacci_retracement, color: "#F39C12" },
                    { name: "Order Flow", val: config.trading.allocation_order_flow_momentum, color: "#00CEC9" },
                    { name: "Trend Follow", val: config.trading.allocation_trend_following, color: "#00B894" },
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
