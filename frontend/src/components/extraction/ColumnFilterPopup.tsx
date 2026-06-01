/**
 * ColumnFilterPopup — Excel-style per-column distinct-value filter
 * (Rule 49 §E). Anchored to the right-click mouse position. Provides
 * select-all / clear-all, free-text search, per-value checkboxes with
 * counts, and Apply / Cancel.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

export interface ColumnFilterPopupProps {
  columnLabel: string;
  values: string[];
  selected: Set<string>;
  x: number;
  y: number;
  onApply: (selected: Set<string>) => void;
  onCancel: () => void;
}

export function ColumnFilterPopup({
  columnLabel,
  values,
  selected,
  x,
  y,
  onApply,
  onCancel,
}: ColumnFilterPopupProps) {
  const [draft, setDraft] = useState<Set<string>>(() => new Set(selected));
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setDraft(new Set(selected));
  }, [selected]);

  useEffect(() => {
    function onDocClick(event: MouseEvent) {
      const node = ref.current;
      if (node && event.target instanceof Node && !node.contains(event.target)) {
        onCancel();
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onCancel();
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [onCancel]);

  const filteredValues = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return values;
    return values.filter((v) => v.toLocaleLowerCase().includes(needle));
  }, [values, search]);

  const toggle = useCallback((value: string) => {
    setDraft((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setDraft(new Set(filteredValues));
  }, [filteredValues]);

  const clear = useCallback(() => {
    setDraft(new Set());
  }, []);

  const valueCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const v of values) counts[v] = (counts[v] ?? 0) + 1;
    return counts;
  }, [values]);

  const style = useMemo<CSSProperties>(() => {
    const maxX = typeof window !== "undefined" ? window.innerWidth - 320 : x;
    const maxY = typeof window !== "undefined" ? window.innerHeight - 360 : y;
    return {
      position: "fixed",
      left: Math.max(8, Math.min(x, maxX)),
      top: Math.max(8, Math.min(y, maxY)),
      width: "300px",
      zIndex: 100,
    };
  }, [x, y]);

  return (
    <div ref={ref} style={style} className="glass shadow-lg" role="dialog">
      <div className="border-b border-white/10 px-3 py-2">
        <div className="kicker">Filter</div>
        <div className="text-sm text-ink">{columnLabel}</div>
      </div>
      <div className="space-y-2 px-3 py-2">
        <input
          type="text"
          placeholder="Search values…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input-glass h-8 w-full text-xs"
          autoFocus
        />
        <div className="flex items-center justify-between text-xs">
          <button
            type="button"
            onClick={selectAll}
            className="button-ghost h-7 px-2 text-xs"
          >
            Select all
          </button>
          <button
            type="button"
            onClick={clear}
            className="button-ghost h-7 px-2 text-xs"
          >
            Clear
          </button>
        </div>
        <div className="max-h-56 overflow-auto rounded border border-white/5">
          {filteredValues.length === 0 ? (
            <div className="px-3 py-4 text-center text-xs muted">
              No values match.
            </div>
          ) : (
            filteredValues.map((value) => (
              <label
                key={value || "_blank"}
                className="flex cursor-pointer items-center gap-2 px-2 py-1 text-xs text-ink hover:bg-white/5"
              >
                <input
                  type="checkbox"
                  checked={draft.has(value)}
                  onChange={() => toggle(value)}
                  className="h-3 w-3 accent-biu-sky"
                />
                <span className="flex-1 truncate" title={value}>
                  {value || "(blank)"}
                </span>
                <span className="muted">({valueCounts[value] ?? 0})</span>
              </label>
            ))
          )}
        </div>
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-white/10 px-3 py-2">
        <button
          type="button"
          onClick={onCancel}
          className="button-ghost h-7 px-3 text-xs"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onApply(draft)}
          className="button-primary h-7 px-3 text-xs"
        >
          Apply
        </button>
      </div>
    </div>
  );
}
