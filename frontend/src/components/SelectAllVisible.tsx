/**
 * SelectAllVisible — shared bulk-selection control used above every
 * filterable table that supports multi-select.
 *
 * Three states:
 *   • none   — no visible row is selected  →  blank ☐
 *   • some   — some but not all are        →  indeterminate ▣ (CSS)
 *   • all    — every visible row is        →  checked ☑
 *
 * Clicking the checkbox always operates on the CURRENT visible set
 * (i.e. post-filter). Hidden rows are never touched, so the user can
 * filter to a subset, bulk-approve those, change the filter, and the
 * rest of the run stays as it was.
 *
 *   <SelectAllVisible
 *     visibleCount={filtered.length}
 *     selectedCount={selectedInVisible}
 *     onSelectAll={() => setSelected(new Set([...selected, ...filtered.map((m) => m.id)]))}
 *     onClear={() => setSelected(new Set())} />
 */

import { useEffect, useRef } from "react";


export function SelectAllVisible({
  visibleCount,
  selectedCount,
  onSelectAll,
  onClear,
  label = "rows",
}: {
  /** number of rows after every active filter. */
  visibleCount: number;
  /** number of rows that ARE both visible AND in the selected set. */
  selectedCount: number;
  /** add every visible row to the selection. */
  onSelectAll: () => void;
  /** drop every visible row from the selection. */
  onClear: () => void;
  /** noun for the label — "matches", "statements", "items". */
  label?: string;
}) {
  const ref = useRef<HTMLInputElement | null>(null);

  // Indeterminate is a DOM-only property — React doesn't bind it.
  const all  = visibleCount > 0 && selectedCount === visibleCount;
  const some = selectedCount > 0 && selectedCount < visibleCount;
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = some;
  }, [some]);

  function handle() {
    if (all) onClear();
    else     onSelectAll();
  }

  return (
    <label className="inline-flex items-center gap-2 cursor-pointer select-none
                      glass-pill px-3 py-1 text-xs hover:bg-white/5 transition">
      <input ref={ref} type="checkbox" checked={all} onChange={handle}
             className="cursor-pointer" />
      <span className="muted">
        {selectedCount === 0
          ? <>Select all <b className="text-ink">{visibleCount}</b> visible {label}</>
          : selectedCount === visibleCount
            ? <>All <b className="text-ink">{visibleCount}</b> visible {label} selected</>
            : <>{selectedCount} of <b className="text-ink">{visibleCount}</b> visible {label} selected</>}
      </span>
      {selectedCount > 0 && (
        <button
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onClear(); }}
          className="text-biu-sky hover:underline ml-1"
          title="Clear selection">
          clear
        </button>
      )}
    </label>
  );
}
