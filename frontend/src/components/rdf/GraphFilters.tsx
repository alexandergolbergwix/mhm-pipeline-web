/**
 * GraphFilters — chip-row filter bar above the RDF graph canvas.
 *
 * Sections: search · node-type chips · predicate chips
 *   (+ overflow popup) · SHACL-only toggle · neighbourhood radius.
 *
 * Visual parity with [EntityFilterChips] — same .glass wrapper,
 * .glass-pill text-xs chip, .input-glass h-8 search box, .kicker
 * w-16 row label. AND across dimensions; OR within each row.
 *
 * Pure presentational + state — the actual filter-set computation
 * lives in [useGraphFilters], so this component just renders and
 * forwards state changes.
 */

import { useEffect, useMemo, useState } from "react";

import type { GraphEdge, GraphNode } from "@/api/rdf";
import { ColumnFilterPopup } from "@/components/extraction/ColumnFilterPopup";
import { useDebounce } from "@/hooks/useDebounce";

import {
  emptyFilterState,
  type GraphFilterState,
} from "./useGraphFilters";

// Fixed node-type chips. Matches the LEGEND constant on StageRdf so
// the legend swatches and the type chips can't drift.
const NODE_TYPES = [
  "Manuscript", "Person", "Work", "Place",
  "Event", "Organization", "Codicological", "Other",
] as const;

const PREDICATE_CHIP_LIMIT = 8;

export interface GraphFiltersProps {
  nodes:        GraphNode[];
  edges:        GraphEdge[];
  shaclFocus:   Set<string>;          // empty ⇒ SHACL chip hidden
  selectedId:   string | null;        // null ⇒ neighbourhood row hidden
  state:        GraphFilterState;
  onChange:     (state: GraphFilterState) => void;
  visibleCount: number;
  totalCount:   number;
}

interface ChipProps {
  label:    string;
  active:   boolean;
  count?:   number;
  onToggle: () => void;
  testId?:  string;
}

function Chip({ label, active, count, onToggle, testId }: ChipProps) {
  return (
    <button type="button" onClick={onToggle}
      data-testid={testId}
      data-active={active}
      className={`glass-pill text-xs ${active ? "border-biu-sky text-biu-sky" : "muted"}`}
      aria-pressed={active}>
      {label}
      {count !== undefined && <span className="ml-1 opacity-70">({count})</span>}
    </button>
  );
}

export function GraphFilters({
  nodes,
  edges,
  shaclFocus,
  selectedId,
  state,
  onChange,
  visibleCount,
  totalCount,
}: GraphFiltersProps) {
  // Local draft keeps the input visually responsive on every keystroke.
  // The debounced value feeds the upstream filter call so the O(N+E)
  // pass only runs after the user pauses.
  const [searchDraft, setSearchDraft] = useState(state.query);
  const debouncedSearch = useDebounce(searchDraft, 150);

  useEffect(() => {
    onChange({ ...state, query: debouncedSearch });
    // state intentionally omitted — react only to debounced value changes,
    // not every render with a fresh state reference.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  // Keep the input synced if the parent resets ``state.query`` via
  // Clear-all.
  useEffect(() => {
    if (state.query === "" && searchDraft !== "") setSearchDraft("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.query]);

  // Per-type counts for the chip badges.
  const typeCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of nodes) c[n.type] = (c[n.type] ?? 0) + 1;
    return c;
  }, [nodes]);

  // Distinct predicate labels found on the loaded edges, sorted by
  // descending count. The first PREDICATE_CHIP_LIMIT render inline;
  // the rest are reachable via a "More…" popup.
  const predicateInfo = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of edges) {
      const lbl = e.predicate_label || e.predicate || "";
      if (!lbl) continue;
      c[lbl] = (c[lbl] ?? 0) + 1;
    }
    const ordered = Object.entries(c)
      .sort((a, b) => b[1] - a[1])
      .map(([label, count]) => ({ label, count }));
    return {
      all: ordered,
      head: ordered.slice(0, PREDICATE_CHIP_LIMIT),
      tail: ordered.slice(PREDICATE_CHIP_LIMIT),
    };
  }, [edges]);

  const [predOverflow, setPredOverflow] = useState<{ x: number; y: number } | null>(null);

  function toggleType(t: string) {
    const next = new Set(state.types);
    if (next.has(t)) next.delete(t); else next.add(t);
    onChange({ ...state, types: next });
  }
  function togglePredicate(p: string) {
    const next = new Set(state.predicates);
    if (next.has(p)) next.delete(p); else next.add(p);
    onChange({ ...state, predicates: next });
  }
  function toggleShacl() {
    onChange({ ...state, shaclOnly: !state.shaclOnly });
  }
  function setRadius(r: 0 | 1 | 2) {
    onChange({ ...state, radius: state.radius === r ? 0 : r });
  }
  function clearAll() {
    setSearchDraft("");
    onChange(emptyFilterState());
  }

  const anyActive =
    state.query.length > 0 ||
    state.types.size > 0 ||
    state.predicates.size > 0 ||
    state.shaclOnly ||
    state.radius > 0;

  return (
    <div className="glass space-y-3 p-3" data-testid="graph-filters">
      {/* Search + visible-count + clear */}
      <div className="flex items-center gap-2">
        <input type="text"
          placeholder="Search nodes, properties, edge predicates…"
          data-testid="graph-search"
          value={searchDraft}
          onChange={(e) => setSearchDraft(e.target.value)}
          className="input-glass h-8 flex-1 text-sm" />
        <span className="muted text-xs whitespace-nowrap"
              data-testid="graph-visible-count">
          {visibleCount} / {totalCount} visible
        </span>
        {anyActive && (
          <button type="button" onClick={clearAll}
            data-testid="graph-clear-filters"
            className="button-ghost h-8 px-3 text-xs">
            Clear all
          </button>
        )}
      </div>

      {/* Type chips */}
      <div className="flex flex-wrap items-center gap-2" data-testid="graph-filter-row-type">
        <span className="kicker w-20">Node type</span>
        {NODE_TYPES.map((t) => (
          <Chip key={t} label={t}
                testId={`graph-chip-type-${t}`}
                active={state.types.has(t)}
                count={typeCounts[t] ?? 0}
                onToggle={() => toggleType(t)} />
        ))}
      </div>

      {/* Predicate chips */}
      {predicateInfo.all.length > 0 && (
        <div className="flex flex-wrap items-center gap-2"
             data-testid="graph-filter-row-predicate">
          <span className="kicker w-20">Edge type</span>
          {predicateInfo.head.map((p) => (
            <Chip key={p.label} label={p.label}
                  testId={`graph-chip-pred-${slug(p.label)}`}
                  active={state.predicates.has(p.label)}
                  count={p.count}
                  onToggle={() => togglePredicate(p.label)} />
          ))}
          {predicateInfo.tail.length > 0 && (
            <button type="button"
              data-testid="graph-pred-overflow"
              onClick={(ev) => setPredOverflow({
                x: ev.clientX, y: ev.clientY + 4,
              })}
              className="glass-pill text-xs muted">
              +{predicateInfo.tail.length} more…
            </button>
          )}
        </div>
      )}

      {/* SHACL + neighbourhood */}
      {(shaclFocus.size > 0 || selectedId) && (
        <div className="flex flex-wrap items-center gap-2"
             data-testid="graph-filter-row-extra">
          <span className="kicker w-20">Scope</span>
          {shaclFocus.size > 0 && (
            <Chip label={`SHACL flagged (${shaclFocus.size})`}
                  testId="graph-chip-shacl"
                  active={state.shaclOnly}
                  onToggle={toggleShacl} />
          )}
          {selectedId && (
            <>
              <Chip label="1 hop"
                    testId="graph-chip-radius-1"
                    active={state.radius === 1}
                    onToggle={() => setRadius(1)} />
              <Chip label="2 hops"
                    testId="graph-chip-radius-2"
                    active={state.radius === 2}
                    onToggle={() => setRadius(2)} />
            </>
          )}
        </div>
      )}

      {predOverflow && (
        <ColumnFilterPopup
          columnLabel="Edge type"
          values={predicateInfo.tail.map(p => p.label)}
          selected={new Set(
            [...state.predicates].filter(p =>
              predicateInfo.tail.some(t => t.label === p),
            ),
          )}
          x={predOverflow.x}
          y={predOverflow.y}
          onApply={(picked) => {
            // Merge picked tail values with currently-active head
            // predicates (so the popup doesn't drop the chip
            // selections that were already on).
            const headActive = new Set(
              [...state.predicates].filter(p =>
                predicateInfo.head.some(h => h.label === p),
              ),
            );
            onChange({
              ...state,
              predicates: new Set([...headActive, ...picked]),
            });
            setPredOverflow(null);
          }}
          onCancel={() => setPredOverflow(null)}
        />
      )}
    </div>
  );
}

function slug(s: string): string {
  return s.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
