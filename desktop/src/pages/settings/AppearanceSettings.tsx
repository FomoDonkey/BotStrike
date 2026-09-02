import { GlassPanel } from "@/components/shared/GlassPanel";
import { Palette, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";
import { useThemeStore, type ThemeVariant } from "@/stores/themeStore";
import { useAlertStore } from "@/stores/alertStore";

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

  return (
    <div className="space-y-4">
      <GlassPanel className="p-4 sm:p-5">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
          <Palette className="w-3 h-3" /> Theme
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {THEMES.map((t) => (
            <button
              key={t.id}
              onClick={() => setTheme(t.id)}
              className={cn(
                "p-4 rounded-lg border text-left transition-all",
                themeVariant === t.id
                  ? "border-accent/60"
                  : "border-hairline hover:border-white/20"
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

      <GlassPanel className="p-4 sm:p-5">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Sound</h3>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {soundEnabled ? <Volume2 className="w-4 h-4 text-accent" /> : <VolumeX className="w-4 h-4 text-text-muted" />}
            <span className="text-sm text-text-secondary">Notification Sounds</span>
          </div>
          <button
            onClick={toggleSound}
            role="switch"
            aria-checked={soundEnabled}
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
  );
}
