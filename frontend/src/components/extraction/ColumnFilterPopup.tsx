/**
 * ColumnFilterPopup — Google Sheets-style per-column distinct-value filter
 * (Rule 49 §E). Anchored to the right-click mouse position.
 *
 * Auto-mode: ≤10 distinct values → checkbox mode (no search input).
 *            >10 distinct values → search mode (search input at top).
 */

import {useCallback, useEffect, useMemo, useRef, useState} from "react";
import type {CSSProperties} from "react";
import {createPortal} from "react-dom";
import {Glass} from "@/components/glass";

export interface ColumnFilterPopupProps {
  columnLabel: string;
  values: string[];
  /** Per-value row counts across the full column (not just distinct values). */
  valueCounts?: Record<string, number>;
  selected: Set<string>;
  x: number;
  y: number;
  onApply: (selected: Set<string>) => void;
  onCancel: () => void;
}

export function ColumnFilterPopup({
  columnLabel,
  values,
  valueCounts: valueCountsProp,
  selected,
  x,
  y,
  onApply,
  onCancel,
}: ColumnFilterPopupProps) {
  const [draft, setDraft] = useState<Set<string>>(() => new Set(selected));
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);
  const selectAllRef = useRef<HTMLInputElement | null>(null);

  const isSearchMode = values.length > 10;

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
    if (!isSearchMode) return values;
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return values;
    return values.filter((v) => v.toLocaleLowerCase().includes(needle));
  }, [values, search, isSearchMode]);

  const allVisibleSelected = filteredValues.length > 0 && filteredValues.every((v) => draft.has(v));
  const someVisibleSelected = filteredValues.some((v) => draft.has(v));

  useEffect(() => {
    const el = selectAllRef.current;
    if (!el) return;
    el.indeterminate = someVisibleSelected && !allVisibleSelected;
  }, [someVisibleSelected, allVisibleSelected]);

  const toggle = useCallback((value: string) => {
    setDraft((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setDraft((prev) => {
      const next = new Set(prev);
      if (filteredValues.every((v) => next.has(v))) {
        for (const v of filteredValues) next.delete(v);
      } else {
        for (const v of filteredValues) next.add(v);
      }
      return next;
    });
  }, [filteredValues]);

  const valueCounts = useMemo(() => {
    if (valueCountsProp) return valueCountsProp;
    // Callers that pass already-distinct `values` without row totals would
    // otherwise show a fake "(1)" for every option. Prefer an empty map so
    // the count badge is omitted until real counts are supplied.
    return {} as Record<string, number>;
  }, [valueCountsProp]);

  const style = useMemo<CSSProperties>(() => {
    const maxX = typeof window !== "undefined" ? window.innerWidth - 340 : x;
    const maxY = typeof window !== "undefined" ? window.innerHeight - 400 : y;
    return {
      position: "fixed",
      left: Math.max(8, Math.min(x, maxX)),
      top: Math.max(8, Math.min(y, maxY)),
      width: "320px",
      zIndex: 9999,
    };
  }, [x, y]);

  const popup = (
    <Glass className="shadow-2xl" ref={ref} style={style}  role="dialog">
      <div className="border-b border-white/10 px-3 py-2">
        <div className="kicker">Filter</div>
        <div className="text-sm text-ink">{columnLabel}</div>
        {selected.size > 0 && (
          <span className="text-xs muted">{selected.size} of {values.length} selected</span>
        )}
      </div>

      <div className="space-y-2 px-3 py-2">
        {isSearchMode && (
          <input
            type="text"
            placeholder="Search values…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input-glass h-8 w-full text-xs"
            autoFocus
            data-testid="filter-search-input"
          />
        )}

        <div
          className="max-h-56 overflow-auto rounded border border-white/5"
          data-testid={isSearchMode ? "filter-mode-search" : "filter-mode-checkbox"}
        >
          <label className="flex cursor-pointer items-center gap-2 border-b border-white/5 px-2 py-1 text-xs text-ink hover:bg-white/5">
            <input
              ref={selectAllRef}
              type="checkbox"
              checked={allVisibleSelected}
              onChange={toggleSelectAll}
              className="h-3 w-3 accent-biu-sky"
              data-testid="filter-select-all-checkbox"
            />
            <span className="flex-1 font-medium">(Select all)</span>
          </label>

          {filteredValues.length === 0 ? (
            <div className="px-3 py-4 text-center text-xs muted">No values match.</div>
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
                <span className="flex-1 truncate" title={value}>{value || "(blank)"}</span>
                {typeof valueCounts[value] === "number" && (
                  <span className="muted">({valueCounts[value]})</span>
                )}
              </label>
            ))
          )}
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-white/10 px-3 py-2">
        <button type="button" onClick={onCancel} className="button-ghost h-7 px-3 text-xs">Cancel</button>
        <button type="button" onClick={() => onApply(draft)} className="button-primary h-7 px-3 text-xs">Apply</button>
      </div>
    </Glass>
  );

  if (typeof document === "undefined") return popup;
  return createPortal(popup, document.body);
}
