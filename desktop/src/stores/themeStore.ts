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

interface ThemeState {
  variant: ThemeVariant;
  setVariant: (v: ThemeVariant) => void;
}

export const useThemeStore = create<ThemeState>((set) => ({
  variant: (localStorage.getItem("botstrike-theme") as ThemeVariant) || "dark",

  setVariant: (variant) => {
    // Apply CSS variables to :root
    const vars = THEMES[variant];
    const root = document.documentElement;
    for (const [key, value] of Object.entries(vars)) {
      root.style.setProperty(key, value);
    }
    localStorage.setItem("botstrike-theme", variant);
    set({ variant });
  },
}));

// Apply saved theme on load
export function initTheme() {
  const saved = (localStorage.getItem("botstrike-theme") as ThemeVariant) || "dark";
  const vars = THEMES[saved];
  const root = document.documentElement;
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value);
  }
}
