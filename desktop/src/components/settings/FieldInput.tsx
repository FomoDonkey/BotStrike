import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ConfigField, ConfigScalar } from "@/lib/api";
import { fieldLabel, isSameValue, trimNumber } from "./schemaUtils";

export const INPUT_CLS =
  "w-full bg-bg-base border border-white/10 rounded-lg px-3 py-1.5 text-sm text-text-primary font-mono focus:outline-none focus:border-accent/50 disabled:opacity-50";

interface FieldInputProps {
  field: ConfigField;
  /** Current draft value (or the config value when untouched) */
  value: ConfigScalar | undefined;
  /** Value from GET /api/config — used for the dirty marker */
  original: ConfigScalar | undefined;
  /** Set by the user earlier (data/config_overrides.json) */
  overridden?: boolean;
  error?: string;
  disabled?: boolean;
  /** Bump to resync the text of numeric inputs with `value` (revert / save / discard) */
  resetToken: number;
  onChange: (v: ConfigScalar) => void;
  onRevert: () => void;
}

/** One schema field: label + help + restart badge on the left, the typed control on the right. */
export function FieldInput({ field, value, original, overridden, error, disabled, resetToken, onChange, onRevert }: FieldInputProps) {
  const dirty = !isSameValue(value, original);
  return (
    <div
      className={cn(
        "py-3 border-b border-white/[0.04] flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-6 -mx-2 px-2 rounded-lg",
        dirty && "bg-accent/[0.03]",
      )}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={cn("text-sm", dirty ? "text-text-primary" : "text-text-secondary")}>{fieldLabel(field)}</span>
          {field.restart_required && (
            <span
              className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider bg-warning/10 text-warning"
              title="Applies after an engine restart"
            >
              restart
            </span>
          )}
          {overridden && !dirty && (
            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider bg-info/10 text-info" title="Overridden by you">
              custom
            </span>
          )}
          {dirty && (
            <button
              type="button"
              onClick={onRevert}
              className="text-text-muted hover:text-text-secondary"
              title="Revert this change"
              aria-label="Revert"
            >
              <RotateCcw className="w-3 h-3" />
            </button>
          )}
        </div>
        {field.help && <p className="text-[11px] text-text-muted mt-0.5 leading-snug">{field.help}</p>}
        {error && <p className="text-[11px] text-loss mt-1 font-mono">{error}</p>}
      </div>
      <div className="w-full sm:w-60 shrink-0">
        <Control key={resetToken} field={field} value={value} disabled={disabled} invalid={!!error} onChange={onChange} />
      </div>
    </div>
  );
}

interface ControlProps {
  field: ConfigField;
  value: ConfigScalar | undefined;
  disabled?: boolean;
  invalid?: boolean;
  onChange: (v: ConfigScalar) => void;
}

function Control(props: ControlProps) {
  const { field, value, disabled, invalid, onChange } = props;
  switch (field.type) {
    case "bool":
      return <Toggle checked={value === true} disabled={disabled} onChange={onChange} />;
    case "select":
      return (
        <select
          value={value === undefined || value === null ? "" : String(value)}
          disabled={disabled}
          onChange={(e) => {
            const opt = field.options?.find((o) => String(o.value) === e.target.value);
            onChange(opt ? opt.value : e.target.value);
          }}
          className={cn(INPUT_CLS, invalid && "border-loss/50")}
        >
          {(field.options ?? []).map((o) => (
            <option key={String(o.value)} value={String(o.value)}>{o.label}</option>
          ))}
        </select>
      );
    case "list":
      return <ListControl {...props} />;
    case "string":
      return (
        <input
          type="text"
          value={value === undefined || value === null ? "" : String(value)}
          disabled={disabled}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => onChange(e.target.value)}
          className={cn(INPUT_CLS, invalid && "border-loss/50")}
        />
      );
    default:
      return <NumberControl {...props} />;
  }
}

function Toggle({ checked, disabled, onChange }: { checked: boolean; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between sm:justify-end gap-3 h-8">
      <span className="text-xs font-mono text-text-muted">{checked ? "ON" : "OFF"}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn("w-10 h-5 rounded-full transition-all relative disabled:opacity-50", checked ? "bg-accent" : "bg-white/10")}
      >
        <span className={cn("absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all", checked ? "left-[22px]" : "left-0.5")} />
      </button>
    </div>
  );
}

/** Comma-separated list. Sent back as an array when the config value is one, else as a string. */
function ListControl({ value, disabled, invalid, onChange }: ControlProps) {
  const asArray = Array.isArray(value);
  const [text, setText] = useState(() => (Array.isArray(value) ? value.join(", ") : value === undefined || value === null ? "" : String(value)));
  return (
    <input
      type="text"
      value={text}
      disabled={disabled}
      spellCheck={false}
      autoComplete="off"
      placeholder="a, b, c"
      onChange={(e) => {
        setText(e.target.value);
        const items = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
        onChange(asArray ? items : items.join(","));
      }}
      className={cn(INPUT_CLS, invalid && "border-loss/50")}
    />
  );
}

/** number / int / percent — percent is stored 0–1 and edited as 0–100. */
function NumberControl({ field, value, disabled, invalid, onChange }: ControlProps) {
  const isPct = field.type === "percent";
  const [text, setText] = useState(() => (typeof value === "number" && Number.isFinite(value) ? trimNumber(isPct ? value * 100 : value) : ""));
  const unit = isPct ? "%" : field.unit;
  const scale = (n: number | undefined) => (n === undefined ? undefined : isPct ? Number(trimNumber(n * 100)) : n);
  const step = field.step !== undefined ? scale(field.step) : field.type === "int" ? 1 : "any";

  return (
    <div className="relative">
      <input
        type="number"
        inputMode="decimal"
        value={text}
        disabled={disabled}
        min={scale(field.min)}
        max={scale(field.max)}
        step={step}
        onChange={(e) => {
          const t = e.target.value;
          setText(t);
          if (t.trim() === "") {
            onChange(Number.NaN);
            return;
          }
          const n = Number(t);
          if (!Number.isFinite(n)) return;
          onChange(isPct ? Number((n / 100).toFixed(8)) : n);
        }}
        className={cn(INPUT_CLS, unit && "pr-12", invalid && "border-loss/50")}
      />
      {unit && (
        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-text-muted pointer-events-none">{unit}</span>
      )}
    </div>
  );
}
