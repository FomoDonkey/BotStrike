import { create } from "zustand";

export type ThemeVariant = "dark" | "darker" | "oled";

// Neutral near-black palettes (v2.15 visual system) — no navy tint, hairline borders.
const THEMES: Record<ThemeVariant, Record<string, string>> = {
  dark: {
    "--color-bg-base": "#0A0A0A",
    "--color-bg-surface": "#0F0F0F",
    "--color-bg-elevated": "#171717",
    "--color-border-subtle": "#1F1F1F",
    "--color-border-default": "#2A2A2A",
  },
  darker: {
    "--color-bg-base": "#050505",
    "--color-bg-surface": "#0A0A0A",
    "--color-bg-elevated": "#111111",
    "--color-border-subtle": "#1A1A1A",
    "--color-border-default": "#242424",
  },
  oled: {
    "--color-bg-base": "#000000",
    "--color-bg-surface": "#050505",
    "--color-bg-elevated": "#0C0C0C",
    "--color-border-subtle": "#161616",
    "--color-border-default": "#202020",
  },
};

function safeGetItem(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function safeSetItem(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

interface ThemeState {
  variant: ThemeVariant;
  setVariant: (v: ThemeVariant) => void;
}

export const useThemeStore = create<ThemeState>((set) => ({
  variant: (safeGetItem("botstrike-theme") as ThemeVariant) || "dark",

  setVariant: (variant) => {
    try {
      const vars = THEMES[variant];
      const root = document.documentElement;
      for (const [key, value] of Object.entries(vars)) {
        root.style.setProperty(key, value);
      }
    } catch { /* ignore */ }
    safeSetItem("botstrike-theme", variant);
    set({ variant });
  },
}));

export function initTheme() {
  try {
    const saved = (safeGetItem("botstrike-theme") as ThemeVariant) || "dark";
    const vars = THEMES[saved];
    if (vars && document.documentElement) {
      for (const [key, value] of Object.entries(vars)) {
        document.documentElement.style.setProperty(key, value);
      }
    }
  } catch {
    // Silently ignore — default CSS theme applies
  }
}
