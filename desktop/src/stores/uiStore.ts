import { create } from "zustand";

/**
 * Settings popover (gear) — Strike's Layout / Trading / Display switches. Persisted in
 * localStorage; the Layout switches only affect the Trade page, the Display switches are
 * applied as `data-*` attributes on <html> (colour-blind palette, compact rows).
 */
export interface UiLayout {
  accountOverview: boolean;
  chart: boolean;
  favorites: boolean;
  orderBook: boolean;
  tables: boolean;
  activityFeed: boolean;
}

export interface UiDisplay {
  tradeToasts: boolean;
  colorBlind: boolean;
  compactRows: boolean;
}

interface UiState {
  layout: UiLayout;
  display: UiDisplay;
  /** Activity feed drawer (footer "Activity") */
  activityOpen: boolean;
  setLayout: (key: keyof UiLayout, value: boolean) => void;
  setDisplay: (key: keyof UiDisplay, value: boolean) => void;
  setActivityOpen: (open: boolean) => void;
  resetAll: () => void;
}

const KEY = "botstrike.ui";

export const DEFAULT_LAYOUT: UiLayout = {
  accountOverview: true,
  chart: true,
  favorites: true,
  orderBook: true,
  tables: true,
  activityFeed: true,
};

export const DEFAULT_DISPLAY: UiDisplay = {
  tradeToasts: true,
  colorBlind: false,
  compactRows: false,
};

function load(): { layout: UiLayout; display: UiDisplay } {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<{ layout: Partial<UiLayout>; display: Partial<UiDisplay> }>;
      return {
        layout: { ...DEFAULT_LAYOUT, ...(parsed.layout ?? {}) },
        display: { ...DEFAULT_DISPLAY, ...(parsed.display ?? {}) },
      };
    }
  } catch {
    /* corrupt / unavailable storage */
  }
  return { layout: { ...DEFAULT_LAYOUT }, display: { ...DEFAULT_DISPLAY } };
}

function persist(layout: UiLayout, display: UiDisplay) {
  try { localStorage.setItem(KEY, JSON.stringify({ layout, display })); } catch { /* ignore */ }
}

/** Reflect the display switches on <html> so CSS (tokens, row height) follows them. */
export function applyDisplayAttributes(display: UiDisplay) {
  const root = document.documentElement;
  if (!root) return;
  if (display.colorBlind) root.setAttribute("data-cb", "1");
  else root.removeAttribute("data-cb");
  if (display.compactRows) root.setAttribute("data-compact", "1");
  else root.removeAttribute("data-compact");
}

const initial = load();

export const useUiStore = create<UiState>((set, get) => ({
  layout: initial.layout,
  display: initial.display,
  activityOpen: false,

  setLayout: (key, value) => {
    const layout = { ...get().layout, [key]: value };
    persist(layout, get().display);
    set({ layout });
  },

  setDisplay: (key, value) => {
    const display = { ...get().display, [key]: value };
    persist(get().layout, display);
    try { applyDisplayAttributes(display); } catch { /* no DOM */ }
    set({ display });
  },

  setActivityOpen: (activityOpen) => set({ activityOpen }),

  resetAll: () => {
    const layout = { ...DEFAULT_LAYOUT };
    const display = { ...DEFAULT_DISPLAY };
    persist(layout, display);
    try { applyDisplayAttributes(display); } catch { /* no DOM */ }
    set({ layout, display });
  },
}));

export function initUiDisplay() {
  try { applyDisplayAttributes(useUiStore.getState().display); } catch { /* no DOM */ }
}
