import { create } from "zustand";

export type ThemeVariant = "dark" | "darker" | "oled";

// Neutral near-black palettes (v2.16 tokens) — no navy tint, hairline borders.
const THEMES: Record<ThemeVariant, Record<string, string>> = {
  dark: {
    "--color-bg": "#0A0A0A",
    "--color-panel": "#0F0F0F",
    "--color-panel-2": "#141414",
    "--color-hover": "#1A1A1A",
    "--color-active": "#232323",
  },
  darker: {
    "--color-bg": "#050505",
    "--color-panel": "#0A0A0A",
    "--color-panel-2": "#101010",
    "--color-hover": "#161616",
    "--color-active": "#1F1F1F",
  },
  oled: {
    "--color-bg": "#000000",
    "--color-panel": "#050505",
    "--color-panel-2": "#0C0C0C",
    "--color-hover": "#141414",
    "--color-active": "#1C1C1C",
  },
};

const ALL_KEYS = Object.keys(THEMES.dark);

function safeGetItem(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function safeSetItem(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

function apply(variant: ThemeVariant) {
  const vars = THEMES[variant];
  const root = document.documentElement;
  if (!vars || !root) return;
  for (const key of ALL_KEYS) {
    // "dark" is the stylesheet default: clear the inline override instead of pinning it
    if (variant === "dark") root.style.removeProperty(key);
    else root.style.setProperty(key, vars[key]);
  }
}

interface ThemeState {
  variant: ThemeVariant;
  setVariant: (v: ThemeVariant) => void;
}

export const useThemeStore = create<ThemeState>((set) => ({
  variant: (safeGetItem("botstrike-theme") as ThemeVariant) || "dark",

  setVariant: (variant) => {
    try { apply(variant); } catch { /* ignore */ }
    safeSetItem("botstrike-theme", variant);
    set({ variant });
  },
}));

export function initTheme() {
  try {
    const saved = (safeGetItem("botstrike-theme") as ThemeVariant) || "dark";
    apply(saved in THEMES ? saved : "dark");
  } catch {
    // Silently ignore — default CSS theme applies
  }
}
