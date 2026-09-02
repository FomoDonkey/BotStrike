import type { ConfigField, ConfigResponse, ConfigScalar, ConfigUpdateRequest } from "@/lib/api";

/** Human label for a field without one: `max_drawdown_pct` → "Max drawdown pct". */
export function fieldLabel(f: ConfigField): string {
  if (f.label) return f.label;
  const key = f.path.split(".").pop() ?? f.path;
  return key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** Drop float noise: 0.085000000001 → "0.085". */
export function trimNumber(n: number): string {
  return Number(n.toFixed(6)).toString();
}

/**
 * Resolve a schema path against GET /api/config.
 *   trading.max_drawdown_pct → config.trading.max_drawdown_pct
 *   symbols.BTC-USD.leverage → config.symbols[symbol === "BTC-USD"].leverage  (array on the wire)
 */
export function getConfigValue(config: ConfigResponse, path: string): ConfigScalar | undefined {
  const parts = path.split(".");
  if (parts[0] === "symbols" && parts.length >= 3) {
    const sc = config.symbols.find((s) => s.symbol === parts[1]);
    return sc?.[parts.slice(2).join(".")] as ConfigScalar | undefined;
  }
  let cur: unknown = config;
  for (const p of parts) {
    if (!cur || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur as ConfigScalar | undefined;
}

/** Is this path overridden by the user (data/config_overrides.json)? */
export function isOverridden(config: ConfigResponse, path: string): boolean {
  const ov = config.overrides;
  if (!ov) return false;
  const parts = path.split(".");
  if (parts[0] === "symbols" && parts.length >= 3) {
    return parts[2] in (ov.symbols?.[parts[1]] ?? {});
  }
  const root = (ov as Record<string, unknown>)[parts[0]];
  return !!root && typeof root === "object" && parts[1] in (root as Record<string, unknown>);
}

export function isSameValue(a: ConfigScalar | undefined, b: ConfigScalar | undefined): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    const la = Array.isArray(a) ? a.join(",") : String(a ?? "");
    const lb = Array.isArray(b) ? b.join(",") : String(b ?? "");
    return la === lb;
  }
  if (typeof a === "number" && typeof b === "number") return Object.is(a, b) || Math.abs(a - b) < 1e-12;
  return a === b;
}

function fmtBound(f: ConfigField, n: number): string {
  if (f.type === "percent") return `${trimNumber(n * 100)}%`;
  return `${trimNumber(n)}${f.unit ? ` ${f.unit}` : ""}`;
}

/** Client-side mirror of the bridge validation → inline error before the round-trip. */
export function validateField(f: ConfigField, v: ConfigScalar | undefined): string | null {
  if (f.type === "number" || f.type === "int" || f.type === "percent") {
    if (typeof v !== "number" || !Number.isFinite(v)) return "Enter a number";
    if (f.type === "int" && !Number.isInteger(v)) return "Must be a whole number";
    if (f.min !== undefined && v < f.min) return `Must be ≥ ${fmtBound(f, f.min)}`;
    if (f.max !== undefined && v > f.max) return `Must be ≤ ${fmtBound(f, f.max)}`;
  }
  if (f.type === "select" && f.options?.length) {
    if (!f.options.some((o) => String(o.value) === String(v))) return "Pick one of the options";
  }
  return null;
}

/** Changed paths → PUT /api/config body: {trading: {...}, symbols: {SYM: {...}}}. */
export function buildUpdateBody(draft: Record<string, ConfigScalar>): ConfigUpdateRequest {
  const body: ConfigUpdateRequest = {};
  for (const [path, value] of Object.entries(draft)) {
    const parts = path.split(".");
    if (parts[0] === "symbols" && parts.length >= 3) {
      const symbols = (body.symbols ??= {});
      (symbols[parts[1]] ??= {})[parts.slice(2).join(".")] = value;
    } else if (parts.length >= 2) {
      const root = (body[parts[0]] ??= {}) as Record<string, ConfigScalar>;
      root[parts.slice(1).join(".")] = value;
    }
  }
  return body;
}

/**
 * "trading.max_drawdown_pct: must be between 0.01 and 0.5" → { path, message }
 * (FastAPI 400 detail from the contract). Null when the text carries no path prefix.
 */
export function parseFieldError(detail: string): { path: string; message: string } | null {
  const m = /^\s*([A-Za-z0-9_.{}-]+)\s*:\s*(.+)$/.exec(detail);
  if (!m || !m[1].includes(".")) return null;
  return { path: m[1], message: m[2].trim() };
}

/** Strategy type → the allocation field path the bridge exposes (`allocation_<type>`). */
export function allocationPath(type: string): string {
  return `trading.allocation_${type.toLowerCase()}`;
}
