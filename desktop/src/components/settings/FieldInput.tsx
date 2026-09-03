import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ConfigField, ConfigScalar } from "@/lib/api";
import { fieldLabel, isSameValue, trimNumber } from "./schemaUtils";
import { Switch } from "@/components/ui/Switch";
import { Chip } from "@/components/ui/Chip";

/** White text on `--panel-2`, hairline border, mint focus ring (spec §3.7). */
export const INPUT_CLS =
  "w-full h-8 bg-panel-2 border border-hairline rounded-[6px] px-2.5 text-[13px] font-medium text-text num placeholder:text-text-3 focus:outline-none focus:border-mint disabled:opacity-50";

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

/** One schema field: `label · help · control` row (spec §3.7). */
export function FieldInput({ field, value, original, overridden, error, disabled, resetToken, onChange, onRevert }: FieldInputProps) {
  const dirty = !isSameValue(value, original);
  return (
    <div className={cn("py-3 border-b border-hairline-soft last:border-b-0 flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-6 -mx-2 px-2 rounded-[6px]", dirty && "bg-mint-soft/40")}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[13px] font-semibold text-text">{fieldLabel(field)}</span>
          {field.restart_required && <Chip tone="amber" size="xs" title="Applies after an engine restart">restart</Chip>}
          {overridden && !dirty && <Chip tone="blue" size="xs" title="Overridden by you">custom</Chip>}
          {dirty && (
            <button type="button" onClick={onRevert} className="inline-flex items-center justify-center w-6 h-6 rounded-[6px] text-text hover:bg-hover" title="Revert this change" aria-label="Revert">
              <RotateCcw className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        {field.help && <p className="text-[12.5px] font-medium text-text-2 mt-0.5 leading-snug">{field.help}</p>}
        {error && <p className="text-[12.5px] font-medium text-rose mt-1">{error}</p>}
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
      return (
        <div className="flex items-center justify-between sm:justify-end gap-3 h-8">
          <span className="text-[12.5px] font-semibold text-text">{value === true ? "ON" : "OFF"}</span>
          <Switch checked={value === true} disabled={disabled} onChange={onChange} label={fieldLabel(field)} />
        </div>
      );
    case "select":
      return (
        <select
          value={value === undefined || value === null ? "" : String(value)}
          disabled={disabled}
          onChange={(e) => {
            const opt = field.options?.find((o) => String(o.value) === e.target.value);
            onChange(opt ? opt.value : e.target.value);
          }}
          className={cn(INPUT_CLS, "bs-select", invalid && "border-rose")}
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
          className={cn(INPUT_CLS, invalid && "border-rose")}
        />
      );
    default:
      return <NumberControl {...props} />;
  }
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
      className={cn(INPUT_CLS, invalid && "border-rose")}
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
        className={cn(INPUT_CLS, unit && "pr-12", invalid && "border-rose")}
      />
      {unit && <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[12px] font-medium text-text-2 pointer-events-none">{unit}</span>}
    </div>
  );
}
