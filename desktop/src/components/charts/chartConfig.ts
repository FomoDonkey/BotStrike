// Shared chart configuration (non-component module so react-refresh stays happy).

export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h";

export const TIMEFRAMES: readonly Timeframe[] = ["1m", "5m", "15m", "1h", "4h"];

export const TF_SECONDS: Record<Timeframe, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "4h": 14400,
};

/** Shared chart chrome so the indicator pane matches the main chart pixel for pixel. */
export const CHART_THEME = {
  textColor: "rgba(255,255,255,0.6)",
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 11,
  grid: "rgba(255,255,255,0.04)",
  border: "rgba(255,255,255,0.12)",
  crosshair: "rgba(255,255,255,0.35)",
  labelBg: "#171717",
  /** right price scale width forced equal on every pane */
  priceScaleWidth: 76,
} as const;
