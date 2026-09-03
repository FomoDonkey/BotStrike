// Shared chart configuration (non-component module so react-refresh stays happy).

export type Timeframe = "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "2h" | "4h";

/** Toolbar pills; the rest live under "More ▾". */
export const TIMEFRAMES: readonly Timeframe[] = ["1m", "5m", "15m", "1h", "4h"];
export const MORE_TIMEFRAMES: readonly Timeframe[] = ["3m", "30m", "2h"];

export const TF_SECONDS: Record<Timeframe, number> = {
  "1m": 60,
  "3m": 180,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1h": 3600,
  "2h": 7200,
  "4h": 14400,
};

/** Shared chart chrome so the indicator pane matches the main chart pixel for pixel. */
export const CHART_THEME = {
  textColor: "rgba(255,255,255,0.80)",
  fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
  fontSize: 11,
  grid: "rgba(255,255,255,0.06)",
  border: "rgba(255,255,255,0.18)",
  crosshair: "rgba(255,255,255,0.45)",
  labelBg: "#232323",
  /** right price scale width forced equal on every pane */
  priceScaleWidth: 76,
} as const;
