import { useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { Hint } from "@/components/shared/Hint";
import { EmptyState } from "./Panel";

export interface Column<T> {
  id: string;
  label: ReactNode;
  hint?: string;
  align?: "l" | "r" | "c";
  /** Enables sorting on this column */
  sortValue?: (row: T) => number | string | null | undefined;
  render: (row: T, index: number) => ReactNode;
  className?: string;
  headerClassName?: string;
}

interface DataTableProps<T> {
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T, index: number) => string;
  /** Row class (e.g. highlight the chart symbol) */
  rowClassName?: (row: T, index: number) => string | undefined;
  onRowClick?: (row: T, index: number) => void;
  /** Extra row rendered right after a row (expanded detail) */
  renderDetail?: (row: T, index: number) => ReactNode;
  emptyText?: ReactNode;
  emptySub?: ReactNode;
  minWidth?: string;
  className?: string;
  defaultSort?: { id: string; dir: "asc" | "desc" };
  /** Rows to show (after sorting) */
  limit?: number;
}

/**
 * Sticky-header, tabular, hoverable, sortable table (spec §4 DataTable). Scrolls inside its own
 * container — the page body never scrolls sideways. Empty state in white.
 */
export function DataTable<T>({ columns, rows, rowKey, rowClassName, onRowClick, renderDetail, emptyText = "No rows", emptySub, minWidth, className, defaultSort, limit }: DataTableProps<T>) {
  const [sort, setSort] = useState<{ id: string; dir: "asc" | "desc" } | null>(defaultSort ?? null);

  const sorted = useMemo(() => {
    let list = rows as T[];
    if (sort) {
      const col = columns.find((c) => c.id === sort.id);
      if (col?.sortValue) {
        const sv = col.sortValue;
        const dir = sort.dir === "asc" ? 1 : -1;
        list = [...rows].sort((a, b) => {
          const va = sv(a);
          const vb = sv(b);
          if (va === vb) return 0;
          if (va === null || va === undefined) return 1;
          if (vb === null || vb === undefined) return -1;
          if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
          return String(va).localeCompare(String(vb)) * dir;
        });
      }
    }
    return typeof limit === "number" ? list.slice(0, limit) : list;
  }, [rows, sort, columns, limit]);

  if (rows.length === 0) {
    return <EmptyState sub={emptySub}>{emptyText}</EmptyState>;
  }

  const toggleSort = (c: Column<T>) => {
    if (!c.sortValue) return;
    setSort((s) => (s?.id === c.id ? { id: c.id, dir: s.dir === "asc" ? "desc" : "asc" } : { id: c.id, dir: "desc" }));
  };

  return (
    <div className={cn("overflow-auto flex-1 min-h-0", className)}>
      <table className="term-table" style={minWidth ? { minWidth } : undefined}>
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.id === c.id;
              return (
                <th
                  key={c.id}
                  className={cn(c.align ?? "r", c.sortValue && "sortable", c.headerClassName)}
                  onClick={() => toggleSort(c)}
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                >
                  <span className="inline-flex items-center gap-1">
                    {c.hint ? <Hint title={c.hint}>{c.label}</Hint> : c.label}
                    {active && (sort!.dir === "asc" ? <ArrowUp className="w-3 h-3 text-mint" /> : <ArrowDown className="w-3 h-3 text-mint" />)}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => {
            const key = rowKey(row, i);
            const detail = renderDetail?.(row, i);
            return (
              <RowPair key={key}>
                <tr className={cn(onRowClick && "cursor-pointer", rowClassName?.(row, i))} onClick={onRowClick ? () => onRowClick(row, i) : undefined}>
                  {columns.map((c) => (
                    <td key={c.id} className={cn(c.align ?? "r", c.className)}>{c.render(row, i)}</td>
                  ))}
                </tr>
                {detail && (
                  <tr className="detail">
                    <td colSpan={columns.length}>{detail}</td>
                  </tr>
                )}
              </RowPair>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RowPair({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

/** Label/value cell of an expanded detail grid. */
export function DetailCell({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: "mint" | "rose" }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-[0.04em] text-text-2 truncate">{hint ? <Hint title={hint}>{label}</Hint> : label}</p>
      <p className={cn("num text-[12.5px] font-semibold truncate", tone === "mint" && "text-mint", tone === "rose" && "text-rose", !tone && "text-text")}>{value}</p>
    </div>
  );
}
