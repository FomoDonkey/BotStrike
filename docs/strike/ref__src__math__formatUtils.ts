/**
 * Format a price for display with dynamic decimal places based on magnitude.
 *
 * @example formatPrice(43250.5)  -> "43,251"   (>= 10000: 0 decimals)
 * @example formatPrice(1250.75)  -> "1,250.8"  (>= 1000: 1 decimal)
 * @example formatPrice(125.456)  -> "125.46"   (>= 100: 2 decimals)
 * @example formatPrice(12.3456)  -> "12.346"   (>= 10: 3 decimals)
 * @example formatPrice(1.23456)  -> "1.2346"   (>= 1: 4 decimals)
 * @example formatPrice(0.005678) -> "0.0056780" (< 1: 5 sig figs)
 */
export function formatPrice(price: number): string {
  if (!Number.isFinite(price)) return "--";
  const abs = Math.abs(price);

  let decimals: number;
  if (abs >= 10000) decimals = 0;
  else if (abs >= 1000) decimals = 1;
  else if (abs >= 100) decimals = 2;
  else if (abs >= 10) decimals = 3;
  else if (abs >= 1) decimals = 4;
  else {
    // < 1: show 5 significant figures
    return price.toPrecision(5);
  }

  return price.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/**
 * Format a USD currency value (2 decimal places).
 * @example formatCurrency(1075.5)  -> "$1,075.50"
 * @example formatCurrency(250)     -> "$250.00"
 */
export function formatCurrency(value: number): string {
  if (!Number.isFinite(value)) return "--";
  const floored = Math.floor(value * 100) / 100;
  return "$" + floored.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * Format a signed currency value with +/- prefix.
 * @example formatSignedCurrency(250)   -> "+$250.00"
 * @example formatSignedCurrency(-12.5) -> "-$12.50"
 */
export function formatSignedCurrency(value: number): string {
  if (!Number.isFinite(value)) return "--";
  const abs = Math.abs(value);
  const floored = Math.floor(abs * 100) / 100;
  const formatted = floored.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return (value >= 0 ? "+" : "-") + "$" + formatted;
}

/**
 * Format a signed percentage.
 * @example formatSignedPercent(23.26)  -> "+23.26%"
 * @example formatSignedPercent(-5.5)   -> "-5.50%"
 */
export function formatSignedPercent(value: number): string {
  if (!Number.isFinite(value)) return "--";
  const sign = value >= 0 ? "+" : "";
  return sign + value.toFixed(2) + "%";
}

/**
 * Format a position size with precision.
 * @example formatSize(0.5, 4) -> "0.5000"
 * @example formatSize(1.2345678, 4) -> "1.2345"
 */
export function formatSize(value: number, basePrec: number = 4): string {
  if (!Number.isFinite(value)) return "--";
  return value.toFixed(basePrec);
}

/**
 * Snap a value to the nearest valid step size (floored).
 * @example snapToStep(0.5123, 0.0001, 4) -> 0.5123
 * @example snapToStep(0.5127, 0.001, 3)  -> 0.512
 * @example snapToStep(43001.57, 0.01, 2) -> 43001.57
 */
export function snapToStep(
  value: number,
  stepSize: number,
  precision: number
): number {
  if (stepSize <= 0) return value;
  const snapped = Math.floor(value / stepSize) * stepSize;
  return parseFloat(snapped.toFixed(precision));
}

/**
 * Format size for API submission (string, no trailing artifacts).
 * @example formatSizeForApi(0.5, 4) -> "0.5"
 */
export function formatSizeForApi(value: number, basePrec: number): string {
  return Number(value.toFixed(basePrec)).toString();
}
