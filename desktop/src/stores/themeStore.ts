import { create } from "zustand";

export type ThemeVariant = "dark" | "darker" | "oled";

const THEMES: Record<ThemeVariant, Record<string, string>> = {
  dark: {
    "--color-bg-base": "#050810",
    "--color-bg-surface": "#0B1120",
    "--color-bg-elevated": "#111B2E",
    "--color-border-subtle": "#1A2540",
    "--color-border-default": "#243050",
  },
  darker: {
    "--color-bg-base": "#020408",
    "--color-bg-surface": "#060B15",
    "--color-bg-elevated": "#0A1020",
    "--color-border-subtle": "#121C30",
    "--color-border-default": "#1A2540",
  },
  oled: {
    "--color-bg-base": "#000000",
    "--color-bg-surface": "#050508",
    "--color-bg-elevated": "#0A0A10",
    "--color-border-subtle": "#101018",
    "--color-border-default": "#181828",
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
