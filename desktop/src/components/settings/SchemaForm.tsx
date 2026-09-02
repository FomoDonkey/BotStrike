import { useMemo, useState } from "react";
import { Loader2, Save, Undo2 } from "lucide-react";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { api, ApiError, type ConfigField, type ConfigGroup, type ConfigResponse, type ConfigScalar } from "@/lib/api";
import { cn } from "@/lib/utils";
import { FieldInput } from "./FieldInput";
import { buildUpdateBody, getConfigValue, isOverridden, isSameValue, parseFieldError, validateField } from "./schemaUtils";

interface SchemaFormProps {
  group: ConfigGroup;
  config: ConfigResponse;
  /** PUT succeeded — the parent stores the fresh config and shows the toast / restart banner */
  onSaved: (config: ConfigResponse, restartRequired: boolean, applied: string[]) => void;
}

interface Section {
  title: string | null;
  fields: ConfigField[];
}

/**
 * Generic editor for one schema group. Tracks a draft per path (only changed paths are kept),
 * validates client-side with the schema bounds, and sends ONLY the changed paths with
 * PUT /api/config. Server-side 400 details ("trading.x: must be…") land next to their field.
 */
export function SchemaForm({ group, config, onSaved }: SchemaFormProps) {
  const [draft, setDraft] = useState<Record<string, ConfigScalar>>({});
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [resetToken, setResetToken] = useState(0);

  // Per-symbol groups expand `symbols.{symbol}.x` into one section per configured symbol.
  const sections = useMemo<Section[]>(() => {
    if (group.per_symbol) {
      return config.symbols.map((sc) => ({
        title: sc.symbol,
        fields: group.fields.map((f) => ({ ...f, path: f.path.replace("{symbol}", sc.symbol) })),
      }));
    }
    return [{ title: null, fields: group.fields }];
  }, [group, config]);

  const fieldByPath = useMemo(() => {
    const m = new Map<string, ConfigField>();
    for (const s of sections) for (const f of s.fields) m.set(f.path, f);
    return m;
  }, [sections]);

  const errors = useMemo(() => {
    const out: Record<string, string> = { ...serverErrors };
    for (const [path, v] of Object.entries(draft)) {
      const f = fieldByPath.get(path);
      const e = f ? validateField(f, v) : null;
      if (e) out[path] = e;
    }
    return out;
  }, [draft, serverErrors, fieldByPath]);

  const dirtyPaths = Object.keys(draft);
  const hasErrors = Object.keys(errors).length > 0;

  const change = (path: string, v: ConfigScalar) => {
    setServerErrors((e) => {
      if (!(path in e)) return e;
      const { [path]: _drop, ...rest } = e;
      return rest;
    });
    setDraft((d) => {
      if (isSameValue(getConfigValue(config, path), v)) {
        const { [path]: _drop, ...rest } = d;
        return rest;
      }
      return { ...d, [path]: v };
    });
  };

  const revert = (path: string) => {
    setDraft((d) => {
      const { [path]: _drop, ...rest } = d;
      return rest;
    });
    setServerErrors((e) => {
      const { [path]: _drop, ...rest } = e;
      return rest;
    });
    setResetToken((t) => t + 1);
  };

  const discard = () => {
    setDraft({});
    setServerErrors({});
    setFormError(null);
    setResetToken((t) => t + 1);
  };

  const save = async () => {
    if (hasErrors || dirtyPaths.length === 0 || saving) return;
    setSaving(true);
    setFormError(null);
    setServerErrors({});
    try {
      const res = await api.configUpdate(buildUpdateBody(draft));
      setDraft({});
      setResetToken((t) => t + 1);
      onSaved(res.config, res.restart_required, res.applied ?? []);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e);
      const fe = parseFieldError(msg);
      if (fe && fieldByPath.has(fe.path)) {
        setServerErrors({ [fe.path]: fe.message });
      } else if (e instanceof ApiError && e.isAuth) {
        setFormError(`${msg} — set the bridge auth token in Settings → Connection`);
      } else {
        setFormError(msg);
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      {sections.map((sec) => (
        <GlassPanel key={sec.title ?? group.id} className="p-4 sm:p-5">
          {sec.title && <h3 className="text-sm font-mono font-bold text-text-primary mb-1">{sec.title}</h3>}
          {sec.fields.length === 0 ? (
            <p className="text-xs text-text-muted py-2">No editable fields in this group.</p>
          ) : (
            sec.fields.map((f) => {
              const original = getConfigValue(config, f.path);
              const value = f.path in draft ? draft[f.path] : original;
              return (
                <FieldInput
                  key={f.path}
                  field={f}
                  value={value}
                  original={original}
                  overridden={isOverridden(config, f.path)}
                  error={errors[f.path]}
                  disabled={saving}
                  resetToken={resetToken}
                  onChange={(v) => change(f.path, v)}
                  onRevert={() => revert(f.path)}
                />
              );
            })
          )}
        </GlassPanel>
      ))}

      {/* Sticky action bar */}
      <div
        className={cn(
          "sticky bottom-0 z-10 flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3 backdrop-blur-xl transition-colors",
          dirtyPaths.length > 0 ? "bg-bg-surface/90 border-accent/30" : "bg-bg-surface/70 border-white/5",
        )}
      >
        <span className="text-xs text-text-muted">
          {dirtyPaths.length === 0
            ? "No pending changes"
            : `${dirtyPaths.length} pending change${dirtyPaths.length === 1 ? "" : "s"}`}
          {hasErrors && <span className="text-loss ml-2">· fix the highlighted fields</span>}
        </span>
        {formError && <span className="text-xs font-mono text-loss basis-full sm:basis-auto">{formError}</span>}
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={discard}
            disabled={dirtyPaths.length === 0 || saving}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-white/10 text-sm text-text-secondary hover:border-white/20 disabled:opacity-40 transition-all"
          >
            <Undo2 className="w-3.5 h-3.5" /> Discard
          </button>
          <button
            type="button"
            onClick={save}
            disabled={dirtyPaths.length === 0 || hasErrors || saving}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-accent text-bg-base text-sm font-semibold hover:bg-accent/90 disabled:opacity-40 transition-all"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
